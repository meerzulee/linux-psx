/* SPDX-License-Identifier: MIT */
#ifndef AMDGPU_PS4_UVD_LIVE_CONTROL_H
#define AMDGPU_PS4_UVD_LIVE_CONTROL_H

#include <linux/bits.h>
#include <linux/spinlock.h>
#include <linux/types.h>

#include "amdgpu_ps4_live_prepare.h"

/*
 * Per-device control state for one future live UVD readiness operation.
 * Unwired: no caller, worker, debugfs file, module parameter or hardware
 * path uses it yet. Every input is a caller observation copied by value;
 * nothing here proves current hardware, mapping, reset or DMA state.
 */

enum ps4_uvd_live_phase {
	PS4_UVD_LIVE_UNINITIALIZED = 0,	/* zeroed storage; init has not run */
	PS4_UVD_LIVE_DISABLED,		/* no distinct live opt-in this boot */
	PS4_UVD_LIVE_IDLE,		/* opted in and never claimed */
	PS4_UVD_LIVE_CLAIMED,		/* used; no hardware entry granted */
	PS4_UVD_LIVE_RUNNING,		/* entry granted; touch witness set */
	PS4_UVD_LIVE_FINISHED,		/* owner reported its final step */
};

/* First terminal record. Later events never rewrite it. */
enum ps4_uvd_live_end {
	PS4_UVD_LIVE_END_NONE = 0,
	PS4_UVD_LIVE_END_COMPLETED,	/* finish() after entry, error 0 */
	PS4_UVD_LIVE_END_FAILED,	/* finish() after entry, error != 0 */
	PS4_UVD_LIVE_END_CANCELLED,	/* finish() without entry */
	PS4_UVD_LIVE_END_REFUSED,	/* start() failed a fresh entry check */
	PS4_UVD_LIVE_END_INVALIDATED,	/* invalidation reached the claim first */
};

enum ps4_uvd_live_verdict {
	PS4_UVD_LIVE_OK = 0,
	PS4_UVD_LIVE_REJECT_UNINITIALIZED,
	PS4_UVD_LIVE_REJECT_DISABLED,
	PS4_UVD_LIVE_REJECT_USED,
	PS4_UVD_LIVE_REJECT_INVALIDATED,
	PS4_UVD_LIVE_REJECT_NOT_PREPARED,
	PS4_UVD_LIVE_REJECT_OWNERSHIP,
	PS4_UVD_LIVE_REJECT_GENERATION,
	PS4_UVD_LIVE_REJECT_RESET,
	PS4_UVD_LIVE_REJECT_BOOT_USED,
	PS4_UVD_LIVE_REJECT_PHASE,
	PS4_UVD_LIVE_REJECT_TICKET,
	PS4_UVD_LIVE_REJECT_TERMINAL,
	PS4_UVD_LIVE_REJECT_GUARD,
	PS4_UVD_LIVE_REJECT_IDENTITY,
};

/* Caller assertions presented to start(). The helper cannot verify them. */
#define PS4_UVD_LIVE_GUARD_DEVICE	BIT(0)	/* drm_dev_enter() is held */
#define PS4_UVD_LIVE_GUARD_RESET	BIT(1)	/* reset-domain read side is held */
#define PS4_UVD_LIVE_GUARD_FIRMWARE	BIT(2)	/* image validated in CPU memory */
#define PS4_UVD_LIVE_GUARDS_REQUIRED	(PS4_UVD_LIVE_GUARD_DEVICE | \
					 PS4_UVD_LIVE_GUARD_RESET | \
					 PS4_UVD_LIVE_GUARD_FIRMWARE)

/* Copied by the caller from amdgpu_ps4_live_prepare_snapshot() plus one
 * read of adev->gpu_reset_counter. A stale copy is still only an observation.
 */
struct ps4_uvd_live_observation {
	struct ps4_live_prepare_snapshot prepare;
	u32 gpu_reset_counter;
};

#define PS4_UVD_LIVE_RESULT_WORDS 4

/* Owner-defined scalar result, copied once by finish(). No pointers. */
struct ps4_uvd_live_result {
	int error;
	u32 code;
	u32 words[PS4_UVD_LIVE_RESULT_WORDS];
};

/* Scalar state and its snapshot. Exporters must format fields one by one;
 * copying the raw structure would also copy unspecified padding.
 */
struct ps4_uvd_live_snapshot {
	enum ps4_uvd_live_phase phase;
	bool opted_in;			/* fixed by the first init */
	bool used;			/* sticky once a claim succeeds */
	bool touch_entered;		/* sticky once start() grants entry */
	bool invalidated;		/* sticky once any invalidation arrives */
	bool result_published;		/* set by the one accepted finish() */
	bool result_after_terminal;	/* that finish() followed a terminal */

	/* Written once by the successful claim. */
	u64 ticket;
	struct ps4_uvd_live_observation claim;

	/* Written once by the first terminal transition. */
	enum ps4_uvd_live_end end;
	enum ps4_uvd_live_phase end_phase;
	enum ps4_uvd_live_verdict end_verdict;	/* REFUSED reason only */
	enum ps4_live_prepare_event end_event;	/* INVALIDATED cause only */
	int end_error;

	/* Written once by the accepted finish(). */
	struct ps4_uvd_live_result result;

	/* The first invalidation is fixed; later ones stay countable. */
	enum ps4_live_prepare_event first_event;
	enum ps4_uvd_live_phase first_event_phase;
	enum ps4_live_prepare_event last_event;
	u32 invalidations;		/* saturating */

	/* Rejected calls never change any field above. */
	u64 claim_attempts;		/* saturating; source of the ticket */
	u32 rejected_claims;		/* saturating */
	u32 rejected_transitions;	/* saturating start()/finish() misuse */
	enum ps4_uvd_live_verdict last_rejection;
};

struct amdgpu_ps4_uvd_live {
	spinlock_t lock;		/* leaf lock; never held on return */
	bool initialized;		/* release-published by init */
	struct ps4_uvd_live_snapshot state;	/* guarded by lock */
};

/*
 * init runs once on zeroed storage before any other call or event source;
 * a second init is recorded as a REINIT invalidation and keeps the first
 * opt-in. claim, start and finish return a verdict and never sleep.
 * invalidate always records and cannot veto; with a non-NULL @after it
 * returns the state at that same linearization point. After any
 * invalidation, start() never grants entry again. Snapshots are a coherent
 * cut of this helper only, not of preparation, fault history or hardware.
 */
void amdgpu_ps4_uvd_live_init(struct amdgpu_ps4_uvd_live *live, bool opted_in);
enum ps4_uvd_live_verdict
amdgpu_ps4_uvd_live_claim(struct amdgpu_ps4_uvd_live *live,
			  const struct ps4_uvd_live_observation *obs, u64 *ticket);
enum ps4_uvd_live_verdict
amdgpu_ps4_uvd_live_start(struct amdgpu_ps4_uvd_live *live, u64 ticket,
			  const struct ps4_uvd_live_observation *obs, u32 guards);
enum ps4_uvd_live_verdict
amdgpu_ps4_uvd_live_finish(struct amdgpu_ps4_uvd_live *live, u64 ticket,
			   const struct ps4_uvd_live_result *result);
void amdgpu_ps4_uvd_live_invalidate(struct amdgpu_ps4_uvd_live *live,
				    enum ps4_live_prepare_event event,
				    struct ps4_uvd_live_snapshot *after);
void amdgpu_ps4_uvd_live_snapshot(struct amdgpu_ps4_uvd_live *live,
				  struct ps4_uvd_live_snapshot *out);
int amdgpu_ps4_uvd_live_errno(enum ps4_uvd_live_verdict verdict);

#endif
