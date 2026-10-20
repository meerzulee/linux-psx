#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the actual fixed-reference CPU component against pinned public sources."""
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
import tarfile
import traceback

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[2]
PUBLIC = Path("lab/uvd-driver-goal-2026-10-20")
MESA = PUBLIC / "public-mesa/source-26.1.7/src/amd/common"
FIXTURE = Path("lab/uvd-2026-10-20/decode-fixtures/run-001")
INPUTS = {
    "mesa.c": (MESA / "ac_uvd_dec.c", "f9ace763a478dba1264cece9af0011065e8ded5fdf5f8e41c95d3acd2d843870"),
    "mesa.h": (MESA / "ac_uvd_dec.h", "fde6080faf355b00d373f8f72e109461f1072b6290d17f93fe8d5e1bb58842e1"),
    "video-dec.h": (MESA / "ac_video_dec.h", "f7d549dcd423cea1c37db326ad2c1a423627a2077c6df6e1463538a80042fb9a"),
    "amd_family.h": (MESA / "amd_family.h", "759810fcee5b8fdeae5ff61ccf47604cb13dba2b81cadfae636b10aa3ddff480"),
    "uvd_v5_0.c": (PUBLIC / "public-path/upstream/uvd_v5_0.c", "7925b5f1488191041f3b58a115175b2100ffaf77e79fd6cbd8cac25630ce0543"),
    "registers.h": (Path("lab/uvd-2026-10-20/driver-integration/native-control-check/runs/native-02/source/drivers/gpu/drm/amd/include/asic_reg/uvd/uvd_5_0_d.h"), "ddee9d3d00fa475cd3f55813edcfb1955febc33300520483d38a0c017674faee"),
    "frame.h264": (FIXTURE / "frame.h264", "7dc73a00614226ee4e7dc222109f6fa7af17a6e03496c0fac3ffb200ee45cb62"),
    "expected.nv12": (FIXTURE / "expected.nv12", "12404191ab945b7478093a852545adfbaa9af44bd32f83ccf24f489e385628df"),
    "headers.stderr.txt": (FIXTURE / "headers.stderr.txt", "afe92ea54b51ada196d8f30e88f59b543192b843f45de4d3d1172953e38ae3f5"),
}
ARCHIVE = PUBLIC / "public-mesa/mesa-26.1.7.tar.xz"
ARCHIVE_SHA = "25e0a669e6638c3563e7be32a0a09f1888317e6eed0d047dc41d49dc8de26c7d"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save_json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def run(command, output, label, env=None):
    proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=30, check=False, env=env)
    (output / (label + ".stdout.txt")).write_bytes(proc.stdout)
    (output / (label + ".stderr.txt")).write_bytes(proc.stderr)
    save_json(output / (label + ".json"), {"argv": command, "returncode": proc.returncode})
    return proc


def require(proc, label):
    if proc.returncode:
        raise ValueError(label + " failed; exact output preserved")


def braced(text, marker, semicolon=False):
    start = text.index(marker)
    opening = text.index("{", start)
    depth = 0
    for end in range(opening, len(text)):
        depth += (text[end] == "{") - (text[end] == "}")
        if not depth:
            if semicolon and text[end + 1] != ";":
                raise ValueError("expected trailing semicolon: " + marker)
            return text[start:end + (2 if semicolon else 1)]
    raise ValueError("unclosed source fragment: " + marker)


def function(text, name):
    pattern = r"(?:static (?:void|int|uint32_t)\n|static void |static int )" + re.escape(name) + r"\("
    found = re.search(pattern, text)
    if not found:
        raise ValueError("missing exact function: " + name)
    return braced(text, found.group(0))


def reference(inputs, video_h, output):
    mesa, video, linux = (inputs[k].decode() for k in ("mesa.c", "video-dec.h", "uvd_v5_0.c"))
    fragments = {name: function(mesa, name) for name in
                 ("send_cmd", "build_avc_msg", "uvd_build_decode_cmd")}
    avc = braced(video, "struct ac_video_dec_avc {", True)
    decoder = braced(mesa, "struct ac_uvd_decoder {", True)
    command = braced(mesa, "struct cmd_buffer {", True)
    codec_enum = braced(video_h, "enum ac_video_codec {", True)
    macros = "\n".join(re.findall(r"^#define H264_[^\n]+", video, re.M))
    notice = mesa.split("#include", 1)[0] + video.split("#ifndef", 1)[0]
    header = notice + r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "reference/ac_uvd_dec.h"
#include "reference/amd_family.h"
#define align(v, a) (((v) + (a) - 1) & ~((a) - 1))
#define DIV_ROUND_UP(v, a) (((v) + (a) - 1) / (a))
struct ac_cmdbuf { uint32_t *buf; uint32_t max_dw, cdw; };
#define ac_cmdbuf_begin(p) struct ac_cmdbuf *reference_cs = (p)
#define ac_cmdbuf_emit(v) do { assert(reference_cs->cdw < reference_cs->max_dw); \
   reference_cs->buf[reference_cs->cdw++] = (uint32_t)(v); } while (0)
#define ac_cmdbuf_end() do {} while (0)
''' + macros + "\n" + codec_enum + "\n" + avc + r'''
/* Host wrappers carry only fields used by the extracted functions. */
struct ac_video_dec {
   struct { enum ac_video_codec codec; } param;
   uint32_t max_decode_cmd_dw;
};
struct radeon_surf {
   uint32_t blk_w;
   union {
      struct { struct { uint32_t nblk_x; } level[1]; } legacy;
      struct { uint32_t surf_pitch, swizzle_mode; } gfx9;
   } u;
};
struct reference_surface {
   uint64_t size;
   struct { uint64_t va; struct radeon_surf *surf; } planes[2];
};
struct ac_video_dec_decode_cmd {
   uint32_t *cmd_buffer;
   void *embedded_ptr;
   uint64_t embedded_va, session_va, bitstream_va;
   uint32_t width, height, bitstream_size;
   struct reference_surface ref_surfaces[1], decode_surface;
   union { struct ac_video_dec_avc avc; struct { uint32_t profile; } vc1; } codec_param;
   struct { uint32_t cmd_dw; } out;
};
''' + decoder + "\n" + command + "\n" + fragments["send_cmd"] + "\n" + fragments["build_avc_msg"]
    for kind in ("hevc", "mpeg2", "vc1"):
        type_name = {"hevc": "h265", "mpeg2": "mpeg2", "vc1": "vc1"}[kind]
        header += f'''
static void build_{kind}_msg(struct cmd_buffer *b, struct ac_video_dec_decode_cmd *c,
                            struct ruvd_{type_name} *p)
{{ (void)b; (void)c; (void)p; abort(); /* Unsupported reference branch. */ }}
'''
    header += "\n" + fragments["uvd_build_decode_cmd"] + "\n"
    registers = inputs["registers.h"].decode()
    names = ("mmUVD_CONTEXT_ID", "mmUVD_GPCOM_VCPU_DATA0", "mmUVD_GPCOM_VCPU_DATA1",
             "mmUVD_GPCOM_VCPU_CMD", "mmUVD_LMI_RBC_IB_64BIT_BAR_LOW",
             "mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH", "mmUVD_RBC_IB_SIZE")
    for name in names:
        match = re.search(r"^#define\s+" + name + r"\s+0x[0-9a-fA-F]+\s*$", registers, re.M)
        if not match:
            raise ValueError("register definition not found: " + name)
        header += match.group(0) + "\n"
    header += linux.split("#include", 1)[0] + r'''
#define PACKET0(reg, n) (((reg) & 0xffffU) | ((n) << 16))
#define upper_32_bits(value) ((uint32_t)((value) >> 32))
#define lower_32_bits(value) ((uint32_t)(value))
#define AMDGPU_FENCE_FLAG_64BIT 1
#define WARN_ON(c) assert(!(c))
struct amdgpu_ring { uint32_t words[64]; unsigned cdw; };
struct amdgpu_ib { uint64_t gpu_addr; uint32_t length_dw; };
static void amdgpu_ring_write(struct amdgpu_ring *ring, uint32_t value)
{ assert(ring->cdw < 64); ring->words[ring->cdw++] = value; }
'''
    for name, signature in (
            ("uvd_v5_0_ring_emit_ib", "static void public_emit_ib(struct amdgpu_ring *ring, struct amdgpu_ib *ib)"),
            ("uvd_v5_0_ring_emit_fence", "static void public_emit_fence(struct amdgpu_ring *ring, uint64_t addr, uint64_t seq)")):
        full = function(linux, name)
        body = full[full.index("{"):]
        fragments[name] = full
        if "fence" in name:
            body = "{\n   unsigned flags = 0;\n" + body[1:]
        header += signature + "\n" + body + "\n"
    (output / "public_reference.h").write_text(header)
    return {"fragments_sha256": {k: sha(v.encode()) for k, v in fragments.items()},
            "header_sha256": sha(header.encode()),
            "wrapper_scope": "Synthetic arrays and source-compatible minimal context fields only; full unmodified Mesa send/AVC/decode bodies execute, unsupported codecs abort; Linux bodies execute with no real ring/device."}


def fixture_parameters(inputs, output):
    trace = inputs["headers.stderr.txt"].decode()
    expected = {"profile_idc": 66, "level_idc": 10, "direct_8x8_inference_flag": 1,
                "frame_mbs_only_flag": 1, "deblocking_filter_control_present_flag": 1,
                "pic_order_cnt_type": 2, "max_num_ref_frames": 0, "pic_init_qp_minus26": -8,
                "log2_max_frame_num_minus4": 0, "entropy_coding_mode_flag": 0,
                "num_slice_groups_minus1": 0, "frame_num": 0,
                "pic_width_in_mbs_minus1": 3, "pic_height_in_map_units_minus1": 3}
    found = {}
    header = "/* Values parsed from separately pinned FFmpeg trace of the original fixture. */\n"
    for name, value in expected.items():
        values = {int(x) for x in re.findall(r"\b" + name + r"\s+[^\n]*?=\s*(-?\d+)\s*$", trace, re.M)}
        if values != {value}:
            raise ValueError("fixture syntax value mismatch: " + name)
        found[name] = value
        header += f"#define FIXTURE_{name.upper()} ({value})\n"
    for name, file_name in (("expected_fixture_sha256", "frame.h264"), ("expected_nv12_sha256", "expected.nv12")):
        header += f"static const uint8_t {name}[32] = {{ "
        header += ", ".join("0x" + sha(inputs[file_name])[i:i + 2] for i in range(0, 64, 2)) + " };\n"
    (output / "fixture_params.h").write_text(header)
    return {"parsed_fields": found, "header_sha256": sha(header.encode()),
            "scaling_defaults": "Resolved flat16 for both arrays; baseline SPS/PPS has no scaling-matrix syntax",
            "idr_slice_sha256": sha(inputs["frame.h264"][591:]),
            "idr_slice_bytes": len(inputs["frame.h264"][591:])}


def check(workspace, output, cc):
    output.mkdir(parents=True, exist_ok=False)
    try:
        sources = {}
        names = ("ps4_uvd_decode_message.c", "ps4_uvd_decode_message.h", "test_decode_message.c", "check.py")
        for name in names:
            sources[name] = (HERE / name).read_bytes()
            (output / name).write_bytes(sources[name])
        dependency = HERE.parent / "ps4-uvd-message-format"
        for name, digest in (("ps4_uvd_message.c", "7cbb584b8a628e2726521e7e28436aa99ed17d7034bf8c0ddf62800cd2df57c2"),
                             ("ps4_uvd_message.h", "95daa65583c4aeb13f39e8ecad62666fd38dc4666b0ba8a3efc71aed1896da97")):
            raw = (dependency / name).read_bytes()
            if sha(raw) != digest:
                raise ValueError("existing serializer changed: " + name)
            (output / name).write_bytes(raw)
        inputs = {}
        (output / "inputs").mkdir()
        for name, (path, digest) in INPUTS.items():
            raw = (workspace / path).read_bytes()
            if sha(raw) != digest:
                raise ValueError("pinned input changed: " + str(path))
            inputs[name] = raw
            (output / "inputs" / name).write_bytes(raw)
        archive = (workspace / ARCHIVE).read_bytes()
        if sha(archive) != ARCHIVE_SHA:
            raise ValueError("pinned Mesa archive changed")
        with tarfile.open(workspace / ARCHIVE) as tar:
            video_h = tar.extractfile("mesa-26.1.7/src/amd/common/ac_video.h").read()
        (output / "inputs/ac_video.h").write_bytes(video_h)
        ref = output / "reference"
        ref.mkdir()
        (ref / "ac_uvd_dec.h").write_bytes(inputs["mesa.h"])
        (ref / "amd_family.h").write_bytes(inputs["amd_family.h"])
        (ref / "ac_video_dec.h").write_text(
            "/* Message header dependency shim: fixed-width and opaque prototype types only. */\n"
            "#include <stdint.h>\nstruct radeon_info;\nstruct ac_video_dec_session_param;\nstruct ac_video_dec;\n")
        shim = output / "shim/linux"
        shim.mkdir(parents=True)
        (shim / "types.h").write_text(
            "#include <stdint.h>\n#include <stddef.h>\n#include <stdbool.h>\n"
            "typedef uint8_t u8;\ntypedef uint32_t u32;\ntypedef uint64_t u64;\n")
        (shim / "string.h").write_text("#include <string.h>\n")
        (shim / "errno.h").write_text("#define EINVAL 22\n#define ERANGE 34\n#define EBADMSG 74\n")
        public_reference = reference(inputs, video_h.decode(), output)
        fixture = fixture_parameters(inputs, output)
        compiler = str(Path(cc).resolve())
        require(run([compiler, "--version"], output, "compiler"), "compiler identity")
        flags = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I" + str(output / "shim"), "-I" + str(output)]
        test = str(output / "test_decode_message.c")
        component = output / "ps4_uvd_decode_message.c"
        existing = str(output / "ps4_uvd_message.c")
        arguments = [str(output / "inputs/frame.h264")]
        optimized = output / "optimized"
        require(run(flags + ["-O2", test, str(component), existing, "-o", str(optimized)], output, "optimized-compile"), "optimized compile")
        first = run([str(optimized)] + arguments, output, "optimized-run")
        require(first, "optimized run")
        sanitized = output / "sanitized"
        require(run(flags + ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                             test, str(component), existing, "-o", str(sanitized)], output, "sanitized-compile"), "sanitized compile")
        second = run([str(sanitized)] + arguments, output, "sanitized-run",
                     dict(os.environ, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1",
                          UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1"))
        require(second, "sanitized run")
        match = re.fullmatch(rb"PASS: (\d+) fixed-reference actual-source cases\n", first.stdout)
        if not match or first.stdout != second.stdout or int(match.group(1)) < 40:
            raise ValueError("actual-source run accounting mismatch")
        mutants = (
            ("avc-level", "page + 228, 10", "page + 228, 11", "AVC_LEVEL"),
            ("command-shift", "command << 1", "command", "PUBLIC_IB_BYTES"),
            ("feedback-length", "dec_le32(out->feedback_le, PS4_UVD_DEC_FEEDBACK_BYTES)", "dec_le32(out->feedback_le, 1024)", "FEEDBACK_LENGTH"),
            ("work-bounds", "in.work_bytes < PS4_UVD_DEC_WORK_MIN_BYTES", "in.work_bytes < PS4_UVD_DEC_PAGE_BYTES", "WORK_SIZE_REJECT"),
            ("fixture-digest", "PS4_UVD_DEC_SHA256_BYTES) ||", "0) ||", "FIXTURE_DIGEST_REJECT"),
        )
        witnesses = []
        for name, old, new, witness in mutants:
            mutated = sources["ps4_uvd_decode_message.c"].decode()
            if mutated.count(old) != 1:
                raise ValueError("mutant anchor count: " + name)
            mutated = mutated.replace(old, new, 1)
            source, binary = output / ("mutant-" + name + ".c"), output / ("mutant-" + name)
            source.write_text(mutated)
            require(run(flags + ["-O2", test, str(source), existing, "-o", str(binary)], output, name + "-compile"), "mutant compile")
            result = run([str(binary)] + arguments, output, name + "-run")
            if result.returncode == 0 or not result.stderr.startswith(("FAIL:" + witness + " line=").encode()):
                raise ValueError("exact mutant witness: " + name)
            witnesses.append({"name": name, "witness": witness, "returncode": result.returncode,
                              "source_sha256": sha(mutated.encode()), "stderr_sha256": sha(result.stderr)})
        receipt = {"schema": 1, "status": "PASS_FIXED_REFERENCE_DECODE_CPU_CHECKS",
            "cases": int(match.group(1)), "optimized_pass": True, "asan_ubsan_pass": True,
            "sensitivity": witnesses, "sources": {k: sha(v) for k, v in sources.items()},
            "public_inputs": {name: {"path": str(path), "sha256": digest} for name, (path, digest) in INPUTS.items()},
            "archive": {"path": str(ARCHIVE), "sha256": ARCHIVE_SHA, "extracted_ac_video_h_sha256": sha(video_h)},
            "reference": public_reference, "fixture": fixture,
            "compiler": compiler, "compiler_version_sha256": sha((output / "compiler.stdout.txt").read_bytes()),
            "platform": platform.platform(), "python": sys.version,
            "hardware_actions": False, "hardware_acceptance": False, "kernel_built": False,
            "decode_demonstrated": False,
            "native_linux_check": platform.system() == "Linux" and platform.machine() == "x86_64",
            "limits": ["Fixed original fixture only; caller must compute SHA256 over the actual buffer before entry",
                       "Pure CPU bytes/layout arithmetic; no mappings, ownership, submission, fence completion or firmware acceptance proved",
                       "No current CREATE kernel or candidate changed; public prefix intentionally omits trap suffix"]}
        save_json(output / "receipt.json", receipt)
        return receipt
    except BaseException as error:
        save_json(output / "FAILURE.json", {"error": repr(error), "traceback": traceback.format_exc()})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cc", default=shutil.which("cc"))
    args = parser.parse_args()
    if not args.cc:
        parser.error("native C compiler required")
    result = check(args.workspace.resolve(), args.output.resolve(), args.cc)
    print(json.dumps({"status": result["status"], "cases": result["cases"], "mutants": len(result["sensitivity"])}, sort_keys=True))
