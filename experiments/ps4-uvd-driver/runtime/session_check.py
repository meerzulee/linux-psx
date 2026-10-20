#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile complete generated session functions against explicit API stubs.

This is a CPU control-flow check, not a kernel/concurrency/DMA model. The
separate jobs checker owns scheduler cancellation and actual lease cleanup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
FUNCTIONS = {
    "amdgpu_uvd.c": ("amdgpu_uvd_cs_msg_decode", "amdgpu_uvd_cs_msg"),
    "amdgpu_ps4_uvd_runtime.c": ("amdgpu_ps4_uvd_runtime_drain_jobs",),
    "amdgpu_cs.c": ("amdgpu_cs_ioctl",),
    "amdgpu_job.c": ("amdgpu_job_run",),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract(source: str, name: str) -> str:
    match = re.search(r"(?m)^(?:static\s+)?(?:int|void|struct dma_fence\s*\*)\s*"
                      + re.escape(name) + r"\([^;{]*?\)\s*\n\{", source)
    if not match:
        raise ValueError(f"whole function not found: {name}")
    # Top-level kernel definitions end with a column-zero brace. Keep the
    # signature, comments and complete body verbatim; no body rewriting.
    end = re.search(r"(?m)^\}", source[match.end():])
    if not end:
        raise ValueError(f"function end not found: {name}")
    return source[match.start():match.end() + end.end()] + "\n"


def no_core() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def execute(argv: list[str], cwd: Path, stem: str, env=None) -> dict:
    run = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=60,
                         env=env, preexec_fn=no_core)
    (cwd / f"{stem}.stdout").write_bytes(run.stdout)
    (cwd / f"{stem}.stderr").write_bytes(run.stderr)
    return {"argv": argv, "returncode": run.returncode,
            "stdout": run.stdout.decode(errors="replace"),
            "stderr": run.stderr.decode(errors="replace")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path,
                        help="generated attempt directory or candidate amdgpu directory")
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    parser.add_argument("--output", type=Path, help="fresh private output directory")
    args = parser.parse_args()
    if args.output:
        args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
        out = args.output.resolve()
    else:
        out = Path(tempfile.mkdtemp(prefix="ps4-uvd-session-check-"))
    candidate = args.candidate.resolve()
    if not (candidate / "amdgpu_uvd.c").exists():
        candidate = candidate / "candidate/drivers/gpu/drm/amd/amdgpu"
    receipt = {
        "result": "FAIL", "hardware_acceptance": False,
        "scope": "whole actual-source session functions; deterministic external-API stubs; no concurrency or DMA acceptance",
        "platform": platform.platform(), "candidate": str(candidate),
        "checker_sha256": sha(Path(__file__).read_bytes()),
        "fixture_sha256": sha((HERE / "session_fixture.c").read_bytes()),
        "inputs_sha256": {}, "functions_sha256": {}, "checks": [],
    }
    try:
        inputs = out / "inputs"
        inputs.mkdir()
        functions: dict[str, str] = {}
        names = dict(FUNCTIONS)
        names["amdgpu_uvd.c"] += ("amdgpu_uvd_free_handles",)
        for file, definitions in names.items():
            data = (candidate / file).read_bytes()
            (inputs / file).write_bytes(data)
            receipt["inputs_sha256"][file] = sha(data)
            for name in definitions:
                functions[name] = extract(data.decode(), name)
                receipt["functions_sha256"][name] = sha(functions[name].encode())
        # The drain definition precedes free_handles, which calls it.
        order = ("amdgpu_uvd_cs_msg_decode", "amdgpu_uvd_cs_msg",
                 "amdgpu_ps4_uvd_runtime_drain_jobs", "amdgpu_uvd_free_handles",
                 "amdgpu_cs_ioctl", "amdgpu_job_run")
        actual = "\n".join(functions[name] for name in order)
        (out / "actual-functions.c.inc").write_text(actual)
        fixture = (HERE / "session_fixture.c").read_text()
        marker = "/* SESSION_ACTUAL_FUNCTIONS */"
        if fixture.count(marker) != 1:
            raise ValueError("fixture marker is not unique")

        def render(implementation: str) -> str:
            return fixture.replace(marker, implementation)

        version = execute([args.cc, "--version"], out, "compiler-version")
        if version["returncode"]:
            raise ValueError("compiler unavailable")
        receipt["compiler"] = version["stdout"].splitlines()[0]
        common = [args.cc, "-std=gnu11", "-g", "-Wall", "-Wextra", "-Werror",
                  "-Wno-unused-function", "-Wno-unused-parameter",
                  "-Wno-pointer-sign", "-Wno-sign-compare"]

        def check(name: str, implementation: str, flags: list[str], mutant=False) -> None:
            data = render(implementation)
            source, binary = out / f"{name}.c", out / name
            source.write_text(data)
            compiled = execute(common + flags + [str(source), "-o", str(binary)], out, name + "-compile")
            record = {"name": name, "source_sha256": sha(data.encode()),
                      "mutant_expected_failure": mutant, "compile": compiled}
            receipt["checks"].append(record)
            if compiled["returncode"]:
                raise ValueError(f"compile failed: {name}")
            env = os.environ.copy()
            env["ASAN_OPTIONS"] = ("detect_leaks=0" if platform.system() == "Darwin" else "detect_leaks=1") + ":halt_on_error=1"
            env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
            ran = execute([str(binary)], out, name + "-run", env)
            record["run"] = ran
            if mutant:
                if ran["returncode"] != -6 or not re.search(r":\d+ [a-z-]+: ", ran["stderr"]):
                    raise ValueError(f"mutant did not reach a fixture assertion: {name}")
                record["result"] = "DETECTED"
            else:
                if ran["returncode"] or not re.fullmatch(r"PASS actual-source sessions: \d+ cases, \d+ assertions\n", ran["stdout"]):
                    raise ValueError(f"actual-source fixture failed: {name}")
                record["result"] = "PASS"

        check("optimized", actual, ["-O2"])
        check("asan-ubsan", actual, ["-O1", "-fno-omit-frame-pointer", "-fsanitize=address,undefined"])
        mutants = (
            ("no-cs-error-close", "if (ps4_uvd_session_locked)\n\t\tamdgpu_ps4_uvd_runtime_fault(adev, r);",
             "if (ps4_uvd_session_locked)\n\t\t(void)r;"),
            ("unlock-before-parse", "\tr = amdgpu_cs_patch_jobs(&parser);",
             "\tif (ps4_uvd_session_locked) mutex_unlock(&adev->ps4_uvd_runtime.session_lock);\n\tr = amdgpu_cs_patch_jobs(&parser);"),
            ("no-close-drain", "if (ps4_runtime && amdgpu_ps4_uvd_runtime_drain_jobs(adev))", "if (false)"),
            ("no-destroy-owner", "adev->uvd.filp[0] != ctx->parser->filp", "false"),
            ("non-avc-decode", "(stream_type != 0 || width > 1920 || height > 1088)",
             "(false || width > 1920 || height > 1088)"),
            ("ignore-drain-closed", "if (closed || atomic_read(&s->failed))", "if ((closed || atomic_read(&s->failed)) && false)"),
            ("ignore-destroy-wait-error", "if (waited <= 0 || dma_fence_get_status(fence) < 0)",
             "if ((waited <= 0 || dma_fence_get_status(fence) < 0) && false)"),
            ("ignore-run-finished-error", "if (amdgpu_ps4_uvd_runtime_ring(ring))\n\t\t\tamdgpu_ps4_uvd_runtime_fault(adev, finished->error);",
             "if (amdgpu_ps4_uvd_runtime_ring(ring))\n\t\t\t(void)finished;"),
        )
        for name, before, after in mutants:
            if actual.count(before) != 1:
                raise ValueError(f"mutation anchor is not unique: {name}")
            check("mutant-" + name, actual.replace(before, after), ["-O2"], mutant=True)
        receipt["result"] = "PASS_CPU_SESSIONS"
    except Exception as exc:
        receipt["error"] = str(exc)
    finally:
        path = out / "receipt.json"
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        for root, dirs, files in os.walk(out):
            os.chmod(root, 0o700)
            for name in files:
                p = Path(root) / name
                os.chmod(p, 0o700 if os.access(p, os.X_OK) else 0o600)
        print(f"{receipt['result']} {path}")
        if receipt.get("error"):
            print(receipt["error"])
    return 0 if receipt["result"] == "PASS_CPU_SESSIONS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
