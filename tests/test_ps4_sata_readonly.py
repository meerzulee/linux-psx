"""Execute the early shell guard against fake sysfs; never access host disks."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "rootfs/ps4-diagnostics/sata-readonly"
ARGS = ("libata.force=1:1.5Gbps libahci.ps4_ahci_poll_ms=0 "
        "libahci.ps4_probe_ms=5 ps4_bpcie.ps4_sata_phy_orbit=1 systemd.gpt_auto=0")


class SataReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.sys = self.base / "sys"
        self.dev = self.base / "dev"
        self.dev.mkdir()
        self.pci = self.sys / "devices/pci0000:00/0000:00:14.7"
        self.pci.mkdir(parents=True)
        (self.pci / "vendor").write_text("0x104d\n")
        (self.pci / "device").write_text("0x90de\n")
        link = self.sys / "bus/pci/devices/0000:00:14.7"
        link.parent.mkdir(parents=True)
        link.symlink_to(self.pci)
        (self.sys / "class/block").mkdir(parents=True)
        self.log = self.base / "calls"

    def disk(self, name="sdb", ancestry="ata1/host0/target0:0:0/0:0:0:0"):
        target = self.pci / ancestry / "block" / name
        target.mkdir(parents=True)
        for key, value in (("removable", "0"), ("ro", "0"), ("size", "1953525168")):
            (target / key).write_text(value + "\n")
        (self.sys / "class/block" / name).symlink_to(target)
        (self.dev / name).touch()
        return target

    def partition(self, disk, name):
        target = disk / name
        target.mkdir()
        (target / "partition").write_text("1\n")
        (target / "ro").write_text("0\n")
        (self.dev / name).touch()
        (self.sys / "class/block" / name).symlink_to(target)
        return target

    def execute(self, operation, args="", fail=False, update=True):
        program = '''
            . "$1"
            psx_sata_block_node() { [ -f "$1" ]; }
            blockdev() {
                [ "$1" = --setro ] || exit 90
                case "$2" in "$FAKE_DEV"/*) ;; *) exit 91 ;; esac
                printf '%s\n' "$2" >> "$FAKE_LOG"
                [ "$FAKE_FAIL" = 0 ] || return 1
                if [ "$FAKE_UPDATE" = 1 ]; then
                    printf '1\n' > "$FAKE_SYS/class/block/${2##*/}/ro"
                fi
            }
            case "$2" in
                args) psx_sata_arguments "$3" ;;
                disks) psx_sata_readonly "$FAKE_SYS" "$FAKE_DEV" ;;
                *) exit 92 ;;
            esac
        '''
        env = dict(os.environ, FAKE_SYS=str(self.sys), FAKE_DEV=str(self.dev),
                   FAKE_LOG=str(self.log), FAKE_FAIL=str(int(fail)), FAKE_UPDATE=str(int(update)))
        return subprocess.run(["/bin/sh", "-c", program, "guard-test", str(HELPER), operation, args],
                              env=env, capture_output=True, text=True, timeout=5)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_exact_arguments_and_unrelated_display_arguments(self):
        self.assertEqual(self.execute("args", "console=tty0 " + ARGS).returncode, 0)

    def test_missing_duplicate_conflicting_or_legacy_probe_arguments_fail(self):
        variants = [ARGS.replace("libahci.ps4_probe_ms=5", ""), ARGS + " libahci.ps4_probe_ms=5",
                    ARGS.replace("ps4_ahci_poll_ms=0", "ps4_ahci_poll_ms=5"),
                    ARGS.replace("libata.force=1:1.5Gbps", "libata.force=noncq"),
                    ARGS.replace("systemd.gpt_auto=0", "systemd.gpt_auto=1"),
                    ARGS.replace("ps4_probe_ms=5", "ps4_ahci_completion_probe_ms=5")]
        for args in variants:
            with self.subTest(args=args):
                self.assertNotEqual(self.execute("args", args).returncode, 0)
        self.assertFalse(self.calls())

    def test_disk_and_partitions_are_protected_independently_of_sd_order(self):
        disk = self.disk("sdc")
        part = self.partition(disk, "sdc1")
        result = self.execute("disks")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [str(self.dev / "sdc"), str(self.dev / "sdc1")])
        self.assertEqual((disk / "ro").read_text(), "1\n")
        self.assertEqual((part / "ro").read_text(), "1\n")

    def test_usb_on_the_shared_function_is_never_touched(self):
        self.disk("sda", "usb1/1-1/host9/target9:0:0/9:0:0:0")
        self.disk("sdb")
        self.assertEqual(self.execute("disks").returncode, 0)
        self.assertEqual(self.calls(), [str(self.dev / "sdb")])

    def test_no_disk_waits_without_any_block_ioctl(self):
        self.assertEqual(self.execute("disks").returncode, 1)
        self.assertFalse(self.calls())

    def test_wrong_controller_refuses_before_any_block_ioctl(self):
        self.disk()
        (self.pci / "device").write_text("0x909f\n")
        self.assertEqual(self.execute("disks").returncode, 2)
        self.assertFalse(self.calls())

    def test_missing_node_waits_without_using_another_device(self):
        self.disk()
        (self.dev / "sdb").unlink()
        self.assertEqual(self.execute("disks").returncode, 1)
        self.assertFalse(self.calls())

    def test_blockdev_failure_or_unverified_readonly_is_terminal(self):
        self.disk()
        self.assertEqual(self.execute("disks", fail=True).returncode, 2)
        self.assertEqual(self.execute("disks", update=False).returncode, 2)

    def test_ambiguous_second_internal_disk_is_terminal(self):
        self.disk("sdb")
        self.disk("sdc", "ata2/host1/target1:0:0/1:0:0:0")
        self.assertEqual(self.execute("disks").returncode, 2)


if __name__ == "__main__":
    unittest.main()
