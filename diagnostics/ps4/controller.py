#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Small local diagnostic controller: typed Unix IPC, finite jobs and browser lab."""
from __future__ import annotations

import argparse
import contextlib
import ctypes
from datetime import datetime, timezone
import fcntl
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
import signal
import socketserver
import stat
import subprocess
import sys
import tempfile
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
WAYLAND_MODES = ('browser', 'chromium', 'browser-manual', 'chromium-manual',
                 'protondb', 'opengl', 'vulkan', 'diagnostics')
WAYLAND_UNITS = tuple('psx-diagnostics-graphics@'+mode+'.service' for mode in WAYLAND_MODES) + tuple(
    'psx-diagnostics-window-'+mode+'.service' for mode in WAYLAND_MODES) + ('psx-diagnostics-wayland.service',)
DIAGNOSTICS_UNIT = 'psx-diagnostics-graphics@diagnostics.service'
UVD_SCENARIOS = {'canonical-120': ('uvd.avc', 'Canonical 120-frame AVC reference'),
                 'native720-900': ('uvd.native720', 'Native 720p30 900-frame AVC reference'),
                 'native1080-900': ('uvd.native1080', 'Native 1080p30 900-frame AVC reference')}
POWER_OPERATIONS = {'shutdown': 'Shutdown', 'reboot-orbis': 'Reboot to Orbis'}
POWER_PROFILES = ('ps4-baikal-diagnostics-uvd-alpha4', 'ps4-baikal-diagnostics-uvd-alpha5',
                  'ps4-baikal-diagnostics-uvd-alpha6')
KERNEL_RESTART_RELEASE = '7.2.11-psx-ps4-baikal-uvd-runtime-dev5'
IMMEDIATE_POWER_PROFILES = {
    '7.2.11-psx-ps4-baikal-uvd-runtime-dev6': 'ps4-baikal-diagnostics-uvd-alpha7',
}
IMMEDIATE_POWER_RECORD = '/run/psx-power/power-dispatch.json'
POWER_PHASES = {
    'prepare.flush': ('Preparing: saving diagnostic reports.', 'Preparing: diagnostic reports saved.'),
    'dispatch.lock': ('Preparing: checking the diagnostic lock.', 'Preparing: diagnostic lock acquired.'),
    'dispatch.guard': ('Preparing: checking the confirmed request.', 'Preparing: power checks returned.'),
    'dispatch.flush': ('Preparing: flushing reports before power dispatch.', 'Preparing: report flush returned.'),
    'dispatch.mounts': ('Preparing: checking storage mounts.', 'Preparing: storage mounts checked.'),
    'dispatch.sync': ('Preparing: synchronizing storage.', 'Preparing: storage sync returned.'),
    'dispatch.remount': ('Preparing: making the USB read-only.', 'Preparing: USB remount returned.'),
    'dispatch.remount.verify': ('Preparing: verifying read-only storage.', 'Preparing: read-only storage verified.'),
    'dispatch.icc': ('Requesting restart to Orbis; physical outcome is unobserved.',
                     'Restart request returned; physical outcome is unobserved.'),
    'dispatch.restart': ('Requesting Linux restart to Orbis; physical outcome is unobserved.',
                        'Linux restart request accepted; physical outcome is unobserved.'),
    'dispatch.shutdown': ('Requesting shutdown; physical outcome is unobserved.',
                          'Shutdown request returned; physical outcome is unobserved.'),
}
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


def _power_read_json(path):
    """Bounded Power receipts, including roots with the legacy runner ABI."""
    limit = 65536
    path = Path(path).absolute()
    if (path.resolve(strict=True) != path
            or any(path.is_relative_to(root) for root in (Path('/dev'), Path('/proc'), Path('/sys')))
            or not stat.S_ISREG(path.lstat().st_mode)):
        raise ValueError('Power JSON must be a regular file without symlinks.')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError('Power JSON exceeds its 64 KiB regular-file budget.')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > limit:
        raise ValueError('Power JSON exceeds its 64 KiB budget.')
    if (len(raw) != before.st_size
            or (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns)):
        raise ValueError('Power JSON changed while reading.')
    return decode(raw)


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
        self.revision = 0
        self._finalizing = None
        self.stopping = threading.Event()

    def submit(self, title, function, after_save=None):
        with self.guard:
            if self.stopping.is_set():
                raise BusyError('A confirmed power action has stopped diagnostic work.')
            if self.active:
                raise BusyError("Another action is running; wait for its result.")
            identifier = uuid.uuid4().hex
            record = {"id": identifier, "title": title, "state": "running", "started_at": diagnostics.timestamp(),
                      "result": None, "error": None, "output": [], "hardware_acceptance": False}
            # Reserve before storage I/O, keeping status available and excluding
            # a second submit even if mkdir/fsync cannot return promptly.
            self.active = identifier
            self.revision += 1
            self.records[identifier] = record
            initial = dict(record, output=[])
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            diagnostics.atomic_json(self.directory / (identifier + ".json"), initial)
        except Exception:
            with self.guard:
                self.records.pop(identifier, None)
                if self.active == identifier:
                    self.active = None
                self.revision += 1
            # A late fsync failure may leave a file; preserve it as evidence.
            # No worker or post-save callback has started.
            raise
        with self.guard:
            if self.stopping.is_set():
                return {key: record[key] for key in ('id', 'state', 'title')} | {'job_id': identifier}
            while len(self.records) > 32:
                del self.records[next(iter(self.records))]
        def work():
            if self.stopping.is_set():
                return
            saved = False
            try:
                value = function()
                changes = {"state": "completed", "result": value}
            except Exception as error:
                changes = {"state": "failed", "error": safe_error(error)}
            with self.guard:
                if self.stopping.is_set():
                    return
                # Seal output at the same point the former locked save did.
                # Late events must not mutate the snapshot being serialized.
                self._finalizing = identifier
                final = dict(record, **changes, completed_at=diagnostics.timestamp())
                final["output"] = list(record["output"])
            try:
                diagnostics.atomic_json(self.directory / (identifier + ".json"), final)
                saved = True
            except Exception:
                final.update(state="failed", error="The action finished but its report could not be saved.")
            with self.guard:
                if self.stopping.is_set():
                    return
                # Until the durable save returns, readers still see running and
                # the active reservation still excludes another action.
                record.update(final)
                self.active = None
                self._finalizing = None
                self.revision += 1
            # A confirmed power request may dispatch only after its final job
            # record is durable. Ordinary jobs retain their existing behavior.
            if saved and record['state'] == 'completed' and after_save is not None:
                after_save(record['result'])
        threading.Thread(target=work, daemon=True, name="diagnostic-job").start()
        return {"id": identifier, "job_id": identifier, "state": "running", "title": title}

    def event(self, value):
        with self.guard:
            if not self.stopping.is_set() and self.active and self._finalizing != self.active:
                record = self.records[self.active]
                record["output"].append(diagnostics.clean_terminal(json.dumps(value))[:1500])
                record["output"] = record["output"][-40:]

    def preempt(self):
        """Memory-only cancellation; never wait for a worker or its storage."""
        self.stopping.set()
        with self.guard:
            for record in self.records.values():
                if record.get('state') == 'running':
                    record.update(state='failed', completed_at=diagnostics.timestamp(),
                                  error='Stopped by the confirmed power request; unsaved evidence may be lost.')
            self.active = None
            self._finalizing = None
            self.revision += 1

    def submit_now(self, title, function):
        """Power-only RAM job; intentionally has no USB save or after-save gate."""
        identifier = uuid.uuid4().hex
        with self.guard:
            record = {'id': identifier, 'title': title, 'state': 'running', 'started_at': diagnostics.timestamp(),
                      'result': None, 'error': None, 'output': [], 'hardware_acceptance': False}
            self.records[identifier] = record
            self.active = identifier
            self.revision += 1
            while len(self.records) > 32:
                del self.records[next(iter(self.records))]
        def work():
            try:
                value = function(identifier)
                changes = {'state': 'completed', 'result': value}
            except Exception as error:
                changes = {'state': 'failed', 'error': safe_error(error)}
            with self.guard:
                record.update(changes, completed_at=diagnostics.timestamp())
                if self.active == identifier:
                    self.active = None
                self.revision += 1
        threading.Thread(target=work, daemon=True, name='diagnostic-power').start()
        return {'id': identifier, 'job_id': identifier, 'state': 'running', 'title': title}

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
        self._power_status_cache = None
        self._power_guard_lock = threading.Lock()
        self._power_boot = None
        self._power_now = None
        self._power_uncertain = ''
        self._worker_guard = threading.RLock()
        self._launch_guard = threading.Lock()
        self._workers = {}
        self.runner.process_started = self._register_worker
        self.runner.cancelled = self.jobs.stopping.is_set
        self.runner.launch_guard = self._launch_guard
        if getattr(self.network, 'executor', True) is None and not self.runner.fs.fixture:
            self.network.executor = lambda argv, timeout: self._cancellable_capture(argv, timeout=timeout, text=True)
        self._cache_power_boot()

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
            self._ssh_access = module.SSHAccess(root_prefix=str(self.runner.fs.root),
                executor=lambda argv, timeout, data: self._cancellable_capture(argv, timeout=timeout, input=data))
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
        if self.jobs.stopping.is_set():
            raise ValueError('Diagnostics were stopped by a confirmed power request; only power and status remain available.')
        identity = self.runner.identity()
        if not identity["release_matches"] or not identity["boot_id"]:
            raise ValueError("This action requires the matching PS4 diagnostic kernel and boot manifest.")
        power_intent = self._power_intent_path(identity)
        if power_intent.exists() or power_intent.is_symlink():
            raise ValueError('A power request is already recorded; only status/reports remain available.')
        uvd_stop = self.runner.uvd_stop_reason()
        if uvd_stop:
            raise ValueError(uvd_stop)
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
            "browser.canvas2d", "browser.webgl", "browser.animation", "browser.capabilities", "browser.video", "browser.batch", "wayland.native",
            "uvd.native720", "uvd.native1080", "power.shutdown.request", "power.reboot-orbis.request"}
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
        power = self._power_dispatch_outcome(self.runner.identity(), include_pending=True)
        if power:
            records[power['run_id']] = power
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
        # Reuse the existing bounded report read for the hardware overview.
        # Truncating first would hide a foundation PASS after browser traffic.
        results = self.results()
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
                "power": self.power_status(identity),
                "tests": tests, "results": [summary(record) for record in results[:30]],
                "overview_results": [summary(record) for record in latest.values()],
                "jobs": self.jobs.list(), "hardware_acceptance": False}

    def _power_intent_path(self, identity):
        boot = identity.get('boot_id', '')
        # Noncanonical fixture/ordinary boot IDs still fail exact power guards;
        # keep this lookup a fixed safe filename for the ordinary ready path.
        if not isinstance(boot, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,64}', boot):
            boot = 'invalid-boot'
        return self.results_dir / 'power-intents' / (boot + '.json')

    def _cache_power_boot(self):
        """One startup read of trusted identity; power never waits for USB I/O."""
        self._power_boot = None
        try:
            identity = self.runner.identity()
            release = identity.get('kernel_release')
            if release not in IMMEDIATE_POWER_PROFILES:
                return
            def require(value, message):
                if not value:
                    raise ValueError(message)
            require(identity.get('release_matches') and identity.get('manifest_available')
                    and str(uuid.UUID(identity.get('boot_id', ''))) == identity['boot_id'], 'Unverified boot')
            manifest = diagnostics.read_json(self.runner.fs.root / self.runner.manifest_path.lstrip('/'))
            require(manifest.get('profile') == IMMEDIATE_POWER_PROFILES[release], 'Unmatched power profile')
            adapter = self.runner.uvd_adapter()
            adapter.select_contract(manifest)
            validation = manifest.get('manifest_validation', {})
            require(validation.get('embedded_root_contract_verified') is True
                    and validation.get('observed_kernel_release') == release
                    and validation.get('observed_media_initramfs_sha256') == manifest.get('initramfs_sha256')
                    and manifest.get('required_boot_arguments') == adapter.POWEROFF_BOOT_ARGUMENTS,
                    'Unverified root/initramfs contract')
            tokens = (self.runner.fs.text('/proc/cmdline') or '').split()
            for argument in adapter.POWEROFF_BOOT_ARGUMENTS:
                key = argument.split('=')[0]
                require([value for value in tokens if value.split('=')[0] == key] == [argument]
                        and self.runner.fs.text('/sys/module/amdgpu/parameters/' + key.split('.')[1]) in ('Y', '1'),
                        'Unmatched immutable power gate')
            receipt = diagnostics.read_json(self.runner.fs.root / 'run/psx-media/psx-diagnostics/installed.json')
            require(receipt.get('owner') == 'psx-diagnostics-usb-v1' and receipt.get('verified_readback') is True
                    and receipt.get('profile') == manifest['profile'] and receipt.get('kernel_release') == release
                    and ('image_version' not in receipt or receipt['image_version'] == manifest['image_version'])
                    and all(re.fullmatch(r'[0-9a-f]{64}', str(manifest.get(key, '')))
                            and receipt.get('files', {}).get(name) == manifest[key] for name, key in
                        (('bzImage', 'kernel_sha256'), ('initramfs.cpio.gz', 'initramfs_sha256'),
                         ('psx-diagnostics/rootfs.ext4', 'rootfs_sha256'))), 'Unmatched verified USB receipt')
            self._power_boot = json.loads(json.dumps(identity))
            self._restore_power_now()
        except (OSError, ValueError, TypeError, KeyError, ImportError):
            pass

    def _restore_power_now(self):
        try:
            saved = _power_read_json(self.runner.fs.root / IMMEDIATE_POWER_RECORD.lstrip('/'))
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError):
            self._power_uncertain = 'The prior power outcome cannot be read; another request could duplicate it.'
            return
        if not isinstance(saved, dict) or saved.get('immediate') is not True or saved.get('identity') != self._power_boot:
            return
        operation = saved.get('operation')
        valid = (isinstance(operation, str) and operation in POWER_OPERATIONS and saved.get('title') == POWER_OPERATIONS[operation]
            and saved.get('test_id') == 'power.' + operation + '.request'
            and re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}', str(saved.get('run_id', '')))
            and re.fullmatch(r'[0-9a-f]{32}', str(saved.get('job_id', '')))
            and saved.get('phase') == 'completed' and saved.get('status') == 'INCONCLUSIVE'
            and saved.get('hardware_acceptance') is False and saved.get('stop_required') is True
            and all(type(saved.get(key)) is bool for key in ('command_submitted', 'in_flight', 'retry_allowed', 'cancelled'))
            and saved.get('dispatch_state') in ('preparing', 'issuing_shutdown', 'issuing_kernel_restart',
                'request_returned_physical_outcome_unobserved', 'failed_no_retry')
            and isinstance(saved.get('message'), str) and len(saved['message']) <= 1000
            and type(saved.get('dispatch_deadline_monotonic')) in (int, float)
            and math.isfinite(saved['dispatch_deadline_monotonic']))
        if valid:
            self._power_now = saved
            self.jobs.stopping.set()
        else:
            self._power_uncertain = 'The prior power record is incomplete; another request could duplicate it.'

    def _register_worker(self, process):
        with self._worker_guard:
            self._workers[process.pid] = process
        if self.jobs.stopping.is_set():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        def unregister():
            with self._worker_guard:
                self._workers.pop(process.pid, None)
        return unregister

    def _cancellable_capture(self, argv, *, timeout, check=False, capture_output=True, text=False, env=None, input=None):
        """Tracked fixed tools, anonymous RAM output, and no secret input logs."""
        if self.runner.fs.fixture:
            raise ValueError('Fixture roots never execute controller tools.')
        if self.jobs.stopping.is_set():
            raise ValueError('Diagnostic command cancelled by the confirmed power request')
        data = input.encode() if isinstance(input, str) else input
        if data is not None and (not isinstance(data, bytes) or len(data) > LIMIT):
            raise ValueError('Private tool input exceeds its bound')
        environment = {'PATH': '/usr/bin:/usr/sbin:/bin:/sbin', 'LC_ALL': 'C', 'LANG': 'C',
                       'HOME': '/root', 'NM_CLI_COLOR': 'never'}
        environment.update(env or {})
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error, tempfile.TemporaryFile() as incoming:
            if data is not None:
                incoming.write(data); incoming.seek(0)
            result = diagnostics.execute_process(argv, timeout,
                Path('/proc/self/fd') / str(output.fileno()), Path('/proc/self/fd') / str(error.fileno()), environment,
                process_started=self._register_worker, cancelled=self.jobs.stopping.is_set,
                launch_guard=self._launch_guard, stdin=incoming if data is not None else None, preview_limit=LIMIT)
        if result.interrupted:
            raise ValueError('Diagnostic command cancelled by the confirmed power request')
        if result.timed_out:
            raise subprocess.TimeoutExpired(argv, timeout)
        if result.output_limited:
            raise ValueError('Controller tool output exceeded its bound')
        response = subprocess.CompletedProcess(argv, result.returncode,
            result.stdout if text else result.stdout.encode(), result.stderr if text else result.stderr.encode())
        if check:
            response.check_returncode()
        return response

    def _kill_power_workers(self):
        self.jobs.stopping.set()
        with self._worker_guard:
            killed = 0
            for process in self._workers.values():
                if process.returncode is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                        killed += 1
                    except OSError:
                        pass
        return killed

    def _kill_power_graphics(self):
        """Signal only fixed graphical cgroups, never the controller or PID1."""
        root = self.runner.fs.root / 'sys/fs/cgroup/system.slice'
        killed = []
        for unit in WAYLAND_UNITS:
            parents = [root]
            if '@' in unit:
                template = unit.split('@', 1)[0].replace('-', '\\x2d')
                parents.append(root / ('system-' + template + '.slice'))
            for parent in parents:
                path = parent / unit / 'cgroup.kill'
                try:
                    if path.resolve(strict=True) != path:
                        continue
                    descriptor = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
                    try:
                        if os.write(descriptor, b'1') == 1:
                            killed.append(unit)
                    finally:
                        os.close(descriptor)
                except (OSError, RuntimeError):
                    continue
        return killed

    def _power_ram_record(self, record):
        """Atomic RAM visibility/fence, deliberately without fsync or USB writes."""
        if self._power_now is not record:
            raise ValueError('A superseded power request was not sent.')
        temporary = None
        try:
            parent = self.runner.fs.root / 'run/psx-power'
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = parent.stat()
            if parent.resolve() != parent or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('The power RAM directory is not protected.')
            record['ram_record_saved'] = True
            record.pop('ram_record_error', None)
            data = (json.dumps(record) + '\n').encode()
            if len(data) > 65536:
                raise ValueError('The power RAM record exceeds its budget.')
            temporary = parent / ('.power-' + uuid.uuid4().hex + '.json')
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
            with self._power_guard_lock:
                if self._power_now is not record:
                    raise ValueError('A superseded power record cannot replace the current fence.')
                os.replace(temporary, parent / 'power-dispatch.json')
            return True
        except (OSError, ValueError) as error:
            record.update(ram_record_saved=False, ram_record_error=safe_error(error)[:300])
            return False
        finally:
            if temporary is not None:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def _power_kernel_call(self, operation):
        if self.runner.fs.fixture or self.runner.fs.root != Path('/') or os.geteuid() != 0:
            raise ValueError('Fixture roots never execute a power syscall.')
        commands = {'shutdown': 0x4321FEDC, 'reboot-orbis': 0x01234567}
        libc = ctypes.CDLL(None, use_errno=True)
        reboot = libc.reboot
        reboot.argtypes = [ctypes.c_int]
        reboot.restype = ctypes.c_int
        if reboot(commands[operation]) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))

    def request_power_now(self, operation):
        identity = self._power_boot
        if identity is None:
            raise ValueError('Immediate power needs the exact verified diagnostic boot.')
        if self._power_uncertain:
            raise ValueError(self._power_uncertain)
        with self._power_guard_lock:
            prior = self._power_now
            if prior is None:
                self._restore_power_now()
                prior = self._power_now
                if self._power_uncertain:
                    raise ValueError(self._power_uncertain)
            self._expire_power_now()
            if prior and (prior.get('command_submitted') is True or prior.get('in_flight') is True):
                identifier = prior['job_id']
                with self.jobs.guard:
                    self.jobs.records.setdefault(identifier, {'id': identifier, 'title': prior['title'],
                        'state': 'completed', 'result': dict(prior), 'error': None, 'output': []})
                return {'id': identifier, 'job_id': identifier, 'state': 'completed', 'title': prior['title']}
            run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
            record = {'schema_version': 1, 'run_id': run_id, 'test_id': 'power.' + operation + '.request',
                'title': POWER_OPERATIONS[operation], 'identity': identity, 'operation': operation,
                'phase': 'completed', 'status': 'INCONCLUSIVE', 'hardware_acceptance': False,
                'stop_required': True, 'power_armed': True, 'immediate': True, 'in_flight': True,
                'command_submitted': False, 'retry_allowed': False, 'dispatch_state': 'preparing',
                'dispatch_deadline_monotonic': time.monotonic() + 5, 'cancelled': False,
                'started_at': diagnostics.timestamp(),
                'message': 'Stopping diagnostics now. Unsaved reports may be lost; physical outcome is unobserved.'}
            self._power_now = record
            self.jobs.preempt()
            def dispatch(identifier):
                record['job_id'] = identifier
                entered_call = False
                try:
                    record['worker_groups_killed'] = self._kill_power_workers()
                    record['graphics_units_killed'] = self._kill_power_graphics()
                    with self._power_guard_lock:
                        if (record['cancelled'] or self._power_now is not record
                                or time.monotonic() >= record['dispatch_deadline_monotonic']):
                            raise TimeoutError('Power preparation expired before submission.')
                        record.update(command_submitted=True, in_flight=False,
                            dispatch_state='issuing_shutdown' if operation == 'shutdown' else 'issuing_kernel_restart',
                            message='Power requested now; physical outcome is unobserved. Unsaved reports may be lost.')
                    self._power_ram_record(record)
                    # Publish a completed RAM job before entering a kernel call
                    # which may never return. No service/worker/USB wait occurs.
                    with self.jobs.guard:
                        self.jobs.records[identifier].update(state='completed', result=dict(record),
                                                             completed_at=diagnostics.timestamp())
                        if self.jobs.active == identifier:
                            self.jobs.active = None
                    entered_call = True
                    self._power_kernel_call(operation)
                    record.update(dispatch_state='request_returned_physical_outcome_unobserved')
                except Exception as error:
                    definite_refusal = isinstance(error, OSError) and error.errno in (1, 22, 38)
                    retry = not entered_call or definite_refusal
                    record.update(dispatch_state='failed_no_retry', command_submitted=not retry,
                        retry_allowed=retry, message='Power request failed: ' + safe_error(error)[:300]
                            + (' Request was not sent; confirm again to retry.' if retry else ' Outcome is uncertain; no duplicate request will be sent.'))
                record['in_flight'] = False
                record['completed_at'] = diagnostics.timestamp()
                if self._power_now is record:
                    try:
                        self._power_ram_record(record)
                    except (OSError, ValueError):
                        pass
                return record
            job = self.jobs.submit_now(POWER_OPERATIONS[operation], dispatch)
            record.setdefault('job_id', job['id'])
            return job

    def _expire_power_now(self):
        record = self._power_now
        if (record and record.get('immediate') is True and record.get('in_flight') is True
                and record.get('command_submitted') is not True
                and (time.monotonic() >= record.get('dispatch_deadline_monotonic', 0)
                     or record.get('job_id') and record['job_id'] not in self.jobs.records)):
            record.update(cancelled=True, in_flight=False, retry_allowed=True, dispatch_state='failed_no_retry',
                          message='Power request was not submitted. Confirm again to retry.')

    def _power_now_status(self):
        if self._power_guard_lock.acquire(blocking=False):
            try:
                self._expire_power_now()
            finally:
                self._power_guard_lock.release()
        record = self._power_now
        blocked = bool(self._power_uncertain or record and (record.get('command_submitted') or record.get('in_flight')))
        policy = {'available': not blocked, 'experimental': True,
                  'reason': self._power_uncertain or (record['message'] if blocked else 'Stops diagnostics immediately; unsaved reports may be lost.')}
        result = {operation: dict(policy, title=title) for operation, title in POWER_OPERATIONS.items()}
        if record:
            result['current_request'] = dict(summary(record), operation=record['operation'],
                dispatch_state=record['dispatch_state'], retry_allowed=record.get('retry_allowed', False),
                ram_record_saved=record.get('ram_record_saved', False), ram_record_error=record.get('ram_record_error'))
        return result

    def _power_records(self):
        records, total = [], 0
        for index, path in enumerate(self.results_dir.glob('*/result.json')):
            if index >= 4096: raise ValueError('Power preparation needs a bounded reviewed report directory.')
            total += path.stat().st_size
            if total > 128 * 1024**2: raise ValueError('Power report review exceeds its 128 MiB budget.')
            record = diagnostics.read_json(path)
            if not isinstance(record, dict): raise ValueError('A saved report is malformed; inspect Reports first.')
            records.append(record)
        return records

    def _power_guard(self, operation, *, within_job=False, ignore_run=None):
        if operation not in POWER_OPERATIONS: raise ValueError('Choose Shutdown or Reboot to Orbis.')
        auxiliary = self.runner.fs.root / 'sys/module/psx_sbram'
        if auxiliary.exists() or auxiliary.is_symlink():
            raise ValueError('Auxiliary-memory experiment active; use its recovery procedure.')
        if self.jobs.active and not within_job: raise BusyError('Finish the current test before a power action.')
        identity = self.runner.identity()
        if (not identity.get('release_matches') or not identity.get('manifest_available')
                or str(uuid.UUID(identity.get('boot_id', ''))) != identity.get('boot_id')):
            raise ValueError('Power actions require the exact verified diagnostic boot.')
        adapter = self.runner.uvd_adapter()
        manifest = diagnostics.read_json(self.runner.fs.root / self.runner.manifest_path.lstrip('/'))
        if manifest.get('profile') not in POWER_PROFILES:
            raise ValueError('This build has no reviewed diagnostic power path.')
        adapter.select_contract(manifest)
        validation = manifest.get('manifest_validation', {})
        if (not isinstance(validation, dict) or validation.get('embedded_root_contract_verified') is not True
                or validation.get('observed_kernel_release') != identity['kernel_release']
                or validation.get('observed_media_initramfs_sha256') != manifest.get('initramfs_sha256')
                or any(not re.fullmatch(r'[0-9a-f]{64}', str(manifest.get(key, '')))
                       for key in ('initramfs_sha256', 'rootfs_sha256'))
                or manifest.get('required_boot_arguments') != adapter.POWEROFF_BOOT_ARGUMENTS):
            raise ValueError('The running root/boot-set contract is not verified.')
        args = (self.runner.fs.text('/proc/cmdline') or '').split()
        for argument in adapter.POWEROFF_BOOT_ARGUMENTS:
            key = argument.split('=')[0]
            if ([value for value in args if value.split('=')[0] == key] != [argument]
                    or self.runner.fs.text('/sys/module/amdgpu/parameters/' + key.split('.')[1]) not in ('Y', '1')):
                raise ValueError('The experimental power-off gates are missing or ambiguous.')
        intent = self._power_intent_path(identity)
        if intent.exists() or intent.is_symlink():
            reserved = diagnostics.read_json(intent)
            if (not isinstance(reserved, dict) or reserved.get('run_id') != ignore_run
                    or reserved.get('operation') != operation or reserved.get('power_armed') is not True
                    or reserved.get('identity') != identity):
                raise ValueError('A power request is already recorded; do not retry it.')
        marker = adapter.marker_path(self.runner, identity['boot_id'])
        uvd_intent = diagnostics.read_json(marker) if marker.exists() or marker.is_symlink() else None
        if uvd_intent is not None and (not isinstance(uvd_intent, dict)
                or uvd_intent.get('schema_version') != 1 or uvd_intent.get('boot_id') != identity['boot_id']
                or not isinstance(uvd_intent.get('run_id'), str) or not uvd_intent['run_id']
                or uvd_intent.get('stop_required') is not True):
            raise ValueError('The UVD attempt is malformed; use physical recovery.')
        records = self._power_records()
        matching_uvd = None
        for record in records:
            if not isinstance(record.get('identity'), dict):
                raise ValueError('A saved report identity is malformed; inspect Reports first.')
            if record.get('identity', {}).get('boot_id') != identity['boot_id'] or record.get('run_id') == ignore_run: continue
            if record.get('phase') != 'completed': raise ValueError('A report is unfinished; inspect Reports before a power action.')
            if record.get('test_id') == 'browser.batch' and record.get('closed') is not True:
                raise ValueError('A browser batch has not durably confirmed closing; wait for its final report.')
            post_uvd = (uvd_intent and record.get('run_id') == uvd_intent.get('run_id')
                and record.get('test_id') in ('uvd.avc', 'uvd.native720', 'uvd.native1080')
                and record.get('status') == 'PASS' and record.get('hardware_acceptance') is False
                and all(record.get('identity', {}).get(key) == identity.get(key) for key in
                    ('boot_id', 'kernel_release', 'manifest_sha256', 'runner_sha256', 'catalogue_sha256')))
            if post_uvd: matching_uvd = record
            if record.get('stop_required') and not post_uvd and not self.reviewed_setup_failure(record):
                raise ValueError('A saved fault requires physical recovery; power actions are unavailable.')
        if uvd_intent and (not matching_uvd or uvd_intent.get('manifest_sha256') != identity['manifest_sha256']):
            raise ValueError('The UVD attempt has no matching completed PASS; use physical recovery.')
        batch = self._browser_batch
        if (batch and batch['record'].get('identity', {}).get('boot_id') == identity['boot_id']
                and (not batch['finished'] or batch['record'].get('stop_required') or batch['record'].get('closed') is not True)):
            raise ValueError('Finish the browser batch and inspect any error before a power action.')
        media = self.runner.fs.root / 'run/psx-media/psx-diagnostics/installed.json'
        receipt = diagnostics.read_json(media)
        if (not isinstance(receipt, dict) or not isinstance(receipt.get('files'), dict)
                or receipt.get('owner') != 'psx-diagnostics-usb-v1' or receipt.get('verified_readback') is not True
                or receipt.get('profile') != manifest.get('profile')
                or 'image_version' in receipt and receipt['image_version'] != manifest.get('image_version')
                or receipt.get('kernel_release') != identity['kernel_release']
                or any(receipt.get('files', {}).get(name) != manifest.get(key) for name, key in
                    (('bzImage', 'kernel_sha256'), ('initramfs.cpio.gz', 'initramfs_sha256'),
                     ('psx-diagnostics/rootfs.ext4', 'rootfs_sha256')))):
            raise ValueError('The diagnostic USB ownership receipt does not match this boot.')
        return identity

    def power_status(self, identity=None):
        if self._power_boot is not None:
            return self._power_now_status()
        # Both operations have the same eligibility policy. Inspect saved
        # history only after an action/identity change or the short cache TTL.
        # Confirmation and dispatch always reread all guards independently.
        identity = identity or self.runner.identity()
        auxiliary = self.runner.fs.root / 'sys/module/psx_sbram'
        intent = self._power_intent_path(identity)
        batch = self._browser_batch
        # Dispatch happens after the durable job snapshot. Its small current
        # outcome must stay fresh even while the eligibility scan is cached.
        dispatch = self._power_dispatch_outcome(identity, include_pending=True)
        key = (self.jobs.revision, identity.get('boot_id'), identity.get('manifest_sha256'),
               identity.get('release_matches'), auxiliary.exists() or auxiliary.is_symlink(),
               intent.exists() or intent.is_symlink(),
               (batch['finished'], batch['record'].get('stop_required'), batch['record'].get('closed')) if batch else None,
               (dispatch['dispatch_state'], dispatch['message']) if dispatch else None)
        now = time.monotonic()
        cached = self._power_status_cache
        if cached and cached[0] == key and now < cached[1]:
            return cached[2]
        try:
            self._power_guard('shutdown')
            policy = {'available': True, 'experimental': True}
        except (OSError, ValueError, ImportError, KeyError, TypeError) as error:
            policy = {'available': False, 'reason': safe_error(error)}
        if dispatch and dispatch['dispatch_state'] == 'failed_no_retry':
            policy = {'available': False, 'reason': dispatch['message']}
        result = {operation: dict(policy, title=title) for operation, title in POWER_OPERATIONS.items()}
        if dispatch:
            current = summary(dispatch)
            current.update(operation=dispatch['operation'], dispatch_state=dispatch['dispatch_state'])
            messages = {
                'preparing': 'Preparing the confirmed power request. Reports are saved.',
                'not_sent': 'Preparing the power request: synchronizing storage.',
                'issuing_forced_icc_restart': 'Requesting a restart to Orbis; physical outcome is unobserved.',
                'issuing_kernel_restart': 'Requesting Linux restart to Orbis; physical outcome is unobserved.',
                'issuing_shutdown': 'Requesting shutdown; physical outcome is unobserved.',
                'request_returned_physical_outcome_unobserved': 'Power request sent; physical outcome is unobserved.',
            }
            if dispatch.get('dispatch_phase') in POWER_PHASES:
                current.update(dispatch_phase=dispatch['dispatch_phase'],
                               dispatch_phase_state=dispatch['dispatch_phase_state'])
                current['message'] = dispatch['message']
            else:
                current['message'] = messages.get(dispatch['dispatch_state'], dispatch['message'])
            result['current_request'] = current
        self._power_status_cache = (key, now + 5, result)
        return result

    def _power_dispatch_outcome(self, identity, *, include_pending=False):
        """Only the bounded, reserved current-boot outcome can replace a report."""
        if self._power_now and self._power_now.get('identity') == identity:
            return dict(self._power_now)
        try:
            intent = _power_read_json(self._power_intent_path(identity))
            if (not isinstance(intent, dict) or intent.get('power_armed') is not True
                    or intent.get('operation') not in POWER_OPERATIONS
                    or intent.get('phase') != 'completed' or intent.get('status') != 'INCONCLUSIVE'
                    or intent.get('hardware_acceptance') is not False or intent.get('stop_required') is not True
                    or intent.get('test_id') != 'power.' + intent['operation'] + '.request'
                    or not isinstance(intent.get('identity'), dict)
                    or any(intent['identity'].get(key) != identity.get(key) for key in
                           ('boot_id', 'kernel_release', 'manifest_sha256', 'runner_sha256', 'catalogue_sha256'))
                    or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}', str(intent.get('run_id', '')))):
                return None
        except (OSError, ValueError, KeyError, TypeError):
            return None
        # The USB report is an optional fallback if the RAM outcome could not
        # be written. Never remount a read-only USB merely to update a report.
        for path in (self.runner.fs.root / 'run/psx-diagnostics/power-dispatch.json',
                     self.results_dir / intent['run_id'] / 'result.json'):
            try:
                record = _power_read_json(path)
                if (not isinstance(record, dict) or record.get('identity') != intent['identity']
                        or record.get('run_id') != intent['run_id'] or record.get('operation') != intent['operation']
                        or record.get('test_id') != 'power.' + intent['operation'] + '.request'
                        or record.get('hardware_acceptance') is not False or record.get('stop_required') is not True
                        or record.get('phase') != 'completed' or record.get('status') != 'INCONCLUSIVE'
                        or record.get('dispatch_state') not in ('preparing', 'not_sent', 'issuing_forced_icc_restart', 'issuing_kernel_restart',
                            'issuing_shutdown', 'request_returned_physical_outcome_unobserved', 'failed_no_retry')
                        or 'dispatch_phase' in record and (record['dispatch_phase'] not in POWER_PHASES
                            or record.get('dispatch_phase_state') not in ('started', 'returned', 'failed'))
                        or not isinstance(record.get('message'), str) or len(record['message']) > 1000):
                    continue
                return record
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if include_pending:
            return dict(intent, dispatch_state='preparing',
                        message='Preparing the confirmed power request. Reports are saved.')
        return None

    def _power_phase(self, outcome, phase, state):
        """Fixed data-only progress before/after a call, never a retry/deadline."""
        if phase not in POWER_PHASES or state not in ('started', 'returned', 'failed'):
            raise ValueError('Unknown power progress phase')
        outcome.update(dispatch_phase=phase, dispatch_phase_state=state,
                       dispatch_phase_at=diagnostics.timestamp(), dispatch_monotonic_ns=time.monotonic_ns())
        if state != 'failed':
            outcome['message'] = POWER_PHASES[phase][state == 'returned']
        path = self.runner.fs.root / 'run/psx-diagnostics/power-dispatch.json'
        outcome['uart_marker_written'] = False
        diagnostics.atomic_json(path, outcome)
        self._power_status_cache = None
        line = (f"PSX_POWER_V1 boot={outcome['identity']['boot_id']} run={outcome['run_id']} "
                f"operation={outcome['operation']} phase={phase} state={state} "
                f"monotonic_ns={outcome['dispatch_monotonic_ns']} physical=unobserved\n").encode('ascii')
        # Kernel INFO follows the existing UART console. Nonblocking, bounded
        # writes never use a host device from a synthetic root, even when a
        # dispatch test deliberately clears FixtureFS.fixture.
        written = False
        if not self.runner.fs.fixture and self.runner.fs.root == Path('/') and len(line) <= 512:
            try:
                fd = os.open('/dev/kmsg', os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
                try:
                    if stat.S_ISCHR(os.fstat(fd).st_mode):
                        written = os.write(fd, b'<6>' + line) == len(line) + 3
                finally:
                    os.close(fd)
            except OSError:
                pass
        if written:
            outcome['uart_marker_written'] = True
            diagnostics.atomic_json(path, outcome)

    @contextlib.contextmanager
    def _power_dispatch_lock(self):
        # Same flock as Runner.lock(), but a read-only fd must survive the FAT
        # remount. Runner.lock() opens for append and would keep a writer open.
        path = self.results_dir / '.runner.lock'
        if path.resolve() != path: raise ValueError('The diagnostic lock path contains a symlink.')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode): raise ValueError('The diagnostic lock is not a regular file.')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('Another diagnostic run owns this results directory; power was not sent.') from error
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _power_flush(self):
        directories, count = set(), 0
        for base, dirs, files in os.walk(self.results_dir, followlinks=False):
            directory = Path(base)
            if directory.resolve() != directory or any((directory / name).is_symlink() for name in dirs):
                raise ValueError('Report directories contain a symlink; power preparation stopped.')
            directories.add(directory)
            for name in files:
                count += 1
                if count > 32768: raise ValueError('Too many report files to flush in this action.')
                path = directory / name
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    if not stat.S_ISREG(os.fstat(fd).st_mode): raise ValueError('Report entry is not a regular file.')
                    os.fsync(fd)
                finally: os.close(fd)
        for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            diagnostics.fsync_directory(directory)

    def prepare_power(self, operation):
        identity = self._power_guard(operation, within_job=True)
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
        with self.runner.lock():
            directory = self.results_dir / run_id; directory.mkdir(mode=0o700)
            record = {'schema_version': 1, 'run_id': run_id, 'test_id': 'power.' + operation + '.request',
                'title': POWER_OPERATIONS[operation], 'identity': identity, 'phase': 'running', 'status': 'INCONCLUSIVE',
                'started_at': diagnostics.timestamp(), 'message': 'Checking the confirmed power request.',
                'power_armed': False, 'operation': operation, 'stop_required': False, 'hardware_acceptance': False,
                'result_directory': str(directory)}
            kernel_restart = identity['kernel_release'] == KERNEL_RESTART_RELEASE
            record['power_path'] = ('software-poweroff' if operation == 'shutdown' else
                                    'kernel-restart' if kernel_restart else 'forced-icc-restart')
            record['limitations'] = ('Experimental retained-DMA power-off; wider cleanup and repeated cycles are unproved.'
                if operation == 'shutdown' else
                'Ordinary Linux reboot retains UVD backing until the atomic Baikal ICC restart. Hardware qualification is pending.'
                if kernel_restart else
                'Linux reboot teardown is skipped. One dev3 sequence was observed; dev4 preserves the ICC source path but has no separate observed restart acceptance.')
            record['operator_evidence'] = 'Keep UART capture through the planned power experiment; a returned command or lost connection does not prove the physical outcome.'
            diagnostics.atomic_json(directory / 'result.json', record)
            self.runner.log = diagnostics.Evidence(directory, self.jobs.event)
            self.runner.commands = []
            try:
                states = self._wayland_states()
                if any(value != 'inactive' for name, value in states.items() if name != DIAGNOSTICS_UNIT):
                    raise ValueError('Close browser and graphics tests before a power action.')
                self._power_mounts()
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                record.update(phase='completed', status='BLOCKED', message=safe_error(error),
                    completed_at=diagnostics.timestamp(), commands=self.runner.commands)
                diagnostics.atomic_json(directory / 'result.json', record)
                return record
            record.update(phase='completed', power_armed=True, stop_required=True,
                completed_at=diagnostics.timestamp(), commands=self.runner.commands,
                message='Reports saved; confirmed power request is pending. Physical outcome is not observed.')
            diagnostics.atomic_json(directory / 'result.json', record)
            path = self._power_intent_path(identity); path.parent.mkdir(mode=0o700, exist_ok=True)
            parent = path.parent.stat()
            if (path.parent.resolve() != path.parent or parent.st_uid != (os.geteuid() if self.runner.fs.fixture else 0)
                    or parent.st_mode & 0o077):
                raise ValueError('Power intent directory is not private.')
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(record, stream); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            diagnostics.fsync_directory(path.parent)
            progress = dict(record, dispatch_state='preparing')
            self._power_phase(progress, 'prepare.flush', 'started')
            try:
                self._power_flush()
            except (OSError, ValueError) as error:
                progress.update(dispatch_state='failed_no_retry',
                    message='Power preparation failed: ' + safe_error(error)[:300] + ' Do not retry; use recovery.')
                try:
                    self._power_phase(progress, 'prepare.flush', 'failed')
                except OSError:
                    pass
                raise
            self._power_phase(progress, 'prepare.flush', 'returned')
        return record

    def _power_mounts(self):
        raw = self.runner.fs.data('/proc/self/mountinfo', 1024**2 + 1)
        if raw is None or len(raw) > 1024**2: raise ValueError('Mount evidence is missing or exceeds its limit.')
        text = raw.decode('utf-8', 'replace')
        result = {}
        for line in text.splitlines():
            fields = line.split()
            if len(fields) < 10 or '-' not in fields: raise ValueError('Mount evidence is incomplete.')
            separator = fields.index('-')
            if separator < 6 or len(fields) != separator + 4: raise ValueError('Mount evidence is incomplete.')
            mountpoint = fields[4]
            if mountpoint in ('/', '/run/psx-media'):
                if mountpoint in result: raise ValueError('Mount evidence is ambiguous.')
                result[mountpoint] = (set(fields[5].split(',')), fields[separator + 1], fields[separator + 2])
        if ('/' not in result or '/run/psx-media' not in result or 'ro' not in result['/'][0]
                or result['/'][1] != 'ext4' or not re.fullmatch(r'/dev/loop[0-9]+', result['/'][2])
                or result['/run/psx-media'][1] != 'vfat'):
            raise ValueError('Power actions require the reviewed read-only loop root and diagnostic FAT USB.')
        return result

    @contextlib.contextmanager
    def _power_icc_device(self):
        """Open only the protected ICC node on the reviewed device mount."""
        if self.runner.fs.fixture or self.runner.fs.root != Path('/'):
            raise ValueError('Fixture roots never open the ICC power device.')
        node = '/dev/psx-diagnostics-power/icc'
        raw = self.runner.fs.data('/proc/self/mountinfo', 1024**2 + 1)
        if raw is None or len(raw) > 1024**2:
            raise ValueError('ICC device mount evidence is missing or exceeds its limit.')
        device_mount = None
        for line in raw.decode('utf-8', 'strict').splitlines():
            fields = line.split()
            if len(fields) < 10 or '-' not in fields:
                raise ValueError('ICC device mount evidence is incomplete.')
            separator = fields.index('-')
            if separator < 6 or len(fields) != separator + 4:
                raise ValueError('ICC device mount evidence is incomplete.')
            mountpoint = fields[4]
            if mountpoint == '/dev':
                if device_mount is not None:
                    raise ValueError('ICC device mount evidence is ambiguous.')
                device_mount = (fields[3], fields[separator + 1],
                                set(fields[5].split(',')) | set(fields[separator + 3].split(',')))
            elif mountpoint != '/' and (node == mountpoint or node.startswith(mountpoint.rstrip('/') + '/')):
                raise ValueError('The ICC power path has an unexpected covering mount.')
        if (device_mount is None or device_mount[:2] != ('/', 'devtmpfs')
                or 'rw' not in device_mount[2] or {'ro', 'nodev'} & device_mount[2]):
            raise ValueError('The ICC power device requires writable /dev devtmpfs without nodev.')
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        expected_device = os.makedev(73, 0)

        def valid_node(info):
            return (stat.S_ISCHR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_rdev == expected_device)

        with contextlib.ExitStack() as cleanup:
            dev_fd = os.open('/dev', directory_flags)
            cleanup.callback(os.close, dev_fd)
            dev = os.fstat(dev_fd)
            if (not stat.S_ISDIR(dev.st_mode) or dev.st_uid != 0 or dev.st_gid != 0
                    or dev.st_mode & 0o022):
                raise ValueError('The device directory is not protected.')
            try: os.mkdir('psx-diagnostics-power', 0o700, dir_fd=dev_fd)
            except FileExistsError: pass
            parent_fd = os.open('psx-diagnostics-power', directory_flags, dir_fd=dev_fd)
            cleanup.callback(os.close, parent_fd)
            parent = os.fstat(parent_fd)
            if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_gid != 0
                    or stat.S_IMODE(parent.st_mode) != 0o700 or parent.st_dev != dev.st_dev):
                raise ValueError('The ICC power directory is not protected.')
            try: info = os.stat('icc', dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                os.mknod('icc', stat.S_IFCHR | 0o600, expected_device, dir_fd=parent_fd)
                info = os.stat('icc', dir_fd=parent_fd, follow_symlinks=False)
            if not valid_node(info) or info.st_dev != parent.st_dev:
                raise ValueError('The fixed ICC power node is not protected.')
            descriptor = os.open('icc', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
            cleanup.callback(os.close, descriptor)
            opened = os.fstat(descriptor)
            if not valid_node(opened) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('The opened ICC power node differs from the protected node.')
            yield descriptor

    def _power_icc_restart(self):
        if self.runner.fs.fixture or self.runner.fs.root != Path('/'):
            raise ValueError('Fixture roots never execute ICC or power commands.')
        if not re.search(r'^\s*73\s+icc\s*$', self.runner.fs.text('/proc/devices') or '', re.M):
            raise ValueError('The fixed ICC character driver is absent.')
        for index, process in enumerate(path for path in Path('/proc').iterdir() if path.name.isdigit()):
            if index >= 8192: raise ValueError('ICC caller inventory exceeds its limit.')
            try:
                for number, entry in enumerate((process / 'fd').iterdir()):
                    if number >= 4096: raise ValueError('ICC descriptor inventory exceeds its limit.')
                    try: info = entry.stat()
                    except FileNotFoundError: continue
                    if stat.S_ISCHR(info.st_mode) and os.major(info.st_rdev) == 73:
                        raise ValueError('Another ICC caller is active; restart was not sent.')
            except FileNotFoundError: continue
        class Command(ctypes.Structure):
            _fields_ = [('major', ctypes.c_uint8), ('minor', ctypes.c_uint16), ('data', ctypes.c_void_p),
                       ('length', ctypes.c_uint16), ('reply', ctypes.c_void_p), ('reply_length', ctypes.c_uint16)]
        if ctypes.sizeof(Command) != 40: raise ValueError('ICC command ABI differs.')
        payload = ctypes.create_string_buffer(bytes.fromhex('000102000100'))
        command = Command(4, 1, ctypes.addressof(payload), 6, None, 0)
        with self._power_icc_device() as descriptor:
            fcntl.ioctl(descriptor, 0xc0284901, bytearray(bytes(command)), True)

    def dispatch_power(self, record):
        if not record.get('power_armed'): return
        outcome = dict(record, dispatch_state='not_sent')
        path = self.runner.fs.root / 'run/psx-diagnostics/power-dispatch.json'
        errors = (OSError, ValueError, ImportError, KeyError, TypeError, subprocess.SubprocessError)
        def failed(error):
            reason = ('A power preparation command failed or timed out.' if isinstance(error, subprocess.SubprocessError)
                      else safe_error(error)[:300])
            outcome.update(dispatch_state='failed_no_retry', message='Power request failed: ' + reason + ' Do not retry; use recovery.')
            try:
                self._power_phase(outcome, outcome.get('dispatch_phase', 'dispatch.lock'), 'failed')
            except OSError:
                pass
        try:
            if self.runner.fs.fixture: raise ValueError('Fixture roots never execute actual power commands.')
            self._power_phase(outcome, 'dispatch.lock', 'started')
            with self._power_dispatch_lock():
                try:
                    self._power_phase(outcome, 'dispatch.lock', 'returned')
                    self._power_phase(outcome, 'dispatch.guard', 'started')
                    self._power_guard(record['operation'], ignore_run=record['run_id'])
                    self._power_phase(outcome, 'dispatch.guard', 'returned')
                    self._power_phase(outcome, 'dispatch.flush', 'started')
                    self._power_flush()
                    self._power_phase(outcome, 'dispatch.flush', 'returned')
                    self._power_phase(outcome, 'dispatch.mounts', 'started')
                    mounts = self._power_mounts()
                    self._power_phase(outcome, 'dispatch.mounts', 'returned')
                    def checked(argv):
                        subprocess.run(argv, check=True, timeout=30, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self._power_phase(outcome, 'dispatch.sync', 'started')
                    checked(['/usr/bin/sync'])
                    self._power_phase(outcome, 'dispatch.sync', 'returned')
                    if record['operation'] == 'reboot-orbis':
                        if 'rw' in mounts['/run/psx-media'][0]:
                            self._power_phase(outcome, 'dispatch.remount', 'started')
                            checked(['/usr/bin/mount', '-o', 'remount,ro', '/run/psx-media'])
                            self._power_phase(outcome, 'dispatch.remount', 'returned')
                        self._power_phase(outcome, 'dispatch.remount.verify', 'started')
                        if 'ro' not in self._power_mounts()['/run/psx-media'][0]:
                            raise ValueError('The diagnostic USB did not become read-only; restart was not sent.')
                        self._power_phase(outcome, 'dispatch.remount.verify', 'returned')
                        if record['identity']['kernel_release'] == KERNEL_RESTART_RELEASE:
                            outcome['dispatch_state'] = 'issuing_kernel_restart'
                            self._power_phase(outcome, 'dispatch.restart', 'started')
                            checked(['/usr/bin/systemctl', 'reboot', '--no-block'])
                            self._power_phase(outcome, 'dispatch.restart', 'returned')
                        else:
                            outcome['dispatch_state'] = 'issuing_forced_icc_restart'
                            self._power_phase(outcome, 'dispatch.icc', 'started')
                            self._power_icc_restart()
                            self._power_phase(outcome, 'dispatch.icc', 'returned')
                    else:
                        outcome['dispatch_state'] = 'issuing_shutdown'
                        self._power_phase(outcome, 'dispatch.shutdown', 'started')
                        checked(['/usr/bin/systemctl', 'poweroff', '--no-block'])
                        self._power_phase(outcome, 'dispatch.shutdown', 'returned')
                    outcome.update(dispatch_state='request_returned_physical_outcome_unobserved',
                                   message='Power request sent; physical outcome is not observed.')
                except errors as error:
                    failed(error)
                if outcome['dispatch_state'] == 'failed_no_retry':
                    try:
                        if 'rw' in self._power_mounts()['/run/psx-media'][0]:
                            report = self.results_dir / record['run_id'] / 'result.json'
                            original = _power_read_json(report)
                            if original.get('identity') == record['identity'] and original.get('run_id') == record['run_id']:
                                diagnostics.atomic_json(report, outcome)
                    except errors:
                        pass
        except errors as error:
            failed(error)
        try:
            diagnostics.atomic_json(path, outcome)
        except OSError:
            pass
        with self.jobs.guard:
            record.update(outcome)
        self._power_status_cache = None

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
        alpha_uvd = test_id == "uvd.avc" and self.runner.identity().get("declared_artifacts", {}).get("uvd_runtime") is True
        if alpha_uvd or test_id in ("memory.pattern", "audio.44100", "ethernet.https") or test_id.startswith(("gpu.", "wayland.")):
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

    def _wayland_states(self):
        result = self.runner.command(['/usr/bin/systemctl', 'show', '--property=Id,LoadState,ActiveState',
                                      *WAYLAND_UNITS], 10)
        if result.returncode not in (0, 1) or result.timed_out or result.interrupted or result.output_limited:
            raise ValueError('Graphics service state could not be verified')
        records = {}
        for block in result.stdout.strip().split('\n\n'):
            fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
            name = fields.get('Id')
            if (name not in WAYLAND_UNITS or name in records or fields.get('LoadState') not in ('loaded', 'not-found')
                    or fields.get('ActiveState') not in ('active', 'inactive')
                    or fields.get('LoadState') == 'not-found' and fields.get('ActiveState') != 'inactive'):
                raise ValueError('A graphics service is failed, transitioning or unidentified')
            records[name] = fields['ActiveState']
        if set(records) != set(WAYLAND_UNITS):
            raise ValueError('Incomplete graphics service inventory')
        return records

    def uvd_scenario(self, scenario):
        """One selected terminal scenario, inside a saved job and shared intent."""
        if not isinstance(scenario, str) or scenario not in UVD_SCENARIOS:
            raise ValueError('Unknown fixed UVD scenario')
        identity = self.ready()
        test_id, title = UVD_SCENARIOS[scenario]
        enabled = identity.get('declared_artifacts', {}).get('uvd_runtime') is True
        if enabled:
            blocked = self.core_prerequisites(test_id)
            if blocked: return blocked
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
        directory = self.results_dir / run_id
        with self.runner.lock():
            directory.mkdir(mode=0o700)
            diagnostics.fsync_directory(self.results_dir)
            record = {'schema_version': 1, 'run_id': run_id, 'test_id': test_id, 'title': title,
                'parameters': {'scenario': scenario}, 'identity': identity, 'phase': 'running',
                'status': 'INCONCLUSIVE', 'started_at': diagnostics.timestamp(),
                'stop_required': False, 'hardware_acceptance': False, 'result_directory': str(directory),
                'message': 'Selected terminal UVD scenario is pending'}
            diagnostics.atomic_json(directory / 'result.json', record)
            self.runner.log = diagnostics.Evidence(directory, self.jobs.event)
            self.runner.commands = []
            self.runner.log.event('run_started', test_id=test_id, parameters=record['parameters'])
            invoked = False
            try:
                if not enabled:
                    value = {'status': 'UNSUPPORTED', 'message': 'Ordinary UVD flag is false; no decoder was opened or failed.'}
                else:
                    path = HERE / 'uvd_suite.py'
                    if not path.is_file(): path = self.runner.fs.local('/opt/psx-diagnostics/uvd_suite.py')
                    if not path.is_file():
                        value = {'status': 'BLOCKED', 'message': 'The fixed UVD scenario module is not installed; no decoder was opened.'}
                    else:
                        spec = importlib.util.spec_from_file_location('psx_uvd_terminal_suite', path)
                        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                        invoked = True
                        value = module.run(self.runner, scenario)
                if (not isinstance(value, dict) or value.get('status') not in diagnostics.STATUSES
                        or value.get('status') == 'PASS' and value.get('stop_required') is not True):
                    raise ValueError('UVD scenario did not return a valid terminal receipt')
                if self.runner.identity() != identity:
                    raise ValueError('Boot/artifact identity changed during the UVD scenario')
                record.update({key: value[key] for key in ('status', 'message', 'evidence', 'stop_required', 'scenario') if key in value})
            except (OSError, ValueError, ImportError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
                record.update(status='INCONCLUSIVE' if invoked else 'BLOCKED', stop_required=invoked,
                    message='UVD scenario stopped: ' + (safe_error(error) or type(error).__name__))
            record.update(phase='completed', completed_at=diagnostics.timestamp(), commands=self.runner.commands)
            self.runner.log.event('run_completed', status=record['status'], message=record['message'])
            diagnostics.atomic_json(directory / 'result.json', record)
            return record

    def _wayland_read(self, logical, maximum):
        path = self.runner.fs.root / str(logical).lstrip('/')
        info = path.lstat()
        owner = os.geteuid() if self.runner.fs.fixture else 0
        if (path.resolve() != path or not stat.S_ISREG(info.st_mode) or info.st_uid != owner
                or info.st_mode & 0o022 or info.st_size > maximum):
            raise ValueError('Wayland input/evidence is not a protected bounded file')
        raw = path.read_bytes()
        if len(raw) != info.st_size: raise ValueError('Wayland file changed while reading')
        return raw

    def wayland_test(self):
        identity = self.ready()
        blocked = self.core_prerequisites('wayland.native')
        if blocked: return blocked
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
        native_id = uuid.uuid4().hex
        directory = self.results_dir / run_id
        with self.runner.lock():
            directory.mkdir(mode=0o700)
            record = {'schema_version': 1, 'run_id': run_id, 'test_id': 'wayland.native',
                      'title': 'Native Wayland reference pixels', 'phase': 'running', 'status': 'INCONCLUSIVE',
                      'identity': identity, 'started_at': diagnostics.timestamp(), 'stop_required': False,
                      'hardware_acceptance': False, 'result_directory': str(directory), 'native_run_id': native_id,
                      'message': 'Checking the fixed native Wayland provider and display handoff'}
            diagnostics.atomic_json(directory / 'result.json', record)
            self.runner.log = diagnostics.Evidence(directory, self.jobs.event)
            self.runner.commands = []
            stopped = stop_completed = launched = False
            restore = False
            def checked(argv, timeout):
                result = self.runner.command(argv, timeout)
                if result.returncode or result.timed_out or result.interrupted or result.output_limited:
                    raise ValueError('Bounded Wayland service/setup command did not complete')
                return result
            try:
                if self.runner.fs.fixture and self.runner.executor is None:
                    raise ValueError('Fixture roots cannot start Wayland display sessions')
                manifest = decode(self._wayland_read(self.runner.manifest_path, 1024**2))
                provider = manifest.get('alpha_derivation', {}).get('wayland_suite', {})
                archive = '/usr/share/psx-diagnostics/wayland/psx-wayland-suite.tar.gz'
                expected = provider.get('sha256')
                if (provider.get('path') != archive or not isinstance(expected, str)
                        or not re.fullmatch(r'[0-9a-f]{64}', expected)
                        or manifest.get('manifest_validation', {}).get('embedded_root_contract_verified') is not True):
                    raise ValueError('This boot has no verified fixed native Wayland provider')
                raw = self._wayland_read(archive, 2*1024**2)
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError('Wayland archive differs from the boot contract')
                helper = '/usr/lib/psx-diagnostics/launch-wayland'
                helper_raw = self._wayland_read(helper, 128*1024)
                record['provider'] = {'archive_sha256': expected, 'launcher_sha256': hashlib.sha256(helper_raw).hexdigest()}
                base = ['/usr/bin/python3', helper, '--suite', archive, '--expected-sha256', expected, '--run-id', native_id]
                states = self._wayland_states()
                if any(value != 'inactive' for name, value in states.items() if name != DIAGNOSTICS_UNIT):
                    raise ValueError('Close all browser/graphics windows before native Wayland')
                restore = states[DIAGNOSTICS_UNIT] == 'active'
                if restore:
                    stopped = True  # An interrupted stop is also a display handoff.
                    checked(['/usr/bin/systemctl', 'stop', DIAGNOSTICS_UNIT], 15)
                    stop_completed = True
                if any(value != 'inactive' for value in self._wayland_states().values()):
                    raise ValueError('Xorg did not release the display for native Wayland')
                # This invocation validates only files/services/Mesa identity.
                checked(base, 25)
                if self.runner.identity() != identity: raise ValueError('Boot identity changed before Wayland')
                diagnostics.atomic_json(directory / 'result.json', dict(record, message='Native Wayland attempt starting'))
                launched = True
                command = self.runner.command(base + ['--apply'], 65)
                native_path = '/run/psx-wayland/runs/' + native_id
                artifacts = {}
                for name in ('result.json', 'client.jsonl', 'compositor.log'):
                    data = self._wayland_read(native_path + '/' + name, 1024**2)
                    destination = directory / ('wayland-' + name)
                    with destination.open('xb') as saved:
                        saved.write(data); saved.flush(); os.fsync(saved.fileno())
                    artifacts[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                native = decode((directory / 'wayland-result.json').read_bytes())
                client = native.get('client') if isinstance(native, dict) else None
                if (not isinstance(native, dict) or native.get('run_id') != native_id
                        or native.get('boot_id') != identity['boot_id'] or native.get('kernel_release') != identity['kernel_release']):
                    raise ValueError('Wayland result does not belong to this boot and run')
                record.update(native_report=native, artifacts=artifacts)
                renderer = client.get('renderer') if isinstance(client, dict) else None
                passed = (command.returncode == 0 and not (command.timed_out or command.interrupted or command.output_limited)
                          and native.get('phase') == 'completed' and native.get('status') == 'PASS'
                          and native.get('stop_required') is False and native.get('returncode') == 0
                          and native.get('native_wayland') is True and native.get('nested') is False
                          and native.get('backend') == 'drm' and isinstance(client, dict) and client.get('status') == 'PASS'
                          and client.get('native_wayland') is True and client.get('known_pixels') is True
                          and client.get('frames') == 3 and isinstance(renderer, str) and renderer.strip()
                          and not any(word in renderer.lower() for word in ('llvmpipe', 'softpipe', 'software', 'swrast', 'lavapipe', 'swiftshader')))
                if not passed: raise ValueError('Native Wayland did not establish a complete hardware pixel PASS')
                if self.runner.identity() != identity: raise ValueError('Boot identity changed during Wayland')
                diagnostics.fsync_directory(directory)
                if any(value != 'inactive' for value in self._wayland_states().values()):
                    raise ValueError('A native/display service remains active after the test')
                if restore:
                    checked(['/usr/bin/systemctl', 'start', DIAGNOSTICS_UNIT], 15)
                    states = self._wayland_states()
                    if states[DIAGNOSTICS_UNIT] != 'active': raise ValueError('Diagnostics display did not return')
                record.update(status='PASS', message='Three native Wayland frames matched reference pixels; endurance and input qualification remain separate.')
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
                record.update(status='INCONCLUSIVE' if launched or stopped else 'BLOCKED',
                              stop_required=bool(launched or stopped), message=safe_error(error) or 'Wayland action was interrupted')
                # A failed/uncertain active test never automatically opens Xorg.
                record['diagnostics_display_restored'] = False
                if restore and stop_completed and not launched:
                    try:
                        if self.runner.identity() != identity or any(v != 'inactive' for v in self._wayland_states().values()):
                            raise ValueError('Display/boot state changed during data-only preflight')
                        checked(['/usr/bin/systemctl', 'start', DIAGNOSTICS_UNIT], 15)
                        if self._wayland_states()[DIAGNOSTICS_UNIT] != 'active':
                            raise ValueError('Diagnostics display did not return')
                        record.update(status='BLOCKED', stop_required=False, diagnostics_display_restored=True)
                    except (OSError, ValueError, subprocess.SubprocessError):
                        record.update(status='INCONCLUSIVE', stop_required=True,
                                      message=record['message']+'; diagnostics display restoration was not verified')
            else:
                record['diagnostics_display_restored'] = restore
            record.update(phase='completed', completed_at=diagnostics.timestamp(), commands=self.runner.commands)
            diagnostics.atomic_json(directory / 'result.json', record)
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
            self._cancellable_capture(['/usr/bin/python3', str(prepare)], check=True, timeout=60, capture_output=True)
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
            launched = self._cancellable_capture([str(helper), *(['--automatic'] if automatic else []), app], check=True, timeout=15, capture_output=True)
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
                    self._cancellable_capture(['/usr/bin/systemctl', 'stop', service], check=True, timeout=12,
                                              capture_output=True)
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
        if isinstance(action, str) and action in ("state", "job.get", "results.list", "power.execute"):
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
                   "graphics.launch": {"app"}, "graphics.wayland": set(), "uvd.scenario": {"scenario"},
                   "power.execute": {"operation", "confirm"}, "result.observe": {"run_id", "answer"},
                   "settings.set": {"theme", "font_family", "font_size"},
                   "ssh.info": set(), "ssh.github.preview": {"username"}, "ssh.github.add": {"preview_id"},
                   "ssh.password.enable": set(), "ssh.password.disable": set()}
        if action not in allowed or set(params) - allowed[action]:
            raise ValueError("Unknown action or parameter")
        if action not in ('state', 'results.list', 'job.get', 'ssh.info'):
            self._power_status_cache = None
        if action == "state":
            return self.state()
        if action == "results.list":
            return [summary(record) for record in self.results()[:100]]
        if action == "job.get":
            return self.jobs.get(params.get("id"))
        if action == "result.observe":
            intent = self._power_intent_path(self.runner.identity())
            if intent.exists() or intent.is_symlink():
                raise ValueError('A power request is already recorded; report changes are unavailable.')
            return self.runner.observe(params.get("run_id", ""), params.get("answer", ""))
        if action == 'ssh.info':
            if self._ssh_setup_error:
                return {'login': 'root', 'setup_available': False, 'password_enabled': False,
                        'key_fingerprints': [], 'github_available': False, 'message': self._ssh_setup_error}
            return self.ssh_access().info()
        if action == 'power.execute':
            operation = params.get('operation')
            if (not isinstance(operation, str) or operation not in POWER_OPERATIONS
                    or params.get('confirm') is not True):
                raise ValueError('Choose one power action and confirm it explicitly.')
            if self._power_boot is not None:
                return self.request_power_now(operation)
            self._power_guard(operation)
            return self.jobs.submit(POWER_OPERATIONS[operation], lambda: self.prepare_power(operation),
                                    after_save=self.dispatch_power)
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
        if action == 'graphics.wayland':
            return self.jobs.submit('Native Wayland reference pixels', self.wayland_test)
        if action == 'uvd.scenario':
            scenario = params.get('scenario')
            if not isinstance(scenario, str) or scenario not in UVD_SCENARIOS:
                raise ValueError('Choose one fixed UVD scenario')
            return self.jobs.submit(UVD_SCENARIOS[scenario][1], lambda: self.uvd_scenario(scenario))
        raise ValueError("Action not implemented")

    def browser_display_mode(self):
        unknown = {"available": False, "source": "X11 RandR", "scope": "Configured mode; not a physical refresh measurement"}
        if self.runner.fs.fixture:
            return dict(unknown, reason="Fixture roots do not query a display")
        helper = Path('/run/psx-frontend/wait-graphics-wm')
        if not helper.is_file():
            helper = Path('/usr/lib/psx-diagnostics/wait-graphics-wm')
        try:
            response = self._cancellable_capture(['/usr/bin/python3', str(helper), '--display-info'],
                env={'PATH': '/usr/bin:/bin', 'HOME': '/root', 'DISPLAY': ':0',
                     'XAUTHORITY': '/run/psx-desktop/Xauthority'},
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
        self._power_status_cache = None
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
        self._power_status_cache = None
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
