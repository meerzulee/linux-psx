"""Compile the actual patched boot-policy functions against CPU-only mocks."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "platforms/ps4/patches/7.2-ps4-baikal/0100-x86-platform/0001-ps4-default-platform-boot-policy.patch"


def postimages(patch=PATCH):
    files = {}
    current = None
    in_hunk = False
    for line in patch.read_text().splitlines(keepends=True):
        if line.startswith("diff --git "):
            current = line.split(" b/", 1)[1].strip()
            files[current] = []
            in_hunk = False
        elif line.startswith("@@ "):
            in_hunk = True
        elif in_hunk and line[:1] in (" ", "+"):
            files[current].append(line[1:])
    return {name: "".join(lines) for name, lines in files.items()}


def function(text, name):
    match = re.search(r"^[\w \t]*\b" + name + r"\([^;]*?\)\n\{", text, re.M)
    if not match:
        raise AssertionError("complete function absent from patch: " + name)
    start, depth = match.start(), 0
    for pos in range(text.index("{", match.start()), len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if not depth:
                return text[start:pos + 1] + "\n"
    raise AssertionError("incomplete function: " + name)


class BootDefaultsTests(unittest.TestCase):
    def test_real_policy_and_explicit_overrides(self):
        images = postimages()
        irq = images["drivers/iommu/irq_remapping.c"]
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define __init
#define __initdata
#define pr_info(...) ((void)0)
#define IS_ENABLED(x) (x)
#define CONFIG_INTEL_IOMMU 0
#define CONFIG_AMD_IOMMU 1
#define CONFIG_HYPERV 0
#define CONFIG_X86_POSTED_MSI 1
#define PCI_ROOT_NO_CRS 1
#define PCI_USE__CRS 2
#define PCI_NO_E820 4
#define PCI_USE_E820 8
static bool is_ps4, pci_use_crs, pci_use_e820;
static unsigned int pci_probe;
static struct { uint64_t end; } iomem_resource = { UINT64_MAX };
static int pci_crs_quirks;
static int dmi_get_bios_year(void) { return 2026; }
static void dmi_check_system(int unused) { (void)unused; }
static int disable_irq_remap, disable_irq_post, disable_sourceid_checking;
static int no_x2apic_optout, prepare_calls;
static bool enable_posted_msi;
struct irq_remap_ops { int (*prepare)(void); };
static int prepare(void) { prepare_calls++; return 0; }
static struct irq_remap_ops intel_irq_remap_ops = { prepare };
static struct irq_remap_ops amd_iommu_irq_ops = { prepare };
static struct irq_remap_ops hyperv_irq_remap_ops = { prepare };
static struct irq_remap_ops *remap_ops;
'''
        source += re.search(r"^static bool irq_remap_cmdline[^;]*;", irq, re.M).group() + "\n"
        source += function(images["arch/x86/platform/ps4/ps4.c"], "ps4_platform_present")
        source += function(images["arch/x86/pci/acpi.c"], "pci_acpi_crs_quirks")
        for name in ("setup_nointremap", "setup_irqremap", "irq_remapping_prepare"):
            source += function(irq, name)
        source += r'''
static void reset(bool ps4)
{
    is_ps4 = ps4;
    pci_use_crs = pci_use_e820 = true;
    pci_probe = 0;
    disable_irq_remap = disable_irq_post = disable_sourceid_checking = 0;
    no_x2apic_optout = prepare_calls = 0;
    irq_remap_cmdline = enable_posted_msi = false;
    remap_ops = NULL;
}
int main(void)
{
    reset(true); pci_acpi_crs_quirks(); assert(!pci_use_crs);
    reset(false); pci_acpi_crs_quirks(); assert(pci_use_crs);
    reset(true); pci_probe = PCI_USE__CRS; pci_acpi_crs_quirks(); assert(pci_use_crs);
    reset(true); pci_probe = PCI_ROOT_NO_CRS; pci_acpi_crs_quirks(); assert(!pci_use_crs);
    reset(false); pci_probe = PCI_ROOT_NO_CRS; pci_acpi_crs_quirks(); assert(!pci_use_crs);
    reset(true); pci_probe = PCI_USE_E820; pci_acpi_crs_quirks(); assert(!pci_use_crs && pci_use_e820);
    reset(true); pci_probe = PCI_USE__CRS | PCI_ROOT_NO_CRS; pci_acpi_crs_quirks(); assert(!pci_use_crs);
    reset(true); assert(irq_remapping_prepare() == -ENOSYS); assert(disable_irq_post && !prepare_calls);
    reset(false); assert(irq_remapping_prepare() == 0); assert(!disable_irq_post && prepare_calls == 1);
    reset(true); setup_irqremap("on"); assert(irq_remapping_prepare() == 0); assert(!disable_irq_post);
    reset(true); setup_irqremap("off"); assert(irq_remapping_prepare() == -ENOSYS); assert(disable_irq_post);
    reset(true); setup_nointremap(NULL); assert(irq_remapping_prepare() == -ENOSYS); assert(!prepare_calls);
    reset(true); setup_irqremap("nosid"); assert(irq_remapping_prepare() == -ENOSYS); assert(disable_sourceid_checking);
    reset(true); setup_irqremap("on,nopost"); assert(irq_remapping_prepare() == 0); assert(disable_irq_post);
    reset(true); setup_irqremap("off,on"); assert(irq_remapping_prepare() == 0);
    reset(true); setup_irqremap("on,off"); assert(irq_remapping_prepare() == -ENOSYS);
    reset(true); setup_nointremap(NULL); setup_irqremap("on"); assert(irq_remapping_prepare() == 0);
    reset(true); setup_irqremap("on"); setup_nointremap(NULL); assert(irq_remapping_prepare() == -ENOSYS);
    reset(true); setup_irqremap("unknown"); assert(irq_remapping_prepare() == -ENOSYS);
    reset(true); assert(setup_irqremap(NULL) == -EINVAL); assert(irq_remapping_prepare() == -ENOSYS);
    puts("PASS: actual PCI/IR policy, runtime platform gate and explicit overrides");
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "policy.c").write_text(source)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-Wno-unused-parameter", str(path / "policy.c"), "-o", str(path / "policy")], check=True)
            subprocess.run([str(path / "policy")], check=True)

    def test_non_ps4_config_uses_false_accessor(self):
        header = postimages()["arch/x86/include/asm/ps4.h"]
        stub = function(header, "ps4_platform_present")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "stub.c").write_text("#include <stdbool.h>\n" + stub +
                                        "int main(void) { return ps4_platform_present(); }\n")
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            str(path / "stub.c"), "-o", str(path / "stub")], check=True)
            subprocess.run([str(path / "stub")], check=True)


if __name__ == "__main__":
    unittest.main()
