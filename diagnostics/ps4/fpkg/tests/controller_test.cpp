// SPDX-License-Identifier: MIT
#include "controller.hpp"
#include <cassert>
#include <iostream>

using diagnostix::ControllerMapper;
constexpr uint32_t kUp = 0x0010, kDown = 0x0040;
constexpr uint32_t kCircle = 0x2000, kCross = 0x4000;

int main() {
  ControllerMapper mapper;
  assert(!mapper.update(kCross, 0, true).confirm);
  assert(!mapper.update(kCross, 128, true).confirm);
  assert(!mapper.update(0, 128, true).confirm);
  auto action = mapper.update(kCross, 128, true);
  assert(action.confirm && !action.move && !action.back);
  assert(!mapper.update(kCross, 128, true).confirm);
  assert(!mapper.update(0, 128, true).confirm);

  assert(mapper.update(kUp, 128, true).move == -1);
  assert(mapper.update(kUp, 128, true).move == 0);
  assert(mapper.update(0, 128, true).move == 0);
  assert(mapper.update(kUp | kDown, 128, true).move == 0);
  assert(mapper.update(0, 128, true).move == 0);
  assert(mapper.update(kDown, 128, true).move == 1);
  assert(mapper.update(kDown, 128, true).move == 0);
  assert(mapper.update(0, 128, true).move == 0);

  assert(mapper.update(0, 40, true).move == -1);
  assert(mapper.update(0, 40, true).move == 0);
  assert(mapper.update(0, 220, true).move == 0);
  assert(mapper.update(0, 100, true).move == 0);
  assert(mapper.update(0, 220, true).move == 1);
  assert(mapper.update(0, 150, true).move == 0);
  assert(mapper.update(0, 20, true).move == -1);

  assert(mapper.update(kUp, 220, true).move == -1);
  assert(mapper.update(kUp, 128, true).move == 0);
  action = mapper.update(kCircle, 128, true);
  assert(action.back && !action.confirm);
  assert(!mapper.update(kCircle, 128, true).back);

  assert(!mapper.update(0, 128, false).confirm);
  assert(!mapper.update(kCross, 220, true).confirm);
  assert(!mapper.update(kCross, 128, true).confirm);
  assert(!mapper.update(0, 128, true).confirm);
  assert(mapper.update(kCross, 128, true).confirm);
  assert(!mapper.update(0, 128, true).confirm);
  assert(mapper.update(0, 220, true).move == 1);
  std::cout << "controller edges, D-pad priority, stick deadzone/hysteresis and reconnect guards PASS\n";
}
