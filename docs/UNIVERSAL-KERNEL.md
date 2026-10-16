# Can PS4 and PS5 share one Linux kernel?

Research snapshot: 2026-10-16. These findings come from source inspection,
not from a merged kernel build or console test.

**A shared source project is practical. One identical kernel binary for PS4
and PS5 is technically plausible, but the current patch sets need deliberate
integration before that can work.** Neither a universal build nor a universal
boot image is provided by this import.

## Three different goals

| Goal | Assessment |
| --- | --- |
| One repository and common tooling, with separate console targets | Practical now; this repository starts that organization. |
| One Linux source tree supporting both consoles, built with separate configs | Plausible after choosing a common base and reconciling the patches. |
| One identical `bzImage` booting on both consoles | Longer-term engineering goal requiring reliable platform identification and runtime-gated behavior. |

Both inspected platform Kconfig definitions require `X86_64`. That makes a
shared x86-64 image possible in principle; it does not establish compatible
boot handoff, interrupt routing, firmware or device initialization.

## Sources inspected

| Component | Immutable revision | Linux base |
| --- | --- | --- |
| [PS4 patch source](https://github.com/meerzulee/linux-ps4/tree/f61663b369b76f1fc7e8cf29dd946a1bfc5b1e75) | `f61663b369b76f1fc7e8cf29dd946a1bfc5b1e75` | 6.18.44 |
| [PS5 community patches](https://github.com/ps5-linux/ps5-linux-patches/tree/753c7936dcb079bfeaaa0bb19dfc495744266500) | `753c7936dcb079bfeaaa0bb19dfc495744266500` | 7.2.3, from its pinned `.config` |
| [PS4 loader](https://github.com/ps4-linux/ps4-linux-loader/tree/9acef9fbf79097a2bb39d6c9c17228198bc445cc) | `9acef9fbf79097a2bb39d6c9c17228198bc445cc` | Boot-interface reference |
| [PS5 loader](https://github.com/ps5-linux/ps5-linux-loader/tree/64637c177eaed277fb6ccce394cbb29dd047b893) | `64637c177eaed277fb6ccce394cbb29dd047b893` | Boot-interface reference |

The PS5 source currently uses a single `linux.patch` plus a full config.
Its README selects upstream Linux using the version recorded in that config.
The current main snapshot is 7.2.3; it should not be confused with an older
7.1.7 release asset. Research pins and file digests are recorded in
[provenance/ps5-research.json](../provenance/ps5-research.json).

## Finding 1: the boot identifiers collide

The [PS4 loader header](https://github.com/ps4-linux/ps4-linux-loader/blob/9acef9fbf79097a2bb39d6c9c17228198bc445cc/linux/ps4-kexec-common/linux_boot.h#L23)
defines `X86_SUBARCH_PS4` as `5`. The
[PS5 loader header](https://github.com/ps5-linux/ps5-linux-loader/blob/64637c177eaed277fb6ccce394cbb29dd047b893/include/linux.h#L7)
also defines `X86_SUBARCH_PS5` as `5`. Their boot code writes the corresponding
value to `hardware_subarch`.

Both kernel patches append their own platform entry in the same place in
`enum x86_hardware_subarch`, then dispatch platform early setup through it.
Appending both names to a merged enum would give them different numbers,
while existing loaders would still both send `5`.

**Inference:** a shared binary cannot select the correct console using this
field alone. Integration needs a coordinated boot-interface extension or a
reliable additional early discriminator, while preserving compatibility with
existing loaders. No replacement numeric ID is chosen here. The upstream
[x86 boot protocol](https://docs.kernel.org/arch/x86/boot.html)
documents this header and its subarchitecture fields.

## Finding 2: enabling both Kconfig options is insufficient

In the inspected PS5 `linux.patch`, `CONFIG_X86_PS5` controls behavior at
compile time in code used outside its dedicated platform directory. Examples:

- `arch/x86/pci/irq.c`: legacy IRQ enable/disable functions return immediately
  when PS5 support is compiled in.
- `drivers/firmware/dmi-id.c`: the system is labeled PlayStation 5 under the
  compile-time option, without first checking the running console there.
- `amdgpu_bios.c`: the platform VBIOS address is hardcoded and alternative BIOS
  acquisition paths are excluded under the option.
- `amdgpu_vcn.c`: the VCN firmware prefix changes to `ps5_vcn` when the option
  is enabled.

These are observations about the
[pinned PS5 patch](https://github.com/ps5-linux/ps5-linux-patches/blob/753c7936dcb079bfeaaa0bb19dfc495744266500/linux.patch),
not claims that the PS5-only build is defective. **Inference:** a multi-console
build needs a review of these sites and of the PS4 equivalents, with console
or device checks replacing platform-wide compile-time assumptions where needed.

## Finding 3: the patch bases and shared subsystems differ

The 54 active PS4 patches touch 311 unique paths; the PS5 patch touches 86.
Fourteen paths are shared, including x86 Kconfig, boot parameters, `head64.c`,
AMDGPU, PCI probing, AMD IOMMU initialization and USB build wiring.

This is a touched-path comparison, not a count of textual conflicts. Changes
to different files can still interact semantically. The full intersection is
recorded in `provenance/ps5-research.json`.

A common source tree first requires selecting one upstream Linux base and
porting one or both patch sets to it. The configs also differ: the imported
PS4 baseline enables AMDGPU CIK support and uses 8 CPUs; the inspected PS5
config disables CIK and uses 16. A shared config would need the required
drivers, adequate CPU limits, compatible early boot options and separate
device/firmware selection.

## Recommended sequence

1. Preserve the current PS4 patch stack and its provenance. Keep PS5 research
   separately pinned until its patch, config and firmware requirements are
   reviewed for integration.
2. Establish independently working PS4 and PS5 targets. Keep matching modules,
   boot artifacts and acceptance records per console.
3. Choose a maintained common Linux base, rebase the patches and reconcile
   shared subsystem changes while still building two configs.
4. Design compatible early platform identification and audit every PS4/PS5
   compile-time quirk for safe runtime selection.
5. Build a combined config and validate repeated boots and essential devices
   on both consoles before presenting the same binary as supported.

A common kernel would still be launched through console-specific boot loaders.
It would not by itself unify the firmware exploit, first-stage boot process,
root filesystem, initramfs or recovery procedures. Those can remain separate
even when the kernel source is shared.

Future consoles remain a porting goal. Their architecture and accessible boot
interfaces cannot be assumed to match today's x86-64 systems.
