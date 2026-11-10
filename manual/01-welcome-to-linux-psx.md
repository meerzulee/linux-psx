# Welcome to Linux psX!

Linux psX is the Linux kernel for PlayStation consoles. This is where you check whether your console can run it, download a kernel, test your hardware with DiagnostiX, and help bring Linux to a console we haven't reached yet.

A PS4 is an eight-core AMD x86-64 computer with a Radeon-family GPU, but it isn't a PC. Its southbridge, its boot path and much of its hardware need their own drivers. This project collects that work, keeps track of who wrote it, and records what has actually been seen working.

## Start here

| You want to | Go to |
| --- | --- |
| Find out whether your console can run this | [Is My Console Supported?](02-compatibility.md) |
| Download a kernel | [Kernels](03-kernels.md) |
| Boot a kernel with your own Linux system | [Booting a Kernel](04-booting-a-kernel.md) |
| Run a distribution of your choice | [Other Distributions](05-other-distributions.md) |
| Test your console and get a report | [DiagnostiX](06-diagnostix.md) |
| Get an unsupported console working | [Help Port Your Console](09-help-port-your-console.md) |
| Get graphics drivers for your distribution | [Mesa and Video](08-mesa-and-video.md) |
| Compile it yourself | [Building from Source](10-building-from-source.md) |

**Just want a desktop on your PS4?** Go to [Omarchy psX](https://omarchy.mrz.sh/). It's the distribution we support first.

## Where we are

Linux psX is in **beta**. Everything we list as working has passed a test on a real console.

- The current kernel is **Linux 7.2.11 beta2**, for **PS4 Slim** consoles with a **Baikal** southbridge.
- Nearly every result comes from **one PS4 Slim with a Baikal B1 southbridge on firmware 12.02**. A community member has reported results from a second Baikal Slim.
- Aeolia, Belize and Belize 2 consoles and the PS4 Pro have **no recorded result**. If you own one, [we can port it together](09-help-port-your-console.md).
- A **PS5** kernel candidate builds, but this project hasn't tested it on a console.

## How the pieces fit

**The kernel** is the core of the project. It doesn't depend on any particular distribution, desktop, package manager or init system.

**DiagnostiX** is a PS4 app that starts a small Linux system built on that kernel. It exists to test hardware, and its results feed the compatibility list.

**Mesa** is the graphics driver that runs in userspace. PS4 patches for it live here too, but the kernel doesn't need them.

**[Omarchy psX](https://omarchy.mrz.sh/)** is a separate project: the Omarchy desktop on PlayStation, built on these kernels.

## What isn't included

A Linux psX kernel is one part of a working system. You supply the rest:

- a loader that suits your console and its firmware;
- an initramfs and a root filesystem;
- device firmware, which kernel downloads don't include.

[Booting a Kernel](04-booting-a-kernel.md) goes through each of those. Preparing a console to run homebrew is outside this project.

## What beta means

Everything marked `beta` has been built and tested by us on a real console, and it works in those tests. Expect bugs. When you find one, [tell us](07-results-and-reports.md), and it goes on the [known problems list](02-compatibility.md#known-problems) once we've reproduced it.

<!-- manual-navigation -->

---

[Contents](README.md) · [Next →](02-compatibility.md)
