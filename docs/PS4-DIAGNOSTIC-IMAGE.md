# psX Diagnostics: a minimal PS4 hardware test system

Source and image builds now belong to Linux psX. Start with the
[standalone build guide](DIAGNOSTICS.md); the observations below retain their
original artifact and historical project scope.

Status: local development appliance, 2026-11-04. The native assembler,
Ratatui interface and selected tests have host validation. The base console1
image reached Arch userspace in EXP-20261104-001. EXP-20261104-003 observed the
olive menu, log separation and one working wired SSH session. EXP-20261104-004
observed a successful Wi-Fi connection after correcting station selection.
EXP-20261104-008 observed accelerated Xorg/GLX, moving OpenGL gears and the
local Firefox Canvas/WebGL/animation/video checks. Video completed with dropped
frames; hardware video decoding and long-term graphics stability remain unqualified.
PocketJS remains a possible later frontend.
This work complements the historical
[portable Portal idea](https://github.com/meerzulee/omarchy-psx/blob/bbff47f25249d32e867ced1a400229cdf5a28737/platforms/ps4/docs/PORTABLE-PORTAL-IDEA.md).

## Current console1 profile

### Recorded failure and corrective rules — 2026-11-04

The first frontend updater rebuilt installed boot arguments from the image
template. That silently restored `loglevel=3` and removed `ignore_loglevel`
after the working verbose boot. The next no-video observation consequently
lacked UART progress markers. Quieting the video menu must not reduce serial
diagnostic visibility. This was a distribution updater regression; it does
not establish the cause of the video failure.

The frontend updater now preserves installed boot arguments byte-for-byte by
default. Its explicit `--verbose-uart` option changes only verbosity tokens.
Regression checks protect UART settings and unrelated PCI, Ethernet and SATA
arguments. Kernel messages are redirected away from the menu, systemd status
is suppressed for the menu, and the journal remains available on tty12.

The warning `No irq handler for 0.49` occurred in both the working and the
quiet boot, so it must not be presented as the diagnosed cause. The actual
video failure remains unresolved until a sufficiently detailed trace or a
screen observation identifies it. Host tests and uploaded file hashes must
be reported separately from observed console startup and usable UI.

During an operator test, give a concise update within 60 seconds, state the
last observed stage and missing evidence, and close a bounded capture when
its question is answered or its timeout expires. Keep the continuous logger
running. Do not leave the operator waiting while pursuing source hypotheses
without communicating the current finding and next concrete action.

Live update preparation also hit `EBUSY` while replacing a RAM file already
used by frontend bind mounts. Its rollback attempted the same replacement and
left the menu stopped until the primary explicitly restored both services;
SSH remained responsive and the operator confirmed recovered input. Stage new
file sources and add reversible bind layers instead of renaming over those
live sources. Service restoration must run even when file cleanup fails.

Independent Astra boot review also found a conditional rescue-shell defect:
an error inside the frontend manifest loop inherited the manifest as stdin.
The source now explicitly reopens `/dev/console` for an interactive shell;
two PTY regressions passed. This source fix is intentionally not in the
EXP-20261104-003 initramfs, preserving that experiment's verbosity-only change.
It is not an established cause of the no-video observation.

The initial Wi-Fi selector sorted every NetworkManager `wifi` device by name.
The PS4 vendor driver exposes `ap0` and `ap1` alongside its station `wlan0`,
so connect checked `ap0` even when the displayed network came from `wlan0`.
Three saved requests failed with `SSID_NOT_FOUND` before authentication.
The selector now excludes AP/P2P names and prefers `wlan0`; regression tests
cover the real three-interface layout, profile binding and activation target.
The live RAM fix passed one operator connection under EXP-20261104-004, with
5 GHz association and DHCP verified over SSH. The correction was subsequently
saved in the verified USB frontend. This is a userspace correction, not a Wi-Fi hardware
acceptance claim.

`manifests/ps4-diagnostics-console.json` builds a complete OS payload capped at
1,073,741,824 bytes (1024 MiB), including kernel, initramfs, root image and
receipts. The base console1 build measures 1,063,288,159 bytes (1014.03 MiB);
the tested JetBrains/glamor frontend remains about 1016.51 MiB with the same root image.
The ext4 file is exactly 1000 MiB. This is a storage budget, not a RAM allocation. The USB keeps its
existing FAT32 partition. A read-only ext4 loop image avoids unpacking the
whole OS into RAM; writable system directories use a bounded 512 MiB tmpfs.
Reports and unique SSH host keys persist under `psx-diagnostics/results/` and
`psx-diagnostics/state/`. The full corresponding kernel source remains a
verified local build input; its source pins are carried on the USB.

The current frontend uses a centered olive-green Ratatui interface on tty1. Its first screen
asks for Ethernet or Wi-Fi and shows IP addresses and SSH readiness. Native
Ethernet uses `autoarm=1` and NetworkManager DHCP, with the driver's retained
one-generation limit. Wi-Fi supports visible open and personal WPA networks;
passwords stay in protected RAM. SSH accepts only the explicitly supplied
owner public key, with a unique installation host key. Ctrl-Alt-F2 opens a
local passwordless root rescue shell. Staging seeds `time=` from the host
clock and requests `systemd-timesyncd` so the frozen package/build date does
not invalidate HTTPS certificate checks.

Before the menu draws, console preparation disables systemd status output,
keeps raw kernel text on tty11 and starts a complete live journal view on tty12.
Ctrl-Alt-F12 opens that log view, Ctrl-Alt-F1 returns to the menu, and
Ctrl-Alt-F2 opens rescue. UART remains independent. The menu clears old boot
text on startup and supports Ctrl-L to clear/repaint. The olive Linux palette
applies to the diagnostic virtual consoles; SSH uses its terminal's supported
color mode.

`scripts/refresh-ps4-diagnostics-frontend` replaces the frontend without
repacking the 1000 MiB root. The current source registers 29 fixed, pinned
assets, with an 8 MiB total payload limit inside a bounded 16 MiB tmpfs.
The 28-asset source-handoff artifact remains frozen while the new optional SSH
setup module receives its separate source validation.
Twelve existing regular targets receive read-only binds; the remaining
helpers, libraries, font and Settings data stay in RAM. External and embedded
boot contracts include every frontend hash. Both FTP and pinned-key SSH
updaters back up and verify the small changed boot files while retaining
the previously verified kernel/root, exact installed boot arguments and
private USB state. A live overlay records its boot-bound hashes separately
from the manifest originally loaded at boot.

The optional graphical diagnostic terminal uses JetBrainsMono Nerd Font and
a collapsible hardware report tree. Prepared Settings offer Olive plus 22
Omarchy palettes and font sizes 10–24, persisted on the USB. This terminal
shares the existing Xorg display with unprivileged browser/demo windows;
opening a test does not create a second X server or window manager.

Xorg, xfwm4 and Firefox start only when a graphical test is selected; there
is no full desktop session at boot. All graphical processes use the pinned
private Mesa candidate. The offline browser lab includes Canvas pixel checks,
WebGL render/readback, finite animation and a generated muted VP8 video clip.
Separate EGL/OpenGL and Vulkan probes check actual rendered pixels and identify
software rendering. A browser opened remotely reports that client's results.

The observed Firefox baseline averaged 60.198 animation FPS over five seconds;
the configured X11 mode independently reported 1920×1080 at nominal 60 Hz.
These are distinct from physical refresh measurements. The initial video
report's PASS records completion only: 15 of 92 frames were dropped.
The browser compositor is configured for software while WebGL has its own
renderer and pixel checks. Capabilities records API availability as
INCONCLUSIVE, not functional acceptance. Automated sequential checks and an
optional Chromium comparison pack are being prepared; the latter is separate
USB data because its package alone exceeds the remaining core OS budget.

The profile retains all 41 matching native-dev3 modules, wireless firmware,
inventory, CPU/memory checks, reports and the 44.1 kHz audio fixture. Internal
SATA stays disabled, native 48 kHz is deferred, and experimental UVD and power
transitions are excluded. Every report preserves `hardware_acceptance=false`.

Use the assembly command below with these additional explicit inputs:

```sh
  --profile manifests/ps4-diagnostics-console.json \
  --firmware-dir cache/ps4-firmware \
  --ssh-authorized-key /path/to/owner-key.pub \
  --tui-binary /path/to/psx-diagnostics-tui \
  --graphics-tools /path/to/compiled-probes \
  --browser-fixture /path/to/generated/video.webm
```

`scripts/deploy-ps4-diagnostics-ftp` updates an already identified diagnostic
USB through Orbis FTP. Its default is a read-only plan. Apply requires the
recorded serial, capacity and UUID; it verifies a host recovery copy and
readbacks of staged and published files. Superseded OS files leave the USB
after verification, while reports and SSH state remain. It does not launch
the console or modify internal boot files. The owner's FTP-only UART exception
applies to this preparation; it does not establish hardware acceptance.

## Preserved first development profile

`scripts/build-ps4-diagnostics` assembles the first Baikal image natively on
Linux x86-64. It consumes the matching native-dev3 kernel and all 41 modules,
the public pinned initramfs seed, a checksum-pinned local Arch development
package snapshot, and the private Mesa candidate. It does not call or change
the older OrbStack image wrappers. Generated images contain externally acquired
firmware and stay local; no binary or public release is established here.

This kernel has neither SquashFS nor overlayfs enabled. The image therefore
uses a small recovery initramfs and a compressed `rootfs.tar.gz` on FAT32,
unpacked into a bounded 1536 MiB tmpfs. The initial root is under 1 GiB of
regular files. System changes disappear on power-off. Results and interrupted
test journals are written to `psx-diagnostics/results` on the selected USB.
An embedded checksum binds the root archive to the initramfs; an embedded
contract validates the external boot receipt before starting the UI. The
running release is observed, while a kernel hash remains a build contract,
not a measurement of live kernel memory.

TTY1 opens the diagnostic menu and records inventory automatically.
**Quick check** runs inventory, a two-second CPU known-answer test and a
32 MiB memory pattern test. Ethernet connection and interface-bound HTTPS,
offscreen EGL/Vulkan queries and a 44.1 kHz audible fixture are individual
selections. Enumeration and API queries do not establish functional graphics
support. The DHCP service renews the selected connection without reopening or
rearming its retained MTS generation. No network service starts at boot.
Ctrl-Alt-F2 opens a local root rescue shell; SSH and password login are disabled.

The first profile requires `libata.force=1.00:disable` and
`psx_mts_net.autoarm=0`. It excludes internal-storage writes, power transitions
and the experimental UVD runtime. Native 48 kHz and wireless investigations
remain deferred. Every result carries `hardware_acceptance=false`; stability
still requires the repeated hardware gates in the kernel finish plan.

Example assembly from the repository root (supply the recorded local inputs):

```sh
sudo python3 scripts/build-ps4-diagnostics \
  --kernel-runtime /path/to/matching/dist/runtime \
  --kernel-source /path/to/matching/dist/source \
  --snapshot /path/to/pinned/package-snapshot \
  --seed cache/ps4/initramfs-v26-with-edid.cpio.gz \
  --mesa-archive /path/to/pinned/mesa-psx-candidate.tar.xz \
  --work /var/tmp/psx-diagnostic-build \
  --output output/ps4-diagnostics-dev1/boot-set
```

Use `--plan` without sudo for input validation and a dependency plan. `--resume`
requires the builder's matching ownership marker. The output contains the
whole boot set, source archive, package lock, metadata and `SHA256SUMS`.
The scripts refuse work/output directories overlapping their inputs.

`scripts/stage-ps4-diagnostics-usb` copies these files onto an existing mounted
FAT32 partition. It requires the complete stable `/dev/disk/by-id/usb-...`
identity, expected serial, exact capacity and mountpoint. Run its read-only
plan first, then use `--apply` for the authorized selected device. It does no
formatting or repartitioning, refuses unowned file replacements, keeps previous
owned files as backups, substitutes the selected UUID into `bootargs.txt`,
and reads back every copied file. Update an image by rebuilding and staging
the matching set; unchanged files are skipped. It never sends a payload.
The set includes `vram.txt` requesting 1024 MiB. The loader checks USB boot
files before its internal fallback paths; use this complete USB set and keep
other boot USB media disconnected so per-file lookup cannot mix two sets.

Run the synthetic tests with `TMPDIR=/var/tmp python3 -m unittest discover -s
tests -p 'test_ps4_diagnostics*.py'`. They use temporary fixtures rather than
live storage, sysfs or console hardware. A real launch still requires the
continuous UART logger, one bounded experiment and the existing PS4 loader.

The remaining sections describe the wider target, including suites and the
PocketJS frontend that are not implemented in this first image.

## Purpose

Boot a small, pleasant Linux environment that inventories the actual PS4,
explains each hardware feature, runs a selected finite test suite, and exports
evidence that contributors can reproduce. It should answer what works on this
particular console and build without requiring a full desktop installation.

The same suite should run from a shell, UART TUI or graphical UI. A successful
screen, driver probe or device enumeration must never silently become a
hardware PASS. The image is a diagnostic/development environment, not a new
claim that all PS4 hardware is supported.

## Boot architecture

```text
Orbis + the existing authorized loader entrance
  -> pinned Linux psX kernel and its whole matching module set
  -> small recovery/diagnostic initramfs
  -> RAM overlay + optional read-only Arch diagnostic root
  -> test runner <-> versioned results/events <-> PocketJS UI / UART TUI
```

On PS4, this is a Linux program after the loader/kexec handoff. A PC's UEFI
binary or GRUB menu does not replace that PS4 entrance. The UI can have a
boot-menu appearance inside the initramfs. A later PC edition may reuse its
views with an actual UEFI host, while Linux tests still need Linux drivers.

Use a tiny initramfs for early recovery, platform detection, UART status and
the menu. Use a pinned minimal Arch userspace for the larger development
edition: common packages, familiar tooling and repeatable package manifests
make driver iteration easier. A read-only image plus a RAM overlay avoids a
persistent installation; optional logs go only to an explicitly chosen place.
Choose the final root-image format after the loader/kernel capabilities and
the complete dependency footprint are measured. Arch is a userspace choice;
it does not change the PS4 kernel or boot protocol.

Keep the ordinary kernel and UVD experimental variant separate in the build
manifest and suite selection. Never load a second retained driver generation
to switch variants during one boot.

The inventory profile must declare MTS activation policy. Default it to
`psx_mts_net.autoarm=0` and prevent a background manager from opening `psx0`
before the selected test's fresh-boot checks. A selected test may use the
existing allow-open gate once. Native-autoload qualification is a separate
profile preserving the normal `autoarm=1` startup; a gated inventory boot
cannot establish that autoload result. Both modes record one generation only.

## Ownership and inputs

| Component | Owner |
| --- | --- |
| Kernel, matching modules, hardware probes and kernel evidence contract | `linux-psx` |
| Optional diagnostic initramfs/root image, UI, package selection and image builders | `linux-psx` |
| Production Omarchy distribution, installation and desktop policy | `omarchy-psx` |
| Continuous UART and bounded experiment extraction | `ps4-uart`, or equivalent recorder |
| Per-console keys, raw logs and acquired firmware | Local operator inputs |

Pin kernel/config/modules, loader, initramfs, root image, test suite, UI and
package versions. Supply required firmware separately under its recorded
terms. No image or report contains console keys, default passwords or private
network identity. Existing source acquisition/setup rules and OrbStack/native
host boundaries continue to apply.

Fresh full roots install the preserved diagnostic MIT notice at
`/usr/share/licenses/psx-diagnostics/LICENSE`; profile pruning retains it.
A frontend refresh preserves the original root bytes, so this root notice
appears only after a full root build.

## PocketJS interface

PocketJS is the owner-selected graphical candidate, subject to an actual PS4
prototype. The confirmed project is [PocketJS](https://pocketjs.pocket.nexus/)
and its reviewed upstream is `pocket-nexus/pocketjs`, commit
`252f8ee2173700998e5690858851c69f746171bb`. Its source includes
`platforms/linux/fbdev` and `programs/linux-kms`. The latter composes a CPU
renderer and QuickJS guest, tries KMS and then fbdev, and offers package-only
checking and an explicit headless mode. These are upstream implementation
facts, not PS4 acceptance.

Define our own compiled host contract for the actual PS4 framebuffer mode
and chosen logical viewport; the upstream 800x600 example rejects a different
physical size. Expose only the input/services we implement. The current
example host does not supply our diagnostic protocol: add a small typed native
bridge to the runner, rather than assuming Node, shell access or network APIs
are available to the guest.

Prefer a CPU software-rendered framebuffer path for the first screen, with
keyboard/controller input through Linux. Do not require working OpenGL,
Vulkan or a complete compositor to report a failed GPU test. Keep a UART/text
frontend with the same protocol. Separate the runner process from the GUI so
a failed frontend can reconnect to its journal, and checkpoint before a GPU
workload that could freeze the whole machine.

CPU rendering still depends on the kernel/display path: a GPU hang can freeze
the graphical screen too. UART and an external collector are the fallback.
Prefer render-node/offscreen GPU tests that leave the UI's display alone.
Tests that take DRM master or change modes need an explicit display handoff
and verified restoration; do not run competing modeset owners. A terminal
fault stops the suite rather than triggering an unqualified GPU reset.

PocketJS rendering and a possible UEFI backend solve presentation. They do
not provide Linux Ethernet, SATA, ALSA, DRM or decoder drivers. Never interpret
an ordinary desktop/window host as proof of an early-boot framebuffer host.

Prototype gate: a native x86-64 image paints readable text and progress on the
PS4 display without accelerated rendering, receives input, displays an injected
test failure, and preserves the UART/result stream. Measure binary, runtime
dependencies, font/asset sizes, RAM and boot time before choosing a size target.

References: [PocketJS overview](https://pocketjs.dev/docs/overview/),
[platform contracts](https://pocketjs.dev/docs/platform-contracts/),
[pinned source](https://github.com/pocket-nexus/pocketjs/tree/252f8ee2173700998e5690858851c69f746171bb).

## Interaction

The landing screen shows console/southbridge/GPU identity, exact boot set,
suite version, previous interrupted test and available capabilities.

Primary actions: **Quick check**, **Choose tests**, **Results**, **Export**,
and **Recovery**. Each capability card explains the operation it tests, what
an observed pass means, remaining limits, expected time, and dependencies.
Show logs and progress without requiring a shell; provide one concise CLI
entry point to run the same manifest for development and automation.

Routine enumeration can run automatically. A selected preset can run bounded
functional tests once its peers, media and required evidence are ready.
Storage writes, power transitions and known terminal-risk GPU/UVD workloads
remain distinct operator-selected tests. A workload requiring an external
peer, display observation or completed UART must show that dependency.

## Test catalogue

Discover actual PCI/USB/SDIO devices and runtime support. Do not choose Wi-Fi
or GPU behavior solely from “PS4,” or assume Baikal evidence covers Aeolia,
Belize, Gladius and every loader/firmware combination.

| Area | Useful evidence | Boundary |
| --- | --- | --- |
| CPU | Online cores, bounded work and errors | No claim of cpufreq or sustained cooling from enumeration |
| RAM | Available memory and bounded allocation/pattern test | Leave RAM for the system; no guessed VRAM repartition |
| Virtualization | KVM availability and an optional bounded guest smoke test | `/dev/kvm` presence alone does not prove a working guest |
| Display | Mode/connector data plus visible pattern confirmation | No automatic pass from connector status alone |
| OpenGL/Vulkan | Actual renderer, known pixel/reference test and bounded scene | Keep software rendering separate; renderer string alone is insufficient |
| Ethernet | Native autoload, DHCP, route, interface-bound traffic, peer hashes, recovery and CPU cost | Exactly one retained generation; no reset/unload/rearm |
| Wi-Fi/Bluetooth | Identify chip/firmware and expose optional network/pairing tests | Working on reported setups; further investigations deferred pending detailed bugs |
| HDMI audio | Known sample, supported 44.1 kHz output, duration/pitch observation | Native 48 kHz is deferred, not a default failing release gate |
| USB audio/input | Exact device identity and selected capture/playback/input test | A device node does not prove audible output or recorded input |
| USB storage | Read-only identity and selected checksum reads | Never choose a write target from `/dev/sdX` order |
| Internal SATA | Stable identity, mapping validation, read tests, separately selected disposable-file I/O | No automatic format, raw-disk write, Orbis mutation or image growth |
| UVD AVC | Verified hardware path, downloaded pixels vs reference, paced playback and handle lifetime | Use the matching UVD variant and explicit one-session/recovery limits |
| Power | Selected shutdown/reboot test and external observation on next startup | A program exit cannot prove physical power-off; suspend remains excluded |
| Thermal/fan | Read-only sensors and available controls | No automatic PWM/clock writes or unbounded heat stress |
| LED/buzzer/clock controls | Inventory and separately selected observed control tests | Unsupported controls stay disabled; no automatic clock or power-policy changes |
| Optical/other devices | Inventory and an explicit supported probe | Absent/unsupported/untested are distinct from failed |

Every test definition declares its hardware/config dependencies, finite time
budget, stimulus, oracle, artifacts, cleanup, terminal-stop rules and supported
state transitions. Unsupported transitions are disabled instead of attempted
as routine diagnostics. Do not turn a failed action into an automatic retry.

For an internal-root variant, revalidate the complete extent map through a
read-only view of the containing filesystem before each writable activation.
Installation-time verification or matching only the first ext4 UUID is not
enough; a changed/unverifiable map enters recovery before any image write.

## Results and repeatability

Use distinct statuses: `PASS`, `FAIL`, `DEGRADED`, `NOT_RUN`, `UNSUPPORTED`,
`DEFERRED`, `BLOCKED` and `INCONCLUSIVE`. Record the specific reason and
whether the result is automatic, operator-confirmed or externally observed.
“Not detected” without enough inventory evidence is not a failure diagnosis.

Every run records a schema and suite version, unique run/boot IDs, console
and device facts, artifact hashes, parameters, expected result, measurements,
timestamps, exit/signal/timeout, relevant redacted logs, operator observation,
UART session and continuity state, and next-boot/recovery correlation where
needed. Export a machine-readable report plus a short human-readable view.

Store a durable “test about to start” record before terminal-risk workloads.
The RAM overlay is not durable across a power-off. Such a test must have a
selected persistent evidence sink or an external collector that acknowledges
the saved run marker; an explicit operator receipt for the established UART
capture/session can provide that handoff. Without durable evidence readiness,
power, destructive and terminal-risk tests remain `BLOCKED`. Never create a
new disk-writing destination implicitly just to satisfy this requirement.
After a crash or power test, the next boot can correlate the unfinished run
with operator/UART evidence; it must not manufacture a PASS from a missing log.
Power-cut tests require an observation outside the process being stopped.
One successful run remains evidence; stability follows the repeated gates in
the kernel finish plan. A report collected without UART can still help triage,
but cannot satisfy a gate that explicitly requires completed UART continuity.

The UI consumes structured events/results. It does not parse arbitrary shell
output or decide privilege. The runner accepts versioned test IDs and typed
parameters, not arbitrary commands supplied by the frontend. Test programs
retain their normal process boundaries and finite termination behavior.

## Developer experience

Develop and replay the UI against recorded synthetic results on the host.
Offer deterministic PASS/failure/timeout/disconnect/unsupported scenarios and
snapshot the same screens used on the console. Test the protocol and runner
without accessing the host's devices. Package actual GPU/audio/network tools
as selected suites; a complete desktop is optional.

Use existing native Linux source/test tools. The existing PS4 image wrappers
remain OrbStack-only; a native Linux diagnostic-image assembler needs its own
implementation and host validation. Do not bypass those runtime guards.

During development, side-load a rebuilt `.pocket` UI package and restart only
its frontend when the current driver state permits it. Test workers and
reports stay independent. Kernel/module changes require a new whole matching
boot set; a faster UI loop must not hide that artifact boundary.

## Delivery sequence

1. Finish and package the current native kernel checkpoint; write its exact
   hardware gates. Keep the existing boot/recovery set available.
2. Define the small test/result protocol and implement host-synthetic replay
   plus a UART TUI. Reuse reviewed kernel probes instead of duplicating them.
3. Build the minimal diagnostic userspace with a pinned package manifest and
   no automatic destructive or terminal-risk suite.
4. Prototype the PocketJS software framebuffer frontend, then qualify its
   display/input and result recovery on real hardware.
5. Add Ethernet, display/audio and other ordinary suites against the frozen
   acceptance criteria. Add internal-storage and UVD suites separately when
   their source and lifecycle preconditions are satisfied.
6. Expand the hardware catalogue as concrete support and test oracles exist.
   Add a PC UEFI presentation only as a separate platform; it is not required
   to make PS4 diagnosis useful.

This proposal authorizes no console action, disk write, public release or
firmware redistribution. Concrete implementation and hardware experiments
keep their existing source-review, authority and UART requirements.
