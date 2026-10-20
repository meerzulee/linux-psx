<div align="center">
  <h1>Linux psX</h1>
  <p><strong>Linux 7.2.x for PlayStation 4 Baikal.</strong></p>
  <p>Console kernel patches, native Ethernet and experimental Liverpool UVD.</p>
  <p>
    <a href="https://github.com/meerzulee/linux-psx/wiki">Wiki</a>
    &middot;
    <a href="https://github.com/meerzulee/omarchy-psx">Omarchy psX</a>
    &middot;
    <a href="https://github.com/meerzulee/linux-psx/issues">Issues &amp; contributions</a>
  </p>
</div>

## PS4 Baikal on Linux 7.2

Current PS4 development focuses on **Linux 7.2.11 for Baikal consoles**.
The repository contains the console platform patches, AMDGPU changes, native
MTS Ethernet, kernel configurations, build tools and recorded hardware results.

The normal 7.2 Baikal build reached Linux and passed a short wired-network
test on one console. A separate experimental Liverpool UVD build decoded
H.264 through FFmpeg/VAAPI: all 900 frames of a native 1080p30 fractal matched
the software reference. Its bounded playback test completed five loops with
zero reported display/decoder drops and measured speed approximately 1.00×.
The isolated PS4 Mesa candidate also displayed clean picture and text in the
tested OpenGL path.

These results apply to the exact tested artifacts. Broader video profiles,
seeking, long-duration playback and repeated cold boots remain open. See
[the UVD guide](docs/UVD.md) for the source, proof and current limits, and
[the Linux 7.2 port notes](docs/PORT-7.2.md) for the kernel variants.

## Build the Linux 7.2 Baikal kernel

Use `7.2-ps4-baikal-native-dev` for the Baikal port with native Ethernet.
It produces `7.2.11-psx-ps4-baikal-native-dev1`. The UVD runtime is a separate
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

Keep a kernel and all its modules together. Device firmware is external and
is not embedded or bundled by this repository. A compatible console loader,
initramfs and Linux root filesystem are separate inputs; see the
[boot guide](docs/BOOTING.md). New builds require their own hardware tests.

## Ethernet and graphics

The native Ethernet driver publishes `psx0`; the network manager's normal
interface open brings it up. [Ethernet notes](docs/ETHERNET.md) describe the
autoload switches, observations and remaining limits.

The experimental UVD backend is Liverpool-only and off by default. It exposes
8-bit NV12 AVC with one active handle. It has no HEVC hardware decoder and
no qualified 4K, 1080p60 or VCE path. Its retained-DMA recovery restrictions
are documented in the UVD guide. The PS4 Mesa changes and playback tools live
in [Omarchy psX](https://github.com/meerzulee/omarchy-psx/tree/master/platforms/ps4/experiments/mesa-uvd).

## Linux 6.18 and other work

The preserved Linux 6.18 Baikal build, its artifact layout and historical
qualification are documented separately in [Linux 6.18](docs/LINUX-6.18.md).
Select the 7.2 target explicitly when building the current development kernel.

PS5 remains a separate development candidate with no local hardware acceptance;
see [PS5 notes](docs/PS5.md). Existing research and imported patch records remain
in the repository. [Credits and sources](CREDITS.md) retain the original
contributors, licenses and provenance.

## Contributing and licensing

Read [CONTRIBUTING.md](CONTRIBUTING.md) for source checks and hardware reports.
Kernel artifacts require complete corresponding GPL source and matching
metadata; see the [artifact contract](docs/ARTIFACTS.md).

Linux psX is an independent community project, unaffiliated with Sony
Interactive Entertainment. PlayStation, PS4 and PS5 identify the target
hardware. Existing authorship and per-file license notices remain in force.
