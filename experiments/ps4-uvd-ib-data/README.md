# UVD IB-only DATA0 packet construction

`ps4_uvd_ib_data_plan.h` constructs two separate 4096-byte images for a
proposed UVD5-style indirect-buffer marker diagnostic. The main ring requests
one IB; only the IB writes the DATA0 marker. This is reusable, original host
preparation code. It performs no MMIO, allocation, publication, waiting or
hardware outcome classification. Neither it nor the fixture emulates
Liverpool hardware or firmware.

The API is:

```c
ps4_uvd_ib_data_build(ring_storage, ring_bytes,
                     ib_storage, ib_bytes,
                     request, plan);
```

The request is passed by value. It contains `ring_gpu_base`, `ib_gpu_base`,
`baseline`, `token`, `data_before_baseline`, `initial_rptr_raw` and
`initial_wptr_raw`, all in CPU byte order. On success, the plan records the
request, both exclusive GPU ends, both page sizes, and the raw DWORD counts.
The header provides separate host and `__KERNEL__` type branches without
requiring the older ring planner header.

## Byte contract

Every register-write pair is `PACKET0(register, 0)` followed by one DWORD.
The header uses explicit little-endian byte stores; no native word cast
determines the image byte order.

| Image | DWORDs | Content |
| --- | --- | --- |
| Main ring | 0–1 | `0x00003c67`, low 32 bits of owned IB GPU VA |
| Main ring | 2–3 | `0x00003c66`, high 32 bits of owned IB GPU VA |
| Main ring | 4–5 | `0x00003da2`, raw IB length **16 DWORDs** |
| Main ring | 6–15 | Five `0x00003bff`, `0` NO_OP pairs |
| Main ring | 16–1023 | 504 more NO_OP pairs, outside the proposed publication |
| IB | 0–1 | `0x00003bc4` (DATA0), caller-selected fresh token |
| IB | 2–15 | Seven NO_OP pairs |
| IB | 16–1023 | 504 more NO_OP pairs, outside the requested IB length |

The main-ring prefix retains the EXP004 UVD5-style LOW/HIGH/SIZE request. Its
only address is the IB GPU VA; there is no DATA0, CONTEXT_ID, CMD, DATA1,
fence/trap or second IB request in its decoded packet stream. The IB contains
only one DATA0 write and NO_OP pairs. The whole page remainder is initialized;
that does not authorize consumption beyond either 16-DWORD prefix.

Initial raw RPTR and WPTR must both be zero. The plan's proposed published
WPTR, expected final RPTR and IB length are all **16**, without a byte-offset
shift, mask or normalization. These are packet-construction values and
expectations, not evidence of pointer units or execution on PS4.

## Validation and caller duties

Before modifying either page or the plan, the builder checks:

- Non-NULL outputs and exactly 4096 advertised bytes for each image.
- Four-byte CPU image alignment and natural plan alignment.
- Representable CPU ranges and pairwise non-overlap of both images and plan.
- 4096-byte-aligned, non-overlapping GPU intervals with representable
  exclusive ends. Adjacent intervals and either address ordering are valid.
- Both initial raw pointers are zero.
- A token that is nonzero, not all-ones, and distinct from both the planted
  DATA0 baseline and the actual DATA0 value observed before planting it.

Every error leaves both pages and the plan byte-for-byte unchanged. Baseline
itself has no extra value restriction in this helper. Validation does not
verify a later baseline store or readback. As with any C buffer API, the
caller must supply real writable objects of the advertised capacities;
integer range checks cannot discover inaccessible memory.

The caller must establish allocation, pinning, physical non-aliasing,
CPU/GPU mapping correspondence, requester reachability, VMID and address-width
constraints, cache/publication ordering, exclusive access, and retention.
The builder must run before any reader or device can use the destinations.
It cannot establish historical token freshness or exclude competing DATA0
writers. A numerical coincidence between the token and an address or packet
header does not constitute a DATA0 write; attribution depends on decoded
commands and the caller's controlled register writes.

The UVD5 address form is the chosen diagnostic input, not a claim that it is
the correct Liverpool interface. UVD4.2's different address form, the earlier
IB timeout, and runtime status meanings remain separate research questions.
There is no firmware-message submission or video decode in these images.

## Host checks

From the `linux-psx` repository, compile and run the exact header included by
the fixture:

```sh
packet_build_dir="$(mktemp -d "${TMPDIR:-/tmp}/ps4-uvd-ib-data.XXXXXX")"
trap 'rm -rf "$packet_build_dir"' EXIT
/usr/bin/cc -std=c11 -Wall -Wextra -Werror -pedantic -O2 \
  experiments/ps4-uvd-ib-data/packet_test.c -o "$packet_build_dir/optimized"
"$packet_build_dir/optimized"
/usr/bin/cc -std=c11 -Wall -Wextra -Werror -pedantic -O1 -g \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  -fno-sanitize-recover=all \
  experiments/ps4-uvd-ib-data/packet_test.c -o "$packet_build_dir/sanitized"
"$packet_build_dir/sanitized"
```

The independent decoder uses literal register values and separately decodes
packet type, count, register, address halves and ordering. It checks the
entire images and the 16-DWORD prefixes. The fixture checks the high GPU VA
`0x30030a000`, raw length 16, exact first/last bytes, every padding pair,
canaries, all 32 individual token bits and plan fields. Negative cases cover
NULLs, sizes, alignment, pointer/GPU overflow, overlapping images/plan, bad
initial pointers and token freshness, with whole-output snapshots checking
that rejection performs no partial write.

Decoder sensitivity cases deliberately introduce forbidden or repeated
register writes, nested IB requests, address truncation/swapping, shifted
lengths, type/count violations, incorrect tokens and malformed padding.
These cases exercise the independent checker; they are not injected defects
in a kernel implementation and do not certify driver control flow.

On 2026-10-20, macOS ARM64 Apple clang 21.0.0
(`clang-2100.3.34.2`) passed both commands with no compile diagnostics or
sanitizer reports. Each run printed:

```text
PASS: exact main/IB images, 32 token bits, 88 atomic rejections, 46 decoder sensitivity cases; host byte contract only
```

No native Linux run, `__KERNEL__` compilation, driver linkage, console run or
hardware acceptance is claimed by these host results. A kernel adapter must
use and test this same header or prove equality to its frozen input.

## License and public-source comparison

The header and fixture are GPL-2.0-only. The validation/explicit-LE-store
structure adapts the existing original GPL-2.0-only
`../ps4-uvd-ring/ps4_uvd_ring_plan.h`; that license is retained. No private
firmware or reverse-engineered implementation bytes are included.

Public source was compared at Linux commit
`a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea`, including local pinned source reads:

- `uvd_v5_0.c`, `uvd_v5_0_ring_emit_ib()` supplies the public
  LOW/HIGH/SIZE order and unshifted `ib->length_dw`;
  `uvd_v5_0_ring_insert_nop()` supplies the two-DWORD NO_OP pair convention.
- `uvd_5_0_d.h` supplies the five register indices used here.
- `vid.h` supplies PACKET0 encoding.
- `amdgpu_uvd.c`, `amdgpu_uvd_send_msg()` is a comparison for the DATA0
  packet convention. Its real message payload also uses DATA1 and CMD, which
  this diagnostic omits. Its 16-DWORD image is an **IB**, including when
  submitted via the upstream direct-job path.

The compared UVD5 implementation credits AMD (2014) and Christian König;
its MIT notices and authorship remain in the upstream source. This header
is an original restricted constructor, not an imported upstream driver.

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/uvd_v5_0.c

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/include/asic_reg/uvd/uvd_5_0_d.h

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/vid.h

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_uvd.c
