#!/usr/bin/env python3
"""Generate an unstaged preparation-only candidate from exact IB-DATA inputs.

No checkout, build, installation, SSH, UART or hardware operation is performed.
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
PATCH_NAME = "0011-drm-amdgpu-ps4-live-prepare-only.patch"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"expected one anchor, found {text.count(old)}: {old!r}")
    return text.replace(old, new, 1)


def candidate(inputs: dict[str, str]) -> dict[str, str]:
    out = dict(inputs)
    name = "amdgpu_ps4_map_canary.c"
    s = out[name]
    s = once(s, "#include <linux/crc32.h>\n",
             "#include <linux/crc32.h>\n#include <linux/ktime.h>\n")
    s = once(s, "\tbool ready_mode;\n", "\tbool ready_mode;\n"
             "\tbool ready_layout;\n\tbool prepare_only;\n")
    s = once(s, "/* Retained ownership also makes timeout resources discoverable in a dump. */",
             (HERE / "live_prepare.c.inc").read_text() + "\n"
             "/* Original parked boot probes keep their legacy retained owner. */")
    s = once(s, "return (ps4_map_canary || amdgpu_ps4_uvd_ready_enabled(adev)) &&",
             "return (ps4_map_canary || amdgpu_ps4_uvd_ready_enabled(adev) ||\n"
             "\t\tamdgpu_ps4_live_prepare_enabled(adev)) &&")
    s = once(s, "{\n\tdev_err(adev->dev,\n\t\t\"PS4_MAP_CANARY FAIL",
             "{\n\tps4_live_prepare_failure(adev, error);\n"
             "\tdev_err(adev->dev,\n\t\t\"PS4_MAP_CANARY FAIL")
    s = once(s, "u64 span = s->ready_mode ? SZ_4M", "u64 span = s->ready_layout ? SZ_4M")
    s = once(s, "\tif (atomic_cmpxchg(&ps4_canary_attempted, 0, 1))\n"
             "\t\tps4_canary_park(adev, \"second-init-refused\", -EALREADY);",
             "\tif (amdgpu_ps4_live_prepare_enabled(adev)) {\n"
             "\t\tif (ps4_map_canary || amdgpu_ps4_uvd_boot_probe_requested())\n"
             "\t\t\tps4_canary_park(adev, \"live-prepare-conflicting-gates\", -EINVAL);\n"
             "\t\tif (IS_ENABLED(CONFIG_PM) || !IS_ENABLED(CONFIG_DEBUG_FS))\n"
             "\t\t\tps4_canary_park(adev, \"live-prepare-config\", -EINVAL);\n"
             "\t\tr = ps4_live_prepare_begin(adev);\n"
             "\t\tif (r)\n"
             "\t\t\tps4_canary_park(adev, \"live-prepare-begin\", r);\n"
             "\t} else if (atomic_cmpxchg(&ps4_canary_attempted, 0, 1)) {\n"
             "\t\tps4_canary_park(adev, \"second-init-refused\", -EALREADY);\n\t}")
    s = once(s, "\tps4_canary_retained = s;\n\ts->adev = adev;\n"
             "\ts->ready_mode = amdgpu_ps4_uvd_ready_enabled(adev);\n"
             "\ts->src_va = PS4_CANARY_VA + (s->ready_mode ?",
             "\ts->adev = adev;\n"
             "\ts->ready_mode = amdgpu_ps4_uvd_ready_enabled(adev);\n"
             "\ts->prepare_only = amdgpu_ps4_live_prepare_enabled(adev);\n"
             "\ts->ready_layout = s->ready_mode || s->prepare_only;\n"
             "\tif (s->prepare_only)\n\t\tps4_live_prepare_attach(adev, s);\n"
             "\telse\n\t\tps4_canary_retained = s;\n"
             "\ts->src_va = PS4_CANARY_VA + (s->ready_layout ?")
    if s.count("if (!r && s->ready_mode)") != 3:
        raise ValueError("ready-layout allocation anchors changed")
    s = s.replace("if (!r && s->ready_mode)", "if (!r && s->ready_layout)")
    s = once(s, "if (s->ready_mode) {\n\t\tr = ps4_canary_ready_pages",
             "if (s->ready_layout) {\n\t\tr = ps4_canary_ready_pages")
    s = once(s, "if (s->ready_mode) {\n\t\tfor (i = 0; i < s->mapped_pages; ++i)",
             "if (s->ready_layout) {\n\t\tfor (i = 0; i < s->mapped_pages; ++i)")
    s = once(s, "\tcrc = crc32_le(~0U, (u8 *)dst, PS4_CANARY_BYTES) ^ ~0U;\n",
             "\tcrc = crc32_le(~0U, (u8 *)dst, PS4_CANARY_BYTES) ^ ~0U;\n"
             "\tif (s->prepare_only) {\n"
             "\t\tr = ps4_canary_exclusive_init(adev);\n"
             "\t\tif (r)\n\t\t\tps4_canary_park(adev, \"pre-prepare-exclusive-init\", r);\n"
             "\t\ts->passed = true;\n"
             "\t\tr = ps4_live_prepare_publish(adev, s, crc);\n"
             "\t\tif (r)\n\t\t\tps4_canary_park(adev, \"live-prepare-publish\", r);\n"
             "\t\tdev_info(adev->dev,\n"
             "\t\t\t \"PS4_LIVE_PREPARE PREPARED vmid=4 pages=%u copy_crc32=%08x historical_sdma_proof=1 retained=1 UVD_setup=never arm=absent; continuing normal initialization\\n\",\n"
             "\t\t\t s->mapped_pages, crc);\n\t\treturn;\n\t}\n")
    s = once(s, " * Even on success, the small pinned allocation and fence are retained until\n"
             " * poweroff. There is deliberately no runtime trigger or teardown interface.\n",
             " * Original boot probes retain their allocations and fences until poweroff.\n"
             " * The separate preparation-only mode retains the full owned layout but\n"
             " * returns without calling UVD. It has a cached read-only status and an\n"
             " * ordinary preparation-owner destructor, never a UVD drain/rearm API.\n")
    out[name] = s

    name = "amdgpu_ps4_uvd_ready.c"
    out[name] = once(out[name], "bool amdgpu_ps4_uvd_ready_requested(void)\n",
                    "/* A preparation-only boot rejects every legacy probe switch. */\n"
                    "bool amdgpu_ps4_uvd_boot_probe_requested(void)\n{\n"
                    "\treturn ps4_uvd_ready || ps4_uvd_poll_state || ps4_uvd_token ||\n"
                    "\t\tps4_uvd_data_control || ps4_uvd_status_observation ||\n"
                    "\t\tps4_uvd_ib_data;\n}\n\n"
                    "bool amdgpu_ps4_uvd_ready_requested(void)\n")
    name = "amdgpu_ps4_uvd_ready.h"
    out[name] = once(out[name], "bool amdgpu_ps4_uvd_ready_requested(void);",
                    "bool amdgpu_ps4_uvd_boot_probe_requested(void);\n"
                    "bool amdgpu_ps4_uvd_ready_requested(void);")
    name = "amdgpu_ps4_map_canary.h"
    out[name] = once(out[name], "/* Returns only for gate-off or the original copy-only mode's valid PASS.\n",
                    "/* Returns for gate-off, original copy-only PASS or preparation-only PASS.\n")

    name = "amdgpu.h"
    out[name] = once(out[name], '#include "amdgpu_ps4_uvd_irq.h"',
                    '#include "amdgpu_ps4_uvd_irq.h"\n#include "amdgpu_ps4_live_prepare.h"')
    out[name] = once(out[name], "\tstruct ps4_uvd_irq_observer ps4_uvd_irq;",
                    "\tstruct ps4_uvd_irq_observer ps4_uvd_irq;\n"
                    "\tstruct amdgpu_ps4_live_prepare ps4_live_prepare;\n"
                    "\tbool ps4_canary_fences_initialized;")
    out["amdgpu_ps4_live_prepare.h"] = (HERE / "live_prepare.h").read_text()

    name = "amdgpu_ids.c"
    out[name] = once(out[name], "\t\t\t\tINIT_LIST_HEAD(&id_mgr->ids[j].list);\n\t\t\t\tcontinue;",
                    "\t\t\t\tINIT_LIST_HEAD(&id_mgr->ids[j].list);\n"
                    "\t\t\t\tamdgpu_ps4_live_prepare_reserved(adev);\n\t\t\t\tcontinue;")
    out[name] = once(out[name], "void amdgpu_vmid_mgr_fini(struct amdgpu_device *adev)\n{\n\tunsigned i, j;\n",
                    "void amdgpu_vmid_mgr_fini(struct amdgpu_device *adev)\n{\n\tunsigned i, j;\n\n"
                    "\tamdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_VMID_FINI);\n")

    name = "amdgpu_device.c"
    s = out[name]
    s = once(s, "\t\tamdgpu_fence_driver_hw_init(adev);\n\t\tamdgpu_ps4_map_canary_run(adev);",
             "\t\tif (!adev->ps4_canary_fences_initialized) {\n"
             "\t\t\tamdgpu_fence_driver_hw_init(adev);\n"
             "\t\t\tadev->ps4_canary_fences_initialized = true;\n\t\t}\n"
             "\t\tamdgpu_ps4_map_canary_run(adev);")
    s = once(s, "if (!amdgpu_ps4_map_canary_completed(adev))",
             "if (!adev->ps4_canary_fences_initialized)")
    s = once(s, "\tadev->usec_timeout = AMDGPU_MAX_USEC_TIMEOUT;",
             "\tamdgpu_ps4_live_prepare_init(adev);\n\n"
             "\tadev->usec_timeout = AMDGPU_MAX_USEC_TIMEOUT;")
    s = once(s, "void amdgpu_device_fini_hw(struct amdgpu_device *adev)\n{\n",
             "void amdgpu_device_fini_hw(struct amdgpu_device *adev)\n{\n"
             "\tamdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_HW_FINI);\n")
    s = once(s, "\tamdgpu_device_ip_fini_early(adev);\n\n\tamdgpu_irq_fini_hw(adev);",
             "\tamdgpu_device_ip_fini_early(adev);\n\n"
             "\t/* Only completed SDMA preparation owners; no UVD was started. */\n"
             "\tamdgpu_ps4_live_prepare_fini(adev);\n\n\tamdgpu_irq_fini_hw(adev);")
    s = once(s, "\t/* Evict the majority of BOs before starting suspend sequence */",
             "\tamdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_SUSPEND);\n\n"
             "\t/* Evict the majority of BOs before starting suspend sequence */")
    s = once(s, "\tadev->in_suspend = true;\n",
             "\tamdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_SUSPEND);\n"
             "\tadev->in_suspend = true;\n")
    s = once(s, "\tif (reset_context->reset_req_dev == adev)\n\t\tjob = reset_context->job;",
             "\tamdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_RESET);\n\n"
             "\tif (reset_context->reset_req_dev == adev)\n\t\tjob = reset_context->job;")
    s = once(s, "\t/*\n\t * If it reaches here because of hang/timeout and a RAS error is",
             "\tamdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_RESET);\n\n"
             "\t/*\n\t * If it reaches here because of hang/timeout and a RAS error is")
    out[name] = s

    name = "gmc_v7_0.c"
    out[name] = once(out[name], "\tamdgpu_gtt_mgr_recover(&adev->mman.gtt_mgr);",
                    "\tamdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_GART_ENABLE);\n"
                    "\tamdgpu_gtt_mgr_recover(&adev->mman.gtt_mgr);")
    out[name] = once(out[name], "\t/* Disable all tables */",
                    "\tamdgpu_ps4_live_prepare_invalidate(adev, PS4_LIVE_EVENT_GART_DISABLE);\n"
                    "\t/* Disable all tables */")

    name = "amdgpu_drv.c"
    s = out[name]
    s = once(s, "\tint ret, retry = 0, i;\n\tbool supports_atomic = false;",
             "\tint ret, retry = 0, i;\n\tbool supports_atomic = false;\n"
             "\tbool ps4_prepare_loaded = false;\n"
             "\tbool ps4_prepare_registered = false;")
    s = once(s, "\tret = amdgpu_driver_load_kms(adev, flags);\n"
             "\tif (ret)\n\t\tgoto err_pci;\n\nretry_init:",
             "\tret = amdgpu_driver_load_kms(adev, flags);\n"
             "\tif (ret)\n\t\tgoto err_pci;\n"
             "\t/* LIVE_PREPARE_PROBE_LOADED_BEGIN */\n"
             "\tps4_prepare_loaded = true;\n"
             "\t/* LIVE_PREPARE_PROBE_LOADED_END */\n\nretry_init:")
    s = once(s, "\t} else if (ret) {\n\t\tgoto err_pci;\n\t}\n\n"
             "\tret = amdgpu_xcp_dev_register(adev, ent);",
             "\t} else if (ret) {\n\t\tgoto err_pci;\n\t}\n"
             "\t/* LIVE_PREPARE_PROBE_REGISTERED_BEGIN */\n"
             "\tps4_prepare_registered = true;\n"
             "\t/* LIVE_PREPARE_PROBE_REGISTERED_END */\n\n"
             "\tret = amdgpu_xcp_dev_register(adev, ent);")
    s = once(s, "err_pci:\n\tpci_disable_device(pdev);\n\treturn ret;\n",
             "err_pci:\n"
             "\t/* LIVE_PREPARE_PROBE_ERROR_BEGIN */\n"
             "\t/* A failed load already unloads internally. Only terminal errors\n"
             "\t * after a successful load need this preparation-only cleanup.\n"
             "\t * Do not defer hw_fini to DRM final release: PCI/devres mappings\n"
             "\t * may disappear while open DRM references still exist.\n\t */\n"
             "\tif (ps4_prepare_loaded && amdgpu_ps4_live_prepare_enabled(adev)) {\n"
             "\t\tif (ps4_prepare_registered)\n"
             "\t\t\tdrm_dev_unplug(ddev);\n"
             "\t\tamdgpu_driver_unload_kms(ddev);\n\t}\n"
             "\t/* LIVE_PREPARE_PROBE_ERROR_END */\n"
             "\tpci_disable_device(pdev);\n\treturn ret;\n")
    out[name] = s

    name = "amdgpu_debugfs.c"
    out[name] = once(out[name], "#if defined(CONFIG_DEBUG_FS)\n",
                    "#if defined(CONFIG_DEBUG_FS)\n#include <drm/drm_drv.h>\n#include <generated/utsrelease.h>\n\n"
                    + (HERE / "debugfs.c.inc").read_text() + "\n")
    out[name] = once(out[name], "\tdebugfs_create_x32(\"amdgpu_smu_debug\", 0600, root,",
                    "\tif (amdgpu_ps4_live_prepare_enabled(adev))\n"
                    "\t\tdebugfs_create_file(\"amdgpu_ps4_live_prepare\", 0400, root, adev,\n"
                    "\t\t\t\t    &amdgpu_ps4_live_prepare_fops);\n\n"
                    "\tdebugfs_create_x32(\"amdgpu_smu_debug\", 0600, root,")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=WORKSPACE / "lab/uvd-2026-10-20/live-prepare/source/baseline")
    parser.add_argument("--output", type=Path, required=True, help="new output directory; never an active source tree")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("output already exists; preserve it and choose a new attempt")
    pins = json.loads((HERE / "baseline-pins.json").read_text())
    inputs = {}
    for name, expected in pins["files"].items():
        data = (args.baseline / REL / name).read_bytes()
        if digest(data) != expected:
            raise SystemExit(f"baseline hash mismatch: {name}")
        inputs[name] = data.decode()
    outputs = candidate(inputs)
    patch = ("From: Linux psX contributors\n"
             "Subject: [EXPERIMENTAL] drm/amdgpu: PS4 preparation-only live-development owner\n\n"
             "Adapt the frozen IB-DATA candidate, retaining all upstream notices.\n"
             "Reserve and prove the owned mapping before clients; return without\n"
             "calling UVD. Add cached status and one-way lifecycle invalidation.\n"
             "Cleanup applies only to completed SDMA preparation, never UVD.\n"
             "No active series, kernel build or hardware qualification is included.\n"
             "Baseline files are pinned individually in baseline-pins.json.\n\n")
    changed = []
    for name in sorted(outputs):
        text = outputs[name]
        target = args.output / "candidate" / REL / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        before = inputs.get(name, "")
        if text == before:
            continue
        changed.append(name)
        rel = (REL / name).as_posix()
        patch += f"diff --git a/{rel} b/{rel}\n"
        if name not in inputs:
            patch += "new file mode 100644\n"
        patch += "".join(difflib.unified_diff(before.splitlines(True), text.splitlines(True),
                                           fromfile=f"a/{rel}" if name in inputs else "/dev/null",
                                           tofile=f"b/{rel}"))
    (args.output / PATCH_NAME).write_text(patch)
    receipt = {
        "schema": 1, "status": "UNSTAGED_SOURCE_CANDIDATE_NOT_KERNEL_BUILT",
        "baseline_files": pins["files"], "changed_files": changed,
        "candidate_files": {n: digest(s.encode()) for n, s in sorted(outputs.items())},
        "patch": {"file": PATCH_NAME, "sha256": digest(patch.encode())},
        "generator_sha256": digest(Path(__file__).read_bytes()),
        "generator_inputs": {n: digest((HERE / n).read_bytes()) for n in
                             ("baseline-pins.json", "live_prepare.h",
                              "live_prepare.c.inc", "debugfs.c.inc")},
        "kernel_built": False, "hardware_tested": False, "uvd_arm_implemented": False,
    }
    (args.output / "SOURCE-RECEIPT.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
