/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Original Linux psX packet construction code, 2026.
 * Validation/LE-store structure adapted from ps4-uvd-ring/ps4_uvd_ring_plan.h
 * under the same GPL-2.0-only license. Public Linux comparison: README.md.
 * This constructs bytes only. It does not access or emulate UVD hardware.
 */
#ifndef PS4_UVD_IB_DATA_PLAN_H
#define PS4_UVD_IB_DATA_PLAN_H

#ifdef __KERNEL__
#include <linux/types.h>
typedef u8 ps4_uvd_ib_data_u8;
typedef u32 ps4_uvd_ib_data_u32;
typedef u64 ps4_uvd_ib_data_u64;
typedef unsigned long ps4_uvd_ib_data_uptr;
#else
#include <stddef.h>
#include <stdint.h>
typedef uint8_t ps4_uvd_ib_data_u8;
typedef uint32_t ps4_uvd_ib_data_u32;
typedef uint64_t ps4_uvd_ib_data_u64;
typedef uintptr_t ps4_uvd_ib_data_uptr;
#endif

#define PS4_UVD_IB_DATA_PAGE_BYTES 4096U
#define PS4_UVD_IB_DATA_SUBMIT_DWORDS 16U
#define PS4_UVD_IB_DATA_IB_LENGTH_DWORDS 16U
#define PS4_UVD_IB_DATA_IB_LOW_REGISTER 0x3c67U
#define PS4_UVD_IB_DATA_IB_HIGH_REGISTER 0x3c66U
#define PS4_UVD_IB_DATA_IB_SIZE_REGISTER 0x3da2U
#define PS4_UVD_IB_DATA_DATA0_REGISTER 0x3bc4U
#define PS4_UVD_IB_DATA_NOP_REGISTER 0x3bffU

enum ps4_uvd_ib_data_error {
	PS4_UVD_IB_DATA_OK = 0,
	PS4_UVD_IB_DATA_NULL,
	PS4_UVD_IB_DATA_SIZE,
	PS4_UVD_IB_DATA_ALIGNMENT,
	PS4_UVD_IB_DATA_RANGE,
	PS4_UVD_IB_DATA_OVERLAP,
	PS4_UVD_IB_DATA_INITIAL_POINTER,
	PS4_UVD_IB_DATA_TOKEN
};

struct ps4_uvd_ib_data_request {
	/* GPU VAs; neither is inferred from a CPU pointer or DMA address. */
	ps4_uvd_ib_data_u64 ring_gpu_base;
	ps4_uvd_ib_data_u64 ib_gpu_base;
	ps4_uvd_ib_data_u32 baseline;
	ps4_uvd_ib_data_u32 token;
	/* Actual DATA0 read before the caller plants the baseline. */
	ps4_uvd_ib_data_u32 data_before_baseline;
	ps4_uvd_ib_data_u32 initial_rptr_raw;
	ps4_uvd_ib_data_u32 initial_wptr_raw;
};

struct ps4_uvd_ib_data_plan {
	struct ps4_uvd_ib_data_request request;
	ps4_uvd_ib_data_u64 ring_gpu_end_exclusive;
	ps4_uvd_ib_data_u64 ib_gpu_end_exclusive;
	ps4_uvd_ib_data_u32 ring_bytes;
	ps4_uvd_ib_data_u32 ib_bytes;
	ps4_uvd_ib_data_u32 submitted_dwords;
	ps4_uvd_ib_data_u32 ib_length_dw_raw;
	/* These are raw DWORD counts, not byte offsets or masked pointers. */
	ps4_uvd_ib_data_u32 expected_rptr_raw;
	ps4_uvd_ib_data_u32 publish_wptr_raw;
};

static inline enum ps4_uvd_ib_data_error
ps4_uvd_ib_data_validate_request(struct ps4_uvd_ib_data_request request)
{
	ps4_uvd_ib_data_u64 maximum = ~(ps4_uvd_ib_data_u64)0;
	ps4_uvd_ib_data_u64 ring_end, ib_end;

	if ((request.ring_gpu_base | request.ib_gpu_base) &
	    (PS4_UVD_IB_DATA_PAGE_BYTES - 1U))
		return PS4_UVD_IB_DATA_ALIGNMENT;
	if (request.ring_gpu_base > maximum - PS4_UVD_IB_DATA_PAGE_BYTES ||
	    request.ib_gpu_base > maximum - PS4_UVD_IB_DATA_PAGE_BYTES)
		return PS4_UVD_IB_DATA_RANGE;
	ring_end = request.ring_gpu_base + PS4_UVD_IB_DATA_PAGE_BYTES;
	ib_end = request.ib_gpu_base + PS4_UVD_IB_DATA_PAGE_BYTES;
	if (request.ring_gpu_base < ib_end && request.ib_gpu_base < ring_end)
		return PS4_UVD_IB_DATA_OVERLAP;
	if (request.initial_rptr_raw || request.initial_wptr_raw)
		return PS4_UVD_IB_DATA_INITIAL_POINTER;
	if (!request.token || request.token == ~(ps4_uvd_ib_data_u32)0 ||
	    request.token == request.baseline ||
	    request.token == request.data_before_baseline)
		return PS4_UVD_IB_DATA_TOKEN;
	return PS4_UVD_IB_DATA_OK;
}

/* Byte stores explicitly encode LE on either host CPU byte order. */
static inline void
ps4_uvd_ib_data_store_le32(ps4_uvd_ib_data_u8 *destination,
			 ps4_uvd_ib_data_u32 value)
{
	destination[0] = (ps4_uvd_ib_data_u8)value;
	destination[1] = (ps4_uvd_ib_data_u8)(value >> 8);
	destination[2] = (ps4_uvd_ib_data_u8)(value >> 16);
	destination[3] = (ps4_uvd_ib_data_u8)(value >> 24);
}

/* Inclusive bounds are used only after both integer additions are checked. */
static inline int
ps4_uvd_ib_data_cpu_overlap(ps4_uvd_ib_data_uptr first, size_t first_size,
			    ps4_uvd_ib_data_uptr second, size_t second_size)
{
	return first <= second + second_size - 1U &&
	       second <= first + first_size - 1U;
}

/*
 * Each image is a real, writable, 4-byte-aligned 4096-byte CPU object. The
 * two CPU ranges and the naturally aligned plan must be pairwise disjoint.
 * GPU ranges are distinct, 4096-byte-aligned and have representable exclusive
 * ends. Request is passed by value, including when it originates in plan.
 *
 * Every rejection precedes every output write. On failure both full images
 * and the plan remain byte-for-byte unchanged. The caller owns/pins the pages,
 * establishes their mappings, excludes other readers/writers during building,
 * validates baseline readback, and rules out historical token reuse. Numeric
 * ranges cannot prove those properties. No allocation, MMIO, publication,
 * polling, mutable static state, or hardware outcome classification occurs.
 */
static inline enum ps4_uvd_ib_data_error
ps4_uvd_ib_data_build(void *ring_storage, size_t ring_bytes,
		      void *ib_storage, size_t ib_bytes,
		      struct ps4_uvd_ib_data_request request,
		      struct ps4_uvd_ib_data_plan *plan)
{
	ps4_uvd_ib_data_uptr ring_address = (ps4_uvd_ib_data_uptr)ring_storage;
	ps4_uvd_ib_data_uptr ib_address = (ps4_uvd_ib_data_uptr)ib_storage;
	ps4_uvd_ib_data_uptr plan_address = (ps4_uvd_ib_data_uptr)plan;
	ps4_uvd_ib_data_uptr pointer_max = ~(ps4_uvd_ib_data_uptr)0;
	ps4_uvd_ib_data_u8 *ring = (ps4_uvd_ib_data_u8 *)ring_storage;
	ps4_uvd_ib_data_u8 *ib = (ps4_uvd_ib_data_u8 *)ib_storage;
	enum ps4_uvd_ib_data_error error;
	size_t offset;

	if (!ring_storage || !ib_storage || !plan)
		return PS4_UVD_IB_DATA_NULL;
	if (ring_bytes != PS4_UVD_IB_DATA_PAGE_BYTES ||
	    ib_bytes != PS4_UVD_IB_DATA_PAGE_BYTES)
		return PS4_UVD_IB_DATA_SIZE;
	if ((ring_address & 3U) || (ib_address & 3U) ||
	    plan_address % _Alignof(struct ps4_uvd_ib_data_plan))
		return PS4_UVD_IB_DATA_ALIGNMENT;
	if (ring_address > pointer_max - (PS4_UVD_IB_DATA_PAGE_BYTES - 1U) ||
	    ib_address > pointer_max - (PS4_UVD_IB_DATA_PAGE_BYTES - 1U) ||
	    plan_address > pointer_max - (sizeof(*plan) - 1U))
		return PS4_UVD_IB_DATA_RANGE;
	if (ps4_uvd_ib_data_cpu_overlap(ring_address, ring_bytes,
				       ib_address, ib_bytes) ||
	    ps4_uvd_ib_data_cpu_overlap(ring_address, ring_bytes,
				       plan_address, sizeof(*plan)) ||
	    ps4_uvd_ib_data_cpu_overlap(ib_address, ib_bytes,
				       plan_address, sizeof(*plan)))
		return PS4_UVD_IB_DATA_OVERLAP;
	error = ps4_uvd_ib_data_validate_request(request);
	if (error != PS4_UVD_IB_DATA_OK)
		return error;

	/* PACKET0(reg, 0) is a register index followed by one data DWORD. */
	for (offset = 0; offset < PS4_UVD_IB_DATA_PAGE_BYTES; offset += 8U) {
		ps4_uvd_ib_data_store_le32(ring + offset,
					 PS4_UVD_IB_DATA_NOP_REGISTER);
		ps4_uvd_ib_data_store_le32(ring + offset + 4U, 0);
		ps4_uvd_ib_data_store_le32(ib + offset,
					 PS4_UVD_IB_DATA_NOP_REGISTER);
		ps4_uvd_ib_data_store_le32(ib + offset + 4U, 0);
	}
	ps4_uvd_ib_data_store_le32(ring, PS4_UVD_IB_DATA_IB_LOW_REGISTER);
	ps4_uvd_ib_data_store_le32(ring + 4U,
				 (ps4_uvd_ib_data_u32)request.ib_gpu_base);
	ps4_uvd_ib_data_store_le32(ring + 8U, PS4_UVD_IB_DATA_IB_HIGH_REGISTER);
	ps4_uvd_ib_data_store_le32(ring + 12U,
				 (ps4_uvd_ib_data_u32)(request.ib_gpu_base >> 32));
	ps4_uvd_ib_data_store_le32(ring + 16U, PS4_UVD_IB_DATA_IB_SIZE_REGISTER);
	ps4_uvd_ib_data_store_le32(ring + 20U, PS4_UVD_IB_DATA_IB_LENGTH_DWORDS);
	ps4_uvd_ib_data_store_le32(ib, PS4_UVD_IB_DATA_DATA0_REGISTER);
	ps4_uvd_ib_data_store_le32(ib + 4U, request.token);

	plan->request = request;
	plan->ring_gpu_end_exclusive = request.ring_gpu_base + ring_bytes;
	plan->ib_gpu_end_exclusive = request.ib_gpu_base + ib_bytes;
	plan->ring_bytes = PS4_UVD_IB_DATA_PAGE_BYTES;
	plan->ib_bytes = PS4_UVD_IB_DATA_PAGE_BYTES;
	plan->submitted_dwords = PS4_UVD_IB_DATA_SUBMIT_DWORDS;
	plan->ib_length_dw_raw = PS4_UVD_IB_DATA_IB_LENGTH_DWORDS;
	plan->expected_rptr_raw = PS4_UVD_IB_DATA_SUBMIT_DWORDS;
	plan->publish_wptr_raw = PS4_UVD_IB_DATA_SUBMIT_DWORDS;
	return PS4_UVD_IB_DATA_OK;
}

#endif /* PS4_UVD_IB_DATA_PLAN_H */
