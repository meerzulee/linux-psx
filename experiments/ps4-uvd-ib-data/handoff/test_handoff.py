#!/usr/bin/env python3
"""Synthetic host tests only. No device, mount, UART or privilege actions."""
import argparse
import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import platform
import tempfile
import tarfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent

def module(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
c=module('test_stobs_common',HERE/'common.py')
e=module('test_stobs_extract',HERE/'extract.py')
old=c.WORKSPACE/'lab/uvd-2026-10-19/data-control/handoff'
legacy=module('retained_synthetic_tests',old/'test_handoff.py')
legacy.m=c.t
# In-memory label required by one unchanged historical transaction assertion.
# The directory is synthetic, and production EXP stays None until configure().
c.configure({'experiment':'EXP-20261019-005'})
c.t.EXPECTED_RELEASE=c.RELEASE


def build_fixture(stage):
    put=lambda n,v:c.save(stage/n,v)
    oldbytes={n:('native '+n+'\n').encode() for n in c.t.BOOT_FILES};oldbytes['bootargs.txt']=b'root=synthetic\n'
    current={n:('revision-c '+n+'\n').encode() for n in c.t.BOOT_FILES}
    current['bootargs.txt']=oldbytes['bootargs.txt'][:-1]+b' amdgpu.ps4_uvd_ready=1 amdgpu.ps4_uvd_poll_state=1 amdgpu.ps4_uvd_token=1 amdgpu.ps4_uvd_status_observation=1\n'
    for name in c.t.KEEP_FILES:current[name]=oldbytes[name]
    rollback=c.load_json(HERE/'rollback.json');rollback['old_boot_hashes']={n:c.sha(v) for n,v in oldbytes.items()};rollback['old_bootargs_sha256']=c.sha(oldbytes['bootargs.txt'])
    source=c.load_json(HERE/'install-source.json');source['boot_hashes']={n:c.sha(v) for n,v in current.items()}
    for dirname,items in (('old-boot',oldbytes),('install-from',current)):
        (stage/dirname).mkdir()
        for n,v in items.items():c.write(stage/dirname/n,v)
    prefix='lib/modules/'+c.RELEASE+'/'
    tree={prefix+(f'kernel/{i:02d}.ko' if i<41 else f'modules.synthetic{i}'): {'sha256':c.sha(f'file{i}'.encode()),'kind':'file','bytes':5,'mode':420} for i in range(55)}
    kernel=bytearray(600);kernel[0x202:0x206]=b'HdrS'
    blobs={'bzImage':bytes(kernel),'config':b'synthetic config','modules.tar.zst':b'synthetic module archive','inputs.tar.zst':b'synthetic inputs archive','bootargs.new':c.new_bootargs(oldbytes['bootargs.txt']),
           'initramfs.cpio.gz':current['initramfs.cpio.gz'],'initramfs-preparation.json':b'{}\n'}
    for n,v in blobs.items():c.write(stage/n,v)
    rollback['initramfs_sha256']=c.sha(blobs['initramfs.cpio.gz']);rollback['initramfs_preparation_receipt_sha256']=c.sha(blobs['initramfs-preparation.json'])
    put('prior-plan.json',{'experiment':source['experiment'],'release':source['release'],'module_tree':source['module_tree'],'old_boot_hashes':rollback['old_boot_hashes'],'rollback_module_tree':rollback['rollback_module_tree']})
    put('prior-install-receipt.json',{'experiment':source['experiment'],'committed':True,'unmounted':True,'boot_after':source['boot_hashes']})
    put('prior-install-complete.json',{'experiment':source['experiment'],'status':'PASS_INSTALLED_UNMOUNTED','plan_sha256':c.file_sha(stage/'prior-plan.json')})
    source['pins']={key:c.file_sha(stage/name) for name,key in (('prior-plan.json','plan_sha256'),('prior-install-receipt.json','install_receipt_sha256'),('prior-install-complete.json','install_complete_sha256'))}
    pins={name:{'sha256':c.sha(name.encode()),'path':'synthetic/'+name} for name in ('patch','checker','fixture','planner','flow','generator','design','native_tool','independent_review')};pins['patch']['sha256']=c.PATCH;pins['planner']['sha256']=c.SOURCE_PINS[c.PREFIX+'amdgpu_ps4_uvd_ib_data_plan.h']
    output={**c.SOURCE_PINS,c.PREFIX+'amdgpu_ps4_uvd_ring_plan.h':'2299fd28005bdbcc2aa3c3664740f5d87a34cb7294c705c45facdde46d44df2e'}
    for name,system in (('host-mac.json','Darwin'),('host-linux.json','Linux')):
        put(name,{'status':'PASS_EXTRACTED_SOURCE_HOST_ONLY',**{key:pins[pin]['sha256'] for key,pin in (('patch_sha256','patch'),('checker_sha256','checker'),('fixture_sha256','fixture'),('planner_sha256','planner'),('flow_sha256','flow'),('generator_sha256','generator'))},'output_sha256':output,'toolchain':{'host':{'system':system,'machine':'x86_64' if system=='Linux' else 'arm64'},'compiler_argv':['cc'],'compiler_path':'/synthetic/cc','compiler_version':'synthetic','compiler_target':'synthetic'},'results':[{'mode':mode,'status':'PASS','returncode':0,'output':'PASS: 3 actual-source cases; synthetic'} for mode in ('optimized','sanitized')],'sensitivity':[{'name':'fixture-mutation','detected':True,'returncode':-6,'expected_witness':'specific','observed_witness':'specific'}],'generated_fixture_sha256':c.sha(b'synthetic generated')})
    primary={'final_primary_review':True,'verdict':'APPROVED_FOR_NATIVE_BUILD_ONLY','unresolved_findings':[],'target':c.TARGET,'release':c.RELEASE,'host_cases':3,'pins':pins,'source_pins':{n:{'path':n,'sha256':h} for n,h in c.SOURCE_PINS.items()},'required_mutations':{'fixture-mutation':'specific'},'host_receipts':[{'path':name,'sha256':c.file_sha(stage/name)} for name in ('host-mac.json','host-linux.json')]};put('primary-review.json',primary)
    rh=c.file_sha(stage/'primary-review.json');put('identity.json',{'review_sha256':rh});ih=c.file_sha(stage/'identity.json')
    shared={'target':c.TARGET,'release':c.RELEASE,'review_sha256':rh,'baseline_preserved':True,'hardware_acceptance':False,'identity_sha256':ih}
    put('prepare-base.json',{**shared,'status':'PASS_PREPARE_BASE'})
    put('prepare.json',{**shared,'status':'PASS_PREPARE','previous_phase_receipt_sha256':c.file_sha(stage/'prepare-base.json'),'changed_sources':sorted(c.SOURCE_PINS),'patches':[{'sha256':'a'*64}]*72})
    put('module-tree.json',tree);put('project-inputs.json',{'synthetic':{'kind':'file'}})
    manifest={'target':c.TARGET,'release':c.RELEASE,'active_patch_count':72,'review_sha256':rh,'patch_sha256':c.PATCH,'irq_source_sha256':c.IRQ,'hardware_acceptance':False,'project_base_commit':'35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2','project_inputs_sha256':c.file_sha(stage/'project-inputs.json'),'native_tool_sha256':pins['native_tool']['sha256']};put('manifest.json',manifest)
    build={**shared,'status':'PASS_BUILD','previous_phase_receipt_sha256':c.file_sha(stage/'prepare.json'),'artifact_sha256':{n:c.file_sha(stage/n) for n in ('bzImage','config','manifest.json')},'module_count':41,'module_tree_sha256':c.file_sha(stage/'module-tree.json'),'vmlinux_sha256':c.sha(b'linked kernel')};put('build.json',build)
    artifacts={'kernel_release':c.RELEASE,'hardware_acceptance':'none','embedded_firmware':False,'module_count':41,'active_patch_count':72,'review_sha256':rh,'kernel_sha256':c.file_sha(stage/'bzImage'),'kernel_config_sha256':c.file_sha(stage/'config'),'modules_archive':{'sha256':c.file_sha(stage/'modules.tar.zst')},'inputs_archive':{'sha256':c.file_sha(stage/'inputs.tar.zst')},'source_archive':{'path':'source-'+c.RELEASE+'.tar.zst','sha256':c.sha(b'source archive')}};put('artifacts.json',artifacts)
    renamed={'modules.tar.zst':'modules-'+c.RELEASE+'.tar.zst'}
    package={**shared,'status':'PASS_PACKAGE','previous_phase_receipt_sha256':c.file_sha(stage/'build.json'),'artifacts':artifacts,'artifact_sha256':{renamed.get(n,n):c.file_sha(stage/n) for n in ('bzImage','config','manifest.json','artifacts.json','modules.tar.zst','inputs.tar.zst')},'archive_verification':{'modules':{'regular_files':55,'members_verified':55},'inputs':{'regular_files':2,'members_verified':2},'source':{'regular_files':3,'members_verified':3}}};package['artifact_sha256']['source-'+c.RELEASE+'.tar.zst']=c.sha(b'source archive');put('package.json',package)
    compiled={'status':'PASS_SCOPED_COMPILED_REVIEW_NO_HARDWARE_ACCEPTANCE','target':c.TARGET,'release':c.RELEASE,'patch_sha256':c.PATCH,'ready_source_sha256':c.CANDIDATE,'source_sha256':c.SOURCE_PINS,'ready_object_sha256':c.sha(b'object'),'canary_object_sha256':c.sha(b'canary object'),'vmlinux_sha256':build['vmlinux_sha256'],'kernel_sha256':artifacts['kernel_sha256'],'config_sha256':artifacts['kernel_config_sha256'],'modules_archive_sha256':artifacts['modules_archive']['sha256'],'native_build_receipt_sha256':c.file_sha(stage/'build.json'),'source_review_sha256':rh,'hardware_acceptance':False,'must_fix_findings':[],'prior_interrupted_reviews_remain_incomplete':True};put('compiled-review.json',compiled)
    p={**rollback,'schema':1,'status':'HOST_STAGED_PENDING_FINAL_REVIEW','experiment':'EXP-20261020-999','target':c.TARGET,'release':c.RELEASE,'candidate_sha256':c.CANDIDATE,'patch_sha256':c.PATCH,'hardware_acceptance':False,'launch_count':1,'launch_timeout_seconds':180,'installation_state':{'usb':'attached-to-Omarchynator','console':['not-observed','orbis-idle','fully-off'],'console_power_observation_required':False,'operator_holds_linux_launch':True,'fresh_full_poweroff_required_for_post_test_rollback':True},'observation':{'max_records':40,'deadline_ns':250000000,'publication_count':1,'confirmation_count':2,'ring_crc32':'e5b6afa6','ib_crc32':'e847443a','data_baseline':'43504442','data_result':'55564442','context_baseline':'43504342','accepted':0},'tools':c.tool_pins(),'module_tree':{n:r['sha256'] for n,r in tree.items()},'source_archive_sha256':artifacts['source_archive']['sha256'],'install_boot_hashes':source['boot_hashes'],'install_release':source['release'],'install_module_tree':source['module_tree'],'native_restore_automatic':False}
    p['files']={x.name:c.file_sha(x) for x in stage.iterdir() if x.is_file()}
    for n,k in (('bzImage','kernel_sha256'),('config','config_sha256'),('modules.tar.zst','modules_archive_sha256'),('bootargs.new','new_bootargs_sha256')):p[k]=p['files'][n]
    put('plan.json',p)
    return p,rollback,source


class AdapterTests(unittest.TestCase):
    def test_retained_transaction_functions_exact(self):
        def funcs(path):
            text=path.read_text();lines=text.splitlines(keepends=True)
            return {n.name:''.join(lines[n.lineno-1:n.end_lineno]) for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)}
        a,b=funcs(HERE.parents[1]/'ps4-uvd-status-observation/handoff/transaction.py'),funcs(HERE/'transaction.py')
        changed={n for n in a if a[n]!=b[n]}
        self.assertEqual(changed,{'install_boot','rollback_hashes','preflight_paths','main'})
        self.assertEqual(set(b)-set(a),{'install_boot_hashes','known_recovery_states','verify_install_source_modules'})
    def test_bootargs_only_gate_change(self):
        a=(old/'stage/old-boot/bootargs.txt').read_bytes();b=(old/'stage/bootargs.new').read_bytes()
        self.assertEqual(c.new_bootargs(a),b.replace(b'amdgpu.ps4_uvd_data_control=1',b'amdgpu.ps4_uvd_ib_data=1'))
    def test_gate_aliases_rejected(self):
        for x in ('ps4_uvd_status_observation=1','amdgpu.ps4-uvd-data-control=0','amdgpu.ps4_uvd_status_observation','ps4-map-canary=1','amdgpu.ps4_uvd_ready=1'):
            with self.subTest(x=x),self.assertRaises(RuntimeError):c.new_bootargs(('root=synthetic '+x+'\n').encode())
    def test_invalid_experiment(self):
        for x in ('EXP-20261301-001','EXP-20261020-000','../EXP-20261020-001','EXP-20261020-001/','not-assigned'):
            with self.subTest(x=x),self.assertRaises((RuntimeError,ValueError)):c.experiment(x)


class NativePaxTests(unittest.TestCase):
    def archive(self, name, metadata):
        data=b'synthetic long-path module'
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w',format=tarfile.PAX_FORMAT) as tar:
            info=tarfile.TarInfo(name);info.size=len(data);info.pax_headers=metadata
            tar.addfile(info,io.BytesIO(data))
        return out.getvalue(),data
    def test_native_long_canonical_path_only(self):
        name='lib/modules/'+c.RELEASE+'/kernel/drivers/net/wireless/mediatek/mt76/mt7615/mt7663-usb-sdio-common.ko'
        raw,data=self.archive(name,{})
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:self.assertEqual(tar.next().pax_headers,{'path':name})
        plan={'release':c.RELEASE,'module_tree':{name:c.sha(data)}}
        self.assertEqual(c.t.validate_archive(raw,plan),{'kernel/drivers/net/wireless/mediatek/mt76/mt7615/mt7663-usb-sdio-common.ko':data})
    def test_noncanonical_and_substituted_pax_path_rejected(self):
        prefix='lib/modules/'+c.RELEASE+'/'
        for metadata in ({'path':prefix+'../outside.ko'},{'path':'/'+prefix+'kernel/x.ko'},{'path':prefix+'kernel/other.ko'}):
            with self.subTest(metadata=metadata):
                raw,data=self.archive(prefix+'kernel/x.ko',metadata)
                with self.assertRaises(RuntimeError):c.t.validate_archive(raw,{'release':c.RELEASE,'module_tree':{prefix+'kernel/x.ko':c.sha(data)}})
    def test_every_other_pax_override_rejected(self):
        name='lib/modules/'+c.RELEASE+'/kernel/test.ko'
        for metadata in ({'size':str(len(b'synthetic long-path module'))},{'linkpath':'../elsewhere'},{'uid':'0'},{'mtime':'1'},{'comment':'extra'},{'path':name,'size':str(len(b'synthetic long-path module'))}):
            with self.subTest(metadata=metadata):
                raw,data=self.archive(name,metadata)
                with self.assertRaisesRegex(RuntimeError,'PAX'):c.t.validate_archive(raw,{'release':c.RELEASE,'module_tree':{name:c.sha(data)}})


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.stage=Path(self.tmp.name)/'stage';self.stage.mkdir()
        self.plan,self.rollback,self.source=build_fixture(self.stage)
        load=c.load_json
        self.addCleanup(patch.stopall)
        patch.object(c,'load_json',side_effect=lambda p:self.rollback if p==HERE/'rollback.json' else self.source if p==HERE/'install-source.json' else load(p)).start()
        patch.object(c.t,'OLD_NATIVE_BOOT_HASHES',self.rollback['old_boot_hashes']).start()
    def test_valid_chain(self):
        c.validate_plan(self.plan);c.validate_chain(self.plan,self.stage)
    def test_each_binding_changed_rejected_before_command(self):
        for n in self.plan['files']:
            with self.subTest(n=n):
                p=self.stage/n;oldbytes=p.read_bytes();p.write_bytes(oldbytes+b' ')
                with patch.object(c.t,'run',side_effect=AssertionError('No command allowed')):
                    with self.assertRaises(RuntimeError):c.load_inputs(self.stage)
                p.write_bytes(oldbytes)
    def test_bad_receipt_semantics_even_after_rehash(self):
        changes={'build.json':('baseline_preserved',False),'package.json':('status','FAILED_OR_INTERRUPTED'),
                 'primary-review.json':('final_primary_review',False),'manifest.json':('irq_source_sha256','0'*64),
                 'compiled-review.json':('must_fix_findings',['pending'])}
        for n,(key,value) in changes.items():
            with self.subTest(n=n):
                p=self.stage/n;oldbytes=p.read_bytes();v=json.loads(oldbytes);v[key]=value;p.write_text(json.dumps(v));plan=copy.deepcopy(self.plan);plan['files'][n]=c.file_sha(p)
                with self.assertRaises(RuntimeError):c.validate_chain(plan,self.stage)
                p.write_bytes(oldbytes)
    def test_retained_c_copy_is_required_for_install_not_rollback(self):
        (self.stage/'install-from/bzImage').write_bytes(b'wrong current')
        with patch.object(c.t,'run',side_effect=AssertionError('No commands')):
            with self.assertRaisesRegex(RuntimeError,'Retained Revision C'):c.load_inputs(self.stage)
            c.load_inputs(self.stage,rollback=True)
    def test_missing_exact_mutation_witness_is_rejected(self):
        primary=c.load_json(self.stage/'primary-review.json');host=self.stage/'host-linux.json'
        value=json.loads(host.read_text());value['sensitivity'][0]['observed_witness']='unrelated crash';host.write_text(json.dumps(value));plan=copy.deepcopy(self.plan);plan['files']['host-linux.json']=c.file_sha(host)
        primary['host_receipts'][1]['sha256']=c.file_sha(host)
        with self.assertRaisesRegex(RuntimeError,'witness'):c.validate_host_receipts(plan,self.stage,primary)
    def test_host_stager_preserves_distinct_c_and_native_sets(self):
        import shutil
        stager=module('ibdata_stage_test',HERE/'prepare_stage.py');stager.c=c
        workspace=Path(self.tmp.name)/'workspace';workspace.mkdir()
        run=workspace/'native';receipts=run/'receipts';receipts.mkdir(parents=True)
        output=run/'project/output'/c.TARGET;output.mkdir(parents=True)
        for name in ('build.json','package.json','prepare.json','prepare-base.json','identity.json','module-tree.json','project-inputs.json'):
            shutil.copy2(self.stage/name,receipts/name)
        shutil.copy2(self.stage/'primary-review.json',receipts/'review.json')
        for name in ('host-mac.json','host-linux.json'):shutil.copy2(self.stage/name,workspace/name)
        for name in ('bzImage','config','manifest.json','artifacts.json','inputs.tar.zst'):shutil.copy2(self.stage/name,output/name)
        shutil.copy2(self.stage/'modules.tar.zst',output/('modules-'+c.RELEASE+'.tar.zst'))
        (output/('source-'+c.RELEASE+'.tar.zst')).write_bytes(b'source archive')
        prior=workspace/'lab/uvd-2026-10-19/status-observation/handoff/stage';prior.mkdir(parents=True)
        shutil.copytree(self.stage/'old-boot',prior/'old-boot')
        for n in ('bzImage','initramfs.cpio.gz'):shutil.copy2(self.stage/'install-from'/n,prior/n)
        shutil.copy2(self.stage/'install-from/bootargs.txt',prior/'bootargs.new')
        shutil.copy2(self.stage/'initramfs-preparation.json',prior/'initramfs-preparation.json')
        shutil.copy2(self.stage/'prior-plan.json',prior/'plan.json');(prior.parent/'runtime').mkdir()
        shutil.copy2(self.stage/'prior-install-receipt.json',prior.parent/'runtime/install-receipt.json')
        shutil.copy2(self.stage/'prior-install-complete.json',prior.parent/'runtime/install-complete.json')
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w',format=tarfile.PAX_FORMAT) as tar:
            for i,(name,row) in enumerate(self.plan['module_tree'].items()):
                # Recover matching fixture content from its expected digest.
                content=next(('file'+str(j)).encode() for j in range(55) if c.sha(('file'+str(j)).encode())==row)
                item=tarfile.TarInfo(name);item.size=len(content);tar.addfile(item,io.BytesIO(content))
        def command(*argv,**kwargs):
            if argv[0]=='zstd':return archive.getvalue()
            if argv[0]=='modinfo':return c.RELEASE+' synthetic'
            raise AssertionError('No other commands permitted')
        destination=workspace/'new-host-stage'
        with patch.object(c,'WORKSPACE',workspace),patch.object(c.t,'run',side_effect=command),patch.object(c.t,'validate_initramfs_receipt',return_value={}):
            result=stager.prepare(run,self.stage/'compiled-review.json','EXP-20261020-999',destination)
            self.assertEqual(result['status'],'HOST_STAGE_VERIFIED_NO_DEVICES')
            with self.assertRaisesRegex(RuntimeError,'already exists'):stager.prepare(run,self.stage/'compiled-review.json','EXP-20261020-999',destination)
        self.assertEqual((destination/'install-from/bzImage').read_bytes(),(self.stage/'install-from/bzImage').read_bytes())
        self.assertEqual((destination/'old-boot/bzImage').read_bytes(),(self.stage/'old-boot/bzImage').read_bytes())
        self.assertNotEqual((destination/'install-from/bzImage').read_bytes(),(destination/'old-boot/bzImage').read_bytes())
    def test_missing_compiled_review(self):
        (self.stage/'compiled-review.json').unlink()
        with patch.object(c.t,'run',side_effect=AssertionError('No command allowed')):
            with self.assertRaises(RuntimeError):c.load_inputs(self.stage)
    def test_tool_changed_rejected(self):
        p=copy.deepcopy(self.plan);p['tools']['transaction.py']='0'*64
        with self.assertRaisesRegex(RuntimeError,'tool bytes'):c.validate_plan(p)
    def test_whole_modules_required(self):
        p=copy.deepcopy(self.plan);p['module_tree'].pop(next(iter(p['module_tree'])))
        with self.assertRaises(RuntimeError):c.validate_plan(p)
    def test_rollback_independent_of_missing_experimental_binaries(self):
        (self.stage/'bzImage').unlink();(self.stage/'modules.tar.zst').unlink()
        self.assertEqual(c.load_inputs(self.stage,rollback=True)[4],{n:(self.stage/'old-boot'/n).read_bytes() for n in c.t.BOOT_FILES})
    def test_handoff_requires_exact_compiled_and_tools(self):
        plan_sha=c.file_sha(self.stage/'plan.json')
        r={'status':'APPROVED_ONE_BOUNDED_TEST','experiment':self.plan['experiment'],'target':c.TARGET,'release':c.RELEASE,'plan_sha256':plan_sha,'tools':self.plan['tools'],'compiled_review_sha256':self.plan['files']['compiled-review.json'],'source_review_sha256':self.plan['files']['primary-review.json'],'build_receipt_sha256':self.plan['files']['build.json'],'package_receipt_sha256':self.plan['files']['package.json'],'hardware_acceptance':False,'prior_interrupted_reviews_remain_incomplete':True}
        f=self.stage.parent/'REVIEWED-HANDOFF.json';c.save(f,r);c.validate_reviewed_handoff(self.stage,c.file_sha(f),plan_sha)
        r['compiled_review_sha256']='0'*64;f.write_text(json.dumps(r))
        with self.assertRaises(RuntimeError):c.validate_reviewed_handoff(self.stage,c.file_sha(f),plan_sha)


class RunnerAssertionTests(unittest.TestCase):
    def test_physical_assertion_and_evidence_required_before_reads(self):
        runner=module('stobs_runner_test',HERE/'run_test.py')
        for mode in ('install','rollback'):
            for extra in ([],['--confirm-fresh-poweroff-and-usb-return'],['--state-evidence','operator report']):
                argv=['run_test.py',mode,'--stage','/synthetic-nonexistent','--plan-sha256','a'*64,'--review-sha256','b'*64,*extra]
                with self.subTest(mode=mode,extra=extra),patch.object(runner.sys,'argv',argv),patch.object(runner,'sha',side_effect=AssertionError('No reads')),patch.object(runner,'command',side_effect=AssertionError('No commands')):
                    with self.assertRaises(RuntimeError):runner.main()
    def run_install_fixture(self,state,fail_identify_at=None):
        runner=module('stobs_runner_positive',HERE/'run_test.py')
        with tempfile.TemporaryDirectory() as tmp:
            stage=Path(tmp)/'stage';stage.mkdir()
            exp='EXP-'+runner.datetime.datetime.now(runner.datetime.timezone(runner.datetime.timedelta(hours=6))).strftime('%Y%m%d')+'-999'
            plan={'experiment':exp}
            disk={'path':'/dev/synthetic','serial':c.t.SERIAL}
            parts={role:{'path':'/dev/synthetic-'+role} for role in ('boot','root')}
            calls=[]
            identified=0
            def identify(*args,**kwargs):
                nonlocal identified
                identified+=1
                if identified==fail_identify_at:raise RuntimeError('Synthetic live USB identity changed')
                return disk,parts
            def command(argv,report=None):
                words=[str(x) for x in argv];calls.append(words)
                if words[-1]=='status':
                    return 'No active debugging session.\n' if 'session' in words else '[+] Status:  READY\n'
                if '--apply' in words:return json.dumps({'committed':True,'unmounted':True})
                return ''
            argv=['run_test.py','install','--stage',str(stage),'--plan-sha256','a'*64,'--review-sha256','b'*64,'--confirm-usb-on-host-launch-held','--console-state',state,'--state-evidence','Live USB identity verified on host; exclusive installation ownership; launch held. Console state: '+state]
            with patch.object(runner.sys,'argv',argv),patch.object(runner.sys,'platform','linux'),patch.object(runner.os,'geteuid',return_value=1000),patch.object(runner,'sha',return_value='a'*64),patch.object(runner.c,'validate_reviewed_handoff',return_value={}),patch.object(runner.c,'load_inputs',return_value=(plan,None,None,{},None,None)),patch.object(runner.c.t,'identify',side_effect=identify),patch.object(runner.c.t,'uart_ready',return_value={}),patch.object(runner,'WORKSPACE',Path(tmp)),patch.object(runner,'command',side_effect=command),patch.object(runner.subprocess,'run',return_value=SimpleNamespace(returncode=0)),contextlib.redirect_stdout(io.StringIO()):
                if fail_identify_at:
                    with self.assertRaisesRegex(RuntimeError,'live USB identity changed'):runner.main()
                else:runner.main()
            if fail_identify_at:
                self.assertFalse(any('--apply' in call for call in calls))
                if fail_identify_at==1:self.assertEqual(calls,[])
                return
            start=json.loads((stage.parent/'runtime/install-started.json').read_text())
            self.assertEqual(start['console_state'],state)
            self.assertEqual(start['console_state_observed'],state!='not-observed')
            self.assertEqual(start['operator_reported_console_state'],None if state=='not-observed' else state)
            self.assertFalse(start['fresh_poweroff_and_usb_return_confirmation'])
            self.assertTrue(start['operator_holds_linux_launch'])
            apply=next(x for x in calls if '--apply' in x)
            self.assertIn('--confirm-usb-on-host-launch-held',apply);self.assertNotIn('--confirm-poweroff-and-usb-return',apply)
            self.assertTrue((stage.parent/'runtime/install-complete.json').exists())
    def test_runner_accepts_orbis_and_records_truthful_state(self):
        self.run_install_fixture('orbis-idle')
    def test_runner_accepts_unobserved_console_without_inventing_report(self):
        self.run_install_fixture('not-observed')
    def test_asserted_host_usb_cannot_override_missing_live_identity(self):
        self.run_install_fixture('not-observed',fail_identify_at=1)
    def test_stale_usb_identity_stops_before_install(self):
        self.run_install_fixture('not-observed',fail_identify_at=2)
    def test_direct_installer_missing_host_assertion_before_inventory(self):
        with patch.object(c.t.sys,'argv',['install_usb.py','--apply','/synthetic-nonexistent','--confirm-serial',c.t.SERIAL]),patch.object(c.t.sys,'platform','linux'),patch.object(c.t.os,'geteuid',return_value=0),patch.object(c.t,'identify',side_effect=AssertionError('No device read')) as identify,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):c.t.main()
            identify.assert_not_called()


class InstallationStateTests(unittest.TestCase):
    def run_transaction_fixture(self,state):
        m = c.t
        with tempfile.TemporaryDirectory(prefix="psx-flow-") as tmp:
            base = Path(tmp); stage = base / "stage"; stage.mkdir()
            (stage / "plan.json").write_bytes(b"synthetic reviewed plan")
            private = base / "private"; private.mkdir()
            plan = {"install_release": 'prior-release', "release": c.t.EXPECTED_RELEASE, "boot_uuid": c.t.MEDIA["boot_uuid"],
                    "old_boot_hashes": {"bzImage": "old", "initramfs.cpio.gz": "oldinit"}, "old_bootargs_sha256": "oldargs"}
            disk = {"path": "/dev/synthetic"}
            parts = {role: {"path": "/dev/synthetic" + role, "size": 1, "uuid": role,
                            "fstype": "ext4" if role == "root" else "vfat", "mounts": [], "maj:min": role}
                     for role in ("root", "boot")}
            order = []
            with patch.object(c.t.sys, "argv", ["install_usb.py", "--apply", str(stage), "--confirm-serial", c.t.SERIAL,
                              "--reviewed-plan-sha256", c.t.sha(b"synthetic reviewed plan"),
                              "--reviewed-handoff-sha256", "1" * 64, "--confirm-usb-on-host-launch-held", "--console-state", state]), \
                 patch.object(c.t.sys, "platform", "linux"), patch.object(c.t.os, "geteuid", return_value=0), \
                 patch.object(m, "validate_reviewed_handoff", return_value={}), \
                 patch.object(m, "load_inputs", return_value=(plan, b"kernel", b"initramfs", {"a.ko": b"module"}, {}, b"args")), \
                 patch.object(m, "identify", return_value=(disk, parts)), \
                 patch.object(m, "uart_ready", return_value={"synthetic": True}), \
                 patch.object(c.t.ctypes, "CDLL", return_value=SimpleNamespace(unshare=lambda flags: 0)), \
                 patch.object(c.t.tempfile, "mkdtemp", return_value=str(private)), \
                 patch.object(m, "install_boot_hashes", return_value={}), patch.object(m, "preflight_paths"), \
                 patch.object(m, "module_parent"), patch.object(m, "verify_rollback_modules", return_value=41), patch.object(m, "verify_install_source_modules", return_value=41), \
                 patch.object(m, "install_modules", side_effect=lambda *a: order.append("modules") or []), \
                 patch.object(m, "install_boot", side_effect=lambda *a: order.append("initramfs then bootargs then kernel") or {}), \
                 patch.object(m, "run", return_value=""), patch.object(c.t.os.path, "ismount", return_value=False), \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                c.t.main()
            self.assertEqual(order, ["modules", "initramfs then bootargs then kernel"])
            self.assertTrue(json.loads(out.getvalue())["committed"])

            self.assertEqual(json.loads(out.getvalue())["console_state"], state)
            self.assertEqual(json.loads(out.getvalue())["console_state_observed"], state != "not-observed")
            self.assertEqual(json.loads(out.getvalue())["operator_reported_console_state"], None if state == "not-observed" else state)
            self.assertFalse(json.loads(out.getvalue())["operator_poweroff_and_usb_return_confirmed"])

    def test_idle_orbis_install_modules_precede_boot_commit(self):
        self.run_transaction_fixture('orbis-idle')
    def test_unobserved_console_keeps_modules_before_boot_and_no_power_claim(self):
        self.run_transaction_fixture('not-observed')
    def test_fully_off_console_remains_supported(self):
        self.run_transaction_fixture('fully-off')
    def test_post_test_rollback_still_requires_observed_full_off(self):
        argv=['install_usb.py','--rollback','/synthetic-nonexistent','--confirm-serial',c.t.SERIAL,'--confirm-poweroff-and-usb-return','--console-state','not-observed']
        with patch.object(c.t.sys,'argv',argv),patch.object(c.t.sys,'platform','linux'),patch.object(c.t.os,'geteuid',return_value=0),patch.object(c.t,'identify',side_effect=AssertionError('No inventory')) as identify,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):c.t.main()
            identify.assert_not_called()
    def test_launching_console_refused_before_inventory(self):
        for state,flag in (("linux-launching",True),("orbis-idle",False),("not-observed",False)):
            argv=["install_usb.py","--apply","/synthetic-nonexistent","--confirm-serial",c.t.SERIAL,"--console-state",state]
            if flag: argv.append("--confirm-usb-on-host-launch-held")
            with self.subTest(state=state,flag=flag),patch.object(c.t.sys,"argv",argv),patch.object(c.t.sys,"platform","linux"),patch.object(c.t.os,"geteuid",return_value=0),patch.object(c.t,"identify",side_effect=AssertionError("No inventory")) as identify,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):c.t.main()
                identify.assert_not_called()


class IBTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.boot=Path(self.tmp.name)
        self.native={n:('native '+n).encode() for n in c.t.BOOT_FILES}
        self.current={n:('revision-c '+n).encode() for n in c.t.BOOT_FILES}
        for n in c.t.KEEP_FILES:self.current[n]=self.native[n]
        self.kernel=b'IB kernel';self.initramfs=self.current['initramfs.cpio.gz'];self.args=b'IB args'
        self.plan={'old_boot_hashes':{n:c.sha(v) for n,v in self.native.items()},'install_boot_hashes':{n:c.sha(v) for n,v in self.current.items()},'kernel_sha256':c.sha(self.kernel),'initramfs_sha256':c.sha(self.initramfs),'old_bootargs_sha256':c.sha(self.native['bootargs.txt']),'new_bootargs_sha256':c.sha(self.args)}
        for n,v in self.current.items():(self.boot/n).write_bytes(v)
        self.real_replace=c.t.os.replace
    def recovery(self):
        d=self.boot/('psx-recovery-'+c.t.EXP);d.mkdir()
        for n,v in self.native.items():(d/n).write_bytes(v)
        return d
    def test_install_from_c_saves_native_not_c(self):
        order=[]
        def replace(src,dst):
            self.assertEqual({n:(self.boot/('psx-recovery-'+c.t.EXP)/n).read_bytes() for n in c.t.BOOT_FILES},self.native)
            order.append(Path(dst).name);self.real_replace(src,dst)
        with patch.object(c.t,'run',return_value=''),patch.object(c.t.os,'replace',side_effect=replace):
            result=c.t.install_boot(self.boot,self.plan,self.kernel,self.initramfs,self.native,self.args)
        self.assertEqual(order,['initramfs.cpio.gz','bootargs.txt','bzImage'])
        self.assertEqual(result['bzImage'],c.sha(self.kernel))
    def test_native_or_unknown_is_not_install_from_c(self):
        for value in (self.native['bzImage'],b'unknown'):
            with self.subTest(value=value):
                (self.boot/'bzImage').write_bytes(value)
                with patch.object(c.t,'run',side_effect=AssertionError('No commands')),self.assertRaises(RuntimeError):c.t.install_boot(self.boot,self.plan,self.kernel,self.initramfs,self.native,self.args)
                self.assertFalse((self.boot/('psx-recovery-'+c.t.EXP)).exists())
    def test_known_states_exactly_reachable_not_cartesian(self):
        self.recovery();allowed=c.t.known_recovery_states(self.plan);self.assertEqual(len(allowed),8)
        import itertools
        candidates={'bzImage':[self.native['bzImage'],self.current['bzImage'],self.kernel],'initramfs.cpio.gz':[self.native['initramfs.cpio.gz'],self.initramfs],'bootargs.txt':[self.native['bootargs.txt'],self.current['bootargs.txt'],self.args]}
        accepted=rejected=0
        for values in itertools.product(*candidates.values()):
            for n,v in zip(candidates,values):(self.boot/n).write_bytes(v)
            state={n:c.sha((self.boot/n).read_bytes()) for n in c.t.BOOT_FILES}
            if tuple(sorted(state.items())) in allowed:
                self.assertEqual(c.t.rollback_hashes(self.boot,self.plan),state);accepted+=1
            else:
                with self.assertRaises(RuntimeError):c.t.rollback_hashes(self.boot,self.plan)
                rejected+=1
        self.assertEqual((accepted,rejected),(8,10))
    def test_all_known_states_restore_native_kernel_first(self):
        self.recovery()
        lookup={c.sha(v):v for v in [*self.native.values(),*self.current.values(),self.kernel,self.initramfs,self.args]}
        for row in c.t.known_recovery_states(self.plan):
            for n,h in row:(self.boot/n).write_bytes(lookup[h])
            order=[]
            def replace(src,dst):order.append(Path(dst).name);self.real_replace(src,dst)
            with patch.object(c.t,'run',return_value=''),patch.object(c.t.os,'replace',side_effect=replace):r=c.t.restore_boot(self.boot,self.plan,self.native)
            self.assertEqual(order,['bzImage','initramfs.cpio.gz','bootargs.txt']);self.assertEqual(r,self.plan['old_boot_hashes'])
    def test_each_install_interruption_keeps_recoverable_known_state(self):
        for failed in ('initramfs.cpio.gz','bootargs.txt','bzImage'):
            with self.subTest(failed=failed),tempfile.TemporaryDirectory() as tmp:
                boot=Path(tmp)
                for n,v in self.current.items():(boot/n).write_bytes(v)
                def replace(src,dst):
                    if Path(dst).name==failed:raise OSError('synthetic interrupted commit')
                    self.real_replace(src,dst)
                with patch.object(c.t,'run',return_value=''),patch.object(c.t.os,'replace',side_effect=replace):
                    with self.assertRaises(OSError):c.t.install_boot(boot,self.plan,self.kernel,self.initramfs,self.native,self.args)
                c.t.preflight_paths(boot,self.plan,rollback=True)
                with patch.object(c.t,'run',return_value=''):self.assertEqual(c.t.restore_boot(boot,self.plan,self.native),self.plan['old_boot_hashes'])
    def test_prior_c_recovery_is_preserved(self):
        prior=self.boot/'psx-recovery-EXP-20261020-001';prior.mkdir();(prior/'keep').write_bytes(b'prior')
        with patch.object(c.t,'run',return_value=''):c.t.install_boot(self.boot,self.plan,self.kernel,self.initramfs,self.native,self.args)
        self.assertEqual((prior/'keep').read_bytes(),b'prior')


def rec(i=0,data=e.VALUE,rp=16,status=2,valid=1,start=None,end=None,context=e.CONTEXT):
    start=100000+i*50000 if start is None else start;end=start+10000 if end is None else end
    return dict(i=i,elapsed_ns=start,t_end_ns=end,valid=valid,predeadline=int(end<e.LIMIT),status=status,int=2,rbc=0x1100080c,ib=16,wp=16,rp=rp,ctx=context,data=data,wpc=0,rpa=0,fault=0,reset=0,in_reset=0,pd=0x3000,bit16=int(bool(status&0x10000)))


def stats(**kwargs):
    p=dict(valid_samples=0,data_seen=0,transport_seen=0,consecutive=0,joint_samples=0,confirmed=0,first_data_ns=0,first_data_end_ns=0,first_transport_ns=0,first_joint_ns=0,confirmed_ns=0,observed_data_seen=0,last_rp=0,bit16_seen=0)
    p.update(kwargs);return p


def positive_stats():
    return stats(valid_samples=2,data_seen=1,transport_seen=1,consecutive=2,joint_samples=2,confirmed=1,first_data_ns=100000,first_data_end_ns=110000,first_transport_ns=100000,first_joint_ns=100000,confirmed_ns=160000,observed_data_seen=1,last_rp=16)


def logfixture(records,progress,klass,result=None,error=0,reason='deadline',final=None,sweep=1,save_last=False):
    def line(tag,values):
        rendered=[]
        for k,v in values.items():rendered.append(k+'='+ (f'{v:08x}' if k in e.HEX else str(v)))
        return 'PS4_UVD_IBDATA '+tag+' '+' '.join(rendered)
    final_sampled=reason!='fault';result=result or klass
    if final is None:final={k:records[-1][k] for k in e.SAMPLE} if records else {k:rec()[k] for k in e.SAMPLE}
    evidence={k:v for k,v in progress.items() if k in e.EVIDENCE_KEYS};evidence['class']=klass
    end=dict(result=result,error=error,end_reason=reason,enabled=1,submitted=1,elapsed_ns=(max([r['t_end_ns'] for r in records] or [0]) if reason=='record_budget' else max(e.LIMIT,max([r['t_end_ns'] for r in records] or [0]))),publish_ns=90000,records=len(records),predeadline_records=sum(r['valid'] and r['predeadline'] for r in records),observed_data_seen=progress['observed_data_seen'],last_rp=progress['last_rp'],bit16_seen=progress['bit16_seen'],final_sampled=int(final_sampled),final_ib=final['ib'] if final_sampled else 0,final_valid=final['valid'] if final_sampled else 0,sweep_ran=int(final_sampled and sweep is not None),sweep_valid=int(bool(sweep)) if final_sampled else 0,marker_observed=int(final_sampled and not error and progress['confirmed']),accepted=0)
    lines=['Linux version '+c.RELEASE+' synthetic',line('PREPARED',e.PREPARED),'PS4_UVD_IBDATA PUBLICATION_BEGIN empty_baselines=data:43504442/context:43504342 elapsed_ns=80000; next write']
    if final_sampled:
        lines.append(line('FINAL_CORE',{'sampled':'all_then_data0',**final}))
        if sweep is not None:lines.append(line('CTX_SWEEP',dict(valid=sweep,kind=0,reg=0,value=0,mask=0,expected=0)))
    if save_last:lines.append(line('SAVED',{'sampled':'all_then_data0',**{k:records[-1][k] for k in e.SAMPLE}}))
    lines.extend(line('REC',r) for r in records);lines.extend([line('EVIDENCE',evidence),line('END',end),f'PS4_UVD_READY RESULT={result} error={error} PARKED retained'])
    raw=('\n'.join(lines)+'\n').encode();session={'label':'EXP-20261020-999','state':'completed','continuity_errors':[],'bytes':len(raw),'start_offset':55,'end_offset':55+len(raw),'capture_generation':'synthetic','connection_epoch':1}
    return raw,session


class IBExtractTests(unittest.TestCase):
    def runlog(self,*args,**kwargs):
        raw,session=logfixture(*args,**kwargs);return e.analyze(raw,session,session['label'])
    def test_two_predeadline_joint_samples_only(self):
        r=self.runlog([rec(),rec(1)],positive_stats(),'IBDATA_MARKER_OBSERVED')
        self.assertEqual(r['evidence_errors'],[]);self.assertTrue(r['log_supports_marker']);self.assertFalse(r['qualified_marker_observation']);self.assertFalse(r['hardware_acceptance'])
    def test_record_budget_is_distinct_from_deadline(self):
        p=positive_stats();p.update(valid_samples=40,joint_samples=40,consecutive=40)
        r=self.runlog([rec(i) for i in range(40)],p,'IBDATA_MARKER_OBSERVED',reason='record_budget')
        self.assertEqual(r['evidence_errors'],[]);self.assertTrue(r['log_supports_marker']);self.assertLess(r['end']['elapsed_ns'],e.LIMIT)
        raw,session=logfixture([rec(),rec(1)],positive_stats(),'IBDATA_MARKER_OBSERVED',reason='record_budget')
        bad=e.analyze(raw,session,session['label']);self.assertIn('Record-budget end differs',bad['evidence_errors'])
    def test_negative_outcome_classes(self):
        cases=[([rec(data=e.BASE),rec(1,data=e.BASE)],stats(valid_samples=2,transport_seen=1,last_rp=16,first_transport_ns=100000),'IBDATA_DRAINED_NO_MARKER',-110),
               ([rec(data=e.BASE,status=0x10002)],stats(valid_samples=1,transport_seen=1,last_rp=16,first_transport_ns=100000,bit16_seen=1),'IBDATA_STATUS_WITHOUT_MARKER',-5),
               ([rec(rp=8)],stats(valid_samples=1,data_seen=1,observed_data_seen=1,last_rp=8,first_data_ns=100000,first_data_end_ns=110000),'IBDATA_MARKER_UNCONFIRMED',-110),
               ([rec(data=e.BASE,rp=0)],stats(valid_samples=1),'IBDATA_NO_TRANSPORT',-110)]
        for records,p,klass,error in cases:
            with self.subTest(klass=klass):
                r=self.runlog(records,p,klass,error=error);self.assertEqual(r['evidence_errors'],[]);self.assertFalse(r['log_supports_marker'])
    def test_busy_bit_zero_does_not_create_status_without_marker(self):
        r=self.runlog([rec(data=e.BASE,rp=0,status=3)],stats(valid_samples=1),'IBDATA_NO_TRANSPORT',error=-110)
        self.assertEqual(r['evidence_errors'],[])
    def test_late_marker_is_preserved_but_uncredited(self):
        r=self.runlog([rec(start=e.LIMIT-1,end=e.LIMIT)],stats(observed_data_seen=1,last_rp=16),'IBDATA_NO_TRANSPORT',error=-110,save_last=True)
        self.assertEqual(r['evidence_errors'],[]);self.assertEqual(r['records'][0]['data'],e.VALUE);self.assertEqual(r['evidence']['data_seen'],0);self.assertFalse(r['log_supports_marker'])
    def test_failing_marker_is_preserved_but_uncredited(self):
        r=self.runlog([rec(valid=0,context=0)],stats(),'IBDATA_NO_TRANSPORT',result='IBDATA_UNEXPECTED_CONTEXT',error=-5,reason='fault',save_last=True)
        self.assertEqual(r['evidence_errors'],[]);self.assertEqual(r['records'][0]['data'],e.VALUE);self.assertEqual(r['evidence']['valid_samples'],0)
    def test_late_marker_regression_remains_terminal(self):
        r=self.runlog([rec(),rec(1),rec(2,data=e.BASE,start=e.LIMIT-1,end=e.LIMIT)],positive_stats(),'IBDATA_MARKER_OBSERVED',result='IBDATA_MARKER_REGRESSION',error=-5,reason='fault',save_last=True)
        self.assertEqual(r['evidence_errors'],[]);self.assertFalse(r['log_supports_marker']);self.assertEqual(r['evidence']['confirmed'],1)
    def test_endpoint_cannot_create_success(self):
        final={k:rec()[k] for k in e.SAMPLE}
        r=self.runlog([rec(data=e.BASE)],stats(valid_samples=1,transport_seen=1,last_rp=16,first_transport_ns=100000),'IBDATA_DRAINED_NO_MARKER',error=-110,final=final)
        self.assertEqual(r['evidence_errors'],[]);self.assertFalse(r['log_supports_marker'])
    def test_endpoint_and_sweep_failures_retain_prior_confirmation(self):
        for name,final,sweep in [('IBDATA_MARKER_REGRESSION',{k:rec(data=e.BASE)[k] for k in e.SAMPLE},None),('IBDATA_FINAL_GUARD',{k:rec(valid=0,status=4)[k] for k in e.SAMPLE},None),('IBDATA_POINTER_REGRESSION',{k:rec(rp=8)[k] for k in e.SAMPLE},None),('IBDATA_CTX_SWEEP_GUARD',None,0)]:
            with self.subTest(name=name):
                r=self.runlog([rec(),rec(1)],positive_stats(),'IBDATA_MARKER_OBSERVED',result=name,error=-5,final=final,sweep=sweep);self.assertEqual(r['evidence_errors'],[]);self.assertFalse(r['log_supports_marker']);self.assertEqual(r['evidence']['confirmed'],1)
    def test_valid_regression_record_counts_but_does_not_credit(self):
        p=stats(valid_samples=1,data_seen=1,transport_seen=1,consecutive=1,joint_samples=1,observed_data_seen=1,last_rp=16,first_data_ns=100000,first_data_end_ns=110000,first_transport_ns=100000,first_joint_ns=100000)
        r=self.runlog([rec(),rec(1,rp=8)],p,'IBDATA_MARKER_UNCONFIRMED',result='IBDATA_POINTER_REGRESSION',error=-5,reason='fault',save_last=True)
        self.assertEqual(r['evidence_errors'],[]);self.assertEqual(r['end']['predeadline_records'],2);self.assertEqual(r['evidence']['valid_samples'],1)
    def test_invalid_continuity_wrong_boot_and_summary_tamper_rejected(self):
        raw,s=logfixture([rec(),rec(1)],positive_stats(),'IBDATA_MARKER_OBSERVED')
        for old,new in ((b'accepted=0',b'accepted=1'),(b'confirmed_ns=160000',b'confirmed_ns=170000'),(b'ring_crc=e5b6afa6',b'ring_crc=e5b6afa7'),(b'predeadline=1',b'predeadline=0'),(b'i=1',b'i=2'),(b'Linux version',b'Linux version wrong\nLinux version')):
            with self.subTest(old=old):
                modified=raw.replace(old,new);session={**s,'bytes':len(modified),'end_offset':55+len(modified)};r=e.analyze(modified,session,s['label']);self.assertTrue(r['evidence_errors']);self.assertFalse(r['log_supports_marker'])
        r=e.analyze(raw,{**s,'state':'invalid'},s['label']);self.assertFalse(r['log_supports_marker'])
    def test_missing_saved_late_sample_report_rejected(self):
        raw,s=logfixture([rec(start=e.LIMIT-1,end=e.LIMIT)],stats(observed_data_seen=1,last_rp=16),'IBDATA_NO_TRANSPORT',error=-110,save_last=True)
        raw=b'\n'.join(line for line in raw.split(b'\n') if b'PS4_UVD_IBDATA SAVED' not in line);s.update(bytes=len(raw),end_offset=55+len(raw))
        r=e.analyze(raw,s,s['label']);self.assertIn('Terminal failing/late sample report missing',r['evidence_errors'])
    def test_endpoint_only_counter_forgery_rejected(self):
        raw,s=logfixture([rec(data=e.BASE)],stats(valid_samples=1,transport_seen=1,last_rp=16,first_transport_ns=100000),'IBDATA_DRAINED_NO_MARKER',error=-110)
        modified=raw.replace(b'confirmed=0',b'confirmed=1');r=e.analyze(modified,s,s['label']);self.assertTrue(r['evidence_errors'])



if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path);a=p.parse_args()
    suite=unittest.TestSuite()
    # These inherited cases execute the unchanged functions on temporary files,
    # with command execution mocked. No metadata fixture authorizes production.
    for cls in (AdapterTests,NativePaxTests,ChainTests,RunnerAssertionTests,InstallationStateTests,IBTransactionTests,IBExtractTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    for name in ('test_missing_confirmation_refused_before_inventory','test_media_constraints_and_host_root_refused','test_uart_nested_path_and_invalid_session','test_archive_escape_symlink_duplicate_and_hash_refused'):
        suite.addTest(legacy.GuardTests(name))
    for name in ('test_fixed_wrapper_and_preservation_receipt_with_output_hash','test_regular_input_limit_and_symlink_refused'):
        suite.addTest(legacy.InitramfsReportTests(name))
    stream=io.StringIO();r=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite);print(stream.getvalue(),end='')
    result={'status':'PASS_SYNTHETIC_HANDOFF_TESTS_NO_DEVICES' if r.wasSuccessful() else 'FAIL_SYNTHETIC', 'cases':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'host':{'system':platform.system(),'machine':platform.machine()},'tools':c.tool_pins(),'test_sha256':c.file_sha(Path(__file__)),'hardware_actions':False,'hardware_acceptance':False,'log':stream.getvalue()}
    if a.report:c.save(a.report,result)
    raise SystemExit(not r.wasSuccessful())
