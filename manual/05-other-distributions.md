# Other Distributions

A Linux psX kernel doesn't depend on any particular distribution, desktop, package manager or init system. You can run it under the Linux system you prefer, as long as you're prepared to put the pieces together.

## Our recommendation

**[Omarchy psX](https://omarchy.mrz.sh/) is the distribution we support first.** It has the same maintainer as this kernel and is built on it, and it's where desktop problems get fixed first. If you want a working desktop and don't mind which one, start there.

Everything below is for people who would rather use something else.

## What any distribution needs

| Part | What it's for | Where it comes from |
| --- | --- | --- |
| The kernel and its whole module set | Drives the console's hardware | [Kernels](03-kernels.md) |
| An initramfs | Finds your root filesystem and carries early firmware | Your distribution's PS4 build, or your own |
| Device firmware | Display, Wi-Fi and Bluetooth | Obtained separately. See [Booting a Kernel](04-booting-a-kernel.md#firmware) |
| PS4 graphics drivers | OpenGL and Vulkan on this GPU | [Mesa and Video](08-mesa-and-video.md) |
| A root filesystem | The distribution itself | Your distribution |
| A loader | Starts Linux from the PS4 system | Outside this project |

[Booting a Kernel](04-booting-a-kernel.md) goes through how these fit together.

## Using an existing PS4 distribution

The community publishes ready-made PS4 distribution images and install guides, and we don't repeat their steps here. The [PS4 Linux Tutorial](https://dionkill.github.io/ps4-linux-tutorial/) by DionKill is the most complete guide we know of, and it's the place to start if you want an image that somebody else has already put together.

To try one with a Linux psX kernel, replace its kernel and modules as one matched set, and keep the original set so you can go back. Most images bring their own Mesa. On the [second Baikal Slim report](https://github.com/meerzulee/linux-psx/issues/2), a Mesa 25.1 development build from the psxitarch distribution ran accelerated OpenGL and Vulkan.

We haven't tested other distributions ourselves, so a result from yours is useful either way.

## Making your own

Install the distribution in a virtual machine or on a USB drive, then add the parts from the table above.

Mesa is the part that takes the most work, because a compiled Mesa is tied to the distribution it was built on. Build yours from the [PS4 patches](08-mesa-and-video.md#why-mesa-is-built-per-distribution). On Arch-based systems, [ps4-video-archlinux](https://github.com/DionKill/ps4-video-archlinux) is a community package repository you can use instead. We haven't tested those packages.

## What to expect on any distribution

- **Linux doesn't replace the PS4 system.** You start Linux from the PS4 system each time, and a reboot goes back to it.
- **1920×1080 at 60 Hz is the tested display mode.** Other modes need their own tests.
- **Suspend and resume aren't available.**
- **Use a USB drive.** It keeps Linux apart from your PS4 data. Writing to the internal drive hasn't been tested with these kernels yet.

## Tell us how it went

If you run a Linux psX kernel on another distribution, [send a report](07-results-and-reports.md), including one that says it didn't work. Name the distribution and its Mesa version, and the exact kernel release.

<!-- manual-navigation -->

---

[← Previous](04-booting-a-kernel.md) · [Contents](README.md) · [Next →](06-diagnostix.md)
