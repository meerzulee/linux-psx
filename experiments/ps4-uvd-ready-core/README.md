# Offline PS4 returning readiness core

This candidate factors the existing boot-only readiness controller into a
private returning function with per-device retained operation/result state.
The legacy wrapper still admits one attempt per cold boot, runs the same
early diagnostic checks, dispatches the same selected historical diagnostic
after the readiness convention, and parks. No normal-Linux caller, export,
debugfs write, command submission, cleanup or reset interface is added.

The baseline is preparation attempt06, source receipt
`2b1a4f8b7aceec62f8c42b3b5f39430bfaf4b10b97e4a8929642178390c835fa`.
Only `amdgpu_ps4_uvd_ready.c`, its header and `amdgpu.h` change. The generator
verifies and preserves all sixteen recorded baseline files. The generated
patch is experimental and is not appended to an active series.

From the workspace root, choose new output directories:

```sh
python3 -B linux-psx/experiments/ps4-uvd-ready-core/build_candidate.py \
  --output lab/uvd-2026-10-20/ready-core/source/new-attempt

python3 -B linux-psx/experiments/ps4-uvd-ready-core/check.py \
  --candidate lab/uvd-2026-10-20/ready-core/source/new-attempt \
  --output lab/uvd-2026-10-20/ready-core/host-tests/new-run
```

The generator reads the exact frozen local baseline; it neither fetches
firmware nor changes that baseline. Its output is a candidate tree, patch and
source receipt. The checker replays the patch in a private scratch directory,
verifies unchanged helper/diagnostic bytes and compiles actual extracted
controllers into a synthetic host fixture. It preserves complete source,
commands, compiler identity, outputs and failures.

The host fixture compares the original and refactored wrappers' observable
controller traces: register accesses, helper calls, logging, sleeps, backing
copies, diagnostic arguments and final park/error. Hardware helpers and
historical diagnostic bodies are synthetic boundaries; their source identity
is checked separately. These tests do not emulate hardware or firmware.

The completed Mac run passed 72 cases in optimized and ASan/UBSan builds,
with nine exact injected-defect witnesses. The first compile failure is
preserved: Darwin's fortified `memset`/`memcpy` macros needed explicit
`#undef` at the fixture's instrumentation boundary. No candidate code,
warning flags or assertions changed after that failure.

The primary's first native GCC run then reported `-Werror=clobbered` for
three fixture locals around the simulated park's `setjmp` boundary. They now
exist only inside the initial-return branch. Mac `macos-03` again passes
72 optimized/sanitized cases and all nine exact mutants; candidate attempt01
is unchanged. The older tool snapshots, receipts and README are preserved.

The private core can return a readiness result while all resources remain
retained. Only its boot wrapper currently calls it, and that wrapper never
returns to normal clients. A returned result does not make post-touch cleanup,
device removal, reset, unload, hot replacement or rearm safe. The original
two-second readiness poll remains; firmware I/O, helper execution and lock
waits do not acquire a whole-worker duration bound through this refactor.

See `lab/uvd-2026-10-20/ready-core/DESIGN.md` and `SOURCE-REPORT-REV2.md` for the
ownership contract, preserved behavior, artifact pins and remaining work.
Native isolated compilation and integration review belong to the primary.
