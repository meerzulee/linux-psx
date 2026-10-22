# Baikal shared-HDD PHY caller, Linux 7.2.11

This separate experimental candidate changes the embedded HDD path in
`drivers/usb/host/xhci-aeolia.c` to call the already-present
`bpcie_baikal_sata_phy_init(pdev, hpriv->mmio)` on Baikal XHCI. It adapts the
Baikal-only selection in rmuxnet's source at commit
`858d97a4519d4e0110e4b5114b3206ecf05f28d6`. The retained source header declares
GPLv2; original notices and the frozen imported patches remain unchanged.
`BASELINE.json` records the source, attribution, prerequisite patch and exact
before/after member hashes.

https://github.com/vnkgdshrr/linux-rmuxnet/blob/858d97a4519d4e0110e4b5114b3206ecf05f28d6/drivers/usb/host/xhci-aeolia.c

Apply `../0001-ps4-sata-uart-observability.patch` to its recorded runtime source
first, then `0002-ps4-baikal-shared-sata-phy.patch`. The input release is
`7.2.11-psx-ps4-baikal-sata-diag-dev1`; the new build must have distinct release
`7.2.11-psx-ps4-baikal-sata-phy-dev1`. No active target or series is changed here.
Keep the existing diagnostic build as the comparison and rollback artifact.
The primary builder owns compilation, complete matching modules, corresponding
source, configuration and artifact identities.

Baikal initialization failures before ATA host allocation, including the new
PHY caller's `-EPROBE_DEFER`, propagate through AHCI's existing mapping/resource
cleanup, then the outer probe releases its
assigned IRQs, frees its managed state, clears driver data and disables the PCI
device. The new pre-HCD jump lands after the `remove_hcds` loop, so it never
reads the still-uninitialized `idx`. The propagation guard requires both a
nonzero return and `axhci->host == NULL`: no ATA host or HCD exists on this path.
For non-Baikal devices, the legacy PHY code and the existing treatment of AHCI
errors remain unchanged.

Post-host AHCI errors retain the inherited behavior. The underlying custom
driver restores driver data to `axhci`, while libata devres release/stop
callbacks expect driver data to identify `ata_host`. Its existing AHCI error
tail can also unmap/free `hpriv` after host allocation/start. Broadly returning
these later errors from the outer probe would newly trigger driver-core
unwind through that separate lifetime mismatch. This candidate therefore
does not propagate post-host errors or attempt that larger cleanup refactor.
Preserving the old behavior does not establish that those inherited paths are
safe. Revision 1's wider error propagation was rejected during primary review;
`BASELINE.json` records its superseded patch identity.

With `ps4_sata_diag=1`, the Baikal caller emits
`embedded_ahci.baikal_phy.begin` and
`embedded_ahci.baikal_phy.end rc=<return code>` through the existing bounded
diagnostic logger. The PHY selection itself is unconditional for Baikal in
this candidate; the argument controls logging. Return code zero does not prove
readiness: the existing helper ignores the return from its final readiness
poll. That helper's internals are deliberately unchanged. This patch adds no
DMA-mask, IRQ-allocation, NCQ, speed, timeout, retry or delay changes.

The HDD and USB root share this PCI function. A Baikal pre-host initialization
error now stops its entire probe before USB HCD creation. The candidate can therefore
prevent USB boot, so hardware evaluation needs the separate baseline kernel,
unchanged recovery boot set and a fresh bounded UART experiment. This is a
testable candidate cause; there is no hardware acceptance or demonstrated SATA
repair yet. The loader, FPKG, initramfs and boot arguments are outside this patch.

Run the meaningful CPU-only checks from the repository root, supplying the
exact instrumented member (the command reads it and patches a temporary copy):

```sh
python3 -B diagnostics/ps4-sata/phy-dev1/test-host.py \
  --baseline /absolute/path/to/instrumented/drivers/usb/host/xhci-aeolia.c
```

The test compiles the actual patched PHY selection with its original error
tail and the complete outer probe, with hardware calls stubbed. Twenty-three cases
exercise Baikal success, defer and errors; pre-HCD cleanup order; non-Baikal
success and allocation failures against the original branch; existing outer
AHCI-error behavior; post-host error returns with a nonnull host and no new
outer cleanup; and the existing child-removal path. It also checks exact
patch/member identities, clean patch application, the unchanged GPL header,
byte-identical non-Baikal logic after removing indentation, and the unchanged
AHCI error tail. `VALIDATION.json` records the host result. These checks do not
run real MMIO, helper internals, full AHCI host allocation, IRQs or DMA. Native
kernel compilation and UART hardware evidence are separate checks.
