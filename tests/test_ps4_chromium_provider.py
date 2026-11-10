"""Synthetic optional-provider checks; never mounts, launches a browser or contacts a console."""
import copy
import json
from pathlib import Path, PurePosixPath
import runpy
import stat
from types import SimpleNamespace
import unittest
from unittest import mock

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
        # Chromium registers with Google on its own otherwise, and DiagnostiX promises it sends nothing.
        self.assertIn("--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1", argv)
        self.assertTrue(any(x.startswith("--user-data-dir=/home/psx/") for x in argv))

    def test_changed_flags_or_remote_lab_cannot_change_fixed_launch_scope(self):
        for key, value in (("lab_url", "https://example.com/"), ("browser_flags", SPEC["browser_flags"] + ["--no-sandbox"]),
                           ("kernel_release", "another-kernel")):
            altered = copy.deepcopy(SPEC); altered[key] = value
            with self.assertRaises(ValueError): APP["command"](altered, SPEC["mesa_manifest_sha256"])


class LauncherMesaPinTests(unittest.TestCase):
    """The launcher runs as psx, which cannot read the root-private boot set."""
    PIN = "m" * 64

    class Reached(Exception):
        pass

    def launch(self, passed_on, boot=None):
        scope = APP["main"].__globals__
        names = ("chromium", "chrome-sandbox", "chrome_crashpad_handler")
        provider = {"mesa_manifest_sha256": self.PIN,
                    "files": [{"path": "usr/lib/chromium/" + name, "sha256": "d"} for name in names]}
        config = dict({"schema_version": 1, "provider_manifest_sha256": "d"}, **passed_on)

        def read(path):
            if path == scope["CONFIG"]:
                return config
            if path == scope["BOOT_SET"]:
                if boot is None:
                    raise PermissionError(13, "Permission denied")
                return boot
            return provider

        cookie = mock.MagicMock()
        cookie.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000)
        # The first step after the Mesa check, so nothing is created or executed.
        profile = mock.MagicMock()
        profile.mkdir.side_effect = self.Reached
        replaced = {"root_json": read, "digest": lambda path: "d", "mounted_readonly_exec": lambda text: True,
                    "COOKIE": cookie, "PROFILE": profile}
        with mock.patch.dict(scope, replaced), mock.patch("sys.argv", ["launch"]), \
                mock.patch("os.geteuid", return_value=1000), \
                mock.patch("pwd.getpwuid", return_value=SimpleNamespace(pw_name="psx")), \
                mock.patch("os.uname", return_value=SimpleNamespace(release=scope["RELEASE"])), \
                mock.patch("os.access", return_value=True), mock.patch("pathlib.Path.read_text", return_value="1\n"):
            scope["main"]()

    def test_the_pin_root_passed_on_is_enough(self):
        with self.assertRaises(self.Reached):
            self.launch({"mesa_manifest_sha256": self.PIN})

    def test_a_different_passed_on_pin_is_refused(self):
        with self.assertRaisesRegex(ValueError, "same pinned Mesa"):
            self.launch({"mesa_manifest_sha256": "x" * 64})

    def test_without_a_passed_on_pin_the_boot_set_decides(self):
        with self.assertRaises(self.Reached):
            self.launch({}, boot={"mesa_manifest_sha256": self.PIN})
        with self.assertRaisesRegex(ValueError, "same pinned Mesa"):
            self.launch({}, boot={"mesa_manifest_sha256": "x" * 64})


class TrimTests(unittest.TestCase):
    BUILDER = runpy.run_path(str(ROOT / "scripts/prepare-ps4-chromium-provider"))

    def omitted(self, path):
        return self.BUILDER["omit"](path, PurePosixPath(path))

    def test_everything_the_kiosk_needs_stays(self):
        for path in ("usr/lib/chromium/chromium", "usr/lib/chromium/chrome-sandbox", "usr/lib/chromium/chrome_crashpad_handler",
                     "usr/lib/chromium/resources.pak", "usr/lib/chromium/chrome_100_percent.pak", "usr/lib/chromium/icudtl.dat",
                     "usr/lib/chromium/v8_context_snapshot.bin", "usr/lib/chromium/libEGL.so", "usr/lib/chromium/libGLESv2.so",
                     "usr/lib/chromium/locales/en-US.pak", "usr/lib/libopenh264.so.8", "usr/lib/libxslt.so.1",
                     "usr/share/licenses/chromium/LICENSE"):
            with self.subTest(path=path):
                self.assertFalse(self.omitted(path))

    def test_what_it_never_reaches_goes(self):
        for path in ("usr/bin/chromedriver", "usr/bin/chromium", "usr/lib/chromium/libvk_swiftshader.so",
                     "usr/lib/chromium/libqt6_shim.so", "usr/lib/chromium/chrome_200_percent.pak",
                     "usr/lib/chromium/locales/de.pak", "usr/share/man/man1/chromium.1.gz", "usr/share/fonts/x.ttf",
                     "usr/lib/pkgconfig/libxslt.pc", "usr/lib/python3.14/site-packages/x.py"):
            with self.subTest(path=path):
                self.assertTrue(self.omitted(path))

    def test_the_launcher_and_the_recipe_name_the_same_kernel(self):
        self.assertEqual(APP["RELEASE"], SPEC["kernel_release"])


if __name__ == "__main__": unittest.main()
