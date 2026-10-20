#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded synthetic packaging tests. Never invoke zstd, make, SSH or hardware."""
from pathlib import Path
from types import SimpleNamespace
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BUILD_TOOL = HERE / "native_build.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p = load("native_package", HERE / "native_package.py")
b = load("native_build", BUILD_TOOL)


def pin(data, mode=0o644):
    return {"kind": "file", "mode": mode, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def symlink(target):
    return {**pin(target.encode(), 0o777), "kind": "symlink", "target": target}


def raw_tar(rows):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, kind, data, mode in rows:
            item = tarfile.TarInfo(name)
            item.mode = mode
            if kind == "file":
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
            else:
                item.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
                             "directory": tarfile.DIRTYPE}[kind]
                item.linkname = data
                archive.addfile(item)
    stream.seek(0)
    return stream


class ArchiveChecks(unittest.TestCase):
    def setUp(self):
        self.rows = [("root/LICENSE", "file", b"synthetic notice\n", 0o644),
                     ("root/run", "file", b"synthetic executable\n", 0o755),
                     ("root/link", "symlink", "LICENSE", 0o777)]
        self.inventory = {"LICENSE": pin(self.rows[0][2]), "run": pin(self.rows[1][2], 0o755),
                          "link": symlink("LICENSE")}

    def verify(self, rows=None, inventory=None, **kwargs):
        return p.verify_tar_stream(raw_tar(self.rows if rows is None else rows), "root",
                                   self.inventory if inventory is None else inventory, **kwargs)

    def test_exact_paths_modes_contents_and_links(self):
        result = self.verify()
        self.assertEqual((result["members"], result["symlinks"]), (3, 1))

    def test_missing_extra_duplicate_and_wrong_prefix(self):
        cases = [self.rows[:-1], self.rows + [("root/extra", "file", b"x", 0o644)],
                 self.rows + [self.rows[0]], [("wrong/LICENSE", *self.rows[0][1:])]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.verify(rows)

    def test_wrong_bytes_same_length_wrong_mode_and_wrong_link_target(self):
        edits = [(0, ("root/LICENSE", "file", b"Xynthetic notice\n", 0o644)),
                 (1, ("root/run", "file", self.rows[1][2], 0o644)),
                 (2, ("root/link", "symlink", "run", 0o777))]
        for index, row in edits:
            rows = list(self.rows)
            rows[index] = row
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.verify(rows)

    def test_hardlink_directory_and_file_replacing_symlink(self):
        for kind, data in (("hardlink", "root/LICENSE"), ("directory", ""),
                           ("file", b"LICENSE")):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.verify([*self.rows[:2], ("root/link", kind, data, 0o777)])

    def test_modules_forbid_all_links(self):
        with self.assertRaisesRegex(ValueError, "Invalid inventory"):
            self.verify(links=False)

    def test_path_traversal_absolute_and_noncanonical(self):
        for name in ("root/../outside", "/root/LICENSE", "root/./LICENSE", "root//LICENSE"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.verify([(name, "file", b"x", 0o644)])

    def test_inventory_escaping_link_and_descendant_rejected(self):
        for inventory in ({"x": symlink("../escape")},
                          {"x": symlink("LICENSE"), "x/child": pin(b"child")},
                          {"x": {**symlink("LICENSE"), "sha256": "0" * 64}}):
            with self.subTest(inventory=inventory), self.assertRaises(ValueError):
                self.verify(inventory=inventory)

    def test_writer_is_inventory_only_and_roundtrips_long_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "LICENSE").write_bytes(b"synthetic notice\n")
            (root / "generated.o").write_bytes(b"must be excluded")
            long_name = "long/" + "x" * 180
            (root / long_name).parent.mkdir()
            (root / long_name).write_bytes(b"long path")
            (root / "link").symlink_to("LICENSE")
            inventory = {n: p.record(root / n) for n in ("LICENSE", "link", long_name)}
            stream = io.BytesIO()
            identity = p.write_tar_stream(stream, "root", inventory, {n: root / n for n in inventory})
            stream.seek(0)
            result = p.verify_tar_stream(stream, "root", inventory, identity)
            self.assertEqual(result["members"], 3)
            self.assertEqual((root / "generated.o").read_bytes(), b"must be excluded")

    def test_whole_tar_identity_detects_trailing_bytes(self):
        stream = raw_tar(self.rows)
        raw = stream.read()
        identity = {"tar_bytes": len(raw), "tar_sha256": hashlib.sha256(raw).hexdigest()}
        p.verify_tar_stream(io.BytesIO(raw), "root", self.inventory, identity)
        with self.assertRaisesRegex(ValueError, "Complete tar stream"):
            p.verify_tar_stream(io.BytesIO(raw + b"unexpected trailer"), "root", self.inventory, identity)

    def test_writer_rejects_changed_input_and_mapping_addition(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input"
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "input differs"):
                p.write_tar_stream(io.BytesIO(), "root", {"input": pin(b"original")}, {"input": path})
            with self.assertRaisesRegex(ValueError, "path set differs"):
                p.write_tar_stream(io.BytesIO(), "root", {"input": pin(b"changed")},
                                   {"input": path, "extra": path})


def fake_archive(zstd, destination, prefix, inventory, paths, logs, links=True):
    """Exercise the real tar writer/readback with no subprocess or compression."""
    with destination.open("xb") as out:
        identity = p.write_tar_stream(out, prefix, inventory, paths)
    with destination.open("rb") as inp:
        readback = p.verify_tar_stream(inp, prefix, inventory, identity, links=links)
    return {"sha256": p.sha(destination), "readback": readback, "synthetic_uncompressed_test_only": True}


class PackageChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.work = self.root / "build"
        self.source = self.work / "source"
        self.native_output = self.work / "output"
        for directory in (self.source, self.native_output, self.work / "patches",
                          self.work / "base-metadata", self.work / "inputs", self.work / "logs"):
            directory.mkdir(parents=True, exist_ok=True)
        base_config = (b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-live-prepare-dev1"\n'
                       b'CONFIG_EXTRA_FIRMWARE=""\nCONFIG_INITRAMFS_SOURCE=""\n')
        built_config = base_config.replace(b.OLD_RELEASE.removeprefix("7.2.11").encode(),
                                           b.RELEASE.removeprefix("7.2.11").encode())
        (self.source / ".config").write_bytes(built_config)
        (self.source / "COPYING").write_bytes(b"synthetic original notices\n")
        (self.source / "code.c").write_bytes(b"synthetic complete source\n")
        (self.source / "link").symlink_to("COPYING")
        inventory = p.tree(self.source)
        self.expected = {**inventory, ".config": pin(base_config)}
        # A generated file in the native build must never enter corresponding source.
        (self.source / "generated.o").write_bytes(b"generated object\n")
        (self.source / "vmlinux").write_bytes(b"synthetic kernel ELF identity\n")
        for name, data in (("bzImage", b"synthetic compressed kernel\n"),
                           ("System.map", b"synthetic map\n"), ("config", built_config),
                           ("version.txt", (b.RELEASE + "\n").encode())):
            (self.native_output / name).write_bytes(data)
        module_root = self.native_output / "modules"
        module_root.mkdir()
        names = [f"lib/modules/{b.RELEASE}/kernel/toy/module-{i:02d}.ko" for i in range(41)]
        elf = b"\x7fELF\x02\x01" + b"\0" * 10 + struct.pack("<HH", 1, 62)
        for name in names:
            path = module_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(elf + name.encode())
        dep = module_root / f"lib/modules/{b.RELEASE}/modules.dep"
        dep.write_bytes(b"synthetic depmod metadata\n")
        self.module_tree = p.tree(module_root)
        self.modules = {n: {**self.module_tree[n], "vermagic": b.RELEASE + " synthetic"} for n in names}
        self.baseline = {n.replace(b.RELEASE, b.OLD_RELEASE): {} for n in names}
        self.manifest = {"tool_sha256": p.sha(BUILD_TOOL), "base": {}, "patches": [],
                         "host_receipts": {}}
        for role in ("inventory", "config", "package", "build", "compiler", "modules"):
            path = self.root / ("base-" + role)
            if role == "config":
                path.write_bytes(base_config)
            else:
                p.save(path, self.baseline if role == "modules" else {"synthetic": role})
            self.manifest["base"][role] = {"path": str(path), "sha256": p.sha(path)}
        for role in ("source_receipt", "review"):
            path = self.root / (role + ".json")
            p.save(path, {"synthetic": role})
            self.manifest[role] = {"path": str(path), "sha256": p.sha(path)}
        for host in ("macos", "linux"):
            path = self.root / (host + ".json")
            p.save(path, {"synthetic": host})
            self.manifest["host_receipts"][host] = {"path": str(path), "sha256": p.sha(path)}
        for i in range(3):
            path = self.root / f"p{i}.patch"
            path.write_text(f"synthetic patch {i}\n")
            self.manifest["patches"].append({"path": str(path), "sha256": p.sha(path)})
            p.copy_file(path, self.work / "patches" / f"{i+1:02d}-p{i}.patch")
        (self.work / "patches/series").write_text("01-p0.patch\n02-p1.patch\n03-p2.patch\n")
        self.extra_pins = {}
        for name in b.EXTRA_PINS:
            path = self.work / "base-metadata" / name
            path.write_text("synthetic lineage " + name + "\n")
            self.extra_pins[name] = p.sha(path)
        self.manifest_path = self.root / "MANIFEST.json"
        p.save(self.manifest_path, self.manifest)
        manifest_sha = p.sha(self.manifest_path)
        p.copy_file(self.manifest_path, self.work / "inputs/REVIEWED-MANIFEST.json")
        p.copy_file(BUILD_TOOL, self.work / "inputs/native_build.py")
        for name, value in (("PREPARED-SOURCE.json", self.expected), ("BUILT-SOURCE.json", inventory),
                             ("MODULES.json", self.modules), ("MODULE-TREE.json", self.module_tree)):
            p.save(self.work / name, value)
        p.save(self.work / "PREPARE.json", {"status": "PASS_PREPARE", "manifest_sha256": manifest_sha,
               "prepared_source_sha256": p.sha(self.work / "PREPARED-SOURCE.json"),
               "base_and_frozen_inputs_preserved": True})
        prepare_sha = p.sha(self.work / "PREPARE.json")
        p.save(self.work / "BUILD-STARTED.json", {"manifest_sha256": manifest_sha,
                                                 "prepare_receipt_sha256": prepare_sha})
        self.build = {"status": "PASS_BUILD", "release": b.RELEASE, "manifest_sha256": manifest_sha,
                      "previous_phase_receipt_sha256": prepare_sha,
                      "base_and_frozen_inputs_preserved": True, "hardware_acceptance": False,
                      "module_count": 41, "built_source_sha256": p.sha(self.work / "BUILT-SOURCE.json"),
                      "modules_sha256": p.sha(self.work / "MODULES.json"),
                      "module_tree_sha256": p.sha(self.work / "MODULE-TREE.json"),
                      "artifact_sha256": {name: p.sha(self.native_output / name)
                                          for name in ("bzImage", "System.map", "config", "version.txt")}}
        self.build["artifact_sha256"]["vmlinux"] = p.sha(self.source / "vmlinux")
        p.save(self.work / "BUILD.json", self.build)
        self.args = SimpleNamespace(manifest=self.manifest_path, manifest_sha256=manifest_sha,
                                    build_tool=BUILD_TOOL, build_sha256=p.sha(self.work / "BUILD.json"),
                                    output=self.root / "package", receipt=[], include_build_logs=False)
        frozen = [*self.manifest["base"].values(), *self.manifest["patches"],
                  self.manifest["source_receipt"], self.manifest["review"],
                  *self.manifest["host_receipts"].values()]
        pins = {row["path"]: row["sha256"] for row in frozen}
        pins[str(self.manifest_path)] = manifest_sha

        def check_frozen():
            for name, digest in pins.items():
                p.require(p.sha(name) == digest, "Frozen input changed")

        self.fake_workflow = SimpleNamespace(work=self.work, source=self.source, output=self.native_output,
                                             expected=self.expected, pins=pins, check_inputs=check_frozen)
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.object(p, "load_build_tool", return_value=b))
        self.stack.enter_context(patch.object(b, "Workflow", return_value=self.fake_workflow))
        self.stack.enter_context(patch.object(b, "EXTRA_PINS", self.extra_pins))
        self.stack.enter_context(patch.object(p, "archive", side_effect=fake_archive))

    def tearDown(self):
        self.stack.close()
        self.tmp.cleanup()

    def package(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return p.Package(self.args).run()

    def test_full_synthetic_pipeline_source_complete_and_all_modules(self):
        before = p.tree(self.work)
        receipt = self.package()
        self.assertEqual(receipt["status"], "PASS_PACKAGE")
        self.assertFalse(receipt["hardware_acceptance"])
        self.assertFalse(receipt["compiled_review"])
        self.assertEqual(receipt["module_count"], 41)
        metadata = p.read_json(self.args.output / "metadata/PSX-BUILD-MANIFEST.json")
        self.assertEqual(metadata["status"], "PRIVATE_EXPERIMENTAL_UVD_CORRESPONDING_SOURCE")
        self.assertEqual(metadata["release"], b.RELEASE)
        self.assertEqual(metadata["native_build_tool_sha256"], p.sha(BUILD_TOOL))
        source_inventory = p.read_json(self.args.output / "SOURCE-ARCHIVE.json")
        self.assertEqual(set(source_inventory), set(self.expected) | {
            "PSX-SOURCE-INPUTS.json", "PSX-BUILD-MANIFEST.json", "PSX-BUILD-INPUTS.tar.zst", "PSX-REBUILD.md"})
        self.assertNotIn("generated.o", source_inventory)
        self.assertNotIn("vmlinux", source_inventory)
        self.assertEqual(p.read_json(self.args.output / "MODULE-ARCHIVE.json"), self.module_tree)
        # The archive is uncompressed only in this synthetic fixture. Consumers
        # require paths directly under lib/modules/RELEASE/, with no extra root.
        with tarfile.open(self.args.output / ("modules-" + b.RELEASE + ".tar.zst"), "r:") as archive:
            members = archive.getmembers()
            self.assertEqual({item.name for item in members}, set(self.module_tree))
            self.assertEqual(sum(item.name.endswith(".ko") for item in members), 41)
            for item in members:
                self.assertTrue(item.isfile())
                self.assertEqual(item.mode, self.module_tree[item.name]["mode"])
                with archive.extractfile(item) as inp:
                    self.assertEqual(hashlib.sha256(inp.read()).hexdigest(),
                                     self.module_tree[item.name]["sha256"])
        self.assertEqual(p.tree(self.work), before)
        sums = (self.args.output / "SHA256SUMS").read_text().splitlines()
        self.assertTrue(any(line.endswith("  PACKAGE.json") for line in sums))
        for line in sums:
            digest, name = line.split("  ", 1)
            self.assertEqual(p.sha(self.args.output / name), digest)
        self.assertEqual(self.args.output.stat().st_mode & 0o777, 0o700)

    def test_phase_hash_and_failed_build_gate(self):
        self.args.build_sha256 = "0" * 64
        with self.assertRaisesRegex(ValueError, "Build receipt digest"):
            p.Package(self.args)
        self.assertFalse(self.args.output.exists())
        self.build["status"] = "FAILED_PRESERVED"
        (self.work / "BUILD.json").unlink()
        p.save(self.work / "BUILD.json", self.build)
        self.args.build_sha256 = p.sha(self.work / "BUILD.json")
        with self.assertRaisesRegex(ValueError, "Successful private build"):
            p.Package(self.args)

    def test_missing_source_is_rejected(self):
        (self.source / "COPYING").unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            p.Package(self.args)
        self.assertFalse(self.args.output.exists())

    def test_changed_source_same_size_is_rejected(self):
        path = self.source / "code.c"
        path.write_bytes(b"X" + path.read_bytes()[1:])
        with self.assertRaisesRegex(ValueError, "Source input changed"):
            p.Package(self.args)

    def test_module_tree_rejects_extra_file_and_link(self):
        path = self.native_output / "modules/extra"
        path.write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError, "module tree changed"):
            p.Package(self.args)
        path.unlink()
        path.symlink_to("missing")
        with self.assertRaisesRegex(ValueError, "module tree changed"):
            p.Package(self.args)

    def test_preserved_lineage_and_patches_are_bound(self):
        path = self.work / "base-metadata/PSX-REBUILD.md"
        path.write_bytes(b"changed lineage")
        with self.assertRaisesRegex(ValueError, "lineage differs"):
            p.Package(self.args)

    def test_extra_receipt_pin_and_copy(self):
        receipt = self.root / "tool-review.md"
        receipt.write_text("synthetic pinned review\n")
        self.args.receipt = [("tool-review.md", receipt, p.sha(receipt))]
        self.package()
        copied = self.args.output / "build-inputs/extra-receipts/tool-review.md"
        self.assertEqual(copied.read_bytes(), receipt.read_bytes())

    def test_fresh_output_required_and_prior_failure_not_overwritten(self):
        self.args.output.mkdir()
        marker = self.args.output / "PACKAGE.json"
        marker.write_text("prior failed package preserved\n")
        with self.assertRaisesRegex(ValueError, "Fresh package output"):
            p.Package(self.args)
        self.assertEqual(marker.read_text(), "prior failed package preserved\n")

    def test_archive_failure_preserves_output_and_inputs(self):
        before = p.tree(self.work)
        with patch.object(p, "archive", side_effect=ValueError("synthetic archive failure")):
            with self.assertRaisesRegex(ValueError, "synthetic archive failure"):
                self.package()
        receipt = p.read_json(self.args.output / "PACKAGE.json")
        self.assertEqual(receipt["status"], "FAILED_PRESERVED")
        self.assertFalse(receipt["packaging_complete"])
        self.assertTrue(receipt["all_build_and_frozen_inputs_preserved"])
        self.assertTrue((self.args.output / "build-inputs/receipts/BUILD.json").is_file())
        self.assertEqual(p.tree(self.work), before)
        self.assertFalse((self.args.output / "SHA256SUMS").exists())
        with self.assertRaisesRegex(ValueError, "Fresh package output"):
            p.Package(self.args)

    def test_final_input_mutation_fails_receipt(self):
        tool_review = self.root / "extra-review.json"
        tool_review.write_bytes(b"review before\n")
        self.args.receipt = [("extra-review.json", tool_review, p.sha(tool_review))]
        workflow = p.Package(self.args)
        real_build_package = workflow.build_package

        def mutate_after_packaging():
            real_build_package()
            tool_review.write_bytes(b"review after!\n")

        with patch.object(workflow, "build_package", side_effect=mutate_after_packaging):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "input changed"):
                workflow.run()
        receipt = p.read_json(self.args.output / "PACKAGE.json")
        self.assertEqual(receipt["status"], "FAILED_PRESERVED")
        self.assertFalse(receipt["packaging_complete"])
        self.assertIn("preservation_error", receipt)

    def test_checksum_failure_is_preserved_as_failure(self):
        workflow = p.Package(self.args)
        real_tree = p.tree

        def fail_final_output_tree(root):
            if root == self.args.output:
                raise OSError("synthetic checksum read failure")
            return real_tree(root)

        with patch.object(p, "tree", side_effect=fail_final_output_tree):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(OSError, "checksum read failure"):
                workflow.run()
        receipt = p.read_json(self.args.output / "PACKAGE.json")
        self.assertEqual(receipt["status"], "FAILED_PRESERVED")
        self.assertFalse(receipt["packaging_complete"])
        self.assertIn("checksum_error", receipt)


def main():
    global b, BUILD_TOOL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--build-tool", type=Path, default=BUILD_TOOL)
    args = parser.parse_args()
    BUILD_TOOL = args.build_tool.resolve(strict=True)
    b = load("native_build_for_package_tests", BUILD_TOOL)
    require_output = args.report
    p.require(not require_output.exists() and require_output.parent.is_dir(), "New report path required")
    suite = unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    p.save(args.report, {"schema": 1, "status": "PASS" if result.wasSuccessful() else "FAIL",
                        "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                        "native_package_sha256": p.sha(HERE / "native_package.py"),
                        "native_package_test_sha256": p.sha(Path(__file__)),
                        "selected_build_tool_sha256": p.sha(BUILD_TOOL),
                        "scope": "Synthetic tar/inventory/phase/preservation checks; compression mocked",
                        "external_commands": [], "console_operated": False, "hardware_acceptance": False})
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
