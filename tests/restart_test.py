"""CPU-only checks of the main-series atomic Baikal terminal callbacks."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / (
    "platforms/ps4/patches/7.2-ps4-baikal/0200-ps4-drivers/"
    "0004-ps4-bpcie-icc-atomic-restart.patch"
)


def patched_image(filename):
    section = PATCH.read_text().split("diff --git a/" + filename + " b/" + filename + "\n", 1)[1]
    section = section.split("diff --git ", 1)[0]
    return "".join(line[1:] for line in section.splitlines(keepends=True)
                   if line.startswith(("+", " ")) and not line.startswith("+++"))


def function(image, name):
    match = re.search(r"^(?:static )?(?:int|void) " + name + r"\(.*?^}\n",
                      image, re.MULTILINE | re.DOTALL)
    if match is None:
        raise AssertionError("Complete patched function missing: " + name)
    return match.group()


COMMON = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
#define READ_ONCE(x) (x)
#define BPCIE_ICC_SEND 1
#define BPCIE_ICC_ACK 2
#define BPCIE_REG_ICC_STATUS 0
#define BPCIE_REG_ICC_DOORBELL 4
#define NOTIFY_DONE 0
#define NOTIFY_STOP 0x8001
#define pr_emerg(...) ((void)++logs)
#define WARN_ON(x) ((void)(warnings += !!(x)))
enum { SYSTEM_RUNNING, SYSTEM_HALT, SYSTEM_POWER_OFF, SYSTEM_RESTART };
struct sys_off_data { int unused; };
struct bpcie_dev {
    struct {
        bool reply_pending;
        void *reply_buffer;
        unsigned int reply_length;
        struct { unsigned int cookie; } request;
    } icc;
    unsigned char bar2[8];
};
static struct bpcie_dev *icc_sc;
static unsigned int calls, mmio, delays, logs, warnings, legacy_calls;
static unsigned int sent_major, sent_minor, sent_length;
static unsigned char sent_data[6];
static int system_state, reboot_force, kexec_in_progress, helper_error;
static int bcpie_icc_mutex, irq_disabled, baikal_status, aeolia_status;
static int mutex_is_locked(const int *lock) { return *lock; }
static int irqs_disabled(void) { return irq_disabled; }
static int bpcie_status(void) { return baikal_status; }
static int apcie_status(void) { return aeolia_status; }
static void mdelay(unsigned int value) { assert(value == 3000); delays++; }
static int apcie_icc_cmd(u8 major, u16 minor, const void *data, u16 length,
                         void *reply, u16 reply_length)
{
    static const unsigned char restart[] = {0, 1, 2, 0, 1, 0};
    assert(major == 4 && minor == 1 && length == sizeof(restart));
    assert(!memcmp(data, restart, length) && !reply && !reply_length);
    legacy_calls++;
    return 0;
}
static int icc_write_request(struct bpcie_dev *sc, u8 major, u16 minor,
                             const void *data, u16 length)
{
    calls++;
    sent_major = major;
    sent_minor = minor;
    sent_length = length;
    assert(length <= sizeof(sent_data));
    if (length) memcpy(sent_data, data, length);
    sc->icc.request.cookie++;
    return helper_error;
}
static void iowrite32(unsigned int value, void *address)
{
    if (!mmio) {
        assert(address == icc_sc->bar2 + BPCIE_REG_ICC_STATUS);
        assert(value == (BPCIE_ICC_SEND | BPCIE_ICC_ACK));
    } else {
        assert(mmio == 1);
        assert(address == icc_sc->bar2 + BPCIE_REG_ICC_DOORBELL);
        assert(value == BPCIE_ICC_SEND);
    }
    mmio++;
}
static void reset(struct bpcie_dev *sc)
{
    memset(sc, 0, sizeof(*sc));
    icc_sc = sc;
    calls = mmio = delays = logs = warnings = legacy_calls = 0;
    helper_error = reboot_force = kexec_in_progress = bcpie_icc_mutex = 0;
    irq_disabled = baikal_status = aeolia_status = 1;
    system_state = SYSTEM_RESTART;
    memset(sent_data, 0xa5, sizeof(sent_data));
}
'''


class RestartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baikal = patched_image("drivers/ps4/ps4-bpcie-icc.c")
        cls.aeolia = patched_image("drivers/ps4/ps4-apcie-icc.c")

    def compile_and_run(self, body):
        # Compile the actual functions from the patch, not a second policy model.
        source = COMMON
        for name in ("bpcie_icc_send_nowait", "icc_shutdown", "icc_restart"):
            source += function(self.baikal, name)
        source += function(self.aeolia, "icc_reboot")
        source += body
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "test.c").write_text(source)
            subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                            "-Wno-unused-parameter", "-Wno-unused-function",
                            str(path / "test.c"), "-o", str(path / "test")],
                           check=True, timeout=30)
            subprocess.run([str(path / "test")], check=True, timeout=5)

    def test_terminal_context_and_busy_guards_leave_mailbox_untouched(self):
        self.compile_and_run(r'''
int main(void)
{
    struct bpcie_dev sc;
    unsigned char marker;
    for (unsigned int state = SYSTEM_RUNNING; state <= SYSTEM_RESTART; state++) {
        for (unsigned int mask = 0; mask < 32; mask++) {
            reset(&sc);
            system_state = state;
            reboot_force = !!(mask & 1);
            kexec_in_progress = !!(mask & 2);
            irq_disabled = !(mask & 4);
            bcpie_icc_mutex = !!(mask & 8);
            sc.icc.reply_pending = !!(mask & 16);
            sc.icc.reply_buffer = &marker;
            sc.icc.reply_length = 15;
            sc.icc.request.cookie = 7;
            int ret = bpcie_icc_send_nowait(4, 1, NULL, 0);
            if (state < SYSTEM_POWER_OFF || mask) {
                assert(ret == (state < SYSTEM_POWER_OFF ? -EINVAL : -EBUSY));
                assert(!calls && !mmio);
                assert(sc.icc.reply_buffer == &marker && sc.icc.reply_length == 15);
                assert(sc.icc.request.cookie == 7);
                assert(sc.icc.reply_pending == !!(mask & 16));
            } else {
                assert(!ret && calls == 1 && mmio == 2);
                assert(!sc.icc.reply_buffer && !sc.icc.reply_length);
            }
        }
    }
    reset(&sc);
    icc_sc = NULL;
    assert(bpcie_icc_send_nowait(4, 1, NULL, 0) == -EAGAIN);
    assert(!calls && !mmio);
    reset(&sc);
    helper_error = -EIO;
    assert(bpcie_icc_send_nowait(4, 1, NULL, 0) == -EIO);
    assert(calls == 1 && !mmio);
    return 0;
}
''')

    def test_restart_sends_fixed_command_once_without_sleeping_fallback(self):
        self.compile_and_run(r'''
int main(void)
{
    struct bpcie_dev sc;
    const unsigned char expected[] = {0, 1, 2, 0, 1, 0};
    reset(&sc);
    assert(icc_restart(NULL) == NOTIFY_STOP);
    assert(calls == 1 && mmio == 2 && delays == 1);
    assert(sent_major == 4 && sent_minor == 1 && sent_length == 6);
    assert(!memcmp(sent_data, expected, sizeof(expected)));
    icc_reboot(); /* Native x86 calls this even after NOTIFY_STOP. */
    assert(calls == 1 && mmio == 2 && delays == 1 && !legacy_calls);
    for (unsigned int failure = 0; failure < 3; failure++) {
        reset(&sc);
        if (failure == 0) sc.icc.reply_pending = true;
        if (failure == 1) helper_error = -EIO;
        if (failure == 2) bcpie_icc_mutex = 1;
        assert(icc_restart(NULL) == NOTIFY_STOP);
        assert(!mmio && !delays);
        unsigned int initial_calls = calls;
        icc_reboot();
        assert(calls == initial_calls && !legacy_calls && !delays);
    }
    reset(&sc);
    icc_reboot(); /* Emergency path has no normal-shutdown ownership proof. */
    assert(!calls && !mmio && !legacy_calls && !delays);
    return 0;
}
''')

    def test_poweroff_command_and_aeolia_fallback_are_preserved(self):
        self.compile_and_run(r'''
int main(void)
{
    struct bpcie_dev sc;
    const unsigned char expected[] = {0, 0, 2, 0, 1, 0};
    reset(&sc);
    system_state = SYSTEM_POWER_OFF;
    icc_shutdown();
    assert(calls == 1 && mmio == 2 && delays == 1);
    assert(sent_major == 4 && sent_minor == 1 && sent_length == 6);
    assert(!memcmp(sent_data, expected, sizeof(expected)));
    reset(&sc);
    system_state = SYSTEM_POWER_OFF;
    sc.icc.reply_pending = true;
    icc_shutdown();
    assert(!calls && !mmio && !delays);
    reset(&sc);
    baikal_status = 0;
    assert(icc_restart(NULL) == NOTIFY_DONE);
    assert(!calls && !mmio && !delays);
    icc_reboot();
    assert(legacy_calls == 1 && delays == 1 && warnings == 1);
    reset(&sc);
    baikal_status = aeolia_status = 0;
    icc_reboot();
    assert(!legacy_calls && !delays);
    return 0;
}
''')

    def test_restart_registration_and_removal_are_paired(self):
        self.assertRegex(self.baikal, r"icc_restart_handler = register_sys_off_handler\(SYS_OFF_MODE_RESTART,\s*"
                         r"SYS_OFF_PRIO_FIRMWARE \+ 1,\s*icc_restart, NULL\)")
        remove = function(self.baikal, "bpcie_icc_remove")
        self.assertLess(remove.index("unregister_sys_off_handler(icc_restart_handler)"),
                        remove.index("icc_sc = NULL"))
        self.assertIn("unregister_sys_off_handler(icc_power_off_handler)", remove)
        sender = function(self.baikal, "bpcie_icc_send_nowait")
        self.assertNotRegex(sender, r"\b(?:mutex_lock|spin_lock|usleep_range|schedule|icc_interrupt|jiffies)\b")


if __name__ == "__main__":
    unittest.main()
