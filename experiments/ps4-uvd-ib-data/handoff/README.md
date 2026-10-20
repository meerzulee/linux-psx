# IB-DATA private handoff

The public source uses synthetic USB/partition identities. These historical
media tools intentionally reject a real device with those defaults. An
operator must supply and review their own authorized target plan; the examples
do not identify the development console or its media. No storage guard was
removed for publication. Frozen private experiment receipts remain unchanged.


This directory owns host staging, guarded USB transactions and saved-UART
extraction for the frozen IB-only DATA0 marker diagnostic. It does not build a
kernel, allocate an experiment, operate the PS4 or implicitly restore anything.
The actual experiment is declared by root after source/build/compiled review.

The retained installation source is Revision C from EXP-20261020-001:

- kernel `439a2c593261e1afb7d62c34a7555b048e4f9c0eb91b83c78ee44046886ade92`
- initramfs `cf2102f52b8b5d048538e1dd410b2c9b43ec81d2a567294d6204578aa83103f7`
- bootargs `c779e4fa2dd5f36e5aacc384438cf09795a50185a4d6993efedabb4145205f52`

`install-source.json` pins its exact five-file set, complete module manifest,
plan and successful installation receipts. These describe last installed
bytes, not a fresh assertion of physical state. The owner requested retaining
that test image and continuing development. Native restoration is **optional,
explicit and never automatic**.

The stage keeps two distinct five-file copies:

- `install-from/`: exact Revision C files, including its kernel and arguments.
- `old-boot/`: exact native rollback. The new USB recovery directory is always
  populated from these native files; Revision C never replaces native recovery.

Before installation, the live five-file USB set must equal Revision C exactly.
The full native and Revision C kernel-module sets are checked; the complete
new module release is installed separately before any boot-file commit. The
five-file recovery, private mounts, live serial/UUID/size/host-root exclusion,
read-only fscks, per-file readback and unmount checks are retained.

The only changed existing transaction functions relative to the frozen
status-observation implementation are `install_boot`, `rollback_hashes`,
`preflight_paths` and `main`. Nineteen existing functions remain byte-identical,
including device identity, archive parsing, module installation and native
restore order. New helpers are `install_boot_hashes`, `known_recovery_states`
and `verify_install_source_modules`. Their exact differences are in the lab
`REUSE.json` and `TRANSACTION-ADAPTATION.diff`.

Recovery admits only states reachable by C→IB initramfs/args/kernel commits,
and native-kernel-first restoration prefixes from each such state. With the
unchanged initramfs this is eight distinct states, not all combinations of
individually known hashes. Interrupted temporaries must match exact planned
bytes. Unknown files, recovery directories or states stop the operation.

## Host checks

From the workspace root:

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-ib-data/handoff/test_handoff.py \
  --report lab/uvd-2026-10-20/ib-data/handoff/test-evidence/HOST-ATTEMPT.json
```

The suite uses temporary directories and mocked subprocess/device operations.
It covers the new receipt schema, dual extracted-source receipts and exact
mutation witnesses, synthetic host staging with separate C/native copies,
exact retained-function identity, truthful unobserved-console/Orbis/off installation, rejected concurrent
launching and missing/stale live USB identity, all eight valid recovery states, ten other known-hash combinations
that must be rejected, interrupted commit recovery, and the new log rules.
No test mounts or probes media, runs a UART command, changes privilege or
executes a kernel. Use a new report filename; preserve failed attempts.

## Source/build contracts

The stager consumes the new native run layout:

```text
lab/uvd-2026-10-20/ib-data/build-preparation/native-01/
  receipts/{identity,prepare-base,prepare,build,package,review,module-tree,project-inputs}.json
  project/output/7.2-ps4-baikal-uvd-ib-data-dev1/
```

It checks the complete native phase chain, 72 patches, exact four-file source
scope, native tool/compiler provenance through the reviewed manifest, complete
41-module/55-file tree, source/input/module archive bindings and actual output
hashes. It retains both matching host fixture receipts, their compiler/host
identities, exact generated fixture identity and the primary's necessary
mutation set/witnesses. It does not manufacture an approval or infer hardware
acceptance from these reports.

The scoped compiled review requires these fields (extra evidence allowed):

- `status: PASS_SCOPED_COMPILED_REVIEW_NO_HARDWARE_ACCEPTANCE`
- `target`, `release`, `patch_sha256`, `ready_source_sha256`
- `source_sha256`: the exact four paths/hashes in the primary `source_pins`
- `ready_object_sha256`, `canary_object_sha256`, `vmlinux_sha256`
- `kernel_sha256`, `modules_archive_sha256`, `config_sha256`
- `native_build_receipt_sha256`: actual new `receipts/build.json`
- `source_review_sha256`: actual new `receipts/review.json`
- `must_fix_findings: []`, `hardware_acceptance: false`
- `prior_interrupted_reviews_remain_incomplete: true`

Root supplies an actual scoped compiled review. No new third-party approval
step is introduced. The native schema is documented in `../NATIVE-BUILD.md`.

## Host stage and final handoff

After native packaging and compiled review, root selects an unused ID on the
actual session date, Asia/Bishkek. None is reserved by this tooling task. The
runner rechecks the date immediately before starting the bounded session.

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-ib-data/handoff/prepare_stage.py \
  --run lab/uvd-2026-10-20/ib-data/build-preparation/native-01 \
  --compiled-review lab/uvd-2026-10-20/ib-data/compiled-audit/compiled-review.json \
  --experiment EXP-YYYYMMDD-NNN \
  --stage lab/uvd-2026-10-20/ib-data/handoff/revision-2/stage
```

This command reads host files only and refuses an existing stage. It derives
bootargs from frozen native arguments, then verifies that replacing the
single Revision C status_observation gate with `amdgpu.ps4_uvd_ib_data=1`
produces exactly those same bytes. Ready, poll_state and token remain enabled;
data_control and status_observation are absent. There is no regex stripping
of live arguments and no change to console/memory options.

The original `handoff/stage` and its review remain immutable. Revision 2 uses
the distinct parent `handoff/revision-2/`; its stage, review and runtime receipts
remain separate. After reviewing the actual new stage, root creates
`handoff/revision-2/REVIEWED-HANDOFF.json` beside it, with the following required fields:

```json
{
  "status": "APPROVED_ONE_BOUNDED_TEST",
  "experiment": "ACTUAL_EXPERIMENT_ID",
  "target": "7.2-ps4-baikal-uvd-ib-data-dev1",
  "release": "7.2.11-psx-ps4-baikal-uvd-ib-data-dev1",
  "plan_sha256": "ACTUAL_PLAN_HASH",
  "tools": {"EXACT_TOOL_NAMES": "EXACT_PLAN_TOOL_HASHES"},
  "compiled_review_sha256": "PLAN.files.compiled-review.json",
  "source_review_sha256": "PLAN.files.primary-review.json",
  "build_receipt_sha256": "PLAN.files.build.json",
  "package_receipt_sha256": "PLAN.files.package.json",
  "hardware_acceptance": false,
  "prior_interrupted_reviews_remain_incomplete": true
}
```

The strings above are explanatory placeholders, not executable approval.
Root must pin the completed bytes after review; the validator refuses missing
or inconsistent values.

## Host-isolated installation — tooling revision 2

Installation requires positively identified USB on Omarchynator, isolated host
ownership, and Linux launch held until committed readback and unmount. Current
console power is not required to write detached host USB. Use the explicit
`not-observed` state when it has not been reported; receipts record no operator
power-state report. Actual `orbis-idle` and `fully-off` reports remain supported,
and an explicit concurrent `linux-launching` state is rejected. A missing or
stale USB/launch assertion cannot replace fresh live device checks.

This is the owner's authorized host-workflow correction. Kernel/source/build
and compiled review are unchanged. A prior full-off between attempted
experiments and full-off before returning used media remain separate rules;
do not infer either from a successful host installation.

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-ib-data/handoff/run_test.py verify-only \
  --stage lab/uvd-2026-10-20/ib-data/handoff/revision-2/stage \
  --plan-sha256 ACTUAL_PLAN_HASH --review-sha256 ACTUAL_REVIEW_HASH
```

`verify-only` reads real USB/UART state but mounts/writes nothing and starts no
session. For the authorized real installation, once the host USB/launch-hold evidence
supports the operation:

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-ib-data/handoff/run_test.py install \
  --stage lab/uvd-2026-10-20/ib-data/handoff/revision-2/stage \
  --plan-sha256 ACTUAL_PLAN_HASH --review-sha256 ACTUAL_REVIEW_HASH \
  --confirm-usb-on-host-launch-held --console-state not-observed \
  --state-evidence 'Live exact USB identity verified on Omarchynator; isolated host ownership; Linux launch held until readback and unmount. Current console power not observed.'
```

Use `--console-state orbis-idle` or `fully-off` only for an actual current report. The
wrapper starts exactly one fresh bounded UART session before storage actions,
keeps the continuous logger running, and leaves the session open. It neither
clears capture nor sends a console command. Only the fully committed,
read-back-verified, unmounted receipt plus root's final check yields a launch
signal. Do not automatically rerun a partial installation.

## Observation and optional recovery

The operator launches once, reports the launch, then waits for the terminal
result or root's stop signal. The test parks early; a desktop is not expected.
Stop on a kernel fault, unexpected transition, invalid continuity or the
180-second launch timeout. After the test, fully power off and wait for the
indicator to go completely off before returning USB. No second launch or
in-boot retry belongs to this plan.

Keep the test image unless the owner asks for native recovery or recovery is
needed and root invokes it explicitly. Native restoration is not a required
closeout step. Root closes the bounded session once its actual coherent
sequence is complete, preserving the continuous logger and exact evidence.

If explicit native recovery is needed while this same coherent session is
still active, root can use:

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-ib-data/handoff/run_test.py rollback \
  --stage lab/uvd-2026-10-20/ib-data/handoff/revision-2/stage \
  --plan-sha256 ACTUAL_PLAN_HASH --review-sha256 ACTUAL_REVIEW_HASH \
  --confirm-fresh-poweroff-and-usb-return \
  --state-evidence 'Actual post-test owner report: indicator fully off and USB returned; native restoration explicitly requested.'
```

This restores native kernel first, native initramfs second and native arguments
third, preserving native/C/IB module trees and earlier evidence. After a closed
session, a later separate recovery needs its own declared evidence sequence;
do not reopen or relabel completed sessions to make this command pass.

## Saved-UART extraction

The extractor uses exact recorded `PS4_UVD_IBDATA` fields. It independently
reconstructs eligible progress from complete valid samples whose **end** is
strictly before 250 ms, requires two consecutive joint DATA0+RPTR/WPTR16
samples, and never lets an endpoint create marker/transport/confirmation
credit. Invalid and late REC/SAVED values remain in the result. A valid
regression record can count in END.predeadline_records while contributing no
progress; the extractor preserves that distinction. Late regressions still
terminate. Endpoint direct/sweep failures override successful outcomes without
hiding earlier diagnostic evidence. Deadline and 40-record budget endings are
distinguished.

The command validates the exact plan/review/source/build/compiled-review chain
and actual C→IB install receipt before setting `qualified_marker_observation`.
It also requires completed fault-free continuity, byte fences and one matching
Linux release. Bare parser use yields at most `log_supports_marker`; it does
not qualify source/build identity. Hidden guard reads and indexed state between
endpoints cannot be reconstructed from printed fields.

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-ib-data/handoff/extract.py \
  --raw PATH_TO_SAVED_BOUNDED_RAW \
  --session-json PATH_TO_COMPLETED_SESSION_JSON \
  --stage lab/uvd-2026-10-20/ib-data/handoff/revision-2/stage \
  --plan-sha256 ACTUAL_PLAN_HASH --review-sha256 ACTUAL_REVIEW_HASH \
  --output lab/uvd-2026-10-20/ib-data/handoff/revision-2/runtime/RESULT.json
```

The session JSON may be the closed index record or the existing
`{"index_record":..., "artifacts":...}` envelope. When the envelope includes
its copied raw digest, that digest is checked. The extractor reads saved host
files only; it invokes no UART, storage or module tool. Every result retains
`accepted=0`, `hardware_acceptance=false` and `decode_demonstrated=false`.
Source-level attribution supports one narrowly scoped IB marker observation;
it is not a formal translation proof or working video decoding.
