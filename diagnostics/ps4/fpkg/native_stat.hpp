// SPDX-License-Identifier: MIT
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <sys/stat.h>

namespace diagnostix {
// ABI facts, not SDK typedefs. FreeBSD 9 amd64 / Orbis uses 16-bit mode/nlink.
// https://github.com/freebsd/freebsd-src/blob/release/9.0.0/sys/sys/stat.h
// https://github.com/freebsd/freebsd-src/blob/release/9.0.0/sys/sys/_types.h
struct NativeTimespec { int64_t seconds, nanoseconds; };
struct alignas(8) NativeStat {
  uint32_t device, inode;
  uint16_t mode, links;
  uint32_t uid, gid, rdevice;
  NativeTimespec accessed, modified, changed;
  int64_t size, blocks;
  uint32_t block_size, flags, generation;
  int32_t spare;
  NativeTimespec born;
};
constexpr size_t kNativeStatBytes = 120, kNativeStatBufferBytes = 256;
static_assert(sizeof(NativeTimespec) == 16 && sizeof(NativeStat) == kNativeStatBytes, "native stat ABI size");
#define DIAGNOSTIX_STAT_OFFSET(field, offset) static_assert(offsetof(NativeStat, field) == offset, "native stat offset: " #field)
DIAGNOSTIX_STAT_OFFSET(device, 0); DIAGNOSTIX_STAT_OFFSET(inode, 4);
DIAGNOSTIX_STAT_OFFSET(mode, 8); DIAGNOSTIX_STAT_OFFSET(links, 10);
DIAGNOSTIX_STAT_OFFSET(uid, 12); DIAGNOSTIX_STAT_OFFSET(gid, 16); DIAGNOSTIX_STAT_OFFSET(rdevice, 20);
DIAGNOSTIX_STAT_OFFSET(accessed, 24); DIAGNOSTIX_STAT_OFFSET(modified, 40); DIAGNOSTIX_STAT_OFFSET(changed, 56);
DIAGNOSTIX_STAT_OFFSET(size, 72); DIAGNOSTIX_STAT_OFFSET(blocks, 80); DIAGNOSTIX_STAT_OFFSET(block_size, 88);
DIAGNOSTIX_STAT_OFFSET(flags, 92); DIAGNOSTIX_STAT_OFFSET(generation, 96); DIAGNOSTIX_STAT_OFFSET(spare, 100);
DIAGNOSTIX_STAT_OFFSET(born, 104);
#undef DIAGNOSTIX_STAT_OFFSET
bool decode_native_stat(const uint8_t* bytes, size_t length, NativeStat* out);
using NativeFstatCall = int (*)(int fd, void* native_buffer);
int read_native_stat(int fd, struct stat* out, NativeFstatCall call);
int native_fstat(int fd, struct stat* out);
}  // namespace diagnostix
