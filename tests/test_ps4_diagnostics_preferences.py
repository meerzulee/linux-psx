"""Display preferences and controller guards using private synthetic files only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('psx_display_tests', ROOT / 'diagnostics/ps4/preferences.py')
APP = importlib.util.module_from_spec(spec)
spec.loader.exec_module(APP)
controller_spec = importlib.util.spec_from_file_location('psx_settings_controller_tests', ROOT / 'diagnostics/ps4/controller.py')
CONTROL = importlib.util.module_from_spec(controller_spec)
controller_spec.loader.exec_module(CONTROL)


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/var/tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = APP.DisplayPreferences(self.root)

    def session_fixture(self, execute):
        store = APP.DisplayPreferences(self.root, executor=execute)
        directory = self.root / 'proc/99'
        directory.mkdir(parents=True)
        (directory / 'stat').write_text('99 (graphics-session) ' + ' '.join(['S'] + ['0']*18 + ['12345']))
        (directory / 'cmdline').write_bytes(b'/bin/bash\0/usr/lib/psx-diagnostics/graphics-session\0')
        boot = self.root / 'proc/sys/kernel/random/boot_id'
        boot.parent.mkdir(parents=True)
        boot.write_text('fixture-boot')
        tool = self.root / 'usr/bin/xfconf-query'
        tool.parent.mkdir(parents=True)
        tool.write_text('synthetic; never executed')
        tool.chmod(0o755)
        bus = self.root / 'tmp/dbus-fixture'
        bus.parent.mkdir()
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        server.bind(str(bus))
        with mock.patch.dict(os.environ, {'DISPLAY': ':0', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/tmp/dbus-fixture'}):
            store.session_start(99)
        return store

    def test_default_is_olive_and_state_never_executes_host_tools(self):
        with mock.patch.object(APP.subprocess, 'run', side_effect=AssertionError('live command')):
            state = self.store.state()
        self.assertEqual(state['theme'], 'olive')
        self.assertEqual(state['font_size'], 16)
        self.assertFalse(self.store.path.exists())
        self.assertIn({'id': 'tokyo-night', 'name': 'Tokyo Night'}, state['available_themes'])

    def test_preferences_round_trip_private_and_partial_updates_preserve_other_fields(self):
        saved = self.store.set({'theme': 'tokyo-night', 'font_size': 12})
        self.assertFalse(saved['settings']['live']['applied'])
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        fresh = APP.DisplayPreferences(self.root)
        self.assertEqual(fresh.load()['theme'], 'tokyo-night')
        fresh.set({'font_size': 24})
        self.assertEqual(fresh.load()['theme'], 'tokyo-night')
        self.assertEqual(fresh.load()['font_family'], 'JetBrainsMono Nerd Font')

    def test_invalid_fields_ids_family_and_size_never_write(self):
        for changes in ({'theme': '../other'}, {'font_size': 9}, {'font_size': 25}, {'font_size': True},
                        {'font_size': '14'}, {'font_family': 'Other; shell'}, {'shell': 'reboot'}, {}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.set(changes)
            self.assertFalse(self.store.path.exists())

    def test_interrupted_atomic_publish_retains_previous_preferences(self):
        self.store.set({'font_size': 14})
        previous = self.store.path.read_bytes()
        with mock.patch.object(APP.os, 'replace', side_effect=OSError('synthetic publication failure')):
            with self.assertRaises(OSError):
                self.store.set({'font_size': 18})
        self.assertEqual(self.store.path.read_bytes(), previous)
        self.assertEqual(list(self.store.path.parent.glob('.display-*')), [])

    def test_symlink_and_nonprivate_saved_state_are_refused(self):
        self.store.set({'font_size': 14})
        self.store.path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.store.load()
        self.assertIn('could not be read', self.store.state()['error'])
        self.store.path.unlink()
        target = self.root / 'unrelated'
        target.write_text('preserve')
        self.store.path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.store.set({'font_size': 18})
        self.assertEqual(target.read_text(), 'preserve')

    def test_live_updates_use_only_fixed_xfconf_properties_on_private_session(self):
        calls = []
        store = self.session_fixture(lambda argv, env: calls.append((argv, env)))
        calls.clear()
        response = store.set({'font_size': 20, 'theme': 'nord'})
        self.assertTrue(response['settings']['live']['applied'])
        font = next(argv for argv, _ in calls if '/font-name' in argv)
        self.assertEqual(font[-1], 'JetBrainsMono Nerd Font 20')
        self.assertTrue(all(argv[1:3] == ['--channel', 'xfce4-terminal'] for argv, _ in calls))
        self.assertTrue(all(env['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=/tmp/dbus-fixture' for _, env in calls))
        self.assertFalse(any('systemctl' in str(argv) for argv, _ in calls))
        restarted = APP.DisplayPreferences(self.root)
        self.assertTrue(restarted.state()['live']['applied'])
        (self.root / 'proc/99/stat').write_text('99 (graphics-session) ' + ' '.join(['S'] + ['0']*18 + ['different-start']))
        self.assertFalse(restarted.state()['live']['applied'])

    def test_failed_live_tool_keeps_saved_values_and_reports_next_launch(self):
        store = self.session_fixture(lambda argv, env: None)
        def fail(argv, env):
            raise subprocess.CalledProcessError(1, argv)
        store.execute = fail
        response = store.set({'font_size': 22})
        self.assertEqual(store.load()['font_size'], 22)
        self.assertFalse(response['settings']['live']['applied'])
        self.assertIn('Reopen', response['message'])

    def test_stale_session_cannot_update_another_process_and_failed_live_apply_still_saves(self):
        calls = []
        store = self.session_fixture(lambda argv, env: calls.append(argv))
        calls.clear()
        (self.root / 'proc/sys/kernel/random/boot_id').write_text('next-boot')
        response = store.set({'font_size': 18})
        self.assertFalse(response['settings']['live']['applied'])
        self.assertEqual(calls, [])
        self.assertEqual(store.load()['font_size'], 18)

    def test_imported_palettes_retain_pinned_provenance_and_mit_notice(self):
        manifest = json.loads((ROOT / 'diagnostics/ps4/themes.json').read_text())
        self.assertEqual(manifest['upstream']['commit'], 'f0020448ca87329199de7cb12f2015ebc4a3e5e7')
        notice = (ROOT / 'diagnostics/ps4/themes-LICENSE').read_bytes()
        self.assertEqual(hashlib.sha256(notice).hexdigest(), manifest['upstream']['license_sha256'])
        self.assertIn(b'Copyright (c) David Heinemeier Hansson', notice)
        self.assertTrue(all(len(theme['source_sha256']) == 64 for theme in manifest['themes'] if theme['id'] != 'olive'))

    def test_browser_styles_follow_selected_palette_without_script_or_external_assets(self):
        self.store.set({'theme': 'gruvbox'})
        css = self.store.browser_css()
        self.assertIn('#282828', css)
        self.assertNotIn('url(', css)
        self.assertNotIn('<script', css)

    def test_controller_settings_honor_recovery_and_kernel_guards(self):
        controller = CONTROL.Controller(self.root / 'results', self.root,
            network=SimpleNamespace(status=lambda: {}, ssh_status=lambda: {}))
        with mock.patch.object(controller.runner, 'identity', return_value={'release_matches': True, 'boot_id': 'current'}), \
             mock.patch.object(controller, 'results', return_value=[{'stop_required': True, 'identity': {'boot_id': 'current'}}]):
            with self.assertRaisesRegex(ValueError, 'recovery'):
                controller.request('settings.set', {'font_size': 18})
        with mock.patch.object(controller.runner, 'identity', return_value={'release_matches': False, 'boot_id': 'current'}):
            with self.assertRaisesRegex(ValueError, 'matching'):
                controller.request('settings.set', {'theme': 'nord'})
        self.assertFalse(controller.display.path.exists())


if __name__ == '__main__':
    unittest.main()
