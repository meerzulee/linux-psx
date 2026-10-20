<!-- SPDX-License-Identifier: GPL-2.0-only -->
# Boot-only PS4 UVD memory writeback candidate

Patch 70 follows the exact frozen 69-patch `uvd-token-dev2` source. EXP002
observed one context token with raw RPTR/WPTR16. This successor asks whether
the public UVD fence prefix writes a fresh value to an owned page and whether
the CPU observes it. It does not submit a video frame.

The intended release is `7.2.11-psx-ps4-baikal-uvd-writeback-dev1`. Source and
synthetic host checks are separate from native compilation and hardware
acceptance. The build tools require the final reviewed source identities;
this directory does not itself authorize a build, installation or launch.

## Exact scope

`amdgpu.ps4_uvd_writeback=1` is default-off and read-only (`0444`). It requires
the existing ready, poll-state and token gates. It cannot enable them itself.
The old token helper is byte-identical. With writeback off, normalization of
only the new API argument and gated call sites reproduces the frozen source.
Invalid page/gate combinations stop before readiness setup and firmware access.

The canary lends only the first 4096 bytes of retained `uvd_ib`. Its underlying
BO must still be a pinned kernel GTT allocation of exactly 128 KiB, already
validated and mapped in the 812-page inventory. The first page is VMID4 slot
778, private VA `0x30030a000`; the native GART alias is not substituted.
The completed SDMA source, destination and command page remain untouched.
No allocation, PTE change, page-inventory append or lifetime transfer is added.

The receiver checks exact page size/VA, CPU alignment and interval overflow,
ring/page disjointness, target alignment/bounds and representability within
the public 40-bit address encoding. The target is at byte offset `0x40`, VA
`0x30030a040`. The CPU initializes all 1024 little-endian words: the target to
`0x43505533`, every other word to `0xa5330000 ^ index`. It never stores the
expected device result `0x55564433` in this page. An initially matching target
or context is rejected before publication. These fresh values are reserved
for one planned EXP003 attempt and must not be reused for a future accepted boot.

The unchanged planner fills the entire ring with legal NOP pairs and produces
the context-token pair. The new C adapter then emits the public fence prefix:

| DWORD pair | Register | Value |
| --- | --- | --- |
| 0 | CONTEXT_ID | `0x55564433` |
| 1 | GPCOM_VCPU_DATA0 | `0x0030a040` |
| 2 | GPCOM_VCPU_DATA1 | `0x00000003` |
| 3 | GPCOM_VCPU_CMD | `0` |
| 4–7 | UVD_NO_OP | `0` |

Each header is `PACKET0(register, 0)`. Only 16 DWORDs are published. The public
helper appends a separate CMD2 trap suffix unconditionally; this probe emits
the prefix directly and omits that suffix. No trap, IB or codec command is
included, and IB_SIZE remains zero. GPCOM registers are not treated as fixed
completion-state guards, since command execution may change them.

## Observation and terminal behavior

Existing token register/ownership checks remain strict, including exact held
STATUS2 and post-fetch STATUS2/3, exact MASTINT2, requester selectors, page-table
base, ring configuration, pointers, IB_SIZE, sampled faults and reset state.
The only direct data MMIO stores remain context baseline, NO_FETCH-only clear,
and one raw WPTR16. Indexed selector save/restore still occurs in the inherited
read helper. CPU publication uses the existing barrier/HDP helper convention.

Each memory sample uses the canary's invalidate-HDP/barrier convention, reads
the target with `READ_ONCE` and little-endian conversion, then checks all 1023
other words. The helper may be a no-op on native x86 APUs; no new cache-register
recipe is added. Terminal evidence retains the sampled target and first bad
guard index/value, alongside the existing deciding register evidence.

The same 250 ms deadline starts before fetch-enable. It is checked after the
combined register/page sample and before success; expiry during the page scan
cannot be accepted. Exact result, context and raw RPTR/WPTR16 must all match.
A drained ring with the old memory baseline stays pending. An unexpected word,
changed guard or sampled fault/reset condition is terminal. Every outcome
retains all mappings and parks; there is no retry, reset, free or client return.

## Host checks

The checker is self-contained beside the patch and fixture. It verifies the
four frozen baseline hashes, replays the three-file patch, checks the unchanged
planner and token-only functions, and extracts the actual new owner, receiver,
packet builder, memory sampler, dispatcher and terminal flow into the fixture.

```sh
python3 experiments/ps4-uvd-writeback/check.py \
  --baseline "$BASELINE_SOURCE" --source "$CANDIDATE_SOURCE" \
  --headers "$CANDIDATE_SOURCE/drivers/gpu/drm/amd/include/asic_reg" \
  --planner experiments/ps4-uvd-ring/ps4_uvd_ring_plan.h \
  --planner-fixture experiments/ps4-uvd-ring/ring_plan_test.c \
  --report "$REPORT_DIRECTORY"
```

The fixture independently decodes the full ring image, rejects trap/unknown
packets and escaping addresses, bounds every simulated CPU/device page write,
and protects adjacent memory with canaries. It covers 35 event cases, 16 exact
preconditions, descriptor/plan overlap without partial writes, stale results,
gate-off and token-only behavior, delayed writeback after a drained ring,
partial/mismatched results, first/last guard changes, result without matching
context/pointers, and deadline expiry during scanning/before publication.
Both actual-flow and the original planner fixture run optimized and under
ASan/UBSan. This is a synthetic model, not an emulation of UVD.

## Evidence limits and attribution

CMD0 routing through the configured VMID4 and CPU-visible coherency remain
hardware hypotheses. Full-page guard observations do not establish outside-page
containment or detect every transient fault. No decode, generic fence support,
interrupt safety, DMA quiescence, reset/unload or repeatability is established.

Protocol comparison is public Linux/AMDGPU at
`a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea`, including AMD and Christian König's
UVD work. Existing MIT notices are preserved; the adapter is original work in
those MIT source files. The existing GPL-2.0-only planner is unchanged and the
new host fixture/checker are original GPL-2.0-only code. Source artifacts contain
no proprietary firmware bytes, private UART captures or decompiled retail code.
Device firmware remains a separate private runtime input.

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/uvd_v5_0.c#L492-L512

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_fence.c#L79-L89

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_device.c#L6816-L6826
