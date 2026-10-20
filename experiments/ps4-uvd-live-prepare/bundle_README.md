# Preparation-only offline bundle

`bundle.py` assembles a private host directory from the reviewed native-02
build and the retained IB-DATA revision-2 stage. It has no install, rollback,
device, UART, console or experiment-allocation interface. Its completed output
is always `OFFLINE_BUNDLE_PENDING_OPERATOR_PLAN`, with
`hardware_acceptance=false` and `executable_install=false`.

The tool is specific to release
`7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1` and attempt06 patch
`9cc51da26d81342d2fd3cc674a4495cbdc4298350059cdeb0eeba9d744693456`.
It does not rebuild or execute the kernel, modules, source or initramfs.

## Inputs and verification

Provide the entire native run, including `receipts/`, `project/output/` and
`project/src/`. An output-only copy is insufficient: the tool rehashes the
16 reviewed source files, nine reviewed objects and their compile-command
records, and `vmlinux` against the separately pinned compiled review.

Before creating the destination, the tool verifies:

- The caller's SHA-256 pin for the completed compiled review; exact review,
  identity and prepare-base/prepare/build/package receipt chain; source and
  build scope, patch stack, compiler and inventory bindings.
- All nine packaged output hashes, image header, release and reviewed config.
  Original receipts retain their historical `compiled_audit: pending` fields;
  the completed review is copied separately and bound by its digest.
- Every file in the 55-file module archive and all 41 modules: exact path set,
  SHA-256, size, mode, owner, ELF64 x86-64 relocatable identity and vermagic.
  The only supported PAX extension is a canonical `path` equal to the member
  name. Links, duplicates, escapes and other extensions are rejected.
- The opaque retained initramfs `cf2102f5…`, its preparation receipt
  `28150c8b…`, and their IB-stage plan bindings. It is never unpacked or run.
- Exact saved IB arguments `4d08ab79…`, also bound to the retained stage plan.
  Input files and path ancestors must not be symlinks.

Python 3.10+ and the `zstd` executable are required. The one subprocess in the
builder is `zstd -d -c` with a memory cap, solely to decode the module tar for
in-memory validation. It extracts no files to the filesystem and executes no
archive member. Source and input archives are stream-hashed without unpacking.

## Two declared boot-configuration changes

The exact retained IB arguments have the four gates
`amdgpu.ps4_uvd_ready=1`, `amdgpu.ps4_uvd_poll_state=1`,
`amdgpu.ps4_uvd_token=1` and `amdgpu.ps4_uvd_ib_data=1`.
The builder removes these and appends, in order:

```text
amdgpu.ps4_uvd_live_prepare=1 debugfs=on
```

The first option selects preparation-only mode. The second enables the cached
observation interface: the reviewed config has `CONFIG_DEBUG_FS=y`,
`CONFIG_DEBUG_FS_ALLOW_NONE=y` and `CONFIG_DEBUG_FS_ALLOW_ALL` unset. The pinned
Linux `fs/debugfs/inode.c` early parameter enables registration/API for
`debugfs=on`; mounting alone cannot change that boot policy. The config anchor
is `compiled-audit/native02-types-02/config.txt:4642–4644`; the primary source
evidence is recorded in `handoff/BOOTARGS-PROPOSAL-REV2.json`.

All other token bytes and their order are preserved. The saved input has one
space between tokens and one final newline; ambiguous quoting/whitespace,
duplicate or unknown experimental gates, disabled gates, an existing debugfs
option, and `--` are rejected. Existing options are never silently replaced.

The actual input transforms to 535 bytes with SHA-256
`3f6381594c3f3b080728aa894a8c2aabd28651dcfb5541aee83aff27bcdcded1`,
matching the primary's independent `bootargs.with-debugfs.proposed`. The
earlier gate-only proposal `4deee3bd…` is preserved evidence, superseded for
this bundle because it cannot enable cached debugfs observation.

## Host-only invocation

Run from the native workspace after reviewing the tool. The destination must
be absent, its parent must already exist, and it must be outside the two
preserved input trees. This command only creates an offline host directory:

```sh
/usr/bin/python3 linux-psx/experiments/ps4-uvd-live-prepare/bundle.py \
  --native-run /home/developer/Work/psx/lab/uvd-2026-10-20/live-prepare/build-preparation/native-02 \
  --compiled-review /home/developer/Work/psx/lab/uvd-2026-10-20/live-prepare/compiled-audit/compiled-review.json \
  --compiled-review-sha256 13e4837544a6ea7faade1bf932d7da2f739c1909df1fb3cdb3f88adf649c09b4 \
  --retained-ib-stage /home/developer/Work/psx/lab/uvd-2026-10-20/ib-data/handoff/revision-2/stage \
  --destination /home/developer/Work/psx/lab/uvd-2026-10-20/live-prepare/offline-bundle-native02-01
```

Output contains `bzImage`, `config`, `modules.tar.zst`, `initramfs.cpio.gz`,
`bootargs.new`, unchanged provenance receipts, `MANIFEST.json` and
`STATUS.json`. Validation failure creates no destination. A copy or I/O failure
retains the partial directory as `OFFLINE_BUNDLE_INCOMPLETE`, with a failure
receipt when storage permits. The tool never deletes or overwrites inputs or
an existing destination; it will refuse a retry to that same partial path.

The source and build-input archives remain external absolute-path references
with SHA-256 and byte count. They are not duplicated into this small bundle.
Keep those exact bytes available; moving to another host requires copying
and separately rebinding those references. This private bundle is not a
self-contained corresponding-source distribution or a public release.

## Synthetic verification and limits

```sh
python3 linux-psx/experiments/ps4-uvd-live-prepare/bundle_test.py \
  --report /absolute/new/path/synthetic-report.json
```

The test report must be new. Tests use temporary synthetic files with the real
receipt shapes and all 55 archive entries. Their in-memory opaque-input pin
substitution exists only in the test harness; the CLI has no test override.
They test valid output, preserved history, hash/chain/config/source/object
tampering, archive paths and contents, ELF/vermagic, symlinks, argument
conflicts, existing output and incomplete-copy handling. They do not establish
compatibility of the real boot, inspect initramfs contents, or qualify hardware.

This worker ran Mac synthetic tests only and compared the saved arguments in
memory. No native bundle, device action or experiment was performed. Actual
native artifact validation, the separate boot-compatibility disposition and
the operator plan remain the primary agent's next steps.
