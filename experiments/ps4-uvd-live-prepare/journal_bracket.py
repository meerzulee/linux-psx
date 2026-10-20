#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Read-only boot brackets around one cached acquisition and one journal query."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import time
import types

ACQUIRE_SHA256 = '9811aa5b174d58857f733cf458f6ecd264717f8d416d8b9b597b1a0959e1de3c'
BOOT_ID = '/proc/sys/kernel/random/boot_id'
UPTIME = '/proc/uptime'
PID1_COMM = '/proc/1/comm'
OUTER_LIMITS = {BOOT_ID: 64, UPTIME: 128, PID1_COMM: 64}
RELEASE_PATTERN = r'[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}'
BDF_PATTERN = r'[0-9a-f]{4}:[0-9a-f]{2}:[01][0-9a-f]\.[0-7]'
STATUS_PATTERN = r'/sys/kernel/debug/dri/(?:0|[1-9][0-9]{0,9})/amdgpu_ps4_live_prepare'
JOURNAL_GREP = r'PS4_MAP_CANARY (BEGIN|READY_LAYOUT|MAP)|PS4_LIVE_PREPARE PREPARED'
STDOUT_LIMIT = 262144
STDERR_LIMIT = 65536
JOURNAL_TIMEOUT = 5.0
CLEANUP_TIMEOUT = 0.5
JOURNAL_ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'SYSTEMD_COLORS': '0', 'SYSTEMD_LOG_TARGET': 'console'}
LIMITATIONS = [
    'Complete means bounded input acquisition with consistent sampled identities, not a passed hardware experiment.',
    'Journal JSON bytes and their original order are preserved; message semantics, arrays/null values, the 64-entry cap and UART matching belong to the outer binder.',
    'The grep/lines query implies reverse journal order; receive timestamps are not UART timestamps or proof timestamps.',
    'Boot IDs, releases, cached history and journal content corroborate a boot but do not attest binaries, host authenticity or guaranteed-unique physical addresses.',
    'Sequential observations are not atomic; changes between sampled identities can remain undetected.',
    'A source-complete PID1 comm read is not evidence that PID1 is systemd; raw bytes must be assessed separately.',
    'Only the journal child has a wall-clock timeout. Kernel reads remain byte-bounded and can block in kernel code.',
    'No retries, fallback log source, flush/sync/follow query, mount, privilege change, device discovery, UVD arm or network operation is provided.',
]


def sha(data): return hashlib.sha256(data).hexdigest()


def raw_record(data, complete):
    return {'base64': base64.b64encode(data).decode('ascii'), 'sha256': sha(data), 'bytes': len(data),
            'source_complete': complete, 'representation': 'exact_bytes' if complete else 'observed_prefix'}


class ReadFailure(Exception):
    def __init__(self, message, partial=None):
        super().__init__(message); self.partial = partial


def read_outer(path, limit, *, open_fn=os.open, fstat_fn=os.fstat, read_fn=os.read, close_fn=os.close):
    """Fixed outer proc files only; all OS calls injectable for offline tests."""
    if path not in OUTER_LIMITS or type(limit) is not int or limit != OUTER_LIMITS[path]:
        raise ReadFailure('Outer path/limit not allowed')
    fd = None; data = bytearray(); failure = None; observed = False
    try:
        fd = open_fn(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        if not stat.S_ISREG(fstat_fn(fd).st_mode): raise ReadFailure('Outer input is not regular/seq_file')
        while len(data) <= limit:
            count = min(4096, limit + 1 - len(data)); block = read_fn(fd, count)
            if not isinstance(block, bytes) or len(block) > count: raise ReadFailure('Invalid bounded reader result')
            observed = True; data.extend(block)
            if not block or len(data) > limit: break
    except (OSError, ReadFailure) as error:
        failure = error
    finally:
        if fd is not None:
            try: close_fn(fd)
            except OSError as error:
                if failure is None: failure = error
    if failure is not None: raise ReadFailure(str(failure), bytes(data) if observed else None) from failure
    return bytes(data)


def journal_argv(boot_hex):
    if not isinstance(boot_hex, str) or not re.fullmatch('[0-9a-f]{32}', boot_hex) or boot_hex == '0' * 32:
        raise ValueError('Journal requires one explicit nonzero 32-hex boot ID')
    return ['/usr/bin/journalctl', '--boot=' + boot_hex, '_TRANSPORT=kernel', '--no-pager', '--output=json', '--all',
            '--output-fields=MESSAGE,_BOOT_ID,_TRANSPORT', '--grep=' + JOURNAL_GREP, '--lines=64']


def run_journal(argv, *, stdout_limit=STDOUT_LIMIT, stderr_limit=STDERR_LIMIT, timeout_seconds=JOURNAL_TIMEOUT,
                popen=subprocess.Popen, selector_factory=selectors.DefaultSelector, read_fn=os.read, monotonic=time.monotonic):
    """One fixed read-only child, bounded bytes/time, with fully injectable I/O."""
    if (not isinstance(argv, list) or len(argv) != 9 or not isinstance(argv[1], str) or
            not argv[1].startswith('--boot=') or argv != journal_argv(argv[1][7:]) or
            (stdout_limit, stderr_limit, timeout_seconds) != (STDOUT_LIMIT, STDERR_LIMIT, JOURNAL_TIMEOUT)):
        raise ValueError('Only the fixed bounded journal query is allowed')
    result = {'stdout': b'', 'stderr': b'', 'stdout_eof': False, 'stderr_eof': False, 'returncode': None,
              'spawned': False, 'timed_out': False, 'overflow_stream': None, 'signals_sent_to_own_child': [], 'errors': []}
    child = selector = None; buffers = {'stdout': bytearray(), 'stderr': bytearray()}; last = None
    def now():
        nonlocal last
        value = monotonic()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (last is not None and value < last):
            raise ValueError('Journal monotonic clock invalid/regressed')
        last = value; return value
    def error(exc): result['errors'].append({'exception': type(exc).__name__, 'detail': str(exc)[:512]})
    try:
        deadline = now() + timeout_seconds
        child = popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      close_fds=True, bufsize=0, env=dict(JOURNAL_ENV))
        result['spawned'] = True; selector = selector_factory()
        for label in ('stdout', 'stderr'): selector.register(getattr(child, label), selectors.EVENT_READ, label)
        while selector.get_map():
            remaining = deadline - now()
            if remaining <= 0: result['timed_out'] = True; break
            ready = selector.select(remaining)
            for key, _ in ready:
                label = key.data; limit = stdout_limit if label == 'stdout' else stderr_limit
                count = min(65536, limit + 1 - len(buffers[label]))
                block = read_fn(key.fileobj.fileno(), count)
                if not isinstance(block, bytes) or len(block) > count: raise ValueError('Journal pipe reader violated byte bound')
                buffers[label].extend(block)
                if not block:
                    result[label + '_eof'] = True; selector.unregister(key.fileobj)
                if len(buffers[label]) > limit:
                    result['overflow_stream'] = label; break
            if result['overflow_stream']: break
        if not result['timed_out'] and not result['overflow_stream']:
            remaining = deadline - now()
            if remaining <= 0: result['timed_out'] = True
            else:
                try: result['returncode'] = child.wait(timeout=remaining)
                except subprocess.TimeoutExpired: result['timed_out'] = True
    except Exception as exc:
        error(exc)
    finally:
        if child is not None:
            try:
                if child.poll() is None:
                    child.terminate(); result['signals_sent_to_own_child'].append('SIGTERM')
                    try: child.wait(timeout=CLEANUP_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        child.kill(); result['signals_sent_to_own_child'].append('SIGKILL')
                        child.wait(timeout=CLEANUP_TIMEOUT)
                result['returncode'] = child.poll()
            except Exception as exc: error(exc)
            for label in ('stdout', 'stderr'):
                stream = getattr(child, label, None)
                if stream is not None:
                    try: stream.close()
                    except Exception as exc: error(exc)
        if selector is not None:
            try: selector.close()
            except Exception as exc: error(exc)
        result.update({label: bytes(data) for label, data in buffers.items()})
    return result


def _boot_id(data):
    value = data.decode('ascii')
    if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\n', value) or value == '00000000-0000-0000-0000-000000000000\n':
        raise ValueError('Boot ID must be a canonical nonzero UUID and LF')
    return value[:-1]


def _release(data):
    value = data.decode('ascii')
    if not re.fullmatch(RELEASE_PATTERN, value): raise ValueError('Noncanonical uname release')
    return value


def _uptime(data):
    value = data.decode('ascii'); match = re.fullmatch(r'(0|[1-9][0-9]{0,18})\.([0-9]{2}) (0|[1-9][0-9]{0,18})\.([0-9]{2})\n', value)
    if not match: raise ValueError('Noncanonical uptime')
    return {'uptime_centiseconds': int(match[1]) * 100 + int(match[2]), 'idle_centiseconds': int(match[3]) * 100 + int(match[4])}


def collect(*, expected_release, expected_pci_bdf, status_path, producer, reader, uname,
            monotonic_ns, wall_time_ns, journal_runner, include_pid1_comm=False, source_pins=None):
    """Mandatory injected dependencies; this core never discovers live inputs."""
    report = {'schema': 1, 'producer': 'ps4-uvd-preparation-boot-journal-bracket', 'status': 'INVALID_BRACKET_REQUEST',
              'source_complete': False, 'complete': False, 'identity_consistent': None, 'hardware_acceptance': False,
              'semantic_evaluation_performed': False, 'binary_attestation': False,
              'expected_release': expected_release, 'expected_pci_bdf': expected_pci_bdf, 'status_path': status_path,
              'include_pid1_comm': include_pid1_comm, 'source_pins': source_pins, 'expected_acquire_sha256': ACQUIRE_SHA256,
              'observations': {}, 'timestamps': {}, 'events': [], 'issues': [], 'acquisition': None,
              'producer_calls': 0, 'journal_attempted': False, 'journal': None, 'limits': list(LIMITATIONS)}
    def issue(code, **details): report['issues'].append({'code': code, **details})
    for value, pattern, label in ((expected_release, RELEASE_PATTERN, 'release'), (expected_pci_bdf, BDF_PATTERN, 'pci_bdf'), (status_path, STATUS_PATTERN, 'status_path')):
        if not isinstance(value, str) or not re.fullmatch(pattern, value): issue('INVALID_REQUEST', field=label)
    if type(include_pid1_comm) is not bool: issue('INVALID_REQUEST', field='include_pid1_comm')
    if report['issues']: return report
    last_mono = None
    def stamp(name):
        nonlocal last_mono
        row = {}; report['events'].append({'kind': 'clock', 'name': name})
        for label, function in (('monotonic_ns', monotonic_ns), ('wall_time_ns', wall_time_ns)):
            try:
                value = function()
                if type(value) is not int or value < 0: raise ValueError('Clock must return nonnegative integer ns')
                if label == 'monotonic_ns':
                    if last_mono is not None and value < last_mono: issue('MONOTONIC_CLOCK_REGRESSION', bracket=name)
                    last_mono = value
                row[label] = value
            except Exception as exc:
                row[label] = None; issue('CLOCK_FAILED', bracket=name, clock=label, detail=str(exc)[:512])
        report['timestamps'][name] = row
    def sample(name, source, limit, function, parser=None):
        report['events'].append({'kind': 'read', 'name': name, 'source': source})
        row = {'source': source, 'limit_bytes': limit, 'raw': None, 'parsed': None}; report['observations'][name] = row
        try:
            data = function()
            if not isinstance(data, bytes): raise TypeError('Reader must return bytes')
        except Exception as exc:
            partial = getattr(exc, 'partial', None)
            if isinstance(partial, bytes): row['raw'] = raw_record(partial[:limit + 1], False)
            row['error'] = {'exception': type(exc).__name__, 'detail': str(exc)[:512]}; issue('INPUT_READ_FAILED', input=name)
            return None
        row['raw'] = raw_record(data[:limit + 1], len(data) <= limit)
        if len(data) > limit: issue('INPUT_TOO_LARGE', input=name); return None
        if parser is None: return data
        try: row['parsed'] = parser(data); return row['parsed']
        except (ValueError, UnicodeError) as exc: issue('NONCANONICAL_INPUT', input=name, detail=str(exc)[:512]); return None
    def identities(suffix):
        boot = sample('boot_id_' + suffix, BOOT_ID, 64, lambda: reader(BOOT_ID, 64), _boot_id)
        release = sample('uname_' + suffix, 'uname(2).release', 128, uname, _release)
        uptime = sample('uptime_' + suffix, UPTIME, 128, lambda: reader(UPTIME, 128), _uptime)
        if release is not None and release != expected_release: issue('OUTER_RELEASE_MISMATCH', input='uname_' + suffix)
        return boot, release, uptime
    def inner_identity(value, boot):
        try:
            if (value.get('schema') != 2 or value.get('producer') != 'ps4-uvd-cached-preparation-acquisition' or
                    value.get('hardware_acceptance') is not False or value.get('expected_release') != expected_release or
                    value.get('expected_pci_bdf') != expected_pci_bdf or value.get('status_path') != status_path or
                    value.get('identity_consistent') is not True or value.get('drm_identity_consistent') is not True): return False
            identity_errors = {'RELEASE_MISMATCH', 'IDENTITY_CHANGED_BEFORE_STATUS', 'IDENTITY_NOT_CONSISTENT_AFTER_STATUS', 'DRM_DRIVER_MISMATCH', 'DRM_PCI_BDF_MISMATCH', 'DRM_NAME_NOT_CONSISTENT_AFTER_STATUS'}
            if any(row.get('code') in identity_errors for row in value.get('issues', [])): return False
            obs = value['observations']
            if any(obs['boot_id_' + when]['parsed'] != boot or obs['uname_' + when]['parsed'] != expected_release for when in ('before', 'before_status', 'after')): return False
            return all(obs['drm_name_' + when]['parsed']['driver'] == 'amdgpu' and obs['drm_name_' + when]['parsed']['pci_bdf'] == expected_pci_bdf for when in ('before', 'after'))
        except (KeyError, TypeError): return False
    stamp('outer_start')
    if report['issues']:
        stamp('outer_end'); report['status'] = 'REFUSED_BEFORE_ACQUISITION'; return report
    before = identities('before'); inner_ok = False
    if not report['issues']:
        stamp('before_acquisition')
        if not report['issues']:
            report['producer_calls'] = 1; report['events'].append({'kind': 'producer', 'name': 'cached_acquisition'})
            try:
                acquired = producer()
                encoded = json.dumps(acquired, allow_nan=False, sort_keys=True)
                if not isinstance(acquired, dict) or len(encoded.encode()) > 2 * 1024 * 1024: raise ValueError('Invalid/oversized acquisition report')
                report['acquisition'] = json.loads(encoded); inner_ok = inner_identity(acquired, before[0])
                if not inner_ok: issue('ACQUISITION_IDENTITY_OUTSIDE_BRACKET')
                if acquired.get('complete') is not True: issue('ACQUISITION_INCOMPLETE')
            except Exception as exc: issue('ACQUISITION_FAILED', exception=type(exc).__name__, detail=str(exc)[:512])
            stamp('after_acquisition')
        if inner_ok and not any(x['code'] in ('CLOCK_FAILED', 'MONOTONIC_CLOCK_REGRESSION') for x in report['issues']):
            if include_pid1_comm: sample('pid1_comm', PID1_COMM, 64, lambda: reader(PID1_COMM, 64))
            stamp('before_journal')
            if not any(x['code'] in ('CLOCK_FAILED', 'MONOTONIC_CLOCK_REGRESSION') for x in report['issues']):
                argv = journal_argv(before[0].replace('-', '')); report['journal_attempted'] = True
                report['events'].append({'kind': 'journal', 'argv': argv})
                row = {'argv': argv, 'limits': {'stdout_bytes': STDOUT_LIMIT, 'stderr_bytes': STDERR_LIMIT, 'record_limit': 64, 'timeout_seconds': JOURNAL_TIMEOUT, 'cleanup_wait_seconds_each': CLEANUP_TIMEOUT},
                       'stdout': None, 'stderr': None, 'query_complete': False, 'output_order': 'preserved; --grep plus --lines implies reverse'}
                report['journal'] = row
                try:
                    outcome = journal_runner(argv, stdout_limit=STDOUT_LIMIT, stderr_limit=STDERR_LIMIT, timeout_seconds=JOURNAL_TIMEOUT)
                    for label, limit in (('stdout', STDOUT_LIMIT), ('stderr', STDERR_LIMIT)):
                        data = outcome[label]
                        if not isinstance(data, bytes): raise TypeError('Journal output must be bytes')
                        row[label] = raw_record(data[:limit + 1], outcome.get(label + '_eof') is True and len(data) <= limit)
                    metadata = json.dumps({key: outcome.get(key) for key in ('returncode', 'spawned', 'timed_out', 'overflow_stream', 'signals_sent_to_own_child', 'errors')}, allow_nan=False)
                    if len(metadata.encode()) > 16384: raise ValueError('Oversized journal runner metadata')
                    row.update(json.loads(metadata))
                    row['query_complete'] = (row['spawned'] is True and type(row['returncode']) is int and row['returncode'] == 0 and row['timed_out'] is False and row['overflow_stream'] is None and row['signals_sent_to_own_child'] == [] and row['errors'] == [] and all(row[x]['source_complete'] for x in ('stdout', 'stderr')))
                    if not row['query_complete']: issue('JOURNAL_QUERY_INCOMPLETE')
                    if row['stdout']['bytes'] == 0: issue('JOURNAL_EMPTY')
                except Exception as exc: issue('JOURNAL_RUNNER_FAILED', exception=type(exc).__name__, detail=str(exc)[:512])
            stamp('after_journal')
    after = identities('after'); stamp('outer_end')
    identities_ok = (before[0] is not None and before[1] == expected_release and before[:2] == after[:2] and inner_ok)
    if before[2] is None or after[2] is None or after[2]['uptime_centiseconds'] < before[2]['uptime_centiseconds']:
        identities_ok = False; issue('OUTER_UPTIME_INVALID_OR_REGRESSED')
    report['identity_consistent'] = identities_ok
    if not identities_ok: issue('OUTER_OR_INNER_IDENTITY_INCONSISTENT')
    required = ['boot_id_before', 'uname_before', 'uptime_before', 'boot_id_after', 'uname_after', 'uptime_after'] + (['pid1_comm'] if include_pid1_comm else [])
    report['clock_brackets_complete'] = all(v is not None for stamp in report['timestamps'].values() for v in stamp.values())
    report['source_complete'] = (all(isinstance(report['observations'].get(n, {}).get('raw'), dict) and report['observations'][n]['raw']['source_complete'] is True for n in required) and
                                 report['clock_brackets_complete'] and
                                 isinstance(report['acquisition'], dict) and report['acquisition'].get('complete') is True and
                                 report['journal'] is not None and report['journal'].get('query_complete') is True)
    report['complete'] = report['source_complete'] and identities_ok and not report['issues']
    report['status'] = 'CAPTURED_BOOT_CORRELATION_INPUTS' if report['complete'] else 'INCOMPLETE_BOOT_CORRELATION_INPUTS'
    walls = [x['wall_time_ns'] for x in report['timestamps'].values()]
    report['wall_clock_nondecreasing'] = all(a <= z for a, z in zip(walls, walls[1:])) if all(x is not None for x in walls) else None
    return report


def load_acquire_source(source):
    if not isinstance(source, bytes) or sha(source) != ACQUIRE_SHA256: raise ValueError('Frozen acquire source hash differs')
    module = types.ModuleType('ps4_prepare_acquire_embedded'); module.__file__ = '<reviewed acquire.py>'
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return module


def render_remote_program(acquire_source_bytes, wrapper_source_bytes):
    """Pure byte renderer; contains no SSH client, remote file writes or execution."""
    if not isinstance(acquire_source_bytes, bytes) or sha(acquire_source_bytes) != ACQUIRE_SHA256: raise ValueError('Frozen acquire source hash differs')
    if not isinstance(wrapper_source_bytes, bytes) or not 0 < len(wrapper_source_bytes) <= 512 * 1024: raise ValueError('Wrapper source must be bounded bytes')
    wrapper_source_bytes.decode('utf-8')
    sources = {'acquire': (base64.b64encode(acquire_source_bytes).decode(), sha(acquire_source_bytes)),
               'wrapper': (base64.b64encode(wrapper_source_bytes).decode(), sha(wrapper_source_bytes))}
    program = "import base64, hashlib, types, json\nSOURCES = " + repr(sources) + "\nmodules = {}\n"
    program += "try:\n    for label, (encoded, expected) in SOURCES.items():\n        source = base64.b64decode(encoded, validate=True)\n        if hashlib.sha256(source).hexdigest() != expected: raise RuntimeError('Embedded source hash differs')\n        module = types.ModuleType('ps4_prepare_embedded_' + label)\n        module.__file__ = '<reviewed embedded ' + label + '>'\n        exec(compile(source, module.__file__, 'exec'), module.__dict__)\n        modules[label] = module\n"
    program += "    code = modules['wrapper'].main(acquisition_module=modules['acquire'], source_pins={'acquire_sha256': SOURCES['acquire'][1], 'wrapper_sha256': SOURCES['wrapper'][1]})\n"
    program += "except Exception as error:\n    print(json.dumps({'schema': 1, 'status': 'BRACKET_BOOTSTRAP_ERROR', 'complete': False, 'source_complete': False, 'hardware_acceptance': False, 'exception': type(error).__name__, 'detail': str(error)[:512]}, sort_keys=True))\n    code = 2\nraise SystemExit(code)\n"
    return program.encode('utf-8')


class _Parser(argparse.ArgumentParser):
    def error(self, message): raise ValueError(message)


def main(argv=None, *, acquisition_module=None, source_pins=None, producer=None, reader=None, uname=None,
         monotonic_ns=None, wall_time_ns=None, journal_runner=None, output=None):
    """JSON-only CLI; live adapters are selected only after valid arguments."""
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument('--help', action='store_true')
    for name in ('expected-release', 'expected-pci-bdf', 'status-path'): parser.add_argument('--' + name, action='append')
    parser.add_argument('--include-pid1-comm', action='append_const', const=True)
    try:
        args = parser.parse_args(argv)
        if args.help:
            result = {'schema': 1, 'status': 'USAGE', 'hardware_acceptance': False,
                      'usage': 'python3 -B -I journal_bracket.py --expected-release RELEASE --expected-pci-bdf BDF --status-path EXACT_CACHED_PATH [--include-pid1-comm]', 'limits': list(LIMITATIONS)}; code = 0
        else:
            if any(v is None or len(v) != 1 for v in (args.expected_release, args.expected_pci_bdf, args.status_path)) or (args.include_pid1_comm is not None and len(args.include_pid1_comm) != 1): raise ValueError('Required arguments must occur exactly once; optional flag at most once')
            if acquisition_module is None:
                source = Path(__file__).with_name('acquire.py').read_bytes(); acquisition_module = load_acquire_source(source)
                source_pins = {'acquire_sha256': sha(source), 'wrapper_sha256': sha(Path(__file__).read_bytes())}
            if not isinstance(source_pins, dict) or set(source_pins) != {'acquire_sha256', 'wrapper_sha256'} or source_pins['acquire_sha256'] != ACQUIRE_SHA256 or not re.fullmatch('[0-9a-f]{64}', source_pins['wrapper_sha256']): raise ValueError('Source pins missing/inconsistent')
            mono = time.monotonic_ns if monotonic_ns is None else monotonic_ns
            wall = time.time_ns if wall_time_ns is None else wall_time_ns
            current_uname = acquisition_module.native_uname_release if uname is None else uname
            actual_producer = producer if producer is not None else lambda: acquisition_module.acquire(expected_release=args.expected_release[0], expected_pci_bdf=args.expected_pci_bdf[0], status_path=args.status_path[0], reader=acquisition_module.read_bounded, uname=current_uname, monotonic_ns=mono, wall_time_ns=wall)
            result = collect(expected_release=args.expected_release[0], expected_pci_bdf=args.expected_pci_bdf[0], status_path=args.status_path[0], producer=actual_producer,
                             reader=read_outer if reader is None else reader, uname=current_uname, monotonic_ns=mono, wall_time_ns=wall,
                             journal_runner=run_journal if journal_runner is None else journal_runner, include_pid1_comm=bool(args.include_pid1_comm), source_pins=source_pins)
            code = 0 if result['complete'] else 2 if result['status'] == 'INVALID_BRACKET_REQUEST' else 1
    except Exception as exc:
        result = {'schema': 1, 'status': 'BRACKET_CLI_ERROR', 'complete': False, 'source_complete': False, 'hardware_acceptance': False, 'exception': type(exc).__name__, 'detail': str(exc)[:512]}; code = 2
    print(json.dumps(result, sort_keys=True, allow_nan=False), file=sys.stdout if output is None else output)
    return code


if __name__ == '__main__': raise SystemExit(main())
