# PS4 UVD live control state (unwired helper)

2026-10-20. Software control state for one future per-device live readiness
operation. Local source and host tests only: no caller, Makefile entry, worker,
debugfs file, module parameter, kernel build, module operation or hardware
action is part of this component. It is separate from the readiness-core
refactor and from the revision 2 fault history.

`P/` means the frozen preparation source
`lab/uvd-2026-10-20/live-prepare/source/attempt-06/candidate/drivers/gpu/drm/amd/amdgpu/`.
The helper takes its preparation types from `P/amdgpu_ps4_live_prepare.h`
(SHA256 `3d18c0f0…d612`), pinned by `check.py`.

## Files

| File | Role |
| --- | --- |
| `amdgpu_ps4_uvd_live_control.h` | Types, verdicts, guard bits and the seven-function API. |
| `amdgpu_ps4_uvd_live_control.c` | Transitions. One leaf irqsave spinlock, one boot latch. |
| `fixture.c` | Host fixture template; `check.py` pastes the frozen helper and preparation header into it. |
| `check.py` | Pins inputs, runs static checks, host modes, coverage and named injected defects. |
| `freeze.py` | Copies the helper and tools into a new attempt directory with a receipt. |

The intended kernel location is `drivers/gpu/drm/amd/amdgpu/`. The helper does
not include `amdgpu.h`, takes no `struct amdgpu_device`, and has no MMIO,
firmware, BO, fence, allocation, clock, work-queue or callback path.

## State model

```text
 UNINITIALIZED --init(no)--> DISABLED                   (terminal for the boot)
 UNINITIALIZED --init(yes)-> IDLE --claim--> CLAIMED --start OK--> RUNNING
                                               |                      |
                                               +------finish---------+--> FINISHED
```

`phase` moves only forward and only through the owner's claim, start and
finish. Separately:

- `used` is set by the one successful claim and is never cleared.
- `touch_entered` is set inside the critical section that grants entry, and
  is never cleared.
- `invalidated` is set by any invalidation and is never cleared. The first
  event and the phase it found are fixed. Later events update `last_event` and
  a saturating count.
- `end` is the first terminal record: COMPLETED, FAILED, CANCELLED, REFUSED or
  INVALIDATED, with its phase, verdict, event and error. Nothing rewrites it.
- `result` is the owner's scalar result, copied once by the accepted finish.
  `result_after_terminal` marks a finish that arrived after an earlier
  terminal record. The result is then explicit evidence, not a rewrite.
- Rejected calls only update `claim_attempts`, `rejected_claims`,
  `rejected_transitions` and `last_rejection`. All counters saturate.

## Admission: `amdgpu_ps4_uvd_live_claim()`

The checks run in exactly this order. The first one that fails is returned.

0. Initialized, using an acquire load before the lock. An uninitialized call
   returns `REJECT_UNINITIALIZED` and records nothing.
1. Under `live->lock`, `claim_attempts` is incremented. This is rejection
   accounting and the source of the ticket.
2. `opted_in` is true. It is fixed by the first init (`REJECT_DISABLED`).
3. `used` is false (`REJECT_USED`).
4. `invalidated` is false (`REJECT_INVALIDATED`). A stale copy of the
   observation that still says PREPARED cannot claim after an invalidation.
5. The observation check, shared with entry:
   1. `selected`, `attempted`, `stage == PS4_LIVE_PREPARED`, `proof_passed`,
      `retained` and `error == 0` (`REJECT_NOT_PREPARED`).
   2. `reservation_seen` and `allocator_alive` (`REJECT_OWNERSHIP`).
   3. `0 < proof_generation < U64_MAX` and
      `observed_generation == proof_generation` (`REJECT_GENERATION`).
   4. `proof_reset_counter == 0` and `gpu_reset_counter == 0`
      (`REJECT_RESET`).
6. The boot latch moves from 0 to 1 with `atomic_cmpxchg`
   (`REJECT_BOOT_USED`).

Only step 6 consumes anything. A rejection in steps 1–5 changes no claim
record and leaves eligibility intact, for example while preparation is still
pending. After a successful claim, nothing restores eligibility or clears
`used` or the latch. That holds through completion, failure, cancellation,
refusal and invalidation.

Producer facts behind step 5 (all in `P/amdgpu_ps4_map_canary.c` unless noted):

- **Generation counter:** every invalidation does `++observed_generation`
  (`:141`). This is a plain u64 increment, so it wraps rather than saturates.
- **The proof generation is never zero:** the initial GART enable records an
  event (`P/gmc_v7_0.c:622`) before the proof run (`P/amdgpu_device.c:2636`).
  Begin copies the proof generation (`:193`).
- **Publish conditions:** publish requires equal generations and
  `gpu_reset_counter == 0` (`:243–244`), then records
  `proof_reset_counter = 0` (`:257`).
- **Generation 0 is impossible:** a PREPARED snapshot with generation 0 cannot
  come from the producer, so it is refused, as the saved-state review already
  required.
- **U64_MAX is refused** even though the producer wraps. That value is
  unreachable in practice. Refusing it also means equal counts could never
  stand for an exhausted count, if the producer were ever changed to
  saturate the way this helper's own counters do.

PREPARED alone is never treated as a live capability. Admission also needs the
distinct opt-in, ownership, freshness, a zero reset count and the boot latch.
Entry then needs guards and the same descriptor again.

## Entry: `amdgpu_ps4_uvd_live_start()`

The checks run in this order, under `live->lock`:

1. `phase == CLAIMED` (`REJECT_PHASE`). Double start, start after finish and
   start without a claim are rejections that change no record.
2. `ticket` equals the claim's ticket (`REJECT_TICKET`, also no record change).
3. `end == NONE` (`REJECT_TERMINAL`). An invalidated or refused claim never
   runs, whatever the new observation says.
4. `guards == PS4_UVD_LIVE_GUARDS_REQUIRED` exactly, meaning DEVICE, RESET and
   FIRMWARE. These are caller assertions that the helper cannot verify.
5. The shared observation check (step 5 above), applied to a fresh copy.
6. The same immutable descriptor as the claim (`REJECT_IDENTITY`): proof
   generation, proof time, PD address, PD/PT/R1/RB/IB sizes, mapped pages and
   copy CRC. `proof_reset_counter` is not compared because step 5 pins it to 0
   both times. Mutable fields (stage, observed generation, allocator, retained,
   error) are checked for freshness instead of identity.

A failure in steps 4–6 is a final refusal. It records `end = REFUSED` with the
verdict and its errno, and the phase stays CLAIMED until finish. On success,
`touch_entered = true` and `phase = RUNNING` are written before the unlock and
the return. Any later lock holder therefore sees the witness before the owner
can perform its first operation.

## Finish and invalidation

**Finish.** `amdgpu_ps4_uvd_live_finish()` is accepted once, from CLAIMED or
RUNNING, with the claim's ticket. It does all of the following in one critical
section:

- Copies the result and sets `result_published`.
- Sets `result_after_terminal` if a terminal was already recorded.
- Records the first terminal only if none exists yet:
  - from CLAIMED: CANCELLED, with the result error or `-ECANCELED`;
  - from RUNNING: FAILED when the error is nonzero, otherwise COMPLETED.
- Moves to FINISHED.

**Invalidation.** `amdgpu_ps4_uvd_live_invalidate()` always records and cannot
veto. If the phase is CLAIMED or RUNNING and no terminal exists, it records
`end = INVALIDATED`, with the event and `-ESTALE`. With a non-NULL `after`, it
returns the state at the same linearization point. Once any invalidation has
happened, start never grants entry again.

Every call is linearized by `live->lock`, so each race has one defined result:

| Order | Result |
| --- | --- |
| invalidate → claim | `REJECT_INVALIDATED`; latch untouched; first event phase IDLE |
| claim → invalidate → start | `REJECT_TERMINAL`; no touch; `end` INVALIDATED at CLAIMED |
| claim → start → invalidate → finish | touch kept; `end` INVALIDATED at RUNNING; result explicit with `result_after_terminal` |
| claim → start → finish → invalidate | `end` COMPLETED/FAILED kept; first event phase FINISHED |
| refused start → invalidate → finish | `end` REFUSED kept; invalidation counted; result explicit |

Invariant: `start == OK` exactly when the invalidation's `after` cut shows
`touch_entered`. The host race test checks this on every round.

## Touch witness

`touch_entered` is conservative. It means "this helper granted hardware entry
to the owner". It is not evidence that an MMIO access was issued or completed,
or that UVD did or did not access memory.

- **If it is false** after an invalidation, this helper never granted entry
  and never will. That still does not certify quiescence. It also does not
  make preparation BOs freeable: the preparation's own destructor premise must
  be established separately.
- **If it is true**, the core must treat the prepared mappings as possibly in
  use by UVD for the rest of the boot.

## Locking and snapshots

- **One lock:** `live->lock` is a leaf spinlock taken with irqsave. Each entry
  point takes it exactly once and releases it before returning; there is no
  return inside the critical section. The helper never sleeps, never takes
  another lock while holding it, and never calls out. The eventual worker must
  not hold it, and cannot, because no API returns with it held.
- **Publication:** `initialized` is published with `smp_store_release` after
  `spin_lock_init` and the state set-up. Every entry point reads it with
  `smp_load_acquire` before touching the lock.
- **Snapshots:** a snapshot copies the whole scalar state under the lock. It is
  a coherent cut of this helper only, and is stale as soon as the lock is
  released.
- **Other state:** the cut is unrelated to the preparation snapshot (its own
  lock), to the fault history (its own lock) and to hardware. Never nest those
  locks with this one. Copy each sequentially and treat a changed preparation
  generation as staleness.
- **Host tests:** host mutex tests do not establish Linux IRQ, preemption or
  memory-model semantics.

## Explicitly not provided

This helper has no function that certifies DMA quiescence, frees, drains or
unmaps anything, allows touched BOs to be released, arms or rearms the IRQ
observer, or vetoes reset, suspend, shutdown or removal. `check.py` fails on
calls of that kind, on certifying identifiers, on callbacks and on any
additional file-scope state.

## Choices for root to confirm

1. **Per-boot latch.** At most one claim per boot across all devices and
   rebinds, which is stricter than per device. This follows the preparation
   precedent (`P/amdgpu_ps4_map_canary.c:90–91,188`).
2. **Refusal is final.** A refused start ends the claim, so there is no retry
   this boot.
3. **Invalidation while RUNNING is the first terminal.** The owner's later
   result is recorded with `result_after_terminal`. The alternative is to
   defer the terminal to finish and keep an overlap flag.
4. **Every forwarded invalidation is one-way.** The core must not forward the
   pre-proof initial GART_ENABLE; it should forward exactly when the
   preparation records an invalidation (`P/amdgpu_ps4_map_canary.c:145–154`).
5. **Guards match exactly.** The set is DEVICE, RESET and FIRMWARE. Adding a
   guard bit, such as an indexed-selector lock, is a deliberate helper change.
6. **Freshness window.** The generation window is 1..U64_MAX-1, and both reset
   counters must be 0.
7. **Errno mapping:**

   | Verdict | errno |
   | --- | --- |
   | Staleness | `-ESTALE` |
   | Once-only | `-EALREADY` |
   | No opt-in | `-EPERM` |
   | Uninitialized | `-ENODEV` |
   | Malformed or protocol misuse | `-EINVAL` |

8. **Result shape.** The result is `{error, code, words[4]}`. The
   readiness-core result needs a mapping, or a shared scalar type.
9. **Events.** Events are `enum ps4_live_prepare_event`. There is no separate
   unplug event: unplug is meant to be excluded by `drm_dev_enter` during
   entry, and teardown is meant to arrive as HW_FINI.

## Integration obligations (future core, not done here)

1. **Storage and init.**
   - Embed one zeroed `struct amdgpu_ps4_uvd_live` per device.
   - Call init next to `amdgpu_ps4_live_prepare_init()`
     (`P/amdgpu_device.c:3894`), before every event source.
   - Embedding it in `struct amdgpu_device` changes the layout, so a full
     rebuild is needed.
2. **Opt-in.** Use a distinct 0444 parameter, read once at init. Do not reuse
   `ps4_uvd_live_prepare`.
3. **Invalidation forwarding.**
   - Forward at the preparation invalidation sites (`P/amdgpu_device.c:4389,
     4597,4659,5309,6019`, `P/amdgpu_ids.c:622`, `P/gmc_v7_0.c:622,756`),
     before the destructive step.
   - Act on the returned cut: a CLAIMED or RUNNING phase means the core must
     drain or contain its owner. The helper cannot.
4. **Request path.**
   - Copy the preparation snapshot and read `gpu_reset_counter` once, then
     claim.
   - On OK, queue exactly one finite work item.
   - Every successful claim needs exactly one finish, including when queueing
     fails.
5. **Worker order.**
   1. `drm_dev_enter`.
   2. Take the reset-domain read side.
   3. Validate the firmware in CPU memory.
   4. Take a fresh observation and call start with all guards.
   5. Only after start returns OK, make the first operation register access,
      reads included: clock preflight and indexed context are part of this.
   6. Call finish on every path.
   7. Unlock, then `drm_dev_exit`.
6. **Touch means retention.**
   - The preparation destructor (`P/amdgpu_device.c:4439`) must keep touched
     resources.
   - The TTM clear in `fini_hw` (`P/amdgpu_device.c:4436–4444`) can still
     unpopulate pinned backing. That problem is unresolved here.
7. **IRQ observer.**
   - Its existing arm is check-then-act and one-time. The core must call it at
     most once, serialized by this claim.
   - Fault-history snapshots before and after the operation belong to the
     core.
8. **Reset, suspend, shutdown and removal after touch.** Stop, drain and
   containment remain unresolved. A restriction on operators is not a veto.
9. **Other serialization.** Indexed-selector serialization
   (`P/amdgpu_ps4_uvd_ready.c:248–263`) stays with the core.
10. **Exporters.**
    - Format fields one by one, because of padding.
    - Label `touch_entered` as "entry granted", not as an MMIO completion.
    - Label invalidations as forwarded events.

## Verification

```text
python3 freeze.py --out <lab>/source/attempt-NN --attempt NN
python3 check.py --source <lab>/source/attempt-NN \
  --prepare lab/uvd-2026-10-20/live-prepare/source/attempt-06 \
  --out <lab>/host-tests/<new> --cc <compiler> \
  --modes optimized,sanitized,thread,coverage [--checkpatch <tree>/scripts/checkpatch.pl]
```

`check.py` does the following:

- **Pins:** checks every hash, including the preparation receipt and header.
- **Static checks:** runs the static checks above, plus a strict helper-only
  compile (`-Wdeclaration-after-statement -Wmissing-prototypes
  -Wstrict-prototypes -Wshadow`).
- **Host fixture:** runs the actual-source fixture in each requested mode,
  which must produce identical PASS output.
- **Coverage:** requires 100% line, region and branch-outcome coverage of the
  helper.
- **Injected defects:** each one must stop at its own named witness. They
  cover once-only, the boot latch, invalidation, the three generation rules,
  the reset pin, terminal stability, refusal finality, touch, used and ticket
  invariants.

The deterministic tests cover:

- every admission branch, entry refusal and terminal;
- each identity field;
- both generation bounds;
- reentry, saturation and errno mapping.

Threaded tests cover competing claims on one device and across devices,
invalidation racing claim, start and finish (with monotonic snapshot
invariants), and all-or-nothing result publication.
