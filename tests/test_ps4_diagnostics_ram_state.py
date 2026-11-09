# SPDX-License-Identifier: MIT
"""RAM backend tests use synthetic private files; no mount, network or reboot."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'diagnostics/ps4'))
import ram_state as RAM
import uvd as UVD
import preferences as PREFS
import ssh_access as SSH


def install_ram_fixture(root):
    def write(name, data, mode=0o644):
        p = root / name.lstrip('/')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
        p.chmod(mode)
        return p
    for name in (RAM.STATE, '/run/psx-image', '/run/psx-ram'):
        (root / name.lstrip('/')).mkdir(mode=0o700, parents=True, exist_ok=True)
        (root / name.lstrip('/')).chmod(0o700)
    state = {'kind': 'volatile-tmpfs', 'path': RAM.STATE,
             'results_dir': RAM.STATE + '/results', 'state_dir': RAM.STATE + '/state',
             'maximum_bytes': 128 * 1024**2, 'persistent': False, 'survives_controller_restart': True}
    manifest = dict(copy.deepcopy(UVD.ALPHA7), profile=RAM.PROFILE, image_version='ram-alpha1',
                    rootfs_sha256='34' * 32, rootfs_image_bytes=1000 * 1024**2,
                    module_count=41, frontend_overrides={'files': [{'name': 'fixture', 'sha256': '56' * 32}]},
                    boot_source={'kind': 'embedded-ram', 'backing_path': RAM.BACKING}, state=state,
                    hardware_acceptance=False, required_boot_arguments=list(UVD.POWEROFF_BOOT_ARGUMENTS))
    raw = (json.dumps(manifest, sort_keys=True) + '\n').encode()
    digest = hashlib.sha256(raw).hexdigest()
    write(RAM.EXPECTED, raw)
    validation = {'embedded_root_contract_verified': True, 'ram_boot_contract_verified': True,
                  'expected_contract_sha256': digest, 'observed_kernel_release': RAM.RELEASE}
    write(RAM.MANIFEST, json.dumps(dict(manifest, manifest_validation=validation)))
    boot_id = '797d5098-7d56-41ee-b7e4-ab54d84eadab'
    write('/proc/sys/kernel/random/boot_id', boot_id)
    write('/proc/sys/kernel/osrelease', RAM.RELEASE)
    write('/proc/cmdline', ' '.join(UVD.POWEROFF_BOOT_ARGUMENTS) + ' psx.boot=ram')
    for argument in UVD.POWEROFF_BOOT_ARGUMENTS:
        write('/sys/module/amdgpu/parameters/' + argument.split('=')[0].split('.')[1], 'Y')
    proof = {k: manifest[k] for k in ('profile', 'kernel_release', *RAM.PINS,
             'rootfs_sha256', 'rootfs_image_bytes', 'frontend_overrides', 'boot_source', 'state')}
    proof.update(schema_version=1, kind='psx-diagnostix-ram-boot-v1', boot_id=boot_id,
                 manifest_sha256=digest, module_count=41, hardware_acceptance=False)
    write(RAM.PROOF, json.dumps(proof), 0o600)
    dev = root.stat().st_dev
    device = f'{os.major(dev)}:{os.minor(dev)}'
    write(f'/proc/{os.getpid()}/mountinfo',
          '1 0 7:0 / / ro - ext4 /dev/loop0 ro\n'
          f'2 1 {device} / /run/psx-state rw - tmpfs tmpfs rw\n'
          f'3 1 {device} / /run/psx-image rw - tmpfs tmpfs rw\n')
    backing = root / RAM.BACKING.lstrip('/')
    with backing.open('wb') as f:
        f.truncate(manifest['rootfs_image_bytes'])
    backing.chmod(0o400)
    return manifest, proof, write


class RamStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/var/tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest, self.proof, self.write = install_ram_fixture(self.root)

    def test_exact_ram_boot_needs_no_usb_receipt_or_uuid(self):
        self.assertTrue(RAM.active(self.root))
        self.assertEqual(RAM.validate(self.root, fixture=True), self.proof)
        self.assertFalse((self.root / 'run/psx-media').exists())

    def test_stale_boot_and_wrong_running_release_are_refused(self):
        for name, value in [('/proc/sys/kernel/random/boot_id', '12345678-1234-1234-1234-123456789abc'),
                            ('/proc/sys/kernel/osrelease', 'different-kernel')]:
            path = self.root / name.lstrip('/'); old = path.read_bytes()
            self.write(name, value)
            with self.assertRaises(ValueError): RAM.validate(self.root, fixture=True)
            path.write_bytes(old)

    def test_changed_proof_root_and_frontend_are_refused(self):
        for key, value in [('manifest_sha256', '00' * 32), ('rootfs_sha256', '00' * 32),
                           ('frontend_overrides', {}), ('module_count', 40), ('hardware_acceptance', True)]:
            proof = dict(self.proof, **{key: value})
            self.write(RAM.PROOF, json.dumps(proof), 0o600)
            with self.assertRaises(ValueError): RAM.validate(self.root, fixture=True)
        self.write(RAM.PROOF, json.dumps(self.proof), 0o600)

    def test_mismatched_embedded_and_final_contract_or_fake_media_proof_is_refused(self):
        path = self.root / RAM.MANIFEST.lstrip('/'); original = json.loads(path.read_text())
        for key in ('kernel_sha256', 'config_sha256', 'modules_sha256', 'rootfs_sha256'):
            value = copy.deepcopy(original); value[key] = '00' * 32
            self.write(RAM.MANIFEST, json.dumps(value))
            with self.assertRaises(ValueError): RAM.validate(self.root, fixture=True)
        value = copy.deepcopy(original)
        value['manifest_validation']['observed_media_initramfs_sha256'] = '12' * 32
        self.write(RAM.MANIFEST, json.dumps(value))
        with self.assertRaises(ValueError): RAM.validate(self.root, fixture=True)

    def test_private_proof_backing_mode_size_and_mount_class_are_required(self):
        path = self.root / RAM.PROOF.lstrip('/'); path.chmod(0o644)
        with self.assertRaises(ValueError): RAM.validate(self.root, fixture=True)
        path.chmod(0o600)
        backing = self.root / RAM.BACKING.lstrip('/'); backing.chmod(0o600)
        with self.assertRaises(ValueError): RAM.validate(self.root, fixture=True)
        backing.chmod(0o400)
        mount = self.root / f'proc/{os.getpid()}/mountinfo'; old = mount.read_text()
        mount.write_text(old.replace('- tmpfs tmpfs rw', '- vfat /dev/fixture rw', 1))
        with self.assertRaises(ValueError): RAM.validate(self.root, fixture=True)

    def test_symlinked_boot_proof_is_refused(self):
        proof = self.root / RAM.PROOF.lstrip('/'); target = proof.with_suffix('.old')
        proof.rename(target); proof.symlink_to(target)
        with self.assertRaises(ValueError): RAM.validate(self.root, fixture=True)

    def test_unrelated_systemd_automount_stack_does_not_invalidate_ram_proof(self):
        mount = self.root / f'proc/{os.getpid()}/mountinfo'
        with mount.open('a') as stream:
            stream.write('4 1 0:38 / /proc/sys/fs/binfmt_misc rw - autofs systemd-1 rw\n'
                         '5 4 0:59 / /proc/sys/fs/binfmt_misc rw - binfmt_misc binfmt_misc rw\n')
        self.assertEqual(RAM.validate(self.root, fixture=True), self.proof)

    def test_each_guarded_mount_still_rejects_overmount_ambiguity(self):
        mount = self.root / f'proc/{os.getpid()}/mountinfo'
        original = mount.read_text()
        for target in ('/', RAM.STATE, '/run/psx-image'):
            duplicate = next(row for row in original.splitlines() if row.split()[4] == target)
            mount.write_text(original + duplicate + '\n')
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'Ambiguous RAM mount identity'):
                RAM.validate(self.root, fixture=True)
        mount.write_text(original)

    def test_settings_use_verified_volatile_state_without_usb(self):
        store = PREFS.DisplayPreferences(self.root)
        self.assertTrue(store.ram)
        result = store.set({'font_size': 14})
        self.assertEqual(store.load()['font_size'], 14)
        self.assertFalse(result['settings']['persistent'])
        self.assertEqual(store.path, self.root / 'run/psx-state/state/display.json')
        self.assertFalse((self.root / 'run/psx-media').exists())

    def test_ssh_media_backend_requires_same_boot_proof_and_keeps_volatile_keys(self):
        access = SSH.SSHAccess(self.root)
        self.assertEqual(access.media(), self.root.stat().st_dev)
        self.assertEqual(access.persistent_path(), '/run/psx-state/state/ssh/authorized_keys')
        self.write('/etc/psx-diagnostics/authorized_keys', '')
        key = 'ssh-ed25519 synthetic-public-fixture'
        merged = access.merge({key: 'SHA256:fixture'})
        self.assertIn(key, merged)
        self.assertIn(key, (self.root / 'run/psx-state/state/ssh/authorized_keys').read_text())
        self.assertFalse((self.root / 'run/psx-media').exists())
        self.write('/proc/sys/kernel/random/boot_id', '12345678-1234-1234-1234-123456789abc')
        with self.assertRaises(ValueError): access.media()

    def test_uvd_ram_profile_preserves_exact_frozen_policies(self):
        selected = UVD.select_contract(self.manifest)
        self.assertEqual(selected['kernel_sha256'], UVD.ALPHA7['kernel_sha256'])
        changed = copy.deepcopy(self.manifest)
        changed['uvd_restart_experiment']['retain_dma'] = False
        with self.assertRaises(ValueError): UVD.select_contract(changed)

    def test_immediate_power_uses_verified_ram_proof_without_usb_receipt(self):
        spec = importlib.util.spec_from_file_location('ram_power_base', ROOT / 'tests/test_ps4_diagnostics_power_actions.py')
        base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)
        fixture = base.PowerActionsTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        manifest, _, _ = install_ram_fixture(fixture.fixture)
        # A normal systemd autofs/activated-filesystem stack must not disable
        # either power operation after initramfs verification has succeeded.
        mount = fixture.fixture / f'proc/{os.getpid()}/mountinfo'
        with mount.open('a') as stream:
            stream.write('4 1 0:38 / /proc/sys/fs/binfmt_misc rw - autofs systemd-1 rw\n'
                         '5 4 0:59 / /proc/sys/fs/binfmt_misc rw - binfmt_misc binfmt_misc rw\n')
        (fixture.fixture / 'run/psx-media/psx-diagnostics/installed.json').unlink()
        control = fixture.controller
        control.runner.catalogue['expected_kernel_release'] = RAM.RELEASE
        control._cache_power_boot()
        self.assertIsNotNone(control._power_boot)
        self.assertTrue(control.power_status()['shutdown']['available'])
        with mock.patch.object(control, '_power_kernel_call') as kernel:
            with self.assertRaises(ValueError): control.request('power.execute', {'operation': 'shutdown'})
            kernel.assert_not_called()
        proof = fixture.fixture / RAM.PROOF.lstrip('/'); value = json.loads(proof.read_text())
        value['rootfs_sha256'] = '00' * 32; proof.write_text(json.dumps(value)); proof.chmod(0o600)
        control._cache_power_boot(); self.assertIsNone(control._power_boot)
        status = control.power_status()
        self.assertEqual(status['shutdown']['guard_stage'], 'ram-proof')
        self.assertIn('RAM boot proof', status['reboot-orbis']['reason'])
        with mock.patch.object(control, '_power_kernel_call') as kernel:
            with self.assertRaisesRegex(ValueError, 'ram-proof'):
                control.request('power.execute', {'operation': 'shutdown', 'confirm': True})
            kernel.assert_not_called()
