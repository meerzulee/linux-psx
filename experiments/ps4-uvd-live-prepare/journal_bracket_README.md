# Boot-correlated cached acquisition and journal bracket

`journal_bracket.py` wraps the frozen `acquire.py`
(`9811aa5b174d58857f733cf458f6ecd264717f8d416d8b9b597b1a0959e1de3c`).
It emits one JSON object. It reads fixed outer boot identity files, invokes
the acquisition producer once, optionally reads `/proc/1/comm`, makes one
bounded read-only journal query, and rereads outer identities. It creates no
report file, opens no network connection and contains no SSH, mount, sudo,
discovery, retry, journal flush/sync/follow or device-control operation.

The original acquisition producer remains unchanged and process-free. Only
this wrapper may spawn the single journal child. Tests have used injected
fakes exclusively; this implementation task did not read live proc/sys files,
run journalctl, spawn a real child or acquire console evidence.

## Public core API

```python
collect(
    expected_release=..., expected_pci_bdf=..., status_path=...,
    producer=..., reader=..., uname=...,
    monotonic_ns=..., wall_time_ns=..., journal_runner=...,
    include_pid1_comm=False, source_pins=None,
)
```

Every runtime dependency is mandatory. `producer()` returns the frozen
acquisition schema-2 object. `reader(path, limit)` returns exact bytes through
EOF or at most limit+1 bytes indicating overflow; an exception may carry
`.partial` bytes. `uname()` returns raw release bytes without a newline.
Clocks return nonnegative integer nanoseconds. The journal runner receives
the exact argument list and fixed keyword bounds and returns the fields
documented below. No live defaults are selected by this core.

Order:

1. `outer_start` clocks; boot ID, uname and uptime before collection.
2. `before_acquisition`; one producer invocation; `after_acquisition`.
3. Optional explicit `/proc/1/comm` read, followed by `before_journal`.
4. One journal query; `after_journal`.
5. Boot ID, uname and uptime after collection; `outer_end` clocks.

Wrong or malformed initial identity prevents both producer and journal calls.
An inconsistent producer identity, wrong release or wrong DRM BDF suppresses
the journal query. End identities are still preserved after an attempted
producer or query failure. A denied cached-node read with otherwise consistent
identities can retain the one journal query as additional incomplete evidence;
the cached read is never retried or replaced. Missing journal access or a failed
query cannot produce a complete result.

Outer reads are limited to `/proc/sys/kernel/random/boot_id` (64 bytes),
`/proc/uptime` (128 bytes), and the explicitly requested `/proc/1/comm`
(64 bytes). Unames have a 128-byte limit. Native opens use O_RDONLY,
O_NOFOLLOW, O_CLOEXEC and O_NONBLOCK and require a regular/seq-file node.
These are byte bounds, not timeouts for kernel reads.

## Exact journal request and child lifetime

The dashed outer boot UUID is converted to 32 lowercase hexadecimal digits.
No relative boot offset or unqualified current-boot selector is used:

```text
/usr/bin/journalctl
--boot=EXACT_32_HEX_OUTER_BOOT_ID
_TRANSPORT=kernel
--no-pager
--output=json
--all
--output-fields=MESSAGE,_BOOT_ID,_TRANSPORT
--grep=PS4_MAP_CANARY (BEGIN|READY_LAYOUT|MAP)|PS4_LIVE_PREPARE PREPARED
--lines=64
```

This is an argv vector, with the entire grep expression as one argument; no
shell is involved. The [official journalctl documentation](https://www.freedesktop.org/software/systemd/man/latest/journalctl.html)
specifies that explicit boot IDs filter `_BOOT_ID`, grep with lines implies
reverse order, and some metadata remains present despite output-fields.
Journal JSON fields can also contain null or arrays. The wrapper preserves
all stdout bytes in their original order and does not interpret those fields.

https://www.freedesktop.org/software/systemd/man/latest/journalctl.html

Stdout is bounded to 262,144 bytes and stderr to 65,536 bytes; an overflow
preserves at most limit+1 bytes as an observed prefix. The child has a
5-second monotonic deadline. On overflow, timeout or an adapter failure,
cleanup signals only the `Popen` child created by this invocation: SIGTERM,
at most 0.5 seconds waiting, then SIGKILL and at most another 0.5 seconds if
needed. It never signals a process group or another PID. Reaping or cleanup
failures are reported; they do not trigger another query. Spawn itself and
kernel reads cannot be given a hard total-program deadline by this adapter.

The child receives no stdin. Its environment fixes PATH and locale, disables
colors and directs its diagnostics to stderr instead of requesting journal
logging. Pipes are consumed with an injected-selectors adapter and closed on
exit. The only normal native executable is `/usr/bin/journalctl` with the
fixed arguments above.

`run_journal()` returns raw `stdout`/`stderr` bytes, `stdout_eof` and
`stderr_eof`, `returncode`, `spawned`, `timed_out`, `overflow_stream`,
`signals_sent_to_own_child`, and `errors`. Process creation, selector factory,
pipe reader and monotonic clock are injectable. Tests never use real defaults.

## Report schema for the outer binder

The report is schema 1, producer
`ps4-uvd-preparation-boot-journal-bracket`. It carries:

- `acquisition`: the producer's object with unchanged contents. A hash of its
  canonical JSON is an **embedded serialization** hash, not a hash of original
  standalone acquire stdout.
- `observations`: `boot_id_before`, `uname_before`, `uptime_before`, optional
  `pid1_comm`, `boot_id_after`, `uname_after`, `uptime_after`. Each has `source`,
  `limit_bytes`, `raw`, `parsed`, and any read-error metadata. A raw record has
  `base64`, `sha256`, `bytes`, `source_complete`, and `representation`
  (`exact_bytes` or `observed_prefix`). Parsed boot IDs retain dashes; uname is
  a string; uptime has `uptime_centiseconds` and `idle_centiseconds`; PID1 has
  no parsed semantic value.
- `timestamps`: the six named brackets above, each with `monotonic_ns` and
  `wall_time_ns`. Guarded middle steps are absent when skipped. Clock errors
  and regressions remain explicit. Wall-clock adjustment is reported separately.
- `journal`: exact `argv`, `limits`, raw-record `stdout` and `stderr`, the
  child result metadata, `query_complete` and an order note. Limits also record
  the 64-entry request and cleanup waits. No JSON journal parsing occurs here.
- `source_pins`: acquire and wrapper source SHA-256 supplied by the CLI or
  in-memory renderer; these establish supplied-source provenance, not runtime
  binary attestation.
- `events`, `issues`, `producer_calls`, `journal_attempted`,
  `clock_brackets_complete`, `identity_consistent`, `source_complete` and
  `complete`.

`source_complete` means the required sources and clocks were collected, the
embedded acquisition completed, and the bounded query exited successfully
with both streams through EOF. It does not mean the journal is exhaustive,
that PID1 equals systemd, or that message contents are meaningful. Empty
journal stdout makes `complete=false` while its EOF observations remain
source-complete. Changed end identity likewise preserves successfully
collected bytes while refusing a complete bracket.

The positive status is `CAPTURED_BOOT_CORRELATION_INPUTS`, which is only an
input-collection result. Every report has `hardware_acceptance=false`,
`semantic_evaluation_performed=false` and `binary_attestation=false`.

The independent binder must replay/check these observations, validate the
embedded acquisition, check actual PID1 evidence when required, reject a
64-record cap as potentially truncated, and match exact UART MAP/PREPARED
message text with journal `_BOOT_ID` and `_TRANSPORT`. Journal receive times
must not substitute for UART ordering or historical proof times. Physical
addresses are corroborating fields, not guaranteed-unique boot nonces.

## CLI and pure stdin renderer

Future reviewed CLI invocation:

```sh
python3 -B -I journal_bracket.py \
  --expected-release 7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1 \
  --expected-pci-bdf 0000:00:01.0 \
  --status-path /sys/kernel/debug/dri/NUMBER/amdgpu_ps4_live_prepare \
  --include-pid1-comm
```

The operator plan supplies the actual directory; there is no discovery step.
CLI help and argument errors are JSON only. Arguments cannot be abbreviated or
duplicated. Exit codes: 0 for complete collection/help, 1 for incomplete
collection, 2 for request/bootstrap/CLI errors. No output is saved by the tool.

`render_remote_program(acquire_source_bytes, wrapper_source_bytes) -> bytes`
is a pure helper. It verifies the exact frozen acquire digest, embeds both
reviewed sources with their SHA-256 values, checks those values before loading,
and loads modules using non-`__main__` names. Therefore acquire's own CLI does
not run separately. It calls only the wrapper CLI and emits JSON on bootstrap
failure. The rendered program is intended for `python3 -B -I -` over stdin;
it writes no remote source file. Rendering itself executes neither source.

The primary host wrapper must pin the wrapper source and rendered-program
bytes and preserve the SSH transport receipt separately. This module provides
no SSH client and does not perform that transport. Source hashes do not
authenticate a potentially untrusted host or its running kernel.

## Offline verification

```sh
python3 -B -I linux-psx/experiments/ps4-uvd-live-prepare/journal_bracket_test.py \
  --report /absolute/new/path/journal-bracket-test.json
```

The 32 Mac cases use only injected readers, identities, clocks, processes,
selectors and pipe reads. They cover wrong initial/final identities, denied
cached status, failed/empty/truncated journals, raw JSON arrays/null/order,
PID1 evidence, byte bounds, child timeouts and escalation, read-only paths,
JSON-only CLI, source pins and the in-memory renderer. Earlier test reports
are preserved. Native synthetic execution, review, final binding and any
actual acquisition remain separate primary-agent work.
