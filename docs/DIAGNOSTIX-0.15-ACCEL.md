# Diagnostix 0.15 accelerated RAM-root candidate

Status: **UI5 setup-first package built and uploaded; console install pending.**
Setup is the first screen, Start runs the full suite, every result is appended
live to `/run/psx-state/full-test-report.txt`, and the summary offers Submit.
The 8-way hash boot measured 61.1 s to login. The working 0.15 gzip boot set
remains as rollback.

## Why

The 0.15 gzip boot spent about 66 s inflating the 1,000 MiB ext4 root. The
first zstd candidate cut that to 47.6 s, but measurement showed the decoder was
not the limit: SHA-256 ran at roughly 26 MB/s on the Jaguar CPU. The new init
hashes the inflated root with eight concurrent SHA-256 processes, one per CPU.

## Inputs and identities

| Item | Value |
|---|---|
| Source 0.15 rootfs | 1,048,576,000 bytes, SHA256 `b5b3a0ec3001fa99816ad68071d93091a6c8d0907aeb4ba384094625b7af0107` |
| Old gzip leaf | 402,066,324 bytes |
| New zstd leaf | **364,392,081 bytes**, SHA256 `12aeb1494c34fac4c2909660df4adaac3991e3cf7cbc34db9125e009ca00adbb` |
| Static zstd decoder | 1,040,040 bytes, SHA256 `e5a7f69aeda5c608d516706baa40cb5d59febe01b4c259e753f1f1b90e340ac7` |
| UI5 initramfs | **408,298,384 bytes**, SHA256 `a55de3d6e8839ab39600c7837498e8f83703e6eb50f3c9af7d3b96da717ba289` |
| UI5 package | **424,017,920 bytes**, SHA256 `04b302ae661053aa4a5d43eacfbe4f38bb7e1ab821124e2c36cfe54612006f5e`, 24 members |
| Kernel | unchanged, SHA256 `a7264426ee3b2ffeb030a04bbd7ab070418ecf4c0272b83400b4d5f45473bff4` |
| Loader | unchanged, 41,320 bytes, SHA256 `bf030db3c3c7f7668947074e0d8eca29d604a5bf1fc3cbbda677ffc6970ee5d6` |

The superseded accelerated initramfs (`34cfda12…`) and package (`2ed98032…`)
reach the BusyBox rescue; the first corrected pair (`ce0ed5ea…` /
`71bb60b5…`) boots but still spends two single-threaded SHA-256 passes. The
parallel-hash pair (`40e59e59…` / `4c49ba0b…`) boots in 61.1 s but predates the
flat Tests screen. The UI2 pair (`adcbea0c…` / `124a7d4f…`) has the flat list
but predates the core-first order. The UI3 pair (`0810cb39…` / `0b6dad1d…`)
has the order but auto-opens the issue report during a run. The UI4 pair
(`be4af856…` / `1d7beb40…`) has run controls but predates the setup-first
screen, live report and Submit action. Do not reuse an earlier pair. Their
history is recorded in `diagnostics/SESSIONS.md`.

The zstd source is pinned:

```text
https://github.com/facebook/zstd/releases/download/v1.5.7/zstd-1.5.7.tar.gz
SHA256 eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3
```

`scripts/build-zstd-dec` generates the single-file decoder and compiles
`diagnostics/ps4/zstd-dec.c` statically with baseline x86-64 flags. The BSD
notice is packaged as `LICENSES/zstd-BSD.txt`.

## What changed

- `rootfs/ps4-diagnostics/init-ram` now dispatches on the embedded
  `ROOT_COMPRESSION` value from `/etc/psx-ram.env`:
  - `zstd` -> `/bin/zstd-dec`
  - `gzip` -> the existing BusyBox `gzip -dc`
  - anything else -> rescue, with no silent fallback
  The first accelerated image probed the magic with `od`, which this rescue
  BusyBox does not ship; the empty result silently selected gzip and failed on
  the zstd leaf. The env contract removes that probe. Older gzip images remain
  bootable by setting `ROOT_COMPRESSION='gzip'`.
- The RAM initramfs replaces `psx-ram/rootfs.ext4.gz` with the zstd leaf,
  adds `/bin/zstd-dec`, and updates `COMPRESSED_BYTES`/`COMPRESSED_SHA256`
  plus the embedded `psx-ram-contract.json` boot-source values.
- The inflated root is verified as eight 125 MiB SHA-256 chunks, run
  concurrently, one process per CPU. `ROOTFS_CHUNK_COUNT`, `ROOTFS_CHUNK_MIB`
  and the eight pinned digests live in `/etc/psx-ram.env`. Each chunk is still
  cryptographically pinned; the concatenation is the same 1,000 MiB image.
- The compressed-leaf SHA-256 pass is removed. The inflated chunk digests
  already prove the final image, so a second hash of the compressed input only
  added startup time. Its size pin and the zstd frame check remain.
- The outer initramfs remains gzip, still below the loader’s 512 MiB initrd
  limit and the 1 GiB boot-set cap.
- The FPKG remains version 0.15 with the same app and loader. The new
  initramfs pin is compiled into the app; the old 0.15 boot set is retained as
  `previous-<manifest hash>` after Prepare.

## TUI changes in this candidate

- The System area is now one **flat Tests list**. Every controller test is
  shown with a status glyph, area, kind, id, description and the latest
  message; manual, deferred, unavailable and blocked tests stay visible with
  the reason instead of disappearing into area menus.
- Both the list and **Run all** use one explicit priority order: inventory,
  CPU, RAM, Ethernet, OpenGL/Vulkan information and renders, audio, storage,
  UVD, issue report, then the browser last. The browser is no longer queued
  before Network/Audio/Storage, which previously made Run all stop early.
- **Run all tests** queues every runnable diagnostic and now continues past
  FAIL, BLOCKED, UNSUPPORTED, NOT_RUN and inconclusive results. It records the
  counts and finishes the queue; only a terminal `stop_required` result or a
  lost controller stops it. It still does not retry a previously failed test.
- The first screen is **Setup · optional**: Ethernet, Wi-Fi and optional SSH,
  followed by **Start all tests**. Start closes Setup and launches the full
  suite automatically; no per-test selection is required.
- A run no longer opens the issue report when `support.export` finishes.
  Every result is appended live to `/run/psx-state/full-test-report.txt` while
  the run is still in progress. During a run, **C** or **Esc** cancels the
  queue, **S** saves the final summary, and **V** shows the run summary.
- When the run ends, a **Full test summary** opens with passed/failed/
  observation counts, notes and the save path. **U** opens or creates the issue
  report ZIP for submission, **R** retries the full run after a required
  “what changed” reason, **S** saves the report, and **Esc** closes.
- Tests that need a URL, an operator confirmation, a graphical workspace or a
  UART session are marked manual in the list and keep their dedicated flow.
- **Q opens the Power page** instead of the old exit dialog. The footers now
  read `Q Power`. Power actions remain confirmed Shutdown / Reboot to Orbis.
- `INCONCLUSIVE` is labelled **Inconclusive** instead of **Observed**, so an
  observation-only result is not presented as a finished functional test.
- The Rust TUI suite passes 79 tests.

## Measured effect

The 2026-11-08 boot measured `/init` at 10.661 s, inflation at 17.296 s,
`loop0` at 42.961 s, hostname at 60.590 s and login prompts at 61.104 s.
Inflation is 25.7 s (was 47.6 s with one-pass zstd; 66.4 s with gzip) and
total boot is 61.1 s (was 91.6 s; gzip was 111.7 s). The eight hash jobs
scaled about 2.3x rather than linearly, so the 1 GiB integrity pass still
costs about 16.7 s. The systemd/Wi-Fi phase (~17.6 s) and the
kernel/initramfs phase (~10.7 s) are unchanged. Further reduction would
require dropping or replacing that integrity pass. This is a measured result
for this image, not general hardware acceptance.

## Test procedure

1. Reboot to Orbis and upload this package to
   `/user/data/pkg/diagnostix-ram-alpha1-0.15-accel8.pkg`.
2. Install it over the existing 0.15 app, launch Diagnostix 0.15 and select
   **Prepare Diagnostix**. The new initramfs is copied and the previous 0.15
   generation is retained.
3. Start continuous UART before Boot.
4. Select **Boot Diagnostix** once.
5. Record the same milestones as the 0.15 boot: `Run /init`, `loop0`,
   `EXT4-fs (loop0)`, `Hostname set to <psx-diagnostics>` and login prompts.
   Compare them with the 10.3/93.5/111.2/111.7 s reference.
6. Stop on a decompression, checksum, mount or kernel fault; do not retry an
   unchanged image.

Rollback: the previous 0.15 boot generation and the original 0.15 installer
remain available; `/init` still supports the gzip leaf.
