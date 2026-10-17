# Building Linux psX

The active root target is `6.18-ps4-baikal`, release
`6.18.44-psx-ps4-baikal2`. It fetches upstream Linux v6.18.44 from
`https://github.com/gregkh/linux.git`, checks commit
`1efe5d048a391de3ead2804b2e7f86376c356cc5`, and checks/applies 56 patches
in order. The builder never downloads a distribution or device firmware.
PS5 and the historical ultrawide snapshot are not active build targets.

Three PS4 development targets build Linux v7.2.11. Local Baikal candidates
have scoped boot evidence; rebuilt binaries have no hardware acceptance.
See [PORT-7.2.md](PORT-7.2.md).

- `7.2-ps4-baikal`: the `6.18-ps4-baikal` series ported to 7.2, release
  `7.2.11-psx-ps4-baikal-dev1`. Build it with `make TARGET=7.2-ps4-baikal` or
  `./scripts/build-kernel-orbstack -t 7.2-ps4-baikal`.
- `7.2-ps4-baikal-native-dev`: that exact 61-patch port plus the unchanged
  6.18 native Ethernet autoload patch, release
  `7.2.11-psx-ps4-baikal-native-dev1`. Build with
  `make TARGET=7.2-ps4-baikal-native-dev` or
  `./scripts/build-kernel-orbstack -t 7.2-ps4-baikal-native-dev`.
  Only LOCALVERSION differs from the base config. One local build passed
  short wired traffic in EXP-20261018-004; this does not qualify CI rebuilds.
- `7.2-ps4`: the rmux 7.x stack for all PS4 southbridges, release
  `7.2.11-psx-ps4-dev1`. Build it with `make TARGET=7.2-ps4` or
  `./scripts/build-kernel-orbstack -t 7.2-ps4`.

## Linux x86-64

Use native tools; no container or VM is required. On Arch/Omarchy:

```sh
sudo pacman -S --needed base-devel bc bison cpio flex git kmod libelf openssl python rsync xz zstd
make check
make TARGET=6.18-ps4-baikal
```

On Debian/Ubuntu:

```sh
sudo apt-get install bc binutils bison cpio dwarves flex gcc git kmod libelf-dev libssl-dev make python3 rsync xz-utils zstd
make check
./build.sh -t 6.18-ps4-baikal
```

`./build.sh --jobs 4` selects parallelism; the default uses 80% of online CPUs.
Source fetches need GitHub access and a multi-gigabyte build workspace. Check
free storage before building. GNU make, GNU tar and GNU binutils are required.
Native builds on other architectures require an appropriate x86-64 cross-toolchain;
the supported cross-build path below is macOS ARM64.

## macOS ARM64

Install/start OrbStack and use its Docker context. The helper checks both
before any Docker operation:

```sh
orb status                    # Running
docker context show           # orbstack
make check
./scripts/build-kernel-orbstack -t 6.18-ps4-baikal
```

This uses an ARM64 Debian builder with `x86_64-linux-gnu-` cross-compilers,
`ARCH=x86_64` for configuration and compilation, and a separate
`linux-psx-kernel-build` Docker volume. The container definition pins the Debian
base digest; installed package versions can change with its package repository.
Compiler and image identities are recorded, so do not assume different hosts
produce byte-identical outputs. Do not run the kernel build directly on Darwin.
Do not start another local container/VM runtime for this workflow.

## Source, output and repeat builds

- `targets/*.env` is the target registry, with repository-root paths. The
  active target is `6.18-ps4-baikal`; `7.2-ps4-baikal`, `7.2-ps4-baikal-native-dev` and
  `7.2-ps4` are development targets.
- `platforms/ps4/targets/` and the original imported config are historical inputs.
- `src/<target>/` is a disposable, marked upstream clone. Every invocation
  resets and cleans that generated clone before applying the recorded series.
  Make source changes in patches; never keep work in this generated tree.
- `PSX_KERNEL_BUILD_ROOT` moves the generated source/cache root only; outputs
  stay in the repository. The OrbStack helper sets this to `/build`.
- `output/6.18-ps4-baikal/` contains the kernel, full matching modules archive,
  resolved config, `System.map`, manifest, version and SHA256 checksums.

`./build.sh --patches-only` fetches/checks/applies the full stack without
compilation. `--clean` reclones only the selected generated source and removes
that target's outputs for a full build. An unrecognized existing source directory
is refused. `./build.sh --help` also works on macOS.

The active config is firmware-free and keeps the historical tested
`CONFIG_NETFILTER=n` state. A distribution requiring firewall/container options
needs a separately recorded config and its own qualification. Inherited
`netfilter-ufw` and ultrawide fragments are not silently enabled.

## CI and packaging

GitHub Actions runs `make check`, compiles on native Ubuntu x86-64, then runs
`scripts/ci/package-release`. The packager produces `dist/` with generic
`artifacts.json`, checksums, kernel/module/config outputs, kernel guidance and
complete corresponding GPL source. See [ARTIFACTS.md](ARTIFACTS.md).

CI uploads **two separate artifacts**. `linux-psx-<target>-<commit>` contains
the kernel, full modules, build data and instructions;
`linux-psx-<target>-source-<commit>` contains complete corresponding source. Choose
the first for testing; download the second when inspecting or rebuilding source.
Both carry identical build metadata and their own complete `SHA256SUMS`.

For native local packaging after a committed build, run
`./scripts/ci/package-release`; `dist/` must not already exist. The source
archive includes build inputs from `git archive HEAD`, so commit intended
source changes before packaging. A local OrbStack cross-build does not package
directly on macOS: packaging needs Linux GNU tar, objcopy, modinfo and zstd
and access to the same generated source. CI is the supported packaging path.

Push/PR builds publish expiring CI artifacts only. The manual workflow defaults
`publish_release` to false. Explicitly enabling it after authorized review
publishes an experimental prerelease, never an automatic stable/latest release.
Release assembly verifies both artifacts belong to the expected source commit
and publishes `kernel-<release>.zip` separately from `source-<release>.tar.zst`.
Neither compilation nor packaging constitutes a hardware test.
