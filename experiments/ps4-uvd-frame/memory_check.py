#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Extract and exercise real mapper logic; write only a new private receipt.

The host shims describe synthetic CPU BO/GART storage.  They do not emulate
UVD, device visibility, DMA, firmware or any real-hardware completion.
"""
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

import memory_transform as mt

HERE = Path(__file__).resolve().parent
BASE = Path("lab/uvd-2026-10-20/message-create/source/attempt-03")
MAPPER = Path("candidate/drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_map_canary.c")
FUNCTIONS = (
    "amdgpu_ps4_map_canary_enabled", "ps4_canary_vram_range",
    "ps4_canary_add_bo_pages", "ps4_canary_ready_pages",
    "ps4_canary_token_ring", "ps4_canary_ib_data_page",
    "ps4_canary_message_backing",
)
FRAME_FUNCTIONS = (
    "ps4_canary_work_bytes", "ps4_canary_expected_pages",
    "ps4_canary_work_end_slot", "ps4_canary_frame_pt_valid",
    "ps4_canary_frame_backing",
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def function(source: str, name: str) -> str:
    start, end = mt._function(source, name)
    return source[start:end]


def emission(source: str) -> str:
    start = source.index("\t\tfor (i = 0; i < s->mapped_pages; ++i)")
    end = source.index("\n\t\tmemset(s->r1.cpu", start)
    return source[start:end]


def dispatch(source: str) -> str:
    start = source.index("\t\tif ((amdgpu_ps4_uvd_token_requested() ||")
    end = source.index("\n\t\t/* Defensive local terminal path", start)
    return source[start:end]


def admission(source: str) -> str:
    marker = "\tif (amdgpu_ps4_uvd_decode_frame_requested() &&"
    start = source.index(marker) + len("\tif (")
    end = source.index('\n\t\tps4_canary_park(adev, "decode-frame-conflicting-gates"', start)
    condition = source[start:end]
    if not condition.endswith(")"):
        raise ValueError("frame admission condition anchor changed")
    return "!(" + condition[:-1] + ")"


def fixture(source: str, capable: bool) -> str:
    functions = [function(source, name) for name in FUNCTIONS]
    if capable:
        functions = [function(source, name) for name in FRAME_FUNCTIONS[:3]] + functions
        # The private-PT checker must precede the full frame borrower.
        functions.extend(function(source, name) for name in FRAME_FUNCTIONS[3:])
    constants = "\n".join(re.findall(r"^#define (?:PS4_CANARY_VA|PS4_READY_\w+|PS4_FRAME_\w+) .+$",
                                     source, re.MULTILINE))
    substitutions = {
        "CONSTANTS": constants,
        "FRAME_CAPABLE": "1" if capable else "0",
        "FUNCTIONS": "\n\n".join(functions),
        "EMIT": emission(source),
        "ADMISSION": admission(source) if capable else "true",
        "DISPATCH": dispatch(source),
    }
    text = (HERE / "memory_fixture.c").read_text()
    for key, value in substitutions.items():
        marker = "@" + key + "@"
        if text.count(marker) != 1:
            raise ValueError("fixture substitution changed: " + marker)
        text = text.replace(marker, value, 1)
    if re.search(r"@[A-Z_]+@", text):
        raise ValueError("unexpanded fixture marker")
    return text


def check_source(base: str, candidate: str) -> dict:
    unchanged = (
        "amdgpu_ps4_live_prepare_enabled", "amdgpu_ps4_live_prepare_init",
        "amdgpu_ps4_live_prepare_reserved", "amdgpu_ps4_live_prepare_invalidate",
        "amdgpu_ps4_live_prepare_snapshot", "ps4_live_prepare_begin",
        "ps4_live_prepare_failure", "ps4_live_prepare_attach",
        "ps4_live_prepare_publish", "ps4_live_prepare_free_bo",
        "amdgpu_ps4_live_prepare_fini", "amdgpu_ps4_map_canary_completed",
        "amdgpu_ps4_map_canary_reserve", "ps4_canary_park",
        "ps4_canary_exclusive_init", "ps4_canary_geometry", "ps4_canary_alloc",
        "ps4_canary_leaf_pte", "ps4_canary_vram_range",
        "ps4_canary_ib_data_page", "ps4_canary_message_backing",
    )
    for name in unchanged:
        if function(base, name) != function(candidate, name):
            raise AssertionError("old function changed: " + name)
    if emission(base) != emission(candidate):
        raise AssertionError("private PTE emission loop changed")
    run = function(candidate, "amdgpu_ps4_map_canary_run")
    positions = {
        "raw_gate": run.index("\tif (amdgpu_ps4_uvd_decode_frame_requested() &&"),
        "state_allocation": run.index("\ts = kzalloc_obj(*s);"),
        "bo_allocation": run.index("\tr = ps4_canary_alloc(adev, &s->pd"),
        "frame_pt_readback": run.index("\t\tif (s->frame_mode && !ps4_canary_frame_pt_valid(s))"),
        "sdma_publication": run.index("\tr = amdgpu_job_submit_direct(job, ring, &s->fence);"),
        "frame_borrow": run.index("\t\t    !ps4_canary_frame_backing(s, &frame_backing))"),
        "ready_dispatch": run.index("\t\tamdgpu_ps4_uvd_ready_run("),
    }
    if list(positions.values()) != sorted(positions.values()):
        raise AssertionError("admission / mapping / publication / borrow order changed")
    if "ps4_map_canary || ps4_uvd_live_prepare ||" not in admission(candidate):
        raise AssertionError("missing raw local conflicts")
    old_borrow = function(candidate, "ps4_canary_message_backing")
    old_ib = function(candidate, "ps4_canary_ib_data_page")
    if "PS4_READY_IB_BYTES" not in old_borrow or "AMDGPU_GPU_PAGE_SIZE" not in old_ib:
        raise AssertionError("older descriptor sizes changed")
    expected_full = "\t\t\ts->frame_mode ? &frame_backing :"
    if expected_full not in dispatch(candidate):
        raise AssertionError("frame full descriptor not in final argument")
    # This transform introduces no teardown, reset, retry, or post-publish PT
    # write.  Existing preparation-only teardown remains byte-identical above.
    added = set(candidate.splitlines()) - set(base.splitlines())
    forbidden = re.compile(r"\b(?:amdgpu_bo_free\w*|amdgpu_device_gpu_recover|reset|unmap\w*|unbind\w*|rearm\w*)\s*\(")
    if any(forbidden.search(line) for line in added):
        raise AssertionError("unexpected lifetime operation")
    for bad in (candidate.encode(), base.encode() + b"\n", b""):
        try:
            mt.transform(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("transform accepted reapplication/source drift")
    if mt.transform(base.encode()) != candidate.encode():
        raise AssertionError("transform is nondeterministic")
    return {
        "unchanged_functions": {name: sha(function(base, name).encode()) for name in unchanged},
        "unchanged_pte_emission_sha256": sha(emission(base).encode()),
        "run_byte_offsets": positions,
        "strict_input_identity_and_reapplication_rejection": True,
        "dynamic_helpers": {name: sha(function(candidate, name).encode()) for name in FRAME_FUNCTIONS},
    }


def command(argv: list[str], cwd: Path, env: dict | None = None) -> dict:
    p = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=60)
    return {"argv": argv, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def run(workspace: Path, output: Path, compiler: str) -> dict:
    if output.exists():
        raise ValueError("output already exists; preserve prior receipt")
    receipt_raw = (workspace / BASE / "SOURCE-RECEIPT.json").read_bytes()
    if sha(receipt_raw) != mt.BASE_RECEIPT_SHA256:
        raise ValueError("CREATE source receipt identity mismatch")
    base_raw = (workspace / BASE / MAPPER).read_bytes()
    candidate_raw = mt.transform(base_raw)
    source_checks = check_source(base_raw.decode(), candidate_raw.decode())
    generated = {"baseline": fixture(base_raw.decode(), False),
                 "candidate": fixture(candidate_raw.decode(), True)}
    output.mkdir(parents=True)
    (output / "amdgpu_ps4_map_canary.c").write_bytes(candidate_raw)
    (output / "SOURCE-ANCHORS.json").write_text(json.dumps(source_checks, indent=2, sort_keys=True) + "\n")
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = "detect_leaks=" + ("0" if platform.system() == "Darwin" else "1") + ":abort_on_error=1"
    env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    commands, runs = [], {}
    for name, text in generated.items():
        path = output / (name + "-fixture.c")
        path.write_text(text)
        runs[name] = {}
        for mode, flags in (("optimized", ["-O2"]),
                            ("sanitized", ["-O1", "-fno-omit-frame-pointer", "-fsanitize=address,undefined"])):
            exe = output / (name + "-" + mode)
            built = command([compiler, "-std=gnu11", "-g", "-Wall", "-Wextra", "-Werror",
                             "-Wno-unused-function", *flags, str(path), "-o", str(exe)], output)
            commands.append(built)
            if built["returncode"]:
                raise RuntimeError(json.dumps(built, indent=2))
            result = command([str(exe)], output, env)
            commands.append(result)
            if result["returncode"]:
                raise RuntimeError(json.dumps(result, indent=2))
            runs[name][mode] = {"stdout": result["stdout"], "stderr": result["stderr"]}
    for mode in ("optimized", "sanitized"):
        old = runs["baseline"][mode]["stdout"].splitlines()
        new = [line for line in runs["candidate"][mode]["stdout"].splitlines() if "frame=0 " in line]
        if old != new:
            raise AssertionError("old mode mapping/descriptor correspondence failed")
    # Ensure the checks notice a lost late guard, a missing DMA uniqueness
    # predicate, and changing the historical allocation in the shared helper.
    mutations = {
        "missed-slot842-hole": (
            "if (readq((void __iomem *)s->pt.cpu + slot * sizeof(u64)) != expected)",
            "if (readq((void __iomem *)s->pt.cpu + slot * sizeof(u64)) != expected && slot != 842U)"),
        "missing-dma-uniqueness": (
            "s->pages[j].slot == slot || s->pages[j].dma == dma ||",
            "s->pages[j].slot == slot ||"),
        "changed-old-work-size": (
            "return s->frame_mode ? PS4_FRAME_WORK_BYTES : PS4_READY_IB_BYTES;",
            "return s->frame_mode ? PS4_FRAME_WORK_BYTES : PS4_FRAME_WORK_BYTES;"),
    }
    sensitivity = {}
    for name, (old, new) in mutations.items():
        source = generated["candidate"]
        if source.count(old) != 1:
            raise AssertionError("nonunique sensitivity anchor: " + name)
        source = source.replace(old, new, 1)
        path, exe = output / (name + ".c"), output / name
        path.write_text(source)
        built = command([compiler, "-std=gnu11", "-O2", "-g", "-Wall", "-Wextra", "-Werror",
                         "-Wno-unused-function", str(path), "-o", str(exe)], output)
        commands.append(built)
        if built["returncode"]:
            raise RuntimeError("sensitivity fixture failed compilation: " + json.dumps(built))
        result = command([str(exe)], output, env)
        commands.append(result)
        if result["returncode"] != 2:
            raise AssertionError("fixture did not detect mutation: " + name)
        sensitivity[name] = {"detected": True, "returncode": result["returncode"], "stderr": result["stderr"]}
    receipt = {
        "schema": 1, "status": "HOST_MAPPING_CHECKS_ONLY", "hardware_acceptance": False,
        "base_source_receipt_sha256": sha(receipt_raw), "base_mapper_sha256": sha(base_raw),
        "candidate_mapper_sha256": sha(candidate_raw),
        "inputs": {name: sha((HERE / name).read_bytes()) for name in
                   ("memory_transform.py", "memory_check.py", "memory_fixture.c")},
        "generated_fixture_sha256": {name: sha(text.encode()) for name, text in generated.items()},
        "compiler": command([compiler, "--version"], output),
        "source_checks": source_checks, "runs": runs, "sensitivity": sensitivity,
        "commands": commands,
        "limits": ["actual mapper C functions with synthetic CPU BO/GART storage",
                   "frame options callback stubbed; raw older ready leaves owned by caller",
                   "CPU PTE readback does not establish all-page device visibility",
                   "no kernel compile, firmware, hardware page walk, decoding or DMA quiescence claim",
                   "no SSH, UART, MMIO, device, storage, boot-file or existing source changes"],
    }
    (output / "RECEIPT.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--cc", default=shutil.which("cc") or "cc")
    args = ap.parse_args()
    result = run(args.workspace.resolve(), args.output.resolve(), args.cc)
    print(json.dumps({"status": result["status"], "candidate_mapper_sha256": result["candidate_mapper_sha256"],
                      "receipt": str(args.output.resolve() / "RECEIPT.json")}, indent=2))
