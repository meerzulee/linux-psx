"""Console preparation guards never change the host or an SSH terminal."""
import contextlib
import gzip
import importlib.machinery
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('psx_console_test', str(ROOT / 'rootfs/ps4-diagnostics-desktop/prepare-console'))
spec = importlib.util.spec_from_loader('psx_console_test', loader)
APP = importlib.util.module_from_spec(spec)
loader.exec_module(APP)
RELEASE = '7.2.11-psx-ps4-baikal-native-dev3'


class ButtonIconFontTests(unittest.TestCase):
    """The patched font must stay the same shape the console already accepts."""
    HEBREW = 0x05D0

    def font(self, width=16, height=32, unicode_table=True):
        count = 512
        size = 64 if (width, height) == (16, 32) else ((width + 7) // 8) * height
        header = struct.pack('<8I', APP.MAGIC, 0, 32, 1 if unicode_table else 0, count, size, height, width)
        glyphs = bytes(index % 251 for index in range(count * size))
        points = list(range(0x20, 0x20 + count))
        points[300:320] = [self.HEBREW + offset for offset in range(20)]
        table = b''.join(chr(point).encode() + b'\xff' for point in points)
        return header + glyphs + (table if unicode_table else b'')

    def entries(self, font):
        count, size = struct.unpack_from('<II', font, 16)
        return font[32 + count * size:].split(b'\xff')[:count]

    def test_icons_take_only_the_unused_letters(self):
        original = self.font()
        patched = APP.add_button_icons(original)
        self.assertEqual(patched[:32], original[:32])
        entries = self.entries(patched)
        points = [chr(0xE0F0 + index).encode() for index in range(8)] + ['\u2191'.encode(), '\u2193'.encode(),
                                                                       '\u258c'.encode(), '\u2590'.encode()]
        for point in points:
            self.assertEqual(entries.count(point), 1)
        changed = [slot for slot in range(512) if patched[32 + slot * 64:32 + (slot + 1) * 64] != original[32 + slot * 64:32 + (slot + 1) * 64]]
        self.assertEqual(changed, list(range(300, 312)))
        self.assertEqual(entries[:300], self.entries(original)[:300])
        self.assertEqual(entries[312:], self.entries(original)[312:])

    def test_half_blocks_cover_exactly_half_of_the_cell(self):
        patched = APP.add_button_icons(self.font())
        entries = self.entries(patched)
        for point, row in (('\u258c', b'\xff\x00'), ('\u2590', b'\x00\xff')):
            slot = entries.index(point.encode())
            self.assertEqual(patched[32 + slot * 64:32 + (slot + 1) * 64], row * 32)

    def test_a_font_of_another_shape_is_refused(self):
        for font in (self.font(unicode_table=False), self.font(width=8, height=16)):
            with self.assertRaises(ValueError):
                APP.add_button_icons(font)

    def test_the_real_font_can_be_patched_when_installed(self):
        path = Path('/usr/share/kbd/consolefonts/latarcyrheb-sun32.psfu.gz')
        if not path.exists():
            self.skipTest('kbd console fonts are not installed on this host')
        font = gzip.decompress(path.read_bytes())
        patched = APP.add_button_icons(font)
        self.assertEqual(patched[:32], font[:32])
        self.assertEqual(self.entries(patched).count(chr(0xE0F0).encode()), 1)

    def test_a_failed_patch_falls_back_to_the_stock_font(self):
        console = APP.Console()
        with mock.patch.object(APP.gzip, 'open', side_effect=OSError('missing')), \
                mock.patch.object(APP.subprocess, 'run') as run:
            self.assertEqual(console.font('/dev/tty1'), APP.FONT)
        self.assertTrue(run.call_args.args[0][-1].endswith(APP.FONT + '.psfu.gz'))


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


class RootShellTests(unittest.TestCase):
    def test_tar_is_on_the_path_before_the_screen_starts_and_cannot_stop_it(self):
        text = (ROOT / "rootfs/ps4-diagnostics-desktop/start-console").read_text()
        bind = text.index("/usr/bin/mount --bind /run/psx-bench/bin /usr/local/sbin 2>/dev/null || true")
        self.assertLess(bind, text.index("/usr/bin/psx-diagnostics-tui --socket"))
        self.assertLess(text.index("[ -x /run/psx-bench/bin/tar ]"), bind)
        self.assertLess(text.index("mountpoint -q /usr/local/sbin"), bind)

    def test_the_prompt_and_typed_text_reach_the_screen_after_the_tests_close(self):
        """The console service sends stderr to the journal, where bash writes both."""
        with tempfile.TemporaryDirectory(dir="/var/tmp") as temporary:
            root = Path(temporary)
            tui = root / "tui"
            tui.write_text("#!/bin/sh\nexit 0\n")
            tui.chmod(0o755)
            text = (ROOT / "rootfs/ps4-diagnostics-desktop/start-console").read_text()
            script = root / "start-console"
            script.write_text(text.replace("/usr/bin/psx-diagnostics-tui", str(tui)).replace("/dev/kmsg", "/dev/null"))
            script.chmod(0o755)
            master, slave = pty.openpty()
            self.addCleanup(os.close, master)
            with (root / "journal").open("wb") as journal:
                process = subprocess.Popen(["/bin/sh", str(script)], stdin=slave, stdout=slave, stderr=journal,
                                           start_new_session=True, env={"PATH": "/usr/bin", "TERM": "linux"})
            os.close(slave)
            self.addCleanup(process.wait)
            self.addCleanup(process.kill)
            time.sleep(.5)
            os.write(master, b"echo typed-text\n")
            time.sleep(.3)
            os.write(master, b"exit\n")
            screen, deadline = b"", time.monotonic() + 5
            while time.monotonic() < deadline:
                if select.select([master], [], [], .2)[0]:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    screen += data
            self.assertIn(b"Local root console", screen)
            self.assertIn(b"echo typed-text", screen)
            self.assertEqual((root / "journal").read_bytes(), b"")


if __name__ == '__main__':
    unittest.main()
