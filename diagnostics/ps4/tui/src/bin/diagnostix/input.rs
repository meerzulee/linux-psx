// SPDX-License-Identifier: MIT
//! DualShock buttons, read straight from evdev. The console text screen only
//! sees keyboards, and most people running this have just a controller.
use std::collections::HashSet;
use std::fs::{self, File};
use std::io::Read;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::Sender;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Button {
    Cross,
    Circle,
    Triangle,
    Square,
    Options,
    Up,
    Down,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Press {
    pub button: Button,
}

const EV_KEY: u16 = 1;
const EV_ABS: u16 = 3;
const ABS_HAT0Y: u16 = 0x11;
const BTN_SOUTH: u16 = 0x130;
const BTN_EAST: u16 = 0x131;
const BTN_NORTH: u16 = 0x133;
const BTN_WEST: u16 = 0x134;
const BTN_START: u16 = 0x13B;

pub fn decode(kind: u16, code: u16, value: i32) -> Option<Button> {
    match (kind, code, value) {
        (EV_KEY, BTN_SOUTH, 1) => Some(Button::Cross),
        (EV_KEY, BTN_EAST, 1) => Some(Button::Circle),
        (EV_KEY, BTN_NORTH, 1) => Some(Button::Triangle),
        (EV_KEY, BTN_WEST, 1) => Some(Button::Square),
        (EV_KEY, BTN_START, 1) => Some(Button::Options),
        (EV_ABS, ABS_HAT0Y, -1) => Some(Button::Up),
        (EV_ABS, ABS_HAT0Y, 1) => Some(Button::Down),
        _ => None,
    }
}

/// `capabilities/key` lists 64-bit words with the most significant first. The
/// motion and touchpad halves of a DualShock do not report BTN_SOUTH.
pub fn has_face_buttons(capabilities: &str) -> bool {
    let word = BTN_SOUTH as usize / 64;
    let bit = BTN_SOUTH as usize % 64;
    capabilities
        .split_whitespace()
        .rev()
        .nth(word)
        .and_then(|value| u64::from_str_radix(value, 16).ok())
        .is_some_and(|value| value >> bit & 1 == 1)
}

fn read_events(mut device: File, sender: &Sender<Press>) {
    // struct input_event on x86-64: 16 bytes of time, u16 type, u16 code, s32 value.
    let mut event = [0_u8; 24];
    while device.read_exact(&mut event).is_ok() {
        let kind = u16::from_ne_bytes([event[16], event[17]]);
        let code = u16::from_ne_bytes([event[18], event[19]]);
        let value = i32::from_ne_bytes([event[20], event[21], event[22], event[23]]);
        if let Some(button) = decode(kind, code, value)
            && sender.send(Press { button }).is_err()
        {
            return;
        }
    }
}

/// Follows controllers as they are plugged in and out. `pads` counts those open now.
pub fn watch(sender: Sender<Press>, pads: Arc<AtomicUsize>) {
    let open = Arc::new(Mutex::new(HashSet::<String>::new()));
    thread::spawn(move || {
        loop {
            for entry in fs::read_dir("/sys/class/input").into_iter().flatten().flatten() {
                let name = entry.file_name().to_string_lossy().into_owned();
                if !name.starts_with("event") {
                    continue;
                }
                let keys = entry.path().join("device/capabilities/key");
                let is_pad = fs::read_to_string(keys).is_ok_and(|text| has_face_buttons(&text));
                if !is_pad || open.lock().unwrap().contains(&name) {
                    continue;
                }
                // Only a device that can really be read counts as connected.
                let Ok(device) = File::open(format!("/dev/input/{name}")) else {
                    continue;
                };
                open.lock().unwrap().insert(name.clone());
                pads.fetch_add(1, Ordering::Relaxed);
                let (sender, open, pads) = (sender.clone(), open.clone(), pads.clone());
                thread::spawn(move || {
                    read_events(device, &sender);
                    open.lock().unwrap().remove(&name);
                    pads.fetch_sub(1, Ordering::Relaxed);
                });
            }
            thread::sleep(Duration::from_secs(2));
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dualshock_events_become_buttons() {
        assert_eq!(decode(EV_KEY, BTN_SOUTH, 1), Some(Button::Cross));
        assert_eq!(decode(EV_KEY, BTN_EAST, 1), Some(Button::Circle));
        assert_eq!(decode(EV_KEY, BTN_NORTH, 1), Some(Button::Triangle));
        assert_eq!(decode(EV_KEY, BTN_START, 1), Some(Button::Options));
        assert_eq!(decode(EV_ABS, ABS_HAT0Y, -1), Some(Button::Up));
        assert_eq!(decode(EV_ABS, ABS_HAT0Y, 1), Some(Button::Down));
        assert_eq!(decode(EV_KEY, BTN_SOUTH, 0), None, "a release is not a press");
        assert_eq!(decode(EV_KEY, BTN_SOUTH, 2), None, "key repeat is not a press");
    }

    #[test]
    fn only_the_gamepad_node_counts_as_a_controller() {
        // Real shape of the file: five words, BTN_SOUTH in the fifth from the right.
        assert!(has_face_buttons("3fff000000000000 0 0 0 0"));
        assert!(!has_face_buttons("0 0 0 0 0"), "motion sensors report no keys");
        assert!(!has_face_buttons("0"));
        assert!(!has_face_buttons(""));
    }
}
