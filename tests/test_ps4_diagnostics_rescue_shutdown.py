# SPDX-License-Identifier: MIT
"""Real private PTY Bash shutdown regression; no host services or devices."""
import configparser
import fcntl
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import pty
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import termios
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / 'rootfs/ps4-diagnostics/psx-rescue.service'


def load_assembler():
    loader = importlib.machinery.SourceFileLoader('rescue_shutdown_alpha', str(ROOT / 'scripts/build-ps4-diagnostics-alpha'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class RescueShutdownTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith('linux'), 'Native Linux PTY regression')
    def test_interactive_rescue_ignores_term_but_exits_on_configured_signal(self):
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(UNIT)
        service = unit['Service']
        command = shlex.split(service['ExecStart'])
        configured_signal = getattr(signal, service['KillSignal'])
        self.assertEqual(service['StandardInput'], 'tty')
        self.assertEqual(service['TTYPath'], '/dev/tty2')
        self.assertNotIn('TimeoutStopSec', service)
        self.assertEqual(command[1:], ['--login'])
        for inherited in (signal.SIG_DFL, signal.SIG_IGN):
            with self.subTest(inherited_hup_and_term=inherited):
                self.exercise_rescue_shell(command, configured_signal, inherited)

    def exercise_rescue_shell(self, command, configured_signal, inherited):
        with tempfile.TemporaryDirectory(dir='/var/tmp', prefix='psx-rescue-stop-') as temporary:
            master, slave = pty.openpty()
            attrs = termios.tcgetattr(slave)
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(slave, termios.TCSANOW, attrs)

            def controlling_terminal():
                # PID1 resets signal dispositions before ExecStart. Popen's
                # restore_signals alone does not reset inherited HUP/TERM.
                signal.signal(signal.SIGHUP, signal.SIG_DFL)
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            # Keep the real login/interactive Bash behavior, but exclude the
            # developer host's profile, history and shell customization.
            original = {sig: signal.signal(sig, inherited) for sig in (signal.SIGHUP, signal.SIGTERM)}
            try:
                process = subprocess.Popen([command[0], '--noprofile', '--norc', *command[1:]],
                    stdin=slave, stdout=slave, stderr=slave, preexec_fn=controlling_terminal,
                    env={'PATH': '/usr/bin:/bin', 'HOME': temporary, 'TERM': 'linux',
                         'PS1': '__RESCUE_PROMPT__ ', 'PS2': '', 'HISTFILE': '/dev/null'}, close_fds=True)
            finally:
                for sig, handler in original.items():
                    signal.signal(sig, handler)
            os.close(slave)
            pending = bytearray()

            def read_marker(marker):
                deadline = time.monotonic() + 2
                while marker not in pending and time.monotonic() < deadline:
                    ready, _, _ = select.select([master], [], [], .05)
                    if ready:
                        pending.extend(os.read(master, 4096))
                        self.assertLess(len(pending), 16384, 'Unexpected PTY output budget')
                self.assertIn(marker, pending)
                del pending[:pending.index(marker) + len(marker)]

            def child_state():
                status = (Path('/proc') / str(process.pid) / 'status').read_text()
                fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
                wchan = (Path('/proc') / str(process.pid) / 'wchan').read_text().strip()
                return {key: fields.get(key, '').strip() for key in ('State', 'SigBlk', 'SigIgn', 'SigCgt')} | {'wchan': wchan}

            try:
                # Split each marker in the command so echoed input cannot be
                # mistaken for the shell actually executing our handshake.
                os.write(master, b'[[ $- == *i* ]] && printf "__RESCUE_%s__\\n" INTERACTIVE\n')
                read_marker(b'__RESCUE_INTERACTIVE__')
                read_marker(b'__RESCUE_PROMPT__')
                process.send_signal(signal.SIGTERM)
                with self.assertRaises(subprocess.TimeoutExpired):
                    process.wait(timeout=.15)
                os.write(master, b'printf "__AFTER_%s__\\n" TERM\n')
                read_marker(b'__AFTER_TERM__')
                read_marker(b'__RESCUE_PROMPT__')
                # The observed service was waiting for terminal input. Wait
                # for that same state rather than signal the printf builtin
                # or the transition back into readline's signal handling.
                deadline = time.monotonic() + 1
                state = child_state()
                def waiting_for_input(value):
                    return (value in ('do_select', 'wait_woken', 'n_tty_read')
                            or value.startswith('poll_schedule_timeout'))
                while not waiting_for_input(state['wchan']) and time.monotonic() < deadline:
                    time.sleep(.005)
                    state = child_state()
                self.assertTrue(waiting_for_input(state['wchan']), state)
                ignored = int(state['SigIgn'], 16)
                self.assertFalse(ignored & (1 << (signal.SIGHUP - 1)), state)
                self.assertTrue(ignored & (1 << (signal.SIGTERM - 1)), state)
                process.send_signal(configured_signal)
                try:
                    code = process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.fail(f'Configured signal did not stop rescue shell: {child_state()}, PTY={bytes(pending)!r}')
                self.assertIn(code, (-configured_signal, 128 + configured_signal))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
                os.close(master)

    def test_only_alpha6_installs_exact_canonical_unit_with_readback_facts(self):
        alpha = load_assembler()
        data = UNIT.read_bytes()
        replacements, facts = alpha.alpha6_rescue_override({'image_version': 'alpha6'})
        target = '/usr/lib/systemd/system/psx-rescue.service'
        self.assertEqual(replacements, {target: (data, 0o644)})
        self.assertEqual(facts, {'rootfs/ps4-diagnostics/psx-rescue.service': {
            'source_sha256': alpha.sha(data), 'installed_sha256': alpha.sha(data), 'path': target}})
        for version in range(1, 6):
            with self.subTest(version=version):
                self.assertEqual(alpha.alpha6_rescue_override({'image_version': f'alpha{version}'}), ({}, {}))


if __name__ == '__main__':
    unittest.main()
