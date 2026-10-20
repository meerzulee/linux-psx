#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Small synthetic-only checks; never invoke make, zstd, SSH or a compiler."""
from pathlib import Path
import argparse
import contextlib
import copy
import difflib
import hashlib
import importlib.util
import io
import json
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("native_build", HERE / "native_build.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def pin(data, mode=0o644):
    return {"kind": "file", "mode": mode, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def symlink(target):
    return {**pin(target.encode(), 0o777), "kind": "symlink", "target": target}


def tar_bytes(members):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, kind, data, mode in members:
            item = tarfile.TarInfo(name)
            item.mode = mode
            if kind == "file":
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
            else:
                item.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                item.linkname = data
                archive.addfile(item)
    stream.seek(0)
    return stream


def shape_fixture():
    """Non-executable in-memory schema fixture; no production manifest is saved."""
    record = lambda n, h="a" * 64: {"path": "/synthetic/" + n, "sha256": h}
    return {
        "schema": 1, "status": b.STATUS, "release": b.RELEASE,
        "base": {k: record(k, h) for k, h in b.BASE_PINS.items()},
        "patches": [record("p1", b.FIRST_PATCHES[0]), record("p2", b.FIRST_PATCHES[1]), record("p3")],
        "candidate_files": {"drivers/gpu/drm/amd/amdgpu/new.c": {
            **record("new.c"), **pin(b"new\n")}},
        "source_receipt": record("source-receipt"), "review": record("review"),
        "host_receipts": {"macos": record("mac"), "linux": record("linux")},
        "tools": {name: "b" * 64 for name in b.TOOLS},
        "protected_files": {"/synthetic/kernel-" + str(i): h
                            for i, h in enumerate(sorted(b.OLD_KERNEL_PINS))},
        "tool_sha256": "c" * 64, "minimum_free_bytes": 12 * 1024**3,
    }


class ArchiveChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source, self.metadata = self.root / "source", self.root / "metadata"
        self.source.mkdir()
        self.metadata.mkdir()
        self.inventory = {"dir/file": pin(b"content\n"), "link": symlink("dir/file")}
        self.extra = {"META": hashlib.sha256(b"metadata").hexdigest()}
        self.members = [("prefix/dir/file", "file", b"content\n", 0o644),
                        ("prefix/link", "symlink", "dir/file", 0o777),
                        ("prefix/META", "file", b"metadata", 0o644)]

    def tearDown(self):
        self.tmp.cleanup()

    def extract(self, members=None, inventory=None, extras=None):
        return b.extract_stream(tar_bytes(members if members is not None else self.members),
                                self.source, self.metadata,
                                self.inventory if inventory is None else inventory,
                                self.extra if extras is None else extras, "prefix")

    def test_exact_files_modes_links_and_metadata(self):
        result = self.extract()
        self.assertEqual(result["members"], 3)
        self.assertEqual(result["symlinks"], 1)
        self.assertEqual(b.tree(self.source), self.inventory)
        self.assertEqual((self.metadata / "META").read_bytes(), b"metadata")

    def test_traversal_absolute_and_wrong_prefix(self):
        for name in ("prefix/../escape", "/prefix/dir/file", "different/dir/file"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.extract([(name, "file", b"content\n", 0o644)])
        self.assertFalse((self.root / "escape").exists())

    def test_duplicate_member(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.extract([self.members[0], self.members[0]])

    def test_unexpected_member(self):
        with self.assertRaisesRegex(ValueError, "unexpected"):
            self.extract([("prefix/extra", "file", b"x", 0o644)])

    def test_missing_member(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            self.extract([self.members[0]])

    def test_changed_file_content(self):
        with self.assertRaisesRegex(ValueError, "content differs"):
            self.extract([("prefix/dir/file", "file", b"changed\n", 0o644)])

    def test_wrong_file_mode(self):
        with self.assertRaisesRegex(ValueError, "metadata differs"):
            self.extract([("prefix/dir/file", "file", b"content\n", 0o755)])

    def test_hardlink_rejected(self):
        with self.assertRaisesRegex(ValueError, "Non-regular"):
            self.extract([("prefix/dir/file", "hardlink", "other", 0o644)])

    def test_symlink_target_changed(self):
        with self.assertRaisesRegex(ValueError, "Symlink metadata"):
            self.extract([("prefix/link", "symlink", "different", 0o777)])

    def test_escape_in_inventory_rejected_before_extract(self):
        inventory = dict(self.inventory, link=symlink("../outside"))
        with self.assertRaisesRegex(ValueError, "Escaping"):
            self.extract(inventory=inventory)
        self.assertEqual(list(self.source.iterdir()), [])

    def test_inventory_cannot_descend_symlink(self):
        inventory = {"dir": symlink("elsewhere"), "dir/file": pin(b"x")}
        with self.assertRaisesRegex(ValueError, "descends"):
            self.extract(inventory=inventory)

    def test_metadata_content_checked(self):
        with self.assertRaisesRegex(ValueError, "content differs"):
            self.extract([("prefix/META", "file", b"wrong", 0o644)])

    def test_metadata_cannot_replace_source(self):
        with self.assertRaisesRegex(ValueError, "collision"):
            self.extract(extras={"dir/file": self.inventory["dir/file"]["sha256"]})


class ContractChecks(unittest.TestCase):
    def test_only_localversion_changes(self):
        base = (b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-live-prepare-dev1"\n'
                b'CONFIG_EXTRA_FIRMWARE=""\nCONFIG_INITRAMFS_SOURCE=""\n# CONFIG_X is not set\n')
        new = base.replace(b"uvd-live-prepare-dev1", b"uvd-frame-dev1")
        b.check_config(base, new)
        with self.assertRaisesRegex(ValueError, "beyond LOCALVERSION"):
            b.check_config(base, new.replace(b"# CONFIG_X is not set", b"CONFIG_X=y"))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            b.config_values(new + b"CONFIG_X=y\n")

    def test_review_patch_order_receipts_and_old_kernel_gates(self):
        self.assertEqual(b.RELEASE, "7.2.11-psx-ps4-baikal-uvd-frame-dev1")
        self.assertEqual(b.STATUS, "REVIEWED_FOR_PRIVATE_FRAME_BUILD")
        b.manifest_shape(shape_fixture())
        edits = (
            lambda m: m.update(status="DRAFT"),
            lambda m: m["patches"].reverse(),
            lambda m: m["host_receipts"].pop("linux"),
            lambda m: m.update(protected_files={"/synthetic/something": "f" * 64}),
            lambda m: m["base"]["config"].update(sha256="f" * 64),
            lambda m: m.update(minimum_free_bytes=1),
        )
        for edit in edits:
            m = shape_fixture()
            edit(m)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                b.manifest_shape(m)

    def test_unbound_or_out_of_scope_candidate_rejected(self):
        m = shape_fixture()
        m["candidate_files"]["drivers/gpu/drm/amd/amdgpu/new.c"]["sha256"] = "UNBOUND"
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            b.manifest_shape(m)
        m = shape_fixture()
        m["candidate_files"]["../outside"] = m["candidate_files"].pop(
            "drivers/gpu/drm/amd/amdgpu/new.c")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            b.manifest_shape(m)

    def test_exact_source_then_generated_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.c").write_bytes(b"a")
            expected = {"a.c": pin(b"a")}
            b.verify_source(root, expected, exact=True)
            (root / "a.o").write_bytes(b"generated")
            b.verify_source(root, expected)
            with self.assertRaisesRegex(ValueError, "inventory"):
                b.verify_source(root, expected, exact=True)
            (root / "a.c").write_bytes(b"b")
            with self.assertRaisesRegex(ValueError, "input changed"):
                b.verify_source(root, expected)

    def test_candidate_merge_preserves_unchanged_remainder(self):
        baseline = {"old.c": pin(b"old"), "same.h": pin(b"same")}
        expected = b.expected_source(baseline, {"old.c": pin(b"new"), "new.h": pin(b"header")})
        self.assertEqual(expected["same.h"], baseline["same.h"])
        self.assertEqual(expected["old.c"], pin(b"new"))
        self.assertEqual(baseline["old.c"], pin(b"old"))
        self.assertEqual(set(expected), {"old.c", "same.h", "new.h"})

    def test_ordered_local_toy_replay_and_unchanged_remainder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, original, copied = (root / name for name in ("source", "original", "copied"))
            for path in (source, original, copied):
                path.mkdir()
            (source / "code.c").write_text("/* synthetic A */\n")
            notice = b"synthetic notice: must remain unchanged\n"
            (source / "COPYING").write_bytes(notice)
            rows = []
            for i, (old, new) in enumerate((("A", "B"), ("B", "C"))):
                data = "diff --git a/code.c b/code.c\n" + "".join(difflib.unified_diff(
                    [f"/* synthetic {old} */\n"], [f"/* synthetic {new} */\n"],
                    fromfile="a/code.c", tofile="b/code.c"))
                p = original / (str(i) + ".patch")
                p.write_text(data)
                rows.append({"path": str(p), "sha256": b.sha(p)})
            p = original / "2.patch"
            p.write_text("diff --git a/new.h b/new.h\nnew file mode 100644\n"
                         "--- /dev/null\n+++ b/new.h\n@@ -0,0 +1 @@\n+/* synthetic header */\n")
            rows.append({"path": str(p), "sha256": b.sha(p)})
            labels = []

            def local_git(label, argv):
                labels.append(label)
                r = subprocess.run(argv, cwd=source, capture_output=True, timeout=10,
                                   env={"PATH": "/usr/bin:/bin", "LC_ALL": "C",
                                        "GIT_CEILING_DIRECTORIES": str(root),
                                        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
                b.require(r.returncode == 0, r.stderr.decode(errors="replace"))

            b.replay_patches(rows, {"code.c", "new.h"}, copied, local_git)
            expected = {"code.c": pin(b"/* synthetic C */\n"), "COPYING": pin(notice),
                        "new.h": pin(b"/* synthetic header */\n")}
            b.verify_source(source, expected, exact=True)
            self.assertEqual(labels, [f"patch-{i}-{phase}" for i in (1, 2, 3)
                                      for phase in ("check", "apply")])
            self.assertEqual((copied / "series").read_text(), "01-0.patch\n02-1.patch\n03-2.patch\n")
            for i, row in enumerate(rows, 1):
                self.assertEqual(b.sha(copied / (f"{i:02d}-" + Path(row["path"]).name)), row["sha256"])
            (source / "COPYING").write_bytes(b"changed notice\n")
            with self.assertRaisesRegex(ValueError, "Source"):
                b.verify_source(source, expected, exact=True)

    def test_reused_prepare_does_not_claim_existing_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = object.__new__(b.Workflow)
            w.work, w.owns_work = Path(tmp), False
            with self.assertRaisesRegex(ValueError, "Fresh"):
                w.prepare()
            self.assertFalse(w.owns_work)
            self.assertEqual(list(w.work.iterdir()), [])

    def test_build_requires_exact_prepare_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = object.__new__(b.Workflow)
            w.work, w.owns_work, w.manifest_sha = Path(tmp), False, "a" * 64
            w.output = w.work / "output"
            p = w.work / "PREPARE.json"
            p.write_text(json.dumps({"status": "FAILED_PRESERVED", "manifest_sha256": w.manifest_sha}))
            with self.assertRaisesRegex(ValueError, "Exact prepare"):
                w.build("b" * 64)
            with self.assertRaisesRegex(ValueError, "phase binding"):
                w.build(b.sha(p))
            self.assertFalse(w.owns_work)
            self.assertFalse((w.work / "BUILD-STARTED.json").exists())

    def test_repeated_build_refused_before_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = object.__new__(b.Workflow)
            w.work, w.owns_work, w.manifest_sha = Path(tmp), False, "a" * 64
            w.output = w.work / "output"
            p = w.work / "PREPARE.json"
            p.write_text(json.dumps({"status": "PASS_PREPARE", "manifest_sha256": w.manifest_sha}))
            (w.work / "BUILD-STARTED.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "already attempted"):
                w.build(b.sha(p))
            self.assertFalse(w.owns_work)

    def test_module_elf_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.ko"
            path.write_bytes(b"\x7fELF\x02\x01" + b"\x00" * 10 + struct.pack("<HH", 1, 62))
            b.module_identity(path, b.RELEASE, b.RELEASE + " SMP preempt mod_unload")
            with self.assertRaisesRegex(ValueError, "vermagic"):
                b.module_identity(path, b.RELEASE, b.OLD_RELEASE + " SMP")
            path.write_bytes(path.read_bytes()[:18] + struct.pack("<H", 183))
            with self.assertRaisesRegex(ValueError, "ELF"):
                b.module_identity(path, b.RELEASE, b.RELEASE)

    def test_exact_matching_41_module_names(self):
        baseline = {f"lib/modules/{b.OLD_RELEASE}/kernel/test/{i}.ko" for i in range(41)}
        actual = {name.replace(b.OLD_RELEASE, b.RELEASE) for name in baseline}
        b.matching_module_names(actual, baseline)
        with self.assertRaisesRegex(ValueError, "Matching"):
            b.matching_module_names(set(sorted(actual)[:-1]), baseline)
        changed = set(actual)
        changed.remove(sorted(actual)[0])
        changed.add(f"lib/modules/{b.RELEASE}/kernel/unexpected.ko")
        with self.assertRaisesRegex(ValueError, "Matching"):
            b.matching_module_names(changed, baseline)

    def test_failed_owned_phase_keeps_evidence_and_failure_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work"

            class SyntheticWorkflow:
                manifest_sha = "a" * 64
                env, commands, m = {}, [], {"tools": {}}
                work, owns_work = root, False

                def __init__(self, *args):
                    pass

                def prepare(self):
                    self.work.mkdir()
                    self.owns_work = True
                    (self.work / "saved-evidence.log").write_bytes(b"keep this failure\n")
                    raise ValueError("synthetic prepare failure")

                def check_inputs(self):
                    pass

            argv = ["native_build.py", "prepare", "--manifest", "/synthetic/not-a-production-manifest",
                    "--manifest-sha256", "a" * 64]
            with patch.object(b, "Workflow", SyntheticWorkflow), patch.object(sys, "argv", argv):
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "synthetic"):
                    b.main()
            self.assertEqual((root / "saved-evidence.log").read_bytes(), b"keep this failure\n")
            result = b.json_read(root / "PREPARE.json")
            self.assertEqual(result["status"], "FAILED_PRESERVED")
            self.assertTrue(result["base_and_frozen_inputs_preserved"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__)))
    report = {"status": "PASS_SYNTHETIC_NATIVE_WORKFLOW" if result.wasSuccessful() else "FAIL",
              "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "native_build_py_sha256": b.sha(HERE / "native_build.py"),
              "native_test_py_sha256": b.sha(Path(__file__)),
              "derived_from_native_build_py_sha256": "771a180aa54348271a659f2df2cb59e00b974382dd0ef9282b4d5241a2e6e5a5",
              "derived_from_native_test_py_sha256": "16b3b5367b23b49f1e644af4d93a9b9d73f611bf855a7545c8ea34d0b7158554",
              "adaptation": "Frame release/status/LOCALVERSION and wording; immutable-base/build contracts retained",
              "native_commands_run": False, "production_archive_or_device_access": False,
              "local_git_toy_patch_replay": True}
    b.save(args.report, report)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
