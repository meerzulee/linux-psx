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


class EmbeddedProviderTests(unittest.TestCase):
    PAYLOAD=b'ext4 image bytes'*4096

    def pin(self,**changes):
        import hashlib as h
        return dict({'image_sha256':h.sha256(self.PAYLOAD).hexdigest(),'image_bytes':len(self.PAYLOAD)},**changes)

    def expand(self,raw,pin,deadline=None):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            source=Path(temporary)/'pack';source.write_bytes(raw)
            descriptor=os.open(source,os.O_RDONLY)
            try:
                target=Path(temporary)/'image'
                APP.expand(descriptor,target,pin,time.monotonic()+30 if deadline is None else deadline)
                return target.read_bytes()
            finally:os.close(descriptor)

    def test_a_good_image_is_written_out(self):
        self.assertEqual(self.expand(APP.lzma.compress(self.PAYLOAD),self.pin()),self.PAYLOAD)

    def test_every_bad_image_is_refused(self):
        raw=APP.lzma.compress(self.PAYLOAD)
        for name,data,pin,deadline in (('hash',raw,self.pin(image_sha256='0'*64),None),
                                       ('short',raw,self.pin(image_bytes=len(self.PAYLOAD)+1),None),
                                       ('oversize',raw,self.pin(image_bytes=len(self.PAYLOAD)-1),None),
                                       ('truncated',raw[:len(raw)//2],self.pin(),None),
                                       ('late',raw,self.pin(),0)):
            with self.subTest(name=name),self.assertRaises((ValueError,APP.lzma.LZMAError)):
                self.expand(data,pin,deadline)

    def test_the_pin_row_is_read_strictly(self):
        good=f"{'a'*64}\t12\t{'b'*64}\t34\t{'c'*64}\n".encode()
        with mock.patch.object(APP,'read_file',return_value=good):
            self.assertEqual(APP.embedded_pin(),{'pack_sha256':'a'*64,'pack_bytes':12,'image_sha256':'b'*64,'image_bytes':34,'manifest_sha256':'c'*64})
        for bad in (b'',b'nonsense\n',good.replace(b'12',b'x2'),good.replace(b'a'*64,b'a'*63),good[:-1]+b'\tmore\n'):
            with self.subTest(bad=bad),mock.patch.object(APP,'read_file',return_value=bad),self.assertRaises(ValueError):
                APP.embedded_pin()

    def test_an_image_with_a_pin_never_looks_for_a_usb_stick(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            pin=Path(temporary)/'pin.tsv';pin.write_text('x')
            with mock.patch.object(APP,'PIN_FILE',pin),mock.patch.object(APP,'prepare_embedded') as embedded,\
                 mock.patch.object(APP,'mounts',side_effect=AssertionError('USB path')),\
                 mock.patch.object(APP.os,'geteuid',return_value=0),mock.patch.object(APP.sys,'argv',['prepare']):
                APP.main()
            embedded.assert_called_once_with()

    def embedded_run(self,mounted,existing=None,provider_mesa='m'*64):
        raw=json.dumps({'kernel_release':os.uname().release,'base_rootfs_sha256':'r'*64,'mesa_manifest_sha256':provider_mesa,
                        'hardware_acceptance':False,'provider_id':'p'}).encode()
        boot={'rootfs_sha256':'r'*64,'mesa_manifest_sha256':'m'*64}
        pin={'manifest_sha256':hashlib.sha256(raw).hexdigest(),'image_sha256':'a'*64,'image_bytes':7}
        self.launch={'schema_version':1,'provider_manifest_sha256':pin['manifest_sha256'],'mesa_manifest_sha256':'m'*64}
        self.written=mock.Mock()
        target=mock.MagicMock();target.stat.return_value=SimpleNamespace(st_uid=0);target.iterdir.return_value=[]
        config=mock.MagicMock();config.exists.return_value=True
        info=SimpleNamespace(st_mode=0o100600,st_uid=0,st_size=7)
        found=lambda records,path:{'target':'x'} if path is APP.IMAGE_DIR or (mounted and path is target) else None
        loaded=lambda path:boot if path is APP.BOOT_SET else self.launch if existing=='current' else existing
        with mock.patch.object(APP,'embedded_pin',return_value=pin),mock.patch.object(APP,'read_json',side_effect=loaded),\
             mock.patch.object(APP,'mounts',return_value=[]),mock.patch.object(APP,'exact_mount',side_effect=found),\
             mock.patch.object(APP,'inflate'),mock.patch.object(APP,'safe_path'),mock.patch.object(APP,'verify_mount'),\
             mock.patch.object(APP,'run'),mock.patch.object(APP,'read_file',return_value=raw),\
             mock.patch.object(APP,'atomic_config',self.written),mock.patch.object(APP,'TARGET',target),\
             mock.patch.object(APP,'CONFIG',config),mock.patch.object(APP.os,'open',return_value=99),\
             mock.patch.object(APP.os,'close'),mock.patch.object(APP.os,'fstat',return_value=info),\
             mock.patch.object(APP,'print',create=True):
            APP.prepare_embedded()

    def test_root_hands_the_launcher_the_mesa_pin_it_checked(self):
        self.embedded_run(False)
        self.written.assert_called_once_with(self.launch)
        self.assertEqual(self.launch['mesa_manifest_sha256'],'m'*64)

    def test_a_provider_with_another_mesa_writes_no_configuration(self):
        with self.assertRaises(ValueError):self.embedded_run(False,provider_mesa='x'*64)
        self.written.assert_not_called()

    def test_a_reused_mount_needs_the_configuration_written_for_it(self):
        self.embedded_run(True,existing='current')
        self.written.assert_not_called()
        with self.assertRaises(ValueError):
            self.embedded_run(True,existing={'schema_version':1,'provider_manifest_sha256':'c'*64})


if __name__=='__main__':unittest.main()
