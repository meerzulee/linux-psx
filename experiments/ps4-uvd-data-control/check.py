#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Replay the DATA0 control patch and test its actual source on host-only models."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

PREFIX = Path("drivers/gpu/drm/amd/amdgpu")
READY = "amdgpu_ps4_uvd_ready.c"
BASE = {
    READY: "1c8abc36e63b71fc3584858c85641f62aac79fd28ebd20918bea6e983079fcb9",
    "amdgpu_ps4_map_canary.c": "523a24339622bf81e2dd3e5d99f88cdfe77de1709b34f74f9369ad64f70b6305",
    "amdgpu_ps4_uvd_ready.h": "f49a2648015b8c23998f1e95cab6fc1c9ed157e076eadfa74116c926dd98347d",
    "amdgpu_ps4_uvd_ring_plan.h": "2299fd28005bdbcc2aa3c3664740f5d87a34cb7294c705c45facdde46d44df2e",
}
HEADERS = {
    "uvd/uvd_5_0_d.h": "ddee9d3d00fa475cd3f55813edcfb1955febc33300520483d38a0c017674faee",
    "uvd/uvd_5_0_sh_mask.h": "5d335f462b22162402b53b0d6cf5ff0f97114996171749d6e99803da19bc7612",
    "gmc/gmc_7_1_d.h": "389df3c6e6d530a05b81ca5ab7e4e58c48cb65ea746caab32c50a862ac0c287e",
}
PARAMETER = '''/* Separate default-off immutable main-ring DATA0 readback control. */
static bool ps4_uvd_data_control;
module_param_named(ps4_uvd_data_control, ps4_uvd_data_control, bool, 0444);
MODULE_PARM_DESC(ps4_uvd_data_control,
	"One PS4 UVD DATA0 readback control; requires token+ready+poll_state, always parks (default 0)");

'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    return digest(path.read_bytes())


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


def marked(text, begin, end):
    assert text.count(begin) == text.count(end) == 1
    start = text.rfind("\n", 0, text.index(begin)) + 1
    finish = text.index("\n", text.index(end)) + 1
    return text[start:finish]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "source", "headers", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    here = Path(__file__).resolve().parent
    patch = here / "0008-drm-amdgpu-ps4-uvd-data-readback-control.patch"
    args.report.mkdir(parents=True, exist_ok=False)
    before, after = args.baseline / PREFIX, args.source / PREFIX
    for name, expected in BASE.items():
        assert sha(before / name) == expected, "wrong frozen token-dev2 baseline: " + name
    for name, expected in HEADERS.items():
        assert sha(args.headers / name) == expected, "public header drift: " + name
    assert re.findall(r"^diff --git a/(\S+) b/\S+$", patch.read_text(), re.M) == [str(PREFIX / READY)]
    with tempfile.TemporaryDirectory(prefix="ps4-data-replay-") as directory:
        root = Path(directory)
        (root / PREFIX).mkdir(parents=True)
        for name in BASE:
            shutil.copy2(before / name, root / PREFIX / name)
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=root, check=True)
        subprocess.run(["git", "apply", str(patch)], cwd=root, check=True)
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        assert files == sorted((PREFIX / name).as_posix() for name in BASE), files
        for name in BASE:
            assert (root / PREFIX / name).read_bytes() == (after / name).read_bytes(), name
            if name != READY:
                assert (after / name).read_bytes() == (before / name).read_bytes(), name
    old, new = (before / READY).read_text(), (after / READY).read_text()
    flow = marked(new, "/* DATA_CONTROL_FLOW_BEGIN:", "/* DATA_CONTROL_FLOW_END */")
    early = marked(new, "/* DATA_CONTROL_EARLY_BEGIN:", "/* DATA_CONTROL_EARLY_END */")
    branch = marked(new, "/* DATA_CONTROL_DISPATCH_BEGIN */", "/* DATA_CONTROL_DISPATCH_END */")
    assert new.count(PARAMETER) == 1
    normalized = new.replace(PARAMETER, "", 1).replace(flow + "\n", "", 1)
    normalized = normalized.replace(early, "", 1).replace(branch, "", 1)
    assert normalized == old, "source outside the four additive blocks changed"
    assert new.index(early) < new.index("r = ps4_ready_cache_vmid_preflight", new.index(early))
    assert new.index(early) < new.index("r = request_firmware_direct", new.index(early))
    token = marked(new, "/* TOKEN_FLOW_BEGIN:", "/* TOKEN_FLOW_END */")
    assert token == marked(old, "/* TOKEN_FLOW_BEGIN:", "/* TOKEN_FLOW_END */")
    unchanged = []
    for name in re.findall(r"^(?:static )?(?:__noreturn )?(?:bool|u32|int|void)\s+(\w+)\s*\(", old, re.M):
        if name != "amdgpu_ps4_uvd_ready_run":
            assert function(old, name) == function(new, name), name
            unchanged.append(name)
    stores = re.findall(r"WREG32\((\w+),\s*([^;]+)\);", flow)
    assert stores == [("mmUVD_GPCOM_VCPU_DATA0", "PS4_DATA_BASELINE"),
                      ("mmUVD_CONTEXT_ID", "PS4_DATA_CONTEXT_BASELINE"),
                      ("mmUVD_RBC_RB_CNTL", "PS4_TOKEN_RBC_FETCH"),
                      ("mmUVD_RBC_RB_WPTR", "plan.publish_wptr_raw")], stores
    loop = flow[flow.rindex("\tfor (;;) {"):]
    assert "dev_info(" not in loop and "dev_err(" not in loop
    assert loop.index("elapsed >= PS4_DATA_LIMIT_NS") < loop.index("if (!valid)")
    assert loop.index("elapsed >= PS4_DATA_LIMIT_NS") < loop.index("++progress.consecutive == 2")
    values = [int(x, 16) for x in re.findall(r"^#define PS4_DATA_(?:BASELINE|VALUE|CONTEXT_BASELINE|CONTEXT_VALUE) (0x[0-9a-fA-F]+)U$", flow, re.M)]
    assert values == [0x43504435, 0x55564435, 0x43504335, 0x43545835]
    assert len(set(values)) == 4
    # Exact unchanged indexed reader, including save/select/restore MMIO stores.
    context = function(new, "ps4_ready_ctx")
    selected = defines(new)
    ready_names = {"PS4_READY_VCPU_VA", "PS4_READY_RB_VA", "PS4_READY_IB_VA", "PS4_READY_BIT",
                   "PS4_READY_HOLD", "PS4_READY_UVD_VCPU_CACHE_VMID"}
    ready_defines = "".join(selected[name] for name in sorted(ready_names))
    needed = set(re.findall(r"\b(?:mm[A-Z]\w+|ixUVD_\w+|UVD_\w+__\w+)\b", token + flow + context + ready_defines))
    needed.add("mmUVD_GPCOM_VCPU_CMD")  # Only for a forbidden-store mutation test.
    macros = {}
    for name in HEADERS:
        for key, value in re.findall(r"^#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)\s*$", (args.headers / name).read_text(), re.M):
            if key in needed:
                macros[key] = value
    assert set(macros) == needed, needed - set(macros)
    assert int(macros["mmUVD_GPCOM_VCPU_DATA0"], 0) == 0x3bc4
    assert int(macros["mmUVD_CONTEXT_ID"], 0) == 0x3dbd
    assert int(macros["UVD_STATUS__RBC_BUSY_MASK"], 0) == 1
    new_run = function(new, "amdgpu_ps4_uvd_ready_run")
    start = new_run.index("if (status & PS4_READY_BIT) {")
    dispatch = new_run[start:braces(new_run, start)]
    descriptor = re.search(r"struct amdgpu_ps4_uvd_ring\s*\{.*?\};", (after / "amdgpu_ps4_uvd_ready.h").read_text(), re.S)[0]
    unit = (here / "fixture.c").read_text()
    replacements = {
        "ACTUAL_REGISTER_DEFINITIONS": "\n".join(f"#define {k} {v}" for k, v in sorted(macros.items())),
        "ACTUAL_READY_DEFINITIONS": ready_defines, "ACTUAL_DESCRIPTOR": descriptor,
        "ACTUAL_CONTEXT_READER": context, "ACTUAL_TOKEN_FLOW": token,
        "ACTUAL_DATA_CONTROL_FLOW": flow, "ACTUAL_EARLY_GATE": early,
        "ACTUAL_READY_DISPATCH": dispatch,
    }
    for marker, content in replacements.items():
        assert unit.count("/* " + marker + " */") == 1
        unit = unit.replace("/* " + marker + " */", content)
    generated = args.report / "actual-data-control-fixture.c"
    generated.write_text(unit)
    shutil.copy2(after / "amdgpu_ps4_uvd_ring_plan.h", args.report / "amdgpu_ps4_uvd_ring_plan.h")
    cc = shlex.split(os.environ.get("CC", "cc"))
    results, sensitivity = [], []
    with tempfile.TemporaryDirectory(prefix="ps4-data-host-") as directory:
        binaries = Path(directory)
        for label, flags in (("optimized", ["-O2"]),
                             ("sanitized", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"])):
            binary = binaries / label
            cmd = [*cc, "-std=c11", "-UNDEBUG", "-Wall", "-Wextra", "-Werror", *flags, str(generated), "-o", str(binary)]
            compiled = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
            (args.report / f"{label}-compile.log").write_text(compiled.stdout + compiled.stderr)
            assert compiled.returncode == 0, compiled.stderr
            run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=30)
            (args.report / f"{label}-run.log").write_text(run.stdout + run.stderr)
            assert run.returncode == 0, run.stdout + run.stderr
            results.append({"mode": label, "status": "PASS", "output": run.stdout.strip(), "flags": flags})
        # These deliberately broken extracted-source controls must fail the
        # independent image/store/event assertions. They are never patched or built.
        publication = "\tWREG32(mmUVD_RBC_RB_WPTR, plan.publish_wptr_raw);"
        assert flow.count(publication) == 1
        mutations = {
            "zero-publications": flow.replace(publication, "\t/* deliberately removed by host sensitivity test */"),
            "two-publications": flow.replace(publication, publication + "\n" + publication),
            "forbidden-cmd-zero": flow.replace(publication, "\tWREG32(mmUVD_GPCOM_VCPU_CMD, 0);\n" + publication),
            "one-confirmation": flow.replace("++progress.consecutive == 2", "++progress.consecutive == 1"),
            "missing-deadline": flow.replace("if (elapsed >= PS4_DATA_LIMIT_NS) {", "if (false) {"),
            "no-pointer-regression-check": flow.replace("if (sample.core.rptr < progress.last_rptr)", "if (false)"),
        }
        for label, changed in mutations.items():
            assert changed != flow
            mutant = args.report / ("sensitivity-" + label + ".c")
            mutant.write_text(unit.replace(flow, changed, 1))
            binary = binaries / label
            cmd = [*cc, "-std=c11", "-UNDEBUG", "-Wall", "-Wextra", "-Werror", "-O2", str(mutant), "-o", str(binary)]
            compiled = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
            (args.report / f"sensitivity-{label}-compile.log").write_text(compiled.stdout + compiled.stderr)
            assert compiled.returncode == 0, compiled.stderr
            run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=30)
            (args.report / f"sensitivity-{label}-run.log").write_text(run.stdout + run.stderr)
            assert run.returncode != 0, "fixture did not detect injected defect: " + label
            sensitivity.append({"name": label, "status": "DETECTED_EXPECTED_FAILURE", "returncode": run.returncode})
    for name, expected in BASE.items():
        assert sha(before / name) == expected, "baseline changed during check: " + name
    receipt = {
        "status": "PASS_EXTRACTED_SOURCE_HOST_ONLY", "patch_sha256": sha(patch),
        "baseline_sha256": BASE, "output_sha256": {str(PREFIX / name): sha(after / name) for name in BASE},
        "public_headers_commit": "a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea", "headers_sha256": HEADERS,
        "checker_sha256": sha(Path(__file__)), "fixture_sha256": sha(here / "fixture.c"),
        "generated_fixture_sha256": sha(generated), "extracted_data_flow_sha256": digest(flow.encode()),
        "unchanged_token_flow_sha256": digest(token.encode()), "legacy_helpers_byte_unchanged": unchanged,
        "entire_original_ready_c_equal_after_removing_four_additive_blocks": True,
        "canary_header_planner_byte_unchanged": True, "direct_new_stores": stores,
        "full_page_crc32": "cadc2d79", "results": results, "sensitivity_tests": sensitivity,
        "limits": ["Synthetic MMIO/time model; not hardware/firmware emulation or kernel compilation.",
                   "CPU barrier/HDP, sleep, logging, and terminal park use host substitutes.",
                   "Register readback, firmware side effects, containment and hardware acceptance remain unproved.",
                   "Old compiled audit and interrupted reviews remain incomplete; this check does not replace them."]}
    (args.report / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("PASS: exact ready.c-only patch replay, unchanged baseline source, actual control/guard flow optimized + ASan/UBSan, six injected defects detected")


if __name__ == "__main__":
    main()
