# Private IB DATA0 native build

`native.py` prepares an independent copy of the completed status-observation
Revision C project/cache, applies the reviewed IB DATA0 patch, and builds and
packages the kernel with all matching modules. The experiment design is
`lab/uvd-driver-goal-2026-10-20/IB-DATA-DESIGN.md` relative to the workspace.

This tooling does not approve the candidate, run a hardware experiment, or
establish working decoding. It has no USB, UART, console, privilege, or
publication operation. It never invokes a candidate generator or root
`build.sh`; that builder would reset/clean the cached source.

## Pinned baseline and destination

The native workspace is `/home/developer/Work/psx` on Omarchynator. Every
mutating phase requires Linux x86-64. All command path arguments below are
workspace-relative except `--workspace` itself.

```text
Baseline project:
lab/uvd-2026-10-19/status-observation/build-preparation/native-01/project

Baseline receipts:
lab/uvd-2026-10-19/status-observation/build-preparation/native-01/receipts

Default new run:
lab/uvd-2026-10-20/ib-data/build-preparation/native-01
  project/src/7.2-ps4-baikal-uvd-ib-data-dev1/
  project/output/7.2-ps4-baikal-uvd-ib-data-dev1/
  receipts/
```

The baseline has 752 project inputs, 71 patches and 95,113 source entries.
All were rehashed read-only on Omarchynator for this tooling task; the saved
observation is `lab/uvd-2026-10-20/ib-data/build-preparation/tooling/BASELINE-READONLY.json`.
The wrapper repeats those checks before consequential phases.

| Identity | SHA256 / commit |
|---|---|
| Project base | `35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2` |
| Upstream | `a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea` |
| Baseline ready.c | `60b55de93ef8a3b38626fbb5f063d76dd90cd52a5cd10b9250015fb384707518` |
| Baseline kernel | `439a2c593261e1afb7d62c34a7555b048e4f9c0eb91b83c78ee44046886ade92` |
| Project inventory | `78f049219988e25151d515159364c99fe26ab747f43bde53c8ffe98f31475839` |
| Built-source inventory | `3f3890068380963d6380ef5a8ae59bb7721c22a6cfa9e847206a6c9f56ba7cc0` |
| Build receipt | `f7f68db8d2e321b34088a3dac3974dd06ae97e0acc2a18b7573391c7c14e2b8e` |
| Package receipt | `a46478cd740814d90d115ecac71a652f546d6f542936e0cc41086e11b672760e` |

The old ring planner and IRQ source stay unchanged. The exact new source
contract is three modified regular files and one added regular 0644 header:

```text
drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.c
drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_map_canary.c
drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.h
drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ib_data_plan.h   (new)
```

The header must equal the separately reviewed pure packet planner byte for
byte. Other source paths, bytes, kinds and modes must remain equal to the
baseline. The only config change is LOCALVERSION, producing
`7.2.11-psx-ps4-baikal-uvd-ib-data-dev1`. Firmware/initramfs embedding stays
disabled. Patch 72 is
`0010-drm-amdgpu-ps4-uvd-ib-data-marker.patch`.

## Final primary review manifest

The primary owner supplies a final manifest after source review and actual
host checks. This tool does not infer approval from a PASS or another agent's
agreement. Every mutating command requires its separately supplied SHA256.
Changing this tool, a reviewed input, or the review requires a fresh manifest.

The schema is shown below with invalid placeholders intentionally. Replace
them with final paths, hashes, case count and the actual necessary mutation
set; the example is not approval. Each file pin has exactly `path` and
`sha256`; paths must name regular files within the workspace without symlink
components or escapes.

```json
{
  "schema": 1,
  "target": "7.2-ps4-baikal-uvd-ib-data-dev1",
  "release": "7.2.11-psx-ps4-baikal-uvd-ib-data-dev1",
  "final_primary_review": true,
  "verdict": "APPROVED_FOR_NATIVE_BUILD_ONLY",
  "unresolved_findings": [],
  "host_cases": 0,
  "required_mutations": {
    "FINAL_DECLARED_MUTATION_NAME": "EXACT_EXPECTED_FAILURE_WITNESS"
  },
  "pins": {
    "patch": {"path": "linux-psx/experiments/ps4-uvd-ib-data/0010-drm-amdgpu-ps4-uvd-ib-data-marker.patch", "sha256": "SHA256"},
    "checker": {"path": "linux-psx/experiments/ps4-uvd-ib-data/check.py", "sha256": "SHA256"},
    "fixture": {"path": "linux-psx/experiments/ps4-uvd-ib-data/fixture.c", "sha256": "SHA256"},
    "planner": {"path": "linux-psx/experiments/ps4-uvd-ib-data/ps4_uvd_ib_data_plan.h", "sha256": "SHA256"},
    "flow": {"path": "linux-psx/experiments/ps4-uvd-ib-data/ib_data_flow.inc", "sha256": "SHA256"},
    "generator": {"path": "linux-psx/experiments/ps4-uvd-ib-data/build_candidate.py", "sha256": "SHA256"},
    "design": {"path": "lab/uvd-driver-goal-2026-10-20/IB-DATA-DESIGN.md", "sha256": "SHA256"},
    "native_tool": {"path": "linux-psx/experiments/ps4-uvd-ib-data/native.py", "sha256": "SHA256"},
    "independent_review": {"path": "FINAL_REVIEW_DOCUMENT", "sha256": "SHA256"}
  },
  "source_pins": {
    "drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.c": {"path": "lab/uvd-2026-10-20/ib-data/source/candidate/drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.c", "sha256": "SHA256"},
    "drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_map_canary.c": {"path": "lab/uvd-2026-10-20/ib-data/source/candidate/drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_map_canary.c", "sha256": "SHA256"},
    "drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.h": {"path": "lab/uvd-2026-10-20/ib-data/source/candidate/drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.h", "sha256": "SHA256"},
    "drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ib_data_plan.h": {"path": "lab/uvd-2026-10-20/ib-data/source/candidate/drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ib_data_plan.h", "sha256": "SHA256"}
  },
  "host_receipts": [
    {"path": "FINAL_MAC_RECEIPT.json", "sha256": "SHA256"},
    {"path": "FINAL_LINUX_RECEIPT.json", "sha256": "SHA256"}
  ]
}
```

Host receipts must be `PASS_EXTRACTED_SOURCE_HOST_ONLY`, one Darwin and one
Linux x86-64, with matching patch/checker/fixture/planner/flow/generator and five output
source identities (the four changed files plus unchanged old ring planner).
They must identify the four frozen baseline helpers, compiler argv/path/
version/target and host, the same generated fixture source, and passing
optimized and ASan/UBSan runs with the declared case count.

The receipt's `sensitivity` list must match the manifest's exact
`required_mutations` set. Every entry must have `detected: true`, a nonzero
return code, and both `expected_witness` and `observed_witness` equal to the
declared witness. An unrelated crash does not count. There is no inherited
minimum such as 12 mutations; the reviewed necessary set is the contract.
Mac/Linux receipt bytes may differ because compiler and host identities do.

The frozen checker compares `ib_data_flow.inc` with the actual extracted
candidate block. It hashes `build_candidate.py` as provenance only; neither
the native wrapper nor the checker executes or imports that generator. Both
files are pinned and copied so the prebuild check has its complete read-only
input set. The native source is always produced by applying the frozen patch.

The review document is preserved byte-for-byte. The tool does not interpret
its prose; the primary manifest explicitly resolves its findings for the
source it pins. No hardware approval follows from this manifest.

## Commands and phases

Static read-only planning works without review and creates no files:

```sh
python3 -B linux-psx/experiments/ps4-uvd-ib-data/native.py plan \
  --workspace /home/developer/Work/psx
```

With `--review` and `--review-sha256`, planning also validates the pinned
inputs, host receipts and full baseline. A draft manifest may set
`final_primary_review: false` and `verdict: "PENDING"`; all mutating phases
will reject it.

After final primary review, run each phase separately with the same arguments
and inspect its receipt before continuing:

```sh
python3 -B linux-psx/experiments/ps4-uvd-ib-data/native.py prepare-base \
  --workspace /home/developer/Work/psx \
  --run lab/uvd-2026-10-20/ib-data/build-preparation/native-01 \
  --review lab/uvd-2026-10-20/ib-data/review/PRIMARY-NATIVE-REVIEW.json \
  --review-sha256 FINAL_REVIEW_SHA256
```

Replace `prepare-base` with `prepare`, then `build --jobs 6`, then `package`.
Allowed job counts are 1–6. These are future execution instructions, not
claims that any phase has run for this candidate.

| Phase | Contract |
|---|---|
| `prepare-base` | Requires an absent run directory. Snapshots the frozen project/source/cache/output tree, validates all baseline manifests, clones without hardlinks, copies precisely the recorded overlay, and copies source/cache with `cp -a --reflink=auto`. Verifies all copied bytes/kinds/modes, no shared baseline inodes, no escaping links or Git alternates. Reserves space for a real copy plus 8 GiB. No patch or kernel build. |
| `prepare` | Freezes reviewed inputs/receipts, appends patch 72, applies the exact patch with `git apply --check` followed by apply, verifies the exact four-file delta, and reruns the frozen checker against the full native tree. Records complete project/source inventories. |
| `build` | Revalidates review, source, project inputs and cache bytes; changes LOCALVERSION only; runs native olddefconfig/syncconfig; records controlled environment and toolchain; forces both changed C translation units and command files to rebuild; builds bzImage/modules; records every cache content change; stages all 41 modules and validates the 55-file module tree, expected paths, release/vermagic and x86-64 ELF identity. |
| `package` | Revalidates the whole artifact/module/source/input chain, creates private module/input/corresponding-source archives, and reads every archive member back. Preserves source notices, resolved config and compiler/build identity. |

Each phase requires a successful predecessor and an absent attempt marker.
Failed attempts retain all partial files and evidence; there is no implicit
retry, reset, clean or resume. A corrected attempt uses a new reviewed run.
The baseline is checked before and after every mutating phase, including
bytes, modes, inode/link identities, mtime and ctime. Atime is excluded because
read-only hashing can change it. Symlink target bytes use `os.readlink()`.

The original status-observation scripts remain untouched. Only hash-pinned
utilities from the frozen DATA0-control tools are imported: inventories,
snapshots, source selection, independence checks, config/ELF parsing and
archive readback. Their old experiment-specific phase drivers are never run
or modified.

## Tooling checks and remaining evidence

Run the synthetic suite with a new report filename on each host:

```sh
python3 -B linux-psx/experiments/ps4-uvd-ib-data/native_test.py \
  --report lab/uvd-2026-10-20/ib-data/build-preparation/tooling/HOST-ATTEMPT.json
```

These tests use temporary synthetic files and receipts to exercise scope,
pins, config restrictions, phase order, failure preservation, stale cache,
hardlinks/symlinks/alternates and exact mutation witnesses. They do not copy
the real source/cache or run a kernel build. There is no production test
option that disables the native host, review or baseline checks.
The Linux suite also clones a tiny temporary Git project and copies its
synthetic cache through the real preparation function; this GNU-cp test is
explicitly skipped on macOS.

All generated binary changes are recorded. LOCALVERSION, paths and header
dependencies can rebuild many units; no assertion restricts changes to just
the two deliberately rebuilt objects. A fresh compiled audit must inspect
those objects, the linked kernel, new/unchanged helper paths, parameter
metadata, register accesses and frame/format diagnostics. Build/package
receipts keep that audit pending; they do not finish older incomplete reviews.

No hardware experiment ID or boot-file stage is created here. A future launch
still requires the primary owner's predeclared one-launch plan and bounded
UART evidence. Passing tooling, source tests or compilation is not hardware
acceptance or working video decoding.
