/* IB_DATA_FLOW_BEGIN: extracted verbatim by the host fixture. */
/*
 * Original Linux psX diagnostic additions, MIT, 2026.
 * The direct sampler is adapted from the preserved MIT readiness sampler;
 * the unchanged old flows and their notices remain in the containing file.
 * One DATA0 marker exists only in the owned IB, never in the main ring or
 * in a CPU register write. This is a boot-only diagnostic, not a decoder.
 * After three full prepublication samples, all observation-window reads
 * are direct. No GPU/UVD write or print occurs in the record loop. A valid
 * terminal direct sample may be followed by one endpoint indexed sweep;
 * all such accesses finish before reporting. UART reporting may use MMIO.
 */
#define PS4_IBDATA_BASELINE 0x43504442U
#define PS4_IBDATA_VALUE 0x55564442U
#define PS4_IBDATA_CONTEXT_BASELINE 0x43504342U
#define PS4_IBDATA_LIMIT_NS (250ULL * NSEC_PER_MSEC)
#define PS4_IBDATA_FINE_SAMPLES 24U
#define PS4_IBDATA_COARSE_SAMPLES 16U
#define PS4_IBDATA_RECORDS (PS4_IBDATA_FINE_SAMPLES + PS4_IBDATA_COARSE_SAMPLES)
#define PS4_IBDATA_FINE_SLEEP_LOW 100U
#define PS4_IBDATA_FINE_SLEEP_HIGH 200U
#define PS4_IBDATA_COARSE_SLEEP_LOW 12000U
#define PS4_IBDATA_COARSE_SLEEP_HIGH 12500U

struct ps4_ib_data_record {
	u64 elapsed_ns, elapsed_end_ns;
	struct ps4_data_sample sample;
	bool valid, predeadline, bit16;
};

struct ps4_ib_data_progress {
	u32 valid_samples, consecutive, joint_samples, last_rptr;
	bool data_seen, transport_seen, confirmed, observed_data_seen, bit16_seen;
	u64 first_data_ns, first_data_end_ns, first_transport_ns;
	u64 first_joint_ns, confirmed_ns;
};

static struct ps4_ib_data_record ps4_ib_data_log[PS4_IBDATA_RECORDS];
static unsigned int ps4_ib_data_record_count;
static u64 ps4_ib_data_publish_ns;

/* Numeric validation complements the caller's retained-owner checks. Both
 * exact private VAs and the full CPU page ranges must be distinct. No CPU
 * pointer or native GART alias is used to derive the IB address.
 */
static bool ps4_ib_data_descriptors_valid(const struct amdgpu_ps4_uvd_ring *rb,
	const struct amdgpu_ps4_uvd_ring *ib)
{
	unsigned long rb_start, ib_start;

	if (!ps4_token_descriptor_valid(rb) || !ib || !ib->cpu ||
	    ((unsigned long)ib->cpu & 3U) ||
	    (unsigned long)ib->cpu > ~0UL - (PS4_UVD_IB_DATA_PAGE_BYTES - 1U) ||
	    ib->bytes != PS4_UVD_IB_DATA_PAGE_BYTES ||
	    ib->private_va != PS4_READY_IB_VA)
		return false;
	rb_start = (unsigned long)rb->cpu;
	ib_start = (unsigned long)ib->cpu;
	return rb_start > ib_start + ib->bytes - 1U ||
		ib_start > rb_start + rb->bytes - 1U;
}

static bool ps4_ib_data_inputs_valid(const struct amdgpu_ps4_uvd_ring *rb,
	const struct amdgpu_ps4_uvd_ring *ib, u64 pd_addr)
{
	return ps4_uvd_ib_data && ps4_uvd_token && ps4_uvd_ready &&
		ps4_uvd_poll_state && !ps4_uvd_data_control &&
		!ps4_uvd_status_observation &&
		ps4_ib_data_descriptors_valid(rb, ib) && pd_addr &&
		!(pd_addr & 0xfff) && (pd_addr >> 12) <= U32_MAX;
}

/* IB_DATA_CORE_SAMPLE_BEGIN: same Revision C direct sampler except that
 * IB_SIZE is required to be zero only before publication. Submitted IB_SIZE
 * is recorded raw: no countdown, consumption or completion rule is assumed.
 */
static bool ps4_ib_data_core_sample(struct amdgpu_device *adev,
			    struct ps4_token_sample *s, enum ps4_token_phase phase,
			    u64 pd_addr, u32 cache_vmid, u32 fault_base, int reset_base)
{
	*s = (struct ps4_token_sample) { 0 };
	/* Every field printed by CORE is sampled, even on an early rejection. */
	s->status = RREG32(mmUVD_STATUS);
	s->mastint = RREG32(mmUVD_MASTINT_EN);
	s->rbc = RREG32(mmUVD_RBC_RB_CNTL);
	s->ib_size = RREG32(mmUVD_RBC_IB_SIZE);
	s->wptr = RREG32(mmUVD_RBC_RB_WPTR);
	s->rptr = RREG32(mmUVD_RBC_RB_RPTR);
	s->context = RREG32(mmUVD_CONTEXT_ID);
	s->wptr_cntl = RREG32(mmUVD_RBC_RB_WPTR_CNTL);
	s->rptr_addr = RREG32(mmUVD_RBC_RB_RPTR_ADDR);
	s->fault = RREG32(mmVM_CONTEXT1_PROTECTION_FAULT_STATUS);
	s->reset_count = atomic_read(&adev->gpu_reset_counter);
	s->in_reset = amdgpu_in_reset(adev);
	s->pd = RREG32(mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + 4);
#define TOKEN_SAVED(reg, got, want) \
	if (!ps4_token_expect(s, 0, reg, got, ~0U, want)) return false
	/* STOBS: submitted-phase STATUS additionally tolerates exactly bit16.
	 * The comparison observes bit16; it does not prove busy semantics,
	 * GPCOM naming, DMA quiescence or any Liverpool field specification.
	 */
	if (!ps4_stobs_status_expect(s, phase))
		return false;
	TOKEN_SAVED(mmUVD_MASTINT_EN, s->mastint, UVD_MASTINT_EN__VCPU_EN_MASK);
	TOKEN_SAVED(mmUVD_RBC_RB_CNTL, s->rbc,
		phase == PS4_TOKEN_HELD ? PS4_TOKEN_RBC_HELD : PS4_TOKEN_RBC_FETCH);
	if (phase != PS4_TOKEN_SUBMITTED)
		TOKEN_SAVED(mmUVD_RBC_IB_SIZE, s->ib_size, 0);
	TOKEN_SAVED(mmUVD_RBC_RB_WPTR_CNTL, s->wptr_cntl, 0);
	TOKEN_SAVED(mmUVD_RBC_RB_RPTR_ADDR, s->rptr_addr, 0);
	TOKEN_SAVED(mmUVD_RBC_RB_WPTR, s->wptr,
		phase == PS4_TOKEN_SUBMITTED ? PS4_UVD_SUBMIT_DWORDS : 0);
	if (phase != PS4_TOKEN_SUBMITTED) {
		TOKEN_SAVED(mmUVD_RBC_RB_RPTR, s->rptr, 0);
	} else if (s->rptr > PS4_UVD_SUBMIT_DWORDS) {
		return ps4_token_expect(s, 3, mmUVD_RBC_RB_RPTR,
					s->rptr, ~0U, PS4_UVD_SUBMIT_DWORDS);
	}
	TOKEN_SAVED(mmVM_CONTEXT1_PROTECTION_FAULT_STATUS, s->fault, fault_base);
	TOKEN_SAVED(mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + 4, s->pd, pd_addr >> 12);
	if (!ps4_token_expect(s, 2, 1, s->reset_count, ~0U, reset_base) ||
	    !ps4_token_expect(s, 2, 2, s->in_reset, ~0U, 0) ||
	    !ps4_token_expect(s, 2, 3, adev->mman.buffer_funcs_enabled, ~0U, 0) ||
	    !ps4_token_expect(s, 2, 4, adev->accel_working, ~0U, 0))
		return false;
#define TOKEN_REG(reg, mask, want) \
	if (!ps4_token_expect(s, 0, reg, RREG32(reg), mask, want)) return false
	TOKEN_REG(PS4_READY_UVD_VCPU_CACHE_VMID, ~0U, cache_vmid);
	TOKEN_REG(mmUVD_LMI_RBC_RB_VMID, ~0U, 4);
	TOKEN_REG(mmUVD_LMI_RBC_IB_VMID, ~0U, 4);
	TOKEN_REG(mmUVD_LMI_VCPU_CACHE_64BIT_BAR_LOW, ~0U, lower_32_bits(PS4_READY_VCPU_VA));
	TOKEN_REG(mmUVD_LMI_VCPU_CACHE_64BIT_BAR_HIGH, ~0U, upper_32_bits(PS4_READY_VCPU_VA));
	TOKEN_REG(mmUVD_LMI_RBC_RB_64BIT_BAR_LOW, ~0U, lower_32_bits(PS4_READY_RB_VA));
	TOKEN_REG(mmUVD_LMI_RBC_RB_64BIT_BAR_HIGH, ~0U, upper_32_bits(PS4_READY_RB_VA));
	TOKEN_REG(mmUVD_LMI_RBC_IB_64BIT_BAR_LOW, ~0U, lower_32_bits(PS4_READY_IB_VA));
	TOKEN_REG(mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH, ~0U, upper_32_bits(PS4_READY_IB_VA));
	TOKEN_REG(mmUVD_VCPU_CACHE_OFFSET0, ~0U, 0);
	TOKEN_REG(mmUVD_VCPU_CACHE_SIZE0, ~0U, 0x7d000);
	TOKEN_REG(mmUVD_VCPU_CACHE_OFFSET1, ~0U, 0xfa00);
	TOKEN_REG(mmUVD_VCPU_CACHE_SIZE1, ~0U, 0x40000);
	TOKEN_REG(mmUVD_VCPU_CACHE_OFFSET2, ~0U, 0x17a00);
	TOKEN_REG(mmUVD_VCPU_CACHE_SIZE2, ~0U, 0x120800);
	TOKEN_REG(mmUVD_LMI_SWAP_CNTL, ~0U, 0);
	TOKEN_REG(mmUVD_MP_SWAP_CNTL, ~0U, 0);
	TOKEN_REG(mmUVD_LMI_ADDR_EXT, ~0U, 0);
	TOKEN_REG(mmUVD_LMI_EXT40_ADDR, ~0U, 0);
	TOKEN_REG(mmUVD_SOFT_RESET, PS4_READY_HOLD, 0);
	TOKEN_REG(mmUVD_LMI_CTRL2, UVD_LMI_CTRL2__STALL_ARB_UMC_MASK, 0);
	/* Indexed context reads moved after the window (STOBS review B1);
	 * ps4_stobs_ctx_sweep performs the identical frozen block.
	 */
#undef TOKEN_REG
#undef TOKEN_SAVED
	return true;
}
/* IB_DATA_CORE_SAMPLE_END */

static bool ps4_ib_data_baselines(struct ps4_data_sample *sample)
{
	return ps4_token_expect(&sample->core, 0, mmUVD_GPCOM_VCPU_DATA0,
		sample->data, ~0U, PS4_IBDATA_BASELINE) &&
		ps4_token_expect(&sample->core, 0, mmUVD_CONTEXT_ID,
			sample->core.context, ~0U, PS4_IBDATA_CONTEXT_BASELINE);
}

/* The exact direct core sequence, DATA0 last, then the marker allowances.
 * Public UVD5 bit0 and the previously observed bit16 are recorded under
 * the frozen submitted STATUS predicate. No Liverpool semantics follow.
 */
static bool ps4_ib_data_monitor(struct amdgpu_device *adev,
	struct ps4_data_sample *sample, u64 pd_addr, u32 cache_vmid,
	u32 fault_base, int reset_base, bool *bit16)
{
	bool valid = ps4_ib_data_core_sample(adev, &sample->core,
		PS4_TOKEN_SUBMITTED, pd_addr, cache_vmid, fault_base, reset_base);

	sample->data = RREG32(mmUVD_GPCOM_VCPU_DATA0);
	*bit16 = !!(sample->core.status & PS4_STOBS_BIT16);
	if (!valid)
		return false;
	if (sample->data != PS4_IBDATA_BASELINE &&
	    sample->data != PS4_IBDATA_VALUE)
		return ps4_token_expect(&sample->core, 0,
			mmUVD_GPCOM_VCPU_DATA0, sample->data, ~0U,
			PS4_IBDATA_VALUE);
	return ps4_token_expect(&sample->core, 0, mmUVD_CONTEXT_ID,
		sample->core.context, ~0U, PS4_IBDATA_CONTEXT_BASELINE);
}

static const char *ps4_ib_data_guard_result(const struct ps4_data_sample *s)
{
	if (s->core.bad_kind == 0 &&
	    s->core.bad_reg == mmUVD_GPCOM_VCPU_DATA0)
		return "IBDATA_UNEXPECTED_VALUE";
	if (s->core.bad_kind == 0 && s->core.bad_reg == mmUVD_CONTEXT_ID)
		return "IBDATA_UNEXPECTED_CONTEXT";
	if (s->core.bad_kind == 0 && s->core.bad_reg == mmUVD_STATUS)
		return "IBDATA_STATUS_UNEXPECTED";
	return "IBDATA_CORE_GUARD";
}

/* This classification uses qualifying in-window samples only. Endpoint
 * reads never create data_seen, transport_seen or confirmation evidence.
 */
static const char *ps4_ib_data_evidence_class(
	const struct ps4_ib_data_progress *progress)
{
	if (progress->confirmed)
		return "IBDATA_MARKER_OBSERVED";
	if (progress->data_seen)
		return "IBDATA_MARKER_UNCONFIRMED";
	if (progress->bit16_seen)
		return "IBDATA_STATUS_WITHOUT_MARKER";
	if (progress->transport_seen)
		return "IBDATA_DRAINED_NO_MARKER";
	return "IBDATA_NO_TRANSPORT";
}

static void ps4_ib_data_print_sample(struct amdgpu_device *adev,
	const char *tag, const struct ps4_data_sample *sample, bool valid)
{
	const struct ps4_token_sample *s = &sample->core;

	dev_info(adev->dev,
		"PS4_UVD_IBDATA %s sampled=all_then_data0 status=%08x int=%08x rbc=%08x ib=%08x wp=%08x rp=%08x ctx=%08x data=%08x wpc=%08x rpa=%08x fault=%08x reset=%u in_reset=%u pd=%08x valid=%d\n",
		tag, s->status, s->mastint, s->rbc, s->ib_size, s->wptr,
		s->rptr, s->context, sample->data, s->wptr_cntl, s->rptr_addr,
		s->fault, s->reset_count, s->in_reset, s->pd, valid);
}

static void ps4_ib_data_print_guard(struct amdgpu_device *adev,
	const char *tag, const struct ps4_token_sample *s)
{
	if (s->failed)
		dev_err(adev->dev,
			"PS4_UVD_IBDATA %s kind=%u reg=%08x value=%08x mask=%08x expected=%08x; saved deciding read\n",
			tag, s->bad_kind, s->bad_reg, s->bad_value,
			s->bad_mask, s->bad_expected);
}

static __noreturn void ps4_ib_data_stop(struct amdgpu_device *adev,
	const char *result, int error, const struct ps4_data_sample *failing,
	const struct ps4_ib_data_progress *progress, bool enabled, bool submitted,
	u64 elapsed, u64 pd_addr, u32 cache_vmid, u32 fault_base, int reset_base,
	bool with_final, const char *end_reason)
{
	struct ps4_data_sample final = { 0 };
	struct ps4_token_sample sweep = { 0 };
	const char *evidence_class = ps4_ib_data_evidence_class(progress);
	bool final_valid = false, sweep_ran = false, sweep_valid = false;
	bool final_bit16 = false;
	u32 i, predeadline_records = 0;

	/* Only a completed bounded window permits further GPU/UVD reads.
	 * All final accesses precede the first print. Invalid final direct
	 * state or a later observation regression prohibits the indexed sweep.
	 * A failed sweep is terminal, not a successful marker outcome. Its
	 * endpoint check does not establish indexed validity during the window.
	 */
	if (with_final) {
		final_valid = ps4_ib_data_monitor(adev, &final, pd_addr,
			cache_vmid, fault_base, reset_base, &final_bit16);
		if (!final_valid) {
			result = "IBDATA_FINAL_GUARD";
			error = -EIO;
		} else if (final.core.rptr < progress->last_rptr) {
			result = "IBDATA_POINTER_REGRESSION";
			error = -EIO;
		} else if (progress->observed_data_seen &&
			   final.data == PS4_IBDATA_BASELINE) {
			result = "IBDATA_MARKER_REGRESSION";
			error = -EIO;
		} else {
			sweep_ran = true;
			sweep_valid = ps4_stobs_ctx_sweep(adev, &sweep);
			if (!sweep_valid) {
				result = "IBDATA_CTX_SWEEP_GUARD";
				error = -EIO;
			} else {
				result = evidence_class;
				error = progress->confirmed ? 0 :
					(!progress->data_seen && progress->bit16_seen ?
					 -EIO : -ETIMEDOUT);
			}
		}
	}
	if (with_final) {
		ps4_ib_data_print_sample(adev, "FINAL_CORE", &final, final_valid);
		ps4_ib_data_print_guard(adev, "FINAL_GUARD", &final.core);
		if (sweep_ran) {
			dev_info(adev->dev,
				"PS4_UVD_IBDATA CTX_SWEEP valid=%d kind=%u reg=%08x value=%08x mask=%08x expected=%08x\n",
				sweep_valid, sweep.bad_kind, sweep.bad_reg,
				sweep.bad_value, sweep.bad_mask, sweep.bad_expected);
		}
	}
	if (failing) {
		ps4_ib_data_print_sample(adev, "SAVED", failing,
			!failing->core.failed);
		ps4_ib_data_print_guard(adev, "GUARD", &failing->core);
	}
	for (i = 0; i < ps4_ib_data_record_count; ++i) {
		const struct ps4_ib_data_record *r = &ps4_ib_data_log[i];

		if (r->valid && r->predeadline)
			++predeadline_records;
		dev_info(adev->dev,
			"PS4_UVD_IBDATA REC i=%u elapsed_ns=%llu t_end_ns=%llu valid=%d predeadline=%d status=%08x int=%08x rbc=%08x ib=%08x wp=%08x rp=%08x ctx=%08x data=%08x wpc=%08x rpa=%08x fault=%08x reset=%u in_reset=%u pd=%08x bit16=%d\n",
			i, (unsigned long long)r->elapsed_ns,
			(unsigned long long)r->elapsed_end_ns,
			r->valid, r->predeadline, r->sample.core.status,
			r->sample.core.mastint, r->sample.core.rbc,
			r->sample.core.ib_size, r->sample.core.wptr,
			r->sample.core.rptr, r->sample.core.context, r->sample.data,
			r->sample.core.wptr_cntl, r->sample.core.rptr_addr,
			r->sample.core.fault, r->sample.core.reset_count,
			r->sample.core.in_reset, r->sample.core.pd, r->bit16);
	}
	dev_info(adev->dev,
		"PS4_UVD_IBDATA EVIDENCE class=%s valid_samples=%u data_seen=%d transport_seen=%d consecutive=%u joint_samples=%u confirmed=%d first_data_ns=%llu first_data_end_ns=%llu first_transport_ns=%llu first_joint_ns=%llu confirmed_ns=%llu; complete valid predeadline sequential reads only, endpoint cannot create evidence\n",
		evidence_class, progress->valid_samples, progress->data_seen,
		progress->transport_seen, progress->consecutive,
		progress->joint_samples, progress->confirmed,
		(unsigned long long)progress->first_data_ns,
		(unsigned long long)progress->first_data_end_ns,
		(unsigned long long)progress->first_transport_ns,
		(unsigned long long)progress->first_joint_ns,
		(unsigned long long)progress->confirmed_ns);
	dev_info(adev->dev,
		"PS4_UVD_IBDATA END result=%s error=%d end_reason=%s enabled=%d submitted=%d elapsed_ns=%llu publish_ns=%llu records=%u predeadline_records=%u observed_data_seen=%d last_rp=%u bit16_seen=%d final_sampled=%d final_ib=%08x final_valid=%d sweep_ran=%d sweep_valid=%d marker_observed=%d accepted=0; one publication, retained, no message/decode/containment/quiescence claim\n",
		result, error, end_reason, enabled, submitted,
		(unsigned long long)elapsed,
		(unsigned long long)ps4_ib_data_publish_ns,
		ps4_ib_data_record_count, predeadline_records,
		progress->observed_data_seen, progress->last_rptr,
		progress->bit16_seen, with_final, final.core.ib_size,
		final_valid, sweep_ran, sweep_valid,
		with_final && !error && progress->confirmed);
	ps4_ready_park(adev, result, error);
}

/* One publication; four direct stores in this flow. The main ring contains
 * only IB LOW/HIGH/SIZE and NOP. DATA0 result exists only in the IB page.
 * CPU register stores plant baselines, enable empty fetch and publish WPTR.
 */
static __noreturn void ps4_ib_data_run(struct amdgpu_device *adev,
	const struct amdgpu_ps4_uvd_ring *rb,
	const struct amdgpu_ps4_uvd_ring *ib, u64 pd_addr, u32 cache_vmid,
	u32 fault_base, int reset_base)
{
	struct ps4_uvd_ib_data_plan plan;
	struct ps4_uvd_ib_data_request request;
	struct ps4_data_sample sample;
	struct ps4_ib_data_progress progress = { 0 };
	u64 started, elapsed;
	u32 readback;
	bool valid, bit16;

	ps4_ib_data_record_count = 0;
	ps4_ib_data_publish_ns = 0;
	if (!ps4_ib_data_inputs_valid(rb, ib, pd_addr))
		ps4_ib_data_stop(adev, "IBDATA_DESCRIPTOR_OR_GATES", -EINVAL,
			NULL, &progress, false, false, 0, pd_addr, cache_vmid,
			fault_base, reset_base, false, "prepublication");
	if (!ps4_data_sample(adev, &sample, PS4_TOKEN_HELD, pd_addr,
			     cache_vmid, fault_base, reset_base))
		ps4_ib_data_stop(adev, "IBDATA_PRECONDITION", -EIO,
			&sample, &progress, false, false, 0, pd_addr, cache_vmid,
			fault_base, reset_base, false, "prepublication");
	if (sample.data == PS4_IBDATA_VALUE)
		ps4_ib_data_stop(adev, "IBDATA_STALE_RESULT", -EBUSY,
			&sample, &progress, false, false, 0, pd_addr, cache_vmid,
			fault_base, reset_base, false, "prepublication");
	request = (struct ps4_uvd_ib_data_request) {
		.ring_gpu_base = rb->private_va, .ib_gpu_base = ib->private_va,
		.baseline = PS4_IBDATA_BASELINE, .token = PS4_IBDATA_VALUE,
		.data_before_baseline = sample.data,
		.initial_rptr_raw = sample.core.rptr,
		.initial_wptr_raw = sample.core.wptr,
	};
	if (ps4_uvd_ib_data_build(rb->cpu, rb->bytes, ib->cpu, ib->bytes,
				 request, &plan) != PS4_UVD_IB_DATA_OK)
		ps4_ib_data_stop(adev, "IBDATA_PLAN_REJECTED", -EINVAL,
			&sample, &progress, false, false, 0, pd_addr, cache_vmid,
			fault_base, reset_base, false, "prepublication");
	dev_info(adev->dev,
		"PS4_UVD_IBDATA PREPARED ring_bytes=%u ib_bytes=%u submit_dw=%u ib_length_dw=%u rb_va=%llx ib_va=%llx data_baseline=%08x data_value=%08x context_baseline=%08x ring_crc=%08x ib_crc=%08x; DATA0 result exists only in IB, UVD5 addressing remains a hypothesis\n",
		plan.ring_bytes, plan.ib_bytes, plan.submitted_dwords,
		plan.ib_length_dw_raw, rb->private_va, ib->private_va,
		PS4_IBDATA_BASELINE, PS4_IBDATA_VALUE,
		PS4_IBDATA_CONTEXT_BASELINE,
		crc32_le(~0U, rb->cpu, rb->bytes) ^ ~0U,
		crc32_le(~0U, ib->cpu, ib->bytes) ^ ~0U);
	WREG32(mmUVD_GPCOM_VCPU_DATA0, PS4_IBDATA_BASELINE);
	readback = RREG32(mmUVD_GPCOM_VCPU_DATA0);
	if (readback != PS4_IBDATA_BASELINE) {
		dev_err(adev->dev,
			"PS4_UVD_IBDATA BASELINE_DIRECT reg=%08x value=%08x expected=%08x; no CORE resample\n",
			mmUVD_GPCOM_VCPU_DATA0, readback, PS4_IBDATA_BASELINE);
		ps4_ib_data_stop(adev, "IBDATA_BASELINE_READBACK", -EIO,
			NULL, &progress, false, false, 0, pd_addr, cache_vmid,
			fault_base, reset_base, false, "prepublication");
	}
	WREG32(mmUVD_CONTEXT_ID, PS4_IBDATA_CONTEXT_BASELINE);
	readback = RREG32(mmUVD_CONTEXT_ID);
	if (readback != PS4_IBDATA_CONTEXT_BASELINE) {
		dev_err(adev->dev,
			"PS4_UVD_IBDATA BASELINE_DIRECT reg=%08x value=%08x expected=%08x; no CORE resample\n",
			mmUVD_CONTEXT_ID, readback, PS4_IBDATA_CONTEXT_BASELINE);
		ps4_ib_data_stop(adev, "IBDATA_CONTEXT_BASELINE_READBACK", -EIO,
			NULL, &progress, false, false, 0, pd_addr, cache_vmid,
			fault_base, reset_base, false, "prepublication");
	}
	dev_info(adev->dev,
		"PS4_UVD_IBDATA CPU_BASELINES observed data=%08x context=%08x; fetch remains inhibited\n",
		PS4_IBDATA_BASELINE, readback);
	mb();
	amdgpu_device_flush_hdp(adev, NULL);
	if (!ps4_data_sample(adev, &sample, PS4_TOKEN_HELD, pd_addr,
			     cache_vmid, fault_base, reset_base) ||
	    !ps4_ib_data_baselines(&sample))
		ps4_ib_data_stop(adev, "IBDATA_BASELINE_OR_PUBLICATION_GUARD",
			-EIO, &sample, &progress, false, false, 0, pd_addr,
			cache_vmid, fault_base, reset_base, false, "prepublication");
	dev_info(adev->dev,
		"PS4_UVD_IBDATA ENABLE_BEGIN held_baselines=data:%08x/context:%08x; empty ring only, next write clears NO_FETCH\n",
		sample.data, sample.core.context);
	started = ktime_get_ns();
	WREG32(mmUVD_RBC_RB_CNTL, PS4_TOKEN_RBC_FETCH);
	valid = ps4_data_sample(adev, &sample, PS4_TOKEN_EMPTY, pd_addr,
				cache_vmid, fault_base, reset_base);
	elapsed = ktime_get_ns() - started;
	if (elapsed >= PS4_IBDATA_LIMIT_NS)
		ps4_ib_data_stop(adev, "IBDATA_ENABLE_TIMEOUT", -ETIMEDOUT,
			&sample, &progress, true, false, elapsed, pd_addr,
			cache_vmid, fault_base, reset_base, false, "prepublication");
	if (!valid || !ps4_ib_data_baselines(&sample))
		ps4_ib_data_stop(adev, "IBDATA_ENABLE_GUARD", -EIO,
			&sample, &progress, true, false, elapsed, pd_addr,
			cache_vmid, fault_base, reset_base, false, "prepublication");
	dev_info(adev->dev,
		"PS4_UVD_IBDATA PUBLICATION_BEGIN empty_baselines=data:%08x/context:%08x elapsed_ns=%llu; next write sole WPTR16 after direct baseline and deadline recheck; no SUBMITTED print\n",
		sample.data, sample.core.context, (unsigned long long)elapsed);
	mb();
	elapsed = ktime_get_ns() - started;
	if (elapsed >= PS4_IBDATA_LIMIT_NS)
		ps4_ib_data_stop(adev, "IBDATA_BEFORE_SUBMIT_TIMEOUT", -ETIMEDOUT,
			&sample, &progress, true, false, elapsed, pd_addr,
			cache_vmid, fault_base, reset_base, false, "prepublication");
	/* Direct-only strict EMPTY guard; no print between this read and WPTR. */
	valid = ps4_ib_data_core_sample(adev, &sample.core, PS4_TOKEN_EMPTY,
		pd_addr, cache_vmid, fault_base, reset_base);
	sample.data = RREG32(mmUVD_GPCOM_VCPU_DATA0);
	elapsed = ktime_get_ns() - started;
	if (!valid || !ps4_ib_data_baselines(&sample))
		ps4_ib_data_stop(adev, "IBDATA_PREPUBLICATION_GUARD", -EIO,
			&sample, &progress, true, false, elapsed, pd_addr,
			cache_vmid, fault_base, reset_base, false, "prepublication");
	elapsed = ktime_get_ns() - started;
	if (elapsed >= PS4_IBDATA_LIMIT_NS)
		ps4_ib_data_stop(adev, "IBDATA_BEFORE_SUBMIT_TIMEOUT", -ETIMEDOUT,
			&sample, &progress, true, false, elapsed, pd_addr,
			cache_vmid, fault_base, reset_base, false, "prepublication");
	WREG32(mmUVD_RBC_RB_WPTR, plan.publish_wptr_raw);
	ps4_ib_data_publish_ns = ktime_get_ns() - started;
	for (;;) {
		u64 t_begin, t_end;
		bool joint;

		t_begin = ktime_get_ns() - started;
		if (t_begin >= PS4_IBDATA_LIMIT_NS)
			ps4_ib_data_stop(adev, "IBDATA_WINDOW_COMPLETED", 0,
				NULL, &progress, true, true, t_begin, pd_addr,
				cache_vmid, fault_base, reset_base, true, "deadline");
		if (ps4_ib_data_record_count >= PS4_IBDATA_RECORDS)
			ps4_ib_data_stop(adev, "IBDATA_RECORD_OVERFLOW", -EINVAL,
				NULL, &progress, true, true, t_begin, pd_addr,
				cache_vmid, fault_base, reset_base, false, "fault");
		valid = ps4_ib_data_monitor(adev, &sample, pd_addr, cache_vmid,
			fault_base, reset_base, &bit16);
		t_end = ktime_get_ns() - started;
		ps4_ib_data_log[ps4_ib_data_record_count++] =
			(struct ps4_ib_data_record) { .elapsed_ns = t_begin,
				.elapsed_end_ns = t_end, .sample = sample,
				.valid = valid, .predeadline = t_end < PS4_IBDATA_LIMIT_NS,
				.bit16 = bit16 };
		if (!valid)
			ps4_ib_data_stop(adev, ps4_ib_data_guard_result(&sample),
				-EIO, &sample, &progress, true, true, t_end, pd_addr,
				cache_vmid, fault_base, reset_base, false, "fault");
		/* Even a late sample can expose a regression. It cannot create
		 * qualifying evidence. These observations remain sequential.
		 */
		if (sample.core.rptr < progress.last_rptr)
			ps4_ib_data_stop(adev, "IBDATA_POINTER_REGRESSION", -EIO,
				&sample, &progress, true, true, t_end, pd_addr,
				cache_vmid, fault_base, reset_base, false, "fault");
		if (progress.observed_data_seen && sample.data == PS4_IBDATA_BASELINE)
			ps4_ib_data_stop(adev, "IBDATA_MARKER_REGRESSION", -EIO,
				&sample, &progress, true, true, t_end, pd_addr,
				cache_vmid, fault_base, reset_base, false, "fault");
		progress.last_rptr = sample.core.rptr;
		progress.observed_data_seen |= sample.data == PS4_IBDATA_VALUE;
		if (t_end >= PS4_IBDATA_LIMIT_NS)
			ps4_ib_data_stop(adev, "IBDATA_WINDOW_COMPLETED", 0,
				&sample, &progress, true, true, t_end, pd_addr,
				cache_vmid, fault_base, reset_base, true, "deadline");
		++progress.valid_samples;
		progress.bit16_seen |= bit16;
		if (!progress.data_seen && sample.data == PS4_IBDATA_VALUE) {
			progress.data_seen = true;
			progress.first_data_ns = t_begin;
			progress.first_data_end_ns = t_end;
		}
		if (!progress.transport_seen &&
		    sample.core.rptr == PS4_UVD_IB_DATA_SUBMIT_DWORDS &&
		    sample.core.wptr == PS4_UVD_IB_DATA_SUBMIT_DWORDS) {
			progress.transport_seen = true;
			progress.first_transport_ns = t_begin;
		}
		joint = sample.data == PS4_IBDATA_VALUE &&
			sample.core.rptr == PS4_UVD_IB_DATA_SUBMIT_DWORDS &&
			sample.core.wptr == PS4_UVD_IB_DATA_SUBMIT_DWORDS;
		if (joint) {
			if (!progress.joint_samples)
				progress.first_joint_ns = t_begin;
			++progress.joint_samples;
			++progress.consecutive;
			if (progress.consecutive >= 2 && !progress.confirmed) {
				progress.confirmed = true;
				progress.confirmed_ns = t_end;
			}
		} else {
			progress.consecutive = 0;
		}
		if (ps4_ib_data_record_count == PS4_IBDATA_RECORDS)
			ps4_ib_data_stop(adev, "IBDATA_WINDOW_COMPLETED", 0,
				NULL, &progress, true, true, t_end, pd_addr,
				cache_vmid, fault_base, reset_base, true, "record_budget");
		if (ps4_ib_data_record_count < PS4_IBDATA_FINE_SAMPLES)
			usleep_range(PS4_IBDATA_FINE_SLEEP_LOW,
				     PS4_IBDATA_FINE_SLEEP_HIGH);
		else
			usleep_range(PS4_IBDATA_COARSE_SLEEP_LOW,
				     PS4_IBDATA_COARSE_SLEEP_HIGH);
	}
}
/* IB_DATA_FLOW_END */
