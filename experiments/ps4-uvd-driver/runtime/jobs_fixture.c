/* SPDX-License-Identifier: MIT */
/* Copyright 2026 psX Linux contributors */
/* CPU policy fixture. No MMIO, device model, firmware or DMA acceptance. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t u32;
typedef uint64_t u64;
#define U64_MAX UINT64_MAX
#define GFP_KERNEL 0
#define READ_ONCE(x) (x)
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define check_add_overflow(a,b,p) __builtin_add_overflow(a,b,p)
#define check_mul_overflow(a,b,p) __builtin_mul_overflow(a,b,p)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))

static const char *case_name;
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d %s: %s\n", \
	__FILE__, __LINE__, case_name, #c); abort(); } } while (0)

struct list_head { struct list_head *next, *prev; };
static void INIT_LIST_HEAD(struct list_head *h) { h->next=h->prev=h; }
static bool list_empty(const struct list_head *h) { return h->next==h; }
static void list_add_tail(struct list_head *n, struct list_head *h)
{ n->prev=h->prev; n->next=h; h->prev->next=n; h->prev=n; }
static void list_del(struct list_head *n)
{ n->prev->next=n->next; n->next->prev=n->prev; }
static void list_del_init(struct list_head *n) { list_del(n); INIT_LIST_HEAD(n); }
static void list_move_tail(struct list_head *n, struct list_head *h)
{ list_del(n); list_add_tail(n,h); }
#define list_for_each_entry(p,h,m) \
	for (struct list_head *_n=(h)->next; \
	     _n!=(h) && ((p)=container_of(_n,__typeof__(*(p)),m),true); _n=_n->next)

typedef struct { bool held; } spinlock_t;
#define spin_lock_init(l) ((l)->held=false)
#define spin_lock_irqsave(l,f) do { (f)=0; CHECK(!(l)->held); (l)->held=true; } while(0)
#define spin_unlock_irqrestore(l,f) do { (void)(f); CHECK((l)->held); (l)->held=false; } while(0)
struct work_struct { void (*fn)(struct work_struct *); bool pending; };
static struct work_struct *workq[128];
static unsigned workq_len;
#define INIT_WORK(w,f) do { (w)->fn=(f); (w)->pending=false; } while(0)
static bool schedule_work(struct work_struct *w)
{
	if (w->pending) return false;
	CHECK(workq_len<128); w->pending=true; workq[workq_len++]=w; return true;
}
static void drain_work(void)
{
	unsigned steps=0;
	while (workq_len) {
		struct work_struct *w=workq[0];
		memmove(workq,workq+1,(--workq_len)*sizeof(workq[0]));
		w->pending=false; CHECK(++steps<256); w->fn(w);
	}
}

static int allocation_fail, live_allocations;
static void *kzalloc(size_t n, int flags)
{
	void *p; (void)flags;
	if (allocation_fail) { --allocation_fail; return NULL; }
	p=calloc(1,n); CHECK(p); ++live_allocations; return p;
}
static bool release_job_if_fixture(void *p);
static void kfree(void *p) { if(release_job_if_fixture(p)) return; if(p) { --live_allocations; CHECK(live_allocations>=0); free(p); } }

struct dma_fence;
struct dma_fence_cb {
	struct list_head node;
	void (*fn)(struct dma_fence *,struct dma_fence_cb *);
};
struct dma_fence {
	u64 context, seqno;
	int error, refs, add_error;
	bool signaled;
	struct list_head callbacks;
};
static struct dma_fence *dma_fence_get(struct dma_fence *f)
{ if(f) { CHECK(f->refs>0); ++f->refs; } return f; }
static void dma_fence_put(struct dma_fence *f) { if(f) CHECK(--f->refs>=1); }
static int dma_fence_get_status(struct dma_fence *f)
{ return !f->signaled ? 0 : f->error ? f->error : 1; }
static int dma_fence_add_callback(struct dma_fence *f,struct dma_fence_cb *cb,
	void (*fn)(struct dma_fence *,struct dma_fence_cb *))
{
	INIT_LIST_HEAD(&cb->node);
	if(f->add_error) return f->add_error;
	if(f->signaled) return -ENOENT;
	cb->fn=fn; list_add_tail(&cb->node,&f->callbacks); return 0;
}
static bool dma_fence_remove_callback(struct dma_fence *f,struct dma_fence_cb *cb)
{ bool r=!list_empty(&cb->node); (void)f; if(r) list_del_init(&cb->node); return r; }
/* Explicit CPU signal: it provides no hardware watermark. */
static void signal_cpu(struct dma_fence *f,int error)
{
	f->error=error; f->signaled=true;
	while(!list_empty(&f->callbacks)) {
		struct dma_fence_cb *cb=container_of(f->callbacks.next,struct dma_fence_cb,node);
		list_del_init(&cb->node); cb->fn(f,cb);
	}
}

#define AMDGPU_GPU_PAGE_SIZE 4096U
#define AMDGPU_RING_TYPE_UVD 3
#define CHIP_LIVERPOOL 17
#define AMDGPU_IB_POOL_MAX 3
#define AMDGPU_GEM_CREATE_VRAM_CONTIGUOUS (1ULL<<5)
#define AMDGPU_GEM_CREATE_ENCRYPTED (1ULL<<10)
#define AMDGPU_VM_PAGE_READABLE (1U<<1)
#define AMDGPU_VM_PAGE_WRITEABLE (1U<<2)
#define AMDGPU_VM_PAGE_PRT (1U<<4)
#define TTM_PL_VRAM 2
#define TTM_PL_TT 1
#define ttm_bo_type_device 0
#define ttm_bo_type_kernel 1
struct dma_resv { void *ctx; bool held; };
struct drm_gem_object { u64 size; bool imported; struct dma_resv *resv; };
struct ttm_resource { int mem_type; };
struct ttm_tt { bool userptr; };
struct ttm_buffer_object {
	struct drm_gem_object base;
	struct ttm_resource *resource;
	struct ttm_tt *ttm;
	u32 pin_count;
	int type;
};
struct amdgpu_bo {
	struct ttm_buffer_object tbo;
	struct amdgpu_bo *parent;
	void *kfd_bo;
	u64 flags;
	int refs,pin_error,gart_error,reserve_error;
	unsigned pins,unpins,refs_added,refs_dropped;
};
struct amdgpu_bo_va;
struct amdgpu_bo_va_mapping {
	struct list_head list;
	struct amdgpu_bo_va *bo_va;
	u64 start,last,offset;
	u32 flags;
};
struct amdgpu_bo_va {
	struct { struct amdgpu_bo *bo; } base;
	struct list_head valids,invalids;
	bool is_xgmi,userq_va_mapped;
	u32 queue_refcount;
};
struct drm_suballoc_manager { size_t size; };
struct drm_suballoc {
	struct drm_suballoc_manager *manager;
	size_t soffset,eoffset;
	struct dma_fence *fence;
	unsigned frees;
	struct dma_fence *released_against;
};
struct amdgpu_sa_manager {
	struct drm_suballoc_manager base;
	struct amdgpu_bo *bo;
	void *cpu_ptr;
};
struct amdgpu_device { int asic_type; struct amdgpu_sa_manager ib_pools[3]; };
struct drm_gpu_scheduler { int unused; };
struct amdgpu_ring_funcs { int type; };
struct amdgpu_ring {
	struct amdgpu_device *adev;
	const struct amdgpu_ring_funcs *funcs;
	struct drm_gpu_scheduler sched;
};
struct drm_sched_entity { struct { struct drm_gpu_scheduler *sched; } *rq; };
struct drm_sched_fence { struct dma_fence finished,scheduled; };
struct amdgpu_fence { struct dma_fence base; };
struct drm_sched_job {
	struct drm_sched_entity *entity;
	struct drm_gpu_scheduler *sched;
	struct drm_sched_fence *s_fence;
};
struct amdgpu_ib { struct drm_suballoc *sa_bo; u32 *ptr,length_dw; };
struct amdgpu_ps4_uvd_lease;
struct amdgpu_job {
	struct drm_sched_job base;
	void *vm,*gang_submit;
	struct amdgpu_ps4_uvd_lease *ps4_uvd_lease;
	u32 num_ibs;
	unsigned freed,scheduler_cleanups,sync_frees;
	bool expect_scheduler_cleanup;
	struct amdgpu_fence *hw_fence,*hw_vm_fence;
	unsigned explicit_sync;
	struct amdgpu_ib ibs[3];
};
struct amdgpu_bo_list_entry { struct amdgpu_bo *bo; struct amdgpu_bo_va *bo_va; };
struct amdgpu_bo_list {
	struct amdgpu_bo *gds_obj,*gws_obj,*oa_obj;
	u32 num_entries;
	bool bo_list_mutex;
	struct amdgpu_bo_list_entry entries[4];
};
#define amdgpu_bo_list_for_each_entry(e,l) \
	for(e=(l)->entries;e!=(l)->entries+(l)->num_entries;++e)
struct amdgpu_cs_parser {
	struct amdgpu_device *adev;
	u32 gang_size;
	struct amdgpu_job *jobs[4];
	struct amdgpu_bo_list *bo_list;
	struct amdgpu_bo *uf_bo;
	struct { void *ticket; } exec;
};
#define to_amdgpu_ring(s) container_of(s,struct amdgpu_ring,sched)
static struct amdgpu_ring *amdgpu_job_ring(struct amdgpu_job *j)
{ return to_amdgpu_ring(j->base.entity->rq->sched); }
static u64 amdgpu_bo_size(struct amdgpu_bo *b) { return b->tbo.base.size; }
static bool drm_gem_is_imported(struct drm_gem_object *o) { return o->imported; }
static bool amdgpu_ttm_tt_is_userptr(struct ttm_tt *t) { return t && t->userptr; }
static bool amdgpu_bo_encrypted(struct amdgpu_bo *b)
{ return (b->flags&AMDGPU_GEM_CREATE_ENCRYPTED)!=0; }
static u32 amdgpu_mem_type_to_domain(int m) { return m==TTM_PL_VRAM ? 4 : 2; }
#define drm_exec_ticket(e) ((e)->ticket)
#define dma_resv_locking_ctx(r) ((r)->ctx)
#define dma_resv_assert_held(r) CHECK((r)->held)
#define lockdep_assert_held(m) CHECK(*(m))
static int amdgpu_ttm_alloc_gart(struct ttm_buffer_object *t)
{ return container_of(t,struct amdgpu_bo,tbo)->gart_error; }
static int amdgpu_bo_pin(struct amdgpu_bo *b,u32 domain)
{
	(void)domain; CHECK(b->tbo.base.resv->held);
	if(b->pin_error) return b->pin_error;
	++b->tbo.pin_count; ++b->pins; return 0;
}
static void amdgpu_bo_unpin(struct amdgpu_bo *b)
{ CHECK(b->tbo.base.resv->held); CHECK(b->tbo.pin_count); --b->tbo.pin_count; ++b->unpins; }
static struct amdgpu_bo *amdgpu_bo_ref(struct amdgpu_bo *b)
{ CHECK(b->refs>0); ++b->refs; ++b->refs_added; return b; }
static void amdgpu_bo_unref(struct amdgpu_bo **p)
{ CHECK((*p)->refs>1); --(*p)->refs; ++(*p)->refs_dropped; *p=NULL; }
static int amdgpu_bo_reserve(struct amdgpu_bo *b,bool no_intr)
{
	(void)no_intr; if(b->reserve_error) return b->reserve_error;
	CHECK(!b->tbo.base.resv->held); b->tbo.base.resv->held=true; return 0;
}
static void amdgpu_bo_unreserve(struct amdgpu_bo *b)
{ CHECK(b->tbo.base.resv->held); b->tbo.base.resv->held=false; }
static size_t drm_suballoc_size(struct drm_suballoc *s) { return s->eoffset-s->soffset; }
static void *amdgpu_sa_bo_cpu_addr(struct drm_suballoc *s)
{ return (char *)container_of(s->manager,struct amdgpu_sa_manager,base)->cpu_ptr+s->soffset; }
static void amdgpu_ib_free(struct amdgpu_ib *ib,struct dma_fence *f)
{
	if(!ib->sa_bo) return;
	CHECK(ib->sa_bo->frees==0); ++ib->sa_bo->frees;
	ib->sa_bo->released_against=f; ib->sa_bo=NULL;
}
static bool dma_fence_was_initialized(struct dma_fence *f) { return f && f->refs>0; }
#define to_amdgpu_job(s) container_of(s,struct amdgpu_job,base)
static void drm_sched_job_cleanup(struct drm_sched_job *s)
{
	struct amdgpu_job *j=to_amdgpu_job(s);
	CHECK(j->scheduler_cleanups==0); ++j->scheduler_cleanups;
}
static void amdgpu_sync_free(unsigned *sync) { CHECK(*sync==0); ++*sync; }
void amdgpu_job_free(struct amdgpu_job *job);

/* JOBS_ACTUAL_HEADER */
/* JOBS_ACTUAL_SOURCE */
/* JOBS_ACTUAL_CORE_FREE */

/* Fixture setup below does not reimplement the component's decisions. */
static struct amdgpu_ps4_uvd_jobs *bridge;
struct amdgpu_ps4_uvd_jobs *amdgpu_ps4_uvd_runtime_jobs(struct amdgpu_ring *ring)
{ return bridge && bridge->ring==ring ? bridge : NULL; }
static struct amdgpu_job *fixture_jobs[3];
static bool release_job_if_fixture(void *p)
{
	if (!p) return false;
	for (unsigned i=0;i<3;++i) {
		struct amdgpu_job *j=fixture_jobs[i];
		if (p!=j) continue;
		CHECK(!j->freed && j->explicit_sync==1);
		CHECK(j->scheduler_cleanups==(unsigned)j->expect_scheduler_cleanup);
		++j->freed; return true;
	}
	return false;
}

struct fixture {
	struct amdgpu_ps4_uvd_jobs state;
	struct amdgpu_ps4_uvd_jobs_limits limits;
	struct amdgpu_device adev;
	struct amdgpu_ring ring;
	struct amdgpu_ring_funcs funcs;
	struct amdgpu_bo pool_bo,bos[3];
	struct ttm_resource resources[3];
	struct ttm_tt tt[3];
	struct dma_resv resvs[3];
	struct amdgpu_bo_va vas[3];
	struct amdgpu_bo_va_mapping maps[3],extra_map;
	struct amdgpu_bo_list list;
	struct amdgpu_cs_parser parser;
	struct amdgpu_job jobs[3];
	struct drm_suballoc spans[3][3];
	struct drm_sched_entity entities[3];
	struct { struct drm_gpu_scheduler *sched; } rq;
	struct drm_sched_fence sf[3];
	struct dma_fence hw[3];
	struct amdgpu_fence cleanup_hw[3],cleanup_vm[3];
	u32 pool_cpu[1024];
	unsigned closes;
	int close_error;
};
static void closed(void *p,int err)
{ struct fixture *f=p; ++f->closes; f->close_error=err; }
static void setup(struct fixture *f)
{
	memset(f,0,sizeof(*f)); allocation_fail=0; CHECK(!workq_len);
	f->limits=(struct amdgpu_ps4_uvd_jobs_limits){65536,32768,4096,16,8,3};
	f->adev.asic_type=CHIP_LIVERPOOL; f->funcs.type=AMDGPU_RING_TYPE_UVD;
	f->ring.adev=&f->adev; f->ring.funcs=&f->funcs;
	f->pool_bo.tbo.pin_count=1; f->adev.ib_pools[0].bo=&f->pool_bo;
	f->adev.ib_pools[0].cpu_ptr=f->pool_cpu; f->adev.ib_pools[0].base.size=sizeof(f->pool_cpu);
	f->rq.sched=&f->ring.sched;
	for(unsigned i=0;i<3;++i) {
		struct amdgpu_bo *b=&f->bos[i]; struct amdgpu_bo_va *v=&f->vas[i];
		b->tbo.base.size=4096; b->tbo.base.resv=&f->resvs[i]; b->refs=1;
		b->tbo.resource=&f->resources[i]; b->tbo.ttm=&f->tt[i];
		b->tbo.type=ttm_bo_type_device; f->resources[i].mem_type=TTM_PL_TT;
		f->resvs[i].held=true; f->resvs[i].ctx=f;
		v->base.bo=b; INIT_LIST_HEAD(&v->valids); INIT_LIST_HEAD(&v->invalids);
		f->maps[i].bo_va=v; f->maps[i].start=f->maps[i].last=256+i;
		f->maps[i].flags=AMDGPU_VM_PAGE_READABLE|AMDGPU_VM_PAGE_WRITEABLE;
		list_add_tail(&f->maps[i].list,&v->valids);
		f->list.entries[i]=(struct amdgpu_bo_list_entry){b,v};
		fixture_jobs[i]=&f->jobs[i];
		f->jobs[i].expect_scheduler_cleanup=true;
		f->jobs[i].hw_fence=&f->cleanup_hw[i]; f->cleanup_hw[i].base.refs=2;
		f->jobs[i].hw_vm_fence=&f->cleanup_vm[i]; f->cleanup_vm[i].base.refs=2;
		f->jobs[i].base.entity=&f->entities[i];
		f->entities[i].rq=(void *)&f->rq;
		f->jobs[i].base.sched=&f->ring.sched; f->jobs[i].base.s_fence=&f->sf[i];
		f->jobs[i].num_ibs=1;
		for(unsigned j=0;j<3;++j) {
			struct drm_suballoc *s=&f->spans[i][j];
			s->manager=&f->adev.ib_pools[0].base; s->soffset=i*1024+j*256; s->eoffset=s->soffset+256;
			f->jobs[i].ibs[j]=(struct amdgpu_ib){s,(u32 *)amdgpu_sa_bo_cpu_addr(s),32};
		}
		f->hw[i].context=42; f->hw[i].seqno=i+1; f->hw[i].refs=1;
		INIT_LIST_HEAD(&f->hw[i].callbacks);
	}
	f->list.num_entries=1; f->list.bo_list_mutex=true;
	f->parser.adev=&f->adev; f->parser.gang_size=1; f->parser.jobs[0]=&f->jobs[0];
	f->parser.bo_list=&f->list; f->parser.exec.ticket=f;
	CHECK(amdgpu_ps4_uvd_jobs_init(&f->state,&f->ring,42,&f->limits,closed,f)==0);
	bridge=&f->state;
}
static void unlock_bos(struct fixture *f)
{ for(unsigned i=0;i<3;++i) { f->resvs[i].held=false; f->resvs[i].ctx=NULL; } }
static int admit(struct fixture *f)
{ return amdgpu_ps4_uvd_jobs_admit(&f->state,&f->parser,&f->jobs[0]); }
static void bound(struct fixture *f)
{
	CHECK(admit(f)==0); unlock_bos(f);
	CHECK(amdgpu_ps4_uvd_jobs_enter(&f->jobs[0])==0);
	amdgpu_ps4_uvd_jobs_submitted(&f->jobs[0],&f->hw[0],0);
}
static void release_request(struct fixture *f)
{ CHECK(!amdgpu_ps4_uvd_jobs_job_free(&f->jobs[0],false)); drain_work(); }
static void expect_held(struct fixture *f)
{
	CHECK(f->state.jobs==1 && f->jobs[0].ps4_uvd_lease);
	CHECK(f->bos[0].tbo.pin_count==1 && f->bos[0].refs==2);
	CHECK(f->spans[0][0].frees==0 && f->jobs[0].freed==0);
}
static void expect_released(struct fixture *f)
{
	CHECK(!f->state.jobs && !f->state.objects && !f->state.bytes);
	CHECK(!f->jobs[0].ps4_uvd_lease && f->jobs[0].freed==1);
	CHECK(!f->bos[0].tbo.pin_count && f->bos[0].refs==1);
	CHECK(f->spans[0][0].frees==1);
}
/* Test-only destruction of CPU stub allocations AFTER all policy assertions.
 * There is intentionally no analogous production post-entry cleanup API.
 */
static void discard_fixture(struct fixture *f)
{
	drain_work();
	while(!list_empty(&f->state.leases)) {
		struct amdgpu_ps4_uvd_lease *l=container_of(f->state.leases.next,struct amdgpu_ps4_uvd_lease,link);
		if(l->hw) { dma_fence_remove_callback(l->hw,&l->hw_cb); dma_fence_put(l->hw); }
		list_del(&l->link); l->job->ps4_uvd_lease=NULL; kfree(l);
	}
	bridge=NULL; CHECK(!workq_len); CHECK(!live_allocations);
}

static void test_gate_and_init(void)
{
	struct fixture f; setup(&f);
	CHECK(amdgpu_ps4_uvd_jobs_admit(NULL,NULL,NULL)==0);
	CHECK(amdgpu_ps4_uvd_jobs_admit_kernel(NULL,NULL)==0);
	CHECK(amdgpu_ps4_uvd_jobs_admit_kernel_message(NULL,NULL,NULL)==0);
	CHECK(amdgpu_ps4_uvd_jobs_job_free(&f.jobs[0],false));
	CHECK(!amdgpu_ps4_uvd_jobs_defer_resources(&f.jobs[0]));
	bridge=NULL; CHECK(amdgpu_ps4_uvd_jobs_enter(&f.jobs[0])==0);
	CHECK(amdgpu_ps4_uvd_jobs_init(&f.state,&f.ring,42,&f.limits,closed,&f)==-EINVAL);
	struct amdgpu_ps4_uvd_jobs s={0}; struct amdgpu_ps4_uvd_jobs_limits l=f.limits;
	l.max_jobs=0; CHECK(amdgpu_ps4_uvd_jobs_init(&s,&f.ring,42,&l,closed,&f)==-EINVAL);
	l=f.limits; l.max_bytes=AMDGPU_PS4_UVD_JOBS_MAX_BYTES+1; CHECK(amdgpu_ps4_uvd_jobs_init(&s,&f.ring,42,&l,closed,&f)==-EINVAL);
	l=f.limits; l.max_job_ib_bytes=l.max_job_bytes+1; CHECK(amdgpu_ps4_uvd_jobs_init(&s,&f.ring,42,&l,closed,&f)==-EINVAL);
	f.funcs.type=0; CHECK(amdgpu_ps4_uvd_jobs_init(&s,&f.ring,42,&f.limits,closed,&f)==-EINVAL);
	f.funcs.type=AMDGPU_RING_TYPE_UVD; f.adev.asic_type=0; CHECK(amdgpu_ps4_uvd_jobs_init(&s,&f.ring,42,&f.limits,closed,&f)==-EINVAL);
	discard_fixture(&f);
}

static void test_admission_rollback(void)
{
	struct fixture f; setup(&f); f.list.num_entries=2;
	CHECK(admit(&f)==0); CHECK(f.state.objects==3 && f.state.bytes==8448);
	CHECK(f.bos[0].tbo.pin_count==1 && f.bos[1].tbo.pin_count==1);
	CHECK(amdgpu_ps4_uvd_jobs_defer_resources(&f.jobs[0]));
	CHECK(amdgpu_ps4_uvd_jobs_owns_bo(&f.jobs[0],&f.bos[0]));
	CHECK(!amdgpu_ps4_uvd_jobs_owns_bo(&f.jobs[0],&f.bos[2]));
	amdgpu_ps4_uvd_jobs_abort_reserved(&f.jobs[0]);
	CHECK(!f.state.jobs && !f.state.objects && !f.state.bytes);
	CHECK(!f.bos[0].tbo.pin_count && !f.bos[1].tbo.pin_count);
	CHECK(f.bos[0].refs==1 && f.bos[1].refs==1 && f.spans[0][0].frees==1);
	CHECK(amdgpu_ps4_uvd_jobs_job_free(&f.jobs[0],false)); discard_fixture(&f);
}
static void test_duplicates(void)
{
	struct fixture f; setup(&f); f.list.num_entries=2; f.list.entries[1]=f.list.entries[0];
	CHECK(admit(&f)==0); CHECK(f.state.objects==2 && f.state.bytes==4352);
	CHECK(f.bos[0].pins==1 && f.bos[0].refs_added==1);
	amdgpu_ps4_uvd_jobs_abort_reserved(&f.jobs[0]); discard_fixture(&f);
}
static void test_allocation_and_pin_failures(void)
{
	for(unsigned which=0;which<5;++which) {
		struct fixture f; setup(&f); f.list.num_entries=2;
		if(which==0) allocation_fail=1;
		if(which==1) f.bos[0].gart_error=-ENOMEM;
		if(which==2) f.bos[0].pin_error=-ENOMEM;
		if(which==3) f.bos[1].gart_error=-ENOMEM;
		if(which==4) f.bos[1].pin_error=-ENOMEM;
		CHECK(admit(&f)==-ENOMEM); CHECK(!f.state.jobs && !f.state.bytes && !f.state.objects);
		CHECK(!f.jobs[0].ps4_uvd_lease && !f.spans[0][0].frees);
		CHECK(!f.bos[0].tbo.pin_count && !f.bos[1].tbo.pin_count);
		CHECK(f.bos[0].refs==1 && f.bos[1].refs==1);
		if(which>=3) CHECK(f.bos[0].pins==1 && f.bos[0].unpins==1);
		discard_fixture(&f);
	}
}
static void test_admission_filters(void)
{
	for(unsigned which=0;which<18;++which) {
		struct fixture f; setup(&f);
		switch(which) {
		case 0: f.parser.gang_size=2; break;
		case 1: f.parser.uf_bo=&f.bos[1]; break;
		case 2: f.list.gds_obj=&f.bos[1]; break;
		case 3: f.jobs[0].gang_submit=&f; break;
		case 4: f.bos[0].parent=&f.bos[1]; break;
		case 5: f.bos[0].kfd_bo=&f; break;
		case 6: f.bos[0].tbo.resource=NULL; break;
		case 7: f.bos[0].tbo.type=ttm_bo_type_kernel; break;
		case 8: f.bos[0].tbo.base.imported=true; break;
		case 9: f.tt[0].userptr=true; break;
		case 10: f.bos[0].flags|=AMDGPU_GEM_CREATE_ENCRYPTED; break;
		case 11: f.resources[0].mem_type=0; break;
		case 12: f.resvs[0].ctx=NULL; break;
		case 13: f.vas[0].is_xgmi=true; break;
		case 14: f.vas[0].queue_refcount=1; break;
		case 15: f.vas[0].userq_va_mapped=true; break;
		case 16: f.list.entries[0].bo_va=NULL; break;
		case 17: f.parser.jobs[0]=&f.jobs[1]; break;
		}
		CHECK(admit(&f)==-EOPNOTSUPP); CHECK(!f.state.jobs && !f.bos[0].pins);
		discard_fixture(&f);
	}
}
static void test_mapping_filters(void)
{
	for(unsigned which=0;which<9;++which) {
		struct fixture f; setup(&f);
		switch(which) {
		case 0: list_del_init(&f.maps[0].list); break;
		case 1: f.extra_map=f.maps[0]; list_add_tail(&f.extra_map.list,&f.vas[0].invalids); break;
		case 2: f.maps[0].offset=4096; break;
		case 3: f.maps[0].last=f.maps[0].start-1; break;
		case 4: f.maps[0].start=f.maps[0].last=UINT64_MAX; break;
		case 5: f.maps[0].flags|=AMDGPU_VM_PAGE_PRT; break;
		case 6: f.maps[0].flags=AMDGPU_VM_PAGE_READABLE; break;
		case 7: ++f.maps[0].last; break;
		case 8: f.maps[0].bo_va=&f.vas[1]; break;
		}
		CHECK(admit(&f)==-EOPNOTSUPP); CHECK(!f.state.jobs && !f.bos[0].pins);
		discard_fixture(&f);
	}
	struct fixture f; setup(&f); list_move_tail(&f.maps[0].list,&f.vas[0].invalids);
	CHECK(admit(&f)==0); amdgpu_ps4_uvd_jobs_abort_reserved(&f.jobs[0]); discard_fixture(&f);
}
static void test_sa_filters(void)
{
	for(unsigned which=0;which<10;++which) {
		struct fixture f; setup(&f); int wanted=-EINVAL;
		switch(which) {
		case 0: f.jobs[0].num_ibs=0; break;
		case 1: f.jobs[0].ibs[0].sa_bo=NULL; break;
		case 2: f.jobs[0].ibs[0].ptr=NULL; break;
		case 3: f.jobs[0].ibs[0].length_dw=0; break;
		case 4: f.spans[0][0].fence=&f.hw[0]; break;
		case 5: f.spans[0][0].eoffset=0; break;
		case 6: f.jobs[0].num_ibs=2; f.jobs[0].ibs[1]=f.jobs[0].ibs[0]; break;
		case 7: f.pool_bo.tbo.pin_count=0; wanted=-EOPNOTSUPP; break;
		case 8: f.jobs[0].ibs[0].ptr++; wanted=-EOPNOTSUPP; break;
		case 9: f.jobs[0].ibs[0].length_dw=65; wanted=-E2BIG; break;
		}
		CHECK(admit(&f)==wanted); CHECK(!f.state.jobs && !f.bos[0].pins);
		discard_fixture(&f);
	}
}
static void test_budgets(void)
{
	for(unsigned which=0;which<6;++which) {
		struct fixture f; setup(&f);
		switch(which) {
		case 0: f.state.limits.max_job_bytes=4351; break;
		case 1: f.state.limits.max_job_objects=1; break;
		case 2: f.state.limits.max_job_ib_bytes=255; break;
		case 3: f.state.limits.max_bytes=4351; break;
		case 4: f.state.limits.max_objects=1; break;
		case 5: f.state.limits.max_jobs=1; CHECK(admit(&f)==0); f.parser.jobs[0]=&f.jobs[1]; break;
		}
		int rc=amdgpu_ps4_uvd_jobs_admit(&f.state,&f.parser,f.parser.jobs[0]);
		CHECK(rc==(which==2?-E2BIG:-ENOSPC));
		if(which==5) amdgpu_ps4_uvd_jobs_abort_reserved(&f.jobs[0]);
		CHECK(!f.state.jobs && !f.bos[0].tbo.pin_count); discard_fixture(&f);
	}
}
static void test_delayed_direct(void)
{
	struct fixture f; setup(&f); bound(&f); release_request(&f); expect_held(&f);
	CHECK(!f.state.closed);
	amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,1,1); drain_work(); expect_held(&f);
	signal_cpu(&f.hw[0],0); drain_work(); expect_released(&f);
	CHECK(f.spans[0][0].released_against==&f.hw[0]); CHECK(f.hw[0].refs==1); discard_fixture(&f);
}
static void test_cpu_signal_without_watermark(void)
{
	struct fixture f; setup(&f); bound(&f); signal_cpu(&f.hw[0],0);
	release_request(&f); expect_held(&f); CHECK(!f.state.closed);
	amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,0,1); drain_work(); expect_held(&f);
	amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,1,1); drain_work(); expect_released(&f); discard_fixture(&f);
}
static void test_late_binding(void)
{
	struct fixture f; setup(&f); CHECK(admit(&f)==0); unlock_bos(&f);
	CHECK(amdgpu_ps4_uvd_jobs_enter(&f.jobs[0])==0);
	amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,1,1); signal_cpu(&f.hw[0],0);
	amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],&f.hw[0],0);
	release_request(&f); expect_released(&f); discard_fixture(&f);
}
static void test_never_entered_cleanup(void)
{
	struct fixture f; setup(&f); CHECK(admit(&f)==0); unlock_bos(&f);
	amdgpu_ps4_uvd_jobs_fault(&f.state,-EIO); release_request(&f); expect_released(&f);
	CHECK(!f.spans[0][0].released_against && f.state.closed); discard_fixture(&f);
}
static void test_terminal_retention(void)
{
	for(unsigned which=0;which<4;++which) {
		struct fixture f; setup(&f); bound(&f);
		if(which==0) amdgpu_ps4_uvd_jobs_fault(&f.state,-ETIMEDOUT);
		if(which==1) signal_cpu(&f.hw[0],-EIO);
		if(which==2) f.sf[0].finished.error=-ECANCELED;
		if(which==3) CHECK(amdgpu_ps4_uvd_jobs_enter(&f.jobs[0])==-EALREADY);
		release_request(&f); expect_held(&f); CHECK(f.state.closed && f.state.quarantined==1 && f.closes==1);
		int first=f.state.error; amdgpu_ps4_uvd_jobs_fault(&f.state,-EINVAL);
		CHECK(f.closes==1 && f.state.error==first);
		amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,1,1); signal_cpu(&f.hw[0],0); drain_work(); expect_held(&f);
		for(unsigned i=0;i<3;++i) { f.resvs[i].held=true; f.resvs[i].ctx=&f; }
		f.parser.jobs[0]=&f.jobs[1]; CHECK(amdgpu_ps4_uvd_jobs_admit(&f.state,&f.parser,&f.jobs[1])==-ESHUTDOWN);
		discard_fixture(&f);
	}
}
static void test_submission_errors(void)
{
	for(unsigned which=0;which<6;++which) {
		struct fixture f; setup(&f); CHECK(admit(&f)==0); unlock_bos(&f);
		CHECK(amdgpu_ps4_uvd_jobs_enter(&f.jobs[0])==0);
		if(which==0) amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],NULL,-ENOMEM);
		if(which==1) amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],NULL,0);
		if(which==2) { f.hw[0].context=43; amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],&f.hw[0],0); }
		if(which==3) { f.hw[0].seqno=0; amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],&f.hw[0],0); }
		if(which==4) { f.hw[0].add_error=-EINVAL; amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],&f.hw[0],0); }
		if(which==5) { signal_cpu(&f.hw[0],-EIO); amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],&f.hw[0],0); }
		release_request(&f); expect_held(&f); CHECK(f.state.closed && f.closes==1); discard_fixture(&f);
	}
}
static void test_observation_errors(void)
{
	for(unsigned which=0;which<3;++which) {
		struct fixture f; setup(&f); bound(&f);
		if(which==0) amdgpu_ps4_uvd_jobs_observe_hw(&f.state,43,1,1);
		if(which==1) amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,2,1);
		if(which==2) { amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,1,1); amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,0,1); }
		release_request(&f); expect_held(&f); CHECK(f.state.closed); discard_fixture(&f);
	}
}
static void test_reservation_failure_retains_sa(void)
{
	struct fixture f; setup(&f); f.list.num_entries=2; bound(&f);
	f.bos[1].reserve_error=-EIO;
	amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,1,1); signal_cpu(&f.hw[0],0); release_request(&f);
	CHECK(f.state.closed && f.state.jobs==1 && f.state.bytes==8448);
	CHECK(f.bos[0].tbo.pin_count==0 && f.bos[1].tbo.pin_count==1);
	CHECK(!f.jobs[0].freed && !f.spans[0][0].frees); discard_fixture(&f);
}
static void test_entered_abort_and_unleased_guard(void)
{
	struct fixture f; setup(&f); bound(&f);
	amdgpu_ps4_uvd_jobs_abort_reserved(&f.jobs[0]); drain_work(); expect_held(&f); CHECK(f.state.closed);
	discard_fixture(&f); setup(&f);
	CHECK(amdgpu_ps4_uvd_jobs_enter(&f.jobs[0])==-EPERM && f.state.closed); discard_fixture(&f);
}
static void test_kernel_sa_and_message(void)
{
	struct fixture f; setup(&f); unlock_bos(&f);
	CHECK(amdgpu_ps4_uvd_jobs_admit_kernel(&f.state,&f.jobs[0])==0);
	CHECK(f.state.bytes==256 && f.state.objects==1);
	CHECK(amdgpu_ps4_uvd_jobs_enter(&f.jobs[0])==0);
	amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],&f.hw[0],0); release_request(&f);
	CHECK(!f.jobs[0].freed && !f.spans[0][0].frees);
	amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,1,1); signal_cpu(&f.hw[0],0); drain_work();
	CHECK(f.jobs[0].freed==1 && !f.state.jobs); discard_fixture(&f);
	setup(&f); unlock_bos(&f); f.bos[0].tbo.type=ttm_bo_type_kernel; f.bos[0].tbo.pin_count=1;
	CHECK(amdgpu_ps4_uvd_jobs_admit_kernel_message(&f.state,&f.jobs[0],&f.bos[0])==0);
	CHECK(f.state.bytes==4352 && f.state.objects==2 && f.bos[0].tbo.pin_count==2);
	/* Original kernel-message owner drops its pin/ref after send_msg. */
	CHECK(amdgpu_bo_reserve(&f.bos[0],true)==0); amdgpu_bo_unpin(&f.bos[0]); amdgpu_bo_unreserve(&f.bos[0]);
	CHECK(amdgpu_ps4_uvd_jobs_enter(&f.jobs[0])==0);
	amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],&f.hw[0],0);
	amdgpu_ps4_uvd_jobs_fault(&f.state,-ETIMEDOUT); release_request(&f); expect_held(&f); discard_fixture(&f);
}
static void test_kernel_message_failures(void)
{
	for(unsigned which=0;which<7;++which) {
		struct fixture f; setup(&f); unlock_bos(&f);
		f.bos[0].tbo.type=ttm_bo_type_kernel; f.bos[0].tbo.pin_count=1;
		int wanted=-EOPNOTSUPP;
		if(which==0) f.bos[0].tbo.pin_count=0;
		if(which==1) f.bos[0].tbo.base.imported=true;
		if(which==2) { f.bos[0].reserve_error=-ENOMEM; wanted=-ENOMEM; }
		if(which==3) { f.bos[0].pin_error=-ENOMEM; wanted=-ENOMEM; }
		if(which==4) { allocation_fail=1; wanted=-ENOMEM; }
		if(which==5) { f.state.limits.max_job_objects=1; wanted=-ENOSPC; }
		if(which==6) { f.jobs[0].ibs[0].ptr=NULL; wanted=-EINVAL; }
		CHECK(amdgpu_ps4_uvd_jobs_admit_kernel_message(&f.state,&f.jobs[0],&f.bos[0])==wanted);
		CHECK(!f.state.jobs && !f.state.bytes && !f.jobs[0].ps4_uvd_lease && f.bos[0].refs==1);
		CHECK(!f.resvs[0].held); discard_fixture(&f);
	}
}
static void test_shared_bo_leases(void)
{
	struct fixture f; setup(&f); CHECK(admit(&f)==0);
	for(unsigned i=0;i<3;++i) { f.resvs[i].held=true; f.resvs[i].ctx=&f; }
		f.parser.jobs[0]=&f.jobs[1]; CHECK(amdgpu_ps4_uvd_jobs_admit(&f.state,&f.parser,&f.jobs[1])==0);
	CHECK(f.state.jobs==2 && f.state.bytes==8704 && f.bos[0].tbo.pin_count==2 && f.bos[0].refs==3);
	unlock_bos(&f);
	for(unsigned i=0;i<2;++i) { CHECK(amdgpu_ps4_uvd_jobs_enter(&f.jobs[i])==0); amdgpu_ps4_uvd_jobs_submitted(&f.jobs[i],&f.hw[i],0); CHECK(!amdgpu_ps4_uvd_jobs_job_free(&f.jobs[i],false)); }
	amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,1,2); signal_cpu(&f.hw[0],0); drain_work();
	CHECK(f.jobs[0].freed==1 && f.jobs[1].freed==0 && f.state.jobs==1);
	CHECK(f.bos[0].tbo.pin_count==1 && f.bos[0].refs==2 && !f.spans[1][0].frees);
	amdgpu_ps4_uvd_jobs_fault(&f.state,-EIO); drain_work();
	CHECK(f.bos[0].tbo.pin_count==1 && !f.spans[1][0].frees); discard_fixture(&f);
}

static void test_dequeued_scheduler_cleanup(void)
{
	struct fixture f; setup(&f); bound(&f);
	/* Real drm_sched_entity_pop_job clears this before run/free_cb. */
	f.jobs[0].base.entity=NULL;
	amdgpu_job_free_cb(&f.jobs[0].base); drain_work(); expect_held(&f);
	CHECK(!f.jobs[0].scheduler_cleanups && !f.jobs[0].explicit_sync);
	amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,1,1); signal_cpu(&f.hw[0],0); drain_work();
	expect_released(&f); CHECK(f.jobs[0].scheduler_cleanups==1 && f.jobs[0].explicit_sync==1);
	discard_fixture(&f);
}
static void test_direct_cleanup_without_entity(void)
{
	struct fixture f; setup(&f); bound(&f);
	f.jobs[0].base.entity=NULL; f.jobs[0].expect_scheduler_cleanup=false;
	amdgpu_job_free(&f.jobs[0]); drain_work(); expect_held(&f);
	amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,1,1); signal_cpu(&f.hw[0],0); drain_work();
	expect_released(&f); CHECK(f.jobs[0].scheduler_cleanups==0 && f.jobs[0].explicit_sync==1);
	discard_fixture(&f);
}
static void test_early_resource_release_is_deferred(void)
{
	struct fixture f; setup(&f); CHECK(admit(&f)==0);
	amdgpu_job_free_resources(&f.jobs[0]); CHECK(!f.spans[0][0].frees);
	unlock_bos(&f); CHECK(amdgpu_ps4_uvd_jobs_enter(&f.jobs[0])==0);
	amdgpu_job_free_resources(&f.jobs[0]); CHECK(!f.spans[0][0].frees);
	amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],&f.hw[0],0);
	signal_cpu(&f.hw[0],0);
	amdgpu_job_free_resources(&f.jobs[0]); CHECK(!f.spans[0][0].frees);
	release_request(&f); expect_held(&f);
	amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,1,1); drain_work();
	expect_released(&f); discard_fixture(&f);
}
static void test_misordered_submission_retains(void)
{
	for(unsigned which=0;which<5;++which) {
		struct fixture f; setup(&f); CHECK(admit(&f)==0); unlock_bos(&f);
		if(which!=0) CHECK(amdgpu_ps4_uvd_jobs_enter(&f.jobs[0])==0);
		if(which==0) amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],&f.hw[0],0);
		if(which==1) {
			amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],&f.hw[0],0);
			amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],&f.hw[0],0);
		}
		/* which 2 omits submitted entirely: reaper must close, never free. */
		if(which==3) amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],&f.hw[0],1);
		if(which==4) amdgpu_ps4_uvd_jobs_submitted(&f.jobs[0],(void *)(uintptr_t)-EIO,0);
		if(which==0) {
			/* Never entered: no DMA, even the closed queue can clean up. */
			release_request(&f); expect_released(&f); CHECK(f.state.closed);
		} else {
			release_request(&f); expect_held(&f); CHECK(f.state.closed);
		}
		discard_fixture(&f);
	}
}
static void test_cancelled_before_run_closes_queue(void)
{
	struct fixture f; setup(&f); CHECK(admit(&f)==0);
	/* A separate accepted job has already entered hardware. */
	f.parser.jobs[0]=&f.jobs[1];
	CHECK(amdgpu_ps4_uvd_jobs_admit(&f.state,&f.parser,&f.jobs[1])==0);
	unlock_bos(&f);
	CHECK(amdgpu_ps4_uvd_jobs_enter(&f.jobs[1])==0);
	amdgpu_ps4_uvd_jobs_submitted(&f.jobs[1],&f.hw[1],0);
	f.jobs[1].base.entity=NULL;
	amdgpu_job_free_cb(&f.jobs[1].base); drain_work();
	/* sched_entity cancellation calls free_cb without ever running job 0. */
	f.jobs[0].base.entity=NULL; f.sf[0].finished.error=-ESRCH;
	amdgpu_job_free_cb(&f.jobs[0].base);
	CHECK(f.state.closed && f.state.error==-ESRCH && f.closes==1);
	CHECK(f.state.quarantined==1); drain_work();
	CHECK(f.jobs[0].freed==1 && f.jobs[0].scheduler_cleanups==1);
	CHECK(f.spans[0][0].frees==1 && !f.spans[0][0].released_against);
	CHECK(!f.jobs[1].freed && !f.spans[1][0].frees && f.state.jobs==1);
	CHECK(f.bos[0].tbo.pin_count==1 && f.bos[0].refs==2);
	/* A late successful fence cannot reopen or drain the terminal queue. */
	amdgpu_ps4_uvd_jobs_observe_hw(&f.state,42,2,2); signal_cpu(&f.hw[1],0); drain_work();
	CHECK(f.state.jobs==1 && !f.jobs[1].freed && !f.spans[1][0].frees);
	CHECK(amdgpu_ps4_uvd_jobs_admit_kernel(&f.state,&f.jobs[2])==-ESHUTDOWN);
	discard_fixture(&f);
	/* With no other job, jobs==0 still must not permit file-close DESTROY. */
	setup(&f); CHECK(admit(&f)==0); unlock_bos(&f);
	f.jobs[0].base.entity=NULL; f.sf[0].finished.error=-ESRCH;
	amdgpu_job_free_cb(&f.jobs[0].base); drain_work(); expect_released(&f);
	CHECK(f.state.closed && f.state.error==-ESRCH && !f.state.jobs);
	CHECK(amdgpu_ps4_uvd_jobs_admit_kernel(&f.state,&f.jobs[1])==-ESHUTDOWN);
	discard_fixture(&f);
}

int main(void)
{
	struct { const char *name; void (*fn)(void); } cases[]={
		{"gate-and-init",test_gate_and_init},
		{"admission-rollback",test_admission_rollback},
		{"duplicate-bo",test_duplicates},
		{"allocation-gart-pin-failures",test_allocation_and_pin_failures},
		{"admission-filters",test_admission_filters},
		{"mapping-filters",test_mapping_filters},
		{"sa-filters",test_sa_filters},
		{"budgets",test_budgets},
		{"delayed-direct",test_delayed_direct},
		{"cpu-signal-without-watermark",test_cpu_signal_without_watermark},
		{"already-signaled-before-binding",test_late_binding},
		{"never-entered-cleanup",test_never_entered_cleanup},
		{"terminal-retention",test_terminal_retention},
		{"submission-errors",test_submission_errors},
		{"observation-errors",test_observation_errors},
		{"retirement-reserve-failure",test_reservation_failure_retains_sa},
		{"entered-abort-unleased",test_entered_abort_and_unleased_guard},
		{"kernel-sa-and-message",test_kernel_sa_and_message},
		{"kernel-message-failures",test_kernel_message_failures},
		{"shared-bo-leases",test_shared_bo_leases},
		{"dequeued-scheduler-cleanup",test_dequeued_scheduler_cleanup},
		{"direct-cleanup-without-entity",test_direct_cleanup_without_entity},
		{"early-free-resources",test_early_resource_release_is_deferred},
		{"misordered-submission",test_misordered_submission_retains},
		{"cancelled-before-run-closes-queue",test_cancelled_before_run_closes_queue},
	};
	for(unsigned i=0;i<sizeof(cases)/sizeof(cases[0]);++i) {
		case_name=cases[i].name; cases[i].fn();
	}
	printf("PASS actual-source jobs: %zu cases\n",sizeof(cases)/sizeof(cases[0]));
	return 0;
}
