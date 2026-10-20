#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Actual-source differential readiness controller checks; no device access."""
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
import sys
import tempfile
import traceback

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ready_core_generator", HERE / "build_candidate.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
PREFIX = builder.PREFIX


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, data):
    with path.open("x") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")


def function(source, name):
    match = re.search(r"(?m)^(?:static )?(?:__noreturn )?(?:bool|void) " +
                      re.escape(name) + r"\(", source)
    if match is None:
        raise ValueError("missing function: " + name)
    start = source.index("\n{\n", match.start())
    end = source.index("\n}\n", start) + 3
    return source[match.start():end]


def constants(source):
    result, lines, i = [], source.splitlines(keepends=True), 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#define PS4_READY_"):
            result.append(line)
            while line.rstrip().endswith("\\"):
                i += 1
                line = lines[i]
                result.append(line)
        i += 1
    return "".join(result)


def render(template, fragments):
    for key, value in fragments.items():
        token = "/* @SOURCE_" + key + "@ */"
        if template.count(token) != 1:
            raise ValueError("fixture extraction marker: " + token)
        template = template.replace(token, value, 1)
    if "@SOURCE_" in template:
        raise ValueError("unfilled actual-source marker")
    return template


def command(args, output, name, *, cwd=None, env=None, timeout=60):
    proc = subprocess.run(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, check=False)
    for suffix, data in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        with (output / (name + "." + suffix)).open("xb") as stream:
            stream.write(data)
    record = {"argv": list(map(str, args)), "returncode": proc.returncode,
              "cwd": str(cwd) if cwd else None,
              "stdout_sha256": sha(proc.stdout), "stderr_sha256": sha(proc.stderr)}
    write_json(output / (name + ".json"), record)
    return proc


def require_success(proc, name):
    if proc.returncode:
        raise ValueError(name + " failed; see preserved stdout/stderr")


def check(candidate_dir, output, cc):
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "tools"
    snapshot.mkdir()
    for name in ("check.py", "fixture.c", "build_candidate.py", "operation.h.inc"):
        shutil.copyfile(HERE / name, snapshot / name)
    phase = "source-contract"
    try:
        receipt_raw = (candidate_dir / "SOURCE-RECEIPT.json").read_bytes()
        receipt = json.loads(receipt_raw)
        if receipt["baseline_source_receipt_sha256"] != builder.SOURCE_RECEIPT:
            raise ValueError("wrong preparation baseline")
        if receipt["generator_sha256"] != sha((HERE / "build_candidate.py").read_bytes()):
            raise ValueError("generator changed since candidate generation")
        if receipt["generator_inputs"]["operation.h.inc"] != sha((HERE / "operation.h.inc").read_bytes()):
            raise ValueError("operation definition changed")
        sources = {}
        for category in ("baseline", "candidate"):
            table = receipt[category + "_files"]
            sources[category] = {}
            for name, expected in table.items():
                raw = (candidate_dir / category / PREFIX / name).read_bytes()
                if sha(raw) != expected:
                    raise ValueError("source hash changed: " + category + "/" + name)
                sources[category][name] = raw
        old = sources["baseline"][builder.READY].decode()
        new = sources["candidate"][builder.READY].decode()
        header = sources["candidate"][builder.HEADER].decode()
        if new != builder.transform_ready(old):
            raise ValueError("candidate is not deterministic refactor")
        # Reverse the two prefix edits: every helper, policy and full diagnostic
        # flow preceding the readiness entry must remain byte-identical.
        old_prefix = old.split(builder.ENTRY)[0]
        new_prefix = new.split("/* READY_CORE_FUNCTIONS_BEGIN */")[0]
        restored = builder.once(new_prefix,
            "/* Retained operation/firmware now belongs to adev; admission stays global. */\n",
            "/* Deliberately retained, including all failure paths. No teardown API exists. */\n"
            "static const struct firmware *ps4_ready_firmware;\n"
            "static void *ps4_ready_retained_cpu;\n"
            "static u64 ps4_ready_retained_pd;\n")
        restored = builder.once(restored,
            "static bool ps4_ready_fetch_guard_sample(",
            builder.GUARD + "\nstatic bool ps4_ready_fetch_guard_sample(")
        if restored != old_prefix:
            raise ValueError("static helper or historical diagnostic source changed")
        if new.count("ps4_ready_core_run(") != 2:
            raise ValueError("only private definition and boot-wrapper call permitted")
        if "EXPORT_SYMBOL" in new or "release_firmware(" in new:
            raise ValueError("unexpected export or firmware release")
        for name in receipt["historical_blocks_sha256"]:
            block = builder.marker_block(new, name).encode()
            if sha(block) != receipt["historical_blocks_sha256"][name]:
                raise ValueError("historical block changed")
        patch = candidate_dir / receipt["patch"]["path"]
        if sha(patch.read_bytes()) != receipt["patch"]["sha256"]:
            raise ValueError("patch changed")
        phase = "patch-replay"
        with tempfile.TemporaryDirectory(prefix="replay-", dir=output) as temporary:
            replay = Path(temporary)
            for name, raw in sources["baseline"].items():
                path = replay / PREFIX / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            require_success(command(["git", "apply", "--check", str(patch)],
                                    output, "patch-check", cwd=replay), "patch check")
            require_success(command(["git", "apply", str(patch)],
                                    output, "patch-apply", cwd=replay), "patch replay")
            actual_paths = {str(path.relative_to(replay)) for path in replay.rglob("*") if path.is_file()}
            if actual_paths != {str(PREFIX / name) for name in sources["candidate"]}:
                raise ValueError("patch changed path inventory")
            for name, expected in sources["candidate"].items():
                if (replay / PREFIX / name).read_bytes() != expected:
                    raise ValueError("patch replay mismatch: " + name)
        fragments = {
            "CONSTANTS": constants(old),
            "STATE": builder.marker_block(header, "READY_CORE_STATE"),
            "ENABLED": function(old, "amdgpu_ps4_uvd_ready_enabled"),
            "BASELINE_ENTRY": function(old, "amdgpu_ps4_uvd_ready_run").replace(
                "amdgpu_ps4_uvd_ready_run(", "frozen_ready_run(", 1),
            "CORE": builder.marker_block(new, "READY_CORE_FUNCTIONS"),
            "WRAPPER": builder.marker_block(new, "READY_CORE_LEGACY_WRAPPER"),
        }
        template = (HERE / "fixture.c").read_text()
        generated = render(template, fragments)
        with (output / "actual-source-fixture.c").open("x") as stream:
            stream.write(generated)
        compiler = str(Path(cc).resolve())
        require_success(command([compiler, "--version"], output, "compiler"), "compiler identity")
        base_flags = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror"]
        phase = "optimized"
        binary = output / "optimized"
        require_success(command(base_flags + ["-O2", str(output / "actual-source-fixture.c"),
                                              "-o", str(binary)], output, "optimized-compile"), "optimized compile")
        optimized = command([str(binary)], output, "optimized-run", timeout=30)
        require_success(optimized, "optimized run")
        phase = "sanitized"
        binary = output / "sanitized"
        require_success(command(base_flags + ["-O1", "-g", "-fsanitize=address,undefined",
            "-fno-omit-frame-pointer", str(output / "actual-source-fixture.c"), "-o", str(binary)],
            output, "sanitized-compile"), "sanitized compile")
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1",
                   UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
        sanitized = command([str(binary)], output, "sanitized-run", env=env, timeout=30)
        require_success(sanitized, "sanitized run")
        if optimized.stdout != sanitized.stdout or not re.fullmatch(
                rb"PASS: [1-9][0-9]* actual-source ready-core cases; frozen-wrapper traces, terminal state, retention, one-attempt policy\n",
                optimized.stdout):
            raise ValueError("run output identity")
        phase = "sensitivity"
        mutants = (
            ("global-latch-removed", "WRAPPER", "atomic_xchg(&ps4_ready_attempted, 1)", "false", "LEGACY_OUTCOME"),
            ("first-mmio-witness-missing", "CORE", "op->first_mmio_entered = true;",
             "op->first_mmio_entered = false;", "FIRST_MMIO_ENTRY"),
            ("terminal-not-stored", "CORE", "op->completed = true;",
             "op->completed = false;", "TERMINAL_STATE"),
            ("firmware-reference-lost", "CORE", "op->completed = true;",
             "op->firmware = NULL;\n\top->completed = true;", "FIRMWARE_RETENTION"),
            ("producer-exclusion-removed", "CORE",
             "adev->mman.buffer_funcs_enabled || adev->accel_working ||",
             "adev->accel_working ||", "LEGACY_OUTCOME"),
            ("dispatch-cache-changed", "WRAPPER",
             "cache_vmid_expected = op->cache_vmid_expected;", "cache_vmid_expected = 0;",
             "LEGACY_TRACE"),
            ("private-reentry-clobbers-owner", "CORE",
             "if (!op->boot_claimed || op->entered)",
             "op->retained_pd = 0;\n\tif (!op->boot_claimed || op->entered)", "REENTRY_STATE"),
            ("unclaimed-core-enabled", "CORE",
             "if (!op->boot_claimed || op->entered)", "if (op->entered)", "UNCLAIMED_CORE"),
            ("deadline-comparison-relaxed", "CORE",
             "op->elapsed >= PS4_READY_LIMIT_NS", "op->elapsed > PS4_READY_LIMIT_NS", "LEGACY_TRACE"),
        )
        witnesses = []
        for name, part, old_text, replacement, witness in mutants:
            modified = dict(fragments)
            modified[part] = builder.once(modified[part], old_text, replacement)
            source = render(template, modified)
            path = output / ("mutant-" + name + ".c")
            with path.open("x") as stream:
                stream.write(source)
            binary = output / ("mutant-" + name)
            require_success(command(base_flags + ["-O2", str(path), "-o", str(binary)],
                                    output, name + "-compile"), "mutant compile: " + name)
            run = command([str(binary)], output, name + "-run", timeout=30)
            expected = ("FAIL:" + witness + " line=").encode()
            if run.returncode == 0 or not run.stderr.startswith(expected):
                raise ValueError("mutant exact witness not detected: " + name)
            witnesses.append({"name": name, "witness": witness, "returncode": run.returncode,
                              "stderr_sha256": sha(run.stderr), "source_sha256": sha(source.encode())})
        report = {
            "schema": 1, "status": "PASS_ACTUAL_SOURCE_READY_CORE_HOST_CHECKS",
            "cases": int(optimized.stdout.split()[1]), "optimized_pass": True,
            "asan_ubsan_pass": True, "sensitivity": witnesses,
            "candidate_source_receipt_sha256": sha(receipt_raw),
            "patch_sha256": receipt["patch"]["sha256"],
            "candidate_files": receipt["candidate_files"],
            "baseline_files": receipt["baseline_files"],
            "generated_fixture_sha256": sha(generated.encode()),
            "checker_sha256": sha(Path(__file__).read_bytes()),
            "template_sha256": sha((HERE / "fixture.c").read_bytes()),
            "compiler": compiler, "compiler_version_sha256": sha((output / "compiler.stdout").read_bytes()),
            "python": sys.version, "platform": platform.platform(),
            "native_kernel_built": False, "hardware_tested": False,
            "limitations": [
                "Actual readiness controller and legacy wrapper only; unchanged helper bodies and diagnostic flows are source-identity checked, not executed.",
                "Helper/MMIO/clock/firmware results are synthetic; this is not hardware or firmware emulation.",
                "Finite poll/control-flow checks do not establish a whole-worker duration bound.",
                "No live entrypoint, synchronization with normal GPU producers, teardown safety, DMA quiescence or decoder acceptance.",
            ],
        }
        write_json(output / "receipt.json", report)
        return report
    except BaseException as error:
        write_json(output / "FAILURE.json", {"schema": 1, "phase": phase,
            "error": repr(error), "traceback": traceback.format_exc(),
            "hardware_tested": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cc", default=shutil.which("cc"))
    args = parser.parse_args()
    if not args.cc:
        parser.error("native host C compiler required")
    result = check(args.candidate.resolve(), args.output.resolve(), args.cc)
    print(json.dumps({"status": result["status"], "cases": result["cases"],
                      "mutants": len(result["sensitivity"])}, sort_keys=True))
