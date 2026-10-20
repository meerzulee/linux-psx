#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Create a host-only H.264 first-decode fixture in a new output directory."""

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


WIDTH = HEIGHT = 64
Y_BYTES = WIDTH * HEIGHT
C_BYTES = Y_BYTES // 4
FRAME_BYTES = Y_BYTES + 2 * C_BYTES
X264_PARAMS = ":".join([
    "keyint=1", "min-keyint=1", "scenecut=0", "bframes=0", "ref=1",
    "cabac=0", "8x8dct=0", "interlaced=0", "slices=1", "sliced-threads=0",
    "threads=1", "lookahead-threads=1", "sync-lookahead=0", "rc-lookahead=0",
    "mbtree=0", "aq-mode=0", "psy=0", "weightp=0", "weightb=0",
    "open-gop=0", "intra-refresh=0", "repeat-headers=1", "annexb=1",
    "aud=0", "asm=0",
])


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def planar_planes(data):
    require(len(data) == FRAME_BYTES, f"wrong frame length: {len(data)}")
    return data[:Y_BYTES], data[Y_BYTES:Y_BYTES + C_BYTES], data[Y_BYTES + C_BYTES:]


def plane_metadata(data):
    result = {}
    for name, plane, width in zip("YUV", planar_planes(data), (64, 32, 32)):
        require(len(set(plane)) > 1, f"uniform {name} plane")
        half = width // 2
        regions = [
            [plane[y * width + x] for y in range(y0, y0 + half)
             for x in range(x0, x0 + half)]
            for y0, x0 in ((0, 0), (0, half), (half, 0), (half, half))
        ]
        means = [sum(region) / len(region) for region in regions]
        require(max(means) - min(means) > 32, f"weak regional separation in {name}")
        result[name] = {
            "bytes": len(plane), "sha256": digest(plane),
            "min": min(plane), "max": max(plane), "unique_values": len(set(plane)),
            "quadrant_means_tl_tr_bl_br": means,
        }
    return result


def synthetic_frame():
    # Row-major samples; no randomness, external image, or colorspace conversion.
    y = bytes(24 + 48 * (x // 16) + 12 * (row // 16) + (3 * x + 5 * row) % 16
              for row in range(64) for x in range(64))
    u = bytes(40 + 48 * (row // 8) + (3 * x + row) % 16
              for row in range(32) for x in range(32))
    v = bytes(216 - 48 * (x // 8) - (x + 5 * row) % 16
              for row in range(32) for x in range(32))
    return y + u + v


def annex_b_nals(data):
    # Only identify Annex B boundaries and the one-byte NAL header. FFmpeg's
    # trace_headers filter, not this scanner, parses SPS/PPS/slice syntax.
    starts = list(re.finditer(b"\x00\x00\x00\x01|\x00\x00\x01", data))
    require(starts and starts[0].start() == 0, "missing initial Annex B start code")
    result = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(data)
        require(start.end() < end, "empty NAL")
        header = data[start.end()]
        require(header & 0x80 == 0, "forbidden_zero_bit is set")
        result.append({
            "start_code_offset": start.start(), "start_code_bytes": len(start.group()),
            "nal_offset": start.end(), "nal_bytes": end - start.end(),
            "nal_unit_type": header & 31, "nal_ref_idc": (header >> 5) & 3,
        })
    return result


def generate(output, ffmpeg_name, ffprobe_name):
    # Fail before any write when an output directory/file/symlink already exists.
    output.mkdir(parents=True, exist_ok=False)
    script = Path(__file__).read_bytes()
    (output / "generator.py").write_bytes(script)
    manifest = {
        "schema": 1, "fixture_id": "h264-cb-idr-64x64-v1", "status": "running",
        "hardware_acceptance": "none; host software reference only",
        "generator_sha256": digest(script),
        "host": {"system": platform.system(), "release": platform.release(),
                 "machine": platform.machine(), "python": platform.python_version()},
        "environment_overrides": {"LC_ALL": "C", "LANG": "C", "TZ": "UTC"},
        "commands": [], "checks": [],
    }
    manifest_path = output / "manifest.json"

    def save():
        write_json(manifest_path, manifest)

    def run(label, argv):
        record = {"label": label, "argv": argv, "cwd": str(output),
                  "stdout": label + ".stdout.txt", "stderr": label + ".stderr.txt"}
        manifest["commands"].append(record)
        save()
        completed = subprocess.run(argv, cwd=output, capture_output=True, timeout=60,
                                   env={**os.environ, **manifest["environment_overrides"]})
        (output / record["stdout"]).write_bytes(completed.stdout)
        (output / record["stderr"]).write_bytes(completed.stderr)
        record["returncode"] = completed.returncode
        save()
        require(completed.returncode == 0, f"{label} failed: see {record['stderr']}")
        return completed.stdout.decode("utf-8"), completed.stderr.decode("utf-8")

    def check(name, condition, evidence):
        require(condition, name)
        manifest["checks"].append({"name": name, "result": "pass", "evidence": evidence})

    try:
        save()
        tools = {}
        for name, requested in (("ffmpeg", ffmpeg_name), ("ffprobe", ffprobe_name)):
            executable = shutil.which(requested)
            require(executable is not None, f"{requested} is unavailable")
            executable = str(Path(executable).resolve())
            tools[name] = executable
            stdout, stderr = run(name + "-version", [executable, "-version"])
            manifest.setdefault("tools", {})[name] = {
                "requested": requested, "resolved": executable,
                "executable_sha256": digest(Path(executable).read_bytes()),
                "version_output": stdout + stderr,
            }
        ffmpeg, ffprobe = tools["ffmpeg"], tools["ffprobe"]
        source = synthetic_frame()
        (output / "input.yuv420p").write_bytes(source)
        manifest["input_planes"] = plane_metadata(source)

        encode = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "verbose", "-cpuflags", "0",
            "-f", "rawvideo", "-pixel_format", "yuv420p", "-video_size", "64x64",
            "-framerate", "1", "-i", "input.yuv420p", "-map", "0:v:0",
            "-frames:v", "1", "-an", "-sn", "-dn", "-c:v", "libx264",
            "-preset", "medium", "-profile:v", "baseline", "-level:v", "1.0",
            "-pix_fmt", "yuv420p", "-threads:v", "1", "-g", "1", "-keyint_min", "1",
            "-bf", "0", "-refs", "1", "-qp", "18", "-coder", "cavlc",
            "-color_range", "tv", "-colorspace", "smpte170m",
            "-color_primaries", "smpte170m", "-color_trc", "smpte170m",
            "-x264-params", X264_PARAMS, "-f", "h264", "-n",
        ]
        for label, filename in (("encode", "frame.h264"), ("repeat-encode", "repeat.h264")):
            _, stderr = run(label, encode + [filename])
            require("Error parsing option" not in stderr, "libx264 rejected an option")
        bitstream = (output / "frame.h264").read_bytes()
        check("repeat_encode_identical", bitstream == (output / "repeat.h264").read_bytes(),
              "two separate single-thread libx264 invocations, identical bitstream bytes")
        nals = annex_b_nals(bitstream)
        types = [nal["nal_unit_type"] for nal in nals]
        check("annex_b_sps_pps_one_idr_slice", types.count(7) == types.count(8) == types.count(5) == 1
              and set(types) <= {5, 6, 7, 8}, types)
        manifest["nals"] = nals
        x264_sei = re.search(rb"x264 - core [^\x00]+", bitstream)
        require(x264_sei is not None, "missing x264 version/options SEI")
        manifest["encoder"] = {"name": "libx264", "x264_params": X264_PARAMS,
                               "version_options_sei": x264_sei.group().decode("ascii")}

        probe_text, _ = run("probe", [
            ffprobe, "-v", "error", "-f", "h264", "-threads", "1", "-count_frames",
            "-show_streams", "-show_frames", "-show_format", "-of", "json", "frame.h264",
        ])
        probe = json.loads(probe_text)
        write_json(output / "probe.json", probe)
        streams, frames = probe["streams"], probe["frames"]
        check("one_decoded_frame", len(streams) == len(frames) == 1
              and streams[0]["nb_read_frames"] == "1", "ffprobe reads the complete elementary stream")
        stream, frame = streams[0], frames[0]
        check("constrained_baseline_64x64_yuv420p", stream["profile"] == "Constrained Baseline"
              and stream["width"] == stream["height"] == 64 and stream["pix_fmt"] == "yuv420p"
              and stream["has_b_frames"] == 0 and stream["level"] == 10,
              {key: stream[key] for key in ("profile", "width", "height", "pix_fmt", "has_b_frames", "level")})
        check("progressive_key_i_frame", frame["pict_type"] == "I" and frame["key_frame"] == 1
              and frame["interlaced_frame"] == 0 and frame["width"] == frame["height"] == 64,
              {key: frame[key] for key in ("pict_type", "key_frame", "interlaced_frame", "width", "height")})

        _, trace = run("headers", [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "info", "-f", "h264",
            "-i", "frame.h264", "-map", "0:v:0", "-c:v", "copy", "-bsf:v", "trace_headers",
            "-f", "null", "-",
        ])
        header_fields = {}
        expected_fields = {
            "profile_idc": {66}, "constraint_set0_flag": {1}, "constraint_set1_flag": {1},
            "frame_mbs_only_flag": {1}, "max_num_ref_frames": {0},
            "entropy_coding_mode_flag": {0}, "num_slice_groups_minus1": {0},
            "first_mb_in_slice": {0}, "slice_type": {2, 7},
        }
        for field, allowed in expected_fields.items():
            values = [int(value) for value in re.findall(
                r"\b" + re.escape(field) + r"\s+[01]+\s+=\s+(-?\d+)", trace)]
            check("header_" + field, bool(values) and set(values) <= allowed, values)
            header_fields[field] = values
        manifest["header_fields"] = header_fields

        run("decode", [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "verbose", "-xerror", "-cpuflags", "0",
            "-hwaccel", "none", "-c:v", "h264", "-threads:v", "1", "-err_detect", "explode",
            "-f", "h264", "-i", "frame.h264", "-map", "0:v:0", "-an", "-sn", "-dn",
            "-c:v", "rawvideo", "-pix_fmt", "yuv420p", "-threads:v", "1",
            "-f", "rawvideo", "-n", "expected.yuv420p",
        ])
        decoded = (output / "expected.yuv420p").read_bytes()
        check("raw_frame_lengths", len(source) == len(decoded) == FRAME_BYTES,
              {"input": len(source), "decoded": len(decoded), "expected": FRAME_BYTES})
        manifest["expected_planes"] = plane_metadata(decoded)
        check("all_planes_nonuniform_with_distinct_regions", True, manifest["expected_planes"])
        y, u, v = planar_planes(decoded)
        nv12 = y + bytes(sample for pair in zip(u, v) for sample in pair)
        (output / "expected.nv12").write_bytes(nv12)
        roundtrip = nv12[:Y_BYTES] + nv12[Y_BYTES::2] + nv12[Y_BYTES + 1::2]
        check("nv12_length_and_exact_interleave_roundtrip", len(nv12) == FRAME_BYTES and roundtrip == decoded,
              "Y unchanged; UV bytes are U0,V0,U1,V1,...; deinterleave exactly restores planar Y,U,V")
        manifest["layout"] = {
            "width": 64, "height": 64, "bit_depth": 8, "chroma_subsampling": "4:2:0",
            "planar": {"file": "expected.yuv420p", "Y": [0, 4096, 64],
                       "U": [4096, 1024, 32], "V": [5120, 1024, 32],
                       "tuple_meaning": ["byte_offset", "byte_length", "row_stride_bytes"]},
            "nv12": {"file": "expected.nv12", "Y": [0, 4096, 64], "UV": [4096, 2048, 64],
                     "tuple_meaning": ["byte_offset", "byte_length", "row_stride_bytes"]},
            "padding_or_tiling": "none; tightly packed visible planes only",
        }
        manifest["limitations"] = [
            "No hardware execution, UVD decode correctness, driver readiness, or hardware acceptance established.",
            "Expected pixels are FFmpeg h264 software output; this is not an independent decoder conformance oracle.",
            "Encoding is lossy; input.yuv420p is not the expected decoded output.",
            "Repeat identity is tested on this host/tool build; other versions/builds may generate different bytes.",
            "Tool executable hashes and reported library versions do not archive every dynamically linked dependency.",
            "Future hardware comparison requires documented stride, crop and tiling conversion to these visible planes.",
            "No acceptance tolerance is authorized; any difference needs explanation and an explicit acceptance decision.",
        ]
        manifest["files"] = {
            path.name: {"bytes": path.stat().st_size, "sha256": digest(path.read_bytes())}
            for path in sorted(output.iterdir()) if path.is_file() and path != manifest_path
        }
        manifest["status"] = "passed_host_checks"
        save()
        # The checksum list covers the manifest as well; it cannot hash itself.
        (output / "SHA256SUMS").write_text("".join(
            f"{digest(path.read_bytes())}  {path.name}\n"
            for path in sorted(output.iterdir()) if path.is_file() and path.name != "SHA256SUMS"
        ))
        return manifest
    except Exception as error:
        manifest["status"] = "failed_preserved"
        manifest["failure"] = {"type": type(error).__name__, "message": str(error)}
        save()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="directory that must not exist")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()
    try:
        manifest = generate(args.output.expanduser().absolute(), args.ffmpeg, args.ffprobe)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"fixture generation failed (any created output is preserved): {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": manifest["status"], "output": str(args.output.absolute()),
                      "checks": len(manifest["checks"]),
                      "frame_h264_sha256": manifest["files"]["frame.h264"]["sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
