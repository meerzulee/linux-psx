#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import importlib.util
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fpkg_build', HERE / 'build.py')
build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)

class LogoTests(unittest.TestCase):
    def test_launcher_and_console_show_the_same_wordmark(self):
        blocks = set(' █▄▀▌▐')
        def art(source):
            return [line for line in re.findall(r'"([^"]*)"', source) if len(line) > 40 and set(line) <= blocks]
        launcher = art((HERE / 'ui.cpp').read_text(encoding='utf-8'))
        console = art((HERE.parent / 'tui/src/bin/diagnostix/view.rs').read_text(encoding='utf-8'))
        self.assertEqual(len(launcher), 8)
        self.assertEqual(launcher, console)
    def test_icon_is_the_letter_on_the_framed_background(self):
        root = ET.fromstring(build.icon_svg())
        namespace = '{http://www.w3.org/2000/svg}'
        self.assertEqual((root.get('width'), root.get('height')), ('512', '512'))
        cells = [cell for cell in root.find(namespace + 'g')]
        self.assertEqual(len(cells), sum(glyph != ' ' for line in build.ICON_LETTER for glyph in line))
        for cell in cells:
            self.assertGreaterEqual(int(cell.get('x')), 60)
            self.assertLessEqual(int(cell.get('x')) + int(cell.get('width')), 452)
            self.assertGreaterEqual(int(cell.get('y')), 60)
            self.assertLessEqual(int(cell.get('y')) + int(cell.get('height')), 452)

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='psx-fpkg-contract-', dir='/var/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in build.NAMES: (self.root / name).write_bytes(b'fixture')
        (self.root / 'vram.txt').write_bytes(b'1024\n')
        self.data = {'schema': 1, 'product': 'Diagnostix', 'version': '0.16', 'files': {name: build.pin(self.root / name) for name in build.NAMES}}
        self.save()
    def save(self): (self.root / 'bundle.json').write_text(json.dumps(self.data))
    def test_exact_contract_and_generated_pins(self):
        _, facts = build.validate_bundle(self.root)
        text = build.header(facts)
        for name in (*build.NAMES, 'bundle.json'): self.assertIn(facts[name]['sha256'], text)
        self.assertEqual(text.count('ULL'), 6)
    def test_hash_mismatch(self):
        (self.root / 'loader.elf').write_bytes(b'altered')
        with self.assertRaisesRegex(ValueError, 'mismatch'): build.validate_bundle(self.root)
    def test_fixed_paths_only(self):
        self.data['files']['../escape'] = self.data['files'].pop('bzImage'); self.save()
        with self.assertRaises(ValueError): build.validate_bundle(self.root)
    def test_wrong_identity_or_profile(self):
        for key, value in [('schema', True), ('product', 'Omarchy'), ('version', '0.01'), ('version', '0.02'), ('version', '0.03'), ('version', '0.04'), ('version', '0.05'), ('version', '0.06'), ('version', '0.07'), ('version', '0.08'), ('version', '0.09'), ('version', '0.10'), ('version', '0.11'), ('version', '0.12'), ('version', '0.13'), ('version', '0.15'), ('version', '0.17'), ('profile', 'other'), ('kernel_release', 'native-dev3')]:
            original = self.data.copy(); self.data[key] = value; self.save()
            with self.subTest(key=key), self.assertRaises(ValueError): build.validate_bundle(self.root)
            self.data = original
    def test_symlink_refused(self):
        file = self.root / 'loader.elf'; file.unlink(); file.symlink_to('/dev/null')
        with self.assertRaises(ValueError): build.validate_bundle(self.root)
    def test_bootargs_boundary(self):
        for value in (b'a'*512, b'bad\0args', b'bad\xff'):
            (self.root / 'bootargs.txt').write_bytes(value); self.data['files']['bootargs.txt'] = build.pin(self.root / 'bootargs.txt'); self.save()
            with self.subTest(value=value[:10]), self.assertRaises(ValueError): build.validate_bundle(self.root)
    def test_vram_is_explicit_and_only_1024(self):
        for value in (b'1024', b'1024\n', b'2048\n', b'1024\x00', b'1024\nextra', b''):
            (self.root / 'vram.txt').write_bytes(value)
            self.data['files']['vram.txt'] = build.pin(self.root / 'vram.txt'); self.save()
            with self.subTest(value=value):
                if value in (b'1024', b'1024\n'):
                    build.validate_bundle(self.root)
                else:
                    with self.assertRaises(ValueError): build.validate_bundle(self.root)
    def test_duplicate_json_key_refused(self):
        path = self.root / 'bundle.json'; path.write_text(path.read_text().replace('"schema": 1', '"schema": 1, "schema": 1'))
        with self.assertRaisesRegex(ValueError, 'Duplicate'): build.validate_bundle(self.root)
    def test_gp4_actual_directory_tree(self):
        gp4 = self.root / 'test.gp4'; gp4.write_text('<psproject><rootdir><dir targ_name="old-sample"/></rootdir></psproject>')
        build.package_directories(gp4, ['bundle/loader.elf', 'LICENSES/notice.txt', 'assets/fonts/font.ttf', 'sce_sys/about/right.sprx', 'eboot.bin'])
        tree = ET.parse(gp4).getroot().find('rootdir')
        self.assertEqual({x.attrib['targ_name'] for x in tree}, {'bundle', 'LICENSES', 'assets', 'sce_sys'})
        self.assertEqual(tree.find("dir[@targ_name='assets']/dir").attrib['targ_name'], 'fonts')
    def test_pkg_entry_index_and_bounds(self):
        data = bytearray(128); data[:4] = b'\x7fCNT'
        struct.pack_into('>I', data, 0x10, 2); struct.pack_into('>I', data, 0x18, 64)
        struct.pack_into('>I', data, 64, 0x1200); struct.pack_into('>I', data, 96, 0x1000)
        path = self.root / 'synthetic.pkg'; path.write_bytes(data)
        self.assertEqual(build.package_entry_indices(path), {0x1200: 0, 0x1000: 1})
        path.write_bytes(data[:100])
        with self.assertRaisesRegex(ValueError, 'bounds'): build.package_entry_indices(path)

class TransactionTests(unittest.TestCase):
    def test_serialized_orbis_native_stat_abi_with_sanitizers(self):
        with tempfile.TemporaryDirectory(prefix='psx-fpkg-stat-abi-', dir='/var/tmp') as temporary:
            root = Path(temporary); binary = root/'native-stat-test'
            subprocess.run(['/usr/bin/clang++','-std=c++17','-Wall','-Wextra','-Werror','-pedantic','-g','-fsanitize=address,undefined','-DDIAGNOSTIX_HOST_TEST','-I',str(HERE),str(HERE/'native_stat.cpp'),str(HERE/'tests/native_stat_test.cpp'),'-o',str(binary)],check=True)
            result = subprocess.run([str(binary)],text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('observed mismatch',result.stdout)

    def test_actual_controller_mapper_with_sanitizers(self):
        with tempfile.TemporaryDirectory(prefix='psx-fpkg-controller-', dir='/var/tmp') as temporary:
            root = Path(temporary); binary = root/'controller-test'
            subprocess.run(['/usr/bin/clang++','-std=c++17','-Wall','-Wextra','-Werror','-pedantic','-g','-fsanitize=address,undefined','-I',str(HERE),str(HERE/'controller.cpp'),str(HERE/'tests/controller_test.cpp'),'-o',str(binary)],check=True)
            result = subprocess.run([str(binary)],text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('hysteresis',result.stdout)

    def test_actual_socket_writer_with_sanitizers(self):
        with tempfile.TemporaryDirectory(prefix='psx-fpkg-transport-', dir='/var/tmp') as temporary:
            root = Path(temporary); binary = root/'transport-test'
            subprocess.run(['/usr/bin/clang++','-std=c++17','-Wall','-Wextra','-Werror','-pedantic','-g','-fsanitize=address,undefined','-I',str(HERE),str(HERE/'transport.cpp'),str(HERE/'tests/transport_test.cpp'),'-o',str(binary)],check=True)
            result = subprocess.run([str(binary)],text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.count('PASS'),4)

    def test_actual_southbridge_read_with_sanitizers(self):
        with tempfile.TemporaryDirectory(prefix='psx-fpkg-board-', dir='/var/tmp') as temporary:
            binary = Path(temporary)/'board-test'
            subprocess.run(['/usr/bin/clang++','-std=c++17','-Wall','-Wextra','-Werror','-pedantic','-g','-fsanitize=address,undefined','-I',str(HERE),str(HERE/'console_read.cpp'),str(HERE/'tests/console_read_test.cpp'),'-o',str(binary)],check=True)
            result = subprocess.run([str(binary)],text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.count('PASS'),8)

    def test_actual_ui_gate_and_home_rows(self):
        if shutil.which('pkg-config') is None or subprocess.run(['pkg-config','--exists','freetype2']).returncode != 0:
            self.skipTest('host FreeType is not installed')
        flags = subprocess.run(['pkg-config','--cflags','--libs','freetype2'],check=True,capture_output=True,text=True).stdout.split()
        with tempfile.TemporaryDirectory(prefix='psx-fpkg-ui-tests-', dir='/var/tmp') as temporary:
            binary = Path(temporary)/'ui-test'
            subprocess.run(['/usr/bin/clang++','-std=c++17','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-DDIAGNOSTIX_HOST_TEST','-I',str(HERE),str(HERE/'ui.cpp'),str(HERE/'tests/ui_test.cpp'),'-o',str(binary),*flags],check=True)
            result = subprocess.run([str(binary)],text=True,capture_output=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.count('PASS'),1)
    def test_actual_boot_dispatch_launches_once_without_confirmation(self):
        source = (HERE/'main.cpp').read_text()
        method = re.search(r'void launch_boot\(.*?\n\}', source, re.S).group()
        fixture = r'''
#include <cassert>
#include <string>
namespace diagnostix {
enum class Result { Ready, Sent, HandoffFailed, Invalid };
const char* message(Result){return "status";}
struct Bundle { int calls=0; bool manual=false; std::string extra; Result outcome=Result::Sent; Result boot(bool chosen,const std::string& added){++calls;manual=chosen;extra=added;return outcome;} std::string status(Result){return "status";} };
}
''' + method + r'''
int main(){for(auto outcome:{diagnostix::Result::Sent,diagnostix::Result::HandoffFailed,diagnostix::Result::Invalid}){
diagnostix::Bundle bundle;bundle.outcome=outcome;auto result=diagnostix::Result::Ready;bool ready=false,attempted=false;std::string status;
launch_boot(bundle,result,ready,attempted,status,false,"");assert(bundle.calls==0&&!attempted);
ready=true;launch_boot(bundle,result,ready,attempted,status,true,"dx.console=Unknown");assert(bundle.calls==1&&bundle.manual&&bundle.extra=="dx.console=Unknown"&&attempted&&!ready&&result==outcome&&status=="status");
launch_boot(bundle,result,ready,attempted,status,false,"");assert(bundle.calls==1);
}}
'''
        with tempfile.TemporaryDirectory(prefix='psx-fpkg-dispatch-tests-', dir='/var/tmp') as temporary:
            root = Path(temporary); (root/'test.cpp').write_text(fixture)
            subprocess.run(['/usr/bin/clang++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(root/'test.cpp'),'-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True,timeout=5)
    def test_actual_install_dispatch_runs_without_confirmation(self):
        source = (HERE/'main.cpp').read_text()
        method = re.search(r'void launch_install\(.*?\n\}', source, re.S).group()
        fixture = r'''
#include <cassert>
#include <string>
namespace diagnostix {
enum class Result { Ready, Invalid, IoError, Recovered, AlreadyAttempted };
struct Bundle { int calls=0; Result outcome=Result::Ready; Result install(){++calls;return outcome;} std::string status(Result){return "precise reason";} };
}
''' + method + r'''
int main(){for(auto outcome:{diagnostix::Result::Ready,diagnostix::Result::Invalid,diagnostix::Result::IoError,diagnostix::Result::Recovered}){
diagnostix::Bundle bundle;bundle.outcome=outcome;auto result=diagnostix::Result::Invalid;bool ready=false;std::string status;
launch_install(bundle,result,ready,false,status);assert(bundle.calls==1&&ready==(outcome==diagnostix::Result::Ready)&&result==outcome&&status=="precise reason");
launch_install(bundle,result,ready,true,status);assert(bundle.calls==1&&!ready&&result==diagnostix::Result::AlreadyAttempted);
}}
'''
        with tempfile.TemporaryDirectory(prefix='psx-fpkg-install-dispatch-', dir='/var/tmp') as temporary:
            root = Path(temporary); (root/'test.cpp').write_text(fixture)
            subprocess.run(['/usr/bin/clang++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(root/'test.cpp'),'-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True,timeout=5)
    def test_actual_runtime_core_with_sanitizers(self):
        with tempfile.TemporaryDirectory(prefix='psx-fpkg-native-tests-', dir='/var/tmp') as temporary:
            root = Path(temporary); binary = root / 'bundle-test'
            subprocess.run(['/usr/bin/clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pedantic', '-g', '-fsanitize=address,undefined', '-DDIAGNOSTIX_HOST_TEST', '-I', str(HERE), str(HERE/'bundle.cpp'), str(HERE/'sha256.cpp'), str(HERE/'native_stat.cpp'), str(HERE/'tests/bundle_test.cpp'), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary), str(root/'fixture')], text=True, capture_output=True, env={**os.environ, 'TMPDIR':'/var/tmp'})
            self.assertEqual(result.returncode, 0, result.stderr[-2500:])
            self.assertEqual(result.stdout.count('PASS'), 9)
    def test_actual_present_method_has_bounded_error_paths(self):
        source = (HERE/'main.cpp').read_text()
        method = re.search(r'  void present\(int frame_id\) \{.*?\n  \}', source, re.S).group()
        fixture = r'''
#include <cassert>
#include <cstdint>
using OrbisKernelUseconds=uint32_t;
struct OrbisVideoOutFlipStatus { int flipArg; };
struct OrbisKernelEvent {};
constexpr int ORBIS_VIDEO_OUT_FLIP_VSYNC=1;
int mode=0, waits=0, submissions=0;
int sceVideoOutSubmitFlip(int,int,int,int){++submissions;return mode==1?-1:0;}
int sceVideoOutGetFlipStatus(int,OrbisVideoOutFlipStatus* s){s->flipArg=mode==0?9:0;return mode==2?-1:0;}
int sceKernelWaitEqueue(int,OrbisKernelEvent*,int,int*,OrbisKernelUseconds* timeout){assert(timeout&&*timeout==250000);++waits;return mode==3?-1:0;}
struct Display { int video_=0,active_=0,flip_queue_=0; [[noreturn]] static void display_failure(){throw 5;}
''' + method + r'''
};
int main(){for(mode=0;mode<5;++mode){Display d;waits=submissions=0;bool failed=false;try{d.present(9);}catch(int v){assert(v==5);failed=true;}assert(submissions==1);assert(failed==(mode!=0));assert(waits==(mode==3?1:mode==4?8:0));assert(d.active_==(mode==0?1:0));}}
'''
        with tempfile.TemporaryDirectory(prefix='psx-fpkg-flip-tests-', dir='/var/tmp') as temporary:
            root=Path(temporary); (root/'test.cpp').write_text(fixture)
            subprocess.run(['/usr/bin/clang++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(root/'test.cpp'),'-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test')],check=True,timeout=5)

if __name__ == '__main__': unittest.main()
