"""Exercise the real rescue function using a synthetic manifest and PTY only."""
import os
from pathlib import Path
import pty
import select
import subprocess
import tempfile
import termios
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DiagnosticRescueTests(unittest.TestCase):
    def rescue_from_manifest(self, remaining):
        source = (ROOT / "rootfs/ps4-diagnostics/init").read_text()
        body = source.split("rescue() {\n", 1)[1].split("\n}\n", 1)[0]
        # Extract only rescue; never run init, mounts, or the real say/kmsg path.
        body = body.replace("/dev/console", '"$PSX_RESCUE_CONSOLE"')
        script = ('say() { :; }\nrescue() {\n' + body + '\n}\n'
                  'while IFS= read -r row; do rescue "fixture failure"; done '
                  '< "$PSX_RESCUE_MANIFEST"\n')
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "frontend.tsv"
            manifest.write_text("bad-row\n" + remaining)
            master, slave = pty.openpty()
            process = None
            try:
                attributes = termios.tcgetattr(slave)
                attributes[3] &= ~termios.ECHO
                termios.tcsetattr(slave, termios.TCSANOW, attributes)
                process = subprocess.Popen(
                    ["/bin/sh", "-c", script], cwd=temporary,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True,
                    env={"PATH": "/usr/bin:/bin", "ENV": "", "PS1": "", "PS2": "",
                         "PSX_RESCUE_CONSOLE": os.ttyname(slave),
                         "PSX_RESCUE_MANIFEST": str(manifest)})
                # Commands must arrive through the PTY, not the caller's TSV.
                # Split marker text so echoed input cannot satisfy assertions.
                os.write(master, b"printf 'PSX_RESCUE_%s\\n' STDOUT; "
                         b"printf 'PSX_RESCUE_%s\\n' STDERR >&2; "
                         b"case $- in *i*) printf 'PSX_RESCUE_%s\\n' INTERACTIVE;; esac; "
                         b"exit 0\n")
                output = bytearray()
                expected = (b"PSX_RESCUE_STDOUT", b"PSX_RESCUE_STDERR",
                            b"PSX_RESCUE_INTERACTIVE")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([master], [], [], 0.1)
                    if ready:
                        output.extend(os.read(master, 4096))
                        if all(marker in output for marker in expected):
                            break
                    elif process.poll() is not None:
                        break
                for marker in expected:
                    self.assertIn(marker, output, output.decode(errors="replace"))
                self.assertEqual(process.wait(timeout=3), 0)
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=3)
                os.close(master)
                os.close(slave)

    def test_rescue_ignores_unread_manifest_commands(self):
        self.rescue_from_manifest("exit 91\n")

    def test_rescue_accepts_console_input_after_manifest_eof(self):
        self.rescue_from_manifest("")


if __name__ == "__main__":
    unittest.main()
