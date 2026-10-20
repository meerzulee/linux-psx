/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Actual-source controller/wrapper differential fixture. Hardware/firmware/
 * clock and retained diagnostic helpers are synthetic call boundaries, not
 * emulation. The checker separately proves their source bytes are unchanged.
 */
#include <errno.h>
#include <setjmp.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef int atomic_t;
#define __noreturn _Noreturn
#define NSEC_PER_SEC 1000000000ULL
#define AMDGPU_GPU_PAGE_SIZE 4096U
#define CONFIG_DRM_AMDGPU 1
#define CONFIG_X86_PS4 1
static bool builtin = true, ps4_config = true;
#define IS_BUILTIN(x) builtin
#define IS_ENABLED(x) ps4_config
#define UVD_SOFT_RESET__RBC_SOFT_RESET_MASK 1U
#define UVD_SOFT_RESET__LMI_SOFT_RESET_MASK 2U
#define UVD_SOFT_RESET__VCPU_SOFT_RESET_MASK 4U
#define UVD_SOFT_RESET__LMI_UMC_SOFT_RESET_MASK 8U
#define UVD_LMI_CTRL2__STALL_ARB_UMC_MASK 1U
#define UVD_RBC_RB_CNTL__RB_NO_FETCH_MASK 0x10000U
#define UVD_MASTINT_EN__VCPU_EN_MASK 2U
#define UVD_MASTINT_EN__SYS_EN_MASK 1U
#define PS4_GUARD_INT_REGRESSION (1U << 8)
enum { CHIP_LIVERPOOL, CHIP_GLADIUS, CHIP_OTHER };
/* Synthetic register identifiers shared by both extracted controllers. */
enum {
	mmVM_CONTEXT1_PROTECTION_FAULT_STATUS = 1,
	mmVM_CONTEXT1_PROTECTION_FAULT_ADDR,
	mmUVD_STATUS, mmUVD_SOFT_RESET, mmUVD_LMI_CTRL2,
	mmUVD_RBC_RB_CNTL, mmUVD_MASTINT_EN
};
/* @SOURCE_CONSTANTS@ */
struct amdgpu_ps4_uvd_ring { void *cpu; size_t bytes; u64 private_va; };
/* @SOURCE_STATE@ */
struct firmware { const u8 *data; size_t size; };
struct amdgpu_device {
	struct amdgpu_ps4_uvd_ready_operation ps4_uvd_ready_operation;
	struct { struct { u32 gb_addr_config; } config; } gfx;
	struct { bool buffer_funcs_enabled; } mman;
	bool accel_working;
	atomic_t gpu_reset_counter;
	unsigned int asic_type;
	void *dev;
};

#define CHECK(condition, witness) do { if (!(condition)) { \
	fprintf(stderr, "FAIL:%s line=%d\n", witness, __LINE__); exit(2); } } while (0)
static unsigned int checks;
static bool ps4_uvd_ready, ps4_uvd_poll_state, ps4_uvd_token;
static bool ps4_uvd_data_control, ps4_uvd_status_observation, ps4_uvd_ib_data;
static atomic_t ps4_ready_attempted;
static const struct firmware *ps4_ready_firmware;
static void *ps4_ready_retained_cpu;
static u64 ps4_ready_retained_pd;
static u8 firmware_bytes[PS4_READY_FW_BYTES], backing[PS4_READY_BACKING_BYTES];
static u8 baseline_backing[PS4_READY_BACKING_BYTES];
static const struct firmware fw = { firmware_bytes, sizeof(firmware_bytes) };
static struct amdgpu_device device, second_device;
static struct amdgpu_ps4_uvd_ring ring = { backing, 4096, 0x300308000ULL };
static struct amdgpu_ps4_uvd_ring ib = { backing, 0x20000, 0x30030a000ULL };
static const u64 pd = 0x12345000ULL;
enum phase { PRE, HELD, RBC, RUN };
static enum phase phase;
static u32 regs[16];
static bool candidate_mode;
static int poll_samples;
static u64 now;
static char trace[262144], old_trace[262144], final_reason[80];
static size_t trace_used;
static int final_error, park_count, dispatch_count;
static jmp_buf terminal;

enum scenario {
	GATE_OFF, NOT_BUILTIN, NOT_PS4, WRONG_ASIC, REENTRY,
	IB_OPTIONS_DATA, IB_OPTIONS_STOBS, IB_INPUT, DATA_INPUT, STOBS_EXCLUSIVE,
	STOBS_INPUT, TOKEN_NO_POLL, TOKEN_INPUT,
	NULL_CPU, BAD_SIZE, ZERO_PD, UNALIGNED_PD, BAD_GB, BUFFER_ACTIVE,
	ACCEL_ACTIVE, PREFLIGHT_RESET,
	CACHE_PREFLIGHT, CLOCK_PREFLIGHT, FIRMWARE_REQUEST, FIRMWARE_VALIDATE,
	BASE_FAULT, BASE_RESET, BASE_READY, SETUP_ERROR,
	HELD_PROGRAMMED, HELD_FETCH, HELD_FAULT, HELD_RESET_COUNT, HELD_IN_RESET,
	HELD_STATUS, HELD_RESET_BITS, HELD_STALL, HELD_RBC, HELD_INTERRUPT,
	RBC_RESET_BITS, RBC_STALL, RBC_PROGRAMMED, RBC_FETCH, RBC_STATUS,
	RBC_FAULT, RBC_RESET_COUNT, RBC_IN_RESET,
	POLL_FAULT, POLL_RESET, POLL_RESET_COUNT, POLL_CACHE, POLL_FETCH,
	POLL_REGRESSION, TIMEOUT_EQUAL, TIMEOUT_READY, DELAYED_READY,
	READY_PLAIN, READY_CONVENTION, DISPATCH_TOKEN, DISPATCH_DATA,
	DISPATCH_STOBS, DISPATCH_IB, SLOW_REQUEST, SLOW_CLOCK, GLADIUS,
	SCENARIO_COUNT
};
static enum scenario scenario;
struct case_spec { const char *name, *reason; int error; };
static const struct case_spec cases[SCENARIO_COUNT] = {
	[GATE_OFF] = {"gate-off", "GATE_OR_REENTRY", -EPERM},
	[NOT_BUILTIN] = {"not-builtin", "GATE_OR_REENTRY", -EPERM},
	[NOT_PS4] = {"not-ps4", "GATE_OR_REENTRY", -EPERM},
	[WRONG_ASIC] = {"wrong-asic", "GATE_OR_REENTRY", -EPERM},
	[REENTRY] = {"global-reentry", "GATE_OR_REENTRY", -EPERM},
	[IB_OPTIONS_DATA] = {"ib-data-exclusive", "IBDATA_OPTIONS_EXCLUSIVE", -EINVAL},
	[IB_OPTIONS_STOBS] = {"ib-stobs-exclusive", "IBDATA_OPTIONS_EXCLUSIVE", -EINVAL},
	[IB_INPUT] = {"ib-input", "IBDATA_DESCRIPTOR_OR_GATES", -EINVAL},
	[DATA_INPUT] = {"data-input", "DATA_DESCRIPTOR_OR_GATES", -EINVAL},
	[STOBS_EXCLUSIVE] = {"stobs-exclusive", "STOBS_OPTIONS_EXCLUSIVE", -EINVAL},
	[STOBS_INPUT] = {"stobs-input", "STOBS_DESCRIPTOR_OR_GATES", -EINVAL},
	[TOKEN_NO_POLL] = {"token-no-poll", "TOKEN_DESCRIPTOR_OR_GATES", -EINVAL},
	[TOKEN_INPUT] = {"token-input", "TOKEN_DESCRIPTOR_OR_GATES", -EINVAL},
	[NULL_CPU] = {"null-cpu", "PREFLIGHT", -EINVAL},
	[BAD_SIZE] = {"bad-size", "PREFLIGHT", -EINVAL},
	[ZERO_PD] = {"zero-pd", "PREFLIGHT", -EINVAL},
	[UNALIGNED_PD] = {"unaligned-pd", "PREFLIGHT", -EINVAL},
	[BAD_GB] = {"bad-gb", "PREFLIGHT", -EINVAL},
	[BUFFER_ACTIVE] = {"buffer-active", "PREFLIGHT", -EINVAL},
	[ACCEL_ACTIVE] = {"accel-active", "PREFLIGHT", -EINVAL},
	[PREFLIGHT_RESET] = {"preflight-reset", "PREFLIGHT", -EINVAL},
	[CACHE_PREFLIGHT] = {"cache-preflight", "VCPU_CACHE_VMID_PREFLIGHT", -EINVAL},
	[CLOCK_PREFLIGHT] = {"clock-preflight", "ATOM_PREFLIGHT", -EINVAL},
	[FIRMWARE_REQUEST] = {"firmware-request", "FIRMWARE_REQUEST", -ENOENT},
	[FIRMWARE_VALIDATE] = {"firmware-header", "FIRMWARE_HEADER_CRC", -EBADMSG},
	[BASE_FAULT] = {"base-fault", "BASELINE_NOT_CLEAN", -EBUSY},
	[BASE_RESET] = {"base-reset", "BASELINE_NOT_CLEAN", -EBUSY},
	[BASE_READY] = {"base-ready", "BASELINE_NOT_CLEAN", -EBUSY},
	[SETUP_ERROR] = {"setup-error", "INITIAL_SETUP_PARK", -EIO},
	[HELD_PROGRAMMED] = {"held-programmed", "HELD_BASELINE", -EINVAL},
	[HELD_FETCH] = {"held-fetch", "HELD_BASELINE", -EINVAL},
	[HELD_FAULT] = {"held-fault", "HELD_BASELINE", -EINVAL},
	[HELD_RESET_COUNT] = {"held-reset-count", "HELD_BASELINE", -EINVAL},
	[HELD_IN_RESET] = {"held-in-reset", "HELD_BASELINE", -EINVAL},
	[HELD_STATUS] = {"held-status", "HELD_BASELINE", -EINVAL},
	[HELD_RESET_BITS] = {"held-reset-bits", "HELD_BASELINE", -EINVAL},
	[HELD_STALL] = {"held-stall", "HELD_BASELINE", -EINVAL},
	[HELD_RBC] = {"held-rbc", "HELD_BASELINE", -EINVAL},
	[HELD_INTERRUPT] = {"held-interrupt", "HELD_BASELINE", -EINVAL},
	[RBC_RESET_BITS] = {"rbc-reset-bits", "RBC_RELEASE_GUARD", -EIO},
	[RBC_STALL] = {"rbc-stall", "RBC_RELEASE_GUARD", -EIO},
	[RBC_PROGRAMMED] = {"rbc-programmed", "RBC_RELEASE_GUARD", -EIO},
	[RBC_FETCH] = {"rbc-fetch", "RBC_RELEASE_GUARD", -EIO},
	[RBC_STATUS] = {"rbc-status", "RBC_RELEASE_GUARD", -EIO},
	[RBC_FAULT] = {"rbc-fault", "RBC_RELEASE_GUARD", -EIO},
	[RBC_RESET_COUNT] = {"rbc-reset-count", "RBC_RELEASE_GUARD", -EIO},
	[RBC_IN_RESET] = {"rbc-in-reset", "RBC_RELEASE_GUARD", -EIO},
	[POLL_FAULT] = {"poll-fault", "FAULT_OR_RESET", -EIO},
	[POLL_RESET] = {"poll-reset", "FAULT_OR_RESET", -EIO},
	[POLL_RESET_COUNT] = {"poll-reset-count", "FAULT_OR_RESET", -EIO},
	[POLL_CACHE] = {"poll-cache", "VCPU_CACHE_VMID_CHANGED", -EIO},
	[POLL_FETCH] = {"poll-fetch", "FETCH_GUARD_CHANGED", -EIO},
	[POLL_REGRESSION] = {"poll-regression", "POLL_STATE_REGRESSION", -EIO},
	[TIMEOUT_EQUAL] = {"timeout-equal", "READY_TIMEOUT", -ETIMEDOUT},
	[TIMEOUT_READY] = {"ready-at-deadline", "READY_TIMEOUT", -ETIMEDOUT},
	[DELAYED_READY] = {"delayed-ready", "READY_OBSERVED", 0},
	[READY_PLAIN] = {"ready-plain", "READY_OBSERVED", 0},
	[READY_CONVENTION] = {"ready-convention", "READY_CONVENTION_OBSERVED", 0},
	[DISPATCH_TOKEN] = {"dispatch-token", "DIAGNOSTIC_TOKEN", 0},
	[DISPATCH_DATA] = {"dispatch-data", "DIAGNOSTIC_DATA", 0},
	[DISPATCH_STOBS] = {"dispatch-stobs", "DIAGNOSTIC_STOBS", 0},
	[DISPATCH_IB] = {"dispatch-ib", "DIAGNOSTIC_IB", 0},
	[SLOW_REQUEST] = {"slow-firmware", "READY_OBSERVED", 0},
	[SLOW_CLOCK] = {"slow-clock", "READY_OBSERVED", 0},
	[GLADIUS] = {"gladius", "READY_OBSERVED", 0},
};

static void event(const char *format, ...)
{
	va_list args;
	int n;
	CHECK(trace_used < sizeof(trace), "TRACE_CAPACITY");
	va_start(args, format);
	n = vsnprintf(trace + trace_used, sizeof(trace) - trace_used, format, args);
	va_end(args);
	CHECK(n >= 0 && (size_t)n < sizeof(trace) - trace_used, "TRACE_CAPACITY");
	trace_used += (size_t)n;
}

static int atomic_xchg(atomic_t *a, int value)
{
	int old = *a;
	event("atomic_xchg:%d:%d\n", old, value);
	*a = value;
	return old;
}

static int atomic_read(atomic_t *a)
{
	int value = *a;
	if ((phase == PRE && scenario == BASE_RESET) ||
	    (phase == HELD && scenario == HELD_RESET_COUNT) ||
	    (phase == RBC && scenario == RBC_RESET_COUNT) ||
	    (phase == RUN && scenario == POLL_RESET_COUNT))
		value = 1;
	event("reset_counter:%d\n", value);
	return value;
}

static bool amdgpu_in_reset(struct amdgpu_device *adev)
{
	bool value = (phase == PRE && scenario == PREFLIGHT_RESET) ||
		(phase == HELD && scenario == HELD_IN_RESET) ||
		(phase == RBC && scenario == RBC_IN_RESET) ||
		(phase == RUN && scenario == POLL_RESET);
	CHECK(adev != NULL, "DEVICE_REQUIRED");
	event("in_reset:%d\n", value);
	return value;
}

static u32 read_reg(unsigned int reg)
{
	u32 value;
	if (reg == PS4_READY_UVD_VCPU_CACHE_VMID) {
		value = scenario == POLL_CACHE ? 14 : 4;
	} else {
		CHECK(reg < 16, "REGISTER_MODEL_BOUND");
		value = regs[reg];
	}
	if (reg == mmVM_CONTEXT1_PROTECTION_FAULT_STATUS) {
		if (phase == RUN)
			++poll_samples;
		if ((phase == PRE && scenario == BASE_FAULT) ||
		    (phase == HELD && scenario == HELD_FAULT) ||
		    (phase == RBC && scenario == RBC_FAULT) ||
		    (phase == RUN && scenario == POLL_FAULT))
			value = 0x1c03b002U;
	}
	if (reg == mmUVD_STATUS) {
		value = ((phase == PRE && scenario == BASE_READY) ||
			 (phase == HELD && scenario == HELD_STATUS) ||
			 (phase == RBC && scenario == RBC_STATUS) ||
			 (phase == RUN && scenario != TIMEOUT_EQUAL &&
			  !(scenario == POLL_REGRESSION && poll_samples == 1) &&
			  !(scenario == DELAYED_READY && poll_samples < 3))) ?
			PS4_READY_BIT : 0;
	}
	if ((reg == mmUVD_SOFT_RESET &&
	     ((phase == HELD && scenario == HELD_RESET_BITS) ||
	      (phase == RBC && scenario == RBC_RESET_BITS))) ||
	    (reg == mmUVD_LMI_CTRL2 &&
	     ((phase == HELD && scenario == HELD_STALL) ||
	      (phase == RBC && scenario == RBC_STALL))) ||
	    (reg == mmUVD_RBC_RB_CNTL && phase == HELD && scenario == HELD_RBC))
		value = 0;
	if (reg == mmUVD_MASTINT_EN && phase == HELD && scenario == HELD_INTERRUPT)
		value = UVD_MASTINT_EN__SYS_EN_MASK;
	event("read:%u:%08x\n", reg, value);
	return value;
}

static void write_reg(unsigned int reg, u32 value)
{
	CHECK(reg < 16, "REGISTER_MODEL_BOUND");
	if (candidate_mode)
		CHECK(device.ps4_uvd_ready_operation.release_entered, "RELEASE_ENTRY");
	event("write:%u:%08x\n", reg, value);
	regs[reg] = value;
	if (phase == HELD && reg == mmUVD_SOFT_RESET)
		phase = RBC;
	if (reg == mmUVD_SOFT_RESET && !(value & PS4_READY_HOLD))
		phase = RUN;
}

static void log_message(const char *format, ...)
{
	va_list args;
	char text[2048];
	va_start(args, format);
	int n = vsnprintf(text, sizeof(text), format, args);
	va_end(args);
	CHECK(n >= 0 && (size_t)n < sizeof(text), "LOG_CAPACITY");
	event("log:%s", text);
}

static void ps4_ready_snapshot(struct amdgpu_device *adev, const char *stage)
{
	if (candidate_mode)
		CHECK(adev->ps4_uvd_ready_operation.indexed_snapshot_entered, "INDEXED_ENTRY");
	event("snapshot:%s\n", stage);
}

static void ps4_ready_guard_log(struct amdgpu_device *adev, const char *stage,
			       const struct ps4_ready_guard_sample *guard)
{
	(void)adev;
	event("guard:%s:%u:%u:%u\n", stage, guard->sampled, guard->failed, guard->mastint);
}

static int ps4_ready_cache_vmid_preflight(struct amdgpu_device *adev, u32 *original)
{
	if (candidate_mode)
		CHECK(adev->ps4_uvd_ready_operation.first_mmio_entered, "FIRST_MMIO_ENTRY");
	event("cache_preflight\n");
	*original = 0xabcd000eU;
	return scenario == CACHE_PREFLIGHT ? -EINVAL : 0;
}

static u32 ps4_ready_cache_vmid_owned(u32 original)
{
	event("cache_owned:%08x\n", original);
	/* The synthetic poll register returns this value; upper-bit preservation
	 * belongs to the unchanged helper, whose source is checked separately. */
	return original & 0xfU ? 4U : 0U;
}

static int ps4_ready_clock_preflight(struct amdgpu_device *adev)
{
	(void)adev;
	event("clock_preflight\n");
	if (scenario == SLOW_CLOCK)
		now += 4ULL * NSEC_PER_SEC;
	return scenario == CLOCK_PREFLIGHT ? -EINVAL : 0;
}

static int request_firmware_direct(const struct firmware **out, const char *name, void *dev)
{
	(void)dev;
	event("request:%s\n", name);
	if (scenario == FIRMWARE_REQUEST)
		return -ENOENT;
	if (scenario == SLOW_REQUEST)
		now += 5ULL * NSEC_PER_SEC;
	*out = &fw;
	return 0;
}

static int ps4_ready_validate_firmware(const u8 *data, size_t size, u32 *crc)
{
	CHECK(data == firmware_bytes && size == sizeof(firmware_bytes), "FIRMWARE_INPUT");
	event("validate_firmware\n");
	if (scenario == FIRMWARE_VALIDATE)
		return -EBADMSG;
	*crc = PS4_READY_PAYLOAD_CRC;
	return 0;
}

static void amdgpu_device_flush_hdp(struct amdgpu_device *adev, void *ring_ptr)
{
	CHECK(ring_ptr == NULL, "HDP_ARGUMENT");
	if (candidate_mode)
		CHECK(adev->ps4_uvd_ready_operation.hdp_flush_entered, "HDP_ENTRY");
	event("flush_hdp\n");
}

static void amdgpu_ps4_uvd_irq_arm(struct amdgpu_device *adev)
{
	if (candidate_mode)
		CHECK(adev->ps4_uvd_ready_operation.irq_arm_entered, "IRQ_ENTRY");
	event("irq_arm\n");
}

static int ps4_ready_setup_held(struct amdgpu_device *adev, u32 original)
{
	if (candidate_mode)
		CHECK(adev->ps4_uvd_ready_operation.setup_entered, "SETUP_ENTRY");
	event("setup_held:%08x\n", original);
	phase = HELD;
	regs[mmUVD_SOFT_RESET] = PS4_READY_HOLD;
	regs[mmUVD_LMI_CTRL2] = UVD_LMI_CTRL2__STALL_ARB_UMC_MASK;
	regs[mmUVD_RBC_RB_CNTL] = UVD_RBC_RB_CNTL__RB_NO_FETCH_MASK;
	return scenario == SETUP_ERROR ? -EIO : 0;
}

static bool ps4_ready_programmed(struct amdgpu_device *adev, u32 expected)
{
	(void)adev;
	event("programmed:%u\n", expected);
	return !((phase == HELD && scenario == HELD_PROGRAMMED) ||
		 (phase == RBC && scenario == RBC_PROGRAMMED));
}

static bool ps4_ready_fetch_guard_sample(struct amdgpu_device *adev,
					struct ps4_ready_guard_sample *guard,
					bool permit_vcpu)
{
	(void)adev;
	bool bad = (phase == HELD && scenario == HELD_FETCH) ||
		   (phase == RBC && scenario == RBC_FETCH) ||
		   (phase == RUN && scenario == POLL_FETCH);
	memset(guard, 0, sizeof(*guard));
	guard->sampled = 31;
	guard->failed = bad ? 1U : 0U;
	guard->mastint = permit_vcpu &&
		!(scenario == POLL_REGRESSION && poll_samples > 1) ? 2U : 0U;
	event("fetch_guard:%d:%d:%u\n", phase, permit_vcpu, guard->mastint);
	return !bad;
}

static bool ps4_ib_data_inputs_valid(const struct amdgpu_ps4_uvd_ring *rb,
				    const struct amdgpu_ps4_uvd_ring *page, u64 addr)
{
	event("ib_inputs:%d:%d:%llx\n", rb == &ring, page == &ib, addr);
	return scenario != IB_INPUT;
}

static bool ps4_data_control_inputs_valid(const struct amdgpu_ps4_uvd_ring *rb, u64 addr)
{
	event("data_inputs:%d:%llx\n", rb == &ring, addr);
	return scenario != DATA_INPUT;
}

static bool ps4_stobs_inputs_valid(const struct amdgpu_ps4_uvd_ring *rb, u64 addr)
{
	event("stobs_inputs:%d:%llx\n", rb == &ring, addr);
	return scenario != STOBS_INPUT;
}

static bool ps4_token_descriptor_valid(const struct amdgpu_ps4_uvd_ring *rb)
{
	event("token_inputs:%d\n", rb == &ring);
	return scenario != TOKEN_INPUT;
}

static __noreturn void ps4_ready_park(struct amdgpu_device *adev, const char *reason, int error)
{
	(void)adev;
	++park_count;
	snprintf(final_reason, sizeof(final_reason), "%s", reason);
	final_error = error;
	event("park:%s:%d\n", reason, error);
	longjmp(terminal, 1);
}

static __noreturn void dispatch(struct amdgpu_device *adev,
			       const struct amdgpu_ps4_uvd_ring *rb,
			       const struct amdgpu_ps4_uvd_ring *page,
			       u64 addr, u32 cache, u32 fault, int resets,
			       const char *reason)
{
	++dispatch_count;
	event("dispatch:%s:%d:%d:%llx:%u:%u:%d\n", reason, rb == &ring,
	      page == &ib, addr, cache, fault, resets);
	ps4_ready_park(adev, reason, 0);
}

static __noreturn void ps4_ib_data_run(struct amdgpu_device *adev,
	const struct amdgpu_ps4_uvd_ring *rb, const struct amdgpu_ps4_uvd_ring *page,
	u64 addr, u32 cache, u32 fault, int resets)
{
	dispatch(adev, rb, page, addr, cache, fault, resets, "DIAGNOSTIC_IB");
}
#define DIAGNOSTIC(name, result) \
static __noreturn void name(struct amdgpu_device *adev, \
	const struct amdgpu_ps4_uvd_ring *rb, u64 addr, u32 cache, u32 fault, int resets) \
{ dispatch(adev, rb, NULL, addr, cache, fault, resets, result); }
DIAGNOSTIC(ps4_token_run, "DIAGNOSTIC_TOKEN")
DIAGNOSTIC(ps4_data_control_run, "DIAGNOSTIC_DATA")
DIAGNOSTIC(ps4_stobs_run, "DIAGNOSTIC_STOBS")
#undef DIAGNOSTIC

static u64 ktime_get_ns(void) { event("time:%llu\n", now); return now; }
static u64 div_u64(u64 value, u32 by) { return value / by; }
static void msleep(unsigned int ms)
{
	event("msleep:%u\n", ms);
	now += ms * 1000000ULL;
	if (scenario == TIMEOUT_EQUAL || scenario == TIMEOUT_READY)
		now = PS4_READY_LIMIT_NS;
}
static void usleep_range(unsigned int low, unsigned int high)
{
	event("usleep:%u:%u\n", low, high);
	now += low * 1000ULL;
	CHECK(poll_samples < 4, "FINITE_SYNTHETIC_POLL");
}
static void *copy_backing(void *dest, const void *source, size_t n)
{
	event("memcpy:%zu\n", n);
	return memcpy(dest, source, n);
}
static void *fill_backing(void *dest, int value, size_t n)
{
	if (candidate_mode)
		CHECK(device.ps4_uvd_ready_operation.backing_write_entered, "BACKING_ENTRY");
	event("memset:%d:%zu\n", value, n);
	return memset(dest, value, n);
}
#define RREG32(reg) read_reg(reg)
#define WREG32(reg, value) write_reg(reg, value)
#define dev_err(dev, ...) log_message(__VA_ARGS__)
#define dev_info(dev, ...) log_message(__VA_ARGS__)
#define dev_warn(dev, ...) log_message(__VA_ARGS__)
#define mb() event("mb\n")
/* Darwin's libc may define fortified function-like wrappers. Replace them
 * only at the extracted-controller boundary; keep -Werror unchanged. */
#undef memset
#undef memcpy
#define memset fill_backing
#define memcpy copy_backing
/* @SOURCE_ENABLED@ */
/* @SOURCE_BASELINE_ENTRY@ */
/* @SOURCE_CORE@ */
/* @SOURCE_WRAPPER@ */
#undef memset
#undef memcpy

static void configure(enum scenario which, bool is_candidate)
{
	scenario = which;
	candidate_mode = is_candidate;
	memset(&device, 0, sizeof(device));
	device.gfx.config.gb_addr_config = which == BAD_GB ? 0 : 0x02011003U;
	device.mman.buffer_funcs_enabled = which == BUFFER_ACTIVE;
	device.accel_working = which == ACCEL_ACTIVE;
	device.asic_type = which == WRONG_ASIC ? CHIP_OTHER :
		which == GLADIUS ? CHIP_GLADIUS : CHIP_LIVERPOOL;
	builtin = which != NOT_BUILTIN;
	ps4_config = which != NOT_PS4;
	ps4_uvd_ready = which != GATE_OFF;
	ps4_uvd_poll_state = which == POLL_REGRESSION || which == READY_CONVENTION ||
		(which >= DISPATCH_TOKEN && which <= DISPATCH_IB);
	ps4_uvd_token = which == TOKEN_NO_POLL || which == TOKEN_INPUT ||
		(which >= DISPATCH_TOKEN && which <= DISPATCH_IB);
	ps4_uvd_data_control = which == IB_OPTIONS_DATA || which == DATA_INPUT ||
		which == STOBS_EXCLUSIVE || which == DISPATCH_DATA;
	ps4_uvd_status_observation = which == IB_OPTIONS_STOBS ||
		which == STOBS_EXCLUSIVE || which == STOBS_INPUT || which == DISPATCH_STOBS;
	ps4_uvd_ib_data = which == IB_OPTIONS_DATA || which == IB_OPTIONS_STOBS ||
		which == IB_INPUT || which == DISPATCH_IB;
	if (which == TOKEN_INPUT)
		ps4_uvd_poll_state = true;
	ps4_ready_attempted = which == REENTRY ? 1 : 0;
	ps4_ready_firmware = NULL;
	ps4_ready_retained_cpu = NULL;
	ps4_ready_retained_pd = 0;
	memset(backing, 0x77, sizeof(backing));
	memset(regs, 0, sizeof(regs));
	phase = PRE;
	poll_samples = park_count = dispatch_count = 0;
	now = 0;
	trace_used = 0;
	trace[0] = final_reason[0] = '\0';
	final_error = 999;
}

static void call_wrapper(bool is_candidate, struct amdgpu_device *adev)
{
	if (setjmp(terminal) == 0) {
		/* Inputs exist only on the initial return. The simulated park
		 * longjmps out; no automatic input is used across that boundary. */
		void *cpu = scenario == NULL_CPU ? NULL : backing;
		size_t bytes = scenario == BAD_SIZE ? sizeof(backing) - 1 : sizeof(backing);
		u64 address = scenario == ZERO_PD ? 0 : scenario == UNALIGNED_PD ? pd + 1 : pd;

		if (is_candidate)
			amdgpu_ps4_uvd_ready_run(adev, cpu, bytes, address, &ring, &ib);
		else
			frozen_ready_run(adev, cpu, bytes, address, &ring, &ib);
		CHECK(false, "WRAPPER_MUST_PARK");
	}
}

static void check_operation(enum scenario which)
{
	struct amdgpu_ps4_uvd_ready_operation *op = &device.ps4_uvd_ready_operation;
	bool core_entered = which >= NULL_CPU;
	CHECK(op->boot_claimed == (which > REENTRY), "BOOT_CLAIM_STATE");
	CHECK(op->entered == core_entered && op->completed == core_entered, "TERMINAL_STATE");
	if (!core_entered) {
		CHECK(op->result.terminal == PS4_READY_CORE_NONE &&
		      !op->firmware && !op->retained_cpu && !op->retained_pd &&
		      !op->first_mmio_entered, "EARLY_FAILURE_RETENTION");
		return;
	}
	CHECK(op->retained_cpu == (which == NULL_CPU ? NULL : backing), "BORROWED_CPU");
	CHECK(op->retained_pd == (which == ZERO_PD ? 0 : which == UNALIGNED_PD ? pd + 1 : pd),
	      "BORROWED_PD");
	CHECK(op->backing_size == (which == BAD_SIZE ? sizeof(backing) - 1 : sizeof(backing)),
	      "BORROWED_SIZE");
	CHECK(op->firmware == (which >= FIRMWARE_VALIDATE ? &fw : NULL), "FIRMWARE_RETENTION");
	CHECK(op->first_mmio_entered == (which >= CACHE_PREFLIGHT), "FIRST_MMIO_ENTRY");
	CHECK(op->backing_write_entered == (which >= BASE_FAULT), "BACKING_ENTRY");
	CHECK(op->hdp_flush_entered == (which >= BASE_FAULT), "HDP_ENTRY");
	CHECK(op->irq_arm_entered == (which >= SETUP_ERROR), "IRQ_ENTRY");
	CHECK(op->indexed_snapshot_entered == (which >= SETUP_ERROR), "INDEXED_ENTRY");
	CHECK(op->setup_entered == (which >= SETUP_ERROR), "SETUP_ENTRY");
	CHECK(op->release_entered == (which >= RBC_RESET_BITS), "RELEASE_ENTRY");
	const char *reason = which >= DISPATCH_TOKEN && which <= DISPATCH_IB ?
		"READY_CONVENTION_OBSERVED" : cases[which].reason;
	CHECK(strcmp(ps4_ready_core_name(op->result.terminal), reason) == 0 &&
	      op->result.error == cases[which].error, "CORE_RESULT");
	if (which == SLOW_REQUEST || which == SLOW_CLOCK)
		CHECK(op->started >= 4ULL * NSEC_PER_SEC && op->elapsed == 16000000ULL,
		      "POLL_BOUND_ONLY");
}

static void differential_cases(void)
{
	for (enum scenario which = 0; which < SCENARIO_COUNT; ++which) {
		configure(which, false);
		call_wrapper(false, &device);
		CHECK(strcmp(final_reason, cases[which].reason) == 0 &&
		      final_error == cases[which].error, "BASELINE_OUTCOME");
		strcpy(old_trace, trace);
		memcpy(baseline_backing, backing, sizeof(backing));
		int old_attempted = ps4_ready_attempted;
		configure(which, true);
		call_wrapper(true, &device);
		CHECK(strcmp(final_reason, cases[which].reason) == 0 &&
		      final_error == cases[which].error, "LEGACY_OUTCOME");
		CHECK(strcmp(old_trace, trace) == 0, "LEGACY_TRACE");
		CHECK(memcmp(backing, baseline_backing, sizeof(backing)) == 0, "BACKING_CONTENT");
		CHECK(ps4_ready_attempted == old_attempted, "COLD_BOOT_LATCH");
		CHECK(park_count == 1, "ONE_WRAPPER_TERMINAL");
		CHECK(dispatch_count == (which >= DISPATCH_TOKEN && which <= DISPATCH_IB),
		      "LEGACY_DISPATCH");
		check_operation(which);
		++checks;
	}
}

static void private_reentry_and_return(void)
{
	configure(READY_PLAIN, true);
	struct amdgpu_ps4_uvd_ready_operation before = device.ps4_uvd_ready_operation;
	struct ps4_ready_core_result result = ps4_ready_core_run(&device, backing, sizeof(backing), pd);
	CHECK(result.terminal == PS4_READY_CORE_NOT_AUTHORIZED && result.error == -EPERM &&
	      !trace_used && memcmp(&before, &device.ps4_uvd_ready_operation, sizeof(before)) == 0,
	      "UNCLAIMED_CORE");
	++checks;
	device.ps4_uvd_ready_operation.boot_claimed = true;
	result = ps4_ready_core_run(&device, backing, sizeof(backing), pd);
	CHECK(result.terminal == PS4_READY_CORE_READY_OBSERVED && result.error == 0 &&
	      park_count == 0 && dispatch_count == 0, "CORE_RETURNS_WITHOUT_DISPATCH");
	CHECK(device.ps4_uvd_ready_operation.completed &&
	      device.ps4_uvd_ready_operation.firmware == &fw, "TERMINAL_STATE");
	++checks;
	before = device.ps4_uvd_ready_operation;
	size_t previous_trace = trace_used;
	result = ps4_ready_core_run(&device, NULL, 0, 0);
	CHECK(result.terminal == PS4_READY_CORE_NOT_AUTHORIZED &&
	      memcmp(&before, &device.ps4_uvd_ready_operation, sizeof(before)) == 0 &&
	      trace_used == previous_trace, "REENTRY_STATE");
	++checks;
}

static void global_attempts_and_device_isolation(void)
{
	configure(READY_PLAIN, true);
	call_wrapper(true, &device);
	struct amdgpu_ps4_uvd_ready_operation before = device.ps4_uvd_ready_operation;
	CHECK(ps4_ready_attempted == 1, "COLD_BOOT_LATCH");
	call_wrapper(true, &device);
	CHECK(strcmp(final_reason, "GATE_OR_REENTRY") == 0 &&
	      memcmp(&before, &device.ps4_uvd_ready_operation, sizeof(before)) == 0,
	      "WRAPPER_REENTRY_STATE");
	++checks;
	memset(&second_device, 0, sizeof(second_device));
	second_device.asic_type = CHIP_LIVERPOOL;
	second_device.gfx.config.gb_addr_config = 0x02011003;
	call_wrapper(true, &second_device);
	CHECK(strcmp(final_reason, "GATE_OR_REENTRY") == 0 &&
	      !second_device.ps4_uvd_ready_operation.boot_claimed &&
	      !second_device.ps4_uvd_ready_operation.entered &&
	      memcmp(&before, &device.ps4_uvd_ready_operation, sizeof(before)) == 0,
	      "SECOND_DEVICE_LATCH");
	++checks;
	configure(GATE_OFF, true);
	call_wrapper(true, &device);
	CHECK(ps4_ready_attempted == 0, "DISABLED_GATE_NO_CLAIM");
	ps4_uvd_ready = true;
	scenario = READY_PLAIN;
	call_wrapper(true, &device);
	CHECK(device.ps4_uvd_ready_operation.completed, "FIRST_ENABLED_ATTEMPT");
	++checks;
	configure(IB_INPUT, true);
	call_wrapper(true, &device);
	CHECK(ps4_ready_attempted == 1 && !device.ps4_uvd_ready_operation.entered,
	      "EARLY_FAILURE_CONSUMES_LATCH");
	ps4_uvd_ib_data = false;
	scenario = READY_PLAIN;
	call_wrapper(true, &device);
	CHECK(strcmp(final_reason, "GATE_OR_REENTRY") == 0 &&
	      !device.ps4_uvd_ready_operation.entered, "EARLY_FAILURE_NO_RETRY");
	++checks;
}

int main(void)
{
	memset(firmware_bytes, 0xa5, sizeof(firmware_bytes));
	differential_cases();
	private_reentry_and_return();
	global_attempts_and_device_isolation();
	printf("PASS: %u actual-source ready-core cases; frozen-wrapper traces, terminal state, retention, one-attempt policy\n",
	       checks);
	return 0;
}
