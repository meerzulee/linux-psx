// SPDX-License-Identifier: MIT
mod hardware;
mod ipc;
mod model;
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

pub enum FormKind {
    Wifi,
    Https,
    SshGithub,
    Retry { action: String, params: Value },
    Observation { run_id: String },
}

pub struct Form {
    pub kind: FormKind,
    pub fields: Vec<String>,
    pub selected: usize,
    pub error: String,
}

pub struct Job {
    pub id: Value,
    pub title: String,
    pub started: Instant,
    pub data: Value,
}

pub struct HardwareDialog {
    pub inventory: Value,
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
    pub demo: bool,
    pub ticks: u64,
    sender: Option<Sender<ipc::Request>>,
    receiver: Option<Receiver<ipc::Reply>>,
    next_id: u64,
    state_pending: bool,
    job_pending: bool,
    action_pending: bool,
    action_title: String,
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
            category: 4,
            selected: 0,
            focus: Focus::Actions,
            connected: demo,
            error: String::new(),
            notice: if demo {
                "Demo preview only. No hardware actions execute.".into()
            } else {
                "Choose a connection, then continue to diagnostics.".into()
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
            demo,
            ticks: 0,
            sender,
            receiver,
            next_id: 0,
            state_pending: false,
            job_pending: false,
            action_pending: false,
            action_title: String::new(),
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
            model::report_actions(&self.state, &self.report_expanded)
        } else if self.category() == Category::Settings {
            model::settings_actions(&self.state, self.settings_expanded)
        } else {
            let mut actions = model::actions(self.category(), &self.state, self.onboarding);
            if self.category() == Category::System
                && std::env::var("DISPLAY").as_deref() == Ok(":0")
            {
                actions.retain(|item| !matches!(&item.kind, ActionKind::Request("graphics.launch", params) if params["app"]=="diagnostics"));
            }
            actions
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
        if self.busy() {
            self.notice =
                "A test is already running. Review its result before starting another.".into();
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
        self.notice = format!("Starting {title}…");
        self.action_pending = true;
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

    fn activate(&mut self) {
        let actions = self.actions();
        let Some(action) = actions.into_iter().nth(self.selected) else {
            return;
        };
        match action.kind {
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
            ActionKind::ReportGroup(group) => {
                self.report_expanded[group] = !self.report_expanded[group];
            }
            ActionKind::Continue => {
                self.onboarding = false;
                self.category = 0;
                self.selected = 0;
                self.focus = Focus::Categories;
                self.notice = "Choose an area with Left / Right, then press Enter.".into();
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
        if key.kind == KeyEventKind::Release {
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
        if self.report.is_some() {
            match key.code {
                KeyCode::Esc | KeyCode::Enter | KeyCode::Char('v') => self.report = None,
                KeyCode::Down => self.report_scroll = self.report_scroll.saturating_add(1),
                KeyCode::Up => self.report_scroll = self.report_scroll.saturating_sub(1),
                KeyCode::PageDown => self.report_scroll = self.report_scroll.saturating_add(10),
                KeyCode::PageUp => self.report_scroll = self.report_scroll.saturating_sub(10),
                _ => {}
            }
            return false;
        }
        match key.code {
            KeyCode::Char('q') => self.exit_dialog = true,
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
                    self.focus = Focus::Categories;
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
                    dialog.raw = Some(
                        serde_json::to_string_pretty(&hardware::raw(
                            hardware::SECTIONS[dialog.selected],
                            &dialog.inventory,
                        ))
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
        self.last_report = Some(result);
        self.active_job = None;
        self.action_pending = false;
        self.refresh();
    }

    fn receive(&mut self, reply: ipc::Reply) {
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
                value["results"] = merged_results(&value["results"], &self.state["results"]);
                let continue_first = |state: &Value| {
                    model::actions(Category::Network, state, true)
                        .first()
                        .is_some_and(|action| matches!(action.kind, ActionKind::Continue))
                };
                if self.onboarding && !continue_first(&self.state) && continue_first(&value) {
                    self.selected = 0;
                }
                self.state = value;
                if let Some((key, group)) = settings_selection {
                    let actions = self.actions();
                    if let Some(index)=actions.iter().position(|item|model::settings_key(item).as_deref()==Some(key.as_str()))
                        .or_else(||group.and_then(|group|actions.iter().position(|item|matches!(item.kind,ActionKind::SettingsGroup(value) if value==group)))){
                        self.selected=index;
                    }
                }
                self.selected = self.selected.min(self.actions().len().saturating_sub(1));
                if self.active_job.is_none() {
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
                self.complete_job(json!({"status":"INCONCLUSIVE","message":"Demo preview only. No hardware action executed.","hardware_acceptance":false}));
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
                println!("psx-diagnostics-tui {}", env!("CARGO_PKG_VERSION"));
                return Ok(());
            }
            "--help" | "-h" => {
                println!(
                    "psx-diagnostics-tui [--socket PATH] [--demo] [--render-demo WIDTHxHEIGHT]\nNative local-console / SSH diagnostic client. Arrows, Enter, Esc; F1 help.\nDemo mode uses sample data and never contacts a console."
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
            47
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
        assert_eq!(app.actions().len(), 3);
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
        assert_eq!(app.actions().len(), 3);
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
        for expected in [
            Category::Cpu,
            Category::Graphics,
            Category::Browser,
            Category::Network,
            Category::Audio,
            Category::Storage,
            Category::Reports,
            Category::Settings,
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
}
