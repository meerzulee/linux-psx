"""Source provenance recording uses only synthetic regular text files."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("diagnostics_source_receipt_tests", str(ROOT / "scripts/check-ps4-diagnostics"))
spec = importlib.util.spec_from_loader(loader.name, loader)
APP = importlib.util.module_from_spec(spec)
loader.exec_module(APP)


class SourceReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "provenance").mkdir()
        self.receipt = self.root / "provenance/diagnostics-import.json"
        self.source = self.root / "diagnostics/ps4/helper.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("# changed source\n")
        self.original = {"path": "diagnostics/ps4/helper.py", "origin_sha256": "a"*64,
                         "import_sha256": "b"*64, "destination_sha256": "b"*64,
                         "destination_bytes": 8, "mode": "0644"}
        self.receipt.write_text(json.dumps({"files": [self.original], "generated_files": []}))
        patch = mock.patch.object(APP, "REPO", self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def test_adaptation_retains_original_identities_and_appends_an_explicit_reason(self):
        APP.record_adaptations(["diagnostics/ps4/helper.py"], "Explicit new source behavior")
        item = json.loads(self.receipt.read_text())["files"][0]
        self.assertEqual(item["origin_sha256"], self.original["origin_sha256"])
        self.assertEqual(item["import_sha256"], self.original["import_sha256"])
        self.assertEqual(item["destination_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(item["destination_adaptations"][0]["prior_destination_sha256"], "b"*64)
        self.assertEqual(item["destination_adaptations"][0]["reason"], "Explicit new source behavior")

    def test_new_linux_source_does_not_claim_an_omarchy_import_identity(self):
        source = self.source.with_name("new.py")
        source.write_text("# new source\n")
        APP.record_adaptations(["diagnostics/ps4/new.py"], "New optional diagnostic module")
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["files"], [self.original])
        self.assertEqual(receipt["generated_files"][0]["path"], "diagnostics/ps4/new.py")
        self.assertNotIn("origin_sha256", receipt["generated_files"][0])

    def test_binary_symlink_and_escape_are_refused_without_partial_receipt_updates(self):
        data = self.receipt.read_bytes()
        binary = self.source.with_name("binary.py")
        binary.write_bytes(b"bad\0binary")
        symlink = self.source.with_name("link.py")
        symlink.symlink_to(self.source)
        for name in ("../outside", "diagnostics/ps4/binary.py", "diagnostics/ps4/link.py"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                APP.record_adaptations(["diagnostics/ps4/helper.py", name], "Deliberate test change")
            self.assertEqual(self.receipt.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
