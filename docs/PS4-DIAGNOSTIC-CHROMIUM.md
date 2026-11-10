# Optional Chromium comparison provider

This optional provider's source and image builder are owned by Linux psX.
See the [standalone diagnostic guide](DIAGNOSTICS.md) for source ownership and
public-input acquisition. Historical output paths below are explicit data
inputs, not a dependency on another project's source checkout.

Chromium 151.0.7922.169-1 is available in the existing pinned 2026-10-19
development snapshot. It can run the identical local browser laboratory as
Firefox, with its own profile and separately identified reports. No Chromium
console launch or sandbox acceptance follows from this host preparation.

The DiagnostiX RAM image no longer uses this as an optional USB provider: it
ships a trimmed build inside its boot set, in place of Firefox (owner decision,
2026-11-10). The `manifests/ps4-chromium-provider.json` recipe now describes that
build, which keeps the browser, sandbox helper, en-US locale, resources and
licenses. The figures below record the original USB provider.

## Size and exact inputs

The measured 21-asset glamor boot set is 1,065,892,220 bytes, leaving 7,849,604
bytes below the 1 GiB core cap. Its fixed 1000 MiB ext4 root has only 22,831 KiB
free. The forthcoming Wayland archive/launcher adds 758,467 bytes; allowing
another 1 MiB metadata reserve leaves 6,042,561 bytes. Recompute this budget
for later frontend revisions.

Chromium's additional declared package closure is **142,211,619 bytes
compressed / 444,344,541 bytes installed**, so it cannot fit beside Firefox in
the current core image. The optional provider is separately stored test
software. A complete installation including it exceeds 1 GiB; the core image
and its cap remain unchanged.

| Package | Pinned version |
| --- | --- |
| chromium | 151.0.7922.169-1 |
| libxslt | 1.1.45-2 |
| minizip | 1:1.3.2-3 |
| openh264 | 2.6.0-2 |
| ttf-liberation | 2.1.5-2 |
| xdg-utils | 1.2.1-2 |

Full archive sizes and SHA256 pins are in
[`ps4-chromium-provider.json`](../manifests/ps4-chromium-provider.json).
The builder verifies the snapshot checksum-list pin and every selected archive.
It omits ChromeDriver and locale packs other than en-US; browser resources,
sandbox helper, software fallback libraries and all selected package licenses
remain. The measured regular-file payload is **372,504,789 bytes**.

The current kernel supports ext4/loop but lacks SquashFS. The provider therefore
uses a **402,653,184-byte (384 MiB) ext4 file**, mounted separately read-only.
It is not inflated into RAM and does not replace the 1000 MiB root image.
The browser profile and bounded 16 MiB disk cache use the existing writable
RAM state; process memory remains separate from that cache bound.

## Host preparation

Native tools: Python, fakeroot, zstd, mke2fs/e2fsck/debugfs. No container, package
installation, loop attachment, mount or browser execution is performed.
From the repository root, supply existing host inputs:

```sh
python3 scripts/prepare-ps4-chromium-provider \
  --snapshot output/usb-image-build/snapshot-20261019 \
  --base-root /path/to/verified/staged/rootfs \
  --mesa-root /path/to/pinned/mesa/stage \
  --output output/ps4-chromium-provider-new
```

Defaults identify the frozen console1 root image and glamor1 initramfs;
`--base-image` and `--frontend` accept explicit copies with those exact pins.
Outputs must be a new directory below ignored `output/`. fakeroot preserves
package ownership/modes in the image without installing files on the host.

The prepared local candidate is `output/ps4-chromium-provider-v1/`:

- `chromium-151.0.7922.169-psx1.ext4` — SHA256
  `8fbff1558ca522b4834e022449f4ea33d08738b90b7187eacaa794fb9bfe15a4`.
- `provider-manifest.json` — image identity, payload size, closure count and
  `hardware_acceptance=false`.
- `chromium-provider.json` — root-selected launcher configuration; expected
  embedded `PROVIDER.json` SHA256
  `e38814eb6cd59ff4e04cb7ea67059fba8cdab232e866d82127e0482c7172d0e8`.
- `dependency-closure.json`, `filesystem-check.txt`, `sandbox-inode.txt` —
  124 recursively resolved ELF objects, read-only filesystem validation and
  preserved sandbox-helper uid 0 / mode 4755. Each base ELF was compared with
  its bytes in the frozen root image; frontend/private-Mesa libraries are
  recorded separately. Optional Qt integration is not part of this GTK/X11
  dependency check.

## Fixed launcher handoff

The new
[`launch-chromium-provider`](../rootfs/ps4-diagnostics-desktop/launch-chromium-provider)
accepts **no arguments** and executes only as `psx` uid 1000 on the selected
diagnostic kernel. Parent-owned controller/graphics-session integration must
select this fixed action through the existing unprivileged window-app path.
The provider builder and launcher do not mount or stage anything themselves.

For an explicitly scoped deployment, the operator's root staging step must
verify the complete image hash, store it as optional USB data at
`psx-diagnostics/providers/chromium-151.0.7922.169-psx1.ext4`, and establish a
separate `/run/psx-chromium` mount with **ext4, ro, nosuid, nodev, execution
allowed**, and 0755 directory traversal. The diagnostic FAT mount remains
noexec and root-only; executing the browser directly from FAT is not supported.
Install the generated configuration as root-owned 0644
`/etc/psx-diagnostics/chromium-provider.json` in the existing RAM-backed `/etc`.

On the RAM image the launcher runs as psx and cannot read the root-private
`boot-set.json`. The root prepare step checks the Mesa identity there and passes
it on as `mesa_manifest_sha256` in the 0644 configuration, which the launcher
compares with the provider. Without that field (USB flow) it reads the boot set.

The launcher checks that mount, root-selected manifest and executable hashes,
kernel/Mesa identities, namespace limits and the existing private psx cookie
at `/run/psx-graphics-user/Xauthority`. It uses `DISPLAY=:0`,
`/run/user/1000`, and a separate profile under
`/home/psx/.local/share/psx-diagnostics/chromium`. It calls the actual provider
binary rather than the distribution wrapper that reads user flag files.

The initial comparison explicitly requests:

```text
--ozone-platform=x11 --use-gl=angle --use-angle=gl
--disable-features=Vulkan,VulkanFromANGLE
```

No GPU blocklist override, forced unsafe software renderer, headless mode,
remote-debugging port or sandbox-disabling flags are added. Background network
and component-update flags limit unrelated activity. They did not stop Chromium
registering for Google's messaging service on the console (EXP-20261110-006), so
provider `psx3` also passes `--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE
127.0.0.1`. Every name now fails to resolve except the local lab address, whatever
Chromium tries to reach. A host run of Chromium 155 with this rule loaded the lab
and made no DNS lookup or connection. It has not run on a console yet. This OpenGL/ANGLE path is a recorded browser-backend difference from
Firefox and does not resume the separate Vulkan presentation investigation.
Do not rely on an unverified `--disable-vulkan` switch.

The namespace and seccomp sandbox must stay enabled. The nosuid provider mount
deliberately cannot supply a privileged setuid fallback, despite retaining the
original helper. Kernel config and sysctl availability are prerequisites, not
proof of actual sandbox operation. Confirm the real browser's sandbox/process
state before accepting its run; sandbox failure is a stop, never a reason to
add `--no-sandbox` or disable a sandbox layer.

The launcher records browser binary/provider identity and exact arguments in
`/run/user/1000/psx-chromium-launch.json`. Open the same
`http://127.0.0.1:8080/lab`; opening it does not start tests. Use the same explicit
suite and fixtures, resolution, nominal refresh, presentation mode and evidence
policy as Firefox. Keep browser family/version, actual renderer, software
fallback and results distinct. rAF pacing is an observation of that browser's
presentation, not a replacement for the measured RandR display mode.

## Primary source basis

- Chromium 151 GL/ANGLE switches:
  https://chromium.googlesource.com/chromium/src/+/151.0.7922.169/ui/gl/gl_switches.cc
- Chromium 151 Vulkan feature selection:
  https://chromium.googlesource.com/chromium/src/+/151.0.7922.169/gpu/config/gpu_finch_features.cc
- Chromium Linux sandbox design and runtime status:
  https://chromium.googlesource.com/chromium/src/+/main/sandbox/linux/README.md
- Effective command-line inspection:
  https://www.chromium.org/developers/how-tos/run-chromium-with-flags/

The selected package binaries are local development inputs, verified against
the pinned snapshot. They are not a new public browser release or a claim of
current security maintenance. No console action, USB write or publication was
performed during provider preparation.
