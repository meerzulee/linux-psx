# Internal storage: hybrid test image, not an installer

Seeing a SATA disk in Linux does not establish a usable Linux root filesystem
on the console's internal disk. The inspected historical design is layered:

```text
Baikal AHCI/SATA
  -> console-owned encrypted partition
  -> per-console EAP HDD key and AES-XTS mapping
  -> Sony UFS2 filesystem
  -> Linux filesystem image in the user/home area
  -> loop device
  -> Linux root
```

## Current hybrid result (2026-10-22)

The internal Toshiba HDD was detected as `/dev/sdb` and used behind an ext4
test image inside the Orbis user volume. The current test profile uses:

- Orbis AHCI PHY profile: `ps4_bpcie.ps4_sata_phy_orbit=1`
- Command-scoped AHCI completion probe: `libahci.ps4_ahci_completion_probe_ms=5`
- Legacy unconditional poll explicitly disabled: `libahci.ps4_ahci_poll_ms=0`

A short health check passed: 64 MiB write/read on the test image, 256 MiB
direct read from the HDD at about 62 MB/s, and no ATA/EXT4 errors or probe stalls.
The USB root and internal test image were mounted read-write. This is **one
short check**, not endurance or hardware acceptance. Repeat boots, sustained
and random I/O, flush/error paths, the Sony encrypted/UFS stack, an installer
and internal-root boot remain unqualified. Do not treat partition numbers,
offsets or image paths as portable defaults.

## Historical encrypted-storage path

The old kernel project inspected this approach in
`danyboy666/ps4-retrobox` at
`6c79062adbe1691707ac3c1ed7baa1f3118734f8`. It is a research reference,
not an installer imported or accepted by Linux psX:

https://github.com/danyboy666/ps4-retrobox/tree/6c79062adbe1691707ac3c1ed7baa1f3118734f8

https://github.com/meerzulee/linux-ps4/blob/386df1ad97d92defee1794bf961a0e2f5dc85a1b/docs/INTERNAL-STORAGE.md

The selected kernel contains AHCI/SATA, device mapper/crypt, UFS, loop and
ext4 support. The PS4 patches also address AHCI resources, IRQ routing, DMA
boundaries and PHY setup. Those are prerequisites, not a safe installation
or a write-support guarantee. Historical hardware still had internal-disk
timeouts and read errors; the external USB root was independent of that disk.

A future investigation must identify the exact internal controller/device
through stable PCI/sysfs identity rather than `/dev/sdX` order or a partition
glob. Partition numbers and encryption offsets from another firmware or
console are not defaults. Any operator-provided per-console key stays in a
private, permission-restricted location and must never enter Git, CI, logs,
packages or a release.

The first accepted workflow must keep the encrypted mapping, filesystem and
contained image read-only, avoid recovery writes, validate expected signatures
and detach each layer on failure. It needs its own authorized bounded UART
experiment after the external-root recovery path is reliable. An eventual
installer additionally needs explicit target confirmation, backups, space
checks, interruption recovery and a tested uninstall path. No such workflow
is supplied by this migration. Continue development from external Linux
storage and leave Sony partitions untouched.
