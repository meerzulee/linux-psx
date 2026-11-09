#!/usr/bin/env python3
"""Build and extract-audit one fixed native Diagnostix FPKG. No console access."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import struct
import subprocess
import tarfile
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
SDK_SHA = '3c7cd5bb593ca74fa1c13fd59f3938dc0fc07985167f7275063019e63abe4526'
SDK_URL = 'https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain/releases/download/v0.5.4/toolchain-llvm-18.tar.gz'
CACHE = Path('/var/tmp/psx-diagnostix-fpkg-sdk-v0.5.4')
OPENSSL = Path('/var/tmp/psx-diagnostix-fpkg-openssl-1.1.1w/openssl-1.1.1w')
OPENSSL_SHA = 'cf3098950cb4d853ad95c0841f1f9c6d3dc102dccfcacd521d93925208b76ac8'
TITLE_ID = 'DGNX00001'
CONTENT_ID = 'IV0000-DGNX00001_00-DIAGNOSTIX000001'
NAMES = ('bzImage', 'initramfs.cpio.gz', 'bootargs.txt', 'vram.txt', 'loader.elf')
LIMITS = (32 << 20, 1024 << 20, 511, 16, 1 << 20)

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def pin(path):
    return {'bytes': Path(path).stat().st_size, 'sha256': sha(path)}

def regular(path):
    path = Path(path)
    if path.resolve() != path.absolute() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f'Expected regular non-symlink input: {path.name}')
    return path

def validate_bundle(directory):
    directory = Path(directory).absolute()
    if directory.resolve() != directory or not directory.is_dir():
        raise ValueError('Bundle must be a real directory')
    raw = regular(directory / 'bundle.json').read_bytes()
    if len(raw) > 16384:
        raise ValueError('Manifest too large')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    data = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(data, dict) or data.get('schema') != 1 or type(data['schema']) is not int or data.get('product') != 'Diagnostix' or data.get('version') != '0.16':
        raise ValueError('Wrong bundle identity')
    if set(data) - {'schema', 'product', 'version', 'files', 'profile', 'kernel_release'}:
        raise ValueError('Unknown manifest fields')
    if data.get('profile', 'ps4-baikal-diagnostix-ram-alpha1') != 'ps4-baikal-diagnostix-ram-alpha1':
        raise ValueError('Wrong RAM profile')
    if data.get('kernel_release', '7.2.11-psx-ps4-baikal-uvd-runtime-dev6') != '7.2.11-psx-ps4-baikal-uvd-runtime-dev6':
        raise ValueError('Wrong kernel release')
    if not isinstance(data.get('files'), dict) or set(data['files']) != set(NAMES):
        raise ValueError('Exactly five fixed payload files required')
    if {p.name for p in directory.iterdir()} != set(NAMES) | {'bundle.json'}:
        raise ValueError('Unexpected bundle member')
    facts = {}
    for name, maximum in zip(NAMES, LIMITS):
        expected = data['files'][name]
        if not isinstance(expected, dict) or set(expected) != {'bytes', 'sha256'} or type(expected['bytes']) is not int or not 0 < expected['bytes'] <= maximum or not isinstance(expected['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', expected['sha256']):
            raise ValueError(f'Invalid pin: {name}')
        actual = pin(regular(directory / name))
        if actual != expected: raise ValueError(f'Input hash/size mismatch: {name}')
        facts[name] = actual
    arguments = (directory / 'bootargs.txt').read_bytes()
    if b'\0' in arguments or any(c < 32 and c not in (9, 10) or c > 126 for c in arguments):
        raise ValueError('Boot arguments must be bounded ASCII')
    if (directory / 'vram.txt').read_bytes() not in (b'1024', b'1024\n'):
        raise ValueError('Only the 1 GiB VRAM profile is supported')
    if sum(v['bytes'] for v in facts.values()) + (1 << 20) > 1 << 30:
        raise ValueError('Boot payload exceeds 1 GiB including reserve')
    facts['bundle.json'] = pin(directory / 'bundle.json')
    return data, facts

def header(facts):
    rows = ['#pragma once', '#include "bundle.hpp"', 'static const diagnostix::Pin kBundlePins[] = {']
    for name in (*NAMES, 'bundle.json'):
        row = facts[name]
        rows.append(f'  {{"{name}", {row["bytes"]}ULL, "{row["sha256"]}"}},')
    return '\n'.join(rows + ['};', ''])

def verify_sdk(sdk, archive):
    regular(archive)
    if sha(archive) != SDK_SHA: raise ValueError('OpenOrbis SDK archive pin differs')
    prefix = 'OpenOrbis/PS4Toolchain/'
    # Check every actual build dependency against the pinned archive, including headers.
    selected = ('include/', 'lib/', 'bin/linux/', 'samples/font/', 'link.x', 'LICENSE')
    checked = 0
    with tarfile.open(archive, 'r:gz') as tar:
        for item in tar:
            name = item.name.removeprefix('./')
            if not name.startswith(prefix): continue
            name = name[len(prefix):]
            if not any(name.startswith(part) for part in selected): continue
            path = sdk / name
            if item.isfile():
                regular(path)
                if path.stat().st_size != item.size or sha(path) != hashlib.file_digest(tar.extractfile(item), 'sha256').hexdigest():
                    raise ValueError(f'SDK member differs: {name}')
                checked += 1
            elif item.issym():
                if not path.is_symlink() or os.readlink(path) != item.linkname:
                    raise ValueError(f'SDK symlink differs: {name}')
    if checked < 100: raise ValueError('SDK archive layout not recognized')
    return checked

def run(command, cwd, log, openssl_runtime=None, temporary_directory=None):
    environment = {**os.environ, 'TMPDIR': str(temporary_directory or '/var/tmp'), 'LC_ALL': 'C', 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1'}
    tool = Path(command[0])
    if tool.parent.name == 'linux' and tool.parent.parent.name == 'bin':
        environment['OO_PS4_TOOLCHAIN'] = str(tool.parent.parent.parent)
        if tool.name == 'PkgTool.Core' and openssl_runtime:
            environment['LD_LIBRARY_PATH'] = str(openssl_runtime)
    with log.open('ab') as output:
        output.write(('\n$ ' + ' '.join(map(str, command)) + '\n').encode())
        output.flush()
        subprocess.run(list(map(str, command)), cwd=cwd, stdout=output, stderr=subprocess.STDOUT, check=True,
                       env=environment)

def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)

# `omarchy-ascii D`, the font of the Omarchy logo.
ICON_LETTER = (
    '████████▄',
    '███   ▀███',
    '███    ███',
    '███    ███',
    '███    ███',
    '███    ███',
    '███   ▄███',
    '████████▀',
)

def icon_svg():
    cell_width, cell_height = 24, 44
    left = (512 - 10 * cell_width) // 2
    top = (512 - len(ICON_LETTER) * cell_height) // 2
    parts = {'█': (0, 1), '▀': (0, 0.5), '▄': (0.5, 0.5)}
    blocks = []
    for row, line in enumerate(ICON_LETTER):
        for column, glyph in enumerate(line):
            if glyph != ' ':
                start, share = parts[glyph]
                y, height = top + row * cell_height + int(start * cell_height), int(share * cell_height)
                blocks.append(f'<rect x="{left + column * cell_width}" y="{y}" width="{cell_width}" height="{height}"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" shape-rendering="crispEdges">'
            '<rect width="512" height="512" fill="#10141a"/>'
            '<rect x="52" y="52" width="408" height="408" rx="36" fill="#192029" stroke="#8fd3c7" stroke-width="5"/>'
            f'<g fill="#8fd3c7">{"".join(blocks)}</g></svg>')

def package_directories(gp4, members):
    # SDK create-gp4 emits a sample-specific directory list. Build the exact
    # directory tree for our fixed bundle/notices without changing SDK tools.
    tree = ET.parse(gp4); root = tree.getroot().find('rootdir')
    if root is None: raise ValueError('GP4 rootdir missing')
    root.clear(); nodes = {'': root}
    for member in members:
        parts = Path(member).parts[:-1]
        for length in range(1, len(parts) + 1):
            path = '/'.join(parts[:length])
            if path not in nodes:
                nodes[path] = ET.SubElement(nodes['/'.join(parts[:length-1])], 'dir', {'targ_name': parts[length-1]})
    tree.write(gp4, encoding='utf-8', xml_declaration=True)

def package_entry_indices(package):
    # pkg_extractentry takes the table index, despite its <entry_id> help label.
    # The outer header/table are big-endian and are validated by PkgTool first.
    size = package.stat().st_size
    with package.open('rb') as stream:
        header = stream.read(32)
        if len(header) != 32 or header[:4] != b'\x7fCNT': raise ValueError('Invalid PKG header')
        count = struct.unpack_from('>I', header, 0x10)[0]
        offset = struct.unpack_from('>I', header, 0x18)[0]
        if not 0 < count <= 4096 or offset + count * 32 > size: raise ValueError('Invalid PKG entry bounds')
        stream.seek(offset); entries = {}
        for index in range(count):
            record = stream.read(32); identifier = struct.unpack_from('>I', record)[0]
            if identifier in entries: raise ValueError('Duplicate PKG entry')
            entries[identifier] = index
    return entries

def sfo_fields(path):
    data = path.read_bytes()
    if len(data) < 20: raise ValueError('Truncated SFO')
    magic, version, keys, values, count = struct.unpack_from('<5I', data)
    if magic != 0x46535000 or version != 0x101 or count > 128 or 20 + count * 16 > keys or not keys <= values <= len(data):
        raise ValueError('Invalid SFO layout')
    fields = {}
    for index in range(count):
        offset, kind, length, maximum, where = struct.unpack_from('<HHIII', data, 20 + index * 16)
        if keys + offset >= values or length > maximum or values + where + maximum > len(data): raise ValueError('Invalid SFO bounds')
        key_bytes = data[keys + offset:values].split(b'\0', 1)
        if len(key_bytes) != 2: raise ValueError('Unterminated SFO key')
        key = key_bytes[0].decode('ascii')
        if key in fields: raise ValueError('Duplicate SFO field')
        fields[key] = (kind, maximum, data[values + where:values + where + length])
    return fields

def build(bundle, output, sdk, archive, apply=False, openssl_runtime=OPENSSL):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64': raise ValueError('Native Linux x86-64 host required')
    _, facts = validate_bundle(bundle)
    output = output.absolute()
    if output.exists() or output.parent.resolve() != output.parent:
        raise ValueError('Use a NEW output directory with a real parent and enough free space')
    if bundle == output or bundle in output.parents or output in bundle.parents:
        raise ValueError('Input and output overlap')
    checked = verify_sdk(sdk, archive)
    compatibility = json.loads(regular(openssl_runtime.parent / 'BUILD.json').read_text())
    if compatibility.get('source_sha256') != OPENSSL_SHA or sha(regular(openssl_runtime.parent / 'openssl-1.1.1w.tar.gz')) != OPENSSL_SHA:
        raise ValueError('Host OpenSSL source identity differs')
    for name in ('libssl.so.1.1', 'libcrypto.so.1.1', 'LICENSE'):
        if pin(regular(openssl_runtime / name)) != compatibility['files'][name]:
            raise ValueError('Host OpenSSL runtime differs')
    plan = {'schema': 1, 'product': 'Diagnostix', 'version': '0.16', 'title_id': TITLE_ID,
            'content_id': CONTENT_ID, 'bundle': facts, 'sdk': {'url': SDK_URL, 'sha256': SDK_SHA, 'members_verified': checked},
            'host_openssl': compatibility, 'hardware_acceptance': False, 'console_operations': False, 'apply': apply}
    if not apply: return plan
    # PkgTool maps both inner and outer PFS temporary files while stage and
    # package coexist. Keep those files on the explicitly budgeted output FS.
    needed = sum(x['bytes'] for x in facts.values()) * 4 + (64 << 20)
    if shutil.disk_usage(output.parent).free < needed: raise ValueError('Insufficient space for stage/package/extract audit')
    output.mkdir(); stage = output / 'stage'; stage.mkdir(); objects = output / 'objects'; objects.mkdir()
    temporary_directory = output / 'tmp'; temporary_directory.mkdir()
    log = output / 'build.log'
    def execute(command, cwd, log):
        return run(command, cwd, log, openssl_runtime, temporary_directory)
    (output / 'bundle_pins.hpp').write_text(header(facts))
    for name in facts: copy(bundle / name, stage / 'bundle' / name)
    for name in ('LICENSES',): shutil.copytree(HERE / name, stage / name)
    copy(HERE / 'provenance.json', stage / 'LICENSES' / 'provenance.json')
    for name in ('Gontserrat-Regular.ttf', 'OFL.txt'):
        copy(sdk / 'samples/font/assets/fonts' / name, stage / 'assets/fonts' / name)
    for name in ('sce_sys/about/right.sprx', 'sce_module/libc.prx', 'sce_module/libSceFios2.prx'):
        copy(sdk / 'samples/font' / name, stage / name)
    copy(sdk / 'LICENSE', stage / 'LICENSES/OpenOrbis-SDK-GPL3.txt')
    copy(sdk / 'bin/linux/LICENSE.txt', stage / 'LICENSES/OpenOrbis-tools-license.txt')
    copy(HERE.parent / 'fpkg-loader/LICENSE', stage / 'LICENSES/PS4-Linux-Loader-BSD2.txt')
    copy(HERE.parent / 'fpkg-loader/UPSTREAM-LICENSE', stage / 'LICENSES/PS4-Linux-Loader-Upstream-BSD2.txt')
    copy(HERE.parent / 'fpkg-loader/provenance.json', stage / 'LICENSES/PS4-Linux-Loader-provenance.json')
    compiler = Path('/usr/bin/clang++'); linker = Path('/usr/bin/ld.lld')
    flags = ['--target=x86_64-pc-freebsd12-elf', '-std=c++17', '-O2', '-Wall', '-Wextra', '-fPIC', '-funwind-tables', '-c', '-isysroot', sdk, '-isystem', sdk / 'include', '-isystem', sdk / 'include/c++/v1', '-I', HERE, '-I', output]
    source_pins = {name: pin(HERE / name) for name in ('main.cpp', 'ui.cpp', 'ui.hpp', 'controller.cpp', 'controller.hpp', 'bundle.cpp', 'bundle.hpp', 'transport.cpp', 'transport.hpp', 'console_read.cpp', 'console_read.hpp', 'sha256.cpp', 'sha256.hpp', 'native_stat.cpp', 'native_stat.hpp', 'build.py', 'provenance.json')}
    objs = []
    for name in ('main', 'ui', 'controller', 'bundle', 'transport', 'console_read', 'sha256', 'native_stat'):
        obj = objects / f'{name}.o'; execute([compiler, *flags, HERE / f'{name}.cpp', '-o', obj], HERE, log); objs.append(obj)
    elf = output / 'diagnostix.elf'
    execute([linker, *objs, '-o', elf, '-m', 'elf_x86_64', '-pie', '--script', sdk / 'link.x', '--eh-frame-hdr', '-L', sdk / 'lib', '-lc', '-lkernel', '-lc++', '-lSceVideoOut', '-lSceSysmodule', '-lSceFreeType', '-lScePad', '-lSceUserService', sdk / 'lib/crt1.o'], HERE, log)
    tools = sdk / 'bin/linux'
    execute([tools / 'create-fself', '-in=' + str(elf), '-out=' + str(output / 'diagnostix.oelf'), '--eboot', stage / 'eboot.bin', '--paid', '0x3800000000000011'], HERE, log)
    sfo = stage / 'sce_sys/param.sfo'; pkgtool = tools / 'PkgTool.Core'
    execute([pkgtool, 'sfo_new', sfo], HERE, log)
    fields = [('APP_TYPE', 'Integer', 4, '1'), ('APP_VER', 'Utf8', 8, '0.16'), ('ATTRIBUTE', 'Integer', 4, '0'), ('CATEGORY', 'Utf8', 4, 'gd'), ('CONTENT_ID', 'Utf8', 48, CONTENT_ID), ('DOWNLOAD_DATA_SIZE', 'Integer', 4, '0'), ('SYSTEM_VER', 'Integer', 4, '0'), ('TITLE', 'Utf8', 128, 'Diagnostix'), ('TITLE_ID', 'Utf8', 12, TITLE_ID), ('VERSION', 'Utf8', 8, '0.16')]
    for key, kind, length, value in fields:
        execute([pkgtool, 'sfo_setentry', sfo, key, '--type', kind, '--maxsize', length, '--value', value], HERE, log)
    icon = output / 'icon.svg'
    icon.write_text(icon_svg())
    execute(['/usr/bin/magick', icon, '-alpha', 'off', '-type', 'TrueColor', 'PNG24:' + str(stage / 'sce_sys/icon0.png')], HERE, log)
    execute(['/usr/bin/magick', icon, '-resize', '228x128!', '-alpha', 'off', '-type', 'TrueColor', 'PNG24:' + str(stage / 'sce_sys/save_data.png')], HERE, log)
    members = sorted(str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file())
    gp4 = stage / 'diagnostix.gp4'
    execute([tools / 'create-gp4', '-out', gp4, '--content-id=' + CONTENT_ID, '--files', ' '.join(members)], stage, log)
    package_directories(gp4, members)
    execute([pkgtool, 'pkg_build', gp4, output], HERE, log)
    package = output / (CONTENT_ID + '.pkg'); regular(package)
    execute([pkgtool, 'pkg_validate', '--verbose', package], HERE, log)
    audit = output / 'audit'; audit.mkdir()
    execute([pkgtool, 'pkg_extract', '--verbose', package, audit], HERE, log)
    # These are outer PKG entries, not ordinary inner-PFS files.
    entries = package_entry_indices(package)
    for name, entry in (('param.sfo', 0x1000), ('save_data.png', 0x100d), ('icon0.png', 0x1200)):
        execute([pkgtool, 'pkg_extractentry', package, str(entries[entry]), audit / 'uroot/sce_sys' / name], HERE, log)
    for name in members:
        if name == 'sce_sys/param.sfo':
            before, after = sfo_fields(stage / name), sfo_fields(audit / 'uroot' / name)
            if any(after.get(key) != value for key, value in before.items()) or set(after) - set(before) != {'PUBTOOLINFO', 'PUBTOOLVER'}:
                raise ValueError('Packaged SFO identity differs')
        elif pin(audit / 'uroot' / name) != pin(stage / name): raise ValueError(f'Extracted package member differs: {name}')
    if validate_bundle(bundle)[1] != facts: raise ValueError('Source bundle changed during build')
    if any(pin(HERE / name) != value for name, value in source_pins.items()): raise ValueError('Source changed during build')
    plan.update({'package': {'name': package.name, **pin(package)}, 'source': source_pins,
                 'compiler': pin(compiler.resolve()), 'linker': pin(linker.resolve()),
                 'elf': pin(elf), 'package_readback': {'members': len(members), 'all_payload_bytes_match': True, 'sfo_original_fields_match': True, 'sfo_generated_fields': ['PUBTOOLINFO', 'PUBTOOLVER'], 'packaged_sfo': pin(audit / 'uroot/sce_sys/param.sfo')},
                 'installed_namespace': '/data/diagnostix/versions/0.16/boot', 'loader_connection_limit': 1})
    (output / 'BUILD.json').write_text(json.dumps(plan, indent=2, sort_keys=True) + '\n')
    (output / 'SHA256SUMS').write_text(f'{sha(package)}  {package.name}\n{sha(output / "BUILD.json")}  BUILD.json\n')
    return plan

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--sdk', type=Path, default=CACHE / 'OpenOrbis/PS4Toolchain')
    p.add_argument('--sdk-archive', type=Path, default=CACHE / 'toolchain-llvm-18.tar.gz')
    p.add_argument('--openssl-runtime', type=Path, default=OPENSSL, help='Private host-only OpenSSL 1.1 build for the SDK packaging tool')
    p.add_argument('--apply', action='store_true', help='Build a new local package; default verifies inputs and prints plan')
    a = p.parse_args()
    print(json.dumps(build(a.bundle.absolute(), a.output, a.sdk, a.sdk_archive, a.apply, a.openssl_runtime), indent=2, sort_keys=True))
if __name__ == '__main__': main()
