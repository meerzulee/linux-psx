"""Automatic-mode orchestration and issue download; all device state is synthetic."""
import importlib.util
import io
import json
from pathlib import Path
import socket
import threading
import time
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('automatic_fixture', ROOT / 'tests/test_ps4_diagnostics_controller.py')
FIXTURE = importlib.util.module_from_spec(spec); spec.loader.exec_module(FIXTURE)
APP = FIXTURE.APP

class AutomaticTests(unittest.TestCase):
    def setUp(self):
        fixture = FIXTURE.ControllerTests('test_state_uses_fixture_hardware_and_never_invokes_real_tools')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.controller = fixture.controller; self.root = fixture.root; self.fs = fixture.fixture
        self.calls = []; self.failure = None
        self.controller.runner.executor = self.execute
        source = self.fs / 'var/log/dmesg'; source.parent.mkdir(parents=True)
        source.write_text('Linux version fixture\nnetwork 192.168.50.215 mac 12:34:56:78:9a:bc\n')

    def execute(self, argv, timeout, stdout, stderr, env):
        self.calls.append(list(argv)); self.assertIn('_worker', argv)
        kind, amount = argv[-2], int(argv[-1])
        data = {'digest': APP.diagnostics.CPU_ANSWER, 'iterations': 2} if kind == 'cpu' else {'verified_mib': amount, 'patterns': [0, 85, 170, 255]}
        if self.failure == kind:
            data = {'digest': 'wrong', 'iterations': 1} if kind == 'cpu' else {'verified_mib': 0, 'patterns': []}
        return APP.diagnostics.CommandResult(0, json.dumps(data), '')

    def request(self, action='diagnostics.auto', params=None):
        job = self.controller.request(action, params or {})
        deadline = time.monotonic() + 8
        while self.controller.jobs.get(job['id'])['state'] == 'running' and time.monotonic() < deadline:
            time.sleep(.005)
        saved = self.controller.jobs.get(job['id'])
        self.assertEqual(saved['state'], 'completed', saved)
        return saved['result']

    def test_opening_state_never_starts_automatic_or_creates_journal(self):
        self.assertIsNone(self.controller.request('state')['automatic'])
        self.assertFalse((self.root / 'results/automatic-runs').exists())
        self.assertEqual(self.calls, [])

    def test_selected_automatic_runs_fixed_checks_once_and_makes_issue_archive(self):
        result = self.request()
        self.assertEqual(result['status'], 'PASS')
        self.assertFalse(result['hardware_acceptance'])
        self.assertEqual([argv[-2:] for argv in self.calls], [['cpu', '2'], ['memory', '32']])
        self.assertEqual([item['test_id'] for item in result['results']], [step[0] for step in APP.AUTOMATIC_STEPS])
        self.assertTrue(all(step['state'] == 'completed' for step in result['steps']))
        observed = [r for r in result['results'] if self.controller.runner.tests[r['test_id']]['kind'] == 'observation']
        self.assertTrue(all(r['status'] == 'INCONCLUSIVE' for r in observed))
        report = result['issue_report']; self.assertEqual(report['status'], 'READY')
        name, data = self.controller.read_support_report(report['report_id'])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()), {'issue.md', 'report.json', 'kernel.log'})
            combined = b'\n'.join(archive.read(n) for n in archive.namelist())
            self.assertNotIn(b'192.168.50.215', combined); self.assertNotIn(b'12:34:56:78:9a:bc', combined)
        again = self.request(); self.assertEqual(again['run_id'], result['run_id']); self.assertEqual(len(self.calls), 2)
        restored = APP.Controller(self.root / 'results', self.fs, network=FIXTURE.Network(), executor=self.execute)
        self.assertEqual(restored.automatic_diagnostics()['run_id'], result['run_id']); self.assertEqual(len(self.calls), 2)

    def test_cpu_failure_stops_ram_and_does_not_retry_on_second_selection(self):
        self.failure = 'cpu'; result = self.request()
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual([argv[-2] for argv in self.calls], ['cpu'])
        self.assertEqual([r['test_id'] for r in result['results']], ['inventory.readonly', 'cpu.compute'])
        self.assertTrue(all(step['state'] == 'not_run' for step in result['steps'][2:]))
        self.assertEqual(result['issue_report']['status'], 'READY')
        self.request(); self.assertEqual(len(self.calls), 1)

    def test_manual_issue_export_keeps_ssh_and_power_startup_refusal(self):
        self.controller._ssh_setup_error = 'SSH setup unavailable: selected diagnostic kernel differs.'
        self.controller._power_boot_error = {
            'guard_stage': 'ram-proof',
            'reason': 'Power unavailable: the RAM boot proof could not be verified (ram-proof).',
        }
        result = self.request('support.export')
        _, data = self.controller.read_support_report(result['report_id'])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            report = json.loads(archive.read('report.json'))
            self.assertFalse(report['startup']['ssh']['available'])
            self.assertFalse(report['startup']['power']['available'])
            self.assertEqual(report['startup']['power']['guard_stage'], 'ram-proof')
            self.assertIn(b'ram-proof', archive.read('issue.md'))
            self.assertFalse(report['hardware_acceptance'])
        self.assertEqual(self.calls, [])

    def test_identity_change_stops_before_next_worker(self):
        def changed(*args):
            result = self.execute(*args)
            (self.fs / 'proc/sys/kernel/random/boot_id').write_text('changed-boot')
            return result
        self.controller.runner.executor = changed
        result = self.request(); self.assertEqual(result['status'], 'BLOCKED'); self.assertEqual(len(self.calls), 1)
        self.assertEqual(result['steps'][2]['status'], 'NOT_RUN')

    def test_controller_restart_never_resumes_interrupted_automatic(self):
        identity = self.controller.runner.identity(); path = self.controller._automatic_path(identity)
        path.parent.mkdir(parents=True)
        APP.diagnostics.atomic_json(path, {'test_id': 'diagnostics.auto', 'version': 1, 'phase': 'running', 'identity': identity})
        with self.assertRaisesRegex(ValueError, 'already started'):
            self.controller.automatic_diagnostics()
        self.assertEqual(self.calls, [])

    def test_export_stays_available_after_failure_guard_and_runs_no_workers(self):
        with mock.patch.object(self.controller, 'ready', side_effect=ValueError('terminal device state')):
            report = self.request('support.export')
        self.assertEqual(report['status'], 'READY'); self.assertEqual(self.calls, [])
        for action in ('diagnostics.auto', 'support.export'):
            with self.assertRaises(ValueError): self.controller.request(action, {'path': '/etc/shadow'})

    def test_active_job_excludes_automatic_and_export_without_starting_checks(self):
        gate = threading.Event()
        job = self.controller.jobs.submit('Synthetic active work', lambda: gate.wait(3))
        try:
            for action in ('diagnostics.auto', 'support.export'):
                with self.assertRaises(APP.BusyError): self.controller.request(action)
            self.assertEqual(self.calls, [])
        finally:
            gate.set()
            deadline=time.monotonic()+4
            while self.controller.jobs.get(job['id'])['state']=='running' and time.monotonic()<deadline: time.sleep(.005)

    def test_report_reader_refuses_paths_symlinks_and_unknown_ids(self):
        self.request('support.export'); directory=self.root/'results/support-reports'
        for report_id in ('../etc/shadow', 'ABC', '1'*31, '0'*32+'.zip'):
            with self.assertRaises(ValueError): self.controller.read_support_report(report_id)
        (directory/('diagnostix-report-'+'a'*32+'.zip')).symlink_to(self.fs/'var/log/dmesg')
        with self.assertRaises((OSError,ValueError)): self.controller.read_support_report('a'*32)
        with self.assertRaises((OSError,ValueError)): self.controller.read_support_report('b'*32)

    def test_complete_history_blocks_old_or_different_parameter_core_failure(self):
        for seconds, crowded in ((3, False), (2, True)):
            with self.subTest(seconds=seconds,crowded=crowded):
                self.controller._automatic=None
                for path in (self.root/'results/automatic-runs').glob('*.json'):path.unlink()
                self.failure='cpu'
                self.controller.runner.run('cpu.compute', {'seconds':seconds}, retry_reason='Changed synthetic fixture')
                self.failure=None;self.calls.clear()
                if crowded:
                    for number in range(205):
                        path=self.root/'results'/('zzzz-'+str(number).zfill(4))/'result.json'
                        path.parent.mkdir()
                        APP.diagnostics.atomic_json(path,{'test_id':'audio.info','run_id':path.parent.name,
                            'identity':self.controller.runner.identity(),'phase':'completed','status':'INCONCLUSIVE',
                            'started_at':'9999-'+str(number).zfill(4),'evidence':{},'commands':[]})
                result=self.request();self.assertEqual(result['status'],'BLOCKED');self.assertEqual(self.calls,[])

    def test_ram_failure_blocks_later_checks_and_second_selection(self):
        self.failure='memory';result=self.request()
        self.assertEqual(result['status'],'FAIL');self.assertEqual(len(self.calls),2)
        self.assertTrue(all(step['state']=='not_run' for step in result['steps'][3:]))
        self.request();self.assertEqual(len(self.calls),2)

    def test_terminal_ethernet_and_pass_with_stop_required_never_finish_pass(self):
        original=self.controller.catalogue_test
        def terminal(test,params):
            result=original(test,params)
            if test=='ethernet.status':result['evidence']['mts_report']='{"terminal_fault":1}'
            return result
        with mock.patch.object(self.controller,'catalogue_test',side_effect=terminal):result=self.request()
        self.assertEqual(result['status'],'BLOCKED');self.assertTrue(result['stop_required'])
        self.assertEqual(result['steps'][-1]['status'],'NOT_RUN')

    def test_final_step_identity_change_or_stop_marker_blocks_overall_pass(self):
        original=self.controller.catalogue_test
        def changed(test,params):
            result=original(test,params)
            if test=='storage.inventory':
                (self.fs/'proc/sys/kernel/random/boot_id').write_text('changed-at-final-step')
            return result
        with mock.patch.object(self.controller,'catalogue_test',side_effect=changed):result=self.request()
        self.assertEqual(result['status'],'BLOCKED')

    def test_issue_archive_keeps_controller_gpu_browser_and_wayland_failures(self):
        ids=('gpu.opengl.render','gpu.vulkan.render','browser.webgl','wayland.native','uvd.native720')
        for number,test in enumerate(ids):
            path=self.root/'results'/str(number)/'result.json';path.parent.mkdir(parents=True)
            APP.diagnostics.atomic_json(path,{'test_id':test,'run_id':str(number),
                'identity':self.controller.runner.identity(),'phase':'completed','status':'FAIL',
                'started_at':'2026-11-07T00:00:00Z','message':'Synthetic failed oracle','evidence':{},'commands':[]})
        report=self.request('support.export')
        with zipfile.ZipFile(report['path']) as archive:data=json.loads(archive.read('report.json'))
        self.assertEqual({r['test_id'] for r in data['results']},set(ids))
        self.assertTrue(all(r['status']=='FAIL' for r in data['results']))

    def test_capture_preview_retains_real_tail_past_first_megabyte(self):
        raw=b'HEAD-START\n'+b'x'*(2*1024**2)+b'\nACTUAL-TAIL'
        def execute(argv,timeout,stdout,stderr,*args,**kwargs):
            Path(stdout).write_bytes(raw);Path(stderr).write_bytes(b'')
            return APP.diagnostics.CommandResult(0,raw[:1024**2].decode(),'')
        with mock.patch.object(self.controller.runner.fs,'fixture',False),mock.patch.object(APP.diagnostics,'execute_process',side_effect=execute):
            result=self.controller._cancellable_capture(['/usr/bin/dmesg','--color=never'],timeout=5,text=True,capture_head_tail=512*1024)
        self.assertTrue(result.stdout.startswith('HEAD-START'));self.assertTrue(result.stdout.endswith('ACTUAL-TAIL'))
        self.assertLessEqual(len(result.stdout.encode()),512*1024)

    def test_multibyte_kernel_preview_stays_within_byte_budget(self):
        captured=APP.subprocess.CompletedProcess([],0,'HEAD'+('é'*600000)+'ACTUAL-TAIL','')
        with mock.patch.object(self.controller.runner.fs,'fixture',False),mock.patch.object(self.controller,'_cancellable_capture',return_value=captured):
            report=self.request('support.export')
        with zipfile.ZipFile(report['path']) as archive:raw=archive.read('kernel.log')
        self.assertLessEqual(len(raw),512*1024);self.assertIn(b'ACTUAL-TAIL',raw)

    def test_pass_with_stop_required_blocks_the_batch(self):
        original=self.controller.catalogue_test
        def stopped(test,params):
            result=original(test,params)
            if test=='cpu.compute':result.update(status='PASS',stop_required=True)
            return result
        with mock.patch.object(self.controller,'catalogue_test',side_effect=stopped):result=self.request()
        self.assertEqual(result['status'],'BLOCKED');self.assertTrue(result['stop_required'])
        self.assertEqual(result['steps'][2]['status'],'NOT_RUN');self.assertEqual(len(self.calls),1)

    def test_issue_export_carries_appliance_version(self):
        path=self.fs/'etc/psx-diagnostics/boot-set.json';value=json.loads(path.read_text())
        value['appliance_version']='0.13';path.write_text(json.dumps(value))
        report=self.request('support.export')
        with zipfile.ZipFile(report['path']) as archive:data=json.loads(archive.read('report.json'))
        self.assertEqual(data['build']['appliance_version'],'0.13')

    def test_http_export_requires_local_origin_csrf_and_empty_parameters(self):
        import http.client
        from http.server import ThreadingHTTPServer
        server=ThreadingHTTPServer(('127.0.0.1',0),APP.LabHandler);server.controller=self.controller
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        host='%s:%s'%server.server_address
        headers={'Host':host,'Origin':'http://'+host,'Content-Type':'application/json','X-PSX-CSRF':self.controller.csrf}
        cases=({'X-PSX-CSRF':''},{'X-PSX-CSRF':'wrong'},{'Origin':'https://foreign.invalid'},{'Host':'foreign.invalid'})
        for changed in cases:
            conn=http.client.HTTPConnection(*server.server_address,timeout=3)
            conn.request('POST','/api/support/export','{}',headers|changed);reply=conn.getresponse();reply.read()
            self.assertEqual(reply.status,403);conn.close()
        conn=http.client.HTTPConnection(*server.server_address,timeout=3)
        conn.request('POST','/api/support/export',json.dumps({'path':'/etc/shadow'}),headers);reply=conn.getresponse();reply.read()
        self.assertEqual(reply.status,400);self.assertEqual(self.controller.jobs.list(),[])
        conn.request('POST','/api/support/export','{}',headers);reply=conn.getresponse();job=json.loads(reply.read())
        self.assertEqual(reply.status,202)
        deadline=time.monotonic()+4
        while self.controller.jobs.get(job['id'])['state']=='running' and time.monotonic()<deadline:time.sleep(.01)
        conn.request('GET','/api/support/jobs/'+job['id']);reply=conn.getresponse();record=json.loads(reply.read())
        self.assertEqual(reply.status,200);self.assertEqual(record['result']['status'],'READY');conn.close()

    def test_download_http_streams_only_generated_archive(self):
        import http.client
        from http.server import ThreadingHTTPServer
        report=self.request('support.export')
        server=ThreadingHTTPServer(('127.0.0.1',0),APP.LabHandler); server.controller=self.controller
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        conn=http.client.HTTPConnection(*server.server_address,timeout=3); self.addCleanup(conn.close)
        conn.request('GET',report['download_url']); reply=conn.getresponse(); data=reply.read()
        self.assertEqual(reply.status,200); self.assertEqual(reply.getheader('Content-Type'),'application/zip')
        self.assertTrue(reply.getheader('Content-Disposition').startswith('attachment;'))
        self.assertEqual(data,Path(report['path']).read_bytes())
        conn.request('GET','/api/support/reports/%2e%2e%2fetc%2fshadow.zip'); reply=conn.getresponse();reply.read();self.assertEqual(reply.status,404)

if __name__=='__main__':unittest.main()
