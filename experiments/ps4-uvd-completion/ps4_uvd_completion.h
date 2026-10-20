/* SPDX-License-Identifier: GPL-2.0-only */
/* Original host-only completion model; no device or buffer access. */
#ifndef PS4_UVD_COMPLETION_H
#define PS4_UVD_COMPLETION_H

#include <stdint.h>

enum ps4_uvd_sample_kind {
	PS4_UVD_SAMPLE_VALID,
	PS4_UVD_SAMPLE_TIMEOUT,
	PS4_UVD_SAMPLE_FAILURE
};

struct ps4_uvd_completion_sample {
	enum ps4_uvd_sample_kind kind;
	uint32_t word;
};

struct ps4_uvd_completion_state {
	uint32_t expected;
	uint32_t outstanding;
	uint32_t retired;
};

enum ps4_uvd_completion_status {
	PS4_UVD_COMPLETION_READY,
	PS4_UVD_COMPLETION_PENDING,
	PS4_UVD_COMPLETION_MISMATCH,
	PS4_UVD_COMPLETION_OVER_COMPLETION,
	PS4_UVD_COMPLETION_TIMEOUT,
	PS4_UVD_COMPLETION_FAILURE,
	PS4_UVD_COMPLETION_INVALID_SAMPLE,
	PS4_UVD_COMPLETION_INVALID_STATE
};

struct ps4_uvd_completion_decision {
	enum ps4_uvd_completion_status status;
	/* Zero on every result except READY. */
	uint32_t count;
};

/*
 * Caller supplies one previously captured word, by value. There is no second
 * sample, clock, polling operation, or access through a device pointer here.
 * Both input objects are passed by value; assessment cannot change ownership.
 */
static inline struct ps4_uvd_completion_decision
ps4_uvd_completion_assess(struct ps4_uvd_completion_state state,
			 struct ps4_uvd_completion_sample sample)
{
	struct ps4_uvd_completion_decision decision = {
		PS4_UVD_COMPLETION_INVALID_STATE, 0
	};
	uint32_t count;

	if (state.outstanding > UINT32_C(2))
		return decision;

	switch (sample.kind) {
	case PS4_UVD_SAMPLE_TIMEOUT:
		decision.status = PS4_UVD_COMPLETION_TIMEOUT;
		return decision;
	case PS4_UVD_SAMPLE_FAILURE:
		decision.status = PS4_UVD_COMPLETION_FAILURE;
		return decision;
	case PS4_UVD_SAMPLE_VALID:
		break;
	default:
		decision.status = PS4_UVD_COMPLETION_INVALID_SAMPLE;
		return decision;
	}

	if (sample.word == (uint32_t)(state.expected - UINT32_C(1))) {
		decision.status = PS4_UVD_COMPLETION_PENDING;
		return decision;
	}
	if (sample.word == state.expected)
		count = 1;
	else if (sample.word == (uint32_t)(state.expected + UINT32_C(1)))
		count = 2;
	else {
		decision.status = PS4_UVD_COMPLETION_MISMATCH;
		return decision;
	}

	if (count > state.outstanding) {
		decision.status = PS4_UVD_COMPLETION_OVER_COMPLETION;
		return decision;
	}
	decision.status = PS4_UVD_COMPLETION_READY;
	decision.count = count;
	return decision;
}

/*
 * Explicit scalar-state commit. The caller serializes access to state and
 * retains its own buffers. A rejected decision leaves every state field intact.
 * Sequence and retirement counters intentionally wrap modulo 2^32.
 */
static inline struct ps4_uvd_completion_decision
ps4_uvd_completion_commit(struct ps4_uvd_completion_state *state,
			 struct ps4_uvd_completion_sample sample)
{
	struct ps4_uvd_completion_decision decision = {
		PS4_UVD_COMPLETION_INVALID_STATE, 0
	};

	if (!state)
		return decision;
	decision = ps4_uvd_completion_assess(*state, sample);
	if (decision.status == PS4_UVD_COMPLETION_READY) {
		state->expected = (uint32_t)(state->expected + decision.count);
		state->outstanding -= decision.count;
		state->retired = (uint32_t)(state->retired + decision.count);
	}
	return decision;
}

#endif
