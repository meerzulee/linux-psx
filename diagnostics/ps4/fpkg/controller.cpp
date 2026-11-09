// SPDX-License-Identifier: MIT
#include "controller.hpp"

namespace diagnostix {
namespace {
constexpr uint32_t kUp = 0x0010, kDown = 0x0040;
constexpr uint32_t kCircle = 0x2000, kCross = 0x4000;
int stick_zone(uint8_t value) {
  if (value <= 64) return -1;
  if (value >= 192) return 1;
  return 0;
}
}  // namespace

ControllerActions ControllerMapper::update(uint32_t buttons, uint8_t stick_y, bool connected) {
  if (!connected) {
    previous_ = 0;
    stick_zone_ = 0;
    was_connected_ = false;
    return {};
  }
  if (!was_connected_) {
    was_connected_ = true;
    previous_ = buttons;
    stick_zone_ = stick_zone(stick_y);
    return {};
  }

  const uint32_t pressed = buttons & ~previous_;
  previous_ = buttons;
  const int next_zone = stick_zone_;
  if (!stick_zone_) stick_zone_ = stick_zone(stick_y);
  else if (stick_y >= 96 && stick_y <= 160) stick_zone_ = 0;
  const int stick_move = !next_zone ? stick_zone_ : 0;

  const bool up = pressed & kUp, down = pressed & kDown;
  const int pad_move = up == down ? 0 : up ? -1 : 1;
  return {pad_move ? pad_move : stick_move,
          static_cast<bool>(pressed & kCross), static_cast<bool>(pressed & kCircle)};
}
}  // namespace diagnostix
