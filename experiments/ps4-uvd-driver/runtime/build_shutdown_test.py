#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a retained-DMA power-off experiment from the frozen complete dev2.

Native x86-64 only. Reflink the verified cache into a separate output; never
mutate the original. All source bytes are checked against the corresponding
source archive. Only three AMDGPU files, LOCALVERSION and new provenance change.
No firmware, console operation, installation or publication is performed.
"""
import argparse
import difflib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load('poweroff_common', HERE / 'build_poweroff.py')
N = load('native3_common', HERE / 'build_native3.py')
BASE = '7.2.11-psx-ps4-baikal-uvd-runtime-dev2'
RELEASE = '7.2.11-psx-ps4-baikal-uvd-runtime-dev3'
PREFIX = 'drivers/gpu/drm/amd/amdgpu/'
DELTA = tuple(PREFIX + n for n in ('amdgpu_drv.c', 'amdgpu_ps4_uvd_runtime.c', 'amdgpu_ps4_uvd_runtime.h'))
PINS = {
    'bzImage': '6b1d9d90d29eeffb58057cd04a387ad08c53adc7d13afb62bcb2b3af8c686fdc',
    'config': '51c008bb764dc1cbcc7fb81bf8c164ee23447feb5844c596c53e040bb49df4df',
    'source-' + BASE + '.tar.zst': '18b84b276d85eee58d62e8825fa3d9e22a95ecabb181b63c50b4698e19befefa',
    'modules-' + BASE + '.tar.zst': 'a900cf6e72dafbcf5b520c5b83471d6f78ad75eba644178541ad441fce62aef0',
    'PACKAGE.json': '36e744522a4685df806754cd33d4973a517d23684debc35192e3809c8af9fb35',
    'RUNTIME-IDENTITY.json': 'b6122ea0d662271f2e25f23209070d564f4c2279506a56770eadb335f8d350d8',
    'SOURCE-ARCHIVE.json': '12111b803500d640b0e2904830c1a985bf777feb9d0ea952d8a378ec545b3b0f',
    'MODULES.json': 'cd558e20dfacabd3605da8e6169fcf063a6cbc6647769545dc19b2efd9370b2b',
    'build-inputs/runtime/SOURCE-RECEIPT.json': 'fc6c3a1f1b0cb674ca4346512298086dd28d270340016393a6917760910939a6',
    'build-inputs/runtime/LINK-ATTEMPT-01.json': 'd2a567d3d64d674fc5b24c0ef4da892edafc91d82130b7d4bf5f0eec169ac3c7',
}
COMPILER_SHA = '8aac907d6fbf40394b424b5fa4a2ad1aa291dac7d3a0315c5b415c1f5ace1287'


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def changed_config(raw):
    old = 'CONFIG_LOCALVERSION="' + BASE.removeprefix('7.2.11') + '"'
    new = 'CONFIG_LOCALVERSION="' + RELEASE.removeprefix('7.2.11') + '"'
    C.require(raw.count(old.encode()) == 1 and b'CONFIG_EXTRA_FIRMWARE=""' in raw,
              'Unexpected release or embedded firmware')
    return raw.replace(old.encode(), new.encode(), 1)


def changed_driver(raw, guard):
    old = ('\tif (amdgpu_ps4_uvd_runtime_retain(adev))\n'
           '\t\tamdgpu_ps4_uvd_runtime_park(adev, "amdgpu_pci_shutdown");\n')
    C.require(raw.count(old) == 1, 'Expected exact existing PCI shutdown guard')
    return raw.replace(old, guard, 1)


def build(args):
    C.require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'Native x86-64 required')
    C.require(args.jobs == 16, 'This build uses all 16 logical CPUs')
    base = args.base_package.resolve(strict=True)
    cache = args.base_cache.resolve(strict=True)
    for name, expected in PINS.items():
        C.require(C.digest(base / name) == expected, 'Frozen input differs: ' + name)
    C.require(C.digest(cache / '.config') == PINS['config'] and
              C.digest(cache / 'arch/x86/boot/bzImage') == PINS['bzImage'], 'Cached build identity differs')
    C.require(C.digest('/usr/bin/gcc') == COMPILER_SHA, 'Pinned compiler differs')
    expected = json.loads((base / 'SOURCE-ARCHIVE.json').read_text())
    archive = base / ('source-' + BASE + '.tar.zst')
    output = args.output.absolute()
    C.require(output.parent.resolve(strict=True) == output.parent and not output.exists(), 'New canonical output required')
    C.require(all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in (base, cache)),
              'Output overlaps preserved input')
    # A mandatory reflink clone shares old extents, unlike the older full-copy
    # builder. Reserve space for newly compiled objects, link outputs and source.
    C.require(shutil.disk_usage(output.parent).free >= 4 * 1024**3, 'Need 4 GiB free for incremental build and packages')
    output.mkdir(mode=0o700)
    source = output / ('linux-' + RELEASE)
    (output / 'tmp').mkdir()
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(output), 'TMPDIR': str(output / 'tmp'),
           'LC_ALL': 'C', 'LANG': 'C', 'ARCH': 'x86_64', 'KBUILD_BUILD_USER': 'linux-psx',
           'KBUILD_BUILD_HOST': 'builder', 'KBUILD_BUILD_VERSION': '1',
           'KBUILD_BUILD_TIMESTAMP': '2026-10-17 00:00:00 UTC', 'SOURCE_DATE_EPOCH': '1792195200'}
    commands = []

    def run(argv, cwd=output, capture=False):
        number = len(commands)
        print('RUN', number, ' '.join(map(str, argv)), flush=True)
        start = time.monotonic()
        with (output / f'command-{number:02}.log').open('xb') as log:
            result = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE if capture else log, stderr=log)
        commands.append({'argv': list(map(str, argv)), 'returncode': result.returncode,
                         'elapsed_seconds': round(time.monotonic() - start, 3)})
        save(output / 'COMMANDS.json', commands)
        C.require(result.returncode == 0, f'Command {number} failed; preserved log is authoritative')
        return result.stdout.decode().strip() if capture else None

    try:
        print('Verify complete corresponding source and frozen cache', flush=True)
        with (output / 'base-source-readback.log').open('xb') as log:
            C.verify_archive(archive, expected, 'linux-' + BASE, log)
        N.verify_tree(cache, expected)
        run(['cp', '-a', '--reflink=always', '--', cache, source])
        N.verify_tree(source, expected)
        C.require((source / '.config').stat().st_ino != (cache / '.config').stat().st_ino,
                  'Clone must not share file inodes')
        candidate = load('shutdown_candidate', HERE / 'build_candidate.py')
        guard = candidate.pci_shutdown_retention_guard().lstrip('\n')
        changes = []
        for name in DELTA:
            path = source / name
            before = path.read_text()
            after = changed_driver(before, guard) if name.endswith('/amdgpu_drv.c') else (HERE / path.name).read_text()
            C.require(before != after, 'Declared change is missing: ' + name)
            changes.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                               fromfile='a/' + name, tofile='b/' + name))
            path.write_text(after)
            expected[name] = C.file_record(path)
        config = changed_config((source / '.config').read_bytes())
        (source / '.config').write_bytes(config)
        expected['.config'] = C.file_record(source / '.config')
        metadata = source / 'PSX-UVD-POWEROFF-TEST'
        metadata.mkdir()
        (metadata / 'retained-poweroff.patch').write_text(''.join(changes))
        tool_names = ('build_shutdown_test.py', 'build_candidate.py', 'build_native3.py', 'build_poweroff.py')
        tool_pins = {name: C.digest(HERE / name) for name in tool_names}
        for name in tool_names:
            shutil.copyfile(HERE / name, metadata / name)
        policy = {'parameter': 'amdgpu.ps4_uvd_poff', 'default': 0, 'test_value': 1, 'read_only': True,
                  'system_power_off_only': True, 'retain_dma': True,
                  'reboot_suspend_remove_reset_guards_unchanged': True, 'hardware_acceptance': False}
        lineage = {'schema': 1, 'release': RELEASE, 'base_release': BASE,
                   'base_artifact_sha256': PINS, 'code_delta': list(DELTA), 'config_delta': ['CONFIG_LOCALVERSION'],
                   'cache_policy': 'Mandatory independent reflink; verified source bytes; original tree preserved',
                   'uvd_poweroff_experiment': policy, 'uvd_lifecycle_sources_unchanged': False,
                   'icc_patch_sha256': C.PATCH_SHA, 'source_base_commit': N.NATIVE_COMMIT,
                   'integration_tool_sha256': tool_pins, 'hardware_acceptance': False, 'publication': 'private experiment'}
        save(metadata / 'LINEAGE.json', lineage)
        (metadata / 'REBUILD.md').write_text(
            '# Experimental retained-DMA UVD power-off\n\n'
            'Source is already composed from frozen dev2. Firmware remains external.\n'
            'Boot with amdgpu.ps4_uvd_poff=1 only for the controlled power-off experiment.\n'
            'This returns from AMDGPU PCI shutdown without freeing UVD backing. All other\n'
            'lifecycle guards remain. Physical power removal and healthy restart need testing.\n\n'
            'Native rebuild with the included .config:\n\n'
            '    make ARCH=x86_64 LOCALVERSION= CC=gcc HOSTCC=gcc olddefconfig\n'
            '    make ARCH=x86_64 LOCALVERSION= CC=gcc HOSTCC=gcc -j16 bzImage modules\n\n'
            'Use the complete matching module set. No hardware qualification is claimed.\n')
        receipts = output / 'build-inputs/runtime'
        receipts.mkdir(parents=True)
        receipt = dict(lineage, status='SOURCE_PREPARED', resolved_config_sha256=C.digest(source / '.config'),
                       candidate_files={name: C.digest(source / name) for name in DELTA},
                       patch_sha256=C.digest(metadata / 'retained-poweroff.patch'))
        save(receipts / 'SOURCE-RECEIPT.json', receipt)
        shutil.copyfile(receipts / 'SOURCE-RECEIPT.json', metadata / 'SOURCE-RECEIPT.json')
        for p in metadata.iterdir():
            expected[p.relative_to(source).as_posix()] = C.file_record(p)
        save(output / 'SOURCE-ARCHIVE.json', expected)
        make = ['make', 'ARCH=x86_64', 'LOCALVERSION=', 'CC=gcc', 'HOSTCC=gcc']
        for target in ('olddefconfig', 'syncconfig'):
            run(make + [target], cwd=source)
        C.require((source / '.config').read_bytes() == config, 'Undeclared config delta')
        C.require(run(make + ['-s', 'kernelrelease'], cwd=source, capture=True) == RELEASE, 'Wrong release')
        run(make + ['-j16', 'bzImage', 'modules'], cwd=source)
        N.verify_tree(source, expected)
        link = {'schema': 1, 'status': 'PASS_LINK', 'returncode': 0, 'release': RELEASE,
                'source_receipt_sha256': C.digest(receipts / 'SOURCE-RECEIPT.json'),
                'kernel_sha256': C.digest(source / 'arch/x86/boot/bzImage'), 'hardware_acceptance': False}
        save(receipts / 'LINK-ATTEMPT-01.json', link)
        stage = output / 'stage'
        run(make + ['INSTALL_MOD_PATH=' + str(stage), 'INSTALL_MOD_STRIP=1', 'modules_install'], cwd=source)
        modules = stage / 'lib/modules' / RELEASE
        for name in ('build', 'source'):
            path = modules / name
            if path.is_symlink():
                C.require(path.resolve() == source, 'Unexpected module symlink')
                path.unlink()
        run(['depmod', '-b', stage, '-F', source / 'System.map', RELEASE])
        old = json.loads((base / 'RUNTIME-IDENTITY.json').read_text())
        names = {p.relative_to(stage).as_posix() for p in modules.rglob('*.ko')}
        C.require(names == {n.replace(BASE, RELEASE) for n in old['modules']} and len(names) == 41,
                  'Complete matching module set differs')
        records = {}
        for name in sorted(names):
            path = stage / name
            with path.open('rb') as stream:
                header = stream.read(20)
            C.require(header[:6] == b'\x7fELF\x02\x01' and header[16:20] == b'\x01\x00\x3e\x00', 'Wrong module ELF')
            magic = run(['modinfo', '-F', 'vermagic', path], capture=True)
            C.require(magic.split()[0] == RELEASE, 'Module release differs')
            records[name] = dict(C.file_record(path), vermagic=magic)
        save(output / 'MODULES.json', records)
        C.require(not any(p.is_symlink() for p in stage.rglob('*')), 'Unexpected staged symlink')
        module_archive = output / ('modules-' + RELEASE + '.tar.zst')
        run(['tar', '--sort=name', '--owner=0', '--group=0', '--numeric-owner', '--mtime=@1792195200',
             '-I', 'zstd -q -3 -T4', '-cf', module_archive, '-C', stage, 'lib'])
        inventory = {p.relative_to(stage / 'lib').as_posix(): C.file_record(p)
                     for p in (stage / 'lib').rglob('*') if p.is_file()}
        with (output / 'module-readback.log').open('xb') as log:
            C.verify_archive(module_archive, inventory, 'lib', log)
        source_archive = output / ('source-' + RELEASE + '.tar.zst')
        listing = output / 'source-members.nul'
        listing.write_bytes(b''.join((source.name + '/' + name).encode() + b'\0' for name in sorted(expected)))
        run(['tar', '--sort=name', '--owner=0', '--group=0', '--numeric-owner', '--mtime=@1792195200',
             '-I', 'zstd -q -3 -T4', '-cf', source_archive, '-C', output, '--no-recursion', '--null', '-T', listing])
        with (output / 'source-readback.log').open('xb') as log:
            C.verify_archive(source_archive, expected, source.name, log)
        for name, original in (('bzImage', 'arch/x86/boot/bzImage'), ('config', '.config'),
                               ('vmlinux', 'vmlinux'), ('System.map', 'System.map')):
            shutil.copyfile(source / original, output / name)
        (output / 'version.txt').write_text(RELEASE + '\n')
        runtime = dict(schema=1, release=RELEASE, kernel_sha256=C.digest(output / 'bzImage'),
                       config_sha256=C.digest(output / 'config'), module_count=41, modules=records,
                       module_archive={'name': module_archive.name, 'sha256': C.digest(module_archive)},
                       source_archive={'name': source_archive.name, 'sha256': C.digest(source_archive)},
                       source_receipt_sha256=C.digest(receipts / 'SOURCE-RECEIPT.json'),
                       link_receipt_sha256=C.digest(receipts / 'LINK-ATTEMPT-01.json'),
                       source_base_commit=N.NATIVE_COMMIT, uvd_lifecycle_sources_unchanged=False,
                       uvd_poweroff_experiment=policy, hardware_acceptance=False)
        save(output / 'RUNTIME-IDENTITY.json', runtime)
        artifacts = {n: C.file_record(output / n) for n in ('bzImage', 'config', 'vmlinux', 'System.map',
                     'version.txt', source_archive.name, module_archive.name)}
        save(output / 'PACKAGE.json', dict(lineage, status='PASS_PACKAGE', artifacts=artifacts,
             module_count=41, source_receipt=runtime['source_receipt_sha256'], link_receipt=runtime['link_receipt_sha256']))
        for name, expected_sha in PINS.items():
            C.require(C.digest(base / name) == expected_sha, 'Preserved package input changed')
        N.verify_tree(cache, json.loads((base / 'SOURCE-ARCHIVE.json').read_text()))
        for name, expected_sha in tool_pins.items():
            C.require(C.digest(HERE / name) == expected_sha, 'Versioned build input changed during build')
        files = sorted(p for p in output.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
        (output / 'SHA256SUMS').write_text(''.join(C.digest(p) + '  ' + p.name + '\n' for p in files))
        print(json.dumps({'status': 'PASS_PACKAGE', 'release': RELEASE, 'kernel_sha256': runtime['kernel_sha256'],
                          'module_count': 41, 'output': str(output)}), flush=True)
    except BaseException as error:
        save(output / 'FAILED_PRESERVED.json', {'status': 'FAILED_PRESERVED', 'error': str(error)})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-package', 'base-cache', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=16)
    build(parser.parse_args())
