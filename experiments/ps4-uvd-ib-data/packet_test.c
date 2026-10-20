/* SPDX-License-Identifier: GPL-2.0-only */
/* Original Linux psX host checks; these do not emulate Liverpool or firmware. */
#include "ps4_uvd_ib_data_plan.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(expression) do { \
	if (!(expression)) { \
		fprintf(stderr, "%s:%d: %s\n", __func__, __LINE__, #expression); \
		exit(EXIT_FAILURE); \
	} \
} while (0)

/* Deliberately literal fixture dimensions, independent of producer macros. */
struct outputs {
	_Alignas(16) unsigned char arena[2 * 4096 + 3 * 64];
	struct ps4_uvd_ib_data_plan plan;
};

static unsigned rejection_cases;
static unsigned decoder_mutations;

static unsigned char *ring_bytes(struct outputs *out)
{
	return out->arena + 64;
}

static unsigned char *ib_bytes(struct outputs *out)
{
	return out->arena + 64 + 4096 + 64;
}

static struct ps4_uvd_ib_data_request request_fixture(void)
{
	struct ps4_uvd_ib_data_request request = {
		.ring_gpu_base = UINT64_C(0x300308000),
		.ib_gpu_base = UINT64_C(0x30030a000),
		.baseline = UINT32_C(0x43504436),
		.token = UINT32_C(0x55564936),
		.data_before_baseline = UINT32_C(0x013579bd),
		.initial_rptr_raw = 0,
		.initial_wptr_raw = 0
	};
	return request;
}

/* Independent wire reader: no producer helpers, native casts or constants. */
static uint32_t wire_word(const unsigned char *bytes)
{
	uint32_t value = 0;
	int i;

	for (i = 3; i >= 0; --i)
		value = value * 256U + bytes[i];
	return value;
}

struct decoded_main {
	uint64_t ib_address;
	uint32_t length_dw;
	unsigned address_writes;
	unsigned size_writes;
	unsigned nop_pairs;
};

/*
 * Decode the complete proposed grammar, including type/count and position.
 * Only the three IB request pairs followed by zero-valued NO_OP pairs fit.
 * This is a byte-contract checker, not a model of hardware consumption.
 */
static int decode_main(const unsigned char *bytes, size_t size,
		       uint64_t expected_address, struct decoded_main *decoded)
{
	struct decoded_main value = {0};
	size_t position;

	if (size < 64 || size > 4096 || size % 8U)
		return 0;
	for (position = 0; position < size; position += 8U) {
		uint32_t header = wire_word(bytes + position);
		unsigned type = header >> 30;
		unsigned count = ((header >> 16) & 0x3fffU) + 1U;
		unsigned reg = header & 0xffffU;
		uint32_t payload = wire_word(bytes + position + 4U);

		if (type || count != 1)
			return 0;
		if (position == 0) {
			if (reg != 0x3c67U)
				return 0;
			value.ib_address = payload;
			++value.address_writes;
		} else if (position == 8) {
			if (reg != 0x3c66U)
				return 0;
			value.ib_address |= (uint64_t)payload << 32;
			++value.address_writes;
		} else if (position == 16) {
			if (reg != 0x3da2U || payload != 16)
				return 0;
			value.length_dw = payload;
			++value.size_writes;
		} else {
			if (reg != 0x3bffU || payload)
				return 0;
			++value.nop_pairs;
		}
	}
	if (value.ib_address != expected_address)
		return 0;
	*decoded = value;
	return 1;
}

static int decode_ib(const unsigned char *bytes, size_t size,
		     uint32_t expected_token, unsigned *nop_pairs)
{
	unsigned data_writes = 0, nops = 0;
	size_t position;

	if (size < 64 || size > 4096 || size % 8U)
		return 0;
	for (position = 0; position < size; position += 8U) {
		uint32_t header = wire_word(bytes + position);
		unsigned type = header >> 30;
		unsigned count = ((header >> 16) & 0x3fffU) + 1U;
		unsigned reg = header & 0xffffU;
		uint32_t payload = wire_word(bytes + position + 4U);

		if (type || count != 1)
			return 0;
		if (position == 0) {
			if (reg != 0x3bc4U || payload != expected_token)
				return 0;
			++data_writes;
		} else {
			if (reg != 0x3bffU || payload)
				return 0;
			++nops;
		}
	}
	if (data_writes != 1)
		return 0;
	*nop_pairs = nops;
	return 1;
}

static void check_canaries(const struct outputs *out)
{
	size_t i;

	for (i = 0; i < 64; ++i)
		CHECK(out->arena[i] == 0xa5);
	for (i = 64 + 4096; i < 64 + 4096 + 64; ++i)
		CHECK(out->arena[i] == 0xa5);
	for (i = 64 + 4096 + 64 + 4096; i < sizeof(out->arena); ++i)
		CHECK(out->arena[i] == 0xa5);
}

static void check_plan(const struct ps4_uvd_ib_data_plan *plan,
		       struct ps4_uvd_ib_data_request request)
{
	CHECK(plan->request.ring_gpu_base == request.ring_gpu_base);
	CHECK(plan->request.ib_gpu_base == request.ib_gpu_base);
	CHECK(plan->request.baseline == request.baseline);
	CHECK(plan->request.token == request.token);
	CHECK(plan->request.data_before_baseline == request.data_before_baseline);
	CHECK(plan->request.initial_rptr_raw == 0);
	CHECK(plan->request.initial_wptr_raw == 0);
	CHECK(plan->ring_gpu_end_exclusive == request.ring_gpu_base + 4096);
	CHECK(plan->ib_gpu_end_exclusive == request.ib_gpu_base + 4096);
	CHECK(plan->ring_bytes == 4096);
	CHECK(plan->ib_bytes == 4096);
	CHECK(plan->submitted_dwords == 16);
	CHECK(plan->ib_length_dw_raw == 16);
	CHECK(plan->expected_rptr_raw == 16);
	CHECK(plan->publish_wptr_raw == 16);
	CHECK(plan->publish_wptr_raw != 64);
}

static void test_exact_images(void)
{
	struct outputs out;
	struct ps4_uvd_ib_data_request request = request_fixture();
	struct decoded_main decoded;
	unsigned nops;
	size_t position;
	const unsigned char prefix[] = {
		0x67, 0x3c, 0, 0, 0, 0xa0, 0x30, 0,
		0x66, 0x3c, 0, 0, 3, 0, 0, 0,
		0xa2, 0x3d, 0, 0, 0x10, 0, 0, 0
	};
	const unsigned char ib_prefix[] = {
		0xc4, 0x3b, 0, 0, 0x36, 0x49, 0x56, 0x55
	};
	const unsigned char nop[] = {0xff, 0x3b, 0, 0, 0, 0, 0, 0};

	CHECK(CHAR_BIT == 8);
	memset(&out, 0xa5, sizeof(out));
	CHECK(ps4_uvd_ib_data_build(ring_bytes(&out), 4096,
				   ib_bytes(&out), 4096, request, &out.plan) ==
	      PS4_UVD_IB_DATA_OK);
	CHECK(memcmp(ring_bytes(&out), prefix, sizeof(prefix)) == 0);
	CHECK(memcmp(ib_bytes(&out), ib_prefix, sizeof(ib_prefix)) == 0);
	for (position = 24; position < 4096; position += 8)
		CHECK(memcmp(ring_bytes(&out) + position, nop, sizeof(nop)) == 0);
	for (position = 8; position < 4096; position += 8)
		CHECK(memcmp(ib_bytes(&out) + position, nop, sizeof(nop)) == 0);
	CHECK(memcmp(ring_bytes(&out) + 4088, nop, sizeof(nop)) == 0);
	CHECK(memcmp(ib_bytes(&out) + 4088, nop, sizeof(nop)) == 0);
	CHECK(decode_main(ring_bytes(&out), 64, request.ib_gpu_base, &decoded));
	CHECK(decoded.ib_address == UINT64_C(0x30030a000));
	CHECK(decoded.address_writes == 2 && decoded.size_writes == 1);
	CHECK(decoded.length_dw == 16 && decoded.length_dw != 64);
	CHECK(decoded.nop_pairs == 5);
	CHECK(decode_main(ring_bytes(&out), 4096, request.ib_gpu_base, &decoded));
	CHECK(decoded.nop_pairs == 509);
	CHECK(decode_ib(ib_bytes(&out), 64, request.token, &nops) && nops == 7);
	CHECK(decode_ib(ib_bytes(&out), 4096, request.token, &nops) && nops == 511);
	/* This fixture token is absent even as raw main-ring data. */
	for (position = 0; position < 4096; position += 4)
		CHECK(wire_word(ring_bytes(&out) + position) != request.token);
	check_plan(&out.plan, request);
	check_canaries(&out);
}

static void test_token_bits_and_request_copy(void)
{
	struct outputs out;
	struct ps4_uvd_ib_data_request request = request_fixture();
	unsigned bit, byte, nops;

	for (bit = 0; bit < 32; ++bit) {
		request.token = UINT32_C(1) << bit;
		memset(&out, 0xa5, sizeof(out));
		CHECK(ps4_uvd_ib_data_build(ring_bytes(&out), 4096,
					   ib_bytes(&out), 4096, request, &out.plan) ==
		      PS4_UVD_IB_DATA_OK);
		for (byte = 0; byte < 4; ++byte)
			CHECK(ib_bytes(&out)[4 + byte] ==
			      (byte == bit / 8U ? 1U << (bit % 8U) : 0));
		CHECK(decode_ib(ib_bytes(&out), 4096, request.token, &nops));
		check_canaries(&out);
	}
	/* Passing the old plan's request by value must survive plan replacement. */
	request = request_fixture();
	out.plan.request = request;
	CHECK(ps4_uvd_ib_data_build(ring_bytes(&out), 4096, ib_bytes(&out), 4096,
				   out.plan.request, &out.plan) == PS4_UVD_IB_DATA_OK);
	check_plan(&out.plan, request);
}

static void test_valid_boundaries(void)
{
	/* Real distinct C subobjects touch at their boundaries without overlap. */
	struct adjacent_outputs {
		struct ps4_uvd_ib_data_plan plan;
		_Alignas(4) unsigned char ring[4096];
		unsigned char ib[4096];
	} adjacent;
	struct ps4_uvd_ib_data_request request = request_fixture();
	struct decoded_main decoded;
	unsigned nops;

	CHECK((unsigned char *)&adjacent.plan + sizeof(adjacent.plan) == adjacent.ring);
	CHECK(adjacent.ring + sizeof(adjacent.ring) == adjacent.ib);
	request.ring_gpu_base = 0;
	request.ib_gpu_base = 4096;
	request.baseline = 0; /* Only the fresh result is excluded from zero/ones. */
	CHECK(ps4_uvd_ib_data_build(adjacent.ring, 4096, adjacent.ib, 4096,
				   request, &adjacent.plan) == PS4_UVD_IB_DATA_OK);
	check_plan(&adjacent.plan, request);
	CHECK(decode_main(adjacent.ring, 4096, request.ib_gpu_base, &decoded));
	CHECK(decode_ib(adjacent.ib, 4096, request.token, &nops));

	/* Reversed GPU order and highest page with a representable exclusive end. */
	request.ring_gpu_base = UINT64_MAX - UINT64_C(8191);
	request.ib_gpu_base = request.ring_gpu_base - 4096;
	request.baseline = UINT32_MAX;
	CHECK(ps4_uvd_ib_data_build(adjacent.ring, 4096, adjacent.ib, 4096,
				   request, &adjacent.plan) == PS4_UVD_IB_DATA_OK);
	check_plan(&adjacent.plan, request);
	CHECK(decode_main(adjacent.ring, 4096, request.ib_gpu_base, &decoded));
	CHECK(adjacent.plan.ring_gpu_end_exclusive == UINT64_MAX - UINT64_C(4095));

	/* CPU ordering is independent of GPU ordering; the pages may be reversed. */
	CHECK(ps4_uvd_ib_data_build(adjacent.ib, 4096, adjacent.ring, 4096,
				   request, &adjacent.plan) == PS4_UVD_IB_DATA_OK);
	CHECK(decode_main(adjacent.ib, 4096, request.ib_gpu_base, &decoded));
	CHECK(decode_ib(adjacent.ring, 4096, request.token, &nops));
}

static void reject(struct outputs *out, void *ring, size_t ring_size,
		   void *ib, size_t ib_size,
		   struct ps4_uvd_ib_data_request request,
		   struct ps4_uvd_ib_data_plan *plan,
		   enum ps4_uvd_ib_data_error expected)
{
	unsigned char original[sizeof(*out)];

	memset(out, 0xa5, sizeof(*out));
	memcpy(original, out, sizeof(*out));
	CHECK(ps4_uvd_ib_data_build(ring, ring_size, ib, ib_size, request, plan) ==
	      expected);
	/* Includes both entire pages, all guards, plan, and padding. */
	CHECK(memcmp(out, original, sizeof(*out)) == 0);
	++rejection_cases;
}

static void reject_request(struct ps4_uvd_ib_data_request request,
			   enum ps4_uvd_ib_data_error expected)
{
	struct outputs out;

	reject(&out, ring_bytes(&out), 4096, ib_bytes(&out), 4096,
	       request, &out.plan, expected);
}

static void test_invalid_storage(void)
{
	struct outputs out;
	struct ps4_uvd_ib_data_request request = request_fixture();
	const size_t bad_sizes[] = {0, 1, 64, 4095, 4097, 8192, SIZE_MAX};
	size_t i;
	uintptr_t high_page = UINTPTR_MAX - 3;
	uintptr_t high_plan = UINTPTR_MAX -
		(UINTPTR_MAX % _Alignof(struct ps4_uvd_ib_data_plan));

	reject(&out, NULL, 4096, ib_bytes(&out), 4096, request, &out.plan,
	       PS4_UVD_IB_DATA_NULL);
	reject(&out, ring_bytes(&out), 4096, NULL, 4096, request, &out.plan,
	       PS4_UVD_IB_DATA_NULL);
	reject(&out, ring_bytes(&out), 4096, ib_bytes(&out), 4096, request, NULL,
	       PS4_UVD_IB_DATA_NULL);
	for (i = 0; i < sizeof(bad_sizes) / sizeof(bad_sizes[0]); ++i) {
		reject(&out, ring_bytes(&out), bad_sizes[i], ib_bytes(&out), 4096,
		       request, &out.plan, PS4_UVD_IB_DATA_SIZE);
		reject(&out, ring_bytes(&out), 4096, ib_bytes(&out), bad_sizes[i],
		       request, &out.plan, PS4_UVD_IB_DATA_SIZE);
	}
	for (i = 1; i < 4; ++i) {
		reject(&out, ring_bytes(&out) + i, 4096, ib_bytes(&out), 4096,
		       request, &out.plan, PS4_UVD_IB_DATA_ALIGNMENT);
		reject(&out, ring_bytes(&out), 4096, ib_bytes(&out) + i, 4096,
		       request, &out.plan, PS4_UVD_IB_DATA_ALIGNMENT);
	}
	for (i = 1; i < _Alignof(struct ps4_uvd_ib_data_plan); ++i)
		reject(&out, ring_bytes(&out), 4096, ib_bytes(&out), 4096, request,
		       (struct ps4_uvd_ib_data_plan *)((unsigned char *)&out.plan + i),
		       PS4_UVD_IB_DATA_ALIGNMENT);
	/* Invalid high pointers must be rejected without touching those addresses. */
	reject(&out, (void *)high_page, 4096, ib_bytes(&out), 4096,
	       request, &out.plan, PS4_UVD_IB_DATA_RANGE);
	reject(&out, ring_bytes(&out), 4096, (void *)high_page, 4096,
	       request, &out.plan, PS4_UVD_IB_DATA_RANGE);
	reject(&out, ring_bytes(&out), 4096, ib_bytes(&out), 4096,
	       request, (struct ps4_uvd_ib_data_plan *)high_plan,
	       PS4_UVD_IB_DATA_RANGE);
}

static void test_cpu_overlap(void)
{
	struct outputs out;
	struct ps4_uvd_ib_data_request request = request_fixture();
	unsigned char *pages[] = {ring_bytes(&out), ib_bytes(&out)};
	size_t alignment = _Alignof(struct ps4_uvd_ib_data_plan);
	size_t i;

	reject(&out, ring_bytes(&out), 4096, ring_bytes(&out), 4096,
	       request, &out.plan, PS4_UVD_IB_DATA_OVERLAP);
	reject(&out, ring_bytes(&out), 4096, ring_bytes(&out) + 4, 4096,
	       request, &out.plan, PS4_UVD_IB_DATA_OVERLAP);
	reject(&out, ring_bytes(&out) + 4, 4096, ring_bytes(&out), 4096,
	       request, &out.plan, PS4_UVD_IB_DATA_OVERLAP);
	/* Boundary overlap by one DWORD, in either CPU order. */
	reject(&out, ring_bytes(&out), 4096, ring_bytes(&out) + 4092, 4096,
	       request, &out.plan, PS4_UVD_IB_DATA_OVERLAP);
	reject(&out, ring_bytes(&out) + 4092, 4096, ring_bytes(&out), 4096,
	       request, &out.plan, PS4_UVD_IB_DATA_OVERLAP);
	for (i = 0; i < sizeof(pages) / sizeof(pages[0]); ++i) {
		reject(&out, ring_bytes(&out), 4096, ib_bytes(&out), 4096, request,
		       (struct ps4_uvd_ib_data_plan *)pages[i], PS4_UVD_IB_DATA_OVERLAP);
		reject(&out, ring_bytes(&out), 4096, ib_bytes(&out), 4096, request,
		       (struct ps4_uvd_ib_data_plan *)(pages[i] + 4096 - alignment),
		       PS4_UVD_IB_DATA_OVERLAP);
		/* Plan begins before the page and extends into it. */
		reject(&out, ring_bytes(&out), 4096, ib_bytes(&out), 4096, request,
		       (struct ps4_uvd_ib_data_plan *)(pages[i] - alignment),
		       PS4_UVD_IB_DATA_OVERLAP);
	}
}

static void test_invalid_request(void)
{
	struct ps4_uvd_ib_data_request request;
	const uint32_t bad_pointers[] = {1, 2, 16, 64, 1024, UINT32_MAX};
	const uint32_t bad_tokens[] = {0, UINT32_MAX, 0x43504436, 0x013579bd};
	size_t i;

	for (i = 1; i < 4096; i <<= 1) {
		request = request_fixture();
		request.ring_gpu_base += i;
		reject_request(request, PS4_UVD_IB_DATA_ALIGNMENT);
		request = request_fixture();
		request.ib_gpu_base += i;
		reject_request(request, PS4_UVD_IB_DATA_ALIGNMENT);
	}
	request = request_fixture();
	request.ring_gpu_base = UINT64_MAX - UINT64_C(4095);
	reject_request(request, PS4_UVD_IB_DATA_RANGE);
	request = request_fixture();
	request.ib_gpu_base = UINT64_MAX - UINT64_C(4095);
	reject_request(request, PS4_UVD_IB_DATA_RANGE);
	request = request_fixture();
	request.ib_gpu_base = request.ring_gpu_base;
	reject_request(request, PS4_UVD_IB_DATA_OVERLAP);
	request = request_fixture();
	request.ring_gpu_base = request.ib_gpu_base;
	reject_request(request, PS4_UVD_IB_DATA_OVERLAP);
	for (i = 0; i < sizeof(bad_pointers) / sizeof(bad_pointers[0]); ++i) {
		request = request_fixture();
		request.initial_rptr_raw = bad_pointers[i];
		reject_request(request, PS4_UVD_IB_DATA_INITIAL_POINTER);
		request = request_fixture();
		request.initial_wptr_raw = bad_pointers[i];
		reject_request(request, PS4_UVD_IB_DATA_INITIAL_POINTER);
	}
	for (i = 0; i < sizeof(bad_tokens) / sizeof(bad_tokens[0]); ++i) {
		request = request_fixture();
		request.token = bad_tokens[i];
		reject_request(request, PS4_UVD_IB_DATA_TOKEN);
	}
}

static void replace_header(unsigned char *page, size_t offset, unsigned reg)
{
	page[offset] = (unsigned char)(reg & 255U);
	page[offset + 1] = (unsigned char)(reg >> 8);
	page[offset + 2] = 0;
	page[offset + 3] = 0;
}

static void test_decoder_sensitivity(void)
{
	struct outputs out;
	struct ps4_uvd_ib_data_request request = request_fixture();
	struct decoded_main decoded;
	unsigned nops;
	unsigned char changed[4096];
	/* DATA0, context, CMD, DATA1, semaphore, and a second IB request. */
	const unsigned main_forbidden[] = {
		0x3bc4, 0x3dbd, 0x3bc3, 0x3bc5, 0x3bc2, 0x3c67, 0x3c66, 0x3da2
	};
	const unsigned ib_forbidden[] = {
		0x3dbd, 0x3bc3, 0x3bc5, 0x3bc2, 0x3c67, 0x3c66, 0x3da2
	};
	const size_t invalid_lengths[] = {0, 1, 4, 8, 56, 60, 65, 4097};
	size_t i, offset;

	CHECK(ps4_uvd_ib_data_build(ring_bytes(&out), 4096, ib_bytes(&out), 4096,
				   request, &out.plan) == PS4_UVD_IB_DATA_OK);
	for (i = 0; i < sizeof(main_forbidden) / sizeof(main_forbidden[0]); ++i) {
		memcpy(changed, ring_bytes(&out), sizeof(changed));
		replace_header(changed, 24, main_forbidden[i]);
		CHECK(!decode_main(changed, 4096, request.ib_gpu_base, &decoded));
		++decoder_mutations;
	}
	for (i = 0; i < sizeof(ib_forbidden) / sizeof(ib_forbidden[0]); ++i) {
		memcpy(changed, ib_bytes(&out), sizeof(changed));
		replace_header(changed, 8, ib_forbidden[i]);
		CHECK(!decode_ib(changed, 4096, request.token, &nops));
		++decoder_mutations;
	}
	/* Also reject a second result write, including outside the 16-DWORD IB. */
	for (offset = 8; offset <= 4088; offset += 4080) {
		memcpy(changed, ib_bytes(&out), sizeof(changed));
		memcpy(changed + offset, ib_bytes(&out), 8);
		CHECK(!decode_ib(changed, 4096, request.token, &nops));
		++decoder_mutations;
	}
	/* Address truncation, swapped halves, byte-count length and shifted length. */
	memcpy(changed, ring_bytes(&out), sizeof(changed));
	memset(changed + 12, 0, 4);
	CHECK(!decode_main(changed, 64, request.ib_gpu_base, &decoded));
	++decoder_mutations;
	memcpy(changed, ring_bytes(&out), sizeof(changed));
	memcpy(changed + 4, ring_bytes(&out) + 12, 4);
	memcpy(changed + 12, ring_bytes(&out) + 4, 4);
	CHECK(!decode_main(changed, 64, request.ib_gpu_base, &decoded));
	++decoder_mutations;
	for (i = 0; i < 2; ++i) {
		memcpy(changed, ring_bytes(&out), sizeof(changed));
		changed[20] = i ? 4 : 64;
		CHECK(!decode_main(changed, 64, request.ib_gpu_base, &decoded));
		++decoder_mutations;
	}
	/* Type/count violations, zero-filled padding and nonzero NOP payload. */
	for (i = 0; i < 4; ++i) {
		memcpy(changed, ring_bytes(&out), sizeof(changed));
		if (i == 0)
			changed[27] = 0xc0;
		else if (i == 1)
			changed[26] = 1;
		else if (i == 2)
			memset(changed + 24, 0, 4);
		else
			changed[28] = 1;
		CHECK(!decode_main(changed, 4096, request.ib_gpu_base, &decoded));
		++decoder_mutations;
		memcpy(changed, ib_bytes(&out), sizeof(changed));
		if (i == 0)
			changed[11] = 0xc0;
		else if (i == 1)
			changed[10] = 1;
		else if (i == 2)
			memset(changed + 8, 0, 4);
		else
			changed[12] = 1;
		CHECK(!decode_ib(changed, 4096, request.token, &nops));
		++decoder_mutations;
	}
	memcpy(changed, ib_bytes(&out), sizeof(changed));
	changed[4] ^= 1;
	CHECK(!decode_ib(changed, 4096, request.token, &nops));
	++decoder_mutations;
	for (i = 0; i < sizeof(invalid_lengths) / sizeof(invalid_lengths[0]); ++i) {
		CHECK(!decode_main(ring_bytes(&out), invalid_lengths[i],
				   request.ib_gpu_base, &decoded));
		CHECK(!decode_ib(ib_bytes(&out), invalid_lengths[i], request.token, &nops));
		decoder_mutations += 2;
	}
}

int main(void)
{
	test_exact_images();
	test_token_bits_and_request_copy();
	test_valid_boundaries();
	test_invalid_storage();
	test_cpu_overlap();
	test_invalid_request();
	test_decoder_sensitivity();
	printf("PASS: exact main/IB images, 32 token bits, %u atomic rejections, "
	       "%u decoder sensitivity cases; host byte contract only\n",
	       rejection_cases, decoder_mutations);
	return EXIT_SUCCESS;
}
