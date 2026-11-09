// SPDX-License-Identifier: MIT
mod hardware;
mod ipc;
mod model;
mod overview;
mod ui;

use crossterm::cursor::{MoveTo, Show};
use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use crossterm::execute;
use crossterm::style::{Attribute, ResetColor, SetAttribute};
use crossterm::terminal::{self, Clear, ClearType, EnterAlternateScreen, LeaveAlternateScreen};
use model::{ActionKind, CATEGORIES, Category};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use serde_json::{Value, json};
use std::cell::Cell;
use std::io;
use std::io::Write;
use std::path::PathBuf;
use std::sync::mpsc::{Receiver, Sender};
use std::time::{Duration, Instant};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Focus {
    Categories,
    Actions,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum DiagnosticScreen {
    Hidden,
    Choose,
    Automatic,
}

pub enum FormKind {
    Wifi,
    Https,
    SshGithub,
    Retry { action: String, params: Value },
    FullRetry,
    Observation { run_id: String },
}

pub struct Form {
    pub kind: FormKind,
    pub fields: Vec<String>,
    pub selected: usize,
    pub error: String,
}

pub struct PowerDialog {
    pub operation: model::PowerOption,
    pub confirm_selected: bool,
}

pub struct Job {
    pub id: Value,
    pub title: String,
    pub started: Instant,
    pub data: Value,
}

pub struct HardwareDialog {
    pub title: &'static str,
    pub subtitle: &'static str,
    pub inventory: Value,
    pub raw_sources: Option<Vec<Value>>,
    pub pages: Vec<hardware::Page>,
    pub selected: usize,
    pub detail: bool,
    pub raw: Option<String>,
    pub scroll: u16,
    detail_scroll: u16,
}
impl HardwareDialog {
    fn new(inventory: Value, section: hardware::Section) -> Self {
        let pages = hardware::SECTIONS
            .iter()
            .map(|section| hardware::page(*section, &inventory))
            .collect();
        Self {
            title: "Hardware",
            subtitle: "Cached hardware metadata",
            raw_sources: None,
            inventory,
            pages,
            selected: hardware::SECTIONS
                .iter()
                .position(|item| *item == section)
                .unwrap_or(0),
            detail: false,
            raw: None,
            scroll: 0,
            detail_scroll: 0,
        }
    }
    fn qualification() -> Result<Self, String> {
        let plan: Value = serde_json::from_str(include_str!("../../qualification.json"))
            .map_err(|_| "The embedded test plan is invalid.".to_owned())?;
        let checks = model::array(&plan, "/checks");
        if checks.is_empty() {
            return Err("The embedded test plan has no definitions.".into());
        }
        let required = model::array(&plan, "/profiles/kernel-baseline/required")
            .iter()
            .map(model::text)
            .collect::<std::collections::HashSet<_>>();
        let groups: [(&'static str, &[&str]); 12] = [
            ("Prerequisites", &["Boot", "Source"]),
            ("CPU & RAM", &["CPU", "Memory"]),
            ("Thermal", &["Thermal"]),
            ("Ethernet", &["Ethernet"]),
            ("Wireless", &["Wireless"]),
            ("USB / Storage", &["USB", "Storage"]),
            ("Graphics / HDMI", &["Graphics", "Display"]),
            ("Browsers", &["Browser"]),
            ("Audio", &["Audio"]),
            ("UVD", &["UVD"]),
            ("Input / Controls", &["HID", "Controls"]),
            ("Clock / Power", &["RTC", "Power"]),
        ];
        let mut pages = Vec::new();
        let mut raw_sources = Vec::new();
        for (title, areas) in groups {
            let items = checks
                .iter()
                .filter(|item| areas.contains(&model::at(item, "/area").as_str()))
                .collect::<Vec<_>>();
            let mandatory = items
                .iter()
                .filter(|item| required.contains(&model::at(item, "/id")))
                .count();
            let mut rows = Vec::new();
            for item in &items {
                rows.push(hardware::Row::Heading(model::at(item, "/title")));
                let mode = model::at(item, "/mode");
                let status = match mode.as_str() {
                    "unimplemented" => "Blocked · implementation missing",
                    "deferred" => "Deferred",
                    _ => "Not run",
                };
                for (label, value) in [
                    ("Plan status", status.to_owned()),
                    ("Execution", mode),
                    (
                        "Baseline gate",
                        if required.contains(&model::at(item, "/id")) {
                            "Required".into()
                        } else {
                            "Optional / separate scope".into()
                        },
                    ),
                    ("Scope", model::at(item, "/scope")),
                    (
                        "Repetition",
                        format!(
                            "{} runs / {} cold boots",
                            model::at(item, "/acceptance/runs"),
                            model::at(item, "/acceptance/cold_boots")
                        ),
                    ),
                    (
                        "Duration",
                        format!(
                            "{} s per run",
                            model::at(item, "/acceptance/seconds_per_run")
                        ),
                    ),
                    (
                        "After",
                        model::array(item, "/depends_on")
                            .iter()
                            .map(model::text)
                            .collect::<Vec<_>>()
                            .join(", "),
                    ),
                    (
                        "Oracles",
                        model::array(item, "/acceptance/oracles")
                            .iter()
                            .map(|v| model::text(v).replace('_', " "))
                            .collect::<Vec<_>>()
                            .join(", "),
                    ),
                    ("Evidence", model::at(item, "/evidence")),
                    ("Basis", model::at(item, "/basis")),
                    ("Source", model::at(item, "/source")),
                ] {
                    if !value.is_empty() {
                        rows.push(hardware::Row::Field(label.into(), value));
                    }
                }
                for (key, label) in [
                    ("metrics_minimum", "Minimum"),
                    ("metrics_maximum", "Maximum"),
                ] {
                    if let Some(metrics) = item["acceptance"][key].as_object() {
                        if !metrics.is_empty() {
                            rows.push(hardware::Row::Field(
                                label.into(),
                                metrics
                                    .iter()
                                    .map(|(k, v)| {
                                        format!("{}: {}", k.replace('_', " "), model::text(v))
                                    })
                                    .collect::<Vec<_>>()
                                    .join(", "),
                            ));
                        }
                    }
                }
                for scenario in model::array(item, "/scenarios") {
                    rows.push(hardware::Row::Heading(format!(
                        "Scenario · {}",
                        model::at(scenario, "/title")
                    )));
                    let availability = match model::at(scenario, "/availability").as_str() {
                        "available-alpha" => "Available alpha action · not a result",
                        "unimplemented" => "Unimplemented · no runnable fixture/harness",
                        _ => "Unavailable · unknown scenario definition",
                    };
                    rows.push(hardware::Row::Field(
                        "Availability".into(),
                        availability.into(),
                    ));
                    let action = model::at(scenario, "/existing_test_id");
                    if !action.is_empty() {
                        rows.push(hardware::Row::Field(
                            "Existing action".into(),
                            format!("{action} · not started here"),
                        ));
                    }
                    for (key, label) in [
                        ("fixture", "Fixture"),
                        ("preparation", "Prepare"),
                        ("sequence", "Sequence"),
                        ("evidence", "Evidence"),
                        ("limits", "Limits"),
                    ] {
                        let value = model::at(scenario, &format!("/{key}"));
                        if !value.is_empty() {
                            rows.push(hardware::Row::Field(label.into(), value));
                        }
                    }
                }
            }
            pages.push(hardware::Page {
                title,
                summary: format!(
                    "{} planned checks · {} baseline gates",
                    items.len(),
                    mandatory
                ),
                rows,
            });
            raw_sources.push(Value::Array(items.into_iter().cloned().collect()));
        }
        if raw_sources
            .iter()
            .map(|items| model::array(items, "").len())
            .sum::<usize>()
            != checks.len()
        {
            return Err("The embedded plan contains an unknown area.".into());
        }
        Ok(Self {
            title: "Test plan",
            subtitle: "Proposed gates · not executed test results",
            inventory: Value::Null,
            raw_sources: Some(raw_sources),
            pages,
            selected: 0,
            detail: false,
            raw: None,
            scroll: 0,
            detail_scroll: 0,
        })
    }
}

pub struct PrivateSecret(Vec<u8>);
impl PrivateSecret {
    fn new(value: String) -> Self {
        Self(value.into_bytes())
    }
    pub fn expose(&self) -> &str {
        std::str::from_utf8(&self.0).unwrap_or("")
    }
}
impl Drop for PrivateSecret {
    fn drop(&mut self) {
        // Volatile writes keep the compiler from discarding the private buffer scrub.
        for byte in &mut self.0 {
            unsafe { std::ptr::write_volatile(byte, 0) };
        }
        std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
    }
}
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum SshScreen {
    Menu,
    Preview,
    PasswordConfirm,
    Password,
}
pub struct SshDialog {
    pub screen: SshScreen,
    pub info: Value,
    pub selected: usize,
    pub scroll: u16,
    pub error: String,
    pub secret: Option<PrivateSecret>,
    pub expires: Option<Instant>,
}
impl SshDialog {
    fn menu() -> Self {
        Self {
            screen: SshScreen::Menu,
            info: json!({}),
            selected: 0,
            scroll: 0,
            error: String::new(),
            secret: None,
            expires: None,
        }
    }
}

pub struct App {
    pub state: Value,
    pub onboarding: bool,
    pub diagnostic_screen: DiagnosticScreen,
    pub mode_selected: usize,
    pub automatic_selected: usize,
    pub automatic_result: Option<Value>,
    pub issue_report: Option<Value>,
    pub overview: bool,
    pub overview_selected: usize,
    pub overview_offset: Cell<usize>,
    pub category: usize,
    pub selected: usize,
    pub focus: Focus,
    pub connected: bool,
    pub error: String,
    pub notice: String,
    pub active_job: Option<Job>,
    pub last_report: Option<Value>,
    pub form: Option<Form>,
    pub ssh_dialog: Option<SshDialog>,
    pub ssh_pending: bool,
    pub hardware: Option<HardwareDialog>,
    pub viewport: (u16, u16),
    pub report: Option<Value>,
    pub report_scroll: u16,
    pub report_expanded: [bool; model::REPORT_GROUPS.len()],
    pub settings_expanded: [bool; 3],
    pub settings_offset: Cell<usize>,
    pub help: bool,
    pub exit_dialog: bool,
    pub full_queue: Vec<(String, String, Value)>,
    pub full_active: bool,
    pub full_in_flight: bool,
    pub full_stop_after: bool,
    pub full_total: usize,
    pub full_done: usize,
    pub full_passed: usize,
    pub full_failed: usize,
    pub full_observed: usize,
    pub full_notes: Vec<String>,
    pub full_reason: String,
    pub full_current: String,
    pub power_dialog: Option<PowerDialog>,
    pub demo: bool,
    pub ticks: u64,
    sender: Option<Sender<ipc::Request>>,
    receiver: Option<Receiver<ipc::Reply>>,
    next_id: u64,
    state_pending: bool,
    job_pending: bool,
    action_pending: bool,
    power_pending: bool,
    action_title: String,
    action_name: String,
    last_state: Instant,
    last_job: Instant,
    demo_action: Option<(Instant, String)>,
    error_action: String,
    redraw_requested: bool,
}

impl App {
    pub fn new(socket: PathBuf, demo: bool) -> Self {
        let (sender, receiver) = if demo {
            (None, None)
        } else {
            let (tx, rx) = ipc::worker(socket);
            (Some(tx), Some(rx))
        };
        Self {
            state: if demo { model::demo_state() } else { json!({}) },
            onboarding: true,
            diagnostic_screen: DiagnosticScreen::Hidden,
            mode_selected: 1,
            automatic_selected: 0,
            automatic_result: None,
            issue_report: None,
            overview: false,
            overview_selected: 0,
            overview_offset: Cell::new(0),
            category: 4,
            selected: 0,
            focus: Focus::Actions,
            connected: demo,
            error: String::new(),
            notice: if demo {
                "Demo preview only. No hardware actions execute.".into()
            } else {
                "Choose a connection, then continue to Diagnostix.".into()
            },
            active_job: None,
            last_report: None,
            form: None,
            ssh_dialog: None,
            ssh_pending: false,
            hardware: None,
            viewport: (80, 24),
            report: None,
            report_scroll: 0,
            report_expanded: [false; model::REPORT_GROUPS.len()],
            settings_expanded: [false; 3],
            settings_offset: Cell::new(0),
            help: false,
            exit_dialog: false,
            full_queue: Vec::new(),
            full_active: false,
            full_in_flight: false,
            full_stop_after: false,
            full_total: 0,
            full_done: 0,
            full_passed: 0,
            full_failed: 0,
            full_observed: 0,
            full_notes: Vec::new(),
            full_reason: String::new(),
            full_current: String::new(),
            power_dialog: None,
            demo,
            ticks: 0,
            sender,
            receiver,
            next_id: 0,
            state_pending: false,
            job_pending: false,
            action_pending: false,
            power_pending: false,
            action_title: String::new(),
            action_name: String::new(),
            last_state: Instant::now()
                .checked_sub(Duration::from_secs(10))
                .unwrap_or_else(Instant::now),
            last_job: Instant::now(),
            demo_action: None,
            error_action: String::new(),
            redraw_requested: true,
        }
    }

    pub fn category(&self) -> Category {
        CATEGORIES[self.category]
    }
    pub fn actions(&self) -> Vec<model::Action> {
        if self.category() == Category::Reports {
            let mut actions = model::report_actions(&self.state, &self.report_expanded);
            if let Some(report) = self.latest_issue_report() {
                actions.push(model::Action {
                    title: "View issue report".into(),
                    detail: "Show the saved ZIP path and download link for a GitHub issue.".into(),
                    kind: ActionKind::Information(report.clone()),
                });
            }
            actions
        } else if self.category() == Category::Settings {
            model::settings_actions(&self.state, self.settings_expanded)
        } else {
            let mut actions = model::actions(self.category(), &self.state, self.onboarding);
            if self.category() == Category::System
                && std::env::var("DISPLAY").as_deref() == Ok(":0")
            {
                actions.retain(|item| !matches!(&item.kind, ActionKind::Request("graphics.launch", params) if params["app"]=="diagnostics"));
            }
            if self.category() == Category::System && self.latest_automatic().is_some() {
                actions.push(model::Action {
                    title: "Automatic diagnostics summary".into(),
                    detail: "Review the last automatic run without starting another.".into(),
                    kind: ActionKind::AutomaticSummary,
                });
            }
            actions
        }
    }

    pub fn latest_automatic(&self) -> Option<&Value> {
        self.automatic_result.as_ref().or_else(|| {
            self.state
                .get("automatic")
                .filter(|value| value.is_object())
        })
    }

    pub fn latest_issue_report(&self) -> Option<&Value> {
        self.issue_report.as_ref().or_else(|| {
            self.latest_automatic()
                .and_then(|value| value.get("issue_report"))
                .filter(|value| model::IssueReport::from_value(value).is_some())
        })
    }

    fn choose_mode(&mut self) {
        self.onboarding = false;
        self.overview = false;
        self.diagnostic_screen = DiagnosticScreen::Choose;
        self.mode_selected = 1;
        self.notice = "Choose a mode, then press Enter. No tests start until selected.".into();
    }

    fn manual_diagnostics(&mut self) {
        self.diagnostic_screen = DiagnosticScreen::Hidden;
        self.overview = true;
        self.category = 0;
        self.selected = 0;
        self.focus = Focus::Categories;
        self.notice = "Manual diagnostics. Open an area to choose an individual test.".into();
    }

    pub fn automatic_actions(&self) -> Vec<&'static str> {
        let mut actions = Vec::new();
        if self.latest_issue_report().is_some() {
            actions.push("View issue report");
        }
        actions.extend([
            "Create issue report",
            "Manual diagnostics",
            "Choose diagnostic mode",
        ]);
        actions
    }

    pub fn automatic_running(&self) -> bool {
        (self.action_name == "diagnostics.auto" && self.busy())
            || self.active_job.as_ref().is_some_and(|job| {
                job.title == "Automatic diagnostics"
                    || model::automatic_progress(&job.data).is_some()
            })
    }

    fn diagnostic_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Char('q') => self.open_power_page(),
            KeyCode::F(1) => self.help = true,
            KeyCode::F(5) => self.refresh(),
            KeyCode::Esc if self.diagnostic_screen == DiagnosticScreen::Choose => {
                self.mode_selected = 1;
            }
            KeyCode::Esc | KeyCode::Char('m' | 'M') => self.choose_mode(),
            KeyCode::Char('v') if self.diagnostic_screen == DiagnosticScreen::Automatic => {
                self.report = self.latest_automatic().cloned();
                self.report_scroll = 0;
            }
            KeyCode::Up | KeyCode::Down | KeyCode::Home | KeyCode::End => {
                let (selected, count) = if self.diagnostic_screen == DiagnosticScreen::Choose {
                    (&mut self.mode_selected, 2)
                } else {
                    let count = self.automatic_actions().len();
                    (&mut self.automatic_selected, count)
                };
                *selected = match key.code {
                    KeyCode::Up => selected.saturating_sub(1),
                    KeyCode::Down => (*selected + 1).min(count - 1),
                    KeyCode::Home => 0,
                    _ => count - 1,
                };
            }
            KeyCode::Enter if self.diagnostic_screen == DiagnosticScreen::Choose => {
                if self.mode_selected == 1 {
                    self.manual_diagnostics();
                } else {
                    // The chooser is the only automatic submission path. Once
                    // opened, the progress page cannot submit this action again.
                    self.diagnostic_screen = DiagnosticScreen::Automatic;
                    self.automatic_selected = 0;
                    self.run("Automatic diagnostics", "diagnostics.auto", json!({}));
                }
            }
            KeyCode::Enter => {
                match self
                    .automatic_actions()
                    .get(self.automatic_selected)
                    .copied()
                {
                    Some("View issue report") => {
                        self.report = self.latest_issue_report().cloned();
                        self.report_scroll = 0;
                    }
                    Some("Create issue report") => {
                        self.run("Create issue report", "support.export", json!({}));
                    }
                    Some("Manual diagnostics") => self.manual_diagnostics(),
                    Some("Choose diagnostic mode") => self.choose_mode(),
                    _ => {}
                }
            }
            _ => {}
        }
    }

    fn report_tree_focused(&self) -> bool {
        !self.onboarding && self.category() == Category::Reports && self.focus == Focus::Actions
    }

    fn settings_tree_focused(&self) -> bool {
        !self.onboarding
            && self.category() == Category::Settings
            && self.focus == Focus::Actions
            && self
                .actions()
                .get(self.selected)
                .is_some_and(|item| model::settings_group(item).is_some())
    }
    fn collapse_settings(&mut self, group: model::SettingsGroup) {
        self.settings_expanded[group.index()] = false;
        self.selected = self
            .actions()
            .iter()
            .position(|item| matches!(item.kind,ActionKind::SettingsGroup(value) if value==group))
            .unwrap_or(0);
    }
    fn settings_right(&mut self) {
        if let Some(model::Action {
            kind: ActionKind::SettingsGroup(group),
            ..
        }) = self.actions().get(self.selected)
        {
            if self.settings_expanded[group.index()] {
                if self
                    .actions()
                    .get(self.selected + 1)
                    .and_then(model::settings_group)
                    == Some(*group)
                {
                    self.selected += 1;
                }
            } else {
                self.settings_expanded[group.index()] = true;
            }
        }
    }

    fn collapse_report_group(&mut self) {
        let group = self
            .actions()
            .get(self.selected)
            .and_then(|item| match &item.kind {
                ActionKind::ReportGroup(group) => Some(*group),
                ActionKind::Report(value) => Some(model::report_group(value)),
                _ => None,
            });
        if let Some(group) = group {
            self.report_expanded[group] = false;
            if let Some(index) = self.actions().iter().position(
                |item| matches!(item.kind, ActionKind::ReportGroup(value) if value == group),
            ) {
                self.selected = index;
            }
        }
    }

    fn expand_report_group(&mut self) {
        if let Some(model::Action {
            kind: ActionKind::ReportGroup(group),
            ..
        }) = self.actions().get(self.selected)
        {
            if self.report_expanded[*group] {
                self.selected += 1;
            } else {
                self.report_expanded[*group] = true;
            }
        }
    }
    pub fn busy(&self) -> bool {
        self.action_pending || self.active_job.is_some() || self.ssh_pending
    }

    fn open_ssh(&mut self) {
        if self.ssh_pending {
            self.notice = "Wait for the previous SSH setup request to finish.".into();
            return;
        }
        self.ssh_dialog = Some(SshDialog::menu());
        self.ssh_request("ssh.info", json!({}));
    }
    fn close_ssh(&mut self) {
        self.ssh_dialog = None;
        self.redraw_requested = true;
    }
    fn ssh_request(&mut self, action: &str, params: Value) {
        if self.ssh_pending {
            return;
        }
        if !self.connected && !self.demo {
            if let Some(dialog) = self.ssh_dialog.as_mut() {
                dialog.error = "The diagnostic controller is offline.".into();
            }
            return;
        }
        if self.demo || (action != "ssh.info" && self.busy()) {
            if let Some(dialog) = self.ssh_dialog.as_mut() {
                dialog.error = if self.demo {
                    "SSH setup is unavailable in demo mode."
                } else {
                    "Wait for the current action before changing SSH access."
                }
                .into();
            }
            return;
        }
        self.ssh_pending = true;
        self.send(action, params);
    }
    fn receive_ssh(&mut self, reply: ipc::Reply) {
        self.ssh_pending = false;
        let mut value = match reply.result {
            Ok(value) => value,
            Err(error) => {
                if let Some(dialog) = self.ssh_dialog.as_mut() {
                    dialog.error = error;
                }
                return;
            }
        };
        if reply.action == "ssh.password.enable" {
            let password = match value.get_mut("password").map(Value::take) {
                Some(Value::String(value)) => Some(PrivateSecret::new(value)),
                _ => None,
            };
            if let Some(dialog) = self.ssh_dialog.as_mut() {
                if password.is_none() {
                    dialog.error = "The private password reply was incomplete.".into();
                    return;
                }
                dialog.screen = SshScreen::Password;
                dialog.secret = password;
                dialog.error.clear();
                dialog.expires = Some(
                    Instant::now()
                        + Duration::from_secs(
                            value["expires_in_seconds"].as_u64().unwrap_or(600).min(600),
                        ),
                );
            }
            // Even a late reply after the dialog closed is scrubbed here, never retained.
            return;
        }
        let Some(dialog) = self.ssh_dialog.as_mut() else {
            return;
        };
        dialog.error.clear();
        match reply.action.as_str() {
            "ssh.info" => {
                dialog.screen = SshScreen::Menu;
                dialog.info = value;
                dialog.selected = 0;
            }
            "ssh.github.preview" => {
                dialog.screen = SshScreen::Preview;
                dialog.info = value;
                dialog.selected = 0;
                dialog.scroll = 0;
            }
            "ssh.github.add" => {
                dialog.screen = SshScreen::Menu;
                dialog.selected = 0;
                if value["persisted"].as_bool() == Some(true)
                    && value["ssh_ready"].as_bool() == Some(false)
                {
                    dialog.info =
                        json!({"setup_available":false,"message":model::at(&value,"/message")});
                    dialog.error =
                        "Public keys were saved. SSH is not ready; do not add them again.".into();
                } else {
                    dialog.info = json!({});
                    self.ssh_request("ssh.info", json!({}));
                }
            }
            "ssh.password.disable" => {
                dialog.screen = SshScreen::Menu;
                dialog.secret = None;
                self.ssh_request("ssh.info", json!({}));
            }
            _ => {}
        }
    }
    fn ssh_key(&mut self, key: KeyEvent) {
        if matches!(key.code, KeyCode::Esc | KeyCode::Char('q')) {
            self.close_ssh();
            return;
        }
        let Some(dialog) = self.ssh_dialog.as_mut() else {
            return;
        };
        if dialog.screen == SshScreen::Password {
            if key.code == KeyCode::Enter {
                self.close_ssh();
            }
            return;
        }
        if self.ssh_pending {
            return;
        }
        if dialog.screen == SshScreen::Menu
            && dialog.info["setup_available"].as_bool() == Some(false)
        {
            if key.code == KeyCode::Enter {
                self.close_ssh();
            }
            return;
        }
        match key.code {
            KeyCode::PageDown => dialog.scroll = dialog.scroll.saturating_add(8),
            KeyCode::PageUp => dialog.scroll = dialog.scroll.saturating_sub(8),
            KeyCode::Up | KeyCode::Left => dialog.selected = dialog.selected.saturating_sub(1),
            KeyCode::Down | KeyCode::Right | KeyCode::Tab => {
                dialog.selected = (dialog.selected + 1)
                    % if dialog.screen == SshScreen::Menu {
                        3
                    } else {
                        2
                    }
            }
            KeyCode::Enter => match dialog.screen {
                SshScreen::Menu => match dialog.selected {
                    0 => {
                        self.form = Some(Form {
                            kind: FormKind::SshGithub,
                            fields: vec![String::new()],
                            selected: 0,
                            error: String::new(),
                        })
                    }
                    1 => {
                        if dialog.info["password_enabled"].as_bool().unwrap_or(false) {
                            self.ssh_request("ssh.password.disable", json!({}));
                        } else {
                            dialog.screen = SshScreen::PasswordConfirm;
                            dialog.selected = 0;
                        }
                    }
                    _ => self.close_ssh(),
                },
                SshScreen::Preview => {
                    if dialog.selected == 1 {
                        let id = model::at(&dialog.info, "/preview_id");
                        self.ssh_request("ssh.github.add", json!({"preview_id":id}));
                    } else {
                        dialog.screen = SshScreen::Menu;
                        dialog.selected = 0;
                        self.ssh_request("ssh.info", json!({}));
                    }
                }
                SshScreen::PasswordConfirm => {
                    if dialog.selected == 1 {
                        self.ssh_request("ssh.password.enable", json!({}));
                    } else {
                        dialog.screen = SshScreen::Menu;
                        dialog.selected = 0;
                    }
                }
                SshScreen::Password => {}
            },
            _ => {}
        }
    }

    fn send(&mut self, action: &str, params: Value) {
        self.next_id += 1;
        if let Some(sender) = &self.sender {
            if sender
                .send(ipc::Request {
                    id: self.next_id,
                    action: action.into(),
                    params,
                })
                .is_err()
            {
                self.error = "Diagnostic controller worker stopped".into();
                self.action_pending = false;
                self.state_pending = false;
                self.job_pending = false;
                if action.starts_with("ssh.") {
                    self.ssh_pending = false;
                    if let Some(dialog) = self.ssh_dialog.as_mut() {
                        dialog.error = "Diagnostic controller worker stopped".into();
                    }
                }
            }
        }
    }

    fn refresh(&mut self) {
        if !self.demo && !self.state_pending {
            self.state_pending = true;
            self.last_state = Instant::now();
            self.send("state", json!({}));
        }
    }

    fn run(&mut self, title: &str, action: &str, params: Value) {
        if self.power_pending || (self.busy() && action != "power.execute") {
            self.notice =
                "An action is already running. Power can interrupt a running test.".into();
            return;
        }
        if !self.connected && !self.demo {
            self.error =
                "The diagnostic controller is offline. Waiting for it to reconnect.".into();
            return;
        }
        self.error.clear();
        self.error_action.clear();
        self.action_title = title.into();
        self.action_name = action.into();
        self.notice = format!("Starting {title}…");
        self.action_pending = true;
        if action == "power.execute" {
            self.power_pending = true;
            self.active_job = None;
            self.job_pending = false;
        }
        if self.demo {
            self.demo_action = Some((Instant::now(), action.into()));
        } else {
            self.send(action, params);
        }
    }

    fn failed_previous(&self, params: &Value) -> bool {
        let id = model::at(params, "/test_id");
        model::array(&self.state, "/results")
            .iter()
            .find(|r| model::at(r, "/test_id") == id && self.same_boot(r))
            .map(|r| {
                if let Some(required) = r["retry_required"].as_bool() {
                    return required;
                }
                let status = model::at(r, "/status");
                let commands = r["commands"].as_array();
                let has_commands = commands.map(|a| !a.is_empty()).unwrap_or(false);
                let command_failed = commands
                    .map(|items| {
                        items.iter().any(|c| {
                            c["returncode"].as_i64().map(|n| n != 0).unwrap_or(true)
                                || c["timed_out"].as_bool().unwrap_or(false)
                                || c["interrupted"].as_bool().unwrap_or(false)
                                || c["output_limited"].as_bool().unwrap_or(false)
                        })
                    })
                    .unwrap_or(false);
                let functional = [
                    "cpu.compute",
                    "memory.pattern",
                    "audio.44100",
                    "ethernet.connect",
                    "ethernet.https",
                ]
                .contains(&id.as_str());
                status == "FAIL"
                    || (status == "INCONCLUSIVE"
                        && has_commands
                        && !r["observation_required"].as_bool().unwrap_or(false)
                        && (functional || command_failed))
            })
            .unwrap_or(false)
    }

    fn same_boot(&self, record: &Value) -> bool {
        let current = model::at(&self.state, "/identity/boot_id");
        let mut recorded = model::at(record, "/boot_id");
        if recorded.is_empty() {
            recorded = model::at(record, "/identity/boot_id");
        }
        current.is_empty() || recorded == current
    }

    fn quick_needs_reason(&self) -> bool {
        ["cpu.compute", "memory.pattern"]
            .iter()
            .any(|id| self.failed_previous(&json!({"test_id":id})))
    }

    fn start_quick(&mut self) {
        if self.quick_needs_reason() {
            self.form = Some(Form {
                kind: FormKind::Retry {
                    action: "quick".into(),
                    params: json!({"retry_reason":""}),
                },
                fields: vec![String::new()],
                selected: 0,
                error: String::new(),
            });
        } else {
            self.run("Core checks", "quick", json!({"retry_reason":""}));
        }
    }


    fn full_test_queue(&self) -> Vec<(String, String, Value)> {
        let mut queue = vec![(
            "Hardware inventory".into(),
            "test.run".into(),
            json!({"test_id": "inventory.readonly", "parameters": {}, "retry_reason": ""}),
        )];
        let mut seen = std::collections::HashSet::new();
        // Core first, then network, graphics, audio, storage. The browser is
        // deliberately excluded here and launched only after the report.
        for category in [
            model::Category::Cpu,
            model::Category::Network,
            model::Category::Graphics,
            model::Category::Audio,
            model::Category::Storage,
        ] {
            for action in model::actions(category, &self.state, false) {
                let model::ActionKind::Request(name, params) = action.kind else {
                    continue;
                };
                let include = match name {
                    "test.run" => {
                        let id = model::at(&params, "/test_id");
                        id != "uvd.avc" && !self.failed_previous(&params)
                    }
                    "graphics.wayland" | "uvd.scenario" => true,
                    _ => false,
                };
                if !include {
                    continue;
                }
                if seen.insert(format!("{}:{}", name, params)) {
                    queue.push((action.title, name.to_string(), params));
                }
            }
        }
        let uvd = self
            .state
            .pointer("/identity/declared_artifacts/uvd_runtime")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        if uvd {
            for (scenario, title) in [
                ("canonical-120", "UVD AVC reference"),
                ("native720-900", "UVD native 720p30"),
                ("native1080-900", "UVD native 1080p30"),
            ] {
                let params = json!({"scenario": scenario});
                if seen.insert(format!("uvd.scenario:{}", params)) {
                    queue.push((title.into(), "uvd.scenario".into(), params));
                }
            }
        }
        queue.push((
            "Create issue report".into(),
            "support.export".into(),
            json!({}),
        ));
        for action in model::actions(model::Category::Browser, &self.state, false) {
            if let model::ActionKind::Request("graphics.launch", params) = action.kind {
                if model::at(&params, "/app") == "browser" {
                    queue.push((action.title, "graphics.launch".into(), params));
                    break;
                }
            }
        }
        queue
    }

    fn start_full_test(&mut self) {
        if self.demo {
            self.notice = "Demo preview only; the full test is not executed.".into();
            return;
        }
        if !self.connected {
            self.notice = "The diagnostic controller is offline.".into();
            return;
        }
        let queue = self.full_test_queue();
        if queue.is_empty() {
            self.notice = "No runnable tests are available.".into();
            return;
        }
        self.full_total = queue.len();
        self.full_done = 0;
        self.full_passed = 0;
        let _ = std::fs::create_dir_all("/run/psx-state");
        let _ = std::fs::write(
            "/run/psx-state/full-test-report.txt",
            format!("Diagnostix full test\nQueue: {}\n\n", self.full_total),
        );
        self.full_failed = 0;
        self.full_observed = 0;
        self.full_notes.clear();
        self.full_queue = queue;
        self.full_active = true;
        self.full_in_flight = false;
        self.full_stop_after = false;
        self.start_next_full();
    }

    fn start_next_full(&mut self) {
        if !self.full_active {
            return;
        }
        if !self.connected {
            self.full_active = false;
            self.notice = "Full test stopped: the controller went offline.".into();
            return;
        }
        let Some((title, action, params)) = self.full_queue.first().cloned() else {
            self.finish_full(false, false);
            return;
        };
        self.full_queue.remove(0);
        self.full_done += 1;
        self.full_current = title.clone();
        self.full_stop_after = action == "graphics.launch";
        self.full_in_flight = true;
        let progress = format!("Full test {}/{} · {}", self.full_done, self.full_total, title);
        self.run(&title, &action, params);
        self.notice = progress;
    }

    fn advance_full(&mut self, result: &Value) {
        if !self.full_in_flight {
            return;
        }
        self.full_in_flight = false;
        if !self.full_active {
            return;
        }
        let status = model::at(result, "/status");
        let test_id = model::at(result, "/test_id");
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open("/run/psx-state/full-test-report.txt")
        {
            let line = format!(
                "[{}/{}] {}\t{}\n",
                self.full_done,
                self.full_total,
                model::status_label(&status),
                self.full_current
            );
            let _ = std::io::Write::write_all(&mut file, line.as_bytes());
        }
        let observation = model::array(&self.state, "/tests")
            .iter()
            .find(|test| model::at(test, "/id") == test_id)
            .map(|test| model::at(test, "/kind") == "observation")
            .unwrap_or(false);
        if result["stop_required"].as_bool().unwrap_or(false) {
            let message = model::at(result, "/message");
            self.full_notes.push(format!(
                "{}: terminal condition{}",
                self.full_current,
                if message.is_empty() { String::new() } else { format!(": {message}") }
            ));
            self.finish_full(false, false);
            return;
        }
        if matches!(status.as_str(), "PASS" | "READY") {
            self.full_passed += 1;
        } else if status == "INCONCLUSIVE"
            && (observation || result["observation_required"].as_bool().unwrap_or(false))
        {
            self.full_observed += 1;
        } else {
            self.full_failed += 1;
            self.full_notes.push(format!(
                "{}: {}",
                self.full_current,
                model::status_label(&status)
            ));
        }
        if self.full_stop_after {
            self.finish_full(false, true);
            return;
        }
        self.start_next_full();
    }

    fn full_summary(&self, cancelled: bool, browser: bool) -> Value {
        json!({
            "view": "full_test_summary",
            "cancelled": cancelled,
            "browser_launched": browser,
            "total": self.full_total,
            "done": self.full_done,
            "passed": self.full_passed,
            "failed": self.full_failed,
            "observed": self.full_observed,
            "reason": self.full_reason,
            "notes": self.full_notes
        })
    }

    fn full_report_text(&self) -> String {
        let mut text = format!(
            "Diagnostix full test\nQueue: {}/{}\nPassed: {}\nFailed or skipped: {}\nObservations: {}\n",
            self.full_done, self.full_total, self.full_passed, self.full_failed, self.full_observed
        );
        if !self.full_reason.is_empty() {
            text.push_str(&format!("Retry reason: {}\n", self.full_reason));
        }
        if self.full_notes.is_empty() {
            text.push_str("Notes: none\n");
        } else {
            text.push_str("Notes:\n");
            for note in &self.full_notes {
                text.push_str(&format!("- {note}\n"));
            }
        }
        text
    }

    fn save_full_report(&mut self) {
        let path = std::path::Path::new("/run/psx-state/full-test-report.txt");
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        match std::fs::write(path, self.full_report_text()) {
            Ok(()) => {
                if let Some(report) = self.report.as_mut() {
                    if report["view"] == "full_test_summary" {
                        report["saved_path"] = json!(path.display().to_string());
                    }
                }
                self.notice = format!("Report saved: {}", path.display());
            }
            Err(error) => self.notice = format!("Could not save report: {error}"),
        }
    }

    fn finish_full(&mut self, cancelled: bool, browser: bool) {
        self.full_active = false;
        self.full_in_flight = false;
        self.full_queue.clear();
        let summary = self.full_summary(cancelled, browser);
        self.report = Some(summary);
        self.report_scroll = 0;
        self.notice = if cancelled {
            format!(
                "Full test cancelled: {} passed, {} failed/skipped, {} observations. S saves the report.",
                self.full_passed, self.full_failed, self.full_observed
            )
        } else if browser {
            format!(
                "Full test complete: {} passed, {} failed/skipped, {} observations. Browser launched; S saves the report.",
                self.full_passed, self.full_failed, self.full_observed
            )
        } else {
            format!(
                "Full test complete: {} passed, {} failed/skipped, {} observations. S saves the report.",
                self.full_passed, self.full_failed, self.full_observed
            )
        };
    }

    fn cancel_full(&mut self) {
        if !self.full_active {
            return;
        }
        self.full_notes.push("Cancelled by operator".into());
        self.finish_full(true, false);
    }

    fn open_power_page(&mut self) {
        self.exit_dialog = false;
        self.help = false;
        self.report = None;
        self.hardware = None;
        self.diagnostic_screen = DiagnosticScreen::Hidden;
        self.overview = false;
        self.onboarding = false;
        self.full_active = false;
        self.full_queue.clear();
        self.full_in_flight = false;
        self.category = model::CATEGORIES
            .iter()
            .position(|category| *category == model::Category::Power)
            .unwrap_or(9);
        self.selected = 0;
        self.focus = Focus::Actions;
        self.notice = "Power: save reports, then choose a confirmed action.".into();
    }

    fn activate(&mut self) {
        let actions = self.actions();
        let Some(action) = actions.into_iter().nth(self.selected) else {
            return;
        };
        match action.kind {
            ActionKind::FullTest => {
                self.full_reason.clear();
                self.onboarding = false;
                self.category = 0;
                self.selected = 0;
                self.focus = Focus::Actions;
                self.start_full_test();
            }
            ActionKind::ModeChoice => self.choose_mode(),
            ActionKind::AutomaticSummary => {
                self.diagnostic_screen = DiagnosticScreen::Automatic;
                self.automatic_selected = 0;
            }
            ActionKind::Qualification => match HardwareDialog::qualification() {
                Ok(dialog) => self.hardware = Some(dialog),
                Err(message) => self.error = message,
            },
            ActionKind::SettingsGroup(group) => {
                if self.settings_expanded[group.index()] {
                    self.collapse_settings(group);
                } else {
                    self.settings_expanded[group.index()] = true;
                }
            }
            ActionKind::Hardware(section) => {
                self.hardware = Some(HardwareDialog::new(
                    self.state["inventory"].clone(),
                    section,
                ));
            }
            ActionKind::SshSetup => self.open_ssh(),
            ActionKind::Credits => {
                self.report = Some(json!({"view":"creator_credits"}));
                self.report_scroll = 0;
            }
            ActionKind::Power(option) => {
                if !self.demo && option.available(&self.state) && !self.power_pending {
                    self.power_dialog = Some(PowerDialog {
                        operation: option,
                        confirm_selected: false,
                    });
                } else {
                    let reason = if self.demo {
                        "Demo preview only; no power action executes.".into()
                    } else if self.power_pending {
                        "The confirmed power request is being dispatched.".into()
                    } else {
                        option.reason(&self.state)
                    };
                    self.report = Some(
                        json!({"view":"power_unavailable","operation":option.title(),"reason":reason}),
                    );
                    self.report_scroll = 0;
                }
            }
            ActionKind::ReportGroup(group) => {
                self.report_expanded[group] = !self.report_expanded[group];
            }
            ActionKind::Continue => {
                self.choose_mode();
                self.category = 0;
                self.selected = 0;
                self.focus = Focus::Actions;
            }
            ActionKind::Wifi(ssid) => {
                self.form = Some(Form {
                    kind: FormKind::Wifi,
                    fields: vec![ssid, String::new()],
                    selected: 0,
                    error: String::new(),
                });
            }
            ActionKind::Https => {
                self.form = Some(Form {
                    kind: FormKind::Https,
                    fields: vec![String::new(), "psx0".into()],
                    selected: 0,
                    error: String::new(),
                });
            }
            ActionKind::Report(value)
                if value["observation_required"].as_bool().unwrap_or(false)
                    && self.same_boot(&value)
                    && !model::at(&value, "/run_id").is_empty() =>
            {
                self.form = Some(Form {
                    kind: FormKind::Observation {
                        run_id: model::at(&value, "/run_id"),
                    },
                    fields: vec![],
                    selected: 0,
                    error: String::new(),
                });
            }
            ActionKind::Test(value) => {
                let name = model::at(&value, "/run_action");
                let params = value
                    .get("run_params")
                    .cloned()
                    .unwrap_or_else(|| json!({}));
                if name.is_empty() {
                    self.report = Some(value);
                    self.report_scroll = 0;
                } else if name == "test.run" && self.failed_previous(&params) {
                    self.form = Some(Form {
                        kind: FormKind::Retry {
                            action: name,
                            params,
                        },
                        fields: vec![String::new()],
                        selected: 0,
                        error: String::new(),
                    });
                } else {
                    let title = model::at(&value, "/title");
                    self.run(&title, &name, params);
                }
            }
            ActionKind::Information(value) | ActionKind::Report(value) => {
                self.report = Some(value);
                self.report_scroll = 0;
            }
            ActionKind::Request(name, params) => {
                if (name == "test.run" && self.failed_previous(&params))
                    || (name == "quick" && self.quick_needs_reason())
                {
                    self.form = Some(Form {
                        kind: FormKind::Retry {
                            action: name.into(),
                            params,
                        },
                        fields: vec![String::new()],
                        selected: 0,
                        error: String::new(),
                    });
                } else {
                    self.run(&action.title, name, params);
                }
            }
        }
    }

    fn submit_form(&mut self) {
        let Some(mut form) = self.form.take() else {
            return;
        };
        let failure = match &mut form.kind {
            FormKind::SshGithub => {
                let username = form.fields[0].trim();
                if username.is_empty() {
                    Some("Enter your GitHub username".into())
                } else {
                    self.ssh_request("ssh.github.preview", json!({"username":username}));
                    None
                }
            }
            FormKind::Wifi => {
                let ssid = form.fields[0].trim().to_owned();
                if ssid.is_empty() || ssid.len() > 32 {
                    Some("Enter a Wi-Fi name of at most 32 bytes".into())
                } else {
                    let password = std::mem::take(&mut form.fields[1]);
                    self.run(
                        "Join Wi-Fi",
                        "network.wifi.connect",
                        json!({"ssid":ssid,"password":password}),
                    );
                    None
                }
            }
            FormKind::Https => {
                let url = form.fields[0].trim();
                let interface = form.fields[1].trim();
                if !url.starts_with("https://") || interface.is_empty() {
                    Some("Enter an HTTPS URL and interface name".into())
                } else {
                    self.run("HTTPS connection","test.run",json!({"test_id":"ethernet.https","parameters":{"url":url,"interface":interface},"retry_reason":""}));
                    None
                }
            }
            FormKind::Retry { action, params } => {
                let reason = form.fields[0].trim();
                if reason.is_empty() {
                    Some("Record what changed before another attempt".into())
                } else {
                    params["retry_reason"] = json!(reason);
                    self.run("Retry selected test", action, params.clone());
                    None
                }
            }
            FormKind::FullRetry => {
                let reason = form.fields[0].trim();
                if reason.is_empty() {
                    Some("Record what changed before another full run".into())
                } else {
                    self.full_reason = reason.into();
                    self.start_full_test();
                    None
                }
            }
            FormKind::Observation { run_id } => {
                let answer = ["yes", "no", "unsure"][form.selected.min(2)];
                self.run(
                    "Save audio observation",
                    "result.observe",
                    json!({"run_id":run_id,"answer":answer}),
                );
                None
            }
        };
        if let Some(message) = failure {
            form.error = message;
            self.form = Some(form);
        }
    }

    fn form_key(&mut self, key: KeyEvent) {
        if key.code == KeyCode::Esc {
            self.form = None;
            return;
        }
        let Some(form) = self.form.as_mut() else {
            return;
        };
        if matches!(form.kind, FormKind::Observation { .. }) {
            match key.code {
                KeyCode::Up | KeyCode::Left => form.selected = form.selected.saturating_sub(1),
                KeyCode::Down | KeyCode::Right | KeyCode::Tab => {
                    form.selected = (form.selected + 1) % 3
                }
                KeyCode::Enter => self.submit_form(),
                _ => {}
            }
            return;
        }
        let field_count = form.fields.len();
        match key.code {
            KeyCode::Tab | KeyCode::Down => form.selected = (form.selected + 1) % (field_count + 1),
            KeyCode::BackTab | KeyCode::Up => {
                form.selected = (form.selected + field_count) % (field_count + 1)
            }
            KeyCode::Enter => {
                if form.selected < field_count.saturating_sub(1) {
                    form.selected += 1;
                } else {
                    self.submit_form();
                }
            }
            KeyCode::Backspace if form.selected < field_count => {
                form.fields[form.selected].pop();
            }
            KeyCode::Char(c)
                if form.selected < field_count
                    && !c.is_control()
                    && !key
                        .modifiers
                        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
            {
                let limit = match form.kind {
                    FormKind::Wifi if form.selected == 0 => 32,
                    FormKind::Wifi => 128,
                    FormKind::Https if form.selected == 1 => 32,
                    FormKind::SshGithub => 39,
                    _ => 1024,
                };
                if form.fields[form.selected].len() + c.len_utf8() <= limit {
                    form.fields[form.selected].push(c);
                }
            }
            _ => {}
        }
    }

    pub fn key(&mut self, key: KeyEvent) -> bool {
        if key.kind == KeyEventKind::Release
            || (key.kind == KeyEventKind::Repeat && key.code == KeyCode::Enter)
        {
            return false;
        }
        if key.code == KeyCode::Char('c') && key.modifiers.contains(KeyModifiers::CONTROL) {
            return true;
        }
        if matches!(key.code, KeyCode::Char('l' | 'L'))
            && key.modifiers.contains(KeyModifiers::CONTROL)
        {
            self.redraw_requested = true;
            return false;
        }
        if self.form.is_some() {
            self.form_key(key);
            return false;
        }
        if self.ssh_dialog.is_some() {
            self.ssh_key(key);
            return false;
        }
        if self.power_dialog.is_some() {
            match key.code {
                KeyCode::Esc | KeyCode::Char('n') => self.power_dialog = None,
                KeyCode::Left | KeyCode::Right | KeyCode::Tab | KeyCode::BackTab => {
                    let dialog = self.power_dialog.as_mut().unwrap();
                    dialog.confirm_selected = !dialog.confirm_selected;
                }
                KeyCode::Enter => {
                    let dialog = self.power_dialog.take().unwrap();
                    if dialog.confirm_selected
                        && !self.demo
                        && dialog.operation.available(&self.state)
                    {
                        self.run(
                            dialog.operation.title(),
                            "power.execute",
                            json!({"operation":dialog.operation.operation(),"confirm":true}),
                        );
                    }
                }
                _ => {}
            }
            return false;
        }
        if self.hardware.is_some() {
            self.hardware_key(key);
            return false;
        }
        if self.exit_dialog {
            match key.code {
                KeyCode::Enter => return true,
                KeyCode::Esc | KeyCode::Char('n') => self.exit_dialog = false,
                _ => {}
            }
            return false;
        }
        if self.help {
            if matches!(key.code, KeyCode::Esc | KeyCode::F(1) | KeyCode::Enter) {
                self.help = false;
            }
            return false;
        }
        if self.full_active {
            match key.code {
                KeyCode::Esc | KeyCode::Char('c' | 'C') => {
                    self.cancel_full();
                    return false;
                }
                KeyCode::Char('s' | 'S') => {
                    self.save_full_report();
                    return false;
                }
                KeyCode::Char('v' | 'V') => {
                    self.report = Some(self.full_summary(false, false));
                    self.report_scroll = 0;
                    return false;
                }
                _ => {}
            }
        }
        if self.report.is_some() {
            let full_summary = self
                .report
                .as_ref()
                .is_some_and(|value| model::at(value, "/view") == "full_test_summary");
            match key.code {
                KeyCode::Char('u' | 'U') if full_summary => {
                    self.report = None;
                    if let Some(report) = self.issue_report.clone() {
                        self.report = Some(report);
                        self.report_scroll = 0;
                    } else {
                        self.run("Submit report", "support.export", json!({}));
                    }
                }
                KeyCode::Char('r' | 'R') if full_summary => {
                    self.report = None;
                    self.form = Some(Form {
                        kind: FormKind::FullRetry,
                        fields: vec![String::new()],
                        selected: 0,
                        error: String::new(),
                    });
                }
                KeyCode::Char('s' | 'S') if full_summary => self.save_full_report(),
                KeyCode::Esc | KeyCode::Enter | KeyCode::Char('v') => self.report = None,
                KeyCode::Down => self.report_scroll = self.report_scroll.saturating_add(1),
                KeyCode::Up => self.report_scroll = self.report_scroll.saturating_sub(1),
                KeyCode::PageDown => self.report_scroll = self.report_scroll.saturating_add(10),
                KeyCode::PageUp => self.report_scroll = self.report_scroll.saturating_sub(10),
                _ => {}
            }
            return false;
        }
        if self.diagnostic_screen != DiagnosticScreen::Hidden {
            self.diagnostic_key(key);
            return false;
        }
        if !self.onboarding && key.code == KeyCode::F(7) {
            self.overview = !self.overview;
            return false;
        }
        if !self.onboarding && self.overview {
            match key.code {
                KeyCode::Char('q') => self.open_power_page(),
                KeyCode::F(1) => self.help = true,
                KeyCode::Char('a' | 'A')
                | KeyCode::Left
                | KeyCode::Right
                | KeyCode::Tab
                | KeyCode::Esc => self.overview = false,
                KeyCode::Up => self.overview_selected = self.overview_selected.saturating_sub(1),
                KeyCode::Down => {
                    self.overview_selected =
                        (self.overview_selected + 1).min(overview::COMPONENTS.len() - 1)
                }
                KeyCode::Home => self.overview_selected = 0,
                KeyCode::End => self.overview_selected = overview::COMPONENTS.len() - 1,
                KeyCode::PageUp => {
                    self.overview_selected = self.overview_selected.saturating_sub(6)
                }
                KeyCode::PageDown => {
                    self.overview_selected =
                        (self.overview_selected + 6).min(overview::COMPONENTS.len() - 1)
                }
                KeyCode::F(5) => self.refresh(),
                KeyCode::Char('m' | 'M') => self.choose_mode(),
                KeyCode::Enter | KeyCode::F(2) | KeyCode::F(3) | KeyCode::F(4) | KeyCode::F(6) => {
                    let category = match key.code {
                        KeyCode::F(2) => Category::Network,
                        KeyCode::F(3) => Category::Cpu,
                        KeyCode::F(4) => Category::Reports,
                        KeyCode::F(6) => Category::Settings,
                        _ => overview::COMPONENTS[self.overview_selected].category(),
                    };
                    self.overview = false;
                    self.category = CATEGORIES
                        .iter()
                        .position(|value| *value == category)
                        .unwrap_or(0);
                    self.selected = 0;
                    self.focus = Focus::Actions;
                    if category == Category::Reports {
                        self.send("results.list", json!({}));
                    }
                }
                KeyCode::Char('v') => {
                    if let Some(value) = &self.last_report {
                        self.report = Some(value.clone());
                        self.report_scroll = 0;
                    }
                }
                _ => {}
            }
            return false;
        }
        match key.code {
            KeyCode::Char('q') => self.open_power_page(),
            KeyCode::F(1) => self.help = true,
            KeyCode::F(2) => {
                self.category = 4;
                self.selected = 0;
                self.focus = Focus::Actions;
            }
            KeyCode::F(3) => self.start_quick(),
            KeyCode::F(4) => {
                self.onboarding = false;
                self.category = 7;
                self.selected = 0;
                self.focus = Focus::Actions;
                self.send("results.list", json!({}));
            }
            KeyCode::F(5) => self.refresh(),
            KeyCode::F(6) => {
                self.onboarding = false;
                self.category = 8;
                self.selected = 0;
                self.focus = Focus::Actions;
            }
            KeyCode::Char('v') => {
                if let Some(value) = &self.last_report {
                    self.report = Some(value.clone());
                    self.report_scroll = 0;
                }
            }
            KeyCode::Esc => {
                if self.onboarding {
                    self.selected = model::actions(Category::Network, &self.state, true)
                        .iter()
                        .position(|action| matches!(action.kind, ActionKind::Continue))
                        .unwrap_or(0);
                } else {
                    if self.focus == Focus::Categories {
                        self.overview = true;
                    } else {
                        self.focus = Focus::Categories;
                    }
                }
            }
            KeyCode::Tab | KeyCode::BackTab => {
                if !self.onboarding {
                    self.focus = if self.focus == Focus::Actions {
                        Focus::Categories
                    } else {
                        Focus::Actions
                    };
                }
            }
            KeyCode::Left if self.settings_tree_focused() => {
                if let Some(group) = self
                    .actions()
                    .get(self.selected)
                    .and_then(model::settings_group)
                {
                    self.collapse_settings(group);
                }
            }
            KeyCode::Right if self.settings_tree_focused() => self.settings_right(),
            KeyCode::Left if self.report_tree_focused() => self.collapse_report_group(),
            KeyCode::Right if self.report_tree_focused() => self.expand_report_group(),
            KeyCode::Left if !self.onboarding => {
                self.category = (self.category + CATEGORIES.len() - 1) % CATEGORIES.len();
                self.selected = 0;
            }
            KeyCode::Right if !self.onboarding => {
                self.category = (self.category + 1) % CATEGORIES.len();
                self.selected = 0;
            }
            KeyCode::Up if self.report_tree_focused() && self.selected == 0 => {
                self.focus = Focus::Categories;
            }
            KeyCode::Up => self.selected = self.selected.saturating_sub(1),
            KeyCode::Down => {
                self.focus = Focus::Actions;
                self.selected = (self.selected + 1).min(self.actions().len().saturating_sub(1));
            }
            KeyCode::Home => self.selected = 0,
            KeyCode::End => self.selected = self.actions().len().saturating_sub(1),
            KeyCode::Enter => {
                if self.focus == Focus::Categories {
                    self.focus = Focus::Actions;
                } else {
                    self.activate();
                }
            }
            _ => {}
        }
        false
    }

    pub fn handle_event(&mut self, event: Event) -> bool {
        match event {
            Event::Key(key) => self.key(key),
            Event::Resize(width, height) => {
                self.viewport = (width, height);
                let limit = ui::hardware_scroll_limit(self);
                if let Some(dialog) = self.hardware.as_mut() {
                    dialog.scroll = dialog.scroll.min(limit);
                }
                self.redraw_requested = true;
                false
            }
            _ => false,
        }
    }

    fn take_redraw(&mut self) -> bool {
        std::mem::take(&mut self.redraw_requested)
    }

    fn hardware_key(&mut self, key: KeyEvent) {
        let limit = ui::hardware_scroll_limit(self);
        let Some(dialog) = self.hardware.as_mut() else {
            return;
        };
        match key.code {
            KeyCode::Esc | KeyCode::Char('q') | KeyCode::Left => {
                if dialog.raw.take().is_some() {
                    dialog.scroll = dialog.detail_scroll;
                } else if dialog.detail {
                    dialog.detail = false;
                    dialog.scroll = 0;
                } else {
                    self.hardware = None;
                    self.redraw_requested = true;
                }
            }
            KeyCode::Char('a' | 'A') => {
                if dialog.raw.take().is_some() {
                    dialog.scroll = dialog.detail_scroll;
                } else {
                    dialog.detail = true;
                    dialog.detail_scroll = dialog.scroll;
                    dialog.scroll = 0;
                    let value = if let Some(sources) = &dialog.raw_sources {
                        sources[dialog.selected].clone()
                    } else {
                        hardware::raw(hardware::SECTIONS[dialog.selected], &dialog.inventory)
                    };
                    dialog.raw = Some(
                        serde_json::to_string_pretty(&value)
                            .unwrap_or_else(|_| "Unavailable".into()),
                    );
                }
            }
            KeyCode::Enter | KeyCode::Right if !dialog.detail => {
                dialog.detail = true;
                dialog.scroll = 0;
            }
            KeyCode::Home => {
                if dialog.detail {
                    dialog.scroll = 0;
                } else {
                    dialog.selected = 0;
                }
            }
            KeyCode::End => {
                if dialog.detail {
                    dialog.scroll = limit;
                } else {
                    dialog.selected = dialog.pages.len().saturating_sub(1);
                }
            }
            KeyCode::Up => {
                if dialog.detail {
                    dialog.scroll = dialog.scroll.saturating_sub(1);
                } else {
                    dialog.selected = dialog.selected.saturating_sub(1);
                }
            }
            KeyCode::Down => {
                if dialog.detail {
                    dialog.scroll = dialog.scroll.saturating_add(1).min(limit);
                } else {
                    dialog.selected =
                        (dialog.selected + 1).min(dialog.pages.len().saturating_sub(1));
                }
            }
            KeyCode::PageUp => {
                if dialog.detail {
                    dialog.scroll = dialog.scroll.saturating_sub(8);
                } else {
                    dialog.selected = dialog.selected.saturating_sub(5);
                }
            }
            KeyCode::PageDown => {
                if dialog.detail {
                    dialog.scroll = dialog.scroll.saturating_add(8).min(limit);
                } else {
                    dialog.selected =
                        (dialog.selected + 5).min(dialog.pages.len().saturating_sub(1));
                }
            }
            _ => {}
        }
        let limit = ui::hardware_scroll_limit(self);
        if let Some(dialog) = self.hardware.as_mut() {
            dialog.scroll = dialog.scroll.min(limit);
        }
    }

    fn complete_job(&mut self, value: Value) {
        let result = value
            .get("result")
            .filter(|v| !v.is_null())
            .cloned()
            .unwrap_or_else(|| value.clone());
        let message = model::at(&result, "/message");
        let error = model::at(&value, "/error");
        let status = model::at(&result, "/status");
        let description = if !message.is_empty() {
            message
        } else if !error.is_empty() {
            error
        } else {
            "Action finished. Press V to inspect its report.".into()
        };
        self.notice = if status.is_empty() {
            description
        } else {
            format!("{} · {description}", model::status_label(&status))
        };
        if result["observation_required"].as_bool().unwrap_or(false) {
            let run_id = model::at(&result, "/run_id");
            if !run_id.is_empty() {
                self.form = Some(Form {
                    kind: FormKind::Observation { run_id },
                    fields: vec![],
                    selected: 0,
                    error: String::new(),
                });
            }
        }
        if result["retry_allowed"].as_bool() == Some(true) {
            self.power_pending = false;
        }
        let automatic = model::at(&result, "/test_id") == "diagnostics.auto"
            || self.action_name == "diagnostics.auto";
        if automatic {
            self.automatic_result = Some(result.clone());
            self.automatic_selected = 0;
            if let Some(report) = result
                .get("issue_report")
                .filter(|report| model::IssueReport::from_value(report).is_some())
            {
                self.issue_report = Some(report.clone());
            }
        }
        if self.action_name == "support.export" || model::IssueReport::from_value(&result).is_some()
        {
            if model::IssueReport::from_value(&result).is_some() {
                self.issue_report = Some(result.clone());
            }
            if !self.full_active {
                self.report = Some(result.clone());
                self.report_scroll = 0;
            }
        }
        let full_result = result.clone();
        self.last_report = Some(result);
        self.active_job = None;
        self.action_pending = false;
        self.job_pending = false;
        self.action_name.clear();
        self.refresh();
        self.advance_full(&full_result);
    }

    fn refresh_power_request(&mut self) {
        let Some(current) = self.state.pointer("/power/current_request") else {
            return;
        };
        let boot = model::at(&self.state, "/identity/boot_id");
        let run = model::at(current, "/run_id");
        let test = model::at(current, "/test_id");
        let message = model::at(current, "/message");
        if boot.is_empty()
            || model::at(current, "/boot_id") != boot
            || run.is_empty()
            || message.is_empty()
            || !matches!(
                test.as_str(),
                "power.shutdown.request" | "power.reboot-orbis.request"
            )
            || model::at(current, "/status") != "INCONCLUSIVE"
            || !matches!(
                model::at(current, "/dispatch_state").as_str(),
                "preparing"
                    | "not_sent"
                    | "issuing_forced_icc_restart"
                    | "issuing_kernel_restart"
                    | "issuing_shutdown"
                    | "request_returned_physical_outcome_unobserved"
                    | "failed_no_retry"
            )
        {
            return;
        }
        if current["retry_allowed"].as_bool() == Some(true) {
            self.power_pending = false;
        }
        let Some(previous) = self.last_report.as_mut() else {
            return;
        };
        let previous_boot = model::at(previous, "/identity/boot_id");
        let previous_boot = if previous_boot.is_empty() {
            model::at(previous, "/boot_id")
        } else {
            previous_boot
        };
        if previous_boot != boot
            || model::at(previous, "/run_id") != run
            || model::at(previous, "/test_id") != test
        {
            return;
        }
        // This replaces the pre-dispatch snapshot only for the same power
        // request. Ordinary completed jobs remain complete and are not polled.
        let changed = ["message", "dispatch_state", "status"]
            .iter()
            .any(|key| previous[*key] != current[*key]);
        if changed {
            for key in ["message", "dispatch_state", "status"] {
                previous[key] = current[key].clone();
            }
            self.notice = format!("{} · {message}", model::status_label("INCONCLUSIVE"));
        }
        if let Some(view) = self.report.as_mut() {
            if model::at(view, "/run_id") == run && model::at(view, "/test_id") == test {
                for key in ["message", "dispatch_state", "status"] {
                    view[key] = current[key].clone();
                }
            }
        }
    }

    fn receive(&mut self, reply: ipc::Reply) {
        // An interrupted job must not overwrite the terminal power request.
        if self.power_pending
            && !["power.execute", "state", "job.get"].contains(&reply.action.as_str())
        {
            return;
        }
        if reply.action.starts_with("ssh.") {
            self.receive_ssh(reply);
            return;
        }
        if reply.action == "state" {
            self.state_pending = false;
        }
        if reply.action == "job.get" {
            self.job_pending = false;
        }
        let value = match reply.result {
            Ok(v) => {
                self.connected = true;
                if self.error_action == reply.action {
                    self.error.clear();
                    self.error_action.clear();
                }
                v
            }
            Err(error) => {
                self.error = error;
                self.error_action = reply.action.clone();
                if reply.action == "power.execute" {
                    self.power_pending = false;
                }
                if reply.action == "state" {
                    self.connected = false;
                } else if reply.action != "job.get" {
                    self.action_pending = false;
                }
                return;
            }
        };
        match reply.action.as_str() {
            "state" => {
                if !value.is_object() {
                    self.error = "Controller returned an invalid state object".into();
                    self.connected = false;
                    return;
                }
                let mut value = value;
                let settings_selection =
                    if !self.onboarding && self.category() == Category::Settings {
                        self.actions().get(self.selected).and_then(|item| {
                            model::settings_key(item).map(|key| (key, model::settings_group(item)))
                        })
                    } else {
                        None
                    };
                let network_selection = if self.category() == Category::Network {
                    self.actions()
                        .get(self.selected)
                        .and_then(network_action_key)
                } else {
                    None
                };
                value["results"] = merged_results(&value["results"], &self.state["results"]);
                let continue_first = |state: &Value| {
                    model::actions(Category::Network, state, true)
                        .first()
                        .is_some_and(|action| matches!(action.kind, ActionKind::Continue))
                };
                let newly_connected =
                    self.onboarding && !continue_first(&self.state) && continue_first(&value);
                if newly_connected {
                    self.selected = 0;
                }
                self.state = value;
                if let Some(key) = network_selection.filter(|_| !newly_connected) {
                    let actions = self.actions();
                    self.selected = actions.iter().position(|item| network_action_key(item).as_ref() == Some(&key))
                        .or_else(|| actions.iter().position(|item| matches!(item.kind, ActionKind::Continue)))
                        .or_else(|| actions.iter().position(|item| matches!(&item.kind,
                            ActionKind::Request("test.run", params) if params["test_id"] == "ethernet.status")))
                        .unwrap_or(0);
                }
                if let Some((key, group)) = settings_selection {
                    let actions = self.actions();
                    if let Some(index)=actions.iter().position(|item|model::settings_key(item).as_deref()==Some(key.as_str()))
                        .or_else(||group.and_then(|group|actions.iter().position(|item|matches!(item.kind,ActionKind::SettingsGroup(value) if value==group)))){
                        self.selected=index;
                    }
                }
                self.selected = self.selected.min(self.actions().len().saturating_sub(1));
                if self.active_job.is_none() && !self.power_pending {
                    if let Some(job) = model::array(&self.state, "/jobs").iter().find(|j| {
                        ["running", "queued"].contains(&model::at(j, "/state").as_str())
                            && !j["id"].is_null()
                    }) {
                        self.active_job = Some(Job {
                            id: job["id"].clone(),
                            title: unknown_job_title(job),
                            started: Instant::now(),
                            data: job.clone(),
                        });
                        self.last_job = Instant::now()
                            .checked_sub(Duration::from_secs(2))
                            .unwrap_or_else(Instant::now);
                    }
                }
                self.refresh_power_request();
            }
            "results.list" => {
                let fresh = if value.is_array() {
                    value
                } else {
                    value.get("results").cloned().unwrap_or(json!([]))
                };
                self.state["results"] = merged_results(&fresh, &self.state["results"]);
                self.action_pending = false;
            }
            "job.get" => {
                if self.power_pending
                    && self
                        .active_job
                        .as_ref()
                        .is_none_or(|job| value["id"] != job.id)
                {
                    return;
                }
                let status = model::at(&value, "/state");
                if [
                    "complete",
                    "completed",
                    "done",
                    "failed",
                    "error",
                    "finished",
                ]
                .contains(&status.as_str())
                {
                    self.complete_job(value);
                } else if let Some(job) = self.active_job.as_mut() {
                    job.data = value;
                }
            }
            _ => {
                self.action_pending = false;
                let id = value.get("id").or_else(|| value.get("job_id")).cloned();
                if let Some(id) = id {
                    self.active_job = Some(Job {
                        id,
                        title: self.action_title.clone(),
                        started: Instant::now(),
                        data: value,
                    });
                    self.last_job = Instant::now()
                        .checked_sub(Duration::from_secs(2))
                        .unwrap_or_else(Instant::now);
                } else {
                    self.complete_job(value);
                }
            }
        }
    }

    pub fn tick(&mut self) {
        if self.ssh_dialog.as_ref().is_some_and(|dialog| {
            dialog.screen == SshScreen::Password
                && dialog.expires.is_some_and(|time| Instant::now() >= time)
        }) {
            self.close_ssh();
            self.notice = "The temporary SSH password display expired.".into();
        }
        self.ticks = self.ticks.wrapping_add(1);
        let replies: Vec<_> = self
            .receiver
            .as_ref()
            .map(|rx| rx.try_iter().collect())
            .unwrap_or_default();
        for reply in replies {
            self.receive(reply);
        }
        if self.demo {
            if self
                .demo_action
                .as_ref()
                .map(|(start, _)| start.elapsed() > Duration::from_millis(750))
                .unwrap_or(false)
            {
                let (_, action) = self.demo_action.take().unwrap();
                if action == "network.ethernet.connect" || action == "network.wifi.connect" {
                    self.state["network"]["interfaces"][0]["ipv4"] = json!(["192.0.2.7/24"]);
                    self.state["network"]["interfaces"][0]["state"] = json!("connected");
                }
                self.action_pending = false;
                let result = if action == "diagnostics.auto" {
                    json!({"test_id":"diagnostics.auto","status":"INCONCLUSIVE",
                        "message":"Simulated automatic run. No hardware action executed.",
                        "steps":model::AUTOMATIC_STEPS.iter().map(|(id, _)|
                            json!({"test_id":id,"state":"completed","status":"INCONCLUSIVE","message":"Simulated only"})).collect::<Vec<_>>(),
                        "hardware_acceptance":false})
                } else if action == "support.export" {
                    json!({"view":"issue_report_demo","status":"INCONCLUSIVE",
                        "message":"Demo preview only. No ZIP was created or uploaded.","hardware_acceptance":false})
                } else {
                    json!({"status":"INCONCLUSIVE","message":"Demo preview only. No hardware action executed.","hardware_acceptance":false})
                };
                self.complete_job(result);
            }
            return;
        }
        if self.last_state.elapsed() > Duration::from_secs(2) {
            self.refresh();
        }
        if self.active_job.is_some()
            && !self.job_pending
            && self.last_job.elapsed() > Duration::from_millis(700)
        {
            self.job_pending = true;
            self.last_job = Instant::now();
            let id = self
                .active_job
                .as_ref()
                .map(|j| j.id.clone())
                .unwrap_or(Value::Null);
            self.send("job.get", json!({"id":id}));
        }
    }
}

fn unknown_job_title(job: &Value) -> String {
    let title = model::at(job, "/title");
    if title.is_empty() {
        "Existing diagnostic action".into()
    } else {
        title
    }
}

struct Screen;
impl Screen {
    fn restore() {
        let _ = execute!(
            io::stdout(),
            SetAttribute(Attribute::Reset),
            ResetColor,
            Show,
            LeaveAlternateScreen,
            SetAttribute(Attribute::Reset),
            ResetColor,
            Show
        );
        let _ = terminal::disable_raw_mode();
    }
}
impl Drop for Screen {
    fn drop(&mut self) {
        Self::restore();
    }
}

fn network_action_key(action: &model::Action) -> Option<Value> {
    match &action.kind {
        ActionKind::Request(name, params) => Some(json!(["request", name, params])),
        ActionKind::Wifi(ssid) => Some(json!(["wifi", ssid])),
        ActionKind::Https => Some(json!(["https"])),
        ActionKind::Continue => Some(json!(["continue"])),
        ActionKind::Information(_) => Some(json!(["information"])),
        ActionKind::SshSetup => Some(json!(["ssh-setup"])),
        _ => None,
    }
}

fn merged_results(fresh: &Value, cached: &Value) -> Value {
    let mut seen = std::collections::HashSet::new();
    let mut results: Vec<_> = model::array(fresh, "")
        .iter()
        .chain(model::array(cached, "").iter())
        .filter(|record| {
            let id = model::at(record, "/run_id");
            id.is_empty() || seen.insert(id)
        })
        .cloned()
        .collect();
    results.sort_by(|a, b| model::report_time(b).cmp(model::report_time(a)));
    results.truncate(200);
    Value::Array(results)
}

fn clean_display(output: &mut impl io::Write) -> io::Result<()> {
    execute!(
        output,
        SetAttribute(Attribute::Reset),
        ResetColor,
        Clear(ClearType::All),
        Clear(ClearType::Purge),
        MoveTo(0, 0)
    )
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let mut socket = PathBuf::from("/run/psx-diagnostics/control.sock");
    let mut demo = false;
    let mut render = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--socket" => socket = PathBuf::from(args.next().ok_or("--socket needs a path")?),
            "--demo" => demo = true,
            "--render-demo" => {
                demo = true;
                render = Some(args.next().unwrap_or_else(|| "110x30".into()));
            }
            "--version" => {
                println!(
                    "Diagnostix {} (psx-diagnostics-tui)",
                    env!("CARGO_PKG_VERSION")
                );
                return Ok(());
            }
            "--help" | "-h" => {
                println!(
                    "Diagnostix\npsx-diagnostics-tui [--socket PATH] [--demo] [--render-demo WIDTHxHEIGHT]\nNative local-console / SSH diagnostic client. Arrows, Enter, Esc; F1 help.\nDemo mode uses sample data and never contacts a console."
                );
                return Ok(());
            }
            _ => return Err(format!("Unknown argument: {arg}").into()),
        }
    }
    let mut app = App::new(socket, demo);
    if let Some(size) = render {
        let (width, height) = size.split_once('x').ok_or("Use WIDTHxHEIGHT")?;
        let (width, height): (u16, u16) = (width.parse()?, height.parse()?);
        if width > 240 || height > 100 || width == 0 || height == 0 {
            return Err("Demo dimensions must fit 240x100".into());
        }
        let backend = ratatui::backend::TestBackend::new(width, height);
        let mut term = Terminal::new(backend)?;
        term.draw(|frame| ui::draw(frame, &app))?;
        for line in term.backend().buffer().content.chunks(width as usize) {
            let text: String = line.iter().map(|cell| cell.symbol()).collect();
            println!("{}", text.trim_end());
        }
        return Ok(());
    }
    terminal::enable_raw_mode()?;
    let _screen = Screen;
    let previous_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        Screen::restore();
        previous_hook(info);
    }));
    execute!(io::stdout(), EnterAlternateScreen)?;
    let mut terminal = Terminal::new(CrosstermBackend::new(io::stdout()))?;
    let mut previous_palette = String::new();
    loop {
        let size = terminal.size()?;
        app.viewport = (size.width, size.height);
        app.tick();
        if std::env::var("TERM").unwrap_or_default() == "linux" {
            let palette = ui::console_palette(&app.state);
            if palette != previous_palette {
                io::stdout().write_all(palette.as_bytes())?;
                io::stdout().flush()?;
                previous_palette = palette;
                app.redraw_requested = true;
            }
        }
        if app.take_redraw() {
            clean_display(&mut io::stdout())?;
            // Fullscreen resize invalidates Ratatui's frame cache without the
            // cursor-position query performed by Terminal::clear(). We already
            // moved home, and must not wait for a DSR reply from a minimal VT.
            terminal.resize(terminal.size()?.into())?;
        }
        terminal.draw(|frame| ui::draw(frame, &app))?;
        if event::poll(Duration::from_millis(100))? {
            if app.handle_event(event::read()?) {
                break;
            }
        }
    }
    terminal.show_cursor()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }
    #[test]
    fn first_screen_always_requires_explicit_onboarding_continue() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        assert!(app.onboarding);
        assert!(app.category() == Category::Network);
        app.key(key(KeyCode::Esc));
        app.key(key(KeyCode::Enter));
        assert!(!app.onboarding);
        assert!(app.category() == Category::System);
        assert!(app.diagnostic_screen == DiagnosticScreen::Choose);
        assert_eq!(app.mode_selected, 1);
        assert!(!app.action_pending);
    }

    fn controlled_app() -> (App, Receiver<ipc::Request>) {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        let (sender, requests) = std::sync::mpsc::channel();
        app.demo = false;
        app.sender = Some(sender);
        app.connected = true;
        app.last_state = Instant::now();
        (app, requests)
    }

    fn issue_report_fixture() -> Value {
        json!({"status":"READY","report_id":"fixture-report",
            "filename":"diagnostix-fixture-report.zip",
            "path":"/run/psx-diagnostics/support/diagnostix-fixture-report.zip",
            "sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "bytes":12345,"download_url":"/api/support/reports/fixture-report.zip",
            "message":"Report archive created. Attach it to your GitHub issue."})
    }

    #[test]
    fn mode_choice_and_manual_navigation_never_submit_tests() {
        let (mut app, requests) = controlled_app();
        app.key(key(KeyCode::Esc));
        app.key(key(KeyCode::Enter));
        assert!(app.diagnostic_screen == DiagnosticScreen::Choose);
        assert_eq!(app.mode_selected, 1);
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Ok(model::demo_state()),
        });
        app.tick();
        app.key(key(KeyCode::Home));
        app.key(key(KeyCode::F(3)));
        assert_eq!(app.mode_selected, 0);
        assert!(requests.try_recv().is_err());
        app.key(key(KeyCode::Esc));
        assert_eq!(app.mode_selected, 1);
        app.key(key(KeyCode::Enter));
        assert!(app.overview);
        assert!(app.diagnostic_screen == DiagnosticScreen::Hidden);
        assert!(!app.busy());
        assert!(requests.try_recv().is_err());
        app.key(key(KeyCode::Char('m')));
        assert!(app.diagnostic_screen == DiagnosticScreen::Choose);
        assert_eq!(app.mode_selected, 1);
        app.key(key(KeyCode::Enter));
        app.key(key(KeyCode::F(7)));
        app.selected = app
            .actions()
            .iter()
            .position(|action| matches!(action.kind, ActionKind::ModeChoice))
            .unwrap();
        app.focus = Focus::Actions;
        app.key(key(KeyCode::Enter));
        assert!(app.diagnostic_screen == DiagnosticScreen::Choose);
        assert!(requests.try_recv().is_err());
    }

    #[test]
    fn automatic_starts_only_on_explicit_enter_and_refresh_only_polls() {
        let (mut app, requests) = controlled_app();
        app.key(key(KeyCode::Esc));
        app.key(key(KeyCode::Enter));
        app.key(key(KeyCode::Up));
        let mut repeated = key(KeyCode::Enter);
        repeated.kind = KeyEventKind::Repeat;
        app.key(repeated);
        assert!(requests.try_recv().is_err());
        app.key(key(KeyCode::Enter));
        let request = requests.try_recv().unwrap();
        assert_eq!(request.action, "diagnostics.auto");
        assert_eq!(request.params, json!({}));
        assert!(app.diagnostic_screen == DiagnosticScreen::Automatic);
        assert!(app.action_pending);
        app.key(key(KeyCode::Enter));
        app.key(repeated);
        assert!(requests.try_recv().is_err());
        app.receive(ipc::Reply {
            action: "diagnostics.auto".into(),
            result: Ok(json!({"id":"auto-job","state":"running"})),
        });
        app.key(key(KeyCode::F(5)));
        assert_eq!(requests.try_recv().unwrap().action, "state");
        app.tick();
        let request = requests.try_recv().unwrap();
        assert_eq!(request.action, "job.get");
        assert_eq!(request.params, json!({"id":"auto-job"}));
        app.receive(ipc::Reply {
            action: "job.get".into(),
            result: Ok(json!({"id":"auto-job","state":"running","output":[
                "{\"step_current\":2,\"step_total\":6,\"step_title\":\"CPU\"}"]})),
        });
        assert!(app.automatic_running());
        app.key(key(KeyCode::Enter));
        app.key(key(KeyCode::F(5)));
        assert!(requests.try_recv().is_err());
        let report = issue_report_fixture();
        app.receive(ipc::Reply {
            action: "job.get".into(),
            result: Ok(json!({
            "id":"auto-job","state":"completed","result":{
                "test_id":"diagnostics.auto","status":"BLOCKED","message":"Stopped at CPU.",
                "steps":[{"test_id":"cpu.compute","state":"completed","status":"BLOCKED"},
                         {"test_id":"memory.pattern","state":"not_run"}],
                "issue_report":report,"hardware_acceptance":false}})),
        });
        assert!(!app.busy());
        assert!(!app.automatic_running());
        assert!(app.diagnostic_screen == DiagnosticScreen::Automatic);
        assert_eq!(app.latest_automatic().unwrap()["status"], "BLOCKED");
        assert_eq!(app.latest_issue_report().unwrap(), &issue_report_fixture());
        app.key(key(KeyCode::Enter));
        assert_eq!(app.report.as_ref().unwrap(), &issue_report_fixture());
        assert!(requests.try_recv().is_err());
    }

    #[test]
    fn report_export_is_one_async_action_without_test_or_kernel_log_requirements() {
        let (mut app, requests) = controlled_app();
        app.onboarding = false;
        app.category = 7;
        app.state = json!({"results":[],"inventory":{},"kernel_log":{"available":false}});
        app.selected = app
            .actions()
            .iter()
            .position(|action| matches!(action.kind, ActionKind::Request("support.export", _)))
            .unwrap();
        app.key(key(KeyCode::Enter));
        let request = requests.try_recv().unwrap();
        assert_eq!(request.action, "support.export");
        assert_eq!(request.params, json!({}));
        app.key(key(KeyCode::Enter));
        assert!(requests.try_recv().is_err());
        app.receive(ipc::Reply {
            action: "support.export".into(),
            result: Ok(json!({"id":"export-job","state":"running"})),
        });
        assert_eq!(app.active_job.as_ref().unwrap().id, "export-job");
        app.receive(ipc::Reply {
            action: "job.get".into(),
            result: Ok(json!({
            "id":"export-job","state":"completed","result":issue_report_fixture()})),
        });
        assert!(!app.busy());
        assert_eq!(app.report.as_ref().unwrap(), &issue_report_fixture());
        assert_eq!(app.latest_issue_report().unwrap(), &issue_report_fixture());
        assert_eq!(model::status_label("READY"), "Report ready");
        app.key(key(KeyCode::Esc));
        app.selected = app
            .actions()
            .iter()
            .position(|action| action.title == "View issue report")
            .unwrap();
        app.key(key(KeyCode::Enter));
        assert_eq!(app.report.as_ref().unwrap(), &issue_report_fixture());
        let requests = requests
            .try_iter()
            .map(|request| request.action)
            .collect::<Vec<_>>();
        assert_eq!(requests, ["state"]);
        assert!(model::array(&app.state, "/results").is_empty());
    }

    #[test]
    fn automatic_metadata_is_optional_and_demo_never_creates_an_archive_or_pass() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        assert!(app.latest_automatic().is_none());
        assert!(app.latest_issue_report().is_none());
        app.state["automatic"] = json!({"test_id":"diagnostics.auto","status":"BLOCKED",
            "issue_report":issue_report_fixture()});
        assert_eq!(app.latest_automatic().unwrap()["status"], "BLOCKED");
        assert!(app.latest_issue_report().is_some());
        app.state.as_object_mut().unwrap().remove("automatic");
        app.choose_mode();
        app.mode_selected = 0;
        app.key(key(KeyCode::Enter));
        app.demo_action.as_mut().unwrap().0 = Instant::now() - Duration::from_secs(1);
        app.tick();
        assert_eq!(app.latest_automatic().unwrap()["status"], "INCONCLUSIVE");
        assert_eq!(
            app.latest_automatic().unwrap()["hardware_acceptance"],
            false
        );
        assert!(app.latest_issue_report().is_none());
        app.key(key(KeyCode::Enter));
        app.demo_action.as_mut().unwrap().0 = Instant::now() - Duration::from_secs(1);
        app.tick();
        assert_eq!(app.report.as_ref().unwrap()["view"], "issue_report_demo");
        assert!(app.latest_issue_report().is_none());
        assert!(!app.state.to_string().contains(".zip"));
    }
    #[test]
    fn connected_onboarding_defaults_and_escape_select_continue() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.selected = 2;
        let mut state = model::demo_state();
        state["network"]["interfaces"][0]["state"] = json!("connected");
        state["network"]["interfaces"][0]["ipv4"] = json!(["192.0.2.7"]);
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Ok(state),
        });
        assert_eq!(app.selected, 0);
        assert!(matches!(
            app.actions()[app.selected].kind,
            ActionKind::Continue
        ));
        app.selected = 1;
        assert_eq!(app.actions()[app.selected].title, "Disconnect Ethernet");
        app.key(key(KeyCode::Esc));
        assert_eq!(app.selected, 0);
        app.key(key(KeyCode::Enter));
        assert!(!app.onboarding);
        assert!(app.category() == Category::System);
        assert!(app.active_job.is_none());
    }
    #[test]
    fn network_scan_never_becomes_disconnect_when_connection_state_changes() {
        for onboarding in [false, true] {
            let mut app = App::new(PathBuf::from("/unreachable"), true);
            app.onboarding = onboarding;
            app.state["network"]["interfaces"] = json!([
                {"name":"psx0","type":"ethernet","state":"connected"},
                {"name":"wlan0","type":"wifi","state":"disconnected"}
            ]);
            app.selected = app
                .actions()
                .iter()
                .position(|item| matches!(item.kind, ActionKind::Request("network.scan", _)))
                .unwrap();
            let mut next = app.state.clone();
            next["network"]["interfaces"][1]["state"] = json!("connected");
            app.receive(ipc::Reply {
                action: "state".into(),
                result: Ok(next),
            });
            let actions = app.actions();
            if onboarding {
                assert!(matches!(actions[app.selected].kind, ActionKind::Continue));
            } else {
                assert!(matches!(&actions[app.selected].kind,
                    ActionKind::Request("test.run", params) if params["test_id"] == "ethernet.status"));
            }
            assert!(app.active_job.is_none());
            assert!(!app.action_pending);
        }
    }
    #[test]
    fn network_access_point_selection_follows_ssid_across_scan_reordering() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.onboarding = false;
        app.state["network"]["wifi"]["access_points"] = json!([
            {"ssid":"intended","signal":"80","security":"WPA2"},
            {"ssid":"different","signal":"70","security":"WPA2"}
        ]);
        app.selected = app
            .actions()
            .iter()
            .position(|item| matches!(&item.kind, ActionKind::Wifi(ssid) if ssid == "intended"))
            .unwrap();
        let before = app.selected;
        let mut next = app.state.clone();
        next["network"]["wifi"]["access_points"]
            .as_array_mut()
            .unwrap()
            .reverse();
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Ok(next),
        });
        assert_ne!(app.selected, before);
        assert!(matches!(&app.actions()[app.selected].kind,
            ActionKind::Wifi(ssid) if ssid == "intended"));
        app.activate();
        assert_eq!(app.form.as_ref().unwrap().fields[0], "intended");
        assert!(app.active_job.is_none());
    }
    #[test]
    fn network_vanished_ssid_selects_continue_in_connected_onboarding() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.state["network"]["interfaces"][0]["state"] = json!("connected");
        app.state["network"]["wifi"]["access_points"] = json!([
            {"ssid":"gone","signal":"80","security":"WPA2"}
        ]);
        app.selected = app
            .actions()
            .iter()
            .position(|item| matches!(&item.kind, ActionKind::Wifi(ssid) if ssid == "gone"))
            .unwrap();
        let mut next = app.state.clone();
        next["network"]["wifi"]["access_points"] = json!([]);
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Ok(next),
        });
        assert!(matches!(
            app.actions()[app.selected].kind,
            ActionKind::Continue
        ));
        assert!(!app.action_pending);
    }
    #[test]
    fn network_keeps_existing_ssh_and_ethernet_actions_as_wifi_rows_disappear() {
        for select_ssh in [false, true] {
            let mut app = App::new(PathBuf::from("/unreachable"), true);
            app.onboarding = false;
            app.state["network"]["interfaces"] = json!([
                {"name":"psx0","type":"ethernet","state":"connected"},
                {"name":"wlan0","type":"wifi","state":"disconnected"}
            ]);
            app.selected = app
                .actions()
                .iter()
                .position(|item| {
                    if select_ssh {
                        matches!(item.kind, ActionKind::SshSetup)
                    } else {
                        matches!(
                            item.kind,
                            ActionKind::Request("network.ethernet.disconnect", _)
                        )
                    }
                })
                .unwrap();
            let key = network_action_key(&app.actions()[app.selected]);
            let mut next = app.state.clone();
            next["network"]["interfaces"][1]["state"] = json!("connected");
            app.receive(ipc::Reply {
                action: "state".into(),
                result: Ok(next),
            });
            assert_eq!(network_action_key(&app.actions()[app.selected]), key);
            assert!(!app.action_pending);
        }
    }
    #[test]
    fn wifi_password_is_scoped_to_form_and_cancel_discards_it() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.selected = 2;
        app.key(key(KeyCode::Enter));
        app.form.as_mut().unwrap().fields = vec!["SSID".into(), "private password".into()];
        app.key(key(KeyCode::Esc));
        assert!(app.form.is_none());
        assert!(!app.state.to_string().contains("private password"));
    }
    #[test]
    fn changing_category_during_a_job_keeps_the_job_alive() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.onboarding = false;
        app.active_job = Some(Job {
            id: json!("job1"),
            title: "work".into(),
            started: Instant::now(),
            data: json!({}),
        });
        app.key(key(KeyCode::Right));
        app.key(key(KeyCode::Right));
        assert!(app.active_job.is_some());
    }
    #[test]
    fn reconnecting_client_adopts_the_existing_controller_job() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Ok(
                json!({"jobs":[{"id":"existing","state":"running","title":"Graphics test"}]}),
            ),
        });
        assert_eq!(app.active_job.as_ref().unwrap().id, "existing");
        assert!(app.busy());
    }
    #[test]
    fn explicit_manual_choice_opens_overview_and_advanced_switch_preserves_state() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        assert!(app.onboarding);
        app.key(key(KeyCode::Esc));
        app.key(key(KeyCode::Enter));
        assert!(!app.onboarding);
        assert!(app.diagnostic_screen == DiagnosticScreen::Choose);
        app.key(key(KeyCode::Enter));
        assert!(app.overview);
        app.active_job = Some(Job {
            id: json!("working"),
            title: "Existing check".into(),
            started: Instant::now(),
            data: json!({}),
        });
        app.last_report = Some(json!({"run_id":"saved"}));
        app.report_expanded[0] = true;
        app.settings_expanded[1] = true;
        app.category = 8;
        app.selected = 2;
        app.focus = Focus::Actions;
        app.key(key(KeyCode::Down));
        assert_eq!(app.overview_selected, 1);
        app.key(key(KeyCode::F(7)));
        assert!(!app.overview);
        assert_eq!(app.category, 8);
        assert_eq!(app.selected, 2);
        app.key(key(KeyCode::F(7)));
        assert!(app.overview);
        assert_eq!(app.overview_selected, 1);
        assert_eq!(app.active_job.as_ref().unwrap().id, "working");
        assert_eq!(app.last_report.as_ref().unwrap()["run_id"], "saved");
        assert!(app.report_expanded[0]);
        assert!(app.settings_expanded[1]);
        assert_eq!(app.next_id, 0);
        assert!(!app.action_pending);
    }
    #[test]
    fn overview_component_navigation_only_opens_advanced_and_never_runs_a_test() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.onboarding = false;
        app.overview = true;
        app.key(key(KeyCode::End));
        assert_eq!(app.overview_selected, overview::COMPONENTS.len() - 1);
        app.key(key(KeyCode::Enter));
        assert!(!app.overview);
        assert!(app.category() == Category::Power);
        assert!(app.power_dialog.is_none());
        assert!(!app.action_pending);
        app.key(key(KeyCode::Esc));
        app.key(key(KeyCode::Esc));
        assert!(app.overview);
        app.key(key(KeyCode::Home));
        app.key(key(KeyCode::Enter));
        assert!(app.category() == Category::Cpu);
        assert!(!app.action_pending);
        assert_eq!(app.next_id, 0);
    }
    #[test]
    fn failed_job_keeps_its_error_report_available() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.complete_job(json!({"state":"failed","result":null,"error":"Fixture tool failed"}));
        assert_eq!(
            app.last_report.as_ref().unwrap()["error"],
            "Fixture tool failed"
        );
        assert!(app.notice.contains("Fixture tool failed"));
    }
    #[test]
    fn background_refresh_does_not_erase_an_action_error() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.receive(ipc::Reply {
            action: "network.scan".into(),
            result: Err("Missing Wi-Fi firmware".into()),
        });
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Ok(model::demo_state()),
        });
        assert_eq!(app.error, "Missing Wi-Fi firmware");
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Err("Controller offline".into()),
        });
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Ok(model::demo_state()),
        });
        assert!(app.error.is_empty());
    }
    #[test]
    fn compact_retry_flag_applies_only_to_the_current_boot() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.state["identity"] = json!({"boot_id":"current"});
        app.state["results"] = json!([{"test_id":"cpu.compute","status":"FAIL","boot_id":"previous","retry_required":true}]);
        assert!(!app.failed_previous(&json!({"test_id":"cpu.compute"})));
        app.state["results"][0]["boot_id"] = json!("current");
        assert!(app.failed_previous(&json!({"test_id":"cpu.compute"})));
        app.state["results"][0]["retry_required"] = json!(false);
        assert!(!app.failed_previous(&json!({"test_id":"cpu.compute"})));
    }
    #[test]
    fn pending_audio_observation_can_be_reopened_from_reports() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.onboarding = false;
        app.category = 7;
        app.selected = 1;
        app.focus = Focus::Actions;
        app.state["identity"] = json!({"boot_id":"current"});
        app.state["results"] = json!([{"run_id":"audio1","test_id":"audio.44100","boot_id":"current","observation_required":true}]);
        app.key(key(KeyCode::Enter));
        assert!(app.report_expanded[6]);
        app.key(key(KeyCode::Down));
        app.key(key(KeyCode::Enter));
        assert!(matches!(
            app.form.as_ref().unwrap().kind,
            FormKind::Observation { .. }
        ));
        app.key(key(KeyCode::Esc));
        app.state["results"][0]["boot_id"] = json!("previous");
        app.key(key(KeyCode::Enter));
        assert!(app.form.is_none());
        assert!(app.report.is_some());
    }
    #[test]
    fn clean_repaint_preserves_a_running_job_and_an_in_progress_form() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        assert!(app.take_redraw());
        assert!(!app.take_redraw());
        app.selected = 2;
        app.key(key(KeyCode::Enter));
        app.form.as_mut().unwrap().fields = vec!["Example Wi-Fi".into(), "fixture-password".into()];
        app.active_job = Some(Job {
            id: json!("running"),
            title: "Fixture job".into(),
            started: Instant::now(),
            data: json!({}),
        });
        assert!(!app.handle_event(Event::Key(KeyEvent::new(
            KeyCode::Char('l'),
            KeyModifiers::CONTROL
        ))));
        assert!(app.take_redraw());
        assert!(!app.take_redraw());
        assert_eq!(app.active_job.as_ref().unwrap().id, "running");
        assert_eq!(app.form.as_ref().unwrap().fields[0], "Example Wi-Fi");
        assert!(!app.handle_event(Event::Resize(120, 33)));
        assert!(app.take_redraw());
        assert_eq!(app.form.as_ref().unwrap().fields[1], "fixture-password");
        assert!(!app.state.to_string().contains("fixture-password"));
    }
    #[test]
    fn loaded_report_history_survives_compact_state_and_updates_duplicate_runs() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        let reports: Vec<_> = (0..45)
            .map(|i| json!({"run_id":format!("run{i}"),"test_id":"cpu.compute","status":"PASS"}))
            .collect();
        app.receive(ipc::Reply {
            action: "results.list".into(),
            result: Ok(json!(reports)),
        });
        let mut state = model::demo_state();
        state["results"] = json!([{"run_id":"run0","test_id":"cpu.compute","status":"FAIL"}]);
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Ok(state),
        });
        assert_eq!(model::array(&app.state, "/results").len(), 45);
        assert_eq!(app.state["results"][0]["status"], "FAIL");
        assert_eq!(
            model::report_actions(&app.state, &[true; model::REPORT_GROUPS.len()]).len(),
            48
        );
        let large: Vec<_> = (0..250)
            .map(|i| json!({"run_id":format!("run{i}")}))
            .collect();
        assert_eq!(
            model::array(&merged_results(&json!(large), &json!([])), "").len(),
            200
        );
    }
    #[test]
    fn report_tree_navigates_visible_children_and_collapses_without_changing_area() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.onboarding = false;
        app.category = 7;
        app.focus = Focus::Actions;
        app.state["results"] = json!([
            {"run_id":"cpu1","test_id":"cpu.compute","status":"PASS"},
            {"run_id":"memory1","test_id":"memory.pattern","status":"PASS"}
        ]);
        assert_eq!(app.actions().len(), 4);
        app.key(key(KeyCode::Down));
        app.key(key(KeyCode::Right));
        assert!(app.report_expanded[0]);
        assert_eq!(app.selected, 1);
        app.key(key(KeyCode::Down));
        assert!(matches!(
            app.actions()[app.selected].kind,
            ActionKind::Report(_)
        ));
        app.key(key(KeyCode::Enter));
        assert_eq!(app.report.as_ref().unwrap()["run_id"], "cpu1");
        app.key(key(KeyCode::Esc));
        app.key(key(KeyCode::Left));
        assert!(!app.report_expanded[0]);
        assert_eq!(app.selected, 1);
        assert_eq!(app.category, 7);
        app.key(key(KeyCode::Down));
        assert!(matches!(
            app.actions()[app.selected].kind,
            ActionKind::ReportGroup(1)
        ));
        app.key(key(KeyCode::Enter));
        assert!(app.report_expanded[1]);
        app.key(key(KeyCode::Enter));
        assert!(!app.report_expanded[1]);
        app.key(key(KeyCode::Tab));
        app.key(key(KeyCode::Left));
        assert_eq!(app.category, 6);
    }
    #[test]
    fn settings_shortcut_keeps_report_tree_and_history_intact() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.state["results"] = json!([{"run_id":"saved","test_id":"cpu.compute"}]);
        app.report_expanded[0] = true;
        app.key(key(KeyCode::F(6)));
        assert!(app.category() == Category::Settings);
        assert!(!app.onboarding);
        assert!(app.focus == Focus::Actions);
        app.key(key(KeyCode::F(4)));
        assert!(app.report_expanded[0]);
        assert_eq!(model::array(&app.state, "/results").len(), 1);
        assert_eq!(app.actions().len(), 4);
    }
    #[test]
    fn reports_up_at_top_returns_to_areas_even_without_saved_reports() {
        for results in [
            json!([]),
            json!([{"run_id":"saved","test_id":"cpu.compute"}]),
        ] {
            let mut app = App::new(PathBuf::from("/unreachable"), true);
            app.onboarding = false;
            app.category = CATEGORIES
                .iter()
                .position(|value| *value == Category::Reports)
                .unwrap();
            app.focus = Focus::Actions;
            app.selected = 0;
            app.state["results"] = results.clone();
            app.report_expanded[0] = true;
            app.last_report = Some(json!({"run_id":"last"}));
            app.active_job = Some(Job {
                id: json!("existing"),
                title: "Existing test".into(),
                started: Instant::now(),
                data: Value::Null,
            });
            app.key(key(KeyCode::Up));
            assert!(app.focus == Focus::Categories);
            assert_eq!(app.selected, 0);
            assert_eq!(app.state["results"], results);
            assert!(app.report_expanded[0]);
            assert_eq!(app.last_report.as_ref().unwrap()["run_id"], "last");
            assert_eq!(app.active_job.as_ref().unwrap().id, json!("existing"));
            assert_eq!(app.next_id, 0);
            app.key(key(KeyCode::Right));
            assert!(app.category() == Category::Settings);
        }
    }
    #[test]
    fn reports_up_moves_children_first_and_other_areas_keep_their_existing_focus() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.onboarding = false;
        app.category = CATEGORIES
            .iter()
            .position(|value| *value == Category::Reports)
            .unwrap();
        app.focus = Focus::Actions;
        app.state["results"] = json!([{"run_id":"saved","test_id":"cpu.compute"}]);
        app.report_expanded[0] = true;
        app.selected = 2;
        assert!(matches!(
            app.actions()[app.selected].kind,
            ActionKind::Report(_)
        ));
        for expected in [1, 0] {
            app.key(key(KeyCode::Up));
            assert_eq!(app.selected, expected);
            assert!(app.focus == Focus::Actions);
        }
        app.key(key(KeyCode::Up));
        assert!(app.focus == Focus::Categories);
        app.key(key(KeyCode::Enter));
        assert!(app.focus == Focus::Actions);
        app.key(key(KeyCode::Esc));
        assert!(app.focus == Focus::Categories);
        app.category = 0;
        app.focus = Focus::Actions;
        app.key(key(KeyCode::Up));
        assert!(app.focus == Focus::Actions);
        assert!(app.category() == Category::System);
    }
    #[test]
    fn up_in_report_details_scrolls_without_returning_to_areas() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.onboarding = false;
        app.category = CATEGORIES
            .iter()
            .position(|value| *value == Category::Reports)
            .unwrap();
        app.focus = Focus::Actions;
        app.selected = 0;
        app.report = Some(json!({"run_id":"saved","commands":[{"output":"detail"}]}));
        app.report_scroll = 2;
        app.key(key(KeyCode::Up));
        assert_eq!(app.report_scroll, 1);
        assert!(app.focus == Focus::Actions);
        app.report_scroll = 0;
        app.key(key(KeyCode::Up));
        assert_eq!(app.report_scroll, 0);
        assert!(app.report.is_some());
        assert!(app.focus == Focus::Actions);
        app.key(key(KeyCode::Esc));
        assert!(app.report.is_none());
        app.key(key(KeyCode::Up));
        assert!(app.focus == Focus::Categories);
    }
    #[test]
    fn private_password_reply_only_lives_in_modal_and_close_clears_it() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.take_redraw();
        app.ssh_dialog = Some(SshDialog::menu());
        app.receive(ipc::Reply {
            action: "ssh.password.enable".into(),
            result: Ok(json!({"password":"private-fixture","expires_in_seconds":600})),
        });
        assert_eq!(
            app.ssh_dialog
                .as_ref()
                .unwrap()
                .secret
                .as_ref()
                .unwrap()
                .expose(),
            "private-fixture"
        );
        assert!(!app.state.to_string().contains("private-fixture"));
        assert!(app.last_report.is_none());
        assert!(app.active_job.is_none());
        app.key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));
        assert!(app.ssh_dialog.is_none());
        assert!(app.take_redraw());
        app.receive(ipc::Reply {
            action: "ssh.password.enable".into(),
            result: Ok(json!({"password":"late-private-fixture","expires_in_seconds":600})),
        });
        assert!(app.ssh_dialog.is_none());
        assert!(app.last_report.is_none());
        assert!(!app.state.to_string().contains("late-private-fixture"));
    }
    #[test]
    fn github_key_import_requires_preview_and_explicit_add_selection() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.ssh_dialog = Some(SshDialog::menu());
        app.receive(ipc::Reply {action:"ssh.github.preview".into(),result:Ok(json!({"preview_id":"preview","username":"meerzulee","fingerprints":["SHA256:test"]}))});
        assert!(app.ssh_dialog.as_ref().unwrap().screen == SshScreen::Preview);
        assert_eq!(app.ssh_dialog.as_ref().unwrap().selected, 0);
        assert!(!app.ssh_pending);
        assert!(app.last_report.is_none());
        app.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert!(app.ssh_dialog.as_ref().unwrap().screen == SshScreen::Menu);
    }
    #[test]
    fn saved_keys_with_unready_ssh_show_partial_success_without_readding() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.ssh_dialog = Some(SshDialog::menu());
        app.receive(ipc::Reply {
            action: "ssh.github.add".into(),
            result: Ok(json!({
            "added":1,"total":2,"persisted":true,"ssh_ready":false,
            "message":"Keys were saved, but SSH is not ready. Inspect the SSH service."})),
        });
        let dialog = app.ssh_dialog.as_ref().unwrap();
        assert_eq!(dialog.info["setup_available"], false);
        assert!(model::at(&dialog.info, "/message").starts_with("Keys were saved"));
        assert!(!app.ssh_pending);
        assert!(app.last_report.is_none());
        app.key(key(KeyCode::Enter));
        assert!(app.ssh_dialog.is_none());
    }
    #[test]
    fn network_onboarding_precedes_the_core_first_dashboard_order() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        assert!(app.onboarding);
        assert!(app.category() == Category::Network);
        app.selected = app
            .actions()
            .iter()
            .position(|item| matches!(item.kind, ActionKind::Continue))
            .unwrap();
        app.key(key(KeyCode::Enter));
        assert!(app.category() == Category::System);
        assert!(app.diagnostic_screen == DiagnosticScreen::Choose);
        assert!(!app.overview);
        app.key(key(KeyCode::Enter));
        assert!(app.overview);
        app.key(key(KeyCode::F(7)));
        assert!(!app.overview);
        for expected in [
            Category::Cpu,
            Category::Graphics,
            Category::Browser,
            Category::Network,
            Category::Audio,
            Category::Storage,
            Category::Reports,
            Category::Settings,
            Category::Power,
        ] {
            app.key(key(KeyCode::Right));
            assert!(app.category() == expected);
        }
        app.key(key(KeyCode::F(2)));
        assert!(app.category() == Category::Network);
        assert!(!app.onboarding);
    }
    #[test]
    fn hardware_drilldown_uses_a_fixed_snapshot_and_keeps_jobs_reports_and_ipc_untouched() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.onboarding = false;
        app.category = 0;
        app.selected = 0;
        app.state["inventory"]["cpu"] =
            json!({"models":["Original CPU"],"listed_processors":8,"online":"0-7"});
        app.active_job = Some(Job {
            id: json!("running"),
            title: "Existing test".into(),
            started: Instant::now(),
            data: Value::Null,
        });
        app.last_report = Some(json!({"run_id":"saved"}));
        app.report_expanded[0] = true;
        app.key(key(KeyCode::Enter));
        assert!(app.hardware.is_some());
        assert!(app.report.is_none());
        app.state["inventory"]["cpu"]["models"][0] = json!("New snapshot");
        app.key(key(KeyCode::Down));
        app.key(key(KeyCode::Enter));
        assert!(app.hardware.as_ref().unwrap().detail);
        assert_eq!(
            app.hardware.as_ref().unwrap().pages[1].summary,
            "Original CPU"
        );
        app.key(key(KeyCode::Char('a')));
        assert!(
            app.hardware
                .as_ref()
                .unwrap()
                .raw
                .as_ref()
                .unwrap()
                .contains("Original CPU")
        );
        app.key(key(KeyCode::Esc));
        assert!(app.hardware.as_ref().unwrap().detail);
        assert!(app.hardware.as_ref().unwrap().raw.is_none());
        app.key(key(KeyCode::Esc));
        assert!(!app.hardware.as_ref().unwrap().detail);
        assert_eq!(app.hardware.as_ref().unwrap().selected, 1);
        app.key(key(KeyCode::Esc));
        assert!(app.hardware.is_none());
        assert!(app.active_job.is_some());
        assert_eq!(app.last_report.as_ref().unwrap()["run_id"], "saved");
        assert!(app.report_expanded[0]);
        assert_eq!(app.next_id, 0);
        assert!(app.ssh_dialog.is_none());
    }
    #[test]
    fn hardware_scroll_end_is_bounded_and_resize_preserves_component_and_back_stack() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        let inventory = json!({"storage":{"devices":(0..30).map(|i|json!({"name":format!("sda{i}"),"model":"Example device","sectors_512b":"33554432"})).collect::<Vec<_>>()}});
        app.hardware = Some(HardwareDialog::new(inventory, hardware::Section::Storage));
        app.key(key(KeyCode::Enter));
        app.key(key(KeyCode::End));
        let previous = app.hardware.as_ref().unwrap().scroll;
        assert!(previous > 0);
        app.key(key(KeyCode::Down));
        assert_eq!(app.hardware.as_ref().unwrap().scroll, previous);
        app.handle_event(Event::Resize(110, 40));
        let limit = ui::hardware_scroll_limit(&app);
        assert!(app.hardware.as_ref().unwrap().scroll <= limit);
        assert_eq!(app.hardware.as_ref().unwrap().selected, 7);
        app.key(key(KeyCode::Home));
        assert_eq!(app.hardware.as_ref().unwrap().scroll, 0);
        app.key(key(KeyCode::PageDown));
        let saved = app.hardware.as_ref().unwrap().scroll;
        app.key(key(KeyCode::Char('a')));
        app.key(key(KeyCode::End));
        app.key(key(KeyCode::Esc));
        assert_eq!(app.hardware.as_ref().unwrap().scroll, saved);
        app.key(key(KeyCode::Esc));
        assert_eq!(app.hardware.as_ref().unwrap().selected, 7);
        assert!(!app.hardware.as_ref().unwrap().detail);
    }
    #[test]
    fn settings_groups_keep_typed_controls_selection_and_reports_across_refresh() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.state["settings"] = json!({"theme":"olive","font_size":16,"available_themes":[{"id":"olive","name":"Olive"},{"id":"nord","name":"Nord"}]});
        app.report_expanded[0] = true;
        app.last_report = Some(json!({"run_id":"saved"}));
        app.key(key(KeyCode::F(6)));
        assert_eq!(app.actions().len(), 4);
        app.key(key(KeyCode::Enter));
        app.key(key(KeyCode::Right));
        app.key(key(KeyCode::Down));
        let selected = app.selected;
        assert_eq!(selected, 2);
        let mut state = app.state.clone();
        state["settings"]["theme"] = json!("nord");
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Ok(state),
        });
        assert_eq!(app.selected, selected);
        assert!(app.settings_expanded[0]);
        assert!(app.actions()[selected].title.contains("selected"));
        app.key(key(KeyCode::Left));
        assert_eq!(app.selected, 0);
        assert!(!app.settings_expanded[0]);
        app.selected = 1;
        app.key(key(KeyCode::Right));
        app.key(key(KeyCode::Right));
        let actions = app.actions();
        let ActionKind::Request("settings.set", params) = &actions[app.selected].kind else {
            panic!("Font child must use existing typed action");
        };
        assert_eq!(params, &json!({"font_size":15}));
        app.key(key(KeyCode::Left));
        assert_eq!(app.selected, 1);
        assert!(!app.settings_expanded[1]);
        app.selected = 2;
        app.key(key(KeyCode::Enter));
        app.key(key(KeyCode::Right));
        app.key(key(KeyCode::Enter));
        assert_eq!(app.report.as_ref().unwrap()["view"], "creator_credits");
        app.key(key(KeyCode::Esc));
        app.key(key(KeyCode::Left));
        assert_eq!(app.selected, 2);
        assert!(!app.settings_expanded[2]);
        assert!(app.report_expanded[0]);
        assert_eq!(app.last_report.as_ref().unwrap()["run_id"], "saved");
        assert_eq!(app.next_id, 0);
        assert!(app.ssh_dialog.is_none());
        app.key(key(KeyCode::Tab));
        app.key(key(KeyCode::Left));
        assert!(app.category() == Category::Reports);
    }
    #[test]
    fn settings_refresh_keeps_font_control_when_theme_children_arrive_or_font_size_changes() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.key(key(KeyCode::F(6)));
        app.settings_expanded = [true, true, false];
        app.state["settings"] = json!({"theme":"olive","font_size":16,"available_themes":[]});
        app.selected = 2;
        let mut state = app.state.clone();
        state["settings"]["available_themes"] =
            json!([{"id":"olive","name":"Olive"},{"id":"nord","name":"Nord"}]);
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Ok(state),
        });
        assert_eq!(app.selected, 4);
        let mut state = app.state.clone();
        state["settings"]["font_size"] = json!(17);
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Ok(state),
        });
        let actions = app.actions();
        let ActionKind::Request("settings.set", params) = &actions[app.selected].kind else {
            panic!("Font control selection was lost");
        };
        assert_eq!(params, &json!({"font_size":16}));
        assert_eq!(app.selected, 4);
    }
    #[test]
    fn embedded_test_plan_is_grouped_local_and_preserves_runtime_reports() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.onboarding = false;
        app.category = 0;
        app.last_report = Some(json!({"run_id":"saved"}));
        app.report_expanded[0] = true;
        app.selected = app
            .actions()
            .iter()
            .position(|item| matches!(item.kind, ActionKind::Qualification))
            .unwrap();
        app.key(key(KeyCode::Enter));
        let dialog = app.hardware.as_ref().unwrap();
        assert_eq!(dialog.title, "Test plan");
        assert_eq!(dialog.pages.len(), 12);
        let count = dialog
            .raw_sources
            .as_ref()
            .unwrap()
            .iter()
            .map(|items| model::array(items, "").len())
            .sum::<usize>();
        assert_eq!(count, 45);
        assert!(dialog.raw_sources.as_ref().unwrap().iter().any(|items| {
            model::array(items, "")
                .iter()
                .any(|item| item["id"] == "uvd.lifecycle" && item["mode"] == "unimplemented")
        }));
        app.key(key(KeyCode::Down));
        app.key(key(KeyCode::Enter));
        app.key(key(KeyCode::Char('a')));
        assert!(
            app.hardware
                .as_ref()
                .unwrap()
                .raw
                .as_ref()
                .unwrap()
                .contains("cpu.bounded")
        );
        app.key(key(KeyCode::Esc));
        app.key(key(KeyCode::Esc));
        app.key(key(KeyCode::Esc));
        assert!(app.hardware.is_none());
        assert_eq!(app.next_id, 0);
        assert!(app.report_expanded[0]);
        assert_eq!(app.last_report.as_ref().unwrap()["run_id"], "saved");
    }
    #[test]
    fn power_guidance_is_local_in_every_profile_and_preserves_jobs_and_reports() {
        for runtime in [Value::Null, json!(false), json!(true)] {
            let mut app = App::new(PathBuf::from("/unreachable"), true);
            app.onboarding = false;
            app.category = 0;
            app.focus = Focus::Categories;
            app.state["identity"] = json!({"declared_artifacts":{"uvd_runtime":runtime}});
            app.last_report = Some(json!({"run_id":"saved"}));
            app.active_job = Some(Job {
                id: json!("existing"),
                title: "Existing test".into(),
                started: Instant::now(),
                data: Value::Null,
            });
            app.key(key(KeyCode::Left));
            assert!(app.category() == Category::Power);
            app.key(key(KeyCode::Enter));
            for (index, operation) in ["Shutdown", "Reboot to Orbis"].into_iter().enumerate() {
                assert_eq!(app.selected, index);
                assert!(app.actions()[index].title.contains("unavailable"));
                assert!(!app.key(key(KeyCode::Enter)));
                assert_eq!(app.report.as_ref().unwrap()["view"], "power_unavailable");
                assert_eq!(app.report.as_ref().unwrap()["operation"], operation);
                assert_eq!(app.next_id, 0);
                assert!(!app.action_pending);
                assert_eq!(app.active_job.as_ref().unwrap().id, json!("existing"));
                assert_eq!(app.last_report.as_ref().unwrap()["run_id"], "saved");
                app.key(key(if index == 0 {
                    KeyCode::Enter
                } else {
                    KeyCode::Esc
                }));
                assert!(app.report.is_none());
                assert_eq!(app.selected, index);
                if index == 0 {
                    app.key(key(KeyCode::Down));
                }
            }
            app.key(key(KeyCode::Esc));
            app.key(key(KeyCode::Right));
            assert!(app.category() == Category::System);
        }
    }

    #[test]
    fn power_confirmation_defaults_to_cancel_and_submits_once_only_after_explicit_choice() {
        for (index, operation) in ["shutdown", "reboot-orbis"].into_iter().enumerate() {
            let mut app = App::new(PathBuf::from("/unreachable"), true);
            let (sender, receiver) = std::sync::mpsc::channel();
            app.demo = false;
            app.sender = Some(sender);
            app.onboarding = false;
            app.category = CATEGORIES
                .iter()
                .position(|item| *item == Category::Power)
                .unwrap();
            app.focus = Focus::Actions;
            app.selected = index;
            app.state["power"] =
                json!({"shutdown":{"available":true},"reboot-orbis":{"available":true}});
            app.key(key(KeyCode::Enter));
            assert!(!app.power_dialog.as_ref().unwrap().confirm_selected);
            assert!(receiver.try_recv().is_err());
            app.key(key(KeyCode::Enter));
            assert!(app.power_dialog.is_none());
            assert!(receiver.try_recv().is_err());
            app.key(key(KeyCode::Enter));
            app.key(key(KeyCode::Right));
            app.key(key(KeyCode::Esc));
            assert!(receiver.try_recv().is_err());
            app.key(key(KeyCode::Enter));
            app.key(key(KeyCode::Right));
            app.key(key(KeyCode::Enter));
            let request = receiver.try_recv().unwrap();
            assert_eq!(request.action, "power.execute");
            assert_eq!(
                request.params,
                json!({"operation":operation,"confirm":true})
            );
            assert!(app.action_pending);
            app.key(key(KeyCode::Enter));
            assert!(receiver.try_recv().is_err());
        }
    }
    #[test]
    fn confirmed_power_preempts_active_work_and_ignores_late_test_reply() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        let (sender, receiver) = std::sync::mpsc::channel();
        app.demo = false;
        app.sender = Some(sender);
        app.connected = true;
        app.onboarding = false;
        app.category = CATEGORIES
            .iter()
            .position(|item| *item == Category::Power)
            .unwrap();
        app.focus = Focus::Actions;
        app.state["power"] = json!({"shutdown":{"available":true}});
        app.action_pending = true;
        app.active_job = Some(Job {
            id: json!("old-test"),
            title: "Test".into(),
            started: Instant::now(),
            data: Value::Null,
        });
        app.key(key(KeyCode::Enter));
        assert!(app.power_dialog.is_some());
        assert!(!app.power_dialog.as_ref().unwrap().confirm_selected);
        app.key(key(KeyCode::Right));
        app.key(key(KeyCode::Enter));
        assert_eq!(receiver.try_recv().unwrap().action, "power.execute");
        assert!(app.power_pending);
        assert!(app.active_job.is_none());
        app.receive(ipc::Reply {
            action: "test.run".into(),
            result: Ok(json!({"id":"old-test"})),
        });
        assert!(app.active_job.is_none());
        app.key(key(KeyCode::Enter));
        assert!(receiver.try_recv().is_err());
    }

    #[test]
    fn completed_power_snapshot_refreshes_from_current_dispatch_without_polling_or_resubmitting() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        let pending = json!({"run_id":"power-run","test_id":"power.reboot-orbis.request",
            "identity":{"boot_id":"current-boot"},"status":"INCONCLUSIVE","phase":"completed",
            "message":"Reports saved; confirmed power request is pending."});
        app.complete_job(json!({"state":"completed","result":pending}));
        app.report = app.last_report.clone();
        assert!(app.notice.contains("pending"));
        let (sender, requests) = std::sync::mpsc::channel();
        app.demo = false;
        app.sender = Some(sender);
        let original_id = app.next_id;
        for (stage, message) in [
            (
                "preparing",
                "Preparing the confirmed power request. Reports are saved.",
            ),
            (
                "request_returned_physical_outcome_unobserved",
                "Power request sent; physical outcome is unobserved.",
            ),
            (
                "failed_no_retry",
                "Power request failed: storage could not become read-only. Do not retry.",
            ),
        ] {
            app.receive(ipc::Reply {action:"state".into(), result:Ok(json!({
                "identity":{"boot_id":"current-boot"},
                "jobs":[{"id":"completed-power-job","state":"completed","result":pending}],
                "power":{"current_request":{"run_id":"power-run","test_id":"power.reboot-orbis.request",
                    "boot_id":"current-boot","status":"INCONCLUSIVE","dispatch_state":stage,"message":message}}
            }))});
            assert!(app.notice.contains(message));
            assert_eq!(app.last_report.as_ref().unwrap()["dispatch_state"], stage);
            assert_eq!(app.report.as_ref().unwrap()["message"], message);
            assert_eq!(app.last_report.as_ref().unwrap()["status"], "INCONCLUSIVE");
            assert!(app.active_job.is_none());
            assert!(!app.action_pending);
            assert!(!app.job_pending);
            assert_eq!(app.next_id, original_id);
            assert!(requests.try_recv().is_err());
        }
        app.notice = "Reports navigation hint".into();
        app.receive(ipc::Reply {
            action: "state".into(),
            result: Ok(app.state.clone()),
        });
        assert_eq!(app.notice, "Reports navigation hint");
        assert_eq!(
            app.last_report.as_ref().unwrap()["dispatch_state"],
            "failed_no_retry"
        );
        assert!(requests.try_recv().is_err());
    }
    #[test]
    fn power_refresh_ignores_foreign_requests_and_does_not_reopen_completed_ordinary_jobs() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        let pending = json!({"run_id":"power-run","test_id":"power.shutdown.request",
            "identity":{"boot_id":"current-boot"},"status":"INCONCLUSIVE","message":"Pending"});
        app.complete_job(json!({"state":"completed","result":pending}));
        let before = app.notice.clone();
        for (run, boot, status) in [
            ("foreign-run", "current-boot", "INCONCLUSIVE"),
            ("power-run", "old-boot", "INCONCLUSIVE"),
            ("power-run", "current-boot", "PASS"),
        ] {
            app.receive(ipc::Reply {action:"state".into(),result:Ok(json!({
                "identity":{"boot_id":"current-boot"},"power":{"current_request":{
                    "run_id":run,"test_id":"power.shutdown.request","boot_id":boot,
                    "status":status,"dispatch_state":"failed_no_retry","message":"Wrong outcome"}}
            }))});
            assert_eq!(app.notice, before);
            assert_eq!(app.last_report.as_ref().unwrap()["message"], "Pending");
            assert!(app.active_job.is_none());
        }
        app.complete_job(
            json!({"state":"completed","result":{"run_id":"cpu-run","test_id":"cpu.compute",
            "identity":{"boot_id":"current-boot"},"status":"PASS","message":"CPU passed"}}),
        );
        let ordinary_notice = app.notice.clone();
        app.receive(ipc::Reply {action:"state".into(),result:Ok(json!({
            "identity":{"boot_id":"current-boot"},"jobs":[{"id":"old-cpu","state":"completed"}],
            "power":{"current_request":{"run_id":"power-run","test_id":"power.shutdown.request",
                "boot_id":"current-boot","status":"INCONCLUSIVE","dispatch_state":"failed_no_retry",
                "message":"Power failed"}}
        }))});
        assert_eq!(app.notice, ordinary_notice);
        assert_eq!(app.last_report.as_ref().unwrap()["test_id"], "cpu.compute");
        assert!(app.active_job.is_none());
        assert!(!app.job_pending);
    }
    #[test]
    fn uvd_scenarios_are_readable_plan_rows_and_never_start_a_workload() {
        let dialog = HardwareDialog::qualification().unwrap();
        let index = dialog
            .pages
            .iter()
            .position(|page| page.title == "UVD")
            .unwrap();
        let rows = &dialog.pages[index].rows;
        let headings = rows
            .iter()
            .filter_map(|row| match row {
                hardware::Row::Heading(value) if value.starts_with("Scenario · ") => {
                    Some(value.as_str())
                }
                _ => None,
            })
            .collect::<Vec<_>>();
        assert_eq!(headings.len(), 6);
        assert!(
            headings
                .iter()
                .any(|value| value.contains("128x96 exact 120-frame"))
        );
        assert!(headings.iter().any(|value| value.contains("720p30")));
        assert!(headings.iter().any(|value| value.contains("1080p30")));
        let availability = rows
            .iter()
            .filter_map(|row| match row {
                hardware::Row::Field(label, value) if label == "Availability" => {
                    Some(value.as_str())
                }
                _ => None,
            })
            .collect::<Vec<_>>();
        assert_eq!(
            availability
                .iter()
                .filter(|value| value.starts_with("Available alpha"))
                .count(),
            1
        );
        assert_eq!(
            availability
                .iter()
                .filter(|value| value.starts_with("Unimplemented"))
                .count(),
            5
        );
        let actions = rows
            .iter()
            .filter_map(|row| match row {
                hardware::Row::Field(label, value) if label == "Existing action" => {
                    Some(value.as_str())
                }
                _ => None,
            })
            .collect::<Vec<_>>();
        assert_eq!(actions, ["uvd.avc · not started here"]);
        assert!(rows.iter().any(|row| matches!(row,hardware::Row::Field(_,value) if value.contains("2.8 GB") && value.contains("streaming"))));
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.hardware = Some(dialog);
        app.hardware.as_mut().unwrap().selected = index;
        app.key(key(KeyCode::Enter));
        assert!(app.hardware.as_ref().unwrap().detail);
        app.key(key(KeyCode::End));
        let scroll = app.hardware.as_ref().unwrap().scroll;
        assert!(scroll > 0);
        app.key(key(KeyCode::Char('a')));
        assert!(
            app.hardware
                .as_ref()
                .unwrap()
                .raw
                .as_ref()
                .unwrap()
                .contains("uvd.scenario.720p30")
        );
        app.key(key(KeyCode::Esc));
        assert_eq!(app.hardware.as_ref().unwrap().scroll, scroll);
        app.key(key(KeyCode::Esc));
        app.key(key(KeyCode::Esc));
        assert!(app.hardware.is_none());
        assert_eq!(app.next_id, 0);
        assert!(app.active_job.is_none());
        assert!(app.last_report.is_none());
    }

    #[test]
    fn cancel_full_stops_the_queue_and_opens_the_summary() {
        let mut app = App::new(PathBuf::from("/tmp/diagnostix-test.sock"), true);
        app.full_active = true;
        app.full_in_flight = true;
        app.full_total = 5;
        app.full_done = 2;
        app.full_passed = 1;
        app.full_failed = 1;
        app.full_current = "Bounded CPU computation".into();
        app.full_queue = vec![("Next".into(), "test.run".into(), json!({}))];
        app.cancel_full();
        assert!(!app.full_active);
        assert!(app.full_queue.is_empty());
        let report = app.report.as_ref().unwrap();
        assert_eq!(report["view"], "full_test_summary");
        assert_eq!(report["cancelled"], true);
        assert_eq!(report["passed"], 1);
        assert_eq!(report["failed"], 1);
        assert!(app.full_report_text().contains("Passed: 1"));
    }

    #[test]
    fn full_test_records_failure_and_finishes_the_queue_instead_of_stopping() {
        let mut app = App::new(PathBuf::from("/tmp/diagnostix-test.sock"), true);
        app.full_active = true;
        app.full_in_flight = true;
        app.full_total = 1;
        app.full_done = 1;
        app.full_current = "Bounded CPU computation".into();
        app.advance_full(&json!({"status":"FAIL","test_id":"cpu.compute","message":"bad"}));
        assert_eq!(app.full_failed, 1);
        assert_eq!(app.full_passed, 0);
        assert!(!app.full_active);
        assert!(app.notice.contains("Full test complete: 0 passed, 1 failed/skipped"));
    }

    #[test]
    fn q_opens_power_and_full_test_queue_is_bounded_to_tests() {
        let mut app = App::new(PathBuf::from("/unreachable"), true);
        app.key(key(KeyCode::Char('q')));
        assert!(app.category() == Category::Power);
        assert!(!app.exit_dialog);

        let app = App::new(PathBuf::from("/unreachable"), true);
        let queue = app.full_test_queue();
        assert!(queue.iter().any(|(_, action, params)| {
            action == "test.run" && model::at(params, "/test_id") == "inventory.readonly"
        }));
        let core: Vec<_> = queue
            .iter()
            .filter(|(_, action, _)| action == "test.run")
            .map(|(_, _, params)| model::at(params, "/test_id"))
            .take(3)
            .collect();
        assert_eq!(
            core,
            ["inventory.readonly", "cpu.compute", "memory.pattern"]
        );
        let export = queue
            .iter()
            .position(|(_, action, _)| action == "support.export")
            .unwrap();
        let browser = queue
            .iter()
            .position(|(_, action, params)| {
                action == "graphics.launch" && model::at(params, "/app") == "browser"
            })
            .unwrap();
        assert!(export < browser);
        assert!(queue.iter().any(|(_, action, params)| {
            action == "graphics.launch" && model::at(params, "/app") == "browser"
        }));
        assert!(!queue.iter().any(|(_, action, _)| action.starts_with("power.")
            || action.starts_with("network.")
            || action.starts_with("ssh.")));
    }
}
