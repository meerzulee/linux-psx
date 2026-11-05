"""Synthetic FTP deployment/recovery checks; no network, mounts or devices."""
import contextlib
import copy
from datetime import datetime, timedelta
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
        self.retrievals = []
        self.timestamps = {name: "20261105120000" for name in self.files}
        self.metadata_clock = 0
        self.metadata_disabled = False
        self.unstable_metadata = False
        self.missing_stage_metadata = False
        self.rename_metadata_change = None
        self.on_retrieve = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def connect(self, host, port):
        if (host, port) != ("fixture.invalid", 2121):
            raise AssertionError("test attempted an unexpected endpoint")

    def login(self):
        return "230 synthetic"

    def stamp(self):
        self.metadata_clock += 1
        return (datetime(2026, 11, 5, 12) + timedelta(seconds=self.metadata_clock)).strftime("%Y%m%d%H%M%S")

    def voidcmd(self, command):
        if command != "TYPE I": raise AssertionError("unexpected FTP command")
        return "200 binary"

    def size(self, path):
        if self.metadata_disabled: raise ftplib.error_perm("502 SIZE unavailable")
        return len(self.files[normal(path)])

    def sendcmd(self, command):
        if not command.startswith("MDTM "): raise AssertionError("unexpected FTP command")
        path = normal(command[5:])
        if self.metadata_disabled or self.missing_stage_metadata and "/new/" in path:
            raise ftplib.error_perm("502 MDTM unavailable")
        if self.unstable_metadata: self.timestamps[path] = self.stamp()
        return "213 " + self.timestamps.setdefault(path, "20261105120000")

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
        self.retrievals.append(path)
        if path not in self.files:
            raise ftplib.error_perm("550 File unavailable")
        payload = self.files[path]
        for start in range(0, len(payload), blocksize):
            callback(payload[start:start + blocksize])
        if self.on_retrieve: self.on_retrieve(path)
        return "226 synthetic"

    def storbinary(self, command, stream, blocksize=8192, callback=None):
        path = normal(command.removeprefix("STOR "))
        if str(PurePosixPath(path).parent) not in self.directories:
            raise ftplib.error_perm("550 Parent absent")
        self.mutations.append(("store", path))
        self.timestamps[path] = self.stamp()
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
        self.timestamps[target] = self.timestamps.pop(source, "20261105120000")
        if self.rename_metadata_change and self.rename_metadata_change in target:
            self.rename_metadata_change = None
            self.timestamps[target] = self.stamp()
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
        self.timestamps.pop(path, None)


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

    def test_readback_once_hashes_every_new_stage_and_old_backup_exactly_once(self):
        self.run_deploy(extra=("--readback-once",))
        result = json.loads(self.receipt_path.read_text())
        self.assertEqual(result["status"], "PASS")
        marker = json.loads((self.backup / "backup-verified.json").read_text())
        self.assertEqual(set(marker["remote_fingerprints"]), set(self.old))
        for name in self.old:
            self.assertEqual(self.ftp.retrievals.count(USB + "/" + name), 1, name)
        new_root = "psx-diagnostics/rootfs.ext4"
        root_reads = [name for name in self.ftp.retrievals if name.endswith("/" + new_root)]
        self.assertEqual(len(root_reads), 1)
        self.assertIn("/new/", root_reads[0])
        self.assertFalse(any("/old/" in name for name in self.ftp.retrievals))
        current = json.loads(self.ftp.files[USB + "/psx-diagnostics/installed.json"])
        self.assertIn("SIZE/MDTM", current["readback_once"]["verified"])
        self.assert_mutable_preserved()

    def test_default_retains_full_final_readbacks_and_works_without_metadata_commands(self):
        self.ftp.metadata_disabled = True
        self.run_deploy()
        self.assertEqual(sum(path.endswith("/psx-diagnostics/rootfs.ext4") for path in self.ftp.retrievals), 2)
        self.assertTrue((self.backup / "backup-verified.json").is_file())
        self.assertNotIn("remote_fingerprints", json.loads((self.backup / "backup-verified.json").read_text()))

    def test_readback_once_missing_or_unstable_metadata_refuses_before_mutation(self):
        for failure in ("metadata_disabled", "unstable_metadata"):
            setattr(self.ftp, failure, True)
            with self.subTest(failure=failure), self.assertRaisesRegex(ValueError, "SIZE|MDTM|fingerprint"):
                self.run_deploy(extra=("--readback-once",))
            setattr(self.ftp, failure, False)
            self.assertFalse(self.ftp.mutations); self.assertFalse(self.backup.exists())
        self.assert_mutable_preserved()

    def test_metadata_change_during_full_backup_refuses_before_any_remote_mutation(self):
        def changed(path):
            if path == USB + "/bzImage": self.ftp.timestamps[path] = self.ftp.stamp()
        self.ftp.on_retrieve = changed
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.run_deploy(extra=("--readback-once",))
        self.assertFalse(self.ftp.mutations)
        self.assertFalse((self.backup / "backup-verified.json").exists())

    def test_fast_mode_still_requires_full_staged_sha_and_never_publishes_corrupt_bytes(self):
        self.ftp.corrupt_stage = True
        with self.assertRaisesRegex(ValueError, "staged"):
            self.run_deploy(extra=("--readback-once",))
        self.assertTrue(any("/new/" in name for name in self.ftp.retrievals))
        self.assertFalse(any(operation[0] == "rename" for operation in self.ftp.mutations))
        self.assert_old_restored()

    def test_changed_rename_fingerprint_restores_the_exact_original_set(self):
        self.ftp.rename_metadata_change = USB + "/bzImage"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.run_deploy(extra=("--readback-once",))
        self.assert_old_restored()
        self.assertIn('"event": "rollback_verified"', (self.backup / "journal.jsonl").read_text())

    def test_missing_new_metadata_aborts_staging_without_publishing(self):
        self.ftp.missing_stage_metadata = True
        with self.assertRaisesRegex(ValueError, "MDTM"):
            self.run_deploy(extra=("--readback-once",))
        self.assertFalse(any(operation[0] == "rename" for operation in self.ftp.mutations))
        self.assert_old_restored()

    def test_reused_backup_requires_verified_matching_original_fingerprints(self):
        self.seed_verified_backup()
        extra = ("--readback-once", "--reuse-verified-backup")
        with self.assertRaisesRegex(ValueError, "fingerprints"): self.run_deploy(extra=extra)
        self.assertFalse(self.ftp.mutations)
        marker_path = self.backup / "backup-verified.json"
        marker = json.loads(marker_path.read_text())
        marker["remote_fingerprints"] = {name: APP.fingerprint(self.ftp, USB + "/" + name) for name in self.old}
        marker_path.write_text(json.dumps(marker))
        original = self.ftp.timestamps[USB + "/bzImage"]
        self.ftp.timestamps[USB + "/bzImage"] = self.ftp.stamp()
        with self.assertRaisesRegex(ValueError, "fingerprints"): self.run_deploy(extra=extra)
        self.assertFalse(self.ftp.mutations)
        self.ftp.timestamps[USB + "/bzImage"] = original
        self.ftp.retrievals.clear()
        self.run_deploy(extra=extra)
        self.assertEqual(json.loads(self.receipt_path.read_text())["status"], "PASS")
        self.assertFalse(any(USB + "/" + name in self.ftp.retrievals for name in self.old))
        self.assert_mutable_preserved()

    def test_fingerprint_rejects_malformed_server_metadata(self):
        path = USB + "/bzImage"
        for value in ("213 unknown", "213 20261305120000", "250 20261105120000", "213 20261105120000\r\n250 injected"):
            with mock.patch.object(self.ftp, "sendcmd", return_value=value), self.assertRaises(ValueError): APP.fingerprint(self.ftp, path)
        for value in (None, True, -1, 4 * 1024**3 + 1):
            with mock.patch.object(self.ftp, "size", return_value=value), self.assertRaises(ValueError): APP.fingerprint(self.ftp, path)


if __name__ == "__main__":
    unittest.main()
