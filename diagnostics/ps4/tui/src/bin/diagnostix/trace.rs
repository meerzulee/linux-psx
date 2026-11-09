// SPDX-License-Identifier: MIT
//! One line for UART and the report, so a hang shows the last step reached.
use std::fs::OpenOptions;
use std::io::Write;

/// Opens the device each time, because one open file may only write ten lines
/// in five seconds. Info level, so these never count as kernel errors.
pub fn line(text: &str) {
    let clean: String = text.chars().take(400).map(|c| if (' '..='~').contains(&c) { c } else { '?' }).collect();
    if let Ok(mut log) = OpenOptions::new().write(true).open("/dev/kmsg") {
        let _ = log.write_all(format!("<6>dx/tui: {clean}\n").as_bytes());
    }
}
