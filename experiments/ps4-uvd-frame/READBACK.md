# Offline UART frame readback

`readback.py` reconstructs and checks one bounded `PS4_UVD_FRAME` raw UART
report emitted by `flow.c.inc`. It does not connect to a device, operate UART,
submit work, load a driver or establish hardware acceptance.

Supply the actual saved raw excerpt and the pinned expected file:

```sh
python3 -B linux-psx/experiments/ps4-uvd-frame/readback.py \
  --raw /absolute/path/to/completed-session.raw \
  --expected lab/uvd-2026-10-20/decode-fixtures/run-001/expected.nv12 \
  --output lab/uvd-2026-10-20/frame-readback/new-private-result
```

The output directory must be new. It is created with mode0700; result files
use mode0600. Existing outputs are never overwritten. Raw input is bounded
to64MiB. The expected file is mandatory: exactly6144 bytes with SHA-256
`12404191ab945b7478093a852545adfbaa9af44bd32f83ccf24f489e385628df`.
A printed expected hash alone cannot substitute for that file.

## Required evidence

The parser ignores unrelated UART lines and printk/device prefixes, accepts
CRLF, and checks every line containing the exact `PS4_UVD_FRAME` marker.
Unknown, truncated, malformed, missing or duplicate records/fields fail.
There must be exactly one PREPARED, CORE, MEMORY, PIXELS and END, with no frame
records outside the single PREPARED-to-END interval. PREPARED must identify
the64×64 reference, pitch256, UV offset16384 and256KiB owned work layout.

END must report `FRAME_REFERENCE_MATCH`, error0, three publications, all
initialization/enabling/pixel flags1, and final phase3. Each phase0..2 must
appear once, published and completed, with cumulative end_dw16,32,48. The
phase counters must report at least two consecutive/joint confirmations.
Exactly the last two saved observations per phase must independently report
valid core/history/predeadline/joint flags and matching fence, context, RPTR
and WPTR. Their times must be ordered, match the phase confirmation time and
be strictly less than2,000,000,000ns, including the observation end times.
Final core guards/reset/context/pointers and captured memory/history fields
must be consistent with the successful result.

All768 PITCHED and64 FEEDBACK32-byte chunks are required. Offset maps reject
duplicate, unaligned, out-of-range, extra or missing chunks and malformed
hex lengths/characters. Printed order does not replace exact offset coverage.
The reconstructed24576-byte target and2048-byte feedback must match the
kernel's printed CRC32 values. CRC is conventional IEEE CRC32, matching
`crc32_le(~0U, bytes, length) ^ ~0U`.

The visible NV12 image is gathered as64 rows of64 Y bytes at stride256, then
32 rows of64 interleaved UV bytes at stride256 beginning at target+`0x4000`.
It must match the actual expected file byte-for-byte and by SHA-256. PIXELS
must report6144 bytes, match1, the pinned expected hash and the independently
recomputed visible hash. Row padding is excluded from visible comparison but
included in the full-target CRC check.

Output files are `pitched.bin`, `feedback.bin`, `decoded.nv12` and
`receipt.json`. Complete reconstructed binaries are retained even if a later
metadata, deadline, CRC or pixel check fails. The receipt status determines
offline success. Incomplete/malformed chunk coverage may leave only the
failure receipt. An existing/unwritable output directory can prevent a receipt;
the CLI returns an output error without overwriting earlier evidence.

Message-page CRC/changed-word fields are preserved as raw metadata. Submitted
message storage may change. The tool does not impose message immutability,
interpret raw feedback as a firmware status structure, infer IB countdown
semantics or label captured zero-fault history as proof of no possible fault.

## UART continuity remains caller-owned

Optional `--session-context PATH` and `--session-record PATH` attach hashes,
byte counts and paths for existing context/receipt evidence. They are opaque:
the tool does not invent a session schema or infer serial continuity from
their contents. It also does not claim that an excerpt equals an entire
session merely because a receipt path was supplied.

Every output explicitly states `offline_only=true`,
`hardware_acceptance=false`, and `uart_continuity.verified=false`.
Before using the result in an acceptance claim, the caller must independently
verify the actual `ps4-uart` bounded session completed with continuous capture
and associate the exact raw excerpt with that session and kernel candidate.
Invalid/restarted/reconnected sessions cannot support acceptance. These
requirements follow the existing workspace procedure; they are not a new
approval or session format introduced by this tool.

## Synthetic checks

Run against the real pinned expected file and a new private test directory:

```sh
python3 -B linux-psx/experiments/ps4-uvd-frame/readback_test.py \
  --expected lab/uvd-2026-10-20/decode-fixtures/run-001/expected.nv12 \
  --output lab/uvd-2026-10-20/driver-integration/frame-readback-tool-01/new-run
```

The corpus is explicitly marked synthetic. It covers complete reconstruction,
private artifacts, mandatory expected-file identity, two-observation timing,
phase/result consistency, memory/history rejection, mutable message metadata,
chunk duplicate/gap/range/hex failures, padding/feedback CRCs, reported hashes,
a changed visible pixel with recomputed CRC/hash, malformed records, opaque
continuity attachments and refusal to overwrite existing evidence. Test
receipts preserve individual synthetic raw inputs and pass/failure artifacts.
They do not exercise or qualify hardware.
