/* SPDX-License-Identifier: MIT */
#ifndef PS4_UVD_DECODE_MESSAGE_H
#define PS4_UVD_DECODE_MESSAGE_H

#include <linux/types.h>
#include "ps4_uvd_message.h"

/* Fixed h264-cb-idr-64x64-v1 reference, not a general AVC interface. */
#define PS4_UVD_DEC_WIDTH 64U
#define PS4_UVD_DEC_HEIGHT 64U
#define PS4_UVD_DEC_FIXTURE_BYTES 2905U
#define PS4_UVD_DEC_IDR_OFFSET 591U
#define PS4_UVD_DEC_IDR_BYTES 2314U
#define PS4_UVD_DEC_BSD_BYTES 2432U
#define PS4_UVD_DEC_SHA256_BYTES 32U
#define PS4_UVD_DEC_PAGE_BYTES 4096U
#define PS4_UVD_DEC_MAIN_DW 16U
#define PS4_UVD_DEC_IB_DW 32U
#define PS4_UVD_DEC_FEEDBACK_BYTES 2048U
#define PS4_UVD_DEC_PITCH 256U
#define PS4_UVD_DEC_UV_OFFSET 0x4000U
#define PS4_UVD_DEC_TARGET_BYTES 0x6000U
#define PS4_UVD_DEC_DPB_BYTES 157184U
#define PS4_UVD_DEC_DPB_MAPPED_BYTES 0x27000U
#define PS4_UVD_DEC_WORK_MIN_BYTES 0x33000U
#define PS4_UVD_DEC_WORK_RECOMMENDED_BYTES 0x40000U
#define PS4_UVD_DEC_SEGMENT_BYTES 0x10000000ULL

#define PS4_UVD_DEC_IB_OFFSET 0x80U
#define PS4_UVD_DEC_CREATE_OFFSET 0x1000U
#define PS4_UVD_DEC_FENCE_OFFSET 0x2000U
#define PS4_UVD_DEC_MESSAGE_OFFSET 0x3000U
#define PS4_UVD_DEC_FEEDBACK_OFFSET 0x4000U
#define PS4_UVD_DEC_BITSTREAM_OFFSET 0x5000U
#define PS4_UVD_DEC_TARGET_OFFSET 0x6000U
#define PS4_UVD_DEC_DPB_OFFSET 0xc000U
#define PS4_UVD_DEC_FENCE_CELL_OFFSET 0x80U
#define PS4_UVD_DEC_FENCE_GUARD 0xa5deadc3U

extern const u8 ps4_uvd_decode_fixture_sha256[PS4_UVD_DEC_SHA256_BYTES];
extern const u8 ps4_uvd_decode_expected_nv12_sha256[PS4_UVD_DEC_SHA256_BYTES];

struct ps4_uvd_decode_input {
	u64 vcpu_segment_base, main_va, work_va;
	size_t main_bytes, work_bytes;
	u32 handle, fence_initial, fence_sequence;
	const u8 *fixture;
	size_t fixture_bytes;
	/* Caller MUST hash these exact fixture bytes before entry. The helper
	 * compares this reported digest, but contains no hash implementation.
	 */
	u8 verified_fixture_sha256[PS4_UVD_DEC_SHA256_BYTES];
};

struct ps4_uvd_decode_layout {
	u64 vcpu_segment_base, main_va, work_va, work_end;
	u64 ib_va, create_va, fence_page_va, fence_target_va;
	u64 message_va, feedback_va, bitstream_va, target_va, dpb_va;
	u32 main_bytes, work_bytes, main_dw, ib_dw;
	u32 feedback_bytes, bitstream_bytes, bitstream_payload_bytes;
	u32 target_bytes, pitch, uv_offset, dpb_bytes, dpb_mapped_bytes;
	u32 handle, fence_initial, fence_sequence;
};

/* Caller-owned CPU output. Allocate on the heap, not a small kernel stack.
 * Only the explicitly present arrays are initialized. Copying into mapped
 * BOs, guards outside those arrays, CREATE submission and all lifetime,
 * ownership/PTE, synchronization, polling and hardware policy are external.
 */
struct ps4_uvd_decode_plan {
	struct ps4_uvd_decode_layout layout;
	struct ps4_uvd_avc_create create_params;
	u8 main_le[PS4_UVD_DEC_MAIN_DW * 4];
	u8 ib_le[PS4_UVD_DEC_IB_DW * 4];
	u8 message_le[PS4_UVD_DEC_PAGE_BYTES];
	u8 feedback_le[PS4_UVD_DEC_FEEDBACK_BYTES];
	u8 bitstream_le[PS4_UVD_DEC_PAGE_BYTES];
	u8 fence_page_le[PS4_UVD_DEC_PAGE_BYTES];
};

/* Writes only the fixed-reference DECODE message, including a zero page tail.
 * Exactly 4096 writable bytes and a nonzero handle are required. The byte
 * buffer may be unaligned. Rejection leaves it unchanged.
 */
int ps4_uvd_decode_msg_reference(u8 *page, size_t bytes, u32 handle);

/* Pure CPU planner: no allocation, mapped-BO write, firmware, MMIO or I/O.
 * Requires an aligned, writable output object of exactly sizeof(*out), a
 * readable input, and readable fixture_bytes of the SHA-256-verified fixture.
 * Output may not overlap input or fixture storage; those CPU ranges and all
 * GPU address arithmetic are checked before output modification. Main is one
 * aligned page; work is page-aligned, page-sized, >= WORK_MIN_BYTES; the two
 * complete intervals must be disjoint inside the asserted VCPU segment.
 * The fence store fits 40 address bits and initial != sequence.
 * Checks do not establish actual mappings, ownership or fixture SHA validity.
 * Prefix/size checks supplement the mandatory caller-computed SHA precondition.
 * Returns -EINVAL, -ERANGE or -EBADMSG, leaving the entire output unchanged.
 *
 * ib_le belongs at work+0x80, leaving work+0 for a distinct CREATE IB.
 * main_le launches only this DECODE IB and appends the public polled fence
 * prefix plus NOP, omitting its six-DWORD trap suffix. CREATE must have
 * completed separately under the same handle using create_params and the
 * existing ps4_uvd_msg_create_avc serializer before publishing this plan.
 */
int ps4_uvd_decode_reference_build(struct ps4_uvd_decode_plan *out,
				 size_t out_bytes,
				 const struct ps4_uvd_decode_input *input);

#endif
