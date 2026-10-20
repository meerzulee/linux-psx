#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Flatten the frozen CREATE candidate plus one fixed-reference frame leaf."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path

from memory_transform import transform as memory_transform

HERE = Path(__file__).resolve().parent
PREFIX = Path("drivers/gpu/drm/amd/amdgpu")
CREATE = Path("lab/uvd-2026-10-20/message-create/source/attempt-03")
CREATE_RECEIPT_SHA = "ae1f68d18a9a70897f8d969f42f1a1258ec27bf0efc78d2701e56bd0029c32f0"
FIXTURE = Path("lab/uvd-2026-10-20/decode-fixtures/run-001/frame.h264")
FIXTURE_SHA = "7dc73a00614226ee4e7dc222109f6fa7af17a6e03496c0fac3ffb200ee45cb62"
READY = "amdgpu_ps4_uvd_ready.c"

PARAMETER = '''/* DECODE_FRAME_PARAMETER_BEGIN */
/* Immutable, exclusive boot-only one-picture driver bring-up. */
static bool ps4_uvd_decode_frame;
module_param_named(ps4_uvd_decode_frame, ps4_uvd_decode_frame, bool, 0444);
MODULE_PARM_DESC(ps4_uvd_decode_frame,
	"One PS4 UVD CREATE/DECODE/DESTROY reference picture; requires ready+poll_state, excludes all older probes, always parks (default 0)");

bool amdgpu_ps4_uvd_decode_frame_requested(void)
{
	return ps4_uvd_decode_frame;
}

bool amdgpu_ps4_uvd_decode_frame_options_valid(struct amdgpu_device *adev)
{
	return ps4_uvd_ready && ps4_uvd_poll_state && !ps4_uvd_token &&
		!ps4_uvd_data_control && !ps4_uvd_status_observation &&
		!ps4_uvd_ib_data && !ps4_uvd_message_create &&
		!amdgpu_ps4_live_prepare_enabled(adev) &&
		amdgpu_ps4_uvd_ready_enabled(adev);
}
/* DECODE_FRAME_PARAMETER_END */

'''

EARLY = '''	/* DECODE_FRAME_EARLY_BEGIN */
	if (ps4_uvd_decode_frame &&
	    (!amdgpu_ps4_uvd_decode_frame_options_valid(adev) ||
	     !ps4_frame_descriptors_valid(rb, ib, pd_addr)))
		ps4_ready_park(adev, "FRAME_DESCRIPTOR_OR_GATES", -EINVAL);
	/* DECODE_FRAME_EARLY_END */
'''

DISPATCH = '''		/* DECODE_FRAME_DISPATCH_BEGIN */
		if (ps4_uvd_decode_frame) {
			struct ps4_frame_result frame_result;

			frame_result = ps4_frame_run(adev, rb, ib, pd_addr,
				cache_vmid_expected, baseline_fault, reset_count);
			ps4_frame_report(adev);
			ps4_ready_park(adev, frame_result.name, frame_result.error);
		}
		/* DECODE_FRAME_DISPATCH_END */
'''

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("nonunique source anchor: " + repr(old[:100]))
    return text.replace(old, new, 1)

def fragment(text, begin, end):
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError("nonunique source block")
    return text[text.index(begin):text.index(end) + len(end)]

def transform(source):
    out = dict(source)
    text = source[READY].decode()
    text = once(text, " * Private, boot-only Liverpool UVD VCPU readiness hypothesis. Not a decoder.",
                " * Private boot-only Liverpool UVD startup and fixed-reference frame driver.")
    # The older CREATE parameter precedes this gate because the exclusive
    # predicate must inspect raw flags, not filtered enabled() results.
    text = once(text, "/* MESSAGE_CREATE_PARAMETER_END */\n\n",
                "/* MESSAGE_CREATE_PARAMETER_END */\n\n" + PARAMETER)
    text = once(text, "ps4_uvd_ib_data || ps4_uvd_message_create;",
                "ps4_uvd_ib_data || ps4_uvd_message_create || ps4_uvd_decode_frame;")
    text = once(text, "#include <linux/crc32.h>\n",
                "#include <linux/crc32.h>\n#include <crypto/sha2.h>\n")
    text = once(text, '#include "amdgpu_ps4_uvd_ready.h"\n',
                '#include "amdgpu_ps4_uvd_ready.h"\n#include "amdgpu_ps4_uvd_frame_fixture.h"\n')
    sampler = fragment(text, "/* IB_DATA_CORE_SAMPLE_BEGIN", "/* IB_DATA_CORE_SAMPLE_END */")
    sampler = sampler.replace("ps4_ib_data_core_sample", "ps4_frame_core_sample").replace(
        "IB_DATA_CORE_SAMPLE", "FRAME_CORE_SAMPLE").replace(
        "u32 fault_base, int reset_base)", "u32 fault_base, int reset_base, u32 published_dw)")
    sampler = sampler.replace("PS4_UVD_SUBMIT_DWORDS", "published_dw")
    sampler = once(sampler, "\t*s = (struct ps4_token_sample) { 0 };",
        "\tu32 ib_low, expected_ib_low = lower_32_bits(PS4_READY_IB_VA);\n"
        "\tu32 previous_ib_low = expected_ib_low;\n\n"
        "\t/* Main PACKET0 IB_LOW/HIGH writes change the same BARs sampled\n"
        "\t * below. Each append owns its distinct IB. While in flight the\n"
        "\t * previous/current low is admissible; a consumed whole portion\n"
        "\t * must expose the current address. All addresses share high3.\n"
        "\t */\n"
        "\tif (phase == PS4_TOKEN_SUBMITTED && published_dw >= 32) {\n"
        "\t\texpected_ib_low += published_dw == 32 ? 0x80U : 0x100U;\n"
        "\t\tprevious_ib_low = expected_ib_low - 0x80U;\n"
        "\t}\n"
        "\t*s = (struct ps4_token_sample) { 0 };")
    sampler = once(sampler,
        "\tTOKEN_REG(mmUVD_LMI_RBC_IB_64BIT_BAR_LOW, ~0U, lower_32_bits(PS4_READY_IB_VA));",
        "\tib_low = RREG32(mmUVD_LMI_RBC_IB_64BIT_BAR_LOW);\n"
        "\tif (ib_low != expected_ib_low &&\n"
        "\t    (phase != PS4_TOKEN_SUBMITTED || s->rptr == published_dw ||\n"
        "\t     ib_low != previous_ib_low))\n"
        "\t\treturn ps4_token_expect(s, 0, mmUVD_LMI_RBC_IB_64BIT_BAR_LOW,\n"
        "\t\t\tib_low, ~0U, expected_ib_low);")
    # Reuse the actual zero/saturation/all16-VMID captured-history predicate.
    start = text.index("static bool ps4_create_history(")
    end = text.index("\nstatic u32 ps4_create_word(", start)
    history = text[start:end].replace("ps4_create_history", "ps4_frame_history").replace(
        "amdgpu_ps4_uvd_create_operation", "amdgpu_ps4_uvd_frame_operation")
    text = once(text, "/* READY_CORE_FUNCTIONS_BEGIN */", sampler + "\n" + history + "\n" +
                (HERE / "flow.c.inc").read_text() + "\n/* READY_CORE_FUNCTIONS_BEGIN */")
    text = once(text, "\top->boot_claimed = true;\n", "\top->boot_claimed = true;\n" + EARLY)
    text = once(text, "\t\t/* MESSAGE_CREATE_DISPATCH_BEGIN */", DISPATCH +
                "\t\t/* MESSAGE_CREATE_DISPATCH_BEGIN */")
    out[READY] = text.encode()
    out["amdgpu_ps4_map_canary.c"] = memory_transform(source["amdgpu_ps4_map_canary.c"])
    text = source["amdgpu_ps4_uvd_ready.h"].decode()
    text = once(text, "bool amdgpu_ps4_uvd_message_create_requested(void);",
                "bool amdgpu_ps4_uvd_decode_frame_requested(void);\n"
                "bool amdgpu_ps4_uvd_decode_frame_options_valid(struct amdgpu_device *adev);\n"
                "bool amdgpu_ps4_uvd_message_create_requested(void);")
    out["amdgpu_ps4_uvd_ready.h"] = text.encode()
    text = source["amdgpu.h"].decode()
    text = once(text, '#include "amdgpu_ps4_uvd_message_create.h"\n',
                '#include "amdgpu_ps4_uvd_message_create.h"\n#include "amdgpu_ps4_uvd_frame.h"\n')
    text = once(text, "\tstruct amdgpu_ps4_uvd_create_operation ps4_uvd_create_operation;\n",
                "\tstruct amdgpu_ps4_uvd_create_operation ps4_uvd_create_operation;\n"
                "\tstruct amdgpu_ps4_uvd_frame_operation ps4_uvd_frame_operation;\n")
    out["amdgpu.h"] = text.encode()
    out["Makefile"] = once(source["Makefile"].decode(),
        "amdgpu-y += ps4_uvd_message.o ps4_uvd_message_job.o\n",
        "amdgpu-y += ps4_uvd_message.o ps4_uvd_message_job.o ps4_uvd_decode_message.o\n").encode()
    out["amdgpu_ps4_uvd_frame.h"] = (HERE / "operation.h").read_bytes()
    for name in ("ps4_uvd_decode_message.c", "ps4_uvd_decode_message.h"):
        out[name] = (HERE.parent / "ps4-uvd-decode-message" / name).read_bytes()
    return out

def build(workspace, output):
    if output.exists():
        raise ValueError("fresh output required; preserve prior candidate")
    raw = (workspace / CREATE / "SOURCE-RECEIPT.json").read_bytes()
    if sha(raw) != CREATE_RECEIPT_SHA:
        raise ValueError("frozen CREATE receipt mismatch")
    receipt = json.loads(raw)
    categories = {}
    for category, key in (("baseline", "baseline_files"), ("candidate", "candidate_files")):
        categories[category] = {}
        for name, expected in receipt[key].items():
            raw = (workspace / CREATE / category / PREFIX / name).read_bytes()
            if sha(raw) != expected:
                raise ValueError("CREATE source input changed: " + name)
            categories[category][name] = raw
    source = categories["candidate"]
    candidate = transform(source)
    fixture = (workspace / FIXTURE).read_bytes()
    if sha(fixture) != FIXTURE_SHA or len(fixture) != 2905:
        raise ValueError("generated fixture identity differs")
    header = ("/* SPDX-License-Identifier: MIT */\n"
              "/* Locally generated testsrc one-IDR64x64 H.264 fixture, not firmware.\n"
              " * SHA256 " + FIXTURE_SHA + ".\n */\n"
              "static const u8 ps4_frame_fixture[2905] = {\n")
    header += "".join("\t" + ", ".join("0x%02x" % b for b in fixture[i:i+16]) + ",\n"
                      for i in range(0, len(fixture), 16)) + "};\n"
    candidate["amdgpu_ps4_uvd_frame_fixture.h"] = header.encode()
    baseline = categories["baseline"]
    output.mkdir(parents=True)
    for category, files in (("baseline", baseline), ("candidate", candidate)):
        for name, raw in files.items():
            p = output / category / PREFIX / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
    changes = sorted(n for n in baseline.keys() | candidate.keys() if baseline.get(n) != candidate.get(n))
    patch = ("From: Linux psX development <linux-psx@localhost>\n"
             "Subject: [EXPERIMENT] drm/amdgpu: one reference AVC frame through Liverpool UVD\n\n"
             "Add an exclusive default-off fixed-picture CREATE/DECODE/DESTROY leaf.\n"
             "Preserve the older CREATE leaf and startup, append only after observed\n"
             "phase completion, compare visible NV12 bytes and retain all mappings.\n"
             "No generic codec endpoint or DMA quiescence is claimed. Serializer and\n"
             "packet adaptations retain upstream notices. No firmware is embedded.\n\n")
    for name in changes:
        path = str(PREFIX / name)
        patch += f"diff --git a/{path} b/{path}\n"
        if name not in baseline:
            patch += "new file mode 100644\n"
        patch += "".join(difflib.unified_diff(baseline.get(name, b"").decode().splitlines(True),
            candidate[name].decode().splitlines(True),
            fromfile="a/" + path if name in baseline else "/dev/null", tofile="b/" + path))
    patch_name = "0014-drm-amdgpu-ps4-reference-avc-frame.patch"
    (output / patch_name).write_text(patch)
    result = {
        "schema": 1, "status": "SOURCE_CANDIDATE_ONLY", "hardware_acceptance": False,
        "create_receipt_sha256": CREATE_RECEIPT_SHA, "fixture_sha256": FIXTURE_SHA,
        "generator_sha256": sha(Path(__file__).read_bytes()),
        "generator_inputs": {p.name: sha(p.read_bytes()) for p in
            (HERE / "flow.c.inc", HERE / "operation.h", HERE / "memory_transform.py",
             HERE.parent / "ps4-uvd-decode-message/ps4_uvd_decode_message.c",
             HERE.parent / "ps4-uvd-decode-message/ps4_uvd_decode_message.h")},
        "baseline_files": {n: sha(v) for n, v in baseline.items()},
        "candidate_files": {n: sha(v) for n, v in candidate.items()}, "changed_files": changes,
        "patch": {"path": patch_name, "sha256": sha(patch.encode())},
        "limits": ["one boot, one handle, one fixed picture, retained mappings",
                   "startup and transport are Liverpool working assumptions",
                   "decoded pixels require real-hardware observation",
                   "no normal DRM/VAAPI codec endpoint, DMA stop/free/reset/rearm"],
    }
    (output / "SOURCE-RECEIPT.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    print(json.dumps(build(args.workspace.resolve(), args.output.resolve()), indent=2))
