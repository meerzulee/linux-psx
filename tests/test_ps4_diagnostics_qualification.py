"""Qualification schema/evidence checks; never execute diagnostic workloads."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('psx_qualification_tests',ROOT/'diagnostics/ps4/qualification.py')
Q=importlib.util.module_from_spec(spec);spec.loader.exec_module(Q)


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.plan=Q.validate(Q.read_json(Q.PLAN))
        self.scope={key:'1'*64 for key in Q.HASH_FIELDS}
        self.scope.update(console_model='CUH-fixture',southbridge='Baikal',boot_chain='fixture loader',
            kernel_release='fixture-psx',uvd_runtime=False,mesa_manifest_sha256='2'*64,firmware_manifest_sha256=None)

    def record(self,id):
        item=next(item for item in self.plan['checks'] if item['id']==id);gate=item['acceptance']
        metrics=dict(gate['metrics_minimum']);metrics.update(gate['metrics_maximum'])
        return dict(id=id,status='PASS',reviewed_by='fixture reviewer',evidence=['private fixture receipt'],
            passed_oracles=gate['oracles'],runs=[dict(run_id=f'{id}-run-{i}',boot_id=f'boot-{i}',cold_boot=True,
                scope_id=Q.scope_id(self.scope),status='PASS',seconds=gate['seconds_per_run'],uart_continuity='completed',metrics=metrics.copy()) for i in range(gate['runs'])])

    def document(self,*records):return dict(schema_version=1,scope=self.scope.copy(),checks=list(records))
    def row(self,report,id):return next(item for item in report['checks'] if item['id']==id)

    def isolated(self,id):
        plan=copy.deepcopy(self.plan);item=next(item for item in plan['checks'] if item['id']==id)
        item['depends_on']=[];plan['profiles']['isolated']={'title':'Synthetic isolated gate','required':[id]}
        return Q.validate(plan)

    def test_real_plan_is_dependency_ordered_with_executable_and_explicit_gap_modes(self):
        done=set()
        for item in Q.ordered(self.plan):
            self.assertTrue(set(item['depends_on'])<=done);done.add(item['id'])
        self.assertGreaterEqual(len(done),40)
        self.assertEqual({item['mode'] for item in self.plan['checks']},Q.MODES)
        for id in ('audio.native48','power.suspend','uvd.lifecycle','uvd.admission','sata.inventory','browser.firefox','browser.chromium'):
            self.assertIn(id,done)

    def test_cycle_dangling_and_missing_mandatory_dependency_are_rejected(self):
        for change in ('cycle','dangling','profile'):
            plan=copy.deepcopy(self.plan)
            if change=='cycle':plan['checks'][0]['depends_on']=['boot.cold']
            elif change=='dangling':plan['checks'][0]['depends_on']=['missing.check']
            else:plan['profiles']['kernel-baseline']['required'].remove('artifacts.identity')
            with self.subTest(change=change),self.assertRaises(ValueError):Q.validate(plan)

    def test_nonexecutable_placeholders_and_arbitrary_commands_cannot_be_automation(self):
        for binding in ({'api':'runner','test_id':'power.off'},{'api':'runner','test_id':'uvd.avc'}, {'command':['reboot']}):
            plan=copy.deepcopy(self.plan);plan['checks'][0]['automation']=[binding]
            with self.subTest(binding=binding),self.assertRaises(ValueError):Q.validate(plan)

    def test_optional_skipped_or_deferred_is_never_pass_or_an_ordinary_blocker(self):
        report=Q.evaluate(self.plan,'kernel-baseline',self.document())
        self.assertEqual(self.row(report,'wireless.wifi')['status'],'DEFERRED')
        self.assertNotIn('wireless.wifi',report['mandatory_blockers'])
        self.assertNotIn('audio.native48',report['mandatory_blockers'])
        self.assertEqual(self.row(report,'source.audit')['status'],'BLOCKED')
        with self.assertRaises(ValueError):Q.evaluate(self.plan,'kernel-baseline',self.document(dict(self.record('artifacts.identity'),status='SKIPPED')))

    def test_ordinary_uvd_false_is_unsupported_not_a_decode_failure(self):
        report=Q.evaluate(self.plan,'uvd-experimental',self.document())
        row=self.row(report,'uvd.avc.exact')
        self.assertEqual(row['status'],'UNSUPPORTED');self.assertIn('uvd.avc.exact',report['mandatory_blockers'])
        self.assertIn('not failed',row['reasons'][0]);self.assertFalse(report['stability_claim'])

    def test_experimental_uvd_scope_cannot_qualify_an_ordinary_profile(self):
        self.scope['uvd_runtime']=True
        for profile in ('kernel-baseline','desktop-integration'):
            plan=copy.deepcopy(self.plan);plan['profiles'][profile]['required']=['artifacts.identity']
            report=Q.evaluate(plan,profile,self.document(self.record('artifacts.identity')))
            self.assertEqual(report['mandatory_blockers'],['profile.scope'])
            self.assertFalse(report['ready_for_human_review']);self.assertEqual(report['status'],'BLOCKED')
            self.assertIn('retained-DMA',report['profile_blockers'][0])

    def test_one_short_or_one_boot_report_cannot_satisfy_repetition(self):
        plan=self.isolated('cpu.sustained');record=self.record('cpu.sustained')
        variants=[dict(record,runs=record['runs'][:1]),copy.deepcopy(record),copy.deepcopy(record)]
        for run in variants[1]['runs']:run['boot_id']='same-boot'
        for run in variants[2]['runs']:run['seconds']=1
        for record in variants:
            report=Q.evaluate(plan,'isolated',self.document(record))
            self.assertEqual(self.row(report,'cpu.sustained')['status'],'BLOCKED')

    def test_changed_build_invalid_uart_missing_counter_and_false_oracle_block(self):
        plan=self.isolated('gpu.opengl')
        for change in ('scope','uart','metric','oracle','reviewer','mesa'):
            record=self.record('gpu.opengl');document=self.document(record)
            if change=='scope':document['scope']['kernel_sha256']='3'*64
            elif change=='uart':record['runs'][0]['uart_continuity']='invalid'
            elif change=='metric':record['runs'][0]['metrics'].pop('pixel_mismatches')
            elif change=='oracle':record['passed_oracles']=[]
            elif change=='reviewer':record['reviewed_by']=''
            else:
                document['scope']['mesa_manifest_sha256']=None
                for run in record['runs']:run['scope_id']=Q.scope_id(document['scope'])
            with self.subTest(change=change):
                self.assertEqual(self.row(Q.evaluate(plan,'isolated',document),'gpu.opengl')['status'],'BLOCKED')

    def test_unimplemented_pass_cannot_enable_hardware_and_dependencies_still_block(self):
        plan=self.isolated('source.audit')
        self.assertEqual(self.row(Q.evaluate(plan,'isolated',self.document(self.record('source.audit'))),'source.audit')['status'],'BLOCKED')
        report=Q.evaluate(self.plan,'kernel-baseline',self.document(self.record('boot.cold')))
        self.assertEqual(self.row(report,'boot.cold')['status'],'BLOCKED')
        self.assertTrue(any('Dependencies' in reason for reason in self.row(report,'boot.cold')['reasons']))

    def test_complete_declared_gate_only_becomes_ready_for_human_review(self):
        report=Q.evaluate(self.isolated('artifacts.identity'),'isolated',self.document(self.record('artifacts.identity')))
        self.assertEqual(report['mandatory_blockers'],[]);self.assertTrue(report['ready_for_human_review'])
        self.assertEqual(report['status'],'INCONCLUSIVE');self.assertFalse(report['hardware_acceptance']);self.assertFalse(report['stability_claim'])

    def test_duplicate_runs_raw_runner_reports_and_nonfinite_values_are_rejected(self):
        record=self.record('artifacts.identity');record['runs'][1]['run_id']=record['runs'][0]['run_id']
        with self.assertRaises(ValueError):Q.evaluate(self.plan,'kernel-baseline',self.document(record))
        with self.assertRaises(ValueError):Q.evaluate(self.plan,'kernel-baseline',self.document({'test_id':'cpu.compute','status':'PASS'}))
        record=self.record('artifacts.identity');record['runs'][0]['seconds']=float('inf')
        with self.assertRaises(ValueError):Q.evaluate(self.plan,'kernel-baseline',self.document(record))

    def test_default_and_json_cli_are_read_only_and_human_readable(self):
        for argv in ([],['--json'],['--validate']):
            output=io.StringIO()
            with mock.patch('subprocess.run',side_effect=AssertionError('hardware command')), \
                 mock.patch('socket.socket',side_effect=AssertionError('network')),contextlib.redirect_stdout(output):
                self.assertEqual(Q.main(argv),0)
            if argv==['--json']:self.assertIn('assessment',json.loads(output.getvalue()))
            else:self.assertNotIn('"checks": [',output.getvalue())

    def test_device_and_symlink_inputs_are_rejected_before_open(self):
        with mock.patch.object(Q.os,'open',side_effect=AssertionError('device opened')):
            with self.assertRaises(ValueError):Q.read_json('/dev/null')
            with self.assertRaises(ValueError):Q.read_json('/proc/version')
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            path=Path(temporary);(path/'file').write_text('{}');(path/'link').symlink_to(path/'file')
            with self.assertRaises(ValueError):Q.read_json(path/'link')

    def test_scenarios_do_not_change_the_frozen_45_checks_profiles_or_acceptance(self):
        protected={'profiles':self.plan['profiles'],
            'checks':[{key:value for key,value in item.items() if key!='scenarios'} for item in self.plan['checks']]}
        digest=hashlib.sha256(json.dumps(protected,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        # Original definitions before adding descriptive scenarios; all gates stay exact.
        self.assertEqual(digest,'3ad4a40d856a584a685debbb2a0ca0ebfa34f68bd4ee4e7e2a4609f4e857928d')
        self.assertEqual(len(self.plan['checks']),45)
        scenarios=[scenario for item in self.plan['checks'] for scenario in item.get('scenarios',[])]
        self.assertEqual(len(scenarios),6)
        available=[scenario for scenario in scenarios if scenario['availability']=='available-alpha']
        self.assertEqual([scenario['existing_test_id'] for scenario in available],['uvd.avc'])
        self.assertTrue(all(scenario['existing_test_id'] is None for scenario in scenarios if scenario not in available))

    def test_invalid_scenario_availability_duplicate_ids_and_executable_fields_are_rejected(self):
        for change in ('command','available720','binding','duplicate','area'):
            plan=copy.deepcopy(self.plan)
            card=next(item for item in plan['checks'] if item['id']=='uvd.avc.exact')
            if change=='command':card['scenarios'][1]['command']=['decode']
            elif change=='available720':card['scenarios'][1].update(availability='available-alpha',existing_test_id='uvd.avc')
            elif change=='binding':card['scenarios'][1]['existing_test_id']='uvd.avc'
            elif change=='duplicate':card['scenarios'][1]['id']=card['scenarios'][0]['id']
            else:plan['checks'][0]['scenarios']=card['scenarios']
            with self.subTest(change=change),self.assertRaises(ValueError):Q.validate(plan)

    def test_read_only_render_explains_available_baseline_and_missing_harnesses(self):
        with mock.patch('subprocess.run',side_effect=AssertionError('scenario executed')), \
             mock.patch('socket.socket',side_effect=AssertionError('network')):
            text=' '.join(Q.render(self.plan,Q.evaluate(self.plan,'uvd-experimental')).split())
        for value in ('128x96 exact 120-frame baseline','720p30 exact-reference fixture',
                      '1080p30 exact-reference integration','Available alpha action; not a result',
                      'Unimplemented; no runnable fixture/harness','uvd.avc (not invoked)',
                      'fresh physical cold boot','no same-boot','storage budget','streaming'):
            self.assertIn(value.lower(),text.lower())

    def test_three_canonical_baselines_cannot_satisfy_the_unchanged_native_frame_gate(self):
        self.scope.update(uvd_runtime=True,firmware_manifest_sha256='4'*64)
        record=self.record('uvd.avc.exact')
        for run in record['runs']:run['metrics']['native_frames']=0
        report=Q.evaluate(self.isolated('uvd.avc.exact'),'isolated',self.document(record))
        self.assertEqual(self.row(report,'uvd.avc.exact')['status'],'BLOCKED')
        self.assertFalse(report['stability_claim'])


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.plan=Q.validate(Q.read_json(Q.PLAN))
        self.identity=dict(boot_id='616aaa3f-28bf-48bf-bdf5-a98560a0ec94',kernel_release='fixture-alpha4',
            manifest_sha256='a'*64,runner_sha256='b'*64,catalogue_sha256='c'*64,
            release_matches=True,manifest_available=True,declared_artifacts={'uvd_runtime':True,'rootfs_sha256':'d'*64})
        self.records={};self.jobs={};self.counter=0;self.calls=[];self.failure=None;self.stop=None
        self.browser_closed=True;self.renderer='software';self.now=0;self.saves=[]

    def tick(self,seconds):self.now+=seconds

    def record(self,test_id,status='PASS',**fields):
        self.counter+=1
        identifier='20261105T010000Z-'+f'{self.counter:012x}'
        value=dict(run_id=identifier,test_id=test_id,identity=copy.deepcopy(self.identity),
            phase='completed',status=status,started_at=f'{self.counter:08d}',hardware_acceptance=False,
            message='synthetic '+test_id,stop_required=False,test_version=1,
            commands=[dict(returncode=0,signal=None,timed_out=False,interrupted=False,output_limited=False)])
        if test_id=='cpu.compute':value.update(parameters={'seconds':2},evidence={
            'digest':'3951d9a412ce4c815eada76de213f560647cd60d4c37896cbcdcb899b645f197','iterations':2})
        if test_id=='memory.pattern':value.update(parameters={'memory_mib':32},evidence={'verified_mib':32,'patterns':[0,85,170,255]})
        value.update(fields);self.records[identifier]=value
        return value

    def rpc(self,action,params):
        self.calls.append((action,copy.deepcopy(params)))
        if action=='state':return {'identity':copy.deepcopy(self.identity),'jobs':[],
            'tests':[{'id':key,'version':1}for key in ('cpu.compute','memory.pattern')]}
        if action=='results.list':return [dict(run_id=value['run_id'],test_id=value['test_id'],
            status=value['status'],phase=value['phase'],boot_id=value['identity']['boot_id'],
            started_at=value['started_at'],stop_required=value.get('stop_required'))
            for value in sorted(self.records.values(),key=lambda value:value['started_at'],reverse=True)]
        if action=='job.get':return self.jobs[params['id']]
        if action=='test.run':
            key=params['test_id'];kind=next(step['kind']for step in self.plan['smoke']['steps']if step['id']==key)
            value=self.record(key,'INCONCLUSIVE'if kind=='observation'or key=='audio.44100'else'PASS')
            if key==self.failure:value.update(status='FAIL',message='synthetic oracle failure')
            if key==self.stop:value.update(status='INCONCLUSIVE',stop_required=True)
            if key=='uvd.avc':value['stop_required']=True
            if key=='audio.44100':value['observation_required']='audible-correct-pitch-and-duration'
        elif action=='uvd.scenario':value=self.record(Q.TERMINAL_SCENARIOS[params['scenario']]['test_id'],stop_required=True)
        elif action=='graphics.wayland':value=self.record('wayland.native')
        elif action=='graphics.launch':
            app=params['app'];batch=self.record('browser.batch',automatic=True,browser_app=app,
                closed=self.browser_closed,expected_count=5,completed_count=5)
            batch['run_ids']=[]
            for key in ('browser.capabilities','browser.canvas2d','browser.webgl','browser.animation','browser.video'):
                child=self.record(key,batch_id=batch['run_id'],renderer={'classification':self.renderer})
                batch['run_ids'].append(child['run_id'])
            value={'status':'INCONCLUSIVE','automatic_suite':True,'app':app}
        else:raise AssertionError('Unexpected action '+action)
        identifier=f'{len(self.jobs)+1:032x}'
        self.jobs[identifier]={'id':identifier,'state':'completed','result':value}
        return {'id':identifier,'state':'running'}

    def run_smoke(self,**options):
        return Q.run_smoke(self.plan,self.rpc,lambda run_id:copy.deepcopy(self.records[run_id]),
            provider_check=lambda _:None,save=lambda report:self.saves.append(copy.deepcopy(report)),
            clock=lambda:self.now,sleep=self.tick,**options)

    def step(self,report,key):return next(value for value in report['steps']if value['id']==key)

    def test_full_typed_sequence_has_shared_foundations_wayland_and_single_uvd_last(self):
        report=self.run_smoke(https_url='https://example.com/fixture')
        mutations=[(action,params.get('test_id',params.get('app',params.get('scenario','wayland'))))for action,params in self.calls
                   if action in ('test.run','graphics.launch','graphics.wayland','uvd.scenario')]
        self.assertEqual(mutations[:2],[('test.run','cpu.compute'),('test.run','memory.pattern')])
        self.assertEqual(mutations[-1],('uvd.scenario','canonical-120'))
        self.assertEqual(sum(action=='uvd.scenario'for action,_ in mutations),1)
        self.assertEqual(sum(key=='cpu.compute'for _,key in mutations),1)
        self.assertLess(mutations.index(('graphics.launch','chromium')),mutations.index(('graphics.wayland','wayland')))
        self.assertEqual(self.step(report,'inventory.readonly')['status'],'INCONCLUSIVE')
        self.assertEqual(self.step(report,'audio.44100')['status'],'INCONCLUSIVE')
        self.assertEqual(self.step(report,'browser.firefox')['webgl_renderer'],'software')
        self.assertFalse(self.step(report,'browser.firefox')['video_hardware_decode_proven'])
        self.assertIn('no hardware/UVD acceptance',self.step(report,'browser.firefox')['message'])
        self.assertEqual(len(report['coverage']),45)
        self.assertTrue(all(value['qualification_status']=='NOT_EVALUATED'for value in report['coverage']))
        self.assertFalse(report['hardware_acceptance']);self.assertFalse(report['stability_claim'])
        self.assertEqual(report['status'],'INCONCLUSIVE')
        self.assertGreater(len(self.saves),16)
        self.assertFalse(any(action.startswith(('network.','ssh.'))or params.get('test_id','').startswith('power.')
                             for action,params in self.calls))

    def test_matching_core_receipts_are_reused_and_failed_or_stale_core_never_retries(self):
        for key in ('cpu.compute','memory.pattern'):self.record(key)
        report=self.run_smoke()
        self.assertTrue(self.step(report,'cpu.compute')['reused']);self.assertTrue(self.step(report,'memory.pattern')['reused'])
        self.assertFalse(any(action=='test.run'and params['test_id']in('cpu.compute','memory.pattern')for action,params in self.calls))
        self.calls.clear();self.records.clear();self.jobs.clear()
        self.record('cpu.compute',status='FAIL')
        report=self.run_smoke()
        self.assertEqual(self.step(report,'cpu.compute')['status'],'BLOCKED')
        self.assertFalse(any(action in ('test.run','graphics.launch','graphics.wayland')for action,_ in self.calls))
        self.calls.clear();self.records.clear()
        stale=self.record('cpu.compute');stale['identity']['runner_sha256']='e'*64
        report=self.run_smoke()
        self.assertFalse(any(action=='test.run'for action,_ in self.calls))

    def test_fresh_cpu_oracle_failure_and_later_stop_latch_halt_all_downstream_actions(self):
        self.failure='cpu.compute'
        report=self.run_smoke()
        self.assertEqual(self.step(report,'cpu.compute')['status'],'FAIL')
        self.assertEqual([params['test_id']for action,params in self.calls if action=='test.run'],['cpu.compute'])
        self.assertEqual(report['status'],'FAIL')
        self.calls.clear();self.records.clear();self.jobs.clear();self.failure=None;self.stop='gpu.opengl.render'
        report=self.run_smoke()
        self.assertEqual(self.step(report,'gpu.vulkan.render')['status'],'BLOCKED')
        self.assertFalse(any(action in ('graphics.launch','uvd.scenario')for action,params in self.calls))

    def test_core_cache_cannot_substitute_another_test_or_unknown_catalogue_version(self):
        cpu=self.record('cpu.compute');memory=self.record('memory.pattern')
        report=Q.run_smoke(self.plan,self.rpc,lambda _:copy.deepcopy(memory),provider_check=lambda _:None,
            clock=lambda:self.now,sleep=self.tick)
        self.assertEqual(self.step(report,'cpu.compute')['status'],'BLOCKED')
        self.assertFalse(any(action in ('test.run','graphics.launch','graphics.wayland')for action,_ in self.calls))
        self.assertFalse(Q.foundation_pass(cpu,self.identity,{'cpu.compute':None}))
        self.assertFalse(Q.foundation_pass(dict(cpu,test_version=True),self.identity,{'cpu.compute':True}))

    def test_existing_uvd_intent_or_missing_ordinary_runtime_never_reopens_or_claims_failure(self):
        self.record('uvd.avc',stop_required=True)
        report=self.run_smoke()
        self.assertFalse(any(action=='test.run'for action,_ in self.calls))
        self.records.clear();self.calls.clear();self.identity['declared_artifacts']['uvd_runtime']=False
        report=self.run_smoke()
        self.assertEqual(self.step(report,'uvd.avc')['status'],'UNSUPPORTED')
        self.assertFalse(any(action=='uvd.scenario'for action,_ in self.calls))
        self.assertEqual(self.step(report,'ethernet.https')['status'],'NOT_RUN')

    def test_missing_provider_is_explicit_and_unclosed_browser_blocks_wayland_and_uvd(self):
        report=Q.run_smoke(self.plan,self.rpc,lambda key:copy.deepcopy(self.records[key]),
            provider_check=lambda _:'Chromium provider bound to native3/root2c, not current alpha4',
            clock=lambda:self.now,sleep=self.tick)
        self.assertEqual(self.step(report,'browser.chromium')['status'],'BLOCKED')
        self.assertFalse(any(action=='graphics.launch'and params['app']=='chromium'for action,params in self.calls))
        self.records.clear();self.calls.clear();self.jobs.clear();self.browser_closed=False
        report=self.run_smoke()
        self.assertEqual(self.step(report,'browser.firefox')['status'],'BLOCKED')
        self.assertFalse(any(action in ('graphics.wayland','uvd.scenario')for action,params in self.calls))

    def test_completed_batch_waits_for_durable_service_closure_without_relaunch(self):
        reads={}
        def saved(key):
            value=copy.deepcopy(self.records[key])
            if value['test_id']=='browser.batch':
                reads[key]=reads.get(key,0)+1
                if reads[key]<3:value['closed']=False
            return value
        report=Q.run_smoke(self.plan,self.rpc,saved,provider_check=lambda _:None,
            clock=lambda:self.now,sleep=self.tick)
        self.assertEqual(self.step(report,'browser.firefox')['status'],'PASS')
        self.assertEqual(self.step(report,'browser.chromium')['status'],'PASS')
        self.assertEqual(sorted(reads.values()),[3,3])
        self.assertEqual([params['app']for action,params in self.calls if action=='graphics.launch'],['browser','chromium'])
        self.assertGreaterEqual(self.now,1)
        self.assertTrue(any(action=='graphics.wayland'for action,_ in self.calls))

    def test_closure_wait_still_stops_immediately_on_save_identity_or_close_error(self):
        for fault in ('usb','identity','close-error'):
            self.calls.clear();self.records.clear();self.jobs.clear();self.now=0
            def saved(key):
                value=copy.deepcopy(self.records[key])
                if value['test_id']=='browser.batch':
                    value['closed']=False
                    if fault=='usb':value['usb_save_failed']=True
                    elif fault=='identity':value['identity']['manifest_sha256']='e'*64
                    else:value['close_error']='Service stop did not complete'
                return value
            report=Q.run_smoke(self.plan,self.rpc,saved,provider_check=lambda _:None,
                clock=lambda:self.now,sleep=self.tick)
            with self.subTest(fault=fault):
                self.assertEqual(self.step(report,'browser.firefox')['status'],'BLOCKED')
                self.assertEqual(self.now,0)
                self.assertFalse(any(action in ('graphics.wayland','uvd.scenario')for action,_ in self.calls))

    def test_deadline_preserves_inflight_job_without_cancel_reset_or_retry(self):
        rpc=self.rpc
        def pending(action,params):
            value=rpc(action,params)
            if action=='job.get':return dict(value,state='running')
            return value
        report=Q.run_smoke(self.plan,pending,lambda key:self.records[key],provider_check=lambda _:None,
                           clock=lambda:self.now,sleep=self.tick)
        self.assertIn('may still be active',report['halt_reason'])
        self.assertEqual(sum(action=='test.run'for action,_ in self.calls),1)
        self.assertEqual(self.step(report,'uvd.avc')['status'],'BLOCKED')

    def test_browser_requires_auto_grant_and_five_saved_matching_reports_before_continuing(self):
        original=self.rpc
        for fault in ('manual','missing-child','foreign-child'):
            self.calls.clear();self.records.clear();self.jobs.clear()
            def broken(action,params):
                value=original(action,params)
                if action=='graphics.launch':
                    if fault=='manual':self.jobs[value['id']]['result']['automatic_suite']=False
                    else:
                        batch=next(item for item in self.records.values()if item['test_id']=='browser.batch')
                        if fault=='missing-child':batch['run_ids'].pop()
                        else:self.records[batch['run_ids'][-1]]['identity']['boot_id']='another-boot'
                return value
            report=Q.run_smoke(self.plan,broken,lambda key:copy.deepcopy(self.records[key]),provider_check=lambda _:None,
                clock=lambda:self.now,sleep=self.tick)
            with self.subTest(fault=fault):
                self.assertEqual(self.step(report,'browser.firefox')['status'],'BLOCKED')
                self.assertFalse(any(action in ('graphics.wayland','uvd.scenario')for action,params in self.calls))

    def test_native_terminal_selection_replaces_canonical_and_runs_exactly_once(self):
        for scenario in ('native720-900','native1080-900'):
            self.records.clear();self.calls.clear();self.jobs.clear()
            report=self.run_smoke(terminal_scenario=scenario)
            selected=[params for action,params in self.calls if action=='uvd.scenario']
            self.assertEqual(selected,[{'scenario':scenario}])
            self.assertFalse(any(action=='test.run'and params.get('test_id')=='uvd.avc'for action,params in self.calls))
            self.assertEqual(report['terminal_scenario'],scenario)
            self.assertEqual(report['steps'][-1]['timeout_seconds'],240)
            self.assertEqual(len(report['steps']),16)
            self.assertEqual([r['scenario']for r in report['terminal_options']if r['selected']],[scenario])
            self.assertTrue(all(r['status']=='NOT_RUN'for r in report['terminal_options']if not r['selected']))

    def test_wrong_terminal_result_never_counts_as_selected_scenario_pass(self):
        original=self.rpc
        def wrong(action,params):
            value=original(action,params)
            if action=='uvd.scenario':self.jobs[value['id']]['result']['test_id']='uvd.avc'
            return value
        report=Q.run_smoke(self.plan,wrong,lambda key:copy.deepcopy(self.records[key]),
            provider_check=lambda _:None,clock=lambda:self.now,sleep=self.tick,terminal_scenario='native720-900')
        self.assertEqual(self.step(report,'uvd.avc')['status'],'BLOCKED')
        self.assertIn('selected fixed scenario',report['halt_reason'])
        self.assertEqual(sum(action=='uvd.scenario'for action,_ in self.calls),1)

    def test_selected_terminal_plan_is_inert_and_unknown_scenario_refused(self):
        output=io.StringIO()
        with mock.patch.object(Q,'ControllerIPC',side_effect=AssertionError('socket opened')),contextlib.redirect_stdout(output):
            self.assertEqual(Q.main(['--smoke','--terminal-scenario','native1080-900','--json']),0)
        report=json.loads(output.getvalue())
        self.assertEqual(report['terminal_scenario'],'native1080-900')
        self.assertEqual(report['steps'][-1]['parameters'],{'scenario':'native1080-900'})
        with self.assertRaisesRegex(ValueError,'fixed terminal'):
            self.run_smoke(terminal_scenario='power.off')
        self.assertEqual(self.calls,[])

    def test_smoke_plan_cli_is_inert_and_schema_forbids_reordered_or_arbitrary_actions(self):
        output=io.StringIO()
        with mock.patch.object(Q,'ControllerIPC',side_effect=AssertionError('socket opened')),contextlib.redirect_stdout(output):
            self.assertEqual(Q.main(['--smoke','--json']),0)
        self.assertEqual(json.loads(output.getvalue())['phase'],'plan')
        for kind in ('order','power','browser-kind'):
            plan=copy.deepcopy(self.plan)
            if kind=='order':plan['smoke']['steps'].reverse()
            elif kind=='power':plan['smoke']['steps'][0]['parameters']['test_id']='power.off'
            else:plan['smoke']['steps'][11]['kind']='functional'
            with self.subTest(kind=kind),self.assertRaises(ValueError):Q.validate(plan)

    def test_private_output_is_exclusive_bounded_and_fsynced_with_complete_coverage(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp')as directory:
            path=Path(directory)/'smoke.json';writer=Q.SmokeWriter(path)
            report=self.run_smoke();writer(report)
            self.assertEqual(json.loads(path.read_text())['run_id'],report['run_id'])
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            with self.assertRaises(FileExistsError):Q.SmokeWriter(path)
            self.assertEqual(len(list(Path(directory).iterdir())),1)


if __name__=='__main__':unittest.main()
