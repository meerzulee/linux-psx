#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Small host-only source/TSYNC check; never fetch or launch Chromium or a device."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
UPSTREAM_SHA = '92069dde535c687534758fca6013ac15da9f19629ef1c71315cf64aa82254fd3'
PATCH_SHA = '08ca1d2c5b3660c6a0de7acff3928bc00bd10bd2f4f7f9c63c603f2cef2758dc'
PATCHED_SHA = '281d523a13883653eef955b9edf3143373034566e908c7b0fc3bfec75408b1cf'
MAX_SOURCE = 65536


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verified_source(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE:
        raise ValueError('Expected one bounded regular upstream gpu_main.cc')
    data = path.read_bytes()
    if digest(data) != UPSTREAM_SHA:
        raise ValueError('Upstream gpu_main.cc differs from the exact Chromium151 pin')
    return data


def command(argv, cwd, timeout=15):
    result = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f'{argv[0]} returned {result.returncode}:\n{result.stdout}\n{result.stderr}')
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream-file', required=True, type=Path,
                        help='Locally supplied exact Chromium151 content/gpu/gpu_main.cc; no fetching')
    parser.add_argument('--cc', default='cc', help='Native C compiler executable')
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64'):
        raise ValueError('Native Linux x86-64 host required; no container fallback')
    original = verified_source(args.upstream_file)
    patch = HERE / 'chromium-151-gpu-tsync.patch'
    if digest(patch.read_bytes()) != PATCH_SHA:
        raise ValueError('Candidate patch differs from its recorded pin')
    compiler = shutil.which(args.cc)
    patch_tool = shutil.which('patch')
    if not compiler or not patch_tool:
        raise ValueError('Native C compiler and patch are required')
    with tempfile.TemporaryDirectory(prefix='psx-chromium-tsync-', dir=os.environ.get('TMPDIR', '/var/tmp')) as temporary:
        work = Path(temporary)
        source = work / 'content/gpu/gpu_main.cc'
        source.parent.mkdir(parents=True)
        source.write_bytes(original)
        # Test the identity guard against changed source, not merely patch context.
        changed = work / 'changed.cc'
        changed.write_bytes(original + b'\n')
        try:
            verified_source(changed)
        except ValueError:
            pass
        else:
            raise AssertionError('Modified upstream source was accepted')
        command([patch_tool, '--batch', '--forward', '--fuzz=0', '-p1', '-i', str(patch)], work)
        if digest(source.read_bytes()) != PATCHED_SHA:
            raise ValueError('Patched source differs from the reviewed result')
        native = work / 'tsync-check'
        command([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
                 str(HERE / 'tsync_check.c'), '-o', str(native)], work, timeout=30)
        checks = command([str(native)], work, timeout=20)
        print(checks)
        print(json.dumps({'status': 'PASS', 'scope': 'host source application and Linux TSYNC mechanism only',
                          'chromium_integration_tested': False, 'hardware_acceptance': False,
                          'upstream_sha256': UPSTREAM_SHA, 'patch_sha256': PATCH_SHA,
                          'patched_source_sha256': PATCHED_SHA,
                          'native_test_sha256': digest((HERE / 'tsync_check.c').read_bytes())}, indent=2))


if __name__ == '__main__':
    main()
