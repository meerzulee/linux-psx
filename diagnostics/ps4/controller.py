#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Small local diagnostic controller: typed Unix IPC, finite jobs and browser lab."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import hmac
import json
import mimetypes
import math
import os
from pathlib import Path
import re
import secrets
import socketserver
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import uuid

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import runner as diagnostics
if Path('/run/psx-frontend/preferences.py').is_file():
    sys.path.insert(0, '/run/psx-frontend')
from preferences import DisplayPreferences

LIMIT = 1024 * 1024
BROWSER_STEPS = ('browser.capabilities', 'browser.canvas2d', 'browser.webgl', 'browser.animation', 'browser.video')
EXTRA_TESTS = (
    {"id": "gpu.opengl.render", "title": "OpenGL reference pixels", "area": "Graphics", "kind": "functional", "timeout_seconds": 20,
     "description": "Render a small known image with the pinned Mesa stack and verify downloaded pixels."},
    {"id": "gpu.vulkan.render", "title": "Vulkan reference pixels", "area": "Graphics", "kind": "functional", "timeout_seconds": 30,
     "description": "Run the existing bounded Vulkan draw oracle and verify its pixels."},
)


def decode(raw):
    return json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON value")))


def safe_error(error):
    # Subprocess exceptions may contain private credential arguments; never echo them.
    if isinstance(error, subprocess.SubprocessError):
        return "The selected tool failed; inspect its private diagnostic report."
    return str(error)[:1000]


class BusyError(ValueError):
    pass


def summary(record):
    keys = ("id", "run_id", "test_id", "title", "state", "phase", "status", "message",
            "started_at", "completed_at", "observation_required", "result_directory", "stop_required",
            "setup_failure_reviewed", "recovery_message")
    value = {key: record[key] for key in keys if key in record}
    if record.get("identity", {}).get("boot_id"):
        value["boot_id"] = record["identity"]["boot_id"]
    if "result" in record:
        value["result"] = summary(record["result"]) if isinstance(record["result"], dict) else None
    if record.get("error"):
        value["error"] = str(record["error"])[:1000]
    commands = record.get("commands", [])
    failed_command = any(command.get("returncode") != 0 or command.get("timed_out") or
                         command.get("interrupted") or command.get("output_limited") for command in commands)
    value["retry_required"] = bool(record.get("status") == "FAIL" or (
        record.get("status") == "INCONCLUSIVE" and commands and not record.get("observation_required")
        and (failed_command or record.get("test_id") in ("cpu.compute", "memory.pattern", "ethernet.connect", "ethernet.https"))))
    return value


class Jobs:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.guard = threading.Lock()
        self.active = None
        self.records = {}

    def submit(self, title, function):
        with self.guard:
            if self.active:
                raise BusyError("Another action is running; wait for its result.")
            identifier = uuid.uuid4().hex
            record = {"id": identifier, "title": title, "state": "running", "started_at": diagnostics.timestamp(),
                      "result": None, "error": None, "output": [], "hardware_acceptance": False}
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            diagnostics.atomic_json(self.directory / (identifier + ".json"), record)
            self.active = identifier
            self.records[identifier] = record
            while len(self.records) > 32:
                del self.records[next(iter(self.records))]
        def work():
            try:
                value = function()
                changes = {"state": "completed", "result": value}
            except Exception as error:
                changes = {"state": "failed", "error": safe_error(error)}
            with self.guard:
                record.update(changes, completed_at=diagnostics.timestamp())
                try:
                    diagnostics.atomic_json(self.directory / (identifier + ".json"), record)
                except OSError:
                    record.update(state="failed", error="The action finished but its report could not be saved.")
                finally:
                    self.active = None
        threading.Thread(target=work, daemon=True, name="diagnostic-job").start()
        return {"id": identifier, "job_id": identifier, "state": "running", "title": title}

    def event(self, value):
        with self.guard:
            if self.active:
                record = self.records[self.active]
                record["output"].append(diagnostics.clean_terminal(json.dumps(value))[:1500])
                record["output"] = record["output"][-40:]

    def get(self, identifier):
        with self.guard:
            if identifier not in self.records:
                raise ValueError("Unknown job")
            value = json.loads(json.dumps(self.records[identifier]))
            if len(json.dumps(value).encode()) > 256 * 1024:
                result = value.get("result")
                value["result"] = summary(result) if isinstance(result, dict) else result
                value["details_in_saved_report"] = True
            return value

    def list(self):
        with self.guard:
            return [summary(record) for record in list(self.records.values())[-12:]]


class Controller:
    def __init__(self, results_dir, root_prefix="/", manifest=diagnostics.MANIFEST_PATH, network=None, executor=None):
        self.results_dir = Path(results_dir)
        self.jobs = Jobs(self.results_dir / "controller-jobs")
        self.runner = diagnostics.Runner(results_dir, root_prefix, manifest, executor=executor, callback=self.jobs.event)
        if network is None:
            from network import Network
            network = Network(root_prefix=root_prefix)
        self.network = network
        self.csrf = secrets.token_urlsafe(32)
        self.action_guard = threading.RLock()
        self.display = DisplayPreferences(root_prefix)
        self._ssh_access = None
        self._ssh_setup_error = ''
        self._browser_intent_guard = threading.RLock()
        self._browser_auto_intent = None
        self._browser_batch = None
        self._inventory = None
        self._inventory_time = 0
        self._cpu_sample = None
        self._network_snapshot = {"interfaces": [], "wifi": {"available": False, "enabled": False, "access_points": []}, "loading": True}
        self._ssh_snapshot = {"enabled": False, "port": 22, "login": "root"}
        self._monitor_started = False

    def sample_network(self):
        try:
            self._network_snapshot = self.network.status()
        except Exception as error:
            self._network_snapshot = {"interfaces": [], "wifi": {"available": False, "enabled": False, "access_points": []}, "error": safe_error(error)}
        try:
            self._ssh_snapshot = self.network.ssh_status()
        except Exception as error:
            self._ssh_snapshot = {"enabled": False, "port": 22, "login": "root", "error": safe_error(error)}

    def ssh_access(self):
        if self._ssh_access is None:
            path = Path('/run/psx-frontend/ssh_access.py')
            if not path.is_file():
                path = HERE / 'ssh_access.py'
            if not path.is_file():
                raise ValueError('SSH setup is not installed in this diagnostic image')
            spec = importlib.util.spec_from_file_location('psx_private_ssh_access', path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            self._ssh_access = module.SSHAccess(root_prefix=str(self.runner.fs.root))
        return self._ssh_access

    def initialize_ssh(self):
        if self.runner.fs.fixture:
            return
        try:
            self.ssh_access().initialize()
        except (OSError, ValueError, ImportError, subprocess.SubprocessError):
            self._ssh_setup_error = 'SSH setup is unavailable; existing key access remains available.'
            print(self._ssh_setup_error, file=sys.stderr)

    def start_monitor(self):
        if self._monitor_started:
            return
        self._monitor_started = True
        def monitor():
            while True:
                self.sample_network()
                time.sleep(3)
        threading.Thread(target=monitor, daemon=True, name="network-observer").start()

    def cpu_usage(self):
        try:
            fields = (self.runner.fs.text("/proc/stat") or "").splitlines()[0].split()
            numbers = list(map(int, fields[1:9]))
            total, idle = sum(numbers), numbers[3] + numbers[4]
            previous = self._cpu_sample
            self._cpu_sample = (total, idle)
            if previous and total > previous[0]:
                return round(max(0, min(100, 100 * (1 - (idle - previous[1]) / (total - previous[0])))), 1)
        except (IndexError, ValueError):
            pass
        return None

    def ready(self):
        identity = self.runner.identity()
        if not identity["release_matches"] or not identity["boot_id"]:
            raise ValueError("This action requires the matching PS4 diagnostic kernel and boot manifest.")
        batch = self._browser_batch
        if (batch and batch['record'].get('stop_required')
                and batch['record'].get('identity', {}).get('boot_id') == identity['boot_id']):
            raise ValueError('An earlier automatic browser batch needs recovery; inspect its summary first.')
        if any((r.get("stop_required") and not r.get("setup_failure_reviewed")) or
               (r.get("phase") == "running" and r.get("test_id") not in ("cpu.compute", "memory.pattern")) for r in self.results()
               if r.get("identity", {}).get("boot_id") == identity["boot_id"]):
            raise ValueError("An earlier action needs recovery; inspect its saved report first.")
        return identity

    def reviewed_setup_failure(self, record):
        """Resolve only an explicitly reviewed, pre-device Vulkan loader failure.

        The original report and stop flag remain immutable. No RPC can create a
        review; the operator's root engineering session must append the receipt
        and its evidence. Timeouts, signals and later GPU stages never qualify.
        """
        if (record.get("test_id") != "gpu.vulkan.render" or record.get("phase") != "completed"
                or record.get("status") != "INCONCLUSIVE" or record.get("stop_required") is not True):
            return False
        identity = record.get("identity")
        if not isinstance(identity, dict) or not identity.get("boot_id"):
            return False
        identifier = record.get("run_id", "")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", identifier):
            return False
        commands = record.get("commands", [])
        if not isinstance(commands, list) or len(commands) != 1 or not isinstance(commands[0], dict):
            return False
        command = commands[0]
        if (command.get("returncode") != 1 or command.get("signal") is not None
                or any(command.get(key) is not False for key in ("timed_out", "interrupted", "output_limited"))
                or command.get("argv") != ["/usr/lib/psx-diagnostics/check-vulkan"]
                or command.get("stdout") != "command-01.stdout" or command.get("stderr") != "command-01.stderr"):
            return False
        directory = self.results_dir / identifier
        if directory.is_symlink() or not directory.is_dir():
            return False
        def read(name, limit=1024 * 1024):
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
                raise ValueError("Invalid setup-review evidence")
            return path.read_bytes()
        def digest(data):
            return hashlib.sha256(data).hexdigest()
        try:
            review_path = directory / "setup-review.json"
            if not self.runner.fs.fixture:
                info = review_path.lstat()
                if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
                    return False
            review = json.loads(read("setup-review.json", 65536))
            if not isinstance(review, dict):
                return False
            if (review.get("schema_version") != 1 or review.get("kind") != "vulkan-pre-device-setup"
                    or review.get("decision") != "allow-further-actions" or review.get("hardware_acceptance") is not False
                    or review.get("run_id") != identifier
                    or review.get("boot_id") != identity["boot_id"]
                    or review.get("original_report_sha256") != digest(read("result.json"))):
                return False
            if (read("command-01.stdout") != b"CONFIG mode=draw RADV_DEBUG=\nSTAGE create_instance result=-9\n"
                    or read("command-01.stderr") != b"STOP create_instance result=-9; no retry or GPU reset\n"):
                return False
            evidence = review.get("evidence_sha256", {})
            names = {"command-01.stdout", "command-01.stderr", "dependencies-before.json", "dependencies-after.json"}
            if not isinstance(evidence, dict) or set(evidence) != names or any(digest(read(name)) != evidence[name] for name in names):
                return False
            libraries = review.get("runtime_library_sha256", {})
            if not isinstance(libraries, dict) or set(libraries) != {"libdisplay-info.so.3", "libxcb-keysyms.so.1"}:
                return False
            for name, pin in libraries.items():
                data = self.runner.fs.data("/run/psx-frontend/" + name, 2 * 1024 * 1024)
                if data is None or digest(data) != pin:
                    return False
        except (OSError, ValueError, KeyError, TypeError):
            return False
        return True

    def results(self):
        records = {record["run_id"]: record for record in self.runner.results()}
        extra = {test["id"] for test in EXTRA_TESTS} | {
            "browser.canvas2d", "browser.webgl", "browser.animation", "browser.capabilities", "browser.video", "browser.batch"}
        if self.results_dir.exists():
            for path in sorted(self.results_dir.glob("*/result.json"), reverse=True)[:200]:
                try:
                    record = diagnostics.read_json(path)
                    if isinstance(record, dict) and (record.get("test_id") in extra or record.get("prerequisite_blocked") is True):
                        records[record["run_id"]] = record
                except (OSError, ValueError, KeyError):
                    continue
        fallback = self.runner.fs.local('/run/psx-diagnostics/browser-batches')
        for path in sorted(fallback.glob('*.json'), reverse=True)[:20]:
            try:
                record = diagnostics.read_json(path)
                if isinstance(record, dict) and record.get('test_id') == 'browser.batch':
                    records[record['run_id']] = record
            except (OSError, ValueError, KeyError):
                continue
        for record in records.values():
            record.pop("setup_failure_reviewed", None)
            record.pop("recovery_message", None)
            if self.reviewed_setup_failure(record):
                record.update(setup_failure_reviewed=True,
                              recovery_message="Setup failure reviewed; libraries repaired; Vulkan rendering still untested.")
        return sorted(records.values(), key=lambda record: str(record.get("started_at", record.get("completed_at", ""))), reverse=True)

    def state(self):
        now = time.monotonic()
        if self._inventory is None or now - self._inventory_time > 15:
            self._inventory = self.runner.inventory()
            self._inventory_time = now
        inventory = self._inventory
        cpu = inventory.get("cpu", {})
        memory = self.runner.memory_info()
        try:
            uptime = float((self.runner.fs.text("/proc/uptime") or "0").split()[0])
        except (ValueError, IndexError):
            uptime = 0
        if self.runner.fs.fixture and not self._monitor_started:
            self.sample_network()
        network, ssh = self._network_snapshot, self._ssh_snapshot
        results = self.results()[:30]
        latest = {}
        identity = self.runner.identity()
        for result in results:
            if result.get("identity", {}).get("boot_id") == identity["boot_id"]:
                latest.setdefault(result["test_id"], result)
        tests = [dict(test, latest_status=latest.get(test["id"], {}).get("status", "NOT_RUN"))
                 for test in (*self.runner.catalogue["tests"], *EXTRA_TESTS)]
        return {"schema_version": 1, "system": {
                    "hostname": self.runner.fs.text("/proc/sys/kernel/hostname") or "psx-diagnostics",
                    "kernel_release": identity["kernel_release"], "uptime_seconds": uptime,
                    "cpu": {"model": ", ".join(cpu.get("models", [])), "logical": cpu.get("listed_processors", 0), "usage_percent": self.cpu_usage()},
                    "memory": {"total_bytes": memory.get("MemTotal_kib", 0) * 1024,
                               "available_bytes": memory.get("MemAvailable_kib", 0) * 1024}},
                "inventory": inventory, "network": network, "ssh": ssh, "identity": identity,
                "settings": self.display.state(),
                "tests": tests, "results": [summary(record) for record in results], "jobs": self.jobs.list(), "hardware_acceptance": False}

    def core_prerequisites(self, selected_id, allow_run=True, cpu_only=False):
        """Core checks are a functional baseline, never hardware qualification.

        Run missing checks only within an already-saved selected job. A browser
        start request may inspect receipts but must not start core work itself.
        """
        identity = self.runner.identity()
        pins = ("boot_id", "kernel_release", "manifest_sha256", "runner_sha256", "catalogue_sha256")
        required = (("cpu.compute", {"seconds": 2}), ("memory.pattern", {"memory_mib": 32}))
        if cpu_only: required = required[:1]
        def blocked(message, evidence=None):
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
            with self.runner.lock():
                directory = self.results_dir / run_id; directory.mkdir(mode=0o700)
                record = {"run_id": run_id, "test_id": selected_id, "identity": identity,
                          "phase": "completed", "status": "BLOCKED", "prerequisite_blocked": True,
                          "started_at": diagnostics.timestamp(), "completed_at": diagnostics.timestamp(),
                          "message": message, "evidence": evidence or {}, "commands": [],
                          "hardware_acceptance": False, "result_directory": str(directory)}
                diagnostics.atomic_json(directory / "result.json", record)
            return record
        if (not identity.get("release_matches") or not identity.get("manifest_available")
                or any(not identity.get(key) for key in pins)):
            return blocked("Advanced tests need a verified diagnostic boot identity before CPU and RAM checks.")
        # Runner's ordinary report view is capped for UI responsiveness. Inspect
        # complete core history here so a busy session cannot evict a failure or
        # make a missing UI row silently authorize another automatic attempt.
        history = []
        for path in self.results_dir.glob("*/result.json"):
            try:
                record = diagnostics.read_json(path)
                if (isinstance(record, dict) and not record.get("prerequisite_blocked")
                        and record.get("test_id") in {item[0] for item in required}):
                    if not isinstance(record.get("identity"), dict):
                        return blocked("A core report has no valid identity; inspect Reports before advanced tests.")
                    if record["identity"].get("boot_id") == identity["boot_id"]: history.append(record)
            except (OSError, ValueError, TypeError):
                return blocked("Saved core history could not be verified; inspect Reports before advanced tests.")
        history.sort(key=lambda record: str(record.get("started_at", "")), reverse=True)
        for test_id, parameters in required:
            current = self.runner.identity()
            if any(current.get(key) != identity[key] for key in pins):
                return blocked("Boot or diagnostic source identity changed; no advanced test was started.")
            prior = [record for record in history if record["test_id"] == test_id]
            latest = prior[0] if prior else None
            if latest and (latest.get("phase") != "completed" or latest.get("status") != "PASS" or latest.get("stop_required")):
                return blocked("Core check needs attention: " + test_id + ". Review its report, then run the core check manually with changed evidence before advanced tests.",
                               {"prerequisite": test_id, "run_id": latest.get("run_id"), "status": latest.get("status")})
            matching = next((record for record in prior if all(record.get("identity", {}).get(key) == identity[key] for key in pins)), None)
            if matching is None:
                if not allow_run:
                    return blocked("Complete the CPU and RAM core checks before starting browser tests.", {"prerequisite": test_id})
                if not self.jobs.active:
                    return blocked("Select this advanced test from diagnostics so core checks run inside its saved job.")
                self.jobs.event({"stage": "core_prerequisite", "test_id": test_id,
                                 "message": "Checking CPU and RAM before advanced tests."})
                matching = self.runner.run(test_id, parameters)
            if not isinstance(matching, dict):
                return blocked("Core check returned an invalid receipt; no advanced test was started.")
            evidence = matching.get("evidence", {})
            commands = matching.get("commands", [])
            evidence = evidence if isinstance(evidence, dict) else {}
            commands = commands if isinstance(commands, list) else []
            saved_parameters = matching.get("parameters")
            saved_parameters = saved_parameters if isinstance(saved_parameters, dict) else {}
            try:
                valid_parameters = self.runner.validate(test_id, matching.get("parameters", {})) == matching.get("parameters")
            except (ValueError, TypeError): valid_parameters = False
            oracle = (evidence.get("digest") == diagnostics.CPU_ANSWER and type(evidence.get("iterations")) is int
                      and evidence["iterations"] > 0) if test_id == "cpu.compute" else (
                      evidence.get("verified_mib") == saved_parameters.get("memory_mib")
                      and evidence.get("patterns") == [0, 85, 170, 255])
            valid = (matching.get("status") == "PASS" and matching.get("phase") == "completed"
                     and matching.get("hardware_acceptance") is False and not matching.get("stop_required")
                     and matching.get("test_version") == self.runner.tests[test_id]["version"]
                     and all(matching.get("identity", {}).get(key) == identity[key] for key in pins)
                     and valid_parameters and oracle and len(commands) == 1
                     and all(isinstance(command, dict) and command.get("returncode") == 0 and command.get("signal") is None
                             and all(command.get(flag) is False for flag in ("timed_out", "interrupted", "output_limited")) for command in commands))
            if not valid:
                return blocked("Core check did not establish a valid PASS: " + test_id + ". Advanced tests were skipped; inspect Reports and use an explicit core retry after recording changed evidence.",
                               {"prerequisite": test_id, "run_id": matching.get("run_id"), "status": matching.get("status")})
        if any(self.runner.identity().get(key) != identity[key] for key in pins):
            return blocked("Boot or diagnostic source identity changed; no advanced test was started.")
        return None

    def catalogue_test(self, test_id, parameters, retry_reason=""):
        if test_id in ("memory.pattern", "audio.44100", "ethernet.https") or test_id.startswith(("gpu.", "wayland.")):
            blocked = self.core_prerequisites(test_id, cpu_only=test_id == "memory.pattern")
            if blocked: return blocked
        return self.runner.run(test_id, parameters, retry_reason)

    def external_test(self, test_id, retry_reason=""):
        identity = self.ready()
        blocked = self.core_prerequisites(test_id)
        if blocked: return blocked
        previous = [r for r in self.results() if r.get("test_id") == test_id
                    and r.get("identity", {}).get("boot_id") == identity["boot_id"] and not r.get("prerequisite_blocked")]
        if previous and previous[0].get("status") != "PASS" and not retry_reason.strip():
            raise ValueError("Describe the changed evidence before retrying this test.")
        name = {"gpu.opengl.render": "check-opengl", "gpu.vulkan.render": "check-vulkan"}[test_id]
        tool = self.runner.fs.local("/usr/lib/psx-diagnostics/" + name)
        if not tool.is_file():
            return {"status": "NOT_RUN", "message": "This graphics test tool is not installed.", "hardware_acceptance": False}
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
        directory = self.results_dir / run_id
        with self.runner.lock():
            directory.mkdir(mode=0o700)
            record = {"run_id": run_id, "test_id": test_id, "phase": "running", "status": "INCONCLUSIVE",
                      "identity": identity, "started_at": diagnostics.timestamp(), "hardware_acceptance": False,
                      "message": "Graphics test started", "result_directory": str(directory)}
            diagnostics.atomic_json(directory / "result.json", record)
            self.runner.log = diagnostics.Evidence(directory, self.jobs.event)
            self.runner.commands = []
            response = self.runner.command([str(tool)], 30 if "vulkan" in test_id else 20)
            passed = response.returncode == 0 and not (response.timed_out or response.interrupted or response.output_limited)
            record.update(phase="completed", completed_at=diagnostics.timestamp(), commands=self.runner.commands,
                          status="PASS" if passed else "INCONCLUSIVE", stop_required=not passed,
                          message="Reference pixels matched; inspect the renderer in the report." if passed else "Graphics test stopped; inspect the report before further GPU actions.")
            diagnostics.atomic_json(directory / "result.json", record)
            return record

    def launch_graphics(self, app):
        if app not in ("browser", "chromium", "browser-manual", "chromium-manual", "protondb", "opengl", "vulkan", "diagnostics"):
            raise ValueError("Unknown graphical application")
        selected = app
        app = {'browser-manual': 'browser', 'chromium-manual': 'chromium'}.get(app, app)
        automatic = app in ('browser', 'chromium') and not selected.endswith('-manual')
        if app in ('browser', 'chromium', 'protondb'):
            with self._browser_intent_guard:
                if self._browser_batch and not self._browser_batch['finished']:
                    raise BusyError('The automatic browser batch has not finished.')
                self._browser_auto_intent = None
        self.ready()
        if app != 'diagnostics':
            blocked = self.core_prerequisites('graphics.window.' + app)
            if blocked: return blocked
        helper = self.runner.fs.local("/usr/lib/psx-diagnostics/launch-graphics")
        if self.runner.fs.fixture:
            raise ValueError("Fixture roots never launch display sessions")
        if app == 'chromium':
            prepare = Path('/run/psx-frontend/prepare-chromium-provider')
            if not prepare.is_file():
                prepare = Path('/usr/lib/psx-diagnostics/prepare-chromium-provider')
            subprocess.run(['/usr/bin/python3', str(prepare)], check=True, timeout=60, capture_output=True)
        nonce = secrets.token_urlsafe(24) if automatic else None
        if automatic:
            batch_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
            record = {'schema_version': 1, 'run_id': batch_id, 'test_id': 'browser.batch',
                      'title': ('Firefox' if app == 'browser' else 'Chromium') + ' · Automatic baseline',
                      'phase': 'running', 'status': 'INCONCLUSIVE', 'started_at': diagnostics.timestamp(),
                      'identity': self.runner.identity(), 'hardware_acceptance': False, 'run_ids': [],
                      'automatic': True, 'browser_app': app,
                      'message': 'Automatic browser batch launched; completion is pending.'}
            directory = self.results_dir / batch_id
            directory.mkdir(mode=0o700, parents=True)
            diagnostics.atomic_json(directory / 'batch.json', record)
            with self._browser_intent_guard:
                self._browser_auto_intent = {'nonce': nonce, 'family': 'Firefox' if app == 'browser' else 'Chromium',
                                             'expires': time.monotonic() + 60, 'armed': False, 'batch_id': batch_id}
                self._browser_batch = {'nonce': nonce, 'family': self._browser_auto_intent['family'],
                                       'record': record, 'service': None, 'run_ids': [], 'claimed': False,
                                       'deadline': time.monotonic() + 90, 'finished': False}
        try:
            launched = subprocess.run([str(helper), *(['--automatic'] if automatic else []), app], check=True, timeout=15, capture_output=True)
            if automatic:
                if not isinstance(launched.stdout, (str, bytes)) or len(launched.stdout) > 16384:
                    raise ValueError('Invalid graphical launcher receipt')
                receipt = decode(launched.stdout)
                services = {'psx-diagnostics-window-' + app + '.service', 'psx-diagnostics-graphics@' + app + '.service'}
                if not isinstance(receipt, dict) or receipt.get('started') is not True or receipt.get('mode') != app or receipt.get('service') not in services:
                    raise ValueError('Graphical launcher did not identify a fixed browser service')
                with self._browser_intent_guard:
                    self._browser_batch['service'] = receipt['service']
                    self._browser_batch['record']['service'] = receipt['service']
                    diagnostics.atomic_json(directory / 'batch.json', self._browser_batch['record'])
        except Exception:
            with self._browser_intent_guard:
                if self._browser_auto_intent and self._browser_auto_intent['nonce'] == nonce:
                    self._browser_auto_intent = None
                if automatic and self._browser_batch:
                    self._finish_browser_batch(self._browser_batch, 'Browser launch failed.')
            raise
        if automatic:
            with self._browser_intent_guard:
                if self._browser_auto_intent and self._browser_auto_intent['nonce'] == nonce:
                    self._browser_auto_intent['armed'] = True
            if self.runner.fs.root == Path('/'):
                threading.Thread(target=self._browser_watchdog, args=(batch_id,), daemon=True,
                                 name='browser-batch-watchdog').start()
        return {"status": "INCONCLUSIVE", "message": "Graphical session requested on the display. Exit it to return to diagnostics.",
                "app": selected, "automatic_suite": automatic, "hardware_acceptance": False}

    def browser_session(self, user_agent, local=False):
        response = {'csrf_token': self.csrf, 'auto_run': None, 'auto_pending': False}
        family = 'Firefox' if re.search(r'Firefox/[0-9]', user_agent) else 'Chromium' if re.search(r'(Chromium|Chrome)/[0-9]', user_agent) else ''
        with self._browser_intent_guard:
            intent = self._browser_auto_intent
            if intent and time.monotonic() >= intent['expires']:
                self._browser_auto_intent = intent = None
            if local and intent and family == intent['family']:
                if not intent['armed'] or self.jobs.active:
                    response['auto_pending'] = True
                else:
                    response['auto_run'] = {'suite': 'baseline', 'nonce': intent['nonce'],
                                            'batch_id': intent['batch_id'],
                                            'expires_in_seconds': max(1, math.ceil(intent['expires'] - time.monotonic()))}
                    self._browser_batch.update(claimed=True, user_agent=user_agent[:512])
                    self._browser_auto_intent = None
        return response

    def _browser_watchdog(self, batch_id):
        while True:
            with self._browser_intent_guard:
                batch = self._browser_batch
                if not batch or batch['record']['run_id'] != batch_id or batch['finished']:
                    return
                if time.monotonic() >= batch['deadline']:
                    self._finish_browser_batch(batch, 'Automatic browser deadline reached; completion was not confirmed.')
                    return
            time.sleep(0.25)

    def recover_browser_batches(self):
        """Finalize an interrupted automatic batch; never clear individual markers."""
        boot_id = self.runner.identity().get('boot_id')
        for path in sorted(self.results_dir.glob('*/batch.json'), reverse=True)[:200]:
            try:
                record = diagnostics.read_json(path)
                if (not isinstance(record, dict) or record.get('test_id') != 'browser.batch'
                        or record.get('automatic') is not True or record.get('phase') != 'running'
                        or record.get('run_id') != path.parent.name
                        or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', path.parent.name)
                        or record.get('identity', {}).get('boot_id') != boot_id
                        or record.get('browser_app') not in ('browser', 'chromium')):
                    continue
                result_path = path.with_name('result.json')
                if result_path.is_file():
                    completed = diagnostics.read_json(result_path)
                    if isinstance(completed, dict) and completed.get('phase') == 'completed':
                        continue
                ids = record.get('run_ids', [])
                if not isinstance(ids, list) or len(ids) > len(BROWSER_STEPS) or any(
                        not isinstance(item, str) or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', item) for item in ids):
                    continue
                app = record['browser_app']
                service = record.get('service')
                if service not in ('psx-diagnostics-window-' + app + '.service', 'psx-diagnostics-graphics@' + app + '.service'):
                    service = None
                batch = {'record': record, 'run_ids': ids, 'service': service, 'finished': False}
                with self._browser_intent_guard:
                    self._browser_batch = batch
                    self._finish_browser_batch(batch, 'Controller restarted before automatic browser completion was confirmed.')
            except (OSError, ValueError):
                print('An interrupted browser summary could not be recovered.', file=sys.stderr)

    def _persist_browser_batch(self, record):
        try:
            diagnostics.atomic_json(self.results_dir / record['run_id'] / 'result.json', record)
            return 'usb'
        except OSError:
            record['usb_save_failed'] = True
            record['stop_required'] = True
            if record['status'] == 'PASS':
                record['status'] = 'INCONCLUSIVE'
            record['summary_storage'] = 'RAM fallback; USB summary saving failed.'
            fallback = self.runner.fs.local('/run/psx-diagnostics/browser-batches')
            fallback.mkdir(mode=0o700, parents=True, exist_ok=True)
            diagnostics.atomic_json(fallback / (record['run_id'] + '.json'), record)
            return 'ram'

    def _finish_browser_batch(self, batch, error=''):
        if batch['finished']:
            raise ValueError('Automatic browser batch already finished')
        batch['finished'] = True
        record = dict(batch['record'])
        records = []
        for run_id in batch['run_ids']:
            try:
                item = diagnostics.read_json(self.results_dir / run_id / 'result.json')
            except (OSError, ValueError):
                break
            if not isinstance(item, dict) or item.get('phase') != 'completed':
                break
            records.append(item)
        statuses = [item.get('status') for item in records]
        status = 'FAIL' if 'FAIL' in statuses else 'PASS' if not error and len(statuses) == len(BROWSER_STEPS) and all(s == 'PASS' for s in statuses) else 'INCONCLUSIVE'
        record.update(phase='completed', completed_at=diagnostics.timestamp(), status=status,
                      stop_required=bool(error or len(records) != len(BROWSER_STEPS) or 'FAIL' in statuses),
                      message=error[:1000] if error else 'Automatic browser baseline completed.',
                      run_ids=[item['run_id'] for item in records], reports=[summary(item) for item in records],
                      counts={name: statuses.count(name) for name in diagnostics.STATUSES},
                      expected_count=len(BROWSER_STEPS), completed_count=len(records), closed=False)
        batch['record'] = record
        saved, storage, close_error = False, None, None
        try:
            storage = self._persist_browser_batch(record)
            saved = True
        except OSError:
            record['message'] = 'Batch summary could not be saved to USB or RAM.'
        finally:
            service = batch['service']
            allowed = {f'psx-diagnostics-{kind}{app}.service' for kind in ('window-', 'graphics@') for app in ('browser', 'chromium')}
            if service in allowed:
                try:
                    subprocess.run(['/usr/bin/systemctl', 'stop', service], check=True, timeout=12,
                                   stdin=subprocess.DEVNULL, capture_output=True)
                    record['closed'] = True
                except (OSError, subprocess.SubprocessError):
                    close_error = 'The browser service did not confirm closure.'
            if close_error:
                record['close_error'] = close_error
                record['stop_required'] = True
                if record['status'] == 'PASS':
                    record['status'] = 'INCONCLUSIVE'
            try:
                storage = self._persist_browser_batch(record)
                saved = True
            except OSError:
                pass
        batch['record'] = record
        if self._browser_auto_intent and self._browser_auto_intent['batch_id'] == record['run_id']:
            self._browser_auto_intent = None
        return {'saved': saved, 'storage': storage, 'durable': storage == 'usb', 'closed': record['closed'], 'status': record['status']}

    def browser_finish(self, report, user_agent, local=False):
        if (not local or not isinstance(report, dict) or set(report) != {'batch_id', 'nonce', 'run_ids', 'error'}
                or not isinstance(report['nonce'], str) or not isinstance(report['error'], str) or len(report['error']) > 1000
                or not isinstance(report['run_ids'], list) or len(report['run_ids']) > len(BROWSER_STEPS)):
            raise ValueError('Invalid automatic browser completion')
        with self._browser_intent_guard:
            batch = self._browser_batch
            if (not batch or batch['finished'] or not batch['claimed'] or batch['record']['run_id'] != report['batch_id']
                    or not hmac.compare_digest(batch['nonce'], report['nonce']) or batch['user_agent'] != user_agent[:512]
                    or batch['record']['identity']['boot_id'] != self.runner.identity()['boot_id']):
                raise ValueError('Automatic browser completion does not match this launch')
            completed = []
            for index, run_id in enumerate(batch['run_ids']):
                item = diagnostics.read_json(self.results_dir / run_id / 'result.json')
                if (not isinstance(item, dict) or item.get('batch_id') != report['batch_id'] or item.get('identity', {}).get('boot_id') != batch['record']['identity']['boot_id']
                        or item.get('browser_user_agent') != user_agent[:512] or item.get('test_id') != BROWSER_STEPS[index]):
                    raise ValueError('Automatic browser report identity or ordering differs')
                if item.get('phase') != 'completed':
                    break
                completed.append(run_id)
            if completed != report['run_ids'] or (not report['error'] and len(completed) != len(BROWSER_STEPS)):
                raise ValueError('Automatic browser completion does not match saved reports')
            if time.monotonic() >= batch['deadline']:
                return self._finish_browser_batch(batch, 'Automatic browser deadline reached before completion was confirmed.')
            return self._finish_browser_batch(batch, report['error'])

    def request(self, action, params=None):
        if isinstance(action, str) and action in ("state", "job.get", "results.list"):
            return self._request(action, params)
        with self.action_guard:
            return self._request(action, params)

    def _request(self, action, params=None):
        params = params if params is not None else {}
        if not isinstance(action, str) or not isinstance(params, dict):
            raise ValueError("Expected a named action and object parameters")
        allowed = {"state": set(), "results.list": set(), "job.get": {"id"}, "quick": {"retry_reason"},
                   "test.run": {"test_id", "parameters", "retry_reason"}, "network.scan": set(),
                   "network.wifi.connect": {"ssid", "password"}, "network.ethernet.connect": set(),
                   "network.wifi.disconnect": set(), "network.ethernet.disconnect": set(),
                   "graphics.launch": {"app"}, "result.observe": {"run_id", "answer"},
                   "settings.set": {"theme", "font_family", "font_size"},
                   "ssh.info": set(), "ssh.github.preview": {"username"}, "ssh.github.add": {"preview_id"},
                   "ssh.password.enable": set(), "ssh.password.disable": set()}
        if action not in allowed or set(params) - allowed[action]:
            raise ValueError("Unknown action or parameter")
        if action == "state":
            return self.state()
        if action == "results.list":
            return [summary(record) for record in self.results()[:100]]
        if action == "job.get":
            return self.jobs.get(params.get("id"))
        if action == "result.observe":
            return self.runner.observe(params.get("run_id", ""), params.get("answer", ""))
        if action == 'ssh.info':
            if self._ssh_setup_error:
                return {'login': 'root', 'setup_available': False, 'password_enabled': False,
                        'key_fingerprints': [], 'github_available': False, 'message': self._ssh_setup_error}
            return self.ssh_access().info()
        self.ready()
        if action.startswith('ssh.'):
            if self.jobs.active:
                raise BusyError('Wait for the current diagnostic action before changing SSH access')
            if self._ssh_setup_error:
                raise ValueError(self._ssh_setup_error)
            access = self.ssh_access()
            if action == 'ssh.github.preview':
                return access.github_preview(params.get('username', ''))
            if action == 'ssh.github.add':
                return access.github_add(params.get('preview_id', ''))
            if action == 'ssh.password.enable':
                # Private root IPC only: never pass this reply through Jobs or reports.
                try:
                    return access.password_enable()
                except Exception:
                    raise ValueError('Temporary SSH password setup failed. Check SSH setup before trying again.') from None
            if action == 'ssh.password.disable':
                return access.password_disable()
        if action == 'settings.set':
            self.display.updated(params)
            return self.jobs.submit('Update display settings', lambda: self.display.set(params))
        retry = params.get("retry_reason", "")
        if not isinstance(retry, str) or len(retry) > 2048:
            raise ValueError("Invalid retry reason")
        if action == "quick":
            return self.jobs.submit("Quick CPU and RAM check", lambda: self.runner.quick(retry))
        if action == "test.run":
            test_id = params.get("test_id")
            parameters = params.get("parameters", {})
            if test_id in {t["id"] for t in EXTRA_TESTS}:
                if parameters:
                    raise ValueError("Graphics reference tests have no parameters")
                return self.jobs.submit(test_id, lambda: self.external_test(test_id, retry))
            if test_id == "ethernet.connect":
                return self.jobs.submit("Connect Ethernet", self.network.ethernet_connect)
            self.runner.validate(test_id, parameters)
            return self.jobs.submit(test_id, lambda: self.catalogue_test(test_id, parameters, retry))
        if action == "network.scan":
            return self.jobs.submit("Find Wi-Fi networks", self.network.wifi_scan)
        if action == "network.wifi.connect":
            ssid, password = params.get("ssid"), params.get("password", "")
            if not isinstance(ssid, str) or not isinstance(password, str):
                raise ValueError("Wi-Fi name and password must be text")
            return self.jobs.submit("Connect Wi-Fi", lambda: self.network.wifi_connect(ssid, password))
        if action == "network.ethernet.connect":
            return self.jobs.submit("Connect Ethernet", self.network.ethernet_connect)
        if action == "network.ethernet.disconnect":
            return self.jobs.submit("Disconnect Ethernet", self.network.ethernet_disconnect)
        if action == "network.wifi.disconnect":
            return self.jobs.submit("Disconnect Wi-Fi", self.network.wifi_disconnect)
        if action == "graphics.launch":
            app = params.get("app")
            if app not in ("browser", "chromium", "browser-manual", "chromium-manual", "protondb", "opengl", "vulkan", "diagnostics"):
                raise ValueError("Unknown graphical application")
            return self.jobs.submit("Open " + app, lambda: self.launch_graphics(app))
        raise ValueError("Action not implemented")

    def browser_display_mode(self):
        unknown = {"available": False, "source": "X11 RandR", "scope": "Configured mode; not a physical refresh measurement"}
        if self.runner.fs.fixture:
            return dict(unknown, reason="Fixture roots do not query a display")
        helper = Path('/run/psx-frontend/wait-graphics-wm')
        if not helper.is_file():
            helper = Path('/usr/lib/psx-diagnostics/wait-graphics-wm')
        try:
            response = subprocess.run(['/usr/bin/python3', str(helper), '--display-info'],
                env={'PATH': '/usr/bin:/bin', 'HOME': '/root', 'DISPLAY': ':0',
                     'XAUTHORITY': '/run/psx-desktop/Xauthority'}, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=3, check=True)
            if len(response.stdout) > 16384:
                raise ValueError('Display report exceeds its bound')
            value = decode(response.stdout)
            if not isinstance(value, dict) or type(value.get('available')) is not bool:
                raise ValueError('Invalid display report')
            if value['available']:
                for key in ('width', 'height'):
                    if type(value.get(key)) is not int or not 0 < value[key] <= 32768:
                        raise ValueError('Invalid configured display dimensions')
                hz = value.get('configured_refresh_hz')
                if hz is not None and (type(hz) not in (int, float) or not math.isfinite(hz) or not 0 < hz <= 1000):
                    raise ValueError('Invalid configured display cadence')
            return dict(value, source='X11 RandR', scope='Configured mode; not a physical refresh measurement')
        except (OSError, ValueError, subprocess.SubprocessError):
            return dict(unknown, reason='Display information is unavailable')

    def browser_result(self, report, user_agent):
        if not isinstance(report, dict) or set(report) - {"schema_version", "test_id", "status", "duration_ms", "measurements", "renderer", "scope", "run_id"}:
            raise ValueError("Invalid browser result fields")
        if report.get("test_id") not in ("browser.canvas2d", "browser.webgl", "browser.animation", "browser.capabilities", "browser.video"):
            raise ValueError("Unknown browser test")
        if report.get("status") not in diagnostics.STATUSES or report.get("schema_version") != 1:
            raise ValueError("Invalid browser result status")
        run_id = report.get("run_id")
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}", run_id):
            raise ValueError("A saved browser start marker is required")
        directory = self.results_dir / run_id
        previous = diagnostics.read_json(directory / "result.json")
        if (previous.get("test_id") != report["test_id"] or previous.get("phase") != "running"
                or previous.get("identity", {}).get("boot_id") != self.runner.identity()["boot_id"]):
            raise ValueError("Browser start marker does not match this result")
        if previous.get('batch_id') and previous.get('browser_user_agent') != user_agent[:512]:
            raise ValueError('Automatic browser source differs from its saved start marker')
        match = re.search(r'(Firefox|Chromium|Chrome)/([0-9]{1,4})', user_agent)
        browser = ('Firefox' if match[1] == 'Firefox' else 'Chromium') + ' ' + match[2] if match else 'Browser'
        labels = {'browser.capabilities': 'Capabilities', 'browser.canvas2d': 'Canvas', 'browser.webgl': 'WebGL',
                  'browser.animation': 'Frame pacing', 'browser.video': 'Video playback'}
        value = dict(report, run_id=run_id, phase="completed", completed_at=diagnostics.timestamp(),
                     started_at=previous.get('started_at'), title=browser + ' · ' + labels[report['test_id']],
                     batch_id=previous.get('batch_id'),
                     display_mode=previous.get('display_mode'),
                     identity=self.runner.identity(), hardware_acceptance=False, result_directory=str(directory),
                     message="Browser-local result; inspect renderer and client identity.",
                     browser_user_agent=user_agent[:512], evidence_scope="The browser that submitted this report; remote clients do not measure the console GPU.")
        diagnostics.atomic_json(directory / "result.json", value)
        return value

    def browser_start(self, report, user_agent='', local=False):
        with self.action_guard:
            return self._browser_start(report, user_agent, local)

    def _browser_start(self, report, user_agent='', local=False):
        if not isinstance(report, dict) or set(report) not in ({'test_id'}, {'test_id', 'batch_id', 'nonce'}) or report["test_id"] not in (
                "browser.canvas2d", "browser.webgl", "browser.animation", "browser.capabilities", "browser.video"):
            raise ValueError("Unknown browser test")
        identity = self.ready()
        blocked = self.core_prerequisites(report['test_id'], allow_run=False)
        if blocked:
            raise ValueError(blocked['message'])
        if self.jobs.active:
            raise BusyError("Another diagnostic action is running")
        batch_id = None
        if 'batch_id' in report:
            with self._browser_intent_guard:
                batch = self._browser_batch
                if (not local or not batch or not batch['claimed'] or batch['finished']
                        or batch['record']['run_id'] != report['batch_id'] or not isinstance(report['nonce'], str)
                        or not hmac.compare_digest(batch['nonce'], report['nonce'])
                        or batch['user_agent'] != user_agent[:512] or len(batch['run_ids']) >= len(BROWSER_STEPS)
                        or report['test_id'] != BROWSER_STEPS[len(batch['run_ids'])]):
                    raise ValueError('Automatic browser start does not match this launch or step')
                batch_id = report['batch_id']
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
        directory = self.results_dir / run_id
        directory.mkdir(mode=0o700, parents=True)
        display_mode = self.browser_display_mode()
        diagnostics.atomic_json(directory / "result.json", {
            "run_id": run_id, "test_id": report["test_id"], "identity": identity,
            'batch_id': batch_id, 'browser_user_agent': user_agent[:512],
            "phase": "running", "status": "INCONCLUSIVE", "started_at": diagnostics.timestamp(),
            "message": "Browser test started; completion has not been recorded", "hardware_acceptance": False,
            "display_mode": display_mode})
        if batch_id:
            with self._browser_intent_guard:
                self._browser_batch['run_ids'].append(run_id)
                self._browser_batch['record']['run_ids'] = list(self._browser_batch['run_ids'])
                diagnostics.atomic_json(self.results_dir / batch_id / 'batch.json', self._browser_batch['record'])
        return {"run_id": run_id, "display_mode": display_mode}


class IPCHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        raw = self.rfile.readline(LIMIT + 1)
        identifier = None
        try:
            if len(raw) > LIMIT or not raw.endswith(b"\n"):
                raise ValueError("IPC request exceeds limit or is incomplete")
            request = decode(raw)
            if not isinstance(request, dict) or set(request) - {"id", "action", "params"}:
                raise ValueError("Invalid IPC request")
            identifier = request.get("id")
            value = self.server.controller.request(request.get("action"), request.get("params", {}))
            reply = {"id": identifier, "ok": True, "data": value}
        except Exception as error:
            reply = {"id": identifier, "ok": False, "error": safe_error(error)}
        self.wfile.write((json.dumps(reply) + "\n").encode())


class IPCServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class LabHandler(BaseHTTPRequestHandler):
    server_version = "psXDiagnostics"

    def log_message(self, *args):
        pass

    def json_reply(self, code, value):
        payload = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/session":
            local = self.client_address[0] in ('127.0.0.1', '::1')
            return self.json_reply(200, self.server.controller.browser_session(self.headers.get('User-Agent', ''), local=local))
        if path == "/api/state":
            return self.json_reply(200, self.server.controller.state())
        mapping = {"/": "index.html", "/lab": "index.html", "/lab/": "index.html",
                   "/lab.js": "lab.js", "/style.css": "style.css", "/video.webm": "video.webm"}
        name = mapping.get(path)
        if name is None:
            return self.json_reply(404, {"error": "Not found"})
        file = HERE / "lab" / name
        if not file.is_file():
            return self.json_reply(404, {"error": "Browser fixture is not installed"})
        if name == 'style.css':
            payload = file.read_bytes() + self.server.controller.display.browser_css().encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/css; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
        if name in ('index.html', 'lab.js'):
            self.send_header('Cache-Control', 'no-store')
        self.send_header("Content-Length", str(file.stat().st_size))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        with file.open("rb") as source:
            while block := source.read(65536):
                self.wfile.write(block)

    def do_POST(self):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin", "")
        if (urlsplit("http://" + host).hostname not in ("127.0.0.1", "localhost", "::1")
                or origin != "http://" + host
                or not hmac.compare_digest(self.headers.get("X-PSX-CSRF", ""), self.server.controller.csrf)):
            return self.json_reply(403, {"error": "Invalid local browser session"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768 or self.headers.get_content_type() != "application/json":
                raise ValueError("Invalid result body")
            report = decode(self.rfile.read(length))
            path = urlsplit(self.path).path
            if path == "/api/browser/start":
                return self.json_reply(201, self.server.controller.browser_start(
                    report, self.headers.get('User-Agent', ''), self.client_address[0] in ('127.0.0.1', '::1')))
            if path == '/api/browser/finish':
                return self.json_reply(200, self.server.controller.browser_finish(
                    report, self.headers.get('User-Agent', ''), self.client_address[0] in ('127.0.0.1', '::1')))
            if path != "/api/browser/results":
                return self.json_reply(404, {"error": "Unknown action"})
            result = self.server.controller.browser_result(report, self.headers.get("User-Agent", ""))
            return self.json_reply(201, result)
        except (OSError, ValueError, TypeError) as error:
            return self.json_reply(400, {"error": safe_error(error)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=Path("/run/psx-diagnostics/control.sock"))
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--root-prefix", default="/")
    parser.add_argument("--manifest", default=diagnostics.MANIFEST_PATH)
    parser.add_argument("--bind", choices=("127.0.0.1",), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    controller = Controller(args.results_dir, args.root_prefix, args.manifest)
    controller.recover_browser_batches()
    controller.initialize_ssh()
    controller.start_monitor()
    args.socket.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.socket.exists() or args.socket.is_symlink():
        if not stat.S_ISSOCK(args.socket.lstat().st_mode):
            raise ValueError("Refusing to replace an unrelated socket-path file")
        args.socket.unlink()
    ipc = IPCServer(str(args.socket), IPCHandler)
    ipc.controller = controller
    args.socket.chmod(0o600)
    http = ThreadingHTTPServer((args.bind, args.port), LabHandler)
    http.controller = controller
    threading.Thread(target=http.serve_forever, daemon=True, name="browser-lab").start()
    try:
        ipc.serve_forever()
    finally:
        ipc.server_close()
        http.shutdown()
        args.socket.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
