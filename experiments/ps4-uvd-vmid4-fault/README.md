# Captured GMC fault history, revision 2

This isolated prerequisite patch records every nonempty captured GMC handler
observation during the existing armed interval. It does not arm UVD, enable
interrupts, add a report/debugfs endpoint, change resets or consume the new
snapshot in a live worker. Worker/report integration remains outstanding.

Revision 1 retained VMID4 only. Saved EXP-20261018-007 contains STATUS
`0x1c03b002`, which the pinned public field decodes as VMID14. That observation
is sufficient to avoid discarding other VMIDs; it does not identify the fault's
requester or cause. The first revision, its reviews, native object checks and
host receipts remain preserved and do not qualify revision 2.

The generator pins the same native02 observer `.c/.h`, `gmc_v7_0.c` and public
GMC mask header. It emits new copies, a three-file patch and a receipt into a
fresh directory. It never edits the baseline or active series. The GMC hook
and existing MMIO order are identical to revision 1: after acknowledgement
and the empty-tuple check, before cache/log/KFD handling.

The snapshot contains one coherent history under a dedicated early-initialized
IRQ-safe lock:

- a total bucket, updated for every observed handler tuple;
- 16 buckets selected by captured STATUS VMID (mask `0x1e000000`, shift 25);
- an unclassified-status bucket for literal `STATUS == 0xffffffff`, instead
  of assigning that sentinel to VMID15.

Every event updates total and exactly one category under the same lock.
Each bucket has saturating `u64 arrivals`, sticky `saturated`, and first/last
raw ADDR/STATUS/MCCLIENT records. The snapshot also copies existing `active`
and `arm_ns`. `addr` remains the unshifted captured register value. First and
last mean lock-acquisition order, not fault-occurrence time.

The all-ones status class is a literal classification, not proof of a failed
register read. An all-ones status with ordinary ADDR/MCCLIENT and a fully
all-ones tuple are both retained in that class with their distinct raw fields.
Normal VMID15 statuses still enter bucket 15. Category labels support diagnosis,
not cause attribution; a future worker can use total to reject any newly
recorded fault observation.

At `U64_MAX`, a bucket stays saturated while its last record continues to
update. An inactive snapshot or saturated bucket must not establish an
unchanged interval. There is no clear/rearm API; existing arm does not clear
history, and GPU reset does not erase it. Device lifetime, generation and
reset validation remain future caller responsibilities. Future consumers
must format individual fields, not expose raw structure bytes or padding.

Coverage is limited to nonempty tuples reaching this handler while the
recorder is armed. Hardware latch, main-IH or soft-IH delay may make a captured
tuple predate arming. ADDR/STATUS/MCCLIENT are sequential reads, not an atomic
hardware snapshot; re-latching can produce multiple observations. Lost,
coalesced, disabled or unhandled interrupts remain outside coverage. Zero
proves neither fault absence nor containment. The recorder adds no MMIO,
clock read, logging or work submission.

Generate and test from the saved evidence:

```sh
python3 -B linux-psx/experiments/ps4-uvd-vmid4-fault/build_candidate.py \
  --baseline lab/uvd-2026-10-20/live-ready-design/native-lifecycle-evidence/source \
  --mask-header lab/uvd-2026-10-20/vmid4-fault/reference/gmc_7_1_sh_mask.h \
  --mask-sha256 6c3a33360b4ed179cc62fa7ecda27a462552cd740e1409181cbaae1f705704e1 \
  --out lab/uvd-2026-10-20/vmid4-fault/source/attempt-02

python3 -B linux-psx/experiments/ps4-uvd-vmid4-fault/check.py \
  --source lab/uvd-2026-10-20/vmid4-fault/source/attempt-02 \
  --out lab/uvd-2026-10-20/vmid4-fault/host-tests/macos-02
```

Existing directories are refused. Inputs, generated fixture, compiler identity,
patch replay and compile/run logs are retained per attempt, including failures.
The complete prior tool/docs directory is preserved in
`lab/uvd-2026-10-20/vmid4-fault/revision-01-tools/` with its manifest.

The checker compiles actual extracted observer/header/GMC-handler source with
fake register operations and pthread-backed locks. It covers all VMIDs,
sentinel classification, total/category coherence, independent saturation,
plain main IH, delegation/soft IH, first/last records, legacy IH behavior,
no added MMIO/clock reads and mixed-category concurrent snapshots. An explicit
initialized flag in the fixture lock model makes missing initialization a
portable deterministic failure. Ten injected defects must reach exact named
witnesses; arbitrary nonzero exits do not count.

The software fixture is not Linux IRQ or hardware qualification. Native
checks and source/object review must be repeated for revision 2. The enlarged
embedded observer requires a full rebuild before any future linked image;
two isolated translation-unit checks are not a linked-kernel validation.
No native build, stage, firmware, console, UART, USB, privileged operation or
publication is performed by these tools. Revision 2 results and frozen pins
are recorded in `lab/uvd-2026-10-20/vmid4-fault/SOURCE-REPORT-REV2.md`.
