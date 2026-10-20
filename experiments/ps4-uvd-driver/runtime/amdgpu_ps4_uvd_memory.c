// SPDX-License-Identifier: MIT
/* Liverpool UVD fixed-VMID native-address mirror. No post-entry destructor. */
#include <linux/io.h>
#include <linux/overflow.h>
#include <linux/sizes.h>
#include <linux/slab.h>
#include <drm/ttm/ttm_tt.h>

#include "amdgpu.h"
#include "amdgpu_gmc.h"
#include "amdgpu_object.h"
#include "amdgpu_ttm.h"
#include "amdgpu_vm.h"
#include "amdgpu_ps4_uvd_memory.h"
#include "gmc/gmc_7_1_d.h"
#include "gmc/gmc_7_1_sh_mask.h"

#define PS4_UVD_LEAF_ENTRIES 1024ULL
#define PS4_UVD_LEAF_BYTES (PS4_UVD_LEAF_ENTRIES * sizeof(u64))
#define PS4_UVD_LEAF_SPAN (PS4_UVD_LEAF_ENTRIES * AMDGPU_GPU_PAGE_SIZE)
#define PS4_UVD_MAX_PD_BYTES SZ_1M
#define PS4_UVD_MAX_GART_BYTES SZ_1G
#define PS4_UVD_MAX_VRAM_BYTES (8ULL * SZ_1G)
/* Same address restriction as gmc_v7_0_get_vm_pde(), checked before its BUG. */
#define PS4_UVD_BAD_PDE_ADDR 0xffffff0000000fffULL

static bool ps4_uvd_memory_overlap(u64 a, u64 a_end, u64 b, u64 b_end)
{
	return a < b_end && b < a_end;
}

/* Pure geometry, before allocation/MMIO. End addresses are exclusive. */
static int ps4_uvd_memory_plan(u64 max_pfn, u64 gart_start, u64 gart_bytes,
			      u64 vram_start, u64 vram_bytes,
			      struct amdgpu_ps4_uvd_memory *m)
{
	u64 gart_end, vram_end, private_end, root_bytes;

	if (!max_pfn || max_pfn > U32_MAX || !gart_bytes || !vram_bytes ||
	    gart_bytes > PS4_UVD_MAX_GART_BYTES ||
	    vram_bytes > PS4_UVD_MAX_VRAM_BYTES ||
	    ((gart_start | gart_bytes | vram_start | vram_bytes) &
	     (PS4_UVD_LEAF_SPAN - 1)))
		return -EINVAL;
	if (check_add_overflow(gart_start, gart_bytes, &gart_end) ||
	    check_add_overflow(vram_start, vram_bytes, &vram_end) ||
	    check_add_overflow(AMDGPU_PS4_UVD_PRIVATE_VA,
		AMDGPU_PS4_UVD_PRIVATE_BYTES, &private_end))
		return -EOVERFLOW;
	if ((gart_end >> AMDGPU_GPU_PAGE_SHIFT) > max_pfn ||
	    (vram_end >> AMDGPU_GPU_PAGE_SHIFT) > max_pfn ||
	    (private_end >> AMDGPU_GPU_PAGE_SHIFT) > max_pfn ||
	    ps4_uvd_memory_overlap(gart_start, gart_end, vram_start, vram_end) ||
	    ps4_uvd_memory_overlap(gart_start, gart_end,
		AMDGPU_PS4_UVD_PRIVATE_VA, private_end) ||
	    ps4_uvd_memory_overlap(vram_start, vram_end,
		AMDGPU_PS4_UVD_PRIVATE_VA, private_end))
		return -ERANGE;
	m->root_entries = DIV_ROUND_UP_ULL(max_pfn, PS4_UVD_LEAF_ENTRIES);
	if (check_mul_overflow(m->root_entries, (u64)sizeof(u64), &root_bytes) ||
	    root_bytes > PS4_UVD_MAX_PD_BYTES)
		return -EOVERFLOW;
	m->pd.bytes = ALIGN(root_bytes, (u64)PAGE_SIZE);
	m->gart_first_pde = gart_start / PS4_UVD_LEAF_SPAN;
	m->gart_pdes = gart_bytes / PS4_UVD_LEAF_SPAN;
	m->vram_first_pde = vram_start / PS4_UVD_LEAF_SPAN;
	m->vram_pdes = vram_bytes / PS4_UVD_LEAF_SPAN;
	m->private_pde = AMDGPU_PS4_UVD_PRIVATE_VA / PS4_UVD_LEAF_SPAN;
	if (m->gart_first_pde >= m->root_entries ||
	    m->gart_pdes > m->root_entries - m->gart_first_pde ||
	    m->vram_first_pde >= m->root_entries ||
	    m->vram_pdes > m->root_entries - m->vram_first_pde ||
	    m->private_pde >= m->root_entries ||
	    check_mul_overflow(m->vram_pdes, (u64)PS4_UVD_LEAF_BYTES,
			       &m->vram_pt.bytes))
		return -ERANGE;
	m->private_pt.bytes = PS4_UVD_LEAF_BYTES;
	m->gart_start = gart_start;
	m->gart_bytes = gart_bytes;
	m->vram_start = vram_start;
	m->vram_bytes = vram_bytes;
	m->max_pfn = max_pfn;
	return 0;
}

static int ps4_uvd_memory_context(struct amdgpu_device *adev,
				const struct amdgpu_ps4_uvd_memory *m)
{
	struct amdgpu_vmid_mgr *mgr =
		&adev->vm_manager.id_mgr[AMDGPU_GFXHUB(0)];
	u32 control = RREG32(mmVM_CONTEXT1_CNTL);

	if (!m->vmid4_reserved || mgr->num_ids != 8 ||
	    !list_empty(&mgr->ids[AMDGPU_PS4_UVD_MEMORY_VMID].list) ||
	    PAGE_SIZE != AMDGPU_GPU_PAGE_SIZE ||
	    (adev->asic_type != CHIP_LIVERPOOL && adev->asic_type != CHIP_GLADIUS) ||
	    adev->vm_manager.num_level != 1 ||
	    adev->vm_manager.root_level != AMDGPU_VM_PDB0 ||
	    adev->vm_manager.block_size != 10 ||
	    adev->vm_manager.max_pfn != m->max_pfn ||
	    !REG_GET_FIELD(control, VM_CONTEXT1_CNTL, ENABLE_CONTEXT) ||
	    REG_GET_FIELD(control, VM_CONTEXT1_CNTL, PAGE_TABLE_DEPTH) != 1 ||
	    REG_GET_FIELD(control, VM_CONTEXT1_CNTL, PAGE_TABLE_BLOCK_SIZE) != 1 ||
	    control != m->context1_control ||
	    RREG32(mmVM_CONTEXT0_PAGE_TABLE_START_ADDR +
		AMDGPU_PS4_UVD_MEMORY_VMID) != m->vmid4_start ||
	    RREG32(mmVM_CONTEXT0_PAGE_TABLE_END_ADDR +
		AMDGPU_PS4_UVD_MEMORY_VMID) != m->vmid4_end ||
	    m->vmid4_start != 0 || m->vmid4_end != m->max_pfn - 1)
		return -EINVAL;
	return 0;
}

/* Validate the address before calling the ASIC helper, which contains BUG_ON. */
static int ps4_uvd_memory_vram_bo(struct amdgpu_device *adev,
				struct amdgpu_bo *bo, u64 gpu_addr, u64 bytes)
{
	u64 end;

	if (!bo || amdgpu_ttm_adev(bo->tbo.bdev) != adev ||
	    !bo->tbo.resource || !bo->tbo.pin_count || !bytes ||
	    bo->tbo.resource->mem_type != TTM_PL_VRAM ||
	    (bo->flags & AMDGPU_GEM_CREATE_ENCRYPTED) ||
	    (bo->tbo.resource->placement & TTM_PL_FLAG_CONTIGUOUS) == 0 ||
	    amdgpu_bo_gpu_offset(bo) != gpu_addr ||
	    amdgpu_bo_size(bo) < bytes ||
	    ((gpu_addr | bytes) & AMDGPU_GPU_PAGE_MASK) ||
	    check_add_overflow(gpu_addr, bytes, &end))
		return -EINVAL;
	if (gpu_addr < adev->gmc.vram_start || end - 1 > adev->gmc.vram_end ||
	    (gpu_addr & PS4_UVD_BAD_PDE_ADDR) ||
	    ((end - AMDGPU_GPU_PAGE_SIZE) & PS4_UVD_BAD_PDE_ADDR))
		return -ERANGE;
	return 0;
}

static int ps4_uvd_memory_alloc(struct amdgpu_device *adev,
			       struct amdgpu_ps4_uvd_memory_bo *bo)
{
	int r;

	r = amdgpu_bo_create_kernel(adev, bo->bytes, PAGE_SIZE,
		AMDGPU_GEM_DOMAIN_VRAM, &bo->bo, &bo->gpu_addr, &bo->cpu_addr);
	if (r)
		return r;
	if (!bo->cpu_addr)
		return -EINVAL;
	return ps4_uvd_memory_vram_bo(adev, bo->bo, bo->gpu_addr, bo->bytes);
}

static int ps4_uvd_memory_pde(struct amdgpu_device *adev,
			     struct amdgpu_bo *bo, u64 offset, u64 *pte)
{
	u64 addr, flags;

	if (!bo || !bo->tbo.resource ||
	    (amdgpu_bo_gpu_offset(bo) & PS4_UVD_BAD_PDE_ADDR))
		return -EINVAL;
	amdgpu_gmc_get_pde_for_bo(bo, adev->vm_manager.root_level, &addr, &flags);
	if (!(flags & AMDGPU_PTE_VALID) || (flags & AMDGPU_PTE_SYSTEM) ||
	    (flags & adev->gmc.pte_addr_mask) ||
	    (offset & (PS4_UVD_LEAF_BYTES - 1)) ||
	    offset >= amdgpu_bo_size(bo) ||
	    PS4_UVD_LEAF_BYTES > amdgpu_bo_size(bo) - offset ||
	    check_add_overflow(addr, offset, &addr) ||
	    (addr & PS4_UVD_BAD_PDE_ADDR))
		return -ERANGE;
	*pte = addr | flags;
	return 0;
}

static int ps4_uvd_memory_gart_current(struct amdgpu_device *adev,
				      const struct amdgpu_ps4_uvd_memory *m)
{
	u64 pde, table_bytes;
	u32 control = RREG32(mmVM_CONTEXT0_CNTL);
	int r;

	if (!m->gart_pinned || adev->gart.bo != m->gart_bo ||
	    !m->gart_cpu_addr || adev->gart.ptr != m->gart_cpu_addr ||
	    adev->gart.table_size != m->gart_table_bytes ||
	    adev->gmc.gart_start != m->gart_start ||
	    adev->gmc.gart_size != m->gart_bytes ||
	    adev->gmc.gart_end != m->gart_start + m->gart_bytes - 1 ||
	    check_mul_overflow((u64)adev->gart.num_gpu_pages,
		(u64)sizeof(u64), &table_bytes) ||
	    table_bytes != m->gart_table_bytes ||
	    adev->gart.num_gpu_pages != m->gart_bytes / AMDGPU_GPU_PAGE_SIZE ||
	    !REG_GET_FIELD(control, VM_CONTEXT0_CNTL, ENABLE_CONTEXT) ||
	    REG_GET_FIELD(control, VM_CONTEXT0_CNTL, PAGE_TABLE_DEPTH) != 0 ||
	    RREG32(mmVM_CONTEXT0_PAGE_TABLE_START_ADDR) !=
		(m->gart_start >> AMDGPU_GPU_PAGE_SHIFT) ||
	    RREG32(mmVM_CONTEXT0_PAGE_TABLE_END_ADDR) !=
		((m->gart_start + m->gart_bytes - 1) >> AMDGPU_GPU_PAGE_SHIFT) ||
	    RREG32(mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR) !=
		(m->gart_gpu_addr >> AMDGPU_GPU_PAGE_SHIFT))
		return -EINVAL;
	r = ps4_uvd_memory_vram_bo(adev, m->gart_bo, m->gart_gpu_addr,
				 m->gart_table_bytes);
	if (r)
		return r;
	r = ps4_uvd_memory_pde(adev, m->gart_bo, 0, &pde);
	if (r || pde != (m->gart_gpu_addr | m->gart_pde_flags))
		return r ? r : -EINVAL;
	return 0;
}

static int ps4_uvd_memory_expected_pde(struct amdgpu_device *adev,
				const struct amdgpu_ps4_uvd_memory *m,
				u64 index, u64 *pte)
{
	if (index >= m->gart_first_pde &&
	    index - m->gart_first_pde < m->gart_pdes)
		return ps4_uvd_memory_pde(adev, m->gart_bo,
			(index - m->gart_first_pde) * PS4_UVD_LEAF_BYTES, pte);
	if (index >= m->vram_first_pde &&
	    index - m->vram_first_pde < m->vram_pdes)
		return ps4_uvd_memory_pde(adev, m->vram_pt.bo,
			(index - m->vram_first_pde) * PS4_UVD_LEAF_BYTES, pte);
	if (index == m->private_pde)
		return ps4_uvd_memory_pde(adev, m->private_pt.bo, 0, pte);
	*pte = 0;
	return 0;
}

/* CPU preparation readback only, not all-page device visibility or DMA drain. */
static int ps4_uvd_memory_tables_valid(struct amdgpu_device *adev,
				      const struct amdgpu_ps4_uvd_memory *m)
{
	u64 i, expected;
	int r;

	for (i = 0; i < m->pd.bytes / sizeof(u64); ++i) {
		r = ps4_uvd_memory_expected_pde(adev, m, i, &expected);
		if (r || readq((void __iomem *)m->pd.cpu_addr + i * sizeof(u64)) !=
		    expected)
			return r ? r : -EINVAL;
	}
	for (i = 0; i < m->vram_bytes / AMDGPU_GPU_PAGE_SIZE; ++i) {
		expected = (m->vram_pa + i * AMDGPU_GPU_PAGE_SIZE) | m->vram_pte_flags;
		if (readq((void __iomem *)m->vram_pt.cpu_addr + i * sizeof(u64)) !=
		    expected)
			return -EINVAL;
	}
	for (i = 0; i < AMDGPU_PS4_UVD_PRIVATE_PAGES; ++i)
		if (readq((void __iomem *)m->private_pt.cpu_addr + i * sizeof(u64)) !=
		    m->private_ptes[i])
			return -EINVAL;
	return 0;
}

static int ps4_uvd_memory_hold_gart(struct amdgpu_device *adev,
				  struct amdgpu_ps4_uvd_memory *m)
{
	struct amdgpu_bo *bo = adev->gart.bo;
	u64 pde;
	int r;

	if (!bo || !adev->gart.ptr)
		return -EINVAL;
	r = amdgpu_bo_reserve(bo, false);
	if (r)
		return r;
	r = ps4_uvd_memory_vram_bo(adev, bo, amdgpu_bo_gpu_offset(bo),
				 adev->gart.table_size);
	if (!r)
		r = ps4_uvd_memory_pde(adev, bo, 0, &pde);
	if (!r)
		r = amdgpu_bo_pin(bo, AMDGPU_GEM_DOMAIN_VRAM);
	if (!r) {
		m->gart_bo = amdgpu_bo_ref(bo);
		m->gart_pinned = true;
		m->gart_gpu_addr = amdgpu_bo_gpu_offset(bo);
		m->gart_pde_flags = pde & ~adev->gmc.pte_addr_mask;
		m->gart_table_bytes = adev->gart.table_size;
		m->gart_cpu_addr = adev->gart.ptr;
	}
	amdgpu_bo_unreserve(bo);
	return r;
}

int amdgpu_ps4_uvd_memory_prepare(struct amdgpu_device *adev,
				struct amdgpu_ps4_uvd_memory *m,
				bool vmid4_reserved)
{
	u64 i, pte, pa_end, flags, required, vram_bytes;
	int r, cleanup;

	if (!adev || !m || !vmid4_reserved)
		return -EINVAL;
	if (m->owner || m->entered || m->prepared || m->gart_bo || m->pd.bo ||
	    m->vram_pt.bo || m->private_pt.bo || m->private_ptes ||
	    m->private_pfns || m->map_count)
		return -EALREADY;
	m->owner = adev;
	if (adev->gmc.vram_end < adev->gmc.vram_start ||
	    check_add_overflow(adev->gmc.vram_end - adev->gmc.vram_start,
			       1ULL, &vram_bytes))
		return -ERANGE;
	r = ps4_uvd_memory_plan(adev->vm_manager.max_pfn, adev->gmc.gart_start,
		adev->gmc.gart_size, adev->gmc.vram_start, vram_bytes, m);
	if (r)
		goto fail;
	m->vmid4_reserved = vmid4_reserved;
	m->context1_control = RREG32(mmVM_CONTEXT1_CNTL);
	m->vmid4_start = RREG32(mmVM_CONTEXT0_PAGE_TABLE_START_ADDR +
		AMDGPU_PS4_UVD_MEMORY_VMID);
	m->vmid4_end = RREG32(mmVM_CONTEXT0_PAGE_TABLE_END_ADDR +
		AMDGPU_PS4_UVD_MEMORY_VMID);
	r = ps4_uvd_memory_context(adev, m);
	if (r)
		goto fail;
	r = ps4_uvd_memory_hold_gart(adev, m);
	if (r)
		goto fail;
	r = ps4_uvd_memory_gart_current(adev, m);
	if (r)
		goto fail;
	r = ps4_uvd_memory_alloc(adev, &m->pd);
	if (!r)
		r = ps4_uvd_memory_alloc(adev, &m->vram_pt);
	if (!r)
		r = ps4_uvd_memory_alloc(adev, &m->private_pt);
	if (r)
		goto fail;
	m->private_ptes = kcalloc(AMDGPU_PS4_UVD_PRIVATE_PAGES,
				 sizeof(*m->private_ptes), GFP_KERNEL);
	m->private_pfns = kcalloc(AMDGPU_PS4_UVD_PRIVATE_PAGES,
				 sizeof(*m->private_pfns), GFP_KERNEL);
	if (!m->private_ptes || !m->private_pfns) {
		r = -ENOMEM;
		goto fail;
	}
	m->vram_pa = amdgpu_gmc_vram_mc2pa(adev, m->vram_start);
	if ((m->vram_pa & AMDGPU_GPU_PAGE_MASK) ||
	    check_add_overflow(m->vram_pa, m->vram_bytes, &pa_end) ||
	    (m->vram_pa & ~adev->gmc.pte_addr_mask) ||
	    ((pa_end - AMDGPU_GPU_PAGE_SIZE) & ~adev->gmc.pte_addr_mask)) {
		r = -ERANGE;
		goto fail;
	}
	flags = amdgpu_ttm_tt_pte_flags(adev, m->vram_pt.bo->tbo.ttm,
				      m->vram_pt.bo->tbo.resource);
	amdgpu_gmc_get_vm_pte(adev, NULL, m->vram_pt.bo, 0, &flags);
	required = AMDGPU_PTE_VALID | AMDGPU_PTE_READABLE | AMDGPU_PTE_WRITEABLE;
	if ((flags & required) != required || (flags & AMDGPU_PTE_SYSTEM) ||
	    (flags & ~(required | AMDGPU_PTE_SNOOPED))) {
		r = -EINVAL;
		goto fail;
	}
	m->vram_pte_flags = flags;
	memset_io((void __iomem *)m->pd.cpu_addr, 0, m->pd.bytes);
	memset_io((void __iomem *)m->private_pt.cpu_addr, 0, m->private_pt.bytes);
	for (i = 0; i < m->vram_bytes / AMDGPU_GPU_PAGE_SIZE; ++i)
		writeq((m->vram_pa + i * AMDGPU_GPU_PAGE_SIZE) | flags,
		       (void __iomem *)m->vram_pt.cpu_addr + i * sizeof(u64));
	for (i = 0; i < m->root_entries; ++i) {
		r = ps4_uvd_memory_expected_pde(adev, m, i, &pte);
		if (r)
			goto fail;
		writeq(pte, (void __iomem *)m->pd.cpu_addr + i * sizeof(u64));
	}
	r = ps4_uvd_memory_tables_valid(adev, m);
	if (r)
		goto fail;
	m->prepared = true;
	return 0;
fail:
	cleanup = amdgpu_ps4_uvd_memory_cleanup_pre_entry(adev, m);
	return cleanup ? cleanup : r;
}

static int ps4_uvd_memory_private_input(struct amdgpu_device *adev,
		const struct amdgpu_ps4_uvd_memory *m, struct amdgpu_bo *bo,
		u64 native_gpu_addr, u64 bytes, u64 private_va, u64 *first)
{
	u64 end, private_end = AMDGPU_PS4_UVD_PRIVATE_VA +
		AMDGPU_PS4_UVD_PRIVATE_BYTES;
	struct ttm_tt *ttm;

	if (!m->prepared || m->entered || m->map_count >= AMDGPU_PS4_UVD_PRIVATE_MAPS ||
	    !bo || amdgpu_ttm_adev(bo->tbo.bdev) != adev ||
	    !bo->tbo.resource || !bo->tbo.pin_count || !bytes ||
	    bo->tbo.resource->mem_type != TTM_PL_TT ||
	    drm_gem_is_imported(&bo->tbo.base) ||
	    (bo->flags & AMDGPU_GEM_CREATE_ENCRYPTED) ||
	    amdgpu_bo_gpu_offset(bo) != native_gpu_addr ||
	    bytes > amdgpu_bo_size(bo) ||
	    ((native_gpu_addr | private_va | bytes) & AMDGPU_GPU_PAGE_MASK))
		return -EINVAL;
	ttm = bo->tbo.ttm;
	if (!ttm || !ttm_tt_is_populated(ttm) || !ttm->dma_address || !ttm->pages ||
	    (ttm->page_flags & TTM_TT_FLAG_EXTERNAL) ||
	    amdgpu_ttm_tt_get_usermm(ttm) ||
	    bytes / AMDGPU_GPU_PAGE_SIZE > ttm->num_pages ||
	    check_add_overflow(private_va, bytes, &end) ||
	    private_va < AMDGPU_PS4_UVD_PRIVATE_VA || end > private_end)
		return -ERANGE;
	*first = (private_va - AMDGPU_PS4_UVD_PRIVATE_VA) >> AMDGPU_GPU_PAGE_SHIFT;
	return ps4_uvd_memory_gart_current(adev, m);
}

/* Validate all input and previously mapped DMA/PFN pages before any PT write. */
static int ps4_uvd_memory_private_pages(struct amdgpu_device *adev,
		const struct amdgpu_ps4_uvd_memory *m, struct amdgpu_bo *bo,
		u64 native_gpu_addr, u64 bytes, u64 first, u64 *flags)
{
	struct ttm_tt *ttm = bo->tbo.ttm;
	u64 p, q, count = bytes >> AMDGPU_GPU_PAGE_SHIFT;
	u64 required = AMDGPU_PTE_VALID | AMDGPU_PTE_SYSTEM |
		AMDGPU_PTE_READABLE | AMDGPU_PTE_WRITEABLE;

	*flags = amdgpu_ttm_tt_pte_flags(adev, ttm, bo->tbo.resource);
	if ((*flags & required) != required ||
	    (*flags & ~(required | AMDGPU_PTE_SNOOPED)))
		return -EINVAL;
	for (p = 0; p < count; ++p) {
		u64 gpu, index, pte, dma = ttm->dma_address[p];
		unsigned long pfn;

		if (!ttm->pages[p] || (dma & ~adev->gmc.pte_addr_mask) ||
		    check_add_overflow(native_gpu_addr, p * AMDGPU_GPU_PAGE_SIZE, &gpu) ||
		    gpu < m->gart_start || gpu - m->gart_start >= m->gart_bytes ||
		    m->private_ptes[first + p])
			return -ERANGE;
		index = (gpu - m->gart_start) >> AMDGPU_GPU_PAGE_SHIFT;
		if (index >= m->gart_table_bytes / sizeof(u64))
			return -ERANGE;
		pte = readq((void __iomem *)m->gart_cpu_addr + index * sizeof(u64));
		if (pte != (dma | *flags))
			return -EINVAL;
		pfn = page_to_pfn(ttm->pages[p]);
		if (!pfn_valid(pfn))
			return -EINVAL;
		for (q = 0; q < AMDGPU_PS4_UVD_PRIVATE_PAGES; ++q)
			if (m->private_ptes[q] &&
			    ((m->private_ptes[q] & adev->gmc.pte_addr_mask) == dma ||
			     m->private_pfns[q] == pfn))
				return -EINVAL;
		for (q = 0; q < p; ++q)
			if (ttm->dma_address[q] == dma ||
			    page_to_pfn(ttm->pages[q]) == pfn)
				return -EINVAL;
	}
	return 0;
}

int amdgpu_ps4_uvd_memory_map_private_gtt(struct amdgpu_device *adev,
				struct amdgpu_ps4_uvd_memory *m,
				struct amdgpu_bo *bo, u64 native_gpu_addr,
				u64 bytes, u64 private_va)
{
	u64 p, first, flags;
	struct amdgpu_ps4_uvd_memory_mapping *map;
	int r;

	if (!adev || !m || m->owner != adev || !bo || m->entered)
		return -EINVAL;
	r = amdgpu_bo_reserve(bo, false);
	if (r)
		return r;
	r = ps4_uvd_memory_private_input(adev, m, bo, native_gpu_addr,
					 bytes, private_va, &first);
	if (!r)
		r = ps4_uvd_memory_private_pages(adev, m, bo, native_gpu_addr,
						 bytes, first, &flags);
	if (!r)
		r = amdgpu_bo_pin(bo, AMDGPU_GEM_DOMAIN_GTT);
	if (r)
		goto out;
	map = &m->maps[m->map_count++];
	map->bo = amdgpu_bo_ref(bo);
	map->native_gpu_addr = native_gpu_addr;
	map->private_va = private_va;
	map->bytes = bytes;
	for (p = 0; p < bytes / AMDGPU_GPU_PAGE_SIZE; ++p) {
		u64 slot = first + p;

		m->private_ptes[slot] = bo->tbo.ttm->dma_address[p] | flags;
		m->private_pfns[slot] = page_to_pfn(bo->tbo.ttm->pages[p]);
		writeq(m->private_ptes[slot],
		       (void __iomem *)m->private_pt.cpu_addr + slot * sizeof(u64));
	}
	r = ps4_uvd_memory_tables_valid(adev, m);
	if (r)
		m->prepared = false;
out:
	amdgpu_bo_unreserve(bo);
	return r;
}

/* Match the CIK GMC flush policy: order CPU writes/HDP, invalidate only4,
 * and drain posted MMIO with a response read. This is an issued invalidate,
 * not a claim of all-page visibility, engine idleness or completed DMA.
 */
static void ps4_uvd_memory_flush4(struct amdgpu_device *adev)
{
	mb();
	amdgpu_device_flush_hdp(adev, NULL);
	WREG32(mmVM_INVALIDATE_REQUEST, 1U << AMDGPU_PS4_UVD_MEMORY_VMID);
	(void)RREG32(mmVM_INVALIDATE_RESPONSE);
}

int amdgpu_ps4_uvd_memory_install(struct amdgpu_device *adev,
				struct amdgpu_ps4_uvd_memory *m)
{
	int r;

	if (!adev || !m || m->owner != adev || !m->prepared || m->entered ||
	    !m->map_count)
		return -EINVAL;
	r = ps4_uvd_memory_context(adev, m);
	if (!r)
		r = ps4_uvd_memory_gart_current(adev, m);
	if (!r)
		r = ps4_uvd_memory_tables_valid(adev, m);
	if (r)
		return r;
	if ((m->pd.gpu_addr & PS4_UVD_BAD_PDE_ADDR) ||
	    (m->pd.gpu_addr >> AMDGPU_GPU_PAGE_SHIFT) > U32_MAX)
		return -ERANGE;
	m->original_root = RREG32(mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR +
		AMDGPU_PS4_UVD_MEMORY_VMID);
	/* Ownership is irrevocable even if the write/readback below fails. */
	WRITE_ONCE(m->entered, true);
	wmb();
	WREG32(mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + AMDGPU_PS4_UVD_MEMORY_VMID,
		m->pd.gpu_addr >> AMDGPU_GPU_PAGE_SHIFT);
	if (RREG32(mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR +
		AMDGPU_PS4_UVD_MEMORY_VMID) !=
	    m->pd.gpu_addr >> AMDGPU_GPU_PAGE_SHIFT)
		return -EIO;
	ps4_uvd_memory_flush4(adev);
	m->installed = true;
	return 0;
}

int amdgpu_ps4_uvd_memory_refresh(struct amdgpu_device *adev,
				struct amdgpu_ps4_uvd_memory *m)
{
	int r;

	if (!adev || !m || m->owner != adev || !m->entered || !m->installed)
		return -EINVAL;
	r = ps4_uvd_memory_context(adev, m);
	if (!r)
		r = ps4_uvd_memory_gart_current(adev, m);
	if (r)
		return r;
	if (adev->gmc.vram_start != m->vram_start ||
	    adev->gmc.vram_end != m->vram_start + m->vram_bytes - 1 ||
	    amdgpu_gmc_vram_mc2pa(adev, m->vram_start) != m->vram_pa ||
	    RREG32(mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR +
		AMDGPU_PS4_UVD_MEMORY_VMID) !=
	    m->pd.gpu_addr >> AMDGPU_GPU_PAGE_SHIFT)
		return -EINVAL;
	ps4_uvd_memory_flush4(adev);
	return 0;
}

static int ps4_uvd_memory_drop(struct amdgpu_bo **bo, bool unmap)
{
	int r;

	if (!*bo)
		return 0;
	r = amdgpu_bo_reserve(*bo, false);
	if (r)
		return r;
	if (unmap)
		amdgpu_bo_kunmap(*bo);
	amdgpu_bo_unpin(*bo);
	amdgpu_bo_unreserve(*bo);
	amdgpu_bo_unref(bo);
	return 0;
}

int amdgpu_ps4_uvd_memory_cleanup_pre_entry(struct amdgpu_device *adev,
				struct amdgpu_ps4_uvd_memory *m)
{
	u32 i;
	int r;

	if (!adev || !m || (m->owner && m->owner != adev))
		return -EINVAL;
	if (READ_ONCE(m->entered))
		return -EBUSY;
	m->prepared = false;
	for (i = 0; i < m->map_count; ++i) {
		r = ps4_uvd_memory_drop(&m->maps[i].bo, false);
		if (r)
			return r;
	}
	r = ps4_uvd_memory_drop(&m->private_pt.bo, true);
	if (!r)
		r = ps4_uvd_memory_drop(&m->vram_pt.bo, true);
	if (!r)
		r = ps4_uvd_memory_drop(&m->pd.bo, true);
	if (!r)
		r = ps4_uvd_memory_drop(&m->gart_bo, false);
	if (r)
		return r;
	kfree(m->private_pfns);
	kfree(m->private_ptes);
	memset(m, 0, sizeof(*m));
	return 0;
}
