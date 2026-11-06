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
