#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Isolated source/patch generation for the boot-only CREATE controller."""
from __future__ import annotations
import argparse
import difflib
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFIX = Path("drivers/gpu/drm/amd/amdgpu")
BASE = Path("lab/uvd-2026-10-20/driver-integration/attempt-01")
BASE_MANIFEST = "e253c4fee2ec15e7da8a3f74633c1a58b28728a955b68ba614ba6f282a1d2485"
KERNEL_INPUTS = Path("lab/uvd-2026-10-20/message-create/kernel-inputs")
READY = "amdgpu_ps4_uvd_ready.c"
CANARY = "amdgpu_ps4_map_canary.c"
HEADER = "amdgpu_ps4_uvd_ready.h"
STATE = "amdgpu_ps4_uvd_message_create.h"

PARAMETER = """/* MESSAGE_CREATE_PARAMETER_BEGIN */
/* Immutable boot-only gate; no live endpoint or repeated submission. */
static bool ps4_uvd_message_create;
module_param_named(ps4_uvd_message_create, ps4_uvd_message_create, bool, 0444);
MODULE_PARM_DESC(ps4_uvd_message_create,
\t"One PS4 UVD public CREATE plus polled prefix fence; requires ready+poll_state, excludes all other probes, always parks (default 0)");

bool amdgpu_ps4_uvd_message_create_requested(void)
{
\treturn ps4_uvd_message_create;
}

bool amdgpu_ps4_uvd_message_create_options_valid(struct amdgpu_device *adev)
{
\treturn ps4_uvd_ready && ps4_uvd_poll_state && !ps4_uvd_token &&
\t\t!ps4_uvd_data_control && !ps4_uvd_status_observation &&
\t\t!ps4_uvd_ib_data && !amdgpu_ps4_live_prepare_enabled(adev) &&
\t\tamdgpu_ps4_uvd_ready_enabled(adev);
}
/* MESSAGE_CREATE_PARAMETER_END */

"""
EARLY = """\t/* MESSAGE_CREATE_EARLY_BEGIN */
\tif (ps4_uvd_message_create &&
\t    (!amdgpu_ps4_uvd_message_create_options_valid(adev) ||
\t     !ps4_create_descriptors_valid(rb, ib, pd_addr)))
\t\tps4_ready_park(adev, "CREATE_DESCRIPTOR_OR_GATES", -EINVAL);
\t/* MESSAGE_CREATE_EARLY_END */
"""
DISPATCH = """\t\t/* MESSAGE_CREATE_DISPATCH_BEGIN */
\t\tif (ps4_uvd_message_create) {
\t\t\tstruct ps4_create_result message_result;

\t\t\tmessage_result = ps4_create_run(adev, rb, ib, pd_addr,
\t\t\t\tcache_vmid_expected, baseline_fault, reset_count);
\t\t\tps4_create_report(adev);
\t\t\tps4_ready_park(adev, ps4_create_name(message_result.terminal),
\t\t\t\tmessage_result.error);
\t\t}
\t\t/* MESSAGE_CREATE_DISPATCH_END */
"""
BORROW = """/* MESSAGE_CREATE_OWNED_BACKING_BEGIN */
/* Borrow the complete existing 128KiB allocation; no additional BO or PTE. */
static bool ps4_canary_message_backing(struct ps4_canary_state *s,
\t\t\t\t      struct amdgpu_ps4_uvd_ring *backing)
{
\tstruct ps4_canary_bo *bo;

\tif (!s || !backing)
\t\treturn false;
\tbo = &s->uvd_ib;
\tif (s != ps4_canary_retained || !s->ready_mode || !s->submitted ||
\t    !s->fence || !bo->bo || !bo->cpu || ((unsigned long)bo->cpu & 3U) ||
\t    !bo->bo->tbo.resource || !bo->bo->tbo.pin_count ||
\t    bo->bo->tbo.type != ttm_bo_type_kernel ||
\t    bo->bo->tbo.resource->mem_type != TTM_PL_TT ||
\t    amdgpu_bo_size(bo->bo) != PS4_READY_IB_BYTES ||
\t    bo->bo == s->uvd_rb.bo || s->mapped_pages != PS4_READY_MAPPED_PAGES)
\t\treturn false;
\tbacking->cpu = bo->cpu;
\tbacking->bytes = PS4_READY_IB_BYTES;
\t/* bo->gpu is a different native GART alias; never use it here. */
\tbacking->private_va = PS4_CANARY_VA +
\t\t(u64)PS4_READY_IB_SLOT * AMDGPU_GPU_PAGE_SIZE;
\treturn true;
}
/* MESSAGE_CREATE_OWNED_BACKING_END */

"""

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("nonunique source anchor: " + repr(old[:120]))
    return text.replace(old, new, 1)

def transformations(sources):
    new = dict(sources)
    text = sources[READY].decode()
    text = once(text, "bool amdgpu_ps4_uvd_token_requested(void)\n", PARAMETER +
                "bool amdgpu_ps4_uvd_token_requested(void)\n")
    text = once(text, "\t\tps4_uvd_ib_data;\n", "\t\tps4_uvd_ib_data || ps4_uvd_message_create;\n")
    text = once(text, "/* READY_CORE_FUNCTIONS_BEGIN */",
                (HERE / "flow.c.inc").read_text() + "\n/* READY_CORE_FUNCTIONS_BEGIN */")
    text = once(text, "\top->boot_claimed = true;\n",
                "\top->boot_claimed = true;\n" + EARLY)
    text = once(text, "\t\t/* Original diagnostic blocks below are retained verbatim. */",
                DISPATCH + "\t\t/* Original diagnostic blocks below are retained verbatim. */")
    new[READY] = text.encode()
    text = sources[CANARY].decode()
    text = once(text, "\treturn (ps4_map_canary || amdgpu_ps4_uvd_ready_enabled(adev) ||\n",
                "\treturn (ps4_map_canary || amdgpu_ps4_uvd_message_create_requested() ||\n"
                "\t\tamdgpu_ps4_uvd_ready_enabled(adev) ||\n")
    text = once(text, "void amdgpu_ps4_map_canary_run(struct amdgpu_device *adev)\n",
                BORROW + "void amdgpu_ps4_map_canary_run(struct amdgpu_device *adev)\n")
    text = once(text, "\tif (!amdgpu_ps4_map_canary_enabled(adev))\n\t\treturn;\n",
                "\tif (!amdgpu_ps4_map_canary_enabled(adev))\n\t\treturn;\n"
                "\t/* Reject a selected CREATE leaf even when ready was omitted. */\n"
                "\tif (amdgpu_ps4_uvd_message_create_requested() &&\n"
                "\t    (ps4_map_canary || !amdgpu_ps4_uvd_message_create_options_valid(adev)))\n"
                '\t\tps4_canary_park(adev, "message-create-conflicting-gates", -EINVAL);\n')
    text = once(text, "\t\tstruct amdgpu_ps4_uvd_ring token_ring = { 0 };\n",
                "\t\tstruct amdgpu_ps4_uvd_ring token_ring = { 0 };\n"
                "\t\tstruct amdgpu_ps4_uvd_ring message_backing = { 0 };\n")
    text = once(text, "\t\tif (amdgpu_ps4_uvd_token_requested() &&\n",
                "\t\tif ((amdgpu_ps4_uvd_token_requested() ||\n"
                "\t\t     amdgpu_ps4_uvd_message_create_requested()) &&\n")
    text = once(text, "\t\t/* IB_DATA_BORROW_BEGIN */",
                "\t\tif (amdgpu_ps4_uvd_message_create_requested() &&\n"
                "\t\t    !ps4_canary_message_backing(s, &message_backing))\n"
                '\t\t\tps4_canary_park(adev, "message-create-owned-backing", -EINVAL);\n'
                "\t\t/* IB_DATA_BORROW_BEGIN */")
    text = once(text,
        "\t\t\tamdgpu_ps4_uvd_token_requested() ? &token_ring : NULL,\n"
        "\t\t\tamdgpu_ps4_uvd_ib_data_requested() ? &ib_page : NULL);",
        "\t\t\t(amdgpu_ps4_uvd_token_requested() ||\n"
        "\t\t\t amdgpu_ps4_uvd_message_create_requested()) ? &token_ring : NULL,\n"
        "\t\t\tamdgpu_ps4_uvd_message_create_requested() ? &message_backing :\n"
        "\t\t\t(amdgpu_ps4_uvd_ib_data_requested() ? &ib_page : NULL));")
    new[CANARY] = text.encode()
    text = sources[HEADER].decode()
    text = once(text, "bool amdgpu_ps4_uvd_token_requested(void);",
        "bool amdgpu_ps4_uvd_message_create_requested(void);\n"
        "bool amdgpu_ps4_uvd_message_create_options_valid(struct amdgpu_device *adev);\n"
        "bool amdgpu_ps4_uvd_token_requested(void);")
    new[HEADER] = text.encode()
    text = sources["amdgpu.h"].decode()
    text = once(text, '#include "amdgpu_ps4_uvd_ready.h"\n',
                '#include "amdgpu_ps4_uvd_ready.h"\n#include "amdgpu_ps4_uvd_message_create.h"\n')
    text = once(text, "\tstruct amdgpu_ps4_uvd_ready_operation ps4_uvd_ready_operation;\n",
                "\tstruct amdgpu_ps4_uvd_ready_operation ps4_uvd_ready_operation;\n"
                "\tstruct amdgpu_ps4_uvd_create_operation ps4_uvd_create_operation;\n")
    new["amdgpu.h"] = text.encode()
    text = sources["Makefile"].decode()
    text = once(text, "amdgpu-y += amdgpu_ps4_uvd_ready.o\n",
                "amdgpu-y += amdgpu_ps4_uvd_ready.o\n"
                "amdgpu-y += ps4_uvd_message.o ps4_uvd_message_job.o\n")
    new["Makefile"] = text.encode()
    new[STATE] = (HERE / "operation.h").read_bytes()
    return new

def build(workspace, output, library_pins):
    if output.exists():
        raise ValueError("output already exists; preserve prior attempt")
    manifest_raw = (workspace / BASE / "MANIFEST.json").read_bytes()
    if sha(manifest_raw) != BASE_MANIFEST:
        raise ValueError("composition manifest mismatch")
    manifest = json.loads(manifest_raw)
    source = {}
    for path, expected in manifest["candidate_files"].items():
        raw = (workspace / BASE / "candidate" / path).read_bytes()
        if sha(raw) != expected or Path(path).parent != PREFIX:
            raise ValueError("baseline mismatch: " + path)
        source[Path(path).name] = raw
    kernel_inputs = json.loads((workspace / KERNEL_INPUTS / "INPUTS.json").read_bytes())
    for name, expected in kernel_inputs["files"].items():
        raw = (workspace / KERNEL_INPUTS / name).read_bytes()
        if sha(raw) != expected:
            raise ValueError("native source input mismatch: " + name)
        source[name] = raw
    new = transformations(source)
    for name, item in library_pins.items():
        raw = (workspace / item["path"]).read_bytes()
        if sha(raw) != item["sha256"]:
            raise ValueError("library mismatch: " + name)
        new[name] = raw
    output.mkdir(parents=True)
    for category, files in (("baseline", source), ("candidate", new)):
        for name, raw in files.items():
            path = output / category / PREFIX / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    changes = sorted(name for name in set(source) | set(new) if source.get(name) != new.get(name))
    patch = ("From: Linux psX development <linux-psx@localhost>\n"
             "Subject: [EXPERIMENT] drm/amdgpu: one boot-only public CREATE and polled prefix fence\n\n"
             "Add a default-off finite returning controller after the existing ready\n"
             "convention. Retain all mappings and park on every result. No message\n"
             "parsing, DMA quiescence, live endpoint or decoded-frame claim follows.\n"
             "Public source notices remain in the serializer and packet planner.\n\n")
    for name in changes:
        path = str(PREFIX / name)
        patch += f"diff --git a/{path} b/{path}\n"
        if name not in source:
            patch += "new file mode 100644\n"
        patch += "".join(difflib.unified_diff(
            source.get(name, b"").decode().splitlines(True),
            new[name].decode().splitlines(True),
            fromfile="a/" + path if name in source else "/dev/null",
            tofile="b/" + path))
    patch_name = "0014-drm-amdgpu-ps4-public-create-fence.patch"
    (output / patch_name).write_text(patch)
    receipt = {
        "schema": 1, "status": "SOURCE_CANDIDATE_ONLY",
        "base_manifest_sha256": BASE_MANIFEST,
        "generator_sha256": sha(Path(__file__).read_bytes()),
        "generator_inputs": {n: sha((HERE / n).read_bytes()) for n in ("flow.c.inc", "operation.h")},
        "library_pins": library_pins, "kernel_inputs": kernel_inputs,
        "baseline_files": {n: sha(v) for n, v in source.items()},
        "candidate_files": {n: sha(v) for n, v in new.items()},
        "changed_files": changes,
        "patch": {"path": patch_name, "sha256": sha(patch.encode())},
        "hardware_acceptance": False, "full_matching_rebuild_required": True,
        "limits": ["boot-only private caller; no live worker or endpoint",
                   "no DMA stop/free/retry/reset/rearm is authorized by a result",
                   "two sequential predeadline fence/context/pointer observations only",
                   "CREATE parsing, session state and decode remain unproved"],
    }
    (output / "SOURCE-RECEIPT.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--library-pins", type=Path, required=True)
    args = ap.parse_args()
    print(json.dumps(build(args.workspace.resolve(), args.output.resolve(),
                           json.loads(args.library_pins.read_text())), indent=2))
