/* SPDX-License-Identifier: MIT */
/* Host-only fixture. Public reference functions write synthetic CPU arrays. */
#include <linux/errno.h>
#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ps4_uvd_decode_message.h"
#include "public_reference.h"
#include "fixture_params.h"

static unsigned cases;
#define CHECK(c, w) do { if (!(c)) { \
	fprintf(stderr, "FAIL:%s line=%d\n", w, __LINE__); exit(2); \
} } while (0)

_Static_assert(sizeof(struct ruvd_msg) == 3556, "public declared length");
_Static_assert(sizeof(struct ruvd_h264) == 1116, "public AVC size");
#define OFFSET(f, n) _Static_assert(offsetof(struct ruvd_msg, f) == n, #f)
OFFSET(body.decode.decode_flags, 20);
OFFSET(body.decode.width_in_samples, 24);
OFFSET(body.decode.height_in_samples, 28);
OFFSET(body.decode.dpb_size, 36);
OFFSET(body.decode.db_pitch, 52);
OFFSET(body.decode.bsd_size, 88);
OFFSET(body.decode.dt_pitch, 112);
OFFSET(body.decode.dt_chroma_top_offset, 136);
OFFSET(body.decode.codec, 224);
OFFSET(body.decode.codec.h264.profile, 224);
OFFSET(body.decode.codec.h264.level, 228);
OFFSET(body.decode.codec.h264.sps_info_flags, 232);
OFFSET(body.decode.codec.h264.pps_info_flags, 236);
OFFSET(body.decode.codec.h264.chroma_format, 240);
OFFSET(body.decode.codec.h264.pic_order_cnt_type, 244);
OFFSET(body.decode.codec.h264.num_ref_frames, 246);
OFFSET(body.decode.codec.h264.pic_init_qp_minus26, 248);
OFFSET(body.decode.codec.h264.scaling_list_4x4, 260);
OFFSET(body.decode.codec.h264.scaling_list_8x8, 356);
OFFSET(body.decode.codec.h264.frame_num, 484);
OFFSET(body.decode.codec.h264.frame_num_list, 488);
OFFSET(body.decode.codec.h264.curr_field_order_cnt_list, 552);
OFFSET(body.decode.codec.h264.field_order_cnt_list, 560);
OFFSET(body.decode.codec.h264.decoded_pic_idx, 688);
OFFSET(body.decode.codec.h264.curr_pic_ref_frame_num, 692);
OFFSET(body.decode.codec.h264.ref_frame_list, 696);
OFFSET(body.decode.extension_support, 3296);
OFFSET(body.decode.extension_reserved, 3300);

static u8 fixture[2905], broken_fixture[2905];
static struct {
	u64 before;
	struct ps4_uvd_decode_plan plan;
	u64 after;
} actual;
static struct ps4_uvd_decode_plan untouched, repeat;

static u32 word(const u8 *bytes)
{
	return (u32)bytes[0] | (u32)bytes[1] << 8 |
	       (u32)bytes[2] << 16 | (u32)bytes[3] << 24;
}

static struct ps4_uvd_decode_input valid_input(void)
{
	struct ps4_uvd_decode_input in = {
		.vcpu_segment_base = 0x300000000ULL,
		.main_va = 0x300308000ULL, .work_va = 0x30030a000ULL,
		.main_bytes = 4096, .work_bytes = 0x40000,
		.handle = 0x11223344, .fence_initial = 0x55564343,
		.fence_sequence = 0x44454346, .fixture = fixture,
		.fixture_bytes = sizeof(fixture),
	};

	memcpy(in.verified_fixture_sha256, expected_fixture_sha256, 32);
	return in;
}

static void reference_decode(const struct ps4_uvd_decode_input *in,
			     u8 embedded[6144], uint32_t ib[64])
{
	struct ac_uvd_decoder dec = {
		.base = { .param = { .codec = AC_VIDEO_CODEC_AVC },
			  .max_decode_cmd_dw = 64 },
		.family = CHIP_KAVERI, .gfx_level = GFX7, .is_amdgpu = true,
		.stream_type = RUVD_CODEC_H264, .stream_handle = in->handle,
		.dpb_alignment = 16, .feedback_offset = 4096, .feedback_size = 2048,
		.reg = { RUVD_GPCOM_VCPU_DATA0, RUVD_GPCOM_VCPU_DATA1,
			 RUVD_GPCOM_VCPU_CMD, RUVD_ENGINE_CNTL },
	};
	struct radeon_surf surface = {
		.blk_w = 1, .u.legacy.level = {{ .nblk_x = 256 }},
	};
	struct ac_video_dec_decode_cmd cmd = {
		.cmd_buffer = ib, .embedded_ptr = embedded,
		.embedded_va = in->work_va + 0x3000,
		.width = 64, .height = 64, .bitstream_size = 2432,
		.bitstream_va = in->work_va + 0x5000,
		.ref_surfaces = {{ .size = 157184,
			.planes = {{ .va = in->work_va + 0xc000 }} }},
		.decode_surface = { .planes = {
			{ .va = in->work_va + 0x6000, .surf = &surface },
			{ .va = in->work_va + 0xa000 },
		}},
	};
	struct ac_video_dec_avc *avc = &cmd.codec_param.avc;

	avc->profile_idc = FIXTURE_PROFILE_IDC;
	avc->level_idc = FIXTURE_LEVEL_IDC;
	avc->sps_flags.direct_8x8_inference_flag = FIXTURE_DIRECT_8X8_INFERENCE_FLAG;
	avc->sps_flags.frame_mbs_only_flag = FIXTURE_FRAME_MBS_ONLY_FLAG;
	avc->pps_flags.deblocking_filter_control_present_flag = FIXTURE_DEBLOCKING_FILTER_CONTROL_PRESENT_FLAG;
	avc->pic_flags.chroma_format_idc = 1;
	avc->pic_order_cnt_type = FIXTURE_PIC_ORDER_CNT_TYPE;
	avc->max_num_ref_frames = FIXTURE_MAX_NUM_REF_FRAMES;
	avc->pic_init_qp_minus26 = FIXTURE_PIC_INIT_QP_MINUS26;
	memset(avc->scaling_list_4x4, 16, sizeof(avc->scaling_list_4x4));
	memset(avc->scaling_list_8x8, 16, sizeof(avc->scaling_list_8x8));
	memset(embedded, 0, 6144);
	memset(ib, 0, 64 * sizeof(*ib));
	CHECK(uvd_build_decode_cmd(&dec.base, &cmd) == 0, "PUBLIC_REFERENCE_RETURN");
	CHECK(cmd.out.cmd_dw == 32, "PUBLIC_REFERENCE_32_DW");
}

static void check_plan(const struct ps4_uvd_decode_input *in)
{
	u8 embedded[6144], create[4096];
	uint32_t reference_ib[64];
	struct amdgpu_ring ring = {0}, fence = {0};
	struct amdgpu_ib linux_ib = { .gpu_addr = in->work_va + 0x80, .length_dw = 32 };
	struct ruvd_msg expected_create = {0};
	const struct ps4_uvd_decode_plan *p = &actual.plan;
	unsigned i;

	CHECK(word(p->message_le + 228) == FIXTURE_LEVEL_IDC, "AVC_LEVEL");
	CHECK(word(p->message_le + 136) == 0x4000, "TARGET_UV_OFFSET");
	CHECK(word(p->feedback_le) == 2048, "FEEDBACK_LENGTH");
	reference_decode(in, embedded, reference_ib);
	CHECK(memcmp(p->message_le, embedded, 4096) == 0, "PUBLIC_MESSAGE_BYTES");
	CHECK(memcmp(p->feedback_le, embedded + 4096, 2048) == 0, "PUBLIC_FEEDBACK_BYTES");
	for (i = 0; i < 32; ++i)
		CHECK(word(p->ib_le + i * 4) == reference_ib[i], "PUBLIC_IB_BYTES");
	public_emit_ib(&ring, &linux_ib);
	public_emit_fence(&fence, in->work_va + 0x2080, in->fence_sequence);
	CHECK(ring.cdw == 6 && fence.cdw == 14, "PUBLIC_MAIN_COUNTS");
	CHECK(fence.words[8] == 0x3bc4 && !fence.words[9] &&
	      fence.words[10] == 0x3bc5 && !fence.words[11] &&
	      fence.words[12] == 0x3bc3 && fence.words[13] == 2,
	      "PUBLIC_TRAP_SUFFIX");
	for (i = 0; i < 6; ++i)
		CHECK(word(p->main_le + i * 4) == ring.words[i], "PUBLIC_MAIN_IB");
	for (i = 0; i < 8; ++i)
		CHECK(word(p->main_le + (6 + i) * 4) == fence.words[i], "PUBLIC_MAIN_FENCE");
	CHECK(word(p->main_le + 56) == 0x3bff && !word(p->main_le + 60), "MAIN_NOP_OMITS_TRAP");
	CHECK(!memcmp(p->bitstream_le, fixture + 591, 2314), "IDR_COPY_EXACT");
	for (i = 2314; i < 4096; ++i)
		CHECK(!p->bitstream_le[i], "BITSTREAM_ZERO_PADDING");
	for (i = 0; i < 4096; i += 4)
		CHECK(word(p->fence_page_le + i) ==
		      (i == 0x80 ? in->fence_initial : 0xa5deadc3), "FENCE_INIT_GUARDS");
	CHECK(p->layout.main_va == in->main_va && p->layout.work_va == in->work_va &&
	      p->layout.work_end == in->work_va + in->work_bytes &&
	      p->layout.vcpu_segment_base == in->vcpu_segment_base, "LAYOUT_BASES");
	CHECK(p->layout.ib_va == in->work_va + 0x80 && p->layout.create_va == in->work_va + 0x1000 &&
	      p->layout.message_va == in->work_va + 0x3000 &&
	      p->layout.feedback_va == in->work_va + 0x4000 &&
	      p->layout.bitstream_va == in->work_va + 0x5000 &&
	      p->layout.target_va == in->work_va + 0x6000 &&
	      p->layout.dpb_va == in->work_va + 0xc000 &&
	      p->layout.fence_page_va == in->work_va + 0x2000 &&
	      p->layout.fence_target_va == in->work_va + 0x2080, "LAYOUT_ADDRESSES");
	CHECK(p->layout.main_bytes == 4096 && p->layout.work_bytes == in->work_bytes &&
	      p->layout.main_dw == 16 && p->layout.ib_dw == 32 &&
	      p->layout.feedback_bytes == 2048 && p->layout.bitstream_bytes == 2432 &&
	      p->layout.bitstream_payload_bytes == 2314 &&
	      p->layout.target_bytes == 24576 && p->layout.pitch == 256 &&
	      p->layout.uv_offset == 16384 && p->layout.dpb_bytes == 157184 &&
	      p->layout.dpb_mapped_bytes == 159744, "LAYOUT_SIZES");
	CHECK(p->layout.handle == in->handle && p->layout.fence_initial == in->fence_initial &&
	      p->layout.fence_sequence == in->fence_sequence, "LAYOUT_FENCE_HANDLE");
	CHECK(p->create_params.width == 64 && p->create_params.height == 64 &&
	      !p->create_params.dpb_size, "MATCHING_CREATE_PARAMS");
	CHECK(ps4_uvd_msg_create_avc(create, sizeof(create), in->handle, &p->create_params) == 0,
	      "EXISTING_CREATE_RETURN");
	expected_create.size = sizeof(expected_create);
	expected_create.msg_type = RUVD_MSG_CREATE;
	expected_create.stream_handle = in->handle;
	expected_create.body.create.width_in_samples = 64;
	expected_create.body.create.height_in_samples = 64;
	CHECK(!memcmp(create, &expected_create, sizeof(expected_create)), "EXISTING_CREATE_BYTES");
	for (i = sizeof(expected_create); i < sizeof(create); ++i)
		CHECK(!create[i], "CREATE_ZERO_TAIL");
	CHECK(actual.before == 0x0123456789abcdefULL && actual.after == 0xfedcba9876543210ULL,
	      "SURROUNDING_GUARDS");
}

static void success(struct ps4_uvd_decode_input in)
{
	actual.before = 0x0123456789abcdefULL;
	actual.after = 0xfedcba9876543210ULL;
	memset(&actual.plan, 0xa5, sizeof(actual.plan));
	CHECK(ps4_uvd_decode_reference_build(&actual.plan, sizeof(actual.plan), &in) == 0,
	      "VALID_BUILD");
	check_plan(&in);
	++cases;
}

static void reject(struct ps4_uvd_decode_input in, int result, const char *witness)
{
	memset(&actual, 0x5a, sizeof(actual));
	memcpy(&untouched, &actual.plan, sizeof(untouched));
	CHECK(ps4_uvd_decode_reference_build(&actual.plan, sizeof(actual.plan), &in) == result,
	      witness);
	CHECK(!memcmp(&untouched, &actual.plan, sizeof(untouched)), "REJECTION_UNCHANGED");
	CHECK(actual.before == 0x5a5a5a5a5a5a5a5aULL && actual.after == actual.before,
	      "REJECTION_GUARDS");
	++cases;
}

static void rejection_cases(void)
{
	struct ps4_uvd_decode_input in;
#define REJECT(change, err, name) do { in = valid_input(); change; reject(in, err, name); } while (0)
	REJECT(in.handle = 0, -EINVAL, "ZERO_HANDLE_REJECT");
	REJECT(in.main_bytes = 4095, -EINVAL, "MAIN_SIZE_REJECT");
	REJECT(in.work_bytes = 0x20000, -EINVAL, "WORK_SIZE_REJECT");
	REJECT(in.work_bytes = 0x32fff, -EINVAL, "WORK_SIZE_ALIGN_REJECT");
	REJECT(in.work_bytes = 0x32000, -EINVAL, "WORK_MIN_REJECT");
	REJECT(in.main_va++, -EINVAL, "MAIN_ALIGN_REJECT");
	REJECT(in.work_va++, -EINVAL, "WORK_ALIGN_REJECT");
	REJECT(in.vcpu_segment_base++, -EINVAL, "SEGMENT_ALIGN_REJECT");
	REJECT(in.fence_initial = in.fence_sequence, -EINVAL, "STALE_FENCE_REJECT");
	REJECT(in.fixture = NULL, -EINVAL, "NULL_FIXTURE_REJECT");
	REJECT(in.fixture_bytes--, -EINVAL, "SHORT_FIXTURE_REJECT");
	REJECT(in.fixture_bytes++, -EINVAL, "LONG_FIXTURE_REJECT");
	REJECT(in.verified_fixture_sha256[0] ^= 1, -EBADMSG, "FIXTURE_DIGEST_REJECT");
	REJECT(in.main_va = in.work_va, -EINVAL, "GPU_EQUAL_REJECT");
	REJECT(in.main_va = in.work_va + 0x3f000, -EINVAL, "WHOLE_WORK_OVERLAP_REJECT");
	REJECT(in.main_va = in.work_va - 0x1000; in.main_bytes = 8192, -EINVAL, "MAIN_EXACT_SIZE_REJECT");
	REJECT(in.main_va = in.vcpu_segment_base - 4096, -ERANGE, "MAIN_BEFORE_SEGMENT");
	REJECT(in.work_va = in.vcpu_segment_base - 4096, -ERANGE, "WORK_BEFORE_SEGMENT");
	REJECT(in.work_va = in.vcpu_segment_base + 0x10000000 - 4096, -ERANGE, "WORK_CROSSES_SEGMENT");
	REJECT(in.main_va = in.vcpu_segment_base + 0x10000000, -ERANGE, "MAIN_AFTER_SEGMENT");
	REJECT(in.work_va = UINT64_MAX - 4095, -ERANGE, "WORK_ADDRESS_OVERFLOW");
	REJECT(in.main_va = UINT64_MAX - 4095, -ERANGE, "MAIN_ADDRESS_OVERFLOW");
	REJECT(in.work_bytes = SIZE_MAX & ~(size_t)4095, -ERANGE, "WORK_SIZE_OVERFLOW");
	REJECT(in.vcpu_segment_base = UINT64_MAX - 0xfffffff, -ERANGE, "SEGMENT_OVERFLOW");
	REJECT(in.vcpu_segment_base = 1ULL << 40;
	       in.main_va = in.vcpu_segment_base + 4096;
	       in.work_va = in.vcpu_segment_base + 0x4000, -ERANGE, "FENCE_40BIT_REJECT");
	REJECT(in.fixture = (u8 *)&actual.plan, -EINVAL, "CPU_FIXTURE_OVERLAP_REJECT");
	REJECT(in.fixture = (u8 *)(uintptr_t)(UINTPTR_MAX - 100), -ERANGE, "CPU_FIXTURE_OVERFLOW");
	memcpy(broken_fixture, fixture, sizeof(fixture));
	broken_fixture[591] = 1;
	REJECT(in.fixture = broken_fixture, -EBADMSG, "IDR_PREFIX_REJECT");
	memcpy(broken_fixture, fixture, sizeof(fixture)); broken_fixture[5] = 100;
	REJECT(in.fixture = broken_fixture, -EBADMSG, "SPS_PREFIX_REJECT");
	memcpy(broken_fixture, fixture, sizeof(fixture)); broken_fixture[32] = 0;
	REJECT(in.fixture = broken_fixture, -EBADMSG, "PPS_PREFIX_REJECT");
	memcpy(broken_fixture, fixture, sizeof(fixture)); broken_fixture[40] = 0;
	REJECT(in.fixture = broken_fixture, -EBADMSG, "SEI_PREFIX_REJECT");
#undef REJECT
}

static void output_cases(void)
{
	struct ps4_uvd_decode_input in = valid_input();
	u8 page[4098], expected[6144];
	uint32_t ib[64];

	memset(&actual, 0x5a, sizeof(actual));
	memcpy(&untouched, &actual.plan, sizeof(untouched));
	CHECK(ps4_uvd_decode_reference_build(NULL, sizeof(actual.plan), &in) == -EINVAL, "NULL_OUT");
	CHECK(ps4_uvd_decode_reference_build(&actual.plan, sizeof(actual.plan), NULL) == -EINVAL, "NULL_INPUT");
	CHECK(ps4_uvd_decode_reference_build(&actual.plan, sizeof(actual.plan) - 1, &in) == -EINVAL, "SHORT_OUT");
	CHECK(ps4_uvd_decode_reference_build(&actual.plan, sizeof(actual.plan) + 1, &in) == -EINVAL, "LONG_OUT");
	CHECK(ps4_uvd_decode_reference_build((void *)((u8 *)&actual.plan + 1), sizeof(actual.plan), &in) == -EINVAL,
	      "UNALIGNED_OUT");
	CHECK(ps4_uvd_decode_reference_build(&actual.plan, sizeof(actual.plan),
	      (void *)((u8 *)&in + 1)) == -EINVAL, "UNALIGNED_INPUT");
	CHECK(ps4_uvd_decode_reference_build(&actual.plan, sizeof(actual.plan),
	      (void *)&actual.plan) == -EINVAL, "CPU_INPUT_OVERLAP_REJECT");
	CHECK(ps4_uvd_decode_reference_build((void *)(uintptr_t)(UINTPTR_MAX & ~(uintptr_t)7),
	      sizeof(actual.plan), &in) == -ERANGE, "CPU_OUT_OVERFLOW");
	CHECK(!memcmp(&untouched, &actual.plan, sizeof(untouched)), "OUTPUT_ERRORS_UNCHANGED");
	++cases;
	memset(page, 0xa5, sizeof(page));
	CHECK(ps4_uvd_decode_msg_reference(page + 1, 4096, in.handle) == 0, "UNALIGNED_MESSAGE");
	reference_decode(&in, expected, ib);
	CHECK(!memcmp(page + 1, expected, 4096) && page[0] == 0xa5 && page[4097] == 0xa5,
	      "STANDALONE_MESSAGE_GUARDS");
	++cases;
	memset(page, 0x5a, sizeof(page));
	CHECK(ps4_uvd_decode_msg_reference(NULL, 4096, 1) == -EINVAL, "MESSAGE_NULL");
	CHECK(ps4_uvd_decode_msg_reference(page + 1, 4095, 1) == -EINVAL, "MESSAGE_SIZE");
	CHECK(ps4_uvd_decode_msg_reference(page + 1, 4096, 0) == -EINVAL, "MESSAGE_HANDLE");
	CHECK(ps4_uvd_decode_msg_reference((void *)(uintptr_t)(UINTPTR_MAX - 100), 4096, 1) == -ERANGE,
	      "MESSAGE_CPU_OVERFLOW");
	for (unsigned i = 0; i < sizeof(page); ++i)
		CHECK(page[i] == 0x5a, "MESSAGE_ERRORS_UNCHANGED");
	++cases;
}

int main(int argc, char **argv)
{
	struct ps4_uvd_decode_input in;
	FILE *file;
	uint32_t one = 1;

	CHECK(argc == 2, "FIXTURE_ARGUMENT");
	CHECK(*(u8 *)&one == 1, "REFERENCE_HOST_LITTLE_ENDIAN");
	file = fopen(argv[1], "rb"); CHECK(file, "FIXTURE_OPEN");
	CHECK(fread(fixture, 1, sizeof(fixture), file) == sizeof(fixture), "FIXTURE_READ");
	CHECK(fgetc(file) == EOF && !fclose(file), "FIXTURE_EXACT_LENGTH");
	CHECK(!memcmp(ps4_uvd_decode_fixture_sha256, expected_fixture_sha256, 32), "EXPORTED_FIXTURE_DIGEST");
	CHECK(!memcmp(ps4_uvd_decode_expected_nv12_sha256, expected_nv12_sha256, 32), "EXPORTED_OUTPUT_DIGEST");
	success(valid_input());
	memcpy(&repeat, &actual.plan, sizeof(repeat));
	success(valid_input());
	CHECK(!memcmp(&repeat, &actual.plan, sizeof(repeat)), "DETERMINISM_INCLUDING_PADDING");
	in = valid_input(); in.work_bytes = 0x33000; success(in);
	in = valid_input(); in.work_bytes = 0x41000; success(in);
	in = valid_input(); in.work_va = in.main_va + 4096; success(in);
	in = valid_input(); in.main_va = in.work_va + in.work_bytes; success(in);
	in = valid_input(); in.work_va = in.vcpu_segment_base + 0x10000000 - in.work_bytes; success(in);
	in = valid_input(); in.handle = UINT32_MAX; in.fence_sequence = 0; success(in);
	in = valid_input(); in.fence_sequence = UINT32_MAX; success(in);
	in = valid_input(); in.vcpu_segment_base = (1ULL << 40) - 0x10000000;
	in.main_va = in.vcpu_segment_base + 4096; in.work_va = in.vcpu_segment_base + 0x4000; success(in);
	rejection_cases();
	output_cases();
	printf("PASS: %u fixed-reference actual-source cases\n", cases);
	return 0;
}
