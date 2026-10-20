/* SPDX-License-Identifier: MIT */
/* Host fixture: actual extracted mapper functions, synthetic BO/GART storage.
 * This models neither UVD, device caches, hardware page walks nor DMA.
 */
#include <assert.h>
#include <errno.h>
#include <setjmp.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint64_t u64;
#define __iomem
#define AMDGPU_GPU_PAGE_SHIFT 12U
#define AMDGPU_GPU_PAGE_SIZE 4096ULL
#define AMDGPU_GPU_PAGE_MASK 4095ULL
#define AMDGPU_PTE_VALID 1ULL
#define AMDGPU_PTE_SYSTEM 2ULL
#define AMDGPU_PTE_READABLE 4ULL
#define AMDGPU_PTE_WRITEABLE 8ULL
#define TTM_PL_VRAM 1U
#define TTM_PL_TT 2U
#define ttm_bo_type_kernel 3U
#define TTM_TT_FLAG_EXTERNAL 1U
#define GFP_KERNEL 0
#define CONFIG_DRM_AMDGPU 1
#define CONFIG_X86_PS4 1
#define IS_BUILTIN(x) (x)
#define IS_ENABLED(x) (x)
#define CHIP_LIVERPOOL 1U
#define CHIP_GLADIUS 2U
#define check_add_overflow(a,b,p) __builtin_add_overflow((a),(b),(p))
#define check_mul_overflow(a,b,p) __builtin_mul_overflow((a),(b),(p))
#define PAGE_SIZE 4096U

@CONSTANTS@
#define FRAME_CAPABLE @FRAME_CAPABLE@

struct fake_page { unsigned long pfn; };
struct ttm_tt {
	u64 *dma_address;
	struct fake_page **pages;
	unsigned int page_flags, num_pages;
	bool populated;
	u64 flags;
};
struct resource { unsigned int mem_type; };
struct amdgpu_bo {
	struct {
		struct resource *resource;
		unsigned int pin_count, type;
		struct ttm_tt *ttm;
	} tbo;
	u64 bytes;
};
struct amdgpu_device {
	struct { u64 gart_start, gart_end, vram_start, vram_end, pte_addr_mask; } gmc;
	struct { void *ptr; u64 num_gpu_pages, table_size; } gart;
	unsigned int asic_type;
};
struct amdgpu_ps4_uvd_ring { void *cpu; size_t bytes; u64 private_va; };
struct ps4_canary_bo { struct amdgpu_bo *bo; u64 gpu; void *cpu; };
struct ps4_canary_page { u64 dma, pte; unsigned long pfn; unsigned int slot; };
struct ps4_canary_state {
	struct amdgpu_device *adev;
	struct ps4_canary_bo pd, pt, src, dst, ib, r1, uvd_rb, uvd_ib;
	struct ps4_canary_page *pages;
	unsigned int mapped_pages;
	void *fence;
	u64 pd_size, pt_size, leaf_index;
	bool submitted, ready_mode, ready_layout, prepare_only, frame_mode;
};
static struct ps4_canary_state *ps4_canary_retained;
static bool raw_frame, raw_create, raw_token, raw_ib, raw_ready, options_valid;
static bool ps4_map_canary, ps4_uvd_live_prepare;
static bool fail_allocation;
static size_t allocation_elements;
static unsigned int checks;
#define CHECK(x) do { ++checks; if (!(x)) { \
	fprintf(stderr, "check failed line %d: %s\n", __LINE__, #x); exit(2); \
} } while (0)

static u64 amdgpu_bo_size(const struct amdgpu_bo *bo) { return bo->bytes; }
static u64 amdgpu_gmc_pd_addr(const struct amdgpu_bo *bo) { (void)bo; return 0x10000000ULL; }
static bool ttm_tt_is_populated(const struct ttm_tt *ttm) { return ttm->populated; }
static unsigned long page_to_pfn(const struct fake_page *page) { return page->pfn; }
static u64 amdgpu_ttm_tt_pte_flags(struct amdgpu_device *adev, struct ttm_tt *ttm,
				 struct resource *resource)
{ (void)adev; (void)resource; return ttm->flags; }
static u64 readq(const void *ptr) { u64 v; memcpy(&v, ptr, sizeof(v)); return v; }
static void writeq(u64 v, void *ptr) { memcpy(ptr, &v, sizeof(v)); }
static void *kcalloc(size_t n, size_t bytes, unsigned int flags)
{
	(void)flags;
	allocation_elements = n;
	return fail_allocation ? NULL : calloc(n, bytes);
}
static bool amdgpu_ps4_uvd_decode_frame_requested(void) { return raw_frame; }
static bool amdgpu_ps4_uvd_decode_frame_options_valid(struct amdgpu_device *adev)
{ (void)adev; return options_valid; }
static bool amdgpu_ps4_uvd_message_create_requested(void) { return raw_create; }
static bool amdgpu_ps4_uvd_token_requested(void) { return raw_token; }
static bool amdgpu_ps4_uvd_ib_data_requested(void) { return raw_ib; }
static bool amdgpu_ps4_uvd_ready_enabled(struct amdgpu_device *adev)
{ (void)adev; return raw_ready; }
static bool amdgpu_ps4_live_prepare_enabled(struct amdgpu_device *adev)
{ (void)adev; return ps4_uvd_live_prepare; }

@FUNCTIONS@

struct fixture {
	struct amdgpu_device adev;
	struct ps4_canary_state state;
	struct resource resources[8];
	struct amdgpu_bo bos[8];
};

static void init_bo(struct fixture *f, struct ps4_canary_bo *bo, unsigned int id,
		    u64 bytes, bool vram, unsigned int native_index)
{
	struct amdgpu_bo *b = &f->bos[id];
	unsigned int i, count = (unsigned int)(bytes >> AMDGPU_GPU_PAGE_SHIFT);
	b->bytes = bytes;
	b->tbo.resource = &f->resources[id];
	b->tbo.resource->mem_type = vram ? TTM_PL_VRAM : TTM_PL_TT;
	b->tbo.pin_count = 1;
	b->tbo.type = ttm_bo_type_kernel;
	bo->bo = b;
	bo->cpu = calloc(1, (size_t)bytes);
	CHECK(bo->cpu);
	bo->gpu = vram ? f->adev.gmc.vram_start + (u64)native_index * 4096U :
		f->adev.gmc.gart_start + (u64)native_index * 4096U;
	if (vram)
		return;
	b->tbo.ttm = calloc(1, sizeof(*b->tbo.ttm));
	CHECK(b->tbo.ttm);
	b->tbo.ttm->num_pages = count;
	b->tbo.ttm->populated = true;
	b->tbo.ttm->flags = 15;
	b->tbo.ttm->dma_address = calloc(count, sizeof(u64));
	b->tbo.ttm->pages = calloc(count, sizeof(struct fake_page *));
	CHECK(b->tbo.ttm->dma_address && b->tbo.ttm->pages);
	for (i = 0; i < count; ++i) {
		b->tbo.ttm->dma_address[i] = 0x200000000ULL +
			(u64)(native_index + i) * 4096U;
		b->tbo.ttm->pages[i] = calloc(1, sizeof(struct fake_page));
		CHECK(b->tbo.ttm->pages[i]);
		b->tbo.ttm->pages[i]->pfn = 0x400000U + native_index + i;
		writeq(b->tbo.ttm->dma_address[i] | b->tbo.ttm->flags,
		       (char *)f->adev.gart.ptr + (native_index + i) * sizeof(u64));
	}
}

static void init_fixture(struct fixture *f, bool frame, bool prepare)
{
	struct ps4_canary_state *s;
	memset(f, 0, sizeof(*f));
	f->adev.asic_type = CHIP_LIVERPOOL;
	f->adev.gmc.gart_start = 0x1000000000ULL;
	f->adev.gmc.gart_end = f->adev.gmc.gart_start + 4096U * 4096U - 1;
	f->adev.gmc.vram_start = 0x10000000ULL;
	f->adev.gmc.vram_end = 0x1fffffffULL;
	f->adev.gmc.pte_addr_mask = 0x000000fffffff000ULL;
	f->adev.gart.num_gpu_pages = 4096;
	f->adev.gart.table_size = 4096 * sizeof(u64);
	f->adev.gart.ptr = calloc(1, (size_t)f->adev.gart.table_size);
	CHECK(f->adev.gart.ptr);
	s = &f->state;
	s->adev = &f->adev;
	s->pd_size = 4096;
	s->pt_size = 8192;
	s->ready_mode = !prepare;
	s->ready_layout = true;
	s->prepare_only = prepare;
	s->frame_mode = frame;
	s->submitted = true;
	s->fence = s;
	init_bo(f, &s->pd, 0, s->pd_size, true, 0);
	init_bo(f, &s->pt, 1, s->pt_size, true, 1);
	init_bo(f, &s->r1, 2, PS4_READY_R_BYTES, false, 0);
	init_bo(f, &s->uvd_rb, 3, 4096, false, 1000);
	init_bo(f, &s->uvd_ib, 4, frame ? 0x40000 : 0x20000, false, 1100);
	init_bo(f, &s->src, 5, 4096, false, 2000);
	init_bo(f, &s->dst, 6, 4096, false, 2100);
	init_bo(f, &s->ib, 7, 4096, false, 2200);
	ps4_canary_retained = s;
	ps4_map_canary = ps4_uvd_live_prepare = false;
	raw_frame = frame;
	raw_create = raw_token = raw_ib = false;
	raw_ready = !prepare;
	options_valid = true;
	fail_allocation = false;
}

static void destroy_fixture(struct fixture *f)
{
	struct ps4_canary_bo *owned[] = { &f->state.pd, &f->state.pt,
		&f->state.r1, &f->state.uvd_rb, &f->state.uvd_ib,
		&f->state.src, &f->state.dst, &f->state.ib };
	unsigned int i, j;
	for (i = 0; i < 8; ++i) {
		struct ttm_tt *ttm = f->bos[i].tbo.ttm;
		if (ttm) {
			for (j = 0; j < ttm->num_pages; ++j)
				free(ttm->pages[j]);
			free(ttm->pages);
			free(ttm->dma_address);
			free(ttm);
		}
		free(owned[i]->cpu);
	}
	free(f->state.pages);
	free(f->adev.gart.ptr);
	ps4_canary_retained = NULL;
}

static void emit_actual_private_pt(struct ps4_canary_state *s)
{
	unsigned int i;
	@EMIT@
}

static bool extracted_frame_admission(struct amdgpu_device *adev)
{
	(void)adev;
	return @ADMISSION@;
}

static jmp_buf parked;
static const char *park_stage;
static void ps4_canary_park(struct amdgpu_device *adev, const char *stage, int error)
{
	(void)adev;
	CHECK(error == -EINVAL);
	park_stage = stage;
	longjmp(parked, 1);
}
static struct amdgpu_ps4_uvd_ring captured_ring, captured_work;
static bool captured_ring_present, captured_work_present;
static void amdgpu_ps4_uvd_ready_run(struct amdgpu_device *adev, void *r1,
				  size_t backing, u64 pd,
				  const struct amdgpu_ps4_uvd_ring *rb,
				  const struct amdgpu_ps4_uvd_ring *ib)
{
	CHECK(adev == ps4_canary_retained->adev);
	CHECK(r1 == ps4_canary_retained->r1.cpu);
	CHECK(backing == PS4_READY_R_BYTES && pd == 0x10000000);
	captured_ring_present = rb != NULL;
	captured_work_present = ib != NULL;
	if (rb)
		captured_ring = *rb;
	if (ib)
		captured_work = *ib;
}
static void dispatch_actual_borrow_call(struct ps4_canary_state *s)
{
	struct amdgpu_device *adev = s->adev;
	struct amdgpu_ps4_uvd_ring token_ring = { 0 }, message_backing = { 0 };
	struct amdgpu_ps4_uvd_ring ib_page = { 0 };
#if FRAME_CAPABLE
	struct amdgpu_ps4_uvd_ring frame_backing = { 0 };
#endif
	@DISPATCH@
}

/* Keep the jump target in one dispatch call, outside the leaf iteration.
 * An unexpected park terminates here without resuming a loop whose automatic
 * variables were modified after setjmp.
 */
static void dispatch_without_park(struct ps4_canary_state *s)
{
	if (setjmp(parked))
		CHECK(false);
	dispatch_actual_borrow_call(s);
}

static u64 inventory_hash(const struct ps4_canary_state *s)
{
	u64 h = 1469598103934665603ULL;
	unsigned int i;
	for (i = 0; i < s->mapped_pages; ++i) {
		h = (h ^ s->pages[i].dma) * 1099511628211ULL;
		h = (h ^ s->pages[i].pte) * 1099511628211ULL;
		h = (h ^ s->pages[i].pfn) * 1099511628211ULL;
		h = (h ^ s->pages[i].slot) * 1099511628211ULL;
	}
	return h;
}

static void test_mapping(bool frame, bool prepare)
{
	struct fixture f;
	struct ps4_canary_state *s = &f.state;
	struct amdgpu_ps4_uvd_ring ring, work;
	u64 ptes[3];
	unsigned int i, work_pages = frame ? 64U : 32U;
	init_fixture(&f, frame, prepare);
	CHECK(ps4_canary_ready_pages(&f.adev, s, ptes) == 0);
	CHECK(s->mapped_pages == 780U + work_pages);
	CHECK(allocation_elements == (FRAME_CAPABLE ? 844U : 812U));
	for (i = 0; i < s->mapped_pages; ++i) {
		unsigned int expected_slot = i < 777 ? i :
			i < 777 + work_pages ? i + 1 : 1021 + i - (777 + work_pages);
		CHECK(s->pages[i].slot == expected_slot);
		CHECK(s->pages[i].pte == (s->pages[i].dma | 15));
	}
	for (i = 0; i < 3; ++i)
		CHECK(ptes[i] == s->pages[s->mapped_pages - 3 + i].pte);
	emit_actual_private_pt(s);
	CHECK(readq((char *)s->pt.cpu + 777 * sizeof(u64)) == 0);
	CHECK(readq((char *)s->pt.cpu + (778 + work_pages) * sizeof(u64)) == 0);
	CHECK(readq((char *)s->pt.cpu + 1020 * sizeof(u64)) == 0);
	CHECK(ps4_canary_token_ring(s, &ring) == !prepare);
	CHECK(ps4_canary_ib_data_page(s, &work) == (!prepare && !frame));
	if (!prepare && !frame) {
		CHECK(work.bytes == 4096 && work.private_va == 0x30030a000ULL);
		CHECK(work.cpu == s->uvd_ib.cpu);
	}
	CHECK(ps4_canary_message_backing(s, &work) == (!prepare && !frame));
	if (!prepare && !frame)
		CHECK(work.bytes == 0x20000 && work.private_va == 0x30030a000ULL);
	if (!prepare) {
		CHECK(ring.bytes == 4096 && ring.private_va == 0x300308000ULL);
		CHECK(ring.cpu == s->uvd_rb.cpu);
	}
#if FRAME_CAPABLE
	CHECK(ps4_canary_work_bytes(s) == (frame ? 0x40000U : 0x20000U));
	CHECK(ps4_canary_expected_pages(s) == 780U + work_pages);
	CHECK(ps4_canary_work_end_slot(s) == 778U + work_pages);
	CHECK(ps4_canary_frame_pt_valid(s) == (frame && !prepare));
	CHECK(ps4_canary_frame_backing(s, &work) == (frame && !prepare));
	if (frame && !prepare)
		CHECK(work.bytes == 0x40000 && work.private_va == 0x30030a000ULL);
#endif
	if (!prepare) {
		unsigned int leaf;
		for (leaf = 0; leaf < (frame ? 1U : 4U); ++leaf) {
			raw_frame = frame;
			raw_token = !frame && leaf == 1;
			raw_ib = !frame && leaf == 2;
			raw_create = !frame && leaf == 3;
			dispatch_without_park(s);
			CHECK(captured_ring_present == (frame || leaf == 1 || leaf == 3));
			CHECK(captured_work_present == (frame || leaf == 2 || leaf == 3));
			if (captured_ring_present)
				CHECK(captured_ring.bytes == 4096);
			if (captured_work_present)
				CHECK(captured_work.bytes == (frame ? 0x40000U : leaf == 2 ? 4096U : 0x20000U));
		}
	}
	printf("mapping frame=%u prepare=%u pages=%u work=%llu hash=%016llx\n",
	       frame, prepare, s->mapped_pages, (unsigned long long)s->uvd_ib.bo->bytes,
	       (unsigned long long)inventory_hash(s));
	destroy_fixture(&f);
}

static void test_add_bounds(bool frame)
{
	struct fixture f;
	struct ps4_canary_state *s = &f.state;
	struct ps4_canary_bo *bo = &s->src;
	struct ttm_tt *ttm;
	u64 bytes = 4096, saved, *native;
	unsigned int expected = frame ? 844U : 812U;
	init_fixture(&f, frame, false);
	ttm = bo->bo->tbo.ttm;
	s->pages = calloc(844, sizeof(*s->pages));
	CHECK(s->pages);
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1023) == 0);
	CHECK(s->mapped_pages == 1 && s->pages[0].slot == 1023);
	s->mapped_pages = 0;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1024) == -ERANGE);
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, &s->uvd_ib,
				      s->uvd_ib.bo->bytes, 1023) == -ERANGE);
	s->mapped_pages = expected;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -ERANGE);
	s->mapped_pages = expected + 1;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -ERANGE);
	s->mapped_pages = 0;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, 0, 1) == -EINVAL);
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes - 1, 1) == -EINVAL);
	bo->bo->bytes += 4096;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -EINVAL);
	bo->bo->bytes -= 4096;
	ttm->num_pages = 0;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -ERANGE);
	ttm->num_pages = 1;
	ttm->page_flags = TTM_TT_FLAG_EXTERNAL;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -ERANGE);
	ttm->page_flags = 0;
	ttm->populated = false;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -ERANGE);
	ttm->populated = true;
	ttm->flags = 7;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -EINVAL);
	ttm->flags = 15 | 4096;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -EINVAL);
	ttm->flags = 15;
	bo->gpu++;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -ERANGE);
	bo->gpu--;
	saved = f.adev.gart.table_size;
	f.adev.gart.table_size = 2000 * sizeof(u64) + sizeof(u64) - 1;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -ERANGE);
	f.adev.gart.table_size = saved;
	native = (u64 *)f.adev.gart.ptr + 2000;
	*native ^= 1;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -EINVAL);
	*native ^= 1;
	ttm->dma_address[0] |= 1ULL << 48;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -ERANGE);
	ttm->dma_address[0] &= ~(1ULL << 48);
	s->mapped_pages = 1;
	s->pages[0].dma = ttm->dma_address[0];
	s->pages[0].pfn = 0;
	s->pages[0].slot = 100;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -EINVAL);
	s->pages[0].dma = 0;
	s->pages[0].pfn = ttm->pages[0]->pfn;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -EINVAL);
	s->pages[0].pfn = 0;
	s->pages[0].slot = 1;
	CHECK(ps4_canary_add_bo_pages(&f.adev, s, bo, bytes, 1) == -EINVAL);
	destroy_fixture(&f);
}

#if FRAME_CAPABLE
static void test_frame_guards(void)
{
	struct fixture f;
	struct ps4_canary_state *s = &f.state;
	struct amdgpu_ps4_uvd_ring backing;
	u64 ptes[3];
	unsigned int slot;
	init_fixture(&f, true, false);
	CHECK(ps4_canary_ready_pages(&f.adev, s, ptes) == 0);
	CHECK(!ps4_canary_frame_pt_valid(s));
	emit_actual_private_pt(s);
	CHECK(ps4_canary_frame_pt_valid(s));
	for (slot = 0; slot < 1024; ++slot) {
		u64 *pte = (u64 *)s->pt.cpu + slot;
		*pte ^= 16;
		CHECK(!ps4_canary_frame_pt_valid(s));
		CHECK(!ps4_canary_frame_backing(s, &backing));
		*pte ^= 16;
	}
	CHECK(ps4_canary_frame_pt_valid(s));
	s->pages[777].slot = 777;
	CHECK(!ps4_canary_frame_pt_valid(s));
	s->pages[777].slot = 778;
	s->mapped_pages--;
	CHECK(!ps4_canary_frame_pt_valid(s));
	s->mapped_pages++;
	s->pt.bo->bytes--;
	CHECK(!ps4_canary_frame_pt_valid(s));
	s->pt.bo->bytes++;
	s->leaf_index = 1;
	CHECK(!ps4_canary_frame_pt_valid(s));
	s->leaf_index = 0;
	s->uvd_ib.bo->bytes = 0x20000;
	CHECK(!ps4_canary_frame_backing(s, &backing));
	s->uvd_ib.bo->bytes = 0x40000;
	s->submitted = false;
	CHECK(!ps4_canary_frame_backing(s, &backing));
	s->submitted = true;
	raw_frame = false;
	CHECK(!ps4_canary_frame_backing(s, &backing));
	raw_frame = true;
	options_valid = false;
	CHECK(!ps4_canary_frame_backing(s, &backing));
	options_valid = true;
	CHECK(ps4_canary_frame_backing(s, &backing));
	writeq(16, (char *)s->pt.cpu + 842 * sizeof(u64));
	if (!setjmp(parked)) {
		dispatch_actual_borrow_call(s);
		CHECK(false);
	}
	CHECK(!strcmp(park_stage, "decode-frame-owned-backing"));
	destroy_fixture(&f);
}

static void test_raw_gate(void)
{
	struct amdgpu_device adev = { 0 };
	unsigned int flags;
	adev.asic_type = CHIP_LIVERPOOL;
	for (flags = 0; flags < 16; ++flags) {
		raw_frame = !!(flags & 1);
		ps4_map_canary = !!(flags & 2);
		ps4_uvd_live_prepare = !!(flags & 4);
		options_valid = !!(flags & 8);
		raw_ready = false;
		raw_create = false;
		CHECK(extracted_frame_admission(&adev) ==
		      (!raw_frame || (!ps4_map_canary && !ps4_uvd_live_prepare && options_valid)));
		if (raw_frame)
			CHECK(amdgpu_ps4_map_canary_enabled(&adev));
	}
}
#endif

int main(void)
{
	test_mapping(false, false);
	test_mapping(false, true);
	test_add_bounds(false);
#if FRAME_CAPABLE
	test_mapping(true, false);
	test_add_bounds(true);
	test_frame_guards();
	test_raw_gate();
#endif
	fprintf(stderr, "PASS checks=%u frame_capable=%u\n", checks, FRAME_CAPABLE);
	return 0;
}
