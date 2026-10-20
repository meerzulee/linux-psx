/* SPDX-License-Identifier: GPL-2.0-only */
/* Original fixture: fixed contract cases and independent ownership sequences. */
#include "ps4_uvd_completion.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned int checks;

static void check(int condition, const char *expression, int line)
{
	checks++;
	if (!condition) {
		fprintf(stderr, "line %d: %s\n", line, expression);
		exit(EXIT_FAILURE);
	}
}

#define CHECK(expression) check((expression), #expression, __LINE__)

struct owned_state {
	uint32_t guard_before;
	struct ps4_uvd_completion_state state;
	unsigned char retained_buffers[32];
	uint32_t guard_after;
};

static struct owned_state owned(uint32_t expected, uint32_t outstanding,
				uint32_t retired)
{
	struct owned_state value;

	memset(&value, 0xa5, sizeof(value));
	value.state.expected = expected;
	value.state.outstanding = outstanding;
	value.state.retired = retired;
	return value;
}

static struct ps4_uvd_completion_sample sample(uint32_t word)
{
	struct ps4_uvd_completion_sample value = { PS4_UVD_SAMPLE_VALID, word };
	return value;
}

static void same_owned(const struct owned_state *actual,
		       const struct owned_state *expected)
{
	CHECK(actual->guard_before == expected->guard_before);
	CHECK(actual->guard_after == expected->guard_after);
	CHECK(memcmp(actual->retained_buffers, expected->retained_buffers,
		     sizeof(actual->retained_buffers)) == 0);
	CHECK(actual->state.expected == expected->state.expected);
	CHECK(actual->state.outstanding == expected->state.outstanding);
	CHECK(actual->state.retired == expected->state.retired);
}

static void rejected(struct owned_state *value,
		     struct ps4_uvd_completion_sample observation,
		     enum ps4_uvd_completion_status status)
{
	struct owned_state before = *value;
	struct ps4_uvd_completion_decision decision;

	decision = ps4_uvd_completion_assess(value->state, observation);
	CHECK(decision.status == status);
	CHECK(decision.count == 0);
	same_owned(value, &before);
	decision = ps4_uvd_completion_commit(&value->state, observation);
	CHECK(decision.status == status);
	CHECK(decision.count == 0);
	same_owned(value, &before);
}

/* Literal expectations catch >=, signed-order, saturation and wrap mistakes. */
static void boundary_cases(void)
{
	static const struct {
		uint32_t expected, word;
		enum ps4_uvd_completion_status status;
		uint32_t count;
	} cases[] = {
		{1, 0, PS4_UVD_COMPLETION_PENDING, 0},
		{1, 1, PS4_UVD_COMPLETION_READY, 1},
		{1, 2, PS4_UVD_COMPLETION_READY, 2},
		{1, 3, PS4_UVD_COMPLETION_MISMATCH, 0},
		{1, UINT32_MAX, PS4_UVD_COMPLETION_MISMATCH, 0},
		{0, UINT32_MAX, PS4_UVD_COMPLETION_PENDING, 0},
		{0, 0, PS4_UVD_COMPLETION_READY, 1},
		{0, 1, PS4_UVD_COMPLETION_READY, 2},
		{0, 2, PS4_UVD_COMPLETION_MISMATCH, 0},
		{0, UINT32_C(0xfffffffe), PS4_UVD_COMPLETION_MISMATCH, 0},
		{UINT32_MAX, UINT32_C(0xfffffffe), PS4_UVD_COMPLETION_PENDING, 0},
		{UINT32_MAX, UINT32_MAX, PS4_UVD_COMPLETION_READY, 1},
		{UINT32_MAX, 0, PS4_UVD_COMPLETION_READY, 2},
		{UINT32_MAX, 1, PS4_UVD_COMPLETION_MISMATCH, 0},
		{UINT32_C(0x7fffffff), UINT32_C(0x7ffffffe), PS4_UVD_COMPLETION_PENDING, 0},
		{UINT32_C(0x7fffffff), UINT32_C(0x80000000), PS4_UVD_COMPLETION_READY, 2},
		{UINT32_C(0x7fffffff), UINT32_C(0x80000001), PS4_UVD_COMPLETION_MISMATCH, 0},
		{UINT32_C(0x80000000), UINT32_C(0x7fffffff), PS4_UVD_COMPLETION_PENDING, 0},
		{UINT32_C(0x80000000), UINT32_C(0x80000000), PS4_UVD_COMPLETION_READY, 1},
		{UINT32_C(0x80000000), UINT32_C(0x80000001), PS4_UVD_COMPLETION_READY, 2},
		{UINT32_C(0x80000000), 0, PS4_UVD_COMPLETION_MISMATCH, 0},
		{0, UINT32_C(0x80000000), PS4_UVD_COMPLETION_MISMATCH, 0}
	};
	size_t i;
	uint32_t outstanding;

	for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
		for (outstanding = 0; outstanding <= 2; outstanding++) {
			struct owned_state value = owned(cases[i].expected, outstanding, 17);
			struct owned_state before = value;
			struct ps4_uvd_completion_decision decision;
			enum ps4_uvd_completion_status status = cases[i].status;
			uint32_t count = cases[i].count;

			if (count > outstanding) {
				status = PS4_UVD_COMPLETION_OVER_COMPLETION;
				count = 0;
			}
			decision = ps4_uvd_completion_assess(value.state, sample(cases[i].word));
			CHECK(decision.status == status);
			CHECK(decision.count == count);
			same_owned(&value, &before);
			if (status != PS4_UVD_COMPLETION_READY)
				rejected(&value, sample(cases[i].word), status);
		}
	}
}

static void failures_retain_ownership(void)
{
	uint32_t outstanding;
	unsigned int kind;

	for (outstanding = 0; outstanding <= 2; outstanding++) {
		for (kind = 0; kind < 2; kind++) {
			struct owned_state value = owned(UINT32_MAX, outstanding, UINT32_MAX);
			struct ps4_uvd_completion_sample observation = sample(0);

			observation.kind = kind ? PS4_UVD_SAMPLE_FAILURE : PS4_UVD_SAMPLE_TIMEOUT;
			rejected(&value, observation, kind ? PS4_UVD_COMPLETION_FAILURE :
				 PS4_UVD_COMPLETION_TIMEOUT);
		}
	}
	{
		struct owned_state value = owned(1, 2, 0);
		struct ps4_uvd_completion_sample observation = sample(2);

		observation.kind = (enum ps4_uvd_sample_kind)99;
		rejected(&value, observation, PS4_UVD_COMPLETION_INVALID_SAMPLE);
		value.state.outstanding = 3;
		rejected(&value, sample(1), PS4_UVD_COMPLETION_INVALID_STATE);
		value.state.outstanding = UINT32_MAX;
		rejected(&value, sample(1), PS4_UVD_COMPLETION_INVALID_STATE);
	}
	{
		struct ps4_uvd_completion_decision decision;

		decision = ps4_uvd_completion_commit(NULL, sample(1));
		CHECK(decision.status == PS4_UVD_COMPLETION_INVALID_STATE);
		CHECK(decision.count == 0);
	}
}

/* Expected states are literal queue outcomes, independent of the classifier. */
static void commit_sequences(void)
{
	struct owned_state value = owned(10, 2, 7);
	struct owned_state expected = owned(11, 1, 8);
	struct ps4_uvd_completion_sample captured = sample(10);
	struct ps4_uvd_completion_decision decision;
	uint32_t changing_external_word = 10;

	/* A later external change cannot affect the already captured value. */
	captured.word = changing_external_word;
	changing_external_word = 12;
	CHECK(changing_external_word == 12);
	rejected(&value, sample(9), PS4_UVD_COMPLETION_PENDING);
	decision = ps4_uvd_completion_commit(&value.state, captured);
	CHECK(decision.status == PS4_UVD_COMPLETION_READY && decision.count == 1);
	same_owned(&value, &expected);
	rejected(&value, captured, PS4_UVD_COMPLETION_PENDING);
	rejected(&value, sample(12), PS4_UVD_COMPLETION_OVER_COMPLETION);
	decision = ps4_uvd_completion_commit(&value.state, sample(11));
	CHECK(decision.status == PS4_UVD_COMPLETION_READY && decision.count == 1);
	expected = owned(12, 0, 9);
	same_owned(&value, &expected);
	rejected(&value, sample(12), PS4_UVD_COMPLETION_OVER_COMPLETION);
	rejected(&value, sample(11), PS4_UVD_COMPLETION_PENDING);

	value = owned(UINT32_MAX, 2, UINT32_C(0xfffffffe));
	decision = ps4_uvd_completion_commit(&value.state, sample(0));
	CHECK(decision.status == PS4_UVD_COMPLETION_READY && decision.count == 2);
	expected = owned(1, 0, 0);
	same_owned(&value, &expected);
	rejected(&value, sample(0), PS4_UVD_COMPLETION_PENDING);

	value = owned(0, 2, UINT32_MAX);
	rejected(&value, sample(UINT32_MAX), PS4_UVD_COMPLETION_PENDING);
	decision = ps4_uvd_completion_commit(&value.state, sample(0));
	CHECK(decision.status == PS4_UVD_COMPLETION_READY && decision.count == 1);
	expected = owned(1, 1, 0);
	same_owned(&value, &expected);
	decision = ps4_uvd_completion_commit(&value.state, sample(1));
	CHECK(decision.status == PS4_UVD_COMPLETION_READY && decision.count == 1);
	expected = owned(2, 0, 1);
	same_owned(&value, &expected);
}

int main(void)
{
	boundary_cases();
	failures_retain_ownership();
	commit_sequences();
	printf("completion contract: %u checks passed\n", checks);
	return EXIT_SUCCESS;
}
