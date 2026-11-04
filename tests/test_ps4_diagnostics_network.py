"""Synthetic NM and SSH tests. No host hardware, network or systemd queries."""
import base64
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


NET = load("psx_network_test", ROOT / "diagnostics/ps4/network.py")
KEY = load("psx_hostkey_test", ROOT / "rootfs/ps4-diagnostics-gui/prepare-ssh-host-key")
PUBLIC = "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"x" * 32).decode()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls = []
        self.write("/proc/sys/kernel/osrelease", NET.RELEASE)
        for name in ("nmcli", "ip", "systemctl", "ssh-keygen"):
            self.write("/usr/bin/" + name, "synthetic tool, never executed").chmod(0o755)

    def path(self, logical):
        return self.root / logical.lstrip("/")

    def write(self, logical, value):
        path = self.path(logical)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode())
        return path


class NetworkTests(Fixture):
    def setUp(self):
        super().setUp()
        self.wired = "connected"
        self.wifi = "disconnected"
        self.wifi_devices = ["wlan0"]
        self.radio = True
        self.aps = "Lab\\: Wi\\\\Fi:82:WPA2:*\nOpen:60:--:\nLab\\: Wi\\\\Fi:20:WPA2:\n"
        self.wired_ip = ["192.0.2.4"]
        self.secret_file = None
        self.inject = None
        self.set_mts(bound=1, attempted=1, published=1, engine_live=1, admin_up=1, terminal_fault=0, error=0)
        self.write(NET.MTS + "/autoarm", "Y")
        self.network = NET.Network(self.root, self.execute)

    def set_mts(self, **values):
        self.write(NET.MTS + "/report", json.dumps(values))

    def execute(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        if self.inject:
            value = self.inject(argv)
            if value is not None:
                return value
        tool, args = Path(argv[0]).name, argv[1:]
        if tool == "ip":
            result = [{"ifname": "psx0", "address": "02:00:00:00:00:01", "addr_info":
                       [{"family": "inet", "local": address} for address in self.wired_ip]},
                      {"ifname": "wlan0", "address": "02:00:00:00:00:02", "addr_info": []}]
            return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")
        if tool == "systemctl":
            return subprocess.CompletedProcess(argv, 0, "", "")
        self.assertEqual(tool, "nmcli")
        args = args[2:]  # fixed --colors no
        if args[-2:] == ["device", "status"]:
            output = f"psx0:ethernet:{self.wired}\n" + "".join(
                f"{name}:wifi:{self.wifi}\n" for name in self.wifi_devices)
        elif args == ["radio", "wifi"]:
            output = "enabled\n" if self.radio else "disabled\n"
        elif args == ["radio", "wifi", "on"]:
            self.radio = True; output = ""
        elif "list" in args and "wifi" in args:
            self.assertIn(args[args.index("--rescan") + 1], ("yes", "no"))
            output = self.aps
        elif args[:2] == ["connection", "load"]:
            self.secret_file = Path(args[2])
            self.assertEqual(stat.S_IMODE(self.secret_file.stat().st_mode), 0o600)
            output = ""
        elif "up" in args and "connection" in args:
            if NET.WIRED_UUID in args:
                self.wired = "connected"; self.wired_ip = ["192.0.2.4"]
                self.set_mts(bound=1, attempted=1, published=1, engine_live=1,
                             admin_up=1, terminal_fault=0, error=0)
            output = ""
        elif args[:4] == ["--wait", "15", "device", "disconnect"]:
            if args[4] == "psx0":
                self.wired = "disconnected"; self.wired_ip = []
                self.set_mts(bound=1, attempted=1, published=1, engine_live=1,
                             admin_up=0, terminal_fault=0, error=0)
            elif args[4] == "wlan0": self.wifi = "disconnected"
            else: raise AssertionError("Unexpected disconnect interface")
            output = ""
        elif args[:2] in (["connection", "modify"], ["connection", "delete"]) or "rescan" in args:
            output = ""
        else:
            raise AssertionError("Unexpected synthetic command: " + repr(argv))
        return subprocess.CompletedProcess(argv, 0, output, "")

    def test_status_reports_addresses_and_escaped_access_points_without_scan(self):
        result = self.network.status()
        self.assertEqual(result["interfaces"][0]["ipv4"], ["192.0.2.4"])
        self.assertEqual(result["wifi"]["access_points"][0],
                         {"ssid": "Lab: Wi\\Fi", "signal": 82, "security": "WPA2", "active": True})
        self.assertEqual(len(result["wifi"]["access_points"]), 2)
        self.assertTrue(all("rescan" not in argv for argv, _ in self.calls))
        self.assertTrue(all(argv[argv.index("--rescan") + 1] == "no" for argv, _ in self.calls if "--rescan" in argv))

    def test_scan_is_explicit_and_bound_to_wifi_interface(self):
        self.network.wifi_scan()
        commands = [argv for argv, _ in self.calls]
        scan = next(argv for argv in commands if "--rescan" in argv)
        self.assertEqual(scan[-4:], ["--rescan", "yes", "ifname", "wlan0"])
        self.assertFalse(any("psx0" in argv for argv in commands))

    def test_password_stays_in_private_ram_profile_and_out_of_arguments(self):
        secret = "a: private\\password"
        result = self.network.wifi_connect("Lab: Wi\\Fi", secret)
        self.assertTrue(result["ok"])
        self.assertFalse(result["credentials_persisted"])
        self.assertTrue(self.secret_file.is_relative_to(self.path("/run/NetworkManager/system-connections")))
        self.assertIn("psk=a:\\sprivate\\\\password", self.secret_file.read_text())
        self.assertNotIn(secret, repr(self.calls))
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn("password", [argument for argv, _ in self.calls for argument in argv])

    def test_ps4_scan_and_connection_use_station_instead_of_ap_interfaces(self):
        self.wifi_devices = ["ap0", "ap1", "wlan0"]
        self.network.wifi_scan()
        self.network.wifi_connect("Lab: Wi\\Fi", "private-password")
        commands = [argv for argv, _ in self.calls]
        scans = [argv for argv in commands if "--rescan" in argv]
        self.assertTrue(scans)
        self.assertTrue(all(argv[-2:] == ["ifname", "wlan0"] for argv in scans))
        self.assertIn("interface-name=wlan0\n", self.secret_file.read_text())
        activation = next(argv for argv in commands if "up" in argv)
        self.assertEqual(activation[-2:], ["ifname", "wlan0"])
        self.assertFalse(any("ap0" in argv or "ap1" in argv or "psx0" in argv for argv in commands))

    def test_ap_and_p2p_interfaces_alone_are_not_station_fallbacks(self):
        self.wifi_devices = ["ap0", "ap1", "p2p0", "p2p-dev-wlan0"]
        with self.assertRaises(NET.NetworkError) as caught:
            self.network.wifi_scan()
        self.assertEqual(caught.exception.code, "WIFI_UNAVAILABLE")
        self.assertFalse(any("on" in argv or "--rescan" in argv for argv, _ in self.calls))

    def test_other_station_names_are_supported_when_wlan0_is_absent(self):
        self.wifi_devices = ["ap0", "wlp2s0"]
        self.assertEqual(self.network._wifi_interface(), "wlp2s0")

    def test_open_network_profile_has_no_security_or_password(self):
        self.network.wifi_connect("Open", "")
        content = self.secret_file.read_text()
        self.assertNotIn("psk=", content)
        self.assertNotIn("[wifi-security]", content)

    def test_password_newlines_and_oversized_ssids_are_rejected_before_commands(self):
        for ssid, password in (("Open", "bad\npassword"), ("x" * 33, "password"), ("bad\nSSID", "password")):
            with self.subTest(ssid=ssid), self.assertRaises(NET.NetworkError):
                self.network.wifi_connect(ssid, password)
        self.assertEqual(self.calls, [])

    def test_enterprise_network_requires_a_separate_profile(self):
        self.aps = "Corp:90:WPA2 802.1X:\n"
        with self.assertRaises(NET.NetworkError) as caught:
            self.network.wifi_connect("Corp", "password")
        self.assertEqual(caught.exception.code, "UNSUPPORTED_AUTH")
        self.assertIsNone(self.secret_file)

    def test_failed_connection_removes_private_file_and_never_returns_secret_error(self):
        secret = "never-show-this-password"
        def fail(argv):
            if "up" in argv:
                raise subprocess.CalledProcessError(1, argv, output=secret, stderr=secret)
        self.inject = fail
        with self.assertRaises(NET.NetworkError) as caught:
            self.network.wifi_connect("Lab: Wi\\Fi", secret)
        self.assertNotIn(secret, str(caught.exception))
        self.assertFalse(self.secret_file.exists())
        self.assertTrue(any("delete" in argv for argv, _ in self.calls))

    def test_live_wired_connection_is_reused_without_an_activation_command(self):
        result = self.network.ethernet_connect()
        self.assertTrue(result["ok"])
        self.assertTrue(result["reused"])
        self.assertFalse(any("up" in argv or "down" in argv or "modprobe" in argv for argv, _ in self.calls))

    def test_fresh_wired_activation_uses_only_the_fixed_nm_profile(self):
        self.wired = "disconnected"; self.wired_ip = []
        self.set_mts(bound=1, attempted=0, published=0, engine_live=0, admin_up=0, terminal_fault=0, error=0)
        result = self.network.ethernet_connect()
        self.assertTrue(result["ok"])
        activation = next(argv for argv, _ in self.calls if "up" in argv)
        self.assertEqual(activation[-6:], ["connection", "up", "uuid", NET.WIRED_UUID, "ifname", "psx0"])
        self.assertFalse(self.path(NET.MTS + "/allow_open").exists())

    def test_ethernet_disconnect_uses_only_nm_device_disconnect_and_retains_driver(self):
        result = self.network.ethernet_disconnect()
        self.assertTrue(result["ok"]); self.assertEqual(result["state"], "disconnected")
        self.assertIn("SSH", result["message"])
        mutations = [argv[1:] for argv, _ in self.calls if "disconnect" in argv]
        self.assertEqual(mutations, [["--colors", "no", "--wait", "15", "device", "disconnect", "psx0"]])
        report = json.loads(self.path(NET.MTS + "/report").read_text())
        self.assertEqual((report["published"], report["engine_live"], report["admin_up"]), (1, 1, 0))
        self.assertFalse(any(set(argv) & {"modprobe", "down", "restart", "delete", "up"} for argv, _ in self.calls))

    def test_wifi_disconnect_selects_station_and_leaves_radio_and_ethernet_untouched(self):
        self.wifi = "connected"; self.wifi_devices = ["ap0", "ap1", "wlan0"]
        result = self.network.wifi_disconnect()
        self.assertTrue(result["ok"]); self.assertEqual(result["interface"], "wlan0")
        self.assertEqual(self.wired, "connected"); self.assertTrue(self.radio)
        self.assertEqual([argv[-3:] for argv, _ in self.calls if "disconnect" in argv],
                         [["device", "disconnect", "wlan0"]])
        self.assertFalse(any("radio" in argv or "connection" in argv for argv, _ in self.calls))

    def test_disconnected_wifi_does_not_issue_a_mutating_command(self):
        result = self.network.wifi_disconnect()
        self.assertTrue(result["already_disconnected"])
        self.assertFalse(any("disconnect" in argv for argv, _ in self.calls))

    def test_terminal_ethernet_and_ap_only_wifi_refuse_disconnect(self):
        self.set_mts(bound=1, attempted=1, published=1, engine_live=1,
                     admin_up=1, terminal_fault=1, error=0)
        with self.assertRaises(NET.NetworkError): self.network.ethernet_disconnect()
        self.assertEqual(self.calls, [])
        self.wifi_devices = ["ap0", "ap1", "p2p-dev-wlan0"]
        with self.assertRaises(NET.NetworkError): self.network.wifi_disconnect()
        self.assertFalse(any("disconnect" in argv for argv, _ in self.calls))

    def test_terminal_uncertain_and_unready_states_never_issue_nm_activation(self):
        for changes in ({"terminal_fault": 1}, {"error": -5}, {"published": 0}, {"engine_live": 0}):
            values = dict(bound=1, attempted=1, published=1, engine_live=1, admin_up=1, terminal_fault=0, error=0)
            values.update(changes); self.set_mts(**values)
            with self.subTest(changes=changes), self.assertRaises(NET.NetworkError):
                self.network.ethernet_connect()
        self.assertEqual(self.calls, [])

    def test_partial_mts_report_cannot_be_treated_as_a_fresh_publication(self):
        self.set_mts(bound=1)
        with self.assertRaises(NET.NetworkError) as caught:
            self.network.ethernet_connect()
        self.assertEqual(caught.exception.code, "INVALID_REPLY")
        self.assertEqual(self.calls, [])

    def test_wpa3_personal_uses_sae_without_exposing_the_password(self):
        self.aps = "Modern:90:WPA3:\n"
        self.network.wifi_connect("Modern", "sae-pass")
        self.assertIn("key-mgmt=sae", self.secret_file.read_text())
        self.assertNotIn("sae-pass", repr(self.calls))

    def test_concurrent_connection_attempt_is_refused(self):
        other = NET.Network(self.root, self.execute)
        with self.network._operation(), self.assertRaises(NET.NetworkError) as caught:
            other.wifi_scan()
        self.assertEqual(caught.exception.code, "BUSY")
        self.assertEqual(self.calls, [])

    def test_other_kernel_and_gated_profile_refuse_activation(self):
        self.write("/proc/sys/kernel/osrelease", "synthetic-other-kernel")
        with self.assertRaises(NET.NetworkError): self.network.ethernet_connect()
        self.write("/proc/sys/kernel/osrelease", NET.RELEASE)
        self.write(NET.MTS + "/autoarm", "N")
        with self.assertRaises(NET.NetworkError): self.network.ethernet_connect()
        self.assertEqual(self.calls, [])

    def test_fixture_without_executor_never_executes_host_tool(self):
        with self.assertRaises(NET.NetworkError) as caught:
            NET.Network(self.root).status()
        self.assertEqual(caught.exception.code, "FIXTURE_ONLY")

    def test_status_fingerprint_matches_public_key_only(self):
        self.write("/run/psx-ssh/ssh_host_ed25519_key.pub", PUBLIC)
        result = self.network.ssh_status()
        self.assertTrue(result["enabled"])
        self.assertEqual(result["host_key_fingerprint"], KEY.fingerprint(PUBLIC))
        self.assertEqual((result["port"], result["login"]), (22, "root"))


class SSHTests(Fixture):
    def setUp(self):
        super().setUp()
        self.write("/etc/psx-diagnostics/authorized_keys", PUBLIC + " owner\n")
        self.write("/proc/self/mountinfo", "22 1 8:17 / /run/psx-media rw - vfat /dev/synthetic rw\n")
        self.path("/run/psx-media").mkdir(parents=True, mode=0o700)

    def execute(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        if "-q" in argv:
            Path(argv[argv.index("-f") + 1]).write_bytes(b"synthetic private key; never an actual credential")
        return subprocess.CompletedProcess(argv, 0, PUBLIC + "\n" if "-y" in argv else "", "")

    def test_first_boot_generates_then_next_boot_reuses_the_identity(self):
        first = KEY.prepare(self.root, self.execute)
        second = KEY.prepare(self.root, self.execute)
        self.assertEqual(first, second)
        self.assertEqual(sum("-q" in argv for argv, _ in self.calls), 1)
        persistent = self.path(KEY.STATE + "/ssh_host_ed25519_key")
        runtime = self.path(KEY.RUNTIME + "/ssh_host_ed25519_key")
        self.assertEqual(persistent.read_bytes(), runtime.read_bytes())
        self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o600)
        self.assertTrue(self.path(KEY.STATE + "/identity.json").exists())

    def test_preprovisioned_unique_key_is_used_without_generating_another(self):
        state = self.path(KEY.STATE); state.mkdir(parents=True, mode=0o700)
        key = state / "ssh_host_ed25519_key"; key.write_bytes(b"synthetic preprovided key"); key.chmod(0o600)
        (state / "ssh_host_ed25519_key.pub").write_text(PUBLIC)
        KEY.prepare(self.root, self.execute)
        self.assertFalse(any("-q" in argv for argv, _ in self.calls))
        self.assertEqual(self.path(KEY.RUNTIME + "/ssh_host_ed25519_key").read_bytes(), b"synthetic preprovided key")

    def test_world_readable_fat_mount_is_rejected_before_key_creation(self):
        self.path("/run/psx-media").chmod(0o755)
        with self.assertRaisesRegex(ValueError, "root-only"):
            KEY.prepare(self.root, self.execute)
        self.assertEqual(self.calls, [])

    def test_no_authorized_owner_key_means_no_host_key_generation(self):
        self.path("/etc/psx-diagnostics/authorized_keys").unlink()
        with self.assertRaises(OSError): KEY.prepare(self.root, self.execute)
        self.assertEqual(self.calls, [])

    def test_protected_empty_seed_still_prepares_a_unique_persistent_host_identity(self):
        self.path("/etc/psx-diagnostics/authorized_keys").write_bytes(b"")
        first = KEY.prepare(self.root, self.execute)
        second = KEY.prepare(self.root, self.execute)
        self.assertEqual(first, second)
        self.assertEqual(sum("-q" in argv for argv, _ in self.calls), 1)
        self.assertEqual(self.path("/etc/psx-diagnostics/authorized_keys").read_bytes(), b"")
        self.assertFalse(any("systemctl" in argv[0] or "passwd" in argv[0] for argv, _ in self.calls))

    def test_empty_seed_must_remain_bounded_regular_and_protected(self):
        path = self.path("/etc/psx-diagnostics/authorized_keys")
        for data, mode in ((b"", 0o666), (b"x"*65537, 0o644), (b"PRIVATE KEY", 0o644)):
            path.write_bytes(data); path.chmod(mode)
            with self.subTest(mode=mode, size=len(data)), self.assertRaises(ValueError):
                KEY.prepare(self.root, self.execute)
            self.assertEqual(self.calls, [])
        path.write_bytes(b""); path.chmod(0o644)
        with patch.object(KEY.os, "geteuid", return_value=os.geteuid()+1), self.assertRaisesRegex(ValueError, "root-owned"):
            KEY.prepare(self.root, self.execute)
        self.assertEqual(self.calls, [])
        path.unlink(); path.symlink_to(self.path("/elsewhere"))
        self.write("/elsewhere", "")
        with self.assertRaises(ValueError):
            KEY.prepare(self.root, self.execute)
        self.assertEqual(self.calls, [])

    def test_missing_ethernet_cannot_prevent_ssh_over_wifi(self):
        self.assertFalse(self.path("/sys/class/net").exists())
        result = KEY.prepare(self.root, self.execute)
        self.assertEqual(result["identity_scope"], "diagnostic-usb-installation")
        self.assertEqual(result["fingerprint"], KEY.fingerprint(PUBLIC))

    def test_missing_previously_bound_key_never_rotates_silently(self):
        KEY.prepare(self.root, self.execute)
        self.path(KEY.STATE + "/ssh_host_ed25519_key").unlink()
        with self.assertRaisesRegex(ValueError, "silent rotation"):
            KEY.prepare(self.root, self.execute)
        self.assertEqual(sum("-q" in argv for argv, _ in self.calls), 1)

    def test_configuration_requires_key_auth_and_does_not_enable_old_adapter(self):
        sources = ROOT / "rootfs/ps4-diagnostics-gui"
        sshd = (sources / "sshd-psx.conf").read_text()
        for directive in ("AuthenticationMethods publickey", "PasswordAuthentication no", "UsePAM yes",
                          "PermitRootLogin prohibit-password", "AuthorizedKeysFile /etc/psx-diagnostics/authorized_keys"):
            self.assertIn(directive, sshd)
        self.assertIn("AuthorizedKeysFile /etc/psx-diagnostics/authorized_keys /root/.ssh/authorized_keys", sshd)
        unit = (sources / "psx-ssh-host-key.service").read_text()
        self.assertIn("Before=sshd.service psx-diagnostics-control.service", unit)
        wired = (sources / "psx-wired.nmconnection").read_text()
        self.assertIn(NET.WIRED_UUID, wired)
        self.assertIn("autoconnect-retries=1", wired)
        self.assertNotIn("allow_open", (sources / "prepare-ssh-host-key").read_text())


class ProcessTests(unittest.TestCase):
    def test_timeout_is_finite_and_does_not_expose_tool_output(self):
        with self.assertRaises(NET.NetworkError) as caught:
            NET.execute([sys.executable, "-c", "import time; print('synthetic secret',flush=True); time.sleep(30)"], 0.1)
        self.assertEqual(caught.exception.code, "TIMEOUT")
        self.assertNotIn("synthetic secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
