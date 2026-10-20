#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline saved-input tests, driven by the actual debugfs format strings."""

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import platform
import re
import struct
import sys
import tempfile
import unittest
import zlib

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
spec = importlib.util.spec_from_file_location("saved_preparation_observation", HERE / "observation.py")
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)
EXPECTED_RELEASE = "7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1"
CMDLINE = b"root=UUID=synthetic rw console=ttyS0,115200 amdgpu.ps4_uvd_live_prepare=1\n"
TMP_ROOT = None
EVALUATIONS = 0


def source_formats():
    source = (HERE / "debugfs.c.inc").read_bytes()
    if hashlib.sha256(source).hexdigest() != o.SOURCE_ANCHORS["debugfs_template_sha256"]:
        raise AssertionError("formatter source identity changed; reassess the evaluator schema")
    formats = re.findall(r'seq_(?:printf|puts)\(m,\s*("(?:\\.|[^"\\])*")', source.decode())
    return [json.loads(value) for value in formats]


def rendered_sample(**changes):
    # Values are synthetic observations; strings/field order come from C source.
    values = {
        "build": EXPECTED_RELEASE, "stage": "PREPARED", "error": 0,
        "selected": 1, "attempted": 1, "retained": 1, "reservation_seen": 1,
        "allocator_alive_cached": 1, "early_fence_init": 1,
        "observed_generation": 1, "proof_generation": 1, "last_event": "GART_ENABLE",
        "historical_sdma_proof": 1, "proof_ns": 123456789,
        "reset_counter_at_proof": 0, "copy_crc32": 0x570AE4EB,
        "pd_at_proof": 0x800200000, "pd_bytes": 0x20000, "pt_bytes": 0x2000,
        "pages": 812, "r1_bytes": 0x308000, "rb_bytes": 0x1000, "ib_bytes": 0x20000,
    }
    values.update(changes)
    def substitute(match):
        key, prefix, code = match.groups()
        value = values[key]
        rendered = (str(value) if code in ("%s", "%d", "%u", "%llu") else
                    format(value, "08x" if code == "%08x" else "x"))
        return key + "=" + (prefix or "") + rendered
    return "".join(re.sub(r"([A-Za-z0-9_]+)=(0x)?(%llu|%llx|%08x|%[sdu])", substitute, line)
                   for line in source_formats()).encode()


def evaluate(raw=None, uname=None, cmdline=CMDLINE):
    global EVALUATIONS
    EVALUATIONS += 1
    return o.evaluate(rendered_sample() if raw is None else raw,
                      (EXPECTED_RELEASE + "\n").encode() if uname is None else uname,
                      cmdline, expected_release=EXPECTED_RELEASE)


class ObservationTests(unittest.TestCase):
    def assert_nonpass(self, result, expected=None):
        self.assertFalse(result["matched"])
        self.assertFalse(result["hardware_acceptance"])
        self.assertFalse(result["live_acquisition_performed"])
        self.assertNotEqual(result["status"], o.MATCH)
        if expected:
            self.assertEqual(result["status"], expected)

    def test_actual_formatter_and_schema_field_order_agree(self):
        formats = source_formats()
        self.assertEqual(len(formats), 8)
        names = [re.findall(r"(?:^| )([A-Za-z0-9_]+)=", row) for row in formats]
        self.assertEqual(names, [[key for key, _ in row] for row in o.ROWS])

    def test_source_geometry_and_probe_names_are_anchored(self):
        base = WORKSPACE / o.SOURCE_ANCHORS["candidate"] / "drivers/gpu/drm/amd/amdgpu"
        canary = (base / "amdgpu_ps4_map_canary.c").read_bytes()
        ready = (base / "amdgpu_ps4_uvd_ready.c").read_bytes()
        self.assertEqual(hashlib.sha256(canary).hexdigest(), o.SOURCE_ANCHORS["map_canary_sha256"])
        self.assertEqual(hashlib.sha256(ready).hexdigest(), o.SOURCE_ANCHORS["uvd_ready_sha256"])
        for name, value in (("PS4_READY_R_BYTES", o.GEOMETRY["r1_bytes"]),
                            ("PS4_READY_IB_BYTES", o.GEOMETRY["ib_bytes"]),
                            ("PS4_READY_MAPPED_PAGES", o.GEOMETRY["pages"])):
            token = re.search(r"^#define " + name + r" (0x[0-9a-f]+|[0-9]+)(?:U|L)*$",
                              canary.decode(), re.M).group(1)
            self.assertEqual(int(token, 0), value)
        body = re.search(r"bool amdgpu_ps4_uvd_boot_probe_requested\(void\)\n\{(.*?)\n\}",
                         ready.decode(), re.S).group(1)
        names = set(re.findall(r"ps4_uvd_[a-z_]+", body))
        self.assertEqual({"amdgpu." + name for name in names} | {"amdgpu.ps4_map_canary"}, o.OLD_GATES)
        self.assertIn("adev->vm_manager.block_size != 10", canary.decode())
        self.assertIn("root_bytes > SZ_1M", canary.decode())
        self.assertIn("s->pd_size = ALIGN(root_bytes, PAGE_SIZE);", canary.decode())

    def test_crc_reference_independently_computes_exact_pattern(self):
        reference = json.loads((WORKSPACE / o.SOURCE_ANCHORS["crc_reference"]).read_text())
        raw = b"".join(struct.pack("<I", 0x50533400 ^ ((i * 0x01020409) & 0xFFFFFFFF)) for i in range(64))
        crc = 0xFFFFFFFF
        for byte in raw:
            crc ^= byte
            for _ in range(8):
                crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
        crc ^= 0xFFFFFFFF
        self.assertEqual(crc, zlib.crc32(raw))
        self.assertEqual(crc, o.EXPECTED_CRC32)
        self.assertEqual(f"{crc:08x}", reference["expected_crc32"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), o.SOURCE_ANCHORS["crc_bytes_sha256"])
        self.assertEqual(reference["source_sha256"], o.SOURCE_ANCHORS["map_canary_sha256"])

    def test_valid_sample_matches_only_cached_observation(self):
        result = evaluate()
        self.assertEqual(result["status"], o.MATCH)
        self.assertTrue(result["matched"])
        self.assertFalse(result["hardware_acceptance"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["observations"]["status_fields"]["copy_crc32"], o.EXPECTED_CRC32)

    def test_raw_hashes_preserve_original_bytes(self):
        raw = rendered_sample()
        uname = (EXPECTED_RELEASE + "\n").encode()
        result = evaluate(raw, uname)
        for name, data in (("status", raw), ("uname", uname), ("cmdline", CMDLINE)):
            self.assertEqual(result["raw_inputs"][name],
                             {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        trimmed = evaluate(raw, uname[:-1])
        self.assertTrue(trimmed["matched"])
        self.assertNotEqual(trimmed["raw_inputs"]["uname"]["sha256"], result["raw_inputs"]["uname"]["sha256"])

    def test_build_or_uname_mismatch_remains_distinct(self):
        for raw, uname in ((rendered_sample(build="wrong-build"), None), (None, b"wrong-build\n")):
            with self.subTest(uname=uname):
                self.assert_nonpass(evaluate(raw, uname), "BUILD_IDENTITY_MISMATCH")

    def test_every_missing_field_rejected(self):
        for name in o.FIELD_TYPES:
            with self.subTest(field=name):
                raw = re.sub(rb"\b" + name.encode() + rb"=[^ \n]+ ?", b"", rendered_sample(), count=1)
                result = evaluate(raw)
                self.assert_nonpass(result, "MALFORMED_SAVED_INPUT")
                self.assertTrue(any(x["code"] == "MISSING_FIELD" and x["field"] == name for x in result["issues"]))

    def test_every_duplicate_field_rejected_and_preserved(self):
        original = rendered_sample()
        for name in o.FIELD_TYPES:
            with self.subTest(field=name):
                token = re.search(rb"\b" + name.encode() + rb"=[^ \n]+", original).group()
                raw = original.replace(token, token + b" " + token, 1)
                result = evaluate(raw)
                self.assert_nonpass(result, "MALFORMED_SAVED_INPUT")
                self.assertEqual(sum(row["key"] == name for row in result["observations"]["raw_status_fields"]), 2)

    def test_unknown_field_rejected(self):
        result = evaluate(rendered_sample().replace(b"schema=1", b"schema=1 new_field=1", 1))
        self.assert_nonpass(result, "MALFORMED_SAVED_INPUT")
        self.assertTrue(any(x["code"] == "UNKNOWN_FIELD" for x in result["issues"]))

    def test_line_order_spacing_and_termination_rejected(self):
        raw = rendered_sample()
        rows = raw.splitlines(keepends=True)
        samples = [b"".join([rows[1], rows[0], *rows[2:]]), raw.replace(b" ", b"  ", 1),
                   raw.replace(b" ", b"\t", 1), raw[:-1], raw + b"\n", raw.replace(b"\n", b"\r\n")]
        for sample in samples:
            with self.subTest(sample=sample[:30]):
                self.assert_nonpass(evaluate(sample), "MALFORMED_SAVED_INPUT")

    def test_wrong_numeric_types_ranges_and_spellings_rejected(self):
        replacements = {b"selected=1": [b"selected=2", b"selected=true", b"selected=01"],
                        b"error=0": [b"error=2147483648", b"error=-2147483649", b"error=+0"],
                        b"proof_ns=123456789": [b"proof_ns=-1", b"proof_ns=18446744073709551616"],
                        b"copy_crc32=570ae4eb": [b"copy_crc32=570AE4EB", b"copy_crc32=0x570ae4eb"],
                        b"rb_bytes=0x1000": [b"rb_bytes=4096", b"rb_bytes=0x01000", b"rb_bytes=0x10000000000000000"]}
        for old, variants in replacements.items():
            for value in variants:
                with self.subTest(value=value):
                    self.assert_nonpass(evaluate(rendered_sample().replace(old, value)), "MALFORMED_SAVED_INPUT")

    def test_unknown_stage_and_event_are_not_future_passes(self):
        for changes in ({"stage": "FUTURE_READY"}, {"last_event": "UNKNOWN"}):
            with self.subTest(changes=changes):
                self.assert_nonpass(evaluate(rendered_sample(**changes)), "MALFORMED_SAVED_INPUT")

    def test_contract_labels_cannot_be_changed(self):
        raw = rendered_sample()
        for name, value in o.LABELS.items():
            replacement = "2" if name == "schema" else "other"
            with self.subTest(name=name):
                sample = raw.replace(f"{name}={value}".encode(), f"{name}={replacement}".encode(), 1)
                self.assert_nonpass(evaluate(sample), "OBSERVATION_CONTRACT_MISMATCH")

    def test_absent_and_disabled_preparation_gate(self):
        for line in (b"root=x\n", b"amdgpu.ps4_uvd_live_prepare=0\n",
                     b"amdgpu.ps4_uvd_live_prepare=false\n", b"amdgpu.ps4_uvd_live_prepare=\n"):
            with self.subTest(line=line):
                self.assert_nonpass(evaluate(cmdline=line), "BOOT_GATE_MISMATCH")

    def test_canonical_true_boolean_and_bare_gate(self):
        for value in sorted(o.TRUE_GATE_VALUES):
            with self.subTest(value=value):
                self.assertTrue(evaluate(cmdline=(o.PREPARE_GATE + "=" + value + "\n").encode())["matched"])
        self.assertTrue(evaluate(cmdline=(o.PREPARE_GATE + "\n").encode())["matched"])
        self.assert_nonpass(evaluate(cmdline=b"amdgpu.ps4_uvd_live_prepare=truthy-garbage\n"), "BOOT_GATE_MISMATCH")

    def test_every_old_gate_is_absent_even_if_explicitly_zero(self):
        for name in sorted(o.OLD_GATES):
            for suffix in ("", "=0", "=1"):
                with self.subTest(name=name, suffix=suffix):
                    self.assert_nonpass(evaluate(cmdline=CMDLINE.rstrip() + b" " + (name + suffix).encode()), "BOOT_GATE_MISMATCH")

    def test_duplicate_identical_conflicting_and_alias_preparation_gates(self):
        for second in ("amdgpu.ps4_uvd_live_prepare=1", "amdgpu.ps4_uvd_live_prepare=0",
                       "amdgpu.ps4-uvd-live-prepare=1"):
            with self.subTest(second=second):
                result = evaluate(cmdline=CMDLINE.rstrip() + b" " + second.encode())
                self.assert_nonpass(result, "BOOT_GATE_MISMATCH")
                self.assertTrue(any(x["code"] == "DUPLICATE_PREPARATION_GATE" for x in result["issues"]))

    def test_separator_does_not_make_init_argument_a_kernel_gate(self):
        self.assert_nonpass(evaluate(cmdline=b"root=x -- amdgpu.ps4_uvd_live_prepare=1\n"), "BOOT_GATE_MISMATCH")
        self.assert_nonpass(evaluate(cmdline=CMDLINE.rstrip() + b" -- amdgpu.ps4_map_canary=0"), "BOOT_GATE_MISMATCH")
        self.assert_nonpass(evaluate(cmdline=CMDLINE.rstrip() + b" -- amdgpu.ps4_uvd_live_prepare=1"), "BOOT_GATE_MISMATCH")

    def test_unqualified_probe_names_rejected(self):
        for word in ("ps4_uvd_live_prepare=1", "ps4_map_canary=0", "ps4_uvd_token=1"):
            with self.subTest(word=word):
                self.assert_nonpass(evaluate(cmdline=CMDLINE.rstrip() + b" " + word.encode()), "BOOT_GATE_MISMATCH")

    def test_ambiguous_quoting_or_shell_escaping_is_not_interpreted(self):
        for line in (b'amdgpu.ps4_uvd_live_prepare="1"\n', b"'amdgpu.ps4_uvd_live_prepare=1'\n",
                     b"amdgpu.ps4_uvd_live_\\prepare=1\n"):
            with self.subTest(line=line):
                self.assert_nonpass(evaluate(cmdline=line), "MALFORMED_SAVED_INPUT")

    def test_each_nonprepared_stage_is_distinct(self):
        for stage in sorted(o.STAGES - {"PREPARED"}):
            with self.subTest(stage=stage):
                self.assert_nonpass(evaluate(rendered_sample(stage=stage)), "OBSERVED_CACHED_" + stage)

    def test_invalidated_state_keeps_error_and_generation_evidence(self):
        result = evaluate(rendered_sample(stage="INVALIDATED", error=-116,
                                         observed_generation=2, last_event="RESET"))
        self.assert_nonpass(result, "OBSERVED_CACHED_INVALIDATED")
        self.assertEqual(result["observations"]["status_fields"]["error"], -116)
        self.assertIn("CACHED_GENERATION_DRIFT", {x["code"] for x in result["issues"]})

    def test_prepared_nonzero_error_is_not_a_match(self):
        self.assert_nonpass(evaluate(rendered_sample(error=-5)), "CACHED_PREPARATION_ERROR")

    def test_generation_drift_is_distinct(self):
        self.assert_nonpass(evaluate(rendered_sample(observed_generation=2)), "CACHED_PREPARATION_GENERATION_DRIFT")

    def test_event_history_consistency(self):
        for changes in ({"last_event": "RESET"}, {"last_event": "NONE"},
                        {"observed_generation": 0, "proof_generation": 0}):
            with self.subTest(changes=changes):
                self.assert_nonpass(evaluate(rendered_sample(**changes)), "CACHED_PREPARATION_PREDICATES_NOT_MET")
        # The actual normal-boot order requires at least one GART_ENABLE event.
        self.assert_nonpass(evaluate(rendered_sample(observed_generation=0, proof_generation=0,
                            last_event="NONE")), "CACHED_PREPARATION_PREDICATES_NOT_MET")
        # Do not invent an exactly-one event rule from the first expected boot.
        self.assertTrue(evaluate(rendered_sample(observed_generation=2, proof_generation=2,
                                                last_event="GART_ENABLE"))["matched"])

    def test_all_required_flags_proof_time_and_reset(self):
        changes = [{name: 0} for name in o.FLAGS] + [{"proof_ns": 0}, {"reset_counter_at_proof": 1}]
        for variant in changes:
            with self.subTest(variant=variant):
                self.assert_nonpass(evaluate(rendered_sample(**variant)), "CACHED_PREPARATION_PREDICATES_NOT_MET")

    def test_every_fixed_geometry_value_is_checked(self):
        for name, value in o.GEOMETRY.items():
            with self.subTest(name=name):
                raw = rendered_sample()
                if name == "vmid":
                    raw = raw.replace(b"vmid=4", b"vmid=5")
                else:
                    raw = rendered_sample(**{name: value + 1})
                self.assert_nonpass(evaluate(raw), "CACHED_PREPARATION_GEOMETRY_MISMATCH")

    def test_pd_size_is_bounded_not_hardcoded_to_fixture(self):
        for size in (0x7000, 0x10000, 0x20000, 0x100000):
            with self.subTest(size=size):
                self.assertTrue(evaluate(rendered_sample(pd_bytes=size))["matched"])
        for size in (0, 0x6000, 0x7001, 0x101000):
            with self.subTest(size=size):
                self.assert_nonpass(evaluate(rendered_sample(pd_bytes=size)), "CACHED_PREPARATION_GEOMETRY_MISMATCH")

    def test_pd_address_metadata_alignment_and_overflow(self):
        for address in (0, 0x800200001, 0xFFFFFFFFFFFFF000):
            with self.subTest(address=address):
                self.assert_nonpass(evaluate(rendered_sample(pd_at_proof=address)), "CACHED_PREPARATION_GEOMETRY_MISMATCH")

    def test_crc_is_source_pattern_check_not_hardware_claim(self):
        result = evaluate(rendered_sample(copy_crc32=0x12345678))
        self.assert_nonpass(result, "CACHED_PREPARATION_CRC_MISMATCH")
        self.assertEqual(result["observations"]["status_fields"]["copy_crc32"], 0x12345678)
        self.assertEqual(result["expected_copy_crc32"], "570ae4eb")

    def test_empty_nonascii_nul_multiline_inputs_fail(self):
        for raw in (b"", b"\xff", b"\0", b"x" * (o.MAX_INPUT_BYTES + 1)):
            with self.subTest(kind=raw[:5]):
                self.assert_nonpass(evaluate(raw), "MALFORMED_SAVED_INPUT")
        self.assert_nonpass(evaluate(uname=(EXPECTED_RELEASE + "\nextra\n").encode()), "MALFORMED_SAVED_INPUT")
        self.assert_nonpass(evaluate(cmdline=CMDLINE + b"second line\n"), "MALFORMED_SAVED_INPUT")

    def test_api_requires_raw_bytes_and_explicit_release(self):
        with self.assertRaises(TypeError):
            o.evaluate("text", b"x", b"x", expected_release=EXPECTED_RELEASE)
        for release in ("", "spaces in release", "x\n", None):
            with self.subTest(release=release), self.assertRaises(ValueError):
                o.evaluate(b"", b"", b"", expected_release=release)

    def cli_files(self, root, status=None):
        for name, raw in (("status.txt", rendered_sample() if status is None else status),
                          ("uname.txt", (EXPECTED_RELEASE + "\n").encode()), ("cmdline.txt", CMDLINE)):
            (root / name).write_bytes(raw)
        return ["--status-file", str(root / "status.txt"), "--uname-file", str(root / "uname.txt"),
                "--cmdline-file", str(root / "cmdline.txt"), "--expected-release", EXPECTED_RELEASE,
                "--report", str(root / "report.json")]

    def test_cli_new_report_and_no_overwrite(self):
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as name:
            root = Path(name)
            argv = self.cli_files(root)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(o.main(argv), 0)
                before = (root / "report.json").read_bytes()
                self.assertEqual(o.main(argv), 2)
            self.assertEqual(before, (root / "report.json").read_bytes())

    def test_cli_nonpass_still_saves_raw_identity(self):
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as name:
            root = Path(name)
            raw = rendered_sample(stage="RELEASED", retained=0)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(o.main(self.cli_files(root, raw)), 1)
            report = json.loads((root / "report.json").read_text())
            self.assert_nonpass(report, "OBSERVED_CACHED_RELEASED")
            self.assertEqual(report["raw_inputs"]["status"]["sha256"], hashlib.sha256(raw).hexdigest())

    def test_cli_rejects_symlink_and_standard_live_paths(self):
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as name:
            root = Path(name)
            (root / "saved").write_bytes(b"saved")
            (root / "link").symlink_to(root / "saved")
            with self.assertRaises(ValueError):
                o._read_saved(root / "link")
        # Only validate path spelling; no live path is opened or inspected.
        for path in ("/dev/zero", "/proc/cmdline", "/sys/kernel/debug/dri/0/status"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                o._explicit_saved_path(Path(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=False)
    global TMP_ROOT
    TMP_ROOT = str(args.report_dir)
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ObservationTests))
    (args.report_dir / "tests.log").write_text(log.getvalue())
    (args.report_dir / "synthetic-status.txt").write_bytes(rendered_sample())
    (args.report_dir / "synthetic-uname.txt").write_bytes((EXPECTED_RELEASE + "\n").encode())
    (args.report_dir / "synthetic-cmdline.txt").write_bytes(CMDLINE)
    (args.report_dir / "synthetic-evaluation.json").write_text(json.dumps(evaluate(), indent=2) + "\n")
    receipt = {"schema": 1, "status": "PASS_OFFLINE_SAVED_OBSERVATION_TESTS" if result.wasSuccessful() else "FAIL_OFFLINE_SAVED_OBSERVATION_TESTS",
               "tests": result.testsRun, "evaluations_including_parameterized_cases": EVALUATIONS,
               "failures": len(result.failures), "errors": len(result.errors),
               "host": {"system": platform.system(), "machine": platform.machine()},
               "evaluator_sha256": hashlib.sha256((HERE / "observation.py").read_bytes()).hexdigest(),
               "tests_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "log_sha256": hashlib.sha256((args.report_dir / "tests.log").read_bytes()).hexdigest(),
               "input_origin": "synthetic values rendered from actual pinned debugfs format strings",
               "hardware_acceptance": False, "hardware_actions": False, "subprocesses_run": False}
    (args.report_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    if not result.wasSuccessful():
        print(log.getvalue())
        raise SystemExit(1)


if __name__ == "__main__":
    main()
