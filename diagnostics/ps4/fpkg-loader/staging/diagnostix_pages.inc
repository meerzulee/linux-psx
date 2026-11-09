/* SPDX-License-Identifier: BSD-2-Clause
 * Copyright (c) 2026 Meerzulee and Linux psX contributors.
 * Exact FW12.02 kmem_alloc ABI: upstream fw_offsets.h and native review.
 * Native backing is wired page by page, without a physical-contiguity request.
 */
#define DIAG_KMEM_ALLOC_OFFSET 0x4659e0ULL
struct boot_record { void *mapped; size_t requested, bytes; };
static struct boot_record boot_records[5];

#define DIAG_SAFE_PAGE_LOW  0x10000000ULL
#define DIAG_SAFE_PAGE_HIGH 0x70000000ULL /* exclusive validated end */
#define DIAG_SAFE_PAGE_FLAGS 0x0102 /* reviewed M_ZERO | M_WAITOK */
#define DIAG_SAFE_PAGE_ATTR  0x06   /* reviewed VM_MEMATTR_DEFAULT */

_Static_assert(sizeof(vm_offset_t) == 8 && sizeof(vm_size_t) == 8 &&
               sizeof(size_t) == 8 && sizeof(void *) == 8,
               "Diagnostix requires the reviewed 64-bit allocator ABI");

/* One physical page for pre-arm collision replacement. The planner owns a
 * separate bounded list of these ORIGINAL native KVAs and releases each once;
 * they never consume the five ordinary image/control ledger slots. The range
 * excludes all supported immediate copy destinations, not future workspace.
 */
void *kernel_alloc_boot_safe_page(void)
{
    vm_offset_t mapped = 0;
    vm_paddr_t pa = 0;
    const char *reason = "native-profile";
    if (kern.kern_base < 0xffff800000000000ULL || !kern.kernel_map ||
        !*kern.kernel_map || !kern.kmem_alloc_contig || !kern.kmem_free ||
        !kern.pmap_extract || !kern.kernel_pmap_store)
        goto refused;
    mapped = kern.kmem_alloc_contig(*kern.kernel_map, PAGE_SIZE,
        DIAG_SAFE_PAGE_FLAGS, DIAG_SAFE_PAGE_LOW, DIAG_SAFE_PAGE_HIGH,
        PAGE_SIZE, 0, DIAG_SAFE_PAGE_ATTR);
    if (!mapped) { reason = "kmem-returned-zero"; goto refused; }
    if (mapped < 0xffff800000000000ULL || (mapped & PAGE_MASK) ||
        mapped > ~(vm_offset_t)0 - PAGE_SIZE) {
        reason = "native-va"; goto release;
    }
    pa = kern.pmap_extract(kern.kernel_pmap_store, mapped);
    if (!pa || (pa & PAGE_MASK) || pa < DIAG_SAFE_PAGE_LOW ||
        pa > DIAG_SAFE_PAGE_HIGH - PAGE_SIZE || !diag_source_span(pa, PAGE_SIZE)) {
        reason = "physical-span"; goto release;
    }
    for (size_t sub = 0x1000; sub < PAGE_SIZE; sub += 0x1000)
        if (kern.pmap_extract(kern.kernel_pmap_store, mapped + sub) != pa + sub) {
            reason = "native-page-translation"; goto release;
        }
    return (void *)mapped;
release:
    kern.kmem_free(*kern.kernel_map, mapped, PAGE_SIZE);
refused:
    if (kern.printf)
        kern.printf("DIAGNOSTIX_SAFE_PAGE_V1 refused reason=%s va=0x%llx pa=0x%llx\n",
                    reason, (unsigned long long)mapped, (unsigned long long)pa);
    return NULL;
}

void kernel_free_boot_safe_page(void *addr)
{
    vm_offset_t original = (vm_offset_t)addr;
    /* Ownership and once-only cleanup are enforced by the planner's list.
     * Never derive a DMAP alias or a physical address for kmem_free. */
    if (!original || original < 0xffff800000000000ULL || (original & PAGE_MASK) ||
        original > ~(vm_offset_t)0 - PAGE_SIZE || !kern.kernel_map ||
        !*kern.kernel_map || !kern.kmem_free)
        return;
    kern.kmem_free(*kern.kernel_map, original, PAGE_SIZE);
}

void *kernel_alloc_boot(size_t size)
{
    unsigned int slot;
    size_t bytes;
    vm_offset_t mapped;
    const char *reason = "request-size";
    if (!size || size > 1024ULL * 1024 * 1024 || size > ~(size_t)0 - PAGE_MASK)
        goto refused;
    bytes = (size + PAGE_MASK) & ~(size_t)PAGE_MASK;
    for (slot = 0; slot < 5 && boot_records[slot].mapped; ++slot);
    if (slot == 5) { reason = "ledger-full"; goto refused; }
    if (kern.kern_base < 0xffff800000000000ULL ||
        kern.kern_base > ~(u64)0 - DIAG_KMEM_ALLOC_OFFSET || !kern.kernel_map ||
        !kern.kmem_alloc) {
        reason = "native-profile"; goto refused;
    }
    kern.printf("DIAGNOSTIX_PAGES_V1 begin requested=%llu rounded=%llu wired=1 contiguous=0\n",
                (unsigned long long)size, (unsigned long long)bytes);
    mapped = kern.kmem_alloc(*kern.kernel_map, size);
    kern.printf("DIAGNOSTIX_PAGES_V1 end requested=%llu vm=0x%llx\n",
                (unsigned long long)size, (unsigned long long)mapped);
    if (!mapped) { reason = "kmem-returned-zero"; goto refused; }
    if (mapped < 0xffff800000000000ULL || (mapped & PAGE_MASK) ||
        mapped > ~(vm_offset_t)0 - (bytes - 1)) {
        reason = "native-va"; goto release;
    }
    /* Each native page must be physically intact; different pages may scatter.
     * Also inspect 4KiB translations: x86 paging granularity differs from Orbis.
     */
    for (size_t off = 0; off < bytes; off += PAGE_SIZE) {
        vm_paddr_t pa = kern.pmap_extract(kern.kernel_pmap_store, mapped + off);
        if (!pa || (pa & PAGE_MASK) || !diag_source_span(pa, PAGE_SIZE)) {
            kern.printf("DIAGNOSTIX_PAGES_V1 rejected_page vm=0x%llx offset=0x%llx pa=0x%llx page_bytes=0x%llx ram_span=%u sub1=0x%llx sub2=0x%llx sub3=0x%llx\n",
                        (unsigned long long)mapped, (unsigned long long)off,
                        (unsigned long long)pa, (unsigned long long)PAGE_SIZE,
                        (unsigned int)diag_ram_span(pa, PAGE_SIZE),
                        (unsigned long long)kern.pmap_extract(kern.kernel_pmap_store, mapped + off + 0x1000),
                        (unsigned long long)kern.pmap_extract(kern.kernel_pmap_store, mapped + off + 0x2000),
                        (unsigned long long)kern.pmap_extract(kern.kernel_pmap_store, mapped + off + 0x3000));
            reason = !pa ? "page-unmapped" : (pa & PAGE_MASK) ? "page-alignment" : "page-not-source-ram";
            goto release;
        }
        for (size_t sub = 0x1000; sub < PAGE_SIZE; sub += 0x1000)
            if (kern.pmap_extract(kern.kernel_pmap_store, mapped + off + sub) != pa + sub) {
                reason = "native-page-translation"; goto release;
            }
    }
    boot_records[slot] = (struct boot_record){(void *)mapped, size, bytes};
    return (void *)mapped;
release:
    kern.kmem_free(*kern.kernel_map, mapped, bytes);
refused:
    kern.printf("DIAGNOSTIX_PAGES_V1 refused reason=%s requested=%llu\n",
                reason, (unsigned long long)size);
    return NULL;
}

void kernel_free_boot(void *addr, size_t size)
{
    if (!addr) return;
    for (unsigned int i = 0; i < 5; ++i) {
        struct boot_record *record = &boot_records[i];
        if (record->mapped != addr) continue;
        if (record->requested != size) {
            kern.printf("Diagnostix refuses mismatched wired-buffer cleanup\n");
            return;
        }
        kern.kmem_free(*kern.kernel_map, (vm_offset_t)addr, record->bytes);
        *record = (struct boot_record){0};
        return;
    }
}
