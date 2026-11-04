"""Optional provider preparation checks; no mounts, devices or Chromium execution."""
import copy
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
loader=importlib.machinery.SourceFileLoader('psx_chromium_prepare_tests',str(ROOT/'rootfs/ps4-diagnostics-desktop/prepare-chromium-provider'))
spec=importlib.util.spec_from_loader(loader.name,loader)
APP=importlib.util.module_from_spec(spec)
loader.exec_module(APP)


class ChromiumPrepareTests(unittest.TestCase):
    def setUp(self):
        self.receipt={key:APP.EXPECTED[key] for key in ('provider_id','image','image_bytes','image_sha256','provider_manifest_sha256')}
        self.receipt.update(hardware_acceptance=False,kernel_root_unchanged=True,launcher_config=APP.LAUNCH_CONFIG)
        self.boot={'kernel_release':APP.EXPECTED['kernel_release'],'rootfs_sha256':APP.EXPECTED['base_rootfs_sha256'],
                   'mesa_manifest_sha256':APP.EXPECTED['mesa_manifest_sha256'],'rootfs_file':'psx-diagnostics/rootfs.ext4'}
        self.installed={'owner':'psx-diagnostics-usb-v1','verified_readback':True,'hardware_acceptance':False,
                        'target':{'uuid':'1234-ABCD'},'files':{'psx-diagnostics/rootfs.ext4':APP.EXPECTED['base_rootfs_sha256']}}

    def test_sidecars_cannot_change_root_bound_image_provider_or_usb_pins(self):
        APP.verify_sidecars(self.receipt,APP.LAUNCH_CONFIG,self.boot,self.installed,'1234-ABCD')
        for field,value in [('image','../other.ext4'),('image_bytes',1),('image_sha256','0'*64),
                            ('provider_manifest_sha256','f'*64),('hardware_acceptance',True)]:
            changed=dict(self.receipt,**{field:value})
            with self.subTest(field=field),self.assertRaises(ValueError):
                APP.verify_sidecars(changed,APP.LAUNCH_CONFIG,self.boot,self.installed,'1234-ABCD')
        for changed in [dict(self.boot,kernel_release='different'),dict(self.boot,rootfs_sha256='0'*64),dict(self.boot,mesa_manifest_sha256='0'*64)]:
            with self.assertRaises(ValueError):APP.verify_sidecars(self.receipt,APP.LAUNCH_CONFIG,changed,self.installed,'1234-ABCD')
        with self.assertRaises(ValueError):APP.verify_sidecars(self.receipt,APP.LAUNCH_CONFIG,self.boot,self.installed,'FFFF-FFFF')

    def test_overlaid_or_writable_provider_mounts_are_refused(self):
        text='10 1 7:9 / /run/psx-chromium ro,nosuid,nodev - ext4 /dev/loop9 ro\n'
        records=APP.mount_records(text)
        self.assertEqual(APP.exact_mount(records,APP.TARGET)['source'],'/dev/loop9')
        with self.assertRaises(ValueError):APP.exact_mount(records+records,APP.TARGET)
        invalid=dict(records[0],options={'rw','nosuid','nodev'})
        with self.assertRaises(ValueError):APP.verify_mount(invalid,SimpleNamespace())

    def test_reuse_requires_exact_loop_backing_inode_readonly_and_no_offset(self):
        record=APP.mount_records('10 1 7:9 / /run/psx-chromium ro,nosuid,nodev - ext4 /dev/loop9 ro\n')[0]
        info=SimpleNamespace(st_dev=123,st_ino=456,st_size=APP.EXPECTED['image_bytes'])
        values={'backing_file':str(APP.PACKS/APP.EXPECTED['image']),'ro':'1','offset':'0','sizelimit':'0'}
        def read(path):return values[path.name]
        with mock.patch.object(APP.Path,'read_text',read),mock.patch.object(APP.Path,'stat',return_value=info):
            APP.verify_mount(record,info)
            values['offset']='4096'
            with self.assertRaises(ValueError):APP.verify_mount(record,info)
            values['offset']='0';values['backing_file']='/run/psx-media/other.ext4'
            with self.assertRaises(ValueError):APP.verify_mount(record,info)

    def test_stream_hash_is_bounded_and_uses_the_open_file(self):
        with tempfile.TemporaryFile() as stream:
            data=b'provider fixture'*1000;stream.write(data);stream.flush()
            self.assertEqual(APP.digest_fd(stream.fileno(),time.monotonic()+2),hashlib.sha256(data).hexdigest())
            with self.assertRaisesRegex(ValueError,'time limit'):APP.digest_fd(stream.fileno(),time.monotonic()-1)

    def test_bad_sidecar_is_rejected_before_mount_or_configuration_write(self):
        records=APP.mount_records('10 1 8:1 / /run/psx-media rw,nosuid,nodev,noexec - vfat /dev/sda1 rw\n'
                                  '11 1 0:9 /etc /etc rw,nosuid,nodev - tmpfs tmpfs rw\n')
        invalid=dict(self.receipt,image_sha256='0'*64)
        def read(path):
            return {str(APP.PACKS/'provider-manifest.json'):invalid,str(APP.PACKS/'chromium-provider.json'):APP.LAUNCH_CONFIG,
                    '/etc/psx-diagnostics/boot-set.json':self.boot,str(APP.MEDIA/'psx-diagnostics/installed.json'):self.installed}[str(path)]
        with mock.patch.object(APP.os,'geteuid',return_value=0),mock.patch.object(APP.sys,'argv',['prepare']), \
             mock.patch.object(APP.os,'uname',return_value=SimpleNamespace(release=APP.EXPECTED['kernel_release'])), \
             mock.patch.object(APP,'mounts',return_value=records),mock.patch.object(APP.Path,'read_text',return_value='psx.diag_uuid=1234-ABCD'), \
             mock.patch.object(APP,'read_json',side_effect=read),mock.patch.object(APP,'run') as run, \
             mock.patch.object(APP,'atomic_config') as write,self.assertRaises(ValueError):
            APP.main()
        run.assert_not_called();write.assert_not_called()

    def test_root_config_is_publicly_readable_atomic_and_only_selects_the_pinned_manifest(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as directory:
            path=Path(directory)/'chromium-provider.json'
            with mock.patch.object(APP,'CONFIG',path):APP.atomic_config()
            self.assertEqual(json.loads(path.read_text()),APP.LAUNCH_CONFIG)
            self.assertEqual(path.stat().st_mode&0o777,0o644)
            self.assertTrue(path.stat().st_mode&0o004)


if __name__=='__main__':unittest.main()
