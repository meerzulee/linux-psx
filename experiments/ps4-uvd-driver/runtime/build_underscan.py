#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a private PR3 derivative from the exact preserved runtime source package.

Native Linux only. Creates a new work directory, kernel, whole module set and
corresponding source; never installs, operates a console, or publishes anything.
The ordinary native-dev2 target is not a substitute for this UVD lineage.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tarfile
import time

BASE_RELEASE = '7.2.11-psx-ps4-baikal-uvd-runtime-dev1'
RELEASE = '7.2.11-psx-ps4-baikal-uvd-runtime-underscan-dev1'
SOURCE_SHA = '36fa0d0119281503b456e3532080afabe2ea7b5c7bc7b05af08685d295f7a3a4'
KERNEL_SHA = '9c3ec7d27fdfdebfee137684a635301e8dfa259b50e2f5d98ee33cf5f91e6153'
CONFIG_SHA = 'e97f456d3bd55f7c0b28948c1cc09a395ce9e17383512e844c6415f744d030a0'
PATCH_SHA = 'e6cb81ca268b02dcbaccec2f13a63e82015f838942d8dd30ffafe5fa0168930c'
CONNECTOR = 'drivers/gpu/drm/amd/amdgpu/amdgpu_connectors.c'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def changed_config(raw):
    old = 'CONFIG_LOCALVERSION="' + BASE_RELEASE.removeprefix('7.2.11') + '"'
    new = 'CONFIG_LOCALVERSION="' + RELEASE.removeprefix('7.2.11') + '"'
    text = raw.decode()
    require(text.count(old) == 1, 'Unexpected base LOCALVERSION')
    require('CONFIG_EXTRA_FIRMWARE=""' in text, 'Firmware embedding must remain disabled')
    return text.replace(old, new, 1).encode()


def member_name(name, prefix):
    parts = PurePosixPath(name).parts
    require(not name.startswith('/') and '..' not in parts, 'Unsafe archive member')
    require(parts and parts[0] == prefix, 'Unexpected source archive prefix')
    return '/'.join(parts[1:])


def verify_archive(archive, expected, prefix, log):
    """Read every archived source/module byte against the explicit inventory."""
    seen = set()
    process = subprocess.Popen(['zstd', '-qdc', str(archive)], stdout=subprocess.PIPE,
                               stderr=log)
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as stream:
            for member in stream:
                name = member_name(member.name, prefix)
                if member.isdir():
                    continue
                require(name not in seen and name in expected, 'Unknown/duplicate member: ' + name)
                seen.add(name)
                record = expected[name]
                require(member.mode & 0o777 == record['mode'], 'Archive mode differs: ' + name)
                if record['kind'] == 'symlink':
                    require(member.issym() and member.linkname == record['target'], 'Link differs: ' + name)
                else:
                    require(member.isfile() and member.size == record['bytes'], 'File differs: ' + name)
                    with stream.extractfile(member) as data:
                        require(hashlib.file_digest(data, 'sha256').hexdigest() == record['sha256'],
                                'Archive bytes differ: ' + name)
        require(process.wait() == 0 and seen == set(expected), 'Incomplete archive readback')
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
            process.wait()


def file_record(path):
    return {'kind': 'file', 'mode': path.stat().st_mode & 0o777,
            'bytes': path.stat().st_size, 'sha256': digest(path)}


def build(args):
    require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'Native Linux x86-64 required')
    base = args.base_package.resolve(strict=True)
    patch = args.patch.resolve(strict=True)
    archive = base / ('source-' + BASE_RELEASE + '.tar.zst')
    for path, sha in ((archive, SOURCE_SHA), (base / 'bzImage', KERNEL_SHA),
                      (base / 'config', CONFIG_SHA), (patch, PATCH_SHA)):
        require(path.is_file() and not path.is_symlink() and digest(path) == sha,
                'Pinned input differs: ' + str(path))
    require(1 <= args.jobs <= 32, 'Invalid job count')
    output = args.output.absolute()
    require(output.parent.resolve(strict=True) == output.parent, 'Canonical output parent required')
    require(not output.exists() and not output.is_symlink(), 'Existing output preserved')
    require(not output.is_relative_to(base) and not base.is_relative_to(output), 'Output overlaps base')
    require(shutil.disk_usage(output.parent).free >= 12 * 1024**3, 'Need at least 12 GiB free for clean build')
    output.mkdir(mode=0o700)
    source = output / ('linux-' + RELEASE)
    source.mkdir()
    commands = []
    env = dict(os.environ)
    env.update(ARCH='x86_64', KBUILD_BUILD_USER='linux-psx', KBUILD_BUILD_HOST='builder',
               KBUILD_BUILD_VERSION='1', KBUILD_BUILD_TIMESTAMP='2026-10-17 00:00:00 UTC',
               SOURCE_DATE_EPOCH='1792195200', LC_ALL='C', LANG='C')
    for key in ('MAKEFLAGS', 'MFLAGS', 'KBUILD_OUTPUT', 'KCONFIG_CONFIG', 'KCFLAGS', 'KCPPFLAGS',
                'CFLAGS_KERNEL', 'CFLAGS_MODULE', 'CROSS_COMPILE', 'LOCALVERSION'):
        env.pop(key, None)

    def run(argv, cwd=source, capture=False):
        number = len(commands)
        print('RUN', number, ' '.join(map(str, argv)), flush=True)
        started = time.monotonic()
        with (output / f'command-{number:02}.log').open('xb') as log:
            result = subprocess.run(list(map(str, argv)), cwd=cwd, env=env,
                                    stdout=subprocess.PIPE if capture else log, stderr=log)
        commands.append({'argv': list(map(str, argv)), 'returncode': result.returncode,
                         'elapsed_seconds': time.monotonic() - started})
        (output / 'COMMANDS.json').write_text(json.dumps(commands, indent=2) + '\n')
        require(result.returncode == 0, f'Command {number} failed; inspect its preserved log')
        return result.stdout.decode().strip() if capture else None

    try:
        inventory = json.loads((base / 'SOURCE-ARCHIVE.json').read_text())
        with (output / 'base-source-readback.log').open('xb') as log:
            verify_archive(archive, inventory, 'linux-' + BASE_RELEASE, log)
        run(['tar', '--zstd', '-xf', archive, '--strip-components=1', '--no-same-owner',
             '-C', source])
        require(digest(source / '.config') == CONFIG_SHA, 'Extracted config differs')
        run(['git', 'apply', '--check', patch])
        run(['git', 'apply', patch])
        (source / '.config').write_bytes(changed_config((source / '.config').read_bytes()))
        resolved_config = (source / '.config').read_bytes()
        metadata = source / 'PSX-UNDERSCAN'
        metadata.mkdir()
        shutil.copyfile(patch, metadata / 'pr3.patch')
        shutil.copyfile(Path(__file__), metadata / 'build_underscan.py')
        lineage = {'schema': 1, 'release': RELEASE, 'base_release': BASE_RELEASE,
                   'base_source_archive_sha256': SOURCE_SHA, 'base_kernel_sha256': KERNEL_SHA,
                   'base_config_sha256': CONFIG_SHA, 'patch_sha256': PATCH_SHA,
                   'patch_url': 'https://github.com/meerzulee/linux-psx/pull/3',
                   'code_delta': [CONNECTOR], 'config_delta': ['CONFIG_LOCALVERSION'],
                   'prior_PSX_metadata': 'Unchanged original runtime lineage, not this derivative identity',
                   'hardware_acceptance': False, 'publication': 'private only'}
        (metadata / 'LINEAGE.json').write_text(json.dumps(lineage, indent=2) + '\n')
        (metadata / 'REBUILD.md').write_text(
            '# Private runtime + PR3 source\n\n'
            'PR3 is already applied. Original PSX metadata describes the preserved base.\n'
            'Only amdgpu_connectors.c and CONFIG_LOCALVERSION change from that base;\n'
            'PSX-UNDERSCAN contains the derivative inputs. Device firmware is external.\n\n'
            'Native Linux x86-64 rebuild with GCC and the included .config:\n\n'
            '    make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc olddefconfig\n'
            '    make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc -j16 bzImage modules\n\n'
            'Install the whole matching module set, never a single replacement module.\n'
            'A successful build is not hardware acceptance. No firmware or initramfs is bundled.\n')
        for name in ('.config', CONNECTOR):
            inventory[name] = file_record(source / name)
        for path in metadata.iterdir():
            inventory[path.relative_to(source).as_posix()] = file_record(path)
        source_archive = output / ('source-' + RELEASE + '.tar.zst')
        run(['tar', '--sort=name', '--owner=0', '--group=0', '--numeric-owner',
             '--mtime=@1792195200', '-I', 'zstd -q -3 -T4', '-cf', source_archive,
             '-C', output, source.name], cwd=output)
        with (output / 'source-readback.log').open('xb') as log:
            verify_archive(source_archive, inventory, source.name, log)
        make = ['make', 'ARCH=x86_64', 'LOCALVERSION=', 'CC=/usr/bin/gcc', 'HOSTCC=/usr/bin/gcc']
        run(make + ['olddefconfig'])
        require((source / '.config').read_bytes() == resolved_config,
                'olddefconfig changed settings beyond the declared LOCALVERSION delta')
        require(run(make + ['-s', 'kernelrelease'], capture=True) == RELEASE, 'Unexpected kernel release')
        run(make + [f'-j{args.jobs}', 'bzImage', 'modules'])
        stage = output / 'stage'
        run(make + ['INSTALL_MOD_PATH=' + str(stage), 'INSTALL_MOD_STRIP=1', 'modules_install'])
        modules = stage / 'lib/modules' / RELEASE
        for name in ('build', 'source'):
            path = modules / name
            if path.is_symlink():
                require(path.resolve() == source, 'Unexpected generated module symlink')
                path.unlink()
        run(['depmod', '-b', stage, '-F', source / 'System.map', RELEASE])
        identity = json.loads((base / 'RUNTIME-IDENTITY.json').read_text())
        expected_modules = {name.replace(BASE_RELEASE, RELEASE) for name in identity['modules']}
        actual_modules = {p.relative_to(stage).as_posix() for p in modules.rglob('*.ko')}
        require(actual_modules == expected_modules and len(actual_modules) == 41, 'Whole module set differs')
        for name in sorted(actual_modules):
            require(run(['modinfo', '-F', 'vermagic', stage / name], capture=True).split()[0] == RELEASE,
                    'Module release mismatch: ' + name)
        for path in stage.rglob('*'):
            require(not path.is_symlink(), 'Unexpected module archive link')
        module_archive = output / ('modules-' + RELEASE + '.tar.zst')
        run(['tar', '--sort=name', '--owner=0', '--group=0', '--numeric-owner',
             '--mtime=@1792195200', '-I', 'zstd -q -3 -T4', '-cf', module_archive,
             '-C', stage, 'lib'])
        module_inventory = {p.relative_to(stage / 'lib').as_posix(): file_record(p)
                            for p in (stage / 'lib').rglob('*') if p.is_file()}
        with (output / 'module-readback.log').open('xb') as log:
            verify_archive(module_archive, module_inventory, 'lib', log)
        for name, original in (('bzImage', 'arch/x86/boot/bzImage'), ('config', '.config'),
                               ('vmlinux', 'vmlinux'), ('System.map', 'System.map')):
            shutil.copyfile(source / original, output / name)
        (output / 'version.txt').write_text(RELEASE + '\n')
        artifacts = {name: file_record(output / name) for name in
                     ('bzImage', 'config', 'vmlinux', 'System.map', 'version.txt',
                      source_archive.name, module_archive.name)}
        manifest = dict(lineage, status='PASS_BUILD_AND_ARCHIVE_READBACK', module_count=41,
                        artifacts=artifacts, module_inventory=module_inventory,
                        compiler=run(['/usr/bin/gcc', '--version'], capture=True))
        (output / 'ARTIFACTS.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(json.dumps({'status': manifest['status'], 'release': RELEASE,
                          'kernel_sha256': artifacts['bzImage']['sha256'],
                          'module_count': 41, 'output': str(output)}, indent=2), flush=True)
    except BaseException as error:
        (output / 'FAILED_PRESERVED.json').write_text(json.dumps({'error': str(error)}, indent=2) + '\n')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-package', type=Path, required=True)
    parser.add_argument('--patch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=16)
    build(parser.parse_args())


if __name__ == '__main__':
    main()
