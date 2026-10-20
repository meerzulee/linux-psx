/* SPDX-License-Identifier: GPL-2.0-only */
/* Original Linux psX fixture. This includes the intended adapter header. */
#include "ps4_uvd_ring_plan.h"

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

struct guarded_ring {
	_Alignas(4096) unsigned char bytes[4096 + 128];
};

static struct ps4_uvd_ring_request request_fixture(void)
{
	struct ps4_uvd_ring_request request = {
		.gpu_base = UINT64_C(0x12345000),
		.baseline = UINT32_C(0xcafedead),
		.token = UINT32_C(0x10234567),
		.context_before_baseline = UINT32_C(0x8899aabb),
		.initial_rptr_raw = 0,
		.initial_wptr_raw = 0
	};
	return request;
}

/* Independent wire reader: no producer helpers/constants or native casts. */
static uint32_t wire_word(const unsigned char *bytes)
{
	uint32_t result = 0;
	int position;

	for (position = 3; position >= 0; --position)
		result = (result * 256U) + bytes[position];
	return result;
}

/*
 * A deliberately restricted packet interpreter. It decodes TYPE and COUNT,
 * enforces the two-register allowlist and executes only a selected prefix.
 * It does not infer completion from producer metadata or compare two copies
 * of an expected blob. It cannot model Liverpool's real packet parser.
 */
static int interpret(const unsigned char *bytes, size_t byte_count,
		     uint32_t token, uint32_t *context)
{
	size_t offset = 0;
	unsigned contexts = 0;

	if (!byte_count || byte_count % 4U)
		return 0;
	while (offset < byte_count) {
		uint32_t header = wire_word(bytes + offset);
		unsigned type = header >> 30;
		unsigned data_words = ((header >> 16) & 0x3fffU) + 1U;
		unsigned reg = header & 0xffffU;
		uint32_t value;

		if (type != 0 || data_words != 1 || byte_count - offset < 8)
			return 0;
		value = wire_word(bytes + offset + 4);
		if (reg == 0x3dbdU) {
			if (offset || contexts || value != token)
				return 0;
			*context = value;
			++contexts;
		} else if (reg == 0x3bffU) {
			if (value)
				return 0;
		} else {
			/* Includes every GPCOM, semaphore, IB and trap operation. */
			return 0;
		}
		offset += 4U + data_words * 4U;
	}
	return contexts == 1;
}

static void check_canaries(const struct guarded_ring *ring)
{
	size_t i;

	for (i = 0; i < 64; ++i)
		CHECK(ring->bytes[i] == 0xa5);
	for (i = 64 + 4096; i < sizeof(ring->bytes); ++i)
		CHECK(ring->bytes[i] == 0xa5);
}

static void test_packet_image(void)
{
	struct guarded_ring ring;
	struct ps4_uvd_ring_plan plan;
	struct ps4_uvd_ring_request request = request_fixture();
	const unsigned char first[] = {0xbd, 0x3d, 0x00, 0x00,
				       0x67, 0x45, 0x23, 0x10};
	const unsigned char last[] = {0xff, 0x3b, 0x00, 0x00,
				      0x00, 0x00, 0x00, 0x00};
	unsigned char *bytes = ring.bytes + 64;
	uint32_t context = request.baseline;
	size_t offset;

	CHECK(CHAR_BIT == 8);
	memset(&ring, 0xa5, sizeof(ring));
	CHECK(ps4_uvd_build_ring(bytes, 4096, request, &plan) == PS4_UVD_PLAN_OK);
	CHECK(memcmp(bytes, first, sizeof(first)) == 0);
	CHECK(memcmp(bytes + 4096 - 8, last, sizeof(last)) == 0);
	CHECK(interpret(bytes, 64, request.token, &context));
	CHECK(context == request.token);
	context = request.baseline;
	CHECK(interpret(bytes, 4096, request.token, &context));
	CHECK(context == request.token);
	for (offset = 8; offset < 4096; offset += 8)
		CHECK(memcmp(bytes + offset, last, sizeof(last)) == 0);
	CHECK(plan.request.gpu_base == request.gpu_base);
	CHECK(plan.gpu_end_exclusive == UINT64_C(0x12346000));
	CHECK(plan.ring_bytes == 4096);
	CHECK(plan.submitted_dwords == 16);
	CHECK(plan.expected_rptr_raw == 16);
	CHECK(plan.publish_wptr_raw == 16);
	CHECK(plan.publish_wptr_raw != 64); /* Do not publish a byte offset. */
	check_canaries(&ring);
}

static void test_endian_and_tokens(void)
{
	_Alignas(4) unsigned char bytes[4096];
	struct ps4_uvd_ring_request request = request_fixture();
	struct ps4_uvd_ring_plan plan;
	unsigned bit;

	for (bit = 0; bit < 32; ++bit) {
		uint32_t context = request.baseline;
		unsigned byte;

		request.token = UINT32_C(1) << bit;
		CHECK(ps4_uvd_build_ring(bytes, sizeof(bytes), request, &plan) ==
		      PS4_UVD_PLAN_OK);
		for (byte = 0; byte < 4; ++byte)
			CHECK(bytes[4 + byte] ==
			      (byte == bit / 8U ? 1U << (bit % 8U) : 0));
		CHECK(interpret(bytes, 64, request.token, &context));
		CHECK(context == request.token);
	}
	request.token = UINT32_C(0x10234567);
	CHECK(ps4_uvd_build_ring(bytes, sizeof(bytes), request, &plan) ==
	      PS4_UVD_PLAN_OK);
	CHECK(wire_word(bytes + 4) == UINT32_C(0x10234567));
	/* Reading the wire bytes as BE produces the swapped value, on any host. */
	CHECK(((uint32_t)bytes[4] << 24 | (uint32_t)bytes[5] << 16 |
	       (uint32_t)bytes[6] << 8 | bytes[7]) == UINT32_C(0x67452310));
}

static void test_interpreter_rejects_mutations(void)
{
	_Alignas(4) unsigned char valid[4096];
	unsigned char mutated[4096];
	struct ps4_uvd_ring_request request = request_fixture();
	struct ps4_uvd_ring_plan plan;
	/* Independent literals: GPCOM cmd/data, IB low/high/size, semaphore, MMIO. */
	const unsigned forbidden[] = {0x3bc3, 0x3bc4, 0x3bc5, 0x3c67,
				      0x3c66, 0x3da2, 0x3bc2, 0x3da5};
	size_t i;
	uint32_t context;

	CHECK(ps4_uvd_build_ring(valid, sizeof(valid), request, &plan) ==
	      PS4_UVD_PLAN_OK);
	for (i = 0; i < sizeof(forbidden) / sizeof(forbidden[0]); ++i) {
		memcpy(mutated, valid, sizeof(valid));
		mutated[8] = (unsigned char)(forbidden[i] & 0xffU);
		mutated[9] = (unsigned char)(forbidden[i] >> 8);
		context = request.baseline;
		CHECK(!interpret(mutated, 4096, request.token, &context));
	}
	memcpy(mutated, valid, sizeof(valid));
	mutated[11] = 0xc0; /* TYPE3 rather than a register-write packet. */
	CHECK(!interpret(mutated, 64, request.token, &context));
	memcpy(mutated, valid, sizeof(valid));
	mutated[10] = 1; /* More than one data word can escape the allowlist. */
	CHECK(!interpret(mutated, 64, request.token, &context));
	memcpy(mutated, valid, sizeof(valid));
	memset(mutated + 8, 0, 4); /* Zero-fill is not a legal UVD NO_OP pair. */
	CHECK(!interpret(mutated, 64, request.token, &context));
	memcpy(mutated, valid, sizeof(valid));
	mutated[12] = 1; /* Nonzero NO_OP payload. */
	CHECK(!interpret(mutated, 64, request.token, &context));
	memcpy(mutated, valid, sizeof(valid));
	memcpy(mutated + 8, valid, 8); /* Hidden second context write. */
	CHECK(!interpret(mutated, 64, request.token, &context));
	memcpy(mutated, valid, sizeof(valid));
	mutated[4] ^= 1; /* Wrong result token. */
	CHECK(!interpret(mutated, 64, request.token, &context));
	CHECK(!interpret(valid, 0, request.token, &context));
	CHECK(!interpret(valid, 1, request.token, &context));
	CHECK(!interpret(valid, 4, request.token, &context));
	CHECK(!interpret(valid, 60, request.token, &context));
}

static void expect_rejection(struct ps4_uvd_ring_request request,
			     size_t size, unsigned misalign,
			     enum ps4_uvd_plan_error expected)
{
	struct guarded_ring ring;
	struct guarded_ring original;
	struct ps4_uvd_ring_plan plan;
	unsigned char original_plan[sizeof(plan)];

	memset(&ring, 0xa5, sizeof(ring));
	memcpy(&original, &ring, sizeof(ring));
	memset(&plan, 0x5a, sizeof(plan));
	memcpy(original_plan, &plan, sizeof(plan));
	CHECK(ps4_uvd_build_ring(ring.bytes + 64 + misalign, size, request,
				&plan) == expected);
	CHECK(memcmp(&ring, &original, sizeof(ring)) == 0);
	CHECK(memcmp(&plan, original_plan, sizeof(plan)) == 0);
}

static void test_rejections_are_atomic(void)
{
	struct ps4_uvd_ring_request request = request_fixture();
	const size_t bad_sizes[] = {0, 1, 64, 4095, 4097, 8192, SIZE_MAX};
	const uint32_t bad_pointers[] = {1, 2, 16, 64, 1024, UINT32_MAX};
	size_t i;

	for (i = 0; i < sizeof(bad_sizes) / sizeof(bad_sizes[0]); ++i)
		expect_rejection(request, bad_sizes[i], 0, PS4_UVD_PLAN_SIZE);
	for (i = 1; i <= 3; ++i)
		expect_rejection(request, 4096, (unsigned)i, PS4_UVD_PLAN_ALIGNMENT);
	request.gpu_base += 4;
	expect_rejection(request, 4096, 0, PS4_UVD_PLAN_ALIGNMENT);
	request = request_fixture();
	request.gpu_base = UINT64_MAX - UINT64_C(4095);
	expect_rejection(request, 4096, 0, PS4_UVD_PLAN_RANGE);
	for (i = 0; i < sizeof(bad_pointers) / sizeof(bad_pointers[0]); ++i) {
		request = request_fixture();
		request.initial_rptr_raw = bad_pointers[i];
		expect_rejection(request, 4096, 0, PS4_UVD_PLAN_INITIAL_POINTER);
		request.initial_rptr_raw = 0;
		request.initial_wptr_raw = bad_pointers[i];
		expect_rejection(request, 4096, 0, PS4_UVD_PLAN_INITIAL_POINTER);
	}
	request = request_fixture();
	request.token = 0;
	expect_rejection(request, 4096, 0, PS4_UVD_PLAN_TOKEN);
	request.token = UINT32_MAX;
	expect_rejection(request, 4096, 0, PS4_UVD_PLAN_TOKEN);
	request.token = request.baseline;
	expect_rejection(request, 4096, 0, PS4_UVD_PLAN_TOKEN);
	request.token = request.context_before_baseline;
	expect_rejection(request, 4096, 0, PS4_UVD_PLAN_TOKEN);
	request = request_fixture();
	request.baseline = 0;
	expect_rejection(request, 4096, 0, PS4_UVD_PLAN_TOKEN);
	request.baseline = UINT32_MAX;
	expect_rejection(request, 4096, 0, PS4_UVD_PLAN_TOKEN);
}

static void test_destination_boundaries(void)
{
	struct {
		_Alignas(4096) unsigned char bytes[4096];
		struct ps4_uvd_ring_plan plan;
	} adjacent;
	struct guarded_ring ring;
	struct guarded_ring original;
	struct ps4_uvd_ring_plan plan;
	unsigned char original_plan[sizeof(plan)];
	struct ps4_uvd_ring_request request = request_fixture();
	unsigned char *bytes = ring.bytes + 64;
	uintptr_t maximum = UINTPTR_MAX;

	memset(&ring, 0xa5, sizeof(ring));
	memcpy(&original, &ring, sizeof(ring));
	memset(&plan, 0x5a, sizeof(plan));
	memcpy(original_plan, &plan, sizeof(plan));
	CHECK(ps4_uvd_build_ring(NULL, 4096, request, &plan) == PS4_UVD_PLAN_NULL);
	CHECK(ps4_uvd_build_ring(bytes, 4096, request, NULL) == PS4_UVD_PLAN_NULL);
	CHECK(ps4_uvd_build_ring(bytes, 4096, request,
		(struct ps4_uvd_ring_plan *)(void *)(bytes + 8)) == PS4_UVD_PLAN_OVERLAP);
	CHECK(ps4_uvd_build_ring(bytes, 4096, request,
		(struct ps4_uvd_ring_plan *)(void *)(bytes - 8)) == PS4_UVD_PLAN_OVERLAP);
	CHECK(ps4_uvd_build_ring(bytes, 4096, request,
		(struct ps4_uvd_ring_plan *)(void *)(bytes + 4096 - 8)) ==
	      PS4_UVD_PLAN_OVERLAP);
	CHECK(ps4_uvd_build_ring(bytes, 4096, request,
		(struct ps4_uvd_ring_plan *)(void *)(bytes + 1)) ==
	      PS4_UVD_PLAN_ALIGNMENT);
	/* Fabricated overflowing spans must be rejected without dereferencing. */
	CHECK(ps4_uvd_build_ring((void *)(maximum - 3U), 4096, request,
				&plan) == PS4_UVD_PLAN_RANGE);
	CHECK(ps4_uvd_build_ring(bytes, 4096, request,
		(struct ps4_uvd_ring_plan *)(maximum &
		~(uintptr_t)(_Alignof(struct ps4_uvd_ring_plan) - 1U))) ==
	      PS4_UVD_PLAN_RANGE);
	CHECK(memcmp(&ring, &original, sizeof(ring)) == 0);
	CHECK(memcmp(&plan, original_plan, sizeof(plan)) == 0);
	/* An exactly adjacent, naturally aligned plan is allowed. */
	CHECK((unsigned char *)(void *)&adjacent.plan == adjacent.bytes + 4096);
	CHECK(ps4_uvd_build_ring(adjacent.bytes, 4096, request,
				&adjacent.plan) == PS4_UVD_PLAN_OK);
}

static struct ps4_uvd_ring_observation observation_fixture(void)
{
	struct ps4_uvd_ring_observation observation = {
		.baseline_readback = UINT32_C(0xcafedead),
		.context_before_publish = UINT32_C(0xcafedead),
		.context = UINT32_C(0x10234567),
		.rptr_raw = 16,
		.wptr_raw = 16,
		.deadline_expired = 0
	};
	return observation;
}

static void test_observation_evidence(void)
{
	_Alignas(4) unsigned char bytes[4096];
	struct ps4_uvd_ring_plan plan;
	struct ps4_uvd_ring_plan broken;
	struct ps4_uvd_ring_request request = request_fixture();
	struct ps4_uvd_ring_observation observation;
	const uint32_t bad_pointers[] = {17, 64, 1024, 1040, UINT32_MAX};
	size_t i;

	CHECK(ps4_uvd_build_ring(bytes, 4096, request, &plan) == PS4_UVD_PLAN_OK);
	observation = observation_fixture();
	CHECK(ps4_uvd_classify_observation(&plan, &observation) ==
	      PS4_UVD_EXPECTED_OBSERVATION);
	CHECK(ps4_uvd_classify_observation(NULL, &observation) ==
	      PS4_UVD_OBSERVATION_INVALID);
	CHECK(ps4_uvd_classify_observation(&plan, NULL) == PS4_UVD_OBSERVATION_INVALID);
	for (i = 0; i < sizeof(bad_pointers) / sizeof(bad_pointers[0]); ++i) {
		observation = observation_fixture();
		observation.rptr_raw = bad_pointers[i];
		CHECK(ps4_uvd_classify_observation(&plan, &observation) ==
		      PS4_UVD_READ_POINTER_OUT_OF_RANGE);
		observation = observation_fixture();
		observation.wptr_raw = bad_pointers[i];
		CHECK(ps4_uvd_classify_observation(&plan, &observation) ==
		      PS4_UVD_WRITE_POINTER_MISMATCH);
	}
	observation = observation_fixture();
	observation.wptr_raw = 0;
	CHECK(ps4_uvd_classify_observation(&plan, &observation) ==
	      PS4_UVD_WRITE_POINTER_MISMATCH);
	for (i = 0; i < 16; ++i) {
		observation = observation_fixture();
		observation.rptr_raw = (uint32_t)i;
		CHECK(ps4_uvd_classify_observation(&plan, &observation) == PS4_UVD_INCOMPLETE);
		observation.context = request.baseline;
		CHECK(ps4_uvd_classify_observation(&plan, &observation) == PS4_UVD_INCOMPLETE);
	}
	observation = observation_fixture();
	observation.baseline_readback = request.token;
	CHECK(ps4_uvd_classify_observation(&plan, &observation) ==
	      PS4_UVD_BASELINE_UNCONFIRMED);
	observation = observation_fixture();
	observation.context_before_publish = request.token;
	CHECK(ps4_uvd_classify_observation(&plan, &observation) ==
	      PS4_UVD_BASELINE_UNCONFIRMED);
	observation = observation_fixture();
	observation.context = request.baseline;
	CHECK(ps4_uvd_classify_observation(&plan, &observation) ==
	      PS4_UVD_DRAINED_WITHOUT_TOKEN);
	observation.context = UINT32_MAX;
	CHECK(ps4_uvd_classify_observation(&plan, &observation) == PS4_UVD_CONTEXT_UNEXPECTED);
	observation.context = 0;
	CHECK(ps4_uvd_classify_observation(&plan, &observation) == PS4_UVD_CONTEXT_UNEXPECTED);
	observation = observation_fixture();
	observation.deadline_expired = 1;
	CHECK(ps4_uvd_classify_observation(&plan, &observation) == PS4_UVD_DEADLINE_EXPIRED);
	observation.deadline_expired = 2;
	CHECK(ps4_uvd_classify_observation(&plan, &observation) == PS4_UVD_OBSERVATION_INVALID);
	observation = observation_fixture();
	broken = plan;
	broken.publish_wptr_raw = 64;
	CHECK(ps4_uvd_classify_observation(&broken, &observation) == PS4_UVD_OBSERVATION_INVALID);
	broken = plan;
	broken.request.context_before_baseline = request.token;
	CHECK(ps4_uvd_classify_observation(&broken, &observation) == PS4_UVD_OBSERVATION_INVALID);
}

int main(void)
{
	test_packet_image();
	test_endian_and_tokens();
	test_interpreter_rejects_mutations();
	test_rejections_are_atomic();
	test_destination_boundaries();
	test_observation_evidence();
	puts("PASS: six host fixture groups; packet hypotheses only, no hardware evidence");
	return EXIT_SUCCESS;
}
