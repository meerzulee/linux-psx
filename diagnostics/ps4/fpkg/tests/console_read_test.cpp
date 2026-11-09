// SPDX-License-Identifier: MIT
#include "console_read.hpp"
#include <cassert>
#include <cerrno>
#include <iostream>
#include <string>
#include <vector>

using diagnostix::BoardCalls;
using diagnostix::JailbreakBackup;
using diagnostix::kGoldhenJailbreak;
using diagnostix::kGoldhenUnjailbreak;
using diagnostix::kGoldhenVersion;

namespace {
struct Read { int result; int error; uint32_t value; size_t length; };
std::vector<Read> reads;
std::vector<std::string> order;
long version_result, granted_result;
bool fill_backup;
int prison;
void* restored_prison;

int fake_read(uint32_t* value, size_t* length) {
  assert(!reads.empty());
  const Read next = reads.front();
  reads.erase(reads.begin());
  order.push_back("read");
  *value = next.value;
  *length = next.length;
  errno = next.error;
  return next.result;
}
long fake_goldhen(uint64_t command, void* data) {
  if (command == kGoldhenVersion) {
    order.push_back("version");
    assert(data == nullptr);
    return version_result;
  }
  auto* backup = static_cast<JailbreakBackup*>(data);
  if (command == kGoldhenJailbreak) {
    order.push_back("root");
    if (fill_backup) backup->cr_prison = &prison;
    return granted_result;
  }
  assert(command == kGoldhenUnjailbreak);
  order.push_back("unroot");
  restored_prison = backup->cr_prison;
  return 0;
}
void fake_note(const char*, long, int, uint32_t) {}

const Read refused{-1, 1, 0, 4};
const Read board{0, 0, 0x30201, 4};

void reset(std::vector<Read> script) {
  reads = std::move(script);
  order.clear();
  version_result = 0x100;
  granted_result = 0;
  fill_backup = true;
  restored_prison = nullptr;
}
bool run(uint32_t* value) {
  *value = 0xdeadbeef;
  return diagnostix::read_southbridge(BoardCalls{fake_read, fake_goldhen, fake_note}, value);
}
void pass(const char* name) { std::cout << "PASS " << name << "\n"; }
using Steps = std::vector<std::string>;
}  // namespace

int main() {
  uint32_t value;

  reset({board});
  assert(run(&value) && value == 0x30201 && order == Steps({"read"}));
  pass("a plain read never asks for root");

  reset({refused});
  version_result = 14;  // EFAULT, what a kernel without GoldHEN could answer
  assert(!run(&value) && value == 0 && order == Steps({"read", "version"}));
  pass("without GoldHEN it stays unknown and asks for nothing");

  reset({refused});
  version_result = -1;
  assert(!run(&value) && value == 0 && order == Steps({"read", "version"}));
  pass("a negative version asks for nothing");

  reset({refused, board});
  assert(run(&value) && value == 0x30201 && order == Steps({"read", "version", "root", "read", "unroot"}));
  assert(restored_prison == &prison);
  pass("root is held for one read and returned with the same backup");

  reset({refused, refused});
  assert(!run(&value) && value == 0 && order == Steps({"read", "version", "root", "read", "unroot"}));
  pass("root is returned even when the read still fails");

  reset({refused, Read{0, 0, 0x30201, 2}});
  assert(!run(&value) && value == 0 && order.back() == "unroot");
  pass("a short value is not believed");

  reset({refused});
  fill_backup = false;
  assert(!run(&value) && value == 0 && order == Steps({"read", "version", "root"}));
  pass("an empty backup is never written back");

  for (const long answer : {1L, 22L, -1L}) {
    reset({refused, board});
    granted_result = answer;
    assert(run(&value) && value == 0x30201 && order.back() == "unroot");
  }
  pass("a filled backup always returns root, whatever the answer was");
  return 0;
}
