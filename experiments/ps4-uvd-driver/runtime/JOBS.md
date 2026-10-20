This component supplies bounded job/BO/IB lifetime for the gated Liverpool UVD runtime. It does not initialize UVD, program VMID4, implement packet validation, signal hardware completion, reset a GPU, stop DMA, or qualify playback. The parent owns those operations and the lifetime of the device, ring, firmware, shared GART and SA-manager backing BOs.

Only `amdgpu_ps4_uvd_jobs.[ch]` and `ps4-uvd-jobs-hooks.patch` implement this part. The patch targets the exact `native-frame-build-02/work/source` files and preserves their complete existing license notices. No base/build02 file was edited. The parent must add the C object to its runtime build and implement the bridge declared in the header:

```c
struct amdgpu_ps4_uvd_jobs *
amdgpu_ps4_uvd_runtime_jobs(struct amdgpu_ring *ring);
```

The bridge returns the initialized state only for the explicitly enabled Liverpool UVD ring. It returns NULL outside that gate. **After a fault it must continue returning the closed state**, rather than returning NULL and accidentally enabling the ordinary unleased path. The component's initializer also verifies Liverpool and UVD ring type. It assumes no new `amdgpu_device` member: the parent chooses where to retain the state and how to find it.

Zero-initialize once, before admitting any generic UVD submission. Initialize using the actual ring hardware-fence context and explicit limits. A reasonable starting budget, which the parent may reduce for its advertised stream bounds, is:

```c
struct amdgpu_ps4_uvd_jobs_limits limits = {
        .max_bytes = 512ULL << 20,
        .max_job_bytes = 256ULL << 20,
        .max_job_ib_bytes = 1ULL << 20,
        .max_objects = 512,
        .max_job_objects = 128,
        .max_jobs = 8,
};
/* close_cb is nonblocking and IRQ-safe. */
amdgpu_ps4_uvd_jobs_init(state, ring, fence_context, &limits,
                       close_cb, parent_runtime);
```

Hard maxima are 1GiB charged bytes, 1024 objects, 32 jobs, and 128 objects per job. Objects include unique BOs within the lease **plus each kernel IB SA span**. Byte charges include full BO sizes plus actual SA allocation sizes, not just payload length. Shared BOs in different outstanding jobs are conservatively charged twice, each with its own reference/pin; releasing one lease cannot unpin the other. Active, deferred and quarantined leases all keep their charges. Budget rejection occurs before hardware entry. Lease metadata and jobs are separately bounded by the object/job limits; the byte total is a residency/SA budget rather than a claim to count every kernel allocator byte.

The normal user-CS path

`admit(state, parser, job)` runs immediately after successful `amdgpu_cs_parser_bos`, before `amdgpu_cs_patch_jobs`, while `bo_list_mutex`, the VM reservation and every BO reservation are still held. It admits only one nongang UVD job on the exact state ring. It rejects userptr, imported/SG, KFD, encrypted, parent-backed, nonlocal memory, user-fence, GDS/GWS/OA, and unsupported VA mappings. Initially each listed BO needs one full, offset-zero read/write mapping; aliases, partial mappings, PRT and userqueue-owned mappings are rejected. A pending full mapping in the invalids list is allowed because ordinary VM handling follows. These are intentional first-port restrictions, not support claims for those resource classes.

It deduplicates listed BO pointers, checks all limits and kernel-owned SA allocation bounds, establishes contiguous VRAM/GART residency, then pins/references each BO. Any admission/pin failure rolls back its own pins/references and charge under the existing reservations. The normal UVD parser still validates messages, stream ownership, sizes, packet register allowlists, ordering and relocations. A new `amdgpu_cs_find_mapping` hook rejects any packet or source-IB mapping outside the job's pinned lease; merely being an implicitly reserved VM object is not sufficient ownership. No packet-validation bypass is provided.

Pre-patch pinning means the first parser pass must not rely on moving an already pinned BO into a different UVD4.2 segment. The parent explicitly selects the proved PS4 64-bit transport/common-address flag for its GART/VRAM alias, while retaining ordinary UVD packet/message/size/session checks. This component does not change that flag or pretend that userspace family/version gates prove the transport.

Every CS failure after admission calls `abort_reserved(job)` at `error_backoff`, **before unlocking parser reservations**. That is strictly a never-entered rollback: it releases its pins/references and SA spans, detaches the lease and restores the charge. An entered/free-requested job passed there instead closes admission and retains memory. Parser-finalization then follows the ordinary cleanup path. No workqueue attempts to reserve BOs while the CS still owns these reservations.

Kernel submissions and file-close messages

Generic `amdgpu_job_submit` and `submit_direct` acquire `admit_kernel(state, job)` before their first early resource release or scheduling attempt, unless the job already owns a lease. This no-user-BO form validates/charges the actual driver SA allocations and outstanding job. Its precondition is that every other kernel DMA target referenced by that IB is permanently pinned and retained by the parent. It does not turn arbitrary kernel addresses into owned memory.

The base has one important exception to a static-message assumption: `amdgpu_uvd_get_create_msg` and direct DESTROY use `adev->uvd.ib_bo`, but **non-direct DESTROY allocates a fresh 4096-byte kernel message BO and calls `amdgpu_bo_free_kernel` after `amdgpu_uvd_send_msg`**. The extra patch hunk in `send_msg`, after IB construction and before either submit branch, calls `admit_kernel_message(state, job, bo)`. This requires the local kernel BO's original pin to exist, reserves it in process context, and acquires an additional lease reference/pin under the same byte/object budget. Its physical address already written into the IB therefore stays stable. The caller can drop its original kernel allocation/pin without releasing the lease's residency. Generic submit/direct preserve that existing message lease. This covers the file-close DESTROY path on success, cancellation and uncertain DMA.

The shared static `ib_bo` must also remain parent-owned, and its CPU contents must not be rewritten while an earlier job can read them. A pin protects residency, not concurrent content mutation. The parent uses its custom initial ring test and serializes any allowed static-message helper before client admission; generic direct recovery/rearm helpers must not run after entry.

Actual completion and early-free hooks

The patch adds `struct amdgpu_ps4_uvd_lease *ps4_uvd_lease` to the job. `enter(job)` runs immediately before the earliest `amdgpu_ib_schedule`, conservatively marking entry even if scheduling then fails. Re-entry returns an error and closes the queue. An active-runtime unleased job cannot enter. `submitted(job, actual_hw_fence, schedule_error)` binds a reference to the actual hardware fence, checking context and nonzero sequence. A scheduler-finished fence is not a substitute. Errors, missing/mismatched fences and scheduling failure close admission and retain entered jobs.

**The parent must call `observe_hw(state, fence_context, completed, emitted)` only from the actual writeback read/validation path, before signaling matching hardware fences.** It supplies monotonically extended sequences and must validate the hardware/ring/context, reset/fault state and issued range. This function checks context, monotonicity and completed<=emitted. Sequence zero is the initial baseline and never validates a bound job. Never call it from CPU fence writes, force-completion, recovery, teardown or reset. The parent also blocks those paths while this runtime is active. No fence wrap/rearm or context replacement is supported by this component.

`amdgpu_job_free_resources` returns while a lease exists. This covers the early calls in user CS submit, kernel submit, and after run_job; no SA allocation is queued against a potentially forced scheduler-finished fence. **Both** `amdgpu_job_free` and scheduler `free_job` ask `jobs_job_free(job, scheduler_free)` before any scheduler/fence/job cleanup. The scheduler callback passes true; ordinary `amdgpu_job_free` passes false. For a leased job it transfers cleanup to one bounded per-state work item and returns false; the core must immediately return without touching the job. A pending direct submission is ordinary deferred cleanup, not a timeout.

The work item releases only a never-entered job, or an entered job with all of:

- a successfully signaled actual hardware fence;
- matching hardware-fence context and sequence at/below the trusted observed watermark;
- a healthy state at the release decision and no quarantine.

It reserves/unpins/releases BOs in process context, releases the SA spans against the proved actual fence, detaches/un-charges the lease, and invokes the original cleanup path exactly once through `amdgpu_ps4_uvd_job_free_deferred`. The lease retains the scheduler-callback origin: the real scheduler clears `job->base.entity` when dequeuing, so simply calling ordinary `amdgpu_job_free` would skip `drm_sched_job_cleanup` and leak scheduler fence/dependency/stat references. Re-entering the original `free_cb` preserves its unconditional scheduler cleanup. The native scheduler cleanup uses retained job/fence/entity-stats references; it does not dereference an already-closed client's entity. Hardware callbacks only record failure/schedule the work; they do not reserve BOs, unpin, free IBs or destroy jobs in IRQ context. Lock ordering avoids taking a fence lock while holding the state lock.

A successful software/forced fence signal without the hardware watermark cannot recycle the lease. Any negative scheduler-finished error closes admission, including accepted jobs cancelled before hardware entry. A never-entered job may then release safely, but an entered job with a negative hardware or scheduler error, scheduling failure, explicit parent fault/timeout, or completion inconsistency remains quarantined with its BO references/pins, SA spans and full job. No post-fault reaper, destructor, unpin, reset or retry API exists. Already proved safe release decisions remain safe; another outstanding lease independently protects shared BOs. A failed BO reservation during retirement also closes/retains remaining resources conservatively.

Parent integration requirements that the patch does not supply

- Close callback sets the parent's terminal admission state and preserves the first reason. It must be IRQ-safe/nonblocking; it must not reset hardware, force fences or release DMA storage.
- The parent's timeout dispatch calls `jobs_fault` **before** generic timeout recovery/coredump/reset/free behavior and does not continue into it. Fault/IRQ/reset-history violations do likewise. The component itself never operates hardware or retries.
- Parent runtime state, ring, firmware, shared GART/PTEs, VMID reservation, static message/IB BOs and every global SA-manager backing BO remain alive. Keeping a span pointer does not make `amdgpu_sa_bo_manager_fini`, driver removal, suspend or global memory teardown safe. These operations remain blocked after entry under the parent's contract.
- Do not bypass the submit/run hooks with a raw `amdgpu_ib_schedule` on the active UVD ring. There is no naked post-entry SA path. `defer_resources` relies on the admission hooks being installed before any submission publication.
- The core kernel-submit API traditionally returns a fence to callers that do not check ERR_PTR. On admission rejection the hook arms an errored, skipped scheduler job and returns its error fence without publishing hardware. Direct submit keeps its ordinary error/ownership convention.

No device, MMIO or firmware model was added. The bounded host fixture below executes the actual lifecycle C with deterministic kernel-API stubs. It is CPU policy evidence, not execution of this concurrency policy in a kernel and not hardware acceptance.

Exact base SHA-256:

| File | SHA-256 |
|---|---|
| amdgpu_job.c | ecdb1152c3a82f3207f0c649aa82dffa112b64d633675bdc13e0af1f58d32d32 |
| amdgpu_job.h | a130f1686c314a0bdbffdd59ee6ce454a79e6bfda60c98f2a276e4a3336e70df |
| amdgpu_cs.c | 6fcdab083cfe615045ba987468f717a227e32b3a004c576d0651b74dd078c4c2 |
| amdgpu_uvd.c | 6b8c41d474f950b1de4fa57a03498915fa4ed0208341de5655379cdb81e5318d |

The matching local reference files were read and the native hashes checked over SSH. Isolated native objects are compiled against build02's real generated headers/config and its saved GCC16.2.1 kernel compile flags. Only dependency/object outputs and a small header/source overlay are written to fresh `/tmp/ps4-uvd-jobs-isolated-*` directories; build02 files remain unchanged. Explicit preinclude of the patched job header keeps nested quoted base includes from selecting the original struct. Initial attempt01 preserved an overlay include-order failure; attempt02 corrected that checker setup and all four translation units compiled without diagnostics. The final attempt after the lease-membership hook is recorded below.

Earlier focused native compile result (before the scheduler-origin correction): **PASS**, GCC 16.2.1, the actual new jobs C and patched job/CS/UVD C translation units all compiled at the saved kernel `-O2` flags with empty diagnostics. The final patch applied to all four exact base files with zero fuzz. Original base file hashes were rechecked unchanged after compilation. There was no kernel link, module load or execution/hardware claim in this check.

Earlier source SHA-256 associated with that retained compile receipt:

| File | SHA-256 |
|---|---|
| amdgpu_ps4_uvd_jobs.c | 4e279edc13a00710fac71e1b5e5fffeae89e524ff337042412551adcfc197657 |
| amdgpu_ps4_uvd_jobs.h | 69c368f2bdbd0e9022ca8c48b16201f470fc983ba2a6f204a5e96d1c0821ff60 |
| ps4-uvd-jobs-hooks.patch | 3ad5fff92123ca004ca02d8291a093bd2de79d95ca6cd85a20e95bb8fe3fafb1 |

Native receipt on Omarchynator: `/tmp/ps4-uvd-jobs-isolated-03-prpst3la/COMPILE.json`, SHA-256 `1414725f30561bc49fe073aa5430d698ac12deb6d054cec760e2aef1518c9731`. The directory also contains each exact compiler argv, diagnostics, objects and small patched source/header overlay for independent review. It is temporary CPU-check evidence, not a kernel candidate or installed artifact. The final receipt's compiled source hashes are jobs `4e279edc...`, job core `58299c5cc85af7593201dfb1e99d874c4f8b44c84539128b7d276c9db8370998`, CS `ef1e26639aee3e12179e84ecb10660874fd1bb0f7f70d1982ea48ffdea2dde4e`, and UVD `c714fe1dd23b9a34d70319cf25b23c872793fb650075f6e842c2bf15413750ed`.


The subsequent independent review found that the original reaper's ordinary-free call skipped scheduler cleanup after dequeue. The implementation now preserves and re-enters the original scheduler or direct free path as described above. GCC's host check also required explicit initialization of the reaper's local reservation result (`r = 0`); this does not change the branch's intended behavior. The prior native compile receipt remains evidence for its prior hashes, rather than a claim to have compiled these later bytes as a complete kernel.

Actual-source lifecycle check

Run `python3 jobs_check.py --base-amdgpu /path/to/pinned/drivers/gpu/drm/amd/amdgpu` from any directory. `--cc` selects the compiler; `--output` may name a fresh, nonexistent result directory. The checker verifies the four exact base hashes above, applies the real hook patch with zero fuzz/offset, then includes the complete actual lease C/header (removing only include directives) and extracts the actual patched core `free_resources`, `free_cb`, `job_free`, and deferred-finalizer functions. It writes private source, compiler argv, stdout/stderr and a JSON receipt. Failed attempts retain evidence. No copied policy implementation substitutes for the actual functions.

The 24 cases cover gate-off behavior; bounded admission, BO deduplication and rollback; allocation/GART/pin failures including partial rollback; unsupported BO/map/SA rejection; byte/object/job/IB budgets; never-entered abort; delayed direct completion; software-signaled fences without hardware watermark; already-signaled actual-fence binding; timeout, hardware/scheduler errors and re-entry quarantine; fence context/sequence/observation errors; retirement reservation failure; kernel SA leases and temporary message BO extra-pin ownership; message budget recharge refusal; independently pinned shared BOs; actual early `free_resources` deferral; and dequeued scheduler cleanup with entity NULL versus direct cleanup without an entity. Assertions track charges, pins/references, SA release fence, scheduler cleanup and final job release. Stub quarantine metadata is destroyed only after assertions to avoid fake host leaks; production has no analogous post-fault destructor.

Six exact source mutations must fail a specific fixture assertion: removal of watermark proof, treating a pending fence as completed, removing resource deferral, missing pin rollback, reopening faulted admission, and losing the scheduler free callback origin. Mutation failures are preserved individually. The check does not claim exhaustive branch coverage or thread-race verification; workqueue and callback interleavings are deterministic CPU cases.

Prior scheduler-origin result: **PASS_CPU_LIFECYCLE**, 24 cases at `-O2` and again under ASan+UBSan, six of six mutants detected, on Apple Clang 21.0.0 and native Omarchynator GCC 16.2.1. Native ASan includes leak detection and has empty diagnostics; macOS ASan excludes unsupported LeakSanitizer. No hardware, console, USB, installed source or build02 mutation occurred.

Source SHA-256 for that retained 24-case receipt:

| File | SHA-256 |
|---|---|
| amdgpu_ps4_uvd_jobs.c | 7aae2915ee48e77cdee5e9096b598bfc6664863a68814c7830fe7474d4607721 |
| amdgpu_ps4_uvd_jobs.h | d68546faba118b46edf94eaeace8e86fd3564e6af94cbd5933580e6181dded30 |
| ps4-uvd-jobs-hooks.patch | e2539b9113089e0d199000779c0dbbfd1de45b558341b5620bacca727ce2f589 |
| jobs_fixture.c | dc90dd7d814111f9e1ba46bc4c1cead6b1361cc0a2319290de21b2a1ece31584 |
| jobs_check.py | 300fd75538f7c1fd4951629ba1c5ca79061826f92a7460d630a28a94525614ab |

Native receipt: `/tmp/ps4-uvd-jobs-lifecycle-01-OwBVup/check-03/receipt.json`, SHA-256 `b0a1616b4402bd08381f66e07cd3b6c723c075d82682dea771f39ac99bb1fcb8`. Its local mirror is `/tmp/ps4-uvd-jobs-native-lifecycle-03-receipt.json`. The same root preserves the source copies and prior failed host check (GCC's uninitialized-local warning). The first remote launch stopped before receipt creation because the relocated checker computed a nonexistent fourth parent; its fallback path computation is corrected. Mac receipt: `/path/to/private-temporary-artifact`, SHA-256 `e29170667b1dddc5ee1f3d9b804b430177b512c0aad12f774b5f13c10c9f6215`.


Cancellation follow-up

Native `drm_sched_entity_kill_jobs_work` can signal an accepted, never-run job with `-ESRCH` and call `free_cb` without reaching the runtime run hook. Scheduler errors therefore close the queue even when the current lease never entered. Its unpublished BOs/IBs still clean up safely, while other entered leases remain quarantined. A healthy `jobs==0` drain must check the closed/error state as well as the count; cancellation cannot become permission to publish a file-close DESTROY. The caller owns session serialization and that drain.

The additional actual-source case executes this cancellation through the extracted core `free_cb`, both with a sole unentered lease that safely reaches jobs0 and with another entered lease retained despite later positive fence/watermark. The seventh source mutant restores the old entered-only error restriction and is caught at the queue-closure assertion. Final result: **PASS_CPU_LIFECYCLE**, 25 cases at O2 and ASan+UBSan on both Mac and native Linux; native leak detection passes with empty diagnostics, seven of seven mutants detected. No API/header/hooks change was needed for this final correction.

Final source SHA-256:

| File | SHA-256 |
|---|---|
| amdgpu_ps4_uvd_jobs.c | 6a5b25a0dea85930d64716f15df7f83addb6383c7dcb82e1b0a20974c1c950e9 |
| amdgpu_ps4_uvd_jobs.h | d68546faba118b46edf94eaeace8e86fd3564e6af94cbd5933580e6181dded30 |
| ps4-uvd-jobs-hooks.patch | e2539b9113089e0d199000779c0dbbfd1de45b558341b5620bacca727ce2f589 |
| jobs_fixture.c | 5c650e235615bd159a62aee4e8a076fb9e0361645d3318eba00376ad3bc702c1 |
| jobs_check.py | 49f87a0fd1c79ecfb58fefe486146bdb782c7069598a06c3e54ac7b7c0e25e8b |

Final native receipt: `/tmp/ps4-uvd-jobs-cancel-01-12guw0/check/receipt.json`, SHA-256 `c23ba6a39e0830b4a27099e11f6e89c4a6eeb38286a8a6feac37d9e7518d5beb`; local mirror `/tmp/ps4-uvd-jobs-native-cancel-01-receipt.json`. Final Mac receipt: `/path/to/private-temporary-artifact`, SHA-256 `ab538f2f3cc662e727a32407adc81e055175b2796aacf53ff0fcf8e8ca076db1`. Earlier receipts remain intact.
