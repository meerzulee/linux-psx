// SPDX-License-Identifier: MIT
// Native Linux host mechanism test. This is not Chromium integration or PS4 evidence.
#define _GNU_SOURCE
#include <errno.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#if !defined(__linux__) || !defined(__x86_64__)
#error This bounded test requires a native Linux x86-64 host.
#endif

enum { CLOSED_UNSUPPORTED = 77, CLOSED_INSTALL_FAILURE = 78 };
enum mode { NORMAL, INJECT_UNSUPPORTED, REJECTED_FLAGS };

struct state {
  pthread_mutex_t mutex;
  pthread_cond_t condition;
  bool ready, release, done, before_ok, after_ok;
};

static bool allowed_probe(void) {
  errno = 0;
  return syscall(SYS_getppid) > 0 && errno == 0;
}

static bool denied_probe(void) {
  errno = 0;
  long value = syscall(SYS_getppid);
  int saved_errno = errno;
  return value == -1 && saved_errno == EACCES &&
         prctl(PR_GET_SECCOMP, 0, 0, 0, 0) == SECCOMP_MODE_FILTER &&
         prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) == 1;
}

static void *worker(void *opaque) {
  struct state *s = opaque;
  bool before = allowed_probe();
  if (pthread_mutex_lock(&s->mutex)) _exit(20);
  s->before_ok = before;
  s->ready = true;
  if (pthread_cond_signal(&s->condition)) _exit(21);
  while (!s->release)
    if (pthread_cond_wait(&s->condition, &s->mutex)) _exit(22);
  s->after_ok = denied_probe();
  s->done = true;
  if (pthread_cond_signal(&s->condition)) _exit(23);
  if (pthread_mutex_unlock(&s->mutex)) _exit(24);
  return NULL;
}

static void install_or_exit(enum mode mode) {
  // Capability absence is injected because this host may support TSYNC.
  // Chromium has its own capability check and fatal path; this is only a
  // mechanism/failure-control test, not a reimplementation of its sandbox.
  if (mode == INJECT_UNSUPPORTED) _exit(CLOSED_UNSUPPORTED);
  struct sock_filter instructions[] = {
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_getppid, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EACCES),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
  };
  struct sock_fprog program = {
    .len = (unsigned short)(sizeof(instructions) / sizeof(instructions[0])),
    .filter = instructions,
  };
  unsigned flags = SECCOMP_FILTER_FLAG_TSYNC;
  if (mode == REJECTED_FLAGS) flags |= 0x80000000U;
  // Unknown flags exercise an actual kernel rejection, rather than mocking
  // the seccomp syscall. Every unsuccessful installation exits immediately.
  if (syscall(SYS_seccomp, SECCOMP_SET_MODE_FILTER, flags, &program) != 0)
    _exit(CLOSED_INSTALL_FAILURE);
}

static void child_case(enum mode mode, int continuation_fd) {
  alarm(5);
  if (!allowed_probe()) _exit(10);
  if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) _exit(11);
  struct state s = {
    .mutex = PTHREAD_MUTEX_INITIALIZER,
    .condition = PTHREAD_COND_INITIALIZER,
  };
  pthread_t thread;
  if (pthread_create(&thread, NULL, worker, &s)) _exit(12);
  if (pthread_mutex_lock(&s.mutex)) _exit(13);
  while (!s.ready)
    if (pthread_cond_wait(&s.condition, &s.mutex)) _exit(14);
  if (!s.before_ok || !allowed_probe()) _exit(15);
  if (pthread_mutex_unlock(&s.mutex)) _exit(16);

  // The main thread and one pre-existing worker both had an allowed syscall.
  // Only the main thread calls seccomp; TSYNC must filter the worker as well.
  install_or_exit(mode);
  if (write(continuation_fd, "C", 1) != 1) _exit(17);
  if (!denied_probe()) _exit(18);
  if (pthread_mutex_lock(&s.mutex)) _exit(25);
  s.release = true;
  if (pthread_cond_signal(&s.condition)) _exit(26);
  while (!s.done)
    if (pthread_cond_wait(&s.condition, &s.mutex)) _exit(27);
  bool passed = s.after_ok;
  if (pthread_mutex_unlock(&s.mutex)) _exit(28);
  if (pthread_join(thread, NULL)) _exit(29);
  _exit(passed ? 0 : 30);
}

static bool run_case(enum mode mode, int expected, const char *label) {
  int fds[2];
  if (pipe(fds)) return false;
  pid_t child = fork();
  if (child < 0) { close(fds[0]); close(fds[1]); return false; }
  if (child == 0) {
    close(fds[0]);
    child_case(mode, fds[1]);
  }
  close(fds[1]);
  int status;
  pid_t reaped;
  do { reaped = waitpid(child, &status, 0); } while (reaped < 0 && errno == EINTR);
  char continuation[2];
  ssize_t bytes = read(fds[0], continuation, sizeof(continuation));
  close(fds[0]);
  bool ok = reaped == child && WIFEXITED(status) && WEXITSTATUS(status) == expected &&
            (mode == NORMAL ? bytes == 1 && continuation[0] == 'C' : bytes == 0);
  printf("%s: %s\n", label, ok ? "PASS" : "FAIL");
  return ok;
}

int main(void) {
  bool synced = run_case(NORMAL, 0, "two existing threads: allowed before, EACCES after real TSYNC");
  bool unsupported = run_case(INJECT_UNSUPPORTED, CLOSED_UNSUPPORTED,
                              "injected missing capability: no continuation");
  bool rejected = run_case(REJECTED_FLAGS, CLOSED_INSTALL_FAILURE,
                           "kernel-rejected installation: no continuation");
  // The parent never receives any filter; child-only sandboxing must stay local.
  bool parent_unchanged = allowed_probe();
  printf("parent remains outside child test filters: %s\n", parent_unchanged ? "PASS" : "FAIL");
  return synced && unsupported && rejected && parent_unchanged ? 0 : 1;
}
