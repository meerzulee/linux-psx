"""Core-before-advanced policy with real runner records and synthetic workers."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("core_controller_fixture", str(ROOT / "tests/test_ps4_diagnostics_controller.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
FIXTURE = importlib.util.module_from_spec(spec); loader.exec_module(FIXTURE)
APP = FIXTURE.APP


class CorePrerequisiteTests(unittest.TestCase):
    def setUp(self):
        fixture = FIXTURE.ControllerTests("test_state_uses_fixture_hardware_and_never_invokes_real_tools")
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.controller = fixture.controller; self.root = fixture.root; self.fs = fixture.fixture
        self.calls = []; self.failure = None
        self.controller.runner.executor = self.execute
        tool = self.fs / "usr/lib/psx-diagnostics/check-opengl"
        tool.parent.mkdir(parents=True); tool.write_text("synthetic tool; never executed"); tool.chmod(0o755)

    def execute(self, argv, timeout, stdout, stderr, env):
        self.calls.append(list(argv))
        if "_worker" in argv:
            kind, amount = argv[-2], int(argv[-1])
            if kind == "cpu": value = {"digest": APP.diagnostics.CPU_ANSWER, "iterations": 2}
            else: value = {"verified_mib": amount, "patterns": [0, 85, 170, 255]}
            if self.failure == kind:
                value = {"digest": "wrong", "iterations": 1} if kind == "cpu" else {"verified_mib": 0, "patterns": []}
            return APP.diagnostics.CommandResult(0, json.dumps(value), "")
        self.assertTrue(str(argv[0]).endswith("check-opengl"))
        return APP.diagnostics.CommandResult(0, "4096 reference pixels matched", "")

    def request(self, action="test.run", params=None):
        job = self.controller.request(action, {"test_id": "gpu.opengl.render"} if params is None else params)
        deadline = time.monotonic() + 4
        while self.controller.jobs.get(job["id"])["state"] == "running" and time.monotonic() < deadline:
            time.sleep(.01)
        saved = self.controller.jobs.get(job["id"])
        self.assertEqual(saved["state"], "completed", saved)
        return saved["result"]

    def core_records(self):
        return [record for record in self.controller.runner.results() if record["test_id"] in ("cpu.compute", "memory.pattern")]

    def write_record(self, record):
        APP.diagnostics.atomic_json(Path(record["result_directory"]) / "result.json", record)

    def test_missing_core_runs_cpu_then_memory_once_before_selected_gpu(self):
        self.assertEqual(self.request()["status"], "PASS")
        self.assertEqual([argv[-2:] for argv in self.calls[:2]], [["cpu", "2"], ["memory", "32"]])
        self.assertTrue(self.calls[2][0].endswith("check-opengl"))
        self.calls.clear()
        self.assertEqual(self.request()["status"], "PASS")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0][0].endswith("check-opengl"))
        self.assertEqual(len(self.core_records()), 2)

    def test_cpu_failure_blocks_ram_and_gpu_without_automatic_retry(self):
        self.failure = "cpu"
        result = self.request()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(result["prerequisite_blocked"])
        self.assertEqual([argv[-2] for argv in self.calls], ["cpu"])
        self.assertEqual(self.core_records()[0]["status"], "FAIL")
        self.assertEqual(self.request()["status"], "BLOCKED")
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(result["hardware_acceptance"])

    def test_memory_failure_never_runs_gpu_or_retries_core(self):
        self.failure = "memory"
        self.assertEqual(self.request()["status"], "BLOCKED")
        self.assertEqual([argv[-2] for argv in self.calls], ["cpu", "memory"])
        self.assertEqual(self.request()["status"], "BLOCKED")
        self.assertEqual(len(self.calls), 2)

    def test_direct_memory_runs_only_missing_cpu_before_the_selected_ram_amount(self):
        result = self.request(params={"test_id": "memory.pattern", "parameters": {"memory_mib": 16}})
        self.assertEqual(result["status"], "PASS")
        self.assertEqual([argv[-2:] for argv in self.calls], [["cpu", "2"], ["memory", "16"]])

    def test_direct_memory_stops_after_cpu_failure_but_allows_deliberate_ram_retry(self):
        self.failure = "cpu"
        request = {"test_id": "memory.pattern", "parameters": {"memory_mib": 16}}
        self.assertEqual(self.request(params=request)["status"], "BLOCKED")
        self.assertEqual([argv[-2] for argv in self.calls], ["cpu"])
        self.assertEqual(self.request(params=request)["status"], "BLOCKED")
        self.assertEqual(len(self.calls), 1)
        self.failure = None
        self.controller.runner.run("cpu.compute", {"seconds": 2}, retry_reason="Corrected synthetic CPU response")
        self.failure = "memory"
        self.assertEqual(self.request(params=request)["status"], "FAIL")
        self.failure = None
        self.assertEqual(self.request(params=dict(request, retry_reason="Corrected synthetic RAM response"))["status"], "PASS")
        self.assertEqual([argv[-2] for argv in self.calls], ["cpu", "cpu", "memory", "memory"])

    def test_explicit_manual_retry_can_establish_new_core_pass(self):
        self.failure = "cpu"; self.request(); self.failure = None
        without_reason = self.controller.runner.run("cpu.compute", {"seconds": 2})
        self.assertEqual(without_reason["status"], "BLOCKED")
        changed = self.controller.runner.run("cpu.compute", {"seconds": 2}, retry_reason="Corrected synthetic worker response")
        self.assertEqual(changed["status"], "PASS")
        self.assertEqual(self.request()["status"], "PASS")
        self.assertEqual([argv[-2] for argv in self.calls if "_worker" in argv], ["cpu", "cpu", "memory"])

    def test_interrupted_core_blocks_advanced_but_not_network_or_reports(self):
        self.request()
        record = next(r for r in self.core_records() if r["test_id"] == "cpu.compute")
        record.update(phase="running", status="INCONCLUSIVE"); self.write_record(record)
        self.calls.clear()
        self.assertEqual(self.request()["status"], "BLOCKED")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.request("network.ethernet.connect", {})["status"], "PASS")
        self.assertTrue(self.controller.request("results.list"))

    def test_cached_pass_needs_matching_runner_catalogue_manifest_and_boot(self):
        self.request()
        record = next(r for r in self.core_records() if r["test_id"] == "cpu.compute")
        original = dict(record["identity"])
        for key in ("runner_sha256", "catalogue_sha256", "manifest_sha256", "boot_id"):
            with self.subTest(key=key):
                record["identity"] = dict(original, **{key: "different"}); self.write_record(record)
                self.calls.clear()
                blocked = self.controller.core_prerequisites("browser.webgl", allow_run=False)
                self.assertEqual(blocked["status"], "BLOCKED")
                self.assertEqual(self.calls, [])
        record["identity"] = original; self.write_record(record)
        self.assertIsNone(self.controller.core_prerequisites("browser.webgl", allow_run=False))

    def test_invalid_functional_receipt_is_not_reused_or_automatically_retried(self):
        self.request()
        record = next(r for r in self.core_records() if r["test_id"] == "memory.pattern")
        record["evidence"]["patterns"] = [0]; self.write_record(record)
        self.calls.clear()
        self.assertEqual(self.request()["status"], "BLOCKED")
        self.assertEqual(self.calls, [])

    def test_identity_change_after_cpu_prevents_ram_and_gpu(self):
        original = self.execute
        def changed(argv, *args):
            result = original(argv, *args)
            if "_worker" in argv:
                (self.fs / "proc/sys/kernel/random/boot_id").write_text("changed-boot")
            return result
        self.controller.runner.executor = changed
        self.assertEqual(self.request()["status"], "BLOCKED")
        self.assertEqual([argv[-2] for argv in self.calls], ["cpu"])

    def test_every_advanced_window_stops_at_failed_core(self):
        self.failure = "cpu"; self.request(); self.calls.clear()
        with mock.patch.object(APP.subprocess, "run", side_effect=AssertionError("Display command must not execute")):
            for app in ("browser", "chromium", "browser-manual", "chromium-manual", "opengl", "vulkan"):
                with self.subTest(app=app):
                    self.assertEqual(self.request("graphics.launch", {"app": app})["status"], "BLOCKED")
        self.assertEqual(self.calls, [])

    def test_gpu_catalogue_and_future_wayland_classification_are_gated(self):
        self.failure = "cpu"; self.request(); self.calls.clear()
        with mock.patch.object(self.controller.runner, "run", side_effect=AssertionError("Advanced runner must not execute")):
            for test_id in ("gpu.opengl.info", "gpu.vulkan.info", "wayland.egl"):
                self.assertEqual(self.controller.catalogue_test(test_id, {})["status"], "BLOCKED")
        self.assertEqual(self.calls, [])

    def test_failed_cpu_blocks_playback_and_https_diagnostics(self):
        self.failure = "cpu"; self.request(); self.calls.clear()
        requests = ({"test_id": "audio.44100"},
                    {"test_id": "ethernet.https", "parameters": {"url": "https://example.invalid/", "interface": "psx0"}})
        for parameters in requests:
            with self.subTest(test_id=parameters["test_id"]):
                result = self.request(params=parameters)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertTrue(result["prerequisite_blocked"])
        self.assertEqual(self.calls, [])

    def test_failed_cpu_leaves_audio_and_storage_observations_available(self):
        self.failure = "cpu"; self.request(); self.calls.clear()
        for test_id in ("audio.info", "storage.inventory", "inventory.readonly"):
            with self.subTest(test_id=test_id):
                result = self.request(params={"test_id": test_id})
                self.assertEqual(result["status"], "INCONCLUSIVE")
                self.assertFalse(result.get("prerequisite_blocked", False))
        self.assertEqual(self.calls, [])

    def test_browser_start_cannot_auto_run_missing_core_from_http(self):
        with self.assertRaisesRegex(ValueError, "CPU and RAM"):
            self.controller.browser_start({"test_id": "browser.webgl"})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.controller.results()[0]["status"], "BLOCKED")

    def test_core_results_older_than_ui_history_limit_are_reused(self):
        self.request(); self.calls.clear()
        for index in range(205):
            directory = self.controller.results_dir / f"zz-extra-{index}"
            directory.mkdir()
            APP.diagnostics.atomic_json(directory / "result.json", {"run_id": str(index), "test_id": "browser.capabilities", "status": "INCONCLUSIVE"})
        self.assertEqual(self.request()["status"], "PASS")
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__": unittest.main()
