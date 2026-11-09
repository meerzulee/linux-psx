"""Durable automatic browser batches; fake subprocesses, no display or services."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('psx_batch_controller',ROOT/'diagnostics/ps4/controller.py')
APP=importlib.util.module_from_spec(spec);spec.loader.exec_module(APP)
UA='Mozilla Firefox/154'


class BatchTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir='/var/tmp');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.c=APP.Controller(self.root/'results',self.root,network=SimpleNamespace())
        self.stops=[]
        for patch in [mock.patch.object(self.c,'ready',return_value={'boot_id':'fixture'}),
                      mock.patch.object(self.c,'core_prerequisites',return_value=None,create=True),
                      mock.patch.object(self.c.runner,'identity',return_value={'boot_id':'fixture','release_matches':True}),
                      mock.patch.object(self.c.runner.fs,'fixture',False),
                      mock.patch.object(self.c,'_cancellable_capture',side_effect=self.command)]:
            patch.start();self.addCleanup(patch.stop)

    def command(self,argv,**kwargs):
        if argv[:2]==['/usr/bin/systemctl','stop']:
            self.stops.append(argv[2]);return subprocess.CompletedProcess(argv,0,stdout=b'')
        app=argv[-1]
        self.assertTrue(list(self.c.results_dir.glob('*/batch.json')))
        return subprocess.CompletedProcess(argv,0,stdout=json.dumps({'started':True,'mode':app,
            'service':'psx-diagnostics-window-'+app+'.service'}).encode())

    def launch(self):
        self.c.launch_graphics('browser')
        return self.c.browser_session(UA,True)['auto_run']

    def step(self,grant,index=0,status='PASS'):
        test=APP.BROWSER_STEPS[index]
        marker=self.c.browser_start({'test_id':test,'batch_id':grant['batch_id'],'nonce':grant['nonce']},UA,True)
        self.c.browser_result({'schema_version':1,'test_id':test,'run_id':marker['run_id'],
            'status':status,'duration_ms':1,'measurements':{},'renderer':{}},UA)
        return marker['run_id']

    def finish(self,grant,ids,error=''):
        return self.c.browser_finish({'batch_id':grant['batch_id'],'nonce':grant['nonce'],'run_ids':ids,'error':error},UA,True)

    def test_success_saves_same_boot_ordered_summary_before_closing_only_browser(self):
        grant=self.launch();ids=[self.step(grant,index) for index in range(5)]
        original=self.command
        def close_after_save(argv,**kwargs):
            if argv[:2]==['/usr/bin/systemctl','stop']:
                record=json.loads((self.c.results_dir/grant['batch_id']/'result.json').read_text())
                self.assertEqual(record['run_ids'],ids);self.assertEqual(record['phase'],'completed')
            return original(argv,**kwargs)
        with mock.patch.object(self.c,'_cancellable_capture',side_effect=close_after_save):
            reply=self.finish(grant,ids)
        self.assertTrue(reply['saved']);self.assertTrue(reply['closed']);self.assertEqual(reply['status'],'PASS')
        self.assertEqual(self.stops,['psx-diagnostics-window-browser.service'])
        with self.assertRaises(ValueError):self.finish(grant,ids)

    def test_wrong_source_token_order_and_boot_cannot_close_a_browser(self):
        grant=self.launch();first=self.step(grant)
        payload={'batch_id':grant['batch_id'],'nonce':grant['nonce'],'run_ids':[first],'error':'stop'}
        for changed,ua in [({'nonce':'wrong'},UA),({},'Chrome/151'),({'run_ids':[]},UA),({'batch_id':'wrong'},UA)]:
            with self.assertRaises(ValueError):self.c.browser_finish(dict(payload,**changed),ua,True)
        with mock.patch.object(self.c.runner,'identity',return_value={'boot_id':'other'}):
            with self.assertRaises(ValueError):self.c.browser_finish(payload,UA,True)
        self.assertEqual(self.stops,[])

    def test_partial_fail_is_saved_and_closed_without_claiming_remaining_tests_passed(self):
        grant=self.launch();first=self.step(grant,status='FAIL')
        self.assertEqual(self.finish(grant,[first],'Test failed')['status'],'FAIL')
        record=json.loads((self.c.results_dir/grant['batch_id']/'result.json').read_text())
        self.assertEqual(record['completed_count'],1);self.assertEqual(record['counts']['PASS'],0)
        self.assertTrue(record['stop_required']);self.assertTrue(record['closed'])

    def test_save_failure_preserves_unfinished_marker_and_writes_ram_summary_before_close(self):
        grant=self.launch()
        marker=self.c.browser_start({'test_id':APP.BROWSER_STEPS[0],'batch_id':grant['batch_id'],'nonce':grant['nonce']},UA,True)
        original=APP.diagnostics.atomic_json
        def failed_usb(path,value):
            if path==self.c.results_dir/grant['batch_id']/'result.json':raise OSError('fixture USB unavailable')
            return original(path,value)
        with mock.patch.object(APP.diagnostics,'atomic_json',side_effect=failed_usb):
            reply=self.finish(grant,[],'Report save failed')
        self.assertEqual(reply['storage'],'ram');self.assertTrue(reply['closed']);self.assertFalse(reply['durable'])
        self.assertEqual(json.loads((self.c.results_dir/marker['run_id']/'result.json').read_text())['phase'],'running')
        fallback=self.root/'run/psx-diagnostics/browser-batches'/(grant['batch_id']+'.json')
        self.assertTrue(json.loads(fallback.read_text())['stop_required'])

    def test_root_watchdog_closes_hung_page_and_manual_workspace_has_no_batch(self):
        grant=self.launch()
        with mock.patch.object(APP.time,'monotonic',return_value=self.c._browser_batch['deadline']+1):
            self.c._browser_watchdog(grant['batch_id'])
        self.assertTrue(self.c._browser_batch['record']['closed'])
        self.assertEqual(self.c._browser_batch['record']['status'],'INCONCLUSIVE')
        before=len(list(self.c.results_dir.glob('*/batch.json')))
        self.c.launch_graphics('browser-manual')
        self.assertEqual(len(list(self.c.results_dir.glob('*/batch.json'))),before)
        self.assertIsNone(self.c.browser_session(UA,True)['auto_run'])

    def test_launcher_cannot_authorize_closing_the_diagnostic_menu(self):
        response=subprocess.CompletedProcess([],0,stdout=json.dumps({'started':True,'mode':'browser',
            'service':'psx-diagnostics-graphics@diagnostics.service'}).encode())
        with mock.patch.object(self.c,'_cancellable_capture',return_value=response):
            with self.assertRaises(ValueError):self.c.launch_graphics('browser')
        self.assertEqual(self.stops,[])
        self.assertIsNone(self.c.browser_session(UA,True)['auto_run'])

    def test_total_summary_storage_failure_still_closes_and_latches_recovery(self):
        grant=self.launch();ids=[self.step(grant,index) for index in range(5)]
        with mock.patch.object(APP.diagnostics,'atomic_json',side_effect=OSError('fixture stores unavailable')):
            reply=self.finish(grant,ids)
        self.assertFalse(reply['saved']);self.assertTrue(reply['closed'])
        self.assertEqual(reply['status'],'INCONCLUSIVE')
        with self.assertRaisesRegex(ValueError,'batch needs recovery'):
            APP.Controller.ready(self.c)

    def test_controller_restart_recovers_batch_and_keeps_individual_marker_unfinished(self):
        grant=self.launch()
        marker=self.c.browser_start({'test_id':APP.BROWSER_STEPS[0],'batch_id':grant['batch_id'],'nonce':grant['nonce']},UA,True)
        restarted=APP.Controller(self.c.results_dir,self.root,network=SimpleNamespace())
        with mock.patch.object(restarted.runner,'identity',return_value={'boot_id':'fixture','release_matches':True}), \
             mock.patch.object(restarted,'_cancellable_capture',side_effect=self.command):
            restarted.recover_browser_batches()
        record=json.loads((self.c.results_dir/grant['batch_id']/'result.json').read_text())
        self.assertEqual(record['status'],'INCONCLUSIVE');self.assertTrue(record['closed'])
        self.assertTrue(record['stop_required']);self.assertEqual(record['completed_count'],0)
        self.assertIn('Controller restarted',record['message'])
        self.assertEqual(json.loads((self.c.results_dir/marker['run_id']/'result.json').read_text())['phase'],'running')
        self.assertIsNone(restarted.browser_session(UA,True)['auto_run'])


class SummaryStepTests(unittest.TestCase):
    def test_a_batch_summary_carries_only_each_steps_verdict(self):
        record={'run_id':'batch','test_id':'browser.batch','status':'INCONCLUSIVE','reports':[
            {'test_id':'browser.capabilities','status':'INCONCLUSIVE','message':'long text','user_agent':UA},
            {'test_id':'browser.webgl','status':'PASS'}]}
        value=APP.summary(record)
        self.assertEqual(value['steps'],[{'test_id':'browser.capabilities','status':'INCONCLUSIVE'},
                                         {'test_id':'browser.webgl','status':'PASS'}])
        self.assertEqual(value['status'],'INCONCLUSIVE')

    def test_other_records_gain_no_steps(self):
        self.assertNotIn('steps',APP.summary({'run_id':'one','test_id':'cpu.compute','status':'PASS'}))


if __name__=='__main__':unittest.main()
