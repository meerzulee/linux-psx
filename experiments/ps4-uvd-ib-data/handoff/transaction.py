#!/usr/bin/env python3
"""Revision C transaction engine adapted for IB-DATA installed over retained C.

Native rollback and install-from bytes are distinct. Only explicitly known
installation/restoration phase states are recoverable. No automatic rollback.
See the new lab handoff REUSE.json for exact reused/adapted function identities.
"""
import argparse

import ctypes

import hashlib

import io

import json

import os

from pathlib import Path

import re

import stat

import subprocess

import sys

import tarfile

import tempfile

SERIAL = "REDACTED_USB_SERIAL"

BOOT_FILES = {"bzImage", "initramfs.cpio.gz", "bootargs.txt", "vram.txt", "release.json"}

KEEP_FILES = {"vram.txt", "release.json"}

FIRMWARE_WRAPPER = "62a43ef4814f59fdd6772792e0a9303e016fe2e17ad30b36412d1fa7ce4cd5cb"

OLD_FIRMWARE = "30eed8d680321bdc6213409a4a882442049b2a559eadb9ee2554d53b0cfb96c9"

OLD_INITRAMFS = "089d62daaef9b4ab7a61486d193533deef5f79c7782ecd8ddc10030477b0f460"

FIRMWARE_MEMBER = "lib/firmware/amdgpu/liverpool_uvd.bin"

OLD_KERNEL = "e5bdfd965066bac8c0a098ffa351e49b870aff52563e9c74e60e73d1c7cb6868"

OLD_ARGS = "dabd2779ac7f172ac3f534a93883694155d8b0c51d6fe2247597cc7fb111bb84"

OLD_NATIVE_BOOT_HASHES = {
    "bzImage": OLD_KERNEL, "initramfs.cpio.gz": OLD_INITRAMFS, "bootargs.txt": OLD_ARGS,
    "release.json": "3f2bac9755c1abce9daa03cabf47a60d96fe25235634eb1dae5974290fd6958c",
    "vram.txt": "4f71bb761ace37c88826cd8cc1c948e1cf5b5d0cd153dc651a6efdb3dfb9f2b1"}

NATIVE_MODULE_MANIFEST_SHA = "5f6b1ffcecb13725e0a9a1c686609609e1010982ed1bd617ac8d8ac0011fc3fa"

PREPARED_INITRAMFS = "cf2102f52b8b5d048538e1dd410b2c9b43ec81d2a567294d6204578aa83103f7"

PREPARATION_RECEIPT = "28150c8b1fb43addcf0bff20770b59583b46ea9df3afe09d1e4c92b56eacbb9a"

ROLLBACK_RELEASE = "7.2.11-psx-ps4-baikal-native-dev1"

MEDIA = {"disk_bytes": 123983626240, "boot_bytes": 4294967296,
         "root_bytes": 119687610368, "boot_uuid": "11111111-2222-4333-8444-555555555555",
         "root_uuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"}

EXP = None  # Set only from the exact reviewed Revision C plan.

def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(*argv, binary=False, pass_fds=()):
    result = subprocess.run(argv, capture_output=True, pass_fds=pass_fds)
    require(result.returncode == 0, f"Command failed ({result.returncode}): {argv!r}: " + result.stderr.decode(errors="replace").strip())
    return result.stdout if binary else result.stdout.decode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def regular_bytes(path, maximum=None):
    require(path.is_file() and not path.is_symlink(), f"Not a regular file: {path}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode), f"Opened input is not regular: {path}")
        require(maximum is None or info.st_size <= maximum, f"Input exceeds bound: {path}")
        content = stream.read() if maximum is None else stream.read(maximum + 1)
        require(len(content) == info.st_size and (maximum is None or len(content) <= maximum),
                f"Input changed or exceeded bound: {path}")
        return content


def validate_initramfs_receipt(initramfs, receipt):
    """Validate root's reviewed data-only preparation report; never unpack it here."""
    require(receipt.get("base_sha256") == OLD_INITRAMFS and receipt.get("base_bytes") == 2488567,
            "Initramfs preparation base identity differs")
    require(receipt.get("old_member_sha256") == OLD_FIRMWARE and
            receipt.get("replacement_sha256") == FIRMWARE_WRAPPER and
            receipt.get("member") == FIRMWARE_MEMBER, "Firmware preparation identity differs")
    require(type(receipt.get("entries")) is int and receipt["entries"] > 1 and
            type(receipt.get("non_uvd_entries_preserved")) is int and
            receipt["non_uvd_entries_preserved"] == receipt["entries"] - 1 and
            receipt.get("member_metadata_preserved_except_size") is True,
            "Initramfs preservation report is incomplete")
    require(type(receipt.get("output_bytes")) is int and 0 < len(initramfs) <= 16 * 1024 * 1024 and
            receipt["output_bytes"] == len(initramfs) and receipt.get("output_sha256") == sha(initramfs),
            "Initramfs bytes differ from preparation receipt")
    require(isinstance(receipt.get("cpio_sha256"), str) and
            re.fullmatch("[0-9a-f]{64}", receipt["cpio_sha256"]), "Missing expanded archive identity")
    return receipt


def validate_archive(raw, plan):
    prefix = "lib/modules/" + plan["release"] + "/"
    tree = plan["module_tree"]
    require(tree and all(name.startswith(prefix) for name in tree), "Manifest leaves target release")
    files, seen = {}, set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            name = member.name.rstrip("/") if member.isdir() else member.name
            require(name not in seen, f"Duplicate tar member: {name}")
            seen.add(name)
            require(all(part not in ("", ".", "..") for part in name.split("/")) and not name.startswith("/"), "Noncanonical tar path")
            require(name in ("lib", "lib/modules", prefix.rstrip("/")) or name.startswith(prefix), "Tar leaves release")
            # The native packager uses PAX for long canonical module names.
            # Accept only its path extension, identical to the already validated
            # member name. No size/linkpath/ownership/time/vendor overrides.
            require(not member.pax_headers or member.pax_headers == {"path": member.name},
                    "Only an exact canonical PAX path is allowed")
            if member.isdir():
                continue
            require(member.isfile(), "Only regular files and directories are allowed")
            content = archive.extractfile(member).read()
            require(tree.get(name) == sha(content), f"Tar hash differs: {name}")
            files[name[len(prefix):]] = content
    require({prefix + name for name in files} == set(tree), "Archive and manifest file sets differ")
    require(any(name.endswith(".ko") for name in files), "No uncompressed kernel modules found")
    return files


def flatten(items):
    for item in items:
        yield item
        yield from flatten(item.get("children", []))


def mount_inventory():
    return list(flatten(json.loads(run("findmnt", "-J", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS,MAJ:MIN"))["filesystems"]))


def identify(plan, unmounted=False):
    inventory = json.loads(run("lsblk", "--tree", "-J", "-b", "-p", "-o",
                               "PATH,TYPE,SERIAL,SIZE,FSTYPE,UUID,MOUNTPOINTS,MAJ:MIN"))["blockdevices"]
    disks = {d["path"]: d for d in flatten(inventory) if d["type"] == "disk" and d.get("serial") == SERIAL}
    require(len(disks) == 1, "Serial does not identify exactly one disk")
    disk = next(iter(disks.values()))
    require(int(disk["size"]) == plan["disk_bytes"], "Disk size differs")
    descendants = list(flatten(disk.get("children", [])))
    require(len(descendants) == 2 and all(d["type"] == "part" for d in descendants), "Expected exactly two plain partitions")
    parts = {}
    for role, fstype in (("boot", "vfat"), ("root", "ext4")):
        matches = [d for d in descendants if d.get("uuid") == plan[role + "_uuid"]]
        require(len(matches) == 1, f"{role} UUID differs")
        part = matches[0]
        require(part.get("fstype") == fstype and int(part["size"]) == plan[role + "_bytes"], f"{role} type/size differs")
        parts[role] = part
    mounts = mount_inventory()
    target_ids = {d["maj:min"] for d in (disk, *descendants)}
    require(not any(m["maj:min"] in target_ids and m["target"] in ("/", "/boot", "/boot/efi", "/efi") for m in mounts),
            "Target disk backs the running host root/boot")
    for role, part in parts.items():
        part["mounts"] = [m for m in mounts if m["maj:min"] == part["maj:min"]]
        if unmounted:
            require(not part["mounts"] and not any(part.get("mountpoints") or []), f"{role} partition must be unmounted")
    require(not any(m["maj:min"] == disk["maj:min"] for m in mounts), "Whole disk is mounted")
    return disk, parts


def boot_hashes(boot, plan):
    hashes = {name: sha(regular_bytes(boot / name)) for name in BOOT_FILES}
    require(hashes == plan["old_boot_hashes"], "Current boot files differ from verified backup")
    return hashes


def install_boot_hashes(boot, plan):
    hashes = {name: sha(regular_bytes(boot / name)) for name in BOOT_FILES}
    require(hashes == plan['install_boot_hashes'], 'Current boot files differ from retained Revision C installation source')
    return hashes


def known_recovery_states(plan):
    """Exact C-to-IB commit states, each followed by native recovery prefixes.

    These are states reachable by this transaction, not a Cartesian product
    of arbitrary known hashes. Duplicate states collapse when initramfs stays
    unchanged. Native is also the final state of every explicit restoration.
    """
    native = plan['old_boot_hashes']
    current = dict(plan['install_boot_hashes'])
    phases = [dict(current)]
    for name, value in (('initramfs.cpio.gz', plan['initramfs_sha256']),
                        ('bootargs.txt', plan['new_bootargs_sha256']),
                        ('bzImage', plan['kernel_sha256'])):
        current[name] = value
        phases.append(dict(current))
    states = {tuple(sorted(x.items())) for x in phases}
    for phase in phases:
        current = dict(phase)
        for name in ('bzImage', 'initramfs.cpio.gz', 'bootargs.txt'):
            current[name] = native[name]
            states.add(tuple(sorted(current.items())))
    return states


def verify_install_source_modules(root, plan):
    # The same verified relative /lib alias, no-symlink walk and complete
    # kernel-object identity check applies to the retained C release.
    return verify_rollback_modules(root, {'rollback_release':plan['install_release'],
        'rollback_module_tree':plan['install_module_tree']})


def module_base(root):
    require((root / "lib").is_symlink() and os.readlink(root / "lib") == "usr/lib", "Expected relative target /lib -> usr/lib alias")
    for relative in ("usr", "usr/lib", "usr/lib/modules"):
        path = root / relative
        require(path.is_dir() and not path.is_symlink() and path.stat().st_uid == 0, f"Unsafe module directory: {path}")
    parent = root / "usr/lib/modules"
    return parent


def module_parent(root, release):
    parent = module_base(root)
    require(not os.path.lexists(parent / release), "New release is already installed")
    require(not os.path.lexists(parent / (".psx-" + EXP)), "Module staging directory already exists")
    return parent


def verify_rollback_modules(root, plan):
    parent = module_base(root)
    release = parent / plan["rollback_release"]
    require(release.is_dir() and not release.is_symlink() and release.stat().st_uid == 0,
            "Verified rollback module release is missing or unsafe")
    prefix = "lib/modules/" + plan["rollback_release"] + "/"
    expected = {n[len(prefix):]: h for n, h in plan["rollback_module_tree"].items() if ".ko" in n}
    found = {}
    for directory, dirs, names in os.walk(release, followlinks=False):
        require(all(not (Path(directory) / n).is_symlink() for n in dirs + names), "Symlink in rollback module tree")
        for name in names:
            p = Path(directory) / name
            if ".ko" in name:
                found[str(p.relative_to(release))] = sha(regular_bytes(p))
    require(found == expected and expected, "Rollback kernel objects do not match the native build")
    return len(found)


def uart_ready(stage):
    # Resolve sibling UART from this workspace; no capture state is copied.
    workspace = Path(__file__).resolve().parents[4]
    uart = workspace / "ps4-uart/uart"
    require(uart.is_file() and not uart.is_symlink(), "Workspace UART entry point is missing/unsafe")
    prefix = ["sudo", "-u", os.environ["SUDO_USER"]] if os.environ.get("SUDO_USER") not in (None, "root") else []
    status = run(*prefix, str(uart), "status")
    session = run(*prefix, str(uart), "session", "status")
    require(re.search(r"^\[\+\] Status:\s+READY$", status, re.M), "UART capture is not READY")
    match = re.search(r"^\[\+\] Session active: (.+)$", session, re.M)
    require(match and match.group(1) == EXP, "Exact experiment is not active")
    require(re.search(r"^\s+Continuity: valid so far \(", session, re.M), "UART continuity is invalid")
    return {"status": status.strip(), "session": session.strip()}


def fsync_path(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def install_modules(root, plan, files):
    parent = module_parent(root, plan["release"])
    staging = parent / (".psx-" + EXP)
    staging.mkdir(mode=0o755)
    for name, content in sorted(files.items()):
        path = staging / name
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        write_new(path, content)
    require(all(sha(regular_bytes(staging / n)) == sha(v) for n, v in files.items()), "Installed module staging differs")
    for directory, _, _ in os.walk(staging, topdown=False):
        fsync_path(directory)
    os.rename(staging, parent / plan["release"])
    fsync_path(parent)
    run("depmod", "-b", str(root), plan["release"])
    destination = parent / plan["release"]
    require((destination / "modules.dep").is_file(), "depmod index missing")
    # depmod may regenerate indexes, but must not alter any kernel object.
    require(all(sha(regular_bytes(destination / n)) == sha(v) for n, v in files.items() if ".ko" in n), "Installed kernel objects differ")
    for directory, _, _ in os.walk(destination, topdown=False):
        fsync_path(directory)
    return sorted(p.name for p in destination.iterdir() if p.name.startswith("modules."))


def atomic_boot_file(boot, name, content, temporary):
    path = boot / temporary
    write_new(path, content)
    require(sha(regular_bytes(path)) == sha(content), "Temporary boot file differs")
    os.replace(path, boot / name)
    fsync_path(boot)
    # Persist each phase before committing the next file on FAT.
    run("sync", "-f", str(boot))


def install_boot(boot, plan, kernel, initramfs, old, args, check_uart=lambda: None):
    install_boot_hashes(boot, plan)
    recovery = boot / ("psx-recovery-" + EXP)
    require(not os.path.lexists(recovery), "Recovery directory already exists")
    recovery.mkdir()
    for name, content in old.items():
        write_new(recovery / name, content)
    require({n: sha(regular_bytes(recovery / n)) for n in BOOT_FILES} == plan["old_boot_hashes"], "Recovery differs")
    fsync_path(recovery)
    fsync_path(boot)
    run("sync", "-f", str(boot))
    check_uart()
    atomic_boot_file(boot, "initramfs.cpio.gz", initramfs, ".initramfs-" + EXP)
    check_uart()
    atomic_boot_file(boot, "bootargs.txt", args, ".bootargs-" + EXP)
    check_uart()
    atomic_boot_file(boot, "bzImage", kernel, ".bzImage-" + EXP)
    final = {n: sha(regular_bytes(boot / n)) for n in BOOT_FILES}
    require(final == {**plan["install_boot_hashes"], "bzImage": plan["kernel_sha256"],
                      "bootargs.txt": plan["new_bootargs_sha256"],
                      "initramfs.cpio.gz": plan["initramfs_sha256"]}, "Final boot hashes differ")
    return final


def rollback_hashes(boot, plan):
    current = {n: sha(regular_bytes(boot / n)) for n in BOOT_FILES}
    require(all(current[n] == plan["old_boot_hashes"][n] for n in KEEP_FILES), "Preserved boot files differ")
    require(tuple(sorted(current.items())) in known_recovery_states(plan),
            'Rollback refuses a state outside the explicit C/IB/native transaction phases')
    recovery = boot / ("psx-recovery-" + EXP)
    require(recovery.is_dir() and not recovery.is_symlink(), "Experiment recovery directory missing/unsafe")
    require({n: sha(regular_bytes(recovery / n)) for n in BOOT_FILES} == plan["old_boot_hashes"],
            "Recovery boot files differ from verified native rollback")
    return current


def restore_boot(boot, plan, old, check_uart=lambda: None):
    rollback_hashes(boot, plan)
    check_uart()
    atomic_boot_file(boot, "bzImage", old["bzImage"], ".rollback-bzImage-" + EXP)
    check_uart()
    atomic_boot_file(boot, "initramfs.cpio.gz", old["initramfs.cpio.gz"], ".rollback-initramfs-" + EXP)
    check_uart()
    atomic_boot_file(boot, "bootargs.txt", old["bootargs.txt"], ".rollback-bootargs-" + EXP)
    return boot_hashes(boot, plan)


def preflight_paths(boot, plan, rollback=False):
    for name, expected in ((".bzImage-", plan["kernel_sha256"]),
                           (".initramfs-", plan["initramfs_sha256"]),
                           (".bootargs-", plan["new_bootargs_sha256"]),
                           (".rollback-bzImage-", None), (".rollback-initramfs-", None),
                           (".rollback-bootargs-", None)):
        path = boot / (name + EXP)
        if rollback and expected is not None and os.path.lexists(path):
            require(sha(regular_bytes(path)) == expected, "Interrupted-install temporary differs")
        else:
            require(not os.path.lexists(path), "Temporary boot file already exists")
    if rollback:
        rollback_hashes(boot, plan)
    else:
        install_boot_hashes(boot, plan)
        require(not os.path.lexists(boot / ("psx-recovery-" + EXP)), "Recovery already exists")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", type=Path)
    mode.add_argument("--apply", type=Path)
    mode.add_argument("--rollback", type=Path)
    parser.add_argument("--confirm-serial")
    parser.add_argument("--reviewed-plan-sha256", help="Internal root review pin, not another user approval")
    parser.add_argument("--reviewed-handoff-sha256", help="Primary's exact new source/build/plan review receipt")
    parser.add_argument("--confirm-usb-on-host-launch-held", action="store_true",
                        help="For install: positively identified USB isolated on this host; hold Linux launch until readback/unmount.")
    parser.add_argument("--console-state", choices=("not-observed", "orbis-idle", "fully-off", "linux-launching"),
                        help="Console power may be explicitly not-observed for host installation; explicit launching is rejected.")
    parser.add_argument("--confirm-poweroff-and-usb-return", action="store_true",
                        help="For rollback after a test: PS4 fully off and USB returned.")
    args = parser.parse_args()
    stage = (args.apply or args.rollback or args.check).resolve()
    mutate = bool(args.apply or args.rollback)
    receipt = {"experiment": EXP, "mode": "apply" if args.apply else "rollback" if args.rollback else "check", "committed": False}
    mounted, private = [], None
    failed = False
    try:
        require(sys.platform == "linux" and os.geteuid() == 0, "Run on the Linux USB host with sudo")
        require(not args.check or (args.confirm_serial is None and args.reviewed_plan_sha256 is None and
                args.reviewed_handoff_sha256 is None and not args.confirm_poweroff_and_usb_return and
                not args.confirm_usb_on_host_launch_held and args.console_state is None),
                "Confirmation/review pin applies only to writes")
        if mutate:
            require(args.confirm_serial == SERIAL, "Supply the exact --confirm-serial")
            if args.apply:
                require(args.confirm_usb_on_host_launch_held and args.console_state in ("not-observed", "orbis-idle", "fully-off"),
                        "Install requires isolated host USB, held Linux launch and explicit non-launching state (power may be not-observed)")
            else:
                require(args.confirm_poweroff_and_usb_return and args.console_state in (None, "fully-off"),
                        "Rollback requires full-power-off and returned-USB confirmation")
            require(args.reviewed_plan_sha256 == sha(regular_bytes(stage / "plan.json")), "Root must supply the exact reviewed plan SHA256")
            validate_reviewed_handoff(stage, args.reviewed_handoff_sha256, args.reviewed_plan_sha256)
            receipt["operator_poweroff_and_usb_return_confirmed"] = bool(args.rollback)
            receipt["usb_on_host_launch_held_confirmed"] = bool(args.apply)
            receipt["console_state"] = args.console_state if args.apply else "fully-off"
            receipt["console_state_observed"] = not args.apply or args.console_state != "not-observed"
            receipt["operator_reported_console_state"] = (None if args.console_state == "not-observed" else args.console_state) if args.apply else "fully-off"
            receipt["operator_holds_linux_launch"] = bool(args.apply)
            receipt["reviewed_handoff_sha256"] = args.reviewed_handoff_sha256
        plan, kernel, initramfs, files, old, new_args = load_inputs(stage, rollback=bool(args.rollback))
        disk, parts = identify(plan, unmounted=mutate)
        receipt.update(release=plan["release"], install_from_release=plan["install_release"], native_restore_automatic=False, disk=disk["path"], serial=SERIAL,
                       partitions={role: {k: part[k] for k in ("path", "size", "uuid", "fstype", "mounts")} for role, part in parts.items()},
                       module_files=len(files), rollback={"boot_uuid": plan["boot_uuid"],
                           "source": "psx-recovery-" + EXP, "destination": "bzImage then initramfs.cpio.gz then bootargs.txt",
                           "sha256": plan["old_boot_hashes"]["bzImage"],
                           "bootargs_sha256": plan["old_bootargs_sha256"],
                           "initramfs_sha256": plan["old_boot_hashes"]["initramfs.cpio.gz"],
                           "action": "Verify all five recovery hashes; atomically restore kernel FIRST, initramfs SECOND, bootargs THIRD; retain vram/release and both module trees."})
        if args.check:
            for role in ("root", "boot"):
                require(parts[role]["mounts"], f"--check needs an existing {role} mount; it never mounts media")
            boot = Path(parts["boot"]["mounts"][0]["target"])
            root = Path(parts["root"]["mounts"][0]["target"])
            receipt["boot_before"] = install_boot_hashes(boot, plan)
            preflight_paths(boot, plan)
            receipt["rollback_module_count"] = verify_rollback_modules(root, plan)
            receipt["install_source_module_count"] = verify_install_source_modules(root, plan)
            parent = module_parent(root, plan["release"])
            receipt["existing_module_trees"] = sorted(p.name for p in parent.iterdir())
            require(not os.path.lexists(boot / ("psx-recovery-" + EXP)), "Recovery already exists")
            receipt["check_passed"] = True
            return
        receipt["uart_before"] = uart_ready(stage)
        # Keep these temporary mounts private from the host mount namespace.
        libc = ctypes.CDLL(None, use_errno=True)
        require(libc.unshare(0x00020000) == 0, f"Cannot create private mount namespace: errno {ctypes.get_errno()}")
        run("mount", "--make-rprivate", "/")
        checked_disk, checked_parts = identify(plan, unmounted=True)
        require(checked_disk["path"] == disk["path"] and all(
            checked_parts[role]["path"] == parts[role]["path"] and
            checked_parts[role]["maj:min"] == parts[role]["maj:min"] for role in parts),
            "Device path/number changed during preflight; restart the check")
        private = Path(tempfile.mkdtemp(prefix="psx-" + EXP + "-", dir="/run"))
        root, boot = private / "root", private / "boot"
        for path in (root, boot):
            path.mkdir(mode=0o700)
        run("mount", "-t", "ext4", "-o", "rw,nodev,nosuid,noexec", parts["root"]["path"], str(root))
        mounted.append(root)
        run("mount", "-t", "vfat", "-o", "rw,nodev,nosuid,noexec", parts["boot"]["path"], str(boot))
        mounted.append(boot)
        # Recheck both filesystems before the first intentional content change.
        preflight_paths(boot, plan, rollback=bool(args.rollback))
        receipt["boot_before"] = rollback_hashes(boot, plan) if args.rollback else install_boot_hashes(boot, plan)
        receipt["rollback_module_count"] = verify_rollback_modules(root, plan)
        if args.apply:
            receipt["install_source_module_count"] = verify_install_source_modules(root, plan)
            module_parent(root, plan["release"])
        uart_ready(stage)
        if args.apply:
            receipt["module_indexes"] = install_modules(root, plan, files)
            receipt["modules_installed"] = True
            uart_ready(stage)
            receipt["boot_after"] = install_boot(boot, plan, kernel, initramfs, old, new_args, lambda: uart_ready(stage))
        else:
            receipt["boot_after"] = restore_boot(boot, plan, old, lambda: uart_ready(stage))
        run("sync", "-f", str(root))
        run("sync", "-f", str(boot))
        receipt["committed"] = True
        receipt["uart_after"] = uart_ready(stage)
    except Exception as error:
        failed = True
        receipt["error"] = str(error)
        print("REFUSED/FAILED: " + str(error), file=sys.stderr)
    finally:
        cleanup_errors = []
        for path in reversed(mounted):
            try:
                run("sync", "-f", str(path))
            except Exception as error:
                cleanup_errors.append(str(error))
            try:
                run("umount", str(path))
            except Exception as error:
                cleanup_errors.append(str(error))
        receipt["unmounted"] = not any(os.path.ismount(p) for p in mounted)
        if private and receipt["unmounted"]:
            for path in (private / "boot", private / "root", private):
                try:
                    path.rmdir()
                except OSError as error:
                    cleanup_errors.append(str(error))
        if cleanup_errors:
            failed = True
            receipt["cleanup_errors"] = cleanup_errors
        print(json.dumps(receipt, indent=2, sort_keys=True))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    raise SystemExit("Use install_usb.py through the reviewed run_test.py wrapper.")
