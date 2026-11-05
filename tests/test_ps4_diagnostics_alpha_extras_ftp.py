"""Synthetic optional-extras FTP staging; no network, devices or decoder calls."""
import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / relative))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    return module


APP = load('alpha_extras_ftp_tests', 'scripts/stage-ps4-diagnostics-alpha-extras-ftp')
BASE = load('alpha_extras_fake_ftp', 'tests/test_ps4_diagnostics_ftp.py')
U = load('alpha_extras_uvd_test', 'diagnostics/ps4/uvd.py')
S = load('alpha_extras_native_test', 'diagnostics/ps4/uvd_suite.py')
USB = BASE.USB


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ExtrasTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/var/tmp'); self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name); self.source = self.temp / 'source'; self.source.mkdir()
        meta = dict(profile=APP.PROFILE, kernel_release=APP.RELEASE, kernel_sha256=sha(b'kernel'),
                    rootfs_sha256=sha(b'root'), initramfs_sha256=sha(b'init'))
        self.core = {'bzImage': b'kernel', 'initramfs.cpio.gz': b'init',
                     'psx-diagnostics/rootfs.ext4': b'root', APP.META: json.dumps(meta).encode()}
        metadata = self.source / 'boot-set' / APP.META
        metadata.parent.mkdir(parents=True); metadata.write_bytes(self.core[APP.META])
        config = dict(schema_version=1, provider_manifest_sha256=APP.PROVIDER_JSON)
        manifest = dict(image=APP.IMAGE, image_bytes=12, image_sha256=sha(b'image-fixture'),
            kernel_release=APP.RELEASE, kernel_sha256=meta['kernel_sha256'],
            base_rootfs_sha256=meta['rootfs_sha256'], hardware_acceptance=False,
            provider_manifest_sha256=APP.PROVIDER_JSON, launcher_config=config)
        self.payload = {APP.IMAGE: b'image-fixture', 'chromium-provider.json': json.dumps(config).encode(),
                        'provider-manifest.json': json.dumps(manifest).encode()}
        manifest['image_bytes'] = len(self.payload[APP.IMAGE])
        self.payload['provider-manifest.json'] = json.dumps(manifest).encode()
        for name, data in self.payload.items(): (self.source / name).write_bytes(data)
        self.corepins = {name: sha(data) for name, data in self.core.items()}
        self.pins = {name: (len(data), sha(data)) for name, data in self.payload.items()}
        for patch in (mock.patch.dict(APP.CORE, self.corepins, clear=True),
                      mock.patch.dict(APP.PINS, self.pins, clear=True)):
            patch.start(); self.addCleanup(patch.stop)
        self.receipt = dict(owner='psx-diagnostics-usb-v1', verified_readback=True, hardware_acceptance=False,
            target=dict(serial='fixture-serial', bytes=16000000000, uuid='1234-ABCD'), files=self.corepins)
        self.remote = {USB + '/' + name: data for name, data in self.core.items()}
        self.remote.update({USB + '/psx-diagnostics/installed.json': json.dumps(self.receipt).encode(),
            USB + '/psx-diagnostics/USB-SHA256SUMS': APP.DEPLOY.STAGE.installed_sums(self.corepins).encode(),
            USB + '/psx-diagnostics/state/ssh/owner': b'unchanged-owner',
            USB + '/psx-diagnostics/results/old.json': b'unchanged-report',
            USB + '/psx-diagnostics/providers/uvd-alpha4/old.ext4': b'unchanged-older-provider'})
        self.ftp = BASE.FakeFTP(self.remote)
        self.args = SimpleNamespace(source=self.source, native_fixtures=None, host='fixture.invalid', port=2121,
            remote_root=USB, expect_serial='fixture-serial', expect_bytes=16000000000, expect_uuid='1234-ABCD',
            apply=False, receipt=self.temp / 'receipt.json')

    def run_app(self, apply=False):
        self.args.apply = apply
        with mock.patch.object(APP.ftplib, 'FTP', return_value=self.ftp), contextlib.redirect_stdout(io.StringIO()):
            return APP.run(self.args)

    def assert_old_unchanged(self):
        self.assertEqual({name: self.ftp.files.get(name) for name in self.remote}, self.remote)
        self.assertFalse(any(value[0] == 'delete' for value in self.ftp.mutations))

    def test_default_plan_has_no_remote_mutations_and_records_scope(self):
        result = self.run_app()
        self.assertEqual(result['status'], 'planned'); self.assertFalse(result['apply'])
        self.assertFalse(self.ftp.mutations); self.assertEqual(self.ftp.files, self.remote)
        plan = next(value for value in result['events'] if value['event'] == 'plan')
        self.assertIn('no second root download', plan['root_verification'])
        self.assertEqual(len(plan['expected_files']), 3)
        self.assertEqual(self.args.receipt.stat().st_mode & 0o777, 0o600)

    def test_success_publishes_only_three_files_manifest_last_and_is_idempotent(self):
        self.assertEqual(self.run_app(True)['status'], 'completed')
        for name, payload in self.payload.items():
            self.assertEqual(self.ftp.files[USB + '/' + APP.NAMESPACE + '/' + name], payload)
        renames = [value for value in self.ftp.mutations if value[0] == 'rename']
        self.assertTrue(renames[-1][2].endswith('/provider-manifest.json'))
        self.assertFalse(any(name.endswith('.partial') for name in self.ftp.files))
        self.assert_old_unchanged()
        self.ftp.mutations.clear(); self.args.receipt = self.temp / 'repeat.json'
        result = self.run_app(True)
        self.assertEqual(result['status'], 'completed'); self.assertFalse(self.ftp.mutations)

    def test_identity_core_bytes_and_metadata_drift_refuse_before_writes(self):
        for fault in ('serial', 'root_pin', 'kernel_bytes', 'metadata_bytes'):
            with self.subTest(fault=fault):
                self.ftp = BASE.FakeFTP(self.remote); self.args.receipt = self.temp / (fault + '.json')
                if fault == 'serial': self.args.expect_serial = 'different'
                if fault == 'root_pin':
                    receipt = copy.deepcopy(self.receipt); receipt['files']['psx-diagnostics/rootfs.ext4'] = 'a' * 64
                    self.ftp.files[USB + '/psx-diagnostics/installed.json'] = json.dumps(receipt).encode()
                    self.ftp.files[USB + '/psx-diagnostics/USB-SHA256SUMS'] = APP.DEPLOY.STAGE.installed_sums(receipt['files']).encode()
                if fault == 'kernel_bytes': self.ftp.files[USB + '/bzImage'] = b'wrong'
                if fault == 'metadata_bytes': self.ftp.files[USB + '/' + APP.META] += b' '
                with self.assertRaises(ValueError): self.run_app(True)
                self.assertFalse(self.ftp.mutations); self.args.expect_serial = 'fixture-serial'

    def test_existing_different_file_refuses_before_any_upload(self):
        target = USB + '/' + APP.NAMESPACE + '/provider-manifest.json'
        self.ftp = BASE.FakeFTP(dict(self.remote, **{target: b'different'}))
        with self.assertRaises(ValueError): self.run_app(True)
        self.assertFalse(self.ftp.mutations); self.assertEqual(self.ftp.files[target], b'different')

    def test_failed_or_corrupt_transfer_keeps_partial_and_never_publishes(self):
        for fault in ('failed', 'corrupt'):
            self.ftp = BASE.FakeFTP(self.remote); self.args.receipt = self.temp / (fault + '.json')
            original = self.ftp.storbinary
            def broken(command, stream, **kwargs):
                if fault == 'failed':
                    self.ftp.files[command.removeprefix('STOR ')] = b'partial'
                    raise OSError('synthetic interrupted upload')
                result = original(command, stream, **kwargs)
                self.ftp.files[command.removeprefix('STOR ')] = b'corrupt'
                return result
            with mock.patch.object(self.ftp, 'storbinary', side_effect=broken), self.assertRaises((ValueError, OSError)):
                self.run_app(True)
            self.assertFalse(any(value[0] == 'rename' for value in self.ftp.mutations))
            self.assertTrue(any(name.endswith('.partial') for name in self.ftp.files))
            result = json.loads(self.args.receipt.read_text())
            self.assertEqual(result['status'], 'failed_partials_retained_no_retry')
            self.assertTrue(any(value.get('partial', '').endswith('.partial') for value in result['events']))
            self.assert_old_unchanged()

    def test_lost_rename_ack_retains_published_bytes_and_other_partials_without_retry(self):
        self.ftp.fail_rename = (USB + '/' + APP.NAMESPACE + '/' + APP.IMAGE, 'disconnect')
        with self.assertRaises(EOFError): self.run_app(True)
        self.assertEqual(len([value for value in self.ftp.mutations if value[0] == 'rename']), 1)
        self.assertEqual(self.ftp.files[USB + '/' + APP.NAMESPACE + '/' + APP.IMAGE], self.payload[APP.IMAGE])
        self.assertEqual(len([name for name in self.ftp.files if name.endswith('.partial')]), 2)
        self.assert_old_unchanged()

    def test_core_drift_after_staging_prevents_publication(self):
        original = self.ftp.storbinary
        def drift(*args, **kwargs):
            result = original(*args, **kwargs)
            self.ftp.files[USB + '/psx-diagnostics/installed.json'] += b' '
            return result
        with mock.patch.object(self.ftp, 'storbinary', side_effect=drift), self.assertRaisesRegex(ValueError, 'changed during'):
            self.run_app(True)
        self.assertFalse(any(value[0] == 'rename' for value in self.ftp.mutations))
        self.assertEqual(len([name for name in self.ftp.files if name.endswith('.partial')]), 3)

    def test_local_tamper_or_symlink_refuses_before_connect(self):
        path = self.source / APP.IMAGE
        path.write_bytes(b'wrong bytes')
        with mock.patch.object(APP.ftplib, 'FTP') as network, contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            APP.run(self.args)
        network.assert_not_called()
        path.unlink(); path.symlink_to(self.source / 'chromium-provider.json')
        self.args.receipt = self.temp / 'symlink.json'
        with self.assertRaisesRegex(ValueError, 'symlink'): APP.local_inputs(self.source)

    def test_different_final_appearing_after_upload_is_never_overwritten(self):
        original = self.ftp.storbinary
        target = USB + '/' + APP.NAMESPACE + '/' + APP.IMAGE
        def appeared(*args, **kwargs):
            result = original(*args, **kwargs)
            self.ftp.files[target] = b'other-publisher'
            return result
        with mock.patch.object(self.ftp, 'storbinary', side_effect=appeared), self.assertRaises(ValueError):
            self.run_app(True)
        self.assertEqual(self.ftp.files[target], b'other-publisher')
        self.assertFalse(any(value[0] == 'rename' for value in self.ftp.mutations))
        self.assertEqual(len([name for name in self.ftp.files if name.endswith('.partial')]), 3)

    def native_bundle(self):
        bundle = self.temp / 'native'; bundle.mkdir()
        contracts = copy.deepcopy(S.NATIVE)
        for scenario, contract in contracts.items():
            directory = bundle / contract['directory']; directory.mkdir()
            clip = b'Never executed ' + scenario.encode(); md5 = b''.join(
                f'0, {index}, {index}, 1, 6, '.encode() + b'a' * 32 + b'\n' for index in range(900))
            contract.update(width=2, height=2, clip_bytes=len(clip), clip_sha256=sha(clip), framemd5_sha256=sha(md5))
            fixture = dict(schema='psx-uvd-playback-fixture-v1', fixture_profile=contract['fixture_profile'],
                width=2, height=2, frame_count=900, frame_rate='30/1', duration_seconds=30, frame_bytes=6,
                codec='h264', container='h264-annex-b', pixel_format='nv12', profile='Constrained Baseline',
                level=contract['level'], time_base='1/30000', stream_duration_ts=900000, hardware_acceptance=False,
                reference=dict(bytes=5400, sha256=contract['nv12_sha256']),
                clip=dict(path='clip.h264', bytes=len(clip), sha256=sha(clip)),
                encoding=dict(bframes=0, cabac=0, gop=60, level=contract['level'], profile='Constrained Baseline', ref=1, software_threads=2),
                headers=dict(constraint_set1_flag=1, entropy_coding_mode_flag=0, frame_mbs_only_flag=1,
                    level_idc=contract['level'], max_num_ref_frames=1, num_slice_groups_minus1=0, profile_idc=66),
                frame_pts=list(range(0, 900000, 1000)), frame_pts_us=[(i * 1000000 + 15) // 30 for i in range(900)],
                frames=[dict(index=i, bytes=6, sha256='b' * 64) for i in range(900)])
            raw = json.dumps(fixture).encode(); contract['fixture_sha256'] = sha(raw)
            for name, value in [('clip.h264', clip), ('reference-nv12.framemd5', md5), ('FIXTURE.json', raw)]:
                (directory / name).write_bytes(value)
        return bundle, contracts

    def test_optional_six_fixtures_use_real_validator_and_fixed_destinations(self):
        bundle, contracts = self.native_bundle(); self.args.native_fixtures = bundle
        def modules(name, relative):
            return U if relative.endswith('/uvd.py') else S
        with mock.patch.object(APP, 'load', side_effect=modules), mock.patch.dict(S.NATIVE, contracts, clear=True), \
                mock.patch.object(S, 'run', side_effect=AssertionError('decoder must never run')):
            result = self.run_app(True)
            self.assertEqual(result['status'], 'completed')
            extra = set(self.ftp.files) - set(self.remote)
            self.assertEqual(len(extra), 9)
            for resolution in ('720p30', '1080p30'):
                for name in ('clip.h264', 'reference-nv12.framemd5', 'FIXTURE.json'):
                    self.assertIn(USB + '/psx-diagnostics/fixtures/native-video/' + resolution + '/' + name, extra)
            self.assert_old_unchanged()
            (bundle / '720p30/clip.h264').write_bytes(b'bad')
            with self.assertRaises(ValueError): APP.local_inputs(self.source, bundle)


if __name__ == '__main__':
    unittest.main()
