#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate the planned status-observation candidate (Revision C).

Base = the completed data-control candidate (frozen bytes). Exactly four
additive blocks are inserted (PARAMETER, FLOW, EARLY, DISPATCH); removing
them again must reproduce the base byte-for-byte. Revision C also resolves
the follow-up review's pre-publication selector writes, sweep-failure
classification and saved-sample reporting. Revision B incorporated
the independent review (CLAUDE-EXP005-BIT16-INDEPENDENT-REVIEW.md,
sha256 5f9eb0bd...):
- B1: indexed context reads moved after the window (per-record reads write
  no UVD registers);
- B2: terminal classes change only complete outcomes;
- B3: publication timestamp recorded in the record clock;
- B4/F1: immediate pre-WPTR direct read; strict EMPTY predicates retained;
- B5: the failing record is printed in full;
- B6: the terminal sample uses the tolerant sampler, and all terminal
  register access precedes reporting;
- B8: deadline checked before sampling; TOGGLING separated from TRANSIENT.
Writes the candidate file and verifies the normalization before diffing.
"""
from pathlib import Path

WS = Path(__file__).resolve().parents[3]
BASE = WS / "lab/uvd-2026-10-19/data-control/source/candidate/drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.c"
OUT = WS / "lab/uvd-2026-10-19/status-observation/source/candidate/drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.c"
DIFFDIR = WS / "lab/uvd-2026-10-19/status-observation/scratch"

PARAMETER = """/* Separate default-off immutable PS4 UVD status-transition observation. */
static bool ps4_uvd_status_observation;
module_param_named(ps4_uvd_status_observation, ps4_uvd_status_observation, bool, 0444);
MODULE_PARM_DESC(ps4_uvd_status_observation,
	"One PS4 UVD STATUS bit16 transition observation of the EXP005 DATA0 packet; requires token+ready+poll_state, records bit16, never accepts (default 0)");

"""

FLOW = r'''/* STATUS_OBS_FLOW_BEGIN: extracted verbatim by the host fixture. */

/* Bounded observation of EXP005's STATUS bit16 onset and persistence.
 * The ring payload, four direct stores and publication count are retained.
 * The added pre-WPTR direct read, longer enabled-dwell log, before-sample
 * deadline checks and post-window indexed sweep change instrumentation.
 * The three earlier full samples still make 24 selector stores; the new
 * pre-WPTR read and record loop make none. Indexed invariants are not
 * observed between the ENABLE sample and the permitted endpoint sweep.
 * No command, clear, reset or resubmission is added. bit16 is recorded,
 * never accepted, credited or cleared; no outcome names its semantics.
 * Saved samples are printed in full; terminal GPU/UVD register access
 * precedes all reporting. UART reporting itself may use console MMIO.
 */
#define PS4_STOBS_LIMIT_NS (250ULL * NSEC_PER_MSEC)
#define PS4_STOBS_BIT16 0x00010000U
#define PS4_STOBS_FINE_SLEEP_LOW 100U
#define PS4_STOBS_FINE_SLEEP_HIGH 200U
#define PS4_STOBS_COARSE_SLEEP_LOW 12000U
#define PS4_STOBS_COARSE_SLEEP_HIGH 12500U
#define PS4_STOBS_FINE_SAMPLES 24U
#define PS4_STOBS_COARSE_SAMPLES 16U
#define PS4_STOBS_RECORDS (PS4_STOBS_FINE_SAMPLES + PS4_STOBS_COARSE_SAMPLES)

struct ps4_stobs_record {
	u64 elapsed_ns;
	u64 elapsed_end_ns;
	struct ps4_data_sample sample;
	bool bit16;
};

static struct ps4_stobs_record ps4_stobs_log[PS4_STOBS_RECORDS];
static unsigned ps4_stobs_record_count;
static u64 ps4_stobs_publish_ns;

static bool ps4_stobs_inputs_valid(const struct amdgpu_ps4_uvd_ring *rb,
	const u64 pd_addr)
{
	return ps4_uvd_status_observation && ps4_uvd_token && ps4_uvd_ready &&
		ps4_uvd_poll_state && ps4_token_descriptor_valid(rb) && pd_addr &&
		!(pd_addr & 0xfff) && (pd_addr >> 12) <= U32_MAX;
}

/* The single rule difference against ps4_token_sample, isolated here. The
 * submitted phase additionally tolerates exactly STATUS bit16
 * (2|0x00010000); HELD and EMPTY keep the strict original rule. Tolerance
 * observes; it never accepts, credits, clears or widens any other value.
 */
static bool ps4_stobs_status_expect(struct ps4_token_sample *s,
	enum ps4_token_phase phase)
{
	u32 mask = phase == PS4_TOKEN_HELD ? ~0U : ~(u32)UVD_STATUS__RBC_BUSY_MASK;

	return ps4_token_expect(s, 0, mmUVD_STATUS, s->status, mask,
		phase == PS4_TOKEN_SUBMITTED &&
		(s->status & mask) == (PS4_READY_BIT | PS4_STOBS_BIT16) ?
		s->status & mask : PS4_READY_BIT);
}

/* STATUS_OBS_CORE_SAMPLE: copies the ps4_token_sample body verbatim except
 * three documented differences, all restored and asserted byte-equal by
 * check.py: the status rule above, the indexed TOKEN_CTX reads moved to
 * ps4_stobs_ctx_sweep after the window (per-record reads write no UVD
 * registers), and the then-unused u32 declaration. Full direct-MMIO guard
 * coverage stays per record.
 */
static bool ps4_stobs_core_sample(struct amdgpu_device *adev,
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

static bool ps4_stobs_ctx_sweep(struct amdgpu_device *adev,
	struct ps4_token_sample *s)
{
	u32 value;

#define TOKEN_CTX(index, mask, want) do { \
	value = ps4_ready_ctx(adev, index, 0, 0, false); \
	if (!ps4_token_expect(s, 1, index, value, mask, want)) return false; \
} while (0)
	TOKEN_CTX(ixUVD_LMI_VMID_INTERNAL, 0x00ffffff, 0x00444444);
	TOKEN_CTX(ixUVD_LMI_VMID_INTERNAL2, ~0U, 0x44444444);
	TOKEN_CTX(ixUVD_LMI_VMID_INTERNAL3, 0xffff, 0x4444);
	TOKEN_CTX(ixUVD_LMI_CACHE_CTRL, UVD_LMI_CACHE_CTRL__VCPU_EN_MASK, 0);
#undef TOKEN_CTX
	return true;
}

/* Full governed record sampling: copied core sample above, then DATA0 read
 * last (the family sampled=all_then_data0 order), then the value
 * allowances. bit16 is derived from the recorded STATUS, never acted on.
 */
static bool ps4_stobs_monitor(struct amdgpu_device *adev,
	struct ps4_data_sample *sample, u64 pd_addr, u32 cache_vmid,
	u32 fault_base, int reset_base, bool *bit16)
{
	u32 masked;
	bool valid;

	valid = ps4_stobs_core_sample(adev, &sample->core, PS4_TOKEN_SUBMITTED,
		pd_addr, cache_vmid, fault_base, reset_base);
	sample->data = RREG32(mmUVD_GPCOM_VCPU_DATA0);
	masked = sample->core.status & ~UVD_STATUS__RBC_BUSY_MASK;
	*bit16 = masked == (PS4_READY_BIT | PS4_STOBS_BIT16);
	if (!valid)
		return false;
	if (sample->data != PS4_DATA_BASELINE && sample->data != PS4_DATA_VALUE) {
		ps4_token_expect(&sample->core, 0, mmUVD_GPCOM_VCPU_DATA0,
				 sample->data, ~0U, PS4_DATA_VALUE);
		return false;
	}
	if (sample->core.context != PS4_DATA_CONTEXT_BASELINE &&
	    sample->core.context != PS4_DATA_CONTEXT_VALUE) {
		ps4_token_expect(&sample->core, 0, mmUVD_CONTEXT_ID,
				 sample->core.context, ~0U, PS4_DATA_CONTEXT_VALUE);
		return false;
	}
	return true;
}

static __noreturn void ps4_stobs_stop(struct amdgpu_device *adev,
	const char *result, int error, const struct ps4_data_sample *failing,
	bool enabled, bool submitted, u64 elapsed, u64 pd_addr, u32 cache_vmid,
	u32 fault_base, int reset_base, bool with_final)
{
	struct ps4_data_sample final = { 0 };
	bool final_valid = false, sweep_valid = false, markers = false;
	bool prev = false, persisted = false, onset = false;
	u32 records = ps4_stobs_record_count, fine = 0, clears = 0, sets = 0;
	u32 data_regressions = 0, context_regressions = 0;
	u32 prev_data = 0, prev_context = 0;
	u32 sweep_kind = 0, sweep_reg = 0, sweep_value = 0, sweep_mask = 0;
	u32 sweep_expected = 0;
	u64 onset_ns = 0;
	int bit16_first = -1, first_clear = -1;
	const struct ps4_stobs_record *last = records ?
		&ps4_stobs_log[records - 1] : NULL;
	const struct ps4_data_sample *f = failing;
	const char *log_class = "STOBS_NO_RECORDS";
	u32 i;

	/* Completed outcomes only: the terminal record sample runs first,
	 * and the post-window indexed sweep runs only when that sample is
	 * valid; both finish before the first terminal report (host
	 * finding 1). Guard and fault terminals take no follow-up MMIO or
	 * sweeps: the saved deciding sample and the terminal reason are
	 * preserved and the run parks. A failed terminal core sample is
	 * itself an error observation: under the no-follow-up-after-error
	 * policy it prohibits the sweep and its selector stores, the
	 * outcome is reported STOBS_FINAL_GUARD with the core sample's own
	 * guard metadata, and no class change follows. The sweep's
	 * 8 CTX_INDEX stores stay after the observation window, declared
	 * as the post-window intervention.
	 * final_valid covers the core predicates only; terminal DATA0 and
	 * CONTEXT_ID allowances are not rechecked. The endpoint sweep has
	 * its own validity and failure result, separate from log_class.
	 */
	if (with_final) {
		final_valid = ps4_stobs_core_sample(adev, &final.core,
			PS4_TOKEN_SUBMITTED, pd_addr, cache_vmid, fault_base,
			reset_base);
		final.data = RREG32(mmUVD_GPCOM_VCPU_DATA0);
		if (final_valid) {
			sweep_valid = ps4_stobs_ctx_sweep(adev, &final.core);
			if (!sweep_valid) {
				sweep_kind = final.core.bad_kind;
				sweep_reg = final.core.bad_reg;
				sweep_value = final.core.bad_value;
				sweep_mask = final.core.bad_mask;
				sweep_expected = final.core.bad_expected;
				result = "STOBS_CTX_SWEEP_GUARD";
				error = -EIO;
			}
		}
		dev_info(adev->dev,
			"PS4_UVD_STOBS FINAL_CORE sampled=all_then_data0 status=%08x int=%08x rbc=%08x ib=%08x wp=%08x rp=%08x ctx=%08x data=%08x wpc=%08x rpa=%08x fault=%08x reset=%u in_reset=%u pd=%08x valid=%d\n",
			final.core.status, final.core.mastint, final.core.rbc,
			final.core.ib_size, final.core.wptr, final.core.rptr,
			final.core.context, final.data, final.core.wptr_cntl,
			final.core.rptr_addr, final.core.fault,
			final.core.reset_count, final.core.in_reset,
			final.core.pd, final_valid);
		if (!final_valid) {
			result = "STOBS_FINAL_GUARD";
			error = -EIO;
			dev_err(adev->dev,
				"PS4_UVD_STOBS FINAL_GUARD kind=%u reg=%08x value=%08x mask=%08x expected=%08x\n",
				final.core.bad_kind, final.core.bad_reg,
				final.core.bad_value, final.core.bad_mask,
				final.core.bad_expected);
		}
		if (final_valid)
			dev_info(adev->dev,
				"PS4_UVD_STOBS CTX_SWEEP valid=%d kind=%u reg=%08x value=%08x mask=%08x expected=%08x\n",
				sweep_valid, sweep_kind, sweep_reg, sweep_value,
				sweep_mask, sweep_expected);
	}
	if (failing) {
		if (f->core.failed)
			dev_err(adev->dev,
				"PS4_UVD_STOBS GUARD kind=%u reg=%08x value=%08x mask=%08x expected=%08x; saved deciding read\n",
				f->core.bad_kind, f->core.bad_reg, f->core.bad_value,
				f->core.bad_mask, f->core.bad_expected);
		dev_info(adev->dev,
			"PS4_UVD_STOBS GUARD_FULL sampled=all_then_data0 status=%08x int=%08x rbc=%08x ib=%08x wp=%08x rp=%08x ctx=%08x data=%08x wpc=%08x rpa=%08x fault=%08x reset=%u in_reset=%u pd=%08x valid=%d\n",
			f->core.status, f->core.mastint, f->core.rbc,
			f->core.ib_size, f->core.wptr, f->core.rptr,
			f->core.context, f->data, f->core.wptr_cntl,
			f->core.rptr_addr, f->core.fault,
			f->core.reset_count, f->core.in_reset, f->core.pd,
			!f->core.failed);
	}
	for (i = 0; i < records; ++i) {
		const struct ps4_stobs_record *r = &ps4_stobs_log[i];

		if (i < PS4_STOBS_FINE_SAMPLES)
			++fine;
		if (r->bit16) {
			if (bit16_first < 0) {
				bit16_first = (int)i;
				onset_ns = r->elapsed_ns;
				onset = r->sample.data == PS4_DATA_VALUE ||
					r->sample.core.context ==
					PS4_DATA_CONTEXT_VALUE ||
					(r->sample.core.rptr ==
					PS4_UVD_SUBMIT_DWORDS &&
					r->sample.core.wptr ==
					PS4_UVD_SUBMIT_DWORDS);
			}
			if (!prev)
				++sets;
		} else if (prev) {
			if (!clears)
				first_clear = (int)i;
			++clears;
		}
		prev = r->bit16;
		if (r->sample.data == PS4_DATA_VALUE ||
		    r->sample.core.context == PS4_DATA_CONTEXT_VALUE)
			markers = true;
		/* A value regression is one adjacent-record fresh-to-baseline
		 * transition: the previous record carried the fresh packet
		 * value and this record carries the planted baseline (host
		 * finding 2). Baseline-only runs have no edges; the counter
		 * never measures the length of a baseline run. Non-terminal
		 * by design, declared and counted (review B8).
		 */
		if (i && prev_data == PS4_DATA_VALUE &&
		    r->sample.data == PS4_DATA_BASELINE)
			++data_regressions;
		if (i && prev_context == PS4_DATA_CONTEXT_VALUE &&
		    r->sample.core.context == PS4_DATA_CONTEXT_BASELINE)
			++context_regressions;
		prev_data = r->sample.data;
		prev_context = r->sample.core.context;
		dev_info(adev->dev,
			"PS4_UVD_STOBS REC i=%u elapsed_us=%llu t_end_us=%llu status=%08x int=%08x rbc=%08x ib=%08x wp=%08x rp=%08x ctx=%08x data=%08x wpc=%08x rpa=%08x fault=%08x reset=%u in_reset=%u pd=%08x bit16=%d\n",
			i, (unsigned long long)div_u64(r->elapsed_ns, 1000),
			(unsigned long long)div_u64(r->elapsed_end_ns, 1000),
			r->sample.core.status, r->sample.core.mastint,
			r->sample.core.rbc, r->sample.core.ib_size,
			r->sample.core.wptr, r->sample.core.rptr,
			r->sample.core.context, r->sample.data,
			r->sample.core.wptr_cntl, r->sample.core.rptr_addr,
			r->sample.core.fault, r->sample.core.reset_count,
			r->sample.core.in_reset, r->sample.core.pd, r->bit16);
	}
	persisted = bit16_first >= 0 && !clears && sets == 1;
	/* Describe the saved records even when the terminal result is a
	 * failure. This pattern does not qualify incomplete guard coverage.
	 */
	if (submitted && records) {
		if (bit16_first < 0)
			log_class = markers ? "STOBS_BIT16_ABSENT_MARKERS" :
				"STOBS_BIT16_ABSENT_NO_MARKERS";
		else if (persisted)
			log_class = "STOBS_BIT16_PERSISTENT";
		else if (sets == 1)
			log_class = "STOBS_BIT16_TRANSIENT";
		else
			log_class = "STOBS_BIT16_TOGGLING";
	}
	/* Only completed valid outcomes change their terminal result. */
	if (result && error == 0 && submitted && records)
		result = log_class;
	dev_info(adev->dev,
		"PS4_UVD_STOBS END result=%s log_class=%s enabled=%d submitted=%d elapsed_ns=%llu publish_ns=%llu records=%u fine=%u bit16_first=%d onset_ns=%llu sets=%u bit16_clears=%u first_clear=%d markers=%d persistent=%d onset_with_values=%d data_regressions=%u context_regressions=%u accepted=0 last_wp=%u last_rp=%u last_data=%08x last_ctx=%08x final_valid=%d sweep_valid=%d; times include software/logging, sequential observations, retained, no acceptance/crediting, no decode/containment/quiescence claim\n",
		result, log_class, enabled, submitted, (unsigned long long)elapsed,
		(unsigned long long)ps4_stobs_publish_ns, records, fine,
		bit16_first, (unsigned long long)onset_ns, sets, clears,
		first_clear, markers, persisted, onset, data_regressions,
		context_regressions, last ? last->sample.core.wptr : 0,
		last ? last->sample.core.rptr : 0,
		last ? last->sample.data : 0,
		last ? last->sample.core.context : 0, final_valid,
		sweep_valid);
	ps4_ready_park(adev, result, error);
}

/* Observation of one identical EXP005 packet publication. Its deadline
 * anchor and pre-publication store sequence are retained. Added direct
 * reads, stop sites, longer enabled-dwell logging, before-sample deadline
 * priority and the endpoint selector sweep are declared changes. Values may
 * regress without stopping: fresh value disappearance is itself transition
 * evidence, unlike the crediting semantics it replaces. Pointer regression
 * remains a safety terminal.
 */
static __noreturn void ps4_stobs_run(struct amdgpu_device *adev,
	const struct amdgpu_ps4_uvd_ring *rb, u64 pd_addr, u32 cache_vmid,
	u32 fault_base, int reset_base)
{
	struct ps4_uvd_ring_plan plan;
	struct ps4_uvd_ring_request request;
	struct ps4_data_sample sample;
	u64 started, elapsed;
	u32 readback, last_rptr;
	bool valid, ok, bit16;

	ps4_stobs_record_count = 0;
	if (!ps4_stobs_inputs_valid(rb, pd_addr))
		ps4_stobs_stop(adev, "STOBS_DESCRIPTOR_OR_GATES", -EINVAL,
			NULL, false, false, 0, pd_addr, cache_vmid,
			fault_base, reset_base, false);
	if (!ps4_data_sample(adev, &sample, PS4_TOKEN_HELD, pd_addr,
			     cache_vmid, fault_base, reset_base))
		ps4_stobs_stop(adev, "STOBS_PRECONDITION", -EIO, &sample,
			false, false, 0, pd_addr, cache_vmid, fault_base,
			reset_base, false);
	if (sample.data == PS4_DATA_VALUE ||
	    sample.core.context == PS4_DATA_CONTEXT_VALUE)
		ps4_stobs_stop(adev, "STOBS_STALE_RESULT", -EBUSY, &sample,
			false, false, 0, pd_addr, cache_vmid, fault_base,
			reset_base, false);
	request = (struct ps4_uvd_ring_request) {
		.gpu_base = rb->private_va, .baseline = PS4_DATA_CONTEXT_BASELINE,
		.token = PS4_DATA_CONTEXT_VALUE,
		.context_before_baseline = sample.core.context,
		.initial_rptr_raw = sample.core.rptr,
		.initial_wptr_raw = sample.core.wptr,
	};
	if (ps4_uvd_build_ring(rb->cpu, rb->bytes, request, &plan) != PS4_UVD_PLAN_OK)
		ps4_stobs_stop(adev, "STOBS_PLAN_REJECTED", -EINVAL, &sample,
			false, false, 0, pd_addr, cache_vmid, fault_base,
			reset_base, false);
	/* Same page as EXP005: planner fill of count=0 pairs, then only the
	 * first two pairs replaced before any fetch. CRC equals cadc2d79.
	 */
	ps4_uvd_store_le32(rb->cpu, mmUVD_GPCOM_VCPU_DATA0);
	ps4_uvd_store_le32((u8 *)rb->cpu + 4, PS4_DATA_VALUE);
	ps4_uvd_store_le32((u8 *)rb->cpu + 8, mmUVD_CONTEXT_ID);
	ps4_uvd_store_le32((u8 *)rb->cpu + 12, PS4_DATA_CONTEXT_VALUE);
	dev_info(adev->dev,
		"PS4_UVD_STOBS PREPARED bytes=4096 submit_dw=16 va=%llx data_baseline=%08x data_value=%08x context_baseline=%08x context_value=%08x crc=%08x; identical EXP005 packet, observation only\n",
		rb->private_va, PS4_DATA_BASELINE, PS4_DATA_VALUE,
		PS4_DATA_CONTEXT_BASELINE, PS4_DATA_CONTEXT_VALUE,
		crc32_le(~0U, rb->cpu, rb->bytes) ^ ~0U);
	WREG32(mmUVD_GPCOM_VCPU_DATA0, PS4_DATA_BASELINE);
	readback = RREG32(mmUVD_GPCOM_VCPU_DATA0);
	if (readback != PS4_DATA_BASELINE) {
		dev_err(adev->dev,
			"PS4_UVD_STOBS BASELINE_DIRECT reg=%08x value=%08x expected=%08x; no CORE resample\n",
			mmUVD_GPCOM_VCPU_DATA0, readback, PS4_DATA_BASELINE);
		ps4_stobs_stop(adev, "STOBS_BASELINE_READBACK", -EIO, NULL,
			false, false, 0, pd_addr, cache_vmid, fault_base,
			reset_base, false);
	}
	WREG32(mmUVD_CONTEXT_ID, PS4_DATA_CONTEXT_BASELINE);
	readback = RREG32(mmUVD_CONTEXT_ID);
	if (readback != PS4_DATA_CONTEXT_BASELINE) {
		dev_err(adev->dev,
			"PS4_UVD_STOBS BASELINE_DIRECT reg=%08x value=%08x expected=%08x; no CORE resample\n",
			mmUVD_CONTEXT_ID, readback, PS4_DATA_CONTEXT_BASELINE);
		ps4_stobs_stop(adev, "STOBS_CONTEXT_BASELINE_READBACK", -EIO,
			NULL, false, false, 0, pd_addr, cache_vmid, fault_base,
			reset_base, false);
	}
	dev_info(adev->dev,
		"PS4_UVD_STOBS CPU_BASELINES observed data=%08x context=%08x; fetch remains inhibited\n",
		PS4_DATA_BASELINE, readback);
	mb();
	amdgpu_device_flush_hdp(adev, NULL);
	if (!ps4_data_sample(adev, &sample, PS4_TOKEN_HELD, pd_addr,
			     cache_vmid, fault_base, reset_base) ||
	    !ps4_data_baselines(&sample))
		ps4_stobs_stop(adev, "STOBS_BASELINE_OR_PUBLICATION_GUARD", -EIO,
			&sample, false, false, 0, pd_addr, cache_vmid,
			fault_base, reset_base, false);
	dev_info(adev->dev,
		"PS4_UVD_STOBS ENABLE_BEGIN held_baselines=data:%08x/context:%08x; empty ring only, next write clears NO_FETCH\n",
		sample.data, sample.core.context);
	started = ktime_get_ns();
	WREG32(mmUVD_RBC_RB_CNTL, PS4_TOKEN_RBC_FETCH);
	valid = ps4_data_sample(adev, &sample, PS4_TOKEN_EMPTY, pd_addr,
				cache_vmid, fault_base, reset_base);
	elapsed = ktime_get_ns() - started;
	if (elapsed >= PS4_STOBS_LIMIT_NS)
		ps4_stobs_stop(adev, "STOBS_ENABLE_TIMEOUT", -ETIMEDOUT,
			&sample, true, false, elapsed, pd_addr, cache_vmid,
			fault_base, reset_base, false);
	if (!valid && sample.core.bad_reg == mmUVD_STATUS &&
	    (sample.core.bad_value & ~(u32)UVD_STATUS__RBC_BUSY_MASK) ==
	    (PS4_READY_BIT | PS4_STOBS_BIT16))
		ps4_stobs_stop(adev, "STOBS_PREPUBLICATION_BIT16", -EIO,
			&sample, true, false, elapsed, pd_addr, cache_vmid,
			fault_base, reset_base, false);
	if (!valid || !ps4_data_baselines(&sample))
		ps4_stobs_stop(adev, "STOBS_ENABLE_GUARD", -EIO, &sample,
			true, false, elapsed, pd_addr, cache_vmid, fault_base,
			reset_base, false);
	/* Same enabled-dwell print family as EXP005. The PUBLICATION_BEGIN
	 * line is longer than EXP005's counters (review B7); the resulting
	 * dwell is recorded by publish_ns below rather than assumed.
	 */
	dev_info(adev->dev,
		"PS4_UVD_STOBS PUBLICATION_BEGIN empty_baselines=data:%08x/context:%08x elapsed_ns=%llu; next write sole WPTR16 after deadline check; SUBMITTED evidence carried by the record log\n",
		sample.data, sample.core.context, (unsigned long long)elapsed);
	mb();
	elapsed = ktime_get_ns() - started;
	if (elapsed >= PS4_STOBS_LIMIT_NS)
		ps4_stobs_stop(adev, "STOBS_BEFORE_SUBMIT_TIMEOUT", -ETIMEDOUT,
			&sample, true, false, elapsed, pd_addr, cache_vmid,
			fault_base, reset_base, false);
	/* Immediate pre-publication read (review B4): if bit16 is already
	 * set, stop without publishing. The strict EMPTY sampler performs
	 * direct reads only (F1); indexed values were last checked at ENABLE.
	 * No printk occurs between this read, the final deadline recheck and
	 * WPTR. publish_ns is a post-write timestamp, not a two-sided bracket.
	 */
	valid = ps4_stobs_core_sample(adev, &sample.core, PS4_TOKEN_EMPTY,
		pd_addr, cache_vmid, fault_base, reset_base);
	sample.data = RREG32(mmUVD_GPCOM_VCPU_DATA0);
	elapsed = ktime_get_ns() - started;
	if (!valid && sample.core.bad_reg == mmUVD_STATUS &&
	    (sample.core.bad_value & ~(u32)UVD_STATUS__RBC_BUSY_MASK) ==
	    (PS4_READY_BIT | PS4_STOBS_BIT16))
		ps4_stobs_stop(adev, "STOBS_PREPUBLICATION_BIT16", -EIO,
			&sample, true, false, elapsed, pd_addr, cache_vmid,
			fault_base, reset_base, false);
	if (!valid || !ps4_data_baselines(&sample))
		ps4_stobs_stop(adev, "STOBS_PREPUBLICATION_GUARD", -EIO,
			&sample, true, false, elapsed, pd_addr, cache_vmid,
			fault_base, reset_base, false);
	elapsed = ktime_get_ns() - started;
	if (elapsed >= PS4_STOBS_LIMIT_NS)
		ps4_stobs_stop(adev, "STOBS_BEFORE_SUBMIT_TIMEOUT", -ETIMEDOUT,
			&sample, true, false, elapsed, pd_addr, cache_vmid,
			fault_base, reset_base, false);
	WREG32(mmUVD_RBC_RB_WPTR, plan.publish_wptr_raw);
	ps4_stobs_publish_ns = ktime_get_ns() - started;
	last_rptr = 0;
	for (;;) {
		u64 t_begin, t_end;

		t_begin = ktime_get_ns() - started;
		/* Unlike EXP005, check the deadline before starting a sample.
		 * A sample spanning the deadline retains its own guard verdict.
		 */
		if (t_begin >= PS4_STOBS_LIMIT_NS)
			ps4_stobs_stop(adev, "STOBS_OBSERVATION_COMPLETED", 0,
				NULL, true, true, t_begin, pd_addr, cache_vmid,
				fault_base, reset_base, true);
		ok = ps4_stobs_monitor(adev, &sample, pd_addr, cache_vmid,
			fault_base, reset_base, &bit16);
		t_end = ktime_get_ns() - started;
		if (!ok) {
			const char *guard;

			if (sample.core.bad_reg == mmUVD_GPCOM_VCPU_DATA0 &&
			    sample.core.bad_mask == ~0U &&
			    sample.core.bad_expected == PS4_DATA_VALUE)
				guard = "STOBS_UNEXPECTED_VALUE";
			else if (sample.core.bad_reg == mmUVD_CONTEXT_ID &&
				 sample.core.bad_mask == ~0U &&
				 sample.core.bad_expected ==
				 PS4_DATA_CONTEXT_VALUE)
				guard = "STOBS_UNEXPECTED_CONTEXT";
			else if (sample.core.bad_reg == mmUVD_STATUS &&
				 sample.core.bad_mask ==
				 ~(u32)UVD_STATUS__RBC_BUSY_MASK)
				guard = "STOBS_STATUS_UNEXPECTED";
			else
				guard = "STOBS_CORE_GUARD";
			ps4_stobs_stop(adev, guard, -EIO, &sample, true, true,
				t_end, pd_addr, cache_vmid, fault_base,
				reset_base, false);
		}
		if (ps4_stobs_record_count >= PS4_STOBS_RECORDS)
			ps4_stobs_stop(adev, "STOBS_RECORD_OVERFLOW", -EINVAL,
				NULL, true, true, t_end, pd_addr, cache_vmid,
				fault_base, reset_base, false);
		ps4_stobs_log[ps4_stobs_record_count++] =
			(struct ps4_stobs_record) { .elapsed_ns = t_begin,
				.elapsed_end_ns = t_end, .sample = sample,
				.bit16 = bit16 };
		if (sample.core.rptr < last_rptr)
			ps4_stobs_stop(adev, "STOBS_POINTER_REGRESSION", -EIO,
				&sample, true, true, t_end, pd_addr,
				cache_vmid, fault_base, reset_base, false);
		last_rptr = sample.core.rptr;
		if (ps4_stobs_record_count == PS4_STOBS_RECORDS)
			ps4_stobs_stop(adev, "STOBS_OBSERVATION_COMPLETED", 0,
				NULL, true, true, t_end, pd_addr, cache_vmid,
				fault_base, reset_base, true);
		if (ps4_stobs_record_count < PS4_STOBS_FINE_SAMPLES)
			usleep_range(PS4_STOBS_FINE_SLEEP_LOW,
				     PS4_STOBS_FINE_SLEEP_HIGH);
		else
			usleep_range(PS4_STOBS_COARSE_SLEEP_LOW,
				     PS4_STOBS_COARSE_SLEEP_HIGH);
	}
}
/* STATUS_OBS_FLOW_END */
'''

EARLY = """	/* STATUS_OBS_EARLY_BEGIN: before firmware and all readiness setup. */
	if (ps4_uvd_status_observation && ps4_uvd_data_control)
		ps4_ready_park(adev, "STOBS_OPTIONS_EXCLUSIVE", -EINVAL);
	if (ps4_uvd_status_observation && !ps4_stobs_inputs_valid(rb, pd_addr))
		ps4_ready_park(adev, "STOBS_DESCRIPTOR_OR_GATES", -EINVAL);
	/* STATUS_OBS_EARLY_END */
"""

DISPATCH = """				/* STATUS_OBS_DISPATCH_BEGIN */
				if (ps4_uvd_status_observation)
					ps4_stobs_run(adev, rb, pd_addr, cache_vmid_expected,
						      baseline_fault, reset_count);
				/* STATUS_OBS_DISPATCH_END */
"""


def main():
    old = BASE.read_text()
    anchors = {
        "param": """/* Separate default-off immutable main-ring DATA0 readback control. */
static bool ps4_uvd_data_control;
module_param_named(ps4_uvd_data_control, ps4_uvd_data_control, bool, 0444);
MODULE_PARM_DESC(ps4_uvd_data_control,
	"One PS4 UVD DATA0 readback control; requires token+ready+poll_state, always parks (default 0)");

""",
        "flow": "/* DATA_CONTROL_FLOW_END */\n",
        "early": "\t/* DATA_CONTROL_EARLY_END */\n",
        "dispatch": "\n\t\t\t\t/* DATA_CONTROL_DISPATCH_END */\n",
    }
    for name, text in anchors.items():
        assert old.count(text) == 1, f"anchor not unique: {name}"
    new = old.replace(anchors["param"], anchors["param"] + PARAMETER, 1)
    new = new.replace(anchors["flow"], anchors["flow"] + FLOW, 1)
    new = new.replace(anchors["early"], anchors["early"] + EARLY, 1)
    new = new.replace(anchors["dispatch"], anchors["dispatch"] + DISPATCH, 1)

    # Normalization: removing the four additive blocks must return the base.
    norm = new.replace(PARAMETER, "", 1)
    norm = norm.replace(FLOW, "", 1)
    norm = norm.replace(EARLY, "", 1)
    norm = norm.replace(DISPATCH, "", 1)
    assert norm == old, "normalization does not reproduce the base"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(new)
    (DIFFDIR / "ready_base.c").write_text(old)
    (DIFFDIR / "ready_new.c").write_text(new)
    print("candidate written:", OUT)


if __name__ == "__main__":
    main()
