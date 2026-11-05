#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Pinned native DRM/Wayland EGL test; importing this module does nothing."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import stat
import subprocess
import sys
import time

GRAPHICS = ('browser', 'chromium', 'browser-manual', 'chromium-manual',
            'protondb', 'opengl', 'vulkan', 'diagnostics')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_tree(suite):
    suite = Path(suite).resolve(strict=True)
    receipt = suite / 'suite.json'
    if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > 1024**2:
        raise ValueError('Missing bounded Wayland suite receipt')
    manifest = json.loads(receipt.read_text())
    if (manifest.get('schema_version') != 1 or manifest.get('backend') != 'drm'
            or manifest.get('renderer') != 'gles2' or manifest.get('hardware_acceptance') is not False):
        raise ValueError('Unsupported Wayland suite policy')
    total = 0
    for name, expected in manifest['files'].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or str(relative) != name:
            raise ValueError('Unsafe Wayland suite path')
        path = suite / name
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(suite)
                or path.stat().st_size != expected['bytes'] or digest(path) != expected['sha256']
                or stat.S_IMODE(path.stat().st_mode) != int(expected['mode'], 8)):
            raise ValueError('Wayland suite input differs: ' + name)
        total += expected['bytes']
    if total > manifest['maximum_unpacked_bytes']:
        raise ValueError('Wayland suite exceeds its RAM budget')
    actual = {str(path.relative_to(suite)) for path in suite.rglob('*') if path.is_file() or path.is_symlink()}
    if actual != set(manifest['files']) | {'suite.json'}:
        raise ValueError('Unexpected file in the fixed Wayland suite')
    return manifest


class Operations:
    def root(self): return os.geteuid() == 0
    def release(self): return os.uname().release
    def node(self, path): return os.stat(path)
    def command(self, argv):
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=10, check=False)


def require_inactive(operations, name):
    result = operations.command(['/usr/bin/systemctl', 'show', '--property=LoadState,ActiveState', name])
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    state, loaded = fields.get('ActiveState'), fields.get('LoadState')
    code = getattr(result, 'returncode', 0)
    if not (state == 'inactive' and loaded in ('loaded', 'not-found') and
            (code == 0 or code == 1 and loaded == 'not-found')):
        raise ValueError('Close the current Xorg/Wayland session or inspect its failed state before the native Wayland test')


def preflight(suite, root_prefix='/', operations=None, allow_own_service=False, verified_manifest=None):
    root = Path(root_prefix).resolve()
    if root != Path('/') and operations is None:
        raise ValueError('Fixture roots require injected operations')
    operations = operations or Operations()
    # The bootstrap may pass the same manifest after validating every archive
    # member in memory, so a default plan needs no filesystem staging.
    manifest = verify_tree(suite) if verified_manifest is None else verified_manifest
    loaded = json.loads((root / 'etc/psx-diagnostics/boot-set.json').read_text())
    if (not operations.root() or operations.release() != manifest['kernel_release']
            or loaded.get('kernel_release') != manifest['kernel_release']
            or loaded.get('mesa_manifest_sha256') != manifest['mesa_manifest_sha256']):
        raise ValueError('Wayland test requires root and the matching PS4 kernel/Mesa build')
    for field in ('kernel_sha256', 'rootfs_sha256'):
        if field in manifest and (not re.fullmatch(r'[a-f0-9]{64}', str(manifest[field]))
                                  or loaded.get(field) != manifest[field]):
            raise ValueError('Wayland test artifact identity differs: ' + field)
    boot_id = (root / 'proc/sys/kernel/random/boot_id').read_text().strip()
    if not re.fullmatch(r'[a-f0-9-]{36}', boot_id):
        raise ValueError('Missing current kernel boot identity')
    for mode in GRAPHICS:
        for name in ('psx-diagnostics-graphics@' + mode + '.service',
                     'psx-diagnostics-window-' + mode + '.service'):
            require_inactive(operations, name)
    if not allow_own_service:
        require_inactive(operations, 'psx-diagnostics-wayland.service')
    cards = []
    for candidate in sorted((root / 'sys/class/drm').glob('card[0-9]*')):
        if not re.fullmatch(r'card[0-9]+', candidate.name):
            continue
        try:
            vendor = (candidate / 'device/vendor').read_text().strip().lower()
        except OSError:
            continue
        if vendor == '0x1002':
            node = '/dev/dri/' + candidate.name
            info = operations.node(root / node.lstrip('/'))
            if stat.S_ISCHR(info.st_mode) and os.major(info.st_rdev) == 226:
                cards.append(node)
    if len(cards) != 1:
        raise ValueError('Expected one explicit AMD DRM card; no automatic multi-GPU selection')
    for relative in ('etc/pam.d/login', 'usr/lib/security/pam_systemd.so'):
        if not (root / relative).is_file():
            raise ValueError('Native Wayland requires the existing PAM/logind session support')
    # The generic Mesa verifier is data-only and never creates a context.
    verified = operations.command(['/usr/bin/python3', '/opt/psx-mesa-tools/with-mesa.py',
        '--root', '/opt/psx-mesa', '--expected-manifest-sha256', manifest['mesa_manifest_sha256'], '--verify-only'])
    if getattr(verified, 'returncode', 0) != 0:
        raise ValueError('Pinned Mesa verification failed before Wayland startup')
    return {'profile': manifest['profile'], 'kernel_release': manifest['kernel_release'], 'boot_id': boot_id,
            'drm_device': cards[0], 'backend': 'drm', 'renderer': 'gles2', 'seat_backend': 'logind',
            'native_wayland': True, 'nested': False, 'hardware_acceptance': False}


def compositor_environment(base, suite, runtime, card):
    # Keep PAM's fresh logind session, but never inherit a nested display or a
    # renderer override from the caller. with-mesa verifies/selects Mesa later.
    environment = {key: value for key, value in base.items()
                   if key not in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'DRI_PRIME')
                   and not key.startswith(('WLR_', 'LIBSEAT_', 'SEATD_', 'LD_', 'MESA_', 'LIBGL_', 'EGL_', 'VK_'))}
    environment.update(PATH='/usr/bin:/usr/sbin', HOME='/root', XDG_RUNTIME_DIR=str(runtime),
        XDG_SESSION_TYPE='wayland', WLR_BACKENDS='drm,libinput', WLR_RENDERER='gles2',
        WLR_DRM_DEVICES=card, WLR_XWAYLAND='/usr/bin/false', LIBSEAT_BACKEND='logind',
        WLR_LIBINPUT_NO_DEVICES='1', PSX_WAYLAND_SUITE=str(suite))
    return environment


def client_passed(returncode, client):
    if not isinstance(client, dict): return False
    renderer = client.get('renderer')
    return (returncode == 0 and client.get('status') == 'PASS' and client.get('native_wayland') is True
            and client.get('known_pixels') is True and client.get('frames') == 3
            and isinstance(renderer, str) and bool(renderer.strip())
            and not any(name in renderer.lower() for name in ('llvmpipe', 'softpipe', 'software', 'swrast', 'lavapipe')))


def run_session(suite, run_id):
    suite = Path(suite).resolve(strict=True)
    facts = preflight(suite, allow_own_service=True)
    if not os.environ.get('XDG_SESSION_ID'):
        raise ValueError('Missing PAM/logind session; refusing DRM access')
    if not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('Invalid private Wayland run identifier')
    directory = Path('/run/psx-wayland/runs') / run_id
    if directory.is_symlink() or not directory.is_dir() or directory.stat().st_uid != 0:
        raise ValueError('Missing private Wayland result directory')
    runtime = Path('/run/psx-wayland-session')
    if (runtime.is_symlink() or not runtime.is_dir() or runtime.stat().st_uid != 0
            or stat.S_IMODE(runtime.stat().st_mode) != 0o700):
        raise ValueError('Missing private systemd session runtime')
    mesa_runtime = Path('/run/psx-mesa/0')
    mesa_runtime.parent.mkdir(mode=0o755, exist_ok=True)
    mesa_runtime.mkdir(mode=0o700, exist_ok=True)
    if (mesa_runtime.is_symlink() or mesa_runtime.stat().st_uid != 0
            or stat.S_IMODE(mesa_runtime.stat().st_mode) != 0o700):
        raise ValueError('Unsafe root Mesa runtime directory')
    manifest = verify_tree(suite)
    command = ['/usr/bin/python3', '/opt/psx-mesa-tools/with-mesa.py', '--root', '/opt/psx-mesa',
               '--runtime-dir', str(mesa_runtime), '--expected-manifest-sha256', manifest['mesa_manifest_sha256'],
               '--', str(suite / 'compositor-exec')]
    environment = compositor_environment(os.environ, suite, runtime, facts['drm_device'])
    started = time.monotonic()
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024**2, 1024**2))
    with (directory/'client.jsonl').open('xb') as output, (directory/'compositor.log').open('xb') as error:
        try:
            result = subprocess.run(command, env=environment, stdout=output, stderr=error, timeout=25, check=False)
            returncode = result.returncode
        except subprocess.TimeoutExpired:
            returncode = 124
    client = None
    for line in (directory/'client.jsonl').read_text(errors='replace').splitlines():
        try: value = json.loads(line)
        except ValueError: continue
        if isinstance(value, dict) and value.get('native_wayland') is True: client = value
    passed = client_passed(returncode, client)
    report = dict(facts, status='PASS' if passed else 'INCONCLUSIVE', phase='completed', run_id=run_id,
                  stop_required=not passed, returncode=returncode, client=client,
                  duration_seconds=round(time.monotonic()-started, 3))
    for name in ('client.jsonl', 'compositor.log'):
        with (directory/name).open('rb') as saved: os.fsync(saved.fileno())
    with (directory/'result.json').open('x') as saved:
        saved.write(json.dumps(report, indent=2)+'\n'); saved.flush(); os.fsync(saved.fileno())
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)
    print(json.dumps(report), flush=True)
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--suite', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--run-id')
    args = parser.parse_args()
    if args.run:
        raise SystemExit(run_session(args.suite, args.run_id or ''))
    print(json.dumps(preflight(args.suite), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit('Native Wayland test: ' + str(error))
