// SPDX-License-Identifier: MIT
//! What the run screen shows, independent of where the values come from.

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Status {
    Waiting,
    Running,
    Pass,
    Fail,
    /// The hardware was detected. Detection alone is never a pass.
    Found,
    NotFound,
    Skipped,
    Blocked,
}

pub struct Row {
    pub name: &'static str,
    pub detail: String,
    pub status: Status,
    pub about: &'static str,
    /// What the test found, in words, for the report and the review screen.
    pub result: String,
}

pub struct Part {
    pub title: &'static str,
    pub rows: Vec<Row>,
}

pub struct Problem {
    pub name: String,
    pub why: String,
}

pub struct Question {
    pub step: usize,
    pub total: usize,
    pub title: String,
    pub body: Vec<String>,
    /// Download address, when the question offers one.
    pub link: Option<String>,
    /// Button and what it answers, in display order.
    pub choices: Vec<(&'static str, &'static str)>,
    /// Reminder of the keyboard keys, for when a controller is shown instead.
    pub keyboard: &'static str,
    pub skips_in: u32,
}

pub struct Finish {
    pub report: Option<String>,
    pub menu: Vec<&'static str>,
    pub cursor: usize,
}

/// Network download of the finished report.
pub struct Download {
    /// `None` while the console has no network connection.
    pub link: Option<String>,
    pub ssh: Option<String>,
    pub fetched: bool,
}

pub enum Screen {
    /// Waiting for the diagnostic service, before the first test.
    Starting,
    Running,
    Asking(Question),
    Finished(Finish),
    Download(Download),
    /// Manual test: `cursor` indexes every row across the parts.
    Choosing {
        cursor: usize,
    },
    /// Looking back over a finished run.
    Review {
        cursor: usize,
    },
}

pub struct Snapshot {
    pub demo: bool,
    pub console: String,
    pub parts: Vec<Part>,
    pub part: usize,
    pub done: usize,
    pub total: usize,
    pub elapsed: u32,
    /// Set when a new run begins, so its time is counted from zero.
    pub restart_clock: bool,
    pub note: Option<String>,
    pub problems: Vec<Problem>,
    pub log: Vec<String>,
    pub usb: String,
    /// Shown only on builds that carry an owner key. Never written to a report.
    pub login: String,
    /// A controller is plugged in. Without one, hints name keyboard keys.
    pub pad: bool,
    pub screen: Screen,
}

impl Snapshot {
    pub fn rows(&self) -> impl Iterator<Item = &Row> {
        self.parts.iter().flat_map(|part| part.rows.iter())
    }

    pub fn count(&self, status: Status) -> usize {
        self.rows().filter(|row| row.status == status).count()
    }

    pub fn running(&self) -> Option<&Row> {
        self.rows().find(|row| row.status == Status::Running)
    }

    /// Everything back to waiting, for another run in the same boot.
    pub fn start_over(&mut self) {
        for row in self.parts.iter_mut().flat_map(|part| part.rows.iter_mut()) {
            row.status = Status::Waiting;
            row.detail.clear();
            row.result.clear();
        }
        self.problems.clear();
        self.part = 0;
        self.done = 0;
        self.note = None;
        self.restart_clock = true;
        self.screen = Screen::Running;
    }
}

fn row(name: &'static str, about: &'static str) -> Row {
    Row {
        name,
        detail: String::new(),
        status: Status::Waiting,
        about,
        result: String::new(),
    }
}

/// Only ever run by choosing it, until Vulkan has been proven on the hardware.
pub const PICK_ONLY: &str = "Vulkan benchmark";

/// Every test in the order a run does them.
pub fn plan(manual: bool) -> Vec<Part> {
    let mut last = vec![row("3D benchmark", "Runs glmark2 and records its score. It can freeze the screen, so your report is saved first.")];
    if manual {
        last.push(row(PICK_ONLY, "Runs vkmark and records its score. It only runs when you pick it, because it can freeze the screen."));
    }
    last.push(row(
        "Video decoding",
        "Decodes a short video in hardware and checks every frame. It runs last because it ends this session.",
    ));
    vec![
        Part {
            title: "Basics",
            rows: vec![
                row("Boot", "Checks that Linux started and how long it took."),
                row("Processor", "Repeats a known calculation and checks the answer."),
                row("Memory", "Writes test patterns to memory and reads them back."),
                row("Internal storage", "Checks that the internal drive is detected. It never writes."),
                row("USB", "Writes a file to the USB stick and reads it back."),
                row("Temperature", "Reads the temperature sensors."),
                row("Audio device", "Looks for the HDMI audio device."),
            ],
        },
        Part {
            title: "Graphics",
            rows: vec![
                row("Graphics driver", "Checks that the graphics driver started without errors."),
                row("OpenGL", "Draws a known image with OpenGL and compares every pixel."),
                row("Vulkan", "Draws a known image with Vulkan and compares every pixel."),
                row("Browser", "Opens a local test page that runs WebGL, video and JavaScript."),
            ],
        },
        Part {
            title: "Your turn",
            rows: vec![
                row("Sound", "Plays a tone and asks if you can hear it."),
                row("Controller", "Checks whether a controller is connected."),
                row("Ethernet", "Connects over the cable. It only asks if no cable is plugged in."),
                row("Wi-Fi", "Scans for nearby networks."),
                row("Bluetooth", "Checks that the Bluetooth radio is detected."),
            ],
        },
        Part {
            title: "Last",
            rows: last,
        },
    ]
}

pub fn blank(manual: bool) -> Snapshot {
    let parts = plan(manual);
    let total = parts.iter().map(|part| part.rows.len()).sum();
    Snapshot {
        demo: false,
        console: String::new(),
        parts,
        part: 0,
        done: 0,
        total,
        elapsed: 0,
        restart_clock: false,
        note: None,
        problems: Vec::new(),
        log: Vec::new(),
        usb: String::new(),
        login: String::new(),
        pad: false,
        screen: Screen::Running,
    }
}

pub fn clock(seconds: u32) -> String {
    format!("{:02}:{:02}", seconds / 60, seconds % 60)
}
