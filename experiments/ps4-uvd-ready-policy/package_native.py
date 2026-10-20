#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Package the measured private UVD poll build; never reads a device."""
from pathlib import Path
import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--workspace', type=Path, required=True)
args = parser.parse_args()
WORKSPACE = args.workspace.resolve()
ROOT = WORKSPACE / 'lab/uvd-2026-10-19'
REPO = ROOT / 'linux-psx-uvd-poll-dev1'
REPORT = ROOT / 'ready-policy/build'
TARGET = '7.2-ps4-baikal-uvd-poll-dev1'
RELEASE = '7.2.11-psx-ps4-baikal-uvd-poll-dev1'
SOURCE = REPO / 'src' / TARGET
OUTPUT = REPO / 'output' / TARGET
EPOCH = 1792195200
os.environ['PATH'] = '/usr/bin:/bin:/usr/bin/core_perl'
os.environ['GIT_OPTIONAL_LOCKS'] = '0'

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

def save(name, value):
    (REPORT / name).write_text(json.dumps(value, indent=2) + '\n')

def git_names(root, *args):
    return [p.decode() for p in subprocess.check_output(['/usr/bin/git', '-C', str(root),
            'ls-files', '-z', *args]).split(b'\0') if p]

def row(root, name):
    p = root / name
    s = p.lstat()
    result = {'path': name, 'mode': oct(stat.S_IMODE(s.st_mode)), 'bytes': s.st_size}
    if p.is_symlink():
        result.update(kind='symlink', target=os.readlink(p))
    else:
        assert stat.S_ISREG(s.st_mode), p
        result.update(kind='regular', sha256=sha(p))
    return result

def archive(destination, entries, extra=None):
    assert not destination.exists(), destination
    proc = subprocess.Popen(['/usr/bin/zstd', '-q', '-T2', '-c'], stdin=subprocess.PIPE,
                            stdout=destination.open('wb'))
    with tarfile.open(fileobj=proc.stdin, mode='w|', format=tarfile.PAX_FORMAT) as tf:
        for name, path in sorted(entries):
            info = tf.gettarinfo(str(path), arcname=name)
            assert info.isfile() or info.issym(), name
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = EPOCH
            info.pax_headers = {}
            if info.isfile():
                with path.open('rb') as f:
                    tf.addfile(info, f)
            else:
                tf.addfile(info)
        for name, data in sorted((extra or {}).items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.uid = info.gid = 0
            info.mtime = EPOCH
            tf.addfile(info, io.BytesIO(data))
    proc.stdin.close()
    assert proc.wait() == 0

assert (REPORT / 'build-receipt.json').is_file(), 'kernel build has not finished'
tracked = git_names(REPO)
new = git_names(REPO, '--others', '--exclude-standard')
assert len(tracked) == 681 and new
files = [row(REPO, n) for n in sorted(set(tracked + new))]
assert len(files) == 681 + len(new)
prebuild = json.loads((REPORT / 'prebuild-inputs.json').read_text())
assert {r['path']: r['sha256'] for r in files} == {
    r['path']: r['sha256'] for r in prebuild['files']}
assert {r['path']: int(r['mode'], 8) for r in files} == {
    r['path']: int(r['mode'], 8) for r in prebuild['files']}
assert len(files) == prebuild['input_count'] == 700
assert new == prebuild['untracked_overlay_files']
baseline = json.loads((WORKSPACE / 'lab/uvd-2026-10-18/uvd-ready-dev3/inputs.json').read_text())
for old in baseline['files']:
    assert sha(REPO / old['path']) == old['sha256'], old['path']
dev4_root = WORKSPACE / 'lab/uvd-2026-10-18/linux-psx-uvd-ready-dev4'
dev4_inputs = json.loads((WORKSPACE / 'lab/uvd-2026-10-18/uvd-ready-dev4/inputs.json').read_text())
assert dev4_inputs['file_count'] == 689
for old in dev4_inputs['files']:
    assert sha(dev4_root / old['path']) == old['sha256'], old['path']
    assert sha(REPO / old['path']) == old['sha256'], old['path']
dev4_hashes = json.loads((dev4_root / 'private-build/uvd-ready-dev4-source-hashes.json').read_text())
for name, value in dev4_hashes.items():
    assert sha(dev4_root / 'src/7.2-ps4-baikal-uvd-ready-dev4' / name) == value['after_sha256'], name
assert prebuild['project_identity'] == '35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2+UNCOMMITTED-dev4-plus-uvd-poll-dev1-overlay'
inputs = {'schema': 1, 'scope': 'Private exact poll-dev1 build inputs including preserved dev4 and new observation-policy overlay',
          'project_base_commit': '35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2',
          'project_identity': '35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2+UNCOMMITTED-dev4-plus-uvd-poll-dev1-overlay',
          'project_clean': False, 'tracked_baseline_files': 681, 'all681_baseline_bytes_unchanged': True,
          'all689_dev4_inputs_preserved': True, 'frozen_dev3_and_dev4_inputs_preserved': True,
          'untracked_overlay_files': new, 'file_count': len(files), 'files': files,
          'upstream_commit': 'a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea',
          'target': TARGET, 'release': RELEASE, 'active_patch_count': 68}
save('inputs.json', inputs)
archive(REPORT / 'inputs.tar.zst', [(n, REPO / n) for n in tracked + new],
        {'PSX-INPUTS-MANIFEST.json': (REPORT / 'inputs.json').read_bytes()})
archive(REPORT / 'dirty-overlay.tar.zst', [(n, REPO / n) for n in new],
        {'PSX-OVERLAY-MANIFEST.json': json.dumps({'base_commit': inputs['project_base_commit'],
         'files': [row(REPO, n) for n in new]}, indent=2).encode() + b'\n'})

source_names = set(git_names(SOURCE))
series = REPO / f'platforms/ps4/patches/{TARGET}/series'
patchroot = REPO / 'platforms/ps4/patches/7.2-ps4-baikal'
patches = []
for line in series.read_text().splitlines():
    if not line.strip() or line.lstrip().startswith('#'):
        continue
    patch = (patchroot / line.strip()).resolve()
    patches.append(patch)
    for match in re.finditer(rb'^\+\+\+ b/(.+)$', patch.read_bytes(), re.M):
        name = match.group(1).decode()
        assert not Path(name).is_absolute() and '..' not in Path(name).parts, name
        if (SOURCE / name).exists():
            source_names.add(name)
assert len(patches) == 68
source_names.add('.config')
assert 'COPYING' in source_names and any(n.startswith('LICENSES/') for n in source_names)
for n in source_names:
    assert not n.startswith('.git/') and not n.endswith(('.o', '.a', '.ko')), n
    assert (SOURCE / n).exists() or (SOURCE / n).is_symlink(), n
source_rows = [row(SOURCE, n) for n in sorted(source_names)]
save('source-inputs.json', {'schema': 1, 'source': str(SOURCE), 'release': RELEASE,
     'selection': 'All upstream tracked paths, every active patch output including ignored additions, resolved .config; unrelated untracked files excluded',
     'file_count': len(source_rows), 'generated_objects_or_git_history': False,
     'inputs_archive_sha256': sha(REPORT / 'inputs.tar.zst'), 'files': source_rows})
rebuild = f'''# Private poll-dev1 source snapshot

Target: {TARGET}
Release: {RELEASE}
Project: {inputs['project_identity']}
Upstream: {inputs['upstream_commit']}

This archive contains complete patched Linux source and resolved .config.
PSX-BUILD-INPUTS.tar.zst contains all 681 unchanged project baseline files and
the complete UNCOMMITTED dev4 plus poll-dev1 overlay, with a path/hash/mode manifest.
No commit, release, or hardware acceptance is claimed.

On a Linux x86-64 host with the recorded native tools, compile this extracted
source using the recorded identity and existing .config:

    export PATH=/usr/bin:/bin:/usr/bin/core_perl
    export SOURCE_DATE_EPOCH={EPOCH}
    export KBUILD_BUILD_TIMESTAMP='2026-10-17 00:00:00 UTC'
    export KBUILD_BUILD_USER=linux-psx KBUILD_BUILD_HOST=builder KBUILD_BUILD_VERSION=1
    make ARCH=x86_64 LOCALVERSION= olddefconfig
    make ARCH=x86_64 LOCALVERSION= syncconfig
    make ARCH=x86_64 LOCALVERSION= -j6 bzImage modules

Use the private native build script in PSX-BUILD-INPUTS.tar.zst for the exact
configuration merge, source hash assertions, module installation and package
policy when rebuilding a matching Git source checkout. The source/input
manifests and module archive are private local artifacts; no firmware is
embedded or bundled. Historical hardware results do not qualify this build.
'''.encode()
prefix = f'linux-{RELEASE}'
archive(REPORT / f'source-{RELEASE}.tar.zst',
        [(f'{prefix}/{n}', SOURCE / n) for n in source_names],
        {f'{prefix}/PSX-BUILD-INPUTS.tar.zst': (REPORT / 'inputs.tar.zst').read_bytes(),
         f'{prefix}/PSX-BUILD-MANIFEST.txt': (OUTPUT / 'manifest.txt').read_bytes(),
         f'{prefix}/PSX-SOURCE-INPUTS.json': (REPORT / 'source-inputs.json').read_bytes(),
         f'{prefix}/PSX-REBUILD.md': rebuild})
summary = {'input_files': len(files), 'baseline_inputs_unchanged': 681,
           'frozen_dev3_and_dev4_inputs_preserved': True,
           'new_overlay_files': len(new), 'packaging_script_sha256': sha(Path(__file__)), 'complete_source_input_files': len(source_rows),
           'project_base_commit': inputs['project_base_commit'], 'project_clean': False,
           'inputs_archive_sha256': sha(REPORT / 'inputs.tar.zst'),
           'dirty_overlay_sha256': sha(REPORT / 'dirty-overlay.tar.zst'),
           'source_archive_sha256': sha(REPORT / f'source-{RELEASE}.tar.zst'),
           'source_archive_bytes': (REPORT / f'source-{RELEASE}.tar.zst').stat().st_size}
save('source-package.json', summary)
print(json.dumps(summary, indent=2))
