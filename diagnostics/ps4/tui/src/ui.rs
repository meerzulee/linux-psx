// SPDX-License-Identifier: MIT
#[cfg(test)]
use crate::hardware;
use crate::hardware::Row;
use crate::model::{self, CATEGORIES, Category};
use crate::{App, Focus, FormKind, SshScreen};
use ratatui::Frame;
use ratatui::layout::{Alignment, Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear, List, ListItem, ListState, Paragraph, Tabs, Wrap};
use serde_json::Value;

#[derive(Clone, Copy)]
struct Theme {
    background: Color,
    panel: Color,
    line: Color,
    muted: Color,
    selection: Color,
    text: Color,
}
impl Theme {
    fn current(state: &Value) -> Self {
        let term = std::env::var("TERM").unwrap_or_default();
        let colorterm = std::env::var("COLORTERM").unwrap_or_default();
        let fallback = Self::for_terminal(&term, &colorterm);
        if term == "linux" {
            return fallback;
        }
        let color = |name: &str, fallback: Color| {
            let Some([r, g, b]) = palette_rgb(state, name) else {
                return fallback;
            };
            if colorterm == "truecolor" || colorterm == "24bit" {
                Color::Rgb(r, g, b)
            } else {
                indexed_color(r, g, b)
            }
        };
        Self {
            background: color("background", fallback.background),
            panel: color("panel", fallback.panel),
            line: color("accent", fallback.line),
            muted: color("muted", fallback.muted),
            selection: color("selection", fallback.selection),
            text: color("foreground", fallback.text),
        }
    }
    fn for_terminal(term: &str, colorterm: &str) -> Self {
        if term == "linux" {
            // prepare-console programs these six VT palette slots for olive.
            Self {
                background: Color::Black,
                panel: Color::Green,
                line: Color::LightGreen,
                muted: Color::Gray,
                selection: Color::Yellow,
                text: Color::White,
            }
        } else if colorterm == "truecolor" || colorterm == "24bit" {
            Self {
                background: Color::Rgb(32, 39, 27),
                panel: Color::Rgb(46, 58, 37),
                line: Color::Rgb(163, 181, 106),
                muted: Color::Rgb(187, 197, 161),
                selection: Color::Rgb(77, 96, 54),
                text: Color::Rgb(238, 241, 229),
            }
        } else {
            // Xterm's closest dark neutrals with an olive selection and accents.
            Self {
                background: Color::Indexed(235),
                panel: Color::Indexed(236),
                line: Color::Indexed(143),
                muted: Color::Indexed(151),
                selection: Color::Indexed(58),
                text: Color::Indexed(255),
            }
        }
    }
    fn base(self) -> Style {
        Style::default().fg(self.text).bg(self.background)
    }
    fn selected(self) -> Style {
        Style::default()
            .bg(self.selection)
            .fg(self.text)
            .add_modifier(Modifier::BOLD)
    }
}

fn palette_rgb(state: &Value, name: &str) -> Option<[u8; 3]> {
    let value = state
        .pointer(&format!("/settings/palette/{name}"))?
        .as_str()?
        .strip_prefix('#')?;
    if value.len() != 6 || !value.is_ascii() {
        return None;
    }
    Some([
        u8::from_str_radix(&value[0..2], 16).ok()?,
        u8::from_str_radix(&value[2..4], 16).ok()?,
        u8::from_str_radix(&value[4..6], 16).ok()?,
    ])
}

fn indexed_color(r: u8, g: u8, b: u8) -> Color {
    let mut best = (u32::MAX, 0u8);
    for index in 16u16..=255 {
        let rgb = if index < 232 {
            let n = index - 16;
            let levels = [0i32, 95, 135, 175, 215, 255];
            [
                levels[(n / 36) as usize],
                levels[((n / 6) % 6) as usize],
                levels[(n % 6) as usize],
            ]
        } else {
            let gray = 8 + 10 * (index as i32 - 232);
            [gray; 3]
        };
        let distance =
            (rgb[0] - r as i32).pow(2) + (rgb[1] - g as i32).pow(2) + (rgb[2] - b as i32).pow(2);
        if (distance as u32) < best.0 {
            best = (distance as u32, index as u8);
        }
    }
    Color::Indexed(best.1)
}

pub fn console_palette(state: &Value) -> String {
    let mut output = String::new();
    for (slot, name, fallback) in [
        (0, "background", [32, 39, 27]),
        (2, "panel", [46, 58, 37]),
        (3, "selection", [77, 96, 54]),
        (7, "muted", [187, 197, 161]),
        (10, "accent", [163, 181, 106]),
        (15, "foreground", [238, 241, 229]),
    ] {
        let [r, g, b] = palette_rgb(state, name).unwrap_or(fallback);
        output.push_str(&format!("\x1b]P{slot:x}{r:02x}{g:02x}{b:02x}"));
    }
    output
}

fn centered(area: Rect, width: u16, height: u16) -> Rect {
    let width = width.min(area.width);
    let height = height.min(area.height);
    Rect::new(
        area.x + (area.width - width) / 2,
        area.y + (area.height - height) / 2,
        width,
        height,
    )
}

fn panel(title: &str, t: Theme) -> Block<'_> {
    Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(t.line))
        .title(format!(" {title} "))
        .style(Style::default().bg(t.panel).fg(t.text))
}

fn line_pair(label: &str, value: impl Into<String>, t: Theme) -> Line<'static> {
    Line::from(vec![
        Span::styled(format!("{label:<13}"), Style::default().fg(t.muted)),
        Span::raw(value.into()),
    ])
}

fn unknown(s: String) -> String {
    if s.is_empty() { "—".into() } else { s }
}
fn count(state: &Value, path: &str) -> usize {
    model::array(state, path).len()
}

fn memory(state: &Value) -> String {
    let total = state
        .pointer("/system/memory/total_bytes")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let available = state
        .pointer("/system/memory/available_bytes")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    if total > 0.0 {
        format!(
            "{:.1} / {:.1} GiB free",
            available / 1073741824.0,
            total / 1073741824.0
        )
    } else {
        "Not read yet".into()
    }
}

fn summary(app: &App, t: Theme) -> Vec<Line<'static>> {
    let s = &app.state;
    let category = app.category();
    let mut lines = vec![
        Line::styled(category.description(), Style::default().fg(t.muted)),
        Line::default(),
    ];
    match category {
        Category::Network => {
            let ip = model::ip(s);
            let ready = s
                .pointer("/ssh/enabled")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            lines.push(line_pair(
                "Connection",
                if ip.is_empty() {
                    "Waiting for an address".into()
                } else {
                    ip.clone()
                },
                t,
            ));
            for interface in model::array(s, "/network/interfaces").iter().take(4) {
                lines.push(line_pair(
                    &model::at(interface, "/name"),
                    unknown(model::at(interface, "/state")),
                    t,
                ));
            }
            lines.push(Line::default());
            let wifi = s
                .pointer("/network/wifi/available")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            lines.push(line_pair(
                "Wi-Fi",
                if wifi {
                    "Adapter detected"
                } else {
                    "No adapter detected"
                },
                t,
            ));
            lines.push(line_pair(
                "SSH",
                if ready && !ip.is_empty() {
                    "Ready · your key"
                } else if ready {
                    "Enabled · connect network"
                } else {
                    "Not ready"
                },
                t,
            ));
            if ready && !ip.is_empty() {
                let address = ip.split('/').next().unwrap_or(&ip);
                let port = model::at(s, "/ssh/port");
                let user = unknown(model::at(s, "/ssh/login"));
                lines.push(Line::default());
                lines.push(Line::from(if port.is_empty() || port == "22" {
                    format!("ssh {user}@{address}")
                } else {
                    format!("ssh -p {port} {user}@{address}")
                }));
            }
            let key = model::at(s, "/ssh/host_key_fingerprint");
            if !key.is_empty() {
                lines.push(Line::default());
                lines.push(Line::styled("Host key", Style::default().fg(t.muted)));
                lines.push(Line::from(key));
            }
        }
        Category::System | Category::Cpu => {
            let mut cpu = model::at(s, "/system/cpu/model");
            if cpu.is_empty() {
                cpu = model::array(s, "/inventory/cpu/models")
                    .iter()
                    .map(model::text)
                    .collect::<Vec<_>>()
                    .join(", ");
            }
            lines.push(line_pair("Processor", unknown(cpu), t));
            let cores = model::at(s, "/system/cpu/logical");
            lines.push(line_pair(
                "Logical CPUs",
                if cores.is_empty() {
                    unknown(model::at(s, "/inventory/cpu/listed_processors"))
                } else {
                    cores
                },
                t,
            ));
            lines.push(line_pair("Memory", memory(s), t));
            lines.push(line_pair(
                "Kernel",
                unknown(model::at(s, "/system/kernel_release")),
                t,
            ));
            lines.push(Line::default());
            lines.push(line_pair(
                "PCI / USB",
                format!(
                    "{} / {} devices",
                    count(s, "/inventory/pci"),
                    count(s, "/inventory/usb")
                ),
                t,
            ));
            for sensor in model::array(s, "/inventory/thermal").iter().take(2) {
                let value = model::at(sensor, "/temperature_millicelsius")
                    .parse::<f64>()
                    .ok();
                if let Some(value) = value {
                    lines.push(line_pair(
                        &model::at(sensor, "/type"),
                        format!("{:.1} °C", value / 1000.0),
                        t,
                    ));
                }
            }
            if category == Category::Cpu {
                lines.push(Line::default());
                lines.push(Line::styled(
                    "Core checks: CPU first, then 32 MiB RAM.",
                    Style::default().fg(t.muted),
                ));
            }
        }
        Category::Graphics => {
            lines.push(line_pair(
                "DRM nodes",
                count(s, "/inventory/drm_nodes").to_string(),
                t,
            ));
            for node in model::array(s, "/inventory/drm_nodes").iter().take(4) {
                lines.push(Line::from(format!("  {}", model::text(node))));
            }
            lines.push(Line::default());
            lines.push(Line::from("API information is an observation."));
            lines.push(Line::from("Rendered tests open on TTY3."));
            lines.push(Line::from("Exit the test app to return here."));
        }
        Category::Browser => {
            lines.push(Line::from("A browser opens on the graphical screen."));
            lines.push(Line::from("Network access uses your connection."));
            lines.push(Line::default());
            lines.push(Line::from("Automatic runs save results, then close."));
            lines.push(Line::from("Manual workspaces stay open until you exit."));
            lines.push(Line::default());
            lines.push(Line::from("Browser rendering may use software."));
            lines.push(Line::from("Identify the renderer when testing GPU APIs."));
        }
        Category::Audio => {
            lines.push(line_pair("Test output", "44.1 kHz", t));
            lines.push(Line::default());
            lines.push(Line::from("Use your display or audio receiver."));
            lines.push(Line::from("The tone test asks what you heard."));
            lines.push(Line::default());
            lines.push(Line::styled(
                "Native 48 kHz remains deferred.",
                Style::default().fg(t.muted),
            ));
        }
        Category::Storage => {
            let devices = model::array(s, "/inventory/storage/devices");
            lines.push(line_pair(
                "Detected",
                format!("{} block devices", devices.len()),
                t,
            ));
            for device in devices.iter().take(6) {
                let name = model::at(device, "/name");
                let model = model::at(device, "/model");
                let size = model::at(device, "/sectors_512b")
                    .parse::<u64>()
                    .unwrap_or(0);
                lines.push(Line::from(format!(
                    "{name:<8} {:>6.1} GiB  {model}",
                    size as f64 * 512.0 / 1073741824.0
                )));
            }
            lines.push(Line::default());
            lines.push(Line::styled(
                "Inventory reads device metadata only.",
                Style::default().fg(t.muted),
            ));
        }
        Category::Reports => {
            let reports = model::array(s, "/results");
            lines.push(line_pair("Cached reports", reports.len().to_string(), t));
            for (group, name) in model::REPORT_GROUPS.iter().enumerate() {
                let count = reports
                    .iter()
                    .filter(|r| model::report_group(r) == group)
                    .count();
                if count > 0 {
                    lines.push(line_pair(name, count.to_string(), t));
                }
            }
            lines.push(Line::default());
            lines.push(Line::from("Enter / Right expands a group; Left collapses."));
            lines.push(Line::from("Reports are newest first within each group."));
            lines.push(Line::from("Select a report to view its details."));
            lines.push(Line::from("Results remain on the diagnostic USB."));
            lines.push(Line::default());
            lines.push(Line::styled(
                "Observed hardware is not a functional pass.",
                Style::default().fg(t.muted),
            ));
        }
        Category::Settings => {
            lines.push(line_pair(
                "Theme",
                unknown(model::at(s, "/settings/theme_name")),
                t,
            ));
            lines.push(line_pair("Font family", "JetBrainsMono Nerd Font", t));
            lines.push(line_pair(
                "Font size",
                format!(
                    "{} pt (10–24)",
                    s.pointer("/settings/font_size")
                        .and_then(Value::as_u64)
                        .unwrap_or(16)
                ),
                t,
            ));
            lines.push(Line::default());
            lines.push(Line::from("Saved on the diagnostic USB."));
            lines.push(Line::from(model::at(s, "/settings/live/message")));
            let error = model::at(s, "/settings/error");
            if !error.is_empty() {
                lines.push(Line::from(error));
            }
            lines.push(Line::default());
            lines.push(Line::from("Native console uses its bitmap fallback; font size changes affect the graphical terminal."));
            lines.push(line_pair("Creator", "meerzulee", t));
        }
    }
    lines
}

pub fn draw(frame: &mut Frame, app: &App) {
    let t = Theme::current(&app.state);
    let area = frame.area();
    frame.render_widget(Block::default().style(t.base()), area);
    if area.height > 0 {
        frame.render_widget(
            Paragraph::new("made by meerzulee")
                .alignment(Alignment::Center)
                .style(Style::default().fg(t.muted)),
            Rect::new(area.x, area.y + area.height - 1, area.width, 1),
        );
    }
    if area.width < 80 || area.height < 24 {
        let text = format!(
            "psX Diagnostics\n\nUse a terminal at least 80 x 24.\nCurrent: {} x {}\n\nQ exits to the console.",
            area.width, area.height
        );
        frame.render_widget(
            Paragraph::new(text)
                .alignment(Alignment::Center)
                .style(t.base()),
            centered(area, area.width, 8),
        );
        return;
    }
    let window = centered(
        area,
        area.width.saturating_sub(4).min(140),
        area.height.saturating_sub(2).min(44),
    );
    let border = Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(t.line))
        .style(t.base());
    let inner = border.inner(window);
    frame.render_widget(border, window);
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(2),
            Constraint::Length(2),
            Constraint::Length(if app.onboarding { 0 } else { 3 }),
            Constraint::Min(8),
            Constraint::Length(3),
            Constraint::Length(2),
        ])
        .split(inner);
    let title = if app.demo {
        "psX  /  DIAGNOSTICS  ·  DEMO PREVIEW"
    } else {
        "psX  /  DIAGNOSTICS"
    };
    frame.render_widget(
        Paragraph::new(title)
            .alignment(Alignment::Center)
            .style(Style::default().fg(t.text).add_modifier(Modifier::BOLD)),
        rows[0],
    );
    let ip = model::ip(&app.state);
    let ssh = app
        .state
        .pointer("/ssh/enabled")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let label = if !app.connected {
        "Controller connecting"
    } else if ip.is_empty() {
        "Network not connected"
    } else {
        "Connected"
    };
    let status = format!(
        "{label}   |   IP {}   |   SSH {}",
        if ip.is_empty() { "—" } else { &ip },
        if ssh && !ip.is_empty() {
            "ready"
        } else if ssh {
            "enabled"
        } else {
            "waiting"
        }
    );
    frame.render_widget(
        Paragraph::new(status)
            .alignment(Alignment::Center)
            .style(Style::default().fg(t.muted)),
        rows[1],
    );
    if !app.onboarding {
        let titles = CATEGORIES.iter().map(|c| {
            Line::from(if inner.width < 86 {
                c.compact_title()
            } else {
                c.title()
            })
        });
        let highlight = if app.focus == Focus::Categories {
            t.selected()
        } else {
            Style::default().fg(t.text).add_modifier(Modifier::BOLD)
        };
        let tabs =
            Tabs::new(titles)
                .select(app.category)
                .divider("  ")
                .padding("", "")
                .style(Style::default().fg(t.muted))
                .highlight_style(highlight)
                .block(Block::default().borders(Borders::BOTTOM).border_style(
                    Style::default().fg(if app.focus == Focus::Categories {
                        t.text
                    } else {
                        t.line
                    }),
                ));
        frame.render_widget(tabs, rows[2]);
    }
    let columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(43), Constraint::Percentage(57)])
        .split(rows[3]);
    let actions = app.actions();
    let title = if app.onboarding {
        "Connect your console"
    } else {
        app.category().title()
    };
    let list_items: Vec<_> = actions
        .iter()
        .enumerate()
        .map(|(index, a)| {
            if let crate::model::ActionKind::Report(value) = &a.kind {
                let last_child = !matches!(
                    actions.get(index + 1).map(|item| &item.kind),
                    Some(crate::model::ActionKind::Report(_))
                );
                let branch = if last_child { "└─" } else { "├─" };
                ListItem::new(vec![
                    Line::from(format!("   {branch} {}", a.title)),
                    Line::styled(
                        format!(
                            "      {} · {}",
                            model::status_label(&model::at(value, "/status")),
                            model::report_boot_label(&app.state, value)
                        ),
                        Style::default().fg(t.muted),
                    ),
                    Line::styled(
                        format!(
                            "      {}",
                            model::report_time(value)
                                .replace('T', " ")
                                .trim_end_matches('Z')
                        ),
                        Style::default().fg(t.muted),
                    ),
                ])
            } else {
                ListItem::new(format!(" {}", a.title))
            }
        })
        .collect();
    let actions_focused = app.focus == Focus::Actions || app.onboarding;
    let highlight = if actions_focused {
        t.selected()
    } else {
        Style::default().fg(t.muted).add_modifier(Modifier::BOLD)
    };
    let list = List::new(list_items)
        .block(panel(title, t))
        .style(Style::default().fg(t.text))
        .highlight_style(highlight)
        .highlight_symbol(if actions_focused { "› " } else { "  " });
    let mut list_state = ListState::default().with_selected(Some(app.selected));
    if app.category() == Category::Settings {
        let maximum = actions
            .len()
            .saturating_sub(usize::from(columns[0].height.saturating_sub(2)));
        list_state = list_state.with_offset(app.settings_offset.get().min(maximum));
    }
    frame.render_stateful_widget(list, columns[0], &mut list_state);
    if app.category() == Category::Settings {
        app.settings_offset.set(list_state.offset());
    }
    let right_rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(5), Constraint::Length(4)])
        .split(columns[1]);
    frame.render_widget(
        Paragraph::new(summary(app, t))
            .block(panel(
                if app.onboarding {
                    "Connection & SSH"
                } else {
                    "Overview"
                },
                t,
            ))
            .wrap(Wrap { trim: false }),
        right_rows[0],
    );
    let hint = actions
        .get(app.selected)
        .map(|a| a.detail.as_str())
        .unwrap_or("");
    frame.render_widget(
        Paragraph::new(hint)
            .style(Style::default().fg(t.muted))
            .block(panel("Selected action", t))
            .wrap(Wrap { trim: true }),
        right_rows[1],
    );
    let activity = if let Some(job) = &app.active_job {
        let spinner = ["|", "/", "-", "\\"][(app.ticks / 2) as usize % 4];
        let message = model::at(&job.data, "/message");
        format!(
            "{spinner}  {} · {}s{}",
            job.title,
            job.started.elapsed().as_secs(),
            if message.is_empty() {
                String::new()
            } else {
                format!(" · {message}")
            }
        )
    } else if app.action_pending {
        format!("Starting {}…", app.action_title)
    } else if !app.error.is_empty() {
        format!("{}  ·  F5 refresh", app.error)
    } else {
        app.notice.clone()
    };
    let color = if !app.error.is_empty() {
        t.line
    } else {
        t.muted
    };
    frame.render_widget(
        Paragraph::new(activity)
            .style(Style::default().fg(color))
            .wrap(Wrap { trim: true })
            .block(
                Block::default()
                    .borders(Borders::TOP)
                    .border_style(Style::default().fg(t.line)),
            ),
        rows[4],
    );
    let keys = if app.onboarding {
        "↑↓ Choose   Enter Select   Esc Continue offline   F1 Help   Q Exit"
    } else if app.category() == Category::Reports && app.focus == Focus::Actions {
        "↑↓ Select   Enter Open/toggle   ← Collapse   → Expand   Tab Areas   F4 Refresh   Q Exit"
    } else if app.settings_tree_focused() {
        "↑↓ Select   Enter Open/apply   ← Collapse   → Expand\nTab Areas   F2 Network   F3 Core   F4 Reports   V Details   Q Exit"
    } else {
        "←→ Area   ↑↓ Action   Enter Run   F2 Network   F3 Core   F4 Reports   V Details   Q Exit"
    };
    frame.render_widget(
        Paragraph::new(keys)
            .alignment(Alignment::Center)
            .style(Style::default().fg(t.muted))
            .wrap(Wrap { trim: true }),
        rows[5],
    );
    if let Some(value) = &app.report {
        report(frame, app, value, t);
    }
    if app.hardware.is_some() {
        hardware_view(frame, app, t);
    }
    if app.help {
        help(frame, t);
    }
    if app.exit_dialog {
        modal(
            frame,
            "Exit diagnostics?",
            vec![
                Line::from("Return to the local console."),
                Line::default(),
                Line::from("Enter: exit    Esc: return"),
            ],
            64,
            9,
            t,
        );
    }
    if app.ssh_dialog.is_some() {
        ssh_setup(frame, app, t);
    }
    if app.form.is_some() {
        form(frame, app, t);
    }
}

fn hardware_area(area: Rect) -> Rect {
    centered(
        area,
        area.width.saturating_sub(4).min(120),
        area.height.saturating_sub(2).min(38),
    )
}
fn hardware_body(area: Rect) -> Rect {
    let inner = Rect::new(
        area.x + 1,
        area.y + 1,
        area.width.saturating_sub(2),
        area.height.saturating_sub(2),
    );
    Rect::new(
        inner.x,
        inner.y + 3,
        inner.width,
        inner.height.saturating_sub(5),
    )
}

// Hard wrapping is used only for explicit Advanced data; readable fields wrap on words.
fn hardware_wrap(text: &str, width: u16, words: bool) -> Vec<String> {
    let width = usize::from(width.max(1));
    if !words {
        let mut result = Vec::new();
        let mut line = String::new();
        for character in text.chars() {
            if Line::raw(format!("{line}{character}")).width() > width && !line.is_empty() {
                result.push(std::mem::take(&mut line));
            }
            line.push(character);
        }
        result.push(line);
        return result;
    }
    let mut result = Vec::new();
    let mut line = String::new();
    for word in text.split_whitespace() {
        let candidate = if line.is_empty() {
            word.to_owned()
        } else {
            format!("{line} {word}")
        };
        if Line::raw(candidate.clone()).width() <= width {
            line = candidate;
            continue;
        }
        if !line.is_empty() {
            result.push(std::mem::take(&mut line));
        }
        let mut parts = hardware_wrap(word, width as u16, false);
        line = parts.pop().unwrap_or_default();
        result.extend(parts);
    }
    result.push(line);
    result
}
fn hardware_rows(rows: &[Row], width: u16, t: Theme) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    let label_width = 17_u16.min(width.saturating_sub(12));
    for row in rows {
        match row {
            Row::Heading(value) => {
                if !lines.is_empty() {
                    lines.push(Line::default());
                }
                for line in hardware_wrap(value, width, true) {
                    lines.push(Line::styled(
                        line,
                        Style::default().fg(t.line).add_modifier(Modifier::BOLD),
                    ));
                }
            }
            Row::Note(value) => {
                for line in hardware_wrap(value, width, true) {
                    lines.push(Line::styled(line, Style::default().fg(t.muted)));
                }
            }
            Row::Field(label, value) => {
                if Line::raw(label.clone()).width() >= usize::from(label_width) {
                    for line in hardware_wrap(label, width, true) {
                        lines.push(Line::styled(line, Style::default().fg(t.muted)));
                    }
                    for line in hardware_wrap(value, width, true) {
                        lines.push(Line::from(line));
                    }
                } else {
                    for (index, line) in
                        hardware_wrap(value, width.saturating_sub(label_width), true)
                            .into_iter()
                            .enumerate()
                    {
                        let prefix = if index == 0 {
                            format!("{label:<padding$}", padding = usize::from(label_width))
                        } else {
                            " ".repeat(usize::from(label_width))
                        };
                        lines.push(Line::from(vec![
                            Span::styled(prefix, Style::default().fg(t.muted)),
                            Span::raw(line),
                        ]));
                    }
                }
            }
        }
    }
    lines
}
fn hardware_lines(dialog: &crate::HardwareDialog, width: u16, t: Theme) -> Vec<Line<'static>> {
    if let Some(raw) = &dialog.raw {
        raw.lines()
            .flat_map(|line| hardware_wrap(line, width, false))
            .map(Line::from)
            .collect()
    } else {
        hardware_rows(&dialog.pages[dialog.selected].rows, width, t)
    }
}
pub fn hardware_scroll_limit(app: &App) -> u16 {
    let Some(dialog) = &app.hardware else {
        return 0;
    };
    if !dialog.detail {
        return 0;
    }
    let body = hardware_body(hardware_area(Rect::new(
        0,
        0,
        app.viewport.0,
        app.viewport.1,
    )));
    hardware_lines(dialog, body.width, Theme::current(&app.state))
        .len()
        .saturating_sub(usize::from(body.height))
        .min(usize::from(u16::MAX)) as u16
}
fn hardware_view(frame: &mut Frame, app: &App, t: Theme) {
    let Some(dialog) = &app.hardware else {
        return;
    };
    let page = &dialog.pages[dialog.selected];
    let area = hardware_area(frame.area());
    frame.render_widget(Clear, area);
    let block = panel("Hardware", t);
    let inner = block.inner(area);
    frame.render_widget(block, area);
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(1),
            Constraint::Length(2),
        ])
        .split(inner);
    let title = if dialog.raw.is_some() {
        format!("Hardware / {} / Advanced", page.title)
    } else if dialog.detail {
        format!("Hardware / {}", page.title)
    } else {
        "Hardware overview".into()
    };
    frame.render_widget(
        Paragraph::new(vec![
            Line::styled(
                title,
                Style::default().fg(t.text).add_modifier(Modifier::BOLD),
            ),
            Line::styled("Cached hardware metadata", Style::default().fg(t.muted)),
        ]),
        rows[0],
    );
    if !dialog.detail {
        let columns = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([
                Constraint::Length(21),
                Constraint::Length(2),
                Constraint::Min(1),
            ])
            .split(rows[1]);
        let items = dialog
            .pages
            .iter()
            .map(|page| ListItem::new(page.title))
            .collect::<Vec<_>>();
        frame.render_widget(
            Block::default()
                .borders(Borders::LEFT)
                .border_style(Style::default().fg(t.line)),
            columns[1],
        );
        let mut selected = ListState::default().with_selected(Some(dialog.selected));
        frame.render_stateful_widget(
            List::new(items)
                .highlight_style(t.selected())
                .highlight_symbol("› "),
            columns[0],
            &mut selected,
        );
        let mut preview = vec![Line::styled(
            page.title,
            Style::default().fg(t.line).add_modifier(Modifier::BOLD),
        )];
        preview.extend(
            hardware_wrap(&page.summary, columns[2].width, true)
                .into_iter()
                .map(|line| Line::styled(line, Style::default().fg(t.muted))),
        );
        preview.push(Line::default());
        preview.extend(hardware_rows(&page.rows, columns[2].width, t));
        frame.render_widget(Paragraph::new(preview), columns[2]);
        frame.render_widget(
            Paragraph::new(vec![
                Line::from("↑↓ Component   Enter Details   A Advanced   Esc Close"),
                Line::styled(
                    "Snapshot stays fixed while this view is open.",
                    Style::default().fg(t.muted),
                ),
            ]),
            rows[2],
        );
    } else {
        let lines = hardware_lines(dialog, rows[1].width, t);
        let total = lines.len();
        let limit = total
            .saturating_sub(usize::from(rows[1].height))
            .min(usize::from(u16::MAX)) as u16;
        let scroll = dialog.scroll.min(limit);
        frame.render_widget(Paragraph::new(lines).scroll((scroll, 0)), rows[1]);
        let hint = if dialog.raw.is_some() {
            "↑↓ Scroll   PgUp/PgDn   Home/End   A Readable   Esc Back"
        } else {
            "↑↓ Scroll   PgUp/PgDn   Home/End   A Advanced   Esc Back"
        };
        frame.render_widget(
            Paragraph::new(vec![
                Line::from(hint),
                Line::styled(
                    format!(
                        "Lines {}–{} of {}",
                        usize::from(scroll) + 1,
                        (usize::from(scroll) + usize::from(rows[1].height)).min(total),
                        total
                    ),
                    Style::default().fg(t.muted),
                ),
            ]),
            rows[2],
        );
    }
}

fn modal(frame: &mut Frame, title: &str, lines: Vec<Line<'_>>, width: u16, height: u16, t: Theme) {
    let area = centered(frame.area(), width, height);
    frame.render_widget(Clear, area);
    frame.render_widget(
        Paragraph::new(lines)
            .block(panel(title, t))
            .wrap(Wrap { trim: false })
            .alignment(Alignment::Center),
        area,
    );
}

fn report(frame: &mut Frame, app: &App, value: &Value, t: Theme) {
    if value["view"] == "creator_credits" {
        modal(
            frame,
            "Creator credits",
            vec![
                Line::styled(
                    "meerzulee",
                    Style::default().fg(t.text).add_modifier(Modifier::BOLD),
                ),
                Line::default(),
                Line::from("https://github.com/meerzulee"),
                Line::from("https://github.com/meerzulee/linux-psx"),
                Line::default(),
                Line::from("Enter or Esc to return to Settings"),
            ],
            70,
            10,
            t,
        );
        return;
    }
    let area = centered(
        frame.area(),
        frame.area().width.saturating_sub(10).min(128),
        frame.area().height.saturating_sub(4).min(40),
    );
    frame.render_widget(Clear, area);
    let title = if model::at(value, "/test_id").is_empty() {
        "Details · ↑↓ scroll · Esc return"
    } else {
        "Test report · ↑↓ scroll · Esc return"
    };
    let text = serde_json::to_string_pretty(value).unwrap_or_else(|_| "No readable report".into());
    frame.render_widget(
        Paragraph::new(text)
            .block(panel(title, t))
            .wrap(Wrap { trim: false })
            .scroll((app.report_scroll, 0)),
        area,
    );
}

fn ssh_setup(frame: &mut Frame, app: &App, t: Theme) {
    let Some(dialog) = app.ssh_dialog.as_ref() else {
        return;
    };
    let address = model::ip(&app.state)
        .split('/')
        .next()
        .unwrap_or("")
        .to_owned();
    let target = if address.is_empty() {
        "<console-ip>".into()
    } else {
        address.clone()
    };
    let hostkey = model::at(&app.state, "/ssh/host_key_fingerprint");
    let area = centered(
        frame.area(),
        frame.area().width.saturating_sub(4).min(96),
        frame.area().height.saturating_sub(2).min(27),
    );
    frame.render_widget(Clear, area);
    let block = panel("SSH setup", t);
    let inner = block.inner(area);
    frame.render_widget(block, area);
    if dialog.screen == SshScreen::Password {
        let remaining = dialog
            .expires
            .map(|time| {
                time.saturating_duration_since(std::time::Instant::now())
                    .as_secs()
            })
            .unwrap_or(0);
        let mut lines = vec![
            Line::from(format!(
                "Login: root    IP: {}",
                if address.is_empty() {
                    "Connect a network first"
                } else {
                    &address
                }
            )),
            Line::from(format!(
                "Host key: {}",
                if hostkey.is_empty() {
                    "Not available yet"
                } else {
                    &hostkey
                }
            )),
            Line::default(),
            Line::from(format!(
                "Temporary password · {:02}:{:02} remaining",
                remaining / 60,
                remaining % 60
            )),
        ];
        if let Some(secret) = dialog.secret.as_ref() {
            lines.push(Line::styled(
                secret.expose(),
                Style::default().fg(t.text).add_modifier(Modifier::BOLD),
            ));
        }
        lines.extend([
            Line::default(),
            Line::from("On your computer, copy your public key:"),
            Line::from(format!(
                "ssh-copy-id -i ~/.ssh/id_ed25519.pub root@{target}"
            )),
            Line::default(),
            Line::from(format!(
                "Then connect: ssh -i ~/.ssh/id_ed25519 root@{target}"
            )),
            Line::default(),
            Line::from("Shown once. Enter / Esc closes and clears this screen."),
        ]);
        frame.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), inner);
        return;
    }
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(5),
            Constraint::Min(3),
            Constraint::Length(3),
            Constraint::Length(2),
        ])
        .split(inner);
    frame.render_widget(
        Paragraph::new(vec![
            Line::from(format!(
                "IP: {}    Login: root",
                if address.is_empty() {
                    "Connect a network first"
                } else {
                    &address
                }
            )),
            Line::from(format!(
                "Host key: {}",
                if hostkey.is_empty() {
                    "Not available yet"
                } else {
                    &hostkey
                }
            )),
            Line::from(format!("ssh -i ~/.ssh/id_ed25519 root@{target}")),
        ])
        .wrap(Wrap { trim: false }),
        rows[0],
    );
    let (details, choices) = match dialog.screen {
        SshScreen::Menu if dialog.info["setup_available"].as_bool() == Some(false) => (
            vec![Line::from(model::at(&dialog.info, "/message"))],
            vec!["Close"],
        ),
        SshScreen::Menu => (
            vec![
                Line::from(
                    "Import public keys from your GitHub account, or enable a password for 10 minutes to install your key.",
                ),
                Line::from("The existing owner key stays authorized."),
                Line::default(),
                Line::from(format!(
                    "Authorized keys: {}",
                    model::array(&dialog.info, "/key_fingerprints").len()
                )),
            ],
            vec![
                "GitHub public keys",
                if dialog.info["password_enabled"].as_bool().unwrap_or(false) {
                    "Disable temporary password"
                } else {
                    "Temporary password"
                },
                "Close",
            ],
        ),
        SshScreen::PasswordConfirm => (
            vec![
                Line::from("Enable a generated password for 10 minutes?"),
                Line::from(
                    "Use ssh-copy-id to install your public key. The password will be shown once in this private screen.",
                ),
            ],
            vec!["Cancel", "Enable temporary password"],
        ),
        SshScreen::Preview => {
            let mut lines = vec![
                Line::from(format!(
                    "GitHub: {} · verify these public-key fingerprints",
                    model::at(&dialog.info, "/username")
                )),
                Line::default(),
            ];
            lines.extend(
                model::array(&dialog.info, "/fingerprints")
                    .iter()
                    .map(|value| Line::from(model::text(value))),
            );
            (lines, vec!["Cancel", "Add these keys"])
        }
        SshScreen::Password => unreachable!(),
    };
    frame.render_widget(
        Paragraph::new(details)
            .wrap(Wrap { trim: false })
            .scroll((dialog.scroll, 0)),
        rows[1],
    );
    let mut selected = ListState::default().with_selected(Some(dialog.selected));
    frame.render_stateful_widget(
        List::new(choices)
            .highlight_style(t.selected())
            .highlight_symbol("› "),
        rows[2],
        &mut selected,
    );
    let hint = if app.ssh_pending {
        "Working…"
    } else if !dialog.error.is_empty() {
        &dialog.error
    } else {
        "↑↓ Choose   Enter Select   PageUp/PageDown Scroll   Esc Close"
    };
    frame.render_widget(
        Paragraph::new(hint)
            .style(Style::default().fg(t.muted))
            .wrap(Wrap { trim: false }),
        rows[3],
    );
}

fn help(frame: &mut Frame, t: Theme) {
    modal(
        frame,
        "Controls",
        vec![
            Line::from("Left / Right       Choose a diagnostic area"),
            Line::from("Up / Down          Choose an action"),
            Line::from("Enter              Run the selected action once"),
            Line::from("Tab                Move between areas and actions"),
            Line::from("Esc                Back / leave a form"),
            Line::default(),
            Line::from("F2 Network    F3 Core checks    F4 Reports"),
            Line::from("F5 Refresh    F6 Settings    V Last report    Q Exit"),
            Line::from("Ctrl+L              Clean screen and repaint"),
            Line::default(),
            Line::from("Tests keep running while you browse this interface."),
            Line::from("Graphical apps open on TTY3; exit them to return."),
            Line::from("On console: Ctrl+Alt+F12 opens system logs."),
            Line::from("On console: Ctrl+Alt+F2 opens the rescue shell."),
            Line::from("SSH uses the owner key configured for this image."),
            Line::default(),
            Line::from("Enter or Esc to return"),
        ],
        70,
        21,
        t,
    );
}

fn form(frame: &mut Frame, app: &App, t: Theme) {
    let Some(form) = &app.form else {
        return;
    };
    let (title, labels): (&str, Vec<&str>) = match &form.kind {
        FormKind::Wifi => (
            "Join Wi-Fi",
            vec![
                "Network name (SSID)",
                "Password (leave empty for an open network)",
            ],
        ),
        FormKind::Https => ("HTTPS connection", vec!["HTTPS URL", "Interface"]),
        FormKind::SshGithub => ("GitHub public keys", vec!["GitHub username"]),
        FormKind::Retry { .. } => (
            "Before trying again",
            vec!["What changed since the failed attempt?"],
        ),
        FormKind::Observation { .. } => ("Audio observation", vec![]),
    };
    let area = centered(
        frame.area(),
        72,
        if labels.is_empty() {
            12
        } else {
            (labels.len() as u16 * 4 + 7).min(21)
        },
    );
    frame.render_widget(Clear, area);
    let block = panel(title, t);
    let inner = block.inner(area);
    frame.render_widget(block, area);
    if matches!(form.kind, FormKind::Observation { .. }) {
        let items: Vec<_> = [
            "Yes · correct pitch and duration",
            "No · missing or incorrect sound",
            "Unsure",
        ]
        .iter()
        .map(|s| ListItem::new(*s))
        .collect();
        let layout = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(3),
                Constraint::Length(4),
                Constraint::Min(1),
            ])
            .split(inner);
        frame.render_widget(
            Paragraph::new("Was the test tone audible with the correct pitch and duration?")
                .alignment(Alignment::Center)
                .wrap(Wrap { trim: true }),
            layout[0],
        );
        let mut state = ListState::default().with_selected(Some(form.selected));
        frame.render_stateful_widget(
            List::new(items)
                .highlight_style(t.selected())
                .highlight_symbol("› "),
            layout[1],
            &mut state,
        );
        frame.render_widget(
            Paragraph::new("Enter Save    Esc Return")
                .alignment(Alignment::Center)
                .style(Style::default().fg(t.muted)),
            layout[2],
        );
        return;
    }
    let mut constraints = vec![Constraint::Length(4); labels.len()];
    constraints.push(Constraint::Min(3));
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints(constraints)
        .split(inner);
    for (index, label) in labels.iter().enumerate() {
        let hidden = matches!(form.kind, FormKind::Wifi) && index == 1;
        let mut value = if hidden {
            "*".repeat(form.fields[index].chars().count())
        } else {
            form.fields[index].clone()
        };
        let visible = rows[index].width.saturating_sub(3) as usize;
        if value.chars().count() > visible {
            value = value
                .chars()
                .rev()
                .take(visible)
                .collect::<String>()
                .chars()
                .rev()
                .collect();
        }
        let border = if form.selected == index {
            t.text
        } else {
            t.line
        };
        let position = value.chars().count().min(visible) as u16;
        frame.render_widget(
            Paragraph::new(value)
                .block(
                    Block::default()
                        .title(format!(" {label} "))
                        .borders(Borders::ALL)
                        .border_style(Style::default().fg(border)),
                )
                .style(t.base()),
            rows[index],
        );
        if form.selected == index {
            frame.set_cursor_position((rows[index].x + 1 + position, rows[index].y + 1));
        }
    }
    let text = if form.error.is_empty() {
        "Tab Next field   Enter Connect / Run   Esc Cancel"
    } else {
        &form.error
    };
    let color = if form.error.is_empty() {
        t.muted
    } else {
        t.line
    };
    let button = match form.kind {
        FormKind::Wifi => "  Connect  ",
        FormKind::Https => "  Run check  ",
        FormKind::SshGithub => "  Preview keys  ",
        _ => "  Run once  ",
    };
    let button_style = if form.selected == labels.len() {
        t.selected()
    } else {
        Style::default().fg(t.text).add_modifier(Modifier::BOLD)
    };
    frame.render_widget(
        Paragraph::new(vec![
            Line::from(Span::styled(button, button_style)),
            Line::from(Span::styled(text, Style::default().fg(color))),
        ])
        .alignment(Alignment::Center)
        .wrap(Wrap { trim: true }),
        rows[labels.len()],
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::{Terminal, backend::TestBackend};
    #[test]
    fn onboarding_renders_at_supported_minimum_and_larger_sizes() {
        for (width, height) in [(80, 24), (110, 32), (160, 50)] {
            let app = App::new(std::path::PathBuf::from("/unreachable"), true);
            let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
            terminal.draw(|frame| draw(frame, &app)).unwrap();
            let text: String = terminal
                .backend()
                .buffer()
                .content
                .iter()
                .map(|cell| cell.symbol())
                .collect();
            assert!(text.contains("Connect your console"));
            assert!(text.contains("DEMO PREVIEW"));
            assert!(text.contains("Continue offline"));
            assert!(!text.contains("192.0.2.7"));
        }
    }
    #[test]
    fn unsupported_small_window_gives_a_readable_message() {
        let app = App::new(std::path::PathBuf::from("/unreachable"), true);
        let mut terminal = Terminal::new(TestBackend::new(50, 18)).unwrap();
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        assert!(text.contains("80 x 24"));
    }
    #[test]
    fn all_categories_remain_visible_on_the_smallest_dashboard() {
        let mut app = App::new(std::path::PathBuf::from("/unreachable"), true);
        app.onboarding = false;
        app.category = 0;
        let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        for category in CATEGORIES {
            assert!(
                text.contains(category.compact_title()),
                "missing category {}",
                category.title()
            );
        }
    }
    #[test]
    fn clearing_previous_terminal_contents_produces_a_complete_fresh_screen() {
        let app = App::new(std::path::PathBuf::from("/unreachable"), true);
        let mut terminal = Terminal::new(TestBackend::new(120, 33)).unwrap();
        terminal
            .draw(|frame| {
                frame.render_widget(Paragraph::new("STALE-BOOT-LOG\n".repeat(33)), frame.area());
            })
            .unwrap();
        terminal.resize(terminal.size().unwrap().into()).unwrap();
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        assert!(!text.contains("STALE-BOOT-LOG"));
        assert!(text.contains("Connect your console"));
        assert!(text.contains("Continue offline"));
    }
    #[test]
    fn report_tree_renders_nested_children_and_boot_context_at_minimum_size() {
        let mut app = App::new(std::path::PathBuf::from("/unreachable"), true);
        app.onboarding = false;
        app.category = 7;
        app.report_expanded[0] = true;
        app.state["identity"] = serde_json::json!({"boot_id":"current"});
        app.state["results"] = serde_json::json!([
            {"run_id":"new","test_id":"cpu.compute","status":"PASS","boot_id":"current","started_at":"2026-11-04T12:34:56Z"},
            {"run_id":"old","test_id":"cpu.compute","status":"FAIL","boot_id":"earlier","started_at":"2026-11-03T12:34:56Z"}
        ]);
        let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        assert!(text.contains("[-] CPU (2)"));
        assert!(text.contains("├─ Computation"));
        assert!(text.contains("This boot"));
        assert!(text.contains("2026-11-04 12:34:56"));
        assert!(!text.contains("CPU · Computation"));
        app.selected = 3;
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        assert!(text.contains("└─ Computation"));
        assert!(text.contains("Earlier boot"));
        assert!(text.contains("2026-11-03 12:34:56"));
    }
    #[test]
    fn settings_credits_show_public_links_and_font_controls_remain_visible() {
        use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
        let mut app = App::new(std::path::PathBuf::from("/unreachable"), true);
        app.key(KeyEvent::new(KeyCode::F(6), KeyModifiers::NONE));
        let mut terminal = Terminal::new(TestBackend::new(110, 32)).unwrap();
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        assert!(text.contains("JetBrainsMono Nerd Font"));
        assert!(text.contains("[+] Theme"));
        assert!(text.contains("[+] Font"));
        assert!(text.contains("[+] About"));
        app.settings_expanded = [false, true, true];
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        assert!(text.contains("Smaller font"));
        assert!(text.contains("Larger font"));
        assert!(text.contains("Creator credits"));
        app.selected = app
            .actions()
            .iter()
            .position(|item| matches!(item.kind, model::ActionKind::Credits))
            .unwrap();
        app.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        assert!(text.contains("https://github.com/meerzulee"));
        assert!(text.contains("https://github.com/meerzulee/linux-psx"));
        assert!(app.active_job.is_none());
        app.key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
        assert!(app.report.is_none());
        assert!(app.category() == Category::Settings);
    }
    #[test]
    fn private_ssh_password_and_exact_client_commands_clear_after_close() {
        use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
        let mut app = App::new(std::path::PathBuf::from("/unreachable"), true);
        app.state["network"]["interfaces"][0]["ipv4"] = serde_json::json!(["192.0.2.7/24"]);
        app.ssh_dialog = Some(crate::SshDialog::menu());
        app.receive(crate::ipc::Reply {
            action: "ssh.password.enable".into(),
            result: Ok(serde_json::json!({"password":"private-fixture","expires_in_seconds":600})),
        });
        let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        assert!(text.contains("private-fixture"));
        assert!(text.contains("ssh-copy-id -i ~/.ssh/id_ed25519.pub root@192.0.2.7"));
        assert!(!text.contains("root@192.0.2.7/24"));
        let footer: String = terminal
            .backend()
            .buffer()
            .content
            .chunks(80)
            .last()
            .unwrap()
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        assert!(footer.contains("made by meerzulee"));
        app.key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        assert!(!text.contains("private-fixture"));
        assert!(app.last_report.is_none());
    }
    #[test]
    fn hardware_overview_and_memory_details_are_readable_at_minimum_and_tv_sizes() {
        use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
        for (width, height) in [(80, 24), (110, 32), (160, 50)] {
            let mut app = App::new(std::path::PathBuf::from("/unreachable"), true);
            app.viewport = (width, height);
            app.hardware = Some(crate::HardwareDialog::new(
                serde_json::json!({"memory":{"MemTotal_kib":8388608,"MemAvailable_kib":2097152,"SwapTotal_kib":0}}),
                hardware::Section::Memory,
            ));
            let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
            terminal.draw(|frame| draw(frame, &app)).unwrap();
            let text: String = terminal
                .backend()
                .buffer()
                .content
                .iter()
                .map(|cell| cell.symbol())
                .collect();
            for expected in [
                "Hardware overview",
                "PCI devices",
                "Input devices",
                "Temperatures",
                "Enter Details",
                "Esc Close",
            ] {
                assert!(
                    text.contains(expected),
                    "missing {expected} at {width}x{height}"
                );
            }
            assert!(!text.contains("MemTotal_kib"));
            app.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
            terminal.draw(|frame| draw(frame, &app)).unwrap();
            let text: String = terminal
                .backend()
                .buffer()
                .content
                .iter()
                .map(|cell| cell.symbol())
                .collect();
            assert!(text.contains("Hardware / Memory"));
            assert!(text.contains("Total RAM"));
            assert!(text.contains("8.0 GiB"));
            assert!(text.contains("Available RAM"));
            assert!(text.contains("2.0 GiB"));
            assert!(text.contains("Esc Back"));
            assert!(!text.contains("MemTotal_kib"));
            app.key(KeyEvent::new(KeyCode::Char('a'), KeyModifiers::NONE));
            terminal.draw(|frame| draw(frame, &app)).unwrap();
            let text: String = terminal
                .backend()
                .buffer()
                .content
                .iter()
                .map(|cell| cell.symbol())
                .collect();
            assert!(text.contains("Advanced"));
            assert!(text.contains("MemTotal_kib"));
            app.key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
            assert!(app.hardware.as_ref().unwrap().detail);
        }
    }
    #[test]
    fn hardware_long_storage_details_reach_the_last_device_without_overwriting_footer() {
        use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
        let mut app = App::new(std::path::PathBuf::from("/unreachable"), true);
        let devices=(0..8).map(|i|serde_json::json!({"name":format!("sda{i}"),"model":if i==7{"Final disk"}else{"Example disk"},"sectors_512b":"33554432","read_only":"0","rotational":"0","device_node_present":true})).collect::<Vec<_>>();
        app.hardware = Some(crate::HardwareDialog::new(
            serde_json::json!({"storage":{"devices":devices,"mountinfo":"private structured mount metadata"}}),
            hardware::Section::Storage,
        ));
        app.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        app.key(KeyEvent::new(KeyCode::End, KeyModifiers::NONE));
        let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        assert!(text.contains("Final disk"));
        assert!(text.contains("16.0 GiB"));
        assert!(text.contains("Esc Back"));
        assert!(text.contains("Read-only flag"));
        assert!(!text.contains("sectors_512b"));
        assert!(!text.contains("private structured"));
        let footer: String = terminal
            .backend()
            .buffer()
            .content
            .chunks(80)
            .last()
            .unwrap()
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        assert!(footer.contains("made by meerzulee"));
        for line in hardware_wrap(
            "a long /sys/devices/0123456789/long-path Ünicode string",
            8,
            true,
        ) {
            assert!(Line::raw(line).width() <= 8);
        }
    }
    #[test]
    fn settings_tree_scroll_survives_refresh_and_collapsing_restores_other_groups_at_80_columns() {
        use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
        let mut app = App::new(std::path::PathBuf::from("/unreachable"), true);
        app.key(KeyEvent::new(KeyCode::F(6), KeyModifiers::NONE));
        app.state["settings"] = serde_json::json!({"theme":"palette0","font_size":16,
            "available_themes":(0..12).map(|i|serde_json::json!({"id":format!("palette{i}"),"name":format!("Palette {i}")})).collect::<Vec<_>>()});
        let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
        app.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        app.selected = 12;
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let offset = app.settings_offset.get();
        assert!(offset > 0);
        let mut state = app.state.clone();
        state["settings"]["theme"] = serde_json::json!("palette11");
        app.receive(crate::ipc::Reply {
            action: "state".into(),
            result: Ok(state),
        });
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        assert_eq!(app.selected, 12);
        assert_eq!(app.settings_offset.get(), offset);
        assert!(app.settings_expanded[0]);
        app.key(KeyEvent::new(KeyCode::Left, KeyModifiers::NONE));
        terminal.draw(|frame| draw(frame, &app)).unwrap();
        let text: String = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect();
        for expected in [
            "[+] Theme",
            "Palette 11",
            "[+] Font",
            "[+] About",
            "SSH setup",
            "made by meerzulee",
            "F2 Network",
            "Collapse",
            "Expand",
            "Tab Areas",
        ] {
            assert!(text.contains(expected), "missing {expected}");
        }
        assert_eq!(app.selected, 0);
        assert_eq!(app.settings_offset.get(), 0);
    }
}
