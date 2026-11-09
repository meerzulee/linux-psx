// SPDX-License-Identifier: MIT
//! Sample runs for previewing the screen. None of these values are measured.
use crate::model::{Download, Finish, Problem, Question, Screen, Snapshot, Status, blank};

pub const SCENES: [&str; 10] = [
    "starting",
    "basics",
    "graphics",
    "ask-sound",
    "ask-ethernet",
    "before-last",
    "finished",
    "finished-no-usb",
    "download",
    "manual",
];

const SAMPLE_LINK: &str = "http://192.168.1.50:8080/k7m2x9";

fn base() -> Snapshot {
    let mut snapshot = blank(false);
    snapshot.demo = true;
    snapshot.console = "Baikal B1  ·  Firmware 12.02  ·  Linux 7.2.11".into();
    snapshot.usb = "USB stick: saving report".into();
    snapshot
}

fn set(snapshot: &mut Snapshot, name: &str, status: Status, detail: &str) {
    for part in &mut snapshot.parts {
        for row in &mut part.rows {
            if row.name == name {
                row.status = status;
                row.detail = detail.into();
            }
        }
    }
}

fn apply(snapshot: &mut Snapshot, rows: &[(&str, Status, &str)]) {
    for (name, status, detail) in rows {
        set(snapshot, name, *status, detail);
    }
}

const STORAGE_PROBLEM: &str = "Two reads of the same block did not match.";

fn network_found(snapshot: &mut Snapshot) {
    apply(
        snapshot,
        &[
            ("Ethernet", Status::Found, "No cable yet"),
            ("Wi-Fi", Status::Found, ""),
            ("Bluetooth", Status::Found, ""),
        ],
    );
}

fn basics_done(snapshot: &mut Snapshot) {
    use Status::*;
    apply(
        snapshot,
        &[
            ("Boot", Pass, "61 s"),
            ("Processor", Pass, "8 cores"),
            ("Memory", Pass, "6.9 GiB"),
            ("Internal storage", Fail, "1.0 TB"),
            ("USB", Pass, "38 MB/s"),
            ("Temperature", Pass, "54 °C"),
            ("Audio device", Found, "HDMI"),
        ],
    );
    network_found(snapshot);
    snapshot.problems.push(Problem {
        name: "Internal storage".into(),
        why: STORAGE_PROBLEM.into(),
    });
}

fn graphics_done(snapshot: &mut Snapshot) {
    use Status::*;
    apply(
        snapshot,
        &[
            ("Graphics driver", Found, "amdgpu"),
            ("OpenGL", Pass, "4.6"),
            ("Vulkan", Pass, "RADV"),
            ("Browser", Pass, "Chromium"),
        ],
    );
}

fn all_but_last(snapshot: &mut Snapshot) {
    use Status::*;
    basics_done(snapshot);
    graphics_done(snapshot);
    apply(
        snapshot,
        &[
            ("Audio device", Pass, "HDMI"),
            ("Sound", Pass, ""),
            ("Controller", Found, ""),
            ("Ethernet", Pass, "940 Mbit/s"),
            ("Wi-Fi", NotFound, ""),
            ("Bluetooth", Pass, "3 devices"),
        ],
    );
}

fn finished_results(snapshot: &mut Snapshot) {
    use Status::*;
    all_but_last(snapshot);
    apply(snapshot, &[("Browser", Fail, "Chromium"), ("Video decoding", Pass, "H.264 1080p"), ("3D benchmark", Pass, "GL 1842")]);
    snapshot.part = 3;
    snapshot.done = 18;
    snapshot.elapsed = 768;
    snapshot.problems.push(Problem {
        name: "Browser".into(),
        why: "The WebGL page stopped responding after 60 s.".into(),
    });
}

fn no_stick(snapshot: &mut Snapshot) {
    set(snapshot, "USB", Status::NotFound, "");
    snapshot.usb = "No USB stick".into();
}

pub fn scene(name: &str) -> Option<Snapshot> {
    use Status::*;
    let mut snapshot = base();
    match name {
        "starting" => {
            snapshot.screen = Screen::Starting;
            snapshot.note = Some("Waiting for the diagnostic service to start.".into());
        }
        "basics" => {
            apply(
                &mut snapshot,
                &[("Boot", Pass, "61 s"), ("Processor", Pass, "8 cores"), ("Memory", Running, "")],
            );
            snapshot.done = 2;
            snapshot.elapsed = 74;
            snapshot.log = vec![
                "[   61.104] systemd[1]: Reached target Multi-User".into(),
                "[   62.310] diagnostix: start cpu.compute".into(),
                "[   64.318] diagnostix: pass cpu.compute".into(),
                "[   64.402] diagnostix: start memory.pattern".into(),
            ];
        }
        "graphics" => {
            basics_done(&mut snapshot);
            apply(
                &mut snapshot,
                &[("Graphics driver", Found, "amdgpu"), ("OpenGL", Pass, "4.6"), ("Vulkan", Running, "")],
            );
            snapshot.part = 1;
            snapshot.done = 9;
            snapshot.elapsed = 262;
            snapshot.note = Some("The screen may change during this part. It comes back by itself.".into());
            snapshot.log = vec![
                "[  254.118] diagnostix: pass gpu.opengl.render".into(),
                "[  254.902] diagnostix: start gpu.vulkan.render".into(),
                "[  255.417] [drm] ring test on gfx passed".into(),
                "[  256.033] amdgpu: IB test passed on gfx".into(),
            ];
        }
        "ask-sound" => {
            basics_done(&mut snapshot);
            graphics_done(&mut snapshot);
            set(&mut snapshot, "Sound", Running, "");
            snapshot.part = 2;
            snapshot.done = 12;
            snapshot.elapsed = 512;
            snapshot.screen = Screen::Asking(Question {
                step: 1,
                total: 5,
                title: "Sound".into(),
                body: vec!["A tone is playing through your TV now.".into(), "Can you hear it?".into()],
                link: None,
                choices: vec![("Cross", "Yes"), ("Circle", "No"), ("Triangle", "Skip")],
                keyboard: "On a keyboard: Y, N or S",
                skips_in: 42,
            });
        }
        "ask-ethernet" => {
            basics_done(&mut snapshot);
            graphics_done(&mut snapshot);
            apply(
                &mut snapshot,
                &[("Sound", Pass, ""), ("Controller", Found, ""), ("Ethernet", Running, "No cable yet")],
            );
            snapshot.part = 2;
            snapshot.done = 14;
            snapshot.elapsed = 548;
            snapshot.screen = Screen::Asking(Question {
                step: 3,
                total: 5,
                title: "Ethernet".into(),
                body: vec![
                    "The network port was found, but no cable is plugged in.".into(),
                    "Plug in a cable to test it. The test starts by itself.".into(),
                ],
                link: None,
                choices: vec![("Triangle", "Skip")],
                keyboard: "On a keyboard: S",
                skips_in: 58,
            });
        }
        "before-last" => {
            all_but_last(&mut snapshot);
            no_stick(&mut snapshot);
            snapshot.part = 3;
            snapshot.done = 17;
            snapshot.elapsed = 690;
            snapshot.screen = Screen::Asking(Question {
                step: 6,
                total: 6,
                title: "Before the last test".into(),
                body: vec![
                    "The last test can end this session, and there is no USB stick to keep your report.".into(),
                    "Scan this to download it now, or open the address on a computer on the same network.".into(),
                ],
                link: Some(SAMPLE_LINK.into()),
                choices: vec![("Cross", "Run last test"), ("Circle", "Skip it")],
                keyboard: "On a keyboard: Y or N",
                skips_in: 120,
            });
        }
        "finished" => {
            finished_results(&mut snapshot);
            snapshot.usb = "USB stick: report saved".into();
            snapshot.screen = Screen::Finished(Finish {
                report: Some("DiagnostiX/2026-11-09-baikal-b1/".into()),
                menu: vec![
                    "Get report over the network",
                    "See what each test found",
                    "Run tests again",
                    "Restart to PS4",
                    "Turn off",
                ],
                cursor: 0,
            });
        }
        "finished-no-usb" => {
            finished_results(&mut snapshot);
            no_stick(&mut snapshot);
            snapshot.screen = Screen::Finished(Finish {
                report: None,
                menu: vec![
                    "Copy report to a USB stick",
                    "Get report over the network",
                    "See what each test found",
                    "Run tests again",
                    "Restart to PS4",
                    "Turn off",
                ],
                cursor: 0,
            });
        }
        "download" => {
            finished_results(&mut snapshot);
            no_stick(&mut snapshot);
            snapshot.screen = Screen::Download(Download {
                link: Some(SAMPLE_LINK.into()),
                ssh: Some("ssh root@192.168.1.50   (needs your key)".into()),
                fetched: false,
            });
        }
        "manual" => {
            set(&mut snapshot, "Boot", Pass, "61 s");
            network_found(&mut snapshot);
            snapshot.usb = "USB stick: found".into();
            snapshot.screen = Screen::Choosing { cursor: 8 };
        }
        _ => return None,
    }
    Some(snapshot)
}
