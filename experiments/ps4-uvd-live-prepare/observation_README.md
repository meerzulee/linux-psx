# Saved preparation-observation evaluator

`observation.py` evaluates three already-saved byte streams: the preparation
debugfs text, `uname -r`, and `/proc/cmdline`. The caller supplies the expected
kernel release from the selected build. It does not collect these files or
control a device, and it does not create or conclude an experiment.

The positive result is deliberately narrow:

```
MATCHED_CACHED_PREPARATION_OBSERVATION
hardware_acceptance: false
```

That result means the supplied bytes match the specified cached-observation
contract. Saved files cannot establish freshness, authenticity, shared
acquisition time, the actual running binary, current mapping, normal boot,
ordinary GPU-client coexistence, UART continuity, teardown or decoding.

## API

```python
from observation import evaluate

report = evaluate(
    saved_status_bytes,
    saved_uname_bytes,
    saved_cmdline_bytes,
    expected_release="7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1",
)
```

The API requires `bytes` for each saved input so hashes cover the original
bytes without newline/encoding normalization. It returns a JSON-serializable
dictionary, opens no files, invokes no process and performs no discovery or
device access. The explicit expected release must be one release token.

The result preserves SHA256 and byte length for all three inputs, parsed
typed status fields, raw status field/value entries (including duplicates),
the parsed saved release, command-line tokens and probe arguments, all detected
issues, source anchors and limitations. `matched` is true only for the narrow
positive outcome; `hardware_acceptance` and `live_acquisition_performed` are
always false. The caller must not promote that match to a passed operator plan.

## CLI

After the caller has saved the inputs elsewhere:

```sh
python3 -B linux-psx/experiments/ps4-uvd-live-prepare/observation.py \
  --status-file /absolute/path/to/saved-status.txt \
  --uname-file /absolute/path/to/saved-uname.txt \
  --cmdline-file /absolute/path/to/saved-cmdline.txt \
  --expected-release 7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1 \
  --report /absolute/path/to/new-observation-report.json
```

All paths are explicit. Inputs must be regular saved files, not symlinks or
special files; the CLI rejects standard `/dev`, `/proc` and `/sys` paths.
It does not search directories or read any default device/status location.
Each input is bounded to 65,536 bytes. The report's parent directory must
already exist. The only file the evaluator writes is that new report, opened
exclusively; it never overwrites an existing path. It runs no subprocess,
SSH, sudo, mount or hardware operation.

Exit codes: `0` for the narrow match, `1` for a saved-input non-match with a
report, `2` for CLI/file/API misuse where evaluation or report creation could
not complete. Stdout contains only a compact status/report-path summary. A
non-match still preserves the original input hashes and parsed observations.

## Exact checks

The status parser uses the eight line groups and exact field order in
`debugfs.c.inc`. A final LF, single spaces, exact keys, numeric widths/ranges,
known stages/events and canonical C numeric spellings are required. Duplicate,
unknown, missing, reordered or wrongly typed fields never pass. The tests
derive their fixture text from the real C `seq_printf`/`seq_puts` format strings.

The checker requires:

- Schema 1; `build` and saved `uname -r` both equal the explicit expected
  release. Matching strings are not a binary attestation.
- `interface=prepare-only`, `arm=absent`, `observer=cached-only`,
  `current_hardware_mapping=not-sampled`, `UVD_setup=never`.
- One enabled `amdgpu.ps4_uvd_live_prepare` argument before any `--` separator.
  Bare Boolean true and canonical `1`, `y/Y`, `t/T`, `true/True/TRUE`, and
  `on/On/oN/ON` are admitted. Duplicate values, including identical duplicates
  and hyphen/underscore aliases, are rejected. Arbitrary truthy prefixes are
  not accepted.
- No old probe argument, even an explicit zero: `amdgpu.ps4_map_canary`,
  `amdgpu.ps4_uvd_ready`, `amdgpu.ps4_uvd_poll_state`, `amdgpu.ps4_uvd_token`,
  `amdgpu.ps4_uvd_data_control`, `amdgpu.ps4_uvd_status_observation`, or
  `amdgpu.ps4_uvd_ib_data`. Unqualified spellings are also refused by this
  observation profile; this does not assert the kernel recognizes them.
- PREPARED, error 0, every selected/attempted/retained/reservation/allocator/
  early-fence/historical-proof flag set, nonzero proof timestamp and reset
  counter at proof 0.
- Equal observed/proof generations of at least 1, with GART_ENABLE as the
  last event. The frozen boot path initializes this state before GMC's GART
  enable call; generation 0/NONE cannot describe its completed preparation. This admits
  cached history consistent with the helper, not proof that a GART event or
  hardware acquisition occurred. It never asserts current mapping validity.
- VMID4, 812 mapped pages, PT `0x2000`, backing `0x308000`, ring `0x1000` and
  IB `0x20000` bytes. PD size is page aligned, covers entry `0xc00`, and is at
  most `0x100000`; it is not hardcoded to the historical `0x20000` observation.
  PD address metadata is nonzero, page aligned and has no size-addition
  overflow. The text lacks runtime max PFN and VRAM bounds, so this does not
  establish the exact runtime PD size, placement or ownership.
- Copy CRC `570ae4eb`. This is independently source-anchored, not guessed:
  64 little-endian u32 words `0x50533400 ^ ((i * 0x01020409) mod 2^32)`,
  i=0..63, form 256 bytes with SHA256
  `e61c071effa22623d279456a2367b26fa114b894bb573af2f542d385415e3715`.
  Reflected CRC32 with polynomial `0xedb88320` and initial/final XOR
  `0xffffffff` agrees with zlib. The primary's
  `lab/uvd-2026-10-20/live-prepare/handoff/SDMA-PATTERN-REFERENCE.json`
  records this independently. Matching a saved CRC remains saved-text evidence.

The saved command line is a canonical unquoted profile, not shell syntax.
Quotes/backslashes are refused rather than interpreted using incompatible
shell rules. This can reject other valid kernel command-line spellings;
it does not claim those spellings fail in the kernel. Unrelated canonical
arguments are retained but do not change the preparation verdict. Old probe
arguments after `--` are still refused by the profile.

## Distinct non-pass results

Malformed input, stale build identity, wrong boot gates, wrong labels,
nonzero error, generation drift, wrong geometry, CRC mismatch and missing
proof predicates remain separate outcomes. Readable lifecycle stages have
their own `OBSERVED_CACHED_<stage>` result, including INVALIDATED, RELEASING,
RELEASED and FAILED. When several problems coexist, all issues and parsed
fields remain in the report; the headline uses deterministic priority.

No result establishes hardware acceptance. The future operator plan, capture
continuity and acquisition checks belong to the primary workflow, not this
offline parser.

Run its Mac offline tests with a fresh evidence directory:

```sh
python3 -B linux-psx/experiments/ps4-uvd-live-prepare/observation_test.py \
  --report-dir lab/uvd-2026-10-20/live-prepare/observation-tool/attempt-01
```

The test files are synthetic observations rendered from the exact pinned C
formatter. No console or saved hardware acquisition is exercised.
