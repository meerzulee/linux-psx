"""Ordinary and alpha UI-only synthetic refreshes; no devices or networking."""
import copy
import gzip
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / path))
    spec = importlib.util.spec_from_loader(name, loader)
    app = importlib.util.module_from_spec(spec)
    sys.modules[name] = app
    loader.exec_module(app)
    return app


REFRESH = load("diagnostics_refresh_fixture", "scripts/refresh-ps4-diagnostics-frontend")
IMAGE = load("diagnostics_refresh_image_fixture", "scripts/build-ps4-diagnostics")
WRITER = load("diagnostics_refresh_cpio_writer", "scripts/ps4-diagnostics-archive")
CPIO = load("diagnostics_refresh_cpio_parser", "scripts/ps4-diagnostics-archive")


def sha(data):
    return hashlib.sha256(data).hexdigest()


class AlphaUIRefreshTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir='/var/tmp');self.addCleanup(temporary.cleanup)
        self.directory=Path(temporary.name);self.base=self.directory/'base';self.output=self.directory/'new'
        (self.base/'psx-diagnostics').mkdir(parents=True)
        self.stage=load('alpha_ui_stage_fixture','scripts/stage-ps4-diagnostics-usb')
        self.policy=load('alpha_ui_policy_fixture','diagnostics/ps4/uvd.py')
        payload=bytes(313912);crc=zlib.crc32(payload)&0xffffffff
        header=struct.pack('<IIHHHHIIII',314168,32,1,0,4,2,0x012a6509,313912,256,crc)
        self.firmware=header+bytes(256-len(header))+payload
        self.policy.FIRMWARE=dict(self.policy.FIRMWARE,sha256=sha(self.firmware));self.policy.FIRMWARE_CRC=crc
        executable=bytearray(64);executable[:7]=b'\x7fELF\x02\x01\x01';struct.pack_into('<HH',executable,16,3,62)
        self.tui=self.directory/'tui';self.tui.write_bytes(executable+b'new navigation')
        frontend={name:(bytes(executable)+b'old UI' if name=='psx-diagnostics-tui' else ('retained '+name).encode())
                  for name in IMAGE.FRONTEND_FILES}
        def facts(values,snapshot):
            if set(values)!=set(IMAGE.FRONTEND_FILES):raise ValueError('fixed assets differ')
            return {'schema_version':1,'total_bytes':sum(map(len,values.values())),
                    'ram_limit_bytes':16*1024**2,'maximum_payload_bytes':8*1024**2,
                    'files':[{'name':name,'bytes':len(values[name]),'sha256':sha(values[name]),
                              'mode':f'{mode:04o}','bind_target':target}
                             for name,(target,mode) in IMAGE.FRONTEND_FILES.items()]}
        self.image=SimpleNamespace(FRONTEND_FILES=IMAGE.FRONTEND_FILES,console_profile=lambda _:True,
                                   frontend_manifest=facts,executable_input=IMAGE.executable_input)
        root=b'immutable root fixture';kernel=b'immutable kernel fixture'
        self.policy.ALPHA3=dict(self.policy.ALPHA3,kernel_sha256=sha(kernel))
        self.meta=dict(copy.deepcopy(self.policy.ALPHA3),rootfs_file='psx-diagnostics/rootfs.ext4',rootfs_sha256=sha(root),
                       maximum_boot_set_bytes=1024**3,vram_mib=1024,ui_backend='ratatui',tui_sha256=sha(frontend['psx-diagnostics-tui']),
                       rootfs_tui_sha256='ab'*32,alpha_inputs={'root_firmware':self.policy.FIRMWARE},
                       required_boot_arguments=self.policy.BOOT_ARGUMENTS,frontend_overrides=facts(frontend,None),
                       frontend_revision='uvd-alpha3-extras1',alpha_extra_extensions={'provider':'preserved'})
        self.members={
            'init':(0o100755,b'unchanged init policy'),
            'bin/busybox':(0o100755,b'synthetic busybox'),
            'etc/psx-diagnostics.env':(0o100644,b'BUILD_EPOCH=1234\nKERNEL_RELEASE=fixed\n'),
            'lib/firmware/amdgpu/liverpool_uvd.bin':(0o100644,self.firmware),
            'usr/lib/psx-diagnostics/extra-helper':(0o100755,b'keep extra helper byte-for-byte'),
            'etc/psx-frontend.tsv':(0o100644,REFRESH.frontend_table(self.meta['frontend_overrides'])),
        }
        self.members.update({'psx-frontend/'+name:(0o100000|IMAGE.FRONTEND_FILES[name][1],data)for name,data in frontend.items()})
        (self.base/'bzImage').write_bytes(kernel);(self.base/'psx-diagnostics/rootfs.ext4').write_bytes(root)
        (self.base/'vram.txt').write_text('1024\n');(self.base/'bootargs.template.txt').write_text('unchanged gates and UART\n')
        self.write_base()
        parser=SimpleNamespace(parse_newc=CPIO.parse_newc,validate_busybox=mock.Mock())
        root_reader=SimpleNamespace(RootImage=lambda _:SimpleNamespace(read=lambda path:self.firmware))
        modules={'psx_frontend_stage':self.stage,'psx_frontend_image':self.image,'psx_frontend_cpio':parser,
                 'psx_frontend_alpha_uvd':self.policy,'psx_frontend_alpha_root':root_reader}
        patch=mock.patch.object(REFRESH,'load',side_effect=lambda name,path:modules[name]);patch.start();self.addCleanup(patch.stop)
        patch=mock.patch.object(REFRESH,'ALPHA_UI_ROOT_SHA256',sha(root));patch.start();self.addCleanup(patch.stop)

    def write_base(self):
        self.members['etc/psx-diagnostics-expected.json']=(0o100644,json.dumps(self.meta).encode())
        raw=b''.join(WRITER.entry(i,mode,1,1234,name,data)for i,(name,(mode,data))in enumerate(self.members.items(),1))
        raw+=WRITER.entry(len(self.members)+1,0,1,1234,'TRAILER!!!')
        packed=gzip.compress(raw,mtime=1234);(self.base/'initramfs.cpio.gz').write_bytes(packed)
        external=dict(self.meta,initramfs_sha256=sha(packed))
        (self.base/'psx-diagnostics/boot-set.json').write_text(json.dumps(external)+'\n')
        files={str(p.relative_to(self.base)):sha(p.read_bytes())for p in self.base.rglob('*')if p.is_file()and p.name!='SHA256SUMS'}
        (self.base/'SHA256SUMS').write_text(self.stage.installed_sums(files))

    def refresh(self,**kwargs):
        return REFRESH.refresh(self.base,self.tui,self.output,revision='alpha3-nav1',alpha_ui_only=True,**kwargs)

    def power_fixture(self):
        self.policy.ALPHA5 = dict(self.policy.ALPHA5, kernel_sha256=sha((self.base/'bzImage').read_bytes()))
        self.meta.update(copy.deepcopy(self.policy.ALPHA5), required_boot_arguments=self.policy.POWEROFF_BOOT_ARGUMENTS)
        patch = mock.patch.object(REFRESH, 'ALPHA_POWER_UI_ROOT_SHA256', self.meta['rootfs_sha256'])
        patch.start(); self.addCleanup(patch.stop)
        self.write_base()

    def test_power_refresh_changes_controller_tui_and_receipts_only_with_frozen_dev4_policy(self):
        self.power_fixture()
        before = {str(p.relative_to(self.base)):p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        result = REFRESH.refresh(self.base,self.tui,self.output,revision='power1',alpha_power_only=True)
        self.assertEqual(result['changed_frontend_assets'], ['controller.py','psx-diagnostics-tui'])
        old, tail = CPIO.parse_newc(gzip.decompress(before['initramfs.cpio.gz']))
        new, new_tail = CPIO.parse_newc(gzip.decompress((self.output/'initramfs.cpio.gz').read_bytes()))
        changed = {a.name.decode() for a,b in zip(old,new) if a.raw != b.raw}
        self.assertEqual(changed, {'psx-frontend/controller.py','psx-frontend/psx-diagnostics-tui',
                                  'etc/psx-frontend.tsv','etc/psx-diagnostics-expected.json'})
        self.assertEqual(tail,new_tail)
        for name in ['bzImage','psx-diagnostics/rootfs.ext4','bootargs.template.txt','vram.txt']:
            self.assertEqual((self.output/name).read_bytes(),before[name])
        original=json.loads(before['psx-diagnostics/boot-set.json'])
        current=json.loads((self.output/'psx-diagnostics/boot-set.json').read_text())
        allowed={'frontend_overrides','frontend_revision','tui_sha256','frontend_refresh','initramfs_sha256'}
        self.assertEqual({k:v for k,v in original.items() if k not in allowed},
                         {k:v for k,v in current.items() if k not in allowed})
        self.assertEqual((self.output/'initramfs.cpio.gz').read_bytes()[4:8],before['initramfs.cpio.gz'][4:8])
        self.assertTrue(current['frontend_refresh']['alpha_power_only'])
        for name,data in before.items():self.assertEqual((self.base/name).read_bytes(),data)

    def test_power_refresh_plan_is_read_only_and_rejects_old_profile_or_changed_gates(self):
        with self.assertRaises(ValueError):
            REFRESH.refresh(self.base,self.tui,self.output,alpha_power_only=True)
        self.assertFalse(self.output.exists())
        self.power_fixture()
        result=REFRESH.refresh(self.base,self.tui,self.output,plan_only=True,alpha_power_only=True)
        self.assertTrue(result['plan_only']);self.assertFalse(self.output.exists())
        self.meta['required_boot_arguments']=self.policy.BOOT_ARGUMENTS;self.write_base()
        with self.assertRaisesRegex(ValueError,'boot policy'):
            REFRESH.refresh(self.base,self.tui,self.output,alpha_power_only=True)
        self.assertFalse(self.output.exists())

    def test_power_controller_only_update_preserves_unchanged_tui_and_rejects_mixed_modes(self):
        self.power_fixture()
        self.tui.write_bytes(self.members['psx-frontend/psx-diagnostics-tui'][1])
        with self.assertRaisesRegex(ValueError,'exactly one'):
            REFRESH.refresh(self.base,self.tui,self.output,alpha_power_only=True,alpha_ui_only=True)
        result=REFRESH.refresh(self.base,self.tui,self.output,alpha_power_only=True)
        self.assertEqual(result['changed_frontend_assets'],['controller.py'])

    def test_only_tui_and_its_two_receipts_change_all_other_cpio_bytes_and_policy_remain(self):
        before={str(p.relative_to(self.base)):p.read_bytes()for p in self.base.rglob('*')if p.is_file()}
        result=self.refresh();self.assertEqual(result['status'],'PASS')
        old_entries,old_tail=CPIO.parse_newc(gzip.decompress(before['initramfs.cpio.gz']))
        new_entries,new_tail=CPIO.parse_newc(gzip.decompress((self.output/'initramfs.cpio.gz').read_bytes()))
        changed={a.name.decode()for a,b in zip(old_entries,new_entries)if a.raw!=b.raw}
        self.assertEqual(changed,{'psx-frontend/psx-diagnostics-tui','etc/psx-frontend.tsv','etc/psx-diagnostics-expected.json'})
        self.assertEqual(old_tail,new_tail)
        self.assertEqual((self.output/'initramfs.cpio.gz').read_bytes()[4:8],before['initramfs.cpio.gz'][4:8])
        for name,data in before.items():self.assertEqual((self.base/name).read_bytes(),data)
        old=json.loads(before['psx-diagnostics/boot-set.json']);new=json.loads((self.output/'psx-diagnostics/boot-set.json').read_text())
        allowed={'frontend_overrides','frontend_revision','tui_sha256','frontend_refresh','initramfs_sha256'}
        self.assertEqual({k:v for k,v in old.items()if k not in allowed},{k:v for k,v in new.items()if k not in allowed})
        self.assertTrue(new['frontend_refresh']['root_image_unchanged'])
        self.assertEqual(new['frontend_refresh']['base_boot_set_sha256'],sha(before['psx-diagnostics/boot-set.json']))
        self.assertEqual(new['frontend_refresh']['changed_frontend_assets'],['psx-diagnostics-tui'])
        self.assertEqual(result['firmware_unchanged']['sha256'],sha(self.firmware))

    def test_plan_is_read_only_and_runtime_replacements_are_refused(self):
        self.assertTrue(self.refresh(plan_only=True)['plan_only']);self.assertFalse(self.output.exists())
        with self.assertRaisesRegex(ValueError,'do not supply runtime'):
            self.refresh(snapshot=self.directory)
        self.assertFalse(self.output.exists())

    def test_other_alpha_profiles_wrong_root_and_missing_assets_fail_before_output(self):
        original=copy.deepcopy(self.meta)
        for key,value in (('profile','ps4-baikal-diagnostics-uvd-alpha2'),('kernel_release','wrong'),('rootfs_sha256','ff'*32)):
            self.meta=dict(original,**{key:value});self.write_base()
            with self.subTest(key=key),self.assertRaises(ValueError):self.refresh()
            self.assertFalse(self.output.exists())
        self.meta=original;self.members.pop('psx-frontend/network.py');self.write_base()
        with self.assertRaisesRegex(ValueError,'fixed 29'):self.refresh()
        self.assertFalse(self.output.exists())

    def test_stock_cpio_firmware_and_corrupt_manifest_are_refused(self):
        self.members['lib/firmware/amdgpu/liverpool_uvd.bin']=(0o100644,bytes(232752));self.write_base()
        with self.assertRaisesRegex(ValueError,'314168'):self.refresh()
        self.assertFalse(self.output.exists())
        self.members['lib/firmware/amdgpu/liverpool_uvd.bin']=(0o100644,self.firmware)
        self.meta['frontend_overrides']['files'][0]['sha256']='cd'*32;self.write_base()
        with self.assertRaisesRegex(ValueError,'hashes/table'):self.refresh()
        self.assertFalse(self.output.exists())

    def test_ordinary_default_still_refuses_alpha_and_same_tui_is_not_repacked(self):
        with self.assertRaisesRegex(ValueError,'alpha assembler'):
            REFRESH.refresh(self.base,self.tui,self.output)
        self.tui.write_bytes(self.members['psx-frontend/psx-diagnostics-tui'][1])
        with self.assertRaisesRegex(ValueError,'TUI is unchanged'):self.refresh()
        self.assertFalse(self.output.exists())


class FrontendRefreshTests(unittest.TestCase):
    def test_uvd_alpha_cannot_receive_ordinary_release_guard_sources(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            base = Path(temporary)
            output = base.parent / (base.name + '-unused-alpha-refresh')
            stage = SimpleNamespace(source_files=mock.Mock(return_value=({}, {
                'root_format': 'ext4-ro-loop', 'ui_backend': 'ratatui', 'uvd_runtime': True,
            })))
            image = SimpleNamespace(console_profile=lambda _: True)
            modules = {'psx_frontend_stage': stage, 'psx_frontend_image': image,
                       'psx_frontend_cpio': None}
            with mock.patch.object(REFRESH, 'load', side_effect=lambda name, _: modules[name]):
                with self.assertRaisesRegex(ValueError, 'alpha assembler'):
                    REFRESH.refresh(base, base / 'not-opened', output)
            self.assertFalse(output.exists())
            stage.source_files.assert_called_once_with(base)

    def test_old_four_asset_bundle_gains_current_network_helper_without_root_or_kernel_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            repo, base, output = directory / "repo", directory / "base", directory / "output"
            (base / "psx-diagnostics").mkdir(parents=True)
            network = b"#!/usr/bin/env python3\nNETWORK_REVISION = 'wifi-fixed-fixture'\n"
            inputs = {
                "rootfs/ps4-diagnostics-desktop/start-console": b"#!/bin/sh\ntrue\n",
                "rootfs/ps4-diagnostics-desktop/prepare-console": b"#!/usr/bin/python3\npass\n",
                "diagnostics/ps4/lab/style.css": b"body {color: olive}\n",
                "diagnostics/ps4/network.py": network,
                "diagnostics/ps4/controller.py": b"#!/usr/bin/env python3\nCONTROLLER_REVISION = 'review-fixture'\n",
                "rootfs/ps4-diagnostics-desktop/launch-graphics": b"#!/usr/bin/python3\npass\n",
                "rootfs/ps4-diagnostics-desktop/graphics-start": b"#!/bin/bash\ntrue\n",
                "rootfs/ps4-diagnostics-desktop/graphics-client": b"#!/bin/sh\ntrue\n",
                "rootfs/ps4-diagnostics-desktop/graphics-session": b"#!/bin/bash\ntrue\n",
                "rootfs/ps4-diagnostics-desktop/graphics-window": b"#!/bin/bash\ntrue\n",
                "rootfs/ps4-diagnostics-desktop/wait-graphics-wm": b"#!/usr/bin/python3\npass\n",
                "rootfs/ps4-diagnostics-desktop/20-psx-diagnostics.conf": b'Section "Device"\nEndSection\n',
                "diagnostics/ps4/preferences.py": b"#!/usr/bin/env python3\npass\n",
                "diagnostics/ps4/themes.json": b'{"schema_version":1,"themes":[]}\n',
                "diagnostics/ps4/themes-LICENSE": b"Synthetic palette license\n",
                "diagnostics/ps4/lab/index.html": b"<!doctype html><title>Lab fixture</title>\n",
                "diagnostics/ps4/lab/lab.js": b"const fixture = true;\n",
                "rootfs/ps4-diagnostics-desktop/prepare-chromium-provider": b"#!/usr/bin/python3\npass\n",
                "rootfs/ps4-diagnostics-desktop/launch-chromium-provider": b"#!/usr/bin/python3\npass\n",
                "diagnostics/ps4/ssh_access.py": b"#!/usr/bin/env python3\npass\n",
                "scripts/build-ps4-diagnostics": (ROOT / "scripts/build-ps4-diagnostics").read_bytes(),
            }
            for name, data in inputs.items():
                path = repo / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            executable = bytearray(64); executable[:7] = b"\x7fELF\x02\x01\x01"
            struct.pack_into("<HH", executable, 16, 3, 62)
            tui = directory / "tui"; tui.write_bytes(executable)
            libraries = {name: bytes(executable) + name.encode() for name in IMAGE.RUNTIME_LIBRARY_PROVIDERS}
            providers = {name: dict(provider, bytes=len(libraries[name]), sha256=sha(libraries[name]))
                         for name, provider in IMAGE.RUNTIME_LIBRARY_PROVIDERS.items()}
            file_providers = {}
            for name, provider in IMAGE.RUNTIME_FILE_PROVIDERS.items():
                data = (bytes(executable) if provider['kind'] == 'program'
                        else b'\x00\x01\x00\x00synthetic TrueType font' if provider['kind'] == 'font'
                        else b'Synthetic OFL license')
                libraries[name] = data
                file_providers[name] = dict(provider, bytes=len(data), sha256=sha(data))
            library_dir = directory / "libraries"; library_dir.mkdir()
            for name, data in libraries.items():
                (library_dir / name).write_bytes(data)
            with mock.patch.object(IMAGE, "RUNTIME_LIBRARY_PROVIDERS", providers), \
                 mock.patch.object(IMAGE, "RUNTIME_FILE_PROVIDERS", file_providers):
                provider_receipt = IMAGE.runtime_library_receipt()
            (library_dir / "provider-receipt.json").write_text(json.dumps(provider_receipt))
            root_image, kernel = b"unchanged-root-fixture", b"unchanged-kernel-fixture"
            old_frontend = {
                "psx-diagnostics-tui": bytes(executable), "start-console": b"#!/bin/sh\ntrue\n",
                "prepare-console": b"#!/usr/bin/python3\npass\n", "style.css": b"old css fixture",
            }
            old_facts = {'schema_version': 1, 'total_bytes': sum(map(len, old_frontend.values())),
                         'files': [{'name': name, 'bytes': len(data), 'sha256': sha(data),
                                    'mode': f'{IMAGE.FRONTEND_FILES[name][1]:04o}',
                                    'bind_target': IMAGE.FRONTEND_FILES[name][0]}
                                   for name, data in old_frontend.items()]}
            expected = {
                "profile": "fixture-console", "kernel_release": "fixture", "source_date_epoch": 1234,
                "ram_root_mib": 0, "ui_backend": "ratatui", "root_format": "ext4-ro-loop",
                "rootfs_file": "psx-diagnostics/rootfs.ext4", "rootfs_sha256": sha(root_image),
                "kernel_sha256": sha(kernel), "vram_mib": 1024, "hardware_acceptance": False,
                "maximum_boot_set_bytes": 16*1024**2, "tui_sha256": "a"*64,
                "snapshot_sums_sha256": IMAGE.RUNTIME_SNAPSHOT_SHA256,
                "frontend_overrides": old_facts,
            }
            busybox = b"synthetic-static-busybox"
            members = {"init": b"old fixture init", "bin/busybox": busybox,
                       "etc/psx-diagnostics-expected.json": json.dumps(expected).encode(),
                       "lib/firmware/fixture.bin": b"preserved firmware fixture"}
            members.update({"psx-frontend/" + name: data for name, data in old_frontend.items()})
            raw = b"".join(WRITER.entry(index, 0o100755, 1, 1234, name, data)
                           for index, (name, data) in enumerate(members.items(), 1))
            raw += WRITER.entry(len(members)+1, 0, 1, 1234, "TRAILER!!!")
            initial = {
                "bzImage": kernel, "psx-diagnostics/rootfs.ext4": root_image,
                "initramfs.cpio.gz": gzip.compress(raw, mtime=0), "vram.txt": b"1024\n",
                "bootargs.template.txt": b"fixture-bootargs\n",
            }
            external = dict(expected, initramfs_sha256=sha(initial["initramfs.cpio.gz"]))
            initial["psx-diagnostics/boot-set.json"] = (json.dumps(external)+"\n").encode()
            for name, data in initial.items():
                (base / name).write_bytes(data)
            (base / "SHA256SUMS").write_text("".join(f"{sha(data)}  {name}\n" for name, data in initial.items()))
            real_load = REFRESH.load
            validator = mock.Mock()

            def fixture_load(name, path):
                if name == "psx_frontend_elf":
                    return SimpleNamespace(validate_busybox=validator)
                app = real_load(name, ROOT / "scripts" / path.name)
                if name == "psx_frontend_image":
                    app.RUNTIME_LIBRARY_PROVIDERS = providers
                    app.RUNTIME_FILE_PROVIDERS = file_providers
                return app

            with mock.patch.object(REFRESH, "REPO", repo), mock.patch.object(REFRESH, "load", side_effect=fixture_load):
                result = REFRESH.refresh(base, tui, output, revision="wifi-fixture", runtime_library_dir=library_dir)
            validator.assert_called_once_with(busybox)
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["root_image_unchanged"])
            self.assertEqual((output / "bzImage").read_bytes(), kernel)
            self.assertEqual((output / "psx-diagnostics/rootfs.ext4").read_bytes(), root_image)
            for name, data in initial.items():
                self.assertEqual((base / name).read_bytes(), data)
            entries, _ = CPIO.parse_newc(gzip.decompress((output / "initramfs.cpio.gz").read_bytes()))
            found = {entry.name.decode(): entry for entry in entries}
            self.assertEqual(found["psx-frontend/network.py"].data, network)
            self.assertEqual(found["lib/firmware/fixture.bin"].data, members["lib/firmware/fixture.bin"])
            receipt = json.loads((output / "psx-diagnostics/boot-set.json").read_text())
            files = receipt["frontend_overrides"]["files"]
            self.assertEqual(len(files), 29)
            network_fact = next(item for item in files if item["name"] == "network.py")
            self.assertEqual(network_fact["sha256"], sha(network))
            self.assertEqual(network_fact["bind_target"], "/opt/psx-diagnostics/network.py")
            self.assertEqual(found["psx-frontend/controller.py"].data, inputs["diagnostics/ps4/controller.py"])
            self.assertEqual(found["psx-frontend/wait-graphics-wm"].data,
                             inputs["rootfs/ps4-diagnostics-desktop/wait-graphics-wm"])
            self.assertEqual(found["psx-frontend/xorg-diagnostics.conf"].data,
                             inputs["rootfs/ps4-diagnostics-desktop/20-psx-diagnostics.conf"])
            self.assertEqual(receipt["frontend_overrides"]["runtime_libraries"], provider_receipt)
            for name, data in libraries.items():
                self.assertEqual(found["psx-frontend/" + name].data, data)
            self.assertEqual(receipt["rootfs_sha256"], external["rootfs_sha256"])
            self.assertEqual(receipt["kernel_sha256"], external["kernel_sha256"])


if __name__ == "__main__":
    unittest.main()
