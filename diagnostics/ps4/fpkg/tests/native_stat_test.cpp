// SPDX-License-Identifier: MIT
#include "native_stat.hpp"
#include <array>
#include <cassert>
#include <cerrno>
#include <cstring>
#include <iostream>
using diagnostix::NativeStat;
std::array<uint8_t, 256> witness{};
void put(size_t offset, uint64_t value, size_t bytes) {
  for (size_t i = 0; i < bytes; ++i) witness[offset + i] = static_cast<uint8_t>(value >> (i * 8));
}
uint64_t bits(int64_t value) { uint64_t out; std::memcpy(&out, &value, sizeof(out)); return out; }
struct SdkStatLayout {
  uint32_t device, inode, mode; uint16_t links;
  uint32_t uid, gid, rdevice;
  diagnostix::NativeTimespec accessed, modified, changed;
  int64_t size, blocks; uint32_t block_size, flags, generation;
  diagnostix::NativeTimespec born;
};
static_assert(sizeof(SdkStatLayout) == 128 && offsetof(SdkStatLayout, mode) == 8 &&
    offsetof(SdkStatLayout, links) == 12 && offsetof(SdkStatLayout, size) == 80, "observed SDK mismatch");
int error = 0, calls = 0;
int syscall_fixture(int fd, void* storage) {
  assert(fd == 17 && reinterpret_cast<uintptr_t>(storage) % 8 == 0); ++calls;
  auto* p = static_cast<uint8_t*>(storage);
  for (size_t i = 0; i < diagnostix::kNativeStatBufferBytes; ++i) assert(p[i] == 0);
  std::memcpy(p, witness.data(), 120);
  p[255] = 0x5a;  // ASan verifies the production wrapper reserves the full buffer.
  if (error) { errno = EACCES; return error; }
  return 0;
}
int main() {
  // Serialized native bytes, independent of the host and SDK stat structs.
  put(0, 0x12345678, 4); put(4, 0xfedcba98, 4); put(8, 0100555, 2); put(10, 1, 2);
  put(12, 0, 4); put(16, 0x76543210, 4); put(20, 0x89abcdef, 4);
  put(24, bits(-11), 8); put(32, 123456789, 8);
  put(40, 0x123456789, 8); put(48, 987654321, 8);
  put(56, bits(-33), 8); put(64, 42, 8);
  put(72, 11273216, 8); put(80, 22144, 8); put(88, 65536, 4);
  put(92, 0xaabbccdd, 4); put(96, 0x87654321, 4); put(100, 0xfffffffe, 4);
  put(104, bits(-44), 8); put(112, 777777777, 8);
  SdkStatLayout wrong{}; std::memcpy(&wrong, witness.data(), sizeof(wrong));
  assert(wrong.mode == 0300555 && wrong.links == 0 && wrong.size == 22144);
  NativeStat out{}; assert(diagnostix::decode_native_stat(witness.data(), 120, &out));
  assert(out.device == 0x12345678 && out.inode == 0xfedcba98 && out.mode == 0100555 && out.links == 1);
  assert(out.uid == 0 && out.gid == 0x76543210 && out.rdevice == 0x89abcdef);
  assert(out.accessed.seconds == -11 && out.accessed.nanoseconds == 123456789);
  assert(out.modified.seconds == 0x123456789 && out.modified.nanoseconds == 987654321);
  assert(out.changed.seconds == -33 && out.changed.nanoseconds == 42);
  assert(out.size == 11273216 && out.blocks == 22144 && out.block_size == 65536);
  assert(out.flags == 0xaabbccdd && out.generation == 0x87654321 && out.spare == -2);
  assert(out.born.seconds == -44 && out.born.nanoseconds == 777777777);
  for (size_t length = 0; length < 120; ++length) {
    const NativeStat before = out; assert(!diagnostix::decode_native_stat(witness.data(), length, &out));
    assert(std::memcmp(&out, &before, sizeof(out)) == 0);
  }
  assert(!diagnostix::decode_native_stat(nullptr, 120, &out));
  assert(!diagnostix::decode_native_stat(witness.data(), 120, nullptr));
  std::array<uint8_t, 121> unaligned{}; std::memcpy(unaligned.data() + 1, witness.data(), 120);
  assert(diagnostix::decode_native_stat(unaligned.data() + 1, 120, &out) && out.size == 11273216);
  struct stat converted{};
  assert(diagnostix::read_native_stat(17, &converted, syscall_fixture) == 0 && calls == 1);
  assert(converted.st_dev == out.device && converted.st_ino == out.inode && converted.st_mode == out.mode && converted.st_nlink == 1);
  assert(converted.st_uid == 0 && converted.st_gid == out.gid && converted.st_rdev == out.rdevice);
  assert(converted.st_size == 11273216 && converted.st_blocks == 22144 && converted.st_blksize == 65536);
  assert(converted.st_atim.tv_sec == -11 && converted.st_atim.tv_nsec == 123456789);
  assert(converted.st_mtim.tv_sec == 0x123456789 && converted.st_mtim.tv_nsec == 987654321);
  assert(converted.st_ctim.tv_sec == -33 && converted.st_ctim.tv_nsec == 42);
  error = -123; assert(diagnostix::read_native_stat(17, &converted, syscall_fixture) == -123 && errno == EACCES && calls == 2);
  struct stat zero{}; assert(std::memcmp(&converted, &zero, sizeof(zero)) == 0);
  assert(diagnostix::read_native_stat(17, nullptr, syscall_fixture) == -1 && errno == EINVAL && calls == 2);
  assert(diagnostix::read_native_stat(17, &converted, nullptr) == -1 && errno == EINVAL && calls == 2);
  // Negative signed fields retain their representation; policy consumers can refuse them.
  put(72, bits(-7), 8); put(80, bits(-9), 8);
  assert(diagnostix::decode_native_stat(witness.data(), 120, &out) && out.size == -7 && out.blocks == -9);
  std::cout << "native120/SDK128 observed mismatch, complete decode, bounded syscall and error guards PASS\n";
}
