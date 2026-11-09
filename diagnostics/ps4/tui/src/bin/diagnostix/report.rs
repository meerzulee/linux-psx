// SPDX-License-Identifier: MIT
//! The report. Everything is appended and flushed as it happens, so a crash
//! keeps whatever was saved up to that point. It is written to memory and, when
//! a stick is present, to the stick as well.
use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, ErrorKind, Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

pub const SUMMARY: &str = "summary.txt";
pub const PROGRESS: &str = "progress.txt";
pub const CONSOLE: &str = "console.txt";
pub const BENCHMARK: &str = "benchmark.txt";
pub const KERNEL: &str = "kernel.log";
pub const CONTROLLER: &str = "controller-report.zip";
pub const ARCHIVE: &str = "diagnostix-report.zip";

const FOLDER: &str = "DiagnostiX";
const KERNEL_LIMIT: u64 = 16 * 1024 * 1024;
const READ_LIMIT: u64 = 40 * 1024 * 1024;
const O_NONBLOCK: i32 = 0o4000;

struct Sinks {
    ram: Option<File>,
    usb: Option<File>,
}

pub struct Report {
    name: String,
    ram: PathBuf,
    /// `DiagnostiX` on the stick, and this run's folder inside it.
    stick: Option<(PathBuf, PathBuf)>,
    usb_name: String,
    sinks: HashMap<String, Sinks>,
    kernel_bytes: u64,
    lost: bool,
}

fn open_append(path: &Path) -> Option<File> {
    OpenOptions::new().create(true).append(true).open(path).ok()
}

/// Replaces a file so that a crash leaves either the old or the new contents.
fn write_atomic(directory: &Path, name: &str, data: &[u8]) -> io::Result<()> {
    let temporary = directory.join(format!("{name}.tmp"));
    let mut file = File::create(&temporary)?;
    file.write_all(data)?;
    file.sync_all()?;
    drop(file);
    fs::rename(&temporary, directory.join(name))?;
    // Not every filesystem can flush a directory, and the data is already safe.
    if let Ok(folder) = File::open(directory) {
        let _ = folder.sync_all();
    }
    Ok(())
}

fn unique_name(base: &Path, wanted: &str) -> String {
    if !base.join(wanted).exists() {
        return wanted.to_owned();
    }
    (2..1000)
        .map(|number| format!("{wanted}-{number}"))
        .find(|name| !base.join(name).exists())
        .unwrap_or_else(|| wanted.to_owned())
}

impl Report {
    pub fn new(ram_root: &Path, name: &str) -> Report {
        let ram = ram_root.join(unique_name(ram_root, name));
        let _ = fs::create_dir_all(&ram);
        Report {
            name: name.to_owned(),
            ram,
            stick: None,
            usb_name: String::new(),
            sinks: HashMap::new(),
            kernel_bytes: 0,
            lost: false,
        }
    }

    /// Starts saving to a mounted stick, copying over everything written so far.
    pub fn attach(&mut self, mount: &Path) -> io::Result<()> {
        let base = mount.join(FOLDER);
        fs::create_dir_all(&base)?;
        let name = unique_name(&base, &self.name);
        let directory = base.join(&name);
        fs::create_dir_all(&directory)?;
        for entry in fs::read_dir(&self.ram)?.flatten() {
            if entry.path().is_file() {
                let target = directory.join(entry.file_name());
                fs::copy(entry.path(), &target)?;
                File::open(&target)?.sync_all()?;
            }
        }
        for (file, sinks) in &mut self.sinks {
            sinks.usb = open_append(&directory.join(file));
        }
        self.usb_name = name;
        self.stick = Some((base, directory));
        self.lost = false;
        Ok(())
    }

    fn lose_stick(&mut self) {
        self.stick = None;
        self.lost = true;
        for sinks in self.sinks.values_mut() {
            sinks.usb = None;
        }
    }

    pub fn append(&mut self, name: &str, text: &str) {
        if !self.sinks.contains_key(name) {
            let usb = self.stick.as_ref().and_then(|(_, directory)| open_append(&directory.join(name)));
            self.sinks.insert(name.to_owned(), Sinks { ram: open_append(&self.ram.join(name)), usb });
        }
        let Some(sinks) = self.sinks.get_mut(name) else {
            return;
        };
        if let Some(file) = &mut sinks.ram {
            let _ = file.write_all(text.as_bytes());
        }
        let failed = sinks
            .usb
            .as_mut()
            .is_some_and(|file| file.write_all(text.as_bytes()).and_then(|()| file.sync_data()).is_err());
        if failed {
            self.lose_stick();
        }
    }

    pub fn append_kernel(&mut self, text: &str) {
        if self.kernel_bytes >= KERNEL_LIMIT {
            return;
        }
        self.kernel_bytes += text.len() as u64;
        self.append(KERNEL, text);
        if self.kernel_bytes >= KERNEL_LIMIT {
            self.append(KERNEL, "[DiagnostiX stopped saving the kernel log here: size limit reached]\n");
        }
    }

    pub fn replace(&mut self, name: &str, data: &[u8]) {
        let _ = write_atomic(&self.ram, name, data);
        if let Some((_, directory)) = self.stick.clone()
            && write_atomic(&directory, name, data).is_err()
        {
            self.lose_stick();
        }
    }

    /// Saved in the stick's top folder and read by the launcher on the next visit.
    pub fn mark(&mut self, state: &str, stage: &str) {
        let body = format!("state={state}\nstage={stage}\nfolder={}\n", self.usb_name);
        if let Some((base, _)) = self.stick.clone()
            && write_atomic(&base, "last-run.txt", body.as_bytes()).is_err()
        {
            self.lose_stick();
        }
    }

    pub fn import(&mut self, source: &Path, name: &str) -> bool {
        match read_limited(source) {
            Some(data) => {
                self.replace(name, &data);
                true
            }
            None => false,
        }
    }

    /// Builds the single file people share, and returns where it is in memory.
    pub fn pack(&mut self) -> Option<PathBuf> {
        let files: Vec<(String, Vec<u8>)> = [SUMMARY, CONSOLE, BENCHMARK, PROGRESS, KERNEL, CONTROLLER]
            .iter()
            .filter_map(|name| read_limited(&self.ram.join(name)).map(|data| ((*name).to_owned(), data)))
            .collect();
        if files.is_empty() {
            return None;
        }
        self.replace(ARCHIVE, &zip_stored(&files));
        Some(self.ram.join(ARCHIVE))
    }

    pub fn usb_directory(&self) -> Option<PathBuf> {
        self.stick.as_ref().map(|(_, directory)| directory.clone())
    }

    pub fn usb_folder(&self) -> Option<String> {
        self.stick.as_ref().map(|_| format!("{FOLDER}/{}/", self.usb_name))
    }

    pub fn stick_lost(&self) -> bool {
        self.lost
    }

    /// Stops using the stick so it can be unmounted safely.
    pub fn release(&mut self) {
        self.stick = None;
        for sinks in self.sinks.values_mut() {
            sinks.usb = None;
        }
    }
}

fn read_limited(path: &Path) -> Option<Vec<u8>> {
    let mut data = Vec::new();
    File::open(path).ok()?.take(READ_LIMIT).read_to_end(&mut data).ok()?;
    Some(data)
}

fn crc32(data: &[u8]) -> u32 {
    let mut crc = !0_u32;
    for byte in data {
        crc ^= u32::from(*byte);
        for _ in 0..8 {
            crc = (crc >> 1) ^ (0xEDB8_8320 & (crc & 1).wrapping_neg());
        }
    }
    !crc
}

/// A plain zip without compression: small enough to hand-write, and the report
/// is only a few megabytes.
pub fn zip_stored(files: &[(String, Vec<u8>)]) -> Vec<u8> {
    const VERSION: u16 = 20;
    const DOS_DATE: u16 = 33; // 1980-01-01, the earliest a zip can say.
    let mut out = Vec::new();
    let mut directory = Vec::new();
    for (name, data) in files {
        let offset = out.len() as u32;
        let crc = crc32(data);
        let size = data.len() as u32;
        let mut common = Vec::new();
        common.extend_from_slice(&VERSION.to_le_bytes());
        common.extend_from_slice(&0_u16.to_le_bytes()); // flags
        common.extend_from_slice(&0_u16.to_le_bytes()); // stored
        common.extend_from_slice(&0_u16.to_le_bytes()); // time
        common.extend_from_slice(&DOS_DATE.to_le_bytes());
        common.extend_from_slice(&crc.to_le_bytes());
        common.extend_from_slice(&size.to_le_bytes());
        common.extend_from_slice(&size.to_le_bytes());
        common.extend_from_slice(&(name.len() as u16).to_le_bytes());
        common.extend_from_slice(&0_u16.to_le_bytes()); // extra

        out.extend_from_slice(b"PK\x03\x04");
        out.extend_from_slice(&common);
        out.extend_from_slice(name.as_bytes());
        out.extend_from_slice(data);

        directory.extend_from_slice(b"PK\x01\x02");
        directory.extend_from_slice(&VERSION.to_le_bytes());
        directory.extend_from_slice(&common);
        directory.extend_from_slice(&[0_u8; 10]); // comment, disk, attributes
        directory.extend_from_slice(&offset.to_le_bytes());
        directory.extend_from_slice(name.as_bytes());
    }
    let start = out.len() as u32;
    out.extend_from_slice(&directory);
    out.extend_from_slice(b"PK\x05\x06");
    out.extend_from_slice(&[0_u8; 4]); // disk numbers
    out.extend_from_slice(&(files.len() as u16).to_le_bytes());
    out.extend_from_slice(&(files.len() as u16).to_le_bytes());
    out.extend_from_slice(&(directory.len() as u32).to_le_bytes());
    out.extend_from_slice(&start.to_le_bytes());
    out.extend_from_slice(&[0_u8; 2]); // comment
    out
}

fn is_hex(byte: u8) -> bool {
    byte.is_ascii_hexdigit()
}

fn word_edge(bytes: &[u8], index: Option<usize>) -> bool {
    index.and_then(|at| bytes.get(at)).is_none_or(|byte| !byte.is_ascii_alphanumeric())
}

/// Bytes of `xx:xx:xx:xx:xx:xx` or `xx-xx-...` at `at`.
fn mac_len(bytes: &[u8], at: usize) -> Option<usize> {
    let part = bytes.get(at..at + 17)?;
    let separator = part[2];
    if separator != b':' && separator != b'-' {
        return None;
    }
    let shaped = part.iter().enumerate().all(|(index, byte)| {
        if index % 3 == 2 { *byte == separator } else { is_hex(*byte) }
    });
    (shaped && word_edge(bytes, at.checked_sub(1)) && word_edge(bytes, Some(at + 17))).then_some(17)
}

fn uuid_len(bytes: &[u8], at: usize) -> Option<usize> {
    let part = bytes.get(at..at + 36)?;
    let shaped = part.iter().enumerate().all(|(index, byte)| {
        if matches!(index, 8 | 13 | 18 | 23) { *byte == b'-' } else { is_hex(*byte) }
    });
    (shaped && word_edge(bytes, at.checked_sub(1)) && word_edge(bytes, Some(at + 36))).then_some(36)
}

fn ipv4_len(bytes: &[u8], at: usize) -> Option<usize> {
    if !word_edge(bytes, at.checked_sub(1)) || at.checked_sub(1).and_then(|i| bytes.get(i)) == Some(&b'.') {
        return None;
    }
    let mut index = at;
    for octet in 0..4 {
        let start = index;
        while index - start < 3 && bytes.get(index).is_some_and(u8::is_ascii_digit) {
            index += 1;
        }
        if index == start {
            return None;
        }
        if octet < 3 {
            if bytes.get(index) != Some(&b'.') {
                return None;
            }
            index += 1;
        }
    }
    (word_edge(bytes, Some(index)) && bytes.get(index) != Some(&b'.')).then_some(index - at)
}

/// Sixteen or more hex digits in a row: console IDs, serials, and kernel addresses.
fn long_hex_len(bytes: &[u8], at: usize) -> Option<usize> {
    if !word_edge(bytes, at.checked_sub(1)) {
        return None;
    }
    let count = bytes[at..].iter().take_while(|byte| is_hex(**byte)).count();
    (count >= 16 && word_edge(bytes, Some(at + count))).then_some(count)
}

fn names_private_value(line: &str) -> bool {
    let lower = line.to_ascii_lowercase();
    ["serial", "ssid", "password", "passphrase", "console id", "console_id", "idps", "psid"]
        .iter()
        .any(|word| lower.contains(word))
}

/// Removes anything that identifies a person or console from one log line.
pub fn clean_line(line: &str) -> String {
    let printable: String = line.chars().map(|c| if (' '..='~').contains(&c) { c } else { '?' }).collect();
    if names_private_value(&printable) {
        let kept = printable.split([':', '=']).next().unwrap_or_default();
        return format!("{kept}: [removed]");
    }
    let bytes = printable.as_bytes();
    let mut out = String::with_capacity(printable.len());
    let mut at = 0;
    while at < bytes.len() {
        let hit = mac_len(bytes, at)
            .map(|len| (len, "[mac]"))
            .or_else(|| uuid_len(bytes, at).map(|len| (len, "[id]")))
            .or_else(|| ipv4_len(bytes, at).map(|len| (len, "[ip]")))
            .or_else(|| long_hex_len(bytes, at).map(|len| (len, "[id]")));
        match hit {
            Some((length, replacement)) => {
                out.push_str(replacement);
                at += length;
            }
            None => {
                out.push(bytes[at] as char);
                at += 1;
            }
        }
    }
    out
}

/// `priority,sequence,microseconds,flags;message` to `[seconds] message`.
pub fn parse_kmsg(record: &str) -> Option<(u32, String)> {
    let (prefix, message) = record.split_once(';')?;
    let mut fields = prefix.split(',');
    let level = fields.next()?.parse::<u32>().ok()? & 7;
    let time = fields.nth(1)?.parse::<u64>().ok()?;
    let message = message.lines().next().unwrap_or_default();
    let line = format!("[{:>5}.{:03}] {}", time / 1_000_000, time / 1000 % 1000, clean_line(message));
    Some((level, line))
}

pub struct KernelLog {
    /// Set once everything logged before this program started has been read.
    pub caught_up: Arc<AtomicBool>,
    pub errors: Arc<AtomicUsize>,
}

/// Follows the kernel log and saves it once a second.
pub fn follow_kernel_log(
    report: Arc<Mutex<Report>>,
    mut on_lines: impl FnMut(&[String]) + Send + 'static,
) -> KernelLog {
    let caught_up = Arc::new(AtomicBool::new(false));
    let errors = Arc::new(AtomicUsize::new(0));
    let (done, count) = (caught_up.clone(), errors.clone());
    thread::spawn(move || {
        let source = OpenOptions::new().read(true).custom_flags(O_NONBLOCK).open("/dev/kmsg");
        let Ok(mut source) = source else {
            done.store(true, Ordering::Relaxed);
            return;
        };
        let mut buffer = vec![0_u8; 8192];
        loop {
            let mut lines = Vec::new();
            loop {
                match source.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(size) => {
                        if let Some((level, line)) = parse_kmsg(&String::from_utf8_lossy(&buffer[..size])) {
                            if level <= 3 {
                                count.fetch_add(1, Ordering::Relaxed);
                            }
                            lines.push(line);
                        }
                    }
                    Err(error) if error.kind() == ErrorKind::WouldBlock => break,
                    // The oldest lines were overwritten; the next read continues after them.
                    Err(error) if error.kind() == ErrorKind::BrokenPipe => continue,
                    Err(_) => break,
                }
            }
            if !lines.is_empty() {
                report.lock().unwrap().append_kernel(&(lines.join("\n") + "\n"));
                on_lines(&lines);
            }
            done.store(true, Ordering::Relaxed);
            thread::sleep(Duration::from_secs(1));
        }
    });
    KernelLog { caught_up, errors }
}

/// `2026-11-09` from Unix time, or `None` if the console's clock was never set.
pub fn date_from_unix(seconds: u64) -> Option<String> {
    let days = (seconds / 86_400) as i64;
    let shifted = days + 719_468;
    let era = shifted / 146_097;
    let day_of_era = shifted - era * 146_097;
    let year_of_era = (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let shifted_month = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * shifted_month + 2) / 5 + 1;
    let month = if shifted_month < 10 { shifted_month + 3 } else { shifted_month - 9 };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    (year >= 2024).then(|| format!("{year:04}-{month:02}-{day:02}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!("dx-report-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn private_values_are_removed() {
        assert_eq!(clean_line("eth0: link aa:bb:cc:dd:ee:ff up"), "eth0: link [mac] up");
        assert_eq!(clean_line("mac AA-BB-CC-DD-EE-01"), "mac [mac]");
        assert_eq!(clean_line("got 192.168.1.50 from dhcp"), "got [ip] from dhcp");
        assert_eq!(clean_line("id 1b4e28ba-2fa1-11d2-883f-b9a761bde3fb end"), "id [id] end");
        assert_eq!(clean_line("console 0123456789abcdef0123456789abcdef"), "console [id]");
        assert_eq!(clean_line("usb 1-1: SerialNumber: 04E5ABC"), "usb 1-1: [removed]");
        assert_eq!(clean_line("connected to SSID=home-network"), "connected to SSID: [removed]");
        assert_eq!(clean_line("IDPS 00 01 02"), "IDPS 00 01 02: [removed]");
    }

    #[test]
    fn useful_values_survive() {
        for line in [
            "amdgpu 0000:00:01.0: ring gfx test passed",
            "Linux version 7.2.11-psx-ps4-baikal",
            "usb 1-1: new high-speed USB device number 2 using xhci_hcd",
            "Memory: 7168M",
            "aeolia pcie at 0xc8800000",
            "hwmon temp 54000",
        ] {
            assert_eq!(clean_line(line), line);
        }
    }

    #[test]
    fn kernel_records_are_parsed() {
        let (level, line) = parse_kmsg("3,812,61104000,-;amdgpu: ring failed\n SUBSYSTEM=pci\n").unwrap();
        assert_eq!(level, 3);
        assert_eq!(line, "[   61.104] amdgpu: ring failed");
        assert!(parse_kmsg("garbage").is_none());
    }

    #[test]
    fn crc_matches_the_standard_check_value() {
        assert_eq!(crc32(b"123456789"), 0xCBF4_3926);
    }

    #[test]
    fn zip_has_every_part_at_the_right_offset() {
        let archive = zip_stored(&[("a.txt".into(), b"hello".to_vec()), ("b.txt".into(), b"bye".to_vec())]);
        assert_eq!(&archive[..4], b"PK\x03\x04");
        let end = archive.len() - 22;
        assert_eq!(&archive[end..end + 4], b"PK\x05\x06");
        assert_eq!(u16::from_le_bytes([archive[end + 10], archive[end + 11]]), 2);
        let start = u32::from_le_bytes(archive[end + 16..end + 20].try_into().unwrap()) as usize;
        assert_eq!(&archive[start..start + 4], b"PK\x01\x02");
        assert_eq!(archive.windows(5).filter(|part| *part == b"hello").count(), 1);
    }

    #[test]
    fn written_lines_reach_memory_and_the_stick() {
        let root = scratch("append");
        let (ram, stick) = (root.join("ram"), root.join("stick"));
        fs::create_dir_all(&stick).unwrap();
        fs::create_dir_all(&ram).unwrap();
        let mut report = Report::new(&ram, "2026-11-09-baikal-b1");
        report.append(PROGRESS, "before\n");
        report.attach(&stick).unwrap();
        report.append(PROGRESS, "after\n");
        report.mark("running", "Memory");
        let folder = stick.join("DiagnostiX/2026-11-09-baikal-b1");
        assert_eq!(fs::read_to_string(folder.join(PROGRESS)).unwrap(), "before\nafter\n");
        let mark = fs::read_to_string(stick.join("DiagnostiX/last-run.txt")).unwrap();
        assert!(mark.contains("state=running") && mark.contains("stage=Memory"));
        assert_eq!(report.usb_folder().unwrap(), "DiagnostiX/2026-11-09-baikal-b1/");
    }

    #[test]
    fn a_second_run_gets_its_own_folder() {
        let root = scratch("unique");
        let stick = root.join("stick");
        fs::create_dir_all(stick.join("DiagnostiX/run")).unwrap();
        let mut report = Report::new(&root, "run");
        report.attach(&stick).unwrap();
        assert_eq!(report.usb_folder().unwrap(), "DiagnostiX/run-2/");
    }

    #[test]
    fn pulling_the_stick_keeps_the_memory_copy() {
        let root = scratch("lost");
        let stick = root.join("stick");
        fs::create_dir_all(&stick).unwrap();
        let mut report = Report::new(&root, "run");
        report.attach(&stick).unwrap();
        report.append(PROGRESS, "one\n");
        fs::remove_dir_all(&stick).unwrap();
        report.replace(SUMMARY, b"after removal");
        assert!(report.stick_lost());
        assert_eq!(fs::read(root.join("run").join(SUMMARY)).unwrap(), b"after removal");
    }

    #[test]
    fn dates_come_from_a_set_clock_only() {
        assert_eq!(date_from_unix(1_794_182_400).unwrap(), "2026-11-09");
        assert_eq!(date_from_unix(0), None);
    }
}
