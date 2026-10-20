#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CPU-only checks of extracted runtime packet callbacks; never accesses hardware.

Usage: transport_check.py --source runtime.c --kernel-source KERNEL --output NEW
The kernel root supplies uvd_5_0_d.h, sid.h and amdgpu_ring.h. All inputs and
generated fixtures are retained with hashes, including failed executions.
"""
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


FUNCTIONS = ("ps4_uvd_emit_ib", "ps4_uvd_emit_fence", "ps4_uvd_insert_nop")
HEADERS = {
    "uvd_5_0_d.h": "drivers/gpu/drm/amd/include/asic_reg/uvd/uvd_5_0_d.h",
    "sid.h": "drivers/gpu/drm/amd/amdgpu/sid.h",
    "amdgpu_ring.h": "drivers/gpu/drm/amd/amdgpu/amdgpu_ring.h",
}
REGISTERS = {
    "mmUVD_LMI_RBC_IB_64BIT_BAR_LOW": 0x3c67,
    "mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH": 0x3c66,
    "mmUVD_RBC_IB_SIZE": 0x3da2, "mmUVD_CONTEXT_ID": 0x3dbd,
    "mmUVD_GPCOM_VCPU_DATA0": 0x3bc4, "mmUVD_GPCOM_VCPU_DATA1": 0x3bc5,
    "mmUVD_GPCOM_VCPU_CMD": 0x3bc3, "mmUVD_NO_OP": 0x3bff,
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def function(source, name):
    # Kernel style: only the complete function's final brace is in column zero.
    pattern = r"(?ms)^static(?: inline)? void\s+" + re.escape(name) + r"\([^;{]*\)\n\{.*?^\}\n"
    found = list(re.finditer(pattern, source))
    if len(found) != 1:
        raise ValueError("Nonunique or changed function boundary: " + name)
    match = found[0]
    return match.group(), {"line": source.count("\n", 0, match.start()) + 1,
                           "sha256": sha(match.group().encode())}


def define(source, name):
    lines = source.splitlines(keepends=True)
    positions = [i for i, line in enumerate(lines) if re.match(
        r"^#define\s+" + re.escape(name) + r"(?:\s|\()", line)]
    if len(positions) != 1:
        raise ValueError("Nonunique or missing actual macro: " + name)
    index = positions[0]
    result = lines[index]
    while result.rstrip().endswith("\\"):
        index += 1
        result += lines[index]
    return result


FIXTURE = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define lower_32_bits(x) ((u32)(x))
#define upper_32_bits(x) ((u32)((u64)(x) >> 32))
static unsigned int warnings;
static int warn_on(bool bad) { if (bad) ++warnings; return bad; }
#define WARN_ON(x) warn_on(!!(x))
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "CHECK line=%u: %s\n", \
    (unsigned int)__LINE__, #x); exit(2); } } while (0)
struct amdgpu_job { unsigned int unused; };
struct amdgpu_ib { u64 gpu_addr; u32 length_dw; };
struct amdgpu_ring {
    u32 *ring, buf_mask;
    u64 wptr, ptr_mask;
    int count_dw;
    u32 storage[32], recorded[512];
    unsigned int writes;
};
@MACROS@
@ASSERTIONS@
/* Preserve the actual kernel writer body; only its symbol is renamed here. */
#define amdgpu_ring_write actual_kernel_ring_write
@WRITER@
#undef amdgpu_ring_write
static void amdgpu_ring_write(struct amdgpu_ring *ring, u32 value)
{
    CHECK(ring->writes < 512);
    ring->recorded[ring->writes++] = value;
    actual_kernel_ring_write(ring, value);
}
@FUNCTIONS@
static void init(struct amdgpu_ring *ring, u64 start)
{
    unsigned int i;
    memset(ring, 0, sizeof(*ring));
    ring->ring = ring->storage;
    ring->buf_mask = 31;
    ring->ptr_mask = UINT32_MAX;
    ring->wptr = start;
    ring->count_dw = 512;
    for (i = 0; i < 32; ++i) ring->storage[i] = 0x5555a5a5;
    warnings = 0;
}
static void verify(const struct amdgpu_ring *ring, u64 start,
                   const u32 *expected, unsigned int count, unsigned int warn_count)
{
    unsigned int i;
    u32 slots[32];
    CHECK(ring->writes == count);
    CHECK(ring->wptr == ((start + count) & UINT32_MAX));
    CHECK(ring->count_dw == 512 - (int)count);
    CHECK(warnings == warn_count);
    for (i = 0; i < 32; ++i) slots[i] = 0x5555a5a5;
    for (i = 0; i < count; ++i) {
        CHECK(ring->recorded[i] == expected[i]);
        slots[(start + i) % 32] = expected[i];
    }
    CHECK(memcmp(slots, ring->storage, sizeof(slots)) == 0);
}
int main(void)
{
    /* Synthetic bit patterns, not claims about a machine's allocated addresses. */
    const u64 addresses[] = { 0, 0x12345678ULL, 0xffffff00ULL, 0x100000000ULL,
                              0x300308000ULL, 0xfedcba9800ULL, 0xfffffffc00ULL };
    const u64 starts[] = { 0, 30, UINT32_MAX - 1ULL, UINT32_MAX - 15ULL };
    const u32 lengths[] = { 1, 16, 0x3456 };
    const u64 sequences[] = { 0, UINT32_MAX, 0x100000001ULL, 0xdeadbeef01234567ULL };
    const u32 counts[] = { 0, 2, 14, 16, 30, 32, 64 };
    struct amdgpu_ring ring;
    struct amdgpu_job job = { 0 };
    unsigned int a, s, n, q, i, ib_cases = 0, fence_cases = 0, nop_cases = 0;
    for (a = 0; a < sizeof(addresses)/sizeof(addresses[0]); ++a) {
        CHECK((addresses[a] >> 40) == 0);
        for (s = 0; s < sizeof(starts)/sizeof(starts[0]); ++s) {
            for (n = 0; n < sizeof(lengths)/sizeof(lengths[0]); ++n) {
                struct amdgpu_ib ib = { addresses[a], lengths[n] };
                const u32 words[] = { 0x3c67, (u32)addresses[a], 0x3c66,
                                      (u32)(addresses[a] >> 32), 0x3da2, lengths[n] };
                init(&ring, starts[s]);
                ps4_uvd_emit_ib(&ring, &job, &ib, 0);
                verify(&ring, starts[s], words, 6, 0);
                ++ib_cases;
            }
            for (q = 0; q < sizeof(sequences)/sizeof(sequences[0]); ++q) {
                const u32 words[] = { 0x3dbd, (u32)sequences[q], 0x3bc4, (u32)addresses[a],
                                      0x3bc5, (u32)(addresses[a] >> 32) & 0xff, 0x3bc3, 0 };
                init(&ring, starts[s]);
                ps4_uvd_emit_fence(&ring, addresses[a], sequences[q], 0);
                verify(&ring, starts[s], words, 8, 0);
                CHECK(ring.recorded[6] == 0x3bc3 && ring.recorded[7] == 0);
                ++fence_cases;
            }
        }
    }
    /* DATA1 is eight bits even for an out-of-domain input. Admission is not tested. */
    {
        const u32 words[] = { 0x3dbd, 0x12345678, 0x3bc4, 0xabcdef00, 0x3bc5, 0xff, 0x3bc3, 0 };
        init(&ring, 30);
        ps4_uvd_emit_fence(&ring, 0xdeadbeffabcdef00ULL, 0x12345678ULL, 0);
        verify(&ring, 30, words, 8, 0);
        init(&ring, 30);
        ps4_uvd_emit_fence(&ring, 0xffabcdef00ULL, 0x12345678ULL, AMDGPU_FENCE_FLAG_64BIT);
        verify(&ring, 30, words, 8, 1);
    }
    for (s = 0; s < 18; ++s) {
        u64 start = s < 16 ? s * 2ULL : starts[s - 14];
        for (n = 0; n < sizeof(counts)/sizeof(counts[0]); ++n) {
            u32 words[64];
            for (i = 0; i < counts[n]; ++i) words[i] = i % 2 ? 0 : 0x3bff;
            init(&ring, start);
            ps4_uvd_insert_nop(&ring, counts[n]);
            verify(&ring, start, words, counts[n], 0);
            ++nop_cases;
        }
    }
    /* Odd input is WARN-only in the actual callback; require callers to stay even. */
    {
        const u32 words[] = { 0x3bff, 0 };
        init(&ring, 0); ps4_uvd_insert_nop(&ring, 3); verify(&ring, 0, words, 2, 1);
        init(&ring, 31); ps4_uvd_insert_nop(&ring, 2); verify(&ring, 31, words, 2, 1);
        init(&ring, 31); ps4_uvd_insert_nop(&ring, 3); verify(&ring, 31, words, 2, 1);
    }
    /* Typical six-DW IB + eight-DW fence + two-DW alignment pad across wrap. */
    {
        struct amdgpu_ib ib = { 0x300308000ULL, 16 };
        const u32 words[] = { 0x3c67, 0x308000, 0x3c66, 3, 0x3da2, 16,
                              0x3dbd, 0x12345678, 0x3bc4, 0x30a000, 0x3bc5, 3, 0x3bc3, 0,
                              0x3bff, 0 };
        init(&ring, 30);
        ps4_uvd_emit_ib(&ring, &job, &ib, 0);
        ps4_uvd_emit_fence(&ring, 0x30030a000ULL, 0x12345678, 0);
        ps4_uvd_insert_nop(&ring, 2);
        verify(&ring, 30, words, 16, 0);
    }
    printf("{\"status\":\"PASS_PACKET_SERIALIZATION\",\"ib_cases\":%u,"
           "\"fence_cases\":%u,\"nop_cases\":%u,\"boundary_cases\":6}\n",
           ib_cases, fence_cases, nop_cases);
    return 0;
}
'''


def fixture(source, headers):
    extracted = {name: function(source, name) for name in FUNCTIONS}
    writer, writer_anchor = function(headers["amdgpu_ring.h"], "amdgpu_ring_write")
    table = re.findall(r"(?ms)^static const struct amdgpu_ring_funcs ps4_uvd_ring_funcs = \{.*?^\};", source)
    if len(table) != 1:
        raise ValueError("Nonunique runtime ring function table")
    for key, value in {"emit_ib": FUNCTIONS[0], "emit_fence": FUNCTIONS[1],
                       "insert_nop": FUNCTIONS[2], "emit_ib_size": "6",
                       "emit_frame_size": "8", "align_mask": "0xf"}.items():
        if not re.search(r"\." + key + r"\s*=\s*" + value + r"\s*,", table[0]):
            raise ValueError("Runtime callback selection/budget changed: " + key)
    macros = "".join(define(headers["uvd_5_0_d.h"], name) for name in REGISTERS)
    macros += define(headers["sid.h"], "PACKET_TYPE0") + define(headers["sid.h"], "PACKET0")
    macros += define(headers["amdgpu_ring.h"], "AMDGPU_FENCE_FLAG_64BIT")
    assertions = "\n".join(f'_Static_assert({name} == {value}, "EXP005 register: {name}");'
                             for name, value in REGISTERS.items())
    assertions += '\n_Static_assert(PACKET0(0x3c67, 0) == 0x3c67, "PACKET0 register encoding");'
    assertions += '\n_Static_assert(PACKET0(0x3c67, 1) == 0x13c67, "PACKET0 count encoding");'
    result = FIXTURE
    for key, value in {"MACROS": macros, "ASSERTIONS": assertions, "WRITER": writer,
                       "FUNCTIONS": "\n".join(extracted[name][0] for name in FUNCTIONS)}.items():
        result = result.replace("@" + key + "@", value)
    return result, {**{name: item[1] for name, item in extracted.items()},
                    "amdgpu_ring_write": writer_anchor, "ring_table_sha256": sha(table[0].encode())}


def run(args):
    paths = {"runtime.c": args.source.resolve(), **{
        name: (args.kernel_source / relative).resolve() for name, relative in HEADERS.items()}}
    raw = {name: path.read_bytes() for name, path in paths.items()}
    generated, anchors = fixture(raw["runtime.c"].decode(), {
        name: raw[name].decode() for name in HEADERS})
    output = args.output.resolve()
    output.mkdir(mode=0o700)  # Exclusive; never replace failed or existing evidence.
    report = {"status": "FAIL_PACKET_CHECKS", "hardware_acceptance": False,
              "hardware_access": False, "host": platform.platform(),
              "runner_sha256": sha(Path(__file__).read_bytes()), "anchors": anchors,
              "inputs": {name: {"path": str(paths[name]), "sha256": sha(data)}
                         for name, data in raw.items()}, "commands": []}
    compiler = str(Path(shutil.which(args.cc) or args.cc).resolve())
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = "detect_leaks=" + ("0" if platform.system() == "Darwin" else "1") + ":abort_on_error=1"
    env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"

    def command(argv):
        result = subprocess.run(argv, cwd=output, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=60)
        item = {"argv": argv, "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}
        report["commands"].append(item)
        return item

    def build(name, text, flags):
        source_path, executable = output / (name + ".c"), output / name
        source_path.write_text(text)
        result = command([compiler, "-std=gnu11", "-g", "-Wall", "-Werror", "-Wformat=2",
                          "-Wshadow", *flags, str(source_path), "-o", str(executable)])
        if result["returncode"]:
            raise RuntimeError("Fixture compilation failed: " + name)
        return executable

    try:
        for name, data in raw.items():
            (output / name).write_bytes(data)
        report["compiler"] = command([compiler, "--version"])
        report["runs"] = {}
        for mode, flags in (("optimized", ["-O2"]), ("sanitized", ["-O1", "-fno-omit-frame-pointer",
                                                                   "-fsanitize=address,undefined"])):
            executable = build(mode, generated, flags)
            result = command([str(executable)])
            if result["returncode"]:
                raise RuntimeError("Actual callback checks failed: " + mode)
            report["runs"][mode] = json.loads(result["stdout"])
        original_ib, _ = function(raw["runtime.c"].decode(), FUNCTIONS[0])
        swapped_ib = original_ib.replace("mmUVD_LMI_RBC_IB_64BIT_BAR_LOW", "SWAP_MARKER")
        swapped_ib = swapped_ib.replace("mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH", "mmUVD_LMI_RBC_IB_64BIT_BAR_LOW")
        swapped_ib = swapped_ib.replace("SWAP_MARKER", "mmUVD_LMI_RBC_IB_64BIT_BAR_HIGH")
        mutations = {
            "swapped-IB-registers": (original_ib, swapped_ib),
            "CMD2-trap": ("amdgpu_ring_write(ring, 0);\n\t/* Completion",
                           "amdgpu_ring_write(ring, 2);\n\t/* Completion"),
            "unmasked-fence-high": ("upper_32_bits(addr) & 0xff", "upper_32_bits(addr)"),
            "double-NOP-count": ("i < count / 2", "i < count"),
        }
        report["sensitivity"] = {}
        for name, (old, new) in mutations.items():
            if generated.count(old) != 1:
                raise ValueError("Sensitivity anchor changed: " + name)
            executable = build(name, generated.replace(old, new, 1), ["-O2"])
            result = command([str(executable)])
            if result["returncode"] != 2:
                raise RuntimeError("Mutation not detected: " + name)
            report["sensitivity"][name] = "DETECTED"
        report["status"] = "PASS_CPU_PACKET_CHECKS"
    except (Exception, KeyboardInterrupt) as error:
        report["error"] = str(error) or type(error).__name__
    report["inputs_unchanged"] = all(path.read_bytes() == raw[name] for name, path in paths.items())
    if not report["inputs_unchanged"]:
        report.update(status="FAIL_PACKET_CHECKS", error="Input changed during checks")
    report["limits"] = ["Extracted actual callbacks and kernel writer with recording CPU ring stubs",
                        "Synthetic 40-bit/native-VRAM/GART-shaped values, not actual allocated addresses",
                        "Odd NOP count/WPTR and 64-bit fence flag are WARN-only, not rejected by callbacks",
                        "No address admission, MMIO, hardware visibility, DMA, firmware or decode acceptance"]
    report["artifacts"] = {p.name: sha(p.read_bytes()) for p in sorted(output.iterdir()) if p.is_file()}
    (output / "RECEIPT.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--kernel-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cc", default=shutil.which("cc") or "cc")
    arguments = parser.parse_args()
    try:
        receipt = run(arguments)
        print(json.dumps({"status": receipt["status"], "receipt": str(arguments.output.resolve() / "RECEIPT.json"),
                          "source_sha256": receipt["inputs"]["runtime.c"]["sha256"]}, indent=2))
        sys.exit(0 if receipt["status"] == "PASS_CPU_PACKET_CHECKS" else 1)
    except Exception as error:
        print(json.dumps({"status": "FAIL_PACKET_CHECKS", "error": str(error)}))
        sys.exit(1)
