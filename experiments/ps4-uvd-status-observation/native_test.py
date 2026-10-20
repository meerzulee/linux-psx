#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic filesystem/receipt contracts only: no native cache, kernel or devices."""

import argparse
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("stobs_native_tool", HERE / "native.py")
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)


class Contracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="psx-stobs-native-synthetic-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.prep, self.builder, _ = n.load_helpers()

    def file(self, name, data=b"fixture"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_paths_reject_escapes_and_symlinks(self):
        for value in ("", "/absolute", "../escape", "a/../escape", "a\nb", "a\0b", "a//b"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                n.relative(self.root, value)
        (self.root / "link").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            n.relative(self.root, "link/new")

    def test_missing_approval_prevents_any_run_directory(self):
        work = n.Native(self.root, "new-run")
        with mock.patch.object(work.prep, "native_host"):
            with self.assertRaisesRegex(RuntimeError, "review path"):
                work.phase("prepare-base", None, lambda *args: self.fail("action ran"))
        self.assertFalse(work.run.exists())

    def test_plan_is_read_only_and_not_approval(self):
        work = n.Native(self.root, "new-run")
        result = work.plan()
        self.assertEqual(result["status"], "READ_ONLY_PLAN_NOT_BUILD_APPROVAL")
        self.assertFalse(work.run.exists())
        work.run.mkdir()
        with self.assertRaisesRegex(RuntimeError, "existing run"):
            work.plan()

    def test_run_must_not_overlap_baseline(self):
        for path in (n.BASE_REL, n.BASE_REL + "/child", "lab/uvd-2026-10-19"):
            with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, "overlaps"):
                n.Native(self.root, path)

    def test_receipts_cannot_be_overwritten(self):
        path = self.root / "receipt.json"
        n.write_json(path, {"first": True})
        with self.assertRaises(FileExistsError):
            n.write_json(path, {"first": False})
        self.assertEqual(n.json_read(path), {"first": True})

    def test_duplicate_manifest_key_refused(self):
        path = self.file("duplicate.json", b'{"schema":1,"schema":2}')
        with self.assertRaisesRegex(RuntimeError, "duplicate JSON"):
            n.json_read(path)

    def review_fixture(self):
        files = {key: self.file("inputs/" + key, key.encode()) for key in n.PINS}
        files["patch"].write_text(f"diff --git a/{n.READY} b/{n.READY}\n--- a/{n.READY}\n+++ b/{n.READY}\n@@ -1 +1 @@\n-old\n+new\n")
        files["native_tool"].write_bytes(Path(n.__file__).read_bytes())
        pins = {key: {"path": path.relative_to(self.root).as_posix(), "sha256": n.sha(path)} for key, path in files.items()}
        receipt = {"status": "PASS_EXTRACTED_SOURCE_HOST_ONLY", "patch_sha256": pins["patch"]["sha256"],
                   "checker_sha256": pins["checker"]["sha256"], "fixture_sha256": pins["fixture"]["sha256"],
                   "output_sha256": {n.READY: pins["candidate"]["sha256"]},
                   "baseline_sha256": {Path(n.READY).name: n.BASE_READY},
                   "generated_fixture_sha256": "a" * 64,
                   "toolchain": {"compiler_argv": ["cc"], "compiler_path": "/usr/bin/cc", "compiler_version": "synthetic cc",
                                 "compiler_target": "synthetic-target", "host": {"system": "Darwin", "machine": "arm64"}},
                   "results": [{"mode": mode, "status": "PASS", "returncode": 0,
                                "output": "PASS: 200 actual-source cases; synthetic contract"} for mode in ("optimized", "sanitized")],
                   "sensitivity_tests": [{"name": f"defect-{i}", "status": "DETECTED_EXPECTED_FAILURE", "returncode": -6} for i in range(12)]}
        receipts = []
        for host, machine in (("Darwin", "arm64"), ("Linux", "x86_64")):
            row = copy.deepcopy(receipt)
            row["toolchain"]["host"] = {"system": host, "machine": machine}
            row["toolchain"]["compiler_version"] += "-" + host
            path = self.file("inputs/" + host + ".json", json.dumps(row).encode())
            receipts.append({"path": path.relative_to(self.root).as_posix(), "sha256": n.sha(path)})
        review = {"schema": 1, "target": n.TARGET, "release": n.RELEASE, "final_primary_review": True,
                  "verdict": "APPROVED_FOR_NATIVE_BUILD_ONLY", "unresolved_findings": [], "pins": pins,
                  "host_cases": 200, "sensitivity_count": 12, "host_receipts": receipts}
        path = self.file("review.json", json.dumps(review).encode())
        return review, files, path

    def read_review(self, review, files, path, final=True):
        path.write_text(json.dumps(review))
        with mock.patch.object(n, "__file__", str(files["native_tool"])):
            return n.review_manifest(self.root, path, n.sha(path), final)

    def edit_receipt(self, review, index, edit):
        row = review["host_receipts"][index]
        path = self.root / row["path"]
        receipt = n.json_read(path)
        edit(receipt)
        path.write_text(json.dumps(receipt))
        row["sha256"] = n.sha(path)

    def test_distinct_compilers_same_semantics_accepted(self):
        review, files, path = self.review_fixture()
        self.assertNotEqual(review["host_receipts"][0]["sha256"], review["host_receipts"][1]["sha256"])
        _, _, result = self.read_review(review, files, path)
        self.assertTrue(result["approved_for_native_build_only"])

    def test_draft_allows_validation_but_not_prepare(self):
        review, files, path = self.review_fixture()
        review.update(final_primary_review=False, verdict="PENDING")
        self.assertFalse(self.read_review(review, files, path, final=False)[2]["approved_for_native_build_only"])
        with self.assertRaisesRegex(RuntimeError, "final primary review"):
            self.read_review(review, files, path)

    def test_unresolved_finding_refuses_freeze(self):
        review, files, path = self.review_fixture()
        review["unresolved_findings"] = ["F1"]
        with self.assertRaisesRegex(RuntimeError, "final primary review"):
            self.read_review(review, files, path)

    def test_wrong_primary_digest(self):
        _, files, path = self.review_fixture()
        with mock.patch.object(n, "__file__", str(files["native_tool"])):
            with self.assertRaisesRegex(RuntimeError, "manifest digest"):
                n.review_manifest(self.root, path, "0" * 64)

    def test_missing_pin(self):
        review, files, path = self.review_fixture()
        del review["pins"]["design"]
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.read_review(review, files, path)

    def test_changed_reviewed_bytes(self):
        review, files, path = self.review_fixture()
        files["fixture"].write_bytes(b"different fixture")
        with self.assertRaisesRegex(RuntimeError, "pin mismatch"):
            self.read_review(review, files, path)

    def test_patch_extra_file_refused(self):
        review, files, path = self.review_fixture()
        with files["patch"].open("a") as stream:
            stream.write("diff --git a/other.c b/other.c\n--- a/other.c\n+++ b/other.c\n")
        review["pins"]["patch"]["sha256"] = n.sha(files["patch"])
        with self.assertRaisesRegex(RuntimeError, "exactly one ready.c"):
            self.read_review(review, files, path)

    def test_patch_mode_change_refused(self):
        review, files, path = self.review_fixture()
        with files["patch"].open("a") as stream:
            stream.write("old mode 100644\nnew mode 100755\n")
        review["pins"]["patch"]["sha256"] = n.sha(files["patch"])
        with self.assertRaisesRegex(RuntimeError, "file kind/mode"):
            self.read_review(review, files, path)

    def test_wrong_host_receipt_candidate(self):
        review, files, path = self.review_fixture()
        self.edit_receipt(review, 1, lambda r: r["output_sha256"].update({n.READY: "b" * 64}))
        with self.assertRaisesRegex(RuntimeError, "candidate mismatch"):
            self.read_review(review, files, path)

    def test_wrong_host_receipt_checker(self):
        review, files, path = self.review_fixture()
        self.edit_receipt(review, 1, lambda r: r.update(checker_sha256="b" * 64))
        with self.assertRaisesRegex(RuntimeError, "input mismatch"):
            self.read_review(review, files, path)

    def test_missing_compiler_identity(self):
        review, files, path = self.review_fixture()
        self.edit_receipt(review, 1, lambda r: r["toolchain"].pop("compiler_version"))
        with self.assertRaisesRegex(RuntimeError, "compiler identity"):
            self.read_review(review, files, path)

    def test_two_copies_of_one_host_are_not_cross_host_evidence(self):
        review, files, path = self.review_fixture()
        review["host_receipts"][1] = review["host_receipts"][0]
        with self.assertRaisesRegex(RuntimeError, "both Mac and Linux"):
            self.read_review(review, files, path)

    def test_linux_wrong_architecture(self):
        review, files, path = self.review_fixture()
        self.edit_receipt(review, 1, lambda r: r["toolchain"]["host"].update(machine="aarch64"))
        with self.assertRaisesRegex(RuntimeError, "must be x86-64"):
            self.read_review(review, files, path)

    def test_missing_sanitizer_or_wrong_count(self):
        review, files, path = self.review_fixture()
        original = copy.deepcopy(review)
        self.edit_receipt(review, 1, lambda r: r["results"].pop())
        with self.assertRaisesRegex(RuntimeError, "sanitizer"):
            self.read_review(review, files, path)
        review = original
        review["host_cases"] = 201
        with self.assertRaisesRegex(RuntimeError, "case count"):
            self.read_review(review, files, path)

    def test_surviving_or_missing_mutant(self):
        review, files, path = self.review_fixture()
        self.edit_receipt(review, 1, lambda r: r["sensitivity_tests"][0].update(returncode=0))
        with self.assertRaisesRegex(RuntimeError, "every defect"):
            self.read_review(review, files, path)
        self.edit_receipt(review, 1, lambda r: r["sensitivity_tests"].pop())
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.read_review(review, files, path)

    def test_different_generated_fixture(self):
        review, files, path = self.review_fixture()
        self.edit_receipt(review, 1, lambda r: r.update(generated_fixture_sha256="c" * 64))
        with self.assertRaisesRegex(RuntimeError, "identical generated"):
            self.read_review(review, files, path)

    def test_config_only_localversion(self):
        old = self.file("old.config", b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-data-control-dev1"\nCONFIG_EXTRA_FIRMWARE=""\nCONFIG_INITRAMFS_SOURCE=""\nCONFIG_FRAME_WARN=2048\n')
        new = self.file("new.config", old.read_bytes().replace(b"uvd-data-control", b"uvd-status-observation"))
        n.config_contract(old, new, self.builder.config_values)
        new.write_text(new.read_text().replace("2048", "4096"))
        with self.assertRaisesRegex(RuntimeError, "beyond LOCALVERSION"):
            n.config_contract(old, new, self.builder.config_values)

    def test_embedded_firmware_refused_even_if_baseline_contains_it(self):
        old = self.file("old.config", b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-data-control-dev1"\nCONFIG_EXTRA_FIRMWARE="bad.bin"\n')
        new = self.file("new.config", old.read_bytes().replace(b"uvd-data-control", b"uvd-status-observation"))
        with self.assertRaisesRegex(RuntimeError, "embedded firmware"):
            n.config_contract(old, new, self.builder.config_values)

    def test_only_ready_source_change(self):
        base = {n.READY: {"kind": "file", "sha256": n.BASE_READY, "mode": 420},
                "other.c": {"kind": "file", "sha256": "a" * 64, "mode": 420}}
        actual = copy.deepcopy(base)
        actual[n.READY]["sha256"] = "b" * 64
        n.source_contract(base, actual, "b" * 64)
        actual["other.c"]["sha256"] = "c" * 64
        with self.assertRaisesRegex(RuntimeError, "unexpected source delta"):
            n.source_contract(base, actual, "b" * 64)
        del actual["other.c"]
        with self.assertRaisesRegex(RuntimeError, "path set"):
            n.source_contract(base, actual, "b" * 64)

    def test_independent_copy_and_shared_inode_detection(self):
        base = self.root / "baseline"
        self.file("baseline/file", b"frozen")
        before = self.prep.snapshot_tree(base)
        copied = self.root / "copy"
        shutil.copytree(base, copied)
        self.prep.inspect_independence(before, copied)
        (copied / "file").write_bytes(b"new data")
        self.assertEqual(self.prep.snapshot_tree(base), before)
        (copied / "file").unlink()
        os.link(base / "file", copied / "file")
        with self.assertRaisesRegex(RuntimeError, "shared baseline inode"):
            self.prep.inspect_independence(before, copied)

    def test_alternates_and_escaping_link_refused(self):
        candidate = self.root / "copy"
        candidate.mkdir()
        (candidate / "escaped").symlink_to(self.root)
        with self.assertRaisesRegex(RuntimeError, "escaping candidate symlink"):
            self.prep.inspect_independence({}, candidate)
        (candidate / "escaped").unlink()
        self.file("copy/.git/objects/info/alternates", b"/outside\n")
        with self.assertRaisesRegex(RuntimeError, "alternates"):
            self.prep.inspect_independence({}, candidate)

    def test_elf_machine_contract(self):
        header = bytearray(64)
        header[:6] = b"\x7fELF\x02\x01"
        header[16:18] = (1).to_bytes(2, "little")
        header[18:20] = (62).to_bytes(2, "little")
        path = self.file("module.ko", header)
        self.builder.module_elf_header(path)
        header[18:20] = (183).to_bytes(2, "little")
        path.write_bytes(header)
        with self.assertRaisesRegex(RuntimeError, "x86-64"):
            self.builder.module_elf_header(path)

    def test_cached_object_change_before_build_refused(self):
        work = n.Native(self.root, "run")
        work.source.mkdir(parents=True)
        ready = work.source / n.READY
        ready.parent.mkdir(parents=True)
        ready.write_text("old source")
        obj = ready.with_suffix(".o")
        obj.write_bytes(b"frozen object")
        expected = {name: self.prep.content_record(row) for name, row in self.prep.snapshot_tree(work.source).items()}
        n.write_json(work.logs / "copied-cache-content.json", expected)
        n.write_json(work.logs / "prepare-base.json", {"copied_cache_sha256": n.sha(work.logs / "copied-cache-content.json")})
        work.cache_contract()
        ready.write_text("reviewed source")
        work.cache_contract(n.sha(ready))
        obj.write_bytes(b"changed object")
        with self.assertRaisesRegex(RuntimeError, "cache changed before build"):
            work.cache_contract(n.sha(ready))

    def test_new_ignored_source_cache_path_refused(self):
        work = n.Native(self.root, "run")
        work.source.mkdir(parents=True)
        expected = {name: self.prep.content_record(row) for name, row in self.prep.snapshot_tree(work.source).items()}
        n.write_json(work.logs / "copied-cache-content.json", expected)
        n.write_json(work.logs / "prepare-base.json", {"copied_cache_sha256": n.sha(work.logs / "copied-cache-content.json")})
        (work.source / "ignored-added.c").write_text("unreviewed")
        with self.assertRaisesRegex(RuntimeError, "path set changed"):
            work.cache_contract()

    def phase_fixture(self):
        work = n.Native(self.root, "run", "review.json", "f" * 64)
        self.file("review.json", b"{}")
        self.file(n.BASE_REL + "/synthetic-input", b"unchanged")
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(work.prep, "native_host").start()
        mock.patch.object(work, "review", return_value=({}, {}, {})).start()
        mock.patch.object(work, "baseline", return_value=({}, {}, [])).start()
        return work

    def test_phase_failure_preserved_and_not_retried(self):
        work = self.phase_fixture()
        def fail(*args):
            (work.run / "partial").write_text("keep me")
            raise RuntimeError("synthetic copy failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic copy failure"):
            work.phase("prepare-base", None, fail)
        self.assertEqual(n.json_read(work.logs / "prepare-base.json")["status"], "FAILED_OR_INTERRUPTED")
        self.assertEqual((work.run / "partial").read_text(), "keep me")
        with self.assertRaises(FileExistsError):
            work.phase("prepare-base", None, lambda *args: self.fail("retried"))

    def test_missing_previous_phase_never_calls_action(self):
        work = self.phase_fixture()
        work.logs.mkdir(parents=True)
        n.write_json(work.logs / "identity.json", {"review_sha256": work.digest, "tool_sha256": n.sha(Path(n.__file__))})
        shutil.copy2(work.review_path, work.logs / "review.json")
        work.digest = n.sha(work.review_path)
        (work.logs / "identity.json").unlink()
        n.write_json(work.logs / "identity.json", {"review_sha256": work.digest, "tool_sha256": n.sha(Path(n.__file__))})
        with self.assertRaises(FileNotFoundError):
            work.phase("build", "prepare", lambda *args: self.fail("build ran before prepare"))
        self.assertFalse((work.logs / "build-started.json").exists())

    def test_baseline_mutation_is_a_terminal_failure(self):
        work = self.phase_fixture()
        def damage(*args):
            (work.base / "synthetic-input").write_text("changed")
            return {}
        with self.assertRaisesRegex(RuntimeError, "baseline changed"):
            work.phase("prepare-base", None, damage)
        receipt = n.json_read(work.logs / "prepare-base.json")
        self.assertEqual(receipt["status"], "FAIL_BASELINE_PRESERVATION")
        self.assertFalse(receipt["baseline_preserved"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("report already exists; preserve it and choose a new path")
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Contracts))
    receipt = {"schema": 1, "status": "PASS_SYNTHETIC_TOOLING_ONLY" if result.wasSuccessful() else "FAIL_SYNTHETIC_TOOLING",
               "host": {"system": platform.system(), "machine": platform.machine(), "python": sys.version},
               "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "log": stream.getvalue(),
               "native_tool_sha256": n.sha(HERE / "native.py"), "tests_sha256": n.sha(Path(__file__)),
               "helper_sha256": n.HELPERS, "real_native_cache_accessed": False, "kernel_built": False, "hardware_accessed": False}
    n.write_json(args.report, receipt)
    print(stream.getvalue(), end="")
    print(receipt["status"])
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
