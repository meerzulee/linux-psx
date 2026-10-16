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

Linux psX will bring kernel patches, configurations and build tooling into one
project. Each console will keep its own kernel targets and hardware validation,
while sharing the tools and knowledge that make sense across generations.

## Console roadmap

| Console | Project status |
| --- | --- |
| **PlayStation 4** | Linux 6.18.44 patch snapshot imported; existing Baikal evidence remains scoped to its recorded hardware. |
| **PlayStation 5** | Community sources researched and pinned; kernel integration is planned. |
| **Future PlayStation consoles** | Long-term exploration as hardware access and public research become available. |

## What we're building

- Console-specific kernel patches and configurations.
- Reproducible build tooling with pinned upstream Linux sources.
- Matching kernel and module artifacts for each target.
- Clear hardware test records and compatibility documentation.

PS4 and PS5 targets can use different Linux versions and patch sets. Sharing a
repository will let us share tooling while qualifying each console separately.

## The psX projects

| Project | Role |
| --- | --- |
| **Linux psX** | The kernel and console hardware foundation. |
| **[Omarchy psX](https://github.com/meerzulee/omarchy-psx)** | The Omarchy distribution built around Linux psX kernels. |

## Current stage

**Patch foundation.** The PS4 Linux 6.18.44 patches, configs and target metadata
are imported from a pinned revision of the existing PS4 project, with original
headers and bytes preserved. The normal series has 54 active patches; a
separate experimental ultrawide series adds one delta.

| Read next | Contents |
| --- | --- |
| [PS4 patch guide](docs/PS4-PATCHES.md) | Scope, ordering, base pin and checksum verification. |
| [Credits and sources](CREDITS.md) | Original contributors, exact source trees and recovered upstream links. |
| [Universal-kernel research](docs/UNIVERSAL-KERNEL.md) | Shared-source feasibility and the work needed for one PS4/PS5 binary. |
| [Import manifest](provenance/ps4-import.json) | Per-file hashes, immutable source paths and ordered series. |

Build tooling and releases are not yet migrated. This source import and its
checks do not establish new hardware support. PS5 and future-console support
remain planned.

---

Have console Linux experience, hardware to test, or an idea?
[Join the work](https://github.com/meerzulee/linux-psx/issues).
