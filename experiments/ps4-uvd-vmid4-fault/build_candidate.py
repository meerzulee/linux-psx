#!/usr/bin/env python3
"""Emit an isolated, exact-baseline captured GMC fault-history patch.

Reads source files only. No kernel build, checkout mutation or hardware access.
Existing output directories are never reused.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
REL = Path("drivers/gpu/drm/amd/amdgpu")
MASK_REL = Path("drivers/gpu/drm/amd/include/asic_reg/gmc/gmc_7_1_sh_mask.h")
MASK_PIN = "6c3a33360b4ed179cc62fa7ecda27a462552cd740e1409181cbaae1f705704e1"
BASELINE = WORKSPACE / "lab/uvd-2026-10-20/live-ready-design/native-lifecycle-evidence/source"
PINS = {
    "amdgpu_ps4_uvd_irq.c": "ccf618c976b9a7b80045ffaeb81b7ede0707fa98bef8aca4bc7a7653a3de5480",
    "amdgpu_ps4_uvd_irq.h": "17730b60c04f20a7d703151350e26f1ae7657acd77da406614c278ba5ca9e7f1",
    "gmc_v7_0.c": "7304d56b2fabbfbd2aff520dd6ee182dcbe1d0faf984f81b843662726971c4a3",
}
PATCH_NAME = "0012-drm-amdgpu-ps4-record-armed-gmc-fault-history.patch"
OLD_COMMENT = "/* Private PS4 readiness-probe telemetry. This observes decoded hardware IH\n"
NEW_COMMENT = "/* Private PS4 readiness-probe telemetry. Legacy slots observe decoded hardware IH\n"
HOOK = """\t/* VMID4_FAULT_HOOK_BEGIN */
\t/* Preserve the captured tuple after acknowledgement, before reporting. */
\tamdgpu_ps4_uvd_irq_record_fault(adev, addr, status, mc_client);
\t/* VMID4_FAULT_HOOK_END */

"""
INIT = """\t/* GMC_FAULT_INIT_BEGIN */
\tspin_lock_init(&obs->fault_lock);
\tobs->fault_history = (struct ps4_uvd_fault_history) { };
\t/* GMC_FAULT_INIT_END */
"""
FIELDS = """\t/* GMC_FAULT_FIELDS_BEGIN */
\tspinlock_t fault_lock;
\tstruct ps4_uvd_fault_history fault_history;
\t/* GMC_FAULT_FIELDS_END */
"""
API = """/* GMC_FAULT_API_BEGIN */
/* The existing arm bounds this history. Initialization is before IRQ setup;
 * there is no clear/reset API. A device reset does not erase observations.
 * Inactive snapshots or saturated buckets cannot qualify an interval.
 * The total covers every observed nonempty handler tuple; exactly one VMID
 * bucket or literal all-ones-status bucket is updated with it. Categories
 * aid diagnosis, not cause attribution; zero is not proof of fault freedom.
 * These functions do not touch GPU registers. No consumer is added here.
 */
void amdgpu_ps4_uvd_irq_record_fault(struct amdgpu_device *adev,
\t\t\t\t   u32 addr, u32 status, u32 mc_client);
void amdgpu_ps4_uvd_irq_fault_snapshot(struct amdgpu_device *adev,
\t\t\t\t     struct ps4_uvd_fault_snapshot *snapshot);
/* GMC_FAULT_API_END */

"""


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"Expected one source anchor, found {text.count(old)}: {old!r}")
    return text.replace(old, new, 1)


def transform(inputs: dict[str, str]) -> dict[str, str]:
    result = dict(inputs)
    name = "amdgpu_ps4_uvd_irq.h"
    source = once(inputs[name], "#include <linux/atomic.h>\n",
                  "#include <linux/atomic.h>\n#include <linux/spinlock.h>\n")
    source = once(source, "struct ps4_uvd_irq_observer {\n",
                  (HERE / "fault.h.inc").read_text() + "\nstruct ps4_uvd_irq_observer {\n")
    source = once(source, "\tstruct ps4_uvd_irq_slot slots[PS4_UVD_IRQ_SLOTS];\n",
                  "\tstruct ps4_uvd_irq_slot slots[PS4_UVD_IRQ_SLOTS];\n" + FIELDS)
    source = once(source, "\n#endif\n", "\n" + API + "#endif\n")
    result[name] = source

    name = "amdgpu_ps4_uvd_irq.c"
    source = once(inputs[name], OLD_COMMENT, NEW_COMMENT)
    source = once(source, '#include "ivsrcid/ivsrcid_vislands30.h"\n',
                  '#include "ivsrcid/ivsrcid_vislands30.h"\n'
                  '#include "gmc/gmc_7_1_sh_mask.h"\n')
    source = once(source, "\tobs->arm_ns = 0;\n", "\tobs->arm_ns = 0;\n" + INIT)
    source = once(source, "void amdgpu_ps4_uvd_irq_report(struct amdgpu_device *adev)\n",
                  (HERE / "fault.c.inc").read_text() + "\n"
                  "void amdgpu_ps4_uvd_irq_report(struct amdgpu_device *adev)\n")
    result[name] = source

    name = "gmc_v7_0.c"
    source = once(inputs[name], '#include "amdgpu.h"\n',
                  '#include "amdgpu.h"\n#include "amdgpu_ps4_uvd_irq.h"\n')
    anchor = "\tif (!addr && !status)\n\t\treturn 0;\n\n"
    source = once(source, anchor, anchor + HOOK)
    result[name] = source
    return result


def generate(baseline: Path, out: Path, mask_header: Path, mask_sha256: str) -> dict:
    if out.exists():
        raise ValueError("Output exists; preserve it and choose a new directory")
    raw = {}
    for name, expected in PINS.items():
        path = baseline / REL / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Expected regular baseline file: {path}")
        raw[name] = path.read_bytes()
        if sha(raw[name]) != expected:
            raise ValueError(f"Exact baseline mismatch: {name}")
    if mask_header.is_symlink() or not mask_header.is_file():
        raise ValueError("Expected a saved regular mask header")
    mask = mask_header.read_bytes()
    if mask_sha256 != MASK_PIN or sha(mask) != MASK_PIN:
        raise ValueError("Native mask header identity mismatch")
    transformed = transform({name: value.decode() for name, value in raw.items()})
    patch = (
        "From: Linux psX contributors\n"
        "Subject: [PATCH] drm/amdgpu: retain all armed PS4 GMC fault tuples\n\n"
        "Original MIT-licensed observation extension over the exact native02\n"
        "preparation candidate. Record each captured tuple in a total and one\n"
        "category after existing acknowledgement. Keep literal all-ones status\n"
        "separate from VMID15; add no MMIO or legacy IH telemetry change.\n"
        "This is an unstaged source candidate, not hardware qualification.\n\n"
    )
    for name in sorted(PINS):
        relative = (REL / name).as_posix()
        patch += f"diff --git a/{relative} b/{relative}\n"
        patch += "".join(difflib.unified_diff(raw[name].decode().splitlines(True),
                         transformed[name].splitlines(True),
                         fromfile=f"a/{relative}", tofile=f"b/{relative}", n=3))
    out.mkdir(parents=True, mode=0o700)
    for name, value in raw.items():
        for directory, content in (("baseline", value),
                                   ("candidate", transformed[name].encode())):
            path = out / directory / REL / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(content)
    saved_header = out / "reference" / MASK_REL
    saved_header.parent.mkdir(parents=True)
    saved_header.write_bytes(mask)
    (out / PATCH_NAME).write_text(patch)
    tools = {}
    snapshot = out / "generator"
    snapshot.mkdir()
    for name in ("build_candidate.py", "fault.c.inc", "fault.h.inc"):
        data = (HERE / name).read_bytes()
        (snapshot / name).write_bytes(data)
        tools[name] = sha(data)
    receipt = {
        "schema": 1,
        "revision": 2,
        "status": "UNSTAGED_SOURCE_CANDIDATE_NO_BUILD_OR_HARDWARE",
        "baseline_files": PINS,
        "candidate_files": {name: sha(value.encode()) for name, value in transformed.items()},
        "patch": {"file": PATCH_NAME, "sha256": sha(patch.encode())},
        "generator_files": tools,
        "reference_header": {"path": str(MASK_REL), "sha256": sha(mask)},
        "baseline_source_path": str(baseline.resolve()),
        "kernel_built": False,
        "hardware_tested": False,
        "scope": "Existing armed interval; all nonempty captured GMC-handler observations",
        "history": {"total_buckets": 1, "vmid_buckets": 16,
                    "unclassified_status": "0xffffffff", "consumer_added": False},
    }
    with (out / "SOURCE-RECEIPT.json").open("x") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mask-header", type=Path, required=True)
    parser.add_argument("--mask-sha256", required=True,
                        help="Exact native02 header identity from saved source evidence")
    args = parser.parse_args()
    receipt = generate(args.baseline, args.out, args.mask_header, args.mask_sha256)
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
