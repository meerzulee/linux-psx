#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Explicit verified RAM-appliance backend; never impersonate USB media."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

PROFILE = 'ps4-baikal-diagnostix-ram-alpha1'
RELEASE = '7.2.11-psx-ps4-baikal-uvd-runtime-dev6'
EXPECTED = '/etc/psx-diagnostics/expected-ram-boot.json'
MANIFEST = '/etc/psx-diagnostics/boot-set.json'
PROOF = '/run/psx-ram/verified-boot.json'
STATE = '/run/psx-state'
BACKING = '/run/psx-image/rootfs.ext4'
PINS = {
    'kernel_sha256': 'a7264426ee3b2ffeb030a04bbd7ab070418ecf4c0272b83400b4d5f45473bff4',
    'config_sha256': 'ee77a55ff46f9d201d71efb0b87adca4cb1f1bf253c301c9cd8da3b5c4a724ff',
    'modules_sha256': '0e50c537c2395154cff659d9722d165027d0ecea875dfcb14d32d798f9c9e14d',
}


def local(root, name):
    root = Path(root).resolve()
    path = root / name.lstrip('/')
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError('RAM diagnostic paths must not contain symlinks')
    return path


def read(root, name, *, private=False, fixture=False):
    path = local(root, name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024
                or info.st_mode & (0o077 if private else 0o022)
                or info.st_uid != (os.geteuid() if fixture else 0)):
            raise ValueError('RAM boot metadata must be protected bounded regular files')
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('RAM boot metadata exceeds its bound')
    return raw


def active(root='/'):
    """Detection only; every operation still requires validate()."""
    try:
        value = json.loads(read(root, MANIFEST, fixture=Path(root).resolve() != Path('/')))
        return isinstance(value, dict) and value.get('profile') == PROFILE
    except FileNotFoundError:
        return False


def mount_rows(root, fixture=False):
    name = f'/proc/{os.getpid()}/mountinfo'
    if fixture and not local(root, name).exists():
        name = '/proc/self/mountinfo'
    rows = {}
    for line in read(root, name, fixture=fixture).decode().splitlines():
        left, right = line.split(' - ', 1)
        a, b = left.split(), right.split()
        # systemd automounts may legitimately stack an autofs mount and its
        # activated filesystem at the same unrelated path. Only these three
        # mount identities are part of this proof; ambiguity there still fails.
        if a[4] in rows and a[4] in ('/', STATE, '/run/psx-image'):
            raise ValueError('Ambiguous RAM mount identity')
        rows[a[4]] = {'device': a[2], 'options': a[5].split(','), 'fs': b[0], 'source': b[1]}
    return rows


def validate(root='/', manifest=None, *, fixture=False):
    root = Path(root).resolve()
    expected_raw = read(root, EXPECTED, fixture=fixture)
    expected = json.loads(expected_raw)
    value = json.loads(read(root, MANIFEST, fixture=fixture)) if manifest is None else manifest
    proof = json.loads(read(root, PROOF, private=True, fixture=fixture))
    def require(ok, message):
        if not ok:
            raise ValueError(message)
    require(isinstance(expected, dict) and isinstance(value, dict) and isinstance(proof, dict),
            'RAM boot contract is not an object')
    require(expected.get('profile') == PROFILE and expected.get('kernel_release') == RELEASE
            and expected.get('hardware_acceptance') is False
            and all(expected.get(k) == v for k, v in PINS.items())
            and expected.get('rootfs_image_bytes') == 1000 * 1024 * 1024
            and re.fullmatch(r'[0-9a-f]{64}', str(expected.get('rootfs_sha256', ''))),
            'Unmatched RAM kernel/root contract')
    require(set(value) == set(expected) | {'manifest_validation'}
            and all(type(value.get(k)) is type(v) and value[k] == v for k, v in expected.items()),
            'RAM runtime manifest differs from its embedded contract')
    state, source = expected.get('state', {}), expected.get('boot_source', {})
    require(state.get('kind') == 'volatile-tmpfs' and state.get('path') == STATE
            and state.get('results_dir') == STATE + '/results'
            and state.get('state_dir') == STATE + '/state' and state.get('persistent') is False
            and state.get('survives_controller_restart') is True
            and source.get('kind') == 'embedded-ram' and source.get('backing_path') == BACKING,
            'RAM state/asset backend differs')
    boot_id = read(root, '/proc/sys/kernel/random/boot_id', fixture=fixture).decode().strip()
    require(str(uuid.UUID(boot_id)) == boot_id
            and read(root, '/proc/sys/kernel/osrelease', fixture=fixture).decode().strip() == RELEASE,
            'RAM running boot identity differs')
    digest = hashlib.sha256(expected_raw).hexdigest()
    validation = value.get('manifest_validation', {})
    require(validation.get('embedded_root_contract_verified') is True
            and validation.get('ram_boot_contract_verified') is True
            and validation.get('expected_contract_sha256') == digest
            and validation.get('observed_kernel_release') == RELEASE
            and 'observed_media_initramfs_sha256' not in validation,
            'RAM verification result is missing or claims a USB measurement')
    require(proof.get('schema_version') == 1 and proof.get('kind') == 'psx-diagnostix-ram-boot-v1'
            and proof.get('boot_id') == boot_id and proof.get('manifest_sha256') == digest
            and proof.get('hardware_acceptance') is False
            and all(proof.get(k) == expected.get(k) for k in
                    ('profile', 'kernel_release', 'kernel_sha256', 'config_sha256', 'modules_sha256',
                     'rootfs_sha256', 'rootfs_image_bytes', 'frontend_overrides', 'boot_source', 'state')),
            'RAM verification proof differs from this boot')
    rows = mount_rows(root, fixture)
    require('/' in rows and rows['/']['fs'] == 'ext4' and 'ro' in rows['/']['options'],
            'RAM appliance root is not read-only ext4')
    for name in (STATE, '/run/psx-image'):
        row = rows.get(name, {})
        info = local(root, name).stat()
        require(row.get('fs') == 'tmpfs' and 'rw' in row.get('options', [])
                and stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o077
                and info.st_uid == (os.geteuid() if fixture else 0)
                and row.get('device') == f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}',
                'RAM state/backing mount identity differs')
    backing = local(root, BACKING).stat()
    require(stat.S_ISREG(backing.st_mode) and backing.st_size == expected['rootfs_image_bytes']
            and stat.S_IMODE(backing.st_mode) == 0o400
            and backing.st_uid == (os.geteuid() if fixture else 0), 'RAM backing file differs')
    require(proof.get('module_count') == 41, 'RAM matching module proof is incomplete')
    return proof
