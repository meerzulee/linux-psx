# First Liverpool UVD picture

This development leaf implements one 64×64 constrained-baseline H.264 IDR
through the public legacy UVD message ABI. It uses the existing private
startup and VMID4 transport as Liverpool working assumptions, then performs
CREATE → DECODE → visible NV12 comparison → DESTROY. It is a first-picture
driver caller, with no normal DRM/VAAPI submission interface yet.

`build_candidate.py` deterministically adds the leaf to frozen CREATE attempt03
and emits a complete third patch over the existing fault/readiness baseline.
It preserves the old CREATE and diagnostic implementations. Inputs, generated
source and exact patch hashes are retained separately for each attempt.

The exclusive boot gate is `amdgpu.ps4_uvd_decode_frame=1`, together with
`amdgpu.ps4_uvd_ready=1 amdgpu.ps4_uvd_poll_state=1`. It conflicts with the raw
map/live-preparation and older UVD probe gates. All outcomes park with firmware,
the three command sequences and all owned mappings retained until poweroff.
There is no reset, retry, unload or DMA-quiescence claim.

The frame-only work BO is 256 KiB (844 unique mapped pages including the
unchanged firmware/ring/SDMA allocations). The old modes retain 128 KiB work.
One main ring is initialized once; cumulative WPTR 16, 32, 48 publishes CREATE,
DECODE and DESTROY only after the preceding phase passes. Each phase requires
two consecutive joint memory-fence/context/read-pointer observations ending
strictly before its 2-second deadline. Captured GMC fault arrivals, reset,
mapping or immutable-memory guard changes stop later publications.

The fixed serializer is in `../ps4-uvd-decode-message/`. CREATE uses the same
64×64 parameters. DECODE uses a 157184-byte DPB, 256-byte output pitch and
UV offset 16384. The locally generated 2905-byte H.264 fixture is included in
the experimental source; the caller computes its SHA256 before planning.
Device firmware remains an external unchanged runtime input.

After DECODE completion the caller snapshots the full 24576-byte pitched
target and 2048-byte feedback buffer, gathers 6144 visible NV12 bytes and
compares SHA256 with the independent software reference. A mismatch stops
before DESTROY. Raw failure snapshots are diagnostic and cannot replace a
failed completion result. UART exports the saved target and feedback bytes;
reports contain no subsequent GPU register access.

Source checks and a matching build establish no PS4 decoding result. Hardware
qualification requires completed UART continuity, the exact image identity,
all three phase observations and reconstructed pixels matching the software
reference. Repeatability and a useful live decoder remain later work.

See `NATIVE-BUILD.md` for the native Linux build procedure and
`MEMORY-README.md` for mapping checks. Boot-file installation still follows
the workspace's explicit media identity, UART and rollback rules.
