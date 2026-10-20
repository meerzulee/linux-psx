/* SPDX-License-Identifier: MIT
 * Actual-source host fixture. pthread/atomics model software serialization
 * only; no device, kernel IRQ, MMIO, firmware or timing emulation is claimed.
 */
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <sched.h>

typedef unsigned int u32;
typedef unsigned long long u64;
typedef struct { _Atomic u64 value; } atomic64_t;
typedef struct { _Atomic int value; } atomic_t;
static unsigned cases;
static _Atomic u64 clock_calls;
static _Thread_local bool irq_enabled = true;
static _Thread_local bool in_gmc;
static _Thread_local bool ack_seen;

#define CHECK(condition, tag) do { if (!(condition)) { \
	fprintf(stderr, "FAIL:%s:%d\n", tag, __LINE__); exit(2); } } while (0)
#define atomic64_set(p, v) atomic_store(&(p)->value, (v))
#define atomic64_read(p) atomic_load(&(p)->value)
#define atomic64_inc_return(p) (atomic_fetch_add(&(p)->value, 1ULL) + 1ULL)
#define atomic_set(p, v) atomic_store(&(p)->value, (v))
#define atomic_read(p) atomic_load(&(p)->value)
#define atomic_set_release(p, v) atomic_store_explicit(&(p)->value, (v), memory_order_release)
#define atomic_read_acquire(p) atomic_load_explicit(&(p)->value, memory_order_acquire)
#define smp_load_acquire(p) __atomic_load_n((p), __ATOMIC_ACQUIRE)
#define smp_store_release(p, v) __atomic_store_n((p), (v), __ATOMIC_RELEASE)
static int atomic_cmpxchg(atomic_t *value, int expected, int desired)
{
	atomic_compare_exchange_strong(&value->value, &expected, desired);
	return expected;
}
typedef struct {
	pthread_mutex_t mutex;
	bool initialized;
	_Atomic unsigned int acquisitions;
	_Atomic unsigned int releases;
} spinlock_t;
void spin_lock_init(spinlock_t *lock)
{
	CHECK(!lock->initialized, "lock_double_init");
	CHECK(!pthread_mutex_init(&lock->mutex, NULL), "lock_init");
	atomic_init(&lock->acquisitions, 0);
	atomic_init(&lock->releases, 0);
	lock->initialized = true;
}
static void fixture_lock(spinlock_t *lock, unsigned long *flags)
{
	CHECK(lock->initialized, "lock_initialized");
	CHECK(!in_gmc || ack_seen, "record_after_ack");
	*flags = irq_enabled;
	irq_enabled = false;
	CHECK(!pthread_mutex_lock(&lock->mutex), "lock_acquire");
	atomic_fetch_add(&lock->acquisitions, 1);
}
static void fixture_unlock(spinlock_t *lock, unsigned long flags)
{
	CHECK(lock->initialized, "lock_initialized");
	CHECK(!irq_enabled, "irqsave_held");
	atomic_fetch_add(&lock->releases, 1);
	CHECK(!pthread_mutex_unlock(&lock->mutex), "lock_release");
	irq_enabled = flags;
}
#define spin_lock_irqsave(lock, flags) fixture_lock((lock), &(flags))
#define spin_unlock_irqrestore(lock, flags) fixture_unlock((lock), (flags))

#define VISLANDS30_IV_SRCID_UVD_SYSTEM_MESSAGE 124
#define VISLANDS30_IV_SRCID_GFX_PAGE_INV_FAULT 146
#define VISLANDS30_IV_SRCID_GFX_MEM_PROT_FAULT 147
#define AMDGPU_IRQ_CLIENTID_LEGACY 0
#define REG_GET_FIELD(value, reg, field) \
	(((value) & reg##__##field##_MASK) >> reg##__##field##__SHIFT)
/* @MASKS@ */
/* @HEADER@ */

struct mock_ih { bool enabled; };
struct amdgpu_iv_entry {
	struct mock_ih *ih;
	unsigned int client_id, src_id;
	u32 src_data[4], ring_id, vmid, pasid;
};
struct kfd_vm_fault_info {
	u32 vmid, mc_id, status, page_addr;
	bool prot_valid, prot_read, prot_write, prot_exec;
};
struct amdgpu_device {
	void *dev;
	struct ps4_uvd_irq_observer ps4_uvd_irq;
	struct { struct mock_ih ih, ih_soft; } irq;
	struct {
		atomic_t vm_fault_info_updated;
		struct kfd_vm_fault_info *vm_fault_info;
	} gmc;
};
struct amdgpu_irq_src { int unused; };
static u64 ktime_get_mono_fast_ns(void)
{
	return 1000 + atomic_fetch_add(&clock_calls, 1);
}
static void fixture_log(void *dev, const char *format, ...)
{
	(void)dev;
	(void)format;
}
#define dev_info fixture_log
#define dev_err fixture_log
/* @OBSERVER@ */

enum {
	mmVM_CONTEXT1_PROTECTION_FAULT_ADDR,
	mmVM_CONTEXT1_PROTECTION_FAULT_STATUS,
	mmVM_CONTEXT1_PROTECTION_FAULT_MCCLIENT,
	mmVM_CONTEXT1_CNTL2,
};
#define AMDGPU_GPU_PAGE_SHIFT 12
#define AMDGPU_GFXHUB(n) (n)
#define AMDGPU_VM_FAULT_STOP_FIRST 1
static int amdgpu_vm_fault_stop;
static bool permit_print, kfd_for_vmid8;
static u32 mock_addr, mock_status, mock_client;
static unsigned register_reads, register_writes, delegates, caches, prints, disables;
static unsigned trace[16], trace_count;
static bool require_record_at_cache;
static u64 expected_count_at_cache;
static void event(unsigned tag)
{
	CHECK(trace_count < 16, "trace_budget");
	trace[trace_count++] = tag;
}
static u32 fixture_rreg(unsigned reg)
{
	register_reads++;
	event(10 + reg);
	switch (reg) {
	case mmVM_CONTEXT1_PROTECTION_FAULT_ADDR: return mock_addr;
	case mmVM_CONTEXT1_PROTECTION_FAULT_STATUS: return mock_status;
	case mmVM_CONTEXT1_PROTECTION_FAULT_MCCLIENT: return mock_client;
	case mmVM_CONTEXT1_CNTL2: return 0x12340000;
	default: CHECK(false, "unexpected_register"); return 0;
	}
}
static void fixture_wreg_p(unsigned reg, u32 value, u32 preserve)
{
	CHECK(reg == mmVM_CONTEXT1_CNTL2 && value == 1 && preserve == ~1U,
	      "ack_arguments");
	(void)fixture_rreg(reg);
	register_writes++;
	event(20);
	mock_addr = mock_status = 0;
	ack_seen = true;
}
#define RREG32(reg) fixture_rreg(reg)
#define WREG32_P(reg, value, mask) fixture_wreg_p((reg), (value), (mask))
static void amdgpu_irq_delegate(struct amdgpu_device *adev,
			       struct amdgpu_iv_entry *entry, unsigned words)
{
	(void)adev;
	(void)entry;
	CHECK(words == 4, "delegate_width");
	delegates++;
	event(1);
}
static void amdgpu_vm_update_fault_cache(struct amdgpu_device *adev, u32 pasid,
					 u64 address, u32 status, int hub)
{
	struct ps4_uvd_fault_snapshot snapshot;
	(void)pasid;
	(void)address;
	(void)status;
	CHECK(hub == 0 && ack_seen, "cache_after_ack");
	if (require_record_at_cache) {
		amdgpu_ps4_uvd_irq_fault_snapshot(adev, &snapshot);
		CHECK(snapshot.history.total.arrivals == expected_count_at_cache,
		      "record_before_cache");
	}
	caches++;
	event(30);
}
static void gmc_v7_0_set_fault_enable_default(struct amdgpu_device *adev, bool on)
{
	(void)adev;
	CHECK(!on, "legacy_fault_stop");
	disables++;
	event(31);
}
static bool printk_ratelimit(void) { return permit_print; }
static void gmc_v7_0_vm_decode_fault(struct amdgpu_device *adev, u32 status,
				    u32 address, u32 client, u32 pasid)
{
	(void)adev; (void)status; (void)address; (void)client; (void)pasid;
	prints++;
	event(32);
}
static bool amdgpu_amdkfd_is_kfd_vmid(struct amdgpu_device *adev, u32 vmid)
{
	(void)adev;
	return kfd_for_vmid8 && vmid == 8;
}
/* @GMC_HANDLER@ */

static struct amdgpu_device *fresh(bool arm)
{
	struct amdgpu_device *adev = calloc(1, sizeof(*adev));
	CHECK(adev, "allocation");
	amdgpu_ps4_uvd_irq_init(adev);
	if (arm)
		amdgpu_ps4_uvd_irq_arm(adev);
	return adev;
}
static void destroy(struct amdgpu_device *adev)
{
	CHECK(adev->ps4_uvd_irq.fault_lock.initialized, "lock_initialized");
	CHECK(!pthread_mutex_destroy(&adev->ps4_uvd_irq.fault_lock.mutex), "destroy");
	free(adev);
}
static struct ps4_uvd_fault_snapshot snapshot(struct amdgpu_device *adev)
{
	struct ps4_uvd_fault_snapshot value;
	memset(&value, 0xa5, sizeof(value));
	amdgpu_ps4_uvd_irq_fault_snapshot(adev, &value);
	return value;
}
static u32 status_for(unsigned vmid, u32 tag)
{
	/* Independent numeric witness from the pinned public field definition. */
	return ((u32)vmid << 25) | (tag & 0x1ffffffU);
}
static bool same_record(struct ps4_uvd_fault_record a, struct ps4_uvd_fault_record b)
{
	return a.addr == b.addr && a.status == b.status && a.mc_client == b.mc_client;
}
static void expect_record(struct ps4_uvd_fault_record value, u32 addr,
			  u32 status, u32 client, const char *tag)
{
	CHECK(value.addr == addr && value.status == status && value.mc_client == client, tag);
}
static const struct ps4_uvd_fault_bucket *category(const struct ps4_uvd_fault_history *h,
						 unsigned i)
{
	return i == 16 ? &h->unclassified_status : &h->vmid[i];
}
static void check_history(const struct ps4_uvd_fault_history *h)
{
	u64 sum = 0;
	for (unsigned i = 0; i < 17; i++) {
		const struct ps4_uvd_fault_bucket *b = category(h, i);
		CHECK(b->saturated == (b->arrivals == ~0ULL), "bucket_saturation_consistent");
		if (~0ULL - sum < b->arrivals)
			sum = ~0ULL;
		else
			sum += b->arrivals;
		if (!b->arrivals) {
			expect_record(b->first, 0, 0, 0, "empty_first");
			expect_record(b->last, 0, 0, 0, "empty_last");
		} else if (i == 16) {
			CHECK(b->first.status == ~0U && b->last.status == ~0U, "sentinel_class");
		} else {
			CHECK(b->first.status != ~0U && b->last.status != ~0U &&
			      ((b->first.status >> 25) & 15U) == i &&
			      ((b->last.status >> 25) & 15U) == i, "vmid_category");
		}
	}
	CHECK(h->total.arrivals == sum, "total_category_sum");
	CHECK(h->total.saturated == (sum == ~0ULL), "total_saturation_consistent");
	if (!sum) {
		expect_record(h->total.first, 0, 0, 0, "empty_total_first");
		expect_record(h->total.last, 0, 0, 0, "empty_total_last");
	} else {
		unsigned index = h->total.last.status == ~0U ? 16 :
			((h->total.last.status >> 25) & 15U);
		CHECK(same_record(h->total.last, category(h, index)->last), "total_last_category");
	}
}
static void check_empty(struct ps4_uvd_fault_snapshot value, bool active)
{
	CHECK(value.active == active && !value.history.total.arrivals &&
	      !value.history.total.saturated, "empty_history");
	check_history(&value.history);
}
static void test_initialization_and_arm(void)
{
	struct amdgpu_device *adev = fresh(false);
	u64 clocks = atomic_load(&clock_calls);
	check_empty(snapshot(adev), false); cases++;
	CHECK(atomic_load(&adev->ps4_uvd_irq.fault_lock.acquisitions) == 0,
	      "inactive_snapshot_no_lock");
	amdgpu_ps4_uvd_irq_record_fault(adev, 1, status_for(14, 1), 3);
	amdgpu_ps4_uvd_irq_record_fault(adev, ~0U, ~0U, ~0U);
	check_empty(snapshot(adev), false); cases++;
	CHECK(atomic_load(&clock_calls) == clocks, "no_new_clock");
	amdgpu_ps4_uvd_irq_arm(adev);
	struct ps4_uvd_fault_snapshot before = snapshot(adev);
	check_empty(before, true); cases++;
	CHECK(before.arm_ns == 1000 + clocks, "original_arm_time");
	amdgpu_ps4_uvd_irq_record_fault(adev, 7, status_for(14, 9), 11);
	amdgpu_ps4_uvd_irq_arm(adev);
	struct ps4_uvd_fault_snapshot after = snapshot(adev);
	check_history(&after.history);
	CHECK(after.arm_ns == before.arm_ns && after.history.total.arrivals == 1 &&
	      after.history.vmid[14].arrivals == 1, "arm_does_not_reset");
	CHECK(atomic_load(&clock_calls) == clocks + 1, "no_new_clock"); cases++;
	destroy(adev);
}
static void test_all_vmids_and_isolation(void)
{
	struct amdgpu_device *adev = fresh(true);
	for (unsigned vmid = 0; vmid < 16; vmid++) {
		amdgpu_ps4_uvd_irq_record_fault(adev, 0x1000 + vmid,
					      status_for(vmid, 0x551), 0x2000 + vmid);
		struct ps4_uvd_fault_snapshot a = snapshot(adev);
		CHECK(a.history.total.arrivals == vmid + 1, "all_vmid_total");
		for (unsigned j = 0; j < 16; j++) {
			const struct ps4_uvd_fault_bucket *b = &a.history.vmid[j];
			CHECK(b->arrivals == (j <= vmid ? 1U : 0U), "bucket_noninterference");
			if (j <= vmid) {
				expect_record(b->first, 0x1000 + j, status_for(j, 0x551),
					      0x2000 + j, "category_first");
				CHECK(same_record(b->first, b->last), "category_initial_last");
			}
		}
		expect_record(a.history.total.first, 0x1000, status_for(0, 0x551),
			      0x2000, "total_first_immutable");
		check_history(&a.history); cases++;
	}
	for (u32 i = 1; i < 5; i++)
		amdgpu_ps4_uvd_irq_record_fault(adev, i, status_for(4, i), i ^ 0xa5U);
	struct ps4_uvd_fault_snapshot a = snapshot(adev);
	CHECK(a.history.total.arrivals == 20 && a.history.vmid[4].arrivals == 5,
	      "repeat_count");
	expect_record(a.history.vmid[4].first, 0x1004, status_for(4, 0x551),
		      0x2004, "first_immutable");
	expect_record(a.history.vmid[4].last, 4, status_for(4, 4), 4 ^ 0xa5U, "last_tuple");
	check_history(&a.history); cases++;
	struct amdgpu_device *other = fresh(true);
	check_empty(snapshot(other), true);
	amdgpu_ps4_uvd_irq_record_fault(other, 9, status_for(14, 10), 11);
	CHECK(snapshot(other).history.total.arrivals == 1 &&
	      snapshot(adev).history.total.arrivals == 20, "device_noninterference"); cases++;
	destroy(other); destroy(adev);
}
static void test_sentinel_and_vmid15(void)
{
	struct amdgpu_device *adev = fresh(true);
	amdgpu_ps4_uvd_irq_record_fault(adev, 1, status_for(15, 2), 3);
	struct ps4_uvd_fault_snapshot a = snapshot(adev);
	CHECK(a.history.vmid[15].arrivals == 1 && !a.history.unclassified_status.arrivals,
	      "normal_vmid15"); check_history(&a.history); cases++;
	amdgpu_ps4_uvd_irq_record_fault(adev, 0x123, ~0U, 0x456);
	a = snapshot(adev);
	CHECK(a.history.total.arrivals == 2 && a.history.unclassified_status.arrivals == 1 &&
	      a.history.vmid[15].arrivals == 1, "sentinel_not_vmid15");
	expect_record(a.history.unclassified_status.first, 0x123, ~0U, 0x456, "sentinel_raw");
	check_history(&a.history); cases++;
	amdgpu_ps4_uvd_irq_record_fault(adev, ~0U, ~0U, ~0U);
	a = snapshot(adev);
	CHECK(a.history.total.arrivals == 3 && a.history.unclassified_status.arrivals == 2,
	      "all_ones_tuple_count");
	expect_record(a.history.unclassified_status.last, ~0U, ~0U, ~0U, "all_ones_raw");
	CHECK(!same_record(a.history.unclassified_status.first,
			  a.history.unclassified_status.last), "status_vs_tuple_distinct");
	check_history(&a.history); cases++;
	amdgpu_ps4_uvd_irq_record_fault(adev, 0, ~0U, 0);
	amdgpu_ps4_uvd_irq_record_fault(adev, 5, 0xfffffffeU, 6);
	a = snapshot(adev);
	CHECK(a.history.total.arrivals == 5 && a.history.unclassified_status.arrivals == 3 &&
	      a.history.vmid[15].arrivals == 2, "only_literal_sentinel");
	expect_record(a.history.unclassified_status.last, 0, ~0U, 0, "sentinel_zero_others");
	check_history(&a.history); cases++;
	destroy(adev);
}
static void test_saturation(void)
{
	struct amdgpu_device *adev = fresh(true);
	amdgpu_ps4_uvd_irq_record_fault(adev, 2, status_for(2, 2), 2);
	amdgpu_ps4_uvd_irq_record_fault(adev, 3, status_for(3, 3), 3);
	struct ps4_uvd_fault_history *h = &adev->ps4_uvd_irq.fault_history;
	/* Fixture-only near-limit states, before concurrency; no production seed API.
	 * Seeded category sums agree with total, including saturating arithmetic.
	 */
	h->total.arrivals = ~0ULL - 1;
	h->vmid[2].arrivals = ~0ULL - 2;
	amdgpu_ps4_uvd_irq_record_fault(adev, 4, status_for(3, 4), 4);
	struct ps4_uvd_fault_snapshot a = snapshot(adev);
	CHECK(a.history.total.arrivals == ~0ULL && a.history.total.saturated &&
	      a.history.vmid[3].arrivals == 2 && !a.history.vmid[3].saturated &&
	      !a.history.vmid[2].saturated, "total_saturation_independent");
	check_history(&a.history); cases++;
	amdgpu_ps4_uvd_irq_record_fault(adev, 5, status_for(3, 5), 5);
	a = snapshot(adev);
	CHECK(a.history.total.arrivals == ~0ULL && a.history.total.saturated &&
	      a.history.vmid[3].arrivals == 3, "no_wrap");
	expect_record(a.history.total.first, 2, status_for(2, 2), 2, "saturated_first");
	expect_record(a.history.total.last, 5, status_for(3, 5), 5, "total_last_after_saturation");
	check_history(&a.history); cases++;
	destroy(adev);

	adev = fresh(true);
	amdgpu_ps4_uvd_irq_record_fault(adev, 4, status_for(4, 4), 4);
	amdgpu_ps4_uvd_irq_record_fault(adev, 7, status_for(7, 7), 7);
	h = &adev->ps4_uvd_irq.fault_history;
	h->total.arrivals = ~0ULL; h->total.saturated = true;
	h->vmid[4].arrivals = ~0ULL - 1;
	amdgpu_ps4_uvd_irq_record_fault(adev, 8, status_for(4, 8), 8);
	a = snapshot(adev);
	CHECK(a.history.vmid[4].arrivals == ~0ULL && a.history.vmid[4].saturated &&
	      a.history.vmid[7].arrivals == 1 && !a.history.vmid[7].saturated,
	      "category_saturation_independent");
	check_history(&a.history); cases++;
	amdgpu_ps4_uvd_irq_record_fault(adev, 9, status_for(4, 9), 9);
	amdgpu_ps4_uvd_irq_record_fault(adev, 10, status_for(8, 10), 10);
	a = snapshot(adev);
	CHECK(a.history.vmid[4].arrivals == ~0ULL && a.history.vmid[4].saturated &&
	      a.history.vmid[8].arrivals == 1, "category_no_wrap");
	expect_record(a.history.vmid[4].first, 4, status_for(4, 4), 4, "category_saturated_first");
	expect_record(a.history.vmid[4].last, 9, status_for(4, 9), 9, "category_last_after_saturation");
	check_history(&a.history); cases++;
	destroy(adev);

	adev = fresh(true);
	amdgpu_ps4_uvd_irq_record_fault(adev, 11, ~0U, 12);
	amdgpu_ps4_uvd_irq_record_fault(adev, 13, status_for(0, 13), 14);
	h = &adev->ps4_uvd_irq.fault_history;
	h->total.arrivals = ~0ULL; h->total.saturated = true;
	h->unclassified_status.arrivals = ~0ULL - 1;
	amdgpu_ps4_uvd_irq_record_fault(adev, ~0U, ~0U, ~0U);
	a = snapshot(adev);
	CHECK(a.history.unclassified_status.arrivals == ~0ULL &&
	      a.history.unclassified_status.saturated && !a.history.vmid[0].saturated,
	      "unclassified_saturation_independent");
	check_history(&a.history); cases++;
	amdgpu_ps4_uvd_irq_record_fault(adev, 15, ~0U, 16);
	a = snapshot(adev);
	CHECK(a.history.unclassified_status.arrivals == ~0ULL &&
	      a.history.unclassified_status.saturated, "unclassified_no_wrap");
	expect_record(a.history.unclassified_status.first, 11, ~0U, 12, "unclassified_first");
	expect_record(a.history.unclassified_status.last, 15, ~0U, 16, "unclassified_last");
	check_history(&a.history); cases++;
	destroy(adev);
}
static void test_locks_and_no_mmio(void)
{
	struct amdgpu_device *adev = fresh(true);
	u64 clocks = atomic_load(&clock_calls);
	unsigned reads = register_reads, writes = register_writes;
	unsigned before = atomic_load(&adev->ps4_uvd_irq.fault_lock.acquisitions);
	irq_enabled = false;
	amdgpu_ps4_uvd_irq_record_fault(adev, 4, status_for(14, 5), 6);
	CHECK(!irq_enabled, "restore_disabled_irqs");
	CHECK(atomic_load(&adev->ps4_uvd_irq.fault_lock.acquisitions) == before + 1,
	      "record_lock");
	struct ps4_uvd_fault_snapshot a = snapshot(adev);
	CHECK(!irq_enabled && a.history.total.arrivals == 1 && a.history.vmid[14].arrivals == 1,
	      "snapshot_disabled_irqs");
	CHECK(atomic_load(&adev->ps4_uvd_irq.fault_lock.acquisitions) == before + 2,
	      "snapshot_lock");
	irq_enabled = true;
	(void)snapshot(adev);
	CHECK(irq_enabled, "restore_enabled_irqs");
	CHECK(register_reads == reads && register_writes == writes &&
	      atomic_load(&clock_calls) == clocks, "no_extra_mmio_or_clock"); cases++;
	CHECK(atomic_load(&adev->ps4_uvd_irq.fault_lock.acquisitions) ==
	      atomic_load(&adev->ps4_uvd_irq.fault_lock.releases), "balanced_lock"); cases++;
	destroy(adev);
}
static void test_legacy_unchanged(void)
{
	struct amdgpu_device *adev = fresh(true);
	struct amdgpu_iv_entry entry = {
		.ih = &adev->irq.ih, .client_id = AMDGPU_IRQ_CLIENTID_LEGACY,
		.src_id = 146, .src_data = {0x55}, .ring_id = 6, .vmid = 2, .pasid = 9,
	};
	amdgpu_ps4_uvd_irq_observe(adev, &entry);
	entry.vmid = 4; entry.src_data[0] = 0x77;
	amdgpu_ps4_uvd_irq_observe(adev, &entry);
	struct ps4_uvd_irq_slot *slot = &adev->ps4_uvd_irq.slots[1];
	CHECK(atomic64_read(&slot->arrivals) == 2 && slot->vmid == 2 &&
	      slot->src_data0 == 0x55 && atomic_read(&slot->first_state) == PS4_UVD_IRQ_READY,
	      "legacy_first_claim");
	check_empty(snapshot(adev), true); cases++;
	amdgpu_ps4_uvd_irq_record_fault(adev, 7, status_for(14, 8), 9);
	CHECK(atomic64_read(&slot->arrivals) == 2 && slot->vmid == 2,
	      "new_record_preserves_legacy");
	CHECK(snapshot(adev).history.vmid[14].arrivals == 1, "iv_not_gmc"); cases++;
	atomic64_set(&slot->arrivals, ~0ULL);
	amdgpu_ps4_uvd_irq_observe(adev, &entry);
	CHECK(!atomic64_read(&slot->arrivals) && atomic_read(&slot->wrapped) == 1,
	      "legacy_wrap_unchanged"); cases++;
	destroy(adev);
}
static void reset_gmc(u32 addr, u32 status, u32 client)
{
	mock_addr = addr; mock_status = status; mock_client = client;
	register_reads = register_writes = delegates = caches = prints = disables = 0;
	trace_count = 0; ack_seen = false;
	permit_print = kfd_for_vmid8 = require_record_at_cache = false;
	amdgpu_vm_fault_stop = 0;
}
static int run_gmc(struct amdgpu_device *adev, struct amdgpu_iv_entry *entry)
{
	in_gmc = true;
	int r = gmc_v7_0_process_interrupt(adev, NULL, entry);
	in_gmc = false;
	return r;
}
static void test_gmc_wiring(void)
{
	struct amdgpu_device *adev = fresh(true);
	struct amdgpu_iv_entry entry = {
		.ih = &adev->irq.ih, .src_id = 146, .pasid = 44, .vmid = 4,
	};
	reset_gmc(0x101, status_for(3, 0x31), 0x202);
	require_record_at_cache = true; expected_count_at_cache = 1;
	CHECK(!adev->irq.ih_soft.enabled && !run_gmc(adev, &entry), "plain_main_handler");
	struct ps4_uvd_fault_snapshot a = snapshot(adev);
	CHECK(!delegates && register_reads == 4 && register_writes == 1 && caches == 1,
	      "plain_main_mmio");
	CHECK(trace_count == 6 && trace[0] == 10 && trace[1] == 11 && trace[2] == 12 &&
	      trace[3] == 13 && trace[4] == 20 && trace[5] == 30, "plain_main_order");
	expect_record(a.history.vmid[3].last, 0x101, status_for(3, 0x31), 0x202,
		      "plain_main_captured_tuple");
	check_history(&a.history); cases++;

	/* Exact STATUS from preserved EXP-20261018-007. Other test fields are synthetic. */
	reset_gmc(0x111, 0x1c03b002U, 0x333);
	adev->irq.ih_soft.enabled = true;
	CHECK(run_gmc(adev, &entry) == 1 && delegates == 1 &&
	      !register_reads && !register_writes && !caches, "soft_delegate_before_reads");
	CHECK(snapshot(adev).history.total.arrivals == 1, "delegation_does_not_record"); cases++;
	entry.ih = &adev->irq.ih_soft;
	require_record_at_cache = true; expected_count_at_cache = 2;
	CHECK(run_gmc(adev, &entry) == 0, "soft_handler_return");
	a = snapshot(adev);
	CHECK(register_reads == 4 && register_writes == 1 && caches == 1,
	      "existing_mmio_only");
	CHECK(trace_count == 7 && trace[0] == 1 && trace[1] == 10 &&
	      trace[2] == 11 && trace[3] == 12 && trace[4] == 13 &&
	      trace[5] == 20 && trace[6] == 30, "mmio_order");
	expect_record(a.history.vmid[14].last, 0x111, 0x1c03b002U, 0x333,
		      "observed_vmid14_status");
	CHECK(a.history.vmid[14].arrivals == 1 && !a.history.vmid[4].arrivals &&
	      entry.vmid == 4, "status_vmid_not_iv_vmid");
	check_history(&a.history); cases++;
	reset_gmc(0, 0, 0x55);
	CHECK(!run_gmc(adev, &entry) && !caches && register_reads == 4 &&
	      register_writes == 1 && snapshot(adev).history.total.arrivals == 2,
	      "empty_tuple_no_record"); cases++;
	reset_gmc(0x100, status_for(8, 0x77), 0x200);
	struct kfd_vm_fault_info info = {0};
	adev->gmc.vm_fault_info = &info;
	kfd_for_vmid8 = true; permit_print = true;
	amdgpu_vm_fault_stop = AMDGPU_VM_FAULT_STOP_FIRST;
	CHECK(!run_gmc(adev, &entry) && caches == 1 && disables == 1 && prints == 1,
	      "legacy_after_record_order");
	a = snapshot(adev);
	CHECK(info.vmid == 8 && info.status == status_for(8, 0x77) &&
	      atomic_read(&adev->gmc.vm_fault_info_updated) == 1 &&
	      a.history.total.arrivals == 3 && a.history.vmid[8].arrivals == 1,
	      "kfd_state_preserved");
	CHECK(trace[4] == 20 && trace[5] == 30 && trace[6] == 31 && trace[7] == 32,
	      "legacy_post_ack_order");
	check_history(&a.history); cases++;
	reset_gmc(0x222, ~0U, 0x444);
	CHECK(!run_gmc(adev, &entry), "sentinel_handler");
	a = snapshot(adev);
	CHECK(a.history.total.arrivals == 4 && a.history.unclassified_status.arrivals == 1 &&
	      !a.history.vmid[15].arrivals, "handler_sentinel_class");
	check_history(&a.history); cases++;
	reset_gmc(~0U, ~0U, ~0U);
	CHECK(!run_gmc(adev, &entry), "all_ones_handler");
	a = snapshot(adev);
	CHECK(a.history.total.arrivals == 5 && a.history.unclassified_status.arrivals == 2,
	      "handler_all_ones_count");
	expect_record(a.history.unclassified_status.first, 0x222, ~0U, 0x444,
		      "handler_status_vs_tuple_first");
	expect_record(a.history.unclassified_status.last, ~0U, ~0U, ~0U,
		      "handler_status_vs_tuple_last");
	check_history(&a.history); cases++;
	destroy(adev);

	adev = fresh(false); entry.ih = &adev->irq.ih;
	reset_gmc(8, 0x1c03b002U, 9);
	CHECK(!run_gmc(adev, &entry) && register_reads == 4 && register_writes == 1 && caches == 1,
	      "inactive_handler_unchanged");
	check_empty(snapshot(adev), false); cases++;
	destroy(adev);
}
struct concurrent {
	struct amdgpu_device *adev;
	_Atomic unsigned writers_done;
};
struct writer_arg { struct concurrent *c; u32 base; };
static void *writer(void *arg)
{
	struct writer_arg *w = arg;
	for (u32 i = 0; i < 17U * 64U; i++) {
		unsigned index = i % 17;
		u32 addr = w->base + i;
		u32 status = index == 16 ? ~0U : status_for(index, addr);
		amdgpu_ps4_uvd_irq_record_fault(w->c->adev, addr, status, addr ^ 0xa5a5a5a5U);
	}
	atomic_fetch_add(&w->c->writers_done, 1);
	return NULL;
}
static void *reader(void *arg)
{
	struct concurrent *c = arg;
	u64 previous_total = 0, previous[17] = {0};
	struct ps4_uvd_fault_record first[17] = {{0}};
	unsigned rounds = 0;
	do {
		struct ps4_uvd_fault_snapshot a = snapshot(c->adev);
		CHECK(a.active && !a.history.total.saturated &&
		      a.history.total.arrivals >= previous_total, "coherent_count");
		/* Unsaturated total must equal the exact sum in this same snapshot. */
		check_history(&a.history);
		expect_record(a.history.total.first, 1, status_for(0, 1), 1 ^ 0xa5a5a5a5U,
			      "concurrent_total_first");
		for (unsigned i = 0; i < 17; i++) {
			const struct ps4_uvd_fault_bucket *b = category(&a.history, i);
			CHECK(!b->saturated && b->arrivals >= previous[i], "category_monotonic");
			if (b->arrivals) {
				CHECK(b->last.status == (i == 16 ? ~0U : status_for(i, b->last.addr)) &&
				      b->last.mc_client == (b->last.addr ^ 0xa5a5a5a5U), "coherent_tuple");
				if (previous[i])
					CHECK(same_record(first[i], b->first), "concurrent_first_stable");
				else
					first[i] = b->first;
			}
			previous[i] = b->arrivals;
		}
		previous_total = a.history.total.arrivals;
		rounds++;
		if (!(rounds & 63))
			sched_yield();
	} while (atomic_load(&c->writers_done) < 2 || rounds < 2000);
	return NULL;
}
static void test_concurrency(void)
{
	struct concurrent c = {.adev = fresh(true)};
	struct writer_arg args[2] = {{&c, 0x1000}, {&c, 0x2000}};
	pthread_t threads[3];
	amdgpu_ps4_uvd_irq_record_fault(c.adev, 1, status_for(0, 1), 1 ^ 0xa5a5a5a5U);
	CHECK(!pthread_create(&threads[0], NULL, reader, &c), "reader_create");
	CHECK(!pthread_create(&threads[1], NULL, writer, &args[0]), "writer_create");
	CHECK(!pthread_create(&threads[2], NULL, writer, &args[1]), "writer_create");
	for (unsigned i = 0; i < 3; i++)
		CHECK(!pthread_join(threads[i], NULL), "join");
	struct ps4_uvd_fault_snapshot a = snapshot(c.adev);
	CHECK(a.history.total.arrivals == 2177 && a.history.unclassified_status.arrivals == 128,
	      "concurrent_total");
	for (unsigned i = 0; i < 16; i++)
		CHECK(a.history.vmid[i].arrivals == 128U + (i == 0), "concurrent_category_total");
	check_history(&a.history); cases++;
	CHECK(atomic_load(&c.adev->ps4_uvd_irq.fault_lock.acquisitions) ==
	      atomic_load(&c.adev->ps4_uvd_irq.fault_lock.releases), "concurrent_unlocks");
	cases++;
	destroy(c.adev);
}
int main(void)
{
	CHECK(VM_CONTEXT1_PROTECTION_FAULT_STATUS__VMID_MASK == 0x1e000000U &&
	      VM_CONTEXT1_PROTECTION_FAULT_STATUS__VMID__SHIFT == 25 &&
	      PS4_UVD_FAULT_VMIDS == 16, "public_vmid_mask");
	test_initialization_and_arm();
	test_all_vmids_and_isolation();
	test_sentinel_and_vmid15();
	test_saturation();
	test_locks_and_no_mmio();
	test_legacy_unchanged();
	test_gmc_wiring();
	test_concurrency();
	printf("PASS: %u actual-source captured-fault history cases; synthetic locks/registers only\n", cases);
	return 0;
}
