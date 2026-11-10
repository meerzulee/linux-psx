# DiagnostiX

DiagnostiX is a PS4 app that tests the console's hardware. It starts a small Linux system from the console's memory, runs a set of checks and saves the results in a report you can read and share. It answers one question: what works on this console?

It's in beta, and it's made for any PS4. Linux has only been tested on a PS4 Slim with a Baikal B1 southbridge, so a report from any other console helps most, even one that says Linux didn't start.

![The DiagnostiX app's home screen](images/diagnostix-app-home.webp)

_Rendered on a development computer. The console uses its own system font._

## Get it

The current version is **0.16 beta1**. Download `diagnostix-0.16-beta1.pkg` and `SHA256SUMS` from the [beta1 release](https://github.com/meerzulee/linux-psx/releases/tag/diagnostix-0.16-beta1), then check the package with `sha256sum -c --ignore-missing SHA256SUMS`.

You'll need:

- A PS4 that can install homebrew packages, with GoldHEN running. DiagnostiX uses GoldHEN to read the southbridge, to reach the USB stick and to start Linux. Preparing a console for homebrew is outside this project.
- Firmware 12.02 to start Linux. On other firmware the app can save the console's info, but can't start Linux.
- A USB stick, FAT32 or exFAT. DiagnostiX only writes to its own `DiagnostiX` folder.
- About 800 MB free on the internal drive: about 400 MB for the app and the same again for the boot files it sets up.
- An HDMI display. 1920×1080 at 60 Hz is the tested mode.
- An Ethernet cable if you want the Ethernet test. It's optional.

## Which consoles

The home screen shows the console's southbridge and firmware, and what this version can do with them.

| Console | What DiagnostiX offers |
| --- | --- |
| PS4 Slim with Baikal B1, firmware 12.02 | **Automatic test** and **Manual test**. This is the tested console. |
| Any other PS4 on firmware 12.02, including a Pro | **Save console info** and **Try Linux anyway**. Linux hasn't been tested on it, so the screen may stay black. |
| Any PS4 on other firmware | **Save console info**. The loader only knows firmware 12.02. |

**Save console info** writes `DiagnostiX/console-info.txt` to the stick, with the southbridge, firmware, processor and memory. Send it with the [report form](https://github.com/meerzulee/linux-psx/issues/new?template=report.yml). It helps us [port Linux to your console](09-help-port-your-console.md).

## Run it

1. Plug in the USB stick, then open DiagnostiX. The home screen shows **USB stick: Found** within a few seconds.
2. Choose **Automatic test**. The first time, it copies its boot files to the internal drive and checks them, then starts Linux. The screen goes dark for about a minute.
3. The tests start by themselves and need nothing from you. The Ethernet test only asks you something if no cable is plugged in.
4. When the run ends, you get one page of results. Choose **Turn off** or **Restart to PS4**.

![A finished run](images/diagnostix-app-finished.webp)

_A run on the project's PS4 Slim with Baikal B1, 2026-11-11, redrawn on a development computer from that run's results._

A run takes about 6 minutes on the tested console. **Manual test** starts the same Linux and lets you choose the tests.

## What it checks

The tests run in order, from the lowest layer up. If a lower check fails, the ones that depend on it are marked **Blocked** instead of failed, so one bad result doesn't look like five.

| Area | What DiagnostiX does |
| --- | --- |
| System | Measures the boot, then checks the processor, memory, internal drive, USB speed and temperature. |
| Graphics | Finds the GPU driver, draws OpenGL and Vulkan reference images and compares the pixels, starts Chromium, and runs a glmark2 3D benchmark. |
| Video decoding | Decodes an H.264 clip on the PS4's video hardware and compares the frames with a reference. |
| Audio | Lists the audio devices. |
| Network | Connects over Ethernet, scans for Wi-Fi networks and looks for Bluetooth. |
| Controller | Looks for a game controller in Linux. |

On the tested console, every check passes or finds its device, except two:

- **Sound** is skipped, because beta1 has no test sound yet.
- **Controller** shows **Not found**.

## Reports

The report is saved on the USB stick under `DiagnostiX/`, in a folder named after the date and the console, as the tests run, so a freeze can't lose it. At the end you can also download `diagnostix-report.zip` over your own network from a QR code. Without a stick, the network download is the only copy.

Reports leave out serial numbers, MAC and IP addresses, network names and console IDs. Nothing is uploaded. [Results and Reports](07-results-and-reports.md) explains what's in one.

## Before you run a test

- **If the console freezes,** hold the power button to turn it off. Open DiagnostiX again and it shows how far the last run got.
- **Linux runs from memory.** It reads the internal drive but doesn't write to it.
- **Turn off and Restart stop Linux in order** and take about 3 seconds. Restart goes back to the PS4 system.
- **Clear old files** on the home screen removes old installers and boot files, after you review the list.

## The USB kit

The older **alpha7** kit boots DiagnostiX from a USB drive with your own loader. It contains no firmware, so it has to be finished on a Linux x86-64 computer with files you supply. The steps are in the [alpha7 setup guide](https://github.com/meerzulee/linux-psx/blob/diagnostix-alpha7-20261107/docs/DIAGNOSTIX-RELEASE.md), and the download is the [alpha7 release](https://github.com/meerzulee/linux-psx/releases/tag/diagnostix-alpha7-20261107). A finished kit hasn't been booted yet.

<!-- manual-navigation -->

---

[← Previous](05-other-distributions.md) · [Contents](README.md) · [Next →](07-results-and-reports.md)
