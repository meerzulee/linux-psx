# Experimental PS4 Liverpool UVD

The experimental Linux 7.2.11 backend has decoded H.264 through normal
FFmpeg/VAAPI on one Liverpool PS4. The native 1920×1080, 30 fps, 30-second
fractal matched all 900 software-reference frame hashes. A separate bounded
playback test completed five loops with zero reported display/decoder drops
and measured PTS/wall speed 0.997509×. See the [exact results and artifact
identities](../experiments/ps4-uvd-driver/runtime/RESULTS.md).

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
UVD kernel. The exact tested package keeps a complete already-patched Linux source archive, `.config`, notices,
build inputs and `PSX-REBUILD.md` for reconstruction. Its filename is
`source-7.2.11-psx-ps4-baikal-uvd-runtime-dev1.tar.zst`, SHA256
`36fa0d0119281503b456e3532080afabe2ea7b5c7bc7b05af08685d295f7a3a4`.
The source snapshot and generated artifacts are packaged separately from Git;
there is no claim that a new clone alone reconstructs the tested incremental
link through the ordinary target registry. Public artifact publication is a
separate step from this source checkpoint.

The tested kernel SHA256 is
`9c3ec7d27fdfdebfee137684a635301e8dfa259b50e2f5d98ee33cf5f91e6153`,
release `7.2.11-psx-ps4-baikal-uvd-runtime-dev1`. Always keep its whole matching
41-module set and corresponding source. Firmware is external and excluded.

## Mesa and the visible test

The distribution repository owns an isolated Mesa 26.2.5 candidate with
attributed Liverpool/Gladius family setup, the PS4 raster configuration and
AVC-only capabilities. It also owns the deterministic fractal generator, exact
900-frame decode tool and monitored player:

https://github.com/meerzulee/omarchy-psx/tree/master/platforms/ps4/experiments/mesa-uvd

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

The port exposes only 8-bit NV12 AVC, one active handle and an implementation
bound of 1920×1088/level 4.1. Liverpool UVD 4.2 has no HEVC hardware decoder.
No 4K, 1080p60, additional profiles/codecs or VCE qualification is claimed.
Interactive seeking, long-duration stability, system-wide Mesa adoption and
at least three cold boots of a frozen matching artifact set remain open.

After entry, the experimental backend retains DMA backing and blocks reset,
unload, PM and ordinary shutdown cleanup. Full manual power-off and a known
preparation boot set are the recovery. Do not treat it as a normal installable
release or operate a second decoder alongside the one-handle test.
