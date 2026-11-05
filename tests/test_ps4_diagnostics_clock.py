"""Console clock seed prevents stale build dates from invalidating HTTPS tests."""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("psx_clock_stage", str(ROOT / "scripts/stage-ps4-diagnostics-usb"))
spec = importlib.util.spec_from_loader("psx_clock_stage", loader)
APP = importlib.util.module_from_spec(spec)
loader.exec_module(APP)


class ClockTests(unittest.TestCase):
    def test_console_gets_current_seed_and_nonblocking_ntp_want(self):
        result = APP.render_bootargs("psx.diag_uuid=@USB_UUID@ time=1\n", "1234-ABCD", {"ui_backend": "ratatui"}, 1793832000).decode()
        self.assertEqual(sum(t.startswith("time=") for t in result.split()), 1)
        self.assertIn("time=1793832000", result)
        self.assertIn("systemd.wants=systemd-timesyncd.service", result)
        self.assertIn("psx.diag_uuid=1234-ABCD", result)

    def test_legacy_profile_keeps_exact_arguments(self):
        self.assertEqual(APP.render_bootargs("  psx.diag_uuid=@USB_UUID@ time=1\n", "1234-ABCD", {}),
                         b"  psx.diag_uuid=1234-ABCD time=1\n")

    def test_scoped_alpha5_keeps_clock_seed_without_loader_ntp_want(self):
        metadata = {"ui_backend": "ratatui", "profile": "ps4-baikal-diagnostics-uvd-alpha5",
                    "boot_clock_start_timesyncd": False}
        source = "psx.diag_uuid=@USB_UUID@ systemd.wants=systemd-timesyncd.service time=1\n"
        result = APP.render_bootargs(source, "1234-ABCD", metadata, 1793832000).decode()
        self.assertIn("time=1793832000", result)
        self.assertNotIn("systemd.wants=", result)

    def test_other_profiles_or_nonboolean_clock_policy_cannot_drop_ntp_want(self):
        for profile, value in (("ps4-baikal-diagnostics-uvd-alpha4", False),
                               ("ps4-baikal-diagnostics-uvd-alpha5", 0)):
            with self.subTest(profile=profile, value=value), self.assertRaises(ValueError):
                APP.render_bootargs("psx.diag_uuid=@USB_UUID@\n", "1234-ABCD",
                                    {"ui_backend": "ratatui", "profile": profile,
                                     "boot_clock_start_timesyncd": value}, 1793832000)

    def test_invalid_clock_seeds_are_rejected(self):
        for value in (1, -1, True, "1793832000", 4102444800):
            with self.subTest(value=value), self.assertRaises(ValueError):
                APP.render_bootargs("psx.diag_uuid=@USB_UUID@", "1234-ABCD", {"ui_backend": "ratatui"}, value)


if __name__ == "__main__":
    unittest.main()
