/* DECODE_FRAME_FLOW_BEGIN */
/* SPDX-License-Identifier: MIT
 * Linux psX, 2026. One fixed-reference picture through the public legacy UVD
 * ABI. Startup and transport remain explicit Liverpool working assumptions.
 * No normal DRM submission, DMA drain/free, reset, retry or rearm is added.
 * All three main-ring portions are prepared once, then appended 16/32/48.
 */
#define PS4_FRAME_GUARD 0xd4c3b2a1U
#define PS4_FRAME_BASE_CONTEXT 0x46524230U
#define PS4_FRAME_LIMIT_NS (2ULL * NSEC_PER_SEC)
#define PS4_FRAME_MAX_POLLS 2048U

static bool ps4_frame_descriptors_valid(const struct amdgpu_ps4_uvd_ring *rb,
					const struct amdgpu_ps4_uvd_ring *work,
					u64 pd_addr)
{
	unsigned long a, b;

	if (!ps4_token_descriptor_valid(rb) || !work || !work->cpu ||
	    ((unsigned long)work->cpu & 3U) || work->bytes != PS4_FRAME_STORAGE_BYTES ||
	    (unsigned long)work->cpu > ~0UL - (work->bytes - 1) ||
	    work->private_va != PS4_READY_IB_VA || !pd_addr ||
	    (pd_addr & 0xfffU) || (pd_addr >> 12) > U32_MAX)
		return false;
	a = (unsigned long)rb->cpu;
	b = (unsigned long)work->cpu;
	return a > b + work->bytes - 1 || b > a + rb->bytes - 1;
}

/* Public UVD5 packet adaptation uses the unchanged packet builder's notice.
 * MSG IB: DATA0/1, CMD=0, NOPs. Main: IB address/size, context, memory fence
 * prefix, NOP. The interrupt/trap suffix is deliberately omitted.
 */
static void ps4_frame_pair(u8 *bytes, u32 dw, u32 reg, u32 value)
{
	put_unaligned_le32(reg & 0xffffU, bytes + dw * 4);
	put_unaligned_le32(value, bytes + (dw + 1) * 4);
}

static void ps4_frame_message_packets(u8 *main, u8 *ib, u64 ib_va,
				      u64 message_va, u64 fence_va, u32 sequence)
{
	u32 dw;

	ps4_frame_pair(ib, 0, 0x3bc4, lower_32_bits(message_va));
	ps4_frame_pair(ib, 2, 0x3bc5, upper_32_bits(message_va));
	ps4_frame_pair(ib, 4, 0x3bc3, 0);
	for (dw = 6; dw < 16; dw += 2)
		ps4_frame_pair(ib, dw, 0x3bff, 0);
	ps4_frame_pair(main, 0, 0x3c67, lower_32_bits(ib_va));
	ps4_frame_pair(main, 2, 0x3c66, upper_32_bits(ib_va));
	ps4_frame_pair(main, 4, 0x3da2, 16);
	ps4_frame_pair(main, 6, 0x3dbd, sequence);
	ps4_frame_pair(main, 8, 0x3bc4, lower_32_bits(fence_va));
	ps4_frame_pair(main, 10, 0x3bc5, upper_32_bits(fence_va) & 0xffU);
	ps4_frame_pair(main, 12, 0x3bc3, 0);
	ps4_frame_pair(main, 14, 0x3bff, 0);
}

static int ps4_frame_plan(struct amdgpu_ps4_uvd_frame_operation *op)
{
	struct ps4_uvd_decode_input in = {
		.vcpu_segment_base = PS4_READY_VCPU_VA,
		.main_va = op->ring_va, .work_va = op->backing_va,
		.main_bytes = 4096, .work_bytes = PS4_FRAME_STORAGE_BYTES,
		.handle = 1, .fence_initial = 0x46444930U,
		.fence_sequence = 0x46524431U,
		.fixture = ps4_frame_fixture, .fixture_bytes = sizeof(ps4_frame_fixture),
	};
	u32 i;
	int r;

	sha256(in.fixture, in.fixture_bytes, in.verified_fixture_sha256);
	r = ps4_uvd_decode_reference_build(&op->plan, sizeof(op->plan), &in);
	if (r)
		return r;
	r = ps4_uvd_msg_create_avc(op->create_le, sizeof(op->create_le), 1,
				  &op->plan.create_params);
	if (r)
		return r;
	r = ps4_uvd_msg_destroy(op->destroy_le, sizeof(op->destroy_le), 1);
	if (r)
		return r;
	for (i = 0; i < PS4_FRAME_PHASES; ++i) {
		struct ps4_frame_phase *p = &op->phases[i];

		p->initial = 0x46434930U + i * 0x100U;
		p->cell = 0x40U + i * 0x40U;
		p->end_dw = 16U * (i + 1);
	}
	op->phases[0].sequence = 0x46524331U;
	op->phases[1].sequence = in.fence_sequence;
	op->phases[2].sequence = 0x46525831U;
	op->phases[1].initial = in.fence_initial;
	op->phases[0].previous_context = PS4_FRAME_BASE_CONTEXT;
	op->phases[1].previous_context = op->phases[0].sequence;
	op->phases[2].previous_context = op->phases[1].sequence;
	ps4_frame_message_packets(op->main_le, op->create_ib_le, op->backing_va,
		op->backing_va + PS4_UVD_DEC_CREATE_OFFSET,
		op->backing_va + PS4_UVD_DEC_FENCE_OFFSET + 0x40,
		op->phases[0].sequence);
	memcpy(op->main_le + 64, op->plan.main_le, 64);
	ps4_frame_message_packets(op->main_le + 128, op->destroy_ib_le,
		op->backing_va + PS4_FRAME_DESTROY_IB_OFFSET,
		op->backing_va + PS4_FRAME_DESTROY_OFFSET,
		op->backing_va + PS4_UVD_DEC_FENCE_OFFSET + 0xc0,
		op->phases[2].sequence);
	return 0;
}

static void ps4_frame_initialize(struct amdgpu_ps4_uvd_frame_operation *op)
{
	u32 off, i;
	u8 *work = op->backing_cpu;

	for (off = 0; off < 4096; off += 4)
		ps4_create_put(op->ring_cpu, off, (off & 4U) ? 0 : 0x3bffU);
	for (off = 0; off < PS4_FRAME_STORAGE_BYTES; off += 4)
		ps4_create_put(work, off, PS4_FRAME_GUARD);
	/* A full page of valid NOP pairs around the three disjoint IBs. */
	for (off = 0; off < 4096; off += 4)
		ps4_create_put(work, off, (off & 4U) ? 0 : 0x3bffU);
	memcpy(op->ring_cpu, op->main_le, sizeof(op->main_le));
	memcpy(work, op->create_ib_le, sizeof(op->create_ib_le));
	memcpy(work + PS4_UVD_DEC_IB_OFFSET, op->plan.ib_le, sizeof(op->plan.ib_le));
	memcpy(work + PS4_FRAME_DESTROY_IB_OFFSET, op->destroy_ib_le, sizeof(op->destroy_ib_le));
	memcpy(work + PS4_UVD_DEC_CREATE_OFFSET, op->create_le, 4096);
	memcpy(work + PS4_UVD_DEC_FENCE_OFFSET, op->plan.fence_page_le, 4096);
	for (i = 0; i < PS4_FRAME_PHASES; ++i)
		ps4_create_put(work, PS4_UVD_DEC_FENCE_OFFSET + op->phases[i].cell,
			       op->phases[i].initial);
	memcpy(work + PS4_UVD_DEC_MESSAGE_OFFSET, op->plan.message_le, 4096);
	memcpy(work + PS4_UVD_DEC_FEEDBACK_OFFSET, op->plan.feedback_le, 2048);
	memcpy(work + PS4_UVD_DEC_BITSTREAM_OFFSET, op->plan.bitstream_le, 4096);
	/* Device output/reference storage has a distinct deterministic baseline. */
	memset(work + PS4_UVD_DEC_TARGET_OFFSET, 0xa7, PS4_UVD_DEC_TARGET_BYTES);
	memset(work + PS4_UVD_DEC_DPB_OFFSET, 0, PS4_UVD_DEC_DPB_MAPPED_BYTES);
	memcpy(work + PS4_FRAME_DESTROY_OFFSET, op->destroy_le, 4096);
	op->initialized = true;
}

static bool ps4_frame_bad(struct amdgpu_ps4_uvd_frame_operation *op,
			  u32 region, u32 off, u32 actual, u32 expected)
{
	if (actual == expected)
		return false;
	if (!op->memory_bad_region) {
		op->memory_bad_region = region;
		op->memory_bad_offset = off;
		op->memory_bad_value = actual;
		op->memory_bad_expected = expected;
	}
	return true;
}

/* Each unsubmitted message must still match. Submitted messages are mutable
 * ABI storage; preserve CRC/delta counts. Output, DPB and feedback become
 * writable only after DECODE publication. All other DWORDs remain guarded.
 */
static bool ps4_frame_memory(struct amdgpu_ps4_uvd_frame_operation *op)
{
	u32 off, actual, expected, i;
	bool bad = false;
	u8 *work = op->backing_cpu;
	const u8 *messages[] = { op->create_le, op->plan.message_le, op->destroy_le };
	const u32 offsets[] = { 0x1000, 0x3000, PS4_FRAME_DESTROY_OFFSET };

	for (i = 0; i < 3; ++i) {
		op->message_changed[i] = 0;
		op->message_crc[i] = crc32_le(~0U, work + offsets[i], 4096) ^ ~0U;
	}
	for (off = 0; off < 4096; off += 4) {
		expected = off < sizeof(op->main_le) ? get_unaligned_le32(op->main_le + off) :
			((off & 4U) ? 0 : 0x3bffU);
		bad |= ps4_frame_bad(op, 1, off, ps4_create_word(op->ring_cpu, off), expected);
	}
	for (off = 0; off < PS4_FRAME_STORAGE_BYTES; off += 4) {
		actual = ps4_create_word(work, off);
		for (i = 0; i < 3; ++i)
			if (off >= offsets[i] && off < offsets[i] + 4096)
				break;
		if (i < 3) {
			expected = get_unaligned_le32(messages[i] + (off - offsets[i]));
			op->message_changed[i] += actual != expected;
			if (!op->phases[i].published)
				bad |= ps4_frame_bad(op, 2, off, actual, expected);
			continue;
		}
		if (off >= 0x2000 && off < 0x3000) {
			for (i = 0; i < 3; ++i)
				if (off == 0x2000 + op->phases[i].cell)
					break;
			if (i < 3) {
				/* Active fence sampled once with direct core, prior cells
				 * must stay completed; future cells must stay initial. */
				if (op->phases[i].published && !op->phases[i].completed)
					continue;
				expected = op->phases[i].completed ? op->phases[i].sequence : op->phases[i].initial;
			} else {
				expected = PS4_UVD_DEC_FENCE_GUARD;
			}
		} else if (off < 64) {
			expected = get_unaligned_le32(op->create_ib_le + off);
		} else if (off >= 0x80 && off < 0x100) {
			expected = get_unaligned_le32(op->plan.ib_le + (off - 0x80));
		} else if (off >= 0x100 && off < 0x140) {
			expected = get_unaligned_le32(op->destroy_ib_le + (off - 0x100));
		} else if (off < 4096) {
			expected = (off & 4U) ? 0 : 0x3bffU;
		} else if (off >= 0x4000 && off < 0x4800) {
			if (op->phases[1].published)
				continue;
			expected = get_unaligned_le32(op->plan.feedback_le + (off - 0x4000));
		} else if (off >= 0x5000 && off < 0x6000) {
			expected = get_unaligned_le32(op->plan.bitstream_le + (off - 0x5000));
		} else if (off >= 0x6000 && off < 0xc000) {
			if (op->phases[1].published)
				continue;
			expected = 0xa7a7a7a7U;
		} else if (off >= 0xc000 && off < PS4_FRAME_DESTROY_OFFSET) {
			if (op->phases[1].published && off < 0xc000 + PS4_UVD_DEC_DPB_BYTES)
				continue;
			/* The sub-page DPB tail remains outside declared DPB bytes. */
			expected = 0;
		} else {
			expected = PS4_FRAME_GUARD;
		}
		bad |= ps4_frame_bad(op, 2, off, actual, expected);
	}
	return !bad;
}

static struct ps4_frame_result
ps4_frame_finish(struct amdgpu_ps4_uvd_frame_operation *op, const char *name, int error)
{
	op->completed = true;
	op->result = (struct ps4_frame_result) { name, error };
	return op->result;
}

static int ps4_frame_submit(struct amdgpu_device *adev,
			    struct amdgpu_ps4_uvd_frame_operation *op,
			    u32 cache_vmid, u32 fault_base, int reset_base)
{
	struct ps4_frame_phase *p = &op->phases[op->phase];
	struct ps4_token_sample s;
	u32 previous_dw = op->phase ? op->phases[op->phase - 1].end_dw : 0;
	bool valid, history, joint;
	u64 begin, elapsed;

	if (!ps4_frame_memory(op) || !ps4_frame_history(adev, op, false))
		return -EIO;
	valid = ps4_frame_core_sample(adev, &s, op->phase ? PS4_TOKEN_SUBMITTED : PS4_TOKEN_EMPTY,
		op->pd_addr, cache_vmid, fault_base, reset_base, previous_dw);
	ps4_create_save_core(&op->core, &s);
	p->fence = ps4_create_word(op->backing_cpu, 0x2000 + p->cell);
	if (!valid || s.rptr != previous_dw || s.context != p->previous_context || p->fence != p->initial)
		return -EIO;
	if (!ps4_frame_history(adev, op, false))
		return -EIO;
	mb();
	/* One cumulative WPTR store per phase. No rewriting consumed ring bytes. */
	p->start_ns = ktime_get_ns();
	p->published = true;
	++op->publications;
	WREG32(mmUVD_RBC_RB_WPTR, p->end_dw);
	p->publish_ns = ktime_get_ns() - p->start_ns;
	p->last_rptr = previous_dw;
	for (;;) {
		struct ps4_create_record *r;

		begin = ktime_get_ns() - p->start_ns;
		if (begin >= PS4_FRAME_LIMIT_NS || p->polls >= PS4_FRAME_MAX_POLLS)
			return -ETIMEDOUT;
		valid = ps4_frame_core_sample(adev, &s, PS4_TOKEN_SUBMITTED,
			op->pd_addr, cache_vmid, fault_base, reset_base, p->end_dw);
		ps4_create_save_core(&op->core, &s);
		p->fence = ps4_create_word(op->backing_cpu, 0x2000 + p->cell);
		history = ps4_frame_history(adev, op, false);
		elapsed = ktime_get_ns() - p->start_ns;
		joint = p->fence == p->sequence && s.context == p->sequence &&
			s.rptr == p->end_dw && s.wptr == p->end_dw;
		r = &p->last[p->polls++ & 1U];
		*r = (struct ps4_create_record) {
			.begin_ns = begin, .end_ns = elapsed, .status = s.status,
			.rptr = s.rptr, .wptr = s.wptr, .ib_size = s.ib_size,
			.context = s.context, .fence = p->fence, .core_valid = valid,
			.history_valid = history, .predeadline = elapsed < PS4_FRAME_LIMIT_NS,
			.joint = joint, .fault_arrivals = op->fault_latest.history.total.arrivals,
		};
		p->end_ns = elapsed;
		if (!valid || !history || s.rptr < p->last_rptr ||
		    (p->fence != p->initial && p->fence != p->sequence) ||
		    (s.context != p->previous_context && s.context != p->sequence) ||
		    (p->fence_seen && p->fence != p->sequence) ||
		    (p->context_seen && s.context != p->sequence))
			return -EIO;
		p->last_rptr = s.rptr;
		p->fence_seen |= p->fence == p->sequence;
		p->context_seen |= s.context == p->sequence;
		if (elapsed >= PS4_FRAME_LIMIT_NS)
			return -ETIMEDOUT;
		p->joint += joint;
		p->consecutive = joint ? p->consecutive + 1 : 0;
		if (p->consecutive == 2) {
			p->confirmed_ns = elapsed;
			p->completed = true;
			/* Final guards are outside the deadline and cannot turn a
			 * timeout into completion. No next publication on a failure. */
			return ps4_frame_memory(op) && ps4_frame_history(adev, op, false) ? 0 : -EIO;
		}
		usleep_range(1000, 2000);
	}
}

/* CPU snapshot after a completed DECODE fence; 64x64 visible NV12 is gathered
 * from the public linear target with pitch256 and UV at byte16384. Padding
 * never enters the image digest. Device data is copied before reporting.
 */
static void ps4_frame_capture(struct amdgpu_ps4_uvd_frame_operation *op)
{
	u8 *device_target = (u8 *)op->backing_cpu + PS4_UVD_DEC_TARGET_OFFSET;
	u8 *device_feedback = (u8 *)op->backing_cpu + PS4_UVD_DEC_FEEDBACK_OFFSET;
	u8 *target = op->pitched;
	u32 row, x, out = 0;

	rmb();
	for (x = 0; x < sizeof(op->pitched); ++x)
		op->pitched[x] = READ_ONCE(device_target[x]);
	for (x = 0; x < sizeof(op->feedback); ++x)
		op->feedback[x] = READ_ONCE(device_feedback[x]);
	for (row = 0; row < 64; ++row)
		for (x = 0; x < 64; ++x)
			op->visible[out++] = target[row * 256 + x];
	for (row = 0; row < 32; ++row)
		for (x = 0; x < 64; ++x)
			op->visible[out++] = target[0x4000 + row * 256 + x];
	op->target_crc = crc32_le(~0U, target, PS4_UVD_DEC_TARGET_BYTES) ^ ~0U;
	op->feedback_crc = crc32_le(~0U, op->feedback, sizeof(op->feedback)) ^ ~0U;
	sha256(op->visible, sizeof(op->visible), op->visible_sha256);
	op->pixels_captured = true;
	op->pixels_match = !memcmp(op->visible_sha256, ps4_uvd_decode_expected_nv12_sha256, 32);
}

static struct ps4_frame_result
ps4_frame_run(struct amdgpu_device *adev, const struct amdgpu_ps4_uvd_ring *rb,
	      const struct amdgpu_ps4_uvd_ring *work, u64 pd_addr,
	      u32 cache_vmid, u32 fault_base, int reset_base)
{
	struct amdgpu_ps4_uvd_frame_operation *op = &adev->ps4_uvd_frame_operation;
	struct amdgpu_ps4_uvd_ready_operation *ready = &adev->ps4_uvd_ready_operation;
	struct ps4_token_sample sample;
	int r;

	if (op->entered || !ready->boot_claimed || !ready->completed ||
	    ready->result.terminal != PS4_READY_CORE_READY_CONVENTION_OBSERVED || ready->result.error)
		return (struct ps4_frame_result) { "FRAME_NOT_AUTHORIZED", -EPERM };
	op->entered = true;
	if (!ps4_uvd_decode_frame || !amdgpu_ps4_uvd_decode_frame_options_valid(adev) ||
	    !ps4_frame_descriptors_valid(rb, work, pd_addr))
		return ps4_frame_finish(op, "FRAME_INPUTS", -EINVAL);
	op->ring_cpu = rb->cpu;
	op->backing_cpu = work->cpu;
	op->ring_va = rb->private_va;
	op->backing_va = work->private_va;
	op->pd_addr = pd_addr;
	r = ps4_frame_plan(op);
	if (r)
		return ps4_frame_finish(op, "FRAME_PLAN", r);
	if (!ps4_frame_history(adev, op, true))
		return ps4_frame_finish(op, "FRAME_HISTORY_BASELINE", -EBUSY);
	if (!ps4_frame_core_sample(adev, &sample, PS4_TOKEN_HELD,
		pd_addr, cache_vmid, fault_base, reset_base, 0)) {
		ps4_create_save_core(&op->core, &sample);
		return ps4_frame_finish(op, "FRAME_HELD_GUARD", -EIO);
	}
	if (!ps4_stobs_ctx_sweep(adev, &sample)) {
		ps4_create_save_core(&op->sweep, &sample);
		return ps4_frame_finish(op, "FRAME_CONTEXT_GUARD", -EIO);
	}
	ps4_frame_initialize(op);
	WREG32(mmUVD_CONTEXT_ID, PS4_FRAME_BASE_CONTEXT);
	mb();
	amdgpu_device_flush_hdp(adev, NULL);
	if (!ps4_frame_memory(op) || !ps4_frame_history(adev, op, false))
		return ps4_frame_finish(op, "FRAME_PREPARED_GUARD", -EIO);
	if (!ps4_frame_core_sample(adev, &sample, PS4_TOKEN_HELD,
		pd_addr, cache_vmid, fault_base, reset_base, 0) || sample.context != PS4_FRAME_BASE_CONTEXT) {
		ps4_create_save_core(&op->core, &sample);
		return ps4_frame_finish(op, "FRAME_BASELINE", -EIO);
	}
	dev_info(adev->dev, "PS4_UVD_FRAME PREPARED fixture=h264-cb-idr-64x64-v1 handle=1 width=64 height=64 pitch=256 uv=16384 ring=%llx work=%llx bytes=%u phase_deadline_ns=%llu; CREATE DECODE DESTROY cumulative_wptr=16,32,48\n",
		op->ring_va, op->backing_va, PS4_FRAME_STORAGE_BYTES, (unsigned long long)PS4_FRAME_LIMIT_NS);
	op->enabled = true;
	WREG32(mmUVD_RBC_RB_CNTL, PS4_TOKEN_RBC_FETCH);
	for (op->phase = 0; op->phase < PS4_FRAME_PHASES; ++op->phase) {
		r = ps4_frame_submit(adev, op, cache_vmid, fault_base, reset_base);
		if (r) {
			/* Raw failed-run output can diagnose firmware progression.
			 * It never replaces the failed completion result. */
			if (op->phases[1].published)
				ps4_frame_capture(op);
			return ps4_frame_finish(op, r == -ETIMEDOUT ? "FRAME_PHASE_TIMEOUT" : "FRAME_PHASE_GUARD", r);
		}
		if (op->phase == 1) {
			ps4_frame_capture(op);
			if (!op->pixels_match)
				return ps4_frame_finish(op, "FRAME_PIXELS_MISMATCH", -EBADMSG);
		}
	}
	if (!ps4_frame_core_sample(adev, &sample, PS4_TOKEN_SUBMITTED,
		pd_addr, cache_vmid, fault_base, reset_base, 48) ||
	    sample.rptr != 48 || sample.context != op->phases[2].sequence) {
		ps4_create_save_core(&op->core, &sample);
		return ps4_frame_finish(op, "FRAME_FINAL_CORE", -EIO);
	}
	ps4_create_save_core(&op->core, &sample);
	if (!ps4_stobs_ctx_sweep(adev, &sample)) {
		ps4_create_save_core(&op->sweep, &sample);
		return ps4_frame_finish(op, "FRAME_FINAL_CONTEXT", -EIO);
	}
	ps4_create_save_core(&op->sweep, &sample);
	if (!ps4_frame_memory(op) || !ps4_frame_history(adev, op, false))
		return ps4_frame_finish(op, "FRAME_FINAL_MEMORY", -EIO);
	return ps4_frame_finish(op, "FRAME_REFERENCE_MATCH", 0);
}

static void ps4_frame_report(struct amdgpu_device *adev)
{
	const struct amdgpu_ps4_uvd_frame_operation *op = &adev->ps4_uvd_frame_operation;
	const struct ps4_create_saved_core *s = &op->core;
	u32 i, j;

	/* Saved CPU state only. Every GPU access ends before this report. */
	dev_info(adev->dev, "PS4_UVD_FRAME CORE status=%08x rbc=%08x ib=%u rp=%u wp=%u context=%08x fault=%08x reset_count=%u reset=%u guard=%u/%08x/%08x/%08x\n",
		s->status, s->rbc, s->ib_size, s->rptr, s->wptr, s->context, s->fault,
		s->reset_count, s->in_reset, s->bad_kind, s->bad_reg, s->bad_value, s->bad_expected);
	for (i = 0; i < 3; ++i) {
		const struct ps4_frame_phase *p = &op->phases[i];

		dev_info(adev->dev, "PS4_UVD_FRAME PHASE index=%u published=%u completed=%u end_dw=%u seq=%08x initial=%08x polls=%u joint=%u consecutive=%u publish_ns=%llu confirmed_ns=%llu end_ns=%llu message_crc=%08x changed_words=%u\n",
			i, p->published, p->completed, p->end_dw, p->sequence, p->initial,
			p->polls, p->joint, p->consecutive, p->publish_ns, p->confirmed_ns, p->end_ns,
			op->message_crc[i], op->message_changed[i]);
		for (j = 0; j < 2 && j < p->polls; ++j) {
			const struct ps4_create_record *r = &p->last[(p->polls - (p->polls > 1 ? 2 : 1) + j) & 1U];

			dev_info(adev->dev, "PS4_UVD_FRAME REC phase=%u last=%u begin_ns=%llu end_ns=%llu status=%08x rp=%u wp=%u ib=%u context=%08x fence=%08x core=%u history=%u predeadline=%u joint=%u\n",
				i, j, r->begin_ns, r->end_ns, r->status, r->rptr, r->wptr, r->ib_size,
				r->context, r->fence, r->core_valid, r->history_valid, r->predeadline, r->joint);
		}
	}
	dev_info(adev->dev, "PS4_UVD_FRAME MEMORY bad_region=%u offset=%08x expected=%08x actual=%08x feedback_crc=%08x target_crc=%08x history_snapshots=%u history_reason=%u first_history_reason=%u fault_arm=%llu fault_total=%llu\n",
		op->memory_bad_region, op->memory_bad_offset, op->memory_bad_expected, op->memory_bad_value,
		op->feedback_crc, op->target_crc, op->history_snapshots, op->history_reason,
		op->first_history_reason, op->fault_latest.arm_ns, op->fault_latest.history.total.arrivals);
	if (op->first_history_reason)
		ps4_create_report_deciding_history(adev, &op->fault_deciding);
	if (op->pixels_captured) {
		dev_info(adev->dev, "PS4_UVD_FRAME PIXELS bytes=%u sha256=%*phN expected=%*phN match=%u\n",
			PS4_FRAME_VISIBLE_BYTES, 32, op->visible_sha256, 32,
			ps4_uvd_decode_expected_nv12_sha256, op->pixels_match);
		for (i = 0; i < sizeof(op->feedback); i += 32)
			dev_info(adev->dev, "PS4_UVD_FRAME FEEDBACK offset=%u hex=%*phN\n", i, 32, op->feedback + i);
		for (i = 0; i < sizeof(op->pitched); i += 32)
			dev_info(adev->dev, "PS4_UVD_FRAME PITCHED offset=%u hex=%*phN\n", i, 32, op->pitched + i);
	}
	dev_info(adev->dev, "PS4_UVD_FRAME END result=%s error=%d entered=%u initialized=%u enabled=%u phase=%u publications=%u pixels=%u match=%u; mappings retained, no DMA quiescence/free/rearm\n",
		op->result.name ? op->result.name : "FRAME_NONE", op->result.error, op->entered,
		op->initialized, op->enabled, op->phase, op->publications, op->pixels_captured, op->pixels_match);
}
/* DECODE_FRAME_FLOW_END */
