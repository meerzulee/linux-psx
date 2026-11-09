/* SPDX-License-Identifier: BSD-2-Clause
 * Copyright (c) 2026 Meerzulee and Linux psX contributors.
 */
#include "diagnostix_scatter.h"

_Static_assert(sizeof(void *) == 8 && sizeof(u64) == 8, "64-bit host required");
_Static_assert(sizeof(struct diag_scatter_node) == DIAG_SCATTER_PAGE, "node ABI");
_Static_assert(DIAG_SCATTER_SAFE_SLOTS == (1ULL << 14) && DIAG_SCATTER_SAFE_MAX < 65536,
               "bounded 14-bit safe-page index");
#define MASK (DIAG_SCATTER_PAGE - 1)
#define MAX_NODES ((DIAG_SCATTER_RECORDS + DIAG_SCATTER_PER_NODE - 1) / DIAG_SCATTER_PER_NODE)

static int fail(struct diag_scatter_plan *p, enum diag_scatter_error e, u64 va, u64 pa)
{
    p->error = e; p->error_va = va; p->error_pa = pa;
    return e;
}
static u64 round_page(u64 n) { return (n + MASK) & ~MASK; }
static int buffer_valid(struct diag_scatter_buffer b)
{
    u64 va = (u64)b.kva;
    return va && !(va & MASK) && b.allocation_bytes &&
        !(b.allocation_bytes & MASK) && b.allocation_bytes <= (1ULL << 30) &&
        va <= ~(u64)0 - b.allocation_bytes;
}
static int intersects(u64 a, u64 n, u64 b, u64 m)
{
    return a < b + m && b < a + n; /* all callers have bounded ends */
}
static int page(const struct diag_scatter_ops *o, struct diag_scatter_workspace *w,
                struct diag_scatter_plan *p, u64 va, u64 *pa)
{
    *pa = o->physical(o->ctx, va);
    if (!*pa || (*pa & MASK) || *pa >= DIAG_SCATTER_LIMIT)
        return fail(p, DIAG_SCATTER_TRANSLATION, va, *pa);
    for (u64 i = 0x1000; i < DIAG_SCATTER_PAGE; i += 0x1000) {
        u64 observed = o->physical(o->ctx, va + i);
        if (observed != *pa + i)
            return fail(p, DIAG_SCATTER_TRANSLATION, va + i, observed);
    }
    if (!o->source_ram(o->ctx, *pa, DIAG_SCATTER_PAGE))
        return fail(p, DIAG_SCATTER_NOT_RAM, va, *pa);
    if (*pa < 0x8000)
        return fail(p, DIAG_SCATTER_CONFLICT, va, *pa);
    u64 index = *pa / DIAG_SCATTER_PAGE;
    u64 bit = 1ULL << (index & 63);
    if (w->used[index / 64] & bit)
        return fail(p, DIAG_SCATTER_DUPLICATE, va, *pa);
    w->used[index / 64] |= bit;
    return 0;
}
static int immediate(u64 pa, u64 kernel_copy, u64 initrd_span)
{
    return intersects(pa, DIAG_SCATTER_PAGE, DIAG_SCATTER_KERNEL, kernel_copy) ||
        intersects(pa, DIAG_SCATTER_PAGE, DIAG_SCATTER_INITRD, initrd_span);
}
static u64 safe_slot(u64 pa)
{
    /* Multiplicative hash of a bounded page number; half-full at the hard cap. */
    return ((pa / DIAG_SCATTER_PAGE) * 0x9e3779b97f4a7c15ULL) >> 50;
}
static const struct diag_scatter_safe *safe_entry(const struct diag_scatter_workspace *w,
                                                 const struct diag_scatter_plan *p, u64 pa)
{
    for (u64 slot = safe_slot(pa), n = 0; n < DIAG_SCATTER_SAFE_SLOTS; ++n) {
        u16 index = w->safe_index[slot];
        if (!index) return NULL;
        if (p->safe[index - 1].original_pa == pa) return &p->safe[index - 1];
        slot = (slot + 1) & (DIAG_SCATTER_SAFE_SLOTS - 1);
    }
    return NULL; /* cannot fill: at most half the slots are used */
}
static u64 redirected(const struct diag_scatter_workspace *w,
                      const struct diag_scatter_plan *p, u64 pa)
{
    const struct diag_scatter_safe *entry = safe_entry(w, p, pa);
    return entry ? entry->safe_pa : pa;
}
static int allocate_safe(const struct diag_scatter_ops *o, struct diag_scatter_workspace *w,
                         struct diag_scatter_plan *p, u64 va, u64 pa,
                         u64 kernel_copy, u64 initrd_span)
{
    if (!o->alloc_safe || !o->free_safe)
        return fail(p, DIAG_SCATTER_CONFLICT, va, pa);
    if (p->safe_count >= DIAG_SCATTER_SAFE_MAX)
        return fail(p, DIAG_SCATTER_CAPACITY, va, pa);
    void *owned = o->alloc_safe(o->ctx);
    if (!owned) return fail(p, DIAG_SCATTER_ALLOC, va, pa);
    /* Track the exact callback return before alignment/translation validation. */
    struct diag_scatter_safe *entry = &p->safe[p->safe_count++];
    *entry = (struct diag_scatter_safe){pa, 0, owned};
    if (!buffer_valid((struct diag_scatter_buffer){owned, DIAG_SCATTER_PAGE}))
        return fail(p, DIAG_SCATTER_BOUNDS, (u64)owned, 0);
    int err = page(o, w, p, (u64)owned, &entry->safe_pa);
    if (err) return err;
    if (immediate(entry->safe_pa, kernel_copy, initrd_span))
        return fail(p, DIAG_SCATTER_CONFLICT, (u64)owned, entry->safe_pa);
    u64 slot = safe_slot(pa);
    while (w->safe_index[slot]) slot = (slot + 1) & (DIAG_SCATTER_SAFE_SLOTS - 1);
    w->safe_index[slot] = (u16)p->safe_count;
    return 0;
}
static void copy_page(void *dst, const void *src)
{
    for (u64 i = 0; i < DIAG_SCATTER_PAGE; ++i) ((u8 *)dst)[i] = ((const u8 *)src)[i];
}
static int bounce_sources(const struct diag_scatter_ops *o, struct diag_scatter_workspace *w,
                          struct diag_scatter_plan *p, struct diag_scatter_buffer b,
                          u64 begin, u64 bytes, u64 kernel_copy, u64 initrd_span)
{
    for (u64 offset = begin & ~MASK; offset < begin + bytes; offset += DIAG_SCATTER_PAGE) {
        u64 va = (u64)b.kva + offset, pa = o->physical(o->ctx, va);
        if (!immediate(pa, kernel_copy, initrd_span)) continue;
        int err = allocate_safe(o, w, p, va, pa, kernel_copy, initrd_span);
        if (err) return err;
        copy_page(p->safe[p->safe_count - 1].kva, (void *)va);
    }
    return 0;
}
static int emit(struct diag_scatter_plan *p, u64 src, u64 dst, u64 bytes)
{
    if (!bytes || bytes > DIAG_SCATTER_PAGE || p->total_records >= DIAG_SCATTER_RECORDS ||
        p->total_records / DIAG_SCATTER_PER_NODE >= p->node_count)
        return fail(p, DIAG_SCATTER_CAPACITY, 0, src);
    struct diag_scatter_node *node = (struct diag_scatter_node *)p->metadata_kva +
        p->total_records / DIAG_SCATTER_PER_NODE;
    node->records[node->count++] = (struct diag_scatter_record){src, dst, bytes};
    ++p->total_records;
    return 0;
}
static int scan(const struct diag_scatter_ops *o, struct diag_scatter_workspace *w,
                struct diag_scatter_plan *p, struct diag_scatter_buffer b,
                u64 begin, u64 bytes, u64 dst, int validate)
{
    for (u64 offset = 0; offset < b.allocation_bytes; offset += DIAG_SCATTER_PAGE) {
        u64 pa;
        int err;
        if (validate) {
            err = page(o, w, p, (u64)b.kva + offset, &pa);
            if (err) return err;
            continue;
        }
        pa = redirected(w, p, o->physical(o->ctx, (u64)b.kva + offset));
        u64 start = offset > begin ? offset : begin;
        u64 end = offset + DIAG_SCATTER_PAGE;
        if (end > begin + bytes) end = begin + bytes;
        if (start < end) {
            err = emit(p, pa + start - offset, dst + start - begin, end - start);
            if (err) return err;
        }
    }
    return 0;
}
void diag_scatter_release(const struct diag_scatter_ops *o, struct diag_scatter_plan *p)
{
    if (!p || !o || !o->free || (p->safe_count && !o->free_safe)) return;
    while (p->safe_count) o->free_safe(o->ctx, p->safe[--p->safe_count].kva);
    if (p->metadata_kva) o->free(o->ctx, p->metadata_kva, p->metadata_bytes);
    p->metadata_kva = NULL; p->metadata_bytes = 0;
    p->physical_head = p->total_records = p->node_count = 0;
}
int diag_scatter_prepare(const struct diag_scatter_ops *o, const struct diag_scatter_input *in,
                         struct diag_scatter_workspace *w, struct diag_scatter_plan *p)
{
    if (!p) return DIAG_SCATTER_ARGUMENT;
    if (p->metadata_kva || p->safe_count) return DIAG_SCATTER_ARGUMENT; /* never lose ownership */
    p->metadata_bytes = p->physical_head = p->total_records = p->node_count = 0;
    p->error = DIAG_SCATTER_OK; p->error_va = p->error_pa = 0;
    if (!o || !o->alloc || !o->free || !o->physical || !o->ram || !o->source_ram || !in || !w ||
        in->aux_count > DIAG_SCATTER_AUX_MAX || (in->aux_count && !in->aux) ||
        (!!o->alloc_safe != !!o->free_safe))
        return fail(p, DIAG_SCATTER_ARGUMENT, 0, 0);
    if (!buffer_valid(in->image) || !buffer_valid(in->initrd) ||
        !in->image_bytes || in->image_bytes > (64ULL << 20) ||
        in->image_bytes > in->image.allocation_bytes || !in->protected_bytes ||
        in->setup_bytes > in->image_bytes ||
        in->protected_bytes > in->image_bytes - in->setup_bytes ||
        in->kernel_init_size < in->protected_bytes || in->kernel_init_size > (512ULL << 20) ||
        !in->initrd_bytes || in->initrd_bytes > (513ULL << 20) ||
        in->initrd_bytes > in->initrd.allocation_bytes)
        return fail(p, DIAG_SCATTER_BOUNDS, 0, 0);
    for (u64 i = 0; i < in->aux_count; ++i)
        if (!buffer_valid(in->aux[i])) return fail(p, DIAG_SCATTER_BOUNDS, (u64)in->aux[i].kva, 0);
    u64 kernel_span = round_page(in->kernel_init_size), kernel_copy = round_page(in->protected_bytes);
    u64 initrd_span = round_page(in->initrd_bytes);
    if (!o->ram(o->ctx, DIAG_SCATTER_KERNEL, kernel_span) ||
        !o->ram(o->ctx, DIAG_SCATTER_INITRD, initrd_span) ||
        intersects(DIAG_SCATTER_KERNEL, kernel_span, DIAG_SCATTER_INITRD, initrd_span))
        return fail(p, DIAG_SCATTER_DESTINATION, 0, 0);
    u64 records = ((in->setup_bytes & MASK) + in->protected_bytes + MASK) / DIAG_SCATTER_PAGE +
        initrd_span / DIAG_SCATTER_PAGE + !!(in->protected_bytes & MASK) + !!(in->initrd_bytes & MASK);
    if (records > DIAG_SCATTER_RECORDS) return fail(p, DIAG_SCATTER_CAPACITY, 0, 0);
    p->node_count = (records + DIAG_SCATTER_PER_NODE - 1) / DIAG_SCATTER_PER_NODE;
    p->metadata_bytes = p->node_count * DIAG_SCATTER_PAGE;
    p->metadata_kva = o->alloc(o->ctx, p->metadata_bytes);
    if (!p->metadata_kva) { p->metadata_bytes = p->node_count = 0; return fail(p, DIAG_SCATTER_ALLOC, 0, 0); }
    int err = 0;
    if (!buffer_valid((struct diag_scatter_buffer){p->metadata_kva, p->metadata_bytes})) {
        err = fail(p, DIAG_SCATTER_BOUNDS, (u64)p->metadata_kva, 0); goto bad;
    }
    for (u64 i = 0; i < sizeof(w->used) / sizeof(w->used[0]); ++i) w->used[i] = 0;
    for (u64 i = 0; i < DIAG_SCATTER_SAFE_SLOTS; ++i) w->safe_index[i] = 0;
    u64 node_pa[MAX_NODES];
    for (u64 i = 0; i < p->node_count; ++i) {
        err = page(o, w, p, (u64)p->metadata_kva + i * DIAG_SCATTER_PAGE, &node_pa[i]);
        if (err) goto bad;
    }
    /* Validate ALL sources before metadata writes: an invalid allocator might
     * alias input pages, and refusal must not destroy the caller's inputs. */
    err = scan(o, w, p, in->image, 0, 0, 0, 1);
    if (err) goto bad;
    err = scan(o, w, p, in->initrd, 0, 0, 0, 1);
    if (err) goto bad;
    for (u64 i = 0; i < in->aux_count; ++i) {
        err = scan(o, w, p, in->aux[i], 0, 0, 0, 1);
        if (err) goto bad;
    }
    /* No callback-owned page is allocated/copied until every original page is
     * validated. All originals remain reserved in used, including dead aux. */
    err = bounce_sources(o, w, p, in->image, in->setup_bytes, in->protected_bytes, kernel_copy, initrd_span);
    if (err) goto bad;
    err = bounce_sources(o, w, p, in->initrd, 0, in->initrd_bytes, kernel_copy, initrd_span);
    if (err) goto bad;
    for (u64 i = 0; i < p->node_count; ++i) {
        if (immediate(node_pa[i], kernel_copy, initrd_span)) {
            err = allocate_safe(o, w, p, (u64)p->metadata_kva + i * DIAG_SCATTER_PAGE,
                                node_pa[i], kernel_copy, initrd_span);
            if (err) goto bad;
            node_pa[i] = redirected(w, p, node_pa[i]);
        }
    }
    for (u64 i = 0; i < p->metadata_bytes; ++i) ((u8 *)p->metadata_kva)[i] = 0;
    for (u64 i = 0; i < p->node_count; ++i)
        ((struct diag_scatter_node *)p->metadata_kva)[i].next = i + 1 < p->node_count ? node_pa[i + 1] : 0;
    err = scan(o, w, p, in->image, in->setup_bytes, in->protected_bytes, DIAG_SCATTER_KERNEL, 0);
    if (err) goto bad;
    err = scan(o, w, p, in->initrd, 0, in->initrd_bytes, DIAG_SCATTER_INITRD, 0);
    if (err) goto bad;
    if (in->protected_bytes & MASK) {
        err = emit(p, 0, DIAG_SCATTER_KERNEL + in->protected_bytes, round_page(in->protected_bytes) - in->protected_bytes);
        if (err) goto bad;
    }
    if (in->initrd_bytes & MASK) {
        err = emit(p, 0, DIAG_SCATTER_INITRD + in->initrd_bytes, initrd_span - in->initrd_bytes);
        if (err) goto bad;
    }
    if (p->total_records != records) { err = fail(p, DIAG_SCATTER_CAPACITY, 0, 0); goto bad; }
    for (u64 i = 0; i < p->node_count; ++i) {
        u64 original = o->physical(o->ctx, (u64)p->metadata_kva + i * DIAG_SCATTER_PAGE);
        if (node_pa[i] != original) {
            const struct diag_scatter_safe *entry = safe_entry(w, p, original);
            if (!entry) { err = fail(p, DIAG_SCATTER_CAPACITY, 0, original); goto bad; }
            copy_page(entry->kva, (u8 *)p->metadata_kva + i * DIAG_SCATTER_PAGE);
        }
    }
    p->physical_head = node_pa[0];
    return 0;
bad:
    diag_scatter_release(o, p);
    return err;
}
