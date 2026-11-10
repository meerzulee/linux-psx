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
  assert(console_fit(baikal) == Fit::Tested);
  // Pro has another GPU, so a Baikal B1 inside one is still untested.
  Console pro = baikal;
  pro.pro = true;
  assert(console_fit(pro) == Fit::Untested);
  Console other_board = baikal;
  other_board.southbridge = 0x10200;
  assert(console_fit(other_board) == Fit::Untested);
  Console other_firmware = baikal;
  other_firmware.firmware = 0x11000001;
  assert(console_fit(other_firmware) == Fit::InfoOnly);
  // Firmware decides whether Linux can start at all, so an unread one can't try.
  assert(console_fit(Console{}) == Fit::InfoOnly);
  // An unread southbridge can still try: it may be any of them.
  Console firmware_only;
  firmware_only.firmware_known = true;
  firmware_only.firmware = 0x12020001;
  assert(console_fit(firmware_only) == Fit::Untested);
  firmware_only.firmware = 0x11000001;
  assert(console_fit(firmware_only) == Fit::InfoOnly);
  Console board_only;
  board_only.southbridge_known = true;
  board_only.southbridge = 0x10200;
  assert(console_fit(board_only) == Fit::InfoOnly);

  assert(console_argument(other_board) == "dx.console=Aeolia-A1");
  assert(console_argument(firmware_only) == "dx.console=Unknown");
  assert(console_argument(pro) == "dx.console=PS4-Pro-Baikal-B1");

  HomeView view;
  view.console = baikal;
  std::vector<Row> rows = home_rows(view);
  assert(rows[0].action == Action::Automatic && rows[0].enabled);
  assert(rows[1].action == Action::Manual);

  view.console = other_board;
  rows = home_rows(view);
  assert(rows[0].action == Action::SaveInfo && !rows[0].enabled);
  assert(rows[1].action == Action::TryLinux && rows[1].enabled);
  for (const Row& row : rows) assert(row.action != Action::Automatic && row.action != Action::Manual);
  view.usb = true;
  assert(home_rows(view)[0].enabled);

  view.console = other_firmware;
  rows = home_rows(view);
  assert(rows[0].action == Action::SaveInfo);
  for (const Row& row : rows) {
    assert(row.action != Action::Automatic && row.action != Action::Manual && row.action != Action::TryLinux);
  }

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
