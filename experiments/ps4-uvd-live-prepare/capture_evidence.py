# SPDX-License-Identifier: GPL-2.0-only
"""Pure validator of saved acquisition JSON; never acquire or control a device."""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib.util
import json
from pathlib import Path
import re


def _peer(name):
    # Import only the two known sibling source modules. Their imports perform
    # no acquisition. evaluate() itself never accesses files or live adapters.
    spec = importlib.util.spec_from_file_location(
        "_ps4_capture_evidence_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_acquire = _peer("acquire")
_observation = _peer("observation")
MAX_CAPTURE_BYTES = 512 * 1024
SOURCE_PINS = {
    "acquire.py": "9811aa5b174d58857f733cf458f6ecd264717f8d416d8b9b597b1a0959e1de3c",
    "observation.py": "e6a28755d96292c101bfd5cd0032565075a0f993c09b88b6073281bada46ee2a",
}
STAMPS = ("acquisition_start", "before_cached_status", "after_cached_status", "acquisition_end")
NORMAL_STATUSES = frozenset((_acquire.CAPTURED, "REFUSED_BEFORE_CACHED_STATUS_READ",
                              "INCOMPLETE_CACHED_PREPARATION_ACQUISITION", "INVALID_ACQUISITION_REQUEST"))
RAW_KEYS = frozenset(("base64", "sha256", "bytes", "source_complete", "representation"))
ENTRY_KEYS = frozenset(("source", "limit_bytes", "raw", "parsed", "error", "oversized", "error_prefix_capped"))
MATCH = "MATCHED_SAVED_PREPARATION_CAPTURE"
LIMITATIONS = (
    "Internal replay consistency does not prove that acquisition actually occurred or that saved bytes are authentic or fresh.",
    "No hardware acceptance, normal-userspace, kernel-binary, current-page-table or timing claim is made.",
    "Clock values and identity strings are caller-saved evidence; replay verifies only their producer-defined consistency.",
    "The cached status remains historical state, even when the independent observation evaluator matches it.",
    "Incomplete or error captures remain diagnostic; missing or partial reads are never promoted to complete input.",
    "Source pins are checked by the offline test/review chain; evaluate() performs no filesystem verification or acquisition.",
)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value):
    # JSON spelling distinguishes bool from int, unlike Python's True == 1.
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key[:128])
        result[key] = value
    return result


def _not_number(value):
    raise ValueError("non-integer/nonfinite JSON number: " + value[:128])


def _integer(value):
    if len(value) > 64:
        raise ValueError("JSON integer exceeds bounded spelling")
    return int(value)


def _specs(status_path):
    name_path = status_path.rsplit("/", 1)[0] + "/name"
    return {
        **{"uname_" + when: ("uname(2).release", _acquire.UNAME_LIMIT_BYTES)
           for when in ("before", "before_status", "after")},
        **{"boot_id_" + when: (_acquire.BOOT_ID, _acquire.LIMITS_BYTES[_acquire.BOOT_ID])
           for when in ("before", "before_status", "after")},
        **{"uptime_" + when: (_acquire.UPTIME, _acquire.LIMITS_BYTES[_acquire.UPTIME])
           for when in ("before", "after")},
        **{"drm_name_" + when: (name_path, _acquire.NAME_LIMIT_BYTES) for when in ("before", "after")},
        "cmdline": (_acquire.CMDLINE, _acquire.LIMITS_BYTES[_acquire.CMDLINE]),
        "cached_status": (status_path, _acquire.STATUS_LIMIT_BYTES),
    }


def _boot_policy(raw, label):
    result = {"valid": False, "sha256": _sha(raw), "bytes": len(raw), "tokens": [], "issues": []}
    issues = result["issues"]
    if (len(raw) > _observation.MAX_INPUT_BYTES or not raw.endswith(b"\n") or
            raw.count(b"\n") != 1 or not raw[:-1] or
            any(byte < 32 or byte > 126 for byte in raw[:-1])):
        issues.append({"code": "NONCANONICAL_BOOTARGS", "input": label})
        return result
    tokens = raw[:-1].decode("ascii").split(" ")
    result["tokens"] = tokens
    if any(not token or any(char in token for char in "\\\"'") for token in tokens) or "--" in tokens:
        issues.append({"code": "AMBIGUOUS_BOOTARGS_SYNTAX", "input": label})
    seen = set()
    for token in tokens:
        key = token.partition("=")[0]
        normalized = key.replace("-", "_")
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*", key):
            issues.append({"code": "NONCANONICAL_ARGUMENT_KEY", "key": key})
        if normalized in seen:
            issues.append({"code": "DUPLICATE_ARGUMENT_KEY", "key": normalized})
        seen.add(normalized)
        experimental = (normalized.startswith(("amdgpu.ps4_uvd_", "ps4_uvd_")) or
                        ".ps4_uvd_" in normalized or normalized in ("amdgpu.ps4_map_canary", "ps4_map_canary"))
        if experimental and token != "amdgpu.ps4_uvd_live_prepare=1":
            issues.append({"code": "NONCANONICAL_EXPERIMENTAL_ARGUMENT", "token": token})
    if tokens.count("debugfs=on") != 1 or any(
            token.partition("=")[0] == "debugfs" and token != "debugfs=on" for token in tokens):
        issues.append({"code": "DEBUGFS_ON_REQUIRED"})
    if tokens.count("amdgpu.ps4_uvd_live_prepare=1") != 1:
        issues.append({"code": "EXACT_PREPARATION_GATE_REQUIRED"})
    # Reuse the frozen evaluator's gate parsing; do not create a second allowlist.
    observation_issues = []
    _observation._cmdline(raw, observation_issues)
    issues.extend({"code": "OBSERVATION_BOOT_GATE_REJECTED", "observation_issue": item}
                  for item in observation_issues)
    result["valid"] = not issues
    return result


def _different_paths(saved, replayed, path="", limit=20):
    if type(saved) is not type(replayed):
        return [path or "$type"]
    if isinstance(saved, dict):
        differences = []
        for key in sorted(set(saved) | set(replayed)):
            child = (path + "." if path else "") + key
            if key not in saved or key not in replayed:
                differences.append(child)
            else:
                differences.extend(_different_paths(saved[key], replayed[key], child, limit))
            if len(differences) >= limit:
                break
        return differences[:limit]
    if isinstance(saved, list):
        if len(saved) != len(replayed):
            return [path + ".length"]
        differences = []
        for index, (left, right) in enumerate(zip(saved, replayed)):
            differences.extend(_different_paths(left, right, f"{path}[{index}]", limit))
            if len(differences) >= limit:
                break
        return differences[:limit]
    return [] if saved == replayed else [path]


def _replay(raw, stamps, *, expected_release, expected_pci_bdf, status_path):
    queues = {
        _acquire.BOOT_ID: [raw["boot_id_" + suffix] for suffix in ("before", "before_status", "after")],
        _acquire.CMDLINE: [raw["cmdline"]],
        _acquire.UPTIME: [raw["uptime_before"], raw["uptime_after"]],
        status_path.rsplit("/", 1)[0] + "/name": [raw["drm_name_before"], raw["drm_name_after"]],
        status_path: [raw["cached_status"]],
        "uname": [raw["uname_" + suffix] for suffix in ("before", "before_status", "after")],
        "monotonic": [stamps[name]["monotonic_ns"] for name in STAMPS],
        "wall": [stamps[name]["wall_time_ns"] for name in STAMPS],
    }

    def take(name):
        if name not in queues or not queues[name]:
            raise ValueError("replay requested an absent observation")
        return queues[name].pop(0)

    def reader(path, bound):
        if bound != _acquire._input_limit(path):
            raise ValueError("replay requested a different bound")
        return take(path)

    result = _acquire.acquire(
        expected_release=expected_release, expected_pci_bdf=expected_pci_bdf, status_path=status_path,
        reader=reader, uname=lambda: take("uname"), monotonic_ns=lambda: take("monotonic"),
        wall_time_ns=lambda: take("wall"))
    return result, not any(queues.values())


def evaluate(capture_raw: bytes, *, expected_release: str, expected_pci_bdf: str,
             status_path: str, expected_bootargs: bytes) -> dict:
    """Validate supplied bytes only; return {'report': dict, 'raw_inputs': dict}.

    raw_inputs contains all 12 decoded observations only after exact complete
    producer replay. Otherwise it is empty. The original supplied capture is
    identified by SHA256 and remains the caller's saved diagnostic artifact.
    """
    if not isinstance(capture_raw, bytes) or not isinstance(expected_bootargs, bytes):
        raise TypeError("capture_raw and expected_bootargs must be exact bytes")
    if any(not isinstance(value, str) for value in (expected_release, expected_pci_bdf, status_path)):
        raise TypeError("expected release, PCI BDF and status path must be strings")
    report = {
        "schema": 1, "status": "UNVALIDATED_SAVED_CAPTURE", "matched": False,
        "complete_capture_validated": False, "producer_replay_matched": False,
        "cache_matched": False, "boot_policy_matched": False, "hardware_acceptance": False,
        "normal_userspace_established": False, "acquisition_freshness_established": False,
        "kernel_binary_attested": False, "timing_claim": False,
        "capture_sha256": _sha(capture_raw), "capture_bytes": len(capture_raw),
        "expected_release": expected_release, "expected_pci_bdf": expected_pci_bdf,
        "status_path": status_path, "source_pins": dict(SOURCE_PINS),
        "raw_observation_checks": {}, "producer_diagnostic": None, "identity": None,
        "observation_evaluation": None, "issues": [], "limitations": list(LIMITATIONS),
        "expected_boot_policy": _boot_policy(expected_bootargs, "expected_bootargs"),
        "observed_boot_policy": None,
    }
    output = {"report": report, "raw_inputs": {}}

    def issue(phase, code, **details):
        report["issues"].append({"phase": phase, "code": code, **details})

    for value, pattern, name in ((expected_release, _acquire.RELEASE_PATTERN, "expected_release"),
                                 (expected_pci_bdf, _acquire.PCI_BDF_PATTERN, "expected_pci_bdf"),
                                 (status_path, _acquire.STATUS_PATTERN, "status_path")):
        if not re.fullmatch(pattern, value):
            issue("expectation", "INVALID_CALLER_PIN", input=name)
    if report["issues"]:
        report["status"] = "INVALID_CALLER_EXPECTATION"
        return output
    if len(capture_raw) > MAX_CAPTURE_BYTES:
        issue("json", "CAPTURE_TOO_LARGE", bound_bytes=MAX_CAPTURE_BYTES)
        report["status"] = "MALFORMED_CAPTURE_JSON"
        return output
    try:
        saved = json.loads(capture_raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_float=_not_number, parse_constant=_not_number, parse_int=_integer)
        if not isinstance(saved, dict):
            raise ValueError("capture JSON must be one object")
    except (ValueError, UnicodeError, RecursionError) as error:
        issue("json", "INVALID_CAPTURE_JSON", detail=str(error)[:512])
        report["status"] = "MALFORMED_CAPTURE_JSON"
        return output
    report["producer_diagnostic"] = {
        "declared_status": saved.get("status"), "declared_complete": saved.get("complete"),
        "declared_issues": saved.get("issues"), "exception": saved.get("exception"),
        "detail": saved.get("detail"), "interpretation": "Untrusted saved declarations until replay; never hardware evidence",
    }
    if saved.get("status") == "ACQUISITION_CLI_ERROR":
        wanted = {"schema", "status", "complete", "hardware_acceptance", "exception", "detail"}
        good = (set(saved) == wanted and type(saved.get("schema")) is int and saved["schema"] == 2
                and saved.get("complete") is False and saved.get("hardware_acceptance") is False
                and isinstance(saved.get("exception"), str) and len(saved["exception"]) <= 128
                and isinstance(saved.get("detail"), str) and len(saved["detail"]) <= 512)
        if not good:
            issue("capture", "INVALID_CLI_ERROR_ENVELOPE")
        report["status"] = "DIAGNOSTIC_ACQUISITION_ERROR" if good else "CAPTURE_FORMAT_MISMATCH"
        return output
    fixed = {
        "schema": 2, "producer": "ps4-uvd-cached-preparation-acquisition",
        "expected_release": expected_release, "expected_pci_bdf": expected_pci_bdf,
        "expected_driver": "amdgpu", "status_path": status_path,
        "drm_name_path": status_path.rsplit("/", 1)[0] + "/name",
        "hardware_acceptance": False, "live_control_performed": False,
        "review_scope_reference": _acquire.COMPILED_REVIEW,
        "review_scope_reference_sha256": _acquire.COMPILED_REVIEW_SHA256,
        "drm_name_formatter_reference": _acquire.DRM_NAME_FORMATTER_REFERENCE,
        "drm_name_source_sha256": _acquire.DRM_NAME_SOURCE_SHA256,
        "limits": list(_acquire.LIMITATIONS),
    }
    for key, expected in fixed.items():
        if key not in saved or _canonical_json(saved[key]) != _canonical_json(expected):
            issue("capture", "PRODUCER_FIELD_MISMATCH", field=key)
    dynamic = {"status", "complete", "drm_identity_consistent", "cached_status_read_attempted",
               "identity_consistent", "observations", "timestamps", "events", "issues"}
    if not (set(fixed) | dynamic) <= set(saved) or set(saved) - (set(fixed) | dynamic | {"wall_clock_nondecreasing"}):
        issue("capture", "PRODUCER_ENVELOPE_KEYS_DIFFER")
    if (not isinstance(saved.get("status"), str) or saved["status"] not in NORMAL_STATUSES
            or type(saved.get("complete")) is not bool or type(saved.get("cached_status_read_attempted")) is not bool):
        issue("capture", "INVALID_PRODUCER_OUTCOME")
    if (not isinstance(saved.get("events"), list) or not isinstance(saved.get("issues"), list)
            or not isinstance(saved.get("timestamps"), dict)):
        issue("capture", "INVALID_PRODUCER_COLLECTION_FIELDS")
    for key in ("identity_consistent", "drm_identity_consistent", "wall_clock_nondecreasing"):
        if saved.get(key) is not None and type(saved[key]) is not bool:
            issue("capture", "INVALID_PRODUCER_BOOLEAN", field=key)
    specs, decoded, fully_read = _specs(status_path), {}, set()
    entries = saved.get("observations")
    if not isinstance(entries, dict):
        issue("capture", "INVALID_OBSERVATIONS_OBJECT")
        entries = {}
    for name, entry in entries.items():
        if name not in specs or not isinstance(entry, dict):
            issue("capture", "UNKNOWN_OR_INVALID_OBSERVATION", input=name)
            continue
        source, bound = specs[name]
        required = {"source", "limit_bytes", "raw", "parsed"}
        if (not required <= set(entry) or set(entry) - ENTRY_KEYS or entry.get("source") != source
                or type(entry.get("limit_bytes")) is not int or entry["limit_bytes"] != bound):
            issue("capture", "OBSERVATION_ENVELOPE_MISMATCH", input=name)
        metadata = entry.get("raw")
        if metadata is None:
            report["raw_observation_checks"][name] = {"integrity_verified": False, "raw_present": False}
            continue
        try:
            if not isinstance(metadata, dict) or set(metadata) != RAW_KEYS:
                raise ValueError("raw metadata keys differ")
            encoded = metadata["base64"]
            if not isinstance(encoded, str) or len(encoded) > 4 * ((bound + 3) // 3):
                raise ValueError("base64 is not bounded text")
            raw = base64.b64decode(encoded, validate=True)
            if base64.b64encode(raw).decode("ascii") != encoded:
                raise ValueError("base64 is not canonical")
            if type(metadata["bytes"]) is not int or metadata["bytes"] != len(raw):
                raise ValueError("raw byte count differs")
            if metadata["sha256"] != _sha(raw):
                raise ValueError("raw SHA256 differs")
            if type(metadata["source_complete"]) is not bool:
                raise ValueError("source_complete is not Boolean")
            complete = metadata["source_complete"]
            if metadata["representation"] != ("exact_bytes" if complete else "observed_prefix"):
                raise ValueError("raw representation differs")
            if len(raw) > bound + (0 if complete else 1):
                raise ValueError("raw bytes exceed producer bound")
        except (ValueError, TypeError, UnicodeError, binascii.Error) as error:
            issue("raw", "RAW_INTEGRITY_MISMATCH", input=name, detail=str(error)[:256])
            report["raw_observation_checks"][name] = {"integrity_verified": False, "raw_present": True}
            continue
        decoded[name] = raw
        if complete:
            fully_read.add(name)
        report["raw_observation_checks"][name] = {
            "integrity_verified": True, "source": source, "sha256": _sha(raw), "bytes": len(raw),
            "source_complete": complete, "representation": metadata["representation"],
            "producer_error": entry.get("error"),
        }
    stamps = saved.get("timestamps")
    clocks_ready = (isinstance(stamps, dict) and set(stamps) == set(STAMPS)
                    and all(isinstance(stamps[name], dict) and set(stamps[name]) == {"monotonic_ns", "wall_time_ns"}
                            and all(type(value) is int and value >= 0 for value in stamps[name].values())
                            for name in STAMPS))
    replay_ready = fully_read == set(specs) and clocks_ready and not report["issues"]
    if replay_ready:
        replayed, consumed = _replay(decoded, stamps, expected_release=expected_release,
                                     expected_pci_bdf=expected_pci_bdf, status_path=status_path)
        same = _canonical_json(saved) == _canonical_json(replayed)
        report["producer_replay_matched"] = same and consumed
        report["replayed_status"] = replayed["status"]
        report["replayed_report_canonical_sha256"] = _sha(_canonical_json(replayed).encode())
        if not same or not consumed:
            issue("replay", "PRODUCER_REPLAY_MISMATCH", all_inputs_consumed=consumed,
                  different_fields=_different_paths(saved, replayed))
        report["complete_capture_validated"] = same and consumed and replayed["complete"] is True
    elif saved.get("complete") is True or saved.get("status") == _acquire.CAPTURED:
        issue("replay", "COMPLETE_CAPTURE_INPUTS_UNAVAILABLE",
              missing_complete_inputs=sorted(set(specs) - fully_read), complete_clock_set=clocks_ready)
    if report["issues"]:
        report["status"] = "CAPTURE_FORMAT_OR_REPLAY_MISMATCH"
        return output
    if not report["complete_capture_validated"]:
        report["status"] = "DIAGNOSTIC_INCOMPLETE_CAPTURE"
        return output
    output["raw_inputs"] = dict(decoded)
    report["identity"] = {
        "boot_id": replayed["observations"]["boot_id_before"]["parsed"],
        "release": replayed["observations"]["uname_before"]["parsed"],
        "driver": replayed["observations"]["drm_name_before"]["parsed"]["driver"],
        "pci_bdf": replayed["observations"]["drm_name_before"]["parsed"]["pci_bdf"],
        "status_path": status_path,
    }
    observed_policy = _boot_policy(decoded["cmdline"], "captured_cmdline")
    report["observed_boot_policy"] = observed_policy
    report["boot_policy_matched"] = (report["expected_boot_policy"]["valid"] and observed_policy["valid"]
                                     and report["expected_boot_policy"]["tokens"] == observed_policy["tokens"])
    observation = _observation.evaluate(decoded["cached_status"], decoded["uname_before"], decoded["cmdline"],
                                        expected_release=expected_release)
    report["observation_evaluation"] = observation
    report["cache_matched"] = observation["matched"]
    if not report["expected_boot_policy"]["valid"]:
        report["status"] = "INVALID_EXPECTED_BOOT_POLICY"
    elif not report["boot_policy_matched"]:
        report["status"] = "CAPTURE_BOOT_POLICY_MISMATCH"
    elif not report["cache_matched"]:
        report["status"] = "VALIDATED_COMPLETE_CAPTURE_CACHE_NOT_MATCHED"
    else:
        report["status"], report["matched"] = MATCH, True
    return output
