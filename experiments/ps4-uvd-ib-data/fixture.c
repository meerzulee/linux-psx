/* SPDX-License-Identifier: GPL-2.0-only */
/* Actual-source IB DATA0 control fixture. The mock validates the two packet
 * images and supplies controlled register observations; it does not emulate
 * UVD execution. Time includes configurable MMIO and print costs. Synthetic
 * timestamps test deadline accounting, not a prediction of hardware timing.
 */
#include <assert.h>
#include <errno.h>
#include <inttypes.h>
#include <setjmp.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "amdgpu_ps4_uvd_ring_plan.h"
#include "amdgpu_ps4_uvd_ib_data_plan.h"

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
#define __noreturn __attribute__((noreturn))
#define U32_MAX UINT32_MAX
#define NSEC_PER_MSEC UINT64_C(1000000)
#define lower_32_bits(x) ((u32)(x))
#define upper_32_bits(x) ((u32)((u64)(x) >> 32))
#define div_u64(x,y) ((unsigned long long)((x)/(y)))
#define atomic_read(p) (*(p))
#define spin_lock_irqsave(lock,flags) do { (void)(lock); (flags)=0; } while (0)
#define spin_unlock_irqrestore(lock,flags) do { (void)(lock); (void)(flags); } while (0)
#define AMDGPU_GPU_PAGE_SIZE 4096U
#define TTM_PL_TT 1
#define ttm_bo_type_kernel 2
/* ACTUAL_REGISTER_DEFINITIONS */
/* ACTUAL_READY_DEFINITIONS */
/* ACTUAL_DESCRIPTOR */

struct amdgpu_device {
	void *dev;
	struct { int buffer_funcs_enabled; } mman;
	struct { struct { unsigned long lock; } uvd_ctx; } reg;
	int gpu_reset_counter, accel_working, in_reset;
};
struct resource { int mem_type; };
struct amdgpu_bo {
	struct { struct resource *resource; int pin_count, type; } tbo;
	u64 bytes;
};
struct ps4_canary_bo { struct amdgpu_bo *bo; u64 gpu; void *cpu; };
struct ps4_canary_state {
	struct ps4_canary_bo uvd_rb, uvd_ib;
	bool ready_mode, submitted, passed;
	void *fence;
	unsigned mapped_pages;
};
static struct ps4_canary_state *ps4_canary_retained;
static u64 amdgpu_bo_size(struct amdgpu_bo *bo) { return bo->bytes; }
/* ACTUAL_OWNER_CHECK */

static bool ps4_uvd_ready, ps4_uvd_poll_state, ps4_uvd_token,
	ps4_uvd_data_control, ps4_uvd_status_observation, ps4_uvd_ib_data;
static u32 registers[0x10000], indexed[0x1000];
static _Alignas(8) unsigned char ring_bytes[4096], ib_bytes[4096];
static unsigned data_writes, context_writes, fetch_writes, pointer_writes,
	selector_writes, submitted_selector_writes, prepublication_selector_writes;
static unsigned held_samples, empty_samples, submitted_samples, reads, writes,
	sleeps, barriers, hdp_calls, cases_run;
static bool enabled, submitted, terminal_reporting, publication_announced;
static bool endpoint_sample;
static u64 now_ns, budget_start, pd_argument, read_cost_ns, write_cost_ns;
static u64 print_cost_ns, sleep_extra_ns, submitted_read_cost_ns;
static bool sleep_uses_high;
static jmp_buf terminal;
static const char *result_name, *case_name;
static int result_error;
static char messages[65536];
static size_t message_size;
static struct amdgpu_device device;
static unsigned observation_records(void);

static __noreturn void fail(const char *witness)
{
	fprintf(stderr, "ASSERTION[%s] case=%s result=%s error=%d reads=%u writes=%u records=%u\n",
		witness, case_name ? case_name : "unspecified",
		result_name ? result_name : "pending", result_error, reads, writes,
		observation_records());
	exit(86);
}
#define CHECK(condition, witness) do { if (!(condition)) fail(witness); } while (0)

enum scenario {
	NORMAL, STATUS_BUSY, STATUS_BIT16, STATUS_BIT16_BUSY,
	DRAINED, NO_TRANSPORT, MARKER_NO_DRAIN, ONE_JOINT, DELAYED_JOINT,
	POINTER_REGRESSION, DATA_REGRESSION, ENDPOINT_DATA_REGRESSION,
	ENDPOINT_POINTER_REGRESSION, ENDPOINT_FRESH, FINAL_CORE_GUARD,
	SWEEP_FAILURE, UNEXPECTED_DATA, UNEXPECTED_CONTEXT, BAD_STATUS,
	BIT16_WITHOUT_READY, IB_SIZE_ZERO, IB_SIZE_ARBITRARY, IB_SIZE_MAX,
	DIRECT_DATA_FAIL, DIRECT_CONTEXT_FAIL, HELD_DATA_DRIFT,
	HELD_CONTEXT_DRIFT, EMPTY_DATA_DRIFT, EMPTY_CONTEXT_DRIFT,
	PREWPTR_DATA_DRIFT, PREWPTR_CONTEXT_DRIFT, HELD_STATUS_BUSY,
	EMPTY_STATUS_BIT16, PREWPTR_STATUS_BIT16, HELD_IB_SIZE,
	EMPTY_IB_SIZE, PREWPTR_IB_SIZE, STALE_DATA, ENABLE_READBACK,
	ENABLE_TIMEOUT, PREWPTR_TIMEOUT, FIRST_SAMPLE_LATE,
	SECOND_SAMPLE_EXACT_DEADLINE, SECOND_SAMPLE_LATE,
	TIMED_DEADLINE, AFTER_DEADLINE_GUARD, MUTATE_SUBMITTED,
	BIT16_NO_MARKER, BIT16_TRANSIENT, BIT16_DELAYED_MARKER, EMPTY_STATUS_BUSY,
	SECOND_SAMPLE_LAST_VALID, BUSY_NO_MARKER
};
static enum scenario scenario;
static u32 mutation_reg, mutation_value;
static unsigned driver_mutation;

static void log_message(void *ignored, const char *format, ...)
{
	va_list args;
	int count;
	(void)ignored;
	if (submitted) terminal_reporting = true;
	va_start(args, format);
	count = vsnprintf(messages + message_size, sizeof(messages) - message_size,
		format, args);
	va_end(args);
	CHECK(count >= 0 && (size_t)count < sizeof(messages) - message_size,
		"LOG_BUFFER_RANGE");
	message_size += (size_t)count;
	now_ns += (u64)count * print_cost_ns;
	if (strstr(format, "PUBLICATION_BEGIN")) publication_announced = true;
	if (scenario == PREWPTR_TIMEOUT && strstr(format, "PUBLICATION_BEGIN"))
		now_ns += UINT64_C(250000000);
}
#define dev_info log_message
#define dev_err log_message

static u32 decode_word(const unsigned char *p)
{
	return (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
}
static u32 crc32_le(u32 crc, const void *memory, size_t length)
{
	const u8 *bytes = memory;
	for (size_t i = 0; i < length; ++i) {
		crc ^= bytes[i];
		for (unsigned bit = 0; bit < 8; ++bit)
			crc = (crc >> 1) ^ ((crc & 1U) ? UINT32_C(0xedb88320) : 0);
	}
	return crc;
}

/* Literal independent image checks. No builder constants are used here. */
static void check_images(void)
{
	for (unsigned offset = 0; offset < 4096; offset += 8) {
		u32 header = decode_word(ring_bytes + offset);
		u32 value = decode_word(ring_bytes + offset + 4);
		u32 expected_header = offset == 0 ? 0x3c67U : offset == 8 ? 0x3c66U :
			offset == 16 ? 0x3da2U : 0x3bffU;
		CHECK(header == expected_header, "MAIN_RING_LITERAL_HEADERS");
		if (offset == 0) CHECK(value == 0x0030a000U, "IB_ADDRESS_LOW");
		else if (offset == 8) CHECK(value == 3U, "IB_ADDRESS_HIGH");
		else if (offset == 16) CHECK(value == 16U, "IB_LENGTH_RAW16");
		else CHECK(value == 0, "MAIN_RING_NOP_PADDING");
		CHECK(decode_word(ib_bytes + offset) == (offset ? 0x3bffU : 0x3bc4U),
			"IB_ONLY_DATA0_HEADER");
		CHECK(decode_word(ib_bytes + offset + 4) == (offset ? 0U : 0x55564442U),
			"IB_ONLY_DATA0_VALUE");
	}
	unsigned main_markers = 0, ib_markers = 0;
	for (unsigned offset = 0; offset < 4096; offset += 4) {
		u32 r = decode_word(ring_bytes + offset), i = decode_word(ib_bytes + offset);
		main_markers += r == 0x55564442U; ib_markers += i == 0x55564442U;
		CHECK(r != 0x43504442U && r != 0x43504342U &&
			i != 0x43504442U && i != 0x43504342U, "BASELINES_ABSENT_FROM_IMAGES");
	}
	CHECK(main_markers == 0 && ib_markers == 1, "IB_MARKER_SINGLE_WRITER_IMAGE");
	CHECK((crc32_le(~0U, ring_bytes, 4096) ^ ~0U) == 0xe5b6afa6U &&
		(crc32_le(~0U, ib_bytes, 4096) ^ ~0U) == 0xe847443aU, "INDEPENDENT_PAGE_CRCS");
	CHECK(((u64)decode_word(ring_bytes + 12) << 32 |
		decode_word(ring_bytes + 4)) == UINT64_C(0x30030a000), "IB_ADDRESS_ALL_BITS");
}

static bool window_finished(void)
{
	return observation_records() >= 40 || now_ns - budget_start >= UINT64_C(250000000);
}

static void supplied_sample(void)
{
	++submitted_samples;
	endpoint_sample = window_finished();
	registers[mmUVD_RBC_RB_WPTR] = 16;
	registers[mmUVD_RBC_RB_RPTR] = 16;
	registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564442;
	registers[mmUVD_CONTEXT_ID] = 0x43504342;
	registers[mmUVD_STATUS] = 2;
	registers[mmUVD_RBC_IB_SIZE] = 16;
	switch (scenario) {
	case STATUS_BUSY: registers[mmUVD_STATUS] = 3; break;
	case BIT16_NO_MARKER:
		registers[mmUVD_STATUS] = 0x10002;
		registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504442;
		break;
	case BIT16_TRANSIENT:
		registers[mmUVD_STATUS] = submitted_samples < 4 ? 0x10002 : 2;
		break;
	case BIT16_DELAYED_MARKER:
		registers[mmUVD_STATUS] = 0x10002;
		if (submitted_samples < 4) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504442;
		break;
	case STATUS_BIT16: registers[mmUVD_STATUS] = 0x10002; break;
	case STATUS_BIT16_BUSY: registers[mmUVD_STATUS] = 0x10003; break;
	case DRAINED: registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504442; break;
	case BUSY_NO_MARKER:
		registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504442;
		registers[mmUVD_STATUS] = 3;
		break;
	case NO_TRANSPORT:
		registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504442;
		registers[mmUVD_RBC_RB_RPTR] = 0;
		break;
	case MARKER_NO_DRAIN: registers[mmUVD_RBC_RB_RPTR] = 0; break;
	case ONE_JOINT:
		if (submitted_samples < 40) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504442;
		break;
	case DELAYED_JOINT:
		if (submitted_samples == 1) registers[mmUVD_RBC_RB_RPTR] = 0;
		break;
	case POINTER_REGRESSION:
		if (submitted_samples >= 3) registers[mmUVD_RBC_RB_RPTR] = 12;
		break;
	case DATA_REGRESSION:
		if (submitted_samples >= 3) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504442;
		break;
	case ENDPOINT_DATA_REGRESSION:
		if (endpoint_sample) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504442;
		break;
	case ENDPOINT_POINTER_REGRESSION:
		if (endpoint_sample) registers[mmUVD_RBC_RB_RPTR] = 12;
		break;
	case ENDPOINT_FRESH:
		if (!endpoint_sample) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504442;
		break;
	case FINAL_CORE_GUARD:
		if (endpoint_sample) registers[mmUVD_STATUS] = 0x20002;
		break;
	case SWEEP_FAILURE:
		if (endpoint_sample) indexed[ixUVD_LMI_VMID_INTERNAL2] = 0;
		break;
	case UNEXPECTED_DATA: registers[mmUVD_GPCOM_VCPU_DATA0] = 0x12345678; break;
	case UNEXPECTED_CONTEXT: registers[mmUVD_CONTEXT_ID] = 0x43545842; break;
	case BAD_STATUS: registers[mmUVD_STATUS] = 0x20002; break;
	case BIT16_WITHOUT_READY: registers[mmUVD_STATUS] = 0x10000; break;
	case IB_SIZE_ZERO: registers[mmUVD_RBC_IB_SIZE] = 0; break;
	case IB_SIZE_ARBITRARY: registers[mmUVD_RBC_IB_SIZE] = 67; break;
	case IB_SIZE_MAX: registers[mmUVD_RBC_IB_SIZE] = UINT32_MAX; break;
	case AFTER_DEADLINE_GUARD:
		if (endpoint_sample) registers[mmUVD_STATUS] = 0x20002;
		break;
	case MUTATE_SUBMITTED:
		if (driver_mutation == 1) device.gpu_reset_counter = 1;
		else if (driver_mutation == 2) device.in_reset = 1;
		else if (driver_mutation == 3) device.mman.buffer_funcs_enabled = 1;
		else if (driver_mutation == 4) device.accel_working = 1;
		else registers[mutation_reg] = mutation_value;
		break;
	default: break;
	}
}

static u32 mmio_read(u32 reg)
{
	CHECK(reg < 0x10000, "REGISTER_RANGE");
	CHECK(!terminal_reporting, "GPU_READ_AFTER_REPORT");
	++reads;
	now_ns += read_cost_ns;
	if (reg == mmUVD_STATUS) {
		if (scenario == ENABLE_TIMEOUT && enabled && !submitted)
			now_ns += UINT64_C(250000000);
		if (submitted) supplied_sample();
		else if (!enabled) {
			++held_samples;
			if (held_samples == 2) {
				if (scenario == HELD_DATA_DRIFT) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564442;
				if (scenario == HELD_CONTEXT_DRIFT) registers[mmUVD_CONTEXT_ID] = 0;
			}
		} else {
			++empty_samples;
			if (scenario == EMPTY_STATUS_BUSY) registers[mmUVD_STATUS] = 3;
			if (empty_samples == 1) {
				if (scenario == EMPTY_DATA_DRIFT) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564442;
				if (scenario == EMPTY_CONTEXT_DRIFT) registers[mmUVD_CONTEXT_ID] = 0;
				if (scenario == EMPTY_STATUS_BIT16) registers[mmUVD_STATUS] = 0x10002;
				if (scenario == EMPTY_IB_SIZE) registers[mmUVD_RBC_IB_SIZE] = 16;
			}
			if (empty_samples == 2) {
				if (scenario == PREWPTR_DATA_DRIFT) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564442;
				if (scenario == PREWPTR_CONTEXT_DRIFT) registers[mmUVD_CONTEXT_ID] = 0;
				if (scenario == PREWPTR_STATUS_BIT16) registers[mmUVD_STATUS] = 0x10002;
				if (scenario == PREWPTR_IB_SIZE) registers[mmUVD_RBC_IB_SIZE] = 16;
			}
		}
	}
	if (submitted && reg == mmUVD_GPCOM_VCPU_DATA0 && !endpoint_sample) {
		if (scenario == FIRST_SAMPLE_LATE && submitted_samples == 1)
			now_ns = budget_start + UINT64_C(250000001);
		if (scenario == SECOND_SAMPLE_LAST_VALID && submitted_samples == 2)
			now_ns = budget_start + UINT64_C(249999999);
		if (scenario == SECOND_SAMPLE_EXACT_DEADLINE && submitted_samples == 2)
			now_ns = budget_start + UINT64_C(250000000);
		if (scenario == SECOND_SAMPLE_LATE && submitted_samples == 2)
			now_ns = budget_start + UINT64_C(250000001);
	}
	if (reg == mmUVD_CTX_DATA) return indexed[registers[mmUVD_CTX_INDEX]];
	return registers[reg];
}

static void mmio_write(u32 reg, u32 value)
{
	CHECK(reg < 0x10000, "REGISTER_RANGE");
	CHECK(!terminal_reporting, "GPU_WRITE_AFTER_REPORT");
	++writes;
	if (reg == mmUVD_CTX_INDEX) {
		CHECK(value < 0x1000, "INDEX_RANGE");
		++selector_writes;
		if (submitted) {
			++submitted_selector_writes;
			CHECK(window_finished() && endpoint_sample, "NO_INDEXED_ACCESS_IN_WINDOW");
		} else CHECK(!publication_announced, "DIRECT_PREWPTR_NO_SELECTOR");
	} else if (reg == mmUVD_GPCOM_VCPU_DATA0) {
		CHECK(value == 0x43504442, "CPU_DATA0_BASELINE_ONLY");
		CHECK(!enabled && !submitted && !data_writes++ && !context_writes,
			"CPU_DATA0_SINGLE_STORE_ORDER");
		if (scenario == DIRECT_DATA_FAIL) value = 0;
	} else if (reg == mmUVD_CONTEXT_ID) {
		CHECK(value == 0x43504342, "CPU_CONTEXT_BASELINE_ONLY");
		CHECK(!enabled && !submitted && !context_writes++ && data_writes == 1,
			"CPU_CONTEXT_SINGLE_STORE_ORDER");
		if (scenario == DIRECT_CONTEXT_FAIL) value = 0;
	} else if (reg == mmUVD_RBC_RB_CNTL) {
		CHECK(value == 0x1100080c && registers[reg] == 0x1101080c,
			"FETCH_CHANGES_NO_FETCH_ONLY");
		CHECK(!enabled && !submitted && !fetch_writes++, "ONE_FETCH_ENABLE");
		CHECK(registers[mmUVD_RBC_RB_WPTR] == 0 && data_writes == 1 &&
			context_writes == 1 && hdp_calls == 1 && barriers >= 1,
			"FETCH_AFTER_BASELINES_AND_VISIBILITY");
		check_images();
		budget_start = now_ns;
		enabled = true;
		if (scenario == ENABLE_READBACK) value = 0x1101080c;
	} else if (reg == mmUVD_RBC_RB_WPTR) {
		CHECK(value == 16 && enabled && !submitted && !pointer_writes++, "ONE_WPTR_RAW16");
		CHECK(barriers >= 2 && now_ns - budget_start < UINT64_C(250000000),
			"PUBLICATION_STRICT_DEADLINE");
		CHECK(registers[mmUVD_GPCOM_VCPU_DATA0] == 0x43504442 &&
			registers[mmUVD_CONTEXT_ID] == 0x43504342,
			"PREPUBLICATION_BASELINES_VERIFIED");
		prepublication_selector_writes = selector_writes;
		CHECK(prepublication_selector_writes == 24, "PREPUBLICATION_24_SELECTORS");
		check_images();
		submitted = true;
		read_cost_ns = submitted_read_cost_ns;
	} else fail("FORBIDDEN_GPU_STORE");
	registers[reg] = value;
	now_ns += write_cost_ns;
}
#define RREG32(reg) mmio_read(reg)
#define WREG32(reg,value) mmio_write(reg,value)
static bool amdgpu_in_reset(struct amdgpu_device *adev) { return adev->in_reset; }
static u64 ktime_get_ns(void) { return now_ns; }
static void mb(void) { ++barriers; }
static void amdgpu_device_flush_hdp(struct amdgpu_device *adev, void *ring)
{ (void)adev; (void)ring; ++hdp_calls; }
static void usleep_range(unsigned low, unsigned high)
{
	CHECK((low == 100 && high == 200) || (low == 12000 && high == 12500), "DECLARED_SLEEP_RANGE");
	++sleeps;
	now_ns += (u64)(sleep_uses_high ? high : low) * 1000 + sleep_extra_ns;
}
static __noreturn void ps4_ready_park(struct amdgpu_device *adev, const char *name, int error)
{ (void)adev; result_name = name; result_error = error; longjmp(terminal, 1); }

/* ACTUAL_CONTEXT_READER */
/* ACTUAL_SAMPLER_DEPENDENCIES */
/* ACTUAL_IB_DATA_FLOW */

static unsigned observation_records(void) { return ps4_ib_data_record_count; }
static void receiver_preflight(struct amdgpu_device *adev,
	const struct amdgpu_ps4_uvd_ring *rb, const struct amdgpu_ps4_uvd_ring *ib,
	u64 pd_addr)
{
	/* ACTUAL_EARLY_GATE */
}

static void fresh(enum scenario next, const char *name)
{
	memset(registers, 0, sizeof(registers));
	memset(indexed, 0, sizeof(indexed));
	memset(&device, 0, sizeof(device));
	memset(ring_bytes, 0xa5, sizeof(ring_bytes));
	memset(ib_bytes, 0x5a, sizeof(ib_bytes));
	message_size = 0; messages[0] = 0; result_name = NULL; result_error = 99;
	data_writes = context_writes = fetch_writes = pointer_writes = selector_writes = 0;
	submitted_selector_writes = prepublication_selector_writes = 0;
	held_samples = empty_samples = submitted_samples = reads = writes = 0;
	sleeps = barriers = hdp_calls = 0;
	now_ns = budget_start = 0; pd_argument = 0x100000;
	read_cost_ns = submitted_read_cost_ns = 100; write_cost_ns = 100;
	print_cost_ns = 100; sleep_extra_ns = 0;
	sleep_uses_high = publication_announced = false;
	ps4_ib_data_record_count = 0;
	ps4_ib_data_publish_ns = 0;
	memset(ps4_ib_data_log, 0, sizeof(ps4_ib_data_log));
	enabled = submitted = terminal_reporting = endpoint_sample = false;
	scenario = next; case_name = name;
	mutation_reg = mutation_value = driver_mutation = 0;
	ps4_uvd_ready = ps4_uvd_poll_state = ps4_uvd_token = ps4_uvd_ib_data = true;
	ps4_uvd_data_control = ps4_uvd_status_observation = false;
	registers[mmUVD_STATUS] = 2;
	registers[mmUVD_MASTINT_EN] = 2;
	registers[mmUVD_RBC_RB_CNTL] = 0x1101080c;
	registers[mmUVD_CONTEXT_ID] = 0x8899aabb;
	registers[mmUVD_GPCOM_VCPU_DATA0] = 0x11223344;
	registers[mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + 4] = 0x100;
	registers[PS4_READY_UVD_VCPU_CACHE_VMID] = 4;
	registers[mmUVD_LMI_RBC_RB_VMID] = 4;
	registers[mmUVD_LMI_RBC_IB_VMID] = 4;
	registers[mmUVD_LMI_VCPU_CACHE_64BIT_BAR_HIGH] = 3;
	registers[mmUVD_LMI_RBC_RB_64BIT_BAR_LOW] = 0x308000;
	registers[mmUVD_LMI_RBC_RB_64BIT_BAR_HIGH] = 3;
	registers[mmUVD_LMI_RBC_IB_64BIT_BAR_LOW] = 0x30a000;
	registers[mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH] = 3;
	registers[mmUVD_VCPU_CACHE_SIZE0] = 0x7d000;
	registers[mmUVD_CTX_INDEX] = 0x55;
	registers[mmUVD_VCPU_CACHE_OFFSET1] = 0xfa00;
	registers[mmUVD_VCPU_CACHE_SIZE1] = 0x40000;
	registers[mmUVD_VCPU_CACHE_OFFSET2] = 0x17a00;
	registers[mmUVD_VCPU_CACHE_SIZE2] = 0x120800;
	indexed[ixUVD_LMI_VMID_INTERNAL] = 0x00444444;
	indexed[ixUVD_LMI_VMID_INTERNAL2] = 0x44444444;
	indexed[ixUVD_LMI_VMID_INTERNAL3] = 0x4444;
	if (next == STALE_DATA) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564442;
	if (next == HELD_STATUS_BUSY) registers[mmUVD_STATUS] = 3;
	if (next == HELD_IB_SIZE) registers[mmUVD_RBC_IB_SIZE] = 16;
	if (next == TIMED_DEADLINE || next == AFTER_DEADLINE_GUARD) {
		print_cost_ns = 86806;
		submitted_read_cost_ns = 10000;
		sleep_uses_high = true;
		sleep_extra_ns = UINT64_C(1000000);
	}
}
static struct amdgpu_ps4_uvd_ring ring_descriptor(void)
{ return (struct amdgpu_ps4_uvd_ring){ ring_bytes, 4096, UINT64_C(0x300308000) }; }
static struct amdgpu_ps4_uvd_ring ib_descriptor(void)
{ return (struct amdgpu_ps4_uvd_ring){ ib_bytes, 4096, UINT64_C(0x30030a000) }; }

static void run_direct(const struct amdgpu_ps4_uvd_ring *rb,
	const struct amdgpu_ps4_uvd_ring *ib)
{
	if (!setjmp(terminal)) ps4_ib_data_run(&device, rb, ib, pd_argument, 4, 0, 0);
	CHECK(result_name != NULL, "RETAINED_PARK_REQUIRED");
	CHECK(registers[mmUVD_CTX_INDEX] == 0x55, "SELECTOR_RESTORED");
	++cases_run;
}
static void expect_result(const char *expected, int error, unsigned fetch, unsigned pointer,
	const char *witness)
{
	if (!result_name || strcmp(result_name, expected)) {
		fprintf(stderr, "expected result %s, got %s\n", expected, result_name);
		fail(witness);
	}
	CHECK(error == 0 ? result_error == 0 : result_error < 0, witness);
	CHECK(fetch_writes == fetch && pointer_writes == pointer, witness);
}
static void pages_unchanged(void)
{
	for (unsigned i = 0; i < 4096; ++i)
		CHECK(ring_bytes[i] == 0xa5 && ib_bytes[i] == 0x5a, "NO_IMAGE_WRITE_ON_REJECTION");
}

/* Remaining tests follow the actual candidate's final report interface. */
/* TEST_CASES */
static int64_t report_number(const char *line, const char *key, int base)
{
	char needle[80], *end;
	const char *found, *limit;
	int64_t value;
	CHECK(line != NULL, "REPORT_LINE_PRESENT");
	limit = strchr(line, '\n');
	CHECK(limit != NULL, "REPORT_LINE_COMPLETE");
	snprintf(needle, sizeof(needle), " %s=", key);
	found = strstr(line, needle);
	CHECK(found && found < limit, "REPORT_FIELD_PRESENT");
	found += strlen(needle);
	errno = 0;
	value = strtoll(found, &end, base);
	CHECK(!errno && end != found && end <= limit &&
		(*end == ' ' || *end == ';' || *end == '\n'), "REPORT_FIELD_VALID");
	return value;
}
static const char *report_line(const char *tag)
{
	char needle[80];
	const char *line;
	snprintf(needle, sizeof(needle), "PS4_UVD_IBDATA %s ", tag);
	line = strstr(messages, needle);
	CHECK(line && !strstr(line + 1, needle), "REPORT_LINE_UNIQUE");
	return line;
}
static int64_t end_number(const char *key)
{ return report_number(report_line("END"), key, 10); }
static int64_t evidence_number(const char *key)
{ return report_number(report_line("EVIDENCE"), key, 10); }

static void completion_checks(bool confirmed, bool full_sweep)
{
	unsigned qualifying = 0, joints = 0, consecutive = 0;
	bool modeled_confirmed = false, data_seen = false, transport_seen = false;
	bool bit16_seen = false;
	unsigned count = observation_records();
	CHECK(count > 0 && count <= 40, "BOUNDED_RECORD_COUNT");
	CHECK(prepublication_selector_writes == 24, "PREPUBLICATION_24_SELECTORS");
	if (full_sweep) CHECK(submitted_selector_writes == 8, "ENDPOINT_8_SELECTORS");
	CHECK(end_number("records") == count && end_number("accepted") == 0,
		"EXACT_RECORD_COUNT_NO_ACCEPTANCE");
	for (unsigned i = 0; i < count; ++i) {
		const struct ps4_ib_data_record *r = &ps4_ib_data_log[i];
		CHECK(r->elapsed_end_ns > r->elapsed_ns, "READ_COST_BRACKETS_SAMPLE");
		CHECK(r->elapsed_ns < UINT64_C(250000000), "NO_SAMPLE_START_AT_DEADLINE");
		CHECK(r->predeadline == (r->elapsed_end_ns < UINT64_C(250000000)),
			"STRICT_SAMPLE_END_TIME");
		if (r->valid && r->predeadline) {
			++qualifying;
			data_seen |= r->sample.data == 0x55564442;
			bit16_seen |= !!(r->sample.core.status & 0x10000);
			transport_seen |= r->sample.core.rptr == 16 && r->sample.core.wptr == 16;
			if (r->sample.data == 0x55564442 &&
			    r->sample.core.rptr == 16 && r->sample.core.wptr == 16) {
				++joints; ++consecutive;
				modeled_confirmed |= consecutive >= 2;
			} else consecutive = 0;
		}
	}
	CHECK(evidence_number("valid_samples") == qualifying &&
		evidence_number("joint_samples") == joints &&
		evidence_number("consecutive") == consecutive,
		"QUALIFY_ONLY_VALID_PREDEADLINE_SAMPLES");
	CHECK(evidence_number("data_seen") == data_seen &&
		evidence_number("transport_seen") == transport_seen &&
		end_number("bit16_seen") == bit16_seen,
		"ENDPOINT_CANNOT_CREATE_EVIDENCE");
	CHECK(evidence_number("confirmed") == modeled_confirmed && modeled_confirmed == confirmed,
		"TWO_CONSECUTIVE_JOINT_REQUIRED");
	CHECK(end_number("marker_observed") == (confirmed && result_error == 0),
		"FAILED_FINAL_CANNOT_SUCCEED");
	CHECK(end_number("predeadline_records") == qualifying, "PREDEADLINE_REPORT_COUNT");
	if (count == 40) CHECK(strstr(report_line("END"), " end_reason=record_budget ") != NULL,
		"RECORD_BUDGET_REASON");
	else CHECK(strstr(report_line("END"), " end_reason=deadline ") != NULL,
		"DEADLINE_REASON");
	CHECK(strstr(messages, "PS4_UVD_IBDATA FINAL_CORE ") <
		strstr(messages, "PS4_UVD_IBDATA REC i="), "FINAL_REPORT_BEFORE_RECORD_REPORT");
	CHECK(strstr(messages, "ring_crc=e5b6afa6 ib_crc=e847443a") != NULL,
		"INDEPENDENT_PAGE_CRCS_REPORTED");
}

static void receiver_case(const struct amdgpu_ps4_uvd_ring *rb,
	const struct amdgpu_ps4_uvd_ring *ib)
{
	if (!setjmp(terminal)) {
		receiver_preflight(&device, rb, ib, pd_argument);
		fail("EARLY_GATE_MUST_PARK");
	}
	CHECK(result_error == -EINVAL && !reads && !writes && !message_size,
		"EARLY_GATE_BEFORE_ALL_EFFECTS");
	pages_unchanged();
	++cases_run;
}
static void test_gates_and_descriptors(void)
{
	struct amdgpu_ps4_uvd_ring rb = ring_descriptor(), ib = ib_descriptor();
	for (unsigned gate = 0; gate < 6; ++gate) {
		fresh(NORMAL, "disabled/conflicting gate");
		switch (gate) {
		case 0: ps4_uvd_ready = false; break;
		case 1: ps4_uvd_poll_state = false; break;
		case 2: ps4_uvd_token = false; break;
		case 3: ps4_uvd_data_control = true; break;
		case 4: ps4_uvd_status_observation = true; break;
		case 5: ps4_uvd_ib_data = false; break;
		}
		CHECK(!ps4_ib_data_inputs_valid(&rb, &ib, pd_argument), "ALL_REQUIRED_GATES");
		if (gate != 5) receiver_case(&rb, &ib);
		run_direct(&rb, &ib);
		expect_result("IBDATA_DESCRIPTOR_OR_GATES", -EINVAL, 0, 0, "DIRECT_GATE_REJECTS");
		CHECK(!reads && !writes, "DIRECT_GATE_BEFORE_MMIO");
		pages_unchanged();
	}
	for (unsigned which = 0; which < 19; ++which) {
		fresh(NORMAL, "invalid descriptor/PD");
		rb = ring_descriptor(); ib = ib_descriptor();
		switch (which) {
		case 0: rb.cpu = NULL; break;
		case 1: rb.cpu = ring_bytes + 1; break;
		case 2: rb.cpu = (void *)(UINTPTR_MAX - 3); break;
		case 3: rb.bytes = 4095; break;
		case 4: rb.bytes = 8192; break;
		case 5: rb.private_va += 4096; break;
		case 6: ib.cpu = NULL; break;
		case 7: ib.cpu = ib_bytes + 1; break;
		case 8: ib.cpu = (void *)(UINTPTR_MAX - 3); break;
		case 9: ib.bytes = 4095; break;
		case 10: ib.bytes = 8192; break;
		case 11: ib.private_va += 4096; break;
		case 12: ib.cpu = rb.cpu; break;
		case 13: ib.cpu = ring_bytes + 4; break;
		case 14: rb.cpu = ib_bytes + 4; break;
		case 15: ib.private_va = UINT64_C(0x30a000); break;
		case 16: pd_argument = 0; break;
		case 17: pd_argument = 0x100001; break;
		case 18: pd_argument = UINT64_C(0x100000000000); break;
		}
		receiver_case(&rb, &ib);
		run_direct(&rb, &ib);
		expect_result("IBDATA_DESCRIPTOR_OR_GATES", -EINVAL, 0, 0, "DESCRIPTOR_REJECTS");
		CHECK(!reads && !writes, "BAD_DESCRIPTOR_BEFORE_MMIO");
		pages_unchanged();
	}
	fresh(NORMAL, "null ring"); ib = ib_descriptor();
	receiver_case(NULL, &ib);
	fresh(NORMAL, "null IB"); rb = ring_descriptor();
	receiver_case(&rb, NULL);
	fresh(NORMAL, "gate-off receiver"); ps4_uvd_ib_data = false;
	receiver_preflight(&device, NULL, NULL, 0);
	CHECK(!reads && !writes && !message_size && !result_name, "GATE_OFF_NO_EFFECT");
	++cases_run;
}

static void test_owner(void)
{
	struct resource resource;
	struct amdgpu_bo ring_bo, ib_bo;
	struct ps4_canary_state state, stranger;
	struct amdgpu_ps4_uvd_ring output, saved;
	for (unsigned which = 0; which < 17; ++which) {
		fresh(NORMAL, "borrowed IB owner rejection");
		resource = (struct resource){ TTM_PL_TT };
		ring_bo = (struct amdgpu_bo){ { &resource, 1, ttm_bo_type_kernel }, 4096 };
		ib_bo = (struct amdgpu_bo){ { &resource, 1, ttm_bo_type_kernel }, 0x20000 };
		state = (struct ps4_canary_state){
			.uvd_rb = { &ring_bo, UINT64_C(0x99999000), ring_bytes },
			.uvd_ib = { &ib_bo, UINT64_C(0x88888000), ib_bytes },
			.ready_mode = true, .submitted = true, .passed = false,
			.fence = &device, .mapped_pages = 812,
		};
		stranger = state; ps4_canary_retained = &state;
		memset(&output, 0xa5, sizeof(output)); saved = output;
		switch (which) {
		case 0: ps4_canary_retained = &stranger; break;
		case 1: state.ready_mode = false; break;
		case 2: state.submitted = false; break;
		case 3: state.fence = NULL; break;
		case 4: state.uvd_ib.bo = NULL; break;
		case 5: state.uvd_ib.cpu = NULL; break;
		case 6: state.uvd_ib.cpu = ib_bytes + 1; break;
		case 7: ib_bo.tbo.resource = NULL; break;
		case 8: ib_bo.tbo.pin_count = 0; break;
		case 9: ib_bo.tbo.type = 99; break;
		case 10: resource.mem_type = 99; break;
		case 11: ib_bo.bytes = 4096; break;
		case 12: state.uvd_rb.bo = state.uvd_ib.bo; break;
		case 13: state.mapped_pages = 811; break;
		case 14: state.mapped_pages = 813; break;
		case 15:
			CHECK(!ps4_canary_ib_data_page(NULL, &output), "NULL_OWNER_REJECTED");
			CHECK(!memcmp(&output, &saved, sizeof(output)), "OWNER_OUTPUT_UNCHANGED");
			++cases_run; continue;
		case 16:
			CHECK(!ps4_canary_ib_data_page(&state, NULL), "NULL_OWNER_OUTPUT_REJECTED");
			++cases_run; continue;
		}
		CHECK(!ps4_canary_ib_data_page(&state, &output), "INVALID_OWNER_REJECTED");
		CHECK(!memcmp(&output, &saved, sizeof(output)), "OWNER_OUTPUT_UNCHANGED");
		CHECK(!reads && !writes && !message_size, "OWNER_CHECK_NO_DEVICE_ACCESS");
		++cases_run;
	}
	fresh(NORMAL, "borrowed owned pages");
	resource = (struct resource){ TTM_PL_TT };
	ring_bo = (struct amdgpu_bo){ { &resource, 1, ttm_bo_type_kernel }, 4096 };
	ib_bo = (struct amdgpu_bo){ { &resource, 1, ttm_bo_type_kernel }, 0x20000 };
	state = (struct ps4_canary_state){
		.uvd_rb = { &ring_bo, UINT64_C(0x99999000), ring_bytes },
		.uvd_ib = { &ib_bo, UINT64_C(0x88888000), ib_bytes },
		.ready_mode = true, .submitted = true, .passed = false,
		.fence = &device, .mapped_pages = 812,
	};
	ps4_canary_retained = &state;
	CHECK(ps4_canary_token_ring(&state, &output) && output.cpu == ring_bytes &&
		output.bytes == 4096 && output.private_va == UINT64_C(0x300308000),
		"RETAINED_RING_PRIVATE_ADDRESS");
	CHECK(ps4_canary_ib_data_page(&state, &output) && output.cpu == ib_bytes &&
		output.bytes == 4096 && output.private_va == UINT64_C(0x30030a000),
		"RETAINED_IB_FIRST_PAGE_PRIVATE_ADDRESS");
	CHECK(state.mapped_pages == 812 && !state.passed, "BORROW_DOES_NOT_REINVENT_PRECHECK");
	++cases_run;
}

static void test_prepublication(const struct amdgpu_ps4_uvd_ring *rb,
	const struct amdgpu_ps4_uvd_ring *ib)
{
	const struct {
		enum scenario scenario; const char *name, *result; unsigned fetch;
	} cases[] = {
		{ STALE_DATA, "stale marker", "IBDATA_STALE_RESULT", 0 },
		{ DIRECT_DATA_FAIL, "DATA0 direct readback", "IBDATA_BASELINE_READBACK", 0 },
		{ DIRECT_CONTEXT_FAIL, "CONTEXT direct readback", "IBDATA_CONTEXT_BASELINE_READBACK", 0 },
		{ HELD_DATA_DRIFT, "held DATA0 drift", "IBDATA_BASELINE_OR_PUBLICATION_GUARD", 0 },
		{ HELD_CONTEXT_DRIFT, "held context drift", "IBDATA_BASELINE_OR_PUBLICATION_GUARD", 0 },
		{ EMPTY_DATA_DRIFT, "empty DATA0 drift", "IBDATA_ENABLE_GUARD", 1 },
		{ EMPTY_CONTEXT_DRIFT, "empty context drift", "IBDATA_ENABLE_GUARD", 1 },
		{ PREWPTR_DATA_DRIFT, "pre-WPTR DATA0 drift", "IBDATA_PREPUBLICATION_GUARD", 1 },
		{ PREWPTR_CONTEXT_DRIFT, "pre-WPTR context drift", "IBDATA_PREPUBLICATION_GUARD", 1 },
		{ HELD_STATUS_BUSY, "held status3", "IBDATA_PRECONDITION", 0 },
		{ EMPTY_STATUS_BIT16, "empty bit16", "IBDATA_ENABLE_GUARD", 1 },
		{ PREWPTR_STATUS_BIT16, "pre-WPTR bit16", "IBDATA_PREPUBLICATION_GUARD", 1 },
		{ HELD_IB_SIZE, "held nonzero IB_SIZE", "IBDATA_PRECONDITION", 0 },
		{ EMPTY_IB_SIZE, "empty nonzero IB_SIZE", "IBDATA_ENABLE_GUARD", 1 },
		{ PREWPTR_IB_SIZE, "pre-WPTR nonzero IB_SIZE", "IBDATA_PREPUBLICATION_GUARD", 1 },
		{ ENABLE_READBACK, "enable readback", "IBDATA_ENABLE_GUARD", 1 },
		{ ENABLE_TIMEOUT, "enable deadline", "IBDATA_ENABLE_TIMEOUT", 1 },
		{ PREWPTR_TIMEOUT, "publication log deadline", "IBDATA_BEFORE_SUBMIT_TIMEOUT", 1 }
	};
	for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
		fresh(cases[i].scenario, cases[i].name);
		run_direct(rb, ib);
		expect_result(cases[i].result, -1, cases[i].fetch, 0,
			cases[i].scenario == DIRECT_DATA_FAIL ? "DIRECT_BASELINE_CHECK_REQUIRED" :
			"PREPUBLICATION_TERMINAL");
		CHECK(!submitted_selector_writes && !observation_records(), "NO_WINDOW_AFTER_PREPUB_FAILURE");
		if (cases[i].scenario == DIRECT_DATA_FAIL)
			CHECK(data_writes == 1 && context_writes == 0 && held_samples == 1,
				"DIRECT_BASELINE_CHECK_REQUIRED");
		if (cases[i].scenario == STALE_DATA || cases[i].scenario == HELD_STATUS_BUSY ||
		    cases[i].scenario == HELD_IB_SIZE) pages_unchanged();
	}
}

static void test_completed(const struct amdgpu_ps4_uvd_ring *rb,
	const struct amdgpu_ps4_uvd_ring *ib)
{
	const struct {
		enum scenario scenario; const char *name, *result; bool confirmed;
	} cases[] = {
		{ NORMAL, "normal", "IBDATA_MARKER_OBSERVED", true },
		{ STATUS_BUSY, "submitted status3", "IBDATA_MARKER_OBSERVED", true },
		{ STATUS_BIT16, "submitted status10002", "IBDATA_MARKER_OBSERVED", true },
		{ STATUS_BIT16_BUSY, "submitted status10003", "IBDATA_MARKER_OBSERVED", true },
		{ EMPTY_STATUS_BUSY, "empty status3 allowed", "IBDATA_MARKER_OBSERVED", true },
		{ BIT16_NO_MARKER, "bit16/no marker", "IBDATA_STATUS_WITHOUT_MARKER", false },
		{ BIT16_TRANSIENT, "bit16 set then clear", "IBDATA_MARKER_OBSERVED", true },
		{ BIT16_DELAYED_MARKER, "bit16 before later marker", "IBDATA_MARKER_OBSERVED", true },
		{ DRAINED, "drained/no marker", "IBDATA_DRAINED_NO_MARKER", false },
		{ BUSY_NO_MARKER, "busy bit only/no marker", "IBDATA_DRAINED_NO_MARKER", false },
		{ NO_TRANSPORT, "no transport", "IBDATA_NO_TRANSPORT", false },
		{ MARKER_NO_DRAIN, "marker/no drain", "IBDATA_MARKER_UNCONFIRMED", false },
		{ ONE_JOINT, "only one joint", "IBDATA_MARKER_UNCONFIRMED", false },
		{ DELAYED_JOINT, "marker before drain", "IBDATA_MARKER_OBSERVED", true },
		{ ENDPOINT_FRESH, "endpoint-only fresh", "IBDATA_DRAINED_NO_MARKER", false },
		{ IB_SIZE_ZERO, "raw IB_SIZE0", "IBDATA_MARKER_OBSERVED", true },
		{ IB_SIZE_ARBITRARY, "raw IB_SIZE67", "IBDATA_MARKER_OBSERVED", true },
		{ IB_SIZE_MAX, "raw IB_SIZEffffffff", "IBDATA_MARKER_OBSERVED", true },
		{ FIRST_SAMPLE_LATE, "first sample ends late", "IBDATA_NO_TRANSPORT", false },
		{ SECOND_SAMPLE_LAST_VALID, "second sample ends one ns before deadline", "IBDATA_MARKER_OBSERVED", true },
		{ SECOND_SAMPLE_EXACT_DEADLINE, "second sample ends at deadline", "IBDATA_MARKER_UNCONFIRMED", false },
		{ SECOND_SAMPLE_LATE, "second sample ends late", "IBDATA_MARKER_UNCONFIRMED", false },
		{ TIMED_DEADLINE, "read/print costs end by deadline", "IBDATA_MARKER_OBSERVED", true }
	};
	for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
		fresh(cases[i].scenario, cases[i].name);
		run_direct(rb, ib);
		expect_result(cases[i].result, cases[i].confirmed ? 0 : -1, 1, 1,
			cases[i].scenario == FIRST_SAMPLE_LATE ||
			cases[i].scenario == SECOND_SAMPLE_EXACT_DEADLINE ||
			cases[i].scenario == SECOND_SAMPLE_LATE ? "LATE_SAMPLE_NO_CREDIT" :
			"COMPLETED_CLASSIFICATION");
		completion_checks(cases[i].confirmed, true);
		CHECK(result_error == (cases[i].confirmed ? 0 :
			cases[i].scenario == BIT16_NO_MARKER ? -EIO : -ETIMEDOUT),
			"DECLARED_COMPLETED_ERROR_CODE");
		u32 ib_raw = cases[i].scenario == IB_SIZE_ZERO ? 0 :
			cases[i].scenario == IB_SIZE_ARBITRARY ? 67 :
			cases[i].scenario == IB_SIZE_MAX ? UINT32_MAX : 16;
		CHECK(report_number(report_line("END"), "final_ib", 16) == ib_raw &&
			end_number("final_sampled") == 1, "RAW_FINAL_IB_SIZE");
		for (unsigned j = 0; j < observation_records(); ++j)
			CHECK(ps4_ib_data_log[j].sample.core.ib_size == ib_raw, "RAW_RECORD_IB_SIZE");
		if (cases[i].scenario == ONE_JOINT) CHECK(evidence_number("joint_samples") == 1,
			"ONE_JOINT_NOT_SUFFICIENT");
		if (cases[i].scenario == FIRST_SAMPLE_LATE) CHECK(evidence_number("valid_samples") == 0,
			"FIRST_LATE_SAMPLE_NO_CREDIT");
		if (cases[i].scenario == TIMED_DEADLINE) CHECK(observation_records() < 40,
			"NONZERO_COST_DEADLINE_COMPLETION");
	}
}

static void test_terminals(const struct amdgpu_ps4_uvd_ring *rb,
	const struct amdgpu_ps4_uvd_ring *ib)
{
	const struct { enum scenario scenario; const char *name, *result; bool endpoint; } cases[] = {
		{ POINTER_REGRESSION, "pointer regression", "IBDATA_POINTER_REGRESSION", false },
		{ DATA_REGRESSION, "marker regression", "IBDATA_MARKER_REGRESSION", false },
		{ ENDPOINT_DATA_REGRESSION, "endpoint marker regression", "IBDATA_MARKER_REGRESSION", true },
		{ ENDPOINT_POINTER_REGRESSION, "endpoint pointer regression", "IBDATA_POINTER_REGRESSION", true },
		{ FINAL_CORE_GUARD, "final core failure", "IBDATA_FINAL_GUARD", true },
		{ SWEEP_FAILURE, "indexed sweep failure", "IBDATA_CTX_SWEEP_GUARD", true },
		{ UNEXPECTED_DATA, "foreign DATA0", "IBDATA_UNEXPECTED_VALUE", false },
		{ UNEXPECTED_CONTEXT, "changed context", "IBDATA_UNEXPECTED_CONTEXT", false },
		{ BAD_STATUS, "unexpected status bit", "IBDATA_STATUS_UNEXPECTED", false },
		{ BIT16_WITHOUT_READY, "bit16 without ready", "IBDATA_STATUS_UNEXPECTED", false },
		{ AFTER_DEADLINE_GUARD, "terminal guard after deadline", "IBDATA_FINAL_GUARD", true }
	};
	for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
		fresh(cases[i].scenario, cases[i].name);
		run_direct(rb, ib);
		expect_result(cases[i].result, -1, 1, 1,
			cases[i].scenario == BIT16_WITHOUT_READY ? "STATUS_REQUIRES_READY" :
			cases[i].scenario == SWEEP_FAILURE ? "SWEEP_FAILURE_NONZERO" :
			"FAULT_TERMINAL");
		CHECK(end_number("marker_observed") == 0, "FAULT_NEVER_ACCEPTS_MARKER");
		if (cases[i].scenario == SWEEP_FAILURE) {
			CHECK(submitted_selector_writes == 4 && end_number("sweep_ran") == 1 &&
				end_number("sweep_valid") == 0, "SWEEP_FAILURE_SAVED");
			completion_checks(true, false);
		} else CHECK(submitted_selector_writes == 0, "NO_SWEEP_AFTER_ERROR");
		if (!cases[i].endpoint) {
			CHECK(end_number("final_sampled") == 0, "FAULT_NO_ENDPOINT_SAMPLE");
			if (cases[i].scenario != POINTER_REGRESSION && cases[i].scenario != DATA_REGRESSION)
				CHECK(!ps4_ib_data_log[0].valid && evidence_number("valid_samples") == 0,
					"INVALID_SAMPLE_NEVER_CREDITED");
			CHECK(!strstr(messages, "PS4_UVD_IBDATA FINAL_CORE "), "ERROR_NO_FINAL_RESAMPLE");
			CHECK(strstr(messages, "PS4_UVD_IBDATA SAVED ") != NULL, "ERROR_SAVED_SAMPLE");
		}
	}
}

static void test_direct_guard_coverage(void)
{
	const struct { u32 reg, value; } fields[] = {
		{ mmUVD_MASTINT_EN, 0 }, { mmUVD_RBC_RB_CNTL, 0x1101080c },
		{ mmUVD_RBC_RB_WPTR, 12 }, { mmUVD_RBC_RB_RPTR, 17 },
		{ mmUVD_RBC_RB_WPTR_CNTL, 1 }, { mmUVD_RBC_RB_RPTR_ADDR, 4 },
		{ mmVM_CONTEXT1_PROTECTION_FAULT_STATUS, 1 },
		{ mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + 4, 0 },
		{ PS4_READY_UVD_VCPU_CACHE_VMID, 0 },
		{ mmUVD_LMI_RBC_RB_VMID, 0 }, { mmUVD_LMI_RBC_IB_VMID, 0 },
		{ mmUVD_LMI_VCPU_CACHE_64BIT_BAR_LOW, 4 },
		{ mmUVD_LMI_VCPU_CACHE_64BIT_BAR_HIGH, 0 },
		{ mmUVD_LMI_RBC_RB_64BIT_BAR_LOW, 4 },
		{ mmUVD_LMI_RBC_RB_64BIT_BAR_HIGH, 0 },
		{ mmUVD_LMI_RBC_IB_64BIT_BAR_LOW, 4 },
		{ mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH, 0 },
		{ mmUVD_VCPU_CACHE_OFFSET0, 1 }, { mmUVD_VCPU_CACHE_SIZE0, 0 },
		{ mmUVD_VCPU_CACHE_OFFSET1, 0 }, { mmUVD_VCPU_CACHE_SIZE1, 0 },
		{ mmUVD_VCPU_CACHE_OFFSET2, 0 }, { mmUVD_VCPU_CACHE_SIZE2, 0 },
		{ mmUVD_LMI_SWAP_CNTL, 1 }, { mmUVD_MP_SWAP_CNTL, 1 },
		{ mmUVD_LMI_ADDR_EXT, 1 }, { mmUVD_LMI_EXT40_ADDR, 1 },
		{ mmUVD_SOFT_RESET, PS4_READY_HOLD },
		{ mmUVD_LMI_CTRL2, UVD_LMI_CTRL2__STALL_ARB_UMC_MASK }
	};
	struct amdgpu_ps4_uvd_ring rb = ring_descriptor(), ib = ib_descriptor();
	for (unsigned i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i) {
		fresh(MUTATE_SUBMITTED, "direct guard mutation");
		mutation_reg = fields[i].reg; mutation_value = fields[i].value;
		run_direct(&rb, &ib);
		expect_result("IBDATA_CORE_GUARD", -1, 1, 1, "DIRECT_GUARD_COVERAGE");
		CHECK(ps4_ib_data_log[0].sample.core.failed &&
			ps4_ib_data_log[0].sample.core.bad_reg == fields[i].reg,
			"DIRECT_GUARD_DECIDING_FIELD");
		CHECK(!submitted_selector_writes, "GUARD_NO_ENDPOINT_WRITES");
	}
	for (unsigned which = 1; which <= 4; ++which) {
		fresh(MUTATE_SUBMITTED, "driver state guard mutation");
		driver_mutation = which;
		run_direct(&rb, &ib);
		expect_result("IBDATA_CORE_GUARD", -1, 1, 1, "DRIVER_GUARD_COVERAGE");
		CHECK(ps4_ib_data_log[0].sample.core.bad_kind == 2 &&
			ps4_ib_data_log[0].sample.core.bad_reg == which, "DRIVER_GUARD_DECIDING_FIELD");
	}
}

int main(int argc, char **argv)
{
	struct amdgpu_ps4_uvd_ring rb = ring_descriptor(), ib = ib_descriptor();
	test_gates_and_descriptors();
	test_owner();
	test_prepublication(&rb, &ib);
	test_completed(&rb, &ib);
	test_terminals(&rb, &ib);
	test_direct_guard_coverage();
	fresh(NORMAL, "saved literal images");
	run_direct(&rb, &ib);
	expect_result("IBDATA_MARKER_OBSERVED", 0, 1, 1, "FINAL_NORMAL_RUN");
	check_images();
	if (argc == 3) {
		const unsigned char *images[] = { ring_bytes, ib_bytes };
		for (unsigned i = 0; i < 2; ++i) {
			FILE *out = fopen(argv[i + 1], "wb");
			CHECK(out && fwrite(images[i], 1, 4096, out) == 4096 && !fclose(out),
				"SAVE_LITERAL_IMAGE");
		}
	} else CHECK(argc == 1, "FIXTURE_ARGUMENTS");
	printf("PASS: %u actual-source cases; controlled MMIO/time, no hardware emulation\n", cases_run);
	return 0;
}
