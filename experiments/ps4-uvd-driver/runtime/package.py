#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Private runtime package from exact final source/link pins; no device actions.

Reuses a hash-reviewed native_package.py for the immutable frame contract,
copying, enumerated archives and complete archive readback. The only build
target is modules_install into the new private stage, followed by depmod.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
from types import SimpleNamespace


RELEASE = "7.2.11-psx-ps4-baikal-uvd-runtime-dev1"
PREFIX = "drivers/gpu/drm/amd/amdgpu/"
HELPER_SHA256 = "cd4761adae17d936240429555cec73965cf5416306a876fb65ef8eedfa03b0bf"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def load_helper(path):
    require(sha(path) == HELPER_SHA256, "Reviewed archive helper SHA256 differs")
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("reviewed_runtime_archive_helper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def derive_inventory(h, baseline, frame_tree, runtime_tree, receipt, base_config, config):
    """Source completeness is anchored to the whole frozen frame source inventory."""
    old = b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-frame-dev1"'
    new = b'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-runtime-dev1"'
    require(base_config.count(old) == 1 and config == base_config.replace(old, new),
            "Configuration differs beyond the exact LOCALVERSION substitution")
    require(b'CONFIG_EXTRA_FIRMWARE=""\n' in config and
            b'CONFIG_INITRAMFS_SOURCE=""\n' in config, "Embedded firmware/initramfs forbidden")
    require(receipt.get("schema") == 1 and receipt.get("status") == "SOURCE_CANDIDATE_ONLY" and
            receipt.get("hardware_acceptance") is False and receipt["candidate_files"],
            "Expected exact private source candidate receipt")
    expected = copy.deepcopy(baseline)
    for name, pin in receipt["base_files"].items():
        h.safe_name(name)
        require("/" not in name and baseline[PREFIX + name]["sha256"] == pin,
                "Runtime receipt is not derived from this frame source: " + name)
    for name, pin in receipt["candidate_files"].items():
        h.safe_name(name)
        require("/" not in name and (name == "Makefile" or name.endswith((".c", ".h"))),
                "Candidate outside AMDGPU source scope")
        row = runtime_tree[PREFIX + name]
        require(row["kind"] == "file" and row["mode"] == 0o644 and row["sha256"] == pin,
                "Final candidate source identity differs: " + name)
        expected[PREFIX + name] = row
    expected[".config"] = runtime_tree[".config"]
    require(all(runtime_tree.get(name) == row for name, row in expected.items()),
            "Complete runtime source does not equal frame source plus declared delta/config")
    # Built trees also contain generated objects. Accept the existing frozen
    # frame generated path set, .version, and only the candidate C object/cmd
    # paths. Unknown source or generated additions cannot disappear from audit.
    allowed = set(frame_tree) | set(expected) | {".version"}
    for name in receipt["candidate_files"]:
        if name.endswith(".c"):
            obj = name[:-2] + ".o"
            allowed.update((PREFIX + obj, PREFIX + "." + obj + ".cmd"))
    require(set(runtime_tree) <= allowed, "Unexpected file in built runtime source tree")
    require(all(row["kind"] == "file" or
                (row["kind"] == "symlink" and row == frame_tree.get(name))
                for name, row in runtime_tree.items() if name not in expected),
            "Generated runtime tree contains a new/changed link or special file")
    h.validate_inventory(expected)
    return expected


class Package:
    def __init__(self, args):
        self.args, self.owns_output, self.commands = args, False, []
        require(platform.system() == "Linux" and platform.machine() == "x86_64",
                "Native Linux x86-64 only")
        self.h = load_helper(args.archive_helper.resolve(strict=True))
        h = self.h
        self.output = args.output
        self.base = h.Package(SimpleNamespace(
            manifest=args.frame_manifest, manifest_sha256=args.frame_manifest_sha256,
            build_tool=args.frame_build_tool, build_sha256=args.frame_build_sha256,
            output=self.output, receipt=[], include_build_logs=False))
        self.work = args.runtime_work.resolve(strict=True)
        self.source = self.work / "source"
        require(not self.source.is_symlink() and self.source.is_dir(), "Invalid runtime source")
        require(not self.output.is_relative_to(self.work) and not self.work.is_relative_to(self.output),
                "Package output overlaps runtime work")
        self.pins = {}
        self.extra = {}

        def pin(path, expected):
            require(h.record(path)["kind"] == "file", "Pinned runtime input must not be a link")
            path = path.resolve(strict=True)
            require(re.fullmatch(r"[0-9a-f]{64}", expected) and sha(path) == expected,
                    "Exact input pin differs: " + str(path))
            require(not path.is_relative_to(self.output), "Input inside package output")
            row = h.record(path)
            require(row["kind"] == "file", "Input must be regular")
            require(path not in self.pins or self.pins[path] == row, "Conflicting input pin")
            self.pins[path] = row
            return path

        self.source_receipt = pin(self.work / "SOURCE-RECEIPT.json", args.source_receipt_sha256)
        self.link_receipt = pin(self.work / "LINK-ATTEMPT-06.json", args.link_receipt_sha256)
        self.receipt = h.read_json(self.source_receipt)
        link = h.read_json(self.link_receipt)
        require(link["status"] == "PASS_LINK" and link["returncode"] == 0 and
                link["source_receipt_sha256"] == args.source_receipt_sha256,
                "Exact successful final link required")
        self.runtime_inputs = args.runtime_inputs.resolve(strict=True)
        require(not self.output.is_relative_to(self.runtime_inputs), "Output inside runtime inputs")
        input_pins = {"build_candidate.py": self.receipt["generator_sha256"], **self.receipt["inputs"]}
        require(set(h.tree(self.runtime_inputs)) == set(input_pins), "Frozen runtime build input set differs")
        self.new_inputs = {name: pin(self.runtime_inputs / h.safe_name(name), value)
                           for name, value in input_pins.items()}
        self.delta = pin(self.work / h.safe_name(self.receipt["patch"]["path"]),
                         self.receipt["patch"]["sha256"])
        pin(Path(__file__), sha(__file__))
        pin(args.archive_helper, HELPER_SHA256)
        for value in args.receipt:
            name, path, expected = h.receipt_arg(value)
            require(name not in self.extra, "Duplicate extra receipt")
            self.extra[name] = pin(path, expected)
        self.frame_tree = h.tree(self.base.source)
        self.runtime_tree = h.tree(self.source)
        self.inventory = derive_inventory(h, self.base.inventory, self.frame_tree, self.runtime_tree,
                                          self.receipt, (self.base.source / ".config").read_bytes(),
                                          (self.source / ".config").read_bytes())
        self.artifacts = {"bzImage": self.source / "arch/x86/boot/bzImage",
                          "config": self.source / ".config", "System.map": self.source / "System.map",
                          "vmlinux": self.source / "vmlinux"}
        pin(self.artifacts["bzImage"], args.kernel_sha256)
        pin(self.artifacts["config"], args.config_sha256)
        for path in self.artifacts.values():
            pin(path, sha(path))
        with self.artifacts["vmlinux"].open("rb") as handle:
            raw = handle.read(20)
        require(len(raw) == 20 and raw[:6] == b"\x7fELF\x02\x01" and
                struct.unpack("<HH", raw[16:20]) == (2, 62), "Wrong vmlinux ELF")
        require((self.source / "include/config/kernel.release").read_text() == RELEASE + "\n" and
                (self.source / "include/generated/utsrelease.h").read_text() ==
                '#define UTS_RELEASE "' + RELEASE + '"\n', "Built release differs")
        self.env = dict(self.base.w.env, TMPDIR=str(self.output / "tmp"),
                        GIT_CEILING_DIRECTORIES=str(self.output))

    def check_inputs(self):
        h = self.h
        self.base.check_inputs()
        require(all(h.record(path) == row for path, row in self.pins.items()), "Pinned runtime input changed")
        require(h.tree(self.base.source) == self.frame_tree, "Frozen frame tree changed")
        require(h.tree(self.source) == self.runtime_tree, "Runtime built tree changed")

    def command(self, name, argv, cwd=None):
        logs = self.output / "logs"
        stdout, stderr = logs / (name + ".stdout"), logs / (name + ".stderr")
        row = {"argv": [str(arg) for arg in argv], "cwd": str(cwd or self.source)}
        self.commands.append(row)
        begin = time.monotonic()
        try:
            with stdout.open("xb") as out, stderr.open("xb") as err:
                result = subprocess.run(row["argv"], cwd=cwd or self.source, env=self.env,
                                        stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=600)
            row["returncode"] = result.returncode
        finally:
            row.update(elapsed_seconds=time.monotonic() - begin,
                       stdout_sha256=sha(stdout), stderr_sha256=sha(stderr))
        require(row.get("returncode") == 0, "Command failed: " + name)
        return stdout.read_text(errors="replace").strip()

    def install_modules(self):
        h = self.h
        stage = self.output / "stage"
        self.command("modules-install", [*self.base.b.MAKE, "INSTALL_MOD_PATH=" + str(stage),
                     "INSTALL_MOD_STRIP=1", "DEPMOD=/usr/bin/depmod", "modules_install"])
        root = stage / "lib/modules" / RELEASE
        for name in ("build", "source"):
            path = root / name
            if path.is_symlink():
                require(path.resolve(strict=True) == self.source, "Unexpected installed link")
                path.unlink()  # Only the new private stage's standard build/source links.
            else:
                require(not path.exists(), "Unexpected installed build/source file")
        self.command("depmod", ["/usr/bin/depmod", "-b", stage, "-F", self.source / "System.map", RELEASE])
        self.module_tree = h.tree(stage)
        h.validate_inventory(self.module_tree, links=False)
        require(all(name.startswith("lib/modules/" + RELEASE + "/") for name in self.module_tree),
                "Installed paths outside matching module release")
        self.modules = {name: row for name, row in self.module_tree.items() if name.endswith(".ko")}
        expected = {name.replace("/" + self.base.b.RELEASE + "/", "/" + RELEASE + "/")
                    for name in self.base.modules}
        require(len(expected) == 41 and set(self.modules) == expected, "Complete matching 41-module set differs")
        for index, name in enumerate(sorted(self.modules)):
            vermagic = self.command("module-" + str(index), ["/usr/bin/modinfo", "-F", "vermagic", stage / name])
            self.base.b.module_identity(stage / name, RELEASE, vermagic)
            self.modules[name] = {**self.modules[name], "vermagic": vermagic}
        for name in ("modules.dep", "modules.alias", "modules.symbols", "modules.builtin"):
            require((root / name).is_file(), "Missing depmod/module metadata: " + name)
        h.save(self.output / "MODULE-TREE.json", self.module_tree)
        h.save(self.output / "MODULES.json", self.modules)

    def stage_inputs(self):
        h = self.h
        root = self.base.stage_inputs()  # Exact frame patches, tools, reviews, receipts and original lineage.
        (self.output / "BUILD-INPUTS.json").rename(root / "FRAME-BUILD-INPUTS.json")
        copies = {"runtime/SOURCE-RECEIPT.json": self.source_receipt,
                  "runtime/LINK-ATTEMPT-06.json": self.link_receipt,
                  "runtime/patches/" + self.delta.name: self.delta,
                  "tools/runtime-package.py": Path(__file__).resolve(),
                  **{"runtime/inputs/" + name: path for name, path in self.new_inputs.items()},
                  **{"runtime/extra-receipts/" + name: path for name, path in self.extra.items()}}
        for name, source in copies.items():
            h.copy_file(source, root / name)
        (root / "runtime/patches/series").write_text(self.delta.name + "\n")
        self.inputs_inventory = h.tree(root)
        h.validate_inventory(self.inputs_inventory, links=False)
        h.save(self.output / "BUILD-INPUTS.json", self.inputs_inventory)
        return root

    def check_delta(self):
        h = self.h
        directory = self.output / "delta-check"
        directory.mkdir()
        pairs = re.findall(r"^diff --git a/(\S+) b/(\S+)$", self.delta.read_text(), re.M)
        wanted = {PREFIX + name for name in self.receipt["candidate_files"]}
        require(pairs and len(pairs) == len(wanted) and {a for a, b in pairs if a == b} == wanted,
                "Runtime patch path set differs")
        for name in wanted & set(self.base.inventory):
            h.copy_file(self.base.source / name, directory / name)
        for step in ("check", "apply"):
            self.command("delta-" + step, ["/usr/bin/git", "apply", "--whitespace=nowarn",
                         *(["--check"] if step == "check" else []), self.delta], cwd=directory)
        require(h.tree(directory) == {name: self.inventory[name] for name in wanted},
                "Runtime patch does not reproduce exact candidate bytes/modes")

    def build(self):
        h = self.h
        space = os.statvfs(self.output.parent)
        floor = sum(row["bytes"] for row in self.inventory.values()) + \
                sum(path.stat().st_size for path in self.artifacts.values()) + 1024**3
        require(space.f_bavail * space.f_frsize >= floor, "Insufficient private package free space")
        self.output.mkdir(mode=0o700)
        self.owns_output = True
        for name in ("logs", "tmp", "metadata"):
            (self.output / name).mkdir()
        self.check_delta()
        self.install_modules()
        self.check_inputs()
        inputs = self.stage_inputs()
        metadata = self.output / "metadata"
        self.readbacks = {}
        self.readbacks["PSX-BUILD-INPUTS.tar.zst"] = h.archive(Path("/usr/bin/zstd"),
            metadata / "PSX-BUILD-INPUTS.tar.zst", "PSX-BUILD-INPUTS", self.inputs_inventory,
            {name: inputs / name for name in self.inputs_inventory}, self.output / "logs", links=False)
        h.save(metadata / "PSX-SOURCE-INPUTS.json", self.inventory)
        h.save(metadata / "PSX-BUILD-MANIFEST.json", {
            "schema": 1, "status": "PRIVATE_EXPERIMENTAL_UVD_RUNTIME_CORRESPONDING_SOURCE",
            "release": RELEASE, "source_receipt_sha256": self.args.source_receipt_sha256,
            "link_receipt_sha256": self.args.link_receipt_sha256,
            "frame_build_sha256": self.args.frame_build_sha256,
            "archive_helper_sha256": HELPER_SHA256, "packaging_tool_sha256": sha(__file__),
            "build_inputs_archive_sha256": self.readbacks["PSX-BUILD-INPUTS.tar.zst"]["sha256"],
            "hardware_acceptance": False, "compiled_review": False, "publication": "private only"})
        (metadata / "PSX-REBUILD.md").write_text(
            "# Private experimental runtime source\n\nThis is the complete source inventory of the frozen frame build, with only the\n"
            "declared runtime candidate files and CONFIG_LOCALVERSION changed. All notices\n"
            "remain intact. Generated objects are not source archive members. The three\n"
            "frame patches and the final runtime delta are already applied; do not reapply.\n\n"
            "PSX-BUILD-INPUTS.tar.zst preserves frame receipts/patches/series/tools and\n"
            "original base-metadata as lineage, plus exact runtime generator/inputs/delta,\n"
            "source/link receipts and supplied review/test receipts. Frame reviews do not\n"
            "review this runtime binary. No device firmware or initramfs is bundled.\n\n"
            "Rebuild on native Linux x86-64 with the saved compiler environment/tool pins\n"
            "in receipts/BUILD.json. With the resolved .config preserved:\n\n"
            "    make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc clean\n"
            "    make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc olddefconfig\n"
            "    make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc -j6 bzImage modules\n\n"
            "The original runtime link receipt records the incremental final build; these\n"
            "commands describe a clean reconstruction, not a claim of byte-identical output.\n"
            "Packaging commands and all 41 installed modules/vermagic are in PACKAGE.json\n"
            "and MODULES.json beside the source archive. Packaging has no compiled review,\n"
            "hardware acceptance, installation or publication claim.\n")
        source_inventory = {**self.inventory, **h.tree(metadata)}
        require(len(source_inventory) == len(self.inventory) + len(h.tree(metadata)), "Metadata/source collision")
        h.save(self.output / "SOURCE-ARCHIVE.json", source_inventory)
        source_name, module_name = "source-" + RELEASE + ".tar.zst", "modules-" + RELEASE + ".tar.zst"
        self.readbacks[source_name] = h.archive(Path("/usr/bin/zstd"), self.output / source_name,
            "linux-" + RELEASE, source_inventory,
            {name: (self.source if name in self.inventory else metadata) / name for name in source_inventory},
            self.output / "logs")
        h.save(self.output / "MODULE-ARCHIVE.json", self.module_tree)
        self.readbacks[module_name] = h.archive(Path("/usr/bin/zstd"), self.output / module_name, "lib",
            {name[4:]: row for name, row in self.module_tree.items()},
            {name[4:]: self.output / "stage" / name for name in self.module_tree}, self.output / "logs", links=False)
        for name, path in self.artifacts.items():
            h.copy_file(path, self.output / name)
        (self.output / "version.txt").write_text(RELEASE + "\n")
        self.artifact_hashes = {name: sha(self.output / name) for name in
                               (*self.artifacts, "version.txt", source_name, module_name)}
        h.save(self.output / "RUNTIME-IDENTITY.json", {
            "schema": 1, "release": RELEASE, "hardware_acceptance": False, "compiled_review": False,
            "publication": "private only", "source_receipt_sha256": self.args.source_receipt_sha256,
            "link_receipt_sha256": self.args.link_receipt_sha256, "kernel_sha256": self.artifact_hashes["bzImage"],
            "config_sha256": self.artifact_hashes["config"], "vmlinux_sha256": self.artifact_hashes["vmlinux"],
            "source_archive": {"name": source_name, "sha256": self.artifact_hashes[source_name]},
            "module_archive": {"name": module_name, "sha256": self.artifact_hashes[module_name]},
            "module_count": len(self.modules), "modules": self.modules,
            "module_tree_sha256": sha(self.output / "MODULE-TREE.json")})

    def run(self):
        h = self.h
        result = {"schema": 1, "release": RELEASE, "hardware_acceptance": False,
                  "compiled_review": False, "publication": "private only", "commands": self.commands,
                  "source_receipt_sha256": self.args.source_receipt_sha256,
                  "link_receipt_sha256": self.args.link_receipt_sha256, "packaging_complete": False}
        error = None
        try:
            self.build()
            require(h.tree(self.output / "stage") == self.module_tree, "Installed module stage changed")
            require(h.tree(self.output / "build-inputs") == self.inputs_inventory, "Staged build inputs changed")
            result.update(artifact_sha256=self.artifact_hashes, archive_readbacks=self.readbacks,
                          source_inputs=len(self.inventory), module_count=len(self.modules))
        except (Exception, KeyboardInterrupt) as caught:
            error = caught
            result["error"] = str(caught) or type(caught).__name__
        try:
            self.check_inputs()
            result["all_build_and_frozen_inputs_preserved"] = True
        except Exception as caught:
            error = error or caught
            result["preservation_error"] = str(caught)
        result.update(status="FAILED_PRESERVED" if error else "PASS_PACKAGE", packaging_complete=error is None)
        if self.owns_output:
            if not error:
                try:
                    rows = h.tree(self.output)
                    require(all(row["kind"] == "file" for row in rows.values()), "Private output contains links")
                    rows["PACKAGE.json"] = {"sha256": hashlib.sha256(h.json_bytes(result)).hexdigest()}
                    with (self.output / "SHA256SUMS").open("x") as sums:
                        sums.write("".join(rows[name]["sha256"] + "  " + h.safe_name(name) + "\n"
                                           for name in sorted(rows)))
                except Exception as caught:
                    result.update(status="FAILED_PRESERVED", packaging_complete=False, checksum_error=str(caught))
            h.save(self.output / "PACKAGE.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive-helper", "frame-manifest", "frame-build-tool", "runtime-work", "runtime-inputs", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("frame-manifest-sha256", "frame-build-sha256", "source-receipt-sha256", "link-receipt-sha256",
                 "kernel-sha256", "config-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--receipt", action="append", default=[], help="NAME=ABSOLUTE_PATH=SHA256")
    args = parser.parse_args()
    try:
        result = Package(args).run()
        print(json.dumps({"status": result["status"], "output": str(args.output),
                          "hardware_acceptance": False, "compiled_review": False}))
        return 0 if result["packaging_complete"] else 1
    except Exception as error:
        print(json.dumps({"status": "FAILED_PREFLIGHT", "error": str(error)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
