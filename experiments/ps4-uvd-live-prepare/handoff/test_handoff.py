#!/usr/bin/env python3
"""Synthetic preparation-stage and transaction checks; no devices or SSH."""
import argparse
import ast
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import tempfile
import tarfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent


def module(name, path):
    # The production runner intentionally fixes PATH. Restore this test host's
    # environment after import so its synthetic zstd input remains available.
    with patch.dict(os.environ):
        s = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    return m


c = module('preparation_test_common', HERE / 'common.py')
stager = module('preparation_test_stager', HERE / 'prepare_stage.py'); stager.c = c
bt = module('retained_bundle_synthetic', HERE.parent / 'bundle_test.py'); bt.m = c.b
legacy = module('retained_transaction_guards', c.WORKSPACE / 'lab/uvd-2026-10-19/data-control/handoff/test_handoff.py'); legacy.m = c.t
c.configure({'experiment': 'EXP-20990101-999'}); c.t.EXPECTED_RELEASE = c.RELEASE


class Fixture:
    def __init__(self, root, stack):
        self.root = root; self.f = bt.Fixture(root); f = self.f
        self.stage = root / 'new-stage'; self.compatibility = root / 'compatibility.json'; self.disposition = root / 'disposition.md'; self.contract = root / 'observation-contract.json'
        self.receipt = root / 'prior-install.json'; self.complete = root / 'prior-complete.json'
        self.rollback = copy.deepcopy(c.load_json(HERE / 'rollback.json'))
        self.native = {n: ('synthetic native ' + n).encode() for n in c.t.BOOT_FILES}
        self.rollback['old_boot_hashes'] = {n: c.sha(v) for n, v in self.native.items()}; self.rollback['old_bootargs_sha256'] = c.sha(self.native['bootargs.txt'])
        (f.stage / 'old-boot').mkdir()
        for n, data in self.native.items(): bt.put(f.stage / 'old-boot' / n, data)
        bt.put(f.stage / 'bzImage', b'synthetic retained IB kernel')
        f.init_meta.update(base_sha256=c.t.OLD_INITRAMFS, base_bytes=2488567, old_member_sha256=c.t.OLD_FIRMWARE,
                           replacement_sha256=c.t.FIRMWARE_WRAPPER, member=c.t.FIRMWARE_MEMBER, cpio_sha256=c.sha(b'synthetic opaque cpio'))
        bt.put(f.stage / 'initramfs-preparation.json', f.init_meta); f.rebind_stage()
        self.rollback.update(initramfs_sha256=c.sha(f.init), initramfs_preparation_receipt_sha256=c.file_sha(f.stage / 'initramfs-preparation.json'))
        self.current = {**self.native, 'bzImage': b'synthetic retained IB kernel', 'initramfs.cpio.gz': f.init, 'bootargs.txt': f.args}
        self.source = copy.deepcopy(c.load_json(HERE / 'install-source.json'))
        self.source['boot_hashes'] = {n: c.sha(v) for n, v in self.current.items()}
        prior = c.load_json(f.stage / 'plan.json')
        prior.update(experiment=self.source['experiment'], module_tree=self.source['module_tree'], old_boot_hashes=self.rollback['old_boot_hashes'], rollback_module_tree=self.rollback['rollback_module_tree'])
        bt.put(f.stage / 'plan.json', prior)
        bt.put(self.receipt, {'experiment': self.source['experiment'], 'committed': True, 'unmounted': True, 'boot_after': self.source['boot_hashes']})
        bt.put(self.complete, {'experiment': self.source['experiment'], 'status': 'PASS_INSTALLED_UNMOUNTED', 'plan_sha256': c.file_sha(f.stage / 'plan.json'), 'both_read_only_filesystem_checks_passed_before_and_after': True})
        self.source['pins'] = {key: c.file_sha(path) for path, key in ((f.stage / 'plan.json', 'plan_sha256'), (self.receipt, 'install_receipt_sha256'), (self.complete, 'install_complete_sha256'))}
        original_load = c.load_json
        stack.enter_context(patch.object(c, 'load_json', side_effect=lambda p: self.rollback if p == HERE / 'rollback.json' else self.source if p == HERE / 'install-source.json' else original_load(p)))
        stack.enter_context(patch.object(c.t, 'OLD_NATIVE_BOOT_HASHES', self.rollback['old_boot_hashes']))
        for key, value in (('INITRAMFS', c.sha(f.init)), ('INIT_RECEIPT', c.file_sha(f.stage / 'initramfs-preparation.json')), ('IB_BOOTARGS', c.sha(f.args))):
            stack.enter_context(patch.object(c.b, key, value))
        c.b.bundle(f.run, f.review, f.review_sha, f.stage, f.dest)
        bt.put(self.disposition, b'Synthetic compatibility disposition; no hardware qualification.\n')
        args = c.file_sha(f.dest / 'bootargs.new')
        bt.put(self.compatibility, {'status': 'PASS_SCOPED_STATIC_BOOT_COMPATIBILITY_WITH_DEBUGFS_PREREQUISITE', 'hardware_acceptance': False, 'kernel_rebuild_required': False, 'bootargs_sha256': args, 'chosen_initramfs_sha256': c.sha(f.init), 'primary_disposition_sha256': c.file_sha(self.disposition)})
        frozen = {**c.FROZEN, 'bundle_manifest': c.file_sha(f.dest / 'MANIFEST.json'), 'compiled_review': f.review_sha,
                  'source_review': c.file_sha(f.reports / 'review.json'), 'build': c.file_sha(f.reports / 'build.json'), 'package': c.file_sha(f.reports / 'package.json'),
                  'kernel': c.file_sha(f.dest / 'bzImage'), 'config': c.file_sha(f.dest / 'config'), 'bootargs': args, 'compatibility': c.file_sha(self.compatibility)}
        stack.enter_context(patch.object(c, 'FROZEN', frozen))
        tools = {}
        for role in ('acquisition', 'evaluator', 'result_binding'):
            path = root / (role + '.py'); bt.put(path, ('# Synthetic inert tool: ' + role + '\n').encode())
            tools[role] = {'path': str(path), 'sha256': c.file_sha(path)}
        bt.put(self.contract, {'schema': 1, 'status': 'DATA_ONLY_OBSERVATION_CONTRACT', 'release': c.RELEASE, 'bootargs_sha256': args, 'scope': c.OBSERVATION, 'tools': tools})
    def prepare(self, **overrides):
        values = dict(bundle=self.f.dest, bundle_manifest_sha=c.FROZEN['bundle_manifest'], compiled_sha=c.FROZEN['compiled_review'], source_sha=c.FROZEN['source_review'],
                      compatibility=self.compatibility, compatibility_sha=c.FROZEN['compatibility'], disposition=self.disposition, prior=self.f.stage,
                      prior_receipt=self.receipt, prior_complete=self.complete, contract=self.contract, contract_sha=c.file_sha(self.contract), exp='EXP-20990101-998', destination=self.stage)
        values.update(overrides); return stager.prepare(**values)


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='preparation-handoff-synthetic-'); self.addCleanup(self.tmp.cleanup)
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.f = Fixture(Path(self.tmp.name).resolve(), self.stack)
    def refuses_before_destination(self, **kwargs):
        with self.assertRaises((RuntimeError, ValueError)): self.f.prepare(**kwargs)
        self.assertFalse(self.f.stage.exists())
    def test_valid_stage_has_distinct_ib_native_new_and_no_approval(self):
        before = c.file_sha(self.f.f.dest / 'MANIFEST.json'); result = self.f.prepare()
        self.assertEqual(result['status'], 'HOST_STAGE_VERIFIED_NO_DEVICES'); self.assertTrue(result['final_review_required'])
        self.assertFalse(result['installed']); self.assertFalse(result['session_started']); self.assertFalse(result['observation_tools_executed'])
        p, kernel, _, files, old, args = c.load_inputs(self.f.stage)
        self.assertEqual(len(files), 55); self.assertEqual(old, self.f.native); self.assertNotEqual(kernel, self.f.current['bzImage'])
        self.assertEqual((self.f.stage / 'install-from/bzImage').read_bytes(), self.f.current['bzImage'])
        self.assertTrue(args.endswith(b'amdgpu.ps4_uvd_live_prepare=1 debugfs=on\n'))
        self.assertEqual(before, c.file_sha(self.f.f.dest / 'MANIFEST.json')); self.assertFalse((self.f.stage.parent / 'REVIEWED-HANDOFF.json').exists())
        self.assertFalse(p['native_restore_automatic']); self.assertFalse(p['executable_install'])
    def test_existing_stage_is_preserved(self):
        self.f.stage.mkdir(); bt.put(self.f.stage / 'keep', b'owner')
        with self.assertRaises(RuntimeError): self.f.prepare()
        self.assertEqual((self.f.stage / 'keep').read_bytes(), b'owner')
    def test_wrong_caller_pins_rejected(self):
        for key in ('bundle_manifest_sha', 'compiled_sha', 'source_sha', 'compatibility_sha', 'contract_sha'):
            with self.subTest(key=key): self.refuses_before_destination(**{key: '0' * 64})
    def test_missing_result_binding_contract_cannot_stage(self):
        value = c.load_json(self.f.contract); del value['tools']['result_binding']; bt.put(self.f.contract, value); self.refuses_before_destination()
    def test_observation_scope_cannot_claim_hardware(self):
        value = c.load_json(self.f.contract); value['scope']['hardware_acceptance'] = True; bt.put(self.f.contract, value); self.refuses_before_destination()
    def test_changed_live_observation_tool_refused(self):
        bt.put(self.f.root / 'acquisition.py', b'changed'); self.refuses_before_destination()
    def test_changed_prior_installation_or_native_copy_refused(self):
        for path in (self.f.receipt, self.f.f.stage / 'bzImage', self.f.f.stage / 'old-boot/bzImage'):
            before = path.read_bytes(); path.write_bytes(before + b'changed'); self.refuses_before_destination(); path.write_bytes(before)
    def test_changed_bundle_image_config_module_or_receipt_refused(self):
        for name in ('bzImage', 'config', 'modules.tar.zst', 'provenance/native/build.json', 'provenance/compiled-review.json'):
            path = self.f.f.dest / name; before = path.read_bytes(); path.write_bytes(before + b'changed'); self.refuses_before_destination(); path.write_bytes(before)
    def test_changed_external_source_refused(self):
        row = c.load_json(self.f.f.dest / 'MANIFEST.json')['external_archives']['source-' + c.RELEASE + '.tar.zst']; bt.put(Path(row['path']), b'changed'); self.refuses_before_destination()
    def test_symlink_source_refused(self):
        path = self.f.f.dest / 'config'; real = path.with_name('real-config'); path.rename(real); path.symlink_to(real); self.refuses_before_destination()
    def test_each_staged_binding_tamper_fails_before_commands(self):
        self.f.prepare(); p = c.load_json(self.f.stage / 'plan.json')
        for name in p['files']:
            path = self.f.stage / name; before = path.read_bytes(); path.write_bytes(before + b'changed')
            with self.subTest(name=name), patch.object(c.t, 'run', side_effect=AssertionError('No device or module command')):
                with self.assertRaises((RuntimeError, ValueError)): c.load_inputs(self.f.stage)
            path.write_bytes(before)
    def test_plan_cannot_claim_automatic_restore_or_change_media(self):
        self.f.prepare(); p = c.load_json(self.f.stage / 'plan.json')
        for key, value in (('native_restore_automatic', True), ('disk_bytes', p['disk_bytes'] - 1), ('launch_count', 2)):
            with self.subTest(key=key), self.assertRaises(RuntimeError): c.validate_plan({**p, key: value})
    def test_final_review_missing_is_not_implicit_authority(self):
        self.f.prepare()
        with self.assertRaises(RuntimeError): c.validate_reviewed_handoff(self.f.stage, '0' * 64, c.file_sha(self.f.stage / 'plan.json'))
    def test_native_rollback_does_not_require_corrupted_candidate_kernel(self):
        self.f.prepare(); bt.put(self.f.stage / 'bundle/bzImage', b'corrupted')
        p, kernel, initramfs, files, old, _ = c.load_inputs(self.f.stage, rollback=True)
        self.assertIsNone(kernel); self.assertIsNone(initramfs); self.assertEqual(files, {}); self.assertEqual(old, self.f.native)
    def test_copy_failure_preserves_failed_host_stage_without_final_output(self):
        with patch.object(c.b, 'copy_checked', side_effect=OSError('synthetic copy failure')):
            with self.assertRaises(OSError): self.f.prepare()
        self.assertFalse(self.f.stage.exists()); failed = list(self.f.root.glob('.preparation-stage-*/FAILURE.json')); self.assertEqual(len(failed), 1)
        self.assertEqual(c.load_json(failed[0])['status'], 'INCOMPLETE_STAGE_NO_INSTALL_AUTHORITY')
    def test_no_observation_code_executed(self):
        real = c.b.subprocess.Popen; calls = []
        def popen(argv, *a, **kw): calls.append(argv); return real(argv, *a, **kw)
        with patch.object(c.b.subprocess, 'Popen', side_effect=popen): self.f.prepare()
        self.assertTrue(calls); self.assertTrue(all(Path(x[0]).name == 'zstd' and '-d' in x for x in calls))


class ReuseTests(unittest.TestCase):
    def test_exact_operational_reuse_and_label_only_changes(self):
        def functions(path):
            text = path.read_text(); lines = text.splitlines(keepends=True)
            return {n.name: ''.join(lines[n.lineno - 1:n.end_lineno]) for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)}
        old = HERE.parents[1] / 'ps4-uvd-ib-data/handoff'
        a, z = functions(old / 'transaction.py'), functions(HERE / 'transaction.py')
        changed = {'install_boot_hashes', 'known_recovery_states', 'verify_install_source_modules', 'rollback_hashes', 'main'}
        self.assertEqual(set(a), set(z)); self.assertEqual({n for n in a if a[n] != z[n]}, changed)
        substitutions = [('retained Revision C installation source', 'retained IB-DATA installation source'), ('Exact C-to-IB commit states', 'Exact IB-to-preparation commit states'), ('retained C release', 'retained IB-DATA release'), ('explicit C/IB/native transaction phases', 'explicit IB/preparation/native transaction phases'), ('retain vram/release and both module trees.', 'retain vram/release and all prior module trees.')]
        for name in changed:
            expected = a[name]
            for before, after in substitutions: expected = expected.replace(before, after)
            self.assertEqual(expected, z[name])
        self.assertEqual(functions(old / 'run_test.py'), functions(HERE / 'run_test.py'))
    def test_invalid_experiment_does_not_allocate_anything(self):
        for value in ('EXP-20261301-001', 'EXP-20261020-000', '../EXP-20261020-003', 'not-assigned'):
            with self.assertRaises((RuntimeError, ValueError)): c.experiment(value)


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


class PreparationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.boot=Path(self.tmp.name)
        self.native={n:('native '+n).encode() for n in c.t.BOOT_FILES}
        self.current={n:('retained-ib '+n).encode() for n in c.t.BOOT_FILES}
        for n in c.t.KEEP_FILES:self.current[n]=self.native[n]
        self.kernel=b'preparation kernel';self.initramfs=self.current['initramfs.cpio.gz'];self.args=b'preparation args'
        self.plan={'old_boot_hashes':{n:c.sha(v) for n,v in self.native.items()},'install_boot_hashes':{n:c.sha(v) for n,v in self.current.items()},'kernel_sha256':c.sha(self.kernel),'initramfs_sha256':c.sha(self.initramfs),'old_bootargs_sha256':c.sha(self.native['bootargs.txt']),'new_bootargs_sha256':c.sha(self.args)}
        for n,v in self.current.items():(self.boot/n).write_bytes(v)
        self.real_replace=c.t.os.replace
    def recovery(self):
        d=self.boot/('psx-recovery-'+c.t.EXP);d.mkdir()
        for n,v in self.native.items():(d/n).write_bytes(v)
        return d
    def test_install_from_ib_saves_native_not_ib(self):
        order=[]
        def replace(src,dst):
            self.assertEqual({n:(self.boot/('psx-recovery-'+c.t.EXP)/n).read_bytes() for n in c.t.BOOT_FILES},self.native)
            order.append(Path(dst).name);self.real_replace(src,dst)
        with patch.object(c.t,'run',return_value=''),patch.object(c.t.os,'replace',side_effect=replace):
            result=c.t.install_boot(self.boot,self.plan,self.kernel,self.initramfs,self.native,self.args)
        self.assertEqual(order,['initramfs.cpio.gz','bootargs.txt','bzImage'])
        self.assertEqual(result['bzImage'],c.sha(self.kernel))
    def test_native_or_unknown_is_not_install_from_ib(self):
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
    def test_prior_ib_recovery_is_preserved(self):
        prior=self.boot/'psx-recovery-EXP-20261020-002';prior.mkdir();(prior/'keep').write_bytes(b'prior')
        with patch.object(c.t,'run',return_value=''):c.t.install_boot(self.boot,self.plan,self.kernel,self.initramfs,self.native,self.args)
        self.assertEqual((prior/'keep').read_bytes(),b'prior')



if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--report', type=Path, required=True); args = p.parse_args()
    suite = unittest.TestSuite()
    for cls in (ReuseTests, StageTests, NativePaxTests, RunnerAssertionTests, InstallationStateTests, PreparationTransactionTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    for name in ('test_missing_confirmation_refused_before_inventory', 'test_media_constraints_and_host_root_refused', 'test_uart_nested_path_and_invalid_session', 'test_archive_escape_symlink_duplicate_and_hash_refused'):
        suite.addTest(legacy.GuardTests(name))
    for name in ('test_fixed_wrapper_and_preservation_receipt_with_output_hash', 'test_regular_input_limit_and_symlink_refused'):
        suite.addTest(legacy.InitramfsReportTests(name))
    stream = io.StringIO(); result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    print(stream.getvalue(), end='')
    dependencies = {'bundle_test.py': HERE.parent / 'bundle_test.py', 'retained_guard_tests': c.WORKSPACE / 'lab/uvd-2026-10-19/data-control/handoff/test_handoff.py', 'retained_transaction': HERE.parents[1] / 'ps4-uvd-ib-data/handoff/transaction.py', 'retained_runner': HERE.parents[1] / 'ps4-uvd-ib-data/handoff/run_test.py'}
    c.save(args.report, {'status': 'PASS_SYNTHETIC_HANDOFF_TESTS_NO_DEVICES' if result.wasSuccessful() else 'FAIL_SYNTHETIC', 'cases': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'host': {'system': platform.system(), 'machine': platform.machine()}, 'tools': c.tool_pins(), 'test_sha256': c.file_sha(Path(__file__)), 'test_dependencies': {n: {'path': str(p), 'sha256': c.file_sha(p)} for n, p in dependencies.items()}, 'hardware_actions': False, 'ssh_actions': False, 'observation_tools_executed': False, 'hardware_acceptance': False, 'log': stream.getvalue()})
    raise SystemExit(not result.wasSuccessful())
