"""Small frontend FTP publication checks; all endpoints and bytes are synthetic."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import importlib.machinery
import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / relative))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


HELPERS = load("psx_frontend_ftp_helpers", "tests/test_ps4_diagnostics_ftp.py")
APP = load("psx_frontend_ftp_tests", "scripts/update-ps4-diagnostics-frontend-ftp")
USB, SERIAL, CAPACITY, UUID = HELPERS.USB, HELPERS.SERIAL, HELPERS.CAPACITY, HELPERS.UUID
sha = HELPERS.sha
SMALL = {"initramfs.cpio.gz", "bootargs.txt", "psx-diagnostics/boot-set.json"}


class RecordingFTP(HELPERS.FakeFTP):
    def __init__(self, files):
        super().__init__(files)
        self.reads = []

    def retrbinary(self, command, callback, blocksize=8192):
        self.reads.append(command.removeprefix("RETR "))
        return super().retrbinary(command, callback, blocksize)


class FrontendFTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/var/tmp")
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name)
        self.source = self.temp / "source"
        self.source.mkdir()
        self.backup = self.temp / "backup"
        self.receipt = self.temp / "receipt.json"
        self.new = {
            "bzImage": b"same-kernel",
            "initramfs.cpio.gz": b"new-olive-initramfs",
            "vram.txt": b"1024\n",
            "bootargs.template.txt": b"psx.diag_uuid=@USB_UUID@ olive=1\n",
            "psx-diagnostics/rootfs.ext4": b"same-ext4-root",
            "psx-diagnostics/config": b"same-config",
        }
        self.metadata = {
            "profile": "fixture-ratatui", "kernel_release": "fixture-native",
            "kernel_sha256": sha(self.new["bzImage"]),
            "initramfs_sha256": sha(self.new["initramfs.cpio.gz"]),
            "config_sha256": sha(self.new["psx-diagnostics/config"]),
            "rootfs_sha256": sha(self.new["psx-diagnostics/rootfs.ext4"]),
            "rootfs_file": "psx-diagnostics/rootfs.ext4", "root_format": "ext4-ro-loop",
            "hardware_acceptance": False, "vram_mib": 1024,
            "maximum_boot_set_bytes": 2 * 1024**2, "ui_backend": "ratatui",
            "frontend_revision": "olive-fixture", "frontend_overrides": {"console": True},
            "frontend_refresh": {"root_image_unchanged": True},
        }
        old_meta = copy.deepcopy(self.metadata)
        old_meta.update(initramfs_sha256=sha(b"old-initramfs"), frontend_revision="old")
        self.old = {name: data for name, data in self.new.items() if name != "bootargs.template.txt"}
        self.old.update({"initramfs.cpio.gz": b"old-initramfs",
                         "bootargs.txt": b"psx.diag_uuid=1234-ABCD time=1600000000\n",
                         "psx-diagnostics/boot-set.json": (json.dumps(old_meta) + "\n").encode()})
        self.previous = {
            "owner": "psx-diagnostics-usb-v1", "verified_readback": True,
            "hardware_acceptance": False, "profile": self.metadata["profile"],
            "kernel_release": self.metadata["kernel_release"],
            "target": {"serial": SERIAL, "bytes": CAPACITY, "uuid": UUID},
            "files": {name: sha(data) for name, data in self.old.items()},
        }
        self.previous_raw = (json.dumps(self.previous) + "\n").encode()
        self.sums = APP.DEPLOY.STAGE.installed_sums(self.previous["files"]).encode()
        self.mutable = {
            USB + "/psx-diagnostics/results/run/result.json": b"operator result",
            USB + "/psx-diagnostics/state/ssh/key": b"synthetic state",
            USB + "/psx-diagnostics/backups/note": b"operator note",
        }
        self.ftp = RecordingFTP({USB + "/" + name: data for name, data in self.old.items()} | self.mutable | {
            USB + "/psx-diagnostics/installed.json": self.previous_raw,
            USB + "/psx-diagnostics/USB-SHA256SUMS": self.sums})
        self.write_source()

    def write_source(self):
        self.new["psx-diagnostics/boot-set.json"] = (json.dumps(self.metadata) + "\n").encode()
        for name, data in self.new.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.source / "SHA256SUMS").write_text(APP.DEPLOY.STAGE.installed_sums({
            name: sha(data) for name, data in self.new.items()}))

    def run_update(self, apply=True, extra=()):
        args = ["update", "--source", str(self.source), "--host", "fixture.invalid",
                "--expect-serial", SERIAL, "--expect-bytes", str(CAPACITY), "--expect-uuid", UUID,
                "--backup", str(self.backup), "--receipt", str(self.receipt), *extra]
        if apply:
            args.append("--apply")
        with mock.patch.object(APP.ftplib, "FTP", return_value=self.ftp), \
             mock.patch.object(APP.sys, "argv", args), \
             mock.patch.object(APP.uuid, "uuid4", return_value=SimpleNamespace(hex="frontendfixture")), \
             mock.patch.object(APP.DEPLOY.STAGE.time, "time", return_value=1800000000), \
             contextlib.redirect_stdout(io.StringIO()):
            APP.main()

    def assert_preserved(self):
        for name, data in self.mutable.items():
            self.assertEqual(self.ftp.files[name], data)
        for name in set(self.old) - SMALL:
            self.assertEqual(self.ftp.files[USB + "/" + name], self.old[name])
            self.assertNotIn(USB + "/" + name, self.ftp.reads)

    def assert_rolled_back(self):
        for name, data in self.old.items():
            self.assertEqual(self.ftp.files.get(USB + "/" + name), data, name)
        self.assertEqual(self.ftp.files[USB + "/psx-diagnostics/installed.json"], self.previous_raw)
        self.assertEqual(self.ftp.files[USB + "/psx-diagnostics/USB-SHA256SUMS"], self.sums)
        self.assertFalse(self.receipt.exists())
        self.assert_preserved()

    def test_default_plan_has_no_remote_or_local_mutation(self):
        original = dict(self.ftp.files)
        self.run_update(apply=False)
        self.assertEqual(self.ftp.files, original)
        self.assertEqual(self.ftp.mutations, [])
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.receipt.exists())
        self.assert_preserved()

    def test_actual_root_kernel_and_config_changes_refused_before_mutation(self):
        for name, key in (("psx-diagnostics/rootfs.ext4", "rootfs_sha256"),
                          ("bzImage", "kernel_sha256"), ("psx-diagnostics/config", "config_sha256")):
            original_data, original_pin = self.new[name], self.metadata[key]
            self.new[name] = b"changed unrelated payload"
            self.metadata[key] = sha(self.new[name])
            self.write_source()
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.run_update()
            self.assertEqual(self.ftp.mutations, [])
            self.assertFalse(self.backup.exists())
            self.new[name], self.metadata[key] = original_data, original_pin

    def test_metadata_config_pin_cannot_change_independently(self):
        self.metadata["config_sha256"] = sha(b"unrelated config")
        self.write_source()
        with self.assertRaises(ValueError):
            self.run_update()
        self.assertEqual(self.ftp.mutations, [])

    def test_missing_frontend_contract_refused_before_mutation(self):
        self.metadata["frontend_refresh"]["root_image_unchanged"] = False
        self.write_source()
        with self.assertRaises(ValueError):
            self.run_update()
        self.assertEqual(self.ftp.mutations, [])

    def test_success_only_transfers_small_payloads_and_exactly_reads_back(self):
        self.run_update()
        marker = json.loads(self.ftp.files[USB + "/psx-diagnostics/installed.json"])
        self.assertEqual(marker["frontend_revision"], "olive-fixture")
        self.assertTrue(marker["verified_readback"])
        self.assertFalse(marker["hardware_acceptance"])
        self.assertEqual(set(marker["files"]), set(self.old))
        changed = set(json.loads(self.receipt.read_text())["changed_files"])
        self.assertEqual(changed, SMALL - {'bootargs.txt'})
        self.assertEqual(self.ftp.files[USB + '/bootargs.txt'], self.old['bootargs.txt'])
        for name in changed:
            data = self.ftp.files[USB + "/" + name]
            self.assertEqual(sha(data), marker["files"][name])
            self.assertGreaterEqual(self.ftp.reads.count(USB + "/" + name), 3)
            self.assertEqual((self.backup / name).read_bytes(), self.old[name])
        self.assertEqual((self.backup / "installed.json").read_bytes(), self.previous_raw)
        self.assertEqual((self.backup / "USB-SHA256SUMS").read_bytes(), self.sums)
        self.assertEqual(json.loads(self.receipt.read_text())["status"], "PASS")
        self.assertEqual(self.ftp.files[USB + "/psx-diagnostics/USB-SHA256SUMS"],
                         APP.DEPLOY.STAGE.installed_sums(marker["files"]).encode())
        uploads = [path for operation, *paths in self.ftp.mutations if operation == "store" for path in paths]
        allowed = {USB + "/psx-diagnostics/" + name for name in ("installed.json", "USB-SHA256SUMS")}
        allowed |= {USB + "/.psx-frontend-frontendfixture/new/" + name for name in changed}
        self.assertEqual(set(uploads), allowed)
        self.assert_preserved()

    def test_rename_failure_before_publication_restores_old_set(self):
        self.ftp.fail_rename = (USB + "/initramfs.cpio.gz", "before")
        with self.assertRaises(OSError):
            self.run_update()
        self.assert_rolled_back()

    def test_lost_rename_acknowledgement_restores_old_set(self):
        self.ftp.fail_rename = (USB + "/initramfs.cpio.gz", "after")
        with self.assertRaises(OSError):
            self.run_update()
        self.assert_rolled_back()

    def test_frontend_preserves_verbose_and_unrelated_boot_arguments(self):
        value = b'psx.diag_uuid=1234-ABCD pci=use_crs intremap=off psx_mts_net.autoarm=1 libata.force=1.00:disable loglevel=7 ignore_loglevel time=1600000000\n'
        self.old['bootargs.txt'] = value
        self.previous['files']['bootargs.txt'] = sha(value)
        self.ftp.files[USB + '/bootargs.txt'] = value
        self.ftp.files[USB + '/psx-diagnostics/installed.json'] = (json.dumps(self.previous) + '\n').encode()
        self.ftp.files[USB + '/psx-diagnostics/USB-SHA256SUMS'] = APP.DEPLOY.STAGE.installed_sums(self.previous['files']).encode()
        self.run_update()
        self.assertEqual(self.ftp.files[USB + '/bootargs.txt'], value)
        self.assertNotIn('bootargs.txt', json.loads(self.receipt.read_text())['changed_files'])

    def test_verbose_uart_changes_only_logging_tokens(self):
        value = b'psx.diag_uuid=1234-ABCD pci=use_crs psx_mts_net.autoarm=1 libata.force=1.00:disable systemd.show_status=false loglevel=3\n'
        result = APP.verbose_uart_arguments(value).decode().split()
        self.assertIn('loglevel=7', result)
        self.assertIn('ignore_loglevel', result)
        retained = lambda seq: [x for x in seq if not x.startswith('loglevel=') and x != 'ignore_loglevel']
        self.assertEqual(retained(result), retained(value.decode().split()))
        self.assertEqual(APP.verbose_uart_arguments(APP.verbose_uart_arguments(value)), APP.verbose_uart_arguments(value))

    def test_generated_receipt_partial_upload_restores_old_set(self):
        self.ftp.fail_store = USB + "/psx-diagnostics/USB-SHA256SUMS"
        with self.assertRaises(OSError):
            self.run_update()
        self.assert_rolled_back()

    def test_corrupt_staging_readback_prevents_publication(self):
        self.ftp.corrupt_stage = True
        with self.assertRaises(ValueError):
            self.run_update()
        self.assert_rolled_back()


if __name__ == "__main__":
    unittest.main()
