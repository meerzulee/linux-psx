#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Capture exact CREATE build ELF evidence for manual review, without execution."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

PREFIX = "drivers/gpu/drm/amd/amdgpu/"
OBJECTS = ("amdgpu_ps4_uvd_ready", "amdgpu_ps4_map_canary", "amdgpu_ps4_uvd_irq",
           "gmc_v7_0", "ps4_uvd_message", "ps4_uvd_message_job")
ROOTS = ("amdgpu_ps4_uvd_ready_run", "amdgpu_ps4_uvd_message_create",
         "amdgpu_ps4_map_canary_run", "amdgpu_ps4_uvd_irq_fault",
         "ps4_create_", "ps4_uvd_message_job_build", "ps4_uvd_msg_create_")


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save(path, data):
    with path.open("x") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build-tool", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--manifest-sha256", required=True)
    ap.add_argument("--build-sha256", required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    assert sha(args.manifest) == args.manifest_sha256, "Manifest changed"
    manifest = json.loads(args.manifest.read_bytes())
    assert sha(args.build_tool) == manifest["tool_sha256"], "Build tool changed"
    spec = importlib.util.spec_from_file_location("frozen_native_build", args.build_tool)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    tool.manifest_shape(manifest)
    work = Path(manifest["work"])
    built = work / "BUILD.json"
    assert sha(built) == args.build_sha256, "Build receipt changed"
    report = json.loads(built.read_bytes())
    assert report["status"] == "PASS_BUILD" and report["manifest_sha256"] == args.manifest_sha256
    source = work / "source"
    inventory = work / "BUILT-SOURCE.json"
    assert sha(inventory) == report["built_source_sha256"], "Built inventory changed"
    expected = json.loads(inventory.read_bytes())
    for name, row in manifest["candidate_files"].items():
        assert tool.file_record(source / name) == expected[name]
        assert row["sha256"] == expected[name]["sha256"], name
    outputs = {name: (source / "vmlinux" if name == "vmlinux" else work / "output" / name)
               for name in report["artifact_sha256"]}
    for name, path in outputs.items():
        assert sha(path) == report["artifact_sha256"][name], name
    out = args.output.resolve()
    assert not out.is_relative_to(work) and not work.is_relative_to(out), "Overlapping output"
    out.mkdir(parents=True, exist_ok=False)
    (out / "elf").mkdir()
    commands = []

    def capture(name, argv):
        path = out / name
        with path.open("xb") as stdout, (out / (name + ".stderr")).open("xb") as stderr:
            result = subprocess.run(list(map(str, argv)), stdout=stdout, stderr=stderr, timeout=180)
        row = {"argv": list(map(str, argv)), "returncode": result.returncode,
               "stdout": name, "stdout_sha256": sha(path),
               "stderr_sha256": sha(out / (name + ".stderr"))}
        commands.append(row)
        save(out / (name + ".command.json"), row)
        assert result.returncode == 0, name
        return path

    try:
        objects = {}
        for stem in OBJECTS:
            obj = source / PREFIX / (stem + ".o")
            cmd = source / PREFIX / ("." + stem + ".o.cmd")
            before = sha(obj)
            shutil.copyfile(obj, out / "elf" / obj.name)
            shutil.copyfile(cmd, out / (stem + ".compile-command.txt"))
            assert sha(out / "elf" / obj.name) == before
            capture(stem + ".disassembly.txt", ["/usr/bin/objdump", "-dr", "-Mintel", obj])
            capture(stem + ".elf.txt", ["/usr/bin/readelf", "-W", "-S", "-s", "-r", obj])
            capture(stem + ".symbols.txt", ["/usr/bin/nm", "-S", "--size-sort", obj])
            assert sha(obj) == before
            objects[stem] = {"sha256": before, "bytes": obj.stat().st_size,
                             "source_sha256": expected[PREFIX + stem + ".c"]["sha256"],
                             "compile_command_sha256": sha(cmd)}
        vmlinux = source / "vmlinux"
        shutil.copyfile(vmlinux, out / "elf/vmlinux")
        assert sha(out / "elf/vmlinux") == report["artifact_sha256"]["vmlinux"]
        symbols = capture("vmlinux-symbols.txt", ["/usr/bin/nm", "-S", "--defined-only", vmlinux])
        names = {line.split()[-1] for line in symbols.read_text().splitlines() if line.split()}
        selected = sorted(n for n in names if n.startswith(ROOTS))
        assert "amdgpu_ps4_uvd_ready_run" in names and "ps4_uvd_message_job_build" in names
        for symbol in selected:
            path = capture("linked-" + symbol + ".txt",
                           ["/usr/bin/objdump", "-d", "-Mintel", "--disassemble=" + symbol, vmlinux])
            assert "<" + symbol + ">:" in path.read_text(), symbol
        ready_obj = source / PREFIX / "amdgpu_ps4_uvd_ready.o"
        capture("create-parameters.txt", ["/usr/bin/objdump", "-s", "-j", "__param", ready_obj])
        for name in ("amdgpu_device", "amdgpu_ps4_uvd_create_operation",
                     "amdgpu_ps4_uvd_ready_operation", "ps4_uvd_fault_snapshot"):
            capture("type-" + name + ".txt", ["/usr/bin/pahole", "-C", name, ready_obj])
        diagnostics = []
        for name in ("kernel-build.stdout", "kernel-build.stderr"):
            raw = (work / "logs/build" / name).read_text(errors="replace")
            diagnostics += [line for line in raw.splitlines() if "warning:" in line or "error:" in line]
        (out / "compiler-diagnostics.txt").write_text("\n".join(diagnostics) + "\n")
        for name, path in outputs.items():
            assert sha(path) == report["artifact_sha256"][name], name
        assert sha(built) == args.build_sha256 and sha(args.manifest) == args.manifest_sha256
        save(out / "CAPTURE.json", {
            "schema": 1, "status": "CAPTURED_CREATE_ELF_REVIEW_PENDING",
            "manifest_sha256": args.manifest_sha256, "build_sha256": args.build_sha256,
            "script_sha256": sha(Path(__file__)), "objects": objects,
            "artifact_sha256": report["artifact_sha256"], "linked_symbols": selected,
            "tools": {name: sha(Path('/usr/bin') / name) for name in ("objdump", "readelf", "nm", "pahole")},
            "commands": commands, "compiled_review_complete": False,
            "hardware_acceptance": False, "limits": ["Static capture only; manual review remains required.",
                "No target execution, firmware, console, UART or media operation."]})
        print(json.dumps({"status": "CAPTURED_CREATE_ELF_REVIEW_PENDING", "objects": len(objects),
                          "linked_symbols": len(selected)}))
    except Exception as error:
        save(out / "FAILURE.json", {"error": str(error), "commands": commands})
        raise


if __name__ == "__main__":
    main()
