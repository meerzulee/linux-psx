# First normal Liverpool UVD application result

EXP-20261020-006 passed on 2026-10-20: the returning experimental backend
booted to normal desktop/SSH and FFmpeg decoded one pinned H.264 clip through
Mesa radeonsi VAAPI. All 120 distinct NV12 frames matched the independent
software decode byte-for-byte. This is one clip on one cold boot.

The clip is 128×96 Constrained Baseline, 30 fps, four seconds, with four I and
116 P frames. Downloaded output is 2,211,840 bytes; SHA256
`d99b87567601efb735d438e766f34112344a3a31fc1d9e1b4177bfd329bcb491`.
Primary and an independent saved-data reviewer recomputed all 120 frame hashes
and compared the bytes. The runner captures FFmpeg stdout and does not substitute
reference pixels. Its fixed graph requires VAAPI frames followed by
`hwdownload,format=nv12`, with automatic conversion disabled. The log reports
VAAPI hardware frames, all 120 decoded frames, zero decode errors and exit 0.
The `h264 (native)` log label identifies FFmpeg's decoder wrapper; hardware
frame format and the required download graph establish the acceleration path.

## Frozen identity

- Kernel: `7.2.11-psx-ps4-baikal-uvd-runtime-dev1`.
- bzImage SHA256: `9c3ec7d27fdfdebfee137684a635301e8dfa259b50e2f5d98ee33cf5f91e6153`.
- Running GNU build ID: `32bd29522ed3400ff111a8a42a30f7fb468bd738`.
- Source06 receipt: `6477b996fc28f7d270ba72c10208dc9e1e9fa792504d93ce79ab1c5ae8f0ff1b`.
- Patch: `bc9517e50c1bdc6777f3e6011b6440ff91c1cdaa427439e2e9501d00406e720f`.
- Link receipt: `feb74149ef3bfcbea88230004d7b35e2cb26e20a86d65cec7bd8dadf9e93c6a9`.
- Package receipt: `9c40f863c4255372c9a74008268b3b22a56a231618ae140aceaa92895838584c`;
  complete corresponding source, symbols/config and all 41 matching modules
  were preserved with archive readback. No firmware is included in source.
- Mesa 26.1.7-arch1.1, libdrm 2.4.134, libva 2.24.1, FFmpeg 9.0.1.

## Evidence and limits

Bounded UART session `725b9f3e100a4a6a9434538d5e1c5183` completed continuously,
epoch 1, no continuity errors, offsets 2606131..2872535 (266,404 bytes),
11:37:55.002186–11:47:36.858074 UTC (581.855888 seconds). It contains the exact
kernel banner, `PS4_UVD_RUNTIME STARTED`, `QUEUE_READY` and normal userspace
startup. No runtime closure, GPU reset/fault, timeout or panic was observed.
Read-only status/history sampling has no usable global VM-fault counter and
cannot exclude every possible unreported fault.

Private workspace evidence is under
`lab/uvd-2026-10-20/runtime/handoff/EXP-20261020-006/runtime/`:

- Parent `RESULT.json`: `300526d258751c3b0a0be0cef07b510fb2972471c4696dc8acc11a7514fe3d3a`.
- Application `playback-01/RESULT.json`: `30a42789e704c00c2635d5a6eb9f702346377eb40a6ebf080b27cbaffeb7972c`.
- Raw UART SHA256: `4d4279ed430429c684324561ac4647f39545a548aad26bc9b3684a54533bd525`.
- Independent review receipt: `c677d989005a1097112653ed2a1ccdb51e2f45ed60fdc4b51e2a5b3f38e07f43`.

Runner receipts deliberately leave `hardware_acceptance=false`: the parent
separately binds actual completed UART continuity, installation/running identity
and pixels. PREFLIGHT.json retains its intentional pre-run FAIL_DECODE placeholder;
final RESULT/EXECUTION/log files contain the completed pass. Raw captures, device
identifiers, decoded pixels and binary packages stay private outside Git.

The first result did not qualify visible mpv presentation or broader workloads.
Subsequent results on the same kernel/boot are recorded below. Seeking/profiles,
long-duration playback and at least three successful cold boots of the same
frozen matching artifact set remain ahead.
Other codecs and VCE are unqualified. The backend retains DMA allocations after
entry and blocks reset, unload, PM and ordinary shutdown cleanup. Full power-off
is the recovery; this result is not general release/support or first-ever novelty.


## Later exact1080p decode and controlled playback

No kernel production code or boot artifact changed for these experiments.
An isolated userspace Mesa26.2.5/Liverpool candidate was built and tested;
the installed Mesa/compositor remained unchanged. EXP011/012 corrected the
previously reproduced rawNV12/OpenGL stripes in the captured path, including
owner confirmation of the reference picture/text. The exact causal Mesa hunk
is not isolated because upstream version and PS4 graphics setup changed together.

EXP012 repeated the original120-frame canonical fixture with every byte exact.
EXP013 forced FFmpeg VAAPI/hwdownload for900 native1920×1080 H264 frames:
all frame MD5s/sizes and the whole2,799,360,000-byte NV12 SHA256 matched
`a36c73d7acf80b7aedb44f7af75069b1494abbc6c39838e0755db3a4f2a4e48f`.
Clip SHA256 `1ad9dc8b54545775a761628af471099a9d9df7b1b6d3f057feaba0acef10e0e5`.
Its56.061952sec includes CPU readback/hashing and is not a playback benchmark.
The subsequent mpv launch aborted after CS rejection(-28); this failure is
preserved separately from the passing exact decode. Hardware thread count
actually defaulted to4 despite the software-thread option requesting1.

EXP014 used mpv's separate hwdec-threads=1 option, confirmed by its actual
request log. Same clip, kernel, boot and isolated Mesa. The completed bounded
prefix contains five full30-second loops, zero display/decoder drops across
164samples, PTS/wall playback speed0.997509x and5130 UVD fence completions.
Actual decoder remained vaapi-copy, software fallback disabled; the compositor
screenshot shows clean picture and text. Physical monitor confirmation of
this moving1080p clip remains pending. Later ongoing filming time is unqualified.

Candidate manifest SHA256 `b8ea4288485b0a06ba5405fc852d73c88be5d15493af2e6a7224f9ef63438cca`;
Gallium SHA256 `763ed6e69a955262ba3e475598affa9a3f438b4aac1cb7e0cb7944ca27dd7353`.
Completed UART sessions: EXP013`28c08b4f08c3434a9da4ba1836a0966a`,
EXP014`866f1c41dc244fa5b3c5a611da643b58`, same epoch1 generation as EXP006.
Private parent RESULT hashes respectively
`30c830bf06e83a2380b8b7ea09b0a554f7b5dcc5e85fec990ca9c06d1f8557b7` and
`224175b1d84620a2c872fcad7d0f2d679f6b65fb947c4d5dbfa624c31538c0e8`.
Private artifacts live under `lab/mesa-uvd-2026-10-20/handoff/EXP-20261020-013/`
and `EXP-20261020-014/`; the curated workflow is in the sibling distribution
repository's PS4 experiment ledger. No raw capture, video or binary is versioned.

Kernel admission ENOSPC is pre-publication/nonterminal; outstanding1080p DPB
leases can exhaust the aggregate budget. This is a source-plausible explanation
for the earlier four-thread failure, not proof of its exact rejected guard.
No quota increase, reset, rearm, unload or clock tuning was used. The healthy
player/monitor was left looping under the owner's explicit filming request.
This is one AVC ConstrainedBaselineL4.1 clip on one boot; no HEVC,4K,1080p60,
seeking, VCE, full desktop or release qualification follows from it.
