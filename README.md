<div align="center">
  <h1>Linux psX</h1>
  <p><strong>Linux for PlayStation. Across generations.</strong></p>
  <p>PS4 &middot; PS5 &middot; Future PlayStation consoles</p>
  <p>
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
| **PlayStation 4** | Buildable Linux 6.18.44 Baikal target: 54 platform patches plus experimental MTS2 Ethernet. New psX artifacts require hardware qualification. |
| **PlayStation 5** | Community sources researched and pinned; kernel integration is planned. |
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
| **Linux psX** | Kernel patches, hardware drivers, configurations, source provenance, builds and matching module artifacts. |
| **[Omarchy psX](https://github.com/meerzulee/omarchy-psx)** | Omarchy setup, boot profiles, desktop integration and its optional NetworkManager adapter. |

The kernel has no Omarchy, desktop, package-manager, init-system or network-manager
dependency. Use a compatible console loader, initramfs, firmware and Linux root
filesystem of your choice. Those components are separate from this repository.

## Build Linux 6.18 for PS4

The active target is `6.18-ps4-baikal`, producing
`6.18.44-psx-ps4-baikal1`. It fetches upstream Linux v6.18.44 at
`1efe5d048a391de3ead2804b2e7f86376c356cc5` and applies 55 ordered patches.
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
corresponding GPL source. CI artifacts expire after 30 days. Creating a public
experimental release is a separate, manual workflow option.

## Current stage

**PS4 development target.** The 54 base patches remain byte-for-byte preserved.
The MTS2 Ethernet patch is imported unchanged from the published PS4 project;
its GPL notices and original contributors remain credited. The active target
uses a separate firmware-free config and neutral build identity. Historical
configs and the experimental ultrawide series remain reference inputs.

Historical Ethernet tests cover one PS4 Slim Baikal B1; the overall experiment
was **DEGRADED**, with cold-boot repetition, cable recovery and unattended
activation pending. This new release/config/build has **not** been boot-tested.
Source migration and CI success do not qualify hardware. PS5 integration and
future-console support remain planned.

| Read next | Contents |
| --- | --- |
| [Build guide](docs/BUILDING.md) | Native Linux x86-64, macOS ARM64/OrbStack and CI. |
| [Generic boot guide](docs/BOOTING.md) | Loader/initramfs/rootfs requirements for any distribution. |
| [Ethernet contract](docs/ETHERNET.md) | Explicit first activation, retained DMA and current limitations. |
| [PS4 status](platforms/ps4/STATUS.md) | Historical evidence and remaining qualification. |
| [PS4 development knowledge](platforms/ps4/docs/README.md) | Curated display/MSI, Ethernet, fan/storage/UART findings and historical evidence. |
| [Knowledge migration](platforms/ps4/docs/KNOWLEDGE-MIGRATION.md) | Disposition of 117 legacy knowledge/tool sources, corrected hypotheses and deliberate exclusions. |
| [Contributing](CONTRIBUTING.md) | Patch workflow, portable core tests and hardware report requirements. |
| [Artifact contract](docs/ARTIFACTS.md) | Kernel/modules identities, metadata and corresponding source. |
| [PS4 patch guide](docs/PS4-PATCHES.md) | Scope, ordering, base pin and checksum verification. |
| [Credits and sources](CREDITS.md) | Original contributors, exact source trees and recovered upstream links. |
| [Universal-kernel research](docs/UNIVERSAL-KERNEL.md) | Shared-source feasibility and the work needed for one PS4/PS5 binary. |
| [rmux 7.x imports](docs/RMUX-IMPORT.md) | Per-commit Strawberry kernel sources for the Linux 7.2 port. |
| [Linux 7.2 port](docs/PORT-7.2.md) | Compile-tested development targets: `7.2-ps4-baikal`, the 6.18 Baikal stack on Linux 7.2, and `7.2-ps4`, based on rmux's Strawberry kernel for all PS4 southbridges. |
| [Import manifest](provenance/ps4-import.json) | Per-file hashes, immutable source paths and ordered series. |
| [Active target provenance](provenance/6.18-ps4-baikal.json) | MTS2 import, config adaptations and the active 55-patch stack. |

---

Have console Linux experience, hardware to test, or an idea?
[Join the work](https://github.com/meerzulee/linux-psx/issues).
