"""Exercise the contributed atomic ICC sender with a CPU-only mailbox mock."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / (
    "platforms/ps4/patches/7.2-ps4-baikal/0200-ps4-drivers/"
    "0001-ps4-bpcie-icc-power-off-before-acpi.patch"
)


def sender():
    # Compile the actual function from the patch, rather than a copied model.
    lines = PATCH.read_text().splitlines(keepends=True)
    image = "".join(line[1:] for line in lines
                    if line.startswith(("+", " ")) and not line.startswith("+++"))
    match = re.search(r"^static int bpcie_icc_send_nowait\(.*?^}\n",
                      image, re.MULTILINE | re.DOTALL)
    if match is None:
        raise AssertionError("Complete bpcie_icc_send_nowait definition is missing")
    return match.group()


class PoweroffTests(unittest.TestCase):
    def test_atomic_sender_preserves_pending_state_and_declines_forced_shutdown(self):
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
#define READ_ONCE(x) (x)
#define BPCIE_ICC_SEND 1
#define BPCIE_ICC_ACK 2
#define BPCIE_REG_ICC_STATUS 0
#define BPCIE_REG_ICC_DOORBELL 4
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
static unsigned int calls, mmio;
static int reboot_force, helper_error;
static int icc_write_request(struct bpcie_dev *sc, u8 major, u16 minor,
                             const void *data, u16 length)
{
    assert(major == 4 && minor == 1 && data == NULL && length == 0);
    calls++;
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
'''
        source += sender()
        source += r'''
int main(void)
{
    for (unsigned int scenario = 0; scenario < 5; scenario++) {
        struct bpcie_dev sc = {0};
        unsigned char marker;
        sc.icc.reply_buffer = &marker;
        sc.icc.reply_length = 15;
        sc.icc.request.cookie = 7;
        icc_sc = scenario == 0 ? NULL : &sc;
        sc.icc.reply_pending = scenario == 2;
        helper_error = scenario == 3 ? -EIO : 0;
        reboot_force = scenario == 4;
        calls = mmio = 0;

        int ret = bpcie_icc_send_nowait(4, 1, NULL, 0);
        if (scenario == 0 || scenario == 2 || scenario == 4) {
            assert(ret == (scenario == 0 ? -EAGAIN : -EBUSY));
            assert(!calls && !mmio);
            assert(sc.icc.request.cookie == 7);
            assert(sc.icc.reply_buffer == &marker && sc.icc.reply_length == 15);
            assert(sc.icc.reply_pending == (scenario == 2));
        } else if (scenario == 1) {
            assert(!ret && calls == 1 && mmio == 2);
            assert(!sc.icc.reply_buffer && !sc.icc.reply_length);
        } else {
            assert(ret == -EIO && calls == 1 && !mmio);
        }
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test.c").write_text(source)
            subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                            str(root / "test.c"), "-o", str(root / "test")],
                           check=True, timeout=30)
            subprocess.run([str(root / "test")], check=True, timeout=5)


if __name__ == "__main__":
    unittest.main()
