// SPDX-License-Identifier: MIT
// Streaming SHA-256 and local handoff pattern adapted from Omarchy psX.
// Original MIT notice and exact import identities are in LICENSES/provenance.json.
#define _POSIX_C_SOURCE 200809L
#include "bundle.hpp"
#include "sha256.hpp"
#include "native_stat.hpp"
#include "transport.hpp"
#include <algorithm>
#include <arpa/inet.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

namespace diagnostix {
namespace {
constexpr char owner[] = "Diagnostix namespace v1\n";
constexpr char zeros[] = "0000000000000000000000000000000000000000000000000000000000000000";
constexpr const char* names[] = {"bzImage", "initramfs.cpio.gz", "bootargs.txt", "vram.txt", "loader.elf", "bundle.json"};
std::string join(const std::string& a, const char* b) { return a + "/" + b; }
int safe_open(const std::string& path, int flags, bool directory = false) {
  int fd = open(path.c_str(), flags | O_NOFOLLOW | (directory ? O_DIRECTORY : 0), 0644);
  if (fd < 0) return -1;
  struct stat s{};
  if (native_fstat(fd, &s) != 0 || (directory ? !S_ISDIR(s.st_mode) : !S_ISREG(s.st_mode))) {
    close(fd); return -1;
  }
  return fd;
}
bool directory(const std::string& path) { int fd = safe_open(path, O_RDONLY, true); if (fd < 0) return false; close(fd); return true; }
bool exists(const std::string& path) {
  // Retail Orbis has no usable lstat(). Enumerate the fixed parent so even a
  // dangling journal/staging symlink counts as present and blocks handoff.
  const auto slash = path.find_last_of('/');
  const std::string parent = path.substr(0, slash), name = path.substr(slash + 1);
  if (!directory(parent)) return true;  // Unknown is never treated as absent.
  DIR* dir = opendir(parent.c_str()); if (!dir) return true;
  bool present = false; errno = 0;
  while (dirent* item = readdir(dir)) { if (name == item->d_name) { present = true; break; } }
  if (errno) present = true;
  closedir(dir); return present;
}
bool flush_dir(const std::string& path) { int fd = safe_open(path, O_RDONLY, true); if (fd < 0) return false; bool ok = fsync(fd) == 0; if (close(fd) != 0) ok = false; return ok; }
bool write_all(int fd, const void* data, size_t length) {
  const auto* p = static_cast<const uint8_t*>(data);
  while (length) { ssize_t n = write(fd, p, length); if (n < 0 && errno == EINTR) continue; if (n <= 0) return false; p += n; length -= n; }
  return true;
}
bool small_write(const std::string& path, const std::string& data) {
  int fd = safe_open(path, O_WRONLY | O_CREAT | O_EXCL); if (fd < 0) return false;
  bool ok = write_all(fd, data.data(), data.size()) && fsync(fd) == 0;
  if (close(fd) != 0) ok = false;
  return ok;
}
bool small_read(const std::string& path, std::string* out, size_t maximum) {
  int fd = safe_open(path, O_RDONLY); if (fd < 0) return false;
  char buffer[256]; out->clear(); bool ok = true;
  for (;;) { ssize_t n = read(fd, buffer, sizeof(buffer)); if (n < 0 && errno == EINTR) continue; if (n < 0) { ok = false; break; } if (!n) break; out->append(buffer, n); if (out->size() > maximum) { ok = false; break; } }
  if (close(fd) != 0) ok = false; return ok;
}
bool owned_directory(const std::string& root, bool create) {
  const auto parent = root.substr(0, root.find_last_of('/'));
  if (!directory(parent)) return false;
  if (!exists(root)) {
    if (!create || mkdir(root.c_str(), 0755) != 0) return false;
  }
  if (!directory(root)) return false;
  std::string value;
  if (small_read(join(root, ".owner"), &value, sizeof(owner)) && value == owner) return true;
  if (!create) return false;
  // The first mkdir/owner write can also be interrupted. Only an empty
  // namespace, or its sole regular partial fixed owner marker, is recoverable.
  DIR* dir = opendir(root.c_str()); if (!dir) return false;
  bool empty_or_owner = true; errno = 0;
  while (dirent* item = readdir(dir)) {
    if (strcmp(item->d_name, ".") && strcmp(item->d_name, "..") && strcmp(item->d_name, ".owner")) { empty_or_owner = false; break; }
  }
  if (errno) empty_or_owner = false;
  closedir(dir); if (!empty_or_owner) return false;
  if (exists(join(root, ".owner"))) {
    if (!small_read(join(root, ".owner"), &value, sizeof(owner)) ||
        value.size() > strlen(owner) || strncmp(owner, value.data(), value.size()) || unlink(join(root, ".owner").c_str())) return false;
  }
  return small_write(join(root, ".owner"), owner) && flush_dir(root) && flush_dir(parent);
}
bool hex(const std::string& v) { if (v.size() != 64) return false; for (char c : v) if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f')) return false; return true; }
using FileProgress = void (*)(const char*, uint64_t, uint64_t, void*);
bool digest_file(const std::string& path, uint64_t maximum, std::string* digest, uint64_t* size,
                 FileProgress progress = nullptr, void* context = nullptr, int output = -1) {
  int fd = safe_open(path, O_RDONLY); if (fd < 0) return false;
  omarchy_ps4::Sha256Context hash; omarchy_ps4::sha256_init(&hash);
  uint8_t buffer[65536]; *size = 0; bool ok = true;
  for (;;) {
    ssize_t n = read(fd, buffer, sizeof(buffer)); if (n < 0 && errno == EINTR) continue;
    if (n < 0) { ok = false; break; } if (!n) break;
    if (static_cast<uint64_t>(n) > maximum - *size) { ok = false; break; }
    *size += n; omarchy_ps4::sha256_update(&hash, buffer, n);
    if (output >= 0 && !write_all(output, buffer, n)) { ok = false; break; }
    if (progress) progress(path.c_str(), *size, maximum, context);
  }
  if (close(fd) != 0) ok = false; uint8_t bytes[32]; omarchy_ps4::sha256_final(&hash, bytes);
  char encoded[65]; for (size_t i = 0; i < 32; ++i) snprintf(encoded + i * 2, 3, "%02x", bytes[i]);
  *digest = encoded; return ok;
}
bool manifest_digest(const std::string& dir, std::string* digest) { uint64_t size = 0; return digest_file(join(dir, "bundle.json"), 16384, digest, &size) && size; }
std::string text_digest(const std::string& text) {
  uint8_t bytes[32]; omarchy_ps4::sha256(reinterpret_cast<const uint8_t*>(text.data()), text.size(), bytes);
  char encoded[65]; for (size_t i = 0; i < 32; ++i) snprintf(encoded + i * 2, 3, "%02x", bytes[i]);
  return encoded;
}
bool known_name(const char* name) { for (const char* allowed : names) if (!strcmp(name, allowed)) return true; return false; }
struct InstallerPin { const char* name; uint64_t bytes; };
constexpr InstallerPin installers[] = {{"diagnostix-ram-alpha1-0.01.pkg", 460128256},
    {"diagnostix-ram-alpha1-0.02.pkg", 460128256}, {"diagnostix-ram-alpha1-0.03.pkg", 460193792},
    {"diagnostix-ram-alpha1-0.04.pkg", 460193792}, {"diagnostix-ram-alpha1-0.05.pkg", 460193792}, {"diagnostix-ram-alpha1-0.06.pkg", 460193792},
    {"diagnostix-ram-alpha1-0.07.pkg", 460193792}, {"diagnostix-ram-alpha1-0.08.pkg", 460390400}, {"diagnostix-ram-alpha1-0.09.pkg", 460390400},
    {"diagnostix-ram-alpha1-0.10.pkg", 460390400}};
constexpr char content_id[] = "IV0000-DGNX00001_00-DIAGNOSTIX000001";
constexpr uint64_t limits[] = {32ULL << 20, 1024ULL << 20, 511, 16, 1ULL << 20};
bool older_version(const std::string& version) { return version == "0.01" || version == "0.02" || version == "0.03" || version == "0.04" || version == "0.05" || version == "0.06" || version == "0.07" || version == "0.08" || version == "0.09" || version == "0.10" || version == "0.15"; }
// Only the small, unescaped ASCII schema emitted by our builder is eligible.
// Duplicate/unknown fields, future identities and unsupported JSON stay intact.
class OldManifest {
 public:
  explicit OldManifest(const std::string& text) : text_(text) {}
  uint64_t sizes[5]{};
  bool parse() {
    if (!take('{')) return false;
    unsigned seen = 0;
    do {
      std::string key, value; if (!string(&key) || !take(':')) return false;
      unsigned bit = key == "schema" ? 1 : key == "product" ? 2 : key == "version" ? 4 :
          key == "files" ? 8 : key == "profile" ? 16 : key == "kernel_release" ? 32 : 0;
      if (!bit || (seen & bit)) return false;
      seen |= bit;
      if (bit == 1) { uint64_t n; if (!number(&n) || n != 1) return false; }
      else if (bit == 8) { if (!files()) return false; }
      else {
        if (!string(&value)) return false;
        if ((bit == 2 && value != "Diagnostix") || (bit == 4 && !older_version(value)) ||
            (bit == 16 && value != "ps4-baikal-diagnostix-ram-alpha1") ||
            (bit == 32 && value != "7.2.11-psx-ps4-baikal-uvd-runtime-dev6")) return false;
      }
    } while (take(','));
    if (!take('}') || (seen & 15) != 15) return false;
    whitespace(); uint64_t total = 1 << 20;
    for (uint64_t n : sizes) total += n;
    return at_ == text_.size() && total <= (1ULL << 30);
  }
 private:
  const std::string& text_; size_t at_ = 0;
  void whitespace() { while (at_ < text_.size() && (text_[at_] == ' ' || text_[at_] == '\n' || text_[at_] == '\r' || text_[at_] == '\t')) ++at_; }
  bool take(char c) { whitespace(); if (at_ == text_.size() || text_[at_] != c) return false; ++at_; return true; }
  bool string(std::string* out) {
    if (!take('"')) return false;
    out->clear();
    while (at_ < text_.size() && text_[at_] != '"') {
      unsigned char c = text_[at_++]; if (c < 32 || c > 126 || c == '\\') return false;
      *out += static_cast<char>(c);
    }
    return take('"');
  }
  bool number(uint64_t* out) {
    whitespace(); *out = 0;
    if (at_ == text_.size() || text_[at_] < '1' || text_[at_] > '9') return false;
    while (at_ < text_.size() && text_[at_] >= '0' && text_[at_] <= '9') {
      unsigned n = text_[at_++] - '0'; if (*out > ((1ULL << 30) - n) / 10) return false;
      *out = *out * 10 + n;
    }
    return true;
  }
  bool files() {
    if (!take('{')) return false;
    unsigned seen = 0;
    do {
      std::string key; if (!string(&key) || !take(':') || !take('{')) return false;
      size_t index = 0; while (index < 5 && key != names[index]) ++index;
      if (index == 5 || (seen & (1u << index))) return false;
      seen |= 1u << index; unsigned fields = 0;
      do {
        std::string field, hash; if (!string(&field) || !take(':')) return false;
        if (field == "bytes" && !(fields & 1)) {
          if (!number(&sizes[index]) || sizes[index] > limits[index]) return false;
          fields |= 1;
        } else if (field == "sha256" && !(fields & 2)) {
          if (!string(&hash) || !hex(hash)) return false;
          fields |= 2;
        } else return false;
      } while (take(','));
      if (!take('}') || fields != 3) return false;
    } while (take(','));
    return take('}') && seen == 31;
  }
};
bool identity(const std::string& path, bool dir, std::string* value, uint64_t* bytes = nullptr, bool unique = true, struct stat* actual = nullptr) {
  int fd = safe_open(path, O_RDONLY, dir); if (fd < 0) return false;
  struct stat s{}; bool ok = native_fstat(fd, &s) == 0 && s.st_size >= 0 && (dir || !unique || s.st_nlink == 1);
  if (actual) *actual = s;
  if (close(fd) != 0) ok = false; if (!ok) return false;
  char text[256]; snprintf(text, sizeof(text), "%llu:%llu:%llu:%llu:%lld:%ld:%lld:%ld;",
      (unsigned long long)s.st_dev, (unsigned long long)s.st_ino, (unsigned long long)s.st_mode,
      (unsigned long long)s.st_size, (long long)s.st_mtim.tv_sec, s.st_mtim.tv_nsec,
      (long long)s.st_ctim.tv_sec, s.st_ctim.tv_nsec);
  *value += text; if (bytes) *bytes = static_cast<uint64_t>(s.st_size);
  return true;
}
bool directory_anchor(const std::string& path, std::string* value) {
  int fd = safe_open(path, O_RDONLY, true); if (fd < 0) return false;
  struct stat s{}; bool ok = native_fstat(fd, &s) == 0; if (close(fd) != 0) ok = false; if (!ok) return false;
  char text[96]; snprintf(text, sizeof(text), "%llu:%llu:%llu;", (unsigned long long)s.st_dev,
      (unsigned long long)s.st_ino, (unsigned long long)s.st_mode);
  *value += text; return true;
}
bool cleanup_barrier(const std::string& root) {
  return !exists(root) || (directory(root) && !exists(join(root, ".transaction")) &&
      !exists(join(root, ".transaction.new")) && !exists(join(root, "boot.new")));
}
bool installer_header(const std::string& path, uint64_t bytes) {
  int fd = safe_open(path, O_RDONLY); if (fd < 0) return false;
  uint8_t header[0x70]{}; size_t got = 0;
  while (got < sizeof(header)) {
    ssize_t n = read(fd, header + got, sizeof(header) - got);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) break;
    got += n;
  }
  struct stat s{}; bool ok = native_fstat(fd, &s) == 0 && s.st_size >= 0 && static_cast<uint64_t>(s.st_size) == bytes && s.st_nlink == 1;
  close(fd);
  return ok && got == sizeof(header) && !memcmp(header, "\177CNT", 4) &&
      !memcmp(header + 0x40, content_id, sizeof(content_id)) &&
      std::all_of(header + 0x40 + sizeof(content_id), header + sizeof(header), [](uint8_t c) { return c == 0; });
}
}  // namespace

Bundle::Bundle(const Pin* pins, size_t count, Progress progress, void* context)
    : pins_(pins), count_(count), progress_(progress), context_(context) {}
std::string Bundle::status(Result result) const {
  return (result == Result::Invalid || result == Result::IoError || result == Result::Interrupted) && !detail_.empty() ? detail_ : message(result);
}
void Bundle::record(const char* event, const std::string& detail) {
  const int saved = errno;
  char line[768]; snprintf(line, sizeof(line), "%s: %.680s\n", event, detail.c_str());
  fprintf(stderr, "[diagnostix] %s", line);
  if (logging_) {
    if (pending_log_.size() + strlen(line) > 7168) pending_log_.clear();
    pending_log_ += line;
    constexpr char heading[] = "Diagnostix launcher log v1\n";
    const auto path = join(root_, "launcher-status.log"); std::string marker, old, token;
    if (namespace_parents(false) && directory(root_) && small_read(join(root_, ".owner"), &marker, sizeof(owner)) && marker == owner) {
      const bool present = exists(path);
      bool writable = !present || (identity(path, false, &token) && small_read(path, &old, 8192) && old.compare(0, strlen(heading), heading) == 0);
      if (writable) {
        std::string next = present && old.size() + pending_log_.size() <= 8192 ? old : heading; next += pending_log_;
        int fd = safe_open(path, O_WRONLY | (present ? O_TRUNC : O_CREAT | O_EXCL));
        if (fd >= 0) { bool ok = write_all(fd, next.data(), next.size()) && fsync(fd) == 0; if (close(fd) != 0) ok = false; if (!ok) fprintf(stderr, "[diagnostix] launcher-status.log write failed errno=%d\n", errno); else pending_log_.clear(); }
      }
    }
  }
  errno = saved;
}
bool Bundle::problem(const char* stage, const std::string& path, const std::string& why, int error) {
  const auto slash = path.find_last_of('/'); const auto name = path.substr(slash == std::string::npos ? 0 : slash + 1);
  detail_ = std::string(stage) + ": " + name + ": " + why;
  char metadata[256]; struct stat s{}; int fd = open(path.c_str(), O_RDONLY | O_NOFOLLOW);
  if (fd >= 0 && native_fstat(fd, &s) == 0) {
    snprintf(metadata, sizeof(metadata), " errno=%d mode=%o nlink=%llu size=%lld dev=%llu ino=%llu", error,
        static_cast<unsigned>(s.st_mode), (unsigned long long)s.st_nlink, (long long)s.st_size,
        (unsigned long long)s.st_dev, (unsigned long long)s.st_ino);
  } else snprintf(metadata, sizeof(metadata), " errno=%d stat_errno=%d", error, errno);
  if (fd >= 0) close(fd);
  record("failed", std::string(stage) + " path=" + path + " reason=" + why + metadata);
  errno = error; return false;
}
Result Bundle::fail(Result result, const char* stage, const std::string& path, const char* why) {
  problem(stage, path, why, errno); return result;
}
void Bundle::start(const char* operation, unsigned steps) {
  update_ = {}; update_.operation = operation; update_.steps = steps;
  update_.phase = ""; update_.file = ""; update_.file_count = static_cast<unsigned>(count_);
  for (size_t i = 0; i < count_; ++i) update_.phase_total += pins_[i].bytes;
}
void Bundle::phase(unsigned step, const char* name) {
  update_.step = step; update_.phase = name; update_.file = "";
  update_.file_index = 0; update_.file_done = update_.file_total = update_.phase_done = file_base_ = 0;
  if (progress_) progress_(update_, context_);
  if (logging_) record("phase", name);
}
void Bundle::file(size_t index) {
  update_.file = pins_[index].name; update_.file_index = static_cast<unsigned>(index + 1);
  update_.file_done = 0; update_.file_total = pins_[index].bytes;
  file_base_ = update_.phase_done;
  if (progress_) progress_(update_, context_);
}
void Bundle::finish(const char* name) {
  update_.phase = name; update_.complete = true; update_.step = update_.steps;
  update_.file = ""; update_.phase_done = update_.phase_total;
  if (progress_) progress_(update_, context_);
}
void Bundle::file_progress(const char*, uint64_t done, uint64_t total, void* context) {
  auto* self = static_cast<Bundle*>(context);
  self->update_.file_done = done; self->update_.file_total = total;
  self->update_.phase_done = self->file_base_ + done;
  if (self->progress_) self->progress_(self->update_, self->context_);
}
#ifdef DIAGNOSTIX_HOST_TEST
void Bundle::paths(const std::string& source, const std::string& root, const std::string& namespace_base) {
  source_ = source; root_ = root; namespace_base_ = namespace_base;
}
#endif
bool Bundle::shape(const std::string& path, bool complete) {
  if (!directory(path)) return problem("Shape", path, "cannot open regular directory", errno);
  DIR* dir = opendir(path.c_str()); if (!dir) return problem("Shape", path, "opendir failed", errno);
  size_t count = 0; bool ok = true;
  for (;;) {
    errno = 0; dirent* item = readdir(dir);
    if (!item) {
      const int error = errno;
      if (logging_) record("shape-eof", path + " count=" + std::to_string(count) + " errno=" + std::to_string(error));
      if (error) ok = problem("Shape", path, "readdir failed", error);
      break;
    }
    if (!strcmp(item->d_name, ".") || !strcmp(item->d_name, "..")) continue;
    if (!known_name(item->d_name)) { ok = problem("Shape", join(path, item->d_name), "unexpected member preserved", 0); break; }
    int fd = safe_open(join(path, item->d_name), O_RDONLY);
    if (fd < 0) { ok = problem("Shape", join(path, item->d_name), "regular non-symlink file required", errno); break; }
    if (close(fd) != 0) { ok = problem("Shape", join(path, item->d_name), "close failed", errno); break; }
    ++count;
  }
  if (closedir(dir) != 0 && ok) ok = problem("Shape", path, "closedir failed", errno);
  if (ok && complete && count != 6) ok = problem("Shape", path, "expected exactly six fixed files; found " + std::to_string(count), 0);
  return ok;
}
bool Bundle::preflight(const std::string& path, bool readonly_source) {
  const char* stage = readonly_source ? "Source preflight" : "Installed preflight";
  if (count_ != 6) return problem(stage, path, "invalid compiled pin count", 0);
  if (!shape(path, true)) { detail_ = std::string(stage) + ": " + detail_; return false; }
  for (size_t i = 0; i < count_; ++i) {
    std::string token; uint64_t size = 0; const auto member = join(path, names[i]);
    if (strcmp(pins_[i].name, names[i]) || !hex(pins_[i].sha256) || !pins_[i].bytes) return problem(stage, member, "invalid compiled pin", 0);
    struct stat actual{};
    if (!identity(member, false, &token, &size, !readonly_source, &actual)) return problem(stage, member,
        readonly_source ? "regular source stat/close failed" : "unique regular file identity required", errno);
    if (logging_ && readonly_source) {
      char metadata[192]; snprintf(metadata, sizeof(metadata), " mode=%o nlink=%llu size=%lld expected=%llu", static_cast<unsigned>(actual.st_mode),
          (unsigned long long)actual.st_nlink, (long long)actual.st_size, (unsigned long long)pins_[i].bytes);
      record("source-stat", member + metadata);
    }
    if (size != pins_[i].bytes) return problem(stage, member,
        "size expected " + std::to_string(pins_[i].bytes) + ", found " + std::to_string(size), 0);
  }
  std::string digest;
  if (pins_[5].bytes > 16384 || !manifest_digest(path, &digest)) return problem(stage, join(path, "bundle.json"), "bounded manifest read failed", errno);
  if (digest != pins_[5].sha256) return problem(stage, join(path, "bundle.json"), "manifest hash differs from candidate", 0);
  return true;
}
std::string Bundle::generation_identity(const std::string& path) {
  std::string value;
  // Renaming the directory changes its timestamps, but preserves its inode
  // and all six member identities. No image bytes are read here.
  if (!shape(path, true) || !directory_anchor(path, &value)) return {};
  for (const char* name : names) if (!identity(join(path, name), false, &value)) return {};
  return value;
}
bool Bundle::verify(const std::string& path) {
  if (!preflight(path)) return false;
  const auto before = generation_identity(path); if (before.empty()) return false;
  for (size_t i = 0; i < count_; ++i) {
    file(i);
    std::string digest; uint64_t size;
    if (!digest_file(join(path, pins_[i].name), pins_[i].bytes, &digest, &size, file_progress, this) ||
        size != pins_[i].bytes || digest != pins_[i].sha256) return false;
  }
  return generation_identity(path) == before;
}
bool Bundle::namespace_parents(bool create) {
  if (namespace_base_.empty()) return true; // Explicit isolated host fixture.
  if (root_ != namespace_base_ + "/versions/0.16") return false;
  return owned_directory(namespace_base_, create) &&
      owned_directory(namespace_base_ + "/versions", create);
}
bool Bundle::namespace_ok(bool create) {
  return namespace_parents(create) && owned_directory(root_, create);
}
Result Bundle::inspect() {
  cleanup_pending_ = false; detail_.clear();
  start("Check installed candidate", 1); phase(1, "Verify once or reuse this launch's snapshot");
  const auto result = inspect_state(true);
  if (result == Result::Ready) finish("Installed candidate verified for this launch");
  return result;
}
Result Bundle::inspect_state(bool allow_verification) {
  if (!namespace_base_.empty()) {
    if (root_ != namespace_base_ + "/versions/0.16") return Result::Invalid;
    for (const auto& parent : {namespace_base_, namespace_base_ + "/versions"}) {
      if (!exists(parent)) return Result::Missing;
      if (!owned_directory(parent, false))
        return fail(Result::Invalid, "Namespace", parent, "parent owner/directory check refused");
    }
  }
  if (!exists(root_)) return Result::Missing;
  if (!namespace_ok(false)) { verified_current_.clear(); return fail(Result::Invalid, "Namespace", root_, "owner/directory check refused"); }
  if (!cleanup_barrier(root_)) return fail(Result::Interrupted, "Recovery", root_, "journal or staging present");
  if (!exists(join(root_, "boot"))) return Result::Missing;
  if (!preflight(join(root_, "boot"))) { verified_current_.clear(); return Result::Invalid; }
  const auto current = current_identity();
  if (!current.empty() && current == verified_current_) return Result::Ready;
  verified_current_.clear();
  if (!allow_verification) return fail(Result::Invalid, "Boot snapshot", join(root_, "boot"), "changed or not verified in this app launch");
  if (current.empty() || current == checked_current_) return fail(Result::Invalid, "Installed verification", join(root_, "boot"), "identity invalid or previously refused");
  checked_current_ = current;
  if (!verify(join(root_, "boot")) || current_identity() != current) return Result::Invalid;
  checked_current_.clear(); verified_current_ = current; return Result::Ready;
}
bool Bundle::remove_stage() {
  const auto path = join(root_, "boot.new");
  if (!exists(path)) return true;
  if (!shape(path, false)) return false;
  for (const char* name : names) { const auto file = join(path, name); if (exists(file) && unlink(file.c_str()) != 0) return false; }
  return rmdir(path.c_str()) == 0 && flush_dir(root_);
}
Result Bundle::recover() {
  std::string record;
  if (!small_read(join(root_, ".transaction"), &record, 80) || record.size() != 70 ||
      record.substr(0, 5) != "DGX1 " || record.back() != '\n' || !hex(record.substr(5, 64))) return Result::Invalid;
  const std::string old = record.substr(5, 64), boot = join(root_, "boot"), previous = root_ + "/previous-" + old;
  const auto current = current_identity();
  if (preflight(boot) && !current.empty() &&
      (current == verified_current_ || (verify(boot) && current_identity() == current))) {
    if (!remove_stage() || unlink(join(root_, ".transaction").c_str()) || !flush_dir(root_)) return Result::IoError;
    checked_current_.clear(); verified_current_ = current; return Result::Ready;
  }
  verified_current_.clear();
  if (old != zeros) {
    std::string digest;
    if (!exists(boot)) {
      if (!shape(previous, true) || !manifest_digest(previous, &digest) || digest != old || rename(previous.c_str(), boot.c_str()) || !flush_dir(root_)) return Result::Invalid;
    } else if (!shape(boot, true) || !manifest_digest(boot, &digest) || digest != old) return Result::Invalid;
  } else if (exists(boot)) return Result::Invalid;
  if (!remove_stage() || unlink(join(root_, ".transaction").c_str()) || !flush_dir(root_)) return Result::IoError;
  return Result::Recovered;
}
Result Bundle::install() {
  if (attempted_) return Result::AlreadyAttempted;
  cleanup_pending_ = false; detail_.clear(); pending_log_.clear(); logging_ = true;
  record("action", "Install / Verify selected; entry");
  const auto result = install_state();
  if (result != Result::Ready) verified_current_.clear();
  if ((result == Result::Invalid || result == Result::IoError) && detail_.empty()) problem("Install", root_, std::string("stopped during ") + update_.phase, errno);
  record("result", status(result)); logging_ = false;
  return result;
}
Result Bundle::install_state() {
  start("Install / Verify candidate", 3); phase(1, "Check manifest, sizes and current snapshot");
  // Only bounded metadata and the small manifest are read before copying.
  // Payload hashing happens once, while each source file is copied.
  if (!preflight(source_, true)) return Result::Invalid;
  if (!namespace_ok(true)) return fail(Result::Invalid, "Namespace", root_, "owner/directory check refused");
  record("entry", "owned namespace; source shape, sizes and manifest accepted");
  if (exists(join(root_, ".transaction.new"))) {
    // Journal publication precedes every boot-file write. An unpublished
    // partial journal can be removed only with no stage or active journal.
    std::string partial;
    if (exists(join(root_, ".transaction")) || exists(join(root_, "boot.new")) ||
        !small_read(join(root_, ".transaction.new"), &partial, 80)) return fail(Result::Invalid, "Recovery", join(root_, ".transaction.new"), "conflicting state or unreadable partial journal");
    if (unlink(join(root_, ".transaction.new").c_str()) || !flush_dir(root_)) return fail(Result::IoError, "Recovery", root_, "partial journal removal/flush failed");
    return Result::Recovered;
  }
  if (exists(join(root_, ".transaction"))) {
    const auto result = recover();
    if (result == Result::Ready) finish("Interrupted installation verified");
    return result;
  }
  if (exists(join(root_, "boot.new"))) return fail(Result::Interrupted, "Recovery", join(root_, "boot.new"), "staging exists without a valid journal");
  const auto boot = join(root_, "boot"), stage = join(root_, "boot.new");
  if (inspect_state(true) == Result::Ready) {
    finish("Installed candidate verified; unchanged snapshot reused"); return Result::Ready;
  }
  std::string old = zeros;
  if (exists(boot) && (!shape(boot, true) || !manifest_digest(boot, &old))) return fail(Result::Invalid, "Current generation", boot, "cannot retain this generation safely");
  const auto previous = root_ + "/previous-" + old;
  if (old != zeros && exists(previous)) return fail(Result::Invalid, "Activation", previous, "retained generation already exists");
  if (!small_write(join(root_, ".transaction.new"), "DGX1 " + old + "\n")) return fail(Result::IoError, "Journal", join(root_, ".transaction.new"), "write/flush/close failed");
#ifdef DIAGNOSTIX_HOST_TEST
  if (failpoint == 4) return Result::IoError;
#endif
  if (rename(join(root_, ".transaction.new").c_str(), join(root_, ".transaction").c_str()) || !flush_dir(root_)) return fail(Result::IoError, "Journal", root_, "publish/flush failed");
  if (mkdir(stage.c_str(), 0755)) return fail(Result::IoError, "Stage", stage, "mkdir failed");
  phase(2, "Copy, verify and flush once");
  std::string copied;
  if (!directory_anchor(stage, &copied)) return fail(Result::IoError, "Stage", stage, "directory identity failed");
  for (size_t i = 0; i < count_; ++i) {
    const auto destination = join(stage, pins_[i].name);
    record("copy", pins_[i].name);
    int out = safe_open(destination, O_WRONLY | O_CREAT | O_EXCL);
    if (out < 0) return fail(Result::IoError, "Copy", destination, "open new output failed");
    std::string digest; uint64_t size;
    file(i);
    const bool copied_ok = digest_file(join(source_, pins_[i].name), pins_[i].bytes, &digest, &size, file_progress, this, out);
    int error = errno; const char* reason = !copied_ok ? "source read/output write/close failed" : size != pins_[i].bytes ? "copied size mismatch" : digest != pins_[i].sha256 ? "copied SHA-256 mismatch" : nullptr;
    if (!reason && fsync(out) != 0) { reason = "output fsync failed"; error = errno; }
    if (close(out) != 0 && !reason) { reason = "output close failed"; error = errno; }
    if (reason) { problem("Copy", destination, reason, error); return Result::IoError; }
    if (!identity(destination, false, &copied)) return fail(Result::IoError, "Copy", destination, "output identity failed");
  }
  if (!flush_dir(stage)) return fail(Result::IoError, "Stage", stage, "directory flush/close failed");
#ifdef DIAGNOSTIX_HOST_TEST
  if (failpoint == 1) return Result::IoError;
#endif
  phase(3, "Activate verified generation");
  if (generation_identity(stage) != copied) return fail(Result::IoError, "Activation", stage, "stage identity changed after copy");
  if (old != zeros && (rename(boot.c_str(), previous.c_str()) || !flush_dir(root_))) return fail(Result::IoError, "Activation", boot, "retain previous/flush failed");
#ifdef DIAGNOSTIX_HOST_TEST
  if (failpoint == 2) return Result::IoError;
#endif
  // Bind the single verified copy to the same activated inodes. No reread.
  if (rename(stage.c_str(), boot.c_str()) || !flush_dir(root_) || generation_identity(boot) != copied) return fail(Result::IoError, "Activation", boot, "rename/flush/identity failed");
#ifdef DIAGNOSTIX_HOST_TEST
  if (failpoint == 3) return Result::IoError;
#endif
  if (unlink(join(root_, ".transaction").c_str()) || !flush_dir(root_)) return fail(Result::IoError, "Journal", root_, "remove/flush failed");
  checked_current_.clear(); verified_current_ = current_identity();
  if (verified_current_.empty() || generation_identity(boot) != copied) return fail(Result::IoError, "Activation", boot, "active identity changed");
  detail_.clear(); finish("Copied and verified once; ready to boot");
  return Result::Ready;
}
Result Bundle::boot(bool manual) {
  detail_.clear(); pending_log_.clear(); logging_ = true; record("action", manual ? "Manual boot selected; entry" : "Boot selected; entry");
  const auto result = boot_state(manual); record("result", status(result)); logging_ = false; return result;
}
bool redirect_loader_arguments(uint8_t* loader, size_t length, const std::string& from, const std::string& to) {
  if (to.size() > from.size() || !length) return false;
  // The terminator is part of the match, so a longer path cannot be mistaken for this one.
  const std::string needle(from.c_str(), from.size() + 1);
  uint8_t* const end = loader + length;
  uint8_t* found = std::search(loader, end, needle.begin(), needle.end());
  if (found == end || std::search(found + 1, end, needle.begin(), needle.end()) != end) return false;
  memset(found, 0, needle.size());
  memcpy(found, to.data(), to.size());
  return true;
}
bool Bundle::prepare_manual(uint8_t* loader, size_t length) {
  constexpr size_t loader_limit = 511;
  const auto arguments = join(root_ + "/boot", "bootargs.txt");
  const auto manual = join(namespace_base_, "manual-bootargs.txt");
  std::string text;
  if (!small_read(arguments, &text, loader_limit)) return problem("Manual", arguments, "could not read boot arguments", errno);
  while (!text.empty() && (text.back() == '\n' || text.back() == '\r' || text.back() == ' ')) text.pop_back();
  text += " dx.mode=manual";
  if (text.size() > loader_limit) return problem("Manual", manual, "boot arguments are too long", 0);
  if (exists(manual) && unlink(manual.c_str()) != 0) return problem("Manual", manual, "could not replace the old file", errno);
  if (!small_write(manual, text) || !flush_dir(namespace_base_)) return problem("Manual", manual, "could not save boot arguments", errno);
  if (!redirect_loader_arguments(loader, length, arguments, manual)) return problem("Manual", arguments, "path is not in this loader once", 0);
  return true;
}
Result Bundle::boot_state(bool manual) {
  cleanup_pending_ = false;
  if (attempted_) return Result::AlreadyAttempted;
  start("Boot Diagnostix", 2); phase(1, "Check this launch's verified snapshot");
  if (inspect_state(false) != Result::Ready) return Result::Invalid;
  const Pin& loader = pins_[4];
  if (loader.bytes > 1048576 || !loader.bytes) return Result::Invalid;
  auto* bytes = static_cast<uint8_t*>(malloc(loader.bytes)); if (!bytes) return Result::IoError;
  int fd = safe_open(join(root_ + "/boot", "loader.elf"), O_RDONLY); size_t got = 0;
  bool loaded = fd >= 0;
  if (fd >= 0) { while (got < loader.bytes) { ssize_t n = read(fd, bytes + got, loader.bytes - got); if (n < 0 && errno == EINTR) continue; if (n <= 0) break; got += n; } if (close(fd) != 0) loaded = false; }
  if (!loaded || got != loader.bytes || current_identity() != verified_current_) { verified_current_.clear(); free(bytes); return Result::Invalid; }
  if (manual && !prepare_manual(bytes, loader.bytes)) { free(bytes); return Result::IoError; }
  // One attempt per app launch, including connection/partial-send failures.
  attempted_ = true;
  phase(2, "Send verified loader once");
#ifdef DIAGNOSTIX_HOST_TEST
  ++handoffs; sent.assign(reinterpret_cast<const char*>(bytes), loader.bytes); free(bytes); finish("Loader sent; waiting for Linux"); return Result::Sent;
#else
  int sock = socket(AF_INET, SOCK_STREAM, 0); if (sock < 0) { free(bytes); return Result::HandoffFailed; }
  // SO_SNDBUF is only a throughput hint on this path. The complete-write loop
  // is the delivery contract, and PS4 may cap the reported buffer below the
  // loader size. Never reject a send that the loop can complete.
  int requested = 1048576;
  (void)setsockopt(sock, SOL_SOCKET, SO_SNDBUF, &requested, sizeof(requested));
  sockaddr_in address{}; address.sin_family = AF_INET; address.sin_port = htons(9090); address.sin_addr.s_addr = inet_addr("127.0.0.1");
  bool ok = connect(sock, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0;
  if (ok) {
    int flags = 0;
#ifdef MSG_NOSIGNAL
    flags |= MSG_NOSIGNAL;
#endif
    ok = socket_send_all(sock, bytes, loader.bytes, flags, ::send);
  }
  const int handoff_error = errno;
  record(ok ? "handoff" : "handoff-failed", ok ? "all " + std::to_string(loader.bytes) + " loader bytes written on one connection" :
      "complete loader write failed without reconnect; errno=" + std::to_string(handoff_error));
  close(sock); free(bytes); if (ok) finish("Loader sent; waiting for Linux");
  return ok ? Result::Sent : Result::HandoffFailed;
#endif
}
std::string Bundle::current_identity() {
  std::string value;
  if (!namespace_ok(false) || !identity(join(root_, ".owner"), false, &value) ||
      !identity(join(root_, "boot"), true, &value)) return {};
  const auto generation = generation_identity(join(root_, "boot"));
  return generation.empty() ? std::string() : value + generation;
}
bool Bundle::cleanup_entry(CleanupKind kind, const char* name, CleanupEntry* entry) {
  const std::string path = join(kind == CleanupKind::Installer ? packages_ : root_, name);
  entry->item = {kind, name, 0}; entry->identity.clear();
  if (kind == CleanupKind::Installer) {
    uint64_t expected = 0; for (const auto& allowed : installers) if (!strcmp(name, allowed.name)) expected = allowed.bytes;
    return expected && installer_header(path, expected) && directory_anchor(packages_, &entry->identity) &&
        identity(path, false, &entry->identity, &entry->item.bytes);
  }
  const std::string member = name;
  if (member.size() != 73 || member.substr(0, 9) != "previous-" || !hex(member.substr(9)) ||
      count_ != 6 || member.substr(9) == pins_[5].sha256 || !shape(path, true) ||
      !identity(path, true, &entry->identity)) return false;
  std::string raw; uint64_t bytes = 0;
  if (!small_read(join(path, "bundle.json"), &raw, 16384)) return false;
  const auto hash = text_digest(raw); if (hash != member.substr(9)) return false;
  OldManifest manifest(raw); if (!manifest.parse()) return false;
  entry->identity += hash;
  for (size_t i = 0; i < 6; ++i) {
    if (!identity(join(path, names[i]), false, &entry->identity, &bytes) ||
        bytes != (i == 5 ? raw.size() : manifest.sizes[i])) return false;
    entry->item.bytes += bytes;
  }
  return true;
}
CleanupResult Bundle::scan_cleanup(std::vector<CleanupEntry>* entries, bool* generations_protected) {
  entries->clear(); *generations_protected = true;
  if (attempted_) return CleanupResult::Blocked;
  const bool have_root = exists(root_);
  if (!cleanup_barrier(root_)) return CleanupResult::Blocked;
  if (directory(packages_)) {
    for (const auto& pin : installers) { CleanupEntry entry; if (cleanup_entry(CleanupKind::Installer, pin.name, &entry)) entries->push_back(entry); }
  }
  const std::string current = have_root ? current_identity() : "";
  if (!verified_current_.empty() && current == verified_current_) {
    *generations_protected = false;
    DIR* dir = opendir(root_.c_str()); if (!dir) return CleanupResult::Blocked;
    bool ok = true; errno = 0;
    while (dirent* item = readdir(dir)) {
      if (strncmp(item->d_name, "previous-", 9)) continue;
      CleanupEntry entry;
      if (cleanup_entry(CleanupKind::Generation, item->d_name, &entry)) {
        if (entries->size() == 32) { ok = false; break; }
        entries->push_back(entry);
      }
      errno = 0;  // Refused entries are preserved; only readdir errors block.
    }
    if (errno) ok = false;
    closedir(dir); if (!ok) return CleanupResult::Blocked;
  }
  std::sort(entries->begin(), entries->end(), [](const CleanupEntry& a, const CleanupEntry& b) { return a.item.name < b.item.name; });
  return entries->empty() ? CleanupResult::Empty : CleanupResult::Preview;
}
CleanupResult Bundle::preview_cleanup(CleanupPreview* preview) {
  cleanup_pending_ = false; cleanup_.clear(); *preview = {};
  const auto result = scan_cleanup(&cleanup_, &preview->generations_protected);
  if (result != CleanupResult::Preview) { cleanup_.clear(); return result; }
  for (const auto& entry : cleanup_) { preview->items.push_back(entry.item); preview->bytes += entry.item.bytes; }
  cleanup_pending_ = true; return result;
}
CleanupResult Bundle::clear_old_files(uint64_t* reclaimed) {
  *reclaimed = 0;
  if (!cleanup_pending_) return CleanupResult::Blocked;
  cleanup_pending_ = false;
  std::vector<CleanupEntry> current; bool protected_generations;
  const auto result = scan_cleanup(&current, &protected_generations);
  if (result == CleanupResult::Blocked) return result;
  if (current.size() != cleanup_.size()) return CleanupResult::Changed;
  for (size_t i = 0; i < current.size(); ++i) {
    if (current[i].item.name != cleanup_[i].item.name || current[i].item.kind != cleanup_[i].item.kind ||
        current[i].identity != cleanup_[i].identity) return CleanupResult::Changed;
  }
  // No runtime manifest, UI field or caller supplies a deletion path. Every
  // unlink below uses a known old installer basename or one of six fixed names.
  for (const auto& entry : cleanup_) {
    if (!cleanup_barrier(root_)) return CleanupResult::Blocked;
    const auto path = join(entry.item.kind == CleanupKind::Installer ? packages_ : root_, entry.item.name.c_str());
    if (entry.item.kind == CleanupKind::Installer) {
      CleanupEntry checked;
      if (!cleanup_entry(CleanupKind::Installer, entry.item.name.c_str(), &checked) || checked.identity != entry.identity) return CleanupResult::Changed;
      if (unlink(path.c_str())) return CleanupResult::IoError;
      *reclaimed += entry.item.bytes;
#ifdef DIAGNOSTIX_HOST_TEST
      if (failpoint == 5) return CleanupResult::IoError;
#endif
      if (!flush_dir(packages_)) return CleanupResult::IoError;
    } else {
      CleanupEntry checked;
      if (!namespace_ok(false) || current_identity() != verified_current_ ||
          !cleanup_entry(CleanupKind::Generation, entry.item.name.c_str(), &checked) || checked.identity != entry.identity) return CleanupResult::Changed;
      for (const char* name : names) {
        std::string unused; uint64_t bytes;
        if (!cleanup_barrier(root_) || !shape(path, false) || !identity(join(path, name), false, &unused, &bytes)) return CleanupResult::Changed;
        if (unlink(join(path, name).c_str())) return CleanupResult::IoError;
        *reclaimed += bytes;
#ifdef DIAGNOSTIX_HOST_TEST
        if (failpoint == 5) return CleanupResult::IoError;
#endif
      }
      if (rmdir(path.c_str()) || !flush_dir(root_)) return CleanupResult::IoError;
    }
  }
  cleanup_.clear(); return CleanupResult::Cleared;
}
const char* message(CleanupResult result) {
  switch (result) {
    case CleanupResult::Preview: return "Review the listed old files before confirmation.";
    case CleanupResult::Empty: return "No recognized old files eligible for cleanup.";
    case CleanupResult::Blocked: return "Cleanup blocked. Recover installation or close this app after handoff.";
    case CleanupResult::Changed: return "Files changed since preview. Review a fresh cleanup preview.";
    case CleanupResult::Cleared: return "Listed old files cleared. Active boot files preserved.";
    case CleanupResult::IoError: return "Cleanup stopped on a storage error. Review a fresh preview.";
  }
  return "Cleanup unavailable";
}
const char* message(Result result) {
  switch (result) {
    case Result::Ready: return "Installed candidate verified. Ready to boot.";
    case Result::Recovered: return "Previous state restored. Select Install again to update.";
    case Result::Missing: return "Select Install / Verify to prepare this candidate.";
    case Result::Interrupted: return "Interrupted installation. Select Install / Verify to recover.";
    case Result::Invalid: return "Candidate not verified or changed. Select Install / Verify.";
    case Result::IoError: return "Storage operation incomplete. Boot blocked; recovery required.";
    case Result::Sent: return "Loader sent once. UART reports preparation and boot results.";
    case Result::HandoffFailed: return "Handoff failed. No automatic retry; close this app.";
    case Result::AlreadyAttempted: return "Handoff already attempted. Close this app before recovery.";
  }
  return "Unavailable";
}
}  // namespace diagnostix
