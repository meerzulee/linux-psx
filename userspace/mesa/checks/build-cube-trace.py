#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build an instrumented, pinned upstream vkcube locally. Never run or install it."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--tools-source', type=Path, required=True)
ap.add_argument('--mesa-source', type=Path, required=True)
ap.add_argument('--output', type=Path, required=True)
args = ap.parse_args()
if platform.system() != 'Linux' or platform.machine() != 'x86_64':
    ap.error('Build on native Linux x86-64')
here = Path(__file__).resolve().parent
tools = args.tools_source.resolve(strict=True)
include = args.mesa_source.resolve(strict=True) / 'include'
original = (tools / 'cube/cube.c').read_bytes()
source_hash = hashlib.sha256(original).hexdigest()
if source_hash != 'c609168cdc3c42dd03a011950c971c2b6b926d02a882e340a1311f842e0e4a59':
    ap.error('Require Vulkan-Tools v1.4.357 commit 8c66b352925cb771f793a4d3220b1321ae0febf1')
anchor = '#include "cube_functions.h"\n'
text = original.decode()
if text.count(anchor) != 1:
    ap.error('Nonunique source instrumentation anchor')
out = args.output.absolute()
out.mkdir(parents=True, exist_ok=False)
text = text.replace(anchor, anchor + '#include "cube-trace.h"\n', 1)
(out / 'cube.c').write_text(text)
shutil.copy2(here / 'cube-trace.h', out / 'cube-trace.h')
def pkg_data(name):
    return Path(subprocess.run(['/usr/bin/pkg-config', '--variable=pkgdatadir', name],
                               text=True, capture_output=True, check=True).stdout.strip())
wayland = pkg_data('wayland-client')
protocols = pkg_data('wayland-protocols')
generated = []
for xml, name in ((wayland / 'wayland.xml', 'wayland-client'),
                  (protocols / 'stable/xdg-shell/xdg-shell.xml', 'xdg-shell'),
                  (protocols / 'unstable/xdg-decoration/xdg-decoration-unstable-v1.xml', 'xdg-decoration')):
    code = out / (name + '-code.c')
    subprocess.run(['/usr/bin/wayland-scanner', 'private-code', str(xml), str(code)], check=True)
    generated.append(str(code))
    if name != 'wayland-client':
        subprocess.run(['/usr/bin/wayland-scanner', 'client-header', str(xml),
                        str(out / (name + '-client-header.h'))], check=True)
command = ['/usr/bin/cc', '-std=gnu11', '-O2', '-g', '-Wall', '-Wextra', '-march=x86-64',
           '-DVK_USE_PLATFORM_WAYLAND_KHR', '-I' + str(include), '-I' + str(tools / 'cube'),
           '-I' + str(out), str(out / 'cube.c'), *generated, '-ldl', '-lm', '-pthread',
           '-o', str(out / 'vkcube-trace')]
subprocess.run(command, check=True)
receipt = {'upstream_repository': 'https://github.com/KhronosGroup/Vulkan-Tools',
           'upstream_commit': '8c66b352925cb771f793a4d3220b1321ae0febf1',
           'original_cube_sha256': source_hash, 'compile_command': command,
           'changes': 'Trace selected Vulkan calls; bound fence/acquire waits at2s; stop on failure. No reset/retry.',
           'outputs': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in out.iterdir() if p.is_file()},
           'hardware_acceptance': False}
(out / 'BUILD.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
