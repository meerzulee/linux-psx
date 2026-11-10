// SPDX-License-Identifier: BSD-2-Clause
// Copyright (c) 2026 Meerzulee and Linux psX contributors.
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#undef NULL
#undef offsetof
#define TESTING
#include "kernel.h"
#include "firmware.h"
#include "crc32.h"
#define FILE 0100644
struct ksym_t kern;
static u32 uvd_test_crc;
static unsigned entries;
static size_t last_size;
static char last_name[80];
static int quiet(const char *format, ...) { (void)format; return 0; }
void cpio_hdr(u8 **p, const char *name, u32 mode, size_t size)
{
    (void)p;
    assert(mode == FILE);
    entries++;
    last_size = size;
    snprintf(last_name, sizeof(last_name), "%s", name);
}
#include "uvd.inc"
int main(void)
{
    static const char path[] = "lib/firmware/amdgpu/liverpool_uvd.bin";
    size_t image = (size_t)UVD_KERNEL_OFFSET + UVD_RAW_SIZE;
    u8 *kernel = calloc(1, image), *range = kernel + UVD_KERNEL_OFFSET;
    u8 *out = malloc(FW_HEADER_SIZE + UVD_RAW_SIZE), *p = out;
    assert(kernel && out);
    assert(sizeof(struct firmware_header) == FW_HEADER_SIZE);
    for (size_t i = 0; i < UVD_RAW_SIZE; i++) range[i] = (u8)(i * 31 + 7);
    kern.kern_base = (uintptr_t)kernel;
    kern.printf = (void *)quiet;
    uvd_test_crc = crc32(0, range, UVD_RAW_SIZE);

    assert(add_uvd_firmware(&p, path) == 1);
    assert(entries == 1 && last_size == 314168 && !strcmp(last_name, path));
    assert(p == out + 314168);
    struct firmware_header *header = (struct firmware_header *)out;
    assert(header->size_bytes == 314168 && header->header_size_bytes == 32);
    assert(header->header_version_major == 1 && header->header_version_minor == 0);
    assert(header->ip_version_major == 4 && header->ip_version_minor == 2);
    assert(header->ucode_version == 0x012a6509 && header->ucode_size_bytes == UVD_RAW_SIZE);
    assert(header->ucode_array_offset_bytes == 256 && header->crc32 == uvd_test_crc);
    for (size_t i = 0; i < sizeof(header->raw); i++) assert(!header->raw[i]);
    assert(!memcmp(out + 256, range, UVD_RAW_SIZE));

    /* A single changed byte adds nothing and leaves the cursor alone. */
    range[5] ^= 1;
    entries = 0;
    p = out;
    assert(add_uvd_firmware(&p, path) == 0);
    assert(p == out && entries == 0);

    assert(FW_CPIO_SIZE >= FW_HEADER_SIZE * 9 + UVD_RAW_SIZE + CPIO_HEADER_SIZE * 17);
    free(kernel);
    free(out);
    puts("uvd firmware: exact header and bytes, changed range left out PASS");
    return 0;
}
