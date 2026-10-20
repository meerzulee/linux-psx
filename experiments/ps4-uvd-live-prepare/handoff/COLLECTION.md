# One host SSH capture

`collect_host.py` records one future SSH observation for the preparation-only
experiment. It consumes the reviewed stage and the operator's already saved
launch report. It does not install files, allocate an experiment, operate UART,
launch Linux, grant permissions or decide the experiment result.

Before the real stage is reviewed, include the exact collector path and SHA-256
as the observation contract's `host_transport` tool. The existing stage builder
copies that source and pins the role in the plan. The collector requires those
bytes to match its own source, and pins the result binder to
`6dc833595d1a7a7eb95952da6ffb109dadc5846ccb4b5d41f1fb442c4eb1451b`.
It reuses that binder's complete stage/install validation, saved-file reader,
launch/transport schema and remote argument list. Its remote Python program
comes from the exact acquisition and journal sources in the reviewed stage.

The endpoint, cached status node and existing read permission must be established
from evidence before this call. A path such as
`/sys/kernel/debug/dri/7/amdgpu_ps4_live_prepare` selects numeric minor 7; the tool
does not assume `card0`, discover minors, mount debugfs or use sudo. The remote
program checks the requested release and PCI BDF before acquiring the cache.
Credentials are not arguments or embedded program inputs: SSH uses the owner's
existing authentication and host configuration. Supply only a host alias,
hostname, IP address or `user@host`, not an SSH command, URI or option string.

The launch report is data provided by the operator and recorded by the owner.
It must use `extract.py`'s exact `OPERATOR_LAUNCH_REPORT` schema and identify one
launch after installation, including the separately reported full power-off
since the retained experiment. The collector never authors that report. The
following command is a template; every capitalized value must come from the
reviewed plan and actual recorded launch:

```sh
python3 -B linux-psx/experiments/ps4-uvd-live-prepare/handoff/collect_host.py \
  --stage "$REVIEWED_STAGE" \
  --reviewed-sha256 "$REVIEWED_HANDOFF_SHA256" \
  --plan-sha256 "$PLAN_SHA256" \
  --launch-report "$SAVED_OPERATOR_LAUNCH_REPORT" \
  --ssh-target "$EVIDENCED_SSH_ENDPOINT" \
  --status-path "$EVIDENCED_NUMERIC_STATUS_PATH" \
  --output-directory "$NEW_HOST_OUTPUT_DIRECTORY"
```

All validation occurs before the SSH invocation. The destination must be absent,
outside the immutable stage and under an existing real host directory. Inputs
and destination ancestors cannot be symlinks. A refused preflight creates no
output. After preflight, files are created exclusively with mode 0600 under a
0700 directory; an interrupted or incomplete directory is preserved.

There is one local `/usr/bin/ssh` child, no local shell and no retry. The remote
argv is POSIX-quoted as one command argument and sends the exact rendered program
through stdin to `python3 -B -I -`, including `--include-pid1-comm`. SSH forwarding,
TTY allocation, connection sharing, backgrounding and configured local commands
are disabled. Strict host-key checking and batch mode remain enabled; no
interactive authentication or automatic new host-key acceptance occurs.
Owner-configured routing may still use SSH's existing proxy helpers.

The process I/O budget is 30 seconds, stdout is bounded at 4 MiB and stderr at
256 KiB. An overflow retains at most the limit plus one byte as evidence.
Stdin, stdout and stderr are serviced concurrently. On timeout/error/overflow,
cleanup signals only the owned SSH child: TERM, then KILL if necessary, with
0.5 seconds for each wait. Local SSH termination does not prove that a remote
kernel read has stopped; an uninterruptible remote read may remain blocked.
No other process is discovered or signaled.

Outputs:

- `program.py`: exact rendered stdin bytes.
- `operator-launch.json`: byte-identical copy of the provided report.
- `STARTED.json`: pre-call input pins, exact SSH argv and fixed budgets.
- `stdout.bin` and `stderr.bin`: captured bytes, including bounded prefixes on
  incomplete reads. These are saved before the final clock read so a clock
  failure does not discard them.
- `COLLECTION.json` and `transport.json`: only after complete process accounting,
  EOF on both streams, complete stdin publication and valid clocks. The latter
  is the exact `extract.py` transport schema, including actual returncode and
  source/plan/program/raw-stream hashes.
- `TRANSPORT-FAILURE.json`: failure-only diagnostics, actual process metadata
  and any provisional transport fields; no normal `transport.json` is published
  on timeout, overflow, signal cleanup, missing EOF, clock failure or I/O failure.

The aware UTC and host monotonic interval starts immediately before the runner
and ends after returned stream prefixes are persisted. It includes host
persistence overhead and makes no hardware latency claim. A fully accounted
nonzero SSH exit is recorded with its actual returncode; it is not a successful
transport. A zero exit establishes only that the process completed. No cached
status, journal, UART, boot-correlation or userspace claim is inferred here.
`hardware_acceptance` and `semantic_evaluation_performed` remain false.

The final offline binder consumes `transport.json`, the copied operator report,
exact streams and separately closed full UART evidence. Do not feed a
failure-only record to it as a successful transport. No automatic acquisition
retry or native restoration is performed.

For dependency-injected testing:

```sh
python3 -B linux-psx/experiments/ps4-uvd-live-prepare/handoff/test_collect_host.py \
  --report "$NEW_SYNTHETIC_TEST_REPORT"
```

These tests use actual stage/renderer/binder code with temporary synthetic files,
fake processes, fake pipes and fake clocks. The existing module-archive validator
runs local `zstd` only on the synthetic archive. No SSH, console, UART, live
proc/sys reads or media action occurs. Root must review/pin the final collector,
run this suite natively, add the contract role before final staging, and supply
the real launch report and evidenced endpoint/minor for the later single capture.
