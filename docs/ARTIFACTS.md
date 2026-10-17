# Kernel artifact contract

An artifact set is specific to a target, source commit, resolved configuration
and compiler. `bzImage` must be installed with the **whole** matching module
archive. A release string alone does not establish identical bytes.

The builder writes `output/6.18-ps4-baikal/`. Linux CI packaging adds guidance,
`artifacts.json`, `BUILD-SHA256SUMS`, complete corresponding source and a final
`SHA256SUMS` in `dist/`. No distribution boot profile, root-filesystem
installer, launcher, Ethernet setup tool or firmware is packaged.

## Two downloads

CI stages two independent downloads from the same build:

| Download | Contents |
| --- | --- |
| `linux-psx-ps4-baikal-<commit>` | Kernel, whole modules archive, config, map/manifest/version, `artifacts.json` and boot guidance. No Linux source archive. |
| `linux-psx-ps4-source-<commit>` | Complete `source-<release>.tar.zst` and an identical copy of `artifacts.json`. |

Each contains its own `SHA256SUMS`, covering every included file. Extract the
small kernel download to test the build. Source is available separately.

The flat `dist/` files remain the complete local packaging set; `dist/runtime/`
and `dist/source/` are the CI upload directories. The metadata records the
source archive's hash as well as kernel/module identities, tying the two
downloads together. Future release assembly verifies both before producing a
small `kernel-<release>.zip`, the separate source archive, `artifacts.json` and
the release notes.

## Generic metadata: schema 1

`artifacts.json` is a distribution-independent identity document:

| Field | Meaning |
| --- | --- |
| `schema` | Integer `1`. |
| `target`, `platform` | `6.18-ps4-baikal`, `ps4-baikal` for this target. |
| `kernel_release` | `6.18.44-psx-ps4-baikal2`. |
| `kernel_sha256` | SHA256 of the packaged `bzImage`. |
| `kernel_config_sha256` | SHA256 of the resolved packaged `config`. |
| `embedded_firmware` | Boolean `false`. |
| `modules_archive.path`, `.sha256` | Relative archive filename and SHA256. |
| `module.name` | `psx_mts_net`. |
| `module.path` | Archive-relative `lib/modules/<release>/kernel/drivers/net/ethernet/psx_mts_net.ko`. |
| `module.sha256` | SHA256 of the exact archived runtime module. |
| `module.build_note_hex` | Hex bytes of its entire ELF `.note.gnu.build-id` section, for comparison with the loaded sysfs note. |
| `source_archive.path`, `.sha256` | Separate corresponding-source archive filename and SHA256. |
| `source_commit`, `upstream`, `workflow_run` | Immutable project/upstream identity and build location. |
| `hardware_acceptance` | Qualification statement for this artifact, separate from historical tests. |

Consumers must validate required identity fields and treat metadata extensions
as informational. `module.path` does not prescribe `/usr/lib` or another local
layout. The module archive contains only regular files/directories under
`lib/modules/<release>/` and its parent directories. No build/source symlinks
to a builder's private paths are included.

Verify hashes before installation. A consumer can compare an installed module
with the running release/config and the loaded build note. The metadata does
not hash an already running kernel image in memory and is not a signature.

## Corresponding source and provenance

`source-<release>.tar.zst` contains the complete patched Linux source, resolved
`.config`, original `COPYING` and `LICENSES/` notices, plus:

- `PSX-BUILD-INPUTS.tar.gz`: this project's committed patches, config, target,
  build tools, provenance, credits and documentation.
- `PSX-BUILD-MANIFEST.txt`: compiler, upstream, ordered patch-set and build identity.
- `PSX-REBUILD.md`: reconstruction entry point.

Generated objects, `.git` history, firmware blobs and private research are not
included. Patched source files ignored by upstream Git rules are explicitly
included. Kernel GPL terms and each existing per-file notice remain in force.
The original frozen import and active target have separate manifests; run
`make check` to verify their checksums and ordering.

From an extracted CI download or kernel ZIP, verify its included files:

```sh
# Linux
sha256sum -c SHA256SUMS
# macOS
shasum -a 256 -c SHA256SUMS
```
