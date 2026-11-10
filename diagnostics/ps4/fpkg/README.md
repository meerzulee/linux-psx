# Native Diagnostix launcher

This optional OpenOrbis application packages one pinned experimental Baikal
RAM appliance. It is independent of the ordinary kernel build and has its own
title ID `DGNX00001` (version `0.16`).

Version 0.16 uses its own native directory
`/data/diagnostix/versions/0.16/boot`. Preparation explicitly creates only the
owned `/data/diagnostix`, `versions` and `0.16` directories, checking each
parent's regular-directory and owner marker before proceeding. It refuses
symlinks, foreign owners and nonempty unowned directories. Read-only startup
never creates parents. Existing `/data/diagnostix/boot`, sibling versions and
Omarchy's boot directory are preserved.

The bundled loader stages kernel and initramfs in wired, scattered physical
pages and validates a bounded relocation plan before arming. The complete boot
files include `vram.txt`, whose supported value is exactly 1024 MiB. The loader
checks that versioned file itself. Staging and validation alone do not establish
Linux boot acceptance; the actual terminal copy remains a separate hardware
gate. Original upstream and adaptation BSD notices accompany the loader.

Version 0.10 fixed the GoldHEN loader transport. One blocking connection writes
until every loader byte is sent, retrying interrupted calls and continuing after
short writes. A zero or error return fails the handoff without reconnecting or
retrying. The previous single nonblocking `send()` could deliver a truncated ELF.
That complete-write transport is unchanged through 0.16.

The interface uses a centered 1080-pixel column, rounded dark cards and a teal
focus color instead of full-width menu rows. Cleanup pages retain enough width
for exact filenames. **Prepare Diagnostix** is the existing Install / Verify
transaction. D-pad and left-stick up/down both navigate. Stick movement is
edge-triggered with a dead zone and recenter hysteresis; held input, drift and
initial reconnect input do not repeat or confirm an action. Cross selects and
Circle goes back.

The app starts read-only. It checks the small manifest pin and fixed file
sizes first. A matching installed candidate is hashed once per app launch,
then its six regular-file identities are cached. A different manifest skips
reading the old RAM image. Selecting **Prepare Diagnostix** runs immediately
and reuses an unchanged verified snapshot without another payload read.

A fresh or changed candidate is copied and hashed together in one 64 KiB
streaming pass into `/data/diagnostix/versions/0.16/boot.new`. Every size and SHA-256 must
match the compiled pins; write, flush and close errors stop activation.
The app binds the completed stage's directory inode and six member metadata
snapshots to the activated generation, without rereading the image. It relies
on successful writes/fsync and those identities for this app launch. No
separate source preverification, stage readback or post-rename hash is run.

Version 0.06 converts the native 120-byte Orbis/FreeBSD 9 stat ABI into the
SDK's 128-byte `struct stat`, using an aligned, zeroed 256-byte syscall buffer.
Native mode/link fields are 16 bits at offsets 8/10; native size is at 72.
The pinned SDK reads mode at 8, links at 12 and size at 80, which previously
mixed link count into mode and read allocated blocks as file size. Explicit
fixed-width decoding restores all identities, sizes and timestamps before
any file guard uses them. The pinned SDK is unchanged. Serialized native-byte
ASan/UBSan tests reproduce the observed 0.05 failure and verify the conversion,
short-buffer refusal and failed-syscall handling; Linux host fstat tests alone
do not test this ABI. Layout references: [FreeBSD 9 stat](https://github.com/freebsd/freebsd-src/blob/release/9.0.0/sys/sys/stat.h)
and [type widths](https://github.com/freebsd/freebsd-src/blob/release/9.0.0/sys/sys/_types.h).

Source preflight accepts regular read-only PFS files regardless of their
reported link count; compiled hashes and sizes still apply. Writable stage,
installed and cleanup identities retain their unique-link requirement.
Explicit Install and Boot actions record bounded metadata in the private
`/data/diagnostix/versions/0.16/launcher-status.log` only when the namespace already has its
valid owner marker. The 8 KiB log records action entry, phases, source stat
fields, directory-enumeration EOF errno, and precise failure reasons. Aliased
or unknown existing log files are preserved. Startup and cleanup preview stay
read-only. Failures display their stage and reason on the menu.

Activation renames the complete generation into `boot` and retains the previous
generation as `previous-<manifest SHA-256>`. An interrupted transaction blocks
Boot and can be recovered only by selecting **Prepare Diagnostix** again. Recovery
of an already activated candidate hashes it once when this app has no valid
snapshot. Unknown files, aliases and conflicting previous generations are
refused and preserved. No firmware or generated bundle belongs in this source
directory.

Selecting the enabled **Boot Diagnostix** row launches directly, with no
second confirmation. It checks that this launch's verified snapshot is unchanged,
then reads the small pinned loader into its send buffer and makes one handoff
attempt to GoldHEN's local `127.0.0.1:9090` service. The complete-write loop may
require multiple `send()` calls on that same connection. It does not hash the RAM
image again. Changed metadata blocks Boot and requires an explicit **Prepare
Diagnostix** action. A new app launch establishes a new snapshot with one
installed-file hash pass. A failed connection or incomplete write has no retry
in that app session. The dedicated loader owns RAM allocation and the later Linux
handoff.

Version 0.07 names three preparation steps: manifest/current-snapshot checks,
combined copy/verification/flush, and activation. Overall workflow progress
weights the named steps; it is not a disk-throughput measurement. A fresh
install makes one full image pass; Prepare and Boot reuse its unchanged
snapshot. Failures retain the journal and never display completion or send
the loader.

**Clear old files** remains a separate optional action. Its read-only preview lists each name,
category and size; every preview page must be reviewed before a separate
confirmation, which defaults to Cancel. It considers only the fixed cached
installer names `diagnostix-ram-alpha1-0.01.pkg`,
`diagnostix-ram-alpha1-0.02.pkg`, `diagnostix-ram-alpha1-0.03.pkg`,
`diagnostix-ram-alpha1-0.04.pkg`, `diagnostix-ram-alpha1-0.05.pkg`,
`diagnostix-ram-alpha1-0.06.pkg`, `diagnostix-ram-alpha1-0.07.pkg`,
`diagnostix-ram-alpha1-0.08.pkg`, `diagnostix-ram-alpha1-0.09.pkg` and
`diagnostix-ram-alpha1-0.10.pkg` in native `/data/pkg` (FTP `/user/data/pkg`).
Each must be a regular, non-aliased PKG with the Diagnostix ContentID in its
header and the known size: 460128256 bytes for 0.01/0.02, 460193792 for 0.03
through 0.07, or 460390400 for 0.08 through 0.10. It does not hash those large
cached installers again. The 0.11, 0.12, 0.13, 0.14 and 0.15 installers, current 0.16 installer, future versions and other
packages are preserved. This frozen cleanup allowlist does not traverse legacy
boot storage or sibling version directories.

Retained `previous-<manifest SHA-256>` directories are eligible only after
this app has verified the active candidate and its metadata still matches.
Each must contain exactly the six known regular, non-aliased members; its
small manifest must match the directory suffix, identify Diagnostix 0.01,
0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09 or 0.10, and give the observed payload sizes. Cleanup reads the small manifest,
not the old RAM image. These checks establish cleanup ownership and shape;
they do not establish that an old image is bootable. Unknown or malformed
members, foreign/future versions and symlinks remain untouched. The preview
is capped at 32 items and fails closed above that cap.

Confirmation rechecks the exact saved preview, active candidate metadata,
namespace owner, journals and staging state. No manifest or UI field supplies
a deletion path. Cleanup never removes active `boot`, `.owner`, `boot.new`
or recovery journals; any stage/journal blocks all cleanup, and no cleanup
is allowed after a loader handoff attempt. Deleting a retained generation
removes that rollback copy. Cached installers may be previewed before the
first candidate install. No cleanup happens automatically at startup.

## Native Linux build

Use Python 3, native Clang/LLD and ImageMagick. Obtain the official
[OpenOrbis v0.5.4 archive](https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain/releases/download/v0.5.4/toolchain-llvm-18.tar.gz),
SHA-256 `3c7cd5bb593ca74fa1c13fd59f3938dc0fc07985167f7275063019e63abe4526`.
The default SDK cache is `/var/tmp/psx-diagnostix-fpkg-sdk-v0.5.4`; alternative
paths can be supplied with `--sdk` and `--sdk-archive`. Build dependencies are
checked against that archive, including headers, libraries and tools.

The SDK's older Linux package tool also requires OpenSSL 1.1. The helper below
downloads the official pinned SDK and OpenSSL 1.1.1w sources and builds only a
private host compatibility library. It installs nothing on the host and adds
no OpenSSL binaries to the PS4 application. It prints the three paths to pass
to `build.py`; all downloads and build receipts stay in the new cache:

```sh
TMPDIR=/var/tmp python3 diagnostics/ps4/fpkg/prepare_sdk.py \
  --output /var/tmp/diagnostix-sdk-new --jobs 4 --apply
```

The bundle directory must contain exactly `bzImage`, `initramfs.cpio.gz`,
`bootargs.txt`, `vram.txt`, `loader.elf` and `bundle.json`:

```json
{
  "schema": 1,
  "product": "Diagnostix",
  "version": "0.16",
  "profile": "ps4-baikal-diagnostix-ram-alpha1",
  "kernel_release": "7.2.11-psx-ps4-baikal-uvd-runtime-dev6",
  "files": {
    "bzImage": {"bytes": 1, "sha256": "actual-64-character-sha256"},
    "initramfs.cpio.gz": {"bytes": 1, "sha256": "actual-64-character-sha256"},
    "bootargs.txt": {"bytes": 1, "sha256": "actual-64-character-sha256"},
    "vram.txt": {"bytes": 5, "sha256": "actual-64-character-sha256"},
    "loader.elf": {"bytes": 1, "sha256": "actual-64-character-sha256"}
  }
}
```

The example contains placeholders; the tool accepts only actual hashes/sizes.
Manifest hashes are compiled into the application. Runtime JSON never supplies
paths or relaxes verification. Boot arguments are bounded ASCII (511 bytes),
VRAM is exactly 1024 MiB, and the payload plus reserve must fit 1 GiB.

```sh
TMPDIR=/var/tmp python3 diagnostics/ps4/fpkg/build.py \
  --bundle /var/tmp/diagnostix-bundle --output /var/tmp/diagnostix-fpkg-01
TMPDIR=/var/tmp python3 diagnostics/ps4/fpkg/build.py \
  --bundle /var/tmp/diagnostix-bundle --output /var/tmp/diagnostix-fpkg-01 --apply
TMPDIR=/var/tmp python3 -m unittest discover -s diagnostics/ps4/fpkg/tests -v
```

The default command only validates and prints a plan. `--apply` creates a new
host output directory with a real parent and enough space for four payload
copies plus build overhead, including PkgTool temporary PFS files. Tool temporary
files stay under that output directory so the same filesystem is budgeted. It builds ELF/SELF/SFO/FPKG, runs package validation,
extracts the package and compares every staged file. `BUILD.json` records inputs,
source, toolchain and resulting package hashes; `SHA256SUMS` covers the package/receipt.
There is no upload, installation, console discovery or payload execution in the
builder. A successful host build does not establish hardware acceptance.

Automatic test sends `loader.elf` exactly as installed. Manual test saves the
installed boot arguments plus ` dx.mode=manual` as
`/data/diagnostix/manual-bootargs.txt` and rewrites the loader's path to that
file in the bytes being sent, so neither the loader nor its hash pin changes.

`LICENSES/` and `provenance.json` preserve the Omarchy psX MIT foundations.
The package also includes the pinned SDK/tools notices, Gontserrat OFL text,
and the loader's BSD notices and pinned upstream provenance.
This launcher source is distributable; a package containing private firmware
is a separate local artifact and has no automatic publication permission.

## Southbridge read

The kernel refuses `hw.sce_subsys_subid` to an installed app (`errno=1`). The
launcher tries that plain read first, then asks GoldHEN's public SDK for root
(syscall 500), reads the value once and returns root before doing anything else.
Without GoldHEN, or if it refuses, the row stays Unknown and the loader still
makes its own check. A small detection payload is not an option: GoldHEN's port
9090 closes after one payload, and the loader needs that one (EXP-20261108-007).
