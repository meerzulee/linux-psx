/* SPDX-License-Identifier: GPL-2.0-only */
/* Extracted production controller under controlled observations. No device or
 * firmware emulation: the mock supplies outcomes, never executes UVD packets.
 */
#include <assert.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include <errno.h>
#include "amdgpu_ps4_uvd_message_create.h"

#define U32_MAX UINT32_MAX
#define NSEC_PER_MSEC 1000000ULL
#define NSEC_PER_SEC 1000000000ULL
#define __noreturn __attribute__((noreturn))
#define lower_32_bits(x) ((u32)(x))
#define upper_32_bits(x) ((u32)((u64)(x) >> 32))
#define le32_to_cpu(x) (x)
#define cpu_to_le32(x) (x)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define atomic_read(x) (*(x))
#define atomic_xchg(x,v) fixture_xchg((x),(v))
#define spin_lock_irqsave(x,f) do { (void)(x); (f)=0; } while (0)
#define spin_unlock_irqrestore(x,f) do { (void)(x); (void)(f); } while (0)
#define module_param_named(a,b,c,d)
#define MODULE_PARM_DESC(a,b)
#define IS_BUILTIN(x) builtin
#define IS_ENABLED(x) platform_enabled
#define CHIP_LIVERPOOL 1
#define CHIP_GLADIUS 2
#define AMDGPU_GPU_PAGE_SIZE 4096U
#define TTM_PL_TT 1
#define ttm_bo_type_kernel 2
/* @REGISTERS@ */
/* @CONSTANTS@ */
/* @DESCRIPTOR@ */
/* @READY_STATE@ */
/* @TOKEN_TYPES@ */

struct amdgpu_device {
	void *dev;
	int asic_type, gpu_reset_counter, accel_working, in_reset;
	struct { int buffer_funcs_enabled; } mman;
	struct { struct { unsigned long lock; } uvd_ctx; } reg;
	struct amdgpu_ps4_uvd_ready_operation ps4_uvd_ready_operation;
	struct amdgpu_ps4_uvd_create_operation ps4_uvd_create_operation;
};
struct resource { int mem_type; };
struct amdgpu_bo {
	struct { struct resource *resource; int pin_count, type; } tbo;
	u64 bytes;
};
struct ps4_canary_bo { struct amdgpu_bo *bo; u64 gpu; void *cpu; };
struct ps4_canary_state {
	struct ps4_canary_bo uvd_rb, uvd_ib;
	bool ready_mode, submitted;
	void *fence;
	unsigned mapped_pages;
};

static struct amdgpu_device device;
static struct ps4_canary_state *ps4_canary_retained;
static bool builtin, platform_enabled, prepare_only, ps4_map_canary;
static bool ps4_uvd_ready, ps4_uvd_poll_state, ps4_uvd_token;
static bool ps4_uvd_data_control, ps4_uvd_status_observation, ps4_uvd_ib_data;
static int ps4_ready_attempted;
static u32 regs[0x10000], indexed[0x1000];
static _Alignas(8) u8 ring_cpu[4096], backing_cpu[0x20000];
static unsigned reads, stores, ctx_writes, selector_writes, enabled_samples;
static unsigned publication_writes, submitted_samples, sleeps, barriers, flushes;
static unsigned cases_run, history_reads, legacy_dispatches, ready_calls;
static bool enabled, submitted, report_started, endpoint;
static u64 now_ns, budget_start, read_cost, sleep_cost;
static const char *case_name, *park_name;
static int park_error;
static jmp_buf parked;
static char messages[262144];
static size_t message_bytes;
static u32 mutation_reg, mutation_value;
static unsigned driver_mutation;
static u64 pd_value = 0x12345000ULL;

enum test_case {
	NORMAL, DELAYED, NO_FENCE, NO_CONTEXT, NO_DRAIN, ONE_JOINT,
	POINTER_REGRESSION, FENCE_REGRESSION, CONTEXT_REGRESSION,
	FENCE_UNEXPECTED, CONTEXT_UNEXPECTED, HELD_BAD, EMPTY_BAD,
	PREWPTR_BAD, BASELINE_FAIL, STALE_CONTEXT, STALE_FENCE,
	HISTORY_INACTIVE, HISTORY_SATURATED, HISTORY_NONZERO, HISTORY_VMID14,
	HISTORY_UNKNOWN, HISTORY_ARM_CHANGED, HISTORY_SUBMITTED,
	HISTORY_ENDPOINT, HISTORY_FINAL, HISTORY_GROWS,
	STATUS_BUSY, STATUS_BIT16, STATUS_BIT16_BUSY, STATUS_BAD,
	IB_SIZE_ARBITRARY, MUTATE_REGISTER, MUTATE_DRIVER,
	ENABLE_DEADLINE, PREWPTR_DEADLINE, FIRST_LATE, SECOND_EXACT,
	SECOND_LATE, SECOND_LAST_VALID, LATE_GUARD, ZERO_TIME,
	ENDPOINT_CORE, ENDPOINT_FENCE, ENDPOINT_CONTEXT, ENDPOINT_POINTER,
	ENDPOINT_UNEXPECTED_FENCE, SWEEP_FAIL, INITIAL_SWEEP_FAIL,
	RING_GUARD, IB_GUARD, FENCE_GUARD, TAIL_GUARD, MESSAGE_MUTATION,
	PACKET_MUTATION, MESSAGE_PREPUBLICATION,
};
static enum test_case scenario;

static __noreturn void fail(const char *witness)
{
	fprintf(stderr, "ASSERTION[%s] case=%s terminal=%u samples=%u pub=%u selectors=%u\n",
		witness, case_name ? case_name : "unnamed",
		device.ps4_uvd_create_operation.result.terminal, submitted_samples,
		publication_writes, selector_writes);
	exit(86);
}
#define CHECK(x,w) do { if (!(x)) fail(w); } while (0)

static u32 get_unaligned_le32(const void *p)
{
	const u8 *v = p;
	return (u32)v[0] | (u32)v[1] << 8 | (u32)v[2] << 16 | (u32)v[3] << 24;
}
static void word(u8 *p, u32 value)
{
	p[0] = value; p[1] = value >> 8; p[2] = value >> 16; p[3] = value >> 24;
}
static u32 crc32_le(u32 crc, const void *data, size_t size)
{
	const u8 *p = data;
	for (size_t i = 0; i < size; ++i) {
		crc ^= p[i];
		for (unsigned j = 0; j < 8; ++j)
			crc = (crc >> 1) ^ ((crc & 1U) ? 0xedb88320U : 0);
	}
	return crc;
}
static int fixture_xchg(int *p, int v) { int old = *p; *p = v; return old; }
static u64 amdgpu_bo_size(struct amdgpu_bo *bo) { return bo->bytes; }
static bool amdgpu_in_reset(struct amdgpu_device *d) { return d->in_reset; }
static bool amdgpu_ps4_live_prepare_enabled(struct amdgpu_device *d)
{
	(void)d; return prepare_only;
}
static bool amdgpu_ps4_uvd_ready_enabled(struct amdgpu_device *adev);
static bool amdgpu_ps4_uvd_message_create_options_valid(struct amdgpu_device *adev);

static void log_message(void *unused, const char *format, ...)
{
	va_list ap;
	int n;
	(void)unused;
	if (!strstr(format, " PREPARED ")) report_started = true;
	va_start(ap, format);
	n = vsnprintf(messages + message_bytes, sizeof(messages) - message_bytes, format, ap);
	va_end(ap);
	CHECK(n >= 0 && (size_t)n < sizeof(messages) - message_bytes, "LOG_CAPACITY");
	message_bytes += (size_t)n;
}
#define dev_info log_message
#define dev_err log_message

static void supply_submitted(void)
{
	struct amdgpu_ps4_uvd_create_operation *op = &device.ps4_uvd_create_operation;
	++submitted_samples;
	/* Endpoint resampling follows a completed record without another poll
	 * sleep. Do not infer it from the production confirmation threshold:
	 * the one-confirmation mutant must reach the explicit threshold witness.
	 */
	endpoint = (op->records && sleeps < op->records) ||
		now_ns - budget_start >= 250000000ULL || op->records >= 256;
	regs[mmUVD_STATUS] = 2;
	regs[mmUVD_RBC_IB_SIZE] = 16;
	regs[mmUVD_RBC_RB_RPTR] = 16;
	regs[mmUVD_CONTEXT_ID] = 0x55564343U;
	word(backing_cpu + 0x2040, 0x55564343U);
	switch (scenario) {
	case DELAYED:
		if (submitted_samples < 4) {
			word(backing_cpu + 0x2040, 0x43465243U);
			regs[mmUVD_CONTEXT_ID] = 0x43435443U;
		}
		break;
	case NO_FENCE: case ZERO_TIME:
		word(backing_cpu + 0x2040, 0x43465243U); break;
	case NO_CONTEXT: regs[mmUVD_CONTEXT_ID] = 0x43435443U; break;
	case NO_DRAIN: regs[mmUVD_RBC_RB_RPTR] = 0; break;
	case ONE_JOINT:
		if (submitted_samples > 1) regs[mmUVD_RBC_RB_RPTR] = 16;
		if (submitted_samples == 1) now_ns = budget_start + 249000000ULL;
		break;
	case POINTER_REGRESSION:
		regs[mmUVD_RBC_RB_RPTR] = submitted_samples == 1 ? 8 : 4; break;
	case FENCE_REGRESSION:
		if (submitted_samples > 1) word(backing_cpu + 0x2040, 0x43465243U);
		break;
	case CONTEXT_REGRESSION:
		if (submitted_samples > 1) regs[mmUVD_CONTEXT_ID] = 0x43435443U;
		break;
	case FENCE_UNEXPECTED: word(backing_cpu + 0x2040, 0x12345678U); break;
	case CONTEXT_UNEXPECTED: regs[mmUVD_CONTEXT_ID] = 0x12345678U; break;
	case STATUS_BUSY: regs[mmUVD_STATUS] = 3; break;
	case STATUS_BIT16: regs[mmUVD_STATUS] = 0x10002; break;
	case STATUS_BIT16_BUSY: regs[mmUVD_STATUS] = 0x10003; break;
	case STATUS_BAD: regs[mmUVD_STATUS] = 0x10000; break;
	case IB_SIZE_ARBITRARY: regs[mmUVD_RBC_IB_SIZE] = 0x76543210U; break;
	case MUTATE_REGISTER: regs[mutation_reg] = mutation_value; break;
	case MUTATE_DRIVER:
		if (driver_mutation == 1) device.gpu_reset_counter = 1;
		if (driver_mutation == 2) device.in_reset = 1;
		if (driver_mutation == 3) device.mman.buffer_funcs_enabled = 1;
		if (driver_mutation == 4) device.accel_working = 1;
		break;
	case ENDPOINT_CORE: if (endpoint) regs[mmUVD_STATUS] = 0; break;
	case ENDPOINT_FENCE:
		if (endpoint)
			word(backing_cpu + 0x2040, 0x43465243U);
		break;
	case ENDPOINT_CONTEXT:
		if (endpoint)
			regs[mmUVD_CONTEXT_ID] = 0x43435443U;
		break;
	case ENDPOINT_POINTER:
		if (endpoint)
			regs[mmUVD_RBC_RB_RPTR] = 8;
		break;
	case ENDPOINT_UNEXPECTED_FENCE:
		if (endpoint)
			word(backing_cpu + 0x2040, 0x12345678U);
		break;
	case RING_GUARD: word(ring_cpu + 128, 0); break;
	case IB_GUARD: word(backing_cpu + 128, 0); break;
	case FENCE_GUARD: word(backing_cpu + 0x2044, 0); break;
	case TAIL_GUARD: word(backing_cpu + 0x1fffc, 0); break;
	case MESSAGE_MUTATION: word(backing_cpu + 0x100c, 0x12345678U); break;
	case PACKET_MUTATION: word(backing_cpu, 0); break;
	default: break;
	}
}

static u32 rreg(u32 reg)
{
	CHECK(!report_started, "NO_GPU_ACCESS_AFTER_REPORT");
	CHECK(reg < 0x10000, "REGISTER_RANGE");
	++reads;
	now_ns += read_cost;
	if (reg == mmUVD_STATUS) {
		if (submitted) supply_submitted();
		else if (enabled) {
			++enabled_samples;
			if (scenario == EMPTY_BAD && enabled_samples == 1) regs[reg] = 0x10002;
			if (scenario == PREWPTR_BAD && enabled_samples == 2) regs[reg] = 0x10002;
		}
	}
	if (submitted && reg == mmUVD_LMI_CTRL2) {
		if (scenario == FIRST_LATE && submitted_samples == 1)
			now_ns = budget_start + 250000000ULL;
		if (submitted_samples == 2) {
			if (scenario == SECOND_EXACT) now_ns = budget_start + 250000000ULL;
			if (scenario == SECOND_LATE) now_ns = budget_start + 250000001ULL;
			if (scenario == SECOND_LAST_VALID) now_ns = budget_start + 249999999ULL;
		}
	}
	if (enabled && !submitted && reg == mmUVD_LMI_CTRL2) {
		if (scenario == ENABLE_DEADLINE && enabled_samples == 1)
			now_ns = budget_start + 250000000ULL;
		if (scenario == PREWPTR_DEADLINE && enabled_samples == 2)
			now_ns = budget_start + 250000000ULL;
	}
	if (scenario == LATE_GUARD && submitted && reg == mmUVD_STATUS) {
		now_ns = budget_start + 250000000ULL;
		regs[reg] = 0;
	}
	if (reg == mmUVD_CTX_DATA) {
		if (scenario == INITIAL_SWEEP_FAIL && !submitted) return 0;
		if (scenario == SWEEP_FAIL && endpoint) return 0;
		CHECK(regs[mmUVD_CTX_INDEX] < 0x1000, "INDEX_RANGE");
		return indexed[regs[mmUVD_CTX_INDEX]];
	}
	if (scenario == BASELINE_FAIL && reg == mmUVD_CONTEXT_ID && ctx_writes)
		return 0;
	return regs[reg];
}

static void literal_images(void)
{
	const u32 main_words[16] = { 0x3c67, 0x0030a000, 0x3c66, 3, 0x3da2, 16,
		0x3dbd, 0x55564343, 0x3bc4, 0x0030c040, 0x3bc5, 3, 0x3bc3, 0,
		0x3bff, 0 };
	const u32 ib_words[16] = { 0x3bc4, 0x0030b000, 0x3bc5, 3, 0x3bc3, 0,
		0x3bff, 0, 0x3bff, 0, 0x3bff, 0, 0x3bff, 0, 0x3bff, 0 };
	for (unsigned i = 0; i < 16; ++i) {
		CHECK(get_unaligned_le32(ring_cpu + i * 4) == main_words[i], "LITERAL_MAIN");
		CHECK(get_unaligned_le32(backing_cpu + i * 4) == ib_words[i], "LITERAL_IB");
	}
	for (unsigned i = 0; i < 1024; ++i) {
		u32 expected = i == 0 ? 0xde4 : i == 2 ? 1 : i == 7 ? 0x780 :
			i == 8 ? 0x440 : i == 10 ? 0x1b37000 : 0;
		CHECK(get_unaligned_le32(backing_cpu + 0x1000 + i * 4) == expected, "LITERAL_CREATE");
		if (i >= 16) {
			CHECK(get_unaligned_le32(ring_cpu + i * 4) == ((i & 1U) ? 0 : 0x3bff),
				"FETCHABLE_MAIN_NOP_PADDING");
			CHECK(get_unaligned_le32(backing_cpu + i * 4) == ((i & 1U) ? 0 : 0x3bff),
				"FETCHABLE_IB_NOP_PADDING");
		}
	}
}
static void wreg(u32 reg, u32 value)
{
	CHECK(!report_started, "NO_GPU_ACCESS_AFTER_REPORT");
	++stores;
	if (reg == mmUVD_CTX_INDEX) {
		++selector_writes;
		CHECK(!submitted || endpoint, "NO_SELECTOR_INSIDE_POLL");
	} else if (reg == mmUVD_CONTEXT_ID) {
		CHECK(!enabled && value == 0x43435443U && !ctx_writes, "SINGLE_CONTEXT_BASELINE");
		++ctx_writes;
	} else if (reg == mmUVD_RBC_RB_CNTL) {
		CHECK(!enabled && value == 0x1100080cU && ctx_writes == 1, "SINGLE_EMPTY_ENABLE");
		enabled = true;
		budget_start = now_ns;
	} else if (reg == mmUVD_RBC_RB_WPTR) {
		CHECK(enabled && !submitted && value == 16, "SOLE_PUBLICATION");
		CHECK(selector_writes == 8, "PREPUBLICATION_SELECTOR_COUNT");
		CHECK(barriers == 2 && flushes == 1, "PUBLICATION_BARRIERS");
		literal_images();
		++publication_writes;
		submitted = true;
	} else {
		fail("FORBIDDEN_GPU_STORE");
	}
	regs[reg] = value;
}
#define RREG32(r) rreg(r)
#define WREG32(r,v) wreg((r),(v))
static u64 ktime_get_ns(void) { return now_ns; }
static void mb(void) { ++barriers; }
static void amdgpu_device_flush_hdp(struct amdgpu_device *d, void *r)
{
	(void)d; (void)r; ++flushes;
	if (scenario == MESSAGE_PREPUBLICATION) word(backing_cpu + 0x100c, 0x12345678);
}
static void usleep_range(unsigned low, unsigned high)
{
	CHECK(low == 1000 && high == 2000, "POLL_CADENCE");
	++sleeps; now_ns += sleep_cost;
}
void amdgpu_ps4_uvd_irq_fault_snapshot(struct amdgpu_device *d,
					      struct ps4_uvd_fault_snapshot *s)
{
	(void)d;
	++history_reads;
	memset(s, 0, sizeof(*s));
	s->active = true; s->arm_ns = 42;
	if (scenario == HISTORY_INACTIVE) s->active = false;
	if (scenario == HISTORY_SATURATED) s->history.total.saturated = true;
	if (scenario == HISTORY_NONZERO) s->history.total.arrivals = 1;
	if (scenario == HISTORY_VMID14) s->history.vmid[14].arrivals = 1;
	if (scenario == HISTORY_UNKNOWN) s->history.unclassified_status.arrivals = 1;
	if (scenario == HISTORY_ARM_CHANGED && history_reads >= 2) s->arm_ns = 43;
	if (scenario == HISTORY_SUBMITTED && submitted) s->history.total.arrivals = 1;
	if (scenario == HISTORY_ENDPOINT && endpoint) s->history.total.arrivals = 1;
	if (scenario == HISTORY_FINAL && endpoint) s->history.total.saturated = true;
	if (scenario == HISTORY_GROWS && submitted) {
		u64 n = device.ps4_uvd_create_operation.first_history_reason ? 2 : 1;
		s->history.total.arrivals = n;
		s->history.vmid[14].arrivals = n;
		s->history.vmid[14].first.status = 0x111;
		s->history.vmid[14].last.status = n == 1 ? 0x111 : 0x222;
	}
}
/* @PARAMETER@ */
/* @ENABLED@ */
/* @BOOT_REQUESTED@ */
/* @CANARY_ENABLED@ */
/* @OWNER@ */
/* @HELPERS@ */
/* @FLOW@ */

static struct ps4_ready_core_result
ps4_ready_core_run(struct amdgpu_device *d, void *cpu, size_t bytes, u64 pd)
{
	(void)cpu; (void)bytes; (void)pd;
	++ready_calls;
	d->ps4_uvd_ready_operation.completed = true;
	d->ps4_uvd_ready_operation.cache_vmid_expected = 4;
	d->ps4_uvd_ready_operation.result =
		(struct ps4_ready_core_result) { PS4_READY_CORE_READY_CONVENTION_OBSERVED, 0 };
	return d->ps4_uvd_ready_operation.result;
}
static const char *ps4_ready_core_name(enum ps4_ready_core_terminal t)
{ (void)t; return "READY_CONVENTION_OBSERVED"; }
static __noreturn void ps4_ready_park(struct amdgpu_device *d, const char *s, int e)
{ (void)d; park_name = s; park_error = e; longjmp(parked, 1); }
static bool ps4_ib_data_inputs_valid(const struct amdgpu_ps4_uvd_ring *r,
	const struct amdgpu_ps4_uvd_ring *i, u64 p) { (void)r; (void)i; (void)p; return true; }
static bool ps4_data_control_inputs_valid(const struct amdgpu_ps4_uvd_ring *r, u64 p)
{ (void)r; (void)p; return true; }
static bool ps4_stobs_inputs_valid(const struct amdgpu_ps4_uvd_ring *r, u64 p)
{ (void)r; (void)p; return true; }
static void ps4_ib_data_run(struct amdgpu_device *d, const struct amdgpu_ps4_uvd_ring *r,
	const struct amdgpu_ps4_uvd_ring *i, u64 p, u32 c, u32 f, int x)
{ (void)d; (void)r; (void)i; (void)p; (void)c; (void)f; (void)x; ++legacy_dispatches; }
#define LEGACY_RUN(n) static void n(struct amdgpu_device *d, const struct amdgpu_ps4_uvd_ring *r, u64 p, u32 c, u32 f, int x) \
{ (void)d; (void)r; (void)p; (void)c; (void)f; (void)x; ++legacy_dispatches; }
LEGACY_RUN(ps4_data_control_run)
LEGACY_RUN(ps4_stobs_run)
LEGACY_RUN(ps4_token_run)
/* @WRAPPER@ */

static void reset_case(enum test_case test, const char *name)
{
	memset(&device, 0, sizeof(device)); memset(regs, 0, sizeof(regs));
	memset(indexed, 0, sizeof(indexed)); memset(ring_cpu, 0, sizeof(ring_cpu));
	memset(backing_cpu, 0, sizeof(backing_cpu)); memset(messages, 0, sizeof(messages));
	case_name = name; scenario = test; message_bytes = 0; now_ns = 100000;
	budget_start = 0; read_cost = 1000; sleep_cost = 1000000;
	reads = stores = ctx_writes = selector_writes = enabled_samples = 0;
	publication_writes = submitted_samples = sleeps = barriers = flushes = 0;
	history_reads = legacy_dispatches = ready_calls = 0;
	enabled = submitted = report_started = endpoint = false;
	ps4_ready_attempted = 0; park_name = NULL; park_error = 0;
	builtin = platform_enabled = true; prepare_only = ps4_map_canary = false;
	ps4_uvd_ready = ps4_uvd_poll_state = ps4_uvd_message_create = true;
	ps4_uvd_token = ps4_uvd_ib_data = ps4_uvd_data_control = ps4_uvd_status_observation = false;
	device.asic_type = CHIP_LIVERPOOL;
	device.ps4_uvd_ready_operation.boot_claimed = true;
	device.ps4_uvd_ready_operation.completed = true;
	device.ps4_uvd_ready_operation.result.terminal = PS4_READY_CORE_READY_CONVENTION_OBSERVED;
	regs[mmUVD_STATUS] = 2; regs[mmUVD_MASTINT_EN] = 2;
	regs[mmUVD_RBC_RB_CNTL] = 0x1101080c;
	regs[mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + 4] = pd_value >> 12;
	regs[PS4_READY_UVD_VCPU_CACHE_VMID] = 4;
	regs[mmUVD_LMI_RBC_RB_VMID] = regs[mmUVD_LMI_RBC_IB_VMID] = 4;
	regs[mmUVD_LMI_VCPU_CACHE_64BIT_BAR_LOW] = 0;
	regs[mmUVD_LMI_VCPU_CACHE_64BIT_BAR_HIGH] = 3;
	regs[mmUVD_LMI_RBC_RB_64BIT_BAR_LOW] = 0x308000;
	regs[mmUVD_LMI_RBC_RB_64BIT_BAR_HIGH] = 3;
	regs[mmUVD_LMI_RBC_IB_64BIT_BAR_LOW] = 0x30a000;
	regs[mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH] = 3;
	regs[mmUVD_VCPU_CACHE_SIZE0] = 0x7d000;
	regs[mmUVD_VCPU_CACHE_OFFSET1] = 0xfa00;
	regs[mmUVD_VCPU_CACHE_SIZE1] = 0x40000;
	regs[mmUVD_VCPU_CACHE_OFFSET2] = 0x17a00;
	regs[mmUVD_VCPU_CACHE_SIZE2] = 0x120800;
	regs[mmUVD_CTX_INDEX] = 0x19;
	indexed[ixUVD_LMI_VMID_INTERNAL] = 0x444444;
	indexed[ixUVD_LMI_VMID_INTERNAL2] = 0x44444444;
	indexed[ixUVD_LMI_VMID_INTERNAL3] = 0x4444;
	if (test == HELD_BAD) regs[mmUVD_STATUS] = 3;
	if (test == STALE_CONTEXT) regs[mmUVD_CONTEXT_ID] = 0x55564343;
	if (test == STALE_FENCE) word(backing_cpu + 0x2040, 0x55564343);
	if (test == ZERO_TIME) read_cost = sleep_cost = 0;
}

static struct amdgpu_ps4_uvd_ring rb(void)
{ return (struct amdgpu_ps4_uvd_ring) { ring_cpu, 4096, 0x300308000ULL }; }
static struct amdgpu_ps4_uvd_ring backing(void)
{ return (struct amdgpu_ps4_uvd_ring) { backing_cpu, 0x20000, 0x30030a000ULL }; }

static void run_case(enum test_case test, const char *name, enum ps4_create_terminal expected)
{
	struct amdgpu_ps4_uvd_ring r = rb(), b = backing();
	struct ps4_create_result result;
	struct amdgpu_ps4_uvd_create_operation saved;
	reset_case(test, name);
	result = ps4_create_run(&device, &r, &b, pd_value, 4, 0, 0);
	CHECK(result.terminal == expected, name);
	CHECK((result.error == 0) == (expected == PS4_CREATE_FENCE_OBSERVED), "ERROR_SCOPE");
	CHECK(publication_writes <= 1 && ctx_writes <= 1, "WRITE_COUNTS");
	CHECK(device.ps4_uvd_create_operation.completed, "RETURNED_TERMINAL");
	if (expected == PS4_CREATE_FENCE_OBSERVED) {
		CHECK(device.ps4_uvd_create_operation.consecutive == 2, "TWO_CONSECUTIVE");
		CHECK(device.ps4_uvd_create_operation.confirmed_ns < 250000000ULL, "STRICT_CONFIRM_DEADLINE");
		CHECK(device.ps4_uvd_create_operation.sweep_valid && selector_writes == 16, "ENDPOINT_SWEEP_COUNTS");
		CHECK(publication_writes == 1, "ONE_PUBLICATION");
	}
	if (test == ZERO_TIME)
		CHECK(device.ps4_uvd_create_operation.records == 256, "FINITE_SAMPLE_LIMIT");
	if (test == MESSAGE_MUTATION) {
		CHECK(device.ps4_uvd_create_operation.message_changed_words == 1 &&
			device.ps4_uvd_create_operation.message_first_offset == 12, "OPAQUE_MESSAGE_MUTATION");
	}
	if (test == HISTORY_GROWS) {
		CHECK(device.ps4_uvd_create_operation.fault_deciding.history.total.arrivals == 1 &&
			device.ps4_uvd_create_operation.fault_deciding.history.vmid[14].last.status == 0x111 &&
			device.ps4_uvd_create_operation.fault_latest.history.total.arrivals == 2 &&
			device.ps4_uvd_create_operation.fault_latest.history.vmid[14].last.status == 0x222,
			"FIRST_FAULT_SNAPSHOT_RETAINED");
	}
	if (test == SECOND_EXACT || test == SECOND_LATE)
		CHECK(device.ps4_uvd_create_operation.joint_samples == 1, "LATE_SAMPLE_NOT_CREDITED");
	if (test == ENDPOINT_CORE || test == ENDPOINT_FENCE || test == ENDPOINT_CONTEXT ||
	    test == ENDPOINT_POINTER || test == ENDPOINT_UNEXPECTED_FENCE)
		CHECK(!device.ps4_uvd_create_operation.sweep_attempted, "INVALID_ENDPOINT_NO_SWEEP");
	if (test == HELD_BAD)
		CHECK(selector_writes == 0 && stores == 0, "INVALID_HELD_NO_WRITES");
	saved = device.ps4_uvd_create_operation;
	unsigned saved_reads = reads, saved_stores = stores;
	result = ps4_create_run(&device, &r, &b, pd_value, 4, 0, 0);
	CHECK(result.terminal == PS4_CREATE_NOT_AUTHORIZED && reads == saved_reads &&
		stores == saved_stores && !memcmp(&saved, &device.ps4_uvd_create_operation, sizeof(saved)),
		"REENTRY_PRESERVES_FIRST");
	ps4_create_report(&device);
	CHECK(reads == saved_reads && stores == saved_stores, "REPORT_SAVED_ONLY");
	if (expected == PS4_CREATE_HISTORY_BASELINE) {
		CHECK(strstr(messages, "CORE sampled=0 ") &&
			strstr(messages, "MEMORY write_entered=0 initialized=0 scanned=0 ") &&
			strstr(messages, "baseline_collected=1 latest_collected=0 "),
			"EARLY_REPORT_COVERAGE");
	}
	if (test == HELD_BAD) {
		CHECK(strstr(messages, "CORE sampled=1 ") &&
			!strstr(messages, " CTX_GUARD "), "HELD_GUARD_NOT_INDEXED_GUARD");
	}
	++cases_run;
}

static void descriptor_and_gate_cases(void)
{
	struct amdgpu_ps4_uvd_ring r = rb(), b = backing(), bad;
	reset_case(NORMAL, "DESCRIPTORS");
	CHECK(ps4_create_descriptors_valid(&r, &b, pd_value), "VALID_DESCRIPTORS");
	for (unsigned i = 0; i < 8; ++i) {
		bad = b;
		if (i == 0) bad.cpu = NULL;
		if (i == 1) bad.cpu = backing_cpu + 1;
		if (i == 2) bad.cpu = (void *)(~(uintptr_t)0 - 3);
		if (i == 3) bad.bytes = 4096;
		if (i == 4) bad.private_va += 4096;
		if (i == 5) bad.cpu = ring_cpu;
		if (i == 6) bad.bytes++;
		if (i == 7) bad.bytes--;
		CHECK(!ps4_create_descriptors_valid(&r, &bad, pd_value), "BAD_BACKING_DESCRIPTOR");
		++cases_run;
	}
	CHECK(!ps4_create_descriptors_valid(NULL, &b, pd_value) &&
		!ps4_create_descriptors_valid(&r, NULL, pd_value) &&
		!ps4_create_descriptors_valid(&r, &b, 0) &&
		!ps4_create_descriptors_valid(&r, &b, 1) &&
		!ps4_create_descriptors_valid(&r, &b, 1ULL << 44), "BAD_DESCRIPTOR_OR_PD");
	bool *flags[] = { &ps4_uvd_ready, &ps4_uvd_poll_state, &ps4_uvd_token,
		&ps4_uvd_data_control, &ps4_uvd_status_observation, &ps4_uvd_ib_data,
		&prepare_only, &builtin, &platform_enabled };
	for (unsigned i = 0; i < sizeof(flags) / sizeof(*flags); ++i) {
		reset_case(NORMAL, "OPTIONS");
		*flags[i] = !*flags[i];
		CHECK(!amdgpu_ps4_uvd_message_create_options_valid(&device), "CONFLICTING_OPTIONS");
		CHECK(ps4_create_run(&device, &r, &b, pd_value, 4, 0, 0).terminal == PS4_CREATE_INPUTS &&
			reads == 0 && stores == 0, "OPTIONS_REJECT_BEFORE_MMIO");
		ps4_create_report(&device);
		CHECK(strstr(messages, "CORE sampled=0 ") &&
			strstr(messages, "HISTORY snapshots=0 baseline_collected=0 latest_collected=0 "),
			"NO_EVIDENCE_IN_UNSAMPLED_ZERO");
		++cases_run;
	}
	reset_case(NORMAL, "DEFAULT_OFF");
	ps4_uvd_ready = ps4_uvd_poll_state = ps4_uvd_message_create = false;
	CHECK(!amdgpu_ps4_uvd_message_create_requested() && !amdgpu_ps4_uvd_boot_probe_requested() &&
		!amdgpu_ps4_map_canary_enabled(&device), "DEFAULT_OFF");
	ps4_uvd_message_create = true;
	CHECK(amdgpu_ps4_uvd_boot_probe_requested() && amdgpu_ps4_map_canary_enabled(&device) &&
		!amdgpu_ps4_uvd_message_create_options_valid(&device), "LEAF_ONLY_REJECTABLE");
	device.asic_type = 99;
	CHECK(!amdgpu_ps4_map_canary_enabled(&device), "ASIC_LIMIT");
	++cases_run;
	for (unsigned i = 0; i < 4; ++i) {
		reset_case(NORMAL, "ENTRY_AUTHORITY");
		if (i == 0) device.ps4_uvd_ready_operation.boot_claimed = false;
		if (i == 1) device.ps4_uvd_ready_operation.completed = false;
		if (i == 2) device.ps4_uvd_ready_operation.result.terminal = PS4_READY_CORE_READY_OBSERVED;
		if (i == 3) device.ps4_uvd_ready_operation.result.error = -EIO;
		CHECK(ps4_create_run(&device, &r, &b, pd_value, 4, 0, 0).terminal == PS4_CREATE_NOT_AUTHORIZED &&
			!device.ps4_uvd_create_operation.entered && !reads && !stores,
			"PRIVATE_ENTRY_AUTHORITY");
		++cases_run;
	}
}

static void owner_cases(void)
{
	struct resource resource = { TTM_PL_TT };
	struct amdgpu_bo bo = { { &resource, 1, ttm_bo_type_kernel }, 0x20000 }, rb_bo = bo;
	struct ps4_canary_state s = { .uvd_ib = { &bo, 0xdeadbeef, backing_cpu },
		.uvd_rb = { &rb_bo, 0, ring_cpu }, .ready_mode = true,
		.submitted = true, .fence = &s, .mapped_pages = PS4_READY_MAPPED_PAGES };
	struct amdgpu_ps4_uvd_ring out;
	reset_case(NORMAL, "OWNER");
	ps4_canary_retained = &s;
	CHECK(ps4_canary_message_backing(&s, &out) && out.cpu == backing_cpu &&
		out.bytes == 0x20000 && out.private_va == 0x30030a000ULL, "WHOLE_OWNED_BACKING");
	for (unsigned i = 0; i < 12; ++i) {
		struct ps4_canary_state bad = s;
		struct amdgpu_bo bad_bo = bo;
		bad.uvd_ib.bo = &bad_bo; ps4_canary_retained = &bad;
		if (i == 0) ps4_canary_retained = &s;
		if (i == 1) bad.ready_mode = false;
		if (i == 2) bad.submitted = false;
		if (i == 3) bad.fence = NULL;
		if (i == 4) bad.uvd_ib.bo = NULL;
		if (i == 5) bad.uvd_ib.cpu = NULL;
		if (i == 6) bad.uvd_ib.cpu = backing_cpu + 1;
		if (i == 7) bad_bo.tbo.resource = NULL;
		if (i == 8) bad_bo.tbo.pin_count = 0;
		if (i == 9) bad_bo.tbo.type = 0;
		if (i == 10) bad_bo.bytes = 4096;
		if (i == 11) bad.mapped_pages--;
		CHECK(!ps4_canary_message_backing(&bad, &out), "OWNER_REJECTION");
		++cases_run;
	}
	ps4_canary_retained = NULL;
}

static void wrapper_cases(void)
{
	static struct amdgpu_ps4_uvd_ring r, b;
	r = rb(); b = backing();
	reset_case(NORMAL, "WRAPPER");
	if (!setjmp(parked)) amdgpu_ps4_uvd_ready_run(&device, NULL, 0, pd_value, &r, &b);
	CHECK(!strcmp(park_name, "CREATE_FENCE_OBSERVED") && park_error == 0 &&
		ready_calls == 1 && legacy_dispatches == 0, "WRAPPER_CREATE_RETURN_PARK");
	++cases_run;
	reset_case(NORMAL, "WRAPPER_EARLY");
	ps4_uvd_token = true;
	if (!setjmp(parked)) amdgpu_ps4_uvd_ready_run(&device, NULL, 0, pd_value, &r, &b);
	CHECK(!strcmp(park_name, "CREATE_DESCRIPTOR_OR_GATES") && ready_calls == 0 &&
		reads == 0 && stores == 0, "WRAPPER_BEFORE_READY");
	++cases_run;
	reset_case(NORMAL, "WRAPPER_LEGACY");
	ps4_uvd_message_create = false;
	if (!setjmp(parked)) amdgpu_ps4_uvd_ready_run(&device, NULL, 0, pd_value, &r, &b);
	CHECK(!strcmp(park_name, "READY_CONVENTION_OBSERVED") && ready_calls == 1 &&
		reads == 0 && stores == 0, "OLD_DEFAULT_PATH");
	++cases_run;
	reset_case(NORMAL, "WRAPPER_GLOBAL_LATCH");
	ps4_ready_attempted = 1;
	if (!setjmp(parked)) amdgpu_ps4_uvd_ready_run(&device, NULL, 0, pd_value, &r, &b);
	CHECK(!strcmp(park_name, "GATE_OR_REENTRY") && ready_calls == 0 &&
		!device.ps4_uvd_create_operation.entered, "GLOBAL_BOOT_LATCH");
	++cases_run;
}

int main(void)
{
	const struct { enum test_case test; const char *name; enum ps4_create_terminal expected; } cases[] = {
#define CASE(t,r) { t, #t, PS4_CREATE_##r }
		CASE(NORMAL, FENCE_OBSERVED), CASE(DELAYED, FENCE_OBSERVED),
		CASE(NO_FENCE, TIMEOUT), CASE(NO_CONTEXT, TIMEOUT), CASE(NO_DRAIN, TIMEOUT),
		CASE(ONE_JOINT, TIMEOUT), CASE(POINTER_REGRESSION, POINTER_REGRESSION),
		CASE(FENCE_REGRESSION, FENCE_REGRESSION), CASE(CONTEXT_REGRESSION, CONTEXT_REGRESSION),
		CASE(FENCE_UNEXPECTED, FENCE_UNEXPECTED), CASE(CONTEXT_UNEXPECTED, CONTEXT_UNEXPECTED),
		CASE(HELD_BAD, HELD_GUARD), CASE(EMPTY_BAD, ENABLE_GUARD),
		CASE(PREWPTR_BAD, PREPUBLICATION_GUARD), CASE(BASELINE_FAIL, BASELINE),
		CASE(STALE_CONTEXT, STALE), CASE(STALE_FENCE, STALE),
		CASE(HISTORY_INACTIVE, HISTORY_BASELINE), CASE(HISTORY_SATURATED, HISTORY_BASELINE),
		CASE(HISTORY_NONZERO, HISTORY_BASELINE), CASE(HISTORY_VMID14, HISTORY_BASELINE),
		CASE(HISTORY_UNKNOWN, HISTORY_BASELINE), CASE(HISTORY_ARM_CHANGED, HISTORY_CHANGED),
		CASE(HISTORY_SUBMITTED, HISTORY_CHANGED), CASE(HISTORY_ENDPOINT, FINAL_HISTORY),
		CASE(HISTORY_GROWS, HISTORY_CHANGED),
		CASE(HISTORY_FINAL, FINAL_HISTORY), CASE(STATUS_BUSY, FENCE_OBSERVED),
		CASE(STATUS_BIT16, FENCE_OBSERVED), CASE(STATUS_BIT16_BUSY, FENCE_OBSERVED),
		CASE(STATUS_BAD, SUBMITTED_GUARD), CASE(IB_SIZE_ARBITRARY, FENCE_OBSERVED),
		CASE(ENABLE_DEADLINE, ENABLE_TIMEOUT), CASE(PREWPTR_DEADLINE, PREPUBLICATION_TIMEOUT),
		CASE(FIRST_LATE, TIMEOUT), CASE(SECOND_EXACT, TIMEOUT), CASE(SECOND_LATE, TIMEOUT),
		CASE(SECOND_LAST_VALID, FENCE_OBSERVED), CASE(LATE_GUARD, SUBMITTED_GUARD),
		CASE(ZERO_TIME, SAMPLE_LIMIT), CASE(ENDPOINT_CORE, FINAL_GUARD),
		CASE(ENDPOINT_FENCE, FENCE_REGRESSION), CASE(ENDPOINT_CONTEXT, CONTEXT_REGRESSION),
		CASE(ENDPOINT_POINTER, POINTER_REGRESSION), CASE(ENDPOINT_UNEXPECTED_FENCE, FENCE_UNEXPECTED),
		CASE(SWEEP_FAIL, CTX_SWEEP), CASE(INITIAL_SWEEP_FAIL, HELD_GUARD),
		CASE(RING_GUARD, MEMORY), CASE(IB_GUARD, MEMORY), CASE(FENCE_GUARD, MEMORY),
		CASE(TAIL_GUARD, MEMORY), CASE(PACKET_MUTATION, MEMORY),
		CASE(MESSAGE_MUTATION, FENCE_OBSERVED), CASE(MESSAGE_PREPUBLICATION, MEMORY),
#undef CASE
	};
	descriptor_and_gate_cases(); owner_cases(); wrapper_cases();
	for (unsigned i = 0; i < sizeof(cases) / sizeof(*cases); ++i)
		run_case(cases[i].test, cases[i].name, cases[i].expected);
	const u32 bad_registers[] = {
		mmUVD_MASTINT_EN, mmUVD_RBC_RB_CNTL, mmUVD_RBC_RB_WPTR,
		mmUVD_RBC_RB_WPTR_CNTL, mmUVD_RBC_RB_RPTR_ADDR,
		mmVM_CONTEXT1_PROTECTION_FAULT_STATUS, mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + 4,
		PS4_READY_UVD_VCPU_CACHE_VMID, mmUVD_LMI_RBC_RB_VMID, mmUVD_LMI_RBC_IB_VMID,
		mmUVD_LMI_VCPU_CACHE_64BIT_BAR_LOW, mmUVD_LMI_VCPU_CACHE_64BIT_BAR_HIGH,
		mmUVD_LMI_RBC_RB_64BIT_BAR_LOW, mmUVD_LMI_RBC_RB_64BIT_BAR_HIGH,
		mmUVD_LMI_RBC_IB_64BIT_BAR_LOW, mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH,
		mmUVD_VCPU_CACHE_OFFSET0, mmUVD_VCPU_CACHE_SIZE0,
		mmUVD_VCPU_CACHE_OFFSET1, mmUVD_VCPU_CACHE_SIZE1,
		mmUVD_VCPU_CACHE_OFFSET2, mmUVD_VCPU_CACHE_SIZE2,
		mmUVD_LMI_SWAP_CNTL, mmUVD_MP_SWAP_CNTL,
		mmUVD_LMI_ADDR_EXT, mmUVD_LMI_EXT40_ADDR, mmUVD_SOFT_RESET, mmUVD_LMI_CTRL2,
	};
	for (unsigned i = 0; i < sizeof(bad_registers) / sizeof(*bad_registers); ++i) {
		mutation_reg = bad_registers[i]; mutation_value = ~0U;
		run_case(MUTATE_REGISTER, "DIRECT_REGISTER_GUARD", PS4_CREATE_SUBMITTED_GUARD);
	}
	for (driver_mutation = 1; driver_mutation <= 4; ++driver_mutation)
		run_case(MUTATE_DRIVER, "DRIVER_GUARD", PS4_CREATE_SUBMITTED_GUARD);
	printf("PASS: %u actual-source CREATE controller cases; no hardware or firmware emulation\n", cases_run);
	return 0;
}
