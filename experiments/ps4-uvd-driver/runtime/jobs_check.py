#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright 2026 psX Linux contributors
"""Bounded CPU check of the actual lease C and patched core free functions."""
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
PINS = {
    "amdgpu_job.c": "ecdb1152c3a82f3207f0c649aa82dffa112b64d633675bdc13e0af1f58d32d32",
    "amdgpu_job.h": "a130f1686c314a0bdbffdd59ee6ce454a79e6bfda60c98f2a276e4a3336e70df",
    "amdgpu_cs.c": "6fcdab083cfe615045ba987468f717a227e32b3a004c576d0651b74dd078c4c2",
    "amdgpu_uvd.c": "6b8c41d474f950b1de4fa57a03498915fa4ed0208341de5655379cdb81e5318d",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def no_core() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def execute(argv: list[str], cwd: Path, stem: str, env=None) -> dict:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=60,
                            env=env, preexec_fn=no_core)
    for suffix, data in (("stdout", result.stdout), ("stderr", result.stderr)):
        (cwd / f"{stem}.{suffix}").write_bytes(data)
    return {"argv": argv, "returncode": result.returncode,
            "stdout": result.stdout.decode(errors="replace"),
            "stderr": result.stderr.decode(errors="replace")}


def function(source: str, name: str) -> str:
    match = re.search(r"^(?:static )?void " + re.escape(name) +
                      r"\([^;]*?\)\n\{.*?^\}", source, re.M | re.S)
    if not match:
        raise ValueError(f"exact core definition missing: {name}")
    return match.group() + "\n"


def without_includes(source: str) -> str:
    # Preserve the complete implementation, notices and function bodies.
    return re.sub(r"^#include[^\n]*\n", "", source, flags=re.M)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    parser.add_argument("--base-amdgpu", type=Path, default=
                        HERE / "../../../../lab/psx-baikal-port72/drivers/gpu/drm/amd/amdgpu")
    parser.add_argument("--output", type=Path,
                        help="fresh, nonexistent private result directory")
    args = parser.parse_args()
    if args.output:
        args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
        out = args.output.resolve()
    else:
        out = Path(tempfile.mkdtemp(prefix="ps4-uvd-jobs-policy-"))
    receipt = {"result": "FAIL", "hardware_acceptance": False,
               "scope": "actual-source CPU lifecycle policy; deterministic API stubs, no concurrency or DMA model",
               "platform": platform.platform(), "source_sha256": {},
               "base_sha256": {}, "checks": []}
    try:
        overlay = out / "source/drivers/gpu/drm/amd/amdgpu"
        overlay.mkdir(parents=True, mode=0o700)
        for name, pin in PINS.items():
            data = (args.base_amdgpu / name).read_bytes()
            if sha(data) != pin:
                raise ValueError(f"base pin mismatch: {name}")
            receipt["base_sha256"][name] = pin
            (overlay / name).write_bytes(data)
        for name in ("amdgpu_ps4_uvd_jobs.c", "amdgpu_ps4_uvd_jobs.h",
                     "ps4-uvd-jobs-hooks.patch", "jobs_fixture.c", "jobs_check.py"):
            receipt["source_sha256"][name] = sha((HERE / name).read_bytes())
        check = execute(["patch", "--fuzz=0", "-p1", "-i",
                         str(HERE / "ps4-uvd-jobs-hooks.patch")], out / "source", "patch")
        receipt["patch"] = check
        if check["returncode"] or "offset" in check["stdout"] or "fuzz" in check["stdout"]:
            raise ValueError("hook patch failed exact application")
        patched = (overlay / "amdgpu_job.c").read_text()
        names = ("amdgpu_job_free_resources", "amdgpu_job_free_cb",
                 "amdgpu_job_free", "amdgpu_ps4_uvd_job_free_deferred")
        core = "\n".join(function(patched, name) for name in names)
        receipt["actual_core_function_sha256"] = {
            name: sha(function(patched, name).encode()) for name in names}
        c = (HERE / "amdgpu_ps4_uvd_jobs.c").read_text()
        h = without_includes((HERE / "amdgpu_ps4_uvd_jobs.h").read_text())
        fixture = (HERE / "jobs_fixture.c").read_text()

        def render(implementation: str, finalizer=core) -> str:
            result = fixture
            for marker, data in (("JOBS_ACTUAL_HEADER", h),
                                 ("JOBS_ACTUAL_SOURCE", without_includes(implementation)),
                                 ("JOBS_ACTUAL_CORE_FREE", finalizer)):
                token = f"/* {marker} */"
                if result.count(token) != 1:
                    raise ValueError(f"fixture marker count: {marker}")
                result = result.replace(token, data)
            return result

        version = execute([args.cc, "--version"], out, "compiler-version")
        receipt["compiler"] = version["stdout"].splitlines()[0]
        common = [args.cc, "-std=gnu11", "-g", "-Wall", "-Wextra", "-Werror",
                  "-Wno-unused-function", "-Wno-unused-parameter"]

        def run_case(name: str, source: str, flags: list[str], mutant=False) -> None:
            src, binary = out / f"{name}.c", out / name
            src.write_text(source)
            compiled = execute(common + flags + [str(src), "-o", str(binary)], out, name + "-compile")
            record = {"name": name, "source_sha256": sha(source.encode()), "compile": compiled,
                      "mutant_expected_failure": mutant}
            receipt["checks"].append(record)
            if compiled["returncode"]:
                raise ValueError(f"compile failed: {name}")
            env = os.environ.copy()
            env["ASAN_OPTIONS"] = ("detect_leaks=0" if platform.system() == "Darwin"
                                   else "detect_leaks=1") + ":halt_on_error=1"
            env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
            run = execute([str(binary)], out, name + "-run", env)
            record["run"] = run
            if mutant:
                if run["returncode"] != -6 or not re.search(r":\d+ [a-z-]+: ", run["stderr"]):
                    raise ValueError(f"mutant did not hit a fixture assertion: {name}")
                record["result"] = "DETECTED"
            else:
                if run["returncode"] or not re.fullmatch(r"PASS actual-source jobs: \d+ cases\n", run["stdout"]):
                    raise ValueError(f"lifecycle check failed: {name}")
                record["result"] = "PASS"

        run_case("optimized", render(c), ["-O2"])
        run_case("asan-ubsan", render(c), ["-O1", "-fno-omit-frame-pointer",
                                          "-fsanitize=address,undefined"])
        mutants = (
            ("no-watermark", "lease->hw->seqno <= state->hw_completed", "true"),
            ("pending-is-complete", "status > 0", "status >= 0"),
            ("no-resource-deferral", "return job->ps4_uvd_lease != NULL;", "return false;"),
            ("no-pin-rollback", "fail_pinned:\n\tfor (i = 0; i < lease->pinned; ++i) {\n\t\tamdgpu_bo_unpin(lease->bos[i]);",
             "fail_pinned:\n\tfor (i = 0; i < lease->pinned; ++i) {\n\t\t/* injected missing unpin */"),
            ("reopen-on-fault", "state->closed = true;", "state->closed = false;"),
            ("ignore-unentered-cancellation", "if (job->base.s_fence &&\n\t    READ_ONCE(job->base.s_fence->finished.error) < 0)",
             "if (lease->entered && job->base.s_fence &&\n\t    READ_ONCE(job->base.s_fence->finished.error) < 0)"),
        )
        for name, before, after in mutants:
            if c.count(before) != 1:
                raise ValueError(f"mutation is not unique: {name}")
            run_case("mutant-" + name, render(c.replace(before, after)), ["-O2"], mutant=True)
        # Reintroduce the independently identified scheduler cleanup leak.
        bad = core.replace("amdgpu_job_free_cb(&job->base);", "amdgpu_job_free(job);")
        if bad == core:
            raise ValueError("cleanup-origin mutation missing")
        run_case("mutant-lost-scheduler-origin", render(c, bad), ["-O2"], mutant=True)
        receipt["result"] = "PASS_CPU_LIFECYCLE"
    except Exception as exc:
        receipt["error"] = str(exc)
    finally:
        path = out / "receipt.json"
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        for root, dirs, files in os.walk(out):
            os.chmod(root, 0o700)
            for name in files:
                file = Path(root) / name
                os.chmod(file, 0o700 if os.access(file, os.X_OK) else 0o600)
        print(f"{receipt['result']} {path}")
        if receipt.get("error"):
            print(receipt["error"])
    return 0 if receipt["result"] == "PASS_CPU_LIFECYCLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
