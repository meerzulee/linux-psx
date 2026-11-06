# SPDX-License-Identifier: MIT
"""Storage stalls must not hold the job-state mutex or dispatch power early."""
import importlib.util
import json
from pathlib import Path
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'jobs_controller_fixture', ROOT / 'tests/test_ps4_diagnostics_controller.py')
BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)
APP = BASE.APP


class JobsDurabilityTests(unittest.TestCase):
    def setUp(self):
        BASE.ControllerTests.setUp(self)
        self.jobs = self.controller.jobs
        patch = mock.patch.object(APP.subprocess, 'run', side_effect=AssertionError('live command'))
        patch.start()
        self.addCleanup(patch.stop)

    def call_async(self, function):
        outcome, done = {}, threading.Event()
        def work():
            try:
                outcome['value'] = function()
            except BaseException as error:
                outcome['error'] = error
            finally:
                done.set()
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return outcome, done, thread

    def assert_responsive(self, identifier):
        def query():
            return (self.controller.request('job.get', {'id': identifier}),
                    self.jobs.list(), self.controller.request('state'))
        outcome, done, thread = self.call_async(query)
        self.assertTrue(done.wait(1), 'job.get/list/state waited on blocked storage')
        thread.join(1)
        if 'error' in outcome:
            raise outcome['error']
        job, listed, state = outcome['value']
        self.assertEqual(job['state'], 'running')
        self.assertIsNone(job['result'])
        self.assertNotIn('completed_at', job)
        self.assertEqual(next(x for x in listed if x['id'] == identifier)['state'], 'running')
        self.assertEqual(next(x for x in state['jobs'] if x['id'] == identifier)['state'], 'running')
        self.assertEqual(self.jobs.active, identifier)
        with self.assertRaises(APP.BusyError):
            self.jobs.submit('Must not overlap', lambda: self.fail('second action ran'))

    def wait_finished(self, identifier):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = self.jobs.get(identifier)
            if result['state'] != 'running':
                return result
            time.sleep(.005)
        self.fail('synthetic job did not finish')

    def blocked_initial(self, block_mkdir):
        entered, release, called, dispatched = (threading.Event() for _ in range(4))
        real_save, real_mkdir = APP.diagnostics.atomic_json, Path.mkdir
        def pause():
            entered.set()
            if not release.wait(3):
                raise OSError('synthetic test gate expired')
        def save(path, value):
            if path.parent == self.jobs.directory and value['state'] == 'running' and not block_mkdir:
                pause()
            return real_save(path, value)
        def mkdir(path, *args, **kwargs):
            if path == self.jobs.directory and block_mkdir:
                pause()
            return real_mkdir(path, *args, **kwargs)
        observed = []
        def action():
            identifier = self.jobs.active
            observed.append(json.loads((self.jobs.directory / (identifier + '.json')).read_text())['state'])
            called.set()
            return {'status': 'PASS'}
        with mock.patch.object(APP.diagnostics, 'atomic_json', side_effect=save), \
                mock.patch.object(Path, 'mkdir', new=mkdir):
            outcome, done, thread = self.call_async(
                lambda: self.jobs.submit('Initial durability', action, after_save=lambda _: dispatched.set()))
            try:
                self.assertTrue(entered.wait(1))
                identifier = self.jobs.active
                self.assertIsNotNone(identifier)
                self.assert_responsive(identifier)
                self.assertFalse(called.is_set())
                self.assertFalse(dispatched.is_set())
                self.assertFalse(done.is_set())
            finally:
                release.set()
                thread.join(2)
            self.assertTrue(done.is_set())
            if 'error' in outcome:
                raise outcome['error']
            self.assertEqual(self.wait_finished(identifier)['state'], 'completed')
            self.assertTrue(dispatched.wait(1))
        self.assertEqual(observed, ['running'])

    def test_initial_job_fsync_keeps_status_responsive_and_action_unstarted(self):
        self.blocked_initial(False)

    def test_initial_directory_creation_keeps_status_responsive(self):
        self.blocked_initial(True)

    def test_final_save_seals_events_and_keeps_running_until_durable_then_dispatches_once(self):
        entered, release, dispatched = (threading.Event() for _ in range(3))
        real_save = APP.diagnostics.atomic_json
        snapshots, observed = [], []
        def save(path, value):
            if path.parent == self.jobs.directory and value['state'] == 'completed':
                snapshots.append(value)
                entered.set()
                if not release.wait(3):
                    raise OSError('synthetic final gate expired')
            return real_save(path, value)
        def action():
            self.jobs.event({'phase': 'action-finished'})
            return {'status': 'PASS', 'power_armed': True}
        def after_save(value):
            observed.append((json.loads((self.jobs.directory / (job['id'] + '.json')).read_text()), value))
            dispatched.set()
        with mock.patch.object(APP.diagnostics, 'atomic_json', side_effect=save):
            job = self.jobs.submit('Final durability', action, after_save=after_save)
            try:
                self.assertTrue(entered.wait(1))
                self.assert_responsive(job['id'])
                self.assertFalse(dispatched.is_set())
                self.assertEqual(json.loads((self.jobs.directory / (job['id'] + '.json')).read_text())['state'], 'running')
                before = list(snapshots[0]['output'])
                outcome, done, thread = self.call_async(lambda: self.jobs.event({'phase': 'too-late'}))
                self.assertTrue(done.wait(1), 'late event waited on storage')
                thread.join(1)
                self.assertNotIn('error', outcome)
                self.assertEqual(snapshots[0]['output'], before)
            finally:
                release.set()
            self.assertTrue(dispatched.wait(2))
            final = self.wait_finished(job['id'])
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][0], final)
        self.assertEqual(observed[0][1], final['result'])
        self.assertEqual(len(final['output']), 1)
        self.assertIn('action-finished', final['output'][0])
        self.assertIsNone(self.jobs.active)

    def test_initial_failure_rolls_back_only_reservation_and_never_starts_action(self):
        # Failure must not evict existing history, even when reservation is #33.
        self.jobs.records = {f'old-{i}': {'id': f'old-{i}', 'state': 'completed', 'output': []}
                             for i in range(32)}
        previous = json.loads(json.dumps(self.jobs.records))
        action, dispatch = mock.Mock(), mock.Mock()
        real_save = APP.diagnostics.atomic_json
        for commit_before_failure in (False, True):
            with self.subTest(commit_before_failure=commit_before_failure):
                def fail(path, value):
                    if commit_before_failure:
                        real_save(path, value)
                    raise OSError('synthetic initial durability failure')
                with mock.patch.object(APP.diagnostics, 'atomic_json', side_effect=fail), \
                        self.assertRaisesRegex(OSError, 'initial durability'):
                    self.jobs.submit('Not started', action, after_save=dispatch)
                self.assertIsNone(self.jobs.active)
                self.assertEqual(self.jobs.records, previous)
        action.assert_not_called()
        dispatch.assert_not_called()

    def test_final_save_failure_never_publishes_completed_or_dispatches(self):
        entered, release = threading.Event(), threading.Event()
        real_save, dispatch = APP.diagnostics.atomic_json, mock.Mock()
        def save(path, value):
            if value['state'] == 'completed':
                entered.set()
                release.wait(3)
                raise OSError('synthetic final fsync failure')
            return real_save(path, value)
        with mock.patch.object(APP.diagnostics, 'atomic_json', side_effect=save):
            job = self.jobs.submit('Final failure', lambda: {'status': 'PASS'}, after_save=dispatch)
            try:
                self.assertTrue(entered.wait(1))
                self.assert_responsive(job['id'])
                dispatch.assert_not_called()
            finally:
                release.set()
            final = self.wait_finished(job['id'])
        self.assertEqual(final['state'], 'failed')
        self.assertIn('could not be saved', final['error'])
        self.assertEqual(json.loads((self.jobs.directory / (job['id'] + '.json')).read_text())['state'], 'running')
        self.assertIsNone(self.jobs.active)
        dispatch.assert_not_called()

    def test_worker_failure_is_saved_and_never_dispatches(self):
        dispatch = mock.Mock()
        def action():
            raise ValueError('synthetic action failure')
        job = self.jobs.submit('Action failure', action, after_save=dispatch)
        final = self.wait_finished(job['id'])
        self.assertEqual(final['state'], 'failed')
        self.assertEqual(final['error'], 'synthetic action failure')
        self.assertEqual(json.loads((self.jobs.directory / (job['id'] + '.json')).read_text()), final)
        dispatch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
