#!/usr/bin/env python3
"""Compose two pinned UVD source deltas; do not compile or operate hardware."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys


AMD = Path("drivers/gpu/drm/amd/amdgpu")
LAB = Path("lab/uvd-2026-10-20")
PREP = LAB / "live-prepare/source/attempt-06"
FAULT = LAB / "vmid4-fault/source/attempt-02"
READY = LAB / "ready-core/source/attempt-01"
RECEIPTS = {
    PREP: "2b1a4f8b7aceec62f8c42b3b5f39430bfaf4b10b97e4a8929642178390c835fa",
    FAULT: "97c91ac25099eab7c47eb37b17b9c80ad4c637ccd1a316cadb81ecd325065346",
    READY: "a7bc616f35ca4336197ee78e0f14d5da2686e1be97a435e2af0d6e05abbaf698",
}
PATCHES = (
    (FAULT, "0012-drm-amdgpu-ps4-record-armed-gmc-fault-history.patch",
     "d2360ccb4e651099df8b1c4964dc054706270d71fcb36775ff8e0fa6ca235776",
     {"amdgpu_ps4_uvd_irq.c", "amdgpu_ps4_uvd_irq.h", "gmc_v7_0.c"}),
    (READY, "0013-drm-amdgpu-ps4-returning-ready-core.patch",
     "e0443efc3f778bbc957aa60ffd2e404f1c3485b27b6acaf4b1504294dbf6b3e7",
     {"amdgpu.h", "amdgpu_ps4_uvd_ready.c", "amdgpu_ps4_uvd_ready.h"}),
)
PREP_FILES = {
    "amdgpu.h", "amdgpu_debugfs.c", "amdgpu_device.c", "amdgpu_drv.c",
    "amdgpu_fence.c", "amdgpu_ids.c", "amdgpu_kms.c", "amdgpu_object.c",
    "amdgpu_ps4_live_prepare.h", "amdgpu_ps4_map_canary.c",
    "amdgpu_ps4_map_canary.h", "amdgpu_ps4_uvd_ready.c",
    "amdgpu_ps4_uvd_ready.h", "amdgpu_reset.c", "amdgpu_vm.c", "gmc_v7_0.c",
}
LIMITS = [
    "Source composition only; no combined compilation, kernel link or hardware acceptance.",
    "Live entry, control and lifecycle integration remain unresolved.",
    "No installed or runnable driver is produced.",
    "Embedded amdgpu_device layout changes require a full matching kernel/modules rebuild.",
    "The unfinished live-control component is excluded.",
    "No firmware, native execution, SSH, PS4, USB, UART or privilege operation.",
    "Separate component test/object results do not qualify this combined source.",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def files_under(root):
    require(root.is_dir() and not root.is_symlink(), f"not a regular directory: {root}")
    result = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), f"symlink rejected: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
        else:
            require(path.is_dir(), f"non-regular entry: {path}")
    return result


def compose(workspace, output):
    inputs = {}

    def read(path, expected=None):
        require(path.is_file() and not path.is_symlink(), f"not a regular file: {path}")
        data = path.read_bytes()
        digest = sha(data)
        require(expected is None or digest == expected, f"input hash differs: {path}")
        inputs[str(path)] = digest
        return data

    def tree(path, expected):
        actual = files_under(path)
        relative = {(AMD / name).as_posix(): digest for name, digest in expected.items()}
        require(set(actual) == set(relative), f"source inventory differs: {path}")
        for name, digest in relative.items():
            require(sha(actual[name]) == digest, f"source hash differs: {path / name}")
            inputs[str(path / name)] = digest
        return {Path(name).name: data for name, data in actual.items()}

    receipts = {}
    receipt_bytes = {}
    for rel, digest in RECEIPTS.items():
        receipt_bytes[rel] = read(workspace / rel / "SOURCE-RECEIPT.json", digest)
        receipts[rel] = json.loads(receipt_bytes[rel])
    prep, fault, ready = (receipts[rel] for rel in (PREP, FAULT, READY))
    require(set(prep["candidate_files"]) == PREP_FILES, "preparation inventory differs")
    require(ready["baseline_source_receipt_sha256"] == RECEIPTS[PREP],
            "readiness baseline receipt differs")
    require(ready["baseline_files"] == prep["candidate_files"],
            "readiness/preparation baseline pins differ")
    base = tree(workspace / PREP / "candidate", prep["candidate_files"])
    ready_base = tree(workspace / READY / "baseline", ready["baseline_files"])
    require(base == ready_base, "readiness/preparation baseline bytes differ")
    fault_base = tree(workspace / FAULT / "baseline", fault["baseline_files"])
    require(set(fault_base) == PATCHES[0][3], "fault baseline inventory differs")
    overlap = sorted(set(base) & set(fault_base))
    require(overlap == ["gmc_v7_0.c"], "unexpected baseline overlap")
    for name in overlap:
        require(base[name] == fault_base[name], f"overlapping baseline differs: {name}")
    base.update(fault_base)
    require(len(base) == 18, "combined baseline must contain exactly 18 source files")

    candidates = {}
    patches = {}
    changes = {}
    for rel, filename, digest, expected_changes in PATCHES:
        receipt = receipts[rel]
        require(set(receipt["candidate_files"]) == set(receipt["baseline_files"]),
                f"component source inventory changed: {rel}")
        actual_changes = {
            name for name, value in receipt["candidate_files"].items()
            if value != receipt["baseline_files"][name]
        }
        require(actual_changes == expected_changes, f"component delta differs: {rel}")
        candidates[rel] = tree(workspace / rel / "candidate", receipt["candidate_files"])
        patch = read(workspace / rel / filename, digest)
        require(receipt["patch"]["sha256"] == digest, f"patch receipt differs: {rel}")
        require(receipt["patch"].get("file", receipt["patch"].get("path")) == filename,
                f"patch name differs: {rel}")
        pairs = re.findall(rb"^diff --git a/(\S+) b/(\S+)$", patch, re.MULTILINE)
        wanted = {(AMD / name).as_posix().encode() for name in expected_changes}
        require(len(pairs) == len(wanted) and {a for a, _ in pairs} == wanted
                and all(a == b for a, b in pairs), f"patch path inventory differs: {rel}")
        patches[rel] = patch
        changes[rel] = expected_changes
    require(changes[FAULT].isdisjoint(changes[READY]), "patch deltas overlap")

    tool = Path(__file__).resolve()
    tool_bytes = read(tool)
    readme_bytes = read(tool.with_name("README.md"))
    git = Path("/usr/bin/git")
    git_bytes = read(git)
    # Restrict output to a new immediate child of this experiment's evidence root.
    allowed_parent = workspace / LAB / "driver-integration"
    require(output.parent.resolve() == allowed_parent.resolve(), "unexpected output parent")
    require(not output.exists() and not output.is_symlink(), "output already exists")
    allowed_parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    commands = []
    try:
        candidate = output / "candidate"
        for name, data in sorted(base.items()):
            write(candidate / AMD / name, data)
        for rel, filename, _, _ in PATCHES:
            write(output / "patches" / filename, patches[rel])
        write(output / "patches/series",
              ("".join(filename + "\n" for _, filename, _, _ in PATCHES)).encode())
        for label, rel in (("preparation", PREP), ("fault-history", FAULT), ("readiness", READY)):
            write(output / "inputs" / (label + "-SOURCE-RECEIPT.json"), receipt_bytes[rel])
        write(output / "tool/compose.py", tool_bytes)
        write(output / "tool/README.md", readme_bytes)
        env = {
            "PATH": "/usr/bin:/bin", "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CEILING_DIRECTORIES": str(output),
        }
        write(output / "INVOCATION.json", json_bytes({
            "argv": sys.orig_argv, "python_executable": sys.executable,
            "cwd": str(Path.cwd()),
            "workspace": str(workspace), "output": str(output),
            "python": sys.version, "host": platform.platform(),
            "git": str(git), "git_sha256": sha(git_bytes),
            "subprocess_environment": env, "limits": LIMITS,
        }))
        expected = dict(base)
        checkpoints = []
        for index, (rel, filename, digest, _) in enumerate(PATCHES, 1):
            for phase in ("check", "apply"):
                argv = [str(git), "apply", "--whitespace=nowarn"]
                if phase == "check":
                    argv.append("--check")
                argv.append(str(output / "patches" / filename))
                prefix = f"commands/{index:02d}-{phase}"
                command = {
                    "argv": argv, "cwd": str(candidate), "returncode": None,
                    "stdout": prefix + ".stdout", "stderr": prefix + ".stderr",
                }
                commands.append(command)
                try:
                    run = subprocess.run(argv, cwd=candidate, env=env, capture_output=True,
                                         timeout=30, check=False)
                except subprocess.TimeoutExpired as error:
                    command["timeout_seconds"] = 30
                    write(output / (prefix + ".stdout"), error.stdout or b"")
                    write(output / (prefix + ".stderr"), error.stderr or b"")
                    raise
                write(output / (prefix + ".stdout"), run.stdout)
                write(output / (prefix + ".stderr"), run.stderr)
                command["returncode"] = run.returncode
                require(run.returncode == 0, f"patch {index} {phase} failed")
            for name in changes[rel]:
                expected[name] = candidates[rel][name]
            actual = files_under(candidate)
            expected_paths = {(AMD / name).as_posix(): data for name, data in expected.items()}
            require(actual == expected_paths, f"exact replay differs after patch {index}")
            checkpoints.append({
                "position": index, "patch": filename, "sha256": digest,
                "source_receipt_sha256": RECEIPTS[rel],
                "changed_files": sorted(changes[rel]),
                "candidate_files": {name: sha(data) for name, data in sorted(actual.items())},
            })
        for name, digest in inputs.items():
            path = Path(name)
            require(path.is_file() and not path.is_symlink() and sha(path.read_bytes()) == digest,
                    f"input changed during composition: {name}")
        write(output / "COMMANDS.json", json_bytes(commands))
        all_outputs = files_under(output)
        manifest = {
            "schema": 1, "status": "PASS_EXACT_SOURCE_COMPOSITION_ONLY",
            "workspace": str(workspace), "output": str(output),
            "input_files": inputs,
            "source_receipts": {str(rel): digest for rel, digest in RECEIPTS.items()},
            "baseline_files": {(AMD / name).as_posix(): sha(data)
                               for name, data in sorted(base.items())},
            "baseline_overlap_checks": {
                "preparation_vs_readiness": sorted(PREP_FILES),
                "fault_vs_preparation": overlap,
                "all_equal_by_bytes": True,
            },
            "applied_order": checkpoints,
            "candidate_files": checkpoints[-1]["candidate_files"],
            "candidate_file_count": len(expected),
            "changed_file_count": len(changes[FAULT] | changes[READY]),
            "output_files_excluding_manifest": {
                name: {"sha256": sha(data), "bytes": len(data)}
                for name, data in sorted(all_outputs.items())
            },
            "commands": commands,
            "notices_and_patch_headers": "Original bytes retained; no merged/re-authored patch.",
            "inputs_unchanged_after": True,
            "combined_compiled": False, "native_execution": False,
            "installed_or_runnable_driver": False, "hardware_acceptance": False,
            "full_matching_kernel_modules_rebuild_required": True,
            "unresolved": ["live entry", "control", "lifecycle integration"],
            "limits": LIMITS,
        }
        write(output / "MANIFEST.json", json_bytes(manifest))
        return manifest
    except BaseException as error:
        write(output / "FAILURE.json", json_bytes({
            "status": "FAILED_SOURCE_COMPOSITION", "error": str(error),
            "error_type": type(error).__name__, "commands": commands,
            "limits": LIMITS,
        }))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    output = args.out.absolute()
    manifest = compose(workspace, output)
    print(json.dumps({
        "status": manifest["status"], "candidate_files": manifest["candidate_file_count"],
        "changed_files": manifest["changed_file_count"],
        "manifest": str(output / "MANIFEST.json"),
        "manifest_sha256": sha((output / "MANIFEST.json").read_bytes()),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
