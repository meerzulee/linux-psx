# PS4 Mesa feature and regression matrix

This is the qualification plan for Liverpool/Baikal, not a claim that every
Mesa feature works. Record the kernel, whole module set, Mesa build/patch pins,
loader VRAM allocation, session type and actual mapped libraries for each run.
GPU generation and southbridge are separate identifiers. Other consoles need
their own results.

## Current checkpoint — 2026-10-20

**System adoption is withdrawn.** The exact Mesa 26.2.5 candidate rendered
OpenGL/WebGL and a fresh compositor, but the Vulkan rendering check stalled.
UART recorded `PS4_UVD_RUNTIME CLOSED error=-110` at 17:01:57.165841 UTC;
the owner subsequently observed a frozen desktop/mouse. This establishes a
failure of the combined tested stack, not the root cause or proof that either
RADV or UVD alone caused it. Hyprsunset was never launched.

Both system packages were restored to the signed original 26.1.7 packages,
and previous rendering settings restored. EXP023 then observed a fresh Linux
boot, original package/library identities, UVD queue readiness and a responsive
desktop/mouse confirmed by the owner. No new GPU workload was run during
recovery. Package holds remain enabled, Omarchy's update command remains
blocked, and no repositories were enabled. This is one recovery observation,
not repeated-boot or graphics qualification. The kernel, modules, boot files
and firmware were not replaced in these experiments.

Candidate identity: `ps4-mesa-26.2.5-avc-dev2`, manifest SHA256
`3902e67bd5d5170cd0b2e26304b9dac261ea2fa3245a61a00dbd11e10a225ee6`.
Earlier UVD playback results include an earlier Mesa candidate; reproduce
them against a final frozen stack rather than transferring their verdict.
The [curated experiment ledger](https://github.com/meerzulee/omarchy-psx/blob/master/platforms/ps4/experiments/SESSIONS.md)
keeps the exact scope of EXP006–023. Raw evidence remains private.

Legend: ✅ observed bounded pass; ❌ observed failure; 🧪 partial/historical;
⏳ not run or not qualified. A reported API version is an enumeration result.

| Feature | Current evidence | Qualification test and required result |
| --- | --- | --- |
| Source and CPU tests | ✅ 44 Mesa build tests; 15 FMASK replay cases; 62 player fixture checks | Repeat pinned build/patch verification and relevant source checks for each new artifact. These do not qualify a GPU. |
| Driver selection | ✅ 39 staged identities verified after package install; compositor and Chromium mapped 26.2.5 | Verify package/version, hashes and live process maps; reject software fallback or an older DSO. |
| Desktop OpenGL / GLES | 🧪 GL 4.6 and GLES 3.2 contexts created; narrow rendered fixtures passed | Small clear/triangle/readback under GL core/compatibility and GLES 2/3; expected pixels and clean process exit. |
| EGL / GLX / GBM | 🧪 Wayland/GBM EGL contexts passed; broad eglinfo returned 1 because a platform/device probe failed | Test each intended platform separately, including a controlled Xwayland GLX context; identify failures without hiding exit status. |
| PNG/RGB textures and browser WebGL | ✅ isolated Firefox/Chromium passed; installed-system Chromium displayed matching PNG/WebGL with AA | Verify pixel/oracle output and `glError=0`; WebGL 1 and 2 separately; normal and antialiased contexts. Repeat on the final stack. |
| Framebuffers / MSAA / FMASK | 🧪 original selected-entry assertion fixed and 15 CPU cases pass; browser AA smoke passed | Actual GPU FBO resolve/readback at supported 2×/4×/8× samples, small/odd dimensions, depth/stencil attachments; compare reference pixels. |
| Shaders / blending / depth / stencil | ⏳ no general coverage | Focused Piglit/GL-CTS shader and render cases, then required API-version cases. Record individual names and outputs. |
| Texture formats / tiling / transfers | 🧪 saved RGB/NV12 fixtures clean in tested GL path | RGB/RGBA, supported sRGB/float/integer formats, mipmaps, subimages, PBO transfers and readback; exercise pitch/alignment boundaries. |
| Wayland presentation | 🧪 short browser/player and compositor observations | Resize, fullscreen/windowed, overlays, text, repeat frame presentation; verify display and reference captures without stripes or checkerboards. |
| DMA-BUF / explicit synchronization | ⏳ not isolated | Small export/import and fence-order tests across producer/compositor; compare pixels, ensure bounded completion and clean close. |
| Vulkan enumeration | ✅ RADV 26.2.5 enumerated Liverpool, API 1.3.354 | Record advertised features/queues/formats/extensions without treating them as working rendering. |
| Vulkan rendering / Wayland WSI | ❌ `vkcube --wsi wayland --c 120` stalled in DRM sync-object wait; screenshot timed out | Resolve the regression before another cube or broad suite. Separate device/queue creation, one clear/readback, then a bounded present test with validation. |
| Vulkan compute / barriers / descriptors | ⏳ not qualified | After the basic Vulkan gate passes: small readback oracles for descriptors, memory types, copies and barriers; only then bounded compute cases. |
| VAAPI capability reporting | 🧪 AVC/NV12 gate was source-tested and exercised earlier; post-install vainfo was not reached | Confirm only kernel-supported profiles/entrypoints and explicit unsupported results; capability listing alone is not decode proof. |
| UVD H.264 correctness | 🧪 earlier 120-frame and 900-frame 1080p30 streams matched software references | Forced VAAPI decode, fixed NV12 download, per-frame and whole-stream hashes; prove actual UVD fence activity and no software fallback. |
| UVD playback / frame pacing | 🧪 earlier five 1080p30 loops completed without reported drops | Reproduce on final artifacts with one hardware decode thread; record source FPS, PTS/wall rate and decoder/display drops. |
| UVD seeking / EOF / reopen | ❌ earlier interactive seek error; lifecycle not qualified | Separate bounded seek, drain/EOF and sequential reopen cases. No concurrent handles beyond the implementation's declared single-handle limit. |
| Graphics with experimental UVD runtime enabled | ❌ EXP022 combined stack hit a terminal UVD timeout | No AVC workload was requested by the cube. Audit synchronization/ownership first; compare graphics-only, decode-only and combined cases on fresh healthy boots. |
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
