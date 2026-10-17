# Credits and source lineage

Linux psX starts with existing community work. Importing it into a new
repository does not change its authorship.

## PS4 foundations

| Contributor or project | Role in the imported work | Recorded source |
| --- | --- | --- |
| **Linux kernel contributors** | Upstream kernel and driver foundations. | [Linux 6.18.44](https://github.com/gregkh/linux/tree/1efe5d048a391de3ead2804b2e7f86376c356cc5) |
| **fail0verflow and the PS4 Linux community** | Original PS4 platform and southbridge lineage, subsequently carried through community ports. | [PS4 Linux reference](https://github.com/fail0verflow/ps4-linux/tree/ef83f1c67ae5a57ef6fe2d3d5a6b54a33d64f3ef) |
| **whitehax0r** | Earlier Baikal port reference, credited by the source project; not a new direct import in this phase. | [Baikal reference](https://github.com/whitehax0r/ps4-linux-baikal/tree/c9adfeef300197d8139f352203f1f78fd5161c2e) |
| **DFAUS / feeRnt** | 5.4 Baikal source, MT7668 vendor-tree snapshot, wireless API adaptations and xHCI shutdown work. | [Pinned 5.4 source](https://github.com/feeRnt/ps4-linux-12xx/tree/1fdfbd9a4c5690602893f6d5f65e154c6d9ba711) |
| **crashniels / Niels Westphal** | Initial Linux 6.15 forward-port used to generate subsystem patches, later adapted to 6.18. | [Pinned forward-port](https://github.com/crashniels/linux/tree/b3b6b1e4fe8754482186f1d894a8eda431dbcd05) |
| **rmuxnet** | Wireless fixes and southbridge, MSI, storage, USB, IOMMU and graphics changes used, adapted or consulted as individually recorded. | [Recovered exact comparison snapshot](https://github.com/feeRnt/ps4-linux-12xx/tree/d8cbb8e912f59c352479f1158103e8ce7b6ca8c4) |
| **MediaTek and other per-file copyright holders** | Original wireless driver source. | Notices and dual-license terms remain inside the imported vendor-source patch. |
| **Meerzulee / the Omarchy PS4 maintainers** | Local port adaptations, diagnosis, integration and hardware-backed fixes. | [Exact PS4 import source](https://github.com/meerzulee/linux-ps4/tree/f61663b369b76f1fc7e8cf29dd946a1bfc5b1e75) |

This is a source-lineage summary, not an exhaustive list of every contributor
to Linux or the vendor driver. Patch author fields, sign-offs, copyright notices
and immutable upstream history remain authoritative for individual changes.

## Working locations for historical sources

On 2026-10-16, 14 commits referenced under `rmuxnet/linux` or
`rmuxnet/ps4-linux-12xx` returned 404 at their original API locations. All 14
were also accessible through `feeRnt/ps4-linux-12xx` with exactly the same
commit hashes and original author metadata. Examples:

- [MT7668 API fixes, `ec88cc96`](https://github.com/feeRnt/ps4-linux-12xx/commit/ec88cc9699fd4f35bd4145b8d4ea8b7947b4b738).
- [MT7668 fortified-copy fix, `2a5e7920`](https://github.com/feeRnt/ps4-linux-12xx/commit/2a5e792097d550be08e239da8b0741465746d265).
- [Baikal southbridge/MSI work, `19ec7f4d`](https://github.com/feeRnt/ps4-linux-12xx/commit/19ec7f4d1d9af199ec93ad6be8e635b38f735b86).
- [Bridge-lifetime comparison, `3a8f6997`](https://github.com/feeRnt/ps4-linux-12xx/commit/3a8f6997c56b64321f3ced03886a2dda69835cb6).

The original references remain in the unchanged imported patch headers and
[inherited ledger](provenance/linux-ps4-ledger.md). The current availability
record and exact alternative locations are in
[provenance/upstreams.json](provenance/upstreams.json). A comparison reference
is not automatically a patch import; the inherited ledger explains each use.

## PS5 research references

The researched [ps5-linux patch project](https://github.com/ps5-linux/ps5-linux-patches/tree/753c7936dcb079bfeaaa0bb19dfc495744266500)
is community work led in its recorded history by Andy Nguyen, with rmuxnet and
other contributors. Its loader is a separate
[ps5-linux project](https://github.com/ps5-linux/ps5-linux-loader/tree/64637c177eaed277fb6ccce394cbb29dd047b893).
These are research references in this phase; their code has not been imported
into the PS4 patch snapshot.

## MTS2 Ethernet migration

The original `GPL-2.0-only` Baikal MTS2 implementation by Meerzulee and Linux
psX contributors is imported unchanged from
[linux-ps4 at `386df1ad`](https://github.com/meerzulee/linux-ps4/tree/386df1ad97d92defee1794bf961a0e2f5dc85a1b).
Its portable core header, patch authorship and in-file notices remain intact.
The patch records its MTS1 source ancestry. Hardware analysis and the Linux
MediaTek STAR driver informed the implementation; no Sony firmware or
proprietary driver code is included. See the
[active migration record](provenance/6.18-ps4-baikal.json) for exact hashes.

Build/packaging tools are adapted from that same published source. The earlier
54-patch source lineage above still applies; the Ethernet addition does not
replace or consolidate other contributors' authorship.

## rmux Strawberry 7.x imports

The [rmux imports](docs/RMUX-IMPORT.md) are per-commit `format-patch` exports
from the 2026-08-28 snapshot of rmux's Strawberry kernel, with only
non-kernel paths removed. Each patch keeps its own `From:` author, date,
message and sign-offs. Authors recorded in the imported commits:

| Author as recorded | Imported work |
| --- | --- |
| **rmuxnet**, **rmux**, **rmxunet**, **Armandas Kvietkus** | Most of the 7.x work: forward ports, Baikal migration and unification attempt, ICC/APCIE hardening, LED, fan, buzzer, hwinfo, cpufreq, SMU mailbox, display and Wi-Fi fixes |
| **Niels Westphal (crashniels)** | 6.15 PS4 platform, Aeolia, USB, Ethernet, SDHCI, AHCI, thermal, radeon and amdgpu foundation |
| **feeRnt** | SDIO/Marvell work, MT7668 API fixes, display fixes and the mt76x8 import |
| **M H** | ioremap, Ethernet and APCIE fixes |
| **Regie Vorkman (TheVorkMan)** | DMI string updates |
| **Comfy702** | MT7668 Bluetooth fix for Baikal (Baikal reference only) |
| **Piotr Gorski**, **Peter Jung** | BORE scheduler and dmem cgroup commits carried in the Baikal reference; not platform support |

rmux's README additionally credits whitehax0r for the original 5.4 Baikal
bring-up; Blyadimir, deWaardt, leg (eclipsed.starr) and Package (packagebob)
as core 7.0 Baikal contributors; and the Baikal and general testers it names.
The snapshot README asks that the tree not be used as a kernel base or copied
into a patch repository and presented as one's own work; see
[RMUX-IMPORT.md](docs/RMUX-IMPORT.md) for the quotation and how this import
handles attribution.

## Licensing

The GPLv2 license text is copied from the source repository into [LICENSE](LICENSE).
Existing per-file licenses and notices, including vendor dual-license notices,
remain unchanged. Firmware binaries and boot-loader code are outside this
import. No new authorship or license is asserted for upstream work.
