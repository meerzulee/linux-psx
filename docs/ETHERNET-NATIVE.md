# Native Baikal MTS Ethernet autoload (development)

`0700-network-mts/0002-net-ps4-mts-native-autoload.patch` makes `psx_mts_net`
behave like an ordinary on-board NIC driver. It is applied only by the
development target `6.18-ps4-baikal-native-dev`
(`6.18.44-psx-ps4-baikal-native-dev1`): the active `6.18-ps4-baikal` series
plus this patch, with only the local-version string changed in the config.
The active release and its published identity `6.18.44-psx-ps4-baikal1` are
unchanged. [Its provenance](../provenance/6.18-ps4-baikal-native-dev.json)
records the patch, series and tested build hashes.

Build it with `make TARGET=6.18-ps4-baikal-native-dev` on Linux x86-64 or
`./scripts/build-kernel-orbstack -t 6.18-ps4-baikal-native-dev` on macOS ARM64.
CI builds only the active target, so this target has no download.

## Behaviour

- The PCI ID table (`104d:90d8`) is exported, so udev loads the module at boot
  on any distribution. No userspace adapter, service or profile is required.
- `psx0` is registered from the module `MODULE_STATE_LIVE` notifier instead of
  `probe`. Nothing can open the interface while module initialization may
  still fail, so both existing LIVE checks keep their meaning.
- With `autoarm=1` (default) the first open publishes the single retained DMA
  generation itself; no `allow_open` write is needed. A network manager may
  bring `psx0` up with or without a cable; carrier is sampled every 250 ms, so
  a later cable plug is reported like any other NIC link change.
- Probe, fresh-baseline and first-open checks are unchanged: one publication
  per boot, no unload after publication, no suspend, permanent inherited MAC
  and MTU 1500.

## Turning it off

- `psx_mts_net.autoarm=0` on the kernel command line keeps the old explicit
  gate: the module loads, `psx0` appears, and only an `allow_open` write arms
  the first open.
- `modprobe.blacklist=psx_mts_net` skips loading entirely.

## Relationship to `ps4-ethernet`

`contrib/networkmanager/ps4-ethernet` supports only the releases in its
`RELEASES` table and refuses every other kernel. Native releases are not added
to that table: they need no adapter, and its NetworkManager handoff would race
the network manager that already owns `psx0`.

## Distribution notes

NetworkManager manages `psx0` without configuration. systemd-networkd,
ifupdown and similar tools need a configuration that names `psx0`; it does
not match the common `en*`/`eth*` patterns. The driver has no
`ndo_set_mac_address` or receive-filter callback, so a manager that randomizes
wired MAC addresses fails activation. Leave the wired MAC as `preserve`.

## Hardware status

EXP-20261017-019 passed once on one Baikal console: a fresh boot from Orbis
with the cable connected. udev loaded the module, `psx0` registered after
module LIVE, and NetworkManager's default `Wired connection 1` made the first
open with no userspace Ethernet adapter installed. DHCP, hash-checked 4 MiB
transfers in both directions and interface-bound HTTPS passed. One
unplug/replug then recovered with a new lease and matching transfers. Errors
stayed at 0 and the bounded UART session completed with no fault lines. This
is evidence, not acceptance.

Required evidence before any support claim, on a fresh boot from Orbis each
time:

1. boot with the cable connected: autoload, first open, DHCP, traffic
   (passed once in EXP-20261017-019);
2. boot without a cable, then connect it: link and DHCP without user action
   (next: EXP-20261017-020);
3. unplug and reconnect while running (passed once in EXP-20261017-019);
4. reboot back to Orbis: clean terminal stop and normal Orbis networking.

EXP-20261017-019 used NetworkManager's generic default wired profile (IPv6,
MAC policy, multicast); a console with a tailored profile is a separate case.
One successful boot is evidence only; promotion follows the active acceptance
count.
