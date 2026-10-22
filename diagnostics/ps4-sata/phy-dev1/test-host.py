#!/usr/bin/env python3
"""CPU-only fault injection against code extracted from the actual patched member.

The supplied baseline is hashed, copied to a temporary tree, and patched there.
The PHY branch plus its original error tail and the complete outer probe are
compiled with hardware calls stubbed. No kernel tree or device is modified.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, capture_output=True, **kwargs)


def extract(text, start, end):
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

#define PCI_DEVICE_ID_SONY_AEOLIA_XHCI 0x90a4
#define PCI_DEVICE_ID_SONY_BELIZE_XHCI 0x90cf
#define PCI_DEVICE_ID_SONY_BAIKAL_XHCI 0x90de
#define EPROBE_DEFER 517
#define ENODEV 19
#define ENOMEM 12
#define GFP_KERNEL 0
#define NR_DEVICES 3
#define DMA_BIT_MASK(n) ((1ULL << (n)) - 1)

struct device { int unused; };
struct pci_dev { struct device dev; int device; int irq; void *data; };
struct pci_device_id { int unused; };
struct aeolia_xhci { void *host; int nr_irqs; void *hcd[NR_DEVICES]; };
struct f_resource { int r_bustag; void *r_bushandle; };
struct ahci_controller { struct f_resource *r_mem; int dev_id; int trace_len; };
struct ahci_host_priv { void *mmio; };
typedef unsigned long resource_size_t;

static struct aeolia_xhci axhci_memory;
static struct f_resource rmem_memory;
static struct ahci_controller ctlr_memory;
static struct ahci_host_priv hpriv_memory;
static int mmio_memory;
static int ata_host_memory;
static bool bus_master;
static bool ahci_error_host_present;
static int phy_rc, ahci_rc_override, status_rc, enable_rc, irq_rc;
static int fail_alloc, alloc_calls, free_allocs;
static int baikal_calls, legacy_calls, phy_continued, ahci_calls;
static int phy_unmaps, region_releases, hpriv_frees;
static int irq_assigns, irq_frees, axhci_frees, device_disables, master_calls;
static int child_calls, child_indices[NR_DEVICES], child_fail_index;
static int removed_calls, removed_indices[NR_DEVICES];
static int sleeps, sleep_values[NR_DEVICES + 1];
static int marker_begin, marker_end, marker_rc;
static char events[8192];

static void event(const char *fmt, ...)
{
    va_list ap;
    size_t used = strlen(events);
    va_start(ap, fmt);
    int n = vsnprintf(events + used, sizeof(events) - used, fmt, ap);
    va_end(ap);
    assert(n >= 0 && (size_t)n < sizeof(events) - used);
}

static void ps4_sata_diag(struct device *dev, const char *fmt, ...)
{
    (void)dev;
    if (!strcmp(fmt, "embedded_ahci.baikal_phy.begin\n")) {
        ++marker_begin;
        event("baikal.begin;");
    } else if (!strcmp(fmt, "embedded_ahci.baikal_phy.end rc=%d\n")) {
        va_list ap;
        va_start(ap, fmt);
        marker_rc = va_arg(ap, int);
        va_end(ap);
        ++marker_end;
        event("baikal.end:%d;", marker_rc);
    } else if (!strcmp(fmt, "embedded_ahci.phy.begin\n")) {
        event("legacy.begin;");
    } else if (!strcmp(fmt, "embedded_ahci.phy.end\n")) {
        event("legacy.end;");
    }
}

static void ps4_sata_diag_masks(struct device *dev, const char *stage, int rc)
{ (void)dev; (void)stage; (void)rc; }
static void dev_dbg(struct device *dev, const char *fmt, ...)
{ (void)dev; (void)fmt; }
static void dev_err(struct device *dev, const char *fmt, ...)
{ (void)dev; (void)fmt; }
static const char *dev_driver_string(struct device *dev)
{ (void)dev; return "xhci_aeolia"; }
static const char *pci_name(struct pci_dev *dev)
{ (void)dev; return "0000:00:14.7"; }
static struct pci_dev *to_pci_dev(struct device *dev)
{ return (struct pci_dev *)((char *)dev - offsetof(struct pci_dev, dev)); }

static void *kzalloc(size_t size, int flags)
{
    (void)flags;
    ++alloc_calls;
    event("alloc:%d;", alloc_calls);
    if (alloc_calls == fail_alloc)
        return NULL;
    if (alloc_calls == 1) {
        assert(size == sizeof(rmem_memory));
        memset(&rmem_memory, 0, sizeof(rmem_memory));
        return &rmem_memory;
    }
    assert(alloc_calls == 2 && size == sizeof(ctlr_memory));
    memset(&ctlr_memory, 0, sizeof(ctlr_memory));
    return &ctlr_memory;
}
static void kfree(void *ptr)
{
    assert(ptr == &rmem_memory || ptr == &ctlr_memory);
    ++free_allocs;
    event("free:%s;", ptr == &rmem_memory ? "rmem" : "ctlr");
}
static int bpcie_baikal_sata_phy_init(struct pci_dev *dev, void *mmio)
{
    assert(dev->device == PCI_DEVICE_ID_SONY_BAIKAL_XHCI);
    assert(mmio == &mmio_memory);
    ++baikal_calls;
    event("baikal.call:%d;", phy_rc);
    return phy_rc;
}
static void bpcie_sata_phy_init(struct device *dev, struct ahci_controller *ctlr)
{
    (void)dev;
    assert(ctlr == &ctlr_memory && ctlr->r_mem == &rmem_memory);
    assert(ctlr->r_mem->r_bushandle == &mmio_memory);
    assert(ctlr->r_mem->r_bustag == 1 && ctlr->dev_id == 0 && ctlr->trace_len == 6);
    ++legacy_calls;
    event("legacy.call;");
}
static void iounmap(void *mmio)
{
    assert(mmio == &mmio_memory);
    ++phy_unmaps;
    event("unmap;");
}
static void devm_release_mem_region(struct device *dev, resource_size_t start,
                                   resource_size_t len)
{
    (void)dev;
    assert(start == 0xce800000 && len == 65536);
    ++region_releases;
    event("release.region;");
}
static void devm_kfree(struct device *dev, void *ptr)
{
    (void)dev;
    if (ptr == &hpriv_memory) {
        ++hpriv_frees;
        event("free.hpriv;");
    } else {
        assert(ptr == &axhci_memory);
        ++axhci_frees;
        event("free.axhci;");
    }
}
static void *devm_kzalloc(struct device *dev, size_t size, int flags)
{
    (void)dev; (void)flags;
    assert(size == sizeof(axhci_memory));
    memset(&axhci_memory, 0, sizeof(axhci_memory));
    return &axhci_memory;
}
static int apcie_status(void) { return status_rc; }
static int pci_enable_device(struct pci_dev *dev)
{ (void)dev; return enable_rc; }
static void pci_set_drvdata(struct pci_dev *dev, void *data) { dev->data = data; }
static int apcie_assign_irqs(struct pci_dev *dev, int nr)
{
    assert(nr == NR_DEVICES);
    ++irq_assigns;
    dev->irq = 1;
    event("assign.irqs;");
    return irq_rc;
}
static void apcie_free_irqs(int irq, int nr)
{
    assert(irq == 1 && nr == NR_DEVICES);
    ++irq_frees;
    event("free.irqs;");
}
static void pci_set_master(struct pci_dev *dev)
{ (void)dev; ++master_calls; event("master;"); }
static int dma_set_mask_and_coherent(struct device *dev, uint64_t mask)
{ (void)dev; assert(mask == DMA_BIT_MASK(31)); return 0; }
static void msleep(int ms)
{
    assert(sleeps < NR_DEVICES + 1);
    sleep_values[sleeps++] = ms;
    event("sleep:%d;", ms);
}
static int xhci_aeolia_probe_one(struct pci_dev *dev, int idx)
{
    assert(idx >= 0 && idx < NR_DEVICES && child_calls < NR_DEVICES);
    child_indices[child_calls++] = idx;
    event("child:%d;", idx);
    if (idx == child_fail_index)
        return -5;
    ((struct aeolia_xhci *)dev->data)->hcd[idx] = &mmio_memory;
    return 0;
}
static void xhci_aeolia_remove_one(struct pci_dev *dev, int idx)
{
    assert(idx >= 0 && idx < NR_DEVICES && removed_calls < NR_DEVICES);
    removed_indices[removed_calls++] = idx;
    ((struct aeolia_xhci *)dev->data)->hcd[idx] = NULL;
    event("remove:%d;", idx);
}
static void pci_disable_device(struct pci_dev *dev)
{ (void)dev; ++device_disables; event("disable;"); }

/* PHY_SELECTIONS */

static int ahci_init_one(struct pci_dev *dev)
{
    ++ahci_calls;
    if (ahci_rc_override) {
        if (ahci_error_host_present)
            ((struct aeolia_xhci *)dev->data)->host = &ata_host_memory;
        return ahci_rc_override;
    }
    int rc = phy_under_test(dev);
    if (!rc)
        ((struct aeolia_xhci *)dev->data)->host = &ata_host_memory;
    return rc;
}

/* ACTUAL_IS_BAIKAL */
/* ACTUAL_PROBE */

static void reset(void)
{
    phy_rc = ahci_rc_override = enable_rc = fail_alloc = 0;
    alloc_calls = free_allocs = baikal_calls = legacy_calls = 0;
    phy_continued = ahci_calls = phy_unmaps = region_releases = hpriv_frees = 0;
    irq_assigns = irq_frees = axhci_frees = device_disables = master_calls = 0;
    child_calls = removed_calls = sleeps = marker_begin = marker_end = marker_rc = 0;
    status_rc = 1;
    irq_rc = NR_DEVICES;
    child_fail_index = -1;
    bus_master = false;
    ahci_error_host_present = false;
    memset(events, 0, sizeof(events));
}

static void test_phy_success(void)
{
    struct pci_dev dev = { .device = PCI_DEVICE_ID_SONY_BAIKAL_XHCI };
    reset();
    assert(phy_under_test(&dev) == 0);
    assert(baikal_calls == 1 && legacy_calls == 0 && alloc_calls == 0);
    assert(marker_begin == 1 && marker_end == 1 && marker_rc == 0);
    assert(phy_continued == 1 && phy_unmaps == 0 && region_releases == 0 && hpriv_frees == 0);
    assert(!strcmp(events, "baikal.begin;baikal.call:0;baikal.end:0;continue;"));
    puts("PASS baikal_phy_success");
}

static void test_phy_error(int rc)
{
    struct pci_dev dev = { .device = PCI_DEVICE_ID_SONY_BAIKAL_XHCI };
    reset();
    phy_rc = rc;
    assert(phy_under_test(&dev) == rc);
    assert(baikal_calls == 1 && legacy_calls == 0 && alloc_calls == 0);
    assert(marker_begin == 1 && marker_end == 1 && marker_rc == rc);
    assert(phy_continued == 0 && phy_unmaps == 1 && region_releases == 1 && hpriv_frees == 1);
    char expected[256];
    snprintf(expected, sizeof(expected),
             "baikal.begin;baikal.call:%d;baikal.end:%d;unmap;release.region;free.hpriv;", rc, rc);
    assert(!strcmp(events, expected));
    printf("PASS baikal_phy_cleanup_rc_%d\n", rc);
}

static void test_legacy_unchanged(int device, int fail)
{
    struct pci_dev dev = { .device = device };
    char baseline_events[sizeof(events)];
    reset();
    fail_alloc = fail;
    assert(phy_baseline(&dev) == 0);
    strcpy(baseline_events, events);
    reset();
    fail_alloc = fail;
    assert(phy_under_test(&dev) == 0);
    assert(!strcmp(events, baseline_events));
    assert(baikal_calls == 0 && marker_begin == 0 && marker_end == 0);
    assert(phy_unmaps == 0 && phy_continued == 1);
    printf("PASS non_baikal_phy_unchanged_%x_allocfail_%d\n", device, fail);
}

static void test_probe_success(int device)
{
    struct pci_dev dev = { .device = device };
    reset();
    assert(xhci_aeolia_probe(&dev, NULL) == 0);
    assert(irq_assigns == 1 && irq_frees == 0 && axhci_frees == 0 && device_disables == 0);
    assert(master_calls == 1);
    if (device == PCI_DEVICE_ID_SONY_AEOLIA_XHCI) {
        assert(ahci_calls == 0 && child_calls == 3 && sleeps == 0);
        assert(child_indices[0] == 0 && child_indices[1] == 1 && child_indices[2] == 2);
    } else {
        assert(ahci_calls == 1 && child_calls == 2);
        assert(((struct aeolia_xhci *)dev.data)->host == &ata_host_memory);
        assert(child_indices[0] == 0 && child_indices[1] == 2);
        if (device == PCI_DEVICE_ID_SONY_BAIKAL_XHCI) {
            assert(baikal_calls == 1 && legacy_calls == 0);
            assert(sleeps == 3 && sleep_values[0] == 50 && sleep_values[1] == 20 && sleep_values[2] == 20);
        } else {
            assert(baikal_calls == 0 && legacy_calls == 1 && sleeps == 0);
        }
    }
    printf("PASS probe_success_%x\n", device);
}

static void test_probe_phy_error(int rc)
{
    struct pci_dev dev = { .device = PCI_DEVICE_ID_SONY_BAIKAL_XHCI };
    reset();
    phy_rc = rc;
    assert(xhci_aeolia_probe(&dev, NULL) == rc);
    assert(baikal_calls == 1 && phy_unmaps == 1 && region_releases == 1 && hpriv_frees == 1);
    assert(irq_assigns == 1 && irq_frees == 1 && axhci_frees == 1 && device_disables == 1);
    assert(child_calls == 0 && removed_calls == 0 && sleeps == 0 && master_calls == 0);
    assert(dev.data == NULL);
    assert(strstr(events, "free.hpriv;free.irqs;free.axhci;disable;") != NULL);
    printf("PASS baikal_probe_pre_hcd_cleanup_rc_%d\n", rc);
}

static void test_probe_ahci_error(int device)
{
    struct pci_dev dev = { .device = device };
    reset();
    ahci_rc_override = -5;
    int result = xhci_aeolia_probe(&dev, NULL);
    if (device == PCI_DEVICE_ID_SONY_BAIKAL_XHCI) {
        assert(result == -5 && child_calls == 0 && removed_calls == 0 && sleeps == 0);
        assert(irq_frees == 1 && axhci_frees == 1 && device_disables == 1 && dev.data == NULL);
    } else {
        assert(result == 0 && child_calls == 2 && sleeps == 0);
        assert(irq_frees == 0 && axhci_frees == 0 && device_disables == 0);
    }
    printf("PASS probe_ahci_error_policy_%x\n", device);
}

static void test_probe_post_host_error(int device, int rc)
{
    struct pci_dev dev = { .device = device };
    reset();
    ahci_rc_override = rc;
    ahci_error_host_present = true;
    assert(xhci_aeolia_probe(&dev, NULL) == 0);
    assert(ahci_calls == 1 && child_calls == 2 && removed_calls == 0);
    assert(child_indices[0] == 0 && child_indices[1] == 2);
    assert(irq_assigns == 1 && irq_frees == 0 && axhci_frees == 0 && device_disables == 0);
    assert(dev.data == &axhci_memory && axhci_memory.host == &ata_host_memory);
    assert(master_calls == 1);
    if (device == PCI_DEVICE_ID_SONY_BAIKAL_XHCI)
        assert(sleeps == 3 && sleep_values[0] == 50 && sleep_values[1] == 20 && sleep_values[2] == 20);
    else
        assert(sleeps == 0);
    assert(strstr(events, "free.irqs;") == NULL);
    assert(strstr(events, "free.axhci;") == NULL);
    assert(strstr(events, "disable;") == NULL);
    printf("PASS inherited_post_host_error_preserved_%x_rc_%d\n", device, rc);
}

static void test_probe_child_error(int idx)
{
    struct pci_dev dev = { .device = PCI_DEVICE_ID_SONY_BAIKAL_XHCI };
    reset();
    child_fail_index = idx;
    assert(xhci_aeolia_probe(&dev, NULL) == -5);
    assert(irq_frees == 1 && axhci_frees == 1 && device_disables == 1 && dev.data == NULL);
    if (idx == 0) {
        assert(removed_calls == 0);
    } else {
        assert(removed_calls == 2 && removed_indices[0] == 1 && removed_indices[1] == 0);
    }
    printf("PASS existing_child_cleanup_index_%d\n", idx);
}

int main(void)
{
    test_phy_success();
    test_phy_error(-EPROBE_DEFER);
    test_phy_error(-ENODEV);
    test_phy_error(-5);
    for (int fail = 0; fail <= 2; ++fail) {
        test_legacy_unchanged(PCI_DEVICE_ID_SONY_BELIZE_XHCI, fail);
        test_legacy_unchanged(PCI_DEVICE_ID_SONY_AEOLIA_XHCI, fail);
    }
    test_probe_success(PCI_DEVICE_ID_SONY_AEOLIA_XHCI);
    test_probe_success(PCI_DEVICE_ID_SONY_BELIZE_XHCI);
    test_probe_success(PCI_DEVICE_ID_SONY_BAIKAL_XHCI);
    test_probe_phy_error(-EPROBE_DEFER);
    test_probe_phy_error(-ENODEV);
    test_probe_phy_error(-5);
    test_probe_ahci_error(PCI_DEVICE_ID_SONY_BAIKAL_XHCI);
    test_probe_ahci_error(PCI_DEVICE_ID_SONY_BELIZE_XHCI);
    test_probe_post_host_error(PCI_DEVICE_ID_SONY_BAIKAL_XHCI, -5);
    test_probe_post_host_error(PCI_DEVICE_ID_SONY_BAIKAL_XHCI, -ENODEV);
    test_probe_post_host_error(PCI_DEVICE_ID_SONY_BELIZE_XHCI, -5);
    test_probe_child_error(0);
    test_probe_child_error(2);
    return 0;
}
'''


def phy_function(name, branch, error_tail):
    return f'''static int {name}(struct pci_dev *pdev)
{{
    struct f_resource *r_mem;
    struct ahci_controller *ctlr;
    struct ahci_host_priv *hpriv = &hpriv_memory;
    resource_size_t rsrc_start = 0xce800000, rsrc_len = 65536;
    int rc = 0;
    hpriv->mmio = &mmio_memory;
{branch}
    ++phy_continued;
    event("continue;");
    return 0;
{error_tail}
}}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / "BASELINE.json").read_text())
    member = manifest["files"][0]
    before = args.baseline.read_bytes()
    assert sha256(before) == member["before_sha256"], "baseline member identity mismatch"
    patch = here / manifest["patch"]
    assert sha256(patch.read_bytes()) == manifest["patch_sha256"], "patch identity mismatch"
    cc = shlex.split(os.environ.get("CC", "cc"))
    assert shutil.which(cc[0]), "C compiler required"
    with tempfile.TemporaryDirectory(prefix="ps4-phy-host-") as temp:
        work = Path(temp)
        target = work / member["path"]
        target.parent.mkdir(parents=True)
        target.write_bytes(before)
        checked = run(["git", "apply", "--check", str(patch)], cwd=work)
        applied = run(["git", "apply", str(patch)], cwd=work)
        after = target.read_bytes()
        assert sha256(after) == member["after_sha256"], "patched member identity mismatch"
        baseline = before.decode()
        source = after.decode()
        assert source.split("#include", 1)[0] == baseline.split("#include", 1)[0], "GPL header changed"
        ahci = extract(source, "static int ahci_init_one(", "static void ahci_remove_one(")
        original_ahci = extract(baseline, "static int ahci_init_one(", "static void ahci_remove_one(")
        branch_start = ahci.index("\tif (pdev->device == PCI_DEVICE_ID_SONY_BAIKAL_XHCI) {")
        branch_end = ahci.index("\tdevice_wakeup_enable(&pdev->dev);", branch_start)
        branch = ahci[branch_start:branch_end]
        legacy = extract(original_ahci, "\tr_mem = kzalloc(", "\tdevice_wakeup_enable(&pdev->dev);")
        patched_legacy = branch.split("\t} else {\n", 1)[1].rsplit("\t}\n", 1)[0]
        unindented = "".join(line[1:] if line.startswith("\t") else line for line in patched_legacy.splitlines(keepends=True))
        assert unindented == legacy, "non-Baikal branch changed beyond indentation"
        error_tail = extract(ahci, "\tunmap_registers:\n", "\n}\n")
        assert error_tail == extract(original_ahci, "\tunmap_registers:\n", "\n}\n"), "AHCI error cleanup changed"
        is_baikal = extract(source, "static bool xhci_aeolia_is_baikal(", "static void xhci_aeolia_quirks(")
        probe = extract(source, "static int xhci_aeolia_probe(", "static void xhci_aeolia_remove(")
        harness = HARNESS.replace("/* PHY_SELECTIONS */", phy_function("phy_under_test", branch, error_tail) + phy_function("phy_baseline", legacy, error_tail))
        harness = harness.replace("/* ACTUAL_IS_BAIKAL */", is_baikal).replace("/* ACTUAL_PROBE */", probe)
        cfile = work / "test.c"
        cfile.write_text(harness)
        binary = work / "test"
        flags = ["-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-label", "-Wno-unused-parameter"]
        compiled = run(cc + flags + [str(cfile), "-o", str(binary)])
        assert not compiled.stderr, compiled.stderr
        result = run([str(binary)])
        cases = result.stdout.strip().splitlines()
        assert len(cases) == 23 and all(x.startswith("PASS ") for x in cases), result.stdout
        receipt = {
            "schema": 1,
            "revision": manifest["revision"],
            "status": "PASS",
            "scope": "CPU-only actual patched PHY selection/error tail plus full outer probe; hardware calls stubbed",
            "patch_sha256": manifest["patch_sha256"],
            "before_sha256": member["before_sha256"],
            "after_sha256": member["after_sha256"],
            "compiler": run(cc + ["--version"]).stdout.splitlines()[0],
            "compiler_flags": flags,
            "harness_sha256": sha256(harness.encode()),
            "case_count": len(cases),
            "cases": [x.removeprefix("PASS ") for x in cases],
            "patch_application_warnings": list(dict.fromkeys((checked.stderr + applied.stderr).strip().splitlines())),
            "warning_explanation": "The legacy branch keeps an inherited comment's trailing space when it is reindented; source bytes other than indentation are preserved.",
            "source_checks": ["git apply --check and actual application in temporary tree", "exact before/after and patch SHA-256", "GPL header preserved", "legacy branch identical after indentation removal", "original AHCI error tail identical"],
            "hardware_acceptance": False,
            "limitations": ["Does not execute MMIO, actual PHY helper internals, full ahci_init_one, real IRQ or DMA.", "Post-host tests inject an allocated-host marker and late child error; they verify preserved outer control flow, not repair of inherited libata devres lifetime handling.", "Checks control flow with stubs; native kernel compilation and completed UART hardware evidence remain separate."],
        }
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
