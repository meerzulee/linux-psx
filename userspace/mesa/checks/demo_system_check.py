#!/usr/bin/env python3
"""Synthetic installed-player accounting/identity checks; no GPU/process access."""
import hashlib
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / 'demo-system.py'
spec = importlib.util.spec_from_file_location('demo_system', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Checks(unittest.TestCase):
    def test_cpu_comm_parentheses(self):
        fields = ['S'] + ['0'] * 21
        fields[11], fields[12], fields[17], fields[19] = '120', '30', '4', '500'
        self.assertEqual(module.parse_cpu('123 (player (test)) ' + ' '.join(fields)),
                         {'ticks': 150, 'threads': 4, 'start_ticks': 500})

    def test_cpu_units(self):
        first = {'monotonic': 10, 'cpu': {'start_ticks': 1, 'ticks': 100}}
        last = {'monotonic': 12, 'cpu': {'start_ticks': 1, 'ticks': 200}}
        self.assertEqual(module.cpu_rate(first, last, 100)['percent_of_one_cpu_core'], 50)
        last['cpu']['start_ticks'] = 2
        with self.assertRaises(ValueError):
            module.cpu_rate(first, last, 100)

    def test_shared_drm_descriptors(self):
        text = 'drm-driver:\tamdgpu\ndrm-client-id:\t3\ndrm-pdev:\t0000:00:01.0\ndrm-engine-dec:\t100 ns\ndrm-engine-gfx:\t20 ns\n'
        result = module.parse_drm([text, text.replace('100 ns', '110 ns')])
        self.assertEqual(result, {'0000:00:01.0/3': {'dec': 110, 'gfx': 20}})

    def test_missing_decode_accounting_not_zero(self):
        text = 'drm-driver:\tamdgpu\ndrm-client-id:\t3\ndrm-engine-gfx:\t20 ns\n'
        self.assertNotIn('dec', module.parse_drm([text])['/3'])
        self.assertEqual(module.parse_drm(['drm-driver:\ti915\n']), {})

    def test_installed_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'usr/lib').mkdir(parents=True)
            library = root / 'usr/lib/libexample.so.1'
            library.write_bytes(b'pinned-library')
            (root / 'usr/lib/libexample.so').symlink_to(library.name)
            manifest = {'files': {
                'usr/lib/libexample.so.1': {'bytes': 14, 'sha256': hashlib.sha256(b'pinned-library').hexdigest()},
                'usr/lib/libexample.so': {'symlink': library.name}}}
            self.assertEqual(module.verify_installed(manifest, root), 2)
            library.write_bytes(b'changed-library')
            with self.assertRaises(ValueError):
                module.verify_installed(manifest, root)
            with self.assertRaises(ValueError):
                module.verify_installed({'files': {'usr/../escape': {}}}, root)

    def test_kernel_terminal_markers(self):
        for text in ('PS4_UVD_RUNTIME CLOSED error=-5', 'GPU fault detected',
                     'VM fault (0x14)', 'ring gfx timeout', 'IH ring overflow'):
            self.assertIsNotNone(module.KERNEL_FAULT.search(text), text)
        for text in ('PS4_UVD_RUNTIME STARTED', 'PS4_UVD_READY FAULT_COVERAGE no usable counter'):
            self.assertIsNone(module.KERNEL_FAULT.search(text), text)

    def test_journal_cursor_no_entries_and_advance(self):
        responses = [SimpleNamespace(stdout='startup\n-- cursor: first\n'),
                     SimpleNamespace(stdout='-- No entries --\n'),
                     SimpleNamespace(stdout='new line\n-- cursor: second\n')]
        with patch.object(module.subprocess, 'run', side_effect=responses) as run:
            _, cursor = module.kernel_log()
            self.assertEqual(cursor, 'first')
            _, cursor = module.kernel_log(cursor)
            self.assertEqual(cursor, 'first')
            _, cursor = module.kernel_log(cursor)
            self.assertEqual(cursor, 'second')
            self.assertEqual(run.call_args.args[0][-2:], ['--after-cursor', 'first'])

    def test_full_boot_journal_overflow_fails_closed(self):
        response = SimpleNamespace(stdout='x' * (1024 * 1024 + 1))
        with patch.object(module.subprocess, 'run', return_value=response):
            with self.assertRaises(RuntimeError):
                module.kernel_log()

    def test_installed_mapping_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()  # macOS /var aliases /private/var.
            (root / 'usr/lib').mkdir(parents=True)
            manifest = {'files': {}}
            lines = []
            for name in ('libEGL_mesa.so.0.0.0', 'libgallium-26.2.5.so'):
                path = root / 'usr/lib' / name
                path.write_bytes(b'pinned')
                manifest['files'][str(path.relative_to(root))] = {'sha256': module.digest(path), 'bytes': 6}
                lines.append('1-2 r-xp 0 00:00 1 ' + str(path))
            self.assertEqual(len(module.validate_maps('\n'.join(lines), manifest, root)), 2)
            with self.assertRaises(RuntimeError):
                module.validate_maps(lines[0], manifest, root)
            with self.assertRaises(RuntimeError):
                module.validate_maps(lines[0] + ' (deleted)', manifest, root)
            with self.assertRaises(RuntimeError):
                module.validate_maps(lines[0].replace('/usr/lib/', '/home/'), manifest, root)
            (root / 'usr/lib/libgallium-26.2.5.so').write_bytes(b'changed')
            with self.assertRaises(RuntimeError):
                module.validate_maps('\n'.join(lines), manifest, root)

    def observe_fake(self, *, drops=0, timeout=30, kernel_fault=False,
                     initialization_hang=False, postflight_mismatch=False):
        """Exercise the real loop/IPC/progress composition without any live tool."""
        demo_spec = importlib.util.spec_from_file_location('frozen_demo_test', source.with_name('demo.py'))
        demo = importlib.util.module_from_spec(demo_spec)
        demo_spec.loader.exec_module(demo)
        clock = SimpleNamespace(now=0., paused=True, playing_at=0., exit=None)
        child = SimpleNamespace(pid=4321, poll=lambda: clock.exit,
                                wait=lambda timeout: clock.exit,
                                terminate=lambda: setattr(clock, 'exit', -15))

        class IPC:
            def __init__(self, path):
                pass

            def call(self, *command):
                if command[:2] == ('set_property', 'pause'):
                    clock.paused = command[2]
                    clock.playing_at = clock.now
                if command == ('quit',):
                    clock.exit = 0

            def close(self):
                pass

            def snapshot(self):
                return {'hwdec-current': 'vaapi-copy', 'video-codec': 'h264',
                        'video-params': {'w': 1920, 'h': 1080}, 'current-vo': 'gpu',
                        'vo-configured': not initialization_hang, 'pause': clock.paused, 'duration': 4.,
                        'time-pos': 0. if clock.paused else (clock.now - clock.playing_at) % 4.,
                        'frame-drop-count': drops, 'decoder-frame-drop-count': 0}

        def sample(pid):
            clock.now += .01
            return {'monotonic': clock.now, 'cpu': {'start_ticks': 1, 'ticks': int(clock.now * 10)},
                    'drm_clients_scheduler_ns': {}}

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'result'
            args = SimpleNamespace(output=output, clip=Path(tmp) / 'clip', boot_id='mock',
                                   release='mock', experiment='EXP-20261021-999', uart_session='mock',
                                   manifest_sha256='mock', clip_sha256='mock', demo_sha256='mock',
                                   passes=3, timeout=timeout)

            def launch(*argv, **kwargs):
                (output / 'ipc.sock').touch()
                (output / 'mpv.log').write_text('Requesting 1 threads for decoding\n')
                return child

            kernel = ('PS4_UVD_RUNTIME CLOSED error=-5' if kernel_fault else
                      'PS4_UVD_RUNTIME STARTED\nPS4_UVD_RUNTIME QUEUE_READY\n')
            with (patch.object(module.time, 'monotonic', lambda: clock.now),
                  patch.object(module.time, 'sleep', lambda seconds: setattr(clock, 'now', clock.now + seconds)),
                  patch.object(module, 'kernel_log', lambda cursor=None: (kernel, 'mock-cursor')),
                  patch.object(module, 'mapped_installed'), patch.object(module, 'check_identity'),
                  patch.object(module, 'verify_installed', side_effect=ValueError('changed') if postflight_mismatch else None),
                  patch.object(module, 'process_sample', sample), patch.object(module.subprocess, 'Popen', side_effect=launch) as popen,
                  patch.object(demo, 'IPC', IPC),
                  patch.object(demo, 'read_fence', lambda: ({'completed': int(clock.now * 30), 'emitted': int(clock.now * 30)}, 'mock fences')),
                  contextlib.redirect_stdout(io.StringIO())):
                code = module.observe(args, demo, {'files': {}}, {})
            result = json.loads((output / 'RESULT.json').read_text())
            return code, result, popen.call_count

    def test_finite_success_and_normal_quit(self):
        code, result, count = self.observe_fake()
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'PASS_PLAYBACK_OBSERVATION')
        self.assertEqual(result['completed_passes'], 3)
        self.assertEqual(result['mpv_exit'], 0)
        self.assertEqual(count, 1)

    def test_drops_do_not_extend_loop(self):
        code, result, _ = self.observe_fake(drops=1)
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'PLAYBACK_DROPS')
        self.assertEqual(result['completed_passes'], 3)
        self.assertEqual(result['mpv_exit'], 0)

    def test_deadline_is_incomplete_not_hardware_fault(self):
        code, result, _ = self.observe_fake(timeout=3)
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertFalse(result['terminal_kernel_marker'])
        self.assertEqual(result['mpv_exit'], 0)

    def test_terminal_boot_never_launches(self):
        code, result, count = self.observe_fake(kernel_fault=True)
        self.assertEqual(code, 1)
        self.assertEqual(count, 0)
        self.assertTrue(result['terminal_kernel_marker'])

    def test_init_timeout_is_not_a_hardware_fault(self):
        _, result, _ = self.observe_fake(initialization_hang=True)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertFalse(result['terminal_kernel_marker'])

    def test_postflight_identity_failure_stops(self):
        _, result, _ = self.observe_fake(postflight_mismatch=True)
        self.assertEqual(result['status'], 'POSTFLIGHT_FAILED')


if __name__ == '__main__':
    unittest.main()
