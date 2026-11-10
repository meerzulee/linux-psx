// SPDX-License-Identifier: MIT
//! The real run: asks the diagnostic controller to do each test in order and
//! keeps the screen and the report up to date.
//!
//! The controller stays in charge of what is safe. Once it asks for a stop,
//! every later test is shown as blocked rather than attempted.
use crate::input::{Button, Press};
use crate::ipc;
use crate::model::{self, Download, Finish, Problem, Question, Screen, Snapshot, Status, clock};
use crate::report::{self, Report};
use crate::serve::{self, Server};
use crate::trace;
use crate::usb::{self, Stick};
use serde_json::{Value, json};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub type Shared = Arc<Mutex<Snapshot>>;

pub struct Options {
    pub socket: PathBuf,
    pub manual: bool,
    pub ram_root: PathBuf,
    /// An already mounted folder standing in for a stick. Development only.
    pub usb_dir: Option<PathBuf>,
}

/// What this build's loader insists on, so reaching Linux implies both.
const SOUTHBRIDGE: &str = "Baikal B1";
const FIRMWARE: &str = "12.02";
const ANSWER_SECONDS: u32 = 60;
const BEFORE_LAST_SECONDS: u32 = 120;
/// Longer than the controller's own deadlines, which should always answer first.
const GLMARK_SECONDS: u64 = 480;
const VKMARK_SECONDS: u64 = 300;
/// How long a build with an owner key waits for a cable before starting the tests.
const CABLE_SECONDS: u32 = 20;
/// A press already on its way when a question appears was not an answer to it.
const ANSWER_GUARD: Duration = Duration::from_millis(800);
/// The controller needs a reason on record before it repeats a failed test.
const RUN_AGAIN_REASON: &str = "The operator chose Run tests again on the finish screen.";

#[derive(Clone)]
struct Outcome {
    status: Status,
    detail: String,
    why: String,
    stop: bool,
}

impl Outcome {
    fn new(status: Status, detail: impl Into<String>) -> Outcome {
        Outcome { status, detail: detail.into(), why: String::new(), stop: false }
    }

    fn why(mut self, why: impl Into<String>) -> Outcome {
        self.why = why.into();
        self
    }
}

/// Reads a finished controller test. `observation` tests only collect facts,
/// so an inconclusive result from one means it ran, not that it failed.
fn judge(result: &Value, detail: String, observation: bool) -> Outcome {
    let status = result["status"].as_str().unwrap_or("");
    let message = result["message"].as_str().unwrap_or("The test gave no explanation.");
    let stop = result["stop_required"].as_bool() == Some(true);
    let mut outcome = match status {
        "PASS" | "READY" => Outcome::new(Status::Pass, detail),
        "BLOCKED" => Outcome::new(Status::Blocked, detail).why(message),
        "NOT_RUN" => Outcome::new(Status::Skipped, detail).why(message),
        "INCONCLUSIVE" if observation && !stop => Outcome::new(Status::Found, detail),
        _ => Outcome::new(Status::Fail, detail).why(message),
    };
    outcome.stop = stop;
    outcome
}

/// The one browser in the image, named on its row.
const BROWSER: &str = "Chromium";

/// The browser steps that can really go wrong. The capability step only lists
/// what the browser claims and always reports INCONCLUSIVE, and animation is a
/// smoothness observation, so neither decides the row.
const BROWSER_CHECKS: [(&str, &str); 3] =
    [("browser.canvas2d", "Canvas"), ("browser.webgl", "WebGL"), ("browser.video", "Video")];

fn browser_step<'a>(batch: &'a Value, id: &str) -> Option<&'a str> {
    let steps = batch["steps"].as_array()?;
    steps.iter().find(|step| step["test_id"].as_str() == Some(id))?["status"].as_str()
}

fn browser_names(batch: &Value, wanted: fn(Option<&str>) -> bool) -> String {
    let names: Vec<&str> = BROWSER_CHECKS.iter()
        .filter(|(id, _)| wanted(browser_step(batch, id)))
        .map(|(_, name)| *name)
        .collect();
    names.join(", ")
}

/// The batch's own status can never be PASS (see `BROWSER_CHECKS`), so it is
/// read together with the steps. A real failure or a stop keeps its meaning.
fn judge_browser(batch: &Value) -> Outcome {
    let mut outcome = judge(batch, BROWSER.into(), false);
    let failed = browser_names(batch, |status| status == Some("FAIL"));
    if !failed.is_empty() {
        outcome.why = format!("Failed: {failed}.");
    }
    if outcome.stop || batch["status"].as_str() != Some("INCONCLUSIVE") {
        return outcome;
    }
    let unconfirmed = browser_names(batch, |status| status != Some("PASS"));
    if unconfirmed.is_empty() {
        return Outcome::new(Status::Pass, BROWSER);
    }
    Outcome::new(Status::Found, BROWSER).why(format!("Not confirmed: {unconfirmed}."))
}

fn benchmark_score(evidence: &Value) -> String {
    let label = if evidence["tool"].as_str() == Some("vkmark") { "VK" } else { "GL" };
    evidence["score"].as_i64().map_or_else(String::new, |value| format!("{label} {value}"))
}

/// Every scene, so a kernel change can be compared against an earlier run.
fn benchmark_report(evidence: &Value) -> String {
    let text = |name: &str| evidence[name].as_str().unwrap_or("unknown").to_owned();
    let number = |name: &str| evidence[name].as_i64().map_or("none".to_owned(), |value| value.to_string());
    let tool = text("tool");
    let mut lines = vec![
        format!("{tool} score: {}", number("score")),
        format!("Renderer: {} {}", text("renderer"), evidence["gl_version"].as_str().unwrap_or("")).trim_end().to_owned(),
        format!("Screen: {}", text("surface")),
        format!("Seconds per scene: {}", number("scene_seconds")),
        format!("Total seconds: {}", number("duration_seconds")),
        format!("Hottest sensor: {} C", number("peak_celsius")),
        format!("GPU fault lines in the kernel log: {}", number("gpu_fault_lines")),
        format!("Exit code: {}", number("exit_code")),
        String::new(),
        format!("{tool} scenes"),
    ];
    for scene in evidence["scenes"].as_array().into_iter().flatten() {
        lines.push(format!("{:>7} FPS  {}", scene["fps"].as_i64().unwrap_or(0), scene["scene"].as_str().unwrap_or("")));
    }
    lines.iter().map(|line| report::clean_line(line)).collect::<Vec<_>>().join("\n") + "\n"
}

fn first_line(path: impl AsRef<Path>) -> Option<String> {
    let text = fs::read_to_string(path).ok()?;
    text.lines().next().map(|line| line.trim().to_owned())
}

fn cores() -> String {
    let text = fs::read_to_string("/proc/cpuinfo").unwrap_or_default();
    format!("{} cores", text.lines().filter(|line| line.starts_with("processor")).count())
}

fn memory() -> String {
    let text = fs::read_to_string("/proc/meminfo").unwrap_or_default();
    let kib = text
        .lines()
        .find(|line| line.starts_with("MemTotal:"))
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|value| value.parse::<f64>().ok())
        .unwrap_or(0.0);
    format!("{:.1} GiB", kib / 1024.0 / 1024.0)
}

fn uptime() -> u32 {
    first_line("/proc/uptime")
        .and_then(|line| line.split_whitespace().next().and_then(|value| value.parse::<f64>().ok()))
        .unwrap_or(0.0) as u32
}

fn internal_drive() -> String {
    for entry in fs::read_dir("/sys/block").into_iter().flatten().flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if !name.starts_with("sd") || usb::on_usb_bus(&entry.path()) {
            continue;
        }
        let sectors: f64 = first_line(entry.path().join("size")).and_then(|size| size.parse().ok()).unwrap_or(0.0);
        let bytes = sectors * 512.0;
        return if bytes >= 1e12 { format!("{:.1} TB", bytes / 1e12) } else { format!("{:.0} GB", bytes / 1e9) };
    }
    String::new()
}

fn temperature() -> Option<i64> {
    let mut hottest = None;
    let mut consider = |path: PathBuf| {
        if let Some(value) = first_line(path).and_then(|text| text.parse::<i64>().ok())
            && (1000..=125_000).contains(&value)
        {
            hottest = hottest.max(Some(value / 1000));
        }
    };
    for entry in fs::read_dir("/sys/class/hwmon").into_iter().flatten().flatten() {
        for sensor in fs::read_dir(entry.path()).into_iter().flatten().flatten() {
            let name = sensor.file_name().to_string_lossy().into_owned();
            if name.starts_with("temp") && name.ends_with("_input") {
                consider(sensor.path());
            }
        }
    }
    for entry in fs::read_dir("/sys/class/thermal").into_iter().flatten().flatten() {
        if entry.file_name().to_string_lossy().starts_with("thermal_zone") {
            consider(entry.path().join("temp"));
        }
    }
    hottest
}

/// Sony's PCI devices are what identifies a console model to a kernel developer.
fn sony_devices() -> String {
    let mut lines = Vec::new();
    for entry in fs::read_dir("/sys/bus/pci/devices").into_iter().flatten().flatten() {
        let read = |name: &str| first_line(entry.path().join(name)).unwrap_or_default();
        if read("vendor") == "0x104d" {
            lines.push(format!(
                "  {} {}:{} subsystem {}",
                entry.file_name().to_string_lossy(),
                read("vendor").trim_start_matches("0x"),
                read("device").trim_start_matches("0x"),
                read("subsystem_device").trim_start_matches("0x"),
            ));
        }
    }
    lines.sort();
    lines.join("\n")
}

pub fn requested_manual(command_line: &str) -> bool {
    command_line.split_whitespace().any(|word| word == "dx.mode=manual")
}

fn report_name() -> String {
    let now = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |time| time.as_secs());
    let stem = SOUTHBRIDGE.to_ascii_lowercase().replace(' ', "-");
    match report::date_from_unix(now) {
        Some(date) => format!("{date}-{stem}"),
        None => format!("run-{stem}"),
    }
}

/// Why a controller action produced no result.
enum Trouble {
    /// The controller would not start it, so nothing was tested.
    Refused(String),
    /// It started and then could not finish.
    Failed(String),
}

impl Trouble {
    fn outcome(self, detail: impl Into<String>) -> Outcome {
        match self {
            Trouble::Refused(why) => Outcome::new(Status::Blocked, detail).why(why),
            Trouble::Failed(why) => Outcome::new(Status::Fail, detail).why(why),
        }
    }
}

enum Asked {
    Answer(Button),
    TimedOut,
    /// The thing being waited for happened by itself.
    Satisfied,
}

struct Ask {
    step: usize,
    total: usize,
    title: String,
    body: Vec<String>,
    link: Option<String>,
    choices: Vec<(&'static str, &'static str, Button)>,
    keyboard: &'static str,
    seconds: u32,
    /// Only a real controller can answer, except for the skip button.
    watch: Option<fn(&mut Engine) -> bool>,
}

struct Engine {
    shared: Shared,
    presses: Receiver<Press>,
    options: Options,
    report: Arc<Mutex<Report>>,
    pads: Arc<AtomicUsize>,
    kernel: Option<report::KernelLog>,
    started_at_uptime: u32,
    stick_problem: bool,
    next_id: u64,
    user_stop: bool,
    /// Why the controller, or a failed foundation test, ended the run.
    stopped: Option<String>,
    /// Why only the current part cannot run.
    gate: Option<String>,
    /// How the one Ethernet attempt this boot gets turned out.
    ethernet_result: Option<Outcome>,
    /// Sent with every test once a run has been repeated.
    retry_reason: &'static str,
    /// The command to log in with, on a build that carries an owner key.
    login: Option<String>,
    last_login_check: Instant,
    identity: Value,
    archive: Option<PathBuf>,
    server: Option<Server>,
}

pub fn run(shared: Shared, presses: Receiver<Press>, pads: Arc<AtomicUsize>, options: Options) {
    let report = Arc::new(Mutex::new(Report::new(&options.ram_root, &report_name())));
    let mut engine = Engine {
        shared,
        presses,
        options,
        report,
        pads,
        kernel: None,
        started_at_uptime: uptime(),
        stick_problem: false,
        next_id: 1,
        user_stop: false,
        stopped: None,
        gate: None,
        ethernet_result: None,
        retry_reason: "",
        login: None,
        last_login_check: Instant::now(),
        identity: Value::Null,
        archive: None,
        server: None,
    };
    engine.main();
}

impl Engine {
    fn main(&mut self) {
        trace::line(&format!("engine start, manual={}", self.options.manual));
        self.attach_stick();
        self.follow_kernel();
        self.wait_for_controller();
        self.write_console_facts();
        self.connect_for_owner();
        loop {
            if self.options.manual {
                self.manual();
            } else {
                self.automatic();
            }
            self.finish();
            self.start_over();
        }
    }

    /// Another run in this boot, which only the finish menu can ask for.
    /// Ethernet keeps its earlier result: the driver gets one attempt per boot.
    fn start_over(&mut self) {
        self.retry_reason = RUN_AGAIN_REASON;
        self.user_stop = false;
        self.gate = None;
        self.with(Snapshot::start_over);
        self.progress("Run tests again chosen");
    }

    fn with<T>(&self, change: impl FnOnce(&mut Snapshot) -> T) -> T {
        change(&mut self.shared.lock().unwrap())
    }

    fn progress(&self, text: &str) {
        trace::line(text);
        let elapsed = self.with(|snapshot| snapshot.elapsed);
        self.report.lock().unwrap().append(report::PROGRESS, &format!("{} {text}\n", clock(elapsed)));
    }

    fn attach_stick(&mut self) {
        let mount = match self.options.usb_dir.clone() {
            Some(directory) => Some(directory),
            None => match usb::mount_stick() {
                Stick::Mounted(path) => Some(path),
                Stick::Unusable => {
                    self.stick_problem = true;
                    None
                }
                Stick::None => None,
            },
        };
        if let Some(mount) = mount {
            self.stick_problem = self.report.lock().unwrap().attach(&mount).is_err();
            trace::line(&format!("stick mounted, usable={}", !self.stick_problem));
        } else {
            trace::line(&format!("no stick mounted, problem={}", self.stick_problem));
        }
        self.show_stick();
    }

    fn show_stick(&self) {
        let (attached, lost) = {
            let report = self.report.lock().unwrap();
            (report.usb_directory().is_some(), report.stick_lost())
        };
        let text = if attached {
            "USB stick: saving report"
        } else if lost {
            "USB stick was removed"
        } else if self.stick_problem {
            "USB stick cannot be used. FAT32 or exFAT only."
        } else {
            "No USB stick"
        };
        self.with(|snapshot| snapshot.usb = text.into());
    }

    fn follow_kernel(&mut self) {
        let shared = self.shared.clone();
        self.kernel = Some(report::follow_kernel_log(self.report.clone(), move |lines| {
            let mut snapshot = shared.lock().unwrap();
            snapshot.log.extend(lines.iter().cloned());
            let excess = snapshot.log.len().saturating_sub(12);
            snapshot.log.drain(..excess);
        }));
    }

    fn call(&mut self, action: &str, params: Value) -> Result<Value, String> {
        let request = ipc::Request { id: self.next_id, action: action.to_owned(), params };
        self.next_id += 1;
        ipc::exchange(&self.options.socket, &request)
    }

    fn wait_for_controller(&mut self) {
        self.with(|snapshot| {
            snapshot.screen = Screen::Starting;
            snapshot.note = Some("Waiting for the diagnostic service to start.".into());
        });
        let deadline = Instant::now() + Duration::from_secs(120);
        let waiting = Instant::now();
        trace::line("waiting for the controller");
        loop {
            match self.call("state", json!({})) {
                Ok(state) => {
                    trace::line(&format!("controller answered after {} s", waiting.elapsed().as_secs()));
                    self.identity = state["identity"].clone();
                    let release = state["system"]["kernel_release"].as_str().unwrap_or("").to_owned();
                    let linux = release.split('-').next().unwrap_or("").to_owned();
                    self.with(|snapshot| {
                        snapshot.console = format!("{SOUTHBRIDGE}  ·  Firmware {FIRMWARE}  ·  Linux {linux}");
                        snapshot.note = None;
                        snapshot.screen = Screen::Running;
                    });
                    return;
                }
                Err(error) if Instant::now() >= deadline => {
                    trace::line(&format!("controller never answered: {error}"));
                    self.stopped = Some("The diagnostic service did not start.".into());
                    self.with(|snapshot| {
                        snapshot.note = None;
                        snapshot.screen = Screen::Running;
                        snapshot.problems.push(Problem { name: "Diagnostic service".into(), why: error });
                    });
                    return;
                }
                Err(_) => thread::sleep(Duration::from_secs(1)),
            }
        }
    }

    fn write_console_facts(&self) {
        let release = self.identity["kernel_release"].as_str().unwrap_or("unknown");
        let mode = if self.options.manual { "manual" } else { "automatic" };
        let body = format!(
            "DiagnostiX report\nSouthbridge: {SOUTHBRIDGE} (required by this build's loader)\nFirmware: {FIRMWARE} (required by this build's loader)\nLinux: {release}\nMode: {mode}\nCPU: {}\nMemory: {}\nSony PCI devices:\n{}\n",
            cores(),
            memory(),
            sony_devices(),
        );
        self.report.lock().unwrap().replace(report::CONSOLE, body.as_bytes());
    }

    /// A build with an owner key connects before any test can hang the console,
    /// so its owner can log in and bring it back. It is still the one attempt
    /// this boot gets: the Ethernet row reports this result instead of trying again.
    fn connect_for_owner(&mut self) {
        let info = self.call("ssh.info", json!({}));
        let keys = info.as_ref().ok().and_then(|info| info["key_fingerprints"].as_array().map(Vec::len)).unwrap_or(0);
        if keys == 0 || self.stopped.is_some() {
            let why = match &info {
                Ok(info) => info["message"].as_str().unwrap_or("no key in this build").to_owned(),
                Err(error) => error.clone(),
            };
            trace::line(&format!("early network skipped, keys={keys}: {why}"));
            return;
        }
        self.with(|snapshot| snapshot.note = Some("Connecting to the network so this console can be reached.".into()));
        let mut cable = self.has_cable();
        for _ in 0..CABLE_SECONDS {
            if cable {
                break;
            }
            thread::sleep(Duration::from_secs(1));
            cable = self.has_cable();
        }
        if cable {
            trace::line("early network: connecting Ethernet");
            let outcome = self.connect_ethernet();
            trace::line(&format!("early network: {} {} {}", status_word(outcome.status), outcome.detail, outcome.why));
        } else {
            trace::line("early network: no cable");
        }
        self.last_login_check = Instant::now() - Duration::from_secs(5);
        self.poll_login();
        self.with(|snapshot| snapshot.note = None);
    }

    /// Waits for a button without losing track of a stop request.
    fn wait(&mut self, duration: Duration) -> Option<Press> {
        self.poll_login();
        match self.presses.recv_timeout(duration) {
            Ok(press) => Some(press),
            Err(RecvTimeoutError::Timeout) => None,
            Err(RecvTimeoutError::Disconnected) => {
                thread::sleep(duration);
                None
            }
        }
    }

    fn drain_presses(&mut self) {
        while self.presses.try_recv().is_ok() {}
    }

    /// Submits a controller job and waits for its result.
    fn job(&mut self, action: &str, params: Value, seconds: u64) -> Result<Value, Trouble> {
        let label = job_label(action, &params);
        trace::line(&format!("job submit: {label}, limit {seconds} s"));
        let started = Instant::now();
        let mut beat = Instant::now();
        let deadline = Instant::now() + Duration::from_secs(seconds);
        let submitted = loop {
            match self.call(action, params.clone()) {
                Ok(submitted) => break submitted,
                Err(error) if error.contains("Another action") && Instant::now() < deadline => {
                    thread::sleep(Duration::from_secs(1));
                }
                Err(error) => {
                    trace::line(&format!("job refused: {label}: {error}"));
                    return Err(Trouble::Refused(error));
                }
            }
        };
        let Some(id) = submitted["job_id"].as_str().map(str::to_owned) else {
            trace::line(&format!("job answered at once: {label}"));
            return Ok(submitted);
        };
        loop {
            if let Some(press) = self.wait(Duration::from_millis(400))
                && press.button == Button::Circle
            {
                self.user_stop = true;
                self.with(|snapshot| snapshot.note = Some("Stopping after this test.".into()));
            }
            let polled = self.call("job.get", json!({"id": id}));
            match &polled {
                Ok(record) => match record["state"].as_str() {
                    Some("completed") => {
                        trace::line(&format!("job done: {label} after {} s", started.elapsed().as_secs()));
                        return Ok(record["result"].clone());
                    }
                    Some("failed") => {
                        trace::line(&format!("job failed: {label} after {} s", started.elapsed().as_secs()));
                        return Err(Trouble::Failed(record["error"].as_str().unwrap_or("The test could not finish.").to_owned()));
                    }
                    _ => {}
                },
                Err(error) if beat.elapsed() >= Duration::from_secs(10) => trace::line(&format!("job poll failed: {label}: {error}")),
                Err(_) => {}
            }
            if beat.elapsed() >= Duration::from_secs(10) {
                beat = Instant::now();
                trace::line(&format!("job waiting: {label}, {} s", started.elapsed().as_secs()));
            }
            if Instant::now() >= deadline {
                trace::line(&format!("job timed out here: {label} after {} s", started.elapsed().as_secs()));
                return Err(Trouble::Failed("The test did not finish in time.".into()));
            }
        }
    }

    fn test(&mut self, id: &str, parameters: Value, seconds: u64) -> Result<Value, Trouble> {
        self.job("test.run", json!({"test_id": id, "parameters": parameters, "retry_reason": self.retry_reason}), seconds)
    }

    fn check(&mut self, id: &str, parameters: Value, seconds: u64, detail: String, observation: bool) -> Outcome {
        match self.test(id, parameters, seconds) {
            Ok(result) => judge(&result, detail, observation),
            Err(trouble) => trouble.outcome(detail),
        }
    }

    fn status_of(&self, name: &str) -> Status {
        self.with(|snapshot| snapshot.rows().find(|row| row.name == name).map_or(Status::Waiting, |row| row.status))
    }

    fn set_row(&self, name: &str, status: Status, detail: &str, result: &str) {
        self.with(|snapshot| {
            if let Some(row) = snapshot.parts.iter_mut().flat_map(|part| part.rows.iter_mut()).find(|row| row.name == name) {
                row.status = status;
                row.detail = detail.to_owned();
                if !result.is_empty() {
                    row.result = result.to_owned();
                }
            }
        });
    }

    /// Runs one row and records the outcome everywhere it needs to go.
    fn perform(&mut self, name: &'static str) {
        let outcome = if self.user_stop {
            Outcome::new(Status::Skipped, "").why("You stopped the run before this test.")
        } else if let Some(reason) = self.stopped.clone().or_else(|| self.gate.clone()) {
            Outcome::new(Status::Blocked, "").why(reason)
        } else {
            self.set_row(name, Status::Running, "", "");
            self.progress(&format!("start {name}"));
            self.report.lock().unwrap().mark("running", name);
            self.step(name)
        };
        self.conclude(name, outcome);
    }

    fn conclude(&mut self, name: &'static str, outcome: Outcome) {
        let label = status_word(outcome.status);
        self.set_row(name, outcome.status, &outcome.detail, &outcome.why);
        let mut line = format!("{label} {name}");
        if !outcome.detail.is_empty() {
            line.push_str(&format!(" ({})", outcome.detail));
        }
        if !outcome.why.is_empty() {
            line.push_str(&format!(": {}", report::clean_line(&outcome.why)));
        }
        self.progress(&line);
        if outcome.status == Status::Fail {
            let why = if outcome.why.is_empty() { "See the report for details.".to_owned() } else { outcome.why.clone() };
            self.with(|snapshot| snapshot.problems.push(Problem { name: name.into(), why }));
        }
        if outcome.stop && self.stopped.is_none() {
            self.stopped = Some(format!("Stopped for safety after {name}. Restart to test this."));
        }
        self.with(|snapshot| {
            snapshot.done += 1;
            if !matches!(snapshot.screen, Screen::Choosing { .. }) {
                snapshot.screen = Screen::Running;
            }
        });
        self.save_summary();
    }

    fn save_summary(&self) {
        let body = self.with(|snapshot| render_summary(snapshot));
        let mut report = self.report.lock().unwrap();
        report.replace(report::SUMMARY, body.as_bytes());
        drop(report);
        self.show_stick();
    }

    fn step(&mut self, name: &str) -> Outcome {
        match name {
            "Boot" => self.boot(),
            "Processor" => {
                let _ = self.test("inventory.readonly", json!({}), 60);
                self.check("cpu.compute", json!({"seconds": 2}), 60, cores(), false)
            }
            "Memory" => self.check("memory.pattern", json!({"memory_mib": 32}), 90, memory(), false),
            "Internal storage" => {
                let drive = internal_drive();
                if drive.is_empty() {
                    return Outcome::new(Status::NotFound, "");
                }
                self.check("storage.inventory", json!({}), 60, drive, true)
            }
            "USB" => self.stick_test(),
            "Temperature" => match temperature() {
                Some(celsius) => Outcome::new(Status::Pass, format!("{celsius} °C")),
                None => Outcome::new(Status::NotFound, ""),
            },
            "Audio device" => self.check("audio.info", json!({}), 60, "HDMI".into(), true),
            "Graphics driver" => self.graphics_driver(),
            "OpenGL" => self.check("gpu.opengl.render", json!({}), 90, String::new(), false),
            "Vulkan" => self.check("gpu.vulkan.render", json!({}), 90, String::new(), false),
            "3D benchmark" => self.benchmark("gpu.benchmark", GLMARK_SECONDS, "The screen will change for about 3 minutes. It comes back by itself."),
            model::PICK_ONLY => self.benchmark("gpu.benchmark.vulkan", VKMARK_SECONDS, "The screen will change for a minute or two. It comes back by itself."),
            "Browser" => self.browser(),
            "Sound" => self.sound(),
            "Controller" => self.controller(),
            "Ethernet" => self.ethernet(),
            "Wi-Fi" => self.wifi(),
            "Bluetooth" => bluetooth(),
            "Video decoding" => self.video_decoding(),
            _ => Outcome::new(Status::Skipped, ""),
        }
    }

    fn boot(&mut self) -> Outcome {
        let deadline = Instant::now() + Duration::from_secs(3);
        while Instant::now() < deadline {
            if self.kernel.as_ref().is_none_or(|kernel| kernel.caught_up.load(Ordering::Relaxed)) {
                break;
            }
            thread::sleep(Duration::from_millis(100));
        }
        let errors = self.kernel.as_ref().map_or(0, |kernel| kernel.errors.load(Ordering::Relaxed));
        let seconds = self.started_at_uptime;
        let plural = if errors == 1 { "" } else { "s" };
        Outcome::new(Status::Pass, format!("{seconds} s")).why(format!("Linux started in {seconds} s with {errors} kernel error{plural} logged."))
    }

    fn stick_test(&mut self) -> Outcome {
        let directory = self.report.lock().unwrap().usb_directory();
        match directory {
            Some(directory) => match usb::write_test(&directory) {
                Ok(speed) => Outcome::new(Status::Pass, format!("{speed} MB/s")),
                Err(error) => Outcome::new(Status::Fail, "").why(error),
            },
            None if self.stick_problem => Outcome::new(Status::Fail, "").why("A stick was found but could not be used. FAT32 or exFAT only."),
            None => Outcome::new(Status::NotFound, ""),
        }
    }

    fn graphics_driver(&mut self) -> Outcome {
        for id in ["gpu.opengl.info", "gpu.vulkan.info"] {
            let outcome = self.check(id, json!({}), 90, String::new(), true);
            if !matches!(outcome.status, Status::Pass | Status::Found) {
                return outcome;
            }
        }
        Outcome::new(Status::Found, "amdgpu")
    }

    fn benchmark(&mut self, id: &str, seconds: u64, note: &str) -> Outcome {
        self.with(|snapshot| snapshot.note = Some(note.into()));
        let outcome = match self.test(id, json!({}), seconds) {
            Ok(result) => {
                let evidence = &result["evidence"];
                if evidence.is_object() {
                    self.report.lock().unwrap().append(report::BENCHMARK, &(benchmark_report(evidence) + "\n"));
                }
                judge(&result, benchmark_score(evidence), false)
            }
            Err(trouble) => trouble.outcome(""),
        };
        self.with(|snapshot| snapshot.note = None);
        outcome
    }

    fn browser(&mut self) -> Outcome {
        self.with(|snapshot| snapshot.note = Some("The screen will change during this test. It comes back by itself.".into()));
        let outcome = self.run_browser();
        self.with(|snapshot| snapshot.note = None);
        outcome
    }

    fn run_browser(&mut self) -> Outcome {
        let before = self.latest_batch().map(|batch| batch["run_id"].clone());
        let launched = match self.job("graphics.launch", json!({"app": "chromium"}), 240) {
            Ok(launched) => launched,
            Err(trouble) => return trouble.outcome(""),
        };
        // Anything else is the controller refusing to start the browser at all.
        if launched["automatic_suite"].as_bool() != Some(true) {
            return judge(&launched, BROWSER.into(), false);
        }
        // The controller closes the browser by itself and has its own deadline.
        let deadline = Instant::now() + Duration::from_secs(150);
        while Instant::now() < deadline {
            if let Some(batch) = self.latest_batch()
                && Some(&batch["run_id"]) != before.as_ref()
                && batch["phase"].as_str() == Some("completed")
            {
                return judge_browser(&batch);
            }
            thread::sleep(Duration::from_secs(2));
        }
        let mut outcome = Outcome::new(Status::Fail, BROWSER).why("The browser test did not report back in time.");
        outcome.stop = true;
        outcome
    }

    fn latest_batch(&mut self) -> Option<Value> {
        let results = self.call("results.list", json!({})).ok()?;
        results.as_array()?.iter().find(|item| item["test_id"].as_str() == Some("browser.batch")).cloned()
    }

    /// Shows a question and waits for one of its buttons, or for time to run out.
    fn ask(&mut self, ask: Ask) -> Asked {
        trace::line(&format!("asking: {}", ask.title));
        self.drain_presses();
        let shown = Instant::now();
        let deadline = Instant::now() + Duration::from_secs(u64::from(ask.seconds));
        let mut last_watch = Instant::now();
        loop {
            let left = deadline.saturating_duration_since(Instant::now()).as_secs() as u32;
            let question = Question {
                step: ask.step,
                total: ask.total,
                title: ask.title.clone(),
                body: ask.body.clone(),
                link: ask.link.clone(),
                choices: ask
                    .choices
                    .iter()
                    .map(|(key, answer, _)| (*key, *answer))
                    .collect(),
                keyboard: ask.keyboard,
                skips_in: left,
            };
            self.with(|snapshot| snapshot.screen = Screen::Asking(question));
            if left == 0 {
                trace::line(&format!("no answer: {}", ask.title));
                return Asked::TimedOut;
            }
            if let Some(press) = self.wait(Duration::from_millis(250))
                && shown.elapsed() >= ANSWER_GUARD
            {
                let chosen = ask.choices.iter().any(|(_, _, button)| *button == press.button);
                if chosen {
                    trace::line(&format!("answered: {} {:?}", ask.title, press.button));
                    return Asked::Answer(press.button);
                }
            }
            if let Some(watch) = ask.watch
                && last_watch.elapsed() >= Duration::from_secs(2)
            {
                last_watch = Instant::now();
                if watch(self) {
                    return Asked::Satisfied;
                }
            }
        }
    }

    fn your_turn(&self, name: &str) -> usize {
        self.with(|snapshot| snapshot.parts[2].rows.iter().position(|row| row.name == name).map_or(1, |index| index + 1))
    }

    fn yes_no(&mut self, name: &str, body: Vec<String>) -> Asked {
        let step = self.your_turn(name);
        self.ask(Ask {
            step,
            total: 5,
            title: name.to_owned(),
            body,
            link: None,
            choices: vec![
                ("Cross", "Yes", Button::Cross),
                ("Circle", "No", Button::Circle),
                ("Triangle", "Skip", Button::Triangle),
            ],
            keyboard: "On a keyboard: Y, N or S",
            seconds: ANSWER_SECONDS,
            watch: None,
        })
    }

    fn sound(&mut self) -> Outcome {
        let result = match self.test("audio.44100", json!({}), 90) {
            Ok(result) => result,
            Err(trouble) => return trouble.outcome(""),
        };
        if result["observation_required"].as_bool() != Some(true) {
            return judge(&result, String::new(), false);
        }
        let run_id = result["run_id"].as_str().unwrap_or("").to_owned();
        let answered = self.yes_no("Sound", vec!["A short tone just played through your TV.".into(), "Did you hear it?".into()]);
        let answer = match answered {
            Asked::Answer(Button::Cross) => "yes",
            Asked::Answer(Button::Circle) => "no",
            _ => "unsure",
        };
        if let Err(error) = self.call("result.observe", json!({"run_id": run_id, "answer": answer})) {
            return Outcome::new(Status::Fail, "").why(error);
        }
        match answer {
            "yes" => Outcome::new(Status::Pass, ""),
            "no" => Outcome::new(Status::Fail, "").why("The tone played, but you could not hear it."),
            _ => Outcome::new(Status::Skipped, ""),
        }
    }

    fn controller(&mut self) -> Outcome {
        if self.pads.load(Ordering::Relaxed) > 0 {
            Outcome::new(Status::Found, "")
        } else {
            Outcome::new(Status::NotFound, "")
        }
    }

    fn interface(&mut self, name: &str) -> Option<Value> {
        let state = self.call("state", json!({})).ok()?;
        state["network"]["interfaces"].as_array()?.iter().find(|item| item["name"].as_str() == Some(name)).cloned()
    }

    fn has_cable(&mut self) -> bool {
        self.interface("psx0").is_some_and(|port| port["state"].as_str() != Some("unavailable"))
    }

    fn ethernet(&mut self) -> Outcome {
        if let Some(earlier) = self.ethernet_result.clone() {
            return earlier;
        }
        if self.interface("psx0").is_none() {
            return Outcome::new(Status::NotFound, "");
        }
        if !self.has_cable() {
            self.set_row("Ethernet", Status::Running, "No cable yet", "");
            let step = self.your_turn("Ethernet");
            let answer = self.ask(Ask {
                step,
                total: 5,
                title: "Ethernet".into(),
                body: vec!["The network port was found, but no cable is plugged in.".into(), "Plug in a cable to test it. The test starts by itself.".into()],
                link: None,
                choices: vec![("Triangle", "Skip", Button::Triangle)],
                keyboard: "On a keyboard: S",
                seconds: ANSWER_SECONDS,
                watch: Some(Engine::has_cable),
            });
            self.with(|snapshot| snapshot.screen = Screen::Running);
            if !matches!(answer, Asked::Satisfied) {
                return Outcome::new(Status::Skipped, "No cable");
            }
        }
        self.connect_ethernet()
    }

    /// The network driver gets one try per boot, so this never runs twice.
    fn connect_ethernet(&mut self) -> Outcome {
        if let Some(earlier) = self.ethernet_result.clone() {
            return earlier;
        }
        let speed = first_line("/sys/class/net/psx0/speed").filter(|value| value.parse::<u32>().is_ok()).map_or_else(String::new, |value| format!("{value} Mbit/s"));
        let outcome = match self.job("network.ethernet.connect", json!({}), 60) {
            Ok(result) if result["ok"].as_bool() == Some(true) => Outcome::new(Status::Pass, speed),
            Ok(result) => Outcome::new(Status::Found, "Link, no address").why(result["message"].as_str().unwrap_or("The network did not give this console an address.")),
            Err(trouble) => trouble.outcome(speed),
        };
        self.ethernet_result = Some(outcome.clone());
        outcome
    }

    fn wifi(&mut self) -> Outcome {
        let state = self.call("state", json!({})).unwrap_or(Value::Null);
        if state["network"]["wifi"]["available"].as_bool() != Some(true) {
            return Outcome::new(Status::NotFound, "");
        }
        match self.job("network.scan", json!({}), 60) {
            Ok(result) => {
                let count = result["wifi"]["access_points"].as_array().map_or(0, Vec::len);
                if count > 0 {
                    Outcome::new(Status::Pass, format!("{count} networks"))
                } else {
                    Outcome::new(Status::Found, "No networks")
                }
            }
            Err(trouble) => trouble.outcome(""),
        }
    }

    fn video_decoding(&mut self) -> Outcome {
        if self.identity["declared_artifacts"]["uvd_runtime"].as_bool() != Some(true) {
            return Outcome::new(Status::Skipped, "Not in this build");
        }
        match self.job("uvd.scenario", json!({"scenario": "canonical-120"}), 600) {
            Ok(result) => judge(&result, "H.264".into(), false),
            Err(trouble) => trouble.outcome(""),
        }
    }

    /// Fills in what is present before anyone is asked about it.
    fn detect_radios(&mut self) {
        let state = self.call("state", json!({})).unwrap_or(Value::Null);
        let port = state["network"]["interfaces"].as_array().and_then(|items| items.iter().find(|item| item["name"].as_str() == Some("psx0")).cloned());
        let (status, detail) = match port {
            Some(port) if port["state"].as_str() == Some("unavailable") => (Status::Found, "No cable yet"),
            Some(_) => (Status::Found, ""),
            None => (Status::NotFound, ""),
        };
        self.set_row("Ethernet", status, detail, "");
        let wifi = state["network"]["wifi"]["available"].as_bool() == Some(true);
        self.set_row("Wi-Fi", if wifi { Status::Found } else { Status::NotFound }, "", "");
        let radio = bluetooth();
        self.set_row("Bluetooth", radio.status, &radio.detail, "");
    }

    fn run_part(&mut self, index: usize) {
        let names: Vec<&'static str> = self.with(|snapshot| {
            snapshot.part = index;
            snapshot.parts[index].rows.iter().map(|row| row.name).collect()
        });
        trace::line(&format!("part {index} starting"));
        for name in names {
            self.perform(name);
        }
        self.gate = None;
    }

    fn automatic(&mut self) {
        self.run_part(0);
        self.detect_radios();
        if self.status_of("Processor") != Status::Pass || self.status_of("Memory") != Status::Pass {
            self.stopped.get_or_insert_with(|| "Needs the processor and memory tests to pass.".into());
        }
        self.graphics_part();
        self.run_part(2);
        self.last_part();
    }

    fn graphics_part(&mut self) {
        let names: Vec<&'static str> = self.with(|snapshot| {
            snapshot.part = 1;
            snapshot.parts[1].rows.iter().map(|row| row.name).collect()
        });
        for name in names {
            self.perform(name);
            if name == "Graphics driver" && !matches!(self.status_of(name), Status::Pass | Status::Found) {
                self.gate = Some("Needs the graphics driver to start.".into());
            }
        }
        self.gate = None;
    }

    /// With no stick, offers the report first: the last tests can end the session.
    /// The decoder goes last because the controller ends the session after it,
    /// so a benchmark placed after it could never run.
    fn last_part(&mut self) {
        trace::line("last part starting");
        self.with(|snapshot| snapshot.part = 3);
        self.export();
        let on_stick = self.report.lock().unwrap().usb_directory().is_some();
        let blocked = self.stopped.is_some() || self.user_stop;
        if !on_stick && !blocked && !self.confirm_without_stick() {
            for name in ["3D benchmark", "Video decoding"] {
                self.conclude(name, Outcome::new(Status::Skipped, "").why("Skipped because there is no USB stick to keep the report."));
            }
            return;
        }
        self.perform("3D benchmark");
        self.export();
        self.perform("Video decoding");
    }

    fn confirm_without_stick(&mut self) -> bool {
        let link = self.report_link();
        let body = if link.is_some() {
            vec![
                "The last tests can end this session, and there is no USB stick to keep your report.".to_owned(),
                "Scan this to download it now, or open the address on a computer on the same network.".to_owned(),
            ]
        } else {
            vec![
                "The last tests can end this session, and there is no USB stick to keep your report.".to_owned(),
                "Plug in a USB stick or network cable first if you want to keep it.".to_owned(),
            ]
        };
        let answer = self.ask(Ask {
            step: 6,
            total: 6,
            title: "Before the last tests".into(),
            body,
            link,
            choices: vec![("Cross", "Run them", Button::Cross), ("Circle", "Skip them", Button::Circle)],
            keyboard: "On a keyboard: Y or N",
            seconds: BEFORE_LAST_SECONDS,
            watch: None,
        });
        self.with(|snapshot| snapshot.screen = Screen::Running);
        matches!(answer, Asked::Answer(Button::Cross))
    }

    fn manual(&mut self) {
        self.perform("Boot");
        self.detect_radios();
        let names: Vec<&'static str> = self.with(|snapshot| snapshot.rows().map(|row| row.name).collect());
        let mut cursor = 0;
        loop {
            self.with(|snapshot| snapshot.screen = Screen::Choosing { cursor });
            self.drain_presses();
            let press = loop {
                if let Some(press) = self.wait(Duration::from_millis(500)) {
                    break press;
                }
            };
            match press.button {
                Button::Up => cursor = (cursor + names.len() - 1) % names.len(),
                Button::Down => cursor = (cursor + 1) % names.len(),
                Button::Cross => self.run_chosen(names[cursor]),
                Button::Options => {
                    self.run_remaining(&names);
                    break;
                }
                Button::Circle => break,
                _ => {}
            }
        }
    }

    fn select_part_of(&self, name: &str) {
        self.with(|snapshot| {
            if let Some(index) = snapshot.parts.iter().position(|part| part.rows.iter().any(|row| row.name == name)) {
                snapshot.part = index;
            }
        });
    }

    /// A finished row stays finished: repeating a failure needs a fresh boot.
    fn run_chosen(&mut self, name: &'static str) {
        if !matches!(self.status_of(name), Status::Waiting | Status::Found | Status::NotFound) {
            self.with(|snapshot| snapshot.note = Some("Already tested. Restart to test it again.".into()));
            return;
        }
        self.with(|snapshot| {
            snapshot.note = None;
            snapshot.screen = Screen::Running;
        });
        self.select_part_of(name);
        // Each benchmark builds on one of these, so choosing it first still runs it.
        let needed = match name {
            "3D benchmark" => Some("OpenGL"),
            model::PICK_ONLY => Some("Vulkan"),
            _ => None,
        };
        if let Some(needed) = needed
            && self.status_of(needed) == Status::Waiting
        {
            self.perform(needed);
        }
        if can_end_session(name) {
            self.export();
        }
        self.perform(name);
    }

    fn run_remaining(&mut self, names: &[&'static str]) {
        self.with(|snapshot| snapshot.screen = Screen::Running);
        for name in names.iter().filter(|name| **name != model::PICK_ONLY) {
            if matches!(self.status_of(name), Status::Waiting | Status::Found | Status::NotFound) {
                self.select_part_of(name);
                if can_end_session(name) {
                    self.export();
                }
                self.perform(name);
            }
        }
    }

    /// Asks the controller for its report, then builds the one file people share.
    fn export(&mut self) {
        trace::line("export: saving summary");
        self.save_summary();
        if let Ok(result) = self.job("support.export", json!({}), 120)
            && let Some(path) = result["path"].as_str()
        {
            self.report.lock().unwrap().import(Path::new(path), report::CONTROLLER);
        }
        trace::line("export: packing");
        self.archive = self.report.lock().unwrap().pack();
        trace::line(&format!("export: done, packed={}", self.archive.is_some()));
    }

    fn lan_address(&mut self) -> Option<String> {
        let state = self.call("state", json!({})).ok()?;
        let interfaces = state["network"]["interfaces"].as_array()?.clone();
        interfaces.iter().find(|item| item["name"].as_str() != Some("lo")).and_then(|_| {
            interfaces
                .iter()
                .filter(|item| item["name"].as_str() != Some("lo"))
                .find_map(|item| item["ipv4"].as_array()?.first()?.as_str().map(str::to_owned))
        })
    }

    /// Starts the download server the first time it is needed.
    fn report_link(&mut self) -> Option<String> {
        if let Some(server) = &self.server {
            return Some(server.link.clone());
        }
        let address = self.lan_address()?;
        let archive = self.archive.clone()?;
        self.server = serve::start(&address, archive).ok();
        self.server.as_ref().map(|server| server.link.clone())
    }

    fn ssh_command(&mut self) -> Option<String> {
        let info = self.call("ssh.info", json!({})).ok()?;
        if info["key_fingerprints"].as_array()?.is_empty() {
            return None;
        }
        Some(format!("ssh root@{}", self.lan_address()?))
    }

    /// Puts the login on screen as soon as an owner-key build has an address.
    fn poll_login(&mut self) {
        if self.login.is_some() || self.last_login_check.elapsed() < Duration::from_secs(5) {
            return;
        }
        self.last_login_check = Instant::now();
        self.login = self.ssh_command();
        if let Some(login) = self.login.clone() {
            trace::line(&format!("login ready: {login}"));
            self.with(|snapshot| snapshot.login = login);
        }
    }

    fn developer_login(&mut self) -> Option<String> {
        self.ssh_command().map(|command| format!("{command}   (needs your key)"))
    }

    fn download_screen(&mut self) {
        self.drain_presses();
        let ssh = self.developer_login();
        loop {
            if self.server.is_none() && self.ethernet_result.is_none() && self.stopped.is_none() && self.has_cable() {
                let _ = self.connect_ethernet();
            }
            let link = self.report_link();
            let fetched = self.server.as_ref().is_some_and(|server| server.fetched.load(Ordering::Relaxed));
            let view = Download { link, ssh: ssh.clone(), fetched };
            self.with(|snapshot| snapshot.screen = Screen::Download(view));
            if let Some(press) = self.wait(Duration::from_secs(2))
                && press.button == Button::Circle
            {
                return;
            }
        }
    }

    fn review_screen(&mut self) {
        let count = self.with(|snapshot| snapshot.rows().count());
        let mut cursor = 0;
        self.drain_presses();
        loop {
            self.with(|snapshot| snapshot.screen = Screen::Review { cursor });
            let Some(press) = self.wait(Duration::from_secs(1)) else {
                continue;
            };
            match press.button {
                Button::Up => cursor = (cursor + count - 1) % count,
                Button::Down => cursor = (cursor + 1) % count,
                Button::Circle => return,
                _ => {}
            }
        }
    }

    fn copy_to_stick(&mut self) {
        if self.options.usb_dir.is_none() {
            match usb::mount_stick() {
                Stick::Mounted(path) => {
                    self.stick_problem = self.report.lock().unwrap().attach(&path).is_err();
                }
                Stick::Unusable => self.stick_problem = true,
                Stick::None => self.stick_problem = false,
            }
        } else if let Some(path) = self.options.usb_dir.clone() {
            self.stick_problem = self.report.lock().unwrap().attach(&path).is_err();
        }
        let saved = self.report.lock().unwrap().usb_directory().is_some();
        self.show_stick();
        if saved {
            self.report.lock().unwrap().mark("finished", "");
        }
    }

    fn menu(&self) -> Vec<&'static str> {
        finish_menu(self.report.lock().unwrap().usb_directory().is_some(), self.stopped.is_some())
    }

    fn power(&mut self, operation: &str) {
        trace::line(&format!("power {operation}: showing the closing screen"));
        self.with(|snapshot| {
            snapshot.note = Some(power_message(operation).to_owned());
            snapshot.screen = Screen::Starting;
        });
        trace::line(&format!("power {operation}: releasing the report"));
        self.report.lock().unwrap().release();
        if self.options.usb_dir.is_none() {
            usb::unmount();
        }
        trace::line(&format!("power {operation}: stick unmounted, asking the controller"));
        self.with(|snapshot| snapshot.usb = "USB stick: safe to remove".into());
        let reply = self.call("power.execute", json!({"operation": operation, "confirm": true}));
        trace::line(&format!("power {operation}: controller replied, accepted={}", reply.is_ok()));
        match reply {
            Err(error) => {
                trace::line(&format!("power {operation}: {error}"));
                self.with(|snapshot| snapshot.note = Some(error));
            }
            Ok(_) => {
                // Normally the system stops this program during this wait.
                thread::sleep(POWER_WAIT);
                trace::line(&format!("power {operation}: still running after {}s", POWER_WAIT.as_secs()));
                self.with(|snapshot| snapshot.note = Some(POWER_STALLED.into()));
            }
        }
    }

    fn finish(&mut self) {
        trace::line("finishing: final export");
        self.with(|snapshot| {
            snapshot.note = None;
            snapshot.part = snapshot.parts.len() - 1;
        });
        self.export();
        self.report.lock().unwrap().mark("finished", "");
        self.save_summary();
        trace::line("finished: menu showing");
        let mut cursor = 0;
        loop {
            let items = self.menu();
            cursor = cursor.min(items.len() - 1);
            let folder = self.report.lock().unwrap().usb_folder();
            let view = Finish { report: folder, menu: items.clone(), cursor };
            self.with(|snapshot| snapshot.screen = Screen::Finished(view));
            let Some(press) = self.wait(Duration::from_secs(1)) else {
                continue;
            };
            match press.button {
                Button::Up => cursor = (cursor + items.len() - 1) % items.len(),
                Button::Down => cursor = (cursor + 1) % items.len(),
                Button::Cross => {
                    trace::line(&format!("menu: {}", items[cursor]));
                    match items[cursor] {
                        "Copy report to a USB stick" => self.copy_to_stick(),
                        "Get report over the network" => self.download_screen(),
                        "See what each test found" => self.review_screen(),
                        "Run tests again" => return,
                        "Restart to PS4" => self.power("reboot-orbis"),
                        "Turn off" => self.power("shutdown"),
                        _ => {}
                    }
                }
                _ => {}
            }
        }
    }
}

/// Longer than the controller's own wait before it calls the kernel directly.
const POWER_WAIT: Duration = Duration::from_secs(30);
const POWER_STALLED: &str = "The console is still on. Hold the power button to turn it off.";

fn power_message(operation: &str) -> &'static str {
    if operation == "shutdown" { "Turning off..." } else { "Restarting to PS4..." }
}

/// After a safety stop the controller refuses more tests until the console restarts.
fn finish_menu(on_stick: bool, stopped: bool) -> Vec<&'static str> {
    let mut items = Vec::new();
    if !on_stick {
        items.push("Copy report to a USB stick");
    }
    items.extend(["Get report over the network", "See what each test found"]);
    if !stopped {
        items.push("Run tests again");
    }
    items.extend(["Restart to PS4", "Turn off"]);
    items
}

/// Saved first, because these can leave the console needing a restart.
fn can_end_session(name: &str) -> bool {
    matches!(name, "Video decoding" | "3D benchmark" | model::PICK_ONLY)
}

/// Named fields only, so a log line can never carry a password.
fn job_label(action: &str, params: &Value) -> String {
    let named = ["test_id", "scenario", "app"].iter().find_map(|key| params[*key].as_str());
    named.map_or_else(|| action.to_owned(), |value| format!("{action} {value}"))
}

fn bluetooth() -> Outcome {
    let present = fs::read_dir("/sys/class/bluetooth").into_iter().flatten().flatten().any(|entry| entry.file_name().to_string_lossy().starts_with("hci"));
    Outcome::new(if present { Status::Found } else { Status::NotFound }, "")
}

fn status_word(status: Status) -> &'static str {
    match status {
        Status::Waiting => "waiting",
        Status::Running => "running",
        Status::Pass => "PASS",
        Status::Fail => "FAIL",
        Status::Found => "FOUND",
        Status::NotFound => "NOT-FOUND",
        Status::Skipped => "SKIPPED",
        Status::Blocked => "BLOCKED",
    }
}

fn render_summary(snapshot: &Snapshot) -> String {
    let mut text = format!("DiagnostiX report\n{}\n", snapshot.console.replace("  ·  ", ", "));
    for part in &snapshot.parts {
        text.push_str(&format!("\n{}\n", part.title));
        for row in &part.rows {
            let label = if row.status == Status::Waiting { "not run" } else { status_word(row.status) };
            text.push_str(&format!("  {:<18} {:<10} {}\n", row.name, label, row.detail));
            if !row.result.is_empty() {
                text.push_str(&format!("      {}\n", report::clean_line(&row.result)));
            }
        }
    }
    if !snapshot.problems.is_empty() {
        text.push_str("\nProblems\n");
        for problem in &snapshot.problems {
            text.push_str(&format!("  {}: {}\n", problem.name, report::clean_line(&problem.why)));
        }
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_closing_screen_names_the_action() {
        assert_eq!(power_message("shutdown"), "Turning off...");
        assert_eq!(power_message("reboot-orbis"), "Restarting to PS4...");
    }

    #[test]
    fn the_report_never_carries_the_login_address() {
        let mut snapshot = crate::model::blank(false);
        snapshot.login = "ssh root@10.0.0.7".into();
        assert!(!render_summary(&snapshot).contains("10.0.0.7"));
    }

    fn browser_batch(status: &str, stop: bool, steps: [&str; 5]) -> Value {
        let ids = ["browser.capabilities", "browser.canvas2d", "browser.webgl", "browser.animation", "browser.video"];
        let steps: Vec<Value> =
            ids.iter().zip(steps).map(|(id, status)| json!({"test_id": id, "status": status})).collect();
        json!({"status": status, "stop_required": stop, "message": "Automatic browser baseline completed.", "steps": steps})
    }

    #[test]
    fn the_browser_passes_when_its_real_checks_pass() {
        // The run that was wrongly shown as FAIL: only the capability list and
        // the smoothness observation gave no verdict.
        let batch = browser_batch("INCONCLUSIVE", false, ["INCONCLUSIVE", "PASS", "PASS", "INCONCLUSIVE", "PASS"]);
        let outcome = judge_browser(&batch);
        assert_eq!(outcome.status, Status::Pass);
        assert_eq!(outcome.detail, "Chromium");
        assert!(!outcome.stop);
    }

    #[test]
    fn the_browser_names_a_check_it_could_not_confirm() {
        let batch = browser_batch("INCONCLUSIVE", false, ["INCONCLUSIVE", "PASS", "INCONCLUSIVE", "PASS", "UNSUPPORTED"]);
        let outcome = judge_browser(&batch);
        assert_eq!(outcome.status, Status::Found);
        assert_eq!(outcome.why, "Not confirmed: WebGL, Video.");
    }

    #[test]
    fn a_real_browser_failure_stays_a_failure_and_says_which() {
        let batch = browser_batch("FAIL", true, ["INCONCLUSIVE", "PASS", "FAIL", "PASS", "PASS"]);
        let outcome = judge_browser(&batch);
        assert_eq!(outcome.status, Status::Fail);
        assert!(outcome.stop);
        assert_eq!(outcome.why, "Failed: WebGL.");
    }

    #[test]
    fn a_browser_stop_is_never_shown_as_a_pass() {
        // Every real check passed, but the controller could not close the browser.
        let mut batch = browser_batch("INCONCLUSIVE", true, ["INCONCLUSIVE", "PASS", "PASS", "PASS", "PASS"]);
        batch["message"] = json!("The browser service did not confirm closure.");
        let outcome = judge_browser(&batch);
        assert_eq!(outcome.status, Status::Fail);
        assert!(outcome.stop);
    }

    #[test]
    fn a_browser_result_without_steps_is_not_a_pass() {
        let batch = json!({"status": "INCONCLUSIVE", "stop_required": false});
        assert_eq!(judge_browser(&batch).status, Status::Found);
    }

    #[test]
    fn benchmark_row_names_the_tool_that_ran() {
        assert_eq!(benchmark_score(&json!({"tool": "glmark2", "score": 1842})), "GL 1842");
        assert_eq!(benchmark_score(&json!({"tool": "vkmark", "score": 2210})), "VK 2210");
        assert_eq!(benchmark_score(&json!({"tool": "glmark2", "score": null})), "");
        assert_eq!(benchmark_score(&json!({})), "");
    }

    #[test]
    fn only_a_manual_run_offers_the_vulkan_benchmark() {
        let automatic = crate::model::blank(false);
        let manual = crate::model::blank(true);
        assert!(automatic.rows().all(|row| row.name != crate::model::PICK_ONLY));
        assert_eq!(manual.rows().filter(|row| row.name == crate::model::PICK_ONLY).count(), 1);
    }

    #[test]
    fn the_decoder_is_last_because_it_ends_the_session() {
        for manual in [false, true] {
            let snapshot = crate::model::blank(manual);
            assert_eq!(snapshot.rows().last().unwrap().name, "Video decoding");
            assert_eq!(snapshot.parts[3].rows[0].name, "3D benchmark");
            assert!(snapshot.parts[1].rows.iter().all(|row| row.name != "3D benchmark"));
        }
    }

    #[test]
    fn running_again_is_offered_unless_the_controller_stopped_the_run() {
        assert!(finish_menu(true, false).contains(&"Run tests again"));
        assert!(!finish_menu(true, true).contains(&"Run tests again"));
        assert_eq!(finish_menu(true, false)[0], "Get report over the network", "a stray press must not start a run");
        assert_eq!(finish_menu(false, false)[0], "Copy report to a USB stick");
    }

    #[test]
    fn starting_over_clears_the_last_run() {
        let mut snapshot = crate::demo::scene("finished").unwrap();
        snapshot.start_over();
        assert!(snapshot.rows().all(|row| row.status == Status::Waiting && row.detail.is_empty() && row.result.is_empty()));
        assert!(snapshot.problems.is_empty());
        assert_eq!((snapshot.done, snapshot.part), (0, 0));
        assert!(snapshot.restart_clock);
        assert!(matches!(snapshot.screen, Screen::Running));
    }

    #[test]
    fn tests_that_can_end_the_session_save_the_report_first() {
        for name in ["Video decoding", "3D benchmark", crate::model::PICK_ONLY] {
            assert!(can_end_session(name), "{name}");
        }
        assert!(!can_end_session("Memory"));
    }

    #[test]
    fn a_log_label_carries_only_named_fields() {
        assert_eq!(job_label("test.run", &json!({"test_id": "gpu.benchmark", "retry_reason": "x"})), "test.run gpu.benchmark");
        assert_eq!(job_label("network.wifi.connect", &json!({"ssid": "home", "password": "secret"})), "network.wifi.connect");
    }

    #[test]
    fn benchmark_report_lists_every_scene_under_its_tool() {
        let evidence = json!({
            "tool": "glmark2",
            "score": 1842,
            "renderer": "radeonsi",
            "gl_version": "4.6",
            "scene_seconds": 5,
            "exit_code": 0,
            "scenes": [{"scene": "[build] use-vbo=false", "fps": 987, "frame_ms": 1.014}],
        });
        let text = benchmark_report(&evidence);
        assert!(text.contains("glmark2 score: 1842\n"));
        assert!(text.contains("Renderer: radeonsi 4.6\n"));
        assert!(text.contains("Exit code: 0\n"));
        assert!(text.contains("glmark2 scenes\n"));
        assert!(text.ends_with("    987 FPS  [build] use-vbo=false\n"));
    }

    #[test]
    fn manual_is_read_from_the_exact_boot_argument() {
        assert!(requested_manual("quiet dx.mode=manual psx.boot=ram"));
        assert!(!requested_manual("quiet psx.boot=ram"));
        assert!(!requested_manual("xdx.mode=manual"));
    }

    #[test]
    fn a_controller_stop_ends_the_run_whatever_the_status() {
        let outcome = judge(&json!({"status": "INCONCLUSIVE", "stop_required": true, "message": "m"}), String::new(), true);
        assert_eq!(outcome.status, Status::Fail);
        assert!(outcome.stop);
    }

    #[test]
    fn observations_are_found_and_never_passes() {
        let outcome = judge(&json!({"status": "INCONCLUSIVE"}), "x".into(), true);
        assert_eq!(outcome.status, Status::Found);
        let functional = judge(&json!({"status": "INCONCLUSIVE", "message": "m"}), "x".into(), false);
        assert_eq!(functional.status, Status::Fail);
        assert_eq!(judge(&json!({"status": "PASS"}), "x".into(), false).status, Status::Pass);
        assert_eq!(judge(&json!({"status": "BLOCKED", "message": "m"}), "x".into(), false).status, Status::Blocked);
    }

    #[test]
    fn summary_marks_unfinished_tests_as_not_run() {
        let mut snapshot = crate::demo::scene("basics").unwrap();
        snapshot.console = "Baikal B1  ·  Firmware 12.02  ·  Linux 7.2.11".into();
        let text = render_summary(&snapshot);
        assert!(text.contains("Baikal B1, Firmware 12.02, Linux 7.2.11"));
        assert!(text.contains("Processor"));
        assert!(text.contains("not run"));
    }
}
