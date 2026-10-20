#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Package exact private ib-fetch inputs, full source and matching build identities."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

sys.dont_write_bytecode = True
import prepare_native as prep
import build_native as build


def hash_stream(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def verify_archive(path, expected):
    """Read every member back, checking path, type, mode, size and bytes."""
    allowed_directories = {parent.as_posix() for name in expected for parent in Path(name).parents if parent != Path(".")}
    seen = set()
    process = subprocess.Popen(["/usr/bin/zstd", "-q", "-d", "-c", path], stdout=subprocess.PIPE, env=prep.ENV)
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                name = prep.safe_name(member.name)
                if member.isdir():
                    prep.require(name in allowed_directories, f"unexpected archive directory: {name}")
                    continue
                prep.require(name not in seen and name in expected, f"duplicate or unexpected archive member: {name}")
                seen.add(name)
                record = expected[name]
                prep.require(member.mode == record["mode"], f"archive mode mismatch: {name}")
                if record["kind"] == "symlink":
                    prep.require(member.issym() and member.linkname == record["target"], f"archive symlink mismatch: {name}")
                else:
                    prep.require(member.isfile() and member.size == record["bytes"], f"archive file mismatch: {name}")
                    with archive.extractfile(member) as stream:
                        prep.require(hash_stream(stream) == record["sha256"], f"archive digest mismatch: {name}")
        prep.require(seen == set(expected), "archive missing expected files")
    finally:
        process.stdout.close()
        code = process.wait()
    prep.require(code == 0, "zstd verification failed")
    return {"members_verified": len(seen), "regular_files": sum(r["kind"] == "file" for r in expected.values())}


def archive(destination, entries, epoch, extras=None):
    """Write without overwriting, preserving per-file modes and source symlinks."""
    extras = extras or {}
    prep.require(len(entries) == len(set(entries)), "duplicate archive entries")
    prep.require(not set(entries) & set(extras), "extra archive member collides with source")
    expected = {}
    for name, path in entries.items():
        prep.safe_name(name)
        expected[name] = prep.file_record(path)
    for name, data in extras.items():
        prep.safe_name(name)
        expected[name] = {"kind": "file", "mode": 0o644, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    with destination.open("xb") as output:
        process = subprocess.Popen(["/usr/bin/zstd", "-q", "-T2", "-c"], stdin=subprocess.PIPE, stdout=output, env=prep.ENV)
        try:
            with tarfile.open(fileobj=process.stdin, mode="w|", format=tarfile.PAX_FORMAT) as target:
                for name, path in sorted(entries.items()):
                    info = target.gettarinfo(str(path), arcname=name)
                    prep.require(info.isfile() or info.issym(), f"special source file: {name}")
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = epoch
                    info.pax_headers = {}
                    if info.isfile():
                        with path.open("rb") as stream:
                            target.addfile(info, stream)
                    else:
                        target.addfile(info)
                for name, data in sorted(extras.items()):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o644
                    info.uid = info.gid = 0
                    info.mtime = epoch
                    target.addfile(info, io.BytesIO(data))
        finally:
            process.stdin.close()
            code = process.wait()
    prep.require(code == 0, "zstd archive creation failed")
    return verify_archive(destination, expected)


def package(workspace):
    prep.native_host()
    baseline, candidate, logs = prep.paths(workspace)
    receipt = json.loads((logs / "prepared.json").read_text())
    built = json.loads((logs / "build-receipt.json").read_text())
    output = candidate / "output" / prep.TARGET
    source = candidate / "src" / prep.TARGET
    values, inventory = build.validate_inputs(candidate, logs, receipt, built=True)
    prep.require(prep.sha(Path(__file__)) == receipt["input_sha256"]["package_native.py"], "running packager differs from frozen packager")
    build.validate_artifacts(output, built)
    build.validate_config(baseline / "src" / prep.OLD_TARGET / ".config", source / ".config")
    prep.require(prep.sha(source / "vmlinux") == built["vmlinux_sha256"], "linked kernel changed")
    prep.require(prep.sha(logs / "built-source-inputs.json") == built["source_inventory_sha256"], "built source inventory changed")
    source_files = json.loads((logs / "built-source-inputs.json").read_text())
    prep.require(prep.source_inventory(candidate, prep.TARGET) == source_files, "built source bytes changed")
    before = json.loads((logs / "baseline-before.json").read_text())
    prep.verify_baseline(baseline, before, logs / "baseline-before-package.json")
    prep.inspect_independence(before, candidate)
    prep.write_json(logs / "package-started.json", {"package_script_sha256": prep.sha(Path(__file__)),
                    "build_receipt_sha256": prep.sha(logs / "build-receipt.json")})
    outcome = {"status": "FAILED_OR_INTERRUPTED", "target": prep.TARGET, "release": prep.RELEASE}
    try:
        staging = output / "modules"
        module_tree = json.loads((logs / "module-tree.json").read_text())
        actual_tree = {}
        for path in prep.tree_paths(staging):
            prep.require(not path.is_symlink(), f"module tree gained a symlink: {path}")
            if not path.is_dir():
                actual_tree[path.relative_to(staging).as_posix()] = prep.file_record(path)
        prep.require(actual_tree == module_tree, "whole module tree changed")
        modules = json.loads((logs / "modules.json").read_text())
        prep.require(len(modules) == built["module_count"] == build.MODULE_COUNT, "module count mismatch")
        for name, record in modules.items():
            prep.require(module_tree[name] == {k: v for k, v in record.items() if k not in ("vermagic", "elf_header")}, f"module identity mismatch: {name}")
            prep.require(record["vermagic"].split()[0] == prep.RELEASE, f"module release mismatch: {name}")
            prep.require(build.module_elf_header(staging / name) == record["elf_header"], f"module ELF header changed: {name}")
        modules_name = f"modules-{prep.RELEASE}.tar.zst"
        module_check = verify_archive(output / modules_name, module_tree)
        prep.require(module_check["regular_files"] == built["archive_regular_files"], "module archive file count changed")
        inputs = {**inventory, "scope": "Exact private ib-fetch build inputs, preserving all frozen writeback-dev1 inputs",
                  **prep.BASELINE_AUDIT,
                  "project_clean": False, "all737_writeback_dev1_inputs_preserved": True,
                  "file_count": inventory["input_count"], "upstream_commit": prep.UPSTREAM,
                  "target": prep.TARGET, "release": prep.RELEASE, "active_patch_count": prep.PATCH_COUNT}
        prep.write_json(logs / "inputs.json", inputs)
        epoch = int(values["SOURCE_DATE_EPOCH"])
        input_files = {row["path"]: candidate / row["path"] for row in inventory["files"]}
        checks = {"inputs": archive(logs / "inputs.tar.zst", input_files, epoch,
                                     {"PSX-INPUTS-MANIFEST.json": (logs / "inputs.json").read_bytes()})}
        overlay = inventory["untracked_overlay_files"]
        checks["overlay"] = archive(logs / "dirty-overlay.tar.zst", {name: candidate / name for name in overlay}, epoch,
                                     {"PSX-OVERLAY-MANIFEST.json": (json.dumps({"base_commit": prep.PROJECT_BASE,
                                       "files": [row for row in inventory["files"] if row["path"] in overlay]}, indent=2) + "\n").encode()})
        source_manifest = {"schema": 1, "source": str(source), "target": prep.TARGET, "release": prep.RELEASE,
                           **prep.BASELINE_AUDIT,
                           "selection": "All tracked upstream files, active patch outputs including ignored additions, and resolved .config",
                           "generated_objects_or_git_history": False, "file_count": len(source_files),
                           "inputs_archive_sha256": prep.sha(logs / "inputs.tar.zst"),
                           "files": [{"path": name, **row} for name, row in source_files.items()]}
        prep.write_json(logs / "source-inputs.json", source_manifest)
        rebuild = f'''# Private ib-fetch-dev1 corresponding source

Target: {prep.TARGET}
Release: {prep.RELEASE}
Project: {receipt['project_identity']}
Upstream: {prep.UPSTREAM}

This archive contains complete patched Linux source, resolved .config, COPYING,
LICENSES and all {prep.PATCH_COUNT} patches' outputs. PSX-BUILD-INPUTS.tar.zst
contains {inventory['input_count']} exact project inputs, including all
{prep.BASE_INPUT_COUNT} unchanged writeback-dev1 inputs and this uncommitted overlay.
No commit, release, installation or hardware acceptance is claimed. Firmware
remains external; no firmware or initramfs payload is embedded or bundled.
The predecessor's compiled audit remains incomplete; this build and package
do not complete it. Its status-only receipt is referenced in the manifests.

On Linux x86-64 with the recorded native toolchain, rebuild extracted source:

    export PATH=/usr/bin:/bin:/usr/bin/core_perl
    unset CROSS_COMPILE MAKEFLAGS MFLAGS
    export SOURCE_DATE_EPOCH={epoch}
    export KBUILD_BUILD_TIMESTAMP='{values['KBUILD_RELEASE_TIMESTAMP']}'
    export KBUILD_BUILD_USER={values['KBUILD_RELEASE_USER']} KBUILD_BUILD_HOST={values['KBUILD_RELEASE_HOST']}
    export KBUILD_BUILD_VERSION={values['KBUILD_RELEASE_VERSION']}
    make ARCH=x86_64 LOCALVERSION= olddefconfig
    make ARCH=x86_64 LOCALVERSION= syncconfig
    make ARCH=x86_64 LOCALVERSION= -j6 bzImage modules

Use the included private-build/ib-fetch source tools and manifests for
the full source/config/whole-module identity checks when reconstructing the
matching private Git checkout. Tool version and environment differences may
change rebuilt bytes; compare hashes instead of assuming binary identity.
This package contains no media-write, console or publication instruction.
'''.encode()
        prefix = f"linux-{prep.RELEASE}"
        source_name = f"source-{prep.RELEASE}.tar.zst"
        checks["source"] = archive(logs / source_name, {f"{prefix}/{name}": source / name for name in source_files}, epoch,
                                    {f"{prefix}/PSX-BUILD-INPUTS.tar.zst": (logs / "inputs.tar.zst").read_bytes(),
                                     f"{prefix}/PSX-BUILD-MANIFEST.txt": (output / "manifest.txt").read_bytes(),
                                     f"{prefix}/PSX-SOURCE-INPUTS.json": (logs / "source-inputs.json").read_bytes(),
                                     f"{prefix}/PSX-REBUILD.md": rebuild})
        source_archive_sha = prep.sha(logs / source_name)
        artifacts = {"schema": 1, "target": prep.TARGET, "platform": values["PLATFORM"], "kernel_release": prep.RELEASE,
                     "kernel_sha256": prep.sha(output / "bzImage"), "kernel_config_sha256": prep.sha(output / "config"),
                     "embedded_firmware": False, "modules_archive": {"path": modules_name, "sha256": prep.sha(output / modules_name)},
                     "module_count": len(modules), "archive_regular_files": module_check["regular_files"],
                     "source_archive": {"path": source_name, "sha256": source_archive_sha},
                     "project_identity": receipt["project_identity"], "project_base_commit": prep.PROJECT_BASE,
                     "upstream_commit": prep.UPSTREAM, "active_patch_count": prep.PATCH_COUNT,
                     "inputs_file_count": inventory["input_count"], "frozen_baseline_input_count": prep.BASE_INPUT_COUNT,
                     "uncommitted_overlay_files": overlay, "ib_fetch_patch_sha256": receipt["ib_fetch_patch_sha256"],
                     "writeback_patch_sha256": prep.WRITEBACK_PATCH_SHA, **prep.BASELINE_AUDIT,
                     "token_patch_sha256": prep.TOKEN_PATCH_SHA,
                     "policy_patch_sha256": prep.POLICY_PATCH_SHA, "ready_source_sha256": prep.sha(source / prep.READY_C),
                     "hardware_acceptance": "none; build and source checks only", "publication": "private-uncommitted-candidate"}
        prep.write_json(logs / "artifacts.json", artifacts)
        prep.write_json(logs / "archive-verification.json", {**checks, "modules": module_check})
        build.validate_inputs(candidate, logs, receipt, built=True)
        build.validate_artifacts(output, built)
        prep.require(prep.source_inventory(candidate, prep.TARGET) == source_files, "source changed while packaging")
        outcome.update({**artifacts, "status": "PASS_PRIVATE_SOURCE_PACKAGE_NO_HARDWARE_ACCEPTANCE",
                        "input_files": inventory["input_count"], "all737_writeback_dev1_inputs_preserved": True,
                        "new_overlay_files": len(overlay), "packaging_script_sha256": prep.sha(Path(__file__)),
                        "complete_source_input_files": len(source_files), "source_archive_bytes": (logs / source_name).stat().st_size,
                        "inputs_archive_sha256": prep.sha(logs / "inputs.tar.zst"),
                        "dirty_overlay_sha256": prep.sha(logs / "dirty-overlay.tar.zst"),
                        "source_archive_sha256": source_archive_sha})
    except BaseException as error:
        outcome["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        try:
            prep.verify_baseline(baseline, before, logs / "baseline-after-package.json")
            outcome["baseline_preserved"] = True
        except BaseException as error:
            outcome.update(status="FAIL_BASELINE_CHANGED", baseline_preserved=False, baseline_error=str(error))
            raise
        finally:
            prep.write_json(logs / "source-package.json", outcome)
    checksum_names = ["inputs.tar.zst", "dirty-overlay.tar.zst", source_name, "artifacts.json", "source-package.json",
                      "inputs.json", "source-inputs.json", "archive-verification.json", "build-receipt.json"]
    with (logs / "PACKAGE-SHA256SUMS").open("x") as stream:
        stream.write("".join(f"{prep.sha(logs / name)}  {name}\n" for name in checksum_names))
    prep.command(["/usr/bin/sha256sum", "-c", "PACKAGE-SHA256SUMS"], cwd=logs)
    print(json.dumps({key: outcome[key] for key in ("status", "target", "inputs_file_count", "module_count", "source_archive_sha256", "baseline_preserved")}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    arguments = parser.parse_args()
    package(arguments.workspace.resolve())


if __name__ == "__main__":
    main()
