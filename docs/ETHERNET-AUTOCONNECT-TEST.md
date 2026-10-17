# Standalone Ethernet service: same-boot test

**EXP-20261017-017: PASS for service adoption and wired transport on the
already-running original MTS2 kernel.** This does not qualify fresh first
activation or an unattended boot of the new service.

- Hardware: the recorded PS4 Slim Baikal B1, Orbis 12.02 boot chain.
- Running release: `6.18.44-ps4-baikal-mts2`, with its existing complete module
  set and root-sealed original candidate metadata. No kernel, initramfs, module
  or boot files were replaced.
- Tested standalone tool SHA256:
  `cdde1893455e097df1366a83f12fd30593ff2d6a22ae0cf663b622857dd04779`.
- The current interface was already published, healthy and NetworkManager
  managed. `--setup` installed the adapter, metadata and service, enabled it
  and started it successfully. The service positively identified the existing
  state and reported `ALREADY INITIALIZED`; an explicit repeat did the same.
- The old Omarchy service was disabled for future startup only, leaving the
  current active/exited unit and its loaded driver intact. The new service
  finished active/exited, enabled, with exit status zero.
- A new SSH connection directly to the wired address verified the PS4 host key
  using the already-trusted console identity. Its actual server address and
  source-specific reply route were verified through `psx0`.
- Independent random **4 MiB files in each direction** matched SHA256 hashes
  across the wired SSH connection. TX and RX byte counters both advanced.
- An interface-bound kernel.org HTTPS request returned **HTTP 200**, successful
  TLS certificate verification and the wired local address.
- Before/after snapshots matched the boot, loaded module note, module/config
  hashes, profile hash, routes, four DMA extents, permanent reference count and
  admin open/close counts. Engine, polling and carrier stayed healthy; driver
  terminal/error/stop-error values stayed zero. Wi-Fi remained configured.

The first HTTPS URL (`/robots.txt`) returned HTTP 404 with valid TLS and the
wired source address. The runner restored the previous future-service setting;
the original error and rollback receipts were retained. Changing to the
homepage answered the HTTP test. No driver activation was repeated, and the
new future-service setting was restored only after all state/traffic checks.

Continuous UART remained on the same generation/connection epoch. Its bounded
session completed with 4,632 bytes, 67 compact-context lines and zero logger
events. Exact private snapshots, transfer receipts and UART artifacts remain
local; no raw console/network identifiers are published.

## Remaining scope

This is positive same-boot integration evidence for the original private MTS2
release. Synthetic host checks also cover the public MTS2 and new psX release
contracts. The new psX kernel has not been boot-tested. The new service's fresh
first activation, unattended repeated boot, cable recovery and additional
console revisions remain unqualified. Historical EXP016 remains DEGRADED.

Use [the setup guide](../contrib/networkmanager/README.md) for the one-time
installation command. The next hardware question is unattended startup at the
owner's next normal boot; this experiment requested no reboot or cable action.
