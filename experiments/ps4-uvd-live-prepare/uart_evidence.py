#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Pure comparison of bounded saved UART and preparation cache inputs.

No acquisition, storage operation, session action or acceptance is performed.
The outer handoff must separately bind installation, acquisition and timing.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
from pathlib import Path
import re

_spec = importlib.util.spec_from_file_location(
    "live_prepare_saved_observation", Path(__file__).with_name("observation.py"))
_observation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_observation)

MAX_RAW_BYTES = 16 * 1024 * 1024
BDF = r"[0-9a-f]{4}:[0-9a-f]{2}:[01][0-9a-f]\.[0-7]"
HEX = r"0x(?:0|[1-9a-f][0-9a-f]{0,15})"
DEC = r"(?:0|[1-9][0-9]{0,9})"
STAMP = r"\[\s*(?P<seconds>[0-9]{1,20})\.(?P<fraction>[0-9]{6})\] "
MAP_FIELDS = ("max_pfn", "block", "pd_bytes", "pde", "pt_bytes",
              "src_pte", "dst_pte", "ib_pte")
MAP = re.compile(STAMP + r"amdgpu (?P<device>" + BDF + r"): PS4_MAP_CANARY MAP " +
    " ".join(name + "=(?P<" + name + ">" + (DEC if name == "block" else HEX) + ")"
             for name in MAP_FIELDS))
PREPARED = re.compile(STAMP + r"amdgpu (?P<device>" + BDF +
    r"): PS4_LIVE_PREPARE PREPARED vmid=(?P<vmid>" + DEC + r") pages=(?P<pages>" + DEC +
    r") copy_crc32=(?P<copy_crc32>[0-9a-f]{8}) historical_sdma_proof=1 retained=1 "
    r"UVD_setup=never arm=absent; continuing normal initialization")
BOOT = re.compile(STAMP + r"Linux version (?P<release>[^\s]+) .+")
CMDLINE = re.compile(STAMP + r"Command line: (?P<cmdline>.+)")
KERNEL_CMDLINE = re.compile(STAMP + r"Kernel command line: (?P<cmdline>.+)")
FAULT = re.compile(r"PS4_MAP_CANARY FAIL|\bPARKED\b|Kernel panic|Oops:|"
                   r"BUG: unable to handle|soft lockup|watchdog: BUG:")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _stamp(match):
    return int(match["seconds"]) * 1_000_000_000 + int(match["fraction"]) * 1000


def _instant(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
        return parsed if parsed.utcoffset() is not None else None
    except ValueError:
        return None


def _session(raw, session, experiment, issues):
    def problem(code):
        issues.append({"category": "continuity", "code": code})
    if not isinstance(session, dict):
        problem("SESSION_NOT_OBJECT")
        return {}
    row = session.get("index_record", session)
    if not isinstance(row, dict):
        problem("SESSION_RECORD_NOT_OBJECT")
        return {}
    if row.get("label") != experiment:
        problem("EXPERIMENT_LABEL_MISMATCH")
    if row.get("version") != 2 or row.get("state") != "completed" or row.get("continuity_errors") != []:
        problem("CONTINUITY_NOT_COMPLETED_CLEANLY")
    for key in ("capture_generation", "session_id"):
        if not isinstance(row.get(key), str) or not re.fullmatch(r"[0-9a-f]{32}", row[key]):
            problem("INVALID_" + key.upper())
    if type(row.get("connection_epoch")) is not int or row["connection_epoch"] < 1:
        problem("INVALID_CONNECTION_EPOCH")
    start, end, length = (row.get(key) for key in ("start_offset", "end_offset", "bytes"))
    if (not all(type(v) is int for v in (start, end, length)) or
            not 0 <= start <= end or end - start != length or length != len(raw)):
        problem("RAW_BYTE_FENCES_MISMATCH")
    begun, stopped = (_instant(row.get(key)) for key in ("started_at", "stopped_at"))
    if begun is None or stopped is None or stopped < begun:
        problem("SESSION_TIME_RANGE_INVALID")
    if "artifacts" in session:
        artifact = session.get("artifacts", {}).get("raw", {}) if isinstance(session.get("artifacts"), dict) else {}
        if not isinstance(artifact, dict) or artifact.get("sha256") != _sha(raw) or artifact.get("bytes") != len(raw):
            problem("SAVED_RAW_RECEIPT_MISMATCH")
    return row


def analyze(raw: bytes, session: dict, status_raw: bytes, uname_raw: bytes,
            cmdline_raw: bytes, *, expected_release: str, experiment: str,
            expected_device: str) -> dict:
    """Compare caller-supplied bytes; a positive is correspondence, not a pass."""
    if not isinstance(raw, bytes) or len(raw) > MAX_RAW_BYTES:
        raise ValueError("raw must be bounded saved bytes")
    if not isinstance(experiment, str) or not re.fullmatch(r"EXP-[0-9]{8}-[0-9]{3}", experiment) or experiment.endswith("-000"):
        raise ValueError("explicit experiment identifier required")
    datetime.datetime.strptime(experiment[4:12], "%Y%m%d")
    if not isinstance(expected_device, str) or not re.fullmatch(BDF, expected_device):
        raise ValueError("explicit canonical PCI BDF required")
    cached = _observation.evaluate(status_raw, uname_raw, cmdline_raw,
                                   expected_release=expected_release)
    issues = []
    row = _session(raw, session, experiment, issues)
    records = {name: [] for name in ("boot", "cmdline", "kernel_cmdline", "map", "prepared")}
    faults, other_probe_lines, lifecycle = [], [], []
    specifications = (
        ("Linux version ", "boot", BOOT),
        ("Command line: ", "cmdline", CMDLINE),
        ("Kernel command line: ", "kernel_cmdline", KERNEL_CMDLINE),
        ("PS4_MAP_CANARY MAP ", "map", MAP),
        ("PS4_LIVE_PREPARE PREPARED ", "prepared", PREPARED),
    )
    for number, line_raw in enumerate(raw.splitlines(keepends=True), 1):
        # Transport CRLF is accepted, but other bytes in a relevant line are not
        # stripped into an apparently valid message. Prefix/field order is exact.
        line = line_raw.removesuffix(b"\n").removesuffix(b"\r").decode("ascii", errors="replace")
        if FAULT.search(line):
            faults.append({"line": number, "text": line})
        device_prefix = "amdgpu " + expected_device + ": "
        if device_prefix in line:
            body = line.split(device_prefix, 1)[1]
            # Frozen device.c invalidates cached history before these prints.
            # Retain the evidence even if a prior saved cache still says PREPARED.
            if body.startswith(("GPU reset begin!.", "GPU jobs stop begin!.",
                                "finishing device.", "Emergency reboot.")):
                lifecycle.append({"line": number, "text": line})
            if body.startswith("VM fault ("):
                faults.append({"line": number, "text": line})
        if "PS4_UVD_" in line:
            other_probe_lines.append({"line": number, "text": line})
        for marker, kind, pattern in specifications:
            if not re.search(re.escape(marker.rstrip()) + r"(?=\s|$)", line):
                continue
            # Loader prose is not a kernel command-line observation.
            if kind == "kernel_cmdline" and not line.startswith("["):
                continue
            match = pattern.fullmatch(line)
            if match is None or not line_raw.endswith(b"\n"):
                issues.append({"category": "uart", "code": "MALFORMED_" + kind.upper(), "line": number})
                continue
            fields = match.groupdict()
            result = {"line": number, "timestamp_ns": _stamp(fields)}
            for key, value in fields.items():
                if key in ("seconds", "fraction"):
                    continue
                if key in MAP_FIELDS or key in ("vmid", "pages", "copy_crc32"):
                    result[key] = int(value, 16 if key not in ("block", "vmid", "pages") else 10)
                else:
                    result[key] = value
            records[kind].append(result)

    def issue(code, **details):
        issues.append({"category": "uart", "code": code, **details})
    for kind in records:
        if len(records[kind]) != 1:
            issue("EXPECTED_ONE_" + kind.upper(), observed=len(records[kind]))
    if other_probe_lines:
        issue("UNEXPECTED_OLD_UVD_PROBE_LOG")
    if faults:
        issue("TERMINAL_FAULT_MARKER")
    if lifecycle:
        issue("UART_HISTORY_INVALIDATION_OBSERVED")
    if len(records["boot"]) == 1 and records["boot"][0]["release"] != expected_release:
        issue("UART_RELEASE_MISMATCH")
    identity_cmdline = cached["observations"]["cmdline"]["tokens"]
    for kind in ("cmdline", "kernel_cmdline"):
        if len(records[kind]) == 1 and records[kind][0]["cmdline"].split() != identity_cmdline:
            issue("UART_CACHED_CMDLINE_MISMATCH", record=kind)

    if all(len(v) == 1 for v in records.values()):
        boot, cmd, kcmd, mapping, prepared = (records[k][0] for k in records)
        ordered = (boot, cmd, kcmd, mapping, prepared)
        if any(a["line"] >= b["line"] or a["timestamp_ns"] > b["timestamp_ns"] for a, b in zip(ordered, ordered[1:])):
            issue("UART_OBSERVATION_ORDER")
        if mapping["device"] != expected_device or prepared["device"] != expected_device:
            issue("UART_DEVICE_MISMATCH")
        if {k: prepared[k] for k in ("vmid", "pages", "copy_crc32")} != {
                "vmid": 4, "pages": 812, "copy_crc32": 0x570ae4eb}:
            issue("PREPARED_FIELDS_MISMATCH")
        max_pfn, block = mapping["max_pfn"], mapping["block"]
        # Literal two-level source geometry: ceil(max_pfn/1024)*8, page aligned;
        # cover [0x300000000,0x300400000), PDE index0xc00, one 8KiB PT.
        if block != 10 or not 0x300400 <= max_pfn <= 0xffffffff:
            issue("MAP_SOURCE_GEOMETRY_REJECTED")
        else:
            root_bytes = ((max_pfn + 1023) // 1024) * 8
            pd_bytes = (root_bytes + 4095) & ~4095
            if root_bytes > 0x100000 or mapping["pd_bytes"] != pd_bytes:
                issue("MAP_PD_SIZE_MISMATCH", computed=pd_bytes)
        if mapping["pde"] != 0xc00 or mapping["pt_bytes"] != 0x2000:
            issue("MAP_PT_OR_PDE_MISMATCH")
        ptes = [mapping[key] for key in ("src_pte", "dst_pte", "ib_pte")]
        # Source requires VALID|SYSTEM|READABLE|WRITEABLE and rejects duplicate
        # DMA frames. These printed consistency checks do not prove ownership.
        if any(pte & 0x63 != 0x63 for pte in ptes):
            issue("MAP_PTE_REQUIRED_FLAGS_ABSENT")
        if len({pte & 0x000000FFFFFFF000 for pte in ptes}) != 3:
            issue("MAP_PTE_ADDRESSES_ALIAS")
        fields = cached["observations"]["status_fields"]
        for key in ("pd_bytes", "pt_bytes"):
            if mapping[key] != fields.get(key):
                issue("MAP_CACHED_GEOMETRY_MISMATCH", field=key)
        if prepared["copy_crc32"] != fields.get("copy_crc32") or prepared["pages"] != fields.get("pages"):
            issue("PREPARED_CACHED_PROOF_MISMATCH")

    continuity_ok = not any(p["category"] == "continuity" for p in issues)
    uart_ok = not any(p["category"] == "uart" for p in issues)
    correspondence = continuity_ok and uart_ok and cached["matched"]
    if not continuity_ok:
        status = "INVALID_CONTINUITY"
    elif faults:
        status = "PREPARATION_PARKED_OR_FAILED"
    elif lifecycle:
        status = "UART_PREPARATION_HISTORY_INVALIDATED"
    elif (any(x["code"] in ("UART_RELEASE_MISMATCH", "UART_CACHED_CMDLINE_MISMATCH", "UART_DEVICE_MISMATCH") for x in issues)
          or cached["status"] in ("BUILD_IDENTITY_MISMATCH", "BOOT_GATE_MISMATCH", "OBSERVATION_CONTRACT_MISMATCH")):
        status = "BOOT_IDENTITY_MISMATCH"
    elif not uart_ok:
        status = "UART_OBSERVATION_INCOMPLETE"
    elif cached["status"] == "OBSERVED_CACHED_INVALIDATED":
        f = cached["observations"]["status_fields"]
        valid_invalidation = (f.get("error") == -116 and
            type(f.get("observed_generation")) is int and type(f.get("proof_generation")) is int and
            f["observed_generation"] > f["proof_generation"] >= 1 and
            f.get("last_event") not in (None, "NONE") and f.get("historical_sdma_proof") == 1)
        status = "PREPARATION_HISTORY_INVALIDATED" if valid_invalidation else "PREPARATION_STATE_CONTRADICTION"
    elif cached["status"] != "MALFORMED_SAVED_INPUT" and not cached["matched"]:
        status = "PREPARATION_STATE_CONTRADICTION"
    elif not cached["matched"]:
        status = "CACHED_OBSERVATION_NOT_MATCHED"
    else:
        status = "MATCHED_UART_AND_CACHED_PREPARATION_INPUTS"
    return {"schema": 1, "status": status, "experiment": experiment,
            "expected_release": expected_release, "expected_device": expected_device,
            "raw_sha256": _sha(raw), "raw_bytes": len(raw), "session": row,
            "records": records, "faults": faults, "lifecycle": lifecycle,
            "other_probe_lines": other_probe_lines,
            "cached_evaluation": cached, "issues": issues,
            "correspondence": correspondence, "hardware_acceptance": False,
            "normal_userspace_proven": False, "decode_demonstrated": False,
            "limitations": [
                "Saved input correspondence only; acquisition freshness and installed binary identity require the outer handoff.",
                "Session metadata is checked, not rederived from the continuous logger/event stream.",
                "UART MAP has no PD GPU address; only printed dynamic size/geometry are compared with the cache.",
                "Printed kernel timestamps and cached proof_ns use different reporting paths; no exact clock equivalence is assumed.",
                "Normal userspace, launch deadline, device-name provenance and common boot must be established separately.",
                "The cached proof is history, not a fresh mapping sample, UVD operation or decoder evidence."]}
