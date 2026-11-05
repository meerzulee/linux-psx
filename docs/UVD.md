# Experimental PS4 Liverpool UVD

The experimental Linux 7.2.11 backend has decoded H.264 through normal
FFmpeg/VAAPI on one Liverpool PS4. On the corrected Diagnostix alpha3/dev2
artifact set, the native 1920×1080, 30 fps, 30-second fixture matched all 900
software-reference frames. Firefox 154 also completed that clip with 900
VA-API frame outputs and zero software frames, but reported one dropped frame;
its aggregate result remains INCONCLUSIVE. See the
[diagnostic experiments](../diagnostics/SESSIONS.md#exp-20261105-013--dev2-native-1080p-pixel-oracle-passes).

Historical dev1 testing separately matched 900 native frames and completed
five playback loops with zero reported display/decoder drops and measured
PTS/wall speed 0.997509×. Those [results and artifact
identities](../experiments/ps4-uvd-driver/runtime/RESULTS.md) retain their original
scope; they do not supply endurance evidence for dev2 or a new dev3 build.

This is an experimental source checkpoint for engineering review. It does not
change the default kernel target, qualify a supported binary release, or
establish the first implementation by anyone. Source/build checks and a
new compilation do not inherit the tested binary's hardware results.

## Source and build entry points

- [Runtime source and design](../experiments/ps4-uvd-driver/runtime/README.md):
  the default-off Liverpool backend, job ownership, memory and session hooks.
- [Native build lineage](../experiments/ps4-uvd-driver/NATIVE-BUILD.md) and
  [runtime packaging](../experiments/ps4-uvd-driver/runtime/PACKAGE.md):
  source receipts, frozen inputs, whole matching modules and archive readback.
- [Original application proof](../experiments/ps4-uvd-driver/runtime/PLAYBACK.md):
  the fixed hardware-download graph and software-reference comparison.

Run `make check` for the repository's source contracts. The runtime directory
also contains `transport_check.py`, `jobs_check.py` and `session_check.py`;
read their input requirements before running them. These are CPU/source checks.

The ordinary `make TARGET=6.18-ps4-baikal` and the separate PS4 Linux 6.x/7.x
CI workflows build registered targets; they do **not** build this experimental
UVD kernel. The historical tested dev1 package keeps a complete already-patched
Linux source archive, `.config`, notices, build inputs and `PSX-REBUILD.md` for
reconstruction. Its filename is
`source-7.2.11-psx-ps4-baikal-uvd-runtime-dev1.tar.zst`, SHA256
`36fa0d0119281503b456e3532080afabe2ea7b5c7bc7b05af08685d295f7a3a4`.
The source snapshot and generated artifacts are packaged separately from Git;
there is no claim that a new clone alone reconstructs the tested incremental
link through the ordinary target registry. Public artifact publication is a
separate step from this source checkpoint.

The historical tested dev1 kernel SHA256 is
`9c3ec7d27fdfdebfee137684a635301e8dfa259b50e2f5d98ee33cf5f91e6153`,
release `7.2.11-psx-ps4-baikal-uvd-runtime-dev1`. Always keep its whole matching
41-module set and corresponding source. Firmware is external and excluded.

The newer Diagnostix alpha2 uses a separately built
`7.2.11-psx-ps4-baikal-uvd-runtime-dev2`. Its
[native3 composition helper](../experiments/ps4-uvd-driver/runtime/build_native3.py)
applies all 73 active patches from the frozen native-dev3 baseline and the
declared 41-file UVD overlay. It preserves the current platform fixes,
including the credited ICC power-off patch; candidate and disabled patches
remain inactive. The helper uses the shared packaging routines in
[`build_poweroff.py`](../experiments/ps4-uvd-driver/runtime/build_poweroff.py).
Both helpers have focused host checks beside them. The
[alpha2 profile](../manifests/ps4-diagnostics-uvd-alpha2.json) pins the kernel,
resolved config, complete matching modules and corresponding source archive.
Its kernel SHA256 is
`6b1d9d90d29eeffb58057cd04a387ad08c53adc7d13afb62bcb2b3af8c686fdc`.
Compilation and archive readbacks passed; the earlier dev1 hardware results
do not qualify this new build.

EXP-20261105-004 found that the diagnostic alpha had retained the ordinary
232,752-byte firmware even though this backend requires the preserved
314,168-byte variant. The verbose boot recorded startup error `-22`, GPU-init
failure and retained-state parking. The corrected
[alpha3 profile](../manifests/ps4-diagnostics-uvd-alpha3.json) keeps the kernel
unchanged and supplies the matching external firmware to both root and
initramfs, with complete header/CRC/SHA256 checks. See the
[diagnostic ledger](../diagnostics/SESSIONS.md) and
[firmware packaging contract](PS4-DIAGNOSTIC-ALPHA.md). This fixes a package
input; it does not relax the driver or establish hardware acceptance.

The tested dev1/dev2 runtime still parks during shutdown after entry, before
ordinary ICC power-off can run. The host-only
[`prepare-ps4-diagnostix-power-test`](../scripts/prepare-ps4-diagnostix-power-test)
derives a separate boot set with all three UVD gates explicitly disabled and
unchanged kernel, root and modules. Power-off qualification needs that fresh
captured boot, observed disabled sysfs gates and no UVD runtime/ready entry;
it cannot be inferred from an enabled UVD test or a physical power-off.

## Current dev2 diagnostic observations

The corrected alpha3 package retains the dev2 kernel identity above. Its
same-boot EXP013 native test forced VAAPI/hardware download and matched all
900 frame hashes and the full 2,799,360,000-byte NV12 reference. This is an
unpaced decode-correctness PASS, with the pinned driver observed in the actual
process. It is not a browser-output pixel comparison or an endurance pass.

EXP014 then exercised paced Firefox playback of the same native clip. It
stopped after 580 hardware frames when the next compressed sample crossed
the harness's 16 MiB per-file limit. Firefox reported a media-cache read error;
the surface pool remained bounded and no VA-API error was observed. EXP015
changed only that file limit to 32 MiB and completed 30 seconds with 900 total
and presented frames, 900 distinct VA-API timestamps and zero software frames.
One reported drop keeps the strict aggregate INCONCLUSIVE. Existing log/RAM
limits, sandbox checks and negative results remain in force.

The earlier Chromium 151 small-clip comparison selected hardware VAAPI after
disabling its below-360-pixel software-first ordering policy. However, the GPU
process failed sandbox initialization with existing worker threads and had
Seccomp=0. This is hardware-path evidence, not an intact-sandbox PASS. The
[default-off TSYNC source candidate](../experiments/ps4-browser-uvd/README.md)
has host mechanism checks only and has not been built into the tested provider.

All three native/Firefox sessions had completed UART continuity and no
observed terminal UVD/GPU fault. They used explicit private same-boot scopes
that preserved prior intents/results; the public one-attempt policy is
unchanged. These observations neither add cold boots nor qualify dev3.

## Default-off dev3 power-off test candidate

A separate dev3 kernel candidate has compiled and passed complete module and
corresponding-source archive readbacks for a narrow power-off experiment.
The [alpha4 profile](../manifests/ps4-diagnostics-uvd-alpha4.json) pins kernel
SHA256 `01fa05f683c4c36ae83678f09cf3df502ddb0731dc73f2530cdb05fbe317804d`
and all 41 matching modules. No hardware power-off PASS is claimed here.
Its boot-only `amdgpu.ps4_uvd_poff=1` opt-in is default 0, read-only in sysfs
and protected by `__ro_after_init`.

The exception applies only to an entered, retained Liverpool runtime while
the system is in `SYSTEM_POWER_OFF`, with `reboot_force` and
`kexec_in_progress` both false.
It closes new job admission, retains firmware/page-table/DMA backing, and
returns from the PCI shutdown callback before normal teardown so the existing,
credited ICC power-off path can proceed. It does not establish DMA quiescence,
free resources, reset or rearm the decoder. Other reset, removal, unload, PM,
reboot and shutdown guards remain unchanged. With the opt-in absent, the
existing retained-state park remains.

The [runtime implementation](../experiments/ps4-uvd-driver/runtime/amdgpu_ps4_uvd_runtime.c)
and [PCI shutdown integration](../experiments/ps4-uvd-driver/runtime/build_candidate.py)
define the exact boundary. The new candidate requires its own matching
kernel/config/modules/source receipts and hardware observations; dev2 results
cannot be inherited as dev3 qualification.

## Mesa and the visible test

Linux psX owns the pinned Mesa 26.2.5 candidate in `userspace/mesa`, with
attributed Liverpool/Gladius family setup, the PS4 raster configuration and
AVC-only capabilities. It also includes the deterministic fractal generator, exact
900-frame decode tool and monitored player:

https://github.com/meerzulee/linux-psx/tree/master/userspace/mesa

Saved NV12 pixels reproduced the old OpenGL stripes without a new UVD decode.
The new process-local Mesa candidate displayed clean captured picture/text;
the owner confirmed the reference on the monitor. This is evidence for the
tested rendering path, not every desktop/compositor artifact. The version and
PS4 patches changed together, so the exact causal hunk is not isolated.

The 1080p player requires `--hwdec=vaapi-copy`, disables software fallback and
uses `--hwdec-threads=1`. mpv 0.41.0 independently defaults hardware decoding
to four threads even when `--vd-lavc-threads=1` is supplied. The earlier
four-thread initialization failed with CS rejection; that negative result is
preserved. The one-thread test changed no kernel quota or clock setting.
Live stats show actual decoder, dimensions, source FPS, measured playback
speed, drop counters and UVD fence completions.

## Current boundary

A historical Mesa 26.2.5 system trial (EXP022) stalled during Vulkan rendering,
then recorded a terminal UVD timeout and frozen desktop. No AVC workload was
requested by that test. Both system packages
were rolled back to signed 26.1.7, and EXP023 recovered a responsive desktop
on a fresh boot. Hyprsunset was never launched. See the
[Mesa qualification matrix](MESA-TESTING.md) before further GPU testing.

The subsequent dev5 Mesa candidate exposes an existing compute-fill offset
bound to the shader compiler, retaining buffer stores in the tested fill.
The previously failing D16 oracle and cube now pass with HTILE enabled.
Sequential UVD checks matched 120 canonical frames and 900 native 1080p30
frames, with 122/902 UVD completions and no terminal marker. An additional live-library
observer failed because a /proc children interface is absent; that collection
error is preserved separately. A corrected PPid-based observer then captures
the actual dev5 driver during a repeated exact 120-frame check. The owner
confirms desktop/mouse normal. These are focused passes, not unrestricted
Vulkan qualification, a proved lower-level FLAT defect or system adoption.

The port exposes only 8-bit NV12 AVC, one active handle and an implementation
bound of 1920×1088/level 4.1. Liverpool UVD 4.2 has no HEVC hardware decoder.
No 4K, 1080p60, additional profiles/codecs or VCE qualification is claimed.
Interactive seeking, long-duration stability, system-wide Mesa adoption and
at least three cold boots of a frozen matching artifact set remain open.

GCN graphics, UVD decoding and VCE encoding are separate hardware/driver
paths. Liverpool uses UVD, not VCN; selecting a VCN backend is not an encoder
enablement switch. Hardware VCE encoding remains unimplemented and unqualified
in this port. Software encoding does not satisfy that requirement.

After entry, the tested backend retains DMA backing and blocks reset, unload,
PM and ordinary shutdown cleanup. The dev3 power-off-only candidate above is
an explicit experiment, not general lifecycle support. Full manual power-off
and a known preparation boot set remain the recovery for the tested dev2
image. Do not operate a second decoder alongside the one-handle test.

## Experimental beta and stable qualification

The recorded component results can support an explicitly experimental beta
description with exact artifact identities and published limits. Such a beta
must retain the one-handle/final-action restrictions, distinguish native
pixel-oracle results from browser presentation, and disclose incomplete browser
acceptance, lifecycle and encoding support. No beta binary release is declared
by this source documentation.

Stable qualification still requires:

- At least three cold boots of one frozen matching artifact set, with saved
  prerequisite and canonical/native decode evidence on each boot.
- At least 60 minutes of paced playback with verified hardware decoding,
  complete bounded evidence, no terminal faults or software fallback, and the
  declared frame-pacing/drop criteria met. A 30-second run with one drop does
  not pass the current zero-drop gate.
- Admission and lifecycle tests covering the one-handle limit, rejected
  unsupported inputs, fault/timeout closure and intended power transitions.
  Reuse or freeing of DMA-reachable resources requires a separate quiescence
  proof; retaining allocations through a power-off exception is not that proof.
- Independent encoding implementation and qualification if VCE is included
  in a future support claim; decode success cannot qualify it.
