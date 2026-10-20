#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Package a completed private experimental UVD build; no installation or hardware actions."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import importlib.util
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    with path.open("xb") as stream:
        stream.write(json_bytes(value))
    path.chmod(0o644)


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def read_json(path):
    return json.loads(Path(path).read_bytes())


def safe_name(name):
    require(isinstance(name, str), "Non-string relative path")
    p = PurePosixPath(name)
    require(name and not p.is_absolute() and ".." not in p.parts and
            p.as_posix() == name and name != "." and
            not any(ord(c) < 32 or c == "\\" for c in name),
            "Unsafe relative path: " + name)
    return name


def record(path):
    st = path.lstat()
    if path.is_symlink():
        target = os.readlink(path)
        data = target.encode()
        return {"kind": "symlink", "mode": st.st_mode & 0o777,
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "target": target}
    require(path.is_file(), "Special file: " + str(path))
    return {"kind": "file", "mode": st.st_mode & 0o777, "bytes": st.st_size, "sha256": sha(path)}


def tree(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            if path.is_symlink() or not path.is_dir():
                result[path.relative_to(root).as_posix()] = record(path)
    return result


def validate_inventory(inventory, links=True):
    require(isinstance(inventory, dict) and inventory, "Empty/invalid inventory")
    symlinks = {name for name, row in inventory.items() if row.get("kind") == "symlink"}
    for name, row in inventory.items():
        safe_name(name)
        require(row.get("kind") in (("file", "symlink") if links else ("file",)) and
                type(row.get("mode")) is int and 0 <= row["mode"] <= 0o777 and
                type(row.get("bytes")) is int and row["bytes"] >= 0 and
                re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")),
                "Invalid inventory record: " + name)
        require(not any(p.as_posix() in symlinks for p in PurePosixPath(name).parents),
                "Inventory descends through symlink: " + name)
        if row["kind"] == "symlink":
            target = row.get("target")
            require(isinstance(target, str) and target and not PurePosixPath(target).is_absolute(),
                    "Invalid/absolute symlink: " + name)
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            require(resolved != ".." and not resolved.startswith("../"), "Escaping symlink: " + name)
            data = target.encode()
            require(len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"],
                    "Symlink inventory digest differs: " + name)


class DigestStream:
    """Count/hash the complete uncompressed stream, including tar padding."""
    def __init__(self, stream):
        self.stream, self.h, self.bytes = stream, hashlib.sha256(), 0

    def write(self, data):
        size = self.stream.write(data)
        require(size == len(data), "Short archive write")
        self.h.update(data)
        self.bytes += size
        return size

    def read(self, size=-1):
        data = self.stream.read(size)
        self.h.update(data)
        self.bytes += len(data)
        return data

    def identity(self):
        return {"tar_sha256": self.h.hexdigest(), "tar_bytes": self.bytes}


def write_tar_stream(stream, prefix, inventory, paths):
    """Write only enumerated inputs. Never follow links or recurse into a source tree."""
    safe_name(prefix)
    validate_inventory(inventory)
    require(set(paths) == set(inventory), "Archive input path set differs")
    digest = DigestStream(stream)
    with tarfile.open(fileobj=digest, mode="w|", format=tarfile.PAX_FORMAT) as archive:
        for name, row in sorted(inventory.items()):
            path = paths[name]
            require(record(path) == row, "Archive input differs: " + name)
            item = tarfile.TarInfo(prefix + "/" + name)
            item.mode, item.mtime, item.uid, item.gid = row["mode"], 0, 0, 0
            item.uname = item.gname = ""
            if row["kind"] == "symlink":
                item.type, item.linkname = tarfile.SYMTYPE, row["target"]
                archive.addfile(item)
            else:
                item.size = row["bytes"]
                with path.open("rb") as inp:
                    archive.addfile(item, inp)
    return digest.identity()


def verify_tar_stream(stream, prefix, inventory, expected_stream=None, links=True):
    """Read back every member and byte; reject additions, omissions and type changes."""
    safe_name(prefix)
    validate_inventory(inventory, links=links)
    seen, digest = set(), DigestStream(stream)
    with tarfile.open(fileobj=digest, mode="r|") as archive:
        for item in archive:
            name = safe_name(item.name)
            require(name.startswith(prefix + "/"), "Archive prefix differs")
            name = safe_name(name[len(prefix) + 1:])
            require(name in inventory and name not in seen, "Duplicate/unexpected member: " + name)
            seen.add(name)
            row = inventory[name]
            require(item.mode == row["mode"], "Archive mode differs: " + name)
            if row["kind"] == "symlink":
                require(item.issym() and item.size == 0 and item.linkname == row["target"],
                        "Archive symlink differs: " + name)
            else:
                require(item.isfile() and item.size == row["bytes"], "Archive file type/size differs: " + name)
                h = hashlib.sha256()
                with archive.extractfile(item) as inp:
                    for block in iter(lambda: inp.read(1024 * 1024), b""):
                        h.update(block)
                require(h.hexdigest() == row["sha256"], "Archive content differs: " + name)
    for _ in iter(lambda: digest.read(1024 * 1024), b""):
        pass
    require(seen == set(inventory), "Archive members missing")
    identity = digest.identity()
    if expected_stream is not None:
        require(identity == expected_stream, "Complete tar stream identity differs")
    return {**identity, "members": len(seen),
            "symlinks": sum(row["kind"] == "symlink" for row in inventory.values())}


def archive(zstd, destination, prefix, inventory, paths, logs, links=True):
    """Only external command is the reviewed local zstd compressor/decompressor."""
    stem = destination.name
    commands = []
    stderr = logs / (stem + ".compress.stderr")
    argv = [str(zstd), "-q", "-3", "-T1", "-c"]
    with destination.open("xb") as out, stderr.open("xb") as err:
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err)
        try:
            identity = write_tar_stream(child.stdin, prefix, inventory, paths)
            child.stdin.close()
            require(child.wait(timeout=1800) == 0, "Compression failed: " + stem)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            child.stdin.close()
    commands.append({"argv": argv, "returncode": child.returncode, "stderr_sha256": sha(stderr)})
    stderr = logs / (stem + ".readback.stderr")
    argv = [str(zstd), "-q", "-d", "-c", str(destination)]
    with stderr.open("xb") as err:
        child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=err)
        try:
            readback = verify_tar_stream(child.stdout, prefix, inventory, identity, links=links)
            require(child.wait(timeout=1800) == 0, "Archive decompression failed: " + stem)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            child.stdout.close()
    commands.append({"argv": argv, "returncode": child.returncode, "stderr_sha256": sha(stderr)})
    return {"sha256": sha(destination), "readback": readback, "commands": commands}


def copy_file(source, destination, expected=None):
    row = record(source)
    require(row["kind"] == "file" and (expected is None or row == expected),
            "Copy input differs: " + str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as inp, destination.open("xb") as out:
        shutil.copyfileobj(inp, out, 1024 * 1024)
    destination.chmod(row["mode"])
    require(record(destination) == row, "Copied input differs: " + str(source))


def receipt_arg(value):
    parts = value.split("=", 2)
    require(len(parts) == 3 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", parts[0]) and
            Path(parts[1]).is_absolute() and re.fullmatch(r"[0-9a-f]{64}", parts[2]),
            "Receipt must be NAME=ABSOLUTE_PATH=SHA256")
    return parts[0], Path(parts[1]), parts[2]


def load_build_tool(path, manifest):
    path = path.resolve(strict=True)
    require(sha(path) == manifest["tool_sha256"], "Native build tool digest differs")
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("reviewed_native_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Package:
    def __init__(self, args):
        self.args, self.owns_output = args, False
        self.manifest_path = args.manifest.resolve(strict=True)
        require(sha(self.manifest_path) == args.manifest_sha256, "Manifest digest differs")
        self.m = read_json(self.manifest_path)
        self.b = load_build_tool(args.build_tool, self.m)
        # Reuse the exact native build contract, including its immutable-base and tool checks.
        self.w = self.b.Workflow(self.manifest_path, args.manifest_sha256, "package-readonly")
        self.work, self.source = self.w.work, self.w.source
        self.output = args.output
        require(self.output.is_absolute() and self.output.parent.resolve(strict=True) == self.output.parent
                and not self.output.is_symlink(), "Output requires an absolute canonical existing parent")
        require(not self.output.is_relative_to(self.work) and not self.work.is_relative_to(self.output),
                "Package output overlaps native build")
        require(not self.output.exists(), "Fresh package output required; preserve the prior attempt")
        self.pins = dict(self.w.pins)
        self.pins[str(Path(__file__).resolve())] = sha(__file__)
        for label, path, pin in args.receipt:
            require(label not in getattr(self, "extra_receipts", {}), "Duplicate extra receipt label")
            if not hasattr(self, "extra_receipts"):
                self.extra_receipts = {}
            self.extra_receipts[label] = path
            require(str(path) not in self.pins or self.pins[str(path)] == pin, "Conflicting receipt pin")
            self.pins[str(path)] = pin
        self.extra_receipts = getattr(self, "extra_receipts", {})
        require(not any(Path(p).resolve().is_relative_to(self.output) for p in self.pins),
                "Pinned input lies inside output")
        self.phase = {}
        for name in ("PREPARE.json", "BUILD-STARTED.json", "BUILD.json", "PREPARED-SOURCE.json",
                     "BUILT-SOURCE.json", "MODULES.json", "MODULE-TREE.json"):
            path = self.work / name
            self.pins[str(path)] = sha(path)
            self.phase[name] = read_json(path)
        require(self.pins[str(self.work / "BUILD.json")] == args.build_sha256, "Build receipt digest differs")
        built, prepared = self.phase["BUILD.json"], self.phase["PREPARE.json"]
        require(built["status"] == "PASS_BUILD" and built["release"] == self.b.RELEASE and
                built["manifest_sha256"] == args.manifest_sha256 and
                built["base_and_frozen_inputs_preserved"] is True and
                built["hardware_acceptance"] is False and built["module_count"] == 41,
                "Successful private build required")
        require(prepared["status"] == "PASS_PREPARE" and
                prepared["manifest_sha256"] == args.manifest_sha256 and
                prepared["base_and_frozen_inputs_preserved"] is True and
                built["previous_phase_receipt_sha256"] == self.pins[str(self.work / "PREPARE.json")] and
                prepared["prepared_source_sha256"] == self.pins[str(self.work / "PREPARED-SOURCE.json")],
                "Prepare/build receipt chain differs")
        require(self.phase["BUILD-STARTED.json"] == {
            "manifest_sha256": args.manifest_sha256,
            "prepare_receipt_sha256": built["previous_phase_receipt_sha256"]}, "Build-start binding differs")
        for field, filename in (("built_source_sha256", "BUILT-SOURCE.json"),
                                ("modules_sha256", "MODULES.json"),
                                ("module_tree_sha256", "MODULE-TREE.json")):
            require(built[field] == self.pins[str(self.work / filename)], "Build inventory binding differs")
        self.inventory = self.phase["BUILT-SOURCE.json"]
        validate_inventory(self.inventory)
        expected = dict(self.w.expected)
        expected[".config"] = self.inventory[".config"]
        require(self.inventory == expected and self.phase["PREPARED-SOURCE.json"] == self.w.expected,
                "Complete corresponding-source inventory differs")
        self.module_tree = self.phase["MODULE-TREE.json"]
        validate_inventory(self.module_tree, links=False)
        self.modules = self.phase["MODULES.json"]
        baseline = read_json(self.m["base"]["modules"]["path"])
        self.b.matching_module_names(self.modules, baseline)
        require(set(self.modules) == {n for n in self.module_tree if n.endswith(".ko")},
                "Module inventory/tree set differs")
        for name, row in self.modules.items():
            require({k: v for k, v in row.items() if k != "vermagic"} == self.module_tree[name],
                    "Module identity/tree differs: " + name)
            self.b.module_identity(self.w.output / "modules" / name, self.b.RELEASE, row["vermagic"])
        self.patches = {}
        for i, row in enumerate(self.m["patches"], 1):
            name = f"{i:02d}-" + Path(row["path"]).name
            self.patches[name] = record(self.work / "patches" / name)
            require(self.patches[name]["sha256"] == row["sha256"], "Prepared patch differs")
        self.patches["series"] = record(self.work / "patches/series")
        require((self.work / "patches/series").read_text() ==
                "".join(n + "\n" for n in self.patches if n != "series"), "Patch series differs")
        require(tree(self.work / "patches") == self.patches and
                all(row["kind"] == "file" for row in self.patches.values()),
                "Patch directory inventory differs")
        self.prepared_inputs = tree(self.work / "inputs")
        require(set(self.prepared_inputs) == {"REVIEWED-MANIFEST.json", "native_build.py"} and
                all(row["kind"] == "file" for row in self.prepared_inputs.values()) and
                self.prepared_inputs["REVIEWED-MANIFEST.json"]["sha256"] == args.manifest_sha256 and
                self.prepared_inputs["native_build.py"]["sha256"] == self.m["tool_sha256"],
                "Prepared manifest/tool copies differ")
        self.base_metadata = tree(self.work / "base-metadata")
        require(set(self.base_metadata) == set(self.b.EXTRA_PINS) and
                all(row["kind"] == "file" and row["mode"] == 0o644 and
                    row["sha256"] == self.b.EXTRA_PINS[name] for name, row in self.base_metadata.items()),
                "Original base-metadata lineage differs")
        self.artifacts = {"vmlinux": self.source / "vmlinux", **{
            name: self.w.output / name for name in ("bzImage", "System.map", "config", "version.txt")}}
        require(set(built["artifact_sha256"]) == set(self.artifacts), "Build artifact set differs")
        for name, path in self.artifacts.items():
            self.pins[str(path)] = built["artifact_sha256"][name]
        require((self.w.output / "version.txt").read_text() == self.b.RELEASE + "\n" and
                built["artifact_sha256"]["config"] == self.inventory[".config"]["sha256"],
                "Runtime release/config differs")
        self.logs = tree(self.work / "logs") if args.include_build_logs else {}
        if self.logs:
            validate_inventory(self.logs, links=False)
            require(sum(r["bytes"] for r in self.logs.values()) <= 64 * 1024**2,
                    "Optional build logs exceed 64 MiB")
        self.pin_records = {name: record(Path(name)) for name in self.pins}
        self.check_inputs()

    def check_inputs(self):
        self.w.check_inputs()
        for path, pin in self.pins.items():
            require(Path(path).is_file() and sha(path) == pin and
                    record(Path(path)) == self.pin_records[path],
                    "Package input changed: " + path)
        self.b.check_config(Path(self.m["base"]["config"]["path"]).read_bytes(),
                            (self.source / ".config").read_bytes())
        self.b.verify_source(self.source, self.inventory)
        require(tree(self.w.output / "modules") == self.module_tree, "Installed module tree changed")
        require(tree(self.work / "patches") == self.patches, "Patch tree changed")
        require(tree(self.work / "inputs") == self.prepared_inputs, "Prepared input copies changed")
        require(tree(self.work / "base-metadata") == self.base_metadata, "Base lineage changed")
        if self.args.include_build_logs:
            require(tree(self.work / "logs") == self.logs, "Optional build logs changed")

    def stage_inputs(self):
        root = self.output / "build-inputs"
        root.mkdir()
        copies = {"tools/native_build.py": self.args.build_tool.resolve(),
                  "tools/native_package.py": Path(__file__).resolve(),
                  "manifest/REVIEWED-MANIFEST.json": self.manifest_path}
        for name in self.phase:
            copies["receipts/" + name] = self.work / name
        for role in ("inventory", "config", "package", "build", "compiler", "modules"):
            copies["base/" + role + (".config" if role == "config" else ".json")] = Path(self.m["base"][role]["path"])
        for role in ("source_receipt", "review"):
            copies["reviews/" + role + ".json"] = Path(self.m[role]["path"])
        for host, row in self.m["host_receipts"].items():
            copies["reviews/host-" + host + ".json"] = Path(row["path"])
        for label, path in self.extra_receipts.items():
            copies["extra-receipts/" + label] = path
        for name in self.patches:
            copies["patches/" + name] = self.work / "patches" / name
        for name in self.base_metadata:
            copies["base-metadata/" + name] = self.work / "base-metadata" / name
        for name in self.logs:
            copies["logs/" + name] = self.work / "logs" / name
        for name, source in sorted(copies.items()):
            copy_file(source, root / safe_name(name))
        self.inputs_inventory = tree(root)
        validate_inventory(self.inputs_inventory, links=False)
        save(self.output / "BUILD-INPUTS.json", self.inputs_inventory)
        return root

    def build_package(self):
        space = os.statvfs(self.output.parent)
        floor = (sum(r["bytes"] for r in self.inventory.values() if r["kind"] == "file") +
                 sum(r["bytes"] for r in self.module_tree.values()) +
                 sum(path.stat().st_size for path in self.artifacts.values()) + 512 * 1024**2)
        require(space.f_bavail * space.f_frsize >= floor, "Insufficient conservative packaging free-space floor")
        self.output.mkdir(mode=0o700)
        self.owns_output = True
        logs, metadata = self.output / "logs", self.output / "metadata"
        logs.mkdir()
        metadata.mkdir()
        inputs = self.stage_inputs()
        zstd = Path("/usr/bin/zstd")
        self.readbacks = {}
        name = "PSX-BUILD-INPUTS.tar.zst"
        self.readbacks[name] = archive(zstd, metadata / name, "PSX-BUILD-INPUTS", self.inputs_inventory,
                                      {n: inputs / n for n in self.inputs_inventory}, logs, links=False)
        copy_file(self.work / "BUILT-SOURCE.json", metadata / "PSX-SOURCE-INPUTS.json")
        save(metadata / "PSX-BUILD-MANIFEST.json", {
            "schema": 1, "status": "PRIVATE_EXPERIMENTAL_UVD_CORRESPONDING_SOURCE", "release": self.b.RELEASE,
            "reviewed_manifest_sha256": self.args.manifest_sha256,
            "build_receipt_sha256": self.args.build_sha256,
            "source_inventory_sha256": sha(self.work / "BUILT-SOURCE.json"),
            "build_inputs_archive_sha256": self.readbacks[name]["sha256"],
            "native_build_tool_sha256": self.m["tool_sha256"], "packaging_tool_sha256": sha(__file__),
            "hardware_acceptance": False, "compiled_review": False, "publication": "private only",
            "lineage": "Original native02 PSX metadata is under base-metadata/ in PSX-BUILD-INPUTS.tar.zst.",
        })
        with (metadata / "PSX-REBUILD.md").open("x") as out:
            out.write(f"""# Private {self.b.RELEASE} corresponding source

This tree contains exactly BUILT-SOURCE.json's source inputs, including the
resolved .config and every original notice. Generated build outputs are excluded.
The three ordered patches are already applied. Do not reapply them to this tree.

PSX-SOURCE-INPUTS.json preserves BUILT-SOURCE.json verbatim. PSX-BUILD-INPUTS.tar.zst
contains the reviewed manifest, exact patches/series, native build and packaging
tools, review/test receipts, preparation/build receipts, complete inventories,
and original base-metadata as lineage. The four lineage PSX files describe the
older native02 build; PSX-BUILD-MANIFEST.json describes this build.

Rebuild on native Linux x86-64 with the exact toolchain hashes and saved
environment in receipts/BUILD.json and base/compiler.json from the inputs archive.
The build did not embed device firmware or an initramfs. Runtime firmware is a
separate owner-provided input and is not included in these artifacts.

With the resolved .config unchanged, the recorded build commands are:

    make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc clean
    make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc olddefconfig
    make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc syncconfig
    make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc -j6 bzImage modules

The matching modules were installed only to a private build directory with
INSTALL_MOD_STRIP=1. Their exact paths, contents and vermagic are recorded in
receipts/MODULE-TREE.json and receipts/MODULES.json. Retain the whole matching
tree together with this kernel; changing one module is not a matching rebuild.

This is a private experimental UVD build. Packaging and compilation do not
establish compiled review, successful device execution, decoded output, or hardware
acceptance. No staging, installation, console operation or publication is done
by the packager. hardware_acceptance is false.
""")
        (metadata / "PSX-REBUILD.md").chmod(0o644)
        self.metadata_inventory = tree(metadata)
        require(not set(self.inventory) & set(self.metadata_inventory), "PSX metadata/source collision")
        source_inventory = {**self.inventory, **self.metadata_inventory}
        save(self.output / "SOURCE-ARCHIVE.json", source_inventory)
        source_paths = {n: self.source / n for n in self.inventory}
        source_paths.update({n: metadata / n for n in self.metadata_inventory})
        source_name = "source-" + self.b.RELEASE + ".tar.zst"
        self.readbacks[source_name] = archive(zstd, self.output / source_name, "linux-" + self.b.RELEASE,
                                             source_inventory, source_paths, logs)
        module_name = "modules-" + self.b.RELEASE + ".tar.zst"
        save(self.output / "MODULE-ARCHIVE.json", self.module_tree)
        require(all(n.startswith("lib/modules/" + self.b.RELEASE + "/") for n in self.module_tree),
                "Module archive paths outside matching lib/modules tree")
        self.readbacks[module_name] = archive(zstd, self.output / module_name, "lib",
                                             {n[len("lib/"):]: row for n, row in self.module_tree.items()},
                                             {n[len("lib/"):]: self.w.output / "modules" / n for n in self.module_tree},
                                             logs, links=False)
        for name, source in self.artifacts.items():
            copy_file(source, self.output / name)
        self.artifact_sha256 = {name: sha(self.output / name) for name in [
            *self.artifacts, source_name, module_name, "SOURCE-ARCHIVE.json", "MODULE-ARCHIVE.json", "BUILD-INPUTS.json"]}
        save(self.output / "RUNTIME-IDENTITY.json", {
            "schema": 1, "release": self.b.RELEASE, "hardware_acceptance": False,
            "compiled_review": False, "publication": "private only",
            "build_receipt_sha256": self.args.build_sha256,
            "kernel_sha256": self.artifact_sha256["bzImage"],
            "vmlinux_sha256": self.artifact_sha256["vmlinux"],
            "config_sha256": self.artifact_sha256["config"],
            "source_archive": {"name": source_name, "sha256": self.artifact_sha256[source_name]},
            "module_archive": {"name": module_name, "sha256": self.artifact_sha256[module_name]},
            "module_count": len(self.modules), "modules": self.modules,
            "module_tree_sha256": sha(self.work / "MODULE-TREE.json"),
        })
        self.artifact_sha256["RUNTIME-IDENTITY.json"] = sha(self.output / "RUNTIME-IDENTITY.json")

    def run(self):
        result = {"schema": 1, "status": "STARTED", "release": self.b.RELEASE,
                  "manifest_sha256": self.args.manifest_sha256,
                  "previous_phase_receipt_sha256": self.args.build_sha256,
                  "hardware_acceptance": False, "compiled_review": False,
                  "publication": "private only", "packaging_complete": False}
        failed = None
        try:
            self.build_package()
            result.update(artifact_sha256=self.artifact_sha256, archive_readbacks=self.readbacks,
                          source_inputs=len(self.inventory), module_count=len(self.modules),
                          extra_receipts={n: self.pins[str(p)] for n, p in self.extra_receipts.items()},
                          build_logs_included=self.args.include_build_logs)
        except Exception as error:
            failed = error
            result.update(error=type(error).__name__ + ": " + str(error))
        finally:
            try:
                self.check_inputs()
                result["all_build_and_frozen_inputs_preserved"] = True
            except Exception as error:
                failed = failed or error
                result.update(preservation_error=type(error).__name__ + ": " + str(error))
            result.update(status="FAILED_PRESERVED" if failed else "PASS_PACKAGE",
                          packaging_complete=failed is None)
            if self.owns_output:
                if failed is None:
                    try:
                        rows = tree(self.output)
                        require(all(r["kind"] == "file" for r in rows.values()), "Output contains links")
                        rows["PACKAGE.json"] = {"sha256": hashlib.sha256(json_bytes(result)).hexdigest()}
                        with (self.output / "SHA256SUMS").open("x") as sums:
                            sums.write("".join(rows[n]["sha256"] + "  " + safe_name(n) + "\n" for n in sorted(rows)))
                    except Exception as error:
                        failed = error
                        result.update(status="FAILED_PRESERVED", packaging_complete=False,
                                      checksum_error=type(error).__name__ + ": " + str(error))
                save(self.output / "PACKAGE.json", result)
            print(json.dumps({"status": result["status"], "output": str(self.output),
                              "hardware_acceptance": False, "packaging_complete": failed is None}))
        if failed:
            raise failed
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--build-tool", required=True, type=Path)
    p.add_argument("--build-sha256", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--receipt", action="append", type=receipt_arg, default=[],
                   help="Additional frozen tool/review/test receipt: NAME=ABSOLUTE_PATH=SHA256")
    p.add_argument("--include-build-logs", action="store_true",
                   help="Include only native build logs, up to 64 MiB; no UART logs")
    args = p.parse_args()
    require(re.fullmatch(r"[0-9a-f]{64}", args.manifest_sha256) and
            re.fullmatch(r"[0-9a-f]{64}", args.build_sha256), "Exact manifest/build SHA256 required")
    Package(args).run()


if __name__ == "__main__":
    main()
