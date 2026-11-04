"""Alpha UVD orchestration with synthetic files/processes; never opens a device."""
import copy
import importlib.util
import json
from pathlib import Path
import struct
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load("uvd_runner_fixture", ROOT / "tests/test_ps4_diagnostics.py")
CORE = load("uvd_core_fixture", ROOT / "tests/test_ps4_diagnostics_prerequisites.py")
UVD = load("uvd_adapter_fixture", ROOT / "diagnostics/ps4/uvd.py")
HARNESS = load("uvd_playback_fixture", ROOT / "experiments/ps4-uvd-driver/runtime/playback.py")
BOOT = "616aaa3f-28bf-48bf-bdf5-a98560a0ec94"


def synthetic_firmware():
    payload = bytes(313912)
    crc = zlib.crc32(payload) & 0xffffffff
    header = struct.pack('<IIHHHHIIII', 314168, 32, 1, 0, 4, 2, 0x012a6509, 313912, 256, crc)
    return header + bytes(256 - len(header)) + payload, crc


class FirmwareContractTests(unittest.TestCase):
    def test_exact_header_and_computed_crc_are_required_in_addition_to_hash(self):
        data, crc = synthetic_firmware()
        with mock.patch.object(UVD, 'FIRMWARE', dict(UVD.FIRMWARE, sha256=UVD.sha(data))), \
             mock.patch.object(UVD, 'FIRMWARE_CRC', crc):
            value = UVD.validate_firmware(data)
            self.assertEqual(value['bytes'], 314168)
            self.assertEqual(value['payload_offset'], 256)
            for offset, fmt, value in ((0, '<I', 232752), (4, '<I', 64), (8, '<H', 2),
                                       (10, '<H', 1), (12, '<H', 5), (14, '<H', 1),
                                       (16, '<I', 0x012a6508), (20, '<I', 313911), (24, '<I', 32),
                                       (28, '<I', crc ^ 1)):
                changed = bytearray(data); struct.pack_into(fmt, changed, offset, value)
                with self.subTest(offset=offset), self.assertRaises(ValueError):
                    UVD.validate_firmware(changed)
            changed = bytearray(data); changed[-1] = 1
            with self.assertRaisesRegex(ValueError, 'CRC'):
                UVD.validate_firmware(changed)

    def test_stock_length_and_unpinned_payload_are_refused(self):
        with self.assertRaisesRegex(ValueError, '314168'):
            UVD.validate_firmware(bytes(232752))
        data, crc = synthetic_firmware()
        with mock.patch.object(UVD, 'FIRMWARE_CRC', crc), self.assertRaisesRegex(ValueError, 'SHA256'):
            UVD.validate_firmware(data)

    def test_alpha3_keeps_dev2_kernel_and_requires_the_correct_firmware_pin(self):
        profile = json.loads((ROOT / 'manifests/ps4-diagnostics-uvd-alpha3.json').read_text())
        self.assertEqual(UVD.select_contract(profile), UVD.ALPHA3)
        for key in ('kernel_release', 'kernel_sha256', 'config_sha256', 'modules_sha256'):
            self.assertEqual(UVD.ALPHA3[key], UVD.ALPHA2[key])
        self.assertEqual(profile['alpha_inputs']['root_firmware'], UVD.FIRMWARE)
        self.assertEqual(UVD.FIRMWARE['bytes'], 314168)
        self.assertEqual(UVD.FIRMWARE_CRC, 0xa465ba07)


class UVDTests(unittest.TestCase):
    def test_pending_or_unknown_variants_cannot_become_an_accepted_contract(self):
        pending = dict(UVD.ALPHA2, kernel_sha256=None, config_sha256=None, modules_sha256=None)
        with mock.patch.object(UVD, 'ALPHA2', pending):
            with self.assertRaisesRegex(ValueError, 'pins are pending'):
                UVD.select_contract(copy.deepcopy(pending))
        unknown = dict(UVD.ALPHA, profile='ps4-baikal-diagnostics-uvd-unreviewed')
        with self.assertRaisesRegex(ValueError, 'Unknown frozen'):
            UVD.select_contract(unknown)

    def test_registered_combined_variant_cannot_borrow_old_hashes_or_relax_park(self):
        combined = dict(UVD.ALPHA2, kernel_sha256='ab'*32, config_sha256='cd'*32,
                        modules_sha256='ef'*32)
        with mock.patch.object(UVD, 'ALPHA2', combined):
            self.assertEqual(UVD.select_contract(copy.deepcopy(combined)), combined)
            wrong = dict(combined, kernel_sha256=UVD.ALPHA['kernel_sha256'])
            with self.assertRaisesRegex(ValueError, 'exact frozen'):
                UVD.select_contract(wrong)
            wrong = copy.deepcopy(combined)
            wrong['poweroff_fix']['uvd_shutdown_park_unchanged'] = False
            with self.assertRaisesRegex(ValueError, 'exact frozen'):
                UVD.select_contract(wrong)
        self.assertEqual(UVD.select_contract(dict(UVD.ALPHA)), UVD.ALPHA)

    def setUp(self):
        base = BASE.DiagnosticsTests("test_inventory_is_observation_with_exact_receipts_and_no_commands")
        base.setUp(); self.addCleanup(base.doCleanups)
        self.base = base; self.write = base.write; self.fs = base.fixture
        self.mode = "pass"; self.calls = []; self.journals = 0
        self.contract = copy.deepcopy(UVD.DIAGNOSTIC)
        self.firmware = dict(UVD.FIRMWARE)
        self.write("/proc/sys/kernel/osrelease", UVD.ALPHA["kernel_release"])
        self.write("/proc/sys/kernel/random/boot_id", BOOT)
        self.write("/proc/cmdline", " ".join(UVD.BOOT_ARGUMENTS))
        for flag in UVD.BOOT_ARGUMENTS:
            self.write("/sys/module/amdgpu/parameters/" + flag.split("=")[0].split(".")[1], "Y")
        self.session = {"schema_version": 1, "boot_id": BOOT, "experiment_id": "EXP-20261105-999",
                        "session_id": "synthetic-uart-session", "state": "active"}
        self.session_file = self.write(UVD.SESSION, json.dumps(self.session)); self.session_file.chmod(0o600)
        for name in ("python3", "journalctl"):
            base.tool(name)
        self.make_fixture()
        for key, data in (("ffmpeg", b"synthetic ffmpeg never executed"),
                          ("driver", b"synthetic private Mesa never loaded"),
                          ("playback", (ROOT / "experiments/ps4-uvd-driver/runtime/playback.py").read_bytes())):
            self.write(self.contract[key], data)
            self.contract[key + "_sha256"] = UVD.sha(data)
        firmware, self.firmware_crc = synthetic_firmware()
        self.write(self.firmware["path"], firmware)
        self.firmware.update(bytes=len(firmware), sha256=UVD.sha(firmware))
        self.manifest = json.loads((ROOT / "manifests/ps4-diagnostics-uvd-alpha1.json").read_text())
        self.manifest.update(uvd_diagnostic=self.contract, initramfs_sha256="a" * 64, rootfs_sha256="b" * 64,
                             manifest_validation={"embedded_root_contract_verified": True,
                             "observed_kernel_release": UVD.ALPHA["kernel_release"],
                             "observed_media_initramfs_sha256": "a" * 64})
        self.manifest["alpha_inputs"]["root_firmware"] = self.firmware
        self.save_manifest()
        self.runner = base.runner()
        self.runner.executor = self.execute
        self.runner.catalogue["expected_kernel_release"] = UVD.ALPHA["kernel_release"]
        self.runner.tests["uvd.avc"] = dict(self.runner.tests["uvd.avc"], kind="functional", timeout_seconds=120)
        for patch in (mock.patch.object(UVD, "DIAGNOSTIC", self.contract), mock.patch.object(UVD, "FIRMWARE", self.firmware),
                      mock.patch.object(UVD, "FIRMWARE_CRC", self.firmware_crc),
                      mock.patch.object(BASE.diag.Runner, "uvd_adapter", return_value=UVD)):
            patch.start(); self.addCleanup(patch.stop)

    def save_manifest(self):
        self.write(BASE.diag.MANIFEST_PATH, json.dumps(self.manifest))

    def make_fixture(self):
        directory = "/usr/share/psx-diagnostics/uvd/"
        def asset(name, data):
            self.write(directory + name, data)
            return {"path": name, "bytes": len(data), "sha256": UVD.sha(data)}
        frames = []
        reference = bytearray()
        for index in range(120):
            data = bytes([index] * 6); reference.extend(data)
            frames.append(dict(asset(f"frames/{index:03d}.nv12", data), index=index,
                               pict_type="I" if index == 0 else "P"))
        self.raw = bytes(reference)
        self.fixture = {"schema": HARNESS.SCHEMA, "width": 2, "height": 2,
                        "frame_count": 120, "frame_rate": "30/1", "pixel_format": "nv12",
                        "frame_bytes": 6, "codec": "h264", "container": "h264-annex-b", "profile": "Baseline",
                        "frames": frames, "clip": asset("clip.h264", b"synthetic no decoder needed"),
                        "reference": asset("reference.nv12", self.raw), "evidence": []}
        data = json.dumps(self.fixture).encode()
        self.write(self.contract["fixture"], data)
        self.contract["fixture_sha256"] = UVD.sha(data)

    def execute(self, argv, timeout, stdout, stderr, env):
        self.calls.append(list(argv))
        self.assertEqual(env, {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        if argv[0].endswith("journalctl"):
            self.journals += 1
            text = "PS4_UVD_RUNTIME STARTED\nPS4_UVD_RUNTIME QUEUE_READY ring=0 credits=1 AVC experimental\n"
            if self.mode == "not-ready": text = "no queue marker\n"
            if self.mode == "kernel-fault" or self.mode == "post-fault" and self.journals == 2:
                text += "PS4_UVD_RUNTIME CLOSED error=-110; no reset/rearm/free\n"
            if self.mode == "late-journal-fault":
                stdout.write_text(text + "routine evidence\n" * 9000 + "PS4_UVD_RUNTIME CLOSED error=-110\n")
            return BASE.diag.CommandResult(0, text)
        self.assertEqual(argv[1:3], ["-I", "-B"])
        self.assertEqual(timeout, 120)
        self.assertEqual(argv[-2:], ["--timeout", "60"])
        self.assertTrue((self.base.results / "uvd-attempts" / (BOOT + ".json")).is_file())
        if self.mode == "timeout": return BASE.diag.CommandResult(None, timed_out=True)
        if self.mode == "signal": return BASE.diag.CommandResult(-11)
        if self.mode == "interrupt": return BASE.diag.CommandResult(-2, interrupted=True)
        if self.mode == "child-preflight": return BASE.diag.CommandResult(1, '{"status":"FAIL_PREFLIGHT"}')
        output = Path(argv[argv.index("--output") + 1]); output.mkdir(mode=0o700)
        raw = self.raw if self.mode != "mismatch" else b"\xff" + self.raw[1:]
        (output / "decoded.nv12").write_bytes(raw)
        (output / "ffmpeg.stderr.txt").write_text("synthetic VAAPI receipt")
        fixture_path = self.fs / self.contract["fixture"].lstrip("/")
        command = HARNESS.decode_command(self.fs / "usr/bin/ffmpeg", self.fs / "dev/dri/renderD128",
                                         fixture_path.parent / "clip.h264")
        preflight = {"boot_id": BOOT, "kernel_release": UVD.ALPHA["kernel_release"], "command": command,
                     "environment": HARNESS.environment(self.fs / self.contract["driver"].lstrip("/")),
                     "ffmpeg": {"sha256": self.contract["ffmpeg_sha256"]},
                     "driver": {"sha256": self.contract["driver_sha256"]}}
        execution = {"returncode": 0, "failure": None, "timeout_seconds": 60}
        comparison = HARNESS.compare(self.fixture, fixture_path.parent, output / "decoded.nv12")
        for name, value in (("PREFLIGHT.json", preflight), ("EXECUTION.json", execution), ("COMPARISON.json", comparison)):
            (output / name).write_text(json.dumps(value))
        child = {"hardware_decode_invoked": True, "inputs_unchanged": True,
                 "runner_sha256": self.contract["playback_sha256"], "experiment": self.session["experiment_id"],
                 "uart_session_assertion": self.session["session_id"], "preflight": preflight,
                 "execution": execution, "status": "PASS_EXACT_NV12" if comparison["pass"] else "FAIL_COMPARISON",
                 "artifacts": {p.name: {"bytes": p.stat().st_size, "sha256": UVD.sha(p.read_bytes())} for p in output.iterdir()}}
        if self.mode == "forged-software": preflight["command"] = ["ffmpeg", "-i", "clip.h264"]
        if self.mode == "artifact-tamper": (output / "decoded.nv12").write_bytes(b"\xff" + self.raw[1:])
        if self.mode == "oracle-tamper":
            (output / "COMPARISON.json").write_text('{"pass":true}')
            p = output / "COMPARISON.json"
            child["artifacts"][p.name] = {"bytes": p.stat().st_size, "sha256": UVD.sha(p.read_bytes())}
        if self.mode != "missing-result": (output / "RESULT.json").write_text(json.dumps(child))
        return BASE.diag.CommandResult(0 if comparison["pass"] else 1, json.dumps(child))

    def run_uvd(self):
        return self.runner.run("uvd.avc")

    def test_real_manifest_and_unchanged_harness_pin_agree(self):
        manifest = json.loads((ROOT / "manifests/ps4-diagnostics-uvd-alpha1.json").read_text())
        self.assertEqual(manifest["required_boot_arguments"], UVD.BOOT_ARGUMENTS)
        for key, value in UVD.ALPHA.items(): self.assertEqual(manifest[key], value)
        self.assertEqual(manifest["uvd_diagnostic"]["playback_sha256"],
                         UVD.sha((ROOT / "experiments/ps4-uvd-driver/runtime/playback.py").read_bytes()))

    def test_exact_forced_vaapi_pass_is_the_final_active_action_of_the_boot(self):
        result = self.run_uvd()
        self.assertEqual(result["status"], "PASS", result)
        self.assertTrue(result["stop_required"]); self.assertFalse(result["hardware_acceptance"])
        comparison = result["evidence"]["comparison"]
        self.assertEqual(comparison["complete_frames"], 120)
        self.assertEqual(comparison["mismatched_frames"], [])
        self.assertEqual(len(self.calls), 3)
        self.assertIsNotNone(UVD.stop_reason(self.runner))
        self.assertIn("final active test", result["message"])
        self.calls.clear()
        result = self.run_uvd()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(self.calls, [])

    def test_output_directory_fsync_failure_never_promotes_pass(self):
        real_sync = UVD.sync
        def failing(path):
            if path.name == "uvd-reference":
                raise OSError("Synthetic output-directory fsync failure")
            return real_sync(path)
        with mock.patch.object(UVD, "sync", side_effect=failing):
            result = self.run_uvd()
        self.assertEqual(result["status"], "INCONCLUSIVE", result)
        self.assertTrue(result["stop_required"])
        self.assertIn("output-directory fsync", result["evidence"]["incomplete_reason"])
        self.assertIsNotNone(UVD.stop_reason(self.runner))

    def test_marker_parent_fsync_failure_blocks_before_any_external_command(self):
        real_sync = UVD.sync
        def failing(path):
            if path.name == "uvd-attempts":
                raise OSError("Synthetic marker-parent fsync failure")
            return real_sync(path)
        with mock.patch.object(UVD, "sync", side_effect=failing):
            result = self.run_uvd()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(self.calls, [])
        self.assertIsNotNone(UVD.stop_reason(self.runner))

    def test_success_never_rewrites_intent_or_syncs_its_parent_after_launch(self):
        original_execute = self.execute
        captured = []
        def execute(*args):
            marker = self.base.results / "uvd-attempts" / (BOOT + ".json")
            captured.append(marker.read_bytes())
            return original_execute(*args)
        self.runner.executor = execute
        real_sync = UVD.sync
        def checked(path):
            if path.name == "uvd-attempts" and self.calls:
                raise AssertionError("There must be no completion-marker transaction")
            return real_sync(path)
        with mock.patch.object(UVD, "sync", side_effect=checked):
            result = self.run_uvd()
        self.assertEqual(result["status"], "PASS", result)
        marker = self.base.results / "uvd-attempts" / (BOOT + ".json")
        self.assertTrue(captured)
        self.assertTrue(all(data == marker.read_bytes() for data in captured))
        self.assertEqual(json.loads(marker.read_bytes())["phase"], "reserved")
        Path(result["result_directory"], "result.json").unlink()
        restarted = BASE.diag.Runner(self.base.results, self.fs, executor=self.execute)
        self.assertIsNotNone(restarted.uvd_stop_reason())

    def test_even_an_old_pass_marker_cannot_reopen_active_work(self):
        marker = self.base.results / "uvd-attempts" / (BOOT + ".json")
        marker.parent.mkdir(parents=True, mode=0o700)
        marker.write_text(json.dumps({"schema_version": 1, "boot_id": BOOT,
                          "phase": "completed", "status": "PASS", "stop_required": False}))
        marker.chmod(0o600)
        self.assertIsNotNone(UVD.stop_reason(self.runner))
        self.assertEqual(self.run_uvd()["status"], "BLOCKED")
        self.assertEqual(self.calls, [])

    def test_mismatch_has_independent_frame_oracle_and_terminal_stop(self):
        self.mode = "mismatch"
        result = self.run_uvd()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(result["evidence"]["comparison"]["mismatched_frames"], [0])
        self.assertTrue(result["stop_required"])
        self.assertIsNotNone(UVD.stop_reason(self.runner))

    def test_unknown_process_failure_stays_stopped_even_without_recent_result(self):
        self.mode = "timeout"
        result = self.run_uvd()
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertTrue(result["evidence"]["command_timed_out"])
        Path(result["result_directory"], "result.json").unlink()
        restarted = BASE.diag.Runner(self.base.results, self.fs, executor=self.execute)
        restarted.catalogue["expected_kernel_release"] = UVD.ALPHA["kernel_release"]
        self.assertIsNotNone(restarted.uvd_stop_reason())
        self.calls.clear()
        self.assertEqual(restarted.run("cpu.compute", {"seconds": 1})["status"], "BLOCKED")
        self.assertEqual(self.calls, [])

    def test_every_uncertain_or_forged_result_keeps_the_stop_latch(self):
        for mode in ("signal", "interrupt", "child-preflight", "missing-result", "artifact-tamper", "oracle-tamper", "forged-software"):
            with self.subTest(mode=mode):
                fixture = UVDTests("test_mismatch_has_independent_frame_oracle_and_terminal_stop")
                fixture.setUp()
                try:
                    fixture.mode = mode
                    result = fixture.run_uvd()
                    self.assertEqual(result["status"], "INCONCLUSIVE", result)
                    self.assertTrue(result["stop_required"])
                    self.assertIsNotNone(UVD.stop_reason(fixture.runner))
                finally: fixture.doCleanups()

    def test_current_boot_kernel_fault_or_missing_readiness_never_reaches_decoder(self):
        self.mode = "kernel-fault"
        result = self.run_uvd()
        self.assertTrue(result["stop_required"])
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0][0].endswith("journalctl"))

    def test_post_decode_kernel_fault_prevents_pass(self):
        self.mode = "post-fault"
        result = self.run_uvd()
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertTrue(result["stop_required"])
        self.assertIn("terminal evidence", result["evidence"]["incomplete_reason"])

    def test_fault_beyond_stdout_preview_is_not_hidden(self):
        self.mode = "late-journal-fault"
        result = self.run_uvd()
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertTrue(result["stop_required"])
        self.assertEqual(len(self.calls), 1)
        self.assertIn("terminal evidence", result["evidence"]["incomplete_reason"])

    def test_missing_queue_readiness_never_launches_decoder(self):
        self.mode = "not-ready"
        result = self.run_uvd()
        self.assertTrue(result["stop_required"])
        self.assertEqual(len(self.calls), 1)
        self.assertIn("not confirmed ready", result["evidence"]["incomplete_reason"])

    def test_attempt_reservation_failure_never_launches_a_process(self):
        with mock.patch.object(UVD, "create_marker", side_effect=OSError("Synthetic unavailable results media")):
            result = self.run_uvd()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(self.calls, [])

    def test_old_boot_marker_cannot_stall_an_unrelated_new_boot(self):
        self.mode = "timeout"; self.run_uvd()
        self.write("/proc/sys/kernel/random/boot_id", "d7605f88-49e6-4612-adb2-05ac74e0efcd")
        self.assertIsNone(UVD.stop_reason(self.runner))

    def test_private_mesa_alias_is_allowed_only_inside_the_pinned_tree(self):
        path = self.fs / self.contract["driver"].lstrip("/")
        actual = path.parent / "libgallium-synthetic.so"
        path.rename(actual); path.symlink_to(actual)
        self.assertEqual(self.run_uvd()["status"], "PASS")

    def test_missing_wrong_or_unsafe_session_never_executes(self):
        for change in ("boot", "inactive", "permissions", "symlink"):
            with self.subTest(change=change):
                self.session_file.unlink(missing_ok=True)
                value = dict(self.session)
                if change == "boot": value["boot_id"] = "different"
                if change == "inactive": value["state"] = "completed"
                self.session_file.write_text(json.dumps(value)); self.session_file.chmod(0o600)
                if change == "permissions": self.session_file.chmod(0o644)
                if change == "symlink":
                    target = self.session_file.with_name("other.json")
                    target.write_text(json.dumps(value)); target.chmod(0o600)
                    self.session_file.unlink(); self.session_file.symlink_to(target)
                self.assertEqual(self.run_uvd()["status"], "BLOCKED")
                self.assertEqual(self.calls, [])

    def test_wrong_artifacts_or_unverified_boot_contract_never_execute(self):
        for key in ("kernel_sha256", "modules_sha256", "mesa_manifest_sha256", "rootfs_sha256"):
            with self.subTest(key=key):
                old = self.manifest[key]; self.manifest[key] = "wrong"; self.save_manifest()
                self.assertEqual(self.run_uvd()["status"], "BLOCKED")
                self.manifest[key] = old
        self.manifest["manifest_validation"] = {}; self.save_manifest()
        self.assertEqual(self.run_uvd()["status"], "BLOCKED")
        self.assertEqual(self.calls, [])

    def test_malformed_nested_profile_is_a_static_block(self):
        for value in (None, [], "invalid", 1):
            with self.subTest(value=value):
                self.manifest["alpha_inputs"] = value; self.save_manifest()
                self.assertEqual(self.run_uvd()["status"], "BLOCKED")
        self.assertEqual(self.calls, [])

    def test_effective_gate_and_duplicate_commandline_are_refused(self):
        self.write("/proc/cmdline", " ".join(UVD.BOOT_ARGUMENTS + [UVD.BOOT_ARGUMENTS[0]]))
        self.assertEqual(self.run_uvd()["status"], "BLOCKED")
        self.write("/proc/cmdline", " ".join(UVD.BOOT_ARGUMENTS))
        self.write("/sys/module/amdgpu/parameters/ps4_uvd_ready", "N")
        self.assertEqual(self.run_uvd()["status"], "BLOCKED")
        self.assertEqual(self.calls, [])

    def test_changed_fixture_or_executable_is_refused_before_attempt(self):
        self.write(self.contract["ffmpeg"], b"changed")
        self.assertEqual(self.run_uvd()["status"], "BLOCKED")
        self.assertEqual(self.calls, [])
        self.assertFalse((self.base.results / "uvd-attempts").exists())

    def test_attempt_file_symlink_fails_closed(self):
        marker = self.base.results / "uvd-attempts" / (BOOT + ".json")
        marker.parent.mkdir(parents=True, mode=0o700)
        marker.symlink_to(self.base.base / "absent")
        self.assertIsNotNone(UVD.stop_reason(self.runner))
        self.assertEqual(self.run_uvd()["status"], "BLOCKED")
        self.assertEqual(self.calls, [])


class UVDPrerequisiteTests(unittest.TestCase):
    def setUp(self):
        fixture = CORE.CorePrerequisiteTests("test_cpu_failure_blocks_ram_and_gpu_without_automatic_retry")
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.fixture = fixture; self.controller = fixture.controller

    def test_ordinary_profile_is_unsupported_without_core_or_optional_module(self):
        with mock.patch.object(self.controller, "core_prerequisites", side_effect=AssertionError("No ordinary UVD core work")), \
             mock.patch.object(self.controller.runner, "uvd_adapter", side_effect=AssertionError("No optional alpha import")):
            result = self.fixture.request(params={"test_id": "uvd.avc"})
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertEqual(self.fixture.calls, [])

    def test_alpha_uvd_runs_cpu_then_ram_and_failed_cpu_prevents_dispatch(self):
        manifest_path = self.fixture.fs / "etc/psx-diagnostics/boot-set.json"
        manifest = json.loads(manifest_path.read_text()); manifest["uvd_runtime"] = True
        manifest_path.write_text(json.dumps(manifest))
        fake = mock.Mock(); fake.stop_reason.return_value = None
        fake.decode.return_value = {"status": "PASS", "message": "Synthetic decoder", "hardware_acceptance": False}
        with mock.patch.object(self.controller.runner, "uvd_adapter", return_value=fake):
            self.fixture.failure = "cpu"
            result = self.fixture.request(params={"test_id": "uvd.avc"})
            self.assertEqual(result["status"], "BLOCKED")
            fake.decode.assert_not_called()
            self.assertEqual([argv[-2] for argv in self.fixture.calls], ["cpu"])
            self.fixture.failure = None
            self.controller.runner.run("cpu.compute", {"seconds": 2}, retry_reason="Changed synthetic CPU evidence")
            result = self.fixture.request(params={"test_id": "uvd.avc"})
            self.assertEqual(result["status"], "PASS")
            fake.decode.assert_called_once()
            self.assertEqual([argv[-2] for argv in self.fixture.calls], ["cpu", "cpu", "memory"])


if __name__ == "__main__":
    unittest.main()
