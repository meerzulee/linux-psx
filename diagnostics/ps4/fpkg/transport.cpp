// SPDX-License-Identifier: MIT
#include "transport.hpp"
#include <errno.h>
#include <stdint.h>

namespace diagnostix {
bool socket_send_all(int socket, const void* data, size_t length, int flags, SocketSend send_function) {
  const auto* next = static_cast<const uint8_t*>(data);
  while (length) {
    const ssize_t written = send_function(socket, next, length, flags);
    if (written < 0 && errno == EINTR) continue;
    if (written <= 0 || static_cast<size_t>(written) > length) return false;
    next += written;
    length -= static_cast<size_t>(written);
  }
  return true;
}
}  // namespace diagnostix
