# Legal Notice

Linux psX is an independent community project. It isn't affiliated with or endorsed by Sony Interactive Entertainment or the upstream Omarchy project. Console and project names identify the hardware and software discussed in this manual.

## Scope of this manual

The manual covers Linux on a console that can already load a Linux payload: kernels, diagnostics, hardware observations and building from source. Preparing a console to run homebrew is outside its scope.

## Licenses and attribution

| Component | License and source record |
| --- | --- |
| Linux psX kernel work | [GPLv2](../LICENSE), with existing per-file notices preserved. |
| DiagnostiX tools | [MIT License](../diagnostics/LICENSE). Packages, fonts and themes keep their own notices. |
| PS4 Mesa patches | Per-file licenses. See [`userspace/mesa`](../userspace/mesa/README.md). |
| This manual's text | Part of the Linux psX repository, whose root license is [GPLv2](../LICENSE). |
| This website's code | [MIT License](https://linux.mrz.sh/LICENSE.txt), adapted from the Omarchy psX manual site. |
| Console and southbridge facts | Restated from community sources: the [PS4 Developer wiki](https://www.psdevwiki.com/ps4/Southbridge) (GNU Free Documentation License 1.2) and the [PS4 Linux Tutorial](https://github.com/DionKill/ps4-linux-tutorial) by Diego Pireddu (MIT License). |
| Website wordmark | Rendered with Omarchy's `omarchy-ascii` in Delta Corps Priest 1, a FIGlet font credited to CoSMiC cHiLD. The [upstream MIT notice](https://linux.mrz.sh/assets/LICENSE-omarchy.txt) is retained. |

The patches begin with work by many people, including the Linux kernel contributors, fail0verflow and the PS4 Linux community. The [credits](../CREDITS.md) record the source of each part. Patch author fields, sign-offs and copyright notices remain authoritative for individual changes.

Public kernel binaries are accompanied by complete corresponding source and the required notices. See the [artifact contract](../docs/ARTIFACTS.md).

## Firmware

Linux psX doesn't host or redistribute device firmware, and its kernels embed none. Some drivers need firmware supplied separately. A public download location doesn't by itself establish permission to redistribute a file, so check the license of each file before sharing anything you've assembled.

## Development status

This software is provided under its licenses' warranty terms. Running it on a console is at your own risk. Hardware observations apply to the recorded console and exact build, so keep your own backups and a complete way back before testing a different build.

Questions about attribution or included material can go to the [maintainer](https://github.com/meerzulee) or the [issue tracker](https://github.com/meerzulee/linux-psx/issues).

<!-- manual-navigation -->

---

[← Previous](11-contributing.md) · [Contents](README.md)
