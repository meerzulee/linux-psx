"""Browser report identity and optional display metadata; no live display calls."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('psx_browser_reports_tests',ROOT/'diagnostics/ps4/controller.py')
APP=importlib.util.module_from_spec(spec)
spec.loader.exec_module(APP)


class BrowserReportsTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir='/var/tmp')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.controller=APP.Controller(self.root/'results',self.root,
            network=SimpleNamespace(status=lambda:{},ssh_status=lambda:{}))
        self.identity={'release_matches':True,'boot_id':'fixture-boot','kernel_release':'fixture-psx-ps4'}
        patch=mock.patch.object(self.controller.runner,'identity',return_value=self.identity)
        patch.start();self.addCleanup(patch.stop)
        # Protocol/display tests isolate receipts; the prerequisite suite tests the gate.
        patch=mock.patch.object(self.controller,'core_prerequisites',return_value=None)
        patch.start();self.addCleanup(patch.stop)

    def test_completion_keeps_start_time_server_mode_and_browser_specific_title(self):
        for ua,title in [('Mozilla Firefox/154.0','Firefox 154'),('Mozilla Chrome/151.0.0','Chromium 151')]:
            with self.subTest(ua=ua),mock.patch.object(self.controller,'_cancellable_capture',side_effect=AssertionError('live display')):
                marker=self.controller.browser_start({'test_id':'browser.animation'})
                path=self.root/'results'/marker['run_id']/'result.json'
                started=json.loads(path.read_text())
                mode={'available':True,'width':1920,'height':1080,'configured_refresh_hz':60,'source':'X11 RandR'}
                started['display_mode']=mode
                APP.diagnostics.atomic_json(path,started)
                report=self.controller.browser_result({'schema_version':1,'test_id':'browser.animation','status':'PASS',
                    'run_id':marker['run_id'],'measurements':{'suite':{'id':'baseline','step':4},'estimated_raf_hz':59.9}},ua)
                self.assertEqual(report['display_mode'],mode)
                self.assertEqual(report['started_at'],started['started_at'])
                self.assertEqual(report['title'],title+' · Frame pacing')
                self.assertEqual(report['browser_user_agent'],ua)
                self.assertFalse(report['hardware_acceptance'])

    def test_optional_display_probe_is_fixed_bounded_and_failure_is_unknown(self):
        response=SimpleNamespace(stdout=json.dumps({'available':True,'width':1920,'height':1080,'configured_refresh_hz':60}))
        with mock.patch.object(self.controller.runner.fs,'fixture',False), \
             mock.patch.object(APP.Path,'is_file',return_value=True), \
             mock.patch.object(self.controller,'_cancellable_capture',return_value=response) as run:
            mode=self.controller.browser_display_mode()
            self.assertTrue(mode['available'])
            self.assertEqual(run.call_args.args[0],['/usr/bin/python3','/run/psx-frontend/wait-graphics-wm','--display-info'])
            self.assertEqual(run.call_args.kwargs['timeout'],3)
            self.assertEqual(run.call_args.kwargs['env']['DISPLAY'],':0')
            self.assertIn('not a physical',mode['scope'])
        with mock.patch.object(self.controller.runner.fs,'fixture',False), \
             mock.patch.object(APP.Path,'is_file',return_value=True), \
             mock.patch.object(self.controller,'_cancellable_capture',side_effect=subprocess.TimeoutExpired('fixed-probe',3)):
            self.assertFalse(self.controller.browser_display_mode()['available'])

    def test_invalid_display_probe_never_becomes_a_configured_rate(self):
        with mock.patch.object(self.controller.runner.fs,'fixture',False), \
             mock.patch.object(APP.Path,'is_file',return_value=True), \
             mock.patch.object(self.controller,'_cancellable_capture',return_value=SimpleNamespace(stdout='{"available":true,"width":1920,"height":1080,"configured_refresh_hz":-1}')):
            self.assertFalse(self.controller.browser_display_mode()['available'])

    def test_chromium_is_a_fixed_guarded_graphics_action(self):
        with mock.patch.object(self.controller,'launch_graphics',return_value={'app':'chromium','hardware_acceptance':False}) as launch, \
             mock.patch.object(self.controller.jobs,'submit',side_effect=lambda title, function:function()):
            result=self.controller.request('graphics.launch',{'app':'chromium'})
        launch.assert_called_once_with('chromium')
        self.assertEqual(result['app'],'chromium')
        with mock.patch.object(self.controller,'ready',side_effect=ValueError('recovery required')), \
             mock.patch.object(self.controller.jobs,'submit') as submit, self.assertRaisesRegex(ValueError,'recovery'):
            self.controller.request('graphics.launch',{'app':'chromium'})
        submit.assert_not_called()

    def test_chromium_preparation_is_bounded_and_precedes_graphical_launch(self):
        with mock.patch.object(self.controller.runner.fs,'fixture',False), \
             mock.patch.object(APP.Path,'is_file',return_value=True),mock.patch.object(self.controller,'_cancellable_capture') as run:
            self.controller.launch_graphics('chromium-manual')
        self.assertEqual(run.call_args_list[0].args[0],['/usr/bin/python3','/run/psx-frontend/prepare-chromium-provider'])
        self.assertEqual(run.call_args_list[0].kwargs['timeout'],170)
        self.assertEqual(run.call_args_list[1].args[0][-1],'chromium')
        with mock.patch.object(self.controller.runner.fs,'fixture',False), \
             mock.patch.object(APP.Path,'is_file',return_value=True), \
             mock.patch.object(self.controller,'_cancellable_capture',side_effect=subprocess.CalledProcessError(1,'prepare')) as run:
            with self.assertRaises(subprocess.CalledProcessError):self.controller.launch_graphics('chromium-manual')
        self.assertEqual(run.call_count,1)


if __name__=='__main__':unittest.main()
