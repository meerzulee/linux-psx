"""Reject PS5 candidate drift even when edited local files are rehashed."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("ps5_provenance", str(ROOT / "scripts/check-provenance"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
APP = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(APP)


class PS5ProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.manifest_path = self.root / "provenance/7.2-ps5.json"
        self.record = json.loads((ROOT / "provenance/7.2-ps5.json").read_text())
        names = {item["path"] for item in self.record["inputs"] + self.record["imports"]}
        names.update({"provenance/7.2-ps5.json", "provenance/ps5-research.json", self.record["series_path"]})
        for name in names:
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        override = patch.object(APP, "ROOT", self.root)
        override.start()
        self.addCleanup(override.stop)

    def save(self):
        self.manifest_path.write_text(json.dumps(self.record))

    def update_input_digest(self, name):
        digest = hashlib.sha256((self.root / name).read_bytes()).hexdigest()
        for item in self.record["inputs"]:
            if item["path"] == name:
                item["sha256"] = digest
                return
        self.fail("Fixture input was not recorded")

    def test_import_and_documented_config_delta_are_accepted(self):
        self.assertEqual(APP.check_ps5_target(), 1)

    def test_rehashed_unrecorded_hardware_tweak_is_rejected(self):
        name = self.record["config"]["path"]
        path = self.root / name
        original = path.read_text()
        self.assertIn("CONFIG_NR_CPUS=16\n", original)
        path.write_text(original.replace("CONFIG_NR_CPUS=16\n", "CONFIG_NR_CPUS=8\n"))
        self.update_input_digest(name)
        self.save()
        with self.assertRaisesRegex(ValueError, "changes beyond"):
            APP.check_ps5_target()

    def test_rehashed_ps4_config_fragment_cannot_enter_the_ps5_target(self):
        name = "targets/7.2-ps5.env"
        path = self.root / name
        path.write_text(path.read_text().replace('CONFIG_FRAGMENTS=""',
                        'CONFIG_FRAGMENTS="platforms/ps4/config/fragments/netfilter-ufw.config"'))
        self.update_input_digest(name)
        self.save()
        with self.assertRaisesRegex(ValueError, "without extra fragments"):
            APP.check_ps5_target()

    def test_rehashed_reference_cannot_replace_the_pinned_original(self):
        name = self.record["config"]["derived_from"]
        path = self.root / name
        path.write_bytes(path.read_bytes() + b"# modified upstream reference\n")
        self.update_input_digest(name)
        for item in self.record["imports"]:
            if item["path"] == name:
                item.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size)
        self.save()
        with self.assertRaisesRegex(ValueError, "differs from its original source"):
            APP.check_ps5_target()


if __name__ == "__main__":
    unittest.main()
