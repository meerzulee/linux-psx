// SPDX-License-Identifier: MIT
/*
 * Copyright 2011, 2014 Advanced Micro Devices, Inc.
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
 * Public packet adaptation: Linux a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea,
 * amdgpu_uvd_send_msg and uvd_v5_0_ring_emit_ib/fence/insert_nop.
 * Original author: Christian König. Original notices are also preserved in
 * UPSTREAM-NOTICES. This is not the full upstream fence or ring self-test:
 * the six-DWORD DATA0=0/DATA1=0/CMD=2 trap suffix is deliberately omitted.
 *
 * No device operation, mapped-buffer write or publication takes place here.
 */
#include <linux/errno.h>
#include <linux/string.h>
#include "ps4_uvd_message_job.h"

/* Public UVD5 DWORD register indices, not MMIO accesses. */
enum {
	JOB_CMD = 0x3bc3, JOB_DATA0 = 0x3bc4, JOB_DATA1 = 0x3bc5,
	JOB_NOP = 0x3bff, JOB_IB_LOW = 0x3c67, JOB_IB_HIGH = 0x3c66,
	JOB_IB_SIZE = 0x3da2, JOB_CONTEXT = 0x3dbd,
};

static bool job_end(u64 base, u64 bytes, u64 *end)
{
	if (base > ~(u64)0 - bytes)
		return false;
	*end = base + bytes;
	return true;
}

static void job_le32(u8 *bytes, u32 value)
{
	bytes[0] = (u8)value;
	bytes[1] = (u8)(value >> 8);
	bytes[2] = (u8)(value >> 16);
	bytes[3] = (u8)(value >> 24);
}

static void job_pair(u8 *bytes, unsigned int dw, u32 reg, u32 value)
{
	/* Public PACKET0(reg, 0): type/count are zero, register is low 16 bits. */
	job_le32(bytes + dw * 4, reg & 0xffffU);
	job_le32(bytes + (dw + 1) * 4, value);
}

int ps4_uvd_message_job_build(struct ps4_uvd_message_job_plan *out,
			     const struct ps4_uvd_message_job_input *input)
{
	struct ps4_uvd_message_job_input in;
	u64 main_end, backing_end, segment_end, create_va, fence_page, fence_va;
	unsigned int dw;
	int r;

	if (!out || !input)
		return -EINVAL;
	in = *input;
	if (in.main_bytes != PS4_UVD_JOB_MAIN_BYTES ||
	    in.backing_bytes != PS4_UVD_JOB_BACKING_BYTES ||
	    (in.main_va & (PS4_UVD_JOB_MAIN_BYTES - 1)) ||
	    (in.backing_va & (PS4_UVD_MSG_PAGE_BYTES - 1)) ||
	    (in.vcpu_segment_base & (PS4_UVD_JOB_SEGMENT_BYTES - 1)))
		return -EINVAL;
	if (!job_end(in.main_va, in.main_bytes, &main_end) ||
	    !job_end(in.backing_va, in.backing_bytes, &backing_end) ||
	    !job_end(in.vcpu_segment_base, PS4_UVD_JOB_SEGMENT_BYTES, &segment_end))
		return -ERANGE;
	if (in.main_va < backing_end && in.backing_va < main_end)
		return -EINVAL;
	if (in.main_va < in.vcpu_segment_base || main_end > segment_end ||
	    in.backing_va < in.vcpu_segment_base || backing_end > segment_end)
		return -ERANGE;
	/* Offsets fit the already checked complete 128KiB backing interval. */
	create_va = in.backing_va + PS4_UVD_JOB_CREATE_OFFSET;
	fence_page = in.backing_va + PS4_UVD_JOB_FENCE_OFFSET;
	fence_va = fence_page + PS4_UVD_JOB_FENCE_TARGET_OFFSET;
	if (fence_va > (1ULL << 40) - 4)
		return -ERANGE;

	/* The pinned serializer leaves its output unchanged on rejection. It is
	 * the last fallible call; no packet/layout write precedes its success.
	 */
	r = ps4_uvd_msg_create_linux_dummy(out->create_le, PS4_UVD_MSG_PAGE_BYTES,
					 in.handle);
	if (r)
		return r;
	memset(&out->layout, 0, sizeof(out->layout));
	out->layout.vcpu_segment_base = in.vcpu_segment_base;
	out->layout.main_va = in.main_va;
	out->layout.backing_va = in.backing_va;
	out->layout.ib_va = in.backing_va + PS4_UVD_JOB_IB_OFFSET;
	out->layout.create_va = create_va;
	out->layout.fence_page_va = fence_page;
	out->layout.fence_target_va = fence_va;
	out->layout.main_page_bytes = PS4_UVD_JOB_MAIN_BYTES;
	out->layout.backing_bytes = PS4_UVD_JOB_BACKING_BYTES;
	out->layout.main_dw = PS4_UVD_JOB_MAIN_DW;
	out->layout.ib_dw = PS4_UVD_JOB_IB_DW;
	out->layout.create_page_bytes = PS4_UVD_MSG_PAGE_BYTES;
	out->layout.create_declared_bytes = PS4_UVD_MSG_DECLARED_BYTES;
	out->layout.fence_page_bytes = PS4_UVD_MSG_PAGE_BYTES;
	out->layout.fence_target_offset = PS4_UVD_JOB_FENCE_TARGET_OFFSET;
	out->layout.fence_store_bytes = 4;
	out->layout.sequence = in.sequence;

	job_pair(out->ib_le, 0, JOB_DATA0, (u32)create_va);
	job_pair(out->ib_le, 2, JOB_DATA1, (u32)(create_va >> 32));
	job_pair(out->ib_le, 4, JOB_CMD, 0);
	for (dw = 6; dw < PS4_UVD_JOB_IB_DW; dw += 2)
		job_pair(out->ib_le, dw, JOB_NOP, 0);

	job_pair(out->main_le, 0, JOB_IB_LOW, (u32)in.backing_va);
	job_pair(out->main_le, 2, JOB_IB_HIGH, (u32)(in.backing_va >> 32));
	job_pair(out->main_le, 4, JOB_IB_SIZE, PS4_UVD_JOB_IB_DW);
	job_pair(out->main_le, 6, JOB_CONTEXT, in.sequence);
	job_pair(out->main_le, 8, JOB_DATA0, (u32)fence_va);
	job_pair(out->main_le, 10, JOB_DATA1, (u32)(fence_va >> 32) & 0xffU);
	job_pair(out->main_le, 12, JOB_CMD, 0);
	job_pair(out->main_le, 14, JOB_NOP, 0);
	return 0;
}
