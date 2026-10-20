/* SPDX-License-Identifier: MIT */
#ifndef PS4_UVD_MESSAGE_H
#define PS4_UVD_MESSAGE_H

#include <linux/types.h>

#define PS4_UVD_MSG_PAGE_BYTES 4096U
#define PS4_UVD_MSG_DECLARED_BYTES 0xde4U

/*
 * Public legacy AVC CREATE fields, not a hardware capability declaration.
 * Width/height must be nonzero; all other u32 values are serialized literally.
 * dpb_size == 0 matches Mesa's dimensions-only CREATE policy. Nonzero sizes
 * are not validated as allocations, suitable DPBs, or firmware-supported.
 */
struct ps4_uvd_avc_create {
	u32 width;
	u32 height;
	u32 dpb_size;
};

/*
 * CPU-only output: exactly one writable 4096-byte buffer, no alignment need.
 * Reject NULL, wrong byte count, zero handle or zero dimensions with -EINVAL,
 * leaving output unchanged. CREATE also requires a readable params object,
 * which may overlap the output: its values are copied before clearing.
 * Success zeroes every unused byte, including bytes beyond declared length.
 * No address, lifetime, firmware, session or submission validation is implied.
 */
int ps4_uvd_msg_create_avc(u8 *page, size_t bytes, u32 handle,
			 const struct ps4_uvd_avc_create *params);
int ps4_uvd_msg_create_linux_dummy(u8 *page, size_t bytes, u32 handle);
int ps4_uvd_msg_destroy(u8 *page, size_t bytes, u32 handle);

#endif
