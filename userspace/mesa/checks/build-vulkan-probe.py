#!/usr/bin/env python3
"""Build only: native Linux Vulkan probe, generated shaders and source receipt."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--mesa-source', type=Path, required=True)
ap.add_argument('--output', type=Path, required=True)
args = ap.parse_args()
if platform.system() != 'Linux' or platform.machine() != 'x86_64':
    ap.error('Build on native Linux x86-64; no GPU commands are run')
here = Path(__file__).resolve().parent
include = args.mesa_source.resolve(strict=True) / 'include'
if not (include / 'vulkan/vulkan.h').is_file():
    ap.error('Require the recorded Mesa source with bundled Vulkan headers')
out = args.output.absolute()
out.mkdir(parents=True, exist_ok=False)
snapshot = out / 'source'
snapshot.mkdir()
for name in ('vulkan-probe.c', 'vulkan-probe.vert', 'vulkan-probe.frag',
             'build-vulkan-probe.py'):
    shutil.copyfile(here / name, snapshot / name)
sources = {}
sources['build-vulkan-probe.py'] = hashlib.sha256(
    (snapshot / 'build-vulkan-probe.py').read_bytes()).hexdigest()
header = []
for stage, variable in (('vert', 'vertex_spirv'), ('frag', 'fragment_spirv')):
    source = snapshot / ('vulkan-probe.' + stage)
    sources[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
    spirv = out / (stage + '.spv')
    subprocess.run(['/usr/bin/glslangValidator', '-V', '--target-env', 'vulkan1.0',
                    '-o', str(spirv), str(source)], check=True)
    raw = spirv.read_bytes()
    words = struct.unpack('<' + str(len(raw) // 4) + 'I', raw)
    if words[0] != 0x07230203:
        raise ValueError('Invalid generated SPIR-V')
    header.append('static const uint32_t ' + variable + '[] = {\n' +
                  ',\n'.join('0x%08xu' % v for v in words) + '\n};\n')
(out / 'vulkan-probe-shaders.h').write_text(''.join(header))
source = snapshot / 'vulkan-probe.c'
sources[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
command = ['/usr/bin/cc', '-std=c11', '-O2', '-g', '-Wall', '-Wextra', '-Wpedantic',
           '-Werror', '-march=x86-64', '-I' + str(include), '-I' + str(out),
           str(source), '-o', str(out / 'vulkan-probe'), '-lvulkan']
subprocess.run(command, check=True)
receipt = {'scope': 'Build only; no GPU access or hardware acceptance',
           'sources': sources, 'compile_command': command,
           'outputs': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in out.iterdir() if p.is_file()}}
(out / 'BUILD.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
