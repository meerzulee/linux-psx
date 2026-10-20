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

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint32_t __le32;
#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
#define cpu_to_le32(x) ((u32)(x))
#define le32_to_cpu(x) ((u32)(x))
#else
#define cpu_to_le32(x) __builtin_bswap32((u32)(x))
#define le32_to_cpu(x) __builtin_bswap32((u32)(x))
#endif
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
#define PS4_READY_IB_SLOT 778U
#define PS4_READY_IB_BYTES 0x20000ULL
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
	struct ps4_canary_bo uvd_rb, uvd_ib;
	bool ready_mode, submitted, passed;
	void *fence;
	unsigned mapped_pages;
};
static struct ps4_canary_state *ps4_canary_retained;
static u64 amdgpu_bo_size(struct amdgpu_bo *bo) { return bo->bytes; }
/* ACTUAL_OWNER_CHECK */

static bool ps4_uvd_ready = true, ps4_uvd_poll_state = true, ps4_uvd_token, ps4_uvd_writeback;
static u32 registers[0x10000], indexed[0x1000];
static _Alignas(8) unsigned char ring_bytes[4096];
static struct { u32 before[8], page[1024], after[8]; } wb_storage;
static unsigned cpu_word_writes, gpu_word_writes, target_reads, guard_reads, invalidate_calls;
static bool empty_scan_completed;
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
	BUSY_BOTH, UNKNOWN_EMPTY, UNKNOWN_SUBMITTED,
	CPU_INIT_CORRUPT, EMPTY_PAGE_CORRUPT, EARLY_RESULT,
	DELAYED_WRITEBACK, NEVER_WRITEBACK, WRONG_MEMORY, PARTIAL_MEMORY,
	WORD_BEFORE_CONTEXT, WORD_BEFORE_POINTER, WORD_WITHOUT_CONTEXT,
	GUARD_FIRST, GUARD_LAST, SCAN_DEADLINE
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

/* Independent decoder: no producer writer/helper is used to accept packets. */
struct decoded { u32 sequence; u64 address; };
static bool parse_page(struct decoded *out)
{
	unsigned offset;
	u32 expected_register, header, value;
	u64 address;
	for (offset = 0; offset < 4096; offset += 8) {
		header = decode_word(ring_bytes + offset);
		value = decode_word(ring_bytes + offset + 4);
		expected_register = 0x3bff;
		if (!offset) expected_register = 0x3dbd;
		if (ps4_uvd_writeback) {
			if (offset == 8) expected_register = 0x3bc4;
			if (offset == 16) expected_register = 0x3bc5;
			if (offset == 24) expected_register = 0x3bc3;
		}
		if (header != expected_register) return false;
		if (!offset) {
			if (value != (ps4_uvd_writeback ? 0x55564433U : 0x55564432U)) return false;
		} else if ((!ps4_uvd_writeback || offset >= 24) && value) return false;
	}
	out->sequence = decode_word(ring_bytes + 4);
	out->address = 0;
	if (!ps4_uvd_writeback) return true;
	if (decode_word(ring_bytes + 20) > 0xff) return false;
	address = decode_word(ring_bytes + 12) | (u64)decode_word(ring_bytes + 20) << 32;
	if ((address & 3) || address < UINT64_C(0x30030a000) ||
	    address > UINT64_C(0x30030affc) || address != UINT64_C(0x30030a040)) return false;
	out->address = address;
	return true;
}
static u32 command_token(void)
{
	struct decoded decoded;
	assert(parse_page(&decoded));
	return decoded.sequence;
}
static void gpu_store(u64 address, u32 value)
{
	unsigned index;
	assert(!(address & 3) && address >= UINT64_C(0x30030a000) &&
	       address <= UINT64_C(0x30030affc));
	index = (unsigned)((address - UINT64_C(0x30030a000)) / 4);
	wb_storage.page[index] = cpu_to_le32(value);
	++gpu_word_writes;
}
static void cpu_store(__le32 *pointer, __le32 value)
{
	uintptr_t address = (uintptr_t)pointer, start = (uintptr_t)wb_storage.page;
	unsigned index;
	assert(address >= start && address <= start + 4092 && !(address & 3));
	assert(!enabled && !submitted && le32_to_cpu(value) != 0x55564433U);
	index = (unsigned)((address - start) / 4);
	if (scenario == CPU_INIT_CORRUPT && index == 1000) value = cpu_to_le32(7);
	*pointer = value;
	++cpu_word_writes;
}
static __le32 cpu_read(const __le32 *pointer)
{
	uintptr_t address = (uintptr_t)pointer, start = (uintptr_t)wb_storage.page;
	unsigned index;
	assert(address >= start && address <= start + 4092 && !(address & 3));
	index = (unsigned)((address - start) / 4);
	if (index == 16) ++target_reads; else ++guard_reads;
	if (index == 1023 && enabled && !submitted) empty_scan_completed = true;
	if (index == 1023 && submitted && scenario == SCAN_DEADLINE)
		now_ns = UINT64_C(250000000);
	return *pointer;
}
#define WRITE_ONCE(location,value) cpu_store(&(location),(value))
#define READ_ONCE(location) cpu_read(&(location))

static u32 mmio_read(u32 reg)
{
	assert(reg < 0x10000);
	if (reg == mmUVD_STATUS && enabled) {
		registers[mmUVD_STATUS] = scenario == BUSY_BOTH ? 3 : 2;
		if ((scenario == UNKNOWN_EMPTY && !submitted) ||
		    (scenario == UNKNOWN_SUBMITTED && submitted)) registers[mmUVD_STATUS] = 7;
		if (!submitted && scenario == ENABLE_EXPIRED) now_ns = UINT64_C(250000000);
		if (!submitted && scenario == EMPTY_PAGE_CORRUPT) wb_storage.page[1000] = cpu_to_le32(7);
		if (!submitted && scenario == EARLY_RESULT) wb_storage.page[16] = cpu_to_le32(0x55564433U);
	}
	if (reg == mmUVD_STATUS && submitted) {
		struct decoded decoded;
		assert(parse_page(&decoded));
		++submitted_samples;
		registers[mmUVD_CONTEXT_ID] = decoded.sequence;
		registers[mmUVD_RBC_RB_RPTR] = 16;
		if ((scenario == DRAINED_THEN_TOKEN || scenario == WORD_BEFORE_CONTEXT) && submitted_samples == 1)
			registers[mmUVD_CONTEXT_ID] = 0x43505533U;
		if ((scenario == TOKEN_THEN_POINTER || scenario == WORD_BEFORE_POINTER) && submitted_samples == 1)
			registers[mmUVD_RBC_RB_RPTR] = 0;
		if (scenario == WORD_WITHOUT_CONTEXT) registers[mmUVD_CONTEXT_ID] = 0x43505533U;
		if (scenario == EXACT_DEADLINE) now_ns = UINT64_C(250000000);
		if (scenario == LATE_SUCCESS) now_ns = UINT64_C(250000001);
		if (scenario == LAST_VALID_NS) now_ns = UINT64_C(249999999);
		if (scenario == NEVER_COMPLETE) {
			registers[mmUVD_CONTEXT_ID] = 0x43505533U;
			registers[mmUVD_RBC_RB_RPTR] = 0;
		}
		if (scenario == FOREIGN_CONTEXT) registers[mmUVD_CONTEXT_ID] = 0x12345678;
		if (scenario == MASTINT_CHANGED) registers[mmUVD_MASTINT_EN] = 6;
		if (scenario == RPTR_BYTES) registers[mmUVD_RBC_RB_RPTR] = 64;
		if (scenario == FAULT_CHANGED) registers[mmVM_CONTEXT1_PROTECTION_FAULT_STATUS] = 1;
		if (scenario == RESET_CHANGED) device.gpu_reset_counter = 1;
		if (scenario == SELECTOR_CHANGED) indexed[ixUVD_LMI_VMID_INTERNAL2] = 0;
		if (scenario == PD_CHANGED) registers[mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + 4] = 0;
		if (ps4_uvd_writeback && !gpu_word_writes && scenario != NEVER_WRITEBACK &&
		    !(scenario == DELAYED_WRITEBACK && submitted_samples == 1)) {
			u32 value = decoded.sequence;
			if (scenario == WRONG_MEMORY) value = 0x12345678;
			if (scenario == PARTIAL_MEMORY) value = 0x43504433;
			gpu_store(decoded.address, value);
			if (scenario == GUARD_FIRST) gpu_store(UINT64_C(0x30030a000), 7);
			if (scenario == GUARD_LAST) gpu_store(UINT64_C(0x30030affc), 9);
			/* Deliberately changed command registers are not completion guards. */
			registers[mmUVD_GPCOM_VCPU_DATA0] = 0xabcdef01;
			registers[mmUVD_GPCOM_VCPU_DATA1] = 0x55;
			registers[mmUVD_GPCOM_VCPU_CMD] = 0x99;
		}
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
		assert(value == (ps4_uvd_writeback ? 0x43505533U : 0x43505532U)); /* CPU must never write result token. */
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
	if (enabled && !submitted && empty_scan_completed && scenario == BEFORE_SUBMIT_EXPIRED)
		now_ns = UINT64_C(250000000);
}
static void amdgpu_device_flush_hdp(struct amdgpu_device *adev, void *ring)
{ (void)adev; assert(!ring); ++hdp_calls; }
static void amdgpu_device_invalidate_hdp(struct amdgpu_device *adev, void *ring)
{ (void)adev; assert(!ring); ++invalidate_calls; }
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
/* ACTUAL_WRITEBACK_FLOW */

static void receiver_preflight(struct amdgpu_device *adev,
			       const struct amdgpu_ps4_uvd_ring *rb,
			       const struct amdgpu_ps4_uvd_writeback *wb)
{
	/* ACTUAL_EARLY_GATE */
}

static void dispatch(struct amdgpu_device *adev, const struct amdgpu_ps4_uvd_ring *rb,
		     const struct amdgpu_ps4_uvd_writeback *wb)
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
	memset(&wb_storage, 0x5a, sizeof(wb_storage));
	cpu_word_writes = gpu_word_writes = target_reads = guard_reads = invalidate_calls = 0;
	empty_scan_completed = false;
	message_size = 0; messages[0] = 0; result_name = NULL; result_error = 99;
	context_writes = fetch_writes = pointer_writes = selector_writes = 0;
	submitted_samples = sleeps = barriers = hdp_calls = 0;
	now_ns = 0; enabled = submitted = false; scenario = next;
	ps4_uvd_ready = ps4_uvd_poll_state = ps4_uvd_token = ps4_uvd_writeback = true;
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

static struct amdgpu_ps4_uvd_writeback wb_descriptor(void)
{
	struct amdgpu_ps4_uvd_writeback wb = { wb_storage.page, 4096, UINT64_C(0x30030a000) };
	return wb;
}
static void run_case(const struct amdgpu_ps4_uvd_ring *rb,
	const struct amdgpu_ps4_uvd_writeback *wb, const char *expected,
	unsigned expected_fetch, unsigned expected_pointer)
{
	unsigned i;
	if (!setjmp(terminal)) dispatch(&device, rb, wb);
	if (!result_name || strcmp(result_name, expected)) {
		fprintf(stderr, "expected %s, got %s\n%s", expected, result_name, messages);
		abort();
	}
	assert(fetch_writes == expected_fetch && pointer_writes == expected_pointer);
	assert(registers[mmUVD_CTX_INDEX] == 0x55);
	for (i = 0; i < 8; ++i) {
		assert(wb_storage.before[i] == 0x5a5a5a5aU);
		assert(wb_storage.after[i] == 0x5a5a5a5aU);
	}
	assert(!strstr(messages, "PS4_UVD_TOKEN CORE ") || strstr(messages, "sampled=all"));
}

static void test_owner(void)
{
	struct resource resource = { TTM_PL_TT };
	struct amdgpu_bo ring_bo = { { &resource, 1, ttm_bo_type_kernel }, 4096 };
	struct amdgpu_bo wb_bo = { { &resource, 1, ttm_bo_type_kernel }, 0x20000 };
	struct ps4_canary_state state = { .uvd_rb = { &ring_bo, 0xdead000, ring_bytes },
		.uvd_ib = { &wb_bo, 0xbeef000, wb_storage.page }, .ready_mode = true,
		.submitted = true, .passed = false, .fence = &wb_bo, .mapped_pages = 812 };
	struct amdgpu_ps4_uvd_ring rb;
	struct amdgpu_ps4_uvd_writeback wb;
	ps4_canary_retained = &state;
	assert(ps4_canary_token_ring(&state, &rb));
	assert(ps4_canary_writeback_page(&state, &wb));
	assert(wb.cpu == wb_storage.page && wb.bytes == 4096 && wb.private_va == UINT64_C(0x30030a000));
	assert(wb.private_va != state.uvd_ib.gpu && !state.passed);
	wb_bo.bytes = 4096; assert(!ps4_canary_writeback_page(&state, &wb)); wb_bo.bytes = 0x20000;
	wb_bo.tbo.pin_count = 0; assert(!ps4_canary_writeback_page(&state, &wb)); wb_bo.tbo.pin_count = 1;
	state.uvd_ib.bo = &ring_bo; assert(!ps4_canary_writeback_page(&state, &wb)); state.uvd_ib.bo = &wb_bo;
	state.mapped_pages = 811; assert(!ps4_canary_writeback_page(&state, &wb)); state.mapped_pages = 812;
	state.fence = NULL; assert(!ps4_canary_writeback_page(&state, &wb)); state.fence = &wb_bo;
	state.uvd_ib.cpu = (u8 *)wb_storage.page + 1; assert(!ps4_canary_writeback_page(&state, &wb));
	state.uvd_ib.cpu = wb_storage.page; resource.mem_type = 0; assert(!ps4_canary_writeback_page(&state, &wb));
	resource.mem_type = TTM_PL_TT; ps4_canary_retained = NULL; assert(!ps4_canary_writeback_page(&state, &wb));
}

static void test_planner(const struct amdgpu_ps4_uvd_ring *rb, const struct amdgpu_ps4_uvd_writeback *wb)
{
	struct ps4_uvd_ring_request request = { rb->private_va, 0x43505533U, 0x55564433U, 0, 0, 0 };
	struct ps4_uvd_ring_plan plan;
	struct decoded decoded;
	u8 saved[4096];
	unsigned i;
	fresh(NORMAL); memcpy(saved, ring_bytes, sizeof(saved));
	assert(ps4_wb_build_ring(rb, wb, request, (void *)wb_storage.page) == PS4_UVD_PLAN_OVERLAP);
	assert(!memcmp(saved, ring_bytes, sizeof(saved)));
	assert(ps4_wb_build_ring(rb, wb, request, (void *)ring_bytes) == PS4_UVD_PLAN_OVERLAP);
	assert(!memcmp(saved, ring_bytes, sizeof(saved)));
	for (i = 0; i < 1024; ++i) assert(wb_storage.page[i] == 0x5a5a5a5aU);
	assert(ps4_wb_build_ring(rb, wb, request, &plan) == PS4_UVD_PLAN_OK);
	assert(plan.publish_wptr_raw == 16 && plan.submitted_dwords == 16);
	assert(parse_page(&decoded) && decoded.address == UINT64_C(0x30030a040));
	memcpy(saved, ring_bytes, sizeof(saved));
	/* Trap, IB/unknown header, address escape/high truncation and misalignment. */
	const struct { unsigned offset; u8 value; } bad[] = {
		{ 28, 2 }, { 0, 0xff }, { 24, 0xff }, { 12, 0x41 }, { 13, 0xb0 }, { 21, 1 }, { 64, 1 }
	};
	for (i = 0; i < sizeof(bad)/sizeof(bad[0]); ++i) {
		ring_bytes[bad[i].offset] = bad[i].value;
		assert(!parse_page(&decoded)); memcpy(ring_bytes, saved, sizeof(saved));
	}
	assert(!cpu_word_writes && !gpu_word_writes);
}

int main(int argc, char **argv)
{
	struct amdgpu_ps4_uvd_ring rb = descriptor();
	struct amdgpu_ps4_uvd_writeback wb = wb_descriptor();
	unsigned i;
	const struct { u32 reg, value; } preconditions[] = {
		{ mmUVD_STATUS, 3 }, { mmUVD_STATUS, 6 }, { mmUVD_MASTINT_EN, 0 },
		{ mmUVD_RBC_RB_CNTL, 0x1001080c }, { mmUVD_RBC_IB_SIZE, 1 },
		{ mmUVD_RBC_RB_WPTR, 1 }, { mmUVD_RBC_RB_RPTR, 1 },
		{ mmUVD_RBC_RB_WPTR_CNTL, 1 }, { mmUVD_RBC_RB_RPTR_ADDR, 1 },
		{ mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + 4, 0 },
		{ PS4_READY_UVD_VCPU_CACHE_VMID, 5 }, { mmUVD_LMI_RBC_RB_VMID, 0 },
		{ mmUVD_LMI_RBC_IB_VMID, 0 }, { mmUVD_LMI_RBC_IB_64BIT_BAR_LOW, 0 },
		{ mmUVD_LMI_SWAP_CNTL, 1 }, { mmUVD_SOFT_RESET, UVD_SOFT_RESET__RBC_SOFT_RESET_MASK }
	};
	const struct { enum scenario scenario; const char *result; unsigned fetch, pointer; } cases[] = {
		{ NORMAL, "WRITEBACK_OBSERVED", 1, 1 },
		{ BASELINE_READBACK, "WRITEBACK_BASELINE_READBACK", 0, 0 },
		{ ENABLE_READBACK, "WRITEBACK_ENABLE_GUARD", 1, 0 },
		{ ENABLE_EXPIRED, "WRITEBACK_ENABLE_TIMEOUT", 1, 0 },
		{ BEFORE_SUBMIT_EXPIRED, "WRITEBACK_BEFORE_SUBMIT_TIMEOUT", 1, 0 },
		{ WPTR_READBACK, "WRITEBACK_SUBMITTED_GUARD", 1, 1 },
		{ DRAINED_THEN_TOKEN, "WRITEBACK_OBSERVED", 1, 1 },
		{ TOKEN_THEN_POINTER, "WRITEBACK_OBSERVED", 1, 1 },
		{ EXACT_DEADLINE, "WRITEBACK_TIMEOUT", 1, 1 },
		{ LATE_SUCCESS, "WRITEBACK_TIMEOUT", 1, 1 },
		{ LAST_VALID_NS, "WRITEBACK_OBSERVED", 1, 1 },
		{ NEVER_COMPLETE, "WRITEBACK_TIMEOUT", 1, 1 },
		{ FOREIGN_CONTEXT, "WRITEBACK_UNEXPECTED_CONTEXT", 1, 1 },
		{ MASTINT_CHANGED, "WRITEBACK_SUBMITTED_GUARD", 1, 1 },
		{ RPTR_BYTES, "WRITEBACK_SUBMITTED_GUARD", 1, 1 },
		{ FAULT_CHANGED, "WRITEBACK_SUBMITTED_GUARD", 1, 1 },
		{ RESET_CHANGED, "WRITEBACK_SUBMITTED_GUARD", 1, 1 },
		{ SELECTOR_CHANGED, "WRITEBACK_SUBMITTED_GUARD", 1, 1 },
		{ PD_CHANGED, "WRITEBACK_SUBMITTED_GUARD", 1, 1 },
		{ BUSY_BOTH, "WRITEBACK_OBSERVED", 1, 1 },
		{ UNKNOWN_EMPTY, "WRITEBACK_ENABLE_GUARD", 1, 0 },
		{ UNKNOWN_SUBMITTED, "WRITEBACK_SUBMITTED_GUARD", 1, 1 },
		{ CPU_INIT_CORRUPT, "WRITEBACK_PUBLICATION_GUARD", 0, 0 },
		{ EMPTY_PAGE_CORRUPT, "WRITEBACK_ENABLE_GUARD", 1, 0 },
		{ EARLY_RESULT, "WRITEBACK_ENABLE_GUARD", 1, 0 },
		{ DELAYED_WRITEBACK, "WRITEBACK_OBSERVED", 1, 1 },
		{ NEVER_WRITEBACK, "WRITEBACK_TIMEOUT", 1, 1 },
		{ WRONG_MEMORY, "WRITEBACK_UNEXPECTED_WORD", 1, 1 },
		{ PARTIAL_MEMORY, "WRITEBACK_UNEXPECTED_WORD", 1, 1 },
		{ WORD_BEFORE_CONTEXT, "WRITEBACK_OBSERVED", 1, 1 },
		{ WORD_BEFORE_POINTER, "WRITEBACK_OBSERVED", 1, 1 },
		{ WORD_WITHOUT_CONTEXT, "WRITEBACK_TIMEOUT", 1, 1 },
		{ GUARD_FIRST, "WRITEBACK_PAGE_GUARD", 1, 1 },
		{ GUARD_LAST, "WRITEBACK_PAGE_GUARD", 1, 1 },
		{ SCAN_DEADLINE, "WRITEBACK_TIMEOUT", 1, 1 }
	};
	test_owner(); test_planner(&rb, &wb);
	fresh(NORMAL); ps4_uvd_writeback = false;
	receiver_preflight(&device, &rb, NULL);
	run_case(&rb, NULL, "TOKEN_TRANSPORT_OBSERVED", 1, 1);
	assert(!cpu_word_writes && !gpu_word_writes && !target_reads);
	fresh(NORMAL); ps4_uvd_writeback = ps4_uvd_token = false;
	run_case(NULL, NULL, "READY_CONVENTION_OBSERVED", 0, 0);
	assert(!context_writes && !cpu_word_writes);
	fresh(NORMAL); ps4_uvd_token = false;
	if (!setjmp(terminal)) { receiver_preflight(&device, &rb, &wb); abort(); }
	assert(!strcmp(result_name, "WRITEBACK_DESCRIPTOR_OR_GATES") && !context_writes && !cpu_word_writes);
	fresh(NORMAL); ps4_uvd_poll_state = false;
	if (!setjmp(terminal)) { receiver_preflight(&device, &rb, &wb); abort(); }
	assert(!strcmp(result_name, "WRITEBACK_DESCRIPTOR_OR_GATES") && !context_writes);
	assert(!ps4_wb_descriptor_valid(&rb, NULL));
	for (i = 0; i < 7; ++i) {
		wb = wb_descriptor();
		if (i == 0) wb.cpu = NULL;
		if (i == 1) wb.cpu = (u8 *)wb.cpu + 1;
		if (i == 2) wb.cpu = (void *)(~0UL - 3UL);
		if (i == 3) wb.cpu = ring_bytes;
		if (i == 4) wb.bytes = 8192;
		if (i == 5) wb.private_va = UINT64_C(1) << 40;
		if (i == 6) wb.private_va = rb.private_va;
		fresh(NORMAL); run_case(&rb, &wb, "WRITEBACK_DESCRIPTOR_OR_GATES", 0, 0);
		assert(!context_writes && !cpu_word_writes && !gpu_word_writes);
	}
	wb = wb_descriptor();
	fresh(NORMAL); wb_storage.page[16] = cpu_to_le32(0x55564433U);
	run_case(&rb, &wb, "WRITEBACK_STALE_WORD", 0, 0); assert(!cpu_word_writes && !context_writes);
	fresh(NORMAL); registers[mmUVD_CONTEXT_ID] = 0x55564433U;
	run_case(&rb, &wb, "WRITEBACK_PLAN_REJECTED", 0, 0); assert(!cpu_word_writes && !context_writes);
	for (i = 0; i < sizeof(preconditions)/sizeof(preconditions[0]); ++i) {
		fresh(NORMAL); registers[preconditions[i].reg] = preconditions[i].value;
		run_case(&rb, &wb, "WRITEBACK_PRECONDITION", 0, 0);
		assert(!cpu_word_writes && !context_writes);
	}
	for (i = 0; i < sizeof(cases)/sizeof(cases[0]); ++i) {
		fresh(cases[i].scenario);
		run_case(&rb, &wb, cases[i].result, cases[i].fetch, cases[i].pointer);
		assert(cpu_word_writes == 1024);
		if (!strcmp(cases[i].result, "WRITEBACK_OBSERVED")) {
			assert(!result_error && gpu_word_writes == 1 && submitted_samples >= 1);
			assert(le32_to_cpu(wb_storage.page[16]) == 0x55564433U);
			assert(strstr(messages, "guard_words=1023 intact=1"));
		} else assert(result_error < 0);
		if (cases[i].scenario == DELAYED_WRITEBACK || cases[i].scenario == WORD_BEFORE_CONTEXT ||
		    cases[i].scenario == WORD_BEFORE_POINTER) assert(submitted_samples == 2 && sleeps == 1);
		if (cases[i].scenario == GUARD_LAST) assert(strstr(messages, "GUARD index=1023 value=00000009"));
		if (cases[i].scenario == GUARD_FIRST) assert(strstr(messages, "GUARD index=0 value=00000007"));
		if (cases[i].scenario != BASELINE_READBACK) assert(guard_reads >= 1023 && guard_reads % 1023 == 0);
	}
	assert(argc == 2);
	assert((crc32_le(~0U, ring_bytes, sizeof(ring_bytes)) ^ ~0U) == 0x26f7b2c2U);
	FILE *image = fopen(argv[1], "wb");
	assert(image && fwrite(ring_bytes, 1, sizeof(ring_bytes), image) == sizeof(ring_bytes));
	assert(fclose(image) == 0);
	puts("PASS: actual writeback/owner/descriptor flow; 35 event cases + 16 exact preconditions; packet/page bounds, freshness, gates and token-only preservation");
	return 0;
}
