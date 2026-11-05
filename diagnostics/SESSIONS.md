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

## EXP-20261105-002 — alpha preparation and preserved rollback

- The operator returned from the existing ordinary diagnostic boot to Orbis
  and started FTP. The existing diagnostic USB ownership, serial/capacity/UUID
  and 13-file receipt matched the recorded device. A complete host rollback
  copy passed streamed SHA256 readback for every owned boot file, including
  the root image. Recovery: workspace
  `ps4/backups/psx-diagnostix-power-alpha2-20261105/`.
- No console/USB file was changed and no alpha kernel or decoder was launched.
  Before deployment the owner expanded the candidate to include every current
  reviewed native-dev3 fix plus UVD and the ICC power-off contribution. The
  frozen historical UVD alpha was superseded by that new combined-build scope.
  Diagnostix naming and unavailable Power controls are interface work, not
  observed power-management support.
- Verdict: rollback preparation passed; the proposed alpha boot/decode is
  **NOT RUN**, so this session establishes no UVD, power-off or kernel stability
  acceptance. Completed UART continuity: 155,932 bytes, same generation/epoch,
  00:58:35–01:13:58 local. Compact context:
  `20261105_005835_491537-exp-20261105-002-matched-uvd-alpha1-integration-update-identifie-3861cb7c.md`
  in sibling `ps4-uart/sessions/`. Continuous capture remains running; begin a
  new bounded session for deployment and launch of the combined candidate.

## EXP-20261105-003 — matched alpha2 upload; first boot has no display

- Built `7.2.11-psx-ps4-baikal-uvd-runtime-dev2` from all 73 frozen active
  native-dev3 patches plus the declared UVD overlay, retaining the ICC power-off
  fix and UVD shutdown park. Kernel SHA256:
  `6b1d9d90d29eeffb58057cd04a387ad08c53adc7d13afb62bcb2b3af8c686fdc`.
  Full corresponding source and all 41 matching modules passed archive
  readback. Diagnostic checks passed 433 Python, 15 browser and 47 Rust tests;
  kernel source checks passed 47 tests, and build helpers passed nine checks.
- The 19-file core and separate matched Chromium provider passed staged and
  published FTP readback on the identified diagnostic USB. Its prior owned OS
  files were backed up and retired; SSH state/results and ordinary optional
  providers were retained. Core size: 1,066,195,032 bytes, plus the reserved
  1 MiB still below 1 GiB. Final initramfs SHA256:
  `72aa6762d03bad5b4b2434917e5d1c8e5c8d2d5f58afffcb3a96e105a6f59071`.
  Root SHA256:
  `7b903fa116835f1a5c9d2c287dd0a0603167b0ad83585442133f476327bc1e0a`.
- One launch through the existing 1024 MiB BIN reached the intended dev2 Linux
  kernel with all three UVD gates enabled. The operator ultimately reported
  **no HDMI signal**; Ctrl+Alt+F2 produced no visible console. Neither previous
  Linux network address responded to ping/SSH. Userspace and UVD entry are not
  established. The last UART line was `No irq handler for 0.49` at 60.894343 s;
  that emergency-priority message does not isolate the cause.
- `loglevel=3` suppresses even `dev_err` firmware/UVD messages, so the quiet
  capture cannot locate the failing stage or establish an E820 stop. Source
  inspection confirms the expected UVD firmware is in the actual initramfs.
  Next isolate logging only on a cold boot, retaining every artifact and all
  three UVD gates. No decoder retry, GPU reset/unload or power test occurred.
- Verdict: file preparation passed; the observed display/network boot failed,
  with cause **INCONCLUSIVE**. UVD decode and power-off tests are **NOT RUN**;
  no new hardware acceptance follows. Completed UART continuity: 40,364 bytes,
  same generation/epoch, 01:40:44–02:08:58 local. Compact context:
  `20261105_014044_417313-exp-20261105-003-matched-diagnostix-alpha2-usb-deployment-and-si-54a8ab1d.md`
  in sibling `ps4-uart/sessions/`. Private receipts and failure record:
  `output/ps4-diagnostix-alpha2-complete/`.

## EXP-20261105-004 — verbose trace localizes UVD startup rejection

- Following physical recovery to Orbis, changed only boot logging to
  `loglevel=7 ignore_loglevel`. The 500-byte argument file and generated
  ownership/checksum receipts passed transactional backup and readback;
  kernel, initramfs, root, modules and all three enabled UVD gates were retained.
  Host checks passed 439 Python and 15 browser tests. The corrected updater
  reads only changed USB files, preserving the verified large-image pins.
- One fresh boot found the UVD firmware and reached the cache-VMID preflight
  with `before=0000000e`, matching its expected initial selector 14. At
  8.506288 s it logged `PS4_UVD_RUNTIME CLOSED error=-22`, failed the UVD IP
  hardware init and then GPU init. At 8.538510 s it entered
  `RETAINED reason=unload-kms`. This terminal safety park explains why this
  boot cannot provide the diagnostic display or SSH. No recovery/reset/unload
  or further active test was attempted on the parked kernel.
- Source and independent review found a definite packaging defect: alpha2's
  232,752-byte stock firmware, SHA256
  `30eed8d680321bdc6213409a4a882442049b2a559eadb9ee2554d53b0cfb96c9`,
  cannot pass this runtime's 314,168-byte firmware guard. The preserved matching
  variant has SHA256
  `62a43ef4814f59fdd6772792e0a9303e016fe2e17ad30b36412d1fa7ce4cd5cb`,
  header version `0x012a6509`, payload 313,912 bytes at offset 256 and calculated
  CRC `0xa465ba07`; each matches the frozen source contract. Firmware stays a
  private external input. Clock preflight precedes firmware validation and can
  also return `-EINVAL`, so the log alone does not prove which returned first.
  The wrong firmware is independently sufficient to prevent startup.
- Verdict: trace **PASS** for localization; alpha2 boot **FAIL** from a package
  with an incompatible firmware input. UVD playback/power tests are **NOT RUN**.
  Correct both root and initramfs firmware identities, preserve every driver
  guard and verify the replacement package before another captured cold boot.
  Completed UART continuity: 251,803 bytes, same generation/epoch,
  02:12:07–02:24:02 local. Compact context:
  `20261105_021207_447500-exp-20261105-004-logging-only-alpha2-cold-boot-trace-same-artifa-a6f091c9.md`
  in sibling `ps4-uart/sessions/`; private receipts:
  `output/ps4-diagnostix-alpha2-boot-trace1/`.

## EXP-20261105-005 — corrected firmware boots; first exact AVC decode passes

- Corrected both firmware copies to the runtime's verified 314,168-byte input,
  SHA256 `62a43ef4814f59fdd6772792e0a9303e016fe2e17ad30b36412d1fa7ce4cd5cb`.
  Preserved the combined dev2 kernel, all 41 matching modules, verbose boot
  arguments and all three UVD gates. Core deployment/readback passed; root
  SHA256 `9312ebc229f0b12a9d8586e266dc02da757b302db07a3d4878941d20d8a4ec33`,
  initial initramfs SHA256
  `d9189a025447117216b6f981859ba2f802ad3bac91c82b1680fb9f50b2040dfe`.
- One boot reached `PS4_UVD_RUNTIME STARTED` at 8.877329 s and `QUEUE_READY`
  at 8.890038 s, verified the root checksum and reached the menu and pinned-key
  SSH. CPU computation and the owned 32 MiB memory-pattern check passed on
  this boot. No failed systemd units or terminal UVD/GPU fault were observed.
  The FAT dirty-volume warning remains; this is not filesystem qualification.
- The separately scoped Chromium provider failed its first FTP hash check.
  A fresh compressed SSH transfer matched every provider file before atomic
  publication. Its automatic five-step browser baseline completed and closed:
  Canvas, hardware WebGL, frame pacing and video passed; capabilities remained
  observational `INCONCLUSIVE`, so the batch does not claim a blanket PASS.
  WebGL identified AMD radeonsi Liverpool through ANGLE. Callback cadence was
  about 59.85 fps at a configured 1920x1080/60 Hz; this is not measured physical
  refresh. Browser hardware video decoding remained disabled.
- Before decoding, applied the separately requested navigation-only TUI as a
  read-only RAM file overlay, SHA256
  `752c59644f5a152ee57809ec221660165361e1bced516d7e49a7c0e41abcc97a`.
  Captured the filled active tab and Reports Up-to-navbar navigation; controller
  and SSH PIDs stayed unchanged. USB boot files, root, firmware and kernel were
  unchanged. This live UI overlay is distinct from the frozen boot-set identity.
- An earlier menu selection was blocked before hardware access by the missing
  UART assertion; no attempt was reserved. After verifying the same continuous
  session and installing its boot-bound assertion, ran exactly one explicit
  `uvd.avc` action. Forced VAAPI on the pinned render node and Mesa driver
  produced all 120 128x96, 30 fps Constrained Baseline AVC frames as exact NV12:
  2,211,840 bytes, SHA256
  `d99b87567601efb735d438e766f34112344a3a31fc1d9e1b4177bfd329bcb491`.
  The decoder finished in about 0.56 s without a timeout; that is a short
  unpaced fixture result, not sustained playback performance. Host readback
  independently verified every frame and all saved artifact hashes.
- Verdict: corrected boot and **one exact hardware AVC decode PASS**. This
  does not establish repeated boots, 720p/1080p, other AVC profiles, browser
  hardware decoding, long playback, cleanup/resource reuse or stable support.
  The immutable attempt/stop intent remains. No further active test, driver
  reset/unload/rearm, power command or automatic recovery occurred afterward;
  collect reports, then recover by physical power-off before another experiment.
  Ethernet was usable for SSH transfer; its snapshot had zero RX/TX errors but
  1,096 RX drops, so its separate three-boot/endurance gate remains open.
- Completed UART continuity: 300,594 bytes, same generation/epoch,
  02:33:23–03:29:20 local. Compact context:
  `20261105_023323_797690-exp-20261105-005-corrected-matching-uvd-firmware-alpha3-package--5bf7d3a1.md`
  in sibling `ps4-uart/sessions/`. Private report, decoded-output readback,
  provider receipts and screen captures:
  `output/ps4-diagnostix-alpha3-preparation/`.

## EXP-20261105-006 — new menu persists; cold boot and reference decode pass

- Published only the new initramfs and boot metadata, with changed-file SHA256
  readback and a private small-file rollback. Kernel, root, matching modules,
  firmware and enabled UVD gates stayed fixed. Preserved the working verbose
  argument file and USB SSH/preferences/reports/providers. Frozen frontend:
  `alpha3-scenarios1`, source `bc4c463`; initramfs SHA256
  `8741e0459347809460b095905b2f981297979e1e7558eadca03c51661187678a`,
  TUI SHA256
  `14ded3f1873fc11145cbbfccf2d00e33337d155ab201c21a4538fc6179f96cee`.
- The operator confirmed full physical power-off before this launch. The
  intended kernel reached UVD STARTED/QUEUE_READY at 8.622933/8.635617 s,
  verified the read-only root, and reached the native TUI and pinned-key SSH.
  The installed TUI and firmware hashes matched. X11 stayed inactive; there
  were no failed systemd units. The operator confirmed menu response, filled
  Reports selection and Up-to-navbar navigation. The FAT dirty-volume warning
  remains; filesystem integrity and ordinary shutdown are not qualified.
- Current-boot CPU computation and the owned 32 MiB memory-pattern check passed.
  One final `uvd.avc` action produced all 120 128x96 Constrained Baseline AVC
  frames through forced VAAPI, with no software fallback. All 2,211,840 NV12
  bytes matched SHA256
  `d99b87567601efb735d438e766f34112344a3a31fc1d9e1b4177bfd329bcb491`.
  The short unpaced decoder took about 0.58 s. Host readback compared every
  frame directly against the pinned fixture manifest and verified saved
  artifact hashes; post-run evidence contained no terminal UVD/GPU fault.
- Verdict: **single cold boot, menu navigation and canonical AVC decode PASS**.
  This is the first canonical component on this exact new initramfs/TUI set.
  EXP005 retains its different frontend scope and cannot silently fill this
  cohort's three-boot count. Native 900-frame/high-resolution, sustained
  playback, lifecycle/admission/quiescence and power gates remain open; the
  six scenario descriptions do not make unfinished harnesses executable.
  The immutable stop intent remained; no further active hardware work followed.
- Completed UART continuity: 120,437 bytes, same generation/epoch,
  03:41:45–03:50:45 local. Compact context:
  `20261105_034145_665347-exp-20261105-006-alpha3-tui-scenarios-update-and-fresh-boot-same-c4f70499.md`
  in sibling `ps4-uart/sessions/`. Private exact output, fixture comparison,
  boot-health checks and operator observations:
  `output/ps4-diagnostix-alpha3-scenarios1/`. Continuous UART capture remains
  running; physical power-off recovery is required before another UVD run.

## EXP-20261105-007 — repeated cold boot passes; decode deferred

- The operator confirmed full physical power-off. The unchanged
  `alpha3-scenarios1` boot set reached the native menu, pinned-key SSH and UVD
  STARTED/QUEUE_READY on a new boot. Installed frontend, firmware and declared
  artifact identities matched EXP006. Current-boot CPU computation and the
  owned 32 MiB memory-pattern check passed.
- The owner requested graphical applications before the final decoder action.
  No actual UVD attempt was reserved or run in this experiment. This adds one
  successful boot observation, not another canonical AVC decode pass.
- Completed UART continuity: 141,861 bytes, same generation/epoch,
  03:54:26–04:02:49 local. Compact context:
  `20261105_035426_106659-exp-20261105-007-second-cold-boot-of-frozen-alpha3-scenarios1-ca-b93fec89.md`
  in sibling `ps4-uart/sessions/`; private evidence:
  `output/ps4-diagnostix-alpha3-coldboot2/`.

## EXP-20261105-008 — OpenGL and Firefox functional baseline

- On the same unchanged boot, explicitly opened the optional X11 menu. The
  60-second OpenGL gears window identified hardware Liverpool rendering and
  ran at about 59.5–59.9 fps; the owner confirmed normal motion. The bounded
  OpenGL reference-pixel check also passed. This is scoped rendering evidence,
  not physical display-refresh or broad GPU qualification.
- Firefox 154's automatic functional baseline completed and closed. Canvas,
  WebGL, animation and the software-decoded VP8 video passed; capabilities
  remained observational INCONCLUSIVE, so the aggregate remained INCONCLUSIVE.
  The owner then requested actual browser hardware AVC decoding. Chromium
  and the final standalone AVC action were not started in this experiment.
- Completed UART continuity: 7,545 bytes, same generation/epoch,
  04:03:17–04:20:27 local. Compact context:
  `20261105_040317_974271-exp-20261105-008-x11-graphical-diagnostics-apps-before-final-avc-c9be2c3b.md`
  in sibling `ps4-uart/sessions/`; private application reports:
  `output/ps4-diagnostix-alpha3-graphics1/`.

## EXP-20261105-009 — browser AVC plays after hardware hand-off failure

- Ran one explicit sandboxed Firefox 154 trial with hardware WebRender/EGL and
  AVC decoding enabled. The kernel, root, firmware and matching modules stayed
  fixed. The separately staged page and 91,028-byte MP4 were SHA-pinned. Host
  software decoding had independently verified all 120 canonical NV12 frames;
  this verifies the input container, not browser output or hardware playback.
- Firefox initialized VA-API with the pinned driver, then logged
  `CreateImageVAAPI(): failed to get VideoFrameSurface` at its first frame.
  It recreated a software decoder: zero completed VA-API frame messages and
  120 software frame messages. The page ended at about four seconds with
  120 total/presented frames and zero reported drops. The owner observed the
  page appear, play apparently normally and close. **Browser hardware decoding
  did not pass.** No terminal UVD/GPU fault appeared in the captured evidence.
- The original result remains INCONCLUSIVE. Its first page predicate rejected
  an unavailable `corruptedVideoFrames` counter before surfacing the actual
  decoder failure; retained raw evidence establishes the fallback. Separately,
  the RDD sandbox denied the libva trace-file open, and an incorrectly cased
  `DMABuf` logging category omitted the inner hand-off details. These collection
  limitations do not turn software playback into hardware evidence.
- The immutable per-boot UVD intent remains consumed. No further active tests,
  reset, unload, rearm or software power command followed. Reports were saved;
  another decoder trial requires physical power-off and a fresh boot. Future
  source work adds a longer offline fixture, accurate failure reporting and
  corrected logging. An EGL vendor-file accessibility issue is a source-backed
  hypothesis, not a demonstrated hardware fix.
- Completed UART continuity: 1,805 bytes, same generation/epoch,
  04:36:41–04:45:36 local. Compact context:
  `20261105_043641_079802-exp-20261105-009-one-firefox154-hardware-avc-clip-with-immutable-f84eacbd.md`
  in sibling `ps4-uart/sessions/`. Private page, actual decoder logs/process
  maps, original results and conclusion:
  `output/ps4-diagnostix-browser-uvd1/`. The continuous logger remains running.

## EXP-20261105-010 — Firefox reaches hardware frames; capture runs out of RAM space

- The owner explicitly directed a retry on the current boot after the normal
  cold-boot requirement was explained. The original consumed intent and failed
  result remained byte-identical. A private, single-use adaptation reserved a
  separate immutable EXP010 intent and exempted only that exact preceding
  result from the history stop; all other image, firmware, prerequisite,
  sandbox and terminal-fault checks remained. The public runner's normal
  one-attempt rule was not changed.
- Selected the same verified EGL provider through a protected file under
  `/etc/psx-diagnostics/`. Also changed to the separately verified 30-second,
  900-frame MP4, SHA256
  `fe5c60408d3251e94e1b64b59610b918d51a01a747b1e3d5960e001f6ed27862`.
  Duration and selector changed together at the owner's direction; this
  does not isolate their individual causal effects.
- The page ended at 30 seconds: 900 total/presented frames and zero reported
  drops. The RDD mapped the pinned EGL provider and passed DMA-BUF texture
  creation. Saved logs contain 232 distinct VA-API output-frame messages, one
  hardware decoder initialization and no software-frame messages. The earlier
  first-frame hand-off error did not recur. No terminal kernel fault appeared.
- Aggregate remains INCONCLUSIVE: the hardware-frame log is incomplete and
  RDD libva trace writes are still denied. Read-only inspection found the
  64 MiB `/run` filesystem had only 4 KiB free; two retained Firefox profiles
  occupied about 52 MB. Multiple logs splice midline at 4 KiB boundaries, while
  archive readback matches exactly. This strongly supports failed log writes
  from filesystem exhaustion; the page alone cannot supply the missing proof.
- Future source moves browser state/capture to the existing larger RAM pool
  with preflight and low-space guards. This does not enlarge the core image.
  Completed UART continuity: 1,444 bytes, same generation/epoch,
  04:56:01–05:03:56 local. The session's original cold-boot label was retained;
  it is not evidence of a cold boot. Compact context:
  `20261105_045601_433731-exp-20261105-010-fresh-cold-boot-firefox-egl-selector-correction-c1e1c0a5.md`.
  Private reports: `output/ps4-diagnostix-browser-uvd-egl2/`.

## EXP-20261105-011 — Chromium baseline selects software for the small clip

- The owner also requested Chromium. Preserved both earlier intents/results;
  a private, exclusive EXP011 intent covered this additional same-boot trial.
  Retired Firefox profiles were copied to the existing larger RAM filesystem,
  with every regular file hash verified before removing its old RAM copy.
  Reports, logs, source copies and boot files were preserved. `/run` available
  space increased from 4 KiB to about 52 MB. New profiles/cache/logs used `/var`.
- Verified and mounted the existing optional Chromium 151 provider read-only.
  Kept its manifest unchanged; the explicit private hardware-test arguments
  removed the software-decode disabling flag and selected the source-reviewed
  Linux GL acceleration features. Namespace/seccomp-disabling flags were not
  added. Enabled CDP Media events before navigating to the single pinned clip.
- The 30-second page ended with 900 total frames, 893 presented and eight
  reported drops. CDP explicitly identified `FFmpegVideoDecoder` and
  `kIsPlatformVideoDecoder=false`, with no media error. **This was software
  playback, not a UVD decode failure or hardware PASS.** Source review found
  Chromium's default resolution-based priority prefers software below 360
  pixels high; the control is 128x96. This supplies a concrete comparison for
  the next experiment. No terminal kernel fault was observed.
- Completed UART continuity: 1,279 bytes, same generation/epoch,
  05:07:26–05:14:57 local. Compact context:
  `20261105_050726_815583-exp-20261105-011-owner-authorized-chromium-vaapi-30second-test-s-96be2506.md`.
  Private CDP traffic, provider receipt, original results and archive:
  `output/ps4-diagnostix-chromium-uvd1/`.

## EXP-20261105-012 — Chromium selects VA-API; strict acceptance remains open

- Changed only Chromium's decoder-ordering policy by adding
  `ResolutionBasedDecoderPriority` to the disabled-feature list. Kept the same
  MP4, provider, EGL selection, kernel and other browser flags. Improved process
  observation to recognize Chromium's rewritten command-line strings; this
  changes observation, not decoder policy. A separate exclusive EXP012 intent
  preserved all earlier intents/results under the owner's same-boot direction.
- CDP recorded `kVideoDecoderName=VaapiVideoDecoder` and
  `kIsPlatformVideoDecoder=true` for the target player through its ended event.
  No later software selection or media error was recorded. The actual GPU
  process mapped the pinned VA driver, SHA256
  `16d87fdff0011054eeba34e2dff9067519d8756de788c4970cecf4a7038ce4a3`.
  The 30-second clip ended with 900 total frames, 898 presented and two drops.
- Verdict: **hardware AVC path observed in Chromium**, with zero-drop and
  broader stability gates still open. The GPU process had psx credentials and
  NoNewPrivs=1, but Seccomp was 0. Its console log records sandbox initialization
  refusing a GPU process with multiple threads; other recorded processes had
  active seccomp filters. Requested sandbox flags did not establish an intact
  GPU sandbox. Root results remain INCONCLUSIVE;
  the private host conclusion records the narrower positive decoder evidence.
  No terminal UVD/GPU fault appeared. CDP captured the ended event before the
  browser's automatic stop closed the debugging connection.
- Completed UART continuity: zero bytes emitted, same generation/epoch,
  05:15:23–05:16:57 local. Silence is not decoder evidence; the CDP/process/page
  reports supply the observations. Compact context:
  `20261105_051523_516155-exp-20261105-012-chromium-decoder-priority-comparison-same-900fr-58d7fa76.md`.
  Private evidence: `output/ps4-diagnostix-chromium-uvd2/`.
  Continuous capture remains running. No broad UVD, browser or kernel stability
  promotion follows from these short, small-resolution same-boot trials.

## EXP-20261105-013 — dev2 native 1080p pixel oracle passes

- On the existing corrected alpha3/dev2 boot, the owner explicitly requested
  further stability investigation. A separate exclusive intent preserved all
  preceding intents/results; the public runner's one-attempt rule was unchanged.
  Kernel, matching modules, root, firmware and pinned Mesa remained fixed.
- One forced FFmpeg VAAPI/hardware-download decode processed the native
  1920x1080, 30 fps, 30-second AVC fixture with no software fallback. All 900
  per-frame hashes and the complete 2,799,360,000-byte NV12 output matched the
  independently generated software reference, SHA256
  `b88dd37166f35cdfaa60aa130e65cbb8ac4ba0c959d0dc8d30b74e83bde44790`.
  The actual decoder process mapped the pinned VA driver.
- Verdict: **native 1080p decode pixel-oracle PASS**, scoped to this unpaced
  component check. It does not measure browser presentation, real-time speed,
  endurance, encoding, lifecycle safety or another cold boot. The result retains
  `stop_required=true` and `hardware_acceptance=false`.
- UART session closed with completed continuity, 1,444 bytes and no continuity
  errors. Reviewed context contained no terminal UVD/GPU fault. Raw logs and
  machine-specific receipts remain private. These dev2 observations do not
  qualify a newly built kernel.

## EXP-20261105-014 — native Firefox playback hits the harness file limit

- Tested paced Firefox 154 playback of the same 900-frame native 1080p fixture
  under a separate owner-authorized intent. The fixed dev2 kernel, decoder,
  firmware and Mesa identities remained unchanged; no earlier intent was
  removed. Profile and logs used the monitored RAM pool.
- The page stopped near 19.217 seconds with media error 3. Complete RDD logs
  contain 580 successful VA-API frame outputs, ending at PTS 19.300000 seconds,
  and zero software frames. The DMA-BUF surface pool stayed bounded at eight;
  no VA-API or DMA-BUF failure was recorded. The console instead reported
  `ReadCacheFile failed` and `Sample data read failed` with `NS_ERROR_FAILURE`.
- The 26,008,330-byte MP4 used Firefox's separate file-backed media cache.
  Packet 579 ended at byte 16,752,570; packet 580 was the first to cross byte
  16,777,216, exactly the trial's `LimitFSIZE=16M`. Available RAM stayed above
  290 MB. This identifies a harness per-file limit, not storage exhaustion or
  a demonstrated UVD decoder regression. Root status remains INCONCLUSIVE;
  the page recorded one dropped frame before the incomplete playback ended.
- UART session closed with completed continuity, 2,527 bytes and no continuity
  errors or observed terminal UVD/GPU fault. The next comparison changes only
  the per-file limit; the failed result and its immutable stop intent remain.
  Raw evidence and machine-specific paths are withheld from this summary.

## EXP-20261105-015 — native Firefox hardware playback completes; one drop remains

- Changed only the trial's per-file size limit from 16 MiB to 32 MiB. Kept the
  same native 1080p MP4, Firefox, hardware decoder preferences, sandbox, kernel,
  firmware, Mesa and independent trace/total-log budgets. A separate exclusive
  intent preserved the preceding experiment under the owner's explicit scope.
- The page ended at 30 seconds with 900 total frames and 900 presented frames.
  Complete decoder logs contain 900 distinct VA-API frame timestamps and zero
  software frames. The page still reported one dropped frame; its unavailable
  corruption counter remains unknown. The earlier media-cache error did not
  recur. This controlled comparison confirms the harness file-limit diagnosis.
- Verdict: **complete 30-second native 1080p Firefox hardware playback
  observed**. Root status remains INCONCLUSIVE under the strict zero-drop and
  capture criteria. Browser output pixels were not compared with the native
  oracle, and this short same-boot observation does not establish endurance,
  lifecycle safety, encoding or stable support. The stop intent remains.
- UART session closed with completed continuity, 4,332 bytes and no continuity
  errors. Reviewed context contained Wi-Fi scan messages and no terminal
  UVD/GPU fault. Raw logs remain private. EXP013–015 apply to the tested dev2
  artifact set and cannot qualify the separate dev3 power-off candidate.

## EXP-20261105-016 — dev3 UVD decode followed by software power-off

- Booted the matched alpha4/dev3 set, kernel SHA256
  `01fa05f683c4c36ae83678f09cf3df502ddb0731dc73f2530cdb05fbe317804d`,
  whole 41-module archive `603a4b9453fba5462c702102afab335a857d01d707cf945f7a8809ec5640396c`,
  root `51e10b3de0d8a4f1645654b8c686635305f2de25e3cf2782a922d172d85e2197`
  and initramfs `805fdbab38a5ed11be094fb7886ed3bab3fd2548fae1d8e4c47c8839a03dd508`.
  The three UVD gates and experimental `ps4_uvd_poff` were observed enabled.
  Embedded/root/boot-set validation passed; no failed systemd units were found.
- CPU computation and owned-memory checks passed. One normal public-runner UVD
  attempt forced VAAPI and matched all 120 NV12 frames, 2,211,840 bytes, with
  zero mismatches and whole-output SHA256
  `d99b87567601efb735d438e766f34112344a3a31fc1d9e1b4177bfd329bcb491`.
  No terminal GPU/UVD fault appeared before shutdown. Reports and their hashes
  were copied to the host and USB writes synced before the power action.
- Ordinary `systemctl poweroff` returned success. UART recorded the explicit
  request, followed by expected shutdown closure, then
  `EXPERIMENTAL_POWEROFF retained=1 teardown=0` and `reboot: Power down`.
  The original PCI-shutdown parking marker was absent. Ethernet shutdown
  reported `stop_ack=1 retained=1 drain=unproven`; it did not free its DMA.
  The owner confirmed the power light and fans were fully off.
- Verdict: **PASS for this single canonical UVD-then-software-power-off
  sequence**. UART continuity completed without reconnect or logger restart.
  This does not qualify ordinary reboot, kexec, suspend, DMA quiescence,
  broader AVC profiles, native 1080p on dev3, endurance or stable support.
- Shutdown took about 90.86 seconds after the request; about 90.44 seconds
  preceded final systemd-shutdown. The delaying unit has not been identified.
  Finalization remounted the USB read-only and synced it, while reporting the
  still-busy loop-backed root/USB mount. Both are retained findings for the
  diagnostic shutdown flow, not hidden by the successful physical power-off.
- The owner subsequently returned to Orbis and started FTP. A separate
  read-only check confirmed the exact installed USB receipt was unchanged.
  Raw UART, private transfer journals and per-console reports remain local.
