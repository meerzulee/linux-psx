#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Replay the planned status-observation patch and host-test the
actual extracted observation flow over the frozen data-control candidate.
Not hardware or firmware emulation.
"""
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
import tempfile

PREFIX = Path("drivers/gpu/drm/amd/amdgpu")
READY = "amdgpu_ps4_uvd_ready.c"
# Frozen completed data-control candidate is the baseline being extended.
BASE = {
    READY: "6f4025a772f7f818ca7ee5dbe309ff8d7dc2bdb1b65dfe5becfaec39eb236287",
    "amdgpu_ps4_map_canary.c": "523a24339622bf81e2dd3e5d99f88cdfe77de1709b34f74f9369ad64f70b6305",
    "amdgpu_ps4_uvd_ready.h": "f49a2648015b8c23998f1e95cab6fc1c9ed157e076eadfa74116c926dd98347d",
    "amdgpu_ps4_uvd_ring_plan.h": "2299fd28005bdbcc2aa3c3664740f5d87a34cb7294c705c45facdde46d44df2e",
}
HEADERS = {
    "uvd/uvd_5_0_d.h": "ddee9d3d00fa475cd3f55813edcfb1955febc33300520483d38a0c017674faee",
    "uvd/uvd_5_0_sh_mask.h": "5d335f462b22162402b53b0d6cf5ff0f97114996171749d6e99803da19bc7612",
    "gmc/gmc_7_1_d.h": "389df3c6e6d530a05b81ca5ab7e4e58c48cb65ea746caab32c50a862ac0c287e",
}
PARAMETER = '''/* Separate default-off immutable PS4 UVD status-transition observation. */
static bool ps4_uvd_status_observation;
module_param_named(ps4_uvd_status_observation, ps4_uvd_status_observation, bool, 0444);
MODULE_PARM_DESC(ps4_uvd_status_observation,
	"One PS4 UVD STATUS bit16 transition observation of the EXP005 DATA0 packet; requires token+ready+poll_state, records bit16, never accepts (default 0)");

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
    patch = here / "0009-drm-amdgpu-ps4-uvd-status-observation.patch"
    args.report.mkdir(parents=True, exist_ok=False)
    before, after = args.baseline / PREFIX, args.source / PREFIX
    for name, expected in BASE.items():
        assert sha(before / name) == expected, "wrong frozen data-control baseline: " + name
    for name, expected in HEADERS.items():
        assert sha(args.headers / name) == expected, "public header drift: " + name
    assert re.findall(r"^diff --git a/(\S+) b/\S+$", patch.read_text(), re.M) == [str(PREFIX / READY)]
    with tempfile.TemporaryDirectory(prefix="ps4-stobs-replay-") as directory:
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
    flow = marked(new, "/* STATUS_OBS_FLOW_BEGIN:", "/* STATUS_OBS_FLOW_END */")
    early = marked(new, "/* STATUS_OBS_EARLY_BEGIN:", "/* STATUS_OBS_EARLY_END */")
    branch = marked(new, "/* STATUS_OBS_DISPATCH_BEGIN */", "/* STATUS_OBS_DISPATCH_END */")
    assert new.count(PARAMETER) == 1
    # marked() includes the added block's terminating newline. The next
    # blank line belongs to the frozen baseline and must remain byte-exact.
    normalized = new.replace(PARAMETER, "", 1).replace(flow, "", 1)
    normalized = normalized.replace(early, "", 1).replace(branch, "", 1)
    assert normalized == old, "source outside the four additive blocks changed"
    assert new.index(early) < new.index("r = ps4_ready_cache_vmid_preflight", new.index(early))
    assert new.index(early) < new.index("r = request_firmware_direct", new.index(early))
    token = marked(new, "/* TOKEN_FLOW_BEGIN:", "/* TOKEN_FLOW_END */")
    data_flow = marked(new, "/* DATA_CONTROL_FLOW_BEGIN:", "/* DATA_CONTROL_FLOW_END */")
    assert token == marked(old, "/* TOKEN_FLOW_BEGIN:", "/* TOKEN_FLOW_END */")
    assert data_flow == marked(old, "/* DATA_CONTROL_FLOW_BEGIN:", "/* DATA_CONTROL_FLOW_END */")
    unchanged = []
    for name in re.findall(r"^(?:static )?(?:__noreturn )?(?:bool|u32|int|void)\s+(\w+)\s*\(", old, re.M):
        if name != "amdgpu_ps4_uvd_ready_run":
            assert function(old, name) == function(new, name), name
            unchanged.append(name)

    # Constants asserted against the candidate bytes regardless of any
    # snapshot or feedback document (owner reconciliation note).
    for const, value in (("PS4_DATA_BASELINE", "0x43504435"),
                         ("PS4_DATA_VALUE", "0x55564435"),
                         ("PS4_DATA_CONTEXT_BASELINE", "0x43504335"),
                         ("PS4_DATA_CONTEXT_VALUE", "0x43545835")):
        assert re.search(r"#define %s %sU\n" % (const, value), old) and \
            re.search(r"#define %s %sU\n" % (const, value), new), const

    STATUS_BLOCK_ORIGINAL = """	/* Public UVD5 names bit0 RBC_BUSY; tolerate only after fetch. This
	 * comparison does not prove Liverpool busy semantics or DMA quiescence.
	 */
	if (!ps4_token_expect(s, 0, mmUVD_STATUS, s->status,
		phase == PS4_TOKEN_HELD ? ~0U : ~(u32)UVD_STATUS__RBC_BUSY_MASK,
		PS4_READY_BIT))
		return false;"""

    STATUS_BLOCK_STOBS = """	/* STOBS: submitted-phase STATUS additionally tolerates exactly bit16.
	 * The comparison observes bit16; it does not prove busy semantics,
	 * GPCOM naming, DMA quiescence or any Liverpool field specification.
	 */
	if (!ps4_stobs_status_expect(s, phase))
		return false;"""

    CTX_ORIGINAL = """#define TOKEN_CTX(index, mask, want) do { \\
	value = ps4_ready_ctx(adev, index, 0, 0, false); \\
	if (!ps4_token_expect(s, 1, index, value, mask, want)) return false; \\
} while (0)
	TOKEN_CTX(ixUVD_LMI_VMID_INTERNAL, 0x00ffffff, 0x00444444);
	TOKEN_CTX(ixUVD_LMI_VMID_INTERNAL2, ~0U, 0x44444444);
	TOKEN_CTX(ixUVD_LMI_VMID_INTERNAL3, 0xffff, 0x4444);
	TOKEN_CTX(ixUVD_LMI_CACHE_CTRL, UVD_LMI_CACHE_CTRL__VCPU_EN_MASK, 0);
#undef TOKEN_CTX"""

    CTX_MOVED = """	/* Indexed context reads moved after the window (STOBS review B1);
	 * ps4_stobs_ctx_sweep performs the identical frozen block.
	 */"""

    DECL_ORIGINAL = "{\n\tu32 value;\n\n\t*s = (struct ps4_token_sample) { 0 };"
    DECL_STOBS = "{\n\t*s = (struct ps4_token_sample) { 0 };"

    # Coverage parity: the copied core sample equals ps4_token_sample after
    # restoring all three documented differences (status rule, indexed
    # reads moved after the window, the then-unused u32 declaration). One
    # semantic rule; full direct-MMIO coverage stays per record.
    copy_fn = function(new, "ps4_stobs_core_sample")
    ref_fn = function(old, "ps4_token_sample")
    sweep_fn = function(new, "ps4_stobs_ctx_sweep")
    assert copy_fn.count(STATUS_BLOCK_STOBS) == 1
    assert ref_fn.count(STATUS_BLOCK_ORIGINAL) == 1
    assert copy_fn.count(CTX_MOVED) == 1 and ref_fn.count(CTX_ORIGINAL) == 1
    assert sweep_fn.count(CTX_ORIGINAL) == 1, "ctx sweep block must be verbatim"
    assert copy_fn.count(DECL_STOBS) == 1 and ref_fn.count(DECL_ORIGINAL) == 1
    assert copy_fn.count("ps4_ready_ctx(") == 0
    # One call site in TOKEN_CTX expands four times. Byte parity below
    # checks the exact indexed block; the fixture checks actual MMIO writes.
    assert flow.count("ps4_ready_ctx(") == 1, "indexed helper only in the sweep"
    restored = copy_fn.replace(STATUS_BLOCK_STOBS, STATUS_BLOCK_ORIGINAL)
    restored = restored.replace(CTX_MOVED, CTX_ORIGINAL)
    restored = restored.replace(DECL_STOBS, DECL_ORIGINAL)
    restored = restored.replace("ps4_stobs_core_sample", "ps4_token_sample")
    assert restored == ref_fn, "ps4_stobs_core_sample parity broken"

    # Stop-ordering and terminal-intervention declarations.
    assert flow.count("if (with_final) {") == 1
    assert flow.count('"STOBS_OBSERVATION_COMPLETED"') == 2
    assert flow.count("fault_base, reset_base, true);") == 2
    assert flow.count('"STOBS_BEFORE_SUBMIT_TIMEOUT"') == 2
    prepublish_segment = flow[flow.index("/* Immediate pre-publication read (review B4)"):
                              flow.index("WREG32(mmUVD_RBC_RB_WPTR, plan.publish_wptr_raw);")]
    assert "dev_info(" not in prepublish_segment and "dev_err(" not in prepublish_segment
    assert "ps4_data_sample(" not in prepublish_segment, "pre-WPTR sampling must not select indexed registers"
    assert "ps4_stobs_core_sample(" in prepublish_segment
    assert "sample.data = RREG32(mmUVD_GPCOM_VCPU_DATA0);" in prepublish_segment
    assert "publish_ns=%llu" in flow and "accepted=0" in flow
    assert "GUARD_FULL sampled=all_then_data0" in flow
    assert flow.index("PS4_UVD_STOBS FINAL_CORE") < flow.index("PS4_UVD_STOBS REC i=")

    stores = re.findall(r"WREG32\((\w+),\s*([^;]+)\);", flow)
    assert stores == [("mmUVD_GPCOM_VCPU_DATA0", "PS4_DATA_BASELINE"),
                      ("mmUVD_CONTEXT_ID", "PS4_DATA_CONTEXT_BASELINE"),
                      ("mmUVD_RBC_RB_CNTL", "PS4_TOKEN_RBC_FETCH"),
                      ("mmUVD_RBC_RB_WPTR", "plan.publish_wptr_raw")], stores
    loop = flow[flow.rindex("\tfor (;;) {\n") + len("\tfor (;;) {\n"):]
    assert "dev_info(" not in loop and "dev_err(" not in loop
    # Publication append precedes completion; the deadline wins before
    # sampling (B8), and the append precedes the completion stop.
    deadlines = loop.index("t_begin >= PS4_STOBS_LIMIT_NS")
    monitor = loop.index("ps4_stobs_monitor(adev, &sample")
    appends = loop.index("ps4_stobs_log[ps4_stobs_record_count++]")
    completions = loop.index("== PS4_STOBS_RECORDS)")
    assert deadlines < monitor < appends < completions
    # The observation flow must not contain any acceptance crediting.
    for banned in ("consecutive", "joint_samples", "data_seen", "context_seen",
                   "transport_seen", "first_data_ns", "first_joint_ns"):
        assert banned not in flow, banned
    stobs_defines = {}
    for name, value in re.findall(r"^#define (PS4_STOBS_\w+) (.+?)\s*$", flow, re.M):
        stobs_defines[name] = value
    assert stobs_defines == {
        "PS4_STOBS_LIMIT_NS": "(250ULL * NSEC_PER_MSEC)",
        "PS4_STOBS_BIT16": "0x00010000U",
        "PS4_STOBS_FINE_SLEEP_LOW": "100U", "PS4_STOBS_FINE_SLEEP_HIGH": "200U",
        "PS4_STOBS_COARSE_SLEEP_LOW": "12000U", "PS4_STOBS_COARSE_SLEEP_HIGH": "12500U",
        "PS4_STOBS_FINE_SAMPLES": "24U", "PS4_STOBS_COARSE_SAMPLES": "16U",
        "PS4_STOBS_RECORDS": "(PS4_STOBS_FINE_SAMPLES + PS4_STOBS_COARSE_SAMPLES)",
    }, stobs_defines

    # Exact unchanged indexed reader, including save/select/restore MMIO stores.
    context = function(new, "ps4_ready_ctx")
    selected = defines(new)
    ready_names = {"PS4_READY_VCPU_VA", "PS4_READY_RB_VA", "PS4_READY_IB_VA", "PS4_READY_BIT",
                   "PS4_READY_HOLD", "PS4_READY_UVD_VCPU_CACHE_VMID"}
    ready_defines = "".join(selected[name] for name in sorted(ready_names))
    needed = set(re.findall(r"\b(?:mm[A-Z]\w+|ixUVD_\w+|UVD_\w+__\w+)\b",
                            token + data_flow + flow + context + ready_defines))
    needed.add("mmUVD_GPCOM_VCPU_CMD")  # Only for a forbidden-store mutation test.
    macros = {}
    for name in HEADERS:
        for key, value in re.findall(r"^#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)\s*$", (args.headers / name).read_text(), re.M):
            if key in needed:
                macros[key] = value
    assert set(macros) == needed, needed - set(macros)
    assert int(macros["mmUVD_GPCOM_VCPU_DATA0"], 0) == 0x3bc4
    assert int(macros["mmUVD_CONTEXT_ID"], 0) == 0x3dbd
    assert int(macros["mmUVD_STATUS"], 0) == 0x3daf
    assert int(macros["UVD_STATUS__RBC_BUSY_MASK"], 0) == 1
    new_run = function(new, "amdgpu_ps4_uvd_ready_run")
    start = new_run.index("if (status & PS4_READY_BIT) {")
    dispatch = new_run[start:braces(new_run, start)]
    assert "STATUS_OBS_DISPATCH_BEGIN" in dispatch and "STATUS_OBS_DISPATCH_END" in dispatch
    descriptor = re.search(r"struct amdgpu_ps4_uvd_ring\s*\{.*?\};", (after / "amdgpu_ps4_uvd_ready.h").read_text(), re.S)[0]
    unit = (here / "fixture.c").read_text()
    replacements = {
        "ACTUAL_REGISTER_DEFINITIONS": "\n".join(f"#define {k} {v}" for k, v in sorted(macros.items())),
        "ACTUAL_READY_DEFINITIONS": ready_defines, "ACTUAL_DESCRIPTOR": descriptor,
        "ACTUAL_CONTEXT_READER": context, "ACTUAL_TOKEN_FLOW": token,
        "ACTUAL_DATA_CONTROL_FLOW": data_flow, "ACTUAL_STATUS_OBSERVATION_FLOW": flow,
        "ACTUAL_EARLY_GATE": early, "ACTUAL_READY_DISPATCH": dispatch,
    }
    for marker, content in replacements.items():
        assert unit.count("/* " + marker + " */") == 1
        unit = unit.replace("/* " + marker + " */", content)
    generated = args.report / "actual-status-observation-fixture.c"
    generated.write_text(unit)
    shutil.copy2(after / "amdgpu_ps4_uvd_ring_plan.h", args.report / "amdgpu_ps4_uvd_ring_plan.h")
    cc = shlex.split(os.environ.get("CC", "cc"))
    version = subprocess.run([*cc, "--version"], text=True, capture_output=True, check=True, timeout=15)
    target = subprocess.run([*cc, "-dumpmachine"], text=True, capture_output=True, check=True, timeout=15)
    toolchain = {"compiler_argv": cc, "compiler_path": shutil.which(cc[0]),
                 "compiler_version": version.stdout.strip(), "compiler_target": target.stdout.strip(),
                 "host": {"system": platform.system(), "machine": platform.machine(),
                          "release": platform.release(), "platform": platform.platform()}}
    (args.report / "toolchain.json").write_text(json.dumps(toolchain, indent=2) + "\n")
    results, sensitivity = [], []
    with tempfile.TemporaryDirectory(prefix="ps4-stobs-host-") as directory:
        binaries = Path(directory)
        for label, flags in (("optimized", ["-O2"]),
                             ("sanitized", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer"])):
            binary = binaries / label
            cmd = [*cc, "-std=c11", "-UNDEBUG", "-Wall", "-Wextra", "-Werror", *flags, str(generated), "-o", str(binary)]
            compiled = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
            (args.report / f"{label}-compile.log").write_text(compiled.stdout + compiled.stderr)
            assert compiled.returncode == 0, compiled.stderr
            run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=30)
            (args.report / f"{label}-run.log").write_text(run.stdout + run.stderr)
            results.append({"mode": label, "status": "PASS" if run.returncode == 0 and not run.stderr else "FAIL",
                            "returncode": run.returncode, "output": run.stdout.strip(),
                            "stderr": run.stderr, "flags": flags, "compile_command": cmd})
        if any(result["status"] != "PASS" for result in results):
            (args.report / "host-failure.json").write_text(json.dumps({
                "status": "FAIL_EXTRACTED_SOURCE_HOST_ONLY", "patch_sha256": sha(patch),
                "checker_sha256": sha(Path(__file__)), "fixture_sha256": sha(here / "fixture.c"),
                "generated_fixture_sha256": sha(generated), "results": results,
                "toolchain": toolchain,
                "sensitivity_tests_run": False, "hardware_acceptance": False,
            }, indent=2) + "\n")
            raise AssertionError("extracted-source host contract failure; see host-failure.json and run logs")
        # Deliberately broken extracted-source controls must fail the
        # independent publication and observation contracts. These modified
        # extraction units are never applied to the candidate or built as kernels.
        publication = "\tWREG32(mmUVD_RBC_RB_WPTR, plan.publish_wptr_raw);"
        assert flow.count(publication) == 1
        mutations = {
            "two-publications": flow.replace(publication, publication + "\n" + publication),
            "forbidden-cmd-zero": flow.replace(publication, "\tWREG32(mmUVD_GPCOM_VCPU_CMD, 0);\n" + publication),
            "status-whitelist": flow.replace("(s->status & mask) == (PS4_READY_BIT | PS4_STOBS_BIT16)",
                                             "(s->status & mask & PS4_STOBS_BIT16)"),
            "budget-completion-removed": flow.replace("if (ps4_stobs_record_count == PS4_STOBS_RECORDS)",
                                                      "if (false)"),
            "missing-deadline": flow.replace(
                "\t\tif (t_begin >= PS4_STOBS_LIMIT_NS)\n"
                "\t\t\tps4_stobs_stop(adev, \"STOBS_OBSERVATION_COMPLETED\", 0,",
                "\t\tif (false)\n"
                "\t\t\tps4_stobs_stop(adev, \"STOBS_OBSERVATION_COMPLETED\", 0,"),
            "no-pointer-regression-check": flow.replace("if (sample.core.rptr < last_rptr)", "if (sample.core.rptr < last_rptr && false)"),
            "coarse-cadence-removed": flow.replace("PS4_STOBS_COARSE_SLEEP_LOW,\n\t\t\t\t     PS4_STOBS_COARSE_SLEEP_HIGH",
                                                   "PS4_STOBS_FINE_SLEEP_LOW, PS4_STOBS_FINE_SLEEP_HIGH"),
            "copy-core-guard-removed": flow.replace("TOKEN_SAVED(mmUVD_RBC_RB_CNTL, s->rbc,\n\t\tphase == PS4_TOKEN_HELD ? PS4_TOKEN_RBC_HELD : PS4_TOKEN_RBC_FETCH);",
                                                    "\t\t/* deliberately removed by host sensitivity test */"),
            "selector-store-during-observation": flow.replace(
                "\tlast_rptr = 0;", "\tlast_rptr = 0;\n"
                "\tWREG32(mmUVD_CTX_INDEX, RREG32(mmUVD_CTX_INDEX));"),
            "error-terminal-relabelled": flow.replace(
                "if (result && error == 0 && submitted && records)",
                "if (result && submitted && records)"),
            "report-before-terminal-mmio": flow.replace(
                "\tif (with_final) {", "\tif (with_final) {\n"
                '\t\tdev_info(adev->dev, "PS4_UVD_STOBS CTX_SWEEP premature_report=1\\n");'),
            "sweep-after-terminal-core-failure": flow.replace(
                "\t\tif (final_valid) {\n\t\t\tsweep_valid = ps4_stobs_ctx_sweep",
                "\t\tif (true) {\n\t\t\tsweep_valid = ps4_stobs_ctx_sweep"),
            "extra-prepublication-selector-sweep": flow.replace(publication,
                "\tps4_ready_ctx(adev, ixUVD_LMI_VMID_INTERNAL, 0, 0, false);\n"
                "\tps4_ready_ctx(adev, ixUVD_LMI_VMID_INTERNAL2, 0, 0, false);\n"
                "\tps4_ready_ctx(adev, ixUVD_LMI_VMID_INTERNAL3, 0, 0, false);\n"
                "\tps4_ready_ctx(adev, ixUVD_LMI_CACHE_CTRL, 0, 0, false);\n" + publication),
            "unconditional-saved-guard": flow.replace("if (f->core.failed)", "if (true)"),
            "saved-sample-falsely-invalid": flow.replace("!f->core.failed", "0"),
            "sweep-failure-treated-as-success": flow.replace(
                "\tpersisted = bit16_first >= 0 && !clears && sets == 1;",
                "\tif (with_final && final_valid && !sweep_valid) error = 0;\n"
                "\tpersisted = bit16_first >= 0 && !clears && sets == 1;"),
        }
        witnesses = {
            "two-publications": "value == 16 && enabled && !submitted && !pointer_writes++",
            "forbidden-cmd-zero": "forbidden MMIO write: includes CMD even zero",
            "status-whitelist": "expected STOBS_STATUS_UNEXPECTED, got ",
            "budget-completion-removed": "expected STOBS_BIT16_ABSENT_MARKERS, got STOBS_RECORD_OVERFLOW",
            "missing-deadline": "expected STOBS_OBSERVATION_COMPLETED, got ",
            "no-pointer-regression-check": "expected STOBS_POINTER_REGRESSION, got ",
            "coarse-cadence-removed": 'report_number(record_line(24), "elapsed_us", 10) == 14300',
            "copy-core-guard-removed": "expected STOBS_CORE_GUARD, got ",
            "error-terminal-relabelled": "expected STOBS_CTX_SWEEP_GUARD, got ",
            "selector-store-during-observation": "CTX_INDEX write inside the observation window",
            "report-before-terminal-mmio": "MMIO read after post-publication reporting started",
            "sweep-after-terminal-core-failure": "failed terminal core sample was followed by indexed selector writes",
            "extra-prepublication-selector-sweep": "pre-publication selector-store count must equal 24",
            "unconditional-saved-guard": "non-guard stop emitted a GUARD record",
            "saved-sample-falsely-invalid": "saved sample validity disagrees with the deciding guard state",
            "sweep-failure-treated-as-success": "expected STOBS_CTX_SWEEP_GUARD, got ",
        }
        assert set(witnesses) == set(mutations)
        for label, changed in mutations.items():
            assert changed != flow, label
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
            witness = witnesses[label]
            assert witness in run.stderr, "wrong failure detected for: " + label
            sensitivity.append({"name": label, "status": "DETECTED_EXPECTED_FAILURE",
                                "returncode": run.returncode, "expected_failure_witness": witness,
                                "witness_observed": True, "compile_command": cmd})
    for name, expected in BASE.items():
        assert sha(before / name) == expected, "baseline changed during check: " + name
    receipt = {
        "status": "PASS_EXTRACTED_SOURCE_HOST_ONLY", "patch_sha256": sha(patch),
        "toolchain": toolchain,
        "baseline_sha256": BASE, "output_sha256": {str(PREFIX / name): sha(after / name) for name in BASE},
        "public_headers_commit": "a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea", "headers_sha256": HEADERS,
        "checker_sha256": sha(Path(__file__)), "fixture_sha256": sha(here / "fixture.c"),
        "generated_fixture_sha256": sha(generated),
        "extracted_status_flow_sha256": digest(flow.encode()),
        "extracted_data_flow_sha256": digest(data_flow.encode()),
        "unchanged_token_flow_sha256": digest(token.encode()),
        "legacy_helpers_byte_unchanged": unchanged,
        "core_sample_parity_after_restoring_declared_differences": True,
        "restored_differences": ["STATUS predicate", "indexed block moved to terminal sweep", "unused local declaration"],
        "entire_data_control_ready_c_equal_after_removing_four_additive_blocks": True,
        "canary_header_planner_byte_unchanged": True, "direct_new_stores": stores,
        "full_page_crc32": "cadc2d79", "results": results, "sensitivity_tests": sensitivity,
        "limits": ["Synthetic MMIO/time model; not hardware/firmware emulation or kernel compilation.",
                   "CPU barrier/HDP, sleep, logging, and terminal park use host substitutes.",
                   "Register readback, firmware side effects, bit16 semantics, containment and hardware acceptance remain unproved.",
                   "Old compiled audit and interrupted reviews remain incomplete; this check does not replace them.",
                   "A draft-design review does not approve the changed code it reviewed."]}
    (args.report / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("PASS: data-control-candidate replay, unchanged frozen flows, "
          "core-sample parity, actual observation flow optimized + ASan/UBSan, "
          f"{len(sensitivity)} injected defects detected")


if __name__ == "__main__":
    main()
