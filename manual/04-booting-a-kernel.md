# Booting a Kernel

This chapter is for running a Linux psX kernel with a distribution of your choice. Omarchy, systemd, NetworkManager and any particular root filesystem format aren't required. For a broader look at running other systems, see [Other Distributions](05-other-distributions.md).

## What you supply

1. A PS4 loader that suits your console, firmware and Linux boot protocol.
2. An initramfs that can find and mount your root filesystem.
3. A Linux x86-64 root filesystem, with the **whole matching** module tree installed.
4. The device firmware that the drivers need, obtained separately.
5. Boot arguments that match your loader, initramfs, root device and display.

A PC's UEFI or GRUB instructions don't apply. A console boot needs its own loader.

## Install the modules

Verify the download first. The modules are in `modules-<kernel_release>.tar.zst`, inside the kernel download. Extract that archive at the top of your root filesystem, so the files land in `lib/modules/<kernel_release>/`, and run your distribution's `depmod` for the new release.

Look inside the archive before you install it, keep the old release, and never overwrite an existing directory with the same release name.

## Boot arguments

Set `root=` to a device, label or UUID that **your initramfs supports**. For an initramfs that understands labels, an external ext4 root could look like this:

```text
root=LABEL=PS4LINUX rootfstype=ext4 rootwait rw
```

That only selects the root filesystem. It isn't a complete console command line.

For early logs over the physical serial port, the tested form is:

```text
earlycon=uart8250,mmio32,0xC890E000,115200n8 console=null keep_bootcon 8250.nr_uarts=0
```

The complete list is in [PS4 boot arguments](../docs/PS4-BOOTARGS.md).

## Firmware

Linux psX kernels embed no device firmware. Drivers built into the kernel can ask for firmware before the real root filesystem is mounted, so firmware that exists only on the root filesystem may arrive too late. Put it in the initramfs.

On a Baikal console that means:

- **Display:** the Liverpool AMDGPU firmware and a 1920×1080 EDID.
- **Wi-Fi and Bluetooth:** files for the console's radio. The project's console has an MT7668, and another Baikal Slim has a Marvell SDIO radio.

This project doesn't host or redistribute firmware. A public download location doesn't by itself give permission to redistribute a file. The [booting notes](../docs/BOOTING.md#early-firmware-and-the-initramfs) list the exact MT7668 files and their license status.

## The first boot

Keep a way back before you start: the previous kernel, initramfs, arguments and modules, kept as a complete set.

Once it's up, check:

- `uname -r` shows the release you meant to boot;
- the installed modules match that release;
- the kernel log has no early firmware errors.

## Ethernet

Built-in Ethernet starts by itself on Baikal: udev loads `psx_mts_net`, and the first time a network manager opens the interface, `psx0`, the driver starts.

To keep it off, boot with one of these:

```text
psx_mts_net.autoarm=0
modprobe.blacklist=psx_mts_net
```

The first keeps the driver loaded but waits for an explicit go-ahead. The second doesn't load it at all.

The driver keeps its memory until power-off, so never unload, reset or rearm it once it has started. NetworkManager works with no configuration, as long as wired MAC randomization is off. Other network managers need a configuration that names `psx0`. See [Ethernet](../docs/ETHERNET.md).

<!-- manual-navigation -->

---

[← Previous](03-kernels.md) · [Contents](README.md) · [Next →](05-other-distributions.md)
