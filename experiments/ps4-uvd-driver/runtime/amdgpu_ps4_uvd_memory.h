/* SPDX-License-Identifier: MIT */
#ifndef AMDGPU_PS4_UVD_MEMORY_H
#define AMDGPU_PS4_UVD_MEMORY_H

#include <linux/types.h>

#define AMDGPU_PS4_UVD_MEMORY_VMID 4U
#define AMDGPU_PS4_UVD_PRIVATE_VA 0x300000000ULL
#define AMDGPU_PS4_UVD_PRIVATE_BYTES 0x400000ULL
#define AMDGPU_PS4_UVD_PRIVATE_PAGES 1024U
#define AMDGPU_PS4_UVD_PRIVATE_MAPS 16U

struct amdgpu_bo;
struct amdgpu_device;

struct amdgpu_ps4_uvd_memory_bo {
	struct amdgpu_bo *bo;
	u64 gpu_addr;
	void *cpu_addr;
	u64 bytes;
};

struct amdgpu_ps4_uvd_memory_mapping {
	struct amdgpu_bo *bo;
	u64 native_gpu_addr;
	u64 private_va;
	u64 bytes;
};

/* Zero-initialize once. Never clear/reinitialize after entered becomes true.
 * Caller owns serialization, the device/reset lifetime and VMID4 reservation.
 */
struct amdgpu_ps4_uvd_memory {
	struct amdgpu_device *owner;
	struct amdgpu_ps4_uvd_memory_bo pd;
	struct amdgpu_ps4_uvd_memory_bo vram_pt;
	struct amdgpu_ps4_uvd_memory_bo private_pt;
	struct amdgpu_bo *gart_bo;
	void *gart_cpu_addr;
	u64 gart_gpu_addr;
	u64 gart_table_bytes;
	u64 gart_pde_flags;
	u64 gart_start;
	u64 gart_bytes;
	u64 vram_start;
	u64 vram_bytes;
	u64 vram_pa;
	u64 max_pfn;
	u64 root_entries;
	u64 gart_first_pde;
	u64 gart_pdes;
	u64 vram_first_pde;
	u64 vram_pdes;
	u64 private_pde;
	u64 vram_pte_flags;
	u64 *private_ptes;
	unsigned long *private_pfns;
	struct amdgpu_ps4_uvd_memory_mapping maps[AMDGPU_PS4_UVD_PRIVATE_MAPS];
	u32 map_count;
	u32 context1_control;
	u32 vmid4_start;
	u32 vmid4_end;
	u32 original_root;
	bool gart_pinned;
	bool vmid4_reserved;
	bool prepared;
	bool entered;
	bool installed;
};

/* Preparation/map/cleanup may sleep; caller must not hold a BO reservation.
 * vmid4_reserved is the parent allocator's explicit ownership assertion; this
 * component also requires VMID4's normal LRU node to remain empty.
 */
int amdgpu_ps4_uvd_memory_prepare(struct amdgpu_device *adev,
				struct amdgpu_ps4_uvd_memory *memory,
				bool vmid4_reserved);
int amdgpu_ps4_uvd_memory_map_private_gtt(struct amdgpu_device *adev,
				struct amdgpu_ps4_uvd_memory *memory,
				struct amdgpu_bo *bo, u64 native_gpu_addr,
				u64 bytes, u64 private_va);

/* Install once while no UVD requester can run. entered is set before the
 * first root MMIO write, including when the following readback/flush fails.
 */
int amdgpu_ps4_uvd_memory_install(struct amdgpu_device *adev,
				struct amdgpu_ps4_uvd_memory *memory);

/* Shared GART leaves need no copy/rewrite. Call after final BO validation and
 * dependency waits, before UVD publication, with the prior UVD job complete.
 * Caller serializes against reset/GART teardown and other VM invalidators.
 */
int amdgpu_ps4_uvd_memory_refresh(struct amdgpu_device *adev,
				struct amdgpu_ps4_uvd_memory *memory);

/* Returns -EBUSY after entered, without changing any owned reference/pin.
 * No post-entry destructor, original-root restoration, rearm or DMA-stop API.
 */
int amdgpu_ps4_uvd_memory_cleanup_pre_entry(struct amdgpu_device *adev,
				struct amdgpu_ps4_uvd_memory *memory);

#endif
