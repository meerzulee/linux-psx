// SPDX-License-Identifier: MIT
//! Finds the USB stick that holds the report. Only devices on the USB bus are
//! touched, so the console's internal drive is never mounted.
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Instant;

const MOUNT_POINT: &str = "/run/psx-usb";

pub enum Stick {
    None,
    /// A stick is plugged in but could not be mounted for writing.
    Unusable,
    Mounted(PathBuf),
}

pub fn on_usb_bus(device: &Path) -> bool {
    fs::canonicalize(device).is_ok_and(|path| path.to_string_lossy().contains("/usb"))
}

/// Device names to try: each partition, or the whole device if it has none.
pub fn candidates(sys_block: &Path) -> Vec<String> {
    let mut found = Vec::new();
    let mut devices: Vec<String> = fs::read_dir(sys_block)
        .into_iter()
        .flatten()
        .flatten()
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .filter(|name| name.starts_with("sd"))
        .collect();
    devices.sort();
    for device in devices {
        let path = sys_block.join(&device);
        if !on_usb_bus(&path) {
            continue;
        }
        let mut partitions: Vec<String> = fs::read_dir(&path)
            .into_iter()
            .flatten()
            .flatten()
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .filter(|name| name.starts_with(&device))
            .collect();
        partitions.sort();
        if partitions.is_empty() {
            found.push(device);
        } else {
            found.extend(partitions);
        }
    }
    found
}

pub fn mount_stick() -> Stick {
    let names = candidates(Path::new("/sys/block"));
    if names.is_empty() {
        return Stick::None;
    }
    let _ = fs::create_dir_all(MOUNT_POINT);
    for name in names {
        let mounted = Command::new("/usr/bin/mount")
            .args(["-t", "vfat,exfat", "-o", "rw,nosuid,nodev,noexec,noatime"])
            .arg(format!("/dev/{name}"))
            .arg(MOUNT_POINT)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .is_ok_and(|status| status.success());
        if mounted {
            return Stick::Mounted(PathBuf::from(MOUNT_POINT));
        }
    }
    Stick::Unusable
}

pub fn unmount() {
    let _ = Command::new("/usr/bin/umount")
        .arg(MOUNT_POINT)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

fn pattern(buffer: &mut [u8], seed: u64) {
    let mut state = seed | 1;
    for chunk in buffer.chunks_mut(8) {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        let bytes = state.to_le_bytes();
        chunk.copy_from_slice(&bytes[..chunk.len()]);
    }
}

/// Writes a known file inside `directory`, reads it back, removes it, and
/// returns the read speed in MB/s.
pub fn write_test(directory: &Path) -> Result<u64, String> {
    const CHUNK: usize = 1024 * 1024;
    const CHUNKS: u64 = 8;
    let path = directory.join("speed-check.tmp");
    let cleanup = |message: &str| {
        let _ = fs::remove_file(&path);
        message.to_owned()
    };
    let mut expected = vec![0_u8; CHUNK];
    {
        let mut file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&path)
            .map_err(|_| "Could not create a file on the stick.".to_owned())?;
        for index in 0..CHUNKS {
            pattern(&mut expected, index + 1);
            file.write_all(&expected).map_err(|_| cleanup("Writing to the stick failed."))?;
        }
        file.sync_all().map_err(|_| cleanup("The stick did not confirm the write."))?;
    }
    // Without this the read would come straight back from memory.
    let _ = fs::write("/proc/sys/vm/drop_caches", "1\n");
    let started = Instant::now();
    let mut file = File::open(&path).map_err(|_| cleanup("Could not reopen the file on the stick."))?;
    let mut actual = vec![0_u8; CHUNK];
    for index in 0..CHUNKS {
        file.read_exact(&mut actual).map_err(|_| cleanup("Reading from the stick failed."))?;
        pattern(&mut expected, index + 1);
        if actual != expected {
            return Err(cleanup("The data read back did not match what was written."));
        }
    }
    let seconds = started.elapsed().as_secs_f64().max(0.001);
    let _ = fs::remove_file(&path);
    Ok((CHUNKS as f64 / seconds).round() as u64)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    fn scratch(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!("dx-usb-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn internal_drive_is_never_a_candidate() {
        let root = scratch("candidates");
        let devices = root.join("devices");
        let usb = devices.join("pci0/usb1/1-1/block/sdb");
        let sata = devices.join("pci0/ata1/block/sda");
        fs::create_dir_all(usb.join("sdb1")).unwrap();
        fs::create_dir_all(sata.join("sda1")).unwrap();
        let block = root.join("block");
        fs::create_dir_all(&block).unwrap();
        symlink(&usb, block.join("sdb")).unwrap();
        symlink(&sata, block.join("sda")).unwrap();
        assert_eq!(candidates(&block), vec!["sdb1".to_owned()]);
    }

    #[test]
    fn stick_without_partitions_uses_the_whole_device() {
        let root = scratch("whole");
        let usb = root.join("devices/usb1/1-1/block/sdc");
        fs::create_dir_all(&usb).unwrap();
        let block = root.join("block");
        fs::create_dir_all(&block).unwrap();
        symlink(&usb, block.join("sdc")).unwrap();
        assert_eq!(candidates(&block), vec!["sdc".to_owned()]);
    }

    #[test]
    fn write_test_leaves_nothing_behind() {
        let root = scratch("write");
        assert!(write_test(&root).unwrap() > 0);
        assert_eq!(fs::read_dir(&root).unwrap().count(), 0);
    }
}
