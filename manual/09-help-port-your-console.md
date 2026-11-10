# Help Port Your Console

Linux psX runs on a Baikal southbridge because that's the console the maintainer has. Aeolia, Belize, Belize 2 and the PS4 Pro need somebody who owns one. If that's you, we can work on it together: you run the tests on your console, and the maintainer builds and fixes the kernel.

You don't have to be a kernel developer. You do need to be comfortable following exact steps and copying text out of a terminal.

## What we need from you

- **The console**, and time for a series of short tests on it.
- **Its details:** the model number, southbridge and system software version. [Is My Console Supported?](02-compatibility.md) shows how to find them.
- **A way to boot Linux on it.** Preparing a console to run homebrew is outside this project, so this part has to be working already.
- **A USB drive and a USB keyboard.**
- **A serial (UART) adapter, if you can connect one.** It's optional at the start. When a console hangs before the screen comes on, it's the only way to see why, so a port goes much faster with one.

## How to start

1. Find your model and southbridge.
2. If any Linux already runs on your console, even with problems, save its device list and kernel log:

   ```sh
   lspci -nn > lspci.txt
   dmesg > dmesg.txt
   ```

   Look through them and remove anything personal, such as serial numbers and MAC addresses.
3. Fill in the [report form](https://github.com/meerzulee/linux-psx/issues/new?template=report.yml) and choose **My console isn't supported yet and I'd like to help port it**. Title it with your model and southbridge, for example `Report: CUH-1215A, Belize`. Attach the two files, and say which kernel produced them and what does and doesn't work.

The maintainer will reply with what to try first.

## How a port goes

A port is a series of short rounds:

1. The maintainer builds a test kernel for your console.
2. You boot it once and report what you saw, with the logs.
3. We change one thing and try again.

Changing one thing at a time keeps each result useful, and a failed boot is as helpful as a working one. Results go on the [compatibility list](02-compatibility.md) only after a recorded test on your console, and the full rules are in [Contributing](11-contributing.md#the-rules-for-hardware-testing).

## What the kernel already has

Earlier community work added code for Aeolia and Belize, and the `7.2-ps4` target builds with it. This project hasn't run that build on a console. Your console may need very little work or quite a lot, and only a test can tell us which.

The PS4 Pro has a different GPU, called Gladius, so it needs graphics work as well as southbridge work.

## Before you start

- **Test kernels are experimental.** They can hang or fail to boot, and the way out of a hang is holding the power button. Running one is at your own risk.
- **Keep a way back.** Keep your own backups, and keep a set of files that you know boots.
- **There's no timeline.** A port depends on the maintainer's time, and on what the hardware does when we try.
- **You'll be credited.** Tell us the name you'd like on your results, or say if you'd rather stay anonymous.

<!-- manual-navigation -->

---

[← Previous](08-mesa-and-video.md) · [Contents](README.md) · [Next →](10-building-from-source.md)
