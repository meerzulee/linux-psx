// SPDX-License-Identifier: MIT
#include "native_stat.hpp"
#include <errno.h>
#include <string.h>
#ifndef DIAGNOSTIX_HOST_TEST
#include <orbis/libkernel.h>
static_assert(sizeof(struct stat) == 128 && offsetof(struct stat, st_mode) == 8 &&
    offsetof(struct stat, st_nlink) == 12 && offsetof(struct stat, st_size) == 80,
    "pinned SDK stat layout differs; review the native ABI conversion");
#endif

namespace diagnostix {
namespace {
uint16_t le16(const uint8_t* p) { return static_cast<uint16_t>(p[0] | static_cast<uint16_t>(p[1]) << 8); }
uint32_t le32(const uint8_t* p) { uint32_t n = 0; for (unsigned i = 0; i < 4; ++i) n |= static_cast<uint32_t>(p[i]) << (i * 8); return n; }
int64_t le64(const uint8_t* p) { uint64_t n = 0; for (unsigned i = 0; i < 8; ++i) n |= static_cast<uint64_t>(p[i]) << (i * 8); int64_t signed_value; memcpy(&signed_value, &n, sizeof(n)); return signed_value; }
NativeTimespec timestamp(const uint8_t* p) { return {le64(p), le64(p + 8)}; }
#ifndef DIAGNOSTIX_HOST_TEST
int kernel_fstat(int fd, void* buffer) {
  // The SDK prototype names its incompatible stat type. Pass independent raw
  // storage; never let the kernel write native bytes into an SDK stat object.
  return sceKernelFstat(fd, static_cast<OrbisKernelStat*>(buffer));
}
#endif
}  // namespace
bool decode_native_stat(const uint8_t* bytes, size_t length, NativeStat* out) {
  if (!bytes || !out || length < kNativeStatBytes) return false;
  NativeStat value{};
  value.device = le32(bytes); value.inode = le32(bytes + 4);
  value.mode = le16(bytes + 8); value.links = le16(bytes + 10);
  value.uid = le32(bytes + 12); value.gid = le32(bytes + 16); value.rdevice = le32(bytes + 20);
  value.accessed = timestamp(bytes + 24); value.modified = timestamp(bytes + 40); value.changed = timestamp(bytes + 56);
  value.size = le64(bytes + 72); value.blocks = le64(bytes + 80);
  value.block_size = le32(bytes + 88); value.flags = le32(bytes + 92); value.generation = le32(bytes + 96);
  const uint32_t spare = le32(bytes + 100); memcpy(&value.spare, &spare, sizeof(spare));
  value.born = timestamp(bytes + 104); *out = value; return true;
}
int read_native_stat(int fd, struct stat* out, NativeFstatCall call) {
  if (!out || !call) { errno = EINVAL; return -1; }
  *out = {};
  alignas(8) uint8_t bytes[kNativeStatBufferBytes]{};
  static_assert(sizeof(bytes) >= kNativeStatBytes && kNativeStatBufferBytes == 256, "kernel stat buffer capacity");
  const int result = call(fd, bytes);
  if (result != 0) return result;  // Never decode bytes from a failed API call.
  NativeStat value{};
  if (!decode_native_stat(bytes, sizeof(bytes), &value)) { errno = EINVAL; return -1; }
  out->st_dev = value.device; out->st_ino = value.inode; out->st_mode = value.mode; out->st_nlink = value.links;
  out->st_uid = value.uid; out->st_gid = value.gid; out->st_rdev = value.rdevice;
  out->st_atim = {value.accessed.seconds, value.accessed.nanoseconds};
  out->st_mtim = {value.modified.seconds, value.modified.nanoseconds};
  out->st_ctim = {value.changed.seconds, value.changed.nanoseconds};
  out->st_size = value.size; out->st_blocks = value.blocks; out->st_blksize = value.block_size;
#ifndef DIAGNOSTIX_HOST_TEST
  out->st_flags = value.flags; out->st_gen = value.generation;
  out->st_birthtim = {value.born.seconds, value.born.nanoseconds};
#endif
  return 0;
}
int native_fstat(int fd, struct stat* out) {
#ifdef DIAGNOSTIX_HOST_TEST
  return fstat(fd, out);
#else
  return read_native_stat(fd, out, kernel_fstat);
#endif
}
}  // namespace diagnostix
