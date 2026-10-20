#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic UART/cache correspondence tests; no devices or acquisition."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import sys
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tested_uart_evidence", HERE / "uart_evidence.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)
RELEASE = "7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1"
EXPERIMENT = "EXP-20261020-999"  # Synthetic identifier; never allocated.
DEVICE = "0000:00:01.0"
ARGS = b"root=LABEL=OMARCHY-PS4 amdgpu.ps4_uvd_live_prepare=1 debugfs=on\n"
STATUS = (f"schema=1 build={RELEASE} interface=prepare-only arm=absent\n"
    "stage=PREPARED error=0 selected=1 attempted=1 retained=1\n"
    "vmid=4 reservation_seen=1 allocator_alive_cached=1 early_fence_init=1\n"
    "observed_generation=1 proof_generation=1 last_event=GART_ENABLE\n"
    "historical_sdma_proof=1 proof_ns=8000000000 reset_counter_at_proof=0 copy_crc32=570ae4eb\n"
    "pd_at_proof=0x100000 pd_bytes=0x20000 pt_bytes=0x2000 pages=812\n"
    "r1_bytes=0x308000 rb_bytes=0x1000 ib_bytes=0x20000\n"
    "observer=cached-only current_hardware_mapping=not-sampled UVD_setup=never\n").encode()
RAW = (f"[    0.000000] Linux version {RELEASE} (synthetic@fixture) #1\n"
    f"[    0.000000] Command line: {ARGS.decode().strip()}\n"
    f"[    0.100000] Kernel command line: {ARGS.decode().strip()}\n"
    f"[    7.000000] amdgpu {DEVICE}: PS4_MAP_CANARY MAP max_pfn=0x1000000 block=10 pd_bytes=0x20000 pde=0xc00 pt_bytes=0x2000 src_pte=0x140ad9067 dst_pte=0x140ada067 ib_pte=0x142fba067\n"
    f"[    8.001000] amdgpu {DEVICE}: PS4_LIVE_PREPARE PREPARED vmid=4 pages=812 copy_crc32=570ae4eb historical_sdma_proof=1 retained=1 UVD_setup=never arm=absent; continuing normal initialization\n").encode()


def session(raw):
    return {"index_record": {"version": 2, "label": EXPERIMENT,
        "state": "completed", "continuity_errors": [], "connection_epoch": 1,
        "capture_generation": "a" * 32, "session_id": "b" * 32,
        "start_offset": 100, "end_offset": 100 + len(raw), "bytes": len(raw),
        "started_at": "2026-10-20T07:00:00+06:00", "stopped_at": "2026-10-20T07:02:00+06:00"},
        "artifacts": {"raw": {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}}}


def evaluate(raw=RAW, status=STATUS, metadata=None, args=ARGS):
    return u.analyze(raw, session(raw) if metadata is None else metadata, status,
        (RELEASE + "\n").encode(), args, expected_release=RELEASE,
        experiment=EXPERIMENT, expected_device=DEVICE)


class EvidenceTests(unittest.TestCase):
    def test_correspondence_never_claims_acceptance(self):
        r = evaluate()
        self.assertTrue(r["correspondence"], r["issues"])
        self.assertEqual(r["status"], "MATCHED_UART_AND_CACHED_PREPARATION_INPUTS")
        for key in ("hardware_acceptance", "normal_userspace_proven", "decode_demonstrated"):
            self.assertIs(r[key], False)
        self.assertEqual(r["records"]["map"][0]["src_pte"], 0x140ad9067)

    def test_crlf_and_loader_prose(self):
        raw = b"    Kernel command line: loader prose\r\n" + RAW.replace(b"\n", b"\r\n")
        self.assertTrue(evaluate(raw)["correspondence"])

    def test_session_faults(self):
        changes = {"state": "active", "continuity_errors": ["serial reconnect"],
            "version": 1, "label": "EXP-20261020-998", "connection_epoch": True,
            "capture_generation": "", "session_id": "x", "start_offset": -1,
            "end_offset": 3, "bytes": True, "started_at": "not a time",
            "stopped_at": "2026-10-20T06:59:00+06:00"}
        for key, value in changes.items():
            with self.subTest(key=key):
                s = session(RAW); s["index_record"][key] = value
                r = evaluate(metadata=s)
                self.assertEqual(r["status"], "INVALID_CONTINUITY")
                self.assertFalse(r["correspondence"])

    def test_receipt_and_session_shapes(self):
        for value in (None, [], {"index_record": []}, {"artifacts": []}, {"index_record": session(RAW)["index_record"], "artifacts": {"raw": []}}):
            with self.subTest(value=value):
                self.assertFalse(evaluate(metadata=value if value is not None else [])["correspondence"])
        s = session(RAW); s["artifacts"]["raw"]["sha256"] = "0" * 64
        self.assertEqual(evaluate(metadata=s)["status"], "INVALID_CONTINUITY")

    def test_bare_completed_index_supported(self):
        self.assertTrue(evaluate(metadata=session(RAW)["index_record"])["correspondence"])

    def test_each_required_record_absent_or_duplicated(self):
        lines = RAW.splitlines(keepends=True)
        for i in range(len(lines)):
            for replacement in ([], [lines[i], lines[i]]):
                with self.subTest(i=i, replacement=len(replacement)):
                    raw = b"".join(lines[:i] + replacement + lines[i+1:])
                    self.assertFalse(evaluate(raw)["correspondence"])

    def test_wrong_build_and_device(self):
        for old, new in ((RELEASE.encode(), b"other-release"),
                         (DEVICE.encode(), b"0000:00:02.0")):
            with self.subTest(old=old):
                r = evaluate(RAW.replace(old, new))
                self.assertFalse(r["correspondence"])
                self.assertEqual(r["status"], "BOOT_IDENTITY_MISMATCH")
        missing = b"\n".join(RAW.split(b"\n")[:-2]) + b"\n"
        r = evaluate(missing.replace(RELEASE.encode(), b"other-release"))
        self.assertEqual(r["status"], "BOOT_IDENTITY_MISMATCH")

    def test_reordered_and_time_regressed(self):
        lines = RAW.splitlines(keepends=True)
        raw = b"".join(lines[:3] + [lines[4], lines[3]])
        self.assertFalse(evaluate(raw)["correspondence"])
        raw = RAW.replace(b"[    8.001000]", b"[    6.001000]")
        self.assertFalse(evaluate(raw)["correspondence"])

    def test_malformed_lines_not_sanitized_into_success(self):
        for raw in (RAW[:-1], RAW.replace(b"PS4_MAP_CANARY MAP ", b"PS4_MAP_CANARY MAP \0"),
                    RAW.replace(b"block=10", b"block=10 block=10"),
                    RAW.replace(b"block=10", b"block=010"),
                    RAW.replace(b"0x1000000", b"0x01000000"),
                    RAW.replace(b"[    7.000000]", b"[    7.000000] forged "),
                    RAW.replace(b"pt_bytes=0x2000", b"pt_bytes=0x2000 unexpected=1"),
                    RAW.replace(b"retained=1 UVD_setup", b"retained=0 UVD_setup")):
            with self.subTest(raw=raw[-80:]):
                self.assertFalse(evaluate(raw)["correspondence"])

    def test_prepared_fields(self):
        for old, new in ((b"vmid=4", b"vmid=5"), (b"pages=812", b"pages=3"),
                         (b"copy_crc32=570ae4eb", b"copy_crc32=00000000")):
            with self.subTest(old=old):
                self.assertFalse(evaluate(RAW.replace(old,new))["correspondence"])

    def test_truncated_duplicate_is_not_ignored(self):
        for tag in (b"PS4_MAP_CANARY MAP", b"PS4_LIVE_PREPARE PREPARED"):
            for suffix in (b"", b"\n", b" \n"):
                with self.subTest(tag=tag, suffix=suffix):
                    raw = RAW + b"[    9.000000] amdgpu 0000:00:01.0: " + tag + suffix
                    r = evaluate(raw)
                    self.assertFalse(r["correspondence"])
                    self.assertTrue(any(x["code"].startswith("MALFORMED_") for x in r["issues"]))

    def test_oversized_decimal_is_malformed_not_exception(self):
        for old, new in ((b"pages=812", b"pages=" + b"9" * 5000),
                         (b"[    7.000000]", b"[" + b"9" * 5000 + b".000000]")):
            with self.subTest(old=old):
                self.assertFalse(evaluate(RAW.replace(old, new))["correspondence"])

    def test_printed_pte_flags_and_aliases(self):
        for bad in (b"0x0", b"0x140ad9000", b"0x140ada067"):
            with self.subTest(bad=bad):
                self.assertFalse(evaluate(RAW.replace(b"src_pte=0x140ad9067", b"src_pte=" + bad))["correspondence"])
        for bit in (1, 2, 32, 64):
            value = (0x140ad9067 & ~bit)
            self.assertFalse(evaluate(RAW.replace(b"src_pte=0x140ad9067", f"src_pte=0x{value:x}".encode()))["correspondence"])
        # Source permits additional non-address flag bits; don't overstate the
        # known subset as the entire allowed encoding.
        self.assertTrue(evaluate(RAW.replace(b"src_pte=0x140ad9067", b"src_pte=0x140ad9167"))["correspondence"])

    def test_dynamic_pd_size_not_fixture_constant(self):
        for max_pfn, pd in ((b"0x1800000", b"0x30000"), (b"0x1000001", b"0x21000"),
                            (b"0x8000000", b"0x100000")):
            raw = RAW.replace(b"0x1000000", max_pfn).replace(b"pd_bytes=0x20000", b"pd_bytes=" + pd)
            status = STATUS.replace(b"pd_bytes=0x20000", b"pd_bytes=" + pd)
            with self.subTest(max_pfn=max_pfn):
                self.assertTrue(evaluate(raw, status)["correspondence"])

    def test_source_geometry_and_cache_disagreement(self):
        for old, new in ((b"block=10", b"block=11"), (b"max_pfn=0x1000000", b"max_pfn=0x3003ff"),
                (b"max_pfn=0x1000000", b"max_pfn=0x8000001"),
                (b"max_pfn=0x1000000", b"max_pfn=0x100000000"),
                (b"pde=0xc00", b"pde=0xc01"), (b"pt_bytes=0x2000", b"pt_bytes=0x1000"),
                (b"pd_bytes=0x20000", b"pd_bytes=0x21000")):
            with self.subTest(new=new):
                self.assertFalse(evaluate(RAW.replace(old,new))["correspondence"])
        self.assertFalse(evaluate(status=STATUS.replace(b"pd_bytes=0x20000",b"pd_bytes=0x30000"))["correspondence"])

    def test_cmdline_must_agree_with_saved_identity(self):
        self.assertFalse(evaluate(RAW.replace(b"debugfs=on", b"debugfs=off", 1))["correspondence"])
        self.assertFalse(evaluate(args=ARGS.replace(b"debugfs=on", b"debugfs=off"))["correspondence"])

    def test_fault_and_old_probe_preserved(self):
        for fault in (b"PS4_MAP_CANARY FAIL stage=x error=-5; PARKED", b"Kernel panic - not syncing", b"Oops: x", b"BUG: unable to handle page fault", b"soft lockup"):
            r = evaluate(RAW + b"[    9.000000] " + fault + b"\n")
            self.assertEqual(r["status"], "PREPARATION_PARKED_OR_FAILED")
            self.assertIn(fault.decode(), r["faults"][0]["text"])
        r = evaluate(RAW + b"[    9.000000] PS4_UVD_READY RESULT=x\n")
        self.assertFalse(r["correspondence"])
        self.assertEqual(len(r["other_probe_lines"]),1)

    def test_uart_lifecycle_contradicts_old_prepared_cache(self):
        for message in (b"GPU reset begin!. Source:  1", b"GPU jobs stop begin!. Source:  1",
                        b"finishing device.", b"Emergency reboot."):
            r = evaluate(RAW + b"[    9.000000] amdgpu 0000:00:01.0: " + message + b"\n")
            self.assertEqual(r["status"], "UART_PREPARATION_HISTORY_INVALIDATED")
            self.assertFalse(r["correspondence"])
            self.assertTrue(r["cached_evaluation"]["matched"])
            self.assertEqual(len(r["lifecycle"]),1)
        unrelated = RAW + b"[    9.000000] amdgpu 0000:00:02.0: finishing device.\n"
        self.assertTrue(evaluate(unrelated)["correspondence"])

    def test_vm_fault_is_preserved_without_causal_claim(self):
        raw = RAW + b"[    9.000000] amdgpu 0000:00:01.0: VM fault (0x04, vmid 4, pasid 0) at page 5, read from 'x' (0x00000000) (0)\n"
        r = evaluate(raw)
        self.assertEqual(r["status"], "PREPARATION_PARKED_OR_FAILED")
        self.assertFalse(r["correspondence"])
        self.assertEqual(len(r["faults"]),1)

    def test_invalidated_history_is_not_missing_or_passed(self):
        valid = STATUS.replace(b"stage=PREPARED error=0", b"stage=INVALIDATED error=-116").replace(
            b"observed_generation=1", b"observed_generation=2").replace(b"last_event=GART_ENABLE", b"last_event=RESET")
        r = evaluate(status=valid)
        self.assertEqual(r["status"], "PREPARATION_HISTORY_INVALIDATED")
        self.assertFalse(r["correspondence"])
        for stage in (b"INVALIDATED", b"RELEASING", b"RELEASED", b"FAILED", b"SELECTED", b"PREPARING", b"DISABLED"):
            changed = STATUS.replace(b"stage=PREPARED", b"stage=" + stage)
            r = evaluate(status=changed)
            self.assertEqual(r["status"], "PREPARATION_STATE_CONTRADICTION")
            self.assertFalse(r["correspondence"])
        r = evaluate(status=STATUS.replace(b"observed_generation=1", b"observed_generation=2").replace(b"last_event=GART_ENABLE", b"last_event=RESET"))
        self.assertEqual(r["status"], "PREPARATION_STATE_CONTRADICTION")

    def test_prepared_zero_generation_is_contradictory(self):
        changed = STATUS.replace(b"observed_generation=1 proof_generation=1 last_event=GART_ENABLE",
                                 b"observed_generation=0 proof_generation=0 last_event=NONE")
        r = evaluate(status=changed)
        self.assertEqual(r["status"], "PREPARATION_STATE_CONTRADICTION")
        self.assertFalse(r["correspondence"])

    def test_malformed_cache_not_an_invalidation_diagnosis(self):
        r = evaluate(status=STATUS.replace(b"stage=PREPARED", b"stage=INVALIDATED unknown=1"))
        self.assertEqual(r["status"], "CACHED_OBSERVATION_NOT_MATCHED")

    def test_inputs_not_modified(self):
        s = session(RAW); before = copy.deepcopy(s)
        evaluate(metadata=s)
        self.assertEqual(s, before)

    def test_invalid_api_inputs(self):
        kwargs = dict(raw=RAW, session=session(RAW), status_raw=STATUS,
            uname_raw=(RELEASE+"\n").encode(), cmdline_raw=ARGS,
            expected_release=RELEASE, experiment=EXPERIMENT, expected_device=DEVICE)
        for key, value in (("raw", "text"), ("raw", b"x" * (u.MAX_RAW_BYTES+1)),
                           ("experiment", "EXP-20261399-001"), ("experiment", "EXP-20261020-000"),
                           ("expected_device", "card0")):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    u.analyze(**{**kwargs,key:value})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("report must be new")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EvidenceTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {"schema":1, "status":"PASS_SYNTHETIC_UART_CACHE_COMPARISON" if result.wasSuccessful() else "FAIL",
        "tests":result.testsRun, "failures":len(result.failures), "errors":len(result.errors),
        "python":sys.version, "platform":platform.platform(), "hardware_actions":False,
        "hardware_acceptance":False, "inputs":{n:hashlib.sha256((HERE/n).read_bytes()).hexdigest()
            for n in ("uart_evidence.py","uart_evidence_test.py","observation.py")}}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    with args.report.open("x") as stream:
        json.dump(report,stream,indent=2,sort_keys=True);stream.write("\n")
    sys.exit(0 if result.wasSuccessful() else 1)
