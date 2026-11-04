#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compose the frozen complete native-dev3 baseline plus the frozen UVD overlay.

Copies a verified native build cache into a new output, preserving all inputs.
Only the declared 41 AMDGPU files and LOCALVERSION differ from native-dev3.
Inactive patches stay inactive; firmware, hardware and publication are excluded.
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
import tarfile
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('poweroff_common', HERE / 'build_poweroff.py')
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
NATIVE = '7.2.11-psx-ps4-baikal-native-dev3'
RELEASE = '7.2.11-psx-ps4-baikal-uvd-runtime-dev2'
NATIVE_SOURCE_SHA = '8686f018c2719ff98fa09d1e315820a928ef76cc2127d5789957d4092f110eec'
NATIVE_KERNEL_SHA = '82dadf0821d5e7a7d06c16bd855dc2205adeeefd31ed79f3326f8ee584024fb0'
NATIVE_CONFIG_SHA = 'ed529f99c6bbce733f0df8c6f6d57ac9136561013b86ecdb9e72d7ae75065526'
NATIVE_COMMIT = 'c8495a51e0bad4851015cd5d34ee61b2fde87e44'
PREFIX = 'drivers/gpu/drm/amd/amdgpu/'
OVERLAY = tuple(PREFIX + name for name in (
    'Makefile', 'amdgpu.h', 'amdgpu_cs.c', 'amdgpu_debugfs.c', 'amdgpu_device.c',
    'amdgpu_drv.c', 'amdgpu_fence.c', 'amdgpu_ib.c', 'amdgpu_ids.c', 'amdgpu_irq.c',
    'amdgpu_job.c', 'amdgpu_job.h', 'amdgpu_kms.c', 'amdgpu_ps4_live_prepare.h',
    'amdgpu_ps4_map_canary.c', 'amdgpu_ps4_map_canary.h', 'amdgpu_ps4_uvd_frame.h',
    'amdgpu_ps4_uvd_frame_fixture.h', 'amdgpu_ps4_uvd_ib_data_plan.h',
    'amdgpu_ps4_uvd_irq.c', 'amdgpu_ps4_uvd_irq.h', 'amdgpu_ps4_uvd_jobs.c',
    'amdgpu_ps4_uvd_jobs.h', 'amdgpu_ps4_uvd_memory.c', 'amdgpu_ps4_uvd_memory.h',
    'amdgpu_ps4_uvd_message_create.h', 'amdgpu_ps4_uvd_ready.c', 'amdgpu_ps4_uvd_ready.h',
    'amdgpu_ps4_uvd_ring_plan.h', 'amdgpu_ps4_uvd_runtime.c', 'amdgpu_ps4_uvd_runtime.h',
    'amdgpu_ring.c', 'amdgpu_uvd.c', 'cik.c', 'gmc_v7_0.c', 'ps4_uvd_decode_message.c',
    'ps4_uvd_decode_message.h', 'ps4_uvd_message.c', 'ps4_uvd_message.h',
    'ps4_uvd_message_job.c', 'ps4_uvd_message_job.h'))


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def inventory(archive, prefix):
    """Pin every source member and retain only the small declared UVD overlay."""
    records, overlay = {}, {}
    process = subprocess.Popen(['zstd', '-qdc', str(archive)], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as stream:
            for member in stream:
                name = C.member_name(member.name, prefix)
                if member.isdir():
                    continue
                C.require(name not in records, 'Duplicate archive member')
                if member.issym():
                    records[name] = {'kind': 'symlink', 'mode': member.mode & 0o777, 'target': member.linkname}
                else:
                    C.require(member.isfile(), 'Unexpected archive type')
                    with stream.extractfile(member) as data:
                        if name in OVERLAY:
                            raw = data.read()
                            import hashlib
                            digest = hashlib.sha256(raw).hexdigest()
                            overlay[name] = raw
                        else:
                            import hashlib
                            digest = hashlib.file_digest(data, 'sha256').hexdigest()
                    records[name] = {'kind': 'file', 'mode': member.mode & 0o777,
                                     'bytes': member.size, 'sha256': digest}
        C.require(process.wait() == 0, 'Archive decompression failed')
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate(); process.wait()
    return records, overlay


def verify_tree(source, records, *, metadata_optional=False):
    for name, expected in records.items():
        path = source / name
        if metadata_optional and name.startswith('PSX-') and not path.exists():
            continue
        if expected['kind'] == 'symlink':
            C.require(path.is_symlink() and os.readlink(path) == expected['target'], 'Source link differs: ' + name)
        else:
            C.require(path.is_file() and not path.is_symlink() and C.file_record(path) == expected,
                      'Source byte/mode identity differs: ' + name)


def changed_config(raw):
    old = 'CONFIG_LOCALVERSION="-psx-ps4-baikal-native-dev3"'
    new = 'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-runtime-dev2"'
    C.require(raw.decode().count(old) == 1 and b'CONFIG_EXTRA_FIRMWARE=""' in raw,
              'Unexpected native config or firmware embedding')
    return raw.replace(old.encode(), new.encode(), 1)


def build(args):
    C.require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'Native x86-64 required')
    C.require(args.jobs == 16, 'This owner-authorized build uses all 16 logical CPUs')
    native, cache, base = (p.resolve(strict=True) for p in (args.native_package, args.native_cache, args.base_package))
    native_archive = native / ('source-' + NATIVE + '.tar.zst')
    old_archive = base / ('source-' + C.BASE_RELEASE + '.tar.zst')
    pins = {native_archive: NATIVE_SOURCE_SHA, native / 'bzImage': NATIVE_KERNEL_SHA,
            native / 'config': NATIVE_CONFIG_SHA, cache / 'arch/x86/boot/bzImage': NATIVE_KERNEL_SHA,
            cache / '.config': NATIVE_CONFIG_SHA, old_archive: C.SOURCE_SHA,
            base / 'bzImage': C.KERNEL_SHA, base / 'config': C.CONFIG_SHA,
            **{base / name: sha for name, sha in C.INPUT_PINS.items()}}
    for path, expected in pins.items():
        C.require(path.is_file() and C.digest(path) == expected, 'Pinned input differs: ' + str(path))
    output = args.output.absolute()
    C.require(output.parent.resolve(strict=True) == output.parent and
              (output.is_dir() if args.resume_prepared else not output.exists()), 'New output or explicitly resumed prepared directory required')
    C.require(all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in (native, cache, base)),
              'Output overlaps an input')
    C.require(shutil.disk_usage(output.parent).free >= 16 * 1024**3, 'Need 16 GiB free for copied cache and packages')
    if not args.resume_prepared:
        output.mkdir(mode=0o700)
    source = output / ('linux-' + RELEASE)
    source.mkdir(exist_ok=args.resume_prepared)
    (output / 'tmp').mkdir(exist_ok=args.resume_prepared)
    commands = json.loads((output / 'COMMANDS.json').read_text()) if args.resume_prepared else []
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(output), 'TMPDIR': str(output / 'tmp'),
           'LC_ALL': 'C', 'LANG': 'C', 'ARCH': 'x86_64', 'KBUILD_BUILD_USER': 'linux-psx',
           'KBUILD_BUILD_HOST': 'builder', 'KBUILD_BUILD_VERSION': '1',
           'KBUILD_BUILD_TIMESTAMP': '2026-10-17 00:00:00 UTC', 'SOURCE_DATE_EPOCH': '1792195200'}
    def run(argv, cwd=source, capture=False):
        number = max((int(p.stem.removeprefix('command-')) for p in output.glob('command-*.log')), default=-1) + 1
        print('RUN', number, ' '.join(map(str, argv)), flush=True)
        started = time.monotonic()
        with (output / f'command-{number:02}.log').open('xb') as log:
            result = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE if capture else log, stderr=log)
        commands.append({'argv': list(map(str, argv)), 'returncode': result.returncode,
                         'elapsed_seconds': round(time.monotonic() - started, 3)})
        save(output / 'COMMANDS.json', commands)
        C.require(result.returncode == 0, f'Command {number} failed; inspect preserved output')
        return result.stdout.decode().strip() if capture else None
    try:
        if args.resume_prepared:
            print('Verify prepared source and resume with the original cached compiler spelling', flush=True)
            baseline, _ = inventory(native_archive, 'linux-' + NATIVE)
            expected = json.loads((output / 'SOURCE-ARCHIVE.json').read_text())
            verify_tree(source, expected)
            original_receipt = json.loads((base / 'build-inputs/runtime/SOURCE-RECEIPT.json').read_text())
            C.verify_lifecycle(source, original_receipt)
            config = (source / '.config').read_bytes()
            C.require(config == changed_config((native / 'config').read_bytes()), 'Prepared config differs')
            metadata = source / 'PSX-NATIVE3-UVD'
            receipts = output / 'build-inputs/runtime'
            lineage = json.loads((metadata / 'LINEAGE.json').read_text())
            C.require(lineage['source_base_commit'] == NATIVE_COMMIT and
                      lineage['code_delta_from_native3'] == list(OVERLAY), 'Prepared lineage differs')
            superseded = output / 'initial-invocation-inputs'
            superseded.mkdir()
            for name in ('build_native3.py', 'LINEAGE.json', 'SOURCE-RECEIPT.json'):
                shutil.copyfile(metadata / name, superseded / name)
            shutil.copyfile(output / 'SOURCE-ARCHIVE.json', superseded / 'SOURCE-ARCHIVE.json')
            C.require(Path(shutil.which('gcc', path=env['PATH'])).resolve() == Path('/usr/bin/gcc').resolve(),
                      'gcc must resolve to the same pinned toolchain binary')
            adaptation = {'reason': 'Match verified native cache CC/HOSTCC spelling; compiler and kernel source unchanged',
                          'compiler_path': '/usr/bin/gcc', 'compiler_sha256': C.digest('/usr/bin/gcc'),
                          'previous_make_compiler': '/usr/bin/gcc', 'current_make_compiler': 'gcc',
                          'preserved_interrupted_log': 'command-06.log',
                          'previous_helper_sha256': C.digest(superseded / 'build_native3.py'),
                          'current_helper_sha256': C.digest(Path(__file__))}
            save(metadata / 'BUILD-INVOCATION-ADAPTATION.json', adaptation)
            save(output / 'RESUME.json', dict(adaptation, status='RESUMED_PREPARED_SOURCE'))
            shutil.copyfile(Path(__file__), metadata / 'build_native3.py')
            lineage['build_invocation_adaptation'] = adaptation
            save(metadata / 'LINEAGE.json', lineage)
            receipt = json.loads((receipts / 'SOURCE-RECEIPT.json').read_text())
            receipt['build_invocation_adaptation'] = adaptation
            save(receipts / 'SOURCE-RECEIPT.json', receipt)
            shutil.copyfile(receipts / 'SOURCE-RECEIPT.json', metadata / 'SOURCE-RECEIPT.json')
            for path in metadata.iterdir():
                expected[path.relative_to(source).as_posix()] = C.file_record(path)
            save(output / 'SOURCE-ARCHIVE.json', expected)
        else:
            print('Verify complete native3 and frozen UVD source inputs', flush=True)
            baseline, _ = inventory(native_archive, 'linux-' + NATIVE)
            old_inventory, overlay = inventory(old_archive, 'linux-' + C.BASE_RELEASE)
            C.require(set(overlay) == set(OVERLAY), 'Incomplete exact UVD overlay')
            verify_tree(cache, baseline, metadata_optional=True)
            print('Verified native3 cache; copy objects without changing originals', flush=True)
            run(['rsync', '-a', '--exclude=/.git', str(cache) + '/', str(source) + '/'])
            # Restore source-package metadata absent from the original compiled tree.
            original_metadata = sorted(name for name in baseline if name.startswith('PSX-'))
            run(['tar', '--zstd', '-xf', native_archive, '--strip-components=1', '--no-same-owner', '-C', source,
                 *('linux-' + NATIVE + '/' + name for name in original_metadata)])
            verify_tree(source, baseline)
            changes = []
            for name in OVERLAY:
                path = source / name
                before = path.read_text() if path.exists() else ''
                after = overlay[name].decode()
                changes.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                               fromfile='a/' + name if before else '/dev/null', tofile='b/' + name))
                path.write_bytes(overlay[name]); path.chmod(old_inventory[name]['mode'])
            (source / '.config').write_bytes(changed_config((source / '.config').read_bytes()))
            config = (source / '.config').read_bytes()
            expected = dict(baseline)
            for name in (*OVERLAY, '.config'):
                expected[name] = C.file_record(source / name)
            verify_tree(source, expected)
            original_receipt = json.loads((base / 'build-inputs/runtime/SOURCE-RECEIPT.json').read_text())
            C.verify_lifecycle(source, original_receipt)
            metadata = source / 'PSX-NATIVE3-UVD'; metadata.mkdir()
            (metadata / 'native3-uvd-overlay.patch').write_text(''.join(changes))
            for name in ('build_native3.py', 'build_poweroff.py'):
                shutil.copyfile(HERE / name, metadata / name)
            lineage = {'schema': 1, 'release': RELEASE, 'native_base_release': NATIVE,
                       'source_base_commit': NATIVE_COMMIT, 'native_source_archive_sha256': NATIVE_SOURCE_SHA,
                       'native_kernel_sha256': NATIVE_KERNEL_SHA, 'native_config_sha256': NATIVE_CONFIG_SHA,
                       'uvd_base_commit': C.BASE_COMMIT, 'uvd_source_archive_sha256': C.SOURCE_SHA,
                       'uvd_kernel_sha256': C.KERNEL_SHA, 'icc_patch_sha256': C.PATCH_SHA,
                       'active_baseline': 'Complete frozen native-dev3 source with its included PSX build inputs; no inactive patch activation',
                       'code_delta_from_native3': list(OVERLAY), 'config_delta': ['CONFIG_LOCALVERSION'],
                       'uvd_lifecycle_sources_unchanged': True, 'hardware_acceptance': False,
                       'cache_policy': 'Verified source/kernel/config; copied objects, original tree untouched; Kbuild rechecks dependencies',
                       'compiler': run(['/usr/bin/gcc', '--version'], capture=True),
                       'power_scope': 'Retained UVD shutdown park/EBUSY unchanged; power-off with UVD disabled requires separate qualification'}
            save(metadata / 'LINEAGE.json', lineage)
            (metadata / 'REBUILD.md').write_text(
                '# Complete native-dev3 plus frozen UVD runtime\n\n'
                'Source is already composed. Original PSX metadata identifies native-dev3; this directory identifies the derivative.\n'
                'All current native platform, SATA, HDMI, network, audio and ICC fixes remain. UVD source and lifecycle guards are unchanged.\n'
                'Native clean rebuild: make ARCH=x86_64 LOCALVERSION= CC=gcc HOSTCC=gcc olddefconfig\n'
                'then make ARCH=x86_64 LOCALVERSION= CC=gcc HOSTCC=gcc -j16 bzImage modules.\n'
                'Firmware is external. Use every matching module. No hardware acceptance is claimed.\n')
            receipts = output / 'build-inputs/runtime'; receipts.mkdir(parents=True)
            receipt = dict(lineage, status='SOURCE_PREPARED', resolved_config_sha256=C.digest(source / '.config'),
                           overlay_sha256=C.digest(metadata / 'native3-uvd-overlay.patch'),
                           candidate_files={name: C.digest(source / name) for name in OVERLAY},
                           original_uvd_protected_files=original_receipt['candidate_files'])
            save(receipts / 'SOURCE-RECEIPT.json', receipt)
            shutil.copyfile(receipts / 'SOURCE-RECEIPT.json', metadata / 'SOURCE-RECEIPT.json')
            for path in metadata.iterdir(): expected[path.relative_to(source).as_posix()] = C.file_record(path)
            save(output / 'SOURCE-ARCHIVE.json', expected)
        make = ['make', 'ARCH=x86_64', 'LOCALVERSION=', 'CC=gcc', 'HOSTCC=gcc']
        run(make + ['olddefconfig'])
        # olddefconfig may leave the copied auto.conf/utsrelease cache intact.
        # Refresh generated configuration before reading the new release.
        run(make + ['syncconfig'])
        C.require((source / '.config').read_bytes() == config, 'olddefconfig changed undeclared settings')
        C.require(run(make + ['-s', 'kernelrelease'], capture=True) == RELEASE, 'Unexpected release')
        run(make + ['-j16', 'bzImage', 'modules'])
        verify_tree(source, expected)
        C.verify_lifecycle(source, original_receipt)
        save(receipts / 'LINK-ATTEMPT-01.json', {'schema': 1, 'status': 'PASS_LINK', 'returncode': 0,
             'release': RELEASE, 'source_receipt_sha256': C.digest(receipts / 'SOURCE-RECEIPT.json'),
             'kernel_sha256': C.digest(source / 'arch/x86/boot/bzImage'), 'hardware_acceptance': False})
        stage = output / 'stage'
        run(make + ['INSTALL_MOD_PATH=' + str(stage), 'INSTALL_MOD_STRIP=1', 'modules_install'])
        modules = stage / 'lib/modules' / RELEASE
        for name in ('build', 'source'):
            path = modules / name
            if path.is_symlink():
                C.require(path.resolve() == source, 'Unexpected generated module link'); path.unlink()
        run(['depmod', '-b', stage, '-F', source / 'System.map', RELEASE])
        old_identity = json.loads((base / 'RUNTIME-IDENTITY.json').read_text())
        names = {p.relative_to(stage).as_posix() for p in modules.rglob('*.ko')}
        C.require(names == {name.replace(C.BASE_RELEASE, RELEASE) for name in old_identity['modules']} and len(names) == 41,
                  'Complete 41-module set differs')
        module_records = {}
        for name in sorted(names):
            path = stage / name
            with path.open('rb') as stream: header = stream.read(20)
            C.require(header[:6] == b'\x7fELF\x02\x01' and header[16:20] == b'\x01\x00\x3e\x00', 'Wrong module ELF')
            magic = run(['modinfo', '-F', 'vermagic', path], capture=True)
            C.require(magic.split()[0] == RELEASE, 'Module release differs')
            module_records[name] = dict(C.file_record(path), vermagic=magic)
        save(output / 'MODULES.json', module_records)
        C.require(not any(p.is_symlink() for p in stage.rglob('*')), 'Module archive cannot contain links')
        module_archive = output / ('modules-' + RELEASE + '.tar.zst')
        run(['tar', '--sort=name', '--owner=0', '--group=0', '--numeric-owner', '--mtime=@1792195200',
             '-I', 'zstd -q -3 -T4', '-cf', module_archive, '-C', stage, 'lib'])
        module_inventory = {p.relative_to(stage / 'lib').as_posix(): C.file_record(p)
                            for p in (stage / 'lib').rglob('*') if p.is_file()}
        with (output / 'module-readback.log').open('xb') as log:
            C.verify_archive(module_archive, module_inventory, 'lib', log)
        source_archive = output / ('source-' + RELEASE + '.tar.zst')
        listing = output / 'source-members.nul'
        listing.write_bytes(b''.join((source.name + '/' + name).encode() + b'\0' for name in sorted(expected)))
        run(['tar', '--sort=name', '--owner=0', '--group=0', '--numeric-owner', '--mtime=@1792195200',
             '-I', 'zstd -q -3 -T4', '-cf', source_archive, '-C', output, '--no-recursion', '--null', '-T', listing])
        with (output / 'source-readback.log').open('xb') as log:
            C.verify_archive(source_archive, expected, source.name, log)
        for name, original in (('bzImage', 'arch/x86/boot/bzImage'), ('config', '.config'), ('vmlinux', 'vmlinux'), ('System.map', 'System.map')):
            shutil.copyfile(source / original, output / name)
        (output / 'version.txt').write_text(RELEASE + '\n')
        runtime = {'schema': 1, 'release': RELEASE, 'kernel_sha256': C.digest(output / 'bzImage'),
                   'config_sha256': C.digest(output / 'config'), 'module_count': 41, 'modules': module_records,
                   'module_archive': {'name': module_archive.name, 'sha256': C.digest(module_archive)},
                   'source_archive': {'name': source_archive.name, 'sha256': C.digest(source_archive)},
                   'source_receipt_sha256': C.digest(receipts / 'SOURCE-RECEIPT.json'),
                   'link_receipt_sha256': C.digest(receipts / 'LINK-ATTEMPT-01.json'),
                   'source_base_commit': NATIVE_COMMIT, 'uvd_base_commit': C.BASE_COMMIT,
                   'uvd_lifecycle_sources_unchanged': True, 'hardware_acceptance': False}
        for path, expected_sha in pins.items(): C.require(C.digest(path) == expected_sha, 'Original input changed')
        verify_tree(cache, baseline, metadata_optional=True)
        save(output / 'RUNTIME-IDENTITY.json', runtime)
        artifacts = {name: C.file_record(output / name) for name in
                     ('bzImage', 'config', 'vmlinux', 'System.map', 'version.txt', source_archive.name, module_archive.name)}
        save(output / 'PACKAGE.json', dict(lineage, status='PASS_PACKAGE', artifacts=artifacts, module_count=41,
                                         source_receipt=runtime['source_receipt_sha256'], link_receipt=runtime['link_receipt_sha256']))
        files = sorted(p for p in output.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
        (output / 'SHA256SUMS').write_text(''.join(C.digest(p) + '  ' + p.name + '\n' for p in files))
        print(json.dumps({'status': 'PASS_PACKAGE', 'release': RELEASE, 'kernel_sha256': runtime['kernel_sha256'],
                          'module_count': 41, 'output': str(output)}), flush=True)
    except BaseException as error:
        save(output / 'FAILED_PRESERVED.json', {'error': str(error), 'status': 'FAILED_PRESERVED'})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('native-package', 'native-cache', 'base-package', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=16)
    parser.add_argument('--resume-prepared', action='store_true', help='Resume the preserved verified source after the explicit compiler-invocation correction')
    build(parser.parse_args())
