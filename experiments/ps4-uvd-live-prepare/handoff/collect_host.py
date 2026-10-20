#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Record one reviewed SSH stdin capture; no console, UART or media controls."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
EXTRACT_SHA256 = '6dc833595d1a7a7eb95952da6ffb109dadc5846ccb4b5d41f1fb442c4eb1451b'
STDOUT_LIMIT = 4 * 1024 * 1024
STDERR_LIMIT = 256 * 1024
TIMEOUT_SECONDS = 30.0
CLEANUP_SECONDS = 0.5
SSH_PREFIX = ['/usr/bin/ssh', '-T', '-a', '-x', '-S', 'none']
for option in ('BatchMode=yes', 'ConnectTimeout=10', 'ConnectionAttempts=1', 'StrictHostKeyChecking=yes',
               'UpdateHostKeys=no', 'ControlMaster=no', 'ControlPersist=no', 'PermitLocalCommand=no',
               'ClearAllForwardings=yes', 'ForkAfterAuthentication=no', 'StdinNull=no', 'RemoteCommand=none'):
    SSH_PREFIX.extend(['-o', option])


def sha(data): return hashlib.sha256(data).hexdigest()


def require(ok, message):
    if not ok: raise ValueError(message)


def load_extractor():
    source = HERE / 'extract.py'
    require(not source.is_symlink() and sha(source.read_bytes()) == EXTRACT_SHA256, 'Frozen result binder changed')
    spec = importlib.util.spec_from_file_location('preparation_host_transport_extract', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


e = load_extractor()


def endpoint(value):
    require(isinstance(value, str) and 0 < len(value) <= 255 and value.isascii() and value.count('@') <= 1, 'Explicit SSH endpoint required')
    if '@' in value:
        user, host = value.split('@')
        require(re.fullmatch('[A-Za-z_][A-Za-z0-9_.-]{0,63}', user), 'Unsafe SSH username')
    else: host = value
    if ':' in host:
        literal = host[1:-1] if host.startswith('[') and host.endswith(']') else host
        require('%' not in literal, 'Use an explicit configured endpoint for scoped IPv6')
        ipaddress.IPv6Address(literal)
    else:
        require(re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,252}', host), 'Unsafe SSH host/alias')
    return value


def ssh_argv(target, remote):
    endpoint(target)
    require(isinstance(remote, list) and len(remote) == 11 and remote[:4] == ['python3', '-B', '-I', '-'] and
            remote[4] == '--expected-release' and re.fullmatch(e.j.RELEASE_PATTERN, remote[5]) and
            remote[6] == '--expected-pci-bdf' and re.fullmatch(e.j.BDF_PATTERN, remote[7]) and
            remote[8] == '--status-path' and re.fullmatch(e.j.STATUS_PATTERN, remote[9]) and remote[10] == '--include-pid1-comm', 'Remote argv differs from bounded collector')
    return [*SSH_PREFIX, '--', target, shlex.join(remote)]


def run_ssh(argv, program, *, stdout_limit=STDOUT_LIMIT, stderr_limit=STDERR_LIMIT, timeout_seconds=TIMEOUT_SECONDS,
            popen=subprocess.Popen, selector_factory=selectors.DefaultSelector, read_fn=os.read,
            write_fn=os.write, set_blocking_fn=os.set_blocking, monotonic=time.monotonic):
    """One owned SSH child; concurrent bounded stdin/output; no shell/retry."""
    require(isinstance(argv, list) and len(argv) == len(SSH_PREFIX) + 3 and argv == ssh_argv(argv[-2], shlex.split(argv[-1])), 'Unexpected SSH command')
    require(isinstance(program, bytes) and 0 < len(program) <= 1024 * 1024, 'Rendered program size differs')
    require((stdout_limit, stderr_limit, timeout_seconds) == (STDOUT_LIMIT, STDERR_LIMIT, TIMEOUT_SECONDS), 'Fixed transport bounds required')
    result = {'stdout': b'', 'stderr': b'', 'stdout_eof': False, 'stderr_eof': False, 'input_bytes_sent': 0,
              'stdin_closed': False, 'spawned': False, 'returncode': None, 'timed_out': False,
              'overflow_stream': None, 'signals_sent_to_own_child': [], 'errors': []}
    child = selector = None; buffers = {'stdout': bytearray(), 'stderr': bytearray()}; last = None
    def now():
        nonlocal last
        value = monotonic()
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0 and (last is None or value >= last), 'Transport monotonic clock invalid/regressed')
        last = value; return value
    def failure(error): result['errors'].append({'exception': type(error).__name__, 'detail': str(error)[:512]})
    try:
        deadline = now() + timeout_seconds
        child = popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, bufsize=0)
        result['spawned'] = True; selector = selector_factory()
        for name in ('stdin', 'stdout', 'stderr'):
            stream = getattr(child, name); set_blocking_fn(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_WRITE if name == 'stdin' else selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - now()
            if remaining <= 0: result['timed_out'] = True; break
            for key, _ in selector.select(remaining):
                name, stream = key.data, key.fileobj
                try:
                    if name == 'stdin':
                        sent = result['input_bytes_sent']; block = program[sent:sent + 4096]
                        count = write_fn(stream.fileno(), block)
                        require(type(count) is int and 0 < count <= len(block), 'Invalid stdin write count')
                        result['input_bytes_sent'] += count
                        if result['input_bytes_sent'] == len(program):
                            selector.unregister(stream); stream.close(); result['stdin_closed'] = True
                    else:
                        limit = stdout_limit if name == 'stdout' else stderr_limit
                        count = min(65536, limit + 1 - len(buffers[name])); block = read_fn(stream.fileno(), count)
                        require(isinstance(block, bytes) and len(block) <= count, 'Invalid bounded pipe bytes')
                        buffers[name].extend(block)
                        if not block:
                            result[name + '_eof'] = True; selector.unregister(stream)
                        if len(buffers[name]) > limit: result['overflow_stream'] = name; break
                except BlockingIOError:
                    continue
            if result['overflow_stream']: break
        if not result['timed_out'] and result['overflow_stream'] is None:
            remaining = deadline - now()
            if remaining <= 0: result['timed_out'] = True
            else:
                try: result['returncode'] = child.wait(timeout=remaining)
                except subprocess.TimeoutExpired: result['timed_out'] = True
    except Exception as error:
        failure(error)
    finally:
        if child is not None:
            try:
                if child.poll() is None:
                    child.terminate(); result['signals_sent_to_own_child'].append('SIGTERM')
                    try: child.wait(timeout=CLEANUP_SECONDS)
                    except subprocess.TimeoutExpired:
                        child.kill(); result['signals_sent_to_own_child'].append('SIGKILL'); child.wait(timeout=CLEANUP_SECONDS)
                result['returncode'] = child.poll()
            except Exception as error: failure(error)
            for name in ('stdin', 'stdout', 'stderr'):
                stream = getattr(child, name, None)
                if stream is not None:
                    try: stream.close()
                    except Exception as error: failure(error)
        if selector is not None:
            try: selector.close()
            except Exception as error: failure(error)
        result.update({name: bytes(data) for name, data in buffers.items()})
    return result


def validate_launch(context, raw):
    launch = e.json_data(raw); p = context['plan']
    require(set(launch) == e.LAUNCH_KEYS and type(launch['schema']) is int and launch['schema'] == 1 and launch['kind'] == 'OPERATOR_LAUNCH_REPORT', 'Actual saved operator report required')
    require(launch['experiment'] == p['experiment'] and launch['plan_sha256'] == context['plan_sha256'] and launch['retained_experiment'] == context['retained_experiment'], 'Launch report identity differs')
    require(type(launch['launch_count']) is int and launch['launch_count'] == 1 and launch['full_poweroff_since_retained_experiment'] is True and launch['reporter'] == 'operator' and launch['hardware_acceptance'] is False, 'One-launch/full-off operator report missing')
    launched = e.instant(launch['recorded_at_utc'], utc=True)
    require(context['runtime']['completed_at'] <= launched, 'Launch report predates installation')
    return launch, launched


def clock_pair(utc_now, monotonic_ns):
    instant = utc_now(); mono = monotonic_ns()
    require(isinstance(instant, dt.datetime) and instant.utcoffset() == dt.timedelta(), 'Host UTC clock must return an aware UTC datetime')
    require(type(mono) is int and mono >= 0, 'Host monotonic clock must return nonnegative integer ns')
    return instant.isoformat(), mono


def write_new(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output: output.write(raw); output.flush(); os.fsync(output.fileno())


def save(path, value): write_new(path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode())


def collect(stage, *, reviewed_sha256, plan_sha256, launch_report, ssh_target, status_path, output_directory,
            runner, utc_now, monotonic_ns):
    """Host collection with mandatory runner/clocks; tests supply fakes."""
    endpoint(ssh_target)
    require(isinstance(status_path, str) and re.fullmatch(e.j.STATUS_PATTERN, status_path), 'Explicit numeric cached path required')
    destination = e.safe_path(output_directory, new=True)
    context = e._load_stage(stage, reviewed_sha256, plan_sha256)
    require(not destination.is_relative_to(context['stage']), 'Collection output must be outside the immutable stage')
    p = context['plan']; source = e.read_saved(Path(__file__))
    require(p['observation_tools'].get('host_transport') == sha(source) and e.read_saved(context['stage'] / 'observation-tools/host_transport.source') == source, 'Host collector not pinned by reviewed stage')
    launch_raw = e.read_saved(launch_report); launch, launched = validate_launch(context, launch_raw)
    remote = e.remote_argv(p['release'], context['contract']['expected_pci_bdf'], status_path)
    argv = ssh_argv(ssh_target, remote)
    program = e.j.render_remote_program(context['sources']['acquisition'], context['sources']['journal_collector'])
    preflight_utc, preflight_mono = clock_pair(utc_now, monotonic_ns)
    require(launched <= e.instant(preflight_utc, utc=True), 'Launch report is in the future')
    destination.mkdir(mode=0o700)
    diagnostics = {'schema': 1, 'status': 'HOST_COLLECTION_INCOMPLETE', 'hardware_acceptance': False,
                   'semantic_evaluation_performed': False, 'experiment': p['experiment'], 'plan_sha256': plan_sha256,
                   'reviewed_handoff_sha256': reviewed_sha256, 'collector_sha256': sha(source), 'launch_report_sha256': sha(launch_raw),
                   'ssh_argv': argv, 'remote_program_sha256': sha(program), 'bounds': {'stdout_bytes': STDOUT_LIMIT, 'stderr_bytes': STDERR_LIMIT, 'timeout_seconds': TIMEOUT_SECONDS, 'cleanup_seconds_each': CLEANUP_SECONDS}}
    receipt = None
    try:
        write_new(destination / 'program.py', program); write_new(destination / 'operator-launch.json', launch_raw)
        save(destination / 'STARTED.json', diagnostics)
        # Recheck the small controlling files immediately before this one call.
        require(e.read_saved(launch_report) == launch_raw and e.read_saved(Path(__file__)) == source and
                sha(e.read_saved(context['stage'] / 'plan.json')) == plan_sha256 and
                sha(e.read_saved(context['stage'].parent / 'REVIEWED-HANDOFF.json')) == reviewed_sha256, 'Controlling input changed before SSH')
        start_utc, start_mono = clock_pair(utc_now, monotonic_ns)
        require(e.instant(start_utc, utc=True) >= e.instant(preflight_utc, utc=True) and start_mono >= preflight_mono, 'Host clocks regressed before SSH')
        diagnostics.update(started_at_utc=start_utc, started_monotonic_ns=start_mono)
        outcome = runner(argv, program, stdout_limit=STDOUT_LIMIT, stderr_limit=STDERR_LIMIT, timeout_seconds=TIMEOUT_SECONDS)
        diagnostics['execution'] = {key: value for key, value in outcome.items() if key not in ('stdout', 'stderr')}
        streams = {}
        for name, limit in (('stdout', STDOUT_LIMIT), ('stderr', STDERR_LIMIT)):
            require(isinstance(outcome.get(name), bytes), 'Transport stream is not bytes')
            streams[name] = outcome[name][:limit + 1]
            write_new(destination / (name + '.bin'), streams[name])
        end_utc = end_mono = None; clock_error = None
        try: end_utc, end_mono = clock_pair(utc_now, monotonic_ns)
        except Exception as error:
            clock_error = {'exception': type(error).__name__, 'detail': str(error)[:512]}
        diagnostics.update(ended_at_utc=end_utc, ended_monotonic_ns=end_mono, end_clock_error=clock_error)
        receipt = {'schema': 1, 'kind': 'SSH_PREPARATION_CAPTURE', 'experiment': p['experiment'], 'plan_sha256': plan_sha256,
                   'reviewed_handoff_sha256': reviewed_sha256, 'observation_contract_sha256': p['observation_contract_sha256'],
                   'observation_tools': p['observation_tools'], 'transport': 'ssh', 'ssh_target': ssh_target, 'remote_argv': remote,
                   'remote_program_sha256': sha(program), 'acquisition_source_sha256': p['observation_tools']['acquisition'],
                   'journal_source_sha256': p['observation_tools']['journal_collector'], 'returncode': outcome.get('returncode'),
                   'started_at_utc': start_utc, 'ended_at_utc': end_utc, 'started_monotonic_ns': start_mono, 'ended_monotonic_ns': end_mono,
                   'expected_release': p['release'], 'expected_pci_bdf': context['contract']['expected_pci_bdf'], 'status_path': status_path,
                   'hardware_acceptance': False, **{name + '_sha256': sha(data) for name, data in streams.items()}, **{name + '_bytes': len(data) for name, data in streams.items()}}
        complete = (clock_error is None and outcome.get('spawned') is True and type(outcome.get('returncode')) is int and outcome.get('stdin_closed') is True and
                    type(outcome.get('input_bytes_sent')) is int and outcome['input_bytes_sent'] == len(program) and
                    outcome.get('stdout_eof') is True and outcome.get('stderr_eof') is True and outcome.get('timed_out') is False and
                    outcome.get('overflow_stream') is None and outcome.get('signals_sent_to_own_child') == [] and outcome.get('errors') == [] and
                    len(outcome['stdout']) <= STDOUT_LIMIT and len(outcome['stderr']) <= STDERR_LIMIT)
        require(complete, 'Transport incomplete; exact streams and actual process result retained')
        e._launch_and_transport(context, launch, receipt, streams['stdout'], streams['stderr'])
        for name, data in streams.items(): require(e.read_saved(destination / (name + '.bin'), len(data)) == data, 'Saved transport readback differs')
        require(e.read_saved(destination / 'program.py') == program, 'Saved renderer readback differs')
        diagnostics.update(status='SSH_TRANSPORT_RECORDED', transport_process_succeeded=outcome['returncode'] == 0, transport_schema=e.TRANSPORT_KEYS == set(receipt))
        save(destination / 'COLLECTION.json', diagnostics)
        save(destination / 'transport.json', receipt)
        return {'status': 'SSH_TRANSPORT_RECORDED', 'output_directory': str(destination), 'returncode': outcome['returncode'],
                'transport_process_succeeded': outcome['returncode'] == 0, 'semantic_evaluation_performed': False, 'hardware_acceptance': False,
                'transport_sha256': sha((destination / 'transport.json').read_bytes())}
    except Exception as error:
        diagnostics.update(status='HOST_COLLECTION_INCOMPLETE', error={'exception': type(error).__name__, 'detail': str(error)[:512]}, observed_transport=receipt)
        try: save(destination / 'TRANSPORT-FAILURE.json', diagnostics)
        except OSError: pass
        return {'status': 'HOST_COLLECTION_INCOMPLETE', 'output_directory': str(destination), 'hardware_acceptance': False,
                'semantic_evaluation_performed': False, 'error': diagnostics['error']}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('stage', 'launch-report', 'output-directory'): p.add_argument('--' + name, type=Path, required=True)
    for name in ('reviewed-sha256', 'plan-sha256', 'ssh-target', 'status-path'): p.add_argument('--' + name, required=True)
    a = p.parse_args(argv)
    try:
        result = collect(a.stage, reviewed_sha256=a.reviewed_sha256, plan_sha256=a.plan_sha256, launch_report=a.launch_report,
                         ssh_target=a.ssh_target, status_path=a.status_path, output_directory=a.output_directory,
                         runner=run_ssh, utc_now=lambda: dt.datetime.now(dt.timezone.utc), monotonic_ns=time.monotonic_ns)
    except Exception as error:
        result = {'status': 'HOST_COLLECTION_REFUSED', 'hardware_acceptance': False, 'error': str(error)[:512]}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get('transport_process_succeeded') is True else 1


if __name__ == '__main__': raise SystemExit(main())
