# PS4 patch imports and active target

## Active Linux psX target

`targets/6.18-ps4-baikal.env` is the buildable root target. It uses the same
pinned upstream and 54 baseline patches described below, then the unchanged
MTS2 Ethernet patch imported from the published PS4 source at:

https://github.com/meerzulee/linux-ps4/tree/386df1ad97d92defee1794bf961a0e2f5dc85a1b

Its separate config has no embedded firmware; only the local-version string
changed from the source project's firmware-free public config. Build identity
and paths are adapted for psX. The original imported snapshot stays frozen.
`provenance/6.18-ps4-baikal.json` records the 55 active patch hashes, ordering,
source paths and adaptations. Run `make check` to verify both imports.

See [BUILDING.md](BUILDING.md) and [the PS4 status](../platforms/ps4/STATUS.md).

## rmux 7.x source imports

`platforms/ps4/patches/rmux-7.1.7/` and `rmux-7.0.8-baikal/` are per-commit
exports of rmux's Strawberry kernel for Linux 7.1.7 (Aeolia/Belize plus a
Baikal unification attempt) and 7.0.8 (Baikal). They are inputs for the Linux
7.2 port, not build targets. See [RMUX-IMPORT.md](RMUX-IMPORT.md) for sources,
filters, validation and the maintainer's stated request.

`platforms/ps4/patches/7.2-ps4/` is the development series built from the
7.1.7 import on Linux 7.2.11. [PORT-7.2.md](PORT-7.2.md) records how each
patch differs from its rmux source.

`platforms/ps4/patches/7.2-ps4-baikal/` ports the 6.18 series to Linux 7.2.11.
Its series references the unchanged 6.18 files in place; the directory holds
only the patches regenerated for 7.2 and the port fixes.

## Original frozen import

Imported on 2026-10-16 from:

https://github.com/meerzulee/linux-ps4/tree/f61663b369b76f1fc7e8cf29dd946a1bfc5b1e75

The source is the active `agent/document-baikal-capabilities` revision used by
the existing development workspace. It is newer than the old public r1 tag.
This import does not re-label that historical release or its tested artifacts.

## Included

- The complete `6.18-baikal` patch directory: 54 active patches, retained
  disabled experiments, four `.candidate` files and its original series.
- The separate `6.18-baikal-ultrawide` series: the same 54 baseline patches
  followed by one experimental display delta.
- The full 6.18 configuration, four referenced configuration fragments and
  both target environment files.
- The original GPLv2 license text and the inherited patch provenance ledger.

These files are copied byte for byte from the pinned Git objects. Inherited
release labels such as `omarchy-ps4` remain intact because they are build
identity inputs. Some vendor-source bytes are not UTF-8; do not normalize or
re-encode the patch files.

Older 5.4 and 6.15 stacks and unselected reference patch directories remain in
the original repository. They are outside this first import. Generated kernels,
module archives, firmware blobs, root filesystems, boot loaders and raw UART
captures are outside the import as well.

## Base and ordering

The selected upstream source is `gregkh/linux`, tag `v6.18.44`, at:

https://github.com/gregkh/linux/tree/1efe5d048a391de3ead2804b2e7f86376c356cc5

Keep the exact order in `platforms/ps4/patches/<target>/series`. Commented
patches and candidates are inactive. Do not concatenate this series with the
PS5 patch: the researched PS5 snapshot targets Linux 7.2.3 and has independent
boot and configuration requirements.

Historical target paths are relative to `platforms/ps4/`. These imported target
files remain source metadata; the new root target is the active build interface.
For the original snapshot's historical instructions:

https://github.com/meerzulee/linux-ps4/blob/f61663b369b76f1fc7e8cf29dd946a1bfc5b1e75/README.md

## Verify the imported files

From the `linux-psx` repository root on Linux:

```sh
sha256sum --check provenance/ps4-SHA256SUMS
```

On macOS:

```sh
shasum -a 256 -c provenance/ps4-SHA256SUMS
```

`provenance/ps4-import.json` records each original path, immutable source URL,
last-changing source commit, size and SHA-256. It also records each active
series entry and an ordered patch-set digest using the original entry names.
`provenance/VALIDATION.json` records the checks actually completed for this
import. Patch application is a source check, not a build or a hardware test.

## Authorship and acceptance

Some subsystem patches were generated as aggregate diffs and use a generic
`From: PS4 Linux Baikal Port` header. That label is not an exhaustive author
list. Original trees, individual commits, preserved file notices and adaptation
notes supply the fuller attribution. See [CREDITS.md](../CREDITS.md).

The inherited ledger distinguishes copied changes, adapted changes and
comparison-only work. It also credits rmuxnet's prior bridge-lifetime work
without rewriting the frozen local patch header. This distinction is retained.

The old project tested one PS4 Slim with a Baikal B1 southbridge, firmware
12.02, using a particular loader and image stack. That evidence does not cover
every PS4 revision, the ultrawide delta or PS5. No new hardware acceptance is
established by moving patches into this repository.
