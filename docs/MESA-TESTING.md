# PS4 Mesa feature and regression matrix

This is the qualification plan for Liverpool/Baikal, not a claim that every
Mesa feature works. Record the kernel, whole module set, Mesa build/patch pins,
loader VRAM allocation, session type and actual mapped libraries for each run.
GPU generation and southbridge are separate identifiers. Other consoles need
their own results.

## Current checkpoint — 2026-10-21

**Stable adoption remains withdrawn.** At the owner's explicit request,
EXP-20261021-023 installed exact dev5 as the experimental default on disk:
matched Mesa/RADV packages`1:26.2.5-5.1`, all40 stage identities verified,
clean package/database checks and signed original rollback packages retained.
Rendering settings, update blocks and package holds remain unchanged. This
CPU-only installation neither restarted the desktop nor repaired the existing
CarChase-faulted boot; activation needs full power-off and a separate boot
observation. It does not qualify a stable image or automatic driver updates.

The earlier dev2 Mesa 26.2.5 candidate rendered
OpenGL/WebGL and a fresh compositor, but the Vulkan rendering check stalled.
UART recorded `PS4_UVD_RUNTIME CLOSED error=-110` at 17:01:57.165841 UTC;
the owner subsequently observed a frozen desktop/mouse. This establishes a
failure of the combined tested stack. Subsequent controlled tests below
isolate an HTILE-enabled graphics trigger; the precise underlying defect
remains unknown. Hyprsunset was never launched.

Both system packages were restored to the signed original 26.1.7 packages,
and previous rendering settings restored. EXP023 then observed a fresh Linux
boot, original package/library identities, UVD queue readiness and a responsive
desktop/mouse confirmed by the owner. No new GPU workload was run during
recovery. Package holds remain enabled, Omarchy's update command remains
blocked, and no repositories were enabled. This is one recovery observation,
not repeated-boot or graphics qualification. The kernel, modules, boot files
and firmware were not replaced in these experiments.

Failed system-adoption candidate: `ps4-mesa-26.2.5-avc-dev2`, manifest SHA256
`3902e67bd5d5170cd0b2e26304b9dac261ea2fa3245a61a00dbd11e10a225ee6`.
Earlier UVD playback results include an earlier Mesa candidate; reproduce
them against a final frozen stack rather than transferring their verdict.
The [curated experiment ledger](https://github.com/meerzulee/omarchy-psx/blob/master/platforms/ps4/experiments/SESSIONS.md)
keeps the exact scope of each experiment. Raw evidence remains private.

EXP025/026 passed the same headless Vulkan clear/readback with the original
26.1.7 driver and isolated dev2 respectively. EXP027 passed a no-depth draw.
EXP028's traced cube hung; its original outer runner discarded partial logs
on timeout, so the last Vulkan call cannot be inferred from that missing log.
Post-fault GFX fences were outstanding while UVD fences remained0/0. EXP029
then observed a fresh baseline boot and owner-confirmed desktop recovery.

On that same fresh boot, EXP-20261021-001/002 used one frozen64×64 D16 probe
binary. With `RADV_DEBUG=img,nohiz`, near green occluded far red and all4096
pixels matched. Changing only to `RADV_DEBUG=img` allocated HTILE at offset8192,
size16384, alignment2048; the submission succeeded but its fence returned
`VK_TIMEOUT`. GFX fences stalled while UVD remained0/0, then the kernel's
generic job-timeout guard closed UVD protectively. No decode was requested.
The owner confirmed a frozen desktop. Both sessions have completed UART
continuity. EXP001 closure overran its90-second bookkeeping deadline by9.216s;
actual GPU work ended after2.94s, and no further GPU work ran during the delay.

Dev3 adds a Liverpool-only HTILE policy workaround, preserving ordinary depth
tests and other families. Its patch/build pins are in
[`sources.json`](../userspace/mesa/sources.json). It is a new experimental
candidate, not an accepted driver. EXP-20261021-003 completed full-power-off
baseline recovery. EXP004 stopped at a missing copied selector manifest before
GPU submission; this is a staging error, not a driver result. EXP005 passed
the same draw/depth oracle with dev3 and `RADV_DEBUG=img` only: all4096 green
pixels matched and no HTILE allocation appeared.

EXP006 nevertheless hung after the first cube presentation. The retained
trace reaches successful acquire, render submission and present, then stops
at `vkDeviceWaitIdle`. Requested128×128 became1920×1080 after compositor
configuration; the actual FP16 swapchain has20480B CMASK and the D16 image
has no HTILE. GFX fences remain outstanding and UVD remains0/0. The120-frame
case and all UVD regressions were skipped after the terminal fault. The owner
confirmed a frozen desktop. Disabling HTILE is insufficient for adoption.

Source audit finds both metadata fills above the4096B compute-fill threshold.
Passed image readback also uses compute, so generic compute is not untested:
the new hypothesis is specifically raw/global-address fill or its ordering
before metadata consumption. Allocation sizes/alignment and1D non-TC-compatible
depth are coherent with the existing AddrLib rules; no bad register or compiler
instruction has yet been proved. Next isolate a plain buffer fill around the
4092/4096B threshold and inspect emitted code offline. No more GPU work may run
on the faulted EXP006 boot. A userspace timeout is not GPU recovery.

After fresh baseline recovery and owner desktop/mouse confirmation, EXP009
uses dev4 to verify4092/4096B fills plus every guard byte in16KiB host-visible
VRAM. Actual Liverpool shader output is MUBUF `buffer_store_dwordx4`; both
fences complete. EXP010 changes the former cube only to dev4:1 then120present
calls and process teardown succeed, all GFX work completes, UVD remains0/0,
and the owner confirms desktop/mouse normal. UART continuity completed for
both. This isolates a useful effect of the fill-bounds patch on these workloads;
it does not yet identify the underlying FLAT hardware/kernel failure.

Current source dev5 keeps bounded-fill0005 and omits historical HTILE-disable0004.
EXP011 passes the exact frozen D16 near/far oracle:4096green pixels, HTILE at
offset8192,size16384,alignment2048. EXP012 then passes1/120cube presentations,
normal GPU completion/teardown, with196608B HTILE and20KiB CMASK. The owner
confirms desktop/mouse normal. These have completed UART continuity.

EXP013 forced-VAAPI decoding matches all120canonical and900native1080p30
software-reference NV12 frames, with122/902UVD completions and no terminal
marker. Its additional process-map collector assumed a /proc children
interface absent from this kernel and silently ignored FileNotFoundError.
The controller's original FAILED_STOP receipt is preserved as an observation
failure; both decoder comparison receipts pass. CPU-only diagnosis proves PPid
status and maps remain readable. EXP014 changes only that collector, verifies
it with a CPU child, and repeats the small clip:120exact frames,122completions,
actual dev5 Gallium mapped. The900-frame test is not repeated. Owner desktop
and mouse remain normal after the final check. Source/driver/FFmpeg pins and
all per-frame/whole-stream hashes are retained privately.

Dev5 archive SHA256 `5c397d86a769ed55dd35798c32bfcedea5c4e3ce6eee5147dd282a04c9385fe9`,
manifest `687af5a366d4bba5af1f72cf5b1a57c4e09d1cfef58257a898ee34da2c7e3964`;
all44 Mesa build tests and repository source checks pass. This is a demonstrated
remedy for the tested workloads, not identification of every lower-level cause.
The original FLAT-only fill was not itself measured failing on Liverpool;
instruction selection also changes scheduling/register allocation, and earlier
failures used different fresh boots. Those tests used the original26.1.7 system
packages/compositor; the later on-disk default change is recorded above.
Broader API/MSAA/depth-format/compute coverage, seeking, sustained
load and three fresh boots remain outstanding before full-stack promotion.

### GFXBench follow-up — advanced rendering fault

GFXBench 5.1.5 was built from upstream commit
`89aa956f43836798d1eaaab8ec32d3dc8b02eead` and run with the same isolated
Mesa26.2.5 dev5 candidate and unchanged kernel. Its developer CLI needed
benchmark-only build/window compatibility changes and the complete upstream
GL shader assets. These are separate from Mesa and kernel changes. The
original failed setup receipts remain recorded in the curated ledger.

| Case | Observed result at actual1920×1080 |
| --- | --- |
| T-Rex, standard56seconds | ✅ 3348frames,59.78FPS; requested repeat3350frames,59.80FPS; owner confirms smooth movement. |
| Manhattan, standard62seconds | ✅ 3703frames,59.71FPS; result statusOK. |
| Manhattan3.1, standard62seconds | ✅ 3624frames,58.44FPS; result statusOK. |
| CarChase (`gl_4`, GL4.3 core) | ❌ Graphics VM faults after initialization and entering the run; controller kills its owned benchmark and stops the suite. |
| Remaining37GL and five Vulkan configurations | ⏳ Not run after the terminal fault. |

The passes save actual candidate Gallium maps, result JSON and caught-up cached
fences; UVD stayed unused. Scores are workload observations with vsync and
monitoring overhead, not independent pixel correctness or competitive results.
Requested1280×720 was configured by Hyprland as1920×1080.

In EXP-20261021-022 the first retained graphics VM fault header is at21:20:45UTC
on October20. Two interrupt-ring overflows interleave that header and the first
retained tuple: a read at page`0x00FF0010`; a write at page`0x000018C8` follows.
Earlier hardware events may have been lost. The generic guard records
`PS4_UVD_RUNTIME CLOSED error=-5` at21:20:47 despite no decoding request.
Later cached queues being caught up does not reverse that terminal closure.
No GPU reset/rearm or further GPU workload was attempted; full power-off and
a healthy Linux boot are required before more hardware testing.

The failing instruction, buffer and ownership layer are not yet identified.
CarChase adds particle SSBO, tessellation and other rendering paths;
Manhattan3.1 already exercises compute. Its warmup simulation shader lacks an
explicit range check, but the actual256particle count is divisible by every
tested workgroup size, so that fact does not demonstrate an OOB here. Warmup's
timer-result wait or`glFinish()` also synchronizes completed GPU work. An
upstream `glGetString(GL_EXTENSIONS)` error in a core context also does not
establish the cause of a GPU memory fault. Preserve the first fault and partial
logs, then reduce one dispatch or draw after stage attribution rather than
retrying the entire failing scene. This candidate remains unqualified for stable
system adoption despite the owner's experimental default. The benchmark and
reviewed results are saved privately on the console.


Legend: ✅ observed bounded pass; ❌ observed failure; 🧪 partial/historical;
⏳ not run or not qualified. A reported API version is an enumeration result.

| Feature | Current evidence | Qualification test and required result |
| --- | --- | --- |
| Source and CPU tests | ✅ 44 Mesa build tests; 15 FMASK replay cases; 62 player fixture checks | Repeat pinned build/patch verification and relevant source checks for each new artifact. These do not qualify a GPU. |
| Driver selection | ✅ 39 staged identities verified after package install; compositor and Chromium mapped 26.2.5 | Verify package/version, hashes and live process maps; reject software fallback or an older DSO. |
| Desktop OpenGL / GLES | 🧪 dev5 T-Rex/Manhattan passes; ❌ CarChase graphics VM fault; GL4.6/GLES3.2 contexts enumerated | Small clear/triangle/readback under GL core/compatibility and GLES 2/3, then reduce the advanced rendering failure; expected pixels and clean process exit. |
| EGL / GLX / GBM | 🧪 Wayland/GBM EGL contexts passed; broad eglinfo returned 1 because a platform/device probe failed | Test each intended platform separately, including a controlled Xwayland GLX context; identify failures without hiding exit status. |
| PNG/RGB textures and browser WebGL | ✅ isolated Firefox/Chromium passed; installed-system Chromium displayed matching PNG/WebGL with AA | Verify pixel/oracle output and `glError=0`; WebGL 1 and 2 separately; normal and antialiased contexts. Repeat on the final stack. |
| Framebuffers / MSAA / FMASK | 🧪 original selected-entry assertion fixed and 15 CPU cases pass; browser AA smoke passed | Actual GPU FBO resolve/readback at supported 2×/4×/8× samples, small/odd dimensions, depth/stencil attachments; compare reference pixels. |
| Shaders / blending / depth / stencil | 🧪 dev5 exact D16 near/far oracle passes with HTILE enabled; no general coverage | Add focused depth formats/stencil, shader and blend oracles on the frozen candidate. |
| Texture formats / tiling / transfers | 🧪 saved RGB/NV12 fixtures clean in tested GL path | RGB/RGBA, supported sRGB/float/integer formats, mipmaps, subimages, PBO transfers and readback; exercise pitch/alignment boundaries. |
| Wayland presentation | 🧪 short browser/player and compositor observations | Resize, fullscreen/windowed, overlays, text, repeat frame presentation; verify display and reference captures without stripes or checkerboards. |
| DMA-BUF / explicit synchronization | ⏳ not isolated | Small export/import and fence-order tests across producer/compositor; compare pixels, ensure bounded completion and clean close. |
| Vulkan enumeration | ✅ RADV 26.2.5 enumerated Liverpool, API 1.3.354 | Record advertised features/queues/formats/extensions without treating them as working rendering. |
| Vulkan rendering / Wayland WSI | 🧪 dev5 passes1/120cube presentations with HTILE enabled; former depth/cube failures recorded | Broaden focused formats/MSAA/WSI oracles and repeated boots before adoption; no conformance claim. |
| Vulkan compute / barriers / descriptors | 🧪 typed readback and4092/4096B fill/guard oracles pass; no general compute qualification | Small descriptor, memory-type, copy and barrier oracles, then bounded compute/shader-address cases. |
| VAAPI capability reporting | 🧪 AVC/NV12 gate was source-tested and exercised earlier; post-install vainfo was not reached | Confirm only kernel-supported profiles/entrypoints and explicit unsupported results; capability listing alone is not decode proof. |
| UVD H.264 correctness | ✅ dev5 repeats120canonical and900native1080p30 exact software-reference frames;122/902completions | Forced VAAPI decode, fixed NV12 download, per-frame and whole-stream hashes; prove actual UVD fence activity and no software fallback. |
| UVD playback / frame pacing | 🧪 earlier five 1080p30 loops completed without reported drops | Reproduce on final artifacts with one hardware decode thread; record source FPS, PTS/wall rate and decoder/display drops. |
| UVD seeking / EOF / reopen | ❌ earlier interactive seek error; lifecycle not qualified | Separate bounded seek, drain/EOF and sequential reopen cases. No concurrent handles beyond the implementation's declared single-handle limit. |
| Graphics with experimental UVD runtime enabled | ❌ CarChase VM faults close the protective guard without decode; earlier dev5 cube/UVD checks passed sequentially | Preserve the generic guard. Qualify combined simultaneous cases, sustained load and repeated boots separately. |
| Browser video acceleration | ⏳ rendering tests disabled browser video decode | Record actual stream codec, decoder, mapped driver and dropped frames. Test single supported AVC stream before seeking or multiple tabs. |
| Hyprsunset / CTM / gamma | ⏳ blocked; not tested in EXP022 | Separate display-control experiment after recovery; short mild tint, timed identity restore, independent recovery and operator observation. This also depends on Hyprland/kernel display support. |
| VCE encoding | ⏳ not implemented/qualified | Keep unavailable until separate kernel/backend work and encode correctness tests exist; do not infer support from Mesa enumeration. |
| Games / Wine / DXVK | ⏳ no qualification | After API tests: reproducible small native GL/Vulkan workload, then selected DXVK applications; version, settings and visual oracle recorded. 32-bit drivers require a separate build/test set. |
| Sustained load / cold starts | ⏳ no full-stack qualification | Increment short runs before a 30-minute combined run; monitor actual temperature/thermal limits. Final promotion requires three fresh boots of frozen artifacts and no terminal faults or unexplained failures. |

## Order of work

```text
Full power-off and healthy baseline
    ↓
Read-only source audit of EXP022 timeout / resource ownership
    ↓
Qualified graphics-only kernel control + process-local Mesa
    ↓
GL/EGL/texture/MSAA tests      Vulkan device → clear/readback → presentation
    ↓                            ↓
Focused Piglit / GL-CTS       Focused Vulkan-CTS
    └──────────────┬─────────────┘
                   ↓
UVD-only correctness → playback → EOF/reopen → controlled seek
                   ↓
Combined graphics + UVD and browser/video tests
                   ↓
Separate CTM/night-light test → repeated boots and sustained use
                   ↓
Package installation trial → desktop/application acceptance → update eligibility
```

Use an already qualified non-UVD kernel/module control set to isolate graphics;
do not assume a single boot parameter disables every experimental probe. Any
boot-set change needs its own measured identity, rollback and UART experiment.
Keep driver selection process-local until its applicable hardware gates pass.
Installing RADV must wait for Vulkan rendering acceptance, independently of
OpenGL acceptance. A data-only package build is not that acceptance.

## Test execution rules

1. Choose one stated question and one changed diagnostic variable. Declare
   exact cases, time/memory bounds and rollback before starting one coherent
   UART session. Run serially on this console; no uncontrolled GPU stress loops.
2. Begin with small cases. Pin suite commits, build options, runner options and
   case lists. Record `PASS`, `FAIL`, `CRASH`, `TIMEOUT`, `SKIP` or `NOT_RUN`
   separately. Unsupported features are skips only when documented as such.
3. Stop the batch on the first hang, terminal kernel marker, GPU fault, lost
   presentation or unexpected decoder error. A userspace command timeout does
   not cancel an already submitted GPU job. Do not continue the remaining list.
4. Never unload/reset/rearm the retained UVD/MTS generation. A terminal UVD
   failure requires full physical power-off, not a compositor or warm restart.
5. Read the completed compact UART context and inspect the matching outputs
   before adopting a result. Keep screenshots, traces and raw logs private;
   publish reviewed conclusions and artifact identities.

Hyprsunset's tint is not reliably proven by a screenshot: obtain the operator's
physical-monitor observation. Before the first tint, verify the exact installed
CLI/IPC, identity restoration and an independent timed recovery path. Keep
normal autostart/Omarchy feature guards blocked until this test and recovery
repeat successfully. No Hyprsunset command has been qualified by the Mesa fix.

## Record for each case

| Field | Required data |
| --- | --- |
| Identity | EXP ID, hardware/GPU, boot ID privately, kernel/config/module hashes, Mesa candidate and mapped DSO hashes |
| Execution | Suite commit, exact case name/command, backend, dimensions/format, workload bounds and elapsed time |
| Result | Exit code, case verdict, expected/actual pixels or hashes, errors, drops/fences where applicable |
| Recovery | Process stopped, display/control status, UART continuity, rollback and any required full power-off |
| Scope | Explicitly identify omitted cases, unsupported features and later events outside the captured prefix |

The master matrix summarizes these records; it never turns an API string,
host build, screenshot alone or missing error log into full conformance.

## Upstream suites and references

Mesa CI uses multiple suites, including Piglit, WebGL and VK-GL-CTS. Use them
after focused smoke tests; a benchmark score is a separate performance result.

- [Mesa CI testing overview](https://docs.mesa3d.org/ci/index.html)
- [Mesa utilities and Piglit](https://docs.mesa3d.org/utilities.html)
- [Khronos VK-GL-CTS](https://github.com/KhronosGroup/VK-GL-CTS)
- [Vulkan CTS build/run documentation](https://github.com/KhronosGroup/VK-GL-CTS/blob/main/external/vulkancts/README.md)
- [Hyprsunset controls and observation limits](https://wiki.hypr.land/Hypr-Ecosystem/hyprsunset/)

Pin exact upstream suite revisions for execution. This document has not run a
full Piglit/CTS campaign or established Khronos certification.
