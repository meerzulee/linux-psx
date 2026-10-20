# Fixed-reference public AVC DECODE bytes

This unintegrated component plans one legacy UVD AVC DECODE for the existing
`h264-cb-idr-64x64-v1` fixture. It writes caller-owned CPU memory only. It
contains no device access, allocation, firmware, publication, polling, storage
operation, general AVC parser or VA-API implementation. The current CREATE-only
kernel/candidate was not changed by this component.

The original fixture is 2905 bytes, SHA-256:

```text
7dc73a00614226ee4e7dc222109f6fa7af17a6e03496c0fac3ffb200ee45cb62
```

Before calling the planner, the caller must compute SHA-256 over the actual
readable fixture buffer and put the result in `verified_fixture_sha256`.
The 32 expected digest bytes are exported as `ps4_uvd_decode_fixture_sha256`.
The helper compares the reported digest, exact input length and fixed NAL
prefixes; it does not itself compute SHA-256. Supplying the expected digest
without hashing the input violates the API precondition.

## API

```c
struct ps4_uvd_decode_input input = {
	.vcpu_segment_base = 0x300000000ULL,
	.main_va = 0x300308000ULL,
	.work_va = 0x30030a000ULL,
	.main_bytes = 4096,
	.work_bytes = 0x40000,
	.handle = owned_nonzero_handle,
	.fence_initial = initial_value,
	.fence_sequence = distinct_target_value,
	.fixture = verified_fixture,
	.fixture_bytes = verified_fixture_bytes,
};
/* Compute SHA-256 over input.fixture/input.fixture_bytes into this array. */
memcpy(input.verified_fixture_sha256, computed_fixture_sha256, 32);
ret = ps4_uvd_decode_reference_build(plan, sizeof(*plan), &input);
```

Allocate the approximately 15KiB plan on the heap, outside a small kernel
stack. The header documents writable/readable object requirements. Every
rejection leaves the entire output untouched. Validation covers exact output
size/alignment, CPU range arithmetic and prohibited aliases, nonzero handle,
distinct fence values, fixture bounds/digest report/prefixes, page alignment,
sufficient work size, complete GPU interval overlap/overflow, the asserted
256MiB segment and the fence store's 40-bit encoding.

The independent message API is:

```c
ret = ps4_uvd_decode_msg_reference(page, 4096, handle);
```

It supports an unaligned CPU byte buffer, clears the entire page and rejects
NULL, a wrong size, zero handle or overflowing CPU range without modifying
output. It serializes the fixed picture descriptor only; it has no fixture
argument and does not assert a bitstream's identity.

## Plan and mapped work contract

| Work offset | Owned mapping | Purpose |
|---|---|---|
| `0x0000` | `0x1000` | IB page; caller's CREATE IB may occupy the first64 bytes |
| `0x0080` | within IB page | `ib_le[128]`: this exact32-DWORD DECODE job |
| `0x1000` | `0x1000` | matching64×64 CREATE page, populated by existing serializer |
| `0x2000` | `0x1000` | `fence_page_le`: guard words, initial value at cell`0x80` |
| `0x3000` | `0x1000` | `message_le`: DECODE, declared3556 bytes, zero page tail |
| `0x4000` | `0x1000` | `feedback_le[2048]`: first DWORD2048, other bytes zero |
| `0x5000` | `0x1000` | `bitstream_le`: original fixture bytes591..2904, then zeros |
| `0x6000` | `0x6000` | linear NV12 output, both strides256, UV relative offset`0x4000` |
| `0xc000` | `0x27000` | DPB, logical size157184 bytes |
| `0x33000` onward | remaining work | optional DESTROY page and unused guard area |

The helper requires at least `0x33000` page-aligned work bytes; `0x40000` is
the recommended complete allocation. Main ring is a separate4096-byte page.
It returns addresses/sizes in `layout`, dimensions-only `create_params` for
`ps4_uvd_msg_create_avc`, and explicit little-endian packet/message arrays.
The existing CREATE serializer/header remains an unchanged dependency.

Only the present arrays are initialized. Copying them into actual mappings,
initializing unused page/BO guards, initializing the target and DPB, verifying
every DMA/PFN/PTE, and retaining all resources are the caller's obligations.
No firmware/runtime backing may be repurposed as output or DPB. The mapper's
planned256KiB work allocation needs64 pages and844 total unique pages, with
work slots778..841, separate from SDMA slots1021..1023.

`fence_page_le` is an initialization image for the entire fence page. Apply
it once before publication. If the caller also uses CREATE cell`0x40`, set
that cell afterward and preserve it; never copy this image over a live page.

## Exact message and command policy

The DECODE has legacy stream0, flags1, dimensions64×64, DPB size157184,
DB pitch64, BSD length2432, target pitch256 and UV offset16384. Its AVC
profile is baseline0, level_idc10, SPS flags5, PPS flags8, chroma format1,
POC type2, initial QP minus26 equal−8, flat16 scaling arrays and zero reference
lists. `num_ref_frames` stays0: the seventeen DPB slots are Mesa allocation
policy. Extension support is a one-byte1 at offset3296. All other bytes,
including reserved fields and the unused page tail, are zero.

The payload is the original2314-byte IDR slice beginning with `00 00 01 65`;
the reported length is padded to2432, a multiple of128. No escaped payload
bytes are rewritten. SPS/PPS fields are supplied by the fixed descriptor.

The32-DWORD DECODE IB follows public Mesa order exactly:

1. MSG_BUFFER0 → work+`0x3000`.
2. DPB_BUFFER1 → work+`0xc000`.
3. FEEDBACK_BUFFER3 → work+`0x4000`.
4. BITSTREAM_BUFFER`0x100` → work+`0x5000`.
5. DECODING_TARGET_BUFFER2 → work+`0x6000`.
6. ENGINE_CNTL register value1.

Each resource command is DATA0 low32, DATA1 high32, then CMD=`id<<1`:
six DWORDs each. Engine control contributes two. This branch needs no separate
session/HW context or external IT table. `main_le[64]` launches the DECODE IB
at work+`0x80`, length32, appends the eight-DWORD public polled fence prefix
for work+`0x2080`, then a NOP pair. The upstream six-DWORD trap suffix is
intentionally omitted. This is not the full upstream ring self-test.

CREATE must complete separately under the same handle using
`create_params={64,64,0}`. The main template can be appended after a CREATE
launch/fence on the same ring; publication and fence-gated advancement remain
outside this helper. Do not rewind pointers, reinitialize live arrays or
invent a hardware completion claim from generated bytes.

For readback, concatenate first64 bytes of each of64 Y rows, then first64
bytes of each of32 UV rows. Expected visible6144-byte NV12 SHA-256 is exported
as `ps4_uvd_decode_expected_nv12_sha256`:

```text
12404191ab945b7478093a852545adfbaa9af44bd32f83ccf24f489e385628df
```

Save the original pitched output, the complete2048-byte feedback and any
message-page changes as raw evidence. The public source used here does not
define a feedback-status parser or an immutable firmware-message contract.

## Pinned sources and notices

Mesa26.1.7 `src/amd/common/ac_uvd_dec.c`:

- `157–168`: resource command emitter.
- `170–195`: dimensions-only CREATE.
- `226–299`: exact AVC picture population.
- `480–579`: complete DECODE construction and resource/engine order.
- `96–145,607–669,695–723`: context, feedback and DPB sizing/family branches.

`ac_uvd_dec.h:114–164,335–420` defines the message ABI. The fixture's SPS/PPS
and slice fields are recorded in its pinned FFmpeg trace. Flat16 matrices are
resolved H.264 baseline defaults; Mesa receives already resolved matrices.

Mesa `src/gallium/drivers/radeonsi/si_video_dec.c:246–255` builds and flushes
CREATE before first DECODE construction at`489–634`; `328–355` establishes
128-byte zero padding. Its ordinary legacy linear surface allocation uses
256-byte pitch alignment (`src/amd/common/ac_surface.c:923,943–949`).

Linux commit `a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea`,
`uvd_v5_0.c:492–515,558–569`, supplies IB-launch and fence-prefix bytes.
Their original author credit and all upstream copyright/permission notices
are retained in `UPSTREAM-NOTICES`; complete pinned source copies accompany
each host receipt. Tests also pin the saved UVD5 register header.

https://archive.mesa3d.org/mesa-26.1.7.tar.xz

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/uvd_v5_0.c

The archive's existing acquisition receipt records that its signature was
not verified; this component does not change that limitation.

## Host checks and integration boundary

From the workspace root, choose a new receipt directory:

```sh
python3 -B linux-psx/experiments/ps4-uvd-decode-message/check.py \
  --output lab/uvd-2026-10-20/driver-integration/decode-message-01/new-run
```

Mac `macos-03` and final `macos-04` passed44 actual-source cases under O2 and ASan/UBSan, with
five exact-witness mutants detected: wrong AVC level, omitted CMD shift,
wrong feedback length, insufficient work accepted and digest check removed.
Tests execute unchanged extracted Mesa send/AVC/DECODE functions on synthetic
arrays, compare their complete message/feedback/IB output and public Linux
IB/fence bodies, independently assert28 public field offsets, bind the input
and expected-output SHA-256s and parse picture values from the separately
pinned fixture trace. They cover rejection immutability, bounds, CPU/GPU
overlap/overflow, alignment, minimum/adjacent/end-of-segment mappings,
40-bit fence limits, bitstream padding, CREATE interoperability and guards.

Two earlier failed receipts remain preserved. `macos-01` found a missing VC1
profile member in the minimal host reference context; `macos-02` found the
fixture using Darwin's EBADMSG94 while the implementation correctly returned
kernel EBADMSG74. Both host wrappers were corrected; implementation assertions
and compiler warning flags were retained. No native Linux check was run by
this task; that verification belongs to the primary integration.

The caller still owns matching CREATE submission, one coherent queue
publication sequence, full mapped-page qualification, bounded fence handling,
raw message/feedback/output extraction and safe terminal behavior. The helper
establishes consistent CPU bytes/arithmetic only. It proves no PS4 firmware
acceptance, ordering, completion, decoded pixels or hardware support.
