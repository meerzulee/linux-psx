# Kernel artifact contract

An artifact set is specific to a target, source commit, resolved configuration
and compiler. `bzImage` must be installed with the **whole** matching module
archive. A release string alone does not establish identical bytes.

The builder writes `output/6.18-ps4-baikal/`. Linux CI packaging adds guidance,
`artifacts.json`, `BUILD-SHA256SUMS`, complete corresponding source and a final
`SHA256SUMS` in `dist/`. No distribution boot profile, installer, launcher,
network-manager adapter or firmware is packaged.

## Generic metadata: schema 1

`artifacts.json` is a distribution-independent identity document:

| Field | Meaning |
| --- | --- |
| `schema` | Integer `1`. |
| `target`, `platform` | `6.18-ps4-baikal`, `ps4-baikal` for this target. |
| `kernel_release` | `6.18.44-psx-ps4-baikal1`. |
| `kernel_sha256` | SHA256 of the packaged `bzImage`. |
| `kernel_config_sha256` | SHA256 of the resolved packaged `config`. |
| `embedded_firmware` | Boolean `false`. |
| `modules_archive.path`, `.sha256` | Relative archive filename and SHA256. |
| `module.name` | `psx_mts_net`. |
| `module.path` | Archive-relative `lib/modules/<release>/kernel/drivers/net/ethernet/psx_mts_net.ko`. |
| `module.sha256` | SHA256 of the exact archived runtime module. |
| `module.build_note_hex` | Hex bytes of its entire ELF `.note.gnu.build-id` section, for comparison with the loaded sysfs note. |
| `source_commit`, `upstream`, `workflow_run` | Immutable project/upstream identity and build location. |
| `hardware_acceptance` | Qualification statement for this artifact, separate from historical tests. |

Consumers must validate required identity fields and treat metadata extensions
as informational. `module.path` does not prescribe `/usr/lib` or another local
layout. The module archive contains only regular files/directories under
`lib/modules/<release>/` and its parent directories. No build/source symlinks
to a builder's private paths are included.

Verify hashes before installation and compare the installed module plus
running release/config and loaded build note before activation. The metadata
does not hash an already running kernel image in memory and is not a signature.

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

```sh
# Linux
sha256sum -c SHA256SUMS
# macOS
shasum -a 256 -c SHA256SUMS
```
