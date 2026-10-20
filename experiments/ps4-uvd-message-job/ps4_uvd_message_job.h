/* SPDX-License-Identifier: MIT */
#ifndef PS4_UVD_MESSAGE_JOB_H
#define PS4_UVD_MESSAGE_JOB_H

#include <linux/types.h>
#include "ps4_uvd_message.h"

#define PS4_UVD_JOB_MAIN_BYTES 4096U
#define PS4_UVD_JOB_BACKING_BYTES 0x20000U
#define PS4_UVD_JOB_IB_OFFSET 0U
#define PS4_UVD_JOB_CREATE_OFFSET 0x1000U
#define PS4_UVD_JOB_FENCE_OFFSET 0x2000U
#define PS4_UVD_JOB_FENCE_TARGET_OFFSET 0x40U
#define PS4_UVD_JOB_MAIN_DW 16U
#define PS4_UVD_JOB_IB_DW 16U
#define PS4_UVD_JOB_SEGMENT_BYTES 0x10000000ULL

struct ps4_uvd_message_job_input {
	/* An asserted VCPU segment base, not a sampled mapping or VCPU address. */
	u64 vcpu_segment_base;
	u64 main_va;
	u64 backing_va;
	size_t main_bytes;
	size_t backing_bytes;
	u32 handle;
	u32 sequence;
};

struct ps4_uvd_message_job_layout {
	u64 vcpu_segment_base, main_va, backing_va;
	u64 ib_va, create_va, fence_page_va, fence_target_va;
	u32 main_page_bytes, backing_bytes, main_dw, ib_dw;
	u32 create_page_bytes, create_declared_bytes, fence_page_bytes;
	u32 fence_target_offset, fence_store_bytes, sequence;
};

/*
 * Caller-owned CPU output, deliberately separate from the actual mapped BOs.
 * Only packet bytes are planned for the main/IB pages; unused page bytes and
 * the fence page's initial/guard contents are the caller's later obligation.
 * Allocate this >4KiB object outside a small kernel stack if later integrated.
 */
struct ps4_uvd_message_job_plan {
	struct ps4_uvd_message_job_layout layout;
	u8 main_le[PS4_UVD_JOB_MAIN_DW * 4];
	u8 ib_le[PS4_UVD_JOB_IB_DW * 4];
	u8 create_le[PS4_UVD_MSG_PAGE_BYTES];
};

/*
 * Pure CPU planner for the exact public Linux dummy CREATE. Main/backing
 * bases must be 4KiB aligned (therefore also
 * 256B aligned); exact byte sizes, whole-range non-overlap and a common
 * 256MiB VCPU segment are required. The fence store must fit 40 address bits.
 * Input may overlap output; it is copied before output changes.
 * Any rejection leaves the entire output object untouched.
 *
 * No address ownership, PTE, firmware acceptance, completion or lifetime
 * validation occurs. Sequence is a literal u32, including zero; the later
 * caller must choose and establish a distinct initial polled value.
 */
int ps4_uvd_message_job_build(struct ps4_uvd_message_job_plan *out,
			     const struct ps4_uvd_message_job_input *input);

#endif
