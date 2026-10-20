# Private experimental UVD corresponding-source packaging

`native_package.py` consumes a completed `native_build.py` build and writes
only to one new private output directory. It performs local data operations
and invokes the manifest-pinned `/usr/bin/zstd` for compression and readback.
It does not compile, use SSH, load modules, stage boot files, operate the
console or publish artifacts. A successful package keeps
`hardware_acceptance: false` and `compiled_review: false`.

The supplied hash-reviewed build tool determines the release, manifest status,
resolved configuration restrictions and immutable base contract. Both CREATE
and frame build tools use this workflow. New source metadata is labeled
`PRIVATE_EXPERIMENTAL_UVD_CORRESPONDING_SOURCE`; prior frozen tools, receipts
and archives retain their original bytes and identities.

Run on the same native Linux x86-64 host/account as the build, with all frozen
manifest inputs and tools still available. Python 3.10+ is required. The
native build tool is loaded only after its hash matches the reviewed
manifest; bytecode cache writes are disabled. The existing native tool's
immutable-base, manifest, toolchain and protected-artifact checks are reused.

## Required inputs and invocation

Supply the exact reviewed manifest, its SHA-256, the reviewed build tool,
the final `PASS_BUILD` receipt SHA-256 and a new absolute output directory
whose canonical parent already exists. Output must be outside the build
tree and all pinned inputs. Existing output, including a failed attempt,
is never reused or replaced.

```text
/usr/bin/python3 -B -I native_package.py \
  --manifest ABSOLUTE_REVIEWED_MANIFEST_PATH \
  --manifest-sha256 REVIEWED_MANIFEST_SHA256 \
  --build-tool ABSOLUTE_REVIEWED_NATIVE_BUILD_PY_PATH \
  --build-sha256 EXACT_PASS_BUILD_JSON_SHA256 \
  --output NEW_ABSOLUTE_PRIVATE_PACKAGE_DIRECTORY \
  --receipt native-tool-tests.json=ABSOLUTE_RECEIPT_PATH=RECEIPT_SHA256 \
  --receipt packager-review.md=ABSOLUTE_REVIEW_PATH=REVIEW_SHA256 \
  --receipt packager-tests.json=ABSOLUTE_TEST_PATH=TEST_SHA256
```

These uppercase values describe arguments; they are not production values.
Repeat `--receipt NAME=ABSOLUTE_PATH=SHA256` for additional reviewed tool,
test or provenance receipts. Names must be unique and contain only ASCII
letters, digits, periods, underscores or hyphens. These pins establish
identity; the primary reviewer must establish the verdict and relevance of
each receipt. The source receipt, primary source review and both host source
test receipts are copied automatically from the reviewed manifest.

Full build stdout/stderr logs are omitted by default. Optional
`--include-build-logs` includes only the native work directory's `logs/`
tree, requires regular files and enforces a 64 MiB total bound. No UART
data, device firmware, home-directory scan or unrelated workspace material
is included. Existing base metadata and reviewed build receipts may contain
private host paths; all output remains private pending a separate review.

## Bindings and archive checks

The tool requires the exact successful preparation/build receipt chain,
`BUILD-STARTED.json`, all three bound final inventories and the complete
matching 41-module contract. It checks the prepared manifest/tool copies,
three exact patch copies and ordered `series`, original base metadata,
resolved configuration and all kernel artifact hashes. ELF architecture and
recorded release/vermagic are checked for each module using the native
build contract; no module loader or `modinfo` process is invoked.

The source set must exactly equal the complete prepared source plus the
manifest's reviewed candidate changes, with only the build's resolved
`.config` substituted. Each input is checked on disk. Generated objects and
`vmlinux` are excluded from the source archive by enumerating only the
`BUILT-SOURCE.json` inventory. All source notices, original file modes and
source symlink targets remain intact. No fixed source count is used by the
generic archive helpers; the native build's existing immutable base contract
retains its explicit count.

Each tar is written from an exact path-to-file map with normalized owner and
time fields. Readback rejects missing, duplicate or extra members; unsafe
paths; changed file type, mode, size or bytes; hardlinks; and changed or
escaping symlink targets. The entire decompressed stream, including padding
and any trailer, must match the writer's byte count and SHA-256. Module and
build-input archives permit only regular files. There are no `build` or
`source` links in the module package.

Before and after packaging, the tool rechecks frozen external inputs,
toolchain paths and symlink identities, old protected artifacts, every
source input, the exact installed module tree, prepared input copies,
patches, lineage and any included logs. The package directory is created
with mode `0700`; input modes inside it are preserved. A conservative
free-space floor is checked before creating output. Failure preserves all
created output and writes `FAILED_PRESERVED` when the new output directory
was created. Retrying requires a fresh directory.

## Outputs

The new directory contains `bzImage`, `vmlinux`, `System.map`, resolved
`config`, `version.txt`, complete source and module `.tar.zst` archives,
`RUNTIME-IDENTITY.json`, `PACKAGE.json` and `SHA256SUMS`. Checksums cover all
regular output files, including the final package receipt, except
`SHA256SUMS` itself. `SOURCE-ARCHIVE.json`, `MODULE-ARCHIVE.json` and
`BUILD-INPUTS.json` preserve the exact archive member inventories.

The source archive uses `linux-RELEASE/` as its prefix and adds four new
metadata files to the complete source inventory:

- `PSX-SOURCE-INPUTS.json`: verbatim final `BUILT-SOURCE.json`.
- `PSX-BUILD-MANIFEST.json`: this build's identities and explicit limits.
- `PSX-REBUILD.md`: resolved configuration, toolchain and rebuild guidance.
- `PSX-BUILD-INPUTS.tar.zst`: exact patches/series, reviewed manifest, native
  build and package tools, reviews/tests, phase receipts and inventories,
  original base receipts/config, and all four original `base-metadata/`
  files retained as lineage. Original metadata is never relabeled as new.

The module archive places the complete tree directly under
`lib/modules/RELEASE/`, including all depmod metadata and all 41 matching
`.ko` files, as required by the existing artifact consumers. There is no
additional archive root. `MODULE-ARCHIVE.json` retains these exact installed
tree names. `RUNTIME-IDENTITY.json` binds the kernel, resolved
config, source archive, module archive and full per-module hashes/vermagic
to the exact build receipt. It is an artifact identity, not boot acceptance.

The `build-inputs/`, `metadata/` and compression `logs/` directories remain
available beside the archives for private review. `PACKAGE.json` records
archive readback identities and frozen-input preservation. A package is
complete only when its status is `PASS_PACKAGE` and `packaging_complete`
is true. Staging, compiled review and any hardware experiment are separate
authorized steps.

## Synthetic checks

```text
python3 -B -I native_package_test.py --report NEW_ABSOLUTE_REPORT_PATH
```

The report's parent must exist; reports are never overwritten. The bounded
suite checks exact member identity, missing/extra entries, wrong bytes and
modes, source symlinks, forbidden module links, whole-stream trailers,
source completeness, phase gates, pinned extra receipts, final input
mutation and failed-output preservation. Its full pipeline uses a tiny
synthetic source, 41 small synthetic ELF headers and an uncompressed mock
for the compression layer. It never runs zstd, make, a compiler, SSH or a
device action. Real native zstd/archive readback remains necessary when the
primary invokes packaging on the completed build.

Use optional `--build-tool ABSOLUTE_NATIVE_BUILD_PY_PATH` to run the same
synthetic package checks against another reviewed experimental UVD build
tool, including `../ps4-uvd-frame/native_build.py`. The default remains the
sibling CREATE tool. The report records the selected tool's SHA-256.
