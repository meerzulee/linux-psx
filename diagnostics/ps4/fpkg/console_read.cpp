// SPDX-License-Identifier: MIT
#include "console_read.hpp"
#include <cerrno>

namespace diagnostix {
namespace {
bool read_once(const BoardCalls& calls, const char* step, uint32_t* value) {
  size_t length = sizeof(*value);
  *value = 0;
  errno = 0;
  const int result = calls.read(value, &length);
  calls.note(step, result, errno, *value);
  if (result == 0 && length == sizeof(*value)) return true;
  *value = 0;
  return false;
}
}  // namespace

bool read_southbridge(const BoardCalls& calls, uint32_t* value) {
  if (read_once(calls, "plain", value)) return true;
  // The kernel refuses this value to an installed app. A small payload could read
  // it, but GoldHEN's payload port closes after one payload and the loader needs
  // that one (EXP-20261108-007), so borrow root for this single read instead.
  JailbreakBackup backup{};
  if (!borrow_root(calls.goldhen, calls.note, &backup)) return false;
  const bool found = read_once(calls, "with-root", value);
  return_root(calls.goldhen, calls.note, &backup);
  return found;
}

bool borrow_root(GoldhenCall goldhen, NoteCall note, JailbreakBackup* backup) {
  const long version = goldhen(kGoldhenVersion, nullptr);
  note("goldhen", version, 0, 0);
  if (version < kGoldhenFirstSdk) return false;
  *backup = JailbreakBackup{};
  const long granted = goldhen(kGoldhenJailbreak, backup);
  note("root", granted, 0, 0);
  // GoldHEN's return codes cannot tell success from failure, so a filled backup is
  // the proof. An empty one cannot be restored: it would clear the credentials.
  return backup->cr_prison != nullptr;
}

void return_root(GoldhenCall goldhen, NoteCall note, JailbreakBackup* backup) {
  // Paths resolve differently as root, so this comes before any other work.
  const long restored = goldhen(kGoldhenUnjailbreak, backup);
  note("root-returned", restored, 0, 0);
}
}  // namespace diagnostix
