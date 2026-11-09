/* SPDX-License-Identifier: BSD-2-Clause
 * Copyright (c) 2026 Meerzulee and Linux psX contributors.
 */
#ifndef DIAGNOSTIX_SCATTER_H
#define DIAGNOSTIX_SCATTER_H
#include "types.h"

#define DIAG_SCATTER_PAGE 0x4000ULL
#define DIAG_SCATTER_LIMIT (16ULL << 30)
#define DIAG_SCATTER_KERNEL 0x06000000ULL
#define DIAG_SCATTER_INITRD 0x180000000ULL
#define DIAG_SCATTER_RECORDS 40000ULL
#define DIAG_SCATTER_PER_NODE 682ULL
#define DIAG_SCATTER_AUX_MAX 8ULL
#define DIAG_SCATTER_SAFE_MAX 8192ULL /* 128 MiB of owned 16 KiB bounce pages */
#define DIAG_SCATTER_SAFE_SLOTS (DIAG_SCATTER_SAFE_MAX * 2)

struct diag_scatter_record { u64 src, dst, bytes; }; /* src=0: zero-fill */
struct diag_scatter_node {
    u64 next, count; /* physical next node, zero at end */
    struct diag_scatter_record records[DIAG_SCATTER_PER_NODE];
};
struct diag_scatter_buffer { void *kva; u64 allocation_bytes; };
struct diag_scatter_input {
    struct diag_scatter_buffer image;
    u64 image_bytes, setup_bytes, protected_bytes, kernel_init_size;
    struct diag_scatter_buffer initrd;
    u64 initrd_bytes;
    const struct diag_scatter_buffer *aux;
    u64 aux_count;
};
struct diag_scatter_ops {
    void *ctx;
    void *(*alloc)(void *ctx, u64 rounded_bytes);
    void (*free)(void *ctx, void *kva, u64 rounded_bytes);
    u64 (*physical)(void *ctx, u64 kva); /* zero means unavailable */
    int (*ram)(void *ctx, u64 physical, u64 bytes); /* strict final Linux destination span */
    int (*source_ram)(void *ctx, u64 physical, u64 bytes); /* owned native source/control backing */
    void *(*alloc_safe)(void *ctx); /* optional pair: one owned wired 16 KiB page */
    void (*free_safe)(void *ctx, void *kva); /* exact returned allocation KVA */
};
enum diag_scatter_error {
    DIAG_SCATTER_OK = 0,
    DIAG_SCATTER_ARGUMENT,
    DIAG_SCATTER_BOUNDS,
    DIAG_SCATTER_DESTINATION,
    DIAG_SCATTER_ALLOC,
    DIAG_SCATTER_TRANSLATION,
    DIAG_SCATTER_NOT_RAM,
    DIAG_SCATTER_CONFLICT,
    DIAG_SCATTER_DUPLICATE,
    DIAG_SCATTER_CAPACITY
};
/* Caller supplies static/private storage, never a native kernel stack object.
 * One invocation at a time per workspace. Native APIs stay inside callbacks;
 * bounce copies read/write only the validated, stable caller-owned KVAs. */
struct diag_scatter_workspace {
    u64 used[DIAG_SCATTER_LIMIT / DIAG_SCATTER_PAGE / 64];
    u16 safe_index[DIAG_SCATTER_SAFE_SLOTS]; /* zero or plan.safe[index - 1] */
};
struct diag_scatter_safe { u64 original_pa, safe_pa; void *kva; };
struct diag_scatter_plan {
    void *metadata_kva;
    u64 metadata_bytes, physical_head, total_records, node_count;
    enum diag_scatter_error error;
    u64 error_va, error_pa;
    u64 safe_count;
    struct diag_scatter_safe safe[DIAG_SCATTER_SAFE_MAX];
};
/* Return 0 on success, a nonzero enum diag_scatter_error on failure.
 * out must be zero-initialized and hold no live plan. Inputs remain caller-owned.
 * Every allocation page (including padding/aux) is checked at four 4KiB points.
 * Both RAM callbacks are required: source_ram validates already-owned native
 * backing; ram separately validates final Linux destinations. A source page
 * need not belong to a Linux E820 usable interval. Neither callback grants
 * ownership, and all translation, duplicate and overlap guards still apply.
 * The complete rounded kernel init_size must be eligible destination RAM and
 * disjoint from initrd, but its future decompressor workspace is not live until
 * every terminal copy has completed. Immediate conflict checks cover only the
 * rounded protected kernel copy and initrd. Aux pages must be consumed into
 * low storage before terminal copies; they may overlap either destination.
 * Setup-only/image padding pages are likewise not terminal copy sources.
 * All original pages, including metadata, padding and aux, pass source-RAM,
 * low-stub, four-subpage and unique-PA checks before any metadata/bounce writes.
 * An active image/initrd or metadata page overlapping an immediate destination
 * is copied to an owned alloc_safe page, then its physical references redirect.
 * Safe pages must pass the same checks and be disjoint from every original,
 * prior safe page and immediate destination. Native callbacks constrain their
 * pool separately. Missing callbacks refuse a collision without retrying.
 * At most 8192 pages are retained. Source bytes never change. Metadata safe
 * copies occur only after every descriptor and redirected next link is final.
 * Only output tails to 16KiB are zeroed, not the decompressor workspace.
 * On failure/release all owned safe pages and metadata are freed exactly once;
 * the original caller-owned source allocations are never freed by this API.
 * On failure diagnostic fields remain. Plan/workspace storage is large and
 * must be private static/caller storage, never native kernel stack storage.
 * All buffers/translations must remain wired and stable throughout this call;
 * callbacks must not mutate/revoke inputs. Successful metadata must stay
 * wired/immutable until terminal relocation.
 */
int diag_scatter_prepare(const struct diag_scatter_ops *ops,
                         const struct diag_scatter_input *input,
                         struct diag_scatter_workspace *workspace,
                         struct diag_scatter_plan *out);
void diag_scatter_release(const struct diag_scatter_ops *ops,
                          struct diag_scatter_plan *plan);
#endif
