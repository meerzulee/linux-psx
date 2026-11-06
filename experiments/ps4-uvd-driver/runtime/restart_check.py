#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Apply the retained-restart patch to new copies; test its actual C control flow.

The supplied exact dev4 tree is read only. No device, DMA, ICC, concurrency or
physical restart is emulated or accepted. All generated files stay in NEW output.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parent
PREFIX = Path('drivers/gpu/drm/amd/amdgpu')
POLICY = {
    'parameter': 'amdgpu.ps4_uvd_poff', 'default': 0, 'test_value': 1, 'read_only': True,
    'system_restart_only': True, 'started_runtime_required': True,
    'initialized_open_jobs_required': True, 'healthy_runtime_required': True,
    'first_close_required': True, 'retain_dma': True, 'forced_restart_refused': True,
    'kexec_refused': True, 'poweroff_semantics_unchanged': True,
    'suspend_remove_reset_guards_unchanged': True, 'hardware_acceptance': False,
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def whole(source, name):
    matches = re.findall(r'(?ms)^(?:static\s+)?(?:bool|void)\s+' + re.escape(name)
                         + r'\([^;{]*\)\n\{.*?^\}\n', source)
    if len(matches) != 1:
        raise ValueError('Nonunique complete function: ' + name)
    return matches[0]


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
#define WRITE_ONCE(x, value) ((x) = (value))
#define CHIP_LIVERPOOL 1
#define PP_MP1_STATE_NONE 0
#define PP_MP1_STATE_UNLOAD 1
@STATES@
static enum system_states system_state;
static bool reboot_force, kexec_in_progress;
@PARAMETERS@
struct amdgpu_ps4_uvd_jobs {
    bool initialized, closed;
    int lock, error;
};
struct amdgpu_ps4_uvd_runtime {
    struct { bool entered; } memory;
    struct amdgpu_ps4_uvd_jobs jobs;
    int failed, publish_lock, poll_work;
    bool started, poll_initialized;
};
struct amdgpu_device {
    int asic_type, dev, mp1_state;
    bool in_s4, in_suspend;
    struct amdgpu_ps4_uvd_runtime ps4_uvd_runtime;
    struct { struct { struct { struct { bool ready; } sched; } ring; } inst[1]; } uvd;
    unsigned char retained_backing[64];
};
struct drm_device { struct amdgpu_device *adev; };
struct pci_dev { struct drm_device *drm; };
static struct amdgpu_device device;
static struct drm_device drm = { &device };
static struct pci_dev pci = { &drm };
static int ps4_uvd_retained_wait;
static jmp_buf parked;
static unsigned closes, poff_markers, restart_markers, park_markers, parks;
static unsigned prepares, suspends, cancels, job_faults, cases;
static unsigned depth;
static int *held[2];
static bool inject_runtime_failure, inject_job_failure;

static void fake_lock(int *lock, unsigned long *flags)
{
    assert(!*lock && depth < 2);
    *flags = depth;
    if (lock == &device.ps4_uvd_runtime.jobs.lock) {
        assert(!depth); /* No publish_lock -> jobs.lock nesting. */
        if (inject_job_failure) {
            device.ps4_uvd_runtime.jobs.closed = true;
            device.ps4_uvd_runtime.jobs.error = -EIO;
            inject_job_failure = false;
        }
    } else {
        assert(lock == &device.ps4_uvd_runtime.publish_lock);
        if (depth) assert(held[0] == &device.ps4_uvd_runtime.jobs.lock);
        if (inject_runtime_failure) {
            device.ps4_uvd_runtime.failed = -EIO;
            inject_runtime_failure = false;
        }
    }
    held[depth++] = lock;
    *lock = 1;
}
static void fake_unlock(int *lock, unsigned long flags)
{
    assert(depth && held[depth - 1] == lock && *lock);
    *lock = 0;
    --depth;
    assert(flags == depth);
}
#define spin_lock_irqsave(lock, flags) fake_lock((lock), &(flags))
#define spin_unlock_irqrestore(lock, flags) fake_unlock((lock), (flags))
static int atomic_cmpxchg(int *value, int expected, int replacement)
{
    int previous = *value;
    assert(depth && held[depth - 1] == &device.ps4_uvd_runtime.publish_lock);
    if (previous == expected) *value = replacement;
    return previous;
}
static void cancel_delayed_work(int *work)
{
    assert(!depth && work == &device.ps4_uvd_runtime.poll_work);
    ++cancels;
}
static void err_marker(int unused, const char *message, ...)
{
    (void)unused;
    assert(!depth);
    if (strstr(message, "CLOSED error=")) ++closes;
    else { assert(strstr(message, "RETAINED reason=")); ++park_markers; }
}
static void warn_marker(int unused, const char *message)
{
    (void)unused;
    assert(!depth && !prepares && !suspends);
    assert(strstr(message, "retained=1 teardown=0"));
    if (strstr(message, "EXPERIMENTAL_POWEROFF")) {
        assert(system_state == SYSTEM_POWER_OFF);
        ++poff_markers;
    } else {
        assert(strstr(message, "EXPERIMENTAL_RESTART"));
        assert(system_state == SYSTEM_RESTART);
        ++restart_markers;
    }
}
#define dev_err err_marker
#define dev_warn warn_marker
#define wait_event_idle(queue, condition) do { \
    (void)(queue); assert(!(condition) && !depth); ++parks; \
    longjmp(parked, 1); \
} while (0)
static void ps4_uvd_close(void *arg, int error);
/* Explicit jobs API stub: production jobs_fault closes/quarantines under its
 * own lock, releases it before the callback, and may schedule its reaper.
 * This fixture does not model leases, reaping, DMA or kernel workqueues.
 */
static void amdgpu_ps4_uvd_jobs_fault(struct amdgpu_ps4_uvd_jobs *jobs, int error)
{
    bool first;
    unsigned long flags;
    assert(!depth && jobs->initialized);
    ++job_faults;
    spin_lock_irqsave(&jobs->lock, flags);
    first = !jobs->closed;
    jobs->closed = true;
    if (!jobs->error) jobs->error = error;
    spin_unlock_irqrestore(&jobs->lock, flags);
    if (first) ps4_uvd_close(&device, error);
}
static void *pci_get_drvdata(struct pci_dev *p) { return p->drm; }
static struct amdgpu_device *drm_to_adev(struct drm_device *d) { return d->adev; }
static bool amdgpu_ras_intr_triggered(void) { return false; }
static bool amdgpu_passthrough(struct amdgpu_device *d) { (void)d; return false; }
static void amdgpu_device_prepare(struct drm_device *d) { (void)d; ++prepares; }
static void amdgpu_device_suspend(struct drm_device *d, bool shutdown)
{ (void)d; assert(shutdown); ++suspends; }
@FUNCTIONS@

static void reset_case(unsigned state, unsigned bits)
{
    assert(!depth);
    memset(&device, 0, sizeof(device));
    memset(device.retained_backing, 0x5a, sizeof(device.retained_backing));
    system_state = state;
    ps4_uvd_runtime = !!(bits & 1);
    device.ps4_uvd_runtime.memory.entered = !!(bits & 2);
    device.asic_type = bits & 4 ? CHIP_LIVERPOOL : 2;
    ps4_uvd_poff = !!(bits & 8);
    reboot_force = !!(bits & 16);
    kexec_in_progress = !!(bits & 32);
    device.ps4_uvd_runtime.failed = bits & 64 ? -EIO : 0;
    device.ps4_uvd_runtime.started = !!(bits & 128);
    device.ps4_uvd_runtime.jobs.initialized = !!(bits & 256);
    device.ps4_uvd_runtime.jobs.closed = !!(bits & 512);
    device.ps4_uvd_runtime.jobs.error = bits & 512 ? -EIO : 0;
    device.ps4_uvd_runtime.poll_initialized = !!(bits & 1024);
    device.uvd.inst[0].ring.sched.ready = true;
    closes = poff_markers = restart_markers = park_markers = parks = 0;
    prepares = suspends = cancels = job_faults = 0;
    inject_runtime_failure = inject_job_failure = false;
}

static void check_case(unsigned state, unsigned bits)
{
    struct amdgpu_device original;
    volatile bool retained = CONFIG_DRM_AMDGPU && CONFIG_X86_PS4 && (bits & 1) && (bits & 2) && (bits & 4);
    volatile bool poweroff = retained && state == SYSTEM_POWER_OFF && (bits & 8) && !(bits & 16) && !(bits & 32);
    volatile bool restart = retained && state == SYSTEM_RESTART && (bits & 8) && !(bits & 16) && !(bits & 32) &&
        !(bits & 64) && (bits & 128) && (bits & 256) && !(bits & 512);
    reset_case(state, bits);
    original = device;
    assert(amdgpu_ps4_uvd_runtime_poweroff(&device) == poweroff);
    assert(poff_markers == poweroff && !restart_markers && !depth);
    if (!poweroff) assert(!memcmp(&original, &device, sizeof(device)));
    else {
        assert(device.ps4_uvd_runtime.failed == (bits & 64 ? -EIO : -ESHUTDOWN));
        assert(closes == !(bits & 64));
        assert(job_faults == !!(bits & 256));
    }

    reset_case(state, bits);
    original = device;
    assert(amdgpu_ps4_uvd_runtime_restart(&device) == restart);
    assert(restart_markers == restart && !poff_markers && !depth);
    if (!restart) assert(!memcmp(&original, &device, sizeof(device)));
    else {
        assert(device.ps4_uvd_runtime.failed == -ESHUTDOWN);
        assert(!device.uvd.inst[0].ring.sched.ready);
        assert(device.ps4_uvd_runtime.jobs.closed && closes == 1 && job_faults == 1);
        assert(cancels == !!(bits & 1024));
        assert(!amdgpu_ps4_uvd_runtime_restart(&device)); /* One first-close owner. */
        assert(closes == 1 && job_faults == 1 && restart_markers == 1);
    }
    assert(!memcmp(original.retained_backing, device.retained_backing, sizeof(device.retained_backing)));

    reset_case(state, bits);
    original = device;
    if (!setjmp(parked)) amdgpu_pci_shutdown(&pci);
    assert(!depth);
    assert(poff_markers == poweroff && restart_markers == restart);
    assert(parks == (retained && !poweroff && !restart) && park_markers == parks);
    assert(prepares == !retained && suspends == !retained);
    assert(device.mp1_state == PP_MP1_STATE_NONE);
    assert(!memcmp(original.retained_backing, device.retained_backing, sizeof(device.retained_backing)));
    if (retained) {
        assert(device.ps4_uvd_runtime.failed == (bits & 64 ? -EIO : -ESHUTDOWN));
        assert(closes == !(bits & 64));
        assert(cancels == (!(bits & 64) && (bits & 1024)));
        if (bits & 256) assert(device.ps4_uvd_runtime.jobs.closed);
    }
    ++cases;
}

static void check_fault_interleavings(void)
{
    const unsigned healthy = 1 | 2 | 4 | 8 | 128 | 256 | 1024;
    if (!(CONFIG_DRM_AMDGPU && CONFIG_X86_PS4)) return;
    reset_case(SYSTEM_RESTART, healthy);
    inject_runtime_failure = true; /* A fault wins just before publication closure. */
    assert(!amdgpu_ps4_uvd_runtime_restart(&device));
    assert(device.ps4_uvd_runtime.failed == -EIO && !restart_markers && !closes && !depth);
    if (!setjmp(parked)) amdgpu_pci_shutdown(&pci);
    assert(parks == 1 && !restart_markers && device.ps4_uvd_runtime.failed == -EIO);

    reset_case(SYSTEM_RESTART, healthy);
    inject_job_failure = true; /* Closed jobs, before its runtime callback arrives. */
    assert(!amdgpu_ps4_uvd_runtime_restart(&device));
    assert(device.ps4_uvd_runtime.jobs.closed && device.ps4_uvd_runtime.failed == 0);
    assert(!restart_markers && !closes && !depth);
    if (!setjmp(parked)) amdgpu_pci_shutdown(&pci);
    assert(parks == 1 && !restart_markers && device.ps4_uvd_runtime.jobs.error == -EIO);
}

int main(void)
{
    unsigned state, bits;
    assert(!ps4_uvd_poff && !ps4_uvd_runtime);
    for (state = SYSTEM_BOOTING; state <= SYSTEM_SUSPEND; ++state)
        for (bits = 0; bits < 2048; ++bits) check_case(state, bits);
    check_fault_interleavings();
    printf("PASS retained restart control flow: %u cases; fault interleavings checked\n", cases);
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-source', type=Path, required=True, help='read-only exact dev4 source tree')
    parser.add_argument('--output', type=Path, required=True, help='NEW host-only output directory')
    parser.add_argument('--cc', default=os.environ.get('CC', 'cc'))
    args = parser.parse_args()
    base = args.kernel_source.resolve(strict=True)
    output = args.output.absolute()
    if output.parent.resolve(strict=True) != output.parent or output.is_relative_to(base) or base.is_relative_to(output):
        raise ValueError('New canonical output outside preserved input required')
    output.mkdir(mode=0o700, exist_ok=False)
    report = {'status': 'FAIL', 'hardware_access': False, 'hardware_acceptance': False,
              'scope': 'actual-source deterministic CPU control flow; no concurrency/DMA model',
              'uvd_restart_experiment': POLICY, 'checks': []}
    try:
        patch = (HERE / 'retained-restart.patch').read_bytes()
        pins = dict((name, digest) for digest, name in re.findall(
            r'^Base-SHA256: ([0-9a-f]{64})  ([^\n]+)$', patch.decode(), re.M))
        expected_names = {str(PREFIX / name) for name in ('amdgpu_ps4_uvd_runtime.c',
                         'amdgpu_ps4_uvd_runtime.h', 'amdgpu_drv.c')}
        if set(pins) != expected_names:
            raise ValueError('Patch base inventory differs')
        report['base_source_sha256'] = pins
        report['patch_sha256'] = sha(patch)
        report['checker_sha256'] = sha(Path(__file__).read_bytes())
        staged = output / 'source'
        before = {}
        for name, expected in pins.items():
            path = base / name
            if path.is_symlink() or not path.is_file(): raise ValueError('Nonregular input: ' + name)
            raw = path.read_bytes()
            if sha(raw) != expected: raise ValueError('Frozen dev4 input differs: ' + name)
            before[name] = raw.decode()
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        saved_patch = output / 'retained-restart.patch'
        saved_patch.write_bytes(patch)
        for flags in (['--check'], ['--whitespace=error']):
            run = subprocess.run(['git', 'apply', *flags, str(saved_patch)], cwd=staged,
                                 capture_output=True, timeout=30)
            report['checks'].append({'command': ['git', 'apply', *flags, str(saved_patch)],
                                     'returncode': run.returncode, 'stderr': run.stderr.decode(errors='replace')})
            if run.returncode: raise ValueError('Exact patch application failed')
        after = {name: (staged / name).read_text() for name in pins}
        runtime_name, driver_name = str(PREFIX / 'amdgpu_ps4_uvd_runtime.c'), str(PREFIX / 'amdgpu_drv.c')
        runtime, driver = after[runtime_name], after[driver_name]
        if whole(before[runtime_name], 'amdgpu_ps4_uvd_runtime_poweroff') != whole(runtime, 'amdgpu_ps4_uvd_runtime_poweroff'):
            raise ValueError('Historical POWER_OFF function changed')
        old_call = '\t\tif (amdgpu_ps4_uvd_runtime_poweroff(adev))\n'
        new_call = '\t\tif (amdgpu_ps4_uvd_runtime_poweroff(adev) ||\n\t\t    amdgpu_ps4_uvd_runtime_restart(adev))\n'
        if before[driver_name].count(old_call) != 1 or driver != before[driver_name].replace(old_call, new_call, 1):
            raise ValueError('PCI source changed outside the one shutdown predicate')
        for name in ('amdgpu_ps4_uvd_runtime_enabled', 'amdgpu_ps4_uvd_runtime_retain',
                     'amdgpu_ps4_uvd_runtime_fault', 'amdgpu_ps4_uvd_runtime_park'):
            if whole(before[runtime_name], name) != whole(runtime, name):
                raise ValueError('Existing runtime boundary changed: ' + name)
        parameters = re.findall(r'(?m)^static bool ps4_uvd_(?:runtime|poff)[^;]*;', runtime)
        if parameters != ['static bool ps4_uvd_runtime;', 'static bool ps4_uvd_poff __ro_after_init;']:
            raise ValueError('Gate defaults/immutability changed')
        if runtime.count('module_param_named(ps4_uvd_poff, ps4_uvd_poff, bool, 0444);') != 1:
            raise ValueError('Opt-in must remain read only')
        functions = '\n'.join(whole(runtime, name) for name in (
            'amdgpu_ps4_uvd_runtime_enabled', 'amdgpu_ps4_uvd_runtime_retain',
            'ps4_uvd_begin_close', 'ps4_uvd_finish_close', 'ps4_uvd_close',
            'amdgpu_ps4_uvd_runtime_fault', 'amdgpu_ps4_uvd_runtime_poweroff',
            'amdgpu_ps4_uvd_runtime_restart', 'amdgpu_ps4_uvd_runtime_park'))
        functions += whole(driver, 'amdgpu_pci_shutdown')
        kernel_header = (base / 'include/linux/kernel.h').read_bytes()
        states, = re.findall(r'(?ms)^enum system_states \{.*?^\};', kernel_header.decode())
        fixture = FIXTURE.replace('@STATES@', states).replace('@PARAMETERS@', '\n'.join(parameters))
        fixture = fixture.replace('@FUNCTIONS@', functions)
        source = output / 'restart-fixture.c'
        source.write_text(fixture)
        report['kernel_header_sha256'] = sha(kernel_header)
        report['actual_functions_sha256'] = sha(functions.encode())
        report['patched_source_sha256'] = {name: sha(text.encode()) for name, text in after.items()}
        for builtin, ps4 in ((1, 1), (0, 1), (1, 0), (0, 0)):
            binary = output / f'restart-{builtin}-{ps4}'
            command = [args.cc, '-std=gnu11', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                       '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                       '-fno-omit-frame-pointer', f'-DCONFIG_DRM_AMDGPU={builtin}',
                       f'-DCONFIG_X86_PS4={ps4}', str(source), '-o', str(binary)]
            compiled = subprocess.run(command, capture_output=True, timeout=60)
            (output / (binary.name + '.compile.log')).write_bytes(compiled.stdout + compiled.stderr)
            if compiled.returncode: raise ValueError('Fixture compilation failed: ' + binary.name)
            ran = subprocess.run([str(binary)], capture_output=True, timeout=15)
            (output / (binary.name + '.run.log')).write_bytes(ran.stdout + ran.stderr)
            report['checks'].append({'command': command, 'returncode': ran.returncode,
                                     'stdout': ran.stdout.decode(errors='replace')})
            if ran.returncode or ran.stdout != b'PASS retained restart control flow: 16384 cases; fault interleavings checked\n':
                raise ValueError('Control-flow regression: ' + binary.name)
        # Confirm every input is still exact after patching/testing only copies.
        for name, expected in pins.items():
            if sha((base / name).read_bytes()) != expected: raise ValueError('Preserved input changed: ' + name)
        report['status'] = 'PASS_CPU_RETAINED_RESTART_CONTROL_FLOW'
    except Exception as error:
        report['error'] = str(error)
    (output / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'PASS_CPU_RETAINED_RESTART_CONTROL_FLOW' else 1


if __name__ == '__main__':
    raise SystemExit(main())
