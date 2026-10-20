/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Original Linux psX host-testable planning code, 2026.
 * Public Linux UVD5 ring-test/NO_OP comparison and credits: README.md.
 * This header is not connected to a driver. All Liverpool semantics remain
 * hypotheses until the adapter's hardware contract is independently reviewed.
 */
#ifndef PS4_UVD_RING_PLAN_H
#define PS4_UVD_RING_PLAN_H

#ifdef __KERNEL__
#include <linux/types.h>
typedef u8 ps4_uvd_u8;
typedef u32 ps4_uvd_u32;
typedef u64 ps4_uvd_u64;
typedef unsigned long ps4_uvd_uptr;
#else
#include <stddef.h>
#include <stdint.h>
typedef uint8_t ps4_uvd_u8;
typedef uint32_t ps4_uvd_u32;
typedef uint64_t ps4_uvd_u64;
typedef uintptr_t ps4_uvd_uptr;
#endif

#define PS4_UVD_RING_BYTES 4096U
#define PS4_UVD_RING_DWORDS 1024U
#define PS4_UVD_SUBMIT_DWORDS 16U
#define PS4_UVD_SUBMIT_BYTES 64U
#define PS4_UVD_CONTEXT_REGISTER 0x3dbdU
#define PS4_UVD_NOP_REGISTER 0x3bffU

enum ps4_uvd_plan_error {
	PS4_UVD_PLAN_OK = 0,
	PS4_UVD_PLAN_NULL,
	PS4_UVD_PLAN_SIZE,
	PS4_UVD_PLAN_ALIGNMENT,
	PS4_UVD_PLAN_RANGE,
	PS4_UVD_PLAN_OVERLAP,
	PS4_UVD_PLAN_INITIAL_POINTER,
	PS4_UVD_PLAN_TOKEN
};

struct ps4_uvd_ring_request {
	/* GPU address only; this is not a CPU virtual or necessarily DMA address. */
	ps4_uvd_u64 gpu_base;
	ps4_uvd_u32 baseline;
	ps4_uvd_u32 token;
	/* Real register read before the caller writes the baseline. */
	ps4_uvd_u32 context_before_baseline;
	ps4_uvd_u32 initial_rptr_raw;
	ps4_uvd_u32 initial_wptr_raw;
};

struct ps4_uvd_ring_plan {
	struct ps4_uvd_ring_request request;
	ps4_uvd_u64 gpu_end_exclusive;
	ps4_uvd_u32 ring_bytes;
	ps4_uvd_u32 submitted_dwords;
	/* Raw values, not byte offsets. No masking, shifting or normalization. */
	ps4_uvd_u32 expected_rptr_raw;
	ps4_uvd_u32 publish_wptr_raw;
};

/*
 * Request values are all in host CPU order. Tokens must be fresh per attempt;
 * the caller must also rule out reuse in earlier attempts and other writers.
 * This stateless helper can reject an immediately stale token, not historical
 * reuse. Zero/all-ones are excluded to make common invalid reads unambiguous.
 */
static inline enum ps4_uvd_plan_error
ps4_uvd_validate_request(struct ps4_uvd_ring_request request)
{
	if (request.gpu_base & (PS4_UVD_RING_BYTES - 1U))
		return PS4_UVD_PLAN_ALIGNMENT;
	if (request.gpu_base > ~(ps4_uvd_u64)0 - PS4_UVD_RING_BYTES)
		return PS4_UVD_PLAN_RANGE;
	if (request.initial_rptr_raw || request.initial_wptr_raw)
		return PS4_UVD_PLAN_INITIAL_POINTER;
	if (!request.baseline || request.baseline == ~(ps4_uvd_u32)0 ||
	    !request.token || request.token == ~(ps4_uvd_u32)0 ||
	    request.token == request.baseline ||
	    request.token == request.context_before_baseline)
		return PS4_UVD_PLAN_TOKEN;
	return PS4_UVD_PLAN_OK;
}

/* Byte stores make the output LE on either CPU endianness. No native casts. */
static inline void ps4_uvd_store_le32(ps4_uvd_u8 *destination,
				     ps4_uvd_u32 value)
{
	destination[0] = (ps4_uvd_u8)value;
	destination[1] = (ps4_uvd_u8)(value >> 8);
	destination[2] = (ps4_uvd_u8)(value >> 16);
	destination[3] = (ps4_uvd_u8)(value >> 24);
}

/*
 * Produce exactly one owned 4KiB image; never publish or touch MMIO here.
 * CPU storage: real, writable, 4-byte-aligned, exactly ring_bytes=4096 bytes.
 * The plan must be naturally aligned and disjoint from the ring. The request
 * is passed by value, so it may originate from the caller's ring storage.
 * Caller exclusion is required: no DMA/reader may access it while rebuilding.
 * Validation completes before either destination changes. On any error both
 * destinations remain untouched. No allocation, mutable static state or I/O.
 */
static inline enum ps4_uvd_plan_error
ps4_uvd_build_ring(void *storage, size_t ring_bytes,
		   struct ps4_uvd_ring_request request,
		   struct ps4_uvd_ring_plan *plan)
{
	ps4_uvd_uptr ring_address = (ps4_uvd_uptr)storage;
	ps4_uvd_uptr plan_address = (ps4_uvd_uptr)plan;
	ps4_uvd_uptr pointer_max = ~(ps4_uvd_uptr)0;
	ps4_uvd_u8 *bytes = (ps4_uvd_u8 *)storage;
	enum ps4_uvd_plan_error error;
	size_t offset;

	if (!storage || !plan)
		return PS4_UVD_PLAN_NULL;
	if (ring_bytes != PS4_UVD_RING_BYTES)
		return PS4_UVD_PLAN_SIZE;
	if ((ring_address & 3U) ||
	    plan_address % _Alignof(struct ps4_uvd_ring_plan))
		return PS4_UVD_PLAN_ALIGNMENT;
	if (ring_address > pointer_max - (PS4_UVD_RING_BYTES - 1U) ||
	    plan_address > pointer_max - (sizeof(*plan) - 1U))
		return PS4_UVD_PLAN_RANGE;
	if (ring_address <= plan_address + sizeof(*plan) - 1U &&
	    plan_address <= ring_address + PS4_UVD_RING_BYTES - 1U)
		return PS4_UVD_PLAN_OVERLAP;
	error = ps4_uvd_validate_request(request);
	if (error != PS4_UVD_PLAN_OK)
		return error;

	/* PACKET0(reg, 0) consists of the register index and one data DWORD. */
	for (offset = 0; offset < PS4_UVD_RING_BYTES; offset += 8U) {
		ps4_uvd_store_le32(bytes + offset, PS4_UVD_NOP_REGISTER);
		ps4_uvd_store_le32(bytes + offset + 4U, 0);
	}
	ps4_uvd_store_le32(bytes, PS4_UVD_CONTEXT_REGISTER);
	ps4_uvd_store_le32(bytes + 4U, request.token);

	plan->request = request;
	plan->gpu_end_exclusive = request.gpu_base + PS4_UVD_RING_BYTES;
	plan->ring_bytes = PS4_UVD_RING_BYTES;
	plan->submitted_dwords = PS4_UVD_SUBMIT_DWORDS;
	plan->expected_rptr_raw = PS4_UVD_SUBMIT_DWORDS;
	plan->publish_wptr_raw = PS4_UVD_SUBMIT_DWORDS;
	return PS4_UVD_PLAN_OK;
}

struct ps4_uvd_ring_observation {
	/* Ordered reads: after the baseline store, then immediately pre-publish. */
	ps4_uvd_u32 baseline_readback;
	ps4_uvd_u32 context_before_publish;
	/* Final/polling sample, already converted from MMIO to CPU order. */
	ps4_uvd_u32 context;
	ps4_uvd_u32 rptr_raw;
	ps4_uvd_u32 wptr_raw;
	/* Exactly 0 or 1, set by the caller's finite monotonic deadline. */
	ps4_uvd_u32 deadline_expired;
};

enum ps4_uvd_observation_class {
	PS4_UVD_OBSERVATION_INVALID = 0,
	PS4_UVD_BASELINE_UNCONFIRMED,
	PS4_UVD_WRITE_POINTER_MISMATCH,
	PS4_UVD_READ_POINTER_OUT_OF_RANGE,
	PS4_UVD_DEADLINE_EXPIRED,
	PS4_UVD_CONTEXT_UNEXPECTED,
	PS4_UVD_INCOMPLETE,
	PS4_UVD_DRAINED_WITHOUT_TOKEN,
	PS4_UVD_EXPECTED_OBSERVATION
};

/*
 * Classifies supplied evidence only. EXPECTED_OBSERVATION is conditional on
 * the caller's provenance/read order, exclusion, mapping and DWORD-pointer
 * hypothesis; it is not hardware acceptance, quiescence or decoder support.
 * The token alone never yields EXPECTED_OBSERVATION. A late sample cannot
 * rescue an expired attempt. There is no pointer mask that could hide faults.
 */
static inline enum ps4_uvd_observation_class
ps4_uvd_classify_observation(const struct ps4_uvd_ring_plan *plan,
			    const struct ps4_uvd_ring_observation *observation)
{
	if (!plan || !observation || observation->deadline_expired > 1U)
		return PS4_UVD_OBSERVATION_INVALID;
	if (ps4_uvd_validate_request(plan->request) != PS4_UVD_PLAN_OK ||
	    plan->gpu_end_exclusive !=
			plan->request.gpu_base + PS4_UVD_RING_BYTES ||
	    plan->ring_bytes != PS4_UVD_RING_BYTES ||
	    plan->submitted_dwords != PS4_UVD_SUBMIT_DWORDS ||
	    plan->expected_rptr_raw != PS4_UVD_SUBMIT_DWORDS ||
	    plan->publish_wptr_raw != PS4_UVD_SUBMIT_DWORDS)
		return PS4_UVD_OBSERVATION_INVALID;
	if (observation->baseline_readback != plan->request.baseline ||
	    observation->context_before_publish != plan->request.baseline)
		return PS4_UVD_BASELINE_UNCONFIRMED;
	if (observation->wptr_raw != plan->publish_wptr_raw)
		return PS4_UVD_WRITE_POINTER_MISMATCH;
	if (observation->rptr_raw > plan->expected_rptr_raw)
		return PS4_UVD_READ_POINTER_OUT_OF_RANGE;
	if (observation->deadline_expired)
		return PS4_UVD_DEADLINE_EXPIRED;
	if (observation->context != plan->request.baseline &&
	    observation->context != plan->request.token)
		return PS4_UVD_CONTEXT_UNEXPECTED;
	if (observation->rptr_raw != plan->expected_rptr_raw)
		return PS4_UVD_INCOMPLETE;
	if (observation->context == plan->request.baseline)
		return PS4_UVD_DRAINED_WITHOUT_TOKEN;
	return PS4_UVD_EXPECTED_OBSERVATION;
}

#endif /* PS4_UVD_RING_PLAN_H */
