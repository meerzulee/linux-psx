# Diagnostix FPKG without USB

RAM alpha1 implementation checkpoint: 2026-11-07; 0.15 booted 2026-11-08. **One USB-disconnected
kernel and RAM-root boot is observed; menu confirmation remains pending.**
EXP032 reached Linux runtime-dev6 and `/init`, with the RAMDISK at
`0x180000000..0x19a845fff`. The RAM root mounted read-only on `loop0` at
94.028152 seconds; systemd started at 111.653083 seconds with hostname
`psx-diagnostics`. Continuity completed with 99,714 UART bytes and zero events;
no kernel panic was observed. Verdict: **PASS_KERNEL_RAM_ROOT_MENU_PENDING**.
This does not qualify graphics, networking, export, UVD, Power or repeated boots.
Existing USB appliance and public artifacts retain their separate status.

## Version 0.15: payload size and transport correction

EXP036 showed the 0.14 stage-only loader arriving completely at GoldHEN and
then faulting in the target process before any loader marker. The 0.14 kexec
build embedded its 361,208-byte zero-filled scatter BSS through `objcopy -O
binary`, growing the outer ELF from 172,304 to 401,680 bytes. Version 0.15
embeds only the 21,536-byte non-BSS image, zeroes the BSS inside the kernel
allocation and restores the four-byte upstream footer. The outer loader is
41,320 bytes; the host build reconstructs the exact 382,748-byte kexec blob
byte-for-byte. The app handoff also no longer requires the reported socket
send buffer to be at least the loader size; the existing complete-write loop
remains the delivery contract. This fixes the launch regression only. The 0.13
planner correction and the 0.14 source/destination policy are retained. Two
0.15 boots are now observed: one direct host send and one FPKG app-driven
**Boot Diagnostix** handoff, both reaching Linux, the RAM root and systemd. See
[the 0.15 boot record](DIAGNOSTIX-0.15-BOOT.md). No repeated-boot, menu,
network, graphics, UVD, export or power acceptance is claimed.

## Version 0.14: phase-aware relocation candidate

The owner installed 0.13 and its native verification passed, but EXP035's
retrospective UART showed a pre-arm `DIAG_SCATTER_CONFLICT` refusal. A source
page at0x85fc000 was outside immediate kernel writes, inside only the later
decompressor workspace. All staging is consumed before that later phase.
The 0.12 success was one placement; the same old overlap policy could refuse
another boot. Do not retry either old loader unchanged as a placement test.

Version0.14 keeps full init_size RAM validation and handles genuine immediate
copy collisions with bounded, independently owned16KiB replacement pages
before arming. Original input/metadata/aux pages pass translation, RAM and
duplicate checks first; metadata replacements are copied after final links.
At most8192 replacement pages (128MiB) are retained; allocation failure stops
with exact cleanup. The terminal assembly instructions remain unchanged.
Stage-only validation additionally writes, reads and frees one constrained
page so the native allocator is exercised even with no actual collisions.
This candidate uses `/data/diagnostix/versions/0.14/boot`; hardware qualification
is pending. Version0.13's userspace feature set below is retained.

## Version 0.13: modes, issue reports and RAM integration fixes

The next candidate adds an explicit Automatic/Manual chooser after network setup,
with Manual selected initially. Automatic runs the six standard bounded checks
and creates a redacted issue attachment. Manual retains individual tests and
**Reports → Create issue report**. The browser lab can create/download the same
ZIP with an explicit click. See [diagnostic modes and reports](DIAGNOSTICS.md#automatic-manual-and-issue-reports).

The RAM packager now adapts the SSH and network helpers' strict release pins to dev6 and
includes the current host-key service with ordering before the controller.
The RAM verifier accepts unrelated systemd automount stacks while continuing
to reject ambiguous root, state or backing mounts. Power startup failures name
their fixed verification stage instead of falling back to a legacy USB-only
message. SSH and Power availability reasons are included in issue reports.
These source corrections do not establish a successful console power cycle.

Version 0.13 reads only `/data/diagnostix/versions/0.13/boot`, with the same required
1024 MiB VRAM file. Its native kernel blob is byte-identical to the boot08 blob
used for the 0.12 boot below; only the dedicated file paths changed in the outer
loader. The kernel, matching modules and read-only root image remain the frozen
runtime-dev6 set. The new userspace/image still needs its own console trial.
Preserve the verified 0.12 folder/package as rollback.

## Verified 0.12 boot baseline

The optional source lives in
[`diagnostics/ps4/fpkg/`](../diagnostics/ps4/fpkg/README.md),
[`diagnostics/ps4/fpkg-loader/`](../diagnostics/ps4/fpkg-loader/README.md) and
[`diagnostics/ps4/ram_boot.py`](../diagnostics/ps4/ram_boot.py). Normal kernel
builds do not require these tools.

- App: **Diagnostix**, title ID `DGNX00001`, version `0.12`.
- Interface: a centered 1080-pixel column with rounded dark cards, teal focus
  and a compact status panel instead of full-width rows. D-pad and left-stick
  up/down navigate with recenter hysteresis; Cross selects and Circle goes back.
- Payload: pinned upstream `d0f4d4c` plus wired noncontiguous staging and a
  validated physical relocation plan. Its terminal thunk copies the kernel to
  96 MiB and the initramfs to 6 GiB only after CPU/device quiesce. The app retains
  one GoldHEN connection at `127.0.0.1:9090` and complete-write transport.
- Native boot directory: `/data/diagnostix/versions/0.12/boot`. All four loader
  inputs are required; `vram.txt` must contain exactly `1024` or `1024\n` and is
  checked before other boot files. There is no USB or legacy-directory fallback.
  The app's owned version namespace preserves `/data/diagnostix/boot` and sibling
  versions.
- Profile: `ps4-baikal-diagnostix-ram-alpha1`, Baikal B1 / firmware 12.02 /
  1024 MiB VRAM; kernel `7.2.11-psx-ps4-baikal-uvd-runtime-dev6`.
- Core boot set: about 435 MiB, below the 1 GiB cap. The 444,785,998-byte
  RAM initramfs carries the existing 1,000 MiB ext4 root, all 41 matching
  modules, current frontends and native-video fixtures.
- The local package includes the owner's supplied firmware. It is private.
- Boot-mode loader ELF: 172,304 bytes, SHA256
  `1d32ee28790eb4ca7ef26e26702af9562b9dcff671e9c87c492c768424e0d9e6`;
  build `/var/tmp/psx-diagnostix-scatter-boot-20261107-08`.
- Corrected 0.12 package: 460,521,472 bytes, SHA256
  `996f83d28e5309e9c94afd9a5dd54508c078cb32b13e9e3a2202ea8ea2bcb37e`;
  `/dev/shm/diagnostix-fpkg-0.12-sourcefix/BUILD.json` records all 23 package
  member readbacks passing. Complete FTP readbacks of all six versioned boot
  files and the package passed; the superseded 0.12 package was removed and
  0.11 preserved. See `output/diagnostix-source-policy-20261107-032/FTP-STAGE.json`.
- Settings, reports and SSH identity last for this boot. Export reports before
  shutdown and verify the displayed SSH fingerprint each boot. Controller
  restarts preserve state and this boot's UVD/Power attempt fences.

## Working Omarchy FPKG comparison

The owner reports that the installed Omarchy FPKG successfully loaded Linux.
Separate historical records support that boot architecture: v0.28 reached the
whole-device USB root and graphics in EXP-20260823-018-A11; v0.32 reached the
partitioned USB root, owner setup and Hyprland in EXP-20261016-002. These are
Omarchy results, not Diagnostix RAM-root acceptance. Their compact contexts and
raw slices remain in the sibling UART repository, and their conclusions are in
`omarchy-psx/platforms/ps4/experiments/SESSIONS.md`.

The accepted launcher pins the official v25 ELF, 320,936 bytes, SHA256
`c813d169ef37e4bee574a5058bc6c0b92564e74ab445ef0846d67fa9d1e5ce65`, from
upstream commit `9acef9fbf79097a2bb39d6c9c17228198bc445cc`. Its bootstrap
initramfs is 1,686,508 bytes for the whole-device profile or 1,686,082 bytes
for the partitioned profile; the root filesystem stays on USB. Diagnostix's
444,785,998-byte initramfs instead carries its RAM root. Large-input preparation
and resource use differ substantially, but the observed refusal of the first
kernel buffer does not prove a large-initramfs limit or general RAM exhaustion.

The saved official allocator instructions and the actual loader08 instructions
agree on the eight native arguments: dereferenced kernel map, original byte
size, flags `0x102`, low zero, high all-ones, alignment one, boundary zero and
memory attribute six. The reviewed FW12.02 native wrapper inserts selector zero
before its internal nine-argument implementation; adding a ninth argument to
the public wrapper is not a supported correction. Native zero can originate
from physical allocation or subsequent virtual-address reservation/map insertion.
Current UART does not identify that internal branch.

**Correction to the shutdown-timing hypothesis:** the accepted v0.28/v0.32
captures show allocation and successful arming before visible service teardown.
The later `linux-omarchy-4.5-internal-v25.bin` Payload Guest run overlapped
teardown and ended in an exFAT shutdown panic; it is a different artifact and
experiment. Its ordering does not establish that shutdown frees memory required
for this allocator. Preserve preparation-before-shutdown until a measured
dependency justifies a change; moving GPU/pstate writes earlier is also unproven.

Live upstream master `741b0ef96780afbb262ea7ea3958bcb3dcd09067` was compared
with the dedicated pin on 2026-11-07. Its changes add FW14.02 offsets, a 2.5 GiB
payload target and documentation; they do not change the FW12.02 allocator path.
The byte comparison used local `objdump`; REA's offline ELF parser was unavailable
because its caller-supplied Python provider was not configured. This tooling
limitation is separate from the observed console refusal.

EXP025 completed that read-only memory report: selector-zero orders 10..12
were empty across both active normal free lists, although smaller blocks
remained. Those unlocked counters are consistent with the failed 689-page
contiguous request; they are not a general memory-exhaustion or ownership proof.

EXP026 changed the allocator to native wired backing that can scatter across
physical pages. The real 11,273,216-byte kernel and 444,917,182-byte
initramfs-plus-firmware requests succeeded, copies and header validation passed,
and the planner produced 27,844 records across 41 descriptor pages. UART recorded
`PASS` and cleanup of all five preparation buffers, with completed continuity.
No shutdown hook, GPU/pstate handoff, reboot or Linux boot ran. The existing
bootstrap allocation/patches/syscall remain outside that cleanup claim.
That test used installed 0.11 files. EXP031 subsequently identified why a later
allocation could still be rejected: initrd offset `0x18e18000` mapped to native
physical page `0x28000`, with intact `0x29000`, `0x2a000` and `0x2b000`
subtranslations. Linux E820 marks that range reserved, while EXP025's native
NORMAL segment owns it as RAM. The allocator and planner had conflated source
backing with destination eligibility.

The correction uses distinct source/control and destination callbacks. Already
owned wired source pages may occupy the observed native holes
`[0x8000,0x2c000)` and `[0x400000,0x480000)` in addition to ordinary Linux
RAM. Final destinations retain the strict unchanged E820 policy. Low handoff
memory, VRAM, duplicate pages and source/destination conflicts remain excluded.

EXP032 first ran corrected stage06, SHA256
`83868a65c4463a72d2e71b22c054f85b4f7c126bb851842889bab7f16c4402f9`,
against versioned 0.12 inputs: 27,844 records, 41 nodes, all five preparation
buffers freed and no arm. One exact 172,304-byte boot08 delivery then reached
Linux and `/init`; the RAM root mounted read-only at 94.028152 seconds with
UUID `0d652fe4-a2f5-45f1-85c9-7ea26a52d67c`, and systemd started at
111.653083 seconds. Session continuity completed at bytes 1405145..1504859,
99,714 bytes and zero events. Operator confirmation of the menu and complete
appliance qualification remain pending. The result and private phase receipts
are in `output/diagnostix-source-policy-20261107-032/RESULT.json` and its
siblings; the concise hardware ledger is
[`diagnostics/SESSIONS.md`](../diagnostics/SESSIONS.md).

## Earlier allocation candidates (historical)

The following paragraphs retain the negative findings and sequence preceding
0.12. Statements about untested allocation and old paths describe those earlier
candidates; the current staging evidence and namespace are above.

Loader04 restores one reference `M_ZERO|M_WAITOK` allocation call, without the
upstream outer retry loop, after EXP014 proved the `M_NOWAIT` call itself
returned zero for the 11 MiB kernel buffer. EXP017 then observed the same zero
return with `M_WAITOK`; changing that flag did not solve allocation. It logs entry and return;
physical ranges, full page continuity, header checks, cleanup and pre-arm order
remain unchanged. **The call may wait internally; an experiment timeout cannot
cancel it.** The approximately 425 MiB initramfs allocation and USB-free boot
remain unqualified. It reads only `/data/diagnostix/boot`, with no other boot-set
fallback.

App `0.07` prepares a new candidate in **one streamed copy-and-hash pass**, then
fsyncs and activates the same verified file identities. It does not reread the
large staged image. Install and Boot reuse the unchanged verified snapshot for
that app launch. A restart verifies a matching installed candidate once; changed
metadata blocks reuse. Selecting enabled **Boot** sends the loader once directly,
without another confirmation page. These changes follow the owner's request
to remove the repeated checks and launch confirmation. Fourteen actual-source
FPKG tests and native compilation passed; this is not boot acceptance.

App `0.05` also starts **Install / Verify** directly. The earlier default-Cancel
Install dialog is removed. Errors show their exact stage on the menu and write
an 8 KiB bounded owned `launcher-status.log` for FTP diagnosis. Packaged read-only
PFS files need regular-file/size/hash checks, without the writable-file unique
link assumption; active, staged and cleanup identities retain that guard. The
`0.04` setup failure prevented loader04 execution; EXP017 subsequently reached
it and observed its first allocation return zero.

EXP016 identified the actual `0.05` preparation failure: the SDK's stat layout
read native allocated blocks as byte size (`22144` instead of `11273216`),
combined link count into mode, and read UID as link count. App `0.06` decodes
the native 120-byte metadata into SDK fields through independent aligned
storage. Serialized native-byte tests reproduce the exact observed error and
verify the correction with ASan/UBSan; native compilation and extracted package
comparisons also pass. The single copy/hash pass and all file checks remain.
EXP017 observed the corrected metadata and successful single-pass preparation
on the PS4. Its payload still failed the first kernel allocation, before the
kernel initramfs allocation or shutdown; USB-free boot remains unqualified.

App `0.07` bundles loader05. Only its initramfs input reader changes: it maps
the exact file length read-only/private without touching its contents, instead
of filling a 425 MiB anonymous copy before the first kernel allocation. The
verified generation must stay unchanged until kernel `copyin` completes;
`MAP_PRIVATE` is not a snapshot. There is no eager-copy fallback. All allocator
flags, address limits, page/span guards and kernel handoff code are byte-identical
to loader04. Four actual-source ASan/UBSan suites, including inaccessible-memory
fixtures proving no early data access, pass. This tests input-memory pressure;
EXP018 reached `sys_kexec`, so the Orbis mapping setup succeeded, but the first
11 MiB kernel allocation still returned zero. Removing the eager input copy
did not fix allocation. Actual file paging during kernel copyin, the larger
contiguous allocation and USB-free boot remain unqualified. Further source-only
work addresses high physical memory and matching boot-map coverage; no new
candidate has been deployed for that work.

Loader06 is built locally with checked arithmetic for native direct-map
addresses, verified translations for each allocated page, and separate
replacement identity/direct-map tables covering 16 GiB. The low handoff layout
fits below `0x8000`; allowed RAM, VRAM, reserved-hole and decompressor bounds
remain enforced. All five actual-source ASan/UBSan suites pass, and independent
source review found no material blocker. App 0.08 bundles the reproducibly rebuilt
loader06. EXP019 uploaded that package with complete FTP readback. A subsequent
operator launch reached loader06, but its first kernel allocation returned zero;
Linux did not boot.
This fixes addressability; it does not prove allocator availability. Firmware
inspection shows that native allocation classes and free lists are separate,
so high RAM in Linux's E820 map alone does not establish eligibility for the
loader's class-0 allocator. The loader06 result used high `0x27f000000`, so the
previous below-4 GiB limit is not the explanation. A bounded memory-pool
observation is needed to investigate the remaining allocator refusal.

Loader07 rebases to current upstream `d0f4d4c` and matches its native allocation
call exactly: unrounded request size, high `~0` and alignment `1`, with one
`M_ZERO|M_WAITOK` call and no outer retry. The independent `0x27f000000`
post-allocation guard, checked mappings and Diagnostix paths remain. This is the
direct upstream-semantics comparison missing from loader06. All five loader
suites and 15 launcher tests passed; the 0.09 package passed all 23 readback
comparisons. EXP020 uploaded it with complete FTP readback. A subsequent
operator launch reached loader07 but the same first allocation returned zero;
Linux did not start. EXP021/022 did not establish a stock comparison: a short
`send()` produced a truncated ELF that faulted, and the complete sample was only
received afterward.

App 0.10 replaces that unsafe single nonblocking send with a blocking
complete-write loop on the same connection. It handles short and interrupted
writes, fails on zero or error returns, and never reconnects or automatically
retries. Actual-source ASan/UBSan tests cover all four paths; all 16 launcher
tests and all 771 diagnostics tests pass. The 0.10 package passed native
compilation, SFO 0.10 verification and all 23 readback comparisons. EXP023
verified its complete FTP upload. Its first app launch hung before Prepare; after
a fresh reboot, Prepare completed and all six installed files passed independent
FTP SHA-256 verification. A later operator Boot selection logged all 37,096
loader bytes written on one connection and reached `sys_kexec`, confirming the
transport fix on hardware. Loader07 then returned zero for the same first
kernel-image allocation; Linux did not start. The transport fix does not address
that separate allocator failure.

A separate SATA/payload audit found no missing component. The loader retains
Baikal's `disableMSI(0xf80a20e0)` SATA handoff. The kernel has built-in AHCI,
SCSI/SD, loop, ext4 and tmpfs support; all five scoped SATA boot arguments are
present exactly once, and `etc/psx-sata-readonly` is in the initramfs. The root
is a RAM-backed loop, not a SATA mount. The `read-only-scoped-probe` inventory
gate runs only after Linux starts, so SATA cannot explain the current pre-handoff
allocator failure. No source change was needed for this audit.

The separate [pool observer](../diagnostics/ps4/fpkg-loader/pool-observer/README.md)
is built locally as a 14,640-byte BIN. It reads fixed normal/special memory-pool
metadata and free-list counters, reports over UART, and leaves Orbis running.
It allocates no boot buffers and requests no boot or Power action. Exact firmware,
model, instruction fingerprints and read bounds are checked first. Both
actual-source host suites, native build and independent read-only review passed.
EXP025 subsequently ran the matching 16,240-byte ELF and completed 253 bounded
reads on the console. Counts are approximate and cannot establish a successful
allocation or stability. The 0.08 installer was uploaded and launched
after EXP018; its loader failed before arming shutdown or changing boot files.

Version `0.03` adds **Clear old files**. Version 0.10 previews recognized
`0.01`/`0.02`/`0.03`/`0.04`/`0.05`/`0.06`/`0.07`/`0.08`/`0.09` cached installers
in native `/data/pkg` (FTP `/user/data/pkg`) and eligible retained boot
generations, shows MiB/GiB totals, and requires confirmation. It preserves active
boot files, the current/future installer, unknown files, aliases and pending
installation/recovery state. Archived generations need this app's verified
current candidate and a matching small manifest/metadata; cleanup does not reread
their large image. A storage error stops deletion and reports the bytes actually
removed. The known old `0.01` installer was removed over FTP at the owner's
request; its verified local recovery copy remains.

## Installation and first test

The retained installed legacy generation and cached package are version 0.11;
that candidate's identical failed boot must not be repeated. EXP027 stopped on
FTP deletion permissions without deleting files. EXP028 removed one obsolete
generation's six files but could not remove its directory. EXP029 completed
removal of six obsolete generations totaling 2,736,585,508 bytes and restored
the Diagnostix parent to mode 0755, preserving active 0.11 and rollback 0.10.
Those are storage-cleanup results, not boot evidence. Receipts and completed
UART-context identities are recorded in the diagnostics session ledger.

The corrected 0.12 package and all six versioned boot files passed complete
FTP readback. The earlier package carrying the rejected source policy was
removed; the corrected package occupies the 0.12 remote filename. This proves
transfer identity, not installation of that replacement package by the operator.
EXP032's direct loader delivery reached Linux, `/init`, the read-only RAM root
and systemd. The session is closed with completed continuity. Menu observation
and complete appliance qualification remain pending.

For the first boot, keep the known-working USB as recovery media but disconnect
it from the console to test the intended USB-free path. Start a bounded UART
experiment before launching. Observe the exact kernel/profile, verified RAM
root and responsive menu first, then network/SSH and report export. Graphics,
UVD and Power need declared checks on this profile. Host tests do not establish
console boot or stability.

## Original design findings

The remaining sections preserve the investigation that preceded this candidate;
proposed changes below describe that design baseline rather than acceptance.

## Proposed boot path

```text
Orbis + GoldHEN
  -> Diagnostix FPKG
  -> verify its kernel, complete RAM initramfs and loader
  -> load those files while Orbis can read internal application storage
  -> Linux starts with its diagnostic filesystem already in RAM
  -> network/SSH setup, Overview and advanced tests
```

The installed app and boot files occupy internal storage, but the running
Linux root need not mount the console's encrypted internal filesystem or
depend on Linux SATA root support. Files inside the Orbis `/app0` mount cannot
simply be used as Linux paths after the handoff: the entire required runtime
must reach Linux through the boot image or another explicit transfer mechanism.

The first candidate should embed a compressed copy of the existing ext4 root
in the CPIO, stream-decompress it into a dedicated bounded RAM mount, verify
it, attach it as a read-only loop device, and reuse the current bounded
writable `/etc`, `/var`, `/tmp`, `/home` and `/root` arrangement. This
can preserve the frozen runtime-dev6 kernel and all 41 matching base modules.
It needs a new explicit RAM profile and initramfs, not a relabelled USB image.

## Measured size and memory limits

A host-only measurement of the firmware-free alpha7 public root gave:

| Item | Result |
| --- | ---: |
| Existing ext4 image | 1,048,576,000 bytes / 1,000 MiB |
| `gzip -n -6` of that image | 378,625,528 bytes / about 361.1 MiB |
| Frozen kernel | 11,273,216 bytes |
| Existing writable-state limit | 512 MiB; a limit, not an immediate allocation |

Root SHA256:
`0a637a9275a7f606359aee0c815514868e50c90578457ceaa9262df6c25c57a6`.
Measured gzip SHA256:
`721f0cc01ef7916393fc2016aa7365583344c50df9eb4dc3e094c4eb20840203`.
Kernel, bootstrap files, locally supplied firmware, FPKG overhead and optional
providers are additional bytes. This is not a measured final package size.
The 1 GiB core boot-set cap remains; optional providers retain separate budgets.

On the Orbis side, the inspected loader keeps a userspace input mapping and
then creates a second, physically contiguous kernel copy of the initramfs.
A roughly 361 MiB compressed root therefore implies roughly 722 MiB for
those two root copies alone, before helpers, firmware, kernel copies and
Orbis/application memory. Enough total memory does not establish that a
contiguous allocation is available.

Linux then needs the resident 1,000 MiB image, kernel and application memory,
actual writable state and temporary decompression allocations. The compressed
input and unpacked output can overlap during startup. Existing `/run` has a
64 MiB limit: it cannot hold the image. Use a separate bounded backing mount,
retain its named file across `switch_root`, and release the compressed input
after verification. Avoid making a second full uncompressed image copy. Memory
tests must budget from observed available RAM and disclose the RAM image's
overhead; these estimates are not hardware acceptance.

The frozen kernel supports gzip/XZ initramfs, ext4, loop and tmpfs. It does
**not** enable SquashFS, OverlayFS or zstd initramfs decompression. A compressed
SquashFS root is a later footprint optimization requiring another matching
kernel/module build and its own evidence. It is not a drop-in change to beta2.

## Loader finding that must be addressed first

The exact local reference is PS4 Linux Loader commit
`9acef9fbf79097a2bb39d6c9c17228198bc445cc`:

- `linux/main-aio.c:157`: reads a file into a userspace mapping.
- `linux/main-aio.c:376`: searches USB before `/data/linux/boot` and
  `/user/system/boot`, separately for each boot filename.
- `linux/ps4-kexec-common/kexec.c:180`: allocates the full initramfs plus
  extracted-firmware space as one contiguous buffer and copies the input.
- `linux/ps4-kexec-common/kernel.c:153`: `kernel_alloc_contig()` uses
  `M_WAITOK` in an unbounded retry loop.

There is no established small hard-coded initramfs-size ceiling in those
inspected paths, but that does not prove large-image support. Passing the
full diagnostic image to this allocator unchanged can wait indefinitely.
The first large-image trial requires bounded allocation/failure handling and
preflight before irreversible handoff. It must stop after failure rather
than retrying an uncertain transition.

A Diagnostix-specific loader profile should use one verified, dedicated boot
directory and avoid per-file fallback into an older USB/Omarchy set. It must
not silently replace Omarchy's active internal boot directory. Existing
loader provenance, author notices and the owner's recorded authorization
remain applicable; frozen accepted loader bytes retain their own identity.

## Required appliance changes

The present `rootfs/ps4-diagnostics/init` requires a FAT32 USB UUID, USB
ancestry, external root/boot receipts and a media-side initramfs hash before
mounting its root. The kernel-only FPKG implementation in Omarchy also does
not embed a diagnostic root. Neither current product already implements this
proposed mode.

The RAM profile needs:

1. A versioned embedded contract binding kernel/config, complete modules,
   root image, frontend and allowed boot arguments. Preserve exact identity
   checks; replace USB-specific proof with explicit RAM-boot proof.
2. A read-only asset backing and a separate bounded volatile state backend.
   Do not manufacture a USB receipt, UUID or FAT32 mount to satisfy checks.
3. RAM-aware report, settings and SSH setup. Keep per-boot UVD attempt fences,
   job state and Power deduplication outside removable service runtime paths;
   they must survive controller restarts. Initially reports/settings and
   SSH host identity can be volatile; display the current fingerprint and
   export reports over the network before shutdown. Persistence requires an
   explicit network/optional-media backend or a separately designed Orbis
   state transfer.
4. Power checks for that exact new profile, preserving verified boot/module
   identity, startup/lifecycle gates, confirmation and immediate dispatch.
   Removing every check to make the buttons work is not the adaptation.
5. Explicit RAM asset contracts for optional Chromium, native-video and
   Wayland providers. Existing provider checks cannot be satisfied by copying
   files to a misleading USB path. Core Firefox/X11 and the existing graphics
   and decode fixtures provide the first functional scope.

These are optional diagnostic dependencies. Normal kernel builds and generic
artifacts must not acquire an FPKG, OpenOrbis, GUI or package-manager dependency.

## Packaging and first acceptance

Reuse the inspected Omarchy FPKG implementation as a credited reference:
source revision `bbff47f25249d32e867ced1a400229cdf5a28737`, under
`platforms/ps4/fpkg/`. Its transactional staging, hash checks, rollback and
single-write loader transport are relevant; its old exact-hash boot profiles
and title identity must remain separate. A standalone Diagnostix builder must
fetch pinned inputs or carry licensed adaptations rather than require an
Omarchy checkout. Native Linux FPKG packaging still needs qualification.

The intended package can carry the launcher, loader, kernel, full RAM image,
fonts, local tests and browser/graphics components. The owner's local build
can use the firmware already supplied for that console. A public FPKG retains
the firmware-provisioning boundary of [the public image kit](DIAGNOSTIX-RELEASE.md);
the current firmware-free template is not a complete bootable package.

Implementation order:

1. Bound and review loader allocation/failure behavior; prepare a distinct
   RAM profile and verify its embedded identities on the host.
2. Generate the complete RAM initramfs and a separate Diagnostix FPKG; audit
   the package contents, full hashes, licenses and absence of owner secrets.
3. Run one bounded console experiment with no USB storage connected. Observe
   loader allocation, exact kernel/root identity, RAM-backed root and a
   responsive menu; keep the existing USB set as rollback.
4. In declared follow-up scopes verify network/SSH and report export, then
   GUI/3D/browser/UVD and immediate power. Add optional providers after their
   RAM contracts pass. A boot once is not stability qualification.

The owner subsequently authorized building this private candidate and copying
it to Orbis `/data` for installation. Public FPKG publication is not implied.
