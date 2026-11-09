// SPDX-License-Identifier: MIT
#include "transport.hpp"
#include <cassert>
#include <cerrno>
#include <initializer_list>
#include <iostream>
#include <string>
#include <vector>
#include <sys/socket.h>

struct Script {
  std::vector<ssize_t> results;
  std::vector<int> errors;
  std::vector<size_t> requests;
  std::string received;
  size_t calls = 0;

  Script(std::initializer_list<ssize_t> r, std::initializer_list<int> e) : results(r), errors(e) {}

  ssize_t next(int socket, const void* data, size_t length, int flags) {
    assert(socket == 7 && flags == MSG_NOSIGNAL && calls < results.size());
    requests.push_back(length);
    const ssize_t result = results[calls];
    errno = errors[calls++];
    if (result > 0) received.append(static_cast<const char*>(data), static_cast<size_t>(result));
    return result;
  }
};

Script* active = nullptr;
ssize_t scripted_send(int socket, const void* data, size_t length, int flags) {
  return active->next(socket, data, length, flags);
}

bool run(Script* script, const std::string& input) {
  active = script;
  return diagnostix::socket_send_all(7, input.data(), input.size(), MSG_NOSIGNAL, scripted_send);
}

int main() {
  const std::string input = "0123456789";
  {
    Script script{{3, 2, 5}, {0, 0, 0}};
    assert(run(&script, input));
    assert(script.received == input && script.calls == 3);
    assert((script.requests == std::vector<size_t>{10, 7, 5}));
  }
  std::cout << "short writes advance through the complete loader PASS\n";
  {
    Script script{{-1, 4, 6}, {EINTR, 0, 0}};
    assert(run(&script, input));
    assert(script.received == input && script.calls == 3);
    assert((script.requests == std::vector<size_t>{10, 10, 6}));
  }
  std::cout << "interrupted writes retry on the same connection PASS\n";
  {
    Script script{{4, 0, 6}, {0, 0, 0}};
    assert(!run(&script, input));
    assert(script.received == "0123" && script.calls == 2);
  }
  std::cout << "zero write fails without another send PASS\n";
  {
    Script script{{4, -1, 6}, {0, EPIPE, 0}};
    assert(!run(&script, input));
    assert(script.received == "0123" && script.calls == 2 && errno == EPIPE);
  }
  std::cout << "send error fails without reconnect or retry PASS\n";
}
