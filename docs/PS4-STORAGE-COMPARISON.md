# PS4 storage: SATA, USB, HDD and SSD

Updated 2026-11-06. These comparisons distinguish manufacturer ratings,
interface ceilings and observed console results. They do not qualify a new
drive, installation method or kernel build.

## Representative drives

Peak sequential rates below use **MB/s = 1,000,000 bytes per second**.
They come from the linked manufacturer specifications, not our PS4 tests.
The models illustrate the storage classes; check the exact model and capacity
when comparing a different drive.

| Storage class / example | Rated read / write, MB/s | PS4 context |
| --- | ---: | --- |
| High-end SATA SSD: [Samsung 870 EVO](https://download.semiconductor.samsung.com/resources/data-sheet/Samsung_SSD_870_EVO_Data_Sheet_Rev1.1_230509_10129500053000.pdf) | **560 / 530** | Current SATA test profile is capped at 1.5 Gb/s; approaching the drive rating needs a validated 6 Gb/s path. |
| Fast HDD in the 2.5-inch form factor: [Seagate BarraCuda Pro, 7,200 RPM, 7 mm](https://www.seagate.com/www-content/datasheets/pdfs/barracuda-pro-2-5DS1966-1-1802US-en_US.pdf) | **Up to 160** data transfer | Current SATA link ceiling is 150 MB/s. Disk position and seeks affect the actual rate. |
| Fast external USB SSD: [Samsung T9 4 TB](https://download.semiconductor.samsung.com/resources/data-sheet/samsung_portable_ssd_t9_datasheet_rev1_0.pdf) | **2,000 / 2,000** | Its rating requires a 20 Gb/s host. PS4 USB is 5 Gb/s. |
| 128 GB USB flash example: [original Kingston Kyson, DTKN/128GB](https://www.kingston.com/datasheets/dtkn_en.pdf?_=v1) | **200 / 60** | Model-specific limits; these numbers do not describe every 128 GB or USB 3.2 stick, or the newer Kyson G2. |

The 870 EVO write peak uses its TurboWrite cache; the manufacturer's sustained
rate after that cache differs by capacity. Likewise, a flash drive's advertised
sequential peak does not describe its small random writes or a long transfer.
Kingston explains the distinction between USB signalling rate and the flash
drive's own performance in its [Kyson support notes](https://www.kingston.com/unitedkingdom/en/support/technical/products/dtkn).

## Interface limits

| Interface | Signalling rate | Ceiling after 8b/10b encoding, before protocol overhead |
| --- | ---: | ---: |
| SATA, current test profile | 1.5 Gb/s | **150 MB/s** |
| SATA | 3 Gb/s | **300 MB/s** |
| SATA | 6 Gb/s | **600 MB/s** |
| PS4 SuperSpeed USB | 5 Gb/s | **500 MB/s** |

The SATA ceilings follow the [SATA-IO transfer-rate explanation](https://sata-io.org/sites/default/files/documents/SATA-Revision-3.0-FAQ-FINAL.pdf).
Sony lists SuperSpeed USB 3.1 Gen 1 for the
[PS4 Slim](https://www.playstation.com/content/dam/global_pdc/en/corporate/support/manuals/ps4-docs/cuh-2202ab-cuh-2208ab-cuh-2216a/CUH-22XXAB_PS4_SG%24en.pdf);
USB-IF records the [USB signalling rates](https://www.usb.org/sites/default/files/usb_3_2_language_product_and_packaging_guidelines_final.pdf)
and [SuperSpeed encoding](https://www.usb.org/sites/default/files/USB_3_0_SuperSpeed_CDR_0p5_whitepaper.pdf).
Protocol, controller, bridge, drive and software overhead reduce usable rates.
An individual device can also negotiate a slower link.

The current SATA profile explicitly uses `libata.force=1:1.5Gbps`. That is a
test setting, not a claim that every Baikal controller or drive tops out there.
An SSD swap alone does not remove it. A faster external USB drive likewise
cannot add 10/20/40 Gb/s capability to the PS4's port.

A 5 Gb/s USB SSD can be rated near this interface's ceiling: for example,
Samsung rates its [T5 EVO](https://download.semiconductor.samsung.com/resources/data-sheet/Samsung_Portable_SSD_T5_EVO_Datasheet_Rev1.0.pdf)
at up to 460 MB/s. This illustrates drive capability on a matching host;
it is not a measured speed or promise for our PS4 USB driver.

## Observed project results

| Record | Device and workload | Observed result | Scope |
| --- | --- | --- | --- |
| [EXP-20261105-019/020](../diagnostics/SESSIONS.md#exp-20261105-019--dev4-internal-sata-enumeration-and-read-only-usb-root-boot) | Owner's Toshiba MQ01ABD050; two direct reads of three 8 MiB regions on dev4 | 48 MiB verified in 2.499 s: **19.2 MiB/s, about 20.1 MB/s** | Repeatability run includes topology/read-only checks and SHA256 hashing; it is not a sustained sequential-throughput benchmark. |
| [Historical hybrid test](../platforms/ps4/docs/INTERNAL-STORAGE.md#current-hybrid-result-2026-10-22) | Internal Toshiba HDD; different build, 256 MiB direct read | **About 62 MB/s** | Separate build and method; not a before/after performance comparison. |
| [Contributor report, issue #5](https://github.com/meerzulee/linux-psx/issues/5) | Crucial CT500BX500 SSD, another Baikal Slim; dev2 + PR #4, custom internal-root mapping, 6 Gb/s link | **306 MB/s direct read; 253 MB/s write** | Contributor observations on different artifacts; short tests, no current-build or endurance qualification. |
| Current diagnostic USB | Owner's 16 GB Transcend flash drive | **Matching benchmark pending** | Negotiated USB rate and a comparable throughput run still need recording. |

The 20.1 MB/s verification result therefore does not establish the HDD's
maximum, a SATA/USB winner, or a driver speed improvement. See the
[installed HDD inventory](../platforms/ps4/docs/INTERNAL-STORAGE.md#owners-installed-hdd-recorded-2026-11-06)
for its model, capacity, geometry and recorded test settings.

## Choosing a layout

| Layout | Useful for | Current position |
| --- | --- | --- |
| Diagnostic USB flash drive | Portable, mostly read-only Diagnostix boot and reports | Keep the existing 16 GB diagnostic media; measure it before assuming its performance. |
| External USB SSD root | Full Linux installation, applications and browser profiles | Preferred development layout; exact bridge, negotiated rate and driver behavior still need testing. |
| External root + internal HDD data | Optional ROM libraries, media, downloads and archives | Plausible future layout after writable internal-storage qualification. |
| Internal Linux root | Convenient boot without USB storage | Contributor evidence exists; a portable installation/recovery workflow remains unfinished. |

Internal Linux data has the same encrypted Orbis filesystem and mapping
requirements as other internal writes. Keeping the root external improves
recovery but does not make those writes qualified. Follow the
[internal-storage record](../platforms/ps4/docs/INTERNAL-STORAGE.md); no
formatting, partition changes or automatic expansion are implied here.

## Next comparable read benchmark

Use one bounded UART experiment on an identified running boot. Record the
kernel/boot-set identities, both device models, negotiated SATA/USB rates,
USB storage driver, request limits and queue settings. Keep the internal disk
and partitions read-only and verify the UVD stop guard before active work.

Measure equal-sized sequential reads with the same request size and cache-bypass
method. Add bounded 4 KiB random reads with the same queue depth and recorded
address span to help assess desktop responsiveness. Time I/O separately from
hashing and metadata checks; record warm-drive-cache effects, offsets, counts
and elapsed time. Compare USB and SATA on that same build before claiming a
speed advantage. Stop on I/O errors, topology changes or the declared timeout.
