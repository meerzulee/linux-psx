#!/usr/bin/env python3
"""Finite PS4 diagnostics. No default test, shell execution or power commands.

Only selected actions execute tools. A fixture root disables real subprocesses;
tests may inject an executor. Reports are local evidence, never hardware support
or automatic acceptance. Python 3.10+; optional tools are listed by --catalogue.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid
import wave


HERE = Path(__file__).resolve().parent
CATALOGUE_PATH = HERE / "catalogue.json"
MANIFEST_PATH = "/etc/psx-diagnostics/boot-set.json"
SCHEMA = 1
LOG_LIMIT = 8 * 1024 * 1024
PREVIEW_LIMIT = 128 * 1024
TOOL_DIRECTORIES = ("/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
CPU_ANSWER = "3951d9a412ce4c815eada76de213f560647cd60d4c37896cbcdcb899b645f197"
STATUSES = frozenset(("PASS", "FAIL", "DEGRADED", "NOT_RUN", "UNSUPPORTED",
                      "DEFERRED", "BLOCKED", "INCONCLUSIVE"))
PARAMETERS = {
    "inventory.readonly": set(), "cpu.compute": {"seconds"},
    "memory.pattern": {"memory_mib"}, "ethernet.status": {"interface"},
    "ethernet.connect": set(), "ethernet.https": {"interface", "url"},
    "gpu.opengl.info": set(), "gpu.vulkan.info": set(), "audio.info": set(),
    "audio.44100": {"fixture", "audio_device"}, "storage.inventory": set(),
    "uvd.avc": set(), "audio.native48": set(), "wireless.wifi": set(),
    "wireless.bluetooth": set(), "power.off": set(), "power.reboot": set(),
    "power.suspend": set(),
}
QUICK_STEPS = (("inventory.readonly", {}), ("cpu.compute", {"seconds": 2}),
               ("memory.pattern", {"memory_mib": 32}))


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def clean_terminal(value):
    return "".join(c for c in str(value) if c in "\n\t" or ord(c) >= 32 and ord(c) != 127).replace("\x1b", "")


def read_json(path):
    with Path(path).open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("JSON input exceeds 1 MiB")
    return json.loads(data)


def load_catalogue():
    catalogue = read_json(CATALOGUE_PATH)
    ids = [test["id"] for test in catalogue["tests"]]
    if catalogue["schema_version"] != SCHEMA or len(ids) != len(set(ids)):
        raise ValueError("Invalid diagnostic catalogue")
    if set(ids) != set(PARAMETERS) or set(catalogue["statuses"]) != STATUSES:
        raise ValueError("Catalogue does not match the runner's fixed test IDs")
    return catalogue


class FixtureFS:
    """Reads only explicitly requested paths; fixture symlinks cannot escape."""
    def __init__(self, root="/"):
        self.root = Path(root).resolve()
        self.fixture = self.root != Path("/")

    def local(self, name):
        path = self.root / str(name).lstrip("/")
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("Path escapes the fixture root")
        return resolved

    def data(self, name, limit=65536):
        try:
            with self.local(name).open("rb") as stream:
                return stream.read(limit)
        except (OSError, ValueError):
            return None

    def text(self, name, limit=65536):
        data = self.data(name, limit)
        return data.decode("utf-8", "replace").strip() if data is not None else None

    def exists(self, name):
        try:
            return self.local(name).exists()
        except (OSError, ValueError):
            return False

    def entries(self, name, limit=256):
        try:
            return sorted(p.name for p in self.local(name).iterdir())[:limit]
        except (OSError, ValueError):
            return []

    def link(self, name):
        try:
            path = self.local(name)
            return str(path.relative_to(self.root)) if path.exists() else None
        except (OSError, ValueError):
            return None

    def tool(self, name):
        for directory in TOOL_DIRECTORIES:
            try:
                path = self.local(directory + "/" + name)
                if path.is_file() and os.access(path, os.X_OK):
                    return str(path)
            except (OSError, ValueError):
                pass
        return None


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class Evidence:
    def __init__(self, directory, callback=None):
        self.directory = directory
        self.callback = callback
        self.sequence = 0

    def event(self, kind, **fields):
        self.sequence += 1
        record = {"schema_version": SCHEMA, "sequence": self.sequence,
                  "timestamp": timestamp(), "event": kind, **fields}
        fd = os.open(self.directory / "journal.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(self.directory)
        if self.callback:
            self.callback(record)


@dataclass
class CommandResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    output_limited: bool = False
    interrupted: bool = False
    elapsed_seconds: float = 0.0


def execute_process(argv, timeout, stdout_path, stderr_path, env=None, progress=None):
    """Keep raw output on disk and kill the isolated process group at any exit.

    No shell is involved. Children may not intentionally escape their process
    group; only trusted image tools are invoked by the fixed action handlers.
    """
    started = time.monotonic()
    last_progress = 0
    timed_out = limited = interrupted = False
    process = None
    environment = {"PATH": ":".join(TOOL_DIRECTORIES), "LANG": "C.UTF-8",
                   "LC_ALL": "C.UTF-8", "HOME": "/nonexistent"}
    environment.update(env or {})
    with stdout_path.open("wb") as output, stderr_path.open("wb") as error:
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=error, env=environment, start_new_session=True,
                                       close_fds=True, shell=False)
            while process.poll() is None:
                elapsed = time.monotonic() - started
                timed_out = elapsed >= timeout
                limited = max(stdout_path.stat().st_size, stderr_path.stat().st_size) > LOG_LIMIT
                if timed_out or limited:
                    break
                if progress and elapsed - last_progress >= 1:
                    progress(elapsed)
                    last_progress = elapsed
                time.sleep(0.025)
        except KeyboardInterrupt:
            interrupted = True
        except OSError as exc:
            error.write(("Could not execute tool: " + str(exc) + "\n").encode())
        finally:
            if process is not None:
                # Also stop descendants when their direct parent has exited.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    pass
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            output.flush()
            error.flush()
            os.fsync(output.fileno())
            os.fsync(error.fileno())
    def preview(path):
        with path.open("rb") as stream:
            return stream.read(PREVIEW_LIMIT).decode("utf-8", "replace")
    return CommandResult(process.returncode if process else None, preview(stdout_path),
                         preview(stderr_path), timed_out, limited, interrupted,
                         round(time.monotonic() - started, 3))


def outcome(status, message, evidence=None, **fields):
    assert status in STATUSES
    return {"status": status, "message": message, "evidence": evidence or {}, **fields}


class Runner:
    def __init__(self, results_dir, root_prefix="/", manifest=MANIFEST_PATH,
                 session_receipt=None, executor=None, callback=None):
        self.catalogue = load_catalogue()
        self.tests = {test["id"]: test for test in self.catalogue["tests"]}
        self.fs = FixtureFS(root_prefix)
        self.results_dir = Path(results_dir).resolve() if results_dir else None
        self.manifest_path = manifest
        self.session_path = session_receipt
        self.executor = executor
        self.callback = callback
        self.commands = []
        self.log = None

    def identity(self):
        manifest_bytes = self.fs.data(self.manifest_path, 1024 * 1024)
        manifest_text = manifest_bytes.decode("utf-8", "replace") if manifest_bytes else None
        try:
            manifest = json.loads(manifest_text) if manifest_text else {}
            if not isinstance(manifest, dict):
                manifest = {}
        except ValueError:
            manifest = {}
        kernel = manifest.get("kernel")
        kernel = kernel if isinstance(kernel, dict) else {}
        declared_release = manifest.get("kernel_release") or kernel.get("release")
        expected = self.catalogue["expected_kernel_release"]
        running = self.fs.text("/proc/sys/kernel/osrelease")
        # Select identity fields; do not blindly copy private provisioning data.
        allowed = ("kernel_release", "kernel_sha256", "modules_sha256", "config_sha256",
                   "initramfs_sha256", "rootfs_sha256", "module_count", "uvd_runtime")
        receipt = {key: manifest[key] for key in allowed if key in manifest}
        session = {"provided": False, "continuity": "unverified"}
        if self.session_path:
            raw = self.fs.data(self.session_path)
            try:
                supplied = json.loads(raw or "null")
                if isinstance(supplied, dict):
                    session = {key: supplied[key] for key in
                               ("experiment_id", "session_id", "continuity", "state", "boot_id")
                               if key in supplied}
                    session.update(provided=True, receipt_sha256=digest(raw or b""))
            except ValueError:
                session = {"provided": True, "continuity": "invalid-receipt"}
        return {"boot_id": self.fs.text("/proc/sys/kernel/random/boot_id"),
                "kernel_release": running, "expected_kernel_release": expected,
                "declared_kernel_release": declared_release,
                "release_matches": bool(running and running == expected == declared_release),
                "manifest_available": bool(manifest),
                "manifest_sha256": digest(manifest_bytes) if manifest_bytes else None,
                "declared_artifacts": receipt,
                "artifact_scope": "Manifest declarations and observed release; not a hash of the running kernel",
                "runner_sha256": digest(Path(__file__).read_bytes()),
                "catalogue_sha256": digest(CATALOGUE_PATH.read_bytes()),
                "uart": session}

    @contextlib.contextmanager
    def lock(self):
        if self.results_dir is None:
            raise ValueError("--results-dir is required to run or record a test")
        self.results_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.results_dir / ".runner.lock").open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Another diagnostic run owns this results directory") from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def results(self):
        if not self.results_dir or not self.results_dir.exists():
            return []
        records = []
        for path in sorted(self.results_dir.glob("*/result.json"), reverse=True)[:200]:
            try:
                record = read_json(path)
                if isinstance(record, dict) and record.get("test_id") in self.tests:
                    records.append(record)
            except (OSError, ValueError):
                continue
        return sorted(records, key=lambda record: str(record.get("started_at", "")), reverse=True)

    def validate(self, test_id, params):
        if test_id not in self.tests:
            raise ValueError("Unknown immutable test ID")
        unexpected = set(params) - PARAMETERS[test_id]
        if unexpected:
            raise ValueError("Unexpected parameters: " + ", ".join(sorted(unexpected)))
        params = dict(params)
        if "interface" in PARAMETERS[test_id]:
            params.setdefault("interface", "psx0")
            if not isinstance(params["interface"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}", params["interface"]):
                raise ValueError("Invalid interface name")
        for key, default, minimum, maximum in (("seconds", 3, 1, 10), ("memory_mib", 16, 1, 128)):
            if key in PARAMETERS[test_id]:
                params.setdefault(key, default)
                if type(params[key]) is not int or not minimum <= params[key] <= maximum:
                    raise ValueError(f"{key} must be {minimum}–{maximum}")
        if test_id == "ethernet.https":
            url = params.get("url", "")
            if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 32 for c in url):
                raise ValueError("Invalid HTTPS URL")
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Supply an explicit HTTPS URL without embedded credentials")
        if test_id == "audio.44100":
            params.setdefault("audio_device", "hw:0,3")
            if not isinstance(params["audio_device"], str) or not re.fullmatch(r"(?:plug)?hw:[A-Za-z0-9_-]+,[0-9]+", params["audio_device"]):
                raise ValueError("Use an explicit hw:CARD,DEVICE or plughw:CARD,DEVICE")
            if "fixture" in params and (not isinstance(params["fixture"], str) or
                                       not 0 < len(params["fixture"]) <= 4096 or "\x00" in params["fixture"]):
                raise ValueError("Fixture must be a nonempty logical file path")
        return params

    def needs_retry_reason(self, test_id):
        boot = self.identity()["boot_id"]
        prior = next((r for r in self.results() if r.get("test_id") == test_id and
                      r.get("identity", {}).get("boot_id") == boot and r.get("commands")), {})
        return prior.get("status") == "FAIL" or (
            prior.get("status") == "INCONCLUSIVE" and not prior.get("observation_required") and
            (self.tests[test_id]["kind"] in ("functional", "operator") or any(
                c.get("returncode") != 0 or c.get("timed_out") or c.get("interrupted") or
                c.get("output_limited") for c in prior.get("commands", []))))

    def quick(self, retry_reason=""):
        """Only these three finite checks; no network, graphics or device tests."""
        identity = self.identity()
        group = uuid.uuid4().hex
        records = []
        status = "PASS"
        message = "Quick CPU/owned-memory checks passed; inventory is observation, not hardware qualification"
        for test_id, params in QUICK_STEPS:
            if self.identity()["boot_id"] != identity["boot_id"]:
                status, message = "BLOCKED", "Boot identity changed; the quick sequence stopped"
                break
            record = self.run(test_id, params, retry_reason, preset_run_id=group)
            records.append(record)
            collected = test_id == "inventory.readonly" and record["status"] == "INCONCLUSIVE" and bool(record.get("evidence"))
            if record["status"] != "PASS" and not collected:
                status, message = record["status"], "Quick sequence stopped: " + record["message"]
                break
        return {"schema_version": SCHEMA, "suite_id": self.catalogue["suite_id"],
                "suite_version": self.catalogue["suite_version"], "preset_id": "quick",
                "preset_run_id": group, "identity": identity, "status": status,
                "message": message, "hardware_acceptance": False, "results": records,
                "result_directory": str(self.results_dir)}

    def run(self, test_id, params=None, retry_reason="", preset_run_id=None):
        params = self.validate(test_id, params or {})
        with self.lock():
            identity = self.identity()
            previous = [r for r in self.results() if r.get("identity", {}).get("boot_id") == identity["boot_id"]]
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
            directory = self.results_dir / run_id
            directory.mkdir(mode=0o700)
            fsync_directory(self.results_dir)
            self.log = Evidence(directory, self.callback)
            self.commands = []
            record = {"schema_version": SCHEMA, "suite_id": self.catalogue["suite_id"],
                      "suite_version": self.catalogue["suite_version"], "run_id": run_id,
                      "test_id": test_id, "test_version": self.tests[test_id]["version"],
                      "parameters": params, "retry_reason": retry_reason,
                      "started_at": timestamp(), "identity": identity, "phase": "running",
                      "status": "INCONCLUSIVE", "message": "Run started; completion not recorded",
                      "hardware_acceptance": False, "result_directory": str(directory)}
            if preset_run_id:
                record["preset"] = {"id": "quick", "version": 1, "run_id": preset_run_id}
            atomic_json(directory / "result.json", record)
            self.log.event("run_started", run_id=run_id, test_id=test_id, parameters=params,
                           timeout_seconds=self.tests[test_id]["timeout_seconds"])
            started = time.monotonic()
            try:
                kind = self.tests[test_id]["kind"]
                active = kind in ("functional", "operator") or test_id.startswith("gpu.")
                stopped = any(r.get("stop_required") or r.get("phase") == "running" for r in previous)
                prior = next((r for r in previous if r.get("test_id") == test_id and
                              r.get("parameters") == params and r.get("commands")), {})
                failed_command = any(c.get("returncode") != 0 or c.get("timed_out") or
                                     c.get("interrupted") or c.get("output_limited")
                                     for c in prior.get("commands", []))
                repeated = prior.get("status") == "FAIL" or (
                    prior.get("status") == "INCONCLUSIVE" and not prior.get("observation_required") and
                    (kind in ("functional", "operator") or failed_command))
                if active and stopped:
                    result = outcome("BLOCKED", "An earlier run requires recovery/inspection; only observations are available on this boot")
                elif active and repeated and not retry_reason.strip():
                    result = outcome("BLOCKED", "Record changed evidence with --retry-reason before repeating this failed/inconclusive action")
                elif active and (not identity["manifest_available"] or not identity["release_matches"] or not identity["boot_id"]):
                    result = outcome("BLOCKED", "A boot ID, boot-set manifest and the catalogue's exact matching kernel release are required")
                else:
                    result = self.dispatch(test_id, params)
            except KeyboardInterrupt:
                result = outcome("INCONCLUSIVE", "Operator interrupted the run; inspect the preserved journal",
                                 stop_required=test_id == "ethernet.connect" or test_id.startswith("gpu."))
            except (OSError, ValueError, KeyError) as exc:
                result = outcome("INCONCLUSIVE", "Diagnostic collection failed: " + str(exc))
            record.update(result, commands=self.commands, phase="completed", completed_at=timestamp(),
                          elapsed_seconds=round(time.monotonic() - started, 3))
            self.log.event("run_completed", status=record["status"], message=record["message"])
            atomic_json(directory / "result.json", record)
            return record

    def command(self, argv, timeout, env=None):
        if self.fs.fixture and self.executor is None:
            raise ValueError("Fixture roots never execute real tools; inject a synthetic executor")
        number = len(self.commands) + 1
        output = self.log.directory / f"command-{number:02d}.stdout"
        error = self.log.directory / f"command-{number:02d}.stderr"
        self.log.event("command_started", argv=argv, timeout_seconds=timeout,
                       stdout=output.name, stderr=error.name)
        if self.executor:
            result = self.executor(argv, timeout, output, error, env)
        else:
            result = execute_process(argv, timeout, output, error, env,
                                     progress=lambda elapsed: self.log.event(
                                         "command_progress", command_number=number,
                                         elapsed_seconds=round(elapsed, 1), timeout_seconds=timeout))
        for path, content in ((output, result.stdout), (error, result.stderr)):
            if not path.exists():
                with path.open("x", encoding="utf-8") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
        receipt = {"argv": argv, "returncode": result.returncode,
                   "signal": -result.returncode if result.returncode is not None and result.returncode < 0 else None,
                   "timed_out": result.timed_out, "output_limited": result.output_limited,
                   "interrupted": result.interrupted, "elapsed_seconds": result.elapsed_seconds,
                   "stdout": output.name, "stderr": error.name,
                   "stdout_bytes": output.stat().st_size, "stderr_bytes": error.stat().st_size}
        self.commands.append(receipt)
        self.log.event("command_completed", **receipt)
        if result.interrupted:
            raise KeyboardInterrupt
        return result

    def dispatch(self, test_id, params):
        if test_id in ("inventory.readonly", "storage.inventory", "audio.info", "ethernet.status"):
            evidence = {"inventory.readonly": self.inventory, "storage.inventory": self.storage,
                        "audio.info": self.audio_info,
                        "ethernet.status": lambda: self.network(params["interface"])}[test_id]()
            return outcome("INCONCLUSIVE", "Read-only observations collected; no functional hardware test was performed", evidence)
        if test_id.startswith("power."):
            if test_id == "power.suspend":
                return outcome("UNSUPPORTED", self.tests[test_id]["description"])
            return outcome("BLOCKED", "Power commands are not implemented here; the external UART/observer and operator stage must conduct this test",
                           {"uart_receipt": self.identity()["uart"]})
        if test_id == "uvd.avc":
            return outcome("UNSUPPORTED", "Ordinary native kernel has no experimental UVD runtime; this runner does not initialize or switch decoder variants")
        if self.tests[test_id]["kind"] == "deferred":
            return outcome("DEFERRED", self.tests[test_id]["description"])
        if test_id in ("cpu.compute", "memory.pattern"):
            return self.worker(test_id, params)
        if test_id == "ethernet.connect":
            return self.connect()
        if test_id == "ethernet.https":
            return self.https(params)
        if test_id.startswith("gpu."):
            return self.graphics(test_id)
        if test_id == "audio.44100":
            return self.play_audio(params)
        raise ValueError("No handler for test ID")

    def memory_info(self):
        values = {}
        for line in (self.fs.text("/proc/meminfo") or "").splitlines():
            match = re.match(r"(MemTotal|MemAvailable|SwapTotal):\s+(\d+)\s+kB", line)
            if match:
                values[match[1] + "_kib"] = int(match[2])
        return values

    def inventory(self):
        cpu = self.fs.text("/proc/cpuinfo") or ""
        models = sorted(set(re.findall(r"^model name\s*:\s*(.+)$", cpu, re.M)))
        pci = []
        for name in self.fs.entries("/sys/bus/pci/devices"):
            base = "/sys/bus/pci/devices/" + name
            pci.append({"bdf": name, **{key: self.fs.text(base + "/" + key) for key in
                                         ("vendor", "device", "class", "revision")},
                        "driver": self.fs.link(base + "/driver")})
        usb = []
        for name in self.fs.entries("/sys/bus/usb/devices"):
            base = "/sys/bus/usb/devices/" + name
            if self.fs.exists(base + "/idVendor"):
                usb.append({"sysfs_name": name, **{key: self.fs.text(base + "/" + key) for key in
                                                   ("idVendor", "idProduct", "product", "speed")}})
        thermal = []
        for name in self.fs.entries("/sys/class/thermal"):
            if name.startswith("thermal_zone"):
                base = "/sys/class/thermal/" + name
                thermal.append({"zone": name, "type": self.fs.text(base + "/type"),
                                "temperature_millicelsius": self.fs.text(base + "/temp")})
        selected_args = {}
        for token in (self.fs.text("/proc/cmdline") or "").split():
            key, _, value = token.partition("=")
            if key in ("psx_mts_net.autoarm", "modprobe.blacklist", "amdgpu.ps4_uvd_runtime"):
                selected_args[key] = value
        config = {"proc_config_gz_present": self.fs.exists("/proc/config.gz"),
                  "module_directory_present": self.fs.exists("/usr/lib/modules/" + (self.identity()["kernel_release"] or "missing"))}
        return {"cpu": {"models": models, "listed_processors": len(re.findall(r"^processor\s*:", cpu, re.M)),
                        "online": self.fs.text("/sys/devices/system/cpu/online")},
                "memory": self.memory_info(), "pci": pci, "usb": usb, "thermal": thermal,
                "input_nodes": self.fs.entries("/dev/input"), "drm_nodes": self.fs.entries("/dev/dri"),
                "config": config, "selected_boot_policy": selected_args, "storage": self.storage(),
                "scope": "Existence and metadata only; private serials/MACs and the full command line are omitted"}

    def storage(self):
        devices = []
        for name in self.fs.entries("/sys/class/block"):
            base = "/sys/class/block/" + name
            devices.append({"name": name, "sysfs_path": self.fs.link(base),
                            "sectors_512b": self.fs.text(base + "/size"),
                            "read_only": self.fs.text(base + "/ro"),
                            "rotational": self.fs.text(base + "/queue/rotational"),
                            "model": self.fs.text(base + "/device/model"),
                            "vendor": self.fs.text(base + "/device/vendor"),
                            "device_node_present": self.fs.exists("/dev/" + name)})
        return {"devices": devices, "mountinfo": self.fs.text("/proc/self/mountinfo"),
                "scope": "sysfs and mount metadata only; no disk content or extent map validated"}

    def audio_info(self):
        return {"cards": self.fs.text("/proc/asound/cards"), "pcm": self.fs.text("/proc/asound/pcm"),
                "supported_output_rate": 44100, "native_48000": "DEFERRED"}

    def network(self, interface):
        base = "/sys/class/net/" + interface
        values = {key: self.fs.text(base + "/" + key) for key in ("operstate", "carrier", "mtu", "flags")}
        values.update(interface=interface, present=self.fs.exists(base),
                      device_path=self.fs.link(base + "/device"),
                      driver=self.fs.link(base + "/device/driver"),
                      counters={key: self.fs.text(base + "/statistics/" + key) for key in
                                ("rx_bytes", "tx_bytes", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped")},
                      mts_report=self.fs.text("/sys/module/psx_mts_net/parameters/report"),
                      route_table=self.fs.text("/proc/net/route"))
        return values

    def worker(self, test_id, params):
        worker = "cpu" if test_id == "cpu.compute" else "memory"
        amount = params["seconds"] if worker == "cpu" else params["memory_mib"]
        if worker == "memory":
            available = self.memory_info().get("MemAvailable_kib")
            if available is None or amount * 1024 + 64 * 1024 > available or amount * 1024 > available // 4:
                return outcome("BLOCKED", "Memory budget requires known available RAM, a 64 MiB reserve and at most one quarter of available RAM")
        result = self.command([sys.executable, str(Path(__file__).resolve()), "_worker", worker, str(amount)],
                              self.tests[test_id]["timeout_seconds"])
        if result.timed_out or result.interrupted or result.output_limited:
            return outcome("INCONCLUSIVE", "Worker did not complete within its bounds; output is preserved")
        try:
            value = json.loads(result.stdout)
        except ValueError:
            return outcome("INCONCLUSIVE", "Worker did not produce its typed receipt")
        if not isinstance(value, dict):
            return outcome("INCONCLUSIVE", "Worker receipt is not a JSON object")
        expected = (value.get("digest") == CPU_ANSWER and type(value.get("iterations")) is int and
                    value["iterations"] > 0) if worker == "cpu" else (
                    value.get("verified_mib") == amount and value.get("patterns") == [0, 85, 170, 255])
        if result.returncode != 0 or not expected:
            return outcome("FAIL", "The selected bounded computation/pattern check failed", value)
        return outcome("PASS", "Selected owned-buffer/computation check passed; not full CPU/RAM/cooling qualification", value)

    def ip_evidence(self, interface):
        tool = self.fs.tool("ip")
        if not tool:
            return {"collection": "ip tool missing"}
        evidence = {}
        for key, args in (("addresses", ["-j", "address", "show", "dev", interface]),
                          ("routes", ["-j", "route", "show", "dev", interface])):
            result = self.command([tool, *args], 5)
            try:
                evidence[key] = json.loads(result.stdout) if result.returncode == 0 else None
            except ValueError:
                evidence[key] = None
        return evidence

    def connect(self):
        helper = "/usr/lib/psx-diagnostics/ethernet-up"
        path = self.fs.local(helper)
        if not path.is_file() or not os.access(path, os.X_OK):
            return outcome("NOT_RUN", "The fixed Ethernet first-publication helper is not installed")
        result = self.command([str(path)], 120)
        if result.timed_out or result.interrupted or result.output_limited:
            return outcome("INCONCLUSIVE", "Connection helper timed out/interrupted; do not retry an uncertain publication", stop_required=True)
        if result.returncode != 0:
            return outcome("INCONCLUSIVE", "Connection helper declined or failed; inspect its receipt before another action")
        evidence = {"interface": self.network("psx0"), **self.ip_evidence("psx0")}
        addresses = evidence.get("addresses")
        addresses = addresses if isinstance(addresses, list) else []
        has_address = any(isinstance(a, dict) and a.get("scope") == "global"
                          for link in addresses if isinstance(link, dict)
                          for a in (link.get("addr_info") if isinstance(link.get("addr_info"), list) else []))
        routes = evidence.get("routes")
        has_route = isinstance(routes, list) and any(isinstance(r, dict) and r.get("dev") == "psx0" for r in routes)
        if evidence["interface"]["carrier"] == "1" and has_address and has_route:
            return outcome("PASS", "Carrier, address and route observed after the selected helper; traffic and repeated-boot gates remain", evidence)
        return outcome("INCONCLUSIVE", "Helper returned success, but carrier/address/route evidence is incomplete", evidence)

    def https(self, params):
        interface = params["interface"]
        before = self.network(interface)
        if self.fs.exists("/sys/class/net/" + interface + "/wireless"):
            return outcome("BLOCKED", "Select a wired interface for the Ethernet test", before)
        try:
            mts = json.loads(before.get("mts_report") or "null")
        except ValueError:
            mts = None
        if isinstance(mts, dict) and mts.get("terminal_fault"):
            return outcome("BLOCKED", "Native Ethernet reports a terminal fault; retain state and recover externally", before)
        if interface == "psx0" and (not isinstance(mts, dict) or mts.get("initializing_or_going") or
                                   any(mts.get(key) != 1 for key in ("bound", "published", "engine_live", "admin_up"))):
            return outcome("BLOCKED", "The native driver report is missing or not LIVE/published/up; inspect status before traffic", before)
        if not before["present"] or before["carrier"] != "1" or before["operstate"] != "up":
            return outcome("BLOCKED", "Select an already connected interface; this action never opens or arms it", before)
        curl, ip = self.fs.tool("curl"), self.fs.tool("ip")
        if not curl or not ip:
            return outcome("NOT_RUN", "Both curl and ip are required for a bound transfer and route evidence")
        evidence = {"before": before, **self.ip_evidence(interface)}
        result = self.command([curl, "-q", "--silent", "--show-error", "--fail", "--noproxy", "*",
                               "--proto", "=https", "--connect-timeout", "10", "--max-time", "30",
                               "--max-filesize", "16777216", "--interface", "if!" + interface,
                               "--output", "/dev/null", "--write-out", "%{json}\n", "--url", params["url"]], 35)
        evidence["after"] = self.network(interface)
        if result.timed_out or result.interrupted or result.output_limited:
            return outcome("INCONCLUSIVE", "HTTPS process timed out/interrupted; raw output preserved", evidence)
        try:
            transfer = json.loads(result.stdout)
        except ValueError:
            transfer = {}
        if not isinstance(transfer, dict):
            transfer = {}
        evidence["transfer"] = transfer
        remote = transfer.get("remote_ip")
        if isinstance(remote, str) and remote:
            try:
                ipaddress.ip_address(remote)
                route = self.command([ip, "-j", "route", "get", remote, "oif", interface], 5)
                evidence["peer_route"] = json.loads(route.stdout) if route.returncode == 0 else None
            except ValueError:
                evidence["peer_route"] = None
        code = transfer.get("http_code", transfer.get("response_code", 0))
        if result.returncode == 0 and type(code) is int and 200 <= code < 300 and transfer.get("ssl_verify_result") == 0:
            routes = evidence.get("peer_route")
            if not isinstance(routes, list) or not any(isinstance(r, dict) and r.get("dev") == interface for r in routes):
                return outcome("INCONCLUSIVE", "HTTPS succeeded, but peer-route evidence is incomplete", evidence)
            return outcome("PASS", "One interface-bound HTTPS request passed TLS and returned 2xx; not Ethernet stability qualification", evidence)
        return outcome("FAIL", "Selected HTTPS request failed; server, DNS, routing and driver causes remain distinct", evidence)

    def graphics(self, test_id):
        name = "eglinfo" if test_id == "gpu.opengl.info" else "vulkaninfo"
        tool = self.fs.tool(name)
        if not tool:
            return outcome("NOT_RUN", name + " is not installed; no hardware failure inferred")
        args = ["-B", "-p", "surfaceless"] if name == "eglinfo" else ["--summary"]
        result = self.command([tool, *args], 30, {"EGL_PLATFORM": "surfaceless"})
        if result.timed_out or result.interrupted or result.output_limited:
            return outcome("INCONCLUSIVE", "Graphics collection did not complete; stop graphics work and inspect external evidence", stop_required=True)
        return outcome("INCONCLUSIVE", "Graphics tool output collected; API/renderer enumeration has no rendered-pixel oracle",
                       {"tool": name, "returncode": result.returncode, "offscreen_requested": True,
                        "software_vs_hardware": "Inspect recorded vendor/device/renderer; neither is assumed"})

    def play_audio(self, params):
        manifest = self.fs.text(self.manifest_path)
        fixtures = json.loads(manifest or "{}").get("fixtures", {})
        fixtures = fixtures if isinstance(fixtures, dict) else {}
        declared = fixtures.get("audio_44100", {})
        declared = declared if isinstance(declared, dict) else {}
        fixture = params.get("fixture") or declared.get("path")
        if not fixture:
            return outcome("NOT_RUN", "No known 44.1 kHz WAV fixture selected or supplied in the manifest")
        path = self.fs.local(fixture)
        if not path.exists():
            return outcome("NOT_RUN", "The selected WAV fixture is not present")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 20 * 1024 * 1024:
            return outcome("BLOCKED", "Fixture must be a regular WAV file of at most 20 MiB")
        checksum = digest(path.read_bytes())
        if declared.get("path") == fixture and declared.get("sha256") and checksum != declared["sha256"]:
            return outcome("BLOCKED", "WAV fixture does not match its manifest hash")
        try:
            with wave.open(str(path), "rb") as wav:
                rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
                duration = wav.getnframes() / rate
                if wav.getcomptype() != "NONE" or rate != 44100 or channels not in (1, 2) or width not in (2, 4) or not 0 < duration <= 20:
                    return outcome("BLOCKED", "Use a 44.1 kHz mono/stereo PCM16/PCM32 WAV lasting at most 20 seconds")
        except (wave.Error, EOFError, ZeroDivisionError) as exc:
            return outcome("BLOCKED", "Invalid WAV fixture: " + str(exc))
        tool = self.fs.tool("aplay")
        if not tool:
            return outcome("NOT_RUN", "aplay is not installed")
        result = self.command([tool, "--quiet", "--device", params["audio_device"], "--file-type", "wav", "--", str(path)], 30)
        evidence = {"fixture_sha256": checksum, "sample_rate": rate, "channels": channels,
                    "sample_width_bytes": width, "duration_seconds": duration,
                    "audio_device": params["audio_device"], "alsa": self.audio_info()}
        if result.returncode != 0 or result.timed_out or result.interrupted or result.output_limited:
            return outcome("INCONCLUSIVE", "Playback did not complete; inspect ALSA/device errors", evidence)
        return outcome("INCONCLUSIVE", "Playback completed; confirm audible output, pitch and duration with observe or the TUI",
                       evidence, observation_required="audible-correct-pitch-and-duration")

    def observe(self, run_id, answer, note=""):
        if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}", run_id):
            raise ValueError("Invalid run ID")
        if answer not in ("yes", "no", "unsure"):
            raise ValueError("Observation answer must be yes, no or unsure")
        with self.lock():
            path = self.results_dir / run_id / "result.json"
            record = read_json(path)
            if record.get("test_id") != "audio.44100" or not record.get("observation_required"):
                raise ValueError("This result does not request an audio observation")
            if record["identity"]["boot_id"] != self.identity()["boot_id"]:
                raise ValueError("This audio observation belongs to another boot")
            observation = {"answer": answer, "note": note[:2000], "timestamp": timestamp(), "source": "operator"}
            record["operator_observation"] = observation
            record["status"] = {"yes": "PASS", "no": "FAIL", "unsure": "INCONCLUSIVE"}[answer]
            record["message"] = "Operator observation of this fixture: " + answer + "; not native 48 kHz or broad audio acceptance"
            log = Evidence(path.parent, self.callback)
            with (path.parent / "journal.jsonl").open() as stream:
                log.sequence = sum(1 for _ in stream)
            log.event("operator_observation", **observation)
            atomic_json(path, record)
            return record


def worker_main(kind, amount):
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (15, 16))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    if kind == "cpu":
        if not 1 <= amount <= 10:
            return 2
        started = time.monotonic()
        iterations = 0
        payload = b"psx-diagnostics-known-answer-v1" * 4096
        answer = None
        while time.monotonic() - started < amount:
            answer = digest(payload)
            if answer != CPU_ANSWER:
                return 1
            iterations += 1
        print(json.dumps({"digest": answer, "iterations": iterations, "seconds": amount, "workers": 1}))
    elif kind == "memory":
        if not 1 <= amount <= 128:
            return 2
        block = bytearray(amount * 1024 * 1024)
        for pattern in (0, 85, 170, 255):
            chunk = bytes([pattern]) * (1024 * 1024)
            for offset in range(0, len(block), len(chunk)):
                block[offset:offset + len(chunk)] = chunk
            for offset in range(0, len(block), len(chunk)):
                if block[offset:offset + len(chunk)] != chunk:
                    return 1
        print(json.dumps({"verified_mib": amount, "patterns": [0, 85, 170, 255], "scope": "owned virtual allocation"}))
    else:
        return 2
    return 0


def print_list(catalogue):
    print("psX Diagnostics " + catalogue["suite_version"] + " — select a test; nothing starts automatically")
    for index, test in enumerate(catalogue["tests"], 1):
        print(f"{index:2}. {test['title']:<31} {test['id']:<24} [{test['kind']}]")


def menu(runner):
    if not sys.stdin.isatty():
        print_list(runner.catalogue)
        return 0
    while True:
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="")
        identity = runner.identity()
        print("PSX  /  DIAGNOSTICS\n")
        print(clean_terminal("Kernel: " + str(identity["kernel_release"]) + "  Boot: " + str(identity["boot_id"])))
        print(clean_terminal("Results: " + str(runner.results_dir or "choose --results-dir before running")))
        print("Observations ≠ functional passes ≠ hardware acceptance. Local reports need review before sharing.\n")
        current_results = [r for r in runner.results() if r.get("identity", {}).get("boot_id") == identity["boot_id"]]
        latest = {r["test_id"]: r for r in reversed(current_results)}
        inventory = latest.get("inventory.readonly", {}).get("evidence", {})
        if inventory:
            processors = inventory.get("cpu", {}).get("listed_processors", "?")
            memory = inventory.get("memory", {}).get("MemTotal_kib", 0) / (1024 * 1024)
            print(f"┌ CPU: {processors} listed  ·  RAM: {memory:.2f} GiB  ·  PCI: {len(inventory.get('pci', []))}  ·  USB: {len(inventory.get('usb', []))}")
            print("└ Recorded inventory on this boot; device existence is not a functional test.\n")
        else:
            print("Collect Hardware inventory to populate this boot's hardware overview.\n")
        print("     HARDWARE / ACTION                       THIS BOOT")
        for index, test in enumerate(runner.catalogue["tests"], 1):
            status = latest.get(test["id"], {}).get("status", "NOT_RUN")
            label = f"{test['area']} / {test['title']}"
            print(f"{index:2}  {label:<42} {status}")
        choice = input("\nSelect number · a quick check · r results · q quit: ").strip().lower()
        if choice in ("q", "quit"):
            return 0
        if choice == "r":
            for result in runner.results()[:20]:
                print(clean_terminal(f"{result['run_id']}  {result['test_id']}  {result['status']}\n  {result['message']}"))
            input("\nEnter to return: ")
            continue
        try:
            if choice == "a":
                print("\nQuick check: inventory → 2-second CPU computation → 32 MiB owned-memory patterns.")
                if input("Run this bounded sequence once? [y/N]: ").strip().lower() != "y":
                    continue
                reason = ""
                if any(runner.needs_retry_reason(test_id) for test_id, _ in QUICK_STEPS):
                    reason = input("Changed evidence since the previous failed action: ").strip()
                print("Running the quick checks; the sequence stops at a failed or blocked action.", flush=True)
                result = runner.quick(reason)
                print(clean_terminal(result["status"] + ": " + result["message"]))
                print(clean_terminal("Saved individual reports: " + result["result_directory"]))
                input("\nEnter to return: ")
                continue
            index = int(choice) - 1
            if not 0 <= index < len(runner.catalogue["tests"]):
                raise ValueError("Choose a listed test")
            test = runner.catalogue["tests"][index]
            print("\n" + test["description"])
            params = {}
            if test["id"] == "ethernet.https":
                params = {"interface": input("Interface [psx0]: ").strip() or "psx0",
                          "url": input("Explicit HTTPS URL: ").strip()}
            elif test["id"] == "audio.44100":
                fixture = input("WAV path [manifest fixture]: ").strip()
                if fixture:
                    params["fixture"] = fixture
                params["audio_device"] = input("ALSA device [hw:0,3]: ").strip() or "hw:0,3"
            if test["id"] in ("ethernet.connect", "ethernet.https", "audio.44100") or test["id"].startswith("gpu."):
                if input("Run this selected action once? [y/N]: ").strip().lower() != "y":
                    continue
            reason = ""
            if runner.needs_retry_reason(test["id"]):
                reason = input("Changed evidence since the previous failed action: ").strip()
            print(f"Running {test['id']} (command budget up to {test['timeout_seconds']} s)…", flush=True)
            result = runner.run(test["id"], params, reason)
            print(clean_terminal(result["status"] + ": " + result["message"]))
            if result.get("observation_required"):
                answer = input("Was the fixture audible with correct pitch and duration? [yes/no/unsure]: ").strip().lower()
                if answer in ("yes", "no", "unsure"):
                    result = runner.observe(result["run_id"], answer)
                    print(result["status"])
            print(clean_terminal("Saved: " + result["result_directory"]))
        except (ValueError, OSError) as exc:
            print(clean_terminal("Cannot run: " + str(exc)))
        except KeyboardInterrupt:
            print("\nInterrupted; inspect the latest journal.")
        input("\nEnter to return: ")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", help="Explicit local directory for private journals/results; required for run")
    parser.add_argument("--manifest", default=MANIFEST_PATH)
    parser.add_argument("--root-prefix", default="/", help="Synthetic filesystem root; disables real subprocesses")
    parser.add_argument("--session-receipt", help="Logical path to an external UART/session JSON receipt")
    parser.add_argument("--json", action="store_true", help="Print the final result as JSON")
    parser.add_argument("--events", action="store_true", help="Print versioned journal events as JSON lines")
    parser.add_argument("--catalogue", action="store_true", help="Print catalogue JSON and exit without probing")
    commands = parser.add_subparsers(dest="command")
    for name in ("list", "catalogue", "menu", "inventory", "results"):
        commands.add_parser(name)
    quick = commands.add_parser("quick", help="Inventory, 2-second CPU computation and 32 MiB owned-memory test only")
    quick.add_argument("--retry-reason", default="")
    run = commands.add_parser("run")
    run.add_argument("test_id", choices=sorted(PARAMETERS))
    for name in ("interface", "url", "fixture", "audio-device"):
        run.add_argument("--" + name)
    run.add_argument("--seconds", type=int)
    run.add_argument("--memory-mib", type=int)
    run.add_argument("--retry-reason", default="")
    observe = commands.add_parser("observe")
    observe.add_argument("run_id")
    observe.add_argument("--answer", required=True, choices=("yes", "no", "unsure"))
    observe.add_argument("--note", default="")
    args = parser.parse_args(argv)
    catalogue = load_catalogue()
    if args.catalogue or args.command == "catalogue":
        print(json.dumps(catalogue, indent=2))
        return 0
    action = args.command or ("menu" if sys.stdin.isatty() else "list")
    if action == "list":
        if args.json:
            print(json.dumps(catalogue))
        else:
            print_list(catalogue)
        return 0
    def event(record):
        if args.events:
            print(json.dumps(record), flush=True)
        elif action == "menu" and record["event"] in ("command_started", "command_progress", "command_completed"):
            if record["event"] == "command_started":
                print(clean_terminal("  Running " + Path(record["argv"][0]).name + f" (≤{record['timeout_seconds']} s)"), flush=True)
            elif record["event"] == "command_progress":
                print(f"\r  Working: {record['elapsed_seconds']:.1f} / {record['timeout_seconds']} s", end="", flush=True)
            else:
                print(f"\r  Command finished: rc={record['returncode']} timeout={record['timed_out']}        ", flush=True)
    runner = Runner(args.results_dir, args.root_prefix, args.manifest, args.session_receipt, callback=event)
    try:
        if action == "menu":
            return menu(runner)
        if action == "results":
            print(json.dumps(runner.results(), indent=2))
            return 0
        if action == "observe":
            result = runner.observe(args.run_id, args.answer, args.note)
        elif action == "quick":
            result = runner.quick(args.retry_reason)
        elif action == "inventory":
            result = runner.run("inventory.readonly")
        else:
            params = {key: getattr(args, key) for key in
                      ("interface", "url", "fixture", "audio_device", "seconds", "memory_mib")
                      if getattr(args, key, None) is not None}
            result = runner.run(args.test_id, params, args.retry_reason)
        if args.json or args.events:
            print(json.dumps(result, sort_keys=True))
        else:
            print(clean_terminal(result["status"] + ": " + result["message"]))
            print(clean_terminal("Saved: " + result["result_directory"]))
        return 1 if result["status"] == "FAIL" else 2 if result["status"] == "BLOCKED" else 0
    except EOFError:
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}) if args.json else clean_terminal(str(exc)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "_worker":
        raise SystemExit(worker_main(sys.argv[2], int(sys.argv[3])))
    raise SystemExit(main())
