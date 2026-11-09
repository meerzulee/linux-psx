// SPDX-License-Identifier: MIT
//! The single run screen, drawn cell by cell for the Linux text console.
//!
//! The stock console font has no check marks, so states are words. With a
//! 512-glyph font the console keeps 16 foreground colours but only the first 8
//! background colours, so backgrounds stay in slots 0 to 7. Bold is avoided
//! because it switches the console to a different colour slot.
use crate::qr;
use crate::model::{Download, Finish, Question, Row, Screen, Snapshot, Status, clock};
use ratatui::Frame;
use ratatui::buffer::Buffer;
use ratatui::style::{Color, Style};
use std::sync::atomic::{AtomicBool, Ordering};

pub const PALETTE: [&str; 16] = [
    "10141a", "19202a", "2b3d46", "35424f", "5a2329", "1f4a3a", "6b5420", "9aa7b5", "64717f",
    "f2777a", "7fd6a4", "f0c674", "82aaff", "8fd3c7", "e58fbf", "f0f5f7",
];
const BACKGROUND: u8 = 0;
const PANEL: u8 = 1;
const SELECTION: u8 = 2;
const RULE: u8 = 3;
const YOUR_TURN: u8 = 6;
const MUTED: u8 = 7;
const DIM: u8 = 8;
const RED: u8 = 9;
const GREEN: u8 = 10;
const AMBER: u8 = 11;
const BLUE: u8 = 12;
const ACCENT: u8 = 13;
const PINK: u8 = 14;
const TEXT: u8 = 15;

/// `omarchy-ascii DiagnostiX`, the font of the Omarchy logo. It has capitals
/// only, so the D and the X take the accent colour to keep the name's case.
const WORDMARK: [&str; 8] = [
    "████████▄   ▄█     ▄████████    ▄██████▄  ███▄▄▄▄    ▄██████▄     ▄████████     ███      ▄█  ▀████    ▐████▀",
    "███   ▀███ ███    ███    ███   ███    ███ ███▀▀▀██▄ ███    ███   ███    ███ ▀█████████▄ ███    ███▌   ████▀",
    "███    ███ ███▌   ███    ███   ███    █▀  ███   ███ ███    ███   ███    █▀     ▀███▀▀██ ███▌    ███  ▐███",
    "███    ███ ███▌   ███    ███  ▄███        ███   ███ ███    ███   ███            ███   ▀ ███▌    ▀███▄███▀",
    "███    ███ ███▌ ▀███████████ ▀▀███ ████▄  ███   ███ ███    ███ ▀███████████     ███     ███▌    ████▀██▄",
    "███    ███ ███    ███    ███   ███    ███ ███   ███ ███    ███          ███     ███     ███    ▐███  ▀███",
    "███   ▄███ ███    ███    ███   ███    ███ ███   ███ ███    ███    ▄█    ███     ███     ███   ▄███     ███▄",
    "████████▀  █▀     ███    █▀    ████████▀   ▀█   █▀   ▀██████▀   ▄████████▀     ▄████▀   █▀   ████       ███▄",
];
const WORDMARK_D_WIDTH: usize = 10;
const WORDMARK_X_COLUMN: usize = 93;

static TRUE_COLOR: AtomicBool = AtomicBool::new(false);
static ICONS: AtomicBool = AtomicBool::new(false);

/// Over SSH the palette cannot be reprogrammed, so colours are sent directly.
pub fn use_true_color(enabled: bool) {
    TRUE_COLOR.store(enabled, Ordering::Relaxed);
}

/// The console font with button icons loaded, so the glyphs below will show.
pub fn use_icons(enabled: bool) {
    ICONS.store(enabled, Ordering::Relaxed);
}

/// Linux console escape sequences that load this screen's colours.
pub fn console_palette() -> String {
    PALETTE
        .iter()
        .enumerate()
        .map(|(slot, rgb)| format!("\x1b]P{slot:X}{rgb}"))
        .collect()
}

fn color(slot: u8) -> Color {
    if !TRUE_COLOR.load(Ordering::Relaxed) {
        return Color::Indexed(slot);
    }
    let rgb = PALETTE[slot as usize];
    let part = |at: usize| u8::from_str_radix(&rgb[at..at + 2], 16).unwrap_or(0);
    Color::Rgb(part(0), part(2), part(4))
}

struct Canvas<'a> {
    buffer: &'a mut Buffer,
    width: u16,
    height: u16,
}

impl Canvas<'_> {
    fn text(&mut self, x: u16, y: u16, value: &str, foreground: u8, background: u8) -> u16 {
        if y >= self.height || x >= self.width {
            return x;
        }
        let room = (self.width - x) as usize;
        let style = Style::new().fg(color(foreground)).bg(color(background));
        self.buffer.set_stringn(x, y, value, room, style);
        x + value.chars().count().min(room) as u16
    }

    fn band(&mut self, x: u16, y: u16, width: u16, background: u8) {
        self.text(x, y, &" ".repeat(width as usize), TEXT, background);
    }

    fn right(&mut self, end: u16, y: u16, value: &str, foreground: u8, background: u8) {
        let x = end.saturating_sub(value.chars().count() as u16);
        self.text(x, y, value, foreground, background);
    }

    fn rule(&mut self, x: u16, y: u16, width: u16) {
        self.text(x, y, &"─".repeat(width as usize), RULE, BACKGROUND);
    }

    /// Word-wraps within `width` and returns the row after the last line.
    fn wrapped(&mut self, x: u16, mut y: u16, width: u16, value: &str, foreground: u8) -> u16 {
        for line in wrap(value, width as usize) {
            self.text(x, y, &line, foreground, BACKGROUND);
            y += 1;
        }
        y
    }
}

/// Glyphs that prepare-console adds to the console font. Only the arrows are
/// shown without a controller, because a keyboard has arrow keys too.
fn icon(key: &str) -> Option<(&'static str, u8)> {
    if !ICONS.load(Ordering::Relaxed) {
        return None;
    }
    match key {
        "Cross" => Some(("\u{E0F0}\u{E0F1}", BLUE)),
        "Circle" => Some(("\u{E0F2}\u{E0F3}", RED)),
        "Triangle" => Some(("\u{E0F4}\u{E0F5}", GREEN)),
        "Square" => Some(("\u{E0F6}\u{E0F7}", PINK)),
        "Up / Down" => Some(("\u{2191}\u{2193}", TEXT)),
        _ => None,
    }
}

/// Keys that do the same as each button. `answering` is a question, where
/// Y and N read better than Enter and Esc. Both work everywhere.
fn keyboard_key(button: &str, answering: bool) -> &str {
    match (button, answering) {
        ("Cross", true) => "Y",
        ("Circle", true) => "N",
        ("Cross", false) => "Enter",
        ("Circle", false) => "Esc",
        ("Triangle", _) => "S",
        ("Options", _) => "A",
        (other, _) => other,
    }
}

/// The button's icon with a controller, otherwise the key to press.
fn key_hint(canvas: &mut Canvas, at: (u16, u16), button: &str, background: u8, pad: bool, answering: bool) -> u16 {
    let (x, y) = at;
    let shown = if pad || button == "Up / Down" { icon(button) } else { None };
    match shown {
        Some((glyphs, shade)) => canvas.text(x, y, glyphs, shade, background),
        None if pad => canvas.text(x, y, button, TEXT, background),
        None => canvas.text(x, y, keyboard_key(button, answering), TEXT, background),
    }
}

fn wrap(value: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    let mut line = String::new();
    for word in value.split_whitespace() {
        if !line.is_empty() && line.chars().count() + 1 + word.chars().count() > width {
            lines.push(std::mem::take(&mut line));
        }
        if !line.is_empty() {
            line.push(' ');
        }
        line.push_str(word);
    }
    if !line.is_empty() {
        lines.push(line);
    }
    lines
}

fn status_label(status: Status) -> (&'static str, u8) {
    match status {
        Status::Waiting => ("·", DIM),
        Status::Running => ("RUNNING", ACCENT),
        Status::Pass => ("PASS", GREEN),
        Status::Fail => ("FAIL", RED),
        Status::Found => ("FOUND", BLUE),
        Status::NotFound => ("NOT FOUND", AMBER),
        Status::Skipped => ("SKIPPED", DIM),
        Status::Blocked => ("BLOCKED", AMBER),
    }
}

enum Line<'a> {
    Heading(&'static str, bool),
    Item(&'a Row, usize),
}

pub fn draw(frame: &mut Frame, snapshot: &Snapshot) {
    let area = frame.area();
    let (width, height) = (area.width, area.height);
    let mut canvas = Canvas {
        buffer: frame.buffer_mut(),
        width,
        height,
    };
    for y in 0..height {
        canvas.band(0, y, width, BACKGROUND);
    }
    if width < 90 || height < 26 {
        canvas.text(1, 1, "This window is too small for DiagnostiX.", TEXT, BACKGROUND);
        return;
    }
    if matches!(snapshot.screen, Screen::Starting) {
        starting(&mut canvas, width, height, snapshot.note.as_deref());
        return;
    }

    let left = 3;
    let column = width - 6;
    let end = left + column;
    let title = canvas.text(left, 1, "DiagnostiX", TEXT, BACKGROUND);
    if snapshot.demo {
        canvas.text(title + 2, 1, "sample data", DIM, BACKGROUND);
    }
    canvas.right(end, 1, &header_status(&snapshot.login, &snapshot.console), MUTED, BACKGROUND);
    canvas.rule(left, 2, column);
    overview(&mut canvas, left, end, snapshot);

    let body_top = 7;
    let body_height = height - 3 - body_top;
    let link = match &snapshot.screen {
        Screen::Download(download) => download.link.as_deref(),
        Screen::Asking(question) => question.link.as_deref(),
        _ => None,
    };
    let code = link.and_then(qr::cells).filter(|rows| rows.len() as u16 <= body_height);
    let right = if let Some(rows) = &code {
        for (offset, row) in rows.iter().enumerate() {
            canvas.text(left, body_top + offset as u16, row, TEXT, BACKGROUND);
        }
        left + rows[0].chars().count() as u16 + 4
    } else {
        let list_width = (column * 48 / 100).clamp(46, 62);
        list(&mut canvas, left, body_top, list_width, body_height, snapshot);
        for y in body_top..body_top + body_height {
            canvas.text(left + list_width + 1, y, "│", RULE, BACKGROUND);
        }
        left + list_width + 3
    };
    let right_width = end - right;
    match &snapshot.screen {
        Screen::Starting => {}
        Screen::Running => now(&mut canvas, right, body_top, right_width, body_height, snapshot),
        Screen::Asking(question) => ask(&mut canvas, right, body_top, right_width, question, snapshot.pad),
        Screen::Finished(finish) => finished(&mut canvas, right, body_top, right_width, snapshot, finish),
        Screen::Download(download) => get_report(&mut canvas, right, body_top, right_width, download, code.is_some()),
        Screen::Choosing { cursor } => {
            let row = snapshot.rows().nth(*cursor);
            choosing(&mut canvas, right, body_top, right_width, row, snapshot.note.as_deref());
        }
        Screen::Review { cursor } => {
            let row = snapshot.rows().nth(*cursor);
            review(&mut canvas, right, body_top, right_width, row);
        }
    }
    canvas.rule(left, height - 3, column);
    footer(&mut canvas, left, end, height - 2, snapshot);
}

/// The half-width blocks exist only in the font prepare-console patches, so
/// the stock font rounds them up to a whole cell.
pub fn console_block(glyph: char, patched_font: bool) -> char {
    if !patched_font && matches!(glyph, '▌' | '▐') { '█' } else { glyph }
}

/// Shown from the moment the program starts until the diagnostic service answers.
fn starting(canvas: &mut Canvas, width: u16, height: u16, note: Option<&str>) {
    let art_width = WORDMARK.iter().map(|row| row.chars().count()).max().unwrap_or(0) as u16;
    let top = height.saturating_sub(WORDMARK.len() as u16 + 3) / 2;
    if width > art_width {
        let left = (width - art_width) / 2;
        let patched_font = ICONS.load(Ordering::Relaxed);
        for (y, row) in (top..).zip(WORDMARK) {
            for (column, glyph) in row.chars().enumerate().filter(|(_, glyph)| *glyph != ' ') {
                let shade = if column < WORDMARK_D_WIDTH || column >= WORDMARK_X_COLUMN { ACCENT } else { TEXT };
                let cell = console_block(glyph, patched_font);
                canvas.text(left + column as u16, y, cell.encode_utf8(&mut [0; 4]), shade, BACKGROUND);
            }
        }
    } else {
        canvas.text(width.saturating_sub(10) / 2, top + 4, "DiagnostiX", TEXT, BACKGROUND);
    }
    if let Some(note) = note {
        let x = width.saturating_sub(note.chars().count() as u16) / 2;
        canvas.text(x, top + WORDMARK.len() as u16 + 2, note, MUTED, BACKGROUND);
    }
}

fn overview(canvas: &mut Canvas, left: u16, end: u16, snapshot: &Snapshot) {
    let column = end - left;
    match &snapshot.screen {
        Screen::Finished(_) | Screen::Download(_) | Screen::Review { .. } => {
            let finished = format!("Finished in {}", clock(snapshot.elapsed));
            canvas.text(left, 4, &finished, TEXT, BACKGROUND);
            let mut x = left;
            for (count, label, shade) in [
                (snapshot.count(Status::Pass), "passed", GREEN),
                (snapshot.count(Status::Fail), "failed", RED),
                (snapshot.count(Status::Blocked), "blocked", AMBER),
                (snapshot.count(Status::Skipped), "skipped", DIM),
            ] {
                if count > 0 || label == "passed" || label == "failed" {
                    x = canvas.text(x, 5, &format!("{count} {label}"), shade, BACKGROUND) + 4;
                }
            }
        }
        Screen::Choosing { .. } => {
            canvas.text(left, 4, "Manual test", TEXT, BACKGROUND);
            canvas.text(left, 5, "Pick a test to run. A report is made when you finish.", MUTED, BACKGROUND);
        }
        _ => {
            let title = snapshot.parts.get(snapshot.part).map_or("", |part| part.title);
            let part = format!("Part {} of {}  ·  {title}", snapshot.part + 1, snapshot.parts.len());
            canvas.text(left, 4, &part, TEXT, BACKGROUND);
            let progress = format!(
                "Test {} of {}  ·  {}",
                (snapshot.done + 1).min(snapshot.total),
                snapshot.total,
                clock(snapshot.elapsed)
            );
            canvas.right(end, 4, &progress, MUTED, BACKGROUND);
            let filled = column as usize * snapshot.done / snapshot.total.max(1);
            canvas.text(left, 5, &"█".repeat(filled), ACCENT, BACKGROUND);
            canvas.text(left + filled as u16, 5, &"░".repeat(column as usize - filled), RULE, BACKGROUND);
        }
    }
}

fn list(canvas: &mut Canvas, x: u16, top: u16, width: u16, height: u16, snapshot: &Snapshot) {
    let mut lines = Vec::new();
    let mut index = 0;
    for (number, part) in snapshot.parts.iter().enumerate() {
        lines.push(Line::Heading(part.title, number == snapshot.part));
        for row in &part.rows {
            lines.push(Line::Item(row, index));
            index += 1;
        }
    }
    let cursor = match snapshot.screen {
        Screen::Choosing { cursor } | Screen::Review { cursor } => Some(cursor),
        _ => None,
    };
    let focus = lines
        .iter()
        .position(|line| match line {
            Line::Item(row, index) => Some(*index) == cursor || (cursor.is_none() && row.status == Status::Running),
            Line::Heading(..) => false,
        })
        .unwrap_or(0);
    let visible = height as usize;
    let first = focus.saturating_sub(visible.saturating_sub(4)).min(lines.len().saturating_sub(visible));
    let finished = matches!(snapshot.screen, Screen::Finished(_) | Screen::Download(_) | Screen::Review { .. });

    for (offset, line) in lines.iter().skip(first).take(visible).enumerate() {
        let y = top + offset as u16;
        match line {
            Line::Heading(title, current) => {
                let shade = if *current && !finished { ACCENT } else { DIM };
                canvas.text(x, y, &title.to_uppercase(), shade, BACKGROUND);
            }
            Line::Item(row, index) => {
                let selected = Some(*index) == cursor;
                let running = row.status == Status::Running;
                let fill = if selected {
                    SELECTION
                } else if running {
                    PANEL
                } else {
                    BACKGROUND
                };
                if fill != BACKGROUND {
                    canvas.band(x, y, width, fill);
                }
                if selected {
                    canvas.text(x, y, "›", ACCENT, fill);
                }
                let name_shade = if row.status == Status::Waiting && !selected { MUTED } else { TEXT };
                canvas.text(x + 2, y, row.name, name_shade, fill);
                canvas.text(x + 22, y, &row.detail, MUTED, fill);
                let (label, shade) = status_label(row.status);
                canvas.right(x + width - 1, y, label, shade, fill);
            }
        }
    }
}

fn now(canvas: &mut Canvas, x: u16, top: u16, width: u16, height: u16, snapshot: &Snapshot) {
    let bottom = top + height;
    canvas.text(x, top, "NOW TESTING", ACCENT, BACKGROUND);
    let mut y = top + 1;
    if let Some(row) = snapshot.running() {
        canvas.text(x, y, row.name, TEXT, BACKGROUND);
        y = canvas.wrapped(x, y + 1, width, row.about, MUTED);
    }
    if let Some(note) = &snapshot.note {
        y = canvas.wrapped(x, y + 1, width, note, AMBER);
    }
    if !snapshot.problems.is_empty() {
        y += 1;
        canvas.text(x, y, &format!("PROBLEMS SO FAR  {}", snapshot.problems.len()), RED, BACKGROUND);
        y += 1;
        for problem in &snapshot.problems {
            canvas.text(x, y, &problem.name, TEXT, BACKGROUND);
            y = canvas.wrapped(x, y + 1, width, &problem.why, MUTED);
        }
    }
    let room = bottom.saturating_sub(y + 2) as usize;
    let shown = snapshot.log.len().min(room);
    if shown > 0 {
        let mut line_y = bottom - shown as u16 - 1;
        canvas.text(x, line_y, "KERNEL LOG", DIM, BACKGROUND);
        for entry in &snapshot.log[snapshot.log.len() - shown..] {
            line_y += 1;
            let clipped: String = entry.chars().take(width as usize).collect();
            canvas.text(x, line_y, &clipped, DIM, BACKGROUND);
        }
    }
}

fn ask(canvas: &mut Canvas, x: u16, top: u16, width: u16, question: &Question, pad: bool) {
    canvas.band(x, top, width, YOUR_TURN);
    let label = format!(" YOUR TURN  ·  {} of {}", question.step, question.total);
    canvas.text(x, top, &label, TEXT, YOUR_TURN);
    canvas.text(x, top + 2, &question.title, TEXT, BACKGROUND);
    let mut y = top + 4;
    for paragraph in &question.body {
        y = canvas.wrapped(x, y, width, paragraph, MUTED) + 1;
    }
    if let Some(link) = &question.link {
        canvas.band(x, y, width, SELECTION);
        canvas.text(x + 1, y, link, TEXT, SELECTION);
        y += 1;
    }
    y += 1;
    let mut button = x;
    for (key, answer) in &question.choices {
        let start = canvas.text(button, y, "  ", TEXT, SELECTION);
        let after_key = key_hint(canvas, (start, y), key, SELECTION, pad, true);
        button = canvas.text(after_key, y, &format!("  {answer}  "), TEXT, SELECTION) + 2;
    }
    y += 2;
    let skip = format!("Skips by itself in {}", clock(question.skips_in));
    canvas.text(x, y, &skip, AMBER, BACKGROUND);
    if pad {
        canvas.text(x, y + 1, question.keyboard, DIM, BACKGROUND);
    }
}

fn finished(canvas: &mut Canvas, x: u16, top: u16, width: u16, snapshot: &Snapshot, finish: &Finish) {
    let mut y = top;
    if snapshot.problems.is_empty() {
        canvas.text(x, y, "NOTHING FAILED", GREEN, BACKGROUND);
        y += 2;
    } else {
        canvas.text(x, y, "WHAT FAILED", RED, BACKGROUND);
        y += 1;
        for problem in &snapshot.problems {
            canvas.text(x, y, &problem.name, TEXT, BACKGROUND);
            y = canvas.wrapped(x, y + 1, width, &problem.why, MUTED);
        }
        y += 1;
    }
    canvas.text(x, y, "YOUR REPORT", ACCENT, BACKGROUND);
    y += 1;
    match &finish.report {
        Some(path) => {
            canvas.text(x, y, "Saved on your USB stick in", MUTED, BACKGROUND);
            canvas.text(x, y + 1, path, TEXT, BACKGROUND);
            y += 2;
        }
        None => {
            canvas.text(x, y, "Only in memory. Save it before you leave.", AMBER, BACKGROUND);
            y += 1;
        }
    }
    y = canvas.wrapped(x, y, width, "Serial numbers, MAC addresses and console IDs are left out.", DIM) + 1;
    for (index, item) in finish.menu.iter().enumerate() {
        let selected = index == finish.cursor;
        let fill = if selected { SELECTION } else { BACKGROUND };
        canvas.band(x, y, width, fill);
        if selected {
            canvas.text(x, y, "›", ACCENT, fill);
        }
        canvas.text(x + 2, y, item, if selected { TEXT } else { MUTED }, fill);
        y += 1;
    }
}

fn get_report(canvas: &mut Canvas, x: u16, top: u16, width: u16, download: &Download, scannable: bool) {
    canvas.text(x, top, "GET YOUR REPORT", ACCENT, BACKGROUND);
    let Some(link) = &download.link else {
        canvas.wrapped(x, top + 2, width, "No network connection. Plug in an Ethernet cable and this screen updates by itself.", AMBER);
        return;
    };
    let how = if scannable {
        "Scan this with your phone, or open the address on a computer. Either one must be on the same network as this console."
    } else {
        "On a phone or computer on the same network, open this address:"
    };
    let mut y = canvas.wrapped(x, top + 2, width, how, MUTED) + 1;
    canvas.band(x, y, width, SELECTION);
    canvas.text(x + 1, y, link, TEXT, SELECTION);
    y = canvas.wrapped(x, y + 2, width, "It downloads one file. Serial numbers, MAC addresses and console IDs are left out.", MUTED) + 1;
    let (state, shade) = if download.fetched {
        ("Downloaded.", GREEN)
    } else {
        ("Waiting for you to open it.", AMBER)
    };
    canvas.text(x, y, state, shade, BACKGROUND);
    if let Some(ssh) = &download.ssh {
        canvas.text(x, y + 2, "FOR DEVELOPERS", DIM, BACKGROUND);
        canvas.text(x, y + 3, ssh, DIM, BACKGROUND);
    }
}

fn choosing(canvas: &mut Canvas, x: u16, top: u16, width: u16, row: Option<&Row>, note: Option<&str>) {
    let Some(row) = row else { return };
    canvas.text(x, top, "SELECTED TEST", ACCENT, BACKGROUND);
    canvas.text(x, top + 1, row.name, TEXT, BACKGROUND);
    let y = canvas.wrapped(x, top + 2, width, row.about, MUTED) + 1;
    let (label, shade) = status_label(row.status);
    let label = if row.status == Status::Waiting { "Not run yet" } else { label };
    let end = canvas.text(x, y, "This boot: ", DIM, BACKGROUND);
    canvas.text(end, y, label, shade, BACKGROUND);
    if let Some(note) = note {
        canvas.wrapped(x, y + 2, width, note, AMBER);
    }
}

fn review(canvas: &mut Canvas, x: u16, top: u16, width: u16, row: Option<&Row>) {
    let Some(row) = row else { return };
    canvas.text(x, top, "WHAT THIS TEST FOUND", ACCENT, BACKGROUND);
    canvas.text(x, top + 1, row.name, TEXT, BACKGROUND);
    let mut y = canvas.wrapped(x, top + 2, width, row.about, MUTED) + 1;
    let (label, shade) = status_label(row.status);
    let label = if row.status == Status::Waiting { "Not run" } else { label };
    let end = canvas.text(x, y, label, shade, BACKGROUND);
    if !row.detail.is_empty() {
        canvas.text(end + 2, y, &row.detail, MUTED, BACKGROUND);
    }
    y += 2;
    if !row.result.is_empty() {
        canvas.wrapped(x, y, width, &row.result, TEXT);
    }
}

fn header_status(login: &str, console: &str) -> String {
    if login.is_empty() {
        console.to_owned()
    } else {
        format!("{login}   ·   {console}")
    }
}

fn footer(canvas: &mut Canvas, left: u16, end: u16, y: u16, snapshot: &Snapshot) {
    let hints: &[(&str, &str)] = match &snapshot.screen {
        Screen::Starting => &[],
        Screen::Running => &[("Circle", "Stop after this test")],
        Screen::Asking(_) => &[],
        Screen::Download(_) => &[("Circle", "Back")],
        Screen::Finished(_) => &[("Up / Down", "Choose"), ("Cross", "Select")],
        Screen::Review { .. } => &[("Up / Down", "Choose"), ("Circle", "Back")],
        Screen::Choosing { .. } => &[
            ("Up / Down", "Choose"),
            ("Cross", "Run this test"),
            ("Options", "Run everything"),
            ("Circle", "Finish"),
        ],
    };
    let mut x = left;
    for (key, label) in hints {
        x = key_hint(canvas, (x, y), key, BACKGROUND, snapshot.pad, false) + 1;
        x = canvas.text(x, y, label, MUTED, BACKGROUND) + 4;
    }
    canvas.right(end, y, &snapshot.usb, MUTED, BACKGROUND);
}
