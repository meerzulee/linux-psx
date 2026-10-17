# PS4 development knowledge

These notes carry the useful PS4 research into Linux psX without making a new
hardware-support claim. They summarize the published PS4 source at
`386df1ad97d92defee1794bf961a0e2f5dc85a1b`; the
[migration ledger](KNOWLEDGE-MIGRATION.md) records what was curated, superseded
or deliberately left in the historical repository.

| Start here | Purpose |
| --- | --- |
| [6.18 bring-up](6.18-BRINGUP.md) | MSI, ICC, ACPI and display fixes, with the diagnostic sequence that justified them |
| [Capabilities and remaining gaps](CAPABILITIES.md) | Tested console scope, fan, storage, audio, wireless and userspace ISA limits |
| [Ethernet investigation](ETHERNET-HISTORY.md) | RX before TX, PHY handoff, packet proof, MTS1/MTS2 and EXP016's degraded result |
| [UART and debugging](UART-AND-DEBUGGING.md) | Capture ownership, early-console lessons and superseded procedures |
| [Internal storage](INTERNAL-STORAGE.md) | Layered storage model and read-only research boundary |
| [Knowledge migration](KNOWLEDGE-MIGRATION.md) | Coverage map and immutable original references |

The current build, boot and artifact interfaces are the root
[BUILDING](../../../docs/BUILDING.md), [BOOTING](../../../docs/BOOTING.md),
[ARTIFACTS](../../../docs/ARTIFACTS.md) and
[ETHERNET](../../../docs/ETHERNET.md) documents. Distribution installation,
initramfs construction, desktop compatibility and NetworkManager integration
belong to the companion project:

https://github.com/meerzulee/omarchy-psx

The historical measurements below used earlier PS4 kernels and boot sets.
`6.18.44-psx-ps4-baikal1` has a different identity and firmware arrangement;
successful source checks or CI do not transfer those measurements to it.
PS5 remains a separate planned port; see
[the shared-kernel research](../../../docs/UNIVERSAL-KERNEL.md).
