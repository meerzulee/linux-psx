#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic offline UART cases; no device, logger or kernel execution."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import unittest
import zlib

sys.dont_write_bytecode = True
import readback

PINNED_SHA = "12404191ab945b7478093a852545adfbaa9af44bd32f83ccf24f489e385628df"


def synthetic_report(expected):
    pitched = bytearray([0xa7] * 24576)
    for row in range(64):
        pitched[row * 256:row * 256 + 64] = expected[row * 64:row * 64 + 64]
    for row in range(32):
        pitched[0x4000 + row * 256:0x4000 + row * 256 + 64] = expected[4096 + row * 64:4096 + row * 64 + 64]
    feedback = bytearray((index * 17 + 3) % 256 for index in range(2048))
    feedback[:4] = (2048).to_bytes(4, "little")
    lines = ["SYNTHETIC OFFLINE TEST DATA: this is not a console capture.",
             "[noise] unrelated boot line",
             "PREPARED fixture=h264-cb-idr-64x64-v1 handle=1 width=64 height=64 pitch=256 uv=16384 ring=300308000 work=30030a000 bytes=262144 phase_deadline_ns=2000000000; CREATE DECODE DESTROY cumulative_wptr=16,32,48",
             "CORE status=00010002 rbc=00011101 ib=0 rp=48 wp=48 context=46525831 fault=00000000 reset_count=0 reset=0 guard=0/00000000/00000000/00000000"]
    sequences = ["46524331", "46524431", "46525831"]
    initials = ["46434930", "46444930", "46434b30"]
    for phase in range(3):
        lines.append(f"PHASE index={phase} published=1 completed=1 end_dw={16 * (phase + 1)} seq={sequences[phase]} initial={initials[phase]} polls=2 joint=2 consecutive=2 publish_ns=1000 confirmed_ns=5000 end_ns=5000 message_crc=cafebabe changed_words=0")
        for last, begin, end in ((0, 2000, 3000), (1, 4000, 5000)):
            lines.append(f"REC phase={phase} last={last} begin_ns={begin} end_ns={end} status=00010002 rp={16 * (phase + 1)} wp={16 * (phase + 1)} ib=0 context={sequences[phase]} fence={sequences[phase]} core=1 history=1 predeadline=1 joint=1")
    lines.append(f"MEMORY bad_region=0 offset=00000000 expected=00000000 actual=00000000 feedback_crc={zlib.crc32(feedback):08x} target_crc={zlib.crc32(pitched):08x} history_snapshots=25 history_reason=0 first_history_reason=0 fault_arm=123456 fault_total=0")
    lines.append(f"PIXELS bytes=6144 sha256={PINNED_SHA} expected={PINNED_SHA} match=1")
    for kind, raw in (("FEEDBACK", feedback), ("PITCHED", pitched)):
        lines.extend(f"{kind} offset={offset} hex={raw[offset:offset + 32].hex()}" for offset in range(0, len(raw), 32))
    lines.append("END result=FRAME_REFERENCE_MATCH error=0 entered=1 initialized=1 enabled=1 phase=3 publications=3 pixels=1 match=1; mappings retained, no DMA quiescence/free/rearm")
    return lines, bytes(pitched), bytes(feedback)


def raw_bytes(lines):
    # Actual printk/device prefixes and CRLF are intentionally exercised.
    return b"\r\n".join((f"[ 123.456789] amdgpu 0000:01:00.0: PS4_UVD_FRAME {line}".encode()
                            if not line.startswith(("SYNTHETIC", "[noise]")) else line.encode())
                           for line in lines) + b"\r\n"


def edit(lines, prefix, old, new):
    result = list(lines)
    indices = [index for index, line in enumerate(result) if line.startswith(prefix)]
    pattern = (r"(?<![A-Za-z0-9_])" if old[0].isalnum() else "") + re.escape(old)
    if len(indices) != 1 or len(re.findall(pattern, result[indices[0]])) != 1:
        raise AssertionError("nonunique synthetic edit: " + prefix + " / " + old)
    result[indices[0]] = re.sub(pattern, lambda _: new, result[indices[0]], count=1)
    return result


class ReadbackCases(unittest.TestCase):
    root = expected_path = None
    attempts = []

    @classmethod
    def setUpClass(cls):
        cls.expected = cls.expected_path.read_bytes()
        if len(cls.expected) != 6144 or hashlib.sha256(cls.expected).hexdigest() != PINNED_SHA:
            raise AssertionError("actual pinned expected.nv12 required")
        cls.lines, cls.pitched, cls.feedback = synthetic_report(cls.expected)

    def invoke(self, lines=None, code=None, expected=None, **attachments):
        label = f"{len(self.attempts):03d}-{self._testMethodName}"
        case = self.root / "cases" / label
        case.mkdir(parents=True, mode=0o700)
        raw = case / "synthetic.raw"
        raw.write_bytes(raw_bytes(self.lines if lines is None else lines))
        expected_path = self.expected_path
        if expected is not None:
            expected_path = case / "expected-test.nv12"
            expected_path.write_bytes(expected)
        receipt = readback.run(raw, expected_path, case / "artifacts", **attachments)
        saved = json.loads((case / "artifacts/receipt.json").read_text())
        self.assertEqual(receipt, saved)
        self.assertFalse(receipt["hardware_acceptance"])
        self.assertTrue(receipt["offline_only"])
        self.assertFalse(receipt["uart_continuity"]["verified"])
        if code is None:
            self.assertEqual(receipt["status"], "PASS_OFFLINE_FRAME_READBACK", receipt)
            self.assertEqual((case / "artifacts/decoded.nv12").read_bytes(), self.expected)
        else:
            self.assertEqual(receipt["status"], "FAIL_OFFLINE_FRAME_READBACK")
            self.assertEqual(receipt["error"]["code"], code, receipt)
        self.attempts.append({"case": label, "expected_error": code, "receipt": str((case / "artifacts/receipt.json").relative_to(self.root))})
        return case, receipt

    def test_valid_exact_outputs_and_private_permissions(self):
        case, receipt = self.invoke()
        output = case / "artifacts"
        self.assertEqual((output / "pitched.bin").read_bytes(), self.pitched)
        self.assertEqual((output / "feedback.bin").read_bytes(), self.feedback)
        self.assertEqual(receipt["checks"]["record_counts"]["PITCHED"], 768)
        self.assertEqual(receipt["checks"]["record_counts"]["FEEDBACK"], 64)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        for file in output.iterdir():
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        self.assertEqual(readback.crc32(b"123456789"), 0xcbf43926)

    def test_report_boundaries_and_counts(self):
        for kind in ("PREPARED", "CORE", "MEMORY", "PIXELS", "END"):
            with self.subTest(missing=kind):
                self.invoke([line for line in self.lines if not line.startswith(kind + " ")], "RECORD_COUNT")
            with self.subTest(duplicate=kind):
                line = next(line for line in self.lines if line.startswith(kind + " "))
                self.invoke(self.lines[:-1] + [line, self.lines[-1]], "RECORD_COUNT")
        lines = list(self.lines)
        core = next(line for line in lines if line.startswith("CORE "))
        lines.remove(core); lines.insert(2, core)
        self.invoke(lines, "FRAME_BOUNDARY")

    def test_end_must_report_three_completed_publications(self):
        for old, new in (("result=FRAME_REFERENCE_MATCH", "result=FRAME_PHASE_TIMEOUT"),
                         ("error=0", "error=-74"), ("publications=3", "publications=2"),
                         ("phase=3", "phase=2"), ("match=1", "match=0")):
            with self.subTest(change=old):
                self.invoke(edit(self.lines, "END ", old, new), "END")

    def test_prepared_geometry(self):
        self.invoke(edit(self.lines, "PREPARED ", "pitch=256", "pitch=64"), "PREPARED")
        self.invoke(edit(self.lines, "PREPARED ", "work=30030a000", "work=300308000"), "PREPARED_RANGE")

    def test_phase_missing_duplicate_and_order(self):
        self.invoke([line for line in self.lines if not line.startswith("PHASE index=1 ")], "PHASES")
        self.invoke(edit(self.lines, "PHASE index=1 ", "index=1", "index=0"), "PHASES")
        self.invoke(edit(self.lines, "PHASE index=1 ", "seq=46524431", "seq=46524331"), "SEQUENCES")

    def test_phase_completion_and_confirmations(self):
        for old, new, code in (("published=1", "published=0", "PHASE_COMPLETE"),
                               ("completed=1", "completed=0", "PHASE_COMPLETE"),
                               ("end_dw=32", "end_dw=16", "PHASE_COMPLETE"),
                               ("consecutive=2", "consecutive=1", "PHASE_CONFIRMATIONS"),
                               ("joint=2", "joint=1", "PHASE_CONFIRMATIONS"),
                               ("polls=2", "polls=2049", "PHASE_CONFIRMATIONS")):
            with self.subTest(change=old):
                self.invoke(edit(self.lines, "PHASE index=1 ", old, new), code)

    def test_deadline_is_strict_and_both_observations_count(self):
        lines = edit(self.lines, "PHASE index=1 ", "confirmed_ns=5000 end_ns=5000", "confirmed_ns=2000000000 end_ns=2000000000")
        self.invoke(lines, "PHASE_DEADLINE")
        self.invoke(edit(self.lines, "REC phase=1 last=0 ", "end_ns=3000", "end_ns=2000000000"), "OBSERVATION_DEADLINE")
        self.invoke(edit(self.lines, "REC phase=1 last=1 ", "begin_ns=4000", "begin_ns=2999"), "OBSERVATION_ORDER")
        self.invoke(edit(self.lines, "REC phase=1 last=1 ", "end_ns=5000", "end_ns=4999"), "OBSERVATION_ORDER")

    def test_two_joint_observations_must_be_present(self):
        self.invoke([line for line in self.lines if not line.startswith("REC phase=1 last=0 ")], "OBSERVATIONS")
        self.invoke(edit(self.lines, "REC phase=1 last=1 ", "last=1", "last=0"), "OBSERVATIONS")
        for field in ("core", "history", "predeadline", "joint"):
            with self.subTest(field=field):
                self.invoke(edit(self.lines, "REC phase=1 last=0 ", field + "=1", field + "=0"), "OBSERVATION_VALID")
        for old, new in (("rp=32", "rp=16"), ("wp=32", "wp=48"),
                         ("fence=46524431", "fence=46444930"), ("context=46524431", "context=46524331")):
            with self.subTest(change=old):
                self.invoke(edit(self.lines, "REC phase=1 last=0 ", old, new), "OBSERVATION_JOINT")

    def test_bad_core_memory_or_history_fails(self):
        self.invoke(edit(self.lines, "CORE ", "guard=0/", "guard=1/"), "FINAL_CORE")
        self.invoke(edit(self.lines, "CORE ", "reset=0", "reset=1"), "FINAL_CORE")
        for old, new in (("bad_region=0", "bad_region=2"), ("history_reason=0", "history_reason=4"),
                         ("first_history_reason=0", "first_history_reason=4"),
                         ("fault_total=0", "fault_total=1"), ("fault_arm=123456", "fault_arm=0"),
                         ("history_snapshots=25", "history_snapshots=0")):
            with self.subTest(change=old):
                self.invoke(edit(self.lines, "MEMORY ", old, new), "MEMORY_HISTORY")

    def test_message_mutation_is_raw_evidence(self):
        self.invoke(edit(self.lines, "PHASE index=1 ", "changed_words=0", "changed_words=512"))
        self.invoke(edit(self.lines, "PHASE index=1 ", "changed_words=0", "changed_words=1025"), "MESSAGE_DELTA")

    def test_chunk_duplicate_gap_extra_and_alignment(self):
        for kind, size in (("PITCHED", 24576), ("FEEDBACK", 2048)):
            first = next(line for line in self.lines if line.startswith(kind + " offset=0 "))
            with self.subTest(kind=kind, error="duplicate"):
                self.invoke(self.lines[:-1] + [first, self.lines[-1]], "CHUNK_DUPLICATE")
            with self.subTest(kind=kind, error="gap"):
                self.invoke([line for line in self.lines if not line.startswith(kind + " offset=32 ")], "CHUNK_GAP")
            with self.subTest(kind=kind, error="extra"):
                self.invoke(self.lines[:-1] + [first.replace("offset=0", "offset=" + str(size)), self.lines[-1]], "CHUNK_RANGE")
            with self.subTest(kind=kind, error="alignment"):
                self.invoke(edit(self.lines, kind + " offset=32 ", "offset=32", "offset=31"), "CHUNK_RANGE")

    def test_chunk_hex_length_and_characters(self):
        line = next(line for line in self.lines if line.startswith("PITCHED offset=0 "))
        for damaged in (line[:-1], line + "00", line[:-1] + "g"):
            with self.subTest(damaged=damaged[-5:]):
                self.invoke([damaged if item == line else item for item in self.lines], "HEX")

    def test_crc_covers_padding_and_feedback(self):
        line = next(line for line in self.lines if line.startswith("PITCHED offset=64 "))
        damaged = line[:-2] + ("00" if line[-2:] != "00" else "01")
        case, _ = self.invoke([damaged if item == line else item for item in self.lines], "PITCHED_CRC")
        self.assertEqual((case / "artifacts/decoded.nv12").read_bytes(), self.expected)
        line = next(line for line in self.lines if line.startswith("FEEDBACK offset=32 "))
        damaged = line[:-2] + ("00" if line[-2:] != "00" else "01")
        self.invoke([damaged if item == line else item for item in self.lines], "FEEDBACK_CRC")

    def test_reported_sha_and_match_are_checked(self):
        self.invoke(edit(self.lines, "PIXELS ", "match=1", "match=0"), "PIXELS_REPORT")
        self.invoke(edit(self.lines, "PIXELS ", "sha256=" + PINNED_SHA, "sha256=" + "0" * 64), "PIXELS_REPORT")
        self.invoke(edit(self.lines, "PIXELS ", "expected=" + PINNED_SHA, "expected=" + "0" * 64), "PIXELS_REPORT")

    def test_wrong_visible_image_cannot_pass_with_updated_crcs_and_printed_hash(self):
        pitched = bytearray(self.pitched); pitched[0] ^= 1
        line = next(line for line in self.lines if line.startswith("PITCHED offset=0 "))
        lines = [f"PITCHED offset=0 hex={pitched[:32].hex()}" if item == line else item for item in self.lines]
        lines = edit(lines, "MEMORY ", f"target_crc={zlib.crc32(self.pitched):08x}", f"target_crc={zlib.crc32(pitched):08x}")
        changed = bytes([self.expected[0] ^ 1]) + self.expected[1:]
        lines = edit(lines, "PIXELS ", "sha256=" + PINNED_SHA, "sha256=" + hashlib.sha256(changed).hexdigest())
        self.invoke(lines, "PIXELS_REFERENCE")

    def test_actual_pinned_expected_file_is_mandatory(self):
        self.invoke(expected=b"\0" * 6144, code="EXPECTED_REFERENCE")
        self.invoke(expected=self.expected[:-1], code="EXPECTED_REFERENCE")
        self.invoke(expected=self.expected + b"\0", code="INPUT_SIZE")
        case = self.root / "missing-expected-cli"
        case.mkdir(mode=0o700)
        raw = case / "synthetic.raw"; raw.write_bytes(raw_bytes(self.lines))
        p = subprocess.run([sys.executable, "-B", str(Path(readback.__file__)), "--raw", str(raw),
                            "--output", str(case / "out")], capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"--expected", p.stderr)
        self.assertFalse((case / "out").exists())

    def test_unknown_duplicate_or_missing_fields(self):
        self.invoke(edit(self.lines, "PHASE index=1 ", "polls=2", "polls=2 polls=2"), "FIELDS")
        self.invoke(edit(self.lines, "PHASE index=1 ", "polls=2", "poll_count=2"), "FIELDS")
        self.invoke(edit(self.lines, "PHASE index=1 ", " polls=2", ""), "FIELDS")
        self.invoke(self.lines[:-1] + ["UNKNOWN extra=1", self.lines[-1]], "RECORD_TYPE")

    def test_numeric_corruption_suffix_and_non_ascii(self):
        self.invoke(edit(self.lines, "PHASE index=1 ", "polls=2", "polls=-1"), "NUMBER")
        self.invoke(edit(self.lines, "PHASE index=1 ", "polls=2", "polls=4294967296"), "NUMBER")
        self.invoke(edit(self.lines, "END ", "; mappings retained, no DMA quiescence/free/rearm", ""), "SUFFIX")
        self.invoke(edit(self.lines, "PIXELS ", "match=1", "match=1é"), "ASCII")

    def test_chunk_offsets_reconstruct_independently_of_print_order(self):
        indices = [index for index, line in enumerate(self.lines) if line.startswith("PITCHED ")]
        lines = list(self.lines)
        for index, line in zip(indices, reversed([self.lines[index] for index in indices])):
            lines[index] = line
        self.invoke(lines)

    def test_opaque_attachments_do_not_claim_continuity(self):
        context = self.root / "existing-session-context.md"; context.write_text("Existing context attachment.\n")
        record = self.root / "existing-session-record.json"; record.write_text("Not interpreted as an invented session schema.\n")
        _, receipt = self.invoke(session_context=context, session_record=record)
        self.assertEqual(receipt["inputs"]["session_record"]["sha256"], hashlib.sha256(record.read_bytes()).hexdigest())
        self.assertEqual(receipt["uart_continuity"]["responsibility"], "caller")

    def test_existing_output_is_never_overwritten(self):
        case, _ = self.invoke()
        before = {file.name: file.read_bytes() for file in (case / "artifacts").iterdir()}
        with self.assertRaises(FileExistsError):
            readback.run(case / "synthetic.raw", self.expected_path, case / "artifacts")
        self.assertEqual(before, {file.name: file.read_bytes() for file in (case / "artifacts").iterdir()})

    def test_cli_success_and_failure_receipts(self):
        case = self.root / "cli-results"
        case.mkdir(mode=0o700)
        for label, lines, exit_code, status in (
                ("valid", self.lines, 0, "PASS_OFFLINE_FRAME_READBACK"),
                ("failed", edit(self.lines, "END ", "error=0", "error=-74"), 2, "FAIL_OFFLINE_FRAME_READBACK")):
            with self.subTest(label=label):
                raw = case / (label + "-synthetic.raw")
                raw.write_bytes(raw_bytes(lines))
                output = case / label
                process = subprocess.run([sys.executable, "-B", str(Path(readback.__file__)),
                                          "--raw", str(raw), "--expected", str(self.expected_path),
                                          "--output", str(output)], capture_output=True, timeout=10)
                self.assertEqual(process.returncode, exit_code, process.stderr)
                result = json.loads(process.stdout)
                self.assertEqual(result["status"], status)
                self.assertFalse(result["hardware_acceptance"])
                self.assertEqual(json.loads((output / "receipt.json").read_text())["status"], status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    ReadbackCases.root = args.output
    ReadbackCases.expected_path = args.expected.resolve()
    for name in ("readback.py", "readback_test.py", "READBACK.md"):
        shutil.copyfile(Path(__file__).parent / name, args.output / name)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReadbackCases))
    (args.output / "unittest.txt").write_text(stream.getvalue())
    receipt = {"status": "PASS_SYNTHETIC_OFFLINE_READBACK_TESTS" if result.wasSuccessful() else "FAIL_SYNTHETIC_OFFLINE_READBACK_TESTS",
               "unittest_cases": result.testsRun, "readback_attempts": len(ReadbackCases.attempts),
               "attempts": ReadbackCases.attempts, "failures": len(result.failures), "errors": len(result.errors),
               "sources": {name: hashlib.sha256((args.output / name).read_bytes()).hexdigest()
                           for name in ("readback.py", "readback_test.py", "READBACK.md")},
               "expected_sha256": hashlib.sha256(args.expected.read_bytes()).hexdigest(),
               "synthetic_only": True, "hardware_acceptance": False, "hardware_actions": False}
    (args.output / "test-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({key: receipt[key] for key in ("status", "unittest_cases", "readback_attempts", "failures", "errors")}))
    if not result.wasSuccessful():
        print(stream.getvalue(), file=sys.stderr)
    return 0 if result.wasSuccessful() else 2


if __name__ == "__main__":
    raise SystemExit(main())
