/* SPDX-License-Identifier: MIT */
/* Actual extracted preparation helpers with host lifetime/lock models.
 * No firmware, GPU, device, allocator or reset emulation is claimed.
 */
#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void check(bool value, const char *name)
{
	if (!value) { fprintf(stderr, "TEST_FAIL:%s\n", name); exit(86); }
}
#define CHECK(v, name) check((v), (name))
typedef unsigned long long u64;
typedef unsigned int u32;
typedef _Atomic int atomic_t;
#define ATOMIC_INIT(v) (v)
int atomic_cmpxchg(atomic_t *p, int expected, int desired)
{
	atomic_compare_exchange_strong(p, &expected, desired);
	return expected;
}
typedef pthread_mutex_t spinlock_t;
static _Thread_local unsigned lock_depth;
static void lock_init(spinlock_t *p) { CHECK(!pthread_mutex_init(p, NULL), "mutex_init"); }
static void lock_take(spinlock_t *p) { CHECK(!pthread_mutex_lock(p), "mutex_lock"); ++lock_depth; }
static void lock_drop(spinlock_t *p) { CHECK(lock_depth == 1, "lock_scope"); --lock_depth; CHECK(!pthread_mutex_unlock(p), "mutex_unlock"); }
#define spin_lock_init(p) lock_init(p)
#define spin_lock_irqsave(p, flags) do { (flags) = 0; lock_take(p); } while (0)
#define spin_unlock_irqrestore(p, flags) do { (void)(flags); lock_drop(p); } while (0)
#define atomic_read(p) atomic_load(p)
#define READ_ONCE(x) __atomic_load_n(&(x), __ATOMIC_RELAXED)
static bool cfg_builtin = true, cfg_ps4 = true;
#define IS_BUILTIN(x) cfg_builtin
#define IS_ENABLED(x) cfg_ps4
#define module_param_named(a, b, c, d)
#define MODULE_PARM_DESC(a, b)
#define CHIP_LIVERPOOL 1
#define CHIP_GLADIUS 2
#define PAGE_SIZE 4096U
/* @CONSTANTS@ */
/* @HEADER@ */

struct fake_bo { u64 address; unsigned id; };
struct ps4_canary_bo { struct fake_bo *bo; u64 gpu; void *cpu; };
struct ps4_canary_state {
	struct ps4_canary_bo pd, pt, src, dst, ib, r1, uvd_rb, uvd_ib;
	void *pages, *fence;
	u64 pd_size, pt_size;
	unsigned mapped_pages;
	bool prepare_only, ready_layout, ready_mode, passed, submitted;
};
struct drm_device { unsigned refs; };
struct amdgpu_device {
	int asic_type;
	atomic_t gpu_reset_counter;
	bool ps4_canary_fences_initialized;
	struct amdgpu_ps4_live_prepare ps4_live_prepare;
	struct drm_device drm;
};
static unsigned cases, bo_frees, fence_puts, heap_frees, freed_mask;
static unsigned resource_reads, test_forbidden_gpu_reads;
static struct amdgpu_device *release_observer;
static bool nested_release;

static void observe_release(void)
{
	if (release_observer) {
		struct ps4_live_prepare_snapshot s;
		CHECK(lock_depth == 0, "release_outside_lock");
		CHECK(release_observer->ps4_live_prepare.owner == NULL, "owner_detached");
		amdgpu_ps4_live_prepare_snapshot(release_observer, &s);
		CHECK(s.stage == PS4_LIVE_RELEASING && s.retained, "release_retention");
		if (nested_release) amdgpu_ps4_live_prepare_fini(release_observer);
	}
}
static u64 amdgpu_gmc_pd_addr(struct fake_bo *bo) { CHECK(bo != NULL, "address_lookup_invalid"); ++resource_reads; return bo->address; }
static u64 ktime_get_ns(void) { return 123456789ULL; }
static void dma_fence_put(void *p) { observe_release(); CHECK(p != NULL, "fence_present"); ++fence_puts; free(p); }
static void amdgpu_bo_free_kernel(struct fake_bo **p, u64 *gpu, void **cpu)
{
	observe_release(); CHECK(*p != NULL, "bo_present");
	CHECK(!(freed_mask & (1U << (*p)->id)), "bo_once");
	freed_mask |= 1U << (*p)->id; ++bo_frees;
	free(*p); free(*cpu); *p = NULL; *cpu = NULL; *gpu = 0;
}
static void kfree(void *p) { observe_release(); ++heap_frees; free(p); }
/* @FLOW@ */

struct seq_file { void *private; char text[4096]; size_t len; };
struct inode { void *i_private; };
struct file { void *private_data; };
static bool fail_open;
static struct drm_device *adev_to_drm(struct amdgpu_device *a) { return &a->drm; }
void drm_dev_get(struct drm_device *d) { ++d->refs; }
static void drm_dev_put(struct drm_device *d) { CHECK(d->refs > 0, "device_ref_underflow"); --d->refs; }
static void seq_printf(struct seq_file *m, const char *fmt, ...)
{
	va_list ap; va_start(ap, fmt);
	int n = vsnprintf(m->text + m->len, sizeof(m->text) - m->len, fmt, ap);
	va_end(ap); CHECK(n >= 0 && (size_t)n < sizeof(m->text) - m->len, "status_size"); m->len += (size_t)n;
}
static void seq_puts(struct seq_file *m, const char *s) { seq_printf(m, "%s", s); }
static int single_open(struct file *f, int (*show)(struct seq_file *, void *), void *p)
{
	(void)show; if (fail_open) return -ENOMEM;
	struct seq_file *m = calloc(1, sizeof(*m)); CHECK(m != NULL, "allocation"); m->private = p; f->private_data = m; return 0;
}
static int single_release(struct inode *i, struct file *f) { (void)i; free(f->private_data); f->private_data = NULL; return 0; }
static int seq_read(void) { return 0; }
static int seq_lseek(void) { return 0; }
struct file_operations {
	void *owner;
	int (*open)(struct inode *, struct file *);
	int (*read)(void), (*llseek)(void);
	int (*release)(struct inode *, struct file *);
};
#define THIS_MODULE ((void *)1)
#define UTS_RELEASE "host-fixture-not-a-kernel"
/* @DEBUGFS@ */

static unsigned fence_inits, canary_calls;
static bool mapping_gate;
static bool amdgpu_ps4_map_canary_enabled(struct amdgpu_device *a) { (void)a; return mapping_gate; }
static void amdgpu_fence_driver_hw_init(struct amdgpu_device *a) { (void)a; ++fence_inits; }
static void amdgpu_ps4_map_canary_run(struct amdgpu_device *a) { CHECK(a->ps4_canary_fences_initialized, "fence_before_canary"); ++canary_calls; }
static void extracted_early_fence(struct amdgpu_device *adev) { /* @EARLY_FENCE@ */ }
static void extracted_outer_fence(struct amdgpu_device *adev) { /* @OUTER_FENCE@ */ }

static void reset_counters(void)
{
	bo_frees = fence_puts = heap_frees = freed_mask = resource_reads = test_forbidden_gpu_reads = 0;
	release_observer = NULL; nested_release = false;
}
static void init_device_same_kernel(struct amdgpu_device *a, bool selected)
{
	memset(a, 0, sizeof(*a)); atomic_init(&a->gpu_reset_counter, 0);
	a->asic_type = CHIP_LIVERPOOL; a->drm.refs = 1;
	ps4_uvd_live_prepare = selected; cfg_builtin = cfg_ps4 = true;
	amdgpu_ps4_live_prepare_init(a);
}
static void init_device(struct amdgpu_device *a, bool selected)
{
	/* Only the test harness starts a new synthetic kernel instance. */
	atomic_store(&ps4_live_prepare_attempted, 0);
	init_device_same_kernel(a, selected);
}
static void destroy_device(struct amdgpu_device *a)
{
	CHECK(!a->ps4_live_prepare.owner && !a->ps4_live_prepare.pending, "test_owner_cleanup");
	CHECK(!pthread_mutex_destroy(&a->ps4_live_prepare.lock), "mutex_destroy");
}
static struct ps4_canary_state *new_state(void)
{
	struct ps4_canary_state *s = calloc(1, sizeof(*s)); CHECK(s != NULL, "allocation");
	struct ps4_canary_bo *v[] = { &s->pd, &s->pt, &s->src, &s->dst, &s->ib, &s->r1, &s->uvd_rb, &s->uvd_ib };
	for (unsigned i = 0; i < 8; ++i) {
		v[i]->bo = calloc(1, sizeof(*v[i]->bo)); v[i]->cpu = calloc(1, 16);
		CHECK(v[i]->bo && v[i]->cpu, "allocation"); v[i]->bo->id = i;
		v[i]->bo->address = v[i]->gpu = 0x12345000ULL + i * PAGE_SIZE;
	}
	s->pages = calloc(1, 16); s->fence = calloc(1, 16); CHECK(s->pages && s->fence, "allocation");
	s->pd_size = 0x20000; s->pt_size = 0x2000; s->mapped_pages = 812;
	s->prepare_only = s->ready_layout = s->passed = s->submitted = true;
	return s;
}
/* Host-only disposal of simulated parked/failed objects. Not production cleanup. */
static void discard_test_state(struct ps4_canary_state *s)
{
	struct ps4_canary_bo *v[] = { &s->pd, &s->pt, &s->src, &s->dst, &s->ib, &s->r1, &s->uvd_rb, &s->uvd_ib };
	for (unsigned i = 0; i < 8; ++i) { free(v[i]->bo); free(v[i]->cpu); }
	free(s->pages); free(s->fence); free(s);
}
static struct ps4_canary_state *begin_preparation(struct amdgpu_device *a)
{
	amdgpu_ps4_live_prepare_reserved(a); CHECK(ps4_live_prepare_begin(a) == 0, "begin_reserved");
	a->ps4_canary_fences_initialized = true;
	struct ps4_canary_state *s = new_state(); ps4_live_prepare_attach(a, s); return s;
}
static void make_prepared(struct amdgpu_device *a)
{
	struct ps4_canary_state *s = begin_preparation(a);
	CHECK(ps4_live_prepare_publish(a, s, 0x570ae4eb) == 0, "publish_valid");
}
static void check_no_hardware(void) { CHECK(test_forbidden_gpu_reads == 0, "cached_only"); }

static void gates(void)
{
	for (unsigned enabled = 0; enabled < 2; ++enabled)
	for (unsigned builtin = 0; builtin < 2; ++builtin)
	for (unsigned ps4 = 0; ps4 < 2; ++ps4)
	for (unsigned chip = 0; chip < 3; ++chip) {
		struct amdgpu_device a = {0}; a.asic_type = (int)chip;
		ps4_uvd_live_prepare = enabled; cfg_builtin = builtin; cfg_ps4 = ps4;
		CHECK(amdgpu_ps4_live_prepare_enabled(&a) == (enabled && builtin && ps4 && chip != 0), "boot_gate"); ++cases;
	}
}
static void lifecycle(void)
{
	struct amdgpu_device a; struct ps4_live_prepare_snapshot q;
	reset_counters(); memset(&a, 0, sizeof(a)); memset(&q, 0xa5, sizeof(q));
	amdgpu_ps4_live_prepare_snapshot(&a, &q); CHECK(q.stage == PS4_LIVE_DISABLED && !q.retained, "uninitialized_query");
	amdgpu_ps4_live_prepare_reserved(&a); amdgpu_ps4_live_prepare_invalidate(&a, PS4_LIVE_EVENT_RESET); amdgpu_ps4_live_prepare_fini(&a);
	CHECK(!a.ps4_live_prepare.initialized && bo_frees == 0, "uninitialized_noop"); check_no_hardware(); ++cases;
	init_device(&a, false); CHECK(ps4_live_prepare_begin(&a) == -EINVAL, "disabled_begin");
	amdgpu_ps4_live_prepare_reserved(&a); amdgpu_ps4_live_prepare_invalidate(&a, PS4_LIVE_EVENT_RESET);
	amdgpu_ps4_live_prepare_snapshot(&a, &q); CHECK(q.stage == PS4_LIVE_DISABLED && !q.reservation_seen, "disabled_noop"); destroy_device(&a); ++cases;
	init_device(&a, true); CHECK(ps4_live_prepare_begin(&a) == -EINVAL, "unreserved_begin"); make_prepared(&a);
	CHECK(ps4_live_prepare_begin(&a) == -EALREADY, "repeat_begin");
	unsigned reads = resource_reads; amdgpu_ps4_live_prepare_snapshot(&a, &q);
	CHECK(q.stage == PS4_LIVE_PREPARED && q.proof_passed && q.retained && q.attempted, "prepared_history");
	CHECK(q.pd_addr == 0x12345000 && q.mapped_pages == 812 && q.copy_crc == 0x570ae4eb && q.proof_ns == 123456789, "proof_values");
	CHECK(q.pd_bytes == 0x20000 && q.pt_bytes == 0x2000 && q.r1_bytes == 0x308000 && q.ib_bytes == 0x20000 && q.rb_bytes == 4096, "proof_layout");
	q.stage = PS4_LIVE_FAILED; amdgpu_ps4_live_prepare_snapshot(&a, &q); CHECK(q.stage == PS4_LIVE_PREPARED && reads == resource_reads, "snapshot_independent");
	release_observer = &a; nested_release = true; amdgpu_ps4_live_prepare_fini(&a); release_observer = NULL;
	CHECK(!a.ps4_live_prepare.owner && !a.ps4_live_prepare.pending, "owner_detached");
	CHECK(bo_frees == 8 && fence_puts == 1 && heap_frees == 2 && freed_mask == 255, "release_exact");
	amdgpu_ps4_live_prepare_fini(&a); CHECK(bo_frees == 8 && fence_puts == 1, "release_idempotent");
	amdgpu_ps4_live_prepare_snapshot(&a, &q); CHECK(q.stage == PS4_LIVE_RELEASED && !q.retained && q.proof_passed, "released_history");
	CHECK(ps4_live_prepare_begin(&a) == -EALREADY, "released_no_rearm"); check_no_hardware(); destroy_device(&a); ++cases;
}
static void invalidations(void)
{
	for (unsigned e = PS4_LIVE_EVENT_GART_ENABLE; e <= PS4_LIVE_EVENT_REINIT; ++e) {
		struct amdgpu_device a; struct ps4_live_prepare_snapshot q;
		reset_counters(); init_device(&a, true); amdgpu_ps4_live_prepare_reserved(&a);
		amdgpu_ps4_live_prepare_invalidate(&a, (enum ps4_live_prepare_event)e);
		amdgpu_ps4_live_prepare_snapshot(&a, &q);
		CHECK(q.observed_generation == 1 && q.last_event == (enum ps4_live_prepare_event)e, "event_recorded");
		if (e == PS4_LIVE_EVENT_GART_ENABLE) CHECK(ps4_live_prepare_begin(&a) == 0, "initial_gart_allowed");
		else CHECK(q.stage == PS4_LIVE_INVALIDATED && ps4_live_prepare_begin(&a) == -EINVAL, "preinvalidate_blocks");
		destroy_device(&a); ++cases;
		reset_counters(); init_device(&a, true); make_prepared(&a);
		amdgpu_ps4_live_prepare_invalidate(&a, (enum ps4_live_prepare_event)e); amdgpu_ps4_live_prepare_reserved(&a);
		amdgpu_ps4_live_prepare_snapshot(&a, &q);
		CHECK(q.stage == PS4_LIVE_INVALIDATED && q.error == -ESTALE && q.proof_passed && q.retained, "invalidate_prepared");
		CHECK(q.proof_generation == 0 && q.observed_generation == 1 && ps4_live_prepare_begin(&a) == -EALREADY, "generation_history");
		amdgpu_ps4_live_prepare_fini(&a); destroy_device(&a); ++cases;
	}
	struct amdgpu_device a; init_device(&a, true); amdgpu_ps4_live_prepare_reserved(&a); amdgpu_ps4_live_prepare_init(&a);
	CHECK(a.ps4_live_prepare.cached.stage == PS4_LIVE_INVALIDATED && ps4_live_prepare_begin(&a) == -EINVAL, "reinit_no_rearm"); destroy_device(&a); ++cases;
}
static void rejected_publications(void)
{
	static const char *names[] = { "pending", "owner", "prepare_only", "layout", "ready_mode", "passed", "submitted", "fence", "pages", "fence_init", "stage", "generation", "allocator", "reset", "pd" };
	for (unsigned n = 0; n < sizeof(names) / sizeof(names[0]); ++n) {
		struct amdgpu_device a; reset_counters(); init_device(&a, true); struct ps4_canary_state *s = begin_preparation(&a); void *fence = s->fence;
		struct fake_bo *pd = s->pd.bo;
		switch (n) {
		case 0: a.ps4_live_prepare.pending = NULL; break;
		case 1: a.ps4_live_prepare.owner = s; break;
		case 2: s->prepare_only = false; break;
		case 3: s->ready_layout = false; break;
		case 4: s->ready_mode = true; break;
		case 5: s->passed = false; break;
		case 6: s->submitted = false; break;
		case 7: s->fence = NULL; break;
		case 8: --s->mapped_pages; break;
		case 9: a.ps4_canary_fences_initialized = false; break;
		case 10: a.ps4_live_prepare.cached.stage = PS4_LIVE_INVALIDATED; break;
		case 11: ++a.ps4_live_prepare.cached.observed_generation; break;
		case 12: a.ps4_live_prepare.cached.allocator_alive = false; break;
		case 13: atomic_store(&a.gpu_reset_counter, 1); break;
		case 14: s->pd.bo = NULL; break;
		}
		char witness[80]; snprintf(witness, sizeof(witness), "publish_reject_%s", names[n]);
		CHECK(ps4_live_prepare_publish(&a, s, 1) == -ESTALE, witness);
		CHECK(!a.ps4_live_prepare.cached.proof_passed && bo_frees == 0 && resource_reads == 0, "failed_publish_no_claim");
		a.ps4_live_prepare.owner = a.ps4_live_prepare.pending = NULL; s->fence = fence; s->pd.bo = pd; discard_test_state(s); destroy_device(&a); ++cases;
	}
	struct amdgpu_device a = {0}; reset_counters();
	CHECK(ps4_live_prepare_publish(&a, NULL, 0) == -EINVAL, "uninitialized_publish"); ++cases;
	init_device(&a, false); CHECK(ps4_live_prepare_publish(&a, NULL, 0) == -EINVAL, "disabled_publish"); destroy_device(&a); ++cases;
	init_device(&a, true); CHECK(ps4_live_prepare_publish(&a, NULL, 0) == -EINVAL && resource_reads == 0, "null_publish"); destroy_device(&a); ++cases;
}
static void failed_owner(void)
{
	struct amdgpu_device a; reset_counters(); init_device(&a, true); struct ps4_canary_state *s = begin_preparation(&a);
	ps4_live_prepare_failure(&a, -ETIMEDOUT); amdgpu_ps4_live_prepare_invalidate(&a, PS4_LIVE_EVENT_RESET); amdgpu_ps4_live_prepare_fini(&a);
	CHECK(a.ps4_live_prepare.cached.stage == PS4_LIVE_FAILED && a.ps4_live_prepare.cached.error == -ETIMEDOUT, "failure_preserved");
	CHECK(a.ps4_live_prepare.pending == s && a.ps4_live_prepare.cached.retained && bo_frees == 0, "failed_pending_retained");
	CHECK(ps4_live_prepare_begin(&a) == -EALREADY, "failed_no_rearm");
	a.ps4_live_prepare.pending = NULL; discard_test_state(s); destroy_device(&a); ++cases;
}
static void debugfs_lifetime(void)
{
	struct amdgpu_device a; reset_counters(); init_device(&a, true); make_prepared(&a);
	struct inode i = { .i_private = &a }; struct file f = {0};
	fail_open = true; CHECK(amdgpu_ps4_live_prepare_fops.open(&i, &f) == -ENOMEM && a.drm.refs == 1, "open_failure_ref"); ++cases;
	fail_open = false; CHECK(amdgpu_ps4_live_prepare_fops.open(&i, &f) == 0 && a.drm.refs == 2, "open_device_ref");
	unsigned reads = resource_reads; CHECK(amdgpu_ps4_live_prepare_show(f.private_data, NULL) == 0, "status_show");
	CHECK(strstr(((struct seq_file *)f.private_data)->text, "observer=cached-only current_hardware_mapping=not-sampled UVD_setup=never"), "status_scope");
	amdgpu_ps4_live_prepare_fini(&a); CHECK(a.drm.refs == 2, "open_survives_owner_release");
	((struct seq_file *)f.private_data)->len = 0; CHECK(amdgpu_ps4_live_prepare_show(f.private_data, NULL) == 0 && reads == resource_reads, "status_after_release");
	CHECK(strstr(((struct seq_file *)f.private_data)->text, "stage=RELEASED"), "released_status");
	CHECK(amdgpu_ps4_live_prepare_fops.release(&i, &f) == 0 && a.drm.refs == 1 && f.private_data == NULL, "close_device_ref");
	check_no_hardware(); destroy_device(&a); ++cases;
}
static _Atomic bool reader_stop;
static _Atomic unsigned reader_samples;
static void *reader(void *arg)
{
	struct amdgpu_device *a = arg; struct ps4_live_prepare_snapshot s;
	do {
		amdgpu_ps4_live_prepare_snapshot(a, &s);
		CHECK(s.proof_passed && s.stage >= PS4_LIVE_PREPARED && s.stage <= PS4_LIVE_RELEASED, "reader_stage");
		CHECK(s.stage != PS4_LIVE_RELEASED || !s.retained, "reader_release_consistency");
		atomic_fetch_add(&reader_samples, 1);
	} while (!atomic_load(&reader_stop));
	return NULL;
}
static void concurrent_release(void)
{
	struct amdgpu_device a; reset_counters(); init_device(&a, true); make_prepared(&a);
	atomic_store(&reader_stop, false); atomic_store(&reader_samples, 0); pthread_t t;
	CHECK(!pthread_create(&t, NULL, reader, &a), "thread_create");
	while (!atomic_load(&reader_samples)) { }
	amdgpu_ps4_live_prepare_invalidate(&a, PS4_LIVE_EVENT_HW_FINI);
	amdgpu_ps4_live_prepare_fini(&a); atomic_store(&reader_stop, true);
	CHECK(!pthread_join(t, NULL), "thread_join"); CHECK(bo_frees == 8 && atomic_load(&reader_samples) > 0, "concurrent_release"); destroy_device(&a); ++cases;
}
static void fence_accounting(void)
{
	for (unsigned gate = 0; gate < 2; ++gate) {
		struct amdgpu_device a = {0}; mapping_gate = gate; fence_inits = canary_calls = 0;
		extracted_early_fence(&a); extracted_outer_fence(&a);
		CHECK(fence_inits == 1 && canary_calls == gate, "fence_once"); ++cases;
	}
}
static void rebind_blocked(void)
{
	struct amdgpu_device a, b; reset_counters(); init_device(&a, true); make_prepared(&a);
	amdgpu_ps4_live_prepare_invalidate(&a, PS4_LIVE_EVENT_HW_FINI);
	amdgpu_ps4_live_prepare_fini(&a); destroy_device(&a);
	init_device_same_kernel(&b, true); amdgpu_ps4_live_prepare_reserved(&b);
	CHECK(ps4_live_prepare_begin(&b) == -EALREADY && !b.ps4_live_prepare.cached.attempted, "rebind_no_repeat");
	CHECK(!b.ps4_live_prepare.owner && !b.ps4_live_prepare.pending, "rebind_no_owner"); destroy_device(&b); ++cases;
}

/* Models probe outcomes around the three actual new integration blocks.
 * Does not execute DRM registration, PCI access or AMDGPU hardware teardown.
 */
static struct amdgpu_device *probe_adev;
static bool probe_registered;
static char probe_events[32];
static unsigned probe_event_count;
static void probe_record(char event)
{
	CHECK(probe_event_count + 1 < sizeof(probe_events), "probe_trace_size");
	probe_events[probe_event_count++] = event; probe_events[probe_event_count] = 0;
}
static void drm_dev_unplug(struct drm_device *ddev)
{
	CHECK(ddev == &probe_adev->drm && probe_registered, "probe_unplug_registered");
	probe_registered = false; probe_record('U');
}
static void amdgpu_driver_unload_kms(struct drm_device *ddev)
{
	CHECK(ddev == &probe_adev->drm && !probe_registered, "probe_unplug_before_unload");
	probe_record('H');
	amdgpu_ps4_live_prepare_invalidate(probe_adev, PS4_LIVE_EVENT_HW_FINI);
	amdgpu_ps4_live_prepare_fini(probe_adev);
}
static void pci_disable_device(void *pdev) { (void)pdev; probe_record('P'); }
static int modeled_probe(struct amdgpu_device *adev, unsigned scenario)
{
	struct drm_device *ddev = &adev->drm; void *pdev = NULL;
	bool ps4_prepare_loaded = false, ps4_prepare_registered = false;
	int ret = -EIO;
	if (scenario == 0 || scenario == 4) {
		/* Existing load_kms failure already calls unload. */
		amdgpu_driver_unload_kms(ddev); goto err_pci;
	}
	/* @PROBE_LOADED@ */
	for (unsigned retry = 0; retry < 2; ++retry)
		CHECK(probe_event_count == 0, "probe_retry_no_cleanup");
	if (scenario == 1) goto err_pci;
	probe_registered = true;
	/* @PROBE_REGISTERED@ */
	if (scenario == 2) goto err_pci;
	return 0;
err_pci:
	/* @PROBE_ERROR@ */
	pci_disable_device(pdev);
	return ret;
}
static void probe_failures(void)
{
	for (unsigned selected = 0; selected < 2; ++selected)
	for (unsigned scenario = 0; scenario < 5; ++scenario) {
		struct amdgpu_device a; reset_counters(); init_device(&a, selected);
		bool owns = selected && scenario != 4;
		if (owns) make_prepared(&a);
		probe_adev = &a; probe_registered = false;
		probe_event_count = 0; probe_events[0] = 0;
		int r = modeled_probe(&a, scenario);
		if (scenario == 3) {
			CHECK(r == 0 && probe_event_count == 0 && probe_registered, "probe_success_retained");
			CHECK((a.ps4_live_prepare.owner != NULL) == owns, "probe_success_owner");
			drm_dev_unplug(&a.drm); amdgpu_driver_unload_kms(&a.drm);
		} else {
			const char *expected = (scenario == 0 || scenario == 4) ? "HP" : !selected ? "P" : scenario == 2 ? "UHP" : "HP";
			CHECK(r == -EIO && !strcmp(probe_events, expected), "probe_unwind_order");
			CHECK(!a.ps4_live_prepare.owner && bo_frees == (owns ? 8U : 0U), "probe_completed_owner_released");
		}
		destroy_device(&a); ++cases;
	}
}
int main(void)
{
	gates(); lifecycle(); invalidations(); rejected_publications(); failed_owner(); debugfs_lifetime(); concurrent_release(); fence_accounting(); rebind_blocked(); probe_failures();
	printf("PASS: %u actual-source preparation/lifetime cases; no devices or hardware emulation\n", cases);
	return 0;
}
