#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Independent actual-source frame-controller host checks; no device control."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
PREFIX = Path("drivers/gpu/drm/amd/amdgpu")
HEADER_PINS = {
    "uvd/uvd_5_0_d.h": "ddee9d3d00fa475cd3f55813edcfb1955febc33300520483d38a0c017674faee",
    "uvd/uvd_5_0_sh_mask.h": "5d335f462b22162402b53b0d6cf5ff0f97114996171749d6e99803da19bc7612",
    "gmc/gmc_7_1_d.h": "389df3c6e6d530a05b81ca5ab7e4e58c48cb65ea746caab32c50a862ac0c287e",
}
HELPERS = ("ps4_ready_ctx", "ps4_token_descriptor_valid", "ps4_token_expect",
           "ps4_stobs_status_expect", "ps4_frame_core_sample", "ps4_stobs_ctx_sweep",
           "ps4_create_save_core", "ps4_create_word", "ps4_create_put", "ps4_frame_history")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def masked(text):
    return re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"',
                  lambda m: "".join("\n" if c == "\n" else " " for c in m[0]), text, flags=re.S)


def function(text, name):
    clean = masked(text)
    m = re.search(r"\b" + name + r"\s*\([^;{}]*\)\s*\{", clean)
    if not m:
        raise ValueError("missing function " + name)
    start = clean.rfind("\n", 0, m.start()) + 1
    opening = clean.index("{", m.start())
    depth, end = 1, opening + 1
    while depth:
        depth += (clean[end] == "{") - (clean[end] == "}")
        end += 1
    return text[start:end]


def marked(text, name):
    m = re.search(r"^[ \t]*/\* " + name + r"_BEGIN\b.*?^[ \t]*/\* " + name + r"_END \*/\n",
                  text, re.M | re.S)
    if not m:
        raise ValueError("missing block " + name)
    return m[0]


def definitions(text):
    result = {}
    lines = iter(text.splitlines(True))
    for line in lines:
        m = re.match(r"#define\s+(\w+)\s+", line)
        if m:
            value = line
            while value.endswith("\\\n"):
                value += next(lines)
            result[m[1]] = value
    return result


def run_command(argv, output, name, env=None):
    p = subprocess.run(list(map(str, argv)), capture_output=True, timeout=60, env=env)
    (output / (name + ".stdout")).write_bytes(p.stdout)
    (output / (name + ".stderr")).write_bytes(p.stderr)
    record = {"argv": list(map(str, argv)), "returncode": p.returncode,
              "stdout": p.stdout.decode(errors="replace"), "stderr": p.stderr.decode(errors="replace")}
    (output / (name + ".json")).write_text(json.dumps(record, indent=2) + "\n")
    return record


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("nonunique fixture anchor: " + repr(old[:120]))
    return text.replace(old, new, 1)


def check(workspace, candidate, headers, output, cc, frozen_source=False):
    if output.exists():
        raise ValueError("preserve prior check; use a new output directory")
    source_receipt = (candidate / "SOURCE-RECEIPT.json").read_bytes()
    receipt = json.loads(source_receipt)
    source = {}
    for name, expected in receipt["candidate_files"].items():
        raw = (candidate / "candidate" / PREFIX / name).read_bytes()
        if sha(raw) != expected:
            raise ValueError("candidate identity changed: " + name)
        source[name] = raw
    ready = source["amdgpu_ps4_uvd_ready.c"].decode()
    flow = marked(ready, "DECODE_FRAME_FLOW")
    live_flow_match = flow == (HERE / "flow.c.inc").read_text()
    live_operation_match = source["amdgpu_ps4_uvd_frame.h"] == (HERE / "operation.h").read_bytes()
    if not live_flow_match and not frozen_source:
        raise ValueError("candidate flow differs from live flow input")
    if not live_operation_match and not frozen_source:
        raise ValueError("candidate operation differs from live operation input")
    helpers = "\n\n".join(function(ready, name) for name in HELPERS)
    params = function(ready, "amdgpu_ps4_uvd_ready_enabled") + "\n" + marked(ready, "DECODE_FRAME_PARAMETER")
    type_text = re.search(r"enum ps4_token_phase \{.*?\};", ready, re.S)[0] + "\n" + \
        re.search(r"struct ps4_token_sample \{.*?\};", ready, re.S)[0]
    register_text = ""
    for path, expected in HEADER_PINS.items():
        raw = (headers / path).read_bytes()
        if sha(raw) != expected:
            raise ValueError("register source identity changed: " + path)
        register_text += raw.decode()
    defs = definitions(register_text)
    defs.update(definitions(ready))
    defs.update(definitions(source["amdgpu_ps4_uvd_ring_plan.h"].decode()))
    # Include only referenced object-like macros and their transitive inputs.
    defined_locally = set(definitions(flow)) | set(definitions(source["amdgpu_ps4_uvd_frame.h"].decode()))
    needed = set(re.findall(r"\b[A-Za-z_]\w*\b", helpers + params + flow + (HERE / "frame_flow_fixture.c").read_text()))
    selected = set()
    while True:
        more = (needed & set(defs)) - selected - defined_locally
        if not more:
            break
        selected |= more
        for key in more:
            needed.update(re.findall(r"\b[A-Za-z_]\w*\b", defs[key]))
    constants = "".join(defs[key] for key in defs if key in selected)
    expected_raw = (workspace / "lab/uvd-2026-10-20/decode-fixtures/run-001/expected.nv12").read_bytes()
    if sha(expected_raw) != "12404191ab945b7478093a852545adfbaa9af44bd32f83ccf24f489e385628df":
        raise ValueError("reference output identity changed")
    expected = "static const u8 expected_nv12[6144] = {\n" + \
        ",\n".join(",".join(str(x) for x in expected_raw[i:i+32]) for i in range(0, len(expected_raw), 32)) + "\n};\n"
    generated = (HERE / "frame_flow_fixture.c").read_text()
    for key, value in {"CONSTANTS": constants, "TYPES": type_text,
                       "FIXTURE": source["amdgpu_ps4_uvd_frame_fixture.h"].decode(),
                       "EXPECTED": expected, "HELPERS": helpers, "PARAMETERS": params, "FLOW": flow}.items():
        generated = once(generated, "@" + key + "@", value)
    if re.search(r"@[A-Z_]+@", generated):
        raise ValueError("unexpanded fixture marker")
    output.mkdir(parents=True)
    inc = output / "include"
    (inc / "linux").mkdir(parents=True)
    for name in ("ps4_uvd_message.h", "ps4_uvd_decode_message.h", "amdgpu_ps4_uvd_frame.h"):
        (inc / name).write_bytes(source[name])
    create_header = source["amdgpu_ps4_uvd_message_create.h"].decode()
    create_shim = marked(source["amdgpu_ps4_uvd_irq.h"].decode(), "GMC_FAULT_TYPES") + "\n" + \
        re.search(r"struct ps4_create_record \{.*?\};", create_header, re.S)[0] + "\n" + \
        re.search(r"struct ps4_create_saved_core \{.*?\};", create_header, re.S)[0]
    (inc / "amdgpu_ps4_uvd_message_create.h").write_text("#include <linux/types.h>\n" + create_shim + "\n")
    (inc / "linux/types.h").write_text("#pragma once\n#include <stdint.h>\n#include <stdbool.h>\n#include <stddef.h>\n"
        "typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64; typedef uint32_t __le32;\n")
    (inc / "linux/errno.h").write_text("#include_next <linux/errno.h>\n" if platform.system() == "Linux" else "#include <errno.h>\n")
    (inc / "linux/string.h").write_text("#include <string.h>\n")
    for name in ("ps4_uvd_message.c", "ps4_uvd_decode_message.c"):
        (output / name).write_bytes(source[name])
    fixture_path = output / "fixture.c"
    fixture_path.write_text(generated)
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = "detect_leaks=" + ("0" if platform.system() == "Darwin" else "1") + ":abort_on_error=1"
    env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    libraries = [] if platform.system() == "Darwin" else ["-lcrypto"]
    common = [cc, "-std=gnu11", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-I", str(inc)]
    linked = [str(output / "ps4_uvd_message.c"), str(output / "ps4_uvd_decode_message.c")]
    results = {}
    for mode, flags in (("optimized", ["-O2"]), ("sanitized", ["-O1", "-fno-omit-frame-pointer", "-fsanitize=address,undefined"])):
        exe = output / mode
        built = run_command(common + flags + [str(fixture_path)] + linked + libraries + ["-o", str(exe)], output, "compile-" + mode)
        if built["returncode"]:
            raise RuntimeError("fixture compile failed: " + built["stderr"])
        result = run_command([exe], output, "run-" + mode, env)
        if result["returncode"]:
            raise RuntimeError("fixture failed: " + result["stderr"])
        results[mode] = result
    mutations = {
        "one-observation": ("if (p->consecutive == 2)", "if (p->consecutive == 1)"),
        "deadline-equality": ("if (elapsed >= PS4_FRAME_LIMIT_NS)", "if (elapsed > PS4_FRAME_LIMIT_NS)"),
        "packed-luma-stride": ("target[row * 256 + x]", "target[row * 64 + x]"),
        "older-create-leaf": ("!ps4_uvd_ib_data && !ps4_uvd_message_create &&", "!ps4_uvd_ib_data &&"),
    }
    sensitivity = {}
    for name, (old, new) in mutations.items():
        mutant = output / (name + ".c")
        mutant.write_text(once(generated, old, new))
        exe = output / name
        built = run_command(common + ["-O2", str(mutant)] + linked + libraries + ["-o", str(exe)], output, "compile-" + name)
        if built["returncode"]:
            raise RuntimeError("sensitivity compile failed: " + built["stderr"])
        result = run_command([exe], output, "run-" + name, env)
        if result["returncode"] != 86:
            raise RuntimeError("lost sensitivity: " + name + " " + result["stderr"])
        sensitivity[name] = result
    report = {
        "schema": 1, "status": "HOST_FRAME_FLOW_CHECKS_ONLY", "hardware_acceptance": False,
        "source_receipt_sha256": sha(source_receipt), "candidate": str(candidate),
        "candidate_ready_sha256": sha(source["amdgpu_ps4_uvd_ready.c"]),
        "candidate_operation_sha256": sha(source["amdgpu_ps4_uvd_frame.h"]),
        "frozen_source_requested": frozen_source,
        "live_source_correspondence": {"flow": live_flow_match, "operation": live_operation_match},
        "flow_sha256": sha(flow.encode()), "generated_fixture_sha256": sha(generated.encode()),
        "test_source_sha256": {name: sha((HERE / name).read_bytes()) for name in ("frame_flow_check.py", "frame_flow_fixture.c")},
        "actual_extracted_helpers": {name: sha(function(ready, name).encode()) for name in HELPERS},
        "register_header_pins": HEADER_PINS, "results": results, "sensitivity": sensitivity,
        "compiler": run_command([cc, "--version"], output, "compiler-version"),
        "host": {"system": platform.system(), "architecture": platform.machine()},
        "limits": ["controlled CPU register, time, fault-history and output observations",
                   "actual controller/core/history/options/serializers and real SHA-256 are compiled",
                   "does not emulate or validate firmware, packets executing, DMA, device caches or timing",
                   "does not test outer boot wrapper admission or actual page mappings",
                   "no kernel compilation, SSH, UART, console, storage or boot-file operation"],
    }
    (output / "RECEIPT.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--candidate", type=Path, required=True)
    ap.add_argument("--headers", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--cc", default=shutil.which("cc") or "cc")
    ap.add_argument("--frozen-source", action="store_true", help="test a receipt-pinned older snapshot for a negative witness")
    a = ap.parse_args()
    r = check(a.workspace.resolve(), a.candidate.resolve(), a.headers.resolve(), a.output.resolve(), a.cc, a.frozen_source)
    print(json.dumps({"status": r["status"], "results": r["results"],
                      "receipt": str(a.output.resolve() / "RECEIPT.json")}, indent=2))
