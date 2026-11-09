#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""One explicit alpha AVC attempt; never initialize, reset, unload or rearm UVD.

The immutable playback harness owns the forced-VAAPI decoder and exact NV12
oracle. This adapter verifies its inputs, requires an operator's active UART
session assertion, and retains an immutable per-boot attempt/stop intent outside
the bounded report listing. UVD is the final active test of an alpha boot, even
when its oracle passes. An assertion is not authenticated UART continuity.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import types
import uuid
import zlib


ALPHA = {
    "schema_version": 1,
    "profile": "ps4-baikal-diagnostics-uvd-alpha1",
    "kernel_release": "7.2.11-psx-ps4-baikal-uvd-runtime-dev1",
    "kernel_sha256": "9c3ec7d27fdfdebfee137684a635301e8dfa259b50e2f5d98ee33cf5f91e6153",
    "config_sha256": "e97f456d3bd55f7c0b28948c1cc09a395ce9e17383512e844c6415f744d030a0",
    "modules_sha256": "38767e5450dc4a136f07003d36edc71cf09f512d6b238255f8edf2fc1ce43987",
    "source_commit": "35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2",
    "module_count": 41,
    "root_format": "ext4-ro-loop",
    "image_version": "alpha1",
    "snapshot_sums_sha256": "6b98da612b59e63b8548393cf0df8c7d9a572a4cad1f851aec930eb594e9cd57",
    "mesa_archive_sha256": "5c397d86a769ed55dd35798c32bfcedea5c4e3ce6eee5147dd282a04c9385fe9",
    "mesa_manifest_sha256": "687af5a366d4bba5af1f72cf5b1a57c4e09d1cfef58257a898ee34da2c7e3964",
    "uvd_runtime": True,
    "hardware_acceptance": False,
}
# Complete native3 + UVD dev2 artifacts, frozen after all module/source
# readbacks. Unknown or pending identities cannot pass select_contract.
ALPHA2 = dict(ALPHA, profile="ps4-baikal-diagnostics-uvd-alpha2",
              kernel_release="7.2.11-psx-ps4-baikal-uvd-runtime-dev2",
              kernel_sha256='6b1d9d90d29eeffb58057cd04a387ad08c53adc7d13afb62bcb2b3af8c686fdc',
              config_sha256='51c008bb764dc1cbcc7fb81bf8c164ee23447feb5844c596c53e040bb49df4df',
              modules_sha256='a900cf6e72dafbcf5b520c5b83471d6f78ad75eba644178541ad441fce62aef0',
              source_commit="c8495a51e0bad4851015cd5d34ee61b2fde87e44",
              image_version="alpha2", build_status="frozen", product_name="Diagnostix",
              poweroff_fix={
                  "patch": "platforms/ps4/patches/7.2-ps4-baikal/0200-ps4-drivers/0001-ps4-bpcie-icc-power-off-before-acpi.patch",
                  "sha256": "cb01d71ad7e12144ee7f014fe5385e37f7e239b2f2f3ee065b5ec7e0a0de219a",
                  "contributor": "suds-mcduff",
                  "original_pr": "https://github.com/meerzulee/linux-psx/pull/4",
                  "maintainer_guards": "Reject forced shutdown and pending ICC replies before changing shared state.",
                  "uvd_shutdown_park_unchanged": True,
                  "hardware_acceptance": False,
              })
ALPHA3 = dict(ALPHA2, profile="ps4-baikal-diagnostics-uvd-alpha3", image_version="alpha3")
POWEROFF_EXPERIMENT = {"parameter": "amdgpu.ps4_uvd_poff", "default": 0, "test_value": 1,
    "read_only": True, "system_power_off_only": True, "retain_dma": True,
    "reboot_suspend_remove_reset_guards_unchanged": True, "hardware_acceptance": False}
# Matched dev3 package independently read back after the scoped power-off build.
# This does not relax the immutable post-attempt stop or add a power command.
ALPHA4 = dict(ALPHA3, profile="ps4-baikal-diagnostics-uvd-alpha4", image_version="alpha4",
              kernel_release="7.2.11-psx-ps4-baikal-uvd-runtime-dev3",
              kernel_sha256="01fa05f683c4c36ae83678f09cf3df502ddb0731dc73f2530cdb05fbe317804d",
              config_sha256="e4ae5561870aa34c9eafad0dcb9de3eaf77c4a8248237134d7dfcd0e76aa93fe",
              modules_sha256="603a4b9453fba5462c702102afab335a857d01d707cf945f7a8809ec5640396c",
              build_status="frozen", console_loglevel=7,
              poweroff_fix=dict(ALPHA3["poweroff_fix"], uvd_shutdown_park_unchanged=False),
              uvd_lifecycle_sources_unchanged=False, uvd_poweroff_experiment=POWEROFF_EXPERIMENT)
# Dev4 changes only the scoped AHCI completion probe and release identity.
# The experimental UVD and power-off source bytes are unchanged from dev3.
ALPHA5 = dict(ALPHA4, profile="ps4-baikal-diagnostics-uvd-alpha5", image_version="alpha5",
              kernel_release="7.2.11-psx-ps4-baikal-uvd-runtime-dev4",
              kernel_sha256="9b3842def11f0638601f60ab7659eb8fa9c4c7e5cb81bab64d25f301ad826d37",
              config_sha256="50676ba45e8535e1f7a3fd9f8eeab73fa7f841e255c1ccf846fda652d499dba0",
              modules_sha256="fcda424b74177ec2316e58187d1c1c86e6e521782094cad77a6cdcb8fdc32dcc",
              uvd_sources_unchanged_from_dev3=True, sata_policy="read-only-scoped-probe")
RESTART_EXPERIMENT = {"parameter": "amdgpu.ps4_uvd_poff", "default": 0, "test_value": 1,
    "read_only": True, "system_restart_only": True, "started_runtime_required": True,
    "initialized_open_jobs_required": True, "healthy_runtime_required": True,
    "first_close_required": True, "retain_dma": True, "forced_restart_refused": True,
    "kexec_refused": True, "poweroff_semantics_unchanged": True,
    "suspend_remove_reset_guards_unchanged": True, "hardware_acceptance": False}
RESTART_FIX = {
    "patch_path": "platforms/ps4/patches/7.2-ps4-baikal/0200-ps4-drivers/0004-ps4-bpcie-icc-atomic-restart.patch",
    "patch_sha256": "205fa678d28315ac813e870aeb19add85d643d6f79cb0180f42dd5530802f40f",
    "atomic_registered_restart": True, "legacy_baikal_fallback_disabled": True,
    "hardware_acceptance": False}
# Complete dev5 kernel/modules/source package independently verified and frozen.
# This identity has source/build evidence, not hardware power acceptance.
ALPHA6 = dict(ALPHA5, profile="ps4-baikal-diagnostics-uvd-alpha6", image_version="alpha6",
              kernel_release="7.2.11-psx-ps4-baikal-uvd-runtime-dev5",
              kernel_sha256='4b628855f41d5740609607fdac528f189ba3c056e31791ab69da746c51f3c6f8', config_sha256='0b62a6638da83e415aab58e2164a35aa1aebca60b77a6763812abc230e12bfb2', modules_sha256='5dcd3e005999df31066870cafab2d8fe6f064fec8b8f3029c64fd162148153cc',
              uvd_sources_unchanged_from_dev3=False,
              uvd_poweroff_experiment=dict(POWEROFF_EXPERIMENT,
                  reboot_suspend_remove_reset_guards_unchanged=False),
              inherited_uvd_poweroff_experiment=POWEROFF_EXPERIMENT,
              uvd_restart_experiment=RESTART_EXPERIMENT, restart_fix=RESTART_FIX,
              uvd_poweroff_semantics_unchanged_from_dev4=True,
              uvd_restart_guards_unchanged_from_dev4=False)
TERMINAL_RESTART_EXPERIMENT = dict(RESTART_EXPERIMENT,
    healthy_runtime_required=False, initialized_open_jobs_required=False, first_close_required=False,
    initialized_jobs_required=True, post_start_failure_permitted=True, startup_hw_init_completed_required=True)
TERMINAL_RESTART_FIX = {
    "patch_path": "experiments/ps4-uvd-driver/runtime/terminal-restart.patch",
    "patch_sha256": "75aa87d67122c5208bfae293c50aa134382138f4ca7a23aa8f0bd609ce5f2324", "hardware_acceptance": False}
ALPHA7 = dict(ALPHA6, profile="ps4-baikal-diagnostics-uvd-alpha7", image_version="alpha7",
              kernel_release="7.2.11-psx-ps4-baikal-uvd-runtime-dev6",
              kernel_sha256='a7264426ee3b2ffeb030a04bbd7ab070418ecf4c0272b83400b4d5f45473bff4', config_sha256='ee77a55ff46f9d201d71efb0b87adca4cb1f1bf253c301c9cd8da3b5c4a724ff', modules_sha256='0e50c537c2395154cff659d9722d165027d0ecea875dfcb14d32d798f9c9e14d',
              uvd_restart_experiment=TERMINAL_RESTART_EXPERIMENT,
              inherited_dev5_uvd_restart_experiment=RESTART_EXPERIMENT,
              terminal_restart_fix=TERMINAL_RESTART_FIX)

MTS_TX_RECLAIM_EXPERIMENT = {
    "single_change": "omit_word0_clear_on_tx_reclaim", "preserve_returned_word0": True,
    "word2_sentinel_unchanged": True, "ownership_predicate_unchanged": True,
    "producer_assignment_unchanged": True, "retained_buffers": True,
    "polling_and_lifecycle_unchanged": True, "hypothesis_only": True, "hardware_acceptance": False}
MTS_TX_RECLAIM_FIX = {
    "patch_path": "experiments/baikal-mts-tx-reclaim/terminal-reclaim.patch",
    "patch_sha256": "b6fbbc41b295ba259b01e89aa2d7dafdfcc37441b74506cdff30087370400797",
    "hardware_acceptance": False}
# Same verified dev6 kernel/config/ABI; a separate complete module-set identity.
ALPHA8 = dict(ALPHA7, profile="ps4-baikal-diagnostics-uvd-alpha8", image_version="alpha8",
              modules_sha256='fa14256fcebfe28f919f6e0c9efd89f852b4c0657ae5077d44f4c8da8a31a107', variant="mts-tx-reclaim-net1", abi_unchanged=True, kernel_unchanged=True,
              mts_tx_reclaim_experiment=MTS_TX_RECLAIM_EXPERIMENT, mts_tx_reclaim_fix=MTS_TX_RECLAIM_FIX)

DIAGNOSTIC = {
    "fixture": "/usr/share/psx-diagnostics/uvd/FIXTURE.json",
    "fixture_sha256": "8999f750efd20cf71f176efb5b5febb0681c493e23ca1dfb6f0bc7bcf8288626",
    "playback": "/opt/psx-diagnostics/uvd-playback.py",
    "playback_sha256": "5c738a6ae099bad4a15aab94f90128d8cbff34cc92dcd55654f94bf738e03ebd",
    "ffmpeg": "/usr/bin/ffmpeg",
    "ffmpeg_sha256": "2e37495139c8cee4711b7e1475dac11ce54633431c4a025b7b70fa2e6d7ca3c3",
    "driver": "/opt/psx-mesa/usr/lib/dri/radeonsi_drv_video.so",
    "driver_sha256": "16d87fdff0011054eeba34e2dff9067519d8756de788c4970cecf4a7038ce4a3",
    "render_node": "/dev/dri/renderD128",
    "timeout_seconds": 60,
    "maximum_attempts_per_boot": 1,
}
FIRMWARE = {"path": "/usr/lib/firmware/amdgpu/liverpool_uvd.bin", "bytes": 314168,
            "sha256": "62a43ef4814f59fdd6772792e0a9303e016fe2e17ad30b36412d1fa7ce4cd5cb"}
FIRMWARE_CRC = 0xa465ba07
BOOT_ARGUMENTS = ["amdgpu.ps4_uvd_runtime=1", "amdgpu.ps4_uvd_ready=1",
                  "amdgpu.ps4_uvd_poll_state=1"]
POWEROFF_BOOT_ARGUMENTS = BOOT_ARGUMENTS + ["amdgpu.ps4_uvd_poff=1"]
SESSION = "/run/psx-diagnostics/uvd-session.json"
OUTER_TIMEOUT = 120  # 60s decode plus four bounded 10s identity queries and I/O.
KERNEL_FAULT = re.compile(
    r"PS4_UVD_RUNTIME\s+(?:CLOSED|RETAINED)|GPU fault detected|\bVM fault\b|"
    r"GPU reset|ring\s+[^\n]*timeout|ring\s+[^\n]*test failed|IH[^\n]*overflow|"
    r"amdgpu_job_timedout|Kernel panic|Oops:", re.I)
RECOVERY = "No retry/reset/rearm/unload; inspect evidence and use physical power-off and the known-good rollback."
FINISH = "UVD is the final active test on this boot; finish the evidence session, then use physical power-off and the known-good rollback."


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_firmware(data):
    """Mirror the frozen kernel's input-only firmware contract; never execute it."""
    require(len(data) == 314168, "UVD firmware must be the 314168-byte runtime image")
    fields = struct.unpack_from("<IIHHHHIIII", data)
    require(fields[:9] == (314168, 32, 1, 0, 4, 2, 0x012a6509, 313912, 256),
            "UVD firmware header/version/payload contract differs")
    require(fields[9] == FIRMWARE_CRC and zlib.crc32(data[256:]) & 0xffffffff == FIRMWARE_CRC,
            "UVD firmware payload CRC differs")
    require(sha(data) == FIRMWARE["sha256"], "UVD firmware SHA256 differs")
    return {"bytes": len(data), "sha256": sha(data), "header_bytes": 32,
            "header_version": [1, 0], "ip_version": [4, 2], "ucode_version": "0x012a6509",
            "payload_bytes": 313912, "payload_offset": 256, "payload_crc32": f"{FIRMWARE_CRC:08x}"}


def read(path, limit=1024 * 1024, *, private=False, owner=0):
    """Bounded, protected regular files only; never follow a final symlink."""
    require(path.resolve() == path, "UVD evidence/input path contains a symlink")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == owner and
                not info.st_mode & (0o077 if private else 0o022), "UVD file ownership or permissions differ")
        data = stream.read(limit + 1)
    require(len(data) <= limit, "UVD file exceeds its bound")
    return data


def local(runner, name, *, mesa_link=False):
    path = runner.fs.root / str(name).lstrip("/")
    if mesa_link:
        resolved = path.resolve(strict=True)
        require(resolved.is_relative_to(runner.fs.root / "opt/psx-mesa"), "Private Mesa path escaped")
        return resolved
    require(path.resolve() == path, "UVD input path contains a symlink")
    return path


def owner(runner):
    return os.geteuid() if runner.fs.fixture else 0


def document(path, runner, *, private=False):
    value = json.loads(read(path, private=private, owner=owner(runner)))
    require(isinstance(value, dict), "UVD receipt must be an object")
    return value


def marker_path(runner, boot):
    require(isinstance(boot, str) and str(uuid.UUID(boot)) == boot, "UVD needs a canonical boot UUID")
    path = runner.results_dir / "uvd-attempts" / (boot + ".json")
    require(path.resolve() == path, "UVD attempt path contains a symlink")
    return path


def stop_reason(runner):
    """Every current-boot intent permanently closes active work, including PASS.

    This is deliberately independent of completion reports and their retention.
    No fallible completion write can turn the durable intent into permission.
    """
    try:
        boot = runner.identity()["boot_id"]
        path = marker_path(runner, boot)
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        return "A UVD attempt has already been reserved. " + FINISH
    except (OSError, ValueError, TypeError, KeyError):
        return "UVD attempt state cannot be verified. " + RECOVERY


def create_marker(runner, identity, session):
    path = marker_path(runner, identity["boot_id"])
    path.parent.mkdir(mode=0o700, exist_ok=True)
    info = path.parent.stat()
    require(info.st_uid == owner(runner) and not info.st_mode & 0o077, "UVD attempt directory is not private")
    value = {"schema_version": 1, "boot_id": identity["boot_id"],
             "manifest_sha256": identity["manifest_sha256"], "run_id": runner.log.directory.name,
             "phase": "reserved", "status": "INCONCLUSIVE", "stop_required": True,
             "experiment_id": session["experiment_id"], "session_id": session["session_id"]}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True); stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
    sync(path.parent); sync(path.parent.parent)
    return path, value


def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def select_contract(manifest):
    require(isinstance(manifest, dict), "UVD manifest must be an object")
    selected = {ALPHA["profile"]: ALPHA, ALPHA2["profile"]: ALPHA2, ALPHA3["profile"]: ALPHA3,
                ALPHA4["profile"]: ALPHA4, ALPHA5["profile"]: ALPHA5,
                ALPHA6["profile"]: ALPHA6, ALPHA7["profile"]: ALPHA7, ALPHA8["profile"]: ALPHA8}.get(manifest.get("profile"))
    if manifest.get('profile') == 'ps4-baikal-diagnostix-ram-alpha1':
        selected = dict(ALPHA7, profile='ps4-baikal-diagnostix-ram-alpha1', image_version='ram-alpha1')
    require(selected is not None, "Unknown frozen UVD alpha artifact profile")
    for key in ("kernel_sha256", "config_sha256", "modules_sha256"):
        value = selected.get(key)
        require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)
                and len(set(value)) > 1, "UVD alpha artifact pins are pending")
    require(all(type(manifest.get(k)) is type(v) and manifest[k] == v for k, v in selected.items()),
            "This is not the exact frozen UVD alpha artifact profile")
    if selected["image_version"] in ("alpha4", "alpha5", "alpha6", "alpha7", "alpha8", "ram-alpha1"):
        policy = manifest["uvd_poweroff_experiment"]
        expected = selected["uvd_poweroff_experiment"]
        require(all(type(policy.get(key)) is type(value) and policy[key] == value
                    for key, value in expected.items()), "Alpha power-off experiment policy types differ")
    if selected["image_version"] in ("alpha6", "alpha7", "alpha8", "ram-alpha1"):
        for key in ("uvd_restart_experiment", "restart_fix", "inherited_uvd_poweroff_experiment"):
            policy = manifest[key]
            require(all(type(policy.get(name)) is type(value) and policy[name] == value
                        for name, value in selected[key].items()),
                    "Alpha6 retained restart policy types differ")
        if selected["image_version"] in ("alpha7", "alpha8", "ram-alpha1"):
            for key in ("terminal_restart_fix", "inherited_dev5_uvd_restart_experiment"):
                require(all(type(manifest[key].get(name)) is type(value) and manifest[key][name] == value
                            for name, value in selected[key].items()), "Alpha7 terminal policy types differ")
    if selected["image_version"] == "alpha8":
        for key in ("mts_tx_reclaim_experiment", "mts_tx_reclaim_fix"):
            require(all(type(manifest[key].get(name)) is type(value) and manifest[key][name] == value
                        for name, value in selected[key].items()), "Alpha8 MTS policy types differ")
    return selected


def preflight(runner):
    identity = runner.identity()
    require(identity["release_matches"], "Alpha kernel/catalogue release mismatch")
    require(str(uuid.UUID(identity["boot_id"])) == identity["boot_id"], "Invalid boot UUID")
    manifest = document(local(runner, runner.manifest_path), runner)
    selected = select_contract(manifest)
    arguments = POWEROFF_BOOT_ARGUMENTS if selected["image_version"] in ("alpha4", "alpha5", "alpha6", "alpha7", "alpha8", "ram-alpha1") else BOOT_ARGUMENTS
    alpha_inputs = manifest.get("alpha_inputs")
    require(isinstance(alpha_inputs, dict) and manifest.get("uvd_diagnostic") == DIAGNOSTIC and
            manifest.get("required_boot_arguments") == arguments and
            alpha_inputs.get("root_firmware") == FIRMWARE,
            "Alpha UVD inputs/boot policy differ")
    validation = manifest.get("manifest_validation", {})
    ram = selected['image_version'] == 'ram-alpha1'
    if ram:
        import sys
        if '/run/psx-ram' not in sys.path:
            sys.path.insert(0, '/run/psx-ram')
        import ram_state
        ram_state.validate(runner.fs.root, manifest, fixture=runner.fs.fixture)
    require(ram or isinstance(validation, dict) and validation.get("embedded_root_contract_verified") is True and
            validation.get("observed_kernel_release") == identity["kernel_release"] and
            validation.get("observed_media_initramfs_sha256") == manifest.get("initramfs_sha256") and
            all(re.fullmatch(r"[0-9a-f]{64}", str(manifest.get(key, ""))) for key in
                ("initramfs_sha256", "rootfs_sha256")), "Verified root/boot-set contract is missing")
    cmdline = runner.fs.text("/proc/cmdline") or ""
    for argument in arguments:
        key = argument.split("=", 1)[0]
        require([token for token in cmdline.split() if token.split("=", 1)[0] == key] == [argument],
                "Required UVD boot gate is missing or duplicated: " + key)
        require(runner.fs.text("/sys/module/amdgpu/parameters/" + key.split(".")[1]) in ("1", "Y"),
                "Effective UVD module gate differs: " + key)
    session = document(local(runner, SESSION), runner, private=True)
    require(session.get("schema_version") == 1 and session.get("boot_id") == identity["boot_id"] and
            session.get("state") == "active" and
            re.fullmatch(r"EXP-[0-9]{8}-[0-9]{3}", str(session.get("experiment_id", ""))) and
            isinstance(session.get("session_id"), str) and 0 < len(session["session_id"]) <= 256 and
            all(32 <= ord(c) != 127 for c in session["session_id"]), "Active same-boot UART session assertion required")
    session = {key: session[key] for key in ("boot_id", "experiment_id", "session_id", "state")}
    session.update(continuity="unverified", authenticated=False)
    paths = {key: local(runner, DIAGNOSTIC[key], mesa_link=key == "driver")
             for key in ("fixture", "playback", "ffmpeg", "driver")}
    contents = {}
    for key, path in paths.items():
        contents[key] = read(path, 64 * 1024 * 1024, owner=owner(runner))
        require(sha(contents[key]) == DIAGNOSTIC[key + "_sha256"], "Pinned UVD input differs: " + key)
    firmware = read(local(runner, FIRMWARE["path"]), FIRMWARE["bytes"], owner=owner(runner))
    validate_firmware(firmware)
    harness = types.ModuleType("psx_pinned_uvd_playback")
    harness.__file__ = str(paths["playback"])
    exec(compile(contents["playback"], str(paths["playback"]), "exec"), harness.__dict__)
    fixture, _ = harness.verify_fixture(paths["fixture"], DIAGNOSTIC["fixture_sha256"])
    require(runner.fs.tool("python3") and runner.fs.tool("journalctl"), "Python or kernel journal reader is absent")
    require(not marker_path(runner, identity["boot_id"]).exists(), "One UVD attempt is already recorded for this boot; no retry")
    return identity, session, paths, harness, fixture


def kernel_snapshot(runner):
    result = runner.command([runner.fs.tool("journalctl"), "-k", "-b", "--no-pager", "-o", "short-monotonic"], 5,
                            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
    require(result.returncode == 0 and not any((result.timed_out, result.output_limited, result.interrupted)),
            "Current-boot kernel evidence is incomplete")
    # CommandResult.stdout is only the runner's 128 KiB preview. Inspect the full
    # bounded saved journal so a later terminal fault cannot be truncated away.
    saved = runner.log.directory / runner.commands[-1]["stdout"]
    text = read(saved, 1024 * 1024, owner=owner(runner)).decode("utf-8", "replace")
    require(not KERNEL_FAULT.search(text), "Current-boot UVD/GPU terminal evidence requires recovery")
    require(all("PS4_UVD_RUNTIME " + marker in text for marker in ("STARTED", "QUEUE_READY")),
            "Experimental UVD queue is not confirmed ready on this boot")


def decode(runner):
    """Called only by an explicit selected run after controller core checks."""
    try:
        identity, session, paths, harness, fixture = preflight(runner)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"status": "BLOCKED", "message": "UVD preflight blocked: " + str(error), "hardware_acceptance": False}
    try:
        marker, _ = create_marker(runner, identity, session)
    except (OSError, ValueError) as error:
        return {"status": "BLOCKED", "message": "UVD attempt could not be reserved: " + str(error), "hardware_acceptance": False}
    evidence = {"uart": session, "attempt_receipt": str(marker), "decoder_timeout_seconds": 60,
                "harness_timeout_seconds": OUTER_TIMEOUT, "playback_sha256": DIAGNOSTIC["playback_sha256"]}
    result = {"status": "INCONCLUSIVE", "message": "UVD attempt did not establish a complete oracle. " + RECOVERY,
              "stop_required": True, "hardware_acceptance": False, "evidence": evidence}
    try:
        kernel_snapshot(runner)
        output = runner.log.directory / "uvd-reference"
        argv = [runner.fs.tool("python3"), "-I", "-B", str(paths["playback"])]
        for key in ("fixture", "ffmpeg", "driver"):
            # Preserve the selected radeonsi alias; the harness pins its resolved bytes.
            selected = runner.fs.root / DIAGNOSTIC[key].lstrip("/")
            argv += ["--" + key, str(selected), "--" + key + "-sha256", DIAGNOSTIC[key + "_sha256"]]
        argv += ["--render-node", str(runner.fs.root / DIAGNOSTIC["render_node"].lstrip("/")),
                 "--release", identity["kernel_release"], "--boot-id", identity["boot_id"],
                 "--run", "--output", str(output), "--experiment", session["experiment_id"],
                 "--uart-session", session["session_id"], "--timeout", "60"]
        command = runner.command(argv, OUTER_TIMEOUT, env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        evidence["command_returncode"] = command.returncode
        evidence["command_timed_out"] = command.timed_out
        require(not any((command.timed_out, command.output_limited, command.interrupted)) and
                command.returncode in (0, 1), "Decoder process did not finish normally")
        child = document(output / "RESULT.json", runner)
        evidence["playback_status"] = child.get("status")
        if isinstance(child.get("execution"), dict):
            evidence["execution"] = child["execution"]
        require(child.get("hardware_decode_invoked") is True and child.get("inputs_unchanged") is True and
                child.get("runner_sha256") == DIAGNOSTIC["playback_sha256"] and
                child.get("experiment") == session["experiment_id"] and
                child.get("uart_session_assertion") == session["session_id"], "Incomplete playback/input identity receipt")
        before = child.get("preflight", {})
        require(isinstance(before, dict) and before.get("boot_id") == identity["boot_id"] and
                before.get("kernel_release") == identity["kernel_release"] and
                isinstance(before.get("ffmpeg"), dict) and isinstance(before.get("driver"), dict) and
                before["ffmpeg"].get("sha256") == DIAGNOSTIC["ffmpeg_sha256"] and
                before["driver"].get("sha256") == DIAGNOSTIC["driver_sha256"], "Playback identity differs")
        expected_command = harness.decode_command(paths["ffmpeg"], runner.fs.root / DIAGNOSTIC["render_node"].lstrip("/"),
                                                   paths["fixture"].parent / fixture["clip"]["path"])
        selected_driver = runner.fs.root / DIAGNOSTIC["driver"].lstrip("/")
        require(before.get("command") == expected_command and before.get("environment") == harness.environment(selected_driver),
                "Playback did not use the fixed forced-VAAPI command/environment")
        execution = child.get("execution", {})
        require(isinstance(execution, dict) and execution.get("returncode") == 0 and execution.get("failure") is None and
                execution.get("timeout_seconds") == 60, "Decoder failed or exceeded a bound")
        evidence["execution"] = execution
        artifacts = child.get("artifacts", {})
        require(isinstance(artifacts, dict), "Missing playback artifact table")
        for name in ("decoded.nv12", "ffmpeg.stderr.txt", "PREFLIGHT.json", "EXECUTION.json", "COMPARISON.json"):
            limit = fixture["reference"]["bytes"] + fixture["frame_bytes"] if name == "decoded.nv12" else 1024 * 1024
            data = read(output / name, limit, owner=owner(runner))
            require(artifacts.get(name) == {"bytes": len(data), "sha256": sha(data)}, "Playback artifact hash/size differs")
            with (output / name).open("rb") as stream:
                os.fsync(stream.fileno())
        comparison = harness.compare(fixture, paths["fixture"].parent, output / "decoded.nv12")
        require(document(output / "COMPARISON.json", runner) == comparison, "Saved/computed NV12 oracle differs")
        evidence["comparison"] = comparison
        evidence["playback_result_sha256"] = sha(read(output / "RESULT.json", owner=owner(runner)))
        with (output / "RESULT.json").open("rb") as stream:
            os.fsync(stream.fileno())
        require(runner.identity() == identity, "Boot/artifact identity changed during UVD attempt")
        kernel_snapshot(runner)
        sync(output)
        if comparison["pass"] and child.get("status") == "PASS_EXACT_NV12" and command.returncode == 0:
            result.update(status="PASS", message="All 120 forced-VAAPI NV12 frames exactly matched the pinned reference; experimental single-run evidence only. " + FINISH)
        elif not comparison["pass"] and child.get("status") == "FAIL_COMPARISON" and command.returncode == 1:
            result.update(status="FAIL", message="Decoded NV12 frames differ from the pinned reference. " + RECOVERY)
        else:
            raise ValueError("Playback status and independently computed oracle disagree")
    except (OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
        result.update(status="INCONCLUSIVE", stop_required=True,
                      message="UVD attempt did not establish a complete durable oracle. " + RECOVERY)
        evidence["incomplete_reason"] = str(error) or type(error).__name__
    # The original fsynced intent is never updated or removed, even on PASS.
    # Completion belongs only in this run's normal durable report/artifacts.
    return result
