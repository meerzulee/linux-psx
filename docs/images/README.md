# Diagnostix screenshots

These images illustrate the PS4 interface. They are not evidence that a new image,
kernel or complete test suite has passed.

## The app, version 0.16

Rendered on a development computer on 2026-11-10 from the interface code in this
repository. They are not console captures, and every result in them is sample data.

| Image | How it was made |
| --- | --- |
| [Home](diagnostix-app-home.png) | `diagnostics/ps4/fpkg/tests/ui_preview.cpp`, 1920x1080. Noto Sans stands in for the console's system font. |
| [Test in progress](diagnostix-app-testing.png) | `diagnostix --demo graphics` in a 120x33 terminal, drawn with the console font and palette. |
| [Finished run](diagnostix-app-finished.png) | `diagnostix --demo finished`, made the same way. |

## The USB kit

Historical console captures of the earlier USB kit.

| Image | Origin and changes |
| --- | --- |
| [Network setup](diagnostix-network.png) | Actual PS4 X11 onboarding capture from the alpha3 graphics session. Privacy-edited with the built-in imagegen tool: discovered Wi-Fi rows and the SSH fingerprint were removed, and IP addresses were replaced with `<console-ip>`. The generated edit also resampled the screenshot; it is not a byte-identical framebuffer capture. |
| [GPU inventory](diagnostix-hardware.png) | Unmodified 1920×1080 PS4 X11 capture from [EXP-20261104-016](../../diagnostics/SESSIONS.md#exp-20261104-016--readable-hardware-overview-on-the-console), 2026-11-04. The hardware modal covers connection identifiers. |

The GPU screenshot was captured through the existing FFmpeg X11 grabber.
It shows the console-rendered framebuffer, not a photograph of HDMI scanout.
Neither image contains a saved password, real network address, nearby SSID,
device serial or private key. Raw captures and private diagnostic logs remain
outside the public assets.

The interface source is covered by [the diagnostic MIT license](../../diagnostics/LICENSE).
The [network-edit prompt](diagnostix-network-edit.txt) records the privacy edit;
it does not change the diagnostic source or its test results.
