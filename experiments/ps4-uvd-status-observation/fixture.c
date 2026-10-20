/* SPDX-License-Identifier: GPL-2.0-only */
/* Synthetic MMIO/time harness for the planned status-observation flow
 * (Revision C) over the frozen data-control candidate. All ACTUAL_* blocks
 * are extracted from the actual candidate source. Literal publication and
 * ring-image assertions are independent of producer macros. This is not
 * hardware or firmware emulation.
 * Revision B: deadline wins before sampling; error terminals take no
 * follow-up MMIO (no FINAL_CORE/CTX_SWEEP); completed outcomes run the
 * tolerant terminal sample and the post-window indexed sweep once.
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
/* ACTUAL_REGISTER_DEFINITIONS */
/* ACTUAL_READY_DEFINITIONS */
/* ACTUAL_DESCRIPTOR */

struct amdgpu_device {
	void *dev;
	struct { int buffer_funcs_enabled; } mman;
	struct { struct { unsigned long lock; } uvd_ctx; } reg;
	int gpu_reset_counter, accel_working, in_reset;
};
static bool ps4_uvd_ready, ps4_uvd_poll_state, ps4_uvd_token,
	ps4_uvd_data_control, ps4_uvd_status_observation;
static u32 registers[0x10000], indexed[0x1000];
static _Alignas(8) unsigned char ring_bytes[4096];
static unsigned data_writes, context_writes, fetch_writes, pointer_writes,
	selector_writes, empty_reads;
static unsigned held_samples, submitted_samples, sleeps, barriers,
	hdp_calls, clock_calls;
static unsigned submitted_selector_writes, prepublication_selector_writes;
static bool enabled, submitted, loop_active, terminal_reporting, publication_announced;
static u64 now_ns, budget_start, pd_argument;
static u64 read_cost_ns, write_cost_ns, published_at_ns, log_ns_per_char, sleep_extra_ns;
static bool sleep_uses_high;
static jmp_buf terminal;
static const char *result_name;
static int result_error;
static char messages[49152];
static size_t message_size;
static unsigned cases_run;
static struct amdgpu_device device;
static unsigned observation_records(void);
static unsigned contract_failures, contract_reported;

/* Keep collecting independent synthetic findings, then fail the complete
 * invocation. A failed contract can never produce a passing receipt. */
static void contract_check(bool holds, unsigned flag, const char *description)
{
	if (holds) return;
	++contract_failures;
	if (!(contract_reported & flag)) {
		fprintf(stderr, "CONTRACT FAILURE: %s\n", description);
		contract_reported |= flag;
	}
}

enum scenario {
	NORMAL, NORMAL_REPLAY, BIT16_LATE_ONSET, BIT16_TRANSIENT,
	BIT16_WITHOUT_MARKERS, BIT16_ABSENT_MARKERS, BIT16_ABSENT_NO_MARKERS,
	SLOW_DEADLINE, STATUS_UNEXPECTED_EXTRA, LATE_CORE_GUARD, CORE_GUARD_RBC,
	SWEEP_DRIFT, RP_OVERRUN, RP_REGRESSION, UNEXPECTED_VALUE,
	UNEXPECTED_CONTEXT, BIT16_TOGGLING, PREPUB_BIT16, WPTR_BIT16,
	WPTR_BIT16_EXTRA, ENABLE_READBACK, ENABLE_EXACT_DEADLINE,
	BEFORE_SUBMIT_EXACT_DEADLINE, PRE_READ_EXACT_DEADLINE,
	PUBLICATION_EXACT_DEADLINE, DATA_DIRECT_FAIL, CONTEXT_DIRECT_FAIL,
	HELD_DATA_CHANGED, HELD_CONTEXT_CHANGED, EMPTY_DATA_CHANGED,
	STALE_DATA, STALE_CONTEXT, READY_CONVENTION, PREPUBLISH_GUARD,
	POSTSUBMIT_GUARD, SLOW_PREPUBLICATION_READS, MARKER_REGRESSIONS,
	FINAL_STATUS_GUARD, FINAL_RBC_GUARD, TIMED_BUDGET, TIMED_DEADLINE,
	BIT16_THEN_GUARD
};
static enum scenario scenario;
enum mutation_kind { MUT_REG, MUT_INDEXED, MUT_RESET, MUT_IN_RESET, MUT_BUFFER, MUT_ACCEL };
static enum mutation_kind mutation_kind;
static u32 mutation_reg, mutation_value;
static void unknown_prepublication_statuses(void);

static void log_message(void *ignored, const char *format, ...)
{
	va_list args;
	int count;
	(void)ignored;
	if (submitted) terminal_reporting = true;
	/* Only terminal paths may log after polling has begun. */
	if (loop_active)
		assert(strstr(format, " CORE ") || strstr(format, " GUARD ") ||
		       strstr(format, "PS4_UVD_STOBS GUARD_FULL ") ||
		       strstr(format, " END ") || strstr(format, " REC ") ||
		       strstr(format, " FINAL_CORE ") ||
		       strstr(format, " FINAL_GUARD ") ||
		       strstr(format, " CTX_SWEEP "));
	va_start(args, format);
	count = vsnprintf(messages + message_size, sizeof(messages) - message_size, format, args);
	va_end(args);
	assert(count >= 0 && (size_t)count < sizeof(messages) - message_size);
	message_size += (size_t)count;
	now_ns += (u64)count * log_ns_per_char;
	if (strstr(format, "PS4_UVD_STOBS PUBLICATION_BEGIN "))
		publication_announced = true;
	if (scenario == SLOW_DEADLINE && strstr(format, " ENABLE_BEGIN "))
		now_ns = budget_start + UINT64_C(1000000);
	if (scenario == SLOW_PREPUBLICATION_READS &&
	    strstr(format, "PS4_UVD_STOBS PUBLICATION_BEGIN "))
		read_cost_ns = UINT64_C(10000000);
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

/* Independent literal full-page parser: no writer metadata or producer macros. */
static void check_page(void)
{
	for (unsigned offset = 0; offset < 4096; offset += 8) {
		u32 header = decode_word(ring_bytes + offset);
		u32 data = decode_word(ring_bytes + offset + 4);

		if (offset == 0)
			assert(header == 0x3bc4 && data == 0x55564435);
		else if (offset == 8)
			assert(header == 0x3dbd && data == 0x43545835);
		else
			assert(header == 0x3bff && data == 0);
		assert((header >> 30) == 0 && ((header >> 16) & 0x3fff) == 0);
	}
	assert((crc32_le(~0U, ring_bytes, sizeof(ring_bytes)) ^ ~0U) == 0xcadc2d79U);
}

static void apply_mutation(void)
{
	switch (mutation_kind) {
	case MUT_REG: registers[mutation_reg] = mutation_value; break;
	case MUT_INDEXED: indexed[mutation_reg] = mutation_value; break;
	case MUT_RESET: device.gpu_reset_counter = (int)mutation_value; break;
	case MUT_IN_RESET: device.in_reset = (int)mutation_value; break;
	case MUT_BUFFER: device.mman.buffer_funcs_enabled = (int)mutation_value; break;
	case MUT_ACCEL: device.accel_working = (int)mutation_value; break;
	}
}

static void submitted_state(void)
{
	++submitted_samples;
	loop_active = true;
	/* Steady defaults; scenarios override by record count. */
	registers[mmUVD_RBC_RB_WPTR] = 16;
	registers[mmUVD_MASTINT_EN] = 2;
	registers[mmUVD_STATUS] = 2;
	registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564435;
	registers[mmUVD_CONTEXT_ID] = 0x43545835;
	registers[mmUVD_RBC_RB_RPTR] = 16;
	switch (scenario) {
	case NORMAL_REPLAY:
		registers[mmUVD_STATUS] = 0x00010002;
		break;
	case BIT16_LATE_ONSET:
		registers[mmUVD_STATUS] = submitted_samples >= 12 ? 0x00010002 : 2;
		break;
	case BIT16_TRANSIENT:
		registers[mmUVD_STATUS] = submitted_samples <= 9 ? 0x00010002 : 2;
		break;
	case BIT16_TOGGLING:
		registers[mmUVD_STATUS] =
			(submitted_samples <= 6 || submitted_samples >= 11) ?
			0x00010002 : 2;
		break;
	case BIT16_WITHOUT_MARKERS:
		registers[mmUVD_STATUS] = 0x00010002;
		registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504435;
		registers[mmUVD_CONTEXT_ID] = 0x43504335;
		registers[mmUVD_RBC_RB_RPTR] = 0;
		break;
	case BIT16_ABSENT_NO_MARKERS:
	case SLOW_DEADLINE:
		registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504435;
		registers[mmUVD_CONTEXT_ID] = 0x43504335;
		registers[mmUVD_RBC_RB_RPTR] = 0;
		break;
	case STATUS_UNEXPECTED_EXTRA:
		if (submitted_samples >= 3)
			registers[mmUVD_STATUS] = 0x00020002;
		break;
	case LATE_CORE_GUARD:
		if (submitted_samples >= 12)
			registers[mmUVD_RBC_RB_CNTL] = 0x1101080c;
		break;
	case CORE_GUARD_RBC:
		if (submitted_samples >= 2)
			registers[mmUVD_RBC_RB_CNTL] = 0x1101080c;
		break;
	case SWEEP_DRIFT:
		if (submitted_samples >= 2)
			registers[mmUVD_LMI_RBC_RB_VMID] = 0;
		break;
	case RP_OVERRUN:
		if (submitted_samples >= 2)
			registers[mmUVD_RBC_RB_RPTR] = 20;
		break;
	case RP_REGRESSION:
		if (submitted_samples == 2)
			registers[mmUVD_RBC_RB_RPTR] = 12;
		break;
	case UNEXPECTED_VALUE:
		if (submitted_samples >= 2)
			registers[mmUVD_GPCOM_VCPU_DATA0] = 0x12345678;
		break;
	case UNEXPECTED_CONTEXT:
		if (submitted_samples >= 2)
			registers[mmUVD_CONTEXT_ID] = 0x87654321;
		break;
	case POSTSUBMIT_GUARD:
		apply_mutation();
		break;
	case MARKER_REGRESSIONS:
		if (submitted_samples <= 5 ||
		    (submitted_samples >= 11 && submitted_samples <= 20) ||
		    submitted_samples >= 31)
			registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504435;
		if (submitted_samples <= 12 || submitted_samples >= 25)
			registers[mmUVD_CONTEXT_ID] = 0x43504335;
		break;
	case FINAL_STATUS_GUARD:
		if (submitted_samples == 41) registers[mmUVD_STATUS] = 0x20002;
		break;
	case FINAL_RBC_GUARD:
		if (submitted_samples == 41) registers[mmUVD_RBC_RB_CNTL] = 0x1101080c;
		break;
	case BIT16_THEN_GUARD:
		if (submitted_samples <= 10) registers[mmUVD_STATUS] = 0x10002;
		else registers[mmUVD_RBC_RB_CNTL] = 0x1101080c;
		break;
	default:
		break;
	}
}

static u32 mmio_read(u32 reg)
{
	assert(reg < 0x10000);
	contract_check(!submitted || !terminal_reporting, 1,
		"MMIO read after post-publication reporting started");
	now_ns += read_cost_ns;
	if (reg == mmUVD_STATUS) {
		if (submitted) submitted_state();
		else if (!enabled) ++held_samples;
		if (held_samples == 2 && !enabled) {
			if (scenario == HELD_DATA_CHANGED)
				registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564435;
			if (scenario == HELD_CONTEXT_CHANGED)
				registers[mmUVD_CONTEXT_ID] = 0x43545835;
		}
		if (enabled && !submitted) {
			++empty_reads;
			if (scenario == PREPUB_BIT16)
				registers[mmUVD_STATUS] = 0x00010002;
			if ((scenario == WPTR_BIT16 || scenario == WPTR_BIT16_EXTRA) &&
			    empty_reads >= 2)
				registers[mmUVD_STATUS] = scenario == WPTR_BIT16 ?
					0x00010002U : 0x00020002U;
			if (scenario == EMPTY_DATA_CHANGED)
				registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564435;
			if (scenario == PREPUBLISH_GUARD) apply_mutation();
		}
	}
	if (reg == mmUVD_CTX_DATA) return indexed[registers[mmUVD_CTX_INDEX]];
	return registers[reg];
}

static void mmio_write(u32 reg, u32 value)
{
	assert(reg < 0x10000);
	contract_check(!submitted || !terminal_reporting, 1,
		"MMIO write after post-publication reporting started");
	if (reg == mmUVD_CTX_INDEX) {
		assert(value < 0x1000);
		++selector_writes;
		if (submitted) {
			++submitted_selector_writes;
			contract_check(observation_records() == 40 ||
				now_ns - budget_start >= UINT64_C(250000000), 2,
				"CTX_INDEX write inside the observation window");
		} else {
			contract_check(!publication_announced, 256,
				"selector write in the direct pre-publication sample");
		}
	} else if (reg == mmUVD_GPCOM_VCPU_DATA0) {
		assert(ps4_uvd_status_observation && !ps4_uvd_data_control &&
		       value == 0x43504435);
		assert(!enabled && !submitted && !data_writes++ && !context_writes);
		if (scenario == DATA_DIRECT_FAIL) value = 0;
	} else if (reg == mmUVD_CONTEXT_ID) {
		assert(value == 0x43504335U);
		assert(!enabled && !submitted && !context_writes++);
		assert(data_writes == 1);
		if (scenario == CONTEXT_DIRECT_FAIL) value = 0;
	} else if (reg == mmUVD_RBC_RB_CNTL) {
		assert(value == 0x1100080c && registers[reg] == 0x1101080c);
		assert(!enabled && !submitted && !fetch_writes++);
		assert(registers[mmUVD_RBC_RB_WPTR] == 0);
		assert(context_writes == 1 && hdp_calls == 1 && barriers >= 1);
		assert(data_writes == 1);
		check_page();
		budget_start = now_ns;
		enabled = true;
		if (scenario == ENABLE_READBACK) value = 0x1101080c;
	} else if (reg == mmUVD_RBC_RB_WPTR) {
		assert(value == 16 && enabled && !submitted && !pointer_writes++);
		assert(barriers >= 2 && now_ns - budget_start < UINT64_C(250000000));
		prepublication_selector_writes = selector_writes;
		contract_check(prepublication_selector_writes == 24, 512,
			"pre-publication selector-store count must equal 24");
		check_page();
		submitted = true;
	} else {
		assert(!"forbidden MMIO write: includes CMD even zero, DATA1, IB, trap, reset, IRQ enable");
	}
	registers[reg] = value;
	now_ns += write_cost_ns;
	if (reg == mmUVD_RBC_RB_WPTR) published_at_ns = now_ns;
}
#define RREG32(reg) mmio_read(reg)
#define WREG32(reg,value) mmio_write(reg,value)

static bool amdgpu_in_reset(struct amdgpu_device *adev) { return adev->in_reset; }
static u64 ktime_get_ns(void)
{
	++clock_calls;
	if ((scenario == ENABLE_EXACT_DEADLINE && clock_calls == 2) ||
	    (scenario == BEFORE_SUBMIT_EXACT_DEADLINE && clock_calls == 3) ||
	    (scenario == PRE_READ_EXACT_DEADLINE && clock_calls == 4) ||
	    (scenario == PUBLICATION_EXACT_DEADLINE && clock_calls == 6))
		now_ns = budget_start + UINT64_C(250000000);
	return now_ns;
}
static void mb(void) { ++barriers; }
static void amdgpu_device_flush_hdp(struct amdgpu_device *adev, void *ring)
{ (void)adev; (void)ring; ++hdp_calls; }
static void usleep_range(unsigned low, unsigned high)
{
	assert((low == 100 && high == 200) || (low == 12000 && high == 12500));
	++sleeps;
	now_ns += scenario == SLOW_DEADLINE ? UINT64_C(20000000) :
		(u64)(sleep_uses_high ? high : low) * 1000 + sleep_extra_ns;
}
static __noreturn void ps4_ready_park(struct amdgpu_device *adev, const char *name, int error)
{ (void)adev; result_name = name; result_error = error; longjmp(terminal, 1); }
static void ps4_ready_snapshot(struct amdgpu_device *adev, const char *name)
{ (void)adev; (void)name; }

/* ACTUAL_CONTEXT_READER */
/* ACTUAL_TOKEN_FLOW */
/* ACTUAL_DATA_CONTROL_FLOW */
/* ACTUAL_STATUS_OBSERVATION_FLOW */

static unsigned observation_records(void) { return ps4_stobs_record_count; }

static void receiver_preflight(struct amdgpu_device *adev,
	const struct amdgpu_ps4_uvd_ring *rb, u64 pd_addr)
{
	/* ACTUAL_EARLY_GATE */
}

static void dispatch(struct amdgpu_device *adev, const struct amdgpu_ps4_uvd_ring *rb)
{
	u32 status = 2, cache_vmid_expected = 4, baseline_fault = 0;
	int reset_count = 0;
	u64 pd_addr = pd_argument, elapsed = 42246000;
	struct { u32 mastint; } guard_sample = { 2 };
	/* ACTUAL_READY_DISPATCH */
	assert(!"expected terminal dispatch");
}

static void fresh(enum scenario next)
{
	memset(registers, 0, sizeof(registers));
	memset(indexed, 0, sizeof(indexed));
	memset(&device, 0, sizeof(device));
	memset(ring_bytes, 0xa5, sizeof(ring_bytes));
	message_size = 0; messages[0] = 0; result_name = NULL; result_error = 99;
	data_writes = context_writes = fetch_writes = pointer_writes = selector_writes = 0;
	empty_reads = 0;
	submitted_selector_writes = prepublication_selector_writes = 0;
	held_samples = submitted_samples = sleeps = barriers = hdp_calls = clock_calls = 0;
	now_ns = budget_start = 0; pd_argument = 0x100000;
	read_cost_ns = write_cost_ns = published_at_ns = 0;
	log_ns_per_char = sleep_extra_ns = 0;
	sleep_uses_high = publication_announced = false;
	ps4_stobs_record_count = 0;
	ps4_stobs_publish_ns = 0;
	memset(ps4_stobs_log, 0, sizeof(ps4_stobs_log));
	enabled = submitted = loop_active = terminal_reporting = false;
	scenario = next;
	mutation_kind = MUT_REG; mutation_reg = mutation_value = 0;
	ps4_uvd_ready = ps4_uvd_poll_state = ps4_uvd_token = true;
	ps4_uvd_data_control = false;
	ps4_uvd_status_observation = true;
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
	indexed[ixUVD_LMI_VMID_INTERNAL & 0xfff] = 0x00444444;
	indexed[ixUVD_LMI_VMID_INTERNAL2 & 0xfff] = 0x44444444;
	indexed[ixUVD_LMI_VMID_INTERNAL3 & 0xfff] = 0x4444;
	if (next == STALE_DATA) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564435;
	if (next == STALE_CONTEXT) registers[mmUVD_CONTEXT_ID] = 0x43545835;
}

static struct amdgpu_ps4_uvd_ring descriptor(void)
{
	struct amdgpu_ps4_uvd_ring rb = { ring_bytes, 4096, UINT64_C(0x300308000) };
	return rb;
}

static void expect_result(const char *expected, unsigned fetch, unsigned pointer)
{
	if (!result_name || strcmp(result_name, expected)) {
		fprintf(stderr, "expected %s, got %s\n%s", expected, result_name, messages);
		abort();
	}
	if (fetch_writes != fetch || pointer_writes != pointer) {
		fprintf(stderr, "scenario=%d result=%s fetch=%u expected=%u publication=%u expected=%u\n%s",
			scenario, result_name, fetch_writes, fetch, pointer_writes, pointer,
			messages);
		abort();
	}
	assert(registers[mmUVD_CTX_INDEX] == 0x55);
	assert(fetch_writes + pointer_writes == 0 ||
	       (data_writes == 1 && context_writes == 1 && hdp_calls >= 1 &&
		barriers >= 1));
	assert(!pointer_writes || barriers >= 2);
	++cases_run;
}

static void run_case(const struct amdgpu_ps4_uvd_ring *rb, const char *expected,
	unsigned expected_fetch, unsigned expected_pointer)
{
	if (!setjmp(terminal)) dispatch(&device, rb);
	expect_result(expected, expected_fetch, expected_pointer);
}

static void run_direct(const struct amdgpu_ps4_uvd_ring *rb, const char *expected)
{
	if (!setjmp(terminal)) ps4_stobs_run(&device, rb, pd_argument, 4, 0, 0);
	expect_result(expected, 0, 0);
}

/* Keep setjmp out of the caller's iteration/counter scope. */
static void run_direct_complete(const struct amdgpu_ps4_uvd_ring *rb)
{
	if (!setjmp(terminal)) ps4_stobs_run(&device, rb, pd_argument, 4, 0, 0);
	expect_result("STOBS_BIT16_ABSENT_MARKERS", 1, 1);
}

static void run_receiver(const struct amdgpu_ps4_uvd_ring *rb, const char *expected)
{
	if (!setjmp(terminal)) {
		receiver_preflight(&device, rb, pd_argument);
		assert(!"ear gate passed unexpectedly");
	}
	expect_result(expected, 0, 0);
}

/* Parse a field from exactly one report line. Matching a prose fragment or
 * a different record must not satisfy an assertion about this record. */
static int64_t report_number(const char *line, const char *key, int base)
{
	char needle[80], *end;
	const char *found, *limit;
	int64_t value;

	assert(line);
	limit = strchr(line, '\n');
	assert(limit);
	snprintf(needle, sizeof(needle), " %s=", key);
	found = strstr(line, needle);
	if (!found || found >= limit) {
		fprintf(stderr, "missing field %s in %.300s\n", key, line);
		abort();
	}
	found += strlen(needle);
	errno = 0;
	value = strtoll(found, &end, base);
	assert(!errno && end != found && end <= limit);
	assert(*end == ' ' || *end == ';' || *end == '\n');
	return value;
}

static const char *record_line(unsigned index)
{
	char tag[80];
	const char *line;

	snprintf(tag, sizeof(tag), "PS4_UVD_STOBS REC i=%u ", index);
	line = strstr(messages, tag);
	assert(line && !strstr(line + strlen(tag), tag));
	return line;
}

static int64_t end_number(const char *key)
{
	const char *line = strstr(messages, "PS4_UVD_STOBS END ");
	assert(line && !strstr(line + 1, "PS4_UVD_STOBS END "));
	return report_number(line, key, 10);
}

static void expect_log_class(const char *expected)
{
	const char *line = strstr(messages, "PS4_UVD_STOBS END ");
	const char *field, *limit;
	assert(line);
	limit = strchr(line, '\n');
	assert(limit);
	field = strstr(line, " log_class=");
	if (!field || field >= limit ||
	    strncmp(field + 11, expected, strlen(expected)) ||
	    field[11 + strlen(expected)] != ' ') {
		fprintf(stderr, "unexpected log_class, expected %s\n%s", expected, messages);
		abort();
	}
}

static void saved_sample_checks(bool guard_failed)
{
	const char *full = strstr(messages, "PS4_UVD_STOBS GUARD_FULL ");
	const char *guard = strstr(messages, "PS4_UVD_STOBS GUARD kind=");
	contract_check(guard_failed ? guard != NULL : guard == NULL, 1024,
		guard_failed ? "failed sample omitted its GUARD record" :
		"non-guard stop emitted a GUARD record");
	contract_check(report_number(full, "valid", 10) == !guard_failed, 2048,
		"saved sample validity disagrees with the deciding guard state");
}

/* Completed outcomes: terminal sample and post-window sweep precede the
 * record lines; no crediting vocabulary anywhere in the message stream.
 */
static void completion_checks(unsigned records)
{
	unsigned count = 0;
	const char *p_final, *p_rec;

	assert(end_number("records") == records);
	assert(end_number("fine") == (records < 24 ? records : 24));
	assert(end_number("publish_ns") >= 0 && end_number("accepted") == 0);
	assert(strstr(messages, "crc=cadc2d79"));
	assert(strstr(messages, "ENABLE_BEGIN ") &&
	       strstr(messages, "PUBLICATION_BEGIN "));
	assert(strstr(messages, "CTX_SWEEP valid=1"));
	contract_check(prepublication_selector_writes == 24, 512,
		"pre-publication selector-store count must equal 24");
	contract_check(submitted_selector_writes == 8, 4,
		"completed indexed sweep did not perform exactly four save/select/restore pairs");
	assert(strstr(messages, "FINAL_CORE sampled=all_then_data0") &&
	       strstr(messages, "final_valid=1"));
	assert(!strstr(messages, "FINAL_GUARD"));
	p_final = strstr(messages, "PS4_UVD_STOBS FINAL_CORE");
	p_rec = strstr(messages, "PS4_UVD_STOBS REC i=");
	assert(p_final && p_rec && p_final < p_rec);
	for (const char *p = p_rec; p; p = strstr(p + 1, "PS4_UVD_STOBS REC i="))
		++count;
	assert(count == records);
	assert(!strstr(messages, "consecutive=") && !strstr(messages, "joint_samples=") &&
	       !strstr(messages, "data_seen=") && !strstr(messages, "transport_seen=") &&
	       !strstr(messages, "context_seen="));
}

/* Error terminals: no follow-up MMIO at all; the saved deciding sample is
 * printed in full; the terminal reason is preserved (owner + review B2/B5).
 */
static void guard_checks(const char *first, unsigned records, bool full_sample)
{
	unsigned count = 0;

	assert(strstr(messages, first));
	if (full_sample)
		saved_sample_checks(scenario != RP_REGRESSION);
	assert(!strstr(messages, "FINAL_CORE") && !strstr(messages, "CTX_SWEEP"));
	contract_check(submitted_selector_writes == 0, 8,
		"error terminal performed indexed MMIO after publication");
	assert(strstr(messages, "final_valid=0") &&
	       strstr(messages, "sweep_valid=0"));
	for (const char *p = strstr(messages, "PS4_UVD_STOBS REC i=");
	     p; p = strstr(p + 1, "PS4_UVD_STOBS REC i="))
		++count;
	assert(count == records);
}

static void test_gates_and_descriptors(void)
{
	struct amdgpu_ps4_uvd_ring rb = descriptor();
	unsigned guard_masks = 0, full_runs = 0;
	for (unsigned mask = 0; mask < 32; ++mask) {
		if (!(mask & 8))
			continue;
		++guard_masks;
		fresh(NORMAL);
		ps4_uvd_ready = !!(mask & 1); ps4_uvd_poll_state = !!(mask & 2);
		ps4_uvd_token = !!(mask & 4); ps4_uvd_status_observation = true;
		ps4_uvd_data_control = !!(mask & 16);
		if (ps4_uvd_data_control) {
			run_receiver(&rb, "STOBS_OPTIONS_EXCLUSIVE");
			assert(!fetch_writes && !pointer_writes && !held_samples);
		} else if (ps4_uvd_token && ps4_uvd_ready && ps4_uvd_poll_state) {
			++full_runs;
			run_direct_complete(&rb);
			assert(result_error == 0 && pointer_writes == 1 &&
			       fetch_writes == 1 && data_writes == 1 &&
			       context_writes == 1 && selector_writes > 0);
			completion_checks(40);
			assert(end_number("bit16_first") == -1 && end_number("bit16_clears") == 0);
			assert(end_number("markers") == 1 && end_number("persistent") == 0);
			assert(report_number(record_line(0), "elapsed_us", 10) == 0);
			assert(report_number(record_line(0), "status", 16) == 2);
			assert(report_number(record_line(23), "elapsed_us", 10) == 2300);
			assert(report_number(record_line(24), "elapsed_us", 10) == 14300);
			assert(report_number(record_line(39), "elapsed_us", 10) == 194300);
			assert(report_number(record_line(39), "t_end_us", 10) == 194300);
			assert(!strstr(messages, "SUBMITTED raw_wptr=16 once"));
			assert(sleeps == 39 && submitted_samples == 41);
		} else {
			run_direct(&rb, "STOBS_DESCRIPTOR_OR_GATES");
			assert(!data_writes && !context_writes && !selector_writes);
			assert(!fetch_writes && !pointer_writes && !held_samples);
		}
	}
	assert(guard_masks == 16 && full_runs == 1);
	fresh(NORMAL); run_direct(NULL, "STOBS_DESCRIPTOR_OR_GATES");
	for (unsigned which = 0; which < 6; ++which) {
		fresh(NORMAL); rb = descriptor();
		switch (which) {
		case 0: rb.cpu = NULL; break;
		case 1: rb.cpu = ring_bytes + 1; break;
		case 2: rb.cpu = (void *)(~0UL - 3UL); break;
		case 3: rb.bytes = 4095; break;
		case 4: rb.bytes = 8192; break;
		case 5: rb.private_va += 4096; break;
		}
		run_direct(&rb, "STOBS_DESCRIPTOR_OR_GATES");
	}
	rb = descriptor();
	for (unsigned which = 0; which < 3; ++which) {
		fresh(NORMAL);
		pd_argument = which == 0 ? 0 : which == 1 ? 0x100001 : UINT64_C(0x100000000000);
		run_direct(&rb, "STOBS_DESCRIPTOR_OR_GATES");
	}
	pd_argument = 0x100000;
	/* No params at all: the observed ready convention still parks. */
	fresh(READY_CONVENTION);
	ps4_uvd_status_observation = ps4_uvd_token = false;
	run_case(&rb, "READY_CONVENTION_OBSERVED", 0, 0);
	assert(!data_writes && !context_writes && !selector_writes);
}

static void test_guards(void)
{
	const struct { enum mutation_kind kind; u32 reg, value; } mutations[] = {
		{ MUT_REG, mmUVD_STATUS, 0x10002 }, { MUT_REG, mmUVD_STATUS, 6 },
		{ MUT_REG, mmUVD_STATUS, 0x10006 }, { MUT_REG, mmUVD_STATUS, 0x10000 },
		{ MUT_REG, mmUVD_STATUS, 0x30002 }, { MUT_REG, mmUVD_STATUS, 0x80010002 },
		{ MUT_REG, mmUVD_MASTINT_EN, 0 }, { MUT_REG, mmUVD_RBC_RB_CNTL, 0x1000080c },
		{ MUT_REG, mmUVD_RBC_IB_SIZE, 1 }, { MUT_REG, mmUVD_RBC_RB_WPTR, 0 },
		{ MUT_REG, mmUVD_RBC_RB_RPTR, 64 }, { MUT_REG, mmUVD_RBC_RB_WPTR_CNTL, 1 },
		{ MUT_REG, mmUVD_RBC_RB_RPTR_ADDR, 1 },
		{ MUT_REG, mmVM_CONTEXT1_PROTECTION_FAULT_STATUS, 1 },
		{ MUT_REG, mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + 4, 0 },
		{ MUT_REG, PS4_READY_UVD_VCPU_CACHE_VMID, 5 },
		{ MUT_REG, mmUVD_LMI_RBC_RB_VMID, 0 }, { MUT_REG, mmUVD_LMI_RBC_IB_VMID, 0 },
		{ MUT_REG, mmUVD_LMI_VCPU_CACHE_64BIT_BAR_LOW, 1 },
		{ MUT_REG, mmUVD_LMI_VCPU_CACHE_64BIT_BAR_HIGH, 0 },
		{ MUT_REG, mmUVD_LMI_RBC_RB_64BIT_BAR_LOW, 0 },
		{ MUT_REG, mmUVD_LMI_RBC_RB_64BIT_BAR_HIGH, 0 },
		{ MUT_REG, mmUVD_LMI_RBC_IB_64BIT_BAR_LOW, 0 },
		{ MUT_REG, mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH, 0 },
		{ MUT_REG, mmUVD_VCPU_CACHE_OFFSET0, 1 }, { MUT_REG, mmUVD_VCPU_CACHE_SIZE0, 0 },
		{ MUT_REG, mmUVD_VCPU_CACHE_OFFSET1, 0 }, { MUT_REG, mmUVD_VCPU_CACHE_SIZE1, 0 },
		{ MUT_REG, mmUVD_VCPU_CACHE_OFFSET2, 0 }, { MUT_REG, mmUVD_VCPU_CACHE_SIZE2, 0 },
		{ MUT_REG, mmUVD_LMI_SWAP_CNTL, 1 }, { MUT_REG, mmUVD_MP_SWAP_CNTL, 1 },
		{ MUT_REG, mmUVD_LMI_ADDR_EXT, 1 }, { MUT_REG, mmUVD_LMI_EXT40_ADDR, 1 },
		{ MUT_REG, mmUVD_SOFT_RESET, UVD_SOFT_RESET__RBC_SOFT_RESET_MASK },
		{ MUT_REG, mmUVD_LMI_CTRL2, UVD_LMI_CTRL2__STALL_ARB_UMC_MASK },
		{ MUT_INDEXED, ixUVD_LMI_VMID_INTERNAL, 0 },
		{ MUT_INDEXED, ixUVD_LMI_VMID_INTERNAL2, 0 },
		{ MUT_INDEXED, ixUVD_LMI_VMID_INTERNAL3, 0 },
		{ MUT_INDEXED, ixUVD_LMI_CACHE_CTRL, UVD_LMI_CACHE_CTRL__VCPU_EN_MASK },
		{ MUT_RESET, 1, 1 }, { MUT_IN_RESET, 2, 1 },
		{ MUT_BUFFER, 3, 1 }, { MUT_ACCEL, 4, 1 },
	};
	struct amdgpu_ps4_uvd_ring rb = descriptor();
	for (unsigned phase = 0; phase < 3; ++phase) {
		for (unsigned i = 0; i < sizeof(mutations)/sizeof(mutations[0]); ++i) {
			char evidence[100];
			bool bit16_row = mutations[i].kind == MUT_REG &&
				mutations[i].reg == mmUVD_STATUS &&
				mutations[i].value == 0x10002;
			bool status_row = mutations[i].kind == MUT_REG &&
				mutations[i].reg == mmUVD_STATUS;
			bool deferred_indexed = phase == 2 && mutations[i].kind == MUT_INDEXED;
			const char *expected = phase == 0 ? "STOBS_PRECONDITION" :
				phase == 1 ? (bit16_row ? "STOBS_PREPUBLICATION_BIT16" :
					      "STOBS_ENABLE_GUARD") :
				(bit16_row ? "STOBS_BIT16_PERSISTENT" :
				 deferred_indexed ? "STOBS_CTX_SWEEP_GUARD" :
				 status_row ? "STOBS_STATUS_UNEXPECTED" :
				 "STOBS_CORE_GUARD");
			fresh(phase == 0 ? NORMAL : phase == 1 ? PREPUBLISH_GUARD : POSTSUBMIT_GUARD);
			mutation_kind = mutations[i].kind;
			mutation_reg = mutations[i].reg; mutation_value = mutations[i].value;
			if (mutation_reg == mmUVD_RBC_RB_WPTR && phase < 2) mutation_value = 1;
			if (phase == 0) apply_mutation();
			run_case(&rb, expected, phase > 0 ? 1U : 0U,
				 phase == 2 ? 1U : 0U);
			if (phase == 0) assert(!data_writes && !context_writes);
			if (bit16_row && phase == 2) {
				assert(result_error == 0);
				assert(end_number("bit16_first") == 0 && end_number("bit16_clears") == 0);
				assert(end_number("sets") == 1 && end_number("markers") == 1);
				assert(end_number("persistent") == 1);
				assert(strstr(messages, "status=00010002"));
				assert(strstr(messages, "records=40 fine=24"));
				assert(strstr(messages, "CTX_SWEEP valid=1"));
			} else {
				if (deferred_indexed) {
					/* The recorded window and the later indexed sweep
					 * are distinct observations. Require explicit failure
					 * of the deferred check, never count it as passing. */
					assert(result_error == -EIO && end_number("records") == 40);
					assert(end_number("sweep_valid") == 0);
					assert(strstr(messages, "PS4_UVD_STOBS CTX_SWEEP valid=0"));
					expect_log_class("STOBS_BIT16_ABSENT_MARKERS");
				} else {
					assert(result_error == -EIO);
				}
				if (phase == 1)
					assert(strstr(messages, "GUARD_FULL sampled=all_then_data0"));
				snprintf(evidence, sizeof(evidence), "kind=%u reg=%08x value=%08x",
					 mutation_kind == MUT_REG ?
					 (mutation_reg == mmUVD_RBC_RB_RPTR && phase == 2 ? 3 : 0) :
					 mutation_kind == MUT_INDEXED ? 1 : 2,
					 mutation_reg, mutation_value);
				assert(strstr(messages, evidence));
			}
		}
	}
	unknown_prepublication_statuses();
}

/* Pre-publication and value rules: anything beyond 2 (or the predeclared
 * exact bit16 tolerance) is rejected; stale and baseline rules mirror the
 * frozen control with STOBS names.
 */
static void unknown_prepublication_statuses(void)
{
	struct amdgpu_ps4_uvd_ring rb = descriptor();
	for (u32 status = 0; status < 4; ++status) {
		if (status == 2) continue;
		fresh(NORMAL); registers[mmUVD_STATUS] = status;
		run_case(&rb, "STOBS_PRECONDITION", 0, 0);
	}
	fresh(STALE_DATA);
	run_case(&rb, "STOBS_STALE_RESULT", 0, 0); assert(!data_writes && !context_writes);
	saved_sample_checks(false);
	expect_log_class("STOBS_NO_RECORDS");
	fresh(STALE_CONTEXT);
	run_case(&rb, "STOBS_STALE_RESULT", 0, 0); assert(!data_writes && !context_writes);
	saved_sample_checks(false);
	fresh(DATA_DIRECT_FAIL);
	run_case(&rb, "STOBS_BASELINE_READBACK", 0, 0);
	assert(data_writes == 1 && !context_writes && !strstr(messages, "REC i=0 "));
	assert(strstr(messages, "reg=00003bc4 value=00000000 expected=43504435"));
	fresh(CONTEXT_DIRECT_FAIL);
	run_case(&rb, "STOBS_CONTEXT_BASELINE_READBACK", 0, 0);
	assert(data_writes == 1 && context_writes == 1);
	assert(strstr(messages, "reg=00003dbd value=00000000 expected=43504335"));
	fresh(HELD_DATA_CHANGED);
	run_case(&rb, "STOBS_BASELINE_OR_PUBLICATION_GUARD", 0, 0);
	fresh(HELD_CONTEXT_CHANGED);
	run_case(&rb, "STOBS_BASELINE_OR_PUBLICATION_GUARD", 0, 0);
	fresh(EMPTY_DATA_CHANGED);
	run_case(&rb, "STOBS_ENABLE_GUARD", 1, 0);
	fresh(ENABLE_READBACK);
	run_case(&rb, "STOBS_ENABLE_GUARD", 1, 0);
	fresh(ENABLE_EXACT_DEADLINE);
	run_case(&rb, "STOBS_ENABLE_TIMEOUT", 1, 0);
	saved_sample_checks(false);
	assert(strstr(messages, "records=0 "));
	fresh(BEFORE_SUBMIT_EXACT_DEADLINE);
	run_case(&rb, "STOBS_BEFORE_SUBMIT_TIMEOUT", 1, 0);
	saved_sample_checks(false);
	fresh(PRE_READ_EXACT_DEADLINE);
	run_case(&rb, "STOBS_BEFORE_SUBMIT_TIMEOUT", 1, 0);
	saved_sample_checks(false);
	fresh(PREPUB_BIT16);
	run_case(&rb, "STOBS_PREPUBLICATION_BIT16", 1, 0);
	assert(!pointer_writes);
	fresh(WPTR_BIT16);
	run_case(&rb, "STOBS_PREPUBLICATION_BIT16", 1, 0);
	assert(!pointer_writes);
	fresh(WPTR_BIT16_EXTRA);
	run_case(&rb, "STOBS_PREPUBLICATION_GUARD", 1, 0);
	assert(!pointer_writes);
}

/* Deadline-vs-record order (review B8): at the exact deadline the loop
 * stops before sampling; zero records, completed outcome, no class change.
 */
static void deadline_before_record(void)
{
	struct amdgpu_ps4_uvd_ring rb = descriptor();

	fresh(PUBLICATION_EXACT_DEADLINE);
	run_case(&rb, "STOBS_OBSERVATION_COMPLETED", 1, 1);
	assert(result_error == 0);
	assert(!ps4_stobs_record_count && !sleeps);
	expect_log_class("STOBS_NO_RECORDS");
	assert(strstr(messages, "records=0 fine=0 bit16_first=-1") &&
	       strstr(messages, "submitted=1"));
	assert(strstr(messages, "FINAL_CORE sampled=all_then_data0 status=00000002") &&
	       strstr(messages, "final_valid=1") && strstr(messages, "CTX_SWEEP valid=1"));
	assert(!strstr(messages, "PS4_UVD_STOBS REC i="));
}

/* Exercise timestamps and the final pre-publication deadline with real
 * nonzero costs in this synthetic model, not only clock-call injection. */
static void nonzero_read_costs(void)
{
	struct amdgpu_ps4_uvd_ring rb = descriptor();

	fresh(NORMAL_REPLAY);
	read_cost_ns = 1000;
	write_cost_ns = 500;
	run_case(&rb, "STOBS_BIT16_PERSISTENT", 1, 1);
	completion_checks(40);
	assert((u64)end_number("publish_ns") == published_at_ns - budget_start);
	for (unsigned i = 0; i < 40; ++i) {
		const char *record = record_line(i);
		assert(report_number(record, "t_end_us", 10) >
		       report_number(record, "elapsed_us", 10));
	}
	fresh(SLOW_PREPUBLICATION_READS);
	run_case(&rb, "STOBS_BEFORE_SUBMIT_TIMEOUT", 1, 0);
	assert(result_error == -ETIMEDOUT && !pointer_writes);
	assert(end_number("elapsed_ns") >= 250000000);
	assert(end_number("records") == 0 && !submitted_samples);
	saved_sample_checks(false);

	/* Failure time must include the immediate pre-WPTR direct reads. */
	fresh(WPTR_BIT16);
	read_cost_ns = 1000;
	run_case(&rb, "STOBS_PREPUBLICATION_BIT16", 1, 0);
	saved_sample_checks(true);
	assert((u64)end_number("elapsed_ns") == now_ns - budget_start);
}

static void variable_timing_paths(void)
{
	struct amdgpu_ps4_uvd_ring rb = descriptor();
	const enum scenario cases[] = { TIMED_BUDGET, TIMED_DEADLINE };
	for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
		unsigned records;
		fresh(cases[i]);
		read_cost_ns = 1000;
		write_cost_ns = 500;
		/* Deliberately synthetic costs, not a UART timing calibration. */
		log_ns_per_char = 100000;
		sleep_uses_high = true;
		sleep_extra_ns = i ? 3000000 : 0;
		run_case(&rb, "STOBS_BIT16_ABSENT_MARKERS", 1, 1);
		records = (unsigned)end_number("records");
		assert(records > 0 && records <= 40);
		if (i) assert(records < 40 && end_number("elapsed_ns") >= 250000000);
		else assert(records == 40 && end_number("elapsed_ns") < 250000000);
		completion_checks(records);
		expect_log_class("STOBS_BIT16_ABSENT_MARKERS");
		assert(end_number("publish_ns") > 10000000);
		for (unsigned j = 0; j < records; ++j) {
			assert(report_number(record_line(j), "elapsed_us", 10) < 250000);
			assert(report_number(record_line(j), "t_end_us", 10) >
			       report_number(record_line(j), "elapsed_us", 10));
		}
	}
}

static void marker_regression_counts(void)
{
	struct amdgpu_ps4_uvd_ring rb = descriptor();

	fresh(MARKER_REGRESSIONS);
	run_case(&rb, "STOBS_BIT16_ABSENT_MARKERS", 1, 1);
	completion_checks(40);
	/* DATA0 has two fresh-to-baseline edges, CONTEXT_ID has one.
	 * Long initial and trailing baseline runs do not create extra edges. */
	contract_check(end_number("data_regressions") == 2 &&
		end_number("context_regressions") == 1, 64,
		"reported fresh-to-baseline counts do not match the two DATA0 edges and one CONTEXT_ID edge");
	for (unsigned i = 0; i < 40; ++i) {
		bool data_fresh = (i >= 5 && i < 10) || (i >= 20 && i < 30);
		bool context_fresh = i >= 12 && i < 24;
		assert(report_number(record_line(i), "data", 16) ==
		       (data_fresh ? 0x55564435 : 0x43504435));
		assert(report_number(record_line(i), "ctx", 16) ==
		       (context_fresh ? 0x43545835 : 0x43504335));
	}
}

static void terminal_core_guard(void)
{
	struct amdgpu_ps4_uvd_ring rb = descriptor();
	const enum scenario cases[] = { FINAL_STATUS_GUARD, FINAL_RBC_GUARD };

	for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
		const char *guard;
		fresh(cases[i]);
		run_case(&rb, "STOBS_FINAL_GUARD", 1, 1);
		assert(result_error == -EIO);
		assert(end_number("records") == 40 && submitted_samples == 41);
		contract_check(submitted_selector_writes == 0, 128,
			"failed terminal core sample was followed by indexed selector writes");
		assert(end_number("final_valid") == 0 && end_number("sweep_valid") == 0);
		expect_log_class("STOBS_BIT16_ABSENT_MARKERS");
		guard = strstr(messages, "PS4_UVD_STOBS FINAL_GUARD ");
		assert(report_number(guard, "reg", 16) ==
		       (i == 0 ? mmUVD_STATUS : mmUVD_RBC_RB_CNTL));
		assert(report_number(guard, "value", 16) ==
		       (i == 0 ? 0x20002 : 0x1101080c));
		assert(!strstr(messages, "result=STOBS_BIT16_ABSENT"));
	}
}

int main(void)
{
	struct amdgpu_ps4_uvd_ring rb = descriptor();
	const struct {
		enum scenario scenario;
		const char *result;
		unsigned records, sleeps;
		int first, first_clear;
		unsigned sets, clears, markers, persistent;
	} cases[] = {
		{ NORMAL_REPLAY, "STOBS_BIT16_PERSISTENT", 40, 39, 0, -1, 1, 0, 1, 1 },
		{ BIT16_LATE_ONSET, "STOBS_BIT16_PERSISTENT", 40, 39, 11, -1, 1, 0, 1, 1 },
		{ BIT16_TRANSIENT, "STOBS_BIT16_TRANSIENT", 40, 39, 0, 9, 1, 1, 1, 0 },
		{ BIT16_TOGGLING, "STOBS_BIT16_TOGGLING", 40, 39, 0, 6, 2, 1, 1, 0 },
		{ BIT16_WITHOUT_MARKERS, "STOBS_BIT16_PERSISTENT", 40, 39, 0, -1, 1, 0, 0, 1 },
		{ BIT16_ABSENT_MARKERS, "STOBS_BIT16_ABSENT_MARKERS", 40, 39, -1, -1, 0, 0, 1, 0 },
		{ BIT16_ABSENT_NO_MARKERS, "STOBS_BIT16_ABSENT_NO_MARKERS", 40, 39, -1, -1, 0, 0, 0, 0 },
		{ SLOW_DEADLINE, "STOBS_BIT16_ABSENT_NO_MARKERS", 13, 13, -1, -1, 0, 0, 0, 0 },
		{ STATUS_UNEXPECTED_EXTRA, "STOBS_STATUS_UNEXPECTED", 2, 2, -1, -1, 0, 0, 1, 0 },
		{ LATE_CORE_GUARD, "STOBS_CORE_GUARD", 11, 11, -1, -1, 0, 0, 1, 0 },
		{ CORE_GUARD_RBC, "STOBS_CORE_GUARD", 1, 1, -1, -1, 0, 0, 1, 0 },
		{ SWEEP_DRIFT, "STOBS_CORE_GUARD", 1, 1, -1, -1, 0, 0, 1, 0 },
		{ RP_OVERRUN, "STOBS_CORE_GUARD", 1, 1, -1, -1, 0, 0, 1, 0 },
		{ RP_REGRESSION, "STOBS_POINTER_REGRESSION", 2, 1, -1, -1, 0, 0, 1, 0 },
		{ UNEXPECTED_VALUE, "STOBS_UNEXPECTED_VALUE", 1, 1, -1, -1, 0, 0, 1, 0 },
		{ UNEXPECTED_CONTEXT, "STOBS_UNEXPECTED_CONTEXT", 1, 1, -1, -1, 0, 0, 1, 0 },
		{ BIT16_THEN_GUARD, "STOBS_CORE_GUARD", 10, 10, 0, -1, 1, 0, 1, 1 },
	};
	test_gates_and_descriptors();
	test_guards();
	deadline_before_record();
	nonzero_read_costs();
	variable_timing_paths();
	marker_regression_counts();
	terminal_core_guard();
	for (unsigned i = 0; i < sizeof(cases)/sizeof(cases[0]); ++i) {
		fresh(cases[i].scenario);
		run_case(&rb, cases[i].result, 1, 1);
		assert(ps4_stobs_record_count == cases[i].records);
		assert(sleeps == cases[i].sleeps);
		assert(end_number("records") == cases[i].records);
		assert(end_number("bit16_first") == cases[i].first);
		assert(end_number("first_clear") == cases[i].first_clear);
		assert(end_number("sets") == cases[i].sets);
		assert(end_number("bit16_clears") == cases[i].clears);
		assert(end_number("markers") == cases[i].markers);
		assert(end_number("persistent") == cases[i].persistent);
		assert(end_number("accepted") == 0);
		expect_log_class(i < 8 ? cases[i].result : scenario == BIT16_THEN_GUARD ?
			"STOBS_BIT16_PERSISTENT" : "STOBS_BIT16_ABSENT_MARKERS");
		if (i < 8) {
			assert(result_error == 0);
			completion_checks(cases[i].records);
		} else {
			assert(result_error == -EIO);
			guard_checks(cases[i].result, cases[i].records, true);
			contract_check(submitted_samples == cases[i].records +
				(scenario == RP_REGRESSION ? 0U : 1U), 16,
				"error terminal sampled STATUS again after its deciding observation");
			assert(!strstr(messages, "result=STOBS_BIT16_ABSENT"));
		}
		if (scenario == NORMAL_REPLAY) {
			assert(report_number(record_line(0), "status", 16) == 0x10002);
			assert(report_number(record_line(0), "data", 16) == 0x55564435);
			assert(report_number(record_line(0), "ctx", 16) == 0x43545835);
			assert(report_number(record_line(0), "wp", 16) == 16);
			assert(report_number(record_line(0), "rp", 16) == 16);
		}
		if (scenario == BIT16_LATE_ONSET) {
			assert(report_number(record_line(10), "status", 16) == 2);
			assert(report_number(record_line(11), "status", 16) == 0x10002);
			assert(report_number(record_line(11), "elapsed_us", 10) == 1100);
		}
		if (scenario == BIT16_TRANSIENT) {
			assert(report_number(record_line(8), "status", 16) == 0x10002);
			assert(report_number(record_line(9), "status", 16) == 2);
		}
		if (scenario == BIT16_TOGGLING) {
			assert(report_number(record_line(5), "status", 16) == 0x10002);
			assert(report_number(record_line(6), "status", 16) == 2);
			assert(report_number(record_line(9), "status", 16) == 2);
			assert(report_number(record_line(10), "status", 16) == 0x10002);
		}
		if (scenario == BIT16_WITHOUT_MARKERS || scenario == BIT16_ABSENT_NO_MARKERS) {
			assert(report_number(record_line(0), "data", 16) == 0x43504435);
			assert(report_number(record_line(0), "ctx", 16) == 0x43504335);
			assert(report_number(record_line(0), "rp", 16) == 0);
			contract_check(end_number("data_regressions") == 0 &&
				end_number("context_regressions") == 0, 32,
				"unchanged baseline-only values were reported as fresh-to-baseline regressions");
		}
		if (scenario == STATUS_UNEXPECTED_EXTRA)
			assert(strstr(messages, "reg=00003daf value=00020002"));
		if (scenario == RP_OVERRUN)
			assert(strstr(messages, "rp=00000014") &&
			       strstr(messages, "expected=00000010;"));
		if (scenario == RP_REGRESSION)
			assert(strstr(messages, "rp=0000000c"));
		if (scenario == UNEXPECTED_VALUE || scenario == UNEXPECTED_CONTEXT) {
			const char *guard = strstr(messages, "PS4_UVD_STOBS GUARD kind=");
			assert(report_number(guard, "reg", 16) ==
			       (scenario == UNEXPECTED_VALUE ? 0x3bc4 : 0x3dbd));
			assert(report_number(guard, "value", 16) ==
			       (scenario == UNEXPECTED_VALUE ? 0x12345678 : 0x87654321));
			assert(report_number(guard, "expected", 16) ==
			       (scenario == UNEXPECTED_VALUE ? 0x55564435 : 0x43545835));
		}
	}
	if (contract_failures) {
		fprintf(stderr, "FAIL: %u contract violations across %u actual-source cases; defect mask=%u\n",
			contract_failures, cases_run, contract_reported);
		return 1;
	}
	printf("PASS: %u actual-source cases; 17 transition/fault scripts, phase guards, gates, baselines, deadlines and variable timing; full-page CRC cadc2d79; no hardware qualification\n", cases_run);
	return 0;
}
