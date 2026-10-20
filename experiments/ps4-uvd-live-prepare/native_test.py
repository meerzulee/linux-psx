#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Small offline orchestration contracts; no kernel build, archive or devices."""

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
WORKSPACE = HERE.parents[2]
spec = importlib.util.spec_from_file_location("live_prepare_native", HERE / "native.py")
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)
TMP_ROOT = None


class Contracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="native-synthetic-", dir=TMP_ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.prep, self.build, self.package = n.load_helpers()

    def file(self, name, data=b"synthetic"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def review_fixture(self):
        # Only the small selected-file snapshot is copied; no full source/cache.
        original_path = WORKSPACE / "lab/uvd-2026-10-20/live-prepare/review/PRIMARY-SOURCE-REVIEW.json"
        review = n.json_read(original_path)
        original_candidate = WORKSPACE / review["candidate"]
        candidate = self.root / "source/candidate"
        candidate.mkdir(parents=True)
        for name in review["source_sha256"]:
            self.file("source/candidate/" + n.PREFIX + name,
                      (original_candidate / n.PREFIX / name).read_bytes())
        for name in ("SOURCE-RECEIPT.json", n.PATCH):
            self.file("source/" + name, (original_candidate.parent / name).read_bytes())
        tools_dir = self.root / "tools"
        for name in n.INPUT_FILES.values():
            self.file("tools/" + name, (HERE / name).read_bytes())
        receipt_rows = []
        for index, row in enumerate(review["host_receipts"]):
            path = self.file(f"hosts/{index}.json", (WORKSPACE / row["path"]).read_bytes())
            receipt_rows.append({"path": path.relative_to(self.root).as_posix(), "sha256": n.sha(path)})
        review.update(candidate="source/candidate", host_receipts=receipt_rows)
        review_path = self.file("review.json", json.dumps(review).encode())
        return review, tools_dir, review_path

    def read_review(self, review, tools_dir, path, final=True):
        path.write_text(json.dumps(review))
        with mock.patch.object(n, "HERE", tools_dir):
            return n.review_manifest(self.root, path, n.sha(path), final)

    def edit_host(self, review, index, transform):
        row = review["host_receipts"][index]
        path = self.root / row["path"]
        receipt = n.json_read(path)
        transform(receipt)
        path.write_text(json.dumps(receipt))
        row["sha256"] = n.sha(path)

    def test_relative_path_escape_and_symlink_rejection(self):
        for name in ("", "/absolute", "../up", "a/../b", "a//b", "a\nb", "a\0b"):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                n.relative(self.root, name)
        (self.root / "linked").symlink_to(self.root)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            n.relative(self.root, "linked/child")

    def test_pinned_file_digest_is_enforced(self):
        self.file("data")
        with self.assertRaisesRegex(RuntimeError, "pin mismatch"):
            n.pin_file(self.root, {"path": "data", "sha256": "0" * 64})

    def test_duplicate_json_keys_rejected(self):
        path = self.file("bad.json", b'{"a": 1, "a": 2}')
        with self.assertRaisesRegex(RuntimeError, "duplicate JSON"):
            n.json_read(path)

    def test_receipt_creation_is_exclusive(self):
        path = self.root / "receipt.json"
        n.write_json(path, {"first": True})
        with self.assertRaises(FileExistsError):
            n.write_json(path, {"first": False})
        self.assertEqual(n.json_read(path), {"first": True})

    def test_plan_does_not_create_run(self):
        work = n.Native(self.root, "new-run")
        self.assertEqual(work.plan()["status"], "READ_ONLY_PLAN_NOT_BUILD_APPROVAL")
        self.assertFalse(work.run.exists())

    def test_baseline_overlap_rejected(self):
        for run in (n.BASE_REL, n.BASE_REL + "/nested", "lab"):
            with self.subTest(run=run), self.assertRaisesRegex(RuntimeError, "overlaps"):
                n.Native(self.root, run)

    def test_actual_review_shape_and_both_host_receipts_accept(self):
        review, tools, path = self.review_fixture()
        normalized, _, summary = self.read_review(review, tools, path)
        self.assertTrue(summary["approved_for_native_build_only"])
        self.assertEqual(set(normalized["source_pins"]), n.SELECTED)
        self.assertEqual(len(normalized["source_pins"]), 16)

    def test_root_review_verdict_required(self):
        review, tools, path = self.review_fixture()
        review["status"] = "PENDING"
        with self.assertRaisesRegex(RuntimeError, "final root source"):
            self.read_review(review, tools, path)

    def test_root_must_fix_is_blocking(self):
        review, tools, path = self.review_fixture()
        review["must_fix"] = ["unresolved cleanup"]
        with self.assertRaisesRegex(RuntimeError, "final root source"):
            self.read_review(review, tools, path)

    def test_review_digest_is_enforced(self):
        _, tools, path = self.review_fixture()
        with mock.patch.object(n, "HERE", tools), self.assertRaisesRegex(RuntimeError, "review digest"):
            n.review_manifest(self.root, path, "0" * 64)

    def test_source_receipt_hash_is_enforced(self):
        review, tools, path = self.review_fixture()
        self.file("source/SOURCE-RECEIPT.json", b"changed")
        with self.assertRaisesRegex(RuntimeError, "source receipt changed"):
            self.read_review(review, tools, path)

    def test_changed_selected_source_rejected(self):
        review, tools, path = self.review_fixture()
        self.file("source/candidate/" + n.READY, b"changed")
        with self.assertRaisesRegex(RuntimeError, "pin mismatch"):
            self.read_review(review, tools, path)

    def test_changed_generator_input_rejected(self):
        review, tools, path = self.review_fixture()
        (tools / "live_prepare.c.inc").write_text("changed")
        with self.assertRaisesRegex(RuntimeError, "generator input changed"):
            self.read_review(review, tools, path)

    def test_changed_checker_rejected(self):
        review, tools, path = self.review_fixture()
        (tools / "check.py").write_text("changed")
        with self.assertRaisesRegex(RuntimeError, "host input changed"):
            self.read_review(review, tools, path)

    def test_missing_host_receipt_rejected(self):
        review, tools, path = self.review_fixture()
        review["host_receipts"].pop()
        with self.assertRaisesRegex(RuntimeError, "two pinned host"):
            self.read_review(review, tools, path)

    def test_two_same_hosts_rejected(self):
        review, tools, path = self.review_fixture()
        review["host_receipts"][1] = review["host_receipts"][0]
        with self.assertRaisesRegex(RuntimeError, "both Mac and Linux"):
            self.read_review(review, tools, path)

    def test_failed_host_receipt_rejected(self):
        review, tools, path = self.review_fixture()
        self.edit_host(review, 1, lambda r: r.update(status="FAIL"))
        with self.assertRaisesRegex(RuntimeError, "did not pass"):
            self.read_review(review, tools, path)

    def test_host_source_mismatch_rejected(self):
        review, tools, path = self.review_fixture()
        self.edit_host(review, 1, lambda r: r["source_sha256"].update({"amdgpu.h": "0" * 64}))
        with self.assertRaisesRegex(RuntimeError, "candidate scope or identity"):
            self.read_review(review, tools, path)

    def test_missing_sanitizer_rejected(self):
        review, tools, path = self.review_fixture()
        self.edit_host(review, 1, lambda r: r["runs"].pop("sanitized"))
        with self.assertRaisesRegex(RuntimeError, "sanitizer"):
            self.read_review(review, tools, path)

    def test_reduced_case_coverage_rejected(self):
        review, tools, path = self.review_fixture()
        self.edit_host(review, 1, lambda r: r["runs"]["optimized"].update(cases=76))
        with self.assertRaisesRegex(RuntimeError, "coverage incomplete"):
            self.read_review(review, tools, path)

    def test_wrong_mutant_witness_rejected(self):
        review, tools, path = self.review_fixture()
        self.edit_host(review, 1, lambda r: r["sensitivity"][0].update(witness="unrelated failure"))
        with self.assertRaisesRegex(RuntimeError, "different reason"):
            self.read_review(review, tools, path)

    def test_missing_mutant_rejected(self):
        review, tools, path = self.review_fixture()
        self.edit_host(review, 1, lambda r: r["sensitivity"].pop())
        with self.assertRaisesRegex(RuntimeError, "mutation set"):
            self.read_review(review, tools, path)

    def test_cross_host_execution_disagreement_rejected(self):
        review, tools, path = self.review_fixture()
        def edit(receipt):
            for row in receipt["runs"].values():
                row["cases"] = 78
        self.edit_host(review, 1, edit)
        with self.assertRaisesRegex(RuntimeError, "cross-host evidence"):
            self.read_review(review, tools, path)

    def test_arm_linux_receipt_rejected(self):
        review, tools, path = self.review_fixture()
        self.edit_host(review, 1, lambda r: r["toolchain"]["host"].update(machine="aarch64"))
        with self.assertRaisesRegex(RuntimeError, "must be x86-64"):
            self.read_review(review, tools, path)

    def test_patch_scope_and_mode_changes_rejected(self):
        text = (HERE / n.PATCH).read_text()
        n.patch_contract(text)
        with self.assertRaisesRegex(RuntimeError, "eleven source"):
            n.patch_contract(text + "diff --git a/other b/other\n--- a/other\n+++ b/other\n")
        with self.assertRaisesRegex(RuntimeError, "file type/mode"):
            n.patch_contract(text + "old mode 100644\nnew mode 100755\n")

    def test_only_localversion_config_change_accepted(self):
        old = self.file("old.config", b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-ib-data-dev1"\nCONFIG_EXTRA_FIRMWARE=""\nCONFIG_DEBUG_FS=y\n')
        new = self.file("new.config", old.read_bytes().replace(b"uvd-ib-data-dev1", b"uvd-live-prepare-dev1"))
        n.config_contract(old, new, self.build.config_values)
        new.write_text(new.read_text().replace("CONFIG_DEBUG_FS=y", "CONFIG_DEBUG_FS=n"))
        with self.assertRaisesRegex(RuntimeError, "beyond LOCALVERSION"):
            n.config_contract(old, new, self.build.config_values)

    def test_embedded_firmware_rejected(self):
        old = self.file("old", b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-ib-data-dev1"\nCONFIG_EXTRA_FIRMWARE="blob"\n')
        new = self.file("new", old.read_bytes().replace(b"uvd-ib-data-dev1", b"uvd-live-prepare-dev1"))
        with self.assertRaisesRegex(RuntimeError, "embedded firmware"):
            n.config_contract(old, new, self.build.config_values)

    def source_records(self):
        previous = {name: {"kind": "file", "mode": 0o644, "sha256": digest, "bytes": 1}
                    for name, digest in n.BASE_SOURCE.items()}
        previous["elsewhere.c"] = {"kind": "file", "mode": 0o644, "sha256": "1" * 64, "bytes": 1}
        actual = copy.deepcopy(previous)
        for index, name in enumerate(sorted(n.CHANGED)):
            actual[name] = {"kind": "file", "mode": 0o644, "sha256": f"{index + 100:064x}", "bytes": 2}
        pins = {name: actual[name]["sha256"] for name in n.SELECTED}
        return previous, actual, pins

    def test_full_inventory_only_declared_delta(self):
        before, after, pins = self.source_records()
        n.source_contract(before, after, pins)
        after["elsewhere.c"]["sha256"] = "2" * 64
        with self.assertRaisesRegex(RuntimeError, "unexpected source delta"):
            n.source_contract(before, after, pins)

    def test_full_inventory_extra_path_rejected(self):
        before, after, pins = self.source_records()
        after["extra.c"] = copy.deepcopy(after["elsewhere.c"])
        with self.assertRaisesRegex(RuntimeError, "source path set"):
            n.source_contract(before, after, pins)

    def test_changed_source_mode_rejected(self):
        before, after, pins = self.source_records()
        after[n.LIVE_H]["mode"] = 0o755
        with self.assertRaisesRegex(RuntimeError, "source mode"):
            n.source_contract(before, after, pins)

    def test_hardlink_and_alternates_rejected(self):
        old = self.file("base/file")
        previous = self.prep.snapshot_tree(old.parent)
        candidate = self.root / "copy"
        candidate.mkdir()
        os.link(old, candidate / "file")
        with self.assertRaisesRegex(RuntimeError, "shared baseline inode"):
            self.prep.inspect_independence(previous, candidate)
        (candidate / "file").unlink()
        shutil.copy2(old, candidate / "file")
        self.prep.inspect_independence(previous, candidate)
        self.file("copy/.git/objects/info/alternates", b"/somewhere")
        with self.assertRaisesRegex(RuntimeError, "alternates forbidden"):
            self.prep.inspect_independence(previous, candidate)

    def test_no_action_without_tool_digest(self):
        work = n.Native(self.root, "new-run")
        with mock.patch.object(work.prep, "native_host"), self.assertRaisesRegex(RuntimeError, "tool-sha256"):
            work.phase("prepare-base", None, lambda *a: self.fail("action ran"))
        self.assertFalse(work.run.exists())

    def test_no_action_without_review(self):
        work = n.Native(self.root, "new-run", tool_digest=n.sha(Path(n.__file__)))
        with mock.patch.object(work.prep, "native_host"), self.assertRaisesRegex(RuntimeError, "review path"):
            work.phase("prepare-base", None, lambda *a: self.fail("action ran"))
        self.assertFalse(work.run.exists())

    def test_failure_preserves_attempt_and_receipt(self):
        review_path = self.file("review.json", b"{}")
        work = n.Native(self.root, "run", "review.json", n.sha(review_path), n.sha(Path(n.__file__)))
        work.base.mkdir(parents=True)
        def fail(*args):
            self.file("run/partial", b"retained")
            raise RuntimeError("synthetic interrupted operation")
        with mock.patch.object(work.prep, "native_host"), \
             mock.patch.object(work, "review", return_value=({"pins": {}}, {}, {})), \
             mock.patch.object(work, "baseline"):
            with self.assertRaisesRegex(RuntimeError, "synthetic interrupted"):
                work.phase("prepare-base", None, fail)
            self.assertEqual((work.run / "partial").read_bytes(), b"retained")
            self.assertEqual(n.json_read(work.logs / "prepare-base.json")["status"], "FAILED_OR_INTERRUPTED")
            with self.assertRaises(FileExistsError):
                work.phase("prepare-base", None, fail)

    def test_failed_command_keeps_log(self):
        log = self.root / "command.log"
        with mock.patch.object(n.subprocess, "run", return_value=mock.Mock(returncode=7)), \
             self.assertRaisesRegex(RuntimeError, "preserved log"):
            n.run_command(["/usr/bin/false"], self.root, log)
        self.assertIn('/usr/bin/false', log.read_text())

    def test_job_limit_before_build_work(self):
        work = n.Native(self.root, "run")
        for jobs in (0, 7, -1):
            with self.subTest(jobs=jobs), mock.patch.object(work, "validate_prepared") as validation:
                with self.assertRaisesRegex(RuntimeError, "jobs must"):
                    work.build({}, {}, {}, jobs)
                validation.assert_not_called()

    def test_native_host_and_environment_contract(self):
        self.assertEqual(n.ENV["PATH"], "/usr/bin:/bin")
        self.assertEqual(self.prep.ENV, n.ENV)
        for system, machine in (("Darwin", "arm64"), ("Linux", "aarch64")):
            with self.subTest(system=system, machine=machine), \
                 mock.patch.object(self.prep.platform, "system", return_value=system), \
                 mock.patch.object(self.prep.platform, "machine", return_value=machine), \
                 self.assertRaisesRegex(RuntimeError, "native Linux x86-64"):
                self.prep.native_host()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.report.mkdir(parents=True, exist_ok=False)
    global TMP_ROOT
    TMP_ROOT = str(args.report.resolve())
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(Contracts))
    (args.report / "tests.log").write_text(log.getvalue())
    report = {"status": "PASS_SYNTHETIC_NATIVE_TOOL_CONTRACTS_NO_BUILD_NO_DEVICES"
              if result.wasSuccessful() else "FAIL_SYNTHETIC_NATIVE_TOOL_CONTRACTS",
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "host": {"system": platform.system(), "machine": platform.machine()},
              "native_sha256": n.sha(HERE / "native.py"), "test_sha256": n.sha(Path(__file__)),
              "log_sha256": n.sha(args.report / "tests.log"),
              "kernel_built": False, "full_tree_copied": False, "artifacts_packaged": False,
              "hardware_actions": False}
    (args.report / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not result.wasSuccessful():
        print(log.getvalue())
        raise SystemExit(1)


if __name__ == "__main__":
    main()
