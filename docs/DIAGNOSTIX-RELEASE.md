# Diagnostix experimental release

## The app is not released yet

The installable app (0.16) is separate from the kit described below. Local candidate
`ram-alpha1-0.16-nofw2` was built on 2026-11-10 for this review.

| Requirement | State |
| --- | --- |
| No owner SSH keys | Done. Built without `--owner-ssh-key`. A scan for the owner's public keys finds none in the initramfs, and the same scan finds one in the private dev build. |
| No background contact with Google | Built. Chromium provider `psx4` blocks every name except the local lab, and Browser passed with it. **Open:** a network capture to prove nothing reaches Google. |
| Reports leave out identifiers | Done in code and host tests. Serial numbers, MAC and IP addresses, network names and console IDs are removed. |
| Automatic run on this exact build | Done once, on one Baikal B1 console (EXP-20261110-011). |
| No Sony firmware in the package | Done. The package build fails if any is found, and Automatic passed without it (EXP-20261110-011). |
| Git holds no firmware | Done. The 10 MediaTek Wi-Fi files are fetched from pinned public sources at build time. |
| Complete corresponding source | **Open.** The kernel, BusyBox, glmark2, vkmark and Chromium inputs need their source published with the binary. |
| Legal notice | **Open.** The manual's notice (`manual/10-legal-notice.md`, not in Git yet) says Linux psX doesn't redistribute firmware. Change it when a package that includes firmware is published. |

Firmware policy (owner decisions, 2026-11-10): the package carries no Sony
firmware, and Git holds no firmware. Each console supplies its own GPU and UVD
files through the loader at boot.

`repack-ps4-diagnostix` leaves out the 8 GPU files, the UVD and VCE files and the
two Marvell files. `trim-ps4-diagnostix-root` removes the same files from the root
image and writes only the blocks still in use, so deleted bytes do not remain.
`diagnostics/ps4/fpkg/build.py` then runs `scripts/check-ps4-package-firmware`,
which fails on a forbidden name or on matching bytes in early boot, in the tools
archive, or anywhere in the embedded filesystem images. Its manifest holds hashes
only. VCE is removed outright, because nothing loads it.

The loader reads a 313,912-byte UVD range from the running 12.02 kernel, checks its
CRC32, adds the standard AMD firmware header, and leaves the file out on any
mismatch. The result is `amdgpu/liverpool_uvd.bin` (314,168 bytes, SHA256
`62a43ef4814f59fdd6772792e0a9303e016fe2e17ad30b36412d1fa7ce4cd5cb`). The boot
script exposes that file at the root path Video decoding reads. If the loader
left it out, the boot continues and Video decoding reports the problem. The stock
232,752-byte `liverpool_uvd.bin` cannot replace it, because the driver rejects
that file and GPU initialization does not complete (EXP-20261105-004).

With the package's early-boot copies removed, the loader's UVD file passed Video
decoding (EXP-20261110-008), and its 8 GPU files passed OpenGL, Vulkan, the 3D
benchmark and Video decoding (EXP-20261110-009), each once on one Baikal B1
console. With the root-image copies also removed, the same tests passed once more
(EXP-20261110-010), and Browser passed once its Chromium image was built for this root
(EXP-20261110-011).

The package build starts from a frozen local base image that still holds these
files, and removes them. The older full-image builder that made that base needs
the historical seed from `linux-ps4`, which went private on 2026-11-10, so that
builder only runs with an existing local copy. Rebuilding that lineage with no seed would
change every pinned hash after it, so it needs its own hardware qualification.

The 10 MediaTek Wi-Fi files stay in the package (owner decision, 2026-11-10).
`scripts/fetch-ps4-firmware` fetches them from their pinned public repositories at
build time, and the app downloads nothing on the console.

A published package redistributes the files inside it even though Git holds none
of them. The manifest records a license for only one of them,
`mediatek/mt7668pr2h.bin`, so publishing needs the owner's explicit approval.

## The USB kit

The public release consists of a **kernel/source kit and a firmware-free image
template**. The template is not bootable and is not a disk image to flash. A
host finalizer adds the user's local, checksum-matched firmware and creates a
new private `boot-set/`. The owner's firmware-bearing image, USB state and
credentials are not public downloads.

Keep the exact `bzImage`, resolved configuration and **all matching modules**
together. The complete corresponding Linux source archive belongs with the
kernel kit; match its release and hashes to the supplied receipts. The
template also includes a separately built static BusyBox, its upstream source,
configuration, musl source, exported headers, licenses and rebuild instructions.
These source materials are separate from the 1 GiB core boot-set budget.

## Make a local boot set

Use the matching Linux psX source revision on a native Linux x86-64 host, with
Python 3, `fakeroot`, `e2fsprogs` and the repository's documented build tools.
Extract the public template archive into an ordinary directory. Obtain the
files listed in `FIRMWARE-REQUIRED.json` locally under their applicable terms,
preserving its relative paths. The finalizer accepts only the listed byte
sizes and SHA256 values; it does not download firmware.

From the repository root, inspect the plan, then create a new output directory:

```sh
python3 scripts/prepare-ps4-diagnostics-public finalize \
  --template /path/to/diagnostix-template \
  --firmware-dir /path/to/local-firmware \
  --output /path/to/new-local-image

python3 scripts/prepare-ps4-diagnostics-public finalize \
  --template /path/to/diagnostix-template \
  --firmware-dir /path/to/local-firmware \
  --output /path/to/new-local-image --apply
```

Finalization builds a fresh filesystem, restores the firmware in both the root
and initramfs, and regenerates the root, embedded and external contracts and
checksums. It preserves the kernel and module bytes. The canonical video's
private build paths have been replaced with portable metadata; clip and pixel
oracle bytes remain unchanged, and the installed diagnostic adapter records
the new fixture checksum. This derivative has its own hashes and does not
inherit the owner's image acceptance.

## Copy to USB and boot

Use an already mounted FAT32 USB on the host. Substitute that device's actual
whole-disk by-id path, serial, byte capacity and mountpoint; do not copy another
owner's identifiers. The current stager checks these identities, renders the
USB UUID into `bootargs.txt`, preserves owned rollback files and reads back the
written files. It does not format or repartition.

```sh
sudo python3 scripts/stage-ps4-diagnostics-usb \
  --source /path/to/new-local-image/boot-set \
  --device /dev/disk/by-id/usb-YOUR_DEVICE \
  --expect-serial YOUR_SERIAL --expect-bytes YOUR_CAPACITY_BYTES \
  --mountpoint /path/to/mounted-usb
```

Run the same command with `--apply --receipt /path/to/private-usb-receipt.json`
to copy the verified set. Keep the receipt private. Safely eject the USB, then
use a loader compatible with the console, southbridge, firmware and boot
layout. The recorded Baikal setup uses a particular **v25 loader** and 1 GiB
VRAM configuration; that label is not a compatibility promise for other PS4
models or firmware. A PC UEFI boot menu cannot launch this appliance. See
[boot requirements](BOOTING.md) and retain a known-working rollback.

The template cannot be passed directly to the USB stager. Do not write the
ext4 root file to a raw device or mix files from different release sets.

## Network, SSH and Power

Choose Ethernet or Wi-Fi from Network. No shared SSH key or password is
shipped. Enroll public keys through the on-console GitHub fingerprint preview
and confirmation, or explicitly open the temporary password window and run
`ssh-copy-id root@CONSOLE_ADDRESS`. Verify the displayed host fingerprint.
Enrollment and the unique host identity persist on that USB; see
[SSH setup](PS4-DIAGNOSTIC-SSH-ACCESS.md).

The alpha7 Power implementation is an explicit terminal action: selecting
and confirming immediate Shutdown or Reboot may terminate active tests and
applications, discarding unsaved results and data. It does not wait for an
application to approve stopping. This is opt-in, never an automatic response
to a test failure. The kernel retains UVD backing for the terminal transition;
it does not reset, unload or recover the decoder in place. Reboot targets
Orbis, not a direct Linux-to-Linux handoff.

## Status and redistribution boundary

Runtime-dev5/alpha6 has recorded owner-observed ordinary reboot and shutdown,
including the rescue-shell fix that removed the 90-second userspace delay.
The new runtime-dev6/alpha7 preemption/faulted-restart extension is a separate
candidate; host tests and packaging are not hardware acceptance. The public
template's new BusyBox, sanitized filesystem and locally finalized image also
need their own boot observations. Consult the [session ledger](../diagnostics/SESSIONS.md)
for exact artifact scope. Other consoles, endurance, suspend and broad hardware
stability remain unqualified.

The previous ready-to-boot owner image cannot simply be uploaded: it contains
private external Liverpool UVD firmware, other inherited GPU/Marvell firmware
without a completed redistribution audit, and nine vendor Wi-Fi files for
which [the manifest](../manifests/ps4-firmware.json) records no redistribution
license. That manifest separately identifies the Linux-firmware MediaTek
Bluetooth file's `LICENCE.mediatek` basis; the template nevertheless excludes
all firmware. A historical public download is not a new license grant.

The template removes the owner's key/fingerprint, machine identity and saved
state, and rebuilds ext4 from clean file contents so deleted blocks cannot
carry the old bytes. It replaces the historical seed's BusyBox with the
source-pinned build and retains source/license notices. Public kernel, package,
font and theme notices still apply; firmware supplied during local finalization
does not acquire redistribution permission. Keep the finalized image and all
USB state, raw logs and credentials private. Report results with artifact
hashes and reviewed, redacted evidence rather than uploading a used USB image.
