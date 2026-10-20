# One public CREATE IB and one polled fence prefix

This CPU-only planner produces an exact public Linux dummy CREATE page,
a 16-DWORD message IB, and a 16-DWORD main-ring image. It does not touch mapped
buffers, submit work, allocate memory, access firmware or operate hardware.
The kernel adapter and its ownership/publication policy are separate.

```text
main page: 4096 bytes, separate from the whole private backing
  main_le[64]:
    IB LOW / HIGH / SIZE=16                         6 DWORDs
    CONTEXT_ID=sequence / DATA0=target-low
      / DATA1=(target-high & 0xff) / CMD=0          8 DWORDs
    NO_OP / 0                                     2 DWORDs
                                                  ----------
                                                  16 DWORDs

borrowed backing: exactly 0x20000 bytes
  +0x0000  IB page: DATA0=create-low / DATA1=create-high / CMD=0
                     followed by five NO_OP pairs   16 DWORDs
  +0x1000  CREATE page: exact Linux dummy, 4096 bytes
  +0x2000  fence page
  +0x2040    one 32-bit fence target
```

The upstream fence routine emits a further six DWORDs:
`DATA0=0 / DATA1=0 / CMD=2`. **That trap suffix is deliberately omitted.**
This planner is not the full upstream fence sequence or upstream ring
self-test. It plans one CREATE message and one polled 32-bit prefix, with
no DESTROY or implicit teardown.

## API and validation

`ps4_uvd_message_job_build(out, input)` is declared in
`ps4_uvd_message_job.h`. Inputs are:

- An explicitly asserted, 256MiB-aligned `vcpu_segment_base`.
- Main-page and private-backing GPU VAs and their exact byte lengths.
- Nonzero stream handle and a literal 32-bit fence sequence.

Main and backing bases must be 4KiB aligned, which also satisfies 256-byte
alignment. This deliberately plans entire pages, not a 256-byte-aligned
interior slice of a larger mapping. Sizes must be exactly 4096 and `0x20000`.
Checked additions precede interval comparisons. The complete main and backing
intervals must be disjoint and remain inside the asserted VCPU segment.
The complete 32-bit target store must fit the fence's 40-bit address encoding.

CPU output contains the addresses/lengths in `layout`, `main_le[64]`,
`ib_le[64]`, and `create_le[4096]`. Arrays are explicit little-endian bytes.
The IB length is 16 DWORDs, not 64 bytes. The page offsets are fixed constants.
No large temporary plan is put on the function's stack; the caller owns the
output object, which exceeds 4KiB and should not be allocated on a small kernel
stack if integrated.

Every rejection leaves the complete output unchanged. Inputs are copied
before output changes, so input/output overlap is supported. The reviewed
serializer is the last fallible call and itself preserves output on failure;
all packet/layout stores follow its success.

The sequence may be zero or any other `u32`. The later caller must establish
a distinct initial target value and its desired fence-page guard contents.
This planner initializes neither the fence page nor unused main/IB page
tails, and it implements no polling or completion decision.

## Public source and dependency

Linux commit `a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea` provides:

- `amdgpu_uvd_send_msg`: DATA0/DATA1/CMD0 plus five NOP pairs, length 16.
- `uvd_v5_0_ring_emit_ib`: LOW/HIGH/SIZE, six DWORDs.
- `uvd_v5_0_ring_emit_fence`: the first eight DWORDs and the separate six-DWORD
  trap suffix.
- `uvd_v5_0_ring_insert_nop`: one register/value pair for a count of two.

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_uvd.c

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/uvd_v5_0.c

The UVD5 register indices are checked against the saved native header
`ddee9d3d…`; PACKET0 encoding is checked against the separately pinned saved
`cikd.h` (`330255af…`). The receipt records their exact locations and hashes;
the historical `cikd.h` is not mislabeled as a new upstream acquisition.
Original AMD notices and author credits are preserved in `UPSTREAM-NOTICES`.

The linked, unchanged serializer dependency is
`../ps4-uvd-message-format/ps4_uvd_message.c/.h`:

- `.c`: `7cbb584b8a628e2726521e7e28436aa99ed17d7034bf8c0ddf62800cd2df57c2`
- `.h`: `95daa65583c4aeb13f39e8ecad62666fd38dc4666b0ba8a3efc71aed1896da97`

The planner calls `ps4_uvd_msg_create_linux_dummy`: 1920 × 1088, DPB-size
field `0x01b37000`. It does not substitute Mesa's zero-DPB dimensions-only
CREATE. No existing dependency file is edited.

## Host evidence and limits

Run from the workspace root with a new output directory:

```sh
python3 -B linux-psx/experiments/ps4-uvd-message-job/check.py \
  --output lab/uvd-2026-10-20/message-job/host-tests/new-run
```

The actual planner and serializer are compiled with small host kernel-header
shims. Public serialization/emission bodies operate only on synthetic arrays.
The full upstream fence reference generates fourteen words in the fixture;
tests compare its first eight, verify its remaining six trap words, and verify
the planned NOP at the corresponding main-ring end.

Mac `macos-02` passed **42 cases** under O2 and ASan/UBSan. Coverage includes
literal packet bytes, public reference words, exact Linux CREATE, accounting,
endianness, deterministic output, page/segment boundaries, maximum 40-bit
addresses, zero/max sequences, overflow, whole-backing overlap, sizes,
alignment, invalid handles, NULLs and input/output aliasing. Six exact-witness
mutants were rejected.

`macos-01` preserves a reference-fixture compile failure: the public NOP body
uses a signed loop index against an unsigned count. The host wrapper now takes
signed count and requires exactly two, its only test value; the upstream body,
planner, assertions and warning flags remain unchanged. This wrapper does
not claim to validate arbitrary upstream NOP counts.

The historical `EXP-20261019-003` result recorded one CPU-visible writeback
transaction, not general ABI, containment, lifecycle or repeatability support.
Its saved result SHA is
`b08ce205b2b6e7d5bc8d4c424ea4a1668fa542dde43b323bc55af4da9f5cba31`;
its older compiled audit remains incomplete. That scoped precedent does not
qualify this new CREATE-plus-prefix composition.

Successful planning means consistent bytes and arithmetic only. It proves
neither DMA ownership/PTEs, VCPU mapping, message parsing, IB/fence execution,
ordering, completion, quiescence nor decode. Existing firmware/advisory and
hardware-review limitations are not replaced by these CPU tests.
