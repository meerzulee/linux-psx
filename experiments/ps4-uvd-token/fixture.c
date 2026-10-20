/* SPDX-License-Identifier: GPL-2.0-only */
/* Synthetic MMIO/clock harness. ACTUAL_* blocks come from patched source. */
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
#define PS4_CANARY_VA UINT64_C(0x300000000)
#define PS4_READY_RB_SLOT 776U
#define PS4_READY_MAPPED_PAGES 812U
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
	struct ps4_canary_bo uvd_rb;
	bool ready_mode, submitted, passed;
	void *fence;
	unsigned mapped_pages;
};
static struct ps4_canary_state *ps4_canary_retained;
static u64 amdgpu_bo_size(struct amdgpu_bo *bo) { return bo->bytes; }
/* ACTUAL_OWNER_CHECK */

static bool ps4_uvd_ready = true, ps4_uvd_poll_state = true, ps4_uvd_token;
static u32 registers[0x10000], indexed[0x1000];
static _Alignas(8) unsigned char ring_bytes[4096];
static unsigned context_writes, fetch_writes, pointer_writes, selector_writes;
static unsigned submitted_samples, sleeps, barriers, hdp_calls;
static bool enabled, submitted;
static u64 now_ns;
static jmp_buf terminal;
static const char *result_name;
static int result_error;
static char messages[16384];
static size_t message_size;

enum scenario {
	NORMAL, BASELINE_READBACK, ENABLE_READBACK, ENABLE_EXPIRED,
	BEFORE_SUBMIT_EXPIRED, WPTR_READBACK, DRAINED_THEN_TOKEN,
	TOKEN_THEN_POINTER, EXACT_DEADLINE, LATE_SUCCESS, LAST_VALID_NS,
	NEVER_COMPLETE, FOREIGN_CONTEXT, MASTINT_CHANGED, RPTR_BYTES,
	FAULT_CHANGED, RESET_CHANGED, SELECTOR_CHANGED, PD_CHANGED,
	BUSY_EMPTY, BUSY_SUBMITTED, BUSY_BOTH,
	UNKNOWN_EMPTY, UNKNOWN_BUSY_EMPTY, UNKNOWN_SUBMITTED, UNKNOWN_BUSY_SUBMITTED
};
static enum scenario scenario;
static struct amdgpu_device device;

static void log_message(void *ignored, const char *format, ...)
{
	va_list args;
	int count;
	(void)ignored;
	va_start(args, format);
	count = vsnprintf(messages + message_size, sizeof(messages) - message_size,
			  format, args);
	va_end(args);
	assert(count >= 0 && (size_t)count < sizeof(messages) - message_size);
	message_size += (size_t)count;
}
#define dev_info log_message
#define dev_err log_message

static u32 decode_word(const unsigned char *p)
{
	return (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
}

/* Independent packet parser, not the producer's writer/metadata. */
static u32 command_token(void)
{
	unsigned offset;
	for (offset = 0; offset < 4096; offset += 8) {
		u32 header = decode_word(ring_bytes + offset);
		u32 data = decode_word(ring_bytes + offset + 4);
		assert((header >> 30) == 0 && ((header >> 16) & 0x3fff) == 0);
		assert((header & 0xffff) == (offset ? 0x3bffU : 0x3dbdU));
		assert(data == (offset ? 0 : 0x55564432U));
	}
	return decode_word(ring_bytes + 4);
}

static u32 mmio_read(u32 reg)
{
	assert(reg < 0x10000);
	if (reg == mmUVD_STATUS && enabled) {
		registers[mmUVD_STATUS] = 2;
		if (scenario == BUSY_BOTH ||
		    (scenario == BUSY_EMPTY && !submitted) ||
		    (scenario == BUSY_SUBMITTED && submitted))
			registers[mmUVD_STATUS] = 3;
		if ((scenario == UNKNOWN_EMPTY && !submitted) ||
		    (scenario == UNKNOWN_SUBMITTED && submitted))
			registers[mmUVD_STATUS] = 6;
		if ((scenario == UNKNOWN_BUSY_EMPTY && !submitted) ||
		    (scenario == UNKNOWN_BUSY_SUBMITTED && submitted))
			registers[mmUVD_STATUS] = 7;
	}
	if (reg == mmUVD_STATUS && enabled && !submitted && scenario == ENABLE_EXPIRED)
		now_ns = UINT64_C(250000000);
	if (reg == mmUVD_STATUS && submitted) {
		++submitted_samples;
		registers[mmUVD_CONTEXT_ID] = command_token();
		registers[mmUVD_RBC_RB_RPTR] = 16;
		if (scenario == DRAINED_THEN_TOKEN && submitted_samples == 1)
			registers[mmUVD_CONTEXT_ID] = 0x43505532U;
		if (scenario == TOKEN_THEN_POINTER && submitted_samples == 1)
			registers[mmUVD_RBC_RB_RPTR] = 0;
		if (scenario == EXACT_DEADLINE) now_ns = UINT64_C(250000000);
		if (scenario == LATE_SUCCESS) now_ns = UINT64_C(250000001);
		if (scenario == LAST_VALID_NS) now_ns = UINT64_C(249999999);
		if (scenario == NEVER_COMPLETE) {
			registers[mmUVD_CONTEXT_ID] = 0x43505532U;
			registers[mmUVD_RBC_RB_RPTR] = 0;
		}
		if (scenario == FOREIGN_CONTEXT) registers[mmUVD_CONTEXT_ID] = 0x12345678;
		if (scenario == MASTINT_CHANGED) registers[mmUVD_MASTINT_EN] = 6;
		if (scenario == RPTR_BYTES) registers[mmUVD_RBC_RB_RPTR] = 64;
		if (scenario == FAULT_CHANGED) registers[mmVM_CONTEXT1_PROTECTION_FAULT_STATUS] = 1;
		if (scenario == RESET_CHANGED) device.gpu_reset_counter = 1;
		if (scenario == SELECTOR_CHANGED) indexed[ixUVD_LMI_VMID_INTERNAL2] = 0;
		if (scenario == PD_CHANGED) registers[mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + 4] = 0;
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
	} else if (reg == mmUVD_CONTEXT_ID) {
		assert(value == 0x43505532U); /* CPU must never write result token. */
		assert(!enabled && !submitted && !context_writes++);
		if (scenario == BASELINE_READBACK) value = 0;
	} else if (reg == mmUVD_RBC_RB_CNTL) {
		assert(value == 0x1100080cU && !enabled && !fetch_writes++);
		assert(registers[mmUVD_RBC_RB_WPTR] == 0);
		assert(context_writes == 1 && hdp_calls == 1 && barriers >= 1);
		(void)command_token();
		enabled = true;
		if (scenario == ENABLE_READBACK) value = 0x1101080c;
	} else if (reg == mmUVD_RBC_RB_WPTR) {
		assert(value == 16 && enabled && !submitted && !pointer_writes++);
		assert(barriers >= 2 && now_ns < UINT64_C(250000000));
		submitted = true;
		if (scenario == WPTR_READBACK) value = 0;
	} else {
		assert(!"forbidden MMIO write (includes trap, IB, resets, IRQ enable)");
	}
	registers[reg] = value;
}
#define RREG32(reg) mmio_read(reg)
#define WREG32(reg,value) mmio_write(reg,value)

static bool amdgpu_in_reset(struct amdgpu_device *adev) { return adev->in_reset; }
static u64 ktime_get_ns(void) { return now_ns; }
static void mb(void)
{
	++barriers;
	if (enabled && !submitted && scenario == BEFORE_SUBMIT_EXPIRED)
		now_ns = UINT64_C(250000000);
}
static void amdgpu_device_flush_hdp(struct amdgpu_device *adev, void *ring)
{ (void)adev; assert(!ring); ++hdp_calls; }
static void usleep_range(unsigned low, unsigned high)
{ assert(low == 100 && high == 200); ++sleeps; now_ns += UINT64_C(1000000); }
static u32 crc32_le(u32 crc, const void *data, size_t length)
{
	const unsigned char *bytes = data;
	unsigned bit;
	while (length--) {
		crc ^= *bytes++;
		for (bit = 0; bit < 8; ++bit)
			crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0);
	}
	return crc;
}
static __noreturn void ps4_ready_park(struct amdgpu_device *adev, const char *name, int error)
{ (void)adev; result_name = name; result_error = error; longjmp(terminal, 1); }
static void ps4_ready_snapshot(struct amdgpu_device *adev, const char *name)
{ (void)adev; (void)name; }

/* ACTUAL_CONTEXT_READER */
/* ACTUAL_TOKEN_FLOW */

static void receiver_preflight(struct amdgpu_device *adev,
			       const struct amdgpu_ps4_uvd_ring *rb)
{
	/* ACTUAL_EARLY_GATE */
}

static void dispatch(struct amdgpu_device *adev, const struct amdgpu_ps4_uvd_ring *rb)
{
	u32 status = 2, cache_vmid_expected = 4, baseline_fault = 0;
	int reset_count = 0;
	u64 pd_addr = 0x100000, elapsed = 42246000;
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
	context_writes = fetch_writes = pointer_writes = selector_writes = 0;
	submitted_samples = sleeps = barriers = hdp_calls = 0;
	now_ns = 0; enabled = submitted = false; scenario = next;
	ps4_uvd_ready = ps4_uvd_poll_state = ps4_uvd_token = true;
	registers[mmUVD_STATUS] = 2;
	registers[mmUVD_MASTINT_EN] = 2;
	registers[mmUVD_RBC_RB_CNTL] = 0x1101080c;
	registers[mmUVD_CONTEXT_ID] = 0x8899aabb;
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

static void run_case(const struct amdgpu_ps4_uvd_ring *rb, const char *expected,
		     unsigned expected_fetch, unsigned expected_pointer)
{
	if (!setjmp(terminal)) dispatch(&device, rb);
	if (!result_name || strcmp(result_name, expected)) {
		fprintf(stderr, "expected %s, got %s\n%s", expected, result_name, messages);
		abort();
	}
	assert(fetch_writes == expected_fetch && pointer_writes == expected_pointer);
	assert(registers[mmUVD_CTX_INDEX] == 0x55); /* Actual reader restores index. */
	assert(!strstr(messages, "PS4_UVD_TOKEN CORE ") || strstr(messages, "sampled=all"));
}

static void test_owner(void)
{
	struct resource resource = { TTM_PL_TT };
	struct amdgpu_bo bo = { { &resource, 1, ttm_bo_type_kernel }, 4096 };
	struct ps4_canary_state state = { .uvd_rb = { &bo, 0xdead000, ring_bytes },
		.ready_mode = true, .submitted = true, .passed = false,
		.fence = &bo, .mapped_pages = 812 };
	struct amdgpu_ps4_uvd_ring rb;
	ps4_canary_retained = &state;
	assert(ps4_canary_token_ring(&state, &rb));
	assert(rb.cpu == ring_bytes && rb.bytes == 4096 && rb.private_va == UINT64_C(0x300308000));
	assert(rb.private_va != state.uvd_rb.gpu && !state.passed);
	bo.tbo.pin_count = 0; assert(!ps4_canary_token_ring(&state, &rb)); bo.tbo.pin_count = 1;
	bo.bytes = 8192; assert(!ps4_canary_token_ring(&state, &rb)); bo.bytes = 4096;
	state.mapped_pages = 811; assert(!ps4_canary_token_ring(&state, &rb)); state.mapped_pages = 812;
	state.submitted = false; assert(!ps4_canary_token_ring(&state, &rb));
	state.submitted = true; state.fence = NULL; assert(!ps4_canary_token_ring(&state, &rb));
	state.fence = &bo; bo.tbo.type = 0; assert(!ps4_canary_token_ring(&state, &rb));
	bo.tbo.type = ttm_bo_type_kernel; resource.mem_type = 0;
	assert(!ps4_canary_token_ring(&state, &rb)); resource.mem_type = TTM_PL_TT;
	state.uvd_rb.cpu = ring_bytes + 1; assert(!ps4_canary_token_ring(&state, &rb));
	state.uvd_rb.cpu = ring_bytes; ps4_canary_retained = NULL;
	assert(!ps4_canary_token_ring(&state, &rb));
}

int main(void)
{
	struct amdgpu_ps4_uvd_ring rb = descriptor();
	unsigned i;
	const struct { u32 reg, value; } preconditions[] = {
		{ mmUVD_STATUS, 6 }, { mmUVD_STATUS, 3 }, { mmUVD_STATUS, 7 },
		{ mmUVD_STATUS, 0 }, { mmUVD_STATUS, 1 }, { mmUVD_MASTINT_EN, 0 },
		{ mmUVD_RBC_RB_CNTL, 0x1001080c }, { mmUVD_RBC_IB_SIZE, 1 },
		{ mmUVD_RBC_RB_WPTR, 1 }, { mmUVD_RBC_RB_RPTR, 1 },
		{ mmUVD_RBC_RB_WPTR_CNTL, 1 }, { mmUVD_RBC_RB_RPTR_ADDR, 1 },
		{ mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + 4, 0 },
		{ PS4_READY_UVD_VCPU_CACHE_VMID, 5 }, { mmUVD_LMI_RBC_RB_VMID, 0 },
		{ mmUVD_LMI_RBC_IB_VMID, 0 }, { mmUVD_LMI_RBC_RB_64BIT_BAR_LOW, 0 },
		{ mmUVD_LMI_SWAP_CNTL, 1 }, { mmUVD_SOFT_RESET, UVD_SOFT_RESET__RBC_SOFT_RESET_MASK },
	};
	const struct { enum scenario scenario; const char *result; unsigned fetch, pointer; } cases[] = {
		{ NORMAL, "TOKEN_TRANSPORT_OBSERVED", 1, 1 },
		{ BASELINE_READBACK, "TOKEN_BASELINE_READBACK", 0, 0 },
		{ ENABLE_READBACK, "TOKEN_ENABLE_GUARD", 1, 0 },
		{ ENABLE_EXPIRED, "TOKEN_ENABLE_TIMEOUT", 1, 0 },
		{ BEFORE_SUBMIT_EXPIRED, "TOKEN_BEFORE_SUBMIT_TIMEOUT", 1, 0 },
		{ WPTR_READBACK, "TOKEN_SUBMITTED_GUARD", 1, 1 },
		{ DRAINED_THEN_TOKEN, "TOKEN_TRANSPORT_OBSERVED", 1, 1 },
		{ TOKEN_THEN_POINTER, "TOKEN_TRANSPORT_OBSERVED", 1, 1 },
		{ EXACT_DEADLINE, "TOKEN_TIMEOUT", 1, 1 },
		{ LATE_SUCCESS, "TOKEN_TIMEOUT", 1, 1 },
		{ LAST_VALID_NS, "TOKEN_TRANSPORT_OBSERVED", 1, 1 },
		{ NEVER_COMPLETE, "TOKEN_TIMEOUT", 1, 1 },
		{ FOREIGN_CONTEXT, "TOKEN_UNEXPECTED_CONTEXT", 1, 1 },
		{ MASTINT_CHANGED, "TOKEN_SUBMITTED_GUARD", 1, 1 },
		{ RPTR_BYTES, "TOKEN_SUBMITTED_GUARD", 1, 1 },
		{ FAULT_CHANGED, "TOKEN_SUBMITTED_GUARD", 1, 1 },
		{ RESET_CHANGED, "TOKEN_SUBMITTED_GUARD", 1, 1 },
		{ SELECTOR_CHANGED, "TOKEN_SUBMITTED_GUARD", 1, 1 },
		{ PD_CHANGED, "TOKEN_SUBMITTED_GUARD", 1, 1 },
		{ BUSY_EMPTY, "TOKEN_TRANSPORT_OBSERVED", 1, 1 },
		{ BUSY_SUBMITTED, "TOKEN_TRANSPORT_OBSERVED", 1, 1 },
		{ BUSY_BOTH, "TOKEN_TRANSPORT_OBSERVED", 1, 1 },
		{ UNKNOWN_EMPTY, "TOKEN_ENABLE_GUARD", 1, 0 },
		{ UNKNOWN_BUSY_EMPTY, "TOKEN_ENABLE_GUARD", 1, 0 },
		{ UNKNOWN_SUBMITTED, "TOKEN_SUBMITTED_GUARD", 1, 1 },
		{ UNKNOWN_BUSY_SUBMITTED, "TOKEN_SUBMITTED_GUARD", 1, 1 },
	};
	test_owner();
	fresh(NORMAL); ps4_uvd_token = false;
	receiver_preflight(&device, NULL);
	run_case(NULL, "READY_CONVENTION_OBSERVED", 0, 0);
	assert(!context_writes && !selector_writes && !hdp_calls);
	fresh(NORMAL); ps4_uvd_token = false; ps4_uvd_poll_state = false;
	run_case(NULL, "READY_OBSERVED", 0, 0);
	assert(!context_writes && !selector_writes);
	assert(!ps4_token_descriptor_valid(NULL));
	rb.cpu = (void *)(~0UL - 3UL);
	assert(!ps4_token_descriptor_valid(&rb));
	fresh(NORMAL); run_case(&rb, "TOKEN_DESCRIPTOR_OR_GATES", 0, 0);
	assert(!context_writes && !selector_writes);
	rb = descriptor();
	fresh(NORMAL); ps4_uvd_poll_state = false;
	if (!setjmp(terminal)) { receiver_preflight(&device, &rb); abort(); }
	assert(!strcmp(result_name, "TOKEN_DESCRIPTOR_OR_GATES"));
	assert(!context_writes && !selector_writes && !fetch_writes && !pointer_writes);
	fresh(NORMAL); ps4_uvd_ready = false;
	run_case(&rb, "TOKEN_DESCRIPTOR_OR_GATES", 0, 0);
	rb.bytes = 4095; assert(!ps4_token_descriptor_valid(&rb)); rb = descriptor();
	rb.private_va += 4096; assert(!ps4_token_descriptor_valid(&rb)); rb = descriptor();
	fresh(NORMAL); registers[mmUVD_RBC_RB_WPTR_CNTL] = 1;
	run_case(&rb, "TOKEN_PRECONDITION", 0, 0); assert(!context_writes);
	assert(strstr(messages, "reg=00003da6 value=00000001"));
	fresh(NORMAL); registers[mmUVD_RBC_RB_RPTR_ADDR] = 1;
	run_case(&rb, "TOKEN_PRECONDITION", 0, 0); assert(!context_writes);
	fresh(NORMAL); registers[mmUVD_CONTEXT_ID] = 0x55564432;
	run_case(&rb, "TOKEN_PLAN_REJECTED", 0, 0); assert(!context_writes);
	for (i = 0; i < sizeof(preconditions)/sizeof(preconditions[0]); ++i) {
		char evidence[80];
		fresh(NORMAL); registers[preconditions[i].reg] = preconditions[i].value;
		run_case(&rb, "TOKEN_PRECONDITION", 0, 0);
		assert(!context_writes);
		snprintf(evidence, sizeof(evidence), "reg=%08x value=%08x",
			 preconditions[i].reg, preconditions[i].value);
		assert(strstr(messages, evidence));
		if (preconditions[i].reg == mmUVD_STATUS)
			assert(strstr(messages, "mask=ffffffff expected=00000002"));
	}
	for (i = 0; i < sizeof(cases)/sizeof(cases[0]); ++i) {
		fresh(cases[i].scenario);
		run_case(&rb, cases[i].result, cases[i].fetch, cases[i].pointer);
		if (cases[i].scenario == DRAINED_THEN_TOKEN || cases[i].scenario == TOKEN_THEN_POINTER)
			assert(submitted_samples == 2 && sleeps == 1);
		if (!strcmp(cases[i].result, "TOKEN_TRANSPORT_OBSERVED")) assert(result_error == 0);
		else assert(result_error < 0);
		if (cases[i].scenario == BUSY_SUBMITTED || cases[i].scenario == BUSY_BOTH) {
			assert(strstr(messages, "CORE sampled=all status=00000003"));
			assert(strstr(messages, "no quiescence/decode claim"));
		}
		if (cases[i].scenario == UNKNOWN_EMPTY || cases[i].scenario == UNKNOWN_SUBMITTED ||
		    cases[i].scenario == UNKNOWN_BUSY_EMPTY || cases[i].scenario == UNKNOWN_BUSY_SUBMITTED) {
			char evidence[80];
			u32 raw = cases[i].scenario == UNKNOWN_BUSY_EMPTY ||
				  cases[i].scenario == UNKNOWN_BUSY_SUBMITTED ? 7 : 6;
			snprintf(evidence, sizeof(evidence), "reg=%08x value=%08x mask=fffffffe expected=00000002",
				 mmUVD_STATUS, raw);
			assert(strstr(messages, evidence));
		}
	}
	puts("PASS: actual owner/descriptor/context-reader/token flow; 26 event cases + 19 exact preconditions + gate/ownership/bounds cases; post-fetch RBC_BUSY hypothesis only");
	return 0;
}
