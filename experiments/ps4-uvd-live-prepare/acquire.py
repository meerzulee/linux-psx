#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Capture bounded, read-only preparation evidence; stdout JSON, no controls."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import sys
import time


BOOT_ID = "/proc/sys/kernel/random/boot_id"
CMDLINE = "/proc/cmdline"
UPTIME = "/proc/uptime"
STATUS_PATTERN = r"/sys/kernel/debug/dri/(?:0|[1-9][0-9]{0,9})/amdgpu_ps4_live_prepare"
NAME_PATTERN = r"/sys/kernel/debug/dri/(?:0|[1-9][0-9]{0,9})/name"
PCI_BDF_PATTERN = r"[0-9a-f]{4}:[0-9a-f]{2}:[01][0-9a-f]\.[0-7]"
RELEASE_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}"
LIMITS_BYTES = {BOOT_ID: 64, CMDLINE: 65536, UPTIME: 128}
STATUS_LIMIT_BYTES = 65536
NAME_LIMIT_BYTES = 4096
UNAME_LIMIT_BYTES = 128
CAPTURED = "CAPTURED_CACHED_PREPARATION_INPUTS"
COMPILED_REVIEW = "lab/uvd-2026-10-20/live-prepare/compiled-audit/compiled-review.json"
COMPILED_REVIEW_SHA256 = "13e4837544a6ea7faade1bf932d7da2f739c1909df1fb3cdb3f88adf649c09b4"
DRM_NAME_FORMATTER_REFERENCE = "lab/uvd-2026-10-20/live-prepare/review/DRM-NAME-FORMATTER.json"
DRM_NAME_SOURCE_SHA256 = "d0b17791b2536af59e628fcbcf0c06a72b2a494cf22624c79dcb87c6308a2d38"
LIMITATIONS = (
    "This is acquisition evidence, not a PREPARED evaluation or a passed experiment.",
    "Release strings, boot ID and DRM name binding do not attest the running binary or authenticity of the host/device.",
    "The reviewed node reports cached history; reading it does not sample current GPU/UVD registers or validate page tables.",
    "The separate reads and paired clock calls are not an atomic snapshot; changes between observations can go undetected.",
    "Clock brackets describe acquisition calls, not the time of the historical SDMA proof; wall time may be adjusted.",
    "Leaf no-follow and regular-file checks do not authenticate mounts, parent directories, namespaces or the host.",
    "A byte bound and nonblocking open do not impose a wall-clock deadline on a kernel read.",
    "No normal-boot, ordinary GPU-client coexistence, UART continuity, teardown, UVD arm or decoding acceptance follows.",
    "No files are written, permissions changed, filesystems mounted, processes spawned or network connections opened by this producer.",
)


class ReadFailure(Exception):
    """A bounded reader failure with an optional exact observed byte prefix."""

    def __init__(self, code, message, *, partial=None, errno=None):
        super().__init__(message)
        self.code = code
        self.partial = partial
        self.errno = errno


def _allowed_path(path):
    return isinstance(path, str) and (path in LIMITS_BYTES or
                                     re.fullmatch(STATUS_PATTERN, path) is not None or
                                     re.fullmatch(NAME_PATTERN, path) is not None)


def _input_limit(path):
    if path in LIMITS_BYTES:
        return LIMITS_BYTES[path]
    return NAME_LIMIT_BYTES if re.fullmatch(NAME_PATTERN, path) else STATUS_LIMIT_BYTES


def read_bounded(path, limit, *, open_fn=os.open, fstat_fn=os.fstat,
                 read_fn=os.read, close_fn=os.close):
    """Native adapter; injectable OS calls let tests avoid all live files.

    Only the fixed proc files, exact cached-node path or its name sibling are allowed.
    Return through EOF or a limit+1-byte prefix. A zero st_size is supported.
    O_NONBLOCK prevents an accidental FIFO open from blocking before fstat;
    it is not a timeout for regular/debugfs kernel operations.
    """
    if not _allowed_path(path):
        raise ReadFailure("PATH_NOT_ALLOWED", "Not an allowed acquisition input")
    expected_limit = _input_limit(path)
    if type(limit) is not int or limit != expected_limit:
        raise ReadFailure("LIMIT_NOT_ALLOWED", "Input limit must equal its fixed bound")
    required = ("O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")
    if any(not hasattr(os, name) for name in required):
        raise ReadFailure("OPEN_FLAGS_UNAVAILABLE", "Required no-follow open flags unavailable")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    descriptor, failure = None, None
    data = bytearray()
    observed_read = False
    try:
        descriptor = open_fn(path, flags)
        info = fstat_fn(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ReadFailure("NOT_REGULAR_FILE", "Input is not a regular/seq_file node")
        while len(data) <= limit:
            count = min(4096, limit + 1 - len(data))
            block = read_fn(descriptor, count)
            if not isinstance(block, bytes) or len(block) > count:
                raise ReadFailure("READ_CONTRACT_ERROR", "Reader returned invalid bounded bytes")
            observed_read = True
            data.extend(block)
            if not block or len(data) > limit:
                break
    except (OSError, ReadFailure) as error:
        failure = error
    finally:
        if descriptor is not None:
            try:
                close_fn(descriptor)
            except OSError as error:
                if failure is None:
                    failure = error
    if failure is not None:
        raise ReadFailure(
            getattr(failure, "code", "READ_OS_ERROR"), str(failure),
            partial=bytes(data) if observed_read else None,
            errno=getattr(failure, "errno", None),
        ) from failure
    return bytes(data)


def native_uname_release():
    """Return the release bytes from uname(2), without adding a newline."""
    return os.fsencode(os.uname().release)


def _raw_record(raw, *, complete):
    return {"base64": base64.b64encode(raw).decode("ascii"),
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "source_complete": complete,
            "representation": "exact_bytes" if complete else "observed_prefix"}


def _release(raw):
    text = raw.decode("ascii")
    if not re.fullmatch(RELEASE_PATTERN, text):
        raise ValueError("uname release must be one canonical token without a newline")
    return text


def _boot_id(raw):
    text = raw.decode("ascii")
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\n", text):
        raise ValueError("boot ID must be a lowercase UUID followed by one LF")
    value = text[:-1]
    if value == "00000000-0000-0000-0000-000000000000":
        raise ValueError("zero boot ID is not an identity")
    return value


def _uptime(raw):
    text = raw.decode("ascii")
    match = re.fullmatch(r"(0|[1-9][0-9]{0,18})\.([0-9]{2}) (0|[1-9][0-9]{0,18})\.([0-9]{2})\n", text)
    if not match:
        raise ValueError("uptime requires two canonical centisecond values and one LF")
    return {"uptime_centiseconds": int(match[1]) * 100 + int(match[2]),
            "idle_centiseconds": int(match[3]) * 100 + int(match[4])}


def _drm_name(raw):
    text = raw.decode("ascii")
    # drm_name_info emits driver, dev, then optional master/unique text without
    # escaping it. Only the immediate dev= field establishes the PCI identity.
    # The optional suffix is opaque: do not mistake a BDF within it for dev=.
    match = re.fullmatch(
        r"(?P<driver>[A-Za-z0-9_.+\-]{1,128}) dev=(?P<pci_bdf>" + PCI_BDF_PATTERN +
        r")(?P<optional_suffix>(?: (?:master|unique)=[\x20-\x7e]*)?)\n", text)
    if not match:
        raise ValueError("DRM name requires driver plus immediate canonical PCI dev= field and one LF")
    return match.groupdict()


def acquire(*, expected_release, expected_pci_bdf, status_path, reader, uname, monotonic_ns,
            wall_time_ns):
    """Acquire with mandatory injected dependencies; no implicit live access.

    reader(path, limit) returns bytes through EOF, or at most limit+1 bytes to
    signal oversize; it may raise ReadFailure with an observed prefix. uname()
    returns raw release bytes without a newline. Clock functions return int ns.
    The CLI supplies the native adapters only after argument parsing.
    """
    if not all(isinstance(value, str) for value in (expected_release, expected_pci_bdf, status_path)):
        raise TypeError("expected_release, expected_pci_bdf and status_path must be strings")
    report = {
        "schema": 2, "producer": "ps4-uvd-cached-preparation-acquisition",
        "status": "INVALID_ACQUISITION_REQUEST", "complete": False,
        "hardware_acceptance": False, "live_control_performed": False,
        "expected_release": expected_release, "status_path": status_path,
        "expected_pci_bdf": expected_pci_bdf, "expected_driver": "amdgpu",
        "drm_name_path": None, "drm_identity_consistent": None,
        "cached_status_read_attempted": False, "identity_consistent": None,
        "observations": {}, "timestamps": {}, "events": [], "issues": [],
        "review_scope_reference": COMPILED_REVIEW,
        "review_scope_reference_sha256": COMPILED_REVIEW_SHA256,
        "drm_name_formatter_reference": DRM_NAME_FORMATTER_REFERENCE,
        "drm_name_source_sha256": DRM_NAME_SOURCE_SHA256,
        "limits": list(LIMITATIONS),
    }

    def issue(code, **details):
        report["issues"].append({"code": code, **details})

    if not re.fullmatch(RELEASE_PATTERN, expected_release):
        issue("INVALID_EXPECTED_RELEASE")
    if not re.fullmatch(PCI_BDF_PATTERN, expected_pci_bdf):
        issue("INVALID_EXPECTED_PCI_BDF")
    if not re.fullmatch(STATUS_PATTERN, status_path):
        issue("INVALID_STATUS_PATH")
    if report["issues"]:
        return report
    name_path = status_path.rsplit("/", 1)[0] + "/name"
    report["drm_name_path"] = name_path

    last_monotonic = None

    def stamp(name):
        nonlocal last_monotonic
        report["events"].append({"kind": "clock_bracket", "name": name})
        values = {}
        for label, function in (("monotonic_ns", monotonic_ns), ("wall_time_ns", wall_time_ns)):
            try:
                value = function()
                if type(value) is not int or value < 0:
                    raise ValueError("clock must return a nonnegative integer nanosecond value")
                values[label] = value
            except Exception as error:
                values[label] = None
                issue("CLOCK_FAILED", bracket=name, clock=label,
                      exception=type(error).__name__, detail=str(error)[:512])
        current = values["monotonic_ns"]
        if current is not None:
            if last_monotonic is not None and current < last_monotonic:
                issue("MONOTONIC_CLOCK_REGRESSION", bracket=name)
            last_monotonic = current
        report["timestamps"][name] = values

    def sample(name, source, limit, producer, parser=None):
        report["events"].append({"kind": "read", "name": name, "source": source})
        entry = {"source": source, "limit_bytes": limit, "raw": None, "parsed": None}
        report["observations"][name] = entry
        try:
            raw = producer()
            if not isinstance(raw, bytes):
                raise TypeError("input producer must return bytes")
        except Exception as error:
            partial = getattr(error, "partial", None)
            if isinstance(partial, bytes):
                entry["raw"] = _raw_record(partial[:limit + 1], complete=False)
                entry["error_prefix_capped"] = len(partial) > limit + 1
            entry["error"] = {"code": getattr(error, "code", "INPUT_READ_FAILED"),
                              "exception": type(error).__name__,
                              "errno": getattr(error, "errno", None),
                              "detail": str(error)[:512]}
            issue("INPUT_READ_FAILED", input=name, reader_error=entry["error"])
            return None
        oversized = len(raw) > limit
        entry["raw"] = _raw_record(raw[:limit + 1], complete=not oversized)
        if oversized:
            entry["oversized"] = True
            issue("INPUT_TOO_LARGE", input=name, bound_bytes=limit,
                  observed_prefix_bytes=entry["raw"]["bytes"])
            return None
        if parser is None:
            return raw
        try:
            value = parser(raw)
        except (ValueError, UnicodeError) as error:
            entry["error"] = {"code": "NONCANONICAL_INPUT", "detail": str(error)[:512]}
            issue("NONCANONICAL_INPUT", input=name, detail=str(error)[:512])
            return None
        entry["parsed"] = value
        return value

    def file_sample(name, path, parser=None):
        limit = _input_limit(path)
        return sample(name, path, limit, lambda: reader(path, limit), parser)

    def drm_identity(suffix):
        name = "drm_name_" + suffix
        value = file_sample(name, name_path, _drm_name)
        if value is not None:
            if value["driver"] != "amdgpu":
                issue("DRM_DRIVER_MISMATCH", input=name, expected="amdgpu", observed=value["driver"])
            if value["pci_bdf"] != expected_pci_bdf:
                issue("DRM_PCI_BDF_MISMATCH", input=name,
                      expected=expected_pci_bdf, observed=value["pci_bdf"])
        return value

    def identities(suffix):
        release = sample("uname_" + suffix, "uname(2).release", UNAME_LIMIT_BYTES,
                         uname, _release)
        identity = file_sample("boot_id_" + suffix, BOOT_ID, _boot_id)
        if release is not None and release != expected_release:
            issue("RELEASE_MISMATCH", input="uname_" + suffix,
                  expected=expected_release, observed=release)
        return release, identity

    def finish():
        stamp("acquisition_end")
        report["complete"] = not report["issues"] and report["cached_status_read_attempted"]
        report["status"] = (CAPTURED if report["complete"] else
                            "INCOMPLETE_CACHED_PREPARATION_ACQUISITION" if report["cached_status_read_attempted"] else
                            "REFUSED_BEFORE_CACHED_STATUS_READ")
        stamps = list(report["timestamps"].values())
        walls = [point["wall_time_ns"] for point in stamps]
        report["wall_clock_nondecreasing"] = (
            all(a <= b for a, b in zip(walls, walls[1:])) if all(v is not None for v in walls) else None)
        return report

    stamp("acquisition_start")
    if report["issues"]:
        return finish()
    before = identities("before")
    if report["issues"]:
        return finish()
    file_sample("cmdline", CMDLINE)
    uptime_before = file_sample("uptime_before", UPTIME, _uptime)
    # The second pair makes consistency a prerequisite to touching the node,
    # as well as a postcondition. These sequential reads are still non-atomic.
    pre_status = identities("before_status")
    if pre_status != before:
        report["identity_consistent"] = False
        issue("IDENTITY_CHANGED_BEFORE_STATUS")
    if report["issues"]:
        return finish()
    drm_before = drm_identity("before")
    if report["issues"]:
        return finish()
    stamp("before_cached_status")
    if report["issues"]:
        return finish()
    report["cached_status_read_attempted"] = True
    file_sample("cached_status", status_path)
    stamp("after_cached_status")
    drm_after = drm_identity("after")
    report["drm_identity_consistent"] = (
        drm_after is not None and drm_before == drm_after
        and report["observations"]["drm_name_before"]["raw"] == report["observations"]["drm_name_after"]["raw"])
    if not report["drm_identity_consistent"]:
        issue("DRM_NAME_NOT_CONSISTENT_AFTER_STATUS")
    uptime_after = file_sample("uptime_after", UPTIME, _uptime)
    after = identities("after")
    report["identity_consistent"] = (
        all(item is not None for pair in (before, pre_status, after) for item in pair)
        and before == pre_status == after)
    if not report["identity_consistent"]:
        issue("IDENTITY_NOT_CONSISTENT_AFTER_STATUS")
    if uptime_before is not None and uptime_after is not None:
        if uptime_after["uptime_centiseconds"] < uptime_before["uptime_centiseconds"]:
            issue("UPTIME_REGRESSION")
    return finish()


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None, *, reader=None, uname=None, monotonic_ns=None, wall_time_ns=None,
         output=None):
    """JSON-only CLI; dependency injection is also available for offline tests."""
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--help", action="store_true")
    parser.add_argument("--expected-release", action="append")
    parser.add_argument("--expected-pci-bdf", action="append")
    parser.add_argument("--status-path", action="append")
    try:
        args = parser.parse_args(argv)
        if args.help:
            result = {"schema": 2, "status": "USAGE", "hardware_acceptance": False,
                      "usage": "python3 -B -I acquire.py --expected-release RELEASE --expected-pci-bdf DOMAIN:BUS:SLOT.FUNCTION --status-path /sys/kernel/debug/dri/NUMBER/amdgpu_ps4_live_prepare",
                      "limits": list(LIMITATIONS)}
            code = 0
        else:
            if any(value is None for value in (args.expected_release, args.expected_pci_bdf, args.status_path)):
                raise ValueError("--expected-release, --expected-pci-bdf and --status-path are required")
            if any(len(value) != 1 for value in (args.expected_release, args.expected_pci_bdf, args.status_path)):
                raise ValueError("each required argument must occur exactly once")
            result = acquire(expected_release=args.expected_release[0], expected_pci_bdf=args.expected_pci_bdf[0],
                             status_path=args.status_path[0],
                             reader=read_bounded if reader is None else reader,
                             uname=native_uname_release if uname is None else uname,
                             monotonic_ns=time.monotonic_ns if monotonic_ns is None else monotonic_ns,
                             wall_time_ns=time.time_ns if wall_time_ns is None else wall_time_ns)
            code = 0 if result["complete"] else 2 if result["status"] == "INVALID_ACQUISITION_REQUEST" else 1
    except Exception as error:
        result = {"schema": 2, "status": "ACQUISITION_CLI_ERROR", "complete": False,
                  "hardware_acceptance": False, "exception": type(error).__name__,
                  "detail": str(error)[:512]}
        code = 2
    print(json.dumps(result, sort_keys=True), file=sys.stdout if output is None else output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
