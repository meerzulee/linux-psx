#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Manifest-gated private CREATE source preparation and clean native kernel build."""
from pathlib import Path, PurePosixPath
import argparse
import copy
import hashlib
import json
import os
import platform
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import time

OLD_RELEASE = "7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1"
RELEASE = "7.2.11-psx-ps4-baikal-uvd-create-dev1"
BASE_PINS = {
    "archive": "ece0782b5d52d6a6a28948c0f9e45d47b424659ed669889b85ee26116276222c",
    "inventory": "8505023a622ca744f104d07cb82c2dcd2f4d6af551cdf7bd5a77edae6fc4385a",
    "config": "16f041910342b089da805e6b390430f7b577438b18c713d9cb6de496044ca3a1",
    "package": "7a3fbd35e488c378703bafdee72080b8d679039f1aa403c825b570aa2e3297db",
    "build": "99ffabe51420204296b1cd3fa6a16877e05bf5bd082db5cc23e49934aa017c16",
    "compiler": "1f26c1e1140bc6d481537f49e08d1dda9eafaf40dcf360a70efd3c30698b7a90",
    "modules": "c4ba18fc99c116df62eaaa1a5ddd3e325f2ca339bffdb866f6afb80ccafcb528",
}
FIRST_PATCHES = (
    "d2360ccb4e651099df8b1c4964dc054706270d71fcb36775ff8e0fa6ca235776",
    "e0443efc3f778bbc957aa60ffd2e404f1c3485b27b6acaf4b1504294dbf6b3e7",
)
EXTRA_PINS = {
    "PSX-BUILD-INPUTS.tar.zst": "6230a5f84994de146ca0227c812dea50ad77c49f81074bcbfaedde4c762b7393",
    "PSX-BUILD-MANIFEST.json": "0ee5ba6660cd46052e06f9c84b356f6df4c995bcb3162ea6a19d4990088fa4c6",
    "PSX-SOURCE-INPUTS.json": BASE_PINS["inventory"],
    "PSX-REBUILD.md": "05daaa70a8323fbb5282d2e675cded65cabafb946ef75a138d6d17064f3a2f14",
}
MAKE = ["/usr/bin/make", "ARCH=x86_64", "LOCALVERSION=", "CC=/usr/bin/gcc", "HOSTCC=/usr/bin/gcc"]
TOOLS = {"/usr/bin/" + name for name in (
    "gcc", "make", "git", "zstd", "ld", "as", "ar", "nm", "objcopy", "objdump",
    "strip", "depmod", "modinfo", "pahole")}
STATUS = "REVIEWED_FOR_PRIVATE_CREATE_BUILD"
OLD_KERNEL_PINS = {
    "e7abc9a8f4c5d4d19ce12dbab2df9f113a137c61f67497ab5c00ccb31a74cb4f",
    "7744dc0898d215cac91e9877b6539450c1889f360506394be9ce1bd3ab5fffd9",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def json_read(path):
    return json.loads(Path(path).read_bytes())


def safe_name(name):
    p = PurePosixPath(name)
    require(name and not p.is_absolute() and ".." not in p.parts and
            p.as_posix() == name and name != ".", "Unsafe relative path: " + name)
    return name


def file_record(path):
    st = path.lstat()
    if path.is_symlink():
        target = os.readlink(path)
        raw = target.encode()
        return {"kind": "symlink", "mode": st.st_mode & 0o777,
                "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "target": target}
    require(path.is_file(), "Special file: " + str(path))
    return {"kind": "file", "mode": st.st_mode & 0o777, "bytes": st.st_size, "sha256": sha(path)}


def tree(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            if path.is_symlink() or not path.is_dir():
                result[path.relative_to(root).as_posix()] = file_record(path)
    return result


def validate_inventory(inventory):
    links = {name for name, row in inventory.items() if row["kind"] == "symlink"}
    for name, row in inventory.items():
        safe_name(name)
        require(row["kind"] in ("file", "symlink") and 0 <= row["mode"] <= 0o777,
                "Unsupported inventory type/mode: " + name)
        require(not any(parent.as_posix() in links for parent in PurePosixPath(name).parents),
                "Inventory descends through symlink: " + name)
        if row["kind"] == "symlink":
            target = row["target"]
            require(not PurePosixPath(target).is_absolute(), "Absolute source symlink")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            require(resolved != ".." and not resolved.startswith("../"), "Escaping source symlink")


def extract_stream(stream, source, metadata, inventory, extra_pins, prefix):
    """Manual extraction: exact inventory; symlinks created only after regular files."""
    validate_inventory(inventory)
    require(not set(inventory) & set(extra_pins), "Metadata/source collision")
    allowed = set(inventory) | set(extra_pins)
    seen, links, extras = set(), [], {}
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            name = safe_name(member.name)
            require(name.startswith(prefix + "/"), "Wrong archive prefix")
            rel = safe_name(name[len(prefix) + 1:])
            require(rel not in seen and rel in allowed,
                    "Duplicate/unexpected member: " + rel)
            seen.add(rel)
            if rel in inventory and inventory[rel]["kind"] == "symlink":
                row = inventory[rel]
                require(member.issym() and member.size == 0 and member.linkname == row["target"] and
                        member.mode == row["mode"], "Symlink metadata differs: " + rel)
                links.append((rel, row))
                continue
            require(member.isfile(), "Non-regular archive member: " + rel)
            row = inventory.get(rel)
            if row:
                require(member.size == row["bytes"] and member.mode == row["mode"],
                        "Archive file metadata differs: " + rel)
            else:
                require(member.mode == 0o644 and member.size <= 256 * 1024 * 1024,
                        "Unexpected base metadata size/mode")
            dest = (source if row else metadata) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as inp, dest.open("xb") as out:
                shutil.copyfileobj(inp, out, 1024 * 1024)
            dest.chmod(member.mode)
            require(sha(dest) == (row["sha256"] if row else extra_pins[rel]),
                    "Archive content differs: " + rel)
            if not row:
                extras[rel] = file_record(dest)
    require(seen == allowed, "Archive members missing")
    for rel, row in links:
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(row["target"])
        if path.lstat().st_mode & 0o777 != row["mode"]:
            os.chmod(path, row["mode"], follow_symlinks=False)
    require(tree(source) == inventory, "Extracted source differs")
    return {"members": len(seen), "symlinks": len(links), "metadata": extras}


def config_values(raw):
    values = {}
    for line in raw.decode().splitlines():
        if line.startswith("CONFIG_"):
            key, value = line.split("=", 1)
        elif re.fullmatch(r"# CONFIG_[A-Z0-9_]+ is not set", line):
            key, value = line[2:-11], "n"
        else:
            require(not line or line.startswith("#"), "Unrecognized configuration line")
            continue
        require(key not in values, "Duplicate config key: " + key)
        values[key] = value
    return values


def check_config(base, current):
    before, after = config_values(base), config_values(current)
    wanted = dict(before, CONFIG_LOCALVERSION='"-psx-ps4-baikal-uvd-create-dev1"')
    require(after == wanted, "Configuration changed beyond LOCALVERSION")
    require(after.get("CONFIG_EXTRA_FIRMWARE") == '""' and
            after.get("CONFIG_INITRAMFS_SOURCE", '""') == '""', "Embedded firmware/initramfs")


def manifest_shape(m):
    require(m.get("schema") == 1 and m.get("status") == STATUS and
            m.get("release") == RELEASE, "Manifest is not approved for this build")
    require(set(m["base"]) == set(BASE_PINS), "Base input roles differ")
    for role, pin in BASE_PINS.items():
        require(m["base"][role]["sha256"] == pin, "Wrong base pin: " + role)
    require(len(m["patches"]) == 3 and
            tuple(x["sha256"] for x in m["patches"][:2]) == FIRST_PATCHES, "Patch order differs")
    require(set(m["host_receipts"]) == {"macos", "linux"}, "Both frozen host receipts required")
    require(m["candidate_files"] and m["protected_files"] and
            TOOLS <= set(m["tools"]), "Candidate/protected/tool pins incomplete")
    require(OLD_KERNEL_PINS <= set(m["protected_files"].values()), "Original kernel pins missing")
    for name, row in m["candidate_files"].items():
        safe_name(name)
        require(name.startswith("drivers/gpu/drm/amd/amdgpu/") and
                row["mode"] == 0o644, "Candidate scope/mode differs")
    records = [*m["base"].values(), *m["patches"], *m["candidate_files"].values(),
               m["source_receipt"], m["review"], *m["host_receipts"].values()]
    for row in records:
        require(Path(row["path"]).is_absolute() and
                re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "Incomplete frozen input pin")
    require(re.fullmatch(r"[0-9a-f]{64}", m["tool_sha256"]), "Tool pin missing")
    require(m["minimum_free_bytes"] >= 12 * 1024**3, "Reviewed space floor must be at least 12 GiB")
    return records


def expected_source(base, candidate):
    expected = copy.deepcopy(base)
    for name, row in candidate.items():
        expected[name] = {k: row[k] for k in ("kind", "mode", "bytes", "sha256")}
        require(row["kind"] == "file", "Only regular candidate files supported")
    return expected


def verify_source(source, expected, exact=False):
    if exact:
        require(tree(source) == expected, "Source inventory/content differs")
    else:
        for name, row in expected.items():
            require(file_record(source / name) == row, "Source input changed: " + name)


def module_identity(path, release, vermagic):
    raw = path.read_bytes()[:20]
    require(len(raw) == 20 and raw[:6] == b"\x7fELF\x02\x01" and
            struct.unpack("<HH", raw[16:20]) == (1, 62), "Wrong module ELF")
    require(vermagic.split() and vermagic.split()[0] == release, "Wrong module vermagic")


def matching_module_names(actual, baseline):
    expected = {name.replace("/" + OLD_RELEASE + "/", "/" + RELEASE + "/") for name in baseline}
    require(len(baseline) == 41 and len(actual) == 41 and set(actual) == expected,
            "Matching module set differs")


def replay_patches(rows, candidate_names, destination, run):
    """Keep exact patch bytes and their declared order; caller verifies the whole tree."""
    names = []
    for i, row in enumerate(rows, 1):
        patch = destination / (str(i).zfill(2) + "-" + Path(row["path"]).name)
        with Path(row["path"]).open("rb") as inp, patch.open("xb") as out:
            shutil.copyfileobj(inp, out)
        require(sha(patch) == row["sha256"], "Patch copy differs")
        pairs = re.findall(r"^diff --git a/(\S+) b/(\S+)$", patch.read_text(), re.M)
        require(pairs and all(a == b and safe_name(a) in candidate_names for a, b in pairs),
                "Patch paths outside frozen candidate")
        run(f"patch-{i}-check", ["/usr/bin/git", "apply", "--check", "--whitespace=nowarn", patch])
        run(f"patch-{i}-apply", ["/usr/bin/git", "apply", "--whitespace=nowarn", patch])
        names.append(patch.name)
    with (destination / "series").open("x") as stream:
        stream.write("".join(name + "\n" for name in names))


class Workflow:
    def __init__(self, manifest, digest, phase):
        require(platform.system() == "Linux" and platform.machine() == "x86_64",
                "Native Linux x86-64 only")
        self.manifest_path = manifest.resolve(strict=True)
        require(sha(self.manifest_path) == digest, "Manifest digest differs")
        self.m = json_read(self.manifest_path)
        records = manifest_shape(self.m)
        pairs = [(row["path"], row["sha256"]) for row in records]
        pairs += [*self.m["tools"].items(), *self.m["protected_files"].items(),
                  (str(self.manifest_path), digest),
                  (str(Path(__file__).resolve()), self.m["tool_sha256"])]
        self.pins = {}
        for name, expected in pairs:
            require(name not in self.pins or self.pins[name] == expected, "Conflicting input pins")
            self.pins[name] = expected
        require(all(Path(p).is_absolute() and re.fullmatch(r"[0-9a-f]{64}", h)
                    for p, h in self.pins.items()), "Invalid protected input")
        self.work = Path(self.m["work"])
        require(self.work.is_absolute() and self.work.parent.resolve() == self.work.parent
                and not self.work.is_symlink(), "Invalid work directory")
        require(not any(Path(p).is_relative_to(self.work) for p in self.pins),
                "Input or tool is inside output")
        old = Path(self.m["base"]["archive"]["path"]).parents[2]
        require(not self.work.is_relative_to(old), "Work is inside immutable base project")
        self.source, self.logs = self.work / "source", self.work / "logs" / phase
        self.output = self.work / "output"
        self.base = json_read(self.m["base"]["inventory"]["path"])
        self.expected = expected_source(self.base, self.m["candidate_files"])
        compiler = json_read(self.m["base"]["compiler"]["path"])
        require(self.m["tools"]["/usr/bin/gcc"] == compiler["gcc_sha256"], "Compiler identity differs")
        self.env = dict(compiler["environment"])
        require(self.env["HOME"] == os.environ.get("HOME"), "Native account differs")
        self.env.update(TMPDIR=str(self.work / "tmp"), GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL="/dev/null", GIT_CEILING_DIRECTORIES=str(self.work))
        self.commands = []
        self.owns_work = False
        self.manifest_sha = digest
        self.check_inputs()

    def check_inputs(self):
        for name, expected in self.pins.items():
            require(Path(name).is_file() and sha(name) == expected, "Input changed: " + name)
        package = json_read(self.m["base"]["package"]["path"])
        built = json_read(self.m["base"]["build"]["path"])
        require(package["status"] == "PASS_PACKAGE" and built["status"] == "PASS_BUILD" and
                package["previous_phase_receipt_sha256"] == BASE_PINS["build"] and
                built["source_inventory_sha256"] == BASE_PINS["inventory"] and
                package["artifact_sha256"]["source-" + OLD_RELEASE + ".tar.zst"] == BASE_PINS["archive"],
                "Base receipt chain differs")
        require(len(self.base) == 95115 and self.base[".config"]["sha256"] == BASE_PINS["config"],
                "Base source inventory differs")

    def run(self, label, argv, timeout=7200):
        self.logs.mkdir(parents=True, exist_ok=True)
        stdout, stderr = self.logs / (label + ".stdout"), self.logs / (label + ".stderr")
        row = {"argv": [str(x) for x in argv], "cwd": str(self.source), "returncode": None,
               "stdout": str(stdout), "stderr": str(stderr)}
        self.commands.append(row)
        save(self.logs / (label + ".command.json"), row)
        begin = time.monotonic()
        try:
            with stdout.open("xb") as out, stderr.open("xb") as err:
                r = subprocess.run(argv, cwd=self.source, env=self.env, stdout=out,
                                   stderr=err, timeout=timeout, check=False)
            row["returncode"] = r.returncode
        finally:
            row["elapsed_seconds"] = time.monotonic() - begin
            row["stdout_sha256"], row["stderr_sha256"] = sha(stdout), sha(stderr)
            save(self.logs / (label + ".json"), row)
        require(row["returncode"] == 0, "Command failed: " + label)
        return stdout.read_text(errors="replace")

    def prepare(self):
        require(not self.work.exists(), "Fresh preparation output required")
        space = os.statvfs(self.work.parent)
        require(space.f_bavail * space.f_frsize >= self.m["minimum_free_bytes"], "Insufficient free space")
        self.work.mkdir()
        self.owns_work = True
        for name in ("source", "base-metadata", "inputs", "patches", "tmp"):
            (self.work / name).mkdir()
        self.logs.mkdir(parents=True)
        shutil.copyfile(self.manifest_path, self.work / "inputs/REVIEWED-MANIFEST.json")
        shutil.copyfile(__file__, self.work / "inputs/native_build.py")
        archive = self.m["base"]["archive"]["path"]
        argv = ["/usr/bin/zstd", "-q", "-d", "-c", archive]
        self.commands.append({"argv": argv, "cwd": str(self.source), "operation": "validated extraction"})
        with (self.logs / "zstd.stderr").open("xb") as err:
            p = subprocess.Popen(argv, cwd=self.source, stdout=subprocess.PIPE, stderr=err, env=self.env)
            try:
                self.extraction = extract_stream(p.stdout, self.source, self.work / "base-metadata",
                                                 self.base, EXTRA_PINS, "linux-" + OLD_RELEASE)
                for _ in iter(lambda: p.stdout.read(1024 * 1024), b""):
                    pass
                require(p.wait(timeout=60) == 0, "Source decompression failed")
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait(timeout=10)
                p.stdout.close()
                self.commands[-1]["returncode"] = p.returncode
                self.commands[-1]["stderr_sha256"] = sha(self.logs / "zstd.stderr")
        require(self.extraction["members"] == 95119 and self.extraction["symlinks"] == 99,
                "Archive count differs")
        replay_patches(self.m["patches"], self.m["candidate_files"], self.work / "patches", self.run)
        verify_source(self.source, self.expected, exact=True)
        save(self.work / "PREPARED-SOURCE.json", self.expected)

    def build(self, prepared_sha):
        receipt = self.work / "PREPARE.json"
        require(prepared_sha and sha(receipt) == prepared_sha, "Exact prepare receipt required")
        prepared = json_read(receipt)
        require(prepared["status"] == "PASS_PREPARE" and
                prepared["manifest_sha256"] == self.manifest_sha, "Prepared phase binding differs")
        require(not (self.work / "BUILD-STARTED.json").exists() and not self.output.exists(),
                "Build already attempted; preserve it and use a fresh workspace")
        verify_source(self.source, self.expected, exact=True)
        require(sha(self.work / "PREPARED-SOURCE.json") == prepared["prepared_source_sha256"],
                "Prepared inventory changed")
        save(self.work / "BUILD-STARTED.json", {"manifest_sha256": self.manifest_sha,
                                              "prepare_receipt_sha256": prepared_sha})
        self.owns_work = True
        self.run("clean", [*MAKE, "clean"])
        verify_source(self.source, self.expected)
        baseline_config = Path(self.m["base"]["config"]["path"]).read_bytes()
        old = ('CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-live-prepare-dev1"').encode()
        require(baseline_config.count(old) == 1, "Base LOCALVERSION differs")
        (self.source / ".config").write_bytes(baseline_config.replace(
            old, b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-create-dev1"'))
        for target in ("olddefconfig", "syncconfig"):
            self.run(target, [*MAKE, target])
        check_config(baseline_config, (self.source / ".config").read_bytes())
        require(self.run("release-before", [*MAKE, "-s", "kernelrelease"]).strip() == RELEASE,
                "Configured release differs")
        self.expected[".config"] = file_record(self.source / ".config")
        verify_source(self.source, self.expected)
        for name, command in (("gcc", ["/usr/bin/gcc", "--version"]),
                              ("gcc-target", ["/usr/bin/gcc", "-dumpmachine"]),
                              ("ld", ["/usr/bin/ld", "--version"]),
                              ("make", ["/usr/bin/make", "--version"]),
                              ("pahole", ["/usr/bin/pahole", "--version"])):
            self.run("identity-" + name, command, 60)
        self.run("kernel-build", [*MAKE, "-j6", "bzImage", "modules"])
        require(self.run("release-after", [*MAKE, "-s", "kernelrelease"]).strip() == RELEASE,
                "Built release differs")
        check_config(baseline_config, (self.source / ".config").read_bytes())
        verify_source(self.source, self.expected)
        self.output.mkdir()
        for src, dest in (("arch/x86/boot/bzImage", "bzImage"), ("System.map", "System.map"),
                          (".config", "config")):
            shutil.copyfile(self.source / src, self.output / dest)
        (self.output / "version.txt").write_text(RELEASE + "\n")
        modules = self.output / "modules"
        self.run("modules-install", [*MAKE, "INSTALL_MOD_PATH=" + str(modules),
                                     "INSTALL_MOD_STRIP=1", "modules_install"])
        module_root = modules / "lib/modules" / RELEASE
        for name in ("build", "source"):
            path = module_root / name
            if path.is_symlink():
                require(path.resolve().is_relative_to(self.work), "Unexpected installed link target")
                path.unlink()
            else:
                require(not path.exists(), "Unexpected installed link type")
        installed = tree(modules)
        require(all(row["kind"] == "file" for row in installed.values()), "Module tree contains symlinks")
        ko = {name: row for name, row in installed.items() if name.endswith(".ko")}
        baseline = json_read(self.m["base"]["modules"]["path"])
        matching_module_names(ko, baseline)
        for i, name in enumerate(sorted(ko)):
            value = self.run("module-" + str(i), ["/usr/bin/modinfo", "-F", "vermagic", modules / name], 60).strip()
            module_identity(modules / name, RELEASE, value)
            ko[name] = {**ko[name], "vermagic": value}
        check_config(baseline_config, (self.source / ".config").read_bytes())
        verify_source(self.source, self.expected)
        save(self.work / "BUILT-SOURCE.json", self.expected)
        save(self.work / "MODULES.json", ko)
        save(self.work / "MODULE-TREE.json", installed)
        self.artifacts = {name: sha(self.output / name) for name in ("bzImage", "System.map", "config", "version.txt")}
        self.artifacts["vmlinux"] = sha(self.source / "vmlinux")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=("prepare", "build"))
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--prepared-sha256")
    args = p.parse_args()
    w = Workflow(args.manifest, args.manifest_sha256, args.phase)
    result = {"status": "STARTED", "manifest_sha256": w.manifest_sha, "release": RELEASE,
              "environment": w.env, "compiler_and_tools": w.m["tools"],
              "hardware_acceptance": False, "compiled_review": False, "packaging_complete": False,
              "publication": "private only", "commands": w.commands}
    if args.phase == "build":
        result["previous_phase_receipt_sha256"] = args.prepared_sha256
    failed = None
    try:
        if args.phase == "prepare":
            w.prepare()
            result["extraction"] = w.extraction
            result["prepared_source_sha256"] = sha(w.work / "PREPARED-SOURCE.json")
        else:
            w.build(args.prepared_sha256)
            result["artifact_sha256"] = w.artifacts
            result["module_count"] = 41
            result["built_source_sha256"] = sha(w.work / "BUILT-SOURCE.json")
            result["modules_sha256"] = sha(w.work / "MODULES.json")
            result["module_tree_sha256"] = sha(w.work / "MODULE-TREE.json")
        result["status"] = "PASS_" + args.phase.upper()
    except Exception as error:
        failed = error
        result.update(status="FAILED_PRESERVED", error=type(error).__name__ + ": " + str(error))
    finally:
        try:
            w.check_inputs()
            result["base_and_frozen_inputs_preserved"] = True
        except Exception as error:
            failed = failed or error
            result.update(status="FAILED_PRESERVED", preservation_error=str(error))
        if w.owns_work:
            save(w.work / (args.phase.upper() + ".json"), result)
        print(json.dumps({"status": result["status"], "work": str(w.work), "packaging_complete": False}))
    if failed:
        raise failed


if __name__ == "__main__":
    main()
