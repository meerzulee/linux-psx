# rmux Strawberry kernel imports

Imported on 2026-10-17 as source material for moving the Linux psX PS4 port
to Linux 7.2.y and toward one kernel for the Aeolia, Belize and Baikal
southbridges. These series are **not build targets**. They record rmux's 7.x
work exactly, so that the port can be reviewed against it.

## Source

| Item | Location |
| --- | --- |
| Original repository | https://github.com/rmuxnet/linux (HTTP 404 on 2026-10-17; the `rmuxnet` account still exists) |
| Snapshot used | https://github.com/vnkgdshrr/linux-rmuxnet, a fork created and last pushed on 2026-08-28 |
| Same commits, other network member | for example https://github.com/feeRnt/ps4-linux-12xx/commit/853d3557f1834fa4d9dea4e1e5dabaeaa47d19d4 |

The snapshot README calls the tree "Strawberry Kernel". At the snapshot it
listed `zaebiz/7.1.7-Stable` for Aeolia/Belize and `baikal/7.0.8-Stable` for
Baikal Slim/Pro as the stable PS4 branches. It contains this request from the
maintainer, quoted verbatim:

> To contribute to this kernel, fork it and open a pull request.
> Do not base a kernel on this one. Do not copy it to a .patch repo and call it your own work.

The code is Linux kernel code under GPL-2.0; some vendor files carry dual
BSD/GPL notices. On 2026-10-17 the Linux psX owner decided to build on this
work rather than reimplement it. psX reuses and adapts it under GPL-2.0, keeps
every commit's author, date, message and sign-offs, lists those authors in
[CREDITS.md](../CREDITS.md), and says where its targets are based on it.
psX's own changes are recorded separately, for example in
[`provenance/7.2-ps4.json`](../provenance/7.2-ps4.json).

## What was imported

| Import | Upstream base | Source range | Patches | Dropped commits |
| --- | --- | --- | --- | --- |
| [`rmux-7.1.7`](../platforms/ps4/patches/rmux-7.1.7/series) | v7.1.7 `c7ba9d6de43e9d9bd755b1f3c19501a38898c6b6` | `bebez/7.1.7-Try` (244 commits), then the 3 later `zaebiz/7.1.7-Stable` commits | 170 | 77 |
| [`rmux-7.0.8-baikal`](../platforms/ps4/patches/rmux-7.0.8-baikal/series) | v7.0.8 `ec984f1eb8a34b75657f440ebff0dee27c9b850d` | `baikal/7.0.8-Stable` (183 commits, one merge) | 124 | 59 |

`bebez/7.1.7-Try` is `zaebiz/7.1.7-Stable` at `d67b62838b41` plus five rmux
commits (2026-08-08). They add Baikal support to the Aeolia/Belize stack,
restrict the IRQ-domain and IOMMU changes to Baikal, fix Aeolia boot
regressions and remove a Baikal `sky2` ID. It was not listed as a stable
branch. It is the closest existing source for a
single PS4 kernel, so it is the primary input. `baikal/7.0.8-Stable` is the
field-tested Baikal reference.

Not imported: `rmux/baikal/ethernet` (based on mainline v7.0; its last commit
describes the `stmmac` glue as non-working, and psX MTS2 supersedes it),
`aeolia-belize/7.0.8-Stable` and the older `zaebiz` 7.1.3/7.1.6 branches
(superseded), and the arm64 SM8250 phone/tablet branches.

Each commit was exported with `git format-patch` using pinned diff options.
These top-level paths, which are not Linux source, were removed: `.github`,
`build.sh`, `Dockerfile*`, `README*`, `BRANCHES.md`, `docs`,
`extra_firmware` (proprietary firmware blobs), `firmware`, `IMAGES`, root
images, `config`, `configs` and root Git metadata files. In
[`provenance/rmux-import.json`](../provenance/rmux-import.json), every patch
lists the paths it lost, and every commit that produced no patch is listed
with a reason. No generated patch byte was edited.

rmux's full configs and build fragments are copied verbatim to
[`platforms/ps4/config/rmux-reference/`](../platforms/ps4/config/rmux-reference)
for comparison only. The root `config` embeds proprietary firmware through
`CONFIG_EXTRA_FIRMWARE` and points `CONFIG_EXTRA_FIRMWARE_DIR` at the
maintainer's home directory. Never merge those lines into a psX config; no
target may reference these files, and `make check` enforces that.

## Validation

Completed on 2026-10-17 on macOS with Git 2.54:

- `scripts/import-rmux --verify` applies each series with
  `git apply --cached` (no fuzz, as `build.sh` applies patches) to its pinned
  upstream base. Outside the removed paths, the result equals the source
  commit: 168 patches give `853d3557f183`; the next 2 give `590e14c0f523` for
  every file they touch; 124 patches give `d8cbb8e912f5`.
- `scripts/import-rmux --check` regenerates byte-identical patches and
  reference files.
- `make check` verifies the checksums, series order and pins.

No kernel was compiled from these series, and no console was operated.

## Notes for the Linux 7.2 port

The imports stay faithful; these choices belong to the port.

- **Drop:** `init: add strawberry banner` rewrites `linux_banner` and
  therefore `/proc/version`. `drivers/ps4: add PS4 Mesa lock verification
  endpoint` adds `/dev/ps4-mesa-lock`, a build-token gate that makes rmux's
  patched Mesa refuse other kernels; it has no hardware function. The
  liverpool_clk SCLK force driver is added and later removed. The Baikal
  reference also carries the BORE scheduler (added, then reverted) and a dmem
  cgroup extension, which are not platform support.
- **Rework:** `CONFIG_PS4_DMI_SPOOF` fills missing DMI fields at compile time
  with fixed strings, including a constant fake serial and UUID and board name
  "Belize" on every southbridge. A universal kernel needs a runtime console
  check and detected values; see [UNIVERSAL-KERNEL.md](UNIVERSAL-KERNEL.md).
- **Compare before choosing:** the display path (rmux's Belize retry and EDID
  work versus the hardware-tested psX 6.18 patches 0022–0036), and the Baikal
  xHCI, AHCI, IOMMU and MSI paths across bebez, the Baikal reference and psX
  6.18.
- **Carry:** psX MTS2 Ethernet, which has no working rmux equivalent.
- **Baikal-only fixes:** `git range-diff` pairs 159 of the 182 non-merge
  Baikal commits with bebez commits. Of the other 23, these have no bebez
  counterpart and must be checked against bebez's consolidated Baikal commits:
  `4237de231b41` (xHCI INTx IRQ overlap), `7aed26ac6127` (DP training
  give-up), `bc0d84377983` (ACPI SCI bypass), `3a29a5b9f700` (x86 PCI INTx
  skip), `c9e16f3ad259` (Liverpool/Gladius display paths), `c4e7f040d08f`
  (Baikal IOMMU/IR tables), `a90c0d1c0c17` (Baikal selection),
  `a827d7936c44` (Baikal AHCI/xHCI/SDHCI), `d98d3e9e0df2` (MT7668 fortified
  copy), `13ca33b98833` (DP training counter) and `ad61075172f1` (MT7668
  Bluetooth on Baikal).

## Reproduce

```sh
# Shallow fetch of the pinned commits (about 420 MiB), then regenerate.
scripts/import-rmux --repo /path/to/rmux --fetch
scripts/import-rmux --repo /path/to/rmux --verify
scripts/import-rmux --repo /path/to/rmux --check
```

A full clone of the snapshot also works as `--repo` without `--fetch`.

## Independent implementation direction — 2026-11-03

The owner selected the native Baikal stack as the foundation for new Linux psX
implementations. rmux remains a pinned comparison reference. New capabilities
should use upstream interfaces and explicit hardware contracts, with small
separately recorded patches. The existing native baseline still contains
credited inherited adaptations; this direction does not erase that lineage or
claim a clean-room rewrite. Retained source and equivalent prior fixes keep
their provenance. The rmux-based `7.2-ps4` target is for comparison, not the
base to transplant wholesale into native builds.

An independent GPT-6 Astra maximum-reasoning audit and primary source/test
review covered all 294 patch identities and series ordering, nine reference
files, and high-risk resulting code. The two imports touch 352 and 366 paths;
63 Baikal normalized diffs exactly match the primary import. Each `0042`
contains 213,323 added lines, largely inherited MediaTek/vendor source, not
rmux-authored code. This is not exhaustive line-by-line certification of that
vendor baseline. Per-patch inventory, dispositions and remaining findings:
[audit record](../provenance/rmux-audit-2026-11-03.json).

The native dev3 source candidate adds eight focused patches:

- Runtime PS4 PCI/interrupt-remapping defaults with explicit overrides.
- Correct mutex release on APCIE/BPCIE not-ready exits.
- Bounded EMC stabilization and tick waits, keeping the native register setup.
- Balanced connector allocation and AUX registration cleanup, retaining PR #3.
- Explicit unregistered UART slot state before probe cleanup.
- Correct proc configuration prefix/payload accounting and bounded firmware
  command fields in the inherited wireless driver.
- Native modeset audio DTO initialization from the existing psX fix, with
  Liverpool/Gladius and positive-clock guards; native 48 kHz remains open.
- suds-mcduff's Baikal atomic ICC power-off contribution from PR #4, with
  separately recorded maintainer guards for pending replies and forced shutdown.

The wireless patch bounds encoded lengths and copies; the existing parser
still narrows token sizes before that boundary. A 257-byte token can therefore
be treated as one byte. Numeric conversion, oversized proc-write truncation
and partial local-config updates retain their old semantics. A complete parser
replacement needs separate tokenization and transaction tests.

The original imports remain byte-identical. Some small fixes already existed
in the comparison source; their references are recorded without importing its
larger rewrites. CPU tests compile actual patched functions, and affected
kernel objects plus the full MT76x8 subtree compile. No new hardware acceptance
or public binary release follows from this work.

Remaining implementation priorities are ICC transaction ownership and reply
lengths, bounded interrupt processing, reboot and timed-out ICC recovery,
and AHCI IRQ allocation/failure cleanup. The corrected PR #4 final power-off
sender is integrated into the local native candidate; hardware validation remains open.
The comparison target's broad DP-status bypass and early PCI-based IOMMU
detection must not be adopted unchanged. These require separate designs and
tests and independent hardware evidence.

LED/buzzer/thermal controls, CPU frequency, GPU mailbox controls, EDID/hotplug
and Bluetooth quirks remain separate optional capabilities. Branding, Mesa
locking, fabricated DMI identities and scheduler policy do not need replacement
to provide hardware interoperability. Aeolia/Belize and PS5 need independent
device and repeated-boot evidence before support claims.

## Replacing reference implementations

The current finish pass keeps the native Baikal target authoritative and
the rmux-based target as a comparison source. The native lineage still
contains imported community work; choosing that target does not remove all
rmux-derived code or make an independently implemented driver complete.

Replace implementations by subsystem, using the hardware behavior and
mainline Linux interfaces as the contract:

1. Write the required behavior, runtime device gate, ownership/lifetime rules,
   failure behavior and measurable result before changing code.
2. Prefer an adequate upstream implementation. Where a PS4-specific path is
   required, implement the smallest scoped design instead of transplanting a
   larger reference rewrite.
3. Review against the known failure cases and add meaningful CPU/host checks
   where possible; compile and test the complete integrated target.
4. Qualify one hardware variable at a time. Retire a reference implementation
   from the active series only when its replacement meets the declared gate.
5. Record which source was copied, adapted or only consulted, and preserve
   earlier author headers, notices and immutable reference identities. Changing
   names or formatting is not an independent reimplementation.

The dev3 boot policy and focused failure-path changes are first steps, not a
claim that every inherited driver has been rewritten. Next source work is
ICC transaction/reply ownership, bounded status processing and AHCI IRQ error
handling. Display/bridge, firmware transport, wireless and optional controls
need their own designs and evidence; they must not delay finishing the frozen
baseline. Imported patch bytes and historical credits remain preserved when
a later implementation makes them inactive.
