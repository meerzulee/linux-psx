<!-- SPDX-License-Identifier: GPL-2.0-only -->
# Boot-only PS4 UVD indirect-fetch candidate

Patch 71 applies to the exact frozen 70-patch `uvd-writeback-dev1` source.
This separate experiment asks whether a main-ring IB-submit command can fetch
an owned indirect buffer and execute its fresh context token. It does not
complete the predecessor's interrupted compiled audit or claim a writeback
hardware result. The intended release is
`7.2.11-psx-ps4-baikal-uvd-ib-fetch-dev1`, with EXP004 reserved for one attempt.
Source checks, native compilation and hardware acceptance are separate steps.

## Source contract

`amdgpu.ps4_uvd_ib_fetch=1` is an immutable, default-off `0444` gate. It requires
ready, poll-state and token gates and rejects the writeback gate. Invalid
ownership or gate combinations stop before readiness setup and firmware access.
The existing six-argument readiness API remains unchanged. The descriptor and
caller helper retain their historical writeback names; this mutually exclusive
mode borrows the same first 4096 bytes of the retained 128 KiB `uvd_ib` BO.

The existing owner helper requires a pinned kernel TT allocation, the exact
underlying size, positive pre-client fence path, retained state and validated
812-page inventory. The borrowed private VMID4 address is `0x30030a000`, not
the native GART alias. Receiver checks retain exact page/VA, CPU alignment,
interval-overflow and ring/page-disjointness requirements. The SDMA command,
source and destination pages are untouched. No allocation, mapping, reference
transfer, retry, reset, free or client continuation is added.

The producer builds the main ring directly; it never temporarily places a
context-result packet there. Its first 16 DWORDs contain the public UVD5 IB
submission followed by five NOP pairs:

| Pair | PACKET0 register | Value |
| --- | --- | --- |
| 0 | IB_64BIT_BAR_LOW (`0x3c67`) | `0x0030a000` |
| 1 | IB_64BIT_BAR_HIGH (`0x3c66`) | `0x00000003` |
| 2 | RBC_IB_SIZE (`0x3da2`) | `0x00000010` |
| 3–7 | UVD_NO_OP (`0x3bff`) | `0` |

The address high word is the full upper 32 bits, unlike the GPCOM fence's
high-eight-bit encoding. The size is raw `length_dw=16`, as written by the
public UVD5 helper; it is not shifted by the register header's field shift.
The rest of the full 4096-byte main ring is also NOP-padded.

The unchanged GPL planner is used only for the separate IB image. That whole
4096-byte page starts with `PACKET0(CONTEXT_ID, 0), 0x55564434`, followed by
NOP pairs; the submitted IB length is 16 DWORDs. No fence, trap, GPCOM, codec
message or nested IB command is emitted in this mode. The CPU writes only
baseline `0x43505534` to CONTEXT_ID, never the result. An initially matching
context is rejected before either image changes. These identities are reserved
for this single planned attempt and must not be reused for a future accepted
boot. CPU initialization of the IB command operand is distinct from a CPU
CONTEXT_ID result store.

## Observation and terminal behavior

The old token and writeback flow blocks are byte-identical. A separate IB
sampler preserves the original checks with one exception: IB_SIZE must remain
zero while held and fetch-enabled-but-empty; after submission it is sampled
and reported without imposing zero, 16, a countdown, or an invented mask.
All other status, MASTINT, configuration, pointers, requester selectors,
page-table base, sampled fault and reset checks remain unchanged.

The three direct data MMIO stores are the CPU context baseline, NO_FETCH-only
clear and one raw WPTR16 publication. Inherited indexed-selector save/restore
still occurs. Both images are complete before the existing barrier/HDP flush
convention; the helper may be a no-op on x86 APUs, and no cache-register recipe
is added.

The same 250 ms deadline starts before fetch-enable and is checked after each
sample and before success, as well as before WPTR publication. A drained main
ring with baseline context remains pending. Acceptance requires fresh
`0x55564434` and raw RPTR/WPTR16 before the deadline. The first submitted token
observation is saved before timeout/guard classification, so a later failure
cannot erase that evidence. Final reports include context, pointers and raw
IB_SIZE together. An unexpected token before submission is also retained in
the final sample but is not labeled a submitted token. All outcomes retain
resources and park; no DMA drain or safe shutdown is inferred.

## Host checks

`check.py` is self-contained beside the patch and fixture. It pins all four
baseline files, replays the three-file patch, verifies the unchanged planner,
normalizes only the new gate/call sites, and extracts the actual owner,
descriptor, packet builder, samplers, dispatch and terminal functions. It
also proves the IB sampler differs from the original only by its name and
post-submission IB_SIZE predicate.

```sh
python3 experiments/ps4-uvd-ib-fetch/check.py \
  --baseline "$BASELINE_SOURCE" --source "$CANDIDATE_SOURCE" \
  --headers "$CANDIDATE_SOURCE/drivers/gpu/drm/amd/include/asic_reg" \
  --planner experiments/ps4-uvd-ring/ps4_uvd_ring_plan.h \
  --planner-fixture experiments/ps4-uvd-ring/ring_plan_test.c \
  --report "$REPORT_DIRECTORY"
```

The fixture independently decodes both full pages and derives the simulated
context token only from the IB. It rejects main-ring result tokens, unknown
or forbidden packets, nested IBs, address truncation/escape, shifted size and
invalid padding. It covers 32 IB events and 16 exact preconditions, descriptor
bounds/alias rejection with no partial writes, gate conflicts, stale context,
ring drain without IB execution, delayed execution, token before pointer
advance, raw IB_SIZE variants, exact deadline boundaries and preservation of
a token before a later fault or timeout. Adjacent page canaries remain intact.
The original 35-event writeback suite, its 16 preconditions, token-only and
gate-off paths run against the same extracted candidate. Actual-flow and the
original planner fixture both run optimized and under ASan/UBSan. They model
MMIO, time and ownership; they do not emulate UVD.

The actual builder images are pinned independently:

- Main ring CRC32 `e5b6afa6`, SHA256
  `03c4bd41b6ca4bad2646b50a5cff20a99436b66f40cacb57496c326f341bf4df`.
- IB CRC32 `e6268505`, SHA256
  `8338f9806853aeda9c9ad50ff44fbf3fc0e650996612f29a228173d34bdfdacc`.

## Public comparison and limits

UVD5 provides the six-DWORD LOW/HIGH/SIZE submission used here. UVD4.2 uses a
different legacy BASE/SIZE form; UVD6 prepends a VMID command. Existing IB_VMID4
programming is retained. Public `amdgpu_uvd_send_msg` demonstrates PACKET0/NOP
pairs in a 16-DWORD IB, but its stock IB test uses firmware create/destroy
messages and fences. Its userspace parser whitelist omits CONTEXT_ID; that
software admission rule does not establish hardware execution semantics for
this bounded kernel-owned diagnostic. No userspace parser policy is changed.

Liverpool acceptance of this UVD5 submission format and of CONTEXT_ID inside
an IB remain hardware hypotheses. Even a positive observation would not prove
exact length units, general IB completion, DMA quiescence, decoding, safe
interrupt handling, unload/reset or repeatability. Host bounds and sampled
fault checks do not establish outside-allocation containment or detection of
every transient device fault.

Comparison source is public Linux/AMDGPU commit
`a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea`, including AMD and Christian König's
UVD work. Existing MIT notices are preserved; the new adapter in those source
files is original work. The existing GPL-2.0-only planner remains unchanged;
the new host checker and fixture are original GPL-2.0-only code. Source
artifacts contain no proprietary firmware bytes, private UART captures or
decompiled retail code. Device firmware remains a separate runtime input.

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/uvd_v5_0.c#L558-L568

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_uvd.c#L989-L1005

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_uvd.c#L1130-L1147

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_uvd.c#L1309
