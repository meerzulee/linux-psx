#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reconstruct and verify one saved PS4_UVD_FRAME UART report offline."""
import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import zlib

EXPECTED_SHA256 = "12404191ab945b7478093a852545adfbaa9af44bd32f83ccf24f489e385628df"
VISIBLE_BYTES = 6144
PITCHED_BYTES = 24576
FEEDBACK_BYTES = 2048
DEADLINE_NS = 2_000_000_000
MAX_RAW_BYTES = 64 * 1024 * 1024

# Names/types come from flow.c.inc's saved-state reporting statements.
FIELDS = {
    "PREPARED": "fixture:text handle:u32 width:u32 height:u32 pitch:u32 uv:u32 ring:va work:va bytes:u32 phase_deadline_ns:u64",
    "CORE": "status:hex rbc:hex ib:u32 rp:u32 wp:u32 context:hex fault:hex reset_count:u32 reset:bool guard:guard",
    "PHASE": "index:u32 published:bool completed:bool end_dw:u32 seq:hex initial:hex polls:u32 joint:u32 consecutive:u32 publish_ns:u64 confirmed_ns:u64 end_ns:u64 message_crc:hex changed_words:u32",
    "REC": "phase:u32 last:u32 begin_ns:u64 end_ns:u64 status:hex rp:u32 wp:u32 ib:u32 context:hex fence:hex core:bool history:bool predeadline:bool joint:bool",
    "MEMORY": "bad_region:u32 offset:hex expected:hex actual:hex feedback_crc:hex target_crc:hex history_snapshots:u32 history_reason:u32 first_history_reason:u32 fault_arm:u64 fault_total:u64",
    "PIXELS": "bytes:u32 sha256:sha expected:sha match:bool",
    "FEEDBACK": "offset:u32 hex:chunk",
    "PITCHED": "offset:u32 hex:chunk",
    "END": "result:text error:s32 entered:bool initialized:bool enabled:bool phase:u32 publications:u32 pixels:bool match:bool",
}
SUFFIXES = {
    "PREPARED": "CREATE DECODE DESTROY cumulative_wptr=16,32,48",
    "END": "mappings retained, no DMA quiescence/free/rearm",
}


class ReadbackError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise ReadbackError(code, message)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def crc32(raw):
    # Same conventional IEEE CRC32 as crc32_le(~0U, bytes, n) ^ ~0U.
    return zlib.crc32(raw) & 0xffffffff


@dataclass(frozen=True)
class Record:
    kind: str
    fields: dict
    line: int


@dataclass(frozen=True)
class Capture:
    records: list
    pitched: bytes
    feedback: bytes
    visible: bytes


def value(text, kind, location):
    if kind in ("u32", "u64", "bool", "s32"):
        pattern = r"-?(?:0|[1-9][0-9]*)" if kind == "s32" else r"(?:0|[1-9][0-9]*)"
        require(bool(re.fullmatch(pattern, text)), "NUMBER", location + ": malformed decimal")
        number = int(text)
        low, high = {"u32": (0, 2**32 - 1), "u64": (0, 2**64 - 1),
                     "bool": (0, 1), "s32": (-2**31, 2**31 - 1)}[kind]
        require(low <= number <= high, "NUMBER", location + ": value outside printed type")
        return number
    if kind in ("hex", "va", "sha", "chunk"):
        pattern = {"hex": r"[0-9a-f]{8}", "va": r"[0-9a-f]{1,16}",
                   "sha": r"[0-9a-f]{64}", "chunk": r"[0-9a-f]{64}"}[kind]
        require(bool(re.fullmatch(pattern, text)), "HEX", location + ": malformed hex/length")
        return int(text, 16) if kind in ("hex", "va") else text
    if kind == "guard":
        parts = text.split("/")
        require(len(parts) == 4, "GUARD", location + ": incomplete guard tuple")
        return [value(part, "u32" if index == 0 else "hex", location)
                for index, part in enumerate(parts)]
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]+", text)), "TEXT", location + ": malformed token")
    return text


def parse_records(raw):
    require(bool(raw), "EMPTY_RAW", "raw excerpt is empty")
    records = []
    marker = b"PS4_UVD_FRAME"
    for line_number, line in enumerate(raw.splitlines(), 1):
        if marker not in line:
            continue
        require(line.count(marker) == 1, "MARKER", f"line {line_number}: merged frame markers")
        payload = line.split(marker, 1)[1]
        require(payload.startswith(b" "), "MARKER", f"line {line_number}: malformed frame marker")
        try:
            payload = payload.decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise ReadbackError("ASCII", f"line {line_number}: non-ASCII frame record") from error
        head, separator, suffix = payload.partition(";")
        tokens = head.split()
        require(bool(tokens) and tokens[0] in FIELDS, "RECORD_TYPE", f"line {line_number}: unknown/truncated record")
        kind = tokens[0]
        expected_suffix = SUFFIXES.get(kind)
        require((separator and suffix.strip() == expected_suffix) if expected_suffix else not separator,
                "SUFFIX", f"line {line_number}: missing/unexpected report suffix")
        fields = {}
        schema = dict(part.split(":") for part in FIELDS[kind].split())
        for token in tokens[1:]:
            key, equals, text = token.partition("=")
            require(equals and key in schema and key not in fields, "FIELDS",
                    f"line {line_number}: unknown/duplicate/malformed field {key!r}")
            fields[key] = value(text, schema[key], f"line {line_number} {kind}.{key}")
        require(set(fields) == set(schema), "FIELDS", f"line {line_number}: missing {kind} fields")
        records.append(Record(kind, fields, line_number))
    require(bool(records), "NO_FRAME", "no PS4_UVD_FRAME records found")
    counts = Counter(record.kind for record in records)
    for name in ("PREPARED", "CORE", "MEMORY", "PIXELS", "END"):
        require(counts[name] == 1, "RECORD_COUNT", f"require exactly one {name}, found {counts[name]}")
    require(records[0].kind == "PREPARED" and records[-1].kind == "END", "FRAME_BOUNDARY",
            "frame records outside the single PREPARED-to-END interval")
    return records


def reconstruct(records):
    buffers = {}
    for kind, size in (("PITCHED", PITCHED_BYTES), ("FEEDBACK", FEEDBACK_BYTES)):
        chunks = {}
        for record in records:
            if record.kind != kind:
                continue
            offset = record.fields["offset"]
            require(offset % 32 == 0 and offset + 32 <= size, "CHUNK_RANGE",
                    f"{kind} line {record.line}: extra/out-of-range or unaligned chunk")
            require(offset not in chunks, "CHUNK_DUPLICATE", f"{kind}: duplicate offset {offset}")
            chunks[offset] = bytes.fromhex(record.fields["hex"])
        require(set(chunks) == set(range(0, size, 32)), "CHUNK_GAP",
                f"{kind}: incomplete {size}-byte coverage")
        buffers[kind] = b"".join(chunks[offset] for offset in range(0, size, 32))
    pitched = buffers["PITCHED"]
    visible = b"".join(pitched[row * 256:row * 256 + 64] for row in range(64))
    visible += b"".join(pitched[0x4000 + row * 256:0x4000 + row * 256 + 64] for row in range(32))
    require(len(visible) == VISIBLE_BYTES, "VISIBLE_SIZE", "internal NV12 gather size mismatch")
    return Capture(records, pitched, buffers["FEEDBACK"], visible)


def verify(capture, expected):
    records = capture.records
    singles = {record.kind: record.fields for record in records
               if record.kind in ("PREPARED", "CORE", "MEMORY", "PIXELS", "END")}
    prepared, core, memory, pixels, end = (singles[name] for name in
                                          ("PREPARED", "CORE", "MEMORY", "PIXELS", "END"))
    require(prepared["fixture"] == "h264-cb-idr-64x64-v1" and
            [prepared[name] for name in ("handle", "width", "height", "pitch", "uv", "bytes", "phase_deadline_ns")]
            == [1, 64, 64, 256, 16384, 262144, DEADLINE_NS], "PREPARED", "unexpected fixed-frame preparation")
    require(prepared["ring"] % 4096 == 0 and prepared["work"] % 4096 == 0 and
            not (prepared["ring"] < prepared["work"] + 262144 and
                 prepared["work"] < prepared["ring"] + 4096), "PREPARED_RANGE", "prepared ring/work alignment or overlap")
    require(end["result"] == "FRAME_REFERENCE_MATCH" and end["error"] == 0 and
            [end[name] for name in ("entered", "initialized", "enabled", "phase", "publications", "pixels", "match")]
            == [1, 1, 1, 3, 3, 1, 1], "END", "END does not report a complete three-phase frame reference match")
    phase_records = [record for record in records if record.kind == "PHASE"]
    require(len(phase_records) == 3 and [record.fields["index"] for record in phase_records] == [0, 1, 2],
            "PHASES", "require one ordered PHASE for each index 0..2")
    phases = [record.fields for record in phase_records]
    require(len({phase["seq"] for phase in phases}) == 3, "SEQUENCES", "phase sequences must be distinct")
    observations = [record for record in records if record.kind == "REC"]
    require(len(observations) == 6 and
            [(record.fields["phase"], record.fields["last"]) for record in observations]
            == [(phase, last) for phase in range(3) for last in range(2)],
            "OBSERVATIONS", "require exactly the last two ordered observations per phase")
    for index, phase in enumerate(phases):
        require(phase["published"] == phase["completed"] == 1 and phase["end_dw"] == 16 * (index + 1),
                "PHASE_COMPLETE", f"phase {index}: publication/completion/end_dw mismatch")
        require(phase["seq"] != 0 and phase["initial"] != phase["seq"] and
                2 <= phase["consecutive"] <= phase["joint"] <= phase["polls"] <= 2048,
                "PHASE_CONFIRMATIONS", f"phase {index}: insufficient/inconsistent independent confirmations")
        require(0 <= phase["publish_ns"] <= phase["confirmed_ns"] == phase["end_ns"] < DEADLINE_NS,
                "PHASE_DEADLINE", f"phase {index}: completion is outside the strict deadline")
        require(phase["changed_words"] <= 1024, "MESSAGE_DELTA", f"phase {index}: impossible message word count")
        recs = [record.fields for record in observations if record.fields["phase"] == index]
        for rec in recs:
            require(rec["core"] == rec["history"] == rec["predeadline"] == rec["joint"] == 1,
                    "OBSERVATION_VALID", f"phase {index}: invalid core/history/deadline/joint observation")
            require(rec["rp"] == rec["wp"] == phase["end_dw"] and rec["context"] == rec["fence"] == phase["seq"],
                    "OBSERVATION_JOINT", f"phase {index}: fence/context/pointers do not jointly confirm completion")
            require(phase["publish_ns"] <= rec["begin_ns"] <= rec["end_ns"] < DEADLINE_NS,
                    "OBSERVATION_DEADLINE", f"phase {index}: observation crosses the strict deadline")
        require(recs[0]["end_ns"] <= recs[1]["begin_ns"] and recs[1]["end_ns"] == phase["confirmed_ns"],
                "OBSERVATION_ORDER", f"phase {index}: last observations are not ordered or do not confirm PHASE time")
    require(core["rp"] == core["wp"] == 48 and core["context"] == phases[2]["seq"] and
            core["reset"] == 0 and core["guard"] == [0, 0, 0, 0], "FINAL_CORE", "final core guard/context/pointers mismatch")
    require([memory[name] for name in ("bad_region", "offset", "expected", "actual", "history_reason", "first_history_reason", "fault_total")]
            == [0] * 7 and memory["history_snapshots"] > 0 and memory["fault_arm"] > 0,
            "MEMORY_HISTORY", "bad memory or missing/failed captured fault history")
    require(crc32(capture.pitched) == memory["target_crc"], "PITCHED_CRC", "reconstructed pitched CRC differs from MEMORY")
    require(crc32(capture.feedback) == memory["feedback_crc"], "FEEDBACK_CRC", "reconstructed feedback CRC differs from MEMORY")
    digest = sha256(capture.visible)
    require(pixels["bytes"] == VISIBLE_BYTES and pixels["match"] == 1 and
            pixels["expected"] == EXPECTED_SHA256 and pixels["sha256"] == digest,
            "PIXELS_REPORT", "PIXELS size/match/expected/actual hash is inconsistent with reconstructed NV12")
    require(digest == EXPECTED_SHA256 and capture.visible == expected, "PIXELS_REFERENCE",
            "reconstructed visible NV12 differs from the pinned expected file")
    return {"prepared": prepared, "end": end, "core": core, "phases": phases,
            "observations": [record.fields for record in observations], "memory": memory,
            "pixels": pixels, "record_counts": dict(Counter(record.kind for record in records)),
            "frame_first_line": records[0].line, "frame_last_line": records[-1].line,
            "visible_sha256": digest, "target_crc32": f"{crc32(capture.pitched):08x}",
            "feedback_crc32": f"{crc32(capture.feedback):08x}"}


def private_write(path, raw):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def file_input(path, limit):
    path = Path(path)
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "INPUT_SIZE", f"{path}: exceeds bounded input limit {limit}")
    return raw, {"path": str(path.resolve()), "bytes": len(raw), "sha256": sha256(raw)}


def run(raw_path, expected_path, output, session_context=None, session_record=None):
    output = Path(output)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    receipt = {"schema": 1, "status": "FAIL_OFFLINE_FRAME_READBACK", "offline_only": True,
               "hardware_acceptance": False, "artifacts": {}, "inputs": {},
               "tool_sha256": sha256(Path(__file__).read_bytes()),
               "uart_continuity": {"responsibility": "caller", "verified": False,
                   "requirement_before_acceptance": "Verify the real ps4-uart bounded session completed with continuous capture; attachments are opaque evidence only"}}
    try:
        raw, receipt["inputs"]["raw"] = file_input(raw_path, MAX_RAW_BYTES)
        expected, receipt["inputs"]["expected"] = file_input(expected_path, VISIBLE_BYTES)
        require(len(expected) == VISIBLE_BYTES and sha256(expected) == EXPECTED_SHA256,
                "EXPECTED_REFERENCE", "supplied expected file is not the pinned 6144-byte expected.nv12")
        for name, path in (("session_context", session_context), ("session_record", session_record)):
            if path is not None:
                _, receipt["inputs"][name] = file_input(path, MAX_RAW_BYTES)
        capture = reconstruct(parse_records(raw))
        # Preserve complete reconstructed bytes even when metadata/CRC/pixels
        # later fail; only receipt status describes verification success.
        for name, data in (("pitched.bin", capture.pitched), ("feedback.bin", capture.feedback),
                           ("decoded.nv12", capture.visible)):
            private_write(output / name, data)
            receipt["artifacts"][name] = {"bytes": len(data), "sha256": sha256(data)}
        receipt["checks"] = verify(capture, expected)
        receipt["status"] = "PASS_OFFLINE_FRAME_READBACK"
    except Exception as error:
        receipt["error"] = {"code": getattr(error, "code", type(error).__name__), "message": str(error)}
    private_write(output / "receipt.json", (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True, type=Path, help="saved bounded raw UART excerpt")
    parser.add_argument("--expected", required=True, type=Path, help="actual pinned expected.nv12 file")
    parser.add_argument("--output", required=True, type=Path, help="new private artifact directory")
    parser.add_argument("--session-context", type=Path, help="optional existing context attachment, hashed without interpretation")
    parser.add_argument("--session-record", type=Path, help="optional existing session receipt attachment, hashed without schema assumptions")
    args = parser.parse_args(argv)
    try:
        receipt = run(args.raw, args.expected, args.output, args.session_context, args.session_record)
    except Exception as error:
        print(json.dumps({"status": "OUTPUT_ERROR", "message": str(error), "hardware_acceptance": False}), file=sys.stderr)
        return 2
    print(json.dumps({"status": receipt["status"], "receipt": str((args.output / "receipt.json").resolve()),
                      "hardware_acceptance": False, **({"error": receipt["error"]} if "error" in receipt else {})}))
    return 0 if receipt["status"] == "PASS_OFFLINE_FRAME_READBACK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
