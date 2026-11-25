#!/usr/bin/env python3
"""Add FM4 Event 749 poster entries by cloning Event 302 ZIP records byte-for-byte.

This avoids recompressing the XMemCompress/LZX streams. The source archive is
never modified; the output is written to a new file.
"""
from __future__ import annotations

import hashlib
import os
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


def fail(message: str) -> None:
    raise RuntimeError(message)


def parse_extra(extra: bytes, *, label: str) -> list[tuple[int, int, int]]:
    """Return (field id, data start, data length), rejecting malformed extras."""
    out = []
    p = 0
    while p < len(extra):
        if p + 4 > len(extra):
            fail(f"Malformed extra field in {label}: truncated header")
        field_id, size = struct.unpack_from("<HH", extra, p)
        start = p + 4
        end = start + size
        if end > len(extra):
            fail(f"Malformed extra field in {label}: field exceeds extra data")
        out.append((field_id, start, size))
        p = end
    return out


def patch_offset_extra(extra: bytes, data_offset: int, *, label: str) -> bytes:
    fields = [x for x in parse_extra(extra, label=label) if x[0] == OFFSET_EXTRA_ID]
    if len(fields) != 1:
        fail(f"Expected exactly one 0x1123 offset field in {label}; found {len(fields)}")
    _, start, size = fields[0]
    if size != 4:
        fail(f"Expected a 4-byte 0x1123 field in {label}; found {size} bytes")
    result = bytearray(extra)
    struct.pack_into("<I", result, start, data_offset)
    return bytes(result)


def find_eocd(data: bytes) -> tuple[int, tuple[int, ...]]:
    start = max(0, len(data) - (EOCD_SIZE + MAX_COMMENT))
    tail = data[start:]
    search = len(tail)
    while True:
        pos = tail.rfind(EOCD_SIG, 0, search)
        if pos < 0:
            fail("Could not find a valid ZIP end-of-central-directory record")
        if pos + EOCD_SIZE <= len(tail):
            fields = struct.unpack_from("<IHHHHIIH", tail, pos)
            comment_len = fields[7]
            if pos + EOCD_SIZE + comment_len == len(tail):
                return start + pos, fields
        search = pos


def parse_archive(data: bytes):
    eocd_pos, eocd = find_eocd(data)
    _, disk_no, cd_disk, disk_entries, total_entries, cd_size, cd_offset, comment_len = eocd
    if disk_no or cd_disk or disk_entries != total_entries:
        fail("Multi-disk ZIP archives are not supported")
    if total_entries == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        fail("ZIP64 archives are not supported by this patcher")
    if cd_offset + cd_size > eocd_pos:
        fail("Central directory extends beyond the end record")

    cd = data[cd_offset:cd_offset + cd_size]
    entries = []
    pos = 0
    while pos < len(cd):
        if pos + CENTRAL_SIZE > len(cd) or cd[pos:pos + 4] != CENTRAL_SIG:
            fail(f"Unexpected data in central directory at byte {pos}")
        vals = struct.unpack_from("<IHHHHHHIIIHHHHHII", cd, pos)
        name_len, extra_len, comment_len_entry = vals[10], vals[11], vals[12]
        record_len = CENTRAL_SIZE + name_len + extra_len + comment_len_entry
        if pos + record_len > len(cd):
            fail("Truncated central-directory record")
        name_start = pos + CENTRAL_SIZE
        name_bytes = cd[name_start:name_start + name_len]
        extra_start = name_start + name_len
        extra = cd[extra_start:extra_start + extra_len]
        comment = cd[extra_start + extra_len:pos + record_len]
        try:
            name = name_bytes.decode("utf-8")
        except UnicodeDecodeError:
            name = name_bytes.decode("cp437")
        entries.append({
            "name": name,
            "record": cd[pos:pos + record_len],
            "vals": vals,
            "name_len": name_len,
            "extra_len": extra_len,
            "comment_len": comment_len_entry,
            "name_bytes": name_bytes,
            "extra": extra,
            "comment": comment,
        })
        pos += record_len
    if pos != cd_size or len(entries) != total_entries:
        fail(f"Central-directory count mismatch: parsed {len(entries)}, EOCD says {total_entries}")
    return eocd_pos, eocd, cd_offset, cd_size, cd, entries


def clone_entry(data: bytes, cd_offset: int, entry: dict, new_name: str, new_local_offset: int):
    vals = entry["vals"]
    flags, method = vals[3], vals[4]
    crc, comp_size, uncomp_size = vals[7], vals[8], vals[9]
    old_name = entry["name"]
    if len(new_name.encode("utf-8")) != entry["name_len"]:
        fail(f"Replacement name length differs for {old_name}")
    if flags & 0x0009:
        fail(f"{old_name} uses encryption or a data descriptor; refusing an unsafe clone")
    if method != 21:
        fail(f"{old_name} is not XMemCompress/LZX (method 21)")

    local_offset = vals[16]
    if local_offset + LOCAL_SIZE > cd_offset:
        fail(f"Local header for {old_name} is outside the local-file area")
    local = data[local_offset:local_offset + LOCAL_SIZE]
    lv = struct.unpack("<IHHHHHIIIHH", local)
    if lv[0] != 0x04034B50:
        fail(f"Missing local header for {old_name}")
    local_flags, local_method = lv[2], lv[3]
    local_crc, local_comp, local_uncomp = lv[6], lv[7], lv[8]
    local_name_len, local_extra_len = lv[9], lv[10]
    if (local_flags, local_method, local_crc, local_comp, local_uncomp) != (flags, method, crc, comp_size, uncomp_size):
        fail(f"Local/central metadata mismatch for {old_name}")
    if local_name_len != entry["name_len"]:
        fail(f"Local filename length mismatch for {old_name}")

    local_name_start = local_offset + LOCAL_SIZE
    local_name = data[local_name_start:local_name_start + local_name_len]
    local_extra_start = local_name_start + local_name_len
    local_extra = data[local_extra_start:local_extra_start + local_extra_len]
    data_start = local_extra_start + local_extra_len
    data_end = data_start + comp_size
    if data_end > cd_offset:
        fail(f"Compressed data for {old_name} runs into the central directory")
    if local_name != entry["name_bytes"]:
        fail(f"Local filename differs from central filename for {old_name}")

    central_fields = [x for x in parse_extra(entry["extra"], label=old_name) if x[0] == OFFSET_EXTRA_ID]
    if len(central_fields) != 1 or central_fields[0][2] != 4:
        fail(f"Expected one 4-byte 0x1123 field for {old_name}")
    _, central_off_pos, _ = central_fields[0]
    recorded_data_offset = struct.unpack_from("<I", entry["extra"], central_off_pos)[0]
    if recorded_data_offset != data_start:
        fail(f"0x1123 offset for {old_name} does not point to its compressed data")

    new_name_bytes = new_name.encode("utf-8")
    new_data_start = new_local_offset + LOCAL_SIZE + local_name_len + local_extra_len
    if new_data_start > 0xFFFFFFFF or new_local_offset > 0xFFFFFFFF:
        fail("New offsets exceed classic ZIP's 32-bit limits")

    local_record = bytearray(data[local_offset:data_end])
    name_at = LOCAL_SIZE
    local_record[name_at:name_at + local_name_len] = new_name_bytes
    if local_extra_len:
        local_fields = parse_extra(local_extra, label=f"local extra {new_name}")
        if any(fid == OFFSET_EXTRA_ID for fid, _, _ in local_fields):
            patched_local_extra = patch_offset_extra(local_extra, new_data_start, label=f"local extra {new_name}")
        else:
            patched_local_extra = local_extra
        local_record[LOCAL_SIZE + local_name_len:LOCAL_SIZE + local_name_len + local_extra_len] = patched_local_extra

    central_record = bytearray(entry["record"])
    central_record[CENTRAL_SIZE:CENTRAL_SIZE + entry["name_len"]] = new_name_bytes
    struct.pack_into("<I", central_record, 42, new_local_offset)
    extra_start = CENTRAL_SIZE + entry["name_len"]
    patched_central_extra = patch_offset_extra(entry["extra"], new_data_start, label=f"central extra {new_name}")
    central_record[extra_start:extra_start + entry["extra_len"]] = patched_central_extra

    compressed = data[data_start:data_end]
    digest = hashlib.sha256(compressed).hexdigest()
    return bytes(local_record), bytes(central_record), len(compressed), digest, new_data_start


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python add_fm4_event749_posters.py <original_Textures.zip> <output_Textures.zip>")
        return 2
    source_path, output_path = map(Path, sys.argv[1:])
    if not source_path.is_file():
        fail(f"Source archive does not exist: {source_path}")
    if output_path.exists():
        fail(f"Output already exists; choose another name or remove it first: {output_path}")
    if source_path.resolve() == output_path.resolve():
        fail("Output path must be different from the original archive")

    original = source_path.read_bytes()
    eocd_pos, eocd, cd_offset, cd_size, cd, entries = parse_archive(original)
    by_name = {e["name"].replace("\\", "/"): e for e in entries}

    pairs = [
        ("UI4/Logos/Posters/EventBkgd302.xds", "UI4/Logos/Posters/EventBkgd749.xds"),
        ("UI4/Logos/Posters_SM/EventBkgd302.xds", "UI4/Logos/Posters_SM/EventBkgd749.xds"),
    ]
    for old, new in pairs:
        if old not in by_name:
            fail(f"Source poster not found: {old}")
        if new in by_name:
            fail(f"Destination poster already exists: {new}")

    cd_end = cd_offset + cd_size
    gap = original[cd_end:eocd_pos]
    if gap:
        fail(f"Found {len(gap)} unexplained bytes between the central directory and EOCD; refusing to alter it")

    local_additions = bytearray()
    central_additions = bytearray()
    details = []
    for old, new in pairs:
        entry = by_name[old]
        new_local_offset = cd_offset + len(local_additions)
        local_blob, central_blob, comp_len, digest, data_offset = clone_entry(
            original, cd_offset, entry, new, new_local_offset
        )
        local_additions.extend(local_blob)
        central_additions.extend(central_blob)
        details.append((old, new, comp_len, digest, data_offset))

    old_count = eocd[4]
    new_count = old_count + len(pairs)
    new_cd_offset = cd_offset + len(local_additions)
    new_cd_size = cd_size + len(central_additions)
    if new_count > 0xFFFF or new_cd_offset > 0xFFFFFFFF or new_cd_size > 0xFFFFFFFF:
        fail("Patched archive would exceed classic ZIP limits")

    eocd_tail = bytearray(original[eocd_pos:])
    struct.pack_into("<H", eocd_tail, 8, new_count)
    struct.pack_into("<H", eocd_tail, 10, new_count)
    struct.pack_into("<I", eocd_tail, 12, new_cd_size)
    struct.pack_into("<I", eocd_tail, 16, new_cd_offset)

    output = original[:cd_offset] + bytes(local_additions) + cd + bytes(central_additions) + bytes(eocd_tail)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as f:
        f.write(output)

    check_eocd_pos, check_eocd, check_cd_offset, check_cd_size, check_cd, check_entries = parse_archive(output)
    if output[:cd_offset] != original[:cd_offset]:
        fail("Internal verification failed: original local-file area changed")
    if check_cd[:cd_size] != cd:
        fail("Internal verification failed: original central-directory records changed")
    check_names = {e["name"].replace("\\", "/") for e in check_entries}
    if not all(new in check_names for _, new in pairs):
        fail("Internal verification failed: new entries are missing")
    if check_eocd[4] != old_count + 2:
        fail("Internal verification failed: ZIP entry count is incorrect")

    print(f"Created: {output_path}")
    print(f"Original entries: {old_count}; new total: {check_eocd[4]}")
    print("Original local-file area and all original central-directory records preserved byte-for-byte.")
    for old, new, comp_len, digest, data_offset in details:
        print(f"Added {new}")
        print(f"  Cloned compressed bytes from {old}: {comp_len} bytes")
        print(f"  SHA-256 of copied compressed stream: {digest}")
        print(f"  New absolute data offset: {data_offset}")
    print(f"Output size: {len(output):,} bytes")
    print("Run lzxzip list/test on this output before trying it in Xenia.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
