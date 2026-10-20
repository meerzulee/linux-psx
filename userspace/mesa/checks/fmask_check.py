#!/usr/bin/env python3
"""Replay Liverpool FMASK layouts against a built Mesa AddrLib without a GPU."""
import argparse
import json
from pathlib import Path
import resource
import subprocess


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True, help='New output directory')
    args = ap.parse_args()
    source = args.source.resolve(strict=True)
    build = args.build.resolve(strict=True)
    work = args.work.absolute()
    work.mkdir(parents=True, exist_ok=False)
    binary = work / 'fmask-replay'
    command = ['/usr/bin/g++', '-std=c++17', '-O2', '-march=x86-64',
               '-DADDR_FASTCALL=', '-I' + str(source / 'src/amd/addrlib/inc'),
               str(Path(__file__).with_name('fmask-repro.cpp')),
               str(build / 'src/amd/addrlib/libaddrlib.a'), '-o', str(binary)]
    subprocess.run(command, check=True)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    results = []
    for mode in range(5):
        p = subprocess.run([str(binary), str(mode)], check=False,
                           capture_output=True, text=True, timeout=10)
        results.append(dict(mode=mode, exit=p.returncode,
                            stdout=p.stdout, stderr=p.stderr))
    (work / 'RESULT.json').write_text(json.dumps(dict(
        compiler_command=command, results=results), indent=2) + '\n')
    if any(r['exit'] != 0 for r in results):
        raise SystemExit('FAIL: positive or missing-entry replay; inspect RESULT.json')
    # The original library with a valid synthetic unused3D entry produced these
    # layouts. Requiring identical outputs avoids changing2D allocation sizes.
    expected = [(2, 256, 128), (4, 256, 128), (8, 128, 64)]
    for mode in (0, 1, 4):
        lines = results[mode]['stdout'].splitlines()
        wanted = [f'mode={mode} samples={n} status=0 tile={15 if mode == 4 else 14}'
                  f' pitch={pitch} height={height} bytes=32768 align=32768'
                  ' internal_pointer=null' for n, pitch, height in expected]
        if lines != wanted:
            raise SystemExit(f'FAIL: changed layout or temporary pointer in mode{mode}')
    for mode in (2, 3):
        wanted = [f'mode={mode} samples={n} status=3 tile=0 pitch=0 height=0'
                  ' bytes=0 align=0 internal_pointer=null' for n, _, _ in expected]
        if results[mode]['stdout'].splitlines() != wanted:
            raise SystemExit(f'FAIL: missing selected entry not rejected in mode{mode}')
    print('PASS: 15 CPU-only FMASK cases; no GPU access or hardware acceptance')


if __name__ == '__main__':
    main()
