#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Apply/replay and test the exact token patch on synthetic MMIO, never hardware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

PREFIX = Path("drivers/gpu/drm/amd/amdgpu")
BASE = {
    "amdgpu_ps4_uvd_ready.c": "57459edfca80983b1d248704ce790ad8dd5c08a9ac6bc08910828f6b083779c7",
    "amdgpu_ps4_map_canary.c": "a5cc36b7d917fae7d674b79476c58f58005d0100881dca51ede6c713d7c39ddd",
    "amdgpu_ps4_uvd_ready.h": "198a840658c7d816c48b01f3229d42f663105baca9e2a7e04a3f46f886d10a5b",
}
HEADER = "amdgpu_ps4_uvd_ring_plan.h"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def masked(text):
    pattern = r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    return re.sub(pattern, lambda m: "".join("\n" if c == "\n" else " " for c in m[0]), text, flags=re.S)


def braces(text, left):
    clean = masked(text)
    start = clean.index("{", left)
    depth, end = 1, start + 1
    while depth:
        depth += (clean[end] == "{") - (clean[end] == "}")
        end += 1
    return end


def function(text, name):
    clean = masked(text)
    match = re.search(r"\b" + name + r"\s*\([^;{}]*\)\s*\{", clean)
    assert match, name
    start = clean.rfind("\n", 0, match.start()) + 1
    return text[start:braces(text, match.start())]


def defines(text):
    result = {}
    lines = iter(text.splitlines(keepends=True))
    for line in lines:
        match = re.match(r"#define\s+(\w+)\s", line)
        if match:
            body = line
            while body.endswith("\\\n"):
                body += next(lines)
            result[match[1]] = body
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "source", "headers", "planner", "planner-fixture", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    here = Path(__file__).resolve().parent
    patch = here / "0007-drm-amdgpu-ps4-one-shot-uvd-token.patch"
    args.report.mkdir(parents=True, exist_ok=True)
    before, after = args.baseline / PREFIX, args.source / PREFIX
    for name, expected in BASE.items():
        assert sha(before / name) == expected, "wrong frozen baseline: " + name
    assert (after / HEADER).read_bytes() == args.planner.read_bytes(), "planner header drift"
    outputs = [*BASE, HEADER]
    with tempfile.TemporaryDirectory(prefix="ps4-token-replay-") as directory:
        root = Path(directory)
        (root / PREFIX).mkdir(parents=True)
        for name in BASE:
            shutil.copy2(before / name, root / PREFIX / name)
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=root, check=True)
        subprocess.run(["git", "apply", str(patch)], cwd=root, check=True)
        actual_files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        assert actual_files == sorted((PREFIX / name).as_posix() for name in outputs)
        for name in outputs:
            assert (root / PREFIX / name).read_bytes() == (after / name).read_bytes(), name
    old = (before / "amdgpu_ps4_uvd_ready.c").read_text()
    new = (after / "amdgpu_ps4_uvd_ready.c").read_text()
    old_canary = (before / "amdgpu_ps4_map_canary.c").read_text()
    new_canary = (after / "amdgpu_ps4_map_canary.c").read_text()
    unchanged = []
    for original, changed, exception in ((old, new, "amdgpu_ps4_uvd_ready_run"),
                                       (old_canary, new_canary, "amdgpu_ps4_map_canary_run")):
        names = re.findall(r"^(?:static )?(?:__noreturn )?(?:bool|u32|int|void)\s+(\w+)\s*\(", original, re.M)
        for name in names:
            if name != exception:
                assert function(original, name) == function(changed, name), name
                unchanged.append(name)
    old_run = function(old, "amdgpu_ps4_uvd_ready_run")
    new_run = function(new, "amdgpu_ps4_uvd_ready_run")
    normalized = new_run.replace("u64 pd_addr,\n\t\t\t\t      const struct amdgpu_ps4_uvd_ring *rb)", "u64 pd_addr)", 1)
    early = '''\t/* Receiver validation precedes all readiness setup and firmware access. */
\tif (ps4_uvd_token && (!ps4_uvd_poll_state || !ps4_token_descriptor_valid(rb)))
\t\tps4_ready_park(adev, "TOKEN_DESCRIPTOR_OR_GATES", -EINVAL);
'''
    assert normalized.count(early) == 1
    assert normalized.index(early) < normalized.index("ps4_ready_cache_vmid_preflight")
    normalized = normalized.replace(early, "", 1)
    branch = '''\t\t\t\tif (ps4_uvd_token)
\t\t\t\t\tps4_token_run(adev, rb, pd_addr, cache_vmid_expected,
\t\t\t\t\t\t      baseline_fault, reset_count);
'''
    assert normalized.count(branch) == 1
    assert normalized.replace(branch, "", 1) == old_run, "legacy startup/dispatch changed"
    assert "static bool ps4_uvd_token;" in new
    assert "module_param_named(ps4_uvd_token, ps4_uvd_token, bool, 0444)" in new
    assert function(new, "amdgpu_ps4_uvd_token_requested").count("ps4_uvd_token") == 2
    canary_run = function(new_canary, "amdgpu_ps4_map_canary_run")
    canary_run = canary_run.replace("\t\tstruct amdgpu_ps4_uvd_ring token_ring = { 0 };\n\n", "", 1)
    caller = '''\t\tif (amdgpu_ps4_uvd_token_requested() &&
\t\t    !ps4_canary_token_ring(s, &token_ring))
\t\t\tps4_canary_park(adev, "token-owned-ring", -EINVAL);
'''
    assert canary_run.count(caller) == 1
    canary_run = canary_run.replace(caller, "", 1).replace(
        "amdgpu_gmc_pd_addr(s->pd.bo),\n\t\t\tamdgpu_ps4_uvd_token_requested() ? &token_ring : NULL);",
        "amdgpu_gmc_pd_addr(s->pd.bo));", 1)
    assert canary_run == function(old_canary, "amdgpu_ps4_map_canary_run"), "legacy caller changed"
    flow = new[new.index("/* TOKEN_FLOW_BEGIN:"):new.index("/* TOKEN_FLOW_END */")]
    context = function(new, "ps4_ready_ctx")
    # Direct new hardware stores: baseline, NO_FETCH-only, sole raw WPTR.
    stores = re.findall(r"WREG32\((\w+),\s*([^;]+)\);", flow)
    assert stores == [("mmUVD_CONTEXT_ID", "PS4_TOKEN_BASELINE"),
                      ("mmUVD_RBC_RB_CNTL", "PS4_TOKEN_RBC_FETCH"),
                      ("mmUVD_RBC_RB_WPTR", "plan.publish_wptr_raw")], stores
    loop = flow[flow.rindex("\tfor (;;) {"):]
    assert "dev_info(" not in loop and "dev_err(" not in loop
    assert loop.index("elapsed >= PS4_TOKEN_LIMIT_NS") < loop.index("sample.context == PS4_TOKEN_VALUE &&")
    selected_defines = defines(new)
    names = {"PS4_READY_VCPU_VA", "PS4_READY_RB_VA", "PS4_READY_IB_VA", "PS4_READY_BIT",
             "PS4_READY_HOLD", "PS4_READY_UVD_VCPU_CACHE_VMID"}
    ready_defines = "".join(selected_defines[name] for name in sorted(names))
    needed = set(re.findall(r"\b(?:mm[A-Z]\w+|ixUVD_\w+|UVD_\w+__(?:\w+))\b", flow + context + ready_defines))
    headers = [args.headers / "uvd/uvd_5_0_d.h", args.headers / "uvd/uvd_5_0_sh_mask.h",
               args.headers / "gmc/gmc_7_1_d.h"]
    macros = {}
    for path in headers:
        for name, value in re.findall(r"^#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)\s*$", path.read_text(), re.M):
            if name in needed:
                macros[name] = value
    assert set(macros) == needed, needed - set(macros)
    assert int(macros["UVD_STATUS__RBC_BUSY_MASK"], 0) == 1, "public UVD5 RBC_BUSY comparison pin changed"
    dispatch_start = new_run.index("if (status & PS4_READY_BIT) {")
    dispatch = new_run[dispatch_start:braces(new_run, dispatch_start)]
    header_text = (after / "amdgpu_ps4_uvd_ready.h").read_text()
    descriptor = re.search(r"struct amdgpu_ps4_uvd_ring\s*\{.*?\};", header_text, re.S)[0]
    unit = (here / "fixture.c").read_text()
    replacements = {
        "ACTUAL_REGISTER_DEFINITIONS": "\n".join(f"#define {k} {v}" for k, v in sorted(macros.items())),
        "ACTUAL_READY_DEFINITIONS": ready_defines, "ACTUAL_DESCRIPTOR": descriptor,
        "ACTUAL_OWNER_CHECK": function(new_canary, "ps4_canary_token_ring"),
        "ACTUAL_CONTEXT_READER": context, "ACTUAL_TOKEN_FLOW": flow,
        "ACTUAL_READY_DISPATCH": dispatch, "ACTUAL_EARLY_GATE": early,
    }
    for marker, content in replacements.items():
        assert unit.count("/* " + marker + " */") == 1
        unit = unit.replace("/* " + marker + " */", content)
    generated = args.report / "actual-token-fixture.c"
    generated.write_text(unit)
    shutil.copy2(after / HEADER, args.report / HEADER)
    shutil.copy2(after / HEADER, args.report / "ps4_uvd_ring_plan.h")
    planner_fixture = args.report / "ring_plan_test.c"
    shutil.copy2(args.planner_fixture, planner_fixture)
    cc = os.environ.get("CC", "cc")
    results = []
    with tempfile.TemporaryDirectory(prefix="ps4-token-host-") as directory:
        for name, path in (("actual-flow", generated), ("actual-planner", planner_fixture)):
            for label, flags in (("optimized", ["-O2"]),
                                 ("sanitized", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"])):
                binary = Path(directory) / (name + "-" + label)
                cmd = [cc, "-std=c11", "-UNDEBUG", "-Wall", "-Wextra", "-Werror", *flags, str(path), "-o", str(binary)]
                compiled = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
                (args.report / f"{name}-{label}-compile.log").write_text(compiled.stdout + compiled.stderr)
                assert compiled.returncode == 0, compiled.stderr
                run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=30)
                (args.report / f"{name}-{label}-run.log").write_text(run.stdout + run.stderr)
                assert run.returncode == 0, run.stdout + run.stderr
                results.append({"name": name, "mode": label, "output": run.stdout.strip()})
    receipt = {"status": "PASS_EXTRACTED_SOURCE_HOST_ONLY", "patch_sha256": sha(patch),
               "baseline_sha256": BASE, "output_sha256": {str(PREFIX / name): sha(after / name) for name in outputs},
               "planner_sha256": sha(args.planner), "planner_fixture_sha256": sha(args.planner_fixture),
               "fixture_sha256": sha(here / "fixture.c"), "checker_sha256": sha(Path(__file__)),
               "headers_sha256": {str(path): sha(path) for path in headers},
               "legacy_helpers_byte_unchanged": unchanged,
               "legacy_ready_and_canary_bodies_equal_after_gateoff_normalization": True,
               "direct_new_stores": stores, "results": results,
               "transport_status_policy": {"held_raw": [2], "empty_submitted_raw": [2, 3],
                    "ignored_after_fetch_mask": "0x00000001", "source_macro": "UVD_STATUS__RBC_BUSY_MASK",
                    "scope": "Public UVD5 comparison hypothesis; Liverpool busy meaning and DMA quiescence unproved"},
               "limits": ["Synthetic MMIO/time/exclusive-owner model; not a hardware emulator.",
                          "CPU barrier/HDP and terminal park are stubs; no hardware or kernel build executed.",
                          "No decoder, interrupt direction, DMA drain or general runtime ownership claim."]}
    (args.report / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("PASS: exact patch replay, unchanged legacy paths, actual extracted token flow and copied planner (optimized + ASan/UBSan)")


if __name__ == "__main__":
    main()
