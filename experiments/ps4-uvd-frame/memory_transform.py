# SPDX-License-Identifier: GPL-2.0-only
"""Add the exclusive first-frame work mapping to the frozen CREATE mapper.

This module transforms source bytes only.  It allocates no device resources,
does no source-tree writes and has no hardware entry point.  The caller owns
the ready gate declarations, controller, receipt and complete kernel build.
"""
from __future__ import annotations

import hashlib

BASE_SHA256 = "7c04a9be39867e1208a57e3212daea7d303175a9ee8c7558a2f8e0d4b6bb60df"
BASE_RECEIPT_SHA256 = "ae1f68d18a9a70897f8d969f42f1a1258ec27bf0efc78d2701e56bd0029c32f0"

HELPERS = """/* DECODE_FRAME_LAYOUT_BEGIN */
/* Older leaves retain their exact 128KiB work allocation and 812 pages.
 * Only the exclusive first-frame leaf owns the separate 256KiB work BO.
 */
static u64 ps4_canary_work_bytes(const struct ps4_canary_state *s)
{
\treturn s->frame_mode ? PS4_FRAME_WORK_BYTES : PS4_READY_IB_BYTES;
}

static unsigned int ps4_canary_expected_pages(const struct ps4_canary_state *s)
{
\treturn s->frame_mode ? PS4_FRAME_MAPPED_PAGES : PS4_READY_MAPPED_PAGES;
}

static unsigned int ps4_canary_work_end_slot(const struct ps4_canary_state *s)
{
\treturn PS4_READY_IB_SLOT +
\t\t(unsigned int)(ps4_canary_work_bytes(s) >> AMDGPU_GPU_PAGE_SHIFT);
}
/* DECODE_FRAME_LAYOUT_END */

"""

PT_READBACK = """/* DECODE_FRAME_PT_READBACK_BEGIN */
/* CPU preparation/readback evidence only: this is not a claim that UVD has
 * observed every page, nor a proof of DMA quiescence.  No PTE is changed here.
 * The inventory is in increasing slot order; every unmapped hole stays zero.
 */
static bool ps4_canary_frame_pt_valid(const struct ps4_canary_state *s)
{
\tunsigned int slot, page = 0;

\tif (!s || !s->frame_mode || !s->ready_mode || !s->ready_layout ||
\t    s->prepare_only || !s->pages || !s->pt.bo || !s->pt.cpu ||
\t    s->leaf_index || s->pt_size != 1024U * sizeof(u64) ||
\t    amdgpu_bo_size(s->pt.bo) != s->pt_size ||
\t    s->mapped_pages != ps4_canary_expected_pages(s))
\t\treturn false;
\tfor (slot = 0; slot < 1024U; ++slot) {
\t\tu64 expected = 0;

\t\tif (page < s->mapped_pages && s->pages[page].slot == slot)
\t\t\texpected = s->pages[page++].pte;
\t\tif (readq((void __iomem *)s->pt.cpu + slot * sizeof(u64)) != expected)
\t\t\treturn false;
\t}
\treturn page == s->mapped_pages;
}
/* DECODE_FRAME_PT_READBACK_END */

"""

BORROW = """/* DECODE_FRAME_OWNED_BACKING_BEGIN */
/* Borrow the full separately owned 256KiB frame BO, never firmware storage.
 * The normal UVD main ring remains its own 4096-byte allocation.  The frame
 * gate is immutable, and the complete mapping was validated before publish.
 */
static bool ps4_canary_frame_backing(struct ps4_canary_state *s,
\t\t\t\t    struct amdgpu_ps4_uvd_ring *backing)
{
\tstruct ps4_canary_bo *bo;

\tif (!s || !backing || !s->frame_mode ||
\t    !amdgpu_ps4_uvd_decode_frame_requested() ||
\t    !amdgpu_ps4_uvd_decode_frame_options_valid(s->adev))
\t\treturn false;
\tbo = &s->uvd_ib;
\tif (s != ps4_canary_retained || !s->ready_mode || !s->submitted ||
\t    !s->fence || !bo->bo || !bo->cpu || ((unsigned long)bo->cpu & 3U) ||
\t    !bo->bo->tbo.resource || !bo->bo->tbo.pin_count ||
\t    bo->bo->tbo.type != ttm_bo_type_kernel ||
\t    bo->bo->tbo.resource->mem_type != TTM_PL_TT ||
\t    amdgpu_bo_size(bo->bo) != PS4_FRAME_WORK_BYTES ||
\t    bo->bo == s->uvd_rb.bo || !ps4_canary_frame_pt_valid(s))
\t\treturn false;
\tbacking->cpu = bo->cpu;
\tbacking->bytes = PS4_FRAME_WORK_BYTES;
\t/* The native bo->gpu alias is never a command address for VMID4. */
\tbacking->private_va = PS4_CANARY_VA +
\t\t(u64)PS4_READY_IB_SLOT * AMDGPU_GPU_PAGE_SIZE;
\treturn true;
}
/* DECODE_FRAME_OWNED_BACKING_END */

"""

EARLY_GATE = """\t/* Reject the raw frame gate before allocating state or any BO.  The
\t * ready helper checks raw older leaves; map-only and raw live preparation
\t * belong to this file and must not disappear behind an enabled predicate.
\t */
\tif (amdgpu_ps4_uvd_decode_frame_requested() &&
\t    (ps4_map_canary || ps4_uvd_live_prepare ||
\t     !amdgpu_ps4_uvd_decode_frame_options_valid(adev)))
\t\tps4_canary_park(adev, "decode-frame-conflicting-gates", -EINVAL);
"""


def _once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError("nonunique mapper anchor: " + repr(old[:120]))
    return text.replace(old, new, 1)


def _function(text: str, name: str) -> tuple[int, int]:
    """Locate a complete C function in this pinned source (including body)."""
    marker = name + "("
    positions = [i for i in range(len(text)) if text.startswith(marker, i)]
    definitions = []
    for position in positions:
        opening = text.find("{", position)
        semicolon = text.find(";", position)
        if opening < 0 or (0 <= semicolon < opening):
            continue
        line = text.rfind("\n", 0, position) + 1
        # Calls inside another function are indented; definitions are not.
        if text[line:position].startswith(("\t", " ")):
            continue
        depth = 0
        for end in range(opening, len(text)):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    definitions.append((line, end + 1))
                    break
    if len(definitions) != 1:
        raise ValueError("nonunique mapper function: " + name)
    return definitions[0]


def _in_function(text: str, name: str, old: str, new: str) -> str:
    start, end = _function(text, name)
    return text[:start] + _once(text[start:end], old, new) + text[end:]


def transform(source_bytes: bytes) -> bytes:
    """Return the one deterministic additive mapper candidate or fail closed.

    The input must be the exact attempt-03 CREATE mapper.  Reapplication and
    unrelated source drift are rejected rather than partially transforming a
    different source.  No external files or process state are consulted.
    """
    if not isinstance(source_bytes, bytes):
        raise TypeError("mapper source must be bytes")
    if hashlib.sha256(source_bytes).hexdigest() != BASE_SHA256:
        raise ValueError("frozen attempt-03 mapper SHA-256 mismatch")
    text = source_bytes.decode("utf-8")
    text = _once(text, "#define PS4_READY_MAPPED_PAGES 812U\n",
                 "#define PS4_READY_MAPPED_PAGES 812U\n"
                 "#define PS4_FRAME_WORK_BYTES 0x40000ULL\n"
                 "#define PS4_FRAME_MAPPED_PAGES 844U\n"
                 "#define PS4_READY_MAX_MAPPED_PAGES PS4_FRAME_MAPPED_PAGES\n")
    text = _once(text, "\tbool ready_layout;\n", "\tbool ready_layout;\n\tbool frame_mode;\n")
    text = _once(text, "/* LIVE_PREPARE_STATE_BEGIN */", HELPERS + "/* LIVE_PREPARE_STATE_BEGIN */")
    text = _in_function(text, "amdgpu_ps4_map_canary_enabled",
        "\treturn (ps4_map_canary || amdgpu_ps4_uvd_message_create_requested() ||\n",
        "\treturn (ps4_map_canary || amdgpu_ps4_uvd_decode_frame_requested() ||\n"
        "\t\tamdgpu_ps4_uvd_message_create_requested() ||\n")
    text = _in_function(text, "ps4_canary_add_bo_pages",
        "\t    s->mapped_pages > PS4_READY_MAPPED_PAGES ||\n"
        "\t    count > PS4_READY_MAPPED_PAGES - s->mapped_pages)",
        "\t    s->mapped_pages > ps4_canary_expected_pages(s) ||\n"
        "\t    count > ps4_canary_expected_pages(s) - s->mapped_pages)")
    text = _in_function(text, "ps4_canary_ready_pages",
        "kcalloc(PS4_READY_MAPPED_PAGES, sizeof(*s->pages), GFP_KERNEL)",
        "kcalloc(PS4_READY_MAX_MAPPED_PAGES, sizeof(*s->pages), GFP_KERNEL)")
    text = _in_function(text, "ps4_canary_ready_pages",
        "\t\t\tPS4_READY_IB_BYTES, PS4_READY_IB_SLOT);",
        "\t\t\tps4_canary_work_bytes(s), PS4_READY_IB_SLOT);")
    text = _in_function(text, "ps4_canary_ready_pages",
        "s->mapped_pages != PS4_READY_MAPPED_PAGES",
        "s->mapped_pages != ps4_canary_expected_pages(s)")
    text = _once(text,
        "/* Borrow exactly the ring already validated with all812 unique DMA/PFN pages.",
        PT_READBACK + "/* Borrow exactly the ring already validated with all expected unique DMA/PFN pages.")
    text = _in_function(text, "ps4_canary_token_ring",
        "s->mapped_pages != PS4_READY_MAPPED_PAGES",
        "s->mapped_pages != ps4_canary_expected_pages(s)")
    text = _once(text, "void amdgpu_ps4_map_canary_run(struct amdgpu_device *adev)\n",
                 BORROW + "void amdgpu_ps4_map_canary_run(struct amdgpu_device *adev)\n")
    text = _in_function(text, "amdgpu_ps4_map_canary_run",
        "\t/* Reject a selected CREATE leaf even when ready was omitted. */",
        EARLY_GATE + "\t/* Reject a selected CREATE leaf even when ready was omitted. */")
    text = _in_function(text, "amdgpu_ps4_map_canary_run",
        "\ts->ready_mode = amdgpu_ps4_uvd_ready_enabled(adev);\n",
        "\ts->ready_mode = amdgpu_ps4_uvd_ready_enabled(adev);\n"
        "\ts->frame_mode = amdgpu_ps4_uvd_decode_frame_requested();\n")
    text = _in_function(text, "amdgpu_ps4_map_canary_run",
        "ps4_canary_alloc(adev, &s->uvd_ib, PS4_READY_IB_BYTES,",
        "ps4_canary_alloc(adev, &s->uvd_ib, ps4_canary_work_bytes(s),")
    text = _in_function(text, "amdgpu_ps4_map_canary_run",
        "\t\tmemset(s->uvd_ib.cpu, 0, PS4_READY_IB_BYTES);\n",
        "\t\tmemset(s->uvd_ib.cpu, 0, ps4_canary_work_bytes(s));\n"
        "\t\tif (s->frame_mode && !ps4_canary_frame_pt_valid(s))\n"
        '\t\t\tps4_canary_park(adev, "decode-frame-private-pte-readback", -EINVAL);\n')
    text = _in_function(text, "amdgpu_ps4_map_canary_run",
        '"PS4_MAP_CANARY READY_LAYOUT pages=%u guards=777,810..1020 sdma_slots=1021..1023 all_dma_pfn_unique=1\\n",\n'
        "\t\t\t s->mapped_pages);",
        '"PS4_MAP_CANARY READY_LAYOUT pages=%u guards=777,%u..1020 sdma_slots=1021..1023 all_dma_pfn_unique=1\\n",\n'
        "\t\t\t s->mapped_pages, ps4_canary_work_end_slot(s));")
    text = _in_function(text, "amdgpu_ps4_map_canary_run",
        "\t\tstruct amdgpu_ps4_uvd_ring message_backing = { 0 };\n",
        "\t\tstruct amdgpu_ps4_uvd_ring message_backing = { 0 };\n"
        "\t\tstruct amdgpu_ps4_uvd_ring frame_backing = { 0 };\n")
    text = _in_function(text, "amdgpu_ps4_map_canary_run",
        "\t\tif ((amdgpu_ps4_uvd_token_requested() ||\n"
        "\t\t     amdgpu_ps4_uvd_message_create_requested()) &&",
        "\t\tif ((amdgpu_ps4_uvd_token_requested() ||\n"
        "\t\t     amdgpu_ps4_uvd_message_create_requested() ||\n"
        "\t\t     amdgpu_ps4_uvd_decode_frame_requested()) &&")
    text = _in_function(text, "amdgpu_ps4_map_canary_run",
        "\t\t/* IB_DATA_BORROW_BEGIN */",
        "\t\tif (s->frame_mode &&\n"
        "\t\t    !ps4_canary_frame_backing(s, &frame_backing))\n"
        '\t\t\tps4_canary_park(adev, "decode-frame-owned-backing", -EINVAL);\n'
        "\t\t/* IB_DATA_BORROW_BEGIN */")
    text = _in_function(text, "amdgpu_ps4_map_canary_run",
        "\t\t\t(amdgpu_ps4_uvd_token_requested() ||\n"
        "\t\t\t amdgpu_ps4_uvd_message_create_requested()) ? &token_ring : NULL,\n"
        "\t\t\tamdgpu_ps4_uvd_message_create_requested() ? &message_backing :",
        "\t\t\t(amdgpu_ps4_uvd_token_requested() ||\n"
        "\t\t\t amdgpu_ps4_uvd_message_create_requested() ||\n"
        "\t\t\t amdgpu_ps4_uvd_decode_frame_requested()) ? &token_ring : NULL,\n"
        "\t\t\ts->frame_mode ? &frame_backing :\n"
        "\t\t\tamdgpu_ps4_uvd_message_create_requested() ? &message_backing :")
    return text.encode("utf-8")
