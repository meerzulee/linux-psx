// SPDX-License-Identifier: MIT
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string>
#include <vector>

namespace diagnostix {
struct Pin { const char* name; uint64_t bytes; const char* sha256; };
struct ProgressUpdate {
  const char* operation; const char* phase; const char* file;
  unsigned step, steps, file_index, file_count;
  uint64_t file_done, file_total, phase_done, phase_total;
  bool complete;
};
using Progress = void (*)(const ProgressUpdate&, void*);
enum class Result { Ready, Recovered, Missing, Interrupted, Invalid, IoError, Sent, HandoffFailed, AlreadyAttempted };
const char* message(Result result);
// Makes the loader read its boot arguments from a different file by rewriting
// the path in the bytes about to be sent. The installed loader is not changed.
// `from` must appear exactly once and `to` must be no longer than `from`.
bool redirect_loader_arguments(uint8_t* loader, size_t length, const std::string& from, const std::string& to);
enum class CleanupResult { Preview, Empty, Blocked, Changed, Cleared, IoError };
enum class CleanupKind { Installer, Generation };
struct CleanupItem { CleanupKind kind; std::string name; uint64_t bytes; };
struct CleanupPreview { std::vector<CleanupItem> items; uint64_t bytes = 0; bool generations_protected = true; };
const char* message(CleanupResult result);
class Bundle {
 public:
  Bundle(const Pin* pins, size_t count, Progress progress = nullptr, void* context = nullptr);
  Result inspect();                 // Read-only; never repairs at startup.
  Result install();                 // Explicit menu action; no extra confirmation.
  // Reuse this launch's unchanged verified snapshot; no retry. Manual adds
  // dx.mode=manual to the arguments; Automatic sends the loader unchanged.
  Result boot(bool manual = false);
  std::string status(Result result) const;
  CleanupResult preview_cleanup(CleanupPreview* preview); // Read-only; saves a bounded exact plan.
  CleanupResult clear_old_files(uint64_t* reclaimed);      // UI confirmation of that preview only.
#ifdef DIAGNOSTIX_HOST_TEST
  void paths(const std::string& source, const std::string& root,
             const std::string& namespace_base = "");
  void package_path(const std::string& path) { packages_ = path; }
  int failpoint = 0;
  int handoffs = 0;
  std::string sent;
#endif
 private:
  const Pin* pins_; size_t count_; Progress progress_; void* context_;
  std::string source_ = "/app0/bundle", root_ = "/data/diagnostix/versions/0.16", packages_ = "/data/pkg";
  std::string namespace_base_ = "/data/diagnostix";
  bool attempted_ = false;
  struct CleanupEntry { CleanupItem item; std::string identity; };
  std::vector<CleanupEntry> cleanup_;
  std::string verified_current_, checked_current_;
  std::string detail_, pending_log_;
  bool logging_ = false;
  bool cleanup_pending_ = false;
  ProgressUpdate update_{};
  uint64_t file_base_ = 0;
  void start(const char* operation, unsigned steps);
  void phase(unsigned step, const char* name);
  void file(size_t index);
  void finish(const char* name);
  static void file_progress(const char*, uint64_t done, uint64_t total, void* context);
  Result inspect_state(bool allow_verification);
  Result install_state();
  Result boot_state(bool manual);
  bool prepare_manual(uint8_t* loader, size_t length);
  std::string current_identity();
  std::string generation_identity(const std::string& directory);
  bool preflight(const std::string& directory, bool readonly_source = false);
  void record(const char* event, const std::string& detail);
  bool problem(const char* stage, const std::string& path, const std::string& why, int error);
  Result fail(Result result, const char* stage, const std::string& path, const char* why);
  CleanupResult scan_cleanup(std::vector<CleanupEntry>* entries, bool* generations_protected);
  bool cleanup_entry(CleanupKind kind, const char* name, CleanupEntry* entry);
  bool verify(const std::string& directory);
  bool namespace_ok(bool create);
  bool namespace_parents(bool create);
  bool shape(const std::string& directory, bool complete);
  bool remove_stage();
  Result recover();
};
}  // namespace diagnostix
