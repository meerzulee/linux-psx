# Captured GMC fault-history design, revision 2

The native02 GMC handler sequentially reads fault address, status and memory
client, then acknowledges before updating its cache or printing. A later
readiness poll may see cleared registers. The existing dedicated observer
counts IH sources across VMIDs and preserves one first claimant per source;
it does not retain a complete captured-GMC history.

Revision 1 added a passive VMID4-only history. The preserved EXP-20261018-007
raw capture at `lab/uvd-2026-10-18/uvd-ready-dev2/runtime/20261018_040536_301621-exp-20261018-007-e972ab65.raw`
has SHA256 `b74f7a633330ecb0a82c30be09352b992f0f639db7825aa75fd9d17a4e90ad28`.
It reports STATUS `0x1c03b002` (for example at printk time 8.685145), which
maps to VMID14 under the pinned mask. This establishes a coverage reason to
retain other VMIDs. It does not identify requester origin, diagnose the cause
or establish a new hardware result. Historical revision 1 evidence is retained.

## Same source basis and hardware-facing hook

Frozen source root:
`/home/developer/Work/psx/lab/uvd-2026-10-20/live-prepare/build-preparation/native-02/project/src/7.2-ps4-baikal-uvd-live-prepare-dev1`.

| File | SHA256 |
| --- | --- |
| `amdgpu_ps4_uvd_irq.c` | `ccf618c976b9a7b80045ffaeb81b7ede0707fa98bef8aca4bc7a7653a3de5480` |
| `amdgpu_ps4_uvd_irq.h` | `17730b60c04f20a7d703151350e26f1ae7657acd77da406614c278ba5ca9e7f1` |
| `gmc_v7_0.c` | `7304d56b2fabbfbd2aff520dd6ee182dcbe1d0faf984f81b843662726971c4a3` |
| `gmc_7_1_sh_mask.h` | `6c3a33360b4ed179cc62fa7ecda27a462552cd740e1409181cbaae1f705704e1` |

Baseline anchors remain:

- `amdgpu_drv.c:2450`: early observer initialization at device allocation.
- `amdgpu_ps4_uvd_irq.c:41–50`: existing one-time arm publication.
- `amdgpu_ps4_uvd_ready.c:2313`: existing parked-probe arm site;
  preparation-only boot does not arm the collector.
- `gmc_v7_0.c:1283–1293`: optional soft-IH delegation, then
  ADDR → STATUS → MCCLIENT → acknowledgement.
- `gmc_v7_0.c:1295–1299`: empty-tuple rejection then fault-cache update;
  the additive recorder call remains between these steps.
- `gmc_v7_0.c:1315–1316`: original late VMID assignment is unchanged.

No GMC register access, acknowledgement, selector assignment, routing or
existing call moves. The patch still changes only observer `.c/.h` and GMC
source. Generic `amdgpu_ps4_uvd_irq_record_fault` and
`amdgpu_ps4_uvd_irq_fault_snapshot` names are retained. No consumer, live arm,
report or endpoint is introduced. Complete live-worker/report integration
is explicitly outstanding.

## Data and classification

`ps4_uvd_fault_history` has 18 buckets: total, 16 captured-VMID buckets, and
one `unclassified_status` bucket. Each bucket contains a saturating u64 count,
a sticky saturation flag, and first/last raw ADDR/STATUS/MCCLIENT records.
The snapshot combines that history with the existing active and arm time.

For each nonempty tuple admitted by the GMC handler while the observer is
active:

1. If captured STATUS equals literal U32_MAX, select `unclassified_status`.
2. Otherwise select `(status & 0x1e000000) >> 25`, an index in 0–15.
3. Under one dedicated IRQ-safe lock, update total and exactly that category.

A normal VMID15 record is distinct from the literal all-ones status class.
All-ones STATUS with other fields ordinary and an entirely all-ones tuple
retain their different raw fields in the same sentinel class. The classifier
makes no failed-read claim. Every observation reaches total; categories are
diagnostic labels, not causal attribution or ownership proof. No IH metadata
VMID replaces the captured-status field.

## Synchronization and lifetime

The legacy IH slots have separate atomics; their counters, wrap flags,
first-claimant records, report and arm behavior remain byte-identical. The
new dedicated lock is initialized by the existing early observer initializer
and protects only the new history. General `adev->irq.lock` is not reused.

The bucket helper records first only when its count is zero, always records
last, and increments unless already U64_MAX. Reaching the maximum makes
`saturated` permanently true. It has no independent lock; both category and
total calls occur inside the enclosing recorder's one critical section.
Thus an ordinary coherent snapshot's total equals the sum of its categories;
with saturation the total is the saturating sum, and exhaustion remains
explicit per bucket. The order is software lock-acquisition order, not
hardware fault time. No new clock sample is taken.

The snapshot requires a live caller-owned `adev` and non-null output. It clears
the output, acquires the existing active publication, and returns explicitly
inactive if unarmed. Otherwise the same lock protects one copy of arm identity
and all new buckets. There is no GPU register, clock, allocation, printk or
workqueue operation. Old IH telemetry and new GMC history are different
observation points; no coherent or one-to-one relationship between them is
claimed.

Initialization is pre-IRQ and must never be repeated on an armed device.
There is no clear/rearm API. Arm does not restart counts; GPU reset does not
clear them. Device destruction ends their lifetime. A future consumer still
needs independent device-reference, generation, reset and interval validation.
An inactive snapshot or saturated relevant bucket cannot qualify an unchanged
interval. Two equal nonsaturated counts establish only that no additional
observations were recorded between those cuts. Zero does not prove fault
absence. Consumers must format fields individually rather than exposing raw
structure storage or padding.

The snapshot is larger than revision 1. Native source/object checks must be
repeated with the actual kernel flags, including its frame-size check; no
kernel frame-size or complete build claim follows from the host fixture.
An enlarged embedded observer also requires a full rebuild before any linked
image can be used.

## Observation limits

These are sequential captured register values, not an atomic hardware tuple.
Hardware re-latching can produce repeated or mixed-time observations. A tuple
may predate arming through a hardware latch, main-IH queue or soft-IH delay.
Acknowledgement precedes recording, so interruption after it can leave an
unrecorded event. Lost/coalesced/disabled interrupts and other fault paths
remain outside coverage. No total-physical-fault count, fault writer,
DMA quiescence, reset safety, decode or UVD readiness is established.

## Verification contract

The complete prior canonical tool/docs directory is preserved before edits
under `revision-01-tools/files/`; its manifest binds every copied file.
Frozen attempt01, all receipts, primary/reviewer records and hardware artifacts
are left untouched. Attempt02 is regenerated from the same exact baseline.
Removing declared additions and reverting the one descriptive comment must
recover all three baseline files byte-for-byte. The GMC candidate itself must
remain byte-identical to revision 1.

The actual-source fixture covers every VMID, the observed STATUS1c03b002 with
disagreeing IH VMID, plain main-IH processing with soft disabled, delegation
and soft-IH processing, literal all-ones classification, normal VMID15, raw
sentinel-versus-full-tuple distinction, first/last integrity, bucket/device
noninterference, independently reaching total/category/sentinel saturation,
legacy IH behavior and no added MMIO/clock calls. Concurrent mixed-VMID and
sentinel snapshots check exact unsaturated total/category sums and per-bucket
monotonicity and record integrity.

The fixture lock has an explicit initialized flag checked before use, so the
named missing-init mutant fails deterministically on both macOS and glibc
rather than relying on an uninitialized pthread mutex's incidental behavior.
Ten injected defects require exact named failure witnesses. Optimized and
ASan/UBSan runs are required. Fake register functions and pthread locks test
software accounting and call ordering only; they do not emulate or validate
hardware or Linux IRQ semantics.
