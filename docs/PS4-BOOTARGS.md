# PS4/Baikal kernel boot arguments for other distributions

This is the distribution-independent boot recipe for the current Linux psX
7.2 Baikal development target. Omarchy, systemd, NetworkManager and a specific
root filesystem are not required.

The target is experimental. A command line below is a starting point for a
bounded test, not a hardware-acceptance claim.

## Kernel and modules

Build or download `7.2-ps4-baikal-native-dev` and install the **whole matching**
module tree. The current CI release is
`7.2.11-psx-ps4-baikal-native-dev2`.

Do not mix a kernel with another release's modules. In particular, install
`psx_mts_net.ko` from the same archive and run the target distribution's
`depmod`.

The exact working lab configuration is in the private lab archive; this public
target has its own artifact identity.

## Minimal platform arguments

These are the important PS4/Baikal platform settings:

```text
pci=nocrs intremap=off
```

Keep them unless the chosen loader or kernel documents a different requirement.
They avoid firmware/ACPI interrupt routing assumptions that do not match this
console.

For visible early diagnostics over the physical UART, the tested form is:

```text
earlycon=uart8250,mmio32,0xC890E000,115200n8 console=null keep_bootcon
8250.nr_uarts=0
```

`earlycon` is useful for bring-up. `console=null` prevents the ordinary 8250
driver from claiming a console that is not registered on this platform; adapt
this only if your loader has a proven serial-console arrangement. A quiet boot
is not proof of success.

## Root and display

Use a root selector supported by your own initramfs:

```text
root=UUID=<your-root-uuid> rootfstype=ext4 rootwait rw
```

A label such as `root=LABEL=PS4LINUX` is fine only if your initramfs resolves
labels. The kernel cannot mount a root filesystem unless the required storage
driver is built in or present in the initramfs.

For the tested PS4 HDMI setup:

```text
drm.edid_firmware=edid/1920x1080.bin
video=HDMI-A-1:1920x1080@60D
```

The EDID file must be present in the initramfs or firmware search path. For a
headless test, leave `amdgpu.modeset=0`; for a desktop, use the normal amdgpu
modeset path and test the display separately.

## Internal SATA test profile

The current internal-SATA experiment uses the Baikal PHY profile and the
command-scoped AHCI completion probe:

```text
libata.force=1:1.5Gbps
ps4_bpcie.ps4_sata_phy_orbit=1
libahci.ps4_ahci_poll_ms=0
libahci.ps4_ahci_completion_probe_ms=5
```

Meanings:

- `libata.force=1:1.5Gbps` limits the first ATA link to the tested 1.5 Gbps
  profile. It is a test setting, not a universal requirement.
- `ps4_bpcie.ps4_sata_phy_orbit=1` selects the Orbis-derived SATA PHY profile.
- `libahci.ps4_ahci_poll_ms=0` disables the legacy unconditional 5 ms timer.
- `libahci.ps4_ahci_completion_probe_ms=5` enables the command-scoped probe.

Do not enable both completion mechanisms. Verify the effective values after
boot:

```sh
cat /sys/module/libahci/parameters/ps4_ahci_poll_ms
cat /sys/module/libahci/parameters/ps4_ahci_completion_probe_ms
cat /sys/module/ps4_bpcie/parameters/ps4_sata_phy_orbit
```

The internal disk layout, encryption, UFS layer and installer remain
unqualified. Keep the internal path read-only or limited to an identified test
image until your own evidence covers writes, repeat boots and recovery. Never
write Orbis system partitions.

## Ethernet

The native driver is a module (`psx_mts_net`) and is loaded by its PCI alias.
The first interface open publishes `psx0`. No distribution-specific service is
required. NetworkManager, systemd-networkd and ifupdown need their normal
wired-interface configuration; `psx0` does not match many `en*`/`eth*` rules.

To disable it for a test:

```text
psx_mts_net.autoarm=0
```

or:

```text
modprobe.blacklist=psx_mts_net
```

Do not unload or reset an already-published generation.

## Firmware

The kernel is firmware-free. Early display or WiFi/Bluetooth firmware must be
available to the initramfs if its driver is built in. See
[BOOTING.md](BOOTING.md#early-firmware-and-the-initramfs) for the current PS4
firmware notes and license limits.

WiFi/Bluetooth are not part of the current working Omarchy root because the
required MediaTek files are absent. Ethernet is the demonstrated network path.

## Omarchy-specific arguments

Do not copy these into a generic distribution:

```text
omarchy.* ps4root.mode=hybrid-usb-rw
```

They select Omarchy distribution/initramfs behavior, not kernel platform
support. The exact Omarchy boot profile is documented in the companion
repository:

https://github.com/meerzulee/omarchy-psx/blob/master/docs/USING-LINUX-PSX.md

## Suggested first boot

Use a complete recovery set and a bounded UART session. Start with:

```text
pci=nocrs intremap=off
earlycon=uart8250,mmio32,0xC890E000,115200n8 console=null
root=<your-root> rootfstype=<your-fs> rootwait rw
```

Add the display arguments only after the console and root boot; add the SATA
profile only when testing internal storage; add Ethernet only when its module
and network manager configuration are ready.

Check `uname -r`, the installed module layout, `/sys/module/.../parameters`,
`dmesg` for firmware/ATA errors and the actual mounted root before making a
support claim.
