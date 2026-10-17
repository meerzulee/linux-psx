import importlib.util
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_release", ROOT / "scripts/ci/package_release.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
RELEASE = "6.18.44-psx-ps4-baikal1"


class ReleaseContractTests(unittest.TestCase):
    def test_optional_ethernet_bundle_preserves_tool_and_has_portable_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            commit = "c" * 40
            package.package_ethernet_tool(ROOT, destination, commit)
            tool = (destination / "ps4-ethernet").read_bytes()
            self.assertEqual(tool, (ROOT / "contrib/networkmanager/ps4-ethernet").read_bytes())
            self.assertIn(b"SPDX-License-Identifier: MIT", tool)
            guide = (destination / "ETHERNET-SETUP.md").read_text()
            self.assertIn('--setup --manifest "$PWD/artifacts.json"', guide)
            self.assertIn("(BOOTING.md)", guide)
            self.assertIn("(ETHERNET.md)", guide)
            self.assertNotIn("../../", guide)
            self.assertIn(f"/blob/{commit}/provenance/ethernet-activation-import.json", guide)

    def test_firmware_embedding_and_wrong_release_are_refused(self):
        config = (ROOT / "platforms/ps4/config/6.18-psx-baikal.config").read_text()
        package.check_config(config, RELEASE)
        with self.assertRaisesRegex(ValueError, "must not embed"):
            package.check_config(config.replace('CONFIG_EXTRA_FIRMWARE=""', 'CONFIG_EXTRA_FIRMWARE="vendor.bin"'), RELEASE)
        with self.assertRaisesRegex(ValueError, "missing target"):
            package.check_config(config.replace('CONFIG_LOCALVERSION="-psx-ps4-baikal1"', 'CONFIG_LOCALVERSION="-wrong"'), RELEASE)

    def test_complete_source_includes_ignored_patch_input_not_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            for name, data in {"COPYING": "license", "LICENSES/preferred/GPL-2.0": "GPL", "tracked.c": "base", ".gitignore": "*.o\n.config\nignored-source.h\n"}.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(data)
            subprocess.run(["git", "add", "."], cwd=source, check=True)
            (source / "tracked.c").write_text("patched")
            for name in ["drivers/net/ethernet/psx_mts_net.c", "drivers/net/ethernet/mts_net_core.h", ".config", "build.o", "ignored-source.h"]:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("source")
            result = package.source_files(source, {"ignored-source.h"})
            for name in ["ignored-source.h", ".config", "tracked.c", "drivers/net/ethernet/psx_mts_net.c", "LICENSES/preferred/GPL-2.0"]:
                self.assertIn(name, result)
            self.assertNotIn("build.o", result)
            firmware = source / "firmware/vendor.bin"
            firmware.parent.mkdir()
            firmware.write_bytes(b"firmware")
            with self.assertRaisesRegex(ValueError, "unexpected staged firmware"):
                package.source_files(source)

    def test_module_archive_refuses_escape_links_and_other_releases(self):
        for name in [f"lib/modules/{RELEASE}/kernel/driver.ko", "lib", "lib/modules"]:
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE if name in ("lib", "lib/modules") else tarfile.REGTYPE
            package.check_module_member(member, RELEASE)
        for name in ["/etc/passwd", f"lib/modules/{RELEASE}/../../etc/passwd", "lib/modules/wrong/driver.ko"]:
            with self.assertRaisesRegex(ValueError, "unsafe module archive"):
                package.check_module_member(tarfile.TarInfo(name), RELEASE)
        member = tarfile.TarInfo(f"lib/modules/{RELEASE}/source")
        member.type = tarfile.SYMTYPE
        member.linkname = "/private/source"
        with self.assertRaisesRegex(ValueError, "unexpected module archive member type"):
            package.check_module_member(member, RELEASE)


if __name__ == "__main__":
    unittest.main()
