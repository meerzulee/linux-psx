"""Fixed terminal scenarios with synthetic inputs/pipes; no PS4/device access."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


S = load('native_uvd_suite_tests', ROOT / 'diagnostics/ps4/uvd_suite.py')
U = load('native_uvd_adapter_tests', ROOT / 'diagnostics/ps4/uvd.py')
H = load('native_uvd_command_tests', ROOT / 'experiments/ps4-uvd-driver/runtime/playback.py')
BOOT = '616aaa3f-28bf-48bf-bdf5-a98560a0ec94'


def oracle_fixture(count=3):
    frames = [bytes([index % 256]) * 6 for index in range(count)]
    return {'frame_count': count, 'frame_bytes': 6,
        'frames': [{'sha256': hashlib.sha256(frame).hexdigest()} for frame in frames],
        'reference': {'sha256': hashlib.sha256(b''.join(frames)).hexdigest()}}, [
        hashlib.md5(frame, usedforsecurity=False).hexdigest() for frame in frames], frames


class OracleTests(unittest.TestCase):
    def test_fragmented_frames_have_both_oracles_and_no_raw_buffers(self):
        fixture, md5, frames = oracle_fixture()
        oracle = S.FrameOracle(fixture, md5)
        data = b''.join(frames)
        for offset in range(0, len(data), 5): oracle.write(data[offset:offset + 5])
        result = oracle.finish()
        self.assertEqual(result['frames'], 3)
        self.assertEqual(result['nv12_sha256'], fixture['reference']['sha256'])
        self.assertEqual(result['retained_raw_frame_buffers'], 0)
        self.assertEqual(len(result['frame_hashes']), 3)
        self.assertFalse(any(isinstance(value, (bytes, bytearray)) for value in vars(oracle).values()))

    def test_mismatch_extra_truncated_and_false_whole_hash_are_refused(self):
        for fault in ('sha', 'md5', 'extra', 'truncated', 'whole'):
            fixture, md5, frames = oracle_fixture()
            if fault == 'sha': fixture['frames'][0]['sha256'] = '0' * 64
            if fault == 'md5': md5[0] = '0' * 32
            if fault == 'whole': fixture['reference']['sha256'] = '0' * 64
            oracle = S.FrameOracle(fixture, md5); data = b''.join(frames)
            if fault == 'extra': data += b'x'
            if fault == 'truncated': data = data[:-1]
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                oracle.write(data); oracle.finish()

    def test_reference_parser_rejects_wrong_order_size_count_and_oversize(self):
        rows = ''.join(f'0, {index}, {index}, 1, 6, '+('a' * 32)+'\n' for index in range(900)).encode()
        self.assertEqual(len(S.framemd5(rows, 6)), 900)
        for changed in (rows.replace(b'0, 1, 1,', b'0, 2, 1,', 1), rows.replace(b', 6,', b', 8,', 1),
                        rows.splitlines()[0], rows + rows.splitlines()[0], b'#' * (256 * 1024 + 1)):
            with self.assertRaises(ValueError): S.framemd5(changed, 6)


class NativeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/var/tmp'); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.bundle = self.root / 'bundle'; self.bundle.mkdir()
        self.results = self.root / 'results'; self.results.mkdir(mode=0o700)
        self.run_dir = self.results / '20261105T010000Z-123456789abc'; self.run_dir.mkdir(mode=0o700)
        self.fixture, self.md5, self.frames = oracle_fixture(900)
        contract = copy.deepcopy(S.NATIVE['native720-900'])
        contract.update(width=2, height=2)
        directory = self.bundle / contract['directory']; directory.mkdir()
        clip = b'Never decoded synthetic Annex-B input'
        contract.update(clip_bytes=len(clip), clip_sha256=hashlib.sha256(clip).hexdigest(),
                        nv12_sha256=self.fixture['reference']['sha256'])
        fixture = dict(self.fixture, schema='psx-uvd-playback-fixture-v1', fixture_profile=contract['fixture_profile'],
            width=2, height=2, frame_count=900, frame_rate='30/1', duration_seconds=30,
            codec='h264', container='h264-annex-b', pixel_format='nv12', profile='Constrained Baseline', level=31,
            time_base='1/30000', stream_duration_ts=900000, hardware_acceptance=False,
            clip={'path': 'clip.h264', 'bytes': len(clip), 'sha256': contract['clip_sha256']},
            encoding={'bframes': 0, 'cabac': 0, 'gop': 60, 'level': 31,
                      'profile': 'Constrained Baseline', 'ref': 1, 'software_threads': 2},
            headers={'constraint_set1_flag': 1, 'entropy_coding_mode_flag': 0, 'frame_mbs_only_flag': 1,
                'level_idc': 31, 'max_num_ref_frames': 1, 'num_slice_groups_minus1': 0, 'profile_idc': 66},
            frame_pts=list(range(0, 900000, 1000)),
            frame_pts_us=[(index * 1000000 + 15) // 30 for index in range(900)])
        fixture['reference'] = dict(fixture['reference'], bytes=5400)
        fixture['frames'] = [dict(frame, index=index, bytes=6) for index, frame in enumerate(fixture['frames'])]
        (directory / 'FIXTURE.json').write_text(json.dumps(fixture))
        (directory / 'clip.h264').write_bytes(clip)
        md5 = ''.join(f'0, {index}, {index}, 1, 6, {value}\n' for index, value in enumerate(self.md5)).encode()
        (directory / 'reference-nv12.framemd5').write_bytes(md5)
        contract['fixture_sha256'] = hashlib.sha256((directory / 'FIXTURE.json').read_bytes()).hexdigest()
        contract['framemd5_sha256'] = hashlib.sha256(md5).hexdigest()
        self.directory, self.contract = directory, contract
        self.identity = {'boot_id': BOOT, 'kernel_release': 'fixture-alpha4', 'manifest_sha256': 'a' * 64}
        self.session = dict(boot_id=BOOT, experiment_id='EXP-20261105-999', session_id='synthetic-uart', state='active')
        self.paths = {}
        diagnostic = dict(U.DIAGNOSTIC)
        for key in ('ffmpeg', 'driver'):
            path = self.root / key; path.write_text('Never executed/loaded ' + key)
            self.paths[key] = path; diagnostic[key + '_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = self.root / 'manifest.json'; manifest.write_text(json.dumps({'profile': S.PROFILE}))
        node = self.root / U.DIAGNOSTIC['render_node'].lstrip('/'); node.parent.mkdir(parents=True); node.write_text('not a device')
        self.calls = []; self.snapshots = 0; self.fault = None
        self.adapter = types.SimpleNamespace(owner=lambda _: os.geteuid(), read=U.read, document=U.document,
            local=U.local, create_marker=U.create_marker, stop_reason=U.stop_reason, sync=U.sync,
            DIAGNOSTIC=diagnostic, KERNEL_FAULT=U.KERNEL_FAULT, RECOVERY=U.RECOVERY, FINISH=U.FINISH,
            decode=mock.Mock(return_value={'status': 'PASS', 'stop_required': True}))
        def preflight(runner):
            if U.marker_path(runner, BOOT).exists(): raise ValueError('Shared UVD attempt already exists')
            return copy.deepcopy(self.identity), self.session, self.paths, H, {}
        def snapshot(_):
            self.snapshots += 1
            if self.fault == 'kernel' and self.snapshots == 2: raise ValueError('GPU fault detected')
        self.adapter.preflight = preflight; self.adapter.kernel_snapshot = snapshot
        self.runner = types.SimpleNamespace(fs=types.SimpleNamespace(root=self.root, fixture=False),
            results_dir=self.results, manifest_path='/manifest.json', log=types.SimpleNamespace(directory=self.run_dir),
            identity=lambda: copy.deepcopy(self.identity), uvd_adapter=lambda: self.adapter)
        for patch in (mock.patch.dict(S.NATIVE, {'native720-900': contract}), mock.patch.object(U, 'owner', lambda _: os.geteuid()),
                      mock.patch.object(S.stat, 'S_ISCHR', return_value=True)):
            patch.start(); self.addCleanup(patch.stop)

    def capture(self, argv, environment, oracle, output, pattern, monitor):
        self.calls.append(argv)
        self.assertTrue(U.marker_path(self.runner, BOOT).is_file())
        self.assertEqual(argv[argv.index('-hwaccel') + 1], 'vaapi')
        self.assertEqual(argv[argv.index('-hwaccel_output_format') + 1], 'vaapi')
        self.assertEqual(argv[argv.index('-vf') + 1], 'hwdownload,format=nv12')
        self.assertEqual(argv[argv.index('-threads:v') + 1], '1')
        self.assertEqual(argv[argv.index('-filter_threads') + 1], '1')
        self.assertEqual(argv[-2:], ['rawvideo', 'pipe:1'])
        self.assertIn('-noauto_conversion_filters', argv)
        self.assertNotIn('-frames:v', argv)
        (output / 'ffmpeg.stderr.txt').write_text('Synthetic forced VAAPI log; no process spawned\n')
        if self.fault == 'timeout': return {'returncode': -9, 'failure': 'deadline', 'hardware_decode_invoked': True}
        data = b''.join(self.frames)
        if self.fault == 'mismatch': data = b'\xff' + data[1:]
        try:
            for offset in range(0, len(data), 17): oracle.write(data[offset:offset + 17])
        except S.OracleMismatch as error:
            return {'returncode': -9, 'failure': str(error), 'oracle_mismatch': True, 'hardware_decode_invoked': True}
        return {'returncode': 0, 'failure': None, 'hardware_decode_invoked': True}

    def run_scenario(self, scenario='native720-900'):
        with mock.patch.object(S, 'capture', side_effect=self.capture):
            return S.run(self.runner, scenario, bundle=self.bundle)

    def test_native_streaming_pass_reserves_shared_intent_and_never_reopens(self):
        result = self.run_scenario()
        self.assertEqual(result['status'], 'PASS'); self.assertTrue(result['stop_required'])
        self.assertFalse(result['hardware_acceptance'])
        self.assertEqual(result['evidence']['comparison']['frames'], 900)
        marker = U.marker_path(self.runner, BOOT); original = marker.read_bytes()
        again = self.run_scenario()
        self.assertEqual(again['status'], 'BLOCKED'); self.assertTrue(again['stop_required'])
        self.assertEqual(marker.read_bytes(), original); self.assertEqual(len(self.calls), 1)
        self.assertFalse(any(path.suffix == '.nv12' for path in self.run_dir.rglob('*')))
        self.assertEqual(json.loads((self.run_dir / 'uvd-native-reference/RESULT.json').read_text())['status'], 'PASS')

    def test_unsupported_lifecycle_and_encoding_never_import_or_reserve(self):
        self.runner.uvd_adapter = mock.Mock(side_effect=AssertionError('adapter touched'))
        for scenario in ('reopen-20', 'endurance', 'encoding', 'canonical+native', 'unknown'):
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                self.assertIn(result['status'], ('BLOCKED', 'UNSUPPORTED'))
        self.assertFalse(U.marker_path(self.runner, BOOT).exists()); self.assertFalse(self.calls)

    def test_canonical_delegates_unchanged_and_native_profile_refusal_precedes_marker(self):
        self.assertEqual(self.run_scenario('canonical-120')['status'], 'PASS')
        self.adapter.decode.assert_called_once_with(self.runner)
        (self.root / 'manifest.json').write_text(json.dumps({'profile': 'alpha3'}))
        self.assertEqual(self.run_scenario()['status'], 'BLOCKED')
        self.assertFalse(U.marker_path(self.runner, BOOT).exists()); self.assertFalse(self.calls)

    def test_altered_optional_input_or_reference_and_symlinks_refuse_before_intent(self):
        for name in ('FIXTURE.json', 'clip.h264', 'reference-nv12.framemd5'):
            path = self.directory / name; previous = path.read_bytes(); path.write_bytes(previous + b'x')
            self.assertEqual(self.run_scenario()['status'], 'BLOCKED')
            path.write_bytes(previous)
        path = self.directory / 'clip.h264'; path.rename(path.with_suffix('.saved')); path.symlink_to(path.with_suffix('.saved'))
        self.assertEqual(self.run_scenario()['status'], 'BLOCKED')
        self.assertFalse(U.marker_path(self.runner, BOOT).exists()); self.assertFalse(self.calls)

    def test_native_metadata_feature_pts_and_frame_schema_are_independently_checked(self):
        target = self.directory / 'FIXTURE.json'; original = json.loads(target.read_text())
        for field in ('width', 'encoding', 'frame_pts_us', 'frames'):
            changed = copy.deepcopy(original)
            if field == 'width': changed[field] = 1920
            elif field == 'encoding': changed[field]['cabac'] = 1
            elif field == 'frame_pts_us': changed[field][2] = changed[field][1]
            else: changed[field].pop()
            raw = json.dumps(changed).encode(); target.write_bytes(raw)
            self.contract['fixture_sha256'] = hashlib.sha256(raw).hexdigest()
            with self.subTest(field=field), self.assertRaises(ValueError): S.verify_native(self.adapter, self.runner, 'native720-900', self.bundle)
        self.assertFalse(U.marker_path(self.runner, BOOT).exists())

    def test_timeout_retains_intent_and_exact_mismatch_is_fail_not_pass(self):
        self.fault = 'mismatch'; result = self.run_scenario()
        self.assertEqual(result['status'], 'FAIL'); self.assertTrue(result['stop_required'])
        self.assertTrue(U.marker_path(self.runner, BOOT).exists()); self.assertEqual(len(self.calls), 1)

    def test_decoder_deadline_is_inconclusive_and_preserves_reserved_intent(self):
        self.fault = 'timeout'; result = self.run_scenario()
        self.assertEqual(result['status'], 'INCONCLUSIVE'); self.assertTrue(result['stop_required'])
        self.assertTrue(U.marker_path(self.runner, BOOT).is_file()); self.assertEqual(len(self.calls), 1)

    def test_post_decode_kernel_fault_retains_inconclusive_stop(self):
        self.fault = 'kernel'; result = self.run_scenario()
        self.assertEqual(result['status'], 'INCONCLUSIVE'); self.assertTrue(result['stop_required'])
        self.assertEqual(len(self.calls), 1)

    def test_exact_alpha5_profile_uses_the_same_preflight_and_terminal_guard(self):
        (self.root / 'manifest.json').write_text(json.dumps({'profile': 'ps4-baikal-diagnostics-uvd-alpha5'}))
        result = self.run_scenario()
        self.assertEqual(result['status'], 'PASS'); self.assertTrue(result['stop_required'])
        self.assertTrue(U.marker_path(self.runner, BOOT).exists()); self.assertEqual(len(self.calls), 1)

    def test_late_directory_fsync_failure_downgrades_visible_pass_and_keeps_intent(self):
        with mock.patch.object(self.adapter, 'sync', side_effect=[None, OSError('synthetic directory fsync failure'), None]):
            result = self.run_scenario()
        saved = json.loads((self.run_dir / 'uvd-native-reference/RESULT.json').read_text())
        self.assertEqual(result['status'], 'INCONCLUSIVE'); self.assertEqual(saved['status'], 'INCONCLUSIVE')
        self.assertTrue(saved['stop_required']); self.assertTrue(U.marker_path(self.runner, BOOT).exists())

    def test_marker_sync_failure_never_starts_decoder_and_still_closes_boot(self):
        with mock.patch.object(U, 'sync', side_effect=OSError('synthetic fsync error')):
            result = self.run_scenario()
        self.assertEqual(result['status'], 'BLOCKED'); self.assertTrue(result['stop_required'])
        self.assertFalse(self.calls)


class PipeTests(unittest.TestCase):
    def test_unreaped_decoder_is_reported_without_reset_or_a_second_launch(self):
        streams = []
        for _ in range(2):
            read, write = os.pipe(); os.close(write); streams.append(os.fdopen(read, 'rb'))
        process = types.SimpleNamespace(pid=2147483647, stdout=streams[0], stderr=streams[1],
            poll=lambda: None, wait=mock.Mock(side_effect=S.subprocess.TimeoutExpired('synthetic decoder', 5)))
        fixture, md5, _ = oracle_fixture()
        with tempfile.TemporaryDirectory(dir='/var/tmp') as directory, \
             mock.patch.object(S.subprocess, 'Popen', return_value=process) as launch, \
             mock.patch.object(S.os, 'killpg') as stop:
            result = S.capture(['synthetic-never-executed'], {}, S.FrameOracle(fixture, md5),
                Path(directory), U.KERNEL_FAULT, lambda: None, timeout=1)
        self.assertTrue(result['process_may_remain']); self.assertTrue(result['failure'])
        self.assertEqual(launch.call_count, 1); self.assertEqual(stop.call_count, 1)
        self.assertTrue(all(stream.closed for stream in streams))

    def test_real_cpu_pipe_fragmentation_timeout_log_overflow_and_fault_stop(self):
        fixture, md5, frames = oracle_fixture()
        for fault in ('none', 'timeout', 'overflow', 'fault'):
            with tempfile.TemporaryDirectory(dir='/var/tmp') as directory:
                command = "import os,time;os.write(1," + repr(b''.join(frames)) + ");"
                if fault == 'timeout': command += 'time.sleep(5)'
                elif fault == 'overflow': command += "os.write(2,b'x'*4096)"
                elif fault == 'fault': command += "os.write(2,b'GPU fault detected')"
                else: command += "os.write(2,b'synthetic CPU pipe')"
                with mock.patch.object(S, 'LOG_LIMIT', 1024):
                    result = S.capture([sys.executable, '-I', '-c', command], {'PATH': '/usr/bin:/bin'},
                        S.FrameOracle(fixture, md5), Path(directory), U.KERNEL_FAULT, lambda: None,
                        timeout=.15 if fault == 'timeout' else 3)
                with self.subTest(fault=fault):
                    if fault == 'none': self.assertEqual(result['returncode'], 0); self.assertIsNone(result['failure'])
                    else: self.assertTrue(result['failure'])


if __name__ == '__main__': unittest.main()
