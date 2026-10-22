# Finite installed-Mesa playback observation

`demo-system.py` is an experimental companion to the frozen `demo.py`, not a
replacement for its historical receipts. It imports the existing player argv,
IPC, hardware-thread/decoder gates, fence parser and loop-progress checks.
It selects installed `/usr` Mesa libraries instead of a process-local stage.
No package, boot file, driver quota, clock, reset or rearm is changed.

## Question and scope

The observation asks whether a pinned native 1080p30 AVC clip plays in real time
through one-thread `vaapi-copy` and the installed Mesa OpenGL/compositor path,
with verified libraries and recorded drops/CPU cost. It is not an isolated UVD
throughput benchmark, zero-copy qualification, pixel oracle, browser test or
claim of maximum GPU utilization. Earlier exact pixel checks remain separate.
Three completed passes are a measurement window, not stable adoption or a
replacement for longer/repeated-boot qualification.

## Preflight

Supply the staged candidate manifest as the reference for **installed** file and
symlink identities; no staged library is selected or loaded. The manifest,
clip and frozen demo source need independently recorded SHA256 pins, plus the
expected kernel release, boot ID and published native Wayland socket name.

```sh
python3 -B demo-system.py \
  --output /private/existing-parent/new-observation \
  --manifest /private/mesa-psx-candidate/MESA-PSX-CANDIDATE.json \
  --manifest-sha256 '<reviewed-manifest-sha256>' \
  --clip /private/known-1080p30-baseline.mp4 \
  --clip-sha256 '<reviewed-clip-sha256>' \
  --demo-sha256 '<reviewed-frozen-demo-sha256>' \
  --release '<expected-kernel-release>' --boot-id '<expected-boot-id>' \
  --wayland-display '<published-socket-name>' \
  --amd-debug notiling --passes 3 --timeout 130
```

Default preflight submits no GPU work and does not read fence debugfs. It checks
installed identities, the player/media/source pins, current-boot journal startup
and fault evidence, observer access and the socket metadata. Unexpected ambient
driver overrides are rejected; matching `AMD_DEBUG=notiling` is accepted and
explicitly recorded in the clean child environment. This preserves the current
tested desktop policy, **not** historical policy equivalence with EXP014.

The complete initial kernel journal is capped at 1 MiB and fails closed if too
large; no tail-only scan silently discards an older terminal fault. Subsequent
observations and the final check advance a journal cursor. A journal failure is
an observation failure, not proof of a GPU fault. The final kernel check follows
the final fence observer because that debugfs read processes real writeback and
can signal callbacks; it is not a purely passive cached-counter read.

## Explicit hardware execution

After authority, one declared bounded experiment and continuous UART readiness
are established, add `--run --experiment EXP-YYYYMMDD-NNN --uart-session
'<active-external-session-reference>'`. The UART reference is a caller assertion,
not authentication. Parent supervision must stop the whole experiment on a fault
or failed prerequisite; an internal process timeout does not cancel GPU work.

The player starts paused for decoder/thread/map checks, unpauses once, and stops
after the requested number of wraps **even when drops occurred**. A wrap counts
a completed pass plus a possible partial following pass before the 1 Hz stop.
The process deadline includes initialization; ordinary quit has a separate
five-second allowance. A stuck process PID is retained, never SIGKILLed/reset.
One-second OSD/telemetry adds monitoring and GL/compositor work to the pipeline.

Receipts distinguish terminal kernel faults, workload failure, observer failure,
incomplete observations, postflight identity/outstanding-work failure, drops and
pacing outside 0.97–1.03×. Neither an observer error nor a process exit proves a
terminal boot or recovery. Inspect UART, guard state and operator evidence.

## Metrics and limits

- CPU is accumulated mpv user+system time over the sampled interval, reported as
  a percentage of **one CPU core**, not of the whole eight-core system.
- UVD fence deltas include management jobs and are not decoded/displayed-frame
  counts, per-frame latency, client identity or recovery evidence.
- DRM fdinfo is retained per client with duplicate FDs deduplicated. Engine
  nanoseconds are scheduler accounting, affected by completion observation; they
  are **not physical UVD busy percentage**. Missing `dec` is unavailable, not 0%.
- The required hardware decoder and disabled fallback establish the chosen
  pipeline. Valid pixels need an independent oracle; visual correctness and
  responsiveness need the operator. Runner `hardware_acceptance` stays false.

Portable synthetic checks (no process, journal, decoder or GPU access):

```sh
python3 -B checks/demo_system_check.py
```
