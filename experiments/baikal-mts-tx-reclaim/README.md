# MTS TX word0 retention candidate

This GPL-2.0-only candidate removes exactly one write from the existing
`mts_tx_reclaim()` in `drivers/net/ethernet/psx_mts_net.c`:

```c
WRITE_ONCE(ring[slot].word[0], cpu_to_le32(mts_tx_idle(slot)));
```

The descriptor's returned word0 therefore remains intact until the next
submission assigns a complete replacement. Buffer allocation/retention, word2
sentinel assignment, ownership checks, software ledger, BQL accounting, polling,
MAC/PHY initialization and lifecycle guards are unchanged. There is no new
parameter, hardware read, reset, rearm, recovery or cleanup path. The original
driver copyright and GPL-2.0-only notice remain intact.

The parent selected a module-only `mts-tx-reclaim-net1` build: keep the frozen
runtime-dev6 kernel, release, config and ABI, rebuild only `psx_mts_net.ko`, and
package all 41 modules with the other 40 byte-identical. The new variant and
whole-module archive hash must distinguish it from frozen dev6. Do not replace
the module on a running retained generation; the parent owns a separate fresh
boot and exact image binding. Build success is not hardware acceptance.

## Source basis and uncertainty

The retained native FW12.02 completion review identifies the following raw
file offsets in the owner's private, pinned reference:

- `0x252ce2/0x252ce5` and `0x252d48`: test returned COWN bit31.
- `0x252d4d/0x252d54`: reject the idle word2 sentinel range.
- `0x252d13`: OR word2 with `0xffff0000`.
- `0x252d72/0x252d77`: free the associated packet and clear its software pointer.

The inspected completion routine contains no descriptor-word0 store. The
native contract is in the workspace's private
`ps4/experiments/network/ethernet-netdev-2026-10-17/native-contract/REPORT.md`
(SHA-256 `a4a6dcd622bc3b70735f9ff8e3c0f649d60ba4e3cf91097cf065e1037fc0c7e9`);
its saved `tx-complete.asm` has SHA-256
`e92e0f0b04a6d0a3e83aaf264d1d27216fd5cfc9688f6b682d1368794939d0d2`.
The reference firmware identity is
`55b4948df94aee193ebeb653bd40aff55b3cf09ac7741f58f3dadc239b29a0a2`.
These identify evidence only; no private firmware or instruction bytes are
included in the patch, checker or corresponding-source inputs.

The current Linux producer at `mts_xmit()` already assigns the full new
`mts_tx_ready(slot, length)` value before `dma_wmb()` and the final COWN clear.
It does not OR the new length into a previous completion word. Keeping that
old word through reclaim therefore does not preserve stale length/status on
reuse. Native word2 uses OR while Linux assigns the full sentinel; that is a
separate unresolved difference. This experiment deliberately leaves Linux's
word2 behavior unchanged.

This is a source-supported parity hypothesis, **not an established cause or
fix** for the SSH stall. Upstream MediaTek STAR clears returned status fields
before freeing its mapped packet; the native Sony path also frees its packet
after COWN. Those facts support buffer reuse after ownership return, but do not
prove physical wire delivery. There is no established evidence that COWN is
merely an unsafe command-consumed indication on this device. Neither source
permits claiming that omitting the extra word0 clear will repair the link.

The first captured failure had fewer than 256 completed TX packets, before a
full software ring wrap. Reusing a bounce buffer after a complete wrap is
therefore a weak explanation for that failure. In the later capture, all host
SYNs reached the console and the kernel generated correctly addressed SYN-ACKs,
but the host saw none while unicast ARP still passed. This excludes sshd's
established-session behavior as a sufficient explanation. It does not prove
whether the remaining loss is inside the MAC or farther along the link.

## Exact patch and CPU check

| Input/output | SHA-256 |
| --- | --- |
| Base `psx_mts_net.c` | `fa1e0181ca11e9e21fea509b14654c0e37788d537f1664f598dec6dca4bfda79` |
| Patched `psx_mts_net.c` | `8a1f0b9a351099e1dae454f7d35d84f26abc0784b89fd47c24fe77241b0d45bf` |
| Unchanged `mts_net_core.h` | `d1e138130df0f7d63555704daa61846f478416a6f0e689809ab7fa3ee00a931b` |
| `terminal-reclaim.patch` | `b6fbbc41b295ba259b01e89aa2d7dafdfcc37441b74506cdff30087370400797` |

Run the checker against the read-only frozen dev6 source and a new output:

```text
env TMPDIR=/var/tmp PYTHONDONTWRITEBYTECODE=1 python3 experiments/baikal-mts-tx-reclaim/check.py --kernel-source /path/to/exact/runtime-dev6 --output /var/tmp/new-mts-reclaim-check
```

`check.py` verifies input hashes, applies the actual patch only to a new copy,
and requires that its sole source difference is that deleted statement. It
extracts complete production `mts_tx_allowed`, `mts_tx_reclaim` and `mts_xmit`
functions and includes the unchanged portable core header. API stubs record
descriptor writes and barriers; synthetic hardware returns ownership.

Checks cover preservation of the entire completion word and bounce pool,
unchanged sentinel retirement, no double reclamation, refusing hardware-owned
or idle-sentinel descriptors, full-ring backpressure, complete word0 assignment
before OWN-last publication, zero padding, slot255 EOR and three full reuse
wraps. A negative control runs the identical fixture against the unpatched
source and must fail the precise word0-preservation assertion.

On 2026-11-07, exact patch application and these checks passed with
`-Wall -Wextra -Werror` plus AddressSanitizer/UndefinedBehaviorSanitizer.
Receipt: `/var/tmp/psx-mts-tx-reclaim-check-20261107-02/receipt.json`.
An earlier fixture compile failure used the wrong stub pointer type; the stub
now matches production `void *cpu`. The patch was unchanged between attempts.
The tests model CPU control flow, not DMA timing, hardware completion or wire
transmission. No hardware action was performed by this implementation.

`POLICY` in `check.py` is the agreed `mts_tx_reclaim_experiment` metadata. Keep
`hypothesis_only=true` and `hardware_acceptance=false` in fresh build receipts.
