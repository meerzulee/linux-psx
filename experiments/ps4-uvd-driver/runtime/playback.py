#!/usr/bin/env python3
"""Pinned, single-attempt FFmpeg VAAPI/NV12 comparison; no hardware work on import."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import selectors
import stat
import subprocess
import sys
import time
import uuid


SCHEMA = "psx-uvd-playback-fixture-v1"
LOG_LIMIT = 256 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def pinned_file(path, expected):
    require(re.fullmatch(r"[0-9a-f]{64}", expected), "Invalid SHA256 pin")
    require(stat.S_ISREG(path.stat().st_mode), f"Not a regular file: {path}")
    actual = digest(path)
    require(actual == expected, f"SHA256 mismatch: {path}")
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": actual}


def fixture_file(root, record):
    name = record["path"]
    parts = PurePosixPath(name)
    require(not parts.is_absolute() and parts.as_posix() == name and
            all(part not in ("", ".", "..") for part in parts.parts),
            "Fixture paths must be plain relative paths")
    path = root / name
    require(path.resolve() == path and path.is_relative_to(root),
            f"Fixture links/escapes forbidden: {name}")
    identity = pinned_file(path, record["sha256"])
    require(identity["bytes"] == record["bytes"], f"Fixture size mismatch: {name}")
    return path


def verify_fixture(path, expected):
    identity = pinned_file(path, expected)
    fixture = json.loads(path.read_text())
    require(fixture["schema"] == SCHEMA, "Unknown fixture schema")
    width, height = fixture["width"], fixture["height"]
    require(type(width) is int and type(height) is int and
            0 < width <= 2048 and 0 < height <= 2048 and not (width % 2 or height % 2),
            "Invalid bounded NV12 dimensions")
    frame_bytes = width * height * 3 // 2
    require(fixture["frame_count"] == 120 and fixture["frame_rate"] == "30/1" and
            fixture["pixel_format"] == "nv12" and
            fixture["frame_bytes"] == frame_bytes and frame_bytes * 120 <= 64 * 1024**2,
            "Expected 120 bounded NV12 frames")
    require(fixture["codec"] == "h264" and fixture["container"] == "h264-annex-b" and
            fixture["profile"] in ("Baseline", "Constrained Baseline"),
            "Expected baseline H.264 Annex B")
    frames = fixture["frames"]
    require(len(frames) == 120 and [f["index"] for f in frames] == list(range(120)),
            "Reference frame indices must be exactly 0..119")
    types = Counter(f["pict_type"] for f in frames)
    require(set(types) == {"I", "P"} and frames[0]["pict_type"] == "I",
            "Fixture must include I/P prediction with no B frames")
    root = path.parent
    records = [fixture["clip"], fixture["reference"], *frames, *fixture["evidence"]]
    require(len({r["path"] for r in records}) == len(records), "Duplicate fixture paths")
    for record in records:
        fixture_file(root, record)
    require(fixture["reference"]["bytes"] == frame_bytes * 120 and
            all(f["bytes"] == frame_bytes for f in frames), "Reference length mismatch")
    with (root / fixture["reference"]["path"]).open("rb") as handle:
        for frame in frames:
            require(hashlib.sha256(handle.read(frame_bytes)).hexdigest() == frame["sha256"],
                    f"Concatenated/per-frame reference disagreement: {frame['index']}")
    unique = len({f["sha256"] for f in frames})
    require(unique > 1, "Fixture must contain different pictures")
    return fixture, {"manifest": identity, "clip": fixture["clip"],
                     "reference": fixture["reference"], "width": width, "height": height,
                     "frame_count": 120, "unique_frames": unique, "picture_types": dict(types)}


def environment(driver):
    suffix = "_drv_video.so"
    require(driver.name.endswith(suffix), "Expected a Mesa/libva *_drv_video.so file")
    return {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
            "LIBVA_DRIVER_NAME": driver.name[:-len(suffix)],
            "LIBVA_DRIVERS_PATH": str(driver.parent), "LIBVA_MESSAGING_LEVEL": "2"}


def decode_command(ffmpeg, node, clip):
    # There is no hwupload, scale, frame limiter, or software-output alternative.
    return [str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "verbose", "-nostats",
            "-xerror", "-noauto_conversion_filters", "-err_detect", "explode",
            "-hwaccel", "vaapi", "-hwaccel_device", str(node),
            "-hwaccel_output_format", "vaapi", "-c:v", "h264", "-threads", "1",
            "-f", "h264", "-framerate", "30", "-i", str(clip), "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", "hwdownload,format=nv12", "-fps_mode", "passthrough",
            "-c:v", "rawvideo", "-pix_fmt", "nv12", "-f", "rawvideo", "pipe:1"]


def query(ffmpeg, option, env):
    result = subprocess.run([str(ffmpeg), option], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10,
                            check=True)
    require(len(result.stdout) <= LOG_LIMIT, "FFmpeg identity query exceeded log bound")
    return result.stdout.decode("utf-8", errors="replace")


def preflight(args):
    fixture, fixture_identity = verify_fixture(args.fixture, args.fixture_sha256)
    require(platform.system() == "Linux", "Runtime preflight requires Linux")
    require(platform.release() == args.release, "Running kernel release mismatch")
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    require(boot_id == str(uuid.UUID(args.boot_id)), "Running boot ID mismatch")
    ffmpeg = args.ffmpeg.resolve(strict=True)
    selected_driver = args.driver.absolute()
    driver = selected_driver.resolve(strict=True)
    node = args.render_node.resolve(strict=True)
    require(re.fullmatch(r"/dev/dri/renderD[0-9]+", str(node)), "Expected DRM render node")
    node_stat = node.stat()
    require(stat.S_ISCHR(node_stat.st_mode), "Render node must be a character device")
    require(os.access(node, os.R_OK | os.W_OK), "Render node is not accessible")
    ffmpeg_identity = pinned_file(ffmpeg, args.ffmpeg_sha256)
    driver_identity = pinned_file(driver, args.driver_sha256)
    driver_identity["selected_path"] = str(selected_driver)
    env = environment(selected_driver)
    version = query(ffmpeg, "-version", env)
    hwaccels = query(ffmpeg, "-hwaccels", env)
    require("vaapi" in hwaccels.splitlines(), "FFmpeg build lacks VAAPI")
    command = decode_command(ffmpeg, node, args.fixture.parent / fixture["clip"]["path"])
    return fixture, {"fixture": fixture_identity, "kernel_release": platform.release(),
                     "boot_id": boot_id, "render_node": {"path": str(node),
                     "major": os.major(node_stat.st_rdev), "minor": os.minor(node_stat.st_rdev)},
                     "ffmpeg": ffmpeg_identity, "ffmpeg_version": version,
                     "ffmpeg_hwaccels": hwaccels, "driver": driver_identity,
                     "environment": env, "command": command}


def write_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def bounded_decode(command, env, output, expected_bytes, frame_bytes, timeout):
    started = time.monotonic()
    sizes = {"raw": 0, "log": 0}
    limits = {"raw": expected_bytes + frame_bytes, "log": LOG_LIMIT}
    reason = None
    child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with selectors.DefaultSelector() as selector, \
                (output / "decoded.nv12").open("xb") as raw, \
                (output / "ffmpeg.stderr.txt").open("xb") as log:
            selector.register(child.stdout, selectors.EVENT_READ, ("raw", raw))
            selector.register(child.stderr, selectors.EVENT_READ, ("log", log))
            while selector.get_map():
                require(time.monotonic() - started <= timeout, "FFmpeg decode timeout")
                for key, _ in selector.select(0.1):
                    block = os.read(key.fd, 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    name, handle = key.data
                    remaining = limits[name] - sizes[name]
                    handle.write(block[:remaining])
                    sizes[name] += min(len(block), remaining)
                    require(len(block) <= remaining, f"FFmpeg {name} output exceeded bound")
            code = child.wait(timeout=max(0.1, timeout - (time.monotonic() - started)))
    except (Exception, KeyboardInterrupt) as error:
        reason = str(error) or type(error).__name__
        child.kill()
        code = child.wait(timeout=5)
    finally:
        child.stdout.close()
        child.stderr.close()
    return {"returncode": code, "failure": reason,
            "elapsed_seconds": round(time.monotonic() - started, 6), "retained_bytes": sizes,
            "timeout_seconds": timeout, "byte_limits": limits}


def compare(fixture, root, decoded):
    data = decoded.read_bytes()
    size = fixture["frame_bytes"]
    frames = [{"index": f["index"], "sha256": hashlib.sha256(
               data[f["index"] * size:(f["index"] + 1) * size]).hexdigest(),
               "expected_sha256": f["sha256"]} for f in fixture["frames"]]
    mismatches = [f["index"] for f in frames if f["sha256"] != f["expected_sha256"]]
    total_hash = hashlib.sha256(data).hexdigest()
    result = {"policy": "exact-nv12-bytes-v1", "bytes": len(data),
              "complete_frames": len(data) // size, "trailing_bytes": len(data) % size,
              "sha256": total_hash, "frames": frames, "mismatched_frames": mismatches,
              "pass": len(data) == fixture["reference"]["bytes"] and not mismatches and
              total_hash == fixture["reference"]["sha256"]}
    if len(data) == fixture["reference"]["bytes"] and mismatches:
        reference = (root / fixture["reference"]["path"]).read_bytes()
        result["different_sample_bytes"] = sum(a != b for a, b in zip(data, reference))
        result["maximum_absolute_sample_error"] = max(abs(a - b) for a, b in zip(data, reference))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--fixture-sha256", required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffmpeg-sha256", required=True)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--driver-sha256", required=True)
    parser.add_argument("--render-node", type=Path, required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--boot-id", required=True)
    parser.add_argument("--run", action="store_true", help="Explicitly execute one hardware decode")
    parser.add_argument("--output", type=Path, help="New private result directory; required with --run")
    parser.add_argument("--experiment")
    parser.add_argument("--uart-session", help="External bounded-session reference assertion only")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)
    output = None
    created_output = False
    result = {"status": "FAIL_PREFLIGHT", "hardware_acceptance": False}
    try:
        args.fixture = args.fixture.resolve(strict=True)
        require(1 <= args.timeout <= 120, "Timeout must be 1..120 seconds")
        if args.run:
            require(args.output is not None and args.experiment and args.uart_session,
                    "--run requires --output, --experiment and --uart-session")
            require(re.fullmatch(r"EXP-[0-9]{8}-[0-9]{3}", args.experiment), "Invalid experiment ID")
            require(0 < len(args.uart_session) <= 256 and not any(
                    ord(c) < 32 for c in args.uart_session), "Invalid UART reference assertion")
        fixture, identity = preflight(args)
        result.update(status="PASS_PREFLIGHT", preflight=identity, hardware_decode_invoked=False)
        if not args.run:
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        result["status"] = "FAIL_RUN"
        output = args.output.absolute()
        require(output.parent.is_dir() and output.parent.resolve() == output.parent,
                "Output needs an existing canonical parent")
        require(not output.is_relative_to(args.fixture.parent), "Output must be outside fixture tree")
        output.mkdir(mode=0o700)  # Exclusive: existing or failed directories are never reused.
        created_output = True
        result.update(status="FAIL_DECODE", experiment=args.experiment,
                      uart_session_assertion=args.uart_session, uart_authenticated=False,
                      hardware_decode_invoked=True, runner_sha256=digest(Path(__file__).resolve()))
        write_json(output / "PREFLIGHT.json", result)
        execution = bounded_decode(identity["command"], identity["environment"], output,
                                   fixture["reference"]["bytes"], fixture["frame_bytes"], args.timeout)
        result["execution"] = execution
        write_json(output / "EXECUTION.json", execution)
        comparison = compare(fixture, args.fixture.parent, output / "decoded.nv12")
        write_json(output / "COMPARISON.json", comparison)
        # Inputs, selected device and the running boot must remain exactly the preflight identities.
        _, after = preflight(args)
        require(after == identity, "Runtime/input identity changed during decode")
        result["inputs_unchanged"] = True
        result["pixel_comparison_pass"] = comparison["pass"]
        if execution["returncode"] == 0 and execution["failure"] is None:
            result["status"] = "PASS_EXACT_NV12" if comparison["pass"] else "FAIL_COMPARISON"
        result["artifacts"] = {name: {"sha256": digest(output / name),
                              "bytes": (output / name).stat().st_size} for name in
                              ("decoded.nv12", "ffmpeg.stderr.txt", "PREFLIGHT.json",
                               "EXECUTION.json", "COMPARISON.json")}
        write_json(output / "RESULT.json", result)
    except (Exception, KeyboardInterrupt) as error:
        result.update(error=str(error) or type(error).__name__)
        if created_output and not (output / "RESULT.json").exists():
            write_json(output / "RESULT.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS_EXACT_NV12" else 1


if __name__ == "__main__":
    sys.exit(main())
