# Boot-only public CREATE and polled prefix fence

This source experiment adds one finite returning controller after the existing
readiness convention. Its private boot wrapper retains every mapped resource
and parks after the result. It adds no live endpoint, reload path, cleanup,
reset, rearm, retry or decoded-frame claim.

The baseline is the frozen readiness/fault-history composition:
`lab/uvd-2026-10-20/driver-integration/attempt-01/`, manifest
`e253c4fee2ec15e7da8a3f74633c1a58b28728a955b68ba614ba6f282a1d2485`.
The native Makefile and old planner headers are pinned in
`lab/uvd-2026-10-20/message-create/kernel-inputs/INPUTS.json`.

The new gate is `amdgpu.ps4_uvd_message_create=1`, immutable `0444`, default
false. It requires `ps4_uvd_ready` and `ps4_uvd_poll_state`; token, DATA0,
status-observation, IB-marker, map-canary-only and live-preparation modes
conflict. A selected CREATE leaf with missing prerequisites is rejected before
canary allocation. The existing global boot latch and pre-client exclusion
remain mandatory.

`build_candidate.py` copies exact pinned source to an unused output directory,
applies deterministic additive changes and emits a patch plus source receipt.
It never modifies the composed baseline, builds a kernel or touches devices.
The four serializer/planner files are copied verbatim from their separately
reviewed sources, with their notices. The Makefile adds the two C objects
explicitly. The existing generic IB descriptor carries the complete 128 KiB
allocation only in this mutually exclusive mode; historical IB-marker mode
continues to receive the original first-page descriptor.

From the workspace root, with a new output path for every attempt:

```sh
python3 linux-psx/experiments/ps4-uvd-message-create/build_candidate.py \
  --workspace "$PWD" \
  --library-pins lab/uvd-2026-10-20/message-create/LIBRARY-PINS.json \
  --output "$PWD/lab/uvd-2026-10-20/message-create/source/NEW-ATTEMPT"

python3 linux-psx/experiments/ps4-uvd-message-create/check.py \
  --candidate lab/uvd-2026-10-20/message-create/source/NEW-ATTEMPT \
  --headers linux-ps4-git/src/linux/drivers/gpu/drm/amd/include/asic_reg \
  --output lab/uvd-2026-10-20/message-create/host-tests/NEW-ATTEMPT
```

The header location is an input path, not a requirement to build from the
historical checkout. The checker requires exact recorded public-header hashes.
The primary agent owns native host execution, full matching kernel/modules
build, compiled review and any later hardware plan.

The host fixture extracts the actual new controller, gate predicates, wrapper,
whole-region owner accessor and unchanged direct/indexed sampling helpers.
It supplies controlled register/memory/time observations, and separately links
the actual serializer/planner C files. It does not emulate UVD, firmware,
messages, DMA or PS4 timing. It checks patch replay and unchanged old source,
plus optimized and ASan/UBSan execution and exact-witness sensitivity cases.
Apple's sanitizer runtime does not support leak detection; the receipt records
`detect_leaks=0` there and `detect_leaks=1` on Linux. ASan and UBSan remain
enabled on both. The tested controller allocates nothing.

See the lab `DESIGN.md`, attempts and handoff for the exact evidence and
limitations. No result from this experiment establishes CREATE parsing,
session creation, usable decoding, general coherence or DMA quiescence.
