# PS4 knowledge migration coverage

The active 55-patch stack, configs, builder and artifact contract were migrated
separately; see [PS4-PATCHES](../../../docs/PS4-PATCHES.md) and their existing
provenance manifests. This handoff brings the useful kernel knowledge forward
from the published PS4 repository at the immutable commit below:

https://github.com/meerzulee/linux-ps4/tree/386df1ad97d92defee1794bf961a0e2f5dc85a1b

[`provenance/knowledge-migration.json`](../../../provenance/knowledge-migration.json)
lists every file in this knowledge audit's scope with its original path,
size, SHA-256, immutable source URL, disposition and current destination. The
scope is all old `docs/`, `checkpoint/docs/`, `experiments/` and `scripts/dev/`
files plus the top-level contribution/status/build records and checkpoint
indexes. It is a coverage inventory, not a claim that every historical sentence
or executable was copied. The old repository remains the immutable archive.

| Earlier material | Current handoff | Disposition |
| --- | --- | --- |
| `ORIGINAL_CONTRIBUTIONS.md`, `CONTRIBUTING.md` | Root [CONTRIBUTING](../../../CONTRIBUTING.md), [CONTRIBUTIONS](../../../docs/CONTRIBUTIONS.md), unchanged contributor/patch provenance | Curated attribution and current workflow |
| `BUILD_LOG.md`, `docs/6.18-PORT.md`, May MSI/ACPI/display reports | [6.18-BRINGUP](6.18-BRINGUP.md) | Evidence ladder retained; intermediate theories remain historical |
| `STATUS.md`, hardware/runtime/capability records | [CAPABILITIES](CAPABILITIES.md) | Scoped dated results and unresolved fan/audio/SATA/power gaps |
| Baikal Ethernet bring-up/handoff/candidate/release documents | [ETHERNET-HISTORY](ETHERNET-HISTORY.md), root [ETHERNET](../../../docs/ETHERNET.md), PS4 status | EXP012–016 results and limits; EXP016 remains DEGRADED |
| `docs/INTERNAL-STORAGE.md` | [INTERNAL-STORAGE](INTERNAL-STORAGE.md) | Layered model and read-only boundary; no installer or keys |
| UART, iteration and failure-analysis study notes | [UART-AND-DEBUGGING](UART-AND-DEBUGGING.md), root hardware protocol | Corrected lessons; no `/dev/mem`/kexec/USB-write runners promoted |
| rmux comparison, provenance and upstream survey | Existing [CREDITS](../../../CREDITS.md), frozen [ledger](../../../provenance/linux-ps4-ledger.md), current bring-up/contribution notes | Exact sources and prior art retained; the old dated comparison is not current runtime status |
| Original portable `netdev/tests/core_test.c` | `tests/fixtures/mts_core_test.c`, run by `tests/mts_core_test.py` | Byte-identical GPL fixture compiled against the active-patch header |
| Historical PHY/packet/single-open module source | Current MTS2 patch and curated research references | Superseded loadable experiments stay outside the active tree |
| `scripts/dev/activate-mts2-public` and its tests | Optional companion distribution adapter | Distro-owned adaptation; no NetworkManager dependency in this kernel |
| Arch packaging / Omarchy readiness plans | Companion distro project and current kernel artifact contract | Historical packaging ideas, not a claimed published AUR package or supported desktop |
| Raw UART/runtime captures, screenshots, checkpoint binaries, payloads, initramfs and firmware | Private/local evidence or old artifact archive | Excluded from this source/knowledge import |

The companion distribution project is:

https://github.com/meerzulee/omarchy-psx

## Corrections carried forward

- The PHY ID is measured; the physical MAC IP lineage is unproven. Historical
  sky2/DWMAC speculation is not a controller identification.
- Native shutdown/suspend's MMD30:0147=`0010` write was established; an explicit
  matching native normal-init zero write was not. The link correction and
  later fresh DMA generation do not isolate one exclusive cause of TX failure.
- Old UART/`keep_bootcon` conclusions changed between 5.4 and 6.x. Preserve
  exact artifact context and observed continuity instead of universalizing
  a failing early profile.
- Jaguar's lack of v3 instructions matters, but the old blanket claim that
  modern Arch universally transitioned to v3 is not a valid diagnosis.
- A locally derived managed bridge repair has documented rmux prior art.
  Broad exclusivity claims in the older contribution essay are not repeated.
- The earlier private firmware-bearing kernel, later firmware-free public
  rebuild and newly named psX build have distinct identities. Hardware
  evidence for one does not qualify another, and EXP016 remains DEGRADED.

## What is complete and what remains

The existing PS4 kernel's active source inputs, tested portable contracts,
contribution lineage and reusable findings now have current psX locations and
pinned historical references. The old snapshot is still needed to inspect
individual superseded iterations, not to build the active target. Raw private
research and binary assets are not a missing source dependency.

No new console test was performed for this migration. Remaining engineering
includes the new firmware-free boot pairing and PS4 hardware gates, unattended
distro activation, cable/lifecycle behavior, fan/audio/storage gaps and the
separate PS5 port. See [UNIVERSAL-KERNEL](../../../docs/UNIVERSAL-KERNEL.md)
for the PS4/PS5 architecture questions; a knowledge migration is not the PS5
implementation.
