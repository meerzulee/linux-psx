/* SPDX-License-Identifier: GPL-2.0-only */
/* CPU-only tests: public packet routines write synthetic arrays, never MMIO. */
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ps4_uvd_message_job.h"

#define CHECK(x, name) do { if (!(x)) { \
	fprintf(stderr, "FAIL:%s line=%d\n", name, __LINE__); exit(2); } } while (0)
#define WARN_ON(x) CHECK(!(x), "PUBLIC_REFERENCE_PRECONDITION")
#define AMDGPU_FENCE_FLAG_64BIT 1
#define lower_32_bits(x) ((u32)(x))
#define upper_32_bits(x) ((u32)((u64)(x) >> 32))
struct amdgpu_ring { u32 words[32], wptr; };
struct amdgpu_ib { u32 ptr[16], length_dw; u64 gpu_addr; };
static void amdgpu_ring_write(struct amdgpu_ring *ring, u32 value)
{
	CHECK(ring->wptr < 32, "REFERENCE_CAPACITY");
	ring->words[ring->wptr++] = value;
}
static u32 cpu_to_le32(u32 value)
{
	const u8 bytes[4] = {(u8)value, (u8)(value >> 8), (u8)(value >> 16), (u8)(value >> 24)};
	u32 native;
	memcpy(&native, bytes, 4);
	return native;
}
#include "public_reference.h"

static unsigned int cases;
static struct ps4_uvd_message_job_plan out, before, duplicate;
static const struct ps4_uvd_message_job_input normal = {
	.vcpu_segment_base = 0x300000000ULL,
	.main_va = 0x300308000ULL,
	.backing_va = 0x30030a000ULL,
	.main_bytes = 4096,
	.backing_bytes = 0x20000,
	.handle = 0x11223344,
	.sequence = 0xa1b2c3d4,
};
_Static_assert(sizeof(out.main_le) == 64 && sizeof(out.ib_le) == 64, "packet arrays");
_Static_assert(sizeof(out.create_le) == 4096, "message page");

static u32 le_word(const u8 *bytes, unsigned int index)
{
	bytes += index * 4;
	return (u32)bytes[0] | (u32)bytes[1] << 8 |
	       (u32)bytes[2] << 16 | (u32)bytes[3] << 24;
}

static void packet_reference(const struct ps4_uvd_message_job_input *input)
{
	struct amdgpu_ib ref_ib = { .gpu_addr = input->backing_va };
	struct amdgpu_ring main = {0}, fence = {0}, nop = {0};
	u32 create[1024];
	u64 message_va = input->backing_va + 0x1000;
	u64 fence_va = input->backing_va + 0x2040;
	reference_message_ib(&ref_ib, message_va);
	reference_emit_ib(&main, &ref_ib);
	reference_emit_fence(&fence, fence_va, input->sequence, 0);
	reference_insert_nop(&nop, 2);
	CHECK(main.wptr == 6 && fence.wptr == 14 && nop.wptr == 2, "PUBLIC_COUNTS");
	CHECK(ref_ib.length_dw == 16, "PUBLIC_IB_DWORDS");
	for (unsigned int i = 0; i < 16; ++i)
		CHECK(le_word(out.ib_le, i) == ref_ib.ptr[i], "REFERENCE_IB");
	for (unsigned int i = 0; i < 6; ++i)
		CHECK(le_word(out.main_le, i) == main.words[i], "REFERENCE_MAIN");
	for (unsigned int i = 0; i < 8; ++i)
		CHECK(le_word(out.main_le, 6 + i) == fence.words[i], "REFERENCE_FENCE_PREFIX");
	CHECK(fence.words[8] == PACKET0(mmUVD_GPCOM_VCPU_DATA0, 0) && fence.words[9] == 0 &&
	      fence.words[10] == PACKET0(mmUVD_GPCOM_VCPU_DATA1, 0) && fence.words[11] == 0 &&
	      fence.words[12] == PACKET0(mmUVD_GPCOM_VCPU_CMD, 0) && fence.words[13] == 2,
	      "PUBLIC_TRAP_SUFFIX");
	CHECK(le_word(out.main_le, 14) == nop.words[0] &&
	      le_word(out.main_le, 15) == nop.words[1], "TRAP_OMITTED_NOP");
	reference_linux_create(create, input->handle);
	CHECK(memcmp(out.create_le, create, sizeof(create)) == 0, "PUBLIC_LINUX_CREATE");
}

static void valid(struct ps4_uvd_message_job_input input)
{
	memset(&out, 0xa5, sizeof(out));
	CHECK(ps4_uvd_message_job_build(&out, &input) == 0, "VALID_RETURN");
	CHECK(out.layout.vcpu_segment_base == input.vcpu_segment_base &&
	      out.layout.main_va == input.main_va && out.layout.backing_va == input.backing_va &&
	      out.layout.ib_va == input.backing_va && out.layout.create_va == input.backing_va + 0x1000 &&
	      out.layout.fence_page_va == input.backing_va + 0x2000 &&
	      out.layout.fence_target_va == input.backing_va + 0x2040, "LAYOUT_ADDRESSES");
	CHECK(out.layout.main_page_bytes == 4096 && out.layout.backing_bytes == 0x20000 &&
	      out.layout.main_dw == 16 && out.layout.ib_dw == 16 &&
	      out.layout.create_page_bytes == 4096 && out.layout.create_declared_bytes == 0xde4 &&
	      out.layout.fence_page_bytes == 4096 && out.layout.fence_target_offset == 0x40 &&
	      out.layout.fence_store_bytes == 4 && out.layout.sequence == input.sequence, "LAYOUT_LENGTHS");
	packet_reference(&input);
	memset(&duplicate, 0x3c, sizeof(duplicate));
	CHECK(ps4_uvd_message_job_build(&duplicate, &input) == 0 &&
	      memcmp(&duplicate, &out, sizeof(out)) == 0, "DETERMINISTIC_PLAN");
	++cases;
}

static void reject(struct ps4_uvd_message_job_input input, int error, const char *witness)
{
	memset(&out, 0x79, sizeof(out));
	before = out;
	int result = ps4_uvd_message_job_build(&out, &input);
	CHECK(result == error, witness);
	CHECK(memcmp(&out, &before, sizeof(out)) == 0, "REJECT_UNCHANGED");
	++cases;
}

static void valid_boundaries(void)
{
	struct ps4_uvd_message_job_input input = normal;
	valid(input);
	const u32 expected_main[16] = {
		0x3c67, 0x0030a000, 0x3c66, 3, 0x3da2, 16, 0x3dbd, 0xa1b2c3d4,
		0x3bc4, 0x0030c040, 0x3bc5, 3, 0x3bc3, 0, 0x3bff, 0
	};
	const u32 expected_ib[16] = {
		0x3bc4, 0x0030b000, 0x3bc5, 3, 0x3bc3, 0,
		0x3bff, 0, 0x3bff, 0, 0x3bff, 0, 0x3bff, 0, 0x3bff, 0
	};
	for (unsigned int i = 0; i < 16; ++i)
		CHECK(le_word(out.main_le, i) == expected_main[i] &&
		      le_word(out.ib_le, i) == expected_ib[i], "LITERAL_GOLDEN_PACKETS");
	const u8 sequence_bytes[] = {0xd4, 0xc3, 0xb2, 0xa1};
	CHECK(memcmp(out.main_le + 7 * 4, sequence_bytes, 4) == 0, "EXPLICIT_ENDIAN");
	input.sequence = 0; valid(input);
	input.sequence = UINT32_MAX; input.handle = UINT32_MAX; valid(input);
	input = normal; input.main_va = input.backing_va - 4096; valid(input);
	input.main_va = input.backing_va + 0x20000; valid(input);
	input = normal; input.backing_va = input.vcpu_segment_base + 0x10000000 - 0x20000; valid(input);
	input = normal; input.main_va = input.vcpu_segment_base + 0x10000000 - 4096; valid(input);
	input = normal;
	input.vcpu_segment_base = (1ULL << 40) - 0x10000000;
	input.backing_va = (1ULL << 40) - 0x20000;
	input.main_va = input.backing_va - 4096;
	valid(input);
	CHECK(le_word(out.main_le, 11) == 0xff, "FULL_40BIT_HIGH_BYTE");
	input = normal; input.vcpu_segment_base = 0; input.main_va = 0; input.backing_va = 0x2000; valid(input);
}

static void invalid_ranges(void)
{
	const size_t main_sizes[] = {0, 64, 4095, 4097, SIZE_MAX};
	const size_t backing_sizes[] = {0, 4096, 0x1ffff, 0x20001, SIZE_MAX};
	const u64 misalignments[] = {1, 4, 0x100, 0x800};
	struct ps4_uvd_message_job_input input;
	for (size_t i = 0; i < 5; ++i) {
		input = normal; input.main_bytes = main_sizes[i];
		reject(input, -EINVAL, "SIZE_REJECT");
		input = normal; input.backing_bytes = backing_sizes[i];
		reject(input, -EINVAL, "SIZE_REJECT");
	}
	for (size_t i = 0; i < 4; ++i) {
		input = normal; input.main_va += misalignments[i];
		reject(input, -EINVAL, "ALIGNMENT_REJECT");
		input = normal; input.backing_va += misalignments[i];
		reject(input, -EINVAL, "ALIGNMENT_REJECT");
	}
	input = normal; input.vcpu_segment_base += 4096; reject(input, -EINVAL, "SEGMENT_BASE_REJECT");
	input = normal; input.main_va = input.backing_va; reject(input, -EINVAL, "OVERLAP_REJECT");
	input.main_va += 4096; reject(input, -EINVAL, "OVERLAP_REJECT");
	input.main_va = input.backing_va + 0x1f000; reject(input, -EINVAL, "WHOLE_BACKING_OVERLAP_REJECT");
	input = normal; input.main_va = input.vcpu_segment_base - 4096; reject(input, -ERANGE, "SEGMENT_REJECT");
	input = normal; input.main_va = input.vcpu_segment_base + 0x10000000; reject(input, -ERANGE, "SEGMENT_REJECT");
	input = normal; input.backing_va = input.vcpu_segment_base - 0x20000; reject(input, -ERANGE, "SEGMENT_REJECT");
	input = normal; input.backing_va = input.vcpu_segment_base + 0x10000000 - 4096;
	reject(input, -ERANGE, "SEGMENT_REJECT");
	input = normal; input.main_va = UINT64_MAX - 4095; reject(input, -ERANGE, "OVERFLOW_REJECT");
	input = normal; input.backing_va = UINT64_MAX - 0x1ffff; reject(input, -ERANGE, "OVERFLOW_REJECT");
	input = normal; input.vcpu_segment_base = UINT64_MAX - 0xfffffff;
	reject(input, -ERANGE, "OVERFLOW_REJECT");
	input = normal; input.vcpu_segment_base = 1ULL << 40;
	input.main_va = input.vcpu_segment_base + 0x1000;
	input.backing_va = input.vcpu_segment_base + 0x4000;
	reject(input, -ERANGE, "ADDRESS_40BIT_REJECT");
	input = normal; input.handle = 0; reject(input, -EINVAL, "SERIALIZER_REJECT");
}

static void pointers_and_alias(void)
{
	union {
		struct ps4_uvd_message_job_plan plan;
		struct ps4_uvd_message_job_input input;
	} overlapping;
	memset(&out, 0x39, sizeof(out));
	before = out;
	CHECK(ps4_uvd_message_job_build(&out, NULL) == -EINVAL &&
	      memcmp(&out, &before, sizeof(out)) == 0 &&
	      ps4_uvd_message_job_build(NULL, &normal) == -EINVAL, "NULL_REJECT");
	++cases;
	overlapping.input = normal;
	CHECK(ps4_uvd_message_job_build(&overlapping.plan, &overlapping.input) == 0 &&
	      ps4_uvd_message_job_build(&out, &normal) == 0 &&
	      memcmp(&overlapping.plan, &out, sizeof(out)) == 0, "INPUT_OUTPUT_ALIAS");
	++cases;
}

int main(void)
{
	valid_boundaries();
	invalid_ranges();
	pointers_and_alias();
	printf("PASS: %u actual-source message-job cases; public packets, layout, endian, limits and unchanged rejection\n", cases);
	return 0;
}
