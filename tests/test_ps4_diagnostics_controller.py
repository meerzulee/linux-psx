"""Typed local control tests; all hardware and network-manager state is synthetic."""
import importlib.machinery
import importlib.util
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("psx_controller", str(ROOT / "diagnostics/ps4/controller.py"))
spec = importlib.util.spec_from_loader("psx_controller", loader)
APP = importlib.util.module_from_spec(spec)
loader.exec_module(APP)


class Network:
    def status(self):
        return {"interfaces": [], "wifi": {"available": False, "enabled": False, "access_points": []}}

    def ssh_status(self):
        return {"enabled": False, "port": 22, "login": "root"}

    def wifi_scan(self):
        return {"status": "INCONCLUSIVE", "access_points": []}

    def wifi_connect(self, ssid, password):
        return {"status": "PASS", "message": "Connected"}

    def ethernet_connect(self):
        return {"status": "PASS", "message": "Connected"}

    def ethernet_disconnect(self):
        return {"ok": True, "interface": "psx0", "state": "disconnected"}

    def wifi_disconnect(self):
        return {"ok": True, "interface": "wlan0", "state": "disconnected"}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/var/tmp", prefix="psxc-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = self.root / "fixture"
        release = "7.2.11-psx-ps4-baikal-native-dev3"
        for name, data in {"proc/sys/kernel/osrelease": release,
                           "proc/sys/kernel/random/boot_id": "fixture-boot",
                           "proc/cpuinfo": "processor : 0\nmodel name : Synthetic CPU\n",
                           "proc/meminfo": "MemTotal: 4096000 kB\nMemAvailable: 2048000 kB\n",
                           "proc/uptime": "100.0 50.0",
                           "etc/psx-diagnostics/boot-set.json": json.dumps({"kernel_release": release})}.items():
            path = self.fixture / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
        self.controller = APP.Controller(self.root / "results", self.fixture, network=Network())

    def test_state_uses_fixture_hardware_and_never_invokes_real_tools(self):
        with mock.patch.object(APP.subprocess, "run", side_effect=AssertionError("live command")):
            state = self.controller.request("state")
        self.assertEqual(state["system"]["cpu"]["logical"], 1)
        self.assertEqual(state["system"]["cpu"]["model"], "Synthetic CPU")
        self.assertEqual(state["system"]["memory"]["available_bytes"], 2048000 * 1024)
        self.assertFalse(state["hardware_acceptance"])

    def test_unknown_actions_and_parameters_are_rejected(self):
        for action, params in (("shell", {"command": "touch /oops"}), ("quick", {"command": "oops"}),
                               ("graphics.launch", {"app": ";reboot"}), ("test.run", {"test_id": "unknown"})):
            with self.subTest(action=action), self.assertRaises((ValueError, KeyError)):
                self.controller.request(action, params)

    def test_wrong_kernel_cannot_connect_network_or_launch_any_action(self):
        (self.fixture / "proc/sys/kernel/osrelease").write_text("another-kernel")
        with mock.patch.object(self.controller.network, "ethernet_connect") as connect:
            with self.assertRaisesRegex(ValueError, "matching"):
                self.controller.request("network.ethernet.connect")
            connect.assert_not_called()

    def test_job_is_saved_before_action_and_only_one_action_runs(self):
        gate = threading.Event()
        observed = []
        def worker(*_):
            files = list(self.controller.jobs.directory.glob("*.json"))
            observed.append(json.loads(files[0].read_text())["state"])
            gate.wait(2)
            return {"status": "PASS", "message": "Synthetic"}
        with mock.patch.object(self.controller.runner, "quick", side_effect=worker):
            job = self.controller.request("quick")
            with self.assertRaises(APP.BusyError):
                self.controller.request("network.scan")
            gate.set()
            self.wait(job["id"])
        self.assertEqual(observed, ["running"])
        self.assertEqual(self.controller.jobs.get(job["id"])["result"]["status"], "PASS")

    def wait(self, identifier):
        deadline = time.monotonic() + 3
        while self.controller.jobs.get(identifier)["state"] == "running" and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertNotEqual(self.controller.jobs.get(identifier)["state"], "running")

    def test_wifi_password_is_not_saved_to_the_job_report(self):
        job = self.controller.request("network.wifi.connect", {"ssid": "Fixture", "password": "secret fixture credential"})
        self.wait(job["id"])
        self.assertNotIn("secret fixture credential", (self.controller.jobs.directory / (job["id"] + ".json")).read_text())

    def test_disconnect_actions_save_jobs_and_accept_no_interface_override(self):
        for kind, interface in (("ethernet", "psx0"), ("wifi", "wlan0")):
            with self.subTest(kind=kind):
                action = "network." + kind + ".disconnect"
                with self.assertRaises(ValueError): self.controller.request(action, {"interface": "ap0"})
                job = self.controller.request(action)
                self.wait(job["id"])
                saved = json.loads((self.controller.jobs.directory / (job["id"] + ".json")).read_text())
                self.assertEqual(saved["state"], "completed")
                self.assertEqual(saved["result"]["interface"], interface)
                self.assertFalse(saved["hardware_acceptance"])

    def test_disconnect_actions_obey_ready_guard_before_job_submission(self):
        for kind in ("ethernet", "wifi"):
            with self.subTest(kind=kind), mock.patch.object(self.controller, "ready", side_effect=ValueError("stop required")), \
                    mock.patch.object(self.controller.network, kind + "_disconnect") as disconnect:
                with self.assertRaisesRegex(ValueError, "stop required"):
                    self.controller.request("network." + kind + ".disconnect")
                disconnect.assert_not_called()
        self.assertEqual(self.controller.jobs.list(), [])

    def test_browser_completion_requires_a_matching_durable_start_marker(self):
        # This test isolates browser markers; prerequisite coverage has its own suite.
        with mock.patch.object(self.controller, 'core_prerequisites', return_value=None):
            start = self.controller.browser_start({"test_id": "browser.canvas2d"})
        with self.assertRaisesRegex(ValueError, "earlier action"):
            self.controller.request("quick")
        result = self.controller.browser_result({"schema_version": 1, "test_id": "browser.canvas2d",
                   "status": "PASS", "run_id": start["run_id"], "measurements": {"pixels": 4}}, "Synthetic browser")
        self.assertFalse(result["hardware_acceptance"])
        self.assertIn("remote clients", result["evidence_scope"])
        self.assertEqual(result["phase"], "completed")
        with self.assertRaises(ValueError):
            self.controller.browser_result({"schema_version": 1, "test_id": "browser.canvas2d",
                   "status": "PASS", "run_id": "../../elsewhere"}, "Synthetic")

    def test_oversized_job_details_do_not_overflow_the_ipc_message(self):
        job = self.controller.jobs.submit("Synthetic", lambda: {"status": "PASS", "message": "small", "large": "x" * (2 * APP.LIMIT)})
        self.wait(job["id"])
        state = self.controller.state()
        self.assertLess(len(json.dumps(state).encode()), APP.LIMIT)
        self.assertLess(len(json.dumps(self.controller.jobs.get(job["id"])).encode()), APP.LIMIT)

    def test_state_reads_cached_network_while_the_monitor_is_active(self):
        self.controller.sample_network()
        self.controller._monitor_started = True
        with mock.patch.object(self.controller.network, "status", side_effect=AssertionError("blocking network query")), \
                mock.patch.object(self.controller.network, "ssh_status", side_effect=AssertionError("blocking SSH query")):
            state = self.controller.request("state")
        self.assertEqual(state["network"]["interfaces"], [])
        self.assertFalse(state["ssh"]["enabled"])

    def test_compact_reports_preserve_boot_and_retry_evidence(self):
        report = {"test_id": "gpu.opengl.render", "status": "FAIL",
                  "identity": {"boot_id": "fixture-boot"}, "commands": [{"returncode": 1}],
                  "measurements": {"large": "x" * APP.LIMIT}}
        compact = APP.summary(report)
        self.assertTrue(compact["retry_required"])
        self.assertEqual(compact["boot_id"], "fixture-boot")
        self.assertNotIn("measurements", compact)

    def reviewed_setup_fixture(self, **changes):
        identifier = "fixture-vulkan-setup"
        directory = self.controller.results_dir / identifier
        directory.mkdir(parents=True, exist_ok=True)
        record = {"run_id": identifier, "test_id": "gpu.vulkan.render", "phase": "completed",
                  "status": "INCONCLUSIVE", "stop_required": True, "identity": {"boot_id": "fixture-boot"},
                  "commands": [{"argv": ["/usr/lib/psx-diagnostics/check-vulkan"], "returncode": 1,
                                "signal": None, "timed_out": False, "interrupted": False, "output_limited": False,
                                "stdout": "command-01.stdout", "stderr": "command-01.stderr"}]}
        record.update(changes)
        raw = (json.dumps(record) + "\n").encode()
        (directory / "result.json").write_bytes(raw)
        evidence = {"command-01.stdout": b"CONFIG mode=draw RADV_DEBUG=\nSTAGE create_instance result=-9\n",
                    "command-01.stderr": b"STOP create_instance result=-9; no retry or GPU reset\n",
                    "dependencies-before.json": b'{"scope":"synthetic missing dependencies"}',
                    "dependencies-after.json": b'{"scope":"synthetic repaired dependencies"}'}
        for name, value in evidence.items():
            (directory / name).write_bytes(value)
        libraries = {}
        for name in ("libdisplay-info.so.3", "libxcb-keysyms.so.1"):
            path = self.fixture / "run/psx-frontend" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("synthetic library " + name).encode())
            libraries[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        review = {"schema_version": 1, "kind": "vulkan-pre-device-setup", "decision": "allow-further-actions",
                  "hardware_acceptance": False, "run_id": identifier, "boot_id": "fixture-boot",
                  "original_report_sha256": hashlib.sha256(raw).hexdigest(),
                  "evidence_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in evidence.items()},
                  "runtime_library_sha256": libraries}
        (directory / "setup-review.json").write_text(json.dumps(review))
        return directory, record, review

    def test_specific_review_keeps_original_report_and_releases_only_setup_stop(self):
        directory, record, _ = self.reviewed_setup_fixture()
        before = (directory / "result.json").read_bytes()
        self.assertTrue(self.controller.reviewed_setup_failure(record))
        self.controller.ready()
        shown = next(r for r in self.controller.results() if r["run_id"] == record["run_id"])
        self.assertTrue(shown["stop_required"])
        self.assertTrue(shown["setup_failure_reviewed"])
        self.assertEqual((directory / "result.json").read_bytes(), before)

    def test_setup_review_cannot_release_timeout_signal_running_or_other_gpu_failure(self):
        for key, value in (("timed_out", True), ("interrupted", True), ("output_limited", True),
                           ("signal", 9), ("returncode", 124)):
            with self.subTest(key=key):
                directory, record, review = self.reviewed_setup_fixture()
                record["commands"][0][key] = value
                raw = json.dumps(record).encode()
                (directory / "result.json").write_bytes(raw)
                review["original_report_sha256"] = hashlib.sha256(raw).hexdigest()
                (directory / "setup-review.json").write_text(json.dumps(review))
                self.assertFalse(self.controller.reviewed_setup_failure(record))
        for changes in ({"phase": "running"}, {"test_id": "gpu.opengl.render"}, {"status": "PASS"}):
            with self.subTest(changes=changes):
                _, record, _ = self.reviewed_setup_fixture(**changes)
                self.assertFalse(self.controller.reviewed_setup_failure(record))

    def test_setup_review_rejects_drifted_report_evidence_libraries_or_wrong_boot(self):
        for change in ("report", "evidence", "library", "boot", "run"):
            with self.subTest(change=change):
                directory, record, review = self.reviewed_setup_fixture()
                if change == "report":
                    (directory / "result.json").write_text("{}")
                elif change == "evidence":
                    (directory / "command-01.stdout").write_text("STAGE queue_submit result=2\n")
                elif change == "library":
                    (self.fixture / "run/psx-frontend/libdisplay-info.so.3").write_bytes(b"drifted")
                else:
                    review["boot_id" if change == "boot" else "run_id"] = "wrong"
                    (directory / "setup-review.json").write_text(json.dumps(review))
                self.assertFalse(self.controller.reviewed_setup_failure(record))

    def test_review_cannot_release_a_separate_stopped_or_running_incident(self):
        self.reviewed_setup_fixture()
        for phase in ("completed", "running"):
            directory = self.controller.results_dir / "other-gpu-incident"
            directory.mkdir(exist_ok=True)
            (directory / "result.json").write_text(json.dumps({"run_id": directory.name,
                "test_id": "gpu.opengl.render", "phase": phase, "status": "INCONCLUSIVE",
                "stop_required": True, "identity": {"boot_id": "fixture-boot"}}))
            with self.assertRaisesRegex(ValueError, "earlier action"):
                self.controller.ready()

    def test_malformed_review_shapes_keep_the_stop_without_breaking_status_reads(self):
        for field in (None, "evidence_sha256", "runtime_library_sha256"):
            for value in (None, [], ["libdisplay-info.so.3", "libxcb-keysyms.so.1"], "invalid", 42):
                with self.subTest(field=field, value=value):
                    directory, record, review = self.reviewed_setup_fixture()
                    if field:
                        review[field] = value
                    else:
                        review = value
                    (directory / "setup-review.json").write_text(json.dumps(review))
                    self.assertFalse(self.controller.reviewed_setup_failure(record))
                    self.controller.state()
                    with self.assertRaisesRegex(ValueError, "earlier action"):
                        self.controller.ready()

    def test_real_local_ipc_uses_only_a_named_action(self):
        path = self.root / "control.sock"
        server = APP.IPCServer(str(path), APP.IPCHandler)
        server.controller = self.controller
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.socket(socket.AF_UNIX) as client:
                client.connect(str(path))
                client.sendall(b'{"id":1,"action":"state","params":{}}\n')
                data = client.makefile("rb").readline(APP.LIMIT)
            response = json.loads(data)
            self.assertTrue(response["ok"])
            self.assertEqual(response["data"]["system"]["cpu"]["logical"], 1)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
