#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare an independent token candidate from frozen poll-dev1; never boot it."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import stat
import subprocess

PROJECT_BASE = "35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2"
UPSTREAM = "a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea"
OLD_TARGET = "7.2-ps4-baikal-uvd-poll-dev1"
TARGET = "7.2-ps4-baikal-uvd-token-dev2"
RELEASE = "7.2.11-psx-ps4-baikal-uvd-token-dev2"
PROJECT_OVERLAY = "UNCOMMITTED-dev4-plus-uvd-poll-dev1-plus-uvd-token-dev2-overlay"
PATCH_NAME = "0007-drm-amdgpu-ps4-one-shot-uvd-token.patch"
BASE_INPUT_COUNT = 700
BASE_PATCH_COUNT = 68
PATCH_COUNT = 69
POLICY_PATCH_SHA = "8a83bb6e7f97b978d81314b138be7340e2326bd86434e5291eb07ee3f78cf16b"
BASE_INPUT_MANIFEST_SHA = "d0ba69f9bad87a20364564f7d58ddec8d2ea6f82f84807c54458466d4c9d382b"
BASE_SOURCE_MANIFEST_SHA = "d5ca7f8c0049a5a483ac23e8ac0add98c7008261f02a01aaee9483e94c66041a"
BASE_BUILD_RECEIPT_SHA = "c85a40bd1f7121a229a28d49293542b1ca28ecf4944b87538aa3d646b8725813"
AMD = "drivers/gpu/drm/amd/amdgpu/"
READY_C = AMD + "amdgpu_ps4_uvd_ready.c"
CANARY_C = AMD + "amdgpu_ps4_map_canary.c"
READY_H = AMD + "amdgpu_ps4_uvd_ready.h"
PLAN_H = AMD + "amdgpu_ps4_uvd_ring_plan.h"
BASE_SOURCE_SHA = {
    CANARY_C: "a5cc36b7d917fae7d674b79476c58f58005d0100881dca51ede6c713d7c39ddd",
    READY_C: "57459edfca80983b1d248704ce790ad8dd5c08a9ac6bc08910828f6b083779c7",
    READY_H: "198a840658c7d816c48b01f3229d42f663105baca9e2a7e04a3f46f886d10a5b",
}
CHANGED_SOURCE = frozenset((*BASE_SOURCE_SHA, PLAN_H))
INPUTS = (PATCH_NAME, "check.py", "fixture.c", "README.md", "prepare_native.py",
          "build_native.py", "package_native.py")
PRIVATE = "private-build/token-transport-v2"
# Do not inherit MAKEFLAGS, compiler overrides, PYTHONPATH or mise activation.
ENV = {"PATH": "/usr/bin:/bin:/usr/bin/core_perl", "LC_ALL": "C", "TZ": "UTC",
       "GIT_OPTIONAL_LOCKS": "0", "PYTHONDONTWRITEBYTECODE": "1"}
if "HOME" in os.environ:
    ENV["HOME"] = os.environ["HOME"]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command(argv, cwd=None):
    print("+ " + shlex.join(map(str, argv)), flush=True)
    subprocess.run(list(map(str, argv)), cwd=cwd, env=ENV, check=True)


def capture(argv, cwd=None):
    return subprocess.check_output(list(map(str, argv)), cwd=cwd, env=ENV).decode()


def git_names(repo, *arguments):
    return sorted(set(name for name in capture(
        ["/usr/bin/git", "-C", repo, "ls-files", "-z", *arguments]).split("\0") if name))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def safe_name(name):
    path = Path(name)
    require(name and not path.is_absolute() and ".." not in path.parts and
            path.as_posix() == name and "\n" not in name and "\0" not in name,
            f"unsafe relative path: {name!r}")
    return name


def contained(root, name):
    path = root / safe_name(name)
    require(path.resolve().is_relative_to(root.resolve()), f"escaping path: {name}")
    return path


def file_record(path):
    info = path.lstat()
    record = {"mode": stat.S_IMODE(info.st_mode), "bytes": info.st_size}
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(path)
        record.update(kind="symlink", target=target,
                      sha256=hashlib.sha256(os.fsencode(target)).hexdigest())
    else:
        require(stat.S_ISREG(info.st_mode), f"non-regular file: {path}")
        record.update(kind="file", sha256=sha(path))
    return record


def normalize_record(row):
    """Normalize the frozen poll manifests without changing their bytes."""
    result = {key: row[key] for key in ("bytes", "kind")}
    result["kind"] = "file" if result["kind"] == "regular" else result["kind"]
    result["mode"] = int(row["mode"], 8) if isinstance(row["mode"], str) else row["mode"]
    if result["kind"] == "symlink":
        result["target"] = row["target"]
        result["sha256"] = hashlib.sha256(os.fsencode(row["target"])).hexdigest()
    else:
        result["sha256"] = row["sha256"]
    return result


def tree_paths(root):
    """Include directories and symlinks, but never follow a symlink."""
    yield root
    for directory, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        files.sort()
        for name in list(directories):
            path = Path(directory) / name
            yield path
            if path.is_symlink():
                directories.remove(name)
        for name in files:
            yield Path(directory) / name


def snapshot_tree(root):
    """Hash all bytes, including cached objects, and record immutable identities.

    atime is intentionally excluded: read-only hashing may update it. No claim
    is made about access times. mtime, ctime, inode and link count are retained.
    """
    result = {}
    for path in tree_paths(root):
        info = path.lstat()
        record = {"device": info.st_dev, "inode": info.st_ino,
                  "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
                  "nlink": info.st_nlink}
        if stat.S_ISDIR(info.st_mode):
            record.update(kind="directory", mode=stat.S_IMODE(info.st_mode))
        else:
            record.update(file_record(path))
        result[path.relative_to(root).as_posix()] = record
    return result


def content_record(record):
    return {key: value for key, value in record.items()
            if key not in ("device", "inode", "mtime_ns", "ctime_ns", "nlink")}


def verify_baseline(baseline, before, report):
    after = snapshot_tree(baseline)
    write_json(report, after)
    require(after == before, "frozen poll baseline bytes or identities changed; retain all evidence")


def inspect_independence(baseline_snapshot, candidate):
    previous = {(r["device"], r["inode"]) for r in baseline_snapshot.values()}
    count = links = 0
    for path in tree_paths(candidate):
        info = path.lstat()
        require((info.st_dev, info.st_ino) not in previous, f"shared baseline inode: {path}")
        if path.is_symlink():
            require(path.resolve().is_relative_to(candidate.resolve()), f"escaping candidate symlink: {path}")
            links += 1
        elif path.is_file():
            count += 1
        if path.name == "alternates" and path.parent.name == "info" and path.parent.parent.name == "objects":
            raise RuntimeError(f"Git object alternates forbidden: {path}")
    return {"independent_regular_files": count, "contained_symlinks": links,
            "shared_baseline_inodes": 0}


def env_file(path):
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        words = shlex.split(value, comments=True)
        require(len(words) == 1 and name not in values, f"unsupported env entry: {name}")
        values[name] = words[0]
    return values


def entries(text):
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def patch_records(repo, target):
    values = env_file(repo / "targets" / f"{target}.env")
    records = []
    for entry in entries(contained(repo, values["SERIES_FILE"]).read_text()):
        patch = repo / values["PATCHES_DIR"] / entry
        require(patch.resolve().is_relative_to(repo.resolve()), f"escaping patch: {entry}")
        records.append({"entry": entry, "sha256": sha(patch)})
    return records


def source_names(repo, target):
    source = repo / "src" / target
    names = set(git_names(source))
    values = env_file(repo / "targets" / f"{target}.env")
    for record in patch_records(repo, target):
        patch = repo / values["PATCHES_DIR"] / record["entry"]
        # Historical patch payloads need not be UTF-8. Only path headers are
        # decoded, so imported bytes and encodings remain unchanged.
        data = patch.read_bytes()
        for name in re.findall(rb"^\+\+\+ b/(.+)$", data, re.M):
            names.add(safe_name(name.decode()))
        # Removed tracked paths must not be silently omitted for another reason.
        for name in re.findall(rb"^--- a/(.+)\n\+\+\+ /dev/null$", data, re.M):
            names.discard(safe_name(name.decode()))
    names.add(".config")
    for name in names:
        require(not name.startswith(".git/") and not name.endswith((".o", ".a", ".ko")),
                f"generated file selected as source: {name}")
        contained(source, name).lstat()
    require("COPYING" in names and any(name.startswith("LICENSES/") for name in names), "source notices missing")
    return sorted(names)


def source_inventory(repo, target):
    root = repo / "src" / target
    return {name: file_record(contained(root, name)) for name in source_names(repo, target)}


def input_inventory(repo):
    tracked = git_names(repo)
    overlay = git_names(repo, "--others", "--exclude-standard")
    files = [{"path": name, **file_record(contained(repo, name))} for name in sorted(set(tracked + overlay))]
    return {"schema": 1, "project_base_commit": PROJECT_BASE,
            "project_identity": PROJECT_BASE + "+" + PROJECT_OVERLAY,
            "tracked_count": len(tracked), "untracked_overlay_files": overlay,
            "uncommitted_overlay_files": overlay, "input_count": len(files),
            "inputs_file_count": len(files), "frozen_baseline_input_count": BASE_INPUT_COUNT, "files": files}


def paths(workspace):
    root = workspace / "lab/uvd-2026-10-19"
    return root / "linux-psx-uvd-poll-dev1", root / "linux-psx-uvd-token-dev2", root / "token-transport-v2/build"


def native_host():
    require(platform.system() == "Linux" and platform.machine() == "x86_64", "native Linux x86-64 required")
    require(not os.environ.get("CROSS_COMPILE"), "CROSS_COMPILE must be unset")


def baseline_contract(workspace, before):
    baseline, _, _ = paths(workspace)
    reports = workspace / "lab/uvd-2026-10-19/ready-policy/build"
    for name, expected in (("prebuild-inputs.json", BASE_INPUT_MANIFEST_SHA),
                           ("source-inputs.json", BASE_SOURCE_MANIFEST_SHA),
                           ("build-receipt.json", BASE_BUILD_RECEIPT_SHA)):
        require(sha(reports / name) == expected, f"frozen baseline receipt changed: {name}")
    inputs = json.loads((reports / "prebuild-inputs.json").read_text())
    require(inputs["input_count"] == BASE_INPUT_COUNT, "baseline input count changed")
    actual_names = sorted(set(git_names(baseline) + git_names(baseline, "--others", "--exclude-standard")))
    require(actual_names == sorted(row["path"] for row in inputs["files"]), "baseline input paths changed")
    for row in inputs["files"]:
        require(content_record(before[row["path"]]) == normalize_record(row), f"baseline input changed: {row['path']}")
    source_manifest = json.loads((reports / "source-inputs.json").read_text())
    for row in source_manifest["files"]:
        require(content_record(before[f"src/{OLD_TARGET}/{row['path']}"]) == normalize_record(row), f"baseline source changed: {row['path']}")
    require(source_names(baseline, OLD_TARGET) == sorted(row["path"] for row in source_manifest["files"]), "baseline source selection changed")
    built = json.loads((reports / "build-receipt.json").read_text())
    require(built["status"] == "PASS_NATIVE_BUILD_NO_HARDWARE_ACCEPTANCE" and built["baseline_preserved"], "baseline build did not pass")
    for name, expected in built["artifact_sha256"].items():
        require(before[f"output/{OLD_TARGET}/{safe_name(name)}"]["sha256"] == expected, f"baseline artifact changed: {name}")
    require(capture(["/usr/bin/git", "-C", baseline, "rev-parse", "HEAD"]).strip() == PROJECT_BASE, "unexpected project base")
    command(["/usr/bin/git", "-C", baseline, "diff", "HEAD", "--exit-code", "--quiet"])
    source = baseline / "src" / OLD_TARGET
    require(capture(["/usr/bin/git", "-C", source, "rev-parse", "HEAD"]).strip() == UPSTREAM, "unexpected source base")
    require(len(patch_records(baseline, OLD_TARGET)) == BASE_PATCH_COUNT, "baseline patch count changed")
    for name, expected in BASE_SOURCE_SHA.items():
        require(before[f"src/{OLD_TARGET}/{name}"]["sha256"] == expected, f"baseline helper changed: {name}")
    return inputs


def load_review(path, input_dir):
    review = json.loads(path.read_text())
    require(review.get("schema") == 1, "review manifest schema must be 1")
    require(set(review.get("source_sha256", {})) == CHANGED_SOURCE, "review must identify exactly four changed source files")
    for value in [review.get("patch_sha256", ""), *review["source_sha256"].values()]:
        require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "invalid review digest")
    patch = input_dir / PATCH_NAME
    require(sha(patch) == review["patch_sha256"], "reviewed patch digest mismatch")
    require(set(re.findall(r"^\+\+\+ b/(.+)$", patch.read_text(), re.M)) == CHANGED_SOURCE,
            "token patch must touch exactly the four reviewed paths")
    return review


def prepare_base(workspace):
    baseline, candidate, logs = paths(workspace)
    require(not candidate.exists() and not candidate.is_symlink(), f"existing candidate retained: {candidate}")
    require(not logs.exists(), f"existing preparation reports retained: {logs}")
    before = snapshot_tree(baseline)
    inputs = baseline_contract(workspace, before)
    write_json(logs / "baseline-before.json", before)
    write_json(logs / "baseline-inputs.json", inputs)
    try:
        command(["/usr/bin/git", "clone", "--no-hardlinks", "--no-checkout", baseline, candidate])
        command(["/usr/bin/git", "-C", candidate, "checkout", "--detach", PROJECT_BASE])
        for name in inputs["untracked_overlay_files"]:
            destination = contained(candidate, name)
            require(not destination.exists(), f"overlay would overwrite tracked file: {name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(baseline / name, destination, follow_symlinks=False)
        source = candidate / "src" / TARGET
        source.parent.mkdir()
        command(["/usr/bin/cp", "-a", "--reflink=auto", baseline / "src" / OLD_TARGET, source])
        independence = inspect_independence(before, candidate)
        copied = snapshot_tree(source)
        prefix = f"src/{OLD_TARGET}/"
        expected = {name[len(prefix):]: content_record(row) for name, row in before.items() if name.startswith(prefix)}
        expected["."] = content_record(before[f"src/{OLD_TARGET}"])
        require({name: content_record(row) for name, row in copied.items()} == expected, "copied source/cache differs")
        for row in inputs["files"]:
            require(file_record(candidate / row["path"]) == normalize_record(row), f"copied input differs: {row['path']}")
        write_json(logs / "base-prepared.json", {"status": "BASE_COPIED_NOT_PATCHED_OR_BUILT", "independence": independence})
    finally:
        verify_baseline(baseline, before, logs / "baseline-after-copy.json")


def host_check(candidate, source, report):
    frozen = candidate / PRIVATE
    command(["/usr/bin/python3", frozen / "check.py", "--baseline", frozen / "baseline",
             "--source", source, "--headers", source / "drivers/gpu/drm/amd/include/asic_reg",
             "--planner", frozen / "ps4_uvd_ring_plan.h", "--planner-fixture", frozen / "ring_plan_test.c",
             "--report", report])
    require((report / "receipt.json").is_file(), "host checker did not write its receipt")


def prepare_overlay(workspace, input_dir, review_path, planner, planner_fixture):
    baseline, candidate, logs = paths(workspace)
    require((logs / "base-prepared.json").is_file(), "prepare base first")
    require(not (logs / "prepared.json").exists(), "existing prepared candidate retained")
    review = load_review(review_path, input_dir)
    before = json.loads((logs / "baseline-before.json").read_text())
    verify_baseline(baseline, before, logs / "baseline-before-overlay.json")
    inspect_independence(before, candidate)
    frozen = candidate / PRIVATE
    frozen.mkdir()
    try:
        for name in INPUTS:
            require((input_dir / name).is_file() and not (input_dir / name).is_symlink(), f"missing regular input: {name}")
            shutil.copy2(input_dir / name, frozen / name)
        for original, name in ((review_path, "review.json"), (planner, "ps4_uvd_ring_plan.h"), (planner_fixture, "ring_plan_test.c")):
            require(original.is_file() and not original.is_symlink(), f"missing regular input: {original}")
            shutil.copy2(original, frozen / name)
        for name, expected in BASE_SOURCE_SHA.items():
            destination = frozen / "baseline" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(baseline / "src" / OLD_TARGET / name, destination)
            require(sha(destination) == expected, f"baseline fixture copy changed: {name}")
        source = candidate / "src" / TARGET
        for name, expected in BASE_SOURCE_SHA.items():
            require(sha(source / name) == expected, f"candidate not frozen poll-dev1: {name}")
        patch_dir = candidate / "platforms/ps4/patches" / TARGET
        patch_dir.mkdir()
        shutil.copy2(frozen / PATCH_NAME, patch_dir / PATCH_NAME)
        old_values = env_file(candidate / "targets" / f"{OLD_TARGET}.env")
        series = patch_dir / "series"
        series.write_text((candidate / old_values["SERIES_FILE"]).read_text() +
                          f"\n# Default-off one-shot context-token transport.\n../{TARGET}/{PATCH_NAME}\n")
        old_config = candidate / old_values["CONFIG_FILE"]
        config = candidate / old_values["CONFIG_FILE"].replace("uvd-poll-dev1", "uvd-token-dev2")
        old_line = 'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-poll-dev1"'
        require(old_config.read_text().count(old_line) == 1, "unexpected baseline LOCALVERSION")
        config.write_text(old_config.read_text().replace(old_line, 'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-token-dev2"'))
        target = candidate / "targets" / f"{TARGET}.env"
        target.write_text((candidate / "targets" / f"{OLD_TARGET}.env").read_text().replace("uvd-poll-dev1", "uvd-token-dev2"))
        patches = patch_records(candidate, TARGET)
        require(len(patches) == PATCH_COUNT and patches[:-1] == patch_records(baseline, OLD_TARGET), "patch order changed beyond append")
        command(["/usr/bin/git", "apply", "--check", patch_dir / PATCH_NAME], cwd=source)
        command(["/usr/bin/git", "apply", patch_dir / PATCH_NAME], cwd=source)
        for name, expected in review["source_sha256"].items():
            require(sha(source / name) == expected, f"reviewed source digest mismatch: {name}")
        require(sha(source / PLAN_H) == sha(frozen / "ps4_uvd_ring_plan.h"), "planner header differs from host input")
        source_files = source_inventory(candidate, TARGET)
        previous = source_inventory(baseline, OLD_TARGET)
        require(set(source_files) == set(previous) | {PLAN_H}, "unexpected source path change")
        for name, record in previous.items():
            if name not in CHANGED_SOURCE:
                require(source_files[name] == record, f"unexpected source delta: {name}")
        host_check(candidate, source, logs / "host-native")
        write_json(logs / "prepared-source-inputs.json", source_files)
        frozen_names = [p.relative_to(frozen).as_posix() for p in tree_paths(frozen) if p.is_file()]
        receipt = {
            "schema": 1, "status": "PREPARED_HOST_CHECKED_NOT_BUILT", "project_base_commit": PROJECT_BASE,
            "project_overlay": PROJECT_OVERLAY, "project_identity": PROJECT_BASE + "+" + PROJECT_OVERLAY,
            "project_clean": False, "upstream_commit": UPSTREAM, "target": TARGET, "release": RELEASE,
            "patch_sha256": review["patch_sha256"], "token_patch_sha256": review["patch_sha256"],
            "policy_patch_sha256": POLICY_PATCH_SHA, "ready_source_sha256": sha(source / READY_C),
            "reviewed_source_sha256": review["source_sha256"],
            "input_sha256": {name: sha(frozen / name) for name in sorted(frozen_names)},
            "patches": patches, "active_patch_count": PATCH_COUNT, "series_sha256": sha(series),
            "config_input_sha256": sha(config), "target_sha256": sha(target),
            "baseline_snapshot_sha256": sha(logs / "baseline-before.json"),
            "baseline_inputs_sha256": sha(logs / "baseline-inputs.json"),
            "source_inventory_sha256": sha(logs / "prepared-source-inputs.json"),
            "host_receipt_sha256": sha(logs / "host-native/receipt.json"),
            "frozen_baseline_input_count": BASE_INPUT_COUNT, "all700_poll_inputs_preserved": True,
            "firmware_accessed": False, "hardware_tested": False,
        }
        provenance = candidate / "provenance" / f"{TARGET}.json"
        receipt["inputs_file_count"] = input_inventory(candidate)["input_count"] + 1
        receipt["uncommitted_overlay_files"] = sorted(input_inventory(candidate)["untracked_overlay_files"] + [provenance.relative_to(candidate).as_posix()])
        write_json(provenance, receipt)
        write_json(logs / "prepared.json", receipt)
        inventory = input_inventory(candidate)
        require(inventory["input_count"] == receipt["inputs_file_count"], "final input count mismatch")
        for row in json.loads((logs / "baseline-inputs.json").read_text())["files"]:
            require(file_record(candidate / row["path"]) == normalize_record(row), f"frozen poll input changed: {row['path']}")
        write_json(logs / "prebuild-inputs.json", inventory)
        print(f"Prepared only: {TARGET}; {inventory['input_count']} exact inputs; {PATCH_COUNT} patches", flush=True)
    finally:
        verify_baseline(baseline, before, logs / "baseline-after-prepare.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--stage", choices=("base", "overlay", "all"), default="all")
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--review-manifest", type=Path, help="schema 1; patch_sha256 and four-path source_sha256 map")
    parser.add_argument("--planner", type=Path)
    parser.add_argument("--planner-fixture", type=Path)
    arguments = parser.parse_args()
    native_host()
    if arguments.stage in ("overlay", "all"):
        require(all((arguments.input_dir, arguments.review_manifest, arguments.planner, arguments.planner_fixture)), "overlay requires reviewed inputs and planner fixture")
        load_review(arguments.review_manifest, arguments.input_dir)
    workspace = arguments.workspace.resolve()
    if arguments.stage in ("base", "all"):
        prepare_base(workspace)
    if arguments.stage in ("overlay", "all"):
        prepare_overlay(workspace, arguments.input_dir.resolve(), arguments.review_manifest.resolve(),
                        arguments.planner.resolve(), arguments.planner_fixture.resolve())


if __name__ == "__main__":
    main()
