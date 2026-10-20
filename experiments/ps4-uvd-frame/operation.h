/* SPDX-License-Identifier: MIT */
#ifndef AMDGPU_PS4_UVD_FRAME_H
#define AMDGPU_PS4_UVD_FRAME_H

#include "amdgpu_ps4_uvd_message_create.h"
#include "ps4_uvd_decode_message.h"

#define PS4_FRAME_PHASES 3U
#define PS4_FRAME_VISIBLE_BYTES 6144U
#define PS4_FRAME_STORAGE_BYTES 0x40000U
#define PS4_FRAME_DESTROY_OFFSET 0x33000U
#define PS4_FRAME_DESTROY_IB_OFFSET 0x100U

struct ps4_frame_result {
	const char *name;
	int error;
};

struct ps4_frame_phase {
	u32 sequence, initial, cell, end_dw, previous_context;
	u32 polls, joint, consecutive, last_rptr, fence;
	u64 start_ns, publish_ns, confirmed_ns, end_ns;
	bool published, completed, fence_seen, context_seen;
	struct ps4_create_record last[2];
};

/* One boot, one handle and one picture. Owned by the existing retained
 * canary, never freed/rearmed by this experiment. Large arrays stay off stack.
 */
struct amdgpu_ps4_uvd_frame_operation {
	bool entered, completed, initialized, enabled, pixels_captured, pixels_match;
	bool baseline_history_collected, latest_history_collected;
	u32 publications, phase, history_snapshots, history_reason, first_history_reason;
	u32 memory_bad_region, memory_bad_offset, memory_bad_value, memory_bad_expected;
	u32 message_crc[3], message_changed[3], feedback_crc, target_crc;
	void *ring_cpu, *backing_cpu;
	u64 ring_va, backing_va, pd_addr;
	u8 main_le[192], create_ib_le[64], destroy_ib_le[64];
	u8 create_le[4096], destroy_le[4096];
	u8 pitched[PS4_UVD_DEC_TARGET_BYTES];
	u8 visible[PS4_FRAME_VISIBLE_BYTES], visible_sha256[32], feedback[2048];
	struct ps4_uvd_decode_plan plan;
	struct ps4_frame_phase phases[PS4_FRAME_PHASES];
	struct ps4_uvd_fault_snapshot fault_baseline, fault_latest, fault_deciding;
	struct ps4_create_saved_core core, sweep;
	struct ps4_frame_result result;
};

#endif
