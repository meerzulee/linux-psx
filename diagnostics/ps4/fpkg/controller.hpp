// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>

namespace diagnostix {
struct ControllerActions {
  int move = 0;
  bool confirm = false;
  bool back = false;
};
class ControllerMapper {
 public:
  ControllerActions update(uint32_t buttons, uint8_t stick_y, bool connected);
 private:
  uint32_t previous_ = 0;
  int stick_zone_ = 0;
  bool was_connected_ = false;
};
}  // namespace diagnostix
