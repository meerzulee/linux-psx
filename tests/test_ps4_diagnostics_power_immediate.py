# SPDX-License-Identifier: MIT
"""Immediate power is synthetic; native child tests own only their PTY-free tools."""
import errno
import copy
import importlib.util
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('immediate_power_base', ROOT / 'tests/test_ps4_diagnostics_power_actions.py')
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
APP = BASE.APP


class ImmediatePowerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = BASE.PowerActionsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.select_alpha6()
        pins = {'kernel_sha256': 'ab' * 32, 'config_sha256': 'bc' * 32, 'modules_sha256': 'cd' * 32}
        patch = mock.patch.dict(BASE.UVD.ALPHA7, pins)
        patch.start(); self.addCleanup(patch.stop)
        self.fixture.manifest.update(copy.deepcopy(BASE.UVD.ALPHA7))
        release = self.fixture.manifest['kernel_release']
        self.fixture.manifest['manifest_validation']['observed_kernel_release'] = release
        self.fixture.write('/proc/sys/kernel/osrelease', release)
        self.fixture.controller.runner.catalogue['expected_kernel_release'] = release
        self.fixture.save_manifest()
        self.fixture.receipt.update(profile=self.fixture.manifest['profile'], kernel_release=release)
        self.fixture.receipt['files']['bzImage'] = pins['kernel_sha256']
        self.fixture.save_receipt()
        self.controller = self.fixture.controller
        self.controller._cache_power_boot()
        self.assertIsNotNone(self.controller._power_boot)
        patch = mock.patch.object(self.controller, '_power_kernel_call', return_value=None)
        self.kernel_call = patch.start()
        self.addCleanup(patch.stop)

    def request(self, operation='shutdown'):
        return self.controller.request('power.execute', {'operation': operation, 'confirm': True})

    def outcome(self, state):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            record = self.controller._power_now
            if record and record.get('dispatch_state') == state and record.get('in_flight') is False:
                return record
            time.sleep(.005)
        self.fail('Power outcome did not arrive: ' + str(self.controller._power_now))

    def test_confirmation_and_exact_cached_boot_are_still_required(self):
        for params in ({'operation': 'shutdown'}, {'operation': 'shutdown', 'confirm': 1},
                       {'operation': 'reboot', 'confirm': True},
                       {'operation': 'shutdown', 'confirm': True, 'command': 'anything'}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.controller.request('power.execute', params)
        self.kernel_call.assert_not_called()
        self.fixture.receipt['files']['bzImage'] = '00' * 32
        self.fixture.save_receipt()
        self.controller._cache_power_boot()
        self.assertIsNone(self.controller._power_boot)
        with self.assertRaises(ValueError):
            self.request()
        self.kernel_call.assert_not_called()

    def test_dev5_stays_on_legacy_guarded_power_path(self):
        self.fixture.select_alpha6()
        self.controller._cache_power_boot()
        self.assertIsNone(self.controller._power_boot)

    def test_cached_path_ignores_legacy_intents_history_and_all_report_io(self):
        intent = self.controller._power_intent_path(self.controller._power_boot)
        intent.parent.mkdir()
        intent.write_text('{"legacy_pending":true}')
        original = intent.read_bytes()
        with mock.patch.object(self.controller.runner, 'identity', side_effect=AssertionError('live identity read')), \
                mock.patch.object(APP.diagnostics, 'read_json', side_effect=AssertionError('USB read')), \
                mock.patch.object(APP.diagnostics, 'atomic_json', side_effect=AssertionError('report save')), \
                mock.patch.object(APP.os, 'fsync', side_effect=AssertionError('fsync')), \
                mock.patch.object(self.controller, '_power_flush', side_effect=AssertionError('flush')), \
                mock.patch.object(self.controller, '_power_mounts', side_effect=AssertionError('mount check')):
            self.request()
            record = self.outcome('request_returned_physical_outcome_unobserved')
            self.assertFalse(record['hardware_acceptance'])
            self.assertIn('Unsaved reports', record['message'])
        self.assertEqual(intent.read_bytes(), original)
        self.kernel_call.assert_called_once_with('shutdown')
        self.fixture.process.assert_not_called()

    def test_bypasses_action_guard_and_stalled_initial_job_save(self):
        entered, release, guard_held, guard_release = (threading.Event() for _ in range(4))
        real_save = APP.diagnostics.atomic_json
        def save(path, record):
            if path.parent == self.controller.jobs.directory:
                entered.set(); release.wait(3)
            return real_save(path, record)
        def hold_guard():
            with self.controller.action_guard:
                guard_held.set(); guard_release.wait(3)
        action = mock.Mock(return_value={'status': 'PASS'})
        thread = threading.Thread(target=hold_guard)
        thread.start()
        with mock.patch.object(APP.diagnostics, 'atomic_json', side_effect=save):
            old = threading.Thread(target=lambda: self.controller.jobs.submit('Stalled save', action))
            old.start()
            try:
                self.assertTrue(entered.wait(1)); self.assertTrue(guard_held.wait(1))
                started = time.monotonic()
                self.request()
                self.outcome('request_returned_physical_outcome_unobserved')
                self.assertLess(time.monotonic() - started, 1)
                self.assertFalse(release.is_set())
                self.kernel_call.assert_called_once()
            finally:
                release.set(); guard_release.set(); old.join(2); thread.join(2)
        action.assert_not_called()
        with self.assertRaises(APP.BusyError):
            self.controller.jobs.submit('Late work', action)

    def test_stalled_final_job_save_cannot_run_late_callback(self):
        entered, release = threading.Event(), threading.Event()
        real_save = APP.diagnostics.atomic_json
        callback = mock.Mock()
        def save(path, record):
            if path.parent == self.controller.jobs.directory and record['state'] == 'completed':
                entered.set(); release.wait(3)
            return real_save(path, record)
        with mock.patch.object(APP.diagnostics, 'atomic_json', side_effect=save):
            old = self.controller.jobs.submit('Old work', lambda: {'status': 'PASS'}, after_save=callback)
            try:
                self.assertTrue(entered.wait(1))
                self.request()
                self.outcome('request_returned_physical_outcome_unobserved')
                self.assertEqual(self.controller.jobs.get(old['id'])['state'], 'failed')
            finally:
                release.set()
        time.sleep(.025)
        callback.assert_not_called()

    def test_duplicate_requests_never_repeat_issued_command_even_if_call_hangs(self):
        entered, release = threading.Event(), threading.Event()
        def call(_):
            entered.set(); release.wait(3)
        self.kernel_call.side_effect = call
        first = self.request('reboot-orbis')
        try:
            self.assertTrue(entered.wait(1))
            second = self.request('shutdown')
            self.assertEqual(first['id'], second['id'])
            self.assertEqual(self.controller.jobs.get(first['id'])['state'], 'completed')
            self.assertFalse(self.controller.power_status()['shutdown']['available'])
            self.assertEqual(self.kernel_call.call_count, 1)
        finally:
            release.set()
        self.outcome('request_returned_physical_outcome_unobserved')

    def test_known_syscall_refusal_is_retryable_but_unknown_error_is_not(self):
        self.kernel_call.side_effect = OSError(errno.EPERM, 'synthetic permission refusal')
        first = self.request()
        refused = self.outcome('failed_no_retry')
        self.assertTrue(refused['retry_allowed'])
        self.assertFalse(refused['command_submitted'])
        self.assertTrue(self.controller.power_status()['shutdown']['available'])
        self.kernel_call.side_effect = OSError(errno.EIO, 'synthetic uncertain error')
        second = self.request('reboot-orbis')
        self.assertNotEqual(first['id'], second['id'])
        uncertain = self.outcome('failed_no_retry')
        self.assertFalse(uncertain['retry_allowed'])
        self.assertIn('uncertain', uncertain['message'])
        self.assertEqual(self.request()['id'], second['id'])
        self.assertEqual(self.kernel_call.call_count, 2)

    def test_expired_presubmission_can_never_dispatch_later(self):
        entered, release = threading.Event(), threading.Event()
        def pause():
            entered.set(); release.wait(3)
            return []
        with mock.patch.object(self.controller, '_kill_power_graphics', side_effect=pause):
            self.request()
            try:
                self.assertTrue(entered.wait(1))
                self.controller._power_now['dispatch_deadline_monotonic'] = time.monotonic() - 1
                self.assertTrue(self.controller.power_status()['shutdown']['available'])
                self.assertTrue(self.controller._power_now['cancelled'])
            finally:
                release.set()
            self.outcome('failed_no_retry')
        self.kernel_call.assert_not_called()

    def test_fixed_graphics_cgroups_exclude_controller_and_unrelated_units(self):
        root = self.fixture.fixture / 'sys/fs/cgroup/system.slice'
        units = ('psx-diagnostics-window-browser.service', 'psx-diagnostics-control.service', 'unrelated.service')
        for name in units:
            path = root / name / 'cgroup.kill'; path.parent.mkdir(parents=True)
            path.write_bytes(b'0')
        killed = self.controller._kill_power_graphics()
        self.assertEqual(killed, [units[0]])
        self.assertEqual((root / units[0] / 'cgroup.kill').read_bytes(), b'1')
        for name in units[1:]:
            self.assertEqual((root / name / 'cgroup.kill').read_bytes(), b'0')

    def test_runner_hooks_kill_owned_process_group_and_refuse_future_children(self):
        started, done = threading.Event(), threading.Event()
        result = {}
        output = self.fixture.root / 'stdout'; error = self.fixture.root / 'stderr'
        def register(process):
            cleanup = self.controller._register_worker(process)
            result['pid'] = process.pid; started.set()
            return cleanup
        def work():
            try:
                result['value'] = APP.diagnostics.execute_process(
                    [sys.executable, '-c', 'import time; time.sleep(60)'], 65, output, error,
                    process_started=register, cancelled=self.controller.jobs.stopping.is_set)
            finally:
                done.set()
        thread = threading.Thread(target=work)
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertEqual(os.getpgid(result['pid']), result['pid'])
            self.assertEqual(self.controller._kill_power_workers(), 1)
            self.assertTrue(done.wait(2))
        finally:
            self.controller.jobs.stopping.set()
            thread.join(3)
        self.assertEqual(result['value'].returncode, -signal.SIGKILL)
        self.assertTrue(result['value'].interrupted)
        self.assertEqual(self.controller._workers, {})
        with mock.patch.object(APP.diagnostics.subprocess, 'Popen', side_effect=AssertionError('late child')):
            stopped = APP.diagnostics.execute_process(['unused'], 1, output, error,
                cancelled=self.controller.jobs.stopping.is_set)
        self.assertTrue(stopped.interrupted)
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            self.controller.runner.command(['unused'], 1)

    def test_real_syscall_helper_refuses_fixture_before_loading_libc(self):
        with mock.patch.object(APP.ctypes, 'CDLL', side_effect=AssertionError('real syscall')):
            with self.assertRaisesRegex(ValueError, 'Fixture'):
                APP.Controller._power_kernel_call(self.controller, 'shutdown')

    def test_syscall_dispatch_uses_only_ordinary_restart_and_poweroff_constants(self):
        libc = mock.Mock()
        libc.reboot.return_value = 0
        with mock.patch.object(self.controller.runner.fs, 'root', Path('/')), \
                mock.patch.object(self.controller.runner.fs, 'fixture', False), \
                mock.patch.object(APP.os, 'geteuid', return_value=0), \
                mock.patch.object(APP.ctypes, 'CDLL', return_value=libc):
            APP.Controller._power_kernel_call(self.controller, 'shutdown')
            APP.Controller._power_kernel_call(self.controller, 'reboot-orbis')
        self.assertEqual(libc.reboot.call_args_list, [mock.call(0x4321FEDC), mock.call(0x01234567)])
        self.fixture.process.assert_not_called()

    def test_submitted_ram_fence_survives_controller_memory_loss(self):
        first = self.request('reboot-orbis')
        record = self.outcome('request_returned_physical_outcome_unobserved')
        self.controller._power_now = None
        self.controller.jobs.records.clear()
        self.controller.jobs.stopping.clear()
        shutil.rmtree(self.fixture.fixture / 'run/psx-diagnostics')
        self.controller._restore_power_now()
        self.assertTrue(self.controller.jobs.stopping.is_set())
        with self.assertRaises(APP.BusyError):
            self.controller.jobs.submit('Restarted work', lambda: self.fail('late worker'))
        self.assertEqual(self.controller.power_status()['current_request']['run_id'], record['run_id'])
        duplicate = self.request()
        self.assertEqual(first['id'], duplicate['id'])
        self.kernel_call.assert_called_once()

    def test_ram_storage_failure_never_blocks_or_clears_issued_request(self):
        with mock.patch.object(APP.os, 'replace', side_effect=OSError(errno.ENOSPC, 'synthetic RAM full')):
            first = self.request()
            record = self.outcome('request_returned_physical_outcome_unobserved')
        self.assertTrue(record['command_submitted'])
        self.assertFalse(record['ram_record_saved'])
        self.assertIn('RAM full', self.controller.power_status()['current_request']['ram_record_error'])
        self.assertEqual(self.request('reboot-orbis')['id'], first['id'])
        self.kernel_call.assert_called_once()

    def test_delayed_graphics_job_cannot_spawn_after_confirmed_power(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def ready():
            entered.set(); release.wait(3)
            return self.controller._power_boot
        def launch():
            try:
                self.controller.launch_graphics('diagnostics')
            except Exception as error:
                errors.append(error)
            finally:
                done.set()
        with mock.patch.object(self.controller, 'ready', side_effect=ready), \
                mock.patch.object(self.controller.runner.fs, 'fixture', False), \
                mock.patch.object(APP.subprocess, 'Popen', side_effect=AssertionError('late display spawn')) as spawn:
            thread = threading.Thread(target=launch); thread.start()
            try:
                self.assertTrue(entered.wait(1))
                self.request()
                self.outcome('request_returned_physical_outcome_unobserved')
            finally:
                release.set(); thread.join(2)
            self.assertTrue(done.is_set())
            self.assertEqual(len(errors), 1)
            self.assertIn('cancelled', str(errors[0]))
            spawn.assert_not_called()

    def test_capture_executor_tracks_private_input_without_persistent_logs(self):
        private = b'synthetic private stdin\n'
        before = set(self.fixture.root.rglob('*'))
        with mock.patch.object(self.controller.runner.fs, 'fixture', False):
            response = self.controller._cancellable_capture(
                [sys.executable, '-c', 'import sys; print(len(sys.stdin.buffer.read()))'],
                timeout=3, input=private, text=True, check=True)
        self.assertEqual(response.stdout.strip(), str(len(private)))
        self.assertEqual(self.controller._workers, {})
        self.assertEqual(set(self.fixture.root.rglob('*')), before)

    def test_stalled_spawn_cannot_delay_power_and_late_registration_is_killed(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        process = mock.Mock(pid=12345, returncode=None)
        process.poll.side_effect = lambda: process.returncode
        def wait(**_):
            process.returncode = -signal.SIGKILL
            return process.returncode
        process.wait.side_effect = wait
        def spawn(*_, **__):
            entered.set(); release.wait(3)
            return process
        def kill(group, sig):
            self.assertEqual((group, sig), (process.pid, signal.SIGKILL))
            process.returncode = -signal.SIGKILL
        result = {}
        def tool():
            try:
                result['value'] = APP.diagnostics.execute_process(['synthetic-tool'], 4,
                    self.fixture.root / 'blocked-stdout', self.fixture.root / 'blocked-stderr',
                    process_started=self.controller._register_worker,
                    cancelled=self.controller.jobs.stopping.is_set,
                    launch_guard=self.controller._launch_guard)
            finally:
                done.set()
        with mock.patch.object(APP.subprocess, 'Popen', side_effect=spawn), \
                mock.patch.object(APP.os, 'killpg', side_effect=kill) as killed:
            thread = threading.Thread(target=tool); thread.start()
            try:
                self.assertTrue(entered.wait(1))
                self.request()
                self.outcome('request_returned_physical_outcome_unobserved')
                self.kernel_call.assert_called_once()
                self.assertFalse(release.is_set())
                self.assertTrue(self.controller.jobs.stopping.is_set())
                self.assertFalse(done.is_set())
            finally:
                release.set(); thread.join(2)
            self.assertTrue(done.is_set())
            self.assertTrue(result['value'].interrupted)
            self.assertIn(mock.call(process.pid, signal.SIGKILL), killed.call_args_list)
            self.assertEqual(self.controller._workers, {})


if __name__ == '__main__':
    unittest.main()
