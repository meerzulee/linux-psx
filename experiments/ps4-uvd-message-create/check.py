#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Replay the exact patch and exercise extracted controller and borrowed helpers."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import traceback

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("create_builder", HERE / "build_candidate.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
PREFIX = builder.PREFIX
HEADERS = {
    "uvd/uvd_5_0_d.h": "ddee9d3d00fa475cd3f55813edcfb1955febc33300520483d38a0c017674faee",
    "uvd/uvd_5_0_sh_mask.h": "5d335f462b22162402b53b0d6cf5ff0f97114996171749d6e99803da19bc7612",
    "gmc/gmc_7_1_d.h": "389df3c6e6d530a05b81ca5ab7e4e58c48cb65ea746caab32c50a862ac0c287e",
}

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def json_write(path, value):
    with path.open("x") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")

def marked(text, name):
    m = re.search(r"^[ \t]*/\* " + name + r"_BEGIN\b.*?^[ \t]*/\* " + name +
                  r"_END \*/\n", text, re.M | re.S)
    if not m:
        raise ValueError("missing marked block " + name)
    return m.group()

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

def definitions(text):
    out = {}
    lines = iter(text.splitlines(True))
    for line in lines:
        m = re.match(r"#define\s+(\w+)\s+", line)
        if m:
            value = line
            while value.endswith("\\\n"):
                value += next(lines)
            out[m[1]] = value
    return out

def command(argv, output, name, cwd=None, env=None, timeout=60):
    p = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    (output / (name + ".stdout")).write_bytes(p.stdout)
    (output / (name + ".stderr")).write_bytes(p.stderr)
    json_write(output / (name + ".json"), {
        "argv": list(map(str, argv)), "cwd": str(cwd) if cwd else None,
        "returncode": p.returncode, "stdout_sha256": sha(p.stdout), "stderr_sha256": sha(p.stderr)})
    return p

def passed(p, name):
    if p.returncode:
        raise ValueError(name + " failed; see saved logs")

def verify(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tools = output / "tools"
    tools.mkdir()
    for name in ("check.py", "fixture.c", "build_candidate.py", "operation.h", "flow.c.inc"):
        shutil.copyfile(HERE / name, tools / name)
    stage = "source"
    try:
        receipt_raw = (args.candidate / "SOURCE-RECEIPT.json").read_bytes()
        receipt = json.loads(receipt_raw)
        if receipt["base_manifest_sha256"] != builder.BASE_MANIFEST:
            raise ValueError("wrong composed baseline")
        if receipt["generator_sha256"] != sha((HERE / "build_candidate.py").read_bytes()):
            raise ValueError("generator changed")
        for name, expected in receipt["generator_inputs"].items():
            if sha((HERE / name).read_bytes()) != expected:
                raise ValueError("generator input changed")
        sources = {}
        for category in ("baseline", "candidate"):
            sources[category] = {}
            for name, expected in receipt[category + "_files"].items():
                raw = (args.candidate / category / PREFIX / name).read_bytes()
                if sha(raw) != expected:
                    raise ValueError("source changed " + name)
                sources[category][name] = raw
        old, new = sources["baseline"], sources["candidate"]
        rebuilt = builder.transformations(old)
        rebuilt.update({n: new[n] for n in receipt["library_pins"]})
        if rebuilt != new:
            raise ValueError("nonreproducible source")
        ready = new[builder.READY].decode()
        old_ready = old[builder.READY].decode()
        canary = new[builder.CANARY].decode()
        identity = {}
        for key in ("TOKEN_FLOW", "DATA_CONTROL_FLOW", "STATUS_OBS_FLOW", "IB_DATA_FLOW",
                    "READY_CORE_FUNCTIONS"):
            if marked(ready, key) != marked(old_ready, key):
                raise ValueError("historical flow changed " + key)
            identity[key] = sha(marked(ready, key).encode())
        for key in ("IB_DATA_OWNED_PAGE", "IB_DATA_BORROW_DECL", "IB_DATA_BORROW"):
            if marked(canary, key) != marked(old[builder.CANARY].decode(), key):
                raise ValueError("historical owner branch changed")
            identity[key] = sha(marked(canary, key).encode())
        for name in ("ps4_ready_ctx", "ps4_token_expect", "ps4_token_descriptor_valid",
                     "ps4_stobs_status_expect", "ps4_ib_data_core_sample", "ps4_stobs_ctx_sweep"):
            if function(ready, name) != function(old_ready, name):
                raise ValueError("borrowed helper changed " + name)
            identity[name] = sha(function(ready, name).encode())
        flow = marked(ready, "MESSAGE_CREATE_FLOW")
        if flow != (HERE / "flow.c.inc").read_text():
            raise ValueError("flow extraction mismatch")
        stores = re.findall(r"WREG32\((\w+),\s*([^)]+)\)", masked(flow))
        if stores != [("mmUVD_CONTEXT_ID", "PS4_CREATE_CONTEXT_BASELINE"),
                      ("mmUVD_RBC_RB_CNTL", "PS4_TOKEN_RBC_FETCH"),
                      ("mmUVD_RBC_RB_WPTR", "PS4_UVD_JOB_MAIN_DW")]:
            raise ValueError("direct store contract changed " + repr(stores))
        loop = flow[flow.index("\tfor (;;) {"):flow.index("\n\t/*\n\t * Endpoint")]
        if any(word in loop for word in ("WREG32(", "dev_info(", "dev_err(", "ps4_ready_ctx(", "ps4_stobs_ctx_sweep(")):
            raise ValueError("poll contains write/report/indexed access")
        patch = args.candidate / receipt["patch"]["path"]
        if sha(patch.read_bytes()) != receipt["patch"]["sha256"]:
            raise ValueError("patch changed")
        stage = "patch"
        with tempfile.TemporaryDirectory(dir=output, prefix="replay-") as tmp:
            for name, raw in old.items():
                path = Path(tmp) / PREFIX / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            passed(command(["git", "apply", "--check", patch.resolve()], output, "patch-check", cwd=tmp), "patch check")
            passed(command(["git", "apply", patch.resolve()], output, "patch-apply", cwd=tmp), "patch replay")
            paths = {str(p.relative_to(tmp)) for p in Path(tmp).rglob("*") if p.is_file()}
            if paths != {str(PREFIX / n) for n in new}:
                raise ValueError("patch inventory differs")
            for name, raw in new.items():
                if (Path(tmp) / PREFIX / name).read_bytes() != raw:
                    raise ValueError("patch bytes differ")
        header_text = ""
        for name, expected in HEADERS.items():
            raw = (args.headers / name).read_bytes()
            if sha(raw) != expected:
                raise ValueError("public register header changed")
            header_text += raw.decode()
        defs = definitions(header_text)
        defs.update(definitions(old_ready))
        defs.update(definitions(old[builder.CANARY].decode()))
        defs.update(definitions(old["amdgpu_ps4_uvd_ring_plan.h"].decode()))
        helpers = "\n\n".join(function(ready, name) for name in (
            "ps4_ready_ctx", "ps4_token_descriptor_valid", "ps4_token_expect",
            "ps4_stobs_status_expect", "ps4_ib_data_core_sample", "ps4_stobs_ctx_sweep"))
        token_types = re.search(r"enum ps4_token_phase \{.*?\};", ready, re.S)[0] + "\n" + \
            re.search(r"struct ps4_token_sample \{.*?\};", ready, re.S)[0]
        constants = [k for k in defs if k.startswith("PS4_READY_") or k in {
            "PS4_TOKEN_RBC_HELD", "PS4_TOKEN_RBC_FETCH", "PS4_STOBS_BIT16",
            "PS4_UVD_RING_BYTES", "PS4_UVD_SUBMIT_DWORDS", "PS4_CANARY_VA"}]
        fragments = {
            "REGISTERS": "".join(v for k, v in defs.items() if k.startswith(("mmUVD_", "ixUVD_", "mmVM_CONTEXT", "UVD_"))),
            "CONSTANTS": "".join(defs[k] for k in constants),
            "DESCRIPTOR": re.search(r"struct amdgpu_ps4_uvd_ring \{.*?\};", new[builder.HEADER].decode(), re.S)[0],
            "READY_STATE": marked(new[builder.HEADER].decode(), "READY_CORE_STATE"),
            "TOKEN_TYPES": token_types,
            "PARAMETER": marked(ready, "MESSAGE_CREATE_PARAMETER"),
            "ENABLED": function(ready, "amdgpu_ps4_uvd_ready_enabled"),
            "BOOT_REQUESTED": function(ready, "amdgpu_ps4_uvd_boot_probe_requested"),
            "CANARY_ENABLED": function(canary, "amdgpu_ps4_map_canary_enabled"),
            "OWNER": marked(canary, "MESSAGE_CREATE_OWNED_BACKING"),
            "HELPERS": helpers,
            "FLOW": flow,
            "WRAPPER": marked(ready, "READY_CORE_LEGACY_WRAPPER"),
        }
        generated = (HERE / "fixture.c").read_text()
        for name, value in fragments.items():
            generated = builder.once(generated, "/* @" + name + "@ */", value)
        if re.search(r"/\* @\w+@ \*/", generated):
            raise ValueError("unfilled extraction marker")
        include = output / "include"
        (include / "linux").mkdir(parents=True)
        shims = {
            "types.h": "#pragma once\n#include <stddef.h>\n#include <stdint.h>\n#include <stdbool.h>\ntypedef uint8_t u8; typedef uint32_t u32; typedef unsigned long long u64; typedef uint32_t __le32;\n",
            # glibc's <errno.h> itself includes <linux/errno.h>. Including
            # it again from our shim hides the system definitions on Linux.
            "errno.h": ("#pragma once\n#if defined(__linux__)\n"
                        "#include_next <linux/errno.h>\n#else\n"
                        "#include <errno.h>\n#endif\n"),
            "string.h": "#pragma once\n#include <string.h>\n",
            "atomic.h": "#pragma once\ntypedef int atomic_t; typedef long long atomic64_t;\n",
            "spinlock.h": "#pragma once\ntypedef int spinlock_t;\n",
        }
        for name, data in shims.items():
            (include / "linux" / name).write_text(data)
        for name in ("amdgpu_ps4_uvd_irq.h", builder.STATE, *receipt["library_pins"]):
            (include / name).write_bytes(new[name])
        source = output / "actual-source-fixture.c"
        source.write_text(generated)
        libraries = [include / "ps4_uvd_message.c", include / "ps4_uvd_message_job.c"]
        cc = str(Path(args.cc).resolve())
        passed(command([cc, "--version"], output, "compiler"), "compiler")
        flags = [cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", include]
        stage = "optimized"
        passed(command([*flags, "-O2", source, *libraries, "-o", output / "optimized"], output, "optimized-compile"), "optimized compile")
        run = command([output / "optimized"], output, "optimized-run")
        passed(run, "optimized run")
        match = re.fullmatch(rb"PASS: (\d+) actual-source CREATE controller cases; no hardware or firmware emulation\n", run.stdout)
        if not match:
            raise ValueError("unexpected pass output")
        stage = "sanitized"
        passed(command([*flags, "-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                        source, *libraries, "-o", output / "sanitized"], output, "sanitized-compile"), "sanitized compile")
        env = dict(os.environ)
        # Apple's sanitizer runtime rejects LeakSanitizer startup. ASan/UBSan
        # remain enabled; Linux also enables leak detection. The controller
        # itself allocates nothing and retained objects are static in this model.
        env["ASAN_OPTIONS"] = "detect_leaks=" + ("0" if platform.system() == "Darwin" else "1")
        env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
        sanitized = command([output / "sanitized"], output, "sanitized-run", env=env)
        passed(sanitized, "sanitized run")
        if sanitized.stdout != run.stdout or sanitized.stderr:
            raise ValueError("sanitized output differs")
        mutations = [
            ("one-confirmation", "if (op->consecutive == 2)", "if (op->consecutive == 1)", "TWO_CONSECUTIVE"),
            ("inclusive-deadline", "if (elapsed >= PS4_CREATE_LIMIT_NS)\n\t\t\tbreak;",
             "if (elapsed > PS4_CREATE_LIMIT_NS)\n\t\t\tbreak;", "SECOND_EXACT"),
            ("ignore-poll-fault", "if (!history_valid) {\n\t\t\tterminal", "if (false && !history_valid) {\n\t\t\tterminal", "HISTORY_SUBMITTED"),
            ("ignore-baseline-total", "else if (s->history.total.arrivals ||", "else if (0 ||", "HISTORY_NONZERO"),
            ("ignore-memory-guard", "return !bad;", "return !bad || bad;", "RING_GUARD"),
            ("ignore-pointer-regression", "\n\t\tif (sample.rptr < op->last_rptr) {",
             "\n\t\tif (false && sample.rptr < op->last_rptr) {", "POINTER_REGRESSION"),
            ("allow-reentry", "if (op->entered || !ready->boot_claimed", "if (false || !ready->boot_claimed", "REENTRY_PRESERVES_FIRST"),
            ("indexed-in-poll", "\t\tbegin = ktime_get_ns() - op->started_ns;",
             "\t\tps4_ready_ctx(adev, ixUVD_LMI_VMID_INTERNAL, 0, 0, false);\n\t\tbegin = ktime_get_ns() - op->started_ns;",
             "NO_SELECTOR_INSIDE_POLL"),
        ]
        stage = "sensitivity"
        sensitivity = []
        for name, old_text, new_text, witness in mutations:
            mutated = builder.once(generated, old_text, new_text)
            mutant_source = output / (name + ".c")
            mutant_source.write_text(mutated)
            binary = output / name
            passed(command([*flags, "-O2", mutant_source, *libraries, "-o", binary], output, name + "-compile"), name + " compile")
            result = command([binary], output, name + "-run")
            token = ("ASSERTION[" + witness + "]").encode()
            if result.returncode != 86 or token not in result.stderr:
                raise ValueError("sensitivity wrong failure: " + name)
            sensitivity.append({"name": name, "expected_witness": witness,
                                "returncode": result.returncode, "stderr_sha256": sha(result.stderr)})
        final = {
            "schema": 1, "status": "PASS_ACTUAL_SOURCE_SYNTHETIC_CREATE_CONTROLLER",
            "host": {"platform": platform.platform(), "machine": platform.machine()},
            "source_receipt_sha256": sha(receipt_raw), "patch_sha256": receipt["patch"]["sha256"],
            "generated_fixture_sha256": sha(generated.encode()),
            "compiler": cc, "compiler_version_stdout_sha256": sha((output / "compiler.stdout").read_bytes()),
            "cases": int(match[1]), "optimized": True, "asan_ubsan": True,
            "sanitizer_environment": {k: env[k] for k in ("ASAN_OPTIONS", "UBSAN_OPTIONS")},
            "sensitivity": sensitivity, "unchanged_source": identity,
            "hardware_acceptance": False, "kernel_compiled": False, "firmware_emulation": False,
            "limits": ["synthetic sequential observations and time model",
                      "borrowed unchanged direct/indexed samplers execute against controlled registers",
                      "readiness controller and old diagnostic bodies are identity-checked, not reexecuted",
                      "no native kernel build, linked audit, device or firmware operation"],
        }
        json_write(output / "receipt.json", final)
        print(json.dumps(final, indent=2))
    except Exception as exc:
        json_write(output / "FAILURE.json", {"stage": stage, "error": str(exc),
                    "traceback": traceback.format_exc(), "hardware_acceptance": False})
        raise

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", type=Path, required=True)
    ap.add_argument("--headers", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--cc", default=shutil.which("cc"), required=False)
    verify(ap.parse_args())
