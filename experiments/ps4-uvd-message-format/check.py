#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile the CPU-only serializer against pinned public byte references."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import traceback

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
PUBLIC = Path("lab/uvd-driver-goal-2026-10-20")
INPUTS = {
    "linux.c": (PUBLIC / "public-path/upstream/amdgpu_uvd.c",
        "6b8c41d474f950b1de4fa57a03498915fa4ed0208341de5655379cdb81e5318d"),
    "mesa.h": (PUBLIC / "public-mesa/source-26.1.7/src/amd/common/ac_uvd_dec.h",
        "fde6080faf355b00d373f8f72e109461f1072b6290d17f93fe8d5e1bb58842e1"),
    "mesa.c": (PUBLIC / "public-mesa/source-26.1.7/src/amd/common/ac_uvd_dec.c",
        "f9ace763a478dba1264cece9af0011065e8ded5fdf5f8e41c95d3acd2d843870"),
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save_json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def run(command, output, label, env=None):
    proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=30, env=env, check=False)
    (output / (label + ".stdout")).write_bytes(proc.stdout)
    (output / (label + ".stderr")).write_bytes(proc.stderr)
    save_json(output / (label + ".json"), {"argv": command, "returncode": proc.returncode})
    return proc


def require(proc, label):
    if proc.returncode:
        raise ValueError(label + " failed; output preserved")


def references(inputs, output):
    linux, mesa = inputs["linux.c"].decode(), inputs["mesa.c"].decode()
    fragments, golden = {}, {}
    for kind, count in (("create", 11), ("destroy", 4)):
        body = linux.split("int amdgpu_uvd_get_" + kind + "_msg(", 1)[1]
        body = body.split("/* stitch together an UVD " + kind + " msg */\n", 1)[1]
        statements = body.split("\n\n", 1)[0] + "\n"
        assignments = re.findall(r"msg\[(\d+)\] = cpu_to_le32\((handle|0x[0-9a-f]+)\);", statements)
        if [int(index) for index, _ in assignments] != list(range(count)):
            raise ValueError("public Linux assignment extraction")
        if f"for (i = {count}; i < 1024; ++i)\n\t\tmsg[i] = cpu_to_le32(0x0);" not in statements:
            raise ValueError("public Linux zero tail extraction")
        page = bytearray(4096)
        for index, value in assignments:
            struct.pack_into("<I", page, int(index) * 4,
                             0x11223344 if value == "handle" else int(value, 16))
        golden[kind] = bytes(page)
        (output / ("golden-linux-" + kind + "-11223344.bin")).write_bytes(page)
        fragments["linux_" + kind] = (
            f"static void reference_linux_{kind}(uint32_t *msg, uint32_t handle)\n"
            "{\n\tint i;\n" + statements + "}\n")
        body = mesa.split("uvd_build_" + kind + "_cmd(", 1)[1]
        statements = "   memset(msg, 0, sizeof(*msg));" + body.split(
            "   memset(msg, 0, sizeof(*msg));", 1)[1].split("\n\n   struct cmd_buffer", 1)[0]
        if kind == "create":
            signature = "uint32_t width, uint32_t height"
            declarations = (
                "   struct { uint32_t stream_handle, stream_type; } data = {handle, RUVD_CODEC_H264}, *dec = &data;\n"
                "   struct { struct { uint32_t max_width, max_height; } param; } dims = {{width, height}}, *decoder = &dims;\n")
        else:
            signature, declarations = "", "   struct { uint32_t stream_handle; } data = {handle}, *dec = &data;\n"
        fragments["mesa_" + kind] = (
            f"static void reference_mesa_{kind}(struct ruvd_msg *msg, uint32_t handle"
            + (", " + signature if signature else "") + ")\n{\n" +
            declarations + statements + "\n}\n")
    notice = linux.split("#include", 1)[0] + mesa.split("#include", 1)[0]
    header = notice + '\n#include "reference/ac_uvd_dec.h"\n\n'
    header += "\n".join(fragments.values())
    for kind, count in (("create", 44), ("destroy", 16)):
        header += "\nstatic const unsigned char golden_linux_" + kind + "[4096] = {\n   "
        header += ", ".join("0x%02x" % byte for byte in golden[kind][:count]) + "\n};\n"
    (output / "public_reference.h").write_text(header)
    return {"fragments_sha256": {name: sha(text.encode()) for name, text in fragments.items()},
            "reference_header_sha256": sha(header.encode()),
            "golden_page_sha256": {kind: sha(page) for kind, page in golden.items()}}


def check(workspace, output, cc):
    output.mkdir(parents=True, exist_ok=False)
    try:
        sources = {}
        for name in ("ps4_uvd_message.c", "ps4_uvd_message.h", "test_message.c", "check.py"):
            sources[name] = (HERE / name).read_bytes()
            (output / name).write_bytes(sources[name])
        inputs = {}
        input_dir = output / "inputs"
        input_dir.mkdir()
        for name, (path, digest) in INPUTS.items():
            raw = (workspace / path).read_bytes()
            if sha(raw) != digest:
                raise ValueError("public source identity mismatch: " + name)
            inputs[name] = raw
            (input_dir / name).write_bytes(raw)
        ref = output / "reference"
        ref.mkdir()
        (ref / "ac_uvd_dec.h").write_bytes(inputs["mesa.h"])
        (ref / "ac_video_dec.h").write_text(
            "/* Host layout-only dependency shim; no fields or code substituted. */\n"
            "#include <stdint.h>\nstruct radeon_info;\n"
            "struct ac_video_dec_session_param;\nstruct ac_video_dec;\n")
        shim = output / "shim/linux"
        shim.mkdir(parents=True)
        (shim / "types.h").write_text(
            "#include <stdint.h>\n#include <stddef.h>\ntypedef uint8_t u8;\ntypedef uint32_t u32;\n")
        (shim / "string.h").write_text("#include <string.h>\n")
        (shim / "errno.h").write_text("/* Host equivalent of kernel EINVAL. */\n#define EINVAL 22\n")
        reference = references(inputs, output)
        compiler = str(Path(cc).resolve())
        require(run([compiler, "--version"], output, "compiler"), "compiler identity")
        flags = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I" + str(output / "shim"), "-I" + str(output)]
        test = str(output / "test_message.c")
        implementation = output / "ps4_uvd_message.c"
        optimized = output / "optimized"
        require(run(flags + ["-O2", test, str(implementation), "-o", str(optimized)],
                    output, "optimized-compile"), "optimized compile")
        first = run([str(optimized)], output, "optimized-run")
        require(first, "optimized run")
        sanitized = output / "sanitized"
        require(run(flags + ["-O1", "-g", "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer", test, str(implementation), "-o", str(sanitized)],
                    output, "sanitized-compile"), "sanitized compile")
        second = run([str(sanitized)], output, "sanitized-run",
                     dict(os.environ, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1",
                          UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1"))
        require(second, "sanitized run")
        if first.stdout != second.stdout or not first.stdout.startswith(b"PASS: 33 actual-source"):
            raise ValueError("run output agreement")
        mutants = (
            ("length", "page + 0, PS4_UVD_MSG_DECLARED_BYTES", "page + 0, PS4_UVD_MSG_PAGE_BYTES", "DECLARED_LENGTH"),
            ("dpb-offset", "page + 40, values.dpb_size", "page + 36, values.dpb_size", "LINUX_DPB"),
            ("tail", "memset(page, 0, PS4_UVD_MSG_PAGE_BYTES)", "memset(page, 0, PS4_UVD_MSG_DECLARED_BYTES)", "PAGE_ZERO"),
            ("destroy-type", "ps4_uvd_msg_common(page, 2, handle)", "ps4_uvd_msg_common(page, 1, handle)", "DESTROY_TYPE"),
            ("zero-handle", "bytes != PS4_UVD_MSG_PAGE_BYTES || !handle)", "bytes != PS4_UVD_MSG_PAGE_BYTES)", "ZERO_HANDLE_UNCHANGED"),
        )
        witnesses = []
        for name, old, new, expected in mutants:
            text = sources["ps4_uvd_message.c"].decode()
            if text.count(old) != 1:
                raise ValueError("mutant anchor: " + name)
            text = text.replace(old, new, 1)
            source, binary = output / ("mutant-" + name + ".c"), output / ("mutant-" + name)
            source.write_text(text)
            require(run(flags + ["-O2", test, str(source), "-o", str(binary)],
                        output, name + "-compile"), "mutant compile")
            result = run([str(binary)], output, name + "-run")
            if result.returncode == 0 or not result.stderr.startswith(("FAIL:" + expected + " line=").encode()):
                raise ValueError("exact mutant witness: " + name)
            witnesses.append({"name": name, "witness": expected, "returncode": result.returncode,
                              "source_sha256": sha(text.encode()), "stderr_sha256": sha(result.stderr)})
        receipt = {"schema": 1, "status": "PASS_PUBLIC_MESSAGE_SERIALIZER_HOST_CHECKS",
            "cases": 33, "optimized_pass": True, "asan_ubsan_pass": True, "sensitivity": witnesses,
            "sources": {name: sha(raw) for name, raw in sources.items()},
            "public_inputs": {name: {"path": str(path), "sha256": digest}
                              for name, (path, digest) in INPUTS.items()},
            "reference": reference, "compiler": compiler,
            "compiler_version_sha256": sha((output / "compiler.stdout").read_bytes()),
            "python": sys.version, "platform": platform.platform(),
            "shim_sha256": {str(path.relative_to(output)): sha(path.read_bytes())
                           for path in (ref / "ac_video_dec.h", shim / "types.h", shim / "string.h", shim / "errno.h")},
            "hardware_actions": False, "hardware_acceptance": False,
            "ps4_firmware_abi_review": False, "kernel_built": False,
            "limits": ["Public message bytes only; no submission, allocation or lifetime behavior",
                       "Mesa's message population statements and layout header are tested; no Mesa command emitter executes",
                       "Dimension and DPB values are serialized, not validated as supported hardware/session parameters",
                       "Historical interrupted Claude message advisory remains incomplete"]}
        save_json(output / "receipt.json", receipt)
        return receipt
    except BaseException as error:
        save_json(output / "FAILURE.json", {"error": repr(error), "traceback": traceback.format_exc()})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cc", default=shutil.which("cc"))
    args = parser.parse_args()
    if not args.cc:
        parser.error("native C compiler required")
    result = check(args.workspace.resolve(), args.output.resolve(), args.cc)
    print(json.dumps({"status": result["status"], "cases": result["cases"],
                      "mutants": len(result["sensitivity"])}, sort_keys=True))
