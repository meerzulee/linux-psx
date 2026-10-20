/* SPDX-License-Identifier: MIT */
/* Copyright 2026 psX Linux contributors */

#include <linux/dma-fence.h>
#include <linux/overflow.h>
#include <linux/slab.h>
#include <drm/drm_suballoc.h>

#include "amdgpu.h"
#include "amdgpu_cs.h"
#include "amdgpu_object.h"
#include "amdgpu_ttm.h"
#include "amdgpu_ps4_uvd_jobs.h"

struct amdgpu_ps4_uvd_lease {
	struct list_head link;
	struct amdgpu_ps4_uvd_jobs *state;
	struct amdgpu_job *job;
	struct dma_fence *hw;
	struct dma_fence_cb hw_cb;
	struct amdgpu_bo *bos[AMDGPU_PS4_UVD_JOB_MAX_BOS];
	u64 bytes;
	u64 ib_bytes;
	u32 bo_count;
	u32 pinned;
	u32 objects;
	bool entered;
	bool free_requested;
	bool scheduler_free;
	bool quarantined;
	bool busy;
};

static void ps4_uvd_quarantine(struct amdgpu_ps4_uvd_lease *lease)
{
	if (!lease->quarantined) {
		lease->quarantined = true;
		++lease->state->quarantined;
	}
}

void amdgpu_ps4_uvd_jobs_fault(struct amdgpu_ps4_uvd_jobs *state, int error)
{
	struct amdgpu_ps4_uvd_lease *lease;
	unsigned long flags;
	bool first;

	if (!state || !state->initialized)
		return;
	if (error >= 0)
		error = -EIO;
	spin_lock_irqsave(&state->lock, flags);
	first = !state->closed;
	state->closed = true;
	if (!state->error)
		state->error = error;
	list_for_each_entry(lease, &state->leases, link)
		if (lease->entered)
			ps4_uvd_quarantine(lease);
	spin_unlock_irqrestore(&state->lock, flags);
	if (first && state->close)
		state->close(state->close_arg, error);
	schedule_work(&state->reap_work);
}

static void ps4_uvd_uncharge(struct amdgpu_ps4_uvd_lease *lease)
{
	struct amdgpu_ps4_uvd_jobs *state = lease->state;
	unsigned long flags;

	spin_lock_irqsave(&state->lock, flags);
	list_del(&lease->link);
	state->bytes -= lease->bytes;
	state->objects -= lease->objects;
	--state->jobs;
	if (lease->quarantined)
		--state->quarantined;
	lease->job->ps4_uvd_lease = NULL;
	spin_unlock_irqrestore(&state->lock, flags);
}

static void ps4_uvd_free_ibs(struct amdgpu_ps4_uvd_lease *lease)
{
	u32 i;

	/* NULL is valid only for a job that never entered scheduling. */
	for (i = 0; i < lease->job->num_ibs; ++i)
		amdgpu_ib_free(&lease->job->ibs[i], lease->hw);
}

static void ps4_uvd_reap(struct work_struct *work)
{
	struct amdgpu_ps4_uvd_jobs *state =
		container_of(work, struct amdgpu_ps4_uvd_jobs, reap_work);
	struct amdgpu_ps4_uvd_lease *lease, *cursor;
	struct amdgpu_job *job;
	unsigned long flags;
	u32 n, i;
	bool release;
	bool scheduler_free;
	int status, r = 0;

	/* One bounded pass; pending jobs are revisited on HW signal/observation. */
	for (n = 0; n < state->limits.max_jobs; ++n) {
		lease = NULL;
		spin_lock_irqsave(&state->lock, flags);
		list_for_each_entry(cursor, &state->leases, link) {
			if (cursor->free_requested && !cursor->busy &&
			    !cursor->quarantined) {
				lease = cursor;
				lease->busy = true;
				list_move_tail(&lease->link, &state->leases);
				break;
			}
		}
		spin_unlock_irqrestore(&state->lock, flags);
		if (!lease)
			break;

		/* Never take a fence lock while holding the state lock: the real
		 * hardware signal callback may acquire the state lock.
		 */
		status = lease->hw ? dma_fence_get_status(lease->hw) : 0;
		if (lease->entered && (!lease->hw || status < 0))
			amdgpu_ps4_uvd_jobs_fault(state, status < 0 ? status : -EIO);
		spin_lock_irqsave(&state->lock, flags);
		release = !lease->entered ||
			(!state->closed && !lease->quarantined && status > 0 &&
			 lease->hw->context == state->fence_context &&
			 lease->hw->seqno <= state->hw_completed);
		if (!release) {
			lease->busy = false;
			spin_unlock_irqrestore(&state->lock, flags);
			continue;
		}
		/* Positive completion authorizes this lease's release here.
		 * Any other outstanding lease has its own reference and pin.
		 */
		spin_unlock_irqrestore(&state->lock, flags);

		for (i = 0; i < lease->pinned; ++i) {
			struct amdgpu_bo *bo = lease->bos[i];

			if (!bo)
				continue;
			r = amdgpu_bo_reserve(bo, true);
			if (r)
				break;
			amdgpu_bo_unpin(bo);
			amdgpu_bo_unreserve(bo);
			amdgpu_bo_unref(&lease->bos[i]);
		}
		if (i != lease->pinned) {
			amdgpu_ps4_uvd_jobs_fault(state, r);
			spin_lock_irqsave(&state->lock, flags);
			ps4_uvd_quarantine(lease);
			lease->busy = false;
			spin_unlock_irqrestore(&state->lock, flags);
			continue;
		}
		if (lease->hw)
			dma_fence_remove_callback(lease->hw, &lease->hw_cb);
		ps4_uvd_free_ibs(lease);
		job = lease->job;
		scheduler_free = lease->scheduler_free;
		ps4_uvd_uncharge(lease);
		dma_fence_put(lease->hw);
		kfree(lease);
		/* Both normal scheduler free_cb and direct-submit cleanup can
		 * arrive before this work. The detached job takes the ordinary
		 * cleanup path exactly once; no scheduler state was freed early.
		 */
		amdgpu_ps4_uvd_job_free_deferred(job, scheduler_free);
	}
}

int amdgpu_ps4_uvd_jobs_init(struct amdgpu_ps4_uvd_jobs *state,
	struct amdgpu_ring *ring, u64 fence_context,
	const struct amdgpu_ps4_uvd_jobs_limits *limits,
	void (*close)(void *arg, int error), void *close_arg)
{
	if (!state || state->initialized || !ring || !ring->adev ||
	    !ring->funcs || ring->funcs->type != AMDGPU_RING_TYPE_UVD ||
	    ring->adev->asic_type != CHIP_LIVERPOOL || !fence_context ||
	    !limits || !limits->max_jobs || !limits->max_objects ||
	    !limits->max_bytes || !limits->max_job_bytes ||
	    !limits->max_job_ib_bytes || !limits->max_job_objects ||
	    limits->max_jobs > AMDGPU_PS4_UVD_JOBS_MAX_JOBS ||
	    limits->max_objects > AMDGPU_PS4_UVD_JOBS_MAX_OBJECTS ||
	    limits->max_bytes > AMDGPU_PS4_UVD_JOBS_MAX_BYTES ||
	    limits->max_job_bytes > limits->max_bytes ||
	    limits->max_job_ib_bytes > limits->max_job_bytes ||
	    limits->max_job_objects > AMDGPU_PS4_UVD_JOB_MAX_BOS ||
	    limits->max_job_objects > limits->max_objects)
		return -EINVAL;
	spin_lock_init(&state->lock);
	INIT_LIST_HEAD(&state->leases);
	INIT_WORK(&state->reap_work, ps4_uvd_reap);
	state->ring = ring;
	state->fence_context = fence_context;
	state->limits = *limits;
	state->close = close;
	state->close_arg = close_arg;
	/* Caller supplies zero-initialized state; seq0 is not completion. */
	state->initialized = true;
	return 0;
}

static int ps4_uvd_ibs(struct amdgpu_ps4_uvd_lease *lease)
{
	struct amdgpu_device *adev = lease->state->ring->adev;
	struct amdgpu_job *job = lease->job;
	u32 i, j, pool;
	u64 span, payload;

	if (!job->num_ibs || job->num_ibs > lease->state->limits.max_job_objects)
		return -EINVAL;
	for (i = 0; i < job->num_ibs; ++i) {
		struct amdgpu_ib *ib = &job->ibs[i];
		struct drm_suballoc *sa = ib->sa_bo;

		if (!sa || !ib->ptr || !ib->length_dw || sa->fence ||
		    sa->eoffset <= sa->soffset)
			return -EINVAL;
		for (j = 0; j < i; ++j) {
			struct drm_suballoc *other = job->ibs[j].sa_bo;

			if (sa->manager == other->manager &&
			    sa->soffset < other->eoffset &&
			    other->soffset < sa->eoffset)
				return -EINVAL;
		}
		for (pool = 0; pool < AMDGPU_IB_POOL_MAX; ++pool)
			if (sa->manager == &adev->ib_pools[pool].base)
				break;
		if (pool == AMDGPU_IB_POOL_MAX || !adev->ib_pools[pool].bo ||
		    !adev->ib_pools[pool].bo->tbo.pin_count ||
		    sa->eoffset > sa->manager->size ||
		    ib->ptr != amdgpu_sa_bo_cpu_addr(sa))
			return -EOPNOTSUPP;
		span = drm_suballoc_size(sa);
		payload = (u64)ib->length_dw * sizeof(u32);
		if (payload > span ||
		    check_add_overflow(lease->ib_bytes, span, &lease->ib_bytes) ||
		    lease->ib_bytes > lease->state->limits.max_job_ib_bytes)
			return -E2BIG;
	}
	lease->bytes = lease->ib_bytes;
	lease->objects = job->num_ibs;
	return 0;
}

static int ps4_uvd_mapping(struct amdgpu_bo_list_entry *entry)
{
	struct amdgpu_bo *bo = entry->bo;
	struct amdgpu_bo_va *va = entry->bo_va;
	struct amdgpu_bo_va_mapping *map, *only = NULL;
	u64 pages, bytes;
	u32 count = 0;

	if (!va || va->base.bo != bo || va->is_xgmi ||
	    va->queue_refcount || va->userq_va_mapped)
		return -EOPNOTSUPP;
	list_for_each_entry(map, &va->valids, list) {
		only = map;
		if (++count > 1)
			return -EOPNOTSUPP;
	}
	list_for_each_entry(map, &va->invalids, list) {
		only = map;
		if (++count > 1)
			return -EOPNOTSUPP;
	}
	/* One complete local read/write mapping; pending PTE installation is
	 * allowed because ordinary CS vm_handling follows this admission.
	 */
	if (count != 1 || only->bo_va != va || only->offset ||
	    only->last < only->start ||
	    only->last > U64_MAX / AMDGPU_GPU_PAGE_SIZE ||
	    (only->flags & AMDGPU_VM_PAGE_PRT) ||
	    (only->flags & (AMDGPU_VM_PAGE_READABLE | AMDGPU_VM_PAGE_WRITEABLE)) !=
	    (AMDGPU_VM_PAGE_READABLE | AMDGPU_VM_PAGE_WRITEABLE))
		return -EOPNOTSUPP;
	pages = only->last - only->start + 1;
	if (check_mul_overflow(pages, (u64)AMDGPU_GPU_PAGE_SIZE, &bytes) ||
	    bytes != amdgpu_bo_size(bo))
		return -EOPNOTSUPP;
	return 0;
}

static int ps4_uvd_charge(struct amdgpu_ps4_uvd_lease *lease)
{
	struct amdgpu_ps4_uvd_jobs *state = lease->state;
	unsigned long flags;
	int r = 0;

	spin_lock_irqsave(&state->lock, flags);
	if (state->closed)
		r = -ESHUTDOWN;
	else if (lease->bytes > state->limits.max_job_bytes ||
		 lease->objects > state->limits.max_job_objects ||
		 state->jobs >= state->limits.max_jobs ||
		 lease->bytes > state->limits.max_bytes - state->bytes ||
		 lease->objects > state->limits.max_objects - state->objects)
		r = -ENOSPC;
	else {
		state->bytes += lease->bytes;
		state->objects += lease->objects;
		++state->jobs;
		list_add_tail(&lease->link, &state->leases);
	}
	spin_unlock_irqrestore(&state->lock, flags);
	return r;
}

int amdgpu_ps4_uvd_jobs_admit(struct amdgpu_ps4_uvd_jobs *state,
	struct amdgpu_cs_parser *parser, struct amdgpu_job *job)
{
	struct amdgpu_ps4_uvd_lease *lease;
	struct amdgpu_bo_list_entry *entry;
	u32 i;
	int r;

	if (!state)
		return 0;
	if (!state->initialized || !parser || !job || job->ps4_uvd_lease ||
	    parser->adev != state->ring->adev || parser->gang_size != 1 ||
	    parser->jobs[0] != job || amdgpu_job_ring(job) != state->ring ||
	    !parser->bo_list || parser->uf_bo || job->gang_submit ||
	    parser->bo_list->gds_obj || parser->bo_list->gws_obj ||
	    parser->bo_list->oa_obj || !parser->bo_list->num_entries ||
	    parser->bo_list->num_entries > state->limits.max_job_objects)
		return -EOPNOTSUPP;
	lockdep_assert_held(&parser->bo_list->bo_list_mutex);
	lease = kzalloc(sizeof(*lease), GFP_KERNEL);
	if (!lease)
		return -ENOMEM;
	lease->state = state;
	lease->job = job;
	r = ps4_uvd_ibs(lease);
	if (r)
		goto fail;
	amdgpu_bo_list_for_each_entry(entry, parser->bo_list) {
		struct amdgpu_bo *bo = entry->bo;
		u64 bytes;

		if (!bo || bo->parent || bo->kfd_bo || !bo->tbo.resource ||
		    bo->tbo.type != ttm_bo_type_device ||
		    drm_gem_is_imported(&bo->tbo.base) ||
		    amdgpu_ttm_tt_is_userptr(bo->tbo.ttm) ||
		    amdgpu_bo_encrypted(bo) ||
		    (bo->tbo.resource->mem_type != TTM_PL_VRAM &&
		     bo->tbo.resource->mem_type != TTM_PL_TT) ||
		    dma_resv_locking_ctx(bo->tbo.base.resv) !=
		    drm_exec_ticket(&parser->exec)) {
			r = -EOPNOTSUPP;
			goto fail;
		}
		r = ps4_uvd_mapping(entry);
		if (r)
			goto fail;
		for (i = 0; i < lease->bo_count; ++i)
			if (lease->bos[i] == bo)
				break;
		if (i != lease->bo_count)
			continue;
		bytes = amdgpu_bo_size(bo);
		if (!bytes || lease->bo_count == AMDGPU_PS4_UVD_JOB_MAX_BOS ||
		    check_add_overflow(lease->bytes, bytes, &lease->bytes)) {
			r = -E2BIG;
			goto fail;
		}
		lease->bos[lease->bo_count++] = bo;
		++lease->objects;
	}
	r = ps4_uvd_charge(lease);
	if (r)
		goto fail;
	for (i = 0; i < lease->bo_count; ++i) {
		struct amdgpu_bo *bo = lease->bos[i];

		/* Parser lookup would otherwise migrate noncontiguous VRAM
		 * after admission. Establish that placement BEFORE pinning.
		 */
		bo->flags |= AMDGPU_GEM_CREATE_VRAM_CONTIGUOUS;
		r = amdgpu_ttm_alloc_gart(&bo->tbo);
		if (r)
			goto fail_pinned;
		r = amdgpu_bo_pin(bo, amdgpu_mem_type_to_domain(bo->tbo.resource->mem_type));
		if (r)
			goto fail_pinned;
		lease->bos[i] = amdgpu_bo_ref(bo);
		++lease->pinned;
	}
	job->ps4_uvd_lease = lease;
	return 0;

fail_pinned:
	for (i = 0; i < lease->pinned; ++i) {
		amdgpu_bo_unpin(lease->bos[i]);
		amdgpu_bo_unref(&lease->bos[i]);
	}
	ps4_uvd_uncharge(lease);
fail:
	kfree(lease);
	return r;
}

int amdgpu_ps4_uvd_jobs_admit_kernel(struct amdgpu_ps4_uvd_jobs *state,
	struct amdgpu_job *job)
{
	struct amdgpu_ps4_uvd_lease *lease;
	int r;

	if (!state)
		return 0;
	if (!state->initialized || !job || job->ps4_uvd_lease ||
	    job->vm || job->gang_submit ||
	    (job->base.entity ? amdgpu_job_ring(job) :
	     to_amdgpu_ring(job->base.sched)) != state->ring)
		return -EOPNOTSUPP;
	lease = kzalloc(sizeof(*lease), GFP_KERNEL);
	if (!lease)
		return -ENOMEM;
	lease->state = state;
	lease->job = job;
	r = ps4_uvd_ibs(lease);
	if (!r)
		r = ps4_uvd_charge(lease);
	if (r) {
		kfree(lease);
		return r;
	}
	job->ps4_uvd_lease = lease;
	return 0;
}

int amdgpu_ps4_uvd_jobs_admit_kernel_message(struct amdgpu_ps4_uvd_jobs *state,
	struct amdgpu_job *job, struct amdgpu_bo *bo)
{
	struct amdgpu_ps4_uvd_lease *lease;
	int r;

	if (!state)
		return 0;
	if (!bo || !job || job->ps4_uvd_lease)
		return -EINVAL;
	/* send_msg has no parser reservations. The helper's original kernel
	 * pin keeps the physical address already written into the IB stable.
	 */
	r = amdgpu_bo_reserve(bo, true);
	if (r)
		return r;
	if (!bo->tbo.pin_count || !bo->tbo.resource || bo->parent ||
	    bo->kfd_bo || bo->tbo.type != ttm_bo_type_kernel ||
	    drm_gem_is_imported(&bo->tbo.base) || amdgpu_bo_encrypted(bo) ||
	    amdgpu_ttm_tt_is_userptr(bo->tbo.ttm) ||
	    (bo->tbo.resource->mem_type != TTM_PL_VRAM &&
	     bo->tbo.resource->mem_type != TTM_PL_TT)) {
		r = -EOPNOTSUPP;
		goto unlock;
	}
	r = amdgpu_ps4_uvd_jobs_admit_kernel(state, job);
	if (r)
		goto unlock;
	lease = job->ps4_uvd_lease;
	/* Extend the still-unpublished lease under the same explicit budget. */
	ps4_uvd_uncharge(lease);
	if (check_add_overflow(lease->bytes, (u64)amdgpu_bo_size(bo), &lease->bytes)) {
		r = -E2BIG;
		goto fail;
	}
	++lease->objects;
	lease->bo_count = 1;
	r = ps4_uvd_charge(lease);
	if (r)
		goto fail;
	r = amdgpu_bo_pin(bo, amdgpu_mem_type_to_domain(bo->tbo.resource->mem_type));
	if (r) {
		ps4_uvd_uncharge(lease);
		goto fail;
	}
	lease->bos[0] = amdgpu_bo_ref(bo);
	lease->pinned = 1;
	job->ps4_uvd_lease = lease;
	amdgpu_bo_unreserve(bo);
	return 0;
fail:
	kfree(lease);
unlock:
	amdgpu_bo_unreserve(bo);
	return r;
}

void amdgpu_ps4_uvd_jobs_abort_reserved(struct amdgpu_job *job)
{
	struct amdgpu_ps4_uvd_lease *lease = job->ps4_uvd_lease;
	u32 i;

	if (!lease)
		return;
	if (lease->entered || lease->free_requested) {
		amdgpu_ps4_uvd_jobs_fault(lease->state, -EIO);
		return;
	}
	for (i = 0; i < lease->pinned; ++i) {
		dma_resv_assert_held(lease->bos[i]->tbo.base.resv);
		amdgpu_bo_unpin(lease->bos[i]);
		amdgpu_bo_unref(&lease->bos[i]);
	}
	ps4_uvd_free_ibs(lease);
	ps4_uvd_uncharge(lease);
	kfree(lease);
}

bool amdgpu_ps4_uvd_jobs_owns_bo(const struct amdgpu_job *job,
	const struct amdgpu_bo *bo)
{
	const struct amdgpu_ps4_uvd_lease *lease = job->ps4_uvd_lease;
	u32 i;

	if (!lease)
		return true;
	for (i = 0; i < lease->pinned; ++i)
		if (lease->bos[i] == bo)
			return true;
	return false;
}

int amdgpu_ps4_uvd_jobs_enter(struct amdgpu_job *job)
{
	struct amdgpu_ps4_uvd_lease *lease = job->ps4_uvd_lease;
	struct amdgpu_ps4_uvd_jobs *state;
	unsigned long flags;
	int r = 0;

	if (!lease) {
		struct amdgpu_ring *ring = job->base.entity ? amdgpu_job_ring(job) :
			(job->base.sched ? to_amdgpu_ring(job->base.sched) : NULL);

		state = ring ? amdgpu_ps4_uvd_runtime_jobs(ring) : NULL;
		if (state) {
			amdgpu_ps4_uvd_jobs_fault(state, -EPERM);
			return -EPERM;
		}
		return 0;
	}
	state = lease->state;
	spin_lock_irqsave(&state->lock, flags);
	if (lease->entered)
		r = -EALREADY;
	else if (state->closed)
		r = -ESHUTDOWN;
	else
		lease->entered = true;
	spin_unlock_irqrestore(&state->lock, flags);
	if (r == -EALREADY)
		amdgpu_ps4_uvd_jobs_fault(state, r);
	return r;
}

static void ps4_uvd_hw_signaled(struct dma_fence *fence, struct dma_fence_cb *cb)
{
	struct amdgpu_ps4_uvd_lease *lease =
		container_of(cb, struct amdgpu_ps4_uvd_lease, hw_cb);

	if (READ_ONCE(fence->error) < 0)
		amdgpu_ps4_uvd_jobs_fault(lease->state, READ_ONCE(fence->error));
	schedule_work(&lease->state->reap_work);
}

void amdgpu_ps4_uvd_jobs_submitted(struct amdgpu_job *job,
	struct dma_fence *actual_hw_fence, int schedule_error)
{
	struct amdgpu_ps4_uvd_lease *lease = job->ps4_uvd_lease;
	int r;

	if (!lease)
		return;
	if (!lease->entered || lease->hw || schedule_error ||
	    !actual_hw_fence || IS_ERR(actual_hw_fence) ||
	    actual_hw_fence->context != lease->state->fence_context ||
	    !actual_hw_fence->seqno) {
		amdgpu_ps4_uvd_jobs_fault(lease->state,
			schedule_error < 0 ? schedule_error : -EIO);
		return;
	}
	lease->hw = dma_fence_get(actual_hw_fence);
	r = dma_fence_add_callback(lease->hw, &lease->hw_cb, ps4_uvd_hw_signaled);
	if (r && r != -ENOENT)
		amdgpu_ps4_uvd_jobs_fault(lease->state, r);
	if (r == -ENOENT && dma_fence_get_status(lease->hw) < 0)
		amdgpu_ps4_uvd_jobs_fault(lease->state, dma_fence_get_status(lease->hw));
	schedule_work(&lease->state->reap_work);
}

void amdgpu_ps4_uvd_jobs_observe_hw(struct amdgpu_ps4_uvd_jobs *state,
	u64 fence_context, u64 completed, u64 emitted)
{
	unsigned long flags;
	bool bad;

	if (!state || !state->initialized)
		return;
	spin_lock_irqsave(&state->lock, flags);
	bad = fence_context != state->fence_context || completed > emitted ||
		completed < state->hw_completed;
	if (!bad && !state->closed)
		state->hw_completed = completed;
	spin_unlock_irqrestore(&state->lock, flags);
	if (bad)
		amdgpu_ps4_uvd_jobs_fault(state, -EIO);
	else
		schedule_work(&state->reap_work);
}

bool amdgpu_ps4_uvd_jobs_defer_resources(const struct amdgpu_job *job)
{
	return job->ps4_uvd_lease != NULL;
}

bool amdgpu_ps4_uvd_jobs_job_free(struct amdgpu_job *job, bool scheduler_free)
{
	struct amdgpu_ps4_uvd_lease *lease = job->ps4_uvd_lease;
	unsigned long flags;

	if (!lease)
		return true;
	if (job->base.s_fence &&
	    READ_ONCE(job->base.s_fence->finished.error) < 0)
		amdgpu_ps4_uvd_jobs_fault(lease->state,
			READ_ONCE(job->base.s_fence->finished.error));
	spin_lock_irqsave(&lease->state->lock, flags);
	lease->free_requested = true;
	lease->scheduler_free |= scheduler_free;
	spin_unlock_irqrestore(&lease->state->lock, flags);
	schedule_work(&lease->state->reap_work);
	return false;
}
