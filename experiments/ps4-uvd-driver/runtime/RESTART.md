# Retained UVD ordinary kernel restart

`retained-restart.patch` is a new MIT-licensed source delta against the exact
`7.2.11-psx-ps4-baikal-uvd-runtime-dev4` UVD files. It preserves previous
builders, source leaves, patches and frozen packages. Its header records the
three input hashes; it changes only `amdgpu_ps4_uvd_runtime.c`, its header and
the one predicate in `amdgpu_pci_shutdown()`.

The existing callback admits retained UVD only for `SYSTEM_POWER_OFF` and
otherwise parks. Thus an ordinary kernel restart stops before reaching the
platform reset. The new `amdgpu_ps4_uvd_runtime_restart()` is a separate
exception using the existing read-only, default-off `amdgpu.ps4_uvd_poff=1`
gate. It admits only `SYSTEM_RESTART`, with built-in PS4/Liverpool runtime
enabled, hardware backing entered, runtime started, jobs initialized and open,
and no previously latched runtime failure. `reboot_force` and
`kexec_in_progress` both refuse admission.

Admission holds `jobs.lock` while the existing `publish_lock` and atomic
compare/exchange claim the first terminal close. This covers the interval in
which a job fault has closed its queue but has not yet invoked its runtime
callback. The established job fault path releases `jobs.lock` before that
callback; the existing runtime fault path releases `publish_lock` before
faulting jobs. WPTR publication holds only `publish_lock`. The new nesting is
therefore `jobs.lock` then `publish_lock`, without adding a reverse order.
Only the winner finishes the existing admission close, cancels delayed polling
as before, faults the job queue and emits `EXPERIMENTAL_RESTART` before the
PCI callback returns. A second attempt cannot claim the same close.

The historical power-off helper body is unchanged, including its deliberate
allowance for an already-failed runtime. The existing enabled/retain/fault/park
functions are also unchanged. Other suspend, remove, reset, failed-start and
cleanup guards stay in place. Before UVD hardware entry the original AMDGPU
shutdown behavior remains. The generic `CLOSED` message is preserved from the
existing fault path; only the subsequent `EXPERIMENTAL_RESTART retained=1
teardown=0` marker identifies the new restart allowance. Neither marker proves
the physical outcome.

This exception retains backing until a terminal platform reset. It does not
drain DMA, free entered allocations, implement GPU reset or rearm, or establish
suspend/kexec support. It is not a claim of a clean driver teardown. Earlier
forced ICC restart and software power-off observations do not qualify ordinary
kernel restart with this new source.

## Main kernel integration

Apply this delta after the exact dev4 UVD overlay in the new kernel source
composition, together with the separately reviewed platform patch:

```text
platforms/ps4/patches/7.2-ps4-baikal/0200-ps4-drivers/0004-ps4-bpcie-icc-atomic-restart.patch
```

The UVD exception permits normal device shutdown to reach the atomic ICC
restart handler; neither half alone fixes this combined path. The ordinary
native baseline does not contain the experimental UVD overlay, so this delta
must not be applied to that baseline without the complete overlay. Main target,
series, release/config identity, corresponding-source packaging and matching
module build are owned by the parent integration. No frontend helper, private
ioctl or userspace device node is part of this kernel fix.

The CPU checker's `POLICY` is the agreed `uvd_restart_experiment` dictionary.
The new package must set the active inherited power-off policy's
`reboot_suspend_remove_reset_guards_unchanged` to `false`, since this restart
guard changes. Preserve the original dev4 policy only as explicitly inherited
metadata. The separate restart policy states `poweroff_semantics_unchanged:
true`; do not claim all UVD lifecycle source bytes are unchanged from dev4.

## CPU verification

Run from the repository root with an existing exact dev4 tree and a new output:

```text
env TMPDIR=/var/tmp PYTHONDONTWRITEBYTECODE=1 python3 experiments/ps4-uvd-driver/runtime/restart_check.py --kernel-source /path/to/exact/dev4 --output /var/tmp/new-uvd-restart-check
```

The checker verifies the pinned base bytes, copies only those three source
files into new output, applies the actual patch, and extracts its complete
production close/gate/fault/park functions and PCI shutdown callback into
explicit CPU/API stubs. It checks that the old power-off function and other
retention boundaries are byte-identical, the driver changes only at the one
shutdown predicate, and the opt-in remains immutable and off by default.

For each of four independent platform/build configurations it exercises
16,384 combinations covering every kernel system state and the gate, entered,
ASIC, forced/kexec, previous failure, started, initialized/closed jobs and poll
state. It checks the direct helpers and actual callback, retained sentinel
backing, unchanged ordinary teardown for unentered devices, one close owner,
and the required park/refusal branches. Two deterministic interleavings inject
a runtime fault just before compare/exchange and a job fault before its runtime
callback. These are control-flow checks, not a concurrency or DMA model.

The frozen patch SHA-256 is
`0e344be112396dbb49255908466de9eb751180589efe0c6c9e9d0b0ed959af79`.
On 2026-11-06, exact patch application and all 65,536 cases passed with
`-Wall -Wextra -Werror`, AddressSanitizer and UndefinedBehaviorSanitizer. The
local receipt is `/var/tmp/psx-uvd-retained-restart-check-20261106-02/receipt.json`.
The first check directory preserves an earlier fixture-only `longjmp` warning;
the corrected fixture marks its expected outcomes volatile. The source patch
did not change between those attempts.

A full matching kernel/modules build and a separate bounded UART experiment
remain necessary. Hardware acceptance must distinguish ordinary kernel restart
after a successful UVD run from power-off, direct ICC requests and kexec. This
directory supplies no hardware launcher or deployment action.
