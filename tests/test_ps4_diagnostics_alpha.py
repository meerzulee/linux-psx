"""Host-only alpha adapter guards and real rootless ext4 readback; no hardware."""
import copy
import gzip
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader('psx_alpha_tests', str(ROOT / 'scripts/build-ps4-diagnostics-alpha'))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
APP = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(APP)


class AlphaPolicyTests(unittest.TestCase):
    def test_actual_cpio_readback_rejects_stock_missing_or_symlink_firmware(self):
        parser=APP.load('alpha3_cpio_fixture',ROOT/'scripts/ps4-diagnostics-archive')
        expected=b'synthetic corrected payload'
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temp:
            archive=Path(temp)/'initramfs.gz'
            for payload,mode,good in ((expected,stat.S_IFREG|0o644,True),
                                      (b'old stock payload',stat.S_IFREG|0o644,False),
                                      (expected,stat.S_IFLNK|0o777,False)):
                data=parser.entry(1,mode,1,0,APP.UVD_FIRMWARE_MEMBER,payload)+parser.entry(2,0,1,0,'TRAILER!!!')
                archive.write_bytes(gzip.compress(data))
                if good:
                    self.assertEqual(APP.verify_initramfs_firmware(archive,parser,expected)['sha256'],APP.sha(expected))
                else:
                    with self.assertRaisesRegex(ValueError,'firmware readback'):
                        APP.verify_initramfs_firmware(archive,parser,expected)
            archive.write_bytes(gzip.compress(parser.entry(1,0,1,0,'TRAILER!!!')))
            with self.assertRaisesRegex(ValueError,'firmware readback'):
                APP.verify_initramfs_firmware(archive,parser,expected)

    def test_alpha3_has_distinct_stock_anchor_and_target_firmware_and_preserves_kernel(self):
        profile=json.loads((ROOT/'manifests/ps4-diagnostics-uvd-alpha3.json').read_text())
        self.assertEqual(APP.validate_profile(profile)['image_version'],'alpha3')
        previous=json.loads((ROOT/'manifests/ps4-diagnostics-uvd-alpha2.json').read_text())
        for key in ('kernel_release','kernel_sha256','config_sha256','modules_sha256','native3_baseline'):
            self.assertEqual(profile[key],previous[key])
        for key in ('root_firmware','base_root_firmware'):
            changed=copy.deepcopy(profile)
            changed['alpha_inputs'][key]=APP.BASE_FIRMWARE if key=='root_firmware' else APP.UVD_FIRMWARE
            with self.assertRaisesRegex(ValueError,'distinct stock-base'):
                APP.validate_profile(changed)

    def test_stock_firmware_is_rejected_before_any_other_input_or_output(self):
        profile=json.loads((ROOT/'manifests/ps4-diagnostics-uvd-alpha3.json').read_text())
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temp:
            root=Path(temp); manifest=root/'profile.json'; manifest.write_bytes(APP.encoded(profile))
            stock=root/'stock.bin';stock.write_bytes(bytes(232752))
            with mock.patch.object(APP,'validate_paths',side_effect=AssertionError('must reject before image inputs')):
                with self.assertRaises(ValueError):
                    APP.prepare(root/'base',root/'package',root/'fixture',manifest,root/'output',uvd_firmware=stock)
            self.assertFalse((root/'output').exists())

    def test_explicit_external_firmware_is_required(self):
        profile=json.loads((ROOT/'manifests/ps4-diagnostics-uvd-alpha3.json').read_text())
        with self.assertRaisesRegex(ValueError,'--uvd-firmware'):
            APP.prepare_firmware(profile,None)

    def profile(self, version=1):
        value=json.loads((ROOT/'manifests/ps4-diagnostics-uvd-alpha1.json').read_text())
        if version==2:
            old=value['kernel_release'];variant=APP.VARIANTS['ps4-baikal-diagnostics-uvd-alpha2']
            value.update(profile='ps4-baikal-diagnostics-uvd-alpha2',kernel_release=variant['kernel_release'],
                         image_version='alpha2',product_name='Diagnostix',build_status='frozen',
                         poweroff_fix={'patch':APP.POWEROFF_PATCH,'sha256':APP.POWEROFF_SHA256,
                                       'uvd_shutdown_park_unchanged':True,'hardware_acceptance':False})
            value['alpha_inputs']['legacy_package']={name.replace(old,variant['kernel_release']).replace('LINK-ATTEMPT-06','LINK-ATTEMPT-01'):pin
                                                    for name,pin in value['alpha_inputs']['legacy_package'].items()}
        return value

    def test_known_variants_keep_distinct_release_and_build_receipt_contracts(self):
        first=self.profile();second=self.profile(2)
        self.assertEqual(APP.validate_profile(first)['kernel_release'],APP.ALPHA_RELEASE)
        selected=APP.validate_profile(second)
        self.assertEqual(selected['kernel_release'],'7.2.11-psx-ps4-baikal-uvd-runtime-dev2')
        self.assertTrue(selected['link_receipt'].endswith('LINK-ATTEMPT-01.json'))
        for bad in [dict(second,kernel_release=first['kernel_release']),dict(first,kernel_release=second['kernel_release']),
                    dict(second,profile='ps4-baikal-diagnostics-uvd-alpha3'),dict(second,image_version='alpha1')]:
            with self.assertRaisesRegex(ValueError,'mismatched'):
                APP.validate_profile(bad)
        bad=copy.deepcopy(second);pins=bad['alpha_inputs']['legacy_package']
        pins['build-inputs/runtime/LINK-ATTEMPT-06.json']=pins.pop(selected['link_receipt'])
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            APP.validate_profile(bad)

    def test_pending_alpha2_and_placeholder_pins_stop_before_input_tools_or_output(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp')as temp:
            root=Path(temp);profile=self.profile(2);profile['build_status']='pending-new-kernel'
            profile['kernel_sha256']=None;path=root/'pending.json';path.write_bytes(APP.encoded(profile))
            with mock.patch.object(APP,'validate_paths',side_effect=AssertionError('must not inspect inputs')),mock.patch.object(APP,'load',side_effect=AssertionError('must not load build tools')):
                with self.assertRaisesRegex(ValueError,'pending'):
                    APP.prepare(root/'missing-base',root/'missing-package',root/'missing-fixture',path,root/'output')
            self.assertFalse((root/'output').exists())
        for value in (None,'pending','',0,'0'*64,'f'*64,'a'*64):
            profile=self.profile(2);profile['kernel_sha256']=value
            with self.assertRaisesRegex(ValueError,'placeholder'):
                APP.validate_profile(profile)
        for field in ('bzImage','build-inputs/runtime/LINK-ATTEMPT-01.json'):
            profile=self.profile(2);profile['alpha_inputs']['legacy_package'][field]['sha256']=None
            with self.assertRaisesRegex(ValueError,'placeholder'):
                APP.validate_profile(profile)
        profile=self.profile(2);profile['alpha_inputs']['legacy_package']['bzImage']['bytes']=0
        with self.assertRaisesRegex(ValueError,'Pending or invalid'):
            APP.validate_profile(profile)

    def test_alpha2_requires_exact_base_and_reviewed_patch_without_lifecycle_relaxation(self):
        for changes in ({'uvd_shutdown_park_unchanged':False},{'hardware_acceptance':True},{'sha256':'1f'*32},
                        {'patch':'unrelated.patch'}):
            profile=self.profile(2);profile['poweroff_fix'].update(changes)
            with self.assertRaisesRegex(ValueError,'ICC patch'):
                APP.validate_profile(profile)
        profile=self.profile(2);profile['alpha_base'].pop('rootfs_sha256')
        with self.assertRaisesRegex(ValueError,'exact ordinary'):
            APP.validate_profile(profile)

    def test_alpha1_branding_is_unchanged_and_alpha2_changes_display_names_only(self):
        expected=(b'NAME="psX Diagnostics"\nID=psx-diagnostics\nID_LIKE=arch\n'
                  b'PRETTY_NAME="psX Diagnostics UVD alpha1"\nIMAGE_ID=ps4-baikal-diagnostics\nIMAGE_VERSION=alpha1\n')
        self.assertEqual(APP.os_release(self.profile()),expected)
        second=APP.os_release(self.profile(2)).decode()
        self.assertIn('NAME="Diagnostix"\n',second)
        self.assertIn('PRETTY_NAME="Diagnostix alpha2"\n',second)
        self.assertIn('ID=psx-diagnostics\n',second);self.assertIn('IMAGE_ID=ps4-baikal-diagnostics\n',second)
        profile=self.profile();profile['product_name']='bad"\nID=changed'
        with self.assertRaisesRegex(ValueError,'plain product_name'):
            APP.os_release(profile)

    def test_only_exact_release_constant_is_adapted(self):
        data = ('#!/usr/bin/python3\nRELEASE = "' + APP.BASE_RELEASE + '"\nassert observed == RELEASE\n').encode()
        updated = APP.rewrite_release(data)
        self.assertIn(APP.ALPHA_RELEASE.encode(), updated)
        self.assertIn(b'assert observed == RELEASE', updated)
        self.assertEqual(updated.replace(APP.ALPHA_RELEASE.encode(), APP.BASE_RELEASE.encode()), data)
        for invalid in (b'RELEASE = "other"', data+data, data+APP.BASE_RELEASE.encode()):
            with self.assertRaises(ValueError): APP.rewrite_release(invalid)
        for variant in APP.VARIANTS.values():
            release=variant['kernel_release'];updated=APP.rewrite_release(data,release)
            self.assertEqual(updated.replace(release.encode(),APP.BASE_RELEASE.encode()),data)
        with self.assertRaisesRegex(ValueError,'Unknown'):
            APP.rewrite_release(data,'unfrozen-release')

    def test_catalogue_changes_only_release_and_existing_uvd_entry(self):
        original = json.loads((ROOT/'diagnostics/ps4/catalogue.json').read_text())
        changed = json.loads(APP.alpha_catalogue(APP.encoded(original)))
        self.assertEqual(changed['expected_kernel_release'], APP.ALPHA_RELEASE)
        for old, new in zip(original['tests'], changed['tests']):
            if old['id'] == 'uvd.avc':
                self.assertEqual(new['kind'], 'functional')
                self.assertEqual(new['version'], 2)
                self.assertEqual(new['timeout_seconds'], 120)
            else: self.assertEqual(new, old)
        self.assertEqual(original['expected_kernel_release'], APP.BASE_RELEASE)
        with self.assertRaises(ValueError): APP.alpha_catalogue(APP.encoded(changed))
        second=APP.alpha_catalogue(APP.encoded(original),self.profile(2)['kernel_release'])
        self.assertEqual(second.replace(self.profile(2)['kernel_release'].encode(),APP.ALPHA_RELEASE.encode()),APP.encoded(changed))

    def test_bootargs_preserve_disable_and_add_exact_gates_once(self):
        old=b'console=tty0 libata.force=1.00:disable psx_mts_net.autoarm=1 psx.diag_uuid=@USB_UUID@\n'
        changed=APP.alpha_bootargs(old)
        for argument in APP.UVD_ARGS:
            self.assertEqual(changed.decode().split().count(argument), 1)
        self.assertTrue(changed.startswith(old.rstrip()))
        for bad in (changed, old.replace(b'autoarm=1', b'autoarm=0'), old+old):
            with self.assertRaises(ValueError): APP.alpha_bootargs(bad)

    def test_paths_reject_existing_output_input_overlap_symlinks_and_virtual_devices(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temp:
            root=Path(temp); inputs=[root/x for x in ('base','package','fixture')]
            for p in inputs:p.mkdir()
            valid=APP.validate_paths(*inputs,root/'new')
            self.assertEqual(valid[-1],root/'new')
            self.assertFalse((root/'new').exists())
            (root/'linked').symlink_to(inputs[0],target_is_directory=True)
            for output in [inputs[0],inputs[0]/'nested',root/'linked',Path('/dev/psx-alpha-test')]:
                with self.assertRaises(ValueError):APP.validate_paths(*inputs,output)
            with self.assertRaises(ValueError):APP.validate_paths(root/'linked',*inputs[1:],root/'new')
            for path in ['/etc/../passwd','/etc/a b','/etc/a\nrm /etc/passwd','relative','/']:
                with self.assertRaises(ValueError):APP.image_path(path)

    def test_plan_returns_without_writing_image_or_output(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temp:
            root=Path(temp);(root/'base').mkdir();(root/'base/root').write_bytes(b'fixture')
            prepared={'profile':{'profile':'fixture','kernel_release':APP.ALPHA_RELEASE,'module_count':41},'base':root/'base','output':root/'new',
                'original':{'rootfs_sha256':'a'*64,'rootfs_file':'root'},'frontend':{'one':b'x'},'replacements':{},'modules':{},
                'metadata':{'frontend_overrides':{'total_bytes':1},'alpha_derivation':{'fixture_files':135,'fixture_bytes':123}}}
            with mock.patch.object(APP,'copy_regular',side_effect=AssertionError('unexpected write')),mock.patch.object(APP,'RootImage',side_effect=AssertionError('unexpected image write')):
                result=APP.build(prepared,plan_only=True)
            self.assertTrue(result['plan_only']);self.assertFalse(prepared['output'].exists())


@unittest.skipUnless(all(shutil.which(x) for x in ('mkfs.ext4','debugfs','e2fsck','zstd')), 'native e2fsprogs/zstd unavailable')
class AlphaImageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='psx-alpha-test-',dir='/var/tmp');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);stage=self.root/'stage';stage.mkdir()
        files={'usr/lib/modules/old/kernel/example.ko':b'old-module','usr/lib/modules/old/modules.dep':b'old-index',
               'opt/tools/existing.py':b'original','etc/config':b'preserved','opt/driver/real.so':b'pinned-library'}
        for name,data in files.items():
            target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        (stage/'opt/driver/selected.so').symlink_to('real.so')
        (stage/'opt/linked').symlink_to('/etc')
        self.base=self.root/'base.ext4'
        with self.base.open('xb')as f:f.truncate(16*1024**2)
        subprocess.run(['mkfs.ext4','-q','-F','-b','1024','-O','^has_journal','-d',str(stage),str(self.base)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.before=APP.digest(self.base)

    def test_new_copy_whole_tree_replace_metadata_hashes_and_base_preservation(self):
        before=APP.fsck(self.base);self.assertEqual(before['returncode'],0)
        out=self.root/'alpha.ext4';APP.copy_regular(self.base,out)
        image=APP.RootImage(out,writable=True,immutable_base=self.base)
        changes=image.apply({'/usr/lib/modules/new/kernel/example.ko':(b'new-module',0o644),
            '/usr/lib/modules/new/modules.dep':(b'new-index',0o644),'/usr/lib/modules/new/modules.softdep':(b'',0o644),'/opt/tools/existing.py':(b'changed',0o755),
            '/opt/tools/qualification.json':(b'{"schema":1}\n',0o644)},remove_tree='/usr/lib/modules/old')
        self.assertEqual(set(image.entries('/usr/lib/modules')),{'new'})
        self.assertEqual(image.read('/etc/config'),b'preserved')
        self.assertEqual(image.read('/usr/lib/modules/new/modules.softdep'),b'')
        self.assertEqual(image.read('/opt/driver/selected.so',resolve=True),b'pinned-library')
        self.assertEqual(image.entry('/opt/tools/existing.py')['mode'],stat.S_IFREG|0o755)
        self.assertEqual(image.entry('/opt/tools/existing.py')['uid'],0)
        self.assertEqual(image.entry('/opt/tools/existing.py')['gid'],0)
        self.assertEqual(next(x for x in changes if x['path']=='/opt/tools/existing.py')['before_sha256'],hashlib.sha256(b'original').hexdigest())
        self.assertEqual(APP.fsck(out)['returncode'],0)
        self.assertEqual(APP.digest(self.base),self.before)
        self.assertNotEqual(out.stat().st_ino,self.base.stat().st_ino)

    def test_reader_and_writer_reject_original_hardlink_symlink_targets(self):
        with self.assertRaises(ValueError):APP.RootImage(self.base,writable=True,immutable_base=self.base)
        hard=self.root/'hard.ext4';os.link(self.base,hard)
        with self.assertRaises(ValueError):APP.RootImage(hard,writable=True,immutable_base=self.base)
        hard.unlink()
        link=self.root/'link.ext4';link.symlink_to(self.base)
        with self.assertRaises(ValueError):APP.RootImage(link)
        reader=APP.RootImage(self.base)
        with self.assertRaises(ValueError):reader.apply({'/etc/config':(b'bad',0o644)})
        out=self.root/'alpha.ext4';APP.copy_regular(self.base,out);editor=APP.RootImage(out,writable=True,immutable_base=self.base)
        for target in ['/opt/linked/escape','/opt/driver/selected.so']:
            with self.assertRaises(ValueError):editor.apply({target:(b'bad',0o644)})
        self.assertEqual(APP.digest(out),self.before)

    def test_debugfs_error_is_not_mistaken_for_successful_exit_status(self):
        out=self.root/'alpha.ext4';APP.copy_regular(self.base,out);editor=APP.RootImage(out,writable=True,immutable_base=self.base)
        with self.assertRaisesRegex(ValueError,'debugfs rejected'):
            editor.debug('mkdir /missing/child',write=True)
        with self.assertRaisesRegex(ValueError,'module tree'):
            APP.verify_tree(editor,'/usr/lib/modules/old',{'/usr/lib/modules/old/kernel/example.ko':b'old-module'})

    def test_complete_offline_derivative_has_consistent_receipts_and_no_old_module_payload(self):
        base=self.root/'boot-base';(base/'psx-diagnostics').mkdir(parents=True)
        APP.copy_regular(self.base,base/'psx-diagnostics/rootfs.ext4')
        initial={'bzImage':b'old-kernel','initramfs.cpio.gz':b'old-init','vram.txt':b'1024\n',
            'bootargs.template.txt':b'fixture-bootargs\n','psx-diagnostics/modules-old.tar.zst':b'old-archive'}
        for name,data in initial.items():(base/name).write_bytes(data)
        original={'profile':'fixture-ordinary','kernel_release':'old','kernel_sha256':APP.sha(initial['bzImage']),
            'initramfs_sha256':APP.sha(initial['initramfs.cpio.gz']),'rootfs_sha256':self.before,
            'rootfs_file':'psx-diagnostics/rootfs.ext4','root_format':'ext4-ro-loop','vram_mib':1024,
            'maximum_boot_set_bytes':APP.CAP,'hardware_acceptance':False}
        (base/'psx-diagnostics/boot-set.json').write_bytes(APP.encoded(original))
        stage=APP.load('alpha_complete_stage',ROOT/'scripts/stage-ps4-diagnostics-usb')
        sums={str(p.relative_to(base)):APP.digest(p)for p in base.rglob('*')if p.is_file()}
        (base/'SHA256SUMS').write_text(stage.installed_sums(sums));files,_=stage.source_files(base)
        package=self.root/'package';package.mkdir()
        for name,data in {'bzImage':b'new-kernel','config':b'new-config','modules-new.tar.zst':b'new-archive',
                          'source-new.tar.zst':b'corresponding-source','legacy.json':b'{"legacy":true}\n'}.items():
            (package/name).write_bytes(data)
        pins={p.name:{'sha256':APP.digest(p),'bytes':p.stat().st_size}for p in package.iterdir()}
        profile={'profile':'fixture-alpha','kernel_release':'new','image_version':'alpha1','module_count':1,'source_commit':'a'*40,'kernel_sha256':pins['bzImage']['sha256'],
            'config_sha256':pins['config']['sha256'],'modules_sha256':pins['modules-new.tar.zst']['sha256'],
            'alpha_inputs':{'legacy_package':dict(pins,**{'build-inputs/runtime/SOURCE-RECEIPT.json':pins['legacy.json']})}}
        (package/'build-inputs/runtime').mkdir(parents=True);shutil.copyfile(package/'legacy.json',package/'build-inputs/runtime/SOURCE-RECEIPT.json')
        profile_path=self.root/'profile.json';profile_path.write_bytes(APP.encoded(profile))
        metadata=dict(original,profile='fixture-alpha',kernel_release='new',kernel_sha256=pins['bzImage']['sha256'],
            frontend_overrides={'total_bytes':1},alpha_derivation={'profile_sha256':APP.digest(profile_path),'fixture_files':0,'fixture_bytes':0})
        metadata.pop('initramfs_sha256');metadata.pop('rootfs_sha256')
        modules={'/usr/lib/modules/new/kernel/example.ko':b'new-module','/usr/lib/modules/new/modules.dep':b'new-index'}
        seed={'bin/busybox':type('Entry',(),{'data':b'static-fixture','mode':stat.S_IFREG|0o755})()}
        def initramfs(profile,seed,busybox,firmware,rootsha,destination,epoch,expected,frontend):
            self.assertEqual(expected['rootfs_sha256'],rootsha)
            destination.write_bytes(b'synthetic-init-contract\n'+APP.encoded(expected))
        p=dict(profile=profile,base=base,package=package,output=self.root/'alpha',original=original,files=files,
            replacements={path:(data,0o644)for path,data in modules.items()},metadata=metadata,modules=modules,
            frontend={'one':b'x'},old_tree={},before_fsck=APP.fsck(self.base),anchors={},source_facts={},profile_path=profile_path,
            archive_name='modules-new.tar.zst',source_name='source-new.tar.zst',imported={'origin':'synthetic'},bootargs=b'alpha fixture\n',
            seed=seed,stage=stage,image=type('Builder',(),{'initramfs':staticmethod(initramfs)})(),
            variant={'source_receipt':'build-inputs/runtime/SOURCE-RECEIPT.json'})
        with mock.patch.object(APP,'BASE_RELEASE','old'):
            result=APP.build(p)
        self.assertEqual(result['status'],'PASS');self.assertFalse(result['hardware_acceptance'])
        self.assertFalse((p['output']/'psx-diagnostics/modules-old.tar.zst').exists())
        self.assertEqual((p['output']/'psx-diagnostics/modules-new.tar.zst').read_bytes(),b'new-archive')
        self.assertEqual((p['output']/'psx-diagnostics/version.txt').read_bytes(),b'new\n')
        _,final=stage.source_files(p['output'])
        root=APP.RootImage(p['output']/final['rootfs_file'])
        inner=json.loads(root.read('/etc/psx-diagnostics/boot-set.json'))
        self.assertNotIn('rootfs_sha256',inner);self.assertNotIn('initramfs_sha256',inner)
        self.assertEqual(inner['kernel_release'],'new');self.assertEqual(root.read('/etc/config'),b'preserved')
        self.assertEqual(APP.digest(base/'psx-diagnostics/rootfs.ext4'),self.before)
        self.assertLessEqual(result['boot_set_bytes']+APP.MIB,APP.CAP)

    def archive(self,members):
        raw=io.BytesIO()
        with tarfile.open(fileobj=raw,mode='w')as tar:
            for name,data,kind in members:
                item=tarfile.TarInfo(name);item.type=kind;item.size=len(data)if kind==tarfile.REGTYPE else 0
                if kind==tarfile.SYMTYPE:item.linkname='/etc/passwd'
                tar.addfile(item,io.BytesIO(data)if kind==tarfile.REGTYPE else None)
        path=self.root/'modules.tar.zst'
        path.write_bytes(subprocess.run(['zstd','-q','-c'],input=raw.getvalue(),stdout=subprocess.PIPE,check=True).stdout)
        return path

    def test_module_archive_requires_whole_metadata_tree_and_rejects_links_or_traversal(self):
        prefix='lib/modules/fixture/'
        members=[(prefix+'kernel/one.ko',b'elf-fixture',tarfile.REGTYPE)]
        members += [(prefix+name,b'index',tarfile.REGTYPE)for name in ['modules.dep','modules.dep.bin','modules.alias','modules.alias.bin','modules.builtin','modules.order']]
        validator=mock.Mock();files=APP.module_files(self.archive(members),'fixture',validator,1)
        self.assertEqual(len(files),7);validator.validate_module.assert_called_once_with(b'elf-fixture','fixture')
        for bad in [members[:-1],members+[(prefix+'bad',b'',tarfile.SYMTYPE)],members+[(prefix+'../escape',b'x',tarfile.REGTYPE)],members+[members[0]]]:
            with self.assertRaises(ValueError):APP.module_files(self.archive(bad),'fixture',mock.Mock(),1)


if __name__=='__main__':unittest.main()
