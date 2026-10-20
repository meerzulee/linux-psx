/* SPDX-License-Identifier: MIT */
#ifndef AMDGPU_PS4_UVD_RUNTIME_H
#define AMDGPU_PS4_UVD_RUNTIME_H

#include <linux/atomic.h>
#include <linux/mutex.h>
#include <linux/workqueue.h>
#include "amdgpu_ps4_uvd_memory.h"
#include "amdgpu_ps4_uvd_jobs.h"
#include "amdgpu_ps4_uvd_irq.h"

struct amdgpu_device;
struct amdgpu_ip_block_version;
struct amdgpu_ring;
struct amdgpu_bo;

#define AMDGPU_PS4_UVD_BACKING_BYTES 0x308000U
#define AMDGPU_PS4_UVD_BACKING_VA 0x300000000ULL
#define AMDGPU_PS4_UVD_RING_VA 0x300308000ULL

/* Embedded once in adev. Entered hardware has no rearm or teardown path in
 * this development backend. A fault closes admission and retains DMA memory.
 */
struct amdgpu_ps4_uvd_runtime {
	struct amdgpu_ps4_uvd_memory memory;
	struct amdgpu_ps4_uvd_jobs jobs;
	struct delayed_work poll_work;
	spinlock_t observe_lock;
	spinlock_t publish_lock;
	struct mutex session_lock;
	atomic_t failed;
	struct ps4_uvd_fault_snapshot fault_baseline;
	struct amdgpu_bo *scratch_bo;
	void *scratch_cpu;
	u64 scratch_gpu_addr;
	u64 native_ring_gpu_addr;
	u64 last_publish_ns;
	u32 baseline_reset_count;
	bool vmid_reserved;
	bool software_ready;
	bool started;
	bool poll_initialized;
};

bool amdgpu_ps4_uvd_runtime_requested(void);
bool amdgpu_ps4_uvd_runtime_options_valid(struct amdgpu_device *adev);
bool amdgpu_ps4_uvd_runtime_legacy_conflict(void);
bool amdgpu_ps4_uvd_runtime_enabled(struct amdgpu_device *adev);
bool amdgpu_ps4_uvd_runtime_ring(struct amdgpu_ring *ring);
bool amdgpu_ps4_uvd_runtime_retain(struct amdgpu_device *adev);
bool amdgpu_ps4_uvd_runtime_reserve(struct amdgpu_device *adev,
	unsigned int vmhub, unsigned int vmid);
struct amdgpu_ps4_uvd_jobs *
amdgpu_ps4_uvd_runtime_jobs(struct amdgpu_ring *ring);
void amdgpu_ps4_uvd_runtime_fault(struct amdgpu_device *adev, int error);
int amdgpu_ps4_uvd_runtime_drain_jobs(struct amdgpu_device *adev);
int amdgpu_ps4_uvd_runtime_before_submit(struct amdgpu_ring *ring);
bool amdgpu_ps4_uvd_runtime_observe(struct amdgpu_ring *ring, u32 seq);
void amdgpu_ps4_uvd_runtime_park(struct amdgpu_device *adev,
	const char *reason);

/* Implemented beside the proven readiness core, before ordinary producers.
 * Returns once after startup; failure never authorizes cleanup or retry.
 */
int amdgpu_ps4_uvd_runtime_startup(struct amdgpu_device *adev,
	void *backing, size_t bytes, u64 pd_addr);

extern const struct amdgpu_ip_block_version ps4_uvd_ip_block;
#endif
