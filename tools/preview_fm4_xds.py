#!/usr/bin/env python3
"""Decode the tested FM4 EventBkgd XDS poster format to a PNG preview.

Supports format 50, tiled 32-bit texels, endian 2 (Swap8In32), swizzle ZYXW,
single mip level. This is intended to verify custom poster XDS output before
inserting it into Textures.zip.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PIL import Image


def tiled_offset_2d(x: int, y: int, pitch_blocks: int, log2_bytes_per_block: int) -> int:
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


def decode(source: Path, destination: Path) -> None:
    data = source.read_bytes()
    if len(data) < 52:
        raise ValueError("XDS is shorter than its 52-byte header")
    words = struct.unpack(">13I", data[:52])
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

    if not tiled or fmt != 50 or endian != 2 or swizzle != (2, 1, 0, 3):
        raise ValueError(
            f"Unsupported XDS layout: tiled={tiled}, format={fmt}, endian={endian}, swizzle={swizzle}"
        )
    if min_mip != 0 or max_mip != 0:
        raise ValueError(f"Expected one mip level; found min={min_mip}, max={max_mip}")
    if pitch < width or pitch % 32:
        raise ValueError(f"Unexpected pitch {pitch} for width {width}")

    texels = data[52:]
    rgba = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            offset = tiled_offset_2d(x, y, pitch, 2)
            if offset + 4 > len(texels):
                raise ValueError(f"Texture data too short at pixel ({x},{y})")
            # Xenos Swap8In32 reverses the four bytes. Format 50 then exposes
            # X,Y,Z,W; the template's ZYXW swizzle produces R,G,B,A.
            b0, b1, b2, b3 = texels[offset:offset + 4]
            out = (b1, b2, b3, b0)
            pos = (y * width + x) * 4
            rgba[pos:pos + 4] = bytes(out)

    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGBA", (width, height), bytes(rgba)).save(destination)
    print(f"Decoded: {source}")
    print(f"Dimensions: {width} x {height}; pitch {pitch}; {len(texels):,} texture-data bytes")
    print(f"PNG preview: {destination}")


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python preview_fm4_xds.py <input.xds> <output.png>")
        return 2
    try:
        decode(Path(sys.argv[1]), Path(sys.argv[2]))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
