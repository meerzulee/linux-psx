# Contribution records and attribution

Linux psX carries a community kernel port. The Linux contributors, original
PS4 platform authors, downstream wireless copyright holders and subsequent
forward-port authors retain their authorship. See [CREDITS](../CREDITS.md),
the unchanged patch notices, [PS4-PATCHES](PS4-PATCHES.md) and the
[inherited source ledger](../provenance/linux-ps4-ledger.md) for the complete
distinction between imported, adapted and comparison-only work.

This document curates the old project's `ORIGINAL_CONTRIBUTIONS.md`; it does
not reproduce broad “found nowhere else” claims or turn a locally derived
adaptation into an exclusive invention. Research chronology supports a
development history, while attribution still depends on source and prior art.

| Work recorded locally | Evidence and scope |
| --- | --- |
| PS4 IRQ9/ACPI SCI adaptations | Local diagnostic work recorded at [`e01ef218`](https://github.com/meerzulee/linux-ps4/commit/e01ef2188818ba511120f7f6b2d7e86a9eebacd0); addresses missing SCI descriptor and the tested SCI request path |
| Liverpool/Gladius DP transmitter preservation | Local v58–v60 measurements and [`f5aea5d4`](https://github.com/meerzulee/linux-ps4/commit/f5aea5d4d442d3227039d5bd0e7acb05aa4b01c7) milestone; preserves firmware-trained lane state by skipping the destructive TX disable/enable operations |
| Display diagnostics and staged bridge transactions | Per-step lane/ATOM/CQ investigation, then pointer-safe `0035` diagnostics and `0036` transaction boundaries for the 6.18 API path; summarized in [6.18-BRINGUP](../platforms/ps4/docs/6.18-BRINGUP.md) |
| Managed DRM bridge lifetime (`0034`) | Independently derived locally after the 6.18 static-object kref failure; rmuxnet's earlier [`3a8f6997`](https://github.com/feeRnt/ps4-linux-12xx/commit/3a8f6997c56b64321f3ced03886a2dda69835cb6) already implements the same core managed-lifetime pattern and is credited as prior art |
| Baikal native MTS Ethernet | Original GPL-2.0-only module/core by Meerzulee and Linux psX contributors, informed by native hardware observations and the Linux MediaTek STAR reference; firmware/proprietary driver code is excluded |
| Patch-based build, checks and packaging | Local tooling adapted across the old PS4 and new psX repositories; exact active import source and adaptations are recorded in [`6.18-ps4-baikal.json`](../provenance/6.18-ps4-baikal.json) |
| Portable MTS host contracts | Original GPL test copied byte-for-byte from the published source; the new Python wrapper extracts the header from the active patch and compiles/runs those contracts on the host |

The active aggregate patches include substantial fail0verflow/community,
crashniels, feeRnt/DFAUS, rmuxnet and MediaTek lineage. A generic aggregate
`From` label or this repository's package author is not a replacement for
those contributors' metadata. Preserve file notices, patch author headers,
sign-offs and the precise source commit when refreshing a patch.

The older infrastructure list also described USB/kexec/sky2 research scripts.
Those are historical work, not the new kernel's recommended operational
interface. The new tree retains their discovery context through the
[knowledge migration ledger](../platforms/ps4/docs/KNOWLEDGE-MIGRATION.md)
without promoting the unsafe or superseded procedures. Desktop, installer,
initramfs and distribution UX contributions belong with `omarchy-psx`.

The kernel and copied C test retain GPL-2.0-only terms; per-file vendor
licenses remain as supplied. New documentation here is covered by this
repository's [LICENSE](../LICENSE). No firmware or third-party license is
reassigned by the repository rename.
