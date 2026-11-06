# Diagnostix alpha with UVD

This is a local engineering candidate for the recorded Baikal PS4. It combines
the diagnostic interface with a separately pinned experimental UVD kernel and
its whole matching module tree. It does not turn the ordinary native-dev3
kernel into a UVD build, and results from the two kernels stay separate.

The ordinary diagnostic boot set remains the rollback. The core retains its
1 GiB cap; optional browser providers and media have separate explicit budgets.
No public binary release or hardware qualification follows from assembly.

Use the corrected **alpha3** firmware profile for new UVD assembly. Alpha1 and
alpha2 are preserved historical inputs; their stock firmware pin does not fit
the experimental runtime and must not be treated as a boot-ready UVD package.

Diagnostix is the visible appliance name. Source and image builds remain in
Linux psX, preserving the existing `psx-diagnostics` service, path and protocol
identities. The Power tab explains the current restrictions; its Shutdown and
Reboot controls do not execute commands on an unqualified build.
The current frontend adds confirmed [Shutdown and Reboot to Orbis](PS4-DIAGNOSTIC-POWER.md)
for the exact alpha4/alpha5/alpha6 power profiles; alpha7 adds immediate preemption. A dev4 update can replace only its
controller and TUI with `refresh-ps4-diagnostics-frontend --alpha-power-only`,
preserving the tested kernel, root, boot policy and all other CPIO members.

The [alpha6 profile](../manifests/ps4-diagnostics-uvd-alpha6.json) pins
runtime-dev5 and all 41 matching modules. Normal reboot after exact UVD decode
and software Shutdown were observed on this Baikal Slim. The rescue-shell
stop-signal fix removes the measured 90-second userspace wait; corrected-image
acceptance to ICC takes about 1.1 seconds. See EXP-20261106-019/020.

The new [alpha7 profile](../manifests/ps4-diagnostics-uvd-alpha7.json) pins
runtime-dev6. Confirmed Power interrupts running tests, skips pending report
writes and service-stop waits, and enters ordinary kernel power-off/restart.
Its UVD terminal restart also permits a post-start failed or already-closed
queue while retaining all backing. This new behavior is experimental and
not yet hardware-qualified. Optional Chromium/Wayland providers still require
their own matching identities. Public-image preparation is documented in the
[release guide](DIAGNOSTIX-RELEASE.md).

## Frozen inputs

[`ps4-diagnostics-uvd-alpha1.json`](../manifests/ps4-diagnostics-uvd-alpha1.json)
pins the kernel, config, all 41 matching modules, complete corresponding source,
private Mesa, package snapshot, firmware and exact AVC fixture. Its release is
`7.2.11-psx-ps4-baikal-uvd-runtime-dev1`. The preserved patched source snapshot
records project base `35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2`; it is not
native-dev3's later source commit. Original package receipts remain unchanged.

The separate alpha2 profile combines **all 73 reviewed active native-dev3
patches** at `c8495a51e0bad4851015cd5d34ee61b2fde87e44` with the experimental
UVD overlay. This includes the contributor's guarded ICC power-off fix, native
boot defaults, SATA/PHY, HDMI/audio and focused platform/network corrections.
Disabled or candidate patches are not activated. The builder verifies the
whole source and active-patch inventory and packages a new kernel, matching
modules, resolved configuration and full corresponding source. A pending
profile cannot be used until its actual build hashes have been frozen.

[`ps4-diagnostics-uvd-alpha3.json`](../manifests/ps4-diagnostics-uvd-alpha3.json)
retains the same dev2 kernel, complete module set, configuration and platform
fixes, and corrects the firmware packaging defect found by EXP-20261105-004.
Its explicit external `--uvd-firmware` input must be 314,168 bytes with SHA256
`62a43ef4814f59fdd6772792e0a9303e016fe2e17ad30b36412d1fa7ce4cd5cb`.
The builder checks header 1.0, IP 4.2, version `0x012a6509`, payload 313,912
bytes at offset 256 and CRC `0xa465ba07`, then verifies the corrected member
in both the actual root and initramfs. The ordinary base's 232,752-byte stock
firmware remains a separate original-input anchor. Firmware stays external
and private; its binary is not added to this source repository.

The original stock member cannot pass the driver's existing checks. The
verbose boot recorded an initialization rejection and retained-state park
that prevented GPU initialization from completing.
The corrected package preserves those checks, all three UVD gates and every
VMID, clock and retained-DMA restriction. Assembly is not boot qualification.

In dev1/dev2, the ICC fix does not remove the UVD shutdown park. Power-off validation uses
a separate boot of this combined kernel with all three UVD gates disabled;
the UVD-enabled boot ends with physical recovery. A successful TCP disconnect
or power command return is not operator-observed power-off acceptance.

The separate [alpha4 profile](../manifests/ps4-diagnostics-uvd-alpha4.json)
uses the complete matched dev3 package and the same corrected firmware. It
adds the default-off `amdgpu.ps4_uvd_poff=1` experimental boot option alongside
the three UVD gates. Only normal system power-off may return from AMDGPU PCI
shutdown without teardown; DMA backing stays allocated and every other
lifecycle guard remains. Kernel compilation, all 41 matching modules and
complete source/module archive readbacks passed. EXP-20261105-016 subsequently
passed a captured canonical UVD decode followed by owner-observed physical
power-off; Orbis/FTP returned afterwards. This single run does not qualify
reboot, kexec, suspend or broader stability. A roughly 90-second delay and
busy loop-root shutdown messages remain recorded. See the [power-off candidate
details](UVD.md#default-off-dev3-power-off-test-candidate).

EXP-20261105-018 then matched the canonical 120 UVD frames and returned to
Orbis through one forced ICC hardware restart, with UVD still enabled. Reports
were preserved and the USB synced/remounted read-only first. This is distinct
from ordinary Linux reboot teardown. Both scoped power results are recorded
in the [diagnostic session ledger](../diagnostics/SESSIONS.md).

The separate [alpha5 profile](../manifests/ps4-diagnostics-uvd-alpha5.json)
uses runtime-dev4 and its whole matching module set. It preserves the dev3
UVD, ICC, PHY and xHCI source and adds the scoped internal-SATA completion
probe. Its initramfs identifies the internal ATA disk through the exact PCI
controller, sets the disk and its partitions read-only before userspace, and
stops if protection cannot be verified. The USB remains the diagnostic root.
The ordered smoke runner executes foundation, network, graphics, browsers,
Wayland and audio checks before one selected terminal UVD scenario. These
are executable tests; their build receipts do not supply hardware results.

EXP019–022 captured the dev4 read-only SATA boot, matching direct reads,
OpenGL/Vulkan and native Wayland reference pixels, both browser baselines,
all 900 native 1080p UVD reference frames, and software power-off after that
combined use. A fresh boot exercised the corrected audio manifest reader:
44.1 kHz `aplay` completed successfully, with audible verification explicitly
deferred by the owner. Browser-close polling and complete bounded JSON reads
are corrected in source. The original privately tested USB image retains its
original hashes; corrected diagnostic source is used by subsequent builds.

The alpha assembler creates a new regular ext4 image offline from the exact
ordinary root, replaces its entire kernel-module tree, and installs the alpha
runner and checklist. It uses no host mount, container, physical device or
privileged package installation. Filesystem checks and data readback verify the
result; they cannot establish that it will boot. The regenerated initramfs and
external receipt bind the new root, kernel and fixed 29 frontend assets.

Inspect the native assembly commands before supplying explicit artifact paths:

```sh
python3 scripts/build-ps4-diagnostics-alpha --help
python3 scripts/prepare-ps4-diagnostics-alpha-extras --help
```

The alpha builder's `--plan` verifies real inputs without creating output.
Ordinary assembly writes a new host boot-set directory, never the USB. A
matched Wayland archive can be installed inside the read-only root with
`--wayland-suite` and its reviewed `--wayland-sha256`. Chromium preparation
then creates a separate 384 MiB provider and a complete final boot set with
two matching fixed frontend helpers; its provider image remains outside the
1 GiB core cap. Original executables, libraries, licenses, sandbox ownership
and modes are preserved and read back. Neither pack is executed on the host.
Each alpha's optional Chromium files occupy their own USB subdirectory so
the ordinary image's provider and sidecars remain available for rollback.

## What to test

Open **Test plan** in the diagnostic interface, or inspect the source checklist:

```sh
python3 diagnostics/ps4/qualification.py
python3 diagnostics/ps4/qualification.py --json
```

The plan distinguishes available automated probes, operator observations,
separate experiments and deferred work. Run CPU and owned-memory checks before
advanced tests. Keep these evidence boundaries:

| Area | Evidence needed beyond a short successful run |
| --- | --- |
| Boot and recovery | Repeated cold boots of the exact build, artifact/module identity, readable console, SSH enrollment and a verified rollback |
| CPU, RAM and thermal | Correct computation and memory patterns, bounded longer load, temperature observations and no kernel faults |
| Ethernet | Three cold boots; verified 1 GiB transfers each way per boot; 60 minutes mixed traffic; healthy cable/admin recovery and Orbis networking afterward |
| Wi-Fi and Bluetooth | Record adapter/firmware, bands or peer, connection and actual traffic/input; investigate a new defect when a reproducible report identifies it |
| USB and storage | Identify every device; preserve reports and check their readback; internal SATA/root installation stays a separate storage experiment |
| Graphics and display | Hardware renderer plus reference pixels, finite repeated workloads, Xorg/Wayland separately, mode/cursor/TTY recovery and no GPU faults |
| Browsers | Firefox/Chromium separately, saved canvas/WebGL/animation/video results, configured display mode and measured callback cadence, bounded automatic close/error reporting |
| Audio and input | Listen to the 44.1 kHz channels, verify controls/HID; native 48 kHz remains deferred and cannot be inferred from resampling |
| UVD | Forced VAAPI decode and exact frame comparison, then separately qualify sustained decoding and resource/lifecycle behavior |
| Power | Externally observed power-off, reboot and return to Orbis under a reviewed kernel-specific lifecycle plan; suspend remains excluded |

Skipped, unsupported, deferred and inconclusive checks are not passes. The
checklist does not certify stability. A reviewer must evaluate completed UART
sessions, exact build identities, repetitions and model scope against
[the finish gates](CURRENT-FINISH-PLAN.md). Another PS4 model needs its own
report and qualification; success on one Baikal console does not cover it.

## UVD scenarios in the read-only Test plan

**Test plan → UVD** and the [source checklist](../diagnostics/ps4/qualification.json)
describe this progression. Availability describes tooling, never a test result.
The original 45 checks, mandatory profiles and acceptance gates are unchanged.

| Scenario | Current availability and next work |
| --- | --- |
| 128×96, exact 120 frames | Available through **Graphics → UVD AVC reference**. One attempt per physical cold boot; collect the canonical component on three cold boots of the same frozen build. It does not meet the native 900-frame or lifecycle gates. |
| 720p30, 900 frames / 30 seconds | Unimplemented alpha fixture/harness. Prepare pinned input and exact software-reference frame hashes, then a bounded streaming comparison. |
| 1080p30, 900 frames / 30 seconds | Unimplemented alpha integration. Historical native evidence is separate; freeze a matched fixture and streaming comparator before use. |
| Additional 8-bit H.264 profiles/features | Unimplemented. Review actual driver capabilities and limits before selecting one feature and preparing its exact-reference fixture. No broader codec/profile support is promised. |
| Longer playback and resource monitoring | Unimplemented bounded player/sampler. Separately planned 5- and 30-minute windows precede the unchanged 60-minute gate; measure speed, drops, memory/resources, temperatures and UART faults. |
| Close/reopen, admission and exhaustion | Source-gated and unimplemented. Current retained-DMA and one-attempt restrictions prevent treating the existing repetition targets as runnable loops. |

Declare an optional-storage budget for new references: 900 raw 1080p NV12
frames occupy about **2.8 GB**, outside the 1 GiB core budget. Compare with
bounded streaming buffers or pinned frame hashes; never load an entire raw
clip/reference into RAM. Fixture preparation and harness integration do not
qualify hardware.

Within the authorized experiment, every actual UVD attempt remains the final
active action of a fresh physical cold boot. Keep UART capture through physical
power-off/known-good Orbis recovery. No automatic suite, same-boot retry,
second decoder or power/reset/rearm exception is added by this plan.

## First UVD experiment

Only the explicit AVC action may start a decoder. It reuses the existing
[`playback.py`](../experiments/ps4-uvd-driver/runtime/playback.py) harness and
its pinned 120-frame H.264/NV12 fixture. It compares every frame and the whole
decoded output with the software reference. Software fallback cannot pass.
The result does not qualify browser decoding, other codecs, encoding or 4K.

The runtime requires these three boot arguments:

```text
amdgpu.ps4_uvd_runtime=1 amdgpu.ps4_uvd_ready=1 amdgpu.ps4_uvd_poll_state=1
```

A current bounded UART session and a root-private assertion tied to the live
boot are required before the action starts. The assertion is an operator
declaration, not proof of completed capture continuity. The runner permits one
decode attempt per boot and preserves the attempt record across controller
restarts. The UVD attempt is the final active test for that boot, including
when every frame matches. Its immutable stop intent prevents a controller
restart or an incomplete report save from reopening active tests. Reports
and SSH remain available for evidence collection. A timeout, mismatch or
terminal fault remains a stop and cannot count as a pass.

Firefox's alpha profile disables hardware video decoding while retaining the
explicit WebGL tests. Mozilla documents the
[hardware-decoding preference](https://searchfox.org/firefox-main/source/modules/libpref/init/StaticPrefList.yaml)
and the [separate VAAPI force-enable behavior](https://bugzilla.mozilla.org/show_bug.cgi?id=1801054).
This keeps ordinary browser tests separate from the experimental decoder's
resource lifecycle. Optional providers must have alpha-specific kernel/root
identities before use.

After UVD runtime entry, follow the preserved retained-DMA restrictions: no
driver unload, reset, unbind, rearm, suspend or automatic reboot/power recovery.
The first alpha experiment ends with captured results and the operator's
physical power-off/known-good rollback sequence. Broad lifecycle acceptance
remains open in [the UVD guide](UVD.md).
