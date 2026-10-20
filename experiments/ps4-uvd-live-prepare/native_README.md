# Isolated native preparation build tooling

`native.py` prepares, builds and packages a private
`7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1` candidate. It never installs a
kernel or modules on the host/console, accesses USB/UART, changes privileges,
fetches firmware or publishes anything.

This tool adapts the frozen IB-DATA `native.py` phase/manifest conventions and
reuses three exact-hash DATA0 build utilities. It does not change those tools.
Native builds require Linux x86-64. Commands use `/usr/bin` tools and
`PATH=/usr/bin:/bin`; jobs are restricted to 1–6.

## Inputs and gates

- Full frozen IB-DATA native project/tree at
  `lab/uvd-2026-10-20/ib-data/build-preparation/native-01/project`.
- Nine pinned baseline receipts, all 772 project input records, all 95,114
  corresponding-source records, exact upstream/project commits, the ordered
  72-patch prefix, retained baseline binaries and all 55 module-tree files.
- The candidate source receipt, 16 selected source identities, an exact
  eleven-file patch (ten modifications and one new header), source generator
  and its four pinned inputs.
- The primary's `PRIMARY-SOURCE-REVIEW.json`, pinned on the command line,
  with `PASS_SCOPED_SOURCE_REVIEW_FOR_NATIVE_BUILD` and `must_fix: []`.
- Both pinned Mac/Linux fixture receipts with matching checker/template,
  selected source, generated fixture, at least 77 optimized/sanitized cases,
  and the 13 exact injected-defect witnesses.
- `--tool-sha256` binds the reviewed bytes of this native tool. All input pins,
  including its focused tests and this contract, are frozen at the first phase
  and must match in every later phase.

The actual root review is consumed directly; there is no second approval
verdict. No approval or receipt substitutes for the identity checks below.
The full native tree is the supported source input. A source-archive-only
reconstruction is not implemented by this tool.

## Phases

From the native workspace, inspect the plan or validate the source/host review:

```sh
/usr/bin/python3 -B -I linux-psx/experiments/ps4-uvd-live-prepare/native.py plan \
  --workspace /home/developer/Work/psx

/usr/bin/python3 -B -I linux-psx/experiments/ps4-uvd-live-prepare/native.py verify-review \
  --workspace /home/developer/Work/psx \
  --review lab/uvd-2026-10-20/live-prepare/review/PRIMARY-SOURCE-REVIEW.json \
  --review-sha256 '<review digest from the primary>'
```

These commands do not create a run. A plan with a review also validates the
full baseline, which can require reading many gigabytes. `verify-review`
validates only the small source/host-review inputs and runs on either host.

The four mutating phases are `prepare-base`, `prepare`, `build`, `package`.
Each requires the same workspace-relative fresh `--run`, review path/digest,
and `--tool-sha256`. `build` accepts `--jobs 1` through `--jobs 6`.

1. **prepare-base:** Verify the baseline, check conservative free-space headroom,
   clone project Git objects without hardlinks, copy its input overlay, and
   copy the full source/cache into a separate target. Reflinks may be used;
   shared inodes, Git alternates and escaping symlinks are rejected. No frozen
   baseline file is modified.
2. **prepare:** Freeze tool/source/review inputs, append only this patch to a
   separate 73-patch series/target, and change only CONFIG_LOCALVERSION in a
   separate config. Apply/check the patch in the private source, compare all
   selected files and the entire source inventory, and rerun the unchanged
   actual-source fixture against this copied tree before building.
3. **build:** Recheck phase identities. Run `make clean` only in the independent
   private source so the `amdgpu.h` layout change cannot leave stale dependent
   objects. Recheck sources, change resolved CONFIG_LOCALVERSION only, then run
   `olddefconfig`, `syncconfig`, `bzImage` and `modules` with
   `ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc`. Record compiler,
   environment and commands. Install modules only into the private output
   staging directory, verify the complete expected 41 modules/55 files and
   vermagic/ELF headers, and record kernel/config/vmlinux/source identities.
4. **package:** Verify built artifacts and the full module/source/input
   inventories. Create private modules, inputs and complete corresponding
   source archives using the pinned existing archive utility. Read every
   archive member back and verify names, types, modes, sizes and bytes. Record
   artifact hashes and rebuild instructions; no publication occurs.

Existing runs or phase attempts are never silently reused or overwritten.
Failures/interruption retain their outputs and logs. A new attempt requires a
new run path. Every phase checks frozen baseline content and file identities
before/after; access times are deliberately excluded because reading updates
them. A failed baseline-preservation check is terminal for that attempt.

The build uses the source tree, not the candidate generator, as its compiler
input. Frozen metadata beside that tree permits the unchanged fixture's
candidate-path convention; it is outside the kernel source inventory and is
also preserved in the inputs archive.

## Validation scope

`native_test.py` exercises orchestration contracts using small synthetic
filesystems/receipts and mocked actions. It never invokes a kernel build,
copies a full tree, creates release archives or touches devices. Its reports
belong in `lab/uvd-2026-10-20/live-prepare/build-tool/`.

Successful tooling tests do not establish native build success, compiled
control-flow correctness, teardown support or hardware acceptance. A later
compiled audit remains required. The active installed IB-DATA experiment is
unchanged by preparing this tool.
