# Mesa and Video

Mesa is the userspace part of the graphics stack: the OpenGL and Vulkan drivers that applications use. It isn't a kernel dependency, so you can boot a Linux psX kernel without touching it, but what you see on screen depends on it.

## Do I need the PS4 patches?

Yes, unless your distribution's Mesa already has PS4 support. Stock Mesa treats the PS4's Liverpool GPU as a Kaveri and sets up two render backends where the hardware reports four. After that was corrected, our reference images and text came out clean.

Other PS4 projects carry their own Mesa. On the [second Baikal Slim report](https://github.com/meerzulee/linux-psx/issues/2), a Mesa 25.1 development build from the psxitarch distribution ran accelerated OpenGL 4.6 and Vulkan 1.3.

The patches are in [`userspace/mesa`](../userspace/mesa/README.md), with their own build script and tests. The current candidate is `ps4-mesa-26.2.5-fill-bounds-htile-dev5`, four patches on top of Mesa 26.2.5. It's in beta.

## Why Mesa is built per distribution

The patches work anywhere, but a compiled Mesa is tied to the distribution it was built on, because it links against that system's libraries. A build for one distribution generally won't run on another.

So build your own from the public patches:

```sh
python3 userspace/mesa/build-candidate.py --help
```

Omarchy psX builds its own Arch packages from these patches.

## What's been tested

Results here are from the project's console:

- OpenGL and Vulkan reference images matched, and presentation tests finished with a responsive desktop afterwards.

The test-by-test record is in [Mesa testing](../docs/MESA-TESTING.md).

## Hardware video decoding

The PS4's UVD video decoder has a beta Linux driver, and it's what the `uvd-runtime` kernels carry. It's off unless you enable it, and it needs matching external firmware and the PS4 Mesa candidate.

| What | Result | Notes |
| --- | --- | --- |
| H.264, 1080p at 30 frames per second | `beta` | A 900-frame clip matched a software reference on every frame. |
| Decoding several streams at once | `unavailable` | One stream at a time. |
| Output format | `beta` | 8-bit NV12. |
| Decoding in Firefox | `beta` | One clip used the hardware decoder for all 900 frames. |
| Seeking during playback | `untested` | No controlled test yet. |
| Other codecs | `untested` | |
| Video encoding | `untested` | |
| Recovering the decoder after a fault | `unavailable` | It needs a power cycle. |

More detail, including the source and build lineage, is in the [UVD notes](../docs/UVD.md).

<!-- manual-navigation -->

---

[← Previous](07-results-and-reports.md) · [Contents](README.md) · [Next →](09-help-port-your-console.md)
