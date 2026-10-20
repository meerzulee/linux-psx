/* SPDX-License-Identifier: MIT */
/* Actual extracted frame controller under controlled register/time/memory
 * observations. This does not emulate firmware, packets, DMA or decoding.
 */
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef __APPLE__
#include <CommonCrypto/CommonDigest.h>
#else
#include <openssl/sha.h>
#endif
#include "amdgpu_ps4_uvd_frame.h"

#define U32_MAX UINT32_MAX
#define NSEC_PER_MSEC 1000000ULL
#define NSEC_PER_SEC 1000000000ULL
#define lower_32_bits(x) ((u32)(x))
#define upper_32_bits(x) ((u32)((u64)(x) >> 32))
#define le32_to_cpu(x) (x)
#define cpu_to_le32(x) (x)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define atomic_read(x) (*(x))
#define spin_lock_irqsave(x,f) do { (void)(x); (f)=0; } while (0)
#define spin_unlock_irqrestore(x,f) do { (void)(x); (void)(f); } while (0)
#define module_param_named(a,b,c,d)
#define MODULE_PARM_DESC(a,b)
#define IS_BUILTIN(x) builtin
#define IS_ENABLED(x) platform_enabled
#define CHIP_LIVERPOOL 1
#define CHIP_GLADIUS 2

@CONSTANTS@
@TYPES@
struct amdgpu_ps4_uvd_ring { void *cpu; size_t bytes; u64 private_va; };
struct amdgpu_ps4_uvd_ready_operation {
	bool boot_claimed, completed;
	struct { int terminal, error; } result;
};
#define PS4_READY_CORE_READY_CONVENTION_OBSERVED 7
struct amdgpu_device {
	void *dev;
	int asic_type, gpu_reset_counter, accel_working, in_reset;
	struct { int buffer_funcs_enabled; } mman;
	struct { struct { unsigned long lock; } uvd_ctx; } reg;
	struct amdgpu_ps4_uvd_ready_operation ps4_uvd_ready_operation;
	struct amdgpu_ps4_uvd_frame_operation ps4_uvd_frame_operation;
};
static struct amdgpu_device device;
static bool builtin, platform_enabled, prepare_only;
static bool ps4_uvd_ready, ps4_uvd_poll_state, ps4_uvd_token;
static bool ps4_uvd_data_control, ps4_uvd_status_observation, ps4_uvd_ib_data;
static bool ps4_uvd_message_create;
static _Alignas(8) u8 ring_cpu[4096], work_cpu[0x40000];
static u8 expected_pitched[0x6000], expected_feedback[2048];
static u32 regs[0x10000], indexed[0x1000];
static struct ps4_uvd_fault_snapshot supplied_history;
static unsigned int reads, writes, selectors, publications, observations[3];
static unsigned int checks, cases, flushes;
static u32 published[3];
static u32 previous_ib_low[3], previous_ib_high[3];
static u64 now_ns, tick_ns, sleep_ns;
static const char *case_name;
static u32 mutation_reg, mutation_value;
static unsigned int failure_phase;

enum scenario {
	NORMAL, DELAYED, TIMEOUT, PIXEL_MISMATCH, ONE_JOINT,
	SECOND_EXACT, FIRST_LATE, SECOND_LAST_VALID,
	FENCE_REGRESSION, CONTEXT_REGRESSION, POINTER_REGRESSION,
	BAD_FENCE, BAD_CONTEXT, CORE_BAD, HISTORY_BASELINE, HISTORY_POLL,
	HISTORY_DURING_PREPUBLICATION, RING_MUTATION, TARGET_PREPUBLICATION,
	MESSAGE_PREPUBLICATION, MESSAGE_AFTER_PUBLICATION, TAIL_MUTATION,
	IB_PRIOR_INFLIGHT, IB_WRONG_LOW, IB_WRONG_HIGH, IB_STALE_COMPLETED,
	IB_ADVANCED_PREPUBLICATION,
};
static enum scenario scenario;

static void fail(const char *witness, unsigned int line)
{
	fprintf(stderr, "FAIL case=%s witness=%s line=%u pubs=%u phase=%u result=%s error=%d ib=%08x:%08x\n",
		case_name, witness, line, publications, device.ps4_uvd_frame_operation.phase,
		device.ps4_uvd_frame_operation.result.name ? device.ps4_uvd_frame_operation.result.name : "pending",
		device.ps4_uvd_frame_operation.result.error,
		regs[mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH], regs[mmUVD_LMI_RBC_IB_64BIT_BAR_LOW]);
	exit(86);
}
#define CHECK(x) do { ++checks; if (!(x)) fail(#x, __LINE__); } while (0)
static u32 get_unaligned_le32(const void *p)
{
	const u8 *v = p;
	return (u32)v[0] | (u32)v[1] << 8 | (u32)v[2] << 16 | (u32)v[3] << 24;
}
static void put_unaligned_le32(u32 v, void *p)
{
	u8 *b = p;
	b[0] = v; b[1] = v >> 8; b[2] = v >> 16; b[3] = v >> 24;
}
static u32 crc32_le(u32 crc, const void *data, size_t size)
{
	const u8 *p = data;
	for (size_t i = 0; i < size; ++i) {
		crc ^= p[i];
		for (unsigned int j = 0; j < 8; ++j)
			crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320U : 0);
	}
	return crc;
}
static void sha256(const u8 *data, size_t bytes, u8 *digest)
{
#ifdef __APPLE__
	CC_SHA256(data, (CC_LONG)bytes, digest);
#else
	SHA256(data, bytes, digest);
#endif
}
static bool amdgpu_in_reset(struct amdgpu_device *adev) { return adev->in_reset; }
static bool amdgpu_ps4_live_prepare_enabled(struct amdgpu_device *adev)
{ (void)adev; return prepare_only; }
static bool amdgpu_ps4_uvd_ready_enabled(struct amdgpu_device *adev);
static void log_message(void *dev, const char *format, ...)
{ (void)dev; (void)format; }
#define dev_info log_message
static void ps4_create_report_deciding_history(struct amdgpu_device *adev,
					      const struct ps4_uvd_fault_snapshot *snapshot)
{ (void)adev; (void)snapshot; }
static u64 ktime_get_ns(void) { u64 n = now_ns; now_ns += tick_ns; return n; }
static void usleep_range(unsigned int lo, unsigned int hi)
{ CHECK(lo == 1000 && hi == 2000); now_ns += sleep_ns; }
static void mb(void) {}
static void rmb(void) {}
static void amdgpu_device_flush_hdp(struct amdgpu_device *adev, void *ring)
{ (void)adev; (void)ring; ++flushes; }

@FIXTURE@
@EXPECTED@

/* Expected output is copied from a separately decoded reference file. Padding
 * differs deliberately so packed and pitched extraction cannot agree by luck.
 */
static void fill_reference_target(void)
{
	unsigned int row;
	memset(expected_pitched, 0x6e, sizeof(expected_pitched));
	for (row = 0; row < 64; ++row)
		memcpy(expected_pitched + row * 256, expected_nv12 + row * 64, 64);
	for (row = 0; row < 32; ++row)
		memcpy(expected_pitched + 0x4000 + row * 256,
		       expected_nv12 + 4096 + row * 64, 64);
	for (row = 0; row < sizeof(expected_feedback); ++row)
		expected_feedback[row] = (u8)(row * 13U + 7U);
	memcpy(work_cpu + 0x6000, expected_pitched, sizeof(expected_pitched));
	memcpy(work_cpu + 0x4000, expected_feedback, sizeof(expected_feedback));
}

/* A submitted ring section writes these two registers before its IB is
 * fetched. They alias the public IB-base guard registers in the real sampler.
 * Supplying their packet values is essential: leaving the initial base in
 * place would let an incorrect constant-base guard pass every host test.
 */
static void apply_main_ib_registers(unsigned int phase)
{
	regs[mmUVD_LMI_RBC_IB_64BIT_BAR_LOW] = get_unaligned_le32(ring_cpu + phase * 64 + 4);
	regs[mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH] = get_unaligned_le32(ring_cpu + phase * 64 + 12);
}

static void supply_core_observation(void)
{
	struct amdgpu_ps4_uvd_frame_operation *op = &device.ps4_uvd_frame_operation;
	unsigned int phase = op->phase;
	struct ps4_frame_phase *p;
	if (!op->initialized || phase >= 3)
		return;
	p = &op->phases[phase];
	if (!p->published) {
		if (phase == failure_phase && scenario == HISTORY_DURING_PREPUBLICATION)
			supplied_history.history.total.arrivals = 1;
		if (phase == failure_phase && scenario == CORE_BAD)
			regs[mutation_reg] = mutation_value;
		if (phase == failure_phase && scenario == IB_ADVANCED_PREPUBLICATION)
			apply_main_ib_registers(phase);
		return;
	}
	++observations[phase];
	if (phase == failure_phase && scenario == IB_PRIOR_INFLIGHT) {
		if (observations[phase] == 1) {
			CHECK(regs[mmUVD_LMI_RBC_IB_64BIT_BAR_LOW] == previous_ib_low[phase]);
			CHECK(regs[mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH] == previous_ib_high[phase]);
			return;
		}
		apply_main_ib_registers(phase);
	}
	if (phase == failure_phase && scenario == IB_WRONG_LOW)
		regs[mmUVD_LMI_RBC_IB_64BIT_BAR_LOW] ^= 0x40U;
	if (phase == failure_phase && scenario == IB_WRONG_HIGH)
		regs[mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH] ^= 1U;
	if (phase == failure_phase && scenario == IB_STALE_COMPLETED) {
		regs[mmUVD_LMI_RBC_IB_64BIT_BAR_LOW] = previous_ib_low[phase];
		regs[mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH] = previous_ib_high[phase];
	}
	if (phase == failure_phase && scenario == TIMEOUT)
		return;
	if (phase == failure_phase && scenario == DELAYED && observations[phase] == 1)
		return;
	if (phase == failure_phase && scenario == POINTER_REGRESSION) {
		regs[mmUVD_RBC_RB_RPTR] = (phase ? 16 * phase : 0) +
			(observations[phase] == 1 ? 8 : 4);
		return;
	}
	regs[mmUVD_RBC_RB_RPTR] = p->end_dw;
	regs[mmUVD_CONTEXT_ID] = p->sequence;
	put_unaligned_le32(p->sequence, work_cpu + 0x2000 + p->cell);
	if (phase == 1) {
		fill_reference_target();
		if (scenario == PIXEL_MISMATCH)
			work_cpu[0x6000 + 37 * 256 + 9] ^= 1;
	}
	if (phase == failure_phase && observations[phase] == 2) {
		if (scenario == FENCE_REGRESSION)
			put_unaligned_le32(p->initial, work_cpu + 0x2000 + p->cell);
		if (scenario == CONTEXT_REGRESSION)
			regs[mmUVD_CONTEXT_ID] = p->previous_context;
	}
	if (phase == failure_phase && scenario == BAD_FENCE)
		put_unaligned_le32(0xdeadbeef, work_cpu + 0x2000 + p->cell);
	if (phase == failure_phase && scenario == BAD_CONTEXT)
		regs[mmUVD_CONTEXT_ID] = 0xdeadbeef;
	if (phase == failure_phase && scenario == HISTORY_POLL)
		supplied_history.history.vmid[14].arrivals = 1;
	if (phase == failure_phase && scenario == RING_MUTATION)
		ring_cpu[200] ^= observations[phase] == 1;
	if (phase == failure_phase && scenario == TAIL_MUTATION)
		work_cpu[0x3ffff] ^= observations[phase] == 1;
	if (phase == failure_phase && scenario == MESSAGE_PREPUBLICATION)
		work_cpu[phase == 0 ? 0x3004 : 0x33004] ^= observations[phase] == 1;
	if (phase == failure_phase && scenario == TARGET_PREPUBLICATION)
		work_cpu[0x6000] ^= observations[phase] == 1;
	if (scenario == MESSAGE_AFTER_PUBLICATION)
		work_cpu[phase == 0 ? 0x1004 : phase == 1 ? 0x3004 : 0x33004] ^= 0x11;
}

static u32 fixture_read(u32 reg)
{
	++reads;
	CHECK(reg < 0x10000);
	if (reg == mmUVD_STATUS)
		supply_core_observation();
	if (reg == mmUVD_CTX_DATA)
		return indexed[regs[mmUVD_CTX_INDEX]];
	return regs[reg];
}
static void fixture_write(u32 reg, u32 value)
{
	++writes;
	CHECK(reg < 0x10000);
	if (reg == mmUVD_CTX_INDEX) {
		++selectors;
		CHECK(value < 0x1000);
	} else if (reg == mmUVD_RBC_RB_WPTR) {
		static const u32 expected_wp[] = { 16, 32, 48 };
		static const u32 expected_ib[] = { 0, 0x80, 0x100 };
		unsigned int phase = publications;
		struct amdgpu_ps4_uvd_frame_operation *op = &device.ps4_uvd_frame_operation;
		CHECK(phase < 3 && value == expected_wp[phase]);
		if (phase) {
			CHECK(observations[phase - 1] >= 2);
			CHECK(op->phases[phase - 1].completed);
		}
		if (phase == 2)
			CHECK(op->pixels_captured && op->pixels_match);
		CHECK(get_unaligned_le32(ring_cpu + phase * 64 + 20) == (phase == 1 ? 32U : 16U));
		CHECK(get_unaligned_le32(ring_cpu + phase * 64) == 0x3c67U);
		CHECK(get_unaligned_le32(ring_cpu + phase * 64 + 8) == 0x3c66U);
		CHECK(get_unaligned_le32(ring_cpu + phase * 64 + 4) == lower_32_bits(PS4_READY_IB_VA + expected_ib[phase]));
		CHECK(get_unaligned_le32(ring_cpu + phase * 64 + 12) == upper_32_bits(PS4_READY_IB_VA + expected_ib[phase]));
		CHECK(get_unaligned_le32(work_cpu + (phase == 0 ? 0x1000 : phase == 1 ? 0x3000 : 0x33000) + 8) == 1);
		previous_ib_low[phase] = regs[mmUVD_LMI_RBC_IB_64BIT_BAR_LOW];
		previous_ib_high[phase] = regs[mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH];
		if (phase != failure_phase || scenario != IB_PRIOR_INFLIGHT)
			apply_main_ib_registers(phase);
		published[publications++] = value;
	} else {
		CHECK(reg == mmUVD_CONTEXT_ID || reg == mmUVD_RBC_RB_CNTL);
	}
	regs[reg] = value;
}
#define RREG32(reg) fixture_read(reg)
#define WREG32(reg,value) fixture_write((reg),(value))

static void amdgpu_ps4_uvd_irq_fault_snapshot(struct amdgpu_device *adev,
					     struct ps4_uvd_fault_snapshot *out)
{
	struct amdgpu_ps4_uvd_frame_operation *op = &adev->ps4_uvd_frame_operation;
	if (op->phase < 3 && op->phases[op->phase].published &&
	    !op->phases[op->phase].completed && op->phase == failure_phase) {
		unsigned int n = observations[op->phase];
		if ((scenario == SECOND_EXACT && n == 2) || (scenario == FIRST_LATE && n == 1))
			now_ns = op->phases[op->phase].start_ns + 2000000000ULL;
		if (scenario == SECOND_LAST_VALID && n == 2)
			now_ns = op->phases[op->phase].start_ns + 1999999999ULL;
	}
	*out = supplied_history;
}

@HELPERS@
@PARAMETERS@
@FLOW@

static void reset_case(const char *name, enum scenario next, unsigned int phase)
{
	++cases;
	case_name = name;
	scenario = next;
	failure_phase = phase;
	memset(&device, 0, sizeof(device));
	memset(regs, 0, sizeof(regs));
	memset(indexed, 0, sizeof(indexed));
	memset(ring_cpu, 0x99, sizeof(ring_cpu));
	memset(work_cpu, 0x77, sizeof(work_cpu));
	memset(observations, 0, sizeof(observations));
	memset(previous_ib_low, 0, sizeof(previous_ib_low));
	memset(previous_ib_high, 0, sizeof(previous_ib_high));
	memset(&supplied_history, 0, sizeof(supplied_history));
	supplied_history.active = true;
	supplied_history.arm_ns = 1;
	if (scenario == HISTORY_BASELINE)
		supplied_history.history.total.arrivals = 1;
	device.asic_type = CHIP_LIVERPOOL;
	device.ps4_uvd_ready_operation.boot_claimed = true;
	device.ps4_uvd_ready_operation.completed = true;
	device.ps4_uvd_ready_operation.result.terminal = PS4_READY_CORE_READY_CONVENTION_OBSERVED;
	builtin = platform_enabled = true;
	prepare_only = false;
	ps4_uvd_ready = ps4_uvd_poll_state = ps4_uvd_decode_frame = true;
	ps4_uvd_token = ps4_uvd_data_control = ps4_uvd_status_observation = false;
	ps4_uvd_ib_data = ps4_uvd_message_create = false;
	reads = writes = selectors = publications = flushes = 0;
	now_ns = 100;
	tick_ns = 100;
	sleep_ns = 1000000;
	if (scenario == ONE_JOINT)
		sleep_ns = 2000000000ULL;
	if (scenario == SECOND_EXACT || scenario == FIRST_LATE || scenario == SECOND_LAST_VALID)
		tick_ns = 0;
	regs[mmUVD_STATUS] = PS4_READY_BIT;
	regs[mmUVD_MASTINT_EN] = UVD_MASTINT_EN__VCPU_EN_MASK;
	regs[mmUVD_RBC_RB_CNTL] = PS4_TOKEN_RBC_HELD;
	regs[mmVM_CONTEXT0_PAGE_TABLE_BASE_ADDR + 4] = 0x12345;
	regs[PS4_READY_UVD_VCPU_CACHE_VMID] = 4;
	regs[mmUVD_LMI_RBC_RB_VMID] = regs[mmUVD_LMI_RBC_IB_VMID] = 4;
	regs[mmUVD_LMI_VCPU_CACHE_64BIT_BAR_LOW] = lower_32_bits(PS4_READY_VCPU_VA);
	regs[mmUVD_LMI_VCPU_CACHE_64BIT_BAR_HIGH] = upper_32_bits(PS4_READY_VCPU_VA);
	regs[mmUVD_LMI_RBC_RB_64BIT_BAR_LOW] = lower_32_bits(PS4_READY_RB_VA);
	regs[mmUVD_LMI_RBC_RB_64BIT_BAR_HIGH] = upper_32_bits(PS4_READY_RB_VA);
	regs[mmUVD_LMI_RBC_IB_64BIT_BAR_LOW] = lower_32_bits(PS4_READY_IB_VA);
	regs[mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH] = upper_32_bits(PS4_READY_IB_VA);
	regs[mmUVD_VCPU_CACHE_SIZE0] = 0x7d000;
	regs[mmUVD_VCPU_CACHE_OFFSET1] = 0xfa00;
	regs[mmUVD_VCPU_CACHE_SIZE1] = 0x40000;
	regs[mmUVD_VCPU_CACHE_OFFSET2] = 0x17a00;
	regs[mmUVD_VCPU_CACHE_SIZE2] = 0x120800;
	indexed[ixUVD_LMI_VMID_INTERNAL] = 0x00444444;
	indexed[ixUVD_LMI_VMID_INTERNAL2] = 0x44444444;
	indexed[ixUVD_LMI_VMID_INTERNAL3] = 0x4444;
}
static struct ps4_frame_result run_case(void)
{
	struct amdgpu_ps4_uvd_ring rb = { ring_cpu, 4096, PS4_READY_RB_VA };
	struct amdgpu_ps4_uvd_ring work = { work_cpu, 0x40000, PS4_READY_IB_VA };
	return ps4_frame_run(&device, &rb, &work, 0x12345000, 4, 0, 0);
}
static void expect_result(const char *name, int error, unsigned int count)
{
	struct ps4_frame_result result = run_case();
	CHECK(!strcmp(result.name, name));
	CHECK(result.error == error);
	CHECK(publications == count);
	CHECK(device.ps4_uvd_frame_operation.completed);
}

static void test_success(void)
{
	struct amdgpu_ps4_uvd_frame_operation *op = &device.ps4_uvd_frame_operation;
	unsigned int i, before_reads, before_writes;
	u8 *saved;
	reset_case("reference", NORMAL, 0);
	expect_result("FRAME_REFERENCE_MATCH", 0, 3);
	CHECK(published[0] == 16 && published[1] == 32 && published[2] == 48);
	CHECK(op->phase == 3 && op->pixels_captured && op->pixels_match);
	CHECK(!memcmp(op->visible, expected_nv12, 6144));
	CHECK(!memcmp(op->pitched, expected_pitched, 0x6000));
	CHECK(!memcmp(op->feedback, expected_feedback, 2048));
	for (i = 0; i < 3; ++i) {
		CHECK(op->phases[i].polls == 2 && op->phases[i].consecutive == 2);
		CHECK(op->phases[i].confirmed_ns < 2000000000ULL);
	}
	CHECK(get_unaligned_le32(work_cpu + 0x1000 + 28) == 64);
	CHECK(get_unaligned_le32(work_cpu + 0x1000 + 32) == 64);
	CHECK(get_unaligned_le32(work_cpu + 0x1000 + 40) == 0);
	CHECK(get_unaligned_le32(work_cpu + 0x33000 + 4) == 2);
	for (i = 0; i < 5; ++i) {
		static const u32 cmd[] = { 0, 1, 3, 0x100, 2 };
		static const u32 off[] = { 0x3000, 0xc000, 0x4000, 0x5000, 0x6000 };
		u8 *pair = work_cpu + 0x80 + i * 24;
		CHECK(get_unaligned_le32(pair) == 0x3bc4);
		CHECK(get_unaligned_le32(pair + 4) == lower_32_bits(PS4_READY_IB_VA + off[i]));
		CHECK(get_unaligned_le32(pair + 12) == upper_32_bits(PS4_READY_IB_VA + off[i]));
		CHECK(get_unaligned_le32(pair + 20) == cmd[i] * 2);
	}
	memset(work_cpu + 0x6000, 0x11, 0x6000);
	memset(work_cpu + 0x4000, 0x22, 2048);
	CHECK(!memcmp(op->pitched, expected_pitched, 0x6000));
	CHECK(!memcmp(op->feedback, expected_feedback, 2048));
	before_reads = reads; before_writes = writes;
	ps4_frame_report(&device);
	CHECK(reads == before_reads && writes == before_writes);
	saved = malloc(sizeof(*op));
	CHECK(saved);
	memcpy(saved, op, sizeof(*op));
	CHECK(run_case().error == -EPERM);
	CHECK(!memcmp(saved, op, sizeof(*op)));
	CHECK(reads == before_reads && writes == before_writes);
	free(saved);
}

static void test_failures(void)
{
	unsigned int phase;
	reset_case("delayed", DELAYED, 1);
	expect_result("FRAME_REFERENCE_MATCH", 0, 3);
	CHECK(device.ps4_uvd_frame_operation.phases[1].polls == 3);
	for (phase = 0; phase < 3; ++phase) {
		reset_case("timeout", TIMEOUT, phase);
		expect_result("FRAME_PHASE_TIMEOUT", -ETIMEDOUT, phase + 1);
		CHECK(!device.ps4_uvd_frame_operation.phases[phase].completed);
	}
	reset_case("pixels-mismatch", PIXEL_MISMATCH, 1);
	expect_result("FRAME_PIXELS_MISMATCH", -EBADMSG, 2);
	CHECK(device.ps4_uvd_frame_operation.pixels_captured);
	CHECK(!device.ps4_uvd_frame_operation.pixels_match);
	reset_case("one-joint", ONE_JOINT, 0);
	expect_result("FRAME_PHASE_TIMEOUT", -ETIMEDOUT, 1);
	CHECK(device.ps4_uvd_frame_operation.phases[0].polls == 1);
	reset_case("second-exact-deadline", SECOND_EXACT, 0);
	expect_result("FRAME_PHASE_TIMEOUT", -ETIMEDOUT, 1);
	reset_case("first-exact-deadline", FIRST_LATE, 0);
	expect_result("FRAME_PHASE_TIMEOUT", -ETIMEDOUT, 1);
	reset_case("second-before-deadline", SECOND_LAST_VALID, 0);
	expect_result("FRAME_REFERENCE_MATCH", 0, 3);
	CHECK(device.ps4_uvd_frame_operation.phases[0].confirmed_ns == 1999999999ULL);
	const enum scenario rejects[] = { FENCE_REGRESSION, CONTEXT_REGRESSION,
		POINTER_REGRESSION, BAD_FENCE, BAD_CONTEXT, HISTORY_POLL, RING_MUTATION,
		TAIL_MUTATION, MESSAGE_PREPUBLICATION, TARGET_PREPUBLICATION };
	for (phase = 0; phase < sizeof(rejects) / sizeof(rejects[0]); ++phase) {
		reset_case("submitted-guard", rejects[phase], 0);
		expect_result("FRAME_PHASE_GUARD", -EIO, 1);
	}
	reset_case("message-mutations-are-recorded", MESSAGE_AFTER_PUBLICATION, 0);
	expect_result("FRAME_REFERENCE_MATCH", 0, 3);
	reset_case("history-baseline", HISTORY_BASELINE, 0);
	expect_result("FRAME_HISTORY_BASELINE", -EBUSY, 0);
	CHECK(!device.ps4_uvd_frame_operation.initialized && writes == 0);
	reset_case("fault-during-prepublication-core", HISTORY_DURING_PREPUBLICATION, 1);
	expect_result("FRAME_PHASE_GUARD", -EIO, 1);
	reset_case("prepublication-direct-guard", CORE_BAD, 1);
	mutation_reg = mmUVD_LMI_RBC_IB_VMID; mutation_value = 5;
	expect_result("FRAME_PHASE_GUARD", -EIO, 1);
}

static void test_raw_options(void)
{
	unsigned int f;
	for (f = 0; f < 256; ++f) {
		reset_case("raw-options", NORMAL, 0);
		ps4_uvd_ready = !!(f & 1); ps4_uvd_poll_state = !!(f & 2);
		ps4_uvd_token = !!(f & 4); ps4_uvd_data_control = !!(f & 8);
		ps4_uvd_status_observation = !!(f & 16); ps4_uvd_ib_data = !!(f & 32);
		ps4_uvd_message_create = !!(f & 64); prepare_only = !!(f & 128);
		CHECK(amdgpu_ps4_uvd_decode_frame_options_valid(&device) == (f == 3));
		if (f != 3) {
			expect_result("FRAME_INPUTS", -EINVAL, 0);
			CHECK(reads == 0 && writes == 0 && !device.ps4_uvd_frame_operation.initialized);
		}
	}
	for (f = 0; f < 4; ++f) {
		reset_case("ready-authorization", NORMAL, 0);
		if (f == 0) device.ps4_uvd_ready_operation.boot_claimed = false;
		if (f == 1) device.ps4_uvd_ready_operation.completed = false;
		if (f == 2) device.ps4_uvd_ready_operation.result.terminal = 0;
		if (f == 3) device.ps4_uvd_ready_operation.result.error = -EIO;
		CHECK(run_case().error == -EPERM);
		CHECK(!device.ps4_uvd_frame_operation.entered && reads == 0 && writes == 0);
	}
}

static void test_ib_base_transitions(void)
{
	unsigned int phase;
	for (phase = 1; phase < 3; ++phase) {
		reset_case("prior-ib-while-inflight", IB_PRIOR_INFLIGHT, phase);
		expect_result("FRAME_REFERENCE_MATCH", 0, 3);
		CHECK(device.ps4_uvd_frame_operation.phases[phase].polls == 3);
		CHECK(observations[phase] >= 3);
		reset_case("current-ib-while-inflight", DELAYED, phase);
		expect_result("FRAME_REFERENCE_MATCH", 0, 3);
		CHECK(device.ps4_uvd_frame_operation.phases[phase].polls == 3);
		reset_case("wrong-ib-low", IB_WRONG_LOW, phase);
		expect_result("FRAME_PHASE_GUARD", -EIO, phase + 1);
		reset_case("wrong-ib-high", IB_WRONG_HIGH, phase);
		expect_result("FRAME_PHASE_GUARD", -EIO, phase + 1);
		reset_case("prior-ib-at-completion", IB_STALE_COMPLETED, phase);
		expect_result("FRAME_PHASE_GUARD", -EIO, phase + 1);
		reset_case("advanced-ib-before-publication", IB_ADVANCED_PREPUBLICATION, phase);
		expect_result("FRAME_PHASE_GUARD", -EIO, phase);
	}
}

static void test_memory_boundaries(void)
{
	struct amdgpu_ps4_uvd_frame_operation *op = &device.ps4_uvd_frame_operation;
	static const unsigned int offsets[] = { 0, 63, 64, 0x7f, 0x80, 0xff,
		0x100, 0x13f, 0x140, 0xfff, 0x1000, 0x1fff, 0x2000,
		0x2040, 0x2080, 0x20c0, 0x2fff, 0x3000, 0x3fff, 0x4000,
		0x47ff, 0x4800, 0x4fff, 0x5000, 0x5fff, 0x6000, 0xbfff,
		0xc000, 0x325ff, 0x32600, 0x32fff, 0x33000, 0x33fff,
		0x34000, 0x3ffff };
	unsigned int i;
	reset_case("guard-boundaries", NORMAL, 0);
	op->ring_cpu = ring_cpu; op->backing_cpu = work_cpu;
	op->ring_va = PS4_READY_RB_VA; op->backing_va = PS4_READY_IB_VA;
	CHECK(ps4_frame_plan(op) == 0);
	ps4_frame_initialize(op);
	CHECK(ps4_frame_memory(op));
	for (i = 0; i < sizeof(offsets) / sizeof(offsets[0]); ++i) {
		work_cpu[offsets[i]] ^= 1;
		CHECK(!ps4_frame_memory(op));
		work_cpu[offsets[i]] ^= 1;
	}
	op->phases[0].published = true;
	work_cpu[0x1004] ^= 1;
	CHECK(ps4_frame_memory(op));
	work_cpu[0x1004] ^= 1;
	op->phases[1].published = true;
	const unsigned int writable[] = { 0x3004, 0x4000, 0x47ff, 0x6000, 0xbfff, 0xc000, 0x325ff };
	for (i = 0; i < sizeof(writable) / sizeof(writable[0]); ++i) {
		work_cpu[writable[i]] ^= 1;
		CHECK(ps4_frame_memory(op));
		work_cpu[writable[i]] ^= 1;
	}
	work_cpu[0x32600] ^= 1;
	CHECK(!ps4_frame_memory(op));
	work_cpu[0x32600] ^= 1;
	CHECK(ps4_frame_memory(op));
}

int main(void)
{
	test_success();
	test_failures();
	test_raw_options();
	test_ib_base_transitions();
	test_memory_boundaries();
	printf("PASS cases=%u checks=%u actual_core_history=1 actual_serializers=1 real_sha256=1\n", cases, checks);
	return 0;
}
