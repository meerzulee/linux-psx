"""Private SSH IPC integration; no SSH daemon, network fetch or host changes."""
import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('psx_ssh_ui_controller',ROOT/'diagnostics/ps4/controller.py')
APP=importlib.util.module_from_spec(spec);spec.loader.exec_module(APP)
SECRET='private-one-shot-fixture-password'


class Backend:
    def __init__(self):self.added=[]
    def info(self):return {'login':'root','password_enabled':False,'expires_in_seconds':0,'key_fingerprints':['SHA256:owner'],'github_available':True}
    def github_preview(self,username):return {'preview_id':'private-preview','username':username,'fingerprints':['SHA256:public'],'expires_in_seconds':300}
    def github_add(self,identifier):self.added.append(identifier);return {'added':1,'total':2,'fingerprints':['SHA256:public'],'persisted':True}
    def password_enable(self):return {'password':SECRET,'login':'root','expires_in_seconds':600,'instructions':[]}
    def password_disable(self):return {'disabled':True}


class SshIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(dir='/var/tmp');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        network=SimpleNamespace(status=lambda:{'interfaces':[],'wifi':{}},ssh_status=lambda:{'enabled':True,'login':'root'})
        self.controller=APP.Controller(self.root/'results',self.root,network=network)
        self.backend=Backend();self.controller._ssh_access=self.backend
        patch=mock.patch.object(self.controller,'ready',return_value={'boot_id':'fixture'})
        patch.start();self.addCleanup(patch.stop)

    def test_secret_is_a_direct_reply_and_never_enters_jobs_state_or_reports(self):
        reply=self.controller.request('ssh.password.enable')
        self.assertEqual(reply['password'],SECRET)
        self.assertEqual(self.controller.jobs.records,{})
        self.assertFalse(self.controller.results_dir.exists())
        for value in (self.controller.request('state'),self.controller.request('results.list'),self.controller.browser_session('Firefox/154',True)):
            self.assertNotIn(SECRET,json.dumps(value))
        self.assertNotIn(SECRET,json.dumps(self.controller.request('ssh.info')))

    def test_preview_never_imports_without_separate_explicit_confirmation(self):
        preview=self.controller.request('ssh.github.preview',{'username':'meerzulee'})
        self.assertEqual(self.backend.added,[])
        self.controller.request('ssh.github.add',{'preview_id':preview['preview_id']})
        self.assertEqual(self.backend.added,['private-preview'])
        self.assertEqual(self.controller.jobs.records,{})

    def test_mutations_keep_recovery_and_busy_guards(self):
        self.controller.jobs.active='running-test'
        with self.assertRaises(APP.BusyError):self.controller.request('ssh.password.enable')
        self.assertEqual(self.controller.request('ssh.info')['login'],'root')
        self.controller.jobs.active=None
        with mock.patch.object(self.controller,'ready',side_effect=ValueError('recovery required')):
            with self.assertRaisesRegex(ValueError,'recovery'):self.controller.request('ssh.github.add',{'preview_id':'x'})

    def test_unknown_fields_and_password_errors_cannot_leak_a_secret(self):
        with self.assertRaises(ValueError):self.controller.request('ssh.password.enable',{'password':'chosen'})
        with mock.patch.object(self.backend,'password_enable',side_effect=RuntimeError(SECRET)):
            with self.assertRaises(ValueError) as error:self.controller.request('ssh.password.enable')
        self.assertNotIn(SECRET,str(error.exception))

    def test_failed_startup_disables_only_enrollment_without_logging_error_details(self):
        output=io.StringIO()
        with mock.patch.object(self.backend,'initialize',side_effect=ValueError(SECRET),create=True), \
             mock.patch.object(self.controller.runner.fs,'fixture',False), \
             mock.patch('sys.stderr',output):
            self.controller.initialize_ssh()
        info=self.controller.request('ssh.info')
        self.assertFalse(info['setup_available'])
        self.assertFalse(info['password_enabled'])
        self.assertIn('existing key access',info['message'])
        self.assertNotIn(SECRET,output.getvalue()+json.dumps(info))
        with self.assertRaisesRegex(ValueError,'unavailable'):
            self.controller.request('ssh.github.preview',{'username':'meerzulee'})
        self.assertIsInstance(self.controller.request('state'),dict)

    def test_http_never_dispatches_private_ssh_actions(self):
        handler=object.__new__(APP.LabHandler)
        handler.path='/api/ssh/password/enable'
        handler.server=SimpleNamespace(controller=self.controller)
        handler.headers=Message()
        for key,value in {'Host':'127.0.0.1:8080','Origin':'http://127.0.0.1:8080',
                          'X-PSX-CSRF':self.controller.csrf,'Content-Type':'application/json',
                          'Content-Length':'2'}.items():
            handler.headers[key]=value
        handler.rfile=io.BytesIO(b'{}')
        handler.json_reply=mock.Mock()
        with mock.patch.object(self.backend,'password_enable',wraps=self.backend.password_enable) as enable:
            handler.do_POST()
        self.assertEqual(handler.json_reply.call_args.args[0],404)
        enable.assert_not_called()


if __name__=='__main__':unittest.main()
