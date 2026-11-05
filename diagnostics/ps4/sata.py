#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Explicit alpha5 SATA read repeatability; default inert, no raw data retained.

run(runner) is for a future saved controller job holding runner.lock(). The CLI
holds that same lock. PASS means repeatable reads only, not known-good contents,
NCQ/load/endurance qualification, write support or hardware acceptance.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import importlib.util
import json
import mmap
import os
from pathlib import Path
import re
import select
import signal
import stat
import struct
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
PROFILE = "ps4-baikal-diagnostics-uvd-alpha5"
RELEASE = "7.2.11-psx-ps4-baikal-uvd-runtime-dev4"
PCI = "/sys/bus/pci/devices/0000:00:14.7"
TEST_ID = "storage.sata.read-repeatability"
SPAN = 8 * 1024 * 1024
WORKER_SECONDS = 60
PARENT_SECONDS = 180
JOURNAL_LIMIT = 4 * 1024 * 1024
RESULTS = "/run/psx-media/psx-diagnostics/results"
ARGUMENTS = ("libata.force=1:1.5Gbps", "libahci.ps4_ahci_poll_ms=0",
             "libahci.ps4_probe_ms=5", "ps4_bpcie.ps4_sata_phy_orbit=1", "systemd.gpt_auto=0")
FAULTS = {
    "ata-error-recovery": re.compile(r"\bata[0-9]+(?:\.[0-9]+)?:[^\n]*(?:failed|exception|\berror\b|Emask|SError|timed? ?out|hard resetting|softreset|frozen|disabled)", re.I),
    "storage-io-error": re.compile(r"(?:Buffer )?I/O error|illegal qc_active transition|ps4: (?:completion probe stopped|probe refused)", re.I),
    "kernel-terminal": re.compile(r"Kernel panic|Oops:|\bBUG:|general protection fault", re.I),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


class KernelFault(ValueError):
    """A terminal kernel observation, including before any requested data I/O."""


def module(name):
    spec = importlib.util.spec_from_file_location("psx_sata_" + name, HERE / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def number(fs, path, minimum=0):
    value = fs.text(path)
    require(isinstance(value, str) and re.fullmatch(r"[0-9]+", value), "Required numeric sysfs attribute is missing")
    value = int(value)
    require(minimum <= value < 2**63, "Numeric sysfs attribute is outside its bound")
    return value


def discover(fs, deadline=None):
    """Select through topology, never a remembered sd name; read metadata only."""
    require(fs.text(PCI + "/vendor") == "0x104d" and fs.text(PCI + "/device") == "0x90de",
            "Exact Baikal embedded controller is absent")
    controller = fs.local(PCI)
    nodes = list(fs.local("/sys/class/block").iterdir())
    require(len(nodes) <= 1024, "Block topology exceeds the inspection bound")
    candidates = []
    for node in nodes:
        require(deadline is None or time.monotonic() < deadline, "Parent deadline expired")
        resolved = node.resolve(strict=True)
        require(resolved.is_relative_to(fs.root), "Block symlink escapes the selected root")
        if not resolved.is_relative_to(controller) or (resolved / "partition").exists():
            continue
        relative = resolved.relative_to(controller).parts
        if relative and re.fullmatch(r"ata[1-9][0-9]*", relative[0]):
            candidates.append((node.name, resolved, relative[0]))
    require(len(candidates) == 1, "Exactly one whole internal ATA disk is required")
    name, disk, ata = candidates[0]
    require(re.fullmatch(r"sd[a-z]+", name) is not None, "Unexpected ATA block device name")
    port = "/sys/class/ata_port/" + ata
    require(fs.local(port).is_relative_to(controller / ata), "ATA port belongs to a different controller")
    # libata-transport.c exports ap->port_no + 1, not its zero-based value.
    require(number(fs, port + "/port_no") == 1 and number(fs, port + "/nr_pmp_links") == 0,
            "Hardware-local ATA port 0 without a port multiplier is required")
    base = "/sys/class/block/" + name
    require(fs.text(base + "/device/type") == "0" and fs.text(base + "/removable") == "0",
            "Target is not a non-removable direct-access ATA disk")
    sectors = number(fs, base + "/size", 1)
    logical = number(fs, base + "/queue/logical_block_size", 512)
    physical = number(fs, base + "/queue/physical_block_size", logical)
    require(logical in (512, 4096) and physical <= 1024 * 1024 and physical & (physical - 1) == 0,
            "Unsupported block geometry")
    sequence = number(fs, base + "/diskseq", 1)
    members = []
    for node in nodes:
        resolved = node.resolve(strict=True)
        if resolved == disk or resolved.is_relative_to(disk):
            require(resolved == disk or (resolved / "partition").is_file(), "Unexpected child block device")
            require((resolved / "ro").read_text().strip() == "1", "Disk and every partition must already be read-only")
            holders = resolved / "holders"
            require(holders.is_dir() and not list(holders.iterdir()), "Internal disk or partition has active holders")
            dev = (resolved / "dev").read_text().strip()
            require(re.fullmatch(r"[0-9]+:[0-9]+", dev) is not None, "Missing block device number")
            members.append((node.name, dev))
    devnums = {value for _, value in members}
    require(len(devnums) == len(members) and name in dict(members), "Ambiguous block device numbers")
    # Check all visible process mount namespaces, including PID 1. A hidden or
    # unreadable namespace is a refusal, not evidence that the disk is unused.
    processes = [p for p in fs.local("/proc").iterdir() if p.name.isdigit()]
    require(0 < len(processes) <= 4096, "Process mount inventory is missing or too large")
    for process in processes:
        require(deadline is None or time.monotonic() < deadline, "Parent deadline expired")
        try:
            with (process / "mountinfo").open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.EINVAL):
                raise
            if process.exists():
                # proc_namespace.c rejects tasks without nsproxy or fs. Kernel
                # threads and exited zombies may legitimately have no mountinfo.
                with (process / "status").open("rb") as stream:
                    status = stream.read(65537)
                require(len(status) <= 65536 and
                        (re.search(rb"^Kthread:\s+1$", status, re.M) or
                         re.search(rb"^State:\s+[ZX](?:\s|$)", status, re.M)),
                        "A live userspace process mount namespace is unavailable")
            continue
        require(len(raw) <= 1024 * 1024, "Mount namespace exceeds its inspection bound")
        for line in raw.decode("utf-8", "strict").splitlines():
            fields = line.split()
            require(len(fields) >= 10 and "-" in fields, "Malformed mount inventory")
            require(fields[2] not in devnums, "An internal disk member is mounted")
    swaps = fs.text("/proc/swaps")
    require(isinstance(swaps, str) and len(swaps.splitlines()) == 1 and swaps.startswith("Filename"),
            "This bounded SATA test requires no active swap")
    return {"name": name, "node": "/dev/" + name, "sysfs_path": "/" + str(disk.relative_to(fs.root)),
            "controller": "0000:00:14.7", "hardware_port": 0, "ata": ata,
            "dev": dict(members)[name], "diskseq": sequence, "bytes": sectors * 512,
            "logical_sector_bytes": logical, "physical_sector_bytes": physical,
            "members": sorted(members)}


def preflight(runner, deadline=None):
    uvd, qualification = module("uvd"), module("qualification")
    identity = runner.identity()
    require(identity.get("release_matches") is True and identity.get("kernel_release") == RELEASE,
            "Exact running dev4 kernel/catalogue identity is required")
    require(str(uuid.UUID(identity["boot_id"])) == identity["boot_id"], "Canonical boot ID is required")
    manifest = uvd.document(uvd.local(runner, runner.manifest_path), runner)
    selected = uvd.select_contract(manifest)
    require(selected["profile"] == PROFILE, "SATA reads require the exact frozen alpha5 manifest")
    validation = manifest.get("manifest_validation", {})
    require(validation.get("embedded_root_contract_verified") is True and
            validation.get("observed_kernel_release") == RELEASE and
            validation.get("observed_media_initramfs_sha256") == manifest.get("initramfs_sha256") and
            all(re.fullmatch(r"[a-f0-9]{64}", str(manifest.get(key, ""))) for key in
                ("rootfs_sha256", "initramfs_sha256")), "Verified root/boot-set contract is missing")
    require(runner.uvd_stop_reason() is None, "UVD terminal state blocks subsequent active work")
    tokens = (runner.fs.text("/proc/cmdline") or "").split()
    for argument in ARGUMENTS + tuple(uvd.POWEROFF_BOOT_ARGUMENTS):
        key = argument.split("=", 1)[0]
        require([t for t in tokens if t.split("=", 1)[0] == key] == [argument],
                "Required SATA/UVD boot policy is missing, conflicting or duplicated")
    require(not any(t.startswith("libahci.ps4_ahci_completion_probe_ms=") for t in tokens),
            "The long probe alias must not override the frozen short spelling")
    effective = {"/sys/module/libahci/parameters/ps4_probe_ms": "5",
                 "/sys/module/libahci/parameters/ps4_ahci_poll_ms": "0",
                 "/sys/module/ps4_bpcie/parameters/ps4_sata_phy_orbit": "Y"}
    for path, value in effective.items():
        require(runner.fs.text(path) in (("1", "Y") if value == "Y" else (value,)),
                "Effective SATA parameter differs")
    for argument in uvd.POWEROFF_BOOT_ARGUMENTS:
        require(runner.fs.text("/sys/module/amdgpu/parameters/" + argument.split(".", 1)[1].split("=", 1)[0]) in ("1", "Y"),
                "Effective UVD gate differs")
    require(runner.results_dir is not None, "Existing core receipts are required")
    history = []
    paths = list(runner.results_dir.glob("*/result.json"))
    require(len(paths) <= 4096, "Core history exceeds the inspection bound")
    for path in paths:
        require(deadline is None or time.monotonic() < deadline, "Parent deadline expired")
        record = uvd.document(path, runner, private=True)
        if record.get("test_id") in ("cpu.compute", "memory.pattern") and not record.get("prerequisite_blocked"):
            require(isinstance(record.get("identity"), dict), "Core receipt has no valid identity")
            if record["identity"].get("boot_id") == identity["boot_id"]:
                history.append(record)
    versions = {key: runner.tests[key]["version"] for key in ("cpu.compute", "memory.pattern")}
    core = []
    for test_id in versions:
        prior = sorted((r for r in history if r["test_id"] == test_id), key=lambda r: str(r.get("started_at", "")), reverse=True)
        require(prior and qualification.foundation_pass(prior[0], identity, versions),
                "Latest CPU and RAM receipts must be valid same-boot PASS results")
        core.append(prior[0].get("run_id"))
    require(all(identity.get(k) for k in qualification.IDENTITY_PINS), "Complete core identity pins are required")
    target = discover(runner.fs, deadline)
    require(runner.identity() == identity, "Boot/artifact identity changed during preflight")
    return identity, target, core


def positions(target):
    alignment = max(4096, target["physical_sector_bytes"], target["logical_sector_bytes"])
    size = target["bytes"]
    require(size < 2**63 and size >= 3 * SPAN and SPAN % alignment == 0, "Disk cannot contain three aligned bounded regions")
    offsets = (0, ((size - SPAN) // 2) // alignment * alignment, (size - SPAN) // alignment * alignment)
    require(offsets[1] >= SPAN and offsets[2] >= offsets[1] + SPAN, "Read regions overlap")
    return offsets


def ioctl_number(fd, request, fmt):
    raw = bytearray(struct.calcsize(fmt))
    fcntl.ioctl(fd, request, raw, True)
    return struct.unpack(fmt, raw)[0]


def read_direct(target, fs):
    """Read-only fd + O_DIRECT + page-aligned mmap; no fallback to cached I/O."""
    require(discover(fs) == target, "Target changed before open")
    fd = os.open(fs.local(target["node"]), os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_DIRECT)
    try:
        info = os.fstat(fd)
        major, minor = map(int, target["dev"].split(":"))
        require(stat.S_ISBLK(info.st_mode) and info.st_rdev == os.makedev(major, minor), "Opened descriptor is not the selected block device")
        require(ioctl_number(fd, 0x80081272, "=Q") == target["bytes"] and
                ioctl_number(fd, 0x1268, "=I") == target["logical_sector_bytes"], "Opened device geometry changed")
        offsets, hashes = positions(target), [[], []]
        started = time.monotonic()
        with mmap.mmap(-1, SPAN) as buffer:
            view = memoryview(buffer)
            try:
                for iteration in range(2):
                    for offset in offsets:
                        require(time.monotonic() - started < WORKER_SECONDS, "Read worker deadline expired")
                        require(discover(fs) == target, "Topology or read-only/use state changed")
                        require(ioctl_number(fd, 0x125e, "=I") == 1 and
                                ioctl_number(fd, 0x80081280, "=Q") == target["diskseq"],
                                "Opened device read-only state or generation changed")
                        count = os.preadv(fd, [view], offset)
                        require(count == SPAN, "Short direct read; no retry is permitted")
                        hashes[iteration].append(hashlib.sha256(view).hexdigest())
            finally:
                view.release()
        require(discover(fs) == target, "Target changed after direct reads")
        require(hashes[0] == hashes[1], "Direct-read hashes disagree")
        return {"offsets_bytes": list(offsets), "region_bytes": SPAN, "passes": 2,
                "total_read_bytes": 6 * SPAN, "sha256": hashes, "host_page_cache_bypassed": True,
                "method": "O_RDONLY|O_DIRECT + aligned mmap + preadv", "elapsed_seconds": round(time.monotonic() - started, 3)}
    finally:
        os.close(fd)


def fork_read(target, fs, deadline):
    require(not fs.fixture and fs.root == Path("/"), "Fixture roots never perform real block I/O")
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            os.setsid()
            def timeout(_signal, _frame):
                raise TimeoutError("Read worker deadline expired")
            signal.signal(signal.SIGALRM, timeout)
            signal.alarm(WORKER_SECONDS)
            value = {"ok": True, "evidence": read_direct(target, fs)}
        except BaseException as error:
            value = {"ok": False, "error": type(error).__name__, "errno": getattr(error, "errno", None)}
        raw = json.dumps(value, allow_nan=False).encode()
        try:
            require(len(raw) <= 4096, "Worker receipt exceeds bound")
            os.write(write_fd, raw)
        finally:
            os.close(write_fd)
            os._exit(0)
    os.close(write_fd)
    raw = bytearray()
    end = min(deadline, time.monotonic() + WORKER_SECONDS)
    reaped = False
    try:
        while time.monotonic() < end:
            readable, _, _ = select.select([read_fd], [], [], min(0.05, max(0, end - time.monotonic())))
            if readable:
                part = os.read(read_fd, 4097 - len(raw))
                raw.extend(part)
                require(len(raw) <= 4096, "Worker receipt exceeds bound")
            child, status = os.waitpid(pid, os.WNOHANG)
            if child:
                reaped = True
                require(os.waitstatus_to_exitcode(status) == 0, "Read worker exited abnormally")
                # Drain the completed writer; only a small typed hash receipt exists.
                raw.extend(os.read(read_fd, 4097 - len(raw)))
                require(len(raw) <= 4096, "Worker receipt exceeds bound")
                value = json.loads(raw)
                require(value.get("ok") is True, "Read worker failed or timed out; inspect kernel evidence")
                return value["evidence"]
        raise TimeoutError("Read worker exceeded 60 seconds; stop, no retry")
    finally:
        os.close(read_fd)
        if not reaped:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # Never wait indefinitely for a task stuck in kernel I/O. The caller
            # receives stop_required; an in-flight kernel request may outlive kill.
            until = time.monotonic() + 0.25
            while time.monotonic() < until:
                if os.waitpid(pid, os.WNOHANG)[0]:
                    break
                time.sleep(0.01)


def kernel_snapshot(runner, deadline):
    require(not runner.fs.fixture, "Fixture roots never execute a real journal reader")
    tool = runner.fs.tool("journalctl")
    require(tool is not None, "Current-boot kernel journal reader is absent")
    limit = min(5, deadline - time.monotonic())
    require(limit > 0, "Parent deadline expired")
    process = subprocess.Popen([tool, "-k", "-b", "--no-pager", "--quiet", "-o", "cat", "-n", "20000"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, close_fds=True,
                               env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
    collected = {process.stdout.fileno(): bytearray(), process.stderr.fileno(): bytearray()}
    pending = set(collected)
    end = time.monotonic() + limit
    try:
        for descriptor in pending:
            os.set_blocking(descriptor, False)
        while pending:
            require(time.monotonic() < end, "Kernel journal deadline expired")
            ready, _, _ = select.select(list(pending), [], [], min(0.05, max(0, end - time.monotonic())))
            for descriptor in ready:
                data = os.read(descriptor, 16384)
                collected[descriptor].extend(data)
                require(len(collected[descriptor]) <= JOURNAL_LIMIT, "Kernel journal exceeded its memory bound")
                if not data:
                    pending.remove(descriptor)
        process.wait(timeout=max(0.01, end - time.monotonic()))
        output, error = (bytes(collected[p.fileno()]) for p in (process.stdout, process.stderr))
        require(process.returncode == 0 and not error and output, "Current-boot kernel journal is incomplete")
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
        process.stdout.close(); process.stderr.close()
    text = output.decode("utf-8", "strict")
    require(len(text.splitlines()) < 20000, "Kernel journal window was truncated")
    uvd = module("uvd")
    if uvd.KERNEL_FAULT.search(text):
        raise KernelFault("Kernel UVD/GPU terminal state requires recovery")
    if any(pattern.search(text) for pattern in FAULTS.values()):
        raise KernelFault("Kernel storage/EH or terminal fault requires recovery")
    require("ps4: scoped completion probe=5 ms, legacy poll retired" in text,
            "Candidate completion-probe activation is not evidenced")
    return {"sha256": hashlib.sha256(output).hexdigest(), "bytes": len(output),
            "faults_detected": False, "raw_retained": False}


def validate_reads(value, target):
    require(isinstance(value, dict) and value.get("offsets_bytes") == list(positions(target)) and
            type(value.get("region_bytes")) is int and value["region_bytes"] == SPAN and
            type(value.get("passes")) is int and value["passes"] == 2 and
            type(value.get("total_read_bytes")) is int and value["total_read_bytes"] == 6 * SPAN and
            value.get("host_page_cache_bypassed") is True and
            value.get("method") == "O_RDONLY|O_DIRECT + aligned mmap + preadv",
            "Worker did not establish the exact bounded direct-read contract")
    hashes = value.get("sha256")
    require(isinstance(hashes, list) and len(hashes) == 2 and hashes[0] == hashes[1] and
            all(isinstance(row, list) and len(row) == 3 and
                all(isinstance(v, str) and re.fullmatch(r"[a-f0-9]{64}", v) for v in row) for row in hashes),
            "Worker repeatability hashes are missing or disagree")


def run(runner):
    """Explicit selected operation; caller must hold the shared runner lock."""
    started = time.monotonic()
    deadline = started + PARENT_SECONDS
    evidence, invoked = {}, False
    result = {"test_id": TEST_ID, "status": "BLOCKED", "hardware_acceptance": False,
              "stop_required": False, "scope": "read repeatability only; no known-good-content, NCQ, endurance or write qualification"}
    try:
        identity, target, core = preflight(runner, deadline)
        result["identity"] = identity
        evidence.update(target=target, core_run_ids=core, worker_timeout_seconds=WORKER_SECONDS,
                        parent_timeout_seconds=PARENT_SECONDS)
        evidence["kernel_before"] = kernel_snapshot(runner, deadline)
        require(time.monotonic() < deadline, "Parent deadline expired")
        require(runner.uvd_stop_reason() is None, "UVD terminal state appeared before SATA reads")
        invoked = True
        evidence["reads"] = fork_read(target, runner.fs, deadline)
        validate_reads(evidence["reads"], target)
        evidence["kernel_after"] = kernel_snapshot(runner, deadline)
        require(runner.identity() == identity and runner.uvd_stop_reason() is None and discover(runner.fs, deadline) == target,
                "Boot, UVD or storage identity changed during the read test")
        require(time.monotonic() < deadline, "Parent deadline expired")
        result.update(status="PASS", message="Three 8 MiB regions matched on two direct reads; 48 MiB total, no content reference was available")
    except (OSError, ValueError, KeyError, TypeError, TimeoutError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        result.update(status="INCONCLUSIVE" if invoked else "BLOCKED", stop_required=invoked or isinstance(error, KernelFault),
                      message="SATA test stopped: " + str(error)[:500])
        if invoked:
            try:
                evidence["kernel_after_failure"] = kernel_snapshot(runner, deadline)
            except (OSError, ValueError, subprocess.SubprocessError):
                evidence["kernel_after_failure"] = {"available_and_clean": False, "raw_retained": False}
    result.update(evidence=evidence, elapsed_seconds=round(time.monotonic() - started, 3))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly read 48 MiB from the identified read-only internal disk")
    parser.add_argument("--output", type=Path, help="New private JSON report; existing files are refused")
    parser.add_argument("--results-dir", type=Path, default=Path(RESULTS), help="Existing same-boot CPU/RAM reports")
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({"status": "NOT_RUN", "message": "Inert: use --run --output NEW.json inside an active bounded UART experiment", "hardware_acceptance": False}))
        return 0
    if args.output is None:
        parser.error("--run requires --output NEW.json")
    output = args.output.absolute()
    require(output.parent.resolve(strict=True) == output.parent and not output.exists() and not output.is_symlink(),
            "Output must be a new file in a canonical existing directory")
    diagnostics = module("runner")
    runner = diagnostics.Runner(args.results_dir)
    # The report is the only writable file; no disk data or serial is included.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        try:
            with runner.lock():
                result = run(runner)
        except (OSError, ValueError) as error:
            result = {"status": "BLOCKED", "message": str(error)[:500], "hardware_acceptance": False,
                      "stop_required": False, "test_id": TEST_ID}
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    print(json.dumps({"status": result["status"], "output": str(output), "hardware_acceptance": False}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
