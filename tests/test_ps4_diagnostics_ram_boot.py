#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host-only RAM appliance guards; no mounts, devices or console commands."""
import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ram_boot_test', REPO / 'diagnostics/ps4/ram_boot.py')
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)


class RAMBoot(unittest.TestCase):
    def directory(self):
        d = tempfile.TemporaryDirectory(dir='/var/tmp', prefix='psx-ram-guard-')
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def contract(self):
        frontend = [{'name': n, 'bytes': 1, 'sha256': B.sha(b'x'), 'mode': f'{mode:04o}', 'bind_target': target}
                    for n, (target, mode) in B.FRONTEND_TARGETS.items()]
        extras = [{'name': n, 'bytes': 1, 'sha256': B.sha(b'x'), 'mode': f'{mode:04o}', 'bind_target': target}
                  for n, (target, mode) in B.EXTRA_TARGETS.items()]
        return dict(B.PINS, profile=B.PROFILE, image_version='ram-alpha1', kernel_release=B.RELEASE,
                    root_format='ext4-ro-loop', rootfs_image_bytes=B.ROOT_BYTES, module_count=41,
                    hardware_acceptance=False, state=B.state_contract(),
                    boot_source={'kind': 'embedded-ram', 'backing_path': B.BACKING,
                                 'compressed_path': '/psx-ram/rootfs.ext4.gz', 'compressed_bytes': 123,
                                 'compressed_sha256': 'a' * 64, 'maximum_backing_bytes': B.BACKING_BYTES},
                    frontend_overrides={'files': frontend}, ram_assets={'files': extras},
                    required_boot_arguments=['amdgpu.ps4_uvd_runtime=1', 'amdgpu.ps4_uvd_ready=1',
                                             'amdgpu.ps4_uvd_poll_state=1', 'amdgpu.ps4_uvd_poff=1'])

    def test_exact_manifest_and_volatile_state_not_usb(self):
        manifest = self.contract()
        self.assertIs(B.validate_manifest(manifest), manifest)
        self.assertFalse(manifest['state']['persistent'])
        self.assertTrue(manifest['state']['survives_controller_restart'])
        self.assertNotIn('installed.json', B.encoded(manifest).decode())
        self.assertNotIn('/run/psx-media', B.encoded(manifest).decode())
        for key, value in (('kernel_release', 'wrong'), ('kernel_sha256', '0' * 64),
                           ('modules_sha256', '0' * 64), ('rootfs_sha256', '0' * 64),
                           ('rootfs_image_bytes', B.ROOT_BYTES + 1), ('hardware_acceptance', True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                B.validate_manifest(dict(manifest, **{key: value}))

    def test_only_the_frozen_root_and_its_chromium_variant_are_accepted(self):
        manifest = self.contract()
        self.assertIsNotNone(B.validate_manifest(dict(manifest, rootfs_sha256=B.CHROMIUM_ROOT_SHA256)))
        self.assertEqual(B.PINS['rootfs_sha256'], 'b5b3a0ec3001fa99816ad68071d93091a6c8d0907aeb4ba384094625b7af0107')
        with self.assertRaises(ValueError):
            B.validate_manifest(dict(manifest, kernel_sha256=B.CHROMIUM_ROOT_SHA256))

    def test_manifest_rejects_fake_usb_self_measurement_alias_and_state_drift(self):
        original = self.contract()
        for change in ('usb', 'self-hash', 'state', 'target', 'missing'):
            value = copy.deepcopy(original)
            if change == 'usb': value['boot_source']['kind'] = 'usb'
            elif change == 'self-hash': value['initramfs_sha256'] = 'a' * 64
            elif change == 'state': value['state']['persistent'] = True
            elif change == 'target': value['frontend_overrides']['files'][0]['bind_target'] = '/usr/bin/another'
            else: value['ram_assets']['files'].pop(0)
            with self.subTest(change=change), self.assertRaises(ValueError): B.validate_manifest(value)

    def test_streamed_gzip_oracle_and_corruption_size_limits(self):
        root = self.directory(); data = b'0123456789' * 10000; path = root / 'root.gz'
        path.write_bytes(gzip.compress(data, mtime=0))
        self.assertEqual(B.verify_gzip(path, len(data), B.sha(data))['bytes'], len(data))
        for size, checksum in ((len(data) - 1, B.sha(data)), (len(data) + 1, B.sha(data)),
                               (len(data), '0' * 64), (B.ROOT_BYTES + 1, B.sha(data)), (True, B.sha(data))):
            with self.subTest(size=size), self.assertRaises(ValueError): B.verify_gzip(path, size, checksum)
        raw = path.read_bytes(); path.write_bytes(raw[:-5])
        with self.assertRaises((EOFError, OSError, ValueError)): B.verify_gzip(path, len(data), B.sha(data))

    def test_regular_file_guard_refuses_symlinks_and_size_drift(self):
        root = self.directory(); p = root / 'data'; p.write_bytes(b'abc')
        link = root / 'link'; link.symlink_to(p)
        with self.assertRaises(OSError): B.checked(link)
        with self.assertRaises(ValueError): B.checked(p, maximum=2)
        with self.assertRaises(ValueError): B.checked(p, '0' * 64)

    def test_proc_mountinfo_is_bounded_without_regular_file_size_assumption(self):
        root = self.directory(); pid = root / 'proc' / str(os.getpid()); pid.mkdir(parents=True)
        info = pid / 'mountinfo'; info.write_text('1 0 0:1 / / rw - tmpfs tmpfs rw\n')
        real_fstat = B.os.fstat
        def zero_size(fd):
            st = real_fstat(fd)
            values = list(st); values[6] = 0
            return os.stat_result(values)
        # /proc reports st_size=0 despite readable text. Keep nanosecond fields
        # deterministic because os.stat_result(tuple) does not populate them.
        with mock.patch.object(B.os, 'fstat', side_effect=zero_size):
            self.assertEqual(B._mounts(root)['/']['fs'], 'tmpfs')
        with self.assertRaises(ValueError):
            with mock.patch.object(B.os, 'fstat', side_effect=zero_size): B.checked(info)

    def test_arguments_require_no_uuid_and_preserve_all_other_tokens(self):
        old = 'console=tty0 psx.diag_uuid=@USB_UUID@ amdgpu.ps4_uvd_runtime=1 libata.force=1:1.5Gbps'
        new = B.render_bootargs(old)
        self.assertEqual(set(new.split()), {'console=tty0', 'amdgpu.ps4_uvd_runtime=1',
                                           'libata.force=1:1.5Gbps', 'psx.boot=ram'})
        self.assertNotIn('UUID', new)
        for value in (old.replace('psx.diag_uuid=@USB_UUID@', ''), old + ' psx.diag_uuid=@USB_UUID@',
                      old + ' psx.boot=ram', old + ' x=' + 'a' * 512):
            with self.assertRaises(ValueError): B.render_bootargs(value)

    def test_wrong_frozen_source_fails_before_output_creation(self):
        root = self.directory(); source = root / 'source'; source.mkdir()
        (source / 'SHA256SUMS').write_bytes(b'synthetic pinned inventory\n')
        base = dict(B.PINS, profile='ps4-baikal-diagnostics-uvd-alpha7', kernel_release=B.RELEASE,
                    rootfs_image_bytes=B.ROOT_BYTES, module_count=41, initramfs_sha256=B.SOURCE_INIT)
        for key in ('rootfs_sha256', 'kernel_sha256', 'modules_sha256'):
            record = dict(base, **{key: '0' * 64}); out = root / key
            stage = types.SimpleNamespace(source_files=lambda path: ({}, record))
            with mock.patch.object(B, 'SOURCE_SUMS', B.sha((source / 'SHA256SUMS').read_bytes())), \
                    mock.patch.object(B, '_load', return_value=stage):
                with self.assertRaisesRegex(ValueError, 'exact finalized clean dev6'): B.build(source, out)
            self.assertFalse(out.exists())

    def test_shutdown_limits_are_written_into_the_ram_copy_of_etc(self):
        text = (REPO / 'rootfs/ps4-diagnostics/init-ram').read_text()
        self.assertLess(text.index('\ninstall_fast_stop\n'), text.index('exec switch_root'))
        functions = text[text.index('write_stop_setting() {'):text.index('install_loader_firmware() {')]
        root = self.directory()
        (root / 'etc/systemd/system').mkdir(parents=True)
        script = 'umask 077\nsay() { echo "$*" >&2; }\n' + functions.replace('/newroot', str(root)) + 'install_fast_stop\n'
        subprocess.run(['/bin/sh', '-c', script], check=True, capture_output=True)
        expected = {'system.conf.d': '[Manager]\nDefaultTimeoutStopSec=5s\n',
                    'user.conf.d': '[Manager]\nDefaultTimeoutStopSec=5s\n',
                    'system/user@.service.d': '[Service]\nTimeoutStopSec=5s\n',
                    'system/poweroff.target.d': '[Unit]\nJobTimeoutSec=20s\n',
                    'system/reboot.target.d': '[Unit]\nJobTimeoutSec=20s\n'}
        for directory, content in expected.items():
            path = root / 'etc/systemd' / directory / '90-psx-fast-stop.conf'
            with self.subTest(directory=directory):
                self.assertEqual(path.read_text(), content)
                self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_shell_and_service_keep_backing_state_and_zero_usb_dependency(self):
        script = REPO / 'rootfs/ps4-diagnostics/init-ram'
        subprocess.run(['/bin/sh', '-n', str(script)], check=True, capture_output=True)
        text = script.read_text()
        self.assertNotIn('/run/psx-media', text)
        self.assertNotIn('mount -t vfat', text)
        self.assertIn('gzip -dc "$compressed" > "$backing"', text)
        self.assertIn('mount --move /run /newroot/run', text)
        self.assertLess(text.index('mount --move /run'), text.index('ram_boot.py verify'))
        unit = (REPO / 'rootfs/ps4-diagnostics-ram/psx-diagnostics-control.service').read_text()
        self.assertIn('--results-dir /run/psx-state/results', unit)
        self.assertNotIn('RuntimeDirectory=psx-state', unit)

    def test_packaged_ssh_guard_initializes_verified_dev6_ram_and_rejects_wrong_kernel(self):
        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            return module
        ssh_fixture = load('ram_ssh_fixture', REPO / 'tests/test_ps4_diagnostics_ssh_access.py')
        ram_fixture = load('ram_state_fixture', REPO / 'tests/test_ps4_diagnostics_ram_state.py')
        fixture = ssh_fixture.SSHAccessTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        _, _, write = ram_fixture.install_ram_fixture(fixture.root)
        mount = fixture.path(f'/proc/{os.getpid()}/mountinfo')
        with mount.open('a') as stream:
            for number, name in enumerate(('etc', 'root', 'run'), 10):
                stream.write(f'{number} 1 0:2 /{name} /{name} rw - tmpfs tmpfs rw\n')
        source = (REPO / 'diagnostics/ps4/ssh_access.py').read_bytes()
        packaged = B.package_ssh_access(source)
        self.assertIn(b'RELEASE = "' + B.RELEASE.encode() + b'"', packaged)
        self.assertEqual(B.package_ssh_access(packaged), packaged)
        self.assertIn(b'!= RELEASE:', packaged)
        for invalid in (source.replace(b'native-dev3', b'unknown'), source + b'\nRELEASE = "other"\n'):
            with self.assertRaisesRegex(ValueError, 'unrecognized'): B.package_ssh_access(invalid)
        path = self.directory() / 'ssh_access.py'; path.write_bytes(packaged)
        helper = load('packaged_ram_ssh_access', path)
        fixture.commands.clear(); fixture.active = False
        access = helper.SSHAccess(fixture.root, executor=fixture.execute, clock=lambda: fixture.now)
        result = access.initialize()
        self.assertFalse(result['password_enabled'])
        self.assertTrue(result['github_available'])
        self.assertTrue(fixture.active)
        self.assertIn(fixture.owner, fixture.path('/run/psx-state/state/ssh/authorized_keys').read_text())
        self.assertTrue(any(Path(argv[0]).name == 'sshd' and '-T' in argv for argv, _ in fixture.commands))
        write('/proc/sys/kernel/osrelease', 'wrong-kernel')
        fixture.commands.clear()
        with self.assertRaisesRegex(helper.AccessError, 'selected diagnostic kernel'): access.initialize()
        self.assertEqual(fixture.commands, [])

    def test_packaged_network_actions_use_dev6_without_bypassing_ethernet_guards(self):
        spec = importlib.util.spec_from_file_location('ram_network_fixture', REPO / 'tests/test_ps4_diagnostics_network.py')
        fixtures = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixtures)
        fixture = fixtures.NetworkTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        source = (REPO / 'diagnostics/ps4/network.py').read_bytes()
        packaged = B.package_network(source)
        self.assertIn(b'RELEASE = "' + B.RELEASE.encode() + b'"', packaged)
        self.assertEqual(B.package_network(packaged), packaged)
        self.assertIn(b'!= RELEASE:', packaged)
        with self.assertRaisesRegex(ValueError, 'unrecognized'):
            B.package_network(source.replace(b'native-dev3', b'unknown'))
        path = self.directory() / 'network.py'; path.write_bytes(packaged)
        helper = fixtures.load('packaged_ram_network', path)
        fixture.write('/proc/sys/kernel/osrelease', B.RELEASE)
        fixture.aps = 'TestWiFi:90:WPA2:\n'
        network = helper.Network(fixture.root, fixture.execute)
        self.assertTrue(network.wifi_scan()['wifi']['access_points'])
        secret = 'synthetic-passphrase'
        result = network.wifi_connect('TestWiFi', secret)
        self.assertTrue(result['ok']); self.assertFalse(result['credentials_persisted'])
        self.assertEqual(fixture.secret_file.stat().st_mode & 0o777, 0o600)
        self.assertTrue(fixture.secret_file.is_relative_to(fixture.path('/run/NetworkManager/system-connections')))
        self.assertNotIn(secret, repr(fixture.calls)); self.assertNotIn(secret, json.dumps(result))
        network.wifi_disconnect()
        fixture.calls.clear()
        fixture.set_mts(bound=1, attempted=1, published=1, engine_live=1,
                        admin_up=1, terminal_fault=1, error=0)
        for operation in (network.ethernet_connect, network.ethernet_disconnect):
            with self.assertRaises(helper.NetworkError): operation()
        self.assertEqual(fixture.calls, [])
        fixture.write('/proc/sys/kernel/osrelease', 'wrong-kernel')
        for operation in (network.wifi_scan, lambda: network.wifi_connect('TestWiFi', secret),
                          network.wifi_disconnect, network.ethernet_connect):
            with self.assertRaises(helper.NetworkError) as caught: operation()
            self.assertEqual(caught.exception.code, 'WRONG_KERNEL')
        self.assertEqual(fixture.calls, [])

    def test_host_key_service_is_required_hashed_bound_overlay_with_boot_ordering(self):
        name = 'psx-ssh-host-key.service'
        target, mode = B.EXTRA_TARGETS[name]
        self.assertEqual(target, '/usr/lib/systemd/system/psx-ssh-host-key.service')
        self.assertEqual(mode, 0o644)
        data = (REPO / 'rootfs/ps4-diagnostics-gui' / name).read_bytes()
        self.assertIn(b'After=local-fs.target\n', data)
        self.assertIn(b'Before=sshd.service psx-diagnostics-control.service\n', data)
        init = (REPO / 'rootfs/ps4-diagnostics/init-ram').read_text()
        self.assertIn('psx-ssh-host-key.service) bit=256; wanted=0644; target=' + target, init)
        self.assertIn('[ "$seen" -eq 511 ]', init)
        manifest = self.contract()
        record = next(item for item in manifest['ram_assets']['files'] if item['name'] == name)
        record.update(bytes=len(data), sha256=B.sha(data))
        B.validate_manifest(manifest)
        missing = copy.deepcopy(manifest)
        missing['ram_assets']['files'] = [item for item in missing['ram_assets']['files'] if item['name'] != name]
        with self.assertRaisesRegex(ValueError, 'aliases/targets'): B.validate_manifest(missing)
        root = self.directory()
        for path in (root / 'run/psx-ram' / name, root / target.lstrip('/')):
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); path.chmod(mode)
        B._verify_asset(root, name, record, '/run/psx-ram')
        (root / target.lstrip('/')).write_bytes(data.replace(b'Before=', b'#efore='))
        with self.assertRaisesRegex(ValueError, 'checksum'): B._verify_asset(root, name, record, '/run/psx-ram')

    def test_runtime_proof_checks_actual_files_and_preserves_volatile_state(self):
        root = self.directory(); payload = b'bounded synthetic ext4 contents\n'
        def put(name, data, mode=0o644):
            p = root / name.lstrip('/'); p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data); p.chmod(mode); return p
        with mock.patch.object(B, 'ROOT_BYTES', len(payload)), mock.patch.dict(B.PINS, rootfs_sha256=B.sha(payload)):
            value = self.contract()
            put(B.BACKING, payload, 0o400)
            for name in ('/run/psx-image', '/run/psx-state', '/run/psx-ram'):
                (root / name.lstrip('/')).mkdir(parents=True, exist_ok=True)
                (root / name.lstrip('/')).chmod(0o700)
            put('/proc/sys/kernel/osrelease', (B.RELEASE + '\n').encode(), 0o444)
            boot = '453b7111-0eab-4474-95a3-4f845cffc21b'
            put('/proc/sys/kernel/random/boot_id', (boot + '\n').encode(), 0o444)
            put('/proc/cmdline', ('psx.boot=ram psx_mts_net.autoarm=1 ' + ' '.join(value['required_boot_arguments'])).encode(), 0o444)
            for argument in value['required_boot_arguments']:
                put('/sys/module/amdgpu/parameters/' + argument.split('=')[0].split('.')[1], b'Y\n', 0o444)
            for name, data in (('ro', b'1\n'), ('loop/backing_file', (B.BACKING + '\n').encode()), ('loop/offset', b'0\n')):
                put('/sys/class/block/loop0/' + name, data, 0o444)
            value['module_files'] = []
            for n in range(41):
                name = '/usr/lib/modules/' + B.RELEASE + f'/kernel/module-{n}.ko'
                data = f'pinned module {n}'.encode(); put(name, data)
                value['module_files'].append({'path': name, 'bytes': len(data), 'sha256': B.sha(data)})
            for key, base in (('frontend_overrides', '/run/psx-frontend'), ('ram_assets', '/run/psx-ram')):
                for record in value[key]['files']:
                    put(base + '/' + record['name'], b'x', int(record['mode'], 8))
                    if record['bind_target']: put(record['bind_target'], b'x', int(record['mode'], 8))
            data_files = []; pins = {}
            for name in B.NATIVE_FILES:
                data = ('reference ' + name).encode(); pins[name] = B.sha(data)
                path = 'fixtures/native-video/' + name
                put('/run/psx-assets/' + path, data)
                data_files.append({'path': path, 'bytes': len(data), 'sha256': pins[name], 'mode': '0644'})
            value['data_assets'] = {'runtime_directory': '/run/psx-assets', 'maximum_bytes': 64 * 1024**2, 'files': data_files}
            info = (root / 'run/psx-state').stat(); dev = f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
            rows = ['1 0 7:0 / / ro - ext4 /dev/loop0 ro',
                    f'2 1 {dev} / /run/psx-image rw - tmpfs tmpfs rw',
                    f'3 1 {dev} / /run/psx-state rw - tmpfs tmpfs rw',
                    f'4 1 {dev} / /run/psx-assets ro - tmpfs tmpfs ro']
            put(f'/proc/{os.getpid()}/mountinfo', ('\n'.join(rows) + '\n').encode(), 0o444)
            put(B.EXPECTED, B.encoded(value), 0o600)
            sentinel = put('/run/psx-state/results/prior-report.json', b'preserve across service restarts\n', 0o600)
            with mock.patch.dict(B.NATIVE_FILES, pins, clear=True):
                proof = B.verify(root)
                self.assertEqual(proof['module_count'], 41)
                self.assertEqual(proof['manifest_sha256'], B.sha(B.encoded(value)))
                final = json.loads((root / B.MANIFEST.lstrip('/')).read_text())
                self.assertEqual(set(final), set(value) | {'manifest_validation'})
                self.assertNotIn('observed_media_initramfs_sha256', final['manifest_validation'])
                self.assertFalse(proof['state']['persistent'])
                B.verify(root)
                self.assertEqual(sentinel.read_bytes(), b'preserve across service restarts\n')
                broken = root / value['module_files'][0]['path'].lstrip('/')
                broken.write_bytes(b'corrupt module')
                with self.assertRaisesRegex(ValueError, 'checksum'): B.verify(root)


if __name__ == '__main__':
    unittest.main()
