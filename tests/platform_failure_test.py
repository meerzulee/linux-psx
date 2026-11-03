"""Exercise actual platform error paths with no console or MMIO access."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from boot_defaults_test import ROOT, function, postimages

PATCHES = ROOT / "platforms/ps4/patches/7.2-ps4-baikal"


def run_c(source):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "test.c").write_text(source)
        subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", "-Wno-parentheses", str(root / "test.c"), "-o", str(root / "test")], check=True)
        subprocess.run([str(root / "test")], check=True, timeout=5)


class PlatformFailureTests(unittest.TestCase):
    def test_icc_not_ready_and_command_errors_release_both_mutexes(self):
        images = postimages(PATCHES / "0200-ps4-drivers/0002-ps4-icc-unlock-not-ready-command.patch")
        for prefix, lock in (("bpcie", "bcpie_icc_mutex"), ("apcie", "icc_mutex")):
            with self.subTest(controller=prefix):
                source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
#define pr_err(...) ((void)0)
static bool bpcie_initialized;
static void *icc_sc;
static int command_result;
static void mutex_lock(int *lock) { assert(!*lock); *lock = 1; }
static void mutex_unlock(int *lock) { assert(*lock); *lock = 0; }
static int command(void *sc, u8 a, u16 b, const void *c, u16 d, void *e, u16 f, bool g)
{ return command_result; }
'''
                source += f"static int {lock};\n#define _{prefix}_icc_cmd command\n"
                if prefix == "apcie":
                    source += "static int bpcie_icc_cmd(u8 a, u16 b, const void *c, u16 d, void *e, u16 f) { return 77; }\n"
                source += function(images[f"drivers/ps4/ps4-{prefix}-icc.c"], prefix + "_icc_cmd")
                source += f'''
int main(void) {{
    bpcie_initialized = false;
    assert({prefix}_icc_cmd(0,0,NULL,0,NULL,0) == -EAGAIN);
    assert(!{lock});
    assert({prefix}_icc_cmd(0,0,NULL,0,NULL,0) == -EAGAIN);
    icc_sc = &command_result;
    command_result = 19;
    assert({prefix}_icc_cmd(0,0,NULL,0,NULL,0) == 19);
    assert(!{lock});
    command_result = -EIO;
    assert({prefix}_icc_cmd(0,0,NULL,0,NULL,0) == -EIO);
    assert(!{lock});
'''
                if prefix == "apcie":
                    source += "bpcie_initialized = true; assert(apcie_icc_cmd(0,0,NULL,0,NULL,0) == 77); assert(!icc_mutex);\n"
                run_c(source + "return 0; }\n")

    def test_calibration_terminates_on_unstable_or_stopped_timers(self):
        images = postimages(PATCHES / "0100-x86-platform/0002-ps4-bound-emc-calibration-waits.patch")
        text = images["arch/x86/platform/ps4/calibrate.c"]
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define __init
#define current get_current()
#define PS4_DEFAULT_TSC_FREQ 1594000000ULL
#define EMC_TIMER_VALUE 24
#define EMC_TIMER_BASE 0
#define EMC_TIMER_ON_OFF 0
#define EMC_TIMER_RESET_VALUE 8
#define pr_info(...) ((void)0)
#define pr_warn(...) ((void)0)
#define udelay(x) ((void)0)
#define cpu_relax() ((void)0)
static void *emc_timer;
static u64 tsc;
static unsigned int reads, unmaps, mode;
static u32 seed;
static u64 rdtsc(void) { tsc += PS4_DEFAULT_TSC_FREQ / 100; return tsc; }
static u32 emctimer_read32(unsigned int reg)
{
    if (reg != EMC_TIMER_VALUE) return 0;
    reads++;
    if (mode == 0) return seed;
    if (mode == 1) return seed + reads;
    if (mode == 2) return seed + (reads - 1) / 2;
    if (mode == 3) return seed + ((reads > 6 ? 6 : reads) - 1) / 2;
    return seed + ((reads > 30 ? 30 : reads) - 1) / 2;
}
static void emctimer_write32(unsigned int reg, u32 value) { }
static void *early_ioremap(unsigned int addr, size_t size) { return mode == 5 ? NULL : &mode; }
static void early_iounmap(void *addr, size_t size) { unmaps++; }
'''
        for name in ("emctimer_read", "emctimer_wait_ticks", "ps4_measure_tsc_freq"):
            source += function(text, name)
        source += r'''
static void reset(unsigned int new_mode, u32 new_seed)
{ mode = new_mode; seed = new_seed; reads = unmaps = 0; tsc = 0; emc_timer = NULL; }
int main(void)
{
    u32 value;
    reset(1,0); assert(!emctimer_read(&value)); assert(reads == 10001);
    reset(0,0); assert(!emctimer_wait_ticks(0,1,&value)); assert(reads < 5000);
    reset(2,UINT32_MAX - 5); assert(emctimer_wait_ticks(seed,1024,&value));
    assert((u32)(value - seed) >= 1024);
    reset(0,0); assert(ps4_measure_tsc_freq() == 0); assert(unmaps == 1 && !emc_timer);
    reset(1,0); assert(ps4_measure_tsc_freq() == 0); assert(unmaps == 1 && !emc_timer);
    reset(3,0); assert(ps4_measure_tsc_freq() == 0); assert(unmaps == 1 && !emc_timer);
    reset(4,0); assert(ps4_measure_tsc_freq() == 0); assert(unmaps == 1 && !emc_timer);
    reset(5,0); assert(ps4_measure_tsc_freq() == 0); assert(!unmaps && !emc_timer);
    reset(2,0); assert(ps4_measure_tsc_freq() > 0); assert(unmaps == 1 && !emc_timer);
    return 0;
}
'''
        # Tick progression advances every pair of reads; use a finer synthetic
        # TSC step so the healthy wrap case fits inside its time budget.
        source = source.replace("tsc += PS4_DEFAULT_TSC_FREQ / 100;", "tsc += PS4_DEFAULT_TSC_FREQ / 10000;")
        run_c(source)


if __name__ == "__main__":
    unittest.main()
