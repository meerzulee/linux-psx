# SPDX-License-Identifier: MIT
"""Overview evidence scope, using synthetic reports and no hardware commands."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('overview_controller_fixture', ROOT / 'tests/test_ps4_diagnostics_controller.py')
BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)


class OverviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        BASE.ControllerTests.setUp(self)

    def record(self, test_id, run_id, status='PASS', boot='fixture-boot', **fields):
        return dict(test_id=test_id, run_id=run_id, status=status, phase='completed',
                    identity={'boot_id': boot}, **fields)

    def state_with(self, reports):
        with mock.patch.object(self.controller, 'results', return_value=reports) as read, \
                mock.patch.object(BASE.APP.subprocess, 'run', side_effect=AssertionError('live command')):
            state = self.controller.request('state')
        read.assert_called_once_with()
        return state

    def test_foundation_result_survives_the_compact_thirty_report_window(self):
        recent = [self.record('browser.capabilities', f'recent-{n}') for n in range(35)]
        cpu = self.record('cpu.compute', 'earlier-current-boot-cpu')
        state = self.state_with(recent + [cpu])
        self.assertEqual(len(state['results']), 30)
        self.assertNotIn('cpu.compute', {r['test_id'] for r in state['results']})
        self.assertIn('cpu.compute', {r['test_id'] for r in state['overview_results']})
        self.assertEqual(next(t for t in state['tests'] if t['id'] == 'cpu.compute')['latest_status'], 'PASS')

    def test_previous_boot_pass_cannot_replace_current_failure(self):
        state = self.state_with([
            self.record('gpu.opengl.render', 'old-pass', boot='another-boot'),
            self.record('gpu.opengl.render', 'current-failure', status='FAIL'),
            self.record('gpu.opengl.render', 'earlier-current-pass')])
        records = [r for r in state['overview_results'] if r['test_id'] == 'gpu.opengl.render']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['run_id'], 'current-failure')
        self.assertEqual(records[0]['status'], 'FAIL')
        self.assertEqual(records[0]['boot_id'], 'fixture-boot')

    def test_newest_unfinished_check_is_not_replaced_by_an_earlier_pass(self):
        recent = self.record('memory.pattern', 'current-running', status='INCONCLUSIVE')
        recent['phase'] = 'running'
        state = self.state_with([recent, self.record('memory.pattern', 'earlier-pass')])
        result = next(r for r in state['overview_results'] if r['test_id'] == 'memory.pattern')
        self.assertEqual(result['phase'], 'running')
        self.assertEqual(result['status'], 'INCONCLUSIVE')

    def test_overview_is_compact_and_never_awards_hardware_acceptance(self):
        state = self.state_with([self.record('uvd.avc', 'decode', stop_required=True,
            measurements={'large': 'x' * (2 * BASE.APP.LIMIT)}, commands=[])])
        result = state['overview_results'][0]
        self.assertNotIn('measurements', result)
        self.assertTrue(result['stop_required'])
        self.assertFalse(state['hardware_acceptance'])
        self.assertLess(len(json.dumps(state).encode()), BASE.APP.LIMIT)


if __name__ == '__main__':
    unittest.main()
