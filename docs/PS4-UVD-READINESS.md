# PS4 UVD readiness diagnostics

These default-off Linux 7.2 targets are one-shot development probes for PS4
Slim / Baikal B1. They deliberately park the boot on every terminal outcome,
retain published resources and require full power-off. They do not provide a
desktop, registered UVD decode engine or supported acceleration. The ordinary
kernel targets remain unchanged. Firmware and generated binaries are external
inputs and are not included in this source branch.

The ordered diagnostic series retain the native kernel patch prefix. The
mapping canary uses owned VMID4 and first checks one 256-byte SDMA copy through
reserved high slots. That copy does not establish UVD access to the other
mapped pages. The readiness probe validates its supplied firmware wrapper,
holds the affected blocks, installs guarded mappings and observes startup with
ring fetch disabled. Neither readiness outcome submits decode commands.

## Recorded local experiments

| Experiment | Candidate | Result |
| --- | --- | --- |
| EXP-20261018-005 | VMID4 mapping canary | One SDMA copy/readback passed; no UVD startup claim. |
| EXP-20261018-006 | `uvd-ready-dev1` | Preflight parked before UVD setup: computed GB configuration `02011003` differed from expected `02011002`. |
| EXP-20261018-007 | `uvd-ready-dev2`, `c2d1a330` | Corrected GB preflight passed; startup produced a VMID14 UMC read fault at GPU page base `0x300000000` despite owned VMID4 mappings. |
| EXP-20261018-008 | `uvd-ready-dev3`, `35b9f379` | Candidate selector read 14, held write/readback 4 passed; startup parked at the combined interrupt/fetch guard. |

Dev3 adds one diagnostic variable: candidate cached-VCPU selector `0x3d3c`,
low-nibble mask `0xf`. Public VCN1 register names, five common register anchors
with a `+0x3800` offset, and an existing GPL write motivated the hypothesis.
Before this test its Liverpool semantics and initial value were unverified.
The probe requires initial 14, preserves upper bits, rechecks the original word
while held, writes owned 4 once and requires exact readback/stability. It does
not move mappings to VMID14 or retry a failed startup.

EXP008 observed initial `0000000e`, held expected/readback `00000004`, then
`FETCH_GUARD_CHANGED error=-5` at 8.627685 seconds. The later snapshot showed
`STATUS=2`, `MASTINT_EN=2`, `RBC_RB_CNTL=1101080c`, IB size 0 and ring pointers 0/0.
The VCPU interrupt-enable bit `0x2` violates the guard's interrupt-mask clause;
both RBC_NO_FETCH and RBC_NO_UPDATE remain set. This combined guard failure
does not establish that ring fetch became enabled. Snapshot reads occur later
and are non-atomic; they do not identify the first failed read or its writer.

No GPU fault line or `READY_OBSERVED` terminal appeared in the completed EXP008
slice. Sampled fault status can miss IRQ-acknowledged faults, so this does not
prove fault-free operation, actual requester VMID, firmware execution or decode.
The later status bit follows the public readiness convention but was not
accepted by the probe. No UVD readiness or acceleration support is qualified.
Normal native boot was restored and verified after the test. Cold-start and
DMA-quiescence behavior have not been independently qualified.

## Build identity and next question

The tested dev3 source commit is
`35b9f37960ba2fbfc8e2fa0ad5077656ceb1dbc2`, with target
`7.2-ps4-baikal-uvd-ready-dev3` and release
`7.2.11-psx-ps4-baikal-uvd-ready-dev3`. Its 66 patches include all 65 dev2
patches unchanged. The local kernel SHA256 is
`b98603038f0a44856faa89e8dfcabdc670aff09bb4234ce40e1dbc7225c2c4d0`;
the complete matching 41-module archive SHA256 is
`0cea3545ea870b95d321a8da8fa29b81e0ecb05dbfe132c5fad539ca84adcbba`.
Host fixtures, source/compiled audits and full module identity checks passed.
The broader rebuild reports 362 warnings in 52 unchanged baseline paths, with
no diagnostic or objtool warnings. A successful build is not hardware support.
The provenance files describe their original build-time scope; this document
records the later hardware result without changing those frozen build inputs.

Before proposing another candidate, review early UVD IRQ routing and
acknowledgement while the normal UVD IP/trap source is not registered. Public
UVD4.2/5/6 startup polls status, then explicitly enables interrupts; it does not
identify the writer of the observed `MASTINT_EN` transition. Do not remove the
interrupt guard merely because the RBC disable bits remained set. No further
hardware test is scheduled.

The curated distribution ledger is
`omarchy-psx/platforms/ps4/experiments/SESSIONS.md`. Raw UART and firmware remain
private. Follow [HARDWARE-TESTING.md](HARDWARE-TESTING.md) for any later test.
