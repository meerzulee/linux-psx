"""Host-only public-template/privacy checks with real disposable ext4 files."""
import gzip
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
LOADER=importlib.machinery.SourceFileLoader('psx_public_tests',str(ROOT/'scripts/prepare-ps4-diagnostics-public'))
SPEC=importlib.util.spec_from_loader(LOADER.name,LOADER);APP=importlib.util.module_from_spec(SPEC);LOADER.exec_module(APP)


class PublicPolicyTests(unittest.TestCase):
    def test_metadata_starts_from_public_profile_not_private_receipts(self):
        profile={'profile':'test','uvd_diagnostic':{},'kernel_sha256':'1a'*32}
        source={'rootfs_image_bytes':123,'frontend_overrides':{},'tui_sha256':'2b'*32,
                'ssh_authorized_key_fingerprint':'private-fingerprint','owner_serial':'private-serial',
                'alpha_derivation':{'private_path':'/home/private/build'}}
        result=APP.public_metadata(profile,source,template=True)
        self.assertNotIn('private-',json.dumps(result));self.assertNotIn('alpha_derivation',result)
        self.assertTrue(result['ssh_setup_required']);self.assertIsNone(result['ssh_authorized_key_fingerprint'])
        self.assertFalse(result['public_distribution']['bootable'])
        with self.assertRaises(ValueError):APP.public_metadata(dict(profile,path='/Users/private/build'),source,template=True)

    def test_local_firmware_requires_exact_bytes_and_no_symlinks_or_traversal(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            root=Path(temporary);(root/'amdgpu').mkdir();data=b'expected-local-only-firmware'
            path=root/'amdgpu/file.bin';path.write_bytes(data)
            contract={'amdgpu/file.bin':{'bytes':len(data),'sha256':APP.sha(data)}}
            self.assertEqual(APP.local_firmware(root,contract),{'amdgpu/file.bin':data})
            path.write_bytes(data+b'x')
            with self.assertRaises(ValueError):APP.local_firmware(root,contract)
            path.unlink();path.symlink_to(root/'outside')
            with self.assertRaises(ValueError):APP.local_firmware(root,contract)
            with self.assertRaises(ValueError):APP.local_firmware(root,{'../escape':contract['amdgpu/file.bin']})

    def test_new_output_rejects_existing_input_overlap_and_virtual_paths(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            root=Path(temporary);source=root/'source';source.mkdir()
            self.assertEqual(APP.new_output(root/'new',[source]),root/'new')
            for path in (source,source/'nested',root,Path('/dev/not-an-image')):
                with self.subTest(path=path),self.assertRaises(ValueError):APP.new_output(path,[source])

    def test_cpio_repack_removes_all_firmware_and_restores_exact_local_members(self):
        _,parser,_=APP.modules();old=b'old-owner-fingerprint';firmware=b'private-firmware'
        raw=b''.join([parser.entry(1,stat.S_IFREG|0o755,1,1,'init',b'#!/bin/sh\n'),
            parser.entry(2,stat.S_IFREG|0o755,1,1,'bin/busybox',b'old-busybox'),
            parser.entry(3,stat.S_IFREG|0o644,1,1,'etc/psx-diagnostics.env',b"ROOTFS_SHA256='"+b'a'*64+b"'\n"),
            parser.entry(4,stat.S_IFREG|0o644,1,1,'etc/psx-diagnostics-expected.json',old),
            parser.entry(5,stat.S_IFREG|0o644,1,1,'lib/firmware/amdgpu/file.bin',firmware),
            parser.entry(6,0,1,1,'TRAILER!!!')])
        entries,_=parser.parse_newc(raw)
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            path=Path(temporary)/'new.cpio.gz';metadata={'rootfs_sha256':'b'*64,'hardware_acceptance':False}
            APP.rewrap_cpio(entries,parser,b'new-busybox',metadata,'b'*64,{},path,1)
            data=gzip.decompress(path.read_bytes())
            self.assertNotIn(firmware,data);self.assertNotIn(old,data);self.assertNotIn(b'old-busybox',data)
            output,_=parser.parse_newc(data)
            APP.rewrap_cpio(output,parser,b'new-busybox',metadata,'b'*64,{'amdgpu/file.bin':firmware},path,1)
            output,_=parser.parse_newc(gzip.decompress(path.read_bytes()))
            by={e.name.decode():e for e in output}
            self.assertEqual(by['lib/firmware/amdgpu/file.bin'].data,firmware)
            self.assertEqual(json.loads(by['etc/psx-diagnostics-expected.json'].data),metadata)
            for name,major,minor in (('dev/console',5,1),('dev/null',1,3)):
                self.assertTrue(stat.S_ISCHR(by[name].mode))
                self.assertEqual((int(by[name].header[78:86],16),int(by[name].header[86:94],16)),(major,minor))


@unittest.skipUnless(all(shutil.which(x) for x in ('fakeroot','mkfs.ext4','debugfs','e2fsck')),'Native e2fsprogs/fakeroot required')
class FreshPublicImageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='psx-public-test-',dir='/var/tmp');self.addCleanup(self.temp.cleanup)
        self.work=Path(self.temp.name);tree=self.work/'input';tree.mkdir()
        self.licenses=self.work/'licenses';self.licenses.mkdir()
        for name in ('BusyBox-GPL-2.0.txt','musl-COPYRIGHT.txt'):(self.licenses/name).write_text('fixture license\n')
        files={'etc/psx-diagnostics/authorized_keys':b'OWNER-KEY-UNIQUE-PRIVATE-123456',
            'etc/machine-id':b'private-machine-id','etc/hostname':b'private-host',
            'etc/shadow':b'root:!oldhash:0::::::\npsx:!other:0::::::\n',
            'etc/ssh/ssh_host_ed25519_key':b'PRIVATE-HOSTKEY-UNIQUE-123456',
            'etc/NetworkManager/system-connections/psx-wired.nmconnection':b'public-wired-policy',
            'etc/NetworkManager/system-connections/private-wifi':b'PRIVATE-WIFI-UNIQUE-123456',
            'usr/lib/firmware/amdgpu/file.bin':b'PRIVATE-FIRMWARE-UNIQUE-123456',
            'root/old-log':b'PRIVATE-HISTORY-UNIQUE-123456','home/psx/cache':b'PRIVATE-PROFILE-123456',
            'usr/bin/kept':b'preserved-executable','unused':b'FREED-BLOCK-SECRET-UNIQUE-123456'}
        fixture=tree/'usr/share/psx-diagnostics/uvd';fixture.mkdir(parents=True)
        asset=b'unchanged-pixel-oracle';(fixture/'clip.h264').write_bytes(asset);(fixture/'frame.nv12').write_bytes(asset)
        (fixture/'reference.txt').write_bytes(asset);(fixture/'evidence.txt').write_text('/Users/private/build/clip.h264\n')
        def record(name):return {'path':name,'bytes':(fixture/name).stat().st_size,'sha256':APP.digest(fixture/name)}
        manifest={'schema':1,'clip':record('clip.h264'),'frames':[record('frame.nv12')],
            'reference':record('reference.txt'),'evidence':[record('evidence.txt')],
            'recipe':{'argv':['ffmpeg','/Users/private/build/clip.h264']}}
        (fixture/'FIXTURE.json').write_bytes(APP.encoded(manifest));oldpin=APP.digest(fixture/'FIXTURE.json')
        files['opt/psx-diagnostics/uvd.py']=("DIAGNOSTIC={'fixture_sha256':'"+oldpin+"'}\n").encode()
        for name,data in files.items():p=tree/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        (tree/'usr/bin/kept').chmod(0o755)
        (tree/'etc/systemd/system/multi-user.target.wants').mkdir(parents=True)
        (tree/'etc/systemd/system/multi-user.target.wants/sshd.service').symlink_to('/usr/lib/systemd/system/sshd.service')
        self.source=self.work/'source.ext4'
        with self.source.open('xb') as stream:stream.truncate(16*1024**2)
        subprocess.run(['mkfs.ext4','-q','-F','-b','1024','-O','^has_journal','-d',str(tree),str(self.source)],check=True,capture_output=True)
        subprocess.run(['debugfs','-w','-R','rm /unused',str(self.source)],check=True,capture_output=True)
        self.source_sha=APP.digest(self.source)
        self.metadata={'uvd_diagnostic':{'fixture_sha256':oldpin},'hardware_acceptance':False}

    def test_fresh_ext4_removes_live_and_previously_freed_private_bytes_and_preserves_oracles(self):
        output=self.work/'public.ext4'
        result=APP.rebuild(self.source,output,self.metadata,self.licenses,template=True)
        raw=output.read_bytes()
        for secret in (b'OWNER-KEY-UNIQUE',b'PRIVATE-HOSTKEY-UNIQUE',b'PRIVATE-WIFI-UNIQUE',
                       b'PRIVATE-FIRMWARE-UNIQUE',b'PRIVATE-HISTORY-UNIQUE',b'FREED-BLOCK-SECRET-UNIQUE',b'/Users/private/'):
            with self.subTest(secret=secret):self.assertNotIn(secret,raw)
        alpha,_,_=APP.modules();reader=alpha.RootImage(output)
        self.assertEqual(reader.read('/etc/psx-diagnostics/authorized_keys'),b'')
        self.assertEqual(reader.read('/usr/bin/kept'),b'preserved-executable')
        self.assertEqual(reader.entry('/usr/bin/kept')['mode'],stat.S_IFREG|0o755)
        self.assertEqual(reader.read('/usr/share/psx-diagnostics/uvd/clip.h264'),b'unchanged-pixel-oracle')
        self.assertNotEqual(result['uvd_diagnostic']['fixture_sha256'],self.metadata['uvd_diagnostic']['fixture_sha256'])
        self.assertEqual(APP.digest(self.source),self.source_sha)
        self.assertNotEqual(self.source.stat().st_ino,output.stat().st_ino)

    def test_finalization_restores_only_pinned_firmware_without_owner_access(self):
        public=self.work/'public.ext4';metadata=APP.rebuild(self.source,public,self.metadata,self.licenses,template=True)
        firmware=self.work/'firmware';(firmware/'amdgpu').mkdir(parents=True);data=b'local-firmware-exact'
        (firmware/'amdgpu/file.bin').write_bytes(data)
        pins={'amdgpu/file.bin':{'bytes':len(data),'sha256':APP.sha(data)}}
        output=self.work/'final.ext4'
        APP.rebuild(public,output,metadata,self.licenses,template=False,firmware_dir=firmware,firmware_pins=pins)
        alpha,_,_=APP.modules();reader=alpha.RootImage(output)
        self.assertEqual(reader.read('/usr/lib/firmware/amdgpu/file.bin'),data)
        self.assertEqual(reader.read('/etc/psx-diagnostics/authorized_keys'),b'')
        self.assertEqual(APP.digest(self.source),self.source_sha)


if __name__=='__main__':unittest.main()
