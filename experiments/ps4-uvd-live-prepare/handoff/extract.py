#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bind explicit saved preparation artifacts; no device, UART or SSH actions."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


c = module('preparation_extract_common', HERE / 'common.py')
ce = module('preparation_extract_capture', HERE.parent / 'capture_evidence.py')
u = module('preparation_extract_uart', HERE.parent / 'uart_evidence.py')
j = module('preparation_extract_journal', HERE.parent / 'journal_bracket.py')
DEPENDENCIES = {
    'acquisition': ('acquire.py', '9811aa5b174d58857f733cf458f6ecd264717f8d416d8b9b597b1a0959e1de3c'),
    'evaluator': ('observation.py', 'e6a28755d96292c101bfd5cd0032565075a0f993c09b88b6073281bada46ee2a'),
    'capture_evidence': ('capture_evidence.py', '25fab1ccdb6af225f574161b729b3a3549f1d57b15744b48717ad8fac3b979f3'),
    'uart_evidence': ('uart_evidence.py', 'da4583d18d49d1f048d5a224052ec4042a45ea3605d5ce07291c7d60a97402a2'),
    'journal_collector': ('journal_bracket.py', 'e32f9abcf292abffd5c00a392d1723bfabcd92eb20d7a43a6316167228e8b018'),
}
RECORD_LIMIT = 2 * 1024 * 1024
BRACKET_LIMIT = 4 * 1024 * 1024
OUTER_STAMPS = ('outer_start', 'before_acquisition', 'after_acquisition', 'before_journal', 'after_journal', 'outer_end')
LAUNCH_KEYS = {'schema', 'kind', 'experiment', 'plan_sha256', 'recorded_at_utc', 'launch_count',
               'full_poweroff_since_retained_experiment', 'retained_experiment', 'reporter', 'hardware_acceptance'}
TRANSPORT_KEYS = {'schema', 'kind', 'experiment', 'plan_sha256', 'reviewed_handoff_sha256',
                  'observation_contract_sha256', 'observation_tools', 'transport', 'ssh_target', 'remote_argv',
                  'remote_program_sha256', 'acquisition_source_sha256', 'journal_source_sha256',
                  'stdout_sha256', 'stdout_bytes', 'stderr_sha256', 'stderr_bytes', 'returncode',
                  'started_at_utc', 'ended_at_utc', 'started_monotonic_ns', 'ended_monotonic_ns',
                  'expected_release', 'expected_pci_bdf', 'status_path', 'hardware_acceptance'}
LIMITATIONS = [
    'Saved artifact correspondence and bounded userspace observations only; hardware_acceptance=false, decode_demonstrated=false and accepted=0.',
    'Operator UTC records a launch report, not the physical instant of launch or proof of power state.',
    'Host transport records, stable boot IDs and journal/UART messages corroborate a boot; they are not cryptographic host or kernel-binary attestation.',
    'Kernel printk time bounds observed boot progress only; it is not equated with host wall time, journal receive time or cached proof_ns.',
    'PTE values corroborate message correspondence and are not guaranteed per-boot nonces.',
    'The installer verifies all 55 staged files and 41 installed kernel objects. After depmod, generated index names are recorded; their final bytes are not attested by the receipt.',
    'All UART fault/absence scans use the full bounded raw extract, never a compacted context or filtered journal.',
    'Stage validation uses the existing offline zstd archive checks. This binder performs no SSH, device, UART, session, mount, privilege or build action.',
]


class BindingError(ValueError):
    pass


class IdentityError(BindingError):
    pass


def require(value, message):
    if not value:
        raise BindingError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def same(left, right):
    return canonical(left) == canonical(right)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key: ' + key[:128])
        result[key] = value
    return result


def bad_constant(value):
    raise BindingError('Nonfinite JSON value: ' + value)


def finite_float(text):
    value = float(text)
    require(math.isfinite(value), 'JSON numeric overflow is not finite')
    return value


def json_data(raw, bound=RECORD_LIMIT):
    require(isinstance(raw, bytes) and len(raw) <= bound, 'Saved JSON exceeds byte bound')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                           parse_constant=bad_constant, parse_float=finite_float)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise BindingError('Malformed saved JSON: ' + str(error)[:256]) from error
    require(isinstance(value, dict), 'Saved JSON must be an object')
    return value


def safe_path(path, *, directory=False, new=False):
    path = Path(path).absolute()
    require(not any(part == '..' for part in path.parts), 'Parent traversal refused')
    for candidate in (path, *path.parents):
        require(not candidate.is_symlink(), 'Symlink path refused: ' + str(candidate))
    require(len(path.parts) < 2 or path.parts[1] not in ('dev', 'proc', 'sys'), 'Use saved files, not live pseudo-filesystems')
    if new:
        require(not path.exists() and path.parent.is_dir(), 'Output must be new in an existing directory')
    else:
        require(path.is_dir() if directory else stat.S_ISREG(path.stat().st_mode), 'Expected saved directory/regular file')
    return path


def read_saved(path, bound=RECORD_LIMIT):
    path = safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'Saved input changed to nonregular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(bound + 1)
        require(len(data) <= bound, 'Saved input exceeds byte bound: ' + str(path))
        return data
    finally:
        os.close(fd)


def instant(value, *, utc=False):
    require(isinstance(value, str), 'Timestamp must be an aware ISO string')
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as error:
        raise BindingError('Invalid ISO timestamp') from error
    require(parsed.utcoffset() is not None, 'Naive timestamp refused')
    require(not utc or parsed.utcoffset() == dt.timedelta(), 'Host/operator timestamp must explicitly be UTC')
    return parsed.astimezone(dt.timezone.utc)


def remote_argv(release, bdf, status_path):
    return ['python3', '-B', '-I', '-', '--expected-release', release,
            '--expected-pci-bdf', bdf, '--status-path', status_path, '--include-pid1-comm']


def _command(record):
    require(set(record) == {'argv', 'exit_code', 'stdout', 'stderr'}, 'Command receipt schema differs')
    require(type(record['exit_code']) is int and record['exit_code'] == 0, 'Recorded command failed')
    require(isinstance(record['argv'], list) and all(isinstance(x, str) for x in record['argv']), 'Recorded argv differs')
    require(isinstance(record['stdout'], str) and isinstance(record['stderr'], str), 'Command streams must be text')


def _runtime(stage, plan, plan_sha, review_sha, module_files):
    runtime = stage.parent / 'runtime'
    names = ['install-started.json', 'install-receipt.json', 'install-complete.json', 'session-start.json', 'install-command.json']
    names += ['install-' + phase + '-' + role + '-fsck.json' for phase in ('before', 'after') for role in ('boot', 'root')]
    raw = {name: read_saved(runtime / name) for name in names}
    rows = {name: json_data(value) for name, value in raw.items()}
    started, installed, completed = (rows[name] for name in names[:3])
    require(started.get('experiment') == installed.get('experiment') == completed.get('experiment') == plan['experiment'], 'Runtime experiment differs')
    require(started.get('mode') == 'install' and started.get('plan_sha256') == plan_sha and
            started.get('reviewed_handoff_sha256') == review_sha and started.get('wrapper_sha256') == plan['tools']['run_test.py'], 'Install-start pins differ')
    require(all(started.get(key) is True for key in ('host_usb_identity_revalidated', 'operator_holds_linux_launch', 'host_usb_isolation_and_launch_hold_confirmed')), 'Install isolation/held-launch record missing')
    require(isinstance(started.get('physical_state_evidence'), str) and started['physical_state_evidence'].strip(), 'Install host evidence absent')
    require(installed.get('mode') == 'apply' and installed.get('release') == plan['release'] and installed.get('install_from_release') == plan['install_release'], 'Installed release/mode differs')
    require(installed.get('console_state') in plan['installation_state']['console'], 'Installed console state outside reviewed plan')
    require(installed.get('reviewed_handoff_sha256') == review_sha and installed.get('serial') == plan['disk_serial'], 'Install review/media differs')
    require(all(installed.get(key) is True for key in ('committed', 'unmounted', 'modules_installed', 'usb_on_host_launch_held_confirmed', 'operator_holds_linux_launch')), 'Installation not committed/unmounted with matching modules')
    require(installed.get('native_restore_automatic') is False and not installed.get('error') and not installed.get('cleanup_errors'), 'Install error/automatic restoration')
    after = {**plan['install_boot_hashes'], 'bzImage': plan['kernel_sha256'],
             'initramfs.cpio.gz': plan['initramfs_sha256'], 'bootargs.txt': plan['new_bootargs_sha256']}
    require(same(installed.get('boot_before'), plan['install_boot_hashes']) and same(installed.get('boot_after'), after)
            and len(after) == 5, 'Exact five-file installation differs')
    prefix = 'lib/modules/' + plan['release'] + '/'
    require(len(module_files) == 55 and sum(name.endswith('.ko') for name in module_files) == 41, 'Whole staged module set differs')
    require({prefix + name: sha(value) for name, value in module_files.items()} == plan['module_tree'], 'Loaded module bytes do not match plan')
    require(type(installed.get('module_files')) is int and installed['module_files'] == len(module_files), 'Installed module count differs')
    indexes = sorted(name for name in module_files if '/' not in name and name.startswith('modules.'))
    require(same(installed.get('module_indexes'), indexes), 'Generated module index-name set differs')
    for field, tree in (('rollback_module_count', plan['rollback_module_tree']), ('install_source_module_count', plan['install_module_tree'])):
        require(type(installed.get(field)) is int and installed[field] == sum('.ko' in name for name in tree), 'Preserved prior module count differs')
    for role, fstype in (('boot', 'vfat'), ('root', 'ext4')):
        part = installed.get('partitions', {}).get(role, {})
        require(set(part) == {'path', 'size', 'uuid', 'fstype', 'mounts'} and
                part['uuid'] == plan[role + '_uuid'] and type(part['size']) is int and part['size'] == plan[role + '_bytes']
                and part['fstype'] == fstype and part['mounts'] == [] and isinstance(part['path'], str) and part['path'].startswith('/dev/'), 'Recorded media partition differs')
        require(completed.get('partitions', {}).get(role) == part['path'], 'Final unmounted partition differs')
        for phase in ('before', 'after'):
            row = rows['install-' + phase + '-' + role + '-fsck.json']; _command(row)
            expected = ['sudo', '-n', '/usr/bin/fsck.fat', '-n', part['path']] if role == 'boot' else ['sudo', '-n', '/usr/bin/e2fsck', '-f', '-n', part['path']]
            require(row['argv'] == expected, 'Read-only fsck argv differs')
    for key in ('uart_before', 'uart_after'):
        row = installed.get(key, {})
        require(set(row) == {'status', 'session'} and all(isinstance(row[x], str) for x in row), 'Installer UART observation absent')
        require(re.search(r'^\[\+\] Status:\s+READY$', row['status'], re.M) and
                re.search(r'^\[\+\] Session active: ' + re.escape(plan['experiment']) + '$', row['session'], re.M) and
                re.search(r'^\s+Continuity: valid so far \(', row['session'], re.M), 'Installer UART observation differs')
    command = rows['install-command.json']; _command(command)
    require(same(json_data(command['stdout'].encode()), installed), 'Saved installer stdout differs from receipt')
    argv = command['argv']
    require(len(argv) == 17 and argv[:5] == ['sudo', '-n', '/usr/bin/python3', '-B', '-I']
            and Path(argv[5]).is_absolute() and argv[5].endswith('/ps4-uvd-live-prepare/handoff/install_usb.py')
            and argv[6:] == ['--apply', str(stage), '--confirm-serial', plan['disk_serial'],
                             '--reviewed-plan-sha256', plan_sha, '--reviewed-handoff-sha256', review_sha,
                             '--confirm-usb-on-host-launch-held', '--console-state', installed.get('console_state')],
            'Installer command does not identify this reviewed apply stage')
    require(completed.get('status') == 'PASS_INSTALLED_UNMOUNTED' and completed.get('plan_sha256') == plan_sha and
            completed.get('both_read_only_filesystem_checks_passed_before_and_after') is True and
            completed.get('session_left_open_for_primary_agent') is True, 'Install completion/fsck record absent')
    begun, finished = instant(started.get('utc'), utc=True), instant(completed.get('utc'), utc=True)
    require(begun <= finished, 'Installation UTC order regressed')
    return {'rows': rows, 'hashes': {name: sha(value) for name, value in raw.items()},
            'started_at': begun, 'completed_at': finished, 'expected_boot_after': after,
            'module_scope': {'staged_files': 55, 'installed_kernel_objects': 41, 'generated_index_names': indexes,
                             'post_depmod_index_hashes_attested': False}}


def _load_stage(stage, reviewed_sha256, plan_sha256):
    stage = safe_path(stage, directory=True)
    c.validate_reviewed_handoff(stage, reviewed_sha256, plan_sha256)
    plan, kernel, initramfs, files, old, bootargs = c.load_inputs(stage)
    contract = json_data(read_saved(stage / 'observation-contract.json'))
    require(contract.get('expected_driver') == 'amdgpu' and contract.get('required_pid1_comm') == 'systemd'
            and isinstance(contract.get('expected_pci_bdf'), str) and re.fullmatch(j.BDF_PATTERN, contract['expected_pci_bdf']), 'Observation contract lacks explicit PCI/PID1 expectations')
    paths = c.contract_tools(contract)
    require(set(DEPENDENCIES) | {'result_binding'} <= set(paths), 'Final evaluator/tool roles missing')
    sources = {}
    for role, (filename, expected) in DEPENDENCIES.items():
        current = read_saved(HERE.parent / filename)
        saved = read_saved(stage / 'observation-tools' / (role + '.source'))
        require(sha(current) == expected == plan['observation_tools'][role] == sha(saved)
                and sha(read_saved(paths[role])) == expected, 'Frozen evaluation tool differs: ' + role)
        sources[role] = current
    require(sha(read_saved(HERE / 'extract.py')) == plan['observation_tools']['result_binding'] == sha(read_saved(paths['result_binding'])), 'Result binder not pinned by the reviewed plan')
    runtime = _runtime(stage, plan, plan_sha256, reviewed_sha256, files)
    prior = json_data(read_saved(stage / 'prior-plan.json'))
    return {'stage': stage, 'plan': plan, 'contract': contract, 'bootargs': bootargs, 'sources': sources,
            'runtime': runtime, 'retained_experiment': prior['experiment'],
            'plan_sha256': plan_sha256, 'reviewed_sha256': reviewed_sha256}


def _session_start(row, session, experiment):
    _command(row)
    require(len(row['argv']) == 4 and row['argv'][1:] == ['session', 'start', experiment]
            and Path(row['argv'][0]).is_absolute() and row['argv'][0].endswith('/ps4-uart/uart'), 'Session-start argv differs')
    pattern = (r'\[\+\] Session started: (?P<label>[^\r\n]+)\n    Source: (?P<source>[^\r\n]+)\n'
               r'    Capture generation: (?P<generation>[0-9a-f]{32})\n    Connection epoch: (?P<epoch>[1-9][0-9]*)\n'
               r'    Offset: (?P<offset>0|[1-9][0-9]{0,2}(?:,[0-9]{3})*) bytes\n'
               r'    Perform the action, then run: \./uart session stop\n')
    text = row['stdout'].replace('\r\n', '\n')
    match = re.fullmatch(pattern, text)
    require(match is not None and row['stderr'] == '', 'Original session-start stdout malformed')
    expected = {'label': experiment, 'source': session.get('source'), 'generation': session.get('capture_generation'),
                'epoch': str(session.get('connection_epoch')), 'offset': f"{session.get('start_offset'):,}"}
    require(match.groupdict() == expected, 'Session-start identity/byte fence differs from completed session')
    return match.groupdict()


def _launch_and_transport(context, launch, transport, stdout, stderr):
    p, contract = context['plan'], context['contract']
    require(set(launch) == LAUNCH_KEYS and type(launch['schema']) is int and launch['schema'] == 1
            and launch['kind'] == 'OPERATOR_LAUNCH_REPORT', 'Launch report schema differs')
    require(launch['experiment'] == p['experiment'] and launch['plan_sha256'] == context['plan_sha256']
            and launch['retained_experiment'] == context['retained_experiment'], 'Launch report experiment/plan/history differs')
    require(type(launch['launch_count']) is int and launch['launch_count'] == 1 and
            launch['full_poweroff_since_retained_experiment'] is True and launch['reporter'] == 'operator'
            and launch['hardware_acceptance'] is False, 'One-launch/full-poweroff operator report absent')
    require(set(transport) == TRANSPORT_KEYS and type(transport['schema']) is int and transport['schema'] == 1
            and transport['kind'] == 'SSH_PREPARATION_CAPTURE', 'Transport receipt schema differs')
    fixed = {'experiment': p['experiment'], 'plan_sha256': context['plan_sha256'],
             'reviewed_handoff_sha256': context['reviewed_sha256'], 'observation_contract_sha256': p['observation_contract_sha256'],
             'observation_tools': p['observation_tools'], 'transport': 'ssh', 'expected_release': p['release'],
             'expected_pci_bdf': contract['expected_pci_bdf'], 'hardware_acceptance': False,
             'acquisition_source_sha256': p['observation_tools']['acquisition'],
             'journal_source_sha256': p['observation_tools']['journal_collector']}
    require(all(same(transport[key], value) for key, value in fixed.items()), 'Transport plan/tool/PCI expectation differs')
    require(isinstance(transport['ssh_target'], str) and re.fullmatch(r'[^\s\x00-\x1f\x7f]{1,255}', transport['ssh_target']), 'Explicit SSH target absent')
    require(isinstance(transport['status_path'], str) and re.fullmatch(j.STATUS_PATTERN, transport['status_path']), 'Transport status path is not exact cached node')
    require(transport['remote_argv'] == remote_argv(p['release'], contract['expected_pci_bdf'], transport['status_path']), 'Remote stdin program argv differs')
    program = j.render_remote_program(context['sources']['acquisition'], context['sources']['journal_collector'])
    require(transport['remote_program_sha256'] == sha(program), 'Rendered remote program identity differs')
    for label, raw in (('stdout', stdout), ('stderr', stderr)):
        require(type(transport[label + '_bytes']) is int and transport[label + '_bytes'] == len(raw)
                and transport[label + '_sha256'] == sha(raw), 'Transport stream bytes differ: ' + label)
    require(type(transport['returncode']) is int, 'SSH collector returncode is not an integer')
    require(all(type(transport[key]) is int and transport[key] >= 0 for key in ('started_monotonic_ns', 'ended_monotonic_ns'))
            and transport['started_monotonic_ns'] <= transport['ended_monotonic_ns'], 'Host monotonic transport interval invalid')
    launched = instant(launch['recorded_at_utc'], utc=True)
    begun, ended = (instant(transport[key], utc=True) for key in ('started_at_utc', 'ended_at_utc'))
    require(context['runtime']['completed_at'] <= launched <= begun <= ended, 'Install/launch-report/transport UTC order invalid')
    return {'launch_reported_at': launched, 'capture_started_at': begun, 'capture_ended_at': ended,
            'rendered_program_sha256': sha(program), 'status_path': transport['status_path'],
            'transport_succeeded': transport['returncode'] == 0}


def _raw(record, bound, *, complete=True):
    require(isinstance(record, dict) and set(record) == {'base64', 'sha256', 'bytes', 'source_complete', 'representation'}, 'Raw envelope differs')
    require(type(record['source_complete']) is bool and isinstance(record['base64'], str)
            and len(record['base64']) <= 4 * ((bound + 3) // 3), 'Raw encoding/bound differs')
    try:
        raw = base64.b64decode(record['base64'], validate=True)
    except (ValueError, TypeError) as error:
        raise BindingError('Invalid raw base64') from error
    require(type(record['bytes']) is int and len(raw) <= bound + (0 if record['source_complete'] else 1)
            and same(record, j.raw_record(raw, record['source_complete'])), 'Raw hash/length/canonical encoding differs')
    require(not complete or record['source_complete'], 'Incomplete source bytes cannot support this result')
    return raw


def _outer_replay(bracket, release, bdf, status_path, source_pins, expected_boot_id):
    """Actual collector replay, using only saved inputs and no native adapters."""
    expected = {
        'boot_id_before': (j.BOOT_ID, 64), 'uname_before': ('uname(2).release', 128),
        'uptime_before': (j.UPTIME, 128), 'pid1_comm': (j.PID1_COMM, 64),
        'boot_id_after': (j.BOOT_ID, 64), 'uname_after': ('uname(2).release', 128),
        'uptime_after': (j.UPTIME, 128),
    }
    require(isinstance(bracket.get('observations'), dict) and set(bracket['observations']) == set(expected), 'Outer complete observation set differs')
    decoded = {}
    for name, (source, limit) in expected.items():
        row = bracket['observations'][name]
        require(isinstance(row, dict) and row.get('source') == source and type(row.get('limit_bytes')) is int
                and row['limit_bytes'] == limit, 'Outer observation source/bound differs: ' + name)
        decoded[name] = _raw(row.get('raw'), limit)
    # Compare actual canonical bytes, not the collector's parsed/identity flags.
    if any(j._boot_id(decoded[name]) != expected_boot_id for name in ('boot_id_before', 'boot_id_after')):
        raise IdentityError('Outer boot ID differs from the validated embedded capture')
    if any(j._release(decoded[name]) != release for name in ('uname_before', 'uname_after')):
        raise IdentityError('Outer release differs from the reviewed plan')
    stamps = bracket.get('timestamps')
    require(isinstance(stamps, dict) and set(stamps) == set(OUTER_STAMPS), 'Outer clock bracket set differs')
    require(all(isinstance(row, dict) and set(row) == {'monotonic_ns', 'wall_time_ns'}
                and all(type(value) is int and value >= 0 for value in row.values()) for row in stamps.values()), 'Outer clock value types differ')
    journal = bracket.get('journal')
    require(isinstance(journal, dict), 'Journal query record absent')
    stdout, stderr = _raw(journal.get('stdout'), j.STDOUT_LIMIT), _raw(journal.get('stderr'), j.STDERR_LIMIT)
    queues = {
        j.BOOT_ID: [decoded['boot_id_before'], decoded['boot_id_after']],
        j.UPTIME: [decoded['uptime_before'], decoded['uptime_after']], j.PID1_COMM: [decoded['pid1_comm']],
        'uname': [decoded['uname_before'], decoded['uname_after']],
        'monotonic': [stamps[name]['monotonic_ns'] for name in OUTER_STAMPS],
        'wall': [stamps[name]['wall_time_ns'] for name in OUTER_STAMPS],
        'producer': [bracket.get('acquisition')], 'journal': [True],
    }

    def take(name):
        require(name in queues and queues[name], 'Replay requested missing or extra input')
        return queues[name].pop(0)

    def reader(path, limit):
        require(j.OUTER_LIMITS.get(path) == limit, 'Outer replay changed read bound')
        return take(path)

    def runner(argv, **kwargs):
        take('journal')
        require(argv == journal.get('argv') and kwargs == {'stdout_limit': j.STDOUT_LIMIT,
                'stderr_limit': j.STDERR_LIMIT, 'timeout_seconds': j.JOURNAL_TIMEOUT}, 'Journal replay argv/limits differ')
        return {'stdout': stdout, 'stderr': stderr, 'stdout_eof': True, 'stderr_eof': True,
                **{key: journal.get(key) for key in ('returncode', 'spawned', 'timed_out', 'overflow_stream', 'signals_sent_to_own_child', 'errors')}}

    replayed = j.collect(expected_release=release, expected_pci_bdf=bdf, status_path=status_path,
                         producer=lambda: take('producer'), reader=reader, uname=lambda: take('uname'),
                         monotonic_ns=lambda: take('monotonic'), wall_time_ns=lambda: take('wall'),
                         journal_runner=runner, include_pid1_comm=True, source_pins=source_pins)
    require(not any(queues.values()) and same(bracket, replayed), 'Outer collector report differs from exact injected replay')
    require(replayed['complete'] is True, 'Outer collector did not complete consistently')
    return decoded, stdout, stderr


def _journal_match(raw, boot_id, uart_raw, uart_result):
    require(raw.endswith(b'\n'), 'Journal JSON-lines must end with LF')
    lines = raw.splitlines(keepends=True)
    require(0 < len(lines) < 64, 'Journal empty or reached 64-entry saturation limit')
    records = []
    for number, line in enumerate(lines, 1):
        require(line.endswith(b'\n'), 'Incomplete journal line')
        line = line.removesuffix(b'\n').removesuffix(b'\r')
        item = json_data(line, j.STDOUT_LIMIT)
        require(all(isinstance(item.get(key), str) for key in ('MESSAGE', '_BOOT_ID', '_TRANSPORT')), 'Journal identity/message field is not a string')
        if item['_BOOT_ID'] != boot_id.replace('-', '') or item['_TRANSPORT'] != 'kernel':
            raise IdentityError('Journal boot ID or kernel transport differs')
        require(not any(char in item['MESSAGE'] for char in ('\x00', '\r', '\n')), 'Journal MESSAGE contains control framing')
        records.append({'line': number, 'message': item['MESSAGE'], 'record': item})
    uart_lines = uart_raw.splitlines(keepends=True)
    matches = {}
    for kind, marker in (('map', 'PS4_MAP_CANARY MAP'), ('prepared', 'PS4_LIVE_PREPARE PREPARED')):
        rows = uart_result['records'][kind]
        require(len(rows) == 1, 'UART message unavailable for journal comparison')
        raw_line = uart_lines[rows[0]['line'] - 1]
        text = raw_line.removesuffix(b'\n').removesuffix(b'\r').decode('ascii')
        body = re.sub(r'^\[\s*[0-9]+\.[0-9]{6}\] ', '', text, count=1)
        selected = [row for row in records if marker in row['message']]
        require(len(selected) == 1 and selected[0]['message'] == body, 'Journal/UART exact ' + kind + ' message differs')
        matches[kind] = {'journal_line': selected[0]['line'], 'uart_line': rows[0]['line'], 'message': body,
                         'message_sha256': sha(body.encode('ascii'))}
    return {'records': records, 'matches': matches, 'raw_sha256': sha(raw), 'record_count': len(records),
            'order_preserved': True, 'uart_order_equality_required': False, 'pte_nonce_claim': False}


def _userspace(raw, uart_result, root_uuid):
    require(isinstance(root_uuid, str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', root_uuid), 'Plan root UUID malformed')
    stamp = r'\[\s*(?P<seconds>[0-9]{1,20})\.(?P<fraction>[0-9]{6})\] '
    patterns = {
        'run_init': re.compile(stamp + r'Run /init as init process'),
        'root_mount': re.compile(stamp + r'EXT4-fs \((?P<device>[^\s()]+)\): mounted filesystem (?P<uuid>' + re.escape(root_uuid) + r') .+'),
        'normal_target': re.compile(stamp + r'systemd\[1\]: Reached target (?P<target>(?:multi-user|graphical)\.target(?: - [^\r\n]+)?|Multi-User System|Graphical Interface)\.'),
    }
    records = {key: [] for key in patterns}
    for number, line in enumerate(raw.splitlines(keepends=True), 1):
        if not line.endswith(b'\n'):
            continue
        text = line.removesuffix(b'\n').removesuffix(b'\r').decode('ascii', errors='replace')
        for kind, pattern in patterns.items():
            match = pattern.fullmatch(text)
            if match:
                fields = match.groupdict()
                records[kind].append({'line': number, 'timestamp_ns': int(fields.pop('seconds')) * 1_000_000_000 + int(fields.pop('fraction')) * 1000,
                                      'text': text, **fields})
    boot_rows = uart_result.get('records', {}).get('boot', [])
    qualifying = []
    if len(boot_rows) == 1:
        boot = boot_rows[0]
        for target in records['normal_target']:
            progress = target['timestamp_ns'] - boot['timestamp_ns']
            ordered = any(
                boot['line'] < init['line'] < mount['line'] < target['line']
                and boot['timestamp_ns'] <= init['timestamp_ns'] <= mount['timestamp_ns'] <= target['timestamp_ns']
                for init in records['run_init'] for mount in records['root_mount'])
            if ordered and 0 <= progress <= 180_000_000_000:
                qualifying.append({**target, 'observed_boot_progress_ns': progress})
    return {'records': records, 'qualifying_targets': qualifying, 'observed_within_180_seconds': bool(qualifying),
            'root_uuid_from_plan': root_uuid, 'physical_launch_latency_claim': False,
            'systemd_mount_unit_wording_required': False}


def _base():
    return {'schema': 1, 'status': 'INCOMPLETE_OBSERVATION', 'preparation_userspace_observed': False,
            'hardware_acceptance': False, 'decode_demonstrated': False, 'accepted': 0,
            'issues': [], 'limitations': list(LIMITATIONS)}


def _analyze(context, *, uart_raw, session, launch, transport, bracket_raw, transport_stderr):
    p, contract = context['plan'], context['contract']
    result = _base()
    result.update(experiment=p['experiment'], release=p['release'], expected_pci_bdf=contract['expected_pci_bdf'],
                  expected_driver=contract['expected_driver'], expected_root_uuid=p['root_uuid'],
                  plan_sha256=context['plan_sha256'], reviewed_handoff_sha256=context['reviewed_sha256'],
                  observation_contract_sha256=p['observation_contract_sha256'], observation_tools=p['observation_tools'],
                  runtime_hashes=context['runtime']['hashes'], module_evidence=context['runtime']['module_scope'],
                  uart_raw_sha256=sha(uart_raw), uart_raw_bytes=len(uart_raw), bracket_stdout_sha256=sha(bracket_raw))
    bracket = None
    capture = None
    try:
        bracket = json_data(bracket_raw, BRACKET_LIMIT)
        acquisition = bracket.get('acquisition')
        if isinstance(acquisition, dict):
            capture = ce.evaluate((canonical(acquisition) + '\n').encode(), expected_release=p['release'],
                                  expected_pci_bdf=contract['expected_pci_bdf'], status_path=transport.get('status_path', ''),
                                  expected_bootargs=context['bootargs'])
            result['capture_evaluation'] = capture['report']
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        result['issues'].append({'category': 'observation', 'detail': str(error)[:512]})
    raw_inputs = capture['raw_inputs'] if capture else {}
    uart = u.analyze(uart_raw, session, raw_inputs.get('cached_status', b''), raw_inputs.get('uname_before', b''),
                     raw_inputs.get('cmdline', b''), expected_release=p['release'], experiment=p['experiment'],
                     expected_device=contract['expected_pci_bdf'])
    result['uart_evaluation'] = uart
    if uart['status'] == 'INVALID_CONTINUITY':
        result['status'] = 'INVALID_CONTINUITY'
        return result
    try:
        require(type(uart['session'].get('version')) is int and uart['session']['version'] == 2, 'Session version type differs')
        result['session_start_binding'] = _session_start(context['runtime']['rows']['session-start.json'], uart['session'], p['experiment'])
        begun, closed = instant(uart['session']['started_at']), instant(uart['session']['stopped_at'])
        require(begun <= context['runtime']['started_at'] <= context['runtime']['completed_at'] <= closed, 'Installation outside bounded UART session')
    except (ValueError, TypeError, KeyError) as error:
        result['issues'].append({'category': 'continuity', 'detail': str(error)[:512]})
        result['status'] = 'INVALID_CONTINUITY'
        return result
    if uart['faults']:
        result['status'] = 'PREPARATION_PARKED_OR_FAILED'
        return result
    try:
        timing = _launch_and_transport(context, launch, transport, bracket_raw, transport_stderr)
        require(timing['capture_ended_at'] <= closed, 'SSH capture ends after bounded session close')
        result['host_time_binding'] = {key: value.isoformat() if isinstance(value, dt.datetime) else value for key, value in timing.items()}
    except (ValueError, TypeError, KeyError) as error:
        result['issues'].append({'category': 'binding', 'detail': str(error)[:512]})
        result['status'] = 'HOST_RECORD_BINDING_INVALID'
        return result
    # Preserve distinct cache/UART failure classes even when no normal-userspace
    # conclusion can be drawn from the rest of the input bundle.
    actual_uart_identity_failure = any(row.get('code') in ('UART_RELEASE_MISMATCH', 'UART_DEVICE_MISMATCH') for row in uart['issues'])
    if actual_uart_identity_failure:
        result['status'] = 'BOOT_IDENTITY_MISMATCH'
        return result
    if uart['status'] == 'UART_PREPARATION_HISTORY_INVALIDATED' or (
            capture and capture['report']['complete_capture_validated'] and uart['status'] in
            ('PREPARATION_HISTORY_INVALIDATED', 'PREPARATION_STATE_CONTRADICTION', 'BOOT_IDENTITY_MISMATCH')):
        result['status'] = uart['status']
        return result
    if not capture or not capture['report']['complete_capture_validated'] or not timing['transport_succeeded']:
        result['status'] = 'INCOMPLETE_OBSERVATION'
        return result
    if not capture['report']['boot_policy_matched']:
        result['status'] = 'BOOT_IDENTITY_MISMATCH'
        return result
    if not uart['correspondence']:
        result['status'] = uart['status']
        return result
    try:
        outer_raw, journal_stdout, journal_stderr = _outer_replay(bracket, p['release'], contract['expected_pci_bdf'],
            transport['status_path'], {'acquire_sha256': p['observation_tools']['acquisition'],
                                       'wrapper_sha256': p['observation_tools']['journal_collector']},
            capture['report']['identity']['boot_id'])
        require(outer_raw['pid1_comm'] == (contract['required_pid1_comm'] + '\n').encode(), 'PID1 comm is not required systemd')
        inner_times = bracket['acquisition']['timestamps']; outer_times = bracket['timestamps']
        require(outer_times['before_acquisition']['monotonic_ns'] <= inner_times['acquisition_start']['monotonic_ns'] <=
                inner_times['acquisition_end']['monotonic_ns'] <= outer_times['after_acquisition']['monotonic_ns'], 'Embedded capture escapes outer monotonic bracket')
        outer_before = bracket['observations']['uptime_before']['parsed']['uptime_centiseconds']
        outer_after = bracket['observations']['uptime_after']['parsed']['uptime_centiseconds']
        inner_before = bracket['acquisition']['observations']['uptime_before']['parsed']['uptime_centiseconds']
        inner_after = bracket['acquisition']['observations']['uptime_after']['parsed']['uptime_centiseconds']
        require(outer_before <= inner_before <= inner_after <= outer_after, 'Embedded uptime escapes outer bracket')
        result['outer_replay_matched'] = True
        result['pid1_comm'] = contract['required_pid1_comm']
        result['outer_raw_sha256'] = {name: sha(value) for name, value in outer_raw.items()}
        result['journal_correspondence'] = _journal_match(journal_stdout, capture['report']['identity']['boot_id'], uart_raw, uart)
        result['journal_stderr_sha256'] = sha(journal_stderr)
    except IdentityError as error:
        result['issues'].append({'category': 'identity', 'detail': str(error)[:512]})
        result['status'] = 'BOOT_IDENTITY_MISMATCH'
        return result
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        result['issues'].append({'category': 'observation', 'detail': str(error)[:512]})
        result['status'] = 'INCOMPLETE_OBSERVATION'
        return result
    userspace = _userspace(uart_raw, uart, p['root_uuid'])
    result['userspace_evidence'] = userspace
    if not userspace['observed_within_180_seconds']:
        result['status'] = 'NO_USERSPACE_MILESTONE_WITHIN_180_SECONDS'
        return result
    result['status'] = 'PREPARATION_USERSPACE_OBSERVED'
    result['preparation_userspace_observed'] = True
    return result


def extract_saved(stage, *, reviewed_sha256, plan_sha256, uart_raw_path, session_meta_path,
                  launch_report_path, transport_receipt_path, bracket_stdout_path, transport_stderr_path):
    """Read explicit saved files and reviewed stage; no live acquisition.

    common.load_inputs intentionally performs its existing offline zstd archive
    validations. No executable path in this function invokes UART or SSH.
    """
    result = _base()
    try:
        context = _load_stage(stage, reviewed_sha256, plan_sha256)
        inputs = {name: read_saved(path, bound) for name, path, bound in (
            ('uart_raw', uart_raw_path, u.MAX_RAW_BYTES), ('session', session_meta_path, RECORD_LIMIT),
            ('launch', launch_report_path, RECORD_LIMIT), ('transport', transport_receipt_path, RECORD_LIMIT),
            ('bracket', bracket_stdout_path, BRACKET_LIMIT), ('transport_stderr', transport_stderr_path, RECORD_LIMIT))}
        result = _analyze(context, uart_raw=inputs['uart_raw'], session=json_data(inputs['session']),
                          launch=json_data(inputs['launch']), transport=json_data(inputs['transport']),
                          bracket_raw=inputs['bracket'], transport_stderr=inputs['transport_stderr'])
        result['saved_input_hashes'] = {name: {'sha256': sha(raw), 'bytes': len(raw)} for name, raw in inputs.items()}
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, RecursionError) as error:
        result['status'] = 'STAGE_OR_SAVED_INPUT_BINDING_INVALID'
        result['issues'].append({'category': 'stage_or_input', 'detail': str(error)[:512]})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--reviewed-sha256', required=True)
    parser.add_argument('--plan-sha256', required=True)
    for option in ('uart-raw', 'session-meta', 'launch-report', 'transport-receipt', 'bracket-stdout', 'transport-stderr'):
        parser.add_argument('--' + option, type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    output = safe_path(args.output, new=True)
    result = extract_saved(args.stage, reviewed_sha256=args.reviewed_sha256, plan_sha256=args.plan_sha256,
                           uart_raw_path=args.uart_raw, session_meta_path=args.session_meta,
                           launch_report_path=args.launch_report, transport_receipt_path=args.transport_receipt,
                           bracket_stdout_path=args.bracket_stdout, transport_stderr_path=args.transport_stderr)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'preparation_userspace_observed': result['preparation_userspace_observed'],
                      'hardware_acceptance': False, 'decode_demonstrated': False, 'accepted': 0, 'output': str(output)}))
    return 0 if result['preparation_userspace_observed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
