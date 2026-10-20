#!/usr/bin/env python3
"""Freeze the unwired live-control helper and its host tools as a new attempt.

Reads and copies local files only. No patch, kernel build, checkout mutation
or hardware access. Existing output directories are never reused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPER = ("amdgpu_ps4_uvd_live_control.h", "amdgpu_ps4_uvd_live_control.c")
TOOLS = ("check.py", "fixture.c", "freeze.py", "DESIGN.md", "README.md")
PREPARE_HEADER_PIN = "3d18c0f0aa2e8efefad5ca04faa58df7d85bc329209dc5704de7e9bb7024d612"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def freeze(out: Path, attempt: int) -> dict:
    if out.exists():
        raise ValueError("Output exists; preserve it and choose a new directory")
    out.mkdir(parents=True, mode=0o700)
    hashes = {}
    for directory, names in (("helper", HELPER), ("tools", TOOLS)):
        hashes[directory] = {}
        (out / directory).mkdir()
        for name in names:
            data = (HERE / name).read_bytes()
            with (out / directory / name).open("xb") as stream:
                stream.write(data)
            hashes[directory][name] = sha(data)
    receipt = {
        "schema": 1,
        "attempt": attempt,
        "status": "UNWIRED_SOURCE_CANDIDATE_NO_KERNEL_BUILD_OR_HARDWARE",
        "intended_kernel_directory": "drivers/gpu/drm/amd/amdgpu",
        "helper_files": hashes["helper"],
        "tool_files": hashes["tools"],
        "prepare_header_sha256": PREPARE_HEADER_PIN,
        "prepare_header_source": "live-prepare source attempt-06 amdgpu_ps4_live_prepare.h",
        "wired": False,
        "makefile_entry": False,
        "kernel_built": False,
        "hardware_tested": False,
    }
    with (out / "SOURCE-RECEIPT.json").open("x") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.out, args.attempt), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
