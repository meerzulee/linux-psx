"""Synthetic host boot sets only; no kernel, filesystem, mount or power execution."""
import copy
import gzip
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('psx_power_test_tests', str(ROOT/'scripts/prepare-ps4-diagnostix-power-test'))
spec = importlib.util.spec_from_loader(loader.name, loader)
APP = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = APP
loader.exec_module(APP)
ALPHA = APP.load('psx_power_test_alpha', 'scripts/build-ps4-diagnostics-alpha')
IMAGE = APP.load('psx_power_test_image', 'scripts/build-ps4-diagnostics')
ARCHIVE = APP.load('psx_power_test_archive', 'scripts/ps4-diagnostics-archive')
STAGE = APP.load('psx_power_test_stage', 'scripts/stage-ps4-diagnostics-usb')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def static_elf():
    header = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01'+b'\0'*9,
                         2, 62, 1, 0x400078, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    segment = struct.pack('<IIQQQQQQ', 1, 5, 0, 0x400000, 0, 128, 128, 4096)
    return header + segment + b'\0'*8


class PowerTestDerivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir='/var/tmp', prefix='psx-power-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root/'alpha2'
        self.source.mkdir()
        self.output = self.root/'derived'
        self.profile_path = self.root/'profile.json'
        modules = {'psx_power_alpha': ALPHA, 'psx_power_stage': STAGE,
                   'psx_power_image': IMAGE, 'psx_power_cpio': ARCHIVE}
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(APP, 'load', side_effect=lambda name, _path: modules[name]).start()
        # These fixture assets are explicitly synthetic, not the checksum-pinned
        # upstream ELF/font packages. Keep real fixed-name/mode/size validation.
        mock.patch.object(IMAGE, 'runtime_providers', return_value={}).start()
        self.profile = json.loads((ROOT/'manifests/ps4-diagnostics-uvd-alpha1.json').read_text())
        old_release = self.profile['kernel_release']
        self.profile.update(profile=APP.BASE_PROFILE, kernel_release=APP.RELEASE,
            image_version='alpha2', product_name='Diagnostix', build_status='frozen',
            poweroff_fix={'patch': ALPHA.POWEROFF_PATCH, 'sha256': ALPHA.POWEROFF_SHA256,
                          'uvd_shutdown_park_unchanged': True, 'hardware_acceptance': False})
        self.profile['alpha_inputs']['legacy_package'] = {
            name.replace(old_release, APP.RELEASE).replace('LINK-ATTEMPT-06', 'LINK-ATTEMPT-01'): value
            for name, value in self.profile['alpha_inputs']['legacy_package'].items()}
        for name, field, data in [('bzImage', 'kernel_sha256', b'synthetic kernel fixture'),
                                  ('config', 'config_sha256', b'synthetic config fixture'),
                                  (f'modules-{APP.RELEASE}.tar.zst', 'modules_sha256', b'synthetic complete-module-archive fixture')]:
            self.profile[field] = sha(data)
            self.profile['alpha_inputs']['legacy_package'][name] = {'sha256': sha(data), 'bytes': len(data)}
            path = self.source/('bzImage' if name == 'bzImage' else 'psx-diagnostics/'+name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.profile_path.write_bytes(APP.encoded(self.profile))
        frontend = {name: ('synthetic asset '+name).encode() for name in IMAGE.FRONTEND_FILES}
        for name in ('prepare-console', 'launch-graphics', 'wait-graphics-wm',
                     'prepare-chromium-provider', 'launch-chromium-provider'):
            frontend[name] = b'#!/usr/bin/python3\n# synthetic fixture\n'
        for name in ('network.py', 'controller.py', 'preferences.py', 'ssh_access.py'):
            frontend[name] = b'#!/usr/bin/env python3\n# synthetic fixture\n'
        for name in ('graphics-start', 'graphics-session', 'graphics-window'):
            frontend[name] = b'#!/bin/bash\n# synthetic fixture\n'
        for name in ('start-console', 'graphics-client'):
            frontend[name] = b'#!/bin/sh\n# synthetic fixture\n'
        frontend['psx-diagnostics-tui'] = static_elf()
        frontend['themes.json'] = b'{}\n'
        root = self.source/'psx-diagnostics/rootfs.ext4'
        root.write_bytes(b'synthetic root image bytes; never mounted')
        self.metadata = dict(copy.deepcopy(self.profile), rootfs_file='psx-diagnostics/rootfs.ext4',
            rootfs_sha256=APP.digest(root), rootfs_image_bytes=root.stat().st_size,
            frontend_overrides=IMAGE.frontend_manifest(frontend, self.profile['snapshot_sums_sha256']))
        # Deliberately unlike source_date_epoch: this original env value must survive.
        IMAGE.initramfs(self.metadata, {}, static_elf(), {}, self.metadata['rootfs_sha256'],
            self.source/'initramfs.cpio.gz', 1793500000, expected=self.metadata, frontend=frontend)
        self.arguments = (b'console=tty0  libata.force=1.00:disable psx_mts_net.autoarm=1 '
            b'psx.diag_uuid=@USB_UUID@ rootwait quiet '+b' '.join(key.encode()+b'=1' for key in APP.GATES)+b'\n')
        (self.source/'bootargs.template.txt').write_bytes(self.arguments)
        (self.source/'vram.txt').write_text('1024\n')
        (self.source/'psx-diagnostics/alpha-build-receipt.json').write_bytes(b'{"synthetic":"original preserved receipt"}\n')
        self.publish()

    def publish(self):
        self.metadata['initramfs_sha256'] = APP.digest(self.source/'initramfs.cpio.gz')
        (self.source/'psx-diagnostics/boot-set.json').write_bytes(APP.encoded(self.metadata))
        files = {str(path.relative_to(self.source)): APP.digest(path)
                 for path in self.source.rglob('*') if path.is_file() and path.name != 'SHA256SUMS'}
        (self.source/'SHA256SUMS').write_text(STAGE.installed_sums(files))

    def snapshot(self):
        return {str(path.relative_to(self.source)): (APP.digest(path), stat.S_IMODE(path.stat().st_mode))
                for path in self.source.rglob('*') if path.is_file()}

    def modify_cpio(self, name, *, data=None, mode=None):
        entries, trailer = ARCHIVE.parse_newc(gzip.decompress((self.source/'initramfs.cpio.gz').read_bytes()))
        chunks = []
        for entry in entries:
            if entry.name.decode() != name:
                chunks.append(entry.raw)
            elif data is not None:
                chunks.append(ARCHIVE.entry(50, entry.mode, entry.nlink,
                    self.profile['source_date_epoch'], name, data))
            else:
                raw = bytearray(entry.raw)
                raw[14:22] = f'{mode:08X}'.encode()
                chunks.append(raw)
        (self.source/'initramfs.cpio.gz').write_bytes(gzip.compress(b''.join(chunks)+trailer, mtime=0))
        self.publish()

    def test_real_receipts_cpio_and_every_immutable_payload_survive_derivation(self):
        before = self.snapshot()
        old_files, _ = STAGE.source_files(self.source)
        old_cpio, _ = ARCHIVE.parse_newc(gzip.decompress((self.source/'initramfs.cpio.gz').read_bytes()))
        result = APP.prepare(self.source, self.output, self.profile_path)
        new_files, metadata = STAGE.source_files(self.output)
        self.assertEqual(result['status'], 'PASS')
        self.assertFalse(result['hardware_acceptance'])
        self.assertFalse(result['automatic_power_action'])
        self.assertEqual(metadata['profile'], APP.PROFILE)
        self.assertEqual(metadata['image_version'], 'alpha2-power-test')
        self.assertEqual(metadata['kernel_release'], APP.RELEASE)
        self.assertIs(metadata['uvd_runtime'], False)
        self.assertEqual(metadata['poweroff_fix'], self.profile['poweroff_fix'])
        self.assertEqual(metadata['required_boot_arguments'], APP.DISABLED)
        preflight = metadata['power_test']['required_preflight']
        self.assertFalse(preflight['observed'])
        self.assertEqual(set(preflight['sysfs_required_values']), {
            '/sys/module/amdgpu/parameters/'+key.split('.', 1)[1] for key in APP.GATES})
        self.assertTrue(all(values == ['N', '0'] for values in preflight['sysfs_required_values'].values()))
        self.assertIn('runtime_and_ready', ' '.join(preflight))
        self.assertEqual(set(old_files), set(new_files))
        for name, pin in old_files.items():
            if name not in APP.CHANGED:
                self.assertEqual(new_files[name], pin, name)
                self.assertNotEqual(os.stat(self.source/name).st_ino, os.stat(self.output/name).st_ino, name)
        new_cpio, _ = ARCHIVE.parse_newc(gzip.decompress((self.output/'initramfs.cpio.gz').read_bytes()))
        old = {entry.name: (entry.mode, entry.data) for entry in old_cpio}
        new = {entry.name: (entry.mode, entry.data) for entry in new_cpio}
        receipt_name = b'etc/psx-diagnostics-expected.json'
        external = dict(metadata); external.pop('initramfs_sha256')
        self.assertEqual(json.loads(new[receipt_name][1]), external)
        self.assertEqual({name: value for name, value in old.items() if name != receipt_name},
                         {name: value for name, value in new.items() if name != receipt_name})
        self.assertEqual(sum(name.startswith(b'psx-frontend/') for name in new), 29)
        self.assertEqual(metadata['frontend_overrides'], self.metadata['frontend_overrides'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.output/'bootargs.template.txt').read_bytes(),
                         self.arguments.replace(b'_runtime=1', b'_runtime=0').replace(b'_ready=1', b'_ready=0').replace(b'_poll_state=1', b'_poll_state=0'))

    def test_plan_verifies_without_creating_any_output_or_changing_the_base(self):
        before = self.snapshot()
        with mock.patch.object(IMAGE, 'initramfs', side_effect=AssertionError('plan must not write')):
            result = APP.prepare(self.source, self.output, self.profile_path, plan=True)
        self.assertTrue(result['plan_only'])
        self.assertFalse(self.output.exists())
        self.assertEqual(self.snapshot(), before)

    def test_alpha1_pending_pins_and_changed_park_policy_are_refused_before_output(self):
        bad = [dict(self.profile, profile='ps4-baikal-diagnostics-uvd-alpha1'),
               dict(self.profile, build_status='pending-new-kernel'),
               dict(self.profile, kernel_sha256=None),
               dict(self.profile, poweroff_fix=dict(self.profile['poweroff_fix'], uvd_shutdown_park_unchanged=False))]
        for profile in bad:
            with self.subTest(profile=profile.get('build_status')):
                self.profile_path.write_bytes(APP.encoded(profile))
                with self.assertRaises(ValueError):
                    APP.prepare(self.source, self.output, self.profile_path)
                self.assertFalse(self.output.exists())

    def test_hash_mismatch_missing_modules_or_wrong_external_profile_refuse_output(self):
        for path, replacement in [('bzImage', b'changed'),
                                   (f'psx-diagnostics/modules-{APP.RELEASE}.tar.zst', b'changed')]:
            target = self.source/path
            previous = target.read_bytes(); target.write_bytes(replacement)
            with self.assertRaises(ValueError):
                APP.prepare(self.source, self.output, self.profile_path)
            self.assertFalse(self.output.exists())
            target.write_bytes(previous)
        self.metadata['config_sha256'] = '12'*32
        self.publish()
        with self.assertRaisesRegex(ValueError, 'config_sha256'):
            APP.prepare(self.source, self.output, self.profile_path)
        self.assertFalse(self.output.exists())

    def test_receipt_disagreement_wrong_frontend_mode_and_stale_init_are_refused(self):
        original = (self.source/'initramfs.cpio.gz').read_bytes()
        changes = [('etc/psx-diagnostics-expected.json', {'data': b'{}'}),
                   ('psx-frontend/prepare-chromium-provider', {'mode': stat.S_IFREG | 0o644}),
                   ('init', {'data': b'#!/bin/sh\n# unexpected init\n'})]
        for name, kwargs in changes:
            self.modify_cpio(name, **kwargs)
            with self.assertRaises(ValueError):
                APP.prepare(self.source, self.output, self.profile_path)
            self.assertFalse(self.output.exists())
            (self.source/'initramfs.cpio.gz').write_bytes(original)
            self.publish()

    def test_gate_parser_refuses_duplicates_disabled_values_and_policy_changes(self):
        for bad in [self.arguments+b'amdgpu.ps4_uvd_ready=1',
                    self.arguments.replace(b'_ready=1', b'_ready=0'),
                    self.arguments.replace(b'autoarm=1', b'autoarm=0'),
                    self.arguments.replace(b'1.00:disable', b'1.00:enable'),
                    self.arguments.rstrip()+b' amdgpu.ps4_uvd_unknown=1\n']:
            with self.assertRaises(ValueError):
                APP.bootargs_disabled(bad)

    def test_paths_and_reserve_refuse_overwrite_aliases_or_source_mutation(self):
        before = self.snapshot()
        linked = self.root/'source-link'; linked.symlink_to(self.source, target_is_directory=True)
        for source, output in [(linked, self.output), (self.source, self.source),
                               (self.source, self.source/'nested'), (self.source, Path('/dev/psx-power-test'))]:
            with self.assertRaises(ValueError):
                APP.prepare(source, output, self.profile_path)
        size = sum(path.stat().st_size for path in self.source.rglob('*') if path.is_file())
        with mock.patch.object(APP, 'CAP', size + APP.MIB - 1):
            with self.assertRaisesRegex(ValueError, 'reserve'):
                APP.prepare(self.source, self.output, self.profile_path)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.snapshot(), before)

    def test_final_measured_cap_includes_the_new_receipts_and_one_mib_reserve(self):
        before = self.snapshot()
        result = APP.prepare(self.source, self.output, self.profile_path)
        source_bytes = sum(path.stat().st_size for path in self.source.rglob('*') if path.is_file())
        self.assertGreater(result['boot_set_bytes'], source_bytes)
        with mock.patch.object(APP, 'CAP', result['boot_set_bytes'] + APP.MIB - 1):
            with self.assertRaisesRegex(ValueError, 'core cap plus 1 MiB reserve'):
                APP.prepare(self.source, self.root/'over-budget', self.profile_path)
        self.assertEqual(self.snapshot(), before)

    def test_actual_regenerated_cpio_readback_rejects_an_unexpected_member(self):
        before = self.snapshot()
        original_writer = IMAGE.initramfs
        def inject(*args, **kwargs):
            original_writer(*args, **kwargs)
            path = args[5]
            entries, trailer = ARCHIVE.parse_newc(gzip.decompress(path.read_bytes()))
            raw = b''.join(entry.raw for entry in entries)
            extra = ARCHIVE.entry(999, stat.S_IFREG | 0o644, 1,
                                  self.profile['source_date_epoch'], 'unexpected', b'changed')
            path.write_bytes(gzip.compress(raw+extra+trailer, mtime=0))
        with mock.patch.object(IMAGE, 'initramfs', side_effect=inject):
            with self.assertRaisesRegex(ValueError, 'member set changed'):
                APP.prepare(self.source, self.output, self.profile_path)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
