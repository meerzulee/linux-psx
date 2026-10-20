/* LIVE_PREPARE_STATE_BEGIN */
static bool ps4_uvd_live_prepare;
module_param_named(ps4_uvd_live_prepare, ps4_uvd_live_prepare, bool, 0444);
MODULE_PARM_DESC(ps4_uvd_live_prepare,
	"Prepare an owned PS4 VMID4 mapping before clients, then continue boot; no UVD arm (default 0)");

/* A new adev after driver rebind is not permission to repeat this boot. */
static atomic_t ps4_live_prepare_attempted = ATOMIC_INIT(0);

bool amdgpu_ps4_live_prepare_enabled(struct amdgpu_device *adev)
{
	return ps4_uvd_live_prepare && IS_BUILTIN(CONFIG_DRM_AMDGPU) &&
		IS_ENABLED(CONFIG_X86_PS4) &&
		(adev->asic_type == CHIP_LIVERPOOL ||
		 adev->asic_type == CHIP_GLADIUS);
}

void amdgpu_ps4_live_prepare_init(struct amdgpu_device *adev)
{
	struct amdgpu_ps4_live_prepare *p = &adev->ps4_live_prepare;

	/* Never turn a second initialization into permission to prepare again. */
	if (p->initialized) {
		amdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_REINIT);
		return;
	}
	spin_lock_init(&p->lock);
	p->cached.selected = amdgpu_ps4_live_prepare_enabled(adev);
	p->cached.stage = p->cached.selected ? PS4_LIVE_SELECTED :
		PS4_LIVE_DISABLED;
	p->initialized = true;
}

void amdgpu_ps4_live_prepare_reserved(struct amdgpu_device *adev)
{
	struct amdgpu_ps4_live_prepare *p = &adev->ps4_live_prepare;
	unsigned long flags;

	if (!p->initialized || !p->cached.selected)
		return;
	spin_lock_irqsave(&p->lock, flags);
	p->cached.reservation_seen = true;
	p->cached.allocator_alive = true;
	if (p->cached.stage == PS4_LIVE_SELECTED)
		p->cached.stage = PS4_LIVE_RESERVED;
	spin_unlock_irqrestore(&p->lock, flags);
}

void amdgpu_ps4_live_prepare_invalidate(struct amdgpu_device *adev,
				       enum ps4_live_prepare_event event)
{
	struct amdgpu_ps4_live_prepare *p = &adev->ps4_live_prepare;
	unsigned long flags;

	if (!p->initialized || !p->cached.selected)
		return;
	spin_lock_irqsave(&p->lock, flags);
	++p->cached.observed_generation;
	p->cached.last_event = event;
	if (event == PS4_LIVE_EVENT_VMID_FINI)
		p->cached.allocator_alive = false;
	/* Initial GART setup predates preparation. Every event after the
	 * attempt is one-way invalidation, even if reset/suspend later aborts.
	 */
	if ((p->cached.attempted || event != PS4_LIVE_EVENT_GART_ENABLE) &&
	    p->cached.stage != PS4_LIVE_RELEASING &&
	    p->cached.stage != PS4_LIVE_RELEASED &&
	    p->cached.stage != PS4_LIVE_FAILED) {
		p->cached.stage = PS4_LIVE_INVALIDATED;
		p->cached.error = -ESTALE;
	}
	spin_unlock_irqrestore(&p->lock, flags);
}

void amdgpu_ps4_live_prepare_snapshot(struct amdgpu_device *adev,
				     struct ps4_live_prepare_snapshot *out)
{
	struct amdgpu_ps4_live_prepare *p = &adev->ps4_live_prepare;
	unsigned long flags;

	/* Pure cached-data query: no PM, reset lock, allocation, MMIO or work. */
	if (!p->initialized) {
		memset(out, 0, sizeof(*out));
		return;
	}
	spin_lock_irqsave(&p->lock, flags);
	*out = p->cached;
	spin_unlock_irqrestore(&p->lock, flags);
}

static int ps4_live_prepare_begin(struct amdgpu_device *adev)
{
	struct amdgpu_ps4_live_prepare *p = &adev->ps4_live_prepare;
	unsigned long flags;
	int r = 0;

	if (!p->initialized || !p->cached.selected)
		return -EINVAL;
	spin_lock_irqsave(&p->lock, flags);
	if (p->cached.attempted)
		r = -EALREADY;
	else if (p->cached.stage != PS4_LIVE_RESERVED ||
		 !p->cached.reservation_seen || !p->cached.allocator_alive)
		r = -EINVAL;
	else if (atomic_cmpxchg(&ps4_live_prepare_attempted, 0, 1))
		r = -EALREADY;
	else {
		p->cached.attempted = true;
		p->cached.stage = PS4_LIVE_PREPARING;
		p->cached.proof_generation = p->cached.observed_generation;
	}
	spin_unlock_irqrestore(&p->lock, flags);
	return r;
}

static void ps4_live_prepare_failure(struct amdgpu_device *adev, int error)
{
	struct amdgpu_ps4_live_prepare *p = &adev->ps4_live_prepare;
	unsigned long flags;

	if (!p->initialized || !p->cached.selected)
		return;
	spin_lock_irqsave(&p->lock, flags);
	p->cached.stage = PS4_LIVE_FAILED;
	p->cached.error = error;
	spin_unlock_irqrestore(&p->lock, flags);
}

static void ps4_live_prepare_attach(struct amdgpu_device *adev,
				    struct ps4_canary_state *s)
{
	struct amdgpu_ps4_live_prepare *p = &adev->ps4_live_prepare;
	unsigned long flags;

	spin_lock_irqsave(&p->lock, flags);
	p->pending = s;
	p->cached.retained = true;
	spin_unlock_irqrestore(&p->lock, flags);
}

static int ps4_live_prepare_publish(struct amdgpu_device *adev,
				     struct ps4_canary_state *s, u32 crc)
{
	struct amdgpu_ps4_live_prepare *p = &adev->ps4_live_prepare;
	unsigned long flags;
	int r = 0;

	if (!p->initialized || !p->cached.selected || !s)
		return -EINVAL;
	/* The caller has completed fence, exact pattern/guard and exclusivity
	 * checks. No ready helper or firmware access precedes this branch.
	 */
	spin_lock_irqsave(&p->lock, flags);
	if (p->pending != s || p->owner || !s->prepare_only ||
	    !s->ready_layout || s->ready_mode || !s->passed ||
	    !s->submitted || !s->fence || !s->pd.bo ||
	    s->mapped_pages != PS4_READY_MAPPED_PAGES ||
	    !adev->ps4_canary_fences_initialized ||
	    p->cached.stage != PS4_LIVE_PREPARING ||
	    p->cached.proof_generation != p->cached.observed_generation ||
	    !p->cached.allocator_alive || atomic_read(&adev->gpu_reset_counter)) {
		r = -ESTALE;
	} else {
		p->owner = s;
		p->pending = NULL;
		p->cached.pd_addr = amdgpu_gmc_pd_addr(s->pd.bo);
		p->cached.pd_bytes = s->pd_size;
		p->cached.pt_bytes = s->pt_size;
		p->cached.r1_bytes = PS4_READY_R_BYTES;
		p->cached.rb_bytes = PAGE_SIZE;
		p->cached.ib_bytes = PS4_READY_IB_BYTES;
		p->cached.mapped_pages = s->mapped_pages;
		p->cached.copy_crc = crc;
		p->cached.proof_reset_counter = 0;
		p->cached.proof_ns = ktime_get_ns();
		p->cached.proof_passed = true;
		p->cached.error = 0;
		p->cached.stage = PS4_LIVE_PREPARED;
	}
	spin_unlock_irqrestore(&p->lock, flags);
	return r;
}

static void ps4_live_prepare_free_bo(struct ps4_canary_bo *bo)
{
	amdgpu_bo_free_kernel(&bo->bo, &bo->gpu, &bo->cpu);
}

void amdgpu_ps4_live_prepare_fini(struct amdgpu_device *adev)
{
	struct amdgpu_ps4_live_prepare *p = &adev->ps4_live_prepare;
	struct ps4_canary_state *s;
	unsigned long flags;

	if (!p->initialized || !p->cached.selected)
		return;
	spin_lock_irqsave(&p->lock, flags);
	s = p->owner;
	if (!s) {
		spin_unlock_irqrestore(&p->lock, flags);
		return;
	}
	/* Only a successful preparation returns to device initialization.
	 * Failed preparation parks before normal removal can begin. The sole
	 * SDMA job completed; no UVD consumer has ever received these BOs.
	 * IP hw_fini has run; TTM and its DMA mappings still exist at this seam.
	 */
	p->owner = NULL;
	p->cached.stage = PS4_LIVE_RELEASING;
	spin_unlock_irqrestore(&p->lock, flags);
	dma_fence_put(s->fence);
	ps4_live_prepare_free_bo(&s->uvd_ib);
	ps4_live_prepare_free_bo(&s->uvd_rb);
	ps4_live_prepare_free_bo(&s->r1);
	ps4_live_prepare_free_bo(&s->ib);
	ps4_live_prepare_free_bo(&s->dst);
	ps4_live_prepare_free_bo(&s->src);
	ps4_live_prepare_free_bo(&s->pt);
	ps4_live_prepare_free_bo(&s->pd);
	kfree(s->pages);
	kfree(s);
	spin_lock_irqsave(&p->lock, flags);
	p->cached.stage = PS4_LIVE_RELEASED;
	p->cached.retained = false;
	spin_unlock_irqrestore(&p->lock, flags);
}
/* LIVE_PREPARE_STATE_END */
