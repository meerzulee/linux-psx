// SPDX-License-Identifier: MIT
#pragma once
#include <stddef.h>
#include <sys/types.h>

namespace diagnostix {
using SocketSend = ssize_t (*)(int, const void*, size_t, int);
bool socket_send_all(int socket, const void* data, size_t length, int flags, SocketSend send_function);
}  // namespace diagnostix
