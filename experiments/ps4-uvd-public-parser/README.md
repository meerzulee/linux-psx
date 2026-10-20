<!-- SPDX-License-Identifier: GPL-2.0-only -->
# Public UVD parser policy — host checks

This checks the public Linux software admission rules that informed the PS4
UVD experiment design. It extracts three unmodified functions from an exact
Git commit and runs them against synthetic host memory. It neither reviews
the PS4 kernel patch nor supplies a hardware experiment or a decoder.

The important distinction is **packet allowlist versus complete submission**.
The public `amdgpu_uvd_cs_reg()` allows GPCOM DATA0/DATA1 register packets.
However, `amdgpu_uvd_ring_parse_cs()` also requires a message command to have
been validated. A DATA-only stream therefore passes the packet walk and still
fails the full parser. Merely finding a register in the allowlist does not
establish a complete public diagnostic sequence.

| Synthetic stream | Packet policy | Full userspace parser | Command callbacks |
| --- | --- | --- | --- |
| DATA0/DATA1 followed by NOPs | Accept | Reject: no message flag | None |
| NOPs only | Accept | Reject: no message flag | None |
| Type-2 padding only | Accept | Reject: no message flag | None |
| CONTEXT_ID packet | Reject | Reject | None |
| Public message packet layout, callback does not mark a message | Accept | Reject | Stub invoked |
| Same layout, callback explicitly marks a message | Accept | Accept in the stub control only | Stub invoked |

Both `address_64_bit` branches are exercised. The 27 policy cases also cover
packet types 1/3, unknown registers, a truncated register burst, unaligned and
empty IB lengths, callback error propagation and the legacy first-pass exit.
The fake IB storage remains unchanged. The upstream function's `job->vm = NULL`
assignment is observed on a host struct.

## Run

Use a Linux Git repository that contains the recorded commit; no worktree
checkout, network download, kernel build, VM or device is needed:

```sh
python3 experiments/ps4-uvd-public-parser/check.py \
  --source-repo /absolute/path/to/linux-git \
  --report /absolute/path/to/new-private-report
```

For this workspace the public Git repository is `../lab/psx-baikal-port72`
when running from `linux-psx/`. Pass `--cc cc` to select a native compiler.
Both optimized and ASan/UBSan runs are required. The report directory must not
exist: failed or completed attempts are preserved. Reports contain the
generated translation unit, compiler/run output and hashes. No binary is
retained, and no active target, patch series or build configuration is changed.

## What is real and what is stubbed

These functions and the parser context struct are extracted verbatim from
`amdgpu_uvd.c` at commit `a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea`:

- `amdgpu_uvd_cs_reg()`
- `amdgpu_uvd_cs_packets()`
- `amdgpu_uvd_ring_parse_cs()`

The packet field macros come from that commit's `cikd.h`; the register
constants come from `uvd_4_2_d.h`, which the public parser includes. Full-file
SHA256 pins in `check.py` are checked using `git show`, so unrelated worktree
changes cannot silently change the test. The fixture uses literal packet
words to exercise the extracted definitions independently.

Host structs, IB value reads and error logging are stubs. The real pass1/pass2
callbacks, buffer relocation, message validation, session accounting and
ownership code are **not** extracted. A callback that sets `has_msg_cmd` is a
control for the final policy predicate, not a valid message implementation.
The DATA-only rejection does not depend on callback behavior: neither callback
is invoked for that stream.

Compilation uses GNU C11 because the unchanged kernel function uses an empty
aggregate initializer. One existing signed/unsigned loop comparison receives
a scoped `-Wsign-compare` allowance around the extracted functions only. The
original fixture still builds with `-Wall -Wextra -Werror`. The first macOS
attempt exposed that host warning mismatch; the extracted source was not
modified to silence it.

## Implication for the next design

Public `uvd_v5_0_ring_test_ring()` polls CONTEXT_ID after a main-ring packet.
Public `amdgpu_uvd_send_msg()` places DATA0/DATA1, CMD and NOPs inside an IB.
Public `amdgpu_uvd_ring_test_ib()` waits separately for create and destroy
message fences. None of those functions establishes that a standalone DATA
write inside a PS4 IB remains readable through CPU MMIO.

The lexical search across the pinned AMDGPU and Radeon source found command
construction, parser cases and newer VCN register tables, but no DATA-only UVD
readback self-test. This is a bounded negative finding, not proof that the
register is unreadable. The inspected VCN decode ring self-test uses
`scratch9`, so it supplies no DATA0 readback qualification either.

The next design should first establish an observable marker contract. It must
separate CPU baseline/readback, main-ring execution and indirect execution.
If a later control establishes main-ring marker visibility, moving that same
marker into the IB can change one execution variable. Even then, a negative
IB result leaves fetch, requester, packet compatibility and visibility
alternatives. A positive result must remain attributable to the indirect
stream, with fresh values, ownership, deadline and failure observations intact.

Do not add a message command merely to satisfy this userspace check. A
kernel-owned diagnostic does not pass through this parser, so its rejection
cannot explain EXP004's timeout. Adding CMD/message processing is a separate
behavioral change with its own ABI and lifetime requirements. Neither these
host checks nor their hashes qualify such a change for hardware.

The stopped Claude patch review and previous compiled audit remain incomplete.
This public-parser deliverable is a separate software-policy check and does
not replace either review. The frozen EXP004 artifacts are not inputs to it.

## Source and credit

The parser is public Linux/AMDGPU work credited to AMD and Christian König.
Its MIT notice and author block are retained in the generated translation
unit. The checker and fixture are original GPL-2.0-only host tooling. No
proprietary firmware, raw console logs, retail code or private identifiers
are included in this directory.

Exact local source locations at the pin:

| File | Relevant lines |
| --- | --- |
| `drivers/gpu/drm/amd/amdgpu/amdgpu_uvd.c` | 973–1011 packet register policy; 1022–1047 packet walk; 1059–1104 full parser; 1130–1147 message IB; 1309–1341 IB test |
| `drivers/gpu/drm/amd/amdgpu/uvd_v5_0.c` | 521–545 main-ring CONTEXT_ID test; 558–568 IB emitter |
| `drivers/gpu/drm/amd/amdgpu/amdgpu_vcn.c` | 559–588 scratch9 main-ring test |

Full public URLs:

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_uvd.c

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/uvd_v5_0.c

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_vcn.c
