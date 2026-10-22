# PS4 SATA UART diagnostics

This experimental observation patch targets the exact source for the running
`7.2.11-psx-ps4-baikal-uvd-runtime-dev1` kernel. `BASELINE.json` identifies its
source archive and the before/after SHA256 of every changed member. It is not
in an active patch series and does not claim hardware acceptance.

Apply the patch only to an isolated extraction of that matching archive, then
build natively on Omarchynator with a distinct kernel release and the whole
matching module set. Preserve the existing kernel and normal boot entry.
The build owner records resolved configuration and artifact hashes separately.

Enable markers with `ps4_sata_diag=1`. The parameter defaults to disabled and
has no writable runtime interface. In a dedicated diagnostic boot entry,
removing `libata.force=1.00:disable` allows observation past the forced-disable
point; retaining it produces a useful limited baseline. The patch itself does
not remove or override that force setting.

Only Sony `104d:90de` at PCI `0000:00:14.7` matches device markers. ATA markers
also require hardware-local port 0, so changing the global `ataN` number does
not redirect them to the optical drive or another controller. The two global
`boot.async_wait` markers intentionally describe the pre-init barrier.

The `PS4_SATA_DIAG` markers cover:

- Driver entry, existing IRQ assignment, embedded AHCI PHY/reset/activation,
  host start and IRQ request, with effective streaming/coherent DMA masks.
- xHCI setup and DMA selection before/after the shared parent's mask changes.
- Initial EH attach IDENTIFY and configure begin/end; the force point,
  link-speed quirk, ACPI and HPA checkpoints; transfer-mode begin/end.
- Internal ATA command opcode/protocol, requested/effective timeout,
  completion/error and elapsed time. No command data buffers are inspected.
- Async port probe, EH wait, SCSI scan and pre-init async barrier begin/end.
- Actual SG mappings after `dma_map_sg`: first 32 mappings, then a separate
  allowance of 16 error snapshots before unmapping, including SCSI timeouts.
  These record opcode/protocol/tag, mapped element count, DMA minimum/maximum,
  any address above 31 bits and any individual segment crossing 2 GiB.
  Zero-length mapped entries are skipped; `nz_elem` records the count used
  for the range, with minimum/maximum zero if all entries are empty.

Routine device markers share a 192-line ceiling plus one limit notice. A
separate 32-line fault channel (`fault_seq=`) records DMA errors, internal
command wait timeouts/error returns and SCSI command timeouts, with its own
limit notice. Exhausting routine markers does not consume the fault allowance.
Both channels use the same boot gate and stable device/port filter. The two
pre-init barrier markers remain independent of both ceilings. DMA error
snapshots can be duplicates when the same command fails through more than one
path. All diagnostic markers are bounded; normal ATA/kernel logging retains
its existing behavior and may continue beyond these limits.

Times are monotonic jiffies converted to milliseconds; UART printk timestamps
provide cross-stage ordering. Printing and SG inspection add overhead, including
bounded printing in spinlocked issue/error paths, and can change race timing.
The patch does not change commands, timeouts, resets, retries, IRQ/PHY setup,
DMA masks, mappings or write policy. Existing normal enumeration can issue
configuration commands and read partition tables; an instrumented boot is not
a guarantee of electrically passive HDD activity or a software write blocker.

Coverage is for initial attachment, not every later IDENTIFY/revalidation or
block request. `above31=1` establishes an out-of-window mapped address for that
command, not that it caused the timeout; `above31=0` does not exclude descriptor,
coherency, IRQ or PHY faults. No new MMIO reads, raw IDENTIFY, model/serial,
disk payload, filesystem metadata or key material are added to the logs.

Use continuous UART and one bounded experiment under
`docs/HARDWARE-TESTING.md`. No live unbind/rebind/reset is appropriate: this
function also owns USB root. A source/build pass is not permission to stage
boot files or run hardware. The experiment owner supplies the exact boot entry,
capture continuity, cutoff, stop conditions and known-good rollback.
