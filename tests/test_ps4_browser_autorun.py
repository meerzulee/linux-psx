"""Root-selected one-shot suite intent and private profiles; synthetic only."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    loader=importlib.machinery.SourceFileLoader(name,str(ROOT/path))
    spec=importlib.util.spec_from_loader(name,loader)
    app=importlib.util.module_from_spec(spec);loader.exec_module(app);return app
APP=load('psx_auto_controller_tests','diagnostics/ps4/controller.py')
CHROME=load('psx_chrome_profile_tests','rootfs/ps4-diagnostics-desktop/launch-chromium-provider')


class AutoRunTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir='/var/tmp');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.controller=APP.Controller(self.root/'results',self.root,network=SimpleNamespace())
        self.patchers=[mock.patch.object(self.controller,'ready',return_value={'boot_id':'fixture'}),
                       mock.patch.object(self.controller,'core_prerequisites',return_value=None),
                       mock.patch.object(self.controller.runner.fs,'fixture',False),
                       mock.patch.object(APP.Path,'is_file',return_value=True)]
        for patch in self.patchers:patch.start();self.addCleanup(patch.stop)

    @staticmethod
    def receipt(*args,**kwargs):
        app=args[0][-1]
        return SimpleNamespace(stdout=json.dumps({'started':True,'mode':app,'service':'psx-diagnostics-window-'+app+'.service'}))

    def test_nonce_is_local_family_specific_once_only_and_manual_modes_never_grant(self):
        with mock.patch.object(self.controller,'_cancellable_capture',side_effect=self.receipt) as run:
            self.controller.launch_graphics('chromium')
        self.assertEqual(run.call_args.args[0][-1],'chromium')
        self.assertIsNone(self.controller.browser_session('Firefox/154',True)['auto_run'])
        self.assertIsNone(self.controller.browser_session('Chrome/151',False)['auto_run'])
        grant=self.controller.browser_session('Chrome/151',True)['auto_run']
        self.assertEqual(grant['suite'],'baseline');self.assertRegex(grant['nonce'],r'^[A-Za-z0-9_-]{32}$')
        self.assertLessEqual(grant['expires_in_seconds'],60)
        self.assertIsNone(self.controller.browser_session('Chrome/151',True)['auto_run'])
        with mock.patch.object(self.controller,'_cancellable_capture'):
            self.controller._finish_browser_batch(self.controller._browser_batch,'Fixture test finished')
        with mock.patch.object(self.controller,'_cancellable_capture') as run:self.controller.launch_graphics('browser-manual')
        self.assertEqual(run.call_args.args[0][-1],'browser')
        self.assertIsNone(self.controller.browser_session('Firefox/154',True)['auto_run'])

    def test_early_fetch_waits_for_launcher_success_and_durable_job_completion(self):
        def launch(*_,**__):
            pending=self.controller.browser_session('Firefox/154',True)
            self.assertTrue(pending['auto_pending']);self.assertIsNone(pending['auto_run'])
            return self.receipt(*_,**__)
        with mock.patch.object(self.controller,'_cancellable_capture',side_effect=launch):self.controller.launch_graphics('browser')
        self.controller.jobs.active='launch-job'
        self.assertTrue(self.controller.browser_session('Firefox/154',True)['auto_pending'])
        self.controller.jobs.active=None
        self.assertIsNotNone(self.controller.browser_session('Firefox/154',True)['auto_run'])

    def test_expired_or_failed_launch_cannot_grant_or_replay(self):
        with mock.patch.object(APP.time,'monotonic',return_value=100),mock.patch.object(self.controller,'_cancellable_capture',side_effect=self.receipt):
            self.controller.launch_graphics('browser')
        with mock.patch.object(APP.time,'monotonic',return_value=161):
            self.assertIsNone(self.controller.browser_session('Firefox/154',True)['auto_run'])
        with mock.patch.object(self.controller,'_cancellable_capture'):
            self.controller._finish_browser_batch(self.controller._browser_batch,'Fixture deadline expired')
        with mock.patch.object(self.controller,'_cancellable_capture',side_effect=subprocess.CalledProcessError(1,'launch')):
            with self.assertRaises(subprocess.CalledProcessError):self.controller.launch_graphics('browser')
        self.assertIsNone(self.controller.browser_session('Firefox/154',True)['auto_run'])

    def test_chromium_profiles_are_fresh_private_and_do_not_change_provider_flags(self):
        first=CHROME.fresh_profile(self.root);second=CHROME.fresh_profile(self.root)
        self.assertNotEqual(first,second)
        self.assertEqual(first.stat().st_mode&0o777,0o700)
        provider={'browser_flags':CHROME.FLAGS,'lab_url':CHROME.LAB,'kernel_release':CHROME.RELEASE}
        command=CHROME.command(provider,'a'*64,first)
        self.assertIn('--user-data-dir='+str(first),command)
        self.assertEqual(command[-1],CHROME.LAB)
        self.assertNotIn('--no-sandbox',command)

    def test_protondb_is_manual_and_creates_no_browser_reports_or_auto_intent(self):
        with mock.patch.object(self.controller,'_cancellable_capture',side_effect=self.receipt) as run:
            result=self.controller.launch_graphics('protondb')
        self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.args[0][-1],'protondb')
        self.assertNotIn('--automatic',run.call_args.args[0])
        self.assertFalse(result['automatic_suite'])
        self.assertIsNone(self.controller._browser_batch)
        self.assertIsNone(self.controller.browser_session('Firefox/154',True)['auto_run'])
        self.assertEqual(list(self.controller.results_dir.glob('*/result.json')),[])
        self.controller.core_prerequisites.assert_called_once_with('graphics.window.protondb')

    def test_protondb_uses_typed_launch_without_accepting_a_url_parameter(self):
        with mock.patch.object(self.controller.jobs,'submit',side_effect=lambda title,fn:fn()), \
             mock.patch.object(self.controller,'_cancellable_capture',side_effect=self.receipt):
            self.assertEqual(self.controller.request('graphics.launch',{'app':'protondb'})['app'],'protondb')
        with mock.patch.object(self.controller.jobs,'submit') as submit:
            with self.assertRaises(ValueError):self.controller.request('graphics.launch',{'app':'protondb','url':'https://example.invalid/'})
            submit.assert_not_called()


if __name__=='__main__':unittest.main()
