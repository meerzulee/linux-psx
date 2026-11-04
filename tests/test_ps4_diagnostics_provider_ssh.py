"""Optional provider transactions using temporary files only; no SSH or devices."""
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("psx_provider_ssh_test", str(ROOT / "scripts/stage-ps4-diagnostics-provider-ssh"))
spec = importlib.util.spec_from_loader(loader.name, loader)
APP = importlib.util.module_from_spec(spec); sys.modules[spec.name] = APP; loader.exec_module(APP)


class ProviderSSHTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"; self.source.mkdir()
        self.usb = self.root / "usb"; (self.usb / "psx-diagnostics").mkdir(parents=True)
        self.providers = self.usb / APP.PREFIX
        patch = mock.patch.object(APP, "SIZE", 2 * APP.CHUNK + 7); patch.start(); self.addCleanup(patch.stop)
        self.image = b"x" * APP.SIZE
        self.config = {"schema_version": 1, "provider_manifest_sha256": "e" * 64}
        self.metadata = {"provider_id": "chromium-151.0.7922.169-psx1", "image": APP.IMAGE,
            "image_bytes": APP.SIZE, "image_sha256": hashlib.sha256(self.image).hexdigest(),
            "provider_manifest_sha256": "e" * 64, "hardware_acceptance": False, "launcher_config": self.config}
        self.data = {APP.IMAGE: self.image, "chromium-provider.json": json.dumps(self.config).encode(),
            "provider-manifest.json": json.dumps(self.metadata).encode()}
        for name, data in self.data.items(): (self.source / name).write_bytes(data)
        self.facts = {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in self.data.items()}
        self.identity = {"serial": "fixture-only", "bytes": 16 * 1024**3, "uuid": "FAKE-0123"}
        self.args = SimpleNamespace(source=self.source, receipt=self.root / "receipt.json", apply=False,
            host="fixture.invalid", port=22, host_key_alias="fixture-only", identity=self.root / "key",
            known_hosts=self.root / "known-hosts", expect_serial=self.identity["serial"],
            expect_bytes=self.identity["bytes"], expect_uuid=self.identity["uuid"])
        self.args.identity.write_text("synthetic key"); self.args.known_hosts.write_text("synthetic host pin")
        self.events = []; owner = self

        class FixtureUSB(APP.ProviderUSB):
            mountpoint = str(owner.usb)
            def identify(self): return dict(owner.identity), owner.usb.stat().st_dev

        self.fixture_usb = FixtureUSB

        class FixtureSession:
            def __init__(self, _): self.usb = FixtureUSB(owner.identity); self.queue = []
            def __enter__(self): self.usb.__enter__(); return self
            def __exit__(self, *args): self.usb.__exit__(*args)
            def send(self, request):
                if "files" in request:
                    self.files = request["files"]; self.missing = self.usb.inspect_provider(self.files)
                    self.queue.append(dict(status="INSPECTED", identity=self.usb.identity, missing=self.missing))
                elif request["operation"] == "stage":
                    self.missing = self.usb.inspect_provider(self.files)
                    if self.missing: self.usb.create_directory()
                    self.next_file()
                elif request["operation"] != "finish": raise AssertionError("Unexpected operation")
            def next_file(self):
                if self.missing:
                    self.name = self.missing.pop(0)
                    self.queue.append(dict(status="READY", name=self.name, partial=APP.PREFIX + ".fixture.partial"))
                else:
                    if self.usb.inspect_provider(self.files): raise AssertionError("Incomplete fixture")
                    self.queue.append(dict(status="PASS", verified_readback=True, files=self.files))
            def receive(self, timeout=120): return self.queue.pop(0)
            def upload(self, stream, size):
                owner.events.append(self.name)
                self.usb.store(self.name, self.files[self.name], stream)
                self.queue.append(dict(status="STAGED", name=self.name)); self.next_file()

        self.session = FixtureSession

    def test_default_plan_has_no_writes(self):
        result = APP.stage(self.args, session_factory=self.session)
        self.assertEqual(result["status"], "PLAN")
        self.assertEqual(result["missing"], list(APP.FILES))
        self.assertFalse(self.providers.exists()); self.assertFalse(self.args.receipt.exists())

    def test_apply_streams_image_then_sidecars_and_repeated_apply_only_reads(self):
        self.args.apply = True
        result = APP.stage(self.args, session_factory=self.session)
        self.assertEqual(self.events, list(APP.FILES))
        self.assertEqual(result["status"], "PASS"); self.assertTrue(result["verified_readback"])
        self.assertFalse(result["hardware_acceptance"])
        before = {name: (self.providers / name).stat().st_mtime_ns for name in APP.FILES}
        self.events.clear(); self.args.receipt = self.root / "repeat.json"
        self.assertEqual(APP.stage(self.args, session_factory=self.session)["missing"], [])
        self.assertEqual(self.events, [])
        for name, data in self.data.items():
            self.assertEqual((self.providers / name).read_bytes(), data)
            self.assertEqual((self.providers / name).stat().st_mtime_ns, before[name])
        self.assertEqual(json.loads(self.args.receipt.read_text())["status"], "PASS")

    def test_different_existing_file_refused_without_overwrite(self):
        self.providers.mkdir(); target = self.providers / APP.IMAGE; target.write_bytes(b"unrelated")
        self.args.apply = True
        with self.assertRaisesRegex(ValueError, "Different existing"):
            APP.stage(self.args, session_factory=self.session)
        self.assertEqual(target.read_bytes(), b"unrelated"); self.assertEqual(self.events, [])
        self.assertEqual(json.loads(self.args.receipt.read_text())["status"], "ERROR")

    def test_chunked_stream_and_incomplete_transfer_retains_partial(self):
        class BoundedStream(io.BytesIO):
            def read(self, count=-1):
                if not 0 < count <= APP.CHUNK: raise AssertionError("Unbounded image read")
                return super().read(count)
        with self.fixture_usb(self.identity) as usb:
            usb.create_directory()
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                usb.store(APP.IMAGE, self.facts[APP.IMAGE], BoundedStream(self.image[:-1]))
            self.assertEqual((self.usb / usb.partial).read_bytes(), self.image[:-1])
            self.assertFalse((self.providers / APP.IMAGE).exists())

    def test_digest_mismatch_retains_partial(self):
        with self.fixture_usb(self.identity) as usb:
            usb.create_directory()
            with self.assertRaisesRegex(ValueError, "readback differs"):
                usb.store(APP.IMAGE, self.facts[APP.IMAGE], io.BytesIO(b"y" * APP.SIZE))
            self.assertEqual((self.usb / usb.partial).stat().st_size, APP.SIZE)
            self.assertFalse((self.providers / APP.IMAGE).exists())

    def test_destination_race_cannot_overwrite(self):
        with self.fixture_usb(self.identity) as usb:
            usb.create_directory()
            def race(_): (self.providers / APP.IMAGE).write_bytes(b"raced")
            with self.assertRaisesRegex(OSError, "no-overwrite"):
                usb.store(APP.IMAGE, self.facts[APP.IMAGE], io.BytesIO(self.image), race)
            self.assertEqual((self.providers / APP.IMAGE).read_bytes(), b"raced")
            self.assertEqual((self.usb / usb.partial).read_bytes(), self.image)

    def test_symlinked_provider_directory_and_source_refused(self):
        outside = self.root / "outside"; outside.mkdir(); self.providers.symlink_to(outside, target_is_directory=True)
        with self.fixture_usb(self.identity) as usb:
            with self.assertRaises(OSError): usb.inspect_provider(self.facts)
        source_image = self.source / APP.IMAGE; source_image.unlink(); source_image.symlink_to(self.root / "missing")
        with self.assertRaises(OSError): APP.source_files(self.source)
        self.assertEqual(list(outside.iterdir()), [])

    def test_traversal_and_identity_change_refused(self):
        with self.fixture_usb(self.identity) as usb:
            with self.assertRaisesRegex(ValueError, "Unallowlisted"):
                usb.store("../bootargs.txt", self.facts[APP.IMAGE], io.BytesIO(self.image))
            with mock.patch.object(usb, "identify", return_value=({"serial": "wrong"}, usb.device)):
                with self.assertRaisesRegex(ValueError, "identity changed"): usb.create_directory()
        self.assertFalse(self.providers.exists())

    def test_cross_filesystem_directory_refused(self):
        self.providers.mkdir()
        with self.fixture_usb(self.identity) as usb:
            real_stat = os.fstat
            def changed_stat(fd):
                result = real_stat(fd)
                if result.st_ino == self.providers.stat().st_ino:
                    return SimpleNamespace(st_dev=result.st_dev + 1)
                return result
            with mock.patch.object(APP.os, "fstat", side_effect=changed_stat):
                with self.assertRaisesRegex(ValueError, "crosses another filesystem"):
                    usb.inspect_provider(self.facts)
        self.assertEqual(list(self.providers.iterdir()), [])

    def test_source_hash_and_malformed_metadata_fail_before_session(self):
        (self.source / APP.IMAGE).write_bytes(b"y" * APP.SIZE)
        forbidden = mock.Mock(side_effect=AssertionError("Must not connect"))
        with self.assertRaisesRegex(ValueError, "size/SHA256"):
            APP.stage(self.args, session_factory=forbidden)
        (self.source / "provider-manifest.json").write_text("null")
        with self.assertRaisesRegex(ValueError, "manifest/config"):
            APP.stage(self.args, session_factory=forbidden)
        forbidden.assert_not_called()

    def test_failed_upload_receipt_retains_partial_reference(self):
        self.args.apply = True
        with mock.patch.object(self.session, "upload", side_effect=OSError("synthetic connection loss")):
            with self.assertRaisesRegex(OSError, "connection loss"):
                APP.stage(self.args, session_factory=self.session)
        receipt = json.loads(self.args.receipt.read_text())
        self.assertEqual(receipt["status"], "ERROR")
        self.assertTrue(receipt["remote_partial"].endswith(".partial"))
        self.assertFalse((self.providers / APP.IMAGE).exists())

    def test_remote_program_compiles_and_strict_ssh_options_are_preserved(self):
        program = APP.provider_program(); compile(program, "provider-fixture", "exec")
        scope = {}; exec(program.rsplit("\nprovider_main()\n", 1)[0], scope)
        self.assertTrue(issubclass(scope["ProviderUSB"], scope["RemoteUSB"]))
        self.assertEqual(scope["FILES"], APP.FILES)
        command = APP.Session(self.args).command
        for pin in ("StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "HostKeyAlias=fixture-only",
                    "GlobalKnownHostsFile=/dev/null", "UpdateHostKeys=no"):
            self.assertIn(pin, command)


if __name__ == "__main__": unittest.main()
