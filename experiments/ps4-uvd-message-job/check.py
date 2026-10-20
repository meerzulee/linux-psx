#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""CPU-only actual-source planner and public packet-reference checks."""
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
PUBLIC = Path("lab/uvd-driver-goal-2026-10-20/public-path/upstream")
INPUTS = {
    "amdgpu_uvd.c": (PUBLIC / "amdgpu_uvd.c",
        "6b8c41d474f950b1de4fa57a03498915fa4ed0208341de5655379cdb81e5318d"),
    "uvd_v5_0.c": (PUBLIC / "uvd_v5_0.c",
        "7925b5f1488191041f3b58a115175b2100ffaf77e79fd6cbd8cac25630ce0543"),
    "uvd_5_0_d.h": (Path("lab/uvd-2026-10-20/driver-integration/native-control-check/runs/native-02/source/drivers/gpu/drm/amd/include/asic_reg/uvd/uvd_5_0_d.h"),
        "ddee9d3d00fa475cd3f55813edcfb1955febc33300520483d38a0c017674faee"),
    "cikd.h": (Path("linux-ps4-git/src/linux/drivers/gpu/drm/amd/amdgpu/cikd.h"),
        "330255afa7fe39696ef2d9c43a7f14d9f6c375d59f4564e3a3962ad670268373"),
}
DEPENDENCIES = {
    "ps4_uvd_message.c": "7cbb584b8a628e2726521e7e28436aa99ed17d7034bf8c0ddf62800cd2df57c2",
    "ps4_uvd_message.h": "95daa65583c4aeb13f39e8ecad62666fd38dc4666b0ba8a3efc71aed1896da97",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save_json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def run(command, output, label, env=None):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=30, env=env, check=False)
    (output / (label + ".stdout")).write_bytes(result.stdout)
    (output / (label + ".stderr")).write_bytes(result.stderr)
    save_json(output / (label + ".json"), {"argv": command, "returncode": result.returncode})
    return result


def require(result, label):
    if result.returncode:
        raise ValueError(label + " failed; see preserved output")


def macro(text, name):
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if re.match(r"#define[ \t]+" + re.escape(name) + r"(?=[ \t(])", line):
            result = line
            while line.rstrip().endswith("\\"):
                index += 1
                line = lines[index]
                result += line
            return result
    raise ValueError("macro absent: " + name)


def body(text, name):
    start = re.search(r"static void\s+" + re.escape(name) + r"\(", text).start()
    start = text.index("\n{\n", start) + 3
    return text[start:text.index("\n}\n", start)] + "\n"


def make_reference(inputs, output):
    linux, v5, cik = (inputs[name].decode() for name in ("amdgpu_uvd.c", "uvd_v5_0.c", "cikd.h"))
    fragments = {}
    packet_constants = macro(cik, "PACKET_TYPE0") + macro(cik, "PACKET0")
    for name in ("UVD_BASE_SI", "UVD_GPCOM_VCPU_DATA0", "UVD_GPCOM_VCPU_DATA1", "UVD_GPCOM_VCPU_CMD", "UVD_NO_OP"):
        packet_constants += macro(linux, name)
    send = linux.split("static int amdgpu_uvd_send_msg(", 1)[1]
    data = "\tdata[0] = " + send.split("\tdata[0] = ", 1)[1].split("\n\n", 1)[0] + "\n"
    populate = "\tib->ptr[0] = " + send.split("\tib->ptr[0] = ", 1)[1].split("\n\n", 1)[0] + "\n"
    fragments["message_ib"] = (
        "static void reference_message_ib(struct amdgpu_ib *ib, u64 addr)\n{\n"
        "\tu32 data[4], offset = UVD_BASE_SI;\n\tint i;\n" + data + populate + "}\n")
    for suffix, signature in (
        ("ib", "struct amdgpu_ring *ring, struct amdgpu_ib *ib"),
        ("fence", "struct amdgpu_ring *ring, u64 addr, u64 seq, unsigned int flags"),
    ):
        fragments[suffix] = "static void reference_emit_" + suffix + "(" + signature + ")\n{\n" + body(v5, "uvd_v5_0_ring_emit_" + suffix) + "}\n"
    # Only count=2 is modeled. Keep the upstream body verbatim while using a
    # signed host adapter argument to match its signed loop index under
    # -Wsign-compare; the production planner and warning flags are unchanged.
    fragments["nop"] = ("static void reference_insert_nop(struct amdgpu_ring *ring, int count)\n{\n"
                        '\tCHECK(count == 2, "REFERENCE_NOP_COUNT");\n' +
                        body(v5, "uvd_v5_0_ring_insert_nop") + "}\n")
    create = linux.split("/* stitch together an UVD create msg */\n", 1)[1].split("\n\n", 1)[0] + "\n"
    fragments["create"] = ("static void reference_linux_create(u32 *msg, u32 handle)\n{\n\tint i;\n" +
                           create + "}\n")
    notices = linux.split("#include", 1)[0] + v5.split("#include", 1)[0] + cik.split("#ifndef", 1)[0]
    reference = notices + '\n#include "inputs/uvd_5_0_d.h"\n' + packet_constants + "\n" + "\n".join(fragments.values())
    (output / "public_reference.h").write_text(reference)
    # Literal golden corresponding to the fixed synthetic layout in test_job.c.
    main = [0x3c67, 0x0030a000, 0x3c66, 3, 0x3da2, 16, 0x3dbd, 0xa1b2c3d4,
            0x3bc4, 0x0030c040, 0x3bc5, 3, 0x3bc3, 0, 0x3bff, 0]
    ib = [0x3bc4, 0x0030b000, 0x3bc5, 3, 0x3bc3, 0] + [0x3bff, 0] * 5
    goldens = {}
    for name, words in (("main", main), ("ib", ib)):
        raw = struct.pack("<16I", *words)
        (output / ("golden-" + name + ".bin")).write_bytes(raw)
        goldens[name] = sha(raw)
    return {"header_sha256": sha(reference.encode()),
            "fragment_sha256": {name: sha(text.encode()) for name, text in fragments.items()},
            "golden_sha256": goldens}


def check(workspace, output, cc):
    output.mkdir(parents=True, exist_ok=False)
    try:
        sources = {}
        for name in ("ps4_uvd_message_job.c", "ps4_uvd_message_job.h", "test_job.c", "check.py"):
            sources[name] = (HERE / name).read_bytes()
            (output / name).write_bytes(sources[name])
        for name, expected in DEPENDENCIES.items():
            raw = (workspace / "linux-psx/experiments/ps4-uvd-message-format" / name).read_bytes()
            if sha(raw) != expected:
                raise ValueError("reviewed serializer changed: " + name)
            (output / name).write_bytes(raw)
        (output / "inputs").mkdir()
        inputs = {}
        for name, (path, expected) in INPUTS.items():
            raw = (workspace / path).read_bytes()
            if sha(raw) != expected:
                raise ValueError("public reference changed: " + name)
            inputs[name] = raw
            (output / "inputs" / name).write_bytes(raw)
        reference = make_reference(inputs, output)
        shim = output / "shim/linux"
        shim.mkdir(parents=True)
        (shim / "types.h").write_text("#include <stdint.h>\n#include <stddef.h>\n#include <stdbool.h>\ntypedef uint8_t u8;\ntypedef uint32_t u32;\ntypedef uint64_t u64;\n")
        (shim / "errno.h").write_text("#define EINVAL 22\n#define ERANGE 34\n")
        (shim / "string.h").write_text("#include <string.h>\n")
        compiler = str(Path(cc).resolve())
        require(run([compiler, "--version"], output, "compiler"), "compiler identity")
        flags = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I" + str(output / "shim"), "-I" + str(output)]
        shared = [str(output / "test_job.c"), str(output / "ps4_uvd_message.c")]
        planner = output / "ps4_uvd_message_job.c"
        optimized = output / "optimized"
        require(run(flags + ["-O2"] + shared + [str(planner), "-o", str(optimized)],
                    output, "optimized-compile"), "optimized compile")
        first = run([str(optimized)], output, "optimized-run")
        require(first, "optimized run")
        sanitized = output / "sanitized"
        require(run(flags + ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"] +
                    shared + [str(planner), "-o", str(sanitized)],
                    output, "sanitized-compile"), "sanitized compile")
        second = run([str(sanitized)], output, "sanitized-run", dict(os.environ,
                     ASAN_OPTIONS="detect_leaks=0:halt_on_error=1",
                     UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1"))
        require(second, "sanitized run")
        if first.stdout != second.stdout or not first.stdout.startswith(b"PASS: 42 actual-source"):
            raise ValueError("case count/output agreement")
        mutants = (
            ("ib-size-bytes", "JOB_IB_SIZE, PS4_UVD_JOB_IB_DW)", "JOB_IB_SIZE, PS4_UVD_JOB_IB_DW * 4)", "REFERENCE_MAIN"),
            ("overlap-ignored", "if (in.main_va < backing_end && in.backing_va < main_end)", "if (false)", "OVERLAP_REJECT"),
            ("address40-ignored", "if (fence_va > (1ULL << 40) - 4)", "if (false)", "ADDRESS_40BIT_REJECT"),
            ("trap-instead-nop", "job_pair(out->main_le, 14, JOB_NOP, 0)", "job_pair(out->main_le, 14, JOB_CMD, 2)", "TRAP_OMITTED_NOP"),
            ("overflow-ignored", "if (base > ~(u64)0 - bytes)", "if (false)", "OVERFLOW_REJECT"),
            ("early-output-write", "in = *input;", "in = *input;\n\tout->layout.sequence = 0;", "REJECT_UNCHANGED"),
        )
        witnesses = []
        for name, old, new, expected in mutants:
            text = sources["ps4_uvd_message_job.c"].decode()
            if text.count(old) != 1:
                raise ValueError("mutant anchor: " + name)
            text = text.replace(old, new, 1)
            path, binary = output / ("mutant-" + name + ".c"), output / ("mutant-" + name)
            path.write_text(text)
            require(run(flags + ["-O2"] + shared + [str(path), "-o", str(binary)],
                        output, name + "-compile"), "mutant compile")
            result = run([str(binary)], output, name + "-run")
            if result.returncode == 0 or not result.stderr.startswith(("FAIL:" + expected + " line=").encode()):
                raise ValueError("exact mutant witness: " + name)
            witnesses.append({"name": name, "witness": expected, "returncode": result.returncode,
                              "source_sha256": sha(text.encode()), "stderr_sha256": sha(result.stderr)})
        report = {"schema": 1, "status": "PASS_PUBLIC_MESSAGE_JOB_HOST_CHECKS", "cases": 42,
            "optimized_pass": True, "asan_ubsan_pass": True, "sensitivity": witnesses,
            "sources": {name: sha(raw) for name, raw in sources.items()},
            "serializer_dependency_sha256": DEPENDENCIES,
            "public_inputs": {name: {"path": str(path), "sha256": expected}
                              for name, (path, expected) in INPUTS.items()},
            "reference": reference, "compiler": compiler,
            "compiler_version_sha256": sha((output / "compiler.stdout").read_bytes()),
            "python": sys.version, "platform": platform.platform(),
            "kernel_built": False, "hardware_actions": False, "hardware_acceptance": False,
            "limits": ["CPU planning only; addresses are arithmetic inputs, not validated DMA mappings",
                       "One CREATE IB plus polled 32-bit fence prefix; upstream trap suffix intentionally absent",
                       "Not the full upstream self-test, a completion/lifetime policy, or PS4 firmware ABI acceptance",
                       "Main/IB page tails and fence initialization/guards are outside this plan"]}
        save_json(output / "receipt.json", report)
        return report
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
