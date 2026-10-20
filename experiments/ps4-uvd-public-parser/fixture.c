/* SPDX-License-Identifier: GPL-2.0-only */
/* Original host fixture. Public parser functions are extracted by check.py.
 * Fake callbacks exercise control flow only: they do not validate messages.
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>

/* PUBLIC_DEFINES */
struct amdgpu_device { struct { bool address_64_bit; } uvd; };
struct amdgpu_cs_parser { struct amdgpu_device *adev; };
struct amdgpu_job { void *vm; };
struct amdgpu_ib { uint32_t *ptr; unsigned length_dw, capacity; };
/* PUBLIC_CONTEXT */

static char error_text[160];
static unsigned calls[2], cases;
static int errors[2];
static bool callback_marks_message;
#define DRM_ERROR(...) ((void)snprintf(error_text, sizeof(error_text), __VA_ARGS__))

static uint32_t amdgpu_ib_get_value(struct amdgpu_ib *ib, unsigned index)
{
	assert(index < ib->length_dw && index < ib->capacity);
	return ib->ptr[index];
}
static int callback(struct amdgpu_uvd_cs_ctx *ctx, unsigned pass)
{
	assert(pass < 2);
	++calls[pass];
	if (errors[pass])
		return errors[pass];
	if (callback_marks_message)
		ctx->has_msg_cmd = true; /* Explicit stub, not real message validation. */
	return 0;
}
static int amdgpu_uvd_cs_pass1(struct amdgpu_uvd_cs_ctx *ctx) { return callback(ctx, 0); }
static int amdgpu_uvd_cs_pass2(struct amdgpu_uvd_cs_ctx *ctx) { return callback(ctx, 1); }

/* PUBLIC_PARSER_FUNCTIONS */

static void clear_stubs(void)
{
	memset(calls, 0, sizeof(calls));
	memset(errors, 0, sizeof(errors));
	error_text[0] = 0;
	callback_marks_message = false;
}
static void nops(uint32_t words[16])
{
	unsigned i;
	for (i = 0; i < 16; i += 2) {
		words[i] = 0x00003bff; /* Literal independent wire words. */
		words[i + 1] = 0;
	}
}
static int packet_policy(uint32_t words[16], unsigned length)
{
	struct amdgpu_ib ib = { words, length, 16 };
	struct amdgpu_uvd_cs_ctx ctx = { .ib = &ib };
	return amdgpu_uvd_cs_packets(&ctx, amdgpu_uvd_cs_pass2);
}
static int whole_policy(uint32_t words[16], unsigned length, bool address64)
{
	uint32_t copy[16];
	struct amdgpu_device device = { .uvd.address_64_bit = address64 };
	struct amdgpu_cs_parser parser = { &device };
	struct amdgpu_ib ib = { words, length, 16 };
	struct amdgpu_job job = { .vm = &device };
	int result;
	assert(length <= 16);
	memcpy(copy, words, sizeof(copy));
	result = amdgpu_uvd_ring_parse_cs(&parser, &job, &ib);
	assert(job.vm == NULL);
	assert(memcmp(copy, words, sizeof(copy)) == 0);
	return result;
}
static void no_command_case(const char *name, uint32_t words[16], int packet_result)
{
	unsigned address64;
	for (address64 = 0; address64 <= 1; ++address64) {
		clear_stubs();
		assert(packet_policy(words, 16) == packet_result);
		assert(!calls[0] && !calls[1]);
		assert(whole_policy(words, 16, address64) == -EINVAL);
		assert(!calls[0] && !calls[1]);
		if (!packet_result)
			assert(strstr(error_text, "need a msg command"));
		++cases;
	}
	printf("%s: packet=%d, full=-EINVAL, no command callback (both address modes)\n", name, packet_result);
}
int main(void)
{
	uint32_t words[16];
	unsigned i, address64;
	nops(words);
	no_command_case("NOP-only", words, 0);
	words[0] = 0x00003bc4; words[1] = 0x11223344;
	words[2] = 0x00003bc5; words[3] = 0x55667788;
	no_command_case("DATA0/DATA1-only", words, 0);
	nops(words); words[0] = 0x00003dbd; words[1] = 0x10203040;
	no_command_case("CONTEXT_ID", words, -EINVAL);
	nops(words); words[0] = 0x00001234;
	no_command_case("unknown register", words, -EINVAL);
	nops(words); words[0] = 0x40000000;
	no_command_case("packet type1", words, -EINVAL);
	nops(words); words[0] = 0xc0000000;
	no_command_case("packet type3", words, -EINVAL);
	for (i = 0; i < 16; ++i) words[i] = 0x80000000;
	no_command_case("type2-only", words, 0);
	/* A count-one packet needs two payload words, but only one remains. */
	nops(words); words[14] = 0x00013bc4;
	no_command_case("truncated register burst", words, -EINVAL);

	for (address64 = 0; address64 <= 1; ++address64) {
		clear_stubs(); nops(words);
		assert(whole_policy(words, 15, address64) == -EINVAL);
		assert(strstr(error_text, "not 16 dwords aligned") && !calls[0] && !calls[1]);
		++cases;
		clear_stubs();
		assert(whole_policy(words, 0, address64) == -EINVAL);
		assert(strstr(error_text, "need a msg command") && !calls[0] && !calls[1]);
		++cases;
		/* Layout from the public send_msg path; no real message is supplied. */
		clear_stubs(); nops(words);
		words[0] = 0x00003bc4; words[1] = 0x11223344;
		words[2] = 0x00003bc5; words[3] = 0x55667788;
		words[4] = 0x00003bc3; words[5] = 0;
		assert(whole_policy(words, 16, address64) == -EINVAL);
		assert(calls[0] == !address64 && calls[1] == 1);
		assert(strstr(error_text, "need a msg command"));
		++cases;
		clear_stubs(); callback_marks_message = true;
		assert(whole_policy(words, 16, address64) == 0);
		assert(calls[0] == !address64 && calls[1] == 1 && !error_text[0]);
		++cases;
		clear_stubs(); errors[1] = -ENOSPC;
		assert(whole_policy(words, 16, address64) == -ENOSPC);
		assert(calls[0] == !address64 && calls[1] == 1);
		++cases;
	}
	clear_stubs(); errors[0] = -EIO;
	assert(whole_policy(words, 16, false) == -EIO && calls[0] == 1 && calls[1] == 0);
	++cases;
	printf("PASS: %u public parser policy cases; callback success is a stub control, not message or hardware validation\n", cases);
	return 0;
}
