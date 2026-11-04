#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate our small offline browser fixture on the build host, without devices."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.parent.mkdir(parents=True, exist_ok=True)
command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
           "testsrc2=size=640x360:rate=30", "-t", "3", "-an", "-c:v", "libvpx",
           "-b:v", "180k", "-deadline", "good", "-cpu-used", "4", "-threads", "2",
           "-fflags", "+bitexact", "-flags:v", "+bitexact", "-map_metadata", "-1",
           "-f", "webm", "-y", str(args.output)]
subprocess.run(command, check=True, timeout=60)
if not 0 < args.output.stat().st_size < 1024 * 1024:
    raise ValueError("browser fixture exceeds its 1MiB budget")
receipt = {"command": command, "input": "locally generated FFmpeg testsrc2, no external media",
           "seconds": 3, "width": 640, "height": 360, "fps": 30, "audio": False,
           "bytes": args.output.stat().st_size, "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}
args.output.with_suffix(".json").write_text(json.dumps(receipt, indent=2) + "\n")
