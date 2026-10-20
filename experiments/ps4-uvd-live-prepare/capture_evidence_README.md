# Saved acquisition evidence validator

`capture_evidence.py` validates already-saved stdout JSON from the frozen
schema-2 acquisition producer. It accepts bytes through an importable API;
there is no CLI, live path reader, SSH, device, clock, mount, privilege or
control interface. It does not create or conclude a hardware experiment.

A positive result is named narrowly:

```
MATCHED_SAVED_PREPARATION_CAPTURE
hardware_acceptance: false
```

This means the supplied report is internally consistent with the reviewed
producer, matches the caller's complete boot policy, and matches the cached
status evaluator. It does not prove acquisition happened, authenticity,
freshness, a running kernel binary, normal userspace, current page tables,
GPU coexistence, UART continuity or timing. A consistently fabricated capture
cannot be distinguished from an actual one by this offline tool alone.

## API

```python
result = capture_evidence.evaluate(
    saved_acquire_stdout_bytes,
    expected_release="7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1",
    expected_pci_bdf="0000:00:01.0",
    status_path="/sys/kernel/debug/dri/7/amdgpu_ps4_live_prepare",
    expected_bootargs=caller_pinned_bootargs_bytes,
)

report = result["report"]
raw_inputs = result["raw_inputs"]
```

The directory `7` is an example of a caller-selected path, not a default or a
discovery rule. The caller supplies every pin. The original JSON and expected
bootargs must be `bytes`, so their SHA256 values cover the supplied encoding
and newlines. Invalid API types raise `TypeError`; malformed input or a failed
contract returns a non-match report.

`report` is JSON-serializable. It includes the exact capture hash/length,
expected pins and source pins, recomputed raw-observation hashes, diagnostic
producer declarations, replay result, boot-policy results and the complete
status-evaluator result when the acquisition is complete.

`raw_inputs` is empty unless the entire saved producer report matches a
complete synthetic replay. On that match it contains the original bytes of
all twelve observations, keyed by the producer's names:

```
uname_before          uname_before_status          uname_after
boot_id_before        boot_id_before_status        boot_id_after
uptime_before         uptime_after
drm_name_before       drm_name_after
cmdline               cached_status
```

A valid complete capture can still have an invalid boot policy or a cache
non-match. Raw-byte availability is not an acceptance flag. A root combiner
must inspect the separate results and retain the original saved capture.

The positive identity summary is rebuilt by the producer replay and includes
the boot ID, release, driver, PCI BDF and selected status path. This API does
not receive an expected boot ID, so another internally consistent boot ID is
reported rather than declared fresh or tied to a particular experiment. The
primary workflow owns the external journal/UART/boot-ID binding.

## Producer replay

The validator loads only the known sibling `acquire.py` and `observation.py`
modules. Their normal Python source imports occur when this module is loaded;
`evaluate()` itself performs no filesystem access. Use the caller's usual
`python3 -B` invocation to suppress bytecode-cache writes. The reviewed peer
pins are:

| Source | SHA256 |
| --- | --- |
| `acquire.py` | `9811aa5b174d58857f733cf458f6ecd264717f8d416d8b9b597b1a0959e1de3c` |
| `observation.py` | `e6a28755d96292c101bfd5cd0032565075a0f993c09b88b6073281bada46ee2a` |

The tests check these actual source hashes. The runtime API records these
reviewed pins but deliberately does not reread source files or attest runtime
module integrity. The integration/review chain must keep the peer sources
at those bytes. No frozen peer file is changed by this implementation.

For a complete capture, the validator:

1. Parses one bounded JSON object, refusing duplicate keys at every depth,
   floats, nonfinite numbers and overlong integer spellings. The JSON limit
   is 512 KiB. Whitespace/key ordering may differ because comparison is of
   objects; the exact original byte hash is always preserved.
2. Checks schema 2, producer name, caller release/BDF/path, fixed driver,
   references, limitations, sources, bounds and the permitted field sets.
3. Strictly decodes canonical base64 for every observation and recomputes its
   SHA256 and byte count. It checks exact metadata types, representation,
   completeness and byte bounds. Complete raw fields must be within the
   producer's input bound; an observed error/overflow prefix is limited to
   `bound + 1` and cannot become a complete input.
4. Supplies those bytes to the **actual** `acquire.acquire()` function through
   injected reader/uname functions, and supplies saved clock integers through
   injected clock functions. The calls consume fixed per-source queues;
   recorded event order or parsed fields do not drive the replay. No native
   reader or clock is called.
5. Requires all complete inputs to be consumed and compares the entire
   regenerated report with the supplied JSON object, including parsed
   identities, names, uptime, clock order, events, references, errors and
   flags. The comparison distinguishes Boolean from integer values, avoiding
   Python's `True == 1` behavior. No declared `complete`, parsed value or digest
   is accepted as a substitute for this replay.

Changing a digest, length, parse, source, limit, event order, identity or flag
without maintaining the producer's actual behavior causes a non-match. The
same producer checks enforce expected release before the cached read,
consistent boot identities, the explicit `amdgpu` BDF and byte-identical DRM
name across the cached read. Producer-defined monotonic/uptime consistency is
checked without deriving latency or any timing qualification. A recorded
wall-clock adjustment remains allowed by that producer and creates no timing
claim.

## Full boot policy and cached-state meaning

The expected bootargs must be the complete caller-pinned policy, not merely
the two added gates. This profile requires one final LF, printable ASCII,
single spaces, no empty tokens, quotes, backslashes or `--`, and no duplicate
argument keys after hyphen-to-underscore normalization. This intentionally
rejects repeated keys such as two `console=` arguments even if another kernel
configuration could use them; the current proposed policy has unique keys.

It requires the exact canonical tokens:

```
amdgpu.ps4_uvd_live_prepare=1
debugfs=on
```

The frozen observation gate parser checks preparation/old-gate conflicts.
Noncanonical or unknown experimental UVD/map arguments are rejected as well.
The captured cmdline must use the same canonical profile and its **entire
ordered token list** must equal the expected list. Root-device changes,
unrelated additions/removals, order changes, missing or disabled debugfs and
duplicate overrides are therefore visible. The profile does not attempt to
interpret every possible kernel boot parameter.

The exact decoded `cached_status`, `uname_before` and `cmdline` bytes are then
passed to `observation.evaluate()`. Its current frozen contract requires
PREPARED generation at least 1 with GART_ENABLE history. Generation zero/NONE
does not match. This wrapper does not duplicate its geometry, CRC, label or
cached-state implementation.

The report separates:

- `complete_capture_validated`: exact replay of a complete producer report.
- `boot_policy_matched`: complete canonical expected/observed policy match.
- `cache_matched`: the status evaluator's scoped result.
- `matched`: all three above, with no hardware acceptance implied.

Malformed cache text can yield a valid complete capture with
`VALIDATED_COMPLETE_CAPTURE_CACHE_NOT_MATCHED`. Coherent INVALIDATED history
is retained with the observation result `OBSERVED_CACHED_INVALIDATED` and is
not promoted to PREPARED or support.

## Incomplete and error captures

Incomplete reports are not retried or repaired. Available raw fields still
receive bounded base64/hash/length checks. A missing raw field differs from
an empty complete read; a mid-read or overflow prefix stays incomplete.
When all complete inputs and clocks exist, an actual producer replay can
confirm a declared incomplete result, such as name drift. Otherwise the
report is preserved as diagnostic without claiming producer replay or
complete input validation. `raw_inputs` stays empty in either case.

The original saved artifact is identified by `capture_sha256` and
`capture_bytes`; producer error declarations and verified prefix hashes are
retained in the result. Keep the caller's exact input artifact as the source
of all diagnostic bytes. CLI-error envelopes have their own diagnostic result
and never supply capture support. Malformed JSON or inconsistent fields have
distinct non-match outcomes. No error result can be an empty-read success.

## Offline verification

```sh
python3 -B linux-psx/experiments/ps4-uvd-live-prepare/capture_evidence_test.py \
  --report-file lab/uvd-2026-10-20/live-prepare/capture-evidence-tool/attempt-01/macos-01.json
```

Use a fresh receipt filename. The tests construct reports with the real
producer and injected synthetic values, mutate saved reports, and exercise
the real status evaluator. They also block native readers, uname, clock and
file APIs during pure evaluation. The receipt and synthetic example are
offline evidence only. No native Linux acquisition, SSH, hardware or UART
operation is exercised, and the active experiment remains untouched.
