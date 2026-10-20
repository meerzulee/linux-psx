#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic saved-capture tests; no device, live reader, identity or clock."""

import argparse
import base64
import copy
import errno
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("capture_evidence_under_test", HERE / "capture_evidence.py")
ce = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ce)
acq = ce._acquire
RELEASE = "7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1"
BDF = "0000:00:01.0"
STATUS_PATH = "/sys/kernel/debug/dri/7/amdgpu_ps4_live_prepare"
NAME_PATH = "/sys/kernel/debug/dri/7/name"
BOOT = b"f65c99c1-5163-4fef-87cb-a83763f02e55\n"
OTHER_BOOT = b"ce1735b4-43b4-45ba-b8ad-71585eb55554\n"
NAME = b"amdgpu dev=0000:00:01.0 unique=0000:00:01.0\n"
BOOTARGS = b"root=LABEL=OMARCHY-PS4 rootfstype=ext4 rootwait rw console=null amdgpu.ps4_uvd_live_prepare=1 debugfs=on\n"
STATUS_BYTES = (
    f"schema=1 build={RELEASE} interface=prepare-only arm=absent\n"
    "stage=PREPARED error=0 selected=1 attempted=1 retained=1\n"
    "vmid=4 reservation_seen=1 allocator_alive_cached=1 early_fence_init=1\n"
    "observed_generation=1 proof_generation=1 last_event=GART_ENABLE\n"
    "historical_sdma_proof=1 proof_ns=100000 reset_counter_at_proof=0 copy_crc32=570ae4eb\n"
    "pd_at_proof=0x100000 pd_bytes=0x20000 pt_bytes=0x2000 pages=812\n"
    "r1_bytes=0x308000 rb_bytes=0x1000 ib_bytes=0x20000\n"
    "observer=cached-only current_hardware_mapping=not-sampled UVD_setup=never\n"
).encode()


def make_capture(*, status_bytes=STATUS_BYTES, cmdline=BOOTARGS, files=None,
                 releases=None, monotonic=None, wall=None):
    inputs = {
        acq.BOOT_ID: [BOOT, BOOT, BOOT], acq.CMDLINE: [cmdline],
        acq.UPTIME: [b"12.34 38.21\n", b"12.35 38.25\n"],
        NAME_PATH: [NAME, NAME], STATUS_PATH: [status_bytes],
    }
    if files:
        inputs.update({key: list(value) for key, value in files.items()})
    names = list(releases) if releases is not None else [RELEASE.encode()] * 3
    monos = list(monotonic) if monotonic is not None else [100, 200, 300, 400]
    walls = list(wall) if wall is not None else [10000, 10100, 10200, 10300]

    def take(values):
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def reader(path, limit):
        if limit != acq._input_limit(path):
            raise AssertionError("wrong fake read bound")
        return take(inputs[path])

    return acq.acquire(expected_release=RELEASE, expected_pci_bdf=BDF, status_path=STATUS_PATH,
                       reader=reader, uname=lambda: take(names),
                       monotonic_ns=lambda: take(monos), wall_time_ns=lambda: take(walls))


def encoded(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def evaluate(value=None, **changes):
    if value is None:
        value = make_capture()
    raw = value if isinstance(value, bytes) else encoded(value)
    return ce.evaluate(raw, expected_release=changes.get("expected_release", RELEASE),
                       expected_pci_bdf=changes.get("expected_pci_bdf", BDF),
                       status_path=changes.get("status_path", STATUS_PATH),
                       expected_bootargs=changes.get("expected_bootargs", BOOTARGS))


def replace_raw(capture, name, raw):
    capture["observations"][name]["raw"] = {
        "base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw), "source_complete": True, "representation": "exact_bytes",
    }


class CaptureEvidenceTests(unittest.TestCase):
    def rejected(self, value):
        result = evaluate(value)
        self.assertFalse(result["report"]["matched"])
        self.assertFalse(result["report"]["hardware_acceptance"])
        return result

    def test_valid_complete_capture_and_cache_match_are_distinct(self):
        saved = make_capture()
        raw = encoded(saved)
        result = evaluate(raw)
        report = result["report"]
        self.assertEqual(report["status"], ce.MATCH)
        self.assertTrue(report["complete_capture_validated"])
        self.assertTrue(report["producer_replay_matched"])
        self.assertTrue(report["cache_matched"])
        self.assertTrue(report["boot_policy_matched"])
        self.assertEqual(report["capture_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report["identity"], {"boot_id": BOOT.decode().strip(), "release": RELEASE,
                                               "driver": "amdgpu", "pci_bdf": BDF, "status_path": STATUS_PATH})
        self.assertEqual(len(result["raw_inputs"]), 12)
        self.assertEqual(result["raw_inputs"]["cached_status"], STATUS_BYTES)
        self.assertEqual(result["raw_inputs"]["cmdline"], BOOTARGS)
        for flag in ("hardware_acceptance", "normal_userspace_established", "acquisition_freshness_established",
                     "kernel_binary_attested", "timing_claim"):
            self.assertIs(report[flag], False)

    def test_json_object_equality_allows_formatting_but_records_original_hash(self):
        saved = make_capture()
        compact = encoded(saved)
        pretty = (json.dumps(saved, indent=4) + "\n").encode()
        first, second = evaluate(compact), evaluate(pretty)
        self.assertTrue(first["report"]["matched"])
        self.assertTrue(second["report"]["matched"])
        self.assertNotEqual(first["report"]["capture_sha256"], second["report"]["capture_sha256"])
        self.assertEqual(first["report"]["replayed_report_canonical_sha256"],
                         second["report"]["replayed_report_canonical_sha256"])

    def test_malformed_json_is_diagnostic_only(self):
        values = (b"", b"not json", b"[]", b"null", b"{}{}", b"\xff", b'{"x":NaN}',
                  b'{"x":Infinity}', b'{"x":2.0}', b'{"x":' + b"1" * 65 + b"}",
                  b" " * (ce.MAX_CAPTURE_BYTES + 1))
        for raw in values:
            with self.subTest(value=raw[:40]):
                result = self.rejected(raw)
                self.assertEqual(result["report"]["status"], "MALFORMED_CAPTURE_JSON")
                self.assertEqual(result["raw_inputs"], {})

    def test_duplicate_json_keys_are_rejected_at_any_depth(self):
        raw = encoded(make_capture())
        mutations = (raw.replace(b'"schema": 2', b'"schema": 2, "schema": 2', 1),
                     raw.replace(b'"source_complete": true', b'"source_complete": true, "source_complete": true', 1),
                     raw.replace(b'"monotonic_ns": 100', b'"monotonic_ns": 100, "monotonic_ns": 100', 1))
        for changed in mutations:
            result = self.rejected(changed)
            self.assertEqual(result["report"]["status"], "MALFORMED_CAPTURE_JSON")

    def test_exact_top_level_types_are_not_python_truthiness(self):
        for field, value in (("complete", 1), ("hardware_acceptance", 0), ("identity_consistent", 1),
                             ("drm_identity_consistent", 1), ("cached_status_read_attempted", 1),
                             ("wall_clock_nondecreasing", 1), ("schema", True), ("status", [])):
            with self.subTest(field=field):
                saved = make_capture()
                saved[field] = value
                result = self.rejected(saved)
                self.assertFalse(result["report"]["complete_capture_validated"])

    def test_source_references_and_bindings_must_match(self):
        for field in ("producer", "expected_release", "expected_pci_bdf", "expected_driver", "status_path",
                      "drm_name_path", "review_scope_reference", "review_scope_reference_sha256",
                      "drm_name_formatter_reference", "drm_name_source_sha256"):
            with self.subTest(field=field):
                saved = make_capture()
                saved[field] = "changed"
                self.assertFalse(self.rejected(saved)["report"]["complete_capture_validated"])
        saved = make_capture()
        saved["limits"] = []
        self.rejected(saved)

    def test_unknown_or_missing_top_level_field_never_matches(self):
        saved = make_capture()
        saved["extra"] = 1
        self.rejected(saved)
        for field in ("events", "issues", "limits", "producer", "complete", "wall_clock_nondecreasing"):
            with self.subTest(field=field):
                changed = make_capture()
                del changed[field]
                self.rejected(changed)

    def test_every_raw_digest_is_recomputed(self):
        for name in make_capture()["observations"]:
            with self.subTest(name=name):
                saved = make_capture()
                saved["observations"][name]["raw"]["sha256"] = "0" * 64
                result = self.rejected(saved)
                self.assertFalse(result["report"]["raw_observation_checks"][name]["integrity_verified"])
                self.assertEqual(result["raw_inputs"], {})

    def test_raw_byte_length_type_complete_and_representation_are_checked(self):
        changes = (("bytes", 0), ("bytes", True), ("source_complete", 1),
                   ("source_complete", False), ("representation", "pretend"), ("sha256", "A" * 64))
        for field, value in changes:
            with self.subTest(field=field):
                saved = make_capture()
                saved["observations"]["cached_status"]["raw"][field] = value
                self.rejected(saved)

    def test_base64_must_be_strict_canonical_and_bounded(self):
        for text in ("not base64!", "AA==\n", "AB==", "A" * 90000):
            with self.subTest(text=text[:20]):
                saved = make_capture()
                metadata = saved["observations"]["uname_before"]["raw"]
                metadata.update(base64=text, sha256=hashlib.sha256(b"\x00").hexdigest(), bytes=1)
                self.rejected(saved)

    def test_missing_or_partial_observation_is_not_complete(self):
        for name in make_capture()["observations"]:
            for variant in ("absent", "null", "partial"):
                with self.subTest(name=name, variant=variant):
                    saved = make_capture()
                    if variant == "absent":
                        del saved["observations"][name]
                    elif variant == "null":
                        saved["observations"][name]["raw"] = None
                    else:
                        saved["observations"][name]["raw"].update(source_complete=False, representation="observed_prefix")
                    result = self.rejected(saved)
                    self.assertFalse(result["report"]["complete_capture_validated"])
                    self.assertEqual(result["raw_inputs"], {})

    def test_observation_source_bound_and_unknown_metadata_are_checked(self):
        for field, value in (("source", "/proc/wrong"), ("limit_bytes", 999999), ("limit_bytes", True), ("unexpected", 1)):
            saved = make_capture()
            saved["observations"]["cmdline"][field] = value
            self.rejected(saved)
        saved = make_capture()
        saved["observations"]["cmdline"]["raw"]["unexpected"] = 1
        self.rejected(saved)
        saved = make_capture()
        saved["observations"]["extra"] = copy.deepcopy(saved["observations"]["cmdline"])
        self.rejected(saved)

    def test_parsed_values_are_rebuilt_not_trusted(self):
        for name in ("uname_before", "boot_id_after", "uptime_after", "drm_name_before", "cached_status"):
            saved = make_capture()
            saved["observations"][name]["parsed"] = "false supplied parse"
            result = self.rejected(saved)
            self.assertIn("PRODUCER_REPLAY_MISMATCH", [row["code"] for row in result["report"]["issues"]])

    def test_forged_complete_flags_cannot_hide_identity_failure(self):
        saved = make_capture(files={acq.BOOT_ID: [BOOT, BOOT, OTHER_BOOT]})
        self.assertFalse(saved["complete"])
        saved.update(status=acq.CAPTURED, complete=True, identity_consistent=True, issues=[])
        result = self.rejected(saved)
        self.assertFalse(result["report"]["complete_capture_validated"])

    def test_downgrading_success_flags_is_also_a_replay_mismatch(self):
        saved = make_capture()
        saved.update(status="INCOMPLETE_CACHED_PREPARATION_ACQUISITION", complete=False)
        result = self.rejected(saved)
        self.assertEqual(result["report"]["status"], "CAPTURE_FORMAT_OR_REPLAY_MISMATCH")

    def test_swapped_identity_observations_do_not_pass(self):
        saved = make_capture()
        before = saved["observations"]["uname_before"]
        saved["observations"]["uname_before"] = saved["observations"]["boot_id_before"]
        saved["observations"]["boot_id_before"] = before
        self.rejected(saved)
        saved = make_capture()
        replace_raw(saved, "boot_id_after", OTHER_BOOT)
        self.rejected(saved)

    def test_other_consistent_boot_id_is_reported_not_assumed_fresh(self):
        result = evaluate(make_capture(files={acq.BOOT_ID: [OTHER_BOOT] * 3}))
        self.assertTrue(result["report"]["matched"])
        self.assertEqual(result["report"]["identity"]["boot_id"], OTHER_BOOT.decode().strip())
        self.assertFalse(result["report"]["acquisition_freshness_established"])

    def test_changed_bdf_or_driver_cannot_be_hidden_in_parsed_values(self):
        for name_bytes in (b"amdgpu dev=0000:00:02.0 unique=0000:00:01.0\n",
                           b"radeon dev=0000:00:01.0\n"):
            saved = make_capture()
            replace_raw(saved, "drm_name_before", name_bytes)
            replace_raw(saved, "drm_name_after", name_bytes)
            result = self.rejected(saved)
            self.assertFalse(result["report"]["complete_capture_validated"])

    def test_actual_name_drift_is_preserved_as_incomplete(self):
        changed = b"amdgpu dev=0000:00:01.0 master=changed unique=0000:00:01.0\n"
        result = evaluate(make_capture(files={NAME_PATH: [NAME, changed]}))
        self.assertEqual(result["report"]["status"], "DIAGNOSTIC_INCOMPLETE_CAPTURE")
        self.assertTrue(result["report"]["producer_replay_matched"])
        self.assertFalse(result["report"]["complete_capture_validated"])
        self.assertEqual(result["raw_inputs"], {})

    def test_clock_and_uptime_regressions_are_not_success(self):
        saved = make_capture()
        saved["timestamps"]["after_cached_status"]["monotonic_ns"] = 199
        self.rejected(saved)
        saved = make_capture()
        replace_raw(saved, "uptime_after", b"12.33 38.25\n")
        self.rejected(saved)
        actual = make_capture(files={acq.UPTIME: [b"12.34 38.21\n", b"12.33 38.25\n"]})
        result = evaluate(actual)
        self.assertTrue(result["report"]["producer_replay_matched"])
        self.assertEqual(result["report"]["status"], "DIAGNOSTIC_INCOMPLETE_CAPTURE")

    def test_clock_types_fields_and_missing_brackets_fail_closed(self):
        for mutation in (True, None, -1, "200"):
            saved = make_capture()
            saved["timestamps"]["before_cached_status"]["monotonic_ns"] = mutation
            self.rejected(saved)
        saved = make_capture()
        del saved["timestamps"]["before_cached_status"]
        self.rejected(saved)
        saved = make_capture()
        saved["timestamps"]["before_cached_status"]["extra"] = 1
        self.rejected(saved)

    def test_event_order_is_checked_by_exact_replay(self):
        saved = make_capture()
        saved["events"][1], saved["events"][2] = saved["events"][2], saved["events"][1]
        self.rejected(saved)
        saved = make_capture()
        saved["events"].pop()
        self.rejected(saved)

    def test_wall_clock_adjustment_has_no_timing_claim(self):
        result = evaluate(make_capture(wall=[10000, 10100, 10050, 10060]))
        self.assertTrue(result["report"]["matched"])
        self.assertFalse(result["report"]["timing_claim"])

    def test_semantically_malformed_cache_is_still_a_complete_capture(self):
        result = evaluate(make_capture(status_bytes=b"not cached status\n"))
        self.assertTrue(result["report"]["complete_capture_validated"])
        self.assertFalse(result["report"]["cache_matched"])
        self.assertEqual(result["report"]["status"], "VALIDATED_COMPLETE_CAPTURE_CACHE_NOT_MATCHED")
        self.assertEqual(result["report"]["observation_evaluation"]["status"], "MALFORMED_SAVED_INPUT")
        self.assertEqual(result["raw_inputs"]["cached_status"], b"not cached status\n")

    def test_invalidated_history_is_preserved_without_support(self):
        status_bytes = STATUS_BYTES.replace(b"stage=PREPARED", b"stage=INVALIDATED").replace(
            b"observed_generation=1", b"observed_generation=2").replace(b"last_event=GART_ENABLE", b"last_event=RESET")
        result = evaluate(make_capture(status_bytes=status_bytes))
        self.assertTrue(result["report"]["complete_capture_validated"])
        self.assertFalse(result["report"]["matched"])
        self.assertEqual(result["report"]["observation_evaluation"]["status"], "OBSERVED_CACHED_INVALIDATED")
        self.assertEqual(result["raw_inputs"]["cached_status"], status_bytes)

    def test_zero_generation_cannot_match_updated_observation_contract(self):
        status_bytes = STATUS_BYTES.replace(b"observed_generation=1 proof_generation=1 last_event=GART_ENABLE",
                                            b"observed_generation=0 proof_generation=0 last_event=NONE")
        result = evaluate(make_capture(status_bytes=status_bytes))
        self.assertTrue(result["report"]["complete_capture_validated"])
        self.assertFalse(result["report"]["cache_matched"])
        self.assertEqual(result["report"]["observation_evaluation"]["status"], "CACHED_PREPARATION_PREDICATES_NOT_MET")

    def test_debugfs_disabled_changed_or_missing_is_separate_boot_policy_failure(self):
        for cmdline in (BOOTARGS.replace(b"debugfs=on", b"debugfs=off"),
                        BOOTARGS.replace(b" debugfs=on", b""),
                        BOOTARGS.replace(b"debugfs=on", b"debugfs=on debugfs=on")):
            result = evaluate(make_capture(cmdline=cmdline))
            self.assertTrue(result["report"]["complete_capture_validated"])
            self.assertTrue(result["report"]["cache_matched"])
            self.assertFalse(result["report"]["boot_policy_matched"])
            self.assertEqual(result["report"]["status"], "CAPTURE_BOOT_POLICY_MISMATCH")

    def test_complete_ordered_bootargs_must_equal_expectation(self):
        changed = (BOOTARGS.replace(b"rootwait rw", b"rw rootwait"),
                   BOOTARGS.replace(b"rootfstype=ext4", b"rootfstype=btrfs"),
                   BOOTARGS.replace(b" console=null", b""),
                   BOOTARGS[:-1] + b" extra=1\n")
        for cmdline in changed:
            result = evaluate(make_capture(cmdline=cmdline))
            self.assertTrue(result["report"]["complete_capture_validated"])
            self.assertFalse(result["report"]["boot_policy_matched"])
            self.assertFalse(result["report"]["matched"])

    def test_expected_bootargs_must_be_canonical_complete_policy(self):
        bad = (BOOTARGS[:-1], BOOTARGS + b"\n", b" " + BOOTARGS, BOOTARGS.replace(b" rw ", b"  rw "),
               BOOTARGS.replace(b" rw ", b"\trw "), BOOTARGS.replace(b"debugfs=on", b"debugfs=off"),
               BOOTARGS.replace(b"debugfs=on", b"debugfs=on debugfs=on"),
               BOOTARGS.replace(b"rw", b"rw rw"), BOOTARGS.replace(b"console=null", b"console=null console=tty0"),
               BOOTARGS.replace(b"debugfs=on", b"-- debugfs=on"),
               BOOTARGS.replace(b"rootwait", b'other="text" rootwait'), b"\xff\n")
        for expected in bad:
            with self.subTest(expected=expected[:80]):
                result = evaluate(expected_bootargs=expected)
                self.assertTrue(result["report"]["complete_capture_validated"])
                self.assertEqual(result["report"]["status"], "INVALID_EXPECTED_BOOT_POLICY")
                self.assertFalse(result["report"]["matched"])

    def test_gate_alias_duplicate_old_or_unknown_expected_gate_rejected(self):
        for token in (b"amdgpu.ps4_uvd_live_prepare=0", b"amdgpu.ps4_uvd_live_prepare=Y",
                      b"amdgpu.ps4-uvd-live-prepare=1", b"ps4_uvd_live_prepare=1",
                      b"amdgpu.ps4_uvd_ready=0", b"amdgpu.ps4_uvd_unknown=1"):
            expected = BOOTARGS.replace(b"amdgpu.ps4_uvd_live_prepare=1", token)
            result = evaluate(expected_bootargs=expected)
            self.assertFalse(result["report"]["expected_boot_policy"]["valid"])
            self.assertFalse(result["report"]["matched"])
        expected = BOOTARGS[:-1] + b" amdgpu.ps4-uvd-live-prepare=1\n"
        result = evaluate(expected_bootargs=expected)
        self.assertIn("DUPLICATE_ARGUMENT_KEY", [item["code"] for item in result["report"]["expected_boot_policy"]["issues"]])

    def test_observed_noncanonical_spacing_is_not_hidden_by_tokenization(self):
        result = evaluate(make_capture(cmdline=BOOTARGS.replace(b" rw ", b"  rw ")))
        self.assertTrue(result["report"]["complete_capture_validated"])
        self.assertFalse(result["report"]["boot_policy_matched"])

    def test_missing_status_error_is_diagnostic_no_retry_or_empty_success(self):
        saved = make_capture(files={STATUS_PATH: [OSError(errno.ENOENT, "synthetic missing node")]})
        result = evaluate(saved)
        self.assertEqual(result["report"]["status"], "DIAGNOSTIC_INCOMPLETE_CAPTURE")
        self.assertFalse(result["report"]["complete_capture_validated"])
        self.assertEqual(result["raw_inputs"], {})
        self.assertFalse(result["report"]["raw_observation_checks"]["cached_status"]["raw_present"])

    def test_partial_status_keeps_prefix_hash_but_no_positive_inputs(self):
        saved = make_capture(files={STATUS_PATH: [acq.ReadFailure("READ_OS_ERROR", "synthetic EIO", partial=b"partial", errno=5)]})
        result = evaluate(saved)
        self.assertEqual(result["report"]["status"], "DIAGNOSTIC_INCOMPLETE_CAPTURE")
        row = result["report"]["raw_observation_checks"]["cached_status"]
        self.assertEqual(row["sha256"], hashlib.sha256(b"partial").hexdigest())
        self.assertFalse(row["source_complete"])
        self.assertEqual(result["raw_inputs"], {})

    def test_oversized_prefix_limit_is_enforced(self):
        saved = make_capture(files={STATUS_PATH: [b"x" * 65537]})
        result = evaluate(saved)
        self.assertEqual(result["report"]["status"], "DIAGNOSTIC_INCOMPLETE_CAPTURE")
        replace_raw(saved, "cached_status", b"x" * 65537)
        self.rejected(saved)

    def test_incomplete_before_capture_and_clock_error_are_diagnostic(self):
        values = (make_capture(releases=[b"wrong"]),
                  make_capture(wall=[10000, 10100, OSError("fake clock"), 10300]))
        for saved in values:
            result = evaluate(saved)
            self.assertEqual(result["report"]["status"], "DIAGNOSTIC_INCOMPLETE_CAPTURE")
            self.assertFalse(result["report"]["complete_capture_validated"])
            self.assertEqual(result["raw_inputs"], {})

    def test_cli_error_is_preserved_as_error_only(self):
        saved = {"schema": 2, "status": "ACQUISITION_CLI_ERROR", "complete": False,
                 "hardware_acceptance": False, "exception": "ValueError", "detail": "missing argument"}
        result = evaluate(saved)
        self.assertEqual(result["report"]["status"], "DIAGNOSTIC_ACQUISITION_ERROR")
        self.assertFalse(result["report"]["matched"])
        saved["detail"] = "x" * 513
        self.assertEqual(self.rejected(saved)["report"]["status"], "CAPTURE_FORMAT_MISMATCH")

    def test_caller_pins_and_api_types(self):
        for changes in ({"expected_release": "bad release"}, {"expected_pci_bdf": "00:01.0"},
                        {"status_path": "/sys/kernel/debug/dri/0/amdgpu_regs"}):
            result = evaluate(**changes)
            self.assertEqual(result["report"]["status"], "INVALID_CALLER_EXPECTATION")
        for changes in ({"expected_bootargs": "text"}, {"expected_pci_bdf": None}):
            with self.assertRaises(TypeError):
                evaluate(**changes)
        with self.assertRaises(TypeError):
            ce.evaluate("text", expected_release=RELEASE, expected_pci_bdf=BDF,
                        status_path=STATUS_PATH, expected_bootargs=BOOTARGS)

    def test_pure_evaluation_cannot_call_live_adapters_or_file_io(self):
        saved = encoded(make_capture())
        with mock.patch("builtins.open", side_effect=AssertionError("file read")), \
                mock.patch.object(acq, "read_bounded", side_effect=AssertionError("native read")), \
                mock.patch.object(acq, "native_uname_release", side_effect=AssertionError("native uname")), \
                mock.patch.object(acq.os, "open", side_effect=AssertionError("os open")), \
                mock.patch.object(acq.os, "read", side_effect=AssertionError("os read")), \
                mock.patch.object(acq.os, "uname", side_effect=AssertionError("os uname")), \
                mock.patch.object(acq.time, "monotonic_ns", side_effect=AssertionError("clock")), \
                mock.patch.object(acq.time, "time_ns", side_effect=AssertionError("clock")):
            result = evaluate(saved)
        self.assertTrue(result["report"]["matched"])

    def test_dependency_pins_match_actual_peer_source(self):
        for name, expected in ce.SOURCE_PINS.items():
            self.assertEqual(hashlib.sha256((HERE / name).read_bytes()).hexdigest(), expected)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-file", type=Path, required=True)
    args = parser.parse_args()
    paths = (args.report_file, args.report_file.with_suffix(".log"),
             args.report_file.with_name(args.report_file.stem + "-synthetic-result.json"))
    if any(path.exists() for path in paths):
        parser.error("new evidence paths required")
    args.report_file.parent.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue()
    receipt = {
        "schema": 1, "status": "PASS_SAVED_CAPTURE_EVIDENCE_TESTS" if result.wasSuccessful() else "FAIL_SAVED_CAPTURE_EVIDENCE_TESTS",
        "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "platform": sys.platform, "python": sys.version, "source_pins": dict(ce.SOURCE_PINS),
        "validator_sha256": hashlib.sha256((HERE / "capture_evidence.py").read_bytes()).hexdigest(),
        "tests_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "log_sha256": hashlib.sha256(log.encode()).hexdigest(),
        "hardware_acceptance": False, "live_acquisition_performed": False, "subprocesses_run": False,
        "inputs": "Synthetic saved producer reports generated with fake readers, identity and clock functions only",
    }
    with paths[0].open("x", encoding="utf-8") as output:
        json.dump(receipt, output, indent=2, sort_keys=True)
        output.write("\n")
    with paths[1].open("x", encoding="utf-8") as output:
        output.write(log)
    with paths[2].open("x", encoding="utf-8") as output:
        json.dump(evaluate()["report"], output, indent=2, sort_keys=True)
        output.write("\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
