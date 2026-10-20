#!/usr/bin/env python3
"""Validate the isolated patch and run actual-source software fixtures.

No kernel build or device access. All GMC register operations in this fixture
are fake functions; pthread mutexes model mutual exclusion, not Linux IRQs.
"""
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

HERE = Path(__file__).resolve().parent
REL = Path("drivers/gpu/drm/amd/amdgpu")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def save(path: Path, value: object) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def function(source: str, signature: str) -> str:
    require(source.count(signature) == 1, "Function boundary changed: " + signature)
    start = source.index(signature)
    end = source.index("\n}\n", start) + 3
    return source[start:end]


def marked(source: str, name: str) -> str:
    begin, end = f"/* {name}_BEGIN */", f"/* {name}_END */"
    require(source.count(begin) == source.count(end) == 1, "Marker changed: " + name)
    return source[source.index(begin):source.index(end) + len(end)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Fresh source attempt containing SOURCE-RECEIPT.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cc", default=shutil.which("clang") or shutil.which("cc"))
    args = parser.parse_args()
    require(args.cc is not None, "Native host C compiler required")
    require(not args.out.exists(), "Preserve prior output; choose a new directory")
    args.out.mkdir(parents=True, mode=0o700)
    inputs = args.out / "inputs"
    inputs.mkdir()
    for name in ("check.py", "fixture.c", "build_candidate.py", "fault.c.inc", "fault.h.inc"):
        (inputs / name).write_bytes((HERE / name).read_bytes())
    source_receipt = args.source / "SOURCE-RECEIPT.json"
    receipt = json.loads(source_receipt.read_text())
    spec = importlib.util.spec_from_file_location("fault_builder", HERE / "build_candidate.py")
    require(spec is not None and spec.loader is not None, "Builder unavailable")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    require(receipt["baseline_files"] == builder.PINS, "Baseline pin set changed")
    baseline, candidate = {}, {}
    for name, expected in builder.PINS.items():
        raw = (args.source / "baseline" / REL / name).read_bytes()
        require(sha(raw) == expected, "Frozen baseline mismatch: " + name)
        baseline[name] = raw.decode()
        raw = (args.source / "candidate" / REL / name).read_bytes()
        require(sha(raw) == receipt["candidate_files"][name], "Candidate mismatch: " + name)
        candidate[name] = raw.decode()
    require(candidate == builder.transform(baseline), "Candidate not reproduced by current generator")
    require(set(candidate) == set(receipt["candidate_files"]), "Unexpected candidate files")
    for name, expected in receipt["generator_files"].items():
        require(sha((HERE / name).read_bytes()) == expected, "Generator changed: " + name)
    patch = args.source / receipt["patch"]["file"]
    require(sha(patch.read_bytes()) == receipt["patch"]["sha256"], "Patch changed")
    require(patch.read_text().count("diff --git ") == 3, "Patch scope changed")
    replay = args.out / "replay"
    for name, value in baseline.items():
        path = replay / REL / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    for mode in ("check", "apply"):
        argv = ["git", "apply"] + (["--check"] if mode == "check" else []) + [str(patch.resolve())]
        run = subprocess.run(argv, cwd=replay, text=True, capture_output=True)
        save(args.out / f"patch-{mode}.json", {"argv": argv, "returncode": run.returncode,
                                             "stdout": run.stdout, "stderr": run.stderr})
        require(run.returncode == 0, "Patch replay failed: " + mode)
    for name, value in candidate.items():
        require((replay / REL / name).read_text() == value, "Patch replay differs: " + name)

    observer = candidate["amdgpu_ps4_uvd_irq.c"]
    header = candidate["amdgpu_ps4_uvd_irq.h"]
    gmc = candidate["gmc_v7_0.c"]
    # Literal subtraction proves existing source preserved, not just call census.
    require(observer.replace('#include "gmc/gmc_7_1_sh_mask.h"\n', "", 1)
            .replace(builder.INIT, "", 1)
            .replace((HERE / "fault.c.inc").read_text() + "\n", "", 1)
            .replace(builder.NEW_COMMENT, builder.OLD_COMMENT, 1)
            == baseline["amdgpu_ps4_uvd_irq.c"], "Legacy observer bytes changed")
    require(header.replace("#include <linux/spinlock.h>\n", "", 1)
            .replace((HERE / "fault.h.inc").read_text() + "\n", "", 1)
            .replace(builder.FIELDS, "", 1).replace(builder.API, "", 1)
            == baseline["amdgpu_ps4_uvd_irq.h"], "Legacy header bytes changed")
    require(gmc.replace('#include "amdgpu_ps4_uvd_irq.h"\n', "", 1)
            .replace(builder.HOOK, "", 1) == baseline["gmc_v7_0.c"],
            "Existing GMC source changed")
    require(sha(gmc.encode()) ==
            "dc52a218b6999a2914707e37432ebd2e02d889e917488d6c4b732f518c49a33a",
            "Additive GMC hook differs from preserved revision1")
    flow = marked(observer, "GMC_FAULT_FLOW")
    require(not re.search(r"\b(?:[RW]REG\w*|readl\w*|writel\w*|ktime_\w*|dev_\w*|"
                          r"kmalloc|kzalloc|schedule\w*|queue_work)\s*\(", flow),
            "New recorder/snapshot has an unexpected side-effect call")
    require(observer.count("ktime_get_mono_fast_ns(") ==
            baseline["amdgpu_ps4_uvd_irq.c"].count("ktime_get_mono_fast_ns("),
            "Additional timekeeping read")
    gmc_handler = function(gmc, "static int gmc_v7_0_process_interrupt(")
    require(gmc_handler.index("WREG32_P(") <
            gmc_handler.index("if (!addr && !status)") <
            gmc_handler.index("amdgpu_ps4_uvd_irq_record_fault(") <
            gmc_handler.index("amdgpu_vm_update_fault_cache(") <
            gmc_handler.index("vmid = REG_GET_FIELD("), "Handler ordering changed")
    masks_path = args.source / "reference" / receipt["reference_header"]["path"]
    masks_raw = masks_path.read_bytes()
    require(sha(masks_raw) == receipt["reference_header"]["sha256"] == builder.MASK_PIN,
            "Mask header changed")
    masks = []
    for field in ("VMID", "PROTECTIONS", "MEMORY_CLIENT_ID"):
        for suffix in ("_MASK", "__SHIFT"):
            name = f"VM_CONTEXT1_PROTECTION_FAULT_STATUS__{field}{suffix}"
            rows = re.findall(r"^#define " + name + r" .+$", masks_raw.decode(), re.M)
            require(len(rows) == 1, "Missing pinned public mask: " + name)
            masks.extend(rows)
    template = (HERE / "fixture.c").read_text()
    pieces = {
        "MASKS": "\n".join(masks),
        "HEADER": re.sub(r"^#include .*\n", "", header, flags=re.M),
        "OBSERVER": re.sub(r"^#include .*\n", "", observer, flags=re.M),
        "GMC_HANDLER": gmc_handler,
    }

    def render(parts: dict[str, str]) -> str:
        text = template
        for name, value in parts.items():
            token = f"/* @{name}@ */"
            require(text.count(token) == 1, "Fixture extraction token changed: " + name)
            text = text.replace(token, value)
        require(not re.search(r"/\* @[A-Z_]+@ \*/", text), "Unexpanded fixture token")
        return text

    generated = render(pieces)
    generated_path = args.out / "fixture.generated.c"
    generated_path.write_text(generated)
    cc = str(Path(args.cc).resolve())
    compiler = subprocess.run([cc, "--version"], text=True, capture_output=True, check=True)
    save(args.out / "invocation.json", {
        "compiler": cc, "compiler_version": compiler.stdout,
        "system": platform.system(), "machine": platform.machine(),
        "python": sys.version, "source": str(args.source.resolve()),
        "source_receipt_sha256": sha(source_receipt.read_bytes()),
    })
    flags = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-pthread"]

    def compile_run(label: str, text: str, options: list[str]) -> dict:
        path = args.out / (label + ".c")
        path.write_text(text)
        binary = args.out / (label + ".bin")
        argv = [cc, *flags, *options, str(path), "-o", str(binary)]
        result = subprocess.run(argv, capture_output=True, text=True)
        save(args.out / (label + "-compile.json"), {"argv": argv, "returncode": result.returncode,
             "stdout": result.stdout, "stderr": result.stderr})
        require(result.returncode == 0, "Compile failed: " + label)
        environment = dict(os.environ, ASAN_OPTIONS="detect_leaks=" +
                           ("0" if platform.system() == "Darwin" else "1"),
                           UBSAN_OPTIONS="halt_on_error=1")
        result = subprocess.run([str(binary.resolve())], capture_output=True,
                                text=True, env=environment, timeout=30)
        report = {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        save(args.out / (label + "-run.json"), report)
        return report

    runs = {}
    for label, options in [
        ("optimized", ["-O2"]),
        ("sanitized", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]),
    ]:
        run = compile_run(label, generated, options)
        require(run["returncode"] == 0 and not run["stderr"], "Runtime failed: " + label)
        match = re.fullmatch(r"PASS: (\d+) actual-source captured-fault history cases; "
                            r"synthetic locks/registers only\n", run["stdout"])
        require(match is not None, "Missing scoped host result")
        runs[label] = {"cases": int(match[1]), "stdout_sha256": sha(run["stdout"].encode())}
    require(runs["optimized"] == runs["sanitized"], "Host mode result differs")

    mutants = [
        ("discard-other-vmids", "OBSERVER",
         "if (!smp_load_acquire(&obs->active))\n\t\treturn;\n\n\t/* Preserve",
         "if (!smp_load_acquire(&obs->active) ||\n"
         "\t    REG_GET_FIELD(status, VM_CONTEXT1_PROTECTION_FAULT_STATUS, VMID) != 4)\n"
         "\t\treturn;\n\n\t/* Preserve", "arm_does_not_reset"),
        ("wrap-counter", "OBSERVER", "if (bucket->arrivals != ~0ULL)",
         "if (true)", "no_wrap"),
        ("overwrite-first", "OBSERVER", "if (!bucket->arrivals)",
         "if (true)", "total_first_immutable"),
        ("omit-record", "GMC_HANDLER", "amdgpu_ps4_uvd_irq_record_fault(adev, addr, status, mc_client);",
         "/* injected omitted observation */", "record_before_cache"),
        ("wrong-category", "OBSERVER",
         "REG_GET_FIELD(status, VM_CONTEXT1_PROTECTION_FAULT_STATUS, VMID)]",
         "4]", "vmid_category"),
        ("sentinel-as-vmid15", "OBSERVER", "if (status == ~0U)",
         "if (false)", "sentinel_not_vmid15"),
        ("omit-total", "OBSERVER",
         "ps4_uvd_fault_bucket_record(&history->total, &record);",
         "/* injected missing total */", "total_category_sum"),
        ("omit-category", "OBSERVER", "ps4_uvd_fault_bucket_record(category, &record);",
         "(void)category; /* injected missing category */", "total_category_sum"),
        ("missing-lock-init", "OBSERVER", "spin_lock_init(&obs->fault_lock);",
         "/* injected missing initialization */", "lock_initialized"),
    ]
    sensitivity = {}
    for label, part, old, new, witness in mutants:
        changed = dict(pieces)
        require(changed[part].count(old) == 1, "Mutant anchor changed: " + label)
        changed[part] = changed[part].replace(old, new, 1)
        result = compile_run("sensitivity-" + label, render(changed), ["-O2"])
        require(result["returncode"] == 2 and
                re.fullmatch(r"FAIL:" + re.escape(witness) + r":\d+\n", result["stderr"]) is not None,
                "Mutant did not reach expected witness: " + label)
        sensitivity[label] = {"detected": True, "witness": witness}
    # A paired removal is necessary: leaving unlock without lock is not a useful mutant.
    changed = dict(pieces)
    snapshot_function = function(changed["OBSERVER"],
                                 "void amdgpu_ps4_uvd_irq_fault_snapshot(")
    unlocked = snapshot_function.replace("\tspin_lock_irqsave(&obs->fault_lock, flags);\n", "")
    unlocked = unlocked.replace("\tspin_unlock_irqrestore(&obs->fault_lock, flags);\n", "")
    unlocked = unlocked.replace("\tunsigned long flags;\n", "")
    require(unlocked != snapshot_function, "Snapshot mutant did not change")
    changed["OBSERVER"] = changed["OBSERVER"].replace(snapshot_function, unlocked, 1)
    result = compile_run("sensitivity-snapshot-unlocked", render(changed), ["-O2"])
    require(result["returncode"] == 2 and
            re.fullmatch(r"FAIL:snapshot_lock:\d+\n", result["stderr"]) is not None,
            "Unlocked snapshot did not reach expected witness")
    sensitivity["snapshot-unlocked"] = {"detected": True, "witness": "snapshot_lock"}
    result = {
        "schema": 1, "revision": 2, "status": "PASS_ACTUAL_SOURCE_SOFTWARE_HISTORY_AND_WIRING",
        "source_receipt_sha256": sha(source_receipt.read_bytes()),
        "patch_sha256": receipt["patch"]["sha256"], "candidate_files": receipt["candidate_files"],
        "reference_header_sha256": sha(masks_raw),
        "tool_sha256": {name: sha((HERE / name).read_bytes()) for name in
                        ("check.py", "fixture.c", "build_candidate.py", "fault.c.inc", "fault.h.inc")},
        "generated_fixture_sha256": sha(generated.encode()), "runs": runs,
        "sensitivity": sensitivity, "legacy_bytes_preserved": True,
        "existing_gmc_bytes_preserved_outside_additions": True,
        "hardware_acceptance": False, "kernel_built": False,
        "history": {"total_buckets": 1, "vmid_buckets": 16,
                    "unclassified_status": "0xffffffff", "consumer_added": False},
        "limits": ["Synthetic host locks/register functions; no Linux IRQ semantics or hardware execution.",
                   "No proof of fault completeness, DMA quiescence, live UVD readiness or decode.",
                   "No real kernel or compiled-kernel audit is performed by this checker."],
    }
    save(args.out / "receipt.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"CHECK FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
