#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Evaluate saved preparation status/identity files; never acquire live state."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys

MAX_INPUT_BYTES = 65536
MATCH = "MATCHED_CACHED_PREPARATION_OBSERVATION"
PREPARE_GATE = "amdgpu.ps4_uvd_live_prepare"
OLD_GATES = frozenset("amdgpu." + name for name in (
    "ps4_map_canary", "ps4_uvd_ready", "ps4_uvd_poll_state", "ps4_uvd_token",
    "ps4_uvd_data_control", "ps4_uvd_status_observation", "ps4_uvd_ib_data",
))
# A documented canonical subset of the kernel Boolean spellings, plus bare=true.
# Arbitrary strings sharing a truthy prefix are deliberately not admitted.
TRUE_GATE_VALUES = frozenset(("1", "y", "Y", "t", "T", "true", "True", "TRUE",
                              "on", "On", "oN", "ON"))
STAGES = frozenset(("DISABLED", "SELECTED", "RESERVED", "PREPARING", "PREPARED",
                    "INVALIDATED", "RELEASING", "RELEASED", "FAILED"))
EVENTS = frozenset(("NONE", "GART_ENABLE", "GART_DISABLE", "RESET", "SUSPEND",
                    "HW_FINI", "VMID_FINI", "REINIT"))
RELEASE_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}"

# Field order, types and line groups are the eight literal seq_* format strings.
ROWS = (
    (("schema", "u32"), ("build", "release"), ("interface", "label"), ("arm", "label")),
    (("stage", "stage"), ("error", "s32"), ("selected", "flag"),
     ("attempted", "flag"), ("retained", "flag")),
    (("vmid", "u32"), ("reservation_seen", "flag"),
     ("allocator_alive_cached", "flag"), ("early_fence_init", "flag")),
    (("observed_generation", "u64"), ("proof_generation", "u64"), ("last_event", "event")),
    (("historical_sdma_proof", "flag"), ("proof_ns", "u64"),
     ("reset_counter_at_proof", "u32"), ("copy_crc32", "crc32")),
    (("pd_at_proof", "hex64"), ("pd_bytes", "hex64"),
     ("pt_bytes", "hex64"), ("pages", "u32")),
    (("r1_bytes", "hex64"), ("rb_bytes", "hex64"), ("ib_bytes", "hex64")),
    (("observer", "label"), ("current_hardware_mapping", "label"), ("UVD_setup", "label")),
)
FIELD_TYPES = dict(field for row in ROWS for field in row)
LABELS = {"schema": 1, "interface": "prepare-only", "arm": "absent",
          "observer": "cached-only", "current_hardware_mapping": "not-sampled",
          "UVD_setup": "never"}
FLAGS = ("selected", "attempted", "retained", "reservation_seen",
         "allocator_alive_cached", "early_fence_init", "historical_sdma_proof")
GEOMETRY = {"vmid": 4, "pt_bytes": 0x2000, "r1_bytes": 0x308000,
            "rb_bytes": 0x1000, "ib_bytes": 0x20000, "pages": 812}
PAGE_BYTES = 0x1000
PD_INDEX = 0xC00
PD_MAX_BYTES = 0x100000
EXPECTED_CRC32 = 0x570AE4EB
SOURCE_ANCHORS = {
    "debugfs_template": "linux-psx/experiments/ps4-uvd-live-prepare/debugfs.c.inc",
    "debugfs_template_sha256": "16b9b3770841a845a81fb8e874845bbf1f2793730027d61ef6bb35270dde722d",
    "candidate": "lab/uvd-2026-10-20/live-prepare/source/attempt-06/candidate",
    "map_canary_sha256": "de915ec863a6e46973844a3e9edd2a3b4c03427f99690fc6ac4304d87b0378ac",
    "uvd_ready_sha256": "20ab71d1ca64b20c1f6c4ee258d18abb5c9d50726e518ace0b5c67730b9779ff",
    "crc_reference": "lab/uvd-2026-10-20/live-prepare/handoff/SDMA-PATTERN-REFERENCE.json",
    "crc_pattern": "64 LE u32 words: 0x50533400 XOR ((i * 0x01020409) mod 2^32), i=0..63",
    "crc_bytes_sha256": "e61c071effa22623d279456a2367b26fa114b894bb573af2f542d385415e3715",
    "crc_algorithm": "reflected polynomial 0xedb88320; initial/final XOR 0xffffffff",
}
LIMITS = (
    "Saved bytes do not prove acquisition freshness, authenticity or a common acquisition time.",
    "Matching release strings do not attest the kernel binary or source hash.",
    "PREPARED, the proof address and generations are cached history; current hardware mapping is not sampled.",
    "This result does not prove normal boot, GPU coexistence, UART continuity, teardown or decoding.",
    "The status omits runtime max_pfn and VRAM bounds; PD-size constraints are checked, not exact runtime geometry or address ownership.",
    "No experiment or operator plan is passed and hardware_acceptance remains false.",
)


def _issue(issues, category, code, **details):
    issues.append({"category": category, "code": code, **details})


def _decode(raw, name, issues):
    if len(raw) > MAX_INPUT_BYTES:
        _issue(issues, "malformed", "INPUT_TOO_LARGE", input=name, bytes=len(raw))
        return None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        _issue(issues, "malformed", "NON_ASCII_INPUT", input=name)
        return None
    if "\0" in text or "\r" in text:
        _issue(issues, "malformed", "INVALID_CONTROL_CHARACTER", input=name)
        return None
    return text


def _typed(value, kind):
    if kind in ("u32", "u64", "flag"):
        if not re.fullmatch(r"0|[1-9][0-9]*", value):
            raise ValueError("unsigned decimal spelling")
        number = int(value)
        maximum = 1 if kind == "flag" else (1 << (32 if kind == "u32" else 64)) - 1
        if number > maximum:
            raise ValueError("unsigned range")
        return number
    if kind == "s32":
        if not re.fullmatch(r"0|-?[1-9][0-9]*", value):
            raise ValueError("signed decimal spelling")
        number = int(value)
        if not -(1 << 31) <= number < (1 << 31):
            raise ValueError("signed range")
        return number
    if kind in ("hex64", "crc32"):
        pattern = r"0x(?:0|[1-9a-f][0-9a-f]{0,15})" if kind == "hex64" else r"[0-9a-f]{8}"
        if not re.fullmatch(pattern, value):
            raise ValueError("hexadecimal spelling/width")
        return int(value, 16)
    if kind == "release" and not re.fullmatch(RELEASE_PATTERN, value):
        raise ValueError("release spelling")
    if kind == "stage" and value not in STAGES:
        raise ValueError("unknown stage")
    if kind == "event" and value not in EVENTS:
        raise ValueError("unknown event")
    if kind == "label" and not re.fullmatch(r"[A-Za-z0-9_.+\-]+", value):
        raise ValueError("label spelling")
    return value


def _status(raw, issues):
    text = _decode(raw, "status", issues)
    fields, entries, seen = {}, [], set()
    if text is None:
        return fields, entries
    if not text.endswith("\n"):
        _issue(issues, "malformed", "MISSING_STATUS_FINAL_NEWLINE", input="status")
    lines = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
    if len(lines) != len(ROWS):
        _issue(issues, "malformed", "STATUS_LINE_COUNT", expected=len(ROWS), observed=len(lines))
    for line_number, line in enumerate(lines, 1):
        keys = []
        for token in line.split(" "):
            if token.count("=") != 1:
                _issue(issues, "malformed", "INVALID_FIELD_TOKEN", line=line_number, token=token)
                continue
            key, value = token.split("=", 1)
            entries.append({"line": line_number, "key": key, "raw_value": value})
            keys.append(key)
            if key in seen:
                _issue(issues, "malformed", "DUPLICATE_FIELD", field=key, line=line_number)
                continue
            seen.add(key)
            if key not in FIELD_TYPES:
                _issue(issues, "malformed", "UNKNOWN_FIELD", field=key, line=line_number)
                continue
            try:
                fields[key] = _typed(value, FIELD_TYPES[key])
            except ValueError as error:
                _issue(issues, "malformed", "INVALID_FIELD_TYPE", field=key,
                       raw_value=value, detail=str(error))
        expected = [key for key, _ in ROWS[line_number - 1]] if line_number <= len(ROWS) else []
        if keys != expected:
            _issue(issues, "malformed", "FIELD_ORDER_OR_GROUP", line=line_number,
                   expected=expected, observed=keys)
    for missing in sorted(set(FIELD_TYPES) - seen):
        _issue(issues, "malformed", "MISSING_FIELD", field=missing)
    return fields, entries


def _single_line(raw, name, issues):
    text = _decode(raw, name, issues)
    if text is None:
        return None
    text = text[:-1] if text.endswith("\n") else text
    if not text or "\n" in text or any((ord(char) < 32 and char != "\t") or ord(char) == 127 for char in text):
        _issue(issues, "malformed", "EXPECTED_SINGLE_NONEMPTY_LINE", input=name)
        return None
    return text


def _cmdline(raw, issues):
    text = _single_line(raw, "cmdline", issues)
    result = {"tokens": [], "probe_arguments": [], "preparation_occurrences_before_separator": 0}
    if text is None:
        return result
    # This is a saved-boot-profile checker, not a shell or general kernel lexer.
    # Refuse ambiguous syntax instead of treating shell quoting as kernel syntax.
    if any(char in text for char in "\\\"'"):
        _issue(issues, "malformed", "NONCANONICAL_CMDLINE_SYNTAX", input="cmdline")
        return result
    tokens = text.split()
    result["tokens"] = tokens
    before_separator, preparation = True, []
    for index, token in enumerate(tokens):
        if token == "--":
            before_separator = False
            continue
        name, equal, value = token.partition("=")
        normalized = name.replace("-", "_")
        qualified = normalized if normalized.startswith("amdgpu.") else "amdgpu." + normalized
        if qualified not in OLD_GATES | {PREPARE_GATE}:
            continue
        row = {"index": index, "name": name, "normalized_name": normalized,
               "value": value if equal else None, "before_separator": before_separator}
        result["probe_arguments"].append(row)
        if qualified in OLD_GATES:
            _issue(issues, "gate", "OLD_PROBE_ARGUMENT_PRESENT", **row)
        elif normalized != PREPARE_GATE:
            _issue(issues, "gate", "UNQUALIFIED_PREPARATION_ARGUMENT", **row)
        elif not before_separator:
            _issue(issues, "gate", "PREPARATION_ARGUMENT_AFTER_SEPARATOR", **row)
        else:
            preparation.append(row)
    result["preparation_occurrences_before_separator"] = len(preparation)
    if not preparation:
        _issue(issues, "gate", "PREPARATION_GATE_ABSENT")
    elif len(preparation) != 1:
        _issue(issues, "gate", "DUPLICATE_PREPARATION_GATE", arguments=preparation)
    elif preparation[0]["value"] is not None and preparation[0]["value"] not in TRUE_GATE_VALUES:
        _issue(issues, "gate", "PREPARATION_GATE_NOT_CANONICALLY_ENABLED", **preparation[0])
    return result


def evaluate(status_raw: bytes, uname_raw: bytes, cmdline_raw: bytes, *, expected_release: str) -> dict:
    """Pure API: return a JSON-serializable evaluation of caller-supplied bytes.

    No files are opened, no processes are run, and no source is discovered.
    The expected release must come from the caller's selected build/plan.
    """
    if not isinstance(expected_release, str) or not re.fullmatch(RELEASE_PATTERN, expected_release):
        raise ValueError("expected_release must be one explicit release token")
    inputs = {"status": status_raw, "uname": uname_raw, "cmdline": cmdline_raw}
    if any(not isinstance(raw, bytes) for raw in inputs.values()):
        raise TypeError("saved inputs must be bytes so raw SHA256 preserves the original encoding")
    issues = []
    fields, entries = _status(status_raw, issues)
    uname = _single_line(uname_raw, "uname", issues)
    if uname is not None and not re.fullmatch(RELEASE_PATTERN, uname):
        _issue(issues, "malformed", "INVALID_UNAME_RELEASE", observed=uname)
    cmdline = _cmdline(cmdline_raw, issues)
    for name, value in (("status.build", fields.get("build")), ("uname", uname)):
        if value != expected_release:
            _issue(issues, "identity", "RELEASE_MISMATCH", input=name,
                   expected=expected_release, observed=value)
    for key, expected in LABELS.items():
        if fields.get(key) != expected:
            _issue(issues, "contract", "OBSERVATION_LABEL_MISMATCH", field=key,
                   expected=expected, observed=fields.get(key))
    stage = fields.get("stage")
    if stage != "PREPARED":
        _issue(issues, "stage", "NOT_PREPARED", observed=stage)
    if fields.get("error") != 0:
        _issue(issues, "error", "CACHED_ERROR", observed=fields.get("error"))
    for key in FLAGS:
        if fields.get(key) != 1:
            _issue(issues, "predicate", "REQUIRED_FLAG_NOT_SET", field=key, observed=fields.get(key))
    if not isinstance(fields.get("proof_ns"), int) or fields["proof_ns"] == 0:
        _issue(issues, "predicate", "MISSING_PROOF_TIME", observed=fields.get("proof_ns"))
    if fields.get("reset_counter_at_proof") != 0:
        _issue(issues, "predicate", "RESET_AT_PROOF", observed=fields.get("reset_counter_at_proof"))
    generation, proof_generation = fields.get("observed_generation"), fields.get("proof_generation")
    if generation is None or generation != proof_generation:
        _issue(issues, "generation", "CACHED_GENERATION_DRIFT", observed=generation, proof=proof_generation)
    # In the frozen normal-boot path, live_prepare_init precedes ip_init and
    # GMC hw_init calls gart_enable before the preparation can run. The latter
    # records an event even when it subsequently fails. Zero/NONE therefore
    # cannot describe a completed preparation on this path.
    if (not isinstance(generation, int) or generation < 1 or
            fields.get("last_event") != "GART_ENABLE"):
        _issue(issues, "predicate", "PREPARED_EVENT_HISTORY_INCONSISTENT",
               generation=generation, last_event=fields.get("last_event"))
    for key, expected in GEOMETRY.items():
        if fields.get(key) != expected:
            _issue(issues, "geometry", "FIXED_GEOMETRY_MISMATCH", field=key,
                   expected=expected, observed=fields.get(key))
    pd_bytes, pd = fields.get("pd_bytes"), fields.get("pd_at_proof")
    if (not isinstance(pd_bytes, int) or pd_bytes % PAGE_BYTES or
            pd_bytes < (PD_INDEX + 1) * 8 or pd_bytes > PD_MAX_BYTES):
        _issue(issues, "geometry", "PD_SIZE_OUTSIDE_SOURCE_BOUNDS", observed=pd_bytes)
    if (not isinstance(pd, int) or not pd or pd % PAGE_BYTES or
            (isinstance(pd_bytes, int) and pd > (1 << 64) - 1 - pd_bytes)):
        _issue(issues, "geometry", "PD_ADDRESS_METADATA_INVALID", observed=pd)
    if fields.get("copy_crc32") != EXPECTED_CRC32:
        _issue(issues, "crc", "SDMA_PATTERN_CRC_MISMATCH", expected=f"{EXPECTED_CRC32:08x}",
               observed=f"{fields['copy_crc32']:08x}" if "copy_crc32" in fields else None)
    categories = {issue["category"] for issue in issues}
    if "malformed" in categories:
        outcome = "MALFORMED_SAVED_INPUT"
    elif "identity" in categories:
        outcome = "BUILD_IDENTITY_MISMATCH"
    elif "gate" in categories:
        outcome = "BOOT_GATE_MISMATCH"
    elif "contract" in categories:
        outcome = "OBSERVATION_CONTRACT_MISMATCH"
    elif stage != "PREPARED":
        outcome = "OBSERVED_CACHED_" + stage
    elif "error" in categories:
        outcome = "CACHED_PREPARATION_ERROR"
    elif "generation" in categories:
        outcome = "CACHED_PREPARATION_GENERATION_DRIFT"
    elif "geometry" in categories:
        outcome = "CACHED_PREPARATION_GEOMETRY_MISMATCH"
    elif "crc" in categories:
        outcome = "CACHED_PREPARATION_CRC_MISMATCH"
    elif issues:
        outcome = "CACHED_PREPARATION_PREDICATES_NOT_MET"
    else:
        outcome = MATCH
    return {
        "schema": 1, "status": outcome, "matched": outcome == MATCH,
        "hardware_acceptance": False, "live_acquisition_performed": False,
        "expected_release": expected_release,
        "raw_inputs": {name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
                       for name, raw in inputs.items()},
        "observations": {"status_fields": fields, "raw_status_fields": entries,
                         "uname_release": uname, "cmdline": cmdline},
        "issues": issues, "source_anchors": dict(SOURCE_ANCHORS),
        "expected_copy_crc32": f"{EXPECTED_CRC32:08x}", "limitations": list(LIMITS),
    }


def _explicit_saved_path(path: Path) -> Path:
    if path.is_absolute() and len(path.parts) > 1 and path.parts[1] in ("dev", "proc", "sys"):
        raise ValueError("use a saved regular file, not a live pseudo-filesystem path")
    resolved = path.resolve(strict=False)
    if len(resolved.parts) > 1 and resolved.parts[1] in ("dev", "proc", "sys"):
        raise ValueError("use a saved regular file, not a live pseudo-filesystem path")
    return resolved


def _read_saved(path: Path) -> bytes:
    _explicit_saved_path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("input must be a saved regular file, not a symlink or special file")
    if info.st_size > MAX_INPUT_BYTES:
        raise ValueError("saved input exceeds the bounded input size")
    with path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("saved input grew beyond the bounded input size")
    return raw


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--uname-file", type=Path, required=True)
    parser.add_argument("--cmdline-file", type=Path, required=True)
    parser.add_argument("--expected-release", required=True)
    parser.add_argument("--report", type=Path, required=True, help="new JSON file; parent must exist")
    args = parser.parse_args(argv)
    try:
        _explicit_saved_path(args.report)
        report = evaluate(_read_saved(args.status_file), _read_saved(args.uname_file),
                          _read_saved(args.cmdline_file), expected_release=args.expected_release)
        report["input_files"] = {"status": str(args.status_file), "uname": str(args.uname_file),
                                 "cmdline": str(args.cmdline_file)}
        with args.report.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (OSError, ValueError, TypeError) as error:
        print(f"Saved-input evaluation failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "hardware_acceptance": False,
                      "report": str(args.report)}, sort_keys=True))
    return 0 if report["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
