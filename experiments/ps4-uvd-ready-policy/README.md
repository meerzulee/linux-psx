# PS4 UVD ready-convention experiment

This is a separate, default-off observation policy on the frozen dev4
diagnostic. It is the first hardware gate for the broader
[UVD backend plan](../../docs/PS4-UVD-PLAN.md). It does not submit a ring or
claim decoding support.

EXP-20261018-009 rejected `MASTINT_EN=2` before its ordinary ready check,
then observed STATUS=2 in a later snapshot. The engine remained released
with all memory retained. This candidate asks whether the existing
ready convention can be observed within the existing deadline while all
fetch, mapping, pointer and lifetime constraints remain in force.

## Exactly what changes

The patch adds read-only boot option `amdgpu.ps4_uvd_poll_state=1`, which
only has an effect when the existing `amdgpu.ps4_uvd_ready=1` path runs.
With the new option absent, the original strict predicate is preserved.
Both earlier held-state and RBC-release checks always keep that predicate.

Only the post-release polling guard may admit exact raw MASTINT values
`0` or `2`. SYS_EN and every other bit are rejected. A sampled `2 -> 0`
regression ends the attempt and reports the saved failing sample. The
existing checks still require NO_FETCH/NO_UPDATE, IB size zero, WPTR/RPTR
zero, the owned selector and unchanged sampled fault/reset state.

No device-write expression, startup register sequence, clock setting,
firmware validation, mapping, IRQ handler or resource lifetime is changed.
The existing passive IRQ observer remains auto-armed by the diagnostic;
there is no extra observer boot parameter. The original release-based
two-second deadline is checked before readiness acceptance. Setup time and
arbitrary scheduler delays are not bounded by that logical poll deadline.

The new positive result is `READY_CONVENTION_OBSERVED`, with saved sequential
STATUS/MASTINT/time samples. It establishes neither firmware execution,
interrupt containment, ring consumption nor decoded output. Every outcome
enters the existing nonreturning park and retains all resources until full
power-off. The expected test is not a normal desktop boot.

## Source identity and checks

`0006-drm-amdgpu-ps4-observe-ready-convention.patch` applies after the exact
67-patch dev4 series. The old helper SHA256 is
`9f62e1781b7cc3c885e25f53a106b017cc2aa9cb1f5c110c13796e61c2b8e4cc`.
The patch retains its original MIT SPDX notice. The separate check/fixture
is original GPL-2.0-only Linux psX code.

`check.py` takes the exact old/new helper paths and the selected source's
`drivers/gpu/drm/amd/include/asic_reg` directory. It inserts the actual
guard and poll-loop bodies into `fixture.c`, compiles with strict warnings,
and runs both optimized and ASan/UBSan fixtures. It checks:

- Old/default-new equivalence across all 65,536 low16-bit MASTINT values
  with otherwise valid fields, including identical register-read order.
- The exact new0/2 policy, upper-bit rejection and retained fetch/IB/pointer
  restrictions.
- Sixteen actual poll outcomes, including stable2,0->2,2->0, nonzero
  fault/reset/selector changes, exact-deadline rejection and repeated waits.
- Byte-identical setup, snapshot, firmware validation and park functions;
  unchanged lexical device-write/clock/publication call lists and strict
  earlier guard callsites.

These are host checks of a synthetic register/time model. Logging and
snapshot operations are stubbed; their unchanged real source and compiled
objects require separate review. The lexical comparison is not a whole-kernel
proof, and sampled fault/IRQ state cannot exclude every transient event.

## Build and test handoff

The isolated native target is `7.2-ps4-baikal-uvd-poll-dev1`, release
`7.2.11-psx-ps4-baikal-uvd-poll-dev1`. Its base is
`35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2` with the recorded dev4 overlay
and this additional patch68. A distinct LOCALVERSION identifies it;
device firmware remains external.

Native preparation/build scripts are source tools in this directory.
Measured artifacts, corresponding inputs/source, independent reviews and
the concrete install/rollback handoff are private under workspace
`lab/uvd-2026-10-19/ready-policy/`. Existing dev4 source and artifacts remain
frozen. Preparation does not install media or start a hardware session.

Before activation, use the concrete handoff's exact kernel, whole matching
module tree, unchanged validated initramfs and boot arguments. Establish one
bounded UART experiment, launch once, stop at the first terminal result or
the operator deadline, fully power off, and restore the native boot set.
Do not retry/reset/unload inside the diagnostic boot. Follow
[HARDWARE-TESTING.md](../../docs/HARDWARE-TESTING.md).

The separate [ring packet planner](../ps4-uvd-ring/README.md) is prepared for
the next transport experiment. It is deliberately not linked into this kernel.
