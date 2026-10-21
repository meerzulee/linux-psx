# Internal storage remains read-only research

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

## Latest read-only result (2026-10-22)

`EXP-20261022-011` attached the internal Toshiba HDD as `/dev/sdb` and read
its partition table (`sdb1`, `sdb3`, `sdb5` through `sdb31`) under the local
7.2 Baikal candidate. This used two disabled-by-default test patches:

- Orbis AHCI PHY profile: `ps4_bpcie.ps4_sata_phy_orbit=1`
- AHCI completion poll fallback: `libahci.ps4_ahci_poll_ms=5`

The first 4 KiB read and partition scan completed read-only. This is one
boot; it is not hardware acceptance, and the poll fallback is a workaround
for a pending completion FIS, not a proven interrupt fix. Repeat boots,
write stability, the Sony encrypted/UFS layers and an installer remain
unqualified. Do not treat the partition numbers as a portable layout.

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
