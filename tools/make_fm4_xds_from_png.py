#!/usr/bin/env python3
"""Build a replacement FM4 poster .xds from a PNG and an existing .xds template.

The template's 52-byte Xenos resource/fetch-constant header is kept verbatim.
The PNG is fitted to the template dimensions, converted to the template's
32-bit ARGB-in-memory channel order, and written in Xenos 32x32 tiled order.

This first version intentionally supports the known FM4 EventBkgd poster format:
format 50 (8_8_8_8_AS_16_16_16_16), tiled, endian 2 (Swap8In32), swizzle ZYXW,
one mip level. It refuses other formats instead of guessing.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

from PIL import Image, ImageOps

HEADER_SIZE = 52
FORMAT_8888_AS_1616 = 50
ENDIAN_SWAP8_IN_32 = 2
SWIZZLE_ZYXW = (2, 1, 0, 3)


def tiled_offset_2d(x: int, y: int, pitch_blocks: int, log2_bytes_per_block: int) -> int:
    """Xenos XGAddress2DTiledOffset, based on CuteTenshii/xds."""
    macro_outer = ((y >> 5) * (pitch_blocks >> 5)) << (log2_bytes_per_block + 7)
    micro_outer = ((y & 6) << 2) << log2_bytes_per_block
    outer = (
        macro_outer
        + ((micro_outer & ~0xF) << 1)
        + (micro_outer & 0xF)
        + ((y & 8) << (3 + log2_bytes_per_block))
        + ((y & 1) << 4)
    )
    macro_inner = (x >> 5) << (log2_bytes_per_block + 7)
    micro_inner = (x & 7) << log2_bytes_per_block
    offset = outer + macro_inner + ((micro_inner & ~0xF) << 1) + (micro_inner & 0xF)
    return (
        ((offset & ~0x1FF) << 3)
        + ((offset & 0x1C0) << 2)
        + (offset & 0x3F)
        + ((y & 16) << 7)
        + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6)
    )


def read_template(path: Path) -> tuple[bytes, int, int, int, int, int]:
    blob = path.read_bytes()
    if len(blob) < HEADER_SIZE:
        raise ValueError(f"Template is too short: {path}")
    words = struct.unpack_from(">13I", blob, 0)
    w0, w1, w2, w3, w4, w5 = words[7:13]

    tiled = bool(w0 >> 31)
    pitch = ((w0 >> 22) & 0x1FF) << 5
    fmt = w1 & 0x3F
    endian = (w1 >> 6) & 3
    width = (w2 & 0x1FFF) + 1
    height = ((w2 >> 13) & 0x1FFF) + 1
    swizzle_bits = (w3 >> 1) & 0xFFF
    swizzle = tuple((swizzle_bits >> (3 * i)) & 7 for i in range(4))
    min_mip = (w4 >> 2) & 0xF
    max_mip = (w4 >> 6) & 0xF

    if not tiled:
        raise ValueError("Template is not tiled")
    if fmt != FORMAT_8888_AS_1616:
        raise ValueError(f"Unsupported template format {fmt}; expected 50")
    if endian != ENDIAN_SWAP8_IN_32:
        raise ValueError(f"Unsupported endian mode {endian}; expected 2 (Swap8In32)")
    if swizzle != SWIZZLE_ZYXW:
        raise ValueError(f"Unsupported swizzle {swizzle}; expected ZYXW (2,1,0,3)")
    if min_mip != 0 or max_mip != 0:
        raise ValueError(f"Expected a single mip level; found min={min_mip}, max={max_mip}")
    if pitch < width or pitch % 32 != 0:
        raise ValueError(f"Unexpected pitch {pitch} for width {width}")

    return blob[:HEADER_SIZE], width, height, pitch, len(blob) - HEADER_SIZE, fmt


def make_xds(template: Path, image_path: Path, output: Path, force: bool) -> None:
    header, width, height, pitch, data_size, fmt = read_template(template)
    if output.exists() and not force:
        raise FileExistsError(f"Output already exists: {output} (use --force to replace it)")

    with Image.open(image_path) as source:
        image = ImageOps.fit(
            source.convert("RGBA"),
            (width, height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    raw = bytearray(data_size)
    pixels = image.load()
    max_end = 0
    pitch_blocks = pitch
    log2_bytes_per_block = 2

    for y in range(height):
        for x in range(width):
            off = tiled_offset_2d(x, y, pitch_blocks, log2_bytes_per_block)
            if off + 4 > data_size:
                raise ValueError(
                    f"Tiled offset exceeds template data at ({x},{y}): {off+4} > {data_size}"
                )
            r, g, b, a = pixels[x, y]
            raw[off:off + 4] = bytes((a, r, g, b))
            max_end = max(max_end, off + 4)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(header + raw)
    print(f"Created: {output}")
    print(f"Template: {template}")
    print(f"Image: {image_path}")
    print(f"Dimensions: {width} x {height}")
    print(f"Format: {fmt}; tiled; pitch {pitch}; endian 2; swizzle ZYXW; one mip")
    print(f"Texture data: {data_size} bytes; last used byte: {max_end}")
    print(f"Output size: {output.stat().st_size} bytes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path, help="original EventBkgd302.xds")
    parser.add_argument("image", type=Path, help="PNG artwork to import")
    parser.add_argument("output", type=Path, help="new EventBkgd749.xds path")
    parser.add_argument("--force", action="store_true", help="overwrite output if it exists")
    args = parser.parse_args()
    try:
        make_xds(args.template, args.image, args.output, args.force)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
