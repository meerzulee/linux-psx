#!/usr/bin/env python3
"""Synthetic offline bundle tests. No SSH, device, UART, build or artifact execution."""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('offline_bundle',HERE/'bundle.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
H=lambda data:hashlib.sha256(data).hexdigest()


def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(value if isinstance(value,bytes) else (json.dumps(value,indent=2,sort_keys=True)+'\n').encode())


def elf(vermagic):
    names=b'\0.shstrtab\0.modinfo\0';info=b'vermagic='+vermagic.encode()+b'\0name=synthetic\0'
    names_off=64;info_off=names_off+len(names);shoff=info_off+len(info)
    head=b'\x7fELF\x02\x01\x01'+b'\0'*9+struct.pack('<HHIQQQIHHHHHH',1,62,1,0,0,shoff,0,64,0,0,64,3,1)
    section=lambda name,kind,offset,size:struct.pack('<IIQQQQIIQQ',name,kind,0,0,offset,size,0,0,1,0)
    return head+names+info+b'\0'*64+section(1,3,names_off,len(names))+section(11,1,info_off,len(info))


def archive(entries,metadata=None):
    data=io.BytesIO()
    with tarfile.open(fileobj=data,mode='w',format=tarfile.PAX_FORMAT) as tar:
        for name,content in entries:
            item=tarfile.TarInfo(name);item.size=len(content);item.mode=0o644;item.pax_headers=metadata or {}
            tar.addfile(item,io.BytesIO(content))
    return subprocess.run([shutil.which('zstd'),'-q','-c'],input=data.getvalue(),capture_output=True,check=True).stdout


class Fixture:
    def __init__(self,root):
        self.root=root;self.run=root/'native';self.reports=self.run/'receipts';self.out=self.run/'project/output'/m.TARGET;self.src=self.run/'project/src'/m.TARGET;self.stage=root/'ib-stage';self.review=root/'compiled.json';self.dest=root/'bundle'
        for p in (self.reports,self.out,self.src,self.stage):p.mkdir(parents=True)
        selected=['amdgpu.h','amdgpu_debugfs.c','amdgpu_device.c','amdgpu_drv.c','amdgpu_fence.c','amdgpu_ids.c','amdgpu_kms.c','amdgpu_object.c','amdgpu_ps4_live_prepare.h','amdgpu_ps4_map_canary.c','amdgpu_ps4_map_canary.h','amdgpu_ps4_uvd_ready.c','amdgpu_ps4_uvd_ready.h','amdgpu_reset.c','amdgpu_vm.c','gmc_v7_0.c']
        self.inventory={};pins={};self.objects={}
        for name in selected:
            raw=('synthetic source '+name).encode();put(self.src/m.PREFIX/name,raw);pins[name]=H(raw)
            self.inventory[m.PREFIX+name]={'kind':'file','mode':420,'bytes':len(raw),'sha256':H(raw)}
        for name in m.OBJECTS:
            obj=('synthetic reviewed object '+name).encode();cmd=('synthetic compile command '+name).encode()
            put(self.src/m.PREFIX/(name+'.o'),obj);put(self.src/m.PREFIX/('.'+name+'.o.cmd'),cmd)
            self.objects[name]={'sha256':H(obj),'bytes':len(obj),'compile_command_sha256':H(cmd),'source_sha256':pins[name+'.c']}
        put(self.src/'vmlinux',b'synthetic linked kernel')
        source_review={'status':'PASS_SCOPED_SOURCE_REVIEW_FOR_NATIVE_BUILD','must_fix':[],'patch_sha256':m.PATCH,'uvd_arm_implemented':False,'source_sha256':pins,'kernel_built':False,'hardware_tested':False}
        put(self.reports/'review.json',source_review);rh=m.sha(self.reports/'review.json')
        identity={'target':m.TARGET,'release':m.RELEASE,'review_sha256':rh,'tool_sha256':H(b'native tool'),'input_pins':{'patch':{'sha256':m.PATCH},'source_review':{'sha256':rh}},'review':{'approved_for_native_build_only':True}}
        put(self.reports/'identity.json',identity);ih=m.sha(self.reports/'identity.json')
        self.base={'target':m.TARGET,'release':m.RELEASE,'review_sha256':rh,'identity_sha256':ih,'baseline_preserved':True,'hardware_acceptance':False}
        put(self.reports/'prepare-base.json',{**self.base,'status':'PASS_PREPARE_BASE','previous_phase_receipt_sha256':None})
        put(self.reports/'project-inputs.json',{'synthetic':{'kind':'file','sha256':H(b'input')}})
        put(self.reports/'prepare.json',{**self.base,'status':'PASS_PREPARE','previous_phase_receipt_sha256':m.sha(self.reports/'prepare-base.json'),'patches':[{'sha256':H(b'prior')}]*72+[{'sha256':m.PATCH}],'project_inputs_sha256':m.sha(self.reports/'project-inputs.json')})
        put(self.reports/'built-source.json',self.inventory);put(self.reports/'compiler.json',{'compiler_argv':['synthetic cc'],'host':{'system':'Linux','machine':'x86_64'}})
        prefix='lib/modules/'+m.RELEASE+'/';self.entries=[];self.tree={};self.modules={}
        for i in range(55):
            name=prefix+('kernel/drivers/synthetic/really-long-nested-module-path/'+str(i)+'.ko' if i<41 else 'modules.index'+str(i))
            content=elf(m.RELEASE+' SMP preempt mod_unload ') if i<41 else ('synthetic index'+str(i)).encode()
            row={'kind':'file','mode':420,'bytes':len(content),'sha256':H(content)};self.tree[name]=row;self.entries.append((name,content))
            if i<41:self.modules[name]={**row,'vermagic':m.RELEASE+' SMP preempt mod_unload','elf_header':{'class':2,'data':1,'machine':62,'type':1}}
        put(self.reports/'module-tree.json',self.tree);put(self.reports/'modules.json',self.modules)
        image=bytearray(1024);image[0x202:0x206]=b'HdrS';put(self.out/'bzImage',bytes(image))
        config=('CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-live-prepare-dev1"\nCONFIG_DRM_AMDGPU=y\nCONFIG_X86_PS4=y\nCONFIG_DEBUG_FS=y\n# CONFIG_DEBUG_FS_ALLOW_ALL is not set\nCONFIG_DEBUG_FS_ALLOW_NONE=y\n# CONFIG_PM is not set\nCONFIG_EXTRA_FIRMWARE=""\nCONFIG_INITRAMFS_SOURCE=""\n').encode();put(self.out/'config',config)
        put(self.out/'version.txt',(m.RELEASE+'\n').encode());put(self.out/'System.map',b'synthetic symbol map')
        manifest={'target':m.TARGET,'release':m.RELEASE,'active_patch_count':73,'patch_sha256':m.PATCH,'review_sha256':rh,'hardware_acceptance':False,'project_inputs_sha256':m.sha(self.reports/'project-inputs.json'),'built_source_inventory_sha256':m.sha(self.reports/'built-source.json'),'native_tool_sha256':identity['tool_sha256'],'compiler':m.load(self.reports/'compiler.json'),'compiled_audit':'pending; build does not replace object review'};put(self.out/'manifest.json',manifest)
        put(self.out/('modules-'+m.RELEASE+'.tar.zst'),archive(self.entries));put(self.out/'inputs.tar.zst',b'synthetic opaque corresponding inputs');put(self.out/('source-'+m.RELEASE+'.tar.zst'),b'synthetic opaque corresponding source')
        self.build={**self.base,'status':'PASS_BUILD','previous_phase_receipt_sha256':m.sha(self.reports/'prepare.json'),'compiler_sha256':m.sha(self.reports/'compiler.json'),'module_tree_sha256':m.sha(self.reports/'module-tree.json'),'modules_sha256':m.sha(self.reports/'modules.json'),'source_inventory_sha256':m.sha(self.reports/'built-source.json'),'module_count':41,'vmlinux_sha256':m.sha(self.src/'vmlinux'),'compiled_audit':'pending'}
        self.init=b'synthetic opaque initramfs; never unpacked';self.init_meta={'output_sha256':H(self.init),'output_bytes':len(self.init),'entries':347,'non_uvd_entries_preserved':346,'member_metadata_preserved_except_size':True}
        put(self.stage/'initramfs.cpio.gz',self.init);put(self.stage/'initramfs-preparation.json',self.init_meta)
        self.args=b'root=synthetic earlycon=unchanged console=null memory=unchanged '+b' '.join(x+b'=1' for x in sorted(m.IB_GATES))+b'\n'
        put(self.stage/'bootargs.new',self.args)
        self.rebind_stage();self.refresh()
    def rebind_stage(self):
        put(self.stage/'plan.json',{'release':m.IB_RELEASE,'new_bootargs_sha256':m.sha(self.stage/'bootargs.new'),'initramfs_sha256':H(self.init),'initramfs_preparation_receipt_sha256':m.sha(self.stage/'initramfs-preparation.json'),'files':{'bootargs.new':m.sha(self.stage/'bootargs.new'),'initramfs.cpio.gz':H(self.init),'initramfs-preparation.json':m.sha(self.stage/'initramfs-preparation.json')}})
    def refresh(self):
        self.build['artifact_sha256']={n:m.sha(self.out/n) for n in ('bzImage','config','System.map','manifest.json','version.txt')};put(self.reports/'build.json',self.build)
        artifacts={'schema':1,'target':m.TARGET,'kernel_release':m.RELEASE,'active_patch_count':73,'module_count':41,'embedded_firmware':False,'hardware_acceptance':'none','review_sha256':self.base['review_sha256'],'kernel_sha256':m.sha(self.out/'bzImage'),'kernel_config_sha256':m.sha(self.out/'config'),'compiled_audit':'pending'}
        for key,name in (('modules_archive','modules-'+m.RELEASE+'.tar.zst'),('source_archive','source-'+m.RELEASE+'.tar.zst'),('inputs_archive','inputs.tar.zst')):artifacts[key]={'path':name,'sha256':m.sha(self.out/name)}
        put(self.out/'artifacts.json',artifacts)
        package={**self.base,'status':'PASS_PACKAGE','previous_phase_receipt_sha256':m.sha(self.reports/'build.json'),'artifacts':artifacts,'artifact_sha256':{n:m.sha(self.out/n) for n in m.OUTPUTS},'archive_verification':{'modules':{'members_verified':55,'regular_files':55},'inputs':{'members_verified':2,'regular_files':2},'source':{'members_verified':len(self.inventory)+4,'regular_files':len(self.inventory)+4}}};put(self.reports/'package.json',package)
        review={'status':'PASS_SCOPED_COMPILED_PREPARATION_REVIEW','release':m.RELEASE,'patch_sha256':m.PATCH,'must_fix':[],'hardware_acceptance':False,'hardware_tested':False,'usb_staged':False,'uvd_arm_implemented':False,'build_receipt_sha256':m.sha(self.reports/'build.json'),'package_receipt_sha256':m.sha(self.reports/'package.json'),'source_review_sha256':self.base['review_sha256'],'kernel_sha256':m.sha(self.out/'bzImage'),'config_sha256':m.sha(self.out/'config'),'vmlinux_sha256':self.build['vmlinux_sha256'],'objects':self.objects};put(self.review,review)
        self.review_sha=m.sha(self.review)
    def run(self):return m.bundle(self.run,self.review,self.review_sha,self.stage,self.dest)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='offline-bundle-synthetic-');self.addCleanup(self.tmp.cleanup)
        self.f=Fixture(Path(self.tmp.name).resolve());self.addCleanup(patch.stopall)
        patch.object(m,'INITRAMFS',H(self.f.init)).start();patch.object(m,'INIT_RECEIPT',m.sha(self.f.stage/'initramfs-preparation.json')).start();patch.object(m,'IB_BOOTARGS',H(self.f.args)).start()
    def invoke(self):return m.bundle(self.f.run,self.f.review,self.f.review_sha,self.f.stage,self.f.dest)
    def refuses(self):
        with self.assertRaises((RuntimeError,ValueError,tarfile.TarError)):self.invoke()
        self.assertFalse(self.f.dest.exists())
    def test_valid_bundle_remains_offline_and_preserves_pending_receipts(self):
        result=self.invoke();self.assertEqual(result['status'],m.STATUS);self.assertFalse(result['executable_install'])
        manifest=m.load(self.f.dest/'MANIFEST.json');self.assertFalse(manifest['hardware_acceptance']);self.assertNotIn('experiment',manifest);self.assertNotIn('session',manifest)
        self.assertEqual(m.load(self.f.dest/'provenance/native/build.json')['compiled_audit'],'pending')
        self.assertEqual(m.load(self.f.dest/'provenance/compiled-review.json')['status'],'PASS_SCOPED_COMPILED_PREPARATION_REVIEW')
        self.assertEqual((self.f.dest/'initramfs.cpio.gz').read_bytes(),self.f.init)
        self.assertEqual((self.f.dest/'bootargs.new').read_bytes(),b'root=synthetic earlycon=unchanged console=null memory=unchanged amdgpu.ps4_uvd_live_prepare=1 debugfs=on\n')
        self.assertEqual(manifest['added_observation_prerequisite'],'debugfs=on');self.assertEqual(len(manifest['configuration_changes']),2)
        for row in manifest['external_archives'].values():self.assertFalse(row['copied']);self.assertEqual(m.sha(Path(row['path'])),row['sha256'])
        self.assertFalse((self.f.dest/'inputs.tar.zst').exists());self.assertEqual(manifest['module_validation']['modules'],41)
    def test_tampered_pinned_compiled_review(self):
        self.f.review.write_bytes(self.f.review.read_bytes()+b' ');self.refuses()
    def test_wrong_pinned_review_digest(self):
        self.f.review_sha='0'*64;self.refuses()
    def test_tampered_phase_chain(self):
        p=self.f.reports/'prepare.json';v=m.load(p);v['previous_phase_receipt_sha256']='0'*64;put(p,v);self.refuses()
    def test_rebound_failed_phase_still_rejected(self):
        self.f.build['status']='FAILED_OR_INTERRUPTED';self.f.refresh();self.refuses()
    def test_tampered_image(self):
        (self.f.out/'bzImage').write_bytes(b'wrong');self.refuses()
    def test_rebound_missing_image_header(self):
        (self.f.out/'bzImage').write_bytes(b'bad image');self.f.refresh();self.refuses()
    def test_tampered_config(self):
        (self.f.out/'config').write_bytes(b'CONFIG_PM=y\n');self.refuses()
    def test_rebound_unsupported_config(self):
        p=self.f.out/'config';p.write_bytes(p.read_bytes().replace(b'# CONFIG_PM is not set',b'CONFIG_PM=y'));self.f.refresh();self.refuses()
    def test_rebound_debugfs_default_mismatch_rejected(self):
        p=self.f.out/'config';p.write_bytes(p.read_bytes().replace(b'CONFIG_DEBUG_FS_ALLOW_NONE=y',b'# CONFIG_DEBUG_FS_ALLOW_NONE is not set').replace(b'# CONFIG_DEBUG_FS_ALLOW_ALL is not set',b'CONFIG_DEBUG_FS_ALLOW_ALL=y'));self.f.refresh();self.refuses()
    def test_release_mismatch(self):
        (self.f.out/'version.txt').write_bytes(b'wrong-release\n');self.f.refresh();self.refuses()
    def test_changed_source_or_object_or_command(self):
        for suffix in ('.c','.o'):
            p=self.f.src/m.PREFIX/('amdgpu_debugfs'+suffix);old=p.read_bytes();p.write_bytes(old+b' changed');self.refuses();p.write_bytes(old)
        p=self.f.src/m.PREFIX/'.amdgpu_debugfs.o.cmd';p.write_bytes(b'wrong command');self.refuses()
    def test_module_member_tamper_after_archive_rebinding(self):
        entries=list(self.f.entries);n,data=entries[0];entries[0]=(n,data[:-1]+b'X');put(self.f.out/('modules-'+m.RELEASE+'.tar.zst'),archive(entries));self.f.refresh();self.refuses()
    def test_module_path_escape_after_archive_rebinding(self):
        entries=list(self.f.entries);n,data=entries[0];entries[0]=('../escape.ko',data);put(self.f.out/('modules-'+m.RELEASE+'.tar.zst'),archive(entries));self.f.refresh();self.refuses()
    def test_duplicate_member_after_archive_rebinding(self):
        entries=list(self.f.entries);entries[-1]=entries[0];put(self.f.out/('modules-'+m.RELEASE+'.tar.zst'),archive(entries));self.f.refresh();self.refuses()
    def test_pax_override_rejected(self):
        put(self.f.out/('modules-'+m.RELEASE+'.tar.zst'),archive(self.f.entries,{'uid':'0'}));self.f.refresh();self.refuses()
    def test_symlink_input_rejected(self):
        p=self.f.out/'config';target=self.f.root/'real-config';p.rename(target);p.symlink_to(target);self.refuses()
    def test_symlink_destination_parent_rejected(self):
        link=self.f.root/'alias';link.symlink_to(self.f.root,target_is_directory=True);self.f.dest=link/'bundle';self.refuses()
    def test_existing_destination_preserved(self):
        self.f.dest.mkdir();(self.f.dest/'owner-file').write_bytes(b'keep')
        with self.assertRaises(RuntimeError):self.invoke()
        self.assertEqual((self.f.dest/'owner-file').read_bytes(),b'keep')
    def test_args_unknown_conflict_duplicate_and_terminator(self):
        variants=[self.f.args[:-1]+b' amdgpu.ps4_uvd_mystery=1\n',self.f.args[:-1]+b' amdgpu.ps4_uvd_live_prepare=1\n',self.f.args[:-1]+b' amdgpu.ps4_uvd_ready=1\n',b'-- '+self.f.args,self.f.args.replace(b'amdgpu.ps4_uvd_ready=1',b'amdgpu.ps4_uvd_ready=0')]
        for raw in variants:
            with self.subTest(raw=raw),patch.object(m,'IB_BOOTARGS',H(raw)):put(self.f.stage/'bootargs.new',raw);self.f.rebind_stage();self.refuses()
    def test_rebound_changed_saved_ib_arguments_rejected(self):
        put(self.f.stage/'bootargs.new',self.f.args.replace(b'console=null',b'console=changed'));self.f.rebind_stage();self.refuses()
    def test_existing_debugfs_argument_rejected_before_bundle_creation(self):
        for token in (b'debugfs=on',b'debugfs=off',b'debugfs=no-mount',b'debugfs',b'debugfs=on debugfs=on'):
            raw=self.f.args[:-1]+b' '+token+b'\n'
            with self.subTest(token=token),patch.object(m,'IB_BOOTARGS',H(raw)):put(self.f.stage/'bootargs.new',raw);self.f.rebind_stage();self.refuses()
    def test_opaque_initramfs_tamper_rejected(self):
        (self.f.stage/'initramfs.cpio.gz').write_bytes(self.f.init+b'wrong');self.refuses()
    def test_missing_source_archive_refused_before_creation(self):
        (self.f.out/('source-'+m.RELEASE+'.tar.zst')).unlink();self.refuses()
    def test_copy_io_failure_retains_incomplete_bundle(self):
        with patch.object(m,'copy_checked',side_effect=OSError('synthetic copy failure')):
            with self.assertRaises(OSError):self.invoke()
        self.assertTrue(self.f.dest.is_dir());self.assertEqual(m.load(self.f.dest/'STATUS.json')['status'],'OFFLINE_BUNDLE_INCOMPLETE');self.assertTrue((self.f.dest/'FAILURE.json').is_file())
        with self.assertRaises(RuntimeError):self.invoke()
    def test_no_module_or_initramfs_execution_path(self):
        real=m.subprocess.Popen;commands=[]
        def popen(argv,*args,**kwargs):commands.append(argv);return real(argv,*args,**kwargs)
        with patch.object(m.subprocess,'Popen',side_effect=popen):self.invoke()
        self.assertEqual(len(commands),1);self.assertEqual(Path(commands[0][0]).name,'zstd');self.assertIn('-d',commands[0]);self.assertIn('-c',commands[0])


class ModuleAndArgsTests(unittest.TestCase):
    def test_native_modinfo_trailing_space_matches_stripped_receipt(self):
        record={'elf_header':{'class':2,'data':1,'machine':62,'type':1},'vermagic':m.RELEASE+' SMP preempt mod_unload'}
        m.module_identity(elf(record['vermagic']+' '),record)
    def test_wrong_vermagic_and_machine_rejected(self):
        good={'elf_header':{'class':2,'data':1,'machine':62,'type':1},'vermagic':m.RELEASE+' SMP'}
        with self.assertRaises(RuntimeError):m.module_identity(elf('wrong SMP'),good)
        raw=bytearray(elf(m.RELEASE+' SMP'));raw[18:20]=(183).to_bytes(2,'little')
        with self.assertRaises(RuntimeError):m.module_identity(raw,good)
    def test_nonexperimental_tokens_preserved_exactly(self):
        prefix=b'root=x console=null console=tty0 foo=a=b'
        original=prefix+b' '+b' '.join(x+b'=1' for x in sorted(m.IB_GATES))+b'\n'
        result,removed=m.bootargs(original);self.assertEqual(result,prefix+b' amdgpu.ps4_uvd_live_prepare=1 debugfs=on\n');self.assertEqual(len(removed),4)
    def test_malformed_and_unknown_alias_gates_rejected(self):
        base=b'root=x '+b' '.join(x+b'=1' for x in sorted(m.IB_GATES))+b'\n'
        for raw in (base.replace(b'root=x ',b'root=x  '),base.replace(b'root=x ',b'root=x\t'),base.replace(b'amdgpu.ps4_uvd_ready',b'amdgpu.ps4-uvd-ready'),base[:-1]+b' other.ps4_uvd_unknown=1\n'):
            with self.assertRaises(RuntimeError):m.bootargs(raw)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    stream=io.StringIO();suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in (BundleTests,ModuleAndArgsTests));r=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    output={'status':'PASS_SYNTHETIC_OFFLINE_BUNDLE_TESTS' if r.wasSuccessful() else 'FAIL_SYNTHETIC_OFFLINE_BUNDLE_TESTS','cases':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'bundle_sha256':m.sha(HERE/'bundle.py'),'tests_sha256':m.sha(Path(__file__)),'hardware_actions':False,'ssh_actions':False,'artifacts_executed':False,'log':stream.getvalue()}
    with a.report.open('x') as f:json.dump(output,f,indent=2,sort_keys=True);f.write('\n')
    print(stream.getvalue(),end='');raise SystemExit(not r.wasSuccessful())
