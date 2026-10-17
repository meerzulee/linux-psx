# Baikal Ethernet: what the investigation established

This is the kernel-side research handoff from the old PS4 project. The active
module and integration contract are in [ETHERNET](../../../docs/ETHERNET.md).
The full older [bring-up account](https://github.com/meerzulee/linux-ps4/blob/386df1ad97d92defee1794bf961a0e2f5dc85a1b/docs/BAIKAL-ETHERNET-BRINGUP.md)
and [handoff record](https://github.com/meerzulee/linux-ps4/blob/386df1ad97d92defee1794bf961a0e2f5dc85a1b/docs/BAIKAL-MTS-HANDOFF.md)
are pinned historical sources, not instructions to replay the old probes.

## Controller identity and implementation

The measured controller is Sony PCI `104d:90d8` at `0000:00:14.1`, with
Baikal glue `104d:90db` at `0000:00:14.4`, on one Baikal B1 console.
`psx_mts_net` is the project's original GPL-2.0-only direct MMIO/DMA Linux
driver. It is not a `sky2`, Linux `stmmac`/DWMAC or RMU driver. Its exact
physical MAC IP lineage is not established by the measurements.

The Linux MediaTek STAR driver supplied descriptor/register comparisons and
networking examples. PHY0 reported `03a29441`, which the pinned upstream
MediaTek PHY driver identifies as MT7531 PHY. That PHY identity does not
establish the identity of every integrated MAC block:

https://github.com/gregkh/linux/blob/1efe5d048a391de3ead2804b2e7f86376c356cc5/drivers/net/phy/mediatek/mtk-ge.c

https://github.com/gregkh/linux/blob/1efe5d048a391de3ead2804b2e7f86376c356cc5/drivers/net/ethernet/mediatek/mtk_star_emac.c

The historical `sky2`/Yukon and later DWMAC BAR-remapping theories did not
produce the current working transport. Do not re-enable those old probes or
present either hypothesis as a proven controller identification.

## What changed between failed TX and packet proof

An early retained packet harness reported TX-completed=0 and
RX-reply-matched=0. Later buffer inspection found peer DHCP-shaped traffic in
RX memory while TX remained stalled. Thus receiving had progressed before
normal two-way Ethernet worked. Stop acknowledgement did not prove DMA drain,
so the experiment retained all published allocations, references and code.

Private ps4-re scans helped locate native MDIO command phases, PHY status,
descriptor ownership and MAC/DMA operations. Those observations suggested
bounded measurements; neither private firmware nor proprietary implementation
was imported into this kernel. Raw PUP/kernel images, mappings, packet/UART
captures and host-specific operational runners remain outside the repository.

The tested link prerequisite was PHY0 MMD30 register `0x0147`:

| Measurement | Before EXP012 correction | After correction |
| --- | --- | --- |
| PHY ID / page | `03a29441` / `0` | unchanged |
| BMCR | `1040` | unchanged |
| MMD30:0147 | `0010` | `0000` |
| BMSR | `7969` | `796d` |
| MAC status at BAR0+04 | `b18` | `b19` |

The inspected native suspend/shutdown path writes `0010`. No explicit
matching normal-init zero write was established in the bounded source audit.
The Linux correction clears the measured bit after establishing the exact
supported identity and stopped/masked state, before publishing DMA. It does
not replay another PHY's firmware/init sequence or force speed/advertisement.

EXP013 also used fresh separately retained DMA buffers. PHY correction and
generation/handover changes are causal confounders: the evidence establishes
a missing link prerequisite, not that one bit was the sole TX-failure cause.

## Evidence ladder

| Experiment | Observed result | Boundary |
| --- | --- | --- |
| EXP-20261017-002 | No proven TX completion or matched RX reply; acknowledged stop | Published state retained; no successful transport claim |
| EXP003/004, EXP009–011 | Earlier RX evidence, native link down, exact inherited PHY state | Diagnostic observations only |
| EXP012 | Narrow `0010→0000` correction restored native link-status indication | No Ethernet packet acceptance by itself |
| EXP013 | One TX completion; independently captured fresh 60-byte nonce; exact matching reply in PS4 RX; stop acknowledged | Both endpoint evidence and completed UART; drain/resetless replacement not qualified |
| EXP014 | Fresh-boot polled netdevice DHCP and independent hash-checked 4 MiB transfers each way; TX3297/RX3039, zero reported errors/drops | First Linux transport result, including ring wrap; single-open test retained afterward |
| EXP015 | `6.18.44-ps4-baikal-mts1`: modem DHCP, preferred wired IPv4, 4 MiB each way and certificate-verified public HTTPS | Bounded single-open integrated-kernel result; not continuous-lifecycle acceptance |
| EXP016 | `6.18.44-ps4-baikal-mts2`: same transport checks, three NM profile reconnects, later HTTPS and wired SSH beyond 900 seconds | **DEGRADED overall**: initial startup failed, strict admin packet-gating helper failed/inconclusive, unattended correction and cable recovery pending |

For EXP014–016, UART continuity completed and transport evidence was checked
at both endpoints. A peer's injected reply alone was never counted as target
RX delivery. Hash-checked traffic, carrier, DHCP, source tests and absence of
UART fault signatures answer different questions.

## Why MTS2 retains a generation

MTS1's timed single-open lifetime stopped and retained published state. MTS2
removes the fixed lease and uses one permanent code/DMA/map/reference
generation per boot. It has 256 descriptors per ring, coherent checked
32-bit extents, fixed 1536-byte bounce buffers, BQL accounting and NAPI driven
by a 1 ms timer. Native carrier sampling runs about every 250 ms. Healthy
administrative down/up gates CPU packet admission while retaining the same
generation and continuing background reclaim/recycle. Terminal faults stop
and retain irreversibly.

**Do not unload, reset, unbind, rearm or free a published instance.** A stop
acknowledgement is not a DMA-drain proof. IRQ operation, suspend, offloads,
reset/recovery, cable recovery and other hardware revisions remain unqualified.

The first MTS2 boot also exposed a userspace handoff defect. NetworkManager's
early gate-closed UP was refused; opening the gate afterward did not make NM
raise the still-unpublished interface. One explicit first UP while NM was
blocked succeeded. The later distro activation correction performs and
positively verifies that first publication before returning management to NM.
Its unattended startup needs a fresh qualified boot; the historical EXP016
manual recovery does not prove it. This correction did not require another
kernel or DMA generation.

## Reproducible source checks

The active MTS2 patch preserves the original portable header, SHA-256
`d1e138130df0f7d63555704daa61846f478416a6f0e689809ab7fa3ee00a931b`.
`make check` compiles the original GPL test fixture against the header
**extracted from that active patch**, exercising DMA bounds/non-overlap,
descriptor ownership, all 256 slots, RX length/fragment/error handling,
handoff masks, full-ring backpressure and three TX wraps with exact byte
accounting. These host checks do not exercise physical DMA ordering or prove
the interpretation of un-injected hardware error bits.

The rewritten psX build is firmware-free and has a new release identity.
None of the historical results is acceptance for that rebuilt artifact.
Keep the whole matching module tree and artifact manifest together, then
follow [HARDWARE-TESTING](../../../docs/HARDWARE-TESTING.md) for new evidence.
