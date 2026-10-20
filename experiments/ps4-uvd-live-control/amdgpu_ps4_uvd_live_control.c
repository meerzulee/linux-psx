// SPDX-License-Identifier: MIT
/*
 * Unwired PS4 live-readiness control state. Pure transitions over copied
 * scalars under one leaf spinlock: no callback, MMIO, firmware, allocation,
 * clock read, command submission, scheduling or sleep happens here.
 *
 * It never certifies DMA quiescence, never makes touched preparation BOs
 * freeable, never arms or rearms the IRQ observer and cannot veto reset,
 * suspend or removal. Those obligations stay with the integrated core.
 */
#include <linux/atomic.h>
#include <linux/errno.h>
#include <linux/limits.h>
#include <linux/spinlock.h>

#include "amdgpu_ps4_uvd_live_control.h"

/* A new adev after driver rebind is not permission to claim again this boot. */
static atomic_t ps4_uvd_live_boot_claimed = ATOMIC_INIT(0);

static void ps4_uvd_live_count32(u32 *counter)
{
	if (*counter != U32_MAX)
		(*counter)++;
}

static void ps4_uvd_live_count64(u64 *counter)
{
	if (*counter != U64_MAX)
		(*counter)++;
}

static enum ps4_uvd_live_verdict
ps4_uvd_live_reject(struct ps4_uvd_live_snapshot *s, u32 *counter,
		    enum ps4_uvd_live_verdict verdict)
{
	ps4_uvd_live_count32(counter);
	s->last_rejection = verdict;
	return verdict;
}

/* Shared by claim() and start(). These are caller observations only. */
static enum ps4_uvd_live_verdict
ps4_uvd_live_check(const struct ps4_uvd_live_observation *obs)
{
	const struct ps4_live_prepare_snapshot *p = &obs->prepare;

	if (!p->selected || !p->attempted || p->stage != PS4_LIVE_PREPARED ||
	    !p->proof_passed || !p->retained || p->error)
		return PS4_UVD_LIVE_REJECT_NOT_PREPARED;
	if (!p->reservation_seen || !p->allocator_alive)
		return PS4_UVD_LIVE_REJECT_OWNERSHIP;
	/*
	 * The producer counts the initial GART_ENABLE before any proof, so a
	 * real proof generation is nonzero. Its counter wraps rather than
	 * saturates; U64_MAX is refused anyway so that equality never stands
	 * for an exhausted count. Equal only while no invalidation followed.
	 */
	if (!p->proof_generation || p->proof_generation == U64_MAX ||
	    p->observed_generation != p->proof_generation)
		return PS4_UVD_LIVE_REJECT_GENERATION;
	/* Publication requires reset counter 0 and records it as 0. */
	if (p->proof_reset_counter || obs->gpu_reset_counter)
		return PS4_UVD_LIVE_REJECT_RESET;
	return PS4_UVD_LIVE_OK;
}

/*
 * The same immutable preparation descriptor that the claim observed.
 * proof_reset_counter needs no comparison: check() pins it to 0 both times.
 */
static bool ps4_uvd_live_same_proof(const struct ps4_live_prepare_snapshot *now,
				    const struct ps4_live_prepare_snapshot *then)
{
	return now->proof_generation == then->proof_generation &&
	       now->proof_ns == then->proof_ns &&
	       now->pd_addr == then->pd_addr && now->pd_bytes == then->pd_bytes &&
	       now->pt_bytes == then->pt_bytes && now->r1_bytes == then->r1_bytes &&
	       now->rb_bytes == then->rb_bytes && now->ib_bytes == then->ib_bytes &&
	       now->mapped_pages == then->mapped_pages &&
	       now->copy_crc == then->copy_crc;
}

/* The first terminal record is evidence; later events never rewrite it. */
static void ps4_uvd_live_set_end(struct ps4_uvd_live_snapshot *s,
				 enum ps4_uvd_live_end end,
				 enum ps4_uvd_live_verdict verdict,
				 enum ps4_live_prepare_event event, int error)
{
	if (s->end != PS4_UVD_LIVE_END_NONE)
		return;
	s->end = end;
	s->end_phase = s->phase;
	s->end_verdict = verdict;
	s->end_event = event;
	s->end_error = error;
}

static bool ps4_uvd_live_ready(struct amdgpu_ps4_uvd_live *live)
{
	/* Pairs with the release in init(): lock and state are published. */
	return smp_load_acquire(&live->initialized);
}

/* Caller holds the lock; the claim is open and the ticket matched. */
static enum ps4_uvd_live_verdict
ps4_uvd_live_enter(struct ps4_uvd_live_snapshot *s,
		   const struct ps4_uvd_live_observation *obs, u32 guards)
{
	enum ps4_uvd_live_verdict verdict;

	if (guards != PS4_UVD_LIVE_GUARDS_REQUIRED)
		verdict = PS4_UVD_LIVE_REJECT_GUARD;
	else
		verdict = ps4_uvd_live_check(obs);
	if (verdict == PS4_UVD_LIVE_OK &&
	    !ps4_uvd_live_same_proof(&obs->prepare, &s->claim.prepare))
		verdict = PS4_UVD_LIVE_REJECT_IDENTITY;

	if (verdict != PS4_UVD_LIVE_OK) {
		/* Refusal is final: the claim stays used and never runs. */
		ps4_uvd_live_set_end(s, PS4_UVD_LIVE_END_REFUSED, verdict,
				     PS4_LIVE_EVENT_NONE,
				     amdgpu_ps4_uvd_live_errno(verdict));
		return verdict;
	}
	/* Sticky before the owner can perform its first hardware operation. */
	s->touch_entered = true;
	s->phase = PS4_UVD_LIVE_RUNNING;
	return PS4_UVD_LIVE_OK;
}

void amdgpu_ps4_uvd_live_init(struct amdgpu_ps4_uvd_live *live, bool opted_in)
{
	/* Never turn a second initialization into a fresh claim or opt-in. */
	if (ps4_uvd_live_ready(live)) {
		amdgpu_ps4_uvd_live_invalidate(live, PS4_LIVE_EVENT_REINIT, NULL);
		return;
	}
	spin_lock_init(&live->lock);
	live->state = (struct ps4_uvd_live_snapshot) {
		.phase = opted_in ? PS4_UVD_LIVE_IDLE : PS4_UVD_LIVE_DISABLED,
		.opted_in = opted_in,
	};
	/* Publish the lock and state before any entry point can use them. */
	smp_store_release(&live->initialized, true);
}

enum ps4_uvd_live_verdict
amdgpu_ps4_uvd_live_claim(struct amdgpu_ps4_uvd_live *live,
			  const struct ps4_uvd_live_observation *obs, u64 *ticket)
{
	struct ps4_uvd_live_snapshot *s = &live->state;
	enum ps4_uvd_live_verdict verdict;
	unsigned long flags;

	*ticket = 0;
	if (!ps4_uvd_live_ready(live))
		return PS4_UVD_LIVE_REJECT_UNINITIALIZED;

	spin_lock_irqsave(&live->lock, flags);
	ps4_uvd_live_count64(&s->claim_attempts);
	/* Ordered admission. Only the boot latch below consumes anything. */
	if (!s->opted_in)
		verdict = PS4_UVD_LIVE_REJECT_DISABLED;
	else if (s->used)
		verdict = PS4_UVD_LIVE_REJECT_USED;
	else if (s->invalidated)
		verdict = PS4_UVD_LIVE_REJECT_INVALIDATED;
	else
		verdict = ps4_uvd_live_check(obs);
	if (verdict == PS4_UVD_LIVE_OK &&
	    atomic_cmpxchg(&ps4_uvd_live_boot_claimed, 0, 1))
		verdict = PS4_UVD_LIVE_REJECT_BOOT_USED;

	if (verdict == PS4_UVD_LIVE_OK) {
		/* Never cleared: completion, failure or invalidation keep it. */
		s->used = true;
		s->phase = PS4_UVD_LIVE_CLAIMED;
		s->ticket = s->claim_attempts;
		s->claim = *obs;
		*ticket = s->ticket;
	} else {
		ps4_uvd_live_reject(s, &s->rejected_claims, verdict);
	}
	spin_unlock_irqrestore(&live->lock, flags);
	return verdict;
}

enum ps4_uvd_live_verdict
amdgpu_ps4_uvd_live_start(struct amdgpu_ps4_uvd_live *live, u64 ticket,
			  const struct ps4_uvd_live_observation *obs, u32 guards)
{
	struct ps4_uvd_live_snapshot *s = &live->state;
	enum ps4_uvd_live_verdict verdict;
	unsigned long flags;

	if (!ps4_uvd_live_ready(live))
		return PS4_UVD_LIVE_REJECT_UNINITIALIZED;

	spin_lock_irqsave(&live->lock, flags);
	/* An invalidated or refused claim never runs, whatever @obs says. */
	if (s->phase != PS4_UVD_LIVE_CLAIMED)
		verdict = ps4_uvd_live_reject(s, &s->rejected_transitions,
					      PS4_UVD_LIVE_REJECT_PHASE);
	else if (ticket != s->ticket)
		verdict = ps4_uvd_live_reject(s, &s->rejected_transitions,
					      PS4_UVD_LIVE_REJECT_TICKET);
	else if (s->end != PS4_UVD_LIVE_END_NONE)
		verdict = ps4_uvd_live_reject(s, &s->rejected_transitions,
					      PS4_UVD_LIVE_REJECT_TERMINAL);
	else
		verdict = ps4_uvd_live_enter(s, obs, guards);
	spin_unlock_irqrestore(&live->lock, flags);
	return verdict;
}

enum ps4_uvd_live_verdict
amdgpu_ps4_uvd_live_finish(struct amdgpu_ps4_uvd_live *live, u64 ticket,
			   const struct ps4_uvd_live_result *result)
{
	struct ps4_uvd_live_snapshot *s = &live->state;
	enum ps4_uvd_live_verdict verdict = PS4_UVD_LIVE_OK;
	unsigned long flags;

	if (!ps4_uvd_live_ready(live))
		return PS4_UVD_LIVE_REJECT_UNINITIALIZED;

	spin_lock_irqsave(&live->lock, flags);
	if (s->phase != PS4_UVD_LIVE_CLAIMED && s->phase != PS4_UVD_LIVE_RUNNING) {
		verdict = ps4_uvd_live_reject(s, &s->rejected_transitions,
					      PS4_UVD_LIVE_REJECT_PHASE);
	} else if (ticket != s->ticket) {
		verdict = ps4_uvd_live_reject(s, &s->rejected_transitions,
					      PS4_UVD_LIVE_REJECT_TICKET);
	} else {
		/* Published once, with its terminal, in one critical section. */
		s->result = *result;
		s->result_published = true;
		s->result_after_terminal = s->end != PS4_UVD_LIVE_END_NONE;
		if (s->phase == PS4_UVD_LIVE_CLAIMED)
			ps4_uvd_live_set_end(s, PS4_UVD_LIVE_END_CANCELLED,
					     PS4_UVD_LIVE_OK, PS4_LIVE_EVENT_NONE,
					     result->error ? result->error : -ECANCELED);
		else if (result->error)
			ps4_uvd_live_set_end(s, PS4_UVD_LIVE_END_FAILED,
					     PS4_UVD_LIVE_OK, PS4_LIVE_EVENT_NONE,
					     result->error);
		else
			ps4_uvd_live_set_end(s, PS4_UVD_LIVE_END_COMPLETED,
					     PS4_UVD_LIVE_OK, PS4_LIVE_EVENT_NONE, 0);
		s->phase = PS4_UVD_LIVE_FINISHED;
	}
	spin_unlock_irqrestore(&live->lock, flags);
	return verdict;
}

void amdgpu_ps4_uvd_live_invalidate(struct amdgpu_ps4_uvd_live *live,
				    enum ps4_live_prepare_event event,
				    struct ps4_uvd_live_snapshot *after)
{
	struct ps4_uvd_live_snapshot *s = &live->state;
	unsigned long flags;

	if (!ps4_uvd_live_ready(live)) {
		if (after)
			*after = (struct ps4_uvd_live_snapshot) { };
		return;
	}
	spin_lock_irqsave(&live->lock, flags);
	if (!s->invalidated) {
		s->first_event = event;
		s->first_event_phase = s->phase;
	}
	/* One-way. used, touch_entered and any terminal record are kept. */
	s->invalidated = true;
	s->last_event = event;
	ps4_uvd_live_count32(&s->invalidations);
	if (s->phase == PS4_UVD_LIVE_CLAIMED || s->phase == PS4_UVD_LIVE_RUNNING)
		ps4_uvd_live_set_end(s, PS4_UVD_LIVE_END_INVALIDATED,
				     PS4_UVD_LIVE_OK, event, -ESTALE);
	if (after)
		*after = *s;
	spin_unlock_irqrestore(&live->lock, flags);
}

void amdgpu_ps4_uvd_live_snapshot(struct amdgpu_ps4_uvd_live *live,
				  struct ps4_uvd_live_snapshot *out)
{
	unsigned long flags;

	if (!ps4_uvd_live_ready(live)) {
		*out = (struct ps4_uvd_live_snapshot) { };
		return;
	}
	spin_lock_irqsave(&live->lock, flags);
	*out = live->state;
	spin_unlock_irqrestore(&live->lock, flags);
}

int amdgpu_ps4_uvd_live_errno(enum ps4_uvd_live_verdict verdict)
{
	switch (verdict) {
	case PS4_UVD_LIVE_OK:
		return 0;
	case PS4_UVD_LIVE_REJECT_UNINITIALIZED:
		return -ENODEV;
	case PS4_UVD_LIVE_REJECT_DISABLED:
		return -EPERM;
	case PS4_UVD_LIVE_REJECT_USED:
	case PS4_UVD_LIVE_REJECT_BOOT_USED:
		return -EALREADY;
	case PS4_UVD_LIVE_REJECT_INVALIDATED:
	case PS4_UVD_LIVE_REJECT_GENERATION:
	case PS4_UVD_LIVE_REJECT_RESET:
	case PS4_UVD_LIVE_REJECT_TERMINAL:
	case PS4_UVD_LIVE_REJECT_IDENTITY:
		return -ESTALE;
	case PS4_UVD_LIVE_REJECT_NOT_PREPARED:
	case PS4_UVD_LIVE_REJECT_OWNERSHIP:
	case PS4_UVD_LIVE_REJECT_PHASE:
	case PS4_UVD_LIVE_REJECT_TICKET:
	case PS4_UVD_LIVE_REJECT_GUARD:
		return -EINVAL;
	}
	return -EINVAL;
}
