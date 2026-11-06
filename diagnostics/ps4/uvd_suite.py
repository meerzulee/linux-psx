#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Explicit terminal UVD scenarios; no retries, reset, power or raw-video files.

Call run(runner, scenario) only inside a saved controller job after its existing
CPU/RAM prerequisite gate. Select one scenario before reserving the shared UVD
intent. This module does not install providers, start a browser or qualify PM.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time


BUNDLE = Path('/run/psx-media/psx-diagnostics/fixtures/native-video')
READ_BYTES = 65536
LOG_LIMIT = 2 * 1024 * 1024
DECODE_TIMEOUT = 180
PROFILE = 'ps4-baikal-diagnostics-uvd-alpha4'
NATIVE = {
    'native720-900': {'directory': '720p30', 'fixture_profile': 'native-720p30-900',
        'width': 1280, 'height': 720, 'level': 31,
        'fixture_sha256': 'c36b64dfddcefbe54f320729b6471b5703d2daedbb886b74d0a9c0be2312fd13',
        'clip_bytes': 13193877, 'clip_sha256': '7acad559ea292fa5121761d1b7d5ab217d3af2d69c987fcd60ec98efa814c1e7',
        'framemd5_sha256': 'c23e88280006c8513bf8775f0b4bbd13f9c5a91d70477414d08e257bf6ee9565',
        'nv12_sha256': '9837b96e00a695681a627ce25ec4f37fb491ea4955f0d28c58cb2d52fb93bb65'},
    'native1080-900': {'directory': '1080p30', 'fixture_profile': 'native-1080p30-900',
        'width': 1920, 'height': 1080, 'level': 40,
        'fixture_sha256': '32f52ec167633dcf6cfea5e525af51097cacf130798cad680c713e9368db73f3',
        'clip_bytes': 26004034, 'clip_sha256': 'e6f417f1de7dcf96bd517b8b12b2fde28579edbe21bed4e3993414332bbeb08b',
        'framemd5_sha256': '923fc8f20dadb89b98f35d59d3b42fcf85e2f6192e5d8ac286fd9f8398733bca',
        'nv12_sha256': 'b88dd37166f35cdfaa60aa130e65cbb8ac4ba0c959d0dc8d30b74e83bde44790'},
}
BLOCKED = {
    'reopen-20': 'Current retained-DMA source and one-attempt policy do not support 20 close/reopen decoders; no decoder was opened.',
    'endurance': 'No frozen long-duration single-session fixture/harness exists. A 30-second clip is not endurance; no loop/seek/reopen was started.',
    'encoding': 'VCE/encoding has no reviewed driver/provider contract; decode fixtures do not establish encoding support.',
}


def require(value, message):
    if not value:
        raise ValueError(message)


def scenarios():
    return [{'id': key, 'available': True, 'scope': 'One terminal reference decode; experimental evidence only'}
            for key in ('canonical-120', *NATIVE)] + [
            {'id': key, 'available': False, 'reason': reason} for key, reason in BLOCKED.items()]


def pinned_file(adapter, runner, path, expected, limit):
    require(path.resolve() == path, 'Native input path contains a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    digest, size = hashlib.sha256(), 0
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == adapter.owner(runner)
                and not info.st_mode & 0o022 and 0 < info.st_size <= limit, 'Native input ownership/type/size differs')
        for block in iter(lambda: stream.read(READ_BYTES), b''):
            size += len(block)
            require(size <= limit, 'Native input grew beyond its limit')
            digest.update(block)
        require(size == info.st_size and (expected is None or digest.hexdigest() == expected),
                'Native input SHA256/length differs')
    return {'path': str(path), 'bytes': size, 'sha256': digest.hexdigest()}


def framemd5(raw, frame_bytes):
    require(len(raw) <= 256 * 1024, 'Reference MD5 exceeds its limit')
    rows = []
    for line in raw.decode('ascii').splitlines():
        if not line.strip() or line.startswith('#'): continue
        fields = [field.strip() for field in line.split(',')]
        require(len(fields) == 6 and all(re.fullmatch('[0-9]+', value) for value in fields[:5]), 'Malformed reference MD5 row')
        index = len(rows)
        require([int(value) for value in fields[:5]] == [0, index, index, 1, frame_bytes]
                and re.fullmatch('[0-9a-f]{32}', fields[5]) and index < 900, 'Reference MD5 frame/order/size differs')
        rows.append(fields[5])
    require(len(rows) == 900, 'Reference MD5 requires exactly 900 frames')
    return rows


def verify_native(adapter, runner, scenario, bundle=BUNDLE):
    contract = NATIVE[scenario]
    directory = Path(bundle) / contract['directory']
    paths = {'fixture': directory / 'FIXTURE.json', 'clip': directory / 'clip.h264',
             'framemd5': directory / 'reference-nv12.framemd5'}
    inputs = {key: pinned_file(adapter, runner, path, contract[
        'fixture_sha256' if key == 'fixture' else 'clip_sha256' if key == 'clip' else 'framemd5_sha256'],
        512 * 1024 if key != 'clip' else 64 * 1024 * 1024) for key, path in paths.items()}
    require(inputs['clip']['bytes'] == contract['clip_bytes'], 'Native clip length differs')
    fixture = adapter.document(paths['fixture'], runner)
    width, height = contract['width'], contract['height']
    frame_bytes = width * height * 3 // 2
    expected = {'schema': 'psx-uvd-playback-fixture-v1', 'fixture_profile': contract['fixture_profile'],
        'width': width, 'height': height, 'frame_count': 900, 'frame_rate': '30/1',
        'duration_seconds': 30, 'frame_bytes': frame_bytes, 'codec': 'h264', 'container': 'h264-annex-b',
        'pixel_format': 'nv12', 'profile': 'Constrained Baseline', 'level': contract['level'],
        'time_base': '1/30000', 'stream_duration_ts': 900000, 'hardware_acceptance': False,
        'reference': {'bytes': frame_bytes * 900, 'sha256': contract['nv12_sha256']},
        'clip': {'path': 'clip.h264', 'bytes': contract['clip_bytes'], 'sha256': contract['clip_sha256']}}
    require(all(type(fixture.get(key)) is type(value) and fixture[key] == value for key, value in expected.items()),
            'Frozen native fixture geometry/profile/oracle differs')
    require(fixture.get('encoding') == {'bframes': 0, 'cabac': 0, 'gop': 60,
        'level': contract['level'], 'profile': 'Constrained Baseline', 'ref': 1, 'software_threads': 2},
        'Native AVC features differ')
    require(fixture.get('headers') == {'constraint_set1_flag': 1, 'entropy_coding_mode_flag': 0,
        'frame_mbs_only_flag': 1, 'level_idc': contract['level'], 'max_num_ref_frames': 1,
        'num_slice_groups_minus1': 0, 'profile_idc': 66}, 'Native bitstream feature review differs')
    require(fixture.get('frame_pts') == list(range(0, 900000, 1000))
            and fixture.get('frame_pts_us') == [(index * 1000000 + 15) // 30 for index in range(900)],
            'Native reference timestamps differ')
    frames = fixture.get('frames')
    require(isinstance(frames, list) and len(frames) == 900 and all(
        isinstance(value, dict) and value.get('index') == index and value.get('bytes') == frame_bytes
        and re.fullmatch('[0-9a-f]{64}', str(value.get('sha256', ''))) for index, value in enumerate(frames)),
        'Native reference frame count/order/hash differs')
    md5 = framemd5(adapter.read(paths['framemd5'], 256 * 1024, owner=adapter.owner(runner)), frame_bytes)
    return paths, fixture, md5, inputs


class OracleMismatch(ValueError):
    pass


class FrameOracle:
    """Hashes chunks directly; neither a complete frame nor raw clip is retained."""
    def __init__(self, fixture, md5):
        self.fixture, self.expected_md5 = fixture, md5
        self.frame_bytes = fixture['frame_bytes']
        self.count = self.offset = self.bytes = 0
        self.whole, self.frame = hashlib.sha256(), hashlib.sha256()
        self.md5 = hashlib.md5(usedforsecurity=False)
        self.rows = []

    def write(self, data):
        require(self.bytes + len(data) <= self.fixture['frame_count'] * self.frame_bytes, 'Decoder returned extra bytes/frames')
        self.bytes += len(data); self.whole.update(data)
        view = memoryview(data)
        while view:
            length = min(len(view), self.frame_bytes - self.offset)
            self.frame.update(view[:length]); self.md5.update(view[:length])
            self.offset += length; view = view[length:]
            if self.offset == self.frame_bytes:
                sha, md5 = self.frame.hexdigest(), self.md5.hexdigest()
                if sha != self.fixture['frames'][self.count]['sha256'] or md5 != self.expected_md5[self.count]:
                    raise OracleMismatch('Decoded frame oracle mismatch at frame ' + str(self.count))
                self.rows.append({'index': self.count, 'sha256': sha, 'md5': md5})
                self.count += 1; self.offset = 0
                self.frame, self.md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)

    def finish(self):
        require(self.offset == 0 and self.count == self.fixture['frame_count'], 'Decoder returned truncated/missing frames')
        if self.whole.hexdigest() != self.fixture['reference']['sha256']:
            raise OracleMismatch('Whole NV12 oracle mismatch')
        return {'pass': True, 'frames': self.count, 'frame_bytes': self.frame_bytes, 'bytes': self.bytes,
            'nv12_sha256': self.whole.hexdigest(), 'frame_hashes': self.rows,
            'retained_raw_frame_buffers': 0, 'pipe_read_bytes': READ_BYTES}


def write_json(path, value):
    data = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    require(len(data) <= 1024 * 1024, 'Native receipt exceeds 1 MiB')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


def decode_command(harness, ffmpeg, node, clip):
    argv = harness.decode_command(ffmpeg, node, clip)
    argv[argv.index('-hwaccel'):argv.index('-hwaccel')] = ['-filter_threads', '1']
    argv[argv.index('-pix_fmt'):argv.index('-pix_fmt')] = ['-threads:v', '1']
    return argv


def process_resources(pid):
    """Bounded child-process observations, not DMA/resource-exhaustion proof."""
    result = {'pid': pid, 'rss_kib': None, 'peak_rss_kib': None, 'open_fds': None}
    try:
        with Path(f'/proc/{pid}/status').open('rb') as stream: raw = stream.read(65537)
        require(len(raw) <= 65536, 'Process status exceeds its limit')
        for field, target in (('VmRSS', 'rss_kib'), ('VmHWM', 'peak_rss_kib')):
            match = re.search(r'^' + field + r':\s+([0-9]+)\s+kB$', raw.decode('ascii', 'replace'), re.M)
            if match: result[target] = int(match[1])
        count = 0
        with os.scandir(f'/proc/{pid}/fd') as entries:
            for _ in entries:
                count += 1
                require(count <= 4096, 'Process FD observation exceeds its limit')
        result['open_fds'] = count
    except (OSError, ValueError):
        result['available'] = False
    else: result['available'] = True
    return result


def capture(argv, environment, oracle, output, fault_pattern, monitor, *, timeout=DECODE_TIMEOUT):
    """One forced decoder process; kill only its process group on fault/timeout."""
    process, started, next_check = None, time.monotonic(), time.monotonic() + 5
    execution = {'argv': argv, 'environment': environment, 'timeout_seconds': timeout,
        'failure': None, 'returncode': None, 'hardware_decode_invoked': False, 'stderr_bytes': 0, 'oracle_mismatch': False,
        'raw_video_retained': False, 'process_resource_samples': [], 'resource_scope': 'Child RSS/FD observations; GPU/DMA ownership not measured'}
    try:
        fd = os.open(output / 'ffmpeg.stderr.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as error:
            process = subprocess.Popen(argv, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            execution['hardware_decode_invoked'] = True
            execution['process_resource_samples'].append(process_resources(process.pid))
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, 'video')
                selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
                tail = ''
                while selector.get_map():
                    now = time.monotonic()
                    require(now - started < timeout, 'Native decode deadline expired; no retry')
                    if now >= next_check:
                        monitor(); next_check = time.monotonic() + 5
                        if len(execution['process_resource_samples']) < 40:
                            execution['process_resource_samples'].append(process_resources(process.pid))
                    for key, _ in selector.select(.1):
                        block = os.read(key.fd, READ_BYTES)
                        if not block:
                            selector.unregister(key.fileobj); continue
                        if key.data == 'video': oracle.write(block)
                        else:
                            execution['stderr_bytes'] += len(block)
                            require(execution['stderr_bytes'] <= LOG_LIMIT, 'FFmpeg stderr exceeded its 2 MiB budget')
                            error.write(block)
                            text = tail + block.decode('utf-8', 'replace')
                            require(not fault_pattern.search(text), 'Decoder reported a terminal UVD/GPU fault')
                            tail = text[-1024:]
                execution['returncode'] = process.wait(timeout=max(.1, timeout - (time.monotonic() - started)))
            error.flush(); os.fsync(error.fileno())
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        execution['failure'] = str(error) or type(error).__name__
        execution['oracle_mismatch'] = isinstance(error, OracleMismatch)
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                execution['returncode'] = process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError) as error:
                execution['failure'] = execution['failure'] or str(error)
                execution['process_may_remain'] = True
            finally:
                process.stdout.close(); process.stderr.close()
        execution['elapsed_seconds'] = round(time.monotonic() - started, 6)
    return execution


def run(runner, scenario, *, bundle=BUNDLE):
    """Explicit scenario selected before intent; caller owns saved job/core gate."""
    if scenario in BLOCKED:
        return {'status': 'BLOCKED', 'message': BLOCKED[scenario], 'hardware_acceptance': False}
    if scenario not in ('canonical-120', *NATIVE):
        return {'status': 'UNSUPPORTED', 'message': 'Unknown fixed UVD scenario; no decoder opened', 'hardware_acceptance': False}
    try:
        adapter = runner.uvd_adapter()
    except (OSError, ValueError, ImportError) as error:
        return {'status': 'BLOCKED', 'message': 'UVD adapter unavailable: ' + str(error), 'hardware_acceptance': False}
    if scenario == 'canonical-120': return adapter.decode(runner)
    try:
        identity, session, paths, harness, _ = adapter.preflight(runner)
        manifest = adapter.document(adapter.local(runner, runner.manifest_path), runner)
        require(manifest.get('profile') in (PROFILE, 'ps4-baikal-diagnostics-uvd-alpha5',
                                          'ps4-baikal-diagnostics-uvd-alpha6', 'ps4-baikal-diagnostics-uvd-alpha7'),
                'Native scenario requires the exact matched alpha4/alpha5/alpha6/alpha7 build')
        native_paths, fixture, md5, inputs = verify_native(adapter, runner, scenario, bundle)
        require(not runner.fs.fixture, 'Fixture roots never spawn a real decoder')
        output = runner.log.directory / 'uvd-native-reference'
        require(output.resolve() == output, 'Native output contains a symlink')
        output.mkdir(mode=0o700)
        driver = runner.fs.root / adapter.DIAGNOSTIC['driver'].lstrip('/')
        node = adapter.local(runner, adapter.DIAGNOSTIC['render_node'])
        require(stat.S_ISCHR(node.stat().st_mode) and os.access(node, os.R_OK | os.W_OK), 'DRM render node is not accessible')
        argv = decode_command(harness, paths['ffmpeg'], node, native_paths['clip'])
        environment = harness.environment(driver)
        write_json(output / 'PREFLIGHT.json', {'scenario': scenario, 'identity': identity,
            'uart': session, 'inputs': inputs, 'argv': argv, 'environment': environment,
            'reference_nv12_bytes': fixture['reference']['bytes'], 'raw_video_retained': False})
        marker, _ = adapter.create_marker(runner, identity, session)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'status': 'BLOCKED', 'message': 'Native UVD preflight blocked: ' + str(error),
                'stop_required': adapter.stop_reason(runner) is not None, 'hardware_acceptance': False}
    result = {'status': 'INCONCLUSIVE', 'scenario': scenario, 'stop_required': True,
        'hardware_acceptance': False, 'message': 'Native UVD evidence incomplete. ' + adapter.RECOVERY,
        'evidence': {'uart': session, 'attempt_receipt': str(marker), 'inputs': inputs,
            'scenario': scenario, 'reference_frames': 900, 'raw_video_retained': False}}
    evidence = result['evidence']
    try:
        adapter.kernel_snapshot(runner)
        oracle = FrameOracle(fixture, md5)
        execution = capture(argv, environment, oracle, output, adapter.KERNEL_FAULT,
                            lambda: adapter.kernel_snapshot(runner))
        evidence['execution'] = execution
        write_json(output / 'EXECUTION.json', execution)
        require(execution['hardware_decode_invoked'] and execution['returncode'] == 0
                and execution['failure'] is None, 'Native decoder failed or exceeded its bounds')
        comparison = oracle.finish()
        write_json(output / 'COMPARISON.json', comparison)
        # Rehash all small/encoded inputs after decode; raw output never exists.
        require(verify_native(adapter, runner, scenario, bundle)[3] == inputs, 'Native inputs changed during decode')
        for key in ('ffmpeg', 'driver'):
            pinned_file(adapter, runner, paths[key], adapter.DIAGNOSTIC[key + '_sha256'], 64 * 1024 * 1024)
        require(runner.identity() == identity, 'Boot/artifact identity changed during native decode')
        adapter.kernel_snapshot(runner)
        evidence['comparison'] = {key: value for key, value in comparison.items() if key != 'frame_hashes'}
        result.update(status='PASS', message='All 900 forced-VAAPI NV12 frames matched SHA256 and MD5 references. Experimental single-run evidence only. ' + adapter.FINISH)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        evidence['incomplete_reason'] = str(error) or type(error).__name__
        if isinstance(error, OracleMismatch) or evidence.get('execution', {}).get('oracle_mismatch'):
            result.update(status='FAIL', message='Native NV12 oracle differs. ' + adapter.RECOVERY)
    try:
        evidence['artifacts'] = {path.name: pinned_file(adapter, runner, path, None,
            1024 * 1024 if path.name != 'ffmpeg.stderr.txt' else LOG_LIMIT) for path in output.iterdir() if path.is_file()}
        for name in evidence['artifacts']:
            with (output / name).open('rb') as stream: os.fsync(stream.fileno())
        adapter.sync(output)
        write_json(output / 'RESULT.json', result)
        adapter.sync(output)
    except (OSError, ValueError) as error:
        result.update(status='INCONCLUSIVE', message='Native evidence could not be saved completely. ' + adapter.RECOVERY)
        evidence['save_error'] = str(error)
        # A late directory-fsync failure must not leave a visible PASS receipt.
        try:
            path = output / 'RESULT.json'
            raw = (json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
            require(len(raw) <= 1024 * 1024, 'Failure receipt exceeds its limit')
            if path.exists():
                fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'wb') as stream:
                    info = os.fstat(stream.fileno())
                    require(stat.S_ISREG(info.st_mode) and info.st_uid == adapter.owner(runner)
                            and info.st_nlink == 1 and not info.st_mode & 0o022, 'Failure receipt is not protected')
                    stream.truncate(0); stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            else: write_json(path, result)
            adapter.sync(output)
        except (OSError, ValueError): pass
    # Never edit/unlink the shared immutable intent, including after PASS.
    return result
