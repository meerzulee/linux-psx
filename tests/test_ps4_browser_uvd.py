"""CPU-only protocol/evidence fixtures; no browser, server, device or commands."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT/relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


B = load('browser_uvd_fixture', 'diagnostics/ps4/browser_uvd.py')
U = load('browser_uvd_existing_policy', 'diagnostics/ps4/uvd.py')
TOKEN = 'a'*32


def fixture(frames=120):
    return {'fixture_profile': {120: 'canonical-120', 900: 'continuous-900'}[frames],
            'frames': frames, 'frame_count': frames, 'duration_seconds': {120: 4, 900: 30}[frames],
            'width': 128, 'height': 96, 'fps': '30/1', 'frame_bytes': 18432,
            'frame_pts_us': [round(i*1000000/30) for i in range(frames)]}


def report(frames=120):
    seconds = {120: 4, 900: 30}[frames]
    return {'token': TOKEN, 'outcome': 'ended', 'ended': True, 'error': None,
            'currentTime': seconds, 'duration': seconds,
            'quality': {'totalVideoFrames': frames, 'droppedVideoFrames': 0, 'corruptedVideoFrames': 0},
            'frameCallbacks': frames, 'presentedFrames': frames, 'elapsedMs': seconds*1000}


def evidence(frames=120):
    prefix = '[RDD 77: MediaPDecoder #1]: D/FFmpegVideo FFMPEG: '
    mozilla = prefix+'Initialising VA-API FFmpeg decoder\n'+prefix+'VA-API FFmpeg init successful\n'
    mozilla += ''.join(prefix+f'VA-API frame pts={round(i*1000000/30)} dts=0 duration=33333\n' for i in range(frames))
    trace = ('==========va_TraceCreateContext\ncontext = 0x00000001\n'
             'profile = 5,VAProfileH264Main entrypoint = 1,VAEntrypointVLD\nwidth = 128\nheight = 96\n'
             '=========vaCreateContext ret = VA_STATUS_SUCCESS, success\n')
    for i in range(frames):
        trace += ('==========va_TraceBeginPicture\ncontext = 0x00000001\n'+f'frame_count = #{i}\n'
                  '=========vaBeginPicture ret = VA_STATUS_SUCCESS, success\n'
                  '==========va_TraceEndPicture\ncontext = 0x00000001\n'
                  '=========vaEndPicture ret = VA_STATUS_SUCCESS, success\n'
                  '=========vaSyncSurface ret = VA_STATUS_SUCCESS, success\n')
    process = {'pid': 77, 'rdd': True, 'tids': [88], 'driver_sha256': B.DRIVER_SHA,
               'driver_inode_matches': True, 'libva': '/usr/lib/libva.so.2', 'libavcodec': '/usr/lib/libavcodec.so.63',
               'uids': [1000]*4, 'seccomp': 2, 'no_new_privs': 1}
    kernel = 'PS4_UVD_RUNTIME STARTED\nPS4_UVD_RUNTIME QUEUE_READY\n'
    return {'page': B.page_report(report(frames), TOKEN), 'mozilla': mozilla,
            'traces': {'va.123456.thd-0x00000058': trace}, 'processes': [process],
            'kernel_before': kernel, 'kernel_after': kernel, 'fault_pattern': U.KERNEL_FAULT,
            'expected_fixture': fixture(frames)}


class MediaCacheLimitsTests(unittest.TestCase):
    def test_each_frozen_browser_boot_requires_all_its_exact_artifacts(self):
        for contract in (B.RUNTIME_BOOT, B.ALPHA4_BOOT):
            manifest = dict(contract, manifest_validation={'embedded_root_contract_verified': True})
            self.assertEqual(B.boot_contract(manifest), contract)
            for key in contract:
                with self.subTest(profile=contract['profile'], field=key), self.assertRaises(ValueError):
                    B.boot_contract(dict(manifest, **{key: 'unreviewed'}))
            with self.assertRaises(ValueError):
                B.boot_contract(dict(manifest, manifest_validation={'embedded_root_contract_verified': False}))
        with self.assertRaises(ValueError):
            B.boot_contract(dict(B.RUNTIME_BOOT, profile=B.ALPHA4_BOOT['profile'],
                                 manifest_validation={'embedded_root_contract_verified': True}))

    def test_block_rounding_and_strict_power_of_two_headroom_cover_boundary_and_26mb_clip(self):
        mib = 1024**2
        for size, expected in ((1, 8), (88796, 8), (mib, 8),
                               (8*mib-32768, 8), (8*mib-1, 16), (8*mib, 16),
                               (16*mib-32768, 16), (16*mib-1, 32), (16*mib, 32),
                               (26008330, 32), (128*mib-32768, 128)):
            with self.subTest(size=size):
                limits = B.media_cache_limits(size)
                self.assertEqual(limits['file_limit_bytes'], expected*mib)
                self.assertEqual(limits['cache_block_bytes'], 32768)
                self.assertEqual(limits['rounded_cache_bytes'] % 32768, 0)
                self.assertGreaterEqual(limits['rounded_cache_bytes'], size)
                self.assertGreater(limits['file_limit_bytes'], limits['rounded_cache_bytes'])
                self.assertEqual(limits['log_budget_bytes'], 8*mib)
                self.assertEqual(limits['trace_budget_bytes'], 5*mib)

    def test_invalid_or_block_rounded_oversized_media_is_rejected(self):
        for size in (None, True, False, '26008330', 1.0, 0, -1, 128*1024**2-1,
                     128*1024**2, 129*1024**2):
            with self.subTest(size=size), self.assertRaises(ValueError): B.media_cache_limits(size)

    def test_launch_accepts_only_reviewed_numeric_limits_and_default_stays_8m(self):
        runtime = B.RUNTIME_PARENT/'fixture'
        self.assertIn('--property=LimitFSIZE=8M', B.launch_command(runtime, 'http://127.0.0.1:1234/'))
        limit = B.media_cache_limits(26008330)['file_limit_bytes']
        argv = B.launch_command(runtime, 'http://127.0.0.1:1234/', file_limit_bytes=limit)
        self.assertIn('--property=LimitFSIZE=32M', argv)
        self.assertIn('--property=MemoryMax=1G', argv)
        self.assertIn('--property=User=psx', argv)
        self.assertEqual(sum(value.startswith('--property=LimitFSIZE=') for value in argv), 1)
        for value in ('32M', True, 32.0, 0, 7*1024**2, 16*1024**2-1, 256*1024**2):
            with self.subTest(value=value), self.assertRaises(ValueError):
                B.launch_command(runtime, 'http://127.0.0.1:1234/', file_limit_bytes=value)


class ClassifierTests(unittest.TestCase):
    def test_continuous_900_frame_profile_needs_all_900_frames_and_same_hardware_proof(self):
        item = evidence(900)
        self.assertEqual(B.classify(**item)['status'], 'PASS')
        self.assertEqual(B.classify(**item)['evidence']['frames'], 900)
        for key, value in (('traces', {}), ('processes', []), ('expected_fixture', fixture(120))):
            bad = copy.deepcopy(item); bad[key] = value
            with self.subTest(key=key): self.assertEqual(B.classify(**bad)['status'], 'INCONCLUSIVE')
        bad = copy.deepcopy(item); bad['page']['quality']['totalVideoFrames'] = 899
        self.assertEqual(B.classify(**bad)['status'], 'INCONCLUSIVE')
        bad = copy.deepcopy(item); name = next(iter(bad['traces']))
        bad['traces'][name] = bad['traces'][name].replace('frame_count = #899', 'frame_count = #898')
        self.assertEqual(B.classify(**bad)['status'], 'INCONCLUSIVE')

    def test_only_exact_approved_fixture_profiles_and_matching_timestamp_lengths_are_accepted(self):
        for frames in (120, 900):
            self.assertEqual(len(B.fixture_pts(fixture(frames))), frames)
        for key, value in (('fixture_profile', 'continuous-600'), ('frames', 600), ('frame_count', 120),
                           ('duration_seconds', 20), ('duration_seconds', 30.0), ('fps', '60/1'), ('width', 1920)):
            item = fixture(900); item[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): B.fixture_pts(item)
        item = fixture(900); item['frame_pts_us'] = item['frame_pts_us'][:-1]
        with self.assertRaises(ValueError): B.fixture_pts(item)
        with self.assertRaises(ValueError): B.trace_proof([], 600)

    def test_observed_ended_page_with_unavailable_counter_reports_actual_vaapi_failure(self):
        item = evidence()
        # Minimal portable transcription of EXP009 page.json and RDD messages.
        item['page'] = {'currentTime': 3.99996, 'duration': 3.99996, 'elapsedMs': 4464,
                        'ended': True, 'error': None, 'frameCallbacks': 99, 'outcome': 'ended',
                        'presentedFrames': 120, 'quality': {'corruptedVideoFrames': None,
                        'droppedVideoFrames': 0, 'totalVideoFrames': 120}}
        prefix = '[RDD 17001: MediaPDecoder #1]: D/FFmpegVideo FFMPEG: '
        item['mozilla'] = (prefix+'VA-API FFmpeg init successful\n'+
            prefix+'CreateImageVAAPI(): failed to get VideoFrameSurface\n'+
            prefix+'Initialising FFmpeg decoder\n'+prefix+'Requesting pixel format YUV420P.\n')
        item['traces'] = {}; item['expected_fixture'] = None
        result = B.classify(**item)
        self.assertEqual(result['status'], 'INCONCLUSIVE')
        self.assertIn('CreateImageVAAPI(): failed to get VideoFrameSurface', result['message'])
        self.assertNotIn('Page did not finish', result['message'])
        self.assertIsNone(result['evidence']['corruptedVideoFrames'])
        self.assertIs(result['evidence']['corruptedVideoFrames_available'], False)

    def test_unavailable_corruption_counter_is_not_invented_zero_or_hardware_proof(self):
        item = evidence(); item['page']['quality']['corruptedVideoFrames'] = None
        result = B.classify(**item)
        self.assertEqual(result['status'], 'PASS')
        self.assertIsNone(result['evidence']['corruptedVideoFrames'])
        self.assertIs(result['evidence']['corruptedVideoFrames_available'], False)
        item['traces'] = {}
        self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')
        item = evidence(); item['page']['quality']['corruptedVideoFrames'] = 1
        self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')

    def test_exact_container_timestamp_receipt_replaces_nominal_fps_and_is_required(self):
        item = evidence(); pts = [33333*i for i in range(120)]
        item['mozilla'] = re.sub(r'VA-API frame pts=(-?\d+)',
            lambda match: 'VA-API frame pts='+str(pts[item['expected_fixture']['frame_pts_us'].index(int(match[1]))]), item['mozilla'])
        self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')
        item['expected_fixture']['frame_pts_us'] = pts
        self.assertEqual(B.classify(**item)['status'], 'PASS')
        for invalid in (None, pts[:-1], [0]*120, [False]+pts[1:], [-1]+pts[1:]):
            item['expected_fixture']['frame_pts_us'] = invalid
            self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')

    def test_complete_independent_evidence_can_pass_but_never_claims_pixel_equivalence(self):
        value = B.classify(**evidence())
        self.assertEqual(value['status'], 'PASS')
        self.assertEqual(value['evidence']['vaEndPicture'], 120)
        self.assertIn('no pixel-equivalence', value['message'])

    def test_page_or_initialization_alone_never_establish_hardware(self):
        for field, value in (('traces', {}), ('processes', []), ('mozilla', 'VA-API FFmpeg init successful'),
                             ('page', None), ('complete', False)):
            item = evidence(); item[field] = value
            with self.subTest(field=field): self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')

    def test_fallback_sync_error_kernel_fault_and_wrong_process_are_rejected(self):
        for message in ('vaSyncSurface failed', 'decode error, disable HW acceleration',
                        'needs a new decoder', 'FFMPEG: Requesting pixel format YUV420P.',
                        'RDD process crash', 'Segmentation fault', 'NS_ERROR_DOM_MEDIA_NEED_NEW_DECODER',
                        'decoder recreation'):
            item = evidence(); item['mozilla'] += '\n'+message
            with self.subTest(message=message): self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')
        item = evidence(); item['kernel_after'] += 'PS4_UVD_RUNTIME RETAINED'
        self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')
        for key, value in (('driver_sha256', '0'*64), ('driver_inode_matches', False), ('pid', 78), ('tids', [99]),
                           ('uids', [0]*4), ('seccomp', 0), ('seccomp', None)):
            item = evidence(); item['processes'][0][key] = value
            with self.subTest(key=key): self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')

    def test_recreated_context_even_with_reused_id_or_rdd_cannot_pass(self):
        item = evidence(); key = next(iter(item['traces']))
        item['traces'][key] += '\n==========va_TraceCreateContext\ncontext = 0x00000001\n'
        self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')
        item = evidence(); item['processes'].append(dict(item['processes'][0], pid=99))
        self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')

    def test_repeated_pts_missing_success_and_client_drops_cannot_pass(self):
        item = evidence(); item['mozilla'] = item['mozilla'].replace('pts=33333 ', 'pts=0 ')
        self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')
        for old, new in (('vaSyncSurface ret = VA_STATUS_SUCCESS', 'vaSyncSurface ret = VA_STATUS_ERROR_OPERATION_FAILED'),
                         ('frame_count = #119', 'frame_count = #118')):
            item = evidence(); key = next(iter(item['traces'])); item['traces'][key] = item['traces'][key].replace(old, new)
            self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')
        item = evidence(); item['page']['quality']['droppedVideoFrames'] = 1
        self.assertEqual(B.classify(**item)['status'], 'INCONCLUSIVE')


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.p = B.Protocol({'page': b'html', 'clip': b'0123456789', 'fixture': json.dumps(fixture()).encode()}, TOKEN, 'http://127.0.0.1:1234')
        self.headers = {'host': '127.0.0.1:1234', 'referer': 'http://127.0.0.1:1234/',
                        'origin': 'http://127.0.0.1:1234', 'content-type': 'application/json'}

    def test_session_result_and_clip_are_bounded_and_single_use(self):
        response = self.p.request('GET', '/session', self.headers)
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[2]), {'token': TOKEN, 'fixture_profile': 'canonical-120',
                                                  'frame_count': 120, 'duration_seconds': 4})
        with self.assertRaises(ValueError): self.p.request('GET', '/session', self.headers)
        response = self.p.request('GET', '/clip.mp4', dict(self.headers, range='bytes=2-5'))
        self.assertEqual(response[0], 206); self.assertEqual(response[2], b'2345')
        self.assertEqual(response[1]['Content-Range'], 'bytes 2-5/10')
        body = json.dumps(report()).encode()
        self.assertEqual(self.p.request('POST', '/result', self.headers, body)[0], 200)
        self.assertNotIn('token', self.p.result)
        with self.assertRaises(ValueError): self.p.request('POST', '/result', self.headers, body)

    def test_session_carries_validated_30_second_profile_and_result_remains_bounded(self):
        protocol = B.Protocol({'page': b'html', 'clip': b'0123456789', 'fixture': json.dumps(fixture(900)).encode()},
                              TOKEN, 'http://127.0.0.1:1234')
        response = protocol.request('GET', '/session', self.headers)
        self.assertEqual(json.loads(response[2]), {'token': TOKEN, 'fixture_profile': 'continuous-900',
                                                  'frame_count': 900, 'duration_seconds': 30})
        value = report(900)
        self.assertEqual(B.page_report(value, TOKEN)['presentedFrames'], 900)
        for changes in ({'frameCallbacks': 2001}, {'duration': 32}, {'elapsedMs': 60001}):
            with self.assertRaises(ValueError): B.page_report(dict(value, **changes), TOKEN)

    def test_wrong_host_csrf_unknown_fields_oversized_body_and_ranges_rejected(self):
        with self.assertRaises(ValueError): self.p.request('GET', '/session', dict(self.headers, host='outside.example'))
        self.p.request('GET', '/session', self.headers)
        for value in ('bytes=0-1,3-4', 'bytes=-1', 'bytes=10-20', 'bytes=0-999999999999'):
            with self.subTest(value=value), self.assertRaises(ValueError): B.byte_range(value, 10)
        with self.assertRaises(ValueError): self.p.request('POST', '/result', self.headers, b' '*4097)
        with self.assertRaises(ValueError): self.p.request('POST', '/result', dict(self.headers, origin='http://wrong'), json.dumps(report()).encode())
        with self.assertRaises(ValueError): B.page_report(dict(report(), status='PASS'), TOKEN)
        with self.assertRaises(ValueError): B.page_report(dict(report(), duration=float('nan')), TOKEN)

    def test_head_has_representation_length_and_http_budget_stops_repeated_download(self):
        self.p.request('GET', '/session', self.headers)
        self.assertEqual(len(self.p.request('HEAD', '/clip.mp4', self.headers)[2]), 10)
        self.assertEqual(self.p.served, 0)
        self.p.request('GET', '/clip.mp4', self.headers); self.p.request('GET', '/clip.mp4', self.headers)
        with self.assertRaises(ValueError): self.p.request('GET', '/clip.mp4', self.headers)


class LifecycleTests(unittest.TestCase):
    def test_global_gpu_stop_blocks_even_with_passing_core_reports(self):
        records = [dict(run_id=t, test_id=t, identity={'boot_id': 'boot'}, phase='completed', status='PASS')
                   for t in ('cpu.compute', 'memory.pattern')]
        own = {'run_id': 'own', 'phase': 'preflight'}
        B.history_ready(records+[own], [], 'boot', 'own')
        stopped = dict(run_id='gpu', test_id='gpu.opengl.info', identity={'boot_id': 'boot'},
                       phase='completed', status='INCONCLUSIVE', stop_required=True)
        with self.assertRaises(ValueError): B.history_ready(records+[stopped, own], [], 'boot', 'own')
        running = dict(stopped, stop_required=False, phase='running')
        with self.assertRaises(ValueError): B.history_ready(records+[running], [], 'boot', 'own')

    def test_unresolved_batch_blocks_but_matching_completed_result_supersedes_launch_marker(self):
        batch = dict(run_id='batch', test_id='browser.batch', identity={'boot_id': 'boot', 'manifest': 'exact'},
                     phase='running', automatic=True, status='INCONCLUSIVE')
        with self.assertRaises(ValueError): B.history_ready([], [batch], 'boot', 'own')
        completed = dict(batch, phase='completed', status='PASS')
        B.history_ready([completed], [batch], 'boot', 'own')
        with self.assertRaises(ValueError): B.history_ready([dict(completed, stop_required=True)], [batch], 'boot', 'own')
        with self.assertRaises(ValueError):
            B.history_ready([dict(completed, identity={'boot_id': 'boot', 'manifest': 'different'})], [batch], 'boot', 'own')
        B.history_ready([], [dict(batch, identity={'boot_id': 'other'})], 'boot', 'own')

    def test_ram_fallback_stop_cannot_be_hidden_by_a_visible_usb_pass(self):
        completed = dict(run_id='batch', test_id='browser.batch', identity={'boot_id': 'boot'},
                         phase='completed', status='PASS', stop_required=False)
        launch = dict(completed, phase='running')
        ram = dict(completed, status='INCONCLUSIVE', stop_required=True, usb_save_failed=True)
        for order in ([completed, ram], [ram, completed]):
            with self.assertRaises(ValueError): B.history_ready(order, [launch], 'boot', 'own')

    def test_failed_service_is_not_silently_treated_as_inactive(self):
        runner = object()
        with mock.patch.object(B, 'checked_command', side_effect=['active', '\n'.join(['inactive']*11)]):
            B.service_guard(runner)
        with mock.patch.object(B, 'checked_command', side_effect=['active', '\n'.join(['inactive']*10+['failed'])]):
            with self.assertRaisesRegex(ValueError, 'separate review'): B.service_guard(runner)

    def test_cpu_and_ram_need_latest_matching_oracles_without_running_a_prerequisite(self):
        identity = dict(boot_id='boot', kernel_release='kernel', manifest_sha256='manifest',
                        runner_sha256='runner', catalogue_sha256='catalogue')
        runner = SimpleNamespace(tests={k: {'version': 1} for k in ('cpu.compute', 'memory.pattern')},
                                 validate=lambda test, params: params)
        records = []
        for test, params, oracle in (
                ('cpu.compute', {'seconds': 2}, {'digest': '3951d9a412ce4c815eada76de213f560647cd60d4c37896cbcdcb899b645f197', 'iterations': 1}),
                ('memory.pattern', {'memory_mib': 32}, {'verified_mib': 32, 'patterns': [0, 85, 170, 255]})):
            records.append(dict(test_id=test, phase='completed', status='PASS', hardware_acceptance=False,
                test_version=1, started_at='1', identity=identity, parameters=params, evidence=oracle,
                commands=[{'returncode': 0, 'signal': None, 'timed_out': False, 'interrupted': False, 'output_limited': False}]))
        B.core_ready(records, identity, runner)
        for key, value in (('status', 'FAIL'), ('stop_required', True), ('phase', 'running'),
                           ('identity', dict(identity, manifest_sha256='stale'))):
            bad = copy.deepcopy(records); bad[1][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): B.core_ready(bad, identity, runner)
        bad = copy.deepcopy(records); bad.append(dict(bad[0], started_at='2', status='INCONCLUSIVE'))
        with self.assertRaises(ValueError): B.core_ready(bad, identity, runner)

    def test_unprivileged_fixed_launch_preserves_sandbox_and_installs_log_environment_after_mesa(self):
        runtime = B.RUNTIME_PARENT/'fixture'
        argv = B.launch_command(runtime, 'http://127.0.0.1:1234/')
        self.assertIn('--property=User=psx', argv)
        self.assertIn('--property=RuntimeMaxSec=60', argv)
        self.assertIn('--property=KillMode=control-group', argv)
        self.assertGreater(argv.index('LIBVA_TRACE='+str(runtime/'logs/va')), argv.index('/opt/psx-mesa-tools/with-mesa.py'))
        self.assertEqual(argv[argv.index('--runtime-dir')+1], str(runtime/'mesa'))
        for variable, child in (('HOME', 'home'), ('XDG_CACHE_HOME', 'cache'),
                                ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'), ('TMPDIR', 'tmp')):
            assignment = variable+'='+str(runtime/child)
            self.assertIn('--setenv='+assignment, argv)
            self.assertGreater(argv.index(assignment), argv.index('/usr/bin/env'))
        for channel in ('StandardOutput', 'StandardError'):
            self.assertIn('--property='+channel+'=append:'+str(runtime/'logs/console.log'), argv)
        self.assertEqual(argv[argv.index('--profile')+1], str(runtime/'profile'))
        self.assertGreater(argv.index('__EGL_VENDOR_LIBRARY_FILENAMES=/etc/psx-diagnostics/browser-uvd-egl.json'),
                           argv.index('/usr/bin/env'))
        self.assertGreater(argv.index('/usr/bin/env'), argv.index('/opt/psx-mesa-tools/with-mesa.py'))
        self.assertNotIn('__EGL_VENDOR_LIBRARY_FILENAMES=/run/psx-mesa/1000/egl.json', argv)
        self.assertFalse(any('SANDBOX' in value or 'no-sandbox' in value or 'TRACE_SURFACE' in value or 'TRACE_BUFDATA' in value for value in argv))
        self.assertFalse(any('sandbox' in key for key in B.PREFS))
        self.assertFalse(B.PREFS['media.hardware-video-decoding-vulkan.enabled'])
        self.assertTrue(B.PREFS['media.hardware-video-decoding.enabled'])
        self.assertNotIn('media.ffmpeg.vaapi.enabled', B.PREFS)
        self.assertNotIn('media.ffmpeg.vaapi.force-surface-zero-copy', B.PREFS)
        log = next(value for value in argv if value.startswith('MOZ_LOG='))
        self.assertIn('Dmabuf:5,DmabufRef:5', log)
        self.assertNotIn('DMABuf:', log)

    def test_report_completion_never_reopens_or_rewrites_the_existing_consumed_marker(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            root = Path(temporary); output = root/'run'; output.mkdir()
            identity = {'boot_id': '7dbc7b84-3969-4c09-a67e-dcb6b0ed796e', 'manifest_sha256': 'a'*64}
            runner = SimpleNamespace(results_dir=root, log=SimpleNamespace(directory=output),
                                     fs=SimpleNamespace(fixture=True), identity=lambda: identity)
            marker, _ = U.create_marker(runner, identity, {'experiment_id': 'EXP-20261105-009', 'session_id': 'fixture'})
            before = marker.read_bytes()
            diagnostics = SimpleNamespace(fsync_directory=lambda p: None, timestamp=lambda: 'fixture',
                atomic_json=lambda path, value: path.write_text(json.dumps(value)))
            for status in ('PASS', 'INCONCLUSIVE'):
                B.durable_result(diagnostics, output, {}, {'status': status})
                self.assertEqual(marker.read_bytes(), before)
                self.assertIsNotNone(U.stop_reason(runner))
            with self.assertRaises(FileExistsError): U.create_marker(runner, identity, {'experiment_id': 'EXP-20261105-009', 'session_id': 'fixture'})

    def test_evidence_fsync_failure_precedes_pass_promotion(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
            root = Path(temporary); (root/'trace').write_bytes(b'evidence')
            diagnostics = SimpleNamespace(fsync_directory=lambda p: None, timestamp=lambda: 'fixture', atomic_json=mock.Mock())
            record = {'status': 'INCONCLUSIVE'}
            with mock.patch.object(B.os, 'fsync', side_effect=OSError('sync failed')):
                with self.assertRaises(OSError): B.durable_result(diagnostics, root, record, {'status': 'PASS'})
            self.assertEqual(record['status'], 'INCONCLUSIVE'); diagnostics.atomic_json.assert_not_called()

    def test_default_plan_never_loads_live_runner_starts_server_or_runs_commands(self):
        data = {'clip': b'clip', 'page': b'page', 'fixture': json.dumps(fixture()).encode()}
        args = sum(([f'--{name}', '/unused/'+name, f'--{name}-sha256', 'a'*64] for name in data), [])
        with mock.patch.object(B, 'inputs', return_value=data), mock.patch.object(B, 'runtime_modules', side_effect=AssertionError('live import')), \
             mock.patch.object(B, 'HTTPServer', side_effect=AssertionError('socket')), \
             mock.patch.object(B, 'prepare_egl_selection', side_effect=AssertionError('RAM write')), mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(B.main(args), 0)


class RuntimeStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/var/tmp'); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root/'var').mkdir(mode=0o755)
        self.parent = self.root/B.RUNTIME_PARENT.relative_to('/')
        self.run_id = '20261105T112233Z-0123456789ab'
        self.manifest = dict(B.RUNTIME_BOOT, manifest_validation={'embedded_root_contract_verified': True})
        # The frozen boot bind-mounts /var from its shared 512 MiB tmpfs.
        self.mount = '30 1 0:43 /var /var rw,nosuid,nodev - tmpfs tmpfs rw,size=524288k\n'
        self.budget = {}
        patch = mock.patch.object(B.os, 'statvfs', return_value=self.accounting(200))
        self.statvfs = patch.start(); self.addCleanup(patch.stop)

    @staticmethod
    def accounting(free_mib, capacity=B.RUNTIME_CAPACITY, readonly=False):
        return SimpleNamespace(f_frsize=4096, f_blocks=capacity//4096,
                               f_bavail=free_mib*1024**2//4096, f_bfree=free_mib*1024**2//4096,
                               f_flag=os.ST_RDONLY if readonly else 0)

    def prepare(self, **changes):
        return B.prepare_runtime_storage(self.manifest, self.run_id, self.budget, root=self.root,
            owner=os.geteuid(), browser_uid=os.geteuid(), browser_gid=os.getegid(),
            mountinfo=changes.get('mountinfo', self.mount))

    def test_fresh_private_runtime_keeps_profiles_caches_and_accounting_on_larger_ram_pool(self):
        self.statvfs.side_effect = [self.accounting(200), self.accounting(196)]
        runtime = self.prepare()
        self.assertEqual(runtime, self.parent/self.run_id)
        self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
        for name in ('profile', 'logs', 'home', 'cache', 'config', 'data', 'tmp', 'mesa'):
            self.assertEqual((runtime/name).stat().st_mode & 0o777, 0o700)
            self.assertEqual((runtime/name).stat().st_uid, os.geteuid())
        self.assertEqual((runtime/'profile/user.js').read_bytes(), B.preferences())
        self.assertEqual((runtime/'profile/user.js').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.budget['pre']['available_bytes'], 200*1024**2)
        self.assertEqual(self.budget['pre_intent']['available_bytes'], 196*1024**2)
        self.assertEqual(self.budget['post']['used_bytes'], (512-196)*1024**2)
        self.assertEqual(self.budget['required_free_before_intent_bytes'], 160*1024**2)
        self.assertEqual(self.budget['low_water_free_bytes'], 16*1024**2)
        self.assertFalse(self.budget['low_space_stop'])

    def test_low_budget_or_wrong_capacity_refuses_before_any_profile_allocation(self):
        for accounting in (self.accounting(159), self.accounting(200, capacity=64*1024**2),
                           self.accounting(200, readonly=True)):
            self.statvfs.return_value = accounting
            with self.subTest(accounting=accounting), self.assertRaises(ValueError): self.prepare()
            self.assertFalse(self.parent.exists())
        self.statvfs.return_value = self.accounting(159)
        with self.assertRaisesRegex(ValueError, 'before UVD intent'): self.prepare()
        self.assertEqual(self.budget['pre']['available_bytes'], 159*1024**2)

    def test_only_exact_frozen_boot_and_writable_protected_tmpfs_ancestry_are_accepted(self):
        for mount in (self.mount.replace('tmpfs tmpfs', 'ext4 /dev/sda'),
                      self.mount.replace('rw,nosuid,nodev', 'ro,nosuid,nodev'),
                      self.mount.replace('rw,nosuid,nodev', 'rw,nodev'),
                      self.mount+'31 30 0:44 / /var/lib rw,nosuid,nodev - tmpfs tmpfs rw\n',
                      self.mount+'31 30 0:44 / /var/lib/psx-diagnostics/browser-uvd/hidden rw - tmpfs tmpfs rw\n'):
            with self.subTest(mount=mount), self.assertRaises(ValueError): self.prepare(mountinfo=mount)
            self.assertFalse(self.parent.exists())
        for key in (*B.RUNTIME_BOOT, 'manifest_validation'):
            before = self.manifest[key]; self.manifest[key] = 'wrong'
            try:
                with self.subTest(key=key), self.assertRaises(ValueError): self.prepare()
                self.assertFalse(self.parent.exists())
            finally: self.manifest[key] = before

    def test_symlink_or_writable_parent_is_refused_without_replacing_existing_data(self):
        target = self.root/'original'; target.mkdir(); (target/'keep').write_text('retained')
        lib = self.root/'var/lib'; lib.symlink_to(target)
        with self.assertRaises(OSError): self.prepare()
        self.assertTrue(lib.is_symlink()); self.assertEqual((target/'keep').read_text(), 'retained')
        lib.unlink(); lib.mkdir(mode=0o777); lib.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'protected traversable'): self.prepare()
        self.assertEqual(lib.stat().st_mode & 0o777, 0o777)
        self.assertFalse(self.parent.exists())

    def test_existing_run_is_never_reused_and_retired_profiles_are_not_deleted(self):
        runtime = self.prepare(); profile = runtime/'profile/user.js'; inode = profile.stat().st_ino
        old = self.parent/'retired'; old.mkdir(); (old/'saved').write_bytes(b'old profile')
        with self.assertRaises(FileExistsError): self.prepare()
        self.assertEqual(profile.stat().st_ino, inode)
        self.assertEqual(profile.read_bytes(), B.preferences())
        self.assertEqual((old/'saved').read_bytes(), b'old profile')

    def test_low_water_stop_records_observation_and_never_reopens_consumed_uvd_intent(self):
        runtime = self.prepare()
        output = self.root/'evidence'; output.mkdir()
        identity = {'boot_id': '7dbc7b84-3969-4c09-a67e-dcb6b0ed796e', 'manifest_sha256': 'a'*64}
        runner = SimpleNamespace(results_dir=self.root, log=SimpleNamespace(directory=output),
                                 fs=SimpleNamespace(fixture=True), identity=lambda: identity)
        marker, _ = U.create_marker(runner, identity, {'experiment_id': 'EXP-20261105-009', 'session_id': 'fixture'})
        previous = marker.read_bytes()
        self.statvfs.return_value = self.accounting(16)
        self.assertEqual(B.sample_runtime_space(runtime, self.budget)['available_bytes'], 16*1024**2)
        self.statvfs.return_value = self.accounting(15)
        with self.assertRaisesRegex(ValueError, 'RAM space stop'):
            B.sample_runtime_space(runtime, self.budget)
        self.assertTrue(self.budget['low_space_stop'])
        self.assertEqual(self.budget['space_stop']['required_bytes'], 16*1024**2)
        self.assertEqual(self.budget['post']['available_bytes'], 15*1024**2)
        self.assertEqual(self.budget['minimum_available_bytes'], 15*1024**2)
        self.assertEqual(marker.read_bytes(), previous)
        self.assertIsNotNone(U.stop_reason(runner))

    def test_consumption_during_allocation_is_blocked_before_intent_and_sampler_detects_replaced_storage(self):
        self.statvfs.side_effect = [self.accounting(160), self.accounting(159)]
        with self.assertRaisesRegex(ValueError, 'RAM space stop'): self.prepare()
        runtime = self.parent/self.run_id
        self.assertTrue(runtime.is_dir())
        self.assertEqual(self.budget['space_stop']['required_bytes'], 160*1024**2)
        self.assertFalse(self.budget['low_space_stop'])
        self.statvfs.side_effect = None
        self.statvfs.return_value = self.accounting(200, readonly=True)
        with self.assertRaisesRegex(ValueError, 'read-only'): B.sample_runtime_space(runtime, self.budget)
        original = runtime.with_name('retained'); runtime.rename(original); runtime.symlink_to(original)
        with self.assertRaisesRegex(ValueError, 'path changed'): B.sample_runtime_space(runtime, self.budget)

    def test_run_refuses_media_filesystem_or_space_preflight_before_consuming_attempt(self):
        results = self.root/'results'; results.mkdir()
        identity = {'boot_id': '7dbc7b84-3969-4c09-a67e-dcb6b0ed796e', 'manifest_sha256': 'a'*64}
        runner = SimpleNamespace(lock=mock.MagicMock(), identity=lambda: identity,
                                 manifest_path='unused', commands=[])
        def snapshot(_):
            (runner.log.directory/'kernel.txt').write_text('fixture kernel snapshot')
            runner.commands.append({'stdout': 'kernel.txt'})
        adapter = SimpleNamespace(__file__=U.__file__, ALPHA3=U.ALPHA3,
            stop_reason=lambda _: None, preflight=lambda _: (identity, {}, {}, None, None),
            document=lambda *_: self.manifest, local=lambda *_: None,
            kernel_snapshot=snapshot, create_marker=mock.Mock(side_effect=AssertionError('consumed attempt')))
        diagnostics = SimpleNamespace(__file__=B.__file__, Runner=lambda _: runner,
            timestamp=lambda: 'fixture', Evidence=lambda directory: SimpleNamespace(directory=directory),
            fsync_directory=lambda _: None, atomic_json=lambda path, value: path.write_text(json.dumps(value)))
        owner, group = os.geteuid(), os.getegid()
        original_bounded = B.bounded
        original_prepare = B.prepare_runtime_storage
        def bounded(path, limit, owner=None):
            if path == Path('/usr/lib/firefox/application.ini'): return b'[App]\nVersion=154.0\n'
            return original_bounded(path, limit)
        def fixed_path(path):
            if path == '/run/psx-graphics-user/Xauthority':
                return SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=0o100600, st_uid=1000))
            return Path(path)
        # Only unrelated host/device preflight is synthetic; the storage guard is real.
        for scenario in ('wrong_filesystem', 'low_space', 'invalid_media'):
            low_space = scenario == 'low_space'
            mount = self.mount if low_space else self.mount.replace('tmpfs tmpfs', 'ext4 /dev/sda')
            self.statvfs.return_value = self.accounting(159 if low_space else 200)
            def prepare(manifest, run_id, budget):
                return original_prepare(manifest, run_id, budget, root=self.root, owner=owner,
                    browser_uid=owner, browser_gid=group, mountinfo=mount)
            with self.subTest(scenario=scenario), mock.patch.object(B, 'RESULTS', results), \
                 mock.patch.object(B.os, 'geteuid', return_value=0), mock.patch.object(B, 'Path', side_effect=fixed_path), \
                 mock.patch.object(B, 'runtime_modules', return_value=(diagnostics, adapter)), \
                 mock.patch.object(B, 'bounded', side_effect=bounded), \
                 mock.patch.object(B, 'history_ready'), mock.patch.object(B, 'core_ready'), \
                 mock.patch.object(B, 'service_guard'), mock.patch.object(B, 'proc_snapshot', return_value={}), \
                 mock.patch.object(B.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1000)), \
                 mock.patch.object(B, 'prepare_egl_selection', return_value={}), \
                 mock.patch.object(B, 'prepare_runtime_storage', side_effect=prepare) as allocation, \
                 mock.patch.object(B, 'server', side_effect=AssertionError('server started')) as server:
                record = B.run(SimpleNamespace(results_dir=results), {'clip': b'' if scenario == 'invalid_media' else b'clip'})
                self.assertEqual(record['status'], 'BLOCKED')
                self.assertFalse(record['stop_required'])
                if scenario == 'invalid_media': allocation.assert_not_called()
                else:
                    allocation.assert_called_once()
                    self.assertEqual(record['media_cache_limits']['file_limit_bytes'], 8*1024**2)
                adapter.create_marker.assert_not_called(); server.assert_not_called()
                self.assertEqual(json.loads((Path(record['result_directory'])/'result.json').read_text())['status'], 'BLOCKED')
                self.assertFalse(self.parent.exists())


class EGLSelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir='/var/tmp'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root/'etc/psx-diagnostics').mkdir(parents=True)
        self.path = self.root/B.EGL_SELECTION_PATH.relative_to('/')
        self.mesa = self.root/'opt/psx-mesa'
        (self.mesa/'usr/lib').mkdir(parents=True)
        self.library = self.mesa/'usr/lib/libEGL_mesa.so.0.0.0'
        self.library.write_bytes(b'unchanged pinned EGL provider')
        (self.mesa/'usr/lib/libEGL_mesa.so.0').symlink_to(self.library.name)
        receipt = {'files': {'usr/lib/libEGL_mesa.so.0': {'symlink': self.library.name},
                            'usr/lib/'+self.library.name: {'bytes': self.library.stat().st_size,
                                                         'sha256': B.sha(self.library.read_bytes())}}}
        raw = json.dumps(receipt).encode()
        (self.mesa/'MESA-PSX-CANDIDATE.json').write_bytes(raw)
        patch = mock.patch.object(B, 'MESA_SHA', B.sha(raw)); patch.start(); self.addCleanup(patch.stop)
        selected = dict(B.RUNTIME_BOOT, mesa_manifest_sha256=B.MESA_SHA)
        patch = mock.patch.dict(B.BOOT_CONTRACTS, {selected['profile']: selected})
        patch.start(); self.addCleanup(patch.stop)
        self.manifest = dict(selected, manifest_validation={'embedded_root_contract_verified': True})
        self.mount = '24 1 0:42 /etc /etc rw,nosuid,nodev - tmpfs tmpfs rw,size=524288k\n'

    def prepare(self, **changes):
        return B.prepare_egl_selection(self.manifest, root=self.root, owner=os.geteuid(),
                                       mountinfo=changes.get('mountinfo', self.mount))

    def test_fixed_selector_created_and_exact_protected_file_reused_without_driver_replacement(self):
        before = self.library.read_bytes()
        first = self.prepare(); inode = self.path.stat().st_ino
        self.assertTrue(first['created']); self.assertEqual(self.path.read_bytes(), B.EGL_SELECTION)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o444)
        self.assertEqual(json.loads(self.path.read_bytes())['ICD']['library_path'], B.EGL_LIBRARY)
        self.assertFalse(self.prepare()['created']); self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(self.library.read_bytes(), before)
        self.assertEqual(first['scope'], 'explicit browser process only')
        self.assertIn('untested', first['hypothesis'])

    def test_existing_malformed_writable_symlink_and_shared_files_are_never_replaced(self):
        for mode, content, kind in ((0o444, b'{}', 'file'), (0o644, B.EGL_SELECTION, 'file'),
                                    (0o444, B.EGL_SELECTION, 'symlink'), (0o444, B.EGL_SELECTION, 'hardlink')):
            target = self.root/'target'; target.write_bytes(content); target.chmod(mode)
            if kind == 'symlink': self.path.symlink_to(target)
            elif kind == 'hardlink': os.link(target, self.path)
            else: self.path.write_bytes(content); self.path.chmod(mode)
            inode = self.path.lstat().st_ino
            with self.subTest(kind=kind, mode=mode), self.assertRaises((ValueError, OSError)):
                self.prepare()
            self.assertEqual(self.path.lstat().st_ino, inode)
            self.assertEqual(target.read_bytes(), content)
            self.path.unlink(); target.unlink()

    def test_wrong_boot_writable_parent_disk_etc_and_changed_provider_block_before_creation(self):
        for mount in (self.mount.replace('tmpfs tmpfs', 'ext4 /dev/sda'), self.mount.replace('rw,nosuid,nodev', 'rw')):
            with self.assertRaises(ValueError): self.prepare(mountinfo=mount)
            self.assertFalse(self.path.exists())
        self.path.parent.chmod(0o777)
        with self.assertRaises(ValueError): self.prepare()
        self.path.parent.chmod(0o755)
        old = self.manifest['rootfs_sha256']; self.manifest['rootfs_sha256'] = '0'*64
        with self.assertRaises(ValueError): self.prepare()
        self.manifest['rootfs_sha256'] = old
        self.library.write_bytes(b'wrong provider')
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
