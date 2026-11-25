#!/usr/bin/env python3
"""Append staged method-21 XDS entries without rebuilding the original ZIP entries.

First create a staging archive with CuteTenshii/lzxzip:
    lzxzip create staging.zip poster_staging

Then run:
    python append_fm4_lzx_entries.py original_Textures.zip staging.zip output.zip

The original archive's local-file area and central-directory records are copied
byte-for-byte. New method-21 entries are copied from the staging archive; the
central 0x1123 absolute-data-offset field, local-header offsets and EOCD values
are relocated. The input archives are never modified.
"""
from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path

EOCD_SIG = b"PK\x05\x06"
CENTRAL_SIG = b"PK\x01\x02"
LOCAL_SIG = b"PK\x03\x04"
OFFSET_EXTRA_ID = 0x1123
EOCD_SIZE = 22
CENTRAL_SIZE = 46
LOCAL_SIZE = 30
MAX_COMMENT = 0xFFFF
METHOD_LZX = 21


def fail(message: str) -> None:
    raise RuntimeError(message)


def find_eocd(data: bytes, label: str):
    start = max(0, len(data) - EOCD_SIZE - MAX_COMMENT)
    tail = data[start:]
    search = len(tail)
    while True:
        pos = tail.rfind(EOCD_SIG, 0, search)
        if pos < 0:
            fail(f"{label}: ZIP end-of-central-directory record not found")
        if pos + EOCD_SIZE <= len(tail):
            fields = struct.unpack_from("<IHHHHIIH", tail, pos)
            if pos + EOCD_SIZE + fields[7] == len(tail):
                return start + pos, fields
        search = pos


def parse_extra(extra: bytes, label: str):
    fields = []
    pos = 0
    while pos < len(extra):
        if pos + 4 > len(extra):
            fail(f"{label}: malformed extra-field header")
        field_id, size = struct.unpack_from("<HH", extra, pos)
        data_start = pos + 4
        if data_start + size > len(extra):
            fail(f"{label}: truncated extra-field data")
        fields.append((field_id, data_start, size))
        pos = data_start + size
    return fields


def patch_offset(extra: bytes, data_offset: int, label: str) -> bytes:
    matches = [field for field in parse_extra(extra, label) if field[0] == OFFSET_EXTRA_ID]
    if len(matches) != 1 or matches[0][2] != 4:
        fail(f"{label}: expected exactly one 4-byte 0x1123 field")
    result = bytearray(extra)
    struct.pack_into("<I", result, matches[0][1], data_offset)
    return bytes(result)


def parse_archive(data: bytes, label: str):
    eocd_pos, eocd = find_eocd(data, label)
    _, disk_no, cd_disk, disk_count, total_count, cd_size, cd_offset, _ = eocd
    if disk_no or cd_disk or disk_count != total_count:
        fail(f"{label}: multi-disk ZIP is unsupported")
    if total_count == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        fail(f"{label}: ZIP64 is unsupported")
    cd_end = cd_offset + cd_size
    if cd_end > eocd_pos:
        fail(f"{label}: central directory extends beyond EOCD")
    if cd_end != eocd_pos:
        fail(f"{label}: unexplained bytes between central directory and EOCD")

    cd = data[cd_offset:cd_end]
    entries = []
    pos = 0
    while pos < len(cd):
        if pos + CENTRAL_SIZE > len(cd) or cd[pos:pos + 4] != CENTRAL_SIG:
            fail(f"{label}: invalid central-directory signature at {pos}")
        values = struct.unpack_from("<IHHHHHHIIIHHHHHII", cd, pos)
        name_len, extra_len, comment_len = values[10], values[11], values[12]
        record_len = CENTRAL_SIZE + name_len + extra_len + comment_len
        if pos + record_len > len(cd):
            fail(f"{label}: truncated central-directory record")
        name_start = pos + CENTRAL_SIZE
        raw_name = cd[name_start:name_start + name_len]
        extra_start = name_start + name_len
        extra = cd[extra_start:extra_start + extra_len]
        try:
            name = raw_name.decode("utf-8" if (values[3] & 0x800) else "cp437")
        except UnicodeDecodeError:
            name = raw_name.decode("cp437")
        entries.append({
            "name": name.replace("\\", "/"),
            "raw_name": raw_name,
            "record": cd[pos:pos + record_len],
            "values": values,
            "extra": extra,
        })
        pos += record_len

    if pos != cd_size or len(entries) != total_count:
        fail(f"{label}: parsed {len(entries)} entries but EOCD declares {total_count}")
    return eocd_pos, eocd, cd_offset, cd_size, cd, entries


def relocate_staged_entry(stage: bytes, entry: dict, new_local_offset: int):
    name = entry["name"]
    values = entry["values"]
    flags, method = values[3], values[4]
    crc, comp_size, uncomp_size = values[7], values[8], values[9]
    local_offset = values[16]

    if method != METHOD_LZX:
        fail(f"{name}: staging method is {method}; expected LZX method 21")
    if flags & 1:
        fail(f"{name}: encrypted entry is unsupported")
    if flags & 8:
        fail(f"{name}: data-descriptor entries are unsupported")
    if local_offset + LOCAL_SIZE > len(stage) or stage[local_offset:local_offset + 4] != LOCAL_SIG:
        fail(f"{name}: local-file header missing")

    local_values = struct.unpack_from("<IHHHHHIIIHH", stage, local_offset)
    if (local_values[2], local_values[3], local_values[6], local_values[7], local_values[8]) != (
        flags, method, crc, comp_size, uncomp_size
    ):
        fail(f"{name}: local and central metadata disagree")

    local_name_len, local_extra_len = local_values[9], local_values[10]
    if local_name_len != len(entry["raw_name"]):
        fail(f"{name}: local filename length mismatch")
    local_name_start = local_offset + LOCAL_SIZE
    local_name = stage[local_name_start:local_name_start + local_name_len]
    if local_name != entry["raw_name"]:
        fail(f"{name}: local and central filenames differ")
    local_extra_start = local_name_start + local_name_len
    local_extra = stage[local_extra_start:local_extra_start + local_extra_len]
    data_start = local_extra_start + local_extra_len
    data_end = data_start + comp_size
    if data_end > len(stage):
        fail(f"{name}: compressed data extends beyond staging archive")

    central_offset_fields = [
        field for field in parse_extra(entry["extra"], name)
        if field[0] == OFFSET_EXTRA_ID
    ]
    if len(central_offset_fields) != 1 or central_offset_fields[0][2] != 4:
        fail(f"{name}: expected exactly one 4-byte 0x1123 central extra field")
    recorded_data_start = struct.unpack_from(
        "<I", entry["extra"], central_offset_fields[0][1]
    )[0]
    if recorded_data_start != data_start:
        fail(f"{name}: staged 0x1123 field does not point to compressed data")

    new_data_start = new_local_offset + LOCAL_SIZE + local_name_len + local_extra_len
    if new_data_start > 0xFFFFFFFF or new_local_offset > 0xFFFFFFFF:
        fail(f"{name}: relocated offset exceeds ZIP32")

    local_record = bytearray(stage[local_offset:data_end])
    if local_extra and any(
        field[0] == OFFSET_EXTRA_ID for field in parse_extra(local_extra, name + " local extra")
    ):
        fixed_local_extra = patch_offset(local_extra, new_data_start, name + " local extra")
        extra_pos = LOCAL_SIZE + local_name_len
        local_record[extra_pos:extra_pos + local_extra_len] = fixed_local_extra

    central_record = bytearray(entry["record"])
    struct.pack_into("<I", central_record, 42, new_local_offset)
    central_extra_pos = CENTRAL_SIZE + len(entry["raw_name"])
    fixed_central_extra = patch_offset(entry["extra"], new_data_start, name + " central extra")
    central_record[
        central_extra_pos:central_extra_pos + len(entry["extra"])
    ] = fixed_central_extra

    compressed = stage[data_start:data_end]
    return (
        bytes(local_record),
        bytes(central_record),
        comp_size,
        uncomp_size,
        new_data_start,
        hashlib.sha256(compressed).hexdigest(),
    )


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "Usage: python append_fm4_lzx_entries.py "
            "<original_Textures.zip> <staging.zip> <output.zip>"
        )
        return 2

    original_path, staging_path, output_path = map(Path, sys.argv[1:])
    if not original_path.is_file():
        fail(f"Original archive not found: {original_path}")
    if not staging_path.is_file():
        fail(f"Staging archive not found: {staging_path}")
    if output_path.exists():
        fail(f"Output already exists; choose a new name: {output_path}")
    if output_path.resolve() in {original_path.resolve(), staging_path.resolve()}:
        fail("Output must be different from both input archives")

    original = original_path.read_bytes()
    staging = staging_path.read_bytes()

    _, original_eocd, original_cd_offset, original_cd_size, original_cd, original_entries = (
        parse_archive(original, "original")
    )
    _, _, _, _, _, staged_entries = parse_archive(staging, "staging")

    original_names = {entry["name"].casefold() for entry in original_entries}
    staged_names = [entry["name"] for entry in staged_entries]
    if len({name.casefold() for name in staged_names}) != len(staged_names):
        fail("Staging archive contains duplicate filenames")

    for name in staged_names:
        if name.casefold() in original_names:
            fail(f"Entry already exists in original archive: {name}")
        if not name.lower().endswith(".xds"):
            fail(f"Only .xds files may be appended; refusing: {name}")

    local_additions = bytearray()
    central_additions = bytearray()
    summaries = []

    for entry in staged_entries:
        new_local_offset = original_cd_offset + len(local_additions)
        local_blob, central_blob, compressed_size, uncompressed_size, data_offset, digest = (
            relocate_staged_entry(staging, entry, new_local_offset)
        )
        local_additions.extend(local_blob)
        central_additions.extend(central_blob)
        summaries.append(
            (entry["name"], compressed_size, uncompressed_size, data_offset, digest)
        )

    if not staged_entries:
        fail("Staging archive contains no files")

    old_count = original_eocd[4]
    new_count = old_count + len(staged_entries)
    new_cd_offset = original_cd_offset + len(local_additions)
    new_cd_size = original_cd_size + len(central_additions)
    if new_count > 0xFFFF or new_cd_offset > 0xFFFFFFFF or new_cd_size > 0xFFFFFFFF:
        fail("Result exceeds classic ZIP32 limits")

    eocd_pos, _, _, _, _, _ = parse_archive(original, "original")
    eocd_tail = bytearray(original[eocd_pos:])
    struct.pack_into("<H", eocd_tail, 8, new_count)
    struct.pack_into("<H", eocd_tail, 10, new_count)
    struct.pack_into("<I", eocd_tail, 12, new_cd_size)
    struct.pack_into("<I", eocd_tail, 16, new_cd_offset)

    output = (
        original[:original_cd_offset]
        + bytes(local_additions)
        + original_cd
        + bytes(central_additions)
        + bytes(eocd_tail)
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as handle:
        handle.write(output)

    # Verify the output structurally and byte-for-byte for all original records.
    _, check_eocd, _, _, check_cd, check_entries = parse_archive(output, "output")
    if output[:original_cd_offset] != original[:original_cd_offset]:
        fail("Post-write check: original local-file area changed")
    if check_cd[:original_cd_size] != original_cd:
        fail("Post-write check: original central-directory records changed")
    if check_eocd[4] != new_count or len(check_entries) != new_count:
        fail("Post-write check: entry count mismatch")

    print(f"Created: {output_path}")
    print(f"Original entries: {old_count}; staged additions: {len(staged_entries)}; total: {new_count}")
    print("Original local-file area and central-directory records preserved byte-for-byte.")
    for name, compressed_size, uncompressed_size, data_offset, digest in summaries:
        print(f"Added: {name}")
        print(f"  Compressed size: {compressed_size:,} bytes (uncompressed {uncompressed_size:,})")
        print(f"  Absolute compressed-data offset: {data_offset}")
        print(f"  Compressed-stream SHA-256: {digest}")
    print(f"Output size: {len(output):,} bytes")
    print("Next: run lzxzip list and test; do not install until those checks pass.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
