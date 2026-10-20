#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Private IB-derived preparation-only native build; exclusive phases, no devices."""

import argparse
import datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
OLD_TARGET = "7.2-ps4-baikal-uvd-ib-data-dev1"
TARGET = "7.2-ps4-baikal-uvd-live-prepare-dev1"
RELEASE = "7.2.11-psx-ps4-baikal-uvd-live-prepare-dev1"
OLD_RELEASE = "7.2.11-psx-ps4-baikal-uvd-ib-data-dev1"
LOCALVERSION = '"-psx-ps4-baikal-uvd-live-prepare-dev1"'
BASE_REL = "lab/uvd-2026-10-20/ib-data/build-preparation/native-01/project"
REPORT_REL = "lab/uvd-2026-10-20/ib-data/build-preparation/native-01/receipts"
DEFAULT_RUN = "lab/uvd-2026-10-20/live-prepare/build-preparation/native-01"
PATCH = "0011-drm-amdgpu-ps4-live-prepare-only.patch"
PRIVATE = "private-build/live-prepare"
READY = "drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.c"
PROJECT = "35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2"
UPSTREAM = "a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea"
IRQ = "drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_irq.c"
IRQ_SHA = "ccf618c976b9a7b80045ffaeb81b7ede0707fa98bef8aca4bc7a7653a3de5480"
BASE_INPUTS_ARCHIVE = "966915b9489c1e178aa843e2d031e1305ff573989c2531695e84eb6995fb3fd7"
BASE_RECEIPTS = {'build.json': '760dcbb3031ea5754f520773d9d2c5d68e4cf7287a6a35e193a3ca7ec2c05ef5',
 'built-source.json': '1da1dca4556049d8740630bee7d593d266e0072ba1fd7ed3e3421636d8f325bb',
 'identity.json': 'bb66ab6737a0e6a46c66e8c719b114438a72b5bee918cfe6f297fef2c96f14b4',
 'module-tree.json': '36721e4299eabb773af24dd2a5855600dd14e8171d9f3c7a3f802a67e7d5c23e',
 'modules.json': '0c0a959a0177f1f6d39f0b532fbfbc9527290d7444991ce3afb9a9a5391d7718',
 'package.json': '40c12ecd46c14e5122ed2b71ffe4e840c7cfcd74a2ba225c65afc75a39d6ed8d',
 'prepare-base.json': '4f789f5e740dc63bb4a6f6c18233d1553e0b81b41eb11e53ab4ab33bf2cc22f3',
 'prepare.json': '8fd665ba03993de9844fee65bebd922f41f576f1575aab6159e66bc7dd320257',
 'project-inputs.json': 'e53020d0d73bcf7be9536dae52a276f874c5257a92246dc974fbd44dbd72cafd'}

HELPERS = {
    "prepare_native.py": "a64057a9826fa5b2c87b6dc9e53c613a6ea82c7acfe383d3a421c06f4cb58fca",
    "build_native.py": "5b67b651b77ccfcee4613fdf12febe47193ce52a0abae96ce38e7a39e3e20d43",
    "package_native.py": "1f00d27a604a159a16f9c443c23e03da441e0c34ad8c5a0c9a167a2eb71fe686",
}
PREFIX = 'drivers/gpu/drm/amd/amdgpu/'
LIVE_H = PREFIX + "amdgpu_ps4_live_prepare.h"
BASE_SOURCE = {'drivers/gpu/drm/amd/amdgpu/amdgpu.h': 'e311b1c75020e39f86027030769db77a8ce6e3e8f4ee67f6ae7dfc52f08c7982',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_debugfs.c': '637799042fdbffb71525dd45f13950a212ccabb94af626d988e0e959ae54f0c6',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_device.c': '598fb5f86989c28207dd2da9a0223240e1ba3baa1678876643c40b64b0fd75ca',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_drv.c': 'de81866ce1554dd5b0d0bbe2ce0b26fb8db704946bc31b13832696de19b0886a',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_fence.c': '6827a25f5fac3a8bde5a5c96c9b40fc09480eda784dc353a6908c73e0a398727',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_ids.c': 'fc2a6ddf3c78c8054560d3d75cf050980c541d831fb5319dba20621ab39f7ce8',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_kms.c': 'ac674e70a4203543b3f7ee8727ede7899a7215acf464172ae0f938c78d9be61c',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_object.c': 'ff98c136a7c1dfa54d9847cdcdb2f6046291c307f47f0451fe9491df17fdf7b2',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_map_canary.c': 'e7283b838eb1f96cbc0bd865716320dd87538a768bc38b215e98b7f6a29abab8',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_map_canary.h': 'd60274ba58c8a5075e22a20123e0b4bc7f8bb0eb08a11eb144fe20399c0df0de',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.c': 'cf2482e51798a21a2c3b6cbe0ac7ecb270ae0d20cd109c3d29583cbcb4f2201e',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.h': '59a8941710bd4d18dbb1ad877d4f83baf17b6371a287b9ceb5e2ef5f2cf1d7de',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_reset.c': '9b6892c6b052d75719b2215595126e52ce5f4515346d8986270e4d9c35a6105d',
 'drivers/gpu/drm/amd/amdgpu/amdgpu_vm.c': 'ca1bdf662cfab2735cfd2ad8c2d362bee12bd5b0ced27b6061c94af465a216b7',
 'drivers/gpu/drm/amd/amdgpu/gmc_v7_0.c': '77c124010fc52b85e937078d29d2ca765b7b04325a7208e362b9bac435890b23'}
ADDED = frozenset((LIVE_H,))
MODIFIED = frozenset(('drivers/gpu/drm/amd/amdgpu/amdgpu.h', 'drivers/gpu/drm/amd/amdgpu/amdgpu_debugfs.c', 'drivers/gpu/drm/amd/amdgpu/amdgpu_device.c', 'drivers/gpu/drm/amd/amdgpu/amdgpu_drv.c', 'drivers/gpu/drm/amd/amdgpu/amdgpu_ids.c', 'drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_map_canary.c', 'drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_map_canary.h', 'drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.c', 'drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.h', 'drivers/gpu/drm/amd/amdgpu/gmc_v7_0.c'))
CHANGED = MODIFIED | ADDED
SELECTED = frozenset(BASE_SOURCE) | ADDED
MINIMUM_MUTATIONS = {'attempt-not-recorded': 'repeat_begin',
 'early-fence-check-removed': 'publish_reject_fence_init',
 'generation-check-removed': 'publish_reject_generation',
 'kernel-attempt-latch-removed': 'rebind_no_repeat',
 'load-failure-unloaded-twice': 'probe_unwind_order',
 'open-reference-missing': 'open_failure_ref',
 'outer-fence-double-init': 'fence_once',
 'owner-not-detached': 'owner_detached',
 'post-gart-invalidation-removed': 'invalidate_prepared',
 'registration-not-recorded': 'probe_unplug_before_unload',
 'status-hardware-side-effect': 'cached_only',
 'successful-load-not-recorded': 'probe_unwind_order',
 'uvd-owner-accepted': 'publish_reject_ready_mode'}
INPUT_FILES = {
    "patch": PATCH, "checker": "check.py", "fixture": "fixture.c",
    "generator": "build_candidate.py", "owner_header": "live_prepare.h",
    "owner_helpers": "live_prepare.c.inc", "debugfs": "debugfs.c.inc",
    "baseline_pins": "baseline-pins.json", "design": "DESIGN.md",
    "native_tool": "native.py", "native_tests": "native_test.py",
    "native_contract": "native_README.md",
}

ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TZ": "UTC",
       "GIT_OPTIONAL_LOCKS": "0", "PYTHONDONTWRITEBYTECODE": "1"}
if "HOME" in os.environ:
    ENV["HOME"] = os.environ["HOME"]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_helpers():
    directory = HERE.parent / "ps4-uvd-data-control"
    for name, digest in HELPERS.items():
        require(sha(directory / name) == digest, "frozen utility changed: " + name)
    sys.path.insert(0, str(directory))
    prep = importlib.import_module("prepare_native")
    build = importlib.import_module("build_native")
    package = importlib.import_module("package_native")
    for module in (prep, build, package):
        require(Path(module.__file__).resolve().parent == directory, "wrong utility import")
    prep.ENV = dict(ENV)
    return prep, build, package


def relative(root, name):
    require(isinstance(name, str) and name and "\n" not in name and "\0" not in name,
            "invalid relative path")
    path = Path(name)
    require(not path.is_absolute() and ".." not in path.parts and path.as_posix() == name,
            "unsafe relative path: " + name)
    target = root / path
    require(target.resolve().is_relative_to(root.resolve()), "escaping path: " + name)
    # Reject symlink ancestors too: an equivalent spelling must not change later.
    require(not any(p.is_symlink() for p in (target, *target.parents) if p != root.parent),
            "symlink path: " + name)
    return target


def pin_file(workspace, row):
    require(isinstance(row, dict) and set(row) == {"path", "sha256"}, "pin requires path and sha256")
    require(isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "invalid digest")
    path = relative(workspace, row["path"])
    require(path.is_file() and not path.is_symlink(), "missing regular pinned file: " + str(path))
    require(sha(path) == row["sha256"], "pin mismatch: " + str(path))
    return path


def json_read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key: " + key)
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def reviewed_sources(review):
    require(set(review.get("source_pins", {})) == SELECTED,
            "review must pin all sixteen selected candidate files")
    return {name: row["sha256"] for name, row in review["source_pins"].items()}


def patch_contract(text):
    sections = re.split(r"^diff --git a/(\S+) b/(\S+)\n", text, flags=re.M)
    require(len(sections) == 1 + 3 * len(CHANGED), "patch must have exactly eleven source diffs")
    seen = set()
    for index in range(1, len(sections), 3):
        old, new, body = sections[index:index + 3]
        require(old == new and new in CHANGED and new not in seen, "unexpected or duplicate patch path")
        seen.add(new)
        expected_old = "/dev/null" if new in ADDED else "a/" + new
        require(re.findall(r"^--- (.+)$", body, re.M) == [expected_old] and
                re.findall(r"^\+\+\+ (.+)$", body, re.M) == ["b/" + new], "patch source/addition header mismatch")
        require(not re.search(r"^(?:deleted file|old mode|new mode|rename |copy |GIT binary)", body, re.M),
                "patch may not remove, rename or change file type/mode")
        modes = re.findall(r"^new file mode (.+)$", body, re.M)
        require(not modes or (new in ADDED and modes == ["100644"]), "invalid new header mode")
    require(seen == CHANGED, "patch scope incomplete")


def host_receipt(review, receipt):
    require(receipt.get("status") == "PASS_EXTRACTED_PREPARATION_SOFTWARE_NO_DEVICES",
            "host receipt did not pass the preparation fixture")
    for field, pin in (("patch_sha256", "patch"), ("checker_sha256", "checker"),
                       ("fixture_template_sha256", "fixture"),
                       ("source_receipt_sha256", "source_receipt")):
        require(receipt.get(field) == review["pins"][pin]["sha256"],
                "host receipt input mismatch: " + field)
    require(receipt.get("source_sha256") ==
            {Path(k).name: v for k, v in reviewed_sources(review).items()},
            "host receipt candidate scope or identity mismatch")
    require(receipt.get("kernel_built") is False and receipt.get("hardware_actions") is False and
            receipt.get("hardware_acceptance") is False, "host receipt scope changed")
    toolchain = receipt.get("toolchain", {})
    require(toolchain.get("cc") and toolchain.get("version"), "compiler identity missing")
    require(toolchain.get("source_receipt_sha256") == review["pins"]["source_receipt"]["sha256"],
            "toolchain source receipt differs")
    host = toolchain.get("host", {})
    require(host.get("system") in ("Darwin", "Linux"), "unexpected receipt host")
    require(host.get("system") != "Linux" or host.get("machine") == "x86_64",
            "Linux fixture host must be x86-64")
    runs = receipt.get("runs", {})
    require(set(runs) == {"optimized", "sanitized"}, "optimized and sanitizer results required")
    for row in runs.values():
        require(type(row.get("cases")) is int and row["cases"] >= 77, "host case coverage incomplete")
        require(isinstance(row.get("stdout_sha256"), str) and
                re.fullmatch(r"[0-9a-f]{64}", row["stdout_sha256"]), "host run digest absent")
    require(runs["optimized"] == runs["sanitized"], "host build modes disagree")
    mutants = receipt.get("sensitivity", [])
    require(len(mutants) == len(MINIMUM_MUTATIONS) and
            {row.get("name") for row in mutants} == set(MINIMUM_MUTATIONS),
            "mutation set incomplete or unexpected")
    for row in mutants:
        require(row.get("returncode") == 86 and row.get("witness") == MINIMUM_MUTATIONS[row["name"]],
                "mutation failed for a different reason")
        require(isinstance(row.get("source_sha256"), str) and
                re.fullmatch(r"[0-9a-f]{64}", row["source_sha256"]), "mutant identity missing")
    generated = receipt.get("generated_fixture_sha256")
    require(isinstance(generated, str) and re.fullmatch(r"[0-9a-f]{64}", generated),
            "generated fixture digest missing")
    require(generated == review["generated_fixture_sha256"], "reviewed generated fixture differs")
    return host, toolchain, generated


def review_manifest(workspace, path, digest, final=True):
    require(sha(path) == digest, "primary source review digest mismatch")
    root_review = json_read(path)
    approved = (root_review.get("schema") == 1 and
                root_review.get("status") == "PASS_SCOPED_SOURCE_REVIEW_FOR_NATIVE_BUILD" and
                root_review.get("must_fix") == [])
    require(not final or approved, "final root source review is required")
    require(root_review.get("kernel_built") is False and root_review.get("hardware_tested") is False,
            "source review scope changed")
    candidate = relative(workspace, root_review.get("candidate"))
    require(candidate.name == "candidate" and candidate.is_dir(), "selected candidate directory missing")
    source_receipt = candidate.parent / "SOURCE-RECEIPT.json"
    require(sha(source_receipt) == root_review.get("source_receipt_sha256"), "source receipt changed")
    source = json_read(source_receipt)
    require(source.get("status") == "UNSTAGED_SOURCE_CANDIDATE_NOT_KERNEL_BUILT" and
            source.get("kernel_built") is False and source.get("hardware_tested") is False and
            source.get("uvd_arm_implemented") is False, "candidate source scope changed")
    require(source.get("baseline_files") == {Path(k).name: v for k, v in BASE_SOURCE.items()},
            "candidate baseline pins changed")
    require(set(source.get("changed_files", [])) == {Path(k).name for k in CHANGED} and
            len(source["changed_files"]) == len(CHANGED), "candidate patch scope changed")
    require(source.get("candidate_files") == root_review.get("source_sha256") and
            set(source["candidate_files"]) == {Path(k).name for k in SELECTED},
            "review candidate identities incomplete or changed")
    require(source.get("patch") == {"file": PATCH, "sha256": root_review.get("patch_sha256")},
            "source patch identity differs from review")
    files = {key: HERE / name for key, name in INPUT_FILES.items()}
    files.update(source_receipt=source_receipt, source_review=path)
    require(all(q.resolve().is_relative_to(workspace.resolve()) and q.is_file() and not q.is_symlink()
                for q in files.values()), "required build input is absent or outside workspace")
    require(sha(files["patch"]) == root_review["patch_sha256"] and
            sha(candidate.parent / PATCH) == root_review["patch_sha256"], "reviewed patch changed")
    require(sha(files["generator"]) == source["generator_sha256"], "source generator changed")
    for name, expected in source["generator_inputs"].items():
        require(name in ("baseline-pins.json", "live_prepare.h", "live_prepare.c.inc", "debugfs.c.inc"),
                "unexpected source generator input")
        require(sha(HERE / name) == expected, "source generator input changed: " + name)
    require(set(source["generator_inputs"]) == {"baseline-pins.json", "live_prepare.h",
            "live_prepare.c.inc", "debugfs.c.inc"}, "source generator input pins incomplete")
    for field, key in (("checker_sha256", "checker"), ("fixture_template_sha256", "fixture")):
        require(sha(files[key]) == root_review.get(field), "reviewed host input changed: " + key)
    pins = {key: {"path": q.relative_to(workspace).as_posix(), "sha256": sha(q)}
            for key, q in files.items()}
    source_pins = {PREFIX + name: {"path": (candidate / PREFIX / name).relative_to(workspace).as_posix(),
                                  "sha256": expected}
                   for name, expected in source["candidate_files"].items()}
    for name, row in source_pins.items():
        files[name] = pin_file(workspace, row)
    review = {**root_review, "pins": pins, "source_pins": source_pins}
    source_sha = reviewed_sources(review)
    require(all(source_sha[n] != BASE_SOURCE[n] for n in MODIFIED), "declared source modification absent")
    require(all(source_sha[n] == BASE_SOURCE[n] for n in set(BASE_SOURCE) - MODIFIED),
            "unmodified selected reference changed")
    patch_contract(files["patch"].read_text())
    receipts = root_review.get("host_receipts", [])
    require(isinstance(receipts, list) and len(receipts) == 2, "two pinned host receipts required")
    systems, generated, semantic, summaries = set(), set(), [], []
    for row in receipts:
        receipt = json_read(pin_file(workspace, row))
        host, toolchain, unit_sha = host_receipt(review, receipt)
        systems.add(host["system"])
        generated.add(unit_sha)
        semantic.append((receipt["runs"], receipt["sensitivity"]))
        summaries.append({"host": host, "receipt_sha256": row["sha256"], "toolchain": toolchain})
    require(systems == {"Darwin", "Linux"}, "both Mac and Linux receipts required")
    require(len(generated) == 1 and semantic[0] == semantic[1], "cross-host evidence differs")
    return review, files, {"approved_for_native_build_only": approved, "host_receipts": summaries}


def config_contract(old, new, config_values):
    before, after = config_values(old), config_values(new)
    require(before.get("CONFIG_LOCALVERSION") == '"-psx-ps4-baikal-uvd-ib-data-dev1"', "wrong baseline LOCALVERSION")
    before["CONFIG_LOCALVERSION"] = LOCALVERSION
    require(before == after, "config changed beyond LOCALVERSION")
    require(after.get("CONFIG_EXTRA_FIRMWARE") == '""' and after.get("CONFIG_INITRAMFS_SOURCE", '""') == '""',
            "embedded firmware or initramfs forbidden")


def source_contract(previous, actual, candidate_sha, config_changed=False):
    require(set(candidate_sha) == SELECTED, "source pin scope mismatch")
    require(set(actual) == set(previous) | ADDED and not set(previous) & ADDED, "source path set changed beyond new header")
    for name, digest in BASE_SOURCE.items():
        require(previous[name]["sha256"] == digest, "wrong source baseline: " + name)
    for name in SELECTED:
        require(actual[name]["sha256"] == candidate_sha[name], "selected candidate identity changed: " + name)
    for name in actual:
        if name in CHANGED:
            require(actual[name]["sha256"] == candidate_sha[name], "reviewed source not reproduced: " + name)
            require(actual[name]["kind"] == "file", "changed source is not a regular file")
            require(actual[name]["mode"] == (0o644 if name in ADDED else previous[name]["mode"]), "source mode changed")
            if name in MODIFIED:
                require(actual[name]["sha256"] != previous[name]["sha256"], "declared modification did not change")
        elif not (config_changed and name == ".config"):
            require(previous[name] == actual[name], "unexpected source delta: " + name)


def run_command(argv, cwd, log, env=None):
    with log.open("x") as stream:
        stream.write(json.dumps({"argv": list(map(str, argv)), "cwd": str(cwd)}) + "\n")
        stream.flush()
        process = subprocess.run(list(map(str, argv)), cwd=cwd, env=env or ENV,
                                 stdout=stream, stderr=subprocess.STDOUT)
    require(process.returncode == 0, f"command failed ({process.returncode}); preserved log: {log}")


class Native:
    def __init__(self, workspace, run, review=None, digest=None, tool_digest=None):
        self.workspace = workspace.resolve()
        self.run = relative(self.workspace, run)
        self.base = relative(self.workspace, BASE_REL)
        self.project = self.run / "project"
        self.logs = self.run / "receipts"
        self.source = self.project / "src" / TARGET
        self.old_source = self.base / "src" / OLD_TARGET
        require(not (self.run.is_relative_to(self.base) or self.base.is_relative_to(self.run)), "run overlaps baseline")
        self.review_path = relative(self.workspace, review) if review else None
        self.digest = digest
        self.tool_digest = tool_digest
        self.prep, self.build_helper, self.package_helper = load_helpers()

    def review(self, final=True):
        require(self.review_path is not None and self.digest is not None, "review path and digest required")
        review, files, summary = review_manifest(self.workspace, self.review_path, self.digest, final)
        require(not any(p.is_relative_to(self.run) for p in [self.review_path, *files.values()]), "review inputs overlap new run")
        return review, files, summary

    def baseline(self, snapshot=None):
        prep = self.prep
        reports = self.workspace / REPORT_REL
        for name, digest in BASE_RECEIPTS.items():
            require(sha(reports / name) == digest, "frozen IB-DATA evidence changed: " + name)
        inputs = json_read(reports / "project-inputs.json")
        require(len(inputs) == 772, "wrong project input count")
        names = sorted(set(prep.git_names(self.base) + prep.git_names(self.base, "--others", "--exclude-standard")))
        require(names == sorted(inputs), "baseline project path set changed")
        for name, row in inputs.items():
            require(prep.file_record(prep.contained(self.base, name)) == row, "baseline project input changed: " + name)
        require(prep.capture(["/usr/bin/git", "-C", self.base, "rev-parse", "HEAD"]).strip() == PROJECT,
                "baseline project commit changed")
        require(prep.capture(["/usr/bin/git", "-C", self.old_source, "rev-parse", "HEAD"]).strip() == UPSTREAM,
                "baseline upstream commit changed")
        sources = json_read(reports / "built-source.json")
        require(prep.source_inventory(self.base, OLD_TARGET) == sources and len(sources) == 95114, "frozen IB-DATA source inventory changed")
        require(sources[IRQ]["sha256"] == IRQ_SHA and all(sources[n]["sha256"] == digest for n, digest in BASE_SOURCE.items()),
                "baseline IRQ/helper identity changed")
        patches = prep.patch_records(self.base, OLD_TARGET)
        require(len(patches) == 72 and patches == json_read(reports / "prepare.json")["patches"], "baseline patch stack changed")
        built = json_read(reports / "build.json")
        packaged = json_read(reports / "package.json")
        require(built["status"] == "PASS_BUILD" and built["baseline_preserved"] and
                packaged["status"] == "PASS_PACKAGE" and packaged["baseline_preserved"], "frozen IB-DATA build/package incomplete")
        for name, digest in packaged["artifact_sha256"].items():
            require(sha(self.base / "output" / OLD_TARGET / name) == digest, "baseline artifact changed: " + name)
        require(sha(self.old_source / "vmlinux") == built["vmlinux_sha256"], "baseline vmlinux changed")
        require(packaged["artifacts"]["inputs_archive"]["sha256"] == BASE_INPUTS_ARCHIVE, "baseline inputs archive identity changed")
        module_root = self.base / "output" / OLD_TARGET / "modules"
        tree = json_read(reports / "module-tree.json")
        actual_tree = {}
        for path in prep.tree_paths(module_root):
            require(not path.is_symlink(), "baseline module tree gained a symlink")
            if not path.is_dir():
                actual_tree[path.relative_to(module_root).as_posix()] = prep.file_record(path)
        require(actual_tree == tree, "baseline module tree changed")
        if snapshot is not None:
            require(prep.snapshot_tree(self.base) == snapshot, "frozen baseline bytes or identities changed")
        return inputs, sources, patches

    def plan(self):
        summary = {"status": "READ_ONLY_PLAN_NOT_BUILD_APPROVAL", "baseline": str(self.base), "run": str(self.run),
                   "target": TARGET, "release": RELEASE, "phases": ["prepare-base", "prepare", "build", "package"],
                   "baseline_receipts": BASE_RECEIPTS, "helper_sha256": HELPERS, "hardware_acceptance": False}
        if self.review_path:
            _, _, summary["review_validation"] = self.review(final=False)
            self.baseline()
            summary["baseline_validated"] = True
        require(not self.run.exists(), "existing run retained; choose a new run for a fresh plan")
        return summary

    def phase(self, name, previous, action):
        self.prep.native_host()
        require(self.tool_digest is not None and sha(Path(__file__)) == self.tool_digest,
                "running native tool needs its reviewed --tool-sha256")
        review, files, summary = self.review()
        if name == "prepare-base":
            self.run.parent.mkdir(parents=True, exist_ok=True)
            self.run.mkdir()  # exclusive: no resume or implicit reuse after a failed attempt
            self.logs.mkdir()
            shutil.copy2(self.review_path, self.logs / "review.json")
            snapshot = self.prep.snapshot_tree(self.base)
            write_json(self.logs / "baseline-before.json", snapshot)
            write_json(self.logs / "identity.json", {"review_sha256": self.digest, "tool_sha256": sha(Path(__file__)),
                       "helper_sha256": HELPERS, "target": TARGET, "release": RELEASE, "review": summary,
                       "input_pins": review["pins"]})
        else:
            identity = json_read(self.logs / "identity.json")
            require(identity["review_sha256"] == self.digest and identity["tool_sha256"] == sha(Path(__file__)), "phase identity changed")
            require(sha(self.logs / "review.json") == self.digest, "saved review changed")
            require(identity["input_pins"] == review["pins"], "phase input pins changed")
            require(json_read(self.logs / f"{previous}.json")["status"] == "PASS_" + previous.upper().replace("-", "_"),
                    "previous phase did not complete")
            snapshot = json_read(self.logs / "baseline-before.json")
            require(sha(self.logs / "baseline-before.json") ==
                    json_read(self.logs / "prepare-base.json")["baseline_snapshot_sha256"], "baseline snapshot changed")
        require(not (self.logs / f"{name}-started.json").exists(), "existing phase attempt retained")
        write_json(self.logs / f"{name}-started.json", {"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "pid": os.getpid(), "review_sha256": self.digest})
        outcome = {"status": "FAILED_OR_INTERRUPTED", "hardware_acceptance": False, "target": TARGET, "release": RELEASE,
                   "review_sha256": self.digest, "identity_sha256": sha(self.logs / "identity.json"),
                   "previous_phase_receipt_sha256": sha(self.logs / f"{previous}.json") if previous else None}
        try:
            self.baseline(snapshot)
            outcome.update(action(review, files, snapshot))
            outcome["status"] = "PASS_" + name.upper().replace("-", "_")
        except BaseException as error:
            outcome["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            try:
                after = self.prep.snapshot_tree(self.base)
                write_json(self.logs / f"baseline-after-{name}.json", after)
                require(after == snapshot, "baseline changed during phase")
                outcome["baseline_preserved"] = True
            except BaseException as error:
                outcome.update(status="FAIL_BASELINE_PRESERVATION", baseline_preserved=False, baseline_error=str(error))
                raise
            finally:
                write_json(self.logs / f"{name}.json", outcome)
        return outcome

    def copy_base(self, review, files, snapshot):
        prep = self.prep
        inputs, sources, patches = self.baseline()
        # Worst-case allocation: no assumption that this filesystem supports reflinks.
        required_bytes = sum(r.get("bytes", 0) for r in snapshot.values() if r.get("kind") == "file")
        require(shutil.disk_usage(self.run).free >= required_bytes + 8 * 1024**3, "insufficient free space for independent copy and build")
        run_command(["/usr/bin/git", "clone", "--no-hardlinks", "--no-checkout", self.base, self.project], self.run, self.logs / "clone.log")
        run_command(["/usr/bin/git", "checkout", "--detach", PROJECT], self.project, self.logs / "checkout.log")
        for name in sorted(set(inputs) - set(prep.git_names(self.base))):
            destination = prep.contained(self.project, name)
            require(not destination.exists(), "copied overlay collides: " + name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(prep.contained(self.base, name), destination, follow_symlinks=False)
        self.source.parent.mkdir()
        run_command(["/usr/bin/cp", "-a", "--reflink=auto", self.old_source, self.source], self.run, self.logs / "copy-source.log")
        for name, row in inputs.items():
            require(prep.file_record(self.project / name) == row, "copied input mismatch: " + name)
        prefix = "src/" + OLD_TARGET + "/"
        expected = {name[len(prefix):]: prep.content_record(row) for name, row in snapshot.items() if name.startswith(prefix)}
        expected["."] = prep.content_record(snapshot["src/" + OLD_TARGET])
        actual = {name: prep.content_record(row) for name, row in prep.snapshot_tree(self.source).items()}
        require(actual == expected, "source/cache copy mismatch")
        write_json(self.logs / "baseline-inputs.json", inputs)
        write_json(self.logs / "baseline-source.json", sources)
        write_json(self.logs / "copied-cache-content.json", expected)
        return {"copied_inputs": len(inputs), "patches": len(patches),
                "baseline_snapshot_sha256": sha(self.logs / "baseline-before.json"),
                "copied_cache_sha256": sha(self.logs / "copied-cache-content.json"),
                "independence": prep.inspect_independence(snapshot, self.project), "source_patched": False, "kernel_built": False}

    def prepare(self, review, files, snapshot):
        prep = self.prep
        prep.inspect_independence(snapshot, self.project)
        self.cache_contract()
        frozen = self.project / PRIVATE
        frozen.mkdir()
        for key, name in {**INPUT_FILES, "source_receipt": "SOURCE-RECEIPT.json",
                          "source_review": "source-review.json"}.items():
            shutil.copy2(files[key], frozen / name)
            require(sha(frozen / name) == review["pins"][key]["sha256"], "input changed during freeze: " + key)
        shutil.copy2(self.review_path, frozen / "review.json")
        require(sha(frozen / "review.json") == self.digest, "review changed during freeze")
        utility_dir = frozen / "native_helpers"
        utility_dir.mkdir()
        for name, expected in HELPERS.items():
            shutil.copy2(HERE.parent / "ps4-uvd-data-control" / name, utility_dir / name)
            require(sha(utility_dir / name) == expected, "utility changed during freeze: " + name)
        for row in review["host_receipts"]:
            destination = frozen / (row["sha256"] + ".json")
            shutil.copy2(pin_file(self.workspace, row), destination)
            require(sha(destination) == row["sha256"], "host receipt changed during freeze")
        for rel in BASE_SOURCE:
            destination = frozen / "baseline" / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.old_source / rel, destination)
        # The unchanged checker reads receipt/patch next to its --candidate.
        # These metadata files sit above the kernel tree, not in its source inventory.
        for source, destination in ((frozen / "SOURCE-RECEIPT.json", self.source.parent / "SOURCE-RECEIPT.json"),
                                    (frozen / PATCH, self.source.parent / PATCH)):
            require(not destination.exists(), "existing host-check metadata retained")
            shutil.copy2(source, destination)
            require(sha(source) == sha(destination), "host-check metadata copy differs")
        old_values = prep.env_file(self.project / "targets" / (OLD_TARGET + ".env"))
        patch_dir = self.project / "platforms/ps4/patches" / TARGET
        patch_dir.mkdir()
        shutil.copy2(frozen / PATCH, patch_dir / PATCH)
        (patch_dir / "series").write_text((self.project / old_values["SERIES_FILE"]).read_text() + f"\n../{TARGET}/{PATCH}\n")
        old_config = self.project / old_values["CONFIG_FILE"]
        new_config = self.project / old_values["CONFIG_FILE"].replace("uvd-ib-data-dev1", "uvd-live-prepare-dev1")
        text = old_config.read_text()
        old_line = 'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-ib-data-dev1"'
        require(text.count(old_line) == 1, "baseline raw config has wrong LOCALVERSION")
        with new_config.open("x") as stream:
            stream.write(text.replace(old_line, "CONFIG_LOCALVERSION=" + LOCALVERSION))
        config_contract(old_config, new_config, self.build_helper.config_values)
        with (self.project / "targets" / (TARGET + ".env")).open("x") as stream:
            stream.write((self.project / "targets" / (OLD_TARGET + ".env")).read_text().replace("uvd-ib-data-dev1", "uvd-live-prepare-dev1"))
        patches = prep.patch_records(self.project, TARGET)
        require(patches[:-1] == prep.patch_records(self.base, OLD_TARGET) and len(patches) == 73, "ordered stack changed beyond append")
        require(patches[-1]["sha256"] == review["pins"]["patch"]["sha256"], "appended patch differs")
        run_command(["/usr/bin/git", "apply", "--check", patch_dir / PATCH], self.source, self.logs / "patch-check.log")
        run_command(["/usr/bin/git", "apply", patch_dir / PATCH], self.source, self.logs / "patch-apply.log")
        current = prep.source_inventory(self.project, TARGET)
        source_contract(json_read(self.logs / "baseline-source.json"), current, reviewed_sources(review))
        run_command(["/usr/bin/python3", "-B", "-I", frozen / "check.py",
                     "--baseline", frozen / "baseline", "--candidate", self.source,
                     "--out", self.logs / "host-prebuild", "--cc", "/usr/bin/gcc"],
                    self.run, self.logs / "host-prebuild.log")
        host = json_read(self.logs / "host-prebuild/receipt.json")
        host_receipt(review, host)
        write_json(self.logs / "prepared-source.json", current)
        inventory = self.inventory()
        write_json(self.logs / "project-inputs.json", inventory)
        return {"patches": patches, "source_inventory_sha256": sha(self.logs / "prepared-source.json"),
                "project_inputs_sha256": sha(self.logs / "project-inputs.json"), "host_receipt_sha256": sha(self.logs / "host-prebuild/receipt.json"),
                "changed_sources": sorted(CHANGED), "kernel_built": False}

    def cache_contract(self, candidate_sha=None):
        require(sha(self.logs / "copied-cache-content.json") ==
                json_read(self.logs / "prepare-base.json")["copied_cache_sha256"], "copied cache manifest changed")
        expected = json_read(self.logs / "copied-cache-content.json")
        actual = {name: self.prep.content_record(row) for name, row in self.prep.snapshot_tree(self.source).items()}
        allowed_new = ADDED if candidate_sha is not None else set()
        require(set(actual) == set(expected) | allowed_new, "source/cache path set changed before build")
        for name in actual:
            if candidate_sha is not None and name in CHANGED:
                require(actual[name]["sha256"] == candidate_sha[name] and actual[name]["kind"] == "file" and
                        actual[name]["mode"] == (0o644 if name in ADDED else expected[name]["mode"]), "cache contains wrong candidate source")
            else:
                require(actual[name] == expected[name], "source/cache changed before build: " + name)

    def inventory(self):
        names = sorted(set(self.prep.git_names(self.project) + self.prep.git_names(self.project, "--others", "--exclude-standard")))
        return {name: self.prep.file_record(self.prep.contained(self.project, name)) for name in names}

    def validate_prepared(self, review, snapshot, built=False):
        prep = self.prep
        receipt = json_read(self.logs / "prepare.json")
        require(sha(self.logs / "project-inputs.json") == receipt["project_inputs_sha256"], "project manifest changed")
        require(self.inventory() == json_read(self.logs / "project-inputs.json"), "prepared project input changed")
        require(sha(self.logs / "prepared-source.json") == receipt["source_inventory_sha256"], "source manifest changed")
        actual = prep.source_inventory(self.project, TARGET)
        source_contract(json_read(self.logs / "baseline-source.json"), actual, reviewed_sources(review), built)
        require(prep.patch_records(self.project, TARGET) == receipt["patches"], "prepared patch stack changed")
        require(prep.capture(["/usr/bin/git", "-C", self.source, "rev-parse", "HEAD"]).strip() == UPSTREAM, "prepared upstream changed")
        require(prep.capture(["/usr/bin/git", "-C", self.project, "rev-parse", "HEAD"]).strip() == PROJECT, "prepared project commit changed")
        for name, row in json_read(self.logs / "baseline-inputs.json").items():
            require(prep.file_record(self.project / name) == row, "inherited project input changed: " + name)
        prep.inspect_independence(snapshot, self.project)
        if not built:
            self.cache_contract(reviewed_sources(review))
        return actual

    def build(self, review, files, snapshot, jobs):
        require(jobs in range(1, 7), "jobs must be 1 through 6")
        prep = self.prep
        self.validate_prepared(review, snapshot)
        values = prep.env_file(self.project / "targets" / (TARGET + ".env"))
        require(values["TARGET_NAME"] == TARGET and values["KERNEL_RELEASE"] == RELEASE and values["BASE_COMMIT"] == UPSTREAM,
                "target values changed")
        output = self.project / "output" / TARGET
        require(not output.exists(), "existing build output retained")
        env = {**ENV, "SOURCE_DATE_EPOCH": values["SOURCE_DATE_EPOCH"],
               "KBUILD_BUILD_TIMESTAMP": values["KBUILD_RELEASE_TIMESTAMP"], "KBUILD_BUILD_USER": values["KBUILD_RELEASE_USER"],
               "KBUILD_BUILD_HOST": values["KBUILD_RELEASE_HOST"], "KBUILD_BUILD_VERSION": values["KBUILD_RELEASE_VERSION"]}
        toolchain = {"host": {"system": platform.system(), "machine": platform.machine(), "release": platform.release()},
                     "environment": env, "gcc_sha256": sha(Path("/usr/bin/gcc").resolve()),
                     "gcc_path": str(Path("/usr/bin/gcc").resolve()), "compiler_argv": ["/usr/bin/gcc"],
                     "gcc_version": prep.capture(["/usr/bin/gcc", "--version"]),
                     "gcc_target": prep.capture(["/usr/bin/gcc", "-dumpmachine"]).strip(),
                     "ld_version": prep.capture(["/usr/bin/ld", "--version"]), "make_version": prep.capture(["/usr/bin/make", "--version"]),
                     "pahole_version": prep.capture(["/usr/bin/pahole", "--version"]), "python_version": sys.version}
        write_json(self.logs / "compiler.json", toolchain)
        before_cache = prep.snapshot_tree(self.source)
        write_json(self.logs / "cache-before-build.json", before_cache)
        make = ["/usr/bin/make", "ARCH=x86_64", "LOCALVERSION=", "CC=/usr/bin/gcc", "HOSTCC=/usr/bin/gcc"]
        # amdgpu.h changes struct layout: clean all copied generated objects, so
        # no unchanged C unit can retain a stale layout because of cache mtimes.
        # This is the independent private copy, never the frozen baseline.
        run_command([*make, "clean"], self.source, self.logs / "clean.log", env)
        source_contract(json_read(self.logs / "baseline-source.json"),
                        prep.source_inventory(self.project, TARGET), reviewed_sources(review))
        text = (self.old_source / ".config").read_text()
        old_line = 'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-ib-data-dev1"'
        require(text.count(old_line) == 1, "resolved baseline LOCALVERSION missing")
        (self.source / ".config").write_text(text.replace(old_line, "CONFIG_LOCALVERSION=" + LOCALVERSION))
        for target in ("olddefconfig", "syncconfig"):
            run_command([*make, target], self.source, self.logs / (target + ".log"), env)
        config_contract(self.old_source / ".config", self.source / ".config", self.build_helper.config_values)
        version = subprocess.check_output([*make, "-s", "kernelrelease"], cwd=self.source, env=env, text=True).strip()
        require(version == RELEASE, "wrong kernelrelease")
        run_command([*make, f"-j{jobs}", "bzImage", "modules"], self.source, self.logs / "kernel-build.log", env)
        require(subprocess.check_output([*make, "-s", "kernelrelease"], cwd=self.source, env=env, text=True).strip() == RELEASE,
                "release changed during build")
        config_contract(self.old_source / ".config", self.source / ".config", self.build_helper.config_values)
        sources = self.validate_prepared(review, snapshot, built=True)
        write_json(self.logs / "built-source.json", sources)
        output.mkdir(parents=True)
        for rel, name in (("arch/x86/boot/bzImage", "bzImage"), ("System.map", "System.map"), (".config", "config")):
            shutil.copy2(self.source / rel, output / name)
        (output / "version.txt").write_text(RELEASE + "\n")
        staging = output / "modules"
        run_command([*make, f"INSTALL_MOD_PATH={staging}", "INSTALL_MOD_STRIP=1", "modules_install"], self.source,
                    self.logs / "modules-install.log", env)
        module_root = staging / "lib/modules" / RELEASE
        for name in ("build", "source"):
            path = module_root / name
            if path.is_symlink():
                path.unlink()
            else:
                require(not path.exists(), "unexpected installed module link type")
        tree, modules = {}, {}
        for path in prep.tree_paths(staging):
            require(not path.is_symlink(), "installed modules may not contain symlinks")
            if path.is_file():
                name = path.relative_to(staging).as_posix()
                tree[name] = prep.file_record(path)
                if path.suffix == ".ko":
                    vermagic = prep.capture(["/usr/bin/modinfo", "-F", "vermagic", path]).strip()
                    require(vermagic.split()[0] == RELEASE, "module vermagic mismatch")
                    modules[name] = {**tree[name], "vermagic": vermagic, "elf_header": self.build_helper.module_elf_header(path)}
            else:
                require(path.is_dir(), "special module tree entry")
        require(len(modules) == 41, "expected complete set of 41 modules")
        baseline_modules = json_read(self.workspace / REPORT_REL / "modules.json")
        expected_names = {name.replace("/" + OLD_RELEASE + "/", "/" + RELEASE + "/")
                          for name in baseline_modules}
        require(set(modules) == expected_names, "matching module path set changed")
        baseline_tree = json_read(self.workspace / REPORT_REL / "module-tree.json")
        require(set(tree) == {name.replace("/" + OLD_RELEASE + "/", "/" + RELEASE + "/") for name in baseline_tree},
                "whole module-tree path set changed")
        write_json(self.logs / "modules.json", modules)
        write_json(self.logs / "module-tree.json", tree)
        require(self.validate_prepared(review, snapshot, built=True) == sources, "source changed during modules_install")
        after_cache = prep.snapshot_tree(self.source)
        changes = {name: {"before": prep.content_record(before_cache[name]) if name in before_cache else None,
                          "after": prep.content_record(after_cache[name]) if name in after_cache else None}
                   for name in before_cache.keys() | after_cache.keys()
                   if (prep.content_record(before_cache[name]) if name in before_cache else None) !=
                      (prep.content_record(after_cache[name]) if name in after_cache else None)}
        write_json(self.logs / "all-cache-content-changes.json", changes)
        write_json(output / "manifest.json", {"target": TARGET, "release": RELEASE, "project_base_commit": PROJECT,
                   "upstream_commit": UPSTREAM, "baseline_inputs_archive_sha256": BASE_INPUTS_ARCHIVE, "active_patch_count": 73,
                   "review_sha256": self.digest, "patch_sha256": review["pins"]["patch"]["sha256"], "compiler": toolchain,
                   "project_inputs_sha256": sha(self.logs / "project-inputs.json"),
                   "built_source_inventory_sha256": sha(self.logs / "built-source.json"),
                   "native_tool_sha256": sha(Path(__file__)), "helper_sha256": HELPERS, "irq_source_sha256": IRQ_SHA,
                   "hardware_acceptance": False, "compiled_audit": "pending; build does not replace object review"})
        return {"module_count": 41, "compiler_sha256": sha(self.logs / "compiler.json"),
                "all_cache_changes_sha256": sha(self.logs / "all-cache-content-changes.json"),
                "source_inventory_sha256": sha(self.logs / "built-source.json"), "module_tree_sha256": sha(self.logs / "module-tree.json"),
                "modules_sha256": sha(self.logs / "modules.json"), "vmlinux_sha256": sha(self.source / "vmlinux"),
                "artifact_sha256": {p.name: sha(p) for p in output.iterdir() if p.is_file()},
                "jobs": jobs, "compiled_audit": "pending"}

    def package(self, review, files, snapshot):
        prep = self.prep
        sources = self.validate_prepared(review, snapshot, built=True)
        built = json_read(self.logs / "build.json")
        output = self.project / "output" / TARGET
        require(sha(self.logs / "built-source.json") == built["source_inventory_sha256"] and
                sources == json_read(self.logs / "built-source.json"), "built source changed")
        for name, digest in built["artifact_sha256"].items():
            require(sha(output / name) == digest, "built artifact changed: " + name)
        require(sha(self.source / "vmlinux") == built["vmlinux_sha256"], "linked kernel changed")
        require(sha(self.logs / "module-tree.json") == built["module_tree_sha256"] and
                sha(self.logs / "modules.json") == built["modules_sha256"], "module receipts changed")
        expected = json_read(self.logs / "module-tree.json")
        staging = output / "modules"
        actual = {}
        for path in prep.tree_paths(staging):
            require(not path.is_symlink(), "module tree gained a symlink")
            if not path.is_dir():
                actual[path.relative_to(staging).as_posix()] = prep.file_record(path)
        require(actual == expected and all(row["kind"] == "file" for row in actual.values()), "whole matching module tree changed")
        values = prep.env_file(self.project / "targets" / (TARGET + ".env"))
        epoch = int(values["SOURCE_DATE_EPOCH"])
        archive = self.package_helper.archive
        module_name = f"modules-{RELEASE}.tar.zst"
        checks = {"modules": archive(output / module_name, {name: staging / name for name in expected}, epoch)}
        inventory = json_read(self.logs / "project-inputs.json")
        inputs_archive = output / "inputs.tar.zst"
        checks["inputs"] = archive(inputs_archive, {name: self.project / name for name in inventory}, epoch,
                                   {"PSX-INPUTS-MANIFEST.json": (self.logs / "project-inputs.json").read_bytes()})
        source_name = f"source-{RELEASE}.tar.zst"
        prefix = "linux-" + RELEASE
        rebuild = (f"Private native build, no hardware acceptance. Release: {RELEASE}\n"
                   "Resolved .config and full corresponding source are included. Firmware is external.\n"
                   "Use the manifest's toolchain and environment on Linux x86-64.\n"
                   "make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc olddefconfig syncconfig\n"
                   "make ARCH=x86_64 LOCALVERSION= CC=/usr/bin/gcc HOSTCC=/usr/bin/gcc -j6 bzImage modules\n"
                   "No binary reproducibility or object-review completion is implied.\n").encode()
        checks["source"] = archive(output / source_name, {f"{prefix}/{name}": self.source / name for name in sources}, epoch,
                                   {f"{prefix}/PSX-BUILD-INPUTS.tar.zst": inputs_archive.read_bytes(),
                                    f"{prefix}/PSX-BUILD-MANIFEST.json": (output / "manifest.json").read_bytes(),
                                    f"{prefix}/PSX-SOURCE-INPUTS.json": (self.logs / "built-source.json").read_bytes(),
                                    f"{prefix}/PSX-REBUILD.md": rebuild})
        write_json(self.logs / "archive-verification.json", checks)
        artifacts = {"schema": 1, "target": TARGET, "platform": "ps4-baikal", "kernel_release": RELEASE,
                     "kernel_sha256": sha(output / "bzImage"), "kernel_config_sha256": sha(output / "config"),
                     "modules_archive": {"path": module_name, "sha256": sha(output / module_name)}, "module_count": 41,
                     "source_archive": {"path": source_name, "sha256": sha(output / source_name)},
                     "inputs_archive": {"path": inputs_archive.name, "sha256": sha(inputs_archive)}, "active_patch_count": 73,
                     "embedded_firmware": False, "review_sha256": self.digest, "hardware_acceptance": "none",
                     "compiled_audit": "pending", "publication": "private only"}
        write_json(output / "artifacts.json", artifacts)
        hashes = {p.name: sha(p) for p in output.iterdir() if p.is_file()}
        with (output / "SHA256SUMS").open("x") as stream:
            stream.write("".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items())))
        require(prep.source_inventory(self.project, TARGET) == sources and self.inventory() == inventory, "inputs changed during packaging")
        return {"artifacts": artifacts, "artifact_sha256": hashes, "archive_verification": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("plan", "verify-review", "prepare-base", "prepare", "build", "package"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run", default=DEFAULT_RUN, help="workspace-relative fresh directory")
    parser.add_argument("--review", help="workspace-relative primary review manifest")
    parser.add_argument("--review-sha256")
    parser.add_argument("--tool-sha256", help="reviewed digest of this native.py; required by mutating phases")
    parser.add_argument("--jobs", type=int, default=6)
    args = parser.parse_args()
    work = Native(args.workspace, args.run, args.review, args.review_sha256, args.tool_sha256)
    if args.phase == "plan":
        result = work.plan()
    elif args.phase == "verify-review":
        review, _, summary = work.review()
        result = {"status": "PASS_READ_ONLY_SOURCE_AND_HOST_RECEIPT_VALIDATION",
                  "review_sha256": args.review_sha256, "tool_sha256": sha(Path(__file__)),
                  "target": TARGET, "release": RELEASE, "input_pins": review["pins"],
                  "review": summary, "kernel_built": False, "hardware_acceptance": False}
    else:
        action, previous = {"prepare-base": (work.copy_base, None), "prepare": (work.prepare, "prepare-base"),
                            "build": (lambda *a: work.build(*a, args.jobs), "prepare"),
                            "package": (work.package, "build")}[args.phase]
        result = work.phase(args.phase, previous, action)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
