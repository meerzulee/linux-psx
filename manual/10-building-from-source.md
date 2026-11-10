# Building from Source

A fresh build is a new binary. It may be exactly what you want, but it doesn't inherit the hardware results of any published kernel.

## On Linux x86-64

Use native tools. No container or virtual machine is needed.

On Arch or Omarchy:

```sh
sudo pacman -S --needed base-devel bc bison cpio flex gawk git kmod libelf openssl python rsync xz zstd
git clone https://github.com/meerzulee/linux-psx.git
cd linux-psx
make check
make TARGET=7.2-ps4-baikal-native-dev
```

On Debian or Ubuntu:

```sh
sudo apt-get install bc binutils bison cpio dwarves flex gawk gcc git kmod libdw-dev libelf-dev libssl-dev make python3 rsync xz-utils zstd
git clone https://github.com/meerzulee/linux-psx.git
cd linux-psx
make check
./build.sh -t 7.2-ps4-baikal-native-dev
```

`make check` verifies the imported patches and targets, and runs the host tests. Source fetches need GitHub access and several gigabytes of free space. The results land in `output/<target>/`.

## On macOS ARM64

Don't run the kernel build directly on macOS. Use the OrbStack helper, which cross-compiles inside a Debian builder:

```sh
orb status                    # Running
docker context show           # orbstack
make check
./scripts/build-kernel-orbstack -t 7.2-ps4-baikal-native-dev
```

## Rebuilding the published kernel

To rebuild beta2, the kernel on the [Kernels](03-kernels.md) page, start from the source archive in [its release](https://github.com/meerzulee/linux-psx/releases/tag/v7.2.11-psx-ps4-baikal-beta2). It's complete and already patched, and it comes with the configuration and rebuild notes. The build targets below don't produce beta2, because they don't include its video decoding work.

## Build targets for development

| Target | Kernel release | What it is |
| --- | --- | --- |
| `7.2-ps4-baikal-native-dev` | `7.2.11-psx-ps4-baikal-native-dev4` | Baikal port with built-in Ethernet. Start here for new Baikal work. No hardware result for this version yet. |
| `7.2-ps4-baikal` | `7.2.11-psx-ps4-baikal-dev1` | Baikal port without the Ethernet autoload patch. Compile-tested only. |
| `7.2-ps4` | `7.2.11-psx-ps4-dev2` | A separate patch stack for all PS4 southbridges. Compile-tested only. |
| `7.2-ps5` | `7.2.3-psx-ps5-dev1` | PS5 candidate. No hardware result. |

Always name a `TARGET`, because the repository's default is an older kernel line.

## Rebuilding DiagnostiX

DiagnostiX has its own checks, kept apart from the kernel build:

```sh
make check-diagnostics
python3 scripts/build-ps4-diagnostics --help
```

The image needs several separate inputs, including a whole matching kernel and module set. The [image and setup guide](../docs/DIAGNOSTICS.md) covers them.

Full build details are in [Building Linux psX](../docs/BUILDING.md).

<!-- manual-navigation -->

---

[← Previous](09-help-port-your-console.md) · [Contents](README.md) · [Next →](11-contributing.md)
