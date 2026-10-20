// SPDX-License-Identifier: MIT
/*
 * Copyright 2011 Advanced Micro Devices, Inc.
 * All Rights Reserved.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the
 * "Software"), to deal in the Software without restriction, including
 * without limitation the rights to use, copy, modify, merge, publish,
 * distribute, sub license, and/or sell copies of the Software, and to
 * permit persons to whom the Software is furnished to do so, subject to
 * the following conditions:
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NON-INFRINGEMENT. IN NO EVENT SHALL
 * THE COPYRIGHT HOLDERS, AUTHORS AND/OR ITS SUPPLIERS BE LIABLE FOR ANY CLAIM,
 * DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
 * OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
 * USE OR OTHER DEALINGS IN THE SOFTWARE.
 *
 * The above copyright notice and this permission notice (including the
 * next paragraph) shall be included in all copies or substantial portions
 * of the Software.
 *
 * Adapted public message fields: Linux a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea,
 * amdgpu_uvd.c amdgpu_uvd_get_create_msg/amdgpu_uvd_get_destroy_msg
 * (original author Christian König <deathsimple@vodafone.de>).
 * Mesa 26.1.7 ac_uvd_dec.h/c corroborates
 * the common/CREATE offsets and legacy AVC stream type 0. See README for pins.
 *
 * This implementation serializes bytes only. It neither submits commands nor
 * establishes that PS4 firmware implements the public ABI.
 */
#include <linux/errno.h>
#include <linux/string.h>

#include "ps4_uvd_message.h"

/* Byte stores make host endianness and output alignment irrelevant. */
static void ps4_uvd_msg_put_le32(u8 *out, u32 value)
{
	out[0] = (u8)value;
	out[1] = (u8)(value >> 8);
	out[2] = (u8)(value >> 16);
	out[3] = (u8)(value >> 24);
}

static void ps4_uvd_msg_common(u8 *page, u32 type, u32 handle)
{
	memset(page, 0, PS4_UVD_MSG_PAGE_BYTES);
	ps4_uvd_msg_put_le32(page + 0, PS4_UVD_MSG_DECLARED_BYTES);
	ps4_uvd_msg_put_le32(page + 4, type);
	ps4_uvd_msg_put_le32(page + 8, handle);
	/* Feedback number at 12, and all other unused fields, remain zero. */
}

int ps4_uvd_msg_create_avc(u8 *page, size_t bytes, u32 handle,
			 const struct ps4_uvd_avc_create *params)
{
	struct ps4_uvd_avc_create values;

	if (!page || bytes != PS4_UVD_MSG_PAGE_BYTES || !handle || !params)
		return -EINVAL;
	values = *params;
	if (!values.width || !values.height)
		return -EINVAL;
	ps4_uvd_msg_common(page, 0, handle);
	/* stream_type 0: legacy AVC, with flags/ASIC/DPB address/model/version 0. */
	ps4_uvd_msg_put_le32(page + 28, values.width);
	ps4_uvd_msg_put_le32(page + 32, values.height);
	ps4_uvd_msg_put_le32(page + 40, values.dpb_size);
	return 0;
}

int ps4_uvd_msg_create_linux_dummy(u8 *page, size_t bytes, u32 handle)
{
	const struct ps4_uvd_avc_create params = {
		.width = 0x00000780,
		.height = 0x00000440,
		.dpb_size = 0x01b37000,
	};

	return ps4_uvd_msg_create_avc(page, bytes, handle, &params);
}

int ps4_uvd_msg_destroy(u8 *page, size_t bytes, u32 handle)
{
	if (!page || bytes != PS4_UVD_MSG_PAGE_BYTES || !handle)
		return -EINVAL;
	ps4_uvd_msg_common(page, 2, handle);
	return 0;
}
