#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build and verify the explicit, volatile Diagnostix RAM appliance.

Host build never mounts images or operates hardware. The runtime verifier uses
only protected files, sysfs observations and mount metadata; init-ram mounts.
"""
from __future__ import annotations
import argparse
import copy
import gzip
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import time
import uuid

PROFILE = 'ps4-baikal-diagnostix-ram-alpha1'
APPLIANCE_VERSION = '0.16'
RELEASE = '7.2.11-psx-ps4-baikal-uvd-runtime-dev6'
EXPECTED = '/etc/psx-diagnostics/expected-ram-boot.json'
MANIFEST = '/etc/psx-diagnostics/boot-set.json'
PROOF = '/run/psx-ram/verified-boot.json'
BACKING = '/run/psx-image/rootfs.ext4'
ROOT_BYTES = 1000 * 1024**2
BACKING_BYTES = 1088 * 1024**2
STATE_BYTES = 128 * 1024**2
CAP = 1024**3
PINS = {
    'kernel_sha256': 'a7264426ee3b2ffeb030a04bbd7ab070418ecf4c0272b83400b4d5f45473bff4',
    'config_sha256': 'ee77a55ff46f9d201d71efb0b87adca4cb1f1bf253c301c9cd8da3b5c4a724ff',
    'modules_sha256': '0e50c537c2395154cff659d9722d165027d0ecea875dfcb14d32d798f9c9e14d',
    'rootfs_sha256': 'b5b3a0ec3001fa99816ad68071d93091a6c8d0907aeb4ba384094625b7af0107',
}
# The frozen root with only Firefox removed, for Chromium (owner decision, 2026-11-10).
CHROMIUM_ROOT_SHA256 = '6fb9fc119c94679e3e2a86c86e1182cf0563256f975f833ad321ec95b6ada0c7'
SOURCE_SUMS = 'd48fe0a6c2827084634cd6fc20102f9161143afaa03b33d72d5a56a74348f9cd'
SOURCE_INIT = '72badba925a3c9ede549d805a7a298fa65b4e7358e96f54c2c4a5e999135ce2a'
REPO = Path(__file__).resolve().parents[2]
EXTRA_TARGETS = {
    'ram_boot.py': (None, 0o755), 'ram_state.py': (None, 0o755),
    'prepare-ssh-host-key': ('/usr/lib/psx-diagnostics/prepare-ssh-host-key', 0o755),
    'psx-ssh-host-key.service': ('/usr/lib/systemd/system/psx-ssh-host-key.service', 0o644),
    'psx-diagnostics-control.service': ('/usr/lib/systemd/system/psx-diagnostics-control.service', 0o644),
    'runner.py': ('/opt/psx-diagnostics/runner.py', 0o755),
    'uvd.py': ('/opt/psx-diagnostics/uvd.py', 0o755),
    'uvd_suite.py': ('/opt/psx-diagnostics/uvd_suite.py', 0o755),
    'support_report.py': (None, 0o644),
}

FRONTEND_TARGETS = {'JetBrainsMonoNerdFont-OFL.txt': (None, 420),
 'JetBrainsMonoNerdFont-Regular.ttf': (None, 420),
 'controller.py': ('/opt/psx-diagnostics/controller.py', 493),
 'graphics-client': ('/usr/lib/psx-diagnostics/graphics-client', 493),
 'graphics-session': ('/usr/lib/psx-diagnostics/graphics-session', 493),
 'graphics-start': ('/usr/lib/psx-diagnostics/graphics-start', 493),
 'graphics-window': (None, 493),
 'index.html': ('/opt/psx-diagnostics/lab/index.html', 420),
 'lab.js': ('/opt/psx-diagnostics/lab/lab.js', 420),
 'launch-chromium-provider': (None, 493),
 'launch-graphics': ('/usr/lib/psx-diagnostics/launch-graphics', 493),
 'libdisplay-info.so.3': (None, 420),
 'libgtk-layer-shell.so.0': (None, 420),
 'libutempter.so.0': (None, 420),
 'libvte-2.91.so.0': (None, 420),
 'libxcb-keysyms.so.1': (None, 420),
 'network.py': ('/opt/psx-diagnostics/network.py', 493),
 'preferences.py': (None, 493),
 'prepare-chromium-provider': (None, 493),
 'prepare-console': (None, 493),
 'psx-diagnostics-tui': ('/usr/bin/psx-diagnostics-tui', 493),
 'ssh_access.py': (None, 493),
 'start-console': ('/usr/lib/psx-diagnostics/start-console', 493),
 'style.css': ('/opt/psx-diagnostics/lab/style.css', 420),
 'themes-LICENSE': (None, 420),
 'themes.json': (None, 420),
 'wait-graphics-wm': (None, 493),
 'xfce4-terminal': (None, 493),
 'xorg-diagnostics.conf': ('/etc/X11/xorg.conf.d/20-psx-diagnostics.conf', 420)}

NATIVE_FILES = {
    '720p30/FIXTURE.json': 'c36b64dfddcefbe54f320729b6471b5703d2daedbb886b74d0a9c0be2312fd13',
    '720p30/clip.h264': '7acad559ea292fa5121761d1b7d5ab217d3af2d69c987fcd60ec98efa814c1e7',
    '720p30/reference-nv12.framemd5': 'c23e88280006c8513bf8775f0b4bbd13f9c5a91d70477414d08e257bf6ee9565',
    '1080p30/FIXTURE.json': '32f52ec167633dcf6cfea5e525af51097cacf130798cad680c713e9368db73f3',
    '1080p30/clip.h264': 'e6f417f1de7dcf96bd517b8b12b2fde28579edbe21bed4e3993414332bbeb08b',
    '1080p30/reference-nv12.framemd5': '923fc8f20dadb89b98f35d59d3b42fcf85e2f6192e5d8ac286fd9f8398733bca',
    'LICENSE': 'b675cd5546d49b9b0ea5b61632a33fd2b494ff71e25d8f7eed3fc1a91a54f4c4',
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked(path, expected=None, maximum=CAP, *, virtual=False):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    h = hashlib.sha256()
    with os.fdopen(fd, 'rb') as f:
        before = os.fstat(f.fileno())
        require(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= maximum,
                'Expected a bounded regular file: ' + path.name)
        count = 0
        while chunk := f.read(1024**2):
            count += len(chunk)
            require(count <= maximum, 'File exceeds its bound: ' + path.name)
            h.update(chunk)
        after = os.fstat(f.fileno())
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            and (virtual or count == before.st_size), 'File changed while reading: ' + path.name)
    require(expected is None or h.hexdigest() == expected, 'File checksum differs: ' + path.name)
    return {'sha256': h.hexdigest(), 'bytes': count}


def local(root, name):
    root = Path(root).resolve()
    path = root / name.lstrip('/')
    require(path.resolve() == path and path.is_relative_to(root), 'Symlink/escaped RAM boot path')
    return path


def read_json(path):
    checked(path, maximum=1024**2)
    return json.loads(Path(path).read_text(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def state_contract():
    return {'kind': 'volatile-tmpfs', 'path': '/run/psx-state',
            'results_dir': '/run/psx-state/results', 'state_dir': '/run/psx-state/state',
            'maximum_bytes': STATE_BYTES, 'persistent': False, 'survives_controller_restart': True}


def pinned_identity(value):
    return all(value.get(k) == v or (k == 'rootfs_sha256' and value.get(k) == CHROMIUM_ROOT_SHA256)
               for k, v in PINS.items())


def validate_manifest(value):
    require(isinstance(value, dict) and value.get('profile') == PROFILE
            and value.get('image_version') == 'ram-alpha1' and value.get('kernel_release') == RELEASE
            and value.get('hardware_acceptance') is False and value.get('module_count') == 41
            and value.get('root_format') == 'ext4-ro-loop' and value.get('rootfs_image_bytes') == ROOT_BYTES
            and pinned_identity(value), 'Unmatched RAM kernel/root/module identity')
    require(value.get('state') == state_contract(), 'Invalid volatile-state policy')
    source = value.get('boot_source', {})
    require(source.get('kind') == 'embedded-ram' and source.get('backing_path') == BACKING
            and source.get('compressed_path') == '/psx-ram/rootfs.ext4.gz'
            and source.get('maximum_backing_bytes') == BACKING_BYTES
            and type(source.get('compressed_bytes')) is int and 0 < source['compressed_bytes'] < CAP
            and re.fullmatch('[0-9a-f]{64}', str(source.get('compressed_sha256', ''))), 'Invalid RAM root transport')
    require('initramfs_sha256' not in value and 'manifest_validation' not in value,
            'Embedded contract cannot claim its own compressed initramfs hash')
    front = value.get('frontend_overrides', {}).get('files', [])
    require(len(front) == 29 and {r['name'] for r in front} == set(FRONTEND_TARGETS)
            and all((r.get('bind_target'), int(r.get('mode', '0'), 8)) == FRONTEND_TARGETS[r['name']] for r in front),
            'Frontend aliases/targets differ from the fixed policy')
    extra = value.get('ram_assets', {}).get('files', [])
    require({'ram_boot.py', 'ram_state.py', 'prepare-ssh-host-key', 'psx-ssh-host-key.service',
             'psx-diagnostics-control.service'} <= {r['name'] for r in extra}
            and len(extra) == len({r['name'] for r in extra})
            and all(r['name'] in EXTRA_TARGETS and (r.get('bind_target'), int(r.get('mode', '0'), 8)) == EXTRA_TARGETS[r['name']] for r in extra),
            'RAM integration aliases/targets differ')
    return value


def verify_gzip(path, expected_bytes, expected_sha):
    require(type(expected_bytes) is int and 0 < expected_bytes <= ROOT_BYTES, 'Invalid inflated root size')
    h = hashlib.sha256(); count = 0
    with gzip.open(path, 'rb') as f:
        while chunk := f.read(min(1024**2, expected_bytes + 1 - count)):
            count += len(chunk)
            require(count <= expected_bytes, 'Inflated root exceeds the exact size')
            h.update(chunk)
    require(count == expected_bytes and h.hexdigest() == expected_sha, 'Inflated root checksum/size differs')
    return {'bytes': count, 'sha256': h.hexdigest()}


def _mounts(root):
    path = local(root, f'/proc/{os.getpid()}/mountinfo')
    rows = {}
    checked(path, maximum=1024**2, virtual=True)
    for line in path.read_text().splitlines():
        left, right = line.split(' - ', 1); a, b = left.split(), right.split()
        require(a[4] not in rows, 'Ambiguous mountpoint')
        rows[a[4]] = {'fs': b[0], 'source': b[1], 'options': a[5].split(','), 'device': a[2]}
    return rows


def _verify_asset(root, name, record, base):
    require(isinstance(name, str) and re.fullmatch('[A-Za-z0-9_.-]+', name), 'Invalid RAM asset name')
    path = local(root, base + '/' + name)
    info = checked(path, record['sha256'], maximum=8 * 1024**2)
    require(info['bytes'] == record['bytes'] and stat.S_IMODE(path.stat().st_mode) == int(record['mode'], 8),
            'RAM asset mode/size differs')
    target = record.get('bind_target')
    if target:
        require(target.startswith(('/opt/psx-diagnostics/', '/usr/lib/psx-diagnostics/', '/usr/bin/',
                                   '/etc/X11/', '/usr/lib/systemd/system/')), 'Unexpected asset target')
        require(checked(local(root, target), record['sha256'], maximum=8 * 1024**2)['bytes'] == record['bytes'],
                'Bound runtime asset differs')


def verify(root_prefix='/'):
    """Verify the mounted RAM appliance and write its private same-boot proof."""
    root = Path(root_prefix).resolve(); expected_path = local(root, EXPECTED)
    contract = validate_manifest(read_json(expected_path)); raw = expected_path.read_bytes()
    require(raw == encoded(contract), 'Expected RAM contract is not canonical')
    release = local(root, '/proc/sys/kernel/osrelease').read_text().strip()
    boot = local(root, '/proc/sys/kernel/random/boot_id').read_text().strip()
    require(release == RELEASE and str(uuid.UUID(boot)) == boot, 'Running kernel/boot identity differs')
    tokens = local(root, '/proc/cmdline').read_text().split()
    require(tokens.count('psx.boot=ram') == 1 and not any(t.startswith('psx.diag_uuid=') for t in tokens),
            'RAM boot selection is missing or mixed with USB')
    required = contract['required_boot_arguments'] + contract.get('required_sata_boot_arguments', []) + ['psx_mts_net.autoarm=1']
    for argument in required:
        key = argument.split('=', 1)[0]
        require([t for t in tokens if t.split('=', 1)[0] == key] == [argument], 'Missing/duplicated boot gate: ' + key)
        if key.startswith('amdgpu.ps4_uvd'):
            require(local(root, '/sys/module/amdgpu/parameters/' + key.split('.')[1]).read_text().strip() in ('1', 'Y'),
                    'Effective UVD/power gate differs')
    rows = _mounts(root)
    require(rows.get('/', {}).get('fs') == 'ext4' and 'ro' in rows['/']['options']
            and re.fullmatch('/dev/loop[0-9]+', rows['/']['source']), 'Root is not the read-only RAM loop image')
    owner = 0 if root == Path('/') else os.geteuid()
    for name in ('/run/psx-image', '/run/psx-state'):
        path = local(root, name); info = path.stat(); row = rows.get(name, {})
        require(row.get('fs') == 'tmpfs' and 'rw' in row.get('options', [])
                and row.get('device') == f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
                and info.st_uid == owner and not stat.S_IMODE(info.st_mode) & 0o077,
                'Protected RAM backing/state mount differs')
    backing = local(root, BACKING)
    require(stat.S_IMODE(backing.stat().st_mode) == 0o400 and backing.stat().st_uid == owner,
            'RAM image is not protected')
    require(checked(backing, contract['rootfs_sha256'])['bytes'] == ROOT_BYTES, 'RAM root byte count differs')
    loop = rows['/']['source'].rsplit('/', 1)[1]
    sysfs = root / 'sys/class/block' / loop
    for suffix, wanted in (('ro', '1'), ('loop/backing_file', BACKING), ('loop/offset', '0')):
        path = (sysfs / suffix).resolve(strict=True)
        require(path.is_relative_to(root / 'sys') and path.read_text().strip() == wanted, 'Loop backing policy differs')
    modules = contract.get('module_files', [])
    require(len(modules) == 41 and len({r['path'] for r in modules}) == 41, 'Incomplete matching module list')
    for record in modules:
        require(record['path'].startswith('/usr/lib/modules/' + RELEASE + '/'), 'Unrelated module file')
        require(checked(local(root, record['path']), record['sha256'], maximum=32 * 1024**2)['bytes'] == record['bytes'],
                'Root module file differs')
    frontend = contract['frontend_overrides']['files']
    require(len(frontend) == 29 and len({r['name'] for r in frontend}) == 29, 'Incomplete frontend assets')
    for record in frontend: _verify_asset(root, record['name'], record, '/run/psx-frontend')
    for record in contract['ram_assets']['files']: _verify_asset(root, record['name'], record, '/run/psx-ram')
    assets = contract['data_assets']
    require(assets['runtime_directory'] == '/run/psx-assets' and assets['maximum_bytes'] == 64 * 1024**2
            and rows.get('/run/psx-assets', {}).get('fs') == 'tmpfs'
            and 'ro' in rows['/run/psx-assets']['options'], 'Native fixture RAM mount differs')
    require({r['path'].removeprefix('fixtures/native-video/'): r['sha256'] for r in assets['files']} == NATIVE_FILES,
            'Native fixture asset set differs')
    for record in assets['files']:
        require(record['path'].startswith('fixtures/native-video/') and '..' not in PurePosixPath(record['path']).parts,
                'Invalid fixture asset path')
        path = local(root, '/run/psx-assets/' + record['path'])
        require(checked(path, record['sha256'], maximum=64 * 1024**2)['bytes'] == record['bytes']
                and stat.S_IMODE(path.stat().st_mode) == 0o644, 'Native fixture asset differs')
    validation = {'embedded_root_contract_verified': True, 'ram_boot_contract_verified': True,
                  'expected_contract_sha256': sha(raw), 'observed_kernel_release': release,
                  'kernel_hash_scope': 'build contract; running kernel bytes not measured',
                  'hardware_acceptance': False}
    final = dict(contract, manifest_validation=validation)
    local(root, MANIFEST).write_bytes(encoded(final)); local(root, MANIFEST).chmod(0o600)
    proof = {k: contract[k] for k in ('profile', 'kernel_release', 'kernel_sha256', 'config_sha256', 'modules_sha256',
             'module_count', 'rootfs_sha256', 'rootfs_image_bytes', 'frontend_overrides', 'boot_source', 'state', 'data_assets')}
    proof.update(schema_version=1, kind='psx-diagnostix-ram-boot-v1', boot_id=boot, manifest_sha256=sha(raw),
                 modules_verified=41, assets_verified=len(frontend) + len(contract['ram_assets']['files']) + len(assets['files']), hardware_acceptance=False)
    path = local(root, PROOF); temporary = path.with_suffix('.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f: f.write(encoded(proof)); f.flush(); os.fsync(f.fileno())
    os.replace(temporary, path)
    return proof


def _load(name):
    path = REPO / 'scripts' / name
    loader = importlib.machinery.SourceFileLoader('psx_ram_' + name.replace('-', '_'), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader); module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _module_files(archive):
    records = []
    with subprocess.Popen(['zstd', '-dc', str(archive)], stdout=subprocess.PIPE) as p:
        with tarfile.open(fileobj=p.stdout, mode='r|') as t:
            for member in t:
                if not member.name.endswith('.ko'): continue
                require(member.isfile() and 0 < member.size <= 32 * 1024**2
                        and member.name.startswith('lib/modules/' + RELEASE + '/') and '..' not in PurePosixPath(member.name).parts,
                        'Invalid matching-module archive member')
                data = t.extractfile(member).read()
                records.append({'path': '/usr/' + member.name, 'bytes': len(data), 'sha256': sha(data)})
        require(p.wait() == 0 and len(records) == 41, 'Incomplete matching module archive')
    return records


def render_bootargs(template):
    tokens = template.split()
    require(tokens.count('psx.diag_uuid=@USB_UUID@') == 1 and not any(t.startswith('psx.boot=') for t in tokens),
            'Frozen source boot selection differs')
    tokens.remove('psx.diag_uuid=@USB_UUID@'); tokens.append('psx.boot=ram')
    require(len(' '.join(tokens).encode()) <= 511, 'RAM loader arguments exceed 511 bytes')
    return ' '.join(tokens) + '\n'


def _package_kernel_guard(data, component):
    """Adapt only a known strict release declaration; keep its runtime check."""
    require(isinstance(data, bytes), component + ' frontend must be source bytes')
    pattern = rb'^RELEASE = "([^"\r\n]+)"$'
    releases = re.findall(pattern, data, re.M)
    require(len(releases) == 1 and releases[0] in (
        b'7.2.11-psx-ps4-baikal-native-dev3', RELEASE.encode()),
        component + ' frontend has an unrecognized kernel release guard')
    return re.sub(pattern, b'RELEASE = "' + RELEASE.encode() + b'"', data, flags=re.M)


def package_ssh_access(data):
    return _package_kernel_guard(data, 'SSH')


def package_network(data):
    return _package_kernel_guard(data, 'Network')


def build(source, output, frontend_files=None, runtime_files=None, frontend_dir=None):
    """Build a NEW host directory; read parent integration source at call time."""
    source = Path(source).resolve(strict=True); output = Path(output).absolute()
    require(not output.exists() and not output.is_symlink() and output.parent.resolve() == output.parent
            and not output.is_relative_to(source) and not source.is_relative_to(output), 'New disjoint canonical output required')
    checked(source / 'SHA256SUMS', SOURCE_SUMS, 1024**2)
    stage = _load('stage-ps4-diagnostics-usb'); image = _load('build-ps4-diagnostics'); archive = _load('ps4-diagnostics-archive')
    files, old = stage.source_files(source)
    require(old.get('profile') == 'ps4-baikal-diagnostics-uvd-alpha7' and old.get('kernel_release') == RELEASE
            and old.get('rootfs_image_bytes') == ROOT_BYTES and old.get('module_count') == 41
            and old.get('initramfs_sha256') == SOURCE_INIT and all(old.get(k) == v for k, v in PINS.items()),
            'The exact finalized clean dev6 source boot set is required')
    entries, _ = archive.parse_newc(gzip.decompress((source / 'initramfs.cpio.gz').read_bytes()))
    seed = {e.name.decode(): e for e in entries}; archive.validate_busybox(seed['bin/busybox'].data)
    embedded = json.loads(seed['etc/psx-diagnostics-expected.json'].data)
    require(dict(embedded, initramfs_sha256=SOURCE_INIT) == old, 'Frozen source embedded/external contracts differ')
    frontend = {name: seed['psx-frontend/' + name].data for name in image.FRONTEND_FILES}
    require(image.frontend_manifest(frontend, old['snapshot_sums_sha256']) == old['frontend_overrides'], 'Frozen frontend differs')
    reads = {}
    def current(path):
        path = Path(path); checked(path, maximum=8 * 1024**2); data = path.read_bytes(); reads[path] = sha(data); return data
    for name in frontend:
        path = (REPO / 'diagnostics/ps4' / name if name in ('controller.py', 'network.py', 'preferences.py', 'ssh_access.py') else
                REPO / 'diagnostics/ps4/lab' / name if name in ('index.html', 'lab.js', 'style.css') else
                REPO / 'diagnostics/ps4' / name if name in ('themes.json', 'themes-LICENSE') else
                REPO / 'rootfs/ps4-diagnostics-desktop' / ('20-psx-diagnostics.conf' if name == 'xorg-diagnostics.conf' else name))
        if path.is_file(): frontend[name] = current(path)
    tui = Path('/var/tmp/psx-diagnostix-overview-target/release/psx-diagnostics-tui')
    if tui.is_file(): frontend['psx-diagnostics-tui'] = current(tui)
    if frontend_dir:
        for name in frontend: frontend[name] = current(Path(frontend_dir) / name)
    if frontend_files:
        require(set(frontend_files) <= set(frontend), 'Unrecognized frontend override')
        frontend.update(frontend_files)
    frontend['ssh_access.py'] = package_ssh_access(frontend['ssh_access.py'])
    frontend['network.py'] = package_network(frontend['network.py'])
    extras = {'ram_boot.py': current(__file__), 'ram_state.py': current(REPO / 'diagnostics/ps4/ram_state.py'),
              'prepare-ssh-host-key': current(REPO / 'rootfs/ps4-diagnostics-gui/prepare-ssh-host-key'),
              'psx-ssh-host-key.service': current(REPO / 'rootfs/ps4-diagnostics-gui/psx-ssh-host-key.service'),
              'psx-diagnostics-control.service': current(REPO / 'rootfs/ps4-diagnostics-ram/psx-diagnostics-control.service'),
              'runner.py': current(REPO / 'diagnostics/ps4/runner.py'),
              'uvd.py': current(REPO / 'diagnostics/ps4/uvd.py'),
              'uvd_suite.py': current(REPO / 'diagnostics/ps4/uvd_suite.py'),
              'support_report.py': current(REPO / 'diagnostics/ps4/support_report.py')}
    if runtime_files:
        require(set(runtime_files) <= set(EXTRA_TARGETS), 'Unrecognized RAM runtime override')
        extras.update(runtime_files)
    extras['prepare-ssh-host-key'] = extras['prepare-ssh-host-key'].replace(b'7.2.11-psx-ps4-baikal-native-dev3', RELEASE.encode())
    extras['uvd.py'] = extras['uvd.py'].replace(
        b'8999f750efd20cf71f176efb5b5febb0681c493e23ca1dfb6f0bc7bcf8288626',
        old['uvd_diagnostic']['fixture_sha256'].encode())
    require(all(isinstance(v, bytes) and 0 < len(v) <= 8 * 1024**2 for v in extras.values()), 'Invalid RAM runtime asset')
    extra_records = [{'name': n, 'sha256': sha(v), 'bytes': len(v), 'mode': f'{EXTRA_TARGETS[n][1]:04o}',
                      'bind_target': EXTRA_TARGETS[n][0]} for n, v in sorted(extras.items())]
    require(sum(len(v) for v in extras.values()) + sum(len(v) for v in frontend.values()) <= 8 * 1024**2, 'RAM overlays exceed 8 MiB')
    native_root = Path('/var/tmp/psx-native-video-fixtures-20261105-01')
    native = {}
    for name, pin in NATIVE_FILES.items():
        checked(native_root / name, pin, 64 * 1024**2)
        native['fixtures/native-video/' + name] = (native_root / name).read_bytes()
    require(sum(map(len, native.values())) <= 64 * 1024**2, 'Native fixture payload exceeds 64 MiB')
    data_records = [{'path': n, 'bytes': len(v), 'sha256': sha(v), 'mode': '0644'} for n, v in sorted(native.items())]
    bootargs = render_bootargs((source / 'bootargs.template.txt').read_text())
    output.mkdir(mode=0o700); work = output / '.work'; work.mkdir()
    try:
        leaf = work / 'rootfs.ext4.gz'
        with (source / old['rootfs_file']).open('rb') as src, leaf.open('xb') as dst:
            with gzip.GzipFile(filename='', mode='wb', fileobj=dst, compresslevel=3, mtime=0) as gz:
                shutil.copyfileobj(src, gz, 1024**2)
        verify_gzip(leaf, ROOT_BYTES, old['rootfs_sha256']); leaf_record = checked(leaf)
        contract = copy.deepcopy(old); contract.pop('initramfs_sha256'); contract.pop('manifest_validation', None)
        contract.update(profile=PROFILE, image_version='ram-alpha1', appliance_version=APPLIANCE_VERSION, rootfs_file=BACKING,
            boot_source={'kind': 'embedded-ram', 'backing_path': BACKING, 'compressed_path': '/psx-ram/rootfs.ext4.gz',
                         'compressed_bytes': leaf_record['bytes'], 'compressed_sha256': leaf_record['sha256'],
                         'maximum_backing_bytes': BACKING_BYTES}, state=state_contract(),
            frontend_overrides=image.frontend_manifest(frontend, old['snapshot_sums_sha256']),
            ram_assets={'files': extra_records, 'maximum_bytes': 8 * 1024**2},
            data_assets={'runtime_directory': '/run/psx-assets', 'maximum_bytes': 64 * 1024**2, 'files': data_records},
            tui_sha256=sha(frontend['psx-diagnostics-tui']), frontend_revision='ram-alpha1-modes-reports-ssh-network-power1',
            module_files=_module_files(source / ('psx-diagnostics/modules-' + RELEASE + '.tar.zst')),
            source_lineage={'profile': old['profile'], 'boot_set_sha256': sha((source / 'psx-diagnostics/boot-set.json').read_bytes()),
                            'initramfs_sha256': SOURCE_INIT, 'source_sums_sha256': SOURCE_SUMS},
            ram_builder_sha256=sha(current(__file__)))
        validate_manifest(contract); raw = encoded(contract)
        env = (f"KERNEL_RELEASE='{RELEASE}'\nROOTFS_SHA256='{old['rootfs_sha256']}'\nROOTFS_BYTES='{ROOT_BYTES}'\n"
               f"COMPRESSED_SHA256='{leaf_record['sha256']}'\nCOMPRESSED_BYTES='{leaf_record['bytes']}'\n"
               f"BUILD_EPOCH='{int(time.time())}'\nMTS_AUTOARM='1'\nSATA_POLICY='{old['sata_policy']}'\n"
               "ROOT_FORMAT='ext4-ro-loop'\nROOTFS_FILENAME='rootfs.ext4'\nWRITABLE_ROOT_MIB='512'\nFRONTEND_OVERRIDES='1'\nBACKING_MIB='1088'\nSTATE_MIB='128'\n").encode()
        replacements = {'init': (current(REPO / 'rootfs/ps4-diagnostics/init-ram'), stat.S_IFREG | 0o755),
                        'etc/psx-ram.env': (env, stat.S_IFREG | 0o644),
                        'etc/psx-ram-contract.json': (raw, stat.S_IFREG | 0o600)}
        front_rows = []
        for record in contract['frontend_overrides']['files']:
            n = record['name']; replacements['psx-frontend/' + n] = (frontend[n], stat.S_IFREG | int(record['mode'], 8))
            front_rows.append(f"{n}\t{record['sha256']}\t{record['bytes']}\t{record['mode']}\n")
        replacements['etc/psx-frontend.tsv'] = (''.join(front_rows).encode(), stat.S_IFREG | 0o644)
        extra_rows = []
        for record in extra_records:
            n = record['name']; replacements['psx-ram/' + n] = (extras[n], stat.S_IFREG | int(record['mode'], 8))
            extra_rows.append(f"{n}\t{record['sha256']}\t{record['bytes']}\t{record['mode']}\n")
        replacements['etc/psx-ram-assets.tsv'] = (''.join(extra_rows).encode(), stat.S_IFREG | 0o644)
        data_rows = []
        for record in data_records:
            n = record['path']; replacements['psx-assets/' + n] = (native[n], stat.S_IFREG | 0o644)
            data_rows.append(f"{n}\t{record['sha256']}\t{record['bytes']}\t0644\n")
        replacements['etc/psx-ram-data.tsv'] = (''.join(data_rows).encode(), stat.S_IFREG | 0o644)
        additions = set(replacements) - set(seed)
        directories = {str(p) for name in additions | {'psx-ram/rootfs.ext4.gz'} for p in PurePosixPath(name).parents if str(p) != '.'} - set(seed)
        init = output / 'initramfs.cpio.gz'
        with init.open('xb') as destination, gzip.GzipFile(filename='', fileobj=destination, mode='wb', compresslevel=1, mtime=0) as gz:
            ino = 900000
            for name in sorted(directories, key=lambda n: (n.count('/'), n)):
                gz.write(archive.entry(ino, stat.S_IFDIR | 0o755, 2, 0, name)); ino += 1
            for e in entries:
                name = e.name.decode()
                if name in replacements:
                    data, mode = replacements[name]; gz.write(archive.entry(ino, mode, 1, 0, name, data)); ino += 1
                else: gz.write(e.raw)
            for name in sorted(additions):
                data, mode = replacements[name]; gz.write(archive.entry(ino, mode, 1, 0, name, data)); ino += 1
            # Emit the large compressed leaf without making another full in-memory copy.
            name = 'psx-ram/rootfs.ext4.gz'; header = bytearray(archive.entry(ino, stat.S_IFREG | 0o400, 1, 0, name))
            header[54:62] = f"{leaf_record['bytes']:08X}".encode(); gz.write(header)
            with leaf.open('rb') as src: shutil.copyfileobj(src, gz, 1024**2)
            gz.write(b'\0' * ((-leaf_record['bytes']) % 4)); ino += 1
            gz.write(archive.entry(ino, 0, 1, 0, 'TRAILER!!!'))
        parsed, _ = archive.parse_newc(gzip.decompress(init.read_bytes()))
        indexed = {e.name.decode(): e for e in parsed}
        require(indexed['etc/psx-ram-contract.json'].data == raw
                and sha(indexed['psx-ram/rootfs.ext4.gz'].data) == leaf_record['sha256'], 'RAM CPIO readback differs')
        for name, (data, mode) in replacements.items():
            require(indexed[name].data == data and indexed[name].mode == mode, 'RAM CPIO asset readback differs')
        shutil.copyfile(source / 'bzImage', output / 'bzImage'); shutil.copyfile(source / 'vram.txt', output / 'vram.txt')
        (output / 'bootargs.template.txt').write_text(bootargs); (output / 'RAM-BOOT.json').write_bytes(raw)
        for path, pin in reads.items(): checked(path, pin, 8 * 1024**2)
        checked(source / 'SHA256SUMS', SOURCE_SUMS, 1024**2)
        artifact_files = {n: checked(output / n) for n in ('bzImage', 'initramfs.cpio.gz', 'vram.txt', 'bootargs.template.txt', 'RAM-BOOT.json')}
        require(sum(r['bytes'] for r in artifact_files.values()) + 1024**2 <= CAP, 'RAM core exceeds 1 GiB cap plus reserve')
        receipt = {'schema_version': 1, 'status': 'PASS_HOST_RAM_BUILD', 'profile': PROFILE, 'hardware_acceptance': False,
                   'contract_sha256': sha(raw), 'files': artifact_files, 'initramfs_sha256': artifact_files['initramfs.cpio.gz']['sha256'],
                   'initramfs_hash_scope': 'actual outer archive; external build receipt only, not a self-measurement',
                   'source_lineage': contract['source_lineage'], 'root_image_unchanged': True, 'no_usb_required': True}
        (output / 'BUILD-RECEIPT.json').write_bytes(encoded(receipt))
        (output / 'SHA256SUMS').write_text(''.join(checked(p)['sha256'] + '  ' + p.name + '\n' for p in sorted(output.iterdir()) if p.is_file()))
        leaf.unlink(); work.rmdir()
        return receipt
    except BaseException:
        (output / 'FAILED-PRESERVED.json').write_bytes(encoded({'status': 'FAILED_PRESERVED', 'hardware_acceptance': False}))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='action', required=True)
    host = sub.add_parser('build'); host.add_argument('--source', type=Path, required=True); host.add_argument('--output', type=Path, required=True)
    host.add_argument('--frontend-dir', type=Path)
    runtime = sub.add_parser('verify'); runtime.add_argument('--root-prefix', default='/')
    args = parser.parse_args()
    result = build(args.source, args.output, frontend_dir=args.frontend_dir) if args.action == 'build' else verify(args.root_prefix)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
