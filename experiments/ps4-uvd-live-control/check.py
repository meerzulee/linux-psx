#!/usr/bin/env python3
"""Validate a frozen live-control helper and run actual-source host fixtures.

No kernel build, module operation or device access. pthread mutexes model
mutual exclusion only; they do not establish Linux IRQ or memory-model
behaviour. Existing output directories are never reused.
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
import sys

HERE = Path(__file__).resolve().parent
HEADER = "amdgpu_ps4_uvd_live_control.h"
SOURCE = "amdgpu_ps4_uvd_live_control.c"
TOOLS = ("check.py", "fixture.c", "freeze.py")
# Frozen native02 preparation attempt06: receipt and the header it generated.
PREPARE_RECEIPT_PIN = "2b1a4f8b7aceec62f8c42b3b5f39430bfaf4b10b97e4a8929642178390c835fa"
PREPARE_HEADER_PIN = "3d18c0f0aa2e8efefad5ca04faa58df7d85bc329209dc5704de7e9bb7024d612"
PREPARE_HEADER_REL = Path("candidate/drivers/gpu/drm/amd/amdgpu/amdgpu_ps4_live_prepare.h")
INCLUDES = {
    HEADER: ["<linux/bits.h>", "<linux/spinlock.h>", "<linux/types.h>",
             '"amdgpu_ps4_live_prepare.h"'],
    SOURCE: ["<linux/atomic.h>", "<linux/errno.h>", "<linux/limits.h>",
             "<linux/spinlock.h>", '"amdgpu_ps4_uvd_live_control.h"'],
}
EXPORTED = ("amdgpu_ps4_uvd_live_init", "amdgpu_ps4_uvd_live_claim",
            "amdgpu_ps4_uvd_live_start", "amdgpu_ps4_uvd_live_finish",
            "amdgpu_ps4_uvd_live_invalidate", "amdgpu_ps4_uvd_live_snapshot",
            "amdgpu_ps4_uvd_live_errno")
FORBIDDEN_CALLS = re.compile(
    r"\b(?:[RW]REG\w*|readl\w*|writel\w*|ioread\w*|iowrite\w*|ktime_\w*|jiffies\w*|"
    r"dev_\w*|pr_\w*|printk|DRM_\w*|drm_\w*|kmalloc\w*|kzalloc\w*|kcalloc\w*|kfree\w*|"
    r"vmalloc\w*|vfree|devm_\w*|schedule\w*|queue_\w*|flush_\w*|cancel_\w*|mod_delayed_\w*|"
    r"msleep\w*|usleep\w*|udelay|mdelay|ndelay|wait_\w*|complete\w*|mutex_\w*|down_\w*|"
    r"up_\w*|up|rcu_\w*|srcu_\w*|request_firmware\w*|release_firmware|amdgpu_bo_\w*|"
    r"amdgpu_ring_\w*|amdgpu_ib_\w*|amdgpu_fence_\w*|dma_\w*|ttm_\w*|"
    r"amdgpu_ps4_uvd_irq_\w*|amdgpu_ps4_live_prepare_\w*|amdgpu_ps4_uvd_ready_\w*|"
    r"amdgpu_device_\w*|amdgpu_gmc_\w*|amdgpu_vm_\w*)\s*\(")
FORBIDDEN_TOKENS = re.compile(
    r"\b(?:struct\s+amdgpu_device|struct\s+amdgpu_bo|struct\s+ps4_canary_state|"
    r"struct\s+dma_fence|struct\s+firmware|struct\s+work_struct|struct\s+delayed_work|"
    r"module_param\w*|MODULE_PARM_DESC|EXPORT_SYMBOL\w*|ps4_uvd_irq\w*|adev)\b")
CERTIFYING_NAMES = re.compile(r"\b\w*(?:quiesc|veto|drain|may_free|can_free|safe_to|rearm)\w*\b",
                              re.I)
FUNCTION_POINTER = re.compile(r"\(\s*\*\s*\w*\s*\)\s*\(")
MODES = {
    "optimized": ["-O2"],
    "sanitized": ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                  "-fno-sanitize-recover=all"],
    "thread": ["-O1", "-g", "-fsanitize=thread"],
    "coverage": ["-O0", "-fprofile-instr-generate", "-fcoverage-mapping"],
}
FLAGS = ["-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-pthread"]
STRICT = ["-std=gnu11", "-fsyntax-only", "-Wall", "-Wextra", "-Werror",
          "-Wdeclaration-after-statement", "-Wmissing-prototypes", "-Wstrict-prototypes",
          "-Wshadow", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-parameter"]
SIGNATURES = {
    "claim": "amdgpu_ps4_uvd_live_claim(struct amdgpu_ps4_uvd_live *live,",
    "start": "amdgpu_ps4_uvd_live_start(struct amdgpu_ps4_uvd_live *live,",
    "finish": "amdgpu_ps4_uvd_live_finish(struct amdgpu_ps4_uvd_live *live,",
    "invalidate": "void amdgpu_ps4_uvd_live_invalidate(struct amdgpu_ps4_uvd_live *live,",
    "check": "ps4_uvd_live_check(const struct ps4_uvd_live_observation *obs)\n{",
    "enter": "ps4_uvd_live_enter(struct ps4_uvd_live_snapshot *s,",
    "set_end": "static void ps4_uvd_live_set_end(struct ps4_uvd_live_snapshot *s,",
}
# (label, function, exact old text, injected text, expected first witness)
MUTANTS = [
    ("claim-ignores-used", "claim", "else if (s->used)", "else if (false)", "claim_used_once"),
    ("boot-latch-dropped", "claim", "atomic_cmpxchg(&ps4_uvd_live_boot_claimed, 0, 1)", "0",
     "boot_once"),
    ("claim-ignores-invalidation", "claim", "else if (s->invalidated)", "else if (false)",
     "invalidated_blocks_claim"),
    ("generation-unchecked", "check", "p->observed_generation != p->proof_generation)",
     "false)", "stale_generation_refused"),
    ("generation-zero-accepted", "check", "!p->proof_generation || ", "",
     "generation_nonzero"),
    ("generation-max-accepted", "check", "p->proof_generation == U64_MAX ||\n\t    ", "",
     "generation_not_saturated"),
    ("reset-proof-unpinned", "check", "p->proof_reset_counter || ", "", "reset_pinned_zero"),
    ("start-after-terminal", "start", "else if (s->end != PS4_UVD_LIVE_END_NONE)",
     "else if (false)", "terminal_claim_never_runs"),
    ("refusal-not-final", "enter",
     "\t\tps4_uvd_live_set_end(s, PS4_UVD_LIVE_END_REFUSED, verdict,",
     "\t\tif (false) ps4_uvd_live_set_end(s, PS4_UVD_LIVE_END_REFUSED, verdict,",
     "refused_final"),
    ("touch-not-set", "enter", "\ts->touch_entered = true;\n", "", "touch_before_entry"),
    ("touch-cleared-by-invalidation", "invalidate", "\ts->invalidated = true;\n",
     "\ts->invalidated = true;\n\ts->touch_entered = false;\n", "touch_sticky"),
    ("used-cleared-by-finish", "finish", "\t\ts->phase = PS4_UVD_LIVE_FINISHED;\n",
     "\t\ts->used = false;\n\t\ts->phase = PS4_UVD_LIVE_FINISHED;\n", "used_sticky"),
    ("terminal-overwritten", "set_end",
     "\tif (s->end != PS4_UVD_LIVE_END_NONE)\n\t\treturn;\n", "", "first_terminal_stable"),
    ("start-ignores-ticket", "start", "else if (ticket != s->ticket)", "else if (false)",
     "ticket_owner"),
]
PASS_LINE = re.compile(r"PASS: (\d+) actual-source live-control cases; synthetic locks only\n")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def save(path: Path, value: object) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def function(source: str, signature: str) -> str:
    require(source.count(signature) == 1, "Function boundary changed: " + signature)
    start = source.index(signature)
    start = source.rindex("\n\n", 0, start) + 2
    end = source.index("\n}\n", start) + 3
    return source[start:end]


def static_checks(header: str, source: str) -> dict:
    report = {}
    for name, text in ((HEADER, header), (SOURCE, source)):
        found = re.findall(r"^#include (\S+)$", text, re.M)
        require(found == INCLUDES[name], f"Unexpected includes in {name}: {found}")
        code = strip_comments(text)
        require(not FORBIDDEN_CALLS.search(code),
                f"Forbidden side-effect call in {name}: {FORBIDDEN_CALLS.search(code)}")
        require(not FORBIDDEN_TOKENS.search(code),
                f"Forbidden ownership token in {name}: {FORBIDDEN_TOKENS.search(code)}")
        require(not CERTIFYING_NAMES.search(code),
                f"Certifying identifier in {name}: {CERTIFYING_NAMES.search(code)}")
        require(not FUNCTION_POINTER.search(code), f"Function pointer or callback in {name}")
    report["includes"] = INCLUDES
    code = strip_comments(source)
    globals_ = re.findall(r"^static [^\n]*;$", code, re.M)
    require(globals_ == ["static atomic_t ps4_uvd_live_boot_claimed = ATOMIC_INIT(0);"],
            f"Unexpected file-scope state: {globals_}")
    require(not re.findall(r"^(?!static\b)[A-Za-z_][^\n]*;$", code, re.M),
            "Unexpected non-static file-scope declaration")
    definitions = re.findall(r"^(?:void|int|enum ps4_uvd_live_verdict)\n?(?:\s*)"
                             r"(amdgpu_ps4_uvd_live_\w+)\(", code, re.M)
    prototypes = re.findall(r"\b(amdgpu_ps4_uvd_live_\w+)\(", strip_comments(header))
    require(sorted(definitions) == sorted(EXPORTED) == sorted(prototypes),
            f"Exported API changed: {definitions} / {prototypes}")
    locked = []
    for body in re.split(r"\n}\n", code):
        locks = body.count("spin_lock_irqsave(")
        unlocks = body.count("spin_unlock_irqrestore(")
        require(locks == unlocks <= 1, "Unbalanced or repeated critical section")
        if locks:
            require(body.count("spin_lock_irqsave(&live->lock, flags);") == 1 and
                    body.count("spin_unlock_irqrestore(&live->lock, flags);") == 1,
                    "Critical section uses a lock other than live->lock")
            section = body[body.index("spin_lock_irqsave("):body.index("spin_unlock_irqrestore(")]
            require("return" not in section, "Return while holding live->lock")
            locked.append(re.search(r"(amdgpu_ps4_uvd_live_\w+)\(", body)[1])
    require(sorted(locked) == sorted(name for name in EXPORTED if name not in
                                     ("amdgpu_ps4_uvd_live_init", "amdgpu_ps4_uvd_live_errno")),
            f"Locked function set changed: {locked}")
    require(source.count("s->touch_entered = true;") == 1 and
            "touch_entered = false" not in code, "Touch witness writes changed")
    require(code.count("s->used = true;") == 1 and "used = false" not in code,
            "Used flag writes changed")
    require(code.count("atomic_cmpxchg(&ps4_uvd_live_boot_claimed, 0, 1)") == 1 and
            "ps4_uvd_live_boot_claimed, 0)" not in code and "atomic_set" not in code,
            "Boot latch writes changed")
    report.update({"forbidden_calls": "none", "callbacks": "none", "certifying_names": "none",
                   "file_scope_state": globals_, "exported": sorted(EXPORTED),
                   "single_leaf_critical_section": sorted(locked),
                   "sticky_writes": {"touch_entered": "set once in start entry, never cleared",
                                     "used": "set once by claim, never cleared",
                                     "boot_latch": "one cmpxchg 0->1, never cleared"}})
    return report


def tool(name: str) -> list[str] | None:
    if platform.system() == "Darwin":
        found = subprocess.run(["xcrun", "--find", name], capture_output=True, text=True)
        if found.returncode == 0:
            return ["xcrun", name]
    path = shutil.which(name)
    return [path] if path else None


def coverage(out: Path, binary: Path, generated: Path, first: int, last: int) -> dict:
    profdata, cov = tool("llvm-profdata"), tool("llvm-cov")
    require(profdata is not None and cov is not None, "LLVM coverage tools unavailable")
    raw, data = out / "coverage.profraw", out / "coverage.profdata"
    steps = [
        [*profdata, "merge", "-sparse", str(raw), "-o", str(data)],
        [*cov, "export", "-format=text", f"-instr-profile={data}", str(binary)],
        [*cov, "show", f"-instr-profile={data}", "-show-branches=count", str(binary),
         str(generated)],
    ]
    outputs = []
    for index, argv in enumerate(steps):
        result = subprocess.run(argv, capture_output=True, text=True)
        save(out / f"coverage-step{index + 1}.json",
             {"argv": argv, "returncode": result.returncode, "stderr": result.stderr,
              "stdout_sha256": sha(result.stdout.encode())})
        require(result.returncode == 0, "Coverage step failed: " + argv[1])
        outputs.append(result.stdout)
    (out / "coverage-show.txt").write_text(outputs[2])
    exported = json.loads(outputs[1])["data"][0]
    path = str(generated.resolve())
    lines_total = lines_hit = 0
    uncovered_lines = []
    for row in outputs[2].splitlines():
        match = re.match(r"^\s*(\d+)\|\s*([0-9.]+[kMGTE]?)\|", row)
        if match and first <= int(match[1]) <= last:
            lines_total += 1
            if match[2] == "0":
                uncovered_lines.append(int(match[1]))
            else:
                lines_hit += 1
    regions_total = 0
    uncovered_regions = []
    for entry in exported["functions"]:
        names = entry["filenames"]
        for region in entry["regions"]:
            line, count, file_id, kind = region[0], region[4], region[5], region[7]
            if kind == 0 and Path(names[file_id]).resolve() == Path(path) and \
                    first <= line <= last:
                regions_total += 1
                if count == 0:
                    uncovered_regions.append(region[:4])
    files = [item for item in exported["files"] if Path(item["filename"]).resolve() == Path(path)]
    require(len(files) == 1, "Generated fixture missing from coverage export")
    branches_total = 0
    uncovered_branches = []
    for branch in files[0].get("branches", []):
        if first <= branch[0] <= last:
            branches_total += 2
            for outcome, count in (("true", branch[4]), ("false", branch[5])):
                if count == 0:
                    uncovered_branches.append([branch[0], branch[1], outcome])
    require(lines_total > 0 and regions_total > 0 and branches_total > 0,
            "Coverage parse found no helper lines, regions or branches")
    return {
        "scope": f"helper source lines {first}-{last} of the generated fixture",
        "lines": {"covered": lines_hit, "total": lines_total, "uncovered": uncovered_lines},
        "regions": {"covered": regions_total - len(uncovered_regions), "total": regions_total,
                    "uncovered": uncovered_regions},
        "branch_outcomes": {"covered": branches_total - len(uncovered_branches),
                            "total": branches_total, "uncovered": uncovered_branches},
        "show_sha256": sha(outputs[2].encode()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Frozen attempt containing SOURCE-RECEIPT.json")
    parser.add_argument("--prepare", type=Path, required=True,
                        help="Frozen live-prepare source attempt-06 directory")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cc", default=shutil.which("clang") or shutil.which("cc"))
    parser.add_argument("--modes", default="optimized,sanitized",
                        help="Comma list from: " + ",".join(MODES))
    parser.add_argument("--checkpatch", type=Path,
                        help="Optional kernel scripts/checkpatch.pl for a read-only style pass")
    args = parser.parse_args()
    modes = [mode for mode in args.modes.split(",") if mode]
    require(modes and set(modes) <= set(MODES) and "optimized" in modes, "Bad --modes")
    require(args.cc is not None, "Native host C compiler required")
    require(not args.out.exists(), "Preserve prior output; choose a new directory")
    args.out.mkdir(parents=True, mode=0o700)
    inputs = args.out / "inputs"
    inputs.mkdir()
    for name in TOOLS:
        (inputs / name).write_bytes((HERE / name).read_bytes())

    source_receipt = args.source / "SOURCE-RECEIPT.json"
    receipt = json.loads(source_receipt.read_text())
    require(sorted(receipt["helper_files"]) == sorted((HEADER, SOURCE)), "Helper file set changed")
    helper = {}
    for name, expected in receipt["helper_files"].items():
        raw = (args.source / "helper" / name).read_bytes()
        require(sha(raw) == expected, "Frozen helper mismatch: " + name)
        helper[name] = raw.decode()
    for name, expected in receipt["tool_files"].items():
        require(sha((args.source / "tools" / name).read_bytes()) == expected,
                "Frozen tool copy mismatch: " + name)
    prepare_receipt = args.prepare / "SOURCE-RECEIPT.json"
    require(sha(prepare_receipt.read_bytes()) == PREPARE_RECEIPT_PIN, "Preparation receipt changed")
    require(json.loads(prepare_receipt.read_text())["generator_inputs"]["live_prepare.h"] ==
            PREPARE_HEADER_PIN, "Preparation header pin changed")
    prepare_raw = (args.prepare / PREPARE_HEADER_REL).read_bytes()
    require(sha(prepare_raw) == PREPARE_HEADER_PIN, "Preparation header changed")
    require(receipt["prepare_header_sha256"] == PREPARE_HEADER_PIN, "Receipt binds another header")
    statics = static_checks(helper[HEADER], helper[SOURCE])
    style = None
    if args.checkpatch:
        script = args.checkpatch.resolve()
        argv = ["perl", str(script), "--no-tree", "--strict", "--show-types", "-f",
                str((args.source / "helper" / SOURCE).resolve()),
                str((args.source / "helper" / HEADER).resolve())]
        result = subprocess.run(argv, capture_output=True, text=True)
        save(args.out / "checkpatch.json", {"argv": argv, "returncode": result.returncode,
             "stdout": result.stdout, "stderr": result.stderr})
        totals = re.findall(r"^total: (\d+) errors, (\d+) warnings, (\d+) checks", result.stdout,
                            re.M)
        require(result.returncode == 0 and totals == [("0", "0", "0")] * 2,
                "checkpatch reported findings")
        style = {"script": str(script), "script_sha256": sha(script.read_bytes()),
                 "flags": argv[2:6], "per_file": "0 errors, 0 warnings, 0 checks"}

    template = (HERE / "fixture.c").read_text()
    pieces = {
        "PREPARE_HEADER": re.sub(r"^#include .*\n", "", prepare_raw.decode(), flags=re.M),
        "HELPER_HEADER": re.sub(r"^#include .*\n", "", helper[HEADER], flags=re.M),
        "HELPER_SOURCE": re.sub(r"^#include .*\n", "", helper[SOURCE], flags=re.M),
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
    first = generated[:generated.index(pieces["HELPER_SOURCE"])].count("\n") + 1
    last = first + pieces["HELPER_SOURCE"].count("\n") - 1
    generated_path = args.out / "fixture.generated.c"
    generated_path.write_text(generated)
    cc = str(Path(args.cc).resolve())
    compiler = subprocess.run([cc, "--version"], text=True, capture_output=True, check=True)
    save(args.out / "invocation.json", {
        "compiler": cc, "compiler_version": compiler.stdout, "modes": modes,
        "system": platform.system(), "machine": platform.machine(), "python": sys.version,
        "source": str(args.source.resolve()), "prepare": str(args.prepare.resolve()),
        "source_receipt_sha256": sha(source_receipt.read_bytes()),
    })

    # Kernel-like warning set over the helper alone, without fixture tests.
    helper_only = generated[:generated.index("/* FIXTURE_TESTS_BEGIN */")]
    strict_path = args.out / "helper-only.c"
    strict_path.write_text(helper_only)
    argv = [cc, *STRICT, str(strict_path)]
    result = subprocess.run(argv, capture_output=True, text=True)
    save(args.out / "helper-only-compile.json", {"argv": argv, "returncode": result.returncode,
         "stdout": result.stdout, "stderr": result.stderr})
    require(result.returncode == 0 and not result.stderr, "Strict helper-only compile failed")

    def compile_run(label: str, text: str, options: list[str], env: dict | None = None) -> dict:
        path = args.out / (label + ".c")
        path.write_text(text)
        binary = args.out / (label + ".bin")
        argv = [cc, *FLAGS, *options, str(path), "-o", str(binary)]
        result = subprocess.run(argv, capture_output=True, text=True)
        save(args.out / (label + "-compile.json"), {"argv": argv, "returncode": result.returncode,
             "stdout": result.stdout, "stderr": result.stderr})
        require(result.returncode == 0, "Compile failed: " + label)
        environment = dict(os.environ, ASAN_OPTIONS="detect_leaks=" +
                           ("0" if platform.system() == "Darwin" else "1"),
                           UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1",
                           TSAN_OPTIONS="halt_on_error=1", **(env or {}))
        argv = [str(binary.resolve())]
        result = subprocess.run(argv, capture_output=True, text=True, env=environment, timeout=600)
        report = {"argv": argv, "env": {key: environment[key] for key in
                                        ("ASAN_OPTIONS", "UBSAN_OPTIONS", "TSAN_OPTIONS",
                                         *(env or {}))},
                  "returncode": result.returncode, "stdout": result.stdout,
                  "stderr": result.stderr}
        save(args.out / (label + "-run.json"), report)
        report["binary"] = binary
        return report

    runs = {}
    covered = None
    for mode in modes:
        races = args.out / f"{mode}-races.txt"
        extra = {"LIVE_CONTROL_RACE_REPORT": str(races)}
        if mode == "coverage":
            extra["LLVM_PROFILE_FILE"] = str(args.out / "coverage.profraw")
        run = compile_run(mode, generated, MODES[mode], extra)
        require(run["returncode"] == 0 and not run["stderr"], "Runtime failed: " + mode)
        match = PASS_LINE.fullmatch(run["stdout"])
        require(match is not None, "Missing scoped host result: " + mode)
        outcomes = dict(line.split("=") for line in races.read_text().split())
        runs[mode] = {"cases": int(match[1]), "stdout_sha256": sha(run["stdout"].encode()),
                      "race_outcomes_informational": {k: int(v) for k, v in outcomes.items()}}
        if mode == "coverage":
            covered = coverage(args.out, run["binary"], args.out / "coverage.c", first, last)
            require(not covered["lines"]["uncovered"] and not covered["regions"]["uncovered"] and
                    not covered["branch_outcomes"]["uncovered"],
                    "Helper coverage incomplete: " + json.dumps(covered))
    require(len({json.dumps({k: v for k, v in run.items() if k != "race_outcomes_informational"})
                 for run in runs.values()}) == 1, "Host mode results differ")

    sensitivity = {}
    for label, part, old, new, witness in MUTANTS:
        changed = dict(pieces)
        body = function(changed["HELPER_SOURCE"], SIGNATURES[part])
        require(body.count(old) == 1, "Mutant anchor changed: " + label)
        changed["HELPER_SOURCE"] = changed["HELPER_SOURCE"].replace(body, body.replace(old, new, 1), 1)
        require(changed["HELPER_SOURCE"] != pieces["HELPER_SOURCE"], "Mutant made no change: " + label)
        result = compile_run("sensitivity-" + label, render(changed), ["-O2"])
        require(result["returncode"] == 2 and
                re.fullmatch(r"FAIL:" + re.escape(witness) + r":\d+\n", result["stderr"]) is not None,
                f"Mutant did not reach expected witness: {label}: {result['stderr']!r}")
        sensitivity[label] = {"detected": True, "witness": witness}

    result = {
        "schema": 1, "attempt": receipt["attempt"],
        "status": "PASS_ACTUAL_SOURCE_HOST_CONTROL_STATE_ONLY",
        "source_receipt_sha256": sha(source_receipt.read_bytes()),
        "helper_files": receipt["helper_files"],
        "prepare_header_sha256": PREPARE_HEADER_PIN,
        "prepare_receipt_sha256": PREPARE_RECEIPT_PIN,
        "tool_sha256": {name: sha((HERE / name).read_bytes()) for name in TOOLS},
        "generated_fixture_sha256": sha(generated.encode()),
        "helper_only_strict_compile": {"flags": STRICT, "passed": True},
        "static_checks": statics, "checkpatch": style, "runs": runs, "coverage": covered,
        "sensitivity": sensitivity, "wired": False, "kernel_built": False,
        "hardware_acceptance": False,
        "limits": [
            "Synthetic pthread locks and C11 atomics only; no Linux IRQ, preemption or kernel "
            "memory-model semantics are established.",
            "Inputs are synthetic caller observations; nothing tests the real preparation owner, "
            "reset paths, drm_dev_enter, the reset domain, firmware, workers or teardown.",
            "No DMA quiescence, BO lifetime, reset/removal veto, IRQ observer arm or hardware "
            "behaviour is claimed; the helper has no caller.",
            "Race outcome counts are informational and scheduler dependent.",
        ],
    }
    save(args.out / "receipt.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"CHECK FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
