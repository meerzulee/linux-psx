# Finish the current PS4 Baikal candidate

Updated: 2026-11-03. Scope: the current Linux 7.2.11 native dev3 work,
before the separate Linux 7.3 port. This is a plan with explicit gates;
unchecked gates are not hardware results.

## Source and artifact checkpoint

The candidate is `7.2.11-psx-ps4-baikal-native-dev3`, with 73 patches.
Local branch `work/native-dev3-finish-20261103`, source checkpoint
`c8495a51e0bad4851015cd5d34ee61b2fde87e44`, preserves the pending native
fixes on top of merged PR #4 without altering the original working checkout
or pre-existing experiments. All 38 host tests and ordered patch application
pass. The complete native kernel and all 41 matching modules now build, and
runtime/corresponding-source packaging and checksum verification pass. Build
logs and `build-package-receipt.json` are retained under the ignored
`output/finish-current-20261103/` directory. The exact local runtime files and
complete-source archive are in its `checkout/dist/runtime/` and
`checkout/dist/source/` directories. No hardware boot or acceptance follows
from this build.

Finish the complete kernel and whole matching module archive, resolved config,
corresponding source and checksums. Freeze one complete boot set before its
hardware evaluation. Compilation, packaging and a diagnostic screen cannot
substitute for observed tests of those exact artifacts.

## Decisions for this finish pass

- Keep native 48 kHz HDMI research deferred. The working 44.1 kHz output
  profile also plays 48 kHz content through resampling. Native 48 kHz stays
  recorded as unresolved; it is not a blocker for this scoped baseline.
- Treat Wi-Fi and Bluetooth as working on their recorded firmware-equipped
  setups. Do not start another broad investigation. Reopen development for a
  specific reproducible failure with hardware, firmware, kernel and logs.
  Issue #7 remains an untriaged report; defer it pending those details.
- Test ordinary native power-off. Reboot is a separate test and source path.
  Suspend/hibernate remain excluded from this pass.
- Keep the UVD derivative separate from the ordinary native kernel. Its
  retained DMA and power restrictions do not disappear when PR #4 is applied.
- Keep internal-root installation and image growth experimental until their
  storage and mapping gates pass. Preserve the external-root rollback.

These choices change priorities, not the historical evidence or the tested
scope of other console models.

## Ethernet: working and stable in a declared scope

The earlier owner-selected two-boot count establishes native autoload for its
historical patch set. It is preserved in [ETHERNET.md](ETHERNET.md). The
following is the proposed ordinary-use qualification for a new frozen build
on the owner's Baikal console; it does not claim all PS4 revisions.

1. Three cold boots of the same complete set, covering cable connected at
   startup and cable absent until after startup. The normal network manager
   opens the interface; no manual arm tool is used. Exactly one retained
   DMA generation is published per boot.
2. DHCP, routing, interface-bound HTTPS and independently hash-checked
   transfers in both directions pass on every boot. Use at least 1 GiB per
   direction and record actual throughput and CPU cost.
3. One bounded traffic experiment runs for at least 60 minutes, mixing TX,
   RX, simultaneous traffic and idle/resume. Require no corrupted payload,
   terminal driver/kernel fault, stuck queue or unexplained persistent loss.
   Compare loss with a control NIC on the same wired path; do not attribute
   a known RJ45 modification to the driver without isolating it.
4. Five cable unplug/replug cycles and five healthy administrative down/up
   cycles recover carrier, addressing and traffic within a declared timeout.
   They retain the original DMA generation. Declare these ordinary actions
   in the experiment sequence; do not unload, reset, unbind or rearm.
5. After a tested clean Linux power-off and a normal Orbis boot, Orbis
   networking still works. Preserve complete UART continuity and operator
   observations for each coherent experiment.

Passing these gates permits a scoped description such as “working and stable
for ordinary wired networking on the tested Baikal build, polling mode,
MTU 1500.” IRQ mode and offloads are separate performance improvements, not
prerequisites for a stable polling implementation. Unsupported unload/reset/
suspend and other lifecycle limits must remain visible. Do not promise line
rate or broad compatibility beyond the measurements.

## Audio and wireless

The distribution owns the 44.1 kHz PipeWire default. Keep kernel DTO
initialization in the native candidate and verify normal duration/pitch with
that profile. Both 44.1 and 48 kHz source material must sound correct through
the supported output path. Enumeration and zero underrun counters alone do
not establish correct pitch or speed.

There is no verified native 48 kHz fix. Existing experiments already excluded
several GPU/AFMT, codec, period and channel-count explanations. Further work
needs a discriminating native Orbis bridge/HDA reference; do not repeat those
failed variations during this finish pass.

Wireless regressions require the chip/USB or SDIO identity, console model,
kernel/config, supplied firmware, exact operation and relevant logs. Existing
reported pairing, audio and network successes remain credited. Missing firmware
in a rootfs is a provisioning issue to identify before changing a driver.

## Internal HDD root and expansion

[The storage record](../platforms/ps4/docs/INTERNAL-STORAGE.md) distinguishes
the owner's short HDD tests from the contributor's two SSD internal-root boots.
The latter demonstrate feasibility, including one boot with no USB storage;
they do not qualify the stock HDD, an installer or indefinite writes.

The current design preserves the normal Orbis boot entrance:

```text
Orbis -> existing Linux loader -> kernel + initramfs
      -> console's encrypted user volume -> verified image extent map
      -> ext4 Linux root inside a preallocated file
```

The contributor's loader read boot files from `/data/linux/boot` without USB.
That working location is loader- and setup-specific, not a portable default.
Linux's UFS2 view remains read-only. Linux writes only the mapped blocks
reserved for the ext4 image; per-console keys and extent maps stay private.

An installer must establish stable device identity, allocate the image through
Orbis, verify its entire extent map, provision a rescue path and reject holes,
changed mappings and ambiguous targets. A matching first superblock/UUID alone
does not prove that every extent still belongs to the image.
Before each writable internal-root activation, revalidate the complete mapping
against a read-only view of the containing filesystem; fail closed if it
cannot be verified. Installation-time validation alone is insufficient.

“Autoexpand” has two distinct meanings:

- Expanding ext4 to fill already reserved and completely mapped image capacity
  is feasible. For example, a smaller filesystem can grow inside an already
  allocated 64 GiB image after the image and mapping are validated.
- Enlarging the containing file allocates more Orbis filesystem blocks and
  requires a new, fully verified extent map. The existing Linux path cannot
  automatically take the rest of the internal HDD like an external Linux USB
  partition. Do not enable a USB partition-growth routine on the Orbis disk.

Qualify the owner's actual HDD and chosen AHCI completion profile: three
internal-root cold boots, a no-USB boot where supported, sustained sequential
and random I/O, hashes, flush/error handling, clean root shutdown and healthy
Orbis afterward. Begin with read-only mapping checks and disposable data in
the reserved image. A production completion/recovery path and stale-map
handling remain required before general internal-install support.

References: [dm-linear](https://docs.kernel.org/admin-guide/device-mapper/linear.html),
[resize2fs](https://man7.org/linux/man-pages/man8/resize2fs.8.html),
[contributor's exact layout and results](https://github.com/meerzulee/linux-psx/issues/5).

## Power management

For the ordinary native kernel, test three clean power-off cycles with the
guarded contributor patch. Require actual operator-observed power-off,
filesystem sync/unmount evidence, no terminal fault and a healthy subsequent
Linux/Orbis startup. A UART log ending at “Power down” is insufficient.

Review reboot separately before its bounded experiment; a power-off fix is
not a reboot implementation. Keep suspend/hibernate disabled in this scope.
UVD power handling must meet its separate retained-DMA lifecycle requirements.

## UVD: repeatable decoding, then everyday stability

The existing gate in [PS4-UVD-PLAN.md](PS4-UVD-PLAN.md) requires three cold
boots of one frozen kernel/module/Mesa/firmware set. Preserve that count.
Start with the supported Liverpool AVC 8-bit NV12 / 1080p30 scope and one
active session; HEVC, VCE encoding and 4K are separate or unsupported features.

For repeatable experimental decoding, require on each boot:

- Actual forced hardware decoding, with software fallback disabled, and exact
  output comparison for the canonical and native 900-frame fixtures.
- Correct paced picture and sound through the supported audio profile,
  measured playback speed, clean recorded output and no terminal fault.
- A declared longer playback window (initial proposal: 60 minutes), plus
  supported seeking, flush, clip changes and sequential close/reopen behavior.
- Saved actual library/config identities, test receipts, operator observations
  and completed UART. Fence counts alone are not decoded-frame validation.

Everyday/browser stability additionally needs correct handle turnover and
admission: a second decoder or tab must be rejected safely if unsupported,
without permanently closing the valid runtime. Fix the observed exhaustion
and command-rejection path at its source; increasing quotas is not evidence
of a fix. DMA ownership, quiescence and the supported shutdown lifecycle must
be established before relaxing terminal guards or calling the desktop stable.
Do not run currently unsupported teardown/recovery tests until that source
work is ready. [UVD.md](UVD.md) retains the exact present restrictions.

## Remaining source work and diagnostic tooling

Before a broad stability claim, finish the separately tracked ICC transaction
ownership/response-length handling, bounded ICC status processing, and AHCI
IRQ-allocation failure bookkeeping. These are source findings in
`provenance/rmux-audit-2026-11-03.json`, not hardware diagnoses.

The proposed diagnostic image belongs to Omarchy psX at
`docs/PS4-DIAGNOSTIC-IMAGE.md`; reusable kernel probes/results belong here.
It should automate evidence collection and the defined gates, show untested
and deferred cases honestly, and use a UI that can survive GPU-test failure.
Its implementation must not delay completing this kernel's frozen build.

Apply [HARDWARE-TESTING.md](HARDWARE-TESTING.md): one coherent objective per
bounded session, one active hardware experiment, explicit timeout/rollback,
and no automatic retries of an unchanged failure. This plan starts no console
operation or storage write and publishes no artifact.
