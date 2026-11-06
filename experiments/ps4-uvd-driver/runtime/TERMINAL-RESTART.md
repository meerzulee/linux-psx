# Terminal restart after a UVD test is canceled

`terminal-restart.patch` is a separate MIT-licensed delta against the exact
`7.2.11-psx-ps4-baikal-uvd-runtime-dev5` runtime. Apply it after the frozen
[`retained-restart.patch`](retained-restart.patch); neither that patch nor the
previous kernel artifacts are replaced. Only `amdgpu_ps4_uvd_runtime.c` changes.

An operator's terminal restart may follow termination of an active FFmpeg or
browser decoder. File close can already have faulted or closed the UVD queue.
The dev5 restart exception requires a healthy queue and ownership of its first
close, so it deliberately parks in that situation. This delta permits the
terminal handoff after a post-start failure, while preserving the failure.

The exception still requires all of the following:

- The immutable, default-off `amdgpu.ps4_uvd_poff=1` opt-in.
- Ordinary `SYSTEM_RESTART`, with neither `reboot_force` nor
  `kexec_in_progress` set.
- Built-in PS4/Liverpool runtime, entered backing, `started` and initialized
  jobs.
- The exact `ps4_uvd_ip_block` with its existing `status.hw` flag set. AMDGPU
  sets this only after `hw_init` returns successfully. The runtime's `started`
  flag alone is insufficient because it precedes the boot ring test.

The callback invokes the existing idempotent runtime fault path and returns
before ordinary AMDGPU teardown. A previously latched runtime error is not
overwritten, and the job queue keeps its original error. If a job error was
recorded before its runtime callback arrived, the queue retains that cause
even when the runtime first records the terminal `-ESHUTDOWN` close. Entered
leases are quarantined; publication sees the latched failure and cannot write
WPTR. The exception neither waits for a first-close winner nor frees, unmaps,
resets or rearms DMA-reachable state.

The existing POWER_OFF helper, fault and park helpers, startup sequence,
header/layout, job ownership implementation and PCI shutdown callback remain
byte-identical to dev5. Failed startup, nonterminal lifecycle operations and
in-place recovery remain outside this restart exception. The final platform
reset still requires the separately registered atomic Baikal ICC handler.
`EXPERIMENTAL_RESTART retained=1 teardown=0 failed_allowed=1` records admission,
not physical completion. The earlier healthy restart result does not qualify
restart after preemption or failure.

## Host verification

From the repository root, use a preserved dev5 source tree and a new output:

```text
env TMPDIR=/var/tmp PYTHONDONTWRITEBYTECODE=1 python3 experiments/ps4-uvd-driver/runtime/terminal_restart_check.py --kernel-source /path/to/exact/dev5 --output /var/tmp/new-terminal-restart-check
```

The checker verifies the dev5 source hashes and the unchanged predecessor
patch/fixture, copies inputs into new private output, and applies this patch
without fuzz or offsets. It compiles the actual runtime close/fault/restart,
PCI shutdown, job fault/quarantine, IP lookup and WPTR functions with explicit
CPU/API fixtures. Four built-in/platform configurations cover 524,288 state
combinations under ASan/UBSan. Additional deterministic cases place a runtime
fault before publication closure, place a job failure before its callback,
and model a failed boot ring test. The checks verify idempotence, preserved
errors and backing, quarantined entered leases, blocked WPTR writes, the
unchanged power-off path, and rejection of unsupported states.

These are control-flow and ownership-policy checks. They do not model DMA,
real scheduling races, the lease reaper, ICC hardware or physical restart.
A complete matching kernel/module/source build and a separate observed
terminal restart are still required. The checker's `POLICY` is the active
`uvd_restart_experiment` schema; retain the complete dev5 policy separately
as `inherited_dev5_uvd_restart_experiment` when packaging the new candidate.
