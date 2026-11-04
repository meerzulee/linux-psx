"""Frontend SSH transactions against ordinary temporary files; no SSH/devices."""
import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / path))
    spec = importlib.util.spec_from_loader(name, loader)
    app = importlib.util.module_from_spec(spec); sys.modules[name] = app
    loader.exec_module(app)
    return app


APP = load("psx_frontend_ssh_tests", "scripts/update-ps4-diagnostics-frontend-ssh")
FIXTURE = load("psx_frontend_ssh_fixtures", "tests/test_ps4_diagnostics_frontend_ftp.py")


class FrontendSSHTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FIXTURE.FrontendFTPTests("test_default_plan_has_no_remote_or_local_mutation")
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.temp = self.fixture.temp
        self.usb = self.temp / "usb"; self.usb.mkdir()
        self.events = []
        self.fail = None
        self.failed_once = False
        self.identity = {"serial": FIXTURE.SERIAL, "bytes": FIXTURE.CAPACITY, "uuid": FIXTURE.UUID}
        self.args = SimpleNamespace(source=self.fixture.source, backup=self.fixture.backup,
            receipt=self.fixture.receipt, host="fixture.invalid", port=2222,
            identity=self.temp / "identity", known_hosts=self.temp / "known_hosts",
            expect_serial=FIXTURE.SERIAL, expect_bytes=FIXTURE.CAPACITY,
            expect_uuid=FIXTURE.UUID, apply=True)
        self.args.identity.write_bytes(b"synthetic identity; never used for a connection")
        self.args.known_hosts.write_bytes(b"synthetic known_hosts")
        for name, data in self.fixture.old.items():
            path = self.usb / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        for name, data in {"installed.json": self.fixture.previous_raw, "USB-SHA256SUMS": self.fixture.sums}.items():
            (self.usb / "psx-diagnostics" / name).write_bytes(data)
        for name, data in {"results/operator.json": b"operator result", "state/ssh/key": b"synthetic state"}.items():
            path = self.usb / "psx-diagnostics" / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        owner = self

        class FixtureUSB(APP.RemoteUSB):
            mountpoint = str(owner.usb)

            def identify(self):
                identity = dict(owner.identity)
                if owner.fail == "identity":
                    identity["serial"] = "other-usb"
                return identity, owner.usb.stat().st_dev

            def write_staged(self, name, data):
                # The first consequential remote action must follow a complete
                # read-back host backup, not merely the backup directory mkdir.
                manifest = json.loads((owner.args.backup / "backup.json").read_text())
                owner.assertTrue(manifest["recovery_complete"])
                for old_name, old in self.snapshot.items():
                    owner.assertEqual((owner.args.backup / old_name).read_bytes(), old)
                owner.events.append(("stage", name))
                return super().write_staged(name, data)

            def read(self, name, maximum=None):
                data = super().read(name, maximum)
                if owner.failed_once:
                    return data
                if owner.fail == "stage-readback" and "/new/" in name:
                    owner.failed_once = True
                    return b"bad staged readback"
                if (owner.fail == "published-readback" and name == "initramfs.cpio.gz"
                        and data == owner.fixture.new["initramfs.cpio.gz"]):
                    owner.failed_once = True
                    return b"bad published readback"
                if (owner.fail == "receipt-readback" and name == "psx-diagnostics/USB-SHA256SUMS"
                        and data != owner.fixture.sums):
                    owner.failed_once = True
                    return b"bad ownership receipt readback"
                return data

            def replace(self, staged, destination):
                owner.events.append(("replace", destination))
                if (owner.fail == "lost-replace-ack" and destination == "initramfs.cpio.gz"
                        and "/new/" in staged and not owner.failed_once):
                    owner.failed_once = True
                    super().replace(staged, destination)
                    raise OSError("synthetic lost atomic-replace acknowledgement")
                result = super().replace(staged, destination)
                if (owner.fail == "unknown-bytes" and destination == "initramfs.cpio.gz"
                        and "/new/" in staged):
                    (owner.usb / destination).write_bytes(b"unknown externally modified bytes")
                return result

        self.fixture_usb = FixtureUSB

        class Session:
            def __init__(self, _):
                self.usb = FixtureUSB(owner.identity)
                self.response = None

            def __enter__(self):
                self.usb.__enter__()
                return self

            def __exit__(self, *args):
                self.usb.__exit__(*args)

            def send(self, request):
                if request["operation"] == "inspect":
                    self.response = dict(status="INSPECTED", **self.usb.inspect())
                elif request["operation"] == "commit":
                    self.response = self.usb.commit(request)
                elif request["operation"] != "finish":
                    raise AssertionError("Unexpected transaction phase")

            def receive(self):
                return self.response

        self.session = Session

    def run_update(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return APP.update(self.args, session_factory=self.session)

    def assert_original(self):
        for name, data in self.fixture.old.items():
            self.assertEqual((self.usb / name).read_bytes(), data, name)
        self.assertEqual((self.usb / "psx-diagnostics/installed.json").read_bytes(), self.fixture.previous_raw)
        self.assertEqual((self.usb / "psx-diagnostics/USB-SHA256SUMS").read_bytes(), self.fixture.sums)
        self.assertFalse(self.args.receipt.exists())

    def test_default_plan_performs_no_remote_or_local_writes(self):
        self.args.apply = False
        result = self.run_update()
        self.assertEqual(result["status"], "PLAN")
        self.assertEqual(self.events, [])
        self.assertFalse(self.args.backup.exists())
        self.assertEqual(list(self.usb.glob(".psx-frontend-*")), [])
        self.assert_original()

    def test_wrong_live_identity_refused_before_backup_or_mutation(self):
        self.fail = "identity"
        with self.assertRaisesRegex(ValueError, "identity"):
            self.run_update()
        self.assertEqual(self.events, [])
        self.assertFalse(self.args.backup.exists())
        self.assert_original()

    def test_immutable_metadata_drift_refused_before_backup_or_mutation(self):
        original = copy.deepcopy(self.fixture.metadata)
        for key, value in (("config_sha256", "f"*64), ("unexpected_field", None)):
            with self.subTest(key=key):
                self.fixture.metadata = copy.deepcopy(original)
                self.fixture.metadata[key] = value
                self.fixture.write_source()
                with self.assertRaisesRegex(ValueError, "immutable"):
                    self.run_update()
                self.assertFalse(self.args.backup.exists())
                self.assertEqual(self.events, [])

    def test_unrelated_root_bytes_cannot_change(self):
        self.fixture.new["psx-diagnostics/rootfs.ext4"] = b"unrelated changed root"
        self.fixture.metadata["rootfs_sha256"] = APP.pin(b"unrelated changed root")
        self.fixture.write_source()
        with self.assertRaisesRegex(ValueError, "unrelated"):
            self.run_update()
        self.assertEqual(self.events, [])
        self.assert_original()

    def test_success_preserves_bootargs_kernel_root_and_operator_state(self):
        result = self.run_update()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["verified_readback"])
        self.assertEqual(result["changed_files"], sorted(APP.PAYLOAD))
        self.assertEqual((self.usb / "bootargs.txt").read_bytes(), self.fixture.old["bootargs.txt"])
        for name in ("bzImage", "psx-diagnostics/rootfs.ext4", "psx-diagnostics/config"):
            self.assertEqual((self.usb / name).read_bytes(), self.fixture.old[name])
        self.assertEqual((self.usb / "psx-diagnostics/results/operator.json").read_bytes(), b"operator result")
        self.assertEqual((self.usb / "psx-diagnostics/state/ssh/key").read_bytes(), b"synthetic state")
        self.assertEqual({name for event, name in self.events if event == "replace"}, set(APP.PAYLOAD+APP.RECEIPTS))
        marker = json.loads((self.usb / "psx-diagnostics/installed.json").read_bytes())
        self.assertEqual((self.usb / "psx-diagnostics/USB-SHA256SUMS").read_bytes(),
                         APP.DEPLOY.STAGE.installed_sums(marker["files"]).encode())
        self.assertEqual(json.loads(self.args.receipt.read_text())["status"], "PASS")

    def test_staging_readback_failure_publishes_nothing(self):
        self.fail = "stage-readback"
        with self.assertRaisesRegex(ValueError, "Staged"):
            self.run_update()
        self.assertFalse(any(event == "replace" for event, _ in self.events))
        self.assert_original()

    def test_published_readback_failure_restores_only_known_old_bytes(self):
        self.fail = "published-readback"
        with self.assertRaisesRegex(ValueError, "Published"):
            self.run_update()
        self.assert_original()
        self.assertTrue(self.args.backup.exists())

    def test_receipt_readback_failure_restores_payload_and_both_old_receipts(self):
        self.fail = "receipt-readback"
        with self.assertRaisesRegex(ValueError, "Published"):
            self.run_update()
        self.assert_original()

    def test_host_backup_failure_prevents_remote_staging(self):
        with mock.patch.object(APP, "backup_files", side_effect=OSError("synthetic backup failure")):
            with self.assertRaisesRegex(OSError, "backup failure"):
                self.run_update()
        self.assertEqual(self.events, [])
        self.assertEqual(list(self.usb.glob(".psx-frontend-*")), [])
        self.assert_original()

    def test_lost_atomic_replace_acknowledgement_rolls_back(self):
        self.fail = "lost-replace-ack"
        with self.assertRaisesRegex(OSError, "acknowledgement"):
            self.run_update()
        self.assert_original()

    def test_rollback_refuses_unknown_destination_bytes(self):
        self.fail = "unknown-bytes"
        with self.assertRaisesRegex(ValueError, "Rollback refused unknown"):
            self.run_update()
        self.assertEqual((self.usb / "initramfs.cpio.gz").read_bytes(), b"unknown externally modified bytes")
        self.assertFalse(self.args.receipt.exists())

    def test_directory_fd_keeps_reads_on_original_filesystem_directory(self):
        with self.fixture_usb(self.identity) as usb:
            expected = usb.inspect()
            detached = self.temp / "detached"
            self.usb.rename(detached); self.usb.mkdir()
            (self.usb / "unrelated").write_bytes(b"replacement mountpoint contents")
            self.assertEqual(usb.inspect(), expected)
            self.assertEqual((self.usb / "unrelated").read_bytes(), b"replacement mountpoint contents")

    def test_symlinked_usb_metadata_directory_is_refused(self):
        metadata = self.usb / "psx-diagnostics"
        outside = self.temp / "outside"
        metadata.rename(outside); metadata.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.run_update()
        self.assertEqual(self.events, [])

    def test_ssh_requires_explicit_pins_and_quotes_constant_remote_program(self):
        session = APP.SSHSession(self.args)
        self.assertIn("StrictHostKeyChecking=yes", session.command)
        self.assertIn("IdentitiesOnly=yes", session.command)
        self.assertIn("GlobalKnownHostsFile=/dev/null", session.command)
        self.assertIn("UpdateHostKeys=no", session.command)
        self.assertIn("root@fixture.invalid", session.command)
        self.assertEqual(session.command[session.command.index("-p")+1], "2222")
        self.args.host = "fixture.invalid; touch should-never-run"
        with self.assertRaises(ValueError):
            APP.SSHSession(self.args)
        compile(APP.remote_program(), "<remote-updater>", "exec")

    def test_partial_pipe_writes_are_fully_transferred(self):
        session = APP.SSHSession(self.args)
        sent = bytearray()

        def short_write(data):
            sent.extend(data[:7])
            return min(7, len(data))

        session.process = SimpleNamespace(stdin=SimpleNamespace(write=short_write, flush=lambda: None))
        request = {"operation": "fixture", "data": "x"*1024}
        session.send(request)
        self.assertEqual(json.loads(sent), request)


class LiveIdentityTests(unittest.TestCase):
    def test_exact_live_serial_capacity_uuid_and_partition_are_required(self):
        expected = {"serial": "TEST-USB-0001", "bytes": 15791554560, "uuid": "ABCD-1234"}
        device = os.makedev(8, 17)
        mount = {"target": APP.REMOTE_ROOT, "source": "/dev/sdb1", "fstype": "vfat",
                 "options": "rw,nosuid,nodev,noexec", "maj:min": "8:17"}
        inventory = {"blockdevices": [{"path": "/dev/sdb1", "type": "part", "fstype": "vfat",
            "uuid": expected["uuid"], "maj:min": "8:17", "children": [{"path": "/dev/sdb", "type": "disk",
            "tran": "usb", "serial": expected["serial"], "size": expected["bytes"]}]}]}
        self.assertEqual(APP.validate_identity(mount, inventory, device, expected), expected)
        for field, value in (("serial", "different-usb"), ("bytes", 16000000000), ("uuid", "1234-ABCD")):
            bad = dict(expected, **{field: value})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Live USB"):
                APP.validate_identity(mount, inventory, device, bad)
        with self.assertRaises(ValueError):
            APP.validate_identity(dict(mount, target="/run"), inventory, device, expected)
        with self.assertRaises(ValueError):
            APP.validate_identity(mount, inventory, os.makedev(8, 18), expected)

    def test_remote_identity_uses_readonly_findmnt_and_scoped_lsblk(self):
        expected = {"serial": "TEST-USB-0001", "bytes": 15791554560, "uuid": "ABCD-1234"}
        device = os.makedev(8, 17)
        mount = {"target": APP.REMOTE_ROOT, "source": "/dev/sdb1", "fstype": "vfat",
                 "options": "rw,nosuid,nodev,noexec", "maj:min": "8:17"}
        inventory = {"blockdevices": [{"path": "/dev/sdb1", "type": "part", "fstype": "vfat",
            "uuid": expected["uuid"], "maj:min": "8:17", "children": [{"path": "/dev/sdb", "type": "disk",
            "tran": "usb", "serial": expected["serial"], "size": expected["bytes"]}]}]}
        commands = [SimpleNamespace(stdout=json.dumps({"filesystems": [mount]})),
                    SimpleNamespace(stdout=json.dumps(inventory))]
        with mock.patch.object(APP.os, "geteuid", return_value=0), \
             mock.patch.object(APP.os, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=device)), \
             mock.patch.object(APP.subprocess, "run", side_effect=commands) as run:
            self.assertEqual(APP.RemoteUSB(expected).identify(), (expected, device))
        self.assertEqual(run.call_args_list[0].args[0][0], "findmnt")
        self.assertIn(APP.REMOTE_ROOT, run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0][0], "lsblk")
        self.assertIn("--inverse", run.call_args_list[1].args[0])
        self.assertEqual(run.call_args_list[1].args[0][-1], "/dev/sdb1")


if __name__ == "__main__":
    unittest.main()
