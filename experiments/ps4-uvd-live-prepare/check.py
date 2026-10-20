#!/usr/bin/env python3
"""Check extracted preparation helpers only; no devices or kernel build.

The host model checks software ownership, cached readers and scoped wiring.
It does not emulate GPU execution, kernel teardown, interrupt locks or firmware.
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
import tempfile

HERE = Path(__file__).resolve().parent
WS = HERE.parents[2]
REL = Path("drivers/gpu/drm/amd/amdgpu")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(path: Path, value: object) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def marked(text: str, name: str) -> str:
    begin, end = f"/* {name}_BEGIN */", f"/* {name}_END */"
    assert text.count(begin) == text.count(end) == 1, name
    return text[text.index(begin):text.index(end) + len(end)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidate", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, default=WS / "lab/uvd-2026-10-20/live-prepare/source/baseline")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cc", default=shutil.which("clang") or shutil.which("cc"))
    args = ap.parse_args()
    assert args.cc, "Native C compiler required"
    assert not args.out.exists(), "Preserve previous evidence; use a new output directory"
    args.out.mkdir(parents=True, mode=0o700)
    (args.out / "inputs").mkdir()
    for name in ("check.py", "fixture.c"):
        (args.out / "inputs" / name).write_bytes((HERE / name).read_bytes())
    source_receipt = args.candidate.parent / "SOURCE-RECEIPT.json"
    receipt = json.loads(source_receipt.read_text())
    sources = {}
    for name, digest in receipt["candidate_files"].items():
        raw = (args.candidate / REL / name).read_bytes()
        assert sha(raw) == digest, f"Candidate changed: {name}"
        sources[name] = raw.decode()
    patch = args.candidate.parent / receipt["patch"]["file"]
    assert sha(patch.read_bytes()) == receipt["patch"]["sha256"]
    with tempfile.TemporaryDirectory(prefix="psx-live-replay-") as temporary:
        root = Path(temporary)
        for name, digest in receipt["baseline_files"].items():
            raw = (args.baseline / REL / name).read_bytes()
            assert sha(raw) == digest, f"Baseline changed: {name}"
            dest = root / REL / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
        for mode in ("--check", "--apply"):
            argv = ["git", "apply"] + ([mode] if mode == "--check" else []) + [str(patch.resolve())]
            result = subprocess.run(argv, cwd=root, text=True, capture_output=True)
            save(args.out / f"patch{mode}.json", {"argv": argv, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
            assert result.returncode == 0, "Patch replay failed"
        for name, digest in receipt["candidate_files"].items():
            assert sha((root / REL / name).read_bytes()) == digest, f"Replay differs: {name}"

    canary = sources["amdgpu_ps4_map_canary.c"]
    device = sources["amdgpu_device.c"]
    debugfs = sources["amdgpu_debugfs.c"]
    header = sources["amdgpu_ps4_live_prepare.h"]
    for include in ("#include <linux/spinlock.h>\n", "#include <linux/types.h>\n"):
        assert header.count(include) == 1
        header = header.replace(include, "")
    flow = marked(canary, "LIVE_PREPARE_STATE")
    debug = marked(debugfs, "LIVE_PREPARE_DEBUGFS")
    constants = []
    for name in ("PS4_READY_R_BYTES", "PS4_READY_IB_BYTES", "PS4_READY_MAPPED_PAGES"):
        matches = re.findall(r"^#define " + name + r" .+$", canary, re.M)
        assert len(matches) == 1, name
        constants.extend(matches)
    early = "\tif (amdgpu_ps4_map_canary_enabled(adev)) {\n\t\tif (!adev->ps4_canary_fences_initialized) {\n\t\t\tamdgpu_fence_driver_hw_init(adev);\n\t\t\tadev->ps4_canary_fences_initialized = true;\n\t\t}\n\t\tamdgpu_ps4_map_canary_run(adev);\n\t}"
    outer = "\tif (!adev->ps4_canary_fences_initialized)\n\t\tamdgpu_fence_driver_hw_init(adev);"
    assert device.count(early) == device.count(outer) == 1, "Fence call sites changed"
    assert debugfs.count('debugfs_create_file("amdgpu_ps4_live_prepare", 0400, root, adev,') == 1
    assert '.write' not in debug and 'module_param_named(ps4_uvd_live_prepare, ps4_uvd_live_prepare, bool, 0444);' in flow
    assert canary.index('if (s->prepare_only) {\n\t\tr = ps4_canary_exclusive_init') < canary.index('if (s->ready_mode) {\n\t\tstruct amdgpu_ps4_uvd_ring')
    branch = canary.split('if (s->prepare_only) {\n\t\tr = ps4_canary_exclusive_init', 1)[1].split('if (s->ready_mode) {', 1)[0]
    assert '\n\t\treturn;\n\t}\n\t' in branch and 'amdgpu_ps4_uvd_ready_run' not in branch
    assert device.index('amdgpu_ps4_live_prepare_fini(adev);') > device.index('amdgpu_device_ip_fini_early(adev);\n\n\t/* Only completed')
    assert device.index('amdgpu_ps4_live_prepare_fini(adev);') < device.index('ttm_device_clear_dma_mappings(&adev->mman.bdev);')
    assert 'amdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_VMID_FINI);' in sources['amdgpu_ids.c']
    for event in ('GART_ENABLE', 'GART_DISABLE'):
        assert f'amdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_{event});' in sources['gmc_v7_0.c']
    for event in ('HW_FINI', 'SUSPEND', 'RESET'):
        assert f'amdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_{event});' in device
    driver = sources['amdgpu_drv.c']
    probe = driver[driver.index('static int amdgpu_pci_probe('):driver.index('static void\namdgpu_pci_remove(')]
    loaded = marked(probe, 'LIVE_PREPARE_PROBE_LOADED')
    registered = marked(probe, 'LIVE_PREPARE_PROBE_REGISTERED')
    probe_error = marked(probe, 'LIVE_PREPARE_PROBE_ERROR')
    assert probe.count('bool ps4_prepare_loaded = false;') == probe.count('bool ps4_prepare_registered = false;') == 1
    assert probe.index('ret = amdgpu_driver_load_kms(') < probe.index(loaded) < probe.index('retry_init:')
    assert probe.index('goto retry_init;') < probe.index(registered) < probe.index('ret = amdgpu_xcp_dev_register(')
    assert probe.index('err_pci:') < probe.index(probe_error) < probe.index('pci_disable_device(pdev);')

    template = (HERE / "fixture.c").read_text()
    pieces = {"CONSTANTS": "\n".join(constants), "HEADER": header, "FLOW": flow, "DEBUGFS": debug, "EARLY_FENCE": early, "OUTER_FENCE": outer,
              "PROBE_LOADED": loaded, "PROBE_REGISTERED": registered, "PROBE_ERROR": probe_error}

    def generate(parts: dict[str, str]) -> str:
        text = template
        for name, value in parts.items():
            marker = f"/* @{name}@ */"
            assert text.count(marker) == 1, name
            text = text.replace(marker, value)
        assert not re.search(r"/\* @[A-Z_]+@ \*/", text)
        return text

    generated = generate(pieces)
    fixture = args.out / "fixture.generated.c"
    fixture.write_text(generated)
    compiler = subprocess.run([args.cc, "--version"], text=True, capture_output=True, check=True)
    invocation = {"cc": str(Path(args.cc).resolve()), "version": compiler.stdout, "host": {"system": platform.system(), "machine": platform.machine()}, "candidate": str(args.candidate.resolve()), "source_receipt_sha256": sha(source_receipt.read_bytes())}
    save(args.out / "invocation.json", invocation)
    flags = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-pthread"]

    def compile_run(name: str, source: Path, options: list[str]) -> dict:
        executable = args.out / (name + ".bin")
        argv = [args.cc, *flags, *options, str(source), "-o", str(executable)]
        result = subprocess.run(argv, text=True, capture_output=True)
        save(args.out / (name + "-compile.json"), {"argv": argv, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        assert result.returncode == 0, f"Compile failed: {name}"
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=" + ("0" if platform.system() == "Darwin" else "1"), UBSAN_OPTIONS="halt_on_error=1")
        run = subprocess.run([str(executable.resolve())], text=True, capture_output=True, env=env, timeout=30)
        report = {"returncode": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
        save(args.out / (name + "-run.json"), report)
        return report

    runs = {}
    for mode, options in (("optimized", ["-O2"]), ("sanitized", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"])):
        result = compile_run(mode, fixture, options)
        assert result["returncode"] == 0 and not result["stderr"], f"Runtime failed: {mode}"
        match = re.fullmatch(r"PASS: (\d+) actual-source preparation/lifetime cases; no devices or hardware emulation\n", result["stdout"])
        assert match, "Missing scoped PASS"
        runs[mode] = {"cases": int(match[1]), "stdout_sha256": sha(result["stdout"].encode())}
    assert runs["optimized"] == runs["sanitized"]
    mutants = [
        ("attempt-not-recorded", "FLOW", "p->cached.attempted = true;", "p->cached.attempted = false;", "repeat_begin"),
        ("post-gart-invalidation-removed", "FLOW", "p->cached.attempted || event != PS4_LIVE_EVENT_GART_ENABLE", "event != PS4_LIVE_EVENT_GART_ENABLE", "invalidate_prepared"),
        ("generation-check-removed", "FLOW", "p->cached.proof_generation != p->cached.observed_generation ||", "false ||", "publish_reject_generation"),
        ("uvd-owner-accepted", "FLOW", "s->ready_mode || !s->passed", "false || !s->passed", "publish_reject_ready_mode"),
        ("early-fence-check-removed", "FLOW", "!adev->ps4_canary_fences_initialized ||", "false ||", "publish_reject_fence_init"),
        ("owner-not-detached", "FLOW", "p->owner = NULL;", "/* injected missing detach */;", "owner_detached"),
        ("status-hardware-side-effect", "FLOW", "/* Pure cached-data query:", "++test_forbidden_gpu_reads;\n\t/* Pure cached-data query:", "cached_only"),
        ("open-reference-missing", "DEBUGFS", "drm_dev_get(adev_to_drm(adev));", "(void)0;", "open_failure_ref"),
        ("outer-fence-double-init", "OUTER_FENCE", "if (!adev->ps4_canary_fences_initialized)", "if (true)", "fence_once"),
        ("kernel-attempt-latch-removed", "FLOW", "atomic_cmpxchg(&ps4_live_prepare_attempted, 0, 1)", "false", "rebind_no_repeat"),
        ("successful-load-not-recorded", "PROBE_LOADED", "ps4_prepare_loaded = true;", "ps4_prepare_loaded = false;", "probe_unwind_order"),
        ("registration-not-recorded", "PROBE_REGISTERED", "ps4_prepare_registered = true;", "ps4_prepare_registered = false;", "probe_unplug_before_unload"),
        ("load-failure-unloaded-twice", "PROBE_ERROR", "ps4_prepare_loaded && amdgpu_ps4_live_prepare_enabled(adev)", "((void)ps4_prepare_loaded, amdgpu_ps4_live_prepare_enabled(adev))", "probe_unwind_order"),
    ]
    sensitivity = []
    for name, part, old, new, witness in mutants:
        assert pieces[part].count(old) == 1, f"Mutation anchor changed: {name}"
        mutated = dict(pieces); mutated[part] = mutated[part].replace(old, new, 1)
        path = args.out / (name + ".c"); path.write_text(generate(mutated))
        result = compile_run(name, path, ["-O2"])
        assert result["returncode"] == 86 and result["stderr"] == "TEST_FAIL:" + witness + "\n", f"Wrong mutation witness: {name}"
        sensitivity.append({"name": name, "witness": witness, "returncode": result["returncode"], "source_sha256": sha(path.read_bytes())})
    output = {"schema": 1, "status": "PASS_EXTRACTED_PREPARATION_SOFTWARE_NO_DEVICES", "toolchain": invocation, "source_receipt_sha256": sha(source_receipt.read_bytes()), "patch_sha256": receipt["patch"]["sha256"], "source_sha256": receipt["candidate_files"], "generated_fixture_sha256": sha(fixture.read_bytes()), "checker_sha256": sha(Path(__file__).read_bytes()), "fixture_template_sha256": sha((HERE / "fixture.c").read_bytes()), "header_shim": "Only linux/spinlock.h and linux/types.h includes replaced by host types; actual header definitions and extracted bodies unchanged.", "runs": runs, "sensitivity": sensitivity, "kernel_built": False, "hardware_actions": False, "hardware_acceptance": False, "limits": ["Host pthread mutexes are not kernel interrupt-spinlock emulation.", "Tests simulate completed SDMA ownership; no SDMA/UVD execution or hardware quiescence proof.", "Cached-reader and destructor tests do not execute full AMDGPU shutdown, reset, initialization or debugfs infrastructure.", "Two actual fence call sites and read-only/invalidation/cleanup wiring receive scoped source checks; whole-kernel integration still needs compilation and review."]}
    save(args.out / "receipt.json", output)
    print(json.dumps({"status": output["status"], "cases_per_mode": runs["optimized"]["cases"], "mutants": len(sensitivity), "receipt": str(args.out / "receipt.json")}, indent=2))


if __name__ == "__main__":
    main()
