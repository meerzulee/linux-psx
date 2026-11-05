#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Explicit, single-attempt Firefox UVD evidence prototype. Import/plan is inert.

This never resets, rearms or unloads UVD. A reserved attempt is the final active
action of its boot, including PASS. Missing sandboxed logs are INCONCLUSIVE.
Firefox 154 source: dom/media/platforms/ffmpeg/FFmpegVideoDecoder.cpp and
modules/libpref/init/StaticPrefList.yaml at FIREFOX_154_0_RELEASE. The fallback
pref only covers slow-frame fallback; it does not establish hardware decoding.
libva trace grammar: intel/libva 2.24.1 va/va_trace.c (call/return records).
"""
from __future__ import annotations

import argparse
import configparser
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import threading
import time
import uuid

TEST_ID = 'uvd.browser.firefox'
TEST_VERSION = 5
FIXTURE_PROFILES = {'canonical-120': (120, 4), 'continuous-900': (900, 30)}
UNIT = 'psx-browser-uvd.service'
RESULTS = Path('/run/psx-media/psx-diagnostics/results')
ROOT_SHA = '9312ebc229f0b12a9d8586e266dc02da757b302db07a3d4878941d20d8a4ec33'
MESA_SHA = '687af5a366d4bba5af1f72cf5b1a57c4e09d1cfef58257a898ee34da2c7e3964'
DRIVER_SHA = '16d87fdff0011054eeba34e2dff9067519d8756de788c4970cecf4a7038ce4a3'
SOURCE_FIXTURE_SHA = '8999f750efd20cf71f176efb5b5febb0681c493e23ca1dfb6f0bc7bcf8288626'
EGL_SELECTION_PATH = Path('/etc/psx-diagnostics/browser-uvd-egl.json')
EGL_LIBRARY = '/opt/psx-mesa/usr/lib/libEGL_mesa.so.0'
EGL_SELECTION = (json.dumps({'file_format_version': '1.0.0', 'ICD': {'library_path': EGL_LIBRARY}})+'\n').encode()
MAX_LOG = 8 * 1024**2
MAX_TRACE = 5 * 1024**2
MEDIA_CACHE_BLOCK = 32768
DEFAULT_FILE_LIMIT = 8*1024**2
MAX_FILE_LIMIT = 128*1024**2
TIMEOUT = 60
RUNTIME_PARENT = Path('/var/lib/psx-diagnostics/browser-uvd')
RUNTIME_CAPACITY = 512*1024**2
RUNTIME_MIN_FREE = 160*1024**2
RUNTIME_LOW_WATER = 16*1024**2
RUNTIME_BOOT = {'profile':'ps4-baikal-diagnostics-uvd-alpha3', 'rootfs_sha256':ROOT_SHA,
                'kernel_release':'7.2.11-psx-ps4-baikal-uvd-runtime-dev2',
                'kernel_sha256':'6b1d9d90d29eeffb58057cd04a387ad08c53adc7d13afb62bcb2b3af8c686fdc',
                'config_sha256':'51c008bb764dc1cbcc7fb81bf8c164ee23447feb5844c596c53e040bb49df4df',
                'modules_sha256':'a900cf6e72dafbcf5b520c5b83471d6f78ad75eba644178541ad441fce62aef0',
                'mesa_manifest_sha256':MESA_SHA}
PREFS = {
    'layers.acceleration.disabled': False, 'gfx.webrender.software': False,
    'gfx.webrender.all': True, 'media.hardware-video-decoding.enabled': True,
    'media.hardware-video-decoding.force-enabled': True,
    'media.hardware-video-decoding-vulkan.enabled': False,
    'media.sanity-test.disabled': True, 'media.rdd-retryonfailure.enabled': False,
    'media.rdd-process.max-crashes': 0, 'media.video-max-decode-error': 0,
    'media.ffmpeg.disable-software-fallback': True,
    'media.autoplay.default': 0, 'browser.sessionstore.resume_from_crash': False,
    'browser.sessionstore.max_resumed_crashes': 0, 'browser.startup.page': 0,
    'browser.cache.disk.enable': False, 'browser.aboutwelcome.enabled': False,
    'browser.startup.homepage_override.mstone': 'ignore',
    'startup.homepage_welcome_url': '', 'startup.homepage_welcome_url.additional': '',
    'datareporting.policy.firstRunURL': '', 'browser.shell.checkDefaultBrowser': False,
}
BAD_DECODE = re.compile(r'vaSyncSurface failed|vaExportSurfaceHandle.*fail|'
    r'CreateImageVAAPI\(\).*fail|VAAPI .*error|FFMPEG:.*(?:fall.?back|software decoder|'
    r'Requesting pixel format YUV|Failed|Couldn.t)|FFVPX:.*(?:frame|decoder)|'
    r'RDD.*(?:crash|restart)|decode error, disable HW acceleration|needs a new decoder|'
    r'NEED_NEW_DECODER|decoder recreat|vaExportSurfaceHandle.*(?:error|fail)|'
    r'(?:VAAPI|VA-API).*(?:image|sync|export).*fail|Segmentation fault|'
    r'NS_ERROR_DOM_MEDIA_(?:DECODE|FATAL)_ERR', re.I)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def media_cache_limits(clip_bytes):
    """Leave a strict block-rounded margin; this does not widen input profiles."""
    require(type(clip_bytes) is int and 0 < clip_bytes < MAX_FILE_LIMIT,
            'Media clip byte count must be a positive integer below the file-limit cap')
    rounded = ((clip_bytes+MEDIA_CACHE_BLOCK-1)//MEDIA_CACHE_BLOCK)*MEDIA_CACHE_BLOCK
    limit = DEFAULT_FILE_LIMIT
    while limit <= rounded:
        limit *= 2
    require(limit <= MAX_FILE_LIMIT, 'Block-rounded media cache requires a file limit above 128 MiB')
    return {'clip_bytes':clip_bytes,'cache_block_bytes':MEDIA_CACHE_BLOCK,'rounded_cache_bytes':rounded,
            'file_limit_bytes':limit,'file_limit_mib':limit//1024**2,
            'default_file_limit_bytes':DEFAULT_FILE_LIMIT,'maximum_file_limit_bytes':MAX_FILE_LIMIT,
            'log_budget_bytes':MAX_LOG,'trace_budget_bytes':MAX_TRACE}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fixture_spec(fixture):
    require(isinstance(fixture, dict), 'Fixture receipt must be an object')
    profile = fixture.get('fixture_profile')
    require(isinstance(profile, str) and profile in FIXTURE_PROFILES, 'Unknown browser fixture profile')
    frames, duration = FIXTURE_PROFILES[profile]
    require(all(type(fixture.get(key)) is int and fixture[key] == expected for key, expected in
                (('frames', frames), ('frame_count', frames), ('duration_seconds', duration),
                 ('width', 128), ('height', 96), ('frame_bytes', 18432))) and fixture.get('fps') == '30/1',
            'Fixture profile, duration, dimensions or frame counts differ')
    return {'fixture_profile': profile, 'frame_count': frames, 'duration_seconds': duration}


def fixture_pts(fixture):
    """Exact MP4 presentation timestamps supplied by its SHA-pinned receipt."""
    spec = fixture_spec(fixture)
    pts = fixture.get('frame_pts_us')
    require(isinstance(pts, list) and len(pts) == spec['frame_count'] and
            all(type(value) is int and 0 <= value < spec['duration_seconds']*1_000_000 for value in pts) and
            pts[0] == 0 and all(a < b for a, b in zip(pts, pts[1:])),
            'Pinned fixture receipt must contain the selected frame count of exact increasing MP4 frame_pts_us; nominal FPS is not a timestamp oracle')
    return list(pts)


def bounded(path, limit, owner=None):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, 'Input path contains a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit and
                (owner is None or info.st_uid == owner and not info.st_mode & 0o022),
                'Input is not a bounded protected regular file')
        data = stream.read(limit + 1)
    require(len(data) <= limit, 'Input exceeds its bound')
    return data


def inputs(args):
    data = {}
    for name, limit in (('clip', 1024**2), ('page', 64*1024), ('fixture', 256*1024)):
        expected = getattr(args, name + '_sha256')
        require(re.fullmatch('[0-9a-f]{64}', expected) is not None, 'Explicit SHA256 required')
        raw = bounded(getattr(args, name).absolute(), limit)
        require(sha(raw) == expected, 'Staged input changed: ' + name)
        data[name] = raw
    fixture = json.loads(data['fixture'])
    spec = fixture_spec(fixture)
    require(fixture.get('schema_version') == 1 and fixture.get('status') == 'PASS' and
            fixture.get('hardware_acceptance') is False and fixture.get('software_reference_verified') is True and
            fixture.get('source_manifest_sha256') == SOURCE_FIXTURE_SHA and
            fixture.get('page_sha256') == sha(data['page']) and
            fixture.get('container') == {'path': 'clip.mp4', 'bytes': len(data['clip']), 'sha256': sha(data['clip'])},
            'Expected a pinned allowlisted browser fixture receipt')
    oracle = fixture.get('software_oracle', {})
    require(oracle.get('frames') == spec['frame_count'] and oracle.get('mismatched_frames') == [] and
            len(oracle.get('frame_sha256', [])) == spec['frame_count'] and
            oracle.get('sha256') == fixture.get('reference_nv12_sha256'), 'Incomplete CPU fixture oracle')
    fixture_pts(fixture)
    return data


def preferences():
    # There are deliberately no sandbox preferences or legacy vaapi.enabled pref.
    return ''.join('user_pref(' + json.dumps(k) + ', ' + json.dumps(v) + ');\n' for k, v in PREFS.items()).encode()


def page_report(value, token):
    require(isinstance(value, dict) and set(value) == {'token', 'outcome', 'ended', 'error', 'currentTime',
            'duration', 'quality', 'frameCallbacks', 'presentedFrames', 'elapsedMs'}, 'Unknown page result fields')
    require(isinstance(value['token'], str) and secrets.compare_digest(value['token'], token), 'Wrong result token')
    require(value['outcome'] in ('ended', 'error', 'timeout') and type(value['ended']) is bool,
            'Invalid video completion fields')
    require(value['error'] is None or isinstance(value['error'], str) and len(value['error']) <= 160, 'Oversized error')
    for key in ('currentTime', 'duration', 'elapsedMs'):
        n = value[key]
        limit = TIMEOUT*1000 if key == 'elapsedMs' else max(duration for _, duration in FIXTURE_PROFILES.values())+1
        require(n is None or type(n) in (int, float) and math.isfinite(n) and 0 <= n <= limit, 'Invalid video timing')
    q = value['quality']
    require(isinstance(q, dict) and set(q) == {'totalVideoFrames', 'droppedVideoFrames', 'corruptedVideoFrames'}, 'Invalid quality fields')
    for n in (*q.values(), value['presentedFrames']):
        require(n is None or type(n) is int and 0 <= n <= 2000, 'Invalid frame counter')
    require(type(value['frameCallbacks']) is int and 0 <= value['frameCallbacks'] <= 2000, 'Invalid callback counter')
    return {k: v for k, v in value.items() if k != 'token'}


def byte_range(value, length):
    if value is None:
        return 0, length-1, 200
    match = re.fullmatch(r'bytes=([0-9]{1,9})-([0-9]{0,9})', value)
    require(match is not None, 'Only one bounded byte range is allowed')
    start, end = int(match[1]), int(match[2]) if match[2] else length-1
    require(0 <= start <= end < length, 'Range outside clip')
    return start, end, 206


class Protocol:
    """Pure bounded protocol, independently testable without opening a socket."""
    def __init__(self, data, token, origin):
        self.data, self.token, self.origin = data, token, origin
        self.fixture = fixture_spec(json.loads(data['fixture']))
        self.session_sent = False
        self.result = None
        self.requests = self.served = 0

    def request(self, method, path, headers, body=b''):
        self.requests += 1
        require(self.requests <= 64 and headers.get('host') == self.origin.removeprefix('http://'), 'Request/host bound')
        require(not headers.get('transfer-encoding'), 'Chunked input is not supported')
        common = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                  'Content-Security-Policy': "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; media-src 'self'; connect-src 'self'"}
        if method == 'GET' and path == '/':
            return 200, dict(common, **{'Content-Type': 'text/html; charset=utf-8'}), self.data['page']
        if method == 'GET' and path == '/session':
            require(not self.session_sent and headers.get('referer') == self.origin+'/', 'Session is single-use and same-origin')
            self.session_sent = True
            return 200, dict(common, **{'Content-Type': 'application/json'}), json.dumps({'token': self.token, **self.fixture}).encode()
        if method in ('GET', 'HEAD') and path == '/clip.mp4':
            require(self.session_sent, 'Session must precede video')
            start, end, status = byte_range(headers.get('range'), len(self.data['clip']))
            value = self.data['clip'][start:end+1]
            self.served += len(value) if method == 'GET' else 0
            require(self.served <= 2*len(self.data['clip']), 'Clip request budget exceeded')
            h = dict(common, **{'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes'})
            if status == 206:
                h['Content-Range'] = f'bytes {start}-{end}/{len(self.data["clip"])}'
            return status, h, value  # HEAD retains the representation length.
        if method == 'POST' and path == '/result':
            require(self.session_sent and self.result is None and headers.get('origin') == self.origin and
                    headers.get('content-type') == 'application/json' and 0 < len(body) <= 4096, 'Result request rejected')
            value = json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
            self.result = page_report(value, self.token)
            return 200, dict(common, **{'Content-Type': 'application/json'}), b'{"saved":true}'
        return 404, common, b''


def server(data):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def handle_one_request(self):
            self.connection.settimeout(1)
            super().handle_one_request()

        def dispatch(self):
            try:
                require(sum(len(k)+len(v) for k, v in self.headers.items()) <= 8192, 'Header bound')
                length = self.headers.get('Content-Length', '0')
                require(re.fullmatch('[0-9]{1,4}', length) is not None and int(length) <= 4096, 'Body bound')
                body = self.rfile.read(int(length))
                require(len(body) == int(length), 'Short request')
                code, headers, value = self.server.protocol.request(self.command, self.path,
                                               {k.lower(): v for k, v in self.headers.items()}, body)
            except (OSError, ValueError, TypeError, KeyError):
                code, headers, value = 400, {}, b''
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header('Content-Length', str(len(value)))
            self.send_header('Connection', 'close'); self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(value)
        do_GET = do_HEAD = do_POST = dispatch

    http = HTTPServer(('127.0.0.1', 0), Handler)
    origin = 'http://127.0.0.1:' + str(http.server_port)
    http.protocol = Protocol(data, secrets.token_urlsafe(32), origin)
    thread = threading.Thread(target=http.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
    thread.start()
    return http, thread


def core_ready(records, identity, runner):
    pins = ('boot_id', 'kernel_release', 'manifest_sha256', 'runner_sha256', 'catalogue_sha256')
    for test_id in ('cpu.compute', 'memory.pattern'):
        prior = sorted((r for r in records if r.get('test_id') == test_id and not r.get('prerequisite_blocked')
                        and r.get('identity', {}).get('boot_id') == identity['boot_id']),
                       key=lambda r: str(r.get('started_at', '')), reverse=True)
        require(prior, 'Current-boot CPU and RAM PASS reports are required')
        r = prior[0]; e = r.get('evidence', {}); parameters = r.get('parameters', {}); commands = r.get('commands', [])
        oracle = (e.get('digest') == '3951d9a412ce4c815eada76de213f560647cd60d4c37896cbcdcb899b645f197'
                  and type(e.get('iterations')) is int and e['iterations'] > 0) if test_id == 'cpu.compute' else (
                  e.get('verified_mib') == parameters.get('memory_mib') and e.get('patterns') == [0, 85, 170, 255])
        require(r.get('status') == 'PASS' and r.get('phase') == 'completed' and not r.get('stop_required') and
                r.get('hardware_acceptance') is False and r.get('test_version') == runner.tests[test_id]['version'] and
                all(r.get('identity', {}).get(k) == identity[k] for k in pins) and
                runner.validate(test_id, parameters) == parameters and oracle and len(commands) == 1 and
                commands[0].get('returncode') == 0 and commands[0].get('signal') is None and
                all(commands[0].get(k) is False for k in ('timed_out', 'interrupted', 'output_limited')),
                'Latest CPU/RAM report does not establish the exact current-boot oracle')


def history_ready(records, batches, boot_id, own_run_id):
    """Keep global stop state; completed batch results supersede launch markers."""
    indexed = {}
    for record in records:
        require(isinstance(record, dict), 'Malformed saved diagnostic report')
        if record.get('run_id') == own_run_id:
            continue
        identity = record.get('identity')
        require(isinstance(identity, dict), 'Saved report has no verifiable boot identity')
        if identity.get('boot_id') != boot_id:
            continue
        require(not record.get('stop_required') and record.get('phase') not in ('running', 'preflight'),
                'An earlier same-boot diagnostic report requires recovery')
        indexed[record.get('run_id')] = record
    for batch in batches:
        require(isinstance(batch, dict) and isinstance(batch.get('identity'), dict), 'Malformed saved browser batch')
        if batch['identity'].get('boot_id') != boot_id:
            continue
        completed = indexed.get(batch.get('run_id'))
        if (completed and completed.get('test_id') == batch.get('test_id') == 'browser.batch' and
                completed.get('phase') == 'completed' and completed.get('identity') == batch['identity']):
            continue
        require(not batch.get('stop_required') and batch.get('phase') == 'completed',
                'An earlier same-boot automatic browser batch is unresolved')


def trace_proof(texts, frame_count):
    require(type(frame_count) is int and frame_count in {count for count, _ in FIXTURE_PROFILES.values()},
            'Unknown VA frame-count profile')
    text = '\n'.join(texts)
    require(text and not re.search(r'\bret\s*=\s*(?!VA_STATUS_SUCCESS\b)VA_STATUS_', text), 'Missing or failed VA call returns')
    blocks = re.split(r'={10}va_Trace([A-Za-z0-9_]+)\n', text)
    calls = [(blocks[i], blocks[i+1]) for i in range(1, len(blocks), 2)]
    contexts = [body for name, body in calls if name == 'CreateContext']
    require(len(contexts) == 1, 'Exactly one VA context must be proven')
    context = re.search(r'\bcontext = (0x[0-9a-fA-F]+)', contexts[0])
    require(context and 'VAEntrypointVLD' in contexts[0] and 'VAProfileH264' in contexts[0]
            and re.search(r'\bwidth = 128\b', contexts[0]) and re.search(r'\bheight = 96\b', contexts[0]), 'Wrong VA decode context')
    begins = [body for name, body in calls if name == 'BeginPicture']
    require(len(begins) == frame_count and all(re.search(r'\bcontext = '+context[1]+r'\b', body) for body in begins), 'Wrong VA submissions')
    frames = [int(m[1]) for body in begins if (m := re.search(r'frame_count\s*= #(\d+)', body))]
    require(sorted(frames) == list(range(frame_count)), 'VA frame sequence incomplete or repeated')
    counts = {name: len(re.findall(r'={9}'+name+r' ret = VA_STATUS_SUCCESS\b', text))
              for name in ('vaCreateContext', 'vaBeginPicture', 'vaEndPicture', 'vaSyncSurface')}
    require(counts['vaCreateContext'] == 1 and counts['vaBeginPicture'] == counts['vaEndPicture'] == frame_count
            and counts['vaSyncSurface'] >= frame_count, 'VA submissions/completions are not positively proven')
    return dict(counts, context=context[1])


def classify(page, mozilla, traces, processes, kernel_before, kernel_after, fault_pattern, complete=True,
             expected_fixture=None):
    facts = {}
    quality = page.get('quality', {}) if isinstance(page, dict) else {}
    if isinstance(quality, dict):
        facts['corruptedVideoFrames'] = quality.get('corruptedVideoFrames')
        facts['corruptedVideoFrames_available'] = quality.get('corruptedVideoFrames') is not None
    try:
        failure = BAD_DECODE.search(mozilla)
        if failure:
            line = mozilla[mozilla.rfind('\n', 0, failure.start())+1:].split('\n', 1)[0].strip()[:512]
            facts['firefox_decoder_error'] = line
            raise ValueError('Firefox hardware decode error or fallback: ' + line)
        require(complete, 'Evidence was truncated, lost or timed out')
        spec = fixture_spec(expected_fixture)
        frames, duration = spec['frame_count'], spec['duration_seconds']
        expected = fixture_pts(expected_fixture)
        facts['fixture'] = spec
        require(page and page['outcome'] == 'ended' and page['ended'] is True and page['error'] is None and
                isinstance(quality, dict) and quality.get('totalVideoFrames') == frames and
                quality.get('droppedVideoFrames') == 0 and quality.get('corruptedVideoFrames') in (0, None) and
                page['presentedFrames'] == frames and 1 <= page['frameCallbacks'] <= frames and
                type(page['duration']) in (int, float) and abs(page['duration']-duration) < .1 and
                abs(page['currentTime']-page['duration']) < .1, 'Page completion/counters do not match the selected fixture')
        require(not fault_pattern.search(kernel_before+'\n'+kernel_after), 'Kernel fault or retained/closed runtime')
        require(all('PS4_UVD_RUNTIME '+s in kernel_before for s in ('STARTED', 'QUEUE_READY')), 'Kernel queue readiness absent')
        rdds = [p for p in processes if p.get('rdd')]
        require(len(rdds) == 1 and rdds[0].get('driver_sha256') == DRIVER_SHA and
                rdds[0].get('driver_inode_matches') is True and rdds[0].get('libva') and rdds[0].get('libavcodec'),
                'One decoder process with the pinned loaded VA driver must be observed')
        require(rdds[0].get('uids') == [1000]*4 and rdds[0].get('seccomp') == 2,
                'The actual decoder must retain psx credentials and its seccomp sandbox')
        pid = rdds[0]['pid']
        lines = [line for line in mozilla.splitlines() if re.search(r'\[(?:RDD|Child) '+str(pid)+r':', line)]
        own = '\n'.join(lines)
        require(mozilla.count('VA-API FFmpeg init successful') == own.count('VA-API FFmpeg init successful') == 1 and
                mozilla.count('Initialising VA-API FFmpeg decoder') == 1, 'Missing or repeated VAAPI decoder initialization')
        pts = [int(x) for x in re.findall(r'VA-API frame pts=(-?\d+)\b', own)]
        require(len(pts) == frames and len(set(pts)) == frames and all(abs(a-b) <= 1 for a,b in zip(sorted(pts), expected)),
                'RDD VAAPI frame timestamps do not match the exact pinned MP4 timestamp receipt')
        facts['timestamp_oracle'] = {'source': 'SHA-pinned fixture frame_pts_us', 'frames': len(expected),
                                     'first_us': expected[0], 'last_us': expected[-1]}
        tids = set(rdds[0].get('tids', []))
        require(traces and all((m := re.search(r'thd-0x([0-9a-fA-F]+)$', name)) and int(m[1], 16) in tids for name in traces),
                'VA trace files are not tied to the observed decoder threads')
        facts.update(trace_proof(traces.values(), frames), decoder_pid=pid, frames=frames)
        return {'status': 'PASS', 'message': 'One browser VAAPI decode completed with corroborating driver and call evidence; no pixel-equivalence or hardware acceptance claim.', 'evidence': facts}
    except (ValueError, KeyError, TypeError) as error:
        return {'status': 'INCONCLUSIVE', 'message': str(error), 'evidence': facts}


def runtime_modules():
    result = []
    for name in ('runner', 'uvd'):
        path = Path('/opt/psx-diagnostics')/(name+'.py')
        spec = importlib.util.spec_from_file_location('browser_uvd_'+name, path)
        module = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        result.append(module)
    return result


def checked_command(runner, argv, timeout=3):
    result = runner.command(argv, timeout)
    require(result.returncode == 0 and not any((result.timed_out, result.interrupted, result.output_limited)),
            'Bounded command did not complete: ' + argv[0])
    return result.stdout.strip()


def prepare_egl_selection(manifest, *, root=Path('/'), owner=0, mountinfo=None):
    """Fixed process-local selector; never replace a driver or widen a sandbox.

    root/owner/mountinfo permit CPU fixtures. The live CLI has no overrides.
    This only localizes the readable selector; it is not a demonstrated fix.
    """
    require(manifest.get('profile') == 'ps4-baikal-diagnostics-uvd-alpha3' and
            manifest.get('rootfs_sha256') == ROOT_SHA and manifest.get('mesa_manifest_sha256') == MESA_SHA and
            manifest.get('manifest_validation', {}).get('embedded_root_contract_verified') is True,
            'EGL selection requires the verified frozen alpha3 boot')
    require(root.is_absolute() and root.resolve() == root, 'Unexpected fixture/live root')
    if mountinfo is None:
        with (root/'proc/self/mountinfo').open() as stream:
            mountinfo = stream.read(1024*1024+1)
    require(len(mountinfo) <= 1024*1024, 'Mount evidence exceeds its bound')
    etc_mounts = []
    for line in mountinfo.splitlines():
        left, separator, right = line.partition(' - ')
        fields, filesystem = left.split(), right.split()
        if separator and len(fields) >= 6 and fields[4] == '/etc':
            etc_mounts.append((fields, filesystem))
    require(len(etc_mounts) == 1 and len(etc_mounts[0][1]) >= 3 and etc_mounts[0][1][0] == 'tmpfs'
            and {'rw', 'nosuid', 'nodev'} <= set(etc_mounts[0][0][5].split(',')),
            'EGL selector must stay in the existing writable RAM /etc')
    mesa = root/'opt/psx-mesa'
    raw = bounded(mesa/'MESA-PSX-CANDIDATE.json', 1024*1024, owner=owner)
    require(sha(raw) == MESA_SHA, 'Candidate Mesa manifest differs')
    files = json.loads(raw)['files']
    library = root/EGL_LIBRARY.lstrip('/')
    for _ in range(8):
        require(library.is_relative_to(mesa), 'Candidate EGL path escaped')
        name = library.relative_to(mesa).as_posix(); pin = files.get(name)
        require(isinstance(pin, dict), 'Candidate EGL member is not listed')
        if not library.is_symlink():
            data = bounded(library, 64*1024**2, owner=owner)
            require(pin == {'bytes': len(data), 'sha256': sha(data)}, 'Candidate EGL library differs')
            break
        target = os.readlink(library)
        require(pin == {'symlink': target} and not Path(target).is_absolute() and '..' not in Path(target).parts,
                'Candidate EGL alias differs')
        library = library.parent/target
    else:
        raise ValueError('Candidate EGL alias chain exceeds its bound')
    path = root/EGL_SELECTION_PATH.relative_to('/')
    for parent in (root/'etc', path.parent):
        info = parent.lstat()
        require(parent.resolve() == parent and stat.S_ISDIR(info.st_mode) and info.st_uid == owner
                and not info.st_mode & 0o022, 'EGL selector parent is not a protected root directory')
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created = False
    try:
        try:
            fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444, dir_fd=directory)
            created = True
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o444)
                stream.write(EGL_SELECTION); stream.flush(); os.fsync(stream.fileno())
            os.fsync(directory)
        except FileExistsError:
            pass
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == owner and
                    stat.S_IMODE(info.st_mode) == 0o444 and info.st_size == len(EGL_SELECTION) and
                    stream.read(len(EGL_SELECTION)+1) == EGL_SELECTION,
                    'Existing EGL selector is not the exact protected file; it was not replaced')
    finally:
        os.close(directory)
    return {'path': str(EGL_SELECTION_PATH), 'sha256': sha(EGL_SELECTION), 'bytes': len(EGL_SELECTION),
            'created': created, 'mode': '0444', 'owner_uid': owner, 'scope': 'explicit browser process only',
            'library_path': EGL_LIBRARY, 'resolved_library': '/'+library.relative_to(root).as_posix(),
            'library_sha256': sha(data), 'mesa_manifest_sha256': MESA_SHA,
            'boot_rootfs_sha256': ROOT_SHA, 'etc_mount_filesystem': 'tmpfs',
            'hypothesis': 'Make the same candidate EGL selector readable within the existing RDD broker policy; untested, not a hardware fix.'}


def service_guard(runner):
    active = checked_command(runner, ['/usr/bin/systemctl', 'show', 'psx-diagnostics-graphics@diagnostics.service', '-p', 'ActiveState', '--value'])
    require(active == 'active', 'Existing X11 diagnostics session must be active')
    units = [UNIT] + ['psx-diagnostics-graphics@'+n+'.service' for n in ('browser', 'chromium', 'protondb', 'opengl', 'vulkan')]
    units += ['psx-diagnostics-window-'+n+'.service' for n in ('browser', 'chromium', 'protondb', 'opengl', 'vulkan')]
    states = checked_command(runner, ['/usr/bin/systemctl', 'show', *units, '-p', 'ActiveState', '--value']).split()
    require(len(states) == len(units) and all(s == 'inactive' for s in states),
            'A graphics/browser unit is active or failed; separate review is required')


def runtime_usage(path):
    value = os.statvfs(path)
    require(value.f_frsize > 0 and not value.f_flag & os.ST_RDONLY,
            'Invalid or read-only browser RAM filesystem accounting')
    return {'capacity_bytes':value.f_blocks*value.f_frsize,
            'available_bytes':value.f_bavail*value.f_frsize,
            'used_bytes':(value.f_blocks-value.f_bfree)*value.f_frsize}


def sample_runtime_space(runtime, budget, *, minimum=RUNTIME_LOW_WATER):
    require(runtime.resolve() == runtime and runtime.is_dir()
            and runtime.stat().st_dev == budget['filesystem_device'], 'Browser RAM runtime filesystem/path changed')
    value = runtime_usage(runtime)
    budget.update(post=value, sample_count=budget.get('sample_count',0)+1,
                  minimum_available_bytes=min(budget.get('minimum_available_bytes',value['available_bytes']),value['available_bytes']))
    require(value['capacity_bytes'] == RUNTIME_CAPACITY, 'Browser RAM filesystem capacity changed')
    if value['available_bytes'] < minimum:
        budget['space_stop'] = {'available_bytes':value['available_bytes'],'required_bytes':minimum}
        budget['low_space_stop'] = value['available_bytes'] < RUNTIME_LOW_WATER
        raise ValueError(f'Browser UVD RAM space stop: {value["available_bytes"]} bytes available; require {minimum}. No retry.')
    return value


def prepare_runtime_storage(manifest, run_id, budget, *, root=Path('/'), owner=0,
                            browser_uid=1000, browser_gid=1000, mountinfo=None):
    """Allocate only a fresh private run in the frozen boot's existing /var RAM.

    Fixture root/owner/mount evidence allow CPU tests; the live CLI has no path,
    budget or retest overrides. Existing profiles and directories are retained.
    """
    require(isinstance(manifest,dict) and all(manifest.get(key) == value for key,value in RUNTIME_BOOT.items())
            and isinstance(manifest.get('manifest_validation'),dict)
            and manifest['manifest_validation'].get('embedded_root_contract_verified') is True,
            'Browser RAM storage requires the exact verified frozen alpha3 boot')
    require(root.is_absolute() and root.resolve() == root
            and re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}',run_id), 'Invalid runtime root/run identity')
    if mountinfo is None:
        mountinfo = bounded(root/'proc'/str(os.getpid())/'mountinfo',1024**2,owner=owner).decode()
    require(len(mountinfo) <= 1024**2, 'Mount evidence exceeds its bound')
    mounts = []
    for line in mountinfo.splitlines():
        left,separator,right = line.partition(' - ')
        fields,filesystem = left.split(),right.split()
        require(separator and len(fields) >= 6 and len(filesystem) >= 3, 'Malformed browser mount evidence')
        target = Path(re.sub(r'\\([0-7]{3})',lambda match:chr(int(match[1],8)),fields[4]))
        mounts.append((target,fields,filesystem))
    var_mounts = [(fields,filesystem) for target,fields,filesystem in mounts if target == Path('/var')]
    require(len(var_mounts) == 1 and var_mounts[0][1][0] == 'tmpfs'
            and var_mounts[0][0][3] in ('/var','/')
            and {'rw','nosuid','nodev'} <= set(var_mounts[0][0][5].split(','))
            and 'rw' in var_mounts[0][1][2].split(','),
            'Browser runtime requires the existing writable protected tmpfs /var')
    require(not any(target != Path('/var') and target.is_relative_to('/var')
                    and (RUNTIME_PARENT.is_relative_to(target) or target.is_relative_to(RUNTIME_PARENT))
                    for target,_,_ in mounts), 'Nested mount changes browser RAM ancestry')
    var = root/'var'
    for parent in (root,var):
        info = parent.lstat()
        require(parent.resolve() == parent and stat.S_ISDIR(info.st_mode) and info.st_uid == owner
                and not info.st_mode & 0o022, 'Browser RAM ancestor is not a protected root directory')
    runtime = root/RUNTIME_PARENT.relative_to('/')/run_id
    value = runtime_usage(var)
    budget.update(directory=str(runtime),mountpoint='/var',filesystem='tmpfs',
                  filesystem_device=var.stat().st_dev,capacity_bytes=RUNTIME_CAPACITY,
                  required_free_before_intent_bytes=RUNTIME_MIN_FREE,low_water_free_bytes=RUNTIME_LOW_WATER,
                  pre=value,post=value,minimum_available_bytes=value['available_bytes'],sample_count=1,
                  low_space_stop=False,scope='Existing shared writable RAM pool; no hard space preallocation; prior profiles retained')
    require(value['capacity_bytes'] == RUNTIME_CAPACITY, 'Browser /var must be the existing 512 MiB RAM pool')
    require(value['available_bytes'] >= RUNTIME_MIN_FREE,
            f'Browser RAM preflight needs {RUNTIME_MIN_FREE} bytes free before UVD intent; available {value["available_bytes"]}')
    directory = os.open(var,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for name in RUNTIME_PARENT.relative_to('/var').parts:
            try:os.mkdir(name,0o755,dir_fd=directory)
            except FileExistsError:pass
            child = os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            info = os.fstat(child)
            try:
                require(stat.S_ISDIR(info.st_mode) and info.st_uid == owner and not info.st_mode & 0o022
                        and info.st_mode & 0o001 and info.st_dev == budget['filesystem_device'],
                        'Browser runtime parent is not a protected traversable root directory')
            except BaseException:
                os.close(child);raise
            os.close(directory);directory = child
        os.mkdir(run_id,0o700,dir_fd=directory)
    finally:
        os.close(directory)
    children = ('profile','logs','home','cache','config','data','tmp','mesa')
    for name in children:(runtime/name).mkdir(mode=0o700)
    prefs = runtime/'profile/user.js'
    with prefs.open('xb') as stream:
        os.fchmod(stream.fileno(),0o600)
        stream.write(preferences());stream.flush();os.fsync(stream.fileno())
    os.chown(prefs,browser_uid,browser_gid,follow_symlinks=False)
    for name in children:os.chown(runtime/name,browser_uid,browser_gid,follow_symlinks=False)
    os.chown(runtime,browser_uid,browser_gid,follow_symlinks=False)
    budget['pre_intent'] = sample_runtime_space(runtime,budget,minimum=RUNTIME_MIN_FREE)
    return runtime


def proc_snapshot(root=Path('/proc')):
    result = {}
    for path in root.iterdir():
        if not path.name.isdecimal():
            continue
        require(len(result) < 4096, 'Process inventory exceeds bound')
        try:
            status = (path/'status').read_text()[:16384]
            ppid = int(re.search(r'^PPid:\s*(\d+)', status, re.M)[1])
            command = (path/'cmdline').read_bytes()[:8192].split(b'\0')
            start = (path/'stat').read_text().rsplit(')', 1)[1].split()[19]
            uids = re.search(r'^Uid:\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', status, re.M)
            attributes = {}
            for field, key in (('Seccomp', 'seccomp'), ('NoNewPrivs', 'no_new_privs'), ('Seccomp_filters', 'seccomp_filters')):
                match = re.search(r'^'+field+r':\s*(\d+)', status, re.M)
                attributes[key] = int(match[1]) if match else None
            result[int(path.name)] = {'ppid': ppid, 'start': start, 'command': command,
                                      'rdd': b'rdd' in command or bool(re.search(r'^Name:\s*RDD Process$', status, re.M)),
                                      'uids': list(map(int, uids.groups())) if uids else None, **attributes}
        except (OSError, ValueError, TypeError, IndexError):
            continue
    return result


def observe_processes(main_pid, seen, driver, root=Path('/proc')):
    all_pids = proc_snapshot(root)
    family = {main_pid}
    for _ in range(16):
        new = family | {pid for pid, p in all_pids.items() if p['ppid'] in family}
        if new == family:
            break
        family = new
    info = driver.stat()
    for pid in family & all_pids.keys():
        p = all_pids[pid]
        key = str(pid)+':'+p['start']
        record = seen.setdefault(key, {'pid': pid, 'start': p['start'], 'rdd': p['rdd'], 'tids': []})
        record['rdd'] |= p['rdd']
        record.update({k: p[k] for k in ('uids', 'seccomp', 'no_new_privs', 'seccomp_filters')})
        if not record['rdd']:
            continue
        try:
            maps = (root/str(pid)/'maps').read_text()
            require(len(maps) <= 512*1024, 'Decoder maps exceed bound')
            record['tids'] = sorted(set(record['tids']) | {int(t.name) for t in (root/str(pid)/'task').iterdir() if t.name.isdecimal()})
            for line in maps.splitlines():
                parts = line.split(maxsplit=5)
                if len(parts) != 6:
                    continue
                path = parts[5]
                if path == str(driver):
                    record.update(driver_sha256=DRIVER_SHA,
                        driver_inode_matches=(int(parts[4]) == info.st_ino and parts[3].lower() == f'{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}'), maps=maps)
                if path.startswith('/usr/lib/') and re.search(r'/libva\.so\.', path): record['libva'] = path
                if path.startswith('/usr/lib/') and re.search(r'/libavcodec\.so\.', path): record['libavcodec'] = path
        except (OSError, ValueError):
            continue
    require(len(seen) <= 128, 'Browser process budget exceeded')


def launch_command(runtime, url, *, file_limit_bytes=DEFAULT_FILE_LIMIT):
    require(type(file_limit_bytes) is int and DEFAULT_FILE_LIMIT <= file_limit_bytes <= MAX_FILE_LIMIT
            and file_limit_bytes & (file_limit_bytes-1) == 0, 'Unreviewed browser per-file limit')
    logs = runtime/'logs'
    private_environment = ['HOME='+str(runtime/'home'),'XDG_CACHE_HOME='+str(runtime/'cache'),
                           'XDG_CONFIG_HOME='+str(runtime/'config'),'XDG_DATA_HOME='+str(runtime/'data'),
                           'TMPDIR='+str(runtime/'tmp')]
    environment = [*private_environment, 'USER=psx', 'LOGNAME=psx', 'DISPLAY=:0',
                   'XAUTHORITY=/run/psx-graphics-user/Xauthority', 'XDG_RUNTIME_DIR=/run/user/1000']
    argv = ['/usr/bin/systemd-run', '--quiet', '--unit='+UNIT, '--service-type=exec',
            '--property=User=psx', '--property=Group=psx', '--property=RuntimeMaxSec=60',
            '--property=KillMode=control-group', '--property=TimeoutStopSec=8', '--property=LimitCORE=0',
            '--property=LimitFSIZE='+str(file_limit_bytes//1024**2)+'M', '--property=TasksMax=512', '--property=MemoryMax=1G',
            '--property=StandardOutput=append:'+str(logs/'console.log'),
            '--property=StandardError=append:'+str(logs/'console.log'),
            *('--setenv='+e for e in environment), '--', '/usr/bin/python3', '/opt/psx-mesa-tools/with-mesa.py',
            '--root', '/opt/psx-mesa', '--runtime-dir', str(runtime/'mesa'),
            '--expected-manifest-sha256', MESA_SHA, '--', '/usr/bin/env',
            *private_environment,
            'MOZ_X11_EGL=1', 'MOZ_LOG=timestamp,sync,FFmpegVideo:5,PlatformDecoderModule:5,Dmabuf:5,DmabufRef:5',
            'MOZ_LOG_FILE='+str(logs/'mozilla.log'), 'LIBVA_TRACE='+str(logs/'va'),
            'LIBVA_DRIVER_NAME=radeonsi', 'LIBVA_DRIVERS_PATH=/opt/psx-mesa/usr/lib/dri',
            '__EGL_VENDOR_LIBRARY_FILENAMES='+str(EGL_SELECTION_PATH),
            '/usr/bin/dbus-run-session', '/usr/lib/firefox/firefox', '--no-remote',
            '--profile', str(runtime/'profile'), '--kiosk', url]
    return argv


def logs_read(directory):
    values = {}; total = 0
    for path in sorted(directory.iterdir()):
        require(len(values) < 64, 'Too many browser logs')
        require(not path.is_symlink() and path.is_file() and path.stat().st_uid in (0, 1000), 'Unexpected browser log')
        total += path.stat().st_size
        require(total <= MAX_TRACE, 'Browser trace budget exceeded')
        values[path.name] = bounded(path, MAX_TRACE)
    return values


def copy_logs(runtime, directory, partial=False):
    if partial:
        logs = {}
        remaining = min(MAX_TRACE, MAX_LOG - sum(p.stat().st_size for p in directory.iterdir() if p.is_file()) - 512*1024)
        for path in sorted((runtime/'logs').iterdir())[:64]:
            if remaining <= 0:
                break
            require(not path.is_symlink() and path.is_file() and path.stat().st_uid in (0, 1000), 'Unexpected browser log')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                data = stream.read(remaining)
            logs[path.name] = data
            remaining -= len(data)
    else:
        logs = logs_read(runtime/'logs')
    for name, value in logs.items():
        require(re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name), 'Unsafe trace filename')
        path = directory/('browser-'+name)
        if not path.exists():
            with path.open('xb') as stream:
                stream.write(value); stream.flush(); os.fsync(stream.fileno())
    return logs


def durable_result(diagnostics, directory, record, classification):
    # Evidence and directories must be durable before a visible PASS is created.
    for path in directory.iterdir():
        if path.is_file():
            with path.open('rb') as stream: os.fsync(stream.fileno())
    diagnostics.fsync_directory(directory)
    record.update(classification, phase='completed', completed_at=diagnostics.timestamp(),
                  stop_required=True, hardware_acceptance=False)
    diagnostics.atomic_json(directory/'result.json', record)
    return record


def run(args, data):
    require(os.geteuid() == 0 and args.results_dir.absolute() == RESULTS and RESULTS.resolve() == RESULTS,
            'Run requires root and the fixed diagnostic USB results directory')
    diagnostics, uvd = runtime_modules()
    runner = diagnostics.Runner(RESULTS)
    with runner.lock():
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:12]
        directory = RESULTS/run_id; directory.mkdir(mode=0o700)
        diagnostics.fsync_directory(RESULTS)
        runner.log = diagnostics.Evidence(directory)
        record = {'schema_version': 1, 'test_id': TEST_ID, 'test_version': TEST_VERSION, 'run_id': run_id,
                  'started_at': diagnostics.timestamp(), 'phase': 'preflight', 'status': 'INCONCLUSIVE',
                  'identity': runner.identity(),
                  'hardware_acceptance': False, 'stop_required': False, 'result_directory': str(directory),
                  'harness_sha256': sha(Path(__file__).read_bytes()),
                  'runtime_source_sha256': {'runner': sha(Path(diagnostics.__file__).read_bytes()),
                                           'uvd': sha(Path(uvd.__file__).read_bytes())}}
        diagnostics.atomic_json(directory/'result.json', record)
        http = thread = runtime = None
        reserved = launched = False
        seen = {}
        try:
            require(uvd.stop_reason(runner) is None, 'UVD already reserved; no retry')
            record['media_cache_limits'] = media_cache_limits(len(data['clip']))
            identity, session, paths, _, _ = uvd.preflight(runner)
            manifest = uvd.document(uvd.local(runner, runner.manifest_path), runner)
            require(manifest['profile'] == uvd.ALPHA3['profile'] and manifest['rootfs_sha256'] == ROOT_SHA,
                    'Only the exact corrected alpha3 root is supported')
            history = []; batches = []
            for i, path in enumerate(RESULTS.glob('*/result.json')):
                require(i < 4096, 'Result history bound exceeded')
                value = json.loads(bounded(path, 1024**2, owner=0))
                require(value.get('run_id') == path.parent.name, 'Saved report path/identity differs')
                history.append(value)
            for i, path in enumerate(RESULTS.glob('*/batch.json')):
                require(i < 4096, 'Batch history bound exceeded')
                value = json.loads(bounded(path, 1024**2, owner=0))
                require(value.get('run_id') == path.parent.name, 'Saved batch path/identity differs')
                batches.append(value)
            # The controller's RAM fallback can carry a stop after a visible USB
            # PASS suffered a final fsync failure. It must never be overridden.
            for i, path in enumerate(Path('/run/psx-diagnostics/browser-batches').glob('*.json')):
                require(i < 4096, 'RAM browser-batch history bound exceeded')
                value = json.loads(bounded(path, 1024**2, owner=0))
                require(value.get('run_id') == path.stem and value.get('test_id') == 'browser.batch',
                        'RAM browser-batch identity differs')
                history.append(value)
            history_ready(history, batches, identity['boot_id'], run_id)
            core_ready(history, identity, runner)
            service_guard(runner)
            require(not any(any(b'firefox' in c or b'chromium' in c for c in p['command'][:1]) for p in proc_snapshot().values()),
                    'An existing browser process is present')
            app = configparser.ConfigParser(); app.read_string(bounded(Path('/usr/lib/firefox/application.ini'), 65536, owner=0).decode())
            require(app.get('App', 'Version') == '154.0' and pwd.getpwnam('psx').pw_uid == 1000, 'Expected Firefox 154 and psx UID 1000')
            cookie = Path('/run/psx-graphics-user/Xauthority').lstat()
            require(stat.S_ISREG(cookie.st_mode) and cookie.st_uid == 1000 and not cookie.st_mode & 0o077, 'Private existing X11 cookie absent')
            uvd.kernel_snapshot(runner)
            before = (directory/runner.commands[-1]['stdout']).read_text()
            record['egl_selection'] = prepare_egl_selection(manifest)
            record.update(identity=identity, uart=session)
            record['runtime_space'] = {}
            runtime = prepare_runtime_storage(manifest,run_id,record['runtime_space'])
            record['runtime_directory'] = str(runtime)
            diagnostics.atomic_json(directory/'result.json',record)
            record['runtime_space']['pre_intent'] = sample_runtime_space(runtime,record['runtime_space'],minimum=RUNTIME_MIN_FREE)
            # Conservatively mark any failure during reservation as final-active.
            reserved = True
            marker, _ = uvd.create_marker(runner, identity, session)
            record.update(stop_required=True, phase='running', attempt_receipt=str(marker))
            diagnostics.atomic_json(directory/'result.json', record)
            http, thread = server(data)
            record['inputs'] = {k: sha(v) for k, v in data.items()}
            record['fixture'] = fixture_spec(json.loads(data['fixture']))
            record['preferences'] = PREFS
            record['passive_fences'] = {'available': False, 'reason': 'No source-reviewed passive fence interface is available; no debugfs probes or mounts performed.'}
            started = time.monotonic(); launched = True
            checked_command(runner, launch_command(runtime, http.protocol.origin+'/',
                            file_limit_bytes=record['media_cache_limits']['file_limit_bytes']), 5)
            main_pid = int(checked_command(runner, ['/usr/bin/systemctl', 'show', UNIT, '-p', 'MainPID', '--value']))
            require(main_pid > 0, 'Firefox service did not retain a main process')
            complete = True
            while http.protocol.result is None and time.monotonic()-started < TIMEOUT:
                sample_runtime_space(runtime,record['runtime_space'])
                observe_processes(main_pid, seen, paths['driver'])
                logs_read(runtime/'logs')
                time.sleep(.1)
            if http.protocol.result is None: complete = False
            sample_runtime_space(runtime,record['runtime_space'])
            observe_processes(main_pid, seen, paths['driver'])
            checked_command(runner, ['/usr/bin/systemctl', 'stop', UNIT], 10)
            launched = False
            sample_runtime_space(runtime,record['runtime_space'])
            service = checked_command(runner, ['/usr/bin/systemctl', 'show', UNIT,
                '-p', 'Result', '-p', 'ExecMainCode', '-p', 'ExecMainStatus'])
            record['service_completion'] = dict(line.split('=', 1) for line in service.splitlines() if '=' in line)
            state = record['service_completion']
            require(state.get('Result') == 'success' and
                    (state.get('ExecMainStatus') == '0' or
                     state.get('ExecMainCode') == '2' and state.get('ExecMainStatus') == '15'),
                    'Browser service failed, crashed or exceeded its stop deadline')
            uvd.kernel_snapshot(runner)
            after = (directory/runner.commands[-1]['stdout']).read_text()
            require(uvd.sha(uvd.read(paths['driver'], 64*1024**2)) == DRIVER_SHA, 'Mapped driver changed')
            logs = copy_logs(runtime, directory)
            processes = list(seen.values())
            diagnostics.atomic_json(directory/'processes.json', processes)
            diagnostics.atomic_json(directory/'page.json', http.protocol.result)
            mozilla = '\n'.join(v.decode(errors='replace') for k, v in logs.items() if not k.startswith('va.'))
            traces = {k: v.decode(errors='replace') for k, v in logs.items() if k.startswith('va.')}
            total = sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
            verdict = classify(http.protocol.result, mozilla, traces, processes, before, after, uvd.KERNEL_FAULT,
                               complete=complete and total < MAX_LOG,
                               expected_fixture=json.loads(data['fixture']))
            record['commands'] = runner.commands
            return durable_result(diagnostics, directory, record, verdict)
        except BaseException as error:
            collection_errors = []
            if launched:
                try:
                    checked_command(runner, ['/usr/bin/systemctl', 'stop', UNIT], 10)
                    launched = False
                except Exception as failure:
                    collection_errors.append(str(failure))
            if reserved:
                try: uvd.kernel_snapshot(runner)
                except Exception as failure: collection_errors.append(str(failure))
            if runtime:
                record['private_runtime_evidence'] = str(runtime)
                record['failure_logs_may_be_truncated'] = True
                try:
                    post = runtime_usage(runtime)
                    record['runtime_space']['post'] = post
                    record['runtime_space']['minimum_available_bytes'] = min(
                        record['runtime_space'].get('minimum_available_bytes',post['available_bytes']),post['available_bytes'])
                except Exception as failure:collection_errors.append(str(failure))
                try: copy_logs(runtime, directory, partial=True)
                except Exception as failure: collection_errors.append(str(failure))
            if seen:
                diagnostics.atomic_json(directory/'processes.json', list(seen.values()))
            if http and http.protocol.result is not None:
                diagnostics.atomic_json(directory/'page.json', http.protocol.result)
            record.update(status='INCONCLUSIVE' if reserved else 'BLOCKED', message=str(error)[:512],
                          stop_required=reserved, phase='completed', completed_at=diagnostics.timestamp(),
                          collection_errors=collection_errors, commands=runner.commands)
            diagnostics.atomic_json(directory/'result.json', record)
            return record
        finally:
            if launched:
                try: checked_command(runner, ['/usr/bin/systemctl', 'stop', UNIT], 10)
                except Exception: pass
            if http:
                http.shutdown(); http.server_close(); thread.join(timeout=2)
            # Never delete/rewrite the immutable UVD intent, including after PASS.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('clip', 'page', 'fixture'):
        parser.add_argument('--'+name, type=Path, required=True)
        parser.add_argument('--'+name+'-sha256', required=True)
    parser.add_argument('--results-dir', type=Path, default=RESULTS)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    data = inputs(args)
    if not args.run:
        result = {'test_id': TEST_ID, 'test_version': TEST_VERSION, 'run': False, 'hardware_acceptance': False,
                  'inputs': {k: sha(v) for k, v in data.items()}, 'timeout_seconds': TIMEOUT,
                  'media_cache_limits':media_cache_limits(len(data['clip'])),
                  'fixture': fixture_spec(json.loads(data['fixture'])),
                  'planned_runtime_storage':{'directory':str(RUNTIME_PARENT),'filesystem':'tmpfs',
                      'capacity_bytes':RUNTIME_CAPACITY,'required_free_before_intent_bytes':RUNTIME_MIN_FREE,
                      'low_water_free_bytes':RUNTIME_LOW_WATER,'prior_profiles_deleted':False},
                  'planned_egl_selection': {'path': str(EGL_SELECTION_PATH), 'sha256': sha(EGL_SELECTION),
                      'scope': 'explicit browser process only', 'hypothesis': 'Readable candidate selection; not an observed hardware fix'},
                  'requires': ['current-boot CPU/RAM PASS', 'exact alpha3 contract', 'active private UART assertion',
                               'unused immutable UVD attempt', 'existing X11 diagnostics', 'positive decoder evidence']}
    else:
        result = run(args, data)
    print(json.dumps(result, indent=2))
    return 0 if result.get('status') == 'PASS' or not args.run else 1


if __name__ == '__main__':
    raise SystemExit(main())
