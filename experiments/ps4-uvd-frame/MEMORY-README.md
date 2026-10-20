# First-frame owned memory transform

`memory_transform.py` exports `transform(source_bytes: bytes) -> bytes`.
It accepts only the frozen attempt-03 CREATE mapper, SHA-256
`7c04a9be39867e1208a57e3212daea7d303175a9ee8c7558a2f8e0d4b6bb60df`,
from source receipt
`ae1f68d18a9a70897f8d969f42f1a1258ec27bf0efc78d2701e56bd0029c32f0`.
It rejects source drift and reapplication and consults no files or process
state. The caller writes the complete candidate and owns its source receipt.

Only `amdgpu_ps4_map_canary.c` is transformed. The caller provides these
ordinary declarations and definitions through `amdgpu_ps4_uvd_ready.h` and
the readiness implementation:

```c
bool amdgpu_ps4_uvd_decode_frame_requested(void);
bool amdgpu_ps4_uvd_decode_frame_options_valid(struct amdgpu_device *adev);
```

The options callback must require ready+poll and reject all raw older UVD
leaf probes. The mapper directly rejects its local raw map-only and raw
live-preparation gates, before state or BO allocation. A raw frame request
participates in mapper admission even when the prerequisites were omitted.

The exclusive frame mode owns a `0x40000` work BO at private VA
`0x30030a000`, with work slots778..841 and844 unique mapped pages. Older
modes retain `0x20000`, slots778..809 and812 pages. Shared allocation,
exact-size verification, page inventory bounds/counts, initialization and
ring ownership use dynamic size/count helpers; CPU metadata reserves at
most844 entries. Firmware remains `0x308000`; the normal UVD main ring
remains its own4096-byte BO. No firmware pages are reused.

The existing all-page DMA/PFN/slot uniqueness checks and private PTE emission
loop remain in place. The new frame-only checker reads all1024 private PT
entries, including zero guard holes777 and842..1020, before initial SDMA
publication and again before borrowing the frame backing. These are CPU
mapping preparation/readback witnesses, not proof of all-page device
visibility, device page walking, DMA completion or quiescence.

The full frame descriptor reaches the existing readiness function's final
IB descriptor argument under `frame_mode`; its address is the private VMID4
VA, never the native GART alias. The old CREATE full128KiB and IB-marker
first-page accessor functions remain byte-identical. No new teardown,
free, reset, retry, rearm or post-publication mapping operation is added.

The focused host check extracts the actual functions, private PTE emission
and final borrow/dispatch block from both frozen and transformed sources:

```sh
python3 linux-psx/experiments/ps4-uvd-frame/memory_check.py \
  --workspace "$PWD" \
  --output "$PWD/lab/uvd-2026-10-20/driver-integration/frame-memory-NEW"
```

Use an unused output directory. It verifies source correspondence, compiles
with optimization and ASan/UBSan, compares old-mode mapping/descriptor
results, checks bounds and alias rejection, corrupts each private PT slot
independently, and confirms three deliberate defects are detected. The
options callback is stubbed; the caller must test its actual raw older-leaf
predicate with the composed source. No kernel, firmware or hardware decoding
acceptance follows from these host checks.
