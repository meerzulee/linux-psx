#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build frozen runtime-dev4 from verified dev3 plus the scoped SATA patch.

Adapted from build_shutdown_test.py under its MIT notice. Native x86-64,
mandatory independent Btrfs reflink, full source verification and 41 matching
modules. Only three ATA paths, LOCALVERSION and derivative metadata change.
No hardware operation, installation, firmware bundling or publication.
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


COMMON = HERE if (HERE / 'build_poweroff.py').is_file() else HERE.parent / 'ps4-uvd-driver/runtime'
C = load('poweroff_common', COMMON / 'build_poweroff.py')
N = load('native3_common', COMMON / 'build_native3.py')
BASE = '7.2.11-psx-ps4-baikal-uvd-runtime-dev3'
RELEASE = '7.2.11-psx-ps4-baikal-uvd-runtime-dev4'
DELTA = ('drivers/ata/ahci.h', 'drivers/ata/libahci.c', 'drivers/ata/ahci-ps4-probe.h')
PATCH = '0001-ahci-baikal-scoped-completion.patch'
PATCH_SHA = 'a33c832630a285b4f1c7ccd4fe37b5a8899b93e6c435041b371c9184f89f4023'
PINS = {
    'SHA256SUMS': 'eb1fc057d096efbe700e4a3c598a9f421db7cde6bccaba442ae4dbebbaa9b1bc',
    'bzImage': '01fa05f683c4c36ae83678f09cf3df502ddb0731dc73f2530cdb05fbe317804d',
    'config': 'e4ae5561870aa34c9eafad0dcb9de3eaf77c4a8248237134d7dfcd0e76aa93fe',
    'source-' + BASE + '.tar.zst': '16e98003a8dad18649b62e4f42d904859215ca69d0b0e19dc48fee25d08afbb1',
    'modules-' + BASE + '.tar.zst': '603a4b9453fba5462c702102afab335a857d01d707cf945f7a8809ec5640396c',
    'PACKAGE.json': 'c3e4aa3a95f1c30cd7441f42ec3066fc175ac9d6f81f33a05513e18f9882da33',
    'RUNTIME-IDENTITY.json': 'd86cff225318eac61dd9a465233d36ec6ce33b390bc158680f6384efe58bed61',
    'SOURCE-ARCHIVE.json': '3d3e87ad3d607e275d833ce767848e66bc8777b90f7683e16c048d110a8a9ae6',
    'MODULES.json': '81d5d688d0550c588c8c588cdbea3b45aa88cb4dc519c7a4ca180a10436f0d97',
    'build-inputs/runtime/SOURCE-RECEIPT.json': '8dfc8903aa1c0197c1cabbbb3248dec1d2e543dd6503ca542f64d3a7e13f7c69',
    'build-inputs/runtime/LINK-ATTEMPT-01.json': '66d9f0fb17159bd5d8135b608fa9698956c63d5bbc575644ab947c2169353e59',
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
    C.require(C.digest(HERE / PATCH) == PATCH_SHA, 'Frozen SATA patch differs')
    for line in (base / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        C.require('/' not in name and name not in ('.', '..'), 'Unexpected base checksum path')
        C.require(C.digest(base / name) == digest, 'Base checksum differs: ' + name)
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
        run(['git', 'apply', '--check', HERE / PATCH], cwd=source)
        run(['git', 'apply', HERE / PATCH], cwd=source)
        for name in DELTA:
            path = source / name
            C.require(path.is_file() and not path.is_symlink(), 'Missing declared ATA source: ' + name)
            C.require(name not in expected or C.file_record(path) != expected[name],
                      'Declared source change missing: ' + name)
            expected[name] = C.file_record(path)
        # This full inventory check also protects all UVD/ICC/PHY/xHCI bytes.
        N.verify_tree(source, expected)
        config = changed_config((source / '.config').read_bytes())
        (source / '.config').write_bytes(config)
        expected['.config'] = C.file_record(source / '.config')
        metadata = source / 'PSX-SATA-NATIVE'
        metadata.mkdir()
        shutil.copyfile(HERE / PATCH, metadata / PATCH)
        inputs = {name: HERE / name for name in ('build_kernel.py', 'make_patch.py', 'test_host.py',
                  'fixture.c', 'ahci-ps4-probe.h', 'BASELINE.json', 'VALIDATION.json', 'README.md', PATCH)}
        inputs.update({name: COMMON / name for name in ('build_native3.py', 'build_poweroff.py',
                                                       'build_shutdown_test.py')})
        tool_pins = {name: C.digest(path) for name, path in inputs.items()}
        for name, path in inputs.items():
            if name != PATCH:
                shutil.copyfile(path, metadata / name)
        old_package = json.loads((base / 'PACKAGE.json').read_text())
        policy = old_package['uvd_poweroff_experiment']
        sata_policy = {'parameter': 'libahci.ps4_probe_ms', 'long_alias': 'libahci.ps4_ahci_completion_probe_ms',
                       'default': 0, 'test_value': 5, 'read_only': True, 'legacy_poll_required': 0,
                       'pci_bdf': '0000:00:14.7', 'pci_id': '104d:90de', 'hardware_port': 0,
                       'data_write_filter': False, 'hardware_acceptance': False}
        lineage = {'schema': 1, 'release': RELEASE, 'base_release': BASE,
                   'base_artifact_sha256': PINS, 'code_delta': list(DELTA), 'config_delta': ['CONFIG_LOCALVERSION'],
                   'cache_policy': 'Mandatory independent reflink; verified source bytes; original tree preserved',
                   'uvd_poweroff_experiment': policy, 'uvd_lifecycle_sources_unchanged': False, 'uvd_sources_unchanged_from_dev3': True,
                   'icc_patch_sha256': C.PATCH_SHA, 'source_base_commit': N.NATIVE_COMMIT,
                   'integration_tool_sha256': tool_pins, 'sata_probe_experiment': sata_policy,
                   'sata_patch_sha256': PATCH_SHA, 'hardware_acceptance': False, 'publication': 'private experiment'}
        save(metadata / 'LINEAGE.json', lineage)
        (metadata / 'REBUILD.md').write_text(
            '# Experimental runtime-dev4 SATA source\n\n'
            'The scoped SATA patch is already applied to exact frozen runtime-dev3.\n'
            'Only three ATA paths and CONFIG_LOCALVERSION change. UVD/ICC/PHY/xHCI\n'
            'and the retained-DMA power-off policy are inherited unchanged.\n'
            'Original PSX metadata describes the preserved bases, not this derivative.\n'
            'Firmware remains external. The scoped probe defaults off; the test profile\n'
            'uses libahci.ps4_probe_ms=5 and libahci.ps4_ahci_poll_ms=0.\n\n'
            'Native rebuild with the included .config:\n\n'
            '    make ARCH=x86_64 LOCALVERSION= CC=gcc HOSTCC=gcc olddefconfig\n'
            '    make ARCH=x86_64 LOCALVERSION= CC=gcc HOSTCC=gcc -j16 bzImage modules\n\n'
            'Use every matching module. No hardware qualification or write permission is claimed.\n')
        receipts = output / 'build-inputs/runtime'
        receipts.mkdir(parents=True)
        receipt = dict(lineage, status='SOURCE_PREPARED', resolved_config_sha256=C.digest(source / '.config'),
                       candidate_files={name: C.digest(source / name) for name in DELTA},
                       patch_sha256=C.digest(metadata / PATCH))
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
                       source_base_commit=N.NATIVE_COMMIT, uvd_lifecycle_sources_unchanged=False, uvd_sources_unchanged_from_dev3=True,
                       uvd_poweroff_experiment=policy, sata_probe_experiment=sata_policy,
                       sata_patch_sha256=PATCH_SHA, hardware_acceptance=False)
        save(output / 'RUNTIME-IDENTITY.json', runtime)
        artifacts = {n: C.file_record(output / n) for n in ('bzImage', 'config', 'vmlinux', 'System.map',
                     'version.txt', source_archive.name, module_archive.name)}
        save(output / 'PACKAGE.json', dict(lineage, status='PASS_PACKAGE', artifacts=artifacts,
             module_count=41, source_receipt=runtime['source_receipt_sha256'], link_receipt=runtime['link_receipt_sha256']))
        for name, expected_sha in PINS.items():
            C.require(C.digest(base / name) == expected_sha, 'Preserved package input changed')
        N.verify_tree(cache, json.loads((base / 'SOURCE-ARCHIVE.json').read_text()))
        for name, expected_sha in tool_pins.items():
            C.require(C.digest(inputs[name]) == expected_sha, 'Versioned build input changed during build')
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
