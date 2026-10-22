#!/usr/bin/env python3
"""Package a firmware-free kernel, matching modules and corresponding GPL source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile

DEFAULT_TARGET = "6.18-ps4-baikal"


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def check_config(text, release, platform="ps4"):
    lines = text.splitlines()
    if 'CONFIG_EXTRA_FIRMWARE=""' not in lines:
        raise ValueError("kernel must not embed device firmware")
    driver = "CONFIG_DRM_AMDGPU=m" if platform == "ps5" else "CONFIG_PS4_MTS=m"
    for required in [f'CONFIG_LOCALVERSION="-{release.split("-", 1)[1]}"', driver]:
        if required not in lines:
            raise ValueError(f"missing target setting: {required}")


def source_files(source, patched_paths=(), platform="ps4"):
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
    drivers = (["drivers/gpu/drm/amd/amdgpu/amdgpu_drv.c"] if platform == "ps5" else
               ["drivers/net/ethernet/psx_mts_net.c", "drivers/net/ethernet/mts_net_core.h"])
    for required in ["COPYING", ".config", *drivers]:
        if required not in result:
            raise ValueError(f"incomplete corresponding source: {required}")
    if not any(name.startswith("LICENSES/") for name in result):
        raise ValueError("missing Linux license notices")
    return result


def patched_source_paths(repo, target):
    """Use recorded paths: a target's series may share another patch directory."""
    items = target["patches"] + target.get("direct_references", [])
    entries = [line.split("#", 1)[0].strip()
               for line in (repo / target["series_path"]).read_text().splitlines()]
    if [entry for entry in entries if entry] != [item["entry"] for item in items]:
        raise ValueError("packaged patch order differs from provenance")
    patched = set()
    for item in items:
        path = (repo / item["path"]).resolve()
        if not path.is_relative_to(repo.resolve()) or not path.is_file() or digest(path) != item["sha256"]:
            raise ValueError("packaged patch identity differs from provenance")
        raw = subprocess.check_output(["git", "apply", "--numstat", "-z", str(path)])
        for entry in raw.split(b"\0"):
            if entry:
                patched.add(os.fsdecode(entry.split(b"\t", 2)[2]))
    return patched


def release_notes(target, commit, run_url):
    release = target["kernel_release"]
    if target["target"] == "7.2-ps5" and release == "7.2.3-psx-ps5-dev1":
        return (
            f"# Experimental PS5 — {release}\n\n"
            "Pinned Linux 7.2.3 with the ps5-linux community patch/config snapshot. "
            "This build has not been boot-tested. No hardware acceptance is claimed.\n\n"
            "Use bzImage with the whole matching modules archive and a compatible PS5 "
            "loader, initramfs and external root filesystem. PS4 MTS instructions do not "
            "apply. Device firmware is neither embedded nor bundled; the image owns its "
            "external firmware integration. Preserve an existing working boot set.\n\n"
            f"Complete corresponding source: source-{release}.tar.zst, including resolved "
            "config, Linux notices, PSX build inputs and the exact packaging script. "
            "Verify SHA256SUMS before using artifacts.\n\n"
            f"Source checkpoint: {commit}\n\nBuild: {run_url}\n"
        )
    if target["target"] == "6.18-ps4-baikal":
        evidence = (
            "Pinned Linux 6.18.44 with 54 PS4 platform patches, the MTS2 Ethernet patch and its "
            "native autoload. This CI build has not been boot-tested. The same patch set, built "
            "locally as 6.18.44-psx-ps4-baikal-native-dev1, passed two Ethernet boots on one Baikal "
            "PS4; that does not qualify this build, other PS4 revisions or PS5."
        )
    elif target["target"] == "7.2-ps4-baikal-native-dev":
        evidence = (
            "Pinned Linux 7.2.11 with the 61-patch Baikal port, unchanged native MTS autoload "
            "and PR #3 HDMI underscan fix (63 patches). This dev2 build has not been boot-tested. "
            "The historical dev1 local OrbStack build passed one cable-connected boot on one "
            "PS4 Slim / Baikal B1: USB root, desktop, native Ethernet DHCP/default route, gateway "
            "ping, HTTPS and hash-checked 4 MiB transfers both ways (EXP-20261018-004). "
            "That dev1 result does not qualify the new HDMI addition or this rebuilt image. "
            "Ethernet patch bytes and network configuration remain unchanged. RX drops in the "
            "historical local test remain unclassified; long-duration operation, cable/admin "
            "cycles and cold firmware loading remain unqualified. No hardware acceptance or "
            "promotion is claimed for this development target, other PS4 revisions or PS5."
        )
    else:
        raise ValueError("release notes require an explicitly supported PS4 Baikal target")
    return (
        f"# Experimental PS4 Baikal — {release}\n\n{evidence}\n\n"
        f"For testing, download and extract kernel-{release}.zip. The separate "
        f"source-{release}.tar.zst archive contains complete corresponding source. "
        "CI likewise offers separate kernel and source downloads; each has its "
        "own complete SHA256SUMS.\n\n"
        "Use bzImage with the entire matching modules archive, a compatible PS4 loader, "
        "initramfs and root filesystem. Read BOOTING.md, ETHERNET.md and HARDWARE-TESTING.md. "
        "Ethernet needs no setup tool: udev loads psx_mts_net and an ordinary network manager "
        "opens the wired interface. No root filesystem, launcher PKG, "
        "boot-profile defaults or firmware is included. Supply legally obtained device firmware separately; "
        "early display/Wi-Fi firmware may be needed in initramfs.\n\n"
        "MTS Ethernet publishes one DMA generation on its first open and retains DMA and code "
        "until power-off. Boot with psx_mts_net.autoarm=0 to keep the explicit gate, or "
        "modprobe.blacklist=psx_mts_net to skip the driver. Do not unload, reset or rearm it. "
        "Driver qualification remains incomplete.\n\n"
        f"Corresponding source: source-{release}.tar.zst, including original Linux licenses, "
        "resolved config, patch authorship, provenance and project build inputs. Verify "
        "SHA256SUMS with sha256sum -c SHA256SUMS on Linux or shasum -a 256 -c SHA256SUMS on macOS.\n\n"
        f"Source commit: {commit}\n\nCI build: {run_url}\n"
    )


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


def safe_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError(f"unsafe artifact name: {name}")
    return name


def flat_files(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"artifact directory must be a real directory: {directory}")
    result = {}
    for path in sorted(directory.iterdir()):
        safe_name(path.name)
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError(f"artifact must be a flat regular file: {path}")
        result[path.name] = path
    return result


def write_checksums(directory):
    files = flat_files(directory)
    (Path(directory) / "SHA256SUMS").write_text("".join(
        f"{digest(path)}  {name}\n" for name, path in files.items() if name != "SHA256SUMS"))


def verified_files(directory):
    files = flat_files(directory)
    if "SHA256SUMS" not in files:
        raise ValueError("artifact bundle lacks SHA256SUMS")
    expected = {}
    for line in files["SHA256SUMS"].read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if not match or match[2] == "SHA256SUMS" or match[2] in expected:
            raise ValueError("invalid/duplicate artifact checksum entry")
        expected[match[2]] = match[1]
    if set(expected) != set(files) - {"SHA256SUMS"}:
        raise ValueError("artifact checksums do not cover exactly all bundle files")
    for name, value in expected.items():
        if digest(files[name]) != value:
            raise ValueError(f"artifact checksum mismatch: {name}")
    return files


def artifact_metadata(files, release=None, expected_commit=None):
    if "artifacts.json" not in files:
        raise ValueError("artifact bundle lacks artifacts.json")
    metadata = json.loads(files["artifacts.json"].read_text())
    if not isinstance(metadata, dict):
        raise ValueError("artifact metadata must be a JSON object")
    value = metadata.get("kernel_release")
    safe_name(value)
    if release is not None and value != release:
        raise ValueError("artifact kernel release differs")
    commit = metadata.get("source_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("artifact source commit is not a full SHA")
    if expected_commit is not None and commit != expected_commit:
        raise ValueError("artifact source commit differs from expected commit")
    archive = metadata.get("source_archive")
    if not isinstance(archive, dict) or archive.get("path") != f"source-{value}.tar.zst" or not re.fullmatch(
            r"[0-9a-f]{64}", str(archive.get("sha256", ""))):
        raise ValueError("invalid corresponding source archive identity")
    return metadata


def verify_runtime(files, metadata):
    release = metadata["kernel_release"]
    required = {"bzImage", "config", "System.map", "manifest.txt", "version.txt", "artifacts.json",
                "RELEASE-NOTES.md", "BUILD-SHA256SUMS", "BOOTING.md", "ETHERNET.md",
                "HARDWARE-TESTING.md", "ARTIFACTS.md", f"modules-{release}.tar.zst"}
    if not required <= set(files):
        raise ValueError("runtime bundle is missing required kernel/module/guidance files")
    if any(name.startswith("source-") and name.endswith(".tar.zst") for name in files):
        raise ValueError("runtime bundle must not contain corresponding source archives")
    if files["version.txt"].read_text().strip() != release:
        raise ValueError("runtime version differs from metadata")
    if metadata.get("kernel_sha256") != digest(files["bzImage"]) or metadata.get("kernel_config_sha256") != digest(files["config"]):
        raise ValueError("runtime kernel/config identity differs")
    modules = metadata.get("modules_archive")
    if not isinstance(modules, dict) or modules.get("path") != f"modules-{release}.tar.zst" or modules.get("sha256") != digest(files[f"modules-{release}.tar.zst"]):
        raise ValueError("runtime modules archive identity differs")


def stage_ci_artifacts(destination, release):
    """Split canonical flat dist files without copying generated directories."""
    destination = Path(destination)
    safe_name(release)
    files = verified_files(destination)
    metadata = artifact_metadata(files, release)
    source_name = metadata["source_archive"]["path"]
    if source_name not in files or digest(files[source_name]) != metadata["source_archive"]["sha256"]:
        raise ValueError("missing/mismatched complete corresponding source")
    runtime_files = {name: path for name, path in files.items() if name not in (source_name, "SHA256SUMS")}
    verify_runtime(runtime_files, metadata)
    for name in ("runtime", "source"):
        path = destination / name
        if path.exists() or path.is_symlink():
            raise ValueError(f"refusing existing CI staging directory: {path}")
    runtime, source = destination / "runtime", destination / "source"
    runtime.mkdir()
    source.mkdir()
    for name, path in runtime_files.items():
        shutil.copy2(path, runtime / name)
    for name in (source_name, "artifacts.json"):
        shutil.copy2(files[name], source / name)
    write_checksums(runtime)
    write_checksums(source)
    return runtime, source


def assemble_release(runtime, source, destination, expected_commit=None):
    """Verify independent CI downloads and assemble public assets using stdlib."""
    if expected_commit is not None and not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise ValueError("expected commit must be a full SHA")
    runtime_files, source_files = verified_files(runtime), verified_files(source)
    metadata = artifact_metadata(runtime_files, expected_commit=expected_commit)
    release = metadata["kernel_release"]
    source_metadata = artifact_metadata(source_files, release, expected_commit)
    if metadata != source_metadata or runtime_files["artifacts.json"].read_bytes() != source_files["artifacts.json"].read_bytes():
        raise ValueError("runtime and source metadata differ")
    verify_runtime(runtime_files, metadata)
    source_name = metadata["source_archive"]["path"]
    if set(source_files) != {source_name, "artifacts.json", "SHA256SUMS"}:
        raise ValueError("source bundle must contain exactly complete source, metadata and checksums")
    if digest(source_files[source_name]) != metadata["source_archive"]["sha256"]:
        raise ValueError("corresponding source archive identity differs")
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("release destination already exists")
    # All checksum/identity/path checks finish before any output is created.
    destination.mkdir()
    with zipfile.ZipFile(destination / f"kernel-{release}.zip", "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in runtime_files.items():
            archive.write(path, name)
    for name, path in ((source_name, source_files[source_name]), ("artifacts.json", runtime_files["artifacts.json"]),
                       ("RELEASE-NOTES.md", runtime_files["RELEASE-NOTES.md"])):
        shutil.copy2(path, destination / name)
    write_checksums(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--assemble-release", action="store_true")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--expected-commit")
    args = parser.parse_args()
    if args.assemble_release:
        if not all((args.runtime, args.source, args.destination)):
            parser.error("assembly requires --runtime, --source and --destination")
        assemble_release(args.runtime, args.source, args.destination, args.expected_commit)
        print("PASS: assembled verified runtime ZIP and complete corresponding source")
        return
    if any((args.runtime, args.source, args.destination, args.expected_commit)):
        parser.error("assembly paths/commit require --assemble-release")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", args.target):
        parser.error("invalid target name")
    if args.target not in ("6.18-ps4-baikal", "7.2-ps4-baikal-native-dev", "7.2-ps5"):
        parser.error("unsupported release target")
    repo = Path(__file__).resolve().parents[2]
    target = json.loads((repo / f"provenance/{args.target}.json").read_text())
    release = target["kernel_release"]
    output = repo / "output" / args.target
    source = Path(os.environ.get("PSX_KERNEL_BUILD_ROOT", repo)) / "src" / args.target
    if (output / "version.txt").read_text().strip() != release:
        raise ValueError("unexpected built release")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source).decode().strip() != target["upstream"]["commit"]:
        raise ValueError("unexpected upstream source commit")
    platform = "ps5" if args.target == "7.2-ps5" else "ps4"
    check_config((output / "config").read_text(), release, platform)
    check_config((source / ".config").read_text(), release, platform)
    if (source / ".config").read_bytes() != (output / "config").read_bytes():
        raise ValueError("source and built configurations differ")
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=output, check=True)
    patched = patched_source_paths(repo, target)
    names = source_files(source, patched, platform)
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
        module_name = "amdgpu" if platform == "ps5" else "psx_mts_net"
        relative_module = "drivers/gpu/drm/amd/amdgpu/amdgpu.ko" if platform == "ps5" else "drivers/net/ethernet/psx_mts_net.ko"
        module_path = f"lib/modules/{release}/kernel/{relative_module}"
        module = temporary / module_path
        if module.is_symlink() or not module.is_file():
            raise ValueError(f"missing matching {module_name} module")
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
            "module": {"name": module_name, "path": module_path, "sha256": module_digest,
                       "build_note_hex": note.read_bytes().hex()},
            "source_commit": commit, "upstream": target["upstream"],
            "workflow_run": run_url,
            "hardware_acceptance": target["hardware_evidence"]["this_target"],
            "packager_sha256": digest(Path(__file__)),
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
        shutil.copy2(Path(__file__), temporary / "PSX-PACKAGE-RELEASE.py")
        (temporary / "PSX-REBUILD.md").write_text(
            f"Complete patched Linux source and resolved .config for {release}.\n"
            "PSX-BUILD-INPUTS.tar.gz contains this project's source, patches, target and tools.\n"
            f"Extract those build inputs and run ./build.sh --target {args.target} on Linux,\n"
            "or scripts/build-kernel-orbstack on macOS ARM64. Native compiler and identity\n"
            "are recorded in PSX-BUILD-MANIFEST.txt; see the target and docs/BUILDING.md.\n"
            "Firmware is neither embedded nor bundled. No distribution is required.\n"
            "PSX-PACKAGE-RELEASE.py is the exact packaging script, including any changes\n"
            "after the Git checkpoint. Copy it to scripts/ci/package_release.py in the\n"
            "extracted build-input repository before packaging this target.\n"
        )
        files = temporary / "source-files.list"
        files.write_bytes(b"\0".join(os.fsencode(name) for name in names) + b"\0")
        subprocess.run([
            "tar", "--zstd", "--sort=name", f"--mtime=@{target['source_date_epoch']}",
            "--owner=0", "--group=0", "--numeric-owner", f"--transform=flags=r;s,^,linux-{release}/,",
            "-cf", str(destination / f"source-{release}.tar.zst"), "-C", str(source),
            "--null", "-T", str(files), "-C", str(temporary), "--no-null",
            "PSX-BUILD-INPUTS.tar.gz", "PSX-BUILD-MANIFEST.txt", "PSX-REBUILD.md", "PSX-PACKAGE-RELEASE.py",
        ], check=True, env={**os.environ, "ZSTD_CLEVEL": "8", "ZSTD_NBTHREADS": "2"})
    source_archive = destination / f"source-{release}.tar.zst"
    metadata["source_archive"] = {"path": source_archive.name, "sha256": digest(source_archive)}
    (destination / "artifacts.json").write_text(json.dumps(metadata, indent=2) + "\n")
    # Bundle generic guidance; links outside the bundle point at this commit.
    for name in ["BOOTING", "ETHERNET", "HARDWARE-TESTING", "ARTIFACTS"]:
        guide = (repo / f"docs/{name}.md").read_text()
        guide = guide.replace("../platforms/ps4/STATUS.md", f"https://github.com/meerzulee/linux-psx/blob/{commit}/platforms/ps4/STATUS.md")
        guide = guide.replace("../provenance/", f"https://github.com/meerzulee/linux-psx/blob/{commit}/provenance/")
        (destination / f"{name}.md").write_text(guide)
    (destination / "RELEASE-NOTES.md").write_text(release_notes(target, commit, run_url))
    write_checksums(destination)
    stage_ci_artifacts(destination, release)
    print(f"PASS: packaged {release} as separate runtime and complete-source downloads")


if __name__ == "__main__":
    main()
