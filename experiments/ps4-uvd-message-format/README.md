# Public UVD message-byte serializer

This small, unintegrated kernel-style component writes a caller-owned
4096-byte CPU buffer. It implements legacy AVC CREATE, the exact public Linux
dummy CREATE, and DESTROY. It generates no ring, IB, fence or submission.
There is no device access, firmware access, allocation or lifetime policy.

The common message declares `0xde4` bytes. The serializer clears the entire
4096-byte output, including the unused tail, then writes explicit little-endian
bytes. Host endianness and CPU output alignment do not affect those stores.
This does not establish any GPU-address alignment or firmware compatibility.

## API

```c
struct ps4_uvd_avc_create params = {
	.width = 640,
	.height = 360,
	.dpb_size = 0,
};
ret = ps4_uvd_msg_create_avc(message_page, 4096, handle, &params);
ret = ps4_uvd_msg_create_linux_dummy(message_page, 4096, handle);
ret = ps4_uvd_msg_destroy(message_page, 4096, handle);
```

Each function requires a nonzero 32-bit handle and exactly 4096 writable
bytes. CREATE additionally requires readable parameters with nonzero width
and height. A NULL argument, wrong size, zero handle or zero dimension returns
`-EINVAL` without modifying output. Parameter values are copied before the
page is cleared, so the parameter object may overlap the output.

The input fields are `u32`. Nonzero width/height and any DPB-size value are
serialized literally, with no multiplication or narrowing. This is a format
check, **not** validation of supported dimensions, suitable DPB allocation,
valid session handles, codec capability or firmware acceptance.

| Byte offset | Field | Serialized value |
|---:|---|---|
| 0 | declared message size | `0xde4` |
| 4 | type | CREATE `0`, DESTROY `2` |
| 8 | stream handle | nonzero caller value |
| 12 | feedback number | `0` |
| 16 | CREATE stream type | legacy AVC `0` |
| 20 / 24 | session flags / ASIC ID | `0` |
| 28 / 32 | CREATE width / height | caller values |
| 36 | CREATE DPB buffer field | `0` |
| 40 | CREATE DPB size | caller value |
| 44 / 48 | DPB model / version info | `0` |

All bytes not listed as populated remain zero. DESTROY populates only its
three common fields.

## Public references and the two CREATE policies

Linux `amdgpu_uvd_get_create_msg` writes dimensions `0x780 × 0x440`
(1920 × 1088) and DPB size `0x01b37000`; the dummy wrapper reproduces those
values exactly. Its corresponding DESTROY clears all words after the common
header. The public parser rejects handle zero at `amdgpu_uvd.c:820`.

Mesa 26.1.7's `uvd_build_create_cmd` writes the dimensions but leaves DPB size
zero. The generic serializer matches that policy when `dpb_size=0`.
Mesa selects legacy AVC type `0` before the Tonga family; this implementation
does not expose the separate H264_PERF type or any other codec.

Linux clears its full 4096-byte page. Mesa clears `sizeof(struct ruvd_msg)`,
which the verbatim header measures as `0xde4`. The serializer deliberately
clears the full page, while tests compare the Mesa-declared message bytes
and independently check the additional zero tail. A Linux dummy is not
described as a Mesa first-decode session request.

Verified local inputs:

- Linux commit `a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea`,
  `amdgpu_uvd.c:1182–1245`; SHA-256
  `6b8c41d474f950b1de4fa57a03498915fa4ed0208341de5655379cdb81e5318d`.
- Mesa 26.1.7 `ac_uvd_dec.h:336–419`; SHA-256
  `fde6080faf355b00d373f8f72e109461f1072b6290d17f93fe8d5e1bb58842e1`.
- Mesa 26.1.7 `ac_uvd_dec.c:171–223,697`; SHA-256
  `f9ace763a478dba1264cece9af0011065e8ded5fdf5f8e41c95d3acd2d843870`.

Public source locations:

https://github.com/gregkh/linux/blob/a2de78ee6ed8bf8cd99d177c74865a6e8c3cb6ea/drivers/gpu/drm/amd/amdgpu/amdgpu_uvd.c

https://archive.mesa3d.org/mesa-26.1.7.tar.xz

The implementation adapts Linux's public field construction and retains its
AMD copyright, permission notice and original author credit. Mesa's header
and implementation notices remain in the verbatim input copies and generated
reference header. Those notices are not replaced by this project's identity.

## Host checks

From the workspace root, choose a new output directory:

```sh
python3 -B linux-psx/experiments/ps4-uvd-message-format/check.py \
  --output lab/uvd-2026-10-20/message-format/host-tests/new-run
```

The checker compiles the actual `.c` and `.h` with small host type/string/errno
shims. It extracts only the public Linux and Mesa serialization statements;
no upstream allocation, command emitter or send function executes. Mesa's
message header is included verbatim with three opaque prototype declarations
and fixed-width types in place of its unrelated dependency header.

The first Mac run passed **33 cases** in optimized and ASan/UBSan builds.
Tests cover Linux CREATE/DESTROY goldens, all common/CREATE offsets from the
Mesa header, dimensions-only Mesa messages, explicit endian bytes, deterministic
clearing, extreme representable values, aliased parameters, unaligned CPU
buffers, surrounding guards, and unchanged output after invalid input.
Five deliberately injected defects fail at exact named witnesses.

The preserved golden pages use handle `0x11223344` to expose byte ordering.
Handle `1`, used by the public Linux ring test, is also compared directly
against the extracted Linux helper statements; no ring test is run.

This is public-format implementation, not independent review of the PS4
firmware ABI. The historical interrupted Claude message advisory remains
incomplete. No PS4 support, session processing, decoded frame or hardware
acceptance follows from these CPU-only checks.
