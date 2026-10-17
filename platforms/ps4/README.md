# PS4 kernel targets

The base snapshot is a byte-preserving import from `meerzulee/linux-ps4` at
`f61663b369b76f1fc7e8cf29dd946a1bfc5b1e75`. The MTS2 addition comes from
`386df1ad97d92defee1794bf961a0e2f5dc85a1b`; original headers/licenses remain intact.

## Hardware compatibility

These results come from **one PS4 Slim with a Baikal B1 southbridge**, Orbis
12.02 handoff and the recorded earlier Linux 6.18.44 boot sets. They are not
claims for every PS4 model. **The new `6.18.44-psx-ps4-baikal1` build has not
been boot-tested**; its firmware-free configuration needs separate validation.

| Feature | Recorded status | What works / remaining limits |
| --- | --- | --- |
| CPU and RAM | ✅ Basic operation | Eight CPU cores online; about 6.8 GiB usable in the tested VRAM configuration. Sustained stress and extended memory tests remain pending; no CPU-frequency policy exposed. |
| External USB root filesystem | ✅ Working | Linux boots from the tested external USB drive. |
| USB keyboard and mouse | ✅ Working | Input worked with the tested devices. |
| HDMI display | ✅ Working | 1920×1080 at 60 Hz; other monitors and modes need their own tests. |
| OpenGL and Vulkan | 🟡 Smoke-tested | Radeonsi OpenGL 4.6 and RADV Vulkan 1.3 rendered short tests. Sustained load, GPU reset and video codecs are unqualified. |
| MT7668 Wi-Fi | ✅ Working on historical stack | Association, DHCP, SSH, DNS and HTTPS passed with firmware present. The new psX kernel needs external firmware integration and a retest. |
| Built-in Ethernet | 🟡 Working experimentally | MTS2 passed DHCP, independent two-way transfers, verified HTTPS and operation beyond 900 seconds. Automatic startup, strict admin gating and cable recovery remain incomplete. |
| HDMI audio | 🟡 Partial | Device and PCM stream present; no audible output in the recorded test. |
| Bluetooth | 🟡 Partial | Firmware and controller present; scanning, pairing and DualShock reconnect are unverified. |
| Internal SATA | 🟡 Degraded | Disk detected, but reads timed out or failed. Internal storage is not accepted for normal use. |
| Temperature monitoring | ✅ Working | Credible `k10temp` readings in short tests. |
| Fan control | ❌ Unavailable | No Linux fan RPM/PWM interface or qualified fan-control implementation. |
| UART boot logs | ✅ Working | Early-console capture with the recorded Baikal 6.x boot arguments; normal serial-port registration remains limited. |
| Linux shutdown / reboot | ⚪ Unverified | No accepted exact-artifact lifecycle test recorded. |
| Suspend / resume | ❌ Unavailable | Suspend was not exposed in the recorded runtime; MTS2 suspend is unqualified. |
| Internal Linux installation | ❌ Unsupported | No accepted installation path; Linux-side writes to Sony filesystems are unqualified. |
| Ultrawide display target | 🟡 Experimental | Optional patch series preserved; not qualified by this migration. |
| Other PS4 revisions / PS4 Pro | ⚪ Untested | The Baikal B1 Slim results do not establish compatibility with other consoles. |

✅ = observed working on the recorded stack; 🟡 = limited, partial or
experimental; ❌ = unavailable or unsupported; ⚪ = no accepted test.

Ethernet's overall EXP-20261017-016 verdict remains **DEGRADED**, despite the
successful transport tests. See [PS4 status](STATUS.md),
[detailed capabilities](docs/CAPABILITIES.md) and
[Ethernet evidence](docs/ETHERNET-HISTORY.md) for scope and acceptance gaps.

## Enable Ethernet autoconnect

The optional [PS4 Ethernet tool](../../contrib/networkmanager/README.md) works
with NetworkManager on Arch, CachyOS and other systemd distributions; it does
not require Omarchy. Boot a supported kernel and install its complete matching
modules first. In a new CI artifact bundle:

```sh
sudo python3 ./ps4-ethernet --setup
```

This installs and starts a boot service, then hands `psx0` to NetworkManager
with IPv4 DHCP and autoconnect enabled. Setup is done once, rather than manually
loading the driver each boot. The linked guide covers downloading the script,
older MTS2 metadata and viewing startup logs. Unattended boot and cable recovery
still need hardware qualification.

## Kernel targets

| Target | Base | Active patches | Status |
| --- | --- | --- | --- |
| **Root target `6.18-ps4-baikal`** | Linux 6.18.44 | 55 | Active firmware-free psX build; experimental MTS2 Ethernet; new binaries unqualified. |
| `6.18-baikal` | Linux 6.18.44 | 54 | Imported development baseline; historical tests cover PS4 Slim Baikal B1. |
| `6.18-baikal-ultrawide` | Linux 6.18.44 | 55 | Experimental; the baseline plus one ultrawide delta. |

The inherited `patches/`, `config/` and `targets/` retain their original relative layout.
Resolve `CONFIG_FILE`, `SERIES_FILE`, `PATCHES_DIR` and `CONFIG_FRAGMENTS` from
this directory. They are historical source metadata, not active root targets.
The root `targets/6.18-ps4-baikal.env` instead resolves paths from the repository
root and selects `config/6.18-psx-baikal.config` plus the MTS2 series.
Build with the [root builder](../../docs/BUILDING.md).

Only non-comment entries in a target's `series` are active. Disabled patches
and `.candidate` files remain historical references. The ultrawide series
reuses the baseline through relative paths instead of duplicating it.

See [the import guide](../../docs/PS4-PATCHES.md),
[credits](../../CREDITS.md) and
[the file manifest](../../provenance/ps4-import.json).
