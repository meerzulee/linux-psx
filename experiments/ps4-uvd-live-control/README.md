# ps4-uvd-live-control

Unwired, kernel-style control state for one future live UVD readiness
operation on the PS4: one claim per device and boot, explicit claimed, running,
terminal and invalidation records, and a sticky hardware-entry witness. It
holds copied scalar state under one leaf spinlock and has no hardware,
firmware, allocation, worker or callback path.

Read `DESIGN.md` for:

- the exact admission and entry order;
- the race table;
- the choices root needs to confirm;
- the integration obligations that remain with the future core.

The component is not in any patch, series or Makefile. No kernel build, module
operation or hardware action is associated with it. Host results do not
establish Linux IRQ or memory-model behaviour, DMA quiescence or hardware
readiness.

```text
python3 freeze.py --out <new attempt dir> --attempt <n>
python3 check.py --source <attempt dir> \
  --prepare <lab>/live-prepare/source/attempt-06 --out <new output dir> \
  --cc <compiler> --modes optimized,sanitized,thread,coverage
```

Output directories are never reused. Evidence for the frozen attempts lives
under `lab/uvd-2026-10-20/live-control/`.
