# Diagnostic appliance experiments

Linux psX owns the diagnostic source and image builds from 2026-11-04.
Earlier local appliance experiments EXP-20261104-001 through 011 remain in
the preserved Omarchy workspace ledger at
`platforms/ps4/experiments/SESSIONS.md`. This source migration does not promote
their binaries or hardware support. Raw captures and private receipts remain
outside Git; one completed test is not stability qualification.

## EXP-20261104-012 — standalone frontend and automatic Firefox baseline

- Built the 28-asset frontend from Linux psX without an Omarchy source
  dependency. Passed 279 Python, 11 browser JS and 28 Rust tests, then inspected
  actual CPIO assets/modes/hashes and the embedded/external contracts. Core
  payload: 1,065,928,109 bytes. Initramfs: 5,038,832 bytes, SHA256
  `adaac28e7190318757499bcd57cbfaef8ec17633fac1617624b4df802b0b9706`.
  Kernel, complete module set, private Mesa, root image and installed boot
  arguments remain unchanged.
- The identified USB update passed backup/full changed-file readback.
  Recovery copy: workspace `ps4/backups/psx-diagnostics-standalone-20261104-012/`.
  Live activation used a separate private 20 MiB staging tmpfs and 40 owned,
  private, read-only file binds. All source/target readbacks passed; no
  NetworkManager, Ethernet driver or SSH daemon restart occurred.
- The graphical diagnostic menu reopened. Firefox used a fresh profile and
  consumed the explicit one-shot launch intent. Suite
  `45b3d10c-a870-43af-b456-e36df0d8f4af` saved all five reports automatically.
  Capabilities remains informational/INCONCLUSIVE; Canvas, WebGL reference
  pixels, animation completion and video completion passed. WebGL read back
  4,096 expected pixels with zero mismatches/errors/context loss and an AMD
  hardware classification; the privacy-filtered renderer is not an exact GPU ID.
- Each report independently recorded configured 1920×1080/nominal 60 Hz.
  Ten-second animation: 578 samples, 57.784 average FPS, median 16.66 ms,
  p95 17.106 ms, p99 33.372 ms, maximum 50.52 ms, 23 estimated missed frames.
  This is browser timing, not physical scanout measurement or a performance
  qualification. VP8 video completed with zero dropped frames out of 90;
  hardware decoding is not established. The ordinary kernel has UVD disabled.
- A separate manual suite completed earlier at 16:10 UTC, outside EXP010's
  capture. It is retained as client evidence and is not counted as this
  automatic baseline. Report grouping must use suite identity and timestamps.
- Verdict: standalone packaging/live preparation and one automatic Firefox
  baseline observed working; timing gaps remain visible. No Vulkan, Chromium,
  Wayland, driver stability or new boot acceptance follows. Completed UART
  continuity: 12,268 bytes, same generation and connection epoch. Context:
  `20261104_223210_076483-exp-20261104-012-standalone-frontend-and-automatic-firefox-basel-2d0db5cc.md`
  in sibling `ps4-uart/sessions/`; display bridge/IRQ diagnostics are present,
  with no observed GPU timeout/reset/OOM. Private receipts and saved reports:
  `output/ps4-diagnostics-standalone-import1/`.

## EXP-20261104-013 — Chromium automatic comparison baseline

- Selected the fixed optional Chromium 151 provider through the controller.
  Its complete image hash matched before mounting `/dev/loop1` separately as
  ext4 read-only/nosuid/nodev with execution allowed. Core root/kernel/modules
  and the existing Xorg session were retained. Chromium ran as `psx` UID1000,
  using a fresh RAM profile and X11/ANGLE OpenGL; Vulkan features stayed disabled.
- Automatic suite `cf6e51d5-9d8f-4850-8a06-1396a92aa13e` completed all five
  reports. Canvas and WebGL reference pixels passed with no GL error, mismatch
  or context loss. The actual renderer was
  `ANGLE (AMD, AMD DG1401SNF87ID (radeonsi liverpool ACO), OpenGL 4.6)`.
  Capabilities remains informational/INCONCLUSIVE.
- Configured mode: 1920×1080, nominal60 Hz. Ten-second animation:599 samples,
  59.847 average FPS, median16.7 ms, p95/p99 16.8 ms, maximum18.1 ms and zero
  estimated missed frames. Video completed with one dropped frame out of90
  (1.11%); its PASS is playback completion, with a separate visible drop warning.
  Hardware decoding is not established. These are single short observations,
  not comparative benchmarks or stability acceptance.
- Read-only process inspection confirmed renderer children with UID1000,
  NoNewPrivs1, Seccomp2, zero effective capabilities and distinct user/PID
  namespaces. The selected command line contained no sandbox-disabling flags.
  This does not certify every Chromium process's sandbox: the GPU process and
  some utilities reported Seccomp0. Two initial inspector outputs failed to
  identify process roles because Chromium exposes its title as one space-joined
  argv string; corrected inspection used the actual title format. No browser
  restart or sandbox relaxation was used to obtain the corrected evidence.
- Verdict: one automatic Chromium Canvas/WebGL/animation/video baseline
  observed working, with the video drop retained. No Vulkan, UVD or stable
  driver promotion follows. Completed UART continuity:547 bytes, same logger
  generation/epoch. Context:
  `20261104_224025_278849-exp-20261104-013-automatic-chromium-comparison-baseline-47cffcf7.md`
  in sibling `ps4-uart/sessions/`; read-only loop mount, a wireless-extension
  warning and display-mode queries were recorded, with no GPU timeout/reset/OOM.
  Private reports and process receipts:
  `output/ps4-diagnostics-chromium-console1/`.

## EXP-20261104-014 — ordered frontend; SSH initialization compatibility failure

- The frozen 29-asset `ordered-ssh-browser1` candidate passed 354 Python,
  33 Rust and 15 Node tests plus actual archive/contract inspection. Core:
  1,065,953,301 bytes. Initramfs: 5,063,809 bytes, SHA256
  `5d3ff416309d6e15d5c8b419b4ecfdf171203aa41ecf1aed596298da8a70cc0f`.
  Kernel/root/modules/Mesa were unchanged. The identified USB update passed
  backup and full changed-file readback; owner boot arguments were preserved.
  Recovery: workspace `ps4/backups/psx-diagnostics-ordered-20261104-014/`.
- Private live activation passed 41 owned read-only file binds and all 29
  source readbacks. CPU/RAM ordering, the fixed browser layout, UI settings,
  network choices and SSH enrollment source are installed. This is userspace
  preparation; no reboot/new-kernel acceptance follows.
- SSH enrollment initialization refused the actual OpenSSH 10.5p1 effective
  configuration. Its `sshd -T` emits directive names such as `Port`,
  `AuthorizedKeysCommand` and `PasswordAuthentication`; the new validator
  compared unnormalized names against lowercase keys. The original policy was
  restored. Existing owner-key SSH remained available through fresh
  connections, root remained exactly locked (`!`), and password authentication
  remained disabled. No key import or temporary-password window occurred.
- The integration inspector initially asserted that enrollment fingerprints
  would be present; it stopped on the intentionally unavailable setup response.
  Read-only inspection then isolated the case mismatch. This is an SSH-check
  compatibility defect, not lost owner access or a kernel/network failure.
  Automatic browser tests were **not run** in this experiment. The graphical
  menu was requested again for recovery; corrective testing needs a new source
  revision with the case-normalization regression test.
- Verdict: frontend/USB preparation passed; enrollment integration failed;
  automatic lifecycle acceptance remains pending. Capture continuity completed:
  12,283 bytes, same logger generation/epoch, 23:18:11–23:23:43 local. Context:
  `20261104_231811_581141-exp-20261104-014-ordered-frontend-private-ssh-setup-availability-d6f1f261.md`
  in sibling `ps4-uart/sessions/`. Display bridge/IRQ diagnostics and an ordinary
  Wi-Fi rekey were present; no observed GPU timeout/reset/OOM. Private plans,
  validation, activation and SSH inspection receipts are under
  `output/ps4-diagnostics-ordered-ui1/`.

## EXP-20261104-015 — SSH correction and automatic/manual browser lifecycle

- The only changed frontend asset from EXP014 was `ssh_access.py`: normalize
  effective OpenSSH directive names while preserving exact policy values.
  The observed CamelCase regression reproduced EXP014 before the fix and
  passed afterward. All 356 Python and 15 Node tests passed; unchanged UI3
  retained its 33 passing Rust tests. The corrective read-only console
  inspection passed against the original single-owner-key policy before any
  new installation. No enrollment was run by that inspection.
- `ordered-ssh-browser2`: core 1,065,953,353 bytes; initramfs 5,063,861 bytes,
  SHA256 `d6ab2cf11b467993ffa93eeefac0c25b07b60203d465817bedc9bba0f622d567`.
  Actual CPIO comparison confirmed one frontend asset change plus its manifest,
  environment and TSV receipts. USB backup/readback and 41 private live binds
  passed; kernel/root/modules/Mesa/boot arguments remained unchanged.
  Recovery: workspace `ps4/backups/psx-diagnostics-ordered-20261104-015/`.
- SSH initialization passed. The original owner-key bytes were preserved,
  fresh pinned-key SSH connected, and the same validated owner public key was
  copied to protected USB state with exact RAM/USB readback and mode 0600.
  GitHub preview for the owner's account returned three fingerprints; preview
  did not import those keys. Root remained `!`, password authentication was
  disabled, and both original and enrolled key-file paths were authorized.
  Temporary-password enable/expiry remains synthetically tested, not exercised
  on the console. Unseeded-image first boot was not tested by this seeded image.
- Automatic Firefox batch `20261104T173237Z-44b42491029d` and Chromium batch
  `20261104T173408Z-d7d364a2eeab` each saved the five ordered reports and a
  durable completed batch summary, then closed only their fixed browser unit.
  The graphical menu stayed active with the same PID and returned on tty3.
  Both automatic units exposed a two-minute independent systemd runtime limit.
  Existing matching CPU/RAM PASS receipts were reused without another worker
  run. Failure/interruption gating and crash/timeout closure paths have host
  tests; no artificial hardware fault or controller crash was induced here.
- Each browser's capabilities report and aggregate remain INCONCLUSIVE because
  enumeration is observational. Canvas, WebGL known pixels, animation completion
  and video completion passed. Both WebGL runs verified 4,096 pixels with zero
  mismatches/errors/context loss. Configured mode was 1920×1080/nominal 60 Hz.
  Firefox privacy-filtered AMD naming is not an exact GPU model; Chromium
  identified radeonsi Liverpool through ANGLE/OpenGL 4.6.
- Ten-second animation observations: Firefox 567 samples, 56.652 average FPS,
  p95 32.894 ms, maximum 50.34 ms and 34 estimated missed frames; Chromium
  599 samples, 59.848 average FPS, p95 16.8 ms, maximum 18 ms and zero estimated
  missed frames. These are browser callback timings, not physical refresh
  measurements or a comparative benchmark. Three-second VP8 playback completed
  with Firefox 0/90 drops and Chromium 1/90 drops. Hardware decoding is not
  established; ordinary-kernel UVD remains disabled.
- A fresh manual Firefox workspace had runtime `infinity`, remained open for
  the seven-second observation, and created no automatic batch or browser
  reports. It was closed explicitly; the menu was preserved.
- Verdict: this frontend's enrollment availability, automatic report/save/close
  flow and manual idle policy were observed working on the recorded console.
  No new boot, decoder, other-model or stability qualification follows.
  Completed UART continuity: 12,495 bytes, same generation/epoch,
  23:26:59–23:36:27 local. Context:
  `20261104_232659_732886-exp-20261104-015-corrected-openssh-directive-name-parser-validat-0849c075.md`
  in sibling `ps4-uart/sessions/`; display bridge/IRQ diagnostics remain, with
  no observed GPU timeout/reset/OOM. Private receipts and report copies:
  `output/ps4-diagnostics-ordered-ui2/`.

## EXP-20261104-016 — readable hardware overview on the console

- The sole frontend asset change was the TUI binary. The 29-asset boot set
  measured 1,065,967,194 bytes; initramfs 5,077,704 bytes, SHA256
  `5d4fc051886704e552e50c40016b8280dcf9c94f2ebfcf53bc7b6b4244bfd4f6`.
  The TUI was 854,504 bytes, SHA256
  `36d5d5132c63ee228a292c46369c59b6e75bd37ffd388323660791fbc0736f38`.
  All 39 Rust tests, six SSH UI integration checks, PTY navigation/resize/exit
  smoke and an independent source audit passed. Actual CPIO comparison verified
  the TUI-only asset delta and unchanged controller/auth/lab/kernel/root/Mesa.
- Identified USB backup/readback and 41 private live binds passed. Recovery:
  workspace `ps4/backups/psx-diagnostics-hardware-20261104-016/`. Fresh pinned
  owner-key SSH and graphical-menu startup passed; password access stayed off.
- Captured the actual PS4 X11 output at 1920×1080 using the existing FFmpeg
  X11 grabber. The component overview, CPU and memory detail pages rendered
  readable fields; CPU Advanced showed raw data only after explicit A.
  Esc restored detail/overview state. End reached PCI lines 90–119 of 119
  without overwriting the footer. The overview remained open on Graphics.
  These screenshots inspect the console-rendered framebuffer, not physical
  HDMI scanout or a new display stability qualification.
- The input/capture helper required the focused `psX Diagnostics` terminal and
  password access off; no temporary password was enabled. Navigation created no new
  controller jobs or diagnostic reports; the before/after sets matched exactly.
  No browser/GPU diagnostic was rerun for this TUI-only change.
- Verdict: readable hardware overview, drilldown, explicit Advanced data and
  bounded scrolling/back navigation observed working. Completed UART continuity:
  12,042 bytes, same generation/epoch, 23:51:24–23:58:29 local. Context:
  `20261104_235124_542501-exp-20261104-016-hardware-overview-and-enter-esc-advanced-displa-c04856cb.md`
  in sibling `ps4-uart/sessions/`. Display bridge/IRQ messages remain, with no
  observed GPU timeout/reset/OOM. Private captures and validation receipts:
  `output/ps4-diagnostics-hardware-ui1/`.

## EXP-20261105-001 — hardware overview column divider

- Added the requested vertical divider in the existing spacer between the
  component list and details. All 39 Rust tests passed; no new dependency or
  runtime asset was added. Actual CPIO comparison confirmed the TUI-only asset
  delta and unchanged controller, auth, lab, helpers, kernel/root/modules/Mesa.
- `hardware-overview2`: core 1,065,967,170 bytes; initramfs 5,077,680 bytes,
  SHA256 `f9b40c097f97c5a9e308ba193744cbc29788c6c7527d4b2b3c91950c8afa0f77`.
  TUI 854,728 bytes, SHA256
  `1a2836d7e53c0626a4757fe75e96a74520f6080597e9ece229b5e2df72aec68f`.
  USB backup/readback and private RAM activation passed. Recovery: workspace
  `ps4/backups/psx-diagnostics-divider-20261105-001/`.
- Fresh pinned-key SSH, password-access-off check and graphical-menu startup
  passed. Captured actual PS4 X11 output at 1920×1080; the divider is visible
  beside the component list. Enter/arrow navigation reached the Graphics
  overview, which remains open. No browser or GPU diagnostic was rerun for
  this drawing-only change. The prior UI1 binary was preserved before rebuild.
- Verdict: requested separator observed working. Completed UART continuity:
  12,044 bytes, same generation/epoch, 00:02:34–00:05:48 local. Context:
  `20261105_000234_080829-exp-20261105-001-hardware-overview-vertical-divider-expect-visib-1d6a4c67.md`
  in sibling `ps4-uart/sessions/`. Display bridge/IRQ diagnostics remain;
  no new hardware-support or stability qualification follows. Private capture,
  build and deployment receipts: `output/ps4-diagnostics-hardware-ui2/`.
