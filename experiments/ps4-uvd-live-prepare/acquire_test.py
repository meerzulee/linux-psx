#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline acquisition contracts: all runtime readers, identities and clocks fake."""

import argparse
import base64
import errno
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("prepare_acquisition", HERE / "acquire.py")
acq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acq)
RELEASE = "7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1"
STATUS = "/sys/kernel/debug/dri/7/amdgpu_ps4_live_prepare"
NAME = "/sys/kernel/debug/dri/7/name"
BDF = "0000:00:01.0"
RAW_NAME = b"amdgpu dev=0000:00:01.0 unique=0000:00:01.0\n"
BOOT = b"f65c99c1-5163-4fef-87cb-a83763f02e55\n"
OTHER_BOOT = b"ce1735b4-43b4-45ba-b8ad-71585eb55554\n"
RAW_STATUS = b"synthetic cached input; intentionally not a PREPARED evaluation\n"


class Inputs:
    def __init__(self):
        self.files = {acq.BOOT_ID: [BOOT, BOOT, BOOT],
                      acq.CMDLINE: [b"debugfs=on amdgpu.ps4_uvd_live_prepare=1\n"],
                      acq.UPTIME: [b"12.34 38.21\n", b"12.35 38.25\n"],
                      NAME: [RAW_NAME, RAW_NAME],
                      STATUS: [RAW_STATUS]}
        self.releases = [RELEASE.encode()] * 3
        self.mono = [100, 200, 300, 400]
        self.wall = [10000, 10100, 10200, 10300]
        self.calls = []

    @staticmethod
    def take(values):
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def reader(self, path, limit):
        self.calls.append(("read", path, limit))
        return self.take(self.files[path])

    def uname(self):
        self.calls.append(("uname",))
        return self.take(self.releases)

    def monotonic(self):
        self.calls.append(("monotonic",))
        return self.take(self.mono)

    def realtime(self):
        self.calls.append(("wall",))
        return self.take(self.wall)

    def kwargs(self):
        return dict(reader=self.reader, uname=self.uname,
                    monotonic_ns=self.monotonic, wall_time_ns=self.realtime)

    def capture(self, **changes):
        return acq.acquire(expected_release=changes.get("expected_release", RELEASE),
                           expected_pci_bdf=changes.get("expected_pci_bdf", BDF),
                           status_path=changes.get("status_path", STATUS), **self.kwargs())


class NativeCalls:
    """A fake fd boundary. Never calls the actual open/fstat/read/close."""
    def __init__(self, payload=b"cached\n", mode=stat.S_IFREG | 0o400, size=0):
        self.payload, self.mode, self.size = payload, mode, size
        self.position = 0
        self.calls = []
        self.open_error = self.stat_error = self.close_error = None
        self.read_error_call = None
        self.read_calls = 0
        self.chunk_size = 4096

    def open(self, path, flags):
        self.calls.append(("open", path, flags))
        if self.open_error:
            raise self.open_error
        return 41

    def fstat(self, fd):
        self.calls.append(("fstat", fd))
        if self.stat_error:
            raise self.stat_error
        return SimpleNamespace(st_mode=self.mode, st_size=self.size)

    def read(self, fd, count):
        self.calls.append(("read", fd, count))
        self.read_calls += 1
        if self.read_calls == self.read_error_call:
            raise OSError(errno.EIO, "synthetic interrupted read")
        block = self.payload[self.position:self.position + min(count, self.chunk_size)]
        self.position += len(block)
        return block

    def close(self, fd):
        self.calls.append(("close", fd))
        if self.close_error:
            raise self.close_error

    def run(self, path=STATUS, limit=acq.STATUS_LIMIT_BYTES):
        return acq.read_bounded(path, limit, open_fn=self.open, fstat_fn=self.fstat,
                                read_fn=self.read, close_fn=self.close)


class AcquisitionTests(unittest.TestCase):
    def assert_raw(self, entry, data, complete=True):
        self.assertEqual(base64.b64decode(entry["raw"]["base64"], validate=True), data)
        self.assertEqual(entry["raw"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(entry["raw"]["bytes"], len(data))
        self.assertIs(entry["raw"]["source_complete"], complete)

    def assert_refused(self, report, inputs):
        self.assertEqual(report["status"], "REFUSED_BEFORE_CACHED_STATUS_READ")
        self.assertFalse(report["complete"])
        self.assertFalse(report["hardware_acceptance"])
        self.assertFalse(report["cached_status_read_attempted"])
        self.assertNotIn(("read", STATUS, acq.STATUS_LIMIT_BYTES), inputs.calls)

    def test_valid_exact_bytes_and_identity_sequence(self):
        data = Inputs()
        result = data.capture()
        self.assertEqual(result["status"], acq.CAPTURED)
        self.assertEqual(result["schema"], 2)
        self.assertTrue(result["complete"])
        self.assertTrue(result["identity_consistent"])
        self.assertTrue(result["drm_identity_consistent"])
        self.assertEqual(result["expected_pci_bdf"], BDF)
        self.assertEqual(result["expected_driver"], "amdgpu")
        self.assertEqual(result["drm_name_path"], NAME)
        self.assert_raw(result["observations"]["drm_name_before"], RAW_NAME)
        self.assert_raw(result["observations"]["drm_name_after"], RAW_NAME)
        self.assertFalse(result["hardware_acceptance"])
        self.assertFalse(result["live_control_performed"])
        self.assertEqual(result["issues"], [])
        self.assert_raw(result["observations"]["cached_status"], RAW_STATUS)
        self.assert_raw(result["observations"]["uname_before"], RELEASE.encode())
        self.assert_raw(result["observations"]["boot_id_after"], BOOT)
        self.assertEqual(result["observations"]["uptime_before"]["parsed"],
                         {"uptime_centiseconds": 1234, "idle_centiseconds": 3821})
        self.assertEqual([event["name"] for event in result["events"]], [
            "acquisition_start", "uname_before", "boot_id_before", "cmdline", "uptime_before",
            "uname_before_status", "boot_id_before_status", "drm_name_before", "before_cached_status", "cached_status",
            "after_cached_status", "drm_name_after", "uptime_after", "uname_after", "boot_id_after", "acquisition_end"])
        self.assertEqual(result["timestamps"]["before_cached_status"],
                         {"monotonic_ns": 200, "wall_time_ns": 10100})
        self.assertEqual(result["timestamps"]["after_cached_status"]["monotonic_ns"], 300)

    def test_payload_is_acquired_not_semantically_accepted(self):
        for payload in (b"", b"\x00\xffmalformed\n", b"stage=RELEASED\n"):
            with self.subTest(payload=payload):
                data = Inputs()
                data.files[STATUS] = [payload]
                result = data.capture()
                self.assertEqual(result["status"], acq.CAPTURED)
                self.assertFalse(result["hardware_acceptance"])
                self.assert_raw(result["observations"]["cached_status"], payload)

    def test_wrong_release_refused_before_status(self):
        data = Inputs()
        data.releases[0] = b"another-release"
        result = data.capture()
        self.assert_refused(result, data)
        self.assert_raw(result["observations"]["uname_before"], b"another-release")
        self.assertNotIn("cmdline", result["observations"])

    def test_identity_changes_before_status_refuse(self):
        for target in ("release", "boot"):
            with self.subTest(target=target):
                data = Inputs()
                if target == "release":
                    data.releases[1] = b"changed-release"
                else:
                    data.files[acq.BOOT_ID][1] = OTHER_BOOT
                result = data.capture()
                self.assert_refused(result, data)
                self.assertFalse(result["identity_consistent"])

    def test_identity_changes_after_status_incomplete(self):
        for target in ("release", "boot"):
            with self.subTest(target=target):
                data = Inputs()
                if target == "release":
                    data.releases[2] = b"changed-release"
                else:
                    data.files[acq.BOOT_ID][2] = OTHER_BOOT
                result = data.capture()
                self.assertEqual(result["status"], "INCOMPLETE_CACHED_PREPARATION_ACQUISITION")
                self.assertFalse(result["identity_consistent"])
                self.assert_raw(result["observations"]["cached_status"], RAW_STATUS)

    def test_missing_and_denied_status_still_collect_post_identity(self):
        for code in (errno.ENOENT, errno.EACCES, errno.ENODEV):
            with self.subTest(code=code):
                data = Inputs()
                data.files[STATUS] = [OSError(code, "synthetic unavailable node")]
                result = data.capture()
                self.assertEqual(result["status"], "INCOMPLETE_CACHED_PREPARATION_ACQUISITION")
                self.assertTrue(result["identity_consistent"])
                self.assertEqual(result["observations"]["cached_status"]["error"]["errno"], code)
                self.assertIsNone(result["observations"]["cached_status"]["raw"])
                self.assert_raw(result["observations"]["boot_id_after"], BOOT)

    def test_partial_error_preserves_bytes(self):
        data = Inputs()
        data.files[STATUS] = [acq.ReadFailure("READ_OS_ERROR", "synthetic EIO",
                                             partial=b"partial\x00\xff", errno=errno.EIO)]
        result = data.capture()
        self.assertFalse(result["complete"])
        self.assert_raw(result["observations"]["cached_status"], b"partial\x00\xff", False)
        self.assertFalse(result["observations"]["cached_status"]["error_prefix_capped"])

    def test_oversize_status_has_only_bounded_prefix(self):
        data = Inputs()
        data.files[STATUS] = [b"a" * (acq.STATUS_LIMIT_BYTES + 200)]
        result = data.capture()
        self.assertFalse(result["complete"])
        self.assert_raw(result["observations"]["cached_status"],
                        b"a" * (acq.STATUS_LIMIT_BYTES + 1), False)
        issue = next(row for row in result["issues"] if row["code"] == "INPUT_TOO_LARGE")
        self.assertEqual(issue["observed_prefix_bytes"], acq.STATUS_LIMIT_BYTES + 1)

    def test_oversize_error_prefix_is_capped(self):
        data = Inputs()
        data.files[STATUS] = [acq.ReadFailure("READ_OS_ERROR", "fake", partial=b"x" * 70000)]
        result = data.capture()
        self.assert_raw(result["observations"]["cached_status"], b"x" * 65537, False)
        self.assertTrue(result["observations"]["cached_status"]["error_prefix_capped"])

    def test_noncanonical_or_oversize_uname_is_refused(self):
        for payload in (b"", RELEASE.encode() + b"\n", b"two releases", b"\xff", b"a" * 129, b"name\x00"):
            with self.subTest(payload=payload):
                data = Inputs()
                data.releases[0] = payload
                result = data.capture()
                self.assert_refused(result, data)
                self.assert_raw(result["observations"]["uname_before"], payload[:129], len(payload) <= 128)

    def test_noncanonical_or_oversize_boot_id_is_refused(self):
        for payload in (b"", BOOT[:-1], BOOT.upper(), BOOT + b"\n", b"0" * 65,
                        b"00000000-0000-0000-0000-000000000000\n", BOOT.replace(b"-", b"/", 1)):
            with self.subTest(payload=payload):
                data = Inputs()
                data.files[acq.BOOT_ID][0] = payload
                result = data.capture()
                self.assert_refused(result, data)

    def test_after_identity_read_failure_is_incomplete(self):
        data = Inputs()
        data.files[acq.BOOT_ID][2] = PermissionError(errno.EACCES, "fake")
        result = data.capture()
        self.assertFalse(result["complete"])
        self.assertFalse(result["identity_consistent"])
        self.assertIn("uname_after", result["observations"])

    def test_missing_or_oversize_cmdline_refuses_status(self):
        for value in (OSError(errno.ENOENT, "fake"), b"x" * 65537):
            with self.subTest(value=type(value).__name__):
                data = Inputs()
                data.files[acq.CMDLINE] = [value]
                result = data.capture()
                self.assert_refused(result, data)

    def test_cmdline_content_is_not_gate_evaluation(self):
        data = Inputs()
        data.files[acq.CMDLINE] = [b"anything\x00\xff"]
        result = data.capture()
        self.assertEqual(result["status"], acq.CAPTURED)
        self.assert_raw(result["observations"]["cmdline"], b"anything\x00\xff")

    def test_unsafe_path_has_no_reads_or_clock_calls(self):
        paths = ("/sys/kernel/debug/dri/0/amdgpu_regs", "/sys/kernel/debug/dri/0/../amdgpu_ps4_live_prepare",
                 "/sys/kernel/debug/dri/0/amdgpu_ps4_live_prepare/", STATUS + "\n",
                 "/sys/kernel/debug/dri/00/amdgpu_ps4_live_prepare", "/sys/kernel/debug/dri/-1/amdgpu_ps4_live_prepare",
                 "/sys/kernel/debug/dri/0/1/amdgpu_ps4_live_prepare", "sys/kernel/debug/dri/0/amdgpu_ps4_live_prepare",
                 "/proc/cmdline", "", "/tmp/amdgpu_ps4_live_prepare")
        for path in paths:
            with self.subTest(path=path):
                data = Inputs()
                result = data.capture(status_path=path)
                self.assertEqual(result["status"], "INVALID_ACQUISITION_REQUEST")
                self.assertEqual(data.calls, [])

    def test_invalid_expected_release_has_no_reads(self):
        for release in ("", "a\n", "two tokens", "x" * 129, "é", "--bad"):
            with self.subTest(release=release):
                data = Inputs()
                result = data.capture(expected_release=release)
                self.assertEqual(result["status"], "INVALID_ACQUISITION_REQUEST")
                self.assertEqual(data.calls, [])

    def test_invalid_expected_pci_bdf_has_no_reads(self):
        for bdf in ("", "0000:00:01", "00:01.0", "0000:00:20.0", "0000:00:01.8",
                    "0000:0A:01.0", "0000:00:1.0", "0000:00:01.0\n", "00000:00:01.0"):
            with self.subTest(bdf=bdf):
                data = Inputs()
                result = data.capture(expected_pci_bdf=bdf)
                self.assertEqual(result["status"], "INVALID_ACQUISITION_REQUEST")
                self.assertEqual(data.calls, [])

    def test_native_formatter_reference_is_exactly_the_saved_literal(self):
        evidence = json.loads((HERE.parents[2] / acq.DRM_NAME_FORMATTER_REFERENCE).read_bytes())
        self.assertEqual(evidence["sha256"], acq.DRM_NAME_SOURCE_SHA256)
        formats = re.findall(r'seq_printf\(m, "([^"\n]*)"', evidence["formatter"])
        self.assertEqual(formats, ["%s", " dev=%s", " master=%s", " unique=%s", r"\n"])
        self.assertFalse(evidence["matches_local"])

    def test_name_optional_fields_never_supply_the_identity(self):
        suffixes = (b"", b" master=0000:00:01.0", b" unique=0000:00:01.0",
                    b" master=x unique=y", b" master=", b" unique=",
                    b" master=opaque dev=1111:22:03.4 unique=some text")
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                data = Inputs()
                value = b"amdgpu dev=0000:00:01.0" + suffix + b"\n"
                data.files[NAME] = [value, value]
                result = data.capture()
                self.assertEqual(result["status"], acq.CAPTURED)
                parsed = result["observations"]["drm_name_before"]["parsed"]
                self.assertEqual(parsed["driver"], "amdgpu")
                self.assertEqual(parsed["pci_bdf"], BDF)
                self.assertEqual(parsed["optional_suffix"], suffix.decode())

    def test_wrong_driver_or_bdf_prevents_status_read(self):
        for value in (b"radeon dev=0000:00:01.0\n", b"amdgpu dev=0000:00:02.0\n",
                      b"amdgpu dev=0000:00:02.0 master=0000:00:01.0 unique=0000:00:01.0\n",
                      b"amdgpu dev=0000:00:02.0 master=x dev=0000:00:01.0\n"):
            with self.subTest(value=value):
                data = Inputs()
                data.files[NAME][0] = value
                result = data.capture()
                self.assert_refused(result, data)
                self.assert_raw(result["observations"]["drm_name_before"], value)

    def test_missing_dev_cannot_be_replaced_by_optional_fields(self):
        for value in (b"amdgpu\n", b"amdgpu master=0000:00:01.0\n",
                      b"amdgpu unique=0000:00:01.0\n", b"amdgpu master=x dev=0000:00:01.0\n"):
            with self.subTest(value=value):
                data = Inputs()
                data.files[NAME][0] = value
                self.assert_refused(data.capture(), data)

    def test_noncanonical_drm_name_is_refused(self):
        for value in (b"", RAW_NAME[:-1], RAW_NAME + b"\n", RAW_NAME.replace(b" dev=", b"\tdev="),
                      b"amdgpu dev=0000:0A:01.0\n", b"amdgpu dev=0000:00:20.0\n",
                      b"amdgpu dev=0000:00:01.0 other=x\n", b"amdgpu dev=0000:00:01.0 master=\xff\n",
                      b"amdgpu dev=0000:00:01.0 master=x\x00\n", b"amdgpu dev=0000:00:01.0 master=x\x7f\n"):
            with self.subTest(value=value):
                data = Inputs()
                data.files[NAME][0] = value
                self.assert_refused(data.capture(), data)

    def test_name_missing_denied_or_oversize_before_refuses(self):
        for value in (OSError(errno.ENOENT, "fake"), OSError(errno.EACCES, "fake"), b"x" * 4097):
            with self.subTest(value=type(value).__name__):
                data = Inputs()
                data.files[NAME][0] = value
                result = data.capture()
                self.assert_refused(result, data)

    def test_name_drift_including_optional_suffix_makes_incomplete(self):
        for value in (b"radeon dev=0000:00:01.0 unique=0000:00:01.0\n",
                      b"amdgpu dev=0000:00:02.0 unique=0000:00:01.0\n",
                      b"amdgpu dev=0000:00:01.0 master=changed unique=0000:00:01.0\n",
                      b"amdgpu dev=0000:00:01.0 unique=changed\n"):
            with self.subTest(value=value):
                data = Inputs()
                data.files[NAME][1] = value
                result = data.capture()
                self.assertEqual(result["status"], "INCOMPLETE_CACHED_PREPARATION_ACQUISITION")
                self.assertFalse(result["drm_identity_consistent"])
                self.assertTrue(result["identity_consistent"])
                self.assert_raw(result["observations"]["cached_status"], RAW_STATUS)
                self.assert_raw(result["observations"]["drm_name_after"], value)

    def test_name_missing_denied_or_oversize_after_makes_incomplete(self):
        for value in (OSError(errno.ENOENT, "fake"), OSError(errno.EACCES, "fake"), b"x" * 4097, b"bad\n"):
            with self.subTest(value=type(value).__name__):
                data = Inputs()
                data.files[NAME][1] = value
                result = data.capture()
                self.assertEqual(result["status"], "INCOMPLETE_CACHED_PREPARATION_ACQUISITION")
                self.assertFalse(result["drm_identity_consistent"])
                self.assertTrue(result["identity_consistent"])
                self.assert_raw(result["observations"]["cached_status"], RAW_STATUS)

    def test_core_argument_types(self):
        data = Inputs()
        with self.assertRaises(TypeError):
            data.capture(expected_release=b"bytes")
        self.assertEqual(data.calls, [])

    def test_noncanonical_uptime_before_refuses(self):
        for payload in (b"1.0 2.00\n", b"01.00 2.00\n", b"1.00 2.00", b"1.00\t2.00\n", b"-1.00 2.00\n", b"x" * 129):
            with self.subTest(payload=payload):
                data = Inputs()
                data.files[acq.UPTIME][0] = payload
                self.assert_refused(data.capture(), data)

    def test_after_uptime_error_or_regression_is_incomplete(self):
        for payload in (b"bad\n", b"12.33 40.00\n", OSError(errno.EIO, "fake")):
            with self.subTest(payload=payload):
                data = Inputs()
                data.files[acq.UPTIME][1] = payload
                result = data.capture()
                self.assertFalse(result["complete"])
                self.assertTrue(result["identity_consistent"])

    def test_clock_failure_before_status_refuses(self):
        for payload in (True, -1, 1.5, OSError("fake timer error")):
            with self.subTest(payload=payload):
                data = Inputs()
                data.mono[1] = payload
                result = data.capture()
                self.assert_refused(result, data)

    def test_monotonic_regression_refuses_before_status(self):
        data = Inputs()
        data.mono[1] = 99
        result = data.capture()
        self.assert_refused(result, data)
        self.assertIn("MONOTONIC_CLOCK_REGRESSION", [row["code"] for row in result["issues"]])

    def test_clock_failure_after_status_keeps_capture(self):
        data = Inputs()
        data.wall[2] = OSError("fake timer error")
        result = data.capture()
        self.assertFalse(result["complete"])
        self.assertTrue(result["identity_consistent"])
        self.assert_raw(result["observations"]["cached_status"], RAW_STATUS)

    def test_wall_clock_adjustment_is_recorded_not_hardware_failure(self):
        data = Inputs()
        data.wall[2:] = [10050, 10060]
        result = data.capture()
        self.assertTrue(result["complete"])
        self.assertFalse(result["wall_clock_nondecreasing"])

    def test_reader_contract_error_is_reported(self):
        data = Inputs()
        data.files[STATUS] = ["not bytes"]
        result = data.capture()
        self.assertFalse(result["complete"])
        self.assertEqual(result["observations"]["cached_status"]["error"]["exception"], "TypeError")

    def test_documented_scope_cannot_be_an_acceptance(self):
        result = Inputs().capture()
        self.assertIn("cached history", " ".join(result["limits"]))
        self.assertIn("not an atomic snapshot", " ".join(result["limits"]))
        self.assertIn("wall-clock deadline", " ".join(result["limits"]))
        self.assertEqual(result["review_scope_reference_sha256"],
                         "13e4837544a6ea7faade1bf932d7da2f739c1909df1fb3cdb3f88adf649c09b4")
        self.assertFalse(result["hardware_acceptance"])


class NativeAdapterTests(unittest.TestCase):
    def test_seq_file_size_zero_and_read_only_flags(self):
        fake = NativeCalls(size=0)
        self.assertEqual(fake.run(), b"cached\n")
        self.assertEqual([call[0] for call in fake.calls], ["open", "fstat", "read", "read", "close"])
        flags = fake.calls[0][2]
        self.assertTrue(flags & acq.os.O_NOFOLLOW)
        self.assertTrue(flags & acq.os.O_NONBLOCK)
        self.assertTrue(flags & acq.os.O_CLOEXEC)
        self.assertEqual(flags & acq.os.O_ACCMODE, acq.os.O_RDONLY)
        self.assertEqual(flags & (acq.os.O_CREAT | acq.os.O_TRUNC | acq.os.O_APPEND), 0)

    def test_does_not_trust_st_size(self):
        fake = NativeCalls(payload=b"small", size=10**12)
        self.assertEqual(fake.run(), b"small")

    def test_fixed_proc_paths_and_limits_only(self):
        for path, limit in acq.LIMITS_BYTES.items():
            with self.subTest(path=path):
                fake = NativeCalls(payload=b"value")
                self.assertEqual(fake.run(path, limit), b"value")
        fake = NativeCalls(payload=RAW_NAME)
        self.assertEqual(fake.run(NAME, acq.NAME_LIMIT_BYTES), RAW_NAME)
        for path, limit in (("/etc/passwd", 64), (STATUS, 1), (acq.BOOT_ID, 65),
                            (NAME, 65536), ("/sys/kernel/debug/dri/7/name_extra", 4096),
                            ("/sys/kernel/debug/dri/7/amdgpu_regs", 65536)):
            fake = NativeCalls()
            with self.assertRaises(acq.ReadFailure):
                fake.run(path, limit)
            self.assertEqual(fake.calls, [])

    def test_oversize_reads_stop_at_limit_plus_one(self):
        fake = NativeCalls(payload=b"x" * 100000)
        self.assertEqual(fake.run(), b"x" * 65537)
        self.assertEqual(fake.position, 65537)
        self.assertEqual(fake.calls[-2], ("read", 41, 1))
        self.assertEqual(fake.calls[-1], ("close", 41))

    def test_exact_limit_requires_eof_read(self):
        fake = NativeCalls(payload=b"x" * 65536)
        self.assertEqual(fake.run(), b"x" * 65536)
        self.assertEqual(fake.calls[-2], ("read", 41, 1))

    def test_nonregular_nodes_are_closed_without_read(self):
        for mode in (stat.S_IFDIR, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK, stat.S_IFLNK):
            fake = NativeCalls(mode=mode | 0o400)
            with self.assertRaises(acq.ReadFailure) as caught:
                fake.run()
            self.assertEqual(caught.exception.code, "NOT_REGULAR_FILE")
            self.assertEqual([call[0] for call in fake.calls], ["open", "fstat", "close"])

    def test_open_error_makes_no_read_or_close(self):
        fake = NativeCalls()
        fake.open_error = OSError(errno.ELOOP, "synthetic leaf symlink refusal")
        with self.assertRaises(acq.ReadFailure) as caught:
            fake.run()
        self.assertEqual(caught.exception.errno, errno.ELOOP)
        self.assertIsNone(caught.exception.partial)
        self.assertEqual([call[0] for call in fake.calls], ["open"])

    def test_stat_error_closes_without_read(self):
        fake = NativeCalls()
        fake.stat_error = OSError(errno.EIO, "fake fstat failure")
        with self.assertRaises(acq.ReadFailure):
            fake.run()
        self.assertEqual([call[0] for call in fake.calls], ["open", "fstat", "close"])

    def test_partial_read_error_retains_prefix_and_closes(self):
        fake = NativeCalls(payload=b"part remainder")
        fake.chunk_size = 4
        fake.read_error_call = 2
        with self.assertRaises(acq.ReadFailure) as caught:
            fake.run()
        self.assertEqual(caught.exception.partial, b"part")
        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertEqual(fake.calls[-1], ("close", 41))

    def test_close_error_retains_observed_bytes(self):
        fake = NativeCalls(payload=b"whole")
        fake.close_error = OSError(errno.EIO, "fake close failure")
        with self.assertRaises(acq.ReadFailure) as caught:
            fake.run()
        self.assertEqual(caught.exception.partial, b"whole")


class CliTests(unittest.TestCase):
    def call(self, argv, data=None):
        data = Inputs() if data is None else data
        output, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(acq, "read_bounded", side_effect=AssertionError("native reader called")), \
                mock.patch.object(acq, "native_uname_release", side_effect=AssertionError("native uname called")), \
                mock.patch.object(acq.time, "monotonic_ns", side_effect=AssertionError("native clock called")), \
                mock.patch.object(acq.time, "time_ns", side_effect=AssertionError("native clock called")), \
                mock.patch("builtins.open", side_effect=AssertionError("file operation attempted")), \
                mock.patch.object(sys, "stderr", stderr):
            code = acq.main(argv, output=output, **data.kwargs())
        self.assertEqual(stderr.getvalue(), "")
        return code, json.loads(output.getvalue()), data

    def test_cli_json_only_and_no_live_dependency(self):
        code, result, _ = self.call(["--expected-release", RELEASE, "--expected-pci-bdf", BDF, "--status-path", STATUS])
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], acq.CAPTURED)

    def test_cli_noncomplete_exit_one(self):
        data = Inputs()
        data.files[STATUS] = [PermissionError(errno.EACCES, "fake")]
        code, result, _ = self.call(["--expected-release", RELEASE, "--expected-pci-bdf", BDF, "--status-path", STATUS], data)
        self.assertEqual(code, 1)
        self.assertFalse(result["complete"])

    def test_cli_help_json_and_no_reads(self):
        code, result, data = self.call(["--help"])
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "USAGE")
        self.assertEqual(data.calls, [])

    def test_cli_bad_or_duplicate_arguments_json_and_no_reads(self):
        options = ([], ["--bogus"], ["--expected-release"],
                   ["--expected-release", RELEASE, "--status-path", STATUS],
                   ["--expected-release", RELEASE, "--expected-pci-bdf", BDF, "--status-path", STATUS, "--status-path", STATUS],
                   ["--expected-release", RELEASE, "--expected-release", RELEASE, "--expected-pci-bdf", BDF, "--status-path", STATUS],
                   ["--expected-release", RELEASE, "--expected-pci-bdf", BDF, "--expected-pci-bdf", BDF, "--status-path", STATUS])
        for argv in options:
            with self.subTest(argv=argv):
                code, result, data = self.call(argv)
                self.assertEqual(code, 2)
                self.assertEqual(result["status"], "ACQUISITION_CLI_ERROR")
                self.assertFalse(result["hardware_acceptance"])
                self.assertEqual(data.calls, [])

    def test_cli_unsafe_request_json_and_no_reads(self):
        code, result, data = self.call(["--expected-release", RELEASE, "--expected-pci-bdf", BDF, "--status-path", "/proc/cmdline"])
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "INVALID_ACQUISITION_REQUEST")
        self.assertEqual(data.calls, [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-file", type=Path, required=True)
    args = parser.parse_args()
    report_path = args.report_file
    log_path = report_path.with_suffix(".log")
    sample_path = report_path.with_name(report_path.stem + "-synthetic-capture.json")
    if any(path.exists() for path in (report_path, log_path, sample_path)):
        parser.error("new evidence paths are required")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue()
    receipt = {
        "schema": 1, "status": "PASS_OFFLINE_ACQUISITION_CONTRACTS" if result.wasSuccessful() else "FAIL_OFFLINE_ACQUISITION_CONTRACTS",
        "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "platform": sys.platform, "python": sys.version,
        "producer_sha256": hashlib.sha256((HERE / "acquire.py").read_bytes()).hexdigest(),
        "tests_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "log_sha256": hashlib.sha256(log.encode()).hexdigest(),
        "hardware_acceptance": False, "hardware_actions": False, "subprocesses_run": False,
        "runtime_inputs": "All input readers, uname providers, fd operations and clocks are injected fakes; no live proc/sys reads",
    }
    with log_path.open("x", encoding="utf-8") as output:
        output.write(log)
    with report_path.open("x", encoding="utf-8") as output:
        json.dump(receipt, output, indent=2, sort_keys=True)
        output.write("\n")
    with sample_path.open("x", encoding="utf-8") as output:
        json.dump(Inputs().capture(), output, indent=2, sort_keys=True)
        output.write("\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
