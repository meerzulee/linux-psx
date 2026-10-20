// SPDX-License-Identifier: MIT
/*
 * Copyright 2011, 2014, 2025 Advanced Micro Devices, Inc.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the "Software"),
 * to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense,
 * and/or sell copies of the Software, and to permit persons to whom the
 * Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
 * THE COPYRIGHT HOLDER(S) OR AUTHOR(S) BE LIABLE FOR ANY CLAIM, DAMAGES OR
 * OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
 * ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
 * OTHER DEALINGS IN THE SOFTWARE.
 *
 * Adapted from Mesa 26.1.7 ac_uvd_dec.h/c legacy AVC message construction
 * and command emission; Linux a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea
 * uvd_v5_0_ring_emit_ib/fence (original author Christian Koenig).
 * Complete upstream notices and precise inputs are in UPSTREAM-NOTICES.
 * Fixed picture data belongs to the locally generated 64x64 one-IDR fixture.
 * This serializes bytes; it does not operate a device or establish its ABI.
 */
#include <linux/errno.h>
#include <linux/string.h>
#include "ps4_uvd_decode_message.h"

const u8 ps4_uvd_decode_fixture_sha256[PS4_UVD_DEC_SHA256_BYTES] = {
	0x7d, 0xc7, 0x3a, 0x00, 0x61, 0x42, 0x26, 0xee,
	0x4e, 0x7d, 0xc2, 0x22, 0x10, 0x9f, 0x6f, 0xa7,
	0xaf, 0x17, 0xa6, 0xe0, 0x34, 0x96, 0xc0, 0xfa,
	0xc3, 0xff, 0xb2, 0x00, 0xee, 0x45, 0xcb, 0x62,
};

const u8 ps4_uvd_decode_expected_nv12_sha256[PS4_UVD_DEC_SHA256_BYTES] = {
	0x12, 0x40, 0x41, 0x91, 0xab, 0x94, 0x5b, 0x74,
	0x78, 0x09, 0x3a, 0x85, 0x25, 0x45, 0xad, 0xfb,
	0xaa, 0x9a, 0xf4, 0x4b, 0xd3, 0x2f, 0x83, 0xcc,
	0xf2, 0x4f, 0x48, 0x9e, 0x38, 0x56, 0x28, 0xdf,
};

enum {
	DEC_CMD = 0x3bc3, DEC_DATA0 = 0x3bc4, DEC_DATA1 = 0x3bc5,
	DEC_ENGINE = 0x3bc6, DEC_NOP = 0x3bff, DEC_CONTEXT = 0x3dbd,
	DEC_IB_LOW = 0x3c67, DEC_IB_HIGH = 0x3c66, DEC_IB_SIZE = 0x3da2,
};

static void dec_le32(u8 *bytes, u32 value)
{
	bytes[0] = (u8)value;
	bytes[1] = (u8)(value >> 8);
	bytes[2] = (u8)(value >> 16);
	bytes[3] = (u8)(value >> 24);
}

static bool dec_end(u64 base, u64 bytes, u64 *end)
{
	if (base > ~(u64)0 - bytes)
		return false;
	*end = base + bytes;
	return true;
}

static bool dec_cpu_end(const void *pointer, size_t bytes, unsigned long *end)
{
	unsigned long base = (unsigned long)pointer;

	if (bytes > ~0UL - base)
		return false;
	*end = base + bytes;
	return true;
}

static bool dec_overlap(u64 first, u64 first_end, u64 second, u64 second_end)
{
	return first < second_end && second < first_end;
}

static void dec_msg_fill(u8 *page, u32 handle)
{
	memset(page, 0, PS4_UVD_DEC_PAGE_BYTES);
	dec_le32(page + 0, PS4_UVD_MSG_DECLARED_BYTES);
	dec_le32(page + 4, 1);
	dec_le32(page + 8, handle);
	/* All omitted scalar/address/model/tiling/reserved fields stay zero. */
	dec_le32(page + 20, 1);
	dec_le32(page + 24, PS4_UVD_DEC_WIDTH);
	dec_le32(page + 28, PS4_UVD_DEC_HEIGHT);
	dec_le32(page + 36, PS4_UVD_DEC_DPB_BYTES);
	dec_le32(page + 52, PS4_UVD_DEC_WIDTH);
	dec_le32(page + 88, PS4_UVD_DEC_BSD_BYTES);
	dec_le32(page + 112, PS4_UVD_DEC_PITCH);
	dec_le32(page + 136, PS4_UVD_DEC_UV_OFFSET);
	/* codec.h264 begins at byte224. Profile0 is baseline, level_idc10. */
	dec_le32(page + 228, 10);
	dec_le32(page + 232, 5);
	dec_le32(page + 236, 8);
	page[240] = 1;
	page[244] = 2;
	page[248] = 0xf8;
	memset(page + 260, 16, 96);
	memset(page + 356, 16, 128);
	page[3296] = 1;
}

int ps4_uvd_decode_msg_reference(u8 *page, size_t bytes, u32 handle)
{
	unsigned long end;

	if (!page || bytes != PS4_UVD_DEC_PAGE_BYTES || !handle)
		return -EINVAL;
	if (!dec_cpu_end(page, bytes, &end))
		return -ERANGE;
	dec_msg_fill(page, handle);
	return 0;
}

static void dec_pair(u8 *bytes, unsigned int dw, u32 reg, u32 value)
{
	dec_le32(bytes + dw * 4, reg & 0xffffU);
	dec_le32(bytes + (dw + 1) * 4, value);
}

static void dec_command(u8 *ib, unsigned int dw, u32 command, u64 va)
{
	dec_pair(ib, dw, DEC_DATA0, (u32)va);
	dec_pair(ib, dw + 2, DEC_DATA1, (u32)(va >> 32));
	dec_pair(ib, dw + 4, DEC_CMD, command << 1);
}

int ps4_uvd_decode_reference_build(struct ps4_uvd_decode_plan *out,
				 size_t out_bytes,
				 const struct ps4_uvd_decode_input *input)
{
	struct ps4_uvd_decode_input in;
	struct ps4_uvd_decode_layout *l;
	u64 main_end, work_end, segment_end, fence_va;
	unsigned long out_end, input_end, fixture_end;
	unsigned int offset;
	static const u8 sps_prefix[] = { 0, 0, 0, 1, 0x67, 66, 0xc0, 10 };
	static const u8 pps_prefix[] = { 0, 0, 0, 1, 0x68 };
	static const u8 sei_prefix[] = { 0, 0, 1, 6 };
	static const u8 idr_prefix[] = { 0, 0, 1, 0x65 };

	if (!out || !input || out_bytes != sizeof(*out) ||
	    ((unsigned long)out & (__alignof__(*out) - 1)) ||
	    ((unsigned long)input & (__alignof__(*input) - 1)))
		return -EINVAL;
	if (!dec_cpu_end(out, sizeof(*out), &out_end) ||
	    !dec_cpu_end(input, sizeof(*input), &input_end))
		return -ERANGE;
	if (dec_overlap((unsigned long)out, out_end,
			(unsigned long)input, input_end))
		return -EINVAL;
	in = *input;
	if (!in.fixture || in.fixture_bytes != PS4_UVD_DEC_FIXTURE_BYTES ||
	    !in.handle || in.fence_initial == in.fence_sequence ||
	    in.main_bytes != PS4_UVD_DEC_PAGE_BYTES ||
	    in.work_bytes < PS4_UVD_DEC_WORK_MIN_BYTES ||
	    (in.main_va & (PS4_UVD_DEC_PAGE_BYTES - 1)) ||
	    (in.work_va & (PS4_UVD_DEC_PAGE_BYTES - 1)) ||
	    (in.work_bytes & (PS4_UVD_DEC_PAGE_BYTES - 1)) ||
	    (in.vcpu_segment_base & (PS4_UVD_DEC_SEGMENT_BYTES - 1)))
		return -EINVAL;
	if (!dec_cpu_end(in.fixture, in.fixture_bytes, &fixture_end))
		return -ERANGE;
	if (dec_overlap((unsigned long)out, out_end,
			(unsigned long)in.fixture, fixture_end))
		return -EINVAL;
	if (!dec_end(in.main_va, in.main_bytes, &main_end) ||
	    !dec_end(in.work_va, in.work_bytes, &work_end) ||
	    !dec_end(in.vcpu_segment_base, PS4_UVD_DEC_SEGMENT_BYTES, &segment_end))
		return -ERANGE;
	if (dec_overlap(in.main_va, main_end, in.work_va, work_end))
		return -EINVAL;
	if (in.main_va < in.vcpu_segment_base || main_end > segment_end ||
	    in.work_va < in.vcpu_segment_base || work_end > segment_end)
		return -ERANGE;
	/* Every fixed offset/range is inside WORK_MIN_BYTES, checked above. */
	fence_va = in.work_va + PS4_UVD_DEC_FENCE_OFFSET + PS4_UVD_DEC_FENCE_CELL_OFFSET;
	if (fence_va > (1ULL << 40) - 4)
		return -ERANGE;
	if (memcmp(in.verified_fixture_sha256, ps4_uvd_decode_fixture_sha256,
		   PS4_UVD_DEC_SHA256_BYTES) ||
	    memcmp(in.fixture, sps_prefix, sizeof(sps_prefix)) ||
	    memcmp(in.fixture + 28, pps_prefix, sizeof(pps_prefix)) ||
	    memcmp(in.fixture + 37, sei_prefix, sizeof(sei_prefix)) ||
	    memcmp(in.fixture + PS4_UVD_DEC_IDR_OFFSET, idr_prefix, sizeof(idr_prefix)))
		return -EBADMSG;

	/* Validation ends here: no fallible operation or output read follows. */
	memset(out, 0, sizeof(*out));
	l = &out->layout;
	l->vcpu_segment_base = in.vcpu_segment_base;
	l->main_va = in.main_va;
	l->work_va = in.work_va;
	l->work_end = work_end;
	l->ib_va = in.work_va + PS4_UVD_DEC_IB_OFFSET;
	l->create_va = in.work_va + PS4_UVD_DEC_CREATE_OFFSET;
	l->fence_page_va = in.work_va + PS4_UVD_DEC_FENCE_OFFSET;
	l->fence_target_va = fence_va;
	l->message_va = in.work_va + PS4_UVD_DEC_MESSAGE_OFFSET;
	l->feedback_va = in.work_va + PS4_UVD_DEC_FEEDBACK_OFFSET;
	l->bitstream_va = in.work_va + PS4_UVD_DEC_BITSTREAM_OFFSET;
	l->target_va = in.work_va + PS4_UVD_DEC_TARGET_OFFSET;
	l->dpb_va = in.work_va + PS4_UVD_DEC_DPB_OFFSET;
	l->main_bytes = in.main_bytes;
	l->work_bytes = in.work_bytes;
	l->main_dw = PS4_UVD_DEC_MAIN_DW;
	l->ib_dw = PS4_UVD_DEC_IB_DW;
	l->feedback_bytes = PS4_UVD_DEC_FEEDBACK_BYTES;
	l->bitstream_bytes = PS4_UVD_DEC_BSD_BYTES;
	l->bitstream_payload_bytes = PS4_UVD_DEC_IDR_BYTES;
	l->target_bytes = PS4_UVD_DEC_TARGET_BYTES;
	l->pitch = PS4_UVD_DEC_PITCH;
	l->uv_offset = PS4_UVD_DEC_UV_OFFSET;
	l->dpb_bytes = PS4_UVD_DEC_DPB_BYTES;
	l->dpb_mapped_bytes = PS4_UVD_DEC_DPB_MAPPED_BYTES;
	l->handle = in.handle;
	l->fence_initial = in.fence_initial;
	l->fence_sequence = in.fence_sequence;
	out->create_params.width = PS4_UVD_DEC_WIDTH;
	out->create_params.height = PS4_UVD_DEC_HEIGHT;
	out->create_params.dpb_size = 0;
	dec_msg_fill(out->message_le, in.handle);
	dec_le32(out->feedback_le, PS4_UVD_DEC_FEEDBACK_BYTES);
	memcpy(out->bitstream_le, in.fixture + PS4_UVD_DEC_IDR_OFFSET,
	       PS4_UVD_DEC_IDR_BYTES);
	for (offset = 0; offset < PS4_UVD_DEC_PAGE_BYTES; offset += 4)
		dec_le32(out->fence_page_le + offset, PS4_UVD_DEC_FENCE_GUARD);
	dec_le32(out->fence_page_le + PS4_UVD_DEC_FENCE_CELL_OFFSET, in.fence_initial);

	dec_command(out->ib_le, 0, 0, l->message_va);
	dec_command(out->ib_le, 6, 1, l->dpb_va);
	dec_command(out->ib_le, 12, 3, l->feedback_va);
	dec_command(out->ib_le, 18, 0x100, l->bitstream_va);
	dec_command(out->ib_le, 24, 2, l->target_va);
	dec_pair(out->ib_le, 30, DEC_ENGINE, 1);
	dec_pair(out->main_le, 0, DEC_IB_LOW, (u32)l->ib_va);
	dec_pair(out->main_le, 2, DEC_IB_HIGH, (u32)(l->ib_va >> 32));
	dec_pair(out->main_le, 4, DEC_IB_SIZE, PS4_UVD_DEC_IB_DW);
	dec_pair(out->main_le, 6, DEC_CONTEXT, in.fence_sequence);
	dec_pair(out->main_le, 8, DEC_DATA0, (u32)fence_va);
	dec_pair(out->main_le, 10, DEC_DATA1, (u32)(fence_va >> 32) & 0xffU);
	dec_pair(out->main_le, 12, DEC_CMD, 0);
	dec_pair(out->main_le, 14, DEC_NOP, 0);
	return 0;
}
