# PS4 preparation-only live-development foundation

Revision B, 2026-10-20. Unstaged source candidate; no kernel build or console
qualification. This branch does not replace the installed IB-DATA experiment.

## Purpose and boundary

Keep an owned VMID4 mapping after the existing pre-client SDMA proof and let
normal AMDGPU initialization continue. Expose cached preparation history so
later SSH tools can inspect it without starting work. This makes the owner
and lifecycle state explicit before a future live UVD operation is designed.

The new path never calls the UVD ready helper, requests UVD firmware, releases
the UVD processor, publishes a UVD ring pointer or creates a message. There is
no write interface, workqueue, arm, reset-recovery, rearm or decoder interface.
Preparation success is not observed IB execution or video decoding.

The independent pending IB test remains the next hardware question. No build,
USB install, UART marker or new experiment entry is part of this source work.

## Exact source basis

The baseline is the frozen IB-DATA native source on Omarchynator:

```
/home/developer/Work/psx/lab/uvd-2026-10-20/ib-data/build-preparation/native-01/project/src/7.2-ps4-baikal-uvd-ib-data-dev1
```

`baseline-pins.json` pins the 15 selected files copied read-only from that
tree. The generator checks every pin before writing a new output directory.
The local selected-file baseline is
`lab/uvd-2026-10-20/live-prepare/source/baseline/`. It is not a complete kernel
source tree. A later kernel build must apply the emitted patch to the exact
full baseline, preserve its other patches and use a distinct release identity.

The previous feasibility report is
`lab/uvd-2026-10-19/live-development/runtime-feasibility.json`. The primary's
ownership audit is
`lab/uvd-driver-goal-2026-10-20/LIVE-OWNERSHIP-AUDIT.md`.

There is already one relevant normal-boot control: EXP-20261018-005 completed
the small three-page VMID4 SDMA canary and reached a normal desktop and wired
SSH, with no reviewed VM/SDMA/reset fault. That experiment did not use the
full ready layout, this per-device owner/status interface or this destructor.
Its ledger explicitly leaves warm-reboot DMA quiescence unqualified. This
candidate reuses the mapping proof rather than treating it as new evidence.

## Gate and allocator reservation

`amdgpu.ps4_uvd_live_prepare=1` is a separate default-off `bool` boot parameter
with mode `0444`. It selects only built-in AMDGPU on PS4 Liverpool/Gladius.
It has no mutable runtime counterpart.

The existing `amdgpu_ps4_map_canary_reserve()` hook includes the new gate.
At `amdgpu_vmid_mgr_init()`, GFXHUB(0) ID4's synchronization object is
initialized, its list is made empty, and it is omitted from the allocator's
LRU before client allocation. The newly added reservation callback records
the actual exclusion; merely seeing the parameter does not set
`reservation_seen`.

The selected path rejects `ps4_map_canary` and all six existing UVD probe
switches: ready, poll_state, token, data_control, status_observation and
ib_data. A new Boolean-only helper reports those switches; it does not run a
probe. Conflicts park at the existing failure boundary before allocation or
SDMA publication. The original map-versus-ready conflict remains in place.

This first candidate requires `CONFIG_PM=n` and `CONFIG_DEBUG_FS=y`. The
existing exclusive-init guard still rejects HSA, enabled TTM buffer functions,
working acceleration, reset activity/counts, wrong IP topology and pending
scheduler work. Unsupported configurations park; their guards are not removed
to make the new path run. PCI shutdown remains relevant even with PM disabled.

## Separate state, unchanged mapping transaction

Three independent concepts replace the previous accidental coupling:

1. `ready_layout` chooses the full existing PD/PT/backing/ring/IB geometry.
2. The original `ready_mode` means immediate invocation of the never-returning
   UVD boot probe. It is false for preparation-only boots.
3. `adev->ps4_canary_fences_initialized` records the pre-client fence hardware
   initialization itself, independently of mapping mode or proof outcome.

The pre-client seam still follows scheduler initialization and precedes TTM
GPU entities, KFD and DRM registration. It initializes fence interrupts once,
records that fact, and runs the canary. The outer boot initializer uses this
explicit flag instead of the old `map_canary_completed()` predicate. A repeated
invocation cannot initialize the early fences again. Existing reset/resume
fence initialization is not changed or suppressed by this boot-only flag.

The exact existing layout and proof are retained:

- Private VMID4 base `0x300000000`, the measured two-level VM geometry,
  VRAM PD/PT and all prior range/aperture/non-alias checks.
- Backing `0x308000` bytes, ring page at slot 776, IB `0x20000` bytes at slot
  778, SDMA source/destination/IB at slots 1021–1023; 812 unique mapped pages.
- Every original DMA/PFN/page-ownership check, guard-page gap and PTE validation.
- One direct SDMA copy of 256 bytes, the original packet generation, CPU/HDP
  ordering, fence wait/status, exact source/destination/IB bytes and guard checks.
- One final exclusive-init check before publishing cached preparation success.

Only this new mode takes the successful return before `ready_mode` dispatch.
No UVD descriptor is borrowed, and no UVD helper is called on that branch.
Original copy-only and ready/IB boot diagnostics retain their existing global
owner, atomic one-attempt guard, proof, dispatch and parked-outcome policies.

## Per-device owner and transitions

`struct amdgpu_ps4_live_prepare` is embedded in `adev` and initialized after
the ASIC identity is selected, before IP/VM initialization. The owner and
scalar snapshot are protected by a short spinlock. No BO reservation, ring
lock, reset-domain semaphore or display lock remains held after preparation.

The initial state is DISABLED or SELECTED. Actual allocator exclusion produces
RESERVED. A single atomic-under-lock begin moves RESERVED to PREPARING and
captures the observed generation. A second begin returns `-EALREADY`; the
attempt flag is never cleared. A separate static atomic latch admits only one
preparation attempt per running kernel instance, so a new `adev` created by a driver rebind
cannot repeat the proof either. This latch is separate from the legacy boot
probe latch. Re-initialization invalidates the existing record rather than
resetting the attempt.
The latch cannot detect a physical cold boot; a new kernel resets its BSS even
without a power cycle. Any later hardware plan must state its power-state
precondition separately rather than treating this latch as that evidence.

The allocation is first attached as `pending`. Before promotion to `owner`,
the code requires the same pending pointer, no existing owner, preparation-only
mode, full layout, no ready mode, completed proof, submitted SDMA/fence, 812
mapped pages, early fence initialization, PREPARING state, equal observed and
attempt generations, live allocator and zero reset count. The caller has
already checked successful fence status, exact bytes/guards and exclusivity.

Only that promotion sets `proof_passed` and PREPARED. Failures use the existing
permanent pre-client park and retain `pending` resources; no failure can return
to normal clients with a partially prepared mapping. No normal destructor owns
those parked boot diagnostics. A later driver-init failure after successful
preparation does use the ordinary completed-owner destructor below.

## Cached history, not a current-hardware certificate

`observed_generation` counts calls to the explicit lifecycle invalidation
hooks. It is a software event counter within this device instance, not a
hardware generation, reset count, boot identifier or immutable mapping token.
Initial GART enable precedes preparation and may advance this counter while
the state remains RESERVED. All other lifecycle events, and every event after
the attempt begins, invalidate PREPARING/PREPARED permanently.

Hooks cover:

- GART enable, before its first relevant setup/recovery action and before
  all context PD bases (including ID4) are overwritten;
- GART disable, before context table disable;
- GPU recovery entry and pre-ASIC-reset entry;
- device prepare and suspend, before eviction or `in_suspend` changes;
- hardware teardown entry;
- VMID allocator destruction; and
- repeated device initialization.

Repeated notifications are idempotent for ownership and state: they never
restore PREPARED, repeat SDMA, replace an owner or free anything. They can
advance the event counter and update `last_event`; those are event history,
not additional mapping proofs. Failure and release states remain terminal.

`proof_generation`, completion timestamp, PD address/BO geometry, CRC and the
zero reset counter are historical values captured on successful preparation.
They survive invalidation and release. Cached PREPARED means that this source
path has not observed one of its invalidation events since the proof. It does
not certify the current PD register, translation/coherency, firmware state,
external register writes or the entire hardware lifetime. No reads resample
GPU state, restore the map or repair a stale record.

## Read-only status and reader lifetime

When the new gate is enabled, debugfs adds root-readable mode `0400`:

```
/sys/kernel/debug/dri/<card>/amdgpu_ps4_live_prepare
```

It prints the kernel release, fixed prepare-only interface identity, stage,
error, attempt/retention flags, reservation history, allocator cached state,
early fence accounting, event/proof generations, historical mapping geometry
and copy proof. It explicitly says `observer=cached-only`,
`current_hardware_mapping=not-sampled`, and `arm=absent`.

The snapshot copies scalars under the state lock. It contains no BO, firmware,
MMIO or owner pointer. Show/open/read/seek/release perform no GPU/UVD register
access, PM transition, allocation of GPU memory, firmware load, reset-domain
locking or work submission. `single_open()` may allocate ordinary seq-file
CPU bookkeeping. There is no write callback.

Open takes a DRM-device reference before `single_open`, undoing it on failure;
release drops it after `single_release`. The debugfs removal proxy serializes
open against entry removal. An already-open descriptor retains the scalar
storage across hardware teardown without extending hardware life or retaining
a BO pointer. Reads after debugfs removal may fail through its proxy; the
interface does not promise continued read access after removal. Holding this
reference does not prevent reset or removal and is not described as such.

The publication helper calls the non-sleeping `ktime_get_ns()` to timestamp
the historical proof. The platform's timekeeping implementation may read timer
MMIO. The implementation claims no GPU/UVD register access for cached-owner
bookkeeping, not that every timekeeping operation is free of all MMIO.

## Preparation-only destruction and shutdown

`owner` can be non-null only after a successfully completed SDMA preparation;
it is never the global owner of the old parked ready/token/IB diagnostics.
No operation after promotion submits anything against the private buffers.
The cached interface cannot obtain those pointers or publish work.

Hardware fini first invalidates the record, performs the existing IP hardware
teardown (including GMC table disable), and then calls the preparation-owner
destructor before `ttm_device_clear_dma_mappings()`. TTM objects and mappings
still exist at this seam. It detaches `owner` under the lock, marks RELEASING,
drops the already-completed SDMA fence, frees the eight kernel BOs and page
metadata, then marks RELEASED and `retained=false`. A concurrent second call
finds no owner and performs no second free. Readers remain scalar-only during
release; `retained=true` lasts until the frees finish.

The eligibility boundary is established when ownership is promoted, rather
than returning an error that an unconditional teardown caller would ignore.
This is ordinary destruction of buffers whose only submitted operation has
already completed. It supplies no post-UVD-publication drain or cleanup API.
Future live UVD code must not reuse it without a new lifecycle design.

Post-preparation initialization failure is covered by the existing
`amdgpu_driver_load_kms()` failure → `amdgpu_driver_unload_kms()` →
`amdgpu_device_fini_hw()` path. A successful preparation implies initialized
IP blocks, MMIO and TTM at that point. Destruction is not deferred until the
software-fini path that destroys TTM.

A separate late probe-failure path needs an explicit fix: after
`amdgpu_driver_load_kms()` succeeds, terminal DRM registration, XCP or KFD
errors previously reached `err_pci` without hardware fini. The new gate alone
now takes a bounded ordered cleanup there. Local `ps4_prepare_loaded` and
`ps4_prepare_registered` flags start false and are set only after the respective
successful calls. A terminal post-load error unplugs the DRM device only if
registration succeeded, then calls `amdgpu_driver_unload_kms()` before
`pci_disable_device()`. Load failures leave the first flag false and do not
unload twice. Registration's existing `-EAGAIN` retries remain within the retry
loop, with no intervening cleanup. Default-off and legacy probe error behavior
is unchanged. The three new source blocks have distinct
`LIVE_PREPARE_PROBE_LOADED`, `REGISTERED` and `ERROR` extraction markers.

This cleanup occurs while PCI/devres resources still exist. Adding hardware
fini to final DRM release would be too late: open references can postpone that
release beyond PCI/devres teardown. The final-release path is not changed.

Suspend and reset invalidate and retain the owner; they do not reprepare it.
PCI shutdown calls prepare/suspend without honoring their return values.
This candidate does not attempt to veto shutdown through a return code. It
records invalidation and retains preparation allocations until poweroff.
The selected `CONFIG_PM=n` scope has no supported suspend/resume workflow.
Normal removal of this preparation-only state uses the fini seam described
above; neither successful warm reboot nor teardown has been tested on this
candidate. Existing platform-wide restrictions still apply.

## Review and validation required before any build

The source generator and exact patch replay are necessary identity checks,
not proof of kernel compilation or hardware correctness. Root owns the
separate extracted-source host checker/fixture and independent review.

At minimum, those checks should distinguish: gates off and conflicts; actual
reservation versus mere selection; duplicate begin; pending versus completed
owner; invalidation before/during/after proof; reset-generation mismatch;
read-only snapshots; release exactly once; a query while release is in
progress; no legacy-owner cleanup; full-layout geometry/proof preservation;
no UVD dispatch from the selected branch; both boot fence-init sites; and
late probe errors, retry handling and unplug → unload → PCI-disable order.

Native compilation, compiled control-flow audit, matching modules and a
separate bounded console plan remain future steps. No hardware acceptance is
assigned here. The later hardware question would be whether the full layout
can remain retained while normal desktop/SSH/GPU clients run, with this
candidate performing no UVD setup. A cached status alone cannot establish
that the hardware mapping still matches its historical record.
