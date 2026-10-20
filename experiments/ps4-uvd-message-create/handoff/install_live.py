#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exact-plan local installer. Writes require explicit owner/session assertions."""
from pathlib import Path, PurePosixPath
import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tarfile
import time

BOOT_FILES = {"bzImage", "bootargs.txt", "initramfs.cpio.gz", "release.json", "vram.txt"}
CHANGED_FILES = {"bzImage", "bootargs.txt"}
TOOLS = {"/usr/bin/findmnt", "/usr/bin/lsblk", "/usr/bin/zstd"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as inp:
        for block in iter(lambda: inp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def name_safe(name):
    p = PurePosixPath(name)
    require(name and not p.is_absolute() and ".." not in p.parts and p.as_posix() == name and
            name != "." and not any(ord(c) < 32 or c == "\\" for c in name), "Unsafe path: " + name)
    return name


def regular(path):
    require(stat.S_ISREG(path.lstat().st_mode), "Regular file required; links forbidden: " + str(path))
    return {"bytes": path.stat().st_size, "sha256": sha(path)}


def pin_shape(path):
    st = path.lstat()
    require(stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode), "Unsupported pinned input type")
    return {"mode": st.st_mode, "bytes": st.st_size,
            "target": os.readlink(path) if path.is_symlink() else None}


def inventory(root):
    rows = {}
    require(root.is_dir() and not root.is_symlink(), "Real directory required: " + str(root))
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            st = path.lstat()
            if stat.S_ISLNK(st.st_mode):
                rows[rel] = {"kind": "symlink", "target": os.readlink(path), "mode": st.st_mode & 0o777}
            elif not stat.S_ISDIR(st.st_mode):
                rows[rel] = {"kind": "file", "mode": st.st_mode & 0o777, **regular(path)}
    return rows


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save(path, value):
    with path.open("x") as out:
        json.dump(value, out, indent=2, sort_keys=True)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    fsync_directory(path.parent)


def copy_fsync(source, destination, expected, mode=None):
    require(regular(source) == expected, "Copy source differs: " + str(source))
    with source.open("rb") as inp, destination.open("xb") as out:
        for block in iter(lambda: inp.read(1024 * 1024), b""):
            out.write(block)
        if mode is not None:
            os.fchmod(out.fileno(), mode)
        out.flush()
        os.fsync(out.fileno())
    require(regular(destination) == expected, "Copy readback differs: " + str(destination))


def module_inventory(rows, release):
    prefix = "lib/modules/" + release + "/"
    require(isinstance(rows, dict) and rows, "Complete module inventory required")
    for name, row in rows.items():
        name_safe(name)
        require(name.startswith(prefix) and name != prefix and row.get("kind") == "file" and
                type(row.get("mode")) is int and 0 <= row["mode"] <= 0o777 and
                type(row.get("bytes")) is int and row["bytes"] >= 0 and
                re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")), "Invalid module record: " + name)
    return prefix


def read_modules(stream, rows, release, destination=None):
    """Validate complete direct lib/modules paths; optional manual regular-file extraction."""
    prefix = module_inventory(rows, release)
    seen = set()
    with tarfile.open(fileobj=stream, mode="r|", ignore_zeros=True) as archive:
        for item in archive:
            name = name_safe(item.name)
            require(name in rows and name not in seen, "Duplicate/unexpected module: " + name)
            seen.add(name)
            row = rows[name]
            require(item.isfile() and item.mode == row["mode"] and item.size == row["bytes"],
                    "Module type/mode/size differs: " + name)
            h = hashlib.sha256()
            path = destination / name[len(prefix):] if destination is not None else None
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(item) as inp, contextlib.ExitStack() as stack:
                out = stack.enter_context(path.open("xb")) if path is not None else None
                for block in iter(lambda: inp.read(1024 * 1024), b""):
                    h.update(block)
                    if out is not None:
                        out.write(block)
                if out is not None:
                    os.fchmod(out.fileno(), row["mode"])
                    out.flush()
                    os.fsync(out.fileno())
            require(h.hexdigest() == row["sha256"], "Module bytes differ: " + name)
    require(seen == set(rows), "Module archive entries missing")
    # The archive's compressed bytes are pinned separately. Consume the stream
    # completely so decompressor failures cannot be hidden by tar's end marker.
    for _ in iter(lambda: stream.read(1024 * 1024), b""):
        pass


class Host:
    """Only local identity reads, local decompression, sync and filesystem primitives."""
    path = staticmethod(Path)

    def running(self):
        return {"release": platform.release(),
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}

    def command_json(self, argv):
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False,
                                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        require(result.returncode == 0, "Local identity command failed: " + str(argv))
        return json.loads(result.stdout)

    def probe(self, contract):
        mounts = {}
        for role in ("boot", "root"):
            target = contract[role]["mountpoint"]
            mounts[role] = self.command_json(["/usr/bin/findmnt", "--json", "--target", target,
                                            "--output", "SOURCE,TARGET,FSTYPE,UUID,OPTIONS"])
        return {"mounts": mounts, "blocks": self.command_json([
            "/usr/bin/lsblk", "--json", "--tree", "--bytes", "--paths", "--output",
            "PATH,TYPE,SIZE,FSTYPE,UUID,PKNAME,MOUNTPOINTS", contract["disk"]["path"]])}

    @contextlib.contextmanager
    def module_stream(self, archive):
        child = subprocess.Popen(["/usr/bin/zstd", "-q", "-d", "-c", str(archive)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        try:
            yield child.stdout
            error = child.stderr.read(65537)
            require(len(error) <= 65536 and child.wait(timeout=30) == 0, "Module decompression failed")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            child.stdout.close()
            child.stderr.close()

    def require_write_access(self):
        require(platform.system() == "Linux" and os.geteuid() == 0, "Local Linux root required for writes")

    def rename_new(self, source, destination):
        # Atomic no-replacement semantics prevent overwriting even an empty tree
        # created by another actor after the explicit destination absence check.
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(destination))

    replace = staticmethod(os.replace)
    sync = staticmethod(os.sync)


def verify_probe(probe, contract, require_writable=True):
    disks = probe["blocks"]["blockdevices"]
    require(len(disks) == 1, "Exact USB disk identity required")
    disk = disks[0]
    require(disk["path"] == contract["disk"]["path"] and disk["type"] == "disk" and
            int(disk["size"]) == contract["disk"]["bytes"], "USB disk identity differs")
    parts = {row["path"]: row for row in disk.get("children", [])}
    require(set(parts) == {contract[r]["source"] for r in ("boot", "root")}, "USB partition set differs")
    for role in ("boot", "root"):
        wanted = contract[role]
        rows = probe["mounts"][role]["filesystems"]
        require(len(rows) == 1, "Exact mount required: " + role)
        actual = rows[0]
        access_modes = set(actual["options"].split(",")) & {"ro", "rw"}
        require(all(actual[k] == wanted[v] for k, v in
                    (("source", "source"), ("target", "mountpoint"), ("fstype", "fstype"), ("uuid", "uuid"))) and
                (access_modes == {"rw"} or (not require_writable and access_modes == {"ro"})),
                "Mount identity/options differ: " + role)
        part = parts[wanted["source"]]
        require(part["type"] == "part" and int(part["size"]) == wanted["bytes"] and
                part["fstype"] == wanted["fstype"] and part["uuid"] == wanted["uuid"] and
                part["pkname"] == contract["disk"]["path"] and
                set(part["mountpoints"]) == {wanted["mountpoint"]}, "Partition identity differs: " + role)


class Install:
    def __init__(self, args, host=None):
        self.args, self.host, self.owns_output = args, host or Host(), False
        self.plan_path = args.plan.resolve(strict=True)
        require(regular(self.plan_path)["sha256"] == args.plan_sha256, "Exact plan SHA256 required")
        self.plan = json.loads(self.plan_path.read_bytes())
        self.stage = self.plan_path.parent
        self.c = self.plan["install"]
        self.release = self.plan["release"]
        require(self.plan["schema"] == 1 and self.c["schema"] == 1 and
                self.c["status"] == "REVIEWED_FOR_EXPLICIT_LIVE_INSTALL" and
                self.plan["experiment"] == args.experiment and
                re.fullmatch(r"EXP-[0-9]{8}-[0-9]{3}", args.experiment) and
                re.fullmatch(r"[A-Za-z0-9_.-]+", self.release), "Reviewed exact install plan required")
        require(self.c["boot"]["mountpoint"] == "/boot" and self.c["root"]["mountpoint"] == "/" and
                self.c["boot"]["fstype"] == "vfat" and self.c["root"]["fstype"] == "ext4" and
                self.release != self.c["running"]["release"] and
                re.fullmatch(r"[A-Za-z0-9_.-]+", self.c["running"]["release"]) and
                self.c["running"]["release"] not in (".", "..") and
                re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", self.c["running"]["boot_id"]),
                "Explicit target/running identities required")
        self.boot = self.host.path(self.c["boot"]["mountpoint"])
        self.modules_parent = self.host.path(self.c["modules_parent"])
        require(self.modules_parent.is_absolute() and self.modules_parent.resolve(strict=True) == self.modules_parent,
                "Canonical existing module parent required")
        self.output = args.output
        require(self.output.is_absolute() and self.output.parent.resolve(strict=True) == self.output.parent and
                self.output.parent.is_dir() and self.output.parent.stat().st_mode & 0o077 == 0 and
                not self.output.exists() and not self.output.is_symlink() and
                re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", self.output.name), "Fresh output under existing private canonical parent required")
        for protected in (self.boot, self.modules_parent, self.stage):
            require(not self.output.is_relative_to(protected) and not protected.is_relative_to(self.output),
                    "Output overlaps boot, modules or stage")
        self.pins = {self.plan_path: args.plan_sha256,
                     Path(__file__).resolve(): self.c["installer_sha256"]}
        require(set(self.c["tools"]) == TOOLS, "Exact three local tool pins required")
        self.pins.update({self.host.path(path): digest for path, digest in self.c["tools"].items()})
        require(set(self.plan["boot_files"]) == BOOT_FILES and
                set(self.plan["rollback_boot_files"]) == BOOT_FILES and
                {n for n in BOOT_FILES if self.plan["boot_files"][n] != self.plan["rollback_boot_files"][n]} == CHANGED_FILES,
                "Only bzImage and bootargs.txt may change")
        for directory, field in (("candidate-boot", "boot_files"), ("rollback-boot", "rollback_boot_files")):
            root = self.stage / directory
            require(root.resolve() == root and {x.name for x in root.iterdir()} == BOOT_FILES,
                    "Exact five staged boot files required")
            for name, row in self.plan[field].items():
                require(set(row) == {"bytes", "sha256"} and regular(root / name) == row,
                        "Staged boot file differs: " + directory + "/" + name)
                self.pins[root / name] = row["sha256"]
        for field in ("module_tree", "runtime_identity"):
            row = self.c[field]
            path = self.stage / name_safe(row["path"])
            require(path.resolve() == path and regular(path)["sha256"] == row["sha256"], "Sidecar differs: " + field)
            self.pins[path] = row["sha256"]
        self.module_rows = json.loads((self.stage / self.c["module_tree"]["path"]).read_bytes())
        self.module_prefix = module_inventory(self.module_rows, self.release)
        runtime = json.loads((self.stage / self.c["runtime_identity"]["path"]).read_bytes())
        module_archive = self.plan["modules_archive"]
        self.archive = self.stage / name_safe(module_archive["path"])
        require(self.archive.resolve() == self.archive and
                regular(self.archive)["sha256"] == module_archive["sha256"], "Module archive pin differs")
        self.pins[self.archive] = module_archive["sha256"]
        ko = {n for n in self.module_rows if n.endswith(".ko")}
        require(runtime["release"] == self.release and
                runtime["kernel_sha256"] == self.plan["boot_files"]["bzImage"]["sha256"] == self.plan["kernel_sha256"] and
                runtime["module_archive"] == {"name": module_archive["path"], "sha256": module_archive["sha256"]} and
                runtime["module_count"] == module_archive["count"] == len(ko) == 41 and
                set(runtime["modules"]) == ko and
                runtime["module_tree_sha256"] == self.c["module_tree"]["sha256"],
                "Runtime kernel/module identity differs")
        for name, row in runtime["modules"].items():
            require({k: v for k, v in row.items() if k != "vermagic"} == self.module_rows[name] and
                    row["vermagic"].split()[0] == self.release, "Runtime per-module identity differs: " + name)
        self.destination = self.modules_parent / self.release
        suffix = args.experiment + "." + self.output.name + ".pending"
        self.module_temp = self.modules_parent / ("." + self.release + "." + suffix)
        self.boot_temps = {name: self.boot / ("." + name + "." + suffix) for name in CHANGED_FILES}
        require(not any(x.exists() or x.is_symlink() for x in [self.destination, self.module_temp, *self.boot_temps.values()]),
                "Destination/temp already exists; preserve it and use a new plan/attempt")
        self.old_modules = self.modules_parent / self.c["running"]["release"]
        self.old_module_inventory = inventory(self.old_modules)
        self.pin_shapes = {path: pin_shape(path) for path in self.pins}
        self.check_pins()
        self.live_guard(self.plan["rollback_boot_files"])
        with self.host.module_stream(self.archive) as stream:
            read_modules(stream, self.module_rows, self.release)

    def check_pins(self):
        for path, digest in self.pins.items():
            require(path.is_file() and re.fullmatch(r"[0-9a-f]{64}", digest) and sha(path) == digest and
                    pin_shape(path) == self.pin_shapes[path],
                    "Frozen input changed: " + str(path))

    def live_guard(self, expected_boot, allow_temps=False):
        require(self.host.running() == self.c["running"], "Running release/boot ID differs")
        verify_probe(self.host.probe(self.c), self.c, require_writable=self.args.write)
        require(self.boot.is_dir() and not self.boot.is_symlink(), "Real /boot directory required")
        temp_keys = CHANGED_FILES if allow_temps is True else (allow_temps or set())
        allowed = BOOT_FILES | {self.boot_temps[n].name for n in temp_keys}
        require({x.name for x in self.boot.iterdir()} == allowed, "Exact live boot file set differs")
        for name, row in expected_boot.items():
            require(regular(self.boot / name) == row, "Live boot hash differs: " + name)
        require(inventory(self.old_modules) == self.old_module_inventory, "Old module tree changed")

    def journal(self, event, **details):
        with (self.output / "JOURNAL.jsonl").open("a") as out:
            out.write(json.dumps({"event": event, "time_ns": time.time_ns(), **details}, sort_keys=True) + "\n")
            out.flush()
            os.fsync(out.fileno())

    def write(self):
        require(self.args.write and self.args.owner_confirmation and self.args.session_id and
                re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", self.args.session_id),
                "Writes require explicit flag, owner confirmation reference and active session ID assertions")
        self.host.require_write_access()
        self.check_pins()
        self.live_guard(self.plan["rollback_boot_files"])
        require(not self.destination.exists() and not self.destination.is_symlink(), "New module release already exists")
        self.output.mkdir(mode=0o700)
        self.owns_output = True
        fsync_directory(self.output.parent)
        self.journal("started", plan_sha256=self.args.plan_sha256, experiment=self.args.experiment,
                     session_id=self.args.session_id, owner_confirmation=self.args.owner_confirmation,
                     assertions_verified_by_tool=False)
        backup = self.output / "rollback-boot"
        backup.mkdir()
        for name, row in sorted(self.plan["rollback_boot_files"].items()):
            copy_fsync(self.boot / name, backup / name, row)
        fsync_directory(backup)
        save(self.output / "BACKUP.json", {"plan_sha256": self.args.plan_sha256,
             "boot_files": {n: regular(backup / n) for n in sorted(BOOT_FILES)},
             "running": self.host.running(), "targets": self.host.probe(self.c)})
        save(self.output / "OLD-MODULES.json", self.old_module_inventory)
        self.journal("backup_readback_complete")
        self.module_temp.mkdir(mode=0o755)
        self.journal("module_temp_created", path=str(self.module_temp))
        with self.host.module_stream(self.archive) as stream:
            read_modules(stream, self.module_rows, self.release, self.module_temp)
        expected = {n[len(self.module_prefix):]: row for n, row in self.module_rows.items()}
        require(inventory(self.module_temp) == expected, "Extracted module tree differs")
        for directory, _, _ in os.walk(self.module_temp, topdown=False):
            fsync_directory(Path(directory))
        self.live_guard(self.plan["rollback_boot_files"])
        self.check_pins()
        self.host.rename_new(self.module_temp, self.destination)
        fsync_directory(self.modules_parent)
        self.journal("new_module_tree_installed", path=str(self.destination))
        for name in sorted(CHANGED_FILES):
            copy_fsync(self.stage / "candidate-boot" / name, self.boot_temps[name], self.plan["boot_files"][name])
        fsync_directory(self.boot)
        self.live_guard(self.plan["rollback_boot_files"], allow_temps=True)
        self.check_pins()
        # Kernel first. Boot arguments are the final boot-file commit.
        committed = dict(self.plan["rollback_boot_files"])
        remaining = set(CHANGED_FILES)
        for name in ("bzImage", "bootargs.txt"):
            require(regular(self.boot_temps[name]) == self.plan["boot_files"][name], "Boot temp changed: " + name)
            self.host.replace(self.boot_temps[name], self.boot / name)
            fsync_directory(self.boot)
            require(regular(self.boot / name) == self.plan["boot_files"][name], "Committed boot readback differs: " + name)
            self.journal("boot_file_replaced", name=name)
            committed[name] = self.plan["boot_files"][name]
            remaining.remove(name)
            self.live_guard(committed, allow_temps=remaining)
        self.host.sync()
        self.live_guard(self.plan["boot_files"])
        require(inventory(self.destination) == expected, "Installed module readback differs")
        self.check_pins()

    def run(self):
        if not self.args.write:
            result = {"status": "PASS_VALIDATE", "plan_sha256": self.args.plan_sha256,
                      "experiment": self.args.experiment, "writes_performed": False, "hardware_acceptance": False}
            print(json.dumps(result, sort_keys=True))
            return result
        result = {"schema": 1, "status": "STARTED", "plan_sha256": self.args.plan_sha256,
                  "experiment": self.args.experiment, "session_id": self.args.session_id,
                  "owner_confirmation": self.args.owner_confirmation, "caller_assertions_only": True,
                  "hardware_acceptance": False, "reboot_performed": False}
        failed = None
        try:
            self.write()
            result["status"] = "PASS_INSTALL_READBACK"
        except Exception as error:
            failed = error
            result.update(status="FAILED_PRESERVED", error=type(error).__name__ + ": " + str(error))
        finally:
            if self.owns_output:
                try:
                    self.host.sync()
                    self.check_pins()
                    require(inventory(self.old_modules) == self.old_module_inventory, "Old module tree changed")
                    result["frozen_inputs_and_old_modules_preserved"] = True
                except Exception as error:
                    failed = failed or error
                    result.update(status="FAILED_PRESERVED", final_check_error=type(error).__name__ + ": " + str(error))
                try:
                    result["boot_readback"] = {n: regular(self.boot / n) for n in sorted(BOOT_FILES)}
                except Exception as error:
                    failed = failed or error
                    result.update(status="FAILED_PRESERVED", boot_readback_error=type(error).__name__ + ": " + str(error))
                result.update(module_destination=str(self.destination), module_destination_exists=self.destination.exists(),
                              module_temp=str(self.module_temp), module_temp_exists=self.module_temp.exists(),
                              boot_temps={n: {"path": str(x), "exists": x.exists()} for n, x in self.boot_temps.items()})
                save(self.output / "INSTALL.json", result)
            print(json.dumps({"status": result["status"], "output": str(self.output),
                              "hardware_acceptance": False}, sort_keys=True))
        if failed:
            raise failed
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--owner-confirmation")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    Install(args).run()


if __name__ == "__main__":
    main()
