#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the dev6 terminal-restart delta against exact, read-only dev5 inputs.

Only NEW private host files are created. Actual C callbacks run against explicit
CPU/API fixtures, not hardware, DMA, real concurrency or a complete kernel.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PREFIX = Path('drivers/gpu/drm/amd/amdgpu')
PINS = {
    'amdgpu_ps4_uvd_runtime.c': '685e10d862634337217b4647b960b8b0e4613a95cceb59589e013d081fe5f534',
    'amdgpu_ps4_uvd_runtime.h': 'acfac4bddfa8dd77a852bbf81aaeb5e21c24b280dbd926cd83e1dde65f3a507d',
    'amdgpu_drv.c': '07435c1271d24ccd03491036d8ab1cfe007030681a77b7b5a694f24549e9a715',
    'amdgpu_ps4_uvd_jobs.c': '6a5b25a0dea85930d64716f15df7f83addb6383c7dcb82e1b0a20974c1c950e9',
}
PREVIOUS_PATCH_SHA = '0e344be112396dbb49255908466de9eb751180589efe0c6c9e9d0b0ed959af79'
FIXTURE_SHA = 'd8838cbb788229718a981b3df2c9c5210698d6e0853dadd62266c074ed23067c'
POLICY = {
    'parameter': 'amdgpu.ps4_uvd_poff', 'default': 0, 'test_value': 1, 'read_only': True,
    'system_restart_only': True, 'started_runtime_required': True,
    'initialized_open_jobs_required': False, 'healthy_runtime_required': False,
    'first_close_required': False, 'retain_dma': True, 'forced_restart_refused': True,
    'kexec_refused': True, 'poweroff_semantics_unchanged': True,
    'suspend_remove_reset_guards_unchanged': True, 'hardware_acceptance': False,
    'initialized_jobs_required': True, 'post_start_failure_permitted': True,
    'startup_hw_init_completed_required': True,
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Nonunique fixture boundary: ' + before[:80])
    return text.replace(before, after, 1)


CASES = r'''
static void check_case(unsigned state, unsigned bits)
{
    struct amdgpu_device original;
    volatile bool retained = CONFIG_DRM_AMDGPU && CONFIG_X86_PS4 && (bits & 1) && (bits & 2) && (bits & 4);
    volatile bool poweroff = retained && state == SYSTEM_POWER_OFF && (bits & 8) && !(bits & 16) && !(bits & 32);
    volatile bool restart = retained && state == SYSTEM_RESTART && (bits & 8) && !(bits & 16) && !(bits & 32) &&
        (bits & 128) && (bits & 256) && (bits & 2048) && !(bits & 4096) && !(bits & 8192);
    reset_case(state, bits);
    original = device;
    assert(amdgpu_ps4_uvd_runtime_poweroff(&device) == poweroff);
    assert(poff_markers == poweroff && !restart_markers && !depth);
    if (!poweroff) assert(!memcmp(&original, &device, sizeof(device)));
    reset_case(state, bits);
    original = device;
    assert(amdgpu_ps4_uvd_runtime_restart(&device) == restart);
    assert(restart_markers == restart && !poff_markers && !depth);
    if (!restart) assert(!memcmp(&original, &device, sizeof(device)));
    else {
        assert(device.ps4_uvd_runtime.failed == (bits & 64 ? -EIO : -ESHUTDOWN));
        assert(device.ps4_uvd_runtime.jobs.error == (bits & 512 ? -EIO : -ESHUTDOWN));
        assert(device.ps4_uvd_runtime.jobs.closed && job_faults == 1);
        assert(closes == !(bits & 64));
        assert(cancels == (!(bits & 64) && (bits & 1024)));
        assert(leases[0].quarantined && !leases[1].quarantined);
        assert(device.ps4_uvd_runtime.jobs.quarantined == 1);
        ps4_uvd_set_wptr(&device.uvd.inst[0].ring);
        assert(!hardware_writes);
        assert(amdgpu_ps4_uvd_runtime_restart(&device)); /* Idempotent, not a second hardware send. */
        assert(closes == !(bits & 64) && job_faults == 2 && restart_markers == 2);
        assert(device.ps4_uvd_runtime.jobs.quarantined == 1);
    }
    assert(!memcmp(original.retained_backing, device.retained_backing, sizeof(device.retained_backing)));
    assert(!memcmp(leases[0].backing, saved_backing[0], sizeof(saved_backing[0])));
    assert(!memcmp(leases[1].backing, saved_backing[1], sizeof(saved_backing[1])));
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
        if (bits & 256) {
            assert(device.ps4_uvd_runtime.jobs.closed && leases[0].quarantined);
            assert(device.ps4_uvd_runtime.jobs.error == (bits & 512 ? -EIO : -ESHUTDOWN));
        }
    }
    ++cases;
}
'''

INTERLEAVINGS = r'''
static void check_fault_interleavings(void)
{
    const unsigned healthy = 1 | 2 | 4 | 8 | 128 | 256 | 1024 | 2048;
    if (!(CONFIG_DRM_AMDGPU && CONFIG_X86_PS4)) return;
    reset_case(SYSTEM_RESTART, healthy);
    inject_runtime_failure = true; /* Another close wins before our publication lock. */
    assert(amdgpu_ps4_uvd_runtime_restart(&device));
    assert(device.ps4_uvd_runtime.failed == -EIO && restart_markers == 1 && !closes && !depth);
    assert(leases[0].quarantined && device.ps4_uvd_runtime.jobs.closed);
    ps4_uvd_set_wptr(&device.uvd.inst[0].ring);
    assert(!hardware_writes);
    reset_case(SYSTEM_RESTART, healthy);
    inject_job_failure = true; /* Job error arrives before its runtime callback. */
    assert(amdgpu_ps4_uvd_runtime_restart(&device));
    assert(device.ps4_uvd_runtime.jobs.closed && device.ps4_uvd_runtime.jobs.error == -EIO);
    assert(device.ps4_uvd_runtime.failed == -ESHUTDOWN && leases[0].quarantined);
    assert(restart_markers == 1 && closes == 1 && !depth);
    ps4_uvd_set_wptr(&device.uvd.inst[0].ring);
    assert(!hardware_writes);
    reset_case(SYSTEM_RESTART, healthy & ~2048); /* started before failed boot ring test. */
    device.ps4_uvd_runtime.failed = -ETIMEDOUT;
    assert(!amdgpu_ps4_uvd_runtime_restart(&device));
    assert(device.ps4_uvd_runtime.failed == -ETIMEDOUT && !restart_markers && !job_faults);
    if (!setjmp(parked)) amdgpu_pci_shutdown(&pci);
    assert(parks == 1 && !restart_markers && device.ps4_uvd_runtime.failed == -ETIMEDOUT);
}
'''


def fixture(previous, runtime, driver, jobs, ip_source, states):
    text = previous.FIXTURE
    text = replace_once(text, 'struct amdgpu_ps4_uvd_jobs {', 'struct amdgpu_ps4_uvd_lease;\nstruct amdgpu_ps4_uvd_jobs {')
    text = replace_once(text, 'int lock, error;', '''int lock, error, reap_work;
    unsigned quarantined;
    struct amdgpu_ps4_uvd_lease *leases;
    void (*close)(void *, int);
    void *close_arg;''')
    text = replace_once(text, 'struct amdgpu_device {', '''
struct amdgpu_device;
struct amdgpu_ring { struct amdgpu_device *adev; struct { bool ready; } sched; unsigned wptr; };
enum amd_ip_block_type { AMD_IP_BLOCK_TYPE_UVD = 1 };
struct amdgpu_ip_block_version { enum amd_ip_block_type type; };
struct amdgpu_ip_block { struct { bool hw; } status; const struct amdgpu_ip_block_version *version; };
static const struct amdgpu_ip_block_version ps4_uvd_ip_block = { AMD_IP_BLOCK_TYPE_UVD };
static const struct amdgpu_ip_block_version wrong_uvd_ip_block = { AMD_IP_BLOCK_TYPE_UVD };
struct amdgpu_ps4_uvd_lease {
    struct amdgpu_ps4_uvd_jobs *state;
    struct amdgpu_ps4_uvd_lease *next;
    bool entered, quarantined;
    unsigned char backing[64];
};
static struct amdgpu_ps4_uvd_lease leases[2];
static unsigned char saved_backing[2][64];
struct amdgpu_device {
    int num_ip_blocks;
    struct amdgpu_ip_block ip_blocks[1];''')
    text = replace_once(text, 'int failed, publish_lock, poll_work;', 'int failed, publish_lock, poll_work;\n    unsigned long last_publish_ns;')
    text = replace_once(text, 'struct { struct { struct { struct { bool ready; } sched; } ring; } inst[1]; } uvd;',
                        'struct { struct { struct amdgpu_ring ring; } inst[1]; } uvd;')
    text = replace_once(text, 'static unsigned depth;', 'static unsigned depth, hardware_writes;')
    begin = text.index('/* Explicit jobs API stub:')
    end = text.index('static void *pci_get_drvdata', begin)
    source = '''/* Execute the production fault/quarantine functions; list/workqueue APIs remain CPU fixtures. */
#define list_for_each_entry(cursor, head, member) for ((cursor) = *(head); (cursor); (cursor) = (cursor)->next)
static void schedule_work(int *work) { assert(!depth && work == &device.ps4_uvd_runtime.jobs.reap_work); ++job_faults; }
'''
    source += previous.whole(jobs, 'ps4_uvd_quarantine') + previous.whole(jobs, 'amdgpu_ps4_uvd_jobs_fault')
    source += '''#define atomic_read(value) (*(value))
#define mb() ((void)0)
#define WREG32(reg, value) ((void)(reg), (void)(value), ++hardware_writes)
#define mmUVD_RBC_RB_WPTR 0
#define lower_32_bits(value) (value)
#define system_wq 0
#define msecs_to_jiffies(value) (value)
static unsigned long ktime_get_ns(void) { return 1; }
static void mod_delayed_work(int queue, int *work, int delay) { (void)queue; (void)work; (void)delay; }
'''
    lookup, = re.findall(r'(?ms)^struct amdgpu_ip_block \*\namdgpu_device_ip_get_ip_block\([^;{]*\)\n\{.*?^\}\n', ip_source)
    source += lookup
    text = text[:begin] + source + text[end:]
    added_reset = '''
    memset(leases, 0, sizeof(leases));
    for (unsigned i = 0; i < 2; ++i) {
        leases[i].state = &device.ps4_uvd_runtime.jobs;
        memset(leases[i].backing, 0xa5 + i, sizeof(leases[i].backing));
        memcpy(saved_backing[i], leases[i].backing, sizeof(saved_backing[i]));
    }
    leases[0].entered = true;
    leases[0].next = &leases[1];
    device.ps4_uvd_runtime.jobs.leases = leases;
    device.ps4_uvd_runtime.jobs.close = ps4_uvd_close;
    device.ps4_uvd_runtime.jobs.close_arg = &device;
    device.num_ip_blocks = bits & 4096 ? 0 : 1;
    device.ip_blocks[0].version = bits & 8192 ? &wrong_uvd_ip_block : &ps4_uvd_ip_block;
    device.ip_blocks[0].status.hw = !!(bits & 2048);
    device.uvd.inst[0].ring.adev = &device;
    hardware_writes = 0;
'''
    text = replace_once(text, 'inject_runtime_failure = inject_job_failure = false;\n}',
                        'inject_runtime_failure = inject_job_failure = false;' + added_reset + '\n}')
    text = replace_once(text, previous.whole(text, 'check_case'), CASES.lstrip())
    text = replace_once(text, previous.whole(text, 'check_fault_interleavings'), INTERLEAVINGS.lstrip())
    text = replace_once(text, 'bits < 2048', 'bits < 16384')
    text = replace_once(text, 'PASS retained restart control flow:', 'PASS terminal restart control flow:')
    functions = '\n'.join(previous.whole(runtime, name) for name in (
        'amdgpu_ps4_uvd_runtime_enabled', 'amdgpu_ps4_uvd_runtime_retain',
        'ps4_uvd_begin_close', 'ps4_uvd_finish_close', 'ps4_uvd_close',
        'amdgpu_ps4_uvd_runtime_fault', 'amdgpu_ps4_uvd_runtime_poweroff',
        'amdgpu_ps4_uvd_runtime_restart', 'amdgpu_ps4_uvd_runtime_park', 'ps4_uvd_set_wptr'))
    functions += previous.whole(driver, 'amdgpu_pci_shutdown')
    parameters = re.findall(r'(?m)^static bool ps4_uvd_(?:runtime|poff)[^;]*;', runtime)
    return text.replace('@STATES@', states).replace('@PARAMETERS@', '\n'.join(parameters)).replace('@FUNCTIONS@', functions)


def no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-source', type=Path, required=True, help='exact preserved dev5 source')
    parser.add_argument('--output', type=Path, required=True, help='NEW private host directory')
    parser.add_argument('--cc', default=os.environ.get('CC', 'cc'))
    args = parser.parse_args()
    base = args.kernel_source.resolve(strict=True)
    output = args.output.absolute()
    if output.parent.resolve(strict=True) != output.parent or output.is_relative_to(base) or base.is_relative_to(output):
        raise ValueError('New canonical output outside preserved input required')
    output.mkdir(mode=0o700, exist_ok=False)
    report = {'status': 'FAIL', 'hardware_access': False, 'hardware_acceptance': False,
              'scope': 'actual-source deterministic CPU control flow; no DMA/concurrency emulation',
              'uvd_restart_experiment': POLICY, 'checks': []}
    try:
        previous_path = HERE / 'restart_check.py'
        if sha(previous_path.read_bytes()) != FIXTURE_SHA or sha((HERE / 'retained-restart.patch').read_bytes()) != PREVIOUS_PATCH_SHA:
            raise ValueError('Frozen dev5 patch/fixture changed')
        spec = importlib.util.spec_from_file_location('psx_frozen_restart_fixture', previous_path)
        previous = importlib.util.module_from_spec(spec); spec.loader.exec_module(previous)
        before = {}
        for name, pin in PINS.items():
            path = base / PREFIX / name
            if path.is_symlink() or not path.is_file() or sha(path.read_bytes()) != pin:
                raise ValueError('Frozen dev5 input differs: ' + name)
            before[name] = path.read_text()
        patch = (HERE / 'terminal-restart.patch').read_bytes()
        pins = re.findall(r'^Base-SHA256: ([0-9a-f]{64})  ([^\n]+)$', patch.decode(), re.M)
        if pins != [(PINS['amdgpu_ps4_uvd_runtime.c'], str(PREFIX / 'amdgpu_ps4_uvd_runtime.c'))]:
            raise ValueError('Terminal patch must change only the exact dev5 runtime source')
        staged = output / 'source'; (staged / PREFIX).mkdir(parents=True)
        for name, raw in before.items(): (staged / PREFIX / name).write_text(raw)
        saved_patch = output / 'terminal-restart.patch'; saved_patch.write_bytes(patch)
        applied = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(saved_patch)],
                                 cwd=staged, capture_output=True, timeout=30)
        (output / 'apply.log').write_bytes(applied.stdout + applied.stderr)
        if applied.returncode or b'offset' in applied.stdout or b'fuzz' in applied.stdout:
            raise ValueError('Exact patch application failed')
        after = {name: (staged / PREFIX / name).read_text() for name in before}
        runtime = after['amdgpu_ps4_uvd_runtime.c']
        old_fn = previous.whole(before['amdgpu_ps4_uvd_runtime.c'], 'amdgpu_ps4_uvd_runtime_restart')
        new_fn = previous.whole(runtime, 'amdgpu_ps4_uvd_runtime_restart')
        old_without = before['amdgpu_ps4_uvd_runtime.c'].replace(old_fn, '')
        new_without = runtime.replace(new_fn, '')
        strip_comments = lambda text: re.sub(r'/\*.*?\*/', '', text, flags=re.S)
        if strip_comments(old_without) != strip_comments(new_without):
            raise ValueError('Runtime code changed outside the restart helper')
        if any(before[name] != after[name] for name in before if name != 'amdgpu_ps4_uvd_runtime.c'):
            raise ValueError('Header, driver or jobs source changed')
        for name in ('amdgpu_ps4_uvd_runtime_poweroff', 'amdgpu_ps4_uvd_runtime_fault',
                     'amdgpu_ps4_uvd_runtime_park', 'ps4_uvd_set_wptr'):
            if previous.whole(before['amdgpu_ps4_uvd_runtime.c'], name) != previous.whole(runtime, name):
                raise ValueError('Existing lifecycle/publication body changed: ' + name)
        header = (base / 'include/linux/kernel.h').read_bytes()
        states, = re.findall(r'(?ms)^enum system_states \{.*?^\};', header.decode())
        ip_source = (base / PREFIX / 'amdgpu_ip.c').read_text()
        fixture_text = fixture(previous, runtime, after['amdgpu_drv.c'], after['amdgpu_ps4_uvd_jobs.c'], ip_source, states)
        source = output / 'terminal-restart-fixture.c'; source.write_text(fixture_text)
        report.update(base_source_sha256=PINS, patch_sha256=sha(patch), checker_sha256=sha(Path(__file__).read_bytes()),
            previous_patch_sha256=PREVIOUS_PATCH_SHA, previous_fixture_sha256=FIXTURE_SHA,
            kernel_header_sha256=sha(header), ip_lookup_source_sha256=sha(ip_source.encode()),
            fixture_sha256=sha(fixture_text.encode()), patched_source_sha256={name:sha(raw.encode()) for name,raw in after.items()})
        for builtin, ps4 in ((1,1),(0,1),(1,0),(0,0)):
            binary = output / f'terminal-restart-{builtin}-{ps4}'
            command = [args.cc, '-std=gnu11', '-O2', '-g', '-Wall', '-Wextra', '-Werror',
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-omit-frame-pointer',
                f'-DCONFIG_DRM_AMDGPU={builtin}', f'-DCONFIG_X86_PS4={ps4}', str(source), '-o', str(binary)]
            compiled = subprocess.run(command, capture_output=True, timeout=60, preexec_fn=no_core)
            (output / (binary.name + '.compile.log')).write_bytes(compiled.stdout + compiled.stderr)
            if compiled.returncode: raise ValueError('Fixture compile failed: ' + binary.name)
            ran = subprocess.run([str(binary)], capture_output=True, timeout=15, preexec_fn=no_core)
            (output / (binary.name + '.run.log')).write_bytes(ran.stdout + ran.stderr)
            report['checks'].append({'command':command,'returncode':ran.returncode,'stdout':ran.stdout.decode(errors='replace')})
            if ran.returncode or ran.stdout != b'PASS terminal restart control flow: 131072 cases; fault interleavings checked\n':
                raise ValueError('Terminal control-flow regression: ' + binary.name)
        for name, pin in PINS.items():
            if sha((base / PREFIX / name).read_bytes()) != pin: raise ValueError('Preserved dev5 input changed: ' + name)
        if sha((HERE / 'retained-restart.patch').read_bytes()) != PREVIOUS_PATCH_SHA:
            raise ValueError('Preserved dev5 patch changed')
        report['status'] = 'PASS_CPU_TERMINAL_RESTART_CONTROL_FLOW'
    except Exception as error:
        report['error'] = str(error)
    (output / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'PASS_CPU_TERMINAL_RESTART_CONTROL_FLOW' else 1


if __name__ == '__main__':
    raise SystemExit(main())
