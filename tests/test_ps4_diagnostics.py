"""Synthetic diagnostic runner tests: fixture sysfs/proc, never host devices."""
from pathlib import Path
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import wave


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "diagnostics/ps4/runner.py"
spec = importlib.util.spec_from_file_location("psx_diagnostic_runner", SCRIPT)
diag = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = diag
spec.loader.exec_module(diag)


class FakeExecutor:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []
        self.before_call = None

    def __call__(self, argv, timeout, stdout, stderr, env):
        self.calls.append({"argv": argv, "timeout": timeout, "env": env})
        if self.before_call:
            self.before_call(stdout.parent)
        if not self.responses:
            raise AssertionError("Unexpected subprocess: " + repr(argv))
        return self.responses.pop(0)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.fixture = self.base / "fixture"
        self.results = self.base / "results"
        self.fixture.mkdir()
        self.fake = FakeExecutor()
        self.write("/proc/sys/kernel/osrelease", "7.2.11-psx-ps4-baikal-native-dev3\n")
        self.write("/proc/sys/kernel/random/boot_id", "synthetic-boot-001\n")
        self.write("/proc/cpuinfo", "processor : 0\nmodel name : Synthetic Jaguar\nprocessor : 1\n")
        self.write("/proc/meminfo", "MemTotal: 1048576 kB\nMemAvailable: 524288 kB\nSwapTotal: 0 kB\n")
        self.write("/proc/cmdline", "root=private-secret psx_mts_net.autoarm=0 example_key=never-export\n")
        self.write("/sys/devices/system/cpu/online", "0-1\n")
        self.manifest = {
            "schema_version": 1,
            "kernel_release": "7.2.11-psx-ps4-baikal-native-dev3",
            "kernel_sha256": "a" * 64, "modules_sha256": "b" * 64,
            "module_count": 41, "uvd_runtime": False, "private_key": "omit-me",
        }
        self.write_manifest()

    def write(self, logical, content):
        path = self.fixture / logical.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        return path

    def write_manifest(self):
        self.write(diag.MANIFEST_PATH, json.dumps(self.manifest, indent=2) + "\n")

    def tool(self, name):
        path = self.write("/usr/bin/" + name, "synthetic executable; never run\n")
        path.chmod(0o755)
        return str(path)

    def runner(self, executor=True, **kwargs):
        return diag.Runner(self.results, self.fixture,
                           executor=self.fake if executor else None, **kwargs)

    def network(self, carrier="1", state="up"):
        for key, value in {"carrier": carrier, "operstate": state, "mtu": "1500", "flags": "0x1003"}.items():
            self.write("/sys/class/net/psx0/" + key, value)
        self.write("/sys/module/psx_mts_net/parameters/report", json.dumps({"bound": 1, "published": 1,
                   "engine_live": 1, "admin_up": 1, "terminal_fault": 0, "carrier": int(carrier)}))
        self.write("/proc/net/route", "synthetic route evidence")

    @staticmethod
    def ip_responses():
        return [diag.CommandResult(0, json.dumps([{"ifname": "psx0", "addr_info": [{"family": "inet", "scope": "global"}]}])),
                diag.CommandResult(0, json.dumps([{"dev": "psx0", "dst": "default"}]))]

    def test_catalogue_and_cli_default_do_not_probe_or_create_results(self):
        catalogue = diag.load_catalogue()
        self.assertEqual(len(catalogue["tests"]), len(diag.PARAMETERS))
        for test_id, parameters in diag.PARAMETERS.items():
            self.assertEqual(set(catalogue["parameter_schemas"].get(test_id, {})), parameters)
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--root-prefix", str(self.fixture),
                                 "--catalogue"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["suite_version"], "0.1.0")
        listed = subprocess.run([sys.executable, "-B", str(SCRIPT)], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, check=True)
        self.assertIn("nothing starts automatically", listed.stdout)
        self.assertFalse(self.results.exists())

    def test_inventory_is_observation_with_exact_receipts_and_no_commands(self):
        self.write("/sys/bus/pci/devices/0000:00:14.1/vendor", "0x104d")
        self.write("/sys/bus/pci/devices/0000:00:14.1/device", "0x90d8")
        self.write("/sys/bus/usb/devices/1-1/idVendor", "0x0001")
        self.write("/sys/bus/usb/devices/1-1/idProduct", "0x0002")
        self.write("/sys/bus/usb/devices/1-1/serial", "do-not-collect")
        self.write("/sys/class/thermal/thermal_zone0/temp", "42000")
        result = self.runner().run("inventory.readonly")
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertFalse(result["hardware_acceptance"])
        self.assertEqual(result["evidence"]["cpu"]["listed_processors"], 2)
        self.assertEqual(result["identity"]["boot_id"], "synthetic-boot-001")
        expected = hashlib.sha256((self.fixture / diag.MANIFEST_PATH.lstrip("/")).read_bytes()).hexdigest()
        self.assertEqual(result["identity"]["manifest_sha256"], expected)
        serialized = json.dumps(result)
        for private in ("private-secret", "never-export", "omit-me", "do-not-collect"):
            self.assertNotIn(private, serialized)
        self.assertFalse(self.fake.calls)
        path = Path(result["result_directory"])
        events = [json.loads(line) for line in (path / "journal.jsonl").read_text().splitlines()]
        self.assertEqual([e["event"] for e in events], ["run_started", "run_completed"])
        self.assertEqual(diag.read_json(path / "result.json"), result)
        self.assertFalse(list(path.glob("*.tmp-*")))

    def test_fixture_symlink_cannot_escape_into_another_tree(self):
        outside = self.base / "outside"
        outside.write_text("not-fixture-data")
        escaped = self.fixture / "sys/escape"
        escaped.parent.mkdir(parents=True, exist_ok=True)
        escaped.symlink_to(outside)
        self.assertIsNone(self.runner().fs.text("/sys/escape"))

    def test_storage_inventory_never_opens_device_content(self):
        self.write("/sys/class/block/synthetic0/size", "4096")
        self.write("/sys/class/block/synthetic0/device/model", "FAKE DISK")
        self.write("/proc/self/mountinfo", "fixture mount metadata")
        path = self.fixture / "dev/synthetic0"
        path.parent.mkdir()
        os.mkfifo(path)  # Opening this for data would hang; metadata stat is fine.
        result = self.runner().run("storage.inventory")
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertTrue(result["evidence"]["devices"][0]["device_node_present"])
        self.assertFalse(self.fake.calls)

    def test_runtime_variant_and_power_boundaries_never_execute(self):
        self.write("/session.json", json.dumps({"session_id": "test", "continuity": "completed"}))
        runner = self.runner(session_receipt="/session.json")
        for test_id, status in (("uvd.avc", "UNSUPPORTED"), ("power.off", "BLOCKED"),
                                ("power.reboot", "BLOCKED"), ("power.suspend", "UNSUPPORTED"),
                                ("audio.native48", "DEFERRED"), ("wireless.wifi", "DEFERRED"),
                                ("wireless.bluetooth", "DEFERRED")):
            with self.subTest(test_id=test_id):
                self.assertEqual(runner.run(test_id)["status"], status)
        self.assertFalse(self.fake.calls)

    def test_exact_release_and_manifest_are_required_for_actions(self):
        self.write("/proc/sys/kernel/osrelease", "some-other-kernel")
        self.assertEqual(self.runner().run("cpu.compute")["status"], "BLOCKED")
        self.write("/proc/sys/kernel/osrelease", self.manifest["kernel_release"])
        (self.fixture / diag.MANIFEST_PATH.lstrip("/")).unlink()
        self.assertEqual(self.runner().run("cpu.compute")["status"], "BLOCKED")
        self.assertFalse(self.fake.calls)

    def test_manifest_cannot_relabel_another_kernel_as_supported(self):
        self.manifest["kernel_release"] = "synthetic-unsupported-kernel"
        self.write_manifest()
        self.write("/proc/sys/kernel/osrelease", self.manifest["kernel_release"])
        self.assertEqual(self.runner().run("cpu.compute")["status"], "BLOCKED")
        self.assertFalse(self.fake.calls)

    def test_typed_parameters_reject_unknown_commands_or_values(self):
        runner = self.runner()
        cases = [("cpu.compute", {"seconds": 100}), ("memory.pattern", {"memory_mib": 129}),
                 ("cpu.compute", {"seconds": True}), ("ethernet.status", {"interface": "--help"}),
                 ("ethernet.https", {"url": "http://example.invalid"}),
                 ("ethernet.https", {"url": "https://user:password@example.invalid"}),
                 ("ethernet.https", {"url": "https://example.invalid\n--upload-file=/secret"}),
                 ("audio.44100", {"audio_device": "file:FILE=/tmp/writer"}),
                 ("cpu.compute", {"command": "poweroff"}), ("shell", {})]
        for test_id, params in cases:
            with self.subTest(test_id=test_id, params=params), self.assertRaises(ValueError):
                runner.run(test_id, params)
        self.assertFalse(self.fake.calls)

    def test_fixture_root_will_not_execute_real_tools(self):
        self.tool("vulkaninfo")
        result = self.runner(executor=False).run("gpu.vulkan.info")
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertIn("never execute real tools", result["message"])

    def test_missing_optional_graphics_tool_is_not_run(self):
        result = self.runner().run("gpu.opengl.info")
        self.assertEqual(result["status"], "NOT_RUN")
        self.assertFalse(self.fake.calls)

    def test_successful_graphics_enumeration_is_not_functional_pass(self):
        self.tool("eglinfo")
        self.fake.responses = [diag.CommandResult(0, "Synthetic EGL renderer\n")] * 2
        runner = self.runner()
        result = runner.run("gpu.opengl.info")
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(self.fake.calls[0]["env"], {"EGL_PLATFORM": "surfaceless"})
        self.assertEqual(self.fake.calls[0]["argv"][1:], ["-B", "-p", "surfaceless"])
        self.assertEqual(runner.run("gpu.opengl.info")["status"], "INCONCLUSIVE")
        self.assertEqual(len(self.fake.calls), 2, "A completed observation is not a failed retry")

    def test_graphics_prefers_the_images_pinned_mesa_wrapper(self):
        self.tool("eglinfo")
        wrapper = self.write("/usr/local/bin/eglinfo", "synthetic pinned-Mesa wrapper")
        wrapper.chmod(0o755)
        self.fake.responses = [diag.CommandResult(0, "Synthetic pinned Mesa\n")]
        self.runner().run("gpu.opengl.info")
        self.assertEqual(self.fake.calls[0]["argv"][0], str(wrapper))

    def test_menu_displays_only_this_boots_status_without_running_a_test(self):
        self.fake.responses = [diag.CommandResult(0, json.dumps({"digest": diag.CPU_ANSWER, "iterations": 1}))]
        runner = self.runner()
        runner.run("cpu.compute")
        self.write("/proc/sys/kernel/random/boot_id", "new-synthetic-boot")
        display = io.StringIO()
        with mock.patch.object(sys.stdin, "isatty", return_value=True), mock.patch("builtins.input", return_value="q"), mock.patch("sys.stdout", display):
            self.assertEqual(diag.menu(runner), 0)
        cpu_row = next(line for line in display.getvalue().splitlines() if "CPU / Bounded" in line)
        self.assertIn("NOT_RUN", cpu_row)
        self.assertEqual(len(self.fake.calls), 1)

    def test_graphics_timeout_preserves_output_and_blocks_more_actions(self):
        self.tool("vulkaninfo")
        self.fake.responses = [diag.CommandResult(-9, "before timeout\n", "driver diagnostic\n", timed_out=True)]
        runner = self.runner()
        result = runner.run("gpu.vulkan.info")
        self.assertTrue(result["stop_required"])
        folder = Path(result["result_directory"])
        self.assertEqual((folder / "command-01.stdout").read_text(), "before timeout\n")
        self.assertEqual((folder / "command-01.stderr").read_text(), "driver diagnostic\n")
        self.assertEqual(runner.run("cpu.compute")["status"], "BLOCKED")
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(runner.run("inventory.readonly")["status"], "INCONCLUSIVE")

    def test_cpu_known_answer_and_explicit_retry_reason(self):
        self.fake.responses = [diag.CommandResult(0, json.dumps({"digest": "wrong", "iterations": 3})),
                               diag.CommandResult(0, json.dumps({"digest": diag.CPU_ANSWER, "iterations": 9})),
                               diag.CommandResult(0, json.dumps({"digest": diag.CPU_ANSWER, "iterations": 2}))]
        runner = self.runner()
        self.assertEqual(runner.run("cpu.compute")["status"], "FAIL")
        self.assertEqual(runner.run("cpu.compute")["status"], "BLOCKED")
        self.assertEqual(runner.run("cpu.compute", retry_reason="Corrected synthetic receipt")["status"], "PASS")
        self.assertEqual(runner.run("cpu.compute")["status"], "PASS")
        self.assertEqual(len(self.fake.calls), 3)

    def test_malformed_worker_receipt_does_not_crash_or_pass(self):
        self.fake.responses = [diag.CommandResult(0, "[]")]
        self.assertEqual(self.runner().run("cpu.compute")["status"], "INCONCLUSIVE")

    def test_memory_budget_and_pattern_receipt(self):
        self.write("/proc/meminfo", "MemAvailable: 70000 kB\n")
        self.assertEqual(self.runner().run("memory.pattern", {"memory_mib": 16})["status"], "BLOCKED")
        self.assertFalse(self.fake.calls)
        self.write("/proc/meminfo", "MemAvailable: 524288 kB\n")
        self.fake.responses = [diag.CommandResult(0, json.dumps({"verified_mib": 16, "patterns": [0, 85, 170, 255]}))]
        self.assertEqual(self.runner().run("memory.pattern")["status"], "PASS")

    def test_quick_preset_is_only_inventory_cpu_memory_in_order(self):
        self.fake.responses = [diag.CommandResult(0, json.dumps({"digest": diag.CPU_ANSWER, "iterations": 1})),
                               diag.CommandResult(0, json.dumps({"verified_mib": 32, "patterns": [0, 85, 170, 255]}))]
        runner = self.runner()
        result = runner.quick()
        expected = ["inventory.readonly", "cpu.compute", "memory.pattern"]
        self.assertEqual([r["test_id"] for r in result["results"]], expected)
        self.assertEqual([s["test_id"] for s in runner.catalogue["presets"][0]["steps"]], expected)
        self.assertEqual([c["argv"][-2:] for c in self.fake.calls], [["cpu", "2"], ["memory", "32"]])
        self.assertEqual({r["identity"]["boot_id"] for r in result["results"]}, {"synthetic-boot-001"})
        self.assertEqual({r["preset"]["run_id"] for r in result["results"]}, {result["preset_run_id"]})
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["hardware_acceptance"])

    def test_quick_preset_stops_before_memory_after_cpu_failure(self):
        self.fake.responses = [diag.CommandResult(1, json.dumps({"digest": "wrong", "iterations": 1}))]
        result = self.runner().quick()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual([r["test_id"] for r in result["results"]], ["inventory.readonly", "cpu.compute"])
        self.assertEqual(len(self.fake.calls), 1)

    def test_quick_preset_stops_if_the_boot_identity_changes(self):
        self.fake.responses = [diag.CommandResult(0, json.dumps({"digest": diag.CPU_ANSWER, "iterations": 1}))]
        self.fake.before_call = lambda _: self.write("/proc/sys/kernel/random/boot_id", "changed-synthetic-boot")
        result = self.runner().quick()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(len(self.fake.calls), 1)

    def test_quick_tui_has_one_confirmation_and_no_first_run_retry_prompt(self):
        self.fake.responses = [diag.CommandResult(0, json.dumps({"digest": diag.CPU_ANSWER, "iterations": 1})),
                               diag.CommandResult(0, json.dumps({"verified_mib": 32, "patterns": [0, 85, 170, 255]}))]
        with mock.patch.object(sys.stdin, "isatty", return_value=True), mock.patch("builtins.input", side_effect=["a", "y", "", "q"]) as entered, mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(diag.menu(self.runner()), 0)
        prompts = [call.args[0] for call in entered.call_args_list]
        self.assertEqual(sum("sequence once" in p for p in prompts), 1)
        self.assertFalse(any("Changed evidence" in p for p in prompts))

    def test_journal_and_running_receipt_exist_before_subprocess(self):
        def before(directory):
            record = diag.read_json(directory / "result.json")
            self.assertEqual(record["phase"], "running")
            events = (directory / "journal.jsonl").read_text().splitlines()
            self.assertEqual(json.loads(events[-1])["event"], "command_started")
        self.fake.before_call = before
        self.fake.responses = [diag.CommandResult(0, json.dumps({"digest": diag.CPU_ANSWER, "iterations": 1}))]
        self.runner().run("cpu.compute")

    def test_no_result_sink_means_no_action(self):
        runner = diag.Runner(None, self.fixture, executor=self.fake)
        with self.assertRaisesRegex(ValueError, "results-dir"):
            runner.run("cpu.compute")
        self.assertFalse(self.fake.calls)

    def test_only_one_runner_owns_the_results_directory(self):
        first, second = self.runner(), self.runner()
        with first.lock(), self.assertRaisesRegex(ValueError, "Another diagnostic"):
            second.run("cpu.compute")
        self.assertFalse(self.fake.calls)

    def test_https_cannot_open_or_arm_an_interface(self):
        self.network(carrier="0", state="down")
        self.tool("curl")
        self.tool("ip")
        result = self.runner().run("ethernet.https", {"url": "https://example.invalid/test"})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(self.fake.calls)

    def test_https_respects_actual_native_json_terminal_fault_report(self):
        self.network()
        self.tool("curl")
        self.tool("ip")
        self.write("/sys/module/psx_mts_net/parameters/report", '{"terminal_fault":1,"published":1}')
        result = self.runner().run("ethernet.https", {"url": "https://example.invalid/test"})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(self.fake.calls)

    def test_interruption_during_route_collection_cannot_start_https(self):
        self.network()
        self.tool("curl")
        self.tool("ip")
        self.fake.responses = [diag.CommandResult(-2, "partial address evidence", interrupted=True)]
        result = self.runner().run("ethernet.https", {"url": "https://example.invalid/test"})
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(len(self.fake.calls), 1)
        self.assertTrue(result["commands"][0]["interrupted"])

    def test_bound_https_has_route_evidence_and_fixed_arguments(self):
        self.network()
        self.tool("curl")
        self.tool("ip")
        self.fake.responses = self.ip_responses() + [
            diag.CommandResult(0, json.dumps({"http_code": 200, "ssl_verify_result": 0, "remote_ip": "192.0.2.1", "size_download": 4})),
            diag.CommandResult(0, json.dumps([{"dev": "psx0", "dst": "192.0.2.1"}]))]
        result = self.runner().run("ethernet.https", {"url": "https://example.invalid/test"})
        self.assertEqual(result["status"], "PASS")
        curl = self.fake.calls[2]["argv"]
        self.assertIn("if!psx0", curl)
        self.assertEqual(curl[-2:], ["--url", "https://example.invalid/test"])
        self.assertNotIn("--location", curl)
        self.assertNotIn("--insecure", curl)
        self.assertEqual(self.fake.calls[-1]["argv"][-2:], ["oif", "psx0"])
        self.assertFalse(result["hardware_acceptance"])

    def test_ethernet_connection_uses_only_fixed_helper(self):
        helper = self.write("/usr/lib/psx-diagnostics/ethernet-up", "synthetic helper")
        helper.chmod(0o755)
        self.network()
        self.tool("ip")
        self.fake.responses = [diag.CommandResult(0, '{"connected":true}\n')] + self.ip_responses()
        result = self.runner().run("ethernet.connect")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(self.fake.calls[0]["argv"], [str(helper)])
        self.assertEqual(self.fake.calls[0]["timeout"], 120)
        self.assertFalse(any("modprobe" in " ".join(c["argv"]) for c in self.fake.calls))

    def test_audio_fixture_needs_operator_observation_on_same_boot(self):
        path = self.fixture / "opt/fixtures/voice.wav"
        path.parent.mkdir(parents=True)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(44100)
            wav.writeframes(bytes(44100 * 4))
        self.tool("aplay")
        self.fake.responses = [diag.CommandResult(0)]
        runner = self.runner()
        result = runner.run("audio.44100", {"fixture": "/opt/fixtures/voice.wav"})
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertTrue(result["observation_required"])
        observed = runner.observe(result["run_id"], "yes", "Synthetic observation only")
        self.assertEqual(observed["status"], "PASS")
        self.assertEqual(observed["operator_observation"]["source"], "operator")
        self.assertFalse(observed["hardware_acceptance"])
        self.write("/proc/sys/kernel/random/boot_id", "another-synthetic-boot")
        with self.assertRaisesRegex(ValueError, "another boot"):
            runner.observe(result["run_id"], "yes")

    def test_audio_rejects_native_48khz_fixture_without_playing(self):
        path = self.fixture / "opt/fixtures/wrong.wav"
        path.parent.mkdir(parents=True)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(48000)
            wav.writeframes(bytes(48000 * 4))
        result = self.runner().run("audio.44100", {"fixture": "/opt/fixtures/wrong.wav"})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(self.fake.calls)


class ProcessBoundaryTests(unittest.TestCase):
    def test_worker_known_answer_without_hardware_queries_or_real_limits(self):
        # One synthetic iteration, not a timed host stress/acceptance run.
        import resource
        output = io.StringIO()
        with mock.patch.object(resource, "setrlimit"), mock.patch.object(diag.time, "monotonic", side_effect=[0, 0, 2]), mock.patch("sys.stdout", output):
            self.assertEqual(diag.worker_main("cpu", 1), 0)
        self.assertEqual(json.loads(output.getvalue())["digest"], diag.CPU_ANSWER)

    def test_timeout_keeps_both_streams_and_kills_process_group(self):
        # This launches only synthetic Python processes, never a hardware tool.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "child-survived"
            child = ("import pathlib,time; print('child started',flush=True); time.sleep(0.8); "
                     f"pathlib.Path({str(marker)!r}).write_text('unexpected'); time.sleep(30)")
            parent = ("import subprocess,sys,time; print('stdout survives',flush=True); "
                      "print('stderr survives',file=sys.stderr,flush=True); "
                      f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)")
            result = diag.execute_process([sys.executable, "-c", parent], 0.25,
                                          root / "stdout", root / "stderr")
            self.assertTrue(result.timed_out)
            self.assertIn("stdout survives", (root / "stdout").read_text())
            self.assertIn("stderr survives", (root / "stderr").read_text())
            self.assertLess(result.elapsed_seconds, 3)
            time.sleep(0.9)
            self.assertFalse(marker.exists(), "Timed-out child escaped process-group cleanup")


if __name__ == "__main__":
    unittest.main()
