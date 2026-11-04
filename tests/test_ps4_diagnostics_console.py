"""Console preparation guards never change the host or an SSH terminal."""
import contextlib
import importlib.machinery
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('psx_console_test', str(ROOT / 'rootfs/ps4-diagnostics-desktop/prepare-console'))
spec = importlib.util.spec_from_loader('psx_console_test', loader)
APP = importlib.util.module_from_spec(spec)
loader.exec_module(APP)
RELEASE = '7.2.11-psx-ps4-baikal-native-dev3'


class FakeConsole:
    def __init__(self, release=RELEASE, tty='/dev/tty1'):
        self.actual_release, self.actual_tty = release, tty
        self.actions = []

    def release(self): return self.actual_release
    def root(self): return True
    def tty(self, _): return self.actual_tty
    def text_mode(self, _): return True
    def redirect_messages(self, _): self.actions.append('redirect')
    def hide_systemd_status(self): self.actions.append('hide-status')
    def log_view(self): self.actions.append('log-view')
    def palette(self, _): self.actions.append('palette')
    def font(self, _): self.actions.append('font')
    def clear(self, _): self.actions.append('clear')
    def disable_blanking(self, _): self.actions.append('no-blanking')
    def library_cache(self, directory): self.actions.append(('ldconfig', directory))


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/var/tmp', prefix='psx-console-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'etc/psx-diagnostics').mkdir(parents=True)
        (self.root / 'etc/psx-diagnostics/boot-set.json').write_text(json.dumps({'kernel_release': RELEASE}))
        (self.root / 'proc/1').mkdir(parents=True)
        (self.root / 'proc/1/comm').write_text('systemd\n')

    def test_fixture_never_falls_back_to_host_operations(self):
        with self.assertRaisesRegex(ValueError, 'synthetic'):
            APP.prepare(self.root)

    def test_wrong_kernel_cannot_change_console_or_signal_pid1(self):
        console = FakeConsole(release='host-kernel')
        with self.assertRaisesRegex(ValueError, 'matching'):
            APP.prepare(self.root, console)
        self.assertEqual(console.actions, [])

    def test_ssh_terminal_has_no_global_console_side_effects(self):
        console = FakeConsole(tty='/dev/pts/3')
        self.assertFalse(APP.prepare(self.root, console)['applied'])
        self.assertEqual(console.actions, [])

    def test_local_vt_is_cleaned_before_the_menu_without_muting_uart(self):
        console = FakeConsole()
        result = APP.prepare(self.root, console)
        self.assertEqual(console.actions, ['redirect', 'hide-status', 'log-view', 'no-blanking', 'font', 'palette', 'clear'])
        self.assertEqual(result['console_blank_timeout_seconds'], 0)
        self.assertEqual(result['kernel_messages_vt'], 11)
        self.assertEqual(result['journal_vt'], 12)
        self.assertNotIn('loglevel', result)

    def library_bundle(self):
        directory = self.root / 'run/psx-frontend'; directory.mkdir(parents=True, mode=0o755)
        directory.chmod(0o755)
        records = []
        for name in APP.RUNTIME_LIBRARIES:
            data = bytearray(64); data[:7] = b'\x7fELF\x02\x01\x01'
            struct.pack_into('<HH', data, 16, 3, 62)
            data = bytes(data) + name.encode()
            path = directory / name; path.write_bytes(data); path.chmod(0o644)
            records.append({'name': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                            'mode': '0644', 'bind_target': None})
        manifest = self.root / 'etc/psx-diagnostics/boot-set.json'
        manifest.write_text(json.dumps({'kernel_release': RELEASE, 'frontend_overrides': {'files': records}}))
        return directory

    def test_verified_ram_libraries_register_before_ui_without_a_tty_dependency(self):
        directory = self.library_bundle()
        console = FakeConsole(tty='/dev/pts/3')
        result = APP.prepare(self.root, console)
        self.assertFalse(result['applied'])
        self.assertTrue(result['runtime_libraries']['applied'])
        self.assertEqual(console.actions, [('ldconfig', str(directory))])

    def test_wrong_kernel_does_not_register_even_verified_libraries(self):
        self.library_bundle()
        console = FakeConsole(release='host-kernel')
        with self.assertRaisesRegex(ValueError, 'matching'):
            APP.prepare(self.root, console)
        self.assertEqual(console.actions, [])

    def test_missing_or_modified_ram_library_cannot_reach_ldconfig(self):
        directory = self.library_bundle()
        (directory / APP.RUNTIME_LIBRARIES[0]).write_bytes(b'unverified bytes')
        console = FakeConsole(tty='/dev/pts/3')
        result = APP.prepare(self.root, console)
        self.assertFalse(result['runtime_libraries']['applied'])
        self.assertTrue(result['errors'])
        self.assertEqual(console.actions, [])
        (directory / APP.RUNTIME_LIBRARIES[0]).unlink()
        result = APP.prepare(self.root, console)
        self.assertFalse(result['runtime_libraries']['applied'])
        self.assertEqual(console.actions, [])

    def test_unsearchable_ram_library_directory_cannot_reach_ldconfig(self):
        directory = self.library_bundle(); directory.chmod(0o700)
        console = FakeConsole(tty='/dev/pts/3')
        result = APP.prepare(self.root, console)
        self.assertFalse(result['runtime_libraries']['applied'])
        self.assertEqual(console.actions, [])

    def test_library_cache_refresh_never_rewrites_readonly_system_symlinks(self):
        with mock.patch.object(APP.subprocess, 'run') as run:
            APP.Console().library_cache('/run/psx-frontend')
        self.assertEqual(run.call_args.args[0], ['/usr/bin/ldconfig', '-X', '/run/psx-frontend'])
        self.assertTrue(run.call_args.kwargs['check'])

    def active_bundle(self):
        directory = self.library_bundle()
        manifest = self.root / 'etc/psx-diagnostics/boot-set.json'
        candidate = json.loads(manifest.read_text())['frontend_overrides']
        loaded = (json.dumps({'kernel_release': RELEASE, 'frontend_overrides': {'files': []},
                              'evidence': 'original loaded boot remains unchanged'}) + '\n').encode()
        manifest.write_bytes(loaded)
        boot_id = '12345678-1234-4234-8234-123456789abc'
        boot_path = self.root / 'proc/sys/kernel/random/boot_id'
        boot_path.parent.mkdir(parents=True); boot_path.write_text(boot_id + '\n')
        receipt = directory / 'active-overrides.json'
        receipt.write_text(json.dumps({'schema_version': 1, 'boot_id': boot_id,
            'base_manifest_sha256': hashlib.sha256(loaded).hexdigest(), 'frontend_overrides': candidate}))
        receipt.chmod(0o644)
        return receipt, loaded

    @contextlib.contextmanager
    def receipt_owner(self, receipt, uid=0):
        real_lstat, real_fstat = os.lstat, os.fstat
        identity = (receipt.stat().st_dev, receipt.stat().st_ino)

        def adjust(info):
            if (info.st_dev, info.st_ino) != identity:
                return info
            values = list(info); values[4] = uid
            return os.stat_result(values)

        def fake_lstat(*args, **kwargs):
            return adjust(real_lstat(*args, **kwargs))

        def fake_fstat(*args, **kwargs):
            return adjust(real_fstat(*args, **kwargs))

        with mock.patch.object(APP.os, 'lstat', side_effect=fake_lstat), \
             mock.patch.object(APP.os, 'fstat', side_effect=fake_fstat):
            yield

    def test_active_frontend_receipt_registers_exact_live_assets_without_rewriting_loaded_identity(self):
        receipt, loaded = self.active_bundle()
        console = FakeConsole(tty='/dev/pts/3')
        with self.receipt_owner(receipt):
            result = APP.prepare(self.root, console)
        self.assertTrue(result['runtime_libraries']['applied'])
        self.assertEqual(len(console.actions), 1)
        self.assertEqual((self.root / 'etc/psx-diagnostics/boot-set.json').read_bytes(), loaded)

    def test_active_frontend_receipt_rejects_stale_boot_and_wrong_base_hash(self):
        receipt, _ = self.active_bundle()
        original = json.loads(receipt.read_text())
        for field, value in (('boot_id', 'stale-boot'), ('base_manifest_sha256', 'a'*64)):
            receipt.write_text(json.dumps(dict(original, **{field: value})))
            console = FakeConsole(tty='/dev/pts/3')
            with self.receipt_owner(receipt):
                result = APP.prepare(self.root, console)
            self.assertFalse(result['runtime_libraries']['applied'])
            self.assertIn('does not bind', result['errors'][0])
            self.assertEqual(console.actions, [])

    def test_active_frontend_receipt_requires_root_ownership(self):
        receipt, _ = self.active_bundle()
        console = FakeConsole(tty='/dev/pts/3')
        with self.receipt_owner(receipt, uid=123):
            result = APP.prepare(self.root, console)
        self.assertFalse(result['runtime_libraries']['applied'])
        self.assertIn('root-owned', result['errors'][0])
        self.assertEqual(console.actions, [])


if __name__ == '__main__':
    unittest.main()
