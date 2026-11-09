#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Meerzulee and Linux psX contributors.
"""Run the actual scatter planner against synthetic physical memory only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent


def check(source, output):
    source = Path(source).resolve()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    include = source / 'linux/ps4-kexec-common'
    for name in ('types.h', 'diagnostix_scatter.h', 'diagnostix_scatter.c'):
        if not (include / name).is_file():
            raise ValueError(f'missing overlaid source: {include / name}')
    # The test translation unit must include the overlaid production header;
    # compiling it in HERE would find HERE's quoted header before -iquote.
    fixture = output / 'test_scatter.c'
    shutil.copyfile(HERE / 'test_scatter.c', fixture)
    exe = output / 'scatter'
    command = ['gcc', '-std=gnu11', '-DTESTING', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-omit-frame-pointer',
               '-iquote' + str(include), str(include / 'diagnostix_scatter.c'),
               str(fixture), '-o', str(exe)]
    subprocess.run(command, check=True, env=dict(os.environ, TMPDIR=str(output)))
    run = subprocess.run([str(exe)], check=True, capture_output=True, text=True, timeout=30)
    receipt = {'status': 'PASS_HOST_FIXTURES', 'hardware_acceptance': False,
               'stdout': run.stdout.strip(), 'source_sha256': {
                   name: hashlib.sha256((include / name).read_bytes()).hexdigest()
                   for name in ('diagnostix_scatter.h', 'diagnostix_scatter.c')},
               'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
               'types_sha256': hashlib.sha256((include / 'types.h').read_bytes()).hexdigest()}
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    check(args.source, args.output)
