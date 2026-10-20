# Boot-only PS4 UVD token candidate

This inactive patch follows the frozen **68-patch poll-dev1** source and adds
one guarded transport experiment. It is not in an active series or target.
It has host tests; kernel compilation and hardware testing are separate steps.

The v2 source correction preserves exact STATUS2 while fetch is held. Once
fetch is enabled, it accepts STATUS2 or STATUS3 by ignoring only public UVD5's
`UVD_STATUS__RBC_BUSY_MASK=1`. Every other status bit and all other guards stay
strict. V1's private build and stage remain frozen and untested on hardware; the revised
candidate uses a separate `uvd-token-dev2` build identity.

`amdgpu.ps4_uvd_token=1` requires the existing readiness and poll-state gates.
The new parameter is default-off and read-only (`0444`), with no runtime trigger.
The old gate-off startup, held checks, RBC-release checks and readiness loop
remain unchanged. A missing poll-state gate or invalid ring descriptor is
rejected before readiness setup or firmware access. The token gate alone
does not enable the readiness path.

The successful poll-dev1 observation motivates this candidate. It did not
execute a ring command or establish decoder support. The candidate continues
to park initial GPU initialization permanently on every terminal outcome;
normal TTM/client work is not resumed.

## Exact operation

The canary lends its retained, pinned 4096-byte GTT ring CPU mapping through a
small descriptor. It validates the BO type, pin, resource, exact size, pointer
alignment, retained state and completed-precheck control flow. The private
VMID4 address is `0x300308000`, derived from slot776. The BO's native GART
alias is not substituted for that address. No BO, VMID or page-table lifetime
is transferred, and no page inventory is appended again.

The source adds a byte-identical copy of the existing
[`ps4_uvd_ring_plan.h`](../ps4-uvd-ring/ps4_uvd_ring_plan.h) as
`amdgpu_ps4_uvd_ring_plan.h`. The host test verifies that exact linkage and runs
the existing packet fixture against the copied kernel header.

The fixed **single-attempt EXP002** values are:

| Field | Value |
| --- | --- |
| CPU baseline | `0x43505532` |
| Ring-written result | `0x55564432` |
| Total initialized ring | 4096 bytes |
| Submitted prefix | 16 DWORDs / 64 bytes |
| Raw WPTR publication | `16` exactly once |
| Conditional final raw RPTR | `16` |
| Transport deadline | 250 ms, starting before fetch-enable |

Neither value is the earlier public test's `CAFEDEAD`/`DEADBEEF` pair or the
host planner fixture's result. There has been no prior hardware submission
of this pair in either candidate build. An already matching result is rejected.
Do not reuse this fixed token for a later accepted boot; revise and pin a fresh
attempt identity. The CPU writes only the baseline to the context register.

The first pair is `PACKET0(UVD_CONTEXT_ID,0)` plus the result token. Every
remaining pair is `PACKET0(UVD_NO_OP,0)` plus zero. The entire page is initialized;
only the first16 DWORDs are published. There are no codec, GPCOM, IB, fence,
semaphore or trap commands.

While fetch is held, the probe checks exact STATUS2/MASTINT2, full RBC state,
zero pointers/IB size, auxiliary registers, VMID4 page-table base, requester
and cache selectors, BARs, cache-window layout, reset count and sampled fault
status. The old strict predicates are not reused after publication: the new
transport predicate explicitly handles each phase.

The empty-enabled and submitted phases require `(STATUS & ~1) == 2` while
retaining the exact raw sample in terminal evidence. The bit0 exception comes
from the pinned public UVD5 header; its ring test does not require an idle
status. This comparison does not establish Liverpool busy semantics. Busy or
idle is not used as evidence of DMA quiescence.

It performs an immediate baseline readback, then `mb()` plus the existing HDP
helper and another complete guard/context sample. It clears **only NO_FETCH**,
changing `RB_CNTL` from `0x1101080c` to `0x1100080c` while WPTR is still zero.
It verifies that transition before the sole raw WPTR16 store. It checks the
deadline after samples and again immediately before the pointer store.

During bounded polling, the exact result plus raw RPTR/WPTR16 is required.
RPTR16 with the old baseline remains pending because sequential reads can
race. A result observed at or after the deadline is a timeout. Any unexpected
configuration, pointer, context, reset or sampled-fault state terminates.

The only new direct hardware stores are the context baseline, NO_FETCH-only
RBC word and one WPTR value. The existing locked indexed-read helper also
selects/restores `UVD_CTX_INDEX` while checking requester state; it does not
write `UVD_CTX_DATA`. Tests exercise that actual helper and enforce its restore.

No terminal outcome rewrites pointers, re-inhibits fetch, clears interrupts,
resets, retries, releases firmware, frees BOs or removes mappings. All resources
stay retained until full poweroff. Terminal reports preserve the actual
deciding read's namespace/register/value/mask/expected value. CORE fields are
all sampled; immediate baseline failure reports its separate read and does not
pretend that CORE was resampled. No progress messages occur inside the polling
loop. The passive IRQ report remains observational.

## Host verification

Use exact baseline and patched source roots, the real register headers and
the existing planner inputs:

```sh
python3 experiments/ps4-uvd-token/check.py \
  --baseline "$BASELINE_SOURCE" --source "$CANDIDATE_SOURCE" \
  --headers "$CANDIDATE_SOURCE/drivers/gpu/drm/amd/include/asic_reg" \
  --planner experiments/ps4-uvd-ring/ps4_uvd_ring_plan.h \
  --planner-fixture experiments/ps4-uvd-ring/ring_plan_test.c \
  --report "$REPORT_DIRECTORY"
```

The checker validates three frozen source hashes, replays the four-file patch
into a temporary tree, verifies identical output, compares every old helper
and normalizes only the explicitly gated additions to prove legacy flow
preservation. It extracts the actual owner check, descriptor check, indexed
reader, token flow, early gate and readiness dispatch into a synthetic fixture.

The fixture independently parses the emitted page, constrains every MMIO
store, and covers 26 event scenarios, 19 exact prepublication guard failures,
gate-off operation, bad descriptors/ownership, interval overflow, stale tokens,
direct baseline and pointer readback failures, fault/reset/selector changes,
both deadline boundaries and transient drained-baseline/token-without-pointer
states. These include busy during empty-enabled, submitted, and both phases;
held-busy rejection; unknown bits with or without busy; and the exact saved
raw STATUS and deciding mask. Both that fixture and the original packet fixture run optimized and
under ASan/UBSan. The model does not emulate real UVD, interrupts or coherency.

## Remaining hypotheses

Public UVD5 gives byte-based `RB_BUFSZ=12`, raw DWORD pointer publication and
16-DWORD padding precedent. Its normal running ring keeps `NO_UPDATE=1`.
It uses `BLKSZ=1`; this candidate deliberately preserves observed startup's
`BLKSZ=8`, whose Liverpool fetch granularity remains unknown. Legal full-page
padding does not prove pointer units or every possible overfetch safe.

The frozen ready probe did not sample `WPTR_CNTL` or `RPTR_ADDR`. This candidate
requires both raw zero and leaves them untouched. Public5 sets those values
(`upper32(0x300308000)>>2` is zero); a mismatch stops this attempt before fetch
rather than selecting another configuration automatically.

On native x86 APUs, `amdgpu_device_flush_hdp()` may return without an HDP
register write. The existing barrier/helper convention is preserved; no direct
flush workaround is added. Ring requester/coherency and interrupt semantics
remain hypotheses. The SDMA precheck exercised other mapped pages, not this
UVD ring requester. Invalid PTEs do not guarantee fault detection or complete
containment: GMC7's sampled fault status can miss IRQ-acknowledged transients.

Success means only `TOKEN_TRANSPORT_OBSERVED` for that attempt. It does not prove
decode, repeatability, arbitrary command compatibility, interrupt safety,
DMA quiescence, runtime unload or lifecycle support.

Public comparison pin:

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/include/asic_reg/uvd/uvd_5_0_sh_mask.h#L691-L694

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/uvd_v5_0.c

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_ring.c

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_device.c

Existing MIT notices remain unchanged. The copied planner and new host fixture
are original GPL-2.0-only Linux psX work; public AMD UVD code is comparison
evidence, with AMD/Christian König's original notices retained upstream.
