/* SPDX-License-Identifier: GPL-2.0-only */
/* Synthetic MMIO/time harness. All ACTUAL_* blocks are extracted from source.
 * Literal packet/allowed-store assertions are independent of producer macros.
 * This is not hardware or firmware emulation.
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
static bool ps4_uvd_ready, ps4_uvd_poll_state, ps4_uvd_token, ps4_uvd_data_control;
static u32 registers[0x10000], indexed[0x1000];
static _Alignas(8) unsigned char ring_bytes[4096];
static unsigned data_writes, context_writes, fetch_writes, pointer_writes, selector_writes;
static unsigned held_samples, submitted_samples, sleeps, barriers, hdp_calls, clock_calls;
static bool enabled, submitted, loop_active;
static u64 now_ns, budget_start, pd_argument;
static jmp_buf terminal;
static const char *result_name;
static int result_error;
static char messages[32768];
static size_t message_size;
static unsigned cases_run;
static struct amdgpu_device device;

enum scenario {
	NORMAL, DATA_DIRECT_FAIL, CONTEXT_DIRECT_FAIL, HELD_DATA_CHANGED,
	HELD_CONTEXT_CHANGED, EMPTY_DATA_CHANGED, EMPTY_CONTEXT_CHANGED,
	ENABLE_READBACK, ENABLE_EXACT_DEADLINE, BEFORE_SUBMIT_EXACT_DEADLINE,
	PUBLICATION_LOG_EXPIRES, FIRST_POLL_EXACT_DEADLINE, FIRST_POLL_LATE,
	SECOND_POLL_EXACT_DEADLINE, LAST_VALID_NS, NEVER_COMPLETE,
	TRAILER_WITHOUT_DATA, DATA_WITHOUT_TRAILER, DATA_THEN_TRAILER,
	TRAILER_THEN_DATA, BOTH_THEN_POINTER, RACE_CONTEXT_READ_FIRST,
	DATA_REGRESSION, CONTEXT_REGRESSION, DATA_REGRESSION_AFTER_JOINT,
	CONTEXT_REGRESSION_AFTER_JOINT, FOREIGN_DATA, FOREIGN_CONTEXT,
	NONCONSECUTIVE_JOINT, TIMED_OBSERVATIONS, PREPUBLISH_GUARD,
	POSTSUBMIT_GUARD, BUSY_EMPTY, BUSY_SUBMITTED, BUSY_BOTH,
	UNKNOWN_EMPTY, UNKNOWN_SUBMITTED, POINTER_REGRESSION_BEFORE_DRAIN,
	FIRST_TRANSPORT_ON_DEADLINE, DRAIN_ONLY_ON_DEADLINE
};
static enum scenario scenario;
enum mutation_kind { MUT_REG, MUT_INDEXED, MUT_RESET, MUT_IN_RESET, MUT_BUFFER, MUT_ACCEL };
static enum mutation_kind mutation_kind;
static u32 mutation_reg, mutation_value;

static void log_message(void *ignored, const char *format, ...)
{
	va_list args;
	int count;
	(void)ignored;
	/* Only terminal paths may log after polling has begun. */
	if (loop_active)
		assert(strstr(format, " CORE ") || strstr(format, " GUARD ") || strstr(format, " END "));
	va_start(args, format);
	count = vsnprintf(messages + message_size, sizeof(messages) - message_size, format, args);
	va_end(args);
	assert(count >= 0 && (size_t)count < sizeof(messages) - message_size);
	message_size += (size_t)count;
	if (scenario == PUBLICATION_LOG_EXPIRES && strstr(format, " PUBLICATION_BEGIN "))
		now_ns = budget_start + UINT64_C(250000000);
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
		u32 expected_register = 0x3bff, expected_data = 0;
		if (!offset) {
			expected_register = ps4_uvd_data_control ? 0x3bc4 : 0x3dbd;
			expected_data = ps4_uvd_data_control ? 0x55564435 : 0x55564432;
		} else if (offset == 8 && ps4_uvd_data_control) {
			expected_register = 0x3dbd;
			expected_data = 0x43545835;
		}
		assert((header >> 30) == 0 && ((header >> 16) & 0x3fff) == 0);
		assert((header & 0xffff) == expected_register && data == expected_data);
	}
	if (ps4_uvd_data_control)
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
	registers[mmUVD_CONTEXT_ID] = ps4_uvd_data_control ? 0x43545835 : 0x55564432;
	registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564435;
	registers[mmUVD_RBC_RB_RPTR] = 16;
	if (scenario == NEVER_COMPLETE) {
		registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504435;
		registers[mmUVD_CONTEXT_ID] = 0x43504335;
		registers[mmUVD_RBC_RB_RPTR] = 0;
	}
	if (scenario == TRAILER_WITHOUT_DATA ||
	    (scenario == TRAILER_THEN_DATA && submitted_samples == 1))
		registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504435;
	if (scenario == DATA_WITHOUT_TRAILER ||
	    (scenario == DATA_THEN_TRAILER && submitted_samples == 1))
		registers[mmUVD_CONTEXT_ID] = 0x43504335;
	if (scenario == BOTH_THEN_POINTER && submitted_samples == 1)
		registers[mmUVD_RBC_RB_RPTR] = 0;
	if (scenario == RACE_CONTEXT_READ_FIRST && submitted_samples == 1) {
		registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504435;
		registers[mmUVD_CONTEXT_ID] = 0x43504335;
	}
	if (scenario == DATA_REGRESSION) {
		if (submitted_samples == 1) registers[mmUVD_CONTEXT_ID] = 0x43504335;
		else registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504435;
	}
	if (scenario == CONTEXT_REGRESSION) {
		if (submitted_samples == 1) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504435;
		else registers[mmUVD_CONTEXT_ID] = 0x43504335;
	}
	if (scenario == DATA_REGRESSION_AFTER_JOINT && submitted_samples > 1)
		registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504435;
	if (scenario == CONTEXT_REGRESSION_AFTER_JOINT && submitted_samples > 1)
		registers[mmUVD_CONTEXT_ID] = 0x43504335;
	if (scenario == FOREIGN_DATA) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x12345678;
	if (scenario == FOREIGN_CONTEXT) registers[mmUVD_CONTEXT_ID] = 0x87654321;
	if (scenario == NONCONSECUTIVE_JOINT && submitted_samples == 2)
		registers[mmUVD_RBC_RB_RPTR] = 0;
	if (scenario == POINTER_REGRESSION_BEFORE_DRAIN)
		registers[mmUVD_RBC_RB_RPTR] = submitted_samples == 1 ? 8 : 4;
	if (scenario == FIRST_TRANSPORT_ON_DEADLINE || scenario == DRAIN_ONLY_ON_DEADLINE) {
		registers[mmUVD_GPCOM_VCPU_DATA0] = 0x43504435;
		if (submitted_samples == 1) {
			registers[mmUVD_RBC_RB_RPTR] = 0;
			if (scenario == FIRST_TRANSPORT_ON_DEADLINE)
				registers[mmUVD_CONTEXT_ID] = 0x43504335;
		}
	}
	if (scenario == POSTSUBMIT_GUARD) apply_mutation();
}

static u32 mmio_read(u32 reg)
{
	assert(reg < 0x10000);
	if (reg == mmUVD_STATUS) {
		if (submitted) submitted_state();
		else if (!enabled) ++held_samples;
		if (enabled) {
			if (scenario == BUSY_BOTH || (scenario == BUSY_EMPTY && !submitted) ||
			    (scenario == BUSY_SUBMITTED && submitted)) registers[mmUVD_STATUS] = 3;
			if ((scenario == UNKNOWN_EMPTY && !submitted) ||
			    (scenario == UNKNOWN_SUBMITTED && submitted)) registers[mmUVD_STATUS] = 6;
		}
		if (held_samples == 2 && !enabled) {
			if (scenario == HELD_DATA_CHANGED) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564435;
			if (scenario == HELD_CONTEXT_CHANGED) registers[mmUVD_CONTEXT_ID] = 0x43545835;
		}
		if (enabled && !submitted) {
			if (scenario == EMPTY_DATA_CHANGED) registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564435;
			if (scenario == EMPTY_CONTEXT_CHANGED) registers[mmUVD_CONTEXT_ID] = 0x43545835;
			if (scenario == PREPUBLISH_GUARD) apply_mutation();
		}
	}
	if (reg == mmUVD_GPCOM_VCPU_DATA0 && submitted &&
	    scenario == RACE_CONTEXT_READ_FIRST && submitted_samples == 1) {
		/* Device result arrives after saved context read, before DATA0 read. */
		registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564435;
		registers[mmUVD_CONTEXT_ID] = 0x43545835;
	}
	if (reg == mmUVD_CTX_DATA) return indexed[registers[mmUVD_CTX_INDEX]];
	return registers[reg];
}

static void mmio_write(u32 reg, u32 value)
{
	assert(reg < 0x10000);
	if (reg == mmUVD_CTX_INDEX) {
		assert(value < 0x1000);
		++selector_writes;
	} else if (reg == mmUVD_GPCOM_VCPU_DATA0) {
		assert(ps4_uvd_data_control && value == 0x43504435);
		assert(!enabled && !submitted && !data_writes++ && !context_writes);
		if (scenario == DATA_DIRECT_FAIL) value = 0;
	} else if (reg == mmUVD_CONTEXT_ID) {
		assert(value == (ps4_uvd_data_control ? 0x43504335U : 0x43505532U));
		assert(!enabled && !submitted && !context_writes++);
		assert(!ps4_uvd_data_control || data_writes == 1);
		if (scenario == CONTEXT_DIRECT_FAIL) value = 0;
	} else if (reg == mmUVD_RBC_RB_CNTL) {
		assert(value == 0x1100080c && registers[reg] == 0x1101080c);
		assert(!enabled && !submitted && !fetch_writes++);
		assert(registers[mmUVD_RBC_RB_WPTR] == 0);
		assert(context_writes == 1 && hdp_calls == 1 && barriers >= 1);
		assert(!ps4_uvd_data_control || data_writes == 1);
		check_page();
		budget_start = now_ns;
		enabled = true;
		if (scenario == ENABLE_READBACK) value = 0x1101080c;
	} else if (reg == mmUVD_RBC_RB_WPTR) {
		assert(value == 16 && enabled && !submitted && !pointer_writes++);
		assert(barriers >= 2 && now_ns - budget_start < UINT64_C(250000000));
		check_page();
		submitted = true;
	} else {
		assert(!"forbidden MMIO write: includes CMD even zero, DATA1, IB, trap, reset, IRQ enable");
	}
	registers[reg] = value;
}
#define RREG32(reg) mmio_read(reg)
#define WREG32(reg,value) mmio_write(reg,value)

static bool amdgpu_in_reset(struct amdgpu_device *adev) { return adev->in_reset; }
static u64 ktime_get_ns(void)
{
	++clock_calls;
	if ((scenario == ENABLE_EXACT_DEADLINE && clock_calls == 2) ||
	    (scenario == BEFORE_SUBMIT_EXACT_DEADLINE && clock_calls == 3) ||
	    (scenario == FIRST_POLL_EXACT_DEADLINE && clock_calls == 4) ||
	    ((scenario == SECOND_POLL_EXACT_DEADLINE || scenario == FIRST_TRANSPORT_ON_DEADLINE ||
	      scenario == DRAIN_ONLY_ON_DEADLINE) && clock_calls == 5))
		now_ns = budget_start + UINT64_C(250000000);
	if (scenario == FIRST_POLL_LATE && clock_calls == 4)
		now_ns = budget_start + UINT64_C(250000001);
	if (scenario == LAST_VALID_NS && clock_calls >= 4)
		now_ns = budget_start + (clock_calls == 4 ? UINT64_C(249800000) : UINT64_C(249999999));
	if (scenario == TIMED_OBSERVATIONS && clock_calls >= 4)
		now_ns = budget_start + (clock_calls == 4 ? 123 : 100123);
	return now_ns;
}
static void mb(void) { ++barriers; }
static void amdgpu_device_flush_hdp(struct amdgpu_device *adev, void *ring)
{ (void)adev; (void)ring; ++hdp_calls; }
static void usleep_range(unsigned low, unsigned high)
{ assert(low == 100 && high == 200); ++sleeps; now_ns += 100000; }
static __noreturn void ps4_ready_park(struct amdgpu_device *adev, const char *name, int error)
{ (void)adev; result_name = name; result_error = error; longjmp(terminal, 1); }
static void ps4_ready_snapshot(struct amdgpu_device *adev, const char *name)
{ (void)adev; (void)name; }

/* ACTUAL_CONTEXT_READER */
/* ACTUAL_TOKEN_FLOW */
/* ACTUAL_DATA_CONTROL_FLOW */

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
	held_samples = submitted_samples = sleeps = barriers = hdp_calls = clock_calls = 0;
	now_ns = budget_start = 0; pd_argument = 0x100000;
	enabled = submitted = loop_active = false; scenario = next;
	mutation_kind = MUT_REG; mutation_reg = mutation_value = 0;
	ps4_uvd_ready = ps4_uvd_poll_state = ps4_uvd_token = ps4_uvd_data_control = true;
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
	registers[mmUVD_VCPU_CACHE_OFFSET1] = 0xfa00;
	registers[mmUVD_VCPU_CACHE_SIZE1] = 0x40000;
	registers[mmUVD_VCPU_CACHE_OFFSET2] = 0x17a00;
	registers[mmUVD_VCPU_CACHE_SIZE2] = 0x120800;
	registers[mmUVD_CTX_INDEX] = 0x55;
	indexed[ixUVD_LMI_VMID_INTERNAL] = 0x00444444;
	indexed[ixUVD_LMI_VMID_INTERNAL2] = 0x44444444;
	indexed[ixUVD_LMI_VMID_INTERNAL3] = 0x4444;
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
	assert(fetch_writes == fetch && pointer_writes == pointer);
	assert(registers[mmUVD_CTX_INDEX] == 0x55);
	if (!strcmp(expected, "DATA_READBACK_CONTROL_OBSERVED")) {
		assert(result_error == 0 && submitted_samples >= 2 && sleeps >= 1);
		assert(strstr(messages, "consecutive=2") && strstr(messages, "data_seen=1 context_seen=1"));
		assert(strstr(messages, "crc=cadc2d79") && strstr(messages, "CPU_BASELINES "));
		assert(strstr(messages, "ENABLE_BEGIN ") && strstr(messages, "PUBLICATION_BEGIN "));
		assert(strstr(messages, "wp=00000010 rp=00000010 ctx=43545835 data=55564435"));
	} else if (ps4_uvd_data_control) assert(result_error < 0);
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
	if (!setjmp(terminal)) ps4_data_control_run(&device, rb, pd_argument, 4, 0, 0);
	expect_result(expected, 0, 0);
	assert(!data_writes && !context_writes && !selector_writes && !held_samples);
}

static void run_receiver(const struct amdgpu_ps4_uvd_ring *rb)
{
	if (!setjmp(terminal)) {
		receiver_preflight(&device, rb, pd_argument);
		abort();
	}
	expect_result("DATA_DESCRIPTOR_OR_GATES", 0, 0);
	assert(!data_writes && !context_writes && !selector_writes && !held_samples);
}

static void test_gates_and_descriptors(void)
{
	struct amdgpu_ps4_uvd_ring rb = descriptor();
	for (unsigned mask = 0; mask < 15; ++mask) {
		fresh(NORMAL);
		ps4_uvd_ready = !!(mask & 1); ps4_uvd_poll_state = !!(mask & 2);
		ps4_uvd_token = !!(mask & 4); ps4_uvd_data_control = !!(mask & 8);
		run_direct(&rb, "DATA_DESCRIPTOR_OR_GATES");
		if (ps4_uvd_data_control) run_receiver(&rb);
	}
	fresh(NORMAL); run_direct(NULL, "DATA_DESCRIPTOR_OR_GATES"); run_receiver(NULL);
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
		run_direct(&rb, "DATA_DESCRIPTOR_OR_GATES"); run_receiver(&rb);
	}
	rb = descriptor();
	for (unsigned which = 0; which < 3; ++which) {
		fresh(NORMAL);
		pd_argument = which == 0 ? 0 : which == 1 ? 0x100001 : UINT64_C(0x100000000000);
		run_direct(&rb, "DATA_DESCRIPTOR_OR_GATES"); run_receiver(&rb);
	}
	fresh(NORMAL); ps4_uvd_data_control = false;
	receiver_preflight(&device, &rb, pd_argument);
	run_case(&rb, "TOKEN_TRANSPORT_OBSERVED", 1, 1);
	assert(!data_writes && submitted_samples == 1 && !sleeps);
	fresh(NORMAL); ps4_uvd_data_control = ps4_uvd_token = false;
	receiver_preflight(&device, NULL, pd_argument);
	run_case(NULL, "READY_CONVENTION_OBSERVED", 0, 0);
	assert(!data_writes && !context_writes && !selector_writes);
	fresh(NORMAL); ps4_uvd_data_control = ps4_uvd_token = ps4_uvd_poll_state = false;
	run_case(NULL, "READY_OBSERVED", 0, 0);
	assert(!data_writes && !context_writes && !selector_writes);
}

static void test_guards(void)
{
	const struct { enum mutation_kind kind; u32 reg, value; } mutations[] = {
		{ MUT_REG, mmUVD_STATUS, 6 }, { MUT_REG, mmUVD_MASTINT_EN, 0 },
		{ MUT_REG, mmUVD_RBC_RB_CNTL, 0x1000080c }, { MUT_REG, mmUVD_RBC_IB_SIZE, 1 },
		{ MUT_REG, mmUVD_RBC_RB_WPTR, 0 }, { MUT_REG, mmUVD_RBC_RB_RPTR, 64 },
		{ MUT_REG, mmUVD_RBC_RB_WPTR_CNTL, 1 }, { MUT_REG, mmUVD_RBC_RB_RPTR_ADDR, 1 },
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
			fresh(phase == 0 ? NORMAL : phase == 1 ? PREPUBLISH_GUARD : POSTSUBMIT_GUARD);
			mutation_kind = mutations[i].kind;
			mutation_reg = mutations[i].reg; mutation_value = mutations[i].value;
			if (mutation_reg == mmUVD_RBC_RB_WPTR && phase < 2) mutation_value = 1;
			if (phase == 0) apply_mutation();
			run_case(&rb, phase == 0 ? "DATA_PRECONDITION" : phase == 1 ?
				 "DATA_ENABLE_GUARD" : "DATA_SUBMITTED_GUARD", phase > 0, phase == 2);
			if (phase == 0) assert(!data_writes && !context_writes);
			snprintf(evidence, sizeof(evidence), "kind=%u reg=%08x value=%08x",
				 mutation_kind == MUT_REG ? (mutation_reg == mmUVD_RBC_RB_RPTR && phase == 2 ? 3 : 0) :
				 mutation_kind == MUT_INDEXED ? 1 : 2, mutation_reg, mutation_value);
			assert(strstr(messages, evidence));
		}
	}
	/* Busy is forbidden before fetch; unknown bits remain forbidden after it. */
	for (u32 status = 0; status < 4; ++status) {
		if (status == 2) continue;
		fresh(NORMAL); registers[mmUVD_STATUS] = status;
		run_case(&rb, "DATA_PRECONDITION", 0, 0);
	}
	fresh(NORMAL); registers[mmUVD_GPCOM_VCPU_DATA0] = 0x55564435;
	run_case(&rb, "DATA_STALE_RESULT", 0, 0); assert(!data_writes && !context_writes);
	fresh(NORMAL); registers[mmUVD_CONTEXT_ID] = 0x43545835;
	run_case(&rb, "DATA_STALE_RESULT", 0, 0); assert(!data_writes && !context_writes);
}

int main(void)
{
	struct amdgpu_ps4_uvd_ring rb = descriptor();
	const struct {
		enum scenario scenario; const char *result; unsigned fetch, pointer, polls;
	} cases[] = {
		{ NORMAL, "DATA_READBACK_CONTROL_OBSERVED", 1, 1, 2 },
		{ DATA_DIRECT_FAIL, "DATA_BASELINE_READBACK", 0, 0, 0 },
		{ CONTEXT_DIRECT_FAIL, "DATA_CONTEXT_BASELINE_READBACK", 0, 0, 0 },
		{ HELD_DATA_CHANGED, "DATA_BASELINE_OR_PUBLICATION_GUARD", 0, 0, 0 },
		{ HELD_CONTEXT_CHANGED, "DATA_BASELINE_OR_PUBLICATION_GUARD", 0, 0, 0 },
		{ EMPTY_DATA_CHANGED, "DATA_ENABLE_GUARD", 1, 0, 0 },
		{ EMPTY_CONTEXT_CHANGED, "DATA_ENABLE_GUARD", 1, 0, 0 },
		{ ENABLE_READBACK, "DATA_ENABLE_GUARD", 1, 0, 0 },
		{ ENABLE_EXACT_DEADLINE, "DATA_ENABLE_TIMEOUT", 1, 0, 0 },
		{ BEFORE_SUBMIT_EXACT_DEADLINE, "DATA_BEFORE_SUBMIT_TIMEOUT", 1, 0, 0 },
		{ PUBLICATION_LOG_EXPIRES, "DATA_BEFORE_SUBMIT_TIMEOUT", 1, 0, 0 },
		{ FIRST_POLL_EXACT_DEADLINE, "DATA_TIMEOUT", 1, 1, 1 },
		{ FIRST_POLL_LATE, "DATA_TIMEOUT", 1, 1, 1 },
		{ SECOND_POLL_EXACT_DEADLINE, "DATA_TIMEOUT", 1, 1, 2 },
		{ LAST_VALID_NS, "DATA_READBACK_CONTROL_OBSERVED", 1, 1, 2 },
		{ NEVER_COMPLETE, "DATA_TIMEOUT", 1, 1, 2501 },
		{ TRAILER_WITHOUT_DATA, "DATA_NOT_OBSERVED_WITH_TRANSPORT", 1, 1, 2501 },
		{ DATA_WITHOUT_TRAILER, "DATA_TIMEOUT", 1, 1, 2501 },
		{ DATA_THEN_TRAILER, "DATA_READBACK_CONTROL_OBSERVED", 1, 1, 3 },
		{ TRAILER_THEN_DATA, "DATA_READBACK_CONTROL_OBSERVED", 1, 1, 3 },
		{ BOTH_THEN_POINTER, "DATA_READBACK_CONTROL_OBSERVED", 1, 1, 3 },
		{ RACE_CONTEXT_READ_FIRST, "DATA_READBACK_CONTROL_OBSERVED", 1, 1, 3 },
		{ DATA_REGRESSION, "DATA_OBSERVATION_REGRESSION", 1, 1, 2 },
		{ CONTEXT_REGRESSION, "DATA_CONTEXT_REGRESSION", 1, 1, 2 },
		{ DATA_REGRESSION_AFTER_JOINT, "DATA_OBSERVATION_REGRESSION", 1, 1, 2 },
		{ CONTEXT_REGRESSION_AFTER_JOINT, "DATA_CONTEXT_REGRESSION", 1, 1, 2 },
		{ FOREIGN_DATA, "DATA_UNEXPECTED_VALUE", 1, 1, 1 },
		{ FOREIGN_CONTEXT, "DATA_UNEXPECTED_CONTEXT", 1, 1, 1 },
		{ NONCONSECUTIVE_JOINT, "DATA_POINTER_REGRESSION", 1, 1, 2 },
		{ POINTER_REGRESSION_BEFORE_DRAIN, "DATA_POINTER_REGRESSION", 1, 1, 2 },
		{ FIRST_TRANSPORT_ON_DEADLINE, "DATA_TIMEOUT", 1, 1, 2 },
		{ DRAIN_ONLY_ON_DEADLINE, "DATA_TIMEOUT", 1, 1, 2 },
		{ TIMED_OBSERVATIONS, "DATA_READBACK_CONTROL_OBSERVED", 1, 1, 2 },
		{ BUSY_EMPTY, "DATA_READBACK_CONTROL_OBSERVED", 1, 1, 2 },
		{ BUSY_SUBMITTED, "DATA_READBACK_CONTROL_OBSERVED", 1, 1, 2 },
		{ BUSY_BOTH, "DATA_READBACK_CONTROL_OBSERVED", 1, 1, 2 },
		{ UNKNOWN_EMPTY, "DATA_ENABLE_GUARD", 1, 0, 0 },
		{ UNKNOWN_SUBMITTED, "DATA_SUBMITTED_GUARD", 1, 1, 1 },
	};
	const u32 fresh_values[] = { 0x43504435, 0x55564435, 0x43504335, 0x43545835 };
	for (unsigned i = 0; i < 4; ++i) for (unsigned j = i + 1; j < 4; ++j)
		assert(fresh_values[i] != fresh_values[j]);
	test_gates_and_descriptors();
	test_guards();
	for (unsigned i = 0; i < sizeof(cases)/sizeof(cases[0]); ++i) {
		fresh(cases[i].scenario);
		run_case(&rb, cases[i].result, cases[i].fetch, cases[i].pointer);
		assert(submitted_samples == cases[i].polls);
		if (submitted_samples) assert(sleeps == submitted_samples - 1);
		if (scenario == DATA_DIRECT_FAIL) {
			assert(data_writes == 1 && !context_writes && !strstr(messages, "PS4_UVD_DATA CORE "));
			assert(strstr(messages, "reg=00003bc4 value=00000000 expected=43504435"));
		}
		if (scenario == CONTEXT_DIRECT_FAIL) {
			assert(data_writes == 1 && context_writes == 1 && !strstr(messages, "PS4_UVD_DATA CORE "));
			assert(strstr(messages, "reg=00003dbd value=00000000 expected=43504335"));
		}
		if (scenario == FIRST_POLL_EXACT_DEADLINE || scenario == FIRST_POLL_LATE)
			assert(strstr(messages, "consecutive=0 joint_samples=0 data_seen=0 context_seen=0"));
		if (scenario == SECOND_POLL_EXACT_DEADLINE)
			assert(strstr(messages, "consecutive=1 joint_samples=1 data_seen=1 context_seen=1"));
		if (scenario == LAST_VALID_NS) assert(strstr(messages, "elapsed_ns=249999999 "));
		if (scenario == TIMED_OBSERVATIONS)
			assert(strstr(messages, "first_data_ns=123 first_context_ns=123 first_joint_ns=123"));
		if (scenario == TRAILER_THEN_DATA)
			assert(strstr(messages, "first_data_ns=100000 first_context_ns=0 first_joint_ns=100000"));
		if (scenario == DATA_THEN_TRAILER || scenario == RACE_CONTEXT_READ_FIRST)
			assert(strstr(messages, "first_data_ns=0 first_context_ns=100000 first_joint_ns=100000"));
		if (scenario == NONCONSECUTIVE_JOINT) {
			assert(strstr(messages, "consecutive=1 joint_samples=1"));
			assert(strstr(messages, "last_rptr=16 ") && strstr(messages, "rp=00000000"));
		}
		if (scenario == POINTER_REGRESSION_BEFORE_DRAIN)
			assert(strstr(messages, "last_rptr=8 ") && strstr(messages, "rp=00000004"));
		if (scenario == FIRST_TRANSPORT_ON_DEADLINE || scenario == DRAIN_ONLY_ON_DEADLINE)
			assert(strstr(messages, "transport_seen=0") && strstr(messages, "rp=00000010"));
		if (scenario == TRAILER_WITHOUT_DATA) {
			assert(strstr(messages, "wp=00000010 rp=00000010 ctx=43545835 data=43504435"));
			assert(strstr(messages, "data_seen=0 context_seen=1"));
		}
		if (scenario == DATA_REGRESSION || scenario == DATA_REGRESSION_AFTER_JOINT)
			assert(strstr(messages, "data=43504435") && strstr(messages, "data_seen=1"));
		if (scenario == CONTEXT_REGRESSION || scenario == CONTEXT_REGRESSION_AFTER_JOINT)
			assert(strstr(messages, "ctx=43504335") && strstr(messages, "context_seen=1"));
	}
	printf("PASS: %u actual-source cases, including 38 event sequences, 117 phase/guard cases, gates/descriptors, stale values, unchanged gate-off dispatch; full-page CRC cadc2d79\n", cases_run);
	return 0;
}
