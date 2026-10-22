# Booting the kernel with your Linux distribution

Linux psX supplies a kernel and matching modules. Omarchy, systemd,
NetworkManager and a particular root filesystem format are not required.
The active PS4 target remains experimental and has the hardware limits recorded
in [the PS4 status](../platforms/ps4/STATUS.md).

## Components you supply

1. A PS4 loader compatible with your console, firmware and Linux boot protocol.
2. An initramfs that can find and mount your chosen Linux root filesystem.
3. A Linux x86-64 root filesystem and the **whole matching** module tree.
4. Separately obtained device firmware required by the selected drivers
   (see [Early firmware and the initramfs](#early-firmware-and-the-initramfs)).
5. Boot arguments matching that loader/initramfs, root device and display.

No loader payload, jailbreak, firmware, initramfs, distribution image or
launcher PKG is bundled. Normal PC UEFI/GRUB boot instructions do not establish
a working console boot path. PS5 requires its own loader and future target.

## Preserve a complete boot pair

Verify `SHA256SUMS` and `artifacts.json`. Keep the previous kernel, initramfs,
arguments and module directory together as a recovery set on another machine.
Install the entire new `lib/modules/<kernel_release>/` archive into the intended
external Linux root and run that distribution's `depmod` for the new release.
Handle `/lib` versus merged `/usr/lib` according to the distribution. Inspect
the archive first, preserve the old release, and never overwrite an existing
same-release directory or copy only the Ethernet module.

A kernel needs its own matching modules even if another build has the same
upstream Linux version. The firmware-free psX build differs from older
`linux-ps4` binaries; their module trees are not interchangeable.

## Root, display and boot arguments

Set `root=` to the device, label or UUID that **your initramfs supports**.
Specify a compiled-in root filesystem or include its matching module in the
initramfs. For an initramfs that supports labels, a simple ext4 external root
could use `root=LABEL=PS4LINUX rootfstype=ext4 rootwait rw`; this is a root
selector example, not a complete console command line.

Retain the PS4 platform/display/UART arguments required by your established
loader and record every change. For the current Baikal target, `pci=nocrs
intremap=off` are platform settings. Internal SATA is no longer disabled by
default in the current hybrid test profile; the PHY and AHCI completion
settings are documented in [PS4-BOOTARGS.md](PS4-BOOTARGS.md). That page also
separates generic distribution arguments from Omarchy-specific ones.

Loader VRAM settings and boot-file naming belong to the selected loader; no
universal defaults are assumed here. The Omarchy-specific partitioned USB
profile remains in the companion project:

https://github.com/meerzulee/omarchy-psx/blob/master/docs/USING-LINUX-PSX.md

## Early firmware and the initramfs

`CONFIG_EXTRA_FIRMWARE=""` means no device firmware is embedded. Built-in
drivers can request firmware before the real root mounts, so put it in the
initramfs; rootfs firmware alone may arrive too late. On the PS4 Baikal
target that means:

- Display: the Liverpool AMDGPU firmware and the 1920x1080 EDID. The existing
  Omarchy PS4 initramfs already carries them.
- Wi-Fi and Bluetooth (MediaTek MT7668): `mediatek/mt7668pr2h.bin` from
  upstream [linux-firmware](https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git),
  and nine Wi-Fi files from `extra_firmware/` in the Linux 5.4 PS4 kernel
  [feeRnt/ps4-linux-12xx](https://github.com/feeRnt/ps4-linux-12xx/tree/3070025bad397ad499f3c4919f35e8abd547af2b/extra_firmware)
  (branch `5.4.247-baikal-dfaus`): `WIFI_RAM_CODE_MT7668.bin`,
  `WIFI_RAM_CODE2_SDIO_MT7668.bin`, `WIFI_RAM_CODE2_USB_MT7668.bin`,
  `mt7668_patch_e1_hdr.bin`, `mt7668_patch_e2_hdr.bin`, `EEPROM_MT7668.bin`,
  `EEPROM_MT7668_e1.bin`, `TxPwrLimit_MT76x8.dat` and `wifi.cfg`, placed
  directly in `/lib/firmware/`.

Linux psX does not host or redistribute any firmware. The linux-firmware file
is redistributable under its `LICENCE.mediatek`; the Wi-Fi files have no
recorded license, so keep them local. Omarchy psX has scripts that download
exactly these files from those sources, check their pinned SHA-256 hashes and
add them to an initramfs. The pins in its `manifests/ps4-firmware.json` suit
any distribution:

https://github.com/meerzulee/omarchy-psx/blob/master/docs/INITRAMFS.md#add-wi-fi-and-bluetooth-firmware

One fresh boot (omarchy-psx EXP-20261017-018) ran display, Wi-Fi and
Bluetooth with that initramfs on one console. Orbis initializes the MT7668
before every launch, so whether Linux loaded these files was not witnessed.
Keep independent recovery access; do not assume built-in Wi-Fi will work
without its firmware.

## First evaluation

Boot-file writes and console tests require a bounded UART experiment with an
explicit rollback. Follow [HARDWARE-TESTING.md](HARDWARE-TESTING.md). After boot,
check `uname -r`, the installed module/config identities and early firmware
errors. Ethernet starts by itself: udev loads `psx_mts_net` and the network
manager's first open publishes `psx0`. Boot with `psx_mts_net.autoarm=0` or
`modprobe.blacklist=psx_mts_net` to keep it off. Read [ETHERNET.md](ETHERNET.md).
