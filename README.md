<div align="center">
  <h1>Linux psX</h1>
  <p><strong>Linux kernel development for <a href="#playstation-4">PlayStation 4</a> and <a href="#playstation-5">PlayStation 5</a>.</strong></p>
  <p>Console kernels, graphics drivers, configurations and build tools.</p>
  <p>
    <a href="https://github.com/meerzulee/linux-psx/wiki">Wiki</a>
    &middot;
    <a href="https://github.com/meerzulee/omarchy-psx">Omarchy psX</a>
    &middot;
    <a href="https://github.com/meerzulee/linux-psx/issues">Issues &amp; contributions</a>
  </p>
</div>

Linux psX brings PS4 and PS5 kernel development into one source project with
separate console targets. It owns the kernel patches, builds, matching modules, PS4 Mesa source
and hardware evidence. Distribution and desktop integration live in
[Omarchy psX](https://github.com/meerzulee/omarchy-psx).

## PlayStation 4

Current PS4 development focuses on **Linux 7.2.11 for Baikal consoles**.
The project includes AMDGPU changes, native MTS Ethernet and an experimental
Liverpool UVD decoder. Aeolia and Belize have reference source stacks; local
hardware testing has been on one PS4 Slim with a **Baikal B1 southbridge**.

### Compatibility by southbridge

This table summarizes recorded project evidence, including separate experimental
builds. Columns identify southbridges; GPU and wireless chipsets must be
identified separately. Results apply to the exact tested artifacts, not every
console or a fresh rebuild.

✅ Observed working · 🧪 Experimental or partial · ❌ Unavailable or failing ·
⏳ Not yet tested or qualified

| Feature | Baikal (tested B1) | Aeolia | Belize |
| --- | --- | --- | --- |
| Linux 7.2 kernel | ✅ 7.2.11 boot observed | ⏳ Reference source; unbooted | ⏳ Reference source; unbooted |
| GPU / OpenGL / display | ✅ Liverpool; OpenGL 4.6; 1080p at 60 Hz | ⏳ Untested | ⏳ Untested |
| Mesa version | 🧪 26.1.7 restored; fixed 26.2.5 isolated GL tests passed; system trial rolled back | ⏳ Untested | ⏳ Untested |
| Vulkan | 🧪 26.2.5 dev5: HTILE depth oracle and 120 cube presentations passed | ⏳ Untested | ⏳ Untested |
| UVD / H.264 decode | 🧪 UVD runtime experiments passed 1080p30; the current default hybrid boot falls back to software | ⏳ Untested | ⏳ Untested |
| VCE / hardware encoding | ⏳ Not qualified | ⏳ Untested | ⏳ Untested |
| Ethernet | ✅ Native `psx0`; DHCP and bidirectional traffic | ⏳ Untested | ⏳ Untested |
| Internal storage / SATA | 🧪 Hybrid USB root + internal ext4 test image RW; 64 MiB write/read and 256 MiB direct read passed; repeat boots, stress and encryption/UFS remain unqualified | ⏳ Untested | ⏳ Untested |
| External USB root | ✅ Read/write root booted on 7.2 | ⏳ Untested | ⏳ Untested |
| Wi-Fi | 🧪 Historical external firmware stack worked; current Omarchy root has no MT7668 firmware | ⏳ Untested | ⏳ Untested |
| Bluetooth audio | 🧪 Historical external stack worked; current Omarchy root has no firmware | ⏳ Untested | ⏳ Untested |
| HDMI audio | ✅ Clean 44.1 kHz; native 48 kHz remains broken | ⏳ Untested | ⏳ Untested |
| Temperature monitoring | ✅ Historical `k10temp` readings on 6.18 | ⏳ Untested | ⏳ Untested |
| Fan control | ⏳ Not qualified | ⏳ Untested | ⏳ Untested |
| Blu-ray / optical | ⏳ Not detected; controller port and power path unknown | ⏳ Untested | ⏳ Untested |
| Shutdown / reboot / suspend | ⏳ Not qualified; UVD recovery restrictions apply | ⏳ Untested | ⏳ Untested |

The current internal-SATA result is a hybrid USB-root Omarchy boot with an
internal ext4 test image mounted read-write. The legacy AHCI timer was disabled
and the command-scoped completion probe was active. Short write/read and direct
read tests passed. Repeat boots, sustained/random I/O, the Sony encrypted/UFS
stack, installation and a proper production completion path remain unqualified.

### Remaining hardware work

- **Internal SATA:** repeat hybrid boots; sustained and random I/O; flush and
  error-path testing; validate the Sony encrypted/UFS layers; only then consider
  an installer or internal-root acceptance.
- **Thermal/fan:** read-only fan RPM/PWM discovery and sustained-load cooling;
  the current `k10temp` readings are not fan-control qualification.
- **Power lifecycle:** exact-artifact shutdown, reboot and suspend/resume
  acceptance. UVD recovery restrictions still apply.
- **Video:** VCE encoding, broader UVD profiles/sustained playback and a
  system-wide Mesa/Vulkan adoption path. Current UVD and Mesa results are
  experimental and artifact-scoped.
- **Audio:** 44.1 kHz is clean; fix native 48 kHz and verify it after a cold
  boot. ALSA/PipeWire enumeration alone is not acceptance.
- **Wireless:** cold firmware loading, reconnect persistence and broader
  Wi-Fi/Bluetooth device coverage without the retained external boot stack.
- **Ethernet:** IRQ mode, offloads, cable recovery, reset and suspend; the
  current native path is experimental and has no safe unload/reset claim.
- **CPU/RAM:** cpufreq policy, bounded memory allocation and a measured
  RAM/VRAM split. See [PS4 dynamic memory](docs/PS4-DYNAMIC-MEMORY.md).

The [7.2 port notes](docs/PORT-7.2.md) record the core boot and the separate
native Ethernet test. Wi-Fi and Bluetooth results used the retained external
firmware/boot environment; cold firmware loading remains untested. The later
[Bluetooth test](https://github.com/meerzulee/omarchy-psx/blob/master/platforms/ps4/experiments/SESSIONS.md#exp-20261018-002--bluetooth-panel-and-audio-on-linux-72)
confirmed one audio-device connection and playback. Reconnect persistence is
still untested. Storage and thermal observations are retained in the
[historical capability record](platforms/ps4/docs/CAPABILITIES.md).

The [UVD experiment](docs/UVD.md) matched all 900 frames of a native 1080p30
H.264 fractal against a software reference. Five playback loops reported zero
display/decoder drops at approximately 1.00× speed. The process-local Mesa
26.2.5 candidate displayed clean picture and text in the tested OpenGL path;
it is not a system-wide Mesa upgrade or a Vulkan qualification. Broader video
profiles, seeking, sustained playback and repeated cold boots remain open.

A later 26.2.5 system trial rendered clean OpenGL/WebGL, but its Vulkan test
stalled and the experimental UVD runtime timed out. The system upgrade was
rolled back. The [Mesa feature matrix](docs/MESA-TESTING.md) records observed
passes, failures and untested cases; full desktop adoption remains open.

The newer isolated dev5 candidate bounds the compute-fill shader and restores
HTILE. It passes the formerly failing depth/cube cases and repeats exact
120-frame and 900-frame UVD decoding. These results apply to the recorded
artifacts on one console; the system remains on the original Mesa packages.

### Build the Linux 7.2 Baikal kernel

Use `7.2-ps4-baikal-native-dev` for the Baikal port with native Ethernet.
It produces `7.2.11-psx-ps4-baikal-native-dev2`, adding the HDMI underscan fix to
the unchanged native-Ethernet stack. This new kernel revision is not boot-tested. The UVD runtime is a separate
experimental source/build path described in the UVD guide.

```sh
git clone https://github.com/meerzulee/linux-psx.git
cd linux-psx
make check

# Linux x86-64, after installing the build prerequisites:
make TARGET=7.2-ps4-baikal-native-dev

# macOS ARM64, with OrbStack running and Docker context "orbstack":
./scripts/build-kernel-orbstack -t 7.2-ps4-baikal-native-dev
```

Choose the build command for your host. The [build guide](docs/BUILDING.md)
lists dependencies and the native/cross-build setup. The output directory is
`output/7.2-ps4-baikal-native-dev/`, containing the kernel, **whole matching
module archive**, resolved config, build manifest and checksums.

### Latest CI build

The public `PS4 - Linux 7.x` workflow built
`7.2.11-psx-ps4-baikal-native-dev2` successfully from commit `804365c`:

- 65 active patches
- kernel SHA-256 `aac6cc8b7036a1c5d3436df68531c931b731195677160e181389a83842393a43`
- artifact `linux-psx-7.2-ps4-baikal-native-dev-804365c`
- [CI run](https://github.com/meerzulee/linux-psx/actions/runs/39784010700)

This CI target has not been boot-tested and does not include the separate
audio-DTO/`7.2-ps4` experimental target.

Keep a kernel and all its modules together. Device firmware is external and
is not embedded or bundled by this repository. A compatible console loader,
initramfs and Linux root filesystem are separate inputs; see the
[boot guide](docs/BOOTING.md). New builds require their own hardware tests.

For distribution-independent PS4 Baikal boot arguments, including the internal
SATA and Ethernet test profile, see [PS4 boot arguments](docs/PS4-BOOTARGS.md).

### Ethernet and graphics

The native Ethernet driver publishes `psx0`; the network manager's normal
interface open brings it up. [Ethernet notes](docs/ETHERNET.md) describe the
autoload switches, observations and remaining limits.

The experimental UVD backend is Liverpool-only and off by default. It exposes
8-bit NV12 AVC with one active handle. It has no HEVC hardware decoder and
no qualified 4K, 1080p60 or VCE path. Its retained-DMA recovery restrictions
are documented in the UVD guide. The PS4 Mesa changes, native builder and playback tools live
in [userspace/mesa](userspace/mesa/README.md). Omarchy owns system packaging
and desktop integration.

### Linux 6.18 history

The preserved Linux 6.18 Baikal build, its artifact layout and historical
qualification are documented separately in [Linux 6.18](docs/LINUX-6.18.md).
Select the 7.2 target explicitly when building the current development kernel.

Existing research and imported patch records remain in the repository.
[Credits and sources](CREDITS.md) retain the original contributors, licenses
and provenance.

## PlayStation 5

PS5 is a separate development candidate with no local hardware acceptance.
This section will expand as PS5 work progresses; see [PS5 notes](docs/PS5.md)
for the existing source and build record.

## Contributing and licensing

Read [CONTRIBUTING.md](CONTRIBUTING.md) for source checks and hardware reports.
Kernel artifacts require complete corresponding GPL source and matching
metadata; see the [artifact contract](docs/ARTIFACTS.md).

Linux psX is an independent community project, unaffiliated with Sony
Interactive Entertainment. PlayStation, PS4 and PS5 identify the target
hardware. Existing authorship and per-file license notices remain in force.
