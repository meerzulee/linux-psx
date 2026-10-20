#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare an independent private native UVD policy candidate; never boot it."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import subprocess

PROJECT_BASE = "35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2"
UPSTREAM = "a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea"
OLD_TARGET = "7.2-ps4-baikal-uvd-ready-dev4"
TARGET = "7.2-ps4-baikal-uvd-poll-dev1"
RELEASE = "7.2.11-psx-ps4-baikal-uvd-poll-dev1"
PATCH_NAME = "0006-drm-amdgpu-ps4-observe-ready-convention.patch"
READY_C = "drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.c"
BASE_READY_SHA = "9f62e1781b7cc3c885e25f53a106b017cc2aa9cb1f5c110c13796e61c2b8e4cc"
OVERLAY = (
    "platforms/ps4/config/7.2-psx-baikal-uvd-ready-dev4.config",
    f"platforms/ps4/patches/{OLD_TARGET}/0005-drm-amdgpu-ps4-uvd-passive-observation.patch",
    f"platforms/ps4/patches/{OLD_TARGET}/series",
    "private-build/check-uvd-ready-dev4.py",
    "private-build/native-uvd-ready-dev4.sh",
    "private-build/uvd-ready-dev4-source-hashes.json",
    f"provenance/{OLD_TARGET}.json",
    f"targets/{OLD_TARGET}.env",
)
INPUTS = (PATCH_NAME, "check.py", "fixture.c", "prepare_native.py", "build_native.py")
ENV = {**os.environ, "PATH": "/usr/bin:/bin:/usr/bin/core_perl", "GIT_OPTIONAL_LOCKS": "0", "PYTHONDONTWRITEBYTECODE": "1"}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command(argv, cwd=None):
    print("+ " + shlex.join(map(str, argv)), flush=True)
    subprocess.run(list(map(str, argv)), cwd=cwd, env=ENV, check=True)


def capture(argv, cwd=None):
    return subprocess.check_output(list(map(str, argv)), cwd=cwd, env=ENV).decode()


def git_names(repo, arguments):
    return [name for name in capture(["/usr/bin/git", "-C", repo, *arguments, "-z"]).split("\0") if name]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def env_file(path):
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        words = shlex.split(value, comments=True)
        require(len(words) == 1, f"unsupported env value: {name}")
        values[name] = words[0]
    return values


def file_record(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return {"symlink": os.readlink(path), "mode": stat.S_IMODE(info.st_mode)}
    require(stat.S_ISREG(info.st_mode), f"unexpected non-regular input: {path}")
    return {"sha256": sha(path), "size": info.st_size, "mode": stat.S_IMODE(info.st_mode)}


def tree_files(root):
    found = []
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in list(directories):
            path = Path(directory) / name
            if path.is_symlink():
                found.append(path.relative_to(root).as_posix())
                directories.remove(name)
        found.extend((Path(directory) / name).relative_to(root).as_posix() for name in files)
    return sorted(found)


def snapshot_baseline(baseline):
    source = baseline / "src" / OLD_TARGET
    project_names = git_names(baseline, ["ls-files"]) + git_names(baseline, ["ls-files", "--others", "--exclude-standard"])
    touched = git_names(source, ["diff", "HEAD", "--name-only"]) + git_names(source, ["ls-files", "--others", "--exclude-standard"])
    touched += [".config", "include/config/kernel.release"]
    output = baseline / "output" / OLD_TARGET
    return {
        "project_head": capture(["/usr/bin/git", "-C", baseline, "rev-parse", "HEAD"]).strip(),
        "project_status": capture(["/usr/bin/git", "-C", baseline, "status", "--porcelain=v1", "-uall"]),
        "project_inputs": {name: file_record(baseline / name) for name in sorted(set(project_names))},
        "source_head": capture(["/usr/bin/git", "-C", source, "rev-parse", "HEAD"]).strip(),
        "source_status": capture(["/usr/bin/git", "-C", source, "status", "--porcelain=v1", "-uall"]),
        "touched_source": {name: file_record(source / name) for name in sorted(set(touched))},
        "artifacts": {name: file_record(output / name) for name in tree_files(output)},
    }


def verify_baseline(baseline, before, report):
    after = snapshot_baseline(baseline)
    write_json(report, after)
    require(after == before, "frozen baseline changed; stop and preserve all evidence")


def inspect_source_independence(old_source, new_source):
    count = 0
    symlinks = 0
    for name in tree_files(new_source):
        new = new_source / name
        old = old_source / name
        if new.is_symlink():
            require(new.resolve().is_relative_to(new_source.resolve()), f"escaping source symlink: {name}")
            symlinks += 1
            continue
        require(not old.is_symlink(), f"source type mismatch: {name}")
        if old.exists():
            previous, current = old.stat(), new.stat()
            require((previous.st_dev, previous.st_ino) != (current.st_dev, current.st_ino), f"shared source inode: {name}")
        count += 1
    require(not (new_source / ".git/objects/info/alternates").exists(), "source has Git object alternates")
    return {"independent_regular_files": count, "contained_symlinks": symlinks, "cross_baseline_symlinks": 0}


def paths(workspace):
    return (
        workspace / "lab/uvd-2026-10-18/linux-psx-uvd-ready-dev4",
        workspace / "lab/uvd-2026-10-19/linux-psx-uvd-poll-dev1",
        workspace / "lab/uvd-2026-10-19/ready-policy/build",
    )


def input_inventory(candidate):
    tracked = sorted(set(git_names(candidate, ["ls-files"])))
    overlay = sorted(set(git_names(candidate, ["ls-files", "--others", "--exclude-standard"])))
    records = []
    for name in sorted(set(tracked + overlay)):
        path = candidate / name
        info = path.lstat()
        record = {"path": name, "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                  "kind": "symlink" if path.is_symlink() else "file", "bytes": info.st_size}
        if path.is_symlink():
            record["target"] = os.readlink(path)
            record["sha256"] = hashlib.sha256(os.readlink(path).encode()).hexdigest()
        else:
            record["sha256"] = sha(path)
        records.append(record)
    return {"schema": 1, "project_base_commit": PROJECT_BASE,
            "project_identity": PROJECT_BASE + "+UNCOMMITTED-dev4-plus-uvd-poll-dev1-overlay",
            "tracked_count": len(tracked), "untracked_overlay_files": overlay,
            "input_count": len(records), "files": records}


def finalize_inputs(workspace, input_dir):
    """Freeze packaging-complete tools before compilation; preserve revisions."""
    baseline, candidate, logs = paths(workspace)
    require(not (logs / "build-started.json").exists(), "build already claimed; tools cannot change")
    require(not (logs / "prebuild-inputs.json").exists(), "final input inventory already frozen")
    receipt = json.loads((logs / "prepared.json").read_text())
    frozen = candidate / "private-build/ready-policy"
    # This step may refresh orchestration tools, never audited source/fixtures.
    for name in (PATCH_NAME, "check.py", "fixture.c"):
        require(sha(input_dir / name) == receipt["input_sha256"][name], f"audited input changed: {name}")
        require(sha(frozen / name) == receipt["input_sha256"][name], f"frozen input changed: {name}")
    write_json(logs / "prepared-before-input-finalization.json", receipt)
    for name in ("prepare_native.py", "build_native.py"):
        if (input_dir / name).resolve() != (frozen / name).resolve():
            shutil.copy2(input_dir / name, frozen / name)
        receipt["input_sha256"][name] = sha(frozen / name)
    old_c = baseline / "src" / OLD_TARGET / READY_C
    require(sha(old_c) == BASE_READY_SHA, "baseline helper changed")
    baseline_copy = frozen / "baseline-ready-dev4.c"
    require(not baseline_copy.exists(), "existing baseline fixture copy retained")
    shutil.copy2(old_c, baseline_copy)
    receipt["input_sha256"][baseline_copy.name] = sha(baseline_copy)
    receipt["project_identity"] = PROJECT_BASE + "+" + receipt["project_overlay"]
    receipt["frozen_dev3_and_dev4_inputs_preserved"] = True
    write_json(candidate / f"provenance/{TARGET}.json", receipt)
    write_json(logs / "prepared.json", receipt)
    inventory = input_inventory(candidate)
    write_json(logs / "prebuild-inputs.json", inventory)
    print(f"Frozen complete input inventory: {inventory['tracked_count']} tracked + {len(inventory['untracked_overlay_files'])} overlay = {inventory['input_count']}", flush=True)


def prepare_base(workspace):
    baseline, candidate, logs = paths(workspace)
    logs.mkdir(parents=True, exist_ok=True)
    require(not candidate.exists(), f"candidate already exists and is retained: {candidate}")
    require(capture(["/usr/bin/git", "-C", baseline, "rev-parse", "HEAD"]).strip() == PROJECT_BASE, "unexpected baseline project")
    require(set(git_names(baseline, ["ls-files", "--others", "--exclude-standard"])) == set(OVERLAY), "baseline untracked overlay is not the exact eight inputs")
    command(["/usr/bin/git", "-C", baseline, "diff", "HEAD", "--exit-code", "--quiet"])
    source = baseline / "src" / OLD_TARGET
    require(capture(["/usr/bin/git", "-C", source, "rev-parse", "HEAD"]).strip() == UPSTREAM, "unexpected source base")
    require(sha(source / READY_C) == BASE_READY_SHA, "baseline ready helper mismatch")
    command(["/usr/bin/python3", baseline / "private-build/check-uvd-ready-dev4.py", source])
    command(["/usr/bin/sha256sum", "-c", "SHA256SUMS"], cwd=baseline / "output" / OLD_TARGET)
    before = snapshot_baseline(baseline)
    before_path = logs / "baseline-before.json"
    require(not before_path.exists(), "existing baseline receipt retained")
    write_json(before_path, before)
    command(["/usr/bin/git", "clone", "--no-hardlinks", "--no-checkout", baseline, candidate])
    command(["/usr/bin/git", "-C", candidate, "checkout", "--detach", PROJECT_BASE])
    for name in OVERLAY:
        destination = candidate / name
        require(not destination.exists(), f"overlay would overwrite tracked input: {name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(baseline / name, destination)
    # Copy the whole incremental source/cache tree, including independent Git
    # objects. Reflinks may share storage blocks, never mutable file inodes.
    new_source = candidate / "src" / TARGET
    new_source.parent.mkdir(parents=True)
    command(["/usr/bin/cp", "-a", "--reflink=auto", source, new_source])
    independence = inspect_source_independence(source, new_source)
    for name, record in before["project_inputs"].items():
        require(file_record(candidate / name) == record, f"copied project input differs: {name}")
    for name, record in before["touched_source"].items():
        require(file_record(new_source / name) == record, f"copied source input differs: {name}")
    verify_baseline(baseline, before, logs / "baseline-after-copy.json")
    write_json(logs / "base-prepared.json", {"status": "BASE_COPIED_NOT_PATCHED_OR_BUILT", "project_base": PROJECT_BASE, "source_base": UPSTREAM, "independence": independence})
    print(f"Independent base prepared: {candidate}", flush=True)


def prepare_overlay(workspace, input_dir, patch_sha, source_sha):
    baseline, candidate, logs = paths(workspace)
    require((logs / "base-prepared.json").exists(), "prepare base first")
    require(not (logs / "prepared.json").exists(), "existing prepared overlay retained")
    before = json.loads((logs / "baseline-before.json").read_text())
    verify_baseline(baseline, before, logs / "baseline-before-overlay.json")
    require(sha(input_dir / PATCH_NAME) == patch_sha, "reviewed patch digest mismatch")
    frozen = candidate / "private-build/ready-policy"
    frozen.mkdir()
    input_hashes = {}
    for name in INPUTS:
        require((input_dir / name).is_file(), f"missing input: {name}")
        shutil.copy2(input_dir / name, frozen / name)
        input_hashes[name] = sha(frozen / name)
    source = candidate / "src" / TARGET
    require(sha(source / READY_C) == BASE_READY_SHA, "candidate is not untouched dev4")
    new_patch_dir = candidate / "platforms/ps4/patches" / TARGET
    new_patch_dir.mkdir()
    shutil.copy2(frozen / PATCH_NAME, new_patch_dir / PATCH_NAME)
    old_series = candidate / f"platforms/ps4/patches/{OLD_TARGET}/series"
    series_text = old_series.read_text()
    entries = lambda text: [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    require(len(entries(series_text)) == 67, "baseline patch count mismatch")
    series = new_patch_dir / "series"
    series.write_text(series_text + f"\n# Default-off passive readiness classification hypothesis.\n../{TARGET}/{PATCH_NAME}\n")
    require(len(entries(series.read_text())) == 68, "candidate patch count mismatch")
    old_config = candidate / "platforms/ps4/config/7.2-psx-baikal-uvd-ready-dev4.config"
    new_config = candidate / "platforms/ps4/config/7.2-psx-baikal-uvd-poll-dev1.config"
    config = old_config.read_text()
    old_line = 'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-ready-dev4"'
    require(config.count(old_line) == 1, "unexpected baseline LOCALVERSION")
    new_config.write_text(config.replace(old_line, 'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-poll-dev1"'))
    old_env = candidate / "targets" / f"{OLD_TARGET}.env"
    new_env = candidate / "targets" / f"{TARGET}.env"
    new_env.write_text(old_env.read_text().replace("uvd-ready-dev4", "uvd-poll-dev1"))
    command(["/usr/bin/git", "apply", "--check", new_patch_dir / PATCH_NAME], cwd=source)
    command(["/usr/bin/git", "apply", new_patch_dir / PATCH_NAME], cwd=source)
    require(sha(source / READY_C) == source_sha, "reviewed output C digest mismatch")
    # The sole source delta relative to the copied dev4 source is this file.
    for name, record in before["touched_source"].items():
        if name != READY_C:
            require(file_record(source / name) == record, f"unexpected source delta: {name}")
    command(["/usr/bin/python3", frozen / "check.py", "--baseline", baseline / "src" / OLD_TARGET / READY_C,
             "--source", source / READY_C, "--headers", source / "drivers/gpu/drm/amd/include/asic_reg",
             "--report", logs / "host-native"])
    patch_records = []
    target_env = env_file(new_env)
    for entry in entries(series.read_text()):
        patch = candidate / target_env["PATCHES_DIR"] / entry
        require(patch.resolve().is_relative_to(candidate), f"escaping patch path: {entry}")
        patch_records.append({"entry": entry, "sha256": sha(patch)})
    source_records = {name: file_record(source / name) for name in before["touched_source"]}
    receipt = {
        "status": "PREPARED_HOST_CHECKED_NOT_BUILT", "project_base_commit": PROJECT_BASE,
        "project_overlay": "UNCOMMITTED-dev4-plus-uvd-poll-dev1-overlay", "project_clean": False,
        "upstream_commit": UPSTREAM, "target": TARGET, "release": RELEASE,
        "patch_sha256": patch_sha, "ready_source_sha256": source_sha,
        "input_sha256": input_hashes, "patches": patch_records, "active_patch_count": 68,
        "series_sha256": sha(series), "config_input_sha256": sha(new_config),
        "target_sha256": sha(new_env), "source_files": source_records,
        "baseline_snapshot_sha256": sha(logs / "baseline-before.json"),
        "host_receipt_sha256": sha(logs / "host-native/receipt.json"),
        "firmware_accessed": False, "hardware_tested": False,
    }
    verify_baseline(baseline, before, logs / "baseline-after-prepare.json")
    write_json(candidate / f"provenance/{TARGET}.json", receipt)
    write_json(logs / "prepared.json", receipt)
    print(f"Prepared and host-checked only: {TARGET}; patch {patch_sha}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--stage", choices=("base", "overlay", "finalize", "all"), default="all")
    parser.add_argument("--patch-sha256")
    parser.add_argument("--source-sha256")
    arguments = parser.parse_args()
    require(platform.system() == "Linux" and platform.machine() == "x86_64", "native Linux x86-64 required")
    require(not os.environ.get("CROSS_COMPILE"), "CROSS_COMPILE must be unset")
    workspace = arguments.workspace.resolve()
    if arguments.stage in ("base", "all"):
        prepare_base(workspace)
    if arguments.stage in ("overlay", "all"):
        require(arguments.patch_sha256 and arguments.source_sha256, "reviewed patch/source digests required")
        prepare_overlay(workspace, arguments.input_dir.resolve(), arguments.patch_sha256, arguments.source_sha256)
    if arguments.stage in ("finalize", "all"):
        finalize_inputs(workspace, arguments.input_dir.resolve())


if __name__ == "__main__":
    main()
