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

## EXP-20261105-017 — UVD-disabled reboot proposal withdrawn

- The owner requested UVD and restart in the same boot. The separately prepared
  disabled-gate profile was withdrawn before USB deployment or console launch.
  No hardware result is assigned to this proposal; its host artifacts remain
  preserved. EXP018 uses the unchanged, UVD-enabled alpha4 boot set.

## EXP-20261105-018 — dev3 exact UVD decode and forced ICC restart to Orbis

- Booted the same alpha4/dev3 kernel, modules, root and initramfs as EXP016;
  all four UVD/power-off gates remained enabled. CPU and memory checks passed.
  The public UVD runner matched all 120 frames, with zero mismatches and
  whole NV12 SHA256
  `d99b87567601efb735d438e766f34112344a3a31fc1d9e1b4177bfd329bcb491`.
- Saved reports to the host, synced the USB and verified its read-only remount
  before one explicit ICC hardware-restart request from normal userspace.
  Existing retained DMA backing remained allocated. No kernel, boot-argument,
  firmware or driver-reset change was introduced for the restart.
- UART recorded the request followed by a fresh Orbis boot. The owner confirmed
  that Orbis returned. The expired SSH connection alone was not used as reset
  evidence. The bounded UART session completed, 219,445 bytes, without a serial
  reconnect, logger restart or continuity error.
- Verdict: **PASS for one exact UVD-then-forced-ICC-restart sequence**. This
  exercises a hardware restart after explicit storage preparation. Ordinary
  Linux reboot teardown, kexec, suspend, device DMA drain and repeated-cycle
  reliability remain separate tests. Private reports and raw UART stay local.

## EXP-20261105-019 — dev4 internal SATA enumeration and read-only USB-root boot

- Booted alpha5 with kernel `7.2.11-psx-ps4-baikal-uvd-runtime-dev4`, SHA256
  `9b3842def11f0638601f60ab7659eb8fa9c4c7e5cb81bab64d25f301ad826d37`;
  config `50676ba45e8535e1f7a3fd9f8eeab73fa7f841e255c1ccf846fda652d499dba0`,
  whole module archive `fcda424b74177ec2316e58187d1c1c86e6e521782094cad77a6cdcb8fdc32dcc`,
  root `f37178423962f672c4161095c14fd7870c609c818724ae690650c4ac77a61dfb`
  and initramfs `c4c8ccd5157de33456b4dd579e2fb6bd77761a29b51313f58ae281d90ecc447a`.
- Only three ATA code files changed from dev3. The four UVD/power-off gates
  remained enabled; UVD, ICC, PHY and xHCI source bytes were preserved. The
  command-owned probe was active at 5 ms on controller `0000:00:14.7`, port 0,
  with legacy polling disabled and the established 1.5 Gbit/s PHY profile.
- The 500,107,862,016-byte Toshiba MQ01ABD050 enumerated with NCQ enabled.
  The initramfs protected the disk and all observed partitions read-only
  before userspace. Linux reached the matching USB root with no failed units
  or observed ATA timeout/error-recovery cascade. A separate Sony PS-SYSTEM
  420R ATAPI device also enumerated; no optical-medium read was attempted.
- Verdict: **PASS for one scoped disk-enumeration/read-only boot**. This was
  not a data-read, write, sustained-load or internal-installation test.
  Bounded UART completed, 172,397 bytes, without continuity errors.
- Preparation findings remain distinct: FTP timed out before publishing any
  active boot file. Direct USB preparation then encountered a host-side USB
  disconnect/reconnect during final readback. FAT metadata was preserved and
  repaired; complete source hashes were verified and the drive cleanly
  unmounted before this successful console launch. These are transport/media
  findings, not Linux console boot failures. Private evidence remains local.

## EXP-20261105-020 — dev4 component checks and exact native 1080p UVD

- Same boot/artifacts as EXP019. CPU computation and 32 MiB owned-memory
  patterns passed. Six aligned direct reads of three 8 MiB HDD regions
  matched SHA256 values across both passes: 48 MiB in 2.499 seconds, about
  19.2 MiB/s. No raw disk contents were retained or internal disk data written.
- Ethernet passed bound verified HTTPS and matching-hash 16 MiB transfers in
  each direction. Observed SSH rates were 36.8/58.2 Mbit/s including connection
  setup and the host's Wi-Fi path; these are not link-capacity measurements.
- Native OpenGL and Vulkan reference pixels passed. Firefox and Chromium
  completed Canvas, hardware WebGL, animation and software/VP8 video tests;
  capability enumeration remained an observation. Native DRM Wayland produced
  three matching 1920x1080 GLES2 frames on Liverpool and restored diagnostics.
- One terminal forced-VAAPI native 1080p test matched all 900 frames against
  per-frame SHA256/MD5 and whole-output SHA256
  `b88dd37166f35cdfaa60aa130e65cbb8ac4ba0c959d0dc8d30b74e83bde44790`.
  All 2,799,360,000 output bytes were hashed in streams; no raw output video
  was retained. The 82.33-second decode/download/hash pipeline is not a
  real-time presentation benchmark. The immutable UVD stop intent remains.
- Two harness findings were preserved. Browser completion was first inspected
  before its close acknowledgement; bounded same-batch polling fixed this
  race, and the corrected pass completed both browsers. Audio never launched:
  its valid roughly 170 KiB boot manifest was silently truncated to 64 KiB
  before JSON parsing. A strict complete bounded reader was prepared for a
  fresh-boot audio experiment. No audio hardware failure is inferred.
- Verdict: **scoped component passes with an audio harness gap**. No kernel
  panic, Oops or illegal `qc_active` transition appeared in the saved journal.
  Bounded UART completed, 48,910 bytes, without continuity errors. Repeated
  cold boots, endurance, wider AVC profiles, lifecycle and encoding retain
  their separate gates. Reports and raw UART remain private.

## EXP-20261105-021 — dev4 software power-off after SATA, graphics and UVD

- Saved the EXP020 reports on the host and synced USB state before one
  ordinary software power-off request. All four UVD/power-off gates remained
  enabled. UART recorded internal-disk cache synchronization and stop,
  `EXPERIMENTAL_POWEROFF retained=1 teardown=0`, Ethernet
  `stop_ack=1 retained=1 drain=unproven`, and `reboot: Power down`.
- The owner confirmed the power light and fans were fully off. Verdict:
  **PASS for this scoped combined-use software power-off sequence**.
  Bounded UART completed, 4,683 bytes, without continuity errors.
- Request-to-power-down took about 90.94 seconds. The delaying userspace unit
  remains unidentified. USB was remounted read-only and synced; the remaining
  busy loop-backed root prevented complete final unmount/detach. These remain
  recorded diagnostic shutdown findings. No clean reboot, suspend or DMA-drain
  qualification is implied.

## EXP-20261105-022 — corrected audio manifest reader; audible check deferred

- Cold-booted the same dev4 kernel and unchanged alpha5 USB after the confirmed
  software power-off. A fresh boot ID and matching validated manifest were
  observed. This supplied a second observed dev4 boot, not a repeated complete
  graphics/UVD qualification cycle.
- Loaded the corrected diagnostic runner into private RAM, retaining the root
  catalogue and UVD adapter. Runner SHA256
  `5a7a4c4b5d1f6fa855f68f5c060a615f809dfa44fa1939b786ad54312f6cf2b2`
  read the complete 171,869-byte validated manifest. CPU and owned-memory
  checks passed with this runner's actual source identity.
- The pinned three-second stereo PCM16 fixture, SHA256
  `9fb18fc1e75587a0f5a715d5394776563fac75cfdc8af860fb1a2164a1b1b50b`,
  played through ALSA `hw:0,3` at 44,100 Hz. `aplay` returned zero in about
  3.14 seconds with no stderr, timeout, signal or output-limit event. This
  confirms that the manifest-reader correction reached and completed the
  playback command; no kernel or boot-file replacement was needed.
- The owner explicitly deferred sound verification. Audible pitch, duration
  and output acceptance therefore remain **DEFERRED**, while the raw playback
  report remains INCONCLUSIVE pending observation. No audible PASS or native
  48 kHz claim is assigned. Bounded UART completed, 254,827 bytes, without
  continuity errors. Private reports and raw captures remain local.

## EXP-20261106-001 — comparative read benchmark start inconclusive

- Same dev4 boot and artifact declarations as EXP-20261105-022. USB identity,
  the 5 Gb/s `usb-storage` link, the 1.5 Gb/s HDD link, all 16 read-only HDD
  members and same-boot CPU/RAM receipts were verified.
- The host sent the start fence but received no worker start, I/O progress or
  result. The host stopped its SSH transport at 175 seconds. Completed UART
  continuity retained 361 bytes of existing wireless scan messages; silence
  does not prove that a read started or completed. No speed or disk failure
  is inferred. Private plans and logs retain the model-name guard correction
  and connection failures separately from the attempted benchmark.
- Verdict: **INCONCLUSIVE**. Next attempt changes start control and result
  observability; it must check previous-worker quiescence before new I/O.
  UART context: `20261106_134050_963043-exp-20261106-001-readonly-usb-root-image-versus-internal-hdd-per-82eaea81.md`.

## EXP-20261106-002 — direct benchmark launch remained unacknowledged

- Removed the second network-input fence and prepared RAM-only result storage
  plus direct serial phase frames. No launch/start/result frame arrived through
  SSH or UART before the 210-second host limit. The authenticated TCP command
  was acknowledged, but its execution stage remained unknown.
- The owner reported that Diagnostix still responded. Completed UART retained
  1,167 bytes of wireless scan/P2P diagnostics, with no benchmark measurement.
  The host terminated its SSH transport; worker quiescence still needed checking.
- Verdict: **INCONCLUSIVE**, without a storage-failure or speed claim.
  UART context: `20261106_140223_265310-exp-20261106-002-direct-readonly-usb-and-hdd-benchmark-with-inde-c1bd59af.md`.

## EXP-20261106-003 — kernel-log task-state inspection

- A shell-builtin probe wrote selected Python/SSH/shell task states and kernel
  stacks through `/dev/kmsg`. Both BEGIN and END arrived through UART; SSH
  returned only BEGIN. The snapshot contained the diagnostic controller and
  SSH/shell tasks, with no previous benchmark Python worker remaining.
- This establishes snapshot quiescence of those workers and a working result
  path; it does not establish the root cause of the earlier SSH or launch stalls.
  No storage workload, driver reset or power action was performed.
- Verdict: **PASS for scoped task-state inspection**. Completed UART: 3,747
  bytes. Context: `20261106_141213_146582-exp-20261106-003-inspect-stalled-benchmark-task-state-through-sh-1cb3d529.md`.

## EXP-20261106-004 — USB and internal HDD read benchmark

- Direct launch used kernel-log phase/results instead of opening the serial
  device or waiting for a second SSH input fence. Before data I/O it verified
  previous-worker quiescence, the same-boot CPU/RAM receipts, the unchanged
  dev4 manifest and the internal read-only/no-holder/no-mount guards. All four
  UVD/power gates remained enabled; no decoder was started.
- Kernel: `7.2.11-psx-ps4-baikal-uvd-runtime-dev4`, SHA256
  `9b3842def11f0638601f60ab7659eb8fa9c4c7e5cb81bab64d25f301ad826d37`.
  The reviewed [JSON evidence](../docs/evidence/PS4-STORAGE-20261106.json)
  preserves config, modules, root/initramfs identities, offsets and exact timings.
- Sequential reads: two 256 MiB passes per device, 1 MiB userspace requests,
  `O_RDONLY|O_DIRECT`, one outstanding userspace request. The block layer could
  split requests at 256 KiB; HDD NCQ stayed enabled at depth 32. I/O timing
  excluded guard checks and report emission. Both drive caches remained intact.

| Device | Sequential passes, MB/s | 4 KiB random reads, IOPS | Median / 95th percentile, ms |
| --- | ---: | ---: | ---: |
| Transcend 16 GB USB, 5 Gb/s, `usb-storage` | 106.8 / 105.9 | 1,380 | 0.708 / 0.880 |
| Toshiba MQ01ABD050 HDD, 1.5 Gb/s | 69.0 / 73.1 | 105 | 9.678 / 15.989 |

- Random reads used eight seconds per device, 4 KiB requests and a fixed seed
  within a 512 MiB window. USB read the actual ext4 root-image file on FAT32;
  HDD read raw blocks near its middle. OS page cache was bypassed, neither pass
  is claimed cold, and no raw user data was retained or internal disk written.
- No kernel/storage fault matched the run guard. The full result was received
  as SHA256-verified UART chunks after descriptors/locks closed; the shell
  reported exit status 0. Capture continuity completed, 18,618 bytes, unchanged
  logger generation/epoch. Context:
  `20261106_141719_546039-exp-20261106-004-direct-readonly-usb-and-hdd-benchmark-with-inde-4f7f1071.md`.
- Verdict: **PASS for this short read comparison**: USB about 1.50x faster
  sequentially and 13.18x by random-read IOPS in these ranges. This does not
  qualify writes, sustained load, additional boots, installation or stability.
  Kernel/boot files and driver settings were unchanged. Private exact source,
  failed attempts and decoded reports remain under the matching ignored
  `output/ps4-storage-benchmark-20261106-00*/` directories.

## EXP-20261106-005 — Power frontend USB update and first menu boot

- Objective: update the frozen alpha5/dev4 Power frontend and observe its first
  boot/menu. Timeout: ten minutes; rollback: restore the previous initramfs,
  manifest and USB receipts, with physical power-off and normal Orbis recovery
  if the boot fails. Stop on loss of signal, a kernel fault or an unresponsive
  system. Power transitions require their own declared test after this boot.
- Host validation passed: `make check`, 678 optional diagnostic Python tests,
  15 browser host tests and 53 Rust tests. These are source/host results and do
  not qualify the new controls on hardware.
- The existing 16 GB Transcend diagnostic USB was identified through its stable
  device identity, capacity, FAT32 UUID and ownership receipt. All owned files
  matched their previous hashes. A transactional regular-file update changed
  only the active initramfs and external boot manifest, plus their installation
  receipts. Previous files were backed up on the USB and privately on the host.
  Readback passed, the filesystem was synced and unmounted before operator
  handoff. No formatting or console-internal write occurred.
- Frontend revision: `power-menu1`. New initramfs SHA256:
  `6b609667010ea1e8620cf094cc0ce5d434535397b620fb5793ec8ec717604cf8`.
  The four changed archive members are the controller, TUI and their two
  integrity manifests. Kernel, root, modules, firmware and boot arguments remain
  byte-identical to the installed alpha5 set. Kernel SHA256:
  `9b3842def11f0638601f60ab7659eb8fa9c4c7e5cb81bab64d25f301ad826d37`;
  root SHA256:
  `f37178423962f672c4161095c14fd7870c609c818724ae690650c4ac77a61dfb`.
  The four existing UVD/power gates remain enabled.
- A host scratch copy exhausted workspace capacity before any USB update and
  stopped the previous UART logger. Only this task's generated scratch was
  moved to private host temporary storage. Capture was restored and a new
  bounded session started before the first USB write; earlier completed
  experiments retain their separate original continuity records. This session
  uses capture generation `1c88d65779aa4af2ad4bb078e4988771`.
- The owner resumed after the original ten-minute update/handoff window.
  Continuous capture stayed ready through that delay; the original plan was
  retained and the late handoff recorded separately. Linux reached the UVD
  queue-ready stage, verified the USB root at 41.9 seconds and started the
  appliance at 43.6 seconds. The owner confirmed that Shutdown and Reboot to
  Orbis both appeared and that keyboard selection responded. Neither action
  was requested. SSH authenticated and returned the expected dev4 release,
  then its remaining output stalled; this does not establish a power failure.
- Verdict: **PASS for this single updated boot and Power-menu navigation**.
  UART continuity completed, 287,678 bytes, unchanged generation and epoch.
  Context:
  `20261106_155607_776676-exp-20261106-005-power-frontend-usb-update-and-first-boot-menu-v-67d5f483.md`.
  No power-transition, repeated-boot or new decode acceptance follows. The
  separately built auxiliary-memory module was not deployed; usable DDR3
  capacity and memory ownership are still unverified. Private plan, update
  receipt and rollback are under the ignored
  `output/ps4-diagnostix-power-ui-20261106/` staging reference.

## EXP-20261106-006 — wired SSH stall packet observation

- After the successful menu boot, pinned-key SSH returned the expected dev4
  release and then stalled. The owner confirmed the wired address in Network.
  A bounded host capture observed one SSH state query and two ICMP probes;
  no driver, route, boot-file or power change was requested by the host.
- The trace contains bidirectional TCP/SSH setup, followed by a client data
  range that remained unacknowledged despite retransmission and resegmentation
  from 1,268 to 1,024 and 512 bytes. The peer advertised a nonzero receive
  window. Earlier larger data segments were acknowledged, so a simple packet
  size explanation is insufficient. The first unanswered ICMP probe preceded
  the successful SSH exchange and does not establish when the stall began.
- An independent review checked the matching frozen MTS source. It uses timer
  and NAPI polling, so this trace does not establish a lost Ethernet interrupt.
  A host capture cannot distinguish console receive loss, acknowledgement/
  transmit loss or the intervening AP/switch path. Cached driver counter and
  poll-heartbeat snapshots are the next discriminator; no root cause is claimed.
- The owner reported unplugging/replugging Ethernet; its exact timing relative
  to the trace is unknown. The first subsequent short-command SSH probe also
  stalled. This was not a controlled cable-recovery acceptance test.
- Verdict: **FAIL for the observed wired SSH path; root cause inconclusive**.
  UART continuity completed with zero bytes in this observation window. Silence
  is not a demonstrated kernel heartbeat or a fault-free result. Capture context:
  `20261106_161927_834498-exp-20261106-006-observe-ethernet-ssh-stall-with-host-packet-tra-06e4d7d7.md`.
  Private packet data and reports stay under
  `output/ps4-ethernet-stall-20261106-006/`; no public stability promotion follows.

## EXP-20261106-007 — alternate Wi-Fi observation of wired stall

- Objective: inspect the retained wired generation through an independent
  Wi-Fi SSH path. The owner leaves Ethernet attached, connects Wi-Fi in Network
  and supplies its address. The observation limit is three minutes after that
  address is ready. Stop on an unresponsive menu or kernel fault; close host
  observation on timeout and preserve the wired generation without reset/unload.
- Continuous capture was ready and the bounded session started before the
  requested Wi-Fi connection. UART recorded association and the owner confirmed
  Wi-Fi connected. Pinned-key wireless SSH completed and verified the deployed
  controller and TUI hashes against the staged Power update. Both Power controls
  reported available; no power operation was requested and no auxiliary-memory
  module was loaded.
- Two cached MTS reports, 1.21 seconds apart, showed advancing poll timestamps,
  two further received packets, no pending TX and no driver error or terminal
  fault. Driver RX drops stayed zero while the network-core drop count rose.
  Independent source inspection confirms these are separate counters; this is
  not evidence of hardware CRC loss.
- NetworkManager recorded a software device-disconnect at kernel time 726.9
  seconds. The resulting absent wired IPv4 address explains current old-address
  reachability, but occurred after EXP006 and cannot explain that earlier stall.
  The wired engine, original DMA generation and first publication remained live.
- Verdict: **PASS for alternate-path inspection**; the original wired SSH
  failure remains unresolved. UART continuity completed, 6,531 bytes. Context:
  `20261106_162251_169414-exp-20261106-007-inspect-ethernet-stall-through-wi-fi-ssh-15f1331e.md`.
  Private snapshots remain under `output/ps4-ethernet-stall-20261106-007/`.

## EXP-20261106-008 — wired IPv4 restoration and asymmetric SSH observation

- Objective: activate the disconnected wired connection through Diagnostix,
  keeping wireless inspection access and the same retained MTS generation.
  Read-only policy-routing inspection returned Operation not supported before
  any routing or connection mutation. Scoped Wi-Fi link-local IPv6 SSH then
  supplied independent control; no temporary policy rule/table was installed.
- The Diagnostix Connect Ethernet job completed successfully and restored the
  DHCP address. First-publication count, admin-open count and all four DMA
  extents stayed unchanged. A paired console packet tap and host capture
  observed one successful short SSH command.
- This exchange used wired ingress and wireless replies: 22 TCP frames were
  visible at the console's Ethernet tap, incoming checksums were valid, and MTS
  RX advanced by 42 while TX advanced by zero. The success therefore does not
  qualify wired transmission or explain the original failure.
- Verdict: **PASS for IPv4 restoration and the observed asymmetric exchange**.
  UART continuity completed, 99 bytes. Context:
  `20261106_163232_083166-exp-20261106-008-reconnect-wired-ipv4-and-trace-one-ssh-probe-3f0046ec.md`.
  Private connection job, paired observations and setup limitations:
  `output/ps4-ethernet-reconnect-20261106-008/`.

## EXP-20261106-009 — fully wired SSH reproduces outbound loss

- Changed variable: one temporary main-table host route forced laptop IPv4
  replies through the wired device. Scoped wireless IPv6 retained control.
  The route was verified before a single paired-capture SSH query; the declared
  observation bound was 65 seconds after setup. The exact host route was removed
  and its absence verified afterward, restoring the original wireless routing.
- The query delivered a complete identity/Power-state JSON result but SSH then
  timed out rather than closing successfully. The console tap recorded 27
  incoming and 37 outgoing TCP frames with valid IPv4 and TCP checksums. MTS RX
  advanced by 49 and completed-TX accounting by 38; pending TX ended at zero,
  with no driver error and the same retained DMA generation.
- Small outgoing acknowledgements and retransmitted SSH control/data frames
  appeared at the console tap but not at the laptop tap. Host GRO can complicate
  exact matching of large segments; it does not explain all missing small
  control frames. This narrows loss to the path after console packet preparation
  and before host capture, without identifying MAC/PHY, switch/AP or host-radio
  responsibility. A console packet tap is not an external wire observation.
- Independent frozen-source review found no concrete TX copy/padding or length
  bug. Completed-TX accounting checks returned descriptor ownership, not physical
  transmission; no MAC-error interpretation establishes on-wire delivery. Zero
  pending descriptors and valid software checksums do not qualify the link.
- Verdict: **FAIL for a complete wired SSH session; root cause unresolved**.
  UART continuity completed, 99 bytes. Context:
  `20261106_163843_057044-exp-20261106-009-inspect-forced-wired-ssh-exchange-with-wi-fi-ip-29bc5c6c.md`.
  Private paired traces, route cleanup and analysis:
  `output/ps4-ethernet-duplex-20261106-009/`. Wireless SSH remains the inspection
  path. No driver reset, unload, rearm, power transition or stability promotion
  occurred. A link-layer destination check and a wired-side capture/peer are
  the next discriminators; no speculative driver patch was installed.

## INC-20261106-001 — unexplained power-off during auxiliary-memory preflight

- The owner requested a DDR3 check while the same dev4 boot remained active.
  Wireless SSH returned the expected release/boot identity and cached PCI IDs
  for Baikal DMAC `104d:90dc` and MEM `104d:90dd`. No auxiliary-memory module
  was loaded or transferred, DMA submitted, power/reboot request sent, or boot
  file changed by this check.
- A subsequent Python preflight opened each function's PCI `config` file with
  buffered binary I/O and requested 256 bytes, then intended to parse standard
  capabilities/resources and print one final JSON. SSH timed out with zero
  stdout. The exact function, offset and execution stage are unknown. Buffered
  I/O may request more underlying bytes; the actual bus-access range was not
  measured and must not be described as a proved 256-byte-only scan.
- The owner confirmed the console was fully off, with fans and power light
  stopped. The preserved Linux UART segment ends with a keyboard disconnect at
  4,772.747 seconds and a NUL byte. It contains no SBRAM load, panic, UVD
  teardown or normal Powerdown marker. Continuous generation remained
  unchanged; the incident slice ends at byte 297,108 before later Orbis output.
  No bounded driver experiment had begun, and no retroactive experiment or
  hardware acceptance is assigned to this unexpected event.
- The owner connected the identified diagnostic USB to the host. Read-only
  inspection parsed 320 result JSON files without a read error. No shutdown/
  reboot request or matching boot power intent was found. The one historical
  `power.off` record was BLOCKED and belonged to November 3. This is no evidence
  that the new Power-menu dispatch ran; it does not exclude other causes.
- Source review confirms PCI sysfs configuration reads access hardware and
  perform runtime-PM handling. Linux warns about undefined-configuration reads
  locking up some devices. No specific hazardous Baikal offset or static
  configuration-read-to-ICC-power-off path was established. The read is a
  possible suspect, not a demonstrated cause; an SSH timeout alone cannot
  distinguish transport, runtime-PM, locking and hardware access.
- Conclusion: **unexplained abrupt power event; DDR3 remains untested**. Do not
  repeat the broad configuration scan. Subsequent discovery uses kernel-cached
  metadata; matching-module preparation continues offline. Original command
  evidence, preserved UART bytes/events and USB reports remain private in
  `/var/tmp/psx-ddr3-live-20261106/`. No module load, DMA or power acceptance is
  claimed from source review or a build.

## EXP-20261106-010 — cached auxiliary-memory discovery and normal removal

- The owner authorized another test after INC-20261106-001. Changed variable:
  default `psx_sbram` binding with cached preflight replaces the broad PCI scan.
  Objective: inspect cached metadata, verify the matching dev4 identity and
  cached D0/runtime state, load with `arm=0`, query the protected information
  endpoint and normally unload after confirming no DMA entry. Capacity and
  ownership remain unverified; no host-copy, DDR3 access or reset is included.
- The exact dev4 source archive, original config, generated header and retained
  symbol exports were restored into a separate host tree. Missing host objtool
  and host `/tmp` quota failures were preserved as preparation/build failures;
  preparation with the task temporary directory then preserved all three
  original config/symbol/header hashes. Matching module build 03 passed `W=1`
  without warnings or errors using 16 jobs. Module SHA256:
  `f44efe4e05c038623dd2b7b11558cd965c029b3bc704ded216134a17e17de9b6`;
  vermagic: `7.2.11-psx-ps4-baikal-uvd-runtime-dev4 SMP preempt mod_unload`.
  Earlier native-dev3 binaries are not substituted or relabeled.
- The new cached inspector passed ten fixture tests, including an explicit
  file-open allowlist and rejected PCI configuration/BAR/control access. Source
  SHA256:
  `dd02793f4bc5a72486d3782f2dc1ec31882a7168f4035b1037ae5d343f91fede`.
  PCI-core binding still performs standard IRQ/runtime-PM bookkeeping; a
  cached-layout match does not itself establish safe DMA or physical capacity.
- Continuous UART was ready and one bounded session began before USB staging.
  A separate optional 500,205-byte kit, including the matching module, tool,
  inspector, pinned source and notices, was copied with readback verification to
  `psx-diagnostics/experiments/ddr3-dev4-discovery1/`. Every existing owned boot
  file matched its previous hash. No kernel/initramfs/root/modules archive or
  boot arguments changed; the kit has no autoload. The USB was synced and
  safely unmounted before handoff.
- Operator sequence: move USB to PS4, launch the same BIN, connect Wi-Fi and
  report its address; leave other hardware/graphics/power tests idle. Observation
  limit: ten minutes after launch, then three minutes after Wi-Fi is ready and
  30 seconds for the module phase. On terminal fault stop; never force-remove,
  reset or retry. Recovery is physical full power-off and normal Orbis recovery.
  Normal unload is allowed only for confirmed unarmed/unentered discovery.
- The owner launched a fresh dev4 boot and connected Wi-Fi. Cached inspection
  found both expected functions unbound, D0, and the expected BAR sizes. Exact
  controller/manifest identities matched. `CONFIG_PM` is disabled in this
  kernel; no runtime-PM transition is inferred from the generic PCI wrappers.
- The matching module loaded with `arm=0`, returned ABI1 information and normally
  unloaded. All three commands returned zero in a 0.335-second module phase.
  Armed/entered/quarantined flags and verified-copy/capacity/owned fields stayed
  zero. The cached aperture was 1 GiB, which is not a physical-capacity result.
  Power controls refused while the module was present and became available
  again after removal. The out-of-tree-module taint is expected and persists
  after unloading; it is not a kernel WARN or a support qualification.
- Host probe preparation overran the overall launch/post-Wi-Fi timing budgets;
  that overrun is retained rather than presented as a complete qualification
  plan pass. The actual module phase stayed within its 30-second bound.
  Verdict: **PASS for one observed default discovery/query/removal lifecycle**,
  with no DMA or DDR3 acceptance. UART continuity completed, 129,394 bytes:
  `20261106_175207_742341-exp-20261106-010-matching-dev4-auxiliary-memory-cached-discovery-b9863cff.md`.
  Private plan, USB readback, result and source kit remain under
  `/var/tmp/psx-ddr3-live-20261106/`; matching build receipts remain in ignored
  `output/ps4-sbram-implementation-20261106/build-dev4-03/`.

## EXP-20261106-011 — owned-buffer DMA request refused before entry

- The owner explicitly requested the next test. Changed variable: `arm=1` and
  one fixed 4 KiB host-buffer copy request, after the completed default lifecycle.
  The declared DMA timeout was one second, module-phase bound 30 seconds and
  host bound 38 seconds. Preentry refusal permits normal removal; entered or
  unknown state retains backing/code and requires full physical power-off.
- Added named refusal logs without relaxing any gate or adding DMAC accesses.
  Independent review confirmed the same ordered short-circuit register reads
  and unchanged ownership/reset policy. Matching build04 passed `W=1`; module
  SHA256 `646095e73594c56841df27078c5c755e048e02a70a83692992c7e37c7499dd27`.
- The module loaded armed but unentered. Its single copy request read the
  standard PCI command word through the kernel API and refused with `EBUSY`:
  inherited command `0x546` already has bus mastering enabled. This does not
  prove an active DMA channel; the function-wide state lacks a verified handoff.
  The refusal occurred before DMAC BAR mapping, IRQ or DMA allocation, engine
  writes and publication. No broad PCI configuration scan was performed.
- Information flags confirmed entered=false and quarantined=false, zero IRQ
  cause/elapsed/copy bytes and no DDR3 access. Normal module removal returned
  zero; Power controls became available again. The module phase took 0.253
  seconds and the console stayed reachable. No physical recovery was needed.
- Verdict: **BLOCKED at the inherited-state admission gate; no DMA copy ran**.
  Do not clear bus mastering or retry to bypass that finding. The next work is
  an explicit controller handoff, plus independently verified capacity and DDR3
  ownership. UART continuity completed, 1,849 bytes:
  `20261106_182327_677558-exp-20261106-011-one-gated-owned-host-buffer-dma-self-test-2c26260c.md`.
  Private plan, full report and exact build inputs remain under
  `/var/tmp/psx-ddr3-live-20261106/` and the ignored build04 directory.
- A subsequent read-only `/proc/meminfo` snapshot reported 7,259,975,680 bytes
  of Linux-managed main RAM (6.761 GiB), with 6.327 GiB available. These are main
  memory totals, not auxiliary DDR3 capacity. The auxiliary aperture remains
  1 GiB and verified physical/owned DDR3 capacity remains unknown/zero.

## INC-20261106-002 — pending reboot report and reported UI stall

- During offline DDR3 loader preparation, the owner selected Reboot to Orbis
  and reported a persistent pending message, followed by a Diagnostix stall.
  The agent had not issued a power command or changed the running boot set.
  SSH inspection attempts at both last network addresses timed out.
- The owner connected the identified diagnostic USB to the host. Its exact
  current-boot intent and report, run `20261106T125705Z-2579d04f2ff4`, record
  `power.reboot-orbis.request`, armed=true, phase completed and INCONCLUSIVE.
  Both retain the pre-dispatch pending message and no dispatch-state field.
  The RAM-only final outcome was unavailable after recovery; the saved pending
  message does not establish that a command remained queued or could be killed.
- Continuous UART shows ordinary systemd shutdown, the UVD retained-backing
  shutdown notices, busy FAT/root-loop cleanup and `systemd-shutdown: Rebooting.`
  Later output shows Orbis running. The initiating path for ordinary restart,
  exact stall point and whether physical full-off was required remain
  unestablished. Do not infer successful forced-ICC restart from those bytes.
- Source review identified a separate display race: the power preparation job
  becomes completed before its after-save dispatcher finishes, while the TUI
  stops polling that completed job. This can retain the initial pending message
  after the dispatcher updates its outcome; it does not explain the whole
  observed reboot sequence by itself.
- The source visibility fix now carries the bounded identity-matched outcome
  in state and reconciles only the last matching power report/notice. It does
  not alter hardware commands, cancellation or once-per-boot guards, and is
  deliberately not deployed with the separate capacity-loader experiment.
- This operator-initiated incident has continuous evidence but no bounded
  acceptance session; no retroactive experiment or power acceptance is assigned.
  The report/intent were preserved, not deleted or retried. New boot IDs use
  separate intents. Private USB records, exact UART slice/bounds and hashes:
  `/var/tmp/psx-ddr3-research-20261106/INC-20261106-002/`. DDR3 capacity remains
  unknown; the separately built capacity-only loader has not been launched.

## EXP-20261106-012 — native DDR3 capacity witness and Linux handoff

- Objective: capture the native firmware's DDR3 byte-capacity cache before
  Linux handoff. The sole diagnostic variable is a separately built loader
  helper. The existing dev4 kernel, initramfs, root image, module set, arguments
  and VRAM selection stay in place. Capacity is separate from ownership/drain.
- The exact 12.02 ELF getter/initializer and SBRAM media-size use were verified
  in direct IDA and bounded raw analysis. Static fingerprints reproduce from
  the pinned ELF. Portable synthetic ASan/UBSan tests pass; a separate private
  production-profile test passed ten cases using actual text windows and an
  injected cache, including a uniform slide and corrupted code. Injected cache
  values are not hardware measurements.
- A full native BIN was built from clean upstream loader commit
  `9acef9fbf79097a2bb39d6c9c17228198bc445cc` in a fresh isolated tree. Its 316,736
  bytes hash to `bd2323f21f71e5ac235333764a8bc5e75509e0d59f1a39f5db0d6cef9a1866a8`.
  Original/copied build inputs and the unchanged reference checkout were
  verified after the build. Independent source review found no concrete runtime
  blocker within the declared trusted-native-kernel reader model. This is
  host preparation, not hardware acceptance.
- Continuous UART was READY, generation `1c88d65779aa4af2ad4bb078e4988771`, epoch 1;
  one bounded session began at byte 699218 before staging. The complete short
  sequence is stage/read back the distinct BIN, return the unchanged USB to
  PS4, prepare Orbis FTP/verify the distinct Payload Guest entry, launch once,
  capture the witness and observe Linux. Bounds are two minutes after launch
  for native handoff and ten minutes for Linux/menu/network observation. Stop
  at refusal, terminal fault or timeout; keep the original loader and use the
  usual full physical power-off/Orbis recovery if required. No module/DMA test
  is part of this sequence.
- The identified FAT USB UUID `4F18-D03E` received a separate optional kit at
  `psx-diagnostics/experiments/ddr3-capacity-witness1/`: BIN, exact build receipt,
  complete loader/helper source and notices, with readback hashes. Total kit
  size is 3,759,140 bytes. Writes were confined to the new directory; protected
  boot-file metadata and small-file hashes remained unchanged. Large root and
  initramfs files were not unnecessarily reread in full. The USB was safely
  unmounted after staging.
- Orbis FTP subsequently received the distinct full payload as
  `/data/payloads/linux-psx-ddr3-capacity.bin`. Temporary upload and final-path
  readbacks matched the expected SHA256; the USB kit BIN matched as well. The
  existing `linux-psx-diagnostics-usb-v25.bin` was privately backed up and
  verified unchanged. USB boot arguments and VRAM 1024 selection were checked
  unchanged. No payload was launched by the agent.
- The owner launched the distinct payload once. UART captured exactly one
  `PSX_SBRAM_CAPACITY_V1` record: phase pre_shutdown, firmware 1202, southbridge 3,
  status 0, **capacity 268,435,456 bytes / 256 MiB**, ownership 0 and drain false.
  Both native code fingerprints passed before the single cache read. Witness
  global raw byte range is 748756..748880; reviewed line SHA256:
  `bbed085168af480c5b3a243abe348fe98ceff35478da062e0638a6204022b753`.
- The same dev4 kernel booted, verified the read-only USB root at 39.771 seconds
  and began diagnostics at 41.414 seconds. The owner confirmed the menu appeared
  and responded within the declared launch observation limits. No auxiliary
  module, DDR3 aperture read/write or auxiliary DMA was run. The boot retained
  the existing FAT unclean-volume warning and WLAN initialization/scan messages;
  their absence is not assumed and no wireless/storage stability claim follows.
- Verdict: **PASS for one firmware-reported 256 MiB capacity witness and
  Linux/menu handoff only**. The 1 GiB PCI aperture remains a different value.
  Physical-address aliasing, a Linux-owned range, auxiliary DMA, swap, bandwidth
  and stability remain unqualified. UART continuity completed: 143,404 bytes,
  generation unchanged and no reconnect/restart. Context:
  `20261106_190535_452482-exp-20261106-012-capacity-only-ddr3-witness-with-unchanged-dev4--91ca6a7b.md`.
  Private exact witness, result, plan, source/build and staging receipts:
  `/var/tmp/psx-ddr3-capacity-loader-20261106-02/`.

## EXP-20261106-014 — Power progress staging; restart trace not reached

- Objective: install `power-status2`, verify its first boot, then trace one
  confirmed Reboot to Orbis request. The changed variable is frontend power
  reporting; power commands, dev4 kernel, root, modules, arguments and all four
  UVD/power gates are preserved. The earlier physical hang remains unexplained.
- Plan: 150 seconds for staging, ten minutes for operator handoff, ten minutes
  from launch for boot verification and 120 seconds after the single restart
  confirmation. Keep the menu idle through staging, recover to Orbis by the
  usual full physical power-off procedure, launch the same USB diagnostic BIN,
  and wait for boot verification before selecting Power. Stop on an I/O/hash
  fault, UART continuity break, kernel fault, no signal or failed transition.
  Do not retry a failed power request. Rollback is the verified previous
  initramfs, manifest and receipts through recovered Orbis FTP or USB on host;
  incomplete staging requires state inspection/restoration before any boot.
- Host checks passed: `make check`, 682 optional diagnostic Python tests,
  15 browser host tests, 55 Rust tests and 25 focused power fixtures. The latter
  include responsive state/job queries while preparation/final flushes block.
  These checks do not establish a physical power fix.
- SSH inspection found boot `4b933fcf-2cd5-4803-8bea-3b9cc70fdcd8` responsive,
  no current power intent and the old `power-menu1` frontend. Every owned USB
  file matched its existing receipt. Under one continuous UART session, the
  updater verified serial/capacity/UUID, held the shared nonblocking runner
  lock, durably saved old files and replaced only the initramfs and external
  boot manifest plus their two ownership receipts. Final readback and sync
  passed. No root image, kernel, module, arguments or internal boot file was
  replaced, and no power command or auxiliary-memory module was executed.
- Staged initramfs SHA256:
  `7d45ee7a4a69e6d7cc13dd6f1086776733e4dc7cdbd82cd4410857665f26daa2`.
  Controller SHA256:
  `6c9aa99f731aaab58dd0f2a5d34062808dd8a00f2ce1b0cb9386a36c98b3d790`;
  TUI SHA256:
  `18cdd07f73001bcf77790532ea2d15e421e8163c1d394e2d99f94b4e1d5c7873`.
  The four changed CPIO entries are controller/TUI and their integrity tables.
  Backup: `/run/psx-media/psx-diagnostics/backups/EXP-20261106-014/`.
- **Staging verified; new boot and Power-menu restart not observed.** The continuous
  capture generation is `1c88d65779aa4af2ad4bb078e4988771`, epoch 1, start byte
  850028. The session closed after the original ten-minute handoff window.
  Capture continuity completed through byte 1181259: 331231 bytes, unchanged
  generation/epoch and zero capture events. The owner reported a tty2 reboot
  during handoff; UART showed WLAN disconnect/IRQ messages followed by Orbis
  boot. The exact command and any physical-button involvement were unconfirmed.
  No instrumented Linux boot, new Power-menu request or physical-hang fix was
  established; the power objective remains inconclusive. Completed continuity
  supports the retained observation, not an unperformed acceptance test.
  Private plan, build/check receipts, transaction output and host rollback:
  `/var/tmp/psx-power-hang-20261106-01/`.

## EXP-20261106-015 — Verified boot; restart rejected before ICC ioctl

- Objective: boot the verified `overview-power3` frontend and locate any hang
  in one confirmed Reboot to Orbis request. The current kernel, root image,
  modules, firmware and all four UVD/power gates remain unchanged. Auxiliary
  DDR3 work is parked and no auxiliary module is loaded or probed.
- Source fixes: initial/final job directory creation and durable saves no
  longer hold the status mutex. One active reservation remains until saving
  completes; a failed save never starts work or dispatches power. Six blocked
  storage/durability fixtures passed. Existing exact Power guards and commands
  are unchanged. Physical power behavior is not established by these tests.
- Overview follows network setup and uses cached current-boot functional
  receipts, with detection kept separate from test results. F7 opens Advanced;
  navigation starts no test or power request. A bounded overview result map
  reuses the existing report read without losing CPU/RAM checks after the
  compact thirty-report history fills. Missing data and previous boots cannot
  create a current PASS.
- Host checks passed: 692 diagnostic Python tests, 63 Rust tests, 15 browser
  host tests, kernel `make check`, source receipts and clean whitespace checks.
  The four changed CPIO entries are controller/TUI and their integrity records.
  Initramfs SHA256:
  `264c716d864c4084b7aa085a055cd026b5197161fc6b8aae3ab51ea4027cb877`;
  controller:
  `10dedacadde290046215f297c56f29e969e13242fe2527d1ae8e297cc5e81edb`;
  TUI: `b77fdea1d10c707feb6da3e7436fbcff533ea17a4c18f793aab0bf3d6615749b`.
- Orbis FTP readback verified the prior installed kernel, initramfs, arguments,
  metadata and normal loader before update. Under continuous UART the narrow
  FTP updater backed up the previous small files, staged and verified new
  bytes, then published only initramfs/boot metadata and the two ownership
  receipts. Final readback passed. No root-image transfer, kernel change,
  internal boot-file write or payload launch was performed by the agent.
- Plan: ten minutes for operator handoff/boot observation and 120 seconds
  after the single restart confirmation. Launch the verified ordinary
  `linux-psx-diagnostics-usb-v25.bin`, connect Wi-Fi, report the IP and leave
  Power unconfirmed until boot identity is verified. Then use Advanced → Power
  → Reboot to Orbis once, observe without power-button intervention and report
  the physical result. Stop on a fault or deadline; no retry/reset/unload.
  Recovery is normal full physical power-off/Orbis, with verified old frontend
  files retained in the host FTP backup if restoration is needed. Shutdown is
  a later fresh-boot experiment after this restart trace is concluded.
- **FTP staging verified; new boot and power outcome pending.** UART remains
  ready, generation `1c88d65779aa4af2ad4bb078e4988771`, epoch 1, start byte
  1192703. This bounded session remains open through the operator handoff and
  power observation. No hardware acceptance is assigned yet. Private plan,
  exact source/build/check receipts, transfer journal and rollback:
  `/var/tmp/psx-power-overview-20261106-01/` and
  `/var/tmp/psx-power-test-20261106-015/`.
- Conclusion: the owner supplied Wi-Fi `.125`; the fresh boot, embedded root
  contract, new controller/TUI and effective power eligibility were verified.
  IPC responded in 0.139 seconds. One root API restart request completed report
  preparation, final job save, sync and USB read-only remount, then failed
  opening `/run/psx-diagnostics/power-icc` with EACCES13. `/run` was confirmed
  `nodev`; `/dev` was writable devtmpfs. Source ordering establishes that this
  failure preceded the ICC ioctl. No retry, driver reset/unload, writable
  remount or intent removal occurred. Software report flushes took 0.647/0.889
  seconds; the storage-sync and remount steps returned in 0.031/0.039 seconds.
- A second defect hid the error: deployed runner1738 implements the legacy
  one-argument `read_json(path)`. The Power outcome lookup passed a second
  argument, caught its TypeError and returned no current request. The initial
  pending report therefore remained visible. Both defects are corrected in the
  next controller candidate; source fixtures alone do not qualify its power path.
- Current Ethernet observation: DHCP assigned `.95` successfully at boot, then
  a recorded Disconnect Ethernet action removed it before Wi-Fi connected.
  Cached MTS state retained one running generation without pending TX or a
  terminal fault. This explains the current missing wired address; the prior
  established-session packet loss remains unresolved and needs separate capture.
- UART continuity completed: 118852 bytes, byte range1192703..1311555,
  unchanged generation/epoch and zero capture events. Context:
  `20261106_210916_875879-exp-20261106-015-power-job-lock-fix-and-overview-boot-followed-b-8a0b5bc4.md`.
  Verdict: **boot/identity and responsive job processing observed; restart FAIL
  before the firmware command**. No physical restart, shutdown or stability
  acceptance follows. Exact failure, ABI trace, phase timings and result are
  saved privately under the matching experiment directory.

## EXP-20261106-016 — Corrected Power frontend staged; live restart not run

- Objective: load the controller fixes for the confirmed ICC nodev and legacy
  JSON-reader failures, then observe one fresh-boot restart. This changes only
  the controller overlay and integrity metadata; the TUI, kernel, root image,
  modules, firmware, arguments and UVD/power gates remain unchanged.
- Host verification passed: 705 diagnostic Python tests, 15 browser tests,
  kernel source checks and focused device/compatibility regressions. The TUI
  remains the same 63-test build. The packaged initramfs changes only its
  controller and two integrity records. Initramfs SHA256:
  `07e8821233fa541f733906e64bf8e86c4d670a9209e8ccc0481066de6abc567c`;
  controller SHA256:
  `f5cdf55ef60d903e3a13b1595547975549a787686b6a5ba8d286bb0f228aae11`.
- The console remained in Diagnostix at Wi-Fi `.125`, not Orbis. An attempted
  read-only FTP readiness check therefore supplied no update path. The owner
  corrected that state assumption. Live SSH verified the existing boot, exact
  saved failed-before-ioctl error, USB serial/capacity/UUID and every owned
  file hash. The failed Power intent and RAM outcome were preserved verbatim;
  no second power request, intent removal or driver operation was performed.
- With continuous UART and the exclusive runner lock, the narrow updater
  temporarily remounted only the diagnostic FAT USB writable for the authorized
  boot-file update. It durably backed up the old small files and request,
  replaced only initramfs/boot metadata plus their ownership receipts, verified
  readback, synced and returned the USB to read-only. The root image remained
  mounted read-only throughout. Both failed Power records were byte-identical
  at completion. This remount was for boot-file staging, not saving an outcome.
- **SSH staging verified; the live process still runs the old controller.**
  A fresh boot is required to load the corrected files and establish a new
  per-boot Power state. Sequence: keep the USB attached, use normal full
  physical power-off recovery to Orbis, launch the same ordinary diagnostic
  BIN without another FTP update, connect Wi-Fi and report the IP. Verify the
  fresh boot before one restart request; observe up to120 seconds without a
  physical button. Stop on a fault/deadline and do not retry. Ten-minute handoff
  and boot windows apply after the verified update/launch. Previous files are
  retained in host/USB backups for recovery.
- After staging, a direct corrected-helper recovery was prepared at the owner's
  request. SSH failed before that helper could execute; no second restart
  command or ICC ioctl was sent. The owner then confirmed the PS4 was fully
  powered off. That observation is recovery state, not software power-off
  acceptance. Leave the console off while the main kernel power path is fixed.
- UART continuity completed, generation
  `1c88d65779aa4af2ad4bb078e4988771`, epoch1, bytes1314554..1314764
  (210 bytes, zero capture events). Compact context contains a FAT unclean-volume
  warning and an unhandled interrupt message; it contains no successful power
  transition. **Result: boot-file staging verified; corrected live helper and
  fresh-boot restart not run; no new hardware power acceptance.** Exact SSH plan,
  identity/hash proof, readback transaction and host rollback:
  `/var/tmp/psx-power-device-fix-20261106-01/`; hardware plan:
  `/var/tmp/psx-power-test-20261106-016/`. Completed session:
  `20261106_215325_579826-exp-20261106-016-correct-icc-frontend-usb-update-over-ssh-and-fr-9f0895dc`.

## EXP-20261106-017 — PASS: normal kernel reboot after exact UVD decode

- Objective: one ordinary Linux restart to Orbis with healthy UVD enabled.
  The owner confirmed the PS4 fully off, then connected the existing diagnostic
  USB to the laptop. No further command ran on the previous Linux boot.
- Source changes: main native Baikal dev4 now has 74 ordered patches, adding
  a separate atomic ICC restart follow-up without modifying suds-mcduff's
  original PR #4 patch. The final Baikal path no longer enters a sleeping
  command transport or resends through the legacy fallback. A separate UVD
  patch permits only the opted-in, started, healthy runtime to retain backing
  through ordinary restart; the existing power-off helper remains unchanged.
- Combined artifact: `7.2.11-psx-ps4-baikal-uvd-runtime-dev5`, alpha6. Full
  native compilation with 16 jobs took 375.473 seconds. All 41 matching modules
  and corresponding source/module archives passed readback. The new image
  contains the matching root module tree and updated controller/UVD adapter;
  all four existing UVD/power gates stay enabled. Reboot uses `systemctl reboot`
  and cannot fall back to the userspace ICC ioctl on error. Earlier job-lock,
  outcome-visibility and device-path fixes are preserved.
- Kernel SHA256:
  `4b628855f41d5740609607fdac528f189ba3c056e31791ab69da746c51f3c6f8`;
  config SHA256:
  `0b62a6638da83e415aab58e2164a35aa1aebca60b77a6763812abc230e12bfb2`;
  whole-module archive SHA256:
  `5dcd3e005999df31066870cafab2d8fe6f064fec8b8f3029c64fd162148153cc`.
- Root image SHA256:
  `3086be31d980b3afc6f9423083399bda0a900b694318403cf437e477449ba975`;
  initramfs SHA256:
  `a05e0b9fd0891a8cf6146be112d0319f1b82de8ea4a6912686109217f24b0e72`.
  Total core boot set: 1,066,291,318 bytes, within the 1 GiB limit. Host checks:
  all 74 patches apply to the pinned upstream files; 51 kernel/release tests,
  714 diagnostic Python tests, 15 browser tests and 63 TUI tests pass.
  Actual-source UVD CPU checks passed 65,536 cases with ASan/UBSan.
- USB identity, capacity, UUID, ownership and existing file hashes passed the
  read-only plan. Update includes a backup, atomic replacement, hash readback,
  sync and safe unmount; no format, repartition or internal-console write.
  All 19 owned files passed staging/readback; the prior boot set is preserved
  under a dated USB backup. Unmount returned success before handoff; the operator
  then removed the media before a later read-only device check. No new power
  command has run.
- Planned sequence: reconnect USB, launch the same ordinary diagnostics BIN
  once, verify the fresh exact boot and Wi-Fi SSH, run one canonical UVD oracle,
  then submit one normal restart after healthy PASS. Observe Orbis return for
  up to 120 seconds without physical-button intervention. Stop on a boot/UVD
  fault, uncertain power state, UART discontinuity or deadline. Staging has a
  240-second window; handoff 600 seconds, boot 180 seconds, UVD 120 seconds.
  Physical full power-off and the preserved prior boot files are rollback.
- Continuous UART was READY; bounded session started with generation
  `1c88d65779aa4af2ad4bb078e4988771`, epoch 1, byte 1314764. Private
  plan and staging receipt: `/var/tmp/psx-power-kernel-test-20261106-017/`.
  Artifact/readback: `/var/tmp/psx-diagnostix-alpha6-power-20261106-01/` and
  `/var/tmp/psx-diagnostix-alpha6-power-20261106-01-FINAL-READBACK.json`.
- **Result: one normal reboot passed after canonical UVD decode.** Fresh boot
  `6f30b822-acb5-495b-ba31-5691993144e0` matched all kernel/root/frontend pins
  and effective gates. The actual UVD oracle matched all 120 reference frames;
  run `20261106T164006Z-bb8e12a455d8`. Both Power actions were available after
  the saved PASS. One `power.execute` request used the ordinary `systemctl
  reboot --no-block` path, confirmed again by its durable USB report after
  returning to Orbis. No userspace ICC ioctl or command retry ran.
- UART recorded the healthy UVD retained-restart handoff at 526.974 seconds
  and atomic `PS4_ICC_RESTART submitted` at 527.030 seconds, followed by Orbis
  boot. The owner reported "rebooted to orbis", then "ftp ready", during the
  predeclared hands-off observation. FTP read-only recovery verified that the
  canonical UVD PASS, Power request and exact alpha6 boot identity survived.
- A 90.343-second wait remains between systemctl acceptance and final
  systemd-shutdown. From the latter's first message to ICC submission took
  about 0.418 seconds. Busy loop/mount cleanup messages occur after the long
  wait and complete in under a second; they do not explain that wait. The
  userspace stop job still needs identification. The responsive preparation
  and dispatcher returned; this was not a held diagnostic job mutex.
- Continuity completed: 375,375 bytes, range 1314764..1690139, zero capture
  events. Session:
  `20261106_222847_735191-exp-20261106-017-combined-kernel-power-fix-usb-update-boot-and-o-aecdd9cc`.
  This is scoped one-run reboot evidence on the exact candidate, not repeated
  stability or other-model qualification. Exact dev5 software Shutdown is next
  on a fresh boot; no USB update is needed.

## EXP-20261106-018 — PASS: UVD plus software power-off; stop delay identified

- Same dev5/alpha6 kernel and boot files as EXP017, freshly launched from
  Orbis. Exact boot `f5f376da-6004-4755-aa41-2fe87ed9a198` passed image,
  controller and gate verification. Ethernet returned a valid SSH preflight
  but the connection subsequently dropped; Wi-Fi carried test control.
  Ethernet stayed enabled. No network driver reset, unload or rearm ran.
- Canonical UVD run `20261106T165519Z-a3521debbbd1` matched all 120 forced-VAAPI
  NV12 reference frames. One normal Diagnostix Shutdown request followed;
  `20261106T165637Z-75ff757984b6`. Its saved report identifies
  `software-poweroff`. Both reports survived on USB and were copied privately
  after the owner connected it to the laptop.
- A temporary `/run` service polled pending stop jobs and emitted bounded
  kernel-log records. It changed no existing service timeout, boot file or
  shutdown dependency. It identified `psx-rescue.service`, deactivating in
  `stop-sigterm`, with a 90-second deadline. MainPID 593 was blocked on terminal
  input in `do_select`. The canonical tty2 unit runs interactive login Bash
  with the default stop signal, explaining the idle shell's ignored SIGTERM.
- UART recorded systemctl acceptance at 400.590 seconds, final systemd-shutdown
  at 490.913, retained UVD power-off at 491.313, `Power down`, and atomic ICC
  power-off submission at 491.365. The long wait was 90.323 seconds; final
  systemd-shutdown reached ICC in about 0.452 seconds. The owner confirmed
  **fully off, light and fans stopped**. Physical transition latency was not
  independently timestamped.
- **Result: one software power-off passed after exact UVD decode.** Together
  EXP017/018 establish scoped ordinary reboot and software power-off on this
  exact combined kernel, with UVD enabled. Repeated-cycle, other-console and
  broader stability qualification remain separate.
- Continuity completed: 121,654 bytes, range 1711729..1833383, zero capture
  events; generation `1c88d65779aa4af2ad4bb078e4988771`, epoch 1. Session:
  `20261106_224831_102191-exp-20261106-018-same-dev5-uvd-kernel-shutdown-and-userspace-sto-bd808e2a`.
  Private plan/results/observer: `/var/tmp/psx-power-kernel-test-20261106-018/`.
  Next: canonical rescue-shell stop-signal correction and a new matched root
  image, preserving the tested kernel and all 41 modules; verify that power
  operations finish without the 90-second userspace wait.

## EXP-20261106-019 — PASS: rescue-shell fix removes the reboot delay

- Same dev5 kernel and all 41 modules; new alpha6 root changes the canonical
  tty2 rescue service to `KillSignal=SIGHUP`. Its 90-second stop timeout stays
  unchanged. Real interactive-Bash PTY tests verify why SIGTERM was ineffective.
- Exact candidate booted; canonical UVD oracle matched all 120 reference frames.
  One ordinary reboot followed. UART shows systemctl acceptance at 226.382 s,
  final systemd-shutdown at 227.013 s, retained UVD restart at 227.375 s and
  atomic ICC submission at 227.431 s, followed by Orbis startup. The previous
  90-second wait is gone: acceptance to ICC took **1.049 s**.
- Continuity completed: 414,189 bytes, range 1996864..2411053; zero capture
  events. Session `20261106_230815_783904-exp-20261106-019-rescue-shell-stop-signal-image-update-and-fast--b1fc8093`.
  Private receipts: `/var/tmp/psx-power-kernel-test-20261106-019/`.
- The owner then corrected the desired operation to Shutdown. That operational
  mismatch does not change the measured restart result; EXP020 sent Shutdown
  only. This is one observed corrected-image restart, not stability promotion.

## EXP-20261106-020 — PASS: fast Shutdown, physically confirmed fully off

- Fresh boot of the same rescue-fix alpha6 root and unchanged dev5 kernel.
  Verified active UVD runtime and all four immutable gates. No extra decode
  workload: the identical decoder/root already passed its oracle in EXP019.
- The literal Diagnostix operation was `shutdown`; no restart or retry was
  sent. UART shows acceptance at 125.710 s, systemd-shutdown at 126.353 s,
  retained UVD power-off at 126.752 s and ICC power-off at 126.803 s.
  Acceptance to ICC took **1.093 s**; the owner confirmed **fully off, power
  light and fans stopped**. The console was left off.
- Continuity completed: 120,886 bytes, range 2414089..2534975; zero capture
  events. Session `20261106_231906_401828-exp-20261106-020-shutdown-only-after-uvd-with-corrected-rescue-s-d39630ba`.
  Private receipts: `/var/tmp/psx-power-kernel-test-20261106-020/`.
- This establishes the corrected ordinary Shutdown path on this PS4. The new
  alpha7 immediate test-preemption path is a separate candidate and has not
  yet received a hardware test. Ethernet SSH stalls remain unresolved.

## EXP-20261106-021 — Matched alpha7 boot; wired SSH degraded

- The exact alpha7 image booted `7.2.11-psx-ps4-baikal-uvd-runtime-dev6`;
  read-only image/module/frontend verification passed. Kernel SHA256:
  `a7264426ee3b2ffeb030a04bbd7ab070418ecf4c0272b83400b4d5f45473bff4`.
  Overview and Power controls were available; **no Power command was sent**.
- An initial wired query preceded activation and had no ARP response. After
  MTS reached RUNNING and ARP matched, another query returned initial output
  then timed out. Independent Wi-Fi SSH completed the collection. Later
  operator connection changes removed the wired address; that later state
  does not explain away the earlier established-session stall.
- Verdict: matched boot observed, **wired SSH degraded**; no immediate-power
  or Ethernet stability acceptance. The tested path included the laptop's
  Wi-Fi LAN connection; loss attribution remains unresolved. UART completed, bytes
  2683428..2802865 (119,437 bytes), zero capture events. Private boot and
  network receipts remain indexed by this experiment ID.

## EXP-20261107-001 — Wired TCP loss localized with simultaneous captures

- Same alpha7/dev6 boot. Source-specific policy routing was unavailable in
  this config, so no rule was installed; interface-scoped Wi-Fi control kept
  the wired SSH flow separate. No driver change, reset, unload or rearm ran.
- All 25 client TCP packets appeared at the console, but 18 console egress
  packets had no matching host sequence coverage. The missing payload range
  was 296 bytes; fully captured short packets had valid checksums and correct
  Ethernet headers. ARP still reached the host during the stall. Console
  AF_PACKET capture precedes hardware transmission, so this does not identify
  which later boundary lost the packets or prove total PHY failure.
- The loss coincided with an SSH traffic-class change from EF to zero;
  **correlation, not a demonstrated cause**. Wired query failed; independent
  Wi-Fi control survived. UART completed, bytes 2802865..2806712 (3,847 bytes),
  zero capture events. Packet captures and checksum analysis remain private.

## EXP-20261107-002 — Client EF option did not restore wired SSH

- Changed only the client SSH option to `IPQoS=ef` on the same boot, retaining
  the server, driver and independent Wi-Fi control. The wired query returned
  no command output and failed with exit 255 after 5.96 seconds.
- Client EF alone supplied no remedy; this does not establish the cause of
  loss or control the server's socket marking. No persistent change remained.
  UART completed, bytes 2808408..2811308 (2,900 bytes), zero capture events.

## EXP-20261107-003 — Server EF trial unsuccessful; configuration restored

- Temporarily applied RAM-only server `IPQoS ef ef`, validated and reloaded
  sshd while retaining Wi-Fi control. The saved wired query timed out during
  TCP connection establishment after 5.01 seconds, with no command output.
- This trial did not demonstrate a successful workaround or isolate
  post-authentication traffic-class behavior. The temporary configuration was
  removed and the original effective policy restored; no driver operation ran.
  UART completed, bytes 2812156..2814937 (2,781 bytes), zero capture events.

## EXP-20261107-004 — FAIL: net1 did not fix wired SSH stalls

- Fresh matched alpha8 boot retained the exact dev6 kernel/config, UVD/ICC
  code and 40 other modules. Experimental `mts-tx-reclaim-net1` omits only
  the TX-reclaim descriptor word0 clear. It uses normal external Kbuild
  metadata; the staged MTS SHA256 is
  `8272e32c73f900b2a0e35b3f2a82002f2d7411a049a397501af24e2518448b82`.
  The loaded build note matched that candidate.
- Wired preflight returned JSON, then the SSH smoke failed with exit 255 after
  8.96 seconds. The planned 20-session and bidirectional hash-checked transfer
  gates were not completed. **Preserving returned word0 did not resolve the
  stall and must not be promoted as an Ethernet fix.** Wi-Fi provided fallback
  control; no running-module replacement, reset, unload or rearm occurred.
- UART completed, bytes 2948013..3088787 (140,774 bytes), zero capture events.
  Exact staging, loaded-module and failed-query receipts remain private.

## EXP-20261107-005 — Packet-class correlation below SSH; cause unresolved

- Same net1 boot. Bounded owned UDP/ICMP probes and TCP echo connections
  compared zero TOS with EF, without changing the driver. Host capture saw
  3/6 zero-TOS versus 6/6 EF UDP probes, and 0/2 versus 2/2 ICMP probes.
  Zero-TOS TCP echo timed out; EF echo returned the exact payload in 8.7 ms.
- These are **correlated observations, not a causal isolation or acceptance**:
  the TCP phases used different connections. UDP socket delivery was absent
  despite capture receipt, so its application count is not a wire-loss count.
  The final host capture ended before the final EF frames flushed; the TCP
  application did receive its echo. Temporary sockets exited without a
  persistent configuration change.
- UART completed, bytes 3091687..3094330 (2,643 bytes), zero capture events.
  No reset, unload, rearm or automatic recovery was used.

## EXP-20261107-006 — One forced-PTY wired SSH command completed

- Changed only the SSH PTY request on the same net1 boot. One command
  returned the expected marker/release and exited with status 0 in 2.46 seconds.
  All 19 captured console egress IPv4 frames used EF (`0xb8`).
- This is one successful command, **not reliability, a general workaround or
  driver acceptance**. The short connection and capture closed without a
  persistent change. UART continuity completed at bytes 3094330..3094330:
  zero new UART bytes and zero capture events; command/capture records supply
  the positive evidence.

## EXP-20261107-007 — INCONCLUSIVE: same-flow comparison did not run

- Planned to alternate zero/EF response marking within one owned TCP
  connection. Before the listener bound, the operator disconnected Ethernet;
  the wired address was absent and bind failed with `EADDRNOTAVAIL`.
  **No owned TCP probe was sent**, so this experiment cannot resolve the
  traffic-class hypothesis or supply another driver failure/success result.
- Independent Wi-Fi SSH confirmed the same boot and cached MTS state without
  a reported terminal fault. The listener exited and bounded passive capture
  ended; no persistent configuration or driver change occurred.
- UART continuity completed at bytes 3094330..3094330, zero new bytes and
  zero capture events. Private plan, bind failure and control receipts are
  retained; the same-flow comparison remains untested.

## EXP-20261107-008 — Same-flow response marking comparison

- After Ethernet was reconnected, one owned TCP connection alternated zero
  and EF response marking with `SO_PRIORITY=0` held constant. The first two
  ordinary replies arrived; the third and its ordinary retransmissions did
  not. Changing to EF allowed that same sequence to arrive, followed by the
  final response. Normal-priority ACKs also arrived during the loss.
- This demonstrates the marking-dependent behavior within one flow, without
  proving a blanket zero-TOS filter or locating the failing component between
  the console's software egress hook and the laptop capture. No configuration
  or driver change persisted. UART completed, bytes 3094567..3094567,
  zero new bytes and zero capture events.

## EXP-20261107-009 — Server EF policy did not establish a workaround

- A validated RAM-only `IPQoS ef ef` drop-in was applied through independent
  Wi-Fi control. The first wired SSH query timed out before authentication in
  5.01 seconds; the planned transfer loop stopped immediately.
- The post-authentication policy was not exercised. The drop-in was removed,
  sshd validated/reloaded, and the default `ef cs0` policy was confirmed.
  No driver operation ran. UART completed, bytes 3094567..3094567, zero new
  bytes and zero capture events.

## EXP-20261107-010 — Gateway works; laptop path loses ordinary traffic

- Interface-bound wired ICMP reached the LAN gateway 4/4 times for both
  ordinary and EF markings. The laptop received 2/4 ordinary versus 4/4 EF
  probes. Interface-bound HTTPS to kernel.org completed TCP/TLS and returned
  HTTP 404 in 0.61 seconds; this is connectivity evidence, not a successful
  content check.
- Normal-priority bidirectional wired traffic remained possible to the
  gateway/Internet. The loss was destination/path-specific, rather than a
  demonstrated global TX stoppage. The experiment did not distinguish GMAC,
  LAN forwarding or the laptop's receive path. No persistent setting changed.
  UART completed, bytes 3094567..3097526 (2,959 bytes), zero capture events.

## EXP-20261107-011 — Wired SSH recovered after laptop Wi-Fi reassociation

- Changed only the laptop's Wi-Fi association, reconnecting the same existing
  NetworkManager profile. The PS4 stayed on the same alpha8/net1 boot with
  unchanged driver and default sshd `IPQoS ef cs0`; no PS4 reset, unload,
  rearm, SSH policy change or Power request occurred.
- Before reassociation, ordinary wired PS4-to-laptop ICMP was 0/6. Afterward,
  **20/20 ordinary non-PTY SSH logins** completed, and **4 MiB transfers in
  each direction matched SHA256**. Upload took 0.829 seconds and download
  0.789 seconds including SSH setup. Final ordinary ICMP was 6/6.
  Host capture included 1,705 CS0 console egress frames covering 4,206,416
  TCP payload bytes. Cached MTS error, terminal fault and pending TX were zero.
- Verdict: **the tested wired SSH path recovered**. The host association/AP
  path is a relevant variable; the exact faulty component remains unresolved.
  This does not prove a PS4 code fix, net1 causality, endurance or broad
  Ethernet stability. Temporary probe data was removed and the existing host
  profile restored without changing saved settings.
- UART completed, bytes 3097526..3100426 (2,900 bytes), zero capture events.
  Private before/after receipts, captures and matching transfer hashes remain
  indexed by this experiment ID.

## EXP-20261107-012 — Private USB-free FPKG transferred and verified

- Built Diagnostix RAM alpha1 (`DGNX00001`, version `0.01`) with the unchanged
  dev6 kernel and all 41 matching base modules. Host package validation and
  extracted readback of all 20 staged files passed. The 460,128,256-byte package
  includes locally supplied firmware and remains private.
- Uploaded a new temporary file and read back all bytes over Orbis FTP. SHA256
  matched `517531ae200e76106d97a595979de325eb906dadfbf43d524f2789b6207b29a0`
  before renaming it to `/data/diagnostix-ram-alpha1-0.01.pkg`. Upload took
  39.48 seconds; readback took 58.56 seconds. No existing boot files changed.
- UART continuity completed at bytes 3283119..3287025 (3,906 bytes), zero
  capture events. The console remained in Orbis. The private build/transfer
  receipts are indexed by this experiment ID; no app install, loader delivery,
  Linux boot or Power action ran. **USB-free hardware acceptance is pending.**

## OBS-20261107-FPKG-001 — First operator launch stopped before Linux

- The operator installed version `0.01` and selected Boot. Continuous UART
  recorded GoldHEN accepting the exact 37,000-byte ELF, successful kernel
  interface/syscall setup, firmware 12.02, 1024 MiB VRAM and Baikal detection.
  The next loader error was **`Failed to allocate image`**; Linux did not start.
- This error is emitted for the approximately 11 MiB kernel buffer, before
  kernel image copy or initramfs allocation. It conflates allocation failure
  with mapping/range rejection; it does not establish RAM-image exhaustion.
  The preparation-failed return does not request shutdown.
- Source inspection also found four full image passes in installation using
  identical progress labels: embedded verification, copy/hash, staged readback
  and redundant post-rename readback. No automatic install retry was found.
- This was an operator-initiated observation in continuous capture, **without
  a predeclared bounded session**. It is negative diagnostic evidence, not
  acceptance. Private raw context and offsets are retained under this ID.
  The next candidate must report the specific allocation rejection and make
  each installer phase visible before another declared trial.

## EXP-20261107-013 — NOT RUN: FTP unavailable after console transition

- Candidate `0.02` passed native package validation, all 20 extracted member
  comparisons and 11 FPKG host tests. Its loader03 adds exact allocation
  rejection records; no allocator-policy fix is claimed. The kernel and RAM
  image are unchanged. The corrected UI removes one redundant full-image read.
- FTP failed at connection with `EHOSTUNREACH`, before any package bytes or
  boot files were sent. No candidate install, payload or Power action ran.
  Earlier continuous UART independently recorded a Payload Guest launch of
  `linux-omarchy-4.5-internal-v25.bin`, followed by an Orbis exfatfs
  `vputx: negative ref cnt` shutdown panic. That is a separate payload event,
  not a Diagnostix allocation result.
- Capture continuity completed at bytes 3408674..3434594 (25,920 bytes), zero
  events, observing the subsequent Orbis startup. The candidate trial did not
  run and has no acceptance result. Private package/connection receipts remain.

## EXP-20261107-014 — Kernel allocation refusal identified

- New setup evidence: the operator recovered Orbis and reopened FTP; a direct
  read-only connection confirmed `/user/data/pkg` before starting this session.
  Upload/readback of the new `0.02` package precedes operator installation.
- Objective: one explicit Install / Verify and Boot attempt identifies the
  kernel-buffer rejection through `DIAGNOSTIX_ALLOC_V1`, or observes preparation
  proceeding. Timeout 20 minutes; stop on preparation failure, terminal fault
  or UART discontinuity. No automatic retry. Existing package, USB, Omarchy and
  previous installed generation remained recovery options.
- The `0.02` FTP upload/readback completed with matching SHA256
  `aa4098aa113e6cc057d4f549d30f731a1d763a744644dea00d945abee21d76eb`.
  On the owner's explicit request, the obsolete cached `0.01` installer was
  removed (460,128,256 bytes); the verified local recovery package remains.
- Before any loader handoff, the owner requested a cleanup menu and confirmed
  the console was still in Orbis, installing/verifying `0.02`, with FTP open.
  Setup was amended to native app `0.03` for **Clear old files**. Kernel,
  complete RAM image and diagnostic loader03 are byte-identical to `0.02`;
  the single allocation-diagnosis objective and original timeout are unchanged.
  Candidate `0.03` passed 11 host tests, native object/link checks and all 20
  extracted-package comparisons. Its FTP readback matched SHA256
  `53ac770a6901cd26367540e10760309ad7992b3e3cfe21c9618272d45df8af0b`.
- Actual execution remained app `0.02` while `0.03` was being transferred.
  UART identified `DIAGNOSTIX_ALLOC_V1 reason=kmem-returned-zero`, one attempt,
  request 11,273,216 bytes / rounded 11,288,576, flags `0x101`, high
  `0xffffffff`, alignment `0x4000`. Allocation returned zero before every
  post-allocation guard; the kernel image was never copied and Linux did not
  start. The operator confirmed preparation failed. No shutdown was armed.
- The diagnostic objective was answered; **boot acceptance failed**. Capture
  continuity completed at bytes 3559723..3601864 (42,141 bytes), zero events.
  Next source change restores one reference `M_ZERO|M_WAITOK` call without the
  upstream outer retry loop; it may wait internally and needs a new trial.

## EXP-20261107-015 — SETUP BLOCKED: waiting allocation trial not reached

- Candidate app `0.04` contains loader04, one reference `M_ZERO|M_WAITOK`
  allocation call (`0x102`) with entry/return markers. Physical ranges,
  continuity, input/header checks, mapped-address cleanup and pre-arm order
  remain unchanged. There is no outer retry and no hard allocation timeout.
- User requested a single preparation pass and direct launch. App `0.04`
  copies/hashes once, binds the verified copy to file identities, reuses that
  unchanged same-launch snapshot for Boot, and has no extra Boot confirmation.
  Twelve FPKG tests and four actual-source loader suites passed; native builds
  and all 20 extracted package comparisons passed. Hardware result pending.
- Plan: one-pass FTP upload (sent-stream SHA, completion ACK and remote size),
  then operator installs `0.04`, prepares once and selects direct Boot once.
  Stop on preparation failure, terminal fault or UART discontinuity. Observe
  90 seconds at allocation / 120 seconds after handoff, 15 minutes overall.
  A blocked kernel allocation needs operator physical recovery; the timeout
  cannot cancel it. Local prior packages and known USB remain recovery inputs.
- Package `0.04` uploaded in 36.96 seconds, hashing its stream once to
  `6cb9468e29f89a5bb043120cdf932429827277ba0fc23773a9a38a8916e08afa`.
  FTP completion and final 460,193,792-byte size were confirmed; no full remote
  reread ran. Obsolete cached installers `0.02` and `0.03` were removed under
  the owner's cleanup request, leaving only `0.04` in the installer directory.
  Their local recovery packages and active/previous boot files were preserved.
- The operator launched/reinstalled native `0.04`, but reported Install / Verify
  returned to the menu or did not respond, including after the explicit dialog
  selection. Read-only FTP found active manifest still `0.02`, no stage/journal,
  and exact packaged `0.04` manifest bytes/hash. The remaining default-Cancel
  Install dialog was a UX defect; an immediate preflight refusal was also
  possible and had no detailed status log. The exact runtime cause was not
  established. Loader04 was **not invoked**, so this is not a WAITOK result.
- Capture continuity completed at bytes 3623276..3689038 (65,762 bytes), zero
  events. The trial stopped at setup; no kernel boot acceptance is claimed.
  App `0.05` removes the Install dialog, shows precise failure reasons and
  writes a bounded owned status log. Read-only source identities no longer
  require writable-file link-count semantics; stored/cleanup guards remain.

## EXP-20261107-016 — Native stat ABI mismatch identified; loader not reached

- Candidate app `0.05` starts Install / Verify and Boot directly; cleanup
  deletion alone retains confirmation. It copies/hashes once and caches the
  unchanged same-launch identity. Its source preflight logs native mode/link
  count/size, manifest and the exact failing stage to the owned
  `/data/diagnostix/launcher-status.log`; no image contents are logged.
- Thirteen FPKG tests, native object/link checks and all 20 extracted package
  comparisons passed. Kernel, RAM image and waiting loader04 are unchanged.
  Plan: upload once, operator installs `0.05`, selects Install / Verify once;
  inspect the precise setup log on failure, or select direct Boot once when
  ready. Stop on failure or UART discontinuity; 15 minutes
  overall, 90 seconds at allocation, 120 seconds after handoff. Physical/USB
  recovery remains available if an allocation waits indefinitely.
- Actual UART and the owned status log showed source `bzImage` as mode
  `300555`, links `0`, size `22144`, instead of the 11,273,216-byte file.
  The SDK's 128-byte stat object had been passed directly to a native API
  writing the FreeBSD 9 layout: native links at offset 10 polluted SDK mode,
  UID at 12 became SDK links, and allocated blocks at 80 became SDK size.
  Read-only FTP confirmed the packaged file size and manifest. This was our
  metadata adapter defect, not evidence of a truncated image or failed hash.
- Preparation stopped before copying/activation; loader04 was not invoked.
  Capture continuity completed at bytes 3692831..3717418 (24,587 bytes), zero
  events. No WAITOK or Linux boot result is claimed. App `0.06` introduces an
  explicit fixed-width conversion and an aligned zeroed 256-byte API buffer;
  the exact observed wrong fields are reproduced in serialized-ABI tests.

## EXP-20261107-017 — Preparation passed; waiting kernel allocation returned zero

- Objective: app `0.06` reports correct source identities, completes one
  streamed copy/hash and activates its candidate, then the operator selects
  Boot once. Changed variable: the native metadata adapter. Kernel, complete
  RAM image and loader04 bytes/policy are unchanged. Fourteen host tests,
  ASan/UBSan, native object/link checks and all 20 extracted package comparisons
  passed; these do not establish PS4 preparation or boot acceptance.
- Continuous UART was ready; session began at byte 3727244. Bounds: 15 minutes
  overall, 90 seconds observing allocation and 120 seconds after handoff.
  Stop on failure or continuity loss; a waiting kernel allocation cannot be
  cancelled by this observation timeout. No automatic retry. Local prior
  packages, active/previous boot files and known USB remain recovery inputs.
- Package `0.06` uploaded in 45.84 seconds with sent-stream SHA256
  `61fabdad541eff6974f1cb37e4ec7f5d235f341c5f9684e55f89ce4d93836e75`.
  FTP acknowledged completion and final size 460,193,792 bytes; no full remote
  reread ran. Only obsolete cached `0.05` was removed under the owner's cleanup
  request, leaving `/user/data/pkg/diagnostix-ram-alpha1-0.06.pkg`. Its local
  recovery copy remains.
- Actual `0.06` source metadata now reports mode `100555`, links `1`, kernel
  size `11273216` and initramfs size `444785998`. One streamed copy/hash, flush
  and activation completed; the owned log reported ready and FTP read-only
  inspection found active manifest `0.06`. Boot reused that snapshot and sent
  the 37,000-byte ELF once. GoldHEN received and executed it; `sys_kexec` ran.
- The waiting allocator's entry and return markers both appeared. The first
  request (11,273,216 bytes, rounded 11,288,576, flags `0x102`, physical high
  `0xffffffff`, alignment `0x4000`) returned zero. No mapping guard, initramfs
  kernel allocation, shutdown arming or Linux boot occurred. The owner reported
  payload failure. The native metadata/preparation fix is observed on this
  console; the allocation-policy candidate did not solve the boot failure.
- Capture continuity completed at bytes 3727244..3764490 (37,246 bytes), zero
  events. No automatic retry. Subsequent source investigation must account for
  the entire 425 MiB userspace initramfs being read into anonymous RAM before
  this first kernel allocation; the small working USB bootstrap differs there.

## EXP-20261107-018 — Lazy input accepted; kernel allocation still failed

- App `0.07` carries loader05. The single changed diagnostic variable is
  initramfs input backing: an exact-byte read-only/private file mapping with
  no eager reads or prefault, instead of the complete anonymous input copy.
  Mapping/size/close failures abort without fallback. Kernel allocator flags,
  limits, guards and embedded kexec bytes are identical to loader04. The
  verified installed generation remains immutable through kernel copyin.
- Primary review verified the source delta, reran all four actual-source C
  suites with ASan/UBSan and all 14 native-launcher tests, and checked all 20
  extracted FPKG members. Inaccessible host mappings establish no early input
  access; they do not establish Orbis file paging or contiguous allocation.
- After EXP017 had completed, a host artifact move consumed the UART volume's
  reserve. The logger stopped on its 512 MiB free-space floor at 04:46:08;
  no active experiment crossed that stop. The artifact was restored to its
  original verified location and duplicate generated build directories removed.
  Capture was restarted ready at 04:51:06 with generation
  `6666d07ef26644bcb0e800c050ce438f`, epoch 1. This trial begins at byte 114.
- Plan: one upload, operator closes `0.06`, installs `0.07`, prepares once and
  selects Boot once. Observe whether the first kernel allocation advances,
  then the larger initramfs allocation or a precise failure. Bounds: 15 minutes
  overall, 90 seconds at allocation, 120 seconds after handoff; stop on failure
  or continuity loss. An OS allocator wait cannot be cancelled by that timeout.
  No automatic retry; previous local packages and known USB/physical recovery
  remain available. No Power command is sent by the host.
- Package `0.07` uploaded once in 42.96 seconds; sent-stream SHA256 matched
  `7465418c2c91816dbef22afa15b334c7dfb10df957132a25c47fa98c63f1cf58`.
  FTP completion and final 460,193,792-byte size were confirmed without a full
  remote reread. Only obsolete cached `0.06` was removed; its verified local
  recovery package and active/previous boot generations remain. Ready path:
  `/user/data/pkg/diagnostix-ram-alpha1-0.07.pkg`.
- The operator installed `0.07`. One streamed preparation completed and
  subsequent Install selections reused its verified snapshot. Its 37,032-byte
  ELF was received and executed once. Reaching `sys_kexec` establishes that
  opening, mapping, size recheck and descriptor close passed on Orbis; it does
  not establish demand paging of the whole file during kernel copyin.
- The first 11,273,216-byte kernel allocation again returned zero under flags
  `0x102`, high `0xffffffff`, alignment `0x4000`. The input-mapping change did
  not resolve allocation. No kernel image copy, kernel initramfs allocation,
  shutdown arming or Linux boot followed. UART then recorded a physical power
  button and Orbis suspend sequence; the host sent no Power command.
- Capture continuity completed at bytes 114..88320 (88,206 bytes), zero events.
  The trial stops here; another run of this payload is not a new diagnostic
  variable. Further work is source-only review of allocator addressability,
  the native direct map and the replacement boot page tables.


## EXP-20261107-019 — Diagnostix 0.08 FTP readback passed; old cache removed

- Objective: upload only the host-verified Diagnostix `0.08` installer, retrieve
  the complete temporary file and compare its SHA-256, publish it, then remove
  only the obsolete cached `0.07` installer under the owner's explicit request.
  Timeout 20 minutes; stop on FTP error, identity/hash mismatch or capture loss.
  No installation, payload, boot-file or Power action was included.
- The anonymous FTP endpoint and `/user/data/pkg` path were confirmed read-only
  before starting. The only existing Diagnostix installer was the expected
  460,193,792-byte `0.07` package. The verified local `0.07` recovery package
  remains at `/var/tmp/psx-diagnostix-fpkg-ram-alpha1-20261107-07/IV0000-DGNX00001_00-DIAGNOSTIX000001.pkg`
  with SHA256 `7465418c2c91816dbef22afa15b334c7dfb10df957132a25c47fa98c63f1cf58`.
- Upload and complete FTP readback of `0.08` both returned 460,390,400 bytes
  with SHA256 `12cd6a33e2fcd93cc925763c8c206f3013ef42ee547ed723aa9efc664438e3df`.
  The temporary file was renamed to `/user/data/pkg/diagnostix-ram-alpha1-0.08.pkg`.
  Only `diagnostix-ram-alpha1-0.07.pkg` was then deleted; its removal and the
  final directory containing only the `0.08` Diagnostix installer were confirmed.
  The local transfer receipt is `/var/tmp/psx-diagnostix-fpkg-transfer-20261107-08/TRANSFER.json`.
- Capture continuity completed at bytes 354684..355609 (925 bytes), zero events.
  This is storage-transfer evidence only: no package installation, loader
  execution, Linux boot or hardware acceptance is claimed.


## Follow-up after EXP-20261107-019 — Loader06 first allocation failed

- The operator reported `preparation failed` after launching Diagnostix `0.08`.
  This action did not occur inside a bounded session, so it is an operator
  observation correlated with continuous UART and read-only FTP, not a new
  acceptance experiment. No retry was requested or performed by the host.
- The owned launcher log proves that Prepare completed: all six `0.08` source
  files passed their expected metadata, the new generation completed copy,
  verification and activation, and the final result was `Installed candidate
  verified. Ready to boot.` Boot then completed `Send verified loader once`.
  Active FTP manifest is `0.08`; no `boot.new` staging directory remains.
- Continuous UART lines 6267..6278 show `sys_kexec` and loader06's first
  `DIAGNOSTIX_ALLOC_V2` request: 11,273,216 bytes, rounded 11,288,576, flags
  `0x102`, high `0x27f000000`, alignment `0x4000`. Orbis returned `vm=0x0`;
  `DIAGNOSTIX_ALLOC_V1` reports `kmem-returned-zero`, followed by `Failed to
  allocate image`. No post-allocation guard or initramfs allocation was reached.
- Confirmed cause of the visible alert: the native kernel contiguous allocator
  refused the first kernel-image buffer. This is not a package, preparation,
  hash or navigation failure. Expanding the physical high limit did not resolve
  it. The reason for the allocator's zero return remains unconfirmed; do not
  infer general RAM exhaustion. The next lower-layer diagnostic is the existing
  bounded read-only memory-pool observation, not another identical boot retry.


## EXP-20261107-020 — Compact 0.09 uploaded; SATA/payload audit complete

- Objective: upload the compact Diagnostix `0.09` upstream-allocation candidate,
  verify a complete FTP readback and preserve the existing `0.08` installer as
  rollback. Timeout 20 minutes; stop on FTP error, identity/hash mismatch or
  capture loss. No remote deletion, installation, payload or Power action.
- Source audit found no missing SATA/payload component. Loader07 retains the
  upstream Baikal `disableMSI(0xf80a20e0)` handoff. The matching kernel config
  has built-in AHCI, SCSI/SD, loop, ext4 and tmpfs support. All five scoped SATA
  boot arguments occur exactly once, and the initramfs contains the read-only
  guard. The root is RAM-backed, while the separate read-only SATA inventory
  gate runs only after Linux starts; it cannot explain the pre-handoff allocator
  failure. The local audit is `/var/tmp/psx-diagnostix-fpkg-transfer-20261107-09/SATA-PAYLOAD-AUDIT.json`.
- Upload and complete FTP readback both returned 460,390,400 bytes with SHA256
  `f41b0ea1c673ad029f40d5feee6d3c05002d6b0045c6fc1117a9452f999b0ee3`.
  `/user/data/pkg/diagnostix-ram-alpha1-0.09.pkg` was published. Final read-only
  inspection confirmed both `0.08` and `0.09`, each 460,390,400 bytes. No remote
  file was deleted. The transfer receipt is `/var/tmp/psx-diagnostix-fpkg-transfer-20261107-09/TRANSFER.json`.
- Capture continuity completed at bytes 425706..427692 (1,986 bytes), zero events.
  This is source-review and storage-transfer evidence only; no package
  installation, loader execution, Linux boot or hardware acceptance is claimed.


## Follow-up after EXP-20261107-020 — Loader07 upstream call also returned zero

- The operator launched Diagnostix `0.09` outside a bounded session. Continuous
  UART lines 7198..7210 confirm `sys_kexec`, policy `UPSTREAM_SINGLE`, the exact
  unrounded 11,273,216-byte request, high `0xffffffffffffffff`, alignment `1`
  and independent guard high `0x27f000000`. Orbis again returned `vm=0x0` and
  `kmem-returned-zero`, followed by `Failed to allocate image`.
- The upstream size/high/alignment differences are therefore ruled out as the
  cause of this refusal. No initramfs allocation, Linux handoff or SATA
  initialization occurred. This is continuous-capture/operator evidence, not a
  bounded acceptance experiment. No automatic retry was made by the host.

## EXP-20261107-021 — Official sample delivery failed; truncated ELF faulted

- Objective: compare the official unmodified v25 `linux-1024mb.elf` against the
  intact small internal rescue initramfs (3,330,277 bytes), preserving all boot
  files. Payload SHA256 `c813d169ef37e4bee574a5058bc6c0b92564e74ab445ef0846d67fa9d1e5ce65`,
  320,936 bytes. PayLoader reported ready. Stop on stable failure or capture loss.
- The host used one `send()` and incorrectly treated its short 110,048-byte
  return as a completed experiment. GoldHEN saved that truncated ELF, launched
  it, and UART recorded `Fatal trap 12: page fault while in kernel mode`.
  The official loader never reached its file reader or allocator. This was a
  host transport error, not a stock-loader result. Capture completed at bytes
  463190..466283 (3,093 bytes), zero events. No boot file was changed.

## EXP-20261107-022 — Complete sample received after fault; no launch evidence

- Changed variable: one connection with `sendall`, requiring all 320,936 bytes,
  after EXP021's partial write. PayLoader again reported ready and acknowledged
  receipt of all 320,936 bytes. The same official hash and small rescue set were
  preserved. No additional payload or storage action followed.
- UART confirmed receipt but did not show payload launch, `sys_kexec`, an
  allocation or Linux. The preceding Fatal trap made further delivery unsafe;
  capture was stopped at bytes 466283..469102 (2,819 bytes), zero events. There
  is no valid upstream comparison from this experiment.
- Stop condition: kernel fault before a valid sample launch. Do not retry in the
  current Orbis state. A fresh operator-controlled Orbis/GoldHEN recovery is
  required first. The Diagnostix launcher also uses one `send()` and rejects a
  short write before closing; its next candidate must use a complete-write loop
  because GoldHEN can launch a truncated payload. No new package is authorized
  by this observation alone.


## EXP-20261107-023 — Diagnostix 0.10 upload passed; first app launch hung

- Objective: upload the host-verified 0.10 complete-write candidate, install and
  Prepare it, then inspect state over FTP without selecting Boot or sending a
  payload. Timeout 20 minutes; stop on FTP/hash failure, capture loss or console
  fault. Existing packages and installed generations were preserved.
- Upload and complete FTP readback both returned 460,390,400 bytes with SHA256
  `f30f4d253720bc53fbf2581545dd1356838ed8e62e45e0a5dc37c2909a35219f`.
  `/user/data/pkg/diagnostix-ram-alpha1-0.10.pkg` was published; no old package
  was deleted. The transfer receipt is
  `/dev/shm/diagnostix-fpkg-10-transfer/TRANSFER.json`.
- Installation completed and Orbis launched app version 0.10. The operator then
  reported a blank screen and inability to close it. UART shows no Prepare action
  or launcher log entry before the hang. Orbis could not suspend thread 101394,
  and the operator initiated a reboot. No payload was sent and FTP confirmed the
  active installed generation remained 0.09. The initial startup hang's precise
  cause is not established by this capture.
- Capture continuity completed at bytes 537339..573245 (35,906 bytes), zero
  events. This was a failed first launch, not preparation or Linux evidence.


## Follow-up after EXP-20261107-023 — Fresh-boot Prepare and FTP hashes passed

- After the operator rebooted, continuous UART showed GoldHEN/PayLoader ready and
  a fresh Diagnostix 0.10 launch. This action occurred outside a new bounded
  session, so it is continuous-capture/operator evidence rather than bounded
  acceptance. The earlier blank screen did not recur in this launch.
- Prepare copied all six files, activated the generation and ended with
  `Installed candidate verified. Ready to boot.` No Boot action, `sys_kexec`,
  allocation or payload launch followed. FTP found no `.transaction`,
  `.transaction.new` or `boot.new` marker.
- Independent streaming FTP SHA-256 verification passed for all six active files:
  kernel `a7264426ee3b2ffeb030a04bbd7ab070418ecf4c0272b83400b4d5f45473bff4`,
  initramfs `78b7f494066cc7f4ee7a3d0ef047a952fb7133ab24b29b273dd1a57b065cce72`,
  bootargs `2ee3acdf69fcaa7671eb0d9158b0539ad989f43b10fd7e49fef138908f21224d`,
  loader07 `bbd2b4ab45a533449de70483b6c783f2f0a60d7c559f216555d7083d22866753`,
  and the vram/manifest pins. The receipt is
  `/dev/shm/diagnostix-fpkg-10-transfer/INSTALLED-VERIFY.json`.
- Package transfer and fresh-boot preparation integrity therefore pass. This does
  not establish that Linux will boot: the complete-write loop remains untested on
  hardware and loader07's separate first-allocation failure remains unresolved.
  No launch was performed by the host.


## Follow-up after EXP-20261107-023 — 0.10 transport passed; loader allocation failed

- After the verified Prepare, the operator selected Boot outside a bounded
  session. The 0.10 launcher logged `handoff: all 37096 loader bytes written on
  one connection`, then GoldHEN executed loader07 and UART reached `sys_kexec`.
  This directly observes the complete-write transport on hardware; no truncated
  ELF or connection retry occurred.
- Loader07 again used `UPSTREAM_SINGLE` for the first 11,273,216-byte kernel
  request with high `0xffffffffffffffff` and alignment `1`. Orbis returned
  `vm=0x0`, followed by `kmem-returned-zero` and `Failed to allocate image`.
  No initramfs allocation or Linux handoff followed.
- FTP still identifies active version 0.10 with no transaction/staging marker.
  The visible failure is therefore not a preparation failure: Prepare and the
  complete loader write succeeded, while the separate first kernel-image
  allocation failed again. Do not infer generic RAM exhaustion or retry the same
  path without a new diagnostic variable. The next lower-layer investigation is
  the existing read-only memory-pool observation, not another identical launch.


## EXP-20261107-024 — Loader08 bounded retry passed transport; all three allocations refused

- Objective: test only a three-attempt bound on upstream's zero-return allocation
  retry, keeping loader07's call parameters, paths and guards and app 0.10's
  complete-write transport. Upload, install, Prepare once, verify over FTP, then
  one Boot. Timeout 20 minutes; stop on three refusals, a later failure, Linux
  takeover, console fault or capture loss. Existing packages/generations preserved.
- Loader08 SHA256 `169ce5324269626d931db5b54d10d1fda6af1d7580e72efdf7de2c4137220191`.
  App 0.11 package SHA256 `963f8a19890df624de3df689b77eced3db7bfa9704daf4b5f34ebcbce42bc686`.
  Five loader sanitizer suites, 16 launcher tests, 771 diagnostics Python tests,
  15 Node tests, native builds, SFO 0.11 and all 23 package readbacks passed.
- Upload and complete FTP readback both returned 460,390,400 bytes with the
  expected package hash. `/user/data/pkg/diagnostix-ram-alpha1-0.11.pkg` was
  published without deleting old packages. Transfer receipt:
  `/dev/shm/diagnostix-fpkg-11-transfer/TRANSFER.json`.
- Prepare completed. Independent FTP hashes of all six installed files matched,
  including the loader08 and manifest `3d4cc9c6203df374bb0bfa212658ddc9a385c47f3c0098493e1b88953e03c779`
  pins. No transaction marker or earlier Boot followed. Receipt:
  `/dev/shm/diagnostix-fpkg-11-transfer/INSTALLED-VERIFY.json`.
- The single Boot wrote all 37,096 loader bytes on one connection. GoldHEN saved
  and launched the ELF, and UART reached `sys_kexec` with `UPSTREAM_BOUNDED3`.
  Attempts 1, 2 and 3 each requested 11,273,216 bytes with high
  `0xffffffffffffffff` and alignment `1`; every attempt returned `vm=0x0`.
  `kmem-returned-zero attempts=3` and `Failed to allocate image` followed. No
  fourth call, initramfs allocation or Linux handoff occurred.
- Capture continuity completed at bytes 1253672..1311921 (58,249 bytes), zero
  events. Verdict: bounded retry does not resolve the allocator refusal in this
  console state. Do not repeat this path. The next diagnostic must move down to
  the read-only memory-pool observation or establish a valid stock comparison;
  neither is part of this experiment.

## EXP-20261107-025 — Memory report completed after loader08 refusal

- Objective: one existing read-only memory-pool report, without boot, allocation,
  shutdown or boot-file changes. Owner explicitly authorized continuing the fix
  and hardware diagnostics. Stop after completion or within 60 seconds, and on
  mismatch, fault or capture loss. Orbis was at its menu after Diagnostix closed.
- The 16,240-byte ELF SHA256
  `a5de6b8c15f189cf12482e11cadce53fc4eb4ab1cdac77d128d69f4cd83c7aa9`
  matched its reviewed build/source receipt. One complete connection delivered
  it; GoldHEN confirmed that exact size and execution in ScePartyDaemon.
- The report matched FW12.02/Baikal B1 fingerprints, completed 253 bounded
  metadata reads, and left Orbis running. Eight normal segments and two active
  free lists were reported. For normal selector zero, all counts at orders
  10..12 were zero across both free lists and all three pools; smaller blocks
  remained. A separate selector had one order-12 block; special-pool counts
  were zero. These are unlocked counters, not an ownership or availability grant.
- The failed 11,273,216-byte request needs 689 native 16 KiB pages and starts
  the reviewed physical search at order 10. The snapshot is consistent with
  contiguous-block fragmentation in its selected class. It does not establish
  general RAM exhaustion or uniquely identify the earlier native zero-return
  branch. Changing to another pool without its ownership contract is unwarranted.
- Capture continuity completed at bytes 1345100..1349058 (3,958 bytes), zero
  events; compact context
  `20261107_193550_278318-exp-20261107-025-one-verified-read-only-memory-pool-report-after-8943e058.md`.
  Private send/native-review receipts are in
  `output/diagnostix-memory-report-20261107-025/`.
- Conclusion: proceed with source work on owned, noncontiguous staging and
  validated relocation at terminal takeover. Do not repeat the whole-image
  contiguous allocation or move shutdown earlier. No Linux boot occurred.


## EXP-20261107-026 — Full-size wired-page staging passed; no Linux handoff

- Objective: stage the installed 0.11 kernel and complete RAM initramfs using
  native wired, noncontiguous backing, validate the physical relocation plan,
  then free preparation buffers without arming shutdown. Three-minute bound;
  stop on refusal, fault, completion or capture loss. Existing boot files remain
  the rollback; no boot-file write or automatic retry was included.
- The stage-only ELF SHA256 was
  `ed43a620ab2b80f05cf0935b3ecd6266079191809f3f75c4d005c1a0f221c56b`.
  The 11,273,216-byte kernel and 444,917,182-byte initramfs-plus-firmware
  allocation requests succeeded. Firmware extraction, input copies and header
  checks completed; 444,880,734 initrd bytes were represented by the plan.
- UART recorded 27,844 relocation records in 41 descriptor nodes, followed by
  `DIAGNOSTIX_STAGE_V1 PASS` and `cleanup_complete=1 result=0`. All five
  preparation buffers were freed. No shutdown hook, GPU/pstate handoff, reboot
  or Linux boot followed. The existing bootstrap patches, syscall installation
  and retained kexec code are outside that five-buffer cleanup claim.
- Verdict: **PASS_STAGING_ONLY**, resolving the full-size preparation question
  for this candidate and installed 0.11 inputs. This does not qualify the new
  versioned 0.12 path or terminal relocation/Linux boot; Linux boot remains pending.
- Capture continuity completed at bytes 1364431..1367072 (2,641 bytes), zero
  events. Compact context:
  `20261107_195915_696434-exp-20261107-026-full-size-wired-page-staging-and-plan-validatio-7ce14986.md`.
  Receipt: `output/diagnostix-page-staging-20261107-026/RESULT.json`.

## EXP-20261107-027 — Obsolete-generation cleanup stopped at FTP permission failure

- Objective: delete only six independently identified obsolete Diagnostix
  generations, preserving active 0.11, rollback 0.10 and the 0.11 installer.
  Two-minute bound; stop at the first error or capture loss.
- After correcting FTP transfer mode to `TYPE I` before `SIZE`, the first
  `DELE` request for the obsolete 0.01 generation's `bzImage` returned 550.
  No files or directories were deleted; reclaimed bytes were zero. The
  permission failure ended this attempt without repeating the deletion.
- Capture continuity completed at bytes 1369190..1369604 (414 bytes), zero
  events. Compact context:
  `20261107_200410_674751-exp-20261107-027-remove-six-verified-obsolete-diagnostix-ftp-gen-0e573ba2.md`.
  Receipt: `output/diagnostix-ftp-cleanup-20261107/CLEANUP.json`.

## EXP-20261107-028 — Child permission change allowed deletion; parent blocked RMD

- Changed variable: `SITE CHMOD 0777` on the verified obsolete 0.01 generation
  directory after EXP027's permission refusal. Active and rollback permissions
  remained unchanged. Two-minute bound; stop on error or capture loss.
- All six files in that one obsolete generation were deleted, reclaiming
  456,097,558 bytes. Removing its now-empty directory returned FTP 550 because
  parent-directory write permission was still missing. No other generation was
  deleted in this attempt; no automatic repeat followed the RMD failure.
- Capture continuity completed with zero UART bytes and zero events. The FTP
  receipt establishes storage effects; silent UART is not independent deletion
  evidence. Compact context:
  `20261107_200715_733100-exp-20261107-028-scoped-ftp-cache-permission-correction-after-de-38964b11.md`.
  Receipt: `output/diagnostix-ftp-cleanup-20261107/CLEANUP-PERMISSIONS.json`.

## EXP-20261107-029 — Six obsolete generations removed; active and rollback retained

- Changed variable: temporarily grant group write (`0775`) to the Diagnostix
  parent and remaining verified obsolete child directories, then restore the
  parent to `0755`. Two-minute bound; stop on error or capture loss. No loader,
  boot, active-generation replacement or unrelated cleanup was included.
- Removed the empty EXP028 directory and the five remaining obsolete
  generations. This action deleted 2,280,487,950 file bytes; combined with
  EXP028, six generations totaling **2,736,585,508 bytes** were removed.
- FTP confirmed parent permission restoration and the final directory listing.
  Active `boot` version 0.11, rollback 0.10
  (`previous-4bd5d426e278bcd855499164f55a0958caaed54fa2e27799afa438972deb369c`),
  the owner marker, launcher log and cached 0.11 installer were preserved.
- Capture continuity completed with zero UART bytes and zero events; deletion
  and restoration conclusions come from the FTP receipt. Compact context:
  `20261107_200927_657933-exp-20261107-029-finish-verified-obsolete-cache-cleanup-with-tem-ef5c937f.md`.
  Receipt: `output/diagnostix-ftp-cleanup-20261107/CLEANUP-FINAL.json`.
  Timing limit: the final FTP receipt timestamp is 23 ms after the UART stop
  fence; storage completion is established by FTP, not by silent UART.

## EXP-20261107-030 — Versioned 0.12 inputs verified; initrd page guard stopped boot

- Objective: one terminal scatter handoff using the versioned 0.12 boot set and
  explicit 1024 MiB VRAM file. Owner confirmed USB storage disconnected. Setup
  bounded to 15 minutes; handoff to three minutes, stopping at failure/fault or
  capture loss. Old active 0.11, rollback 0.10 and installer remained available.
- Uploaded all six files into `/data/diagnostix/versions/0.12/boot.new`, verified
  every byte by independent FTP readback, then activated `boot`. Each owned
  parent marker matched; the temporary base permission was restored to 0755.
  New subtree directories use 0775 so native app UID1:GID0 can maintain files
  initially created by FTP UID0:GID0. No transaction/stage marker remained.
- The 460,521,472-byte 0.12 package passed 23-member extraction/hash checks,
  SFO checks and full FTP readback; SHA256
  `c11900e2d374323a8e1859ab28e57070aa2a55edde3b692c1e4cf99176ba4b7b`.
  It is staged at `/user/data/pkg/diagnostix-ram-alpha1-0.12.pkg`; native app
  installation was not performed. Seventeen focused launcher tests passed.
- One connection sent the 172,304-byte boot06 ELF SHA256
  `7a433a597c882bd78d4958a74d693695ced564d5170defd4f37d5d64e860a432`;
  GoldHEN received exactly that size and launched it. UART reported FW12.02,
  Baikal and VRAM1024. The kernel allocation succeeded. The initramfs native
  wired allocation returned KVA, but a `physical-page` guard refused backing;
  `Failed to allocate initramfs` and `cleanup_complete=1 result=12` followed.
- Verdict: **PRE_ARM_PHYSICAL_PAGE_REFUSAL**. No shutdown hook, GPU/pstate
  handoff or reboot ran. Orbis continued running. This is different from old
  contiguous allocation returning zero, and does not establish RAM exhaustion.
  The message combines zero/unaligned/non-E820 cases and lacks the rejected PA.
  The next diagnostic adds exact page logging in stage-only mode; no identical
  boot retry or blanket guard relaxation is justified.
- Capture continuity completed at bytes 1389445..1393549 (4,104 bytes), zero
  events. Context:
  `20261107_202048_126682-exp-20261107-030-versioned0-12-full-scatter-relocation-and-linux-ab172d0b.md`.
  Private receipts: `output/diagnostix-0.12-boot-20261107-030/`.

## EXP-20261107-031 — Native source page wrongly excluded by Linux destination map

- Objective: log the exact failed page with unchanged eligibility guards, same
  verified 0.12 inputs, and stage-only cleanup. Three-minute bound; no arming,
  GPU/pstate handoff or reboot. One 172,304-byte ELF SHA256
  `1dbe419abb65ff4c8ad42e41a6f0fa8f66efb3316246c2c5659848c2516c8f71`
  was sent on one connection and received at its exact size.
- The initrd native allocation returned KVA, then offset `0x18e18000` translated
  to physical `0x28000`; the other three 4 KiB translations were `0x29000`,
  `0x2a000`, `0x2b000`. Alignment and native-page continuity therefore hold.
  `ram_span=0` / `page-not-linux-ram` identifies the failed predicate.
- EXP025's native NORMAL segment zero spans `0x4000..0x2c000`, which includes
  that wired page. The inherited Linux E820 reserves `0x8000..0x80000`.
  The allocator guard had incorrectly required staging sources to be valid
  final Linux RAM. Native ownership and Linux destination eligibility differ.
  Cleanup completed with result12; Orbis continued running; no boot ran.
- Next change: retain strict Linux destination ranges and introduce a separate
  source predicate for already-owned wired buffers. Native NORMAL holes
  `0x8000..0x2c000` and `0x400000..0x480000` may qualify as sources. Preserve
  low handoff0..0x8000, ACPIe0000..f0000, VRAM, duplicate and destination-overlap
  protections, four-subpage translation checks and original-KVA cleanup.
  Both allocator and planner must use the source policy; the final E820 does
  not change. This is a targeted source correction, not a global RAM expansion.
- Capture continuity completed at bytes 1398031..1400472 (2,441 bytes), zero
  events. Context:
  `20261107_202913_813911-exp-20261107-031-exact-rejected-native-page-logging-same0-12-inp-38b3137e.md`.
  Private receipts: `output/diagnostix-page-guard-20261107-031/`.

## EXP-20261107-032 — Corrected sources staged; Linux and RAM root booted without USB storage

- Objective: test only the native source-page policy correction established by
  EXP031. Predeclared sequence: one stage-only preparation/free, then verify the
  corrected 0.12 files/package and send one boot-mode loader if staging passed.
  Setup bound15 minutes; three-minute handoff/stall bound; stop on refusal,
  terminal fault or capture loss. Recovery0.11/0.10 files retained; no automatic
  second boot. Owner confirmed USB storage disconnected and authorized running
  Diagnostix after the correction.
- ASan/UBSan loader checks passed, including independent source/destination
  callbacks, real allocator and actual `sys_kexec` regressions for physical
  `0x28000`, bounds/overlap/duplicate checks and the actual assembly copy loop.
  Native raw blobs have no dynamic relocations and include their full BSS.
  Independent Astra review found no new critical blocker; the primary verified
  the source and build evidence before sending.
- Stage-only06 ELF172304 SHA256
  `83868a65c4463a72d2e71b22c054f85b4f7c126bb851842889bab7f16c4402f9`
  passed full-size preparation: 27844 relocation records,41nodes,
  initrd444880734bytes, all five buffers freed, no arming or boot.
- The corrected boot08 ELF172304 SHA256
  `1d32ee28790eb4ca7ef26e26702af9562b9dcff671e9c87c492c768424e0d9e6`
  was bundled into app0.12. Native compilation, all23 package member readbacks,
  SFO0.12 and unchanged launcher checks passed. Package460521472bytes SHA256
  `996f83d28e5309e9c94afd9a5dd54508c078cb32b13e9e3a2202ea8ea2bcb37e`.
- With a transaction barrier, replaced only loader/manifest in the owned
  version directory; all four large/unchanged inputs remained in place. Full
  independent FTP readback of all six final boot files and the complete package
  matched. Temporary small backups and the superseded failing0.12 installer
  were removed after verification;0.11 installer and legacy boot0.11/rollback0.10
  remained. No journal or staging marker remained. Source-fixed package is at
  `/user/data/pkg/diagnostix-ram-alpha1-0.12.pkg`. An earlier0.12 package had been
  installed by the operator during EXP031; installation of this corrected
  replacement is not established by this direct-loader boot experiment.
- One boot08 connection sent exactly172304bytes; GoldHEN received that size.
  Preparation produced27844records/41nodes, installed the shutdown hook and
  completed the existing Orbis shutdown and terminal relocation. Linux
  `7.2.11-psx-ps4-baikal-uvd-runtime-dev6` started with `psx.boot=ram`;
  RAMDISK was at physical0x180000000..0x19a845fff. Linux E820 still reserves the
  added native source holes. AMDGPU reported1024MiB VRAM.
- `/init` ran at10.532s after Linux start. The verified root image began
  inflating into bounded RAM at27.434s. Loop0 mounted the RAM-backed ext4 root
  read-only at94.028s, UUID0d652fe4-a2f5-45f1-85c9-7ea26a52d67c. Systemd261.2
  started at111.653s and selected hostname `psx-diagnostics`.
- Verdict: **PASS_KERNEL_RAM_ROOT_MENU_PENDING**. This is one observed
  USB-storage-disconnected kernel/RAM-root boot. No kernel panic was observed.
  The visible/responsive menu, graphics operation, network/SSH/report export,
  UVD execution, power/reboot behavior and repeated-boot qualification remain
  unaccepted. Kernel Wi-Fi warnings/scans are separate observations, not loader
  failure or network acceptance. No further hardware action was taken.
- Capture continuity completed at bytes1405145..1504859 (99714bytes), zero
  events. Handoff/root initialization progressed within the planned window;
  the capture actually closed208s after boot send while waiting for a screen
  report. Record that observation duration rather than claiming an exact180s
  stop. Context:
  `20261107_203529_309250-exp-20261107-032-source-policy-correction-stage-only-validate-fr-b69a978d.md`.
  Private receipts: `output/diagnostix-source-policy-20261107-032/RESULT.json`
  and adjacent native/package builds, FTP readbacks and send receipts.


## EXP-20261107-033 — Optional modes/report update not delivered

- Objective: deliver the host-built 0.13 candidate and check mode choice and
  issue export, with the observed 0.12 boot retained as rollback. No updated
  package, payload or console boot file was sent during this session.
- The owner initially selected SSH delivery and supplied Linux address .95.
  TCP22 on the console refused the connection; the owner then reported SSH
  setup unavailable. Subsequent owner reports described Shutdown and Reboot
  controls as unavailable; no power dispatch was observed or accepted.
- The host's duplicate PKG copy reduced the Data filesystem below the UART
  logger's 512 MiB floor. Capture stopped at21:44:42; the duplicate was removed
  after the identical original package was verified. This was a host capacity
  failure caused by the extra copy, not a console or loader failure.
- Verdict: **ABORTED_INVALID_CONTINUITY_NO_UPDATE**. Closed21:45:16, bytes
  1513523..1825241 (311718bytes), events369bytes. The earlier completed EXP032
  boot evidence remains valid. A new continuous capture was independently
  established READY under generation9186213bb7d94e0ebef9181484a5cad2; no bounded
  experiment was opened by restarting that logger.
- Context: `20261107_213000_896898-exp-20261107-033-optional0-13-modes-report-update-return-orbis-v-45974f02.md`.
  Private capacity recovery: `output/diagnostix-modes-reports-20261107/UART-SPACE-RECOVERY.json`.
- Source follow-up confirmed the shipped RAM SSH helper retained native-dev3
  while its kernel was runtime-dev6, and the RAM image omitted the newer host-key
  service ordering. Both are corrected in the next host build. A separate RAM
  verifier defect rejected unrelated systemd automount stacks. Narrowing that
  rejection preserves all three guarded mount identities; the actual PS4 power
  refusal still needs runtime evidence and is not hardware acceptance.


## EXP-20261107-034 — Final 0.13 built and staged; installation and runtime tests pending

- Objective: stage the completed 0.13 installer and its isolated owned version
  folder, then read back every final byte. Ten-minute bound; stop on capture or
  transfer failure, retain 0.12 rollback. FTP became reachable after the owner
  returned to Orbis. No package installation or payload launch was performed.
- The fresh build includes explicit Automatic/Manual choice, issue-report ZIPs,
  corrected dev6 SSH/network helper pins, the current host-key service ordering,
  unrelated-systemd-mount tolerance with exact protected mount checks, and fixed
  startup availability reasons in the UI and issue report. The exact earlier
  PS4 power refusal is unproven; physical shutdown/reboot is not accepted here.
- Host checks passed: workspace51 tests, diagnostics819 Python tests, browser22,
  fresh TUI75, native FPKG17 and native loader9 checks. Independent streamed image
  inspection matched all29 frontend files and9 RAM assets, the actual dev6
  helper guards and the host-key ordering against the frozen source. Package
  extraction matched23 members and SFO0.13.
- Package460587008bytes SHA256
  `4f049b83ade856fa69058b7c603aac7b1af9a7484cb4ff64e2df16c987199ba7`.
  Initramfs444816511bytes SHA256
  `9224fd9215b3c63d7c5fb035629082a22ca765cbace98b92225664311ed1a7f6`.
  Fresh loader172304bytes SHA256
  `f68bd79e5b73915b6273a394f5203a15b9515ab82a1e6eb641a5630c49d6ee54`;
  its151516byte kernel blob remains identical to the observed 0.12 boot08 blob.
- Created only `/user/data/diagnostix/versions/0.13`, with an exact owner marker
  and transaction barrier. Uploaded all six files to `boot.new`; uploaded and
  verified the temporary installer, renamed it to
  `/user/data/pkg/diagnostix-ram-alpha1-0.13.pkg`, then published `boot`.
  Complete independent readbacks of all six final boot files and the final
  installer matched their expected size/SHA256. Required `vram.txt` is1024newline.
  Cleared the completed barrier; no staging folder or partial package remains.
  The 0.12 boot file listing and manifest bytes remained unchanged.
- Superseded local installers and compilation/extraction caches were cleared
  with receipts; source, evidence, pinned dependencies and a complete verified
  0.12 rollback package remain. No remote recovery generation was removed here.
- Verdict: **PASS_STAGED_FULL_BYTE_READBACK_INSTALLATION_PENDING**. Transfer
  work finished in334seconds; the bounded capture was22:17:38..22:25:10.
  Continuity completed, bytes145840..151856 (6016bytes), zero events. Compact
  context showed ordinary Orbis activity; no Linux or power test was attempted.
  New-image installation, mode/report operation, SSH access and actual power
  behavior still require their own coherent observed experiment.
- Context: `20261107_221738_647667-exp-20261107-034-stage-final-diagnostix-0-13-installer-and-owned-57239019.md`.
  Private receipts: `output/diagnostix-final-0.13-20261107/`.


## EXP-20261107-035 — Owner-launched 0.13 refused its relocation plan before Linux

- Retrospective diagnosis from continuous UART, not a predeclared bounded
  hardware marker. The owner installed and launched 0.13 after EXP034 closed,
  then reported that Diagnostix did not load and requested UART inspection.
  No agent-initiated payload, retry or power action was performed here.
- UART identifies `DGNX00001` app version0.13. Install/Verify accepted every
  packaged size/hash and reported Ready. Boot wrote all172304 loader bytes on
  one connection; GoldHEN launched that complete ELF. VRAM1024 and FW12.02
  were selected, and all five wired allocations completed.
- The planner then logged `DIAGNOSTIX_SCATTER_V1 refused error=7` at
  VA0xffffff807d7f0000, PA0x85fc000, followed by
  `DIAGNOSTIX_PAGES_V1 cleanup_complete=1 result=22`. No shutdown hook was armed
  and Linux was not entered. Error7 is `DIAG_SCATTER_CONFLICT`, not a bad RAM
  translation, corrupted input, missing VRAM file or a Linux userspace failure.
- Proven range: this kernel has init_size0x2793000, rounded workspace
  `[0x06000000,0x08794000)`. Its immediate protected-kernel writes cover
  `[0x06000000,0x06ac0000)`. The rejected16KiB page at
  `[0x085fc000,0x08600000)` lies only in the later decompressor workspace.
  All staging pages/descriptors are consumed before kernel entry, making that
  particular overlap harmless. The blanket workspace exclusion was too broad.
- Unrestricted wired allocation can also place data in actual copy targets;
  merely narrowing the check would leave a general placement-dependent failure.
  A bounded pre-arm safe-page replacement path is being implemented, with
  full workspace destination validation, original-page/translation/duplicate
  checks and the unchanged terminal copy loop retained. No unchanged retry.
- Verdict: **OBSERVED_PRE_ARM_REFUSAL_CLEANUP_COMPLETE**. This does not establish
  acceptance or a new completed continuity claim. Exact retrospective slice
  bytes166648..176958 (10310bytes), SHA256
  `1a3376e14234724df37d1a8096544de9f9347505342ac5dc78255245d6396007`,
  from generation9186213bb7d94e0ebef9181484a5cad2. One refusal appears in that
  continuous generation. Private context/receipts:
  `output/diagnostix-refusal-20261107-035/`.


## EXP-20261107-036 — 0.14 stage-only payload launched into a fault; 0.15 slim correction

- Objective: verify the corrected 0.14 source-policy package and run one
  stage-only preparation/probe. The 460,849,152-byte package, the six versioned
  boot files and the 401,680-byte stage-only ELF were uploaded and fully read
  back. No boot was requested.
- One connection sent all 401,680 bytes. GoldHEN saved that exact size and
  launched it into `ScePartyDaemon`; UART then recorded a user SIGSEGV at
  RIP0x894066, fault address0x1e0, rsp0x917ff8 and rbp0. No `DIAGNOSTIX_*`
  preparation marker appeared, and GoldHEN reported `failed to launch payload,
  error: 1`. Target process PID46 restarted as PID70. No Linux entry occurred.
- Source review attributes the regression to payload size: the 0.14 kexec
  build embedded its 361,208-byte zero-filled scatter BSS in the outer ELF,
  growing it from 172,304 to 401,680 bytes. GoldHEN received every byte, so
  this is not a TCP or package-transfer failure. The exact GoldHEN launch
  limit is not established, and returning from the stage-only `main` remains
  an untested separate hazard.
- Source correction (0.15, not a hardware result): embed only the 21,536-byte
  non-BSS kexec image, zero the BSS inside the kernel allocation, restore the
  four-byte upstream footer, and reconstruct the exact 382,748-byte runtime
  blob byte-for-byte. The outer loader is 41,320 bytes. The app handoff no
  longer rejects a loader larger than the reported `SO_SNDBUF`; the
  complete-write loop is the delivery contract. Host build and package
  extraction passed; no 0.15 console launch has been performed.
- Verdict: **0.14 OBSERVED_PAYLOAD_LAUNCH_SIGSEGV; 0.15 HOST_BUILD_READY,
  HARDWARE PENDING**. The 0.14 package is not recommended for installation.
  Context: `ps4-uart/sessions/20261107_230835_033048-exp-20261107-036-source-collision-correction-stage-verified0-14--04001a24.md`;
  receipt: `output/diagnostix-refusal-20261107-035/EXP036-RESULT.json`.


## EXP-20261108-002 — Obsolete Diagnostix FTP installers and generations removed

- Objective: delete only the verified obsolete Diagnostix 0.11, 0.13 and 0.14
  installers and the 0.13/0.14 versioned boot directories; preserve the 0.12
  known-good Linux rollback and the current 0.15 installer. No payload,
  installation or boot was included.
- Deleted installers: 0.11 460,390,400 bytes, 0.13 460,587,008 bytes and 0.14
  460,849,152 bytes. Deleted boot sets: 0.13 (172,304-byte loader) and 0.14
  (401,680-byte loader), each with the six fixed members, `.owner` and `boot`
  directory. Combined reclaimed payload/directory bytes: 2,294,582,332.
- Preserved: `/user/data/pkg/diagnostix-ram-alpha1-0.12.pkg` 460,521,472 bytes,
  `/user/data/pkg/diagnostix-ram-alpha1-0.15.pkg` 460,455,936 bytes and the
  0.12 version directory. Unrelated Omarchy, Store, Cobra and personal files
  were not touched. No permission changes were needed.
- First attempt deleted the three installers, then stopped before version
  deletion because the host compared NLST basenames with full paths. The
  continuation re-read each `bundle.json`, verified the exact six-member boot
  set and `.owner`, then completed deletion. It did not retry an unchanged
  destructive step.
- UART session completed with a 496-byte slice and zero events; the storage
  result comes from the FTP receipt `/var/tmp/ftp-cleanup-015.json`, not from
  silent UART. Continuity generation `7143910ecd1048809c9175112d1ad858`.


## EXP-20261108-004 — GoldHEN active payload replaced with downloaded v2.4b18.9

- Objective: extract the owner-downloaded `GoldHEN_v2.4b18.9.7z`, verify the
  contained `goldhen.bin`, replace `/user/data/payload.bin`, and preserve the
  previous v2.4b18.10 payload. No payload execution, boot or console reboot was
  performed by the agent.
- Archive contained six files; `goldhen.bin` is 291,808 bytes, SHA256
  `ab1849d66816a9f4a3d155b06b51cdc5eb07a7fc5bd4333c90e3af74f802b2b2`, identical
  to the existing PSFree Enhanced v2.4b18.9 reference copy.
- Previous active payload was v2.4b18.10, 290,016 bytes, SHA256
  `c6329401d1810e16c84e6474ac30977dbdc951987c10cdb559370de7d59db0b0`.
  It was retained as `/user/data/payload.bin.2.4b18.10` and as the named copy
  `/user/data/payloads/goldhen_v2.4b18.10.bin`.
- Uploaded the new payload to a temporary name first, verified its complete
  readback, then renamed the old payload and activated the new one. Final
  `/user/data/payload.bin` is 291,808 bytes with the expected SHA256. A named
  copy is at `/user/data/payloads/goldhen_v2.4b18.9.bin`.
- UART session completed with zero bytes; the storage result comes from the FTP
  receipt `/var/tmp/replace-goldhen.json`. Reboot/reload is an owner action.


## EXP-20261108-005/006/007 — GoldHEN v2.4b18.9 reload and first real 9090 probe

- EXP-005: the owner reloaded the replaced GoldHEN v2.4b18.9 payload. UART
  recorded `Installing GoldHEN in Kernel...`, `All done!` and `<payloader>
  Server started at 9090 port`. The active `/user/data/payload.bin` is the
  downloaded 291,808-byte v2.4b18.9 file, SHA256 `ab1849d6...`.
- EXP-006 negative finding: an empty TCP port check to 9090 caused
  `<payloader> Error handling payload` and the service stopped; the subsequent
  real probe was refused. Do not use an empty connect as a readiness check.
- EXP-007: after PayLoader was re-enabled, one real 16,240-byte read-only
  pool-observer ELF was sent on one connection. GoldHEN reported `payload
  launched successfully`; UART returned the complete `PSX_POOL_V1` report with
  `status=0`, `reads=253`, `no_boot=1` and `orbis_remains_running=1`. GoldHEN
  then closed the listening socket (`Server socket closed, 0x80410109`).
- This proves 9090 transport and the v2.4b18.9 ELF path for the small probe.
  It is not a Diagnostix boot result. Receipts: `/var/tmp/probe-9090-007.json`
  and `/var/tmp/probe-9090-006.json`; UART contexts in
  `ps4-uart/sessions/20261108_014144_041658-exp-20261108-007-*`.


## EXP-20261108-008 — 0.15 slim loader direct-send boot reached Linux and RAM root

- Objective: send the exact FPKG-prepared 0.15 loader once from the host after
  the app's successful Prepare, and observe Linux/RAM-root startup. PayLoader
  was re-enabled; one connection carried 41,320 bytes, SHA256
  `bf030db3c3c7f7668947074e0d8eca29d604a5bf1fc3cbbda677ffc6970ee5d6`.
  No retry and no app Boot-button action.
- GoldHEN received and launched the ELF. UART recorded `kernel_init()`,
  `sys_kexec`, successful wired noncontiguous `DIAGNOSTIX_PAGES_V1`
  allocations for the 11,273,216-byte kernel and 444,947,617-byte
  initramfs-plus-firmware request, firmware extraction and Linux
  `7.2.11-psx-ps4-baikal-uvd-runtime-dev6`.
- Milestones: `Run /init` at 10.518384 s; `loop0` capacity change at
  93.844443 s; ext4 root UUID `0d652fe4-a2f5-45f1-85c9-7ea26a52d67c` mounted
  read-only at 93.854327 s; systemd hostname `psx-diagnostics` at 111.477167 s;
  `Reached target Login Prompts` at 111.994828 s. No kernel panic or Oops was
  recorded. The owner reports the appliance loaded.
- Evidence scope: the loader and all six boot files are the exact contents the
  0.15 FPKG app prepared under `/data/diagnostix/versions/0.15/boot`. This
  validates the payload, kernel, initramfs, boot arguments and VRAM path. It
  does not validate the app's own Boot-button handoff, repeated boots, menu
  interaction, networking, graphics, UVD, export or power behavior.
- UART session completed with 100,423 bytes from generation
  `7143910ecd1048809c9175112d1ad858`, zero events. Receipt:
  `/var/tmp/send-015-loader-008.json`; context:
  `ps4-uart/sessions/20261108_014513_884727-exp-20261108-008-0-15-first-real-loader-send-user-re-enables-pay-0a9e867c.md`.


## Follow-up after EXP-20261108-008 — 0.15 FPKG app Boot path succeeded

- After the direct-send boot, the owner launched Diagnostix 0.15 and selected
  **Boot Diagnostix** itself. Continuous UART capture (no new bounded marker)
  recorded `[diagnostix] action: Boot selected`, the verified-snapshot phase,
  `[diagnostix] handoff: all 41320 loader bytes written on one connection`, and
  `[diagnostix] result: Loader sent once.`
- A second Linux boot followed: `Run /init` at 10.271769 s; `loop0` capacity
  change at 93.491616 s; ext4 root mounted read-only at 93.501589 s; systemd
  hostname `psx-diagnostics` at 111.153802 s; `Reached target Login Prompts` at
  111.677004 s. No kernel panic or Oops was recorded.
- This is continuous-capture/operator evidence for the FPKG app's own
  `Bundle::boot` handoff, not a bounded session. It validates the 0.15 app
  Boot path and payload on hardware. It does not establish repeated-boot
  support, menu interaction, networking, graphics, UVD, export or power
  acceptance.


## EXP-20261108-009 — Accelerated 0.15+TUI package uploaded and fully read back

- Objective: upload only the new zstd-root/TUI package to
  `/user/data/pkg/diagnostix-ram-alpha1-0.15-accel.pkg`, complete a full FTP
  readback, and preserve the existing 0.12 and 0.15 installers. No installation,
  payload, boot or remote deletion was included.
- Upload and complete readback both returned 424,411,136 bytes with SHA256
  `2ed980321aca131cb81721cf6ae736397c7621d48943aab5df0fc81953ff0d1f`.
  The final directory contains the 0.12, 0.15 and 0.15-accel installers.
- The package carries the 408,688,296-byte initramfs with the zstd root leaf,
  static decoder, new TUI (`Run all tests`, `Q Power`, `Inconclusive` label)
  and the zstd BSD notice. It remains a host-built candidate; console
  installation, Prepare and Boot are pending.
- UART session completed with 6,212 bytes and zero events; the storage result
  comes from the FTP receipt `/var/tmp/upload-accel.json`. Continuity generation
  `7143910ecd1048809c9175112d1ad858`.


## Follow-up after EXP-20261108-009 — accelerated 0.15 reached BusyBox rescue; init corrected

- The owner installed the accelerated 0.15 package and selected **Boot
  Diagnostix**. UART recorded the new RAM init reaching the root inflation:
  `[diagnostix-ram-init] Inflating one verified root image into bounded RAM.`
  followed immediately by
  `[diagnostix-ram-init] STOP: RAM root gzip decompression failed.` and
  `[diagnostix-ram-init] RAM appliance was not started. Rescue shell; no
  automatic retry.`
- Cause: `/init` probed the compressed root with `dd ... | od -An -tx1`. This
  rescue BusyBox has no `od` applet, so the magic string was empty and the
  script silently fell back to `gzip -dc` on the zstd leaf. The 1,000 MiB root
  was never inflated or mounted; the rescue shell changed no storage.
- Correction: `/init` now dispatches on the embedded
  `ROOT_COMPRESSION='zstd'` contract from `/etc/psx-ram.env` and has no `od`
  probe and no silent fallback. The corrected initramfs is 408,688,280 bytes,
  SHA256 `ce0ed5ea9f97e28707e7d67065df4d2978508beeb5a50388261f69f379721563`;
  the corrected package is 424,411,136 bytes, SHA256
  `71bb60b543cc23ef889f254ac735bc6f2dbdbd9fd89c9378fe2da4647fbb7c30`.
- Host verification: the extracted `/init` contains the env dispatch and no
  `od`; `/etc/psx-ram.env` pins `ROOT_COMPRESSION='zstd'`; `bin/zstd-dec` is a
  static x86-64 executable and inflates the packaged leaf to the expected
  1,048,576,000-byte root SHA256
  `b5b3a0ec3001fa99816ad68071d93091a6c8d0907aeb4ba384094625b7af0107`; the
  FPKG readback contains the corrected initramfs pin with 24 members.
- Scope: host build and readback only. The corrected package still needs
  console installation, Prepare and a bounded Boot session; the zstd decoder
  has not yet run on the PS4. The console remained in the BusyBox rescue shell
  and must be rebooted to Orbis before the next upload. No retry of the failed
  image is authorized.


## EXP-20261108-010 — Corrected accelerated 0.15 package replaced over FTP

- Objective: replace the superseded accelerated 0.15 installer with the
  corrected `ROOT_COMPRESSION` initramfs and verify a complete remote readback.
  No installation, Prepare, payload or boot was included.
- The corrected local package is 424,411,136 bytes, SHA256
  `71bb60b543cc23ef889f254ac735bc6f2dbdbd9fd89c9378fe2da4647fbb7c30`. It was
  uploaded to
  `/user/data/pkg/diagnostix-ram-alpha1-0.15-accel.pkg.partial` in 31.9 s at
  13,305,126 B/s. A streaming FTP readback returned exactly 424,411,136 bytes
  with the same SHA256; only then was it renamed over the existing
  `diagnostix-ram-alpha1-0.15-accel.pkg`.
- Final remote size is 424,411,136 bytes. The 0.12 and original 0.15
  installers remain in `/user/data/pkg`. No partial file remains.
- UART session completed with 1,697 bytes from generation
  `7143910ecd1048809c9175112d1ad858` and zero events; output was ordinary
  Orbis ShellCore memory statistics, so the console stayed in Orbis throughout
  the transfer. Receipt: `/var/tmp/upload-accel-010.json`; context:
  `ps4-uart/sessions/20261108_024121_462830-exp-20261108-010-upload-corrected-0-15-accel-package-and-full-re-feb28559.md`.
- Next: install the package, launch Diagnostix 0.15, select **Prepare
  Diagnostix**, then Boot under a new bounded UART session.


## Follow-up after EXP-20261108-010 — SHA-256 bottleneck measured; 8-way hash candidate built

- The corrected zstd package booted successfully: `/init` at 10.503911 s,
  inflation at 25.889703 s, `loop0` at 73.479360 s, ext4 mounted read-only at
  73.489382 s, hostname at 91.095268 s, login prompts at 91.612962 s. Total to
  login was 91.6 s, about 20 s faster than the 111.7 s gzip boot.
- The remaining inflation is 47.6 s. Comparing the gzip and zstd pre-inflate
  phases (402 MB versus 364 MB over 16.5 s versus 15.1 s) gives roughly
  26 MB/s for single-threaded SHA-256, so the two passes cost about 15 s
  (compressed 364 MB) and 38.5 s (inflated 1,000 MB); zstd decode is only
  about 9 s. SATA is not involved: `ata1`/`ata2` finish before `/init`, and the
  root is a loop mount over tmpfs.
- The next init removes the redundant compressed-leaf SHA-256 pass and
  verifies the inflated root as eight 125 MiB SHA-256 chunks, run concurrently
  with one process per CPU. The eight digests are pinned in
  `/etc/psx-ram.env` as `ROOTFS_CHUNK_SHA256`; `ROOTFS_CHUNK_COUNT=8` and
  `ROOTFS_CHUNK_MIB=125`.
- The parallel-hash initramfs is 408,279,269 bytes, SHA256
  `40e59e5986c94588a3d0037b99942c167cd2fc47a8ca6230c51bb2afd23c8e6e`. The
  package is 423,952,384 bytes, SHA256
  `4c49ba0b1d884846263895e5c9a7134881e8006c968c62d06957112dba4ef0bc`, 24
  members; FPKG readback contains the new initramfs pin and all payload bytes
  match.
- Host checks: `/bin/sh -n` passes, and the exact chunk loop verified the real
  1,000 MiB image under the host shell and `sh`. Expected inflation is about
  15 s and total boot roughly 45-50 s, pending console measurement.
- The package has not been uploaded or booted. The console must return to
  Orbis before the next FTP transfer and bounded Boot session.


## EXP-20261108-011 — 8-way hash 0.15 package uploaded and fully read back

- Objective: upload the 8-way chunk SHA-256 package and verify a complete
  remote readback. No installation, Prepare or boot was included.
- The local package is 423,952,384 bytes, SHA256
  `4c49ba0b1d884846263895e5c9a7134881e8006c968c62d06957112dba4ef0bc`. It was
  uploaded to
  `/user/data/pkg/diagnostix-ram-alpha1-0.15-accel8.pkg.partial` in 32.9 s at
  12,903,805 B/s. A streaming FTP readback returned exactly 423,952,384 bytes
  with the same SHA256; it was then renamed to
  `diagnostix-ram-alpha1-0.15-accel8.pkg`.
- Final remote size is 423,952,384 bytes. The 0.12, original 0.15 and previous
  corrected 0.15-accel installers remain in `/user/data/pkg`. No partial file
  remains.
- UART session completed with 8,768 bytes from generation
  `7143910ecd1048809c9175112d1ad858` and zero events. Output was ordinary
  Orbis ShellUI scene activity, so the console stayed in Orbis throughout the
  transfer. Receipt: `/var/tmp/upload-accel8-011.json`; context:
  `ps4-uart/sessions/20261108_025530_398557-exp-20261108-011-upload-8-way-hash-0-15-accel8-package-and-full--7a987b54.md`.
- Next: install the package, launch Diagnostix 0.15, select **Prepare
  Diagnostix**, then Boot under a new bounded UART session.


## Follow-up after EXP-20261108-011 — 8-way hash package boot measured at 61.1 s

- The owner installed the 8-way hash package, selected **Prepare Diagnostix**
  and booted once. No bounded session marker was started for this boot; the
  result is continuous-capture/operator evidence, not bounded acceptance.
- Milestones: `Run /init` 10.661233 s; SATA identity message 11.000750 s;
  inflation 17.295964 s; `loop0` 42.961283 s; ext4 mounted read-only
  42.970901 s; hostname 60.589990 s; login prompts 61.104250 s.
- Inflation is 25.67 s (previous single-pass zstd 47.6 s; gzip 66.4 s). Total
  to login is 61.1 s (previous 91.6 s; gzip 111.7 s), a 30.5 s and 50.6 s
  improvement respectively. No decompression, checksum, mount, kernel or
  systemd fault was recorded.
- The eight SHA-256 jobs reduced the 1 GiB integrity pass from about 38.5 s to
  about 16.7 s, roughly 2.3x rather than linear 8x scaling. The remaining
  inflation is about 9 s zstd decode plus 16.7 s integrity verification.
- Scope: one boot of this image. It validates the chunk contract, zstd
  decoder, loop mount and boot timing on this console. It does not establish
  repeated-boot support, TUI interaction, networking, graphics, UVD, browser,
  report export or power acceptance.


## Follow-up after the 8-way hash boot — UI2 flat Tests screen and non-stopping Run all

- Why the UI felt complex: the TUI was built as a qualification harness with
  ten area tabs, nested reports/settings/power trees and per-area setup menus.
  The catalogue has only 18 base tests plus browser/UVD extras, so most tabs
  were setup and observation rather than tests.
- Why Run all was not full: it stopped on the first FAIL, BLOCKED,
  UNSUPPORTED, NOT_RUN or non-observation INCONCLUSIVE; it excluded tests that
  had failed previously and `uvd.avc`; and advanced tests remain gated by the
  controller's CPU/RAM prerequisites. Tests marked unavailable, deferred or
  blocked in the catalogue were never runnable by design.
- UI2 replaces the System action menu with one flat test list: every test shows
  a status glyph, area, kind, id, description and latest message. Manual,
  deferred, unavailable and blocked tests stay visible with their reason.
- Run all now continues past failures and records passed/failed/observed
  counts; only a terminal `stop_required` result or a lost controller stops it.
  It still does not retry a previously failed test without changed evidence.
- The new TUI binary is 1,098,584 bytes, SHA256
  `a82eb0c6fbbfc1af257d421a3df60e5b4816467fb6af2a54bc9537bd8ec53b34`. The
  initramfs is 408,290,471 bytes, SHA256
  `adcbea0c1ef3728c13c56addbf49cded6480f344b8bf9f3acc0ded4def4caf44`; the
  package is 424,017,920 bytes, SHA256
  `124a7d4f451b87b1514ea483575fb3b2e387de653aa38d501b54b8c0d42ba440`,
  24 members. FPKG readback matches every payload byte.
- Rust TUI tests: 78 pass. The package has not been uploaded or booted; the
  console was in the Linux appliance and Orbis FTP was unavailable.


## Follow-up after UI2 — UI3 core-first logical order

- The UI2 package was not booted. While checking the order, the full queue was
  found to enqueue the browser launch before Network, Audio and Storage; since
  Run all stops after the browser launch, those later tests never ran. UI3
  removes the browser from the area loop and appends it only after the issue
  report.
- The flat Tests list and Run all now share one explicit order: hardware
  inventory, CPU, RAM, Ethernet, OpenGL/Vulkan information, reference renders,
  audio, storage, UVD, issue report, browser last. Manual, deferred and
  unavailable tests remain listed after the runnable ones.
- The new TUI binary is 1,098,584 bytes, SHA256
  `ded26ba78040eff7639eeb3d2293071fb987672ab28d15e34f8f0bef9490670f`. The
  initramfs is 408,292,149 bytes, SHA256
  `0810cb39de6c0c6ca7d57601f2aa0752b476d82108884439dd5539bdd9a60acd`; the
  package is 424,017,920 bytes, SHA256
  `0b6dad1dd68889159c4b880d1f00b0193d3756375295f5f2881a82249335797e`,
  24 members. FPKG readback matches every payload byte.
- Rust TUI tests: 78 pass. The package has not been uploaded or booted; the
  console remained in the Linux appliance and Orbis FTP was unavailable.


## Follow-up after UI3 — UI4 run controls and report handling

- The issue report is no longer opened automatically when `support.export`
  finishes during a full run. The run continues to the browser and ends with a
  summary instead of a forced report view.
- During a run: **C** or **Esc** cancels the queue, **S** writes
  `/run/psx-state/full-test-report.txt`, and **V** shows the run summary.
  Cancelling stops further tests; an in-flight test is allowed to finish and
  its result is not queued.
- When the queue ends, a **Full test summary** opens with passed/failed-skipped/
  observation counts, notes and the saved path. **R** retries the full run only
  after a required reason, **S** saves the report, and **Esc** closes.
- The new TUI binary is 1,114,968 bytes, SHA256
  `b48a07e1775711df1746ffd259bea8c819d8ac4c895c9d21af2d1d6e143ccc75`. The
  initramfs is 408,297,608 bytes, SHA256
  `be4af856a7f2e6487810f5af6479fb41280c30b6aa211d55c723c69be534c135`; the
  package is 424,017,920 bytes, SHA256
  `1d7beb40b7bed84f2abe2fa3acdb6434c0fe0497b3a3a5a0d43dbf8d9dac8cbf`,
  24 members. FPKG readback matches every payload byte.
- Rust TUI tests: 79 pass. The package has not been uploaded or booted; the
  console remained in the Linux appliance and Orbis FTP was unavailable.


## EXP-20261108-012 — UI5 setup-first package uploaded and fully read back

- UI5 makes **Setup the first screen**: Ethernet, Wi-Fi and optional SSH,
  followed by **Start all tests**. Start leaves Setup and launches the full
  suite automatically.
- Every result is appended live to
  `/run/psx-state/full-test-report.txt` during the run. The summary adds **U**
  to open or create the issue-report ZIP for submission; Cancel, Save, Retry
  and the terminal-only stop remain.
- The new TUI binary is 1,114,968 bytes, SHA256
  `99ed97442a58af33f96c6c63fae1405b499689d72429ee4f33271990fccf7763`. The
  initramfs is 408,298,384 bytes, SHA256
  `a55de3d6e8839ab39600c7837498e8f83703e6eb50f3c9af7d3b96da717ba289`; the
  package is 424,017,920 bytes, SHA256
  `04b302ae661053aa4a5d43eacfbe4f38bb7e1ab821124e2c36cfe54612006f5e`,
  24 members. FPKG readback matches every payload byte.
- Upload to `/user/data/pkg/diagnostix-ram-alpha1-0.15-ui5.pkg.partial` took
  90.3 s at 4,693,290 B/s. A streaming readback returned exactly 424,017,920
  bytes with the same SHA256; it was renamed over the final name. The 0.12,
  0.15, 0.15-accel and 0.15-accel8 installers remain.
- UART session completed with 4,116 bytes from generation
  `7143910ecd1048809c9175112d1ad858` and zero events. Receipt: `/var/tmp/upload-ui5-012.json`.
- Rust TUI tests: 79 pass. Next: install, **Prepare Diagnostix**, Boot; expect
  Setup first, then Start, then the live report and summary. The console is
  still in the Linux appliance.

## EXP-20261109-001 — Run3 dev package uploaded; old installers removed; launcher-owned sets refused

- Objective: upload the logo build, verify it, then remove the previous
  DiagnostiX versions at the owner's request. No install, payload or boot.
- The package carries owner SSH keys and stays private. It is 435,617,792 bytes,
  SHA256 `205295c3f2d6ecaf576cf8455a2dbaceaa3e93cff424dcf493b725809e742454`.
  Upload to a `.partial` name took 32.8 s; a streaming readback returned every
  byte with the same SHA256 in 45.5 s, then it was renamed to
  `/user/data/pkg/diagnostix-ram-alpha1-0.15-run3-dev.pkg`.
- Removed the 0.12, 0.15, 0.15-accel, 0.15-accel8, 0.15-ui5 and 0.15-ui8
  installers and the 0.12 boot set (3,073,609,468 bytes). Same-size installer
  copies stay on the host under `/var/tmp`.
- `DELE` returned 550 for all 42 members of the seven `boot`/`previous-*` sets
  in the launcher-owned `0755` directories. The script did not retry them. It
  also held back the leftover `versions/0.12` marker, because its gate required
  no failures at all.
- UART session completed, 541 bytes, zero events; generation
  `c4e344d9667c4aa8b2889bea5bef9210`. Storage effects come from the FTP
  receipt `/var/tmp/upload-run3-dev-20261109.json`, not from UART.

## EXP-20261109-002 — Remaining old DiagnostiX boot sets removed

- Changed variable after EXP-20261109-001's 550 refusals: the
  EXP-20261107-029 procedure, group write (`0775`) on `/user/data/diagnostix`,
  `/user/data/diagnostix/versions/0.15` and each target. Stop at the first error.
- Every target was re-identified first: exact six members, manifest product and
  version, and each `previous-*` name equal to its manifest SHA-256. Removed
  0.11 and 0.10 in the old unversioned location, five retained 0.15 generations
  and the empty `versions/0.12` (3,047,545,370 bytes; 6,121,154,838 bytes across
  both sessions). No error occurred.
- Both parents were restored and confirmed `drwxr-xr-x`. Kept: the active
  `versions/0.15/boot` (UI8, manifest SHA `904fc2f9f604…`), all `.owner` markers
  and both launcher logs. Prepare on the new package will retire that active set
  to `previous-<manifest SHA>` itself.
- `linux-psx-diagnostics-usb-v25.bin` under `/user/data/payloads` and
  `/user/data/linux/boot` were left alone: they are not versions of this app.
  `/user/app/DGNX00001` was already absent, so the next install is fresh.
- UART session completed, zero bytes, zero events. Receipt:
  `/var/tmp/cleanup-run3-002-20261109.json`. Not installed or booted; nothing
  about this build is verified on the console yet.

## EXP-20261109-003 — Run3 dev first boot: graphics job hang at the end of glmark2

- Objective: first launch and Linux boot of the logo/3D-benchmark build. Operator
  installed, launched and chose Automatic test.
- Observed: launcher copied and activated the new set, one loader send, kernel at
  0 s, root in RAM by 46 s with benchmark tools and owner keys, first display
  session at 88.76 s. The launcher showed southbridge and firmware as Unknown.
- Terminal event: `PS4_UVD_RUNTIME CLOSED error=-110` at 267.14 s, then black
  screen with a cursor; Ctrl+Alt+Fn did nothing. The kernel stayed alive (USB
  events to 480 s, last line 674 s). The operator held power at about 14:00.
- Reading of the source: `amdgpu_job_timedout` calls the UVD runtime fault for a
  timeout on **any** ring and returns with no reset, before the normal `ring
  timeout` message. The timeout is 2000 ms, so a job stalled from about 265 s.
  That is 176 s after the display session began: the glmark2-to-vkmark boundary
  by timing only. UVD was given no work in this run. The close is a consequence,
  not evidence of decoder damage. Which job hung is **not established**.
- Identity: booted bzImage SHA256 `a7264426…3bff4` equals the published beta2
  `7.2.11-psx-ps4-baikal-uvd-runtime-dev6`; all 41 image modules are byte-equal
  to its release; Mesa manifest `687af5a3…` is dev5. UVD startup lines match four
  earlier boots of this kernel, so the changed variable is the benchmark.
- Patch audit against the shipped source: 61 of 77 line-for-line. Of the rest,
  the ICC power-off, retained-restart and AHCI patches were read and are replaced
  by later ones; the other 13 were not read individually. `icc_power_off`,
  `icc_restart` and the UVD poweroff/restart paths are built and registered.
  Software power paths were not exercised; none is recorded on exact dev6.
- UART session completed, 116,775 bytes, zero events. SSH was unreachable, so no
  userspace log or benchmark result was retrieved. No acceptance claim. Do not
  repeat this workload unchanged: the next run needs a changed variable.

## EXP-20261109-004 — Run4 dev boot with traces: benchmark not reached, SSH did not start

- Objective: upload, install and boot the build with UART traces, glmark2 alone
  and early Ethernet for SSH. Upload read back byte for byte; operator installed
  and chose Automatic test with no USB stick and a keyboard.
- Launcher: firmware read as 12.02. `hw.sce_subsys_subid` returns `errno=1`
  from an installed app, so the southbridge stays Unknown there; the loader
  still checks it. `kern.sdk_version` also answers, `machdep.upd_version` is
  refused and `kern.upd_version` does not exist.
- `debugfs=on` booted without incident. All `dx/` traces reached UART. Processor,
  Memory, OpenGL, Vulkan and Ethernet passed; Wi-Fi saw 22 networks.
- Browser showed FAIL with "baseline completed": all five steps finished and
  none failed. `browser.capabilities` is always INCONCLUSIVE, so the batch can
  never be PASS, and the screen treats that as FAIL. A labelling bug.
- **The benchmark did not run.** A Circle press arrived 5 ms after "Before the
  last tests" appeared and skipped it, so the earlier hang was not retested.
- SSH: `sshd -t` returned 1 at controller start and TCP22 on the console
  refused. A missing host key gives the same code here, and the key is only made
  when sshd starts. That cause is inferred, not observed on the console.
- After Ctrl+Q the root shell showed nothing: the service sends stderr to the
  journal and bash writes its prompt and typed text there. Reproduced on the host.
- Source review found a benchmark placed after Video decoding could never run,
  because a decoder PASS always requires a stop. Run6 orders them the other way.
- UART session completed, 524,358 bytes, zero events. No acceptance claim. Run5
  and run6 were built afterwards and have not been booted.

## EXP-20261109-005 — Old run packages and the run3 boot set removed

- Operator asked to clear old versions with FTP up. Removed by exact name after
  checking sizes and manifests: the run3 and run4 packages and the retired run3
  boot set, 1,302,389,926 bytes. Used the EXP-20261107-029 temporary mode
  procedure; `versions/0.15` was restored and confirmed `drwxr-xr-x`.
- Kept: the active run4 boot set, the retired UI8 set the owner chose to keep,
  logs and `.owner` markers. Receipt: `/var/tmp/cleanup-run5-20261109.json`.
  The session stays open for the next upload, install and launch.

Run7 continuation of the same session (package SHA256 `51fa215c…`, upload and
full readback receipt `/var/tmp/upload-run7-20261109.json`):

- Owner installed and launched run7. Kernel `7.2.11-psx-ps4-baikal-uvd-runtime-dev6`.
  SSH as root reached the console, so the host-key ordering fix held on hardware.
- First pass: the Picture, Controller and "Before the last tests" questions timed
  out with no press recorded, which skipped the benchmark and decoder.
- **Run tests again worked** (second pass, same boot). Browser showed PASS with
  capabilities INCONCLUSIVE and Canvas, WebGL, animation and video PASS. Picture and
  Controller (USB) passed. Sound stayed NOT_RUN: no 44.1 kHz fixture.
- **3D benchmark passed with no hang:** glmark2 score 2871, 180 s, exit 0, zero GPU
  fault lines, 76 °C peak, screen responsive afterwards. One pass; EXP-003's hang
  did not recur, and its cause stays unidentified.
- **Video decoding BLOCKED** before any decoder opened: `uvd.py` needs
  `/run/psx-diagnostics/uvd-session.json`, a lab record no public run has. A
  read-only probe found the pinned inputs and firmware correct, no attempt marker,
  the UVD queue ready and zero terminal-fault lines, so that file was the only
  blocker. No decode was attempted on this boot.
- Not exercised: the visible root shell after Ctrl+Q, restart and shutdown on dev6.
- The root image has no `tar`; logs were pulled with Python's tarfile instead.
  Private copy: `/var/tmp/diagnostix-run7-logs-20261109/`.
- UART session completed. No acceptance claim. Session:
  `20261109_230727_901155-exp-20261109-005-clear-old-diagnostix-packages-and-the-run3-boot-48308b76`.

## EXP-20261110-001 — Canonical UVD decode passes on the RAM image after the benchmark

- One `uvd.scenario canonical-120` over SSH on the running run7 boot, after the
  CPU, memory, OpenGL, Vulkan, browser and 180 s glmark2 tests of the same boot.
  Changed variable from the earlier BLOCKED attempt: the same-boot session record
  now existed, written by hand and naming this bounded UART session.
- **PASS:** `PASS_EXACT_NV12`, all 120 forced-VAAPI NV12 frames matched the pinned
  reference. Decoder 0.6 s, job 6.4 s, return code 0. Zero terminal-fault lines,
  no `PS4_UVD_RUNTIME CLOSED`. SSH stayed up and the screen stayed on tty1.
- The one-attempt marker was reserved, and the boot is closed to further active
  tests as designed. `hardware_acceptance` stays false: one pass, one console.
- Not exercised: run8's self-written record (same decoder path, different record),
  software restart or shutdown after the decode.
- UART session completed. Private evidence: `/var/tmp/diagnostix-exp-20261110-001/`.
  Session:
  `20261110_001345_800339-exp-20261110-001-one-canonical-120-frame-uvd-decode-on-the-runni-ff2350fc`.

## EXP-20261110-002 — run9 boots with Chromium in place of Firefox; the browser test never starts it

- run9 (SHA256 `1ff375a7…`) uploaded, installed and booted once. The trimmed root,
  boot chain, `tar` on PATH and removal of Firefox all held. The Chromium pack
  expanded in 18.3 s and its loop mount and configuration were correct.
- **Browser FAIL, before Chromium ran:** the launcher runs as psx and could not
  read the root-private `/etc/psx-diagnostics/boot-set.json` (`[Errno 13]`). The
  batch reached its deadline at 196.8 s with no steps, and every later test showed
  BLOCKED by the browser stop. Nothing about the GPU or Chromium was learned.
- UART session completed. No acceptance claim. Session:
  `20261110_005104_148685-exp-20261110-002-run9-upload-install-and-one-automatic-boot-chro-80169010`.

## EXP-20261110-003 — Chromium runs with the GPU on, using the launcher fix in RAM

- Same boot as EXP-20261110-002. Changed variable: `launch-chromium-provider` and
  `prepare-chromium-provider` replaced in `/run/psx-frontend` (RAM only), SHA256
  `93bb7177…` and `d1b85401…`, so root passes the checked Mesa pin to the launcher.
- The controller refused `graphics.launch`, as designed, because it still held the
  failed batch. That record was left alone. `launch-graphics --automatic chromium`
  was run directly as root over SSH, under the 120 s runtime limit.
- **Launcher passed as psx:** provider remounted by the new prepare step, service
  active, launch receipt written, `/lab` served and identified "Chromium family".
- A second window with the same flags plus a debug port reported WebGL 2 on
  `ANGLE (AMD, AMD DG1401SNF87ID (radeonsi liverpool ACO), OpenGL 4.6)` with a
  correct green read-back, and H.264 and VP9 reported as supported.
  `chrome://sandbox`: namespace and seccomp-BPF, "You are adequately sandboxed."
- Zero GPU fault lines in the kernel log or UART. The service stopped cleanly and
  the screen returned to tty1.
- Not exercised: the five-step automatic batch, which needs a fresh boot. The
  per-process seccomp sampling matched no processes (its command-line filter was
  wrong), so sandbox evidence is `chrome://sandbox` on the second window only.
- Open: Chromium logged Google messaging registration errors despite
  `--disable-background-networking`, so it tries to reach Google when online.
- UART session completed. No acceptance claim; one run on one console.

## EXP-20261110-004 — run10 uploaded, never booted

- run10 (SHA256 `498075c2…`) uploaded and read back byte for byte. The UART logger
  had stopped, and the upload script did not wait for the session to open, so the
  upload ran outside any bounded session. The read-back is its evidence.
- The launcher was opened once on run10 and no boot was chosen. Superseded by run11.
- UART session completed. Session: `20261110_023348_134497-exp-20261110-004-run10-install-and-one-automatic-boot-five-brows-72c89daa`.

## EXP-20261110-005 — run11: GoldHEN's answer misread as an error

- Changed variable: the launcher reads the southbridge under GoldHEN root, through
  the public SDK entry (syscall 500). A detection payload was rejected because port
  9090 closes after one payload and the loader needs it (EXP-20261108-007).
- `southbridge goldhen: rc=-1 errno=256`. The value 0x100 is the SDK version, returned
  with the carry flag set. The launcher treated it as a failure and never asked for
  root, so the row stayed Unknown. No boot was chosen.
- UART session completed. Session: `20261110_031047_126796-exp-20261110-005-run11-upload-install-and-one-automatic-boot-sou-c7deadc1`.

## EXP-20261110-006 — run12: southbridge detected, full Automatic run completes

- Changed variable: the raw GoldHEN answer is used, and a filled backup proves root.
  run12 (SHA256 `d54da636…`) read back byte for byte.
- **Southbridge read on hardware:** `goldhen rc=256`, `root rc=0`, `with-root rc=0
  value=0x30201`, `root-returned rc=0`, and the launcher carried on normally. The
  operator confirmed "Baikal B1" on the home screen.
- **One Automatic run on one boot, all from the screen:** Boot 67 s; Processor, Memory,
  OpenGL, Vulkan, Ethernet and Wi-Fi (17 networks) PASS. Browser (Chromium) PASS: canvas,
  WebGL, frame pacing and video passed, capabilities INCONCLUSIVE by design. Picture PASS
  by operator answer. Sound NOT_RUN (no fixture in the manifest), Controller NOT-FOUND.
  3D benchmark GL 2879. Video decoding PASS in 5.5 s using the self-written session
  record, the first use of that path; evidence stays unverified by design.
- Zero GPU fault or hang lines, zero `PS4_UVD_RUNTIME CLOSED`, screen back on tty1. The
  decode closed the boot to further active tests as designed.
- 66 err-level kernel lines after the run (26 at the end of boot): 59 `No irq handler for
  0.227` and one for 0.49, both also seen on earlier boots, then xHCI over-current,
  `icc: not ready`, an xHCI debugfs duplicate, a failed bridge serial port and no IOAPIC.
- Open: Chromium again logged 5 Google messaging registration lines.
- Display detection, read-only: the connector is forced on (`video=HDMI-A-1:...D`), so
  `status=connected` proves nothing, and `edid` is 0 bytes, so no TV identity is known.
  The real hot-plug bit is bit 3 of bridge register `TMONREG` (0x7008), read only by
  `ps4_bridge_detect`, which a forced connector never calls and no boot log records.
- Not exercised: restart or shutdown after the decode. UART session completed. No
  acceptance claim; one run on one console.
  Session: `20261110_032854_717800-exp-20261110-006-run12-upload-install-and-one-automatic-boot-sou-a98f1ebd`.

## EXP-20261110-007 — 0.16: launcher confirmed, Automatic not reached

- Changed variable: 0.16 over run12, with no Picture or Controller question and no
  stripe on the launcher's left edge. Package SHA256 `cc256e85…`, private dev build.
- The operator confirmed that the v0.16 launcher reads Baikal B1 and that the stripe
  is gone. The home screen logged `files=1 ready=0`.
- Automatic was not chosen before a host process restart stopped the UART logger, so
  this session's continuity is invalid. It says nothing about the 0.16 Automatic run.

## EXP-20261110-008 — UVD firmware read from the running kernel at boot

- Changed variable: where the early-boot UVD firmware comes from. The loader
  (`uvd-firmware.patch`, SHA256 `6a264930…`; `loader.elf` `58f8230e…`) reads the
  313,912-byte range at kernel offset `0x8cbff0`, checks CRC32 `a465ba07` and adds the
  standard AMD header. The package's early-boot `lib/firmware/amdgpu/liverpool_uvd.bin`
  was removed, so the loader's copy was the only one the driver could load. The
  root-image copy stayed, because `uvd.py` pins it. Private dev build on 0.16 run1,
  package SHA256 `9c7dd48d…`, read back byte for byte.
- Before the run, on the host: the loader function run against the local 12.02 kernel
  image produced a file identical to the working one (`62a43ef4…`).
- **On hardware:** the loader logged `UVD kernel range crc32=a465ba07 expected=a465ba07`.
  The driver logged `Found UVD firmware Version: 1.101 Family ID: 9`, then
  `PS4_UVD_READY BEGIN ... payload=313912 crc=a465ba07`, `PS4_UVD_RUNTIME STARTED` and
  `QUEUE_READY`.
- First Automatic pass, same boot: "Before the last tests" went unanswered for 120 s with
  no USB stick, so 3D benchmark and Video decoding were skipped as designed.
- The operator chose Run tests again and answered Cross. Boot, Processor, Memory,
  Temperature, OpenGL, Vulkan, Browser (Chromium) and Wi-Fi (25 networks) PASS, 3D
  benchmark GL 2879, **Video decoding PASS (H.264)** in 6 s. The operator confirmed
  "pass". Ethernet SKIPPED (no cable, `link=0`, so no SSH), Sound SKIPPED (no fixture),
  USB and Controller NOT-FOUND.
- Zero panic, Oops or BUG lines and zero GPU fault, hang or reset lines. 80
  `No irq handler` lines, a kind also seen on earlier boots.
- Not exercised: the 8 GPU files from the loader alone (the package's copies still
  replace them), removing the root-image UVD copy, a boot without VCE firmware, restart
  or shutdown after the decode. UART session completed. No acceptance claim; one boot on
  one Baikal B1 console.

## EXP-20261110-009 — the 8 GPU firmware files come from the running kernel only

- Changed variable: where the early-boot GPU firmware comes from. On top of the
  EXP-20261110-008 build, the package's early-boot `liverpool_{pfp,me,ce,mec,mec2,rlc,
  sdma,sdma1}.bin` were removed, so the loader's copies were the only ones. The loader is
  unchanged (`58f8230e…`). VCE, EDID, Marvell and MT7668 files and the root image stayed.
  Package SHA256 `4ffd0d02…`, read back byte for byte.
- Before the run, on the host: the loader's whole firmware step, run against the local
  12.02 kernel image, built a 408,924-byte archive (capacity 445,864). RLC matches the
  package's older copy. The other 7 files differ from it by 2,172 to 13,365 microcode
  bytes, so this boot ran the 12.02 GPU microcode under this kernel for the first time.
- **On hardware:** the loader extracted all 8 files and UVD (`crc32=a465ba07`). No amdgpu
  firmware or `*ERROR*` line. One Automatic pass, answered Cross at the last-tests prompt:
  Boot 67 s, Processor, Memory, Temperature, OpenGL, Vulkan, Browser (Chromium) and Wi-Fi
  (30 networks) PASS, **3D benchmark GL 2887** (2879 on the package's copies), **Video
  decoding PASS (H.264)**. The operator confirmed "pass". Ethernet SKIPPED (no cable), Sound
  SKIPPED (no fixture), USB and Controller NOT-FOUND.
- Zero panic, Oops or BUG lines and zero GPU fault, hang or reset lines.
- Not exercised: a boot without VCE firmware, removing the root-image firmware, restart
  or shutdown after the decode. UART session completed. No acceptance claim; one boot on
  one Baikal B1 console.

## EXP-20261110-010 — the package carries no GPU, UVD, VCE or Marvell firmware

- Changed variable: on top of EXP-20261110-009, the package's VCE and both Marvell files
  were removed from early boot, and all 10 `liverpool_*` and both Marvell files from the root
  image. The boot script binds the loader's UVD file over the root's `amdgpu` directory.
  Build `ram-alpha1-0.16-nofw1`, no owner SSH key, loader unchanged (`58f8230e…`). Package
  SHA256 `b745c48b…`, read back byte for byte.
- Before the run, on the host: `scripts/check-ps4-package-firmware` passed on this package
  and failed on the EXP-20261110-009 package, by name and by raw blocks.
- **On hardware:** the loader extracted all 8 GPU files and UVD (`crc32=a465ba07`), init
  reported the loader's UVD ready, and the driver found UVD 1.101 family 9. One Automatic
  pass: Boot 66 s, Processor, Memory, Temperature, OpenGL, Vulkan and Wi-Fi (29 networks)
  PASS, **3D benchmark GL 2888**, **Video decoding PASS (H.264)**. Ethernet SKIPPED (no
  cable), Sound SKIPPED (no fixture), USB and Controller NOT-FOUND.
- **Browser FAILED after 18 s, before Chromium started.** The Chromium image mounted and
  unmounted within 31 ms, then the prepare step exited with an error. The image records the
  root it was built against and `prepare-chromium-provider` refuses any other, and this
  root is new. That cause comes from the source and the timing; the private diagnostic
  report was not read, because this build has no SSH.
- Zero panic, Oops or BUG lines, zero amdgpu `*ERROR*` lines, zero GPU fault, hang or reset
  lines, and no VCE lines.
- Fix for the next build: Chromium image `psx4` is built against this root, and
  `repack-ps4-diagnostix` now refuses a Chromium image built for another root.
- Not exercised: Browser on this root, restart or shutdown after the decode. UART session
  completed. No acceptance claim; one boot on one Baikal B1 console.

## EXP-20261110-011 — the firmware-free package with a Chromium image built for its root

- Changed variable: the Chromium image. `psx4` is built against the EXP-20261110-010 root,
  so its root check passes. Build `ram-alpha1-0.16-nofw2`, no owner SSH key, loader and root
  unchanged. Package SHA256 `3b9d53f8…`, read back byte for byte. `psx4` carries the Google
  block that arrived with `psx3`, so this was the first console run of that block; the
  browser runs before this one used `psx2`.
- Before the run, on the host: the Chromium root check, run with the real package files,
  failed for `nofw1` and passed for `nofw2`.
- **On hardware:** the loader extracted all 8 GPU files and UVD (`crc32=a465ba07`), init
  reported the loader's UVD ready, and the driver found UVD 1.101 family 9. One Automatic
  pass: Boot 67 s, Processor, Memory, Temperature, OpenGL, Vulkan, **Browser (Chromium)** and
  Wi-Fi (21 networks) PASS, **3D benchmark GL 2890**, **Video decoding PASS (H.264)**.
  Ethernet SKIPPED (no cable), Sound SKIPPED (no fixture), USB and Controller NOT-FOUND. The
  operator reported all passed.
- Zero panic, Oops or BUG lines, zero amdgpu `*ERROR*` lines, zero GPU fault, hang or reset
  lines, and no VCE lines.
- Not exercised: proof that nothing reaches Google, because the console had no network
  connection during the browser test. Restart or shutdown after the decode on this build.
  UART session completed. No acceptance claim; one boot on one Baikal B1 console.

## EXP-20261110-012 — ordered Turn off and Restart on `power1`, with no UART record

- Changed variable: how the console stops. `ram-alpha1-0.16-power1` asks systemd for
  `poweroff` or `reboot` first and calls the kernel directly only when systemd refuses or
  stalls for 15 s. The boot script caps systemd's stop waits in the RAM copy of `/etc`.
  No owner SSH key, loader and root unchanged. Package SHA256 `323eaaaf…`, read back byte
  for byte.
- **Invalid session.** Continuous capture stopped at 20:34:57, before the operator ran the
  test, so it holds only idle Orbis output and no Linux line from this build.
- The operator reported that one run "died immediately", without saying at which step, then
  reported that the build works and closed the investigation. The console was back on Orbis
  at about 22:19.
- Not established: the step that run reached, the time from Turn off to power-off, and the
  `last_shutdown_cause` after a Turn off or Restart on this build. No acceptance claim.
  EXP-20261110-013 was opened for a rerun and aborted with no console action.

## EXP-20261110-015 — the launcher finds the USB stick; ordered Turn off and Restart

- Changed variables: build `try2` stops the console through systemd without force-killing
  programs first, and keeps the Linux screen on its own console until the end. Build `try3`
  changes only the launcher: it borrows root from GoldHEN while it reads or writes the stick,
  and counts a stick only when one is mounted. Linux bytes are the same in both
  (initramfs `5066c949…`, loader `8c270a1a…`). `try3` package SHA256 `41b54cd2…`, read back
  byte for byte. No owner SSH key: the repack recorded none, and a scan of the initramfs and
  the decompressed root image found neither of the owner's public keys.
- **`try2` on hardware:** three launches showed "No USB stick found" with a stick mounted at
  `/mnt/usb0`, and none started Linux. The app's sandbox hides the stick.
- **`try3` on hardware, first launch:** `stick access: root borrowed`, then `usb stick:
  found`. Orbis unmounted and remounted the stick once; the launcher reported it gone and
  found again. Automatic test repaired the installed files, then started Linux. Boot 67 s,
  Processor, Memory, USB, Temperature, OpenGL, Vulkan, Browser (Chromium), Ethernet and
  Wi-Fi (19 networks) PASS, **3D benchmark GL 2891**, **Video decoding PASS (H.264)**. Sound
  SKIPPED (no fixture), Controller NOT-FOUND. **Turn off:** closing screen, stick unmounted,
  systemd accepted the ordered stop, and power went off 3.3 s after the menu choice with no
  forced kill. The operator saw the turning-off screen. The next Orbis boot reported
  `last_shutdown_cause 00`.
- **`try3`, second launch:** stick found, installed files already verified. Same results:
  Boot 66 s, GL 2889, Video decoding PASS, Wi-Fi 15 networks. **Restart to PS4:** the ordered
  stop was accepted, and the system restarted 3.3 s after the menu choice. Orbis started
  with `powerup_cause MAIN_SOC` and `last_shutdown_cause 00`. The operator reported all
  good.
- Both Linux boots: zero panic, Oops or BUG lines, zero amdgpu `*ERROR*` lines, zero GPU
  fault, hang or reset lines. The loader's UVD check matched (`crc32=a465ba07`).
- Not exercised: **Try Linux anyway** and the untested-console tiers, Save console info,
  a run without a stick. UART session completed. One console, two boots; no acceptance
  claim. Released as DiagnostiX 0.16 beta1 on the owner's decision (2026-11-11).
