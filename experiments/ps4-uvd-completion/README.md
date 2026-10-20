<!-- SPDX-License-Identifier: GPL-2.0-only -->
# PS4 UVD host completion model

This original GPL-2.0-only C11 component models a small recovered userspace
completion interface. It keeps sequence classification separate from explicit
retirement. It is a host experiment with no kernel, target, boot or driver
integration and no command-packet or firmware ABI implementation.

Run from the repository root:

```sh
python3 experiments/ps4-uvd-completion/check
```

The check compiles the actual header through the independent fixture with strict
warnings at `-O2`, then at `-O2` with AddressSanitizer and UndefinedBehaviorSanitizer.
Both runs are required. Select another native compiler with `CC` or `--cc`.
Only temporary host executables are created. No container or kernel build is
needed.

## Observed interface

For expected sequence `E`, the recovered interface classifies one 32-bit progress
value using this exact window. All arithmetic wraps modulo 2^32.

| Progress value | Classification | Candidate retirement count |
| --- | --- | --- |
| `E - 1` | Pending | 0 |
| `E` | Ready | 1 |
| `E + 1` | Ready | 2 |
| Any other value | Diagnostic mismatch | 0 |

This supports two stored records and accepts wraparound. For example,
`E = 0xffffffff`, progress `0` permits two completions. Arbitrary forward jumps
do not count as progress. The native predicate returns the same nonzero value
for pending and mismatch; this model exposes their distinction directly.

The source observation is documented privately outside this repository at
workspace `lab/uvd-2026-10-19/proxy-completion/findings.json`, claim `P8`, with
Evidence ID
`ev_67bbca1676c989071b94ccef8987ca256fcdde363edcc6d21a46376aad2a7c9d`.
The report also separates accepted submission, progress, record retirement,
scratch copyback and client notification. This directory contains original
implementation and tests of interface facts; it contains no retail executable,
disassembly, decompiled source or firmware bytes.

## API and Linux design choices

`ps4_uvd_completion_assess(state, sample)` takes both inputs by value and returns
a status and validated count. It cannot change the caller's state. Outstanding
work must be in `0..2`; a ready count larger than the outstanding count is
rejected. Every result except `READY` carries count zero, including pending,
mismatch, over-completion, timeout, failure and invalid inputs.

`ps4_uvd_completion_commit(&state, sample)` is the only state-changing operation.
It assesses the supplied sample, then on `READY` advances `expected` and
`retired` by the validated count and subtracts it from `outstanding`. Both
sequence counters wrap modulo 2^32. Every rejection leaves all fields unchanged.
A null state is rejected. The caller must supply a valid object and serialize
state access; this component is not an atomic queue or a synchronization API.

The caller captures one progress word and passes it by value. Assessment and
commit must use that captured value when describing the same observation.
Neither function samples hardware. This deliberately avoids the native path's
separate predicate read and later count read. The explicit outstanding checks,
distinct mismatch result, timeout/failure inputs and failure-retention policy
are Linux model choices, not claims about every native error path. In particular,
the native error path includes a diagnostic record pop whose safety is unknown.

Timeout and failure samples take precedence over the word, even if it resembles
a ready value. An invalid outstanding count takes precedence over sample kind.
The caller supplies the finite deadline and reports expiry as a timeout sample;
the recovered time units and blocking-wait bounds remain unresolved.

The state records scalar accounting only. It owns no buffer pointer and performs
no allocation, free, copyback, notification, MMIO or DMA operation. A successful
commit does not authorize releasing real resources. The identity of the progress
writer, CPU/GPU visibility, command encoding, decoded output and safe device
quiescence all require separate evidence.

## Fixture coverage

Literal cases cover zero, UINT32_MAX rollover, the signed boundary, distant
values, and each legal outstanding count. Separate operation sequences check
pending and replay, one-at-a-time and batch retirement, both sequence and retire
counter rollover, over-completion and an empty queue. Failure and timeout cases
carry a ready-looking value and must retain the complete state. Canaries and
retained-buffer bytes surround the model state and are checked after operations.
Pure assessment never retires anything; only an explicit valid commit does.
