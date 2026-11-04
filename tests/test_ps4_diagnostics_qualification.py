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


if __name__=='__main__':unittest.main()
