// SPDX-License-Identifier: MIT
/*
 * Copyright 2014 Advanced Micro Devices, Inc.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the "Software"),
 * to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense,
 * and/or sell copies of the Software, and to permit persons to whom the
 * Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
 * THE COPYRIGHT HOLDER(S) OR AUTHOR(S) BE LIABLE FOR ANY CLAIM, DAMAGES OR
 * OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
 * ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
 * OTHER DEALINGS IN THE SOFTWARE.
 *
 * Authors: Christian König <christian.koenig@amd.com>
 */

/* Experimental Liverpool backend for the normal AMDGPU UVD userspace ABI.
 * Startup reuses the source05 sequence exercised by EXP-20261020-005.
 * Packet callbacks adapt Linux uvd_v5_0.c (Linux MIT notices retained in the
 * corresponding-source package). Runtime mapping/coexistence remain untested.
 */
#include <linux/delay.h>
#include <linux/ktime.h>
#include <linux/moduleparam.h>
#include <linux/wait.h>
#include "amdgpu.h"
#include "amdgpu_ps4_uvd_runtime.h"
#include "amdgpu_reset.h"
#include "vid.h"
#include "uvd/uvd_5_0_d.h"
#include "uvd/uvd_5_0_sh_mask.h"
#include "gmc/gmc_7_1_d.h"

static bool ps4_uvd_runtime;
module_param_named(ps4_uvd_runtime, ps4_uvd_runtime, bool, 0444);
MODULE_PARM_DESC(ps4_uvd_runtime,
	"Experimental Liverpool UVD userspace queue; cold boot only, retains DMA on failure (default 0)");

static const struct amdgpu_ring_funcs ps4_uvd_ring_funcs;
static DECLARE_WAIT_QUEUE_HEAD(ps4_uvd_retained_wait);
static atomic_t ps4_uvd_runtime_ib_test_attempted = ATOMIC_INIT(0);

bool amdgpu_ps4_uvd_runtime_requested(void)
{
	return ps4_uvd_runtime;
}

bool amdgpu_ps4_uvd_runtime_enabled(struct amdgpu_device *adev)
{
	return ps4_uvd_runtime && IS_BUILTIN(CONFIG_DRM_AMDGPU) &&
		IS_ENABLED(CONFIG_X86_PS4) && adev->asic_type == CHIP_LIVERPOOL;
}

bool amdgpu_ps4_uvd_runtime_ring(struct amdgpu_ring *ring)
{
	return ring && ring->adev && amdgpu_ps4_uvd_runtime_enabled(ring->adev) &&
		ring->funcs == &ps4_uvd_ring_funcs;
}

bool amdgpu_ps4_uvd_runtime_retain(struct amdgpu_device *adev)
{
	return amdgpu_ps4_uvd_runtime_enabled(adev) &&
		READ_ONCE(adev->ps4_uvd_runtime.memory.entered);
}

bool amdgpu_ps4_uvd_runtime_reserve(struct amdgpu_device *adev,
	unsigned int vmhub, unsigned int vmid)
{
	if (!amdgpu_ps4_uvd_runtime_enabled(adev) || vmhub || vmid != 4)
		return false;
	adev->ps4_uvd_runtime.vmid_reserved = true;
	return true;
}

struct amdgpu_ps4_uvd_jobs *
amdgpu_ps4_uvd_runtime_jobs(struct amdgpu_ring *ring)
{
	if (!amdgpu_ps4_uvd_runtime_ring(ring) ||
	    !READ_ONCE(ring->adev->ps4_uvd_runtime.jobs.initialized))
		return NULL;
	return &ring->adev->ps4_uvd_runtime.jobs;
}

static void ps4_uvd_close(void *arg, int error)
{
	struct amdgpu_device *adev = arg;
	struct amdgpu_ps4_uvd_runtime *s = &adev->ps4_uvd_runtime;
	unsigned long flags;
	bool first;

	spin_lock_irqsave(&s->publish_lock, flags);
	first = !atomic_cmpxchg(&s->failed, 0, error ? error : -EIO);
	spin_unlock_irqrestore(&s->publish_lock, flags);
	if (!first)
		return;
	WRITE_ONCE(adev->uvd.inst[0].ring.sched.ready, false);
	if (s->poll_initialized)
		cancel_delayed_work(&s->poll_work);
	dev_err(adev->dev,
		"PS4_UVD_RUNTIME CLOSED error=%d; no reset/rearm/free, full poweroff required\n",
		error);
}

void amdgpu_ps4_uvd_runtime_fault(struct amdgpu_device *adev, int error)
{
	struct amdgpu_ps4_uvd_runtime *s = &adev->ps4_uvd_runtime;

	if (!amdgpu_ps4_uvd_runtime_enabled(adev))
		return;
	ps4_uvd_close(adev, error);
	if (s->jobs.initialized)
		amdgpu_ps4_uvd_jobs_fault(&s->jobs, error);
}

void amdgpu_ps4_uvd_runtime_park(struct amdgpu_device *adev, const char *reason)
{
	amdgpu_ps4_uvd_runtime_fault(adev, -ESHUTDOWN);
	dev_err(adev->dev,
		"PS4_UVD_RUNTIME RETAINED reason=%s; cleanup blocked, full poweroff required\n",
		reason);
	for (;;)
		wait_event_idle(ps4_uvd_retained_wait, false);
}

/* File-close holds session_lock, so no later user CS can be accepted while
 * this checks every earlier lease, including a failed entity flush. Zero
 * means all kernel IB spans/BO leases retired with trusted completion (or a
 * never-published rollback). A closed state never authorizes DESTROY.
 */
int amdgpu_ps4_uvd_runtime_drain_jobs(struct amdgpu_device *adev)
{
	struct amdgpu_ps4_uvd_runtime *s = &adev->ps4_uvd_runtime;
	u64 deadline = ktime_get_ns() + 2100ULL * NSEC_PER_MSEC;
	unsigned long flags;
	bool closed, idle;

	lockdep_assert_held(&s->session_lock);
	for (;;) {
		spin_lock_irqsave(&s->jobs.lock, flags);
		closed = s->jobs.closed;
		idle = !s->jobs.jobs;
		spin_unlock_irqrestore(&s->jobs.lock, flags);
		if (closed || atomic_read(&s->failed))
			return -ESHUTDOWN;
		if (idle)
			return 0;
		if (ktime_get_ns() >= deadline) {
			amdgpu_ps4_uvd_runtime_fault(adev, -ETIMEDOUT);
			return -ETIMEDOUT;
		}
		usleep_range(1000, 2000);
	}
}

static bool ps4_uvd_history_ok(struct amdgpu_device *adev)
{
	const struct ps4_uvd_fault_snapshot *base = &adev->ps4_uvd_runtime.fault_baseline;
	struct ps4_uvd_fault_snapshot latest;
	unsigned int i;

	amdgpu_ps4_uvd_irq_fault_snapshot(adev, &latest);
	if (!base->active || !base->arm_ns || !latest.active ||
	    latest.arm_ns != base->arm_ns || latest.history.total.saturated ||
	    latest.history.unclassified_status.saturated ||
	    latest.history.total.arrivals || latest.history.unclassified_status.arrivals)
		return false;
	for (i = 0; i < PS4_UVD_FAULT_VMIDS; ++i)
		if (latest.history.vmid[i].saturated || latest.history.vmid[i].arrivals)
			return false;
	return true;
}

/* Called after the actual WB read and before core signals its fence slots.
 * Core force-write/completion paths must instead close this backend.
 */
bool amdgpu_ps4_uvd_runtime_observe(struct amdgpu_ring *ring, u32 seq)
{
	struct amdgpu_ps4_uvd_runtime *s;
	struct amdgpu_device *adev;
	u32 emitted;

	if (!amdgpu_ps4_uvd_runtime_ring(ring))
		return true;
	adev = ring->adev;
	s = &ring->adev->ps4_uvd_runtime;
	if (atomic_read(&s->failed) || !s->jobs.initialized)
		return false;
	emitted = READ_ONCE(ring->fence_drv.sync_seq);
	/* First development policy deliberately rejects counter wrap. */
	if (seq > emitted || seq < READ_ONCE(s->jobs.hw_completed) ||
	    RREG32(mmVM_CONTEXT1_PROTECTION_FAULT_STATUS) ||
	    !ps4_uvd_history_ok(ring->adev) ||
	    atomic_read(&ring->adev->gpu_reset_counter) != s->baseline_reset_count ||
	    amdgpu_in_reset(ring->adev)) {
		amdgpu_ps4_uvd_runtime_fault(ring->adev, -EIO);
		return false;
	}
	amdgpu_ps4_uvd_jobs_observe_hw(&s->jobs,
		s->jobs.fence_context, seq, emitted);
	return !atomic_read(&s->failed);
}

int amdgpu_ps4_uvd_runtime_before_submit(struct amdgpu_ring *ring)
{
	struct amdgpu_ps4_uvd_runtime *s;
	int r;

	if (!amdgpu_ps4_uvd_runtime_ring(ring))
		return 0;
	s = &ring->adev->ps4_uvd_runtime;
	if (!READ_ONCE(s->started) || atomic_read(&s->failed))
		return -EIO;
	if (READ_ONCE(ring->fence_drv.sync_seq) == U32_MAX) {
		amdgpu_ps4_uvd_runtime_fault(ring->adev, -EOVERFLOW);
		return -EOVERFLOW;
	}
	if (!ps4_uvd_history_ok(ring->adev)) {
		amdgpu_ps4_uvd_runtime_fault(ring->adev, -EIO);
		return -EIO;
	}
	/* This ring has one hardware submission credit. Never change its TLB
	 * while an earlier UVD job remains outstanding.
	 */
	if (atomic_read(&ring->fence_drv.last_seq) !=
	    READ_ONCE(ring->fence_drv.sync_seq))
		return -EBUSY;
	r = amdgpu_ps4_uvd_memory_refresh(ring->adev, &s->memory);
	if (r)
		amdgpu_ps4_uvd_runtime_fault(ring->adev, r);
	return r;
}

static void ps4_uvd_poll(struct work_struct *work)
{
	struct amdgpu_ps4_uvd_runtime *s = container_of(to_delayed_work(work),
		struct amdgpu_ps4_uvd_runtime, poll_work);
	struct amdgpu_device *adev = container_of(s, struct amdgpu_device,
		ps4_uvd_runtime);
	struct amdgpu_ring *ring = &adev->uvd.inst[0].ring;

	if (!READ_ONCE(s->started) || atomic_read(&s->failed))
		return;
	if (amdgpu_in_reset(adev) ||
	    atomic_read(&adev->gpu_reset_counter) != s->baseline_reset_count ||
	    RREG32(mmVM_CONTEXT1_PROTECTION_FAULT_STATUS) || !ps4_uvd_history_ok(adev)) {
		amdgpu_ps4_uvd_runtime_fault(adev, -EIO);
		return;
	}
	amdgpu_fence_process(ring);
	if (!atomic_read(&s->failed) &&
	    atomic_read(&ring->fence_drv.last_seq) != READ_ONCE(ring->fence_drv.sync_seq)) {
		if (ktime_get_ns() - READ_ONCE(s->last_publish_ns) > 2 * NSEC_PER_SEC) {
			amdgpu_ps4_uvd_runtime_fault(adev, -ETIMEDOUT);
			return;
		}
		mod_delayed_work(system_wq, &s->poll_work, msecs_to_jiffies(1));
	}
}

static u64 ps4_uvd_get_rptr(struct amdgpu_ring *ring)
{
	struct amdgpu_device *adev = ring->adev;
	return RREG32(mmUVD_RBC_RB_RPTR);
}

static u64 ps4_uvd_get_wptr(struct amdgpu_ring *ring)
{
	struct amdgpu_device *adev = ring->adev;
	return RREG32(mmUVD_RBC_RB_WPTR);
}

static void ps4_uvd_set_wptr(struct amdgpu_ring *ring)
{
	struct amdgpu_device *adev = ring->adev;
	struct amdgpu_ps4_uvd_runtime *s = &adev->ps4_uvd_runtime;
	unsigned long flags;

	spin_lock_irqsave(&s->publish_lock, flags);
	if (atomic_read(&s->failed)) {
		spin_unlock_irqrestore(&s->publish_lock, flags);
		return;
	}
	mb();
	WRITE_ONCE(s->last_publish_ns, ktime_get_ns());
	WREG32(mmUVD_RBC_RB_WPTR, lower_32_bits(ring->wptr));
	spin_unlock_irqrestore(&s->publish_lock, flags);
	mod_delayed_work(system_wq, &s->poll_work, msecs_to_jiffies(1));
}

static void ps4_uvd_emit_ib(struct amdgpu_ring *ring,
	struct amdgpu_job *job, struct amdgpu_ib *ib, u32 flags)
{
	amdgpu_ring_write(ring, PACKET0(mmUVD_LMI_RBC_IB_64BIT_BAR_LOW, 0));
	amdgpu_ring_write(ring, lower_32_bits(ib->gpu_addr));
	amdgpu_ring_write(ring, PACKET0(mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH, 0));
	amdgpu_ring_write(ring, upper_32_bits(ib->gpu_addr));
	amdgpu_ring_write(ring, PACKET0(mmUVD_RBC_IB_SIZE, 0));
	amdgpu_ring_write(ring, ib->length_dw);
}

static void ps4_uvd_emit_fence(struct amdgpu_ring *ring, u64 addr,
	u64 seq, unsigned int flags)
{
	WARN_ON(flags & AMDGPU_FENCE_FLAG_64BIT);
	amdgpu_ring_write(ring, PACKET0(mmUVD_CONTEXT_ID, 0));
	amdgpu_ring_write(ring, lower_32_bits(seq));
	amdgpu_ring_write(ring, PACKET0(mmUVD_GPCOM_VCPU_DATA0, 0));
	amdgpu_ring_write(ring, lower_32_bits(addr));
	amdgpu_ring_write(ring, PACKET0(mmUVD_GPCOM_VCPU_DATA1, 0));
	amdgpu_ring_write(ring, upper_32_bits(addr) & 0xff);
	amdgpu_ring_write(ring, PACKET0(mmUVD_GPCOM_VCPU_CMD, 0));
	amdgpu_ring_write(ring, 0);
	/* Completion is polled; omit CMD=2 trap and all stock idle/PM work. */
}

static void ps4_uvd_insert_nop(struct amdgpu_ring *ring, u32 count)
{
	u32 i;

	WARN_ON(ring->wptr % 2 || count % 2);
	for (i = 0; i < count / 2; ++i) {
		amdgpu_ring_write(ring, PACKET0(mmUVD_NO_OP, 0));
		amdgpu_ring_write(ring, 0);
	}
}

static int ps4_uvd_test_ring(struct amdgpu_ring *ring)
{
	struct amdgpu_device *adev = ring->adev;
	u64 deadline = ktime_get_ns() + 250 * NSEC_PER_MSEC;
	int r;

	WREG32(mmUVD_CONTEXT_ID, 0x52544230);
	r = amdgpu_ring_alloc(ring, 2);
	if (r)
		return r;
	amdgpu_ring_write(ring, PACKET0(mmUVD_CONTEXT_ID, 0));
	amdgpu_ring_write(ring, 0x52545431);
	amdgpu_ring_commit(ring);
	while (ktime_get_ns() < deadline) {
		if (RREG32(mmUVD_CONTEXT_ID) == 0x52545431 &&
		    RREG32(mmUVD_RBC_RB_RPTR) == lower_32_bits(ring->wptr))
			return 0;
		usleep_range(100, 200);
	}
	return -ETIMEDOUT;
}

static int ps4_uvd_test_ib(struct amdgpu_ring *ring, long timeout)
{
	int r;

	if (atomic_cmpxchg(&ps4_uvd_runtime_ib_test_attempted, 0, 1))
		return -EPERM;
	if (atomic_read(&ring->adev->ps4_uvd_runtime.failed))
		return -EIO;
	r = amdgpu_uvd_ring_test_ib(ring, timeout);
	if (r)
		amdgpu_ps4_uvd_runtime_fault(ring->adev, r);
	return r;
}

static int ps4_uvd_early_init(struct amdgpu_ip_block *ip_block)
{
	struct amdgpu_device *adev = ip_block->adev;

	if (!amdgpu_ps4_uvd_runtime_enabled(adev) ||
	    !amdgpu_ps4_uvd_runtime_options_valid(adev))
		return -ENOENT;
	adev->uvd.num_uvd_inst = 1;
	adev->uvd.inst[0].ring.funcs = &ps4_uvd_ring_funcs;
	return 0;
}

static int ps4_uvd_sw_init(struct amdgpu_ip_block *ip_block)
{
	struct amdgpu_device *adev = ip_block->adev;
	struct amdgpu_ps4_uvd_runtime *s = &adev->ps4_uvd_runtime;
	struct amdgpu_ring *ring = &adev->uvd.inst[0].ring;
	const struct amdgpu_ps4_uvd_jobs_limits limits = {
		.max_bytes = 256ULL << 20, .max_job_bytes = 128ULL << 20,
		.max_job_ib_bytes = 1ULL << 20, .max_objects = 512,
		.max_job_objects = 128, .max_jobs = 32,
	};
	int r;

	atomic_set(&s->failed, 0);
	spin_lock_init(&s->observe_lock);
	spin_lock_init(&s->publish_lock);
	mutex_init(&s->session_lock);
	INIT_DELAYED_WORK(&s->poll_work, ps4_uvd_poll);
	s->poll_initialized = true;
	r = amdgpu_uvd_sw_init(adev);
	if (r)
		return r;
	adev->uvd.use_ctx_buf = false;
	r = amdgpu_bo_create_kernel(adev, PAGE_SIZE, PAGE_SIZE,
		AMDGPU_GEM_DOMAIN_GTT, &s->scratch_bo,
		&s->scratch_gpu_addr, &s->scratch_cpu);
	if (r)
		return r;
	snprintf(ring->name, sizeof(ring->name), "ps4_uvd");
	/* Parent ring_init hook selects one hardware credit, yielding 4KiB. */
	r = amdgpu_ring_init(adev, ring, 1024, NULL, 0,
		AMDGPU_RING_PRIO_DEFAULT, NULL);
	if (r)
		return r;
	if (ring->ring_size != 4096)
		return -EINVAL;
	s->native_ring_gpu_addr = ring->gpu_addr;
	r = amdgpu_ps4_uvd_jobs_init(&s->jobs, ring,
		adev->fence_context + ring->idx, &limits, ps4_uvd_close, adev);
	if (r)
		return r;
	s->software_ready = true;
	return 0;
}

static int ps4_uvd_hw_init(struct amdgpu_ip_block *ip_block)
{
	struct amdgpu_device *adev = ip_block->adev;
	struct amdgpu_ps4_uvd_runtime *s = &adev->ps4_uvd_runtime;
	struct amdgpu_ring *ring = &adev->uvd.inst[0].ring;
	u32 i;
	int r;

	if (!s->software_ready || s->memory.entered || s->started)
		return -EPERM;
	r = amdgpu_ps4_uvd_memory_prepare(adev, &s->memory, s->vmid_reserved);
	if (r)
		return r;
	r = amdgpu_ps4_uvd_memory_map_private_gtt(adev, &s->memory,
		adev->uvd.inst[0].vcpu_bo, adev->uvd.inst[0].gpu_addr,
		AMDGPU_PS4_UVD_BACKING_BYTES, AMDGPU_PS4_UVD_BACKING_VA);
	if (r)
		return r;
	r = amdgpu_ps4_uvd_memory_map_private_gtt(adev, &s->memory,
		ring->ring_obj, s->native_ring_gpu_addr,
		ring->ring_size, AMDGPU_PS4_UVD_RING_VA);
	if (r)
		return r;
	/* Internal firmware/ring/messages and IB-pool backing must stay pinned
	 * for device lifetime; job leases separately preserve individual spans.
	 */
	r = amdgpu_ps4_uvd_memory_map_private_gtt(adev, &s->memory,
		s->scratch_bo, s->scratch_gpu_addr, PAGE_SIZE,
		AMDGPU_PS4_UVD_PRIVATE_VA + 0x30a000);
	if (r)
		return r;
	r = amdgpu_ps4_uvd_memory_install(adev, &s->memory);
	if (r)
		goto retained_failure;
	ring->gpu_addr = AMDGPU_PS4_UVD_RING_VA;
	ring->wptr = 0;
	for (i = 0; i < ring->ring_size / sizeof(u32); i += 2) {
		ring->ring[i] = PACKET0(mmUVD_NO_OP, 0);
		ring->ring[i + 1] = 0;
	}
	r = amdgpu_ps4_uvd_runtime_startup(adev, adev->uvd.inst[0].cpu_addr,
		AMDGPU_PS4_UVD_BACKING_BYTES, s->memory.pd.gpu_addr);
	if (r)
		goto retained_failure;
	s->baseline_reset_count = atomic_read(&adev->gpu_reset_counter);
	amdgpu_ps4_uvd_irq_fault_snapshot(adev, &s->fault_baseline);
	if (!ps4_uvd_history_ok(adev)) {
		r = -EIO;
		goto retained_failure;
	}
	WRITE_ONCE(s->started, true);
	r = amdgpu_ring_test_helper(ring);
	if (r)
		goto retained_failure;
	dev_info(adev->dev,
		"PS4_UVD_RUNTIME QUEUE_READY ring=%u credits=1 AVC experimental; normal userspace submission enabled\n",
		ring->idx);
	return 0;

retained_failure:
	amdgpu_ps4_uvd_runtime_fault(adev, r);
	return r;
}

static int ps4_uvd_hw_fini(struct amdgpu_ip_block *ip_block)
{
	if (amdgpu_ps4_uvd_runtime_retain(ip_block->adev)) {
		amdgpu_ps4_uvd_runtime_fault(ip_block->adev, -ESHUTDOWN);
		return -EBUSY;
	}
	return 0;
}

static int ps4_uvd_sw_fini(struct amdgpu_ip_block *ip_block)
{
	struct amdgpu_device *adev = ip_block->adev;
	int r;

	if (amdgpu_ps4_uvd_runtime_retain(adev))
		return -EBUSY;
	r = amdgpu_ps4_uvd_memory_cleanup_pre_entry(adev, &adev->ps4_uvd_runtime.memory);
	if (r)
		return r;
	if (adev->ps4_uvd_runtime.poll_initialized)
		cancel_delayed_work_sync(&adev->ps4_uvd_runtime.poll_work);
	amdgpu_bo_free_kernel(&adev->ps4_uvd_runtime.scratch_bo,
		&adev->ps4_uvd_runtime.scratch_gpu_addr,
		&adev->ps4_uvd_runtime.scratch_cpu);
	return amdgpu_uvd_sw_fini(adev);
}

static int ps4_uvd_no_suspend(struct amdgpu_ip_block *ip_block)
{
	return -EBUSY;
}

static bool ps4_uvd_is_idle(struct amdgpu_ip_block *ip_block)
{
	struct amdgpu_ring *ring = &ip_block->adev->uvd.inst[0].ring;
	return !amdgpu_fence_count_emitted(ring);
}

static const struct amd_ip_funcs ps4_uvd_ip_funcs = {
	.name = "ps4_uvd",
	.early_init = ps4_uvd_early_init,
	.sw_init = ps4_uvd_sw_init,
	.sw_fini = ps4_uvd_sw_fini,
	.hw_init = ps4_uvd_hw_init,
	.hw_fini = ps4_uvd_hw_fini,
	.prepare_suspend = ps4_uvd_no_suspend,
	.suspend = ps4_uvd_no_suspend,
	.resume = ps4_uvd_no_suspend,
	.is_idle = ps4_uvd_is_idle,
};

static const struct amdgpu_ring_funcs ps4_uvd_ring_funcs = {
	.type = AMDGPU_RING_TYPE_UVD,
	.align_mask = 0xf,
	.nop = 0x80000000,
	.support_64bit_ptrs = false,
	.no_user_fence = true,
	.get_rptr = ps4_uvd_get_rptr,
	.get_wptr = ps4_uvd_get_wptr,
	.set_wptr = ps4_uvd_set_wptr,
	.parse_cs = amdgpu_uvd_ring_parse_cs,
	.emit_frame_size = 8,
	.emit_ib_size = 6,
	.emit_ib = ps4_uvd_emit_ib,
	.emit_fence = ps4_uvd_emit_fence,
	.test_ring = ps4_uvd_test_ring,
	.test_ib = ps4_uvd_test_ib,
	.insert_nop = ps4_uvd_insert_nop,
	.pad_ib = amdgpu_ring_generic_pad_ib,
};

const struct amdgpu_ip_block_version ps4_uvd_ip_block = {
	.type = AMD_IP_BLOCK_TYPE_UVD,
	.major = 4, .minor = 2, .rev = 0,
	.funcs = &ps4_uvd_ip_funcs,
};
