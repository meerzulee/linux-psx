// SPDX-License-Identifier: MIT
#pragma once
#include <cstddef>
#include <cstdint>

namespace diagnostix {
// Filled and restored by GoldHEN. The layout and command numbers come from its
// public SDK (GoldHEN_Plugins_SDK, MIT).
struct JailbreakBackup {
  uint32_t cr_uid, cr_ruid, cr_rgid, cr_groups;
  uint64_t cr_paid, cr_caps[2];
  void* cr_prison;
  void* fd_cdir, *fd_jdir, *fd_rdir;
};
enum GoldhenCommand : uint64_t { kGoldhenVersion = 0, kGoldhenJailbreak = 2, kGoldhenUnjailbreak = 3 };
// SDK 1.00. Above every kernel error number, which is what any other answer would be.
constexpr long kGoldhenFirstSdk = 0x100;

// Injected so the order of these calls can be tested on a development host.
struct BoardCalls {
  int (*read)(uint32_t* value, size_t* length);
  // GoldHEN's raw answer. It can arrive flagged as an error: the version does (EXP-20261110-005).
  long (*goldhen)(uint64_t command, void* data);
  void (*note)(const char* step, long result, int error, uint32_t value);
};

// True only when *value holds the whole four-byte hw.sce_subsys_subid.
bool read_southbridge(const BoardCalls& calls, uint32_t* value);

// Root from GoldHEN for one short piece of work. True only when the backup was filled;
// return_root must then follow before anything else runs.
using GoldhenCall = long (*)(uint64_t command, void* data);
using NoteCall = void (*)(const char* step, long result, int error, uint32_t value);
bool borrow_root(GoldhenCall goldhen, NoteCall note, JailbreakBackup* backup);
void return_root(GoldhenCall goldhen, NoteCall note, JailbreakBackup* backup);
}  // namespace diagnostix
