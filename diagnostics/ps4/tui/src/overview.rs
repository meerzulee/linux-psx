// SPDX-License-Identifier: MIT
//! Read-only summaries of cached inventory and current-boot functional receipts.
use crate::model::{self, Category};
use serde_json::Value;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    Passed,
    Failed,
    NotTested,
    Running,
    Inconclusive,
    Blocked,
    Unsupported,
    Deferred,
    Degraded,
    Partial,
}
impl Status {
    pub fn label(self) -> &'static str {
        match self {
            Self::Passed => "Passed",
            Self::Failed => "Failed",
            Self::NotTested => "Not tested",
            Self::Running => "Running",
            Self::Inconclusive => "Inconclusive",
            Self::Blocked => "Blocked",
            Self::Unsupported => "Unsupported",
            Self::Deferred => "Deferred",
            Self::Degraded => "Degraded",
            Self::Partial => "Partly tested",
        }
    }
    pub fn marker(self, rich: bool) -> &'static str {
        match (self, rich) {
            (Self::Passed, true) => "✓",
            (Self::Failed, true) => "✗",
            (Self::Passed, false) => "+",
            (Self::Failed, false) => "!",
            (Self::Running, _) => ">",
            (Self::NotTested, _) => "?",
            _ => "~",
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Component {
    Cpu,
    Memory,
    Temperature,
    Gpu,
    OpenGl,
    Vulkan,
    Uvd,
    Browser,
    Ethernet,
    Wifi,
    Bluetooth,
    Storage,
    Audio,
    Usb,
    Input,
    Display,
    Power,
}
pub const COMPONENTS: [Component; 17] = [
    Component::Cpu,
    Component::Memory,
    Component::Temperature,
    Component::Gpu,
    Component::OpenGl,
    Component::Vulkan,
    Component::Uvd,
    Component::Browser,
    Component::Ethernet,
    Component::Wifi,
    Component::Bluetooth,
    Component::Storage,
    Component::Audio,
    Component::Usb,
    Component::Input,
    Component::Display,
    Component::Power,
];
impl Component {
    pub fn title(self) -> &'static str {
        match self {
            Self::Cpu => "CPU",
            Self::Memory => "Memory",
            Self::Temperature => "Temperature",
            Self::Gpu => "GPU",
            Self::OpenGl => "OpenGL",
            Self::Vulkan => "Vulkan",
            Self::Uvd => "UVD video",
            Self::Browser => "Browser",
            Self::Ethernet => "Ethernet",
            Self::Wifi => "Wi-Fi",
            Self::Bluetooth => "Bluetooth",
            Self::Storage => "Storage",
            Self::Audio => "Audio",
            Self::Usb => "USB",
            Self::Input => "Input",
            Self::Display => "Display / Wayland",
            Self::Power => "Power",
        }
    }
    pub fn icon(self, rich: bool) -> &'static str {
        if !rich {
            return "-";
        }
        match self {
            Self::Cpu | Self::Memory => "",
            Self::Temperature => "",
            Self::Gpu | Self::OpenGl | Self::Vulkan | Self::Display => "",
            Self::Uvd => "",
            Self::Browser => "",
            Self::Ethernet => "",
            Self::Wifi => "",
            Self::Bluetooth => "",
            Self::Storage => "",
            Self::Audio => "",
            Self::Usb => "",
            Self::Input => "",
            Self::Power => "",
        }
    }
    pub fn category(self) -> Category {
        match self {
            Self::Cpu | Self::Memory | Self::Temperature => Category::Cpu,
            Self::Gpu | Self::OpenGl | Self::Vulkan | Self::Uvd | Self::Display => {
                Category::Graphics
            }
            Self::Browser => Category::Browser,
            Self::Ethernet | Self::Wifi | Self::Bluetooth => Category::Network,
            Self::Storage => Category::Storage,
            Self::Audio => Category::Audio,
            Self::Usb | Self::Input => Category::System,
            Self::Power => Category::Power,
        }
    }
    pub fn scope(self) -> &'static str {
        match self {
            Self::Cpu => "Known computation only; this does not test every core or sustained load.",
            Self::Memory => "Owned-memory patterns only; total physical RAM is not fully tested.",
            Self::Temperature => "Sensor snapshot only; cooling and endurance remain untested.",
            Self::Gpu => "Both native OpenGL and Vulkan reference-pixel checks are required here.",
            Self::OpenGl | Self::Vulkan => {
                "Native reference pixels. API/device detection alone cannot pass."
            }
            Self::Uvd => {
                "One exact AVC reference decode. Browser video is separate; retained-DMA lifecycle remains experimental."
            }
            Self::Browser => {
                "Saved browser-local Canvas/WebGL/animation/video checks; this does not establish GPU or UVD hardware decoding."
            }
            Self::Ethernet => {
                "An interface-bound HTTPS check is required; link-up or an address alone cannot pass."
            }
            Self::Wifi | Self::Bluetooth => {
                "Detection and connection are shown separately from functional qualification."
            }
            Self::Storage => {
                "Cached block-device inventory only; no disk read, write or installation is started here."
            }
            Self::Audio => {
                "44.1 kHz requires the saved listening confirmation. Native 48 kHz is deferred."
            }
            Self::Usb | Self::Input => "Detected nodes only; attachment is not a functional test.",
            Self::Display => {
                "Native Wayland reference pixels only; HDMI modes and physical display checks remain separate."
            }
            Self::Power => {
                "Power-request receipts do not prove shutdown or restart. Physical/UART observation is separate."
            }
        }
    }
}
pub struct Entry {
    pub component: Component,
    pub detected: String,
    pub status: Status,
}

fn boot(record: &Value) -> String {
    let flat = model::at(record, "/boot_id");
    let nested = model::at(record, "/identity/boot_id");
    if !flat.is_empty() && !nested.is_empty() && flat != nested {
        return String::new();
    }
    if flat.is_empty() { nested } else { flat }
}
fn latest<'a>(state: &'a Value, id: &str) -> Option<&'a Value> {
    let current = model::at(state, "/identity/boot_id");
    if current.is_empty() {
        return None;
    }
    let records = state
        .pointer("/overview_results")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_else(|| model::array(state, "/results"));
    records
        .iter()
        .filter(|r| model::at(r, "/test_id") == id && boot(r) == current)
        .max_by(|a, b| {
            model::report_time(a)
                .cmp(model::report_time(b))
                .then_with(|| model::at(a, "/run_id").cmp(&model::at(b, "/run_id")))
        })
}
fn receipt(record: Option<&Value>, functional: bool, uvd: bool) -> Status {
    let Some(record) = record else {
        return Status::NotTested;
    };
    if matches!(model::at(record, "/phase").as_str(), "running" | "started") {
        return Status::Running;
    }
    match model::at(record, "/status").as_str() {
        "FAIL" => Status::Failed,
        "BLOCKED" => Status::Blocked,
        "UNSUPPORTED" => Status::Unsupported,
        "DEFERRED" => Status::Deferred,
        "DEGRADED" => Status::Degraded,
        "INCONCLUSIVE" => Status::Inconclusive,
        "PASS"
            if functional
                && model::at(record, "/phase") == "completed"
                && (uvd || record["stop_required"].as_bool() != Some(true)) =>
        {
            Status::Passed
        }
        "PASS" if functional => Status::Inconclusive,
        _ => Status::NotTested,
    }
}
fn aggregate(state: &Value, ids: &[&str], functional: bool, uvd: bool) -> Status {
    let values = ids
        .iter()
        .map(|id| receipt(latest(state, id), functional, uvd))
        .collect::<Vec<_>>();
    for status in [
        Status::Failed,
        Status::Running,
        Status::Blocked,
        Status::Inconclusive,
        Status::Degraded,
        Status::Unsupported,
        Status::Deferred,
    ] {
        if values.contains(&status) {
            return status;
        }
    }
    if !values.is_empty() && values.iter().all(|s| *s == Status::Passed) {
        Status::Passed
    } else if values.contains(&Status::Passed) {
        Status::Partial
    } else {
        Status::NotTested
    }
}
fn status(component: Component, state: &Value) -> Status {
    match component {
        Component::Cpu => aggregate(state, &["cpu.compute"], true, false),
        Component::Memory => aggregate(state, &["memory.pattern"], true, false),
        Component::Gpu => aggregate(
            state,
            &["gpu.opengl.render", "gpu.vulkan.render"],
            true,
            false,
        ),
        Component::OpenGl => aggregate(state, &["gpu.opengl.render"], true, false),
        Component::Vulkan => aggregate(state, &["gpu.vulkan.render"], true, false),
        Component::Uvd => {
            let record = ["uvd.avc", "uvd.native720", "uvd.native1080"]
                .iter()
                .filter_map(|id| latest(state, id))
                .max_by_key(|r| model::report_time(r));
            receipt(record, true, true)
        }
        Component::Browser => aggregate(
            state,
            &[
                "browser.canvas2d",
                "browser.webgl",
                "browser.animation",
                "browser.video",
            ],
            true,
            false,
        ),
        Component::Ethernet => aggregate(state, &["ethernet.https"], true, false),
        Component::Wifi => aggregate(state, &["wireless.wifi"], false, false),
        Component::Bluetooth => aggregate(state, &["wireless.bluetooth"], false, false),
        Component::Audio => {
            let record = latest(state, "audio.44100");
            let confirmed = record.is_some_and(|r| {
                model::at(r, "/operator_observation/answer") == "yes"
                    || model::at(r, "/message")
                        .starts_with("Operator observation of this fixture: yes")
            });
            let value = receipt(record, confirmed, false);
            if value == Status::NotTested
                && record.is_some_and(|r| model::at(r, "/status") == "PASS")
            {
                Status::Inconclusive
            } else {
                value
            }
        }
        Component::Display => aggregate(state, &["wayland.native"], true, false),
        Component::Power => {
            let request = &state["power"]["current_request"];
            if !boot(request).is_empty() && boot(request) == model::at(state, "/identity/boot_id") {
                if matches!(
                    model::at(request, "/dispatch_state").as_str(),
                    "preparing"
                        | "not_sent"
                        | "issuing_forced_icc_restart"
                        | "issuing_kernel_restart"
                        | "issuing_shutdown"
                ) {
                    Status::Running
                } else {
                    Status::Inconclusive
                }
            } else {
                aggregate(state, &["power.off", "power.reboot"], false, false)
            }
        }
        _ => Status::NotTested,
    }
}
fn numeric(value: Option<&Value>) -> Option<f64> {
    value
        .and_then(|v| v.as_f64().or_else(|| v.as_str()?.parse().ok()))
        .filter(|v| v.is_finite() && *v >= 0.0)
}
fn count(state: &Value, path: &str, label: &str) -> String {
    state
        .pointer(path)
        .and_then(Value::as_array)
        .map(|v| format!("{} {label}", v.len()))
        .unwrap_or_else(|| "Not collected".into())
}
fn link(state: &Value, wifi: bool) -> String {
    model::array(state, "/network/interfaces")
        .iter()
        .find(|r| {
            if wifi {
                model::at(r, "/type") == "wifi" && !model::at(r, "/name").starts_with("p2p")
            } else {
                model::at(r, "/type") == "ethernet" || model::at(r, "/name") == "psx0"
            }
        })
        .map(|r| {
            let state = model::at(r, "/state");
            format!(
                "{} · {}",
                model::at(r, "/name"),
                if state.is_empty() {
                    "State unknown"
                } else {
                    &state
                }
            )
        })
        .unwrap_or_else(|| "Adapter not reported".into())
}
fn detected(component: Component, state: &Value) -> String {
    match component {
        Component::Cpu => numeric(state.pointer("/system/cpu/logical"))
            .map(|n| format!("{n:.0} logical CPUs"))
            .unwrap_or_else(|| "Not collected".into()),
        Component::Memory => numeric(state.pointer("/system/memory/total_bytes"))
            .filter(|n| *n > 0.0)
            .map(|n| format!("{:.1} GiB total", n / 1073741824.0))
            .unwrap_or_else(|| "Not collected".into()),
        Component::Temperature => model::array(state, "/inventory/thermal")
            .iter()
            .find_map(|r| numeric(r.get("temperature_millicelsius")))
            .map(|n| format!("{:.1} °C snapshot", n / 1000.0))
            .unwrap_or_else(|| "Not collected".into()),
        Component::Gpu => {
            let count = model::array(state, "/inventory/pci")
                .iter()
                .filter(|r| {
                    u32::from_str_radix(model::at(r, "/class").trim_start_matches("0x"), 16)
                        .is_ok_and(|v| v >> 16 == 3)
                })
                .count();
            if count > 0 {
                format!("{count} display PCI device(s)")
            } else {
                "Not collected".into()
            }
        }
        Component::OpenGl | Component::Vulkan => "Reference-image check".into(),
        Component::Uvd => match state
            .pointer("/identity/declared_artifacts/uvd_runtime")
            .and_then(Value::as_bool)
        {
            Some(true) => "Runtime enabled".into(),
            Some(false) => "Runtime disabled".into(),
            None => "Policy not collected".into(),
        },
        Component::Browser => "Browser-local checks".into(),
        Component::Ethernet => link(state, false),
        Component::Wifi => link(state, true),
        Component::Bluetooth => "Inventory not collected".into(),
        Component::Storage => count(state, "/inventory/storage/devices", "block devices"),
        Component::Audio => "44.1 kHz listening check".into(),
        Component::Usb => count(state, "/inventory/usb", "USB devices"),
        Component::Input => count(state, "/inventory/input_nodes", "input nodes"),
        Component::Display => count(state, "/inventory/drm_nodes", "DRM nodes"),
        Component::Power => "Shutdown / Orbis controls".into(),
    }
}
pub fn entries(state: &Value) -> Vec<Entry> {
    COMPONENTS
        .iter()
        .map(|component| Entry {
            component: *component,
            detected: detected(*component, state),
            status: status(*component, state),
        })
        .collect()
}
pub fn rich_icons(term: &str, display: &str) -> bool {
    term != "linux" && display == ":0"
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn saved(id: &str, status: &str, boot: &str, time: &str) -> Value {
        json!({"run_id":format!("{id}-{time}"),"test_id":id,"status":status,"phase":"completed","boot_id":boot,"started_at":time})
    }
    #[test]
    fn detection_old_boot_and_observation_passes_never_turn_components_green() {
        let mut state = model::demo_state();
        state["identity"] = json!({"boot_id":"now"});
        state["network"]["interfaces"][0]["state"] = json!("connected");
        state["results"] = json!([
            saved("cpu.compute", "PASS", "earlier", "2"),
            saved("inventory.readonly", "PASS", "now", "2"),
            saved("ethernet.connect", "PASS", "now", "2"),
            saved("gpu.opengl.info", "PASS", "now", "2"),
            saved("browser.webgl", "PASS", "now", "2"),
            saved("storage.inventory", "PASS", "now", "2"),
            saved("power.shutdown.request", "PASS", "now", "2")
        ]);
        for component in [
            Component::Cpu,
            Component::Gpu,
            Component::Ethernet,
            Component::Storage,
            Component::Power,
        ] {
            assert_eq!(status(component, &state), Status::NotTested);
        }
        state["identity"] = json!({});
        assert_eq!(status(Component::Browser, &state), Status::NotTested);
    }
    #[test]
    fn latest_functional_results_preserve_failures_partial_and_explicit_non_pass_states() {
        let state = json!({"identity":{"boot_id":"now"},"results":[saved("cpu.compute","FAIL","now","1"),saved("cpu.compute","PASS","now","2"),saved("gpu.opengl.render","PASS","now","2"),saved("memory.pattern","BLOCKED","now","2"),saved("wireless.wifi","DEFERRED","now","2"),saved("wireless.bluetooth","UNSUPPORTED","now","2")]});
        assert_eq!(status(Component::Cpu, &state), Status::Passed);
        assert_eq!(status(Component::Gpu, &state), Status::Partial);
        assert_eq!(status(Component::Memory, &state), Status::Blocked);
        assert_eq!(status(Component::Wifi, &state), Status::Deferred);
        assert_eq!(status(Component::Bluetooth, &state), Status::Unsupported);
        let mut changed = state.clone();
        changed["results"].as_array_mut().unwrap().push(saved(
            "gpu.vulkan.render",
            "FAIL",
            "now",
            "3",
        ));
        assert_eq!(status(Component::Gpu, &changed), Status::Failed);
    }
    #[test]
    fn uvd_retained_stop_is_not_a_decode_failure_and_audio_needs_listening_evidence() {
        let mut decode = saved("uvd.native1080", "PASS", "now", "2");
        decode["stop_required"] = json!(true);
        let mut audio = saved("audio.44100", "PASS", "now", "2");
        audio["observation_required"] = json!(true);
        let mut state = json!({"identity":{"boot_id":"now"},"results":[decode,audio]});
        assert_eq!(status(Component::Uvd, &state), Status::Passed);
        assert_eq!(status(Component::Audio, &state), Status::Inconclusive);
        state["results"][1]["message"] = json!(
            "Operator observation of this fixture: yes; not native 48 kHz or broad audio acceptance"
        );
        assert_eq!(status(Component::Audio, &state), Status::Passed);
    }
    #[test]
    fn plain_console_has_text_markers_and_missing_temperature_stays_missing() {
        assert!(!rich_icons("linux", ":0"));
        assert!(!rich_icons("xterm-256color", ""));
        assert!(rich_icons("xterm-256color", ":0"));
        assert_eq!(Status::Passed.marker(false), "+");
        assert_eq!(
            detected(Component::Temperature, &json!({})),
            "Not collected"
        );
        assert_eq!(COMPONENTS.first(), Some(&Component::Cpu));
        assert_eq!(COMPONENTS.last(), Some(&Component::Power));
    }
    #[test]
    fn dedicated_current_boot_cache_retains_core_checks_and_missing_arrays_are_not_measured_zero() {
        let state = json!({"identity":{"boot_id":"now"},"results":[saved("cpu.compute","FAIL","old","5")],
            "overview_results":[saved("cpu.compute","PASS","now","2")]});
        assert_eq!(status(Component::Cpu, &state), Status::Passed);
        for component in [
            Component::Storage,
            Component::Usb,
            Component::Input,
            Component::Display,
        ] {
            assert_eq!(detected(component, &state), "Not collected");
        }
        let explicit = json!({"inventory":{"usb":[]}});
        assert_eq!(detected(Component::Usb, &explicit), "0 USB devices");
    }
}
