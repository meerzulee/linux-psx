# Dedicated Diagnostix internal loader

The 0.15 candidate keeps the 0.13 pre-arm correction and fixes the 0.14 launch
regression. The 0.14 build embedded the complete zero-filled scatter BSS
(361,208 bytes) in the outer loader, producing a 401,680-byte ELF. GoldHEN's
PayLoader received every byte but its ELF launcher faulted before any loader
marker (EXP036). Version 0.15 embeds only the 21,536-byte non-BSS image, zeroes
the BSS inside the kernel allocation, and restores the four-byte upstream
footer. The outer loader is 41,320 bytes. Slim image plus zeroed BSS plus footer
reconstructs the exact 382,748-byte kexec blob byte-for-byte in the host build.
It distinguishes immediate copy targets from later decompressor workspace and
replaces genuinely colliding source/descriptor pages before arming. The older
0.12 boot is one historical observation, not proof of placement independence.

Version 0.16 changes only the boot directory name. The current app 0.16 profile
targets FW12.02 / Baikal B1 and fixed 1024 MiB
VRAM. It reads only `/data/diagnostix/versions/0.16/boot/`: `vram.txt`,
`bzImage`, `initramfs.cpio.gz` and `bootargs.txt` are all required. The VRAM file
is checked first and accepts only `1024` or `1024\n`; there is no default,
other VRAM selection, USB fallback or legacy-path fallback. Building creates
`loader.elf` and `loader.bin`; it does not deploy them.

Kernel and initramfs staging use native wired `kmem_alloc`, which allocates
physical backing page by page instead of requesting one contiguous extent.
The five-entry ownership ledger retains each original kernel VA, requested
size and rounded extent for exact cleanup. Every native 16 KiB page is checked
at its four 4 KiB translations and must lie wholly in eligible native source
RAM. Source/control backing and final Linux destinations use separate policies.
EXP031 identified an intact owned page at `0x28000` rejected by the inherited
Linux E820 policy. The source policy now additionally permits the observed
native NORMAL holes `[0x8000,0x2c000)` and `[0x400000,0x480000)`; final
Linux destinations still require the unchanged E820 usable-RAM policy.
Low-stub, VRAM, duplicate-page and destination-overlap guards remain enforced.
Native allocation may block internally; no hard cancellation timeout is claimed.

Before arming, the planner validates image, initramfs, auxiliary and descriptor
pages and rejects duplicate physical pages or low-stub overlap. Full kernel
init_size remains a destination RAM/disjoint-initrd check. All staging is
consumed before decompression; only rounded protected-kernel and initramfs
copies are immediate write hazards. Data/descriptor pages conflicting with
those writes get owned16KiB replacements before arming. Aux data is copied to
low storage before terminal writes; setup-only/padding bytes are not live copy
sources. Descriptor replacements are copied only after final next/source links.
Replacement callbacks make one native allocation per page, constrained to
physical256MiB..1.75GiB, with no retries. Their original KVAs remain separately
owned, at most8192pages/128MiB, and all are freed on refusal or stage-only exit.
Stage-only also writes/reads/frees one constrained page even when no conflicts
occur. It never installs the shutdown hook or starts Linux.
Descriptor pages contain at most 682 records each; the complete plan is capped
at 40,000 records. No metadata is written before every input/control page passes
validation. Inputs and metadata remain wired and stable through handoff.

The terminal thunk follows that physical plan after the existing CPU/device
quiesce and replacement page-table switch. It copies the protected kernel to
physical 96 MiB and the complete initramfs to physical 6 GiB, zeroes final page
tails, then enters the kernel. The final kernel workspace and initramfs spans
must fit the existing E820 RAM map. The replacement identity/direct-map tables
cover 16 GiB; this address coverage alone does not grant memory ownership.
The 64-bit Linux ramdisk fields describe the final 6 GiB destination.

Input limits remain kernel 64 MiB, compressed initramfs 512 MiB and bootargs
511 bytes. The kernel/command-line/VRAM reader handles positive partial reads,
signed errors, bounds, unexpected extra bytes and exact cleanup. The initramfs
uses an exact-size read-only private file mapping without prefault or an eager
fallback; its descriptor is closed after a second size check. `MAP_PRIVATE` is
not a snapshot: the verified generation must stay unchanged through `copyin`.

All allocation, copyin/copyinstr, firmware extraction, header checks and plan
validation finish before the shutdown hook and GPU/pstate changes. A failure
returns without requesting shutdown. The bootstrap's permission patches,
retained kexec code and installed syscall are not rolled back. Successful boot
mode retains preparation backing for the terminal handoff; stage-only mode
frees all five preparation buffers and never arms or requests shutdown.

**Hardware status:** EXP026 passed full-size staging using installed 0.11
inputs. EXP032 then completed corrected stage06 against versioned 0.12 inputs:
27,844 records, 41 nodes and all five preparation buffers freed. One boot08
payload reached Linux `7.2.11-psx-ps4-baikal-uvd-runtime-dev6` and `/init`,
with the RAMDISK at `0x180000000..0x19a845fff`. The owner confirmed USB
disconnected. The RAM root mounted read-only on `loop0` at 94.028152 seconds,
UUID `0d652fe4-a2f5-45f1-85c9-7ea26a52d67c`; systemd started at 111.653083
seconds with hostname `psx-diagnostics`. No kernel panic was observed.
Continuity completed with 99,714 UART bytes and zero events. Verdict:
**PASS_KERNEL_RAM_ROOT_MENU_PENDING**. Operator confirmation of the menu and
complete appliance qualification remain pending; one boot is not full support.
Receipt: `output/diagnostix-source-policy-20261107-032/RESULT.json`.

The current boot08 ELF is 172,304 bytes, SHA256
`1d32ee28790eb4ca7ef26e26702af9562b9dcff671e9c87c492c768424e0d9e6`,
built at `/var/tmp/psx-diagnostix-scatter-boot-20261107-08`.
The corrected 460,521,472-byte 0.12 package has SHA256
`996f83d28e5309e9c94afd9a5dd54508c078cb32b13e9e3a2202ea8ea2bcb37e`.
All 23 package member readbacks and complete FTP readbacks of the six versioned
boot files and package passed. The superseded 0.12 package was removed and 0.11
preserved. Receipts: `/dev/shm/diagnostix-fpkg-0.12-sourcefix/BUILD.json` and
`output/diagnostix-source-policy-20261107-032/FTP-STAGE.json`.

Native offsets, GPU operations and firmware extraction retain upstream lineage;
the relocation thunk is an explicitly attributed adaptation. No DDR3 collector
or pool-changing probe is included. Header checks are not cryptographic boot-set
verification; the parent app binds its staged file hashes.

`build.py --loader-tree <clean-pinned-checkout> --output <new-directory>` creates
an independent pinned source extraction, applies the dedicated and scatter
integration patches and declared overlays, runs host checks, builds and records
source/artifact hashes and licenses. `--stage-only` selects preparation/cleanup
without boot; `--yasm` names an existing private yasm binary. The builder rejects
unresolved native relocations and verifies that the raw kexec blob includes its
complete BSS/workspace extent. Native kernel code uses general registers only.

Ten host suites cover actual entry/readers, wired allocation, kernel preparation,
address/layout helpers, the UVD firmware read, stage-only entry/preparation, scatter
planning and the assembly copy loop. ASan/UBSan fixtures use synthetic memory and injected APIs;
they do not invoke console syscalls, MMIO or devices. Independent physical-memory
reconstruction compares outputs and padding, exercises descriptor boundaries,
and rejects source/control conflicts, duplicate/subpage mappings and malformed
sizes. Host assembly checks do not execute privileged CR3/GDT changes or qualify
the complete console handoff.

## UVD firmware from the running kernel

`uvd-firmware.patch` adds `amdgpu/liverpool_uvd.bin` to the loader's firmware
archive. It reads 313,912 bytes at kernel offset `0x8cbff0`, which is inside the
kernel's read-only segment on FW12.00/12.02, and checks CRC32 `a465ba07`. It then
adds the standard 256-byte AMD header, using the same values as the file the driver
was tested with. Any mismatch leaves the file out and the boot continues. A Gladius
console skips this step.

The loader's archive comes first in the initramfs, so a copy of the same name in the
package replaces the loader's. EXP-20261110-008 removed the package's early-boot copy
and passed Video decoding on one Baikal B1 console.

## Earlier candidates (historical)

Build03 adds a single `DIAGNOSTIX_ALLOC_V1` UART record on allocator refusal.
It distinguishes an actual zero allocator return from request/ledger, virtual
alignment/overflow, physical zero/alignment/overflow/limit/direct-map conflict,
and a noncontiguous page translation. It includes the requested/rounded sizes,
actual flags, physical high limit, original VA, first physical translation,
failed page offset/translation and direct-map base. The record precedes freeing
any rejected returned mapping. No limit, retry or allocation policy is relaxed.
These addresses are private debugging evidence, not public release material.
The observed build02 message alone cannot identify which allocator check failed;
it precedes image copy/header validation and any initramfs allocation.

Build04 changes allocation policy after completed EXP-20261107-014 established
that the first 11,273,216-byte image request returned zero from the allocator
under flags `0x101`; no mapping guard caused that failure. It restores the
reference `M_WAITOK` flag, keeps a single call and all guards, and logs begin/end.
It is a source-tested allocation-policy candidate, not a verified fix or a
successful 425 MiB contiguous-initramfs allocation. A future bounded hardware
trial needs its predeclared physical rollback if the OS call does not return.
No payload deployment, automatic retry or hardware action is part of this build.

Build05 changes only the initramfs input mapping after EXP-20261107-017 observed
the first image allocator also return zero under `M_WAITOK`. Previously the
loader had already populated approximately 425 MiB of anonymous initramfs memory
before that image allocation. Lazy file backing tests that input-memory-pressure
hypothesis; it is not a verified allocator fix. All kernel allocator, physical
bounds, header checks, copyin, cleanup and arm code remain byte-identical to
build04. The pinned syscall wrapper uses syscall 477 and moves the fourth
argument from RCX to R10, leaving descriptor and 64-bit file offset in R8/R9.
The bundled headers define `PROT_READ=1`, `MAP_PRIVATE=2` and signed 64-bit
`off_t`. There is no anonymous or prefault flag on the new mapping.

Build06 corrects the direct-map arithmetic after source/binary review found that
OR/AND conversion aliases high physical addresses when the observed direct-map
base is `0xffff840b00000000` (PDP index44). Addition/subtraction now checks the
complete extent, canonical aligned base and overflow. `acpi.h` uses the same
definition. Every returned native allocation must translate correctly through
both its original VA and computed direct-map alias at every16KiB page; failed
alias validation frees the original VA and refuses before arming. A new accepted
allocation record reports its physical/direct addresses for private UART review.

The replacement page hierarchy uses three distinct4KiB pages at physical
`0x1000`, `0x2000`, `0x3000`: PML4, identity PDPT and direct-map PDPT. Its16GiB
address coverage does not grant RAM ownership. Allocations still require the
existing RAM intervals, excluding VRAM4–5GiB, reserved/MMIO spans, low stub and
the complete decompressor workspace. The first loop editing the existing live
page table remains four entries. The replacement alias PDPT starts at the
actual direct-map PDP index; a pre-arm check refuses index overflow or invalid
canonical base. GDT starts at`0x4000`; thunk, its downward stack, boot params and
the full512-byte command-line capacity must fit below`0x8000`. Existing low
physical pointers are passed explicitly without subtracting the direct-map base.
The assembly thunk and native firmware symbol offsets are unchanged.

The fifth fixture walks actual generated entries with an independent x86 page
walker, checks5/8/near10GiB roundtrips at PDP44 and edge PDP indices, compares
allocation intervals with actual `prepare_boot_params` E820 output, and executes
the actual low-data copy against host storage. It includes wrong native aliases,
zero/overflow/cross-reserved ranges and maximal command-line layout. These tests
do not execute page-table installation, MMIO, firmware or console syscalls.
Build06 remains a source-tested addressability correction requiring hardware
qualification; one `M_WAITOK` call may block internally and there is no retry.


Build07 rebases to current upstream `d0f4d4c0bbc9e8b4870300781b352605ed2bc91d`
and changes only the native contiguous-allocation call parameters from build06:
unrounded request size, high `~0` and alignment `1`. This directly tests the
actual upstream policy instead of the earlier 16 KiB-rounded, bounded, aligned
call. The independent physical/RAM guards, lazy input mapping, checked direct-map
arithmetic, replacement page tables and one-attempt/no-reboot-on-failure behavior
remain. All five actual-source sanitizer suites and the native build pass. This
is a source-tested upstream-semantics candidate, not a hardware fix claim.

Build08 changes only the outer retry policy after loader07 returned zero on its
single first-image attempt. Each buffer now receives at most three identical
upstream-shaped calls. A native zero return is retried, a nonzero mapping stops
the retry loop, and all post-allocation validation failures still stop immediately
without another call. Unlike upstream, there is no infinite loop. Every attempt
logs `UPSTREAM_BOUNDED3` and its attempt number; three zero returns preserve the
original failure path. Host fixtures prove three-refusal bounding and success on
the third call with no fourth call, alongside all existing allocator/handoff checks.
