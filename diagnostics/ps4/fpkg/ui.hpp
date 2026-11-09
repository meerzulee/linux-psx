// SPDX-License-Identifier: MIT
// Launcher screens. Nothing here calls Orbis, so tests/ui_preview.cpp can render
// the real screens on a development host.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string>
#include <vector>
#include "bundle.hpp"

typedef struct FT_FaceRec_* FT_Face;

namespace diagnostix {
namespace ui {
constexpr int kWidth = 1920, kHeight = 1080;
constexpr const char* kVersion = "v0.16";

struct Color { uint8_t r, g, b; };

class Surface {
 public:
  void target(uint32_t* pixels) { pixels_ = pixels; }
  void pixel(int x, int y, Color color, uint8_t alpha = 255);
  void rectangle(int x, int y, int width, int height, Color color);
  void rounded_rectangle(int x, int y, int width, int height, int radius, Color color);
 private:
  uint32_t* pixels_ = nullptr;
};

struct Faces {
  FT_Face label = nullptr, small = nullptr, body = nullptr;
  FT_Face action = nullptr, title = nullptr, heading = nullptr;
};
struct FaceSpec { int pixels; bool bold; FT_Face Faces::*slot; };
inline constexpr FaceSpec kFaceSpecs[] = {
    {17, false, &Faces::label}, {21, false, &Faces::small}, {27, false, &Faces::body},
    {26, true, &Faces::action}, {44, true, &Faces::title},  {66, true, &Faces::heading},
};

struct Console {
  bool southbridge_known = false;  // Each value is read on its own, so one can fail alone.
  bool firmware_known = false;
  bool pro = false;
  uint32_t southbridge = 0;  // hw.sce_subsys_subid
  uint32_t firmware = 0;     // OrbisKernelSwVersion.Version, e.g. 0x12020001
};
const char* southbridge_name(uint32_t id);
std::string firmware_name(uint32_t version);
// A value that could not be read never blocks: the loader makes the final check.
bool supported(const Console& console);

enum class Button { Cross, Circle };
enum class Files { Ready, Missing, Repair, Problem };
enum class LastRun { None, Finished, Stopped };
enum class Action { Automatic, Manual, SaveInfo, Clear, About };

struct HomeView {
  Console console;
  Files files = Files::Missing;
  bool usb = false;
  LastRun last = LastRun::None;
  std::string last_stage;  // Empty when the last run left no report.
  std::string problem;     // Failed operation; replaces the hint card.
  std::string notice;      // Completed operation; replaces the hint card.
  uint64_t install_bytes = 0;
  int choice = 0;
};
struct Row { Action action; const char* title; std::string detail; bool enabled; };
std::vector<Row> home_rows(const HomeView& view);
// Reads the text of last-run.txt from a USB stick, which anyone could have edited.
void read_last_run(const std::string& text, HomeView* view);

void draw_home(Surface& surface, const Faces& faces, const HomeView& view);
void draw_no_usb(Surface& surface, const Faces& faces, int choice);
void draw_starting(Surface& surface, const Faces& faces, bool manual);
void draw_about(Surface& surface, const Faces& faces);
void draw_progress(Surface& surface, const Faces& faces, const ProgressUpdate& update);
void draw_cleanup_review(Surface& surface, const Faces& faces, const CleanupPreview& cleanup,
                         size_t page, int choice);
void draw_cleanup_confirm(Surface& surface, const Faces& faces, const CleanupPreview& cleanup,
                          int choice);
std::string storage_size(uint64_t bytes);
}  // namespace ui
}  // namespace diagnostix
