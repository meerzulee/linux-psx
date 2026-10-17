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
