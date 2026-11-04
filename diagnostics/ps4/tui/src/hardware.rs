// SPDX-License-Identifier: MIT
//! Presentation of the cached inventory. No filesystem, commands or RPCs.
use crate::model;
use serde_json::{Value, json};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Section {
    System,
    Cpu,
    Memory,
    Gpu,
    Uvd,
    Network,
    Audio,
    Storage,
    Pci,
    Usb,
    Input,
    Thermal,
}
pub const SECTIONS: [Section; 12] = [
    Section::System,
    Section::Cpu,
    Section::Memory,
    Section::Gpu,
    Section::Uvd,
    Section::Network,
    Section::Audio,
    Section::Storage,
    Section::Pci,
    Section::Usb,
    Section::Input,
    Section::Thermal,
];
impl Section {
    pub fn title(self) -> &'static str {
        match self {
            Self::System => "System & boot",
            Self::Cpu => "CPU",
            Self::Memory => "Memory",
            Self::Gpu => "Graphics / GPU",
            Self::Uvd => "Video / UVD",
            Self::Network => "Network",
            Self::Audio => "Audio",
            Self::Storage => "Storage",
            Self::Pci => "PCI devices",
            Self::Usb => "USB devices",
            Self::Input => "Input devices",
            Self::Thermal => "Temperatures",
        }
    }
}
pub enum Row {
    Heading(String),
    Field(String, String),
    Note(String),
}
pub struct Page {
    pub title: &'static str,
    pub summary: String,
    pub rows: Vec<Row>,
}
impl Page {
    fn new(section: Section, summary: String) -> Self {
        Self {
            title: section.title(),
            summary,
            rows: vec![],
        }
    }
    fn field(&mut self, label: &str, value: impl Into<String>) {
        let value = value.into();
        self.rows.push(Row::Field(
            label.into(),
            if value.is_empty() {
                "Not collected".into()
            } else {
                value
            },
        ));
    }
    fn note(&mut self, value: &str) {
        self.rows.push(Row::Note(value.into()));
    }
    fn heading(&mut self, value: String) {
        self.rows.push(Row::Heading(value));
    }
}
fn clean(value: &Value) -> String {
    let full = model::text(value);
    let mut short: String = full.chars().take(256).collect();
    if full.chars().count() > 256 {
        short.push('…');
    }
    short
}
fn at(value: &Value, path: &str) -> String {
    value.pointer(path).map(clean).unwrap_or_default()
}
fn number(value: &Value) -> Option<f64> {
    value
        .as_f64()
        .or_else(|| value.as_str()?.parse().ok())
        .filter(|n| n.is_finite())
}
fn quantity(value: &Value, multiplier: f64) -> String {
    number(value)
        .map(|n| n * multiplier)
        .filter(|n| *n >= 0.0 && n.is_finite())
        .map(bytes)
        .unwrap_or_default()
}
fn bytes(mut value: f64) -> String {
    let units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let mut unit = 0;
    while value >= 1024.0 && unit < 4 {
        value /= 1024.0;
        unit += 1;
    }
    format!("{value:.1} {}", units[unit])
}
fn flag(value: &Value) -> String {
    match model::text(value).as_str() {
        "1" | "true" => "Yes".into(),
        "0" | "false" => "No".into(),
        _ => String::new(),
    }
}
fn runtime_policy(value: &Value) -> String {
    let setting = clean(value);
    match setting.as_str() {
        "1" => "Enabled".into(),
        "0" => "Disabled".into(),
        "" => String::new(),
        _ => format!("Other ({setting})"),
    }
}
fn id(value: &Value) -> String {
    let value = clean(value)
        .trim_start_matches("0x")
        .trim_start_matches("0X")
        .to_ascii_uppercase();
    if value.is_empty() { "?".into() } else { value }
}
fn driver(value: &Value) -> String {
    let full = at(value, "/driver");
    full.rsplit('/')
        .find(|s| !s.is_empty())
        .unwrap_or("")
        .to_owned()
}
fn class(value: &Value) -> Option<u32> {
    u32::from_str_radix(
        at(value, "/class")
            .trim_start_matches("0x")
            .trim_start_matches("0X"),
        16,
    )
    .ok()
}
fn selected_pci(section: Section, inventory: &Value) -> Vec<Value> {
    model::array(inventory, "/pci")
        .iter()
        .filter(|device| match section {
            Section::Gpu => class(device).is_some_and(|c| c >> 16 == 3),
            Section::Network => class(device).is_some_and(|c| c >> 16 == 2),
            Section::Audio => class(device).is_some_and(|c| matches!(c >> 8, 0x0401 | 0x0403)),
            _ => true,
        })
        .cloned()
        .collect()
}
fn pci_rows(page: &mut Page, devices: &[Value]) {
    for device in devices.iter().take(512) {
        page.heading(format!(
            "{} · {}:{}",
            at(device, "/bdf"),
            id(&device["vendor"]),
            id(&device["device"])
        ));
        page.field("Driver", driver(device));
        page.field("PCI class", at(device, "/class"));
        page.field("Revision", at(device, "/revision"));
        page.field("Driver path", at(device, "/driver"));
    }
    if devices.is_empty() {
        page.note("No matching PCI metadata collected.");
    }
    if devices.len() > 512 {
        page.note("First 512 entries shown; see Advanced for all entries.");
    }
}
pub fn page(section: Section, inventory: &Value) -> Page {
    let mut page = Page::new(section, String::new());
    match section {
        Section::System => {
            page.summary = "Boot configuration & selected policy".into();
            page.field(
                "Config exposed",
                flag(&inventory["config"]["proc_config_gz_present"]),
            );
            page.field(
                "Module directory",
                flag(&inventory["config"]["module_directory_present"]),
            );
            page.heading("Selected boot policy".into());
            if let Some(policy) = inventory["selected_boot_policy"].as_object() {
                for (key, value) in policy {
                    let label = match key.as_str() {
                        "amdgpu.ps4_uvd_runtime" => "UVD runtime policy",
                        "psx_mts_net.autoarm" => "Ethernet auto-arm",
                        "modprobe.blacklist" => "Blocked modules",
                        _ => key,
                    };
                    page.field(
                        label,
                        if matches!(
                            key.as_str(),
                            "amdgpu.ps4_uvd_runtime" | "psx_mts_net.autoarm"
                        ) {
                            runtime_policy(value)
                        } else {
                            clean(value)
                        },
                    );
                }
            }
            if inventory["selected_boot_policy"]
                .as_object()
                .is_none_or(|v| v.is_empty())
            {
                page.note("No selected boot policy fields collected.");
            }
        }
        Section::Cpu => {
            let models = model::array(inventory, "/cpu/models")
                .iter()
                .map(clean)
                .collect::<Vec<_>>()
                .join(", ");
            let count = at(inventory, "/cpu/listed_processors");
            page.summary = if models.is_empty() {
                "CPU model not collected".into()
            } else {
                models.clone()
            };
            page.field("Processor", models);
            page.field("Logical CPUs", count);
            page.field("Online CPU IDs", at(inventory, "/cpu/online"));
            page.note("Logical CPUs; physical topology not collected.");
        }
        Section::Memory => {
            let total = quantity(&inventory["memory"]["MemTotal_kib"], 1024.0);
            let available = quantity(&inventory["memory"]["MemAvailable_kib"], 1024.0);
            page.summary = if total.is_empty() {
                "Memory sizes not collected".into()
            } else {
                format!("{total} total")
            };
            page.field("Total RAM", total);
            page.field("Available RAM", available);
            page.field(
                "Total swap",
                quantity(&inventory["memory"]["SwapTotal_kib"], 1024.0),
            );
            page.note("Available RAM is a snapshot estimate.");
        }
        Section::Gpu | Section::Network | Section::Audio | Section::Pci => {
            let devices = selected_pci(section, inventory);
            page.summary = format!(
                "{} PCI function{} listed",
                devices.len(),
                if devices.len() == 1 { "" } else { "s" }
            );
            if section == Section::Gpu {
                let nodes = model::array(inventory, "/drm_nodes");
                page.field(
                    "DRM nodes",
                    if nodes.is_empty() {
                        String::new()
                    } else {
                        nodes.iter().map(clean).collect::<Vec<_>>().join(", ")
                    },
                );
                page.note("DRM interfaces; rendering unmeasured.");
            } else if section == Section::Network {
                page.note("PCI adapters; link state not collected.");
            } else if section == Section::Audio {
                page.note("ALSA/playback not collected.");
            }
            pci_rows(&mut page, &devices);
        }
        Section::Uvd => {
            page.summary = "Runtime boot policy".into();
            page.field(
                "Runtime policy",
                runtime_policy(&inventory["selected_boot_policy"]["amdgpu.ps4_uvd_runtime"]),
            );
            page.note("Decode unmeasured.");
        }
        Section::Storage => {
            let devices = model::array(inventory, "/storage/devices");
            page.summary = format!("{} block devices listed", devices.len());
            page.note("Block entries include partitions/loops; I/O untested.");
            for device in devices.iter().take(512) {
                page.heading(format!("/dev/{}", at(device, "/name")));
                page.field("Model", at(device, "/model"));
                page.field("Vendor", at(device, "/vendor"));
                page.field("Capacity", quantity(&device["sectors_512b"], 512.0));
                page.field("Device node", flag(&device["device_node_present"]));
                page.field("Read-only flag", flag(&device["read_only"]));
                page.field("Rotational flag", flag(&device["rotational"]));
                page.field("Device path", at(device, "/sysfs_path"));
            }
            if devices.is_empty() {
                page.note("Block-device metadata not collected.");
            }
            if devices.len() > 512 {
                page.note("First 512 devices shown; see Advanced for all entries.");
            }
        }
        Section::Usb => {
            let devices = model::array(inventory, "/usb");
            page.summary = format!("{} USB devices listed", devices.len());
            for device in devices.iter().take(512) {
                let name = at(device, "/product");
                page.heading(if name.is_empty() {
                    at(device, "/sysfs_name")
                } else {
                    name
                });
                page.field(
                    "USB ID",
                    format!("{}:{}", id(&device["idVendor"]), id(&device["idProduct"])),
                );
                page.field("Bus location", at(device, "/sysfs_name"));
                page.field(
                    "Reported speed",
                    number(&device["speed"])
                        .filter(|v| *v >= 0.0)
                        .map(|v| format!("{v} Mb/s"))
                        .unwrap_or_default(),
                );
            }
            if devices.is_empty() {
                page.note("USB metadata not collected.");
            }
            page.note("Bus speed; throughput untested.");
            if devices.len() > 512 {
                page.note("First 512 devices shown; see Advanced for all entries.");
            }
        }
        Section::Input => {
            let nodes = model::array(inventory, "/input_nodes");
            page.summary = format!("{} input entries listed", nodes.len());
            for node in nodes.iter().take(512) {
                page.field("Input entry", format!("/dev/input/{}", clean(node)));
            }
            if nodes.is_empty() {
                page.note("Input entries not collected.");
            } else {
                page.note("Names only; key/button events untested.");
            }
            if nodes.len() > 512 {
                page.note("First 512 entries shown; see Advanced for all entries.");
            }
        }
        Section::Thermal => {
            let sensors = model::array(inventory, "/thermal");
            page.summary = format!("{} thermal zones listed", sensors.len());
            for sensor in sensors.iter().take(512) {
                let name = at(sensor, "/type");
                page.heading(if name.is_empty() {
                    at(sensor, "/zone")
                } else {
                    name
                });
                page.field(
                    "Temperature",
                    number(&sensor["temperature_millicelsius"])
                        .map(|v| format!("{:.1} °C", v / 1000.0))
                        .unwrap_or_default(),
                );
                page.field("Sensor", at(sensor, "/zone"));
            }
            if sensors.is_empty() {
                page.note("Temperature readings not collected.");
            } else {
                page.note("Snapshot readings.");
            }
            if sensors.len() > 512 {
                page.note("First 512 sensors shown; see Advanced for all entries.");
            }
        }
    }
    page
}
pub fn raw(section: Section, inventory: &Value) -> Value {
    match section {
        Section::System => {
            json!({"config":inventory["config"],"selected_boot_policy":inventory["selected_boot_policy"],"scope":inventory["scope"]})
        }
        Section::Cpu => inventory["cpu"].clone(),
        Section::Memory => inventory["memory"].clone(),
        Section::Gpu => {
            json!({"pci":selected_pci(section,inventory),"drm_nodes":inventory["drm_nodes"]})
        }
        Section::Network | Section::Audio | Section::Pci => json!(selected_pci(section, inventory)),
        Section::Uvd => {
            json!({"amdgpu.ps4_uvd_runtime":inventory["selected_boot_policy"]["amdgpu.ps4_uvd_runtime"]})
        }
        Section::Storage => inventory["storage"].clone(),
        Section::Usb => inventory["usb"].clone(),
        Section::Input => inventory["input_nodes"].clone(),
        Section::Thermal => inventory["thermal"].clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fields(page: &Page) -> String {
        page.rows
            .iter()
            .map(|row| match row {
                Row::Field(k, v) => format!("{k}: {v}"),
                Row::Note(v) | Row::Heading(v) => v.clone(),
            })
            .collect::<Vec<_>>()
            .join("\n")
    }
    #[test]
    fn units_preserve_missing_values_and_distinguish_device_flags_from_support() {
        let inventory = json!({"memory":{"MemTotal_kib":8388608,"MemAvailable_kib":2097152,"SwapTotal_kib":0},
            "storage":{"devices":[{"name":"sda1","sectors_512b":"33554432","read_only":"0","rotational":"0"}]},
            "thermal":[{"type":"CPU","temperature_millicelsius":"48500"},{"type":"Missing","temperature_millicelsius":null}]});
        let memory = fields(&page(Section::Memory, &inventory));
        assert!(memory.contains("Total RAM: 8.0 GiB"));
        assert!(memory.contains("Total swap: 0.0 B"));
        let storage = fields(&page(Section::Storage, &inventory));
        assert!(storage.contains("Capacity: 16.0 GiB"));
        assert!(storage.contains("Read-only flag: No"));
        assert!(!storage.contains("SSD"));
        let thermal = fields(&page(Section::Thermal, &inventory));
        assert!(thermal.contains("48.5 °C"));
        assert!(thermal.contains("Temperature: Not collected"));
        assert!(!thermal.contains("0.0 °C"));
        assert!(fields(&page(Section::Memory, &Value::Null)).contains("Total RAM: Not collected"));
    }
    #[test]
    fn pci_groups_keep_factual_ids_and_do_not_invent_audio_or_uvd_test_results() {
        let inventory = json!({"pci":[{"bdf":"0000:00:01.0","vendor":"0x1002","device":"0x9920","class":"0x030000","driver":"/sys/bus/pci/drivers/amdgpu"},
            {"class":"0x020000"},{"class":"0x040300"},{"class":"0x060000"}],"drm_nodes":["card0","renderD128"]});
        assert_eq!(selected_pci(Section::Gpu, &inventory).len(), 1);
        assert_eq!(selected_pci(Section::Network, &inventory).len(), 1);
        assert_eq!(selected_pci(Section::Audio, &inventory).len(), 1);
        assert_eq!(selected_pci(Section::Pci, &inventory).len(), 4);
        let gpu = fields(&page(Section::Gpu, &inventory));
        assert!(gpu.contains("1002:9920"));
        assert!(gpu.contains("Driver: amdgpu"));
        assert!(fields(&page(Section::Audio, &inventory)).contains("not collected"));
        assert!(fields(&page(Section::Uvd, &inventory)).contains("Decode unmeasured"));
        let policy = json!({"selected_boot_policy":{"amdgpu.ps4_uvd_runtime":"1"}});
        assert!(fields(&page(Section::Uvd, &policy)).contains("Runtime policy: Enabled"));
    }
}
