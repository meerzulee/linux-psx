"""Native Wayland source guards with synthetic roots/archives only; no displays."""
import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT/path))
    spec = importlib.util.spec_from_loader(name, loader)
    app = importlib.util.module_from_spec(spec); sys.modules[name] = app
    loader.exec_module(app)
    return app


RUNTIME = load('psx_wayland_runtime_tests', 'diagnostics/ps4/wayland/runtime.py')
LAUNCH = load('psx_wayland_launch_tests', 'rootfs/ps4-wayland/launch-wayland')
PROFILE = json.loads((ROOT/'manifests/ps4-wayland-suite.json').read_text())


class Operations:
    def __init__(self):
        self.actual_release = PROFILE['kernel_release']
        self.is_root = True
        self.active = None
        self.commands = []
    def root(self): return self.is_root
    def release(self): return self.actual_release
    def node(self, _): return SimpleNamespace(st_mode=stat.S_IFCHR|0o600, st_rdev=os.makedev(226, 0))
    def command(self, argv):
        self.commands.append(argv)
        return SimpleNamespace(stdout='active\n' if argv[-1] == self.active else 'inactive\n')


class WaylandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/var/tmp', prefix='psx-wayland-tests-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.suite = self.base/'root', self.base/'suite'
        self.root.mkdir(); self.suite.mkdir()
        self.ops = Operations()
        inputs = {
            'etc/psx-diagnostics/boot-set.json': json.dumps({'kernel_release': PROFILE['kernel_release'],
                'mesa_manifest_sha256': PROFILE['mesa_manifest_sha256']}),
            'proc/sys/kernel/random/boot_id': '12345678-1234-4234-8234-123456789abc\n',
            'sys/class/drm/card0/device/vendor': '0x1002\n',
            'etc/pam.d/login': 'synthetic PAM fixture\n',
            'usr/lib/security/pam_systemd.so': 'synthetic PAM library fixture\n',
        }
        for name, data in inputs.items():
            path = self.root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(data)
        runtime = (ROOT/'diagnostics/ps4/wayland/runtime.py').read_bytes()
        (self.suite/'runtime.py').write_bytes(runtime); (self.suite/'runtime.py').chmod(0o755)
        self.manifest = {key: PROFILE[key] for key in ('schema_version', 'profile', 'kernel_release',
            'mesa_manifest_sha256', 'backend', 'renderer', 'maximum_unpacked_bytes', 'hardware_acceptance')}
        self.manifest['files'] = {'runtime.py': {'bytes': len(runtime), 'sha256': hashlib.sha256(runtime).hexdigest(), 'mode': '0755'}}
        self.write_manifest()

    def write_manifest(self):
        (self.suite/'suite.json').write_text(json.dumps(self.manifest))

    def archive(self, extra=None):
        output = self.base/'suite.tar.gz'
        with tarfile.open(output, 'w:gz') as archive:
            for path in sorted(self.suite.iterdir()):
                data = path.read_bytes(); item = tarfile.TarInfo(path.name)
                item.size = len(data); item.mode = stat.S_IMODE(path.stat().st_mode)
                archive.addfile(item, io.BytesIO(data))
            if extra is not None: archive.addfile(extra, io.BytesIO(b'x'*extra.size))
        return output, hashlib.sha256(output.read_bytes()).hexdigest()

    def test_wrong_kernel_refuses_before_commands_or_devices(self):
        self.ops.actual_release = 'host-kernel'
        with self.assertRaisesRegex(ValueError, 'matching PS4'):
            RUNTIME.preflight(self.suite, self.root, self.ops)
        self.assertEqual(self.ops.commands, [])

    def test_fixture_requires_explicit_synthetic_operations(self):
        with self.assertRaisesRegex(ValueError, 'injected'):
            RUNTIME.preflight(self.suite, self.root)

    def test_alpha_artifact_pins_cannot_be_satisfied_by_a_matching_release_alone(self):
        boot_path = self.root/'etc/psx-diagnostics/boot-set.json'
        boot = json.loads(boot_path.read_text())
        for field in ('kernel_sha256', 'rootfs_sha256'):
            with self.subTest(field=field):
                self.manifest[field] = 'a'*64
                self.write_manifest()
                self.ops.commands.clear()
                with self.assertRaisesRegex(ValueError, 'artifact identity'):
                    RUNTIME.preflight(self.suite, self.root, self.ops)
                self.assertEqual(self.ops.commands, [])
                boot[field] = 'a'*64
                boot_path.write_text(json.dumps(boot))
                self.assertFalse(RUNTIME.preflight(self.suite, self.root, self.ops)['hardware_acceptance'])

    def test_valid_preflight_is_data_only_and_selects_one_amd_card(self):
        result = RUNTIME.preflight(self.suite, self.root, self.ops)
        self.assertEqual(result['drm_device'], '/dev/dri/card0')
        self.assertEqual(result['renderer'], 'gles2')
        self.assertFalse(result['nested']); self.assertFalse(result['hardware_acceptance'])
        self.assertEqual(len(self.ops.commands), 6)
        self.assertIn('--verify-only', self.ops.commands[-1])
        self.assertFalse(any('start' in command or 'systemd-run' in command for command in self.ops.commands))

    def test_existing_xorg_session_blocks_native_drm_handoff(self):
        self.ops.active = 'psx-diagnostics-graphics@diagnostics.service'
        with self.assertRaisesRegex(ValueError, 'Close the current Xorg'):
            RUNTIME.preflight(self.suite, self.root, self.ops)
        self.assertFalse(any('--verify-only' in command for command in self.ops.commands))

    def test_multiple_amd_devices_are_never_auto_selected(self):
        path = self.root/'sys/class/drm/card1/device/vendor'
        path.parent.mkdir(parents=True); path.write_text('0x1002\n')
        with self.assertRaisesRegex(ValueError, 'one explicit AMD'):
            RUNTIME.preflight(self.suite, self.root, self.ops)

    def test_changed_suite_bytes_and_extra_libraries_are_refused(self):
        (self.suite/'runtime.py').write_bytes(b'unverified replacement')
        with self.assertRaisesRegex(ValueError, 'differs'):
            RUNTIME.verify_tree(self.suite)
        (self.suite/'runtime.py').write_bytes((ROOT/'diagnostics/ps4/wayland/runtime.py').read_bytes())
        (self.suite/'unexpected.so').write_bytes(b'unknown library')
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            RUNTIME.verify_tree(self.suite)

    def test_renderer_environment_cannot_inherit_nested_software_or_vulkan_mode(self):
        base = {'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'other', 'WAYLAND_SOCKET': '7',
                'WLR_BACKENDS': 'headless', 'WLR_RENDERER': 'vulkan',
                'WLR_RENDERER_ALLOW_SOFTWARE': '1', 'LIBGL_ALWAYS_SOFTWARE': '1',
                'VK_DRIVER_FILES': '/untrusted.json', 'LD_PRELOAD': '/untrusted.so', 'XDG_SESSION_ID': '42'}
        result = RUNTIME.compositor_environment(base, Path('/suite'), Path('/runtime'), '/dev/dri/card0')
        self.assertEqual(result['WLR_BACKENDS'], 'drm,libinput')
        self.assertEqual(result['WLR_RENDERER'], 'gles2')
        self.assertEqual(result['LIBSEAT_BACKEND'], 'logind')
        self.assertEqual(result['XDG_SESSION_ID'], '42')
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'WLR_RENDERER_ALLOW_SOFTWARE',
                     'LIBGL_ALWAYS_SOFTWARE', 'VK_DRIVER_FILES', 'LD_PRELOAD'):
            self.assertNotIn(name, result)

    def test_archive_pin_and_member_hashes_are_required(self):
        path, checksum = self.archive()
        manifest, members = LAUNCH.inspect_archive(path, checksum)
        self.assertEqual(manifest['profile'], PROFILE['profile'])
        self.assertEqual(set(members), {'suite.json', 'runtime.py'})
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            LAUNCH.inspect_archive(path, 'a'*64)
        self.manifest['files']['runtime.py']['sha256'] = 'a'*64; self.write_manifest()
        path, checksum = self.archive()
        with self.assertRaisesRegex(ValueError, 'member differs'):
            LAUNCH.inspect_archive(path, checksum)

    def test_archive_traversal_and_symlinks_are_never_extracted(self):
        for name, kind in (('../escape', tarfile.REGTYPE), ('linked', tarfile.SYMTYPE)):
            item = tarfile.TarInfo(name); item.type = kind; item.linkname = '/elsewhere'; item.size = 0
            path, checksum = self.archive(item)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unsafe'):
                LAUNCH.inspect_archive(path, checksum)

    def test_default_plan_has_no_staging_or_activation(self):
        path, checksum = self.archive()
        before = sorted(str(path.relative_to(self.base)) for path in self.base.rglob('*'))
        args = SimpleNamespace(suite=path, expected_sha256=checksum, apply=False)
        with contextlib.redirect_stdout(io.StringIO()):
            LAUNCH.launch(args, self.root, self.ops)
        self.assertEqual(before, sorted(str(path.relative_to(self.base)) for path in self.base.rglob('*')))
        self.assertFalse(any('start' in command for command in self.ops.commands))
        args.apply = True
        with self.assertRaisesRegex(ValueError, 'cannot activate'):
            LAUNCH.launch(args, self.root, self.ops)

    def test_unit_is_opt_in_bounded_and_returns_to_tty1(self):
        command = LAUNCH.systemd_command('a'*32)
        self.assertIn('--property=PAMName=login', command)
        self.assertIn('--property=TTYPath=/dev/tty3', command)
        self.assertIn('--property=ExecStopPost=-/usr/bin/chvt 1', command)
        self.assertIn('--property=RuntimeMaxSec=35', command)
        self.assertIn('--property=KillMode=control-group', command)
        self.assertNotIn('enable', command)

    def test_only_successful_native_hardware_pixel_reports_can_pass(self):
        report = {'status': 'PASS', 'native_wayland': True, 'known_pixels': True, 'frames': 3,
                  'renderer': 'AMD LIVERPOOL (radeonsi)'}
        self.assertTrue(RUNTIME.client_passed(0, report))
        for change in ({'renderer': 'llvmpipe'}, {'known_pixels': False}, {'native_wayland': False}, {'frames': 0}):
            self.assertFalse(RUNTIME.client_passed(0, dict(report, **change)))
        self.assertFalse(RUNTIME.client_passed(124, report))


if __name__ == '__main__': unittest.main()
