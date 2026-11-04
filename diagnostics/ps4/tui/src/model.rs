// SPDX-License-Identifier: MIT
use serde_json::{Value, json};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Category {
    System,
    Network,
    Cpu,
    Graphics,
    Browser,
    Audio,
    Storage,
    Reports,
    Settings,
}

pub const CATEGORIES: [Category; 9] = [
    Category::System,
    Category::Cpu,
    Category::Graphics,
    Category::Browser,
    Category::Network,
    Category::Audio,
    Category::Storage,
    Category::Reports,
    Category::Settings,
];

impl Category {
    pub fn title(self) -> &'static str {
        match self {
            Self::System => "System",
            Self::Network => "Network",
            Self::Cpu => "CPU & RAM",
            Self::Graphics => "Graphics",
            Self::Browser => "Browser",
            Self::Audio => "Audio",
            Self::Storage => "Storage",
            Self::Reports => "Reports",
            Self::Settings => "Settings",
        }
    }
    pub fn description(self) -> &'static str {
        match self {
            Self::System => "See what this console has detected.",
            Self::Network => "Connect Ethernet or Wi-Fi. SSH uses your configured key.",
            Self::Cpu => "Short, finite computation and memory checks.",
            Self::Graphics => "Inspect APIs or open a selected graphics test.",
            Self::Browser => "Open the browser workspace on the graphical screen.",
            Self::Audio => "Inspect devices and check the 44.1 kHz output.",
            Self::Storage => "Identify storage without opening or changing its contents.",
            Self::Reports => "Review this console's saved test results.",
            Self::Settings => "Choose a palette and graphical terminal font size.",
        }
    }
    pub fn compact_title(self) -> &'static str {
        match self {
            Self::Network => "Net",
            Self::Cpu => "CPU/RAM",
            Self::Graphics => "GPU",
            Self::Browser => "Web",
            Self::Storage => "Disk",
            _ => self.title(),
        }
    }
}

pub struct Action {
    pub title: String,
    pub detail: String,
    pub kind: ActionKind,
}

pub enum ActionKind {
    Request(&'static str, Value),
    Wifi(String),
    Https,
    Continue,
    Information(Value),
    Hardware(crate::hardware::Section),
    Credits,
    SettingsGroup(SettingsGroup),
    SshSetup,
    ReportGroup(usize),
    Report(Value),
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum SettingsGroup {
    Theme,
    Font,
    About,
}
impl SettingsGroup {
    pub fn index(self) -> usize {
        match self {
            Self::Theme => 0,
            Self::Font => 1,
            Self::About => 2,
        }
    }
}
pub fn settings_group(action: &Action) -> Option<SettingsGroup> {
    match &action.kind {
        ActionKind::SettingsGroup(group) => Some(*group),
        ActionKind::Credits => Some(SettingsGroup::About),
        ActionKind::Request("settings.set", params) if params.get("theme").is_some() => {
            Some(SettingsGroup::Theme)
        }
        ActionKind::Request("settings.set", params) if params.get("font_size").is_some() => {
            Some(SettingsGroup::Font)
        }
        _ => None,
    }
}
pub fn settings_key(action: &Action) -> Option<String> {
    match &action.kind {
        ActionKind::SettingsGroup(group) => Some(format!("group:{}", group.index())),
        ActionKind::Request("settings.set", params) if params.get("theme").is_some() => {
            Some(format!("theme:{}", at(params, "/theme")))
        }
        ActionKind::Request("settings.set", params) if params.get("font_size").is_some() => {
            Some(format!("font:{}", action.title))
        }
        ActionKind::Credits => Some("about:credits".into()),
        ActionKind::SshSetup => Some("ssh:setup".into()),
        _ => None,
    }
}

fn request(title: &str, detail: &str, action: &'static str, params: Value) -> Action {
    Action {
        title: title.into(),
        detail: detail.into(),
        kind: ActionKind::Request(action, params),
    }
}

fn test(title: &str, detail: &str, id: &str, parameters: Value) -> Action {
    request(
        title,
        detail,
        "test.run",
        json!({"test_id":id,"parameters":parameters,"retry_reason":""}),
    )
}

pub fn text(value: &Value) -> String {
    match value {
        Value::String(s) => s.trim().chars().filter(|c| !c.is_control()).collect(),
        Value::Number(n) => n.to_string(),
        Value::Bool(v) => v.to_string(),
        _ => String::new(),
    }
}

pub fn at(value: &Value, path: &str) -> String {
    text(&value.pointer(path).cloned().unwrap_or(Value::Null))
}

pub fn array<'a>(value: &'a Value, path: &str) -> &'a [Value] {
    value
        .pointer(path)
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[])
}

pub fn ip(state: &Value) -> String {
    for interface in array(state, "/network/interfaces") {
        let v = &interface["ipv4"];
        let address = if let Some(items) = v.as_array() {
            items
                .iter()
                .find_map(|item| {
                    let s = if item.is_object() {
                        at(item, "/address")
                    } else {
                        text(item)
                    };
                    (!s.is_empty()).then_some(s)
                })
                .unwrap_or_default()
        } else {
            text(v)
        };
        if !address.is_empty() && !address.starts_with("127.") {
            return address;
        }
    }
    String::new()
}

pub fn connected(state: &Value) -> bool {
    !ip(state).is_empty()
}

pub fn status_label(status: &str) -> &'static str {
    match status {
        "PASS" => "Passed",
        "FAIL" => "Failed",
        "DEGRADED" => "Limited",
        "UNSUPPORTED" => "Unavailable",
        "DEFERRED" => "Deferred",
        "BLOCKED" => "Needs setup",
        "INCONCLUSIVE" => "Observed",
        "running" | "queued" => "Running",
        _ => "Not run",
    }
}

pub const REPORT_GROUPS: [&str; 10] = [
    "CPU",
    "Memory",
    "GPU",
    "Browser",
    "Video / UVD",
    "Network",
    "Audio",
    "Storage",
    "System",
    "Other",
];

pub fn report_group(report: &Value) -> usize {
    let id = at(report, "/test_id");
    match id.split('.').next().unwrap_or("") {
        "cpu" => 0,
        "memory" | "ram" => 1,
        "gpu" | "graphics" => 2,
        "browser" => 3,
        "uvd" => 4,
        "network" | "ethernet" | "wireless" | "wifi" | "bluetooth" => 5,
        "audio" => 6,
        "storage" | "sata" => 7,
        "system" | "inventory" | "power" => 8,
        _ => 9,
    }
}

pub fn report_boot_label(state: &Value, report: &Value) -> &'static str {
    let current = at(state, "/identity/boot_id");
    let mut recorded = at(report, "/boot_id");
    if recorded.is_empty() {
        recorded = at(report, "/identity/boot_id");
    }
    if current.is_empty() || recorded.is_empty() {
        "Boot unknown"
    } else if recorded == current {
        "This boot"
    } else {
        "Earlier boot"
    }
}

pub fn report_time(report: &Value) -> &str {
    report["started_at"]
        .as_str()
        .filter(|s| !s.is_empty())
        .or_else(|| report["completed_at"].as_str())
        .unwrap_or("")
}

fn report_name(report: &Value, state: &Value) -> String {
    let id = at(report, "/test_id");
    let name = match id.as_str() {
        "cpu.compute" => "Computation",
        "memory.pattern" => "Pattern check",
        "gpu.opengl.info" => "OpenGL / EGL information",
        "gpu.vulkan.info" => "Vulkan information",
        "gpu.opengl.render" => "OpenGL reference pixels",
        "gpu.vulkan.render" => "Vulkan reference pixels",
        "uvd.avc" => "AVC decoding",
        "ethernet.status" => "Ethernet status",
        "ethernet.connect" => "Ethernet connection",
        "ethernet.https" => "HTTPS connection",
        "audio.info" => "Audio devices",
        "audio.44100" => "44.1 kHz test tone",
        "audio.native48" => "Native 48 kHz audio",
        "storage.inventory" => "Storage inventory",
        "inventory.readonly" => "Hardware inventory",
        _ => "",
    };
    if !name.is_empty() {
        return name.into();
    }
    let title = at(report, "/title");
    if !title.is_empty() {
        return title;
    }
    if let Some(test) = array(state, "/tests")
        .iter()
        .find(|test| at(test, "/id") == id)
    {
        let title = at(test, "/title");
        if !title.is_empty() {
            return title;
        }
    }
    if id.is_empty() {
        "Saved result".into()
    } else {
        id.replace(['.', '_'], " ")
    }
}

pub fn actions(category: Category, state: &Value, onboarding: bool) -> Vec<Action> {
    match category {
        Category::System => vec![
            Action {
                title: "Hardware overview".into(),
                detail: "Browse the cached inventory. Enter opens readable component details."
                    .into(),
                kind: ActionKind::Hardware(crate::hardware::Section::System),
            },
            request(
                "Graphical menu",
                "Open the high-resolution diagnostics menu.",
                "graphics.launch",
                json!({"app":"diagnostics"}),
            ),
            test(
                "Refresh hardware inventory",
                "Read CPU, RAM, PCI, USB and thermal metadata.",
                "inventory.readonly",
                json!({}),
            ),
            request(
                "Core checks",
                "Check CPU first, then verify 32 MiB of RAM. Advanced tests require both to pass.",
                "quick",
                json!({"retry_reason":""}),
            ),
        ],
        Category::Network => {
            let interfaces = array(state, "/network/interfaces");
            let active = |interface: &&Value| at(interface, "/state").starts_with("connected");
            let ethernet = interfaces.iter().find(|i| at(i, "/name") == "psx0");
            let wifi = interfaces
                .iter()
                .filter(|i| {
                    let name = at(i, "/name");
                    at(i, "/type") == "wifi"
                        && !name.starts_with("p2p")
                        && !name.strip_prefix("ap").is_some_and(|suffix| {
                            !suffix.is_empty() && suffix.chars().all(|c| c.is_ascii_digit())
                        })
                })
                .min_by_key(|i| (at(i, "/name") != "wlan0", at(i, "/name")));
            let ethernet_connected = ethernet.as_ref().is_some_and(active);
            let wifi_connected = wifi.as_ref().is_some_and(active);
            let linked = ethernet_connected || wifi_connected;
            let mut list = vec![request(
                if ethernet_connected {
                    "Disconnect Ethernet"
                } else {
                    "Connect Ethernet"
                },
                if ethernet_connected {
                    "Disconnect the wired link. SSH sessions using Ethernet will close."
                } else {
                    "Use the wired connection and wait for an address."
                },
                if ethernet_connected {
                    "network.ethernet.disconnect"
                } else {
                    "network.ethernet.connect"
                },
                json!({}),
            )];
            if wifi_connected {
                list.push(request(
                    "Disconnect Wi-Fi",
                    "Disconnect the wireless link. SSH sessions using Wi-Fi will close.",
                    "network.wifi.disconnect",
                    json!({}),
                ));
            } else {
                list.extend([
                    request(
                        "Connect Wi-Fi",
                        "Scan using the available wireless adapter.",
                        "network.scan",
                        json!({}),
                    ),
                    Action {
                        title: "Join Wi-Fi manually".into(),
                        detail: "Enter the network name and password.".into(),
                        kind: ActionKind::Wifi(String::new()),
                    },
                ]);
                let mut seen = std::collections::HashSet::new();
                for ap in array(state, "/network/wifi/access_points").iter().take(12) {
                    let ssid = at(ap, "/ssid");
                    if !ssid.is_empty() && seen.insert(ssid.clone()) {
                        list.push(Action {
                            title: format!("Wi-Fi: {}", ssid),
                            detail: format!(
                                "Signal {}%  {}",
                                at(ap, "/signal"),
                                at(ap, "/security")
                            ),
                            kind: ActionKind::Wifi(ssid),
                        });
                    }
                }
            }
            if onboarding {
                let continue_action = Action {
                    title: if linked {
                        "Continue to diagnostics"
                    } else {
                        "Continue offline"
                    }
                    .into(),
                    detail: "You can return to Network at any time with F2.".into(),
                    kind: ActionKind::Continue,
                };
                if linked {
                    list.insert(0, continue_action);
                } else {
                    list.push(continue_action);
                }
            } else {
                list.push(test(
                    "Read Ethernet status",
                    "Observe carrier, addressing and driver status.",
                    "ethernet.status",
                    json!({"interface":"psx0"}),
                ));
                list.push(Action {
                    title: "Check an HTTPS connection".into(),
                    detail: "Fetch an explicit HTTPS URL through the selected interface.".into(),
                    kind: ActionKind::Https,
                });
                list.push(Action {
                    title: "View SSH connection details".into(),
                    detail: "See the login and host-key fingerprint.".into(),
                    kind: ActionKind::Information(state["ssh"].clone()),
                });
            }
            list.push(Action {
                title: "SSH setup".into(),
                detail: "Connection details, GitHub public keys, or a temporary SSH password."
                    .into(),
                kind: ActionKind::SshSetup,
            });
            list
        }
        Category::Cpu => vec![
            request(
                "Core checks",
                "Check CPU first, then verify 32 MiB of RAM. Advanced tests require both to pass.",
                "quick",
                json!({"retry_reason":""}),
            ),
            test(
                "CPU computation · 2 seconds",
                "One worker verifies a known SHA-256 result.",
                "cpu.compute",
                json!({"seconds":2}),
            ),
            test(
                "Memory patterns · 32 MiB",
                "Check four patterns in a small owned allocation.",
                "memory.pattern",
                json!({"memory_mib":32}),
            ),
            test(
                "Memory patterns · 128 MiB",
                "The largest bounded memory test in this image.",
                "memory.pattern",
                json!({"memory_mib":128}),
            ),
        ],
        Category::Graphics => vec![
            test(
                "Verify OpenGL reference pixels",
                "Render a known small image and check the downloaded pixels.",
                "gpu.opengl.render",
                json!({}),
            ),
            test(
                "Verify Vulkan reference pixels",
                "Run the finite Vulkan draw oracle and check its pixels.",
                "gpu.vulkan.render",
                json!({}),
            ),
            test(
                "Read OpenGL / EGL information",
                "Query the offscreen EGL driver; this does not render a reference frame.",
                "gpu.opengl.info",
                json!({}),
            ),
            test(
                "Read Vulkan information",
                "Query available Vulkan devices and drivers.",
                "gpu.vulkan.info",
                json!({}),
            ),
            request(
                "Open OpenGL test",
                "Switch to the graphical test screen. Exit the app to return here.",
                "graphics.launch",
                json!({"app":"opengl"}),
            ),
            request(
                "Open Vulkan test",
                "Switch to the graphical test screen. Exit the app to return here.",
                "graphics.launch",
                json!({"app":"vulkan"}),
            ),
        ],
        Category::Browser => vec![
            request(
                "Firefox · Run all",
                "Run the baseline once in a fresh Firefox profile, save its summary, then close automatically.",
                "graphics.launch",
                json!({"app":"browser"}),
            ),
            request(
                "Chromium · Run all",
                "Requires the verified optional 384 MiB USB provider. Run the baseline, save its summary, then close automatically.",
                "graphics.launch",
                json!({"app":"chromium"}),
            ),
            request(
                "Firefox workspace",
                "Open a fresh Firefox laboratory without starting tests.",
                "graphics.launch",
                json!({"app":"browser-manual"}),
            ),
            request(
                "Chromium workspace",
                "Requires the optional 384 MiB provider. Open the laboratory without starting tests.",
                "graphics.launch",
                json!({"app":"chromium-manual"}),
            ),
            request(
                "ProtonDB compatibility",
                "Community Linux game guidance; PS4 compatibility unverified. Opens Firefox and stays open until you exit.",
                "graphics.launch",
                json!({"app":"protondb"}),
            ),
        ],
        Category::Audio => vec![
            test(
                "Read audio devices",
                "List ALSA devices without playing sound.",
                "audio.info",
                json!({}),
            ),
            test(
                "Play 44.1 kHz test tone",
                "Play the short fixture, then confirm what you heard.",
                "audio.44100",
                json!({}),
            ),
        ],
        Category::Storage => vec![
            test(
                "Read storage inventory",
                "Read device identity, sizes and mount metadata only.",
                "storage.inventory",
                json!({}),
            ),
            Action {
                title: "View detected devices".into(),
                detail: "Device existence does not establish read/write support.".into(),
                kind: ActionKind::Hardware(crate::hardware::Section::Storage),
            },
        ],
        Category::Reports => report_actions(state, &[false; REPORT_GROUPS.len()]),
        Category::Settings => settings_actions(state, [false; 3]),
    }
}

pub fn settings_actions(state: &Value, expanded: [bool; 3]) -> Vec<Action> {
    let mut list = Vec::new();
    let current = at(state, "/settings/theme");
    let themes = array(state, "/settings/available_themes");
    let current_name = themes
        .iter()
        .find(|theme| at(theme, "/id") == current)
        .map(|theme| at(theme, "/name"))
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| {
            if current.is_empty() {
                "Not loaded".into()
            } else {
                current.clone()
            }
        });
    list.push(Action {
        title: format!(
            "[{}] Theme · {current_name}",
            if expanded[0] { '-' } else { '+' }
        ),
        detail: "Enter or Right opens the palettes. Left collapses the group.".into(),
        kind: ActionKind::SettingsGroup(SettingsGroup::Theme),
    });
    if expanded[0] {
        for (index, theme) in themes.iter().enumerate() {
            let id = at(theme, "/id");
            let name = at(theme, "/name");
            let name = if name.is_empty() { &id } else { &name };
            list.push(Action {title:format!("  {} {name}{}",if index+1==themes.len(){"└─"}else{"├─"},if id==current{" · selected"}else{""}),
            detail:"Save this palette on the diagnostic USB. Colors update immediately; browser colors update on reload.".into(),
            kind:ActionKind::Request("settings.set",json!({"theme":id}))});
        }
    }
    let size = state
        .pointer("/settings/font_size")
        .and_then(Value::as_u64)
        .unwrap_or(16)
        .clamp(10, 24);
    list.push(Action {
        title: format!("[{}] Font · {size} pt", if expanded[1] { '-' } else { '+' }),
        detail:
            "JetBrains Mono Nerd Font. Enter opens font-size controls; Left collapses the group."
                .into(),
        kind: ActionKind::SettingsGroup(SettingsGroup::Font),
    });
    if expanded[1] {
        list.push(request(
            "  ├─ Smaller font",
            "Decrease the JetBrains Mono Nerd Font size (minimum 10 pt).",
            "settings.set",
            json!({"font_size":size.saturating_sub(1).max(10)}),
        ));
        list.push(request(
            "  └─ Larger font",
            "Increase the JetBrains Mono Nerd Font size (maximum 24 pt).",
            "settings.set",
            json!({"font_size":(size+1).min(24)}),
        ));
    }
    list.push(Action {
        title: format!("[{}] About", if expanded[2] { '-' } else { '+' }),
        detail: "Creator and public project links. Enter expands; Left collapses the group.".into(),
        kind: ActionKind::SettingsGroup(SettingsGroup::About),
    });
    if expanded[2] {
        list.push(Action {
            title: "  └─ Creator credits".into(),
            detail: "meerzulee · public GitHub profile and psX project.".into(),
            kind: ActionKind::Credits,
        });
    }
    list.push(Action {
        title: "SSH setup".into(),
        detail: "Set up access from your computer while preserving the owner key.".into(),
        kind: ActionKind::SshSetup,
    });
    list
}

pub fn report_actions(state: &Value, expanded: &[bool; REPORT_GROUPS.len()]) -> Vec<Action> {
    let mut list = vec![request(
        "Refresh saved reports",
        "Load saved results from the diagnostic USB.",
        "results.list",
        json!({}),
    )];
    for (group, name) in REPORT_GROUPS.iter().enumerate() {
        let mut reports: Vec<_> = array(state, "/results")
            .iter()
            .filter(|report| report_group(report) == group)
            .collect();
        if reports.is_empty() {
            continue;
        }
        reports.sort_by(|a, b| report_time(b).cmp(report_time(a)));
        list.push(Action {
            title: format!(
                "[{}] {name} ({})",
                if expanded[group] { '-' } else { '+' },
                reports.len()
            ),
            detail:
                "Enter toggles this group. Right expands; Left collapses. Reports are newest first."
                    .into(),
            kind: ActionKind::ReportGroup(group),
        });
        if !expanded[group] {
            continue;
        }
        for report in reports {
            list.push(Action {
                title: report_name(report, state),
                detail: format!(
                    "{} · {} · {}\n{}",
                    status_label(&at(report, "/status")),
                    report_boot_label(state, report),
                    report_time(report),
                    at(report, "/message")
                ),
                kind: ActionKind::Report(report.clone()),
            });
        }
    }
    list
}

pub fn demo_state() -> Value {
    json!({"system":{"hostname":"psx-demo","kernel_release":"DEMO · native-dev3","uptime_seconds":125,
        "cpu":{"model":"AMD Jaguar · example console","logical":8,"usage_percent":4},
        "memory":{"total_bytes":6442450944_u64,"available_bytes":4294967296_u64}},
        "inventory":{"pci":[{"bdf":"00:01.0","vendor":"0x1002","device":"0x9920","class":"0x030000"}],
            "usb":[{"product":"Example USB keyboard"}],"thermal":[{"type":"CPU","temperature_millicelsius":"48000"}],
            "drm_nodes":["card0","renderD128"],"storage":{"devices":[{"name":"sda","model":"Example diagnostic USB","sectors_512b":"30840832"}]}},
        "network":{"interfaces":[{"name":"psx0","type":"ethernet","state":"disconnected","ipv4":[]}],
            "wifi":{"available":true,"enabled":true,"access_points":[{"ssid":"Example Wi-Fi","signal":87,"security":"WPA2"}]}},
        "ssh":{"enabled":true,"port":22,"login":"root","host_key_fingerprint":"DEMO · not a console key"},
        "results":[],"jobs":[]})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn onboarding_is_explicit_even_when_a_connection_already_exists() {
        let mut state = demo_state();
        state["network"]["interfaces"][0]["ipv4"] = json!(["192.0.2.7/24"]);
        state["network"]["interfaces"][0]["state"] = json!("connected");
        assert!(connected(&state));
        let list = actions(Category::Network, &state, true);
        assert!(matches!(list.first().unwrap().kind, ActionKind::Continue));
        assert_eq!(list.first().unwrap().title, "Continue to diagnostics");
        assert_eq!(list[1].title, "Disconnect Ethernet");
        assert!(list[1].detail.contains("SSH"));
    }
    #[test]
    fn network_menu_matches_each_physical_link_and_ignores_ap_interfaces() {
        for (ethernet, wifi) in [(false, false), (true, false), (false, true), (true, true)] {
            let state = json!({"network":{"interfaces":[
                {"name":"psx0","type":"ethernet","state":if ethernet {"connected"} else {"disconnected"}},
                {"name":"ap0","type":"wifi","state":"connected"},
                {"name":"ap1","type":"wifi","state":"connected"},
                {"name":"wlan0","type":"wifi","state":if wifi {"connected"} else {"disconnected"}}
            ]}});
            let list = actions(Category::Network, &state, true);
            assert_eq!(
                matches!(list[0].kind, ActionKind::Continue),
                ethernet || wifi
            );
            for (connected, label) in [(ethernet, "Ethernet"), (wifi, "Wi-Fi")] {
                assert!(list.iter().any(|action| action.title
                    == format!(
                        "{} {}",
                        if connected { "Disconnect" } else { "Connect" },
                        label
                    )));
                assert!(!list.iter().any(|action| action.title
                    == format!(
                        "{} {}",
                        if connected { "Connect" } else { "Disconnect" },
                        label
                    )));
            }
        }
    }
    #[test]
    fn absent_or_disconnected_interfaces_never_invent_an_ip() {
        assert!(ip(&demo_state()).is_empty());
        assert!(!connected(&json!({})));
    }
    #[test]
    fn category_actions_have_no_shell_or_power_controls() {
        for category in CATEGORIES {
            for item in actions(category, &demo_state(), false) {
                if let ActionKind::Request(action, _) = item.kind {
                    assert!(
                        [
                            "test.run",
                            "quick",
                            "network.scan",
                            "network.ethernet.connect",
                            "graphics.launch",
                            "results.list",
                            "settings.set"
                        ]
                        .contains(&action)
                    );
                }
            }
        }
    }
    #[test]
    fn reports_group_hardware_keep_ties_stable_and_include_cached_history() {
        let mut state = json!({"identity":{"boot_id":"current"},"results":[
            {"run_id":"other","test_id":"custom.probe"},
            {"run_id":"old","test_id":"cpu.compute","started_at":"2026-10-01T00:00:00Z","boot_id":"earlier"},
            {"run_id":"uvd","test_id":"uvd.avc"},
            {"run_id":"gpu","test_id":"gpu.vulkan.render"},
            {"run_id":"browser","test_id":"browser.video","title":"Firefox · Video playback"},
            {"run_id":"memory","test_id":"memory.pattern"},
            {"run_id":"new1","test_id":"cpu.compute","started_at":"2026-11-01T00:00:00Z","boot_id":"current"},
            {"run_id":"new2","test_id":"cpu.compute","started_at":"2026-11-01T00:00:00Z","identity":{"boot_id":"current"}},
            {"run_id":"net","test_id":"ethernet.status"},
            {"run_id":"audio","test_id":"audio.44100","observation_required":true},
            {"run_id":"disk","test_id":"storage.inventory"},
            {"run_id":"system","test_id":"inventory.readonly"}
        ]});
        for index in 0..35 {
            state["results"]
                .as_array_mut()
                .unwrap()
                .push(json!({"run_id":format!("extra{index}"),"test_id":"custom.extra"}));
        }
        let actions = report_actions(&state, &[true; REPORT_GROUPS.len()]);
        let reports: Vec<_> = actions
            .iter()
            .filter_map(|action| match &action.kind {
                ActionKind::Report(value) => Some(value),
                _ => None,
            })
            .collect();
        assert_eq!(reports.len(), 47);
        let ids: Vec<_> = reports.iter().take(12).map(|r| at(r, "/run_id")).collect();
        assert_eq!(
            ids,
            [
                "new1", "new2", "old", "memory", "gpu", "browser", "uvd", "net", "audio", "disk",
                "system", "other"
            ]
        );
        assert_eq!(report_boot_label(&state, reports[0]), "This boot");
        assert_eq!(report_boot_label(&state, reports[1]), "This boot");
        assert_eq!(report_boot_label(&state, reports[2]), "Earlier boot");
        assert!(reports[8]["observation_required"].as_bool().unwrap());
        assert_eq!(actions[1].title, "[-] CPU (3)");
        assert_eq!(actions[2].title, "Computation");
        assert_eq!(
            actions
                .iter()
                .filter(|item| matches!(item.kind, ActionKind::ReportGroup(_)))
                .count(),
            10
        );
        assert_eq!(
            report_actions(&state, &[false; REPORT_GROUPS.len()]).len(),
            11
        );
    }
    #[test]
    fn display_settings_keep_font_bounds_and_use_only_available_theme_ids() {
        for size in [10, 16, 24] {
            let state = json!({"settings":{"font_size":size,"theme":"olive","available_themes":[
                {"id":"olive","name":"Olive"},{"id":"nord","name":"Nord"}]}});
            assert_eq!(settings_actions(&state, [false; 3]).len(), 4);
            let actions = settings_actions(&state, [true; 3]);
            let font_controls = actions
                .iter()
                .filter_map(|action| match &action.kind {
                    ActionKind::Request("settings.set", params)
                        if params.get("font_size").is_some() =>
                    {
                        Some(params)
                    }
                    _ => None,
                })
                .collect::<Vec<_>>();
            assert_eq!(font_controls.len(), 2);
            for params in font_controls {
                assert!((10..=24).contains(&params["font_size"].as_u64().unwrap()));
            }
            assert!(matches!(
                actions[0].kind,
                ActionKind::SettingsGroup(SettingsGroup::Theme)
            ));
            assert!(actions[1].title.contains("selected"));
            let ActionKind::Request("settings.set", params) = &actions[2].kind else {
                panic!("not a theme setting")
            };
            assert_eq!(params, &json!({"theme":"nord"}));
        }
    }
    #[test]
    fn browser_choices_share_the_lab_and_label_the_optional_provider() {
        let choices = actions(Category::Browser, &demo_state(), false);
        assert_eq!(choices[0].title, "Firefox · Run all");
        assert!(choices[1].title.contains("Chromium"));
        assert!(choices[1].detail.contains("384 MiB USB provider"));
        assert_eq!(choices[4].title, "ProtonDB compatibility");
        assert!(choices[4].detail.contains("Community Linux"));
        assert!(choices[4].detail.contains("PS4 compatibility unverified"));
        let apps: Vec<_> = choices
            .iter()
            .map(|choice| match &choice.kind {
                ActionKind::Request("graphics.launch", params) => at(params, "/app"),
                _ => panic!("Browser selection must remain a fixed graphics action"),
            })
            .collect();
        assert_eq!(
            apps,
            [
                "browser",
                "chromium",
                "browser-manual",
                "chromium-manual",
                "protondb"
            ]
        );
    }
}
