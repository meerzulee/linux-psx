#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build the reviewed private token candidate natively, with at most six jobs."""

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sys

sys.dont_write_bytecode = True
import prepare_native as prep

MODULE_COUNT = 41


def module_elf_header(path):
    with path.open("rb") as stream:
        header = stream.read(64)
    prep.require(len(header) == 64 and header[:4] == b"\x7fELF", f"missing ELF64 header: {path}")
    result = {"class": header[4], "data": header[5],
              "type": int.from_bytes(header[16:18], "little"),
              "machine": int.from_bytes(header[18:20], "little")}
    prep.require(result == {"class": 2, "data": 1, "type": 1, "machine": 62},
                 f"module must be little-endian x86-64 ET_REL: {path}")
    return result


def config_values(path):
    values = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"(CONFIG_[A-Za-z0-9_]+)=(.*)", line)
        if match:
            values[match[1]] = match[2]
        else:
            match = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
            if match:
                values[match[1]] = "n"
    return values


def validate_config(previous_path, actual_path):
    previous = config_values(previous_path)
    actual = config_values(actual_path)
    previous["CONFIG_LOCALVERSION"] = '"-psx-ps4-baikal-uvd-token-dev2"'
    differences = {name: [previous.get(name), actual.get(name)]
                   for name in previous.keys() | actual.keys() if previous.get(name) != actual.get(name)}
    prep.require(not differences, f"resolved config changed beyond LOCALVERSION: {differences}")
    prep.require(actual.get("CONFIG_EXTRA_FIRMWARE") == '""', "resolved config embeds firmware")
    prep.require(actual.get("CONFIG_INITRAMFS_SOURCE", '""') == '""', "embedded initramfs forbidden")
    return differences


def validate_inputs(candidate, logs, receipt, built=False):
    prep.require(receipt["status"] == "PREPARED_HOST_CHECKED_NOT_BUILT", "preparation did not pass")
    prep.require(receipt["target"] == prep.TARGET and receipt["release"] == prep.RELEASE, "wrong candidate identity")
    prep.require(receipt["project_identity"] == prep.PROJECT_BASE + "+" + prep.PROJECT_OVERLAY, "wrong project identity")
    prep.require(prep.capture(["/usr/bin/git", "-C", candidate, "rev-parse", "HEAD"]).strip() == prep.PROJECT_BASE, "project base changed")
    source = candidate / "src" / prep.TARGET
    prep.require(prep.capture(["/usr/bin/git", "-C", source, "rev-parse", "HEAD"]).strip() == prep.UPSTREAM, "source base changed")
    frozen = candidate / prep.PRIVATE
    for name, expected in receipt["input_sha256"].items():
        prep.require(prep.sha(prep.contained(frozen, name)) == expected, f"frozen input changed: {name}")
    prep.require(prep.sha(Path(__file__)) == receipt["input_sha256"]["build_native.py"], "running builder differs from frozen builder")
    prep.require(prep.sha(Path(prep.__file__)) == receipt["input_sha256"]["prepare_native.py"], "running preparation library differs from frozen library")
    values = prep.env_file(candidate / "targets" / f"{prep.TARGET}.env")
    prep.require(values["TARGET_NAME"] == prep.TARGET and values["KERNEL_RELEASE"] == prep.RELEASE and values["BASE_COMMIT"] == prep.UPSTREAM, "target identity changed")
    for path, key in ((candidate / "targets" / f"{prep.TARGET}.env", "target_sha256"),
                      (prep.contained(candidate, values["CONFIG_FILE"]), "config_input_sha256"),
                      (prep.contained(candidate, values["SERIES_FILE"]), "series_sha256")):
        prep.require(prep.sha(path) == receipt[key], f"build input changed: {path}")
    patches = prep.patch_records(candidate, prep.TARGET)
    prep.require(len(patches) == prep.PATCH_COUNT and patches == receipt["patches"], "ordered patch identity changed")
    prep.require(patches[-1]["sha256"] == receipt["token_patch_sha256"] == receipt["patch_sha256"], "token patch identity changed")
    prep.require(receipt["policy_patch_sha256"] == prep.POLICY_PATCH_SHA, "inherited policy identity changed")
    for name, key in (("baseline-before.json", "baseline_snapshot_sha256"),
                      ("baseline-inputs.json", "baseline_inputs_sha256"),
                      ("prepared-source-inputs.json", "source_inventory_sha256"),
                      ("host-native/receipt.json", "host_receipt_sha256")):
        prep.require(prep.sha(logs / name) == receipt[key], f"preparation evidence changed: {name}")
    expected = json.loads((logs / "prepared-source-inputs.json").read_text())
    current = prep.source_inventory(candidate, prep.TARGET)
    prep.require(current.keys() == expected.keys(), "source path selection changed")
    for name in expected:
        if not (built and name == ".config"):
            prep.require(current[name] == expected[name], f"prepared source changed: {name}")
    inventory = json.loads((logs / "prebuild-inputs.json").read_text())
    prep.require(prep.input_inventory(candidate) == inventory, "final project input inventory changed")
    prep.require(inventory["input_count"] == receipt["inputs_file_count"], "input count mismatch")
    for row in json.loads((logs / "baseline-inputs.json").read_text())["files"]:
        prep.require(prep.file_record(candidate / row["path"]) == prep.normalize_record(row), f"frozen poll input changed: {row['path']}")
    return values, inventory


def validate_artifacts(output, built):
    prep.require(built["status"] == "PASS_NATIVE_BUILD_NO_HARDWARE_ACCEPTANCE" and built["baseline_preserved"], "build did not pass")
    prep.require(built["target"] == prep.TARGET and built["release"] == prep.RELEASE, "wrong build target")
    for name, expected in built["artifact_sha256"].items():
        prep.require(prep.sha(prep.contained(output, name)) == expected, f"built artifact changed: {name}")


def run_build(workspace, audit_sha, jobs):
    prep.native_host()
    prep.require(jobs in range(1, 7), "native build permits one through six jobs")
    baseline, candidate, logs = prep.paths(workspace)
    receipt = json.loads((logs / "prepared.json").read_text())
    prep.require(audit_sha == receipt["token_patch_sha256"], "source review does not match the prepared token patch")
    output = candidate / "output" / prep.TARGET
    prep.require(not output.exists() and not output.is_symlink(), "existing output retained; no implicit retry")
    prep.require(not (logs / "build-started.json").exists(), "existing build attempt retained; no implicit retry")
    values, inventory = validate_inputs(candidate, logs, receipt)
    before = json.loads((logs / "baseline-before.json").read_text())
    prep.verify_baseline(baseline, before, logs / "baseline-before-build.json")
    prep.write_json(logs / "independence-before-build.json", prep.inspect_independence(before, candidate))
    prep.write_json(logs / "build-started.json", {"pid": os.getpid(), "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "jobs": jobs, "reviewed_token_patch_sha256": audit_sha})
    source = candidate / "src" / prep.TARGET
    old_source = baseline / "src" / prep.OLD_TARGET
    prep.ENV.update({"SOURCE_DATE_EPOCH": values["SOURCE_DATE_EPOCH"],
                     "KBUILD_BUILD_TIMESTAMP": values["KBUILD_RELEASE_TIMESTAMP"],
                     "KBUILD_BUILD_USER": values["KBUILD_RELEASE_USER"],
                     "KBUILD_BUILD_HOST": values["KBUILD_RELEASE_HOST"],
                     "KBUILD_BUILD_VERSION": values["KBUILD_RELEASE_VERSION"]})
    make = ["/usr/bin/make", "ARCH=x86_64", "LOCALVERSION="]
    outcome = {"status": "FAILED_OR_INTERRUPTED", "target": prep.TARGET, "release": prep.RELEASE,
               "output": str(output), "jobs": jobs, "project_identity": receipt["project_identity"],
               "active_patch_count": prep.PATCH_COUNT, "inputs_file_count": inventory["input_count"],
               "frozen_baseline_input_count": prep.BASE_INPUT_COUNT,
               "uncommitted_overlay_files": inventory["untracked_overlay_files"],
               "token_patch_sha256": audit_sha, "policy_patch_sha256": prep.POLICY_PATCH_SHA}
    try:
        prep.host_check(candidate, source, logs / "host-native-prebuild")
        shutil.copy2(candidate / values["CONFIG_FILE"], source / ".config")
        for fragment in values.get("CONFIG_FRAGMENTS", "").split():
            prep.command(["/usr/bin/bash", source / "scripts/kconfig/merge_config.sh", "-m", ".config", prep.contained(candidate, fragment)], cwd=source)
        prep.command([*make, "olddefconfig"], cwd=source)
        prep.command([*make, "syncconfig"], cwd=source)
        differences = validate_config(old_source / ".config", source / ".config")
        prep.write_json(logs / "resolved-config-differences.json", differences)
        version = prep.capture([*make, "-s", "kernelrelease"], cwd=source).strip()
        prep.require(version == prep.RELEASE, f"unexpected kernel release: {version}")
        # Force new native objects for both changed translation units. Only the
        # proven-independent candidate cache is touched; the baseline is frozen.
        removed = {}
        objects = [name[:-2] + ".o" for name in (prep.CANARY_C, prep.READY_C)]
        for name in objects:
            for relative in (name, str(Path(name).with_name("." + Path(name).name + ".cmd"))):
                path = prep.contained(source, relative)
                prep.require(path.is_file() and not path.is_symlink(), f"expected copied object/cache: {relative}")
                removed[relative] = prep.file_record(path)
                path.unlink()
        prep.write_json(logs / "forced-rebuild-cached-files.json", removed)
        prep.command([*make, f"-j{jobs}", "bzImage", "modules"], cwd=source)
        prep.require(prep.capture([*make, "-s", "kernelrelease"], cwd=source).strip() == version, "release changed during build")
        object_records = {name: prep.file_record(source / name) for name in removed}
        prep.write_json(logs / "rebuilt-objects.json", object_records)
        output.mkdir(parents=True)
        for original, name in ((source / "arch/x86/boot/bzImage", "bzImage"), (source / ".config", "config"), (source / "System.map", "System.map")):
            shutil.copy2(original, output / name)
        (output / "version.txt").write_text(version + "\n")
        staging = output / "modules"
        prep.command([*make, f"INSTALL_MOD_PATH={staging}", "INSTALL_MOD_STRIP=1", "modules_install"], cwd=source)
        module_root = staging / "lib/modules" / version
        for name in ("build", "source"):
            link = module_root / name
            if link.is_symlink():
                link.unlink()
            else:
                prep.require(not link.exists(), f"unexpected module-tree {name}")
        regular = []
        for path in prep.tree_paths(staging):
            prep.require(not path.is_symlink(), f"module archive symlink forbidden: {path}")
            if not path.is_dir():
                prep.require(path.is_file(), f"special module file: {path}")
                regular.append(path)
        modules = sorted(module_root.rglob("*.ko"))
        prep.require(len(modules) == MODULE_COUNT, f"expected complete {MODULE_COUNT} modules, found {len(modules)}")
        module_records = {}
        for module in modules:
            vermagic = prep.capture(["/usr/bin/modinfo", "-F", "vermagic", module]).strip()
            prep.require(vermagic.split()[0] == version, f"wrong module release: {module}")
            module_records[module.relative_to(staging).as_posix()] = {
                **prep.file_record(module), "vermagic": vermagic, "elf_header": module_elf_header(module)}
        prep.write_json(logs / "modules.json", module_records)
        prep.write_json(logs / "module-tree.json", {p.relative_to(staging).as_posix(): prep.file_record(p) for p in sorted(regular)})
        archive = f"modules-{version}.tar.zst"
        prep.command(["/usr/bin/tar", "--sort=name", f"--mtime=@{values['SOURCE_DATE_EPOCH']}", "--owner=0", "--group=0", "--numeric-owner", "--zstd", "-C", staging, "-cf", output / archive, "lib"])
        # Validate all source bytes after compilation, except the measured
        # resolved .config; include every ignored addition from all 69 patches.
        validate_inputs(candidate, logs, receipt, built=True)
        prep.write_json(logs / "built-source-inputs.json", prep.source_inventory(candidate, prep.TARGET))
        compiler = prep.capture(["/usr/bin/gcc", "--version"]).splitlines()[0]
        patchset_path = logs / "ordered-patchset.sha256"
        with patchset_path.open("x") as stream:
            stream.write("".join(f"{row['sha256']}  {row['entry']}\n" for row in receipt["patches"]))
        manifest = {
            "target": prep.TARGET, "platform": values["PLATFORM"], "kernel_release": version,
            "base_repo": values["BASE_REPO"], "base_ref": values["BASE_REF"], "base_commit": prep.UPSTREAM,
            "project_base_commit": prep.PROJECT_BASE, "project_identity": receipt["project_identity"], "project_clean": "false",
            "project_input_count": inventory["input_count"], "frozen_baseline_input_count": prep.BASE_INPUT_COUNT,
            "prebuild_inputs_sha256": prep.sha(logs / "prebuild-inputs.json"),
            "complete_source_hashes_sha256": prep.sha(logs / "built-source-inputs.json"),
            "prepared_receipt_sha256": prep.sha(logs / "prepared.json"),
            "series_sha256": receipt["series_sha256"], "patchset_sha256": prep.sha(patchset_path),
            "token_patch_sha256": audit_sha, "policy_patch_sha256": prep.POLICY_PATCH_SHA,
            "config_sha256": prep.sha(output / "config"), "active_patch_count": prep.PATCH_COUNT,
            "module_count": len(modules), "archive_regular_files": len(regular), "compiler": compiler,
            "builder_image": "native-linux", "builder_image_id": "not-applicable", "native_jobs": jobs,
            "source_date_epoch": values["SOURCE_DATE_EPOCH"],
            "kbuild_identity": values["KBUILD_RELEASE_USER"] + "@" + values["KBUILD_RELEASE_HOST"],
            "kbuild_version": values["KBUILD_RELEASE_VERSION"], "embedded_firmware": "false",
            "hardware_acceptance": "none", "publication": "private-uncommitted-candidate",
        }
        (output / "manifest.txt").write_text("".join(f"{key}={value}\n" for key, value in manifest.items()))
        names = ["bzImage", "config", "System.map", archive, "manifest.txt", "version.txt"]
        (output / "SHA256SUMS").write_text("".join(f"{prep.sha(output / name)}  {name}\n" for name in names))
        prep.command(["/usr/bin/sha256sum", "-c", "SHA256SUMS"], cwd=output)
        prep.write_json(logs / "independence-after-build.json", prep.inspect_independence(before, candidate))
        outcome.update({"status": "PASS_NATIVE_BUILD_NO_HARDWARE_ACCEPTANCE", "module_count": len(modules),
                        "archive_regular_files": len(regular), "artifact_sha256": {name: prep.sha(output / name) for name in [*names, "SHA256SUMS"]},
                        "compiler": compiler, "ready_source_sha256": prep.sha(source / prep.READY_C),
                        "rebuilt_objects_sha256": prep.sha(logs / "rebuilt-objects.json"),
                        "vmlinux_sha256": prep.sha(source / "vmlinux"),
                        "resolved_config_sha256": prep.sha(source / ".config"),
                        "source_inventory_sha256": prep.sha(logs / "built-source-inputs.json")})
        print(f"PASS native build only: {version}; {len(modules)} matching modules; {output}", flush=True)
    except BaseException as error:
        outcome["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        try:
            prep.verify_baseline(baseline, before, logs / "baseline-after-build.json")
            outcome["baseline_preserved"] = True
        except BaseException as error:
            outcome.update(status="FAIL_BASELINE_CHANGED", baseline_preserved=False, baseline_error=str(error))
            raise
        finally:
            outcome["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            prep.write_json(logs / "build-receipt.json", outcome)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--source-audit-sha256", required=True, help="exact token patch SHA256 accepted by source review")
    parser.add_argument("--jobs", type=int, default=6)
    arguments = parser.parse_args()
    run_build(arguments.workspace.resolve(), arguments.source_audit_sha256, arguments.jobs)


if __name__ == "__main__":
    main()
