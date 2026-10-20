#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic temporary-filesystem checks; no subprocess, real device or sync."""
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
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("install_live", HERE / "install_live.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def put_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def tar_bytes(rows):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, kind, data, mode in rows:
            item = tarfile.TarInfo(name)
            item.mode = mode
            if kind == "file":
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
            else:
                item.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                item.linkname = data
                archive.addfile(item)
    return stream.getvalue()


class FakeHost:
    def __init__(self, root, running, contract):
        self.root, self.current = root, dict(running)
        self.replacements, self.sync_count, self.stream_count = [], 0, 0
        self.fail_second_replace = self.fail_extract_end = False
        self.after_replace = None
        self.observation = {"blocks": {"blockdevices": [{
            "path": contract["disk"]["path"], "type": "disk", "size": contract["disk"]["bytes"],
            "children": [{"path": contract[r]["source"], "type": "part", "size": contract[r]["bytes"],
                          "fstype": contract[r]["fstype"], "uuid": contract[r]["uuid"],
                          "pkname": contract["disk"]["path"], "mountpoints": [contract[r]["mountpoint"]]}
                         for r in ("boot", "root")]}]}, "mounts": {}}
        for role in ("boot", "root"):
            row = contract[role]
            self.observation["mounts"][role] = {"filesystems": [{"source": row["source"],
                "target": row["mountpoint"], "fstype": row["fstype"], "uuid": row["uuid"], "options": "rw,relatime"}]}

    def path(self, value):
        return self.root / value.lstrip("/")

    def running(self):
        return dict(self.current)

    def probe(self, contract):
        return copy.deepcopy(self.observation)

    @contextlib.contextmanager
    def module_stream(self, archive):
        self.stream_count += 1
        yield io.BytesIO(archive.read_bytes())
        if self.fail_extract_end and self.stream_count == 2:
            raise ValueError("synthetic extraction completion failure")

    def require_write_access(self):
        pass

    def rename_new(self, source, destination):
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("synthetic no-replacement rename")
        source.rename(destination)

    def replace(self, source, destination):
        if self.fail_second_replace and self.replacements:
            raise OSError("synthetic second boot-file replacement failure")
        self.replacements.append(destination.name)
        os.replace(source, destination)
        if self.after_replace is not None:
            self.after_replace()

    def sync(self):
        self.sync_count += 1


class InstallChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.stage, self.live = self.root / "stage", self.root / "live"
        self.stage.mkdir()
        self.parent = self.root / "private-backups"
        self.parent.mkdir(mode=0o700)
        self.release, self.old_release = "candidate-generic-test1", "running-test1"
        self.running = {"release": self.old_release, "boot_id": "4b6af8a0-80c6-4abb-a69a-93a21d3bcf18"}
        contract = {"schema": 1, "status": "REVIEWED_FOR_EXPLICIT_LIVE_INSTALL", "running": self.running,
                    "installer_sha256": m.sha(HERE / "install_live.py"),
                    "disk": {"path": "/dev/sda", "bytes": 123983626240},
                    "boot": {"mountpoint": "/boot", "source": "/dev/sda1", "fstype": "vfat",
                             "uuid": "11111111-2222-4333-8444-555555555555", "bytes": 4294967296},
                    "root": {"mountpoint": "/", "source": "/dev/sda2", "fstype": "ext4",
                             "uuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "bytes": 119687610368},
                    "modules_parent": "/usr/lib/modules", "tools": {}}
        self.host = FakeHost(self.live, self.running, contract)
        self.boot, self.modules_parent = self.host.path("/boot"), self.host.path("/usr/lib/modules")
        self.boot.mkdir(parents=True)
        self.modules_parent.mkdir(parents=True)
        old_modules = self.modules_parent / self.old_release
        old_modules.mkdir()
        (old_modules / "retained.ko").write_bytes(b"old module must remain exact\n")
        for tool in m.TOOLS:
            path = self.host.path(tool)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic unused tool " + tool)
            contract["tools"][tool] = m.sha(path)
        self.plan = {"schema": 1, "experiment": "EXP-20261020-004", "release": self.release,
                     "install": contract, "boot_files": {}, "rollback_boot_files": {}}
        for dirname in ("candidate-boot", "rollback-boot"):
            (self.stage / dirname).mkdir()
        for name in m.BOOT_FILES:
            old = ("synthetic old " + name + "\n").encode()
            new = ("synthetic new " + name + "\n").encode() if name in m.CHANGED_FILES else old
            (self.stage / "rollback-boot" / name).write_bytes(old)
            (self.stage / "candidate-boot" / name).write_bytes(new)
            (self.boot / name).write_bytes(old)
            self.plan["boot_files"][name] = m.regular(self.stage / "candidate-boot" / name)
            self.plan["rollback_boot_files"][name] = m.regular(self.stage / "rollback-boot" / name)
        self.plan["kernel_sha256"] = self.plan["boot_files"]["bzImage"]["sha256"]
        prefix = "lib/modules/" + self.release + "/"
        self.rows = [(prefix + f"kernel/module-{i:02d}.ko", "file", (f"synthetic module {i}\n").encode(), 0o644)
                     for i in range(41)]
        self.rows.append((prefix + "modules.dep", "file", b"synthetic depmod\n", 0o644))
        self.module_inventory = {name: {"kind": "file", "mode": mode, "bytes": len(data),
                                       "sha256": hashlib.sha256(data).hexdigest()}
                                 for name, _, data, mode in self.rows}
        put_json(self.stage / "MODULE-TREE.json", self.module_inventory)
        contract["module_tree"] = {"path": "MODULE-TREE.json", "sha256": m.sha(self.stage / "MODULE-TREE.json")}
        self.archive_path = self.stage / "modules.tar.zst"
        self.runtime = {"release": self.release, "kernel_sha256": self.plan["kernel_sha256"],
                        "module_count": 41, "module_tree_sha256": contract["module_tree"]["sha256"],
                        "modules": {n: {**row, "vermagic": self.release + " synthetic"}
                                    for n, row in self.module_inventory.items() if n.endswith(".ko")}}
        self.args = SimpleNamespace(plan=self.stage / "PLAN.json", plan_sha256=None,
                                    output=self.parent / "attempt-01", experiment=self.plan["experiment"],
                                    session_id="synthetic-active-session", owner_confirmation="synthetic-message-reference",
                                    write=True)
        self.refresh_archive(self.rows)

    def tearDown(self):
        self.tmp.cleanup()

    def refresh_archive(self, rows):
        self.archive_path.write_bytes(tar_bytes(rows))
        self.plan["modules_archive"] = {"path": self.archive_path.name, "sha256": m.sha(self.archive_path), "count": 41}
        self.runtime["module_archive"] = {"name": self.archive_path.name, "sha256": m.sha(self.archive_path)}
        put_json(self.stage / "RUNTIME-IDENTITY.json", self.runtime)
        self.plan["install"]["runtime_identity"] = {"path": "RUNTIME-IDENTITY.json",
                                                    "sha256": m.sha(self.stage / "RUNTIME-IDENTITY.json")}
        self.freeze()

    def freeze(self):
        put_json(self.args.plan, self.plan)
        self.args.plan_sha256 = m.sha(self.args.plan)

    def run_install(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return m.Install(self.args, self.host).run()

    def test_validation_only_creates_no_files(self):
        self.args.write = False
        self.args.owner_confirmation = self.args.session_id = None
        before = m.inventory(self.root)
        receipt = self.run_install()
        self.assertEqual(receipt["status"], "PASS_VALIDATE")
        self.assertEqual(m.inventory(self.root), before)
        self.assertEqual(self.host.sync_count, 0)
        self.assertFalse(self.args.output.exists())

    def test_validation_only_accepts_read_only_boot_without_changes(self):
        self.args.write = False
        self.host.observation["mounts"]["boot"]["filesystems"][0]["options"] = "ro,relatime"
        before = m.inventory(self.root)
        self.assertEqual(self.run_install()["status"], "PASS_VALIDATE")
        self.assertEqual(m.inventory(self.root), before)
        self.assertEqual(self.host.sync_count, 0)
        self.assertFalse(self.args.output.exists())

    def test_write_rejects_read_only_boot_before_output(self):
        self.host.observation["mounts"]["boot"]["filesystems"][0]["options"] = "ro,relatime"
        before = m.inventory(self.root)
        with self.assertRaisesRegex(ValueError, "Mount identity/options differ: boot"):
            self.run_install()
        self.assertEqual(m.inventory(self.root), before)
        self.assertEqual(self.host.sync_count, 0)
        self.assertEqual(self.host.replacements, [])
        self.assertFalse(self.args.output.exists())

    def test_full_install_backup_order_modules_and_unchanged_inputs(self):
        stage_before = m.inventory(self.stage)
        old_before = m.inventory(self.modules_parent / self.old_release)
        result = self.run_install()
        self.assertEqual(result["status"], "PASS_INSTALL_READBACK")
        self.assertFalse(result["hardware_acceptance"])
        self.assertEqual(self.host.replacements, ["bzImage", "bootargs.txt"])
        self.assertGreaterEqual(self.host.sync_count, 2)
        self.assertEqual({n: m.regular(self.boot / n) for n in m.BOOT_FILES}, self.plan["boot_files"])
        self.assertEqual({n: m.regular(self.args.output / "rollback-boot" / n) for n in m.BOOT_FILES},
                         self.plan["rollback_boot_files"])
        prefix = "lib/modules/" + self.release + "/"
        self.assertEqual(m.inventory(self.modules_parent / self.release),
                         {n[len(prefix):]: row for n, row in self.module_inventory.items()})
        self.assertEqual(m.inventory(self.modules_parent / self.old_release), old_before)
        self.assertEqual(m.inventory(self.stage), stage_before)
        self.assertEqual(self.args.output.stat().st_mode & 0o777, 0o700)

    def test_write_requires_explicit_confirmation_and_session(self):
        for field in ("owner_confirmation", "session_id"):
            original = getattr(self.args, field)
            setattr(self.args, field, None)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Writes require"):
                self.run_install()
            self.assertFalse(self.args.output.exists())
            setattr(self.args, field, original)

    def test_wrong_disk_size_and_partition_identity(self):
        original = copy.deepcopy(self.host.observation)
        edits = [lambda p: p["blocks"]["blockdevices"][0].update(size=123),
                 lambda p: p["blocks"]["blockdevices"][0]["children"][0].update(uuid="WRONG"),
                 lambda p: p["mounts"]["boot"]["filesystems"][0].update(source="/dev/sdb1"),
                 lambda p: p["mounts"]["root"]["filesystems"][0].update(fstype="wrong")]
        for edit in edits:
            self.host.observation = copy.deepcopy(original)
            edit(self.host.observation)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.run_install()
            self.assertFalse(self.args.output.exists())

    def test_production_probe_explicitly_requests_tree_without_name_column(self):
        calls = []
        actual_host = m.Host()

        def identity_command(argv):
            calls.append(argv)
            if argv[0] == "/usr/bin/lsblk":
                rows = copy.deepcopy(self.host.observation["blocks"])
                if "--tree" not in argv:
                    disk = rows["blockdevices"][0]
                    rows["blockdevices"] = [disk, *disk.pop("children")]
                return rows
            role = "boot" if argv[argv.index("--target") + 1] == "/boot" else "root"
            return copy.deepcopy(self.host.observation["mounts"][role])

        with patch.object(actual_host, "command_json", side_effect=identity_command):
            observation = actual_host.probe(self.plan["install"])
        m.verify_probe(observation, self.plan["install"])
        self.assertEqual(calls[-1], ["/usr/bin/lsblk", "--json", "--tree", "--bytes", "--paths", "--output",
                                   "PATH,TYPE,SIZE,FSTYPE,UUID,PKNAME,MOUNTPOINTS", "/dev/sda"])

    def test_flat_disk_and_partition_rows_remain_rejected(self):
        disk = self.host.observation["blocks"]["blockdevices"][0]
        self.host.observation["blocks"]["blockdevices"] = [disk, *disk.pop("children")]
        before = m.inventory(self.root)
        with self.assertRaisesRegex(ValueError, "Exact USB disk identity"):
            self.run_install()
        self.assertEqual(m.inventory(self.root), before)

    def test_wrong_boot_id_even_with_matching_release(self):
        self.host.current["boot_id"] = "00000000-0000-0000-0000-000000000000"
        with self.assertRaisesRegex(ValueError, "Running release/boot ID"):
            self.run_install()
        self.assertFalse(self.args.output.exists())

    def test_wrong_live_boot_hash_and_live_symlink(self):
        path = self.boot / "bzImage"
        path.write_bytes(b"X" + path.read_bytes()[1:])
        with self.assertRaisesRegex(ValueError, "Live boot hash"):
            self.run_install()
        path.unlink()
        path.symlink_to(self.stage / "rollback-boot/bzImage")
        with self.assertRaisesRegex(ValueError, "links forbidden"):
            self.run_install()
        self.assertFalse(self.args.output.exists())

    def test_wrong_plan_and_staged_candidate_hash(self):
        self.args.plan_sha256 = "0" * 64
        with self.assertRaisesRegex(ValueError, "plan SHA256"):
            self.run_install()
        self.args.plan_sha256 = m.sha(self.args.plan)
        path = self.stage / "candidate-boot/bzImage"
        path.write_bytes(b"X" + path.read_bytes()[1:])
        with self.assertRaisesRegex(ValueError, "Staged boot file differs"):
            self.run_install()

    def test_module_archive_extra_missing_wrong_bytes_and_links(self):
        name, _, data, mode = self.rows[0]
        variants = [self.rows + [("lib/modules/" + self.release + "/extra", "file", b"extra", 0o644)],
                    self.rows[1:], [(name, "file", b"X" + data[1:], mode), *self.rows[1:]],
                    [(name, "symlink", "elsewhere", mode), *self.rows[1:]],
                    [(name, "hardlink", self.rows[1][0], mode), *self.rows[1:]]]
        for rows in variants:
            self.refresh_archive(rows)
            with self.subTest(first=rows[0]), self.assertRaises(ValueError):
                self.run_install()
            self.assertFalse(self.args.output.exists())

    def test_module_archive_rejects_appended_second_tar(self):
        self.archive_path.write_bytes(tar_bytes(self.rows) + tar_bytes([("unexpected", "file", b"x", 0o644)]))
        self.plan["modules_archive"]["sha256"] = m.sha(self.archive_path)
        self.runtime["module_archive"]["sha256"] = m.sha(self.archive_path)
        put_json(self.stage / "RUNTIME-IDENTITY.json", self.runtime)
        self.plan["install"]["runtime_identity"]["sha256"] = m.sha(self.stage / "RUNTIME-IDENTITY.json")
        self.freeze()
        with self.assertRaisesRegex(ValueError, "unexpected module"):
            self.run_install()

    def test_existing_new_release_is_preserved(self):
        existing = self.modules_parent / self.release
        existing.mkdir()
        (existing / "untouched").write_bytes(b"already present")
        before = m.inventory(self.root)
        with self.assertRaisesRegex(ValueError, "Destination/temp already exists"):
            self.run_install()
        self.assertEqual(m.inventory(self.root), before)

    def test_extraction_failure_preserves_backups_temp_and_old_boot(self):
        self.host.fail_extract_end = True
        with self.assertRaisesRegex(ValueError, "extraction completion"):
            self.run_install()
        receipt = json.loads((self.args.output / "INSTALL.json").read_bytes())
        self.assertEqual(receipt["status"], "FAILED_PRESERVED")
        self.assertTrue(receipt["module_temp_exists"])
        self.assertFalse(receipt["module_destination_exists"])
        self.assertTrue((self.args.output / "BACKUP.json").is_file())
        self.assertEqual(receipt["boot_readback"], self.plan["rollback_boot_files"])

    def test_second_replace_failure_preserves_partial_transaction(self):
        self.host.fail_second_replace = True
        with self.assertRaisesRegex(OSError, "second boot-file replacement"):
            self.run_install()
        receipt = json.loads((self.args.output / "INSTALL.json").read_bytes())
        self.assertEqual(receipt["status"], "FAILED_PRESERVED")
        self.assertTrue(receipt["module_destination_exists"])
        self.assertFalse(receipt["module_temp_exists"])
        self.assertTrue(receipt["boot_temps"]["bootargs.txt"]["exists"])
        self.assertFalse(receipt["boot_temps"]["bzImage"]["exists"])
        self.assertEqual(m.regular(self.boot / "bzImage"), self.plan["boot_files"]["bzImage"])
        for name in m.BOOT_FILES - {"bzImage"}:
            self.assertEqual(m.regular(self.boot / name), self.plan["rollback_boot_files"][name])
        before = m.inventory(self.root)
        with self.assertRaises(ValueError):
            self.run_install()
        self.assertEqual(m.inventory(self.root), before)

    def test_output_must_be_private_and_outside_stage(self):
        self.parent.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "private canonical parent"):
            self.run_install()
        self.parent.chmod(0o700)
        self.stage.chmod(0o700)
        self.args.output = self.stage / "bad-output"
        with self.assertRaisesRegex(ValueError, "Output overlaps"):
            self.run_install()

    def test_changed_input_does_not_suppress_final_boot_readback(self):
        def change_frozen_source_after_commit():
            if len(self.host.replacements) == 1:
                source = self.stage / "candidate-boot/bzImage"
                source.write_bytes(b"X" + source.read_bytes()[1:])

        self.host.after_replace = change_frozen_source_after_commit
        with self.assertRaisesRegex(ValueError, "Frozen input changed"):
            self.run_install()
        receipt = json.loads((self.args.output / "INSTALL.json").read_bytes())
        self.assertEqual(receipt["status"], "FAILED_PRESERVED")
        self.assertIn("final_check_error", receipt)
        self.assertEqual(receipt["boot_readback"], self.plan["boot_files"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    m.require(not args.report.exists() and args.report.parent.is_dir(), "New report path required")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__)))
    m.save(args.report, {"schema": 1, "status": "PASS" if result.wasSuccessful() else "FAIL",
                         "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                         "install_live_sha256": m.sha(HERE / "install_live.py"),
                         "test_install_live_sha256": m.sha(Path(__file__)),
                         "scope": "Synthetic filesystem and injected host only; no zstd/findmnt/lsblk/renameat2/device execution",
                         "external_commands": [], "hardware_acceptance": False, "device_operated": False})
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
