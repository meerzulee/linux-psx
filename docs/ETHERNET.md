# Baikal MTS Ethernet

`CONFIG_PS4_MTS=m` builds the GPL driver `psx_mts_net` for the Baikal
`104d:90d8` Ethernet function. It exposes a Linux netdevice named `psx0` and
has no desktop, distribution, init-system or network-manager dependency. The
active target carries the unchanged MTS2 patch plus the Linux psX native
autoload patch `0700-network-mts/0002`.

## How it starts

- The PCI ID table is exported, so udev loads the module at boot like any
  on-board NIC driver. No setup tool, service or profile is needed.
- `psx0` is registered from the module `MODULE_STATE_LIVE` notifier instead of
  `probe`, so nothing can open it while module initialization may still fail.
- The first open publishes the single retained DMA generation (`autoarm=1`,
  the default). A network manager may bring `psx0` up with or without a cable;
  carrier is sampled about every 250 ms, so a later plug is reported like any
  other NIC link change.
- Probe, fresh-baseline and first-open checks are unchanged. The driver checks
  `0000:00:14.1` (`104d:90d8`) and glue function `0000:00:14.4` (`104d:90db`),
  direct coherent 32-bit DMA, PCI state and the inherited MAC/ring/PHY
  baseline. One publication per boot, no unload after publication, no suspend,
  the inherited permanent MAC and MTU 1500.

To turn it off:

- `psx_mts_net.autoarm=0` on the kernel command line keeps the explicit gate:
  the module loads and `psx0` appears, but only a root write of `1` to
  `/sys/module/psx_mts_net/parameters/allow_open` after fresh-boot checks lets
  the first open publish.
- `modprobe.blacklist=psx_mts_net` skips loading entirely.

## Distribution notes

NetworkManager manages `psx0` without configuration and connects with its
default wired profile. systemd-networkd, ifupdown and similar tools need a
configuration that names `psx0`; it does not match the common `en*`/`eth*`
patterns. The driver has no `ndo_set_mac_address` or receive-filter callback,
so a manager that randomizes wired MAC addresses fails activation. Leave the
wired MAC as `preserve`.

## What changed in the earlier investigation

The investigation used public Linux source, local PS4 research and UART plus
bounded packet tests. Allocation-only probes separated CPU metadata from
device publication. A retained packet test timed out with no proven TX
completion; later work corrected the publication/descriptor model and tested
a fresh DMA generation. PHY state also mattered: local evidence found the
MMD device 30 register `0x0147` bit `0x0010` set; clearing it restored link in
the tested sequence. The inspected native shutdown/suspend path writes that
bit; no explicit matching native-init zero write was established.

The evidence does not isolate that PHY bit as the sole cause of the historical
TX failure: the DMA generation and software changed too. MTS2 then retained
one generation and added continuous polling plus synchronized admin cycles.
Historical DHCP, bidirectional data and HTTPS evidence is summarized in
[the PS4 status](../platforms/ps4/STATUS.md). Raw private firmware, mappings,
packet captures and console logs are not copied into this repository.

## Retention and limits

MTS2 polls at 1 ms and samples native link state periodically (about 250 ms).
Once published, code, coherent DMA buffers, mappings and PCI/glue references
are retained until power-off. Ordinary healthy admin down/up changes CPU
admission, retaining the same generation; polling can reclaim TX and discard
RX while down. A terminal fault stops and permanently retains state.

Do not unload, force-unload, reset, unbind, clear overrides, rearm, suspend or
load historical experiment modules over retained state. Stop acknowledgement
does not prove DMA drain. IRQ mode, offloads, configurable PHY operation,
other console revisions and repeated-boot reliability remain unqualified. The
source and kernel checks deliberately refuse unsupported states instead of
offering automatic recovery.

## Hardware status

The active series' ordered patch set equals that of local build
`6.18.44-psx-ps4-baikal-native-dev1`, whose config differed only in the
local-version string. On one Baikal console, with no userspace Ethernet
adapter installed:

- EXP-20261017-019: launched with the cable connected. udev loaded the
  driver, NetworkManager's default `Wired connection 1` made the first open,
  and DHCP, hash-checked 4 MiB transfers in both directions and
  interface-bound HTTPS passed. One unplug/replug recovered, and the link
  still worked beyond 900 seconds.
- EXP-20261017-020: launched without the cable. The first open published with
  no link; plugging the cable in brought carrier, DHCP and the same traffic
  checks with no user network action.

Both boots had 0 errors and completed UART sessions with no fault lines. The
owner set the promotion count for native autoload at these two boots. They are
evidence for this patch set, not acceptance: the `6.18.44-psx-ps4-baikal2` CI
binaries have not been booted. Not tested: Orbis Ethernet after a native Linux
boot, `ip link` administrative down/up under NetworkManager, other network
managers, repeated cold boots and other consoles.
[The target provenance](../provenance/6.18-ps4-baikal.json) records the exact
hashes.

## Observing traffic

These commands are read-only apart from the explicit test traffic:

```sh
uname -r
ip -br link show dev psx0
ip -s link show dev psx0
ip -4 route get 1.1.1.1
sudo cat /sys/module/psx_mts_net/parameters/report
curl --interface psx0 --noproxy '*' --fail --max-time 30 https://example.com/ -o /dev/null
ip -s link show dev psx0
```

Carrier/address alone is insufficient. A successful interface-bound HTTPS
request and advancing TX/RX counters demonstrate real traffic; full acceptance
also needs independent hash-checked transfers, longevity and lifecycle tests
under [HARDWARE-TESTING.md](HARDWARE-TESTING.md). Redact MAC/IP/serial details
before sharing output.
