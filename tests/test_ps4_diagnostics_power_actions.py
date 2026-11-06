# SPDX-License-Identifier: MIT
"""Confirmed power actions; all files, processes and device calls are synthetic."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load('power_controller_tests', ROOT / 'tests/test_ps4_diagnostics_controller.py')
APP = BASE.APP
UVD = load('power_uvd_contract', ROOT / 'diagnostics/ps4/uvd.py')
BOOT = '797d5098-7d56-41ee-b7e4-ab54d84eadab'


class PowerActionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/var/tmp', prefix='psx-power-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = self.root / 'fixture'
        self.manifest = dict(copy.deepcopy(UVD.ALPHA5), initramfs_sha256='12' * 32,
            rootfs_sha256='34' * 32, required_boot_arguments=list(UVD.POWEROFF_BOOT_ARGUMENTS),
            manifest_validation={'embedded_root_contract_verified': True,
                'observed_kernel_release': UVD.ALPHA5['kernel_release'],
                'observed_media_initramfs_sha256': '12' * 32})
        self.write('/proc/sys/kernel/osrelease', self.manifest['kernel_release'])
        self.write('/proc/sys/kernel/random/boot_id', BOOT)
        self.write('/proc/cmdline', ' '.join(UVD.POWEROFF_BOOT_ARGUMENTS))
        for argument in UVD.POWEROFF_BOOT_ARGUMENTS:
            self.write('/sys/module/amdgpu/parameters/' + argument.split('=')[0].split('.')[1], 'Y')
        self.save_manifest()
        self.receipt = {'owner': 'psx-diagnostics-usb-v1', 'verified_readback': True,
            'profile': self.manifest['profile'], 'kernel_release': self.manifest['kernel_release'],
            'files': {name: self.manifest[key] for name, key in (
                ('bzImage', 'kernel_sha256'), ('initramfs.cpio.gz', 'initramfs_sha256'),
                ('psx-diagnostics/rootfs.ext4', 'rootfs_sha256'))}}
        self.save_receipt()
        self.mounts('rw')
        (self.fixture / 'run/psx-diagnostics').mkdir(mode=0o700)
        self.controller = APP.Controller(self.root / 'results', self.fixture, network=BASE.Network())
        self.controller.runner.catalogue['expected_kernel_release'] = self.manifest['kernel_release']
        self.controller.results_dir.mkdir(mode=0o700)
        patch = mock.patch.object(APP.subprocess, 'run', side_effect=AssertionError('live command'))
        self.process = patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(self.controller, '_wayland_states', return_value={
            name: 'active' if name == APP.DIAGNOSTICS_UNIT else 'inactive' for name in APP.WAYLAND_UNITS})
        self.services = patch.start(); self.addCleanup(patch.stop)

    def write(self, name, value):
        path = self.fixture / name.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def save_manifest(self):
        self.write('/etc/psx-diagnostics/boot-set.json', json.dumps(self.manifest))

    def save_receipt(self):
        self.write('/run/psx-media/psx-diagnostics/installed.json', json.dumps(self.receipt))

    def mounts(self, mode):
        self.write('/proc/self/mountinfo',
            '31 1 7:0 / / ro,relatime - ext4 /dev/loop0 ro\n'
            f'32 1 8:1 / /run/psx-media {mode},relatime - vfat /dev/sda1 {mode}\n')

    def report(self, **changes):
        record = dict(run_id='saved-test', test_id='gpu.opengl.render', phase='completed',
            status='PASS', identity=self.controller.runner.identity(), hardware_acceptance=False,
            stop_required=False)
        record.update(changes)
        directory = self.controller.results_dir / record['run_id']
        directory.mkdir(mode=0o700, exist_ok=True)
        APP.diagnostics.atomic_json(directory / 'result.json', record)
        return record

    def uvd_pass(self):
        record = self.report(run_id='saved-uvd', test_id='uvd.native1080', stop_required=True)
        marker = UVD.marker_path(self.controller.runner, BOOT)
        marker.parent.mkdir(mode=0o700)
        APP.diagnostics.atomic_json(marker, {'schema_version': 1, 'boot_id': BOOT,
            'run_id': record['run_id'], 'manifest_sha256': record['identity']['manifest_sha256'],
            'phase': 'reserved', 'status': 'INCONCLUSIVE', 'stop_required': True})
        return marker, record

    def wait(self, job, callback=None):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            record = self.controller.jobs.get(job['id'])
            if record['state'] != 'running' and (callback is None or callback.is_set()):
                return record
            time.sleep(.005)
        self.fail('Synthetic power job did not finish: ' + str(self.controller.jobs.get(job['id'])))

    def test_explicit_confirmation_and_fixed_operation_required_before_any_job(self):
        for params in ({}, {'operation': 'shutdown'}, {'operation': 'shutdown', 'confirm': False},
                       {'operation': 'shutdown', 'confirm': 1}, {'operation': 'reboot', 'confirm': True},
                       {'operation': 'shutdown', 'confirm': True, 'command': 'arbitrary'}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.controller.request('power.execute', params)
        self.assertEqual(self.controller.jobs.list(), [])
        self.assertEqual(list(self.controller.results_dir.iterdir()), [])

    def test_exact_profiles_effective_gates_verified_usb_and_auxiliary_experiment_guard(self):
        self.assertTrue(self.controller.power_status()['shutdown']['available'])
        for kind in ('profile', 'gate', 'duplicate', 'validation', 'root-pin', 'receipt', 'auxiliary'):
            with self.subTest(kind=kind):
                manifest = copy.deepcopy(self.manifest)
                receipt = copy.deepcopy(self.receipt)
                if kind == 'profile': self.manifest['profile'] = UVD.ALPHA3['profile']
                elif kind == 'gate': self.write('/sys/module/amdgpu/parameters/ps4_uvd_poff', 'N')
                elif kind == 'duplicate': self.write('/proc/cmdline', ' '.join(UVD.POWEROFF_BOOT_ARGUMENTS * 2))
                elif kind == 'validation': self.manifest['manifest_validation']['embedded_root_contract_verified'] = False
                elif kind == 'root-pin': self.manifest.pop('rootfs_sha256')
                elif kind == 'receipt': self.receipt['files']['initramfs.cpio.gz'] = '56' * 32
                else: (self.fixture / 'sys/module/psx_sbram').mkdir()
                self.save_manifest(); self.save_receipt()
                with self.assertRaises(ValueError): self.controller.request('power.execute', {'operation': 'shutdown', 'confirm': True})
                self.assertFalse(self.controller.power_status()['reboot-orbis']['available'])
                if kind == 'auxiliary':
                    self.assertIn('Auxiliary-memory experiment', self.controller.power_status()['shutdown']['reason'])
                    (self.fixture / 'sys/module/psx_sbram').rmdir()
                self.manifest, self.receipt = manifest, receipt
                self.save_manifest(); self.save_receipt()
                self.write('/sys/module/amdgpu/parameters/ps4_uvd_poff', 'Y')
                self.write('/proc/cmdline', ' '.join(UVD.POWEROFF_BOOT_ARGUMENTS))
        self.assertEqual(self.controller.jobs.list(), [])
        self.process.assert_not_called()

    def test_dev3_and_dev4_are_distinct_exact_supported_profiles(self):
        for selected in (UVD.ALPHA4, UVD.ALPHA5):
            with self.subTest(profile=selected['profile']):
                self.manifest.update(copy.deepcopy(selected))
                self.manifest['manifest_validation']['observed_kernel_release'] = selected['kernel_release']
                self.write('/proc/sys/kernel/osrelease', selected['kernel_release'])
                self.controller.runner.catalogue['expected_kernel_release'] = selected['kernel_release']
                self.save_manifest()
                self.receipt.update(profile=selected['profile'], kernel_release=selected['kernel_release'])
                self.receipt['files']['bzImage'] = selected['kernel_sha256']
                self.save_receipt()
                self.assertTrue(self.controller.power_status()['shutdown']['available'])

    def test_actual_stager_receipt_needs_no_image_version_but_conflicting_version_refuses(self):
        self.assertNotIn('image_version', self.receipt)
        self.controller._power_guard('shutdown')
        self.receipt['image_version'] = 'unreviewed'
        self.save_receipt()
        with self.assertRaisesRegex(ValueError, 'ownership receipt'):
            self.controller._power_guard('shutdown')

    def test_status_cache_avoids_repeated_history_reads_but_confirmation_rechecks_it(self):
        with mock.patch.object(self.controller, '_power_records', wraps=self.controller._power_records) as history:
            self.assertTrue(self.controller.power_status()['shutdown']['available'])
            self.controller.power_status(); self.controller.power_status()
            self.assertEqual(history.call_count, 1)
            self.controller.jobs.revision += 1
            self.controller.power_status()
            self.assertEqual(history.call_count, 2)
            self.report(phase='running')
            with self.assertRaisesRegex(ValueError, 'unfinished'):
                self.controller.request('power.execute', {'operation': 'shutdown', 'confirm': True})
            self.assertEqual(history.call_count, 3)
        self.assertEqual(self.controller.jobs.list(), [])

    def test_malformed_saved_objects_make_status_unavailable_without_breaking_state(self):
        marker = UVD.marker_path(self.controller.runner, BOOT)
        marker.parent.mkdir(mode=0o700)
        for value in ([], {}, {'schema_version': 1, 'boot_id': 'different-boot'}):
            with self.subTest(value=value):
                APP.diagnostics.atomic_json(marker, value)
                self.controller._power_status_cache = None
                self.assertFalse(self.controller.power_status()['shutdown']['available'])
        marker.unlink()
        self.report(identity=None)
        self.controller._power_status_cache = None
        self.assertFalse(self.controller.power_status()['shutdown']['available'])

    def test_symlink_in_reports_refuses_flush_and_never_follows_its_target(self):
        outside = self.root / 'unrelated.txt'
        outside.write_text('unrelated host data')
        (self.controller.results_dir / 'symlink').symlink_to(outside)
        with self.assertRaises(OSError): self.controller._power_flush()
        self.assertEqual(outside.read_text(), 'unrelated host data')

    def test_completed_uvd_pass_allows_only_confirmed_power_and_keeps_attempt_marker(self):
        marker, _ = self.uvd_pass()
        before = marker.read_bytes()
        self.assertTrue(self.controller.power_status()['shutdown']['available'])
        with self.assertRaisesRegex(ValueError, 'UVD attempt'): self.controller.ready()
        self.assertEqual(marker.read_bytes(), before)
        record = json.loads((self.controller.results_dir / 'saved-uvd/result.json').read_text())
        for changes in ({'phase': 'running'}, {'status': 'FAIL'}, {'hardware_acceptance': True},
                        {'identity': dict(record['identity'], runner_sha256='56' * 32)}):
            APP.diagnostics.atomic_json(self.controller.results_dir / 'saved-uvd/result.json', dict(record, **changes))
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.controller._power_guard('shutdown')
        self.assertEqual(marker.read_bytes(), before)

    def test_unfinished_faulted_or_missing_uvd_evidence_stops_before_reserving_power(self):
        for changes in ({'phase': 'running'}, {'status': 'FAIL', 'stop_required': True}):
            with self.subTest(changes=changes):
                self.report(**changes)
                with self.assertRaises(ValueError): self.controller._power_guard('shutdown')
        (self.controller.results_dir / 'saved-test/result.json').unlink()
        marker, _ = self.uvd_pass()
        (self.controller.results_dir / 'saved-uvd/result.json').unlink()
        with self.assertRaisesRegex(ValueError, 'no matching completed PASS'):
            self.controller._power_guard('shutdown')
        self.assertTrue(marker.exists())
        self.assertFalse(self.controller._power_intent_path(self.controller.runner.identity()).exists())

    def test_browser_close_acknowledgement_must_be_durable_before_power_can_arm(self):
        record = self.report(test_id='browser.batch', closed=False)
        with self.assertRaisesRegex(ValueError, 'confirmed closing'):
            self.controller._power_guard('shutdown')
        APP.diagnostics.atomic_json(self.controller.results_dir / record['run_id'] / 'result.json',
            dict(record, closed=True))
        self.controller._power_guard('shutdown')

    def test_saved_request_and_final_job_are_durable_before_dispatch_and_never_claim_pass(self):
        dispatched = threading.Event()
        seen = []
        def callback(record):
            saved = json.loads(next(self.controller.jobs.directory.glob('*.json')).read_text())
            self.assertEqual(saved['state'], 'completed')
            self.assertEqual(saved['result']['run_id'], record['run_id'])
            intent = self.controller._power_intent_path(record['identity'])
            self.assertEqual(json.loads(intent.read_text())['run_id'], record['run_id'])
            self.assertTrue(json.loads((Path(record['result_directory']) / 'result.json').read_text())['power_armed'])
            seen.append(record); dispatched.set()
        with mock.patch.object(self.controller, 'dispatch_power', side_effect=callback):
            job = self.controller.request('power.execute', {'operation': 'shutdown', 'confirm': True})
            result = self.wait(job, dispatched)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(seen[0]['status'], 'INCONCLUSIVE')
        self.assertFalse(seen[0]['hardware_acceptance'])
        with self.assertRaisesRegex(ValueError, 'already recorded'):
            self.controller.request('power.execute', {'operation': 'reboot-orbis', 'confirm': True})
        with self.assertRaisesRegex(ValueError, 'already recorded'):
            self.controller.request('result.observe', {'run_id': 'saved-test', 'answer': 'yes'})
        self.process.assert_not_called()

    def test_active_application_or_unreviewed_mount_saves_blocked_report_without_dispatch(self):
        for failure in ('app', 'root', 'malformed'):
            with self.subTest(failure=failure):
                self.services.return_value = {APP.DIAGNOSTICS_UNIT: 'active', 'psx-diagnostics-window-browser.service':
                    'active' if failure == 'app' else 'inactive'}
                self.mounts('rw')
                if failure == 'root': self.write('/proc/self/mountinfo',
                    '31 1 8:0 / / rw - ext4 /dev/sda rw\n32 1 8:1 / /run/psx-media rw - vfat /dev/sdb rw\n')
                if failure == 'malformed': self.write('/proc/self/mountinfo', '1 2 3 4 / 6 7 8 9 -\n')
                record = self.controller.prepare_power('shutdown')
                self.assertEqual(record['status'], 'BLOCKED')
                self.assertFalse(record['power_armed'])
                self.assertFalse(self.controller._power_intent_path(record['identity']).exists())
        self.process.assert_not_called()

    def test_report_flush_failure_keeps_intent_but_never_dispatches(self):
        with mock.patch.object(self.controller, '_power_flush', side_effect=OSError('synthetic fsync failure')), \
                mock.patch.object(self.controller, 'dispatch_power') as dispatch:
            job = self.controller.request('power.execute', {'operation': 'shutdown', 'confirm': True})
            self.assertEqual(self.wait(job)['state'], 'failed')
            dispatch.assert_not_called()
        intent = self.controller._power_intent_path(self.controller.runner.identity())
        self.assertTrue(intent.exists())
        self.assertFalse(self.controller.power_status()['shutdown']['available'])
        current = self.controller.power_status()['current_request']
        self.assertEqual(current['dispatch_phase'], 'prepare.flush')
        self.assertEqual(current['dispatch_phase_state'], 'failed')
        self.assertEqual(current['dispatch_state'], 'failed_no_retry')

    def assert_responsive_during_flush(self, job, phase):
        ready = threading.Event()
        observed = {}
        def read():
            try:
                observed['state'] = self.controller.request('state')
                observed['job'] = self.controller.request('job.get', {'id': job['id']})
            finally:
                ready.set()
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(1), 'Read-only state/job.get blocked behind a flush')
        thread.join(1)
        current = observed['state']['power']['current_request']
        self.assertEqual(current['dispatch_phase'], phase)
        self.assertEqual(current['dispatch_phase_state'], 'started')
        self.assertIn('Preparing', current['message'])
        self.assertEqual(current['status'], 'INCONCLUSIVE')
        return observed['job']

    def test_blocked_preparation_flush_exposes_phase_with_responsive_state_and_job(self):
        entered, release = threading.Event(), threading.Event()
        def blocked():
            entered.set()
            release.wait(3)
            raise OSError('synthetic preparation flush stop')
        with mock.patch.object(self.controller, '_power_flush', side_effect=blocked), \
                mock.patch.object(self.controller, 'dispatch_power') as dispatch:
            job = self.controller.request('power.execute', {'operation': 'shutdown', 'confirm': True})
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.assert_responsive_during_flush(job, 'prepare.flush')['state'], 'running')
            finally:
                release.set()
            self.assertEqual(self.wait(job)['state'], 'failed')
            dispatch.assert_not_called()
        self.process.assert_not_called()

    def test_blocked_final_flush_exposes_phase_after_saved_job_without_any_power_command(self):
        record = self.controller.prepare_power('reboot-orbis')
        self.controller.runner.fs.fixture = False  # All execution is refused/mocked; root remains synthetic.
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def blocked():
            entered.set()
            release.wait(3)
            raise OSError('synthetic final flush stop')
        def dispatch(value):
            try:
                self.controller.dispatch_power(value)
            finally:
                finished.set()
        with mock.patch.object(self.controller, '_power_flush', side_effect=blocked):
            job = self.controller.jobs.submit('Reboot to Orbis', lambda: record, after_save=dispatch)
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.assert_responsive_during_flush(job, 'dispatch.flush')['state'], 'completed')
            finally:
                release.set()
            self.assertTrue(finished.wait(1))
        current = self.controller.power_status()['current_request']
        self.assertEqual(current['dispatch_phase'], 'dispatch.flush')
        self.assertEqual(current['dispatch_phase_state'], 'failed')
        self.assertEqual(current['dispatch_state'], 'failed_no_retry')
        self.process.assert_not_called()

    def test_final_job_save_failure_never_runs_post_save_power_callback(self):
        real = APP.diagnostics.atomic_json
        def write(path, record):
            if path.parent == self.controller.jobs.directory and record.get('state') == 'completed':
                raise OSError('synthetic final job fsync failure')
            real(path, record)
        with mock.patch.object(APP.diagnostics, 'atomic_json', side_effect=write), \
                mock.patch.object(self.controller, 'dispatch_power') as dispatch:
            job = self.controller.request('power.execute', {'operation': 'shutdown', 'confirm': True})
            self.assertEqual(self.wait(job)['state'], 'failed')
            dispatch.assert_not_called()
        self.assertTrue(self.controller._power_intent_path(self.controller.runner.identity()).exists())

    def test_fixture_dispatch_refuses_before_any_process_or_device_call(self):
        record = self.controller.prepare_power('shutdown')
        self.controller.dispatch_power(record)
        saved = json.loads((self.fixture / 'run/psx-diagnostics/power-dispatch.json').read_text())
        self.assertEqual(saved['dispatch_state'], 'failed_no_retry')
        self.assertIn('Fixture roots', saved['message'])
        self.process.assert_not_called()

    def test_fixture_phase_never_opens_host_kmsg_even_with_a_mocked_root_path(self):
        record = self.controller.prepare_power('shutdown')
        with mock.patch.object(self.controller.runner.fs, 'root', Path('/')), \
                mock.patch.object(APP.diagnostics, 'atomic_json') as save, \
                mock.patch.object(APP.os, 'open', side_effect=AssertionError('host device open')):
            self.controller._power_phase(dict(record, dispatch_state='preparing'), 'dispatch.flush', 'started')
            self.assertFalse(save.call_args.args[1]['uart_marker_written'])

    def test_shutdown_dispatch_is_fixed_sync_then_poweroff_not_reboot(self):
        record = self.controller.prepare_power('shutdown')
        self.controller.runner.fs.fixture = False  # Everything that could act is mocked below.
        def while_locked(argv, **kwargs):
            # A standalone Runner uses exactly this flock; it cannot enter
            # during either sync or the final power request.
            with self.assertRaisesRegex(ValueError, 'Another diagnostic run'):
                with self.controller.runner.lock(): pass
            marker = json.loads((self.fixture / 'run/psx-diagnostics/power-dispatch.json').read_text())
            self.assertEqual(marker['dispatch_phase'], 'dispatch.sync' if argv[0] == '/usr/bin/sync' else 'dispatch.shutdown')
            self.assertEqual(marker['dispatch_phase_state'], 'started')
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(APP.subprocess, 'run', side_effect=while_locked) as command, \
                mock.patch.object(self.controller, '_power_icc_restart') as icc:
            self.controller.dispatch_power(record)
        self.assertEqual([call.args[0] for call in command.call_args_list], [
            ['/usr/bin/sync'], ['/usr/bin/systemctl', 'poweroff', '--no-block']])
        icc.assert_not_called()
        self.assertEqual(json.loads((self.fixture / 'run/psx-diagnostics/power-dispatch.json').read_text())['dispatch_state'],
            'request_returned_physical_outcome_unobserved')

    def test_forced_restart_syncs_and_checks_usb_readonly_before_single_fixed_icc_request(self):
        record = self.controller.prepare_power('reboot-orbis')
        self.controller.runner.fs.fixture = False
        events = []
        def command(argv, **kwargs):
            events.append(argv)
            marker = json.loads((self.fixture / 'run/psx-diagnostics/power-dispatch.json').read_text())
            self.assertEqual(marker['dispatch_phase'], 'dispatch.sync' if argv[0] == '/usr/bin/sync' else 'dispatch.remount')
            self.assertEqual(marker['dispatch_phase_state'], 'started')
            if argv[0] == '/usr/bin/mount': self.mounts('ro')
            return subprocess.CompletedProcess(argv, 0)
        def icc_request():
            marker = json.loads((self.fixture / 'run/psx-diagnostics/power-dispatch.json').read_text())
            self.assertEqual(marker['dispatch_phase'], 'dispatch.icc')
            self.assertEqual(marker['dispatch_phase_state'], 'started')
            events.append('icc')
        with mock.patch.object(APP.subprocess, 'run', side_effect=command), \
                mock.patch.object(self.controller, '_power_icc_restart', side_effect=icc_request) as icc:
            self.controller.dispatch_power(record)
        self.assertEqual(events, [['/usr/bin/sync'], ['/usr/bin/mount', '-o', 'remount,ro', '/run/psx-media'], 'icc'])
        icc.assert_called_once_with()
        final = json.loads((self.fixture / 'run/psx-diagnostics/power-dispatch.json').read_text())
        self.assertEqual(final['dispatch_phase'], 'dispatch.icc')
        self.assertEqual(final['dispatch_phase_state'], 'returned')
        self.assertTrue(self.controller._power_intent_path(record['identity']).exists())

    def select_alpha6(self):
        # Synthetic pins test the real selection and dispatch code; they never
        # qualify or package a kernel. The adapter refuses pending real pins.
        pins = {'kernel_sha256': '56' * 32, 'config_sha256': '78' * 32,
                'modules_sha256': '9a' * 32}
        patch = mock.patch.dict(UVD.ALPHA6, pins)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(self.controller.runner, 'uvd_adapter', return_value=UVD)
        patch.start(); self.addCleanup(patch.stop)
        self.manifest.update(copy.deepcopy(UVD.ALPHA6))
        release = self.manifest['kernel_release']
        self.manifest['manifest_validation']['observed_kernel_release'] = release
        self.write('/proc/sys/kernel/osrelease', release)
        self.controller.runner.catalogue['expected_kernel_release'] = release
        self.save_manifest()
        self.receipt.update(profile=self.manifest['profile'], kernel_release=release)
        self.receipt['files']['bzImage'] = self.manifest['kernel_sha256']
        self.save_receipt()

    def test_alpha6_restart_uses_normal_kernel_reboot_after_verified_readonly_sync(self):
        self.select_alpha6()
        record = self.controller.prepare_power('reboot-orbis')
        self.assertTrue(record['power_armed'])
        self.assertEqual(record['power_path'], 'kernel-restart')
        self.assertNotIn('teardown is skipped', record['limitations'])
        self.controller.runner.fs.fixture = False  # All processes/devices mocked.
        def execute(argv, **kwargs):
            if argv[0] == '/usr/bin/mount': self.mounts('ro')
            if argv[0] == '/usr/bin/systemctl':
                self.assertIn('ro', self.controller._power_mounts()['/run/psx-media'][0])
                marker = json.loads((self.fixture / 'run/psx-diagnostics/power-dispatch.json').read_text())
                self.assertEqual(marker['dispatch_phase'], 'dispatch.restart')
                self.assertEqual(marker['dispatch_phase_state'], 'started')
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(APP.subprocess, 'run', side_effect=execute) as command, \
                mock.patch.object(self.controller, '_power_icc_restart', side_effect=AssertionError('legacy ICC must not run')):
            self.controller.dispatch_power(record)
        self.assertEqual([call.args[0] for call in command.call_args_list], [
            ['/usr/bin/sync'], ['/usr/bin/mount', '-o', 'remount,ro', '/run/psx-media'],
            ['/usr/bin/systemctl', 'reboot', '--no-block']])
        final = self.controller.power_status()['current_request']
        self.assertEqual(final['dispatch_state'], 'request_returned_physical_outcome_unobserved')
        self.assertEqual(final['status'], 'INCONCLUSIVE')
        self.assertTrue(self.controller._power_intent_path(record['identity']).exists())

    def test_alpha6_reboot_error_never_falls_back_to_userspace_icc(self):
        self.select_alpha6()
        record = self.controller.prepare_power('reboot-orbis')
        self.controller.runner.fs.fixture = False
        def execute(argv, **kwargs):
            if argv[0] == '/usr/bin/mount': self.mounts('ro')
            if argv[0] == '/usr/bin/systemctl':
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(APP.subprocess, 'run', side_effect=execute) as command, \
                mock.patch.object(self.controller, '_power_icc_restart', side_effect=AssertionError('no fallback')):
            self.controller.dispatch_power(record)
        self.assertEqual(sum(call.args[0][0] == '/usr/bin/systemctl' for call in command.call_args_list), 1)
        self.assertEqual(self.controller.power_status()['current_request']['dispatch_state'], 'failed_no_retry')
        with self.assertRaisesRegex(ValueError, 'already recorded'):
            self.controller.prepare_power('reboot-orbis')

    def test_busy_final_runner_lock_refuses_every_power_command_and_exposes_the_failure(self):
        record = self.controller.prepare_power('shutdown')
        self.controller.runner.fs.fixture = False
        with self.controller.runner.lock():
            self.controller.dispatch_power(record)
        self.process.assert_not_called()
        self.assertEqual(record['dispatch_state'], 'failed_no_retry')
        self.assertIn('Another diagnostic run', self.controller.power_status()['shutdown']['reason'])
        reported = next(value for value in self.controller.results() if value['run_id'] == record['run_id'])
        self.assertIn('Another diagnostic run', reported['message'])
        self.assertEqual(reported['status'], 'INCONCLUSIVE')

    def test_post_remount_failure_is_visible_from_ram_without_writing_readonly_usb(self):
        record = self.controller.prepare_power('reboot-orbis')
        report = self.controller.results_dir / record['run_id'] / 'result.json'
        before = report.read_bytes()
        self.controller.runner.fs.fixture = False
        def command(argv, **kwargs):
            if argv[0] == '/usr/bin/mount': self.mounts('ro')
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(APP.subprocess, 'run', side_effect=command), \
                mock.patch.object(self.controller, '_power_icc_restart', side_effect=OSError('synthetic ICC failure')):
            self.controller.dispatch_power(record)
        self.assertEqual(report.read_bytes(), before)
        reason = self.controller.power_status()['reboot-orbis']['reason']
        self.assertIn('synthetic ICC failure', reason)
        self.assertIn('Do not retry', reason)
        reported = next(value for value in self.controller.results() if value['run_id'] == record['run_id'])
        self.assertEqual(reported['dispatch_state'], 'failed_no_retry')
        self.assertEqual(reported['message'], reason)
        self.assertFalse(reported['hardware_acceptance'])

    def test_legacy_runner_json_abi_keeps_pending_failure_and_usb_fallback_visible(self):
        # The frozen alpha5 root has read_json(path), while newer host source
        # accepts a second limit argument. Exercise that actual deployed ABI.
        current_reader = APP.diagnostics.read_json
        def legacy_reader(path):
            return current_reader(path)
        with mock.patch.object(APP.diagnostics, 'read_json', side_effect=legacy_reader):
            record = self.controller.prepare_power('reboot-orbis')
            self.assertEqual(self.controller.state()['power']['current_request']['dispatch_state'], 'preparing')
            self.controller.runner.fs.fixture = False
            with mock.patch.object(self.controller, '_power_flush', side_effect=OSError('synthetic flush failure')):
                self.controller.dispatch_power(record)
            self.process.assert_not_called()
            current = self.controller.state()['power']['current_request']
            self.assertEqual(current['dispatch_state'], 'failed_no_retry')
            self.assertIn('synthetic flush failure', current['message'])
            report = self.controller.results_dir / record['run_id'] / 'result.json'
            self.assertEqual(current_reader(report)['dispatch_state'], 'failed_no_retry')
            (self.fixture / 'run/psx-diagnostics/power-dispatch.json').unlink()
            self.controller._power_status_cache = None
            fallback = self.controller.state()['power']['current_request']
            self.assertEqual(fallback['dispatch_state'], 'failed_no_retry')
            self.assertEqual(fallback['message'], current['message'])
            self.assertTrue(self.controller._power_intent_path(record['identity']).exists())

    def test_power_json_reader_bounds_complete_files_and_refuses_unsafe_inputs(self):
        path = self.root / 'power.json'
        path.write_bytes(b' ' * (65536 - 2) + b'{}')
        self.assertEqual(APP._power_read_json(path), {})
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, '64 KiB'):
            APP._power_read_json(path)
        path.write_text('{"value":NaN}')
        with self.assertRaisesRegex(ValueError, 'Nonfinite'):
            APP._power_read_json(path)
        path.write_text('{"value":')
        with self.assertRaises(json.JSONDecodeError):
            APP._power_read_json(path)
        path.write_text('{}')
        link = self.root / 'linked.json'
        link.symlink_to(path)
        directory = self.root / 'linked-directory'
        directory.symlink_to(self.root, target_is_directory=True)
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        for unsafe in (link, directory / path.name, fifo, Path('/dev/null')):
            with self.subTest(path=unsafe), self.assertRaisesRegex(ValueError, 'regular file'):
                APP._power_read_json(unsafe)

    def test_power_json_reader_refuses_a_file_changed_during_read(self):
        path = self.root / 'power.json'
        path.write_text('{}')
        before = path.stat()
        after = SimpleNamespace(st_size=before.st_size, st_mtime_ns=before.st_mtime_ns + 1)
        with mock.patch.object(APP.os, 'fstat', side_effect=(before, after)), \
                self.assertRaisesRegex(ValueError, 'changed while reading'):
            APP._power_read_json(path)

    def test_dispatch_outcome_rejects_wrong_identity_and_oversized_ram_record(self):
        record = self.controller.prepare_power('shutdown')
        self.controller.dispatch_power(record)  # Fixture refusal creates an inert outcome.
        path = self.fixture / 'run/psx-diagnostics/power-dispatch.json'
        saved = json.loads(path.read_text())
        self.assertIsNotNone(self.controller._power_dispatch_outcome(self.controller.runner.identity()))
        for invalid in (dict(saved, identity=dict(saved['identity'], boot_id='old-boot')),
                        dict(saved, message='x' * 65536), dict(saved, run_id='another-run')):
            with self.subTest(kind=list(invalid)):
                APP.diagnostics.atomic_json(path, invalid)
                self.assertIsNone(self.controller._power_dispatch_outcome(self.controller.runner.identity()))

    def test_state_refresh_exposes_pending_sent_and_failed_after_completed_job_snapshot(self):
        record = self.controller.prepare_power('reboot-orbis')
        initial = self.controller.power_status()['current_request']
        self.assertEqual(initial['run_id'], record['run_id'])
        self.assertEqual(initial['boot_id'], BOOT)
        self.assertEqual(initial['dispatch_state'], 'preparing')
        self.assertIn('Preparing', initial['message'])
        self.assertNotIn('commands', initial)
        intent = self.controller._power_intent_path(record['identity']).read_bytes()
        for stage, message, expected in (
                ('not_sent', record['message'], 'Preparing'),
                ('issuing_forced_icc_restart', record['message'], 'Requesting a restart'),
                ('issuing_kernel_restart', record['message'], 'Requesting Linux restart'),
                ('request_returned_physical_outcome_unobserved', 'Request returned.', 'Power request sent'),
                ('failed_no_retry', 'Power request failed: synthetic failure. Do not retry.', 'synthetic failure')):
            with self.subTest(stage=stage):
                # No action/job revision change: this is a later RAM outcome
                # after the same completed, durably saved job snapshot.
                APP.diagnostics.atomic_json(self.fixture / 'run/psx-diagnostics/power-dispatch.json',
                    dict(record, dispatch_state=stage, message=message))
                state = self.controller.request('state')
                current = state['power']['current_request']
                self.assertEqual(current['dispatch_state'], stage)
                self.assertIn(expected, current['message'])
                self.assertEqual(current['status'], 'INCONCLUSIVE')
                self.assertFalse(state['power']['reboot-orbis']['available'])
        self.assertEqual(self.controller._power_intent_path(record['identity']).read_bytes(), intent)
        self.process.assert_not_called()

    def test_failed_remount_verification_retains_stop_and_never_sends_icc(self):
        record = self.controller.prepare_power('reboot-orbis')
        self.controller.runner.fs.fixture = False
        with mock.patch.object(APP.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
                mock.patch.object(self.controller, '_power_icc_restart') as icc:
            self.controller.dispatch_power(record)
        icc.assert_not_called()
        saved = json.loads((self.fixture / 'run/psx-diagnostics/power-dispatch.json').read_text())
        self.assertEqual(saved['dispatch_state'], 'failed_no_retry')
        self.assertIn('did not become read-only', saved['message'])
        self.assertTrue(self.controller._power_intent_path(record['identity']).exists())
        report = self.controller.results_dir / record['run_id'] / 'result.json'
        self.assertEqual(json.loads(report.read_text())['dispatch_state'], 'failed_no_retry')
        (self.fixture / 'run/psx-diagnostics/power-dispatch.json').unlink()
        self.controller._power_status_cache = None
        self.assertIn('did not become read-only', self.controller.power_status()['reboot-orbis']['reason'])


if __name__ == '__main__':
    unittest.main()
