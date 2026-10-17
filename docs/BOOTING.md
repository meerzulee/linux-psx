# Booting the kernel with your Linux distribution

Linux psX supplies a kernel and matching modules. Omarchy, systemd,
NetworkManager and a particular root filesystem format are not required.
The active PS4 target remains experimental and has the hardware limits recorded
in [the PS4 status](../platforms/ps4/STATUS.md).

## Components you supply

1. A PS4 loader compatible with your console, firmware and Linux boot protocol.
2. An initramfs that can find and mount your chosen Linux root filesystem.
3. A Linux x86-64 root filesystem and the **whole matching** module tree.
4. Separately obtained device firmware required by the selected drivers.
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
loader and record every change. In the historical Baikal setup,
`pci=nocrs intremap=off` were platform settings and
`libata.force=1.00:disable` kept internal SATA disabled. Avoid introducing
internal-storage changes while evaluating a new kernel. Loader VRAM settings
and boot-file naming belong to the selected loader; no universal defaults are
assumed here.

`CONFIG_EXTRA_FIRMWARE=""` means no device firmware is embedded. Built-in
display/Wi-Fi drivers can request firmware before the real root mounts;
provide the required EDID/vendor firmware in a compatible initramfs where
needed. Rootfs firmware alone may arrive too late. Keep independent recovery
access; do not assume built-in Wi-Fi will work without its firmware.

The Omarchy-specific partitioned USB profile and optional network integration
are maintained in the companion project:

https://github.com/meerzulee/omarchy-psx/blob/main/docs/USING-LINUX-PSX.md

## First evaluation

Boot-file writes and console tests require a bounded UART experiment with an
explicit rollback. Follow [HARDWARE-TESTING.md](HARDWARE-TESTING.md). After boot,
check `uname -r`, the installed module/config identities and early firmware
errors before activating new hardware. Ethernet first activation is explicit;
booting this kernel alone does not enable it. Read [ETHERNET.md](ETHERNET.md).
