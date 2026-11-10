// SPDX-License-Identifier: MIT
// Framebuffer/font primitives adapted from Omarchy psX (see provenance.json).
#define _POSIX_C_SOURCE 200809L
#include "ui.hpp"
#include <stdio.h>
#include <algorithm>
#include <initializer_list>
#ifdef DIAGNOSTIX_HOST_TEST
#include <ft2build.h>
#include FT_FREETYPE_H
#else
#include <proto-include.h>
#endif

namespace diagnostix {
namespace ui {
namespace {
constexpr Color background{16, 20, 26}, panel{25, 32, 41}, card{31, 40, 51};
constexpr Color foreground{240, 245, 247}, muted{154, 167, 181}, accent{143, 211, 199};
constexpr Color selected{43, 61, 70}, border{53, 66, 79};
constexpr Color good{127, 214, 164}, caution{240, 198, 116}, bad{242, 119, 122};
constexpr int kLeft = 420, kColumn = 1080;

// `omarchy-ascii DiagnostiX`, the font of the Omarchy logo. It has capitals
// only, so the D and the X take the accent colour to keep the name's case.
constexpr const char* kWordmark[] = {
    "████████▄   ▄█     ▄████████    ▄██████▄  ███▄▄▄▄    ▄██████▄     ▄████████     ███      ▄█  ▀████    ▐████▀",
    "███   ▀███ ███    ███    ███   ███    ███ ███▀▀▀██▄ ███    ███   ███    ███ ▀█████████▄ ███    ███▌   ████▀",
    "███    ███ ███▌   ███    ███   ███    █▀  ███   ███ ███    ███   ███    █▀     ▀███▀▀██ ███▌    ███  ▐███",
    "███    ███ ███▌   ███    ███  ▄███        ███   ███ ███    ███   ███            ███   ▀ ███▌    ▀███▄███▀",
    "███    ███ ███▌ ▀███████████ ▀▀███ ████▄  ███   ███ ███    ███ ▀███████████     ███     ███▌    ████▀██▄",
    "███    ███ ███    ███    ███   ███    ███ ███   ███ ███    ███          ███     ███     ███    ▐███  ▀███",
    "███   ▄███ ███    ███    ███   ███    ███ ███   ███ ███    ███    ▄█    ███     ███     ███   ▄███     ███▄",
    "████████▀  █▀     ███    █▀    ████████▀   ▀█   █▀   ▀██████▀   ▄████████▀     ▄████▀   █▀   ████       ███▄",
};
constexpr int kWordmarkDWidth = 10, kWordmarkXColumn = 93;
constexpr int kBlockWidth = 6, kBlockHeight = 12;

uint32_t pack(Color color) {
  return 0x80000000u | (static_cast<uint32_t>(color.r) << 16) |
         (static_cast<uint32_t>(color.g) << 8) | color.b;
}

void text(Surface& surface, FT_Face face, int start_x, int baseline, const char* value, Color color) {
  int pen_x = start_x;
  for (const unsigned char* cursor = reinterpret_cast<const unsigned char*>(value); *cursor; ++cursor) {
    if (FT_Load_Char(face, *cursor, FT_LOAD_RENDER) != 0) continue;
    FT_GlyphSlot glyph = face->glyph;
    // The field type differs between the SDK's FreeType (int) and current hosts.
    const int rows = static_cast<int>(glyph->bitmap.rows), columns = static_cast<int>(glyph->bitmap.width);
    for (int y = 0; y < rows; ++y) {
      for (int x = 0; x < columns; ++x) {
        const uint8_t coverage = glyph->bitmap.buffer[y * glyph->bitmap.pitch + x];
        if (coverage) surface.pixel(pen_x + glyph->bitmap_left + x, baseline - glyph->bitmap_top + y, color, coverage);
      }
    }
    pen_x += glyph->advance.x >> 6;
  }
}

int text_width(FT_Face face, const char* value) {
  int width = 0;
  for (const unsigned char* cursor = reinterpret_cast<const unsigned char*>(value); *cursor; ++cursor) {
    if (FT_Load_Char(face, *cursor, FT_LOAD_DEFAULT) == 0) width += face->glyph->advance.x >> 6;
  }
  return width;
}

std::vector<std::string> wrap(FT_Face face, const std::string& value, int width) {
  std::vector<std::string> lines;
  std::string line;
  size_t start = 0;
  while (start <= value.size()) {
    size_t end = value.find(' ', start);
    if (end == std::string::npos) end = value.size();
    const std::string word = value.substr(start, end - start);
    const std::string joined = line.empty() ? word : line + " " + word;
    if (!line.empty() && text_width(face, joined.c_str()) > width) {
      lines.push_back(line);
      line = word;
    } else {
      line = joined;
    }
    start = end + 1;
  }
  if (!line.empty()) lines.push_back(line);
  return lines;
}

int paragraph(Surface& surface, FT_Face face, int x, int baseline, int width, int leading,
              const std::string& value, Color color) {
  for (const std::string& line : wrap(face, value, width)) {
    text(surface, face, x, baseline, line.c_str(), color);
    baseline += leading;
  }
  return baseline;
}

void rounded_card(Surface& surface, int x, int y, int width, int height, Color fill, Color edge, int radius = 14) {
  surface.rounded_rectangle(x, y, width, height, radius, edge);
  surface.rounded_rectangle(x + 1, y + 1, width - 2, height - 2, radius - 1, fill);
}

// Every block is the three bytes E2 96 xx; only spaces are one byte.
void wordmark(Surface& surface, int left, int top) {
  const int half_width = kBlockWidth / 2, half_height = kBlockHeight / 2;
  int y = top;
  for (const char* row : kWordmark) {
    int column = 0;
    for (const unsigned char* cell = reinterpret_cast<const unsigned char*>(row); *cell; ++column) {
      if (*cell == ' ') {
        ++cell;
        continue;
      }
      const Color color = column < kWordmarkDWidth || column >= kWordmarkXColumn ? accent : foreground;
      const int x = left + column * kBlockWidth;
      switch (cell[2]) {
        case 0x88: surface.rectangle(x, y, kBlockWidth, kBlockHeight, color); break;
        case 0x84: surface.rectangle(x, y + half_height, kBlockWidth, half_height, color); break;
        case 0x80: surface.rectangle(x, y, kBlockWidth, half_height, color); break;
        case 0x8C: surface.rectangle(x, y, half_width, kBlockHeight, color); break;
        case 0x90: surface.rectangle(x + half_width, y, half_width, kBlockHeight, color); break;
        default: break;
      }
      cell += 3;
    }
    y += kBlockHeight;
  }
}

void header(Surface& surface, const Faces& faces, const char* subtitle) {
  surface.rectangle(0, 0, kWidth, kHeight, background);
  wordmark(surface, kLeft, 50);
  text(surface, faces.small, kLeft + kColumn - text_width(faces.small, kVersion), 112, kVersion, accent);
  text(surface, faces.body, kLeft, 178, subtitle, muted);
  surface.rectangle(kLeft, 212, kColumn, 1, border);
}

bool near_segment(float px, float py, float ax, float ay, float bx, float by, float radius) {
  const float dx = bx - ax, dy = by - ay;
  float along = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy);
  along = along < 0 ? 0 : along > 1 ? 1 : along;
  const float ex = px - ax - along * dx, ey = py - ay - along * dy;
  return ex * ex + ey * ey <= radius * radius;
}

// The same shapes as the Linux console font, on a 32-unit square.
bool button_covers(Button button, float x, float y) {
  if (button == Button::Cross) return near_segment(x, y, 8, 8, 24, 24, 2.3f) || near_segment(x, y, 24, 8, 8, 24, 2.3f);
  const float distance = (x - 16) * (x - 16) + (y - 16) * (y - 16);
  return distance >= 9.8f * 9.8f && distance <= 13.0f * 13.0f;
}

constexpr int kIconSize = 26;

void button_icon(Surface& surface, int x, int y, Button button) {
  const Color color = button == Button::Cross ? Color{130, 170, 255} : Color{242, 119, 122};
  const float scale = 32.0f / kIconSize;
  for (int row = 0; row < kIconSize; ++row) {
    for (int column = 0; column < kIconSize; ++column) {
      int hits = 0;
      for (int i = 0; i < 4; ++i) {
        for (int j = 0; j < 4; ++j) {
          hits += button_covers(button, (column + (i + 0.5f) / 4) * scale, (row + (j + 0.5f) / 4) * scale);
        }
      }
      if (hits) surface.pixel(x + column, y + row, color, static_cast<uint8_t>(hits * 255 / 16));
    }
  }
}

struct Hint2 { const Button* button; const char* label; };

void footer(Surface& surface, const Faces& faces, std::initializer_list<Hint2> hints) {
  surface.rectangle(kLeft, 968, kColumn, 1, border);
  text(surface, faces.small, kLeft, 1015, "D-pad / Left stick: Navigate", muted);
  int width = 0;
  for (const Hint2& hint : hints) width += (hint.button ? kIconSize + 10 : 0) + text_width(faces.small, hint.label) + 36;
  int x = kLeft + kColumn - width + 36;
  for (const Hint2& hint : hints) {
    if (hint.button) {
      button_icon(surface, x, 996, *hint.button);
      x += kIconSize + 10;
    }
    text(surface, faces.small, x, 1015, hint.label, muted);
    x += text_width(faces.small, hint.label) + 36;
  }
}

const Button kCross = Button::Cross, kCircle = Button::Circle;

void menu_row(Surface& surface, const Faces& faces, int y, bool focused, const Row& row) {
  rounded_card(surface, kLeft, y, kColumn, 86, focused ? selected : card, focused ? accent : border);
  if (focused) surface.rounded_rectangle(kLeft + 1, y + 16, 4, 54, 2, accent);
  text(surface, faces.action, kLeft + 44, y + 37, row.title, row.enabled ? foreground : muted);
  text(surface, faces.label, kLeft + 44, y + 66, row.detail.c_str(), muted);
  if (focused) text(surface, faces.title, kLeft + kColumn - 56, y + 57, ">", accent);
}

void choice_rows(Surface& surface, const Faces& faces, int x, int y, int width, const char* const* rows, int choice) {
  for (int index = 0; index < 2; ++index) {
    const int row_y = y + index * 82;
    rounded_card(surface, x, row_y, width, 66, index == choice ? selected : card, index == choice ? accent : border);
    text(surface, faces.action, x + 40, row_y + 43, rows[index], index == choice ? foreground : muted);
  }
}

void fact(Surface& surface, const Faces& faces, int x, const char* label, const std::string& value, Color color) {
  text(surface, faces.label, x, 272, label, muted);
  text(surface, faces.action, x, 312, value.c_str(), color);
}

struct Hint { const char* title; Color color; std::string first, second; };

Hint home_hint(const HomeView& view) {
  if (!view.problem.empty()) return {"SOMETHING WENT WRONG", bad, view.problem, ""};
  if (!view.notice.empty()) return {"DONE", good, view.notice, ""};
  const Fit fit = console_fit(view.console);
  if (fit == Fit::InfoOnly) {
    return {"HELP PORT THIS CONSOLE", accent, "This version can only start Linux on firmware 12.02.",
            "Save your console info and send it with the report form. It helps add support for this console."};
  }
  if (view.last == LastRun::Stopped) {
    if (view.last_stage.empty()) {
      return {"LAST RUN STOPPED", caution, "The last run started but never reported back. It may have stopped before Linux could save anything.",
              "Starting again runs the same tests."};
    }
    return {"LAST RUN STOPPED", caution, "The last run reached " + view.last_stage + " and then stopped.", "Its report is on your USB stick."};
  }
  if (fit == Fit::Untested) {
    return {"HELP PORT THIS CONSOLE", accent, "Linux has only been tested on a PS4 Slim with Baikal B1. It may not start on this console.",
            "Save your console info, try Linux, and send us what happens. A failed start helps too."};
  }
  if (view.files == Files::Missing) {
    return {"FIRST RUN", accent, "Automatic test installs DiagnostiX once, about " + storage_size(view.install_bytes) + ", then starts.",
            "Plug in a USB stick first so the report is kept."};
  }
  if (view.files == Files::Repair) {
    return {"FILES NEED REPAIR", caution, "An earlier install did not finish. Automatic test repairs it, then starts.", ""};
  }
  if (!view.usb) {
    return {"NO USB STICK", caution, "Plug in a USB stick before you start.", "The report is saved there as the tests run, so a freeze can't lose it."};
  }
  return {"READY", accent, "Automatic test needs nothing from you until the last few tests.",
          "If the console freezes, hold the power button to turn it off, then open DiagnostiX again."};
}
}  // namespace

void Surface::pixel(int x, int y, Color color, uint8_t alpha) {
  if (x < 0 || y < 0 || x >= kWidth || y >= kHeight) return;
  uint32_t* target = &pixels_[y * kWidth + x];
  if (alpha == 255) { *target = pack(color); return; }
  const uint32_t previous = *target;
  const uint16_t inverse = 255 - alpha;
  const Color mixed = {
      static_cast<uint8_t>((color.r * alpha + ((previous >> 16) & 0xff) * inverse) / 255),
      static_cast<uint8_t>((color.g * alpha + ((previous >> 8) & 0xff) * inverse) / 255),
      static_cast<uint8_t>((color.b * alpha + (previous & 0xff) * inverse) / 255),
  };
  *target = pack(mixed);
}

void Surface::rectangle(int x, int y, int width, int height, Color color) {
  const int x0 = x < 0 ? 0 : x, y0 = y < 0 ? 0 : y;
  const int x1 = x + width > kWidth ? kWidth : x + width, y1 = y + height > kHeight ? kHeight : y + height;
  const uint32_t encoded = pack(color);
  for (int py = y0; py < y1; ++py) {
    uint32_t* row = pixels_ + py * kWidth;
    for (int px = x0; px < x1; ++px) row[px] = encoded;
  }
}

void Surface::rounded_rectangle(int x, int y, int width, int height, int radius, Color color) {
  if (radius <= 0) { rectangle(x, y, width, height, color); return; }
  if (radius > width / 2) radius = width / 2;
  if (radius > height / 2) radius = height / 2;
  rectangle(x + radius, y, width - 2 * radius, height, color);
  rectangle(x, y + radius, width, height - 2 * radius, color);
  for (int dy = 0; dy < radius; ++dy) {
    for (int dx = 0; dx < radius; ++dx) {
      if (dx * dx + dy * dy > radius * radius) continue;
      pixel(x + radius - dx - 1, y + radius - dy - 1, color);
      pixel(x + width - radius + dx, y + radius - dy - 1, color);
      pixel(x + radius - dx - 1, y + height - radius + dy, color);
      pixel(x + width - radius + dx, y + height - radius + dy, color);
    }
  }
}

const char* southbridge_name(uint32_t id) {
  switch (id & 0xFFFFFF) {
    case 0x10100: return "Aeolia A0";
    case 0x10200: return "Aeolia A1";
    case 0x10300: return "Aeolia A2";
    case 0x20100: return "Belize A0";
    case 0x20200: return "Belize B0";
    case 0x30100: return "Baikal A0";
    case 0x30200: return "Baikal B0";
    case 0x30201: return "Baikal B1";
    case 0x40100: return "Belize2 A0";
    default: return "Unknown";
  }
}

std::string firmware_name(uint32_t version) {
  char value[16];
  snprintf(value, sizeof(value), "%x.%02x", (version >> 24) & 0xff, (version >> 16) & 0xff);
  return value;
}

namespace {
bool loader_firmware(const Console& console) {
  return console.firmware_known && ((console.firmware >> 16) & 0xffff) == 0x1202;
}
bool tested_southbridge(const Console& console) {
  return console.southbridge_known && (console.southbridge & 0xFFFFFF) == 0x30201;
}
}  // namespace

Fit console_fit(const Console& console) {
  if (!loader_firmware(console)) return Fit::InfoOnly;
  return tested_southbridge(console) && !console.pro ? Fit::Tested : Fit::Untested;
}

std::string console_argument(const Console& console) {
  std::string name = console.pro ? "PS4-Pro-" : "";
  name += console.southbridge_known ? southbridge_name(console.southbridge) : "Unknown";
  std::replace(name.begin(), name.end(), ' ', '-');
  return "dx.console=" + name;
}

std::string storage_size(uint64_t bytes) {
  char value[48];
  const uint64_t unit = bytes >= (1ULL << 30) ? 1ULL << 30 : 1ULL << 20;
  if (unit == (1ULL << 20)) {
    snprintf(value, sizeof(value), "%llu MB", static_cast<unsigned long long>(bytes / unit));
  } else {
    snprintf(value, sizeof(value), "%llu.%02llu GB", static_cast<unsigned long long>(bytes / unit),
             static_cast<unsigned long long>((bytes % unit) * 100 / unit));
  }
  return value;
}

void read_last_run(const std::string& text, HomeView* view) {
  constexpr size_t longest_stage = 32;
  view->last = LastRun::None;
  view->last_stage.clear();
  size_t start = 0;
  std::string state, stage;
  while (start < text.size()) {
    size_t end = text.find('\n', start);
    if (end == std::string::npos) end = text.size();
    const std::string line = text.substr(start, end - start);
    if (line.rfind("state=", 0) == 0) state = line.substr(6);
    if (line.rfind("stage=", 0) == 0) stage = line.substr(6);
    start = end + 1;
  }
  if (state == "finished") view->last = LastRun::Finished;
  if (state != "running") return;
  view->last = LastRun::Stopped;
  for (const char c : stage) {
    if (c >= ' ' && c <= '~' && view->last_stage.size() < longest_stage) view->last_stage.push_back(c);
  }
}

std::vector<Row> home_rows(const HomeView& view) {
  std::vector<Row> rows;
  const Fit fit = console_fit(view.console);
  const bool install = view.files != Files::Ready;
  if (fit == Fit::Tested) {
    rows.push_back({Action::Automatic, "Automatic test", install ? "Sets up the files, then runs every test by itself" : "Starts Linux and runs every test by itself", true});
    rows.push_back({Action::Manual, "Manual test", install ? "Sets up the files, then lets you choose the tests" : "Starts Linux and lets you choose the tests", true});
  } else {
    rows.push_back({Action::SaveInfo, "Save console info", view.usb ? "Writes what DiagnostiX can read about this console to your USB stick" : "Plug in a USB stick first", view.usb});
    if (fit == Fit::Untested) {
      rows.push_back({Action::TryLinux, "Try Linux anyway", install ? "Sets up the files, then tries to start Linux" : "Tries to start Linux and runs every test it can", true});
    }
  }
  rows.push_back({Action::Clear, "Clear old files", "Review and remove old installers and boot images", true});
  rows.push_back({Action::About, "About", "Credits, source and compatibility", true});
  return rows;
}

void draw_home(Surface& surface, const Faces& faces, const HomeView& view) {
  header(surface, faces, "Find out what works on your PS4");
  rounded_card(surface, kLeft, 232, kColumn, 104, panel, border, 14);
  const Console& console = view.console;
  fact(surface, faces, kLeft + 36, "CONSOLE", console.pro ? "PS4 Pro" : "PS4", foreground);
  fact(surface, faces, kLeft + 200, "SOUTHBRIDGE", console.southbridge_known ? southbridge_name(console.southbridge) : "Unknown", tested_southbridge(console) ? foreground : caution);
  fact(surface, faces, kLeft + 420, "FIRMWARE", console.firmware_known ? firmware_name(console.firmware).c_str() : "Unknown", loader_firmware(console) ? foreground : caution);
  const char* files = view.files == Files::Ready ? "Verified" : view.files == Files::Missing ? "Not installed" : view.files == Files::Repair ? "Needs repair" : "Problem";
  fact(surface, faces, kLeft + 610, "FILES", files, view.files == Files::Ready ? good : view.files == Files::Missing ? muted : caution);
  fact(surface, faces, kLeft + 860, "USB STICK", view.usb ? "Found" : "Not found", view.usb ? good : caution);

  const std::vector<Row> rows = home_rows(view);
  for (size_t index = 0; index < rows.size(); ++index) {
    menu_row(surface, faces, 356 + static_cast<int>(index) * 98, static_cast<int>(index) == view.choice, rows[index]);
  }

  const Hint hint = home_hint(view);
  rounded_card(surface, kLeft, 756, kColumn, 186, panel, border, 18);
  text(surface, faces.small, kLeft + 44, 804, hint.title, hint.color);
  int baseline = paragraph(surface, faces.small, kLeft + 44, 848, kColumn - 88, 32, hint.first, foreground);
  if (!hint.second.empty()) paragraph(surface, faces.small, kLeft + 44, baseline, kColumn - 88, 32, hint.second, muted);
  footer(surface, faces, {{&kCross, "Select"}, {nullptr, "PS: Return to Orbis"}});
}

void draw_no_usb(Surface& surface, const Faces& faces, int choice) {
  header(surface, faces, "Before you start");
  rounded_card(surface, 460, 300, 1000, 540, panel, border, 18);
  text(surface, faces.title, 510, 385, "No USB stick found", foreground);
  int baseline = paragraph(surface, faces.body, 510, 450, 900, 40, "Without one, you can only download the report over your network at the end. If the console freezes first, that run is lost.", muted);
  paragraph(surface, faces.small, 510, baseline + 14, 900, 32, "Any FAT32 or exFAT stick works. DiagnostiX only writes to its own folder.", muted);
  const char* const rows[] = {"Go back and plug one in", "Continue without a USB stick"};
  choice_rows(surface, faces, 510, 650, 900, rows, choice);
  footer(surface, faces, {{&kCross, "Select"}, {&kCircle, "Back"}});
}

void draw_try_linux(Surface& surface, const Faces& faces, int choice) {
  header(surface, faces, "Before you try");
  rounded_card(surface, 460, 300, 1000, 540, panel, border, 18);
  text(surface, faces.title, 510, 385, "Linux hasn't been tested here", foreground);
  int baseline = paragraph(surface, faces.body, 510, 450, 900, 40, "It has only run on a PS4 Slim with Baikal B1. On this console the screen may stay black. If nothing happens after 3 minutes, hold the power button to turn the console off.", muted);
  paragraph(surface, faces.small, 510, baseline + 14, 900, 32, "Linux runs from memory and doesn't write to the internal drive. Open DiagnostiX again afterwards to see how far it got.", muted);
  const char* const rows[] = {"Go back", "Try Linux anyway"};
  choice_rows(surface, faces, 510, 650, 900, rows, choice);
  footer(surface, faces, {{&kCross, "Select"}, {&kCircle, "Back"}});
}

void draw_starting(Surface& surface, const Faces& faces, bool manual) {
  header(surface, faces, manual ? "Manual test" : "Automatic test");
  rounded_card(surface, kLeft, 300, kColumn, 460, panel, border, 18);
  text(surface, faces.title, 470, 395, "Starting Linux", foreground);
  text(surface, faces.body, 470, 465, "The screen goes dark for about a minute.", muted);
  text(surface, faces.body, 470, 510, manual ? "You can then choose which tests to run." : "The tests then start by themselves.", muted);
  surface.rectangle(470, 560, 980, 1, border);
  text(surface, faces.small, 470, 615, "Nothing after 3 minutes?", caution);
  text(surface, faces.small, 470, 655, "Hold the power button to turn the console off.", foreground);
  text(surface, faces.small, 470, 695, "Open DiagnostiX again and it shows how far the last run got.", foreground);
}

void draw_about(Surface& surface, const Faces& faces) {
  header(surface, faces, "Credits and source");
  rounded_card(surface, kLeft, 300, kColumn, 560, panel, border, 18);
  text(surface, faces.body, 470, 390, "Linux psX / DiagnostiX - Meerzulee and contributors", foreground);
  text(surface, faces.body, 470, 460, "Native UI foundations: Omarchy psX (MIT)", foreground);
  text(surface, faces.body, 470, 530, "OpenOrbis SDK and Gontserrat font (bundled notices)", foreground);
  text(surface, faces.body, 470, 630, "github.com/meerzulee/linux-psx", accent);
  text(surface, faces.small, 470, 715, "Experimental. A passing test covers that check on this boot only.", muted);
  text(surface, faces.small, 470, 765, "Linux runs from memory. Reports leave out serials, MAC addresses and console IDs.", muted);
  text(surface, faces.small, 470, 815, "Made for any PS4. Tested on a PS4 Slim with Baikal B1.", muted);
  footer(surface, faces, {{&kCircle, "Back"}});
}

void draw_progress(Surface& surface, const Faces& faces, const ProgressUpdate& update) {
  header(surface, faces, update.operation);
  rounded_card(surface, kLeft, 300, kColumn, 510, panel, border, 18);
  char line[160];
  snprintf(line, sizeof(line), "STEP %u OF %u", update.step, update.steps);
  text(surface, faces.small, 470, 360, line, accent);
  text(surface, faces.body, 470, 415, update.phase, foreground);
  text(surface, faces.title, 470, 495, *update.file ? update.file : "Please wait", foreground);
  if (*update.file) {
    snprintf(line, sizeof(line), "File %u of %u   %llu / %llu MiB", update.file_index, update.file_count,
             static_cast<unsigned long long>(update.file_done / 1048576),
             static_cast<unsigned long long>((update.file_total + 1048575) / 1048576));
    text(surface, faces.body, 470, 545, line, muted);
  }
  // Overall progress weights each named step equally; it is not a claim that
  // skipped metadata checks read a full image.
  const uint64_t total = update.phase_total * update.steps;
  const uint64_t done = update.complete ? total : update.phase_total * (update.step - 1) + update.phase_done;
  const int bar = total ? static_cast<int>(980 * done / total) : 0;
  surface.rounded_rectangle(470, 605, 980, 22, 11, background);
  if (bar) surface.rounded_rectangle(470, 605, bar, 22, 11, accent);
  snprintf(line, sizeof(line), "%u%%", total ? static_cast<unsigned>(100 * done / total) : 0);
  text(surface, faces.body, 470, 680, line, foreground);
  text(surface, faces.small, 470, 750, "Keep this app open.", muted);
}

void draw_cleanup_review(Surface& surface, const Faces& faces, const CleanupPreview& cleanup, size_t page, int choice) {
  header(surface, faces, "Review exactly what can be removed");
  char line[192];
  const size_t pages = (cleanup.items.size() + 2) / 3;
  snprintf(line, sizeof(line), "PAGE %zu OF %zu   /   %zu ITEMS   /   %s TOTAL", page + 1, pages, cleanup.items.size(), storage_size(cleanup.bytes).c_str());
  text(surface, faces.small, 300, 262, line, accent);
  for (size_t row = 0; row < 3; ++row) {
    const size_t index = page * 3 + row;
    if (index >= cleanup.items.size()) break;
    const CleanupItem& item = cleanup.items[index];
    const int y = 300 + static_cast<int>(row) * 105;
    rounded_card(surface, 300, y, 1320, 88, card, border);
    text(surface, faces.small, 340, y + 34, item.name.c_str(), foreground);
    snprintf(line, sizeof(line), "%s   /   %s", item.kind == CleanupKind::Installer ? "Cached installer (/data/pkg)" : "Retained boot generation", storage_size(item.bytes).c_str());
    text(surface, faces.label, 340, y + 65, line, muted);
  }
  text(surface, faces.label, 300, 650, cleanup.generations_protected ? "Retained generations stay protected until this installed candidate is verified." : "Active boot, current/future versions and unknown files stay protected.", muted);
  const char* const rows[] = {"Cancel", page + 1 < pages ? "Review next page" : "Continue to confirmation"};
  choice_rows(surface, faces, 300, 700, 1320, rows, choice);
  footer(surface, faces, {{&kCross, "Continue"}, {&kCircle, "Cancel"}});
}

void draw_cleanup_confirm(Surface& surface, const Faces& faces, const CleanupPreview& cleanup, int choice) {
  header(surface, faces, "Clear old files");
  rounded_card(surface, 460, 300, 1000, 540, panel, border, 18);
  text(surface, faces.title, 510, 385, "Permanently clear the reviewed files?", foreground);
  char line[160];
  snprintf(line, sizeof(line), "Delete %zu listed items and reclaim %s.", cleanup.items.size(), storage_size(cleanup.bytes).c_str());
  text(surface, faces.body, 510, 450, line, muted);
  text(surface, faces.small, 510, 500, "Deleted files cannot be restored from this app.", muted);
  const char* const rows[] = {"Cancel", "Clear reviewed old files"};
  choice_rows(surface, faces, 510, 650, 900, rows, choice);
  footer(surface, faces, {{&kCross, "Confirm"}, {&kCircle, "Back"}});
}
}  // namespace ui
}  // namespace diagnostix
