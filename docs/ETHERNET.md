# Experimental Baikal MTS Ethernet

`CONFIG_PS4_MTS=m` builds the GPL driver `psx_mts_net`. It exposes a Linux
netdevice normally named `psx0`; it has no desktop, distribution, init-system
or network-manager dependency. The patch is unchanged from the published
PS4 MTS2 source. The new psX build still needs its own hardware qualification.

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

## First-publication integration contract

The driver has no PCI modalias autoload. Loading it does not authorize first
packet publication. A distribution adapter must perform a reviewed sequence:

1. Verify the running release, resolved config, entire matching module archive
   and installed/loaded module build identity against `artifacts.json`.
2. Confirm fresh-boot state, no older retained experiment/driver generation,
   exact supported PCI identities/resources, and the bound `baikal_pcie` glue.
   The driver checks `0000:00:14.1` (`104d:90d8`) and glue function
   `0000:00:14.4` (`104d:90db`), direct coherent 32-bit DMA, PCI state and
   inherited MAC/ring/PHY baseline. Preserve these checks.
3. Prevent the chosen network manager from bringing the interface up before
   the preflight and gate. Keep the inherited permanent MAC and MTU 1500.
4. Permit one first-publication attempt per boot. The root-only `allow_open`
   parameter accepts the explicit LIVE gate after preflight; perform exactly
   one initial administrative UP, then require a positive driver report.
5. Hand the healthy interface to the selected network configuration layer.
   DHCP, addressing and preferred routes are distribution choices.

This protocol is not a universal activation script. The optional reviewed
Omarchy/NetworkManager adapter belongs to the companion distribution project:

https://github.com/meerzulee/omarchy-psx/blob/main/docs/USING-LINUX-PSX.md

Other distributions can implement the same driver/artifact contract without
using Omarchy or NetworkManager. Do not replace it with a blind autoload,
unconditional `ip link set up`, reset or retry loop.

## Retention and limits

MTS2 polls at 1 ms and samples native link state periodically (about 250 ms).
Once published, code, coherent DMA buffers, mappings and PCI/glue references
are retained until power-off. Ordinary healthy admin down/up changes CPU
admission, retaining the same generation; polling can reclaim TX and discard
RX while down. A terminal fault stops and permanently retains state.

Do not unload, force-unload, reset, unbind, clear overrides, rearm, suspend or
load historical experiment modules over retained state. Stop acknowledgement
does not prove DMA drain. IRQ mode, offloads, configurable PHY operation,
other console revisions, cable recovery and repeated-boot reliability remain
unqualified. The source and kernel checks deliberately refuse unsupported
states instead of offering automatic recovery.

## Observing traffic after approved activation

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
