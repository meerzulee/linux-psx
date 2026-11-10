// SPDX-License-Identifier: MIT
//! DiagnostiX run screen. Normally started with `--socket`; `--demo` shows sample scenes.
mod demo;
mod input;
#[path = "../../ipc.rs"]
#[allow(dead_code)]
mod ipc;
mod live;
mod model;
mod qr;
mod report;
mod serve;
mod trace;
mod usb;
mod view;

use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{self, EnterAlternateScreen, LeaveAlternateScreen};
use input::{Button, Press};
use model::{Screen, Snapshot};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use std::io::{self, Stdout, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

type Screenful = Terminal<CrosstermBackend<Stdout>>;

struct Display {
    console: bool,
    screen: Screenful,
}

fn open_display(icons: bool) -> io::Result<Display> {
    let console = std::env::var("TERM").is_ok_and(|term| term == "linux");
    view::use_true_color(!console);
    view::use_icons(console && icons);
    let mut stdout = io::stdout();
    if console {
        stdout.write_all(view::console_palette().as_bytes())?;
    }
    terminal::enable_raw_mode()?;
    execute!(stdout, EnterAlternateScreen)?;
    let mut screen = Terminal::new(CrosstermBackend::new(stdout))?;
    screen.hide_cursor()?;
    Ok(Display { console, screen })
}

fn close_display(mut display: Display) -> io::Result<()> {
    terminal::disable_raw_mode()?;
    execute!(display.screen.backend_mut(), LeaveAlternateScreen)?;
    display.screen.show_cursor()?;
    if display.console {
        print!("\x1b]R");
        io::stdout().flush()?;
    }
    Ok(())
}

fn poll_event(wait: Duration) -> io::Result<Option<Event>> {
    if event::poll(wait)? { event::read().map(Some) } else { Ok(None) }
}

fn demo(start: &str, mut pad: bool) -> io::Result<()> {
    let Some(mut index) = demo::SCENES.iter().position(|name| *name == start) else {
        eprintln!("unknown scene: {start}");
        std::process::exit(2);
    };
    let mut display = open_display(true)?;
    let outcome = loop {
        let Some(mut snapshot) = demo::scene(demo::SCENES[index]) else {
            break Ok(());
        };
        snapshot.pad = pad;
        if let Err(error) = display.screen.draw(|frame| view::draw(frame, &snapshot)) {
            break Err(error);
        }
        match poll_event(Duration::from_millis(200)) {
            Ok(Some(Event::Key(key))) if key.kind != KeyEventKind::Release => match key.code {
                KeyCode::Char('q') | KeyCode::Esc => break Ok(()),
                KeyCode::Char('p') => pad = !pad,
                KeyCode::Right | KeyCode::Tab => index = (index + 1) % demo::SCENES.len(),
                KeyCode::Left | KeyCode::BackTab => index = (index + demo::SCENES.len() - 1) % demo::SCENES.len(),
                _ => {}
            },
            Ok(_) => {}
            Err(error) => break Err(error),
        }
    };
    close_display(display)?;
    outcome
}

fn keyboard_button(code: KeyCode) -> Option<Button> {
    match code {
        KeyCode::Enter | KeyCode::Char('y') | KeyCode::Char('Y') => Some(Button::Cross),
        KeyCode::Esc | KeyCode::Backspace | KeyCode::Char('n') | KeyCode::Char('N') => Some(Button::Circle),
        KeyCode::Char('s') | KeyCode::Char('S') => Some(Button::Triangle),
        KeyCode::Char('a') | KeyCode::Char('A') => Some(Button::Options),
        KeyCode::Up => Some(Button::Up),
        KeyCode::Down => Some(Button::Down),
        _ => None,
    }
}

fn icon_font_loaded() -> bool {
    std::fs::read("/run/psx-console/presentation.json")
        .ok()
        .and_then(|data| serde_json::from_slice::<serde_json::Value>(&data).ok())
        .is_some_and(|facts| facts["font"].as_str() == Some("diagnostix-icons"))
}

fn run_live(options: live::Options) -> io::Result<()> {
    let previous_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let thread = thread::current().name().unwrap_or("unnamed").to_owned();
        trace::line(&format!("PANIC in {thread}: {info}"));
        previous_hook(info);
    }));
    trace::line("starting");
    let shared = Arc::new(Mutex::new(model::blank(options.manual)));
    let (sender, presses) = mpsc::channel::<Press>();
    let pads = Arc::new(AtomicUsize::new(0));
    input::watch(sender.clone(), pads.clone());
    let (engine_state, engine_pads) = (shared.clone(), pads.clone());
    thread::Builder::new().name("engine".into()).spawn(move || live::run(engine_state, presses, engine_pads, options))?;

    let mut display = open_display(icon_font_loaded())?;
    let mut started = Instant::now();
    let mut alive = Instant::now();
    let outcome = loop {
        if alive.elapsed() >= Duration::from_secs(30) {
            alive = Instant::now();
            trace::line(&format!("screen alive at {} s", started.elapsed().as_secs()));
        }
        let frame_result = {
            let mut snapshot: std::sync::MutexGuard<Snapshot> = shared.lock().unwrap();
            snapshot.pad = pads.load(Ordering::Relaxed) > 0;
            if snapshot.restart_clock {
                snapshot.restart_clock = false;
                started = Instant::now();
            }
            if matches!(snapshot.screen, Screen::Starting | Screen::Running | Screen::Asking(_) | Screen::Choosing { .. }) {
                snapshot.elapsed = started.elapsed().as_secs() as u32;
            }
            display.screen.draw(|frame| view::draw(frame, &snapshot))
        };
        if let Err(error) = frame_result {
            break Err(error);
        }
        match poll_event(Duration::from_millis(200)) {
            Ok(Some(Event::Key(key))) if key.kind != KeyEventKind::Release => {
                // Developers only: there is nothing to quit to on the console.
                if key.code == KeyCode::Char('q') && key.modifiers.contains(KeyModifiers::CONTROL) {
                    break Ok(());
                }
                if let Some(button) = keyboard_button(key.code) {
                    let _ = sender.send(Press { button });
                }
            }
            Ok(Some(Event::Resize(..))) => {
                let _ = display.screen.clear();
            }
            Ok(_) => {}
            Err(error) => break Err(error),
        }
    };
    trace::line(&format!("screen closing: {outcome:?}"));
    close_display(display)?;
    outcome
}

fn value_after(arguments: &[String], flag: &str) -> Option<String> {
    arguments.iter().position(|item| item == flag).and_then(|index| arguments.get(index + 1)).cloned()
}

fn usage() -> ! {
    eprintln!("usage: diagnostix --socket PATH [--manual] | --demo [{}] [--pad]", demo::SCENES.join("|"));
    std::process::exit(2);
}

fn main() -> io::Result<()> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    match arguments.first().map(String::as_str) {
        Some("--palette") => {
            println!("{}", view::PALETTE.join(","));
            Ok(())
        }
        Some("--demo") => {
            let pad = arguments.iter().any(|item| item == "--pad");
            let scene = arguments.iter().skip(1).find(|item| *item != "--pad").map_or("basics", String::as_str);
            demo(scene, pad)
        }
        _ => {
            let Some(socket) = value_after(&arguments, "--socket") else {
                usage();
            };
            let command_line = std::fs::read_to_string("/proc/cmdline").unwrap_or_default();
            run_live(live::Options {
                socket: PathBuf::from(socket),
                manual: arguments.iter().any(|item| item == "--manual") || live::requested_manual(&command_line),
                ram_root: value_after(&arguments, "--ram-root").map_or_else(|| PathBuf::from("/run/psx-state/diagnostix"), PathBuf::from),
                usb_dir: value_after(&arguments, "--usb-dir").map(PathBuf::from),
                console: live::requested_console(&command_line),
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::backend::TestBackend;
    use ratatui::style::Color;

    /// Every glyph used besides ASCII must exist in latarcyrheb-sun32 or be
    /// one that prepare-console adds to it.
    const CONSOLE_FONT_EXTRAS: &str = "─│·›█░°▀▄▌▐↑↓\u{E0F0}\u{E0F1}\u{E0F2}\u{E0F3}\u{E0F4}\u{E0F5}\u{E0F6}\u{E0F7}";

    fn render_snapshot(snapshot: &model::Snapshot, width: u16, height: u16) -> ratatui::buffer::Buffer {
        let mut screen = Terminal::new(TestBackend::new(width, height)).unwrap();
        screen.draw(|frame| view::draw(frame, snapshot)).unwrap();
        screen.backend().buffer().clone()
    }

    fn render(scene: &str, width: u16, height: u16) -> ratatui::buffer::Buffer {
        render_snapshot(&demo::scene(scene).unwrap(), width, height)
    }

    fn text(buffer: &ratatui::buffer::Buffer) -> String {
        buffer.content().iter().map(|cell| cell.symbol()).collect()
    }

    #[test]
    fn every_scene_only_uses_glyphs_and_backgrounds_the_console_has() {
        view::use_icons(true);
        for scene in demo::SCENES {
            for (width, height, pad) in [(120, 33, false), (120, 33, true), (160, 45, true), (100, 30, false)] {
                let mut snapshot = demo::scene(scene).unwrap();
                snapshot.pad = pad;
                for cell in render_snapshot(&snapshot, width, height).content() {
                    for glyph in cell.symbol().chars() {
                        assert!(
                            glyph.is_ascii_graphic() || glyph == ' ' || CONSOLE_FONT_EXTRAS.contains(glyph),
                            "{scene}: {glyph:?} is not in the console font"
                        );
                    }
                    match cell.bg {
                        Color::Indexed(slot) => assert!(slot < 8, "{scene}: background slot {slot}"),
                        Color::Reset => {}
                        other => panic!("{scene}: unexpected background {other:?}"),
                    }
                }
            }
        }
    }

    #[test]
    fn the_wordmark_fills_the_console_until_the_service_answers() {
        view::use_icons(true);
        let page = text(&render("starting", 120, 33));
        assert!(page.contains("███▌") && page.contains("Waiting for the diagnostic service"), "{page}");
        let narrow = text(&render("starting", 100, 30));
        assert!(narrow.contains("DiagnostiX") && !narrow.contains('█'), "{narrow}");
    }

    #[test]
    fn the_stock_font_rounds_half_blocks_up_to_cells_it_has() {
        for glyph in ['▌', '▐'] {
            assert_eq!(view::console_block(glyph, false), '█');
            assert_eq!(view::console_block(glyph, true), glyph);
        }
        assert_eq!(view::console_block('▄', false), '▄');
    }

    fn has_button_icons(page: &str) -> bool {
        ('\u{E0F0}'..='\u{E0F7}').any(|glyph| page.contains(glyph))
    }

    #[test]
    fn without_a_controller_hints_name_keyboard_keys() {
        view::use_icons(true);
        let page = text(&render("ask-sound", 120, 33));
        assert!(!has_button_icons(&page));
        assert!(page.contains("Y  Yes") && page.contains("N  No") && page.contains("S  Skip"), "{page}");
        assert!(!page.contains("On a keyboard"), "the buttons already name the keys");
        for scene in demo::SCENES {
            assert!(!has_button_icons(&text(&render(scene, 120, 33))), "{scene}");
        }
        assert!(text(&render("basics", 120, 33)).contains("Esc Stop after this test"));
        assert!(text(&render("finished", 120, 33)).contains("Enter Select"));
    }

    #[test]
    fn with_a_controller_hints_show_its_buttons() {
        view::use_icons(true);
        let mut snapshot = demo::scene("ask-sound").unwrap();
        snapshot.pad = true;
        let page = text(&render_snapshot(&snapshot, 120, 33));
        assert!(has_button_icons(&page));
        assert!(page.contains("On a keyboard"));
    }

    #[test]
    fn the_whole_plan_fits_the_standard_console_grid() {
        let page = text(&render("finished", 120, 33));
        for row in demo::scene("finished").unwrap().rows() {
            assert!(page.contains(row.name), "{} is off screen", row.name);
        }
        assert!(page.contains("Turn off"));
    }

    #[test]
    fn detection_is_not_counted_as_a_pass() {
        let mut snapshot = demo::scene("finished").unwrap();
        let passes = snapshot.count(model::Status::Pass);
        let row = snapshot.parts[0].rows.iter_mut().find(|row| row.status == model::Status::Pass).unwrap();
        row.status = model::Status::Found;
        let page = text(&render_snapshot(&snapshot, 120, 33));
        assert!(page.contains(&format!("{} passed", passes - 1)));
    }

    #[test]
    fn a_narrow_window_says_so_instead_of_drawing_broken_columns() {
        assert!(text(&render("basics", 80, 24)).contains("too small"));
    }
}
