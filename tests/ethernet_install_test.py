#!/usr/bin/env python3
"""Synthetic installation only: temporary files, mocked ownership, no console/NM."""
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
import ethernet_activation_test as fixture

APP = fixture.APP
REAL_FETCH_METADATA = APP.fetch_metadata


class InstallerSystem(fixture.FakeSystem):
    def __init__(self, directory):
        super().__init__()
        self.directory = directory

    def sealed(self, path, mode=None):
        if str(path).startswith(str(self.directory) + "/"):
            return APP.System.sealed(self, path, mode)
        return super().sealed(path, mode)

    def read(self, path):
        if str(path).startswith(str(self.directory) + "/"):
            return Path(path).read_bytes()
        return super().read(path)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(self.sandbox.cleanup)
        self.directory = Path(self.sandbox.name).resolve()
        self.script = self.directory / "usr/local/sbin/ps4-ethernet"
        self.metadata_directory = self.directory / "etc/psx"
        self.owner_overrides = {}
        real_lstat = Path.lstat

        def synthetic_root_stat(path):
            info = real_lstat(path)
            owner = self.owner_overrides.get(str(path), (0, 0))
            mode = info.st_mode
            # The temporary test tree models /usr/local and /etc on a sealed
            # root. Real host ancestors such as Linux /tmp are outside that
            # model, so never make their unrelated permissions test inputs.
            if stat.S_ISDIR(mode) and path != self.directory and self.directory not in path.parents:
                mode = stat.S_IFDIR | 0o755
            return SimpleNamespace(st_uid=owner[0], st_gid=owner[1], st_mode=mode,
                                   st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size)

        mock.patch.object(APP.os, "geteuid", return_value=0).start()
        mock.patch.object(APP.os, "uname", return_value=SimpleNamespace(release=APP.PSX_RELEASE, machine="x86_64")).start()
        mock.patch.object(APP.os, "fchown").start()  # Temporary files remain owned by the test runner.
        mock.patch.object(APP.Path, "lstat", synthetic_root_stat).start()
        mock.patch.object(APP, "INSTALL_SCRIPT", self.script).start()
        mock.patch.object(APP, "INSTALL_METADATA", self.metadata_directory).start()
        self.unit = self.directory / "etc/systemd/system/ps4-ethernet.service"
        mock.patch.object(APP, "INSTALL_UNIT", self.unit).start()
        self.addCleanup(mock.patch.stopall)
        self.no_network = mock.patch.object(APP, "fetch_metadata", side_effect=AssertionError("Unexpected network" )).start()
        APP.select_release()
        self.host = InstallerSystem(self.directory)
        self.manifest = self.directory / "downloaded-artifacts.json"
        self.manifest.write_text(json.dumps(self.host.candidate))

    def assert_no_activation(self):
        self.assertFalse(self.host.loaded)
        self.assertFalse(self.host.claimed)
        self.assertFalse(any(event[0] in ("write", "create", "claim") for event in self.host.events))
        allowed = (("command", "/usr/bin/modinfo"), ("command", "/usr/bin/modprobe", "--show-depends"))
        self.assertTrue(all(any(event[:len(prefix)] == prefix for prefix in allowed)
                            for event in self.host.events))

    def test_install_seals_matching_files_without_activation_and_identical_reinstall_is_unchanged(self):
        result = APP.install(self.host, self.manifest)
        self.assertIn("no activation/startup changes", result)
        self.assertEqual(self.script.read_bytes(), fixture.HELPER.read_bytes())
        self.assertEqual(self.script.stat().st_mode & 0o777, 0o755)
        installed = APP.installed_manifest()
        self.assertEqual(installed.read_bytes(), self.manifest.read_bytes())
        self.assertEqual(installed.stat().st_mode & 0o777, 0o600)
        identities = (self.script.stat().st_ino, installed.stat().st_ino)
        APP.install(self.host, self.manifest)
        self.assertEqual(identities, (self.script.stat().st_ino, installed.stat().st_ino))
        self.assert_no_activation()

    def test_unknown_kernel_fails_before_reading_input_or_writing_destinations(self):
        with mock.patch.object(APP.os, "uname", return_value=SimpleNamespace(release="6.18.44-unknown", machine="x86_64")), \
             mock.patch.object(APP, "read_install_source") as read:
            with self.assertRaisesRegex(RuntimeError, "Unsupported kernel"):
                APP.install(self.host, self.manifest)
        read.assert_not_called()
        self.assertFalse(self.script.parent.exists())
        self.assertFalse(self.metadata_directory.exists())
        self.assert_no_activation()

    def test_wrong_config_or_module_fails_before_creating_directories(self):
        for field in ("kernel_config_sha256", "module"):
            candidate = dict(self.host.candidate)
            if field == "module":
                candidate["module"] = dict(candidate["module"], sha256="0" * 64)
            else:
                candidate[field] = "0" * 64
            self.manifest.write_text(json.dumps(candidate))
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                APP.install(self.host, self.manifest)
            self.assertFalse(self.script.parent.exists())
            self.assertFalse(self.metadata_directory.exists())
            self.assert_no_activation()

    def test_different_existing_script_or_metadata_is_preserved_without_partial_install(self):
        for target in (self.script, APP.installed_manifest()):
            with self.subTest(target=target):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"keep other work\n")
                target.chmod(0o755 if target == self.script else 0o600)
                with self.assertRaisesRegex(RuntimeError, "replace different"):
                    APP.install(self.host, self.manifest)
                self.assertEqual(target.read_bytes(), b"keep other work\n")
                other = APP.installed_manifest() if target == self.script else self.script
                self.assertFalse(other.exists())
                target.unlink()
        self.assert_no_activation()

    def test_symlink_destination_or_ancestor_is_never_followed(self):
        victim = self.directory / "victim"
        victim.mkdir()
        self.script.parent.mkdir(parents=True)
        self.script.symlink_to(victim / "outside-script")
        with self.assertRaises(RuntimeError):
            APP.install(self.host, self.manifest)
        self.assertFalse((victim / "outside-script").exists())
        self.script.unlink()
        self.metadata_directory.parent.mkdir(parents=True)
        self.metadata_directory.symlink_to(victim)
        with self.assertRaisesRegex(RuntimeError, "symlink installation"):
            APP.install(self.host, self.manifest)
        self.assertEqual(list(victim.iterdir()), [])
        self.assertFalse(self.script.exists())
        self.assert_no_activation()

    def test_unowned_or_writable_parent_and_unowned_existing_file_are_refused(self):
        self.script.parent.mkdir(parents=True)
        for failure in ("owner", "writable", "existing-owner"):
            with self.subTest(failure=failure):
                if failure == "owner":
                    self.owner_overrides[str(self.script.parent)] = (1000, 1000)
                elif failure == "writable":
                    self.script.parent.chmod(0o777)
                else:
                    self.script.write_bytes(fixture.HELPER.read_bytes())
                    self.script.chmod(0o755)
                    self.owner_overrides[str(self.script)] = (1000, 1000)
                with self.assertRaises(RuntimeError):
                    APP.install(self.host, self.manifest)
                self.assertFalse(self.metadata_directory.exists())
                self.owner_overrides.clear()
                self.script.parent.chmod(0o755)
                if self.script.exists():
                    self.script.unlink()
        self.assert_no_activation()

    def test_symlink_metadata_source_is_refused_before_any_destination_write(self):
        link = self.directory / "metadata-link.json"
        link.symlink_to(self.manifest)
        with self.assertRaisesRegex(RuntimeError, "regular installation input"):
            APP.install(self.host, link)
        self.assertFalse(self.script.parent.exists())
        self.assertFalse(self.metadata_directory.exists())
        self.assert_no_activation()

    def setup_host(self, active="active"):
        self.host.resolve_tool = mock.Mock(side_effect=lambda name: "/usr/bin/" + name)
        original = self.host.command
        def command(*args, **kwargs):
            if args[0] == "/usr/bin/systemctl":
                self.host.events.append(("command", *args))
                return active if args[1] == "is-active" else ""
            return original(*args, **kwargs)
        self.host.command = command

    def test_setup_installs_unit_then_reloads_enables_and_starts_without_direct_driver_actions(self):
        self.setup_host()
        self.assertIn("enabled and started", APP.setup(self.host, self.manifest))
        unit = self.unit.read_text()
        self.assertEqual(self.unit.stat().st_mode & 0o777, 0o644)
        self.assertIn("After=local-fs.target NetworkManager.service\n", unit)
        self.assertIn("Wants=NetworkManager.service\n", unit)
        self.assertIn("ConditionKernelVersion=" + APP.RELEASE + "\n", unit)
        self.assertIn("ConditionPathExists=" + str(APP.installed_manifest()) + "\n", unit)
        self.assertIn("ExecStart=" + str(self.script) + " --activate\n", unit)
        for setting in ("Type=oneshot", "RemainAfterExit=yes", "TimeoutStartSec=75", "UMask=0077", "WantedBy=multi-user.target"):
            self.assertIn(setting + "\n", unit)
        self.assertNotIn("Restart=", unit)
        self.assertNotIn("ExecStop=", unit)
        self.assertFalse(self.host.loaded or self.host.claimed)
        self.assertFalse(any(event[0] in ("write", "create", "claim") for event in self.host.events))
        calls = [event[2:] for event in self.host.events if event[:2] == ("command", "/usr/bin/systemctl")]
        self.assertEqual(calls, [("is-active", "NetworkManager.service"), ("daemon-reload",),
                                 ("enable", "--now", "ps4-ethernet.service")])
        self.host.resolve_tool.assert_any_call("systemctl")
        self.host.resolve_tool.assert_any_call("nmcli")
        before = self.unit.stat().st_ino
        APP.setup(self.host, self.manifest)
        self.assertEqual(before, self.unit.stat().st_ino)

    def test_bare_install_does_not_write_unit_or_call_systemctl(self):
        APP.install(self.host, self.manifest)
        self.assertFalse(self.unit.exists())
        self.assert_no_activation()

    def test_conflicting_unit_fails_before_install_and_does_not_enable_anything(self):
        self.setup_host()
        self.unit.parent.mkdir(parents=True)
        self.unit.write_text("keep another service\n")
        self.unit.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, "replace different"):
            APP.setup(self.host, self.manifest)
        self.assertEqual(self.unit.read_text(), "keep another service\n")
        self.assertFalse(self.script.exists() or self.metadata_directory.exists())
        self.assertFalse(any("daemon-reload" in event or "enable" in event for event in self.host.events))

    def test_setup_requires_active_networkmanager_and_trusted_systemctl_before_any_write(self):
        for state in ("inactive", "untrusted"):
            with self.subTest(state=state):
                self.setup_host(active="inactive")
                if state == "untrusted":
                    self.host.resolve_tool.side_effect = RuntimeError("Untrusted system tool")
                with self.assertRaises(RuntimeError):
                    APP.setup(self.host, self.manifest)
                self.assertFalse(self.script.parent.exists() or self.unit.parent.exists() or
                                 self.metadata_directory.exists())

    def test_unknown_setup_kernel_fails_before_systemctl_or_installer(self):
        self.setup_host()
        with mock.patch.object(APP.os, "uname", return_value=SimpleNamespace(release="other", machine="x86_64")):
            with self.assertRaisesRegex(RuntimeError, "Unsupported kernel"):
                APP.setup(self.host, self.manifest)
        self.host.resolve_tool.assert_not_called()
        self.assertFalse(self.host.events)

    def test_help_requires_no_root_kernel_metadata_or_hardware(self):
        with mock.patch.object(APP.sys, "argv", ["ps4-ethernet", "--help"]), \
             mock.patch.object(APP.os, "uname", side_effect=AssertionError("uname on help")), \
             mock.patch.object(APP.os, "geteuid", side_effect=AssertionError("root on help")), \
             mock.patch.object(APP.sys, "stdout"):
            with self.assertRaises(SystemExit) as stopped:
                APP.main()
        self.assertEqual(stopped.exception.code, 0)

    def test_main_install_does_not_enter_activation_or_create_lock(self):
        with mock.patch.object(APP.sys, "argv", ["ps4-ethernet", "--install", "--manifest", str(self.manifest)]), \
             mock.patch.object(APP, "install", return_value="installed") as install, \
             mock.patch.object(APP, "activate", side_effect=AssertionError("unexpected activation")), \
             mock.patch.object(APP.os, "open", side_effect=AssertionError("unexpected lock")), \
             mock.patch("builtins.print"):
            APP.main()
        self.assertEqual(install.call_args.args[1], self.manifest)

    def test_main_setup_normalizes_relative_manifest_and_calls_explicit_setup(self):
        with mock.patch.object(APP.sys, "argv", ["ps4-ethernet", "--setup", "--manifest", "artifacts.json"]), \
             mock.patch.object(APP, "setup", return_value="setup") as setup, \
             mock.patch.object(APP, "activate", side_effect=AssertionError("unexpected direct activation")), \
             mock.patch.object(APP.os, "open", side_effect=AssertionError("unexpected lock")), \
             mock.patch("builtins.print"):
            APP.main()
        self.assertEqual(setup.call_args.args[1], Path("artifacts.json").absolute())

    def metadata_bytes(self):
        return json.dumps(self.host.candidate).encode()

    def test_setup_and_install_main_allow_no_manifest(self):
        for mode in ("--setup", "--install"):
            with self.subTest(mode=mode), \
                 mock.patch.object(APP.sys, "argv", ["ps4-ethernet", mode]), \
                 mock.patch.object(APP, "setup", return_value="setup") as setup, \
                 mock.patch.object(APP, "install", return_value="installed") as install, \
                 mock.patch("builtins.print"):
                APP.main()
            selected = setup if mode == "--setup" else install
            self.assertIsNone(selected.call_args.args[1])
        self.no_network.assert_not_called()

    def test_installed_metadata_is_first_sealed_choice_and_runs_real_preflight(self):
        path = APP.installed_manifest()
        original = self.host.sealed
        def sealed(source, mode=None):
            return self.metadata_bytes() if source == str(path) and mode == 0o600 else original(source, mode)
        with mock.patch.object(APP, "metadata_exists", side_effect=lambda candidate: candidate == path) as exists, \
             mock.patch.object(self.host, "sealed", side_effect=sealed) as checked, \
             mock.patch.object(APP, "read_install_source", side_effect=AssertionError("unsealed input")):
            self.assertEqual(APP.resolve_metadata(self.host), self.metadata_bytes())
        self.assertEqual(exists.call_args_list, [mock.call(path)])
        self.assertIn(mock.call(str(path), 0o600), checked.call_args_list)
        self.assertTrue(any(event[:3] == ("command", "/usr/bin/modprobe", "--show-depends")
                            for event in self.host.events))
        self.assert_no_activation()
        self.no_network.assert_not_called()

    def test_adjacent_before_working_directory_and_exact_release_filename(self):
        self.assertEqual(APP.metadata_locations()[1][0].name, "artifacts.json")
        self.assertEqual(APP.metadata_locations()[-1][0].parent, Path.cwd())
        paths = [(Path("/etc/psx/installed.json"), True), (Path("/download/artifacts.json"), False),
                 (Path("/work/artifacts.json"), False)]
        with mock.patch.object(APP, "metadata_locations", return_value=paths), \
             mock.patch.object(APP, "metadata_exists", side_effect=lambda path: path != paths[0][0]) as exists, \
             mock.patch.object(APP, "read_install_source", return_value=self.metadata_bytes()) as read:
            self.assertEqual(APP.resolve_metadata(self.host), self.metadata_bytes())
        read.assert_called_once_with(paths[1][0], APP.METADATA_LIMIT)
        self.assertEqual(exists.call_count, 2)
        self.no_network.assert_not_called()
        self.assert_no_activation()

    def test_corrupt_or_mismatched_local_candidate_never_falls_through_to_download_or_service(self):
        self.setup_host()
        for data in (b"not JSON", json.dumps(dict(self.host.candidate, kernel_config_sha256="0" * 64)).encode()):
            self.host.events.clear()
            with self.subTest(data=data), \
                 mock.patch.object(APP, "metadata_locations", return_value=[(Path("/local/artifacts.json"), False)]), \
                 mock.patch.object(APP, "metadata_exists", return_value=True), \
                 mock.patch.object(APP, "read_install_source", return_value=data):
                with self.assertRaises((RuntimeError, ValueError)):
                    APP.setup(self.host)
            self.assertFalse(any(event[:2] == ("command", "/usr/bin/systemctl") for event in self.host.events))
            self.assertFalse(self.script.parent.exists() or self.unit.parent.exists())
        self.no_network.assert_not_called()

    def test_unsealed_installed_metadata_is_not_accepted_as_downloaded_input(self):
        installed = APP.installed_manifest()
        installed.parent.mkdir(parents=True)
        installed.write_bytes(self.metadata_bytes())
        installed.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, "sealed root-owned"):
            APP.install(self.host)
        self.assertFalse(self.script.parent.exists())
        self.no_network.assert_not_called()

    def test_missing_local_metadata_fetches_exact_psx_release_and_stages_after_real_validation(self):
        self.no_network.side_effect = None
        self.no_network.return_value = self.metadata_bytes()
        with mock.patch.object(APP, "metadata_exists", return_value=False):
            APP.install(self.host)
        self.no_network.assert_called_once_with(
            "https://github.com/meerzulee/linux-psx/releases/download/v" + APP.PSX_RELEASE + "/artifacts.json")
        self.assertEqual(APP.installed_manifest().read_bytes(), self.metadata_bytes())
        self.assert_no_activation()

    def test_downloaded_wrong_module_or_bad_json_never_installs_or_calls_services(self):
        self.setup_host()
        bad_module = dict(self.host.candidate)
        bad_module["module"] = dict(bad_module["module"], sha256="0" * 64)
        for data in (b"not JSON", json.dumps(bad_module).encode()):
            self.no_network.side_effect = None
            self.no_network.return_value = data
            self.host.events.clear()
            with self.subTest(data=data), mock.patch.object(APP, "metadata_exists", return_value=False):
                with self.assertRaises((RuntimeError, ValueError)):
                    APP.setup(self.host)
            self.assertFalse(self.script.parent.exists() or self.unit.parent.exists())
            self.assertFalse(any(event[:2] == ("command", "/usr/bin/systemctl") for event in self.host.events))
            self.assert_no_activation()

    def legacy_metadata_host(self, release):
        mock.patch.object(APP.os, "uname", return_value=SimpleNamespace(release=release, machine="x86_64")).start()
        APP.select_release()
        module = f"/usr/lib/modules/{release}/kernel/drivers/net/ethernet/{APP.NAME}.ko"
        mock.patch.object(fixture, "MODULE_PATH", module).start()
        host = InstallerSystem(self.directory)
        host.candidate = {"schema": 1, "kernel_release": release,
                          "kernel_config_sha256": APP.digest(host.config), "module_path": module,
                          "module_sha256": APP.digest(host.module), "loaded_note_hex": host.note.hex()}
        return host

    def test_public_legacy_fetch_uses_original_candidate_metadata_and_exact_tag(self):
        self.host = self.legacy_metadata_host("6.18.44-ps4-baikal-mts2-public1")
        self.assertEqual(APP.metadata_locations()[1][0].name, "ethernet-candidate.json")
        self.no_network.side_effect = None
        self.no_network.return_value = self.metadata_bytes()
        with mock.patch.object(APP, "metadata_exists", return_value=False):
            self.assertEqual(APP.resolve_metadata(self.host), self.metadata_bytes())
        self.no_network.assert_called_once_with(
            "https://github.com/meerzulee/linux-ps4/releases/download/v6.18.44-ps4-baikal-mts2-public1/ethernet-candidate.json")
        self.assert_no_activation()

    def test_private_legacy_uses_sealed_original_path_and_has_no_public_fallback(self):
        self.host = self.legacy_metadata_host("6.18.44-ps4-baikal-mts2")
        self.assertEqual(APP.metadata_locations()[1], (APP.LEGACY_PRIVATE_METADATA, True))
        original = self.host.sealed
        def sealed(path, mode=None):
            return self.metadata_bytes() if path == str(APP.LEGACY_PRIVATE_METADATA) and mode == 0o600 else original(path, mode)
        with mock.patch.object(APP, "metadata_exists", side_effect=lambda path: path == APP.LEGACY_PRIVATE_METADATA), \
             mock.patch.object(self.host, "sealed", side_effect=sealed):
            self.assertEqual(APP.resolve_metadata(self.host), self.metadata_bytes())
        with mock.patch.object(APP, "metadata_exists", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "no public metadata download"):
                APP.resolve_metadata(self.host)
        self.no_network.assert_not_called()
        self.assert_no_activation()

    def test_explicit_metadata_never_discovers_or_downloads(self):
        with mock.patch.object(APP, "metadata_locations", side_effect=AssertionError("discovery")):
            APP.resolve_metadata(self.host, self.manifest)
        self.no_network.assert_not_called()
        self.assert_no_activation()

    def test_anonymous_https_fetch_bounds_size_timeout_and_rejects_http_redirect(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://release-assets.githubusercontent.com/artifacts.json"
        response.read.return_value = self.metadata_bytes()
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(APP.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(REAL_FETCH_METADATA("https://github.com/test/artifacts.json"), self.metadata_bytes())
            opener.open.assert_called_once()
            self.assertEqual(opener.open.call_args.kwargs, {"timeout": 10})
            self.assertNotIn("Authorization", opener.open.call_args.args[0].headers)
            response.read.assert_called_once_with(APP.METADATA_LIMIT + 1)
            response.read.return_value = b"x" * (APP.METADATA_LIMIT + 1)
            with self.assertRaisesRegex(RuntimeError, "exceeds1 MiB"):
                REAL_FETCH_METADATA("https://github.com/test/artifacts.json")
            response.read.return_value = self.metadata_bytes()
            response.geturl.return_value = "http://unexpected/artifacts.json"
            with self.assertRaisesRegex(RuntimeError, "HTTPS"):
                REAL_FETCH_METADATA("https://github.com/test/artifacts.json")
        with self.assertRaisesRegex(RuntimeError, "HTTPS"):
            REAL_FETCH_METADATA("http://github.com/test/artifacts.json")
        handler = APP.HTTPSRedirectHandler()
        with self.assertRaisesRegex(RuntimeError, "HTTPS"):
            handler.redirect_request(None, None, 302, "found", {}, "http://insecure/metadata.json")

    def test_no_mode_or_unknown_kernel_never_discovers_downloads_or_opens_output(self):
        with mock.patch.object(APP.sys, "argv", ["ps4-ethernet"]), mock.patch.object(APP.sys, "stderr"):
            with self.assertRaises(SystemExit):
                APP.main()
        with mock.patch.object(APP.sys, "argv", ["ps4-ethernet", "--setup"]), \
             mock.patch.object(APP.os, "uname", return_value=SimpleNamespace(release="unknown", machine="x86_64")), \
             mock.patch.object(APP, "metadata_locations", side_effect=AssertionError("discovery")):
            with self.assertRaisesRegex(RuntimeError, "Unsupported kernel"):
                APP.main()
        self.no_network.assert_not_called()

    def test_activation_defaults_to_installed_metadata_and_artifacts_alias_still_works(self):
        for options, expected in (([], APP.installed_manifest()),
                                  (["--artifacts", "/root/explicit.json"], Path("/root/explicit.json")),
                                  (["--manifest", "/root/explicit.json"], Path("/root/explicit.json"))):
            with self.subTest(options=options), \
                 mock.patch.object(APP.sys, "argv", ["ps4-ethernet", "--activate", *options]), \
                 mock.patch.object(APP.System, "sealed", return_value=b"sealed"), \
                 mock.patch.object(APP, "activate", return_value="enabled") as activate, \
                 mock.patch.object(APP.os, "open", return_value=123) as opened, \
                 mock.patch.object(APP.os, "fstat", return_value=SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o600)), \
                 mock.patch.object(APP.fcntl, "flock"), \
                 mock.patch.object(APP.os, "close"), mock.patch("builtins.print"):
                APP.main()
                self.assertEqual(APP.CANDIDATE, str(expected))
                self.assertEqual(opened.call_args.args[0], APP.LOCK)
                activate.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
