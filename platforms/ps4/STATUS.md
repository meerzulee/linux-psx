# PS4 Baikal status

## New psX target

`6.18-ps4-baikal` produces `6.18.44-psx-ps4-baikal1`: pinned Linux 6.18.44,
54 preserved PS4 patches and the unchanged MTS2 Ethernet patch. Its active
config embeds no firmware and uses a neutral psX release/build identity.
CI application/compilation and source packaging are host checks. **No hardware
acceptance has been performed for this new release/config/build.**

PS5 integration, other PS4 southbridges/revisions and the historical ultrawide
snapshot are not qualified by this migration.

## Historical MTS2 evidence

Source: `meerzulee/linux-ps4` at
`386df1ad97d92defee1794bf961a0e2f5dc85a1b`.
Hardware experiment: `EXP-20261017-016`, one PS4 Slim, Baikal B1, Orbis 12.02,
private kernel `6.18.44-ps4-baikal-mts2` with its recorded boot stack.

Observed in that historical build:

- Modem DHCP and preferred wired IPv4 routing.
- Independent hash-checked 4 MiB transfers in both directions and verified HTTPS.
- Three NetworkManager profile cycles and traffic beyond 900 seconds.
- A corrected distribution startup sequence working when manually invoked.

**Overall verdict: DEGRADED.** Initial automatic startup failed. Corrected
unattended startup, cable recovery, strict administrative packet-gating
acceptance, repeated cold boots, other consoles, IRQ mode, offloads, reset,
unload and suspend remain pending or unqualified. Stop acknowledgement is not
proof of DMA drain. Driver code and published DMA state remain retained until
power-off.

The historical private kernel embedded firmware. The later firmware-free
`linux-ps4` public CI build and this new psX build are distinct artifacts;
neither inherits the private build's hardware acceptance. Old module trees are
not matching modules for the psX release. No private firmware, raw console log,
packet capture, memory mapping or root image is published here.

The source migration preserves original patch bytes, authorship and licenses.
See [provenance](../../provenance/6.18-ps4-baikal.json),
[credits](../../CREDITS.md), [Ethernet contract](../../docs/ETHERNET.md) and
[hardware testing](../../docs/HARDWARE-TESTING.md).
