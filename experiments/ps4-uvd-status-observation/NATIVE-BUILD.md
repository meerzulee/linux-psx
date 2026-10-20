# Private status-observation native build

`native.py` prepares a new project and source/cache from the frozen EXP005
build, applies one explicitly reviewed patch, and builds/packages the complete
matching kernel and module set. It never invokes `build_candidate.py` or the
root `build.sh`. It has no USB, UART, console, privilege, or publication action.

This document and the synthetic tooling receipt do **not** approve a candidate
or show that a kernel was built. Source review, native compilation, compiled
object review, and a bounded hardware experiment remain separate results.
Older incomplete reviews remain incomplete.

## Fixed baseline and new paths

Paths are relative to `--workspace`; the intended native workspace is
`/home/developer/Work/psx` on Omarchynator. Linux x86-64 is mandatory for every
mutating phase. The read-only plan and synthetic tests also run on macOS.

The baseline is:

```text
lab/uvd-2026-10-19/linux-psx-uvd-data-control-dev1
  src/7.2-ps4-baikal-uvd-data-control-dev1

lab/uvd-2026-10-19/data-control/build-preparation/native-receipts
```

The tool pins EXP005's project commit, 735 project inputs, 70 ordered patches,
95,113 complete source entries, source/build/package receipts, module manifests,
and dirty-overlay archive. It checks the frozen `amdgpu_ps4_uvd_irq.c` identity
`ccf618c976b9a7b80045ffaeb81b7ede0707fa98bef8aca4bc7a7653a3de5480`.

The default new run is:

```text
lab/uvd-2026-10-19/status-observation/build-preparation/native-01
  project/
    src/7.2-ps4-baikal-uvd-status-observation-dev1/
    output/7.2-ps4-baikal-uvd-status-observation-dev1/
  receipts/
```

The run must not exist when `prepare-base` starts. Each later phase requires a
successful prior phase and refuses an existing attempt marker. Failures retain
all files and logs. There is no retry, clean, reset, resume, overwrite, or delete
option. Diagnose a failure and explicitly choose a new run after the required
correction/review; do not remove the old receipt to continue.

## Review manifest

The primary owner supplies the manifest after resolving findings and validating
the final source. This tool does not manufacture approval from another agent's
agreement or from a host PASS. The manifest's SHA256 is supplied separately on
every command. The manifest must pin the **running** `native.py`, so subsequent
tool changes require a new review manifest and run.

All file references are workspace-relative regular files with no escaping or
symlink path components. A schema example follows; placeholder hashes/counts
must be replaced from the actual final revision. This example is not a receipt.

```json
{
  "schema": 1,
  "target": "7.2-ps4-baikal-uvd-status-observation-dev1",
  "release": "7.2.11-psx-ps4-baikal-uvd-status-observation-dev1",
  "final_primary_review": true,
  "verdict": "APPROVED_FOR_NATIVE_BUILD_ONLY",
  "unresolved_findings": [],
  "host_cases": 0,
  "sensitivity_count": 0,
  "pins": {
    "candidate": {
      "path": "lab/uvd-2026-10-19/status-observation/source/candidate/drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.c",
      "sha256": "FINAL_CANDIDATE_SHA256"
    },
    "patch": {
      "path": "linux-psx/experiments/ps4-uvd-status-observation/0009-drm-amdgpu-ps4-uvd-status-observation.patch",
      "sha256": "FINAL_PATCH_SHA256"
    },
    "checker": {
      "path": "linux-psx/experiments/ps4-uvd-status-observation/check.py",
      "sha256": "FINAL_CHECKER_SHA256"
    },
    "fixture": {
      "path": "linux-psx/experiments/ps4-uvd-status-observation/fixture.c",
      "sha256": "FINAL_FIXTURE_SHA256"
    },
    "design": {
      "path": "lab/uvd-2026-10-19/status-observation/DESIGN.md",
      "sha256": "FINAL_DESIGN_SHA256"
    },
    "native_tool": {
      "path": "linux-psx/experiments/ps4-uvd-status-observation/native.py",
      "sha256": "REVIEWED_NATIVE_TOOL_SHA256"
    },
    "independent_review": {
      "path": "lab/uvd-2026-10-19/status-observation/review/FINAL-INDEPENDENT-REVIEW.md",
      "sha256": "REVIEW_DOCUMENT_SHA256"
    }
  },
  "host_receipts": [
    {"path": "PATH_TO_FINAL_MAC_RECEIPT.json", "sha256": "MAC_RECEIPT_SHA256"},
    {"path": "PATH_TO_FINAL_LINUX_RECEIPT.json", "sha256": "LINUX_RECEIPT_SHA256"}
  ]
}
```

Receipt validation requires one Darwin and one Linux x86-64 execution, matching
patch/checker/fixture/candidate pins and generated fixture source, successful
optimized and ASan/UBSan results with the declared case count, and the declared
set of at least 12 detected mutations. Compiler path/version/target and host
identity must be present. Receipt bytes intentionally need not match: host and
compiler metadata differ. Copy the exact Mac receipt into the native workspace
as an input; do not alter its identity fields.

`independent_review` preserves the review document's bytes. The wrapper does not
interpret prose or resolve findings. The primary manifest's final decision is
the authority for the source it pins. It authorizes this native build only.

## Commands and phases

Read the plan before a final review exists; this prints static paths and
requirements and does not validate the baseline or create files:

```sh
python3 -B linux-psx/experiments/ps4-uvd-status-observation/native.py plan \
  --workspace /home/developer/Work/psx
```

Supplying `--review` and `--review-sha256` to `plan` additionally validates all
pins, host receipts and the full baseline. A draft manifest may use
`final_primary_review: false`, `verdict: "PENDING"`; planning still reports no
build approval. Every mutating phase rejects that draft.

After the primary review has actually been finalized, use the same arguments
with each phase, one at a time. Replace the manifest filename and digest with
the real final receipt; `REVIEW_SHA256` below is a placeholder.

```sh
python3 -B linux-psx/experiments/ps4-uvd-status-observation/native.py prepare-base \
  --workspace /home/developer/Work/psx \
  --run lab/uvd-2026-10-19/status-observation/build-preparation/native-01 \
  --review lab/uvd-2026-10-19/status-observation/review/PRIMARY-NATIVE-REVIEW.json \
  --review-sha256 REVIEW_SHA256
```

Replace only `prepare-base` with `prepare`, then `build --jobs 6`, then
`package`, inspecting the completed receipt between phases. Allowed job counts
are 1–6. No phase can implicitly execute its predecessor.

| Phase | Work and required evidence |
|---|---|
| `prepare-base` | Snapshot/hash the complete frozen project, source, cache and outputs; validate the EXP005 receipts; clone project with `--no-hardlinks`; copy precisely its recorded overlay; copy source/cache with `cp -a --reflink=auto`; compare every copied byte/kind/mode; reject shared baseline inodes, escaping symlinks and Git alternates. Minimum free space conservatively covers a real copy plus 8 GiB. No patch or compilation. |
| `prepare` | Freeze reviewed inputs and host receipts privately; add target/config/series without changing inherited inputs; append patch 71; `git apply --check` then apply the exact frozen patch; require only `ready.c` differs in source; run the frozen host checker against the full patched tree; record project/source inventories. No kernel compilation. |
| `build` | Revalidate review, project/source and copied-cache bytes; change only resolved `CONFIG_LOCALVERSION`; run native olddefconfig/syncconfig; reject other config changes and embedded firmware/initramfs; record compiler binaries/version/target and controlled environment; force `ready.o`/its command file to rebuild; build bzImage and modules; record **all** cache content changes; stage the complete 41-module/55-file tree and check original module paths, release/vermagic and x86-64 ELF headers. |
| `package` | Revalidate inputs, source, kernel and whole module tree; create new module/input/corresponding-source archives and read back every member; record hashes and artifact identities. The full source includes notices and resolved config. No publication or hardware qualification. |

Every mutating phase checks that the baseline's bytes, modes, inode/link
identities, modification times and change times remain unchanged. Access times
are excluded because read-only hashing can update them. Existing baseline
symlink target strings are preserved using the frozen helper's `os.readlink()`
implementation, including legitimate embedded `./` components.

The wrapper reuses only hash-pinned EXP005 helper utilities: manifests,
snapshots, path/independence checks, ELF/config parsing and archive readback. It
does not call, modify, or relax those helpers' EXP005-specific phase drivers.
The final project contains every inherited input unchanged and the new private
inputs. The public source tree and recorded generated cache remain separate
inventories. Kernel `.config` semantic equality permits only LOCALVERSION;
raw candidate config equality changes only that line.

## Synthetic tooling checks and limits

Run on each development host with a fresh report filename:

```sh
python3 -B linux-psx/experiments/ps4-uvd-status-observation/native_test.py \
  --report lab/uvd-2026-10-19/status-observation/build-preparation/tooling/HOST-ATTEMPT.json
```

The tests use temporary synthetic files, receipt pins, source/config inventories,
ELF headers and phase actions. They exercise positive and negative input
contracts, phase order, failure preservation and baseline mutation detection.
They do not copy the real cache, run a kernel compiler, access a device, or
prove the whole native orchestration works. The phase tests mock the host gate
and baseline inventory only for their temporary directory; there is no CLI
test mode or production option that disables these checks.

`all-cache-content-changes.json` deliberately records all generated changes.
LOCALVERSION, build paths and command metadata can rebuild many units; a rule
that only `ready.o` differs would be incorrect. The enforced invariant is only
`ready.c` changed in kernel source, with the separate LOCALVERSION change.

After compilation, a fresh compiled audit must inspect the rebuilt object and
linked kernel, parameter metadata, register-write call paths, unchanged helper
behavior, frame-size and format diagnostics. The baseline uses
`CONFIG_FRAME_WARN=2048`; the build preserves its compiler flags and logs.
Inherited unrelated warnings must be distinguished from new-unit diagnostics.
The wrapper labels that audit pending even when build/package phases pass.

A host PASS, compiled audit and package are not a working decoder or hardware
acceptance. A future launch still needs its own current-date experiment ID,
predeclared objective/outcomes/timeout/rollback, completed UART continuity and
operator sequence. This wrapper neither starts that experiment nor stages its
boot files.
