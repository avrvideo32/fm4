#!/usr/bin/env python3
"""Replace existing FM4 XDS entries using method-21 streams from a staging ZIP.

1. Put replacement XDS files in a folder using their exact archive paths.
2. Create a staging archive:
       lzxzip create staging.zip poster_staging
3. Replace the target entries:
       python replace_fm4_lzx_entries.py working_Textures.zip staging.zip output.zip

The original archive's local-file area and all unaffected central-directory
records are preserved byte-for-byte. Old replaced local payloads remain as
unreferenced bytes in the local-file area; new local records are appended
before the central directory. Normal local-header offsets and central 0x1123
absolute payload offsets are updated for replaced records.
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
            fail(f"{label}: ZIP end record not found")
        if pos + EOCD_SIZE <= len(tail):
            values = struct.unpack_from("<IHHHHIIH", tail, pos)
            if pos + EOCD_SIZE + values[7] == len(tail):
                return start + pos, values
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
        fail(f"{label}: multi-disk ZIP unsupported")
    if total_count == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        fail(f"{label}: ZIP64 unsupported")
    cd_end = cd_offset + cd_size
    if cd_end != eocd_pos:
        fail(f"{label}: unexpected gap between central directory and EOCD")
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
        name = raw_name.decode("utf-8" if values[3] & 0x800 else "cp437").replace("\\", "/")
        entries.append({
            "name": name,
            "raw_name": raw_name,
            "record": cd[pos:pos + record_len],
            "values": values,
            "extra": extra,
        })
        pos += record_len
    if pos != cd_size or len(entries) != total_count:
        fail(f"{label}: central-directory entry count mismatch")
    return eocd_pos, eocd, cd_offset, cd_size, cd, entries


def prepare_replacement(staging: bytes, staged_entry: dict, original_entry: dict, new_local_offset: int):
    name = staged_entry["name"]
    sv = staged_entry["values"]
    ov = original_entry["values"]

    flags, method = sv[3], sv[4]
    crc, compressed_size, uncompressed_size = sv[7], sv[8], sv[9]
    local_offset = sv[16]

    if method != METHOD_LZX:
        fail(f"{name}: staging method is {method}; expected method 21")
    if flags & 1:
        fail(f"{name}: encrypted entries unsupported")
    if flags & 8:
        fail(f"{name}: data descriptors unsupported")
    if staged_entry["raw_name"] != original_entry["raw_name"]:
        fail(f"{name}: staged filename spelling differs from archive filename")

    if local_offset + LOCAL_SIZE > len(staging) or staging[local_offset:local_offset + 4] != LOCAL_SIG:
        fail(f"{name}: staging local header missing")
    lv = struct.unpack_from("<IHHHHHIIIHH", staging, local_offset)
    if (lv[2], lv[3], lv[6], lv[7], lv[8]) != (flags, method, crc, compressed_size, uncompressed_size):
        fail(f"{name}: staging local and central metadata disagree")

    name_len, local_extra_len = lv[9], lv[10]
    if name_len != len(staged_entry["raw_name"]):
        fail(f"{name}: local filename length mismatch")
    name_start = local_offset + LOCAL_SIZE
    local_name = staging[name_start:name_start + name_len]
    if local_name != staged_entry["raw_name"]:
        fail(f"{name}: staging local and central names differ")
    extra_start = name_start + name_len
    local_extra = staging[extra_start:extra_start + local_extra_len]
    data_start = extra_start + local_extra_len
    data_end = data_start + compressed_size
    if data_end > len(staging):
        fail(f"{name}: staged compressed payload extends beyond archive")

    stage_offset_fields = [
        f for f in parse_extra(staged_entry["extra"], name + " staging central extra")
        if f[0] == OFFSET_EXTRA_ID
    ]
    if len(stage_offset_fields) != 1 or stage_offset_fields[0][2] != 4:
        fail(f"{name}: staging central directory needs one 4-byte 0x1123 field")
    actual_stage_offset = struct.unpack_from("<I", staged_entry["extra"], stage_offset_fields[0][1])[0]
    if actual_stage_offset != data_start:
        fail(f"{name}: staging 0x1123 field does not point at its compressed payload")

    new_data_offset = new_local_offset + LOCAL_SIZE + name_len + local_extra_len
    if new_data_offset > 0xFFFFFFFF or new_local_offset > 0xFFFFFFFF:
        fail(f"{name}: new offset exceeds ZIP32")

    local_record = bytearray(staging[local_offset:data_end])
    if local_extra and any(
        f[0] == OFFSET_EXTRA_ID for f in parse_extra(local_extra, name + " staging local extra")
    ):
        fixed = patch_offset(local_extra, new_data_offset, name + " staging local extra")
        local_record[LOCAL_SIZE + name_len:LOCAL_SIZE + name_len + local_extra_len] = fixed

    # Start with the original central-directory record so its comments,
    # attributes and non-offset extra fields remain intact.
    central_record = bytearray(original_entry["record"])
    struct.pack_into("<H", central_record, 8, flags)
    struct.pack_into("<H", central_record, 10, method)
    struct.pack_into("<HH", central_record, 12, sv[5], sv[6])
    struct.pack_into("<III", central_record, 16, crc, compressed_size, uncompressed_size)
    struct.pack_into("<I", central_record, 42, new_local_offset)

    central_extra_pos = CENTRAL_SIZE + len(original_entry["raw_name"])
    fixed_extra = patch_offset(original_entry["extra"], new_data_offset, name + " original central extra")
    central_record[
        central_extra_pos:central_extra_pos + len(original_entry["extra"])
    ] = fixed_extra

    compressed = staging[data_start:data_end]
    return (
        bytes(local_record),
        bytes(central_record),
        compressed_size,
        uncompressed_size,
        new_data_offset,
        hashlib.sha256(compressed).hexdigest(),
    )


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: python replace_fm4_lzx_entries.py <working_Textures.zip> <staging.zip> <output.zip>")
        return 2

    source_path, staging_path, output_path = map(Path, sys.argv[1:])
    for path in (source_path, staging_path):
        if not path.is_file():
            fail(f"Input file not found: {path}")
    if output_path.exists():
        fail(f"Output already exists; choose a new name: {output_path}")
    if output_path.resolve() in {source_path.resolve(), staging_path.resolve()}:
        fail("Output must be separate from both inputs")

    source = source_path.read_bytes()
    staging = staging_path.read_bytes()
    eocd_pos, eocd, cd_offset, cd_size, source_cd, source_entries = parse_archive(source, "source")
    _, _, _, _, _, staged_entries = parse_archive(staging, "staging")

    originals = {entry["name"].casefold(): entry for entry in source_entries}
    stages = {}
    for entry in staged_entries:
        key = entry["name"].casefold()
        if key in stages:
            fail(f"Duplicate staging filename: {entry['name']}")
        if key not in originals:
            fail(f"Replacement target does not exist in source archive: {entry['name']}")
        stages[key] = entry

    if not stages:
        fail("Staging archive contains no entries")

    additions = bytearray()
    replacement_records = {}
    summaries = []
    for key, stage_entry in stages.items():
        original_entry = originals[key]
        new_local_offset = cd_offset + len(additions)
        local_blob, central_blob, comp, uncomp, payload_offset, digest = prepare_replacement(
            staging, stage_entry, original_entry, new_local_offset
        )
        additions.extend(local_blob)
        replacement_records[key] = central_blob
        summaries.append((stage_entry["name"], comp, uncomp, payload_offset, digest))

    # Preserve central-directory order and every untouched record exactly.
    new_cd_parts = []
    for original_entry in source_entries:
        key = original_entry["name"].casefold()
        new_cd_parts.append(replacement_records.get(key, original_entry["record"]))
    new_cd = b"".join(new_cd_parts)
    new_cd_offset = cd_offset + len(additions)
    if new_cd_offset > 0xFFFFFFFF or len(new_cd) > 0xFFFFFFFF:
        fail("Output exceeds ZIP32 limits")

    eocd_tail = bytearray(source[eocd_pos:])
    struct.pack_into("<I", eocd_tail, 12, len(new_cd))
    struct.pack_into("<I", eocd_tail, 16, new_cd_offset)
    # Entry counts remain unchanged because we replace existing members.

    output = source[:cd_offset] + bytes(additions) + new_cd + bytes(eocd_tail)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as handle:
        handle.write(output)

    _, check_eocd, _, _, check_cd, check_entries = parse_archive(output, "output")
    if output[:cd_offset] != source[:cd_offset]:
        fail("Post-write verification failed: original local-file area changed")
    if len(check_entries) != len(source_entries) or check_eocd[4] != len(source_entries):
        fail("Post-write verification failed: entry count changed")
    expected_names = {entry["name"].casefold() for entry in source_entries}
    if {entry["name"].casefold() for entry in check_entries} != expected_names:
        fail("Post-write verification failed: member name set changed")

    untouched_expected = {
        entry["name"].casefold(): entry["record"]
        for entry in source_entries if entry["name"].casefold() not in replacement_records
    }
    for entry in check_entries:
        key = entry["name"].casefold()
        if key in untouched_expected and entry["record"] != untouched_expected[key]:
            fail(f"Post-write verification failed: untouched central record changed: {entry['name']}")

    print(f"Created: {output_path}")
    print(f"Entries retained: {len(source_entries)}; entries replaced: {len(stages)}")
    print("Original local-file area and every untouched central-directory record preserved byte-for-byte.")
    for name, comp, uncomp, payload_offset, digest in summaries:
        print(f"Replaced: {name}")
        print(f"  Compressed: {comp:,} bytes; uncompressed: {uncomp:,} bytes")
        print(f"  New 0x1123 payload offset: {payload_offset}")
        print(f"  Compressed SHA-256: {digest}")
    print(f"Output size: {len(output):,} bytes")
    print("Run lzxzip list and test before trying in Xenia.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
