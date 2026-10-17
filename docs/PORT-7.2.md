# Linux 7.2 port

Working record for moving the PS4 kernel to Linux 7.2.y. Upstream base:
`gregkh/linux` v7.2.11 at `a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea`
(current stable on 2026-10-17; 7.2.y will reach end of life a few weeks after
7.3 is released).

There are two development targets. Both compile; neither has booted.

| Target | Stack | Role |
| --- | --- | --- |
| [`7.2-ps4-baikal`](#the-72-ps4-baikal-development-target) | The 6.18-ps4-baikal series (54 patches and MTS2) on 7.2.11 | First Linux 7.2 boot candidate for Baikal. It carries the series that has run on a Baikal console with 6.18, so after a `6.18-ps4-baikal` boot it changes one variable: the kernel version. |
| [`7.2-ps4`](#the-72-ps4-development-target) | rmux's 7.1.7 Strawberry stack on 7.2.11, plus MTS2 | Reference for Aeolia and Belize, and the source of their features for a unified kernel. |

The unified kernel for every southbridge is to be built on the stack that
boots: once `7.2-ps4-baikal` runs on Baikal, rmux's Aeolia and Belize support
moves onto it one feature at a time, each tested as a single hardware variable.

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
port does not use it. The 6.18 and `7.2-ps4-baikal` targets still carry it
(see [Known issues carried from 6.18](#known-issues-carried-from-618)).

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
firmware ([PS4 status](../platforms/ps4/STATUS.md)). The firmware-free
`6.18-ps4-baikal` build of the same series has not been boot-tested. This
target moves the series to 7.2.11, unchanged wherever Linux 7.2 allows, so that
its first boot tests the kernel version rather than rmux's untested Baikal
paths.

| Item | Location |
| --- | --- |
| Target | [`targets/7.2-ps4-baikal.env`](../targets/7.2-ps4-baikal.env), release `7.2.11-psx-ps4-baikal-dev1` |
| Series | [`platforms/ps4/patches/7.2-ps4-baikal/series`](../platforms/ps4/patches/7.2-ps4-baikal/series): the 55 6.18 entries in order, then five port fixes |
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
| Port fixes (new) | 5 | below |

| Port fix | Cause |
| --- | --- |
| `ps4: icc: use strscpy_pad() for the adapter name` | As `7.2-ps4` `0169` |
| `mt76x8: replace strncpy() removed in Linux 7.2` | As `7.2-ps4` `0170` |
| `mt76x8: adapt remain_on_channel to Linux 7.2` | As `7.2-ps4` `0171` |
| `ps4: bpcie: replace pci_msi_create_irq_domain() removed in Linux 7.2` | psX creates the Baikal MSI parent domain with this helper. The driver now applies what the 6.18 helper did (the PCI/MSI domain flags, `IRQCHIP_ONESHOT_SAFE` and `DOMAIN_BUS_PCI_MSI`) and calls `msi_create_irq_domain()`. bpcie supplies its own domain and chip operations, so the helper's defaults never applied. The MSI core functions involved are unchanged from 6.18. |
| `mt76x8: adapt key and station operations to Linux 7.1` | Linux 7.1 passes the key and station operations a `wireless_dev`. psX's mt76x8 already wraps these operations in adapters; they now pass on the `wireless_dev`'s `net_device` and refuse one without a `net_device`, which cfg80211 never passed before 7.1. rmux's newer mt76x8 handles this itself. |

The other `7.2-ps4` port fixes are not needed: this stack already has feeRnt's
Baikal xHCI shutdown fix (`0800-usb-aeolia/0002`), and its own `gl_proc.c`
change (`0500-network-mt7668/0006`) builds with GCC 16.

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

### Known issues carried from 6.18

- `0500-network-mt7668/0006` makes the proc write handlers use
  `__copy_from_user()`, which skips `access_ok()`, and makes
  `procDriverCmdWrite()` copy 2999 bytes whatever the write size. The nodes
  are root-only (`0664`). It stays unchanged for the first boot; `7.2-ps4`
  port fix `0172` is the candidate replacement afterwards.
- On Aeolia, `xhci_hcd_pci_shutdown()` passes the driver's
  `struct aeolia_xhci` to `usb_hcd_pci_shutdown()`, which expects a
  `struct usb_hcd`. `7.2-ps4` fixes it with `0174`. Baikal is not affected.
- The [mt76x8 vendor issues](#known-mt76x8-vendor-issues) below.

### First boot

Boot `6.18-ps4-baikal` first: it checks the firmware-free configuration on the
kernel version that has run. Then boot this target with the same loader,
initramfs, firmware and root filesystem, replacing only the kernel and its
matching modules. Both configs build amdgpu and mt76x8 into the kernel, so
their firmware must be in the initramfs. The previous boot files are the
rollback.

### Official builds

On 2026-10-17 `build.sh -t 7.2-ps4-baikal` passed on both supported paths.
Every series entry applied with `git apply`, and both runs recorded the series
and patch-set hashes in the provenance (`e1dfa3388176…`, `05fee65c046f…`):

| Host | Compiler | Result |
| --- | --- | --- |
| Arch Linux x86-64, native | GCC 16.2.1 | `PASS: 7.2.11-psx-ps4-baikal-dev1`, 359 distinct warnings |
| macOS ARM64, `scripts/build-kernel-orbstack` | Debian trixie `x86_64-linux-gnu-gcc` 14.2.0 | `PASS: 7.2.11-psx-ps4-baikal-dev1`, 360 distinct warnings |

All warnings are in code the 6.18 series adds: about 306 in the mt76x8 vendor
driver (mostly missing prototypes), 39 in `drivers/ps4`, 10 in `ps4_bridge.c`
and 4 in the x86 PS4 platform code. The one in a port fix is the mt76x8
`kalStrnCpy()` call in `wlanCfgSetChip()`, which copies with the destination
size as the bound, as the `strncpy()` it replaces did. The native run reused a
local clone of the pinned v7.2.11 commit. The artifacts stay local and have
not been booted.

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
driver. All three targets carry the same code, and neither port changes it:

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
