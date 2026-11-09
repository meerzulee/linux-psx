// SPDX-License-Identifier: MIT
#include <cassert>
#include <cstdio>
#include <string>
#include "ui.hpp"
using namespace diagnostix::ui;

int main() {
  assert(firmware_name(0x12020001) == "12.02");
  assert(firmware_name(0x09000001) == "9.00");
  assert(std::string(southbridge_name(0x30201)) == "Baikal B1");
  assert(std::string(southbridge_name(0x10200)) == "Aeolia A1");
  assert(std::string(southbridge_name(0x999)) == "Unknown");

  Console baikal;
  baikal.southbridge_known = true;
  baikal.firmware_known = true;
  baikal.southbridge = 0x30201;
  baikal.firmware = 0x12020001;
  assert(supported(baikal));
  Console other_board = baikal;
  other_board.southbridge = 0x10200;
  assert(!supported(other_board));
  Console other_firmware = baikal;
  other_firmware.firmware = 0x11000001;
  assert(!supported(other_firmware));
  // Unreadable values must not block a console the loader would accept.
  assert(supported(Console{}));
  // One value read alone still counts, and the other does not hide it.
  Console firmware_only;
  firmware_only.firmware_known = true;
  firmware_only.firmware = 0x12020001;
  assert(supported(firmware_only));
  firmware_only.firmware = 0x11000001;
  assert(!supported(firmware_only));
  Console board_only;
  board_only.southbridge_known = true;
  board_only.southbridge = 0x10200;
  assert(!supported(board_only));

  HomeView view;
  view.console = baikal;
  std::vector<Row> rows = home_rows(view);
  assert(rows[0].action == Action::Automatic && rows[0].enabled);
  assert(rows[1].action == Action::Manual);

  view.console = other_board;
  rows = home_rows(view);
  assert(rows[0].action == Action::SaveInfo && !rows[0].enabled);
  for (const Row& row : rows) assert(row.action != Action::Automatic && row.action != Action::Manual);
  view.usb = true;
  assert(home_rows(view)[0].enabled);

  HomeView last;
  read_last_run("state=running\nstage=Video decoding\nfolder=x\n", &last);
  assert(last.last == LastRun::Stopped && last.last_stage == "Video decoding");
  read_last_run("state=finished\nstage=\nfolder=x\n", &last);
  assert(last.last == LastRun::Finished && last.last_stage.empty());
  read_last_run("nonsense", &last);
  assert(last.last == LastRun::None);
  read_last_run("state=running\nstage=" + std::string(500, 'x') + "\x01\n", &last);
  assert(last.last == LastRun::Stopped && last.last_stage.size() == 32);
  puts("PASS");
  return 0;
}
