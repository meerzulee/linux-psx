#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build exact dev5 from frozen dev4 plus reviewed ICC/retained-restart patches.

Adapted from the MIT build_poweroff.py, build_native3.py and
build_shutdown_test.py helpers. Native x86-64 only; a new /var/tmp output,
complete source readback and all 41 matching modules are mandatory. Never
installs, bundles firmware, operates a console or publishes artifacts.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load('restart_poweroff_common', HERE / 'build_poweroff.py')
N = load('restart_native3_common', HERE / 'build_native3.py')
BASE = '7.2.11-psx-ps4-baikal-uvd-runtime-dev4'
RELEASE = '7.2.11-psx-ps4-baikal-uvd-runtime-dev5'
ICC_DELTA = ('drivers/ps4/ps4-bpcie-icc.c', 'drivers/ps4/ps4-apcie-icc.c')
UVD_DELTA = tuple('drivers/gpu/drm/amd/amdgpu/' + name for name in
                 ('amdgpu_drv.c', 'amdgpu_ps4_uvd_runtime.c', 'amdgpu_ps4_uvd_runtime.h'))
DELTA = ICC_DELTA + UVD_DELTA
ICC_PATCH = ROOT / 'platforms/ps4/patches/7.2-ps4-baikal/0200-ps4-drivers/0004-ps4-bpcie-icc-atomic-restart.patch'
UVD_PATCH = HERE / 'retained-restart.patch'
# Filled only from the independent patch owners' final reviewed bytes.
ICC_PATCH_SHA = '205fa678d28315ac813e870aeb19add85d643d6f79cb0180f42dd5530802f40f'
UVD_PATCH_SHA = '0e344be112396dbb49255908466de9eb751180589efe0c6c9e9d0b0ed959af79'
PINS = {
    'bzImage': '9b3842def11f0638601f60ab7659eb8fa9c4c7e5cb81bab64d25f301ad826d37',
    'config': '50676ba45e8535e1f7a3fd9f8eeab73fa7f841e255c1ccf846fda652d499dba0',
    'source-' + BASE + '.tar.zst': '43bb57dbe9dee2360f6ecfaa83e8c2e23c3dd17f265cd8a30791aae65df2e4e1',
    'modules-' + BASE + '.tar.zst': 'fcda424b74177ec2316e58187d1c1c86e6e521782094cad77a6cdcb8fdc32dcc',
    'PACKAGE.json': 'c0e2de068e9d6bf2ab86775ec3860e4d86ea28682fe86ec2d2ddb72e2a5ea8e8',
    'RUNTIME-IDENTITY.json': '5f0cc1653f47d91fbe87bd6d2387df07bde76fbad4fb5927ad13ede9df0fa46c',
    'SOURCE-ARCHIVE.json': '741713e05c488957999d4ab6a3f1ee48c9c5d9bfa7d7a2fdaca0cf61f5bd01f1',
    'MODULES.json': '36a581d5d8c241adbd0f874bc37ad89004d54e76c9f8d95fbee5fa3b4eaf5e2b',
    'SHA256SUMS': 'cc987931e9d16bfa08f61caf5d0a9d304e191b95fc59a0cddc8c14ff5ea84716',
    'build-inputs/runtime/SOURCE-RECEIPT.json': '951344c3c34de8049446c3adf46b0b66c6f8ae097b1e4e39bd0694c3f62d0135',
    'build-inputs/runtime/LINK-ATTEMPT-01.json': '7ef3d88ed2710f41b25db8122a290421dbef8c92b4494074c6c3bda56a9587d5',
}
COMPILER_SHA = '8aac907d6fbf40394b424b5fa4a2ad1aa291dac7d3a0315c5b415c1f5ace1287'
MINIMUM_FREE_BYTES = 32_000_000_000


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def changed_config(raw):
    old = ('CONFIG_LOCALVERSION="' + BASE.removeprefix('7.2.11') + '"').encode()
    new = ('CONFIG_LOCALVERSION="' + RELEASE.removeprefix('7.2.11') + '"').encode()
    C.require(raw.count(old) == 1 and b'CONFIG_EXTRA_FIRMWARE=""\n' in raw
              and b'CONFIG_INITRAMFS_SOURCE=""\n' in raw, 'Unexpected release or embedded firmware/initramfs')
    return raw.replace(old, new, 1)


def checked_file(path, expected):
    path = Path(path).absolute()
    C.require(re.fullmatch(r'[0-9a-f]{64}', str(expected)) is not None,
              'Reviewed patch/input hash is not frozen')
    C.require(path.resolve(strict=True) == path and path.is_file() and not path.is_symlink()
              and C.digest(path) == expected, 'Frozen input differs: ' + str(path))
    return path


def patch_paths(raw, expected):
    """No additions, deletions, mode edits, renames or undeclared source paths."""
    text = raw.decode('utf-8')
    paths = []
    for line in text.splitlines():
        if line.startswith('diff --git '):
            match = re.fullmatch(r'diff --git a/([^ ]+) b/([^ ]+)', line)
            C.require(match is not None and match[1] == match[2], 'Unexpected patch path/rename')
            paths.append(match[1])
        C.require(not line.startswith(('new file mode ', 'deleted file mode ', 'old mode ',
                                      'new mode ', 'rename from ', 'rename to ', 'GIT binary patch')),
                  'Only existing source content may change')
    C.require(len(paths) == len(set(paths)) and set(paths) == set(expected), 'Patch source scope differs')
    for marker in ('---', '+++'):
        headers = [line.removeprefix(marker + ' a/').removeprefix(marker + ' b/')
                   for line in text.splitlines() if line.startswith(marker + ' ')]
        C.require(len(headers) == len(expected) and set(headers) == set(expected), 'Patch file header scope differs')
    return paths


def validate_inputs(args):
    C.require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'Native Linux x86-64 required')
    C.require(type(args.jobs) is int and 1 <= args.jobs <= min(16, os.cpu_count() or 1), 'Invalid native job count')
    base = args.base_package.absolute()
    C.require(base.resolve(strict=True) == base and base.is_dir(), 'Canonical preserved base package required')
    for name, expected in PINS.items():
        checked_file(base / name, expected)
    C.require(C.digest('/usr/bin/gcc') == COMPILER_SHA, 'Pinned compiler differs')
    icc = checked_file(args.icc_patch, ICC_PATCH_SHA)
    uvd = checked_file(args.uvd_patch, UVD_PATCH_SHA)
    patch_paths(icc.read_bytes(), ICC_DELTA)
    patch_paths(uvd.read_bytes(), UVD_DELTA)
    changed_config((base / 'config').read_bytes())
    output = args.output.absolute()
    C.require(output.parent.resolve(strict=True) == output.parent and not output.exists()
              and not output.is_symlink() and output.is_relative_to(Path('/var/tmp')),
              'New canonical /var/tmp output required')
    C.require(not output.is_relative_to(base) and not base.is_relative_to(output), 'Output overlaps preserved input')
    C.require(shutil.disk_usage(output.parent).free >= MINIMUM_FREE_BYTES,
              'Need at least 32 GB free for clean native source, build and packages')
    return base, output, icc, uvd


def update_code_inventory(source, original):
    expected = dict(original)
    for name in DELTA:
        row = C.file_record(source / name)
        C.require(original[name]['kind'] == 'file' and row['mode'] == original[name]['mode']
                  and row['sha256'] != original[name]['sha256'], 'Missing/invalid declared code change: ' + name)
        expected[name] = row
    # This protects every ATA, PHY, xHCI and unrelated AMDGPU/ICC source byte.
    N.verify_tree(source, expected)
    return expected


def build(args):
    base, output, icc, uvd = validate_inputs(args)
    if args.plan:
        print(json.dumps({'status': 'PLAN_ONLY', 'base_release': BASE, 'release': RELEASE,
            'base_artifact_sha256': PINS, 'code_delta': DELTA, 'config_delta': ['CONFIG_LOCALVERSION'],
            'patch_sha256': {'icc': ICC_PATCH_SHA, 'uvd': UVD_PATCH_SHA},
            'jobs': args.jobs, 'output': str(output), 'minimum_free_bytes': MINIMUM_FREE_BYTES,
            'hardware_acceptance': False}, indent=2))
        return
    original = json.loads((base / 'SOURCE-ARCHIVE.json').read_text())
    archive = base / ('source-' + BASE + '.tar.zst')
    output.mkdir(mode=0o700)
    source = output / ('linux-' + RELEASE)
    source.mkdir(); (output / 'tmp').mkdir()
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
        C.require(result.returncode == 0, f'Command {number} failed; inspect its preserved log')
        return result.stdout.decode().strip() if capture else None

    try:
        with (output / 'base-source-readback.log').open('xb') as log:
            C.verify_archive(archive, original, 'linux-' + BASE, log)
        run(['tar', '--zstd', '-xf', archive, '--strip-components=1', '--no-same-owner', '-C', source])
        N.verify_tree(source, original)
        for patch in (icc, uvd):
            run(['git', 'apply', '--check', patch], cwd=source)
            run(['git', 'apply', patch], cwd=source)
        expected = update_code_inventory(source, original)
        config = changed_config((source / '.config').read_bytes())
        (source / '.config').write_bytes(config)
        expected['.config'] = C.file_record(source / '.config')
        metadata = source / 'PSX-UVD-POWER-RESTART'
        metadata.mkdir()
        inputs = {'build_power_restart.py': Path(__file__), 'build_poweroff.py': HERE / 'build_poweroff.py',
                  'build_native3.py': HERE / 'build_native3.py',
                  'icc-atomic-restart.patch': icc, 'retained-restart.patch': uvd}
        tool_pins = {name: C.digest(path) for name, path in inputs.items()}
        for name, path in inputs.items():
            shutil.copyfile(path, metadata / name)
        parent = json.loads((base / 'PACKAGE.json').read_text())
        restart_policy = {'parameter': 'amdgpu.ps4_uvd_poff', 'default': 0, 'test_value': 1,
            'read_only': True, 'system_restart_only': True, 'started_runtime_required': True,
            'initialized_open_jobs_required': True, 'healthy_runtime_required': True,
            'first_close_required': True, 'retain_dma': True, 'forced_restart_refused': True,
            'kexec_refused': True, 'poweroff_semantics_unchanged': True,
            'suspend_remove_reset_guards_unchanged': True, 'hardware_acceptance': False}
        restart_fix = {'patch_path': ICC_PATCH.relative_to(ROOT).as_posix(), 'patch_sha256': ICC_PATCH_SHA,
            'atomic_registered_restart': True, 'legacy_baikal_fallback_disabled': True, 'hardware_acceptance': False}
        poweroff_policy = dict(parent['uvd_poweroff_experiment'], reboot_suspend_remove_reset_guards_unchanged=False)
        lineage = {'schema': 1, 'release': RELEASE, 'base_release': BASE, 'base_artifact_sha256': PINS,
            'code_delta': list(DELTA), 'config_delta': ['CONFIG_LOCALVERSION'],
            'source_base_commit': N.NATIVE_COMMIT, 'integration_tool_sha256': tool_pins,
            'icc_patch_sha256': parent['icc_patch_sha256'], 'icc_restart_patch_sha256': ICC_PATCH_SHA,
            'uvd_restart_patch_sha256': UVD_PATCH_SHA, 'uvd_lifecycle_sources_unchanged': False,
            'uvd_sources_unchanged_from_dev3': False, 'uvd_sources_unchanged_from_dev4': False,
            'sata_sources_unchanged_from_dev4': True,
            'uvd_poweroff_semantics_unchanged_from_dev4': True, 'uvd_restart_guards_unchanged_from_dev4': False,
            'inherited_uvd_poweroff_experiment': parent['uvd_poweroff_experiment'],
            'uvd_poweroff_experiment': poweroff_policy, 'uvd_restart_experiment': restart_policy,
            'restart_fix': restart_fix,
            'sata_probe_experiment': parent['sata_probe_experiment'], 'sata_patch_sha256': parent['sata_patch_sha256'],
            'hardware_acceptance': False, 'publication': 'private experiment'}
        save(metadata / 'LINEAGE.json', lineage)
        (metadata / 'REBUILD.md').write_text(
            '# Experimental dev5 power/restart derivative\n\n'
            'Both patches are already applied to frozen dev4. Only the five declared source paths\n'
            'and CONFIG_LOCALVERSION change; SATA and other source remain exact. Original PSX\n'
            'metadata describes historical bases. Device firmware and initramfs remain external.\n'
            'The retained-DMA restart gate defaults off. No DMA drain, reset, suspend, unload or\n'
            'hardware power acceptance is established by this build. Use every matching module.\n\n'
            '    make ARCH=x86_64 LOCALVERSION= CC=gcc HOSTCC=gcc olddefconfig\n'
            '    make ARCH=x86_64 LOCALVERSION= CC=gcc HOSTCC=gcc -j16 bzImage modules\n')
        receipts = output / 'build-inputs/runtime'
        receipts.mkdir(parents=True)
        receipt = dict(lineage, status='SOURCE_PREPARED', resolved_config_sha256=C.digest(source / '.config'),
                       candidate_files={name: C.digest(source / name) for name in DELTA})
        save(receipts / 'SOURCE-RECEIPT.json', receipt)
        shutil.copyfile(receipts / 'SOURCE-RECEIPT.json', metadata / 'SOURCE-RECEIPT.json')
        for path in metadata.iterdir():
            expected[path.relative_to(source).as_posix()] = C.file_record(path)
        save(output / 'SOURCE-ARCHIVE.json', expected)
        make = ['make', 'ARCH=x86_64', 'LOCALVERSION=', 'CC=gcc', 'HOSTCC=gcc']
        for target in ('olddefconfig', 'syncconfig'):
            run(make + [target], cwd=source)
        C.require((source / '.config').read_bytes() == config, 'Undeclared config delta')
        C.require(run(make + ['-s', 'kernelrelease'], cwd=source, capture=True) == RELEASE, 'Wrong release')
        run(make + [f'-j{args.jobs}', 'bzImage', 'modules'], cwd=source)
        N.verify_tree(source, expected)
        save(receipts / 'LINK-ATTEMPT-01.json', {'schema': 1, 'status': 'PASS_LINK', 'returncode': 0,
            'release': RELEASE, 'source_receipt_sha256': C.digest(receipts / 'SOURCE-RECEIPT.json'),
            'kernel_sha256': C.digest(source / 'arch/x86/boot/bzImage'), 'hardware_acceptance': False})
        stage = output / 'stage'
        run(make + ['INSTALL_MOD_PATH=' + str(stage), 'INSTALL_MOD_STRIP=1', 'modules_install'], cwd=source)
        modules = stage / 'lib/modules' / RELEASE
        for name in ('build', 'source'):
            path = modules / name
            if path.is_symlink():
                C.require(path.resolve() == source, 'Unexpected generated module link')
                path.unlink()
        run(['depmod', '-b', stage, '-F', source / 'System.map', RELEASE])
        old_modules = json.loads((base / 'MODULES.json').read_text())
        names = {p.relative_to(stage).as_posix() for p in modules.rglob('*.ko')}
        C.require(names == {n.replace(BASE, RELEASE) for n in old_modules} and len(names) == 41,
                  'Complete matching 41-module set differs')
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
        C.require(not any(p.is_symlink() for p in stage.rglob('*')), 'Unexpected staged module link')
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
        for name, original_name in (('bzImage', 'arch/x86/boot/bzImage'), ('config', '.config'),
                                    ('vmlinux', 'vmlinux'), ('System.map', 'System.map')):
            shutil.copyfile(source / original_name, output / name)
        (output / 'version.txt').write_text(RELEASE + '\n')
        runtime = dict(lineage, kernel_sha256=C.digest(output / 'bzImage'), config_sha256=C.digest(output / 'config'),
            module_count=41, modules=records, module_archive={'name': module_archive.name, 'sha256': C.digest(module_archive)},
            source_archive={'name': source_archive.name, 'sha256': C.digest(source_archive)},
            source_receipt_sha256=C.digest(receipts / 'SOURCE-RECEIPT.json'),
            link_receipt_sha256=C.digest(receipts / 'LINK-ATTEMPT-01.json'))
        save(output / 'RUNTIME-IDENTITY.json', runtime)
        artifacts = {name: C.file_record(output / name) for name in
            ('bzImage', 'config', 'vmlinux', 'System.map', 'version.txt', source_archive.name, module_archive.name)}
        save(output / 'PACKAGE.json', dict(lineage, status='PASS_PACKAGE', artifacts=artifacts,
             module_count=41, source_receipt=runtime['source_receipt_sha256'], link_receipt=runtime['link_receipt_sha256']))
        for name, expected_sha in PINS.items():
            checked_file(base / name, expected_sha)
        for name, expected_sha in tool_pins.items():
            C.require(C.digest(inputs[name]) == expected_sha, 'Versioned input changed during build')
        files = sorted(p for p in output.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
        (output / 'SHA256SUMS').write_text(''.join(C.digest(p) + '  ' + p.name + '\n' for p in files))
        print(json.dumps({'status': 'PASS_PACKAGE', 'release': RELEASE, 'kernel_sha256': runtime['kernel_sha256'],
                          'module_count': 41, 'output': str(output)}), flush=True)
    except BaseException as error:
        save(output / 'FAILED_PRESERVED.json', {'status': 'FAILED_PRESERVED', 'error': str(error)})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-package', type=Path, required=True)
    parser.add_argument('--icc-patch', type=Path, default=ICC_PATCH)
    parser.add_argument('--uvd-patch', type=Path, default=UVD_PATCH)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=min(16, os.cpu_count() or 1))
    parser.add_argument('--plan', action='store_true', help='Verify input pins and print plan without creating output or building')
    build(parser.parse_args())
