# Experimental Liverpool UVD runtime

This backend connects the startup used by EXP-20261020-005 to the normal
AMDGPU UVD parser, scheduler and BO interfaces. That experiment recovered one
correct H.264 frame using its separate fixed-picture kernel. The normal runtime
then passed EXP-20261020-006: FFmpeg/VAAPI decoded all 120 distinct 128×96 H.264
I/P frames byte-exactly on the console, with completed UART continuity and
desktop/SSH available. See [RESULTS.md](RESULTS.md) for the scope and artifacts.

The runtime gate is immutable and off by default:

```text
amdgpu.ps4_uvd_runtime=1 amdgpu.ps4_uvd_ready=1 amdgpu.ps4_uvd_poll_state=1
```

It selects Liverpool only. Raw map-only, live-preparation and earlier UVD
probe flags conflict with runtime mode. Firmware remains external at
`amdgpu/liverpool_uvd.bin`; no firmware is included here.

`build_candidate.py` verifies the recorded frame source snapshots, applies
the job ownership hooks, and emits a fresh source overlay and patch. It
preserves the older experimental leaves and the default-off path. The
candidate includes:

- One normal UVD IP block and a 4 KiB ring with one hardware submission credit.
- VMID4 reservation, native GART and VRAM mirrors, and fixed startup aliases.
- Bounded BO pins/references and retained kernel IB spans until trusted
  hardware completion. Canceled/failed jobs close the queue.
- UVD5-style LOW/HIGH/SIZE launches and eight-DWORD CMD0 completion packets.
  Polling replaces the trap packet; WB observation precedes fence signaling.
- Serialized session parsing, file ownership checks and a bounded drain
  before file-close DESTROY. Parser failures are terminal in this version.
- AVC stream0 only, one active handle, and an experimental 1920×1088 envelope.
  Reported dimensions are implementation bounds, not qualification results.

After VMID4 entry, this development version retains device DMA allocations.
It blocks reset, suspend, removal and ordinary shutdown cleanup; operator
recovery is a complete power-off. It has no drain/free/rearm implementation.
These restrictions apply to the runtime gate and do not qualify it for a
general binary release.

The component notes are [MEMORY.md](MEMORY.md), [JOBS.md](JOBS.md) and
[PLAYBACK.md](PLAYBACK.md). `transport_check.py`, `jobs_check.py` and
`session_check.py` exercise actual source against explicit CPU/API stubs.
They test serialization/control flow and do not model DMA or establish
concurrency or hardware acceptance. A full matching kernel/modules rebuild
is required because the embedded device and job layouts change.

The measured sequence is:

```text
verified fixed picture
    → normal runtime source + native build
    → PS4 desktop/SSH boot (one frozen runtime boot)
    → exact 120-frame canonical FFmpeg/VAAPI decode
    → isolated Mesa 26.2.5: clean captured OpenGL/reference on monitor
    → exact 900-frame native 1080p30 decode
    → bounded five-loop 1080p30 playback, zero drops (~1.00×)
    → seeking/profiles, duration and repeated cold boots (remaining)
    → separately qualify release and full desktop adoption
    → additional codecs/VCE as separate work
```

The same running kernel was used for the later Mesa and 1080p tests. No
system-wide Mesa installation, quota increase, reset or clock change occurred.
Physical monitor confirmation of the moving 1080p clip remains pending; clean
captured pixels and the earlier reference confirmation are distinct evidence.
See [the public source guide](../../../docs/UVD.md) for the fact that ordinary
default-target CI does not build this experimental kernel.

The first application fixture is 128×96. The runner forces VAAPI hardware
frames through `hwdownload` and cannot pass with software decoding alone.
Keep one completed bounded UART session for the coherent install/boot/decode
experiment, preserve failures and exact artifact identities, and retain the
known rollback set.
