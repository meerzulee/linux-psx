<div align="center">
  <h1>Linux psX</h1>
  <p><strong>Linux for PlayStation. Across generations.</strong></p>
  <p>PS4 &middot; PS5 &middot; Future PlayStation consoles</p>
  <p>
    <a href="https://github.com/meerzulee/linux-psx/wiki">The wiki</a>
    &middot;
    <a href="https://github.com/meerzulee/omarchy-psx">Omarchy psX</a>
    &middot;
    <a href="https://github.com/meerzulee/linux-psx/issues">Ideas &amp; contributions</a>
  </p>
</div>

## The goal

We're porting Linux to **PlayStation 4 and PlayStation 5**, with the long-term
ambition of bringing it to upcoming PlayStation consoles.

Linux psX brings kernel patches, configurations and build tooling into one
project. Each console keeps its own kernel targets and hardware validation,
while sharing tools and knowledge across generations.

## Console roadmap

| Console | Project status |
| --- | --- |
| **PlayStation 4** | Buildable Linux 6.18.44 Baikal target: 56 ordered patches, including MTS2 Ethernet and native autoload. New psX artifacts require hardware qualification. |
| **PlayStation 5** | Separate `7.2-ps5` development target from pinned community sources; no local hardware acceptance. See [PS5 notes](docs/PS5.md). |
| **Future PlayStation consoles** | Long-term exploration as hardware access and public research become available. |

## What we're building

- Console-specific kernel patches and configurations.
- Recorded build inputs with pinned upstream Linux sources.
- Matching kernel and module artifacts for each target.
- Clear hardware test records and compatibility documentation.

PS4 and PS5 targets can use different Linux versions and patch sets. Sharing a
repository will let us share tooling while qualifying each console separately.

## The psX projects

| Project | Role |
| --- | --- |
| **Linux psX** | Kernel patches, hardware drivers, configurations, source provenance, builds and matching modules. |
| **[Omarchy psX](https://github.com/meerzulee/omarchy-psx)** | Omarchy setup, boot profiles and desktop integration. |

The kernel has no Omarchy, desktop, package-manager, init-system or network-manager
dependency. Use a compatible console loader, initramfs, firmware and Linux root
filesystem of your choice. Those components are separate from this repository.

## Build Linux 6.18 for PS4

The active target is `6.18-ps4-baikal`, producing
`6.18.44-psx-ps4-baikal2`. It fetches upstream Linux v6.18.44 at
`1efe5d048a391de3ead2804b2e7f86376c356cc5` and applies 56 ordered patches.
No private kernel archive or existing Omarchy checkout is required.

```sh
git clone https://github.com/meerzulee/linux-psx.git
cd linux-psx
make check

# Linux x86-64: install prerequisites from docs/BUILDING.md, then:
make TARGET=6.18-ps4-baikal

# macOS ARM64: start OrbStack with Docker context "orbstack", then:
./scripts/build-kernel-orbstack -t 6.18-ps4-baikal
```

Choose the command for your host. Outputs are in `output/6.18-ps4-baikal/`:
`bzImage`, the **whole matching modules archive**, resolved `config`,
`System.map`, build manifest, version and checksums. Device firmware is neither
embedded nor bundled; supply it separately, including early initramfs firmware
where required.

[GitHub Actions](https://github.com/meerzulee/linux-psx/actions) builds on native
Linux and packages these outputs with generic `artifacts.json` and complete
corresponding GPL source as **two separate downloads**:

- **`linux-psx-<target>-<commit>`**: the small kernel, matching modules, build
  data and instructions. Choose this to test Linux.
- **`linux-psx-<target>-source-<commit>`**: complete corresponding source and matching
  build data for rebuilding or inspection.

Each download has its own checksums. CI artifacts expire after 30 days.
Creating a public experimental release is a separate, manual workflow option;
it likewise offers a kernel ZIP and a separate source archive.

## Ethernet

Built-in Ethernet needs no setup tool. udev loads `psx_mts_net` at boot and the
first open of `psx0` brings it up; NetworkManager connects with its default
wired profile, with or without the cable plugged in at boot. Other network
managers need a configuration that names `psx0`. See [the Ethernet
guide](docs/ETHERNET.md) for limits and the `psx_mts_net.autoarm=0` and
`modprobe.blacklist=psx_mts_net` switches.

## Experimental Liverpool video decoding

A separate Linux 7.2.11 UVD backend passed exact H.264 decoding through
FFmpeg/VAAPI and a bounded 1080p30 playback test on one Liverpool PS4. An
isolated PS4 Mesa 26.2.5 candidate displayed clean picture and live decoder
stats in the tested OpenGL path. This is a source checkpoint for review; the
default target/CI build and supported release status are unchanged.
See [the UVD guide](docs/UVD.md) for source, artifact identities, reproduction
boundaries and remaining hardware gates.

## Current stage

**PS4 development target.** The 54 base patches remain byte-for-byte preserved.
The MTS2 Ethernet patch is imported unchanged from the published PS4 project;
its GPL notices and original contributors remain credited. The active target
adds psX native Ethernet autoload, a separate firmware-free config and a neutral
build identity. Historical configs and the experimental ultrawide series remain
reference inputs.

The active patch set, built locally as `6.18.44-psx-ps4-baikal-native-dev1`,
passed two native Ethernet boots on one Baikal PS4, launched with and without
the cable and including unplug and replug. The `6.18.44-psx-ps4-baikal2` CI
build has **not** been boot-tested. Source migration and CI success do not
qualify hardware. The separate PS5 development candidate has no local
hardware acceptance; future-console support remains planned.

| Read next | Contents |
| --- | --- |
| [Build guide](docs/BUILDING.md) | Native Linux x86-64, macOS ARM64/OrbStack and CI. |
| [Generic boot guide](docs/BOOTING.md) | Loader/initramfs/rootfs requirements for any distribution. |
| [Ethernet](docs/ETHERNET.md) | Native autoload, retained DMA, hardware status and limits. |
| [PS4 status](platforms/ps4/STATUS.md) | Historical evidence and remaining qualification. |
| [PS4 development knowledge](platforms/ps4/docs/README.md) | Curated display/MSI, Ethernet, fan/storage/UART findings and historical evidence. |
| [Contributing](CONTRIBUTING.md) | Patch workflow, portable core tests and hardware report requirements. |
| [Artifact contract](docs/ARTIFACTS.md) | Kernel/modules identities, metadata and corresponding source. |
| [PS4 patch guide](docs/PS4-PATCHES.md) | Scope, ordering, base pin and checksum verification. |
| [Credits and sources](CREDITS.md) | Original contributors, exact source trees and recovered upstream links. |
| [Universal-kernel research](docs/UNIVERSAL-KERNEL.md) | Shared-source feasibility and the work needed for one PS4/PS5 binary. |
| [rmux 7.x imports](docs/RMUX-IMPORT.md) | Per-commit Strawberry kernel sources for the Linux 7.2 port. |
| [Linux 7.2 port](docs/PORT-7.2.md) | Development targets: the Baikal port, its separate `7.2-ps4-baikal-native-dev` native-Ethernet candidate, and rmux's all-southbridge stack. Local Baikal boots are scoped observations; CI binaries have no hardware acceptance. |
| [Import manifest](provenance/ps4-import.json) | Per-file hashes, immutable source paths and ordered series. |
| [Active target provenance](provenance/6.18-ps4-baikal.json) | MTS2 import, native autoload, config adaptations and the active 56-patch stack. |
| [Wiki](https://github.com/meerzulee/linux-psx/wiki) | Guides, status, FAQ, troubleshooting and glossary. |

## Legal

Linux psX is an independent community project. It is not affiliated with,
authorized, sponsored or endorsed by Sony Interactive Entertainment.
"PlayStation", "PS4" and "PS5" are trademarks of Sony Interactive
Entertainment Inc., used only to identify the hardware the kernel targets.

The guides begin with an established console Linux boot environment and cover
kernel builds, matching modules, hardware observations and recovery. Reviewed
findings from private background work are described as private hardware
research. Selected kernels embed no device firmware; the
[artifact contract](docs/ARTIFACTS.md) defines corresponding-source packaging.
Original authorship and per-file license notices are recorded in
[CREDITS.md](CREDITS.md).

---

Have console Linux experience, hardware to test, or an idea?
[Join the work](https://github.com/meerzulee/linux-psx/issues).
