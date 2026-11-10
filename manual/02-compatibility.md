# Is My Console Supported?

**The short answer:** Linux psX has run on PS4 Slim consoles with a **Baikal** southbridge. Everything else is untested so far. If you have one of those other consoles, [we'd like your help porting to it](09-help-port-your-console.md).

Each result below names the console and build we tested it on, as of 2026-11-10.

## Southbridges in one minute

The southbridge is the chip that connects the slower parts of the console: USB, Ethernet, Wi-Fi, storage and the power controls. Linux needs its own drivers for it, so it decides more about Linux support than the name on the box does.

The PS4 has had four versions:

| Southbridge | Chip marking | Linux psX | Notes |
| --- | --- | --- | --- |
| Baikal | CXD90042GG | `working` | Two Slims have run it, one of them a Baikal B1. A Pro with Baikal has no recorded result. |
| Belize 2 | CXD90046GG | `untested` | No recorded result. |
| Belize | CXD90036G | `untested` | No recorded result. |
| Aeolia | CXD90025G | `untested` | No recorded result. |

In the wider PS4 Linux community, [Belize and Aeolia have the most support and Baikal the least](https://dionkill.github.io/ps4-linux-tutorial/revisions). Linux psX started from the other end, because the maintainer's console is a Baikal.

## Which models can have which southbridge

| Model series | Type | Southbridge |
| --- | --- | --- |
| CUH-10xx, CUH-11xx | Original | Aeolia |
| CUH-12xx | Original | Belize |
| CUH-20xx | Slim | Belize or Baikal, and possibly Belize 2 |
| CUH-21xx | Slim | Belize 2 or Baikal |
| CUH-22xx | Slim | Belize 2 or Baikal |
| CUH-70xx | Pro | Belize |
| CUH-71xx | Pro | Belize 2 or Baikal |
| CUH-72xx | Pro | Belize 2 or Baikal |

The model number, such as CUH-2215B, is printed on the label on your console. Most Slims and later Pros were made with either of two southbridges, so the model number alone often can't settle it. This table comes from community records on the [PS4 Developer wiki](https://www.psdevwiki.com/ps4/Southbridge), and this project hasn't checked it.

## Finding out which one you have

**If Linux already runs on the console,** list its PCI devices:

```sh
lspci -nn | grep 104d
```

| You see | Southbridge |
| --- | --- |
| `104d:90d7` to `104d:90de` | Baikal |
| `104d:90c8` to `104d:90cf` | Belize |
| `104d:908f`, and `104d:909e` to `104d:90a4` | Aeolia |

The kernel has no separate entry for Belize 2, and we have no recorded output from one. If you have one, please [send us yours](09-help-port-your-console.md).

**If Linux doesn't run on the console yet,** the only check today is the marking on the chip itself, inside the console. The DiagnostiX app will show the southbridge on its home screen once it's [public](06-diagnostix.md). Until then, choose **Not sure** on the [report form](https://github.com/meerzulee/linux-psx/issues/new?template=report.yml).

## The hardware

| | Original and Slim | Pro |
| --- | --- | --- |
| Processor | Eight-core AMD Jaguar, 1.6 GHz | Eight-core AMD Jaguar, 2.1 GHz |
| Graphics | AMD Liverpool | AMD Gladius |
| Main memory | 8 GB GDDR5 | 8 GB GDDR5 |
| Extra memory on the southbridge | 256 MB DDR3 | 1 GB DDR3 |

These are general facts about the consoles. Linux psX has only been run on Liverpool so far.

## By console

| Console | Result | Notes |
| --- | --- | --- |
| PS4 Slim with Baikal B1, firmware 12.02, the project's console | `working` | See [By feature](#by-feature). |
| PS4 Slim CUH-2002A with Baikal, firmware 9.60 | `working` | One owner's report. See [A second console](#a-second-console). |
| Other Baikal consoles | `untested` | No recorded result. |
| Aeolia consoles | `untested` | No recorded result. |
| Belize and Belize 2 consoles | `untested` | No recorded result. |
| PS4 Pro | `untested` | No recorded result. |
| PS5 | `untested` | A [kernel candidate](../docs/PS5.md) builds. This project hasn't run it on a console. |

## By feature

These results are from the project's PS4 Slim with a Baikal B1 southbridge, on Linux 7.2.11 builds.

| Feature | Result | What we tested |
| --- | --- | --- |
| Booting Linux 7.2 | `working` | Boots, including the beta2 kernel. |
| USB drive as root | `working` | Reads and writes. |
| CPU and memory | `working` | All eight cores, and about 6.8 GiB of usable RAM with the tested video memory setting. |
| Display and OpenGL | `working` | Liverpool GPU, OpenGL 4.6, 1920×1080 at 60 Hz, and a glmark2 score of 2890. |
| USB keyboard and mouse | `working` | Working. |
| Wi-Fi and Bluetooth audio | `working` | Working, with firmware supplied separately. |
| Shutdown and restart | `beta` | Both passed on the build just before beta2, and restart returns to the PS4 system. |
| Vulkan | `beta` | Works through the PS4 Mesa candidate, and reference images match. |
| H.264 video decoding | `beta` | A 1080p clip matched a reference on all 900 frames. One stream at a time, 8-bit. See [Mesa and Video](08-mesa-and-video.md). |
| Ethernet | `beta` | DHCP, SSH and verified file transfers. See [Ethernet](../docs/ETHERNET.md). |
| Internal drive, reading | `beta` | About 71 MB/s. |
| Internal drive, writing | `untested` | No test yet. |
| HDMI audio | `beta` | Clean through PipeWire at 44.1 kHz, including 48 kHz content. See [Known problems](#known-problems). |
| Temperature readings | `beta` | Correct in our tests. |
| Fan control | `untested` | No test yet. |
| Blu-ray drive | `beta` | Linux detects the drive. Reading discs hasn't been tested yet. |
| Video encoding | `untested` | No test yet. |
| Suspend and resume | `unavailable` | Not built into these kernels yet. Use shutdown instead. |

**What the labels mean:** `working` means it has worked across repeated runs. `beta` means it works in our tests, but expect bugs and [report them](07-results-and-reports.md). `untested` means there's no test yet. `unavailable` means it isn't there yet.

## A second console

A community member [tested an earlier kernel](https://github.com/meerzulee/linux-psx/issues/2) on a PS4 Slim CUH-2002A running firmware 9.60. Its owner reported:

- Boot, 1080p display and UART logging worked.
- Built-in Ethernet handled DHCP, HTTPS and hash-checked transfers, and stayed connected for about 14 hours with no driver errors.
- This console has a **Marvell** Wi-Fi radio rather than the MT7668. Wi-Fi connected and passed HTTPS, and a DualShock 4 paired over Bluetooth.

The same owner has also [booted Linux from an image on an internal SSD](https://github.com/meerzulee/linux-psx/issues/5), twice, the second time with no USB storage attached. See [Internal storage](../platforms/ps4/docs/INTERNAL-STORAGE.md).

## Known problems

A problem goes on this list once we've reproduced it.

- **Native 48 kHz HDMI output sounds robotic.** Forcing the HDMI output to 48 kHz distorted the sound in every test on the project's console, and [measurements on a Baikal console](https://github.com/meerzulee/linux-psx/issues/6) show the same. Run your audio system at 44.1 kHz and 48 kHz content plays cleanly.

## How a result gets on this page

Everything here comes from a test on a real console with a clear pass condition. A successful build or a clean log doesn't count. A feature that passes is `beta`, and repeated runs move it to `working`.

If you have a console that isn't on this page, [help us port to it](09-help-port-your-console.md).

<!-- manual-navigation -->

---

[← Previous](01-welcome-to-linux-psx.md) · [Contents](README.md) · [Next →](03-kernels.md)
