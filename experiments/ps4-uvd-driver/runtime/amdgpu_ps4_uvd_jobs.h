/* SPDX-License-Identifier: MIT */
/* Copyright 2026 psX Linux contributors */
#ifndef AMDGPU_PS4_UVD_JOBS_H
#define AMDGPU_PS4_UVD_JOBS_H

#include <linux/list.h>
#include <linux/spinlock.h>
#include <linux/types.h>
#include <linux/workqueue.h>

struct amdgpu_cs_parser;
struct amdgpu_bo;
struct amdgpu_job;
struct amdgpu_ring;
struct dma_fence;
struct amdgpu_ps4_uvd_lease;

#define AMDGPU_PS4_UVD_JOB_MAX_BOS 128U
#define AMDGPU_PS4_UVD_JOBS_MAX_JOBS 32U
#define AMDGPU_PS4_UVD_JOBS_MAX_OBJECTS 1024U
#define AMDGPU_PS4_UVD_JOBS_MAX_BYTES (1ULL << 30)

/* Charges are conservative per-lease counts, unique within each lease. BOs
 * shared by two outstanding jobs are charged twice and independently pinned.
 * max_bytes includes complete BO sizes AND actual kernel IB SA span sizes.
 */
struct amdgpu_ps4_uvd_jobs_limits {
	u64 max_bytes;
	u64 max_job_bytes;
	u64 max_job_ib_bytes;
	u32 max_objects;
	u32 max_job_objects;
	u32 max_jobs;
};

/* Parent-owned, zero-initialized once and retained through device lifetime.
 * No reset, rearm or post-fault destructor. close() must be nonblocking and
 * IRQ-safe: close runtime admission; do not reset, force fences or free DMA.
 */
struct amdgpu_ps4_uvd_jobs {
	spinlock_t lock;
	struct list_head leases;
	struct work_struct reap_work;
	struct amdgpu_ring *ring;
	struct amdgpu_ps4_uvd_jobs_limits limits;
	u64 fence_context;
	u64 hw_completed;
	u64 bytes;
	u32 objects;
	u32 jobs;
	u32 quarantined;
	int error;
	bool initialized;
	bool closed;
	void (*close)(void *arg, int error);
	void *close_arg;
};

int amdgpu_ps4_uvd_jobs_init(struct amdgpu_ps4_uvd_jobs *state,
	struct amdgpu_ring *ring, u64 fence_context,
	const struct amdgpu_ps4_uvd_jobs_limits *limits,
	void (*close)(void *arg, int error), void *close_arg);

/* Called ONLY after parser_bos success, before patch_jobs, with all BO
 * reservations and bo_list_mutex held. Rejects gangs/unsupported BOs/maps.
 * NULL state is gate-off and returns success without changing the job.
 */
int amdgpu_ps4_uvd_jobs_admit(struct amdgpu_ps4_uvd_jobs *state,
	struct amdgpu_cs_parser *parser, struct amdgpu_job *job);

/* Kernel-only submit/submit_direct path. Parent guarantees every message,
 * FW, ring and SA-manager backing BO is permanently pinned and device-owned.
 * This charges/retains the job's SA spans, not arbitrary kernel DMA targets.
 */
int amdgpu_ps4_uvd_jobs_admit_kernel(struct amdgpu_ps4_uvd_jobs *state,
	struct amdgpu_job *job);
int amdgpu_ps4_uvd_jobs_admit_kernel_message(struct amdgpu_ps4_uvd_jobs *state,
	struct amdgpu_job *job, struct amdgpu_bo *message_bo);

/* Common CS mapping lookup must reject packet addresses outside the pinned
 * lease, including implicitly reserved VM objects absent from BO_HANDLES.
 * Gate-off/unleased jobs return true and retain the ordinary parser checks.
 */
bool amdgpu_ps4_uvd_jobs_owns_bo(const struct amdgpu_job *job,
	const struct amdgpu_bo *bo);

/* CS rollback ONLY, before error_backoff unlocks parser reservations. */
void amdgpu_ps4_uvd_jobs_abort_reserved(struct amdgpu_job *job);

/* Immediately before the EARLIEST amdgpu_ib_schedule; conservatively marks
 * entered even if scheduling subsequently fails. No retry/resubmit allowed.
 */
int amdgpu_ps4_uvd_jobs_enter(struct amdgpu_job *job);
void amdgpu_ps4_uvd_jobs_submitted(struct amdgpu_job *job,
	struct dma_fence *actual_hw_fence, int schedule_error);

/* Actual WB read/validation path ONLY, BEFORE any matching fence signal.
 * completed/emitted are full monotonically extended hardware fence seqnos.
 * Never call from CPU fence-write, force-completion, recovery or reset.
 */
void amdgpu_ps4_uvd_jobs_observe_hw(struct amdgpu_ps4_uvd_jobs *state,
	u64 fence_context, u64 completed, u64 emitted);

/* Atomic/IRQ-safe terminal closure; all entered leases become retained. */
void amdgpu_ps4_uvd_jobs_fault(struct amdgpu_ps4_uvd_jobs *state, int error);

/* All normal early free_resources calls MUST return while this is true.
 * Only jobs_job_free/abort_reserved may detach the lease and permit SA free.
 */
bool amdgpu_ps4_uvd_jobs_defer_resources(const struct amdgpu_job *job);

/* First operation in BOTH job_free paths, before sched cleanup. IRQ-safe.
 * true: no lease; core may free normally.
 * false: work owns cleanup/retention; caller MUST return without any cleanup.
 * Pending direct jobs defer normally; uncertainty permanently quarantines.
 */
bool amdgpu_ps4_uvd_jobs_job_free(struct amdgpu_job *job, bool scheduler_free);

/* Core hook, called only after detaching a proved-safe lease. Preserve the
 * original scheduler free_cb path: dequeued jobs have base.entity == NULL.
 */
void amdgpu_ps4_uvd_job_free_deferred(struct amdgpu_job *job,
	bool scheduler_free);

/* Parent runtime bridge: NULL outside the explicitly enabled PS4 UVD ring.
 * Implemented by the parent, with no assumed new amdgpu_device member here.
 */
struct amdgpu_ps4_uvd_jobs *
amdgpu_ps4_uvd_runtime_jobs(struct amdgpu_ring *ring);

#endif
