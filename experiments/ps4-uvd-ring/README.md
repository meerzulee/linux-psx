# PS4 UVD ring packet planner — host preparation only

This original GPL-2.0-only C header constructs and checks the smallest proposed
register-write ring image. It is **not linked into any kernel, patch series,
target, or driver**. It cannot publish a ring, access registers, allocate a BO,
start UVD, send a payload, or establish hardware readiness. Its output is a
public-Linux-based **packet hypothesis**, pending a PS4 adapter contract.

The fixture includes `ps4_uvd_ring_plan.h` directly. That header is intended to
be the same implementation a future reviewed kernel adapter includes, rather
than a separate host model copied from a driver. A future adapter must retain
this linkage and test the actual header it builds. Nothing currently activates
the header's `__KERNEL__` type branch.

## Run the checks

From the `linux-psx` repository:

```sh
python3 experiments/ps4-uvd-ring/check --require-sanitizers
```

The tool builds native host C11 with strict warnings, runs the fixture, then
repeats it under AddressSanitizer and UndefinedBehaviorSanitizer. Without
`--require-sanitizers`, unavailable sanitizer tooling is explicitly reported
as skipped; an actual sanitized fixture failure remains a failure. Set `CC`
or pass `--cc` to select a compiler. Temporary outputs are automatically
removed. This is a host fixture, not a Darwin kernel build or a VM task.

The independent packet interpreter decodes type/count/register fields,
enforces a two-register allowlist and executes a selected prefix. Mutation
checks prove that it rejects GPCOM/trap/IB/semaphore/other-register commands,
malformed counts, zero-filled padding, nonzero NO_OP data, repeated context
writes and wrong tokens. Other checks cover all 32 token bits, exact wire
bytes, canaries, truncated/oversized buffers, pointer/GPU span overflow,
destination overlap, initial pointer errors, stale tokens, malformed result
records and late/wrong-pointer observations. This does not emulate real UVD.

## Proposed byte contract

`ps4_uvd_build_ring()` receives a by-value request, real writable CPU storage
of exactly **4096 bytes**, and a disjoint output plan. The CPU storage must be
4-byte aligned and the plan naturally aligned. The caller's owned GPU base
must be 4096-byte aligned with a representable exclusive end. A base/length
pair cannot prove allocation, requester reachability, aperture limits, VMID,
cacheability or CPU/GPU address equivalence; the adapter must establish them.
The planner does not translate CPU, DMA and GPU addresses into one another.

Every field supplied to the functions is in CPU order. The ring image is
explicit little-endian bytes produced with byte stores, including on a
big-endian CPU. The fixture verifies this wire contract without native word
casts; testing it on a native big-endian machine is not claimed.

| Range | Proposed bytes / meaning |
| --- | --- |
| DWORD 0 | `0x00003dbd`: `PACKET0(UVD_CONTEXT_ID, 0)` |
| DWORD 1 | Caller-selected fresh result token |
| DWORDs 2–15 | Seven pairs of `0x00003bff`, `0`: `PACKET0(UVD_NO_OP, 0)` plus data |
| DWORDs 16–1023 | 504 more complete NO_OP/data pairs, fully initialized but outside the submitted prefix |
| Initial raw RPTR/WPTR | Both exactly `0` |
| Submitted prefix | Exactly `16 DWORDs = 64 bytes` |
| Planned published raw WPTR | Exactly `16`, **not 64** |
| Conditional final raw RPTR | Exactly `16`, with no mask or normalization |

For the fixture's token `0x10234567`, the first eight bytes must be
`bd 3d 00 00 67 45 23 10`. Every subsequent pair is
`ff 3b 00 00 00 00 00 00`. The image has one context write and 511 NO_OP pairs;
the submitted prefix has one context write and seven NO_OP pairs. There are
no explicit trap, GPCOM, IB, codec, fence, semaphore or address-write commands.
Initializing the remainder with legal pairs reduces accidental garbage; it
does not grant permission for hardware to consume beyond the published end.

The token and baseline must be distinct and neither zero nor all-ones. The
token must also differ from the actual context value read before writing the
baseline. The caller must choose a token not used by an earlier attempt and
exclude every competing context writer; the stateless planner cannot prove
either. There is no retry, allocation, mutable static state or hardware call.
Every rejection occurs before any destination write, leaving both the ring
and output plan unchanged. As with a normal C buffer API, the caller remains
responsible for passing valid objects with the advertised capacities.

## Observations are separate from packet construction

`ps4_uvd_classify_observation()` accepts a completed plan and caller-supplied
reads. Both baseline readback and the immediate pre-publication context must
equal the baseline. It demands raw WPTR `16`, rejects RPTR beyond `16`, and
never converts a late observation into a pass. The caller supplies its own
finite monotonic deadline; this header has no timing or polling loop.

`PS4_UVD_EXPECTED_OBSERVATION` requires the exact token **and** RPTR `16`,
with the preceding evidence intact. A token while RPTR is short is incomplete;
a drained pointer with the baseline still present is recorded separately.
A preexisting token, changed pre-publication context, all-ones context or
wrong pointer must not count as consumption. Reads can race, so a single
incomplete snapshot does not prove failure before the deadline.

This classifier trusts the caller's evidence provenance and ordering. Even
the expected observation remains conditional on correct mapping, ownership,
publication and register semantics. It proves neither interrupt safety,
decode, DMA quiescence, safe cleanup, lifecycle support nor acceptance.

## Unresolved adapter decisions

Before any bootable candidate or hardware experiment, review and pin:

- Liverpool packet/register compatibility and raw pointer units. The header
  uses the public UVD5 **DWORD pointer hypothesis**, not recovered PS4 proof.
- Owned BO allocation, retention, mapping, requester VMID, address width,
  aperture boundaries, endian configuration and publication/coherency barriers.
- The PS4 meaning and permissible startup transitions of MASTINT/VCPU/SYS
  interrupt bits, including unsolicited interrupts. A trap-free payload does
  not establish a quiescent interrupt path.
- `RB_BUFSZ` and **`RB_BLKSZ`**, **`WPTR_CNTL`**, and **`RPTR_ADDR`** semantics.
  The public source's values are comparison evidence, not selected PS4 values.
- Startup/readiness ownership and a one-shot finite failure path that retains
  possibly reachable resources. No generic ring begin/end-use callback may
  silently add clock/restart/idle behavior.
- Exact firmware and whole boot-set identities, independent artifact audit,
  continuous UART with one bounded coherent experiment, and native rollback.

These decisions belong in the active
[UVD plan](../../docs/PS4-UVD-PLAN.md). The current header changes no startup
guard and supplies no automatic hardware procedure.

## Public source comparison and credit

The reference is the repository's pinned upstream commit
`a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea`, read with `git show` from the local
`lab/psx-baikal-port72` checkout. Its current branch HEAD is deliberately not
used as the pin. This is an original GPL-2.0-only planner/fixture using public
register constants and packet facts as comparison; it does not include Orbis
code, firmware, private disassembly or raw UART evidence. Linux's compared
UVD5 file credits AMD (2014) and Christian König; its MIT notices and author
credit remain in that upstream source.

| Source at the exact pin | Relevant evidence |
| --- | --- |
| `drivers/gpu/drm/amd/amdgpu/uvd_v5_0.c`, `uvd_v5_0_ring_test_ring()` | Distinct CPU baseline and ring-written `UVD_CONTEXT_ID` result, polled register read |
| Same file, `uvd_v5_0_ring_insert_nop()` and `uvd_v5_0_ring_funcs` | Two-DWORD NO_OP/data pairs and alignment mask `0xf` |
| Same file, `uvd_v5_0_ring_set_wptr()` and `uvd_v5_0_start()` | Raw write-pointer publication and public startup/RBC comparison |
| `drivers/gpu/drm/amd/amdgpu/vid.h` | PACKET0 type 0, 16-bit register field, 14-bit count-minus-one |
| `drivers/gpu/drm/amd/include/asic_reg/uvd/uvd_5_0_d.h` | Context register `0x3dbd` and NO_OP register `0x3bff` |
| `drivers/gpu/drm/amd/amdgpu/amdgpu_ring.c`, `amdgpu_ring_commit()` | Pads to the ring alignment before the barrier and WPTR publication; invokes `end_use` |

Full source URLs:

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/uvd_v5_0.c

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/vid.h

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/include/asic_reg/uvd/uvd_5_0_d.h

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_ring.c
