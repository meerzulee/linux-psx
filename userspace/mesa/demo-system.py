#!/usr/bin/env python3
"""Finite installed-Mesa playback observation; default is a no-submission preflight.

Imports the frozen demo's player/progress/IPC gates without changing that demo.
Explicit --run needs a separately supervised bounded UART experiment. No driver
installation, resets, clock changes, retries, or hardware-busy percentage claim.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import signal
import stat
import subprocess
import sys
import time

KERNEL_FAULT = re.compile(
    r'PS4_UVD_RUNTIME\s+(?:CLOSED|RETAINED)|GPU fault detected|\bVM fault\b|'
    r'GPU reset|ring\s+[^\n]*timeout|ring\s+[^\n]*test failed|IH[^\n]*overflow|'
    r'amdgpu_job_timedout|Kernel panic|Oops:', re.I)
MESA_NAMES = ('libEGL_mesa', 'libGLX_mesa', 'libgallium-', 'libgbm',
              'dri_gbm', 'libdril', 'radeonsi_drv_video', 'radeonsi_dri')
OVERRIDES = ('LD_', 'MESA_', 'RADEON_', 'R600_', 'RADV_', 'AMD_', 'LIBGL_',
             'LIBVA_', 'GALLIUM_', 'EGL_', '__EGL_', '__GLX_', '__GL_',
             'GBM_', 'VK_', 'ACO_', 'NIR_', 'GLSL_', 'SPIRV_')


class ObservationIncomplete(RuntimeError):
    pass


class WorkloadFailed(RuntimeError):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def verify_installed(manifest, root=Path('/')):
    files = manifest['files']
    for relative, pin in files.items():
        parts = Path(relative).parts
        if not parts or parts[0] != 'usr' or '..' in parts:
            raise ValueError('Invalid installed manifest path: ' + relative)
        path = root / relative
        if 'symlink' in pin:
            if not path.is_symlink() or os.readlink(path) != pin['symlink']:
                raise ValueError('Installed symlink mismatch: ' + relative)
        elif (not path.is_file() or path.is_symlink()
              or path.stat().st_size != pin['bytes'] or digest(path) != pin['sha256']):
            raise ValueError('Installed file mismatch: ' + relative)
    return len(files)


def parse_cpu(text):
    # /proc/PID/stat comm can contain spaces and parentheses.
    fields = text[text.rindex(')') + 2:].split()
    return {'ticks': int(fields[11]) + int(fields[12]),
            'start_ticks': int(fields[19]), 'threads': int(fields[17])}


def parse_drm(texts):
    """Deduplicate shared DRM descriptors; times are scheduler accounting only."""
    clients = {}
    for text in texts:
        fields = dict(line.split(':', 1) for line in text.splitlines() if ':' in line)
        if fields.get('drm-driver', '').strip() != 'amdgpu' or 'drm-client-id' not in fields:
            continue
        key = fields.get('drm-pdev', '').strip() + '/' + fields['drm-client-id'].strip()
        engines = clients.setdefault(key, {})
        for name, value in fields.items():
            match = re.fullmatch(r'\s*(\d+) ns\s*', value)
            if name.startswith('drm-engine-') and match:
                engines[name.removeprefix('drm-engine-')] = max(
                    engines.get(name.removeprefix('drm-engine-'), 0), int(match[1]))
    return clients


def process_sample(pid):
    root = Path('/proc') / str(pid)
    cpu = parse_cpu((root / 'stat').read_text())
    texts = []
    for path in (root / 'fdinfo').iterdir():
        try:
            texts.append(path.read_text())
        except OSError:
            pass  # Optional accounting can disappear as an FD closes.
    return {'monotonic': time.monotonic(), 'cpu': cpu,
            'drm_clients_scheduler_ns': parse_drm(texts)}


def cpu_rate(first, last, ticks_per_second):
    if first['cpu']['start_ticks'] != last['cpu']['start_ticks']:
        raise ValueError('PID identity changed')
    elapsed = last['monotonic'] - first['monotonic']
    ticks = last['cpu']['ticks'] - first['cpu']['ticks']
    if elapsed <= 0 or ticks < 0:
        raise ValueError('Invalid CPU accounting interval')
    seconds = ticks / ticks_per_second
    return {'cpu_seconds': seconds, 'wall_seconds': elapsed,
            'percent_of_one_cpu_core': 100 * seconds / elapsed}


def kernel_log(cursor=None):
    argv = ['/usr/bin/sudo', '-n', '/usr/bin/journalctl', '-k', '-b',
            '--no-pager', '-o', 'short-monotonic', '--show-cursor']
    if cursor:
        argv += ['--after-cursor', cursor]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=3, check=True)
    if len(result.stdout.encode()) > 1024 * 1024:
        raise RuntimeError('Kernel observer output exceeded 1 MiB; no retry')
    matches = re.findall(r'^-- cursor: (.+)$', result.stdout, re.M)
    next_cursor = matches[-1] if matches else cursor
    if not next_cursor:
        raise RuntimeError('Kernel observer has no durable cursor')
    return result.stdout, next_cursor


def validate_maps(text, manifest, root=Path('/')):
    found = {}
    for line in text.splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6 or not any(name in Path(fields[5]).name for name in MESA_NAMES):
            continue
        if fields[5].endswith(' (deleted)'):
            raise RuntimeError('Deleted Mesa mapping: ' + fields[5])
        path = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[5]))
        if not path.is_absolute() or not path.is_relative_to(root / 'usr'):
            raise RuntimeError('Non-system Mesa mapping: ' + str(path))
        relative = str(path.resolve(strict=True).relative_to(root))
        pin = manifest['files'].get(relative, {})
        sha = digest(path)
        if pin.get('sha256') != sha:
            raise RuntimeError('Unpinned mapped Mesa library: ' + str(path))
        found[str(path)] = sha
    for required in ('libEGL_mesa', 'libgallium-'):
        if not any(required in path for path in found):
            raise RuntimeError('Required installed mapping absent: ' + required)
    return found


def mapped_installed(pid, manifest, output):
    text = Path(f'/proc/{pid}/maps').read_text()
    (output / 'mpv.maps').write_text(text)
    found = validate_maps(text, manifest)
    save(output / 'LIBRARIES.json', found)


def observe(args, demo, manifest, env):
    output = args.output
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    command = demo.playback_args(args.clip, output)
    save(output / 'PREFLIGHT.json', {
        'argv': command, 'environment': env, 'boot_id': args.boot_id,
        'release': args.release, 'experiment': args.experiment,
        'caller_asserted_uart_session': args.uart_session,
        'installed_manifest_sha256': args.manifest_sha256,
        'clip_sha256': args.clip_sha256, 'demo_sha256': args.demo_sha256,
        'controller_sha256': digest(__file__), 'passes': args.passes,
        'process_timeout_seconds': args.timeout,
        'scope': 'paced vaapi-copy plus GL presentation; not isolated decoder throughput',
        'physical_hardware_busy_percent_available': False})
    stop = {'reason': None}
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda number, frame: stop.update(reason='operator signal ' + str(number)))
    child = ipc = None
    before = after = first = last = progress = initial = None
    reason = None
    cursor = None
    terminal_seen = postflight_failed = False
    failure_kind = None
    deadline = time.monotonic() + args.timeout
    try:
        kernel, cursor = kernel_log()
        (output / 'kernel-before.txt').write_text(kernel)
        if KERNEL_FAULT.search(kernel):
            terminal_seen = True
            raise RuntimeError('Terminal marker in current-boot kernel log; no launch')
        if not all('PS4_UVD_RUNTIME ' + marker in kernel for marker in ('STARTED', 'QUEUE_READY')):
            raise RuntimeError('Missing positive UVD startup markers')
        before, raw = demo.read_fence()
        (output / 'fence-before.txt').write_text(raw)
        if before['completed'] != before['emitted']:
            raise RuntimeError('UVD has outstanding work before launch')
        kernel, cursor = kernel_log(cursor)
        if KERNEL_FAULT.search(kernel):
            terminal_seen = True
            raise RuntimeError('Kernel fault during initial fence observation; no launch')
        with (output / 'stdout.txt').open('xb') as stdout, (output / 'stderr.txt').open('xb') as stderr:
            child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                     stdout=stdout, stderr=stderr, start_new_session=True)
        save(output / 'PROCESS.json', {'supervisor_pid': os.getpid(), 'mpv_pid': child.pid})
        init_deadline = min(deadline, time.monotonic() + 20)
        while True:
            if stop['reason'] or time.monotonic() >= init_deadline:
                raise ObservationIncomplete('Initialization stopped or exceeded 20 seconds')
            if child.poll() is not None:
                raise WorkloadFailed('Player exited during initialization')
            kernel, cursor = kernel_log(cursor)
            with (output / 'kernel-during.txt').open('a') as stream:
                stream.write(kernel)
            if KERNEL_FAULT.search(kernel):
                terminal_seen = True
                raise RuntimeError('Kernel fault during initialization')
            logs = read_logs(output)
            if demo.TERMINAL.search('\n'.join(logs.values())):
                raise WorkloadFailed('Player initialization error; see retained logs')
            if ipc is None and (output / 'ipc.sock').exists():
                try:
                    ipc = demo.IPC(output / 'ipc.sock')
                except OSError as exc:
                    if exc.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                        raise
            if ipc:
                initial = ipc.snapshot()
                needed = ('hwdec-current', 'video-params', 'current-vo', 'duration',
                          'time-pos', 'video-codec', 'frame-drop-count', 'decoder-frame-drop-count')
                if (demo.single_hwdec_thread(logs.get('mpv.log', ''))
                        and initial.get('vo-configured') is True
                        and all(initial.get(k) is not None for k in needed)):
                    demo.gate(initial, initial=True)
                    if (initial['video-params']['w'], initial['video-params']['h']) != (1920, 1080):
                        raise RuntimeError('Expected the known native 1080p clip')
                    break
            time.sleep(.2)
        mapped_installed(child.pid, manifest, output)
        save(output / 'INITIAL.json', initial)
        paused, raw = demo.read_fence()
        (output / 'fence-paused.txt').write_text(raw)
        ipc.call('show-text', 'PS4 UVD | installed Mesa | paused identity verification', 1500, 0)
        time.sleep(.3)
        demo.gate(ipc.snapshot(), initial=True)
        ipc.call('set_property', 'pause', False)
        first = process_sample(child.pid)
        progress = demo.Progress(initial['duration'], initial['time-pos'], paused, first['monotonic'])
        with (output / 'telemetry.jsonl').open('x', buffering=1) as telemetry:
            while True:
                if stop['reason']:
                    reason = stop['reason']
                    break
                if time.monotonic() >= deadline:
                    reason = 'process deadline reached; observation incomplete'
                    break
                if child.poll() is not None:
                    raise WorkloadFailed('Player exited before observation completed')
                kernel, cursor = kernel_log(cursor)
                with (output / 'kernel-during.txt').open('a') as stream:
                    stream.write(kernel)
                if KERNEL_FAULT.search(kernel):
                    terminal_seen = True
                    raise RuntimeError('Kernel fault; see retained journal and UART')
                if demo.TERMINAL.search('\n'.join(read_logs(output).values())):
                    raise WorkloadFailed('Player error; see retained logs')
                player = ipc.snapshot()
                fence, raw = demo.read_fence()
                last = process_sample(child.pid)
                try:
                    metrics = progress.update(player, fence, last['monotonic'])
                except RuntimeError as exc:
                    raise WorkloadFailed(str(exc)) from exc
                row = {'player': player, 'uvd_fences': fence, **last, **metrics}
                telemetry.write(json.dumps(row, sort_keys=True) + '\n')
                ipc.call('show-text',
                    f'PS4 UVD | vaapi-copy | installed Mesa | 1920x1080 @ 30 fps\n'
                    f'Completed passes {progress.wraps}/{args.passes} | '
                    f'drops display {player["frame-drop-count"]} / decoder {player["decoder-frame-drop-count"]}\n'
                    f'UVD completions {fence["completed"] - before["completed"]} | '
                    f'CPU {cpu_rate(first, last, os.sysconf("SC_CLK_TCK"))["percent_of_one_cpu_core"]:.1f}% of one core',
                    1500, 0)
                if progress.wraps >= args.passes:
                    break  # Stop even if drops occurred; never loop waiting for "clean".
                time.sleep(min(1, max(0, deadline - time.monotonic())))
    except Exception as exc:
        reason = str(exc)
        failure_kind = ('INCOMPLETE' if isinstance(exc, ObservationIncomplete) else
                        'WORKLOAD_FAILED' if isinstance(exc, WorkloadFailed) else 'OBSERVATION_FAILED')
        save(output / 'STOP-CAUSE.json', {'reason': reason, 'kind': failure_kind, 'no_retry': True})
    finally:
        if child is not None and child.poll() is None:
            try:
                if ipc:
                    ipc.call('quit')
                else:
                    child.terminate()
            except Exception:
                child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                reason = (reason or '') + '; player did not exit; PID retained, no SIGKILL/reset'
        if ipc:
            try:
                ipc.close()
            except OSError:
                pass
    try:
        after, raw = demo.read_fence()
        (output / 'fence-after.txt').write_text(raw)
        if after['completed'] != after['emitted']:
            postflight_failed = True
            reason = (reason or '') + '; UVD work still outstanding after stop'
        verify_installed(manifest)
        check_identity(args)
    except Exception as exc:
        postflight_failed = True
        reason = (reason or '') + '; postflight: ' + str(exc)
    terminal_kernel = terminal_seen
    try:
        # Read after the fence observer: that observer itself processes writeback.
        kernel, _ = kernel_log(cursor)
        (output / 'kernel-after.txt').write_text(kernel)
        terminal_kernel |= bool(KERNEL_FAULT.search(kernel))
        if terminal_kernel:
            reason = (reason or '') + '; terminal current-boot kernel marker'
    except Exception as exc:
        postflight_failed = True
        reason = (reason or '') + '; final kernel observer: ' + str(exc)
    passes = progress.wraps if progress else 0
    speed = ((passes * initial['duration'] + progress.position - initial['time-pos']) /
             (last['monotonic'] - first['monotonic'])) if first and last else None
    clean = progress.clean if progress else False
    success = (reason is None and child is not None and child.poll() == 0
               and passes >= args.passes and clean and speed is not None and .97 <= speed <= 1.03)
    if success:
        status = 'PASS_PLAYBACK_OBSERVATION'
    elif terminal_kernel:
        status = 'TERMINAL_FAULT'
    elif postflight_failed:
        status = 'POSTFLIGHT_FAILED'
    elif failure_kind == 'WORKLOAD_FAILED':
        status = 'FAILED_STOP'
    elif failure_kind:
        status = failure_kind
    elif passes < args.passes or (child is not None and child.poll() is None):
        status = 'INCOMPLETE'
    elif not clean:
        status = 'PLAYBACK_DROPS'
    elif speed is not None and not .97 <= speed <= 1.03:
        status = 'PACING_OUT_OF_BAND'
    else:
        status = 'NOT_PASSED'
    result = {'status': status,
              'reason': reason, 'completed_passes': passes, 'zero_drop_samples': clean,
              'pts_wall_speed': speed, 'mpv_exit': child.poll() if child else None,
              'uvd_before': before, 'uvd_after': after,
              'cpu': cpu_rate(first, last, os.sysconf('SC_CLK_TCK')) if first and last else None,
              'initial_process': first, 'last_process': last,
              'physical_hardware_busy_percent': None,
              'pixel_oracle_in_this_observation': False, 'hardware_acceptance': False,
              'terminal_kernel_marker': terminal_kernel,
              'scope': 'finite paced copy-back decode plus GL/compositor/OSD; final UART/operator review required',
              'loop_boundary_note': 'wrap count includes a possible partial following pass before 1 Hz stop',
              'driver_reset': False, 'no_retry': True}
    save(output / 'RESULT.json', result)
    print(json.dumps(result, indent=2))
    return 0 if success else 1


def read_logs(output):
    found = {}
    for name in ('mpv.log', 'stdout.txt', 'stderr.txt'):
        path = output / name
        if path.exists():
            if path.stat().st_size > 4 * 1024 * 1024:
                raise RuntimeError('Player log exceeds 4 MiB; observation stopped')
            found[name] = path.read_text(errors='replace')
    return found


def check_identity(args):
    if (platform.release() != args.release
            or Path('/proc/sys/kernel/random/boot_id').read_text().strip() != args.boot_id):
        raise RuntimeError('Kernel release/boot identity mismatch')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for key in ('output', 'manifest', 'clip'):
        ap.add_argument('--' + key, type=Path, required=True)
    for key in ('manifest-sha256', 'clip-sha256', 'demo-sha256', 'release', 'boot-id', 'wayland-display'):
        ap.add_argument('--' + key, required=True)
    ap.add_argument('--amd-debug', choices=('notiling',), required=True,
                    help='Explicitly preserve the current tested desktop policy; not an EXP014 A/B')
    ap.add_argument('--passes', type=int, default=3)
    ap.add_argument('--timeout', type=int, default=130)
    ap.add_argument('--experiment')
    ap.add_argument('--uart-session')
    ap.add_argument('--run', action='store_true')
    args = ap.parse_args()
    if sys.platform != 'linux' or platform.machine() != 'x86_64' or os.geteuid() == 0:
        ap.error('Use the normal Linux x86_64 desktop user')
    if not 1 <= args.passes <= 5 or not 30 <= args.timeout <= 240:
        ap.error('Require 1..5 passes and 30..240 second process bound')
    if args.run and (not re.fullmatch(r'EXP-\d{8}-\d{3}', args.experiment or '') or not args.uart_session):
        ap.error('--run requires experiment ID and caller-asserted active UART reference')
    args.output = args.output.absolute()
    if not args.output.parent.is_dir() or args.output.exists() or len(os.fsencode(str(args.output / 'ipc.sock'))) >= 104:
        ap.error('Output needs an existing parent, fresh directory, and short IPC path')
    contaminated = [key for key in os.environ
                    if (key.startswith(OVERRIDES) or key.startswith('DRI_PRIME'))
                    and not (key == 'AMD_DEBUG' and os.environ[key] == args.amd_debug)]
    if contaminated:
        ap.error('Ambient driver overrides must be absent; explicit --amd-debug records policy: ' + ', '.join(contaminated))
    check_identity(args)
    source = Path(__file__).with_name('demo.py')
    if digest(source) != args.demo_sha256 or digest(args.manifest) != args.manifest_sha256:
        ap.error('Frozen demo or installed manifest pin mismatch')
    spec = importlib.util.spec_from_file_location('frozen_mesa_demo', source)
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    if digest(demo.MPV) != demo.MPV_SHA256 or digest(args.clip) != args.clip_sha256:
        ap.error('Player or media pin mismatch')
    manifest = json.loads(args.manifest.read_text())
    count = verify_installed(manifest)
    runtime = Path('/run/user') / str(os.getuid())
    if '/' in args.wayland_display or not stat.S_ISSOCK((runtime / args.wayland_display).stat().st_mode):
        ap.error('Expected the published native Wayland socket')
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(Path.home()),
           'USER': os.environ.get('USER', ''), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'XDG_RUNTIME_DIR': str(runtime), 'WAYLAND_DISPLAY': args.wayland_display,
           'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + str(runtime / 'bus'),
           'AMD_DEBUG': args.amd_debug}
    if not args.run:
        kernel, _ = kernel_log()  # Journal observation only; never read fence debugfs here.
        if KERNEL_FAULT.search(kernel) or not all(
                'PS4_UVD_RUNTIME ' + marker in kernel for marker in ('STARTED', 'QUEUE_READY')):
            ap.error('Current-boot journal lacks healthy positive startup evidence')
        subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/test', '-r', demo.FENCE],
                       capture_output=True, check=True, timeout=3)
        print(json.dumps({'preflight': True, 'installed_identities': count,
                          'environment': env, 'no_device_open_or_submission': True,
                          'hardware_acceptance': False}))
        return 0
    return observe(args, demo, manifest, env)


if __name__ == '__main__':
    raise SystemExit(main())
