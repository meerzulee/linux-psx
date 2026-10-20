#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Durable bounded process logs. Importing this module executes no commands."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def capture(command, output, timeout, env=None):
    """Execute one explicit argv; timeout stops owned processes, not GPU work."""
    if not command or not 0 < timeout <= 60:
        raise ValueError('Require a command and timeout in (0,60] seconds')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    timed_out = False
    with (output / 'stdout.txt').open('xb') as stdout, (output / 'stderr.txt').open('xb') as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                                   env=env, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                code = process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                code = None  # Preserve evidence; a kernel wait may outlive SIGKILL.
        finally:
            stdout.flush()
            stderr.flush()
            os.fsync(stdout.fileno())
            os.fsync(stderr.fileno())
    logs = {}
    result = {'command': command, 'pid': process.pid, 'returncode': 124 if timed_out else code,
              'process_returncode': code, 'timed_out': timed_out,
              'elapsed_seconds': time.monotonic() - started,
              'scope': 'Process termination is not cancellation/completion of submitted GPU work.'}
    for stream in ('stdout', 'stderr'):
        raw = (output / (stream + '.txt')).read_bytes()
        logs[stream] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        # The complete durable file remains even when the JSON tail is bounded.
        result[stream] = raw[-1048576:].decode('utf-8', errors='replace')
    result['logs'] = logs
    (output / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
