/* SPDX-License-Identifier: GPL-2.0-only */
/* Synthetic host fixture; check.py inserts the actual patched C bodies. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
#include <errno.h>
typedef uint32_t u32;
typedef uint64_t u64;
struct amdgpu_device { int gpu_reset_counter; };
/* ACTUAL_REGISTER_DEFINITIONS */
#define PS4_READY_UVD_VCPU_CACHE_VMID 0x3d3cU
#define PS4_READY_BIT 2U
#define PS4_READY_LIMIT_NS 2000000000ULL
struct frame { u32 mastint, rbc, ib, wp, rp, status, selector, fault; u64 ns; int reset; };
static struct frame frames[4];
static unsigned frame_index, reads, status_reads;
static u32 read_trace[16];
static bool ps4_uvd_poll_state;
static jmp_buf terminal;
static const char *terminal_label;
static int terminal_error;
static u32 last_guard_failure;
static u32 mock_read(u32 reg)
{
    assert(reads < 16);
    read_trace[reads++] = reg;
    const struct frame *f = &frames[frame_index];
    if (reg == mmUVD_RBC_RB_CNTL) return f->rbc;
    if (reg == mmUVD_MASTINT_EN) return f->mastint;
    if (reg == mmUVD_RBC_IB_SIZE) return f->ib;
    if (reg == mmUVD_RBC_RB_WPTR) return f->wp;
    if (reg == mmUVD_RBC_RB_RPTR) return f->rp;
    if (reg == mmUVD_STATUS) { status_reads++; return f->status; }
    if (reg == PS4_READY_UVD_VCPU_CACHE_VMID) return f->selector;
    if (reg == mmVM_CONTEXT1_PROTECTION_FAULT_STATUS) return f->fault;
    assert(0); return 0;
}
#define RREG32(reg) ((void)adev, mock_read(reg))
#define atomic_read(p) (*(p))
#define amdgpu_in_reset(adev) ((void)(adev), frames[frame_index].reset)
#define ktime_get_ns() (frames[frame_index].ns)
#define div_u64(a,b) ((a)/(b))
#define dev_info(...) ((void)0)
#define msleep(ms) ((void)(ms))
#define ps4_ready_snapshot(adev,phase) ((void)(adev), (void)(phase))
#define ps4_ready_guard_log(adev,phase,sample) ((void)(adev), (void)(phase), last_guard_failure = (sample)->failed)
static void sleep_next(unsigned lo, unsigned hi)
{
    assert(lo == 1000 && hi == 2000);
    assert(++frame_index < 4);
    reads = 0;
}
#define usleep_range(lo,hi) sleep_next(lo,hi)
static _Noreturn void ps4_ready_park(struct amdgpu_device *adev, const char *label, int error)
{
    (void)adev;
    terminal_label = label;
    terminal_error = error;
    longjmp(terminal, 1);
}
/* ACTUAL_GUARDS */
static void run_actual_poll(struct amdgpu_device *adev)
{
    struct ps4_ready_guard_sample guard_sample = {0};
    bool vcpu_seen = false;
    u64 started = 0, elapsed;
    u32 baseline_fault = 0, fault, status, cache_vmid_expected = 4;
    int reset_count = 0;
    /* ACTUAL_POLL_SUFFIX */
}
static void init_frames(void)
{
    for (unsigned i = 0; i < 4; i++) frames[i] = (struct frame){
        .rbc = 0x1101080c, .selector = 4, .ns = 1000000ULL * (i + 1)
    };
    frame_index = reads = status_reads = 0;
    terminal_label = NULL;
    terminal_error = 123;
    last_guard_failure = 0;
}
static void expect_terminal(struct amdgpu_device *adev, const char *label, int error)
{
    if (!setjmp(terminal)) run_actual_poll(adev);
    assert(terminal_label && !strcmp(terminal_label, label));
    assert(terminal_error == error);
}
int main(void)
{
    struct amdgpu_device dev = {0};
    struct ps4_ready_guard_sample before, after;
    unsigned comparisons = 0;
    /* Exhaustively compare the actual old/strict-new predicate and read trace. */
    for (unsigned i = 0; i < 65536; i++) {
        init_frames(); frames[0].mastint = i;
        bool old = old_guard(&dev, &before);
        unsigned n = reads; u32 trace[16]; memcpy(trace, read_trace, sizeof(trace));
        reads = 0;
        bool strict = ps4_ready_fetch_guard_sample(&dev, &after, false);
        assert(old == strict && !memcmp(&before, &after, sizeof(before)));
        assert(n == reads && !memcmp(trace, read_trace, n * sizeof(u32)));
        reads = 0;
        bool observed = ps4_ready_fetch_guard_sample(&dev, &after, true);
        assert(observed == (i == 0 || i == 2));
        comparisons++;
    }
    for (unsigned bit = 16; bit < 32; bit++) {
        init_frames(); frames[0].mastint = (1U << bit) | 2;
        assert(!ps4_ready_fetch_guard_sample(&dev, &after, true));
    }
    for (unsigned field = 0; field < 5; field++) {
        init_frames(); frames[0].mastint = 2;
        if (field == 0) frames[0].rbc &= ~0x10000U;
        if (field == 1) frames[0].rbc &= ~0x1000000U;
        if (field == 2) frames[0].ib = 1;
        if (field == 3) frames[0].wp = 1;
        if (field == 4) frames[0].rp = 1;
        assert(!ps4_ready_fetch_guard_sample(&dev, &after, true));
    }
    init_frames(); ps4_uvd_poll_state = false; frames[0].mastint = 2; frames[0].status = 2;
    expect_terminal(&dev, "FETCH_GUARD_CHANGED", -EIO); assert(!status_reads);
    init_frames(); ps4_uvd_poll_state = true; frames[0].mastint = 2; frames[0].status = 2;
    expect_terminal(&dev, "READY_CONVENTION_OBSERVED", 0); assert(status_reads == 1);
    init_frames(); frames[0].status = 2;
    expect_terminal(&dev, "READY_CONVENTION_OBSERVED", 0);
    init_frames(); frames[1].mastint = 2; frames[1].status = 2;
    expect_terminal(&dev, "READY_CONVENTION_OBSERVED", 0); assert(frame_index == 1);
    init_frames(); frames[0].mastint = 2; frames[1].mastint = 0; frames[1].status = 2;
    expect_terminal(&dev, "POLL_STATE_REGRESSION", -EIO);
    assert(last_guard_failure == PS4_GUARD_INT_REGRESSION);
    init_frames(); frames[0].mastint = 6; frames[0].status = 2;
    expect_terminal(&dev, "FETCH_GUARD_CHANGED", -EIO);
    init_frames(); frames[0].mastint = 0x80000002U; frames[0].status = 2;
    expect_terminal(&dev, "FETCH_GUARD_CHANGED", -EIO);
    init_frames(); frames[0].fault = 1; frames[0].status = 2;
    expect_terminal(&dev, "FAULT_OR_RESET", -EIO);
    init_frames(); frames[0].reset = 1; frames[0].status = 2;
    expect_terminal(&dev, "FAULT_OR_RESET", -EIO);
    init_frames(); dev.gpu_reset_counter = 1; frames[0].status = 2;
    expect_terminal(&dev, "FAULT_OR_RESET", -EIO); dev.gpu_reset_counter = 0;
    init_frames(); frames[0].selector = 14; frames[0].status = 2;
    expect_terminal(&dev, "VCPU_CACHE_VMID_CHANGED", -EIO);
    init_frames(); frames[0].mastint = 2; frames[0].status = 2; frames[0].ns = PS4_READY_LIMIT_NS;
    expect_terminal(&dev, "READY_TIMEOUT", -ETIMEDOUT);
    init_frames(); frames[0].ns = PS4_READY_LIMIT_NS + 1;
    expect_terminal(&dev, "READY_TIMEOUT", -ETIMEDOUT);
    init_frames(); frames[0].mastint = frames[1].mastint = 2; frames[1].status = 2;
    expect_terminal(&dev, "READY_CONVENTION_OBSERVED", 0); assert(frame_index == 1);
    init_frames(); frames[3].ns = PS4_READY_LIMIT_NS;
    expect_terminal(&dev, "READY_TIMEOUT", -ETIMEDOUT); assert(frame_index == 3 && status_reads == 4);
    init_frames(); ps4_uvd_poll_state = false; frames[0].status = 2;
    expect_terminal(&dev, "READY_OBSERVED", 0);
    printf("PASS %u low16-bit guard comparisons, high bits, retained guards and 16 actual poll outcomes\n", comparisons);
    return 0;
}
