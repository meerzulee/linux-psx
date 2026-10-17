# PS4 evidence and remaining gaps

The historical acceptance scope is one PS4 Slim with Baikal B1 southbridge,
Orbis 12.02 handoff, a specific loader/initramfs and external USB root. The
August runtime record used `6.18.44-ps4-baikal` build `#5`; October Ethernet
results used separate experimental modules and MTS1/MTS2 kernel identities.
The new firmware-free `6.18.44-psx-ps4-baikal1` is not a hardware-qualified
replacement merely because it carries the patches.

| Subsystem | Recorded evidence | Remaining qualification |
| --- | --- | --- |
| CPU | Eight cores online and short pinned work on each | No cpufreq policy exposed; sustained stress not accepted |
| RAM | About 6.8 GiB usable in that VRAM/loader configuration | Bounded memory-allocation acceptance pending; VRAM selection changes the split |
| Display | 1080p XFCE and repeated staged bridge completion | Other modes, monitors, console revisions and repeated cold boots |
| GPU | Direct radeonsi OpenGL 4.6; roughly 59 FPS for an eight-second `glxgears` smoke; eight-second RADV Vulkan 1.3 `vkcube` | Short rendering tests do not qualify sustained load, reset, suspend or video codecs |
| External USB | Root disk, keyboard and mouse operated | Device-specific recovery and power-loss behavior remain separate |
| Internal SATA | Controller/disk enumeration, followed by timeouts/read failures | Degraded; no Sony filesystem writes or installation accepted |
| MT7668 Wi-Fi | Association, DHCP, SSH, DNS and verified HTTPS on the old firmware-bearing stack | The new kernel embeds no firmware; supply appropriate external firmware through the distro/boot arrangement and retest |
| Ethernet | EXP014/015 transport and EXP016 continuous MTS2 traffic | Experimental; see the separate [evidence ladder](ETHERNET-HISTORY.md) |
| HDMI audio | HDA/ALSA/PipeWire enumeration and accepted PCM samples | No audible sound in the recorded test; playback remains partial |
| Bluetooth | Firmware and `hci0` present | Scanning, pairing and DualShock reconnect pending |
| Temperature/fan | Credible `k10temp` readings around 60–61°C during short tests | No exposed fan RPM/PWM interface; sustained stress blocked by the recorded fan gap |
| Power | Interfaces/source paths exist | Shutdown/reboot still need their own exact-artifact acceptance; suspend unavailable in the recorded runtime |

These are historical observations, not live probe results. The earlier
[runtime snapshot](https://github.com/meerzulee/linux-ps4/blob/386df1ad97d92defee1794bf961a0e2f5dc85a1b/docs/RUNTIME-2026-08-11.md)
and [capability record](https://github.com/meerzulee/linux-ps4/blob/386df1ad97d92defee1794bf961a0e2f5dc85a1b/docs/CAPABILITY-TESTS.md)
remain pinned references. The later Ethernet results supersede that snapshot's
older “no usable Ethernet interface” observation only for their tested
experimental builds.

## Fan and SATA are independent blockers

The active thermal patches expose Liverpool IDs to `k10temp` and
`fam15h_power`; they do not implement a qualified Baikal fan controller.
The recorded rmux fan implementation was measured on other southbridge
families, not accepted on this Baikal console. A future port should begin
with read-only temperature/RPM and ICC-result observation. Factory fan
threshold bytes must not be overwritten based on another console's protocol.
No fan-control implementation was imported or enabled by this migration.

The approximately 95-second historical boot delay came from failing internal
SATA reads, before the external USB root completed startup. A narrowly selected
`libata.force=1.00:disable` was prepared as a later boot-profile candidate;
the snapshot did not yet accept that next boot. Never generalize its port
selector to a different machine or confuse SATA timeout with USB-root failure.
Internal storage remains [read-only research](INTERNAL-STORAGE.md).

## Userspace instruction set

The PS4 Jaguar CPU lacks AVX2, BMI1/BMI2 and FMA required by x86-64-v3.
A root filesystem, compiler default or individual binary built for v3 can
therefore fail after `switch_root` even while the kernel boots correctly.
Use the selected distro's documented baseline and inspect problematic ELF
GNU property notes/disassembly before attributing an early userspace crash
to the kernel. A missing ISA property note alone does not prove compatibility.

The historical notes incorrectly generalized this to all modern Arch
packages and a supposed blanket Arch baseline transition. Do not carry that
claim forward. Host distro branding, an init exit signal or a date alone does
not establish the offending opcode. The companion Omarchy repository owns
its package-baseline and desktop-compatibility decisions; this kernel has no
Arch or Omarchy runtime requirement.

## Next acceptance record

Record release/config/module/boot-set hashes together with the console and
loader identity. Distinguish enumeration, smoke tests, sustained traffic and
lifecycle checks. Follow [HARDWARE-TESTING](../../../docs/HARDWARE-TESTING.md),
test one diagnostic variable and keep a verified recovery path. Do not turn
old “working” labels into new support promises without repeatable evidence.
