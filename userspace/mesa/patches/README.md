# PS4 Mesa graphics port

`0001-ps4-graphics-families.patch` targets the exact official Mesa 26.2.5
release archive. Apply with `patch -F0 -p1` from its extracted root.
The archive and patch digests, attribution, adaptations and checks are in
`ps4-graphics-provenance.json`; do not substitute a moving Mesa Git branch.

This is a port of the Liverpool/Gladius graphics changes in the pinned
DionKill repository patch (commit author: froyo). Existing Mesa/AMD
per-file MIT copyright and license notices remain unchanged; the source
repository's accompanying license is preserved in `LICENSE.ps4-mesa`.

https://github.com/DionKill/ps4-video-archlinux/blob/57e0f8ecc7a1df9b9664d682b19fadfe3e336f02/mesa-ps4/ps4-mesa.patch

The patch identifies the kernel's FAMILY_KV console revision ranges and
selects console raster layouts, names, compiler target, cache classification
and GS depth. New shared family IDs remain within the existing GFX7 range.
The unused private drm/radeon enum additions and unrelated CLC LLVM edit
were omitted. Build all selected Mesa components from the same patched
source; these internal enum values are not a system-library replacement API.

Graphics source and CPU contract checks passed. Full native Mesa compilation
and the separately owned UVD capability patch have their own receipts. No
installation or PS4 visual result is established by this directory. The
source mismatch is concrete; fixing the observed stripes remains a hardware
test question, including the separate XR30/EGL-format hypothesis.

## Historical Liverpool HTILE workaround (inactive in dev5)

`0004-radv-liverpool-disable-htile.patch` is a new Linux psX adaptation,
applied after the three unchanged imported patches. The dev2 D16 depth test
passed with HTILE disabled and hung with it enabled on one Liverpool/Baikal B1
console (EXP-20261021-001/002). The patch disables HiZ/HTILE at physical-device
policy initialization so allocation and compression properties agree. Normal
depth testing remains available; other families, including Gladius, are
unchanged. The underlying metadata defect is not yet isolated. Dev3 remains
experimental pending its own depth, presentation and UVD regression gates.
EXP005 passed its default-policy depth oracle. EXP006 then hung on the first
cube frame despite HTILE being absent, so this workaround is insufficient for
Vulkan adoption and must not be described as the completed freeze fix.

## Bounded compute-fill offset

`0005-radv-bound-meta-fill-offset.patch` exposes the CPU caller's existing
`size <= UINT32_MAX` and `max_offset = size - store_width` invariant to NIR.
Its clamp preserves valid inputs and ACO's existing overflow protection. It
allows GFX7/8 to retain a uniform MUBUF descriptor rather than converting to a
64-bit VGPR/FLAT address because of an unconstrained potential offset overflow.
This applies to all RADV compute fills, not only metadata or Liverpool; the
pre-existing internal32-bit size contract is unchanged.

CPU Bonaire and actual Liverpool dev4 output show `buffer_store_dwordx4` in
final lowered ACO IR; a machine disassembler is not enabled in this build.
EXP009 passes exact fill/guard oracles around4096B. EXP010 passes1/120cube
presentations with HTILE disabled, where dev3 stalled. These are hardware
results for the exact artifacts. They do not prove every FLAT operation is
broken or establish the underlying hardware/kernel defect. Dev5 retains0005
and omits0004. EXP011 passes the identical D16 oracle with16KiB HTILE enabled;
EXP012 passes1/120cube presentations with196608B HTILE. EXP013/014 preserve
exact UVD decode and actual-driver observations, including the corrected
collection error described in the feature matrix. Broader API coverage,
seeking, sustained load and repeated boots remain separate gates.
