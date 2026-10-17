# Hardware experiments and acceptance

Source review, tests and builds do not establish console support. No console
operation, payload, boot-file write, storage overwrite or public release is
authorized merely by these instructions; obtain task authority first.

Use continuous UART capture plus **one bounded session per coherent hardware
experiment**. A session may cover related setup, one launch, observation and
rollback when they answer one question. Routine navigation and read-only media
inspection do not need artificial markers. Source edits, builds and host tests
do not require a console UART session.

Before a consequential action:

1. Assign `EXP-YYYYMMDD-NNN`; state one objective, expected evidence, timeout
   and safe rollback. Keep at most one active experiment.
2. Confirm continuous UART capture and begin the bounded session before the
   boot, driver load, power transition, storage write or acceptance test.
3. Give the operator the complete short action sequence and stop conditions.
4. Keep the session open through that sequence; stop on a terminal fault or
   safety boundary. Keep continuous capture running between sessions.
5. Close it when the question is answered, read the compact context first,
   and record one concise conclusion with source/artifact identities.

Only **completed** capture continuity supports acceptance. A logger restart
or serial reconnect invalidates that partial session. UART collection can use
the existing separate `ps4-uart` tool or an equivalent recorder; the kernel
does not depend on that tool or a distribution repository.

Change one diagnostic variable per experiment. Do not repeat a failure without
recording new evidence or the changed variable. If the same failure repeats
three times without new evidence, stop that path, mark it blocked/inconclusive
and move down a layer or restore the known recovery state. Conclude the session
before changing diagnostic branches.

## Evaluation record

Record console model/southbridge, boot chain, kernel/config/module hashes,
firmware availability, source pins, experiment ID, UART continuity, operator
observations and verdict (`PASS`, `FAIL`, `INCONCLUSIVE`, `DEGRADED`). Retain raw
logs privately; publish only reviewed/redacted conclusions. Distribution-owned
behavior belongs in its repository; kernel evidence belongs with the target.

For this target, Ethernet evaluation must establish fresh first activation,
DHCP/routing, independent hash-checked transfers in both directions, HTTPS,
operation beyond 900 seconds, admin cycles with an unchanged DMA generation and
cable recovery. Record initial-open and admin-down gating separately. Do not
infer DMA drain from stop acknowledgement. Never unload/reset/rearm a retained
generation.

A successful boot once is evidence. Promotion requires a written plan with
explicit repetition counts and observed criteria. For native Ethernet autoload
the owner set the count at two fresh boots of the same patch set:
EXP-20261017-019 (cable at launch, one replug, beyond 900 seconds) and
EXP-20261017-020 (no cable at launch, then plugged). Both passed as local build
`6.18.44-psx-ps4-baikal-native-dev1`. `ip link` admin down/up, Orbis Ethernet
after Linux, repeated cold boots and other consoles were not tested, and the CI
builds have not been booted. Historical EXP016 remains DEGRADED; consult
[the status record](../platforms/ps4/STATUS.md).
