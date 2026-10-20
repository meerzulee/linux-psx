#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline fake-only boot-bracket tests: no live proc/sys, journal or SSH."""
import argparse
import base64
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import selectors
import stat
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('journal_bracket_under_test', HERE / 'journal_bracket.py')
j = importlib.util.module_from_spec(spec); spec.loader.exec_module(j)
ACQUIRE_SOURCE = (HERE / 'acquire.py').read_bytes(); WRAPPER_SOURCE = (HERE / 'journal_bracket.py').read_bytes()
a = j.load_acquire_source(ACQUIRE_SOURCE)
RELEASE = '7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1'
BDF = '0000:00:01.0'
STATUS = '/sys/kernel/debug/dri/7/amdgpu_ps4_live_prepare'
NAME = '/sys/kernel/debug/dri/7/name'
BOOT = b'f65c99c1-5163-4fef-87cb-a83763f02e55\n'
OTHER_BOOT = b'ce1735b4-43b4-45ba-b8ad-71585eb55554\n'
RAW_JOURNAL = b'{"MESSAGE":"PS4_LIVE_PREPARE PREPARED synthetic","_BOOT_ID":"f65c99c151634fef87cba83763f02e55","_TRANSPORT":"kernel","__CURSOR":"synthetic"}\n'


def query_result(stdout=RAW_JOURNAL, stderr=b'', **changes):
    return {'stdout': stdout, 'stderr': stderr, 'stdout_eof': True, 'stderr_eof': True, 'returncode': 0,
            'spawned': True, 'timed_out': False, 'overflow_stream': None, 'signals_sent_to_own_child': [], 'errors': [], **changes}


class Workflow:
    def __init__(self):
        self.calls = []; self.tick = 0; self.wall_tick = 100000
        self.outer = {j.BOOT_ID: [BOOT, BOOT], j.UPTIME: [b'12.30 20.00\n', b'12.33 20.09\n'], j.PID1_COMM: [b'systemd\n']}
        self.releases = [RELEASE.encode(), RELEASE.encode()]
        self.inner = {a.BOOT_ID: [BOOT] * 3, a.CMDLINE: [b'debugfs=on amdgpu.ps4_uvd_live_prepare=1\n'], a.UPTIME: [b'12.31 20.01\n', b'12.32 20.02\n'],
                      NAME: [b'amdgpu dev=0000:00:01.0\n'] * 2, STATUS: [b'synthetic cached bytes; no semantic PREPARED assertion\n']}
        self.inner_releases = [RELEASE.encode()] * 3; self.outcome = query_result(); self.producer_error = None; self.change_acquisition = None
    @staticmethod
    def take(values):
        value = values.pop(0)
        if isinstance(value, Exception): raise value
        return value
    def reader(self, path, limit): self.calls.append(('outer_read', path, limit)); return self.take(self.outer[path])
    def uname(self): self.calls.append(('outer_uname',)); return self.take(self.releases)
    def mono(self): self.tick += 100; return self.tick
    def wall(self): self.wall_tick += 100; return self.wall_tick
    def inner_reader(self, path, limit): self.calls.append(('inner_read', path, limit)); return self.take(self.inner[path])
    def producer(self):
        self.calls.append(('producer',))
        if self.producer_error: raise self.producer_error
        result = a.acquire(expected_release=RELEASE, expected_pci_bdf=BDF, status_path=STATUS, reader=self.inner_reader,
                           uname=lambda: self.take(self.inner_releases), monotonic_ns=self.mono, wall_time_ns=self.wall)
        if self.change_acquisition: self.change_acquisition(result)
        return result
    def journal(self, argv, **kwargs): self.calls.append(('journal', argv, kwargs)); return self.outcome
    def capture(self, **changes):
        values = dict(expected_release=RELEASE, expected_pci_bdf=BDF, status_path=STATUS, producer=self.producer, reader=self.reader, uname=self.uname,
                      monotonic_ns=self.mono, wall_time_ns=self.wall, journal_runner=self.journal, include_pid1_comm=True)
        values.update(changes)
        with patch.object(j.subprocess, 'Popen', side_effect=AssertionError('No real child permitted')), patch.object(j.os, 'open', side_effect=AssertionError('No live read permitted')), patch.object(j.os, 'uname', side_effect=AssertionError('No live uname permitted')):
            return j.collect(**values)


class FlowTests(unittest.TestCase):
    def raw(self, record):
        data = base64.b64decode(record['base64'], validate=True)
        self.assertEqual(j.sha(data), record['sha256']); self.assertEqual(len(data), record['bytes']); return data
    def test_one_acquisition_query_and_outer_brackets(self):
        f = Workflow(); r = f.capture()
        self.assertEqual(r['status'], 'CAPTURED_BOOT_CORRELATION_INPUTS'); self.assertTrue(r['source_complete']); self.assertTrue(r['complete']); self.assertTrue(r['identity_consistent'])
        self.assertFalse(r['hardware_acceptance']); self.assertFalse(r['semantic_evaluation_performed']); self.assertFalse(r['binary_attestation'])
        self.assertEqual(r['producer_calls'], 1); self.assertEqual(sum(x[0] == 'journal' for x in f.calls), 1)
        self.assertEqual(sum(x[:2] == ('inner_read', STATUS) for x in f.calls), 1)
        self.assertEqual(set(r['observations']), {'boot_id_before', 'uname_before', 'uptime_before', 'pid1_comm', 'boot_id_after', 'uname_after', 'uptime_after'})
        self.assertEqual(r['observations']['boot_id_before']['parsed'], BOOT.decode().strip())
        self.assertEqual(self.raw(r['journal']['stdout']), RAW_JOURNAL); self.assertEqual(self.raw(r['observations']['pid1_comm']['raw']), b'systemd\n')
        names = list(r['timestamps']); self.assertEqual(names, ['outer_start', 'before_acquisition', 'after_acquisition', 'before_journal', 'after_journal', 'outer_end'])
        self.assertLess(r['timestamps']['before_acquisition']['monotonic_ns'], r['acquisition']['timestamps']['acquisition_start']['monotonic_ns'])
        self.assertLess(r['acquisition']['timestamps']['acquisition_end']['monotonic_ns'], r['timestamps']['after_acquisition']['monotonic_ns'])
        self.assertEqual([x[0] for x in f.calls][-3:], ['outer_read', 'outer_uname', 'outer_read'])
    def test_exact_explicit_boot_query_no_discovery_or_follow(self):
        f = Workflow(); r = f.capture(); argv = r['journal']['argv']
        self.assertEqual(argv, ['/usr/bin/journalctl', '--boot=f65c99c151634fef87cba83763f02e55', '_TRANSPORT=kernel', '--no-pager', '--output=json', '--all', '--output-fields=MESSAGE,_BOOT_ID,_TRANSPORT', '--grep=PS4_MAP_CANARY (BEGIN|READY_LAYOUT|MAP)|PS4_LIVE_PREPARE PREPARED', '--lines=64'])
    def test_wrong_initial_release_or_boot_prevents_producer_and_query(self):
        for kind in ('release', 'boot'):
            f = Workflow()
            if kind == 'release': f.releases[0] = b'wrong-release'
            else: f.outer[j.BOOT_ID][0] = b'not-a-uuid\n'
            r = f.capture(); self.assertEqual(r['producer_calls'], 0); self.assertFalse(r['journal_attempted']); self.assertFalse(r['complete']); self.assertIn('boot_id_after', r['observations'])
    def test_final_boot_or_release_change_preserves_all_source_bytes(self):
        for kind in ('release', 'boot'):
            f = Workflow()
            if kind == 'release': f.releases[1] = b'wrong-release'
            else: f.outer[j.BOOT_ID][1] = OTHER_BOOT
            r = f.capture(); self.assertTrue(r['source_complete']); self.assertFalse(r['complete']); self.assertFalse(r['identity_consistent']); self.assertEqual(self.raw(r['journal']['stdout']), RAW_JOURNAL)
    def test_inner_identity_release_or_bdf_failure_suppresses_query(self):
        variants = [lambda r: r.update(identity_consistent=False), lambda r: r.update(expected_release='wrong'), lambda r: r['observations']['drm_name_after']['parsed'].update(pci_bdf='0000:00:02.0'), lambda r: r['issues'].append({'code': 'DRM_PCI_BDF_MISMATCH'})]
        for change in variants:
            f = Workflow(); f.change_acquisition = change; r = f.capture()
            self.assertIsNotNone(r['acquisition']); self.assertFalse(r['journal_attempted']); self.assertFalse(r['complete']); self.assertIn('boot_id_after', r['observations'])
    def test_missing_cached_node_preserved_and_no_retry(self):
        f = Workflow(); f.inner[STATUS] = [PermissionError('synthetic access denied')]; r = f.capture()
        self.assertFalse(r['complete']); self.assertFalse(r['source_complete']); self.assertTrue(r['journal_attempted'])
        self.assertFalse(r['acquisition']['complete']); self.assertEqual(sum(x[:2] == ('inner_read', STATUS) for x in f.calls), 1)
    def test_producer_exception_preserves_end_identity_without_journal(self):
        f = Workflow(); f.producer_error = RuntimeError('synthetic producer failure'); r = f.capture()
        self.assertFalse(r['journal_attempted']); self.assertIsNone(r['acquisition']); self.assertIn('uptime_after', r['observations'])
    def test_journal_empty_is_incomplete_but_reads_reached_eof(self):
        f = Workflow(); f.outcome = query_result(stdout=b''); r = f.capture()
        self.assertTrue(r['source_complete']); self.assertFalse(r['complete']); self.assertIn({'code': 'JOURNAL_EMPTY'}, r['issues'])
    def test_journal_failure_timeout_and_overflow_preserve_prefixes(self):
        for changes in ({'returncode': 1}, {'timed_out': True, 'stdout_eof': False}, {'overflow_stream': 'stdout', 'stdout_eof': False}, {'signals_sent_to_own_child': ['SIGTERM']}):
            f = Workflow(); f.outcome = query_result(stdout=b'partial\xff', stderr=b'warning\x00', **changes); r = f.capture()
            self.assertFalse(r['complete']); self.assertFalse(r['source_complete']); self.assertEqual(self.raw(r['journal']['stdout']), b'partial\xff'); self.assertEqual(self.raw(r['journal']['stderr']), b'warning\x00')
    def test_raw_journal_json_null_arrays_metadata_and_order_are_opaque(self):
        payload = b'{"MESSAGE":["later","again"],"_BOOT_ID":null,"_TRANSPORT":[107,101,114,110,101,108],"__CURSOR":"z"}\n{"MESSAGE":"earlier","__REALTIME_TIMESTAMP":"10"}\n'
        f = Workflow(); f.outcome = query_result(stdout=payload); r = f.capture()
        self.assertTrue(r['complete']); self.assertFalse(r['semantic_evaluation_performed']); self.assertEqual(self.raw(r['journal']['stdout']), payload)
    def test_sixty_four_entries_preserved_without_semantic_acceptance(self):
        f = Workflow(); f.outcome = query_result(stdout=RAW_JOURNAL * 64); r = f.capture()
        self.assertTrue(r['source_complete']); self.assertEqual(self.raw(r['journal']['stdout']), RAW_JOURNAL * 64); self.assertFalse(r['hardware_acceptance'])
    def test_pid1_other_value_is_not_a_source_failure_or_systemd_claim(self):
        f = Workflow(); f.outer[j.PID1_COMM] = [b'other-init\n']; r = f.capture()
        self.assertTrue(r['source_complete']); self.assertEqual(self.raw(r['observations']['pid1_comm']['raw']), b'other-init\n'); self.assertIsNone(r['observations']['pid1_comm']['parsed'])
    def test_pid1_optional_and_permission_failure(self):
        f = Workflow(); r = f.capture(include_pid1_comm=False); self.assertTrue(r['complete']); self.assertNotIn('pid1_comm', r['observations'])
        f = Workflow(); f.outer[j.PID1_COMM] = [PermissionError('denied')]; r = f.capture(); self.assertFalse(r['source_complete']); self.assertTrue(r['journal_attempted'])
    def test_partial_outer_read_and_oversize_are_bounded(self):
        for value in (j.ReadFailure('read failed', partial=b'partial'), b'x' * 100):
            f = Workflow(); f.outer[j.BOOT_ID][0] = value; r = f.capture()
            raw = r['observations']['boot_id_before']['raw']; self.assertFalse(raw['source_complete']); self.assertLessEqual(raw['bytes'], 65); self.assertEqual(r['producer_calls'], 0)
    def test_uptime_regression_and_noncanonical_end(self):
        for value in (b'1.00 2.00\n', b'broken\n'):
            f = Workflow(); f.outer[j.UPTIME][1] = value; r = f.capture(); self.assertFalse(r['identity_consistent']); self.assertFalse(r['complete'])
    def test_monotonic_regression_refuses_further_query(self):
        f = Workflow(); calls = 0
        def clock():
            nonlocal calls
            calls += 1; return 1000 if calls == 1 else 0
        r = f.capture(monotonic_ns=clock); self.assertFalse(r['journal_attempted']); self.assertFalse(r['complete'])
    def test_invalid_request_has_no_dependencies(self):
        fail = lambda *a, **k: (_ for _ in ()).throw(AssertionError('No dependency allowed'))
        r = j.collect(expected_release=RELEASE, expected_pci_bdf=BDF, status_path='/sys/unscoped', producer=fail, reader=fail, uname=fail, monotonic_ns=fail, wall_time_ns=fail, journal_runner=fail)
        self.assertEqual(r['status'], 'INVALID_BRACKET_REQUEST')


class Pipe:
    def __init__(self, fd): self.fd = fd; self.closed = False
    def fileno(self): return self.fd
    def close(self): self.closed = True


class Child:
    def __init__(self, calls, alive=False, ignore_term=False):
        self.stdout = Pipe(300); self.stderr = Pipe(301); self.calls = calls; self.alive = alive; self.ignore_term = ignore_term; self.returncode = None if alive else 0
    def poll(self): return None if self.alive else self.returncode
    def wait(self, timeout):
        self.calls.append(('wait', timeout))
        if self.alive: raise subprocess.TimeoutExpired('/usr/bin/journalctl', timeout)
        return self.returncode
    def terminate(self):
        self.calls.append(('terminate-own-child',))
        if not self.ignore_term: self.alive = False; self.returncode = -15
    def kill(self): self.calls.append(('kill-own-child',)); self.alive = False; self.returncode = -9


class Selector:
    def __init__(self, f): self.f = f; self.keys = {}; self.rounds = 0
    def register(self, stream, event, data): self.keys[stream.fd] = SimpleNamespace(fileobj=stream, data=data)
    def get_map(self): return self.keys
    def select(self, timeout):
        self.rounds += 1
        if self.f.stall_after is not None and self.rounds > self.f.stall_after: return []
        return [(key, selectors.EVENT_READ) for key in list(self.keys.values())]
    def unregister(self, stream): del self.keys[stream.fd]
    def close(self): self.f.calls.append(('selector-close',))


class JournalFakes:
    def __init__(self, stdout=RAW_JOURNAL, stderr=b'', alive=False, ignore_term=False):
        self.calls = []; self.data = {300: bytearray(stdout), 301: bytearray(stderr)}; self.child = Child(self.calls, alive, ignore_term)
        self.time = 0.0; self.step = 0.001; self.stall_after = None; self.spawn_error = None; self.read_error = None
    def mono(self): self.time += self.step; return self.time
    def popen(self, argv, **kwargs):
        self.calls.append(('popen', argv, kwargs))
        if self.spawn_error: raise self.spawn_error
        return self.child
    def read(self, fd, count):
        if self.read_error: raise self.read_error
        data = bytes(self.data[fd][:count]); del self.data[fd][:count]; return data
    def run(self, **changes):
        values = dict(popen=self.popen, selector_factory=lambda: Selector(self), read_fn=self.read, monotonic=self.mono)
        values.update(changes)
        with patch.object(j.os, 'kill', side_effect=AssertionError('No other PID operation')), patch.object(j.subprocess, 'Popen', side_effect=AssertionError('No real process')):
            return j.run_journal(j.journal_argv(BOOT.decode().strip().replace('-', '')), **values)


class JournalAdapterTests(unittest.TestCase):
    def test_one_fixed_child_eof_both_streams_and_no_signals(self):
        f = JournalFakes(stderr=b'synthetic warning'); r = f.run()
        self.assertEqual(r['stdout'], RAW_JOURNAL); self.assertTrue(r['stdout_eof']); self.assertTrue(r['stderr_eof']); self.assertEqual(r['returncode'], 0)
        self.assertEqual(r['signals_sent_to_own_child'], []); self.assertEqual(len([x for x in f.calls if x[0] == 'popen']), 1)
        call = next(x for x in f.calls if x[0] == 'popen'); self.assertEqual(call[2]['stdin'], subprocess.DEVNULL); self.assertNotIn('shell', call[2]); self.assertEqual(call[2]['env'], j.JOURNAL_ENV)
        self.assertTrue(f.child.stdout.closed); self.assertTrue(f.child.stderr.closed)
    def test_stdout_overflow_caps_prefix_and_stops_only_own_child(self):
        f = JournalFakes(stdout=b'x' * (j.STDOUT_LIMIT + 100), alive=True); r = f.run()
        self.assertEqual(len(r['stdout']), j.STDOUT_LIMIT + 1); self.assertEqual(r['overflow_stream'], 'stdout'); self.assertEqual(r['signals_sent_to_own_child'], ['SIGTERM'])
    def test_stderr_overflow_caps_prefix(self):
        f = JournalFakes(stderr=b'e' * (j.STDERR_LIMIT + 20), alive=True); r = f.run()
        self.assertEqual(len(r['stderr']), j.STDERR_LIMIT + 1); self.assertEqual(r['overflow_stream'], 'stderr')
    def test_stalled_child_timeout_preserves_prefix_and_bounded_cleanup(self):
        f = JournalFakes(stdout=b'prefix', stderr=b'error-prefix', alive=True, ignore_term=True); f.step = 2.0; f.stall_after = 1; r = f.run()
        self.assertTrue(r['timed_out']); self.assertEqual(r['stdout'], b'prefix'); self.assertEqual(r['stderr'], b'error-prefix')
        self.assertEqual(r['signals_sent_to_own_child'], ['SIGTERM', 'SIGKILL']); self.assertEqual([x[1] for x in f.calls if x[0] == 'wait'], [0.5, 0.5])
    def test_child_that_closes_pipes_without_exit_times_out(self):
        f = JournalFakes(alive=True); r = f.run(); self.assertTrue(r['stdout_eof']); self.assertTrue(r['timed_out']); self.assertEqual(r['signals_sent_to_own_child'], ['SIGTERM'])
    def test_missing_journal_binary_and_read_failure_are_not_retried(self):
        f = JournalFakes(); f.spawn_error = FileNotFoundError('synthetic journal missing'); r = f.run(); self.assertFalse(r['spawned']); self.assertTrue(r['errors'])
        f = JournalFakes(alive=True); f.read_error = OSError('synthetic pipe failed'); r = f.run(); self.assertTrue(r['errors']); self.assertEqual(r['signals_sent_to_own_child'], ['SIGTERM'])
    def test_changed_query_and_bounds_rejected_before_child(self):
        for argv in (['/usr/bin/journalctl', '--follow'], j.journal_argv('1' * 32) + ['--sync']):
            with patch.object(j.subprocess, 'Popen', side_effect=AssertionError('No child')), self.assertRaises(ValueError): j.run_journal(argv)
        f = JournalFakes()
        with self.assertRaises(ValueError): f.run(stdout_limit=1)
        self.assertEqual(f.calls, [])


class OuterReaderTests(unittest.TestCase):
    def fake(self, payload=b'bytes\n', mode=stat.S_IFREG | 0o400, close_error=False):
        calls = []; data = bytearray(payload)
        def read(fd, count): calls.append(('read', fd, count)); out = bytes(data[:count]); del data[:count]; return out
        def close(fd):
            calls.append(('close', fd))
            if close_error: raise OSError('synthetic close failure')
        kwargs = dict(open_fn=lambda p, flags: calls.append(('open', p, flags)) or 7, fstat_fn=lambda fd: SimpleNamespace(st_mode=mode), read_fn=read, close_fn=close)
        return calls, kwargs
    def test_fixed_readonly_flags_and_zero_size_seq_file(self):
        calls, kwargs = self.fake(); self.assertEqual(j.read_outer(j.PID1_COMM, 64, **kwargs), b'bytes\n')
        flags = calls[0][2]; self.assertEqual(flags, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK); self.assertEqual(calls[-1], ('close', 7))
    def test_wrong_paths_limits_and_nonregular_inputs(self):
        for path, limit in (('/proc/2/comm', 64), (j.BOOT_ID, 65), ('/sys/kernel/debug/dri/7/name', 4096)):
            calls, kwargs = self.fake()
            with self.assertRaises(j.ReadFailure): j.read_outer(path, limit, **kwargs)
            self.assertEqual(calls, [])
        calls, kwargs = self.fake(mode=stat.S_IFIFO | 0o400)
        with self.assertRaises(j.ReadFailure): j.read_outer(j.PID1_COMM, 64, **kwargs)
        self.assertFalse(any(x[0] == 'read' for x in calls))
    def test_overflow_and_close_failure_keep_observed_prefix(self):
        calls, kwargs = self.fake(payload=b'x' * 100); self.assertEqual(len(j.read_outer(j.PID1_COMM, 64, **kwargs)), 65)
        calls, kwargs = self.fake(payload=b'observed', close_error=True)
        with self.assertRaises(j.ReadFailure) as error: j.read_outer(j.PID1_COMM, 64, **kwargs)
        self.assertEqual(error.exception.partial, b'observed')


class CliAndRendererTests(unittest.TestCase):
    def test_help_and_bad_arguments_are_json_only_without_runtime(self):
        for argv, code in ((['--help'], 0), ([], 2), (['--unknown'], 2)):
            out = io.StringIO()
            with patch.object(j.os, 'open', side_effect=AssertionError('No runtime read')), patch.object(j.subprocess, 'Popen', side_effect=AssertionError('No child')):
                self.assertEqual(j.main(argv, output=out), code)
            self.assertFalse(json.loads(out.getvalue())['hardware_acceptance'])
    def test_cli_with_all_fake_runtime_dependencies(self):
        f = Workflow(); out = io.StringIO(); pins = {'acquire_sha256': j.ACQUIRE_SHA256, 'wrapper_sha256': j.sha(WRAPPER_SOURCE)}
        code = j.main(['--expected-release', RELEASE, '--expected-pci-bdf', BDF, '--status-path', STATUS, '--include-pid1-comm'], acquisition_module=a, source_pins=pins,
                      producer=f.producer, reader=f.reader, uname=f.uname, monotonic_ns=f.mono, wall_time_ns=f.wall, journal_runner=f.journal, output=out)
        r = json.loads(out.getvalue()); self.assertEqual(code, 0); self.assertEqual(r['source_pins'], pins); self.assertTrue(r['include_pid1_comm'])
    def test_renderer_rejects_wrong_acquire_pin_and_is_deterministic(self):
        with self.assertRaises(ValueError): j.render_remote_program(ACQUIRE_SOURCE + b'\n', WRAPPER_SOURCE)
        first = j.render_remote_program(ACQUIRE_SOURCE, WRAPPER_SOURCE)
        self.assertEqual(first, j.render_remote_program(ACQUIRE_SOURCE, WRAPPER_SOURCE)); self.assertIn(j.sha(WRAPPER_SOURCE).encode(), first)
        self.assertNotIn(b'ssh ', first); self.assertIn(b'types.ModuleType', first)
    def test_rendered_program_imports_modules_without_standalone_acquire_cli(self):
        program = j.render_remote_program(ACQUIRE_SOURCE, WRAPPER_SOURCE); out = io.StringIO()
        with patch.object(sys, 'argv', ['embedded', '--help']), patch.object(os, 'open', side_effect=AssertionError('No live read')), patch.object(os, 'uname', side_effect=AssertionError('No uname')), patch.object(subprocess, 'Popen', side_effect=AssertionError('No real child')), patch('sys.stdout', out):
            with self.assertRaises(SystemExit) as error: exec(compile(program, '<synthetic-render-test>', 'exec'), {'__name__': '__main__'})
        self.assertEqual(error.exception.code, 0); rows = out.getvalue().splitlines(); self.assertEqual(len(rows), 1); self.assertEqual(json.loads(rows[0])['schema'], 1)
    def test_corrupted_render_transport_is_json_only_error_without_runtime(self):
        program = j.render_remote_program(ACQUIRE_SOURCE, WRAPPER_SOURCE)
        encoded = base64.b64encode(ACQUIRE_SOURCE); program = program.replace(encoded, b'A' + encoded[1:], 1); out = io.StringIO()
        with patch.object(sys, 'argv', ['embedded', '--help']), patch.object(os, 'open', side_effect=AssertionError('No live read')), patch.object(subprocess, 'Popen', side_effect=AssertionError('No real child')), patch('sys.stdout', out):
            with self.assertRaises(SystemExit) as error: exec(compile(program, '<corrupted-render-test>', 'exec'), {'__name__': '__main__'})
        self.assertEqual(error.exception.code, 2); self.assertEqual(json.loads(out.getvalue())['status'], 'BRACKET_BOOTSTRAP_ERROR')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True); args = parser.parse_args()
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in (FlowTests, JournalAdapterTests, OuterReaderTests, CliAndRendererTests))
    stream = io.StringIO(); result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite); print(stream.getvalue(), end='')
    receipt = {'schema': 1, 'status': 'PASS_FAKE_ONLY_BOOT_JOURNAL_BRACKET_TESTS' if result.wasSuccessful() else 'FAIL_FAKE_ONLY_BOOT_JOURNAL_BRACKET_TESTS', 'cases': result.testsRun,
               'failures': len(result.failures), 'errors': len(result.errors), 'host': {'system': platform.system(), 'machine': platform.machine()},
               'source_sha256': j.sha(WRAPPER_SOURCE), 'tests_sha256': j.sha(Path(__file__).read_bytes()), 'acquire_sha256': j.sha(ACQUIRE_SOURCE),
               'live_proc_or_sys_reads': False, 'real_journal_queries': False, 'real_children_spawned': False, 'ssh_actions': False, 'hardware_actions': False, 'log': stream.getvalue()}
    with args.report.open('x') as output: json.dump(receipt, output, indent=2, sort_keys=True); output.write('\n')
    raise SystemExit(not result.wasSuccessful())
