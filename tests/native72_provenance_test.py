"""Preserve the tested native base and the exact contributor underscan patch."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("native72", str(ROOT / "scripts/check-native72"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
APP = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(APP)


class Native72ProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.name = "provenance/7.2-ps4-baikal-native-dev.json"
        self.record = json.loads((ROOT / self.name).read_text())
        names = {item["path"] for item in self.record["inputs"] + self.record["patches"]}
        names.update({self.name, "provenance/7.2-ps4-baikal.json", "provenance/6.18-ps4-baikal.json",
                      "targets/7.2-ps4-baikal.env", "platforms/ps4/config/7.2-psx-baikal.config",
                      self.record["series_path"], self.record["base_target"]["series_path"]})
        for name in names:
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)

    def save(self):
        (self.root / self.name).write_text(json.dumps(self.record))

    def rehash(self, name):
        for item in self.record["inputs"] + self.record["patches"]:
            if item["path"] == name:
                item["sha256"] = hashlib.sha256((self.root / name).read_bytes()).hexdigest()

    def test_exact_tested_native_inputs_are_accepted(self):
        self.assertEqual(APP.check_target(self.root), 65)

    def test_rehashed_hardware_config_change_is_rejected(self):
        name = self.record["config"]["path"]
        path = self.root / name
        path.write_bytes(path.read_bytes() + b"CONFIG_TEST_UNREVIEWED_HW=y\n")
        self.rehash(name)
        self.save()
        with self.assertRaisesRegex(ValueError, "beyond LOCALVERSION"):
            APP.check_target(self.root)

    def test_rehashed_patch_root_change_is_rejected(self):
        name = "targets/7.2-ps4-baikal-native-dev.env"
        path = self.root / name
        path.write_text(path.read_text().replace('PATCHES_DIR="platforms/ps4/patches/7.2-ps4-baikal"',
                                               'PATCHES_DIR="platforms/ps4/patches/7.2-ps4-baikal-native-dev"'))
        self.rehash(name)
        self.save()
        with self.assertRaisesRegex(ValueError, "build assignment"):
            APP.check_target(self.root)

    def test_rehashed_native_patch_change_is_rejected(self):
        name = self.record["native_autoload"]["path"]
        path = self.root / name
        path.write_bytes(path.read_bytes() + b"\n# unreviewed change\n")
        self.rehash(name)
        self.save()
        with self.assertRaisesRegex(ValueError, "tested patchset"):
            APP.check_target(self.root)

    def test_rehashed_contributor_underscan_change_is_rejected(self):
        name = self.record["psx_additions"][0]["path"]
        path = self.root / name
        path.write_bytes(path.read_bytes() + b"\n# changed contributor bytes\n")
        self.rehash(name)
        self.save()
        with self.assertRaisesRegex(ValueError, "tested patchset"):
            APP.check_target(self.root)


if __name__ == "__main__":
    unittest.main()
