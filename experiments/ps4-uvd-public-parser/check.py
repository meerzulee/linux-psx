#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise three pinned public software-parser functions with host stubs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

PIN = "a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea"
SOURCES = {
    "drivers/gpu/drm/amd/amdgpu/amdgpu_uvd.c": "6b8c41d474f950b1de4fa57a03498915fa4ed0208341de5655379cdb81e5318d",
    "drivers/gpu/drm/amd/amdgpu/cikd.h": "3a3940dd1753918a8401e7ad81da32da54714ba0a2c5673bde408a4b239fb0b4",
    "drivers/gpu/drm/amd/include/asic_reg/uvd/uvd_4_2_d.h": "9cd134d9a068e208266d0e0c483470b3ea8419fd18ba993e0fe77a184e305a13",
}
FUNCTIONS = (
    "amdgpu_uvd_cs_reg", "amdgpu_uvd_cs_packets", "amdgpu_uvd_ring_parse_cs",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def masked(source):
    pattern = r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    return re.sub(pattern, lambda m: "".join("\n" if c == "\n" else " " for c in m[0]),
                  source, flags=re.S)


def braced(source, start):
    clean = masked(source)
    left = clean.index("{", start)
    depth = 1
    end = left + 1
    while depth:
        depth += (clean[end] == "{") - (clean[end] == "}")
        end += 1
    return source[start:end]


def function(source, name):
    matches = list(re.finditer(r"^(?:static )?int " + name + r"\s*\(", source, re.M))
    if len(matches) != 1:
        raise RuntimeError("Expected one public function: " + name)
    return braced(source, matches[0].start())


def defines(source):
    result = {}
    lines = iter(source.splitlines(keepends=True))
    for line in lines:
        match = re.match(r"#define\s+(\w+)", line)
        if match:
            body = line
            while body.endswith("\\\n"):
                body += next(lines)
            result[match[1]] = body
    return result


def run(argv):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError("Command failed: " + shlex.join(argv) + "\n" + result.stdout + result.stderr)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-repo", type=Path, required=True,
                    help="Linux Git repository containing the exact public commit; worktree edits are ignored")
    ap.add_argument("--report", type=Path, required=True,
                    help="new host-only report directory; existing evidence is never overwritten")
    ap.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = ap.parse_args()
    compiler = shlex.split(args.cc)
    if not compiler:
        ap.error("--cc must name a compiler")
    if args.report.exists() or args.report.is_symlink():
        ap.error("report directory already exists; preserve the previous run")
    sources = {}
    for name, expected in SOURCES.items():
        data = subprocess.run(["git", "--no-pager", "-C", str(args.source_repo),
                               "show", PIN + ":" + name], capture_output=True, check=True).stdout
        if digest(data) != expected:
            raise RuntimeError("Public Git-object identity mismatch: " + name)
        sources[name] = data.decode("utf-8")
    public, packets, registers = sources.values()
    selected = {name: function(public, name) for name in FUNCTIONS}
    context = braced(public, public.index("struct amdgpu_uvd_cs_ctx {")) + ";"
    packet_defines = defines(packets)
    register_defines = defines(registers)
    macros = "".join(packet_defines[name] for name in (
        "PACKET_TYPE0", "PACKET_TYPE2", "CP_PACKET_GET_TYPE", "CP_PACKET_GET_COUNT", "CP_PACKET0_GET_REG"))
    macros += "".join(register_defines[name] for name in (
        "mmUVD_GPCOM_VCPU_DATA0", "mmUVD_GPCOM_VCPU_DATA1", "mmUVD_GPCOM_VCPU_CMD",
        "mmUVD_ENGINE_CNTL", "mmUVD_NO_OP"))
    here = Path(__file__).resolve().parent
    fixture = (here / "fixture.c").read_text()
    unit = fixture
    for marker, body in {
        "PUBLIC_DEFINES": macros, "PUBLIC_CONTEXT": context,
        # The unchanged upstream loop compares int i with unsigned count.
        # Scope the kernel-style warning allowance to extracted code only.
        "PUBLIC_PARSER_FUNCTIONS": (
            '#pragma GCC diagnostic push\n#pragma GCC diagnostic ignored "-Wsign-compare"\n'
            + "\n\n".join(selected.values()) + '\n#pragma GCC diagnostic pop'),
    }.items():
        placeholder = "/* " + marker + " */"
        if unit.count(placeholder) != 1:
            raise RuntimeError("Fixture extraction marker changed: " + marker)
        unit = unit.replace(placeholder, body)
    # Keep AMD's MIT notice and author credit on the extracted public code.
    unit = public[:public.index("#include")] + "\n" + unit
    args.report.mkdir(parents=True, mode=0o700)
    generated = args.report / "actual-public-parser-fixture.c"
    generated.write_text(unit)
    results = []
    with tempfile.TemporaryDirectory(prefix="psx-public-parser-") as temporary:
        for mode, flags in (
            ("optimized", ["-O2"]),
            ("sanitized", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]),
        ):
            binary = Path(temporary) / mode
            built = run([*compiler, "-std=gnu11", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
                         *flags, str(generated), "-o", str(binary)])
            (args.report / (mode + "-compile.log")).write_text(built.stdout + built.stderr)
            tested = run([str(binary)])
            (args.report / (mode + "-run.log")).write_text(tested.stdout + tested.stderr)
            results.append({"mode": mode, "output": tested.stdout.strip()})
            print(mode + ": " + tested.stdout.strip(), flush=True)
    receipt = {
        "schema": 1, "status": "PASS_PUBLIC_SOFTWARE_PARSER_HOST_ONLY", "public_commit": PIN,
        "source_sha256": SOURCES,
        "extracted_function_sha256": {name: digest(text.encode()) for name, text in selected.items()},
        "fixture_sha256": digest(fixture.encode()), "checker_sha256": digest(Path(__file__).read_bytes()),
        "generated_sha256": digest(unit.encode()), "compiler": run([*compiler, "--version"]).stdout.splitlines()[0],
        "results": results,
        "real_functions": list(FUNCTIONS),
        "stubs": ["host structs", "IB value accessor", "error logging", "pass1/pass2 command callbacks"],
        "warning_allowance": "-Wsign-compare disabled only around unchanged public functions; fixture keeps -Werror",
        "excluded": ["message ABI validation", "relocation and buffer ownership validation", "MMIO/DMA",
                     "firmware", "PS4 kernel patch", "compiled-kernel review", "hardware acceptance"],
        "conclusion": "DATA-only reaches the public packet allowlist but fails full userspace submission without a message flag. CONTEXT_ID fails the allowlist. These are software-policy results, not Liverpool hardware semantics.",
    }
    (args.report / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print("Host-only public parser checks complete; no device or kernel target accessed.")


if __name__ == "__main__":
    main()
