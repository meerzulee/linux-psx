# Private native FRAME build workflow

`native_build.py` prepares and builds a fresh private Linux x86-64 tree.
It does not write a production manifest, install a running driver, operate a
console, stage boot media or publish anything. Packaging is an explicit next
step; neither a successful build nor its receipts constitute compiled review
or hardware acceptance.

This tool is derived byte-for-byte from the frozen CREATE `native_build.py`,
SHA-256 `771a180aa54348271a659f2df2cb59e00b974382dd0ef9282b4d5241a2e6e5a5`.
Only the release/config suffix (`uvd-create-dev1` to `uvd-frame-dev1`) and
CREATE/FRAME wording, including the reviewed manifest status, change. The
immutable base pins, first two patches, source validation, environment,
clean build, module matching and preservation/failure behavior are identical.
The original CREATE tool and already frozen artifacts remain unchanged.

The immutable base is the verified native02 corresponding-source archive:

```
source-7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1.tar.zst
ece0782b5d52d6a6a28948c0f9e45d47b424659ed669889b85ee26116276222c
```

Its inventory has 95,115 inputs, including 99 symbolic links. The source
archive has those inputs and four metadata files, totaling 95,119 members.
The extractor validates the exact archive hash, prefix, path inventory,
types, modes, sizes, contents and link targets. It writes regular files
manually and creates validated links last. Copyrights, notices and source
file modes remain intact. The four old `PSX-*` files are preserved separately
in `base-metadata/` so they cannot be mistaken for this build's metadata.
The rebuild-note hash is derived from the preserved native02 packaging
implementation's exact text; archive validation still has to confirm it.

Patches are applied in this order:

1. Frozen fault-history revision02, SHA `d2360ccb…`.
2. Frozen returning-readiness attempt01, SHA `e0443efc…`.
3. The final flattened CREATE plus frame patch, supplied and approved by
   the primary agent over that same fault/readiness composition.

CREATE is not applied as an additional fourth patch. The third patch must
include all CREATE and frame changes, and the candidate file map must bind
every changed/new file across the complete three-patch result. This build
tool does not approve the candidate or produce its reviewed manifest.

No final manifest exists until the candidate and its reviews are frozen.
The tool pins the first two hashes in full; the reviewed manifest supplies
the third. It retains all patch bytes and a `patches/series`, runs ordered
`git apply --check`/`git apply`, then verifies the complete source inventory.
Every changed or new file must match the frozen candidate; every remaining
base input must remain unchanged.

## Reviewed manifest contract

The primary supplies one JSON manifest plus its exact SHA-256 on the command
line. It is the primary's approval of the pinned source/review/test contents.
This tool checks their identities, not arbitrary review or test-result
schemas. The primary must establish those verdicts before approving the
manifest; a matching hash alone is not a passing test.

| Field | Required contents |
| --- | --- |
| `schema`, `status` | `1`, `REVIEWED_FOR_PRIVATE_FRAME_BUILD` |
| `release` | `7.2.11-psx-ps4-baikal-uvd-frame-dev1` |
| `work` | New absolute directory outside the immutable base project; its parent must exist. |
| `tool_sha256` | Exact final `native_build.py` hash. |
| `base` | Records for `archive`, `inventory`, `config`, `package`, `build`, `compiler`, `modules`, matching the complete pins in `BASE_PINS`. |
| `patches` | Three ordered path/hash records. |
| `candidate_files` | Map of complete kernel-relative paths to frozen candidate records. Scope is `drivers/gpu/drm/amd/amdgpu/`; each record includes absolute `path`, SHA-256, `kind: file`, `mode: 420` and exact `bytes`. Include every changed/new file across all three patches. Unchanged selected files may also be listed. |
| `source_receipt`, `review` | Absolute path/hash records for the final source receipt and primary review. |
| `host_receipts` | `macos` and `linux` absolute path/hash records for the final actual-source checks. |
| `tools` | Absolute executable path → SHA-256 map. Required names are listed in `TOOLS`; include the compiler backend and additional tool dependencies when pinning the native toolchain. GCC must match the saved compiler receipt. |
| `protected_files` | Absolute path → hash map, including the original native02 `bzImage` and `vmlinux` hashes. Original config, archive and base receipts are also pinned through `base`. |
| `minimum_free_bytes` | Primary-reviewed free-space floor of at least 12 GiB. This is a preflight allowance, not a proven peak-space bound. |

All path/hash records must name existing exact files. Conflicting pins,
unbound hashes, absent host receipts or wrong phase/digest are rejected.
No input may lie within the new output directory.

The base receipts are already preserved locally under
`lab/uvd-2026-10-20/live-prepare/build-preparation/native-02/receipts/`.
The complete package receipt hash is
`7a3fbd35e488c378703bafdee72080b8d679039f1aa403c825b570aa2e3297db`.
The source inventory hash is
`8505023a622ca744f104d07cb82c2dcd2f4d6af551cdf7bd5a77edae6fc4385a`.

## Native phases

After final review, invoke the tool with the real manifest path and digest:

```text
/usr/bin/python3 -B -I native_build.py prepare --manifest MANIFEST_PATH --manifest-sha256 MANIFEST_SHA256
/usr/bin/python3 -B -I native_build.py build --manifest MANIFEST_PATH --manifest-sha256 MANIFEST_SHA256 --prepared-sha256 PREPARE_RECEIPT_SHA256
```

These are argument descriptions, not commands with production values.
The first phase exclusively creates `work`, with `source/`, `patches/`,
`base-metadata/`, `inputs/`, `tmp/` and logs. It writes `PREPARED-SOURCE.json`
and `PREPARE.json`. The second phase requires the exact successful prepare
receipt and creates a single `BUILD-STARTED.json` claim. A failed or previously
attempted build is preserved; the tool does not retry in that tree.

`source/` is a fresh in-tree build directory extracted from source-only inputs,
with no objects copied from the old build. A full `make clean` is still run
because embedded device layouts changed. All make commands retain:

```
ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc
```

Only `CONFIG_LOCALVERSION` changes, to `"-psx-ps4-baikal-uvd-frame-dev1"`.
The tool runs `olddefconfig`, `syncconfig`, verifies the resolved config and
kernel release, then runs `-j6 bzImage modules`. It rechecks the config,
release and every source input after compilation. Compiler/version queries,
the exact environment, every command and full stdout/stderr logs are saved.
The saved native02 environment is reused; `TMPDIR` points into the fresh work
directory, and the account's existing home must match the recorded host.

`modules_install` uses only the private `output/modules/` destination and
`INSTALL_MOD_STRIP=1`. Only build/source symlinks pointing inside this new
workspace are removed. All 41 module paths must match the baseline set with
the new release substituted; every module must be ELF64 little-endian x86-64
and carry the new release in vermagic. The complete installed tree is recorded.
All immutable inputs, tools and protected old artifacts are rechecked after
each phase, including failures.

Successful build outputs are `output/bzImage`, `System.map`, `config`,
`version.txt`, and the complete private module tree. `vmlinux` remains in the
fresh source/build tree. `BUILD.json` binds their hashes, final source inventory
and module receipts. It explicitly leaves packaging and compiled review open.

## Packaging follow-up

Before staging or sharing a build, package the complete changed source,
resolved config, original notices, all three exact patches, reviewed manifest,
build tool and receipts, plus the complete matching module tree. Verify archive
readback against those final inventories. Preserve the old metadata as lineage;
generate new rebuild instructions and artifact identities. This tool does not
claim a source release is already complete. The shared
`../ps4-uvd-driver/native_package.py` accepts this exact frame build tool via
`--build-tool`, derives release/config checks from it and labels the resulting
metadata as private experimental UVD. Follow that tool's `PACKAGING.md` and
retain the separate frame review and test receipts.

## Local verification

`native_test.py --report NEW_REPORT_PATH` uses synthetic temporary files.
It checks archive paths/content/modes/link handling, exact source preservation,
review/phase gates, config restrictions, module paths/ELF/vermagic and failed
phase preservation. One test applies three tiny synthetic patches using local
Git. It never runs make, zstd, a compiler, SSH or a real archive/device operation.
The suite reuses the CREATE `native_test.py` source, SHA-256
`16b3b5367b23b49f1e644af4d93a9b9d73f611bf855a7545c8ea34d0b7158554`,
with the frame config/status expectations and derivation hashes in its report.
These synthetic results establish no native frame build or decoded output.
