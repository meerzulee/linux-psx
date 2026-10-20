/* SPDX-License-Identifier: MIT */
#ifndef AMDGPU_PS4_LIVE_PREPARE_H
#define AMDGPU_PS4_LIVE_PREPARE_H

#include <linux/spinlock.h>
#include <linux/types.h>

struct amdgpu_device;
struct ps4_canary_state;

enum ps4_live_prepare_stage {
	PS4_LIVE_DISABLED,
	PS4_LIVE_SELECTED,
	PS4_LIVE_RESERVED,
	PS4_LIVE_PREPARING,
	PS4_LIVE_PREPARED,
	PS4_LIVE_INVALIDATED,
	PS4_LIVE_RELEASING,
	PS4_LIVE_RELEASED,
	PS4_LIVE_FAILED,
};

enum ps4_live_prepare_event {
	PS4_LIVE_EVENT_NONE,
	PS4_LIVE_EVENT_GART_ENABLE,
	PS4_LIVE_EVENT_GART_DISABLE,
	PS4_LIVE_EVENT_RESET,
	PS4_LIVE_EVENT_SUSPEND,
	PS4_LIVE_EVENT_HW_FINI,
	PS4_LIVE_EVENT_VMID_FINI,
	PS4_LIVE_EVENT_REINIT,
};

/* Scalar history only: no BO, device, firmware or MMIO pointer escapes. */
struct ps4_live_prepare_snapshot {
	enum ps4_live_prepare_stage stage;
	enum ps4_live_prepare_event last_event;
	u64 observed_generation, proof_generation, proof_ns;
	u64 pd_addr, pd_bytes, pt_bytes, r1_bytes, rb_bytes, ib_bytes;
	u32 mapped_pages, copy_crc, proof_reset_counter;
	int error;
	bool selected, attempted, reservation_seen, allocator_alive;
	bool proof_passed, retained;
};

/* Lives with adev; only the finite pre-client path can attach an owner. */
struct amdgpu_ps4_live_prepare {
	spinlock_t lock;
	bool initialized;
	struct ps4_live_prepare_snapshot cached;
	/* Failed boot preparation remains parked with this retained owner. */
	struct ps4_canary_state *pending;
	/* Assigned only after completed SDMA proof, never to a UVD boot probe. */
	struct ps4_canary_state *owner;
};

bool amdgpu_ps4_live_prepare_enabled(struct amdgpu_device *adev);
void amdgpu_ps4_live_prepare_init(struct amdgpu_device *adev);
void amdgpu_ps4_live_prepare_reserved(struct amdgpu_device *adev);
void amdgpu_ps4_live_prepare_invalidate(struct amdgpu_device *adev,
				       enum ps4_live_prepare_event event);
void amdgpu_ps4_live_prepare_snapshot(struct amdgpu_device *adev,
				     struct ps4_live_prepare_snapshot *out);
/* Preparation-only destruction, after IP hw_fini and before TTM DMA clear.
 * This is NOT a released-UVD-engine cleanup or quiescence API.
 */
void amdgpu_ps4_live_prepare_fini(struct amdgpu_device *adev);

#endif
