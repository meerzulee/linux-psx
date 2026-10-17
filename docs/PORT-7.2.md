# Linux 7.2 port

Working record for moving the PS4 kernel to Linux 7.2.y. Upstream base:
`gregkh/linux` v7.2.11 at `a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea`
(current stable on 2026-10-17; 7.2.y will reach end of life a few weeks after
7.3 is released).

There are three PS4 development targets. All compile. `7.2-ps4-baikal` passed
one core boot and its separate native Ethernet candidate passed one boot with
short wired traffic on 2026-10-18. The rmux-based `7.2-ps4` has not booted.
None has hardware acceptance; local evidence does not qualify CI rebuilds.

| Target | Stack | Role |
| --- | --- | --- |
| [`7.2-ps4-baikal`](#the-72-ps4-baikal-development-target) | The 6.18-ps4-baikal series (54 patches and MTS2) on 7.2.11 | Passed its first core Baikal boot in EXP-20261018-001, replacing the kernel and matching modules while retaining the existing boot environment. Native Ethernet remains untested. |
| [`7.2-ps4-baikal-native-dev`](#the-native-ethernet-baikal-candidate) | The unchanged 61-patch Baikal port plus the 6.18 native Ethernet autoload patch | The measured local candidate passed one native boot, DHCP and short wired traffic in EXP-20261018-004. |
| [`7.2-ps4`](#the-72-ps4-development-target) | rmux's 7.1.7 Strawberry stack on 7.2.11, plus MTS2 | Reference for Aeolia and Belize, and the source of their features for a unified kernel. |

The unified kernel for every southbridge is to be built on the stack that
boots. With the first `7.2-ps4-baikal` boot recorded, further qualification and
rmux's Aeolia and Belize support proceed one feature at a time, each tested
as a single hardware variable.
Hardware testing follows the same order: Baikal first, then Aeolia and
Belize. The 6.18 series has Aeolia and Belize device IDs and quirks, but has
only run on Baikal.

`7.2-ps4` reuses rmux's Strawberry kernel work rather than reimplementing it,
with credit in [CREDITS.md](../CREDITS.md). It starts from the
[rmux 7.1.7 import](RMUX-IMPORT.md): rmux's Aeolia/Belize stack with its
Baikal unification commits. It proceeds one
variable at a time:

1. Rebase the 170 imported patches onto v7.2.11 and make them compile, with
   no curation.
2. Drop the Strawberry banner and the Mesa lock endpoint.
3. Add psX MTS2 Ethernet.
4. Boot testing. Because the 6.18 series has run on Baikal, the first Baikal
   boot uses `7.2-ps4-baikal` instead. rmux's display path (`ps4_bridge.c`) is
   untested on Baikal.

## Step 1: rebase onto v7.2.11

`git am -3` applied all 170 patches. Four needed manual merge resolution:

| Patch | File | 7.2 change | Resolution |
| --- | --- | --- | --- |
| 0008 feat: AHCI support | `drivers/ata/ahci.c` | `ahci_pci_tbl` rewritten as designated initializers | Sony Aeolia/Belize/Baikal IDs added in the new style before the generic class entry |
| 0011 feat: amdgpu patches | `drivers/gpu/drm/amd/amdgpu/gmc_v7_0.c` | GART size set through `amdgpu_gmc_set_gart_size()` | Liverpool/Gladius use `amdgpu_gmc_set_gart_size(adev, SZ_512M)`, which also honors `amdgpu.gart_size` |
| 0030 small cleanup on ahci | `drivers/ata/ahci.c` | same table rewrite | Sony entries use `board_ahci_31bit_dma` |
| 0165 ps4: add Baikal southbridge support | `drivers/ata/ahci.c` | new `ahci_validate_bar_size()` after `pcim_iomap()` | Baikal SATA PHY initialization kept immediately after mapping, as rmux tested it; the new BAR check follows, so it reads `HOST_CAP` after PHY setup |

### Compile fixes

Four commits on top of the 170 patches make the tree build. Each fixes a
Linux 7.2 or compiler change; none changes behavior for valid input:

| Fix | Cause |
| --- | --- |
| `ps4: icc: use strscpy_pad() for the adapter name` | Linux 7.2 removed `strncpy()`. `strscpy_pad()` writes the same bytes for the constant `"icc"`. |
| `mt76x8: replace strncpy() removed in Linux 7.2` | `kalStrnCpy()` becomes an inline helper with `strncpy()` semantics (copy at most n bytes, zero-fill, no forced NUL). Some callers fill length-delimited firmware command fields, where `strscpy()` would drop a byte. |
| `mt76x8: adapt remain_on_channel to Linux 7.2` | Upstream `4dbd1829045e` added `const u8 *rx_addr`. cfg80211 passes it only with `NL80211_EXT_FEATURE_ROC_ADDR_FILTER`, which the driver does not set. |
| `mt76x8: make proc write bounds checks overflow-safe` | `if (u4CopySize >= count + 1)` wraps for `count == SIZE_MAX`; GCC 16 proves the resulting oversized `copy_from_user()` and fails with `__bad_copy_to()`. The fix compares `count < u4CopySize`. |

rmux fixed the same `gl_proc.c` build error on `baikal/7.0.8-Stable` in
`d98d3e9e0df2` (psX 6.18 carries the original as
`0500-network-mt7668/0006`). That change switches to `__copy_from_user()` and
makes `procDriverCmdWrite()` copy 2999 bytes whatever the write size, so the
port does not use it. The 6.18 target still carries it, and `7.2-ps4-baikal`
undoes it with a port fix (see
[The 7.2-ps4-baikal development target](#the-72-ps4-baikal-development-target)).

### Compile result

Built on 2026-10-17 on an Arch Linux x86-64 host with GCC 16.2.1
(`make ARCH=x86_64 -j16 bzImage modules`). Configuration: the psX
`6.18-psx-baikal.config`, the MT7668 and USB Ethernet fragments, and

```text
CONFIG_LOCALVERSION="-psx-ps4-72dev"
CONFIG_PS4_CPUFREQ=y
CONFIG_PS4_DMI_SPOOF=y
# CONFIG_WERROR is not set
```

`CONFIG_EXTRA_FIRMWARE` is empty. `X86_PS4`, `USB_XHCI_AEOLIA`,
`DRM_AMDGPU_CIK`, `MT76X8`, `MT6632`, `SKY2`, `MWIFIEX_SDIO` and
`MMC_SDHCI_PCI` are enabled.

Result: `make` exit 0, a 11,224,064-byte `bzImage` and 40 modules. All 38
remaining warnings are in the mt76x8 vendor driver: `-Wstringop-overread`,
`-Waddress`, `-Wmisleading-indentation`, `-Wempty-body`, and `-Wpragmas` for
the Clang-only `-Wcast-function-type-strict` in `__diag_ignore_all()`. The
build ran incrementally over three passes (the first with `make -k`); the
curated branch gets a clean build (see Official builds).

The image has not been booted. Compilation is not hardware acceptance.

## Step 2: drop the Strawberry banner and Mesa lock

On the port branch both commits are removed with `git rebase --onto`, which
leaves 168 of the 170 imported patches. Two later patches needed adjusting:

- `ps4-apcie: detect and log southbridge variant, GPU, and function map at
  boot` also edited the banner. That hunk is removed.
- `drivers/ps4: add Aeolia front panel buzzer driver` loses the Mesa lock
  lines from its `drivers/ps4/Makefile` context.

Compared with step 1, the tree differs only in `init/version-timestamp.c`
(now identical to v7.2.11), `drivers/ps4/Makefile` and the two deleted Mesa
lock files. No source file references either feature.

The liverpool_clk SCLK force driver is added (rmux `0050`) and removed again
(`0163`). The pair has no net effect, so it stays rather than rewriting the
commits between them.

`CONFIG_PS4_DMI_SPOOF` is off. Its Kconfig help says it confirms a PS4 through
the ACPI OEM identity, but the code does not check: `dmi_scan_machine()` calls
`ps4_dmi_populate()` on every boot, including when SMBIOS is valid, and fills
each missing field with a fixed value (serial `STRWB3RRYWA5H3R3`, a constant
UUID, board name `Belize` on every southbridge). It needs a runtime console
check and detected values before it can be enabled.

## Step 3: psX MTS2 Ethernet

The 6.18 MTS2 patch applies unchanged (line offsets only) and builds
`psx_mts_net.ko` with `CONFIG_PS4_MTS=m`. The series references the 6.18 file
directly, so its hash stays the one recorded in
[`6.18-ps4-baikal.json`](../provenance/6.18-ps4-baikal.json).

## The 7.2-ps4 development target

| Item | Location |
| --- | --- |
| Target | [`targets/7.2-ps4.env`](../targets/7.2-ps4.env), release `7.2.11-psx-ps4-dev1` |
| Series | [`platforms/ps4/patches/7.2-ps4/series`](../platforms/ps4/patches/7.2-ps4/series): 174 patches plus MTS2 |
| Config | [`platforms/ps4/config/7.2-psx-ps4.config`](../platforms/ps4/config/7.2-psx-ps4.config) with the MT7668 and USB Ethernet fragments |
| Provenance | [`provenance/7.2-ps4.json`](../provenance/7.2-ps4.json) |

The 174 patches are exported from the local port branch with
`git format-patch --zero-commit` and the same pinned diff options as the rmux
import. The provenance maps each one to its rmux import entry and source commit
and classifies the difference:

| Class | Count | Patches |
| --- | --- | --- |
| Unchanged from the rmux import | 160 | |
| Context lines only | 3 | `0014`, `0133`, `0163` |
| Adapted, with the resolution recorded | 5 | `0008`, `0011`, `0030`, `0101`, `0116` |
| Port fixes (new) | 6 | `0169`–`0172` compile fixes, `0173`–`0174` xHCI shutdown |

`make check` verifies the series order, every hash, each rmux mapping, the
target definition and the firmware-free config. The port branch itself is not
published. With it, the series regenerates byte-for-byte:

```sh
scripts/export-port72 --port-repo /path/to/port-repo          # rewrite series and provenance
scripts/export-port72 --port-repo /path/to/port-repo --check  # verify committed patches
```

The exporter refuses a commit that is neither a known rmux import entry nor
a recorded port fix, and a changed rmux patch without a recorded resolution.

The config is the 6.18 config moved to 7.2 with `make olddefconfig` after the
two fragments and these changes:

- `CONFIG_LOCALVERSION="-psx-ps4-dev1"`.
- `PS4_CPUFREQ` (new with the rmux stack, enabled in rmux's 7.1.7 config) is
  off. It has never run on Baikal; enabling it is a separate experiment.
- `PS4_DMI_SPOOF` is off (see step 2).
- `PS4_MTS=m`, as in the 6.18 target.
- `PREEMPT_VOLUNTARY` no longer exists on x86. `make olddefconfig` selects
  `PREEMPT`, which is also what rmux's 7.0.8 and 7.1.7 configs use.
- Every other difference is a Linux 7.2 Kconfig change: removed or renamed
  crypto, MDIO and helper symbols, and `NTFS3_FS` becoming a module next to
  the new `NTFS_FS`.

### Official builds

On 2026-10-17 `build.sh -t 7.2-ps4` passed from a fresh v7.2.11 clone on both
supported paths. Every series entry applied with `git apply`, and both runs
recorded the same series and patch-set hashes:

| Host | Compiler | Result |
| --- | --- | --- |
| Arch Linux x86-64, native | GCC 16.2.1 | `PASS: 7.2.11-psx-ps4-dev1`, 11 warnings |
| macOS ARM64, `scripts/build-kernel-orbstack` | Debian trixie `x86_64-linux-gnu-gcc` 14.2.0 | `PASS: 7.2.11-psx-ps4-dev1`, 14 warnings |

All warnings are in the mt76x8 vendor driver. The build artifacts stay local
and have not been booted.

## The 7.2-ps4-baikal development target

The 6.18 series, MTS2 Ethernet included, has run on a PS4 Slim with a Baikal
B1 southbridge as the private `6.18.44-ps4-baikal-mts2` build, which embedded
firmware ([PS4 status](../platforms/ps4/STATUS.md)). The firmware-free 6.18
builds have their own scoped evidence in
[`6.18-ps4-baikal.json`](../provenance/6.18-ps4-baikal.json). This target moves
the series to 7.2.11, unchanged wherever Linux 7.2 allows, so that its first
boot tests the kernel version rather than rmux's untested Baikal paths.

| Item | Location |
| --- | --- |
| Target | [`targets/7.2-ps4-baikal.env`](../targets/7.2-ps4-baikal.env), release `7.2.11-psx-ps4-baikal-dev1` |
| Series | [`platforms/ps4/patches/7.2-ps4-baikal/series`](../platforms/ps4/patches/7.2-ps4-baikal/series): the first 55 6.18 entries in order, then six port fixes; native autoload remains in the separate candidate below |
| Config | [`platforms/ps4/config/7.2-psx-baikal.config`](../platforms/ps4/config/7.2-psx-baikal.config) with the MT7668 and USB Ethernet fragments |
| Provenance | [`provenance/7.2-ps4-baikal.json`](../provenance/7.2-ps4-baikal.json) |

The port branch applies the 6.18 series with `git am` to v6.18.44 (MTS2 with
`git apply`), which gives the tree of the 6.18 build, then cherry-picks it onto
v7.2.11. Seven patches needed a manual merge:

| 6.18 patch | File | 7.2 change | Resolution |
| --- | --- | --- | --- |
| `0300-gpu-liverpool/0001` | `amdgpu_device.c` | Display Core is the default on CIK; the non-DC CIK case the patch extended is gone | Liverpool and Gladius get their own case that keeps Display Core opt-in (`amdgpu_dc > 0`), so the display stays on the DCE v8 path it uses on 6.18. Other CIK chips keep the 7.2 default. |
| `0300-gpu-liverpool/0001` | `gmc_v7_0.c` | GART size set through `amdgpu_gmc_set_gart_size()` | `SZ_512M` for Liverpool and Gladius, as in `7.2-ps4` |
| `0300-gpu-liverpool/0002` | `radeon_drv.c` | `radeon_pci_probe()` passes `dev` | Probe deferral unchanged, before `devm_drm_dev_alloc(dev, ...)` |
| `0400-storage-ahci/0001` | `ahci.c` | `ahci_pci_tbl` rewritten with designated initializers | Sony entries in the new style, still between Enmotus and Loongson |
| `0400-storage-ahci/0002` | `ahci.c` | `ahci_validate_bar_size()` added where the patch adds `ahci_baikal_shared_phy_seeded()` | Both kept |
| `1000-iommu/0003` | `iommu/amd/iommu.c` | The IOTLB flush functions that followed the new helper moved | Only the helper is added |
| `1100-pci-msi/0001` | `pci/msi/irqdomain.c` | The legacy `pci_msi_create_irq_domain()` code in the hunk is gone | `pci_msi_domain_write_msg()` is still made non-static |
| `1100-pci-msi/0002` | `pci/probe.c` | New trace include | The PS4 includes follow it |

The exporter keeps a 6.18 patch file wherever `git apply` of it gives the port
commit's tree, and regenerates the others from the port commit with their
original header and message bytes:

| Class | Count | Patches |
| --- | --- | --- |
| Unchanged 6.18 file, referenced in place | 47 | including MTS2 |
| Context lines only, regenerated | 6 | `0300/0002`, `0300/0004`, `0400/0002`, `1000/0003`, `1100/0001`, `1100/0002` |
| Adapted, with the resolution recorded | 2 | `0300/0001`, `0400/0001` |
| Port fixes (new) | 6 | below |

| Port fix | Cause |
| --- | --- |
| `ps4: icc: use strscpy_pad() for the adapter name` | As `7.2-ps4` `0169` |
| `mt76x8: replace strncpy() removed in Linux 7.2` | As `7.2-ps4` `0170` |
| `mt76x8: adapt remain_on_channel to Linux 7.2` | As `7.2-ps4` `0171` |
| `ps4: bpcie: replace pci_msi_create_irq_domain() removed in Linux 7.2` | psX creates the Baikal MSI parent domain with this helper. The driver now applies what the 6.18 helper did (the PCI/MSI domain flags, `IRQCHIP_ONESHOT_SAFE` and `DOMAIN_BUS_PCI_MSI`) and calls `msi_create_irq_domain()`. bpcie supplies its own domain and chip operations, so the helper's defaults never applied. The MSI core functions involved are unchanged from 6.18. |
| `mt76x8: adapt key and station operations to Linux 7.1` | Linux 7.1 passes the key and station operations a `wireless_dev`. psX's mt76x8 already wraps these operations in adapters; they now pass on the `wireless_dev`'s `net_device` and refuse one without a `net_device`, which cfg80211 never passed before 7.1. rmux's newer mt76x8 handles this itself. |
| `mt76x8: restore checked proc write copies` | Undoes `0500-network-mt7668/0006`, which the series keeps unchanged. Its `__copy_from_user()` calls skip `access_ok()`, and its `procDriverCmdWrite()` copies 2999 bytes whatever the write size. The handlers go back to `copy_from_user()` with the driver's sizes, and the comparison of `7.2-ps4` `0172` fixes the GCC 16 build error that `0006` worked around. |

`7.2-ps4` port fixes `0173` and `0174` are not taken. `0173` is not needed
because this stack already has feeRnt's Baikal xHCI shutdown fix
(`0800-usb-aeolia/0002`), and `0174` fixes an Aeolia problem left for the
Aeolia tests (see
[Known issues carried from 6.18](#known-issues-carried-from-618)).

The port branch is local. With it, the series regenerates byte-for-byte:

```sh
scripts/export-port72-baikal --port-repo /path/to/port-repo          # rewrite series and provenance
scripts/export-port72-baikal --port-repo /path/to/port-repo --check  # verify committed patches
```

Both exporters read `.gitattributes` from v7.2.11, so the output does not
depend on what the repository has checked out. `make check` verifies every
hash, and that each unchanged entry is the file recorded in the 6.18
provenance, so a later change to a 6.18 patch fails this target too.

The config is `6.18-psx-baikal.config` moved to 7.2 with `make olddefconfig`
after the two fragments, with `CONFIG_LOCALVERSION="-psx-ps4-baikal-dev1"`.
`CONFIG_EXTRA_FIRMWARE` is empty. Two of the config changes are more than
renames:

- `PREEMPT_VOLUNTARY` no longer exists on x86, so `make olddefconfig` selects
  `PREEMPT`.
- `NTFS3_FS` becomes a module next to the new `NTFS_FS`.

### Behaviour that differs from 6.18

- **Baikal AHCI probe.** Linux 7.2 calls `ahci_validate_bar_size()` after
  `pcim_iomap()` and before the Baikal SATA PHY setup. It reads `HOST_CAP` and
  fails the probe with `-ENODEV` if BAR0 is smaller than the reported ports
  need.
- **DRM bridge chain.** Linux 7.2's bridge chain iterators hold
  `encoder->bridge_chain_mutex` and bridge references. The legacy helpers that
  `0300-gpu-liverpool/0004` restores walk `encoder->bridge_chain` without
  them, as on 6.18, which had neither. The PS4 bridge is attached once and
  never removed.
- **Preemption**, as above.
- **mt76x8 proc writes.** The port fix above returns the proc write handlers
  to `copy_from_user()`. `procDriverCmdWrite()` copies at most the bytes
  written rather than always 2999, and a source address outside user space
  fails with `-EFAULT`.

### Known issues carried from 6.18

- On Aeolia, `xhci_hcd_pci_shutdown()` passes the driver's
  `struct aeolia_xhci` to `usb_hcd_pci_shutdown()`, which expects a
  `struct usb_hcd`. `7.2-ps4` fixes it with `0174`. Baikal is not affected.
- The [mt76x8 vendor issues](#known-mt76x8-vendor-issues) below.

### First boot: EXP-20261018-001

On 2026-10-18, `7.2.11-psx-ps4-baikal-dev1` passed one core boot on a PS4 Slim
with a Baikal B1 southbridge and Orbis firmware 12.02. The experiment retained
the existing loader, initramfs, firmware, bootargs, 1024 MiB VRAM setting and
partitioned USB root, replacing only the kernel and installing its complete
matching module tree. All 41 installed module hashes matched the build. The
previous native-dev1 boot set and modules remain the rollback. Both configs
build amdgpu and mt76x8 into the kernel, so their firmware is supplied by the
unchanged initramfs.

| Tested artifact | SHA256 |
| --- | --- |
| Kernel | `c9c0e1aacd30679f7e496c31a2170bbd4e3591b297ade8804068832af881b194` |
| Complete module archive | `e384a5efd1e6bd3644a98dc36208e0874fe0c587ff7d02787773021ea649b20a` |
| Resolved config | `a245fb892a691c0fd607cc6defb2a7d3108e105a2990d587a5f1f5af81eb0eea` |
| Retained initramfs | `089d62daaef9b4ab7a61486d193533deef5f79c7782ecd8ddc10030477b0f460` |

USB root mounted read/write at 11.624 seconds and graphical.target was reached
at 18.246 seconds. The runtime snapshot at 191 seconds showed Hyprland at
1920x1080/60 Hz, AMD hardware OpenGL 4.6 with Mesa 26.1.7, Vulkan device API
1.3.354, Wi-Fi DHCP/default route, gateway ping 3/3 and HTTPS 200 with verified
TLS. No system or user units had failed. `hci0` enumerated and reported firmware
already present; Bluetooth service activation, pairing and audible output were
not tested.

The bounded UART session completed with uninterrupted capture. Its reviewed
record through 222 seconds showed no panic/oops, xHCI death or root I/O fault;
notable warnings also appeared in the 6.18 baseline. Verdict: **PASS for one
core boot**, not acceptance, promotion or long-term stability evidence. Native
Ethernet remains untested in this 61-patch target: it does not carry the later
6.18 native autoload patch. Cold firmware loading also remains untested. The curated
distribution record is EXP-20261018-001 in Omarchy psX's
`platforms/ps4/experiments/SESSIONS.md`; raw UART and runtime receipts remain
private.

### Official builds

On 2026-10-17 `build.sh -t 7.2-ps4-baikal` passed on both supported paths
with the 61-entry series, proc write fix included. Every entry applied with
`git apply`, and both runs recorded the series and patch-set hashes in the
provenance (`4ac75f4e898e…`, `99eb341e71a6…`):

| Host | Compiler | Result |
| --- | --- | --- |
| Arch Linux x86-64, native | GCC 16.2.1 | `PASS: 7.2.11-psx-ps4-baikal-dev1`, 359 distinct warnings |
| macOS ARM64, `scripts/build-kernel-orbstack` | Debian trixie `x86_64-linux-gnu-gcc` 14.2.0 | `PASS: 7.2.11-psx-ps4-baikal-dev1`, 360 distinct warnings |

All warnings are in code the 6.18 series adds: about 306 in the mt76x8 vendor
driver (mostly missing prototypes), 39 in `drivers/ps4`, 10 in `ps4_bridge.c`
and 4 in the x86 PS4 platform code. The one in a port fix is the mt76x8
`kalStrnCpy()` call in `wlanCfgSetChip()`, which copies with the destination
size as the bound, as the `strncpy()` it replaces did. The native run reused a
local clone of the pinned v7.2.11 commit. The artifacts stay local. The
OrbStack candidate identified above passed EXP-20261018-001; the native build
has not been booted.

## The native Ethernet Baikal candidate

`7.2-ps4-baikal-native-dev` preserves the original 61-patch target and appends
the existing `0700-network-mts/0002-net-ps4-mts-native-autoload.patch` without
adaptation. Its bytes and GPL notices are unchanged from the 6.18 target:
`e6e799c9d82618f1c92abfaa801cb49613feb4d4f4f033bdd79e5d5f3a0ee9b3`.
The driver autoloads through its PCI alias, registers `psx0` after the module
becomes live and makes one retained publication on first open. The
[`autoarm=0` and blacklist escape paths](ETHERNET.md) remain available.

| Item | Identity |
| --- | --- |
| Target | [`targets/7.2-ps4-baikal-native-dev.env`](../targets/7.2-ps4-baikal-native-dev.env), release `7.2.11-psx-ps4-baikal-native-dev1` |
| Series | [`platforms/ps4/patches/7.2-ps4-baikal-native-dev/series`](../platforms/ps4/patches/7.2-ps4-baikal-native-dev/series), exactly 62 entries |
| Patch root | `platforms/ps4/patches/7.2-ps4-baikal`; entries resolve through the target's `PATCHES_DIR`, as in the builder |
| Config | [`platforms/ps4/config/7.2-psx-baikal-native-dev.config`](../platforms/ps4/config/7.2-psx-baikal-native-dev.config), base config with only LOCALVERSION changed and the same fragments |
| Provenance | [`provenance/7.2-ps4-baikal-native-dev.json`](../provenance/7.2-ps4-baikal-native-dev.json), measured local artifacts and scoped hardware evidence |

The tested series SHA256 is
`f94de556e26eee2db36c07480cb4ba301f3b032d500adb13cf6a4675426ff4be`;
the ordered patchset is
`ac72dc67aaf232eb43f2116d7003808b43d95b9072d2a6e51225124bb2bf0d91`.
`python3 scripts/check-native72` and `make check` verify the unchanged
61-patch prefix, exact native patch, config delta and target assignments.
The original target, config, series and exporter remain intact.

### Measured local build and EXP-20261018-004

The OrbStack GCC 14.2 build used an isolated prototype based on project commit
`61e2ad8437c6ea00634d1fefc7f7c4fe5ca887b0`. Its new target/config/series and the
reference-clone build option were uncommitted at build time. The target
assignments, config, ordered series and patch bytes are now preserved here;
only stale prototype comments in the target definition were corrected.
No firmware was embedded or bundled. Verification covered all 55 archive
files, all 41 module vermagic/architecture identities, the PCI alias, native
parameter gates and depmod alias.

| Local artifact | SHA256 |
| --- | --- |
| `bzImage` | `e5bdfd965066bac8c0a098ffa351e49b870aff52563e9c74e60e73d1c7cb6868` |
| Whole matching modules archive | `18d3f5f267fc9c4cbe5a65755cf78987669c3a2a9990ab7a39e59c0a6b78ca6a` |
| Resolved config | `7976d507ab702fb88ea4f2c43215ccf2d98d17832f4a39404b963a940122ecec` |
| `psx_mts_net.ko` | `52ca9761a9c9f85f6539c6bb4c5c953c15c28e3cde1f589ac8397a4af66152ed` |

On one PS4 Slim / Baikal B1 / Orbis 12.02, the completed EXP-20261018-004
observed automatic PCI probe, `psx0` registration and one first-open RUNNING
publication. USB root mounted read/write at 11.766 seconds; graphical target
was reached at 17.963 seconds with zero failed system units. Wired DHCP and
the preferred default route worked while Wi-Fi was initially connected.
Interface-bound gateway ping passed 5/5, HTTPS returned 200 with verified TLS,
and independent random 4 MiB memory-only streams passed SHA256 verification
in each direction. RX/TX counters advanced by over 4 MiB each way; errors and
TX drops stayed zero. RX drops increased from 70 to 73 and remain unclassified.

UART continuity completed, with no Linux panic/oops, xHCI death, USB-root I/O
fault or MTS terminal fault in the reviewed evidence. This is one short boot
and traffic result, not acceptance or promotion. Long-duration stability,
cable/admin cycles, cold firmware loading, repeated boots and other consoles
remain untested on this build. CI and other rebuilt binaries require their
own hardware evidence even when the release string matches. The curated
record is EXP-20261018-004 in Omarchy psX's
`platforms/ps4/experiments/SESSIONS.md`; raw evidence remains private.

## Baikal reference audit

rmux's field-tested `baikal/7.0.8-Stable` has 11 commits with no counterpart
in the port's source (listed in [RMUX-IMPORT.md](RMUX-IMPORT.md)). A read-only
review compared each with the port; the findings below that led to changes or
decisions were checked against the source.

| Commit | Result |
| --- | --- |
| `4237de231b41` xHCI IRQ offsets | Already equivalent in the port |
| `7aed26ac6127`, `13ca33b98833` DP training counter | Added and removed again; the port already retrains as the pair ends up |
| `bc0d84377983` ACPI SCI bypass | Present |
| `3a29a5b9f700` x86 PCI INTx skip | Only reachable with `pci=routeirq`; not carried |
| `a90c0d1c0c17` Baikal Kconfig and config | Config only; the port selects Baikal at run time |
| `d98d3e9e0df2` mt76x8 copy | Replaced by port fix `0172` |
| `c9e16f3ad259` Liverpool/Gladius display paths | Not carried; part of the step 4 display decision |
| `c4e7f040d08f` Baikal IOMMU and interrupt remapping | Not carried; see the IOMMU decision below |
| `a827d7936c44` Baikal AHCI, xHCI and SDHCI | Partly present; see below |
| `ad61075172f1` MT7668 Bluetooth on Baikal | Not carried; it changes generic LE and HCI code for every MediaTek SDIO chip |

The review also found a bug outside those commits. `xhci_hcd_pci_shutdown()`
sent every device except Belize to `usb_hcd_pci_shutdown()`, which reads the
PCI driver data as a `struct usb_hcd`, while `xhci-aeolia` stores a
`struct aeolia_xhci` there. On Aeolia and Baikal every reboot or power-off
read past that allocation and could call through a garbage pointer. feeRnt had
fixed it for Baikal, and the 6.18 target carries that fix
(`0800-usb-aeolia/0002`), but bebez had lost it. Two port fixes follow:

- `0173` sends only Aeolia to the generic path, which restores the 6.18
  behaviour on Baikal.
- `0174` shuts down Aeolia's three hosts through the same per-host loop. It
  has not been tested on an Aeolia console and can be dropped on its own.

### Decisions before the first Baikal boot

These apply to `7.2-ps4`. `7.2-ps4-baikal` keeps the 6.18 behaviour: the IOMMU
stays on with the Baikal functions in an identity domain (`1000-iommu/0003`),
and the AHCI and xHCI code is 6.18's.

- **AMD IOMMU on Baikal.** The port means to skip IOMMU detection on Baikal,
  but `amd_iommu_detect()` runs from `arch_mm_preinit()`, before PCI
  enumeration, so `ps4_is_baikal()` (a PCI lookup) is false and the IOMMU is
  detected anyway. The Baikal reference skips detection on every PS4
  (`c4e7f040d08f`), and the 6.18 target uses the IOMMU with its own Baikal
  coherent-DMA patch (`1000-iommu/0003`). The 7.2 tree as built therefore
  runs a combination nobody has tested. Choose one behaviour before boot
  testing; `ps4_is_baikal_early()` exists if the skip is wanted.
- **Baikal SATA PHY.** `ahci.c` skips the Baikal SATA PHY setup when
  `xhci_aeolia` is already bound to the Baikal xHCI, assuming the xHCI driver
  did it, but this `xhci-aeolia` only does that for Belize. With both drivers
  built in, AHCI registers and retries deferred probes first, so the expected
  order still sets up the PHY; a modular AHCI or a different probe order would
  skip it. `a827d7936c44` adds the xHCI-side setup.
- **Rest of `a827d7936c44`.** Its xHCI-attached AHCI host for Baikal and its
  separate AHCI `dma_boundary` change all Sony AHCI devices and need hardware
  evidence first.

## Known mt76x8 vendor issues

A review of the port fixes found these older problems in the MT7668 vendor
driver. They are the same in all three targets, and neither port fixes them:

- `procCfgWrite()` (the `cfg` proc node) subtracts the 8-byte `set_cfg `
  prefix from the copy size and writes the terminator at the wrong offset, so
  every write loses its last 8 bytes. A write shorter than 8 bytes wraps the
  size; `copy_from_user()` then refuses the copy and the write fails with
  `-EFAULT`.
- `wlanCfgParseToFW()` copies `wifi.cfg` keys and values into 32-byte command
  fields using their own lengths, without a bound check.
- Reported but not yet checked: `batchConvertResult()` and
  `priv_driver_get_cfg()` can write past the caller's buffer on the AOSP
  private-ioctl path. On 7.2 that path appears unreachable, because the
  driver sets only `ndo_do_ioctl`.
