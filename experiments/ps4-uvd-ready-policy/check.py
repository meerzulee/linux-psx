#!/usr/bin/env python3
"""Check the actual patched guard/poll body without touching a device."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile

BASE_SHA = "9f62e1781b7cc3c885e25f53a106b017cc2aa9cb1f5c110c13796e61c2b8e4cc"


def masked(text):
    pattern = r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    return re.sub(pattern, lambda m: "".join("\n" if c == "\n" else " " for c in m[0]), text, flags=re.S)


def function(text, name):
    clean = masked(text)
    match = re.search(r"\b" + name + r"\s*\([^;{}]*\)\s*\{", clean)
    assert match, name
    left = clean.index("{", match.start())
    depth, right = 1, left + 1
    while depth:
        depth += (clean[right] == "{") - (clean[right] == "}")
        right += 1
    return text[match.start():right]


def calls(text, names):
    clean = masked(text)
    found = []
    for match in re.finditer(r"\b(" + names + r")\s*\(", clean):
        left = clean.index("(", match.start())
        depth, right = 1, left + 1
        while depth:
            depth += (clean[right] == "(") - (clean[right] == ")")
            right += 1
        found.append(re.sub(r"\s+", "", clean[match.start():right]))
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--headers", type=Path, required=True, help="drivers/gpu/drm/amd/include/asic_reg")
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    args.report.mkdir(parents=True, exist_ok=True)
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    assert sha(args.baseline) == BASE_SHA, "baseline is not the frozen dev4 helper"
    old, new = args.baseline.read_text(), args.source.read_text()
    # Compare all device writes, indexed writes, clock and publication calls.
    hardware = r"WREG\w*|amdgpu_asic_set_uvd_clocks|amdgpu_device_flush_hdp|ps4_ready_ctx"
    assert calls(old, hardware) == calls(new, hardware), "hardware operation list changed"
    for name in ("ps4_ready_setup_held", "ps4_ready_programmed", "ps4_ready_park", "ps4_ready_snapshot", "ps4_ready_validate_firmware"):
        assert function(old, name) == function(new, name), name
    sites = calls(function(new, "amdgpu_ps4_uvd_ready_run"), "ps4_ready_fetch_guard_sample")
    assert sites == ["ps4_ready_fetch_guard_sample(adev,&guard_sample,false)"] * 2 + [
        "ps4_ready_fetch_guard_sample(adev,&guard_sample,ps4_uvd_poll_state)"]
    assert "static bool ps4_uvd_poll_state;" in new
    assert "module_param_named(ps4_uvd_poll_state, ps4_uvd_poll_state, bool, 0444)" in new
    assert function(old, "amdgpu_ps4_uvd_ready_requested") == function(new, "amdgpu_ps4_uvd_ready_requested")
    assert function(old, "amdgpu_ps4_uvd_ready_enabled") == function(new, "amdgpu_ps4_uvd_ready_enabled")
    guard = function(new, "ps4_ready_fetch_guard_sample")
    assert not calls(guard, r"WREG\w*|writel|iowrite\w*"), "guard must only read"
    enum_start = new.index("enum ps4_ready_guard_field")
    enum_end = new.index("static bool ps4_ready_fetch_guard_sample", enum_start)
    definitions = new[enum_start:enum_end]
    old_guard = function(old, "ps4_ready_fetch_guard_sample").replace("ps4_ready_fetch_guard_sample", "old_guard", 1)
    suffix = function(new, "amdgpu_ps4_uvd_ready_run")
    suffix = suffix[suffix.index("msleep(16);"):-1]
    needed = {"mmUVD_RBC_RB_CNTL", "mmUVD_MASTINT_EN", "mmUVD_RBC_IB_SIZE", "mmUVD_RBC_RB_WPTR", "mmUVD_RBC_RB_RPTR", "mmUVD_STATUS", "mmVM_CONTEXT1_PROTECTION_FAULT_STATUS", "UVD_RBC_RB_CNTL__RB_NO_FETCH_MASK", "UVD_RBC_RB_CNTL__RB_NO_UPDATE_MASK", "UVD_MASTINT_EN__VCPU_EN_MASK", "UVD_MASTINT_EN__SYS_EN_MASK"}
    headers = [args.headers / "uvd/uvd_5_0_d.h", args.headers / "uvd/uvd_5_0_sh_mask.h", args.headers / "gmc/gmc_7_1_d.h"]
    macros = {}
    for path in headers:
        for name, value in re.findall(r"^#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)\s*$", path.read_text(), re.M):
            if name in needed:
                macros[name] = value
    assert macros.keys() == needed, needed - macros.keys()
    fixture = Path(__file__).with_name("fixture.c").read_text()
    unit = fixture.replace("/* ACTUAL_REGISTER_DEFINITIONS */", "\n".join(f"#define {k} {v}" for k, v in sorted(macros.items())))
    unit = unit.replace("/* ACTUAL_GUARDS */", definitions + "\nstatic bool " + old_guard + "\nstatic bool " + guard)
    unit = unit.replace("/* ACTUAL_POLL_SUFFIX */", suffix)
    generated = args.report / "actual-policy-fixture.c"
    generated.write_text(unit)
    cc = os.environ.get("CC", "/usr/bin/cc")
    results = []
    with tempfile.TemporaryDirectory(prefix="ps4-uvd-policy-") as tmp:
        for label, extra in [("optimized", ["-O2"]), ("sanitized", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"])]:
            binary = str(Path(tmp) / label)
            cmd = [cc, "-std=c11", "-Wall", "-Wextra", "-Werror", *extra, str(generated), "-o", binary]
            built = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
            (args.report / f"{label}-compile.log").write_text(built.stdout + built.stderr)
            assert built.returncode == 0, built.stderr
            run = subprocess.run([binary], text=True, capture_output=True, timeout=30)
            (args.report / f"{label}-run.log").write_text(run.stdout + run.stderr)
            assert run.returncode == 0, run.stdout + run.stderr
            results.append({"label": label, "command": cmd, "output": run.stdout.strip()})
    receipt = {"status": "PASS_HOST_ONLY", "baseline_sha256": BASE_SHA, "source_sha256": sha(args.source), "header_sha256": {str(p): sha(p) for p in headers}, "static_hardware_call_list_unchanged": True, "earlier_strict_gates_unchanged": True, "results": results, "limits": ["Synthetic register/time/reset model; no hardware executed.", "Snapshot/report calls are stubs; their unchanged source is separately compared.", "No interrupt direction, firmware execution, decode, or DMA-quiescence proof."]}
    (args.report / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("PASS: actual old/new guard equivalence, exact 0/2 policy, real poll suffix, deadlines and retained terminal outcomes")


if __name__ == "__main__":
    main()
