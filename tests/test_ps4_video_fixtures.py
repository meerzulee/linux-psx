# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Meerzulee
"""Host-only native video fixture guards and streaming software-oracle checks."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('psx_video_fixture_tests', str(ROOT/'scripts/prepare-ps4-video-fixtures'))
spec = importlib.util.spec_from_loader(loader.name, loader)
APP = importlib.util.module_from_spec(spec)
loader.exec_module(APP)


def probe(width=1280, height=720, level=31):
    return {'streams': [{'codec_name': 'h264', 'codec_type': 'video', 'profile': 'Constrained Baseline',
        'width': width, 'height': height, 'level': level, 'pix_fmt': 'yuv420p', 'refs': 1,
        'has_b_frames': 0, 'r_frame_rate': '30/1', 'avg_frame_rate': '30/1', 'nb_frames': '900',
        'field_order': 'progressive', 'time_base': '1/30000', 'duration_ts': 900000, 'duration': '30.000000'}],
        'format': {'duration': '30.000000'},
        'frames': [{'pts': index*1000, 'pict_type': 'I' if index % 60 == 0 else 'P', 'interlaced_frame': 0}
                   for index in range(APP.FRAMES)]}


def headers(level=31):
    values = {'profile_idc': 66, 'constraint_set1_flag': 1, 'level_idc': level,
              'max_num_ref_frames': 1, 'entropy_coding_mode_flag': 0,
              'num_slice_groups_minus1': 0, 'frame_mbs_only_flag': 1}
    return ''.join(f'[trace_headers @ synthetic] 12 {name} 001 = {value}\n' for name, value in values.items())


class MetadataTests(unittest.TestCase):
    def test_both_exact_native_profiles_have_all_900_pts_and_30_second_duration(self):
        self.assertEqual(APP.PROFILES['720p30'][:3], (1280, 720, 31))
        self.assertEqual(APP.PROFILES['1080p30'][:3], (1920, 1080, 40))
        for width, height, level, _ in APP.PROFILES.values():
            value = APP.validate_probe(probe(width, height, level), width, height, level)
            self.assertEqual(value['frame_pts'][-1], 899000)
            self.assertEqual(value['frame_pts_us'][:3], [0, 33333, 66667])
            self.assertEqual(value['frame_pts_us'][-1], 29966667)
            self.assertEqual(APP.validate_headers(headers(level), level)['entropy_coding_mode_flag'], 0)

    def test_wrong_dimensions_frame_count_codec_features_and_pts_are_refused(self):
        for key, value in (('width', 1278), ('height', 1080), ('nb_frames', '899'),
                           ('profile', 'High'), ('refs', 2), ('level', 42), ('has_b_frames', 1),
                           ('pix_fmt', 'yuv420p10le'), ('duration_ts', 899000), ('avg_frame_rate', '29/1')):
            document = probe(); document['streams'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): APP.validate_probe(document, 1280, 720, 31)
        for kind in ('missing', 'duplicate-pts', 'last-pts', 'bframe', 'interlaced', 'extra-stream'):
            document = probe()
            if kind == 'missing': document['frames'].pop()
            elif kind == 'duplicate-pts': document['frames'][1]['pts'] = 0
            elif kind == 'last-pts': document['frames'][-1]['pts'] += 1
            elif kind == 'bframe': document['frames'][30]['pict_type'] = 'B'
            elif kind == 'interlaced': document['frames'][30]['interlaced_frame'] = 1
            else: document['streams'].append({'codec_type': 'audio'})
            with self.subTest(kind=kind), self.assertRaises(ValueError): APP.validate_probe(document, 1280, 720, 31)

    def test_actual_sps_pps_cabac_references_and_progressive_guards_cannot_be_replaced_by_cli_intent(self):
        for before, after in (('profile_idc 001 = 66', 'profile_idc 001 = 100'),
                              ('max_num_ref_frames 001 = 1', 'max_num_ref_frames 001 = 2'),
                              ('entropy_coding_mode_flag 001 = 0', 'entropy_coding_mode_flag 001 = 1'),
                              ('frame_mbs_only_flag 001 = 1', 'frame_mbs_only_flag 001 = 0'),
                              ('num_slice_groups_minus1 001 = 0', 'num_slice_groups_minus1 001 = 1')):
            with self.subTest(before=before), self.assertRaises(ValueError):
                APP.validate_headers(headers().replace(before, after), 31)
        with self.assertRaises(ValueError): APP.validate_headers('', 31)


class OracleTests(unittest.TestCase):
    def test_fragmented_stream_hashes_every_frame_without_retaining_raw_frames(self):
        raw = b''.join(bytes([index % 251])*12 for index in range(APP.FRAMES))
        expected = [hashlib.sha256(raw[index*12:(index+1)*12]).hexdigest() for index in range(APP.FRAMES)]
        oracle = APP.FrameOracle(12, expected=expected)
        for offset in range(0, len(raw), 17): oracle.write(raw[offset:offset+17])
        result = oracle.finish()
        self.assertEqual(result['frame_sha256'], expected)
        self.assertEqual(result['frame_md5'], [hashlib.md5(raw[index*12:(index+1)*12], usedforsecurity=False).hexdigest()
                                              for index in range(APP.FRAMES)])
        self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['retained_raw_frame_buffers'], 0)
        self.assertEqual(result['bytes'], len(raw))

    def test_short_extra_and_last_corrupted_frame_never_finish_as_verified(self):
        expected = [hashlib.sha256(b'\0'*12).hexdigest()]*APP.FRAMES
        short = APP.FrameOracle(12); short.write(b'\0'*(12*899+11))
        with self.assertRaisesRegex(ValueError, '900 complete'): short.finish()
        extra = APP.FrameOracle(12)
        with self.assertRaisesRegex(ValueError, 'excess'): extra.write(b'\0'*(12*900+1))
        corrupt = APP.FrameOracle(12, expected=expected)
        corrupt.write(b'\0'*(12*900-1)+b'\1')
        with self.assertRaisesRegex(ValueError, 'oracle mismatch'): corrupt.finish()
        self.assertEqual(corrupt.observations()['mismatched_frames'], [899])


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir='/var/tmp', prefix='psx-video-fixture-tests-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.tool = self.root/'host-tool'; self.tool.write_bytes(b'explicit synthetic executable'); self.tool.chmod(0o755)
        self.output = self.root/'fixtures'
        self.calls = []
        self.bad_probe = False
        self.corrupt = False
        self.timeout = False
        # Keep production's fixed-profile metadata tests above; use tiny frames only
        # to exercise the complete orchestration/oracle failure paths on the CPU.
        patch = mock.patch.object(APP, 'PROFILES', {'720p30': (16, 16, 31, '6000k')})
        patch.start(); self.addCleanup(patch.stop)

    def command(self, argv, **kwargs):
        self.calls.append(argv)
        stdout = stderr = b''
        if self.timeout and '-version' not in argv: raise ValueError('Host video command timed out')
        if '-version' in argv:
            # One test binary represents both tools, retaining each typed version output.
            name = 'ffmpeg' if len(self.calls) == 1 else 'ffprobe'
            stdout = (name+' version synthetic\n').encode()
        elif 'libx264' in argv: Path(argv[-1]).write_bytes(b'synthetic encoded MP4')
        elif '-show_frames' in argv:
            value = probe(16, 16)
            if self.bad_probe: value['streams'][0]['width'] = 18
            stdout = json.dumps(value).encode()
        elif 'trace_headers' in argv: stderr = headers().encode()
        elif 'h264_mp4toannexb' in argv: Path(argv[-1]).write_bytes(b'same synthetic H264 stream')
        elif kwargs.get('sink') is not None:
            frame_bytes = 16*16*3//2
            for index in range(APP.FRAMES):
                frame = bytes([index % 251])*frame_bytes
                if self.corrupt and argv[argv.index('-i')+1].endswith('.mp4') and index == 899:
                    frame = frame[:-1]+bytes([frame[-1] ^ 1])
                kwargs['sink'].write(frame)
        return {'returncode': 0, 'stdout': stdout, 'stderr': stderr,
                'stdout_bytes': kwargs.get('stdout_limit', len(stdout)) if kwargs.get('sink') is not None else len(stdout),
                'stderr_bytes': len(stderr)}

    def prepare(self):
        with mock.patch.object(APP, 'bounded_command', side_effect=self.command):
            return APP.prepare(self.tool, self.tool, self.output,
                               ffmpeg_sha256=APP.digest(self.tool), ffprobe_sha256=APP.digest(self.tool))

    def test_complete_preparation_records_tools_generator_actual_metadata_and_native_oracle(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'READY')
        self.assertFalse(result['hardware_acceptance']); self.assertFalse(result['runtime_integration'])
        fixture = json.loads((self.output/'720p30/FIXTURE.json').read_text())
        self.assertEqual(fixture['codec'], 'h264')
        self.assertEqual(fixture['frame_count'], 900)
        self.assertEqual(len(fixture['frames']), 900)
        self.assertEqual(fixture['frames'][0]['md5'], hashlib.md5(b'\0'*(16*16*3//2), usedforsecurity=False).hexdigest())
        framemd5 = self.output/'720p30/reference-nv12.framemd5'
        self.assertEqual(fixture['reference_framemd5']['sha256'], APP.digest(framemd5))
        decode_loader = importlib.machinery.SourceFileLoader('psx_fixture_existing_md5_consumer', str(ROOT/'userspace/mesa/decode-fractal.py'))
        decode_spec = importlib.util.spec_from_loader(decode_loader.name, decode_loader)
        consumer = importlib.util.module_from_spec(decode_spec); decode_loader.exec_module(consumer)
        self.assertEqual(len(consumer.md5_rows(framemd5, 900, 16*16*3//2)), 900)
        self.assertEqual(fixture['reference']['bytes'], 16*16*3//2*900)
        self.assertFalse(fixture['runtime_available'])
        self.assertEqual(result['ffmpeg']['sha256'], APP.digest(self.tool))
        self.assertEqual(result['ffprobe']['version_sha256'], hashlib.sha256(b'ffprobe version synthetic\n').hexdigest())
        self.assertEqual(result['generator']['tool_sha256'], APP.digest(Path(APP.__file__)))
        self.assertFalse(any(path.suffix in ('.raw', '.nv12') for path in self.output.rglob('*')))
        decodes = [argv for argv in self.calls if '-hwaccel' in argv]
        self.assertEqual(len(decodes), 2)
        self.assertTrue(all(argv[argv.index('-hwaccel')+1] == 'none' for argv in decodes))
        self.assertTrue(all(argv[argv.index('-threads')+1] == '1' for argv in decodes))
        self.assertEqual((self.output/'LICENSE').read_bytes(), (ROOT/'diagnostics/LICENSE').read_bytes())

    def test_wrong_tool_pin_and_existing_output_refuse_before_execution_or_overwrite(self):
        with mock.patch.object(APP, 'bounded_command', side_effect=AssertionError('must not execute')):
            with self.assertRaisesRegex(ValueError, 'SHA256 differs'):
                APP.prepare(self.tool, self.tool, self.output, ffmpeg_sha256='0'*64, ffprobe_sha256=APP.digest(self.tool))
            self.assertFalse(self.output.exists())
            self.output.mkdir(); saved = self.output/'keep'; saved.write_bytes(b'preserved')
            with self.assertRaisesRegex(ValueError, 'new canonical'):
                APP.prepare(self.tool, self.tool, self.output,
                            ffmpeg_sha256=APP.digest(self.tool), ffprobe_sha256=APP.digest(self.tool))
            self.assertEqual(saved.read_bytes(), b'preserved')

    def test_wrong_probe_mismatch_or_timeout_is_durable_failure_before_ready(self):
        for cause in ('bad_probe', 'corrupt', 'timeout'):
            self.output = self.root/cause
            setattr(self, cause, True); self.calls.clear()
            try:
                with self.subTest(cause=cause), self.assertRaises(ValueError): self.prepare()
                result = json.loads((self.output/'build-receipt.json').read_text())
                self.assertEqual(result['status'], 'FAIL')
                self.assertFalse(result['hardware_acceptance'])
                self.assertFalse((self.output/'720p30/FIXTURE.json').exists())
                if cause == 'corrupt':
                    self.assertEqual(result['fixtures']['720p30']['container_software_oracle']['mismatched_frames'], [899])
            finally: setattr(self, cause, False)

    def test_actual_child_timeout_pipe_limit_and_encoded_file_budget_are_enforced(self):
        with self.assertRaisesRegex(ValueError, 'timed out'):
            APP.bounded_command([sys.executable, '-c', 'import time;time.sleep(2)'], timeout=.05)
        with self.assertRaisesRegex(ValueError, 'output exceeded'):
            APP.bounded_command([sys.executable, '-c', 'import os;os.write(1,b"x"*8192)'], stdout_limit=1024)
        target = self.root/'oversized.mp4'
        with mock.patch.object(APP, 'CONTAINER_LIMIT', 128):
            result = APP.bounded_command([sys.executable, '-c',
                'import sys;f=open(sys.argv[1],"wb");f.write(b"x"*1024);f.flush()', str(target)], watched=target)
        self.assertNotEqual(result['returncode'], 0)
        self.assertLessEqual(target.stat().st_size, 128)


if __name__ == '__main__':
    unittest.main()
