#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Fake SSH/pipes/clocks and synthetic saved stages; never connects to a host."""
import argparse
import contextlib
import copy
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import platform
import selectors
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


h = module('tested_live_prepare_host_collector', HERE / 'collect_host.py')
tf = module('host_collector_saved_stage_fixture', HERE / 'test_extract.py')


def utc(second): return dt.datetime(2099, 1, 1, 0, 0, second, tzinfo=dt.timezone.utc)


def complete_outcome(program, stdout, stderr=b'', returncode=0):
    return {'stdout': stdout, 'stderr': stderr, 'stdout_eof': True, 'stderr_eof': True,
            'input_bytes_sent': len(program), 'stdin_closed': True, 'spawned': True,
            'returncode': returncode, 'timed_out': False, 'overflow_stream': None,
            'signals_sent_to_own_child': [], 'errors': []}


class SavedStageTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='fake-host-capture-'))).resolve()
        # Extra role is staged by the actual synthetic stage fixture. The
        # production binder's dependency table is never edited.
        deps = {**tf.e.DEPENDENCIES, 'host_transport': ('handoff/collect_host.py', h.sha(Path(h.__file__).read_bytes()))}
        self.stack.enter_context(patch.object(tf.e, 'DEPENDENCIES', deps))
        self.stack.enter_context(patch.object(h, 'e', tf.e))
        self.f = tf.Fixture(self.root, self.stack)
        self.launch = self.root / 'operator-launch.json'; tf.put(self.launch, self.f.launch)
        self.destination = self.root / 'capture'
        self.calls = []; self.stdout = tf.dumped(self.f.bracket()); self.stderr = b''
        self.outcome_changes = {}; self.times = [utc(29), utc(30), utc(31)]; self.monos = [100, 200, 300]

    def runner(self, argv, program, **kwargs):
        self.calls.append((argv, program, kwargs))
        return {**complete_outcome(program, self.stdout, self.stderr), **self.outcome_changes}

    def collect(self, **changes):
        values = dict(reviewed_sha256=self.f.review_sha, plan_sha256=self.f.plan_sha,
                      launch_report=self.launch, ssh_target='synthetic-user@synthetic-host',
                      status_path=tf.STATUS_PATH, output_directory=self.destination,
                      runner=self.runner, utc_now=lambda: self.times.pop(0), monotonic_ns=lambda: self.monos.pop(0))
        values.update(changes)
        return h.collect(self.f.stage, **values)

    def refused(self, **changes):
        with self.assertRaises((ValueError, RuntimeError, OSError, tf.e.BindingError)):
            self.collect(**changes)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.destination.exists())

    def failed(self, result):
        self.assertEqual(result['status'], 'HOST_COLLECTION_INCOMPLETE', result)
        self.assertFalse(result['hardware_acceptance']); self.assertFalse(result['semantic_evaluation_performed'])
        self.assertFalse((self.destination / 'transport.json').exists())
        return json.loads((self.destination / 'TRANSPORT-FAILURE.json').read_bytes())

    def test_exact_schema_renderer_streams_and_binder_interoperation(self):
        result = self.collect()
        self.assertEqual(result['status'], 'SSH_TRANSPORT_RECORDED'); self.assertTrue(result['transport_process_succeeded'])
        self.assertFalse(result['hardware_acceptance']); self.assertFalse(result['semantic_evaluation_performed'])
        self.assertEqual(len(self.calls), 1)
        argv, program, bounds = self.calls[0]
        self.assertEqual(bounds, dict(stdout_limit=h.STDOUT_LIMIT, stderr_limit=h.STDERR_LIMIT, timeout_seconds=h.TIMEOUT_SECONDS))
        expected_remote = tf.e.remote_argv(self.f.plan['release'], tf.BDF, tf.STATUS_PATH)
        self.assertEqual(shlex.split(argv[-1]), expected_remote); self.assertEqual(argv[:-3], h.SSH_PREFIX)
        self.assertEqual(argv[-3:-1], ['--', 'synthetic-user@synthetic-host'])
        self.assertEqual(program, tf.e.j.render_remote_program(self.f.context['sources']['acquisition'], self.f.context['sources']['journal_collector']))
        self.assertEqual((self.destination / 'program.py').read_bytes(), program)
        self.assertEqual((self.destination / 'operator-launch.json').read_bytes(), self.launch.read_bytes())
        self.assertEqual((self.destination / 'stdout.bin').read_bytes(), self.stdout)
        self.assertEqual((self.destination / 'stderr.bin').read_bytes(), b'')
        receipt = json.loads((self.destination / 'transport.json').read_bytes())
        self.assertEqual(set(receipt), tf.e.TRANSPORT_KEYS); self.assertEqual(receipt['returncode'], 0)
        self.assertEqual(receipt['started_at_utc'], utc(30).isoformat())
        self.assertEqual(receipt['ended_at_utc'], utc(31).isoformat())
        self.assertEqual((receipt['started_monotonic_ns'], receipt['ended_monotonic_ns']), (200, 300))
        linked = tf.e._launch_and_transport(self.f.context, self.f.launch, receipt, self.stdout, b'')
        self.assertTrue(linked['transport_succeeded'])
        bound = self.f.analyze(transport=receipt, bracket=json.loads(self.stdout))
        self.assertTrue(bound['preparation_userspace_observed'], bound)
        self.assertFalse(bound['hardware_acceptance'])
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in self.destination.iterdir()))

    def test_process_zero_is_recorded_without_semantic_success(self):
        self.stdout = b'not a cached observation\n'
        result = self.collect()
        self.assertTrue(result['transport_process_succeeded'])
        self.assertFalse(result['semantic_evaluation_performed'])
        self.assertFalse(result['hardware_acceptance'])

    def test_nonzero_actual_status_is_recorded_without_claim(self):
        self.outcome_changes['returncode'] = 255; self.stderr = b'synthetic connection failure\n'; self.stdout = b''
        result = self.collect()
        self.assertEqual(result['returncode'], 255); self.assertFalse(result['transport_process_succeeded'])
        receipt = json.loads((self.destination / 'transport.json').read_bytes())
        self.assertEqual(receipt['returncode'], 255)
        self.assertEqual((self.destination / 'stderr.bin').read_bytes(), self.stderr)

    def test_timeout_cannot_create_transport_even_if_child_exited_zero(self):
        self.outcome_changes.update(timed_out=True, returncode=0)
        failure = self.failed(self.collect())
        self.assertEqual(failure['observed_transport']['returncode'], 0)
        self.assertTrue(failure['execution']['timed_out'])
        self.assertEqual((self.destination / 'stdout.bin').read_bytes(), self.stdout)

    def test_overflow_retains_limit_plus_one_and_no_normal_receipt(self):
        self.stdout = b's' * (h.STDOUT_LIMIT + 33)
        self.outcome_changes.update(overflow_stream='stdout', stdout_eof=False)
        failure = self.failed(self.collect())
        self.assertEqual(len((self.destination / 'stdout.bin').read_bytes()), h.STDOUT_LIMIT + 1)
        self.assertEqual(failure['observed_transport']['stdout_bytes'], h.STDOUT_LIMIT + 1)

    def test_spawn_failure_preserves_actual_absent_returncode(self):
        self.stdout = b''
        self.outcome_changes.update(spawned=False, returncode=None, stdin_closed=False, input_bytes_sent=0,
                                    errors=[{'exception': 'OSError', 'detail': 'synthetic spawn failure'}])
        failure = self.failed(self.collect())
        self.assertIsNone(failure['observed_transport']['returncode'])
        self.assertFalse(failure['execution']['spawned'])

    def test_missing_eof_or_incomplete_stdin_is_failure_only(self):
        self.outcome_changes.update(stdout_eof=False, input_bytes_sent=10, stdin_closed=False)
        failure = self.failed(self.collect())
        self.assertEqual(failure['execution']['input_bytes_sent'], 10)

    def test_end_clock_exception_retains_exact_streams_before_clock_read(self):
        self.stderr = b'preserved stderr\n'
        def clock():
            if len(self.times) == 1:
                self.assertEqual((self.destination / 'stdout.bin').read_bytes(), self.stdout)
                self.assertEqual((self.destination / 'stderr.bin').read_bytes(), self.stderr)
                raise OSError('synthetic end clock unavailable')
            return self.times.pop(0)
        failure = self.failed(self.collect(utc_now=clock))
        self.assertEqual(failure['end_clock_error']['exception'], 'OSError')
        self.assertEqual(failure['execution']['returncode'], 0)
        self.assertIsNone(failure['observed_transport']['ended_at_utc'])

    def test_invalid_end_clock_still_preserves_streams(self):
        self.times[-1] = dt.datetime(2099, 1, 1)
        self.failed(self.collect())
        self.assertEqual((self.destination / 'stdout.bin').read_bytes(), self.stdout)

    def test_regressed_end_monotonic_is_failure_only(self):
        self.monos[-1] = 199; self.failed(self.collect())

    def test_regressed_end_utc_is_failure_only(self):
        self.times[-1] = utc(29); self.failed(self.collect())

    def test_wrong_review_hash_prevents_call(self): self.refused(reviewed_sha256='0' * 64)
    def test_wrong_plan_hash_prevents_call(self): self.refused(plan_sha256='0' * 64)

    def test_wrong_stage_release_prevents_call(self):
        path = self.f.stage / 'plan.json'
        row = json.loads(path.read_bytes()); row['release'] = 'wrong-release'; tf.put(path, row)
        self.refused()

    def test_staged_acquire_source_change_prevents_call(self):
        path = self.f.stage / 'observation-tools/acquisition.source'; path.write_bytes(path.read_bytes() + b'\n')
        self.refused()

    def test_staged_collector_source_change_prevents_call(self):
        path = self.f.stage / 'observation-tools/host_transport.source'; path.write_bytes(path.read_bytes() + b'\n')
        self.refused()

    def test_failed_install_receipt_prevents_call(self):
        path = self.f.runtime / 'install-complete.json'
        row = json.loads(path.read_bytes()); row['status'] = 'INCOMPLETE'; tf.put(path, row)
        self.refused()

    def test_wrong_launch_identity_prevents_call(self):
        launch = copy.deepcopy(self.f.launch); launch['experiment'] = 'EXP-20990101-997'
        self.assertNotEqual(launch['experiment'], self.f.plan['experiment']); tf.put(self.launch, launch)
        self.refused()

    def test_launch_count_and_full_poweroff_required(self):
        original = self.launch.read_bytes()
        for key, value in (('launch_count', 2), ('launch_count', True),
                           ('full_poweroff_since_retained_experiment', False),
                           ('reporter', 'collector'), ('hardware_acceptance', True)):
            with self.subTest(key=key, value=value):
                launch = json.loads(original); launch[key] = value; tf.put(self.launch, launch); self.refused()

    def test_launch_before_install_or_in_future_prevents_call(self):
        original = self.launch.read_bytes()
        for second in (0, 59):
            launch = json.loads(original); launch['recorded_at_utc'] = utc(second).isoformat(); tf.put(self.launch, launch)
            self.refused()

    def test_duplicate_launch_json_keys_prevents_call(self):
        raw = self.launch.read_bytes().replace(b'"launch_count": 1', b'"launch_count": 1, "launch_count": 1')
        self.launch.write_bytes(raw); self.refused()

    def test_launch_changed_after_preflight_prevents_call(self):
        def clock():
            self.launch.write_bytes(self.launch.read_bytes() + b'\n')
            return self.times.pop(0)
        self.failed(self.collect(utc_now=clock)); self.assertEqual(self.calls, [])

    def test_preflight_clock_failure_has_no_outputs(self):
        self.refused(utc_now=lambda: dt.datetime(2099, 1, 1))

    def test_pre_call_clock_regression_prevents_call(self):
        self.monos[1] = 99; self.failed(self.collect()); self.assertEqual(self.calls, [])

    def test_existing_destination_is_untouched(self):
        self.destination.mkdir(); marker = self.destination / 'original'; marker.write_bytes(b'keep')
        with self.assertRaises(tf.e.BindingError): self.collect()
        self.assertEqual(self.calls, []); self.assertEqual(marker.read_bytes(), b'keep')
        self.assertEqual(list(self.destination.iterdir()), [marker])

    def test_symlink_input_refused(self):
        target = self.root / 'launch-link'; target.symlink_to(self.launch)
        self.refused(launch_report=target)

    def test_output_inside_immutable_stage_refused(self):
        self.refused(output_directory=self.f.stage / 'capture')
        self.assertFalse((self.f.stage / 'capture').exists())

    def test_explicit_numeric_path_required(self):
        for value in ('/sys/kernel/debug/dri/card0/amdgpu_ps4_live_prepare',
                      '/sys/kernel/debug/dri/07/amdgpu_ps4_live_prepare',
                      '/sys/kernel/debug/dri/7/../amdgpu_ps4_live_prepare',
                      '/sys/kernel/debug/dri/7/name'):
            with self.subTest(value=value): self.refused(status_path=value)

    def test_endpoint_options_whitespace_and_shell_text_refused(self):
        for value in ('-oProxyCommand=anything', 'name host', 'user@host;echo', 'user@$(echo)', 'ssh://host',
                      'user@host\n', 'user@host:22', '@host', 'user@@host', 'host`x`'):
            with self.subTest(value=value): self.refused(ssh_target=value)

    def test_exclusive_write_prevents_overwriting(self):
        file = self.root / 'exclusive'; h.write_new(file, b'first')
        with self.assertRaises(FileExistsError): h.write_new(file, b'second')
        self.assertEqual(file.read_bytes(), b'first')


class Pipe:
    def __init__(self, fd): self.fd = fd; self.closed = False
    def fileno(self): return self.fd
    def close(self): self.closed = True


class Child:
    def __init__(self, calls, alive=False, ignore_term=False, returncode=0):
        self.stdin, self.stdout, self.stderr = Pipe(300), Pipe(301), Pipe(302)
        self.calls, self.alive, self.ignore_term = calls, alive, ignore_term
        self.returncode = None if alive else returncode
    def poll(self): return None if self.alive else self.returncode
    def wait(self, timeout):
        self.calls.append(('wait', timeout))
        if self.alive: raise subprocess.TimeoutExpired('/usr/bin/ssh', timeout)
        return self.returncode
    def terminate(self):
        self.calls.append(('terminate-own-child',))
        if not self.ignore_term: self.alive = False; self.returncode = -15
    def kill(self): self.calls.append(('kill-own-child',)); self.alive = False; self.returncode = -9


class Selector:
    def __init__(self, f): self.f = f; self.keys = {}; self.rounds = 0
    def register(self, stream, event, data): self.keys[stream.fd] = SimpleNamespace(fileobj=stream, data=data, events=event)
    def get_map(self): return self.keys
    def select(self, timeout):
        self.rounds += 1
        if self.f.stall_after is not None and self.rounds > self.f.stall_after: return []
        return [(key, key.events) for key in list(self.keys.values())]
    def unregister(self, stream): del self.keys[stream.fd]
    def close(self): self.f.calls.append(('selector-close',))


class SshFakes:
    def __init__(self, stdout=b'synthetic output\n', stderr=b'', alive=False, ignore_term=False, returncode=0):
        self.calls = []; self.data = {301: bytearray(stdout), 302: bytearray(stderr)}
        self.child = Child(self.calls, alive, ignore_term, returncode)
        self.time = 0.0; self.step = .001; self.stall_after = None
        self.spawn_error = self.read_error = self.write_error = None
        self.block_write_once = False; self.max_write = 1000; self.written = bytearray()
        self.program = b'synthetic Python stdin bytes\n' * 300
    def mono(self): self.time += self.step; return self.time
    def popen(self, argv, **kwargs):
        self.calls.append(('popen', argv, kwargs))
        if self.spawn_error: raise self.spawn_error
        return self.child
    def read(self, fd, count):
        if self.read_error: raise self.read_error
        data = bytes(self.data[fd][:count]); del self.data[fd][:count]; return data
    def write(self, fd, raw):
        if self.write_error: raise self.write_error
        if self.block_write_once: self.block_write_once = False; raise BlockingIOError('synthetic backpressure')
        assert fd == 300
        data = raw[:self.max_write]; self.written.extend(data); return len(data)
    def run(self, **changes):
        remote = h.e.remote_argv('7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1', tf.BDF, tf.STATUS_PATH)
        kwargs = dict(popen=self.popen, selector_factory=lambda: Selector(self), read_fn=self.read,
                      write_fn=self.write, set_blocking_fn=lambda fd, flag: self.calls.append(('blocking', fd, flag)),
                      monotonic=self.mono)
        kwargs.update(changes)
        with patch.object(h.os, 'kill', side_effect=AssertionError('No other PID action')), \
             patch.object(h.subprocess, 'Popen', side_effect=AssertionError('No real child')):
            return h.run_ssh(h.ssh_argv('synthetic-host', remote), self.program, **kwargs)


class PipeTests(unittest.TestCase):
    def test_fixed_one_child_partial_stdin_eofs_and_cleanup(self):
        f = SshFakes(stderr=b'synthetic diagnostic'); result = f.run()
        self.assertEqual(result['stdout'], b'synthetic output\n'); self.assertEqual(result['stderr'], b'synthetic diagnostic')
        self.assertEqual(result['returncode'], 0); self.assertEqual(result['errors'], [])
        self.assertEqual(result['input_bytes_sent'], len(f.program)); self.assertEqual(f.written, f.program)
        self.assertTrue(result['stdin_closed']); self.assertTrue(result['stdout_eof']); self.assertTrue(result['stderr_eof'])
        calls = [row for row in f.calls if row[0] == 'popen']; self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, bufsize=0))
        self.assertNotIn('shell', calls[0][2]); self.assertEqual(result['signals_sent_to_own_child'], [])
        self.assertEqual([row for row in f.calls if row[0] == 'blocking'], [('blocking', n, False) for n in (300, 301, 302)])
        self.assertTrue(all(getattr(f.child, name).closed for name in ('stdin', 'stdout', 'stderr')))

    def test_stdin_backpressure_keeps_exact_program(self):
        f = SshFakes(); f.block_write_once = True; result = f.run()
        self.assertEqual(f.written, f.program); self.assertEqual(result['errors'], [])

    def test_stdout_overflow_preserves_prefix_and_terminates_owned_child(self):
        f = SshFakes(stdout=b'x' * (h.STDOUT_LIMIT + 50), alive=True); result = f.run()
        self.assertEqual(result['stdout'], b'x' * (h.STDOUT_LIMIT + 1))
        self.assertEqual(result['overflow_stream'], 'stdout'); self.assertEqual(result['signals_sent_to_own_child'], ['SIGTERM'])
        self.assertEqual(result['returncode'], -15)

    def test_stderr_overflow_preserves_prefix(self):
        f = SshFakes(stderr=b'e' * (h.STDERR_LIMIT + 30), alive=True); result = f.run()
        self.assertEqual(len(result['stderr']), h.STDERR_LIMIT + 1); self.assertEqual(result['overflow_stream'], 'stderr')

    def test_timeout_keeps_prefix_and_kills_only_unresponsive_owned_child(self):
        f = SshFakes(alive=True, ignore_term=True); f.stall_after = 1; f.step = 10
        result = f.run()
        self.assertTrue(result['timed_out']); self.assertEqual(result['stdout'], b'synthetic output\n')
        self.assertEqual(result['signals_sent_to_own_child'], ['SIGTERM', 'SIGKILL']); self.assertEqual(result['returncode'], -9)

    def test_wait_timeout_after_eofs_keeps_actual_termination_status(self):
        f = SshFakes(alive=True); result = f.run()
        self.assertTrue(result['stdout_eof']); self.assertTrue(result['stderr_eof'])
        self.assertTrue(result['timed_out']); self.assertEqual(result['returncode'], -15)

    def test_spawn_failure_does_not_signal_or_invent_returncode(self):
        f = SshFakes(); f.spawn_error = FileNotFoundError('synthetic ssh unavailable'); result = f.run()
        self.assertFalse(result['spawned']); self.assertIsNone(result['returncode'])
        self.assertEqual(result['signals_sent_to_own_child'], []); self.assertEqual(result['errors'][0]['exception'], 'FileNotFoundError')

    def test_broken_stdin_is_not_complete(self):
        f = SshFakes(alive=True); f.write_error = BrokenPipeError('synthetic early exit'); result = f.run()
        self.assertFalse(result['stdin_closed']); self.assertEqual(result['errors'][0]['exception'], 'BrokenPipeError')
        self.assertEqual(result['signals_sent_to_own_child'], ['SIGTERM'])

    def test_read_failure_keeps_error_and_cleans_own_child(self):
        f = SshFakes(alive=True); f.read_error = OSError('synthetic read failure'); result = f.run()
        self.assertEqual(result['errors'][0]['exception'], 'OSError'); self.assertEqual(result['returncode'], -15)

    def test_negative_child_returncode_is_not_rewritten(self):
        f = SshFakes(returncode=-11); result = f.run(); self.assertEqual(result['returncode'], -11)

    def test_regressed_clock_prevents_spawn(self):
        f = SshFakes(); result = f.run(monotonic=lambda: float('nan'))
        self.assertFalse(result['spawned']); self.assertEqual(result['errors'][0]['exception'], 'ValueError')

    def test_changed_command_or_bounds_prevent_spawn(self):
        f = SshFakes()
        with self.assertRaises(ValueError): f.run(timeout_seconds=31)
        self.assertEqual(f.calls, [])
        remote = h.e.remote_argv('release', tf.BDF, tf.STATUS_PATH)
        argv = h.ssh_argv('synthetic-host', remote); argv[1] = '-t'
        with self.assertRaises(ValueError): h.run_ssh(argv, b'program', popen=f.popen)
        self.assertEqual(f.calls, [])

    def test_dns_alias_ip_and_ipv6_argv_roundtrip(self):
        remote = h.e.remote_argv('release', tf.BDF, tf.STATUS_PATH)
        for target in ('synthetic-host', 'user@192.0.2.5', 'host.example', 'user@[2001:db8::1]', '2001:db8::1'):
            argv = h.ssh_argv(target, remote)
            self.assertEqual(argv[-2], target); self.assertEqual(shlex.split(argv[-1]), remote)
            self.assertEqual(argv[-3], '--')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True); args = parser.parse_args()
    if args.report.exists(): raise SystemExit('Refusing an existing report')
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in (SavedStageTests, PipeTests))
    stream = io.StringIO(); result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite); print(stream.getvalue(), end='')
    receipt = {'schema': 1, 'status': 'PASS_SYNTHETIC_HOST_SSH_COLLECTION' if result.wasSuccessful() else 'FAIL_SYNTHETIC_HOST_SSH_COLLECTION',
               'cases': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
               'host': {'system': platform.system(), 'machine': platform.machine()},
               'collector_sha256': h.sha(Path(h.__file__).read_bytes()), 'tests_sha256': h.sha(Path(__file__).read_bytes()),
               'extractor_sha256': h.EXTRACT_SHA256, 'journal_source_sha256': tf.e.DEPENDENCIES['journal_collector'][1],
               'real_ssh_calls': 0, 'hardware_actions': False, 'synthetic_saved_files_only': True,
               'host_archive_validator_uses_zstd_on_synthetic_archive': True, 'log': stream.getvalue()}
    with args.report.open('x') as output: json.dump(receipt, output, indent=2, sort_keys=True); output.write('\n')
    raise SystemExit(not result.wasSuccessful())
