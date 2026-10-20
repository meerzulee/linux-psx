#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Replay the IB-only DATA0 patch and test extracted candidate functions.

Host MMIO observations/time are controlled inputs, not firmware or hardware
emulation. A successful run is not a kernel build, device test or decode claim.
Every invocation requires a new report directory and preserves failures.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import traceback

PREFIX = Path("drivers/gpu/drm/amd/amdgpu")
READY = "amdgpu_ps4_uvd_ready.c"
CANARY = "amdgpu_ps4_map_canary.c"
HEADER = "amdgpu_ps4_uvd_ready.h"
PLANNER = "amdgpu_ps4_uvd_ib_data_plan.h"
PATCH = "0010-drm-amdgpu-ps4-uvd-ib-data-marker.patch"
BASE = {
    READY: "60b55de93ef8a3b38626fbb5f063d76dd90cd52a5cd10b9250015fb384707518",
    CANARY: "523a24339622bf81e2dd3e5d99f88cdfe77de1709b34f74f9369ad64f70b6305",
    HEADER: "f49a2648015b8c23998f1e95cab6fc1c9ed157e076eadfa74116c926dd98347d",
    "amdgpu_ps4_uvd_ring_plan.h": "2299fd28005bdbcc2aa3c3664740f5d87a34cb7294c705c45facdde46d44df2e",
}
HEADERS = {
    "uvd/uvd_5_0_d.h": "ddee9d3d00fa475cd3f55813edcfb1955febc33300520483d38a0c017674faee",
    "uvd/uvd_5_0_sh_mask.h": "5d335f462b22162402b53b0d6cf5ff0f97114996171749d6e99803da19bc7612",
    "gmc/gmc_7_1_d.h": "389df3c6e6d530a05b81ca5ab7e4e58c48cb65ea746caab32c50a862ac0c287e",
}
PLANNER_SHA256 = "ef7fe8ed33507c93d07c62347b57add08daa8dd43727259440321c9f06fdf0e6"
IMAGES = {
    "main": {"sha256": "03c4bd41b6ca4bad2646b50a5cff20a99436b66f40cacb57496c326f341bf4df", "crc32": "e5b6afa6"},
    "ib": {"sha256": "8aac668c22b9a53b354a4747fb70797eef9b88ac3d365b959968edfc8c1fb7fb", "crc32": "e847443a"},
}


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


def marked(text, name):
    begin, end = "/* " + name + "_BEGIN", "/* " + name + "_END */"
    assert text.count(begin) == text.count(end) == 1, name
    start = text.rfind("\n", 0, text.index(begin)) + 1
    finish = text.index("\n", text.index(end)) + 1
    return text[start:finish]


def struct(text, name):
    return re.search(r"struct " + name + r"\s*\{.*?\};", text, re.S)[0]


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


def replace_one(text, old, new):
    assert text.count(old) == 1, "non-unique replacement: " + repr(old)
    return text.replace(old, new, 1)


def normalize(before, after):
    """Independent removal of declared additions; no generator imports."""
    result = dict(after)
    for key in ("IB_DATA_INCLUDE", "IB_DATA_PARAMETER", "IB_DATA_FLOW", "IB_DATA_EARLY", "IB_DATA_DISPATCH"):
        block = marked(result[READY], key)
        # PARAMETER deliberately contributes a separating blank line; the
        # other block terminators precede pre-existing baseline whitespace.
        if key == "IB_DATA_PARAMETER": block += "\n"
        result[READY] = replace_one(result[READY], block, "")
    result[READY] = replace_one(result[READY],
        "const struct amdgpu_ps4_uvd_ring *rb,\n\t\t\t\t      const struct amdgpu_ps4_uvd_ring *ib)\n{\n\tstruct ps4_ready_guard_sample",
        "const struct amdgpu_ps4_uvd_ring *rb)\n{\n\tstruct ps4_ready_guard_sample")
    for key in ("IB_DATA_OWNED_PAGE", "IB_DATA_BORROW_DECL", "IB_DATA_BORROW"):
        block = marked(result[CANARY], key)
        if key == "IB_DATA_OWNED_PAGE": block += "\n"
        result[CANARY] = replace_one(result[CANARY], block, "")
    result[CANARY] = replace_one(result[CANARY],
        "amdgpu_ps4_uvd_token_requested() ? &token_ring : NULL,\n\t\t\tamdgpu_ps4_uvd_ib_data_requested() ? &ib_page : NULL);",
        "amdgpu_ps4_uvd_token_requested() ? &token_ring : NULL);")
    result[HEADER] = replace_one(result[HEADER], marked(result[HEADER], "IB_DATA_REQUESTED"), "")
    result[HEADER] = replace_one(result[HEADER],
        "const struct amdgpu_ps4_uvd_ring *rb,\n\t\t\t\t      const struct amdgpu_ps4_uvd_ring *ib);",
        "const struct amdgpu_ps4_uvd_ring *rb);")
    for name in BASE:
        assert result[name] == before[name], "non-additive baseline change: " + name


def verify_and_extract(args, here):
    before_dir, after_dir = args.baseline / PREFIX, args.source / PREFIX
    outputs = sorted([*BASE, PLANNER])
    for name, digest in BASE.items():
        assert sha(before_dir / name) == digest, "frozen Revision C mismatch: " + name
    for name, digest in HEADERS.items():
        assert sha(args.headers / name) == digest, "public header mismatch: " + name
    assert sha(here / "ps4_uvd_ib_data_plan.h") == PLANNER_SHA256
    assert (after_dir / PLANNER).read_bytes() == (here / "ps4_uvd_ib_data_plan.h").read_bytes()
    patch = here / PATCH
    changed_paths = re.findall(r"^diff --git a/(\S+) b/\S+$", patch.read_text(), re.M)
    assert sorted(changed_paths) == sorted(str(PREFIX / name) for name in (READY, CANARY, HEADER, PLANNER))
    with tempfile.TemporaryDirectory(prefix="ps4-ibdata-replay-") as directory:
        root = Path(directory)
        (root / PREFIX).mkdir(parents=True)
        for name in BASE: shutil.copy2(before_dir / name, root / PREFIX / name)
        for mode in ("--check", None):
            cmd = ["git", "apply", *([mode] if mode else []), str(patch)]
            subprocess.run(cmd, cwd=root, check=True, capture_output=True, text=True)
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        assert files == sorted(str(PREFIX / name) for name in outputs)
        for name in outputs:
            assert (root / PREFIX / name).read_bytes() == (after_dir / name).read_bytes(), name
    old = {name: (before_dir / name).read_text() for name in BASE}
    new = {name: (after_dir / name).read_text() for name in outputs}
    normalize(old, new)
    ready, canary = new[READY], new[CANARY]
    flow = marked(ready, "IB_DATA_FLOW")
    assert flow == (here / "ib_data_flow.inc").read_text()
    for block in ("TOKEN_FLOW", "DATA_CONTROL_FLOW", "STATUS_OBS_FLOW"):
        assert marked(ready, block) == marked(old[READY], block), block
    early = marked(ready, "IB_DATA_EARLY")
    run = function(ready, "amdgpu_ps4_uvd_ready_run")
    assert run.index(early) < run.index("ps4_ready_cache_vmid_preflight") < run.index("request_firmware_direct")
    parameter = marked(ready, "IB_DATA_PARAMETER")
    assert "static bool ps4_uvd_ib_data;" in parameter
    assert "module_param_named(ps4_uvd_ib_data, ps4_uvd_ib_data, bool, 0444);" in parameter
    assert function(ready, "amdgpu_ps4_uvd_ib_data_requested") == \
        "bool amdgpu_ps4_uvd_ib_data_requested(void)\n{\n\treturn ps4_uvd_ib_data;\n}"
    # The copied per-record sampler has exactly one semantic difference:
    # submitted IB_SIZE is observable, never given countdown semantics.
    copied = function(ready, "ps4_ib_data_core_sample")
    restored = replace_one(copied, "ps4_ib_data_core_sample(", "ps4_stobs_core_sample(")
    restored = replace_one(restored,
        "\tif (phase != PS4_TOKEN_SUBMITTED)\n\t\tTOKEN_SAVED(mmUVD_RBC_IB_SIZE, s->ib_size, 0);",
        "\tTOKEN_SAVED(mmUVD_RBC_IB_SIZE, s->ib_size, 0);")
    assert restored == function(old[READY], "ps4_stobs_core_sample")
    stores = re.findall(r"WREG32\((\w+),\s*([^;]+)\);", flow)
    assert stores == [("mmUVD_GPCOM_VCPU_DATA0", "PS4_IBDATA_BASELINE"),
                      ("mmUVD_CONTEXT_ID", "PS4_IBDATA_CONTEXT_BASELINE"),
                      ("mmUVD_RBC_RB_CNTL", "PS4_TOKEN_RBC_FETCH"),
                      ("mmUVD_RBC_RB_WPTR", "plan.publish_wptr_raw")], stores
    loop = flow[flow.rindex("\tfor (;;) {"):]
    assert "dev_info(" not in loop and "dev_err(" not in loop and "WREG32(" not in loop
    assert loop.index("t_begin >= PS4_IBDATA_LIMIT_NS") < loop.index("ps4_ib_data_monitor")
    assert loop.index("t_end >= PS4_IBDATA_LIMIT_NS") < loop.index("++progress.valid_samples")
    assert "progress.consecutive >= 2" in loop
    stop_calls = re.findall(r'ps4_ib_data_stop\(adev,.*?\);', function(ready, "ps4_ib_data_run"), re.S)
    terminal_calls = [call for call in stop_calls if re.search(r'reset_base, true,', call)]
    assert len(terminal_calls) == 3
    assert all('"IBDATA_WINDOW_COMPLETED", 0,' in call and '&progress, true, true,' in call
               for call in terminal_calls)
    assert len(re.findall(r'reset_base, true,', flow)) == 3
    assert "ps4_ready_ctx(" not in flow
    assert "PS4_UVD_IB_DATA_SUBMIT_DWORDS" in loop
    for const, value in (("PS4_IBDATA_BASELINE", "0x43504442U"),
                         ("PS4_IBDATA_VALUE", "0x55564442U"),
                         ("PS4_IBDATA_CONTEXT_BASELINE", "0x43504342U")):
        assert re.search(r"^#define " + const + " " + value + "$", flow, re.M)
    # Pick exact original dependencies; no hand-written copy of probe logic.
    dependency_names = ["ps4_token_descriptor_valid", "ps4_token_expect", "ps4_token_sample",
                        "ps4_data_sample", "ps4_stobs_status_expect", "ps4_stobs_ctx_sweep"]
    selected = defines(ready)
    dependency = "".join(selected[name] for name in sorted(selected)
                         if name.startswith("PS4_TOKEN_") or name == "PS4_STOBS_BIT16")
    dependency += re.search(r"enum ps4_token_phase \{[^}]*\};", ready)[0] + "\n"
    dependency += struct(ready, "ps4_token_sample") + "\n" + struct(ready, "ps4_data_sample") + "\n"
    dependency += "\n\n".join(function(ready, name) for name in dependency_names)
    owner = function(canary, "ps4_canary_token_ring") + "\n" + function(canary, "ps4_canary_ib_data_page")
    context = function(ready, "ps4_ready_ctx")
    ready_names = {"PS4_READY_VCPU_VA", "PS4_READY_RB_VA", "PS4_READY_IB_VA", "PS4_READY_BIT",
                   "PS4_READY_HOLD", "PS4_READY_UVD_VCPU_CACHE_VMID"}
    ready_defines = "".join(selected[name] for name in sorted(ready_names))
    canary_defines = defines(canary)
    ready_defines += "".join(canary_defines[name] for name in
                             ("PS4_CANARY_VA", "PS4_READY_RB_SLOT", "PS4_READY_IB_SLOT", "PS4_READY_IB_BYTES", "PS4_READY_MAPPED_PAGES"))
    needed = set(re.findall(r"\b(?:mm[A-Z]\w+|ixUVD_\w+|UVD_\w+__\w+)\b",
                            dependency + flow + context + ready_defines))
    macros = {}
    for name in HEADERS:
        for key, value in re.findall(r"^#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)\s*$", (args.headers / name).read_text(), re.M):
            if key in needed: macros[key] = value
    assert set(macros) == needed, needed - set(macros)
    replacements = {
        "ACTUAL_REGISTER_DEFINITIONS": "\n".join(f"#define {key} {value}" for key, value in sorted(macros.items())),
        "ACTUAL_READY_DEFINITIONS": ready_defines,
        "ACTUAL_DESCRIPTOR": struct(new[HEADER], "amdgpu_ps4_uvd_ring"),
        "ACTUAL_OWNER_CHECK": owner,
        "ACTUAL_CONTEXT_READER": context,
        "ACTUAL_SAMPLER_DEPENDENCIES": dependency,
        "ACTUAL_IB_DATA_FLOW": flow,
        "ACTUAL_EARLY_GATE": early,
    }
    unit = (here / "fixture.c").read_text()
    for name, content in replacements.items():
        marker = "/* " + name + " */"
        assert unit.count(marker) == 1, name
        unit = unit.replace(marker, content)
    assert "/* ACTUAL_" not in unit
    generated = args.report / "actual-ib-data-fixture.c"
    generated.write_text(unit)
    for name in ("amdgpu_ps4_uvd_ring_plan.h", PLANNER):
        shutil.copy2(after_dir / name, args.report / name)
    return unit, generated, flow, stores, outputs


def mutant_units(unit, planner):
    """Each modified extraction must fail its named independent contract."""
    definitions = [
        ("high-address-truncated", "IB_ADDRESS_HIGH", "planner",
         "(ps4_uvd_ib_data_u32)(request.ib_gpu_base >> 32)", "(ps4_uvd_ib_data_u32)0"),
        ("ib-length-shifted", "IB_LENGTH_RAW16", "planner",
         "ring + 20U, PS4_UVD_IB_DATA_IB_LENGTH_DWORDS", "ring + 20U, PS4_UVD_IB_DATA_IB_LENGTH_DWORDS << 2"),
        ("weakened-status-mask", "STATUS_REQUIRES_READY", "unit",
         "(s->status & mask) == (PS4_READY_BIT | PS4_STOBS_BIT16)", "(s->status & mask & PS4_STOBS_BIT16)"),
        ("late-sample-credited", "LATE_SAMPLE_NO_CREDIT", "unit",
         "if (t_end >= PS4_IBDATA_LIMIT_NS)", "if (false)"),
        ("cpu-fresh-store", "CPU_DATA0_BASELINE_ONLY", "unit",
         "WREG32(mmUVD_GPCOM_VCPU_DATA0, PS4_IBDATA_BASELINE);", "WREG32(mmUVD_GPCOM_VCPU_DATA0, PS4_IBDATA_VALUE);"),
        ("missing-baseline-check", "DIRECT_BASELINE_CHECK_REQUIRED", "unit",
         "if (readback != PS4_IBDATA_BASELINE) {", "if (false) {"),
        ("ignored-sweep-failure", "SWEEP_FAILURE_NONZERO", "unit",
         "if (!sweep_valid) {", "if (false) {"),
        ("in-window-indexed-access", "NO_INDEXED_ACCESS_IN_WINDOW", "unit",
         "valid = ps4_ib_data_monitor(adev, &sample, pd_addr, cache_vmid,",
         "(void)ps4_ready_ctx(adev, ixUVD_LMI_VMID_INTERNAL, 0, 0, false);\n\t\tvalid = ps4_ib_data_monitor(adev, &sample, pd_addr, cache_vmid,"),
    ]
    for name, witness, where, target, replacement in definitions:
        content = unit if where == "unit" else planner
        changed = replace_one(content, target, replacement)
        yield name, witness, changed if where == "unit" else unit, changed if where == "planner" else planner


def run_checks(args, here, metadata):
    unit, generated, flow, stores, outputs = verify_and_extract(args, here)
    cc = shlex.split(os.environ.get("CC", "cc"))
    version = subprocess.run([*cc, "--version"], text=True, capture_output=True, check=True, timeout=15)
    target = subprocess.run([*cc, "-dumpmachine"], text=True, capture_output=True, check=True, timeout=15)
    toolchain = {"compiler_argv": cc, "compiler_path": shutil.which(cc[0]),
                 "compiler_version": version.stdout.strip(), "compiler_target": target.stdout.strip(),
                 "host": {"system": platform.system(), "machine": platform.machine(),
                          "release": platform.release(), "platform": platform.platform()}}
    (args.report / "toolchain.json").write_text(json.dumps(toolchain, indent=2) + "\n")
    metadata.update({"patch_sha256": sha(here / PATCH), "baseline_sha256": BASE,
                     "output_sha256": {str(PREFIX / name): sha(args.source / PREFIX / name) for name in outputs},
                     "planner_sha256": sha(here / "ps4_uvd_ib_data_plan.h"),
                     "generated_fixture_sha256": sha(generated), "toolchain": toolchain,
                     "headers_sha256": {name: sha(args.headers / name) for name in HEADERS},
                     "generator_sha256": sha(here / "build_candidate.py"),
                     "flow_sha256": sha(here / "ib_data_flow.inc"),
                     "direct_new_stores": stores, "results": [], "sensitivity": []})
    (args.report / "input-identities.json").write_text(json.dumps(metadata, indent=2) + "\n")
    flags_common = ["-std=c11", "-UNDEBUG", "-Wall", "-Wextra", "-Werror"]
    with tempfile.TemporaryDirectory(prefix="ps4-ibdata-host-") as directory:
        binaries = Path(directory)
        for label, flags in (("optimized", ["-O2"]),
                             ("sanitized", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"])):
            binary = binaries / label
            cmd = [*cc, *flags_common, *flags, str(generated), "-o", str(binary)]
            compiled = subprocess.run(cmd, text=True, capture_output=True, timeout=90)
            (args.report / f"{label}-compile.log").write_text(compiled.stdout + compiled.stderr)
            assert compiled.returncode == 0, f"{label} compile failed; see preserved log"
            images = [args.report / f"{label}-{name}.bin" for name in IMAGES]
            run = subprocess.run([str(binary), *map(str, images)], text=True, capture_output=True, timeout=40)
            (args.report / f"{label}-run.log").write_text(run.stdout + run.stderr)
            result = {"mode": label, "status": "PASS" if run.returncode == 0 and not run.stderr else "FAIL",
                      "returncode": run.returncode, "output": run.stdout.strip(), "stderr": run.stderr,
                      "flags": flags, "compile_command": cmd}
            metadata["results"].append(result)
            if result["status"] == "PASS":
                import zlib
                for (name, expected), image in zip(IMAGES.items(), images):
                    assert image.stat().st_size == 4096
                    assert sha(image) == expected["sha256"]
                    assert f"{zlib.crc32(image.read_bytes()):08x}" == expected["crc32"]
                match = re.fullmatch(r"PASS: (\d+) actual-source cases; controlled MMIO/time, no hardware emulation", run.stdout.strip())
                assert match
                result["cases"] = int(match[1])
        assert all(result["status"] == "PASS" for result in metadata["results"]), "actual-source host run failed"
        assert metadata["results"][0]["output"] == metadata["results"][1]["output"]
        planner = (args.report / PLANNER).read_text()
        for name, witness, changed_unit, changed_planner in mutant_units(unit, planner):
            mutant_dir = args.report / "sensitivity" / name
            mutant_dir.mkdir(parents=True)
            source = mutant_dir / "fixture.c"
            source.write_text(changed_unit)
            (mutant_dir / PLANNER).write_text(changed_planner)
            shutil.copy2(args.report / "amdgpu_ps4_uvd_ring_plan.h", mutant_dir / "amdgpu_ps4_uvd_ring_plan.h")
            binary = binaries / name
            cmd = [*cc, *flags_common, "-O2", str(source), "-o", str(binary)]
            compiled = subprocess.run(cmd, text=True, capture_output=True, timeout=90)
            (mutant_dir / "compile.log").write_text(compiled.stdout + compiled.stderr)
            assert compiled.returncode == 0, f"mutant {name} did not compile"
            run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=40)
            (mutant_dir / "run.log").write_text(run.stdout + run.stderr)
            found = re.findall(r"ASSERTION\[([^\]]+)\]", run.stderr)
            detected = run.returncode == 86 and found == [witness]
            metadata["sensitivity"].append({"name": name, "detected": detected,
                "returncode": run.returncode, "expected_witness": witness,
                "observed_witness": found[0] if len(found) == 1 else found,
                "source_sha256": sha(source), "planner_sha256": sha(mutant_dir / PLANNER),
                "compile_command": cmd})
            assert detected, f"mutant {name}: expected named witness {witness}, observed {found} / rc {run.returncode}"
    metadata.update({"status": "PASS_EXTRACTED_SOURCE_HOST_ONLY", "image_identities": IMAGES,
        "normalization": "exact Revision C bytes after removing declared additions and reversing API extension",
        "sampler_parity": "Revision C direct sampler with only submitted IB_SIZE comparison omitted",
        "limits": ["Controlled register observations and time, not UVD/firmware emulation.",
                   "Host stubs do not establish cache/DMA/IRQ behavior or hardware ownership.",
                   "No kernel build, compiled audit, hardware test or video decoding performed.",
                   "Endpoint indexed checks do not establish indexed validity within the observation window."]})
    return metadata


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "source", "headers", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    for name in ("baseline", "source", "headers", "report"):
        setattr(args, name, getattr(args, name).resolve())
    args.report.mkdir(parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent
    metadata = {"status": "INCOMPLETE_EXTRACTED_SOURCE_HOST_ONLY", "invocation": sys.argv,
        "cwd": os.getcwd(), "baseline": str(args.baseline), "source": str(args.source),
        "checker_sha256": sha(Path(__file__)), "fixture_sha256": sha(here / "fixture.c"),
        "hardware_acceptance": False, "decode_demonstrated": False}
    (args.report / "invocation.json").write_text(json.dumps(metadata, indent=2) + "\n")
    try:
        receipt = run_checks(args, here, metadata)
    except BaseException as exc:
        metadata.update({"status": "FAIL_EXTRACTED_SOURCE_HOST_ONLY",
                         "failure": str(exc), "traceback": traceback.format_exc()})
        (args.report / "failure.json").write_text(json.dumps(metadata, indent=2) + "\n")
        raise
    (args.report / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("PASS: exact patch replay and extracted source; optimized + ASan/UBSan; all named sensitivity witnesses")


if __name__ == "__main__":
    main()
