/* SPDX-License-Identifier: MIT */
#ifndef AMDGPU_PS4_UVD_MESSAGE_CREATE_H
#define AMDGPU_PS4_UVD_MESSAGE_CREATE_H

#include "amdgpu_ps4_uvd_irq.h"
#include "ps4_uvd_message_job.h"

#define PS4_CREATE_RECORDS 256U

enum ps4_create_terminal {
	PS4_CREATE_NONE,
	PS4_CREATE_NOT_AUTHORIZED,
	PS4_CREATE_INPUTS,
	PS4_CREATE_PLAN,
	PS4_CREATE_HISTORY_BASELINE,
	PS4_CREATE_HELD_GUARD,
	PS4_CREATE_STALE,
	PS4_CREATE_BASELINE,
	PS4_CREATE_MEMORY,
	PS4_CREATE_ENABLE_TIMEOUT,
	PS4_CREATE_ENABLE_GUARD,
	PS4_CREATE_PREPUBLICATION_TIMEOUT,
	PS4_CREATE_PREPUBLICATION_GUARD,
	PS4_CREATE_HISTORY_CHANGED,
	PS4_CREATE_SUBMITTED_GUARD,
	PS4_CREATE_FENCE_UNEXPECTED,
	PS4_CREATE_CONTEXT_UNEXPECTED,
	PS4_CREATE_POINTER_REGRESSION,
	PS4_CREATE_FENCE_REGRESSION,
	PS4_CREATE_CONTEXT_REGRESSION,
	PS4_CREATE_TIMEOUT,
	PS4_CREATE_SAMPLE_LIMIT,
	PS4_CREATE_FINAL_GUARD,
	PS4_CREATE_FINAL_VALUES,
	PS4_CREATE_FINAL_HISTORY,
	PS4_CREATE_CTX_SWEEP,
	PS4_CREATE_FENCE_OBSERVED,
};

struct ps4_create_result {
	enum ps4_create_terminal terminal;
	int error;
};

struct ps4_create_record {
	u64 begin_ns, end_ns, fault_arrivals;
	u32 status, rptr, wptr, ib_size, context, fence;
	bool core_valid, history_valid, predeadline, joint;
};

struct ps4_create_saved_core {
	u32 status, mastint, rbc, ib_size, wptr, rptr, context;
	u32 wptr_cntl, rptr_addr, fault, reset_count, in_reset, pd;
	u32 bad_kind, bad_reg, bad_value, bad_mask, bad_expected;
	bool sampled, failed;
};

/*
 * Embedded in zeroed adev, exclusively accessed by the boot wrapper.
 * No runtime API, rearm, clear, teardown or new ownership is introduced.
 * Descriptors are borrowed from the existing retained canary owner.
 * Entry flags precede operations; they never certify DMA completion/quiescence.
 * The full >4KiB plan and observation log stay off the kernel stack.
 */
struct amdgpu_ps4_uvd_create_operation {
	bool entered, completed, memory_write_entered, memory_initialized, enable_entered, submitted;
	bool memory_scanned, baseline_history_collected, latest_history_collected;
	bool fence_seen, context_seen, endpoint_valid, sweep_attempted, sweep_valid;
	bool initial_sweep_attempted, initial_sweep_valid;
	u32 publication_count, records, valid_samples, joint_samples, consecutive, history_snapshots;
	u32 last_rptr, fence, history_reason, first_history_reason, baseline_readback;
	u32 memory_bad_region, memory_bad_offset, memory_bad_expected, memory_bad_value;
	u32 message_changed_words, message_first_offset, message_first_before, message_first_after;
	u32 ring_crc, ib_crc, message_before_crc, message_after_crc;
	u64 started_ns, publish_before_ns, publish_after_ns, confirmed_ns, end_ns;
	void *ring_cpu, *backing_cpu;
	u64 ring_va, backing_va, pd_addr;
	struct ps4_uvd_message_job_plan plan;
	struct ps4_uvd_fault_snapshot fault_baseline, fault_deciding, fault_latest;
	struct ps4_create_saved_core core, sweep;
	struct ps4_create_record log[PS4_CREATE_RECORDS];
	struct ps4_create_result result;
};

#endif
