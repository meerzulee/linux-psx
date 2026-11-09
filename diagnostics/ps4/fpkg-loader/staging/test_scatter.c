/* SPDX-License-Identifier: BSD-2-Clause
 * Copyright (c) 2026 Meerzulee and Linux psX contributors.
 * Synthetic callbacks only: no console, syscalls, MMIO or live physical memory.
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#undef NULL
#undef offsetof
#include "diagnostix_scatter.h"

#define P DIAG_SCATTER_PAGE
struct region { unsigned char *va; u64 bytes, *pa; };
struct fixture {
    struct region r[8]; unsigned n;
    unsigned allocs, frees, live, source_checks, destination_checks;
    int allocation_failure, metadata_unaligned, bad_destination, metadata_unchanged;
    int metadata_bad_subpage;
    u64 bad_translation_va, reject_pa, metadata_pa, metadata_second_pa;
    u64 freed_bytes;
    struct { unsigned char *va; void *returned; u64 pa; } safe[DIAG_SCATTER_SAFE_MAX];
    unsigned safe_allocs, safe_frees, safe_live;
    unsigned safe_failure_at, safe_bad_subpage;
    int safe_unaligned, safe_duplicate_second, safe_unchanged_last;
    u64 safe_override_pa;
};
static struct diag_scatter_workspace workspace;
static u64 rounded(u64 n) { return (n + P - 1) / P * P; }
static struct region *add_region(struct fixture *f, u64 bytes, u64 base)
{
    assert(f->n < 8 && bytes && bytes % P == 0);
    struct region *r = &f->r[f->n++];
    r->va = aligned_alloc(P, bytes); r->pa = calloc(bytes / P, sizeof(u64));
    assert(r->va && r->pa); r->bytes = bytes;
    for (u64 i = 0; i < bytes / P; ++i) r->pa[i] = base + i * 3 * P;
    for (u64 i = 0; i < bytes; ++i) r->va[i] = (unsigned char)(i * 37 + i / P * 11 + base / P);
    return r;
}
static void *allocate(void *ctx, u64 bytes)
{
    struct fixture *f = ctx; ++f->allocs;
    if (f->allocation_failure) return NULL;
    struct region *r = add_region(f, bytes, f->metadata_pa);
    if (f->metadata_bad_subpage)
        f->bad_translation_va = (u64)r->va + (f->metadata_bad_subpage - 1) * 0x1000;
    if (f->metadata_second_pa && bytes > P) r->pa[1] = f->metadata_second_pa;
    ++f->live;
    return r->va + (f->metadata_unaligned ? 1 : 0);
}
static void deallocate(void *ctx, void *p, u64 bytes)
{
    struct fixture *f = ctx; struct region *r = &f->r[f->n - 1];
    assert(f->live == 1 && p == r->va + (f->metadata_unaligned ? 1 : 0));
    assert(bytes == r->bytes); ++f->frees; --f->live; f->freed_bytes = bytes;
    if (f->metadata_unchanged)
        for (u64 i = 0; i < bytes; ++i)
            assert(r->va[i] == (unsigned char)(i * 37 + i / P * 11 + f->metadata_pa / P));
    free(r->va); free(r->pa); memset(r, 0, sizeof(*r)); --f->n;
}
static u64 physical(void *ctx, u64 va)
{
    struct fixture *f = ctx;
    if (va == f->bad_translation_va) return 0;
    for (unsigned i = 0; i < f->n; ++i) {
        struct region *r = &f->r[i]; u64 base = (u64)r->va;
        if (va >= base && va - base < r->bytes) {
            u64 offset = va - base;
            return r->pa[offset / P] + offset % P;
        }
    }
    for (unsigned i = 0; i < f->safe_live; ++i) {
        u64 base = (u64)f->safe[i].va;
        if (va >= base && va - base < P) return f->safe[i].pa + va - base;
    }
    return 0;
}
static void *allocate_safe(void *ctx)
{
    struct fixture *f = ctx;
    assert(f->safe_allocs < DIAG_SCATTER_SAFE_MAX);
    ++f->safe_allocs;
    if (f->safe_allocs == f->safe_failure_at) return NULL;
    unsigned index = f->safe_live++;
    f->safe[index].va = aligned_alloc(P, P); assert(f->safe[index].va);
    memset(f->safe[index].va, 0xa7, P);
    f->safe[index].returned = f->safe[index].va + !!f->safe_unaligned;
    f->safe[index].pa = f->safe_override_pa ? f->safe_override_pa : 0x40000000 + index * P;
    if (f->safe_duplicate_second && index == 1) f->safe[index].pa = f->safe[0].pa;
    if (f->safe_bad_subpage)
        f->bad_translation_va = (u64)f->safe[index].va + (f->safe_bad_subpage - 1) * 0x1000;
    return f->safe[index].returned;
}
static void deallocate_safe(void *ctx, void *kva)
{
    struct fixture *f = ctx;
    assert(f->safe_live && kva == f->safe[f->safe_live - 1].returned);
    unsigned index = --f->safe_live;
    if (f->safe_unchanged_last && !f->safe_frees)
        for (u64 i = 0; i < P; ++i) assert(f->safe[index].va[i] == 0xa7);
    free(f->safe[index].va); memset(&f->safe[index], 0, sizeof(f->safe[index]));
    ++f->safe_frees;
}
static int native_ram(const struct fixture *f, u64 pa, u64 bytes)
{
    return bytes && pa < DIAG_SCATTER_LIMIT && bytes <= DIAG_SCATTER_LIMIT - pa && pa != f->reject_pa;
}
static int source_ram(void *ctx, u64 pa, u64 bytes)
{
    struct fixture *f = ctx; ++f->source_checks;
    assert(bytes == P);
    return native_ram(f, pa, bytes);
}
static int ram(void *ctx, u64 pa, u64 bytes)
{
    struct fixture *f = ctx; ++f->destination_checks;
    if (f->bad_destination && (pa == DIAG_SCATTER_KERNEL || pa == DIAG_SCATTER_INITRD)) return 0;
    if (!native_ram(f, pa, bytes)) return 0;
    /* Linux's inherited E820 holes differ from native allocator-owned RAM. */
    return !(pa < 0x80000 && pa + bytes > 0x8000) &&
        !(pa < 0x480000 && pa + bytes > 0x400000);
}
static struct diag_scatter_ops ops(struct fixture *f)
{
    return (struct diag_scatter_ops){f, allocate, deallocate, physical, ram, source_ram, NULL, NULL};
}
static struct diag_scatter_ops safe_ops(struct fixture *f)
{
    struct diag_scatter_ops o = ops(f);
    o.alloc_safe = allocate_safe; o.free_safe = deallocate_safe;
    return o;
}
static struct diag_scatter_input setup(struct fixture *f, u64 initrd_pages)
{
    memset(f, 0, sizeof(*f)); f->metadata_pa = 0x340000000ULL;
    struct region *image = add_region(f, P * 4, 0x30000000);
    struct region *initrd = add_region(f, P * initrd_pages, 0x280000000ULL);
    struct region *aux = add_region(f, P, 0x300000000ULL);
    static struct diag_scatter_buffer auxiliary;
    auxiliary = (struct diag_scatter_buffer){aux->va, aux->bytes};
    return (struct diag_scatter_input){
        {image->va, image->bytes}, P * 3 + 123, 0x1230, P * 2 + 57, P * 7 + 1,
        {initrd->va, initrd->bytes}, initrd->bytes - 19, &auxiliary, 1};
}
static void destroy(struct fixture *f)
{
    assert(!f->live && !f->safe_live);
    for (unsigned i = 0; i < f->n; ++i) { free(f->r[i].va); free(f->r[i].pa); }
}
/* Independent physical-memory lookup, unrelated to planner's VA translations. */
static unsigned char *memory(struct fixture *f, u64 pa, u64 bytes)
{
    for (unsigned i = 0; i < f->n; ++i)
        for (u64 j = 0; j < f->r[i].bytes / P; ++j)
            if (pa >= f->r[i].pa[j] && pa - f->r[i].pa[j] < P && bytes <= P - (pa - f->r[i].pa[j]))
                return f->r[i].va + j * P + pa - f->r[i].pa[j];
    for (unsigned i = 0; i < f->safe_live; ++i)
        if (pa >= f->safe[i].pa && pa - f->safe[i].pa < P && bytes <= P - (pa - f->safe[i].pa))
            return f->safe[i].va + pa - f->safe[i].pa;
    assert(!"unknown or cross-page physical reference"); return NULL;
}
static void materialize(struct fixture *f, const struct diag_scatter_input *in,
                        const struct diag_scatter_plan *plan)
{
    u64 kn = rounded(in->protected_bytes), rn = rounded(in->initrd_bytes);
    unsigned char *k = malloc(kn), *r = malloc(rn), *kc = calloc(kn, 1), *rc = calloc(rn, 1);
    assert(k && r && kc && rc); memset(k, 0xa5, kn); memset(r, 0xa5, rn);
    u64 next = plan->physical_head, records = 0, nodes = 0;
    while (next) {
        assert(++nodes <= plan->node_count && next % P == 0);
        const struct diag_scatter_node *node = (const void *)memory(f, next, P);
        assert(node->count && node->count <= 682);
        for (u64 j = 0; j < node->count; ++j) {
            const struct diag_scatter_record *e = &node->records[j];
            assert(e->bytes && e->bytes <= P && ++records <= plan->total_records);
            unsigned char *dst, *coverage; u64 offset;
            if (e->dst >= DIAG_SCATTER_KERNEL && e->dst - DIAG_SCATTER_KERNEL < kn) {
                offset = e->dst - DIAG_SCATTER_KERNEL; assert(e->bytes <= kn - offset);
                dst = k + offset; coverage = kc + offset;
            } else {
                assert(e->dst >= DIAG_SCATTER_INITRD && e->dst - DIAG_SCATTER_INITRD < rn);
                offset = e->dst - DIAG_SCATTER_INITRD; assert(e->bytes <= rn - offset);
                dst = r + offset; coverage = rc + offset;
            }
            for (u64 b = 0; b < e->bytes; ++b) { assert(!coverage[b]); coverage[b] = 1; }
            if (e->src) memcpy(dst, memory(f, e->src, e->bytes), e->bytes);
            else memset(dst, 0, e->bytes);
        }
        next = node->next;
    }
    assert(nodes == plan->node_count && records == plan->total_records);
    assert(!memcmp(k, (u8 *)in->image.kva + in->setup_bytes, in->protected_bytes));
    assert(!memcmp(r, in->initrd.kva, in->initrd_bytes));
    for (u64 i = 0; i < kn; ++i) { assert(kc[i]); if (i >= in->protected_bytes) assert(!k[i]); }
    for (u64 i = 0; i < rn; ++i) { assert(rc[i]); if (i >= in->initrd_bytes) assert(!r[i]); }
    free(k); free(r); free(kc); free(rc);
}
static void success(u64 initrd_pages, int aligned)
{
    struct fixture f; struct diag_scatter_input in = setup(&f, initrd_pages);
    if (aligned) { in.setup_bytes = P; in.protected_bytes = P * 2; in.initrd_bytes = in.initrd.allocation_bytes; }
    struct diag_scatter_ops o = ops(&f); struct diag_scatter_plan p = {0};
    assert(!diag_scatter_prepare(&o, &in, &workspace, &p));
    assert(p.error == DIAG_SCATTER_OK && f.live == 1);
    assert(f.destination_checks == 2 && f.source_checks == 4 + initrd_pages + 1 + p.node_count);
    materialize(&f, &in, &p);
    void *owned = p.metadata_kva;
    assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_ARGUMENT && p.metadata_kva == owned);
    diag_scatter_release(&o, &p); diag_scatter_release(&o, &p);
    assert(f.frees == 1 && !p.physical_head && !p.total_records); destroy(&f);
}
static void failures(void)
{
    for (int test = 0; test < 35; ++test) {
        struct fixture f; struct diag_scatter_input in = setup(&f, test == 22 ? 700 : 3);
        struct diag_scatter_ops o = ops(&f); struct diag_scatter_plan p = {0};
        f.metadata_unchanged = 1;
        enum diag_scatter_error expected = DIAG_SCATTER_BOUNDS;
        switch (test) {
        case 0: in.setup_bytes = ~(u64)0; break;
        case 1: in.protected_bytes = ~(u64)0; break;
        case 2: in.image.kva = (void *)(~(u64)(P - 1)); break;
        case 3: in.image.allocation_bytes = ~(u64)0; break;
        case 4: in.image_bytes = in.image.allocation_bytes + 1; break;
        case 5: in.kernel_init_size = in.protected_bytes - 1; break;
        case 6: in.initrd_bytes = ~(u64)0; break;
        case 7: f.bad_destination = 1; expected = DIAG_SCATTER_DESTINATION; break;
        case 8: f.allocation_failure = 1; expected = DIAG_SCATTER_ALLOC; break;
        case 9: f.metadata_unaligned = 1; break;
        case 10: f.r[0].pa[0] = 0; expected = DIAG_SCATTER_TRANSLATION; break;
        case 11: f.r[0].pa[0]++; expected = DIAG_SCATTER_TRANSLATION; break;
        case 12: f.r[0].pa[0] = DIAG_SCATTER_LIMIT; expected = DIAG_SCATTER_TRANSLATION; break;
        case 13: f.bad_translation_va = (u64)f.r[0].va + 0x1000; expected = DIAG_SCATTER_TRANSLATION; break;
        case 14: f.reject_pa = f.r[1].pa[0]; expected = DIAG_SCATTER_NOT_RAM; break;
        case 15: f.r[0].pa[0] = 0x4000; expected = DIAG_SCATTER_CONFLICT; break;
        case 16: f.r[0].pa[0] = DIAG_SCATTER_KERNEL + P * 2; expected = DIAG_SCATTER_CONFLICT; break;
        case 17: f.r[1].pa[0] = DIAG_SCATTER_INITRD; expected = DIAG_SCATTER_CONFLICT; break;
        case 18: f.r[0].pa[1] = f.r[0].pa[0]; expected = DIAG_SCATTER_DUPLICATE; break;
        case 19: f.r[1].pa[0] = f.r[0].pa[0]; expected = DIAG_SCATTER_DUPLICATE; break;
        case 20: f.r[2].pa[0] = f.r[1].pa[0]; expected = DIAG_SCATTER_DUPLICATE; break;
        case 21: f.metadata_pa = f.r[0].pa[0]; expected = DIAG_SCATTER_DUPLICATE; break;
        case 22: f.metadata_second_pa = f.metadata_pa; expected = DIAG_SCATTER_DUPLICATE; break;
        case 23: f.metadata_pa = DIAG_SCATTER_INITRD; expected = DIAG_SCATTER_CONFLICT; break;
        case 24: f.metadata_pa = DIAG_SCATTER_KERNEL; expected = DIAG_SCATTER_CONFLICT; break;
        case 25: in.aux_count = 9; expected = DIAG_SCATTER_ARGUMENT; break;
        case 26: in.aux = NULL; expected = DIAG_SCATTER_ARGUMENT; break;
        case 27: f.r[2].pa[0] = 0x4000; expected = DIAG_SCATTER_CONFLICT; break;
        case 28: in.image.kva = f.r[0].va + 1; break;
        case 29: in.image.allocation_bytes = 0; break;
        case 30: in.protected_bytes = 0; break;
        case 31: in.initrd_bytes = 0; break;
        case 32: f.metadata_pa = 0x4000; expected = DIAG_SCATTER_CONFLICT; break;
        case 33: f.reject_pa = f.metadata_pa; expected = DIAG_SCATTER_NOT_RAM; break;
        case 34: in.kernel_init_size = ~(u64)0; break;
        }
        unsigned char before[64]; memcpy(before, f.r[0].va, sizeof(before));
        assert(diag_scatter_prepare(&o, &in, &workspace, &p) == (int)expected);
        assert(p.error == expected && !p.metadata_kva && !p.physical_head && !p.total_records && !f.live);
        assert(f.frees == f.allocs - !!f.allocation_failure);
        assert(!memcmp(before, f.r[0].va, sizeof(before)));
        destroy(&f);
    }
    /* Every subpage of sources and auxiliary allocations is checked. */
    for (unsigned region = 0; region < 3; ++region)
        for (u64 sub = 0; sub < 4; ++sub) {
            struct fixture f; struct diag_scatter_input in = setup(&f, 3);
            f.metadata_unchanged = 1;
            f.bad_translation_va = (u64)f.r[region].va + f.r[region].bytes - P + sub * 0x1000;
            struct diag_scatter_ops o = ops(&f); struct diag_scatter_plan p = {0};
            assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_TRANSLATION);
            assert(f.frees == 1 && !f.live); destroy(&f);
        }
    for (int sub = 1; sub <= 4; ++sub) {
        struct fixture f; struct diag_scatter_input in = setup(&f, 3);
        f.metadata_bad_subpage = sub; f.metadata_unchanged = 1;
        struct diag_scatter_ops o = ops(&f); struct diag_scatter_plan p = {0};
        assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_TRANSLATION);
        assert(f.frees == 1 && !f.live); destroy(&f);
    }
    /* The half-open limits allow a page immediately after the protected
     * regions and the last representable physical page below16GiB. */
    struct fixture f; struct diag_scatter_input in = setup(&f, 3);
    f.r[0].pa[0] = 0x8000;
    f.r[0].pa[1] = DIAG_SCATTER_KERNEL + rounded(in.kernel_init_size);
    f.r[0].pa[2] = DIAG_SCATTER_INITRD + rounded(in.initrd_bytes);
    f.r[0].pa[3] = DIAG_SCATTER_LIMIT - P;
    struct diag_scatter_ops o = ops(&f); struct diag_scatter_plan p = {0};
    assert(!diag_scatter_prepare(&o, &in, &workspace, &p));
    materialize(&f, &in, &p); diag_scatter_release(&o, &p); destroy(&f);
    /* Exercise maximum declared extents without touching/allocating huge input
     * buffers: metadata allocation refuses before any physical/source read. */
    in = setup(&f, 3); o = ops(&f); p = (struct diag_scatter_plan){0};
    in.image.allocation_bytes = in.image_bytes = 64ULL << 20;
    in.protected_bytes = in.image_bytes - in.setup_bytes;
    in.kernel_init_size = 512ULL << 20;
    in.initrd.allocation_bytes = in.initrd_bytes = 513ULL << 20;
    f.allocation_failure = 1;
    assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_ALLOC);
    assert(f.allocs == 1 && f.frees == 0);
    ++in.initrd_bytes;
    assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_BOUNDS);
    assert(f.allocs == 1); destroy(&f);
}
static void distinct_source_policy(void)
{
    const u64 native_only[] = {0x28000, 0x400000};
    for (unsigned address = 0; address < 2; ++address)
        for (unsigned role = 0; role < 4; ++role) {
            struct fixture f; struct diag_scatter_input in = setup(&f, 3);
            const u64 pa = native_only[address];
            if (role == 3) f.metadata_pa = pa;
            else f.r[role].pa[0] = pa; /* image, initrd, auxiliary */
            struct diag_scatter_ops o = ops(&f); struct diag_scatter_plan p = {0};
            assert(source_ram(&f, pa, P) && !ram(&f, pa, P));
            f.source_checks = f.destination_checks = 0;
            assert(!diag_scatter_prepare(&o, &in, &workspace, &p));
            assert(f.destination_checks == 2 && f.source_checks == 9);
            materialize(&f, &in, &p); diag_scatter_release(&o, &p);
            /* Reproduce the old conflation: applying destination eligibility
             * to a native source rejects this otherwise intact owned page. */
            o.source_ram = ram;
            assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_NOT_RAM);
            assert(p.error_pa == pa && !p.metadata_kva && !f.live);
            destroy(&f);
        }
    for (int missing = 0; missing < 2; ++missing) {
        struct fixture f; struct diag_scatter_input in = setup(&f, 3);
        struct diag_scatter_ops o = ops(&f); struct diag_scatter_plan p = {0};
        if (missing) o.source_ram = NULL; else o.ram = NULL;
        assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_ARGUMENT);
        assert(!f.allocs && !f.source_checks && !f.destination_checks); destroy(&f);
    }
    struct fixture f; struct diag_scatter_input in = setup(&f, 3);
    f.bad_destination = 1;
    struct diag_scatter_ops o = ops(&f); struct diag_scatter_plan p = {0};
    assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_DESTINATION);
    assert(f.destination_checks == 1 && !f.source_checks && !f.allocs); destroy(&f);
}
static void assert_safe_plan(struct fixture *f, const struct diag_scatter_plan *p)
{
    assert(p->safe_count == f->safe_live && p->safe_count <= DIAG_SCATTER_SAFE_MAX);
    for (u64 i = 0; i < p->safe_count; ++i) {
        assert(p->safe[i].kva == f->safe[i].returned && p->safe[i].safe_pa == f->safe[i].pa);
        assert(p->safe[i].safe_pa != p->safe[i].original_pa);
        assert(!memcmp(p->safe[i].kva, memory(f, p->safe[i].original_pa, P), P));
    }
}
static void phase_lifetimes(void)
{
    /* Exact EXP034 workspace-only PA: copy ends at 0x6ac0000, full init_size
     * ends at 0x8794000. This page is not a terminal destination. */
    struct fixture f; struct diag_scatter_input in = setup(&f, 3);
    free(f.r[0].va); free(f.r[0].pa);
    u64 size = rounded(0xac0000 + in.setup_bytes);
    f.r[0].va = aligned_alloc(P, size); f.r[0].bytes = size;
    f.r[0].pa = calloc(size / P, sizeof(u64)); assert(f.r[0].va && f.r[0].pa);
    for (u64 i = 0; i < size / P; ++i) f.r[0].pa[i] = 0x30000000 + i * 3 * P;
    for (u64 i = 0; i < size; ++i) f.r[0].va[i] = (u8)(i * 31 + 7);
    f.r[0].pa[1] = 0x85fc000;
    in.image = (struct diag_scatter_buffer){f.r[0].va, size};
    in.image_bytes = in.setup_bytes + 0xac0000;
    in.protected_bytes = 0xac0000; in.kernel_init_size = 0x2794000;
    struct diag_scatter_ops o = ops(&f); struct diag_scatter_plan p = {0};
    assert(!diag_scatter_prepare(&o, &in, &workspace, &p));
    assert(!p.safe_count && !f.safe_allocs); materialize(&f, &in, &p);
    diag_scatter_release(&o, &p); destroy(&f);
    /* Auxiliary pages are consumed into low storage before terminal writes. */
    for (unsigned destination = 0; destination < 2; ++destination) {
        in = setup(&f, 3); o = ops(&f);
        f.r[2].pa[0] = destination ? DIAG_SCATTER_INITRD : DIAG_SCATTER_KERNEL;
        assert(!diag_scatter_prepare(&o, &in, &workspace, &p));
        assert(!p.safe_count); materialize(&f, &in, &p);
        diag_scatter_release(&o, &p); destroy(&f);
    }
    /* Image setup-only and unused padding pages are not terminal sources. */
    in = setup(&f, 3); o = ops(&f);
    in.setup_bytes = P; in.protected_bytes = 2 * P;
    f.r[0].pa[0] = DIAG_SCATTER_KERNEL; f.r[0].pa[3] = DIAG_SCATTER_INITRD;
    assert(!diag_scatter_prepare(&o, &in, &workspace, &p));
    assert(!p.safe_count); materialize(&f, &in, &p);
    diag_scatter_release(&o, &p); destroy(&f);
}
static void bounced_successes(void)
{
    for (unsigned test = 0; test < 6; ++test) {
        struct fixture f; struct diag_scatter_input in = setup(&f, test == 3 ? 700 : 3);
        struct diag_scatter_ops o = safe_ops(&f); struct diag_scatter_plan p = {0};
        u64 expected = 1;
        if (test == 0) f.r[0].pa[1] = DIAG_SCATTER_KERNEL;
        if (test == 1) f.r[1].pa[0] = DIAG_SCATTER_KERNEL;
        if (test == 2) { /* kernel/initrd two-way cycle */
            f.r[0].pa[1] = DIAG_SCATTER_INITRD;
            f.r[1].pa[0] = DIAG_SCATTER_KERNEL; expected = 2;
        }
        if (test == 3) { /* both descriptor nodes live inside copy destinations */
            f.metadata_pa = DIAG_SCATTER_KERNEL;
            f.metadata_second_pa = DIAG_SCATTER_INITRD; expected = 2;
        }
        if (test == 4) f.r[0].pa[1] = DIAG_SCATTER_KERNEL + rounded(in.protected_bytes) - P;
        if (test == 5) f.r[0].pa[1] = DIAG_SCATTER_INITRD + rounded(in.initrd_bytes) - P;
        unsigned char before[64]; memcpy(before, f.r[0].va, sizeof(before));
        assert(!diag_scatter_prepare(&o, &in, &workspace, &p));
        assert(p.safe_count == expected); assert_safe_plan(&f, &p);
        if (test == 3) {
            assert(p.physical_head == p.safe[0].safe_pa);
            assert(((struct diag_scatter_node *)p.safe[0].kva)->next == p.safe[1].safe_pa);
        }
        assert(!memcmp(before, f.r[0].va, sizeof(before)));
        materialize(&f, &in, &p);
        void *owned = p.metadata_kva;
        assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_ARGUMENT);
        assert(p.metadata_kva == owned && p.safe_count == expected);
        diag_scatter_release(&o, &p); diag_scatter_release(&o, &p);
        assert(f.frees == 1 && f.safe_frees == expected && !p.safe_count);
        destroy(&f);
    }
}
static void safe_failures(void)
{
    for (unsigned test = 0; test < 14; ++test) {
        struct fixture f; struct diag_scatter_input in = setup(&f, 3);
        struct diag_scatter_ops o = safe_ops(&f); struct diag_scatter_plan p = {0};
        f.r[0].pa[1] = DIAG_SCATTER_KERNEL; f.metadata_unchanged = 1;
        f.safe_unchanged_last = 1;
        enum diag_scatter_error expected = DIAG_SCATTER_DUPLICATE;
        switch (test) {
        case 0: f.safe_override_pa = f.r[0].pa[3]; break; /* unused original */
        case 1: f.safe_override_pa = f.r[1].pa[0]; break;
        case 2: f.safe_override_pa = f.r[2].pa[0]; break; /* dead aux still owned */
        case 3: f.safe_override_pa = f.metadata_pa; break;
        case 4: f.safe_override_pa = DIAG_SCATTER_KERNEL + P; expected = DIAG_SCATTER_CONFLICT; break;
        case 5: f.safe_override_pa = DIAG_SCATTER_INITRD; expected = DIAG_SCATTER_CONFLICT; break;
        case 6: f.safe_override_pa = 0x4000; expected = DIAG_SCATTER_CONFLICT; break;
        case 7: f.safe_override_pa = DIAG_SCATTER_LIMIT; expected = DIAG_SCATTER_TRANSLATION; break;
        case 8: f.safe_override_pa = 0x40000001; expected = DIAG_SCATTER_TRANSLATION; break;
        case 9: f.safe_unaligned = 1; expected = DIAG_SCATTER_BOUNDS; break;
        case 10: f.safe_override_pa = f.reject_pa = 0x40000000; expected = DIAG_SCATTER_NOT_RAM; break;
        case 11: f.r[1].pa[0] = DIAG_SCATTER_INITRD; f.safe_duplicate_second = 1; break;
        case 12: f.safe_failure_at = 1; expected = DIAG_SCATTER_ALLOC; break;
        case 13: f.r[1].pa[0] = DIAG_SCATTER_INITRD; f.safe_failure_at = 2;
            f.safe_unchanged_last = 0; expected = DIAG_SCATTER_ALLOC; break;
        }
        unsigned char before[64]; memcpy(before, f.r[0].va, sizeof(before));
        assert(diag_scatter_prepare(&o, &in, &workspace, &p) == (int)expected);
        assert(p.error == expected && !p.metadata_kva && !p.safe_count && !f.live && !f.safe_live);
        assert(f.safe_frees == f.safe_allocs - !!f.safe_failure_at);
        assert(f.frees == 1 && !memcmp(before, f.r[0].va, sizeof(before))); destroy(&f);
    }
    for (unsigned sub = 1; sub <= 4; ++sub) {
        struct fixture f; struct diag_scatter_input in = setup(&f, 3);
        f.r[0].pa[1] = DIAG_SCATTER_KERNEL; f.safe_bad_subpage = sub;
        f.metadata_unchanged = f.safe_unchanged_last = 1;
        struct diag_scatter_ops o = safe_ops(&f); struct diag_scatter_plan p = {0};
        assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_TRANSLATION);
        assert(f.safe_allocs == 1 && f.safe_frees == 1 && f.frees == 1); destroy(&f);
    }
    /* A late original failure must win before even requesting a bounce page. */
    struct fixture f; struct diag_scatter_input in = setup(&f, 3);
    f.r[0].pa[1] = DIAG_SCATTER_KERNEL;
    f.bad_translation_va = (u64)f.r[2].va + 0x3000; f.metadata_unchanged = 1;
    struct diag_scatter_ops o = safe_ops(&f); struct diag_scatter_plan p = {0};
    assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_TRANSLATION);
    assert(!f.safe_allocs && f.frees == 1); destroy(&f);
    for (unsigned missing = 0; missing < 2; ++missing) {
        in = setup(&f, 3); o = safe_ops(&f);
        if (missing) o.free_safe = NULL; else o.alloc_safe = NULL;
        assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_ARGUMENT);
        assert(!f.allocs && !f.safe_allocs); destroy(&f);
    }
}
static void safe_budget(void)
{
    struct fixture f; struct diag_scatter_input in = setup(&f, DIAG_SCATTER_SAFE_MAX + 1);
    for (u64 i = 0; i < DIAG_SCATTER_SAFE_MAX + 1; ++i) f.r[1].pa[i] = DIAG_SCATTER_INITRD + i * P;
    f.metadata_unchanged = 1;
    struct diag_scatter_ops o = safe_ops(&f); struct diag_scatter_plan p = {0};
    assert(diag_scatter_prepare(&o, &in, &workspace, &p) == DIAG_SCATTER_CAPACITY);
    assert(f.safe_allocs == DIAG_SCATTER_SAFE_MAX && f.safe_frees == DIAG_SCATTER_SAFE_MAX);
    assert(!p.safe_count && !p.metadata_kva && f.frees == 1); destroy(&f);
}
int main(void)
{
    success(3, 0); success(3, 1); success(677, 0); success(678, 0);
    success(700, 0); success(1400, 1);
    failures(); distinct_source_policy();
    phase_lifetimes(); bounced_successes(); safe_failures(); safe_budget();
    puts("scatter planner: phase-aware destinations, owned pre-arm bounce, finalized metadata, original/safe four-subpage/duplicate guards, 8192-page budget and exact cleanup PASS");
    return 0;
}
