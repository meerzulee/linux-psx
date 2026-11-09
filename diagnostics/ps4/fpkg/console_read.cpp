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
  const long version = calls.goldhen(kGoldhenVersion, nullptr);
  calls.note("goldhen", version, 0, 0);
  if (version < kGoldhenFirstSdk) return false;
  JailbreakBackup backup{};
  const long granted = calls.goldhen(kGoldhenJailbreak, &backup);
  calls.note("root", granted, 0, 0);
  // GoldHEN's return codes cannot tell success from failure, so a filled backup is
  // the proof. An empty one cannot be restored: it would clear the credentials.
  if (!backup.cr_prison) return false;
  const bool found = read_once(calls, "with-root", value);
  // Paths resolve differently as root, so this comes before any other work.
  const long restored = calls.goldhen(kGoldhenUnjailbreak, &backup);
  calls.note("root-returned", restored, 0, 0);
  return found;
}
}  // namespace diagnostix
