# SPDX-License-Identifier: MIT
"""Exercise the real staging transaction using temporary regular files only."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader(
    "psx_usb_transaction_test", str(ROOT / "scripts/stage-ps4-diagnostics-usb"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
STAGE = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(STAGE)


class UsbStageTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.mount = self.root / "mounted"
        self.mount.mkdir()
        self.backup = self.mount / "psx-diagnostics/backups/123"
        self.payloads = {
            "bzImage": b"new kernel", "initramfs.cpio.gz": b"new initramfs",
            "vram.txt": b"1024\n", "psx-diagnostics/rootfs.ext4": b"new root image" * 64,
            "psx-diagnostics/boot-set.json": b'{"fixture": "new"}\n',
            "psx-diagnostics/added.json": b"new owned file\n",
            "bootargs.template.txt": b"uuid=@USB_UUID@\n",
        }
        self.files = {name: hashlib.sha256(data).hexdigest()
                      for name, data in self.payloads.items()}
        for name, data in self.payloads.items():
            self.write(self.source / name, data)
        self.expected = set(self.files) - {"bootargs.template.txt"} | {"bootargs.txt"}
        old = {name: b"old bytes: " + name.encode() for name in self.expected
               if name != "psx-diagnostics/added.json"}
        self.device = {"serial": "fixture", "bytes": 1000000, "uuid": "1234-ABCD"}
        self.previous = {
            "owner": "psx-diagnostics-usb-v1", "target": self.device,
            "hardware_acceptance": False, "verified_readback": True,
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in old.items()},
        }
        # Deliberately retain original formatting, not a regenerated equivalent.
        old["psx-diagnostics/installed.json"] = json.dumps(self.previous).encode() + b"\n\n"
        old["psx-diagnostics/USB-SHA256SUMS"] = STAGE.installed_sums(self.previous["files"]).encode()
        for name, data in old.items():
            self.write(self.mount / name, data)
        self.old = old
        self.inodes = {name: (self.mount / name).stat().st_ino for name in old}
        self.unrelated = {"unrelated.bin": b"keep", "psx-diagnostics/results/private.json": b"keep result",
                          "psx-diagnostics/backups/older/rootfs.ext4": b"keep older backup"}
        for name, data in self.unrelated.items():
            self.write(self.mount / name, data)
        self.plan = {"target": self.device, "hardware_acceptance": False, "apply": True}
        self.bootargs = b"new bootargs\n"
        for name in ("inspect",):
            patch = mock.patch.object(STAGE, name, side_effect=AssertionError("no device inspection"))
            patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(STAGE.subprocess, "check_output",
                                  side_effect=AssertionError("no hardware commands"))
        patch.start(); self.addCleanup(patch.stop)

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def stage(self, previous=True):
        # The production mount pin is exercised against this temporary directory.
        with STAGE.pinned_mount(self.mount, self.mount.stat().st_dev), \
                mock.patch.object(STAGE.time, "time_ns", return_value=123):
            return STAGE.copy_boot_set(self.source, self.files, self.bootargs,
                                       self.previous if previous else None, self.plan, self.expected)

    def assert_unrelated(self):
        for name, data in self.unrelated.items():
            self.assertEqual((self.mount / name).read_bytes(), data)
        self.assertFalse(list(self.mount.rglob(".psx-copy-*")))

    def assert_restored(self):
        for name, data in self.old.items():
            self.assertEqual((self.mount / name).read_bytes(), data, name)
            self.assertEqual((self.mount / name).stat().st_ino, self.inodes[name], name)
        self.assertFalse((self.mount / "psx-diagnostics/added.json").exists())
        self.assertEqual(STAGE.previous_install(self.mount, self.device), self.previous)
        self.assert_unrelated()

    def test_success_moves_exact_old_files_and_receipts_then_writes_loader_last(self):
        copied = []
        copy = STAGE.shutil.copyfileobj

        def record(incoming, outgoing, **kwargs):
            copied.append(Path(incoming.name).relative_to(self.source).as_posix())
            return copy(incoming, outgoing, **kwargs)

        with mock.patch.object(STAGE.shutil, "copyfileobj", side_effect=record), \
                mock.patch.object(STAGE.shutil, "copyfile", side_effect=AssertionError("no backup copy")):
            marker = self.stage()
        for name, data in self.old.items():
            saved = self.backup / ("installed.json" if name == "psx-diagnostics/installed.json" else name)
            self.assertEqual(saved.read_bytes(), data, name)
            self.assertEqual(saved.stat().st_ino, self.inodes[name], name)
        for name, pin in marker["files"].items():
            self.assertEqual(STAGE.digest(self.mount / name), pin)
        self.assertEqual(copied, sorted(n for n in self.files if n.startswith("psx-diagnostics/"))
                         + ["bzImage", "initramfs.cpio.gz", "vram.txt"])
        self.assertEqual(STAGE.previous_install(self.mount, self.device), marker)
        self.assert_unrelated()

    def test_partial_payload_copy_restores_all_original_bytes_and_removes_new_files(self):
        copy = STAGE.shutil.copyfileobj

        def fail(incoming, outgoing, **kwargs):
            if Path(incoming.name).name == "rootfs.ext4":
                outgoing.write(incoming.read(7))
                raise OSError("injected mid-copy failure")
            return copy(incoming, outgoing, **kwargs)

        with mock.patch.object(STAGE.shutil, "copyfileobj", side_effect=fail), \
                self.assertRaisesRegex(OSError, "mid-copy"):
            self.stage()
        self.assert_restored()

    def test_interrupt_after_receipts_were_replaced_restores_original_receipts(self):
        write = STAGE.atomic_write

        def fail(target, **kwargs):
            write(target, **kwargs)
            if target.name == "USB-SHA256SUMS":
                raise KeyboardInterrupt("injected after final replacement")

        with mock.patch.object(STAGE, "atomic_write", side_effect=fail), \
                self.assertRaises(KeyboardInterrupt):
            self.stage()
        self.assert_restored()

    def test_backup_move_failure_restores_earlier_moves_without_removing_unmoved_files(self):
        replace = STAGE.os.replace

        def fail(source, target):
            if Path(source) == Path("initramfs.cpio.gz"):
                raise OSError("injected rename failure")
            return replace(source, target)

        with mock.patch.object(STAGE.os, "replace", side_effect=fail), \
                self.assertRaisesRegex(OSError, "rename failure"):
            self.stage()
        self.assert_restored()

    def test_readback_failure_rolls_back(self):
        digest = STAGE.digest

        def wrong(path):
            return "0" * 64 if path == Path("bzImage") else digest(path)

        with mock.patch.object(STAGE, "digest", side_effect=wrong), \
                self.assertRaisesRegex(ValueError, "readback differs"):
            self.stage()
        self.assert_restored()

    def test_failed_first_install_removes_only_new_owned_names(self):
        for name in self.old:
            (self.mount / name).unlink()
        write = STAGE.atomic_write

        def fail(target, **kwargs):
            write(target, **kwargs)
            if target.name == "installed.json":
                raise OSError("injected initial receipt failure")

        with mock.patch.object(STAGE, "atomic_write", side_effect=fail), \
                self.assertRaisesRegex(OSError, "initial receipt"):
            self.stage(previous=False)
        for name in self.expected | {"psx-diagnostics/installed.json", "psx-diagnostics/USB-SHA256SUMS"}:
            self.assertFalse((self.mount / name).exists(), name)
        self.assertFalse(self.backup.exists())
        self.assert_unrelated()

    def test_restore_error_keeps_unrestored_backup_and_attempts_every_other_restore(self):
        replace = STAGE.os.replace
        write = STAGE.atomic_write

        def fail_write(target, **kwargs):
            write(target, **kwargs)
            if target.name == "USB-SHA256SUMS":
                raise OSError("injected copy failure")

        def fail_restore(source, target):
            if Path(source) == Path("psx-diagnostics/backups/123/vram.txt"):
                raise OSError("injected restore failure")
            return replace(source, target)

        with mock.patch.object(STAGE, "atomic_write", side_effect=fail_write), \
                mock.patch.object(STAGE.os, "replace", side_effect=fail_restore), \
                self.assertRaisesRegex(OSError, "rollback incomplete; retained backup"):
            self.stage()
        self.assertEqual((self.backup / "vram.txt").read_bytes(), self.old["vram.txt"])
        for name, data in self.old.items():
            if name != "vram.txt":
                self.assertEqual((self.mount / name).read_bytes(), data, name)
        self.assertFalse((self.mount / "psx-diagnostics/added.json").exists())
        self.assert_unrelated()


if __name__ == "__main__":
    unittest.main()
