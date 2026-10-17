#!/usr/bin/env python3
"""Package a firmware-free kernel, matching modules and corresponding GPL source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

DEFAULT_TARGET = "6.18-ps4-baikal"


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def check_config(text, release):
    lines = text.splitlines()
    if 'CONFIG_EXTRA_FIRMWARE=""' not in lines:
        raise ValueError("kernel must not embed device firmware")
    for required in [f'CONFIG_LOCALVERSION="-{release.split("-", 1)[1]}"', "CONFIG_PS4_MTS=m"]:
        if required not in lines:
            raise ValueError(f"missing target setting: {required}")


def source_files(source, patched_paths=()):
    raw = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=source)
    names = {os.fsdecode(name) for name in raw.split(b"\0") if name}
    names.update(patched_paths)
    names.add(".config")
    result = []
    for name in sorted(names):
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise ValueError(f"unsafe source archive path: {name}")
        if path.parts[0] == "firmware":
            raise ValueError("unexpected staged firmware in source tree")
        if (source / path).exists() or (source / path).is_symlink():
            result.append(name)
    for required in ["COPYING", ".config", "drivers/net/ethernet/psx_mts_net.c", "drivers/net/ethernet/mts_net_core.h"]:
        if required not in result:
            raise ValueError(f"incomplete corresponding source: {required}")
    if not any(name.startswith("LICENSES/") for name in result):
        raise ValueError("missing Linux license notices")
    return result


def check_module_member(member, release):
    path = Path(member.name)
    prefix = f"lib/modules/{release}"
    if (path.is_absolute() or ".." in path.parts or
            not (member.name.rstrip("/") in ("lib", "lib/modules", prefix) or member.name.startswith(prefix + "/"))):
        raise ValueError(f"unsafe module archive path: {member.name}")
    if not (member.isdir() or member.isfile()):
        raise ValueError(f"unexpected module archive member type: {member.name}")


def validate_module_archive(archive, release):
    process = subprocess.Popen(["zstd", "-dc", str(archive)], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as members:
            seen = set()
            for member in members:
                check_module_member(member, release)
                name = member.name.rstrip("/")
                if name in seen:
                    raise ValueError(f"duplicate module archive member: {name}")
                seen.add(name)
        if process.wait() != 0:
            raise ValueError("cannot decompress module archive")
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", args.target):
        parser.error("invalid target name")
    repo = Path(__file__).resolve().parents[2]
    target = json.loads((repo / f"provenance/{args.target}.json").read_text())
    release = target["kernel_release"]
    output = repo / "output" / args.target
    source = Path(os.environ.get("PSX_KERNEL_BUILD_ROOT", repo)) / "src" / args.target
    if (output / "version.txt").read_text().strip() != release:
        raise ValueError("unexpected built release")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source).decode().strip() != target["upstream"]["commit"]:
        raise ValueError("unexpected upstream source commit")
    check_config((output / "config").read_text(), release)
    check_config((source / ".config").read_text(), release)
    if (source / ".config").read_bytes() != (output / "config").read_bytes():
        raise ValueError("source and built configurations differ")
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=output, check=True)
    patched = set()
    series = repo / target["series_path"]
    for line in series.read_text().splitlines():
        name = line.split("#", 1)[0].strip()
        if not name:
            continue
        raw = subprocess.check_output(["git", "apply", "--numstat", "-z", str((series.parent / name).resolve())])
        for entry in raw.split(b"\0"):
            if entry:
                patched.add(os.fsdecode(entry.split(b"\t", 2)[2]))
    names = source_files(source, patched)
    destination = repo / "dist"
    destination.mkdir(exist_ok=False)
    archive = f"modules-{release}.tar.zst"
    for name in ["bzImage", "config", "System.map", "manifest.txt", "version.txt", archive]:
        shutil.copy2(output / name, destination / name)
    shutil.copy2(output / "SHA256SUMS", destination / "BUILD-SHA256SUMS")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    run_url = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'meerzulee/linux-psx')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', 'local')}"
    with tempfile.TemporaryDirectory(prefix="linux-psx-release-") as directory:
        temporary = Path(directory)
        validate_module_archive(output / archive, release)
        subprocess.run(["tar", "--zstd", "-xf", str(output / archive), "-C", str(temporary)], check=True)
        module_path = f"lib/modules/{release}/kernel/drivers/net/ethernet/psx_mts_net.ko"
        module = temporary / module_path
        if module.is_symlink() or not module.is_file():
            raise ValueError("missing matching MTS module")
        if subprocess.check_output(["modinfo", "-F", "vermagic", str(module)]).decode().split()[0] != release:
            raise ValueError("module release mismatch")
        note = temporary / "module-note.bin"
        module_digest = digest(module)
        # GNU objcopy may rewrite its input without a separate output argument.
        subprocess.run(["objcopy", "--dump-section", f".note.gnu.build-id={note}", str(module), str(temporary / "note-copy.ko")], check=True)
        if digest(module) != module_digest:
            raise ValueError("module identity extraction modified the module")
        if not note.is_file() or not note.read_bytes():
            raise ValueError("module build identity missing")
        metadata = {
            "schema": 1, "target": args.target, "platform": target["platform"],
            "kernel_release": release, "kernel_config_sha256": digest(output / "config"),
            "kernel_sha256": digest(output / "bzImage"), "embedded_firmware": False,
            "modules_archive": {"path": archive, "sha256": digest(output / archive)},
            "module": {"name": "psx_mts_net", "path": module_path, "sha256": module_digest,
                       "build_note_hex": note.read_bytes().hex()},
            "source_commit": commit, "upstream": target["upstream"],
            "workflow_run": run_url,
            "hardware_acceptance": "unqualified: new release/config/build; historical EXP016 is not acceptance of this artifact",
        }
        (destination / "artifacts.json").write_text(json.dumps(metadata, indent=2) + "\n")
        inputs = temporary / "PSX-BUILD-INPUTS.tar.gz"
        with inputs.open("wb") as stream:
            subprocess.run(["git", "archive", "--format=tar.gz", "--prefix=linux-psx-build-inputs/", "HEAD"], cwd=repo, stdout=stream, check=True)
        with tarfile.open(inputs) as members:
            for member in members:
                if member.isfile() and member.name.removeprefix("linux-psx-build-inputs/").split("/", 1)[0] in ("firmware", "src", "output", "dist", "checkpoint"):
                    raise ValueError(f"unexpected asset in build inputs: {member.name}")
        shutil.copy2(output / "manifest.txt", temporary / "PSX-BUILD-MANIFEST.txt")
        (temporary / "PSX-REBUILD.md").write_text(
            f"Complete patched Linux source and resolved .config for {release}.\n"
            "PSX-BUILD-INPUTS.tar.gz contains this project's source, patches, target and tools.\n"
            f"Extract those build inputs and run ./build.sh --target {args.target} on Linux,\n"
            "or scripts/build-kernel-orbstack on macOS ARM64. Native compiler and identity\n"
            "are recorded in PSX-BUILD-MANIFEST.txt; see the target and docs/BUILDING.md.\n"
            "Firmware is neither embedded nor bundled. No distribution is required.\n"
        )
        files = temporary / "source-files.list"
        files.write_bytes(b"\0".join(os.fsencode(name) for name in names) + b"\0")
        subprocess.run([
            "tar", "--zstd", "--sort=name", f"--mtime=@{target['source_date_epoch']}",
            "--owner=0", "--group=0", "--numeric-owner", f"--transform=flags=r;s,^,linux-{release}/,",
            "-cf", str(destination / f"source-{release}.tar.zst"), "-C", str(source),
            "--null", "-T", str(files), "-C", str(temporary), "--no-null",
            "PSX-BUILD-INPUTS.tar.gz", "PSX-BUILD-MANIFEST.txt", "PSX-REBUILD.md",
        ], check=True, env={**os.environ, "ZSTD_CLEVEL": "8", "ZSTD_NBTHREADS": "2"})
    # Bundle self-contained kernel guidance. Distribution instructions remain in companion projects.
    for name in ["BOOTING", "ETHERNET", "HARDWARE-TESTING", "ARTIFACTS"]:
        guide = (repo / f"docs/{name}.md").read_text()
        guide = guide.replace("../platforms/ps4/STATUS.md", f"https://github.com/meerzulee/linux-psx/blob/{commit}/platforms/ps4/STATUS.md")
        (destination / f"{name}.md").write_text(guide)
    (destination / "RELEASE-NOTES.md").write_text(
        f"# Experimental PS4 Baikal — {release}\n\n"
        "Pinned Linux 6.18.44 with 54 PS4 platform patches plus the MTS2 Ethernet patch. "
        "This distribution-independent build has not been boot-tested. Historical tests "
        "on one PS4 Slim Baikal B1 do not qualify this build, other PS4 revisions or PS5.\n\n"
        "Use bzImage with the entire matching modules archive, a compatible PS4 loader, "
        "initramfs and root filesystem. Read BOOTING.md, ETHERNET.md and HARDWARE-TESTING.md. "
        "No root filesystem, launcher PKG, boot-profile defaults, network-manager adapter "
        "or firmware is included. Supply legally obtained device firmware separately; "
        "early display/Wi-Fi firmware may be needed in initramfs.\n\n"
        "MTS Ethernet uses explicit first-publication gating and permanent DMA/code retention. "
        "Do not unload, reset, rearm or reuse old experiment modules. Driver qualification "
        "and unattended distribution activation remain incomplete.\n\n"
        f"Corresponding source: source-{release}.tar.zst, including original Linux licenses, "
        "resolved config, patch authorship, provenance and project build inputs. Verify "
        "SHA256SUMS with sha256sum -c SHA256SUMS on Linux or shasum -a 256 -c SHA256SUMS on macOS.\n\n"
        f"Source commit: {commit}\n\nCI build: {run_url}\n"
    )
    (destination / "SHA256SUMS").write_text("".join(f"{digest(path)}  {path.name}\n" for path in sorted(destination.iterdir()) if path.is_file()))
    print(f"PASS: packaged {release}, matching modules and complete corresponding source")


if __name__ == "__main__":
    main()
