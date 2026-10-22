# Private runtime candidate packaging

`package.py` packages final runtime candidate06 on the native Linux x86-64
build host, using Python 3.11+. It imports `../native_package.py` only after
matching its reviewed SHA256
`cd4761adae17d936240429555cec73965cf5416306a876fb65ef8eedfa03b0bf`.
That helper supplies the existing frame build validation, lineage/input staging,
exact enumerated archives and complete compressed archive readback. No archive
implementation is duplicated here.

The wrapper requires exact successful frame manifest/build pins, final runtime
`SOURCE-RECEIPT.json` and `LINK-ATTEMPT-06.json` pins, and the final kernel/config
pins. Its `--runtime-inputs` directory must contain exactly `build_candidate.py`
and the source receipt's declared input files, with every hash matching. Supply
frozen source copies, not a changing development directory.
The frame build tool must be an exact external copy outside the frame work
directory, as required by the existing native workflow's input isolation guard.

```text
python3 -B -I package.py \
  --archive-helper ABSOLUTE_REVIEWED_NATIVE_PACKAGE_PY \
  --frame-manifest ABSOLUTE_FRAME_REVIEWED_MANIFEST_JSON \
  --frame-manifest-sha256 EXACT_FRAME_MANIFEST_SHA256 \
  --frame-build-tool ABSOLUTE_FROZEN_FRAME_NATIVE_BUILD_PY \
  --frame-build-sha256 EXACT_FRAME_BUILD_JSON_SHA256 \
  --runtime-work ABSOLUTE_NATIVE_RUNTIME_BUILD_01 \
  --runtime-inputs ABSOLUTE_FROZEN_RUNTIME_INPUTS_DIRECTORY \
  --source-receipt-sha256 EXACT_SOURCE_RECEIPT_SHA256 \
  --link-receipt-sha256 EXACT_LINK_ATTEMPT_06_SHA256 \
  --kernel-sha256 EXACT_FINAL_BZIMAGE_SHA256 \
  --config-sha256 EXACT_FINAL_CONFIG_SHA256 \
  --output NEW_ABSOLUTE_PRIVATE_OUTPUT_DIRECTORY \
  --receipt source-review.json=ABSOLUTE_REVIEW_PATH=SHA256 \
  --receipt runtime-tests.json=ABSOLUTE_TEST_REPORT_PATH=SHA256
```

Output needs an existing canonical parent and must be outside both build trees
and all inputs. Existing or failed output is never reused. Repeat `--receipt`
for explicit pinned test/review metadata; no UART, firmware, credentials or
unrelated workspace scan is part of packaging. The frame helper also preserves
the original protected artifacts, manifest inputs, toolchain and base lineage.

The complete corresponding-source set must equal the frame `BUILT-SOURCE.json`
inventory plus the receipt's candidate files, with the sole byte substitution
from `uvd-frame-dev1` to `uvd-runtime-dev1` in `CONFIG_LOCALVERSION`. Every source
record and notice is checked. No embedded firmware or initramfs is allowed.
Unknown files are rejected: generated paths may only come from the frozen frame
built tree, `.version`, or declared candidate C object/command files. Generated
links are allowed only when their full record equals the frozen frame record
(including its `arch/x86_64/boot/bzImage` alias). All generated entries are
excluded from the source archive. The exact runtime patch is also
applied to a new private scratch tree and must reproduce every candidate byte
and mode.

The only Kbuild target run is `modules_install`, with `INSTALL_MOD_PATH` inside
the new output and `INSTALL_MOD_STRIP=1`; explicit `depmod -b` follows. All 41
module names must match the frame set after release substitution. Each `.ko`
must be ELF64 little-endian x86-64 relocatable with matching modinfo vermagic.
Only the fresh stage's standard `build`/`source` links are removed, after checking
their exact target. All remaining stage entries must be regular files under
`lib/modules/7.2.11-psx-ps4-baikal-uvd-runtime-dev1/`.

Outputs include the kernel, vmlinux, System.map, resolved config, version,
complete matching module tree/archive, complete source archive, source/module
inventories, runtime identity, package receipt and `SHA256SUMS`. The source
archive includes exact new metadata and a build-input archive containing the
frame's original patches/series/receipts/base-metadata, final runtime delta,
generator and declared inputs, packaging tools, and supplied receipts. Old
metadata remains labeled as frame/original lineage. The complete source is
already patched; rebuild guidance describes a clean reconstruction without
promising byte-identical output from the original incremental link.

All archives are read back through the reviewed helper, checking exact member
set, type, mode, symlink target/content, bytes and whole tar stream. Modules use
the direct `lib/modules/RELEASE/` archive layout with no links. Complete frame
and runtime trees, kernel artifacts, declared inputs and installed module stage
are checked for preservation. Failures retain their new output and a
`FAILED_PRESERVED` receipt where output was created; retry only in a new directory.

`PASS_PACKAGE` requires complete archive readback and input preservation.
`hardware_acceptance` and `compiled_review` remain false. Packaging performs no
SSH, installation, USB/media changes, module load, console operation or
publication. Actual packaging must wait for the parent to declare the final
link ready and authorize the private packaging run.

## Private PR3 underscan derivative

`build_underscan.py` builds a distinct native x86-64 derivative from the exact
preserved runtime-dev1 source archive, config and kernel identities. It applies
the unchanged suds-mcduff PR3 patch (SHA256
`e6cb81ca268b02dcbaccec2f13a63e82015f838942d8dd30ffafe5fa0168930c`)
to `amdgpu_connectors.c` and changes only `CONFIG_LOCALVERSION` to produce
`7.2.11-psx-ps4-baikal-uvd-runtime-underscan-dev1`. It does not substitute the
ordinary native-dev2 target, which lacks this experimental UVD runtime lineage.

Supply `--base-package`, `--patch` and a new `--output` directory; `--jobs`
defaults to16. It requires at least12GiB free, checks the complete base source
archive against its inventory, preserves original source metadata as explicitly
labeled lineage, includes its own patch/tool/rebuild inputs, and reads back
every byte of the new source and module archives. The clean build uses the
resolved base config; `olddefconfig` may not change any other settings. All41
module names must match the base with the new release substituted, and every
module must report that new vermagic. Existing outputs and the base package
are never reused or overwritten.

This helper performs no console, media, driver-loading, installation or public
publication action. Hardware acceptance remains false. The PR3 contributor's
6.18/X11 TV-underscan result is not7.2/Wayland acceptance or an audio-speed fix.
Synthetic guards: `python3 -B underscan_build_check.py` from this directory.
