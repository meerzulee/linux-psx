# Kernels

Use the newest kernel listed for your console. Each kernel is built for a specific console and southbridge, so first check [whether your console can run it](02-compatibility.md).

| Kernel | Console | Status | Tested | Download |
| --- | --- | --- | --- | --- |
| [**7.2.11 beta2**](#linux-7211-beta2) | PS4 Slim with Baikal | `beta` | 2026-11-10 | [Download](https://github.com/meerzulee/linux-psx/releases/tag/v7.2.11-psx-ps4-baikal-beta2) |

`beta` means it works in our tests, but expect bugs and [report them](07-results-and-reports.md).

## Linux 7.2.11 beta2

- **Kernel release:** `7.2.11-psx-ps4-baikal-uvd-runtime-dev6`
- **Tested:** it passed a full DiagnostiX run on the project's PS4 Slim (Baikal B1, firmware 12.02) on 2026-11-10: OpenGL, Vulkan, browser, Wi-Fi, the 3D benchmark and H.264 decoding.
- **Modules:** 41, shipped as one matched set.
- **Source:** the complete patched source is in the same release.

It can't do these yet:

- Suspend and resume.
- Unload or reset the Ethernet driver once it has started. It keeps its memory until power-off, and that's deliberate.
- Boot into another kernel with kexec.

## What's in a download

A release has two separate parts, so you can test a kernel without pulling a large source archive:

- **The kernel download:** `bzImage`, the whole matching module set, the resolved `config`, identity metadata such as `artifacts.json`, and boot notes.
- **The source download:** the complete patched Linux source, the configuration, the original licenses and this project's build inputs.

No release contains firmware, a loader, an initramfs or a root filesystem.

## A kernel and its modules travel together

The kernel you boot and the modules on your root filesystem have to come from the same build. A matching version number isn't enough, because a separately compiled kernel with the same name can still differ.

Install the entire module archive, never just the one module you think you need. Keep the previous kernel, initramfs, arguments and modules together on another machine, so you always have a set to go back to.

## Verify what you downloaded

From the extracted folder:

```sh
# Linux
sha256sum -c SHA256SUMS

# macOS
shasum -a 256 -c SHA256SUMS
```

`artifacts.json` records the identity of the kernel, its configuration, the module archive and the matching source. It isn't a signature, and hashes only show that the files match what was published.

More detail is in the [artifact contract](../docs/ARTIFACTS.md).

## Video decoding builds

The `uvd-runtime` kernels carry the beta PS4 video decoder driver. It's off unless you turn it on with boot arguments, and it needs matching external firmware and Mesa. The arguments are in the `README.md` inside the kernel download.

The ordinary `native-dev` targets don't include it. See [Mesa and Video](08-mesa-and-video.md).

<!-- manual-navigation -->

---

[← Previous](02-compatibility.md) · [Contents](README.md) · [Next →](04-booting-a-kernel.md)
