#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a private ICC power-off derivative from the exact preserved runtime source package.

Native Linux only. Creates a new work directory, kernel, whole module set and
corresponding source; never installs, operates a console, or publishes anything.
Adapted from build_underscan.py; its MIT notice and original input pins remain.
The ICC patch does not bypass UVD park/EBUSY or qualify any power transition.
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
RELEASE = '7.2.11-psx-ps4-baikal-uvd-runtime-dev2'
SOURCE_SHA = '36fa0d0119281503b456e3532080afabe2ea7b5c7bc7b05af08685d295f7a3a4'
KERNEL_SHA = '9c3ec7d27fdfdebfee137684a635301e8dfa259b50e2f5d98ee33cf5f91e6153'
CONFIG_SHA = 'e97f456d3bd55f7c0b28948c1cc09a395ce9e17383512e844c6415f744d030a0'
PATCH_SHA = 'cb01d71ad7e12144ee7f014fe5385e37f7e239b2f2f3ee065b5ec7e0a0de219a'
ICC = 'drivers/ps4/ps4-bpcie-icc.c'
BASE_COMMIT = '35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2'
INPUT_PINS = {
    'RUNTIME-IDENTITY.json': '4d5c576d93e0f84c9bb5a58ca8dd3918472f8744241e889ed1b83a88c4a5068b',
    'build-inputs/runtime/SOURCE-RECEIPT.json': '6477b996fc28f7d270ba72c10208dc9e1e9fa792504d93ce79ab1c5ae8f0ff1b',
}


def verify_lifecycle(source, receipt):
    """All original UVD candidate bytes, including lifecycle hooks, stay exact."""
    prefix = source / 'drivers/gpu/drm/amd/amdgpu'
    for name, expected in receipt['candidate_files'].items():
        require(digest(prefix / name) == expected, 'Protected UVD source changed: ' + name)



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
    for name, expected in INPUT_PINS.items():
        require(digest(base / name) == expected, 'Pinned original receipt differs: ' + name)
    require(1 <= args.jobs <= 8, 'Invalid job count')
    output = args.output.absolute()
    require(output.parent.resolve(strict=True) == output.parent, 'Canonical output parent required')
    require(not output.exists() and not output.is_symlink(), 'Existing output preserved')
    require(not output.is_relative_to(base) and not base.is_relative_to(output), 'Output overlaps base')
    require(shutil.disk_usage(output.parent).free >= 12 * 1024**3, 'Need at least 12 GiB free for clean build')
    output.mkdir(mode=0o700)
    source = output / ('linux-' + RELEASE)
    source.mkdir()
    commands = []
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(output / 'home'), 'TMPDIR': str(output / 'tmp')}
    (output / 'home').mkdir()
    (output / 'tmp').mkdir()
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
        original_receipt = json.loads((base / 'build-inputs/runtime/SOURCE-RECEIPT.json').read_text())
        verify_lifecycle(source, original_receipt)
        metadata = source / 'PSX-ICC-POWEROFF'
        metadata.mkdir()
        shutil.copyfile(patch, metadata / 'icc-poweroff.patch')
        shutil.copyfile(Path(__file__), metadata / 'build_poweroff.py')
        lineage = {'schema': 1, 'release': RELEASE, 'base_release': BASE_RELEASE,
                   'base_source_archive_sha256': SOURCE_SHA, 'base_kernel_sha256': KERNEL_SHA,
                   'base_config_sha256': CONFIG_SHA, 'patch_sha256': PATCH_SHA,
                   'patch_url': 'https://github.com/meerzulee/linux-psx/pull/4',
                   'original_contribution_commit': '16c43fcd090c95669374c27cbb2df835326d259a',
                   'source_base_commit': BASE_COMMIT,
                   'integration_tool_sha256': digest(Path(__file__)),
                   'uvd_lifecycle_sources_unchanged': True,
                   'power_scope': 'ICC handoff only; retained UVD shutdown park/EBUSY unchanged; UVD-disabled power-off needs separate hardware qualification',
                   'code_delta': [ICC], 'config_delta': ['CONFIG_LOCALVERSION'],
                   'prior_PSX_metadata': 'Unchanged original runtime lineage, not this derivative identity',
                   'hardware_acceptance': False, 'publication': 'private only'}
        (metadata / 'LINEAGE.json').write_text(json.dumps(lineage, indent=2) + '\n')
        (metadata / 'REBUILD.md').write_text(
            '# Private UVD runtime + guarded ICC power-off source\n\n'
            'The guarded ICC patch is already applied. Original PSX metadata describes the preserved base.\n'
            'Only drivers/ps4/ps4-bpcie-icc.c and CONFIG_LOCALVERSION change from that base;\n'
            'PSX-ICC-POWEROFF contains the derivative inputs. Device firmware is external.\n'
            'All UVD lifecycle guards remain unchanged. This does not enable shutdown after UVD entry.\n\n'
            'Native Linux x86-64 rebuild with GCC and the included .config:\n\n'
            '    make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc olddefconfig\n'
            '    make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc -j4 bzImage modules\n\n'
            'Install the whole matching module set, never a single replacement module.\n'
            'A successful build is not hardware acceptance. No firmware or initramfs is bundled.\n')
        for name in ('.config', ICC):
            inventory[name] = file_record(source / name)
        for path in metadata.iterdir():
            inventory[path.relative_to(source).as_posix()] = file_record(path)
        receipts = output / 'build-inputs/runtime'
        receipts.mkdir(parents=True)
        source_receipt = dict(lineage, schema=1, status='SOURCE_PREPARED',
                              candidate_files={ICC: digest(source / ICC)},
                              protected_uvd_files=original_receipt['candidate_files'],
                              resolved_config_sha256=digest(source / '.config'),
                              compiler=subprocess.check_output(['/usr/bin/gcc', '--version'], text=True).strip())
        (receipts / 'SOURCE-RECEIPT.json').write_text(json.dumps(source_receipt, indent=2) + '\n')
        shutil.copyfile(receipts / 'SOURCE-RECEIPT.json', metadata / 'SOURCE-RECEIPT.json')
        inventory['PSX-ICC-POWEROFF/SOURCE-RECEIPT.json'] = file_record(metadata / 'SOURCE-RECEIPT.json')
        (output / 'SOURCE-ARCHIVE.json').write_text(json.dumps(inventory, sort_keys=True, indent=2) + '\n')
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
        verify_lifecycle(source, original_receipt)
        require((source / '.config').read_bytes() == resolved_config, 'Build changed the resolved config')
        link = {'schema': 1, 'status': 'PASS_LINK', 'returncode': 0, 'release': RELEASE,
                'source_receipt_sha256': digest(receipts / 'SOURCE-RECEIPT.json'),
                'kernel_sha256': digest(source / 'arch/x86/boot/bzImage'),
                'config_sha256': digest(source / '.config'), 'hardware_acceptance': False}
        (receipts / 'LINK-ATTEMPT-01.json').write_text(json.dumps(link, indent=2) + '\n')
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
            with (stage / name).open('rb') as stream:
                header = stream.read(20)
            require(header[:6] == b'\x7fELF\x02\x01' and header[16:20] == b'\x01\x00\x3e\x00',
                    'Module is not ELF64 little-endian x86-64 relocatable: ' + name)
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
        module_records = {}
        for name in sorted(actual_modules):
            row = file_record(stage / name)
            row['vermagic'] = subprocess.check_output(['modinfo', '-F', 'vermagic', str(stage / name)], text=True).strip()
            module_records[name] = row
        (output / 'MODULES.json').write_text(json.dumps(module_records, indent=2, sort_keys=True) + '\n')
        runtime = {'schema': 1, 'release': RELEASE, 'kernel_sha256': artifacts['bzImage']['sha256'],
                   'config_sha256': artifacts['config']['sha256'], 'module_count': 41,
                   'modules': module_records, 'module_archive': {'name': module_archive.name, 'sha256': digest(module_archive)},
                   'source_archive': {'name': source_archive.name, 'sha256': digest(source_archive)},
                   'source_receipt_sha256': digest(receipts / 'SOURCE-RECEIPT.json'),
                   'link_receipt_sha256': digest(receipts / 'LINK-ATTEMPT-01.json'),
                   'source_base_commit': BASE_COMMIT, 'integration_patch_sha256': PATCH_SHA,
                   'uvd_lifecycle_sources_unchanged': True, 'hardware_acceptance': False, 'publication': 'private only'}
        (output / 'RUNTIME-IDENTITY.json').write_text(json.dumps(runtime, indent=2, sort_keys=True) + '\n')
        manifest = dict(lineage, status='PASS_BUILD_AND_ARCHIVE_READBACK', module_count=41,
                        artifacts=artifacts, module_inventory=module_inventory,
                        compiler=run(['/usr/bin/gcc', '--version'], capture=True))
        (output / 'ARTIFACTS.json').write_text(json.dumps(manifest, indent=2) + '\n')
        manifest.update(schema=1, status='PASS_PACKAGE', source_receipt=runtime['source_receipt_sha256'],
                        link_receipt=runtime['link_receipt_sha256'], uvd_lifecycle_sources_unchanged=True)
        (output / 'PACKAGE.json').write_text(json.dumps(manifest, indent=2) + '\n')
        for path, expected in ((archive, SOURCE_SHA), (base / 'bzImage', KERNEL_SHA), (base / 'config', CONFIG_SHA), (patch, PATCH_SHA)):
            require(digest(path) == expected, 'Original input changed during build: ' + str(path))
        sums = sorted(p for p in output.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
        (output / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + p.name + '\n' for p in sums))
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
    parser.add_argument('--jobs', type=int, default=4)
    build(parser.parse_args())


if __name__ == '__main__':
    main()
