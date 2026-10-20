<!-- SPDX-License-Identifier: GPL-2.0-only -->
# PS4 UVD main-ring DATA0 readback control

This inactive candidate follows the frozen 69-patch EXP-20261019-002
token-dev2 baseline. Its new patch changes only `amdgpu_ps4_uvd_ready.c`.
It has passing actual-source host checks. Native compilation, artifact review
and a bounded hardware experiment are separate steps. It is not selected by
a normal target or series, and no hardware result is claimed here.

The question is whether a DATA0 parameter write on the direct main ring can
be observed through CPU MMIO before that marker is considered for an IB test.
DATA0 is a parameter/low-address operand, not a documented scratch register.
Its readback, possible firmware side effects and later indirect-buffer
behavior remain hardware questions. EXP004's timeout remains unresolved.

## Exact operation

The default-off, read-only (`0444`) boot parameter
`amdgpu.ps4_uvd_data_control=1` requires the existing token, readiness and
poll-state gates. Invalid gates or a retained ring/page-directory descriptor
are rejected before firmware access or readiness setup. Removing the four
additive blocks reproduces the entire old ready.c. Canary ownership, mappings,
planner, old token helpers, startup and retained parking remain unchanged.

The planner initializes the complete owned 4096-byte ring. Its first two
count-zero packet pairs become DATA0 and CONTEXT_ID writes; remaining pairs
are NOP/zero. One raw WPTR16 publication exposes 16 DWORDs.

| Field | Value |
| --- | --- |
| DATA0 CPU baseline / ring result | `0x43504435` / `0x55564435` |
| CONTEXT_ID CPU baseline / ring trailer | `0x43504335` / `0x43545835` |
| Full-page CRC32 | `cadc2d79` |
| Submitted length / raw WPTR | 16 DWORDs / 16 |
| Deadline | 250 ms, starting before fetch enable |

These values identify one proposed attempt; do not recycle them as fresh
evidence on a later accepted boot. Already-present results are rejected.
The four direct CPU stores write the two baselines, the existing NO_FETCH-only
RBC enable word and one WPTR. The indexed guard reader still selects/restores
its index without writing indexed data.

Immediate readbacks and held/empty-enabled samples must show both baselines
before publication. Polls run the unchanged complete core/guard read sequence
then read DATA0. Two consecutive valid observations must contain both results
and raw RPTR/WPTR16 before the deadline. These are sequential samples, not an
atomic state or a general persistence contract. Marker, trailer and pointer
regressions terminate; ordinary intermediate baseline/result combinations
remain pending. The deadline precedes acceptance and is checked immediately
before the sole publication. First-seen times include software/logging.

`DATA_NOT_OBSERVED_WITH_TRANSPORT` requires a valid trailer/drained-pointer
sample before the deadline and no previously accepted DATA0 result. Transport
first appearing at the deadline receives the generic timeout. Neither class
diagnoses the hardware cause. Every terminal reports saved evidence and parks
with all resources retained; there is no retry, reset or teardown.

CMD (including zero), DATA1, ENGINE_CNTL, IB, fence, trap and semaphore writes
are absent from the stream. No generic job/ring-use callback is introduced.
Public UVD uses CMD=0 for operations; it is not an inert clear. Omitting CMD
does not prove absence of firmware side effects, containment or quiescence.

## Host checks and matcher correction

The checker replays the patch into a temporary copy of pinned source, checks
unchanged legacy bytes, extracts the real control/guard/dispatch functions and
public register constants, and runs synthetic MMIO/time tests. It never
accesses hardware or executes firmware. Run from the workspace with a new
report directory:

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-data-control/check.py \
  --baseline lab/uvd-2026-10-19/token-transport-v2/source/candidate \
  --source lab/uvd-2026-10-19/data-control/source/candidate \
  --headers lab/psx-baikal-port72/drivers/gpu/drm/amd/include/asic_reg \
  --report lab/uvd-2026-10-19/data-control/host-NEW
```

The first optimized attempt failed because two negative-path assertions
searched for ` CORE `, also matching `no CORE resample`. Following the owner's
independent assessment and explicit request, the original worker performed a
narrow fixture-only correction through its normal review process. Only those
two matchers changed to `PS4_UVD_DATA CORE `; write-count assertions remain.
Injected actual CORE reports still fail both intended assertions. Kernel
patch/source, checker, build scripts and failed-attempt evidence are unchanged.

The corrected fixture passed 205 cases in optimized and ASan/UBSan runs:
38 event sequences, 117 phase/guard cases, gates/descriptors, stale values and
gate-off dispatch. An independent parser checks all ring bytes and the CRC.
Six temporary injected defects were detected: zero/two publications, CMD=0,
one confirmation, missing deadline and missing pointer-regression rejection.
Mutations are never applied to the kernel patch or candidate source.
Private receipts: `lab/uvd-2026-10-19/data-control/host-worker/attempt-2-matcher/`.
Host substitutes for MMIO, clocks, barriers/HDP and parking do not emulate
hardware or establish decoder support.

## Build and review records

Native preparation preserves all 718 EXP002 project inputs in an independent
copy, appends patch70 and changes only LOCALVERSION in the config. Its tools
require pinned source and host checks, cap compilation at six jobs, preserve
the baseline and package whole matching modules and corresponding source.
An independent base copy alone is not a newly compiled candidate. Actual
build/staging status is recorded separately in private receipts.

The initial source-worker turn was automatically interrupted for “possible
cybersecurity risk”; that history remains recorded. The later fixture-only
correction did not route a rejected task to another agent or erase the stop.
The older writeback compiled audit and Claude IB-patch review remain
incomplete. Host checks replace neither. The preserved original STATUS.json
is a historical closeout, not the new host-test verdict.

Copied baseline source retains its MIT notices. The fixture/tooling are
GPL-2.0-only project work; public AMDGPU sources retain their AMD/contributor
credit. No firmware, raw UART or proprietary code is included here.

Public comparison pin: `a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea`.

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/uvd_v5_0.c

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_uvd.c
