# SPDX-License-Identifier: GPL-2.0-only
"""Run the original portable MTS contracts against the header we actually ship."""
from pathlib import Path
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "platforms/ps4/patches/6.18-baikal-mts2/0700-network-mts/0001-net-ps4-add-continuous-polled-baikal-mts.patch"
FIXTURE = ROOT / "tests/fixtures/mts_core_test.c"
HEADER_SHA256 = "d1e138130df0f7d63555704daa61846f478416a6f0e689809ab7fa3ee00a931b"


def active_header():
    """Extract only the new-file hunk, refusing a partial/context-only header."""
    marker = b"diff --git a/drivers/net/ethernet/mts_net_core.h b/drivers/net/ethernet/mts_net_core.h\n"
    content = PATCH.read_bytes()
    if content.count(marker) != 1:
        raise ValueError("active patch must add exactly one portable header")
    section = content.split(marker, 1)[1].split(b"\ndiff --git ", 1)[0]
    lines = section.splitlines(keepends=True)
    hunk_index = next(i for i, line in enumerate(lines) if line.startswith(b"@@ "))
    hunk = re.fullmatch(rb"@@ -0,0 \+1,(\d+) @@\n", lines[hunk_index])
    if not hunk or b"--- /dev/null\n" not in lines[:hunk_index]:
        raise ValueError("portable header must be a complete new-file hunk")
    count = int(hunk.group(1))
    additions = lines[hunk_index + 1:hunk_index + 1 + count]
    if len(additions) != count or any(not line.startswith(b"+") for line in additions):
        raise ValueError("incomplete portable header additions")
    tail = lines[hunk_index + 1 + count:]
    if any(line.strip() for line in tail):
        raise ValueError("unexpected extra content after portable header")
    return b"".join(line[1:] for line in additions)


class MtsPortableCoreTests(unittest.TestCase):
    def test_active_header_is_the_recorded_portable_contract(self):
        self.assertEqual(hashlib.sha256(active_header()).hexdigest(), HEADER_SHA256)

    def test_dma_descriptors_rx_and_tx_accounting(self):
        compiler = shlex.split(os.environ.get("CC", "cc"))
        self.assertTrue(compiler and shutil.which(compiler[0]),
                        "a native C compiler is required for the MTS host contracts")
        with tempfile.TemporaryDirectory(prefix="psx-mts-core-") as directory:
            temporary = Path(directory)
            (temporary / "mts_net_core.h").write_bytes(active_header())
            executable = temporary / "mts-core-test"
            compiled = subprocess.run(compiler + ["-std=c11", "-O2", "-Wall", "-Wextra",
                                      "-Werror", "-I", str(temporary), str(FIXTURE),
                                      "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True,
                                    timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS extent bounds/nonoverlap", result.stdout)
            self.assertIn("TX ledger/3 wraps/BQL bytes", result.stdout)


if __name__ == "__main__":
    unittest.main()
