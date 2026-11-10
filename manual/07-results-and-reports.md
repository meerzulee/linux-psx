# Results and Reports

DiagnostiX gives every check one of a few results. They're deliberately narrow: each one describes a single run of a single check on a single boot.

## What each result means

| Result | What happened | What it tells you |
| --- | --- | --- |
| **PASS** | The check ran and met its pass condition. | It worked, once, on this boot. |
| **FAIL** | The check ran and didn't meet its pass condition. | Worth reporting. |
| **FOUND** | The hardware was detected, and this check only looks for it. | The device is there. Detection alone is never a pass. |
| **NOT FOUND** | The check looked and the device wasn't there. | Missing hardware, a missing driver, or nothing plugged in. |
| **SKIPPED** | The check couldn't run, for example because a test file or cable is missing. | Nothing about the hardware. |
| **BLOCKED** | A check it depends on didn't pass. | Look at the lower check first. |

Earlier boots stay separate from the current one, so an old pass can't hide a new failure.

## What a pass doesn't mean

A pass covers the finite check that ran. A CPU and memory pass doesn't establish that every core is healthy, that the cooling is adequate, or that every memory address is good. A graphics pass doesn't establish GPU stability. None of them say anything about a different console.

DiagnostiX never declares hardware supported. It collects evidence, and support needs repeated tests on real consoles.

## Sending a report

Send your result with the [report form](https://github.com/meerzulee/linux-psx/issues/new?template=report.yml). Nothing is uploaded automatically, and you choose what to share.

The form asks for:

- your console model, southbridge and firmware version;
- the kernel, and what you ran with it;
- what you did, what you expected and what you saw;
- your setup: the loader, video memory size, initramfs and any firmware you added, plus the SHA256 of your download if you have it;
- the relevant part of the log, read through first.

**A boot that failed counts.** Say what was on the screen, where it stopped and how long you waited. A report that a console doesn't boot tells us far more than no report.

## What to keep private

Remove these from anything you post:

- MAC and IP addresses, and Wi-Fi network names;
- serial numbers and console IDs;
- paths from your own machine;
- passwords and keys.

Never upload these at all:

- firmware or PUP files;
- EAP keys or private memory mappings;
- raw, unreviewed UART or packet captures;
- a used USB drive image.

## One file to share

The DiagnostiX app builds a single `diagnostix-report.zip`. It holds a summary, the console details, the benchmark score, a progress log, a size-limited kernel log and the detailed test report. Serial numbers, MAC addresses, IP addresses, network names and console IDs are removed. It still won't post anything for you. You read it, then attach it to an issue yourself.

<!-- manual-navigation -->

---

[← Previous](06-diagnostix.md) · [Contents](README.md) · [Next →](08-mesa-and-video.md)
