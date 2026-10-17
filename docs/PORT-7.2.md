# Linux 7.2 port

Working record for moving the PS4 kernel to Linux 7.2.y. Upstream base:
`gregkh/linux` v7.2.11 at `a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea`
(current stable on 2026-10-17; 7.2.y will reach end of life a few weeks after
7.3 is released).

The port starts from the [rmux 7.1.7 import](RMUX-IMPORT.md): rmux's
Aeolia/Belize stack with its Baikal unification commits. It proceeds one
variable at a time:

1. Rebase the 170 imported patches onto v7.2.11 and make them compile, with
   no curation.
2. Drop the Strawberry banner and the Mesa lock endpoint.
3. Add psX MTS2 Ethernet.
4. Boot-test the display path. rmux's `ps4_bridge.c` is the first candidate,
   and the hardware-tested psX 6.18 display patches are the alternate.

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
port does not use it. The 6.18 target still carries it; this is worth a
separate fix there.

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
curated branch gets a clean build.

The image has not been booted. Compilation is not hardware acceptance.
