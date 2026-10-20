# Preparation-only handoff tooling

The public source uses synthetic USB/partition identities. These historical
media tools intentionally reject a real device with those defaults. An
operator must supply and review their own authorized target plan; the examples
do not identify the development console or its media. No storage guard was
removed for publication. Frozen private experiment receipts remain unchanged.


This host tooling stages the frozen native-02 preparation bundle and adapts
the prior IB-DATA USB transaction. It does not allocate an experiment, launch
Linux, collect the cached observation, evaluate a hardware result or create an
approval receipt. `extract.py` belongs to the primary agent and is deliberately
absent from this implementation.

The immutable offline bundle retains `executable_install=false` and its
original pending status. A new stage also remains
`HOST_STAGED_PENDING_FINAL_REVIEW`, `executable_install=false`. Only the
primary's separately pinned final plan and `REVIEWED-HANDOFF.json` allow the
existing guarded runner to perform an authorized installation.

## Three distinct artifact sets

- Current installation source: exact EXP-20261020-002 IB-DATA dev1, kernel
  `463eae4c…`, arguments `4d08ab79…`, retained initramfs `cf2102f5…`.
  `install-source.json` pins its whole plan and successful install/completion
  receipts. It describes preserved evidence, not current physical USB state.
- Native recovery: the original five native boot files in `old-boot/`, plus
  the full native module manifest. These never become an IB backup by accident.
- New preparation: bundle manifest `bc47184f…`, kernel `e7abc9a8…`, arguments
  `3f638159…`, all 41 modules/55 archive files, and the unchanged retained
  initramfs. The arguments select preparation-only mode and `debugfs=on`.

The stage copies exact current IB files into `install-from/`, exact native
files into `old-boot/`, and the immutable new bundle under `bundle/`.
`vram.txt` and `release.json` retain their original bytes. The latter remains
development-override metadata; it does not describe the new kernel.

Source and input archives remain external references with exact SHA-256 and
byte count. Staging and installation validation rehash them in place. The
stage is not a portable, self-contained source distribution or public release.

## Explicit observation integration seam

The stager requires a caller-pinned JSON contract. No placeholder contract is
shipped. Its required fields are:

```text
schema: 1
status: DATA_ONLY_OBSERVATION_CONTRACT
release: 7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1
bootargs_sha256: 3f6381594c3f3b080728aa894a8c2aabd28651dcfb5541aee83aff27bcdcded1
scope:
  launch_count: 1
  launch_timeout_seconds: 180
  full_poweroff_before_launch: true
  direct_userspace_evidence_required: true
  completed_uart_continuity_required: true
  cached_status_only: true
  uvd_arm_implemented: false
  hardware_acceptance: false
tools:
  acquisition: {path: explicit file, sha256: exact digest}
  evaluator: {path: explicit file, sha256: exact digest}
  result_binding: {path: explicit file, sha256: exact digest}
```

All three roles are required; additional named helper files may be pinned in
the same `tools` map. Each tool row contains exactly `path` and `sha256`.
Paths may be absolute or canonical workspace-relative paths, with no symlink
ancestors. All files must exist and match before staging, and their original
locations are checked again before installation. The stage preserves copies
under `observation-tools/*.source` as data. It never executes them. The contract
may include further explanatory fields, but cannot change the required scope.

The concrete collection and saved-result tools now exist:
[COLLECTION.md](COLLECTION.md) describes the one-SSH collector and
[EXTRACTION.md](EXTRACTION.md) describes the full offline result binding.
The current workflow pins seven roles, including `capture_evidence`,
`uart_evidence`, `journal_collector` and `host_transport` in addition to the
three minimal stage roles above. A role name and file hash are identity
bindings, not an assertion that a tool implements correct criteria. The
primary final review covers that judgment; the stager itself supplies no
passing hardware result.

## Host stage API

Run only after the actual bundle and observation-tool files are available on
the same host. Supply a new stage directory with an existing parent. The
experiment identifier is selected by the primary at the appropriate session
date; this implementation reserves none. The runner later rechecks its date
and unused status immediately before starting the bounded session.

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-live-prepare/handoff/prepare_stage.py \
  --bundle /absolute/path/to/offline-bundle-native02-01 \
  --bundle-manifest-sha256 bc47184fabd1cca0a3fb5fdc6204dcaceabbddff67902a9eb39290409659de05 \
  --compiled-review-sha256 13e4837544a6ea7faade1bf932d7da2f739c1909df1fb3cdb3f88adf649c09b4 \
  --source-review-sha256 9d65b2adcae90b3da3c9702c3d87c7f44c315ef3220dd781b2691904f2e82c3d \
  --compatibility-status /absolute/path/to/review/BOOT-COMPATIBILITY-STATUS.json \
  --compatibility-status-sha256 8e7c3b89bd59be8a9eeb9f30564433c551a6ed8211efd5be6ba05d546be507e7 \
  --compatibility-disposition /absolute/path/to/review/BOOT-COMPATIBILITY-DISPOSITION.md \
  --retained-ib-stage /absolute/path/to/ib-data/handoff/revision-2/stage \
  --retained-install-receipt /absolute/path/to/ib-data/handoff/revision-2/runtime/install-receipt.json \
  --retained-install-complete /absolute/path/to/ib-data/handoff/revision-2/runtime/install-complete.json \
  --observation-contract /absolute/path/to/final-observation-contract.json \
  --observation-contract-sha256 ACTUAL_CONTRACT_SHA256 \
  --experiment ACTUAL_UNUSED_EXPERIMENT_ID \
  --stage /absolute/new/handoff/stage
```

The descriptive placeholders are intentionally invalid values. Staging reads
host files and decodes the module archive with `zstd`; no device, mount, UART,
privilege or network operation occurs. Module ELF/vermagic and every archived
file are verified through the byte-pinned existing bundle validator. Historical
build receipts remain unchanged alongside the completed compiled review.

Initial identity checks precede copying. A copy or final readback failure
preserves `.preparation-stage-*/` and `FAILURE.json` when storage permits; it
does not publish a complete destination or delete inputs. An existing stage
is never reused. All input, stage, contract, tool and external-archive hashes
are validated again before any media operation.

## Final review and operational reuse

The primary authors `REVIEWED-HANDOFF.json` beside the completed stage. The
validator requires status `APPROVED_ONE_BOUNDED_TEST`, exact experiment,
target, release, plan SHA and tool map, and these binding fields:

```text
bundle_manifest_sha256, compiled_review_sha256, source_review_sha256,
build_receipt_sha256, package_receipt_sha256, compatibility_status_sha256,
observation_contract_sha256, observation_tools
```

It also requires `hardware_acceptance=false`,
`full_poweroff_before_launch_is_separate_prerequisite=true` and
`prior_interrupted_reviews_remain_incomplete=true`. No helper generates this
receipt. The offline bundle, a synthetic PASS, or the stage alone cannot
substitute for it.

The operational interfaces retain the prior runner's options:

```text
run_test.py verify-only --stage DIR --plan-sha256 PIN --review-sha256 PIN
run_test.py install --stage DIR --plan-sha256 PIN --review-sha256 PIN
  --confirm-usb-on-host-launch-held --console-state not-observed
  --state-evidence "Actual live USB identity and held-launch evidence"
run_test.py rollback --stage DIR --plan-sha256 PIN --review-sha256 PIN
  --confirm-fresh-poweroff-and-usb-return --console-state fully-off
  --state-evidence "Actual post-test full-off and USB-return report"
```

These are real hardware interfaces for the primary's later authorized use;
the implementation task executed none of them. `verify-only` does read live
USB/UART state. Installation accepts accurate `orbis-idle`, `fully-off` or
explicitly `not-observed` console state while the verified USB is isolated
on the host and Linux launch is held. An explicit launch in progress is
rejected. Prior full power-off since a parked Linux attempt remains a separate
launch prerequisite. Full-off before returning used media or post-test
recovery is still required; a host install does not attest either fact.

The runner opens one bounded session before storage actions and leaves it
open. It preserves continuous logging, readonly filesystem checks before and
after, exact serial/UUID/size and host-root exclusion, unmounted/private-mount
checks, fresh live identity checks, readback, UART continuity and cleanup.

The installer preserves native and IB module releases, installs all new module
files, then commits initramfs, arguments and **kernel last**. `depmod` can
regenerate indexes; complete kernel-object sets are checked against their
release manifests. Explicit recovery restores the native **kernel first**,
then initramfs and arguments. It accepts only reachable install/restoration
prefixes: eight distinct states here, not arbitrary known-hash combinations.
It preserves earlier recovery directories and never restores automatically.

`REUSE.json` and the two adaptation diffs in the task evidence record exact
function identities. All runner functions are unchanged; transaction changes
inside five functions are wording/comments only, correcting the old C/IB
names to IB/preparation/native. Device, storage and session logic is retained.

## Verification and remaining work

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-live-prepare/handoff/test_handoff.py \
  --report /absolute/new/path/host-test.json
```

Tests use temporary synthetic files and mocked device/privilege/UART commands.
They exercise actual bundle/stage validation, 55-file modules, all reachable
recovery states, rejected cross-combinations, every interrupted boot commit,
host isolation, unchanged operational functions, and missing final authority.
No acquisition or result tool is run. Native testing, actual host staging,
the observation contract, final review and hardware execution remain primary
integration work. The old IB tooling and all frozen kernel inputs are untouched.
