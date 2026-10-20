# Revision C status-observation handoff

The public source uses synthetic USB/partition identities. These historical
media tools intentionally reject a real device with those defaults. An
operator must supply and review their own authorized target plan; the examples
do not identify the development console or its media. No storage guard was
removed for publication. Frozen private experiment receipts remain unchanged.


These tools prepare one private host stage and, under the primary's reviewed
plan, install or restore the test USB. No experiment is allocated or launched
by source preparation or synthetic tests. They are specific to the frozen
Revision C candidate and the current known USB/native rollback.

`transaction.py` preserves 21 EXP005 operational functions exactly.
`main` is adapted so installation truthfully permits the PS4 waiting in
Orbis while the USB is attached to Omarchynator and the operator holds Linux
launch. Post-test rollback still requires complete power-off and returned USB.
The owner explicitly requested this preparation without shutting down Orbis.
`validate_archive` additionally accepts only a PAX `path` extension exactly
equal to the already validated canonical member name; native packaging uses
this for module paths longer than 100 characters. All other PAX metadata is
rejected. Path traversal, archive links, duplicates, incorrect hashes and
incomplete module sets remain rejected. The adaptations change no device identity, host-root exclusion, unmounted
check, fsck sequence, private mount, rollback-file, module-set, commit-order,
UART continuity or cleanup check. The frozen EXP005 files are untouched.

The provenance and exact main-function diff are private lab files:

- `lab/uvd-2026-10-19/status-observation/handoff/REUSE.json`
- `lab/uvd-2026-10-19/status-observation/handoff/INSTALLATION-STATE-ADAPTATION.diff`

The full source design remains `lab/uvd-2026-10-19/status-observation/DESIGN.md`.
This is STATUS/marker observation with `accepted=0`, never decode acceptance.
The continuous logger is kept running. A fresh bounded session provides the
new test boundary; do not restart or clear capture to “reset UART.”

## Host validation

Run from the workspace root on either host:

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-status-observation/handoff/test_handoff.py \
  --report lab/uvd-2026-10-19/status-observation/handoff/test-evidence/linux-01.json
```

Use a fresh report filename for each materially changed run. The suite uses
mocked commands and temporary files. It reuses unchanged EXP005 transaction
cases, tests all eight partially committed boot states, checks the new Orbis
installation assertion and retained post-test rollback boundary, exact tool
and receipt pins, whole module sets, and saved-log extraction. It never probes
a device, runs a UART command or changes privileges. Test-only metadata stays
in memory/temporary directories. The tests read frozen prior source/bootargs
as host fixtures; they do not change them.

## Stage after the actual native build and compiled review

`prepare_stage.py` is host-only and requires Linux tools `zstd` and `modinfo`.
It rehashes all actual packaged outputs, binds the native phase chain and
primary build review, checks the compiled review, preserves the five native
boot files and 55-file native module manifest, and validates all 41 new module
vermagic values. It creates the stage once and refuses replacement.

Allocate an unused `EXP-YYYYMMDD-NNN` for the actual session date, Asia/Bishkek,
from the existing ledger/session index. Do not use the example placeholder
literally. The runner checks the date again immediately before session start.

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-status-observation/handoff/prepare_stage.py \
  --run lab/uvd-2026-10-19/status-observation/build-preparation/native-01 \
  --compiled-review lab/uvd-2026-10-19/status-observation/compiled-audit/compiled-review.json \
  --experiment EXP-YYYYMMDD-NNN \
  --stage lab/uvd-2026-10-19/status-observation/handoff/stage
```

The compiled review may contain additional findings/evidence, but must bind:

- `status`: `PASS_SCOPED_COMPILED_REVIEW_NO_HARDWARE_ACCEPTANCE`
- `target`, `release`, `patch_sha256`, `ready_source_sha256`, `ready_object_sha256`
- `vmlinux_sha256`, `kernel_sha256`, `modules_archive_sha256`, `config_sha256`
- `native_build_receipt_sha256`: exact native `receipts/build.json`
- `source_review_sha256`: exact native `receipts/review.json`
- `must_fix_findings: []`, `hardware_acceptance: false`
- `prior_interrupted_reviews_remain_incomplete: true`

This is the primary's actual scoped compiled review, not a new external-agent
approval requirement. The host stager does not create or invent it.

After reviewing the completed stage, the primary writes
`stage/../REVIEWED-HANDOFF.json` with the following required fields. Each SHA
comes from the actual bytes; `tools` is copied from the reviewed plan. Extra
review observations may be included. No automated tool in this handoff grants
this approval.

```json
{
  "status": "APPROVED_ONE_BOUNDED_TEST",
  "experiment": "EXP-YYYYMMDD-NNN",
  "target": "7.2-ps4-baikal-uvd-status-observation-dev1",
  "release": "7.2.11-psx-ps4-baikal-uvd-status-observation-dev1",
  "plan_sha256": "ACTUAL_PLAN_SHA256",
  "tools": {"COPY_EXACT_TOOL_NAMES_AND_HASHES": "FROM_PLAN"},
  "compiled_review_sha256": "PLAN.files.compiled-review.json",
  "source_review_sha256": "PLAN.files.primary-review.json",
  "build_receipt_sha256": "PLAN.files.build.json",
  "package_receipt_sha256": "PLAN.files.package.json",
  "hardware_acceptance": false,
  "prior_interrupted_reviews_remain_incomplete": true
}
```

## Install under the one coherent session

The following are primary-agent commands, not instructions for the operator
to type. Use the normal Linux user, not a root shell. Pins must be actual
reviewed SHA256 values. `verify-only` reads device/UART state but starts no
session and mounts/writes nothing.

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-status-observation/handoff/run_test.py verify-only \
  --stage lab/uvd-2026-10-19/status-observation/handoff/stage \
  --plan-sha256 ACTUAL_PLAN_SHA256 --review-sha256 ACTUAL_HANDOFF_REVIEW_SHA256
```

Installation uses the owner's current report that PS4 waits in Orbis, the USB
is on Omarchynator, and Linux launch is held. The previous full power-off
between EXP005 and this preparation remains a separate historical fact; the
installer does not claim the console is currently off. Record both provenance
and the current statement, e.g. the actual `build-preparation/OPERATOR-STATE.json`.

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-status-observation/handoff/run_test.py install \
  --stage lab/uvd-2026-10-19/status-observation/handoff/stage \
  --plan-sha256 ACTUAL_PLAN_SHA256 --review-sha256 ACTUAL_HANDOFF_REVIEW_SHA256 \
  --confirm-usb-on-host-console-idle --console-state orbis-idle \
  --state-evidence 'Actual owner report and OPERATOR-STATE.json: USB on host; PS4 waits in Orbis; Linux launch held. Previous full-off after EXP005 recorded.'
```

This establishes one fresh UART session before read-only filesystem checks
and the actual USB transaction. It never restarts the continuous logger. It
rechecks live serial/UUID/size/unmounted identity, runs read-only fscks before
and after, installs matching modules before boot files, writes verified native
recovery before committing initramfs/bootargs/kernel, and verifies unmounting.
No launch permission follows from a partial receipt. Only the complete success
receipt, fresh valid UART and primary final check justify the launch signal.

An interruption is preserved; do not rerun installation automatically. The
recovery path accepts only the predeclared old/new combinations. Existing
module trees, recovery directories and temporary files are retained.

## Return and restore after the one launch

On the terminal PARKED line, fault, or the 180-second launch timeout, the
operator fully powers off, waits for the power indicator to go completely
off, then returns USB. No second launch, reset or in-boot retry belongs to this
experiment. The primary then runs:

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-status-observation/handoff/run_test.py rollback \
  --stage lab/uvd-2026-10-19/status-observation/handoff/stage \
  --plan-sha256 ACTUAL_PLAN_SHA256 --review-sha256 ACTUAL_HANDOFF_REVIEW_SHA256 \
  --confirm-fresh-poweroff-and-usb-return \
  --state-evidence 'Actual post-test owner report: power indicator fully off; USB returned to Omarchynator.'
```

The wrapper leaves the same session open. The primary reads compact context,
closes the bounded session after restoration, copies the exact raw/events/
context artifacts and closure metadata, and records one ledger conclusion.
Keep the continuous logger running. A logger restart/reconnect invalidates
continuity; it must not be treated as successful acceptance.

## Parse the saved result

`extract.py` never invokes UART. It accepts a copied bounded raw file and
completed-session metadata: either the session's index JSON object, or the
existing `{"index_record": ..., "artifacts": ...}` closeout envelope. When
present, the envelope's raw digest is checked in addition to byte offsets.

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-status-observation/handoff/extract.py \
  --raw PATH_TO_SAVED_BOUNDED_RAW \
  --session-json PATH_TO_COMPLETED_SESSION_JSON \
  --experiment EXP-YYYYMMDD-NNN \
  --output lab/uvd-2026-10-19/status-observation/handoff/runtime/RESULT.json
```

The result retains errors separately from the pattern in saved records. It
checks one matching Linux release, unique END/PARK, record indices/timestamps,
bit16 flags, the independently recomputed pattern/counters, endpoint summaries
and valid terminal core/sweep reports for a qualified class observation.
A `PUBLICATION_BEGIN` line is an intention log; it is not evidence by itself
that WPTR was written. An early guard after that line may still have
`submitted=0`. Every result keeps `hardware_acceptance=false` and
`decode_demonstrated=false`. Screens are operator observations with no assigned
cause; actual timing and gaps come from the records and stated limitations.
