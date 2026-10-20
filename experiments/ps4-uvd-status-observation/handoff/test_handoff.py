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
    put=lambda n,v: c.save(stage/n,v)
    oldbytes={n:('synthetic '+n+'\n').encode() for n in c.t.BOOT_FILES};oldbytes['bootargs.txt']=b'root=synthetic\n'
    rollback=c.load_json(HERE/'rollback.json');rollback['old_boot_hashes']={n:c.sha(v) for n,v in oldbytes.items()};rollback['old_bootargs_sha256']=c.sha(oldbytes['bootargs.txt'])
    (stage/'old-boot').mkdir()
    for n,v in oldbytes.items(): c.write(stage/'old-boot'/n,v)
    prefix='lib/modules/'+c.RELEASE+'/'
    tree={prefix+(f'kernel/{i:02d}.ko' if i<41 else f'modules.synthetic{i}'): {'sha256':c.sha(f'file{i}'.encode()),'kind':'file','bytes':5,'mode':420} for i in range(55)}
    kernel=bytearray(600);kernel[0x202:0x206]=b'HdrS'
    blobs={'bzImage':bytes(kernel),'config':b'synthetic config','modules.tar.zst':b'synthetic module archive','inputs.tar.zst':b'synthetic inputs archive','bootargs.new':c.new_bootargs(oldbytes['bootargs.txt']),
           'initramfs.cpio.gz':b'synthetic opaque initramfs','initramfs-preparation.json':b'{}\n'}
    for n,v in blobs.items(): c.write(stage/n,v)
    rollback['initramfs_sha256']=c.sha(blobs['initramfs.cpio.gz']);rollback['initramfs_preparation_receipt_sha256']=c.sha(blobs['initramfs-preparation.json'])
    primary={'final_primary_review':True,'verdict':'APPROVED_FOR_NATIVE_BUILD_ONLY','unresolved_findings':[],'target':c.TARGET,'release':c.RELEASE,'host_cases':202,'sensitivity_count':16,'pins':{'candidate':{'sha256':c.CANDIDATE},'patch':{'sha256':c.PATCH}}};put('primary-review.json',primary)
    rh=c.file_sha(stage/'primary-review.json');put('identity.json',{'review_sha256':rh});ih=c.file_sha(stage/'identity.json')
    shared={'target':c.TARGET,'release':c.RELEASE,'review_sha256':rh,'baseline_preserved':True,'hardware_acceptance':False,'identity_sha256':ih}
    put('prepare-base.json',{**shared,'status':'PASS_PREPARE_BASE'})
    put('prepare.json',{**shared,'status':'PASS_PREPARE','previous_phase_receipt_sha256':c.file_sha(stage/'prepare-base.json')})
    put('module-tree.json',tree);put('project-inputs.json',{'synthetic':{'kind':'file'}})
    manifest={'target':c.TARGET,'release':c.RELEASE,'active_patch_count':71,'review_sha256':rh,'patch_sha256':c.PATCH,'irq_source_sha256':c.IRQ,'hardware_acceptance':False,'project_base_commit':'35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2','project_inputs_sha256':c.file_sha(stage/'project-inputs.json')};put('manifest.json',manifest)
    build={**shared,'status':'PASS_BUILD','previous_phase_receipt_sha256':c.file_sha(stage/'prepare.json'),'artifact_sha256':{n:c.file_sha(stage/n) for n in ('bzImage','config','manifest.json')},'module_count':41,'module_tree_sha256':c.file_sha(stage/'module-tree.json'),'vmlinux_sha256':c.sha(b'linked kernel')};put('build.json',build)
    artifacts={'kernel_release':c.RELEASE,'hardware_acceptance':'none','embedded_firmware':False,'module_count':41,'active_patch_count':71,'review_sha256':rh,'kernel_sha256':c.file_sha(stage/'bzImage'),'kernel_config_sha256':c.file_sha(stage/'config'),'modules_archive':{'sha256':c.file_sha(stage/'modules.tar.zst')},'inputs_archive':{'sha256':c.file_sha(stage/'inputs.tar.zst')},'source_archive':{'sha256':c.sha(b'source archive')}};put('artifacts.json',artifacts)
    renamed={'modules.tar.zst':'modules-'+c.RELEASE+'.tar.zst'}
    package={**shared,'status':'PASS_PACKAGE','previous_phase_receipt_sha256':c.file_sha(stage/'build.json'),'artifacts':artifacts,'artifact_sha256':{renamed.get(n,n):c.file_sha(stage/n) for n in ('bzImage','config','manifest.json','artifacts.json','modules.tar.zst','inputs.tar.zst')},'archive_verification':{'modules':{'regular_files':55,'members_verified':55},'inputs':{'regular_files':2,'members_verified':2},'source':{'regular_files':3,'members_verified':3}}};put('package.json',package)
    compiled={'status':'PASS_SCOPED_COMPILED_REVIEW_NO_HARDWARE_ACCEPTANCE','target':c.TARGET,'release':c.RELEASE,'patch_sha256':c.PATCH,'ready_source_sha256':c.CANDIDATE,'ready_object_sha256':c.sha(b'object'),'vmlinux_sha256':build['vmlinux_sha256'],'kernel_sha256':artifacts['kernel_sha256'],'config_sha256':artifacts['kernel_config_sha256'],'modules_archive_sha256':artifacts['modules_archive']['sha256'],'native_build_receipt_sha256':c.file_sha(stage/'build.json'),'source_review_sha256':rh,'hardware_acceptance':False,'must_fix_findings':[],'prior_interrupted_reviews_remain_incomplete':True};put('compiled-review.json',compiled)
    p={**rollback,'schema':1,'status':'HOST_STAGED_PENDING_FINAL_REVIEW','experiment':'EXP-20261020-999','target':c.TARGET,'release':c.RELEASE,'candidate_sha256':c.CANDIDATE,'patch_sha256':c.PATCH,'hardware_acceptance':False,'launch_count':1,'launch_timeout_seconds':180,'installation_state':{'usb':'attached-to-Omarchynator','console':['orbis-idle','fully-off'],'operator_holds_linux_launch':True,'fresh_full_poweroff_required_for_post_test_rollback':True},'observation':{'max_records':40,'deadline_ns':250000000,'publication_count':1,'ring_crc32':'cadc2d79','accepted':0},'tools':c.tool_pins(),'module_tree':{n:r['sha256'] for n,r in tree.items()},'source_archive_sha256':artifacts['source_archive']['sha256']}
    p['files']={x.name:c.file_sha(x) for x in stage.iterdir() if x.is_file()}
    for n,k in (('bzImage','kernel_sha256'),('config','config_sha256'),('modules.tar.zst','modules_archive_sha256'),('bootargs.new','new_bootargs_sha256')):p[k]=p['files'][n]
    put('plan.json',p)
    return p,rollback


class AdapterTests(unittest.TestCase):
    def test_retained_transaction_functions_exact(self):
        def funcs(path):
            text=path.read_text();lines=text.splitlines(keepends=True)
            return {n.name:''.join(lines[n.lineno-1:n.end_lineno]) for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)}
        a,b=funcs(old/'install_usb.py'),funcs(HERE/'transaction.py')
        self.assertEqual(len(b),23)
        for n in b:
            if n not in ('main','validate_archive'): self.assertEqual(a[n],b[n],n)
        self.assertNotEqual(a['main'],b['main'])
        self.assertNotEqual(a['validate_archive'],b['validate_archive'])
    def test_bootargs_only_gate_change(self):
        a=(old/'stage/old-boot/bootargs.txt').read_bytes();b=(old/'stage/bootargs.new').read_bytes()
        self.assertEqual(c.new_bootargs(a),b.replace(b'amdgpu.ps4_uvd_data_control=1',b'amdgpu.ps4_uvd_status_observation=1'))
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
        self.plan,self.rollback=build_fixture(self.stage)
        load=c.load_json
        self.addCleanup(patch.stopall)
        patch.object(c,'load_json',side_effect=lambda p:self.rollback if p==HERE/'rollback.json' else load(p)).start()
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
    def test_runner_accepts_orbis_and_records_truthful_state(self):
        runner=module('stobs_runner_positive',HERE/'run_test.py')
        with tempfile.TemporaryDirectory() as tmp:
            stage=Path(tmp)/'stage';stage.mkdir()
            exp='EXP-'+runner.datetime.datetime.now(runner.datetime.timezone(runner.datetime.timedelta(hours=6))).strftime('%Y%m%d')+'-999'
            plan={'experiment':exp}
            disk={'path':'/dev/synthetic','serial':c.t.SERIAL}
            parts={role:{'path':'/dev/synthetic-'+role} for role in ('boot','root')}
            calls=[]
            def command(argv,report=None):
                words=[str(x) for x in argv];calls.append(words)
                if words[-1]=='status':
                    return 'No active debugging session.\n' if 'session' in words else '[+] Status:  READY\n'
                if '--apply' in words:return json.dumps({'committed':True,'unmounted':True})
                return ''
            argv=['run_test.py','install','--stage',str(stage),'--plan-sha256','a'*64,'--review-sha256','b'*64,'--confirm-usb-on-host-console-idle','--console-state','orbis-idle','--state-evidence','Owner: Orbis idle; USB on host; Linux launch held. Prior EXP005 full-off confirmed.']
            with patch.object(runner.sys,'argv',argv),patch.object(runner.sys,'platform','linux'),patch.object(runner.os,'geteuid',return_value=1000),patch.object(runner,'sha',return_value='a'*64),patch.object(runner.c,'validate_reviewed_handoff',return_value={}),patch.object(runner.c,'load_inputs',return_value=(plan,None,None,{},None,None)),patch.object(runner.c.t,'identify',return_value=(disk,parts)),patch.object(runner.c.t,'uart_ready',return_value={}),patch.object(runner,'WORKSPACE',Path(tmp)),patch.object(runner,'command',side_effect=command),patch.object(runner.subprocess,'run',return_value=SimpleNamespace(returncode=0)),contextlib.redirect_stdout(io.StringIO()):
                runner.main()
            start=json.loads((stage.parent/'runtime/install-started.json').read_text())
            self.assertEqual(start['operator_reported_console_state'],'orbis-idle')
            self.assertFalse(start['fresh_poweroff_and_usb_return_confirmation'])
            self.assertTrue(start['operator_holds_linux_launch'])
            apply=next(x for x in calls if '--apply' in x)
            self.assertIn('--confirm-usb-on-host-console-idle',apply);self.assertNotIn('--confirm-poweroff-and-usb-return',apply)
            self.assertTrue((stage.parent/'runtime/install-complete.json').exists())
    def test_direct_installer_missing_host_assertion_before_inventory(self):
        with patch.object(c.t.sys,'argv',['install_usb.py','--apply','/synthetic-nonexistent','--confirm-serial',c.t.SERIAL]),patch.object(c.t.sys,'platform','linux'),patch.object(c.t.os,'geteuid',return_value=0),patch.object(c.t,'identify',side_effect=AssertionError('No device read')) as identify,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):c.t.main()
            identify.assert_not_called()


class InstallationStateTests(unittest.TestCase):
    def test_idle_orbis_install_modules_precede_boot_commit(self):
        m = c.t
        with tempfile.TemporaryDirectory(prefix="psx-flow-") as tmp:
            base = Path(tmp); stage = base / "stage"; stage.mkdir()
            (stage / "plan.json").write_bytes(b"synthetic reviewed plan")
            private = base / "private"; private.mkdir()
            plan = {"release": c.t.EXPECTED_RELEASE, "boot_uuid": c.t.MEDIA["boot_uuid"],
                    "old_boot_hashes": {"bzImage": "old", "initramfs.cpio.gz": "oldinit"}, "old_bootargs_sha256": "oldargs"}
            disk = {"path": "/dev/synthetic"}
            parts = {role: {"path": "/dev/synthetic" + role, "size": 1, "uuid": role,
                            "fstype": "ext4" if role == "root" else "vfat", "mounts": [], "maj:min": role}
                     for role in ("root", "boot")}
            order = []
            with patch.object(c.t.sys, "argv", ["install_usb.py", "--apply", str(stage), "--confirm-serial", c.t.SERIAL,
                              "--reviewed-plan-sha256", c.t.sha(b"synthetic reviewed plan"),
                              "--reviewed-handoff-sha256", "1" * 64, "--confirm-usb-on-host-console-idle", "--console-state", "orbis-idle"]), \
                 patch.object(c.t.sys, "platform", "linux"), patch.object(c.t.os, "geteuid", return_value=0), \
                 patch.object(m, "validate_reviewed_handoff", return_value={}), \
                 patch.object(m, "load_inputs", return_value=(plan, b"kernel", b"initramfs", {"a.ko": b"module"}, {}, b"args")), \
                 patch.object(m, "identify", return_value=(disk, parts)), \
                 patch.object(m, "uart_ready", return_value={"synthetic": True}), \
                 patch.object(c.t.ctypes, "CDLL", return_value=SimpleNamespace(unshare=lambda flags: 0)), \
                 patch.object(c.t.tempfile, "mkdtemp", return_value=str(private)), \
                 patch.object(m, "boot_hashes", return_value={}), patch.object(m, "preflight_paths"), \
                 patch.object(m, "module_parent"), patch.object(m, "verify_rollback_modules", return_value=41), \
                 patch.object(m, "install_modules", side_effect=lambda *a: order.append("modules") or []), \
                 patch.object(m, "install_boot", side_effect=lambda *a: order.append("initramfs then bootargs then kernel") or {}), \
                 patch.object(m, "run", return_value=""), patch.object(c.t.os.path, "ismount", return_value=False), \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                c.t.main()
            self.assertEqual(order, ["modules", "initramfs then bootargs then kernel"])
            self.assertTrue(json.loads(out.getvalue())["committed"])

            self.assertEqual(json.loads(out.getvalue())["operator_reported_console_state"], "orbis-idle")
            self.assertFalse(json.loads(out.getvalue())["operator_poweroff_and_usb_return_confirmed"])

    def test_launching_console_refused_before_inventory(self):
        for state,flag in (("linux-launching",True),("orbis-idle",False)):
            argv=["install_usb.py","--apply","/synthetic-nonexistent","--confirm-serial",c.t.SERIAL,"--console-state",state]
            if flag: argv.append("--confirm-usb-on-host-console-idle")
            with self.subTest(state=state,flag=flag),patch.object(c.t.sys,"argv",argv),patch.object(c.t.sys,"platform","linux"),patch.object(c.t.os,"geteuid",return_value=0),patch.object(c.t,"identify",side_effect=AssertionError("No inventory")) as identify,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):c.t.main()
                identify.assert_not_called()


def logs(bits=(1,1),error=0,result=None,sweep=1):
    cls='STOBS_BIT16_PERSISTENT' if bits==(1,1) else 'STOBS_BIT16_TRANSIENT' if bits==(1,0) else 'STOBS_BIT16_TOGGLING' if bits==(1,0,1) else 'STOBS_BIT16_ABSENT_MARKERS'
    rec=[]
    for i,bit in enumerate(bits):
        rec.append(f'PS4_UVD_STOBS REC i={i} elapsed_us={100+i*20} t_end_us={105+i*20} status={2+(bit<<16):08x} int=00000002 rbc=1100080c ib=00000000 wp=00000010 rp=00000010 ctx=43545835 data=55564435 wpc=00000000 rpa=00000000 fault=00000000 reset=0 in_reset=0 pd=00003000 bit16={bit}')
    sets=1 if bits!=(1,0,1) else 2;clears=1 if 0 in bits and 1 in bits else 0;first=0 if 1 in bits else -1
    if not 1 in bits:sets=0
    end=f'PS4_UVD_STOBS END result={result or cls} log_class={cls} enabled=1 submitted=1 elapsed_ns=200000 publish_ns=90000 records={len(bits)} fine={len(bits)} bit16_first={first} onset_ns={100000 if first==0 else 0} sets={sets} bit16_clears={clears} first_clear={1 if clears else -1} markers=1 persistent={int(bits==(1,1))} onset_with_values={int(first==0)} data_regressions=0 context_regressions=0 accepted=0 last_wp=16 last_rp=16 last_data=55564435 last_ctx=43545835 final_valid=1 sweep_valid={sweep}; scope'
    raw=('Linux version '+c.RELEASE+' synthetic\nPS4_UVD_STOBS PREPARED bytes=4096\nPS4_UVD_STOBS PUBLICATION_BEGIN synthetic\n'+'\n'.join(rec)+'\nPS4_UVD_STOBS FINAL_CORE sampled=all_then_data0 valid=1\n'+f'PS4_UVD_STOBS CTX_SWEEP valid={sweep} kind=0\n'+end+f'\nPS4_UVD_READY RESULT={result or cls} error={error} PARKED retained\n').encode()
    session={'label':'EXP-20261020-999','state':'completed','continuity_errors':[],'bytes':len(raw),'start_offset':50,'end_offset':50+len(raw),'capture_generation':'synthetic-generation','connection_epoch':1}
    return raw,session


class ExtractTests(unittest.TestCase):
    def test_four_classes_without_acceptance(self):
        for bits,cls in (((1,1),'PERSISTENT'),((1,0),'TRANSIENT'),((1,0,1),'TOGGLING'),((0,0),'ABSENT_MARKERS')):
            with self.subTest(bits=bits):
                raw,s=logs(bits);r=e.analyze(raw,s,s['label']);self.assertEqual(r['evidence_errors'],[]);self.assertTrue(r['qualified_class_observation']);self.assertFalse(r['hardware_acceptance']);self.assertTrue(r['end']['log_class'].endswith(cls))
    def test_sweep_failure_is_error_with_descriptive_class(self):
        raw,s=logs(error=-5,result='STOBS_CTX_SWEEP_GUARD',sweep=0);r=e.analyze(raw,s,s['label'])
        self.assertFalse(r['qualified_class_observation']);self.assertEqual(r['end']['log_class'],'STOBS_BIT16_PERSISTENT');self.assertEqual(r['status'],'ERROR_OR_UNQUALIFIED_OBSERVATION')
    def test_invalid_continuity_never_qualifies(self):
        raw,s=logs();s['state']='invalid';r=e.analyze(raw,s,s['label']);self.assertFalse(r['qualified_class_observation'])
    def test_corrupt_summaries_and_repeated_boot_rejected(self):
        raw,s=logs()
        for source,target in ((b'accepted=0',b'accepted=1'),(b'records=2',b'records=3'),(b'bit16_clears=0',b'bit16_clears=1'),(b'i=1',b'i=2'),(b't_end_us=105',b't_end_us=99'),(b'Linux version',b'Linux version wrong\nLinux version')):
            with self.subTest(source=source):
                x=raw.replace(source,target);ss={**s,'bytes':len(x),'end_offset':50+len(x)};r=e.analyze(x,ss,ss['label']);self.assertFalse(r['qualified_class_observation']);self.assertTrue(r['evidence_errors'])
    def test_early_park_keeps_no_claim(self):
        raw,s=logs();raw=('Linux version '+c.RELEASE+' synthetic\nPS4_UVD_READY RESULT=STOBS_DESCRIPTOR_OR_GATES error=-22 PARKED retained\n').encode();s.update(bytes=len(raw),end_offset=50+len(raw));r=e.analyze(raw,s,s['label']);self.assertEqual(r['status'],'EARLY_TERMINAL_NO_OBSERVATION')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path);a=p.parse_args()
    suite=unittest.TestSuite()
    # These inherited cases execute the unchanged functions on temporary files,
    # with command execution mocked. No metadata fixture authorizes production.
    for cls in (legacy.TransactionTests,legacy.ThreePhaseTests,AdapterTests,NativePaxTests,ChainTests,RunnerAssertionTests,InstallationStateTests,ExtractTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    for name in ('test_missing_confirmation_refused_before_inventory','test_media_constraints_and_host_root_refused','test_uart_nested_path_and_invalid_session','test_archive_escape_symlink_duplicate_and_hash_refused'):
        suite.addTest(legacy.GuardTests(name))
    for name in ('test_fixed_wrapper_and_preservation_receipt_with_output_hash','test_regular_input_limit_and_symlink_refused'):
        suite.addTest(legacy.InitramfsReportTests(name))
    stream=io.StringIO();r=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite);print(stream.getvalue(),end='')
    result={'status':'PASS_SYNTHETIC_HANDOFF_TESTS_NO_DEVICES' if r.wasSuccessful() else 'FAIL_SYNTHETIC', 'cases':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'host':{'system':platform.system(),'machine':platform.machine()},'tools':c.tool_pins(),'test_sha256':c.file_sha(Path(__file__)),'hardware_actions':False,'hardware_acceptance':False,'log':stream.getvalue()}
    if a.report:c.save(a.report,result)
    raise SystemExit(not r.wasSuccessful())
