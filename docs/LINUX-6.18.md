# Preserved Linux 6.18 Baikal target

The Linux 6.18 target remains available alongside the current Linux 7.2 Baikal
development work. Its patches, configs, provenance, build recipes and historical
results are preserved; moving this guide does not promote or replace an artifact.

## Build

`6.18-ps4-baikal` produces `6.18.44-psx-ps4-baikal2`. It fetches upstream
Linux v6.18.44 at commit `1efe5d048a391de3ead2804b2e7f86376c356cc5` and applies
56 ordered patches, including MTS2 Ethernet and native autoload. No existing
Omarchy checkout or private kernel archive is required.

From the repository root, after the [host prerequisites](BUILDING.md):

```sh
make check

# Native Linux x86-64:
make TARGET=6.18-ps4-baikal

# macOS ARM64, using OrbStack and Docker context "orbstack":
./scripts/build-kernel-orbstack -t 6.18-ps4-baikal
```

The generic `make` default remains this target. CI has independent
[Linux 6.x and Linux 7.x workflows](BUILDING.md#ci-and-packaging); the main
README selects `7.2-ps4-baikal-native-dev` explicitly for current PS4
development.

## Outputs and corresponding source

Outputs live under `output/6.18-ps4-baikal/`: `bzImage`, the whole matching
module archive, resolved config, System.map, build manifest, version and
checksums. Firmware is external and is neither embedded nor bundled.

CI packages two downloads from one build:

- `linux-psx-<target>-<commit>`: kernel, whole modules and identity/boot guidance.
- `linux-psx-<target>-source-<commit>`: complete corresponding GPL source and
  identical build metadata.

Each download carries its own checksums. CI artifacts expire after 30 days.
A public prerelease uses a separate authorized manual workflow and is never
automatically marked stable/latest. See [the artifact contract](ARTIFACTS.md)
for exact contents, source reconstruction and verification.

## Historical hardware scope

The same patch set, built locally as
`6.18.44-psx-ps4-baikal-native-dev1`, passed two native Ethernet boots on one
Baikal PS4, including cable absence at launch and unplug/replug. That does
not qualify a fresh CI build or a different kernel/module/config set.

The 54 baseline patches remain byte-for-byte preserved. The native Ethernet
autoload change follows the imported MTS2 driver. Historical configs and the
ultrawide series remain reference inputs, with their original evidence limits.
[Patch provenance](PS4-PATCHES.md), [credits](../CREDITS.md) and
[Ethernet status](ETHERNET.md) retain their detailed source and test records.

The experimental Liverpool UVD/1080p results are from a separate Linux 7.2.11
build. They do not apply to this Linux 6.18 target.
