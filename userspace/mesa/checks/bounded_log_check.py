#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CPU-only regression for partial stderr lost during a hardware-test timeout."""
from pathlib import Path
import sys
import tempfile
from bounded_command import capture

with tempfile.TemporaryDirectory(prefix='psx-bounded-logs-') as temp:
    base = Path(temp)
    command = [sys.executable, '-c',
               "import sys,time;print('OUT_BEFORE',flush=True);"
               "print('ERR_BEFORE',file=sys.stderr,flush=True);time.sleep(5)"]
    result = capture(command, base / 'timeout', 0.5)
    assert result['timed_out'] and result['returncode'] == 124
    assert 'OUT_BEFORE' in (base / 'timeout/stdout.txt').read_text()
    assert 'ERR_BEFORE' in (base / 'timeout/stderr.txt').read_text()
    assert 'ERR_BEFORE' in result['stderr']
    result = capture([sys.executable, '-c', "print('COMPLETE')"], base / 'normal', 5)
    assert result['returncode'] == 0 and not result['timed_out']
    assert (base / 'normal/stdout.txt').read_text() == 'COMPLETE\n'
    try:
        capture(command, base / 'normal', 1)
    except FileExistsError:
        pass
    else:
        raise AssertionError('Existing evidence must not be overwritten')
print('PASS: partial stdout/stderr survive timeout; normal exit; existing evidence preserved. CPU only.')
