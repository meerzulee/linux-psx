#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CPU-only gate/control-flow checks of the actual UVD power-off source.

Extracts complete functions and the generated PCI callback into API stubs.
No device, DMA, concurrency, ICC or actual power-off acceptance is claimed.
The supplied kernel tree is read only; every generated input stays in NEW output.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parent
PREFIX = Path("drivers/gpu/drm/amd/amdgpu")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def whole(source, name):
    found = re.findall(r"(?ms)^(?:static\s+)?(?:bool|void)\s+" + re.escape(name)
                       + r"\([^;{]*\)\n\{.*?^\}\n", source)
    if len(found) != 1:
        raise ValueError("nonunique whole function: " + name)
    return found[0]


FIXTURE = r'''
#include <assert.h>
#include <errno.h>
#include <setjmp.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define __ro_after_init
#define IS_BUILTIN(x) (x)
#define IS_ENABLED(x) (x)
#define READ_ONCE(x) (x)
#define CHIP_LIVERPOOL 1
#define PP_MP1_STATE_NONE 0
#define PP_MP1_STATE_UNLOAD 1
@STATES@
static enum system_states system_state;
static bool reboot_force, kexec_in_progress;
@PARAMETERS@
struct amdgpu_device {
    int asic_type, dev, mp1_state, failure;
    bool in_s4, in_suspend;
    struct { struct { bool entered; } memory; } ps4_uvd_runtime;
    unsigned char retained_backing[64];
};
struct drm_device { struct amdgpu_device *adev; };
struct pci_dev { struct drm_device *drm; };
static struct amdgpu_device device;
static struct drm_device drm = { &device };
static struct pci_dev pci = { &drm };
static jmp_buf parked;
static unsigned faults, markers, parks, prepares, suspends, cases;
static void *pci_get_drvdata(struct pci_dev *p) { return p->drm; }
static struct amdgpu_device *drm_to_adev(struct drm_device *d) { return d->adev; }
static bool amdgpu_ras_intr_triggered(void) { return false; }
static bool amdgpu_passthrough(struct amdgpu_device *d) { (void)d; return false; }
static void amdgpu_device_prepare(struct drm_device *d) { (void)d; ++prepares; }
static void amdgpu_device_suspend(struct drm_device *d, bool shutdown)
{ (void)d; assert(shutdown); ++suspends; }
static void amdgpu_ps4_uvd_runtime_fault(struct amdgpu_device *d, int error)
{
    assert(error == -ESHUTDOWN && !prepares && !suspends && !markers);
    if (!d->failure) d->failure = error;
    ++faults;
}
static void marker(int ignored, const char *message)
{
    (void)ignored;
    assert(faults == 1 && !parks && !prepares && !suspends);
    assert(strstr(message, "EXPERIMENTAL_POWEROFF retained=1 teardown=0"));
    ++markers;
}
#define dev_warn marker
static void amdgpu_ps4_uvd_runtime_park(struct amdgpu_device *d, const char *why)
{
    (void)d;
    assert(!strcmp(why, "amdgpu_pci_shutdown"));
    ++parks;
    longjmp(parked, 1); /* Model the terminal park without sleeping. */
}
@FUNCTIONS@
static void check_case(unsigned state, unsigned bits)
{
    unsigned char original[64];
    volatile bool retained, allowed;
    memset(&device, 0, sizeof(device));
    memset(device.retained_backing, 0x5a, sizeof(device.retained_backing));
    memcpy(original, device.retained_backing, sizeof(original));
    faults = markers = parks = prepares = suspends = 0;
    system_state = state;
    ps4_uvd_runtime = !!(bits & 1);
    device.ps4_uvd_runtime.memory.entered = !!(bits & 2);
    device.asic_type = bits & 4 ? CHIP_LIVERPOOL : 2;
    ps4_uvd_poff = !!(bits & 8);
    reboot_force = !!(bits & 16);
    kexec_in_progress = !!(bits & 32);
    device.failure = bits & 64 ? -EIO : 0;
    retained = CONFIG_DRM_AMDGPU && CONFIG_X86_PS4 && (bits & 1) &&
               (bits & 2) && (bits & 4);
    allowed = retained && state == SYSTEM_POWER_OFF && (bits & 8) &&
              !(bits & 16) && !(bits & 32);
    /* Exercise the helper directly: even an accidental call on an ordinary
     * device must have no effect. Its only allowed side effect precedes return.
     */
    assert(amdgpu_ps4_uvd_runtime_poweroff(&device) == allowed);
    assert(faults == allowed && markers == allowed);
    assert(device.failure == (bits & 64 ? -EIO : allowed ? -ESHUTDOWN : 0));
    faults = markers = 0;
    device.failure = bits & 64 ? -EIO : 0;
    if (!setjmp(parked)) amdgpu_pci_shutdown(&pci);
    assert(faults == allowed && markers == allowed);
    assert(parks == (retained && !allowed));
    assert(prepares == !retained && suspends == !retained);
    assert(device.mp1_state == PP_MP1_STATE_NONE);
    assert(device.failure == (bits & 64 ? -EIO : allowed ? -ESHUTDOWN : 0));
    assert(!memcmp(original, device.retained_backing, sizeof(original)));
    ++cases;
}
int main(void)
{
    unsigned state, bits;
    assert(!ps4_uvd_poff); /* Actual source declaration defaults off. */
    for (state = SYSTEM_BOOTING; state <= SYSTEM_SUSPEND; ++state)
        for (bits = 0; bits < 128; ++bits) check_case(state, bits);
    printf("PASS power-off control flow: %u cases\n", cases);
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-source", type=Path, required=True,
                        help="read-only frozen dev2 tree, or its new power-off derivative")
    parser.add_argument("--output", type=Path, required=True, help="new host-only directory")
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {"status": "FAIL", "hardware_access": False, "hardware_acceptance": False,
              "scope": "actual-source deterministic CPU control flow only", "checks": []}
    inputs = {"runtime.c": (HERE / "amdgpu_ps4_uvd_runtime.c").read_bytes(),
              "runtime.h": (HERE / "amdgpu_ps4_uvd_runtime.h").read_bytes(),
              "builder.py": (HERE / "build_candidate.py").read_bytes(),
              "driver.c": (args.kernel_source / PREFIX / "amdgpu_drv.c").read_bytes(),
              "kernel.h": (args.kernel_source / "include/linux/kernel.h").read_bytes()}
    try:
        for name, raw in inputs.items(): (args.output / name).write_bytes(raw)
        report["inputs_sha256"] = {name: sha(raw) for name, raw in inputs.items()}
        report["checker_sha256"] = sha(Path(__file__).read_bytes())
        runtime = inputs["runtime.c"].decode()
        parameters = re.findall(r"(?m)^static bool ps4_uvd_(?:runtime|poff)[^;]*;", runtime)
        if parameters != ["static bool ps4_uvd_runtime;", "static bool ps4_uvd_poff __ro_after_init;"]:
            raise ValueError("gate declaration/default or immutability changed")
        if runtime.count("module_param_named(ps4_uvd_poff, ps4_uvd_poff, bool, 0444);") != 1:
            raise ValueError("power-off parameter must remain read only")
        spec = importlib.util.spec_from_file_location("poweroff_generator", HERE / "build_candidate.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        driver = inputs["driver.c"].decode()
        old = ('\n\tif (amdgpu_ps4_uvd_runtime_retain(adev))\n'
               '\t\tamdgpu_ps4_uvd_runtime_park(adev, "amdgpu_pci_shutdown");\n')
        guard = builder.pci_shutdown_retention_guard()
        if old in whole(driver, "amdgpu_pci_shutdown"):
            driver = builder.in_function(driver, "amdgpu_pci_shutdown", old, guard)
        elif whole(driver, "amdgpu_pci_shutdown").count(guard) != 1:
            raise ValueError("kernel input has neither exact old nor new shutdown guard")
        functions = "\n".join(whole(runtime, name) for name in (
            "amdgpu_ps4_uvd_runtime_enabled", "amdgpu_ps4_uvd_runtime_retain",
            "amdgpu_ps4_uvd_runtime_poweroff")) + whole(driver, "amdgpu_pci_shutdown")
        states, = re.findall(r"(?ms)^enum system_states \{.*?^\};", inputs["kernel.h"].decode())
        fixture = FIXTURE.replace("@STATES@", states).replace("@PARAMETERS@", "\n".join(parameters))
        fixture = fixture.replace("@FUNCTIONS@", functions)
        source = args.output / "poweroff-fixture.c"
        source.write_text(fixture)
        report["actual_functions_sha256"] = sha(functions.encode())
        # Independent platform/build configurations and every gate/state/fault
        # combination, including already-failed queues and no hardware entry.
        for builtin, ps4 in ((1, 1), (0, 1), (1, 0), (0, 0)):
            binary = args.output / f"poweroff-{builtin}-{ps4}"
            command = [args.cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
                       "-fsanitize=undefined", "-fno-sanitize-recover=all",
                       f"-DCONFIG_DRM_AMDGPU={builtin}", f"-DCONFIG_X86_PS4={ps4}",
                       str(source), "-o", str(binary)]
            compiled = subprocess.run(command, capture_output=True, timeout=60)
            (args.output / (binary.name + ".compile.log")).write_bytes(compiled.stdout + compiled.stderr)
            if compiled.returncode: raise ValueError("fixture compilation failed: " + binary.name)
            ran = subprocess.run([str(binary)], capture_output=True, timeout=10)
            (args.output / (binary.name + ".run.log")).write_bytes(ran.stdout + ran.stderr)
            report["checks"].append({"command": command, "returncode": ran.returncode,
                                     "stdout": ran.stdout.decode(errors="replace")})
            if ran.returncode or ran.stdout != b"PASS power-off control flow: 1024 cases\n":
                raise ValueError("control-flow regression: " + binary.name)
        report["status"] = "PASS_CPU_POWEROFF_CONTROL_FLOW"
    except Exception as exc:
        report["error"] = str(exc)
    (args.output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS_CPU_POWEROFF_CONTROL_FLOW" else 1


if __name__ == "__main__":
    raise SystemExit(main())
