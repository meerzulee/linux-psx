# Diagnostix 0.15 successful boot record

Status: **one direct-send boot and one FPKG app-driven boot observed** on
2026-11-08. Both reached Linux, the read-only RAM root, systemd and login
prompts. This is not repeated-boot, menu, network, graphics, UVD, export or
power acceptance. The 0.14 payload is known broken and must not be installed.

## What was wrong

- The 0.14 loader was 401,680 bytes because `objcopy -O binary` embedded the
  361,208-byte zero-filled kexec `.bss` in the outer ELF. GoldHEN received all
  bytes but its launcher faulted in `ScePartyDaemon` before any
  `DIAGNOSTIX_*` marker (EXP-20261107-036).
- The app's `Bundle::boot` rejected handoff when the reported `SO_SNDBUF` was
  smaller than the loader. That prerequisite was unnecessary because
  `socket_send_all` already completes short and interrupted writes.
- GoldHEN's PayLoader on 9090 was closed. An empty TCP connect produced
  `[GoldHEN] <payloader> Error handling payload` and stopped the service; a
  real payload then got `Connection refused`.
- The 0.15 app's first Prepare failed with
  `Namespace path=/data/diagnostix/versions/0.15 reason=owner/directory check
  refused errno=13` because `/data/diagnostix/versions` was `0775 0:0` and the
  app could not create the new version directory.

## What fixed it

- The 0.15 loader is 41,320 bytes, SHA256
  `bf030db3c3c7f7668947074e0d8eca29d604a5bf1fc3cbbda677ffc6970ee5d6`.
  It embeds the 21,536-byte non-BSS kexec image, allocates the full
  382,748-byte runtime extent, zeroes the 361,208-byte BSS, restores the
  4-byte upstream footer, then jumps. Host build reconstructs the exact
  382,748-byte blob byte-for-byte.
- `Bundle::boot` now uses one connection and the complete-write loop without a
  socket-buffer-size prerequisite.
- The package `diagnostix-ram-alpha1-0.15.pkg` is 460,455,936 bytes, SHA256
  `54f33a294ff06f2feb2ba3fff287e43a10df9eb02ea9ade2ea95b6660e09496f`.
- GoldHEN v2.4b18.9 is the active `/user/data/payload.bin` (291,808 bytes,
  SHA256 `ab1849d66816a9f4a3d155b06b51cdc5eb07a7fc5bd4333c90e3af74f802b2b2`).
  v2.4b18.10 is retained as `/user/data/payload.bin.2.4b18.10`,
  `/user/data/payloads/goldhen_v2.4b18.10.bin` and
  `/user/data/payloads/goldhen_v2.4b18.9.bin` for v2.4b18.9.
- `/user/data/diagnostix/versions` was temporarily set to `0777` for the
  first app Prepare. Restore it to `0775` at the next Orbis access if the
  policy requires the normal mode.

## Reproducible boot procedure

1. On Orbis, install
   `/user/data/pkg/diagnostix-ram-alpha1-0.15.pkg` and launch Diagnostix 0.15.
2. For the first Prepare only, make the version parent writable by the native
   app:
   `SITE CHMOD 0777 /user/data/diagnostix/versions`.
3. Select **Prepare Diagnostix** and wait for
   `Installed candidate verified. Ready to boot.`
4. Confirm the six files under
   `/data/diagnostix/versions/0.15/boot/`; the loader must be 41,320 bytes
   with SHA256 `bf030db3c3c7f7668947074e0d8eca29d604a5bf1fc3cbbda677ffc6970ee5d6`.
5. Enable GoldHEN **PayLoader Server** on 9090. Do **not** test it with an
   empty TCP connection.
6. Start continuous UART:
   `cd ps4-uart && ./uart start --port /dev/ttyUSB0`; `./uart status` must
   say READY.
7. Select **Boot Diagnostix** once. Alternatively, send the same
   `loader.elf` from the host on one connection. Do not retry a failed send.
8. Watch for:
   `[diagnostix] handoff: all 41320 loader bytes written on one connection`,
   `DIAGNOSTIX_PAGES_V1`, `Run /init`, `loop0`, `EXT4-fs (loop0)`,
   `Hostname set to <psx-diagnostics>` and `Reached target Login Prompts`.

Do not use an empty 9090 connection as a readiness check. It kills the
PayLoader service and forces a GoldHEN toggle or reload.

## Observed timings

Direct host send:

| Milestone | Seconds after Linux start |
|---|---:|
| `Run /init` | 10.518384 |
| `loop0` capacity change | 93.844443 |
| ext4 RAM root mounted read-only | 93.854327 |
| systemd hostname `psx-diagnostics` | 111.477167 |
| `Reached target Login Prompts` | 111.994828 |

FPKG app Boot (second boot):

| Milestone | Seconds after Linux start |
|---|---:|
| `Run /init` | 10.271769 |
| `loop0` capacity change | 93.491616 |
| ext4 RAM root mounted read-only | 93.501589 |
| systemd hostname `psx-diagnostics` | 111.153802 |
| `Reached target Login Prompts` | 111.677004 |

No kernel panic or Oops was recorded. The app's own handoff line was:

```text
[diagnostix] action: Boot selected; entry
[diagnostix] phase: Check this launch's verified snapshot
[diagnostix] phase: Send verified loader once
[diagnostix] handoff: all 41320 loader bytes written on one connection
[diagnostix] result: Loader sent once. UART reports preparation and boot results.
```

## Boot is slow by design

The 1,000 MiB ext4 root is gzip-compressed inside the initramfs and inflated
into a 1,088 MiB RAM tmpfs on every boot. The `/init` script runs
`gzip -dc rootfs.ext4.gz > backing` and then hashes the inflated 1 GiB image
again. That dominates the ~66 s between `/init` and `loop0`; full systemd and
Wi-Fi scans add another ~18 s. The payload is not the bottleneck.

More RAM, or moving the image “to RAM”, does not help: the compressed root and
the destination tmpfs are already RAM-resident. The measured rate is
1,048,576,000 bytes / 66.4 s ≈ 15.8 MB/s, which is single-threaded gzip
decompression, not storage. Pre-inflating the root in Orbis and passing a 1 GiB
buffer to Linux would move the same CPU work earlier and needs a reserved-memory
or second-initrd handoff outside the current 1 GiB boot-set / 512 MiB initrd
contract. zram still requires the image to be decompressed before writing and
mainly saves memory, not time.

Acceleration options, in order of impact:

1. **Squashfs/EROFS root**: mount a compressed read-only root directly instead
   of inflating ext4 into tmpfs; removes the ~66 s inflation. Requires a kernel
   rebuild because the active config has `CONFIG_SQUASHFS` and
   `CONFIG_EROFS_FS` unset, plus a new root image and FPKG.
2. **zstd/lz4 root leaf plus streaming hash**: hash while decompressing
   (`decompressor -dc | tee backing | sha256sum`) instead of a second 1 GiB
   read; expected inflation around 15-25 s. BusyBox has only gzip, so the
   initramfs needs a small compressed-root decompressor.
3. **lz4 outer initramfs**: `CONFIG_RD_LZ4=y` is already set; compressing the
   outer CPIO with lz4 speeds the kernel initramfs load without a kernel
   rebuild.
4. **Defer Wi-Fi/NetworkManager and start the controller earlier**: the systemd
   phase and repeated MT7668 scans account for roughly 18 s.
5. **Shrink the 1,000 MiB root**: proportional reduction, but browser/UVD
   fixtures are part of the size.

## Evidence

- Direct-send receipt: `/var/tmp/send-015-loader-008.json`.
- Probe receipt: `/var/tmp/probe-9090-007.json` (16,240-byte read-only pool
  observer, `PSX_POOL_V1`, `status=0`, `reads=253`, `no_boot=1`,
  `orbis_remains_running=1`).
- GoldHEN replacement receipt: `/var/tmp/replace-goldhen.json`.
- UART contexts:
  `ps4-uart/sessions/20261108_014513_884727-exp-20261108-008-*` and the
  continuous-capture follow-up after it.
- Concise ledger entries: `diagnostics/SESSIONS.md` EXP-20261108-008 and its
  follow-up.

## Scope limits

This record supports the 0.15 payload and the app's Prepare/Boot handoff on
this console. It does not establish repeated boot stability, menu interaction,
SSH/network, browser, UVD, report export, power behavior or full appliance
acceptance. Do not install or boot 0.14; its 401,680-byte loader is the
observed GoldHEN launch fault.
