# Browser UVD diagnostic prototype

The explicit Firefox prototype in
[`browser_uvd.py`](../diagnostics/ps4/browser_uvd.py) tests the frozen alpha3
kernel/root/Mesa combination. It is separate from the ordinary browser lab,
whose video check uses software decoding. It is not installed automatically,
enabled at boot, or a hardware-support declaration. Preparation and default
planning do not contact a console or start a browser.

## Offline video preparation

The host tool accepts the pinned canonical 128x96, 30 fps AVC Annex-B fixture
and its exact per-frame NV12 reference manifest. Supply explicit host FFmpeg
and ffprobe executables and a new output directory:

```sh
python3 scripts/prepare-ps4-browser-uvd-fixture \
  --fixture-json /absolute/path/FIXTURE.json \
  --clip /absolute/path/clip.h264 \
  --ffmpeg /usr/bin/ffmpeg --ffprobe /usr/bin/ffprobe \
  --duration-seconds 30 --output /var/tmp/psx-browser-video-new
```

Only two profiles are admitted: `canonical-120` (four seconds) and
`continuous-900` (30 seconds). The longer profile repeats the canonical encoded
input during host preparation and muxes one continuous MP4. The browser plays
that file once; it does not loop, seek, replay or retry. No audio, remote video,
new codec, resolution or hardware capability is implied.

Preparation stream-copies H.264 and independently software-decodes the result.
Every output frame must match its canonical reference, including the repeated
cycle in the longer profile. The receipt binds the MP4, page, whole NV12 hash,
all frame hashes and exact ffprobe presentation timestamps. Nominal frame rate
is not substituted for the actual MP4 timestamps. Generated media and receipts
stay outside versioned source and do not enlarge the core boot image.

## Explicit runtime trial

The runtime requires explicit SHA256 pins for the clip, page and receipt.
Without `--run`, it only validates the inputs and prints a plan. Actual
execution additionally requires the exact frozen alpha3 image and firmware,
current-boot CPU/RAM checks, an active bounded UART assertion, an idle existing
X11 diagnostic session and no earlier consumed UVD attempt or unresolved stop.
Browser profiles, caches and logs use the existing 512 MiB writable RAM pool
under `/var/lib/psx-diagnostics/browser-uvd/`. Preflight requires 160 MiB free
before intent reservation; capture stops below 16 MiB. These are sampled
shared-pool checks, not an exclusive reservation against other processes.

Before browser startup it reserves the existing immutable per-boot UVD intent.
One sandboxed, unprivileged Firefox profile plays one clip. A 45-second page
deadline and 60-second outer service bound save observations and close the
browser. Completion, failure and timeout all end active tests for that boot;
save evidence and recover by physical power-off before another decoder trial.

PASS needs corroborating decoder-process/library identity, intact credentials
and sandbox, one successful hardware decoder, the complete pinned timestamp
sequence, successful VA submissions/completions, a finished page with no
reported drops, and no terminal kernel fault. Missing counters stay unknown.
Software fallback, decoder recreation, missing traces or truncated evidence
cannot pass. This is not a byte-exact oracle for the browser's displayed pixels.

## Current result and next localization

[EXP-20261105-009](../diagnostics/SESSIONS.md) initialized VA-API but failed the
first Firefox DMA-BUF frame hand-off and played the clip in software. Its
original result and private evidence are preserved. The classifier now reports
that decoder failure before secondary page-counter limitations. Correctly
cased `Dmabuf` and `DmabufRef` logging is used for future trials.

Source review identified a possible packaging cause: the process-local EGL
vendor selection was under `/run`, outside Firefox's existing RDD file policy.
Future source prepares an exact, root-protected selector under
`/etc/psx-diagnostics/`, selecting the same verified Mesa library. The override
applies only to this browser process after the Mesa wrapper. This changes no
sandbox policy, shared driver installation, kernel or copy preference.
EXP010 subsequently reached hardware-frame output with this selection and
the longer clip. Those two changes were combined under explicit owner
direction, so their individual causal effects were not isolated.

RDD libva trace writes were separately denied. The prototype retains its
strict evidence requirement, so missing traces still leave the aggregate
INCONCLUSIVE even if a future run improves frame hand-off. The experiment can
record that narrower improvement without awarding full browser acceptance.
EXP010 also exhausted the old 64 MiB `/run` filesystem with retained profiles,
causing incomplete logs. Future source separates browser state from that small
filesystem and checks available RAM-backed storage before and during a trial.

Chromium EXP011/012 used the same 30-second clip and a private CDP recorder.
The first selected software; disabling its small-video software-first policy
then selected `VaapiVideoDecoder` with platform decoding true. Two dropped
frames and an observed GPU sandbox initialization failure keep strict
acceptance open. The hardware-only test's private arguments disabled
`ResolutionBasedDecoderPriority` to compare identical small clips; ordinary
browser defaults were not rewritten.
These private operator-authorized same-boot exceptions do not add a generic
retry bypass to the public prototype or change the ordinary browser menu.

The source review uses Mozilla's exact
[`FIREFOX_154_0_RELEASE`](https://github.com/mozilla-firefox/firefox/tree/FIREFOX_154_0_RELEASE)
tag: `FFmpegVideoDecoder.cpp`, `FFmpegVideoFramePool.cpp`,
`widget/gtk/DMABufSurface.cpp`, `widget/gtk/nsAppShell.cpp`,
`gfx/thebes/gfxPlatformGtk.cpp` and
`security/sandbox/linux/broker/SandboxBrokerPolicyFactory.cpp`. VA trace syntax
comes from [libva 2.24.1](https://github.com/intel/libva/blob/2.24.1/va/va_trace.c).
These references explain implementation choices, not hardware acceptance.
