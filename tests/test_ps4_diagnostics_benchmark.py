"""The 3D benchmark: its tools at boot, its display session and how results are judged."""
import base64
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    loader.exec_module(value)
    return value


FIXTURE = module("benchmark_controller_fixture", ROOT / "tests/test_ps4_diagnostics_controller.py")
APP = FIXTURE.APP
LAUNCH = module("benchmark_launch_graphics", ROOT / "rootfs/ps4-diagnostics-desktop/launch-graphics")
REPACK = module("benchmark_repack", ROOT / "scripts/repack-ps4-diagnostix")

# Exact output of the pinned builds, on this project's host.
GL_OUTPUT = """=======================================================
    glmark2 2023.01
=======================================================
    OpenGL Information
    GL_VENDOR:      Mesa
    GL_RENDERER:    AMD Radeon Graphics (radeonsi, liverpool)
    GL_VERSION:     4.6 (Compatibility Profile) Mesa 26.2.5
    Surface Config: buf=32 r=8 g=8 b=8 a=8 depth=24 stencil=0 samples=0
    Surface Size:   1920x1080 fullscreen
=======================================================
[build] use-vbo=false: FPS: 987 FrameTime: 1.014 ms
[effect2d] kernel=0,1,0;1,-4,1;0,1,0;: FPS: 950 FrameTime: 1.053 ms
** Failed to set swap interval. Results may be bounded above by refresh rate.
=======================================================
                                  glmark2 Score: 1842 
=======================================================
"""
VK_OUTPUT = """=======================================================
    vkmark 2025.01
=======================================================
    Vendor ID:      0x1002
    Device ID:      0x9920
    Device Name:    AMD Radeon Graphics (RADV LIVERPOOL)
    Driver Version: 109060101
    Device UUID:    5d0e40cef77df5af6f9c57eeaf41849b
=======================================================
[vertex] device-local=true: FPS: 2981 FrameTime: 0.335 ms
[desktop] <default>: FPS: 859 FrameTime: 1.164 ms
=======================================================
                                   vkmark Score: 2210
=======================================================
"""


class ParseTests(unittest.TestCase):
    def test_reads_scores_scenes_and_renderer(self):
        parsed = APP.parse_benchmark(GL_OUTPUT, "glmark2")
        self.assertEqual(parsed["score"], 1842)
        self.assertEqual(parsed["renderer"], "AMD Radeon Graphics (radeonsi, liverpool)")
        self.assertEqual(parsed["surface"], "1920x1080 fullscreen")
        self.assertEqual(parsed["scenes"][0], {"scene": "[build] use-vbo=false", "fps": 987, "frame_ms": 1.014})
        self.assertEqual(len(parsed["scenes"]), 2)

    def test_vulkan_device_is_kept_and_its_unique_id_is_not(self):
        parsed = APP.parse_benchmark(VK_OUTPUT, "vkmark")
        self.assertEqual(parsed["score"], 2210)
        self.assertEqual(parsed["device"], "AMD Radeon Graphics (RADV LIVERPOOL)")
        self.assertNotIn("5d0e40ce", json.dumps(parsed))

    def test_another_tools_score_or_a_truncated_log_gives_none(self):
        self.assertIsNone(APP.parse_benchmark(VK_OUTPUT, "glmark2")["score"])
        self.assertIsNone(APP.parse_benchmark(GL_OUTPUT.split("glmark2 Score")[0], "glmark2")["score"])


class ControllerTests(unittest.TestCase):
    def setUp(self):
        fixture = FIXTURE.ControllerTests("test_state_uses_fixture_hardware_and_never_invokes_real_tools")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.controller, self.fs = fixture.controller, fixture.fixture
        self.controller.runner.executor = self.execute
        for tool in APP.BENCHMARK_TOOLS:
            path = self.fs / tool.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic tool; never executed")
            path.chmod(0o755)
        self.results = self.fs / "run/psx-benchmark"
        self.results.mkdir(parents=True)
        self.session = mock.Mock(return_value={"ended": "", "peak_celsius": 71, "seconds": 224})
        self.controller._benchmark_session = self.session
        self.graphics_passed("gpu.opengl.render")
        self.graphics_passed("gpu.vulkan.render")

    def execute(self, argv, timeout, stdout, stderr, env):
        kind, amount = argv[-2], int(argv[-1])
        value = {"digest": APP.diagnostics.CPU_ANSWER, "iterations": 2} if kind == "cpu" else {"verified_mib": amount, "patterns": [0, 85, 170, 255]}
        return APP.diagnostics.CommandResult(0, json.dumps(value), "")

    def submit(self, test_id="gpu.benchmark"):
        """Through a real job, because that is the only way the core checks will run."""
        job = self.controller.request("test.run", {"test_id": test_id})
        deadline = time.monotonic() + 5
        while self.controller.jobs.get(job["id"])["state"] == "running" and time.monotonic() < deadline:
            time.sleep(.01)
        saved = self.controller.jobs.get(job["id"])
        self.assertEqual(saved["state"], "completed", saved)
        return saved["result"]

    def graphics_passed(self, test_id, status="PASS"):
        run_id = "20261109T000000Z-" + hashlib.sha256(test_id.encode()).hexdigest()[:12]
        directory = self.controller.results_dir / run_id
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        (directory / "journal.jsonl").write_text("{}\n")
        APP.diagnostics.atomic_json(directory / "result.json", {
            "run_id": run_id, "test_id": test_id, "identity": self.controller.runner.identity(), "phase": "completed", "status": status,
            "message": "fixture", "started_at": "2026-11-09T00:00:00Z", "completed_at": "2026-11-09T00:00:01Z",
            "hardware_acceptance": False, "result_directory": str(directory)})

    def wrote(self, gl=GL_OUTPUT, vk=None, gl_status=0, vk_status=0):
        if gl is not None:
            (self.results / "glmark2.log").write_text(gl)
            (self.results / "glmark2.status").write_text(f"{gl_status}\n")
        if vk is not None:
            (self.results / "vkmark.log").write_text(vk)
            (self.results / "vkmark.status").write_text(f"{vk_status}\n")

    def test_the_score_passes_and_is_saved_with_every_scene(self):
        self.wrote()
        record = self.submit()
        self.assertEqual((record["status"], record["stop_required"], record["phase"]), ("PASS", False, "completed"))
        self.assertEqual((record["evidence"]["tool"], record["evidence"]["score"]), ("glmark2", 1842))
        self.assertEqual(len(record["evidence"]["scenes"]), 2)
        self.assertEqual(record["evidence"]["peak_celsius"], 71)
        self.assertEqual(self.session.call_args.args[0]["mode"], "benchmark")
        saved = APP.diagnostics.read_json(Path(record["result_directory"]) / "result.json")
        self.assertEqual(saved["status"], "PASS")
        self.assertFalse(saved["hardware_acceptance"])

    def test_the_vulkan_benchmark_runs_only_vkmark_under_its_own_id(self):
        self.wrote(gl=None, vk=VK_OUTPUT)
        record = self.submit("gpu.benchmark.vulkan")
        self.assertEqual((record["status"], record["test_id"]), ("PASS", "gpu.benchmark.vulkan"))
        self.assertEqual((record["evidence"]["tool"], record["evidence"]["score"]), ("vkmark", 2210))
        self.assertEqual(record["evidence"]["renderer"], "AMD Radeon Graphics (RADV LIVERPOOL)")
        self.assertEqual(self.session.call_args.args[0]["mode"], "benchmark-vulkan")

    def test_a_vulkan_failure_does_not_count_against_the_opengl_benchmark(self):
        self.graphics_passed("gpu.vulkan.render", "FAIL")
        self.wrote()
        self.assertEqual(self.submit()["status"], "PASS")

    def test_a_build_without_tools_says_so_and_starts_nothing(self):
        (self.fs / APP.BENCHMARK_TOOLS[0].lstrip("/")).unlink()
        self.assertEqual(self.submit()["status"], "NOT_RUN")
        self.session.assert_not_called()

    def test_each_tool_needs_its_own_graphics_test_to_have_passed(self):
        self.graphics_passed("gpu.opengl.render", "FAIL")
        record = self.submit()
        self.assertEqual(record["status"], "BLOCKED")
        self.assertIn("OpenGL", record["message"])
        self.session.assert_not_called()

    def test_vulkan_has_to_pass_before_its_benchmark(self):
        self.graphics_passed("gpu.vulkan.render", "FAIL")
        record = self.submit("gpu.benchmark.vulkan")
        self.assertEqual(record["status"], "BLOCKED")
        self.assertIn("Vulkan", record["message"])
        self.session.assert_not_called()

    def test_an_ordinary_refusal_fails_this_row_and_lets_the_run_continue(self):
        self.wrote(gl_status=1)
        record = self.submit()
        self.assertEqual((record["status"], record["stop_required"]), ("FAIL", False))
        self.assertIn("glmark2", record["message"])
        self.assertEqual(self.controller.ready()["boot_id"], record["identity"]["boot_id"])

    def test_a_crash_stops_the_run(self):
        self.wrote(gl_status=139)
        record = self.submit()
        self.assertEqual((record["status"], record["stop_required"]), ("INCONCLUSIVE", True))
        self.assertIn("glmark2", record["message"])

    def test_a_gpu_fault_after_the_start_stops_the_run_even_with_scores(self):
        self.wrote()
        (self.fs / "proc").mkdir(exist_ok=True)
        (self.fs / "proc/uptime").write_text("100.00 90.00\n")
        (self.fs / "var/log").mkdir(parents=True, exist_ok=True)
        (self.fs / "var/log/dmesg").write_text("[  101.500000] amdgpu 0000:00:01.0: amdgpu: ring gfx timeout, signaled seq=1\n")
        record = self.submit()
        self.assertEqual((record["status"], record["stop_required"]), ("INCONCLUSIVE", True))
        self.assertEqual(record["evidence"]["gpu_fault_lines"], 1)

    def test_a_job_timeout_that_only_closes_the_decoder_counts_as_a_fault(self):
        self.wrote()
        (self.fs / "proc").mkdir(exist_ok=True)
        (self.fs / "proc/uptime").write_text("100.00 90.00\n")
        (self.fs / "var/log").mkdir(parents=True, exist_ok=True)
        (self.fs / "var/log/dmesg").write_text("[  267.140213] amdgpu 0000:00:01.0: PS4_UVD_RUNTIME CLOSED error=-110; no reset/rearm/free, full poweroff required\n")
        record = self.submit()
        self.assertEqual((record["status"], record["stop_required"]), ("INCONCLUSIVE", True))
        self.assertEqual(record["evidence"]["gpu_fault_lines"], 1)

    def test_a_fault_before_the_start_does_not_count(self):
        self.wrote()
        (self.fs / "proc").mkdir(exist_ok=True)
        (self.fs / "proc/uptime").write_text("100.00 90.00\n")
        (self.fs / "var/log").mkdir(parents=True, exist_ok=True)
        (self.fs / "var/log/dmesg").write_text("[   10.000000] amdgpu 0000:00:01.0: GPU fault detected\n[  101.000000] usb 1-1: GPU reset of nothing\n")
        self.assertEqual(self.submit()["status"], "PASS")

    def test_overheating_and_a_stuck_display_stop_the_run(self):
        for ended, words in (("hot", "90"), ("deadline", "in time")):
            with self.subTest(ended=ended):
                judged = self.controller._judge_benchmark({"ended": ended, "peak_celsius": 90, "seconds": 9}, 0)
                self.assertEqual((judged["status"], judged["stop_required"]), ("INCONCLUSIVE", True))
                self.assertIn(words, judged["message"])

    def test_no_output_means_the_display_session_never_started(self):
        record = self.submit()
        self.assertEqual((record["status"], record["stop_required"]), ("FAIL", False))
        self.assertIn("did not start", record["message"])

    def test_a_link_planted_by_the_session_is_not_followed(self):
        secret = self.fs / "secret"
        secret.write_text(GL_OUTPUT)
        (self.results / "glmark2.log").symlink_to(secret)
        self.assertEqual(self.controller._benchmark_output("glmark2.log"), "")

    def test_a_session_that_cannot_be_supervised_stops_the_run(self):
        self.controller._benchmark_session = mock.Mock(side_effect=ValueError("no systemctl"))
        record = self.submit()
        self.assertEqual((record["status"], record["stop_required"], record["phase"]), ("INCONCLUSIVE", True, "completed"))

    def test_fixture_roots_never_start_a_display(self):
        del self.controller._benchmark_session
        with self.assertRaisesRegex(ValueError, "Fixture"):
            self.controller._benchmark_session()

    def test_the_request_goes_to_the_benchmark_and_takes_no_parameters(self):
        self.controller.benchmark_test = mock.Mock(return_value={"status": "NOT_RUN"})
        job = self.controller.request("test.run", {"test_id": "gpu.benchmark"})
        deadline = time.monotonic() + 4
        while self.controller.jobs.get(job["id"])["state"] == "running" and time.monotonic() < deadline:
            time.sleep(.01)
        self.controller.benchmark_test.assert_called_once_with("", "gpu.benchmark")
        with self.assertRaises(ValueError):
            self.controller.request("test.run", {"test_id": "gpu.benchmark", "parameters": {"seconds": 1}})

    def test_listed_with_the_other_graphics_tests(self):
        ids = {test["id"] for test in APP.EXTRA_TESTS}
        self.assertLessEqual({"gpu.benchmark", "gpu.benchmark.vulkan"}, ids)
        self.assertIn(APP.BENCHMARK_UNIT, APP.WAYLAND_UNITS)
        self.assertIn("psx-diagnostics-graphics@benchmark-vulkan.service", APP.WAYLAND_UNITS)

    def test_the_host_key_is_made_before_sshd_checks_its_configuration(self):
        order = []
        access = mock.Mock()
        access.initialize.side_effect = lambda: order.append("initialize")
        self.controller.ssh_access = mock.Mock(return_value=access)
        self.controller._cancellable_capture = mock.Mock(side_effect=lambda argv, **_: order.append(argv[2]))
        self.controller.runner.fs.fixture = False
        self.addCleanup(setattr, self.controller.runner.fs, "fixture", True)
        self.controller.initialize_ssh()
        self.assertEqual(order, ["psx-ssh-host-key.service", "initialize"])
        self.assertIn("--no-ask-password", self.controller._cancellable_capture.call_args.args[0])
        self.assertEqual(self.controller._ssh_setup_error, "")

    def test_a_wifi_password_never_reaches_the_trace(self):
        lines = []
        with mock.patch.object(APP, "trace", side_effect=lambda text, source="ctl": lines.append(text)), \
             mock.patch.object(self.controller, "_request", return_value={}):
            self.controller.request("network.wifi.connect", {"ssid": "home", "password": "hunter2"})
        self.assertEqual(len(lines), 1)
        self.assertNotIn("hunter2", lines[0])
        self.assertNotIn("home", lines[0])


class WatchTests(unittest.TestCase):
    """What a hang leaves behind on UART."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/var/tmp")
        self.addCleanup(temporary.cleanup)
        self.results = Path(temporary.name)
        self.lines = []
        patcher = mock.patch.object(APP, "trace", side_effect=lambda text, source="ctl": self.lines.append((source, text)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.watch = APP.BenchmarkWatch(mock.Mock(), APP.BENCHMARKS["gpu.benchmark"])
        self.watch.results = self.results

    def test_a_finished_line_is_copied_once(self):
        (self.results / "glmark2.log").write_text("[build] use-vbo=false: FPS: 987 FrameTime: 1.014 ms\n")
        self.assertTrue(self.watch.copy_output())
        self.assertFalse(self.watch.copy_output())
        self.assertEqual(self.lines, [("glmark2", "[build] use-vbo=false: FPS: 987 FrameTime: 1.014 ms")])

    def test_the_scene_running_at_a_hang_is_reported_once_it_stops_growing(self):
        (self.results / "glmark2.log").write_text("[shading] shading=phong:")
        self.watch.copy_output()
        self.assertEqual(self.lines, [])
        self.watch.copy_output()
        self.assertEqual(self.lines, [("glmark2", "[shading] shading=phong:")])

    def test_session_steps_do_not_count_as_tool_progress(self):
        (self.results / "trace.log").write_text("glmark2 starting, limit 360s\n")
        self.assertFalse(self.watch.copy_output())
        self.assertEqual(self.lines, [("step", "glmark2 starting, limit 360s")])

    def test_a_link_planted_by_the_session_is_not_followed(self):
        secret = self.results / "secret"
        secret.write_text("private\n")
        (self.results / "glmark2.log").symlink_to(secret)
        self.watch.copy_output()
        self.assertEqual(self.lines, [])

    def test_runaway_output_is_bounded_on_uart(self):
        (self.results / "glmark2.log").write_text("x\n" * 3000)
        for _ in range(4):
            self.watch.copy_output()
        self.assertEqual(len(self.lines), 2000)

    def test_rings_with_unfinished_work_are_named(self):
        text = ("--- ring 0 (gfx) ---\nLast signaled fence          0x00000010\nLast emitted                 0x00000012\n"
                "--- ring 1 (sdma0) ---\nLast signaled fence          0x00000005\nLast emitted                 0x00000005\n")
        self.assertEqual(APP.parse_rings(text), ["ring gfx BUSY signaled=0x00000010 emitted=0x00000012"])
        self.assertEqual(APP.parse_rings(text.replace("0x00000012", "0x00000010")), ["rings: all 2 idle"])

    def test_tracing_is_off_unless_the_real_controller_starts_it(self):
        mock.patch.stopall()
        self.addCleanup(mock.patch.stopall)
        with mock.patch.object(APP.os, "open", side_effect=AssertionError("opened the kernel log")):
            APP.trace("nothing should be written")


class LaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/var/tmp")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "results"
        patcher = mock.patch.object(LAUNCH, "BENCHMARK_RESULTS", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fresh(self):
        with mock.patch.object(LAUNCH.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000, pw_gid=1000)), \
             mock.patch.object(LAUNCH.os, "chown") as chown:
            LAUNCH.fresh_benchmark_results()
        chown.assert_called_once_with(self.path, 1000, 1000)

    def test_old_results_are_gone_before_the_session_can_write(self):
        self.path.mkdir()
        (self.path / "glmark2.log").write_text("earlier run")
        self.fresh()
        self.assertEqual(list(self.path.iterdir()), [])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o700)

    def test_a_planted_link_is_refused(self):
        self.path.symlink_to(self.path.parent)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            LAUNCH.fresh_benchmark_results()

    def test_runs_as_a_standalone_session_after_emptying_results(self):
        for mode in ("benchmark", "benchmark-vulkan"):
            with self.subTest(mode=mode):
                order = []
                def respond(command, **_):
                    order.append(command)
                    return SimpleNamespace(stdout="inactive\n")
                with mock.patch.object(LAUNCH.os, "geteuid", return_value=0), \
                     mock.patch.object(LAUNCH.sys, "argv", ["launch-graphics", mode]), \
                     mock.patch.object(LAUNCH.os, "uname", return_value=SimpleNamespace(release="fixture")), \
                     mock.patch.object(LAUNCH.Path, "read_text", return_value='{"kernel_release":"fixture"}'), \
                     mock.patch.object(LAUNCH, "trace"), \
                     mock.patch.object(LAUNCH, "fresh_benchmark_results", side_effect=lambda: order.append("fresh")), \
                     mock.patch.object(LAUNCH.subprocess, "run", side_effect=respond), contextlib.redirect_stdout(io.StringIO()):
                    LAUNCH.main()
                self.assertEqual(order[-2:], ["fresh", ["systemctl", "start", f"psx-diagnostics-graphics@{mode}.service"]])

    def test_is_refused_beside_the_diagnostics_terminal_and_is_never_automatic(self):
        def respond(command, **_):
            return SimpleNamespace(stdout="active\n" if command[-1].endswith("@diagnostics.service") else "inactive\n")
        base = (mock.patch.object(LAUNCH.os, "geteuid", return_value=0),
                mock.patch.object(LAUNCH.os, "uname", return_value=SimpleNamespace(release="fixture")),
                mock.patch.object(LAUNCH.Path, "read_text", return_value='{"kernel_release":"fixture"}'),
                mock.patch.object(LAUNCH, "trace"),
                mock.patch.object(LAUNCH.subprocess, "run", side_effect=respond))
        for arguments, message in ((["benchmark"], "already active"), (["--automatic", "benchmark"], "expected root")):
            with self.subTest(arguments=arguments), mock.patch.object(LAUNCH.sys, "argv", ["launch-graphics", *arguments]):
                for patch in base:
                    patch.start()
                try:
                    with self.assertRaisesRegex(ValueError, message):
                        LAUNCH.main()
                finally:
                    for patch in base:
                        patch.stop()


class SessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/var/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tools, self.results, self.runtime = self.root / "tools", self.root / "results", self.root / "runtime"
        for path in (self.tools / "bin", self.results, self.runtime):
            path.mkdir(parents=True)
        (self.root / "mesa.env").write_text("PSX_MESA_MANIFEST_SHA256=fixture\n")
        (self.runtime / "wait-graphics-wm").write_text("#!/bin/sh\nexit 0\n")
        (self.runtime / "wait-graphics-wm").chmod(0o755)

    def shell(self, path, body):
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o755)
        return path

    def run_session(self, glmark2="exit 0", vkmark="exit 0", missing_tools=False, mode="benchmark"):
        text = (ROOT / "rootfs/ps4-diagnostics-desktop/graphics-session").read_text()
        replacements = {"/run/psx-benchmark": self.results, "/run/psx-bench": self.tools, "/run/psx-frontend": self.runtime,
                        "/etc/psx-diagnostics/mesa.env": self.root / "mesa.env",
                        "/usr/bin/xfwm4": self.shell(self.root / "xfwm4", "exit 0\n"),
                        "/usr/bin/python3": self.shell(self.root / "python3", "exit 0\n"),
                        "/usr/bin/timeout": self.shell(self.root / "timeout", 'echo "$3" >> "$TEST_LIMITS"; shift 3; exec "$@"\n')}
        for old, new in replacements.items():
            text = text.replace(old, str(new))
        if not missing_tools:
            self.shell(self.tools / "bin/glmark2", 'echo "glmark2 $*"; echo "LD=$LD_LIBRARY_PATH"\n' + glmark2 + "\n")
            self.shell(self.tools / "bin/vkmark", 'echo "vkmark $*"\n' + vkmark + "\n")
        script = self.root / "session"
        script.write_text(text)
        return subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, timeout=10,
                              env=dict(os.environ, PSX_GRAPHICS_MODE=mode, XDG_RUNTIME_DIR=str(self.runtime),
                                       TEST_LIMITS=str(self.root / "limits"), LD_LIBRARY_PATH="/mesa"))

    def test_the_opengl_session_runs_only_glmark2(self):
        result = self.run_session()
        self.assertEqual(result.returncode, 0, result.stderr)
        gl = (self.results / "glmark2.log").read_text()
        self.assertIn(f"--data-path {self.tools}/share/glmark2 --fullscreen -b :duration=5", gl)
        self.assertIn(f"LD={self.tools}/lib:/mesa", gl)
        self.assertEqual((self.results / "glmark2.status").read_text(), "0\n")
        self.assertFalse((self.results / "vkmark.log").exists())
        self.assertEqual((self.root / "limits").read_text().split(), ["360"])

    def test_the_vulkan_session_runs_only_vkmark(self):
        result = self.run_session(mode="benchmark-vulkan")
        self.assertEqual(result.returncode, 0, result.stderr)
        vk = (self.results / "vkmark.log").read_text()
        self.assertIn(f"--winsys xcb --winsys-dir {self.tools}/lib/vkmark --data-dir {self.tools}/share/vkmark --fullscreen -b :duration=5", vk)
        self.assertEqual((self.results / "vkmark.status").read_text(), "0\n")
        self.assertFalse((self.results / "glmark2.log").exists())
        self.assertEqual((self.root / "limits").read_text().split(), ["180"])

    def test_every_step_is_written_for_the_controller_to_copy(self):
        self.run_session(glmark2="exit 3")
        trace = (self.results / "trace.log").read_text()
        for step in ("graphics-session running as", "window manager ready", "glmark2 starting, limit 360s", "glmark2 exited with status 3"):
            self.assertIn(step, trace)

    def test_a_crash_is_recorded(self):
        result = self.run_session(glmark2="kill -SEGV $$")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.results / "glmark2.status").read_text(), "139\n")

    def test_a_scene_name_reaches_the_file_while_the_tool_is_still_running(self):
        text = (ROOT / "rootfs/ps4-diagnostics-desktop/graphics-session").read_text()
        command = text.split("/usr/bin/timeout --signal=TERM --kill-after=3 \"$limit\" \"$@\" 2>&1 |\n", 1)[1].split("\n", 1)[0].strip()
        target = self.root / "live"
        script = "printf '[build] use-vbo=false:'; sleep 3; echo ' FPS: 1' | cat >/dev/null"
        process = subprocess.Popen(["/bin/bash", "-c", f'({script}) | ' + command.replace('of="$results/$name.log"', f'of="{target}"')])
        self.addCleanup(process.wait)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not (target.exists() and target.read_text()):
            time.sleep(.02)
        self.assertEqual(target.read_text(), "[build] use-vbo=false:")
        self.assertIsNone(process.poll())

    def test_runaway_output_is_bounded(self):
        self.run_session(glmark2="head -c 3000000 /dev/zero; exit 0")
        self.assertLessEqual((self.results / "glmark2.log").stat().st_size, 1048576)

    def test_missing_tools_end_the_session_without_output(self):
        self.assertEqual(self.run_session(missing_tools=True).returncode, 1)
        self.assertEqual([path.name for path in self.results.iterdir()], ["trace.log"])
        self.assertIn("no usable glmark2", (self.results / "trace.log").read_text())


class BootStepTests(unittest.TestCase):
    """Runs the real function from init-ram with only the mount calls replaced."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/var/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "etc").mkdir()
        (self.root / "payload").mkdir()
        (self.root / "run").mkdir()
        self.target = self.root / "run/bench"
        self.manifest = self.root / "etc/psx-benchmark.tsv"
        self.archive = self.root / "payload/tools.tar"
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            REPACK.add(archive, "bin", 0o755)
            REPACK.add(archive, "bin/glmark2", 0o755, b"tool")
            REPACK.add(archive, "data", 0o644, b"data")
        self.data = stream.getvalue()
        self.archive.write_bytes(self.data)
        self.manifest.write_text(f"{hashlib.sha256(self.data).hexdigest()}\t{len(self.data)}\n")

    def run_step(self, mount_status=0):
        source = (ROOT / "rootfs/ps4-diagnostics/init-ram").read_text()
        body = source.split("install_benchmark_tools() {\n", 1)[1].split("\n}\n", 1)[0]
        body = body.replace("/etc/psx-benchmark.tsv", str(self.manifest)).replace("/run/psx-bench", str(self.target))
        body = body.replace("/psx-bench/tools.tar", str(self.archive))
        script = ("umask 077\nsay() { echo \"SAY $*\"; }\n"
                  f"mount() {{ echo \"mount $*\"; return {mount_status}; }}\numount() {{ echo \"umount $*\"; }}\n"
                  "install_benchmark_tools() {\n" + body + "\n}\ninstall_benchmark_tools\necho finished\n")
        return subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, timeout=10)

    def test_unpacks_readable_tools_into_bounded_read_only_ram(self):
        result = self.run_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"mount -t tmpfs -o mode=0755,nosuid,nodev,size=40m tmpfs {self.target}", result.stdout)
        self.assertIn(f"mount -o remount,ro {self.target}", result.stdout)
        self.assertEqual((self.target / "bin/glmark2").stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.target / "data").stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.target / "bin").stat().st_mode & 0o777, 0o755)

    def test_a_build_without_tools_is_silently_fine(self):
        self.manifest.unlink()
        result = self.run_step()
        self.assertEqual(result.stdout, "finished\n")

    def test_damage_costs_the_benchmark_and_never_the_boot(self):
        cases = {"corrupt": lambda: self.archive.write_bytes(b"x" + self.data[1:]),
                 "short": lambda: self.archive.write_bytes(self.data[:-1]),
                 "missing": lambda: self.archive.unlink(),
                 "bad-manifest": lambda: self.manifest.write_text("nonsense\n"),
                 "extra-field": lambda: self.manifest.write_text(f"{hashlib.sha256(self.data).hexdigest()}\t{len(self.data)}\tmore\n"),
                 "too-large": lambda: self.manifest.write_text(f"{hashlib.sha256(self.data).hexdigest()}\t33554433\n")}
        for name, damage in cases.items():
            with self.subTest(name=name):
                self.setUp()
                damage()
                result = self.run_step()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("SAY Benchmark tools:", result.stdout)
                self.assertTrue(result.stdout.endswith("finished\n"))
                self.assertNotIn("mount", result.stdout)
                self.assertFalse(self.target.exists())

    def test_no_ram_for_them_is_not_fatal_either(self):
        result = self.run_step(mount_status=1)
        self.assertIn("no RAM", result.stdout)
        self.assertTrue(result.stdout.endswith("finished\n"))

    def test_init_runs_it_before_moving_run_into_the_root(self):
        text = (ROOT / "rootfs/ps4-diagnostics/init-ram").read_text()
        self.assertLess(text.index("\ninstall_data_assets\n"), text.index("\ninstall_benchmark_tools\n"))
        self.assertLess(text.index("\ninstall_benchmark_tools\n"), text.index("mount --move /run"))


class ChromiumBootStepTests(unittest.TestCase):
    """Runs the real function from init-ram with only the mount calls replaced."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/var/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "run").mkdir()
        self.target = self.root / "run/pack"
        self.manifest = self.root / "chromium.tsv"
        self.archive = self.root / "chromium.ext4.xz"
        self.data = b"compressed chromium"
        self.archive.write_bytes(self.data)
        self.write_row()

    def write_row(self, pin=None, size=None, tail=""):
        pin = pin or hashlib.sha256(self.data).hexdigest()
        size = len(self.data) if size is None else size
        self.manifest.write_text(f"{pin}\t{size}\t{'b' * 64}\t377487360\t{'c' * 64}{tail}\n")

    def run_step(self, mount_status=0):
        source = (ROOT / "rootfs/ps4-diagnostics/init-ram").read_text()
        body = source.split("install_chromium_pack() {\n", 1)[1].split("\n}\n", 1)[0]
        body = body.replace("/etc/psx-chromium.tsv", str(self.manifest)).replace("/run/psx-chromium-pack", str(self.target))
        body = body.replace("/psx-chromium/chromium.ext4.xz", str(self.archive))
        script = ("umask 077\nsay() { echo \"SAY $*\"; }\n"
                  f"mount() {{ echo \"mount $*\"; return {mount_status}; }}\numount() {{ echo \"umount $*\"; }}\n"
                  "install_chromium_pack() {\n" + body + "\n}\ninstall_chromium_pack\necho finished\n")
        return subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, timeout=10)

    def test_copies_the_checked_image_and_its_pin_into_private_ram(self):
        result = self.run_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"mount -t tmpfs -o mode=0700,nosuid,nodev,noexec,size=136m tmpfs {self.target}", result.stdout)
        self.assertIn(f"mount -o remount,ro {self.target}", result.stdout)
        self.assertIn("SAY Chromium ready.", result.stdout)
        self.assertEqual((self.target / "chromium.ext4.xz").read_bytes(), self.data)
        self.assertEqual((self.target / "pin.tsv").read_text(), self.manifest.read_text())
        self.assertEqual((self.target / "pin.tsv").stat().st_mode & 0o777, 0o600)

    def test_a_build_without_chromium_is_silently_fine(self):
        self.manifest.unlink()
        self.assertEqual(self.run_step().stdout, "finished\n")

    def test_damage_costs_the_browser_test_and_never_the_boot(self):
        cases = {"corrupt": lambda: self.archive.write_bytes(b"x" + self.data[1:]),
                 "short": lambda: self.archive.write_bytes(self.data[:-1]),
                 "missing": lambda: self.archive.unlink(),
                 "bad-row": lambda: self.manifest.write_text("nonsense\n"),
                 "extra-field": lambda: self.write_row(tail="\tmore"),
                 "too-large": lambda: self.write_row(size=136314881)}
        for name, damage in cases.items():
            with self.subTest(name=name):
                self.setUp()
                damage()
                result = self.run_step()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("SAY Chromium:", result.stdout)
                self.assertTrue(result.stdout.endswith("finished\n"))
                self.assertNotIn("mount", result.stdout)
                self.assertFalse(self.target.exists())

    def test_no_ram_for_it_is_not_fatal_either(self):
        result = self.run_step(mount_status=1)
        self.assertIn("no RAM", result.stdout)
        self.assertTrue(result.stdout.endswith("finished\n"))

    def test_init_runs_it_before_moving_run_into_the_root(self):
        text = (ROOT / "rootfs/ps4-diagnostics/init-ram").read_text()
        self.assertLess(text.index("\ninstall_chromium_pack\n"), text.index("mount --move /run"))


class OwnerKeyBootStepTests(unittest.TestCase):
    """The development-only key step, as init runs it."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/var/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.seed = self.root / "seed"
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)

    def run_step(self):
        source = (ROOT / "rootfs/ps4-diagnostics/init-ram").read_text()
        body = source.split("install_owner_ssh_keys() {\n", 1)[1].split("\n}\n", 1)[0]
        body = body.replace("/etc/psx-owner-authorized-keys", str(self.seed)).replace("/run/psx-state/state/ssh", str(self.state))
        script = 'say() { echo "SAY $*"; }\ninstall_owner_ssh_keys() {\n' + body + "\n}\ninstall_owner_ssh_keys\necho finished\n"
        return subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, timeout=10)

    def test_a_public_build_does_nothing(self):
        result = self.run_step()
        self.assertEqual(result.stdout, "finished\n")
        self.assertEqual(list(self.state.iterdir()), [])

    def test_a_development_build_leaves_a_private_key_file_where_ssh_setup_reads(self):
        self.seed.write_text("ssh-ed25519 AAAA\n")
        result = self.run_step()
        self.assertIn("owner SSH keys installed", result.stdout)
        path = self.state / "authorized_keys"
        self.assertEqual(path.read_text(), "ssh-ed25519 AAAA\n")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_a_planted_link_is_ignored(self):
        self.seed.symlink_to("/etc/passwd")
        self.assertEqual(self.run_step().stdout, "finished\n")

    def test_init_runs_it_after_the_state_directory_exists_and_before_verification(self):
        text = (ROOT / "rootfs/ps4-diagnostics/init-ram").read_text()
        self.assertLess(text.index("mkdir -p /run/psx-state/results /run/psx-state/state/ssh"), text.index("\ninstall_owner_ssh_keys\n"))
        self.assertLess(text.index("\ninstall_owner_ssh_keys\n"), text.index("mount --move /run"))


class OwnerSeedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/var/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.image = module("benchmark_image_builder", ROOT / "scripts/build-ps4-diagnostics")

    def key(self, name, byte, comment="someone@example.invalid"):
        blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes([byte]) * 32
        path = self.root / name
        path.write_text("ssh-ed25519 " + base64.b64encode(blob).decode() + " " + comment + "\n")
        return path

    def test_comments_are_dropped_and_every_key_is_kept(self):
        seed, fingerprints = REPACK.owner_seed(self.image, [self.key("a.pub", 1), self.key("b.pub", 2)])
        self.assertNotIn(b"example.invalid", seed)
        self.assertEqual(len(seed.splitlines()), 2)
        self.assertEqual(len(fingerprints), 2)
        self.assertTrue(all(value.startswith("SHA256:") for value in fingerprints))

    def test_anything_but_a_public_key_is_refused(self):
        path = self.root / "private"
        path.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n")
        with self.assertRaises(ValueError):
            REPACK.owner_seed(self.image, [path])

    def test_only_a_build_named_dev_may_carry_keys(self):
        with self.assertRaisesRegex(ValueError, "dev"):
            REPACK.repack(self.root / "unused", self.root / "unused", b"", {}, "ram-alpha1-public", [self.key("a.pub", 1)])


class PackTests(unittest.TestCase):
    def manifest(self):
        return json.loads(REPACK.BENCHMARK_MANIFEST.read_text())

    def test_every_package_is_an_exact_public_pin(self):
        for package in self.manifest()["packages"]:
            with self.subTest(package=package["name"]):
                self.assertRegex(package["sha256"], "^[0-9a-f]{64}$")
                self.assertGreater(package["bytes"], 0)
                self.assertTrue(package["url"].startswith("https://archive.archlinux.org/packages/"))
                self.assertTrue(package["url"].endswith("/" + package["filename"]))

    def test_copyleft_tools_record_their_exact_source(self):
        packages = {package["name"]: package for package in self.manifest()["packages"]}
        for name in ("glmark2", "vkmark"):
            self.assertRegex(packages[name]["source"]["sha256"], "^[0-9a-f]{64}$")
            self.assertTrue(packages[name]["source"]["url"].startswith("https://github.com/"))
        targets = {item["to"] for item in self.manifest()["files"]}
        self.assertLessEqual({"licenses/GPL-3.0-or-later.txt", "licenses/LGPL-2.1-only.txt", "licenses/assimp-LICENSE",
                              "licenses/xcb-util-wm-LICENSE", "licenses/glmark2-COPYING.SGI"}, targets)

    def test_a_trimmed_root_updates_exactly_the_boot_contract_lines(self):
        receipt = {"rootfs_sha256": "a" * 64, "compressed_sha256": "b" * 64, "compressed_bytes": 12, "chunk_sha256": ["c" * 64, "d" * 64]}
        text = ("ROOTFS_SHA256='old'\nCOMPRESSED_SHA256='old'\nCOMPRESSED_BYTES='1'\nROOTFS_CHUNK_SHA256='old'\n"
                "ROOT_COMPRESSION='zstd'\n")
        self.assertEqual(REPACK.update_ram_env(text, receipt),
                         f"ROOTFS_SHA256='{'a' * 64}'\nCOMPRESSED_SHA256='{'b' * 64}'\nCOMPRESSED_BYTES='12'\n"
                         f"ROOTFS_CHUNK_SHA256='{'c' * 64} {'d' * 64}'\nROOT_COMPRESSION='zstd'\n")
        with self.assertRaises(ValueError):
            REPACK.update_ram_env("ROOTFS_SHA256='old'\n", receipt)

    def test_only_the_named_boot_module_row_changes(self):
        rows = "ram_boot.py\told\t1\t0755\nram_state.py\tkeep\t2\t0755\n"
        data = b"new module"
        self.assertEqual(REPACK.update_asset_rows(rows, "ram_boot.py", data),
                         f"ram_boot.py\t{hashlib.sha256(data).hexdigest()}\t{len(data)}\t0755\nram_state.py\tkeep\t2\t0755\n")

    def test_the_size_guard_is_the_loaders_own_limit(self):
        self.assertEqual(REPACK.INITRAMFS_LIMIT, 512 << 20)
        self.assertIn("initramfs_size > 512ULL * 1024 * 1024",
                      (ROOT / "diagnostics/ps4/fpkg-loader/dedicated-loader.patch").read_text())

    def test_tar_is_packed_with_its_exact_gpl_source(self):
        manifest = self.manifest()
        package = next(item for item in manifest["packages"] if item["name"] == "tar")
        self.assertEqual(package["license"], "GPL-3.0-or-later")
        self.assertRegex(package["source"]["sha256"], "^[0-9a-f]{64}$")
        self.assertTrue(package["source"]["url"].startswith("https://ftp.gnu.org/gnu/tar/"))
        self.assertIn({"package": "tar", "from": "usr/bin/tar", "to": "bin/tar"}, manifest["files"])

    def test_firefox_starts_straight_on_the_test_page(self):
        """Every run makes a new profile, so a first-run splash would show every time."""
        text = (ROOT / "rootfs/ps4-diagnostics-desktop/graphics-session").read_text()
        for setting in ('"browser.preonboarding.splashShown", true', '"browser.aboutwelcome.experimentsGate.enabled", false',
                        '"termsofuse.bypassNotification", true',
                        '"datareporting.policy.dataSubmissionPolicyBypassNotification", true'):
            self.assertIn("user_pref(" + setting + ");", text)

    def test_the_budget_matches_what_init_accepts(self):
        self.assertEqual(self.manifest()["maximum_archive_bytes"], 33554432)
        self.assertIn('[ "$bytes" -gt 33554432 ]', (ROOT / "rootfs/ps4-diagnostics/init-ram").read_text())

    def roots(self, base):
        tree = base / "pkg/usr/share/thing"
        tree.mkdir(parents=True)
        (tree / "b.txt").write_bytes(b"b")
        (tree / "a.txt").write_bytes(b"a")
        tool = base / "pkg/usr/bin/tool"
        tool.parent.mkdir(parents=True)
        tool.write_bytes(b"tool")
        tool.chmod(0o755)
        return {"pkg": base / "pkg"}

    def small(self):
        return {"maximum_archive_bytes": 1 << 20,
                "packages": [{"name": "pkg", "version": "1", "url": "https://example.invalid/pkg", "license": "MIT"}],
                "files": [{"package": "pkg", "from": "usr/bin/tool", "to": "bin/tool"}, {"package": "pkg", "from": "usr/share/thing", "to": "share/thing"}]}

    def test_the_archive_is_reproducible_owned_by_root_and_keeps_execute_bits(self):
        with tempfile.TemporaryDirectory(dir="/var/tmp") as temporary:
            roots = self.roots(Path(temporary))
            first, second = REPACK.tools_archive(self.small(), roots), REPACK.tools_archive(self.small(), roots)
        self.assertEqual(first, second)
        with tarfile.open(fileobj=io.BytesIO(first)) as archive:
            members = {member.name: member for member in archive.getmembers()}
        self.assertEqual(members["bin/tool"].mode, 0o755)
        self.assertEqual(members["share/thing/a.txt"].mode, 0o644)
        self.assertTrue(all((m.uid, m.gid, m.mtime) == (0, 0, 0) for m in members.values()))
        self.assertIn("licenses/NOTICE.txt", members)

    def test_too_large_a_pack_is_refused(self):
        with tempfile.TemporaryDirectory(dir="/var/tmp") as temporary:
            manifest = self.small()
            manifest["maximum_archive_bytes"] = 100
            with self.assertRaisesRegex(ValueError, "budget"):
                REPACK.tools_archive(manifest, self.roots(Path(temporary)))

    def test_cpio_members_round_trip_on_the_kernels_alignment(self):
        data = b"".join([REPACK.entry("a", 0o100644, b"hello", 1), REPACK.entry("dir/b", 0o100755, b"", 2),
                         REPACK.entry("TRAILER!!!", 0, b"", 3), b"\0" * 8])
        found, tail = REPACK.members(data)
        self.assertEqual([member["name"] for member in found], ["a", "dir/b", "TRAILER!!!"])
        self.assertEqual(data[found[0]["start"]:found[0]["start"] + found[0]["size"]], b"hello")
        self.assertEqual(found[0]["start"] % 4, 0)
        self.assertEqual(tail, b"\0" * 8)

    def test_verify_refuses_any_undeclared_difference(self):
        base = b"".join([REPACK.entry("a", 0o100644, b"one", 1), REPACK.entry("b", 0o100644, b"two", 2), REPACK.entry("TRAILER!!!", 0, b"", 3)])
        same_order = b"".join([REPACK.entry("a", 0o100644, b"ONE", 1), REPACK.entry("b", 0o100644, b"two", 2),
                               REPACK.entry(REPACK.TOOLS_MEMBER, 0o100644, b"tar", 4), REPACK.entry("TRAILER!!!", 0, b"", 3)])
        REPACK.verify(same_order, base, ["a"], [REPACK.TOOLS_MEMBER], b"tar")
        with self.assertRaisesRegex(ValueError, "unexpected difference"):
            REPACK.verify(same_order, base, [], [REPACK.TOOLS_MEMBER], b"tar")


if __name__ == "__main__":
    unittest.main()
