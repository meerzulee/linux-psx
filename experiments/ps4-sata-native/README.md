# Baikal SATA completion candidate, 2026-11-05

This is an isolated experimental patch for the exact
`7.2.11-psx-ps4-baikal-uvd-runtime-dev3` source identified in `BASELINE.json`.
It is not part of an active series. The parent build assigns
`7.2.11-psx-ps4-baikal-uvd-runtime-dev4` and packages the complete matching
modules and corresponding source. This directory does not operate hardware,
stage media, change UVD gates, mount internal storage or publish artifacts.

The frozen patch is `0001-ahci-baikal-scoped-completion.patch`. Its SHA-256 is
`a33c832630a285b4f1c7ccd4fe37b5a8899b93e6c435041b371c9184f89f4023`.
`make_patch.py` reproduces it from the pinned source members and
`ahci-ps4-probe.h`; a changed baseline is rejected. `test_host.py` applies
that patch with zero fuzz, extracts its actual header/fields, and executes
them with instrumented host stubs. The fixture is not a hardware emulator
and does not prove DMA coherency or Linux IRQ/EH concurrency.

## Source findings

1. The actual runtime-dev3 source has **no command-scoped probe**. Its
   `drivers/ata/libahci.c:41-44,1983-2005,2613-2617` contains only the legacy
   `ps4_ahci_poll_ms` timer. `docs/PS4-BOOTARGS.md:98` documents a long
   `ps4_ahci_completion_probe_ms` parameter absent from this artifact. The
   old hybrid profile therefore cannot be recreated by changing bootargs
   on runtime-dev3 alone.

2. The legacy timer is global to all libahci-started ports, runs while idle,
   rearms while frozen, reads port status before taking the port lock, is
   started before port resume, and has no suspend cancellation. The runtime
   writable interval also has inconsistent off/on behavior: once a callback
   exits with the interval zero, raising it does not restart the timer.
   `ahci_port_stop()` calls `timer_delete_sync()` on any non-null private
   data, although `acard-ahci.c:79-84` supplies its own `port_start()` and
   never initializes that timer. These are source/lifetime faults; they
   are not proof of the observed SATA notification failure.

3. The stronger historical scoped patch is local at
   `ps4/experiments/storage/ps4-sata-completion-probe-20261022-01/patch/0001-ata-ahci-ps4-command-scoped-completion-probe.patch`
   relative to the workspace. It correctly delegates completion to libahci
   and handles suspend/stop, but has no actual PCI/port filter, permits both
   timer mechanisms, leaves the enable flag transition outside `ap->lock`,
   and expresses 20 seconds as a tick count. At HZ=100, a requested 5 ms
   interval becomes a 10 ms tick, making that budget approximately 40 seconds.
   Its no-status diagnostic also compares software `qc_active` directly with
   hardware activity bits. In this baseline, `ATA_TAG_INTERNAL` uses software
   bit 32 and hardware bit 0 (`libata-core.c:1569-1572,5168-5176`), so that
   comparison misclassifies internal commands. The new candidate does not
   infer completion from activity bits.

4. The other historical candidate, the sibling `...-20261022-02`, calls
   `ahci_port_intr()` without requiring nonzero port event status and clears
   the host summary after service without checking whether a new port event
   remains. Its normal path can therefore reconcile inactive tags with no
   latched event. This differs from the deliberately conservative candidate
   here and is not silently imported.

5. The original interrupt cause remains unresolved. Native
   `ps4-bpcie.c:675-716` forces a single vector for function 7; embedded AHCI
   selects the shared IRQ in `xhci-aeolia.c:312-316`. Historical completed
   UART sessions showed inactive hardware tags and pending FIS status, and
   polling allowed reads. That supports servicing pending completions; it
   does not uniquely establish dropped MSI edges or justify changing routing.
   Native `xhci-aeolia.c:46-55,434` retains 31-bit DMA for the shared parent;
   no new DMA or PHY change is included.

6. **Unresolved shared-host initialization failure:**
   `xhci-aeolia.c:439-444` logs an embedded-AHCI initialization error but
   continues parent probing. Its AHCI failure labels at lines 367-376 release
   MMIO/private state without clearing `axhci->host`. This deserves a separate
   lifecycle fix. Invalid probe configurations here refuse the probe only;
   they do not deliberately enter that inherited host-error path. The
   diagnostic wrapper must reject the refusal marker rather than count a
   normal boot with no poll as a candidate pass.

## Driver behavior

Only Sony `104d:90de`, PCI `0000:00:14.7`, hardware-local port 0 can obtain
a probe timer. The candidate requires the existing single-host-lock IRQ
configuration and leaves IRQ dispatch, PHY, DMA, UVD and ICC source unchanged.

The boot-only parameter is `libahci.ps4_probe_ms=5`; the long
`libahci.ps4_ahci_completion_probe_ms=5` spelling addresses exactly the same
variable. Both sysfs parameters are read-only and report the same value. Use
one spelling in the command line. Zero is off; 1 through 1000 are allowed.
The legacy timer is removed. Its retained `libahci.ps4_ahci_poll_ms` argument
must be zero. Invalid intervals, a nonzero legacy argument or multi-MSI
refuse probe activation with a clear error while retaining normal host init.

Issue, IRQ progress, callback and lifecycle changes share `ap->lock`.
Issuing a command starts polling; extra NCQ commands do not postpone the next
tick. Only nonzero `PxIS` enters normal `ahci_port_intr()`. Native FIS/error
handling and libata's software-to-hardware tag conversion remain authoritative.
The probe acknowledges only its own host-summary bit after port status is
empty. A new event can race with acknowledgement; this is not claimed to fix
the underlying IRQ mechanism. Outstanding commands continue to be polled.

Normal IRQ completions and probe completions both renew a real 20-second
jiffies deadline. An idle port cancels the timer. Frozen, resetting,
suspended or unloading ports perform no probe MMIO and do not rearm. A
no-progress deadline stops probing; libata's original timeout/error recovery
still owns the command. Subsequent commands do not replenish an expired
budget around a still-stuck command. Suspend disables under the lock before
waiting for the timer; final stop additionally uses `timer_shutdown_sync()`.
Resume cannot re-enable a terminally stopped timer. Ports with custom start
methods and no initialized probe are ignored by the lifecycle helpers.

One initialization line, at most eight tag-retirement lines and four deadline
warnings are emitted per port. A retirement line is not a claim of successful
data integrity or a lost interrupt. Existing libata error logs remain intact.

## First hardware experiment

The primary operator assigns one new `EXP-YYYYMMDD-NNN`, confirms continuous
UART and starts one bounded session before staging/launch. Objective: the new
kernel reaches the existing USB diagnostic root and identifies exactly the
embedded HDD without a timeout or completion invariant failure. Maximum
launch-to-result window: 180 seconds. This is a read-only enumeration test,
not a sustained I/O or internal-root test.

1. Preserve the exact runtime-dev3 recovery set. Build a distinct runtime-dev4
   with all matching modules. Keep the four UVD gates and previous USB/root
   policy unchanged. Use the established 1.5 Gbps and Orbis PHY profile,
   `libahci.ps4_ahci_poll_ms=0` and `libahci.ps4_probe_ms=5`, with internal SATA
   no longer force-disabled. Validate the loader's 511-byte command-line cap.
2. The dedicated initramfs resolves the internal whole disk through the exact
   PCI parent and ATA port, sets it and its partitions block-read-only before
   general userspace, and refuses multiple/missing identities. Do not use a
   remembered `/dev/sdb` name or a `/dev/sd?27` glob. Keep automount and swap
   off for this disk. Normal libata enumeration still issues ATA configuration
   commands; block-read-only is a software data-write guard, not electrical
   passivity or an in-driver command filter.
3. Launch once. Capture release and kernel/module identity, effective probe
   parameters, the scoped-probe init marker, PCI/ATA/block topology, model,
   capacity, logical/physical sectors and read-only state. Record private
   serial identity only in the private report. Check mounts, swap and holders
   to establish that no internal storage layer was activated. Capture ATA
   errors and relevant IRQ counts; do not use shared IRQ counts to attribute
   individual subfunctions.
4. Stop immediately on timeout/EH cascade, `illegal qc_active transition`,
   panic/Oops, storage write attempt, unexpected target, probe-refused marker
   or USB-root loss. Do not retry in this boot, reset/unbind the shared
   function, unload Ethernet or change UVD gates. Use the already-approved
   safe return/power sequence and verify healthy Orbis. Restore the frozen
   SATA-disabled boot set if the experiment fails.
5. Close the same UART session and record one conclusion. A controller/disk
   identity pass permits planning a separate bounded read-integrity test; it
   does not authorize image writes, extent mapping, format or an installer.

## Historical image and extent findings

The old `ps4-sata-completion-probe-20261022-01/init-omarchy-hybrid` selects a
partition using `/dev/sd?27` plus one fixed sector count, enables writes on
it, mounts UFS read-write and then mounts the existing ext4 image read-write.
It is not a validated extent mapper and is unsuitable for the new read-only
first test. Preserve it only as historical source/evidence.

The contributor's separate dm-linear path is summarized in
`platforms/ps4/docs/INTERNAL-STORAGE.md:34-52`. A fixed map and a correct ext4
UUID/superblock do not prove every extent still belongs to that image. This
review found no reusable, complete extent-validation implementation in the
current diagnostic provider. `diagnostics/ps4/runner.py` storage inventory
reads sysfs/mount metadata only and explicitly says it does not validate disk
content or extents. Therefore do not turn that provider into a write test by
copying either historical initramfs.

Any later existing-image experiment needs a fresh, complete map of that exact
file, stable partition/controller identity, image size and logical coverage,
sector alignment, in-partition bounds, no holes/overlap, unchanged ownership,
read-only comparison against the file path and explicit task authority before
allowing writes within the already-reserved image. Orbis must not have moved,
replaced or grown the file since validation. No such map or write permission
is established by this patch or its host tests.

## Source lineage and validation

Upstream Linux/libahci, fail0verflow and the community Baikal work retain their
existing authors, notices and licenses. The 2026-10-22 scoped probe's recorded
author is `psX Linux workspace <psx@localhost>`; this is an adaptation of its
design, not an authorship replacement. Both historical input hashes are in
`BASELINE.json`. The patch/header/fixture are GPL-2.0-only; Python preparation
and test tools retain their explicit MIT notices.

See `VALIDATION.json` for actual checks and artifact hashes. The host fixture
tests identity/range rejection, initialized-timer ownership, software internal
tag 32, NCQ batching, nonzero-event gating, real IRQ progress, newly pending
status, freeze/error handling, suspend/stop callbacks, no revival after stop,
deadline wraparound and bounded logging under ASan/UBSan at three HZ values.
Compilation and synthetic checks establish no SATA hardware acceptance.
