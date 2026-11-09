#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fetch pinned official host inputs and build private PkgTool SSL libraries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
from build import SDK_URL, SDK_SHA, OPENSSL_SHA, pin

OPENSSL_URL = 'https://github.com/openssl/openssl/releases/download/OpenSSL_1_1_1w/openssl-1.1.1w.tar.gz'

def fetch(url, path, expected):
    subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error', '--output', str(path), url], check=True)
    with path.open('rb') as f: actual = hashlib.file_digest(f, 'sha256').hexdigest()
    if actual != expected: raise ValueError('Downloaded input hash differs')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True, help='NEW host cache directory under /var/tmp')
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--apply', action='store_true')
    a = p.parse_args(); output = a.output.absolute()
    if output.exists() or output.parent.resolve() != output.parent or not str(output).startswith('/var/tmp/') or not 1 <= a.jobs <= 16:
        p.error('Use a new /var/tmp directory and 1–16 jobs')
    plan = {'sdk': {'url': SDK_URL, 'sha256': SDK_SHA}, 'openssl': {'url': OPENSSL_URL, 'sha256': OPENSSL_SHA}, 'output': str(output), 'host_only': True}
    print(json.dumps(plan, indent=2), flush=True)
    if not a.apply: return
    output.mkdir()
    sdk_archive = output / 'toolchain-llvm-18.tar.gz'
    fetch(SDK_URL, sdk_archive, SDK_SHA)
    with tarfile.open(sdk_archive) as tar: tar.extractall(output, filter='data')
    crypto = output / 'openssl'; crypto.mkdir()
    archive = crypto / 'openssl-1.1.1w.tar.gz'; fetch(OPENSSL_URL, archive, OPENSSL_SHA)
    with tarfile.open(archive) as tar: tar.extractall(crypto, filter='data')
    source = crypto / 'openssl-1.1.1w'
    configure = ['linux-x86_64', 'shared', 'no-tests', 'no-ssl3', '--prefix=/nonexistent/diagnostix-host-only']
    make = [f'-j{a.jobs}', 'build_libs']
    with (crypto / 'build.log').open('wb') as log:
        for command in (['./Configure', *configure], ['make', *make]):
            subprocess.run(command, cwd=source, env={**os.environ, 'TMPDIR': '/var/tmp'}, stdout=log, stderr=subprocess.STDOUT, check=True)
    receipt = {'schema': 1, 'purpose': 'Host-only SDK PkgTool private compatibility runtime; not included in PS4 app', 'url': OPENSSL_URL, 'source_sha256': OPENSSL_SHA, 'configure': configure, 'make': make, 'files': {name: pin(source / name) for name in ('libssl.so.1.1', 'libcrypto.so.1.1', 'LICENSE')}}
    (crypto / 'BUILD.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    print('Use --sdk', output / 'OpenOrbis/PS4Toolchain', '--sdk-archive', sdk_archive, '--openssl-runtime', source)

if __name__ == '__main__': main()
