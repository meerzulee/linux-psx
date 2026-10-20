# FFmpeg VAAPI reference comparison

`playback.py` prepares one application-level decode check through FFmpeg and a
selected Mesa VAAPI driver. It defaults to read-only preflight. Importing it does
no work. Only `--run` starts a decoder, and each invocation makes one attempt.
This tool does not install software, change the kernel, operate UART, reboot,
mount media, reset a driver, or retry.

The prepared fixture is 128×96, 120 frames at a nominal 30 fps: Constrained
Baseline H.264, level 1.1, 8-bit 4:2:0, one reference, no B frames or CABAC.
There are four I frames and 116 P frames. All 120 decoded frames have different
hashes. The x264 encode log also records inter-coded P macroblocks and skips.

## Pinned fixture and CPU recipe

The private workspace fixture is:

```text
lab/uvd-2026-10-20/runtime-fixtures/run-001/fixture-mac-02/
FIXTURE.json  8999f750efd20cf71f176efb5b5febb0681c493e23ca1dfb6f0bc7bcf8288626
clip.h264     e76dad0e9ff78727758fa0a55b5cd81b202b28e939a52eaa936f4660a37ebffd
software.nv12 d99b87567601efb735d438e766f34112344a3a31fc1d9e1b4177bfd329bcb491
```

The clip is 88,796 bytes. `software.nv12` is 2,211,840 bytes, exactly 120 tightly
packed NV12 frames of 18,432 bytes. Each reference frame is also saved separately
as `frames/000.nv12` through `119.nv12`, with its SHA256 in `FIXTURE.json`.
The reference comes from decoding the encoded clip independently in software;
it is not the pre-encode test pattern.

The fixture contains its executable CPU-only `recipe.py`, full generation,
software-decode and ffprobe argv arrays, environment, executable hashes, complete
FFmpeg/ffprobe version outputs, logs and probe output. All these inputs have
manifest hashes; `SHA256SUMS` includes the manifest itself. FFmpeg 9.0.2 and
libx264 produced the canonical Mac fixture. To regenerate into a new directory:

```sh
python3 -B "$FIXTURE/recipe.py" /absolute/new-fixture-directory \
  --ffmpeg /absolute/path/to/ffmpeg --ffprobe /absolute/path/to/ffprobe
```

Its media commands are equivalent to the following (the manifest stores full,
expanded argv including diagnostic flags and paths):

```sh
ffmpeg -f lavfi -i 'testsrc2=size=128x96:rate=30:duration=4' \
  -frames:v 120 -an -c:v libx264 -preset medium -profile:v baseline \
  -level:v 1.1 -crf 22 -pix_fmt yuv420p \
  -x264-params 'keyint=30:min-keyint=30:scenecut=0:bframes=0:ref=1:weightp=0:threads=1:lookahead-threads=1:slices=1:repeat-headers=1:aud=1' \
  -f h264 clip.h264
ffmpeg -hwaccel none -c:v h264 -threads 1 -f h264 -framerate 30 \
  -i clip.h264 -map 0:v:0 -an -sn -dn -vf format=nv12 \
  -fps_mode passthrough -c:v rawvideo -pix_fmt nv12 -f rawvideo software.nv12
```

The recipe checks ffprobe's codec/profile/dimensions, zero B frames, exact frame
count and average frame rate, then saves and hashes every decoded frame. Raw
H.264 `r_frame_rate` is reported as 60/1 by these builds; `avg_frame_rate` is
30/1. Both decoding commands explicitly set the demuxer frame rate and pass
frames through without duplication or dropping.

The same recipe passed on native Linux. Its x264 output differs from the Mac
encode, so it has separate hashes and is not interchangeable with the pinned
fixture. Linux's independent software decode of the canonical Mac clip matches
every reference byte and all 120 frame hashes. Evidence is under this run's
`fixture-linux-01/` and `cross-software-linux-01/`. The first Mac recipe attempt,
which rejected the raw `r_frame_rate` assertion, remains preserved separately.

## Preflight and explicit execution

Supply reviewed fixture, FFmpeg and Mesa driver SHA256 pins, the experiment's
expected kernel release and boot ID, and the chosen render node. The driver
argument may be Mesa's usual `radeonsi_drv_video.so` symlink: its resolved bytes
are hashed while the selected filename controls libva driver selection.

```sh
python3 -B playback.py \
  --fixture "$FIXTURE/FIXTURE.json" \
  --fixture-sha256 8999f750efd20cf71f176efb5b5febb0681c493e23ca1dfb6f0bc7bcf8288626 \
  --ffmpeg /usr/bin/ffmpeg --ffmpeg-sha256 '<reviewed-ffmpeg-sha256>' \
  --driver /usr/lib/dri/radeonsi_drv_video.so --driver-sha256 '<reviewed-driver-sha256>' \
  --render-node /dev/dri/renderD128 \
  --release '<experiment-kernel-release>' --boot-id '<experiment-boot-id>'
```

Preflight verifies every declared fixture asset, individual and contiguous
references, selected executable/driver bytes, running release/boot ID and render
node type/access. FFmpeg's `-version` and `-hwaccels` only query build identity;
preflight never opens the render node or decodes media. It prints JSON and
creates no files, even when an `--output` argument is supplied. It requires
Linux and reports failures with a nonzero exit code.

After the parent experiment has established owner authority and a live bounded
UART session, append these arguments to the same pinned invocation:

```sh
--run --output /absolute/existing-private-parent/new-playback-result \
  --experiment EXP-YYYYMMDD-NNN --uart-session '<external-session-reference>' \
  --timeout 60
```

The output directory must be new, outside the fixture tree, with an existing
canonical parent. Existing and failed directories are preserved. The UART value
is an assertion supplied by the operator; the runner does not authenticate a
session, continuity, console identity or owner confirmation.

The fixed hardware command uses `-hwaccel vaapi -hwaccel_output_format vaapi`
before the input and `-vf hwdownload,format=nv12` after it. Automatic filter
conversion is disabled; there is no hardware upload or alternate filter graph.
FFmpeg requires hardware frames at the input of `hwdownload`, so software
decoding alone cannot satisfy this graph. See the upstream filter contract:
https://ffmpeg.org/ffmpeg-filters.html#hwdownload
https://ffmpeg.org/ffmpeg.html#Advanced-Video-options

The decoder reads the entire pinned clip, without a frame limit that could hide
extra frames. It passes frames through to raw NV12 output. The child environment
is recorded and restricted to locale, PATH and explicit libva selection; ambient
driver overrides and `LD_PRELOAD` are not inherited. Driver and FFmpeg binary
pins identify these files, not every dynamically loaded dependency. Package and
kernel provenance remain the parent experiment's responsibility.

## Results and comparison policy

The runner retains `decoded.nv12`, bounded FFmpeg stderr, preflight identity,
execution bounds/return code, per-frame and total comparison hashes, and a final
`RESULT.json` with artifact hashes. stderr is capped at 256 KiB. Raw retention is
capped at the expected 120 frames plus one frame; excess output fails the run.
The timeout defaults to 60 seconds and cannot exceed 120. Failures retain partial
output and receipts; there is no cleanup or automatic retry. Inputs and running
identity are checked again after execution.

`PASS_EXACT_NV12` requires successful FFmpeg exit, no limit failure, exactly
2,211,840 output bytes/120 frames, and equality of every frame and the whole
reference. Wrong, missing and extra frames fail. A same-size mismatch also
records the differing sample count and maximum absolute sample error. These
statistics are diagnostics, with no automatic tolerance or reference rewrite.
If real decoding diverges, retain both outputs and investigate first. Any later
approximate comparison policy needs a separate reviewed experiment with explicit
per-plane bounds and reasons; it cannot turn this exact test into a pass.

`hardware_acceptance` remains false in runner receipts because UART evidence and
driver/kernel provenance require independent review. An exact result establishes
only this clip's pixel comparison through the required hardware-frame graph;
support, repeatability, presentation, audio, seeking, encoding and other codecs
are outside this check. The tool and fixture have CPU-only Mac/native preparation checks. The separate
EXP-20261020-006 hardware experiment subsequently passed one real FFmpeg/VAAPI
run of this exact fixture; see [RESULTS.md](RESULTS.md).
