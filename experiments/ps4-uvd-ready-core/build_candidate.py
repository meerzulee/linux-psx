#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exact-pinned, isolated returning readiness core; never builds or installs."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
BASE = Path("lab/uvd-2026-10-20/live-prepare/source/attempt-06")
SOURCE_RECEIPT = "2b1a4f8b7aceec62f8c42b3b5f39430bfaf4b10b97e4a8929642178390c835fa"
PREFIX = Path("drivers/gpu/drm/amd/amdgpu")
READY = "amdgpu_ps4_uvd_ready.c"
HEADER = "amdgpu_ps4_uvd_ready.h"
CHANGED = (READY, HEADER, "amdgpu.h")
PINS = {
    READY: "20ab71d1ca64b20c1f6c4ee258d18abb5c9d50726e518ace0b5c67730b9779ff",
    HEADER: "e7e4ab522e720a210798fa11b77dc6e16c8d25b7648d9675a6c78573e06576ea",
    "amdgpu.h": "4fcd4a430a502fe5e142261ebe8cfdd9eef5feaf1590827b51756ed46b2c8bbd",
}
ENTRY = "__noreturn void amdgpu_ps4_uvd_ready_run("
GUARD = """struct ps4_ready_guard_sample {
\tu32 sampled, failed;
\tu32 rbc, mastint, ib_size, wptr, rptr;
};
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("expected unique exact anchor: " + repr(old[:80]))
    return source.replace(old, new, 1)


def marker_block(source, name):
    match = re.search(r"^[ \t]*/\* " + re.escape(name) +
        r"_BEGIN\b.*?^[ \t]*/\* " + re.escape(name) + r"_END \*/\n",
        source, re.M | re.S)
    if not match:
        raise ValueError("missing block: " + name)
    return match.group()


def transform_ready(baseline):
    prefix, entry = baseline.split(ENTRY)
    signature, body = (ENTRY + entry).split("\n{\n", 1)
    if not body.endswith("\n}\n"):
        raise ValueError("entry must be final source function")
    body = body[:-3]
    admission_start = body.index("\tif (!amdgpu_ps4_uvd_ready_enabled(adev)")
    retained_start = body.index("\tps4_ready_retained_cpu = r1_cpu;")
    early = body[admission_start:retained_start]
    # The old latch short-circuit and all early diagnostic blocks stay literal.
    early = once(early,
        '\t\tps4_ready_park(adev, "GATE_OR_REENTRY", -EPERM);\n',
        '\t\tps4_ready_park(adev, "GATE_OR_REENTRY", -EPERM);\n'
        '\top->boot_claimed = true;\n')
    readiness = body[retained_start:]
    dispatch_start = readiness.index("\t\t\t\t/* IB_DATA_DISPATCH_BEGIN */")
    dispatch_end = readiness.index('\t\t\t\tps4_ready_park(adev, "READY_CONVENTION_OBSERVED", 0);')
    dispatch = readiness[dispatch_start:dispatch_end]
    readiness = readiness[:dispatch_start] + readiness[dispatch_end:]
    # Same software/MMIO/helper order; only terminal park becomes result return.
    terminals = []
    def terminal(match):
        reason, error = match.groups()
        terminals.append(reason)
        return "return ps4_ready_core_finish(op, PS4_READY_CORE_" + reason + ", " + error + ");"
    readiness, count = re.subn(r'ps4_ready_park\(adev, "([A-Z0-9_]+)", ([^;\n]+)\);',
                               terminal, readiness)
    if count != 16 or len(set(terminals)) != 16:
        raise ValueError("unexpected readiness terminal set")
    for old, new in (
        ("ps4_ready_retained_cpu", "op->retained_cpu"),
        ("ps4_ready_retained_pd", "op->retained_pd"),
        ("ps4_ready_firmware", "op->firmware"),
    ):
        readiness = readiness.replace(old, new)
    readiness = once(readiness, "\top->retained_pd = pd_addr;\n",
                     "\top->retained_pd = pd_addr;\n\top->backing_size = backing_size;\n")
    fields = ("guard_sample", "vcpu_seen", "started", "elapsed", "crc",
              "baseline_fault", "fault", "status", "cache_vmid_original",
              "cache_vmid_expected", "reset_count")
    # Replace identifiers outside string/comment tokens. This preserves every
    # original diagnostic format literal and comment byte.
    pieces = re.split(r'("(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*)', readiness, flags=re.S)
    for index in range(0, len(pieces), 2):
        pieces[index] = re.sub(r"\b(" + "|".join(fields) + r")\b",
                               lambda m: "op->" + m.group(), pieces[index])
    readiness = "".join(pieces)
    boundaries = (
        ("\tr = ps4_ready_cache_vmid_preflight", "\top->first_mmio_entered = true;\n"),
        ("\tmemset(r1_cpu, 0, backing_size);", "\top->backing_write_entered = true;\n"),
        ("\tamdgpu_device_flush_hdp(adev, NULL);\n\n\tdev_info", "\top->hdp_flush_entered = true;\n"),
        ("\tamdgpu_ps4_uvd_irq_arm(adev);", "\top->irq_arm_entered = true;\n"),
        ('\tps4_ready_snapshot(adev, "before");', "\top->indexed_snapshot_entered = true;\n"),
        ("\tr = ps4_ready_setup_held", "\top->setup_entered = true;\n"),
        ("\tWREG32(mmUVD_SOFT_RESET, RREG32(mmUVD_SOFT_RESET) &\n\t\t~UVD_SOFT_RESET__RBC_SOFT_RESET_MASK);",
         "\top->release_entered = true;\n"),
    )
    for anchor, addition in boundaries:
        readiness = once(readiness, anchor, addition + anchor)
    names = ["NONE", "NOT_AUTHORIZED"] + terminals
    naming = "\n".join(
        '\tcase PS4_READY_CORE_' + name + ': return "' +
        ("CORE_" + name if name in ("NONE", "NOT_AUTHORIZED") else name) + '";'
        for name in names)
    helper = """/* READY_CORE_FUNCTIONS_BEGIN */
static const char *ps4_ready_core_name(enum ps4_ready_core_terminal terminal)
{
\tswitch (terminal) {
""" + naming + """
\t}
\treturn "CORE_INVALID_TERMINAL";
}

static struct ps4_ready_core_result
ps4_ready_core_finish(struct amdgpu_ps4_uvd_ready_operation *op,
\t\t      enum ps4_ready_core_terminal terminal, int error)
{
\top->result = (struct ps4_ready_core_result) { terminal, error };
\top->completed = true;
\treturn op->result;
}

/*
 * Private returning controller, reachable only from the legacy boot wrapper.
 * A result never grants cleanup, retry, live producer coexistence or rearm.
 * The existing pre-client exclusion remains mandatory; no new caller exists.
 * The original poll deadline bounds that poll, not firmware I/O, clock helpers
 * or lock wait duration. No claim of a whole-worker wall-clock bound is made.
 */
static struct ps4_ready_core_result
ps4_ready_core_run(struct amdgpu_device *adev, void *r1_cpu,
\t\t   size_t backing_size, u64 pd_addr)
{
\tstruct amdgpu_ps4_uvd_ready_operation *op = &adev->ps4_uvd_ready_operation;
\tconst u32 remaining_hold = PS4_READY_HOLD &
\t\t~UVD_SOFT_RESET__RBC_SOFT_RESET_MASK;
\tint r;

\t/* A denied private reentry cannot erase the first retained operation. */
\tif (!op->boot_claimed || op->entered)
\t\treturn (struct ps4_ready_core_result) {
\t\t\tPS4_READY_CORE_NOT_AUTHORIZED, -EPERM };
\top->entered = true;
""" + readiness + """
}
/* READY_CORE_FUNCTIONS_END */

/* READY_CORE_LEGACY_WRAPPER_BEGIN */
""" + signature + """
{
\tstruct amdgpu_ps4_uvd_ready_operation *op = &adev->ps4_uvd_ready_operation;
\tstruct ps4_ready_core_result result;
\tu32 cache_vmid_expected, baseline_fault;
\tint reset_count;

""" + early + """
\tresult = ps4_ready_core_run(adev, r1_cpu, backing_size, pd_addr);
\tif (result.terminal == PS4_READY_CORE_READY_CONVENTION_OBSERVED) {
\t\tcache_vmid_expected = op->cache_vmid_expected;
\t\tbaseline_fault = op->baseline_fault;
\t\treset_count = op->reset_count;
\t\t/* Original diagnostic blocks below are retained verbatim. */
""" + dispatch + """
\t}
\tps4_ready_park(adev, ps4_ready_core_name(result.terminal), result.error);
}
/* READY_CORE_LEGACY_WRAPPER_END */
"""
    prefix = once(prefix,
        "/* Deliberately retained, including all failure paths. No teardown API exists. */\n"
        "static const struct firmware *ps4_ready_firmware;\n"
        "static void *ps4_ready_retained_cpu;\n"
        "static u64 ps4_ready_retained_pd;\n",
        "/* Retained operation/firmware now belongs to adev; admission stays global. */\n")
    prefix = once(prefix, GUARD + "\n", "")
    return prefix + helper


def build(workspace, output):
    receipt_raw = (workspace / BASE / "SOURCE-RECEIPT.json").read_bytes()
    if sha(receipt_raw) != SOURCE_RECEIPT:
        raise ValueError("frozen preparation source receipt mismatch")
    recorded = json.loads(receipt_raw)["candidate_files"]
    if any(recorded[name] != expected for name, expected in PINS.items()):
        raise ValueError("baseline primary pins disagree")
    baseline = {}
    for name, expected in recorded.items():
        value = (workspace / BASE / "candidate" / PREFIX / name).read_bytes()
        if sha(value) != expected:
            raise ValueError("baseline source mismatch: " + name)
        baseline[name] = value
    candidate = dict(baseline)
    candidate[READY] = transform_ready(baseline[READY].decode()).encode()
    state = (HERE / "operation.h.inc").read_text()
    candidate[HEADER] = once(baseline[HEADER].decode(),
        "struct amdgpu_device;\n", "struct amdgpu_device;\n\n" + state).encode()
    header = once(baseline["amdgpu.h"].decode(),
        '#include "amdgpu_ps4_live_prepare.h"\n',
        '#include "amdgpu_ps4_live_prepare.h"\n#include "amdgpu_ps4_uvd_ready.h"\n')
    header = once(header,
        "\tstruct amdgpu_ps4_live_prepare ps4_live_prepare;\n",
        "\tstruct amdgpu_ps4_live_prepare ps4_live_prepare;\n"
        "\tstruct amdgpu_ps4_uvd_ready_operation ps4_uvd_ready_operation;\n")
    candidate["amdgpu.h"] = header.encode()
    # Exact historical data/code fragments remain unchanged, including early
    # validation and dispatch; only the readiness controller is factored.
    block_names = ("TOKEN_FLOW", "DATA_CONTROL_FLOW", "STATUS_OBS_FLOW", "IB_DATA_FLOW",
                   "IB_DATA_EARLY", "DATA_CONTROL_EARLY", "STATUS_OBS_EARLY",
                   "IB_DATA_DISPATCH", "DATA_CONTROL_DISPATCH", "STATUS_OBS_DISPATCH")
    blocks = {}
    for name in block_names:
        before = marker_block(baseline[READY].decode(), name)
        after = marker_block(candidate[READY].decode(), name)
        if before != after:
            raise ValueError("historical block changed: " + name)
        blocks[name] = sha(before.encode())
    changed = sorted(name for name in baseline if baseline[name] != candidate[name])
    if changed != sorted(CHANGED):
        raise ValueError("unexpected changed source set")
    patch = ("From: Linux psX contributors\n"
        "Subject: [EXPERIMENT ONLY] drm/amdgpu: factor boot-only PS4 readiness core\n\n"
        "MIT source refactor over exact preparation attempt06. Retain inherited\n"
        "notices and public-source lineage. No new entrypoint, arm, submission,\n"
        "cleanup, hardware acceptance or whole-worker duration guarantee.\n"
        "The legacy wrapper still claims once, dispatches its frozen diagnostics\n"
        "and parks; the private returning core retains per-device operation state.\n\n")
    for name in changed:
        path = str(PREFIX / name)
        patch += "diff --git a/" + path + " b/" + path + "\n"
        patch += "".join(difflib.unified_diff(
            baseline[name].decode().splitlines(keepends=True),
            candidate[name].decode().splitlines(keepends=True),
            fromfile="a/" + path, tofile="b/" + path))
    output.mkdir(parents=True, exist_ok=False)
    for category, files in (("baseline", baseline), ("candidate", candidate)):
        root = output / category / PREFIX
        root.mkdir(parents=True)
        for name, value in files.items():
            with (root / name).open("xb") as stream:
                stream.write(value)
    patch_name = "0013-drm-amdgpu-ps4-returning-ready-core.patch"
    with (output / patch_name).open("x") as stream:
        stream.write(patch)
    report = {
        "schema": 1, "status": "GENERATED_OFFLINE_READY_CORE_CANDIDATE",
        "baseline_source_receipt_sha256": SOURCE_RECEIPT,
        "baseline_files": {name: sha(value) for name, value in baseline.items()},
        "candidate_files": {name: sha(value) for name, value in candidate.items()},
        "changed_files": changed, "historical_blocks_sha256": blocks,
        "patch": {"path": patch_name, "sha256": sha(patch.encode())},
        "generator_sha256": sha(Path(__file__).read_bytes()),
        "generator_inputs": {"operation.h.inc": sha((HERE / "operation.h.inc").read_bytes())},
        "native_kernel_built": False, "hardware_tested": False,
        "live_entrypoint": False, "cleanup_implemented": False,
    }
    with (output / "SOURCE-RECEIPT.json").open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build(args.workspace.resolve(), args.output.resolve())
    print(json.dumps(report, sort_keys=True))
