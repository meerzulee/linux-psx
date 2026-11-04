"""Synthetic FTP deployment/recovery checks; no network, mounts or devices."""
import contextlib
import copy
import ftplib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / relative))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


APP = load("psx_ftp_deployment_tests", "scripts/deploy-ps4-diagnostics-ftp")
STAGE = APP.STAGE
USB = "/mnt/usb0"
SERIAL = "fixture-usb-serial"
CAPACITY = 16_000_000_000
UUID = "1234-ABCD"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normal(path):
    return str(PurePosixPath(path))


class FakeFTP:
    """In-memory FTP with deliberate pre/post-publication failure points."""

    def __init__(self, files):
        self.files = dict(files)
        self.directories = {"/", "/mnt", USB}
        for filename in self.files:
            self.directories.update(str(p) for p in PurePosixPath(filename).parents)
        self.current = "/"
        self.mutations = []
        self.fail_rename = None
        self.fail_store = None
        self.fail_delete = None
        self.corrupt_stage = False
        self.corrupt_old = False
        self.denied_directories = set()
        self.disconnected = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def connect(self, host, port):
        if (host, port) != ("fixture.invalid", 2121):
            raise AssertionError("test attempted an unexpected endpoint")

    def login(self):
        return "230 synthetic"

    def cwd(self, path):
        if self.disconnected:
            raise EOFError("synthetic disconnected control channel")
        path = normal(path)
        if path in self.denied_directories:
            raise ftplib.error_perm("550 Directory unavailable")
        if path not in self.directories:
            raise ftplib.error_perm("550 Directory unavailable")
        self.current = path

    def nlst(self):
        paths = set(self.files) | self.directories
        return sorted(p for p in paths if p != self.current and str(PurePosixPath(p).parent) == self.current)

    def retrbinary(self, command, callback, blocksize=8192):
        path = normal(command.removeprefix("RETR "))
        if path not in self.files:
            raise ftplib.error_perm("550 File unavailable")
        payload = self.files[path]
        for start in range(0, len(payload), blocksize):
            callback(payload[start:start + blocksize])
        return "226 synthetic"

    def storbinary(self, command, stream, blocksize=8192, callback=None):
        path = normal(command.removeprefix("STOR "))
        if str(PurePosixPath(path).parent) not in self.directories:
            raise ftplib.error_perm("550 Parent absent")
        self.mutations.append(("store", path))
        if self.fail_store == path:
            self.fail_store = None
            self.files[path] = b"partial-write"
            raise OSError("injected metadata upload failure")
        data = stream.read()
        self.files[path] = data
        if self.corrupt_stage and "/new/" in path:
            self.corrupt_stage = False
            self.files[path] = b"corrupt staging bytes"
        if callback:
            for start in range(0, len(data), blocksize):
                callback(data[start:start + blocksize])
        return "226 synthetic"

    def mkd(self, path):
        path = normal(path)
        if str(PurePosixPath(path).parent) not in self.directories:
            raise ftplib.error_perm("550 Parent absent")
        if path in self.directories or path in self.files:
            raise ftplib.error_perm("550 Already exists")
        self.mutations.append(("mkdir", path))
        self.directories.add(path)

    def rename(self, source, target):
        source, target = normal(source), normal(target)
        self.mutations.append(("rename", source, target))
        if source not in self.files:
            raise ftplib.error_perm("550 Source absent")
        if target in self.files or str(PurePosixPath(target).parent) not in self.directories:
            raise ftplib.error_perm("550 Destination unavailable")
        failure = self.fail_rename
        matches = failure and failure[0] == target
        if matches and failure[1] == "before":
            self.fail_rename = None
            raise OSError("injected rename failure")
        self.files[target] = self.files.pop(source)
        if self.corrupt_old and "/old/" in target:
            self.corrupt_old = False
            self.files[target] = b"corrupt old recovery bytes"
        if matches and failure[1] == "after":
            self.fail_rename = None
            raise OSError("injected lost rename acknowledgement")
        if matches and failure[1] == "disconnect":
            self.fail_rename = None
            self.disconnected = True
            raise EOFError("injected control-channel disconnect after rename")
        return "250 synthetic"

    def delete(self, path):
        path = normal(path)
        self.mutations.append(("delete", path))
        if self.fail_delete and self.fail_delete in path:
            self.fail_delete = None
            raise OSError("injected cleanup deletion failure")
        if path not in self.files:
            raise ftplib.error_perm("550 File absent")
        del self.files[path]


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/var/tmp")
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name)
        self.source = self.temp / "boot-set"
        self.source.mkdir()
        self.backup = self.temp / "rollback"
        self.receipt_path = self.temp / "update-result.json"
        self.old = {
            "bzImage": b"old-kernel", "initramfs.cpio.gz": b"old-init",
            "bootargs.txt": b"psx.diag_uuid=1234-ABCD old=1\n", "vram.txt": b"1024\n",
            "psx-diagnostics/rootfs.tar.gz": b"old-compressed-root",
            "psx-diagnostics/boot-set.json": b'{"profile":"old"}\n',
            "psx-diagnostics/source/old-source.tar.zst": b"old-source-code",
        }
        self.previous = {
            "owner": "psx-diagnostics-usb-v1", "verified_readback": True,
            "hardware_acceptance": False,
            "target": {"serial": SERIAL, "bytes": CAPACITY, "uuid": UUID},
            "files": {name: sha(data) for name, data in self.old.items()},
        }
        self.new = {
            "bzImage": b"new-kernel", "initramfs.cpio.gz": b"new-init",
            "bootargs.template.txt": b"psx.diag_uuid=@USB_UUID@ new=1\n", "vram.txt": b"1024\n",
            "psx-diagnostics/rootfs.ext4": b"new-ext4-root-fixture",
        }
        self.metadata = {
            "profile": "fixture-gui", "kernel_release": "fixture-native",
            "kernel_sha256": sha(self.new["bzImage"]), "initramfs_sha256": sha(self.new["initramfs.cpio.gz"]),
            "rootfs_sha256": sha(self.new["psx-diagnostics/rootfs.ext4"]),
            "rootfs_file": "psx-diagnostics/rootfs.ext4", "root_format": "ext4-ro-loop",
            "hardware_acceptance": False, "vram_mib": 1024, "maximum_boot_set_bytes": 2 * 1024 * 1024,
        }
        self.write_source()
        remote = {USB + "/" + name: data for name, data in self.old.items()}
        self.previous_bytes = (json.dumps(self.previous, separators=(",", ":")) + "\n").encode()
        remote[USB + "/psx-diagnostics/installed.json"] = self.previous_bytes
        remote[USB + "/psx-diagnostics/USB-SHA256SUMS"] = STAGE.installed_sums(self.previous["files"]).encode()
        self.mutable = {
            USB + "/psx-diagnostics/results/run1/result.json": b"user result",
            USB + "/psx-diagnostics/state/ssh/ssh_host_ed25519_key": b"synthetic private state",
            USB + "/psx-diagnostics/backups/operator-note.txt": b"operator backup",
        }
        self.ftp = FakeFTP(remote | self.mutable)

    def write_source(self):
        self.new["psx-diagnostics/boot-set.json"] = (json.dumps(self.metadata) + "\n").encode()
        for name, data in self.new.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.source / "SHA256SUMS").write_text("".join(sha(data) + "  " + name + "\n" for name, data in sorted(self.new.items())))

    def run_deploy(self, apply=True, extra=()):
        args = ["deploy", "--source", str(self.source), "--host", "fixture.invalid",
                "--expect-serial", SERIAL, "--expect-bytes", str(CAPACITY), "--expect-uuid", UUID,
                "--backup", str(self.backup), "--receipt", str(self.receipt_path), *extra]
        if apply:
            args.append("--apply")
        with mock.patch.object(APP.ftplib, "FTP", return_value=self.ftp), \
             mock.patch.object(APP.uuid, "uuid4", return_value=SimpleNamespace(hex="fixturedeployment")), \
             mock.patch.object(APP.sys, "argv", args), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            APP.main()

    def assert_mutable_preserved(self):
        for name, payload in self.mutable.items():
            self.assertEqual(self.ftp.files[name], payload)

    def assert_old_restored(self):
        for name, payload in self.old.items():
            self.assertEqual(self.ftp.files.get(USB + "/" + name), payload, name)
        self.assertEqual(self.ftp.files[USB + "/psx-diagnostics/installed.json"], self.previous_bytes)
        self.assertEqual(self.ftp.files[USB + "/psx-diagnostics/USB-SHA256SUMS"], STAGE.installed_sums(self.previous["files"]).encode())
        self.assertNotIn(USB + "/psx-diagnostics/rootfs.ext4", self.ftp.files)
        self.assert_mutable_preserved()

    def seed_verified_backup(self):
        self.backup.mkdir()
        (self.backup / "installed.json").write_bytes(self.previous_bytes)
        (self.backup / "USB-SHA256SUMS").write_text(STAGE.installed_sums(self.previous["files"]))
        for name, data in self.old.items():
            path = self.backup / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.backup / "backup-verified.json").write_text(json.dumps({
            "status": "PASS", "target": self.previous["target"], "files": self.previous["files"]}))

    def test_receipt_identity_fields_and_claims_are_required(self):
        APP.verify_receipt(self.previous, SERIAL, CAPACITY, UUID)
        for field, value in (("owner", "other"), ("verified_readback", False), ("hardware_acceptance", True)):
            changed = dict(self.previous, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                APP.verify_receipt(changed, SERIAL, CAPACITY, UUID)
        for field, value in (("serial", "other"), ("bytes", CAPACITY + 1), ("uuid", "9999-ABCD")):
            changed = copy.deepcopy(self.previous); changed["target"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                APP.verify_receipt(changed, SERIAL, CAPACITY, UUID)

    def test_receipt_rejects_traversal_unrelated_and_mutable_paths(self):
        for name in ("../elsewhere", "/data/bzImage", "unrelated.txt", "psx-diagnostics/results/run.json",
                     "psx-diagnostics/state/key", "psx-diagnostics/backups/old", "psx-diagnostics/installed.json",
                     "psx-diagnostics/USB-SHA256SUMS", "psx-diagnostics/a\r\nDELE b"):
            changed = copy.deepcopy(self.previous); changed["files"][name] = "a" * 64
            with self.subTest(name=name), self.assertRaises(ValueError):
                APP.verify_receipt(changed, SERIAL, CAPACITY, UUID)

    def test_default_plan_never_mutates_ftp_or_creates_backups(self):
        before = dict(self.ftp.files)
        self.run_deploy(apply=False)
        self.assertEqual(self.ftp.files, before)
        self.assertEqual(self.ftp.mutations, [])
        self.assertFalse(self.backup.exists())

    def test_success_backs_up_changed_and_obsolete_files_preserving_user_state(self):
        self.new["psx-diagnostics/new-tools/helper.txt"] = b"new nested directory"
        self.write_source()
        self.run_deploy()
        result = json.loads(self.receipt_path.read_text())
        self.assertEqual(result["status"], "PASS")
        for name, payload in self.old.items():
            self.assertEqual((self.backup / name).read_bytes(), payload)
        self.assertEqual((self.backup / "installed.json").read_bytes(), self.previous_bytes)
        self.assertNotIn(USB + "/psx-diagnostics/rootfs.tar.gz", self.ftp.files)
        self.assertNotIn(USB + "/psx-diagnostics/source/old-source.tar.zst", self.ftp.files)
        for name, pin in result["files"].items():
            self.assertEqual(sha(self.ftp.files[USB + "/" + name]), pin)
        self.assert_mutable_preserved()

    def test_verified_existing_backup_can_be_reused_without_overwriting_old_bytes(self):
        self.seed_verified_backup()
        self.run_deploy(extra=("--reuse-verified-backup",))
        self.assertEqual(json.loads(self.receipt_path.read_text())["status"], "PASS")
        for name, data in self.old.items():
            self.assertEqual((self.backup / name).read_bytes(), data)
        self.assert_mutable_preserved()

    def test_reuse_rejects_a_corrupted_local_recovery_copy_before_remote_mutation(self):
        self.seed_verified_backup()
        (self.backup / "bzImage").write_bytes(b"corrupt local rollback kernel")
        with self.assertRaisesRegex(ValueError, "recovery"):
            self.run_deploy(extra=("--reuse-verified-backup",))
        self.assertEqual(self.ftp.mutations, [])

    def test_reuse_checks_unchanged_remote_files_before_any_mutation(self):
        self.seed_verified_backup()
        self.ftp.files[USB + "/vram.txt"] = b"unexpected changed setting\n"
        with self.assertRaises(ValueError):
            self.run_deploy(extra=("--reuse-verified-backup",))
        self.assertEqual(self.ftp.mutations, [])
        self.assert_mutable_preserved()

    def test_unowned_existing_destination_stops_before_any_remote_mutation(self):
        self.ftp.files[USB + "/psx-diagnostics/rootfs.ext4"] = b"unowned input"
        with self.assertRaisesRegex(ValueError, "unowned"):
            self.run_deploy()
        self.assertEqual(self.ftp.mutations, [])

    def test_inaccessible_existing_parent_is_not_treated_as_a_missing_destination(self):
        member = "psx-diagnostics/private-existing/helper.txt"
        self.new[member] = b"new helper"
        self.write_source()
        parent = USB + "/psx-diagnostics/private-existing"
        self.ftp.directories.add(parent)
        self.ftp.files[USB + "/" + member] = b"unowned existing helper"
        self.ftp.denied_directories.add(parent)
        with self.assertRaises(ftplib.error_perm):
            self.run_deploy()
        self.assertEqual(self.ftp.mutations, [])
        self.assertEqual(self.ftp.files[USB + "/" + member], b"unowned existing helper")

    def test_old_payload_drift_stops_before_remote_mutation(self):
        self.ftp.files[USB + "/bzImage"] = b"unexpected changed old kernel"
        with self.assertRaisesRegex(ValueError, "changed|recovery"):
            self.run_deploy()
        self.assertEqual(self.ftp.mutations, [])
        self.assert_mutable_preserved()

    def test_mid_publication_failure_restores_complete_old_set(self):
        self.ftp.fail_rename = (USB + "/initramfs.cpio.gz", "before")
        with self.assertRaises(OSError):
            self.run_deploy()
        self.assert_old_restored()
        self.assertIn('"event": "rollback_verified"', (self.backup / "journal.jsonl").read_text())

    def test_lost_rename_acknowledgement_still_restores_by_observed_hashes(self):
        self.ftp.fail_rename = (USB + "/initramfs.cpio.gz", "after")
        with self.assertRaises(OSError):
            self.run_deploy()
        self.assert_old_restored()

    def test_disconnected_rollback_never_claims_a_restored_set(self):
        self.ftp.fail_rename = (USB + "/initramfs.cpio.gz", "disconnect")
        with self.assertRaises(EOFError):
            self.run_deploy()
        journal = (self.backup / "journal.jsonl").read_text()
        self.assertIn('"event": "rollback_incomplete"', journal)
        self.assertNotIn('"event": "rollback_verified"', journal)
        for name, data in self.old.items():
            self.assertEqual((self.backup / name).read_bytes(), data)
        self.assert_mutable_preserved()

    def test_partial_generated_metadata_upload_restores_exact_old_metadata(self):
        self.ftp.fail_store = USB + "/psx-diagnostics/installed.json"
        with self.assertRaises(OSError):
            self.run_deploy()
        self.assert_old_restored()

    def test_corrupt_new_staging_never_replaces_an_active_file(self):
        self.ftp.corrupt_stage = True
        with self.assertRaisesRegex(ValueError, "staged"):
            self.run_deploy()
        self.assert_old_restored()

    def test_corrupt_old_staging_is_not_reactivated_or_reported_as_restored(self):
        self.ftp.corrupt_old = True
        self.ftp.fail_rename = (USB + "/initramfs.cpio.gz", "before")
        with self.assertRaises(OSError):
            self.run_deploy()
        journal = (self.backup / "journal.jsonl").read_text()
        self.assertIn('"event": "rollback_incomplete"', journal)
        self.assertNotIn('"event": "rollback_verified"', journal)
        self.assertFalse(any(payload == b"corrupt old recovery bytes" for path, payload in self.ftp.files.items()
                             if "/.psx-stage-" not in path))
        self.assert_mutable_preserved()

    def test_post_commit_cleanup_failure_keeps_verified_new_set(self):
        self.ftp.fail_delete = "/old/"
        with self.assertRaises(OSError):
            self.run_deploy()
        current = json.loads(self.ftp.files[USB + "/psx-diagnostics/installed.json"])
        self.assertEqual(current["profile"], self.metadata["profile"])
        for name, pin in current["files"].items():
            self.assertEqual(sha(self.ftp.files[USB + "/" + name]), pin)
        self.assertNotIn('"event": "rollback_verified"', (self.backup / "journal.jsonl").read_text())
        self.assert_mutable_preserved()

    def test_complete_payload_reserve_cap_stops_before_mutation(self):
        self.metadata["maximum_boot_set_bytes"] = 100_000
        self.write_source()
        with self.assertRaisesRegex(ValueError, "cap"):
            self.run_deploy()
        self.assertEqual(self.ftp.mutations, [])

    def test_ext4_format_and_declared_hash_are_checked(self):
        files, metadata = STAGE.source_files(self.source)
        self.assertIn("psx-diagnostics/rootfs.ext4", files)
        self.assertEqual(metadata["root_format"], "ext4-ro-loop")
        self.metadata["root_format"] = "gzip-tar-unpacked-into-tmpfs"
        self.write_source()
        with self.assertRaisesRegex(ValueError, "format/path"):
            STAGE.source_files(self.source)

    def test_new_os_source_cannot_claim_mutable_state(self):
        self.new["psx-diagnostics/state/ssh/private-key"] = b"not an OS payload"
        self.write_source()
        with self.assertRaisesRegex(ValueError, "reserved"):
            STAGE.source_files(self.source)


if __name__ == "__main__":
    unittest.main()
