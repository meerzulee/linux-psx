#!/usr/bin/env python3
"""Run the reviewed install/rollback sequence; authenticate sudo in your terminal."""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ibdata_common', HERE / 'common.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
WORKSPACE = c.WORKSPACE
UART = WORKSPACE / 'ps4-uart/uart'
os.environ['PATH'] = '/usr/bin:/bin'

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def save(path, value):
    with path.open('x') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())

def command(argv, report=None):
    r = subprocess.run([str(x) for x in argv], text=True, capture_output=True)
    if report:
        save(report, {'argv': [str(x) for x in argv], 'exit_code': r.returncode,
                      'stdout': r.stdout, 'stderr': r.stderr})
    print(r.stdout, end='', flush=True)
    print(r.stderr, end='', file=sys.stderr, flush=True)
    require(r.returncode == 0, 'Command stopped; preserve the receipt: ' + str(argv[0]))
    return r.stdout

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['verify-only', 'install', 'rollback'])
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--review-sha256', required=True)
    parser.add_argument('--confirm-usb-on-host-launch-held', action='store_true',
                        help='Install: positively identified USB isolated on this host; Linux launch held until readback and unmount.')
    parser.add_argument('--console-state', choices=('not-observed', 'orbis-idle', 'fully-off', 'linux-launching'))
    parser.add_argument('--confirm-fresh-poweroff-and-usb-return', action='store_true',
                        help='Rollback after test: assert PS4 fully off and test USB returned.')
    parser.add_argument('--state-evidence', help='Live host USB evidence and launch-hold instructions; console power may be explicitly not-observed for installation.')
    args = parser.parse_args()
    mode = args.mode
    STAGE = args.stage.resolve()
    PLAN_SHA, REVIEW_SHA = args.plan_sha256, args.review_sha256
    require(all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
                for value in (PLAN_SHA, REVIEW_SHA)),
            'Final plan and installer pins are not reviewed; this draft cannot run.')
    require(mode == 'verify-only' or (isinstance(args.state_evidence, str) and bool(args.state_evidence.strip())),
            'Media operation requires host-isolation/launch evidence or the post-test recovery-state report.')
    require(mode != 'install' or (args.confirm_usb_on_host_launch_held and args.console_state in ('not-observed', 'orbis-idle', 'fully-off')),
            'Install requires isolated host USB and held Linux launch; console state must be explicit and cannot be launching.')
    require(mode != 'rollback' or (args.confirm_fresh_poweroff_and_usb_return and args.console_state in (None, 'fully-off')),
            'Rollback requires fresh full-power-off/USB-return confirmation.')
    require(sys.platform == 'linux' and os.geteuid() != 0,
            'Run as the normal user on Omarchynator, not under sudo; authentication is handled below.')
    require(sha(STAGE / 'plan.json') == PLAN_SHA, 'Reviewed plan changed')
    c.validate_reviewed_handoff(STAGE, REVIEW_SHA, PLAN_SHA)
    plan, _, _, _, _, _ = c.load_inputs(STAGE, rollback=mode == 'rollback')
    c.configure(plan)
    m = c.t
    EXP = plan['experiment']
    disk, parts = m.identify(plan, unmounted=True)
    status = command([UART, 'status'])
    require('[+] Status:  READY' in status, 'UART is not READY')
    session = command([UART, 'session', 'status'])
    print('Verified target:', disk['path'], disk['serial'],
          parts['boot']['path'], parts['root']['path'], flush=True)
    if mode == 'verify-only':
        print('PASS: stage, target identity/unmounted state and UART checked; no session or media change.')
        return
    if mode == 'install':
        require('No active debugging session.' in session, 'An experiment is already active')
        index = WORKSPACE / 'ps4-uart/sessions/index.jsonl'
        require(not index.exists() or EXP not in index.read_text(), 'Experiment identifier already used')
    else:
        m.uart_ready(STAGE)
    runtime = STAGE.parent / 'runtime'
    runtime.mkdir(mode=0o700, exist_ok=True)
    require(not (runtime / (mode + '-started.json')).exists(),
            'This mode was already attempted. Preserve evidence and ask the primary agent to inspect it.')
    probe = subprocess.run(['sudo', '-n', '/usr/bin/true'],
                           text=True, capture_output=True)
    if probe.returncode != 0:
        print('Authenticate sudo here. Do not send the password to the agent.', flush=True)
        subprocess.run(['sudo', '-v'], check=True)
    # Only after authentication, establish the one session before media operations.
    if mode == 'install':
        status = command([UART, 'status'])
        require('[+] Status:  READY' in status, 'UART stopped before session start')
        require(EXP[4:12] == datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=6))).strftime('%Y%m%d'), 'Experiment date differs from operator-session date (Asia/Bishkek); review the plan before starting')
        command([UART, 'session', 'start', EXP], runtime / 'session-start.json')
    m.uart_ready(STAGE)
    save(runtime / (mode + '-started.json'), {
        'experiment': EXP, 'mode': mode,
        'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'plan_sha256': PLAN_SHA, 'wrapper_sha256': sha(Path(__file__)),
        'reviewed_handoff_sha256': REVIEW_SHA,
        'host_usb_identity_revalidated': True,
        'console_state': args.console_state if mode == 'install' else 'fully-off',
        'console_state_observed': mode != 'install' or args.console_state != 'not-observed',
        'operator_reported_console_state': (None if args.console_state == 'not-observed' else args.console_state) if mode == 'install' else 'fully-off',
        'operator_holds_linux_launch': mode == 'install',
        'host_usb_isolation_and_launch_hold_confirmed': mode == 'install',
        'fresh_poweroff_and_usb_return_confirmation': mode == 'rollback',
        'physical_state_evidence': args.state_evidence,
        'authority': 'Owner authorized bounded UVD development and retained the prior test image. Primary verified host USB isolation and held launch; current console power may be unobserved for installation. No automatic native restoration.',
    })
    for phase in ('before', 'after'):
        for role in ('boot', 'root'):
            # Reidentify actual target immediately before each read-only check.
            _, fresh = m.identify(plan, unmounted=True)
            part = fresh[role]['path']
            checker = ['/usr/bin/fsck.fat', '-n', part] if role == 'boot' else ['/usr/bin/e2fsck', '-f', '-n', part]
            command(['sudo', '-n', *checker], runtime / (mode + '-' + phase + '-' + role + '-fsck.json'))
        if phase == 'before':
            m.uart_ready(STAGE)
            option = '--apply' if mode == 'install' else '--rollback'
            state_flags = (['--confirm-usb-on-host-launch-held', '--console-state', args.console_state]
                           if mode == 'install' else ['--confirm-poweroff-and-usb-return'])
            raw = command(['sudo', '-n', '/usr/bin/python3', '-B', '-I', HERE / 'install_usb.py',
                           option, STAGE, '--confirm-serial', disk['serial'],
                           '--reviewed-plan-sha256', PLAN_SHA, '--reviewed-handoff-sha256', REVIEW_SHA,
                           *state_flags], runtime / (mode + '-command.json'))
            receipt = json.loads(raw)
            require(receipt.get('committed') is True and receipt.get('unmounted') is True,
                    'Installer did not report a committed and unmounted result')
            require(not receipt.get('cleanup_errors'), 'Installer cleanup failed')
            save(runtime / (mode + '-receipt.json'), receipt)
    m.uart_ready(STAGE)
    _, final_parts = m.identify(plan, unmounted=True)
    save(runtime / (mode + '-complete.json'), {
        'status': 'PASS_INSTALLED_UNMOUNTED' if mode == 'install' else 'PASS_NATIVE_RESTORED_UNMOUNTED',
        'experiment': EXP, 'plan_sha256': PLAN_SHA,
        'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'both_read_only_filesystem_checks_passed_before_and_after': True,
        'partitions': {role: item['path'] for role, item in final_parts.items()},
        'session_left_open_for_primary_agent': True,
    })
    print('PASS: test files installed and USB unmounted. Tell the primary agent; wait for the one-launch instructions.'
          if mode == 'install' else
          'PASS: native boot files restored and USB unmounted. Tell the primary agent so it can close the UART session.')

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('STOP: ' + str(error), file=sys.stderr)
        print('Do not move or boot the USB. Preserve receipts and tell the primary agent. Do not rerun.', file=sys.stderr)
        sys.exit(1)
