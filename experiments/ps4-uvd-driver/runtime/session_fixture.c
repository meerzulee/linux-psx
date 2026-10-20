/* SPDX-License-Identifier: MIT */
/* Actual-source, CPU-only checks. External kernel/DRM APIs below are stubs;
 * they check observable call order and lock ownership, not DMA or scheduling.
 */
#include <assert.h>
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef ERESTARTSYS
#define ERESTARTSYS 512
#endif
#ifndef EHWPOISON
#define EHWPOISON 133
#endif
typedef uint32_t u32;
typedef uint64_t u64;
typedef struct { int value; } atomic_t;
struct mutex { unsigned held; };
typedef struct { unsigned held; } spinlock_t;
struct amdgpu_device;
struct amdgpu_ring;
struct drm_file { unsigned id; };
struct dma_fence { int error; int status; unsigned puts; };
struct drm_sched_fence { struct dma_fence finished, scheduled; };
struct drm_gpu_scheduler { struct amdgpu_ring *ring; };
struct drm_sched_job { struct drm_gpu_scheduler *sched; struct drm_sched_fence *s_fence; };
struct amdgpu_job {
	struct drm_sched_job base;
	struct amdgpu_ring *ring;
	void *vm, *gang_submit;
	void *ibs;
	unsigned generation, job_run_counter, num_ibs;
	bool leased, entered;
};
struct amdgpu_ps4_uvd_jobs { spinlock_t lock; unsigned jobs; bool closed; int error; };
struct amdgpu_ps4_uvd_runtime {
	struct mutex session_lock;
	atomic_t failed;
	struct amdgpu_ps4_uvd_jobs jobs;
};
struct amdgpu_ring { struct amdgpu_device *adev; struct drm_gpu_scheduler sched; const char *name; };
struct amdgpu_device {
	void *dev;
	bool runtime, accel_working;
	unsigned generation;
	struct amdgpu_ps4_uvd_runtime ps4_uvd_runtime;
	struct {
		struct { struct amdgpu_ring ring; } inst[1];
		atomic_t handles[1];
		struct drm_file *filp[1];
		unsigned max_handles, decode_image_width;
		bool use_ctx_buf;
	} uvd;
};
struct drm_device { struct amdgpu_device *adev; };
struct amdgpu_bo_list { struct mutex bo_list_mutex; };
struct amdgpu_cs_parser {
	struct amdgpu_device *adev;
	struct drm_file *filp;
	unsigned gang_size;
	struct amdgpu_job *jobs[1];
	struct amdgpu_bo_list *bo_list;
};
struct amdgpu_bo { uint32_t words[1024]; unsigned mapped; };
struct amdgpu_uvd_cs_ctx { struct amdgpu_cs_parser *parser; unsigned *buf_sizes; };

static struct amdgpu_device device;
static struct drm_device drm;
static struct drm_file owner, stranger;
static struct amdgpu_bo_list bo_list;
static struct amdgpu_bo message;
static struct amdgpu_job job;
static struct drm_sched_fence scheduler_fence;
static struct dma_fence hardware_fence, destroy_fence;
static unsigned cases, assertions;
static const char *case_name;
static char events[2048];
static unsigned event_count;
static unsigned fault_calls, abort_calls, destroy_calls, wait_calls, fini_calls;
static unsigned entered_calls, scheduled_calls, submitted_calls, resource_calls;
static unsigned sleeps, retire_at_sleep, close_at_sleep;
static u64 now_ns;
static int fail_stage, failure, destroy_error;
static long wait_result;
static bool parse_create, failed_after_admission;

static void check(bool condition, const char *expression, int line)
{
	++assertions;
	if (!condition) {
		fprintf(stderr, "%s:%d %s: %s (events=%s)\n", __FILE__, line,
			case_name, expression, events);
		abort();
	}
}
#define CHECK(x) check(!!(x), #x, __LINE__)
#define NSEC_PER_MSEC 1000000ULL
#define ALIGN(x, a) (((x) + (a) - 1) & ~((a) - 1))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define le32_to_cpu(x) (x)
#define READ_ONCE(x) (x)
#define ERR_PTR(x) ((void *)(intptr_t)(x))
#define IS_ERR(x) ((uintptr_t)(x) >= (uintptr_t)-4095)
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define to_amdgpu_job(p) container_of(p, struct amdgpu_job, base)
#define to_amdgpu_ring(p) ((p)->ring)
#define drm_err(...) ((void)0)
#define drm_err_ratelimited(...) ((void)0)
#define drm_dbg(...) ((void)0)
#define DRM_ERROR(...) ((void)0)
#define dev_dbg(...) ((void)0)
#define dev_err(...) ((void)0)
#define dev_WARN_ONCE(d, c, ...) do { (void)(d); (void)(c); } while (0)
#define trace_amdgpu_sched_run_job(...) ((void)0)

static void event(char c)
{
	CHECK(event_count + 1 < sizeof(events));
	events[event_count++] = c;
	events[event_count] = 0;
}
static int atomic_read(const atomic_t *a) { return a->value; }
static void atomic_set(atomic_t *a, int n) { a->value = n; }
static int atomic_cmpxchg(atomic_t *a, int old, int n)
{
	int prior = a->value;
	if (prior == old) a->value = n;
	return prior;
}
static bool mutex_trylock(struct mutex *m)
{
	CHECK(m == &device.ps4_uvd_runtime.session_lock);
	event('T');
	if (m->held) return false;
	m->held = 1;
	event('L');
	return true;
}
static void mutex_lock(struct mutex *m)
{
	CHECK(m == &device.ps4_uvd_runtime.session_lock && !m->held);
	m->held = 1;
	event('L');
}
static void mutex_unlock(struct mutex *m)
{
	CHECK(m->held == 1);
	m->held = 0;
	event(m == &device.ps4_uvd_runtime.session_lock ? 'U' : 'b');
}
#define spin_lock_irqsave(p, f) do { (f) = 0; CHECK(!(p)->held); (p)->held = 1; } while (0)
#define spin_unlock_irqrestore(p, f) do { (void)(f); CHECK((p)->held == 1); (p)->held = 0; } while (0)
#define lockdep_assert_held(p) CHECK((p)->held == 1)
static u64 ktime_get_ns(void) { return now_ns; }
static unsigned long msecs_to_jiffies(unsigned n) { return n; }
static struct amdgpu_device *drm_to_adev(struct drm_device *d) { return d->adev; }
static void *adev_to_drm(struct amdgpu_device *d) { return d; }
static bool amdgpu_ras_intr_triggered(void) { return false; }
static bool amdgpu_ps4_uvd_runtime_enabled(struct amdgpu_device *d) { return d->runtime; }
static bool amdgpu_ps4_uvd_runtime_ring(struct amdgpu_ring *r) { return r && r->adev->runtime; }
static struct amdgpu_ring *amdgpu_job_ring(struct amdgpu_job *j) { return j->ring; }
static struct amdgpu_ps4_uvd_jobs *amdgpu_ps4_uvd_runtime_jobs(struct amdgpu_ring *r)
{
	return amdgpu_ps4_uvd_runtime_ring(r) ? &r->adev->ps4_uvd_runtime.jobs : NULL;
}
static void amdgpu_ps4_uvd_runtime_fault(struct amdgpu_device *d, int error)
{
	CHECK(error < 0);
	++fault_calls;
	event('F');
	if (!d->ps4_uvd_runtime.failed.value) d->ps4_uvd_runtime.failed.value = error;
	d->ps4_uvd_runtime.jobs.closed = true;
	d->ps4_uvd_runtime.jobs.error = error;
}
static void usleep_range(unsigned min, unsigned max_us)
{
	CHECK(min == 1000 && max_us == 2000);
	CHECK(device.ps4_uvd_runtime.session_lock.held == 1);
	CHECK(!device.ps4_uvd_runtime.jobs.lock.held);
	++sleeps;
	/* A deterministic delayed wakeup: this is not a timing/concurrency model. */
	now_ns += 100 * NSEC_PER_MSEC;
	if (retire_at_sleep && sleeps == retire_at_sleep) device.ps4_uvd_runtime.jobs.jobs = 0;
	if (close_at_sleep && sleeps == close_at_sleep) amdgpu_ps4_uvd_runtime_fault(&device, -EIO);
}
static int amdgpu_bo_kmap(struct amdgpu_bo *b, void **ptr)
{
	CHECK(!b->mapped);
	b->mapped = 1;
	*ptr = b->words;
	return 0;
}
static void amdgpu_bo_kunmap(struct amdgpu_bo *b) { CHECK(b->mapped == 1); b->mapped = 0; }
static int amdgpu_uvd_cs_msg(struct amdgpu_uvd_cs_ctx *, struct amdgpu_bo *, unsigned int);
static int amdgpu_cs_parser_init(struct amdgpu_cs_parser *p, struct amdgpu_device *d,
	struct drm_file *f, void *data)
{
	(void)data;
	memset(p, 0, sizeof(*p));
	p->adev = d; p->filp = f; p->gang_size = 1; p->jobs[0] = &job; p->bo_list = &bo_list;
	return fail_stage == 1 ? failure : 0;
}
static int amdgpu_cs_pass1(struct amdgpu_cs_parser *p, void *data)
{ (void)p; (void)data; return fail_stage == 2 ? failure : 0; }
static int amdgpu_cs_pass2(struct amdgpu_cs_parser *p)
{ (void)p; return fail_stage == 3 ? failure : 0; }
static int amdgpu_cs_parser_bos(struct amdgpu_cs_parser *p, void *data)
{
	(void)data;
	CHECK(!p->bo_list->bo_list_mutex.held);
	if (fail_stage == 4) return failure;
	p->bo_list->bo_list_mutex.held = 1;
	return 0;
}
static int amdgpu_ps4_uvd_jobs_admit(struct amdgpu_ps4_uvd_jobs *s,
	struct amdgpu_cs_parser *p, struct amdgpu_job *j)
{
	CHECK(p->bo_list->bo_list_mutex.held);
	event('A');
	if (fail_stage == 5) return failure;
	if (s->closed) return -ESHUTDOWN;
	j->leased = true; ++s->jobs;
	if (failed_after_admission) device.ps4_uvd_runtime.failed.value = -EIO;
	return 0;
}
static void stage_lock(struct amdgpu_cs_parser *p)
{
	CHECK(p->bo_list->bo_list_mutex.held);
	CHECK(device.ps4_uvd_runtime.session_lock.held == (unsigned)device.runtime);
}
static int amdgpu_cs_patch_jobs(struct amdgpu_cs_parser *p)
{
	stage_lock(p); event('P');
	if (parse_create) {
		unsigned sizes[6] = { 3556, ~0U, ~0U, 2048, ~0U, 992 };
		struct amdgpu_uvd_cs_ctx ctx = { p, sizes };
		int r = amdgpu_uvd_cs_msg(&ctx, &message, 0);
		if (r) return r;
	}
	return fail_stage == 6 ? failure : 0;
}
static int amdgpu_cs_vm_handling(struct amdgpu_cs_parser *p)
{ stage_lock(p); event('V'); return fail_stage == 7 ? failure : 0; }
static int amdgpu_cs_sync_rings(struct amdgpu_cs_parser *p)
{ stage_lock(p); event('S'); return fail_stage == 8 ? failure : 0; }
static void trace_amdgpu_cs_ibs(struct amdgpu_cs_parser *p) { stage_lock(p); }
static int amdgpu_cs_submit(struct amdgpu_cs_parser *p, void *data)
{
	(void)data; stage_lock(p); event('Q');
	if (fail_stage == 9) return failure;
	/* Boundary matches the real successful submit's parser ownership transfer. */
	p->jobs[0] = NULL;
	mutex_unlock(&p->bo_list->bo_list_mutex);
	return 0;
}
static void amdgpu_ps4_uvd_jobs_abort_reserved(struct amdgpu_job *j)
{
	CHECK(bo_list.bo_list_mutex.held);
	if (!j->leased) return;
	++abort_calls; event('R');
	j->leased = false;
	CHECK(device.ps4_uvd_runtime.jobs.jobs > 0);
	--device.ps4_uvd_runtime.jobs.jobs;
}
static void amdgpu_cs_parser_fini(struct amdgpu_cs_parser *p)
{
	CHECK(!p->bo_list->bo_list_mutex.held);
	/* An independently owned busy lock is left held by the trylock refusal. */
	if (!strstr(case_name, "busy")) CHECK(!device.ps4_uvd_runtime.session_lock.held);
	++fini_calls; event('I');
}
static int amdgpu_uvd_get_destroy_msg(struct amdgpu_ring *r, uint32_t handle,
	bool direct, struct dma_fence **f)
{
	CHECK(handle && !direct);
	if (r->adev->runtime) {
		CHECK(r->adev->ps4_uvd_runtime.session_lock.held);
		CHECK(!r->adev->ps4_uvd_runtime.failed.value);
		/* Independent expected protocol: no accepted CREATE remains pending. */
		CHECK(!r->adev->ps4_uvd_runtime.jobs.jobs);
		CHECK(!r->adev->ps4_uvd_runtime.jobs.closed);
	}
	++destroy_calls; event('D');
	if (destroy_error) return destroy_error;
	*f = &destroy_fence;
	return 0;
}
static long dma_fence_wait_timeout(struct dma_fence *f, bool interruptible, unsigned long timeout)
{
	CHECK(f == &destroy_fence && !interruptible && timeout == 2100);
	CHECK(device.ps4_uvd_runtime.session_lock.held);
	++wait_calls; event('W');
	return wait_result;
}
static long dma_fence_wait(struct dma_fence *f, bool interruptible)
{ CHECK(f == &destroy_fence && !interruptible); ++wait_calls; event('W'); return 0; }
static int dma_fence_get_status(struct dma_fence *f) { return f->status; }
static void dma_fence_put(struct dma_fence *f) { CHECK(f == &destroy_fence); CHECK(!f->puts); ++f->puts; }
static unsigned amdgpu_vm_generation(struct amdgpu_device *d, void *vm)
{ (void)vm; return d->generation; }
static void dma_fence_set_error(struct dma_fence *f, int e) { CHECK(e < 0); f->error = e; }
static int amdgpu_ps4_uvd_jobs_enter(struct amdgpu_job *j)
{
	++entered_calls; event('E');
	if (device.ps4_uvd_runtime.jobs.closed) return -ESHUTDOWN;
	j->entered = true;
	return 0;
}
static int amdgpu_ib_schedule(struct amdgpu_ring *r, unsigned count, void *ibs,
	struct amdgpu_job *j, struct dma_fence **f)
{
	(void)r; (void)count; (void)ibs; CHECK(j->entered); ++scheduled_calls; event('H');
	if (fail_stage == 10) return failure;
	*f = &hardware_fence;
	return 0;
}
static void amdgpu_ps4_uvd_jobs_submitted(struct amdgpu_job *j, struct dma_fence *f, int error)
{
	(void)j; ++submitted_calls; event('C');
	if (error) amdgpu_ps4_uvd_runtime_fault(&device, error);
	else CHECK(f == &hardware_fence);
}
static void amdgpu_job_free_resources(struct amdgpu_job *j) { (void)j; ++resource_calls; }

/* SESSION_ACTUAL_FUNCTIONS */

static void reset(const char *name)
{
	case_name = name; ++cases;
	memset(&device, 0, sizeof(device)); memset(&job, 0, sizeof(job));
	memset(&scheduler_fence, 0, sizeof(scheduler_fence)); memset(&hardware_fence, 0, sizeof(hardware_fence));
	memset(&destroy_fence, 0, sizeof(destroy_fence)); memset(&bo_list, 0, sizeof(bo_list));
	memset(&message, 0, sizeof(message));
	owner.id = 1; stranger.id = 2; device.runtime = true; device.accel_working = true;
	device.generation = 4; device.uvd.max_handles = 1;
	device.uvd.inst[0].ring.adev = &device; device.uvd.inst[0].ring.name = "fixture-uvd";
	device.uvd.inst[0].ring.sched.ring = &device.uvd.inst[0].ring;
	job.ring = &device.uvd.inst[0].ring; job.base.sched = &job.ring->sched;
	job.base.s_fence = &scheduler_fence; job.generation = device.generation;
	drm.adev = &device; events[0] = 0; event_count = 0;
	fault_calls = abort_calls = destroy_calls = wait_calls = fini_calls = 0;
	entered_calls = scheduled_calls = submitted_calls = resource_calls = 0;
	sleeps = retire_at_sleep = close_at_sleep = 0; now_ns = 0;
	fail_stage = 0; failure = -EINVAL; destroy_error = 0; wait_result = 1;
	destroy_fence.status = 1; parse_create = failed_after_admission = false;
	message.words[1] = 0; message.words[2] = 17; message.words[4] = 0;
	message.words[7] = 64; message.words[8] = 64;
}
static void owned_handle(void) { atomic_set(&device.uvd.handles[0], 17); device.uvd.filp[0] = &owner; }
static void order(char first, char second)
{
	const char *a = strchr(events, first), *b = strchr(events, second);
	CHECK(a && b && a < b);
}
static void test_cs(void)
{
	reset("cs-success"); parse_create = true;
	CHECK(amdgpu_cs_ioctl(&drm, NULL, &owner) == 0);
	CHECK(!strcmp(events, "ATLPVSQbUI"));
	CHECK(atomic_read(&device.uvd.handles[0]) == 17 && device.uvd.filp[0] == &owner);
	CHECK(!fault_calls && !abort_calls && job.leased && device.ps4_uvd_runtime.jobs.jobs == 1);
	for (int stage = 1; stage <= 9; ++stage) {
		reset("cs-injected-stage-failure"); fail_stage = stage;
		CHECK(amdgpu_cs_ioctl(&drm, NULL, &owner) == -EINVAL);
		CHECK(!device.ps4_uvd_runtime.session_lock.held && !bo_list.bo_list_mutex.held);
		CHECK(fini_calls == (unsigned)(stage != 1));
		CHECK(!device.ps4_uvd_runtime.jobs.jobs && !job.leased);
		CHECK(fault_calls == (unsigned)(stage >= 6));
		CHECK(abort_calls == (unsigned)(stage >= 6));
		if (stage >= 6) { order('F', 'R'); order('R', 'U'); }
	}
	reset("cs-busy-refusal"); device.ps4_uvd_runtime.session_lock.held = 1;
	CHECK(amdgpu_cs_ioctl(&drm, NULL, &owner) == -EBUSY);
	CHECK(!fault_calls && abort_calls == 1 && !strchr(events, 'P') && !strchr(events, 'U'));
	CHECK(device.ps4_uvd_runtime.session_lock.held == 1 && !device.ps4_uvd_runtime.jobs.jobs);
	reset("cs-failed-after-admission"); failed_after_admission = true;
	CHECK(amdgpu_cs_ioctl(&drm, NULL, &owner) == -ESHUTDOWN);
	CHECK(fault_calls == 1 && abort_calls == 1 && !strchr(events, 'P')); order('F', 'R');
	reset("cs-rejected-create-never-destroyed"); parse_create = true; fail_stage = 6;
	CHECK(amdgpu_cs_ioctl(&drm, NULL, &owner) == -EINVAL);
	CHECK(atomic_read(&device.uvd.handles[0]) == 17 && device.ps4_uvd_runtime.failed.value);
	amdgpu_uvd_free_handles(&device, &owner);
	CHECK(!destroy_calls && !wait_calls && !device.ps4_uvd_runtime.session_lock.held);
	reset("cs-non-runtime-correspondence"); device.runtime = false;
	CHECK(amdgpu_cs_ioctl(&drm, NULL, &owner) == 0);
	CHECK(!strcmp(events, "PVSQbI") && !fault_calls && !job.leased);
}
static int actual_message(unsigned type, struct drm_file *f)
{
	unsigned sizes[6] = {3556, ~0U, ~0U, 2048, ~0U, 992};
	struct amdgpu_cs_parser p = { .adev = &device, .filp = f };
	struct amdgpu_uvd_cs_ctx ctx = { &p, sizes };
	message.words[1] = type;
	int r = amdgpu_uvd_cs_msg(&ctx, &message, 0);
	CHECK(!message.mapped);
	return r;
}
static void test_messages(void)
{
	reset("message-create-valid"); CHECK(!actual_message(0, &owner));
	CHECK(atomic_read(&device.uvd.handles[0]) == 17 && device.uvd.filp[0] == &owner);
	CHECK(actual_message(0, &owner) == -EINVAL);
	for (unsigned i = 0; i < 5; ++i) {
		reset("message-create-policy-bounds");
		if (i == 0) message.words[4] = 7;
		if (i == 1) message.words[7] = 15;
		if (i == 2) message.words[8] = 15;
		if (i == 3) message.words[7] = 1921;
		if (i == 4) message.words[8] = 1089;
		CHECK(actual_message(0, &owner) == -EOPNOTSUPP);
		CHECK(!atomic_read(&device.uvd.handles[0]));
	}
	reset("message-create-upper-bound"); message.words[7] = 1920; message.words[8] = 1088;
	CHECK(!actual_message(0, &owner));
	reset("message-destroy-wrong-owner"); owned_handle();
	CHECK(actual_message(2, &stranger) == -EPERM && atomic_read(&device.uvd.handles[0]) == 17);
	CHECK(!actual_message(2, &owner) && !atomic_read(&device.uvd.handles[0]));
	reset("message-destroy-wrong-handle"); owned_handle(); message.words[2] = 18;
	CHECK(actual_message(2, &owner) == -EPERM && atomic_read(&device.uvd.handles[0]) == 17);
	reset("message-non-runtime-correspondence"); device.runtime = false; owned_handle();
	CHECK(!actual_message(2, &stranger) && !atomic_read(&device.uvd.handles[0]));
	for (unsigned i = 0; i < 9; ++i) {
		unsigned sizes[6] = {0};
		uint32_t words[1024] = {0};
		reset("decode-policy-pitch-and-bounds");
		words[4] = 0; words[6] = 64; words[7] = 64; words[9] = 1U << 20; words[28] = 256;
		if (i == 1) words[4] = 7;
		if (i == 2) words[4] = 16;
		if (i == 3) words[6] = 1921;
		if (i == 4) words[7] = 1089;
		if (i == 5) words[6] = 15;
		if (i == 6) words[28] = 63;
		if (i == 7) words[28] = 4097;
		if (i == 8) words[9] = 0;
		int r = amdgpu_uvd_cs_msg_decode(&device, words, sizes);
		if (i == 0) { CHECK(!r); CHECK(sizes[2] == 256 * 64 * 3 / 2); }
		else if (i <= 4) CHECK(r == -EOPNOTSUPP);
		else CHECK(r == -EINVAL);
	}
}
static void test_drain_and_close(void)
{
	reset("close-owned-idle"); owned_handle();
	amdgpu_uvd_free_handles(&device, &owner);
	CHECK(destroy_calls == 1 && wait_calls == 1 && destroy_fence.puts == 1);
	CHECK(!atomic_read(&device.uvd.handles[0]) && !device.uvd.filp[0]);
	CHECK(!strcmp(events, "LDWU") && !fault_calls);
	reset("close-pending-create-after-failed-flush"); owned_handle();
	device.ps4_uvd_runtime.jobs.jobs = 1; retire_at_sleep = 3;
	amdgpu_uvd_free_handles(&device, &owner);
	CHECK(sleeps == 3 && destroy_calls == 1 && !fault_calls);
	CHECK(!atomic_read(&device.uvd.handles[0]));
	reset("close-pending-create-drain-timeout"); owned_handle(); device.ps4_uvd_runtime.jobs.jobs = 1;
	amdgpu_uvd_free_handles(&device, &owner);
	CHECK(sleeps == 21 && fault_calls == 1 && !destroy_calls);
	CHECK(atomic_read(&device.uvd.handles[0]) == 17 && !device.ps4_uvd_runtime.session_lock.held);
	reset("close-fault-during-drain"); owned_handle(); device.ps4_uvd_runtime.jobs.jobs = 1; close_at_sleep = 2;
	amdgpu_uvd_free_handles(&device, &owner);
	CHECK(sleeps == 2 && fault_calls == 1 && !destroy_calls && !device.ps4_uvd_runtime.session_lock.held);
	reset("close-closed-jobs-with-idle-count"); owned_handle(); device.ps4_uvd_runtime.jobs.closed = true;
	amdgpu_uvd_free_handles(&device, &owner); CHECK(!destroy_calls);
	reset("close-unowned-does-not-drain"); owned_handle(); device.ps4_uvd_runtime.jobs.jobs = 1;
	amdgpu_uvd_free_handles(&device, &stranger); CHECK(!sleeps && !destroy_calls && !fault_calls);
	reset("close-destroy-admission-failure"); owned_handle(); destroy_error = -ENOMEM;
	amdgpu_uvd_free_handles(&device, &owner);
	CHECK(destroy_calls == 1 && !wait_calls && fault_calls == 1);
	CHECK(atomic_read(&device.uvd.handles[0]) == 17 && !device.ps4_uvd_runtime.session_lock.held);
	for (unsigned i = 0; i < 3; ++i) {
		reset("close-destroy-completion-refusal"); owned_handle();
		if (i == 0) wait_result = 0;
		if (i == 1) wait_result = -EINTR;
		if (i == 2) destroy_fence.status = -EIO;
		amdgpu_uvd_free_handles(&device, &owner);
		CHECK(fault_calls == 1 && destroy_fence.puts == 1 && atomic_read(&device.uvd.handles[0]) == 17);
		CHECK(device.uvd.filp[0] == &owner && !device.ps4_uvd_runtime.session_lock.held);
	}
	reset("close-non-runtime-correspondence"); device.runtime = false; owned_handle();
	amdgpu_uvd_free_handles(&device, &owner);
	CHECK(!strcmp(events, "DW") && !fault_calls && !atomic_read(&device.uvd.handles[0]));
}
static void test_run(void)
{
	reset("run-positive");
	CHECK(amdgpu_job_run(&job.base) == &hardware_fence);
	CHECK(!strcmp(events, "EHC") && entered_calls == 1 && scheduled_calls == 1 && submitted_calls == 1);
	CHECK(resource_calls == 1 && job.job_run_counter == 1 && !fault_calls);
	reset("run-finished-error-terminal"); scheduler_fence.finished.error = -ECANCELED;
	CHECK(amdgpu_job_run(&job.base) == NULL);
	CHECK(fault_calls == 1 && !entered_calls && !scheduled_calls && !submitted_calls);
	reset("run-generation-error-terminal"); job.generation = 3;
	CHECK(amdgpu_job_run(&job.base) == NULL);
	CHECK(fault_calls == 1 && scheduler_fence.finished.error == -ECANCELED && !entered_calls);
	reset("run-schedule-error-terminal"); fail_stage = 10; failure = -EIO;
	CHECK(amdgpu_job_run(&job.base) == ERR_PTR(-EIO));
	CHECK(fault_calls == 1 && scheduler_fence.finished.error == -EIO && submitted_calls == 1);
	reset("run-non-runtime-correspondence"); device.runtime = false; scheduler_fence.finished.error = -EIO;
	CHECK(amdgpu_job_run(&job.base) == NULL && !fault_calls && !scheduled_calls);
}
int main(void)
{
	test_cs(); test_messages(); test_drain_and_close(); test_run();
	printf("PASS actual-source sessions: %u cases, %u assertions\n", cases, assertions);
	return 0;
}
