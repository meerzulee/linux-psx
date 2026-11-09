"""Terminal UVD IPC integration; only synthetic workers and filesystem state."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


F = load('terminal_uvd_controller_fixture', ROOT / 'tests/test_ps4_diagnostics_controller.py')
U = load('terminal_uvd_guard_fixture', ROOT / 'diagnostics/ps4/uvd.py')
APP = F.APP
BOOT = '616aaa3f-28bf-48bf-bdf5-a98560a0ec94'


class TerminalTests(unittest.TestCase):
    def setUp(self):
        fixture = F.ControllerTests('test_state_uses_fixture_hardware_and_never_invokes_real_tools')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.controller, self.root = fixture.controller, fixture.fixture
        (self.root / 'proc/sys/kernel/random/boot_id').write_text(BOOT)
        self.manifest_path = self.root / 'etc/psx-diagnostics/boot-set.json'
        manifest = json.loads(self.manifest_path.read_text()); manifest['uvd_runtime'] = True
        self.manifest_path.write_text(json.dumps(manifest))
        self.calls = []; self.selected = []; self.mode = 'pass'
        def execute(argv, timeout, stdout, stderr, env):
            self.assertIn('_worker', argv)
            kind = argv[-2]; self.calls.append(kind)
            value = {'digest': APP.diagnostics.CPU_ANSWER, 'iterations': 2} if kind == 'cpu' else {
                'verified_mib': int(argv[-1]), 'patterns': [0, 85, 170, 255]}
            if self.mode == 'cpu-fail': value['digest'] = 'incorrect'
            return APP.diagnostics.CommandResult(0, json.dumps(value))
        self.controller.runner.executor = execute
        original = APP.importlib.util.spec_from_file_location
        test = self
        class Loader:
            def create_module(self, spec): return None
            def exec_module(self, module): module.run = test.scenario
        def select(name, path):
            if name == 'psx_uvd_terminal_suite': return importlib.machinery.ModuleSpec(name, Loader())
            return original(name, path)
        patch = mock.patch.object(APP.importlib.util, 'spec_from_file_location', side_effect=select)
        patch.start(); self.addCleanup(patch.stop)

    def scenario(self, runner, selected):
        self.selected.append(selected)
        self.assertEqual(self.calls, ['cpu', 'memory'])
        pending = json.loads((runner.log.directory / 'result.json').read_text())
        self.assertEqual(pending['phase'], 'running')
        self.assertTrue(any(value['state'] == 'running' for value in self.controller.jobs.list()))
        if self.mode == 'preflight': return {'status': 'BLOCKED', 'stop_required': False, 'message': 'Missing pinned optional fixture'}
        if self.mode == 'forged': return {'status': 'PASS', 'message': 'Missing terminal guard'}
        U.create_marker(runner, runner.identity(), {'experiment_id': 'EXP-20261105-999', 'session_id': 'synthetic'})
        if self.mode == 'exception': raise ValueError('Synthetic decode failure')
        return {'status': 'PASS', 'stop_required': True, 'hardware_acceptance': False,
                'message': 'Synthetic terminal reference matched', 'evidence': {'scenario': selected}}

    def request(self, scenario='native1080-900'):
        job = self.controller.request('uvd.scenario', {'scenario': scenario})
        deadline = time.monotonic() + 4
        while self.controller.jobs.get(job['id'])['state'] == 'running' and time.monotonic() < deadline: time.sleep(.01)
        value = self.controller.jobs.get(job['id'])
        self.assertNotEqual(value['state'], 'running')
        return value

    def test_saved_job_and_core_precede_terminal_decode_and_native_result_survives_collection(self):
        job = self.request()
        self.assertEqual(job['state'], 'completed')
        value = job['result']; self.assertEqual(value['test_id'], 'uvd.native1080')
        self.assertEqual(value['status'], 'PASS'); self.assertTrue(value['stop_required'])
        self.assertFalse(value['hardware_acceptance']); self.assertEqual(self.selected, ['native1080-900'])
        self.assertIn('uvd.native1080', [item['test_id'] for item in self.controller.request('results.list')])
        with self.assertRaises(ValueError): self.controller.request('uvd.scenario', {'scenario': 'canonical-120'})
        self.assertEqual(len(self.selected), 1)

    def test_three_fixed_scenarios_have_distinct_truthful_report_ids(self):
        self.assertEqual(APP.UVD_SCENARIOS['canonical-120'][0], 'uvd.avc')
        self.assertEqual(APP.UVD_SCENARIOS['native720-900'][0], 'uvd.native720')
        self.assertEqual(APP.UVD_SCENARIOS['native1080-900'][0], 'uvd.native1080')

    def test_unknown_scenarios_extra_params_and_retest_escape_are_refused(self):
        for params in ({}, {'scenario': 'reopen-20'}, {'scenario': ';reboot'}, {'scenario': []},
                       {'scenario': 'native720-900', 'bundle': '/tmp/arbitrary'},
                       {'scenario': 'canonical-120', 'retry_reason': 'ignore intent'}):
            with self.subTest(params=params), self.assertRaises(ValueError): self.controller.request('uvd.scenario', params)
        self.assertFalse(self.calls); self.assertFalse(self.selected); self.assertFalse(self.controller.jobs.list())

    def session_path(self, profile):
        manifest = json.loads(self.manifest_path.read_text()); manifest['profile'] = profile
        self.manifest_path.write_text(json.dumps(manifest))
        path = self.root / APP.UVD_SESSION.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_the_ram_image_writes_a_record_the_decoder_accepts_and_claims_no_uart(self):
        path = self.session_path(APP.RAM_PROFILE)
        self.controller._declare_uvd_session({'boot_id': BOOT})
        value = U.document(path, self.controller.runner, private=True)
        self.assertEqual((value['schema_version'], value['boot_id'], value['state']), (1, BOOT, 'active'))
        self.assertRegex(value['experiment_id'], r'^EXP-[0-9]{8}-000$')
        self.assertIn('no-uart', value['session_id'])

    def test_a_developers_own_session_record_is_kept(self):
        path = self.session_path(APP.RAM_PROFILE)
        path.write_text('{"experiment_id": "EXP-20261110-001"}')
        self.controller._declare_uvd_session({'boot_id': BOOT})
        self.assertEqual(path.read_text(), '{"experiment_id": "EXP-20261110-001"}')

    def test_lab_images_still_need_a_session_record_written_by_hand(self):
        path = self.session_path('ps4-baikal-diagnostics-uvd-alpha8')
        self.controller._declare_uvd_session({'boot_id': BOOT})
        self.assertFalse(path.exists())

    def test_the_record_is_written_before_the_decoder_module_runs(self):
        order = []
        with mock.patch.object(self.controller, '_declare_uvd_session', side_effect=lambda identity: order.append('declared')):
            original = self.scenario
            self.scenario = lambda runner, selected: (order.append('decoder'), original(runner, selected))[1]
            self.request()
        self.assertEqual(order, ['declared', 'decoder'])

    def test_failed_core_never_loads_or_calls_terminal_harness(self):
        self.mode = 'cpu-fail'; job = self.request()
        self.assertEqual(job['result']['status'], 'BLOCKED')
        self.assertEqual(self.calls, ['cpu']); self.assertFalse(self.selected)
        self.assertFalse(U.marker_path(self.controller.runner, BOOT).exists())

    def test_ordinary_profile_is_unsupported_without_core_or_optional_harness(self):
        manifest = json.loads(self.manifest_path.read_text()); manifest['uvd_runtime'] = False
        self.manifest_path.write_text(json.dumps(manifest))
        job = self.request()
        self.assertEqual(job['result']['status'], 'UNSUPPORTED')
        self.assertFalse(self.calls); self.assertFalse(self.selected)
        self.assertFalse(U.marker_path(self.controller.runner, BOOT).exists())

    def test_data_only_preflight_refusal_does_not_claim_or_spend_an_attempt(self):
        self.mode = 'preflight'; job = self.request()
        self.assertEqual(job['result']['status'], 'BLOCKED'); self.assertFalse(job['result']['stop_required'])
        self.assertIn('Missing pinned optional fixture', job['result']['message'])
        self.assertFalse(U.marker_path(self.controller.runner, BOOT).exists())

    def test_exception_and_forged_pass_are_durable_inconclusive_stops(self):
        for mode in ('exception', 'forged'):
            # Each sub-case is a separate synthetic controller/boot, never a retry.
            if mode == 'forged':
                other = TerminalTests(); other.setUp(); self.addCleanup(other.doCleanups); target = other
            else: target = self
            target.mode = mode; job = target.request()
            with self.subTest(mode=mode):
                self.assertEqual(job['result']['phase'], 'completed')
                self.assertEqual(job['result']['status'], 'INCONCLUSIVE'); self.assertTrue(job['result']['stop_required'])
                with self.assertRaises(ValueError): target.controller.ready()

    def test_final_usb_report_failure_cannot_erase_intent_or_promote_pass(self):
        original = APP.diagnostics.atomic_json
        def save(path, value):
            if path.name == 'result.json' and value.get('test_id') == 'uvd.native1080' and value.get('phase') == 'completed':
                raise OSError('Synthetic USB save failure')
            return original(path, value)
        with mock.patch.object(APP.diagnostics, 'atomic_json', side_effect=save): job = self.request()
        self.assertEqual(job['state'], 'failed')
        self.assertTrue(U.marker_path(self.controller.runner, BOOT).exists())
        value = next(value for value in self.controller.results() if value['test_id'] == 'uvd.native1080')
        self.assertEqual(value['phase'], 'running'); self.assertEqual(value['status'], 'INCONCLUSIVE')
        with self.assertRaises(ValueError): self.controller.ready()


if __name__ == '__main__': unittest.main()
