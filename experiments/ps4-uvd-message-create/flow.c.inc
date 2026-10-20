/* MESSAGE_CREATE_FLOW_BEGIN */
/*
 * Original Linux psX boot-only CREATE/fence controller, MIT, 2026.
 * Packet and message source adaptations retain their own complete notices.
 * A returned result leaves firmware, mappings and the engine retained.
 * Two observations qualify only this sequence, not message parsing or decode.
 */
#define PS4_CREATE_SEQUENCE 0x55564343U
#define PS4_CREATE_CONTEXT_BASELINE 0x43435443U
#define PS4_CREATE_FENCE_BASELINE 0x43465243U
#define PS4_CREATE_GUARD 0xc3e7a16dU
#define PS4_CREATE_LIMIT_NS (250ULL * NSEC_PER_MSEC)
#define PS4_CREATE_SLEEP_LOW 1000U
#define PS4_CREATE_SLEEP_HIGH 2000U

static const char *ps4_create_name(enum ps4_create_terminal terminal)
{
	switch (terminal) {
#define CREATE_NAME(s) case PS4_CREATE_##s: return "CREATE_" #s
	CREATE_NAME(NONE);
	CREATE_NAME(NOT_AUTHORIZED);
	CREATE_NAME(INPUTS);
	CREATE_NAME(PLAN);
	CREATE_NAME(HISTORY_BASELINE);
	CREATE_NAME(HELD_GUARD);
	CREATE_NAME(STALE);
	CREATE_NAME(BASELINE);
	CREATE_NAME(MEMORY);
	CREATE_NAME(ENABLE_TIMEOUT);
	CREATE_NAME(ENABLE_GUARD);
	CREATE_NAME(PREPUBLICATION_TIMEOUT);
	CREATE_NAME(PREPUBLICATION_GUARD);
	CREATE_NAME(HISTORY_CHANGED);
	CREATE_NAME(SUBMITTED_GUARD);
	CREATE_NAME(FENCE_UNEXPECTED);
	CREATE_NAME(CONTEXT_UNEXPECTED);
	CREATE_NAME(POINTER_REGRESSION);
	CREATE_NAME(FENCE_REGRESSION);
	CREATE_NAME(CONTEXT_REGRESSION);
	CREATE_NAME(TIMEOUT);
	CREATE_NAME(SAMPLE_LIMIT);
	CREATE_NAME(FINAL_GUARD);
	CREATE_NAME(FINAL_VALUES);
	CREATE_NAME(FINAL_HISTORY);
	CREATE_NAME(CTX_SWEEP);
	CREATE_NAME(FENCE_OBSERVED);
#undef CREATE_NAME
	}
	return "CREATE_INVALID";
}

static bool ps4_create_descriptors_valid(const struct amdgpu_ps4_uvd_ring *rb,
					const struct amdgpu_ps4_uvd_ring *backing,
					u64 pd_addr)
{
	unsigned long ring_start, backing_start;

	if (!ps4_token_descriptor_valid(rb) || !backing || !backing->cpu ||
	    ((unsigned long)backing->cpu & 3U) ||
	    backing->bytes != PS4_UVD_JOB_BACKING_BYTES ||
	    (unsigned long)backing->cpu > ~0UL - (backing->bytes - 1) ||
	    backing->private_va != PS4_READY_IB_VA || !pd_addr ||
	    (pd_addr & 0xfffU) || (pd_addr >> 12) > U32_MAX)
		return false;
	ring_start = (unsigned long)rb->cpu;
	backing_start = (unsigned long)backing->cpu;
	return ring_start > backing_start + backing->bytes - 1 ||
		backing_start > ring_start + rb->bytes - 1;
}

static void ps4_create_save_core(struct ps4_create_saved_core *out,
				 const struct ps4_token_sample *in)
{
	*out = (struct ps4_create_saved_core) {
		.status = in->status, .mastint = in->mastint, .rbc = in->rbc,
		.ib_size = in->ib_size, .wptr = in->wptr, .rptr = in->rptr,
		.context = in->context, .wptr_cntl = in->wptr_cntl,
		.rptr_addr = in->rptr_addr, .fault = in->fault,
		.reset_count = in->reset_count, .in_reset = in->in_reset,
		.pd = in->pd, .bad_kind = in->bad_kind, .bad_reg = in->bad_reg,
		.bad_value = in->bad_value, .bad_mask = in->bad_mask,
		.bad_expected = in->bad_expected, .sampled = true, .failed = in->failed,
	};
}

/*
 * Captured history is additional evidence, never a claim of fault absence.
 * Baseline must be empty: acknowledged readiness faults are not hidden.
 * A changed arm, inactive recorder, saturation or any captured arrival fails.
 * Both snapshots live in adev; no ~1KiB snapshot on the kernel stack.
 */
static bool ps4_create_history(struct amdgpu_device *adev,
			       struct amdgpu_ps4_uvd_create_operation *op,
			       bool baseline)
{
	struct ps4_uvd_fault_snapshot *s = baseline ?
		&op->fault_baseline : &op->fault_latest;
	unsigned int i;

	amdgpu_ps4_uvd_irq_fault_snapshot(adev, s);
	++op->history_snapshots;
	if (baseline)
		op->baseline_history_collected = true;
	else
		op->latest_history_collected = true;
	op->history_reason = 0;
	if (!s->active || !s->arm_ns)
		op->history_reason = 1;
	else if (!baseline && s->arm_ns != op->fault_baseline.arm_ns)
		op->history_reason = 2;
	else if (s->history.total.saturated ||
		 s->history.unclassified_status.saturated)
		op->history_reason = 3;
	else if (s->history.total.arrivals ||
		 s->history.unclassified_status.arrivals)
		op->history_reason = 4;
	for (i = 0; !op->history_reason && i < PS4_UVD_FAULT_VMIDS; ++i) {
		if (s->history.vmid[i].saturated)
			op->history_reason = 3;
		else if (s->history.vmid[i].arrivals)
			op->history_reason = 4;
	}
	/* A copied baseline is labelled explicitly; no later snapshot is implied. */
	if (baseline)
		op->fault_latest = *s;
	if (op->history_reason && !op->first_history_reason) {
		op->first_history_reason = op->history_reason;
		op->fault_deciding = *s;
	}
	return !op->history_reason;
}

static u32 ps4_create_word(const void *cpu, u32 offset)
{
	/* Owner guarantees DWORD alignment. Volatile CPU sampling, no flush. */
	return le32_to_cpu(READ_ONCE(*(__le32 *)((u8 *)cpu + offset)));
}

static void ps4_create_put(void *cpu, u32 offset, u32 value)
{
	WRITE_ONCE(*(__le32 *)((u8 *)cpu + offset), cpu_to_le32(value));
}

static void ps4_create_initialize(struct amdgpu_ps4_uvd_create_operation *op)
{
	u32 offset;

	/* Preserve the already observed NOP padding for fetchable ring/IB pages.
	 * The remaining owned pages get a non-packet sentinel, never published.
	 */
	for (offset = 0; offset < PS4_UVD_JOB_MAIN_BYTES; offset += 4)
		ps4_create_put(op->ring_cpu, offset, (offset & 4U) ? 0 : 0x3bffU);
	for (offset = 0; offset < PS4_UVD_JOB_BACKING_BYTES; offset += 4)
		ps4_create_put(op->backing_cpu, offset, offset < PS4_UVD_MSG_PAGE_BYTES ?
			((offset & 4U) ? 0 : 0x3bffU) : PS4_CREATE_GUARD);
	memcpy(op->ring_cpu, op->plan.main_le, sizeof(op->plan.main_le));
	memcpy(op->backing_cpu, op->plan.ib_le, sizeof(op->plan.ib_le));
	memcpy((u8 *)op->backing_cpu + PS4_UVD_JOB_CREATE_OFFSET,
	       op->plan.create_le, sizeof(op->plan.create_le));
	ps4_create_put(op->backing_cpu,
		PS4_UVD_JOB_FENCE_OFFSET + PS4_UVD_JOB_FENCE_TARGET_OFFSET,
		PS4_CREATE_FENCE_BASELINE);
	op->ring_crc = crc32_le(~0U, op->ring_cpu, PS4_UVD_JOB_MAIN_BYTES) ^ ~0U;
	op->ib_crc = crc32_le(~0U, op->backing_cpu, PS4_UVD_MSG_PAGE_BYTES) ^ ~0U;
	op->message_before_crc = crc32_le(~0U, op->plan.create_le,
					 PS4_UVD_MSG_PAGE_BYTES) ^ ~0U;
}

static bool ps4_create_memory_bad(struct amdgpu_ps4_uvd_create_operation *op,
				  u32 region, u32 offset, u32 value, u32 expected)
{
	if (value == expected)
		return false;
	if (!op->memory_bad_region) {
		op->memory_bad_region = region;
		op->memory_bad_offset = offset;
		op->memory_bad_value = value;
		op->memory_bad_expected = expected;
	}
	return true;
}

/*
 * Sequential CPU observations of the complete owned ring/backing ranges.
 * Message-page mutations after submission are recorded separately; the ABI
 * does not establish that page as immutable. No claim covers other mappings.
 * Target fence is sampled once per poll/endpoint; scans skip its changing word.
 */
static bool ps4_create_memory(struct amdgpu_ps4_uvd_create_operation *op,
			      bool before_publication)
{
	u32 offset, value, expected;
	bool bad = false;

	for (offset = 0; offset < PS4_UVD_JOB_MAIN_BYTES; offset += 4) {
		expected = offset < sizeof(op->plan.main_le) ?
			get_unaligned_le32(op->plan.main_le + offset) :
			((offset & 4U) ? 0 : 0x3bffU);
		bad |= ps4_create_memory_bad(op, 1, offset,
			ps4_create_word(op->ring_cpu, offset), expected);
	}
	op->message_changed_words = 0;
	for (offset = 0; offset < PS4_UVD_JOB_BACKING_BYTES; offset += 4) {
		if (offset == PS4_UVD_JOB_FENCE_OFFSET + PS4_UVD_JOB_FENCE_TARGET_OFFSET)
			continue;
		value = ps4_create_word(op->backing_cpu, offset);
		if (offset >= PS4_UVD_JOB_CREATE_OFFSET &&
		    offset < PS4_UVD_JOB_CREATE_OFFSET + PS4_UVD_MSG_PAGE_BYTES) {
			expected = get_unaligned_le32(op->plan.create_le +
						      offset - PS4_UVD_JOB_CREATE_OFFSET);
			if (value != expected) {
				if (!op->message_changed_words) {
					op->message_first_offset = offset - PS4_UVD_JOB_CREATE_OFFSET;
					op->message_first_before = expected;
					op->message_first_after = value;
				}
				++op->message_changed_words;
				if (before_publication)
					bad |= ps4_create_memory_bad(op, 3, offset, value, expected);
			}
			continue;
		}
		expected = offset < sizeof(op->plan.ib_le) ?
			get_unaligned_le32(op->plan.ib_le + offset) :
			(offset < PS4_UVD_MSG_PAGE_BYTES ?
			 ((offset & 4U) ? 0 : 0x3bffU) : PS4_CREATE_GUARD);
		bad |= ps4_create_memory_bad(op, 2, offset, value, expected);
	}
	op->message_after_crc = crc32_le(~0U,
		(u8 *)op->backing_cpu + PS4_UVD_JOB_CREATE_OFFSET,
		PS4_UVD_MSG_PAGE_BYTES) ^ ~0U;
	op->memory_scanned = true;
	return !bad;
}

static u32 ps4_create_fence(struct amdgpu_ps4_uvd_create_operation *op)
{
	return ps4_create_word(op->backing_cpu,
		PS4_UVD_JOB_FENCE_OFFSET + PS4_UVD_JOB_FENCE_TARGET_OFFSET);
}

static struct ps4_create_result
ps4_create_finish(struct amdgpu_ps4_uvd_create_operation *op,
		  enum ps4_create_terminal terminal, int error)
{
	op->result = (struct ps4_create_result) { terminal, error };
	if (op->enable_entered && !op->end_ns)
		op->end_ns = ktime_get_ns() - op->started_ns;
	op->completed = true;
	return op->result;
}

/*
 * Private finite controller. Its wrapper has already claimed the global boot
 * latch and readiness has returned the convention observation. Deadline starts
 * before fetch enable. Readiness/firmware I/O and terminal reporting are outside
 * that bound; sequential samples ending at/after the deadline never qualify.
 */
static struct ps4_create_result
ps4_create_run(struct amdgpu_device *adev,
	       const struct amdgpu_ps4_uvd_ring *rb,
	       const struct amdgpu_ps4_uvd_ring *backing,
	       u64 pd_addr, u32 cache_vmid, u32 fault_base, int reset_base)
{
	struct amdgpu_ps4_uvd_create_operation *op = &adev->ps4_uvd_create_operation;
	struct amdgpu_ps4_uvd_ready_operation *ready = &adev->ps4_uvd_ready_operation;
	struct ps4_uvd_message_job_input input;
	struct ps4_token_sample sample;
	enum ps4_create_terminal terminal;
	u64 elapsed, begin;
	bool valid, history_valid, joint;
	int error = -EIO;

	/* Preserve the first operation on denied entry/reentry. */
	if (op->entered || !ready->boot_claimed || !ready->completed ||
	    ready->result.terminal != PS4_READY_CORE_READY_CONVENTION_OBSERVED ||
	    ready->result.error)
		return (struct ps4_create_result) { PS4_CREATE_NOT_AUTHORIZED, -EPERM };
	op->entered = true;
	if (!ps4_uvd_message_create ||
	    !amdgpu_ps4_uvd_message_create_options_valid(adev) ||
	    !ps4_create_descriptors_valid(rb, backing, pd_addr))
		return ps4_create_finish(op, PS4_CREATE_INPUTS, -EINVAL);
	op->ring_cpu = rb->cpu;
	op->backing_cpu = backing->cpu;
	op->ring_va = rb->private_va;
	op->backing_va = backing->private_va;
	op->pd_addr = pd_addr;
	input = (struct ps4_uvd_message_job_input) {
		.vcpu_segment_base = PS4_READY_VCPU_VA,
		.main_va = rb->private_va, .backing_va = backing->private_va,
		.main_bytes = rb->bytes, .backing_bytes = backing->bytes,
		.handle = 1, .sequence = PS4_CREATE_SEQUENCE,
	};
	if (ps4_uvd_message_job_build(&op->plan, &input))
		return ps4_create_finish(op, PS4_CREATE_PLAN, -EINVAL);
	if (!ps4_create_history(adev, op, true))
		return ps4_create_finish(op, PS4_CREATE_HISTORY_BASELINE, -EBUSY);
	valid = ps4_ib_data_core_sample(adev, &sample, PS4_TOKEN_HELD,
		pd_addr, cache_vmid, fault_base, reset_base);
	ps4_create_save_core(&op->core, &sample);
	if (valid) {
		op->initial_sweep_attempted = true;
		op->initial_sweep_valid = ps4_stobs_ctx_sweep(adev, &sample);
		ps4_create_save_core(&op->sweep, &sample);
	}
	if (!valid || !op->initial_sweep_valid) {
		return ps4_create_finish(op, PS4_CREATE_HELD_GUARD, -EIO);
	}
	op->fence = ps4_create_fence(op);
	if (sample.context == PS4_CREATE_SEQUENCE || op->fence == PS4_CREATE_SEQUENCE)
		return ps4_create_finish(op, PS4_CREATE_STALE, -ESTALE);
	op->memory_write_entered = true;
	ps4_create_initialize(op);
	op->memory_initialized = true;
	WREG32(mmUVD_CONTEXT_ID, PS4_CREATE_CONTEXT_BASELINE);
	op->baseline_readback = RREG32(mmUVD_CONTEXT_ID);
	op->fence = ps4_create_fence(op);
	if (op->baseline_readback != PS4_CREATE_CONTEXT_BASELINE ||
	    op->fence != PS4_CREATE_FENCE_BASELINE)
		return ps4_create_finish(op, PS4_CREATE_BASELINE, -EIO);
	mb();
	amdgpu_device_flush_hdp(adev, NULL);
	valid = ps4_ib_data_core_sample(adev, &sample, PS4_TOKEN_HELD,
		pd_addr, cache_vmid, fault_base, reset_base);
	ps4_create_save_core(&op->core, &sample);
	if (!valid || sample.context != PS4_CREATE_CONTEXT_BASELINE)
		return ps4_create_finish(op, PS4_CREATE_HELD_GUARD, -EIO);
	if (!ps4_create_memory(op, true))
		return ps4_create_finish(op, PS4_CREATE_MEMORY, -EIO);
	if (!ps4_create_history(adev, op, false))
		return ps4_create_finish(op, PS4_CREATE_HISTORY_CHANGED, -EIO);
	dev_info(adev->dev,
		"PS4_UVD_CREATE PREPARED ring=%llx ib=%llx message=%llx fence=%llx ring_crc=%08x ib_crc=%08x message_crc=%08x handle=1 sequence=%08x deadline_ns=%llu samples=%u; one CREATE and prefix fence, parsing unproved\n",
		op->ring_va, op->plan.layout.ib_va, op->plan.layout.create_va,
		op->plan.layout.fence_target_va, op->ring_crc, op->ib_crc,
		op->message_before_crc, PS4_CREATE_SEQUENCE,
		(unsigned long long)PS4_CREATE_LIMIT_NS, PS4_CREATE_RECORDS);
	op->started_ns = ktime_get_ns();
	op->enable_entered = true;
	WREG32(mmUVD_RBC_RB_CNTL, PS4_TOKEN_RBC_FETCH);
	valid = ps4_ib_data_core_sample(adev, &sample, PS4_TOKEN_EMPTY,
		pd_addr, cache_vmid, fault_base, reset_base);
	ps4_create_save_core(&op->core, &sample);
	elapsed = ktime_get_ns() - op->started_ns;
	if (!valid || sample.context != PS4_CREATE_CONTEXT_BASELINE)
		return ps4_create_finish(op, PS4_CREATE_ENABLE_GUARD, -EIO);
	if (elapsed >= PS4_CREATE_LIMIT_NS)
		return ps4_create_finish(op, PS4_CREATE_ENABLE_TIMEOUT, -ETIMEDOUT);
	if (!ps4_create_history(adev, op, false))
		return ps4_create_finish(op, PS4_CREATE_HISTORY_CHANGED, -EIO);
	mb();
	if (ktime_get_ns() - op->started_ns >= PS4_CREATE_LIMIT_NS)
		return ps4_create_finish(op, PS4_CREATE_PREPUBLICATION_TIMEOUT, -ETIMEDOUT);
	/* Final direct guard, no indexed store or print before sole publication. */
	valid = ps4_ib_data_core_sample(adev, &sample, PS4_TOKEN_EMPTY,
		pd_addr, cache_vmid, fault_base, reset_base);
	ps4_create_save_core(&op->core, &sample);
	op->fence = ps4_create_fence(op);
	if (!valid || sample.context != PS4_CREATE_CONTEXT_BASELINE ||
	    op->fence != PS4_CREATE_FENCE_BASELINE)
		return ps4_create_finish(op, PS4_CREATE_PREPUBLICATION_GUARD, -EIO);
	if (!ps4_create_history(adev, op, false))
		return ps4_create_finish(op, PS4_CREATE_HISTORY_CHANGED, -EIO);
	op->publish_before_ns = ktime_get_ns() - op->started_ns;
	if (op->publish_before_ns >= PS4_CREATE_LIMIT_NS)
		return ps4_create_finish(op, PS4_CREATE_PREPUBLICATION_TIMEOUT, -ETIMEDOUT);
	op->submitted = true;
	++op->publication_count;
	WREG32(mmUVD_RBC_RB_WPTR, PS4_UVD_JOB_MAIN_DW);
	op->publish_after_ns = ktime_get_ns() - op->started_ns;
	terminal = PS4_CREATE_TIMEOUT;
	error = -ETIMEDOUT;
	for (;;) {
		struct ps4_create_record *record;

		begin = ktime_get_ns() - op->started_ns;
		if (begin >= PS4_CREATE_LIMIT_NS)
			break;
		if (op->records >= PS4_CREATE_RECORDS) {
			terminal = PS4_CREATE_SAMPLE_LIMIT;
			error = -ENOSPC;
			break;
		}
		valid = ps4_ib_data_core_sample(adev, &sample, PS4_TOKEN_SUBMITTED,
			pd_addr, cache_vmid, fault_base, reset_base);
		ps4_create_save_core(&op->core, &sample);
		op->fence = ps4_create_fence(op);
		history_valid = ps4_create_history(adev, op, false);
		elapsed = ktime_get_ns() - op->started_ns;
		joint = op->fence == PS4_CREATE_SEQUENCE &&
			sample.context == PS4_CREATE_SEQUENCE &&
			sample.rptr == PS4_UVD_JOB_MAIN_DW &&
			sample.wptr == PS4_UVD_JOB_MAIN_DW;
		record = &op->log[op->records++];
		*record = (struct ps4_create_record) {
			.begin_ns = begin, .end_ns = elapsed,
			.fault_arrivals = op->fault_latest.history.total.arrivals,
			.status = sample.status, .rptr = sample.rptr, .wptr = sample.wptr,
			.ib_size = sample.ib_size, .context = sample.context,
			.fence = op->fence, .core_valid = valid, .history_valid = history_valid,
			.predeadline = elapsed < PS4_CREATE_LIMIT_NS, .joint = joint,
		};
		/* Even late observations retain explicit faults/regressions. */
		if (!valid) {
			terminal = PS4_CREATE_SUBMITTED_GUARD;
			error = -EIO;
			break;
		}
		if (!history_valid) {
			terminal = PS4_CREATE_HISTORY_CHANGED;
			error = -EIO;
			break;
		}
		if (op->fence != PS4_CREATE_FENCE_BASELINE &&
		    op->fence != PS4_CREATE_SEQUENCE) {
			terminal = PS4_CREATE_FENCE_UNEXPECTED;
			error = -EIO;
			break;
		}
		if (sample.context != PS4_CREATE_CONTEXT_BASELINE &&
		    sample.context != PS4_CREATE_SEQUENCE) {
			terminal = PS4_CREATE_CONTEXT_UNEXPECTED;
			error = -EIO;
			break;
		}
		if (sample.rptr < op->last_rptr) {
			terminal = PS4_CREATE_POINTER_REGRESSION;
			error = -EIO;
			break;
		}
		if (op->fence_seen && op->fence == PS4_CREATE_FENCE_BASELINE) {
			terminal = PS4_CREATE_FENCE_REGRESSION;
			error = -EIO;
			break;
		}
		if (op->context_seen && sample.context == PS4_CREATE_CONTEXT_BASELINE) {
			terminal = PS4_CREATE_CONTEXT_REGRESSION;
			error = -EIO;
			break;
		}
		op->last_rptr = sample.rptr;
		op->fence_seen |= op->fence == PS4_CREATE_SEQUENCE;
		op->context_seen |= sample.context == PS4_CREATE_SEQUENCE;
		if (elapsed >= PS4_CREATE_LIMIT_NS)
			break;
		++op->valid_samples;
		if (joint) {
			++op->joint_samples;
			++op->consecutive;
		} else {
			op->consecutive = 0;
		}
		if (op->consecutive == 2) {
			op->confirmed_ns = elapsed;
			terminal = PS4_CREATE_FENCE_OBSERVED;
			error = 0;
			break;
		}
		usleep_range(PS4_CREATE_SLEEP_LOW, PS4_CREATE_SLEEP_HIGH);
	}
	/*
	 * Endpoint checks are outside the two-observation qualification window.
	 * Preserve the first fault; a clean observation/timeout gets endpoint
	 * direct reads, then at most one indexed sweep only on valid direct reads.
	 * No further GPU/UVD access occurs after this controller returns.
	 */
	if (terminal == PS4_CREATE_FENCE_OBSERVED || terminal == PS4_CREATE_TIMEOUT ||
	    terminal == PS4_CREATE_SAMPLE_LIMIT) {
		op->endpoint_valid = ps4_ib_data_core_sample(adev, &sample,
			PS4_TOKEN_SUBMITTED, pd_addr, cache_vmid, fault_base, reset_base);
		ps4_create_save_core(&op->core, &sample);
		op->fence = ps4_create_fence(op);
		if (!op->endpoint_valid) {
			terminal = PS4_CREATE_FINAL_GUARD;
			error = -EIO;
		} else if (op->fence != PS4_CREATE_FENCE_BASELINE &&
			   op->fence != PS4_CREATE_SEQUENCE) {
			terminal = PS4_CREATE_FENCE_UNEXPECTED;
			error = -EIO;
		} else if (sample.context != PS4_CREATE_CONTEXT_BASELINE &&
			   sample.context != PS4_CREATE_SEQUENCE) {
			terminal = PS4_CREATE_CONTEXT_UNEXPECTED;
			error = -EIO;
		} else if (sample.rptr < op->last_rptr) {
			terminal = PS4_CREATE_POINTER_REGRESSION;
			error = -EIO;
		} else if (op->fence_seen && op->fence == PS4_CREATE_FENCE_BASELINE) {
			terminal = PS4_CREATE_FENCE_REGRESSION;
			error = -EIO;
		} else if (op->context_seen && sample.context == PS4_CREATE_CONTEXT_BASELINE) {
			terminal = PS4_CREATE_CONTEXT_REGRESSION;
			error = -EIO;
		} else {
			op->sweep_attempted = true;
			op->sweep_valid = ps4_stobs_ctx_sweep(adev, &sample);
			ps4_create_save_core(&op->sweep, &sample);
			if (!op->sweep_valid) {
				terminal = PS4_CREATE_CTX_SWEEP;
				error = -EIO;
			}
		}
		if (!error &&
		    (op->fence != PS4_CREATE_SEQUENCE || sample.context != PS4_CREATE_SEQUENCE ||
		     sample.rptr != PS4_UVD_JOB_MAIN_DW || sample.wptr != PS4_UVD_JOB_MAIN_DW)) {
			terminal = PS4_CREATE_FINAL_VALUES;
			error = -EIO;
		}
	}
	/* Guard scan and final software history snapshot also precede reporting. */
	if (!ps4_create_memory(op, false) && (!error || terminal == PS4_CREATE_TIMEOUT ||
					     terminal == PS4_CREATE_SAMPLE_LIMIT)) {
		terminal = PS4_CREATE_MEMORY;
		error = -EIO;
	}
	history_valid = ps4_create_history(adev, op, false);
	if (!history_valid && (!error || terminal == PS4_CREATE_TIMEOUT ||
			       terminal == PS4_CREATE_SAMPLE_LIMIT)) {
		terminal = PS4_CREATE_FINAL_HISTORY;
		error = -EIO;
	}
	op->end_ns = ktime_get_ns() - op->started_ns;
	return ps4_create_finish(op, terminal, error);
}

static void ps4_create_report_deciding_history(struct amdgpu_device *adev,
					       const struct ps4_uvd_fault_snapshot *s)
{
	const struct ps4_uvd_fault_bucket *b = &s->history.total;
	unsigned int i;

	dev_info(adev->dev,
		"PS4_UVD_CREATE FAULT_DECIDING active=%u arm=%llu total=%llu saturated=%u first=%08x/%08x/%08x last=%08x/%08x/%08x\n",
		s->active, (unsigned long long)s->arm_ns,
		(unsigned long long)b->arrivals, b->saturated,
		b->first.addr, b->first.status, b->first.mc_client,
		b->last.addr, b->last.status, b->last.mc_client);
	for (i = 0; i <= PS4_UVD_FAULT_VMIDS; ++i) {
		b = i < PS4_UVD_FAULT_VMIDS ?
			&s->history.vmid[i] : &s->history.unclassified_status;
		if (b->arrivals || b->saturated)
			dev_info(adev->dev,
				"PS4_UVD_CREATE FAULT_DECIDING_BUCKET index=%u unknown_index=16 total=%llu saturated=%u first=%08x/%08x/%08x last=%08x/%08x/%08x\n",
				i, (unsigned long long)b->arrivals, b->saturated,
				b->first.addr, b->first.status, b->first.mc_client,
				b->last.addr, b->last.status, b->last.mc_client);
	}
}

static void ps4_create_report(struct amdgpu_device *adev)
{
	const struct amdgpu_ps4_uvd_create_operation *op = &adev->ps4_uvd_create_operation;
	const struct ps4_create_saved_core *s = &op->core;
	unsigned int i;

	/* Saved CPU data only. No UVD/GPU register access after reporting starts. */
	dev_info(adev->dev,
		"PS4_UVD_CREATE CORE sampled=%u status=%08x int=%08x rbc=%08x ib=%08x rp=%u wp=%u context=%08x fault=%08x reset_count=%u reset=%u pd=%08x\n",
		s->sampled, s->status, s->mastint, s->rbc, s->ib_size, s->rptr, s->wptr,
		s->context, s->fault, s->reset_count, s->in_reset, s->pd);
	if (s->failed)
		dev_err(adev->dev, "PS4_UVD_CREATE GUARD kind=%u reg=%08x value=%08x mask=%08x expected=%08x\n",
			s->bad_kind, s->bad_reg, s->bad_value, s->bad_mask, s->bad_expected);
	if (op->sweep.failed)
		dev_err(adev->dev, "PS4_UVD_CREATE CTX_GUARD initial_attempted=%u endpoint_attempted=%u kind=%u reg=%08x value=%08x mask=%08x expected=%08x\n",
			op->initial_sweep_attempted, op->sweep_attempted, op->sweep.bad_kind, op->sweep.bad_reg,
			op->sweep.bad_value, op->sweep.bad_mask, op->sweep.bad_expected);
	dev_info(adev->dev,
		"PS4_UVD_CREATE MEMORY write_entered=%u initialized=%u scanned=%u bad_region=%u offset=%08x expected=%08x value=%08x message_changed_words=%u first_offset=%08x before=%08x after=%08x message_crc_before=%08x after=%08x; message mutation is raw evidence\n",
		op->memory_write_entered, op->memory_initialized, op->memory_scanned,
		op->memory_bad_region, op->memory_bad_offset, op->memory_bad_expected,
		op->memory_bad_value, op->message_changed_words, op->message_first_offset,
		op->message_first_before, op->message_first_after,
		op->message_before_crc, op->message_after_crc);
	dev_info(adev->dev,
		"PS4_UVD_CREATE HISTORY snapshots=%u baseline_collected=%u latest_collected=%u first_reason=%u latest_reason=%u baseline_active=%u arm=%llu total=%llu latest_active=%u arm=%llu total=%llu saturated=%u unknown=%llu; latest is baseline copy until collected, zero is not fault absence\n",
		op->history_snapshots, op->baseline_history_collected, op->latest_history_collected,
		op->first_history_reason, op->history_reason, op->fault_baseline.active,
		(unsigned long long)op->fault_baseline.arm_ns,
		(unsigned long long)op->fault_baseline.history.total.arrivals,
		op->fault_latest.active, (unsigned long long)op->fault_latest.arm_ns,
		(unsigned long long)op->fault_latest.history.total.arrivals,
		op->fault_latest.history.total.saturated,
		(unsigned long long)op->fault_latest.history.unclassified_status.arrivals);
	if (op->first_history_reason)
		ps4_create_report_deciding_history(adev, &op->fault_deciding);
	if (op->fault_latest.history.total.arrivals ||
	    op->fault_latest.history.total.saturated) {
		const struct ps4_uvd_fault_bucket *b = &op->fault_latest.history.total;

		dev_info(adev->dev,
			"PS4_UVD_CREATE FAULT_TOTAL first=%08x/%08x/%08x last=%08x/%08x/%08x\n",
			b->first.addr, b->first.status, b->first.mc_client,
			b->last.addr, b->last.status, b->last.mc_client);
	}
	if (op->fault_latest.history.unclassified_status.arrivals ||
	    op->fault_latest.history.unclassified_status.saturated) {
		const struct ps4_uvd_fault_bucket *b = &op->fault_latest.history.unclassified_status;

		dev_info(adev->dev,
			"PS4_UVD_CREATE FAULT_UNKNOWN total=%llu saturated=%u first=%08x/%08x/%08x last=%08x/%08x/%08x\n",
			(unsigned long long)b->arrivals, b->saturated,
			b->first.addr, b->first.status, b->first.mc_client,
			b->last.addr, b->last.status, b->last.mc_client);
	}
	for (i = 0; i < PS4_UVD_FAULT_VMIDS; ++i) {
		const struct ps4_uvd_fault_bucket *b = &op->fault_latest.history.vmid[i];

		if (b->arrivals || b->saturated)
			dev_info(adev->dev,
				"PS4_UVD_CREATE FAULT_VMID vmid=%u total=%llu saturated=%u first=%08x/%08x/%08x last=%08x/%08x/%08x\n",
				i, (unsigned long long)b->arrivals, b->saturated,
				b->first.addr, b->first.status, b->first.mc_client,
				b->last.addr, b->last.status, b->last.mc_client);
	}
	for (i = 0; i < op->records; ++i) {
		const struct ps4_create_record *r = &op->log[i];

		dev_info(adev->dev,
			"PS4_UVD_CREATE REC n=%u begin_ns=%llu end_ns=%llu status=%08x rp=%u wp=%u ib=%u context=%08x fence=%08x fault_total=%llu core_valid=%u history_valid=%u predeadline=%u joint=%u\n",
			i, (unsigned long long)r->begin_ns, (unsigned long long)r->end_ns,
			r->status, r->rptr, r->wptr, r->ib_size, r->context, r->fence,
			(unsigned long long)r->fault_arrivals, r->core_valid,
			r->history_valid, r->predeadline, r->joint);
	}
	dev_info(adev->dev,
		"PS4_UVD_CREATE END result=%s error=%d entered=%u enabled=%u submitted=%u publications=%u records=%u valid=%u joint=%u consecutive=%u publish_before_ns=%llu publish_after_ns=%llu confirmed_ns=%llu end_ns=%llu endpoint=%u sweep_attempted=%u sweep_valid=%u baseline_readback=%08x fence=%08x; retained, no parse/session/decode/quiescence claim\n",
		ps4_create_name(op->result.terminal), op->result.error, op->entered,
		op->enable_entered, op->submitted, op->publication_count, op->records,
		op->valid_samples, op->joint_samples, op->consecutive,
		(unsigned long long)op->publish_before_ns,
		(unsigned long long)op->publish_after_ns,
		(unsigned long long)op->confirmed_ns, (unsigned long long)op->end_ns,
		op->endpoint_valid, op->sweep_attempted, op->sweep_valid,
		op->baseline_readback, op->fence);
}
/* MESSAGE_CREATE_FLOW_END */
