"""Synthetic optional-provider checks; never mounts, launches a browser or contacts a console."""
import copy
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = runpy.run_path(str(ROOT / "rootfs/ps4-diagnostics-desktop/launch-chromium-provider"))
SPEC = json.loads((ROOT / "manifests/ps4-chromium-provider.json").read_text())


class ChromiumProviderTests(unittest.TestCase):
    def test_only_explicit_readonly_executable_ext4_mount_is_accepted(self):
        row = "110 30 7:2 / /run/psx-chromium ro,nosuid,nodev,relatime - ext4 /dev/loop1 ro,noload\n"
        self.assertTrue(APP["mounted_readonly_exec"](row))
        for altered in (row.replace("ro,nosuid", "rw,nosuid"), row.replace("relatime", "noexec"),
                        row.replace("- ext4", "- vfat"), row.replace("/run/psx-chromium", "/another"), row + row):
            self.assertFalse(APP["mounted_readonly_exec"](altered))

    def test_fixed_command_keeps_sandbox_and_uses_opengl_comparison(self):
        argv = APP["command"](SPEC, SPEC["mesa_manifest_sha256"])
        self.assertEqual(argv[-1], "http://127.0.0.1:8080/lab")
        for flag in ("--ozone-platform=x11", "--use-gl=angle", "--use-angle=gl", "--disable-features=Vulkan,VulkanFromANGLE"):
            self.assertIn(flag, argv)
        self.assertFalse(any("no-sandbox" in x or "disable-gpu-sandbox" in x or "ignore-gpu-blocklist" in x for x in argv))
        self.assertTrue(any(x.startswith("--user-data-dir=/home/psx/") for x in argv))

    def test_changed_flags_or_remote_lab_cannot_change_fixed_launch_scope(self):
        for key, value in (("lab_url", "https://example.com/"), ("browser_flags", SPEC["browser_flags"] + ["--no-sandbox"]),
                           ("kernel_release", "another-kernel")):
            altered = copy.deepcopy(SPEC); altered[key] = value
            with self.assertRaises(ValueError): APP["command"](altered, SPEC["mesa_manifest_sha256"])


if __name__ == "__main__": unittest.main()
