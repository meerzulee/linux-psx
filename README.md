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
| **PlayStation 4** | Existing PS4 port; integration into psX is planned. |
| **PlayStation 5** | Planned port and integration of community kernel work. |
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

**Project setup and documentation.** This repository is being prepared for the
existing [PS4 kernel work](https://github.com/meerzulee/linux-ps4) and the planned
PS5 port. Source integration, build instructions and releases will follow.

Community PS5 kernel patches and configurations are available at
[ps5-linux/ps5-linux-patches](https://github.com/ps5-linux/ps5-linux-patches).
Upstream authorship, licenses and patch provenance will be preserved as work is
integrated.

---

Have console Linux experience, hardware to test, or an idea?
[Join the work](https://github.com/meerzulee/linux-psx/issues).
