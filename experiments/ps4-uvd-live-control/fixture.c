/* SPDX-License-Identifier: MIT
 * Actual-source host fixture for the unwired PS4 live-control helper.
 * pthread mutexes and C11 atomics model software serialization only. No
 * Linux IRQ masking, preemption, kernel memory model, MMIO, firmware,
 * worker, reset or device behaviour is emulated or claimed.
 */
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned int u32;
typedef unsigned long long u64;
typedef struct { _Atomic int value; } atomic_t;
#define ATOMIC_INIT(v) { (v) }
#define U32_MAX ((u32)~0U)
#define U64_MAX ((u64)~0ULL)
#define BIT(n) (1UL << (n))
#define smp_load_acquire(p) __atomic_load_n((p), __ATOMIC_ACQUIRE)
#define smp_store_release(p, v) __atomic_store_n((p), (v), __ATOMIC_RELEASE)

#define CHECK(condition, tag) do { if (!(condition)) { \
	fprintf(stderr, "FAIL:%s:%d\n", tag, __LINE__); exit(2); } } while (0)

/* Shims stay compiled when an injected defect removes their only use. */
#define __maybe_unused __attribute__((__unused__))

static unsigned cases;
static bool concurrent;
static _Thread_local unsigned locks_held;
static _Thread_local bool irq_enabled = true;

static __maybe_unused int atomic_cmpxchg(atomic_t *v, int old, int new_value)
{
	atomic_compare_exchange_strong(&v->value, &old, new_value);
	return old;
}

static __maybe_unused int atomic_read(atomic_t *v)
{
	return atomic_load(&v->value);
}

static __maybe_unused void atomic_set(atomic_t *v, int value)
{
	atomic_store(&v->value, value);
}

typedef struct {
	pthread_mutex_t mutex;
	bool initialized;
	_Atomic unsigned acquisitions;
	_Atomic unsigned releases;
} spinlock_t;

static void spin_lock_init(spinlock_t *lock)
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
	CHECK(locks_held == 0, "leaf_lock");
	*flags = irq_enabled;
	irq_enabled = false;
	CHECK(!pthread_mutex_lock(&lock->mutex), "lock_acquire");
	locks_held++;
	atomic_fetch_add(&lock->acquisitions, 1);
}

static void fixture_unlock(spinlock_t *lock, unsigned long flags)
{
	CHECK(lock->initialized && locks_held == 1 && !irq_enabled, "irqsave_held");
	atomic_fetch_add(&lock->releases, 1);
	locks_held--;
	CHECK(!pthread_mutex_unlock(&lock->mutex), "lock_release");
	irq_enabled = flags;
}
#define spin_lock_irqsave(lock, flags) fixture_lock((lock), &(flags))
#define spin_unlock_irqrestore(lock, flags) fixture_unlock((lock), (flags))

/* @PREPARE_HEADER@ */

/* @HELPER_HEADER@ */

/* @HELPER_SOURCE@ */

/* FIXTURE_TESTS_BEGIN */
#define GUARDS PS4_UVD_LIVE_GUARDS_REQUIRED

static struct {
	unsigned claim_rejected, start_refused, invalidated_running, finished_first;
} races;

static void returned(void)
{
	CHECK(locks_held == 0 && irq_enabled, "lock_released_on_return");
}

static unsigned acquisitions(struct amdgpu_ps4_uvd_live *live)
{
	return atomic_load(&live->lock.acquisitions);
}

/* Every initialized call is exactly one critical section, never zero or two. */
static void transition_done(struct amdgpu_ps4_uvd_live *live, unsigned before)
{
	returned();
	if (concurrent)
		return;
	if (smp_load_acquire(&live->initialized))
		CHECK(acquisitions(live) == before + 1 &&
		      atomic_load(&live->lock.releases) == before + 1, "one_critical_section");
	else
		CHECK(acquisitions(live) == 0, "uninitialized_no_lock");
}

static void init(struct amdgpu_ps4_uvd_live *live, bool opted_in)
{
	bool again = smp_load_acquire(&live->initialized);
	unsigned before = acquisitions(live);

	amdgpu_ps4_uvd_live_init(live, opted_in);
	returned();
	/* First init takes no lock; a repeated init is one invalidation. */
	CHECK(acquisitions(live) == before + (again ? 1 : 0), "one_critical_section");
}

static enum ps4_uvd_live_verdict claim(struct amdgpu_ps4_uvd_live *live,
				       const struct ps4_uvd_live_observation *obs,
				       u64 *ticket)
{
	unsigned before = acquisitions(live);
	enum ps4_uvd_live_verdict verdict = amdgpu_ps4_uvd_live_claim(live, obs, ticket);

	transition_done(live, before);
	return verdict;
}

static enum ps4_uvd_live_verdict start(struct amdgpu_ps4_uvd_live *live, u64 ticket,
				       const struct ps4_uvd_live_observation *obs,
				       u32 guards)
{
	unsigned before = acquisitions(live);
	enum ps4_uvd_live_verdict verdict =
		amdgpu_ps4_uvd_live_start(live, ticket, obs, guards);

	transition_done(live, before);
	return verdict;
}

static enum ps4_uvd_live_verdict finish(struct amdgpu_ps4_uvd_live *live, u64 ticket,
					const struct ps4_uvd_live_result *result)
{
	unsigned before = acquisitions(live);
	enum ps4_uvd_live_verdict verdict = amdgpu_ps4_uvd_live_finish(live, ticket, result);

	transition_done(live, before);
	return verdict;
}

static void invalidate(struct amdgpu_ps4_uvd_live *live, enum ps4_live_prepare_event event,
		       struct ps4_uvd_live_snapshot *after)
{
	unsigned before = acquisitions(live);

	amdgpu_ps4_uvd_live_invalidate(live, event, after);
	transition_done(live, before);
}

static struct ps4_uvd_live_snapshot snap(struct amdgpu_ps4_uvd_live *live)
{
	struct ps4_uvd_live_snapshot s;
	unsigned before = acquisitions(live);

	/* Poison first so an unwritten snapshot field cannot pass as zero. */
	memset(&s, 0xa5, sizeof(s));
	amdgpu_ps4_uvd_live_snapshot(live, &s);
	transition_done(live, before);
	return s;
}

static struct amdgpu_ps4_uvd_live *zeroed(void)
{
	struct amdgpu_ps4_uvd_live *live = calloc(1, sizeof(*live));

	CHECK(live != NULL, "fixture_alloc");
	return live;
}

static struct amdgpu_ps4_uvd_live *device(bool opted_in)
{
	struct amdgpu_ps4_uvd_live *live = zeroed();

	init(live, opted_in);
	return live;
}

static void destroy(struct amdgpu_ps4_uvd_live *live)
{
	if (live->lock.initialized)
		CHECK(!pthread_mutex_destroy(&live->lock.mutex), "lock_destroy");
	free(live);
}

/* Simulated reboot of the built-in driver: only the boot latch is global. */
static void new_boot(void)
{
	atomic_set(&ps4_uvd_live_boot_claimed, 0);
}

/* The completed preparation an integrated caller would copy, plus reset 0. */
static struct ps4_uvd_live_observation prepared(void)
{
	struct ps4_uvd_live_observation obs = {
		.prepare = {
			.stage = PS4_LIVE_PREPARED,
			.last_event = PS4_LIVE_EVENT_GART_ENABLE,
			.observed_generation = 1,
			.proof_generation = 1,
			.proof_ns = 8400123456ULL,
			.pd_addr = 0x7f0000000ULL,
			.pd_bytes = 0x1000,
			.pt_bytes = 0x1000,
			.r1_bytes = 0x308000,
			.rb_bytes = 0x1000,
			.ib_bytes = 0x20000,
			.mapped_pages = 812,
			.copy_crc = 0x5eedc0deU,
			.proof_reset_counter = 0,
			.error = 0,
			.selected = true,
			.attempted = true,
			.reservation_seen = true,
			.allocator_alive = true,
			.proof_passed = true,
			.retained = true,
		},
		.gpu_reset_counter = 0,
	};

	return obs;
}

static bool same_prepare(const struct ps4_live_prepare_snapshot *a,
			 const struct ps4_live_prepare_snapshot *b)
{
	return a->stage == b->stage && a->last_event == b->last_event &&
	       a->observed_generation == b->observed_generation &&
	       a->proof_generation == b->proof_generation && a->proof_ns == b->proof_ns &&
	       a->pd_addr == b->pd_addr && a->pd_bytes == b->pd_bytes &&
	       a->pt_bytes == b->pt_bytes && a->r1_bytes == b->r1_bytes &&
	       a->rb_bytes == b->rb_bytes && a->ib_bytes == b->ib_bytes &&
	       a->mapped_pages == b->mapped_pages && a->copy_crc == b->copy_crc &&
	       a->proof_reset_counter == b->proof_reset_counter && a->error == b->error &&
	       a->selected == b->selected && a->attempted == b->attempted &&
	       a->reservation_seen == b->reservation_seen &&
	       a->allocator_alive == b->allocator_alive &&
	       a->proof_passed == b->proof_passed && a->retained == b->retained;
}

static bool same_observation(const struct ps4_uvd_live_observation *a,
			     const struct ps4_uvd_live_observation *b)
{
	return same_prepare(&a->prepare, &b->prepare) &&
	       a->gpu_reset_counter == b->gpu_reset_counter;
}

static bool same_result(const struct ps4_uvd_live_result *a, const struct ps4_uvd_live_result *b)
{
	unsigned i;

	if (a->error != b->error || a->code != b->code)
		return false;
	for (i = 0; i < PS4_UVD_LIVE_RESULT_WORDS; i++)
		if (a->words[i] != b->words[i])
			return false;
	return true;
}

/* Every field except rejection accounting. */
static bool same_record(const struct ps4_uvd_live_snapshot *a,
			const struct ps4_uvd_live_snapshot *b)
{
	return a->phase == b->phase && a->opted_in == b->opted_in && a->used == b->used &&
	       a->touch_entered == b->touch_entered && a->invalidated == b->invalidated &&
	       a->result_published == b->result_published &&
	       a->result_after_terminal == b->result_after_terminal &&
	       a->ticket == b->ticket && same_observation(&a->claim, &b->claim) &&
	       a->end == b->end && a->end_phase == b->end_phase &&
	       a->end_verdict == b->end_verdict && a->end_event == b->end_event &&
	       a->end_error == b->end_error && same_result(&a->result, &b->result) &&
	       a->first_event == b->first_event && a->first_event_phase == b->first_event_phase &&
	       a->last_event == b->last_event && a->invalidations == b->invalidations;
}

static bool same_snapshot(const struct ps4_uvd_live_snapshot *a,
			  const struct ps4_uvd_live_snapshot *b)
{
	return same_record(a, b) && a->claim_attempts == b->claim_attempts &&
	       a->rejected_claims == b->rejected_claims &&
	       a->rejected_transitions == b->rejected_transitions &&
	       a->last_rejection == b->last_rejection;
}

static bool no_claim_record(const struct ps4_uvd_live_snapshot *s)
{
	const struct ps4_uvd_live_observation zero = { 0 };

	return !s->used && s->ticket == 0 && same_observation(&s->claim, &zero);
}

static void invariants(const struct ps4_uvd_live_snapshot *s)
{
	bool claimed = s->phase == PS4_UVD_LIVE_CLAIMED || s->phase == PS4_UVD_LIVE_RUNNING ||
		       s->phase == PS4_UVD_LIVE_FINISHED;

	CHECK((unsigned)s->phase <= PS4_UVD_LIVE_FINISHED, "snapshot_invariant");
	CHECK(s->phase == PS4_UVD_LIVE_UNINITIALIZED ||
	      s->opted_in == (s->phase != PS4_UVD_LIVE_DISABLED), "snapshot_invariant");
	CHECK(s->used == claimed && s->used == (s->ticket != 0), "snapshot_invariant");
	CHECK(!s->touch_entered || s->phase == PS4_UVD_LIVE_RUNNING ||
	      s->phase == PS4_UVD_LIVE_FINISHED, "snapshot_invariant");
	CHECK(s->phase != PS4_UVD_LIVE_RUNNING || s->touch_entered, "snapshot_invariant");
	CHECK(s->end == PS4_UVD_LIVE_END_NONE || s->used, "snapshot_invariant");
	CHECK(s->result_published == (s->phase == PS4_UVD_LIVE_FINISHED), "snapshot_invariant");
	CHECK(s->phase != PS4_UVD_LIVE_FINISHED || s->end != PS4_UVD_LIVE_END_NONE,
	      "snapshot_invariant");
	CHECK(!s->result_after_terminal || s->result_published, "snapshot_invariant");
	CHECK(s->invalidated == (s->invalidations != 0), "snapshot_invariant");
	switch (s->end) {
	case PS4_UVD_LIVE_END_NONE:
		CHECK(s->end_phase == PS4_UVD_LIVE_UNINITIALIZED && !s->end_error &&
		      s->end_verdict == PS4_UVD_LIVE_OK && s->end_event == PS4_LIVE_EVENT_NONE,
		      "snapshot_invariant");
		break;
	case PS4_UVD_LIVE_END_COMPLETED:
	case PS4_UVD_LIVE_END_FAILED:
		CHECK(s->touch_entered && s->end_phase == PS4_UVD_LIVE_RUNNING &&
		      (s->end == PS4_UVD_LIVE_END_FAILED) == (s->end_error != 0) &&
		      !s->result_after_terminal, "snapshot_invariant");
		break;
	case PS4_UVD_LIVE_END_CANCELLED:
		CHECK(!s->touch_entered && s->end_phase == PS4_UVD_LIVE_CLAIMED && s->end_error &&
		      !s->result_after_terminal, "snapshot_invariant");
		break;
	case PS4_UVD_LIVE_END_REFUSED:
		CHECK(!s->touch_entered && s->end_phase == PS4_UVD_LIVE_CLAIMED &&
		      s->end_verdict != PS4_UVD_LIVE_OK &&
		      s->end_error == amdgpu_ps4_uvd_live_errno(s->end_verdict),
		      "snapshot_invariant");
		break;
	case PS4_UVD_LIVE_END_INVALIDATED:
		CHECK(s->invalidated && s->end_error == -ESTALE &&
		      ((s->end_phase == PS4_UVD_LIVE_CLAIMED && !s->touch_entered) ||
		       (s->end_phase == PS4_UVD_LIVE_RUNNING && s->touch_entered)),
		      "snapshot_invariant");
		break;
	default:
		CHECK(false, "snapshot_invariant");
	}
}

static unsigned rank(enum ps4_uvd_live_phase phase)
{
	return phase == PS4_UVD_LIVE_DISABLED ? PS4_UVD_LIVE_IDLE : phase;
}

/* Successive snapshots of one device: nothing sticky or first-recorded reverts. */
static void monotonic(const struct ps4_uvd_live_snapshot *a, const struct ps4_uvd_live_snapshot *b)
{
	CHECK(rank(b->phase) >= rank(a->phase), "monotonic");
	CHECK((!a->used || b->used) && (!a->touch_entered || b->touch_entered) &&
	      (!a->invalidated || b->invalidated) &&
	      (!a->result_published || b->result_published), "monotonic");
	CHECK(b->invalidations >= a->invalidations && b->claim_attempts >= a->claim_attempts &&
	      b->rejected_claims >= a->rejected_claims &&
	      b->rejected_transitions >= a->rejected_transitions, "monotonic");
	if (a->used)
		CHECK(b->ticket == a->ticket && same_observation(&b->claim, &a->claim), "monotonic");
	if (a->end != PS4_UVD_LIVE_END_NONE)
		CHECK(b->end == a->end && b->end_phase == a->end_phase &&
		      b->end_verdict == a->end_verdict && b->end_event == a->end_event &&
		      b->end_error == a->end_error, "monotonic");
	if (a->result_published)
		CHECK(same_result(&b->result, &a->result) &&
		      b->result_after_terminal == a->result_after_terminal, "monotonic");
	if (a->invalidated)
		CHECK(b->first_event == a->first_event &&
		      b->first_event_phase == a->first_event_phase, "monotonic");
}

static void run_thread(pthread_t *thread, void *(*body)(void *), void *arg)
{
	CHECK(!pthread_create(thread, NULL, body, arg), "fixture_thread");
}

static void join_thread(pthread_t thread)
{
	CHECK(!pthread_join(thread, NULL), "fixture_thread");
}

struct gate {
	_Atomic unsigned arrived;
	_Atomic bool open;
};

static void gate_wait(struct gate *gate)
{
	atomic_fetch_add(&gate->arrived, 1);
	while (!atomic_load(&gate->open))
		sched_yield();
}

static void gate_open(struct gate *gate, unsigned threads)
{
	while (atomic_load(&gate->arrived) != threads)
		sched_yield();
	atomic_store(&gate->open, true);
}

static void spin(unsigned rounds)
{
	volatile unsigned i;

	for (i = 0; i < rounds; i++)
		;
}

static void test_errno(void)
{
	static const struct {
		enum ps4_uvd_live_verdict verdict;
		int error;
	} map[] = {
		{ PS4_UVD_LIVE_OK, 0 },
		{ PS4_UVD_LIVE_REJECT_UNINITIALIZED, -ENODEV },
		{ PS4_UVD_LIVE_REJECT_DISABLED, -EPERM },
		{ PS4_UVD_LIVE_REJECT_USED, -EALREADY },
		{ PS4_UVD_LIVE_REJECT_INVALIDATED, -ESTALE },
		{ PS4_UVD_LIVE_REJECT_NOT_PREPARED, -EINVAL },
		{ PS4_UVD_LIVE_REJECT_OWNERSHIP, -EINVAL },
		{ PS4_UVD_LIVE_REJECT_GENERATION, -ESTALE },
		{ PS4_UVD_LIVE_REJECT_RESET, -ESTALE },
		{ PS4_UVD_LIVE_REJECT_BOOT_USED, -EALREADY },
		{ PS4_UVD_LIVE_REJECT_PHASE, -EINVAL },
		{ PS4_UVD_LIVE_REJECT_TICKET, -EINVAL },
		{ PS4_UVD_LIVE_REJECT_TERMINAL, -ESTALE },
		{ PS4_UVD_LIVE_REJECT_GUARD, -EINVAL },
		{ PS4_UVD_LIVE_REJECT_IDENTITY, -ESTALE },
	};
	unsigned i;

	CHECK(sizeof(map) / sizeof(map[0]) == PS4_UVD_LIVE_REJECT_IDENTITY + 1, "errno_map");
	for (i = 0; i < sizeof(map) / sizeof(map[0]); i++)
		CHECK(map[i].verdict == (enum ps4_uvd_live_verdict)i &&
		      amdgpu_ps4_uvd_live_errno(map[i].verdict) == map[i].error, "errno_map");
	CHECK(amdgpu_ps4_uvd_live_errno((enum ps4_uvd_live_verdict)99) == -EINVAL, "errno_map");
	cases++;
}

static void test_uninitialized(void)
{
	struct amdgpu_ps4_uvd_live *live = zeroed();
	struct ps4_uvd_live_observation obs = prepared();
	struct ps4_uvd_live_result result = { .error = -EIO };
	const struct ps4_uvd_live_snapshot zero = { 0 };
	struct ps4_uvd_live_snapshot s;
	u64 ticket = 77;

	new_boot();
	CHECK(claim(live, &obs, &ticket) == PS4_UVD_LIVE_REJECT_UNINITIALIZED && ticket == 0,
	      "uninitialized_reject");
	CHECK(start(live, 1, &obs, GUARDS) == PS4_UVD_LIVE_REJECT_UNINITIALIZED,
	      "uninitialized_reject");
	CHECK(finish(live, 1, &result) == PS4_UVD_LIVE_REJECT_UNINITIALIZED,
	      "uninitialized_reject");
	memset(&s, 0x5a, sizeof(s));
	invalidate(live, PS4_LIVE_EVENT_RESET, &s);
	CHECK(same_snapshot(&s, &zero) && s.phase == PS4_UVD_LIVE_UNINITIALIZED,
	      "uninitialized_snapshot");
	invalidate(live, PS4_LIVE_EVENT_RESET, NULL);
	s = snap(live);
	CHECK(same_snapshot(&s, &zero), "uninitialized_snapshot");
	CHECK(atomic_read(&ps4_uvd_live_boot_claimed) == 0, "latch_not_consumed");
	cases++;
	destroy(live);
}

static void test_init(void)
{
	struct amdgpu_ps4_uvd_live *off, *on;
	struct ps4_uvd_live_observation obs = prepared();
	struct ps4_uvd_live_result result = { 0 };
	struct ps4_uvd_live_snapshot s;
	u64 ticket;

	new_boot();
	off = device(false);
	s = snap(off);
	CHECK(s.phase == PS4_UVD_LIVE_DISABLED && !s.opted_in && no_claim_record(&s) &&
	      !s.invalidated && !s.claim_attempts, "init_disabled");
	invariants(&s);
	CHECK(claim(off, &obs, &ticket) == PS4_UVD_LIVE_REJECT_DISABLED && ticket == 0,
	      "opt_in_required");
	CHECK(start(off, 1, &obs, GUARDS) == PS4_UVD_LIVE_REJECT_PHASE &&
	      finish(off, 1, &result) == PS4_UVD_LIVE_REJECT_PHASE, "disabled_transitions");
	s = snap(off);
	CHECK(s.claim_attempts == 1 && s.rejected_claims == 1 && s.rejected_transitions == 2 &&
	      s.last_rejection == PS4_UVD_LIVE_REJECT_PHASE && no_claim_record(&s),
	      "rejection_accounting");
	CHECK(atomic_read(&ps4_uvd_live_boot_claimed) == 0, "latch_not_consumed");
	/* Re-initialization never grants an opt-in, a claim or a reset. */
	init(off, true);
	s = snap(off);
	CHECK(!s.opted_in && s.phase == PS4_UVD_LIVE_DISABLED && s.invalidated &&
	      s.first_event == PS4_LIVE_EVENT_REINIT &&
	      s.first_event_phase == PS4_UVD_LIVE_DISABLED && s.invalidations == 1,
	      "reinit_invalidates");
	invariants(&s);
	CHECK(claim(off, &obs, &ticket) == PS4_UVD_LIVE_REJECT_DISABLED, "opt_in_required");
	cases++;

	on = device(true);
	s = snap(on);
	CHECK(s.phase == PS4_UVD_LIVE_IDLE && s.opted_in && no_claim_record(&s) &&
	      !s.touch_entered && !s.invalidated && s.end == PS4_UVD_LIVE_END_NONE, "init_idle");
	invariants(&s);
	init(on, false);
	s = snap(on);
	CHECK(s.opted_in && s.phase == PS4_UVD_LIVE_IDLE && s.invalidated &&
	      s.first_event == PS4_LIVE_EVENT_REINIT && s.first_event_phase == PS4_UVD_LIVE_IDLE,
	      "reinit_invalidates");
	CHECK(claim(on, &obs, &ticket) == PS4_UVD_LIVE_REJECT_INVALIDATED && ticket == 0,
	      "invalidated_blocks_claim");
	CHECK(atomic_read(&ps4_uvd_live_boot_claimed) == 0, "latch_not_consumed");
	cases++;
	destroy(off);
	destroy(on);
}

static void rejected_claim(struct ps4_uvd_live_observation obs, enum ps4_uvd_live_verdict expected,
			   const char *tag)
{
	struct amdgpu_ps4_uvd_live *live;
	struct ps4_uvd_live_snapshot s;
	u64 ticket = 99;

	new_boot();
	live = device(true);
	CHECK(claim(live, &obs, &ticket) == expected && ticket == 0, tag);
	s = snap(live);
	CHECK(no_claim_record(&s) && s.phase == PS4_UVD_LIVE_IDLE && s.claim_attempts == 1 &&
	      s.rejected_claims == 1 && s.last_rejection == expected, "rejection_accounting");
	CHECK(atomic_read(&ps4_uvd_live_boot_claimed) == 0, "latch_not_consumed");
	invariants(&s);
	/* A pre-claim rejection consumes nothing: a fresh observation can claim. */
	obs = prepared();
	CHECK(claim(live, &obs, &ticket) == PS4_UVD_LIVE_OK && ticket == 2,
	      "rejection_keeps_eligibility");
	cases++;
	destroy(live);
}

static void test_admission(void)
{
	static const enum ps4_live_prepare_stage stages[] = {
		PS4_LIVE_DISABLED, PS4_LIVE_SELECTED, PS4_LIVE_RESERVED, PS4_LIVE_PREPARING,
		PS4_LIVE_INVALIDATED, PS4_LIVE_RELEASING, PS4_LIVE_RELEASED, PS4_LIVE_FAILED,
	};
	struct ps4_uvd_live_observation obs;
	struct amdgpu_ps4_uvd_live *off;
	unsigned i;
	u64 ticket;

	obs = prepared();
	obs.prepare.selected = false;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_NOT_PREPARED, "admission_prepared");
	obs = prepared();
	obs.prepare.attempted = false;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_NOT_PREPARED, "admission_prepared");
	for (i = 0; i < sizeof(stages) / sizeof(stages[0]); i++) {
		obs = prepared();
		obs.prepare.stage = stages[i];
		rejected_claim(obs, PS4_UVD_LIVE_REJECT_NOT_PREPARED, "admission_prepared");
	}
	obs = prepared();
	obs.prepare.proof_passed = false;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_NOT_PREPARED, "admission_prepared");
	obs = prepared();
	obs.prepare.retained = false;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_NOT_PREPARED, "admission_prepared");
	obs = prepared();
	obs.prepare.error = -ESTALE;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_NOT_PREPARED, "admission_prepared");
	obs = prepared();
	obs.prepare.reservation_seen = false;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_OWNERSHIP, "admission_ownership");
	obs = prepared();
	obs.prepare.allocator_alive = false;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_OWNERSHIP, "admission_ownership");
	obs = prepared();
	obs.prepare.observed_generation = 2;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_GENERATION, "stale_generation_refused");
	/* The producer cannot publish generation 0: GART_ENABLE always precedes. */
	obs = prepared();
	obs.prepare.observed_generation = 0;
	obs.prepare.proof_generation = 0;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_GENERATION, "generation_nonzero");
	/* Equal exhausted counts never stand for freshness. */
	obs = prepared();
	obs.prepare.observed_generation = U64_MAX;
	obs.prepare.proof_generation = U64_MAX;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_GENERATION, "generation_not_saturated");
	obs = prepared();
	obs.gpu_reset_counter = 1;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_RESET, "admission_reset");
	/* Publication records reset counter 0; nonzero is not a real proof. */
	obs = prepared();
	obs.prepare.proof_reset_counter = 1;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_RESET, "reset_pinned_zero");
	obs.gpu_reset_counter = 1;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_RESET, "reset_pinned_zero");

	/* Ordered checks: the first failing class is the reported reason. */
	obs = prepared();
	obs.prepare.retained = false;
	obs.prepare.allocator_alive = false;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_NOT_PREPARED, "admission_order");
	obs = prepared();
	obs.prepare.allocator_alive = false;
	obs.prepare.observed_generation = 2;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_OWNERSHIP, "admission_order");
	obs = prepared();
	obs.prepare.observed_generation = 2;
	obs.gpu_reset_counter = 1;
	rejected_claim(obs, PS4_UVD_LIVE_REJECT_GENERATION, "stale_generation_refused");
	new_boot();
	off = device(false);
	obs.prepare.stage = PS4_LIVE_FAILED;
	CHECK(claim(off, &obs, &ticket) == PS4_UVD_LIVE_REJECT_DISABLED, "admission_order");
	cases++;
	destroy(off);
}

static void test_once(void)
{
	struct amdgpu_ps4_uvd_live *a, *b, *rebound;
	struct ps4_uvd_live_observation obs = prepared(), stale = prepared();
	struct ps4_uvd_live_snapshot before, s;
	u64 ticket, again;

	new_boot();
	a = device(true);
	CHECK(claim(a, &obs, &ticket) == PS4_UVD_LIVE_OK && ticket == 1, "claim_ok");
	CHECK(atomic_read(&ps4_uvd_live_boot_claimed) == 1, "boot_once");
	before = snap(a);
	CHECK(before.used && before.phase == PS4_UVD_LIVE_CLAIMED && before.ticket == ticket &&
	      same_observation(&before.claim, &obs) && !before.touch_entered &&
	      before.end == PS4_UVD_LIVE_END_NONE, "claim_record");
	invariants(&before);
	/* Per device: a second claim is refused and changes no claim record. */
	CHECK(claim(a, &obs, &again) == PS4_UVD_LIVE_REJECT_USED && again == 0, "claim_used_once");
	s = snap(a);
	CHECK(same_record(&s, &before) && s.rejected_claims == 1 && s.claim_attempts == 2,
	      "reentry_no_overwrite");
	/* Used outranks invalidation and any observation. */
	invalidate(a, PS4_LIVE_EVENT_SUSPEND, NULL);
	stale.prepare.stage = PS4_LIVE_INVALIDATED;
	CHECK(claim(a, &stale, &again) == PS4_UVD_LIVE_REJECT_USED, "claim_used_once");
	cases++;

	/* Per boot: another device, or a rebound adev, is refused. */
	b = device(true);
	CHECK(claim(b, &obs, &again) == PS4_UVD_LIVE_REJECT_BOOT_USED && again == 0, "boot_once");
	s = snap(b);
	CHECK(no_claim_record(&s) && s.phase == PS4_UVD_LIVE_IDLE && s.rejected_claims == 1 &&
	      s.last_rejection == PS4_UVD_LIVE_REJECT_BOOT_USED, "boot_once");
	rebound = device(true);
	CHECK(claim(rebound, &obs, &again) == PS4_UVD_LIVE_REJECT_BOOT_USED, "boot_once");
	CHECK(atomic_read(&ps4_uvd_live_boot_claimed) == 1, "boot_once");
	cases++;

	/* Only a new boot clears the latch; per-device state is new storage too. */
	new_boot();
	CHECK(claim(b, &obs, &again) == PS4_UVD_LIVE_OK && again == 2, "next_boot");
	cases++;
	destroy(a);
	destroy(b);
	destroy(rebound);
}

struct probe {
	struct amdgpu_ps4_uvd_live *live;
	struct ps4_uvd_live_snapshot seen;
};

static void *observe(void *arg)
{
	struct probe *probe = arg;

	amdgpu_ps4_uvd_live_snapshot(probe->live, &probe->seen);
	returned();
	return NULL;
}

/* Stand-in for the owner's first hardware operation. Nothing is touched. */
static void first_operation(struct amdgpu_ps4_uvd_live *live)
{
	struct probe probe = { .live = live };
	struct ps4_uvd_live_snapshot s = snap(live);
	pthread_t thread;

	CHECK(s.touch_entered && s.phase == PS4_UVD_LIVE_RUNNING, "touch_before_entry");
	run_thread(&thread, observe, &probe);
	join_thread(thread);
	CHECK(probe.seen.touch_entered && probe.seen.phase == PS4_UVD_LIVE_RUNNING,
	      "touch_before_entry");
}

static void test_completion(void)
{
	struct amdgpu_ps4_uvd_live *live;
	struct ps4_uvd_live_observation obs = prepared();
	struct ps4_uvd_live_result result = {
		.error = 0, .code = 0x52454459, .words = { 1, 2, 3, 0xfffffffeU },
	};
	struct ps4_uvd_live_result other = { .error = -EIO, .code = 7 };
	struct ps4_uvd_live_snapshot s, done, after;
	u64 ticket, again;

	new_boot();
	live = device(true);
	CHECK(claim(live, &obs, &ticket) == PS4_UVD_LIVE_OK, "claim_ok");
	CHECK(start(live, ticket, &obs, GUARDS) == PS4_UVD_LIVE_OK, "start_ok");
	first_operation(live);
	s = snap(live);
	invariants(&s);
	CHECK(finish(live, ticket, &result) == PS4_UVD_LIVE_OK, "finish_ok");
	done = snap(live);
	/* Completion keeps every sticky witness. */
	CHECK(done.used, "used_sticky");
	CHECK(done.touch_entered, "touch_sticky");
	CHECK(done.phase == PS4_UVD_LIVE_FINISHED && done.end == PS4_UVD_LIVE_END_COMPLETED &&
	      done.end_phase == PS4_UVD_LIVE_RUNNING && !done.end_error &&
	      done.result_published && !done.result_after_terminal &&
	      same_result(&done.result, &result), "completion_published");
	invariants(&done);
	cases++;

	/* Rejected reentry cannot overwrite the first operation or its result. */
	CHECK(claim(live, &obs, &again) == PS4_UVD_LIVE_REJECT_USED && again == 0,
	      "claim_used_once");
	CHECK(start(live, ticket, &obs, GUARDS) == PS4_UVD_LIVE_REJECT_PHASE, "reentry_rejected");
	CHECK(finish(live, ticket, &other) == PS4_UVD_LIVE_REJECT_PHASE, "reentry_rejected");
	s = snap(live);
	CHECK(same_record(&s, &done) && s.rejected_claims == 1 && s.rejected_transitions == 2 &&
	      s.last_rejection == PS4_UVD_LIVE_REJECT_PHASE, "reentry_no_overwrite");
	cases++;

	/* A later invalidation is recorded without rewriting the terminal. */
	invalidate(live, PS4_LIVE_EVENT_HW_FINI, &after);
	CHECK(after.touch_entered, "touch_sticky");
	CHECK(after.used, "used_sticky");
	CHECK(after.end == PS4_UVD_LIVE_END_COMPLETED && after.end_phase == PS4_UVD_LIVE_RUNNING &&
	      after.invalidated && after.first_event == PS4_LIVE_EVENT_HW_FINI &&
	      after.first_event_phase == PS4_UVD_LIVE_FINISHED && after.invalidations == 1 &&
	      same_result(&after.result, &result), "invalidation_after_finish");
	invariants(&after);
	s = snap(live);
	CHECK(same_snapshot(&s, &after), "invalidate_returns_same_cut");
	cases++;
	destroy(live);
}

static void test_failure_and_cancel(void)
{
	struct amdgpu_ps4_uvd_live *live;
	struct ps4_uvd_live_observation obs = prepared();
	struct ps4_uvd_live_result failed = { .error = -EIO, .code = 5, .words = { 9 } };
	struct ps4_uvd_live_result gave_up = { .error = -ENODEV };
	struct ps4_uvd_live_result empty = { 0 };
	struct ps4_uvd_live_snapshot s;
	u64 ticket, again;

	new_boot();
	live = device(true);
	CHECK(claim(live, &obs, &ticket) == PS4_UVD_LIVE_OK &&
	      start(live, ticket, &obs, GUARDS) == PS4_UVD_LIVE_OK &&
	      finish(live, ticket, &failed) == PS4_UVD_LIVE_OK, "failure_flow");
	s = snap(live);
	CHECK(s.used && s.touch_entered && s.end == PS4_UVD_LIVE_END_FAILED &&
	      s.end_error == -EIO && s.end_phase == PS4_UVD_LIVE_RUNNING &&
	      same_result(&s.result, &failed), "failure_published");
	invariants(&s);
	CHECK(claim(live, &obs, &again) == PS4_UVD_LIVE_REJECT_USED, "claim_used_once");
	cases++;
	destroy(live);

	/* The owner gave up before entry: no touch, and no eligibility restored. */
	new_boot();
	live = device(true);
	CHECK(claim(live, &obs, &ticket) == PS4_UVD_LIVE_OK &&
	      finish(live, ticket, &gave_up) == PS4_UVD_LIVE_OK, "cancel_flow");
	s = snap(live);
	CHECK(s.used && !s.touch_entered && s.phase == PS4_UVD_LIVE_FINISHED &&
	      s.end == PS4_UVD_LIVE_END_CANCELLED && s.end_phase == PS4_UVD_LIVE_CLAIMED &&
	      s.end_error == -ENODEV, "cancel_before_start");
	invariants(&s);
	CHECK(start(live, ticket, &obs, GUARDS) == PS4_UVD_LIVE_REJECT_PHASE &&
	      !snap(live).touch_entered, "cancel_final");
	CHECK(claim(live, &obs, &again) == PS4_UVD_LIVE_REJECT_USED, "claim_used_once");
	CHECK(atomic_read(&ps4_uvd_live_boot_claimed) == 1, "boot_once");
	cases++;
	destroy(live);

	/* A zero result without entry is still a cancellation, never completion. */
	new_boot();
	live = device(true);
	CHECK(claim(live, &obs, &ticket) == PS4_UVD_LIVE_OK &&
	      finish(live, ticket, &empty) == PS4_UVD_LIVE_OK, "cancel_flow");
	s = snap(live);
	CHECK(s.end == PS4_UVD_LIVE_END_CANCELLED && s.end_error == -ECANCELED &&
	      s.result.error == 0 && !s.touch_entered, "cancel_before_start");
	invariants(&s);
	cases++;
	destroy(live);
}

static void refused_start(struct ps4_uvd_live_observation at_start, u32 guards,
			  enum ps4_uvd_live_verdict expected, const char *tag)
{
	struct amdgpu_ps4_uvd_live *live;
	struct ps4_uvd_live_observation obs = prepared();
	struct ps4_uvd_live_result late = { .error = 0, .code = 9 };
	struct ps4_uvd_live_snapshot s;
	u64 ticket;

	new_boot();
	live = device(true);
	CHECK(claim(live, &obs, &ticket) == PS4_UVD_LIVE_OK, "claim_ok");
	CHECK(start(live, ticket, &at_start, guards) == expected, tag);
	s = snap(live);
	CHECK(s.end == PS4_UVD_LIVE_END_REFUSED && s.end_verdict == expected &&
	      s.end_phase == PS4_UVD_LIVE_CLAIMED &&
	      s.end_error == amdgpu_ps4_uvd_live_errno(expected) && !s.touch_entered &&
	      s.phase == PS4_UVD_LIVE_CLAIMED && s.used, "refused_final");
	invariants(&s);
	/* Even a fully fresh retry by the owner never runs after a refusal. */
	CHECK(start(live, ticket, &obs, GUARDS) == PS4_UVD_LIVE_REJECT_TERMINAL &&
	      !snap(live).touch_entered, "terminal_claim_never_runs");
	CHECK(finish(live, ticket, &late) == PS4_UVD_LIVE_OK, "finish_ok");
	s = snap(live);
	CHECK(s.end == PS4_UVD_LIVE_END_REFUSED && s.end_verdict == expected,
	      "first_terminal_stable");
	CHECK(s.phase == PS4_UVD_LIVE_FINISHED && s.result_published && s.result_after_terminal &&
	      same_result(&s.result, &late) && !s.touch_entered, "late_result_explicit");
	invariants(&s);
	cases++;
	destroy(live);
}

static void test_start(void)
{
	static const u32 partial_guards[] = {
		0,
		PS4_UVD_LIVE_GUARD_DEVICE | PS4_UVD_LIVE_GUARD_RESET,
		PS4_UVD_LIVE_GUARD_DEVICE | PS4_UVD_LIVE_GUARD_FIRMWARE,
		PS4_UVD_LIVE_GUARD_RESET | PS4_UVD_LIVE_GUARD_FIRMWARE,
		PS4_UVD_LIVE_GUARDS_REQUIRED | BIT(3),
	};
	struct amdgpu_ps4_uvd_live *live;
	struct ps4_uvd_live_observation obs = prepared(), changed;
	struct ps4_uvd_live_result result = { .code = 1 };
	struct ps4_uvd_live_snapshot before, s;
	unsigned i;
	u64 ticket;

	/* Only the claim's ticket can start or finish its operation. */
	new_boot();
	live = device(true);
	CHECK(claim(live, &obs, &ticket) == PS4_UVD_LIVE_OK, "claim_ok");
	before = snap(live);
	CHECK(start(live, ticket + 1, &obs, GUARDS) == PS4_UVD_LIVE_REJECT_TICKET &&
	      start(live, 0, &obs, GUARDS) == PS4_UVD_LIVE_REJECT_TICKET, "ticket_owner");
	CHECK(finish(live, ticket + 1, &result) == PS4_UVD_LIVE_REJECT_TICKET, "ticket_owner");
	s = snap(live);
	CHECK(same_record(&s, &before) && s.rejected_transitions == 3 &&
	      s.last_rejection == PS4_UVD_LIVE_REJECT_TICKET, "reentry_no_overwrite");
	CHECK(start(live, ticket, &obs, GUARDS) == PS4_UVD_LIVE_OK &&
	      finish(live, ticket, &result) == PS4_UVD_LIVE_OK, "owner_unaffected");
	cases++;
	destroy(live);

	for (i = 0; i < sizeof(partial_guards) / sizeof(partial_guards[0]); i++)
		refused_start(obs, partial_guards[i], PS4_UVD_LIVE_REJECT_GUARD, "start_guard");

	/* The real stale preparation shape after a later invalidation event. */
	changed = prepared();
	changed.prepare.stage = PS4_LIVE_INVALIDATED;
	changed.prepare.error = -ESTALE;
	changed.prepare.observed_generation = 2;
	changed.prepare.last_event = PS4_LIVE_EVENT_RESET;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_NOT_PREPARED, "start_fresh_check");
	changed = prepared();
	changed.prepare.allocator_alive = false;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_OWNERSHIP, "start_fresh_check");
	changed = prepared();
	changed.prepare.observed_generation = 2;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_GENERATION, "stale_generation_refused");
	changed = prepared();
	changed.prepare.observed_generation = 0;
	changed.prepare.proof_generation = 0;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_GENERATION, "generation_nonzero");
	changed = prepared();
	changed.prepare.observed_generation = U64_MAX;
	changed.prepare.proof_generation = U64_MAX;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_GENERATION, "generation_not_saturated");
	changed = prepared();
	changed.gpu_reset_counter = 1;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_RESET, "start_fresh_check");
	changed = prepared();
	changed.prepare.proof_reset_counter = 1;
	changed.gpu_reset_counter = 1;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_RESET, "reset_pinned_zero");

	/* Internally consistent, but not the preparation descriptor that was claimed. */
	changed = prepared();
	changed.prepare.observed_generation = 2;
	changed.prepare.proof_generation = 2;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_IDENTITY, "start_identity");
	changed = prepared();
	changed.prepare.proof_ns++;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_IDENTITY, "start_identity");
	changed = prepared();
	changed.prepare.pd_addr += 0x1000;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_IDENTITY, "start_identity");
	changed = prepared();
	changed.prepare.pd_bytes += 0x1000;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_IDENTITY, "start_identity");
	changed = prepared();
	changed.prepare.pt_bytes += 0x1000;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_IDENTITY, "start_identity");
	changed = prepared();
	changed.prepare.r1_bytes -= 0x1000;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_IDENTITY, "start_identity");
	changed = prepared();
	changed.prepare.rb_bytes *= 2;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_IDENTITY, "start_identity");
	changed = prepared();
	changed.prepare.ib_bytes /= 2;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_IDENTITY, "start_identity");
	changed = prepared();
	changed.prepare.mapped_pages--;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_IDENTITY, "start_identity");
	changed = prepared();
	changed.prepare.copy_crc ^= 1;
	refused_start(changed, GUARDS, PS4_UVD_LIVE_REJECT_IDENTITY, "start_identity");

	/* The largest accepted generation is U64_MAX - 1, at claim and at entry. */
	new_boot();
	live = device(true);
	changed = prepared();
	changed.prepare.observed_generation = U64_MAX - 1;
	changed.prepare.proof_generation = U64_MAX - 1;
	CHECK(claim(live, &changed, &ticket) == PS4_UVD_LIVE_OK &&
	      start(live, ticket, &changed, GUARDS) == PS4_UVD_LIVE_OK &&
	      finish(live, ticket, &result) == PS4_UVD_LIVE_OK, "generation_boundary");
	cases++;
	destroy(live);
}

static void test_invalidation_orders(void)
{
	struct amdgpu_ps4_uvd_live *live;
	struct ps4_uvd_live_observation obs = prepared();
	struct ps4_uvd_live_result late = { .error = 0, .code = 3 };
	struct ps4_uvd_live_snapshot after, s;
	u64 ticket;

	/* Before claim: a still-PREPARED stale copy cannot claim afterwards. */
	new_boot();
	live = device(true);
	invalidate(live, PS4_LIVE_EVENT_GART_DISABLE, &after);
	CHECK(after.phase == PS4_UVD_LIVE_IDLE && after.first_event_phase == PS4_UVD_LIVE_IDLE &&
	      after.end == PS4_UVD_LIVE_END_NONE && no_claim_record(&after), "invalidation_before_claim");
	invariants(&after);
	CHECK(claim(live, &obs, &ticket) == PS4_UVD_LIVE_REJECT_INVALIDATED,
	      "invalidated_blocks_claim");
	CHECK(atomic_read(&ps4_uvd_live_boot_claimed) == 0, "latch_not_consumed");
	cases++;
	destroy(live);

	/* Claimed, then invalidated: the stale claim never runs. */
	new_boot();
	live = device(true);
	CHECK(claim(live, &obs, &ticket) == PS4_UVD_LIVE_OK, "claim_ok");
	invalidate(live, PS4_LIVE_EVENT_GART_DISABLE, &after);
	CHECK(after.end == PS4_UVD_LIVE_END_INVALIDATED && after.end_phase == PS4_UVD_LIVE_CLAIMED &&
	      after.end_event == PS4_LIVE_EVENT_GART_DISABLE && after.end_error == -ESTALE &&
	      !after.touch_entered && after.phase == PS4_UVD_LIVE_CLAIMED,
	      "invalidation_before_entry");
	invariants(&after);
	CHECK(start(live, ticket, &obs, GUARDS) == PS4_UVD_LIVE_REJECT_TERMINAL &&
	      !snap(live).touch_entered, "terminal_claim_never_runs");
	CHECK(finish(live, ticket, &late) == PS4_UVD_LIVE_OK, "finish_ok");
	s = snap(live);
	CHECK(s.end == PS4_UVD_LIVE_END_INVALIDATED && s.end_phase == PS4_UVD_LIVE_CLAIMED,
	      "first_terminal_stable");
	CHECK(s.phase == PS4_UVD_LIVE_FINISHED && s.result_after_terminal &&
	      same_result(&s.result, &late) && !s.touch_entered, "late_result_explicit");
	invariants(&s);
	cases++;
	destroy(live);

	/* Running, then invalidated: touch stays, and completion is explicit. */
	new_boot();
	live = device(true);
	CHECK(claim(live, &obs, &ticket) == PS4_UVD_LIVE_OK &&
	      start(live, ticket, &obs, GUARDS) == PS4_UVD_LIVE_OK, "start_ok");
	invalidate(live, PS4_LIVE_EVENT_RESET, &after);
	CHECK(after.touch_entered, "touch_sticky");
	CHECK(after.end == PS4_UVD_LIVE_END_INVALIDATED && after.end_phase == PS4_UVD_LIVE_RUNNING &&
	      after.end_event == PS4_LIVE_EVENT_RESET && after.phase == PS4_UVD_LIVE_RUNNING,
	      "invalidation_after_entry");
	invariants(&after);
	CHECK(finish(live, ticket, &late) == PS4_UVD_LIVE_OK, "finish_ok");
	s = snap(live);
	CHECK(s.end == PS4_UVD_LIVE_END_INVALIDATED && s.end_phase == PS4_UVD_LIVE_RUNNING,
	      "first_terminal_stable");
	CHECK(s.touch_entered, "touch_sticky");
	CHECK(s.phase == PS4_UVD_LIVE_FINISHED && s.result_after_terminal && s.result.error == 0 &&
	      same_result(&s.result, &late), "late_result_explicit");
	invalidate(live, PS4_LIVE_EVENT_SUSPEND, &after);
	CHECK(after.first_event == PS4_LIVE_EVENT_RESET &&
	      after.first_event_phase == PS4_UVD_LIVE_RUNNING &&
	      after.last_event == PS4_LIVE_EVENT_SUSPEND && after.invalidations == 2 &&
	      after.end_event == PS4_LIVE_EVENT_RESET, "first_invalidation_stable");
	invariants(&after);
	cases++;
	destroy(live);
}

static void test_saturation(void)
{
	struct amdgpu_ps4_uvd_live *live, *off;
	struct ps4_uvd_live_observation obs = prepared();
	struct ps4_uvd_live_result result = { 0 };
	struct ps4_uvd_live_snapshot s;
	u64 ticket, again;

	/* Seeded single-threaded state; counters stick at their limit. */
	new_boot();
	live = device(true);
	live->state.claim_attempts = U64_MAX - 1;
	CHECK(claim(live, &obs, &ticket) == PS4_UVD_LIVE_OK && ticket == U64_MAX, "no_wrap");
	CHECK(claim(live, &obs, &again) == PS4_UVD_LIVE_REJECT_USED && again == 0, "no_wrap");
	live->state.invalidations = U32_MAX - 1;
	live->state.rejected_transitions = U32_MAX;
	invalidate(live, PS4_LIVE_EVENT_RESET, NULL);
	invalidate(live, PS4_LIVE_EVENT_SUSPEND, NULL);
	CHECK(finish(live, ticket, &result) == PS4_UVD_LIVE_OK &&
	      finish(live, ticket, &result) == PS4_UVD_LIVE_REJECT_PHASE, "no_wrap");
	s = snap(live);
	CHECK(s.claim_attempts == U64_MAX && s.ticket == U64_MAX && s.invalidations == U32_MAX &&
	      s.rejected_transitions == U32_MAX && s.first_event == PS4_LIVE_EVENT_RESET &&
	      s.last_event == PS4_LIVE_EVENT_SUSPEND, "no_wrap");
	off = device(false);
	off->state.rejected_claims = U32_MAX - 1;
	CHECK(claim(off, &obs, &again) == PS4_UVD_LIVE_REJECT_DISABLED &&
	      claim(off, &obs, &again) == PS4_UVD_LIVE_REJECT_DISABLED &&
	      snap(off).rejected_claims == U32_MAX, "no_wrap");
	cases++;
	destroy(live);
	destroy(off);
}

struct claimer {
	struct amdgpu_ps4_uvd_live *live;
	const struct ps4_uvd_live_observation *obs;
	struct gate *gate;
	enum ps4_uvd_live_verdict verdict;
	u64 ticket;
};

static void *claim_body(void *arg)
{
	struct claimer *c = arg;

	gate_wait(c->gate);
	c->verdict = claim(c->live, c->obs, &c->ticket);
	return NULL;
}

/* Competing claimers on several devices in one boot: exactly one wins. */
static void claim_round(unsigned devices, unsigned per_device)
{
	struct amdgpu_ps4_uvd_live *live[4];
	struct claimer claimers[16];
	pthread_t threads[16];
	struct ps4_uvd_live_observation obs = prepared();
	struct gate gate = { 0 };
	struct ps4_uvd_live_snapshot s;
	unsigned d, i, n = devices * per_device, winners = 0, winner = 0;

	CHECK(devices <= 4 && n <= 16, "fixture_budget");
	new_boot();
	for (d = 0; d < devices; d++)
		live[d] = device(true);
	for (i = 0; i < n; i++) {
		claimers[i] = (struct claimer) {
			.live = live[i % devices], .obs = &obs, .gate = &gate,
		};
		run_thread(&threads[i], claim_body, &claimers[i]);
	}
	gate_open(&gate, n);
	for (i = 0; i < n; i++)
		join_thread(threads[i]);
	for (i = 0; i < n; i++) {
		if (claimers[i].verdict == PS4_UVD_LIVE_OK) {
			winners++;
			winner = i;
		}
	}
	CHECK(winners == 1, "competing_claims");
	for (i = 0; i < n; i++) {
		if (i == winner)
			continue;
		CHECK(claimers[i].ticket == 0 &&
		      claimers[i].verdict == (claimers[i].live == claimers[winner].live ?
					      PS4_UVD_LIVE_REJECT_USED :
					      PS4_UVD_LIVE_REJECT_BOOT_USED), "competing_claims");
	}
	for (d = 0; d < devices; d++) {
		s = snap(live[d]);
		invariants(&s);
		CHECK(s.claim_attempts == per_device &&
		      s.rejected_claims == per_device - (live[d] == claimers[winner].live) &&
		      s.used == (live[d] == claimers[winner].live), "competing_claims");
		if (s.used)
			CHECK(s.ticket == claimers[winner].ticket, "competing_claims");
		destroy(live[d]);
	}
}

static void test_competing_claims(void)
{
	unsigned round;

	for (round = 0; round < 64; round++)
		claim_round(1, 8);
	cases++;
	for (round = 0; round < 64; round++)
		claim_round(4, 3);
	cases++;
}

struct race {
	struct amdgpu_ps4_uvd_live *live;
	struct ps4_uvd_live_observation obs;
	struct gate gate;
	unsigned delay;
	_Atomic unsigned done;
	enum ps4_uvd_live_verdict claimed, started, finished;
	struct ps4_uvd_live_snapshot after;
};

static void *owner_body(void *arg)
{
	struct race *race = arg;
	struct ps4_uvd_live_result result = { .error = 0, .code = race->delay };
	u64 ticket;

	gate_wait(&race->gate);
	race->claimed = claim(race->live, &race->obs, &ticket);
	if (race->claimed == PS4_UVD_LIVE_OK) {
		race->started = start(race->live, ticket, &race->obs, GUARDS);
		/* Every successful claim ends with exactly one finish. */
		race->finished = finish(race->live, ticket, &result);
	}
	atomic_fetch_add(&race->done, 1);
	return NULL;
}

static void *invalidator_body(void *arg)
{
	struct race *race = arg;

	gate_wait(&race->gate);
	spin(race->delay);
	invalidate(race->live, PS4_LIVE_EVENT_RESET, &race->after);
	atomic_fetch_add(&race->done, 1);
	return NULL;
}

static void *reader_body(void *arg)
{
	struct race *race = arg;
	struct ps4_uvd_live_snapshot previous, current;

	gate_wait(&race->gate);
	previous = snap(race->live);
	invariants(&previous);
	while (atomic_load(&race->done) != 2) {
		current = snap(race->live);
		invariants(&current);
		monotonic(&previous, &current);
		previous = current;
	}
	return NULL;
}

/* Invalidation racing claim/start/finish: every interleaving has one outcome. */
static void test_invalidation_races(void)
{
	struct race race;
	struct ps4_uvd_live_snapshot s;
	pthread_t threads[3];
	unsigned round;

	for (round = 0; round < 1500; round++) {
		new_boot();
		memset(&race, 0, sizeof(race));
		race.live = device(true);
		race.obs = prepared();
		race.delay = (round * 37) % 4096;
		run_thread(&threads[0], owner_body, &race);
		run_thread(&threads[1], invalidator_body, &race);
		run_thread(&threads[2], reader_body, &race);
		gate_open(&race.gate, 3);
		join_thread(threads[0]);
		join_thread(threads[1]);
		join_thread(threads[2]);
		s = snap(race.live);
		invariants(&s);
		monotonic(&race.after, &s);
		CHECK(s.invalidated && s.invalidations == 1 && s.first_event == PS4_LIVE_EVENT_RESET,
		      "race_outcome");
		if (race.claimed != PS4_UVD_LIVE_OK) {
			CHECK(race.claimed == PS4_UVD_LIVE_REJECT_INVALIDATED && no_claim_record(&s) &&
			      s.first_event_phase == PS4_UVD_LIVE_IDLE && !s.touch_entered,
			      "race_outcome");
			CHECK(atomic_read(&ps4_uvd_live_boot_claimed) == 0, "latch_not_consumed");
			races.claim_rejected++;
		} else {
			/* Entry happened iff it preceded the invalidation's cut. */
			CHECK((race.started == PS4_UVD_LIVE_OK) == race.after.touch_entered,
			      "no_entry_after_invalidation");
			CHECK(race.finished == PS4_UVD_LIVE_OK && s.phase == PS4_UVD_LIVE_FINISHED &&
			      s.result_published && s.result.code == race.delay, "race_outcome");
			if (race.started != PS4_UVD_LIVE_OK) {
				CHECK(race.started == PS4_UVD_LIVE_REJECT_TERMINAL &&
				      s.end == PS4_UVD_LIVE_END_INVALIDATED &&
				      s.end_phase == PS4_UVD_LIVE_CLAIMED && !s.touch_entered &&
				      s.result_after_terminal, "race_outcome");
				races.start_refused++;
			} else if (race.after.phase == PS4_UVD_LIVE_RUNNING) {
				CHECK(s.end == PS4_UVD_LIVE_END_INVALIDATED &&
				      s.end_phase == PS4_UVD_LIVE_RUNNING && s.touch_entered &&
				      s.result_after_terminal && !s.result.error, "race_outcome");
				races.invalidated_running++;
			} else {
				CHECK(race.after.phase == PS4_UVD_LIVE_FINISHED &&
				      s.end == PS4_UVD_LIVE_END_COMPLETED && s.touch_entered &&
				      !s.result_after_terminal &&
				      s.first_event_phase == PS4_UVD_LIVE_FINISHED, "race_outcome");
				races.finished_first++;
			}
		}
		destroy(race.live);
	}
	cases++;
}

struct publication {
	struct amdgpu_ps4_uvd_live *live;
	struct ps4_uvd_live_result expected;
	struct gate gate;
	_Atomic bool done;
};

static void *publication_reader(void *arg)
{
	struct publication *p = arg;
	const struct ps4_uvd_live_result zero = { 0 };
	struct ps4_uvd_live_snapshot s;
	bool last = false;

	gate_wait(&p->gate);
	while (!last) {
		last = atomic_load(&p->done);
		s = snap(p->live);
		invariants(&s);
		/* All or nothing: the result is never visible without its terminal. */
		if (s.result_published)
			CHECK(s.phase == PS4_UVD_LIVE_FINISHED &&
			      s.end == PS4_UVD_LIVE_END_COMPLETED &&
			      same_result(&s.result, &p->expected), "result_publication");
		else
			CHECK(s.phase == PS4_UVD_LIVE_RUNNING && s.end == PS4_UVD_LIVE_END_NONE &&
			      same_result(&s.result, &zero) && !last, "result_publication");
	}
	return NULL;
}

static void test_result_publication(void)
{
	struct ps4_uvd_live_observation obs = prepared();
	struct publication p;
	pthread_t thread;
	unsigned round;
	u64 ticket;

	for (round = 0; round < 300; round++) {
		new_boot();
		memset(&p, 0, sizeof(p));
		p.live = device(true);
		p.expected = (struct ps4_uvd_live_result) {
			.error = 0, .code = round + 1,
			.words = { round, ~round, round ^ 0x5a5a5a5aU, 0xffffffffU },
		};
		CHECK(claim(p.live, &obs, &ticket) == PS4_UVD_LIVE_OK &&
		      start(p.live, ticket, &obs, GUARDS) == PS4_UVD_LIVE_OK, "start_ok");
		run_thread(&thread, publication_reader, &p);
		gate_open(&p.gate, 1);
		spin((round * 53) % 2048);
		CHECK(finish(p.live, ticket, &p.expected) == PS4_UVD_LIVE_OK, "finish_ok");
		atomic_store(&p.done, true);
		join_thread(thread);
		destroy(p.live);
	}
	cases++;
}

/* Interleaving counts vary by host and run; they never enter stdout. */
static void write_race_report(void)
{
	const char *path = getenv("LIVE_CONTROL_RACE_REPORT");
	FILE *report;

	if (!path)
		return;
	report = fopen(path, "w");
	CHECK(report != NULL, "race_report");
	fprintf(report, "claim_rejected=%u\nstart_refused=%u\ninvalidated_running=%u\n"
		"finished_first=%u\n", races.claim_rejected, races.start_refused,
		races.invalidated_running, races.finished_first);
	CHECK(!fclose(report), "race_report");
}

int main(void)
{
	test_errno();
	test_uninitialized();
	test_init();
	test_admission();
	test_once();
	test_completion();
	test_failure_and_cancel();
	test_start();
	test_invalidation_orders();
	test_saturation();
	concurrent = true;
	test_competing_claims();
	test_invalidation_races();
	test_result_publication();
	concurrent = false;
	write_race_report();
	printf("PASS: %u actual-source live-control cases; synthetic locks only\n", cases);
	return 0;
}
