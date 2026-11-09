// SPDX-License-Identifier: MIT
// Renders the real launcher screens on a development host, one PPM per screen.
// usage: ui-preview OUTPUT_DIR REGULAR_FONT BOLD_FONT
// Console text uses the PS4's system font, which a host does not have.
#include <ft2build.h>
#include FT_FREETYPE_H
#include <cassert>
#include <cstdio>
#include <string>
#include <vector>
#include "ui.hpp"

using namespace diagnostix;
using namespace diagnostix::ui;

namespace {
std::vector<uint32_t> pixels(kWidth * kHeight);
Surface surface;
Faces faces;
std::string output;

void save(const char* name) {
  const std::string path = output + "/" + name + ".ppm";
  FILE* file = fopen(path.c_str(), "wb");
  assert(file);
  fprintf(file, "P6\n%d %d\n255\n", kWidth, kHeight);
  for (uint32_t value : pixels) {
    const uint8_t rgb[3] = {static_cast<uint8_t>(value >> 16), static_cast<uint8_t>(value >> 8), static_cast<uint8_t>(value)};
    fwrite(rgb, 1, 3, file);
  }
  fclose(file);
}

HomeView baikal() {
  HomeView view;
  view.console.southbridge_known = true;
  view.console.firmware_known = true;
  view.console.southbridge = 0x30201;
  view.console.firmware = 0x12020001;
  view.files = Files::Ready;
  view.usb = true;
  view.install_bytes = 456095238;
  return view;
}
}  // namespace

int main(int argc, char** argv) {
  assert(argc == 4);
  output = argv[1];
  FT_Library library;
  assert(FT_Init_FreeType(&library) == 0);
  for (const FaceSpec& spec : kFaceSpecs) {
    FT_Face* face = &(faces.*spec.slot);
    assert(FT_New_Face(library, argv[spec.bold ? 3 : 2], 0, face) == 0);
    assert(FT_Set_Pixel_Sizes(*face, 0, spec.pixels) == 0);
  }
  surface.target(pixels.data());

  draw_home(surface, faces, baikal());
  save("1-home-ready");

  HomeView first = baikal();
  first.files = Files::Missing;
  draw_home(surface, faces, first);
  save("2-home-first-run");

  HomeView no_stick = baikal();
  no_stick.usb = false;
  draw_home(surface, faces, no_stick);
  save("3-home-no-usb");

  draw_no_usb(surface, faces, 0);
  save("4-no-usb-confirm");

  HomeView stopped = baikal();
  stopped.last = LastRun::Stopped;
  stopped.last_stage = "Graphics";
  draw_home(surface, faces, stopped);
  save("5-home-last-run-stopped");

  HomeView other = baikal();
  other.console.southbridge = 0x10200;
  other.console.firmware = 0x11000001;
  draw_home(surface, faces, other);
  save("6-home-not-supported");

  ProgressUpdate update{};
  update.operation = "Setting up DiagnostiX";
  update.phase = "Copying and verifying files";
  update.file = "initramfs.cpio.gz";
  update.step = 2;
  update.steps = 3;
  update.file_index = 3;
  update.file_count = 6;
  update.file_done = 212ULL << 20;
  update.file_total = 424ULL << 20;
  update.phase_done = 50;
  update.phase_total = 100;
  draw_progress(surface, faces, update);
  save("7-installing");

  draw_starting(surface, faces, false);
  save("8-starting");
  return 0;
}
