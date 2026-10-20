/* SPDX-License-Identifier: GPL-2.0-only */
/* Actual serializer linked with extracted public serialization-only references. */
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ps4_uvd_message.h"

#define CHECK(x, name) do { if (!(x)) { \
	fprintf(stderr, "FAIL:%s line=%d\n", name, __LINE__); exit(2); } } while (0)
static unsigned int cases;

static uint32_t cpu_to_le32(uint32_t value)
{
	const unsigned char bytes[4] = {
		(unsigned char)value, (unsigned char)(value >> 8),
		(unsigned char)(value >> 16), (unsigned char)(value >> 24)
	};
	uint32_t native;
	memcpy(&native, bytes, 4);
	return native;
}

#include "public_reference.h"

_Static_assert(sizeof(struct ruvd_msg) == 0xde4, "public message size");
_Static_assert(offsetof(struct ruvd_msg, size) == 0, "public size offset");
_Static_assert(offsetof(struct ruvd_msg, msg_type) == 4, "public type offset");
_Static_assert(offsetof(struct ruvd_msg, stream_handle) == 8, "public handle offset");
_Static_assert(offsetof(struct ruvd_msg, status_report_feedback_number) == 12, "feedback offset");
_Static_assert(offsetof(struct ruvd_msg, body.create.stream_type) == 16, "stream offset");
_Static_assert(offsetof(struct ruvd_msg, body.create.session_flags) == 20, "flags offset");
_Static_assert(offsetof(struct ruvd_msg, body.create.asic_id) == 24, "ASIC offset");
_Static_assert(offsetof(struct ruvd_msg, body.create.width_in_samples) == 28, "width offset");
_Static_assert(offsetof(struct ruvd_msg, body.create.height_in_samples) == 32, "height offset");
_Static_assert(offsetof(struct ruvd_msg, body.create.dpb_buffer) == 36, "DPB buffer offset");
_Static_assert(offsetof(struct ruvd_msg, body.create.dpb_size) == 40, "DPB size offset");
_Static_assert(offsetof(struct ruvd_msg, body.create.dpb_model) == 44, "DPB model offset");
_Static_assert(offsetof(struct ruvd_msg, body.create.version_info) == 48, "version offset");
_Static_assert(RUVD_MSG_CREATE == 0 && RUVD_MSG_DESTROY == 2 && RUVD_CODEC_H264 == 0,
	       "public common constants");

static uint32_t word(const unsigned char *p)
{
	return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
	       (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static void zero_range(const unsigned char *page, size_t first, size_t end)
{
	for (size_t i = first; i < end; ++i)
		CHECK(page[i] == 0, "PAGE_ZERO");
}

static void linux_messages(void)
{
	const u32 handles[] = { 0x11223344, 1, 0x80000000U, UINT32_MAX };
	unsigned char page[4096];
	uint32_t reference[1024];

	for (size_t i = 0; i < sizeof(handles) / sizeof(handles[0]); ++i) {
		memset(page, 0xa5, sizeof(page));
		CHECK(ps4_uvd_msg_create_linux_dummy(page, sizeof(page), handles[i]) == 0,
		      "DUMMY_RETURN");
		CHECK(word(page) == 0xde4, "DECLARED_LENGTH");
		CHECK(word(page + 4) == 0 && word(page + 8) == handles[i], "CREATE_HEADER");
		CHECK(word(page + 28) == 1920 && word(page + 32) == 1088, "LINUX_DIMENSIONS");
		CHECK(word(page + 40) == 0x01b37000, "LINUX_DPB");
		zero_range(page, 12, 28);
		zero_range(page, 36, 40);
		zero_range(page, 44, sizeof(page));
		reference_linux_create(reference, handles[i]);
		CHECK(memcmp(page, reference, sizeof(page)) == 0, "LINUX_CREATE_BYTES");
		if (handles[i] == 0x11223344)
			CHECK(memcmp(page, golden_linux_create, sizeof(page)) == 0, "CREATE_GOLDEN");
		++cases;
		CHECK(ps4_uvd_msg_destroy(page, sizeof(page), handles[i]) == 0, "DESTROY_RETURN");
		CHECK(word(page) == 0xde4, "DECLARED_LENGTH");
		CHECK(word(page + 4) == 2, "DESTROY_TYPE");
		CHECK(word(page + 8) == handles[i], "DESTROY_HANDLE");
		zero_range(page, 12, sizeof(page));
		reference_linux_destroy(reference, handles[i]);
		CHECK(memcmp(page, reference, sizeof(page)) == 0, "LINUX_DESTROY_BYTES");
		if (handles[i] == 0x11223344)
			CHECK(memcmp(page, golden_linux_destroy, sizeof(page)) == 0, "DESTROY_GOLDEN");
		++cases;
	}
}

static void mesa_dimensions(void)
{
	const struct ps4_uvd_avc_create parameters[] = {
		{640, 360, 0}, {1920, 1088, 0}, {1, 1, 0},
	};
	unsigned char page[4096], reference_bytes[sizeof(struct ruvd_msg)];
	struct ruvd_msg reference;

	for (size_t n = 0; n < sizeof(parameters) / sizeof(parameters[0]); ++n) {
		CHECK(ps4_uvd_msg_create_avc(page, sizeof(page), 0xa1b2c3d4, &parameters[n]) == 0,
		      "MESA_CREATE_RETURN");
		reference_mesa_create(&reference, 0xa1b2c3d4, parameters[n].width, parameters[n].height);
		/* Mesa assigns native u32s. Normalize each value to LE for this
		 * byte comparison, without assuming the host's byte order. */
		for (size_t i = 0; i < sizeof(reference); i += 4) {
			uint32_t value;
			memcpy(&value, (unsigned char *)&reference + i, 4);
			value = cpu_to_le32(value);
			memcpy(reference_bytes + i, &value, 4);
		}
		CHECK(memcmp(page, reference_bytes, sizeof(reference_bytes)) == 0, "MESA_CREATE_BYTES");
		CHECK(word(page + 40) == 0, "MESA_DPB_ZERO");
		zero_range(page, sizeof(reference), sizeof(page));
		++cases;
	}
	reference_mesa_destroy(&reference, 0xa1b2c3d4);
	CHECK(ps4_uvd_msg_destroy(page, sizeof(page), 0xa1b2c3d4) == 0, "MESA_DESTROY_RETURN");
	for (size_t i = 0; i < sizeof(reference); i += 4) {
		uint32_t value;
		memcpy(&value, (unsigned char *)&reference + i, 4);
		CHECK(word(page + i) == value, "MESA_DESTROY_BYTES");
	}
	++cases;
}

static void explicit_bytes_and_alias(void)
{
	unsigned char page[4096], copy[4096];
	struct ps4_uvd_avc_create params = {0x01020304, 0x10203040, 0xabcdef01};
	const unsigned char expected_handle[] = {0x44, 0x33, 0x22, 0x11};
	const unsigned char expected_width[] = {0x04, 0x03, 0x02, 0x01};
	const unsigned char expected_height[] = {0x40, 0x30, 0x20, 0x10};
	const unsigned char expected_dpb[] = {0x01, 0xef, 0xcd, 0xab};
	union { unsigned char page[4096]; struct ps4_uvd_avc_create params; } overlap;

	CHECK(ps4_uvd_msg_create_avc(page, sizeof(page), 0x11223344, &params) == 0, "BYTES_RETURN");
	CHECK(memcmp(page + 8, expected_handle, 4) == 0 &&
	      memcmp(page + 28, expected_width, 4) == 0 &&
	      memcmp(page + 32, expected_height, 4) == 0 &&
	      memcmp(page + 40, expected_dpb, 4) == 0, "EXPLICIT_LITTLE_ENDIAN");
	memcpy(copy, page, sizeof(copy));
	memset(page, 0x5a, sizeof(page));
	CHECK(ps4_uvd_msg_create_avc(page, sizeof(page), 0x11223344, &params) == 0 &&
	      memcmp(copy, page, sizeof(copy)) == 0, "DETERMINISTIC");
	++cases;
	overlap.params = params;
	CHECK(ps4_uvd_msg_create_avc(overlap.page, sizeof(overlap.page), 0x11223344, &overlap.params) == 0 &&
	      memcmp(copy, overlap.page, sizeof(copy)) == 0, "PARAMS_ALIAS");
	++cases;
	params = (struct ps4_uvd_avc_create){UINT32_MAX, UINT32_MAX, UINT32_MAX};
	CHECK(ps4_uvd_msg_create_avc(page, sizeof(page), UINT32_MAX, &params) == 0 &&
	      word(page + 28) == UINT32_MAX && word(page + 32) == UINT32_MAX &&
	      word(page + 40) == UINT32_MAX, "U32_LITERAL_FIELDS");
	++cases;
}

static void buffer_boundaries(void)
{
	unsigned char storage[4096 + 16];
	const struct ps4_uvd_avc_create params = {1920, 1088, 123};

	for (unsigned int offset = 0; offset < 8; ++offset) {
		memset(storage, 0x5a, sizeof(storage));
		CHECK(ps4_uvd_msg_create_avc(storage + offset, 4096, 7, &params) == 0, "UNALIGNED_CREATE");
		for (unsigned int i = 0; i < sizeof(storage); ++i)
			if (i < offset || i >= offset + 4096)
				CHECK(storage[i] == 0x5a, "OUTPUT_GUARDS");
		CHECK(ps4_uvd_msg_destroy(storage + offset, 4096, 7) == 0, "UNALIGNED_DESTROY");
		for (unsigned int i = 0; i < sizeof(storage); ++i)
			if (i < offset || i >= offset + 4096)
				CHECK(storage[i] == 0x5a, "OUTPUT_GUARDS");
		++cases;
	}
}

static void invalid_inputs_preserve_output(void)
{
	unsigned char page[4096], before[4096];
	struct ps4_uvd_avc_create params = {1920, 1088, 0};
	const size_t sizes[] = {0, 0xde4, 4095, 4097, SIZE_MAX};

	memset(page, 0x93, sizeof(page));
	memcpy(before, page, sizeof(page));
	for (size_t i = 0; i < sizeof(sizes) / sizeof(sizes[0]); ++i) {
		CHECK(ps4_uvd_msg_create_avc(page, sizes[i], 1, &params) == -EINVAL &&
		      ps4_uvd_msg_create_linux_dummy(page, sizes[i], 1) == -EINVAL &&
		      ps4_uvd_msg_destroy(page, sizes[i], 1) == -EINVAL &&
		      memcmp(page, before, sizeof(page)) == 0, "INVALID_SIZE_UNCHANGED");
		++cases;
	}
	CHECK(ps4_uvd_msg_create_avc(page, sizeof(page), 0, &params) == -EINVAL &&
	      ps4_uvd_msg_create_linux_dummy(page, sizeof(page), 0) == -EINVAL &&
	      ps4_uvd_msg_destroy(page, sizeof(page), 0) == -EINVAL &&
	      memcmp(page, before, sizeof(page)) == 0, "ZERO_HANDLE_UNCHANGED");
	++cases;
	CHECK(ps4_uvd_msg_create_avc(NULL, sizeof(page), 1, &params) == -EINVAL &&
	      ps4_uvd_msg_create_linux_dummy(NULL, sizeof(page), 1) == -EINVAL &&
	      ps4_uvd_msg_destroy(NULL, sizeof(page), 1) == -EINVAL, "NULL_PAGE");
	++cases;
	CHECK(ps4_uvd_msg_create_avc(page, sizeof(page), 1, NULL) == -EINVAL &&
	      memcmp(page, before, sizeof(page)) == 0, "NULL_PARAMS_UNCHANGED");
	++cases;
	params.width = 0;
	CHECK(ps4_uvd_msg_create_avc(page, sizeof(page), 1, &params) == -EINVAL &&
	      memcmp(page, before, sizeof(page)) == 0, "ZERO_DIMENSION_UNCHANGED");
	++cases;
	params.width = 1; params.height = 0;
	CHECK(ps4_uvd_msg_create_avc(page, sizeof(page), 1, &params) == -EINVAL &&
	      memcmp(page, before, sizeof(page)) == 0, "ZERO_DIMENSION_UNCHANGED");
	++cases;
}

int main(void)
{
	linux_messages();
	mesa_dimensions();
	explicit_bytes_and_alias();
	buffer_boundaries();
	invalid_inputs_preserve_output();
	printf("PASS: %u actual-source message serializer cases; Linux goldens, Mesa fields, LE bytes, guards and validation\n", cases);
	return 0;
}
