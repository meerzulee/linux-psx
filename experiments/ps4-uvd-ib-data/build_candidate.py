#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate the bounded IB-only DATA0 candidate from frozen Revision C.

This prepares source and a patch only. It neither builds a kernel nor accesses
devices. Every old file is hash-pinned and every adaptation is reversed and
compared byte-for-byte before outputs are written. The borrowed-page helper
comes from the separately pinned earlier IB candidate; its ownership checks
remain, with explicit NULL rejection added. The packet builder is pinned too.
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
BASE_REL = Path("lab/uvd-2026-10-19/status-observation/source/candidate")
EARLIER_REL = Path("lab/uvd-2026-10-19/ib-fetch/source/candidate")
OUT_REL = Path("lab/uvd-2026-10-20/ib-data/source/candidate")
PATCH_NAME = "0010-drm-amdgpu-ps4-uvd-ib-data-marker.patch"
BASE_HASHES = {
    "amdgpu_ps4_uvd_ready.c":
        "60b55de93ef8a3b38626fbb5f063d76dd90cd52a5cd10b9250015fb384707518",
    "amdgpu_ps4_map_canary.c":
        "523a24339622bf81e2dd3e5d99f88cdfe77de1709b34f74f9369ad64f70b6305",
    "amdgpu_ps4_uvd_ready.h":
        "f49a2648015b8c23998f1e95cab6fc1c9ed157e076eadfa74116c926dd98347d",
    "amdgpu_ps4_uvd_ring_plan.h":
        "2299fd28005bdbcc2aa3c3664740f5d87a34cb7294c705c45facdde46d44df2e",
}
EARLIER_CANARY_SHA256 = (
    "109e4086b2d88d45d01fa81ed1c1010e4d67b2f634b814e87337bbe2f409c255"
)
PLANNER_SHA256 = "ef7fe8ed33507c93d07c62347b57add08daa8dd43727259440321c9f06fdf0e6"

INCLUDE = '''/* IB_DATA_INCLUDE_BEGIN */
#include "amdgpu_ps4_uvd_ib_data_plan.h"
/* IB_DATA_INCLUDE_END */
'''

PARAMETER = '''/* IB_DATA_PARAMETER_BEGIN */
/* Immutable default-off boot diagnostic; all outcomes retain and park. */
static bool ps4_uvd_ib_data;
module_param_named(ps4_uvd_ib_data, ps4_uvd_ib_data, bool, 0444);
MODULE_PARM_DESC(ps4_uvd_ib_data,
	"One PS4 UVD IB-only DATA0 marker; requires token+ready+poll_state, excludes data_control/status_observation, always parks (default 0)");

bool amdgpu_ps4_uvd_ib_data_requested(void)
{
	return ps4_uvd_ib_data;
}
/* IB_DATA_PARAMETER_END */

'''

EARLY = '''	/* IB_DATA_EARLY_BEGIN: before firmware and all readiness setup. */
	if (ps4_uvd_ib_data &&
	    (ps4_uvd_data_control || ps4_uvd_status_observation))
		ps4_ready_park(adev, "IBDATA_OPTIONS_EXCLUSIVE", -EINVAL);
	if (ps4_uvd_ib_data && !ps4_ib_data_inputs_valid(rb, ib, pd_addr))
		ps4_ready_park(adev, "IBDATA_DESCRIPTOR_OR_GATES", -EINVAL);
	/* IB_DATA_EARLY_END */
'''

DISPATCH = '''				/* IB_DATA_DISPATCH_BEGIN */
				if (ps4_uvd_ib_data)
					ps4_ib_data_run(adev, rb, ib, pd_addr,
						cache_vmid_expected, baseline_fault,
						reset_count);
				/* IB_DATA_DISPATCH_END */
'''

HEADER_REQUESTED = '''/* IB_DATA_REQUESTED_BEGIN */
bool amdgpu_ps4_uvd_ib_data_requested(void);
/* IB_DATA_REQUESTED_END */
'''

CANARY_DECL = '''		/* IB_DATA_BORROW_DECL_BEGIN */
		struct amdgpu_ps4_uvd_ring ib_page = { 0 };
		/* IB_DATA_BORROW_DECL_END */
'''

CANARY_BORROW = '''		/* IB_DATA_BORROW_BEGIN */
		if (amdgpu_ps4_uvd_ib_data_requested() &&
		    !ps4_canary_ib_data_page(s, &ib_page))
			ps4_canary_park(adev, "ib-data-owned-page", -EINVAL);
		/* IB_DATA_BORROW_END */
'''

READY_API_OLD = "const struct amdgpu_ps4_uvd_ring *rb)\n{\n\tstruct ps4_ready_guard_sample"
READY_API_NEW = ("const struct amdgpu_ps4_uvd_ring *rb,\n"
                 "\t\t\t\t      const struct amdgpu_ps4_uvd_ring *ib)\n"
                 "{\n\tstruct ps4_ready_guard_sample")
HEADER_API_OLD = "const struct amdgpu_ps4_uvd_ring *rb);"
HEADER_API_NEW = ("const struct amdgpu_ps4_uvd_ring *rb,\n"
                  "\t\t\t\t      const struct amdgpu_ps4_uvd_ring *ib);")
CALL_OLD = "amdgpu_ps4_uvd_token_requested() ? &token_ring : NULL);"
CALL_NEW = ("amdgpu_ps4_uvd_token_requested() ? &token_ring : NULL,\n"
            "\t\t\tamdgpu_ps4_uvd_ib_data_requested() ? &ib_page : NULL);")

PATCH_HEADER = """From: Linux psX contributors
Date: Tue, 20 Oct 2026 03:30:00 +0600
Subject: [PATCH] drm/amdgpu: add one bounded PS4 UVD IB-only DATA0 marker

Add an immutable default-off boot diagnostic requiring token, ready and
poll_state and excluding data_control/status_observation. Borrow the first
page of the already owned, retained 128 KiB IB BO through the readiness API;
validate descriptors before readiness/firmware work and image mutation.
Old flows receive NULL for the new argument and remain byte-identical.

Keep EXP004's UVD5 LOW/HIGH/SIZE16 main-ring image. Put one fresh DATA0
marker only in the IB, with NOP padding. The CPU plants distinct DATA0 and
CONTEXT_ID baselines; it never stores the result into DATA0. There is one
WPTR16 publication, no CMD/DATA1/fence/trap/message, allocation or teardown.

Adapt Revision C's bounded observation instrument: 250 ms anchored before
fetch, 24 fine plus 16 coarse records at most, direct reads only in-window.
Only complete valid samples ending strictly before the deadline qualify;
two consecutive joint marker/drain observations are required. Preserve
late/failing observations separately. Terminal direct predicates and the
endpoint indexed sweep must pass; endpoint values cannot create evidence.
Report marker evidence independently from terminal errors and raw bit16/IB
size without assigning undocumented Liverpool semantics. Every outcome parks.

Source provenance: frozen Revision C ready.c SHA256 60b55de93ef8a3b38626fbb5f063d76dd90cd52a5cd10b9250015fb384707518;
earlier IB candidate borrowed-page helper SHA256 109e4086b2d88d45d01fa81ed1c1010e4d67b2f634b814e87337bbe2f409c255.
Public protocol comparison: Linux a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea,
uvd_v5_0_ring_emit_ib; no imported firmware or proprietary implementation.
New diagnostic additions match the containing readiness/canary MIT license;
the separate packet-construction header is GPL-2.0-only. All inherited
source and existing per-file notices are retained.
This source patch alone establishes no build or hardware acceptance.

"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_pinned(path: Path, digest: str) -> str:
    data = path.read_bytes()
    if sha256(data) != digest:
        raise ValueError(f"frozen input mismatch: {path}")
    return data.decode("utf-8")


def replace_unique(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"source anchor is not unique: {old[:90]!r}")
    return text.replace(old, new, 1)


def borrow_helper(earlier: str) -> str:
    start = earlier.index("/* Borrow only the first page of the existing,")
    end = earlier.index("\nvoid amdgpu_ps4_map_canary_run(", start)
    original = earlier[start:end]
    helper = replace_unique(original, "ps4_canary_writeback_page(",
                            "ps4_canary_ib_data_page(")
    helper = helper.replace("struct amdgpu_ps4_uvd_writeback *wb",
                            "struct amdgpu_ps4_uvd_ring *ib_page")
    helper = helper.replace("wb->", "ib_page->")
    helper = replace_unique(helper,
        "struct ps4_canary_bo *bo = &s->uvd_ib;",
        "struct ps4_canary_bo *bo;\n\n"
        "\tif (!s || !ib_page)\n"
        "\t\treturn false;\n"
        "\tbo = &s->uvd_ib;")
    # Reverse the deliberate changes rather than blessing arbitrary helper drift.
    restored = replace_unique(helper,
        "struct ps4_canary_bo *bo;\n\n"
        "\tif (!s || !ib_page)\n"
        "\t\treturn false;\n"
        "\tbo = &s->uvd_ib;",
        "struct ps4_canary_bo *bo = &s->uvd_ib;")
    restored = restored.replace("ib_page->", "wb->")
    restored = restored.replace("struct amdgpu_ps4_uvd_ring *ib_page",
                                 "struct amdgpu_ps4_uvd_writeback *wb")
    restored = replace_unique(restored, "ps4_canary_ib_data_page(",
                               "ps4_canary_writeback_page(")
    if restored != original:
        raise ValueError("borrowed helper normalization mismatch")
    return ("/* IB_DATA_OWNED_PAGE_BEGIN */\n" + helper +
            "/* IB_DATA_OWNED_PAGE_END */\n")


def assert_sampler_parity(base: str, flow: str) -> None:
    start = base.index("static bool ps4_stobs_core_sample(")
    end = base.index("\nstatic bool ps4_stobs_ctx_sweep(", start)
    old = base[start:end].rstrip()
    start = flow.index("static bool ps4_ib_data_core_sample(")
    end = flow.index("\n/* IB_DATA_CORE_SAMPLE_END */", start)
    new = flow[start:end].rstrip()
    normalized = replace_unique(new, "ps4_ib_data_core_sample(",
                                "ps4_stobs_core_sample(")
    normalized = replace_unique(normalized,
        "\tif (phase != PS4_TOKEN_SUBMITTED)\n"
        "\t\tTOKEN_SAVED(mmUVD_RBC_IB_SIZE, s->ib_size, 0);",
        "\tTOKEN_SAVED(mmUVD_RBC_IB_SIZE, s->ib_size, 0);")
    if normalized != old:
        raise ValueError("direct sampler differs beyond submitted IB_SIZE policy")


def build_sources(workspace: Path) -> tuple[dict[str, str], dict[str, str]]:
    old = {name: read_pinned(workspace / BASE_REL / REL / name, digest)
           for name, digest in BASE_HASHES.items()}
    earlier = read_pinned(workspace / EARLIER_REL / REL /
                          "amdgpu_ps4_map_canary.c", EARLIER_CANARY_SHA256)
    planner = read_pinned(HERE / "ps4_uvd_ib_data_plan.h", PLANNER_SHA256)
    flow = (HERE / "ib_data_flow.inc").read_text()
    if not (flow.startswith("/* IB_DATA_FLOW_BEGIN:") and
            flow.endswith("/* IB_DATA_FLOW_END */\n")):
        raise ValueError("flow extraction markers missing")
    assert_sampler_parity(old["amdgpu_ps4_uvd_ready.c"], flow)
    owned_page = borrow_helper(earlier)
    changes: dict[str, list[tuple[str, str]]] = {
        "amdgpu_ps4_uvd_ready.c": [
            ('#include "amdgpu_ps4_uvd_ring_plan.h"\n',
             '#include "amdgpu_ps4_uvd_ring_plan.h"\n' + INCLUDE),
            ("bool amdgpu_ps4_uvd_token_requested(void)\n",
             PARAMETER + "bool amdgpu_ps4_uvd_token_requested(void)\n"),
            ("/* STATUS_OBS_FLOW_END */\n",
             "/* STATUS_OBS_FLOW_END */\n" + flow),
            (READY_API_OLD, READY_API_NEW),
            ("\t/* DATA_CONTROL_EARLY_BEGIN: before firmware and all readiness setup. */\n",
             EARLY + "\t/* DATA_CONTROL_EARLY_BEGIN: before firmware and all readiness setup. */\n"),
            ("\t\t\t\t/* DATA_CONTROL_DISPATCH_BEGIN */\n",
             DISPATCH + "\t\t\t\t/* DATA_CONTROL_DISPATCH_BEGIN */\n"),
        ],
        "amdgpu_ps4_map_canary.c": [
            ("void amdgpu_ps4_map_canary_run(struct amdgpu_device *adev)\n",
             owned_page + "\nvoid amdgpu_ps4_map_canary_run(struct amdgpu_device *adev)\n"),
            ("\t\tstruct amdgpu_ps4_uvd_ring token_ring = { 0 };\n",
             "\t\tstruct amdgpu_ps4_uvd_ring token_ring = { 0 };\n" + CANARY_DECL),
            ("\t\tamdgpu_ps4_uvd_ready_run(adev, s->r1.cpu,\n",
             CANARY_BORROW + "\t\tamdgpu_ps4_uvd_ready_run(adev, s->r1.cpu,\n"),
            (CALL_OLD, CALL_NEW),
        ],
        "amdgpu_ps4_uvd_ready.h": [
            ("bool amdgpu_ps4_uvd_token_requested(void);\n",
             HEADER_REQUESTED + "bool amdgpu_ps4_uvd_token_requested(void);\n"),
            (HEADER_API_OLD, HEADER_API_NEW),
        ],
    }
    new = dict(old)
    for name, replacements in changes.items():
        for before, after in replacements:
            new[name] = replace_unique(new[name], before, after)
        normalized = new[name]
        for before, after in reversed(replacements):
            normalized = replace_unique(normalized, after, before)
        if normalized != old[name]:
            raise ValueError(f"old source normalization mismatch: {name}")
    new["amdgpu_ps4_uvd_ib_data_plan.h"] = planner
    return old, new


def make_patch(old: dict[str, str], new: dict[str, str]) -> str:
    parts = [PATCH_HEADER]
    for name in sorted(new):
        if old.get(name) == new[name]:
            continue
        rel = (REL / name).as_posix()
        parts.append(f"diff --git a/{rel} b/{rel}\n")
        if name not in old:
            parts.append("new file mode 100644\n")
        parts.extend(difflib.unified_diff(
            old.get(name, "").splitlines(keepends=True),
            new[name].splitlines(keepends=True),
            fromfile=f"a/{rel}" if name in old else "/dev/null",
            tofile=f"b/{rel}"))
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true",
                        help="compare existing outputs without writing")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    out = args.output or workspace / OUT_REL
    old, new = build_sources(workspace)
    patch = make_patch(old, new)
    outputs = {out / REL / name: text.encode() for name, text in new.items()}
    outputs[HERE / PATCH_NAME] = patch.encode()
    for path, data in outputs.items():
        if args.check:
            if not path.is_file() or path.read_bytes() != data:
                raise ValueError(f"generated output mismatch: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    print(json.dumps({
        "status": "SOURCE_NORMALIZATION_ONLY_NO_BUILD_OR_RUNTIME_CLAIM",
        "baseline": {name: digest for name, digest in BASE_HASHES.items()},
        "candidate": {name: sha256(text.encode()) for name, text in new.items()},
        "patch": {"name": PATCH_NAME, "sha256": sha256(patch.encode())},
        "planner_sha256": PLANNER_SHA256,
        "flow_sha256": sha256((HERE / "ib_data_flow.inc").read_bytes()),
        "old_files_normalize_exactly": True,
        "direct_sampler_normalizes_exactly": True,
        "borrowed_page_helper_normalizes_exactly": True,
        "check_only": args.check,
    }, indent=2))


if __name__ == "__main__":
    main()
