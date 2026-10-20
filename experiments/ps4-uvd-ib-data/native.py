#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Private Revision C-derived native build; explicit, exclusive phases, no devices."""

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
OLD_TARGET = "7.2-ps4-baikal-uvd-status-observation-dev1"
TARGET = "7.2-ps4-baikal-uvd-ib-data-dev1"
RELEASE = "7.2.11-psx-ps4-baikal-uvd-ib-data-dev1"
OLD_RELEASE = "7.2.11-psx-ps4-baikal-uvd-status-observation-dev1"
LOCALVERSION = '"-psx-ps4-baikal-uvd-ib-data-dev1"'
BASE_REL = "lab/uvd-2026-10-19/status-observation/build-preparation/native-01/project"
REPORT_REL = "lab/uvd-2026-10-19/status-observation/build-preparation/native-01/receipts"
DEFAULT_RUN = "lab/uvd-2026-10-20/ib-data/build-preparation/native-01"
PATCH = "0010-drm-amdgpu-ps4-uvd-ib-data-marker.patch"
PRIVATE = "private-build/ib-data"
READY = "drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_ready.c"
PROJECT = "35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2"
UPSTREAM = "a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea"
BASE_READY = "60b55de93ef8a3b38626fbb5f063d76dd90cd52a5cd10b9250015fb384707518"
IRQ = "drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_uvd_irq.c"
IRQ_SHA = "ccf618c976b9a7b80045ffaeb81b7ede0707fa98bef8aca4bc7a7653a3de5480"
BASE_INPUTS_ARCHIVE = "d03fa3229448c3f0134fb2cafca05cc88c6ad4501890fa00ef4c8fae288f7082"
BASE_RECEIPTS = {
    "build.json": "f7f68db8d2e321b34088a3dac3974dd06ae97e0acc2a18b7573391c7c14e2b8e",
    "built-source.json": "3f3890068380963d6380ef5a8ae59bb7721c22a6cfa9e847206a6c9f56ba7cc0",
    "identity.json": "87aba53bd0ad05b8f8f2c997aa075afcace42980bb8edbf806ac63525023af80",
    "module-tree.json": "b04a3acfbe1ecdc397450bd78c15e36b94ae0277e98b8e8e3685b15700c9907d",
    "modules.json": "f394ee4b43b631fd2393f6ce739cce5c1aead2cdd3358c63a2fcff1070203071",
    "package.json": "a46478cd740814d90d115ecac71a652f546d6f542936e0cc41086e11b672760e",
    "prepare-base.json": "8a5290aaf8abc9a71eaf564b120b3ffed2c9896ec8693b7d5119d75a632e4004",
    "prepare.json": "09499b49c8ce2a90603a92d0fdb03d223eeda694c76808e99147d541143cec71",
    "project-inputs.json": "78f049219988e25151d515159364c99fe26ab747f43bde53c8ffe98f31475839"
}
HELPERS = {
    "prepare_native.py": "a64057a9826fa5b2c87b6dc9e53c613a6ea82c7acfe383d3a421c06f4cb58fca",
    "build_native.py": "5b67b651b77ccfcee4613fdf12febe47193ce52a0abae96ce38e7a39e3e20d43",
    "package_native.py": "1f00d27a604a159a16f9c443c23e03da441e0c34ad8c5a0c9a167a2eb71fe686",
}
PREFIX = "drivers/gpu/drm/amd/amdgpu/"
CANARY = PREFIX + "amdgpu_ps4_map_canary.c"
READY_H = PREFIX + "amdgpu_ps4_uvd_ready.h"
RING_H = PREFIX + "amdgpu_ps4_uvd_ring_plan.h"
IB_H = PREFIX + "amdgpu_ps4_uvd_ib_data_plan.h"
MODIFIED = frozenset((READY, CANARY, READY_H))
ADDED = frozenset((IB_H,))
CHANGED = MODIFIED | ADDED
BASE_SOURCE = {
    READY: BASE_READY,
    CANARY: "523a24339622bf81e2dd3e5d99f88cdfe77de1709b34f74f9369ad64f70b6305",
    READY_H: "f49a2648015b8c23998f1e95cab6fc1c9ed157e076eadfa74116c926dd98347d",
    RING_H: "2299fd28005bdbcc2aa3c3664740f5d87a34cb7294c705c45facdde46d44df2e",
}
PINS = {"patch", "checker", "fixture", "planner", "flow", "generator", "design", "native_tool", "independent_review"}
ENV = {"PATH": "/usr/bin:/bin:/usr/bin/core_perl", "LC_ALL": "C", "TZ": "UTC",
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
    require(set(review.get("source_pins", {})) == CHANGED, "review must pin the exact four-file source delta")
    return {name: row["sha256"] for name, row in review["source_pins"].items()}


def patch_contract(text):
    sections = re.split(r"^diff --git a/(\S+) b/(\S+)\n", text, flags=re.M)
    require(len(sections) == 1 + 3 * len(CHANGED), "patch must have exactly four source diffs")
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
    require(receipt.get("status") == "PASS_EXTRACTED_SOURCE_HOST_ONLY", "host receipt did not pass")
    for key, pin in (("patch_sha256", "patch"), ("checker_sha256", "checker"),
                     ("fixture_sha256", "fixture"), ("planner_sha256", "planner"),
                     ("flow_sha256", "flow"), ("generator_sha256", "generator")):
        require(receipt.get(key) == review["pins"][pin]["sha256"], "host receipt input mismatch: " + key)
    expected_outputs = {**reviewed_sources(review), RING_H: BASE_SOURCE[RING_H]}
    require(receipt.get("output_sha256") == expected_outputs, "host receipt candidate scope or identity mismatch")
    require(receipt.get("baseline_sha256") == {Path(k).name: v for k, v in BASE_SOURCE.items()},
            "host receipt baseline mismatch")
    toolchain = receipt.get("toolchain", {})
    for key in ("compiler_argv", "compiler_path", "compiler_version", "compiler_target"):
        require(bool(toolchain.get(key)), "compiler identity missing: " + key)
    host = toolchain.get("host", {})
    require(host.get("system") in ("Darwin", "Linux"), "unexpected receipt host")
    require(host.get("system") != "Linux" or host.get("machine") == "x86_64", "Linux fixture host must be x86-64")
    results = receipt.get("results", [])
    require(len(results) == 2 and {r.get("mode") for r in results} == {"optimized", "sanitized"},
            "optimized and sanitizer results required")
    for result in results:
        require(result.get("status") == "PASS" and result.get("returncode") == 0, "host execution failed")
        require(re.match(rf"PASS: {review['host_cases']} actual-source cases;", result.get("output", "")),
                "host case count disagrees")
    mutants = receipt.get("sensitivity", [])
    declared = review.get("required_mutations", {})
    require(isinstance(declared, dict) and declared and all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in declared.items()),
            "review must declare the necessary mutation names and witnesses")
    require(len(mutants) == len(declared) and {m.get("name") for m in mutants} == set(declared), "mutation set incomplete or unexpected")
    for mutant in mutants:
        witness = declared[mutant["name"]]
        require(mutant.get("detected") is True and isinstance(mutant.get("returncode"), int) and mutant["returncode"] != 0,
                "mutation run did not detect the defect")
        require(mutant.get("expected_witness") == witness and mutant.get("observed_witness") == witness,
                "mutation failed for a different reason")
    generated = receipt.get("generated_fixture_sha256")
    require(isinstance(generated, str) and re.fullmatch(r"[0-9a-f]{64}", generated), "generated fixture digest missing")
    return host, toolchain, generated


def review_manifest(workspace, path, digest, final=True):
    require(sha(path) == digest, "primary review manifest digest mismatch")
    review = json_read(path)
    require(review.get("schema") == 1 and review.get("target") == TARGET, "wrong review schema/target")
    require(review.get("release") == RELEASE, "wrong review release")
    approved = (review.get("final_primary_review") is True and
                review.get("verdict") == "APPROVED_FOR_NATIVE_BUILD_ONLY" and review.get("unresolved_findings") == [])
    require(not final or approved, "final primary review for native build is required")
    require(set(review.get("pins", {})) == PINS, "incomplete review input pins")
    files = {key: pin_file(workspace, row) for key, row in review["pins"].items()}
    source_sha = reviewed_sources(review)
    files.update({name: pin_file(workspace, row) for name, row in review["source_pins"].items()})
    require(all(source_sha[name] != BASE_SOURCE[name] for name in MODIFIED), "each declared source modification must differ from Revision C")
    require(source_sha[IB_H] == review["pins"]["planner"]["sha256"], "new kernel planner must equal the reviewed pure planner")
    require(files["native_tool"].resolve() == Path(__file__).resolve(), "running native tool is not the reviewed tool")
    patch_contract(files["patch"].read_text())
    require(isinstance(review.get("host_cases"), int) and review["host_cases"] > 0, "host case count missing")
    receipts = review.get("host_receipts", [])
    require(isinstance(receipts, list) and len(receipts) == 2, "two independently pinned host receipts required")
    systems, generated, summaries = set(), set(), []
    for row in receipts:
        receipt = json_read(pin_file(workspace, row))
        host, toolchain, unit_sha = host_receipt(review, receipt)
        systems.add(host["system"])
        generated.add(unit_sha)
        summaries.append({"host": host, "receipt_sha256": row["sha256"], "toolchain": toolchain})
    require(systems == {"Darwin", "Linux"}, "both Mac and Linux receipts required")
    require(len(generated) == 1, "hosts did not run identical generated fixture source")
    return review, files, {"approved_for_native_build_only": approved, "host_receipts": summaries}


def config_contract(old, new, config_values):
    before, after = config_values(old), config_values(new)
    require(before.get("CONFIG_LOCALVERSION") == '"-psx-ps4-baikal-uvd-status-observation-dev1"', "wrong baseline LOCALVERSION")
    before["CONFIG_LOCALVERSION"] = LOCALVERSION
    require(before == after, "config changed beyond LOCALVERSION")
    require(after.get("CONFIG_EXTRA_FIRMWARE") == '""' and after.get("CONFIG_INITRAMFS_SOURCE", '""') == '""',
            "embedded firmware or initramfs forbidden")


def source_contract(previous, actual, candidate_sha, config_changed=False):
    require(set(candidate_sha) == CHANGED, "source pin scope mismatch")
    require(set(actual) == set(previous) | ADDED and not set(previous) & ADDED, "source path set changed beyond new header")
    for name, digest in BASE_SOURCE.items():
        require(previous[name]["sha256"] == digest, "wrong source baseline: " + name)
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
    def __init__(self, workspace, run, review=None, digest=None):
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
            require(sha(reports / name) == digest, "Revision C evidence changed: " + name)
        inputs = json_read(reports / "project-inputs.json")
        require(len(inputs) == 752, "wrong project input count")
        names = sorted(set(prep.git_names(self.base) + prep.git_names(self.base, "--others", "--exclude-standard")))
        require(names == sorted(inputs), "baseline project path set changed")
        for name, row in inputs.items():
            require(prep.file_record(prep.contained(self.base, name)) == row, "baseline project input changed: " + name)
        require(prep.capture(["/usr/bin/git", "-C", self.base, "rev-parse", "HEAD"]).strip() == PROJECT,
                "baseline project commit changed")
        require(prep.capture(["/usr/bin/git", "-C", self.old_source, "rev-parse", "HEAD"]).strip() == UPSTREAM,
                "baseline upstream commit changed")
        sources = json_read(reports / "built-source.json")
        require(prep.source_inventory(self.base, OLD_TARGET) == sources and len(sources) == 95113, "Revision C source inventory changed")
        require(sources[IRQ]["sha256"] == IRQ_SHA and all(sources[n]["sha256"] == digest for n, digest in BASE_SOURCE.items()),
                "baseline IRQ/helper identity changed")
        patches = prep.patch_records(self.base, OLD_TARGET)
        require(len(patches) == 71 and patches == json_read(reports / "prepare.json")["patches"], "baseline patch stack changed")
        built = json_read(reports / "build.json")
        packaged = json_read(reports / "package.json")
        require(built["status"] == "PASS_BUILD" and built["baseline_preserved"] and
                packaged["status"] == "PASS_PACKAGE" and packaged["baseline_preserved"], "Revision C build/package incomplete")
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
        review, files, summary = self.review()
        if name == "prepare-base":
            self.run.parent.mkdir(parents=True, exist_ok=True)
            self.run.mkdir()  # exclusive: no resume or implicit reuse after a failed attempt
            self.logs.mkdir()
            shutil.copy2(self.review_path, self.logs / "review.json")
            snapshot = self.prep.snapshot_tree(self.base)
            write_json(self.logs / "baseline-before.json", snapshot)
            write_json(self.logs / "identity.json", {"review_sha256": self.digest, "tool_sha256": sha(Path(__file__)),
                       "helper_sha256": HELPERS, "target": TARGET, "release": RELEASE, "review": summary})
        else:
            identity = json_read(self.logs / "identity.json")
            require(identity["review_sha256"] == self.digest and identity["tool_sha256"] == sha(Path(__file__)), "phase identity changed")
            require(sha(self.logs / "review.json") == self.digest, "saved review changed")
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
        for key, name in (("patch", PATCH), ("checker", "check.py"), ("fixture", "fixture.c"), ("planner", "ps4_uvd_ib_data_plan.h"),
                          ("flow", "ib_data_flow.inc"), ("generator", "build_candidate.py"),
                          ("design", "DESIGN.md"), ("native_tool", "native.py"), ("independent_review", "independent-review.md")):
            shutil.copy2(files[key], frozen / name)
            require(sha(frozen / name) == review["pins"][key]["sha256"], "input changed during freeze: " + key)
        shutil.copy2(self.review_path, frozen / "review.json")
        require(sha(frozen / "review.json") == self.digest, "review changed during freeze")
        for row in review["host_receipts"]:
            destination = frozen / (row["sha256"] + ".json")
            shutil.copy2(pin_file(self.workspace, row), destination)
            require(sha(destination) == row["sha256"], "host receipt changed during freeze")
        for name in (Path(READY).name, "amdgpu_ps4_map_canary.c", "amdgpu_ps4_uvd_ready.h", "amdgpu_ps4_uvd_ring_plan.h"):
            rel = str(Path(READY).parent / name)
            destination = frozen / "baseline" / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.old_source / rel, destination)
        old_values = prep.env_file(self.project / "targets" / (OLD_TARGET + ".env"))
        patch_dir = self.project / "platforms/ps4/patches" / TARGET
        patch_dir.mkdir()
        shutil.copy2(frozen / PATCH, patch_dir / PATCH)
        (patch_dir / "series").write_text((self.project / old_values["SERIES_FILE"]).read_text() + f"\n../{TARGET}/{PATCH}\n")
        old_config = self.project / old_values["CONFIG_FILE"]
        new_config = self.project / old_values["CONFIG_FILE"].replace("uvd-status-observation-dev1", "uvd-ib-data-dev1")
        text = old_config.read_text()
        old_line = 'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-status-observation-dev1"'
        require(text.count(old_line) == 1, "baseline raw config has wrong LOCALVERSION")
        with new_config.open("x") as stream:
            stream.write(text.replace(old_line, "CONFIG_LOCALVERSION=" + LOCALVERSION))
        config_contract(old_config, new_config, self.build_helper.config_values)
        with (self.project / "targets" / (TARGET + ".env")).open("x") as stream:
            stream.write((self.project / "targets" / (OLD_TARGET + ".env")).read_text().replace("uvd-status-observation-dev1", "uvd-ib-data-dev1"))
        patches = prep.patch_records(self.project, TARGET)
        require(patches[:-1] == prep.patch_records(self.base, OLD_TARGET) and len(patches) == 72, "ordered stack changed beyond append")
        require(patches[-1]["sha256"] == review["pins"]["patch"]["sha256"], "appended patch differs")
        run_command(["/usr/bin/git", "apply", "--check", patch_dir / PATCH], self.source, self.logs / "patch-check.log")
        run_command(["/usr/bin/git", "apply", patch_dir / PATCH], self.source, self.logs / "patch-apply.log")
        current = prep.source_inventory(self.project, TARGET)
        source_contract(json_read(self.logs / "baseline-source.json"), current, reviewed_sources(review))
        run_command(["/usr/bin/python3", "-B", "-I", frozen / "check.py", "--baseline", frozen / "baseline", "--source", self.source,
                     "--headers", self.source / "drivers/gpu/drm/amd/include/asic_reg", "--report", self.logs / "host-prebuild"],
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
        text = (self.old_source / ".config").read_text()
        old_line = 'CONFIG_LOCALVERSION="-psx-ps4-baikal-uvd-status-observation-dev1"'
        require(text.count(old_line) == 1, "resolved baseline LOCALVERSION missing")
        (self.source / ".config").write_text(text.replace(old_line, "CONFIG_LOCALVERSION=" + LOCALVERSION))
        make = ["/usr/bin/make", "ARCH=x86_64", "LOCALVERSION=", "CC=/usr/bin/gcc", "HOSTCC=/usr/bin/gcc"]
        for target in ("olddefconfig", "syncconfig"):
            run_command([*make, target], self.source, self.logs / (target + ".log"), env)
        config_contract(self.old_source / ".config", self.source / ".config", self.build_helper.config_values)
        version = subprocess.check_output([*make, "-s", "kernelrelease"], cwd=self.source, env=env, text=True).strip()
        require(version == RELEASE, "wrong kernelrelease")
        # Force the reviewed translation unit to compile even if cache mtimes coincide.
        removed = {}
        for changed_c in (READY, CANARY):
            obj = Path(changed_c).with_suffix(".o")
            for rel in (obj, obj.with_name("." + obj.name + ".cmd")):
                path = self.source / rel
                require(path.is_file() and not path.is_symlink(), "expected copied object missing: " + str(rel))
                removed[str(rel)] = prep.file_record(path)
                path.unlink()
        write_json(self.logs / "forced-rebuild.json", removed)
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
                   "upstream_commit": UPSTREAM, "baseline_inputs_archive_sha256": BASE_INPUTS_ARCHIVE, "active_patch_count": 72,
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
                     "inputs_archive": {"path": inputs_archive.name, "sha256": sha(inputs_archive)}, "active_patch_count": 72,
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
    parser.add_argument("phase", choices=("plan", "prepare-base", "prepare", "build", "package"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run", default=DEFAULT_RUN, help="workspace-relative fresh directory")
    parser.add_argument("--review", help="workspace-relative primary review manifest")
    parser.add_argument("--review-sha256")
    parser.add_argument("--jobs", type=int, default=6)
    args = parser.parse_args()
    work = Native(args.workspace, args.run, args.review, args.review_sha256)
    if args.phase == "plan":
        result = work.plan()
    else:
        action, previous = {"prepare-base": (work.copy_base, None), "prepare": (work.prepare, "prepare-base"),
                            "build": (lambda *a: work.build(*a, args.jobs), "prepare"),
                            "package": (work.package, "build")}[args.phase]
        result = work.phase(args.phase, previous, action)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
