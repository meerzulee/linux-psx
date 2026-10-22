# Separate PS5 kernel candidate

`7.2-ps5` prepares the community PS5 kernel on its recorded Linux v7.2.3 base,
with release `7.2.3-psx-ps5-dev1`. Source inputs are verified and a local
OrbStack cross-build completed on 2026-10-18. Hardware validation is pending.
This is not a PS4/PS5 universal kernel
or an established firmware 12.40 Linux boot path. The default PS4 target remains
`6.18-ps4-baikal`.

## Inputs and attribution

The unchanged community `linux.patch`, original `.config` and README are pinned
to `753c7936dcb079bfeaaa0bb19dfc495744266500`. Their sizes and SHA256 values match
the existing research snapshot. The patch preserves all source notices and
credits Andy Nguyen, Armandas Kvietkus/rmuxnet and the other original
contributors through its upstream history and per-file notices. See
[CREDITS.md](../CREDITS.md) and [the import record](../provenance/7.2-ps5.json).

https://github.com/ps5-linux/ps5-linux-patches/tree/753c7936dcb079bfeaaa0bb19dfc495744266500

Upstream Linux is v7.2.3 at `58e7295cfecaddec94629160386412e0f2b1e8fe`:

https://github.com/gregkh/linux/tree/58e7295cfecaddec94629160386412e0f2b1e8fe

The original config remains in `platforms/ps5/config/upstream-7.2.3.config`.
The selected `7.2-psx-ps5.config` changes exactly three lines:

- `CONFIG_LOCALVERSION` becomes `-psx-ps5-dev1` for a distinct artifact identity.
- `CONFIG_EXTRA_FIRMWARE` becomes empty instead of embedding
  `amdgpu/ps5_vcn.bin`.
- `CONFIG_EXTRA_FIRMWARE_DIR` is removed because no firmware is embedded.

No PS4 fragments or hardware tweaks are added. The builder runs `olddefconfig`
for its compiler and records the resolved config; compiler capability settings
may therefore differ from the imported reference. `make check` verifies the
unchanged import, upstream pins, separate series and exact source-config delta.

## Local build

On Linux x86-64, after installing the prerequisites in [BUILDING.md](BUILDING.md):

```sh
make check
make TARGET=7.2-ps5
```

On macOS ARM64, use the existing OrbStack builder:

```sh
orb status
docker context show
./scripts/build-kernel-orbstack -t 7.2-ps5
```

OrbStack must report `Running` and Docker's context must be `orbstack`. The
helper fetches/checks the Linux commit and applies the single unchanged patch.
It does not acquire device firmware or run a console payload.

`output/7.2-ps5/` contains `bzImage`, `config`, `System.map`, `version.txt`,
`manifest.txt`, the complete `modules-7.2.3-psx-ps5-dev1.tar.zst`, and
`SHA256SUMS`. Keep the kernel and whole module archive together. The manifest
records the compiler, builder image, resolved config and ordered patch identity.
Build inputs are pinned, but repository-installed compiler packages can change;
byte-identical outputs across builder revisions are not claimed.

The first completed local build used Debian GCC 14.2.0 and produced 519
modules. Its measured identities are recorded under `local_build_validation`
in [provenance/7.2-ps5.json](../provenance/7.2-ps5.json):

```text
bzImage SHA256: 64162b82cf3f7ac37cdb8b4fe8638b10a1d53907e1bfb1be48383b2c9726c822
modules SHA256: fd81f93546f1c70f053dada1124742df2f2350e1dc310359b3a82595551e4f37
```

The complete build also validates the config's libdw and GNU awk host
requirements; see [BUILDING.md](BUILDING.md). This is compilation evidence,
with no PS5 boot or device acceptance.

Release packaging is pending. The current packager requires PS4 MTS config,
source and module identities and writes PS4 guidance, so do not use it for this
candidate. No PS5 `artifacts.json`, public binary release or complete release
bundle is produced by adding this target. A later public binary package must
include complete corresponding GPL source and preserved notices.

## Runtime contracts still required

The reference config enables 16 CPUs, AMDGPU as a module, USB xHCI/storage/UAS,
SCSI disks and ext4. Btrfs and NVMe are modules. These are configuration facts,
not local device acceptance. Its external firmware requirements must be met by
the matching initramfs/root filesystem. Removing firmware embedding does not
establish that graphics or video decode work without those files.

The selected PS5 loader, initramfs, root filesystem, firmware and capture/recovery
procedure remain separate inputs. The reviewed loader uses `bzImage`,
`initrd.img`, optional `cmdline.txt` and `vram.txt`; do not substitute PS4 boot
labels, loader paths or profiles. Its reviewed older-firmware boot interface is
not evidence of a firmware 12.40 handoff.

https://github.com/ps5-linux/ps5-linux-loader/tree/64637c177eaed277fb6ccce394cbb29dd047b893

Omarchy-specific early userspace and rootfs policy belong in sibling
`omarchy-psx`. This kernel repository does not install packages, write console
storage or provide a launcher. Hardware acceptance requires observed PS5 boots
and device results under an agreed capture and rollback plan.

## Native image milestone and follow-up

The shared Omarchy console packages and native PS5 image builder live in the
sibling distribution repository (`platforms/ps5/IMAGE.md`). The kernel packager
now accepts `7.2-ps5`, with whole matching modules and complete corresponding
source; local builds and image assembly still establish no console acceptance.

The requested MediaTek Wi-Fi/Bluetooth work is pinned separately in
`provenance/ps5-mediatek-followup.json` at upstream commit
`b84c899348a6b71aa62cc1c73dfc5e7c03860b4c`. It is not part of the current
baseline build. Port the additional Bluetooth patches and MT7921E config as a
distinct development target, compare already-present community hunks first, and
keep udev and controller-pairing policy in the distribution.
