#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a prepared private native candidate after an exact source audit."""

import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
import prepare_native as prep


def config_values(path):
    result = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"(CONFIG_[A-Za-z0-9_]+)=(.*)", line)
        if match:
            result[match[1]] = match[2]
        else:
            match = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
            if match:
                result[match[1]] = "n"
    return result


def validate_inputs(candidate, logs, receipt):
    prep.require(prep.capture(["/usr/bin/git", "-C", candidate, "rev-parse", "HEAD"]).strip() == prep.PROJECT_BASE, "project base changed")
    source = candidate / "src" / prep.TARGET
    prep.require(prep.capture(["/usr/bin/git", "-C", source, "rev-parse", "HEAD"]).strip() == prep.UPSTREAM, "upstream base changed")
    frozen = candidate / "private-build/ready-policy"
    for name, expected in receipt["input_sha256"].items():
        prep.require(prep.sha(frozen / name) == expected, f"frozen input changed: {name}")
    # Use exactly the scripts recorded by preparation, not a later edited copy.
    prep.require(prep.sha(Path(__file__)) == receipt["input_sha256"]["build_native.py"], "running builder differs from frozen builder")
    prep.require(prep.sha(Path(prep.__file__)) == receipt["input_sha256"]["prepare_native.py"], "running preparation library differs from frozen library")
    values = prep.env_file(candidate / "targets" / f"{prep.TARGET}.env")
    prep.require(values["KERNEL_RELEASE"] == prep.RELEASE, "release contract changed")
    prep.require(prep.sha(candidate / "targets" / f"{prep.TARGET}.env") == receipt["target_sha256"], "target input changed")
    prep.require(prep.sha(candidate / values["CONFIG_FILE"]) == receipt["config_input_sha256"], "config input changed")
    prep.require(prep.sha(candidate / values["SERIES_FILE"]) == receipt["series_sha256"], "series input changed")
    prep.require(len(receipt["patches"]) == 68, "68-patch identity required")
    for record in receipt["patches"]:
        prep.require(prep.sha(candidate / values["PATCHES_DIR"] / record["entry"]) == record["sha256"], f"patch changed: {record['entry']}")
    for name, record in receipt["source_files"].items():
        prep.require(prep.file_record(source / name) == record, f"prepared source changed: {name}")
    prep.require(prep.sha(logs / "host-native/receipt.json") == receipt["host_receipt_sha256"], "native fixture receipt changed")
    return values


def run_build(workspace, audit_sha, jobs):
    baseline, candidate, logs = prep.paths(workspace)
    prep.require(platform.system() == "Linux" and platform.machine() == "x86_64", "native Linux x86-64 required")
    prep.require(not os.environ.get("CROSS_COMPILE"), "CROSS_COMPILE must be unset")
    receipt = json.loads((logs / "prepared.json").read_text())
    prep.require(audit_sha == receipt["patch_sha256"], "source audit does not match prepared patch")
    prep.require(jobs in range(1, 7), "use at most six jobs on the recorded 14GiB host")
    output = candidate / "output" / prep.TARGET
    prep.require(not output.exists(), "existing output retained; a new build attempt needs explicit review")
    claim = logs / "build-started.json"
    with claim.open("x") as stream:
        json.dump({"pid": os.getpid(), "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "jobs": jobs, "audit_patch_sha256": audit_sha}, stream, indent=2)
        stream.write("\n")
    before = json.loads((logs / "baseline-before.json").read_text())
    prep.require(prep.sha(logs / "baseline-before.json") == receipt["baseline_snapshot_sha256"], "baseline receipt changed")
    prep.verify_baseline(baseline, before, logs / "baseline-before-build.json")
    values = validate_inputs(candidate, logs, receipt)
    inventory = json.loads((logs / "prebuild-inputs.json").read_text())
    prep.require(prep.input_inventory(candidate) == inventory, "final project input inventory changed")
    source = candidate / "src" / prep.TARGET
    old_source = baseline / "src" / prep.OLD_TARGET
    frozen = candidate / "private-build/ready-policy"
    for name in prep.OVERLAY:
        prep.require(prep.file_record(candidate / name) == before["project_inputs"][name], f"copied dev4 overlay changed: {name}")
    # Environment is explicit: no mise activation or trust changes.
    prep.ENV.update({
        "SOURCE_DATE_EPOCH": values["SOURCE_DATE_EPOCH"],
        "KBUILD_BUILD_TIMESTAMP": values["KBUILD_RELEASE_TIMESTAMP"],
        "KBUILD_BUILD_USER": values["KBUILD_RELEASE_USER"],
        "KBUILD_BUILD_HOST": values["KBUILD_RELEASE_HOST"],
        "KBUILD_BUILD_VERSION": values["KBUILD_RELEASE_VERSION"],
    })
    make = ["/usr/bin/make", "ARCH=x86_64", "LOCALVERSION="]
    outcome = {"status": "FAILED_OR_INTERRUPTED", "target": prep.TARGET, "output": str(output), "jobs": jobs, "source_audit_patch_sha256": audit_sha}
    try:
        prep.command(["/usr/bin/python3", frozen / "check.py", "--baseline", frozen / "baseline-ready-dev4.c",
                      "--source", source / prep.READY_C, "--headers", source / "drivers/gpu/drm/amd/include/asic_reg",
                      "--report", logs / "host-native-prebuild"])
        shutil.copy2(candidate / values["CONFIG_FILE"], source / ".config")
        for fragment in values.get("CONFIG_FRAGMENTS", "").split():
            prep.command(["/usr/bin/bash", source / "scripts/kconfig/merge_config.sh", "-m", ".config", candidate / fragment], cwd=source)
        prep.command([*make, "olddefconfig"], cwd=source)
        prep.command([*make, "syncconfig"], cwd=source)
        actual_config = config_values(source / ".config")
        previous_config = config_values(old_source / ".config")
        previous_config["CONFIG_LOCALVERSION"] = '"-psx-ps4-baikal-uvd-poll-dev1"'
        differences = {name: [previous_config.get(name), actual_config.get(name)] for name in previous_config.keys() | actual_config.keys() if previous_config.get(name) != actual_config.get(name)}
        prep.write_json(logs / "resolved-config-differences.json", differences)
        prep.require(not differences, "resolved config changed beyond LOCALVERSION")
        prep.require(actual_config.get("CONFIG_EXTRA_FIRMWARE") == '""', "resolved config embeds firmware")
        prep.require(actual_config.get("CONFIG_INITRAMFS_SOURCE", '""') == '""', "unexpected embedded initramfs input")
        version = prep.capture([*make, "-s", "kernelrelease"], cwd=source).strip()
        prep.require(version == prep.RELEASE, f"unexpected release: {version}")
        print(f"Building {version} with {jobs} native jobs", flush=True)
        prep.command([*make, f"-j{jobs}", "bzImage", "modules"], cwd=source)
        prep.require(prep.capture([*make, "-s", "kernelrelease"], cwd=source).strip() == version, "release changed during build")
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
                prep.require(not link.exists(), f"unexpected non-symlink module {name}")
        modules = sorted(module_root.rglob("*.ko"))
        prep.require(len(modules) == 41, f"expected complete 41 modules, got {len(modules)}")
        module_records = {}
        for module in modules:
            vermagic = prep.capture(["/usr/bin/modinfo", "-F", "vermagic", module]).strip()
            prep.require(vermagic.split()[0] == version, f"wrong module release: {module}")
            module_records[module.relative_to(staging).as_posix()] = {"sha256": prep.sha(module), "vermagic": vermagic}
        archive = f"modules-{version}.tar.zst"
        prep.command(["/usr/bin/tar", "--sort=name", f"--mtime=@{values['SOURCE_DATE_EPOCH']}", "--owner=0", "--group=0", "--numeric-owner", "--zstd", "-C", staging, "-cf", output / archive, "lib"])
        patchset = "".join(f"{record['sha256']}  {record['entry']}\n" for record in receipt["patches"])
        patchset_path = logs / "ordered-patchset.sha256"
        patchset_path.write_text(patchset)
        source_contract = logs / "candidate-source-hashes.json"
        post_source = {name: prep.file_record(source / name) for name in receipt["source_files"]}
        for name, record in receipt["source_files"].items():
            if name not in (".config", "include/config/kernel.release"):
                prep.require(post_source[name] == record, f"source file changed while building: {name}")
        prep.write_json(source_contract, post_source)
        compiler = prep.capture(["/usr/bin/gcc", "--version"]).splitlines()[0]
        manifest = {
            "target": prep.TARGET, "platform": values["PLATFORM"], "kernel_release": version,
            "base_repo": values["BASE_REPO"], "base_ref": values["BASE_REF"], "base_commit": prep.UPSTREAM,
            "project_base_commit": prep.PROJECT_BASE, "project_overlay": receipt["project_overlay"], "project_clean": "false",
            "project_identity": receipt["project_identity"], "project_input_count": inventory["input_count"],
            "prebuild_inputs_sha256": prep.sha(logs / "prebuild-inputs.json"),
            "touched_source_hashes_sha256": prep.sha(source_contract), "prepared_receipt_sha256": prep.sha(logs / "prepared.json"),
            "series_sha256": receipt["series_sha256"], "patchset_sha256": prep.sha(patchset_path),
            "policy_patch_sha256": receipt["patch_sha256"], "config_sha256": prep.sha(output / "config"),
            "active_patch_count": 68, "module_count": len(modules), "compiler": compiler,
            "builder_image": "native-linux", "builder_image_id": "not-applicable",
            "source_date_epoch": values["SOURCE_DATE_EPOCH"],
            "kbuild_identity": values["KBUILD_RELEASE_USER"] + "@" + values["KBUILD_RELEASE_HOST"],
            "kbuild_version": values["KBUILD_RELEASE_VERSION"], "embedded_firmware": "false",
            "hardware_acceptance": "none", "publication": "private-uncommitted-candidate",
        }
        (output / "manifest.txt").write_text("".join(f"{key}={value}\n" for key, value in manifest.items()))
        artifact_names = ["bzImage", "config", "System.map", archive, "manifest.txt", "version.txt"]
        (output / "SHA256SUMS").write_text("".join(f"{prep.sha(output / name)}  {name}\n" for name in artifact_names))
        prep.command(["/usr/bin/sha256sum", "-c", "SHA256SUMS"], cwd=output)
        prep.write_json(logs / "modules.json", module_records)
        prep.write_json(logs / "source-independence-after-build.json", prep.inspect_source_independence(old_source, source))
        outcome.update({"status": "PASS_NATIVE_BUILD_NO_HARDWARE_ACCEPTANCE", "release": version, "module_count": len(modules),
                        "artifact_sha256": {name: prep.sha(output / name) for name in [*artifact_names, "SHA256SUMS"]},
                        "compiler": compiler, "ready_source_sha256": prep.sha(source / prep.READY_C)})
        print(f"PASS: {version}; complete {len(modules)} matching modules; artifacts {output}", flush=True)
    except BaseException as error:
        outcome["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        try:
            prep.verify_baseline(baseline, before, logs / "baseline-after-build.json")
            outcome["baseline_preserved"] = True
        except BaseException as error:
            outcome["baseline_preserved"] = False
            outcome["baseline_error"] = str(error)
            outcome["status"] = "FAIL_BASELINE_CHANGED"
            raise
        finally:
            outcome["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            prep.write_json(logs / "build-receipt.json", outcome)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--source-audit-sha256", required=True, help="exact patch digest approved by the source audit")
    parser.add_argument("--jobs", type=int, default=6)
    arguments = parser.parse_args()
    run_build(arguments.workspace.resolve(), arguments.source_audit_sha256, arguments.jobs)


if __name__ == "__main__":
    main()
