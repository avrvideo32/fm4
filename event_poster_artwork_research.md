# FM4 custom career-event poster artwork — working notes

Last updated: 2026-11-25

This note records the tested archive workflow and the current experiment for creating original event posters for Forza Motorsport 4 (FM4) under Xenia Canary. It is intended as handoff context for future work.

## Current status

- Event 749 is a custom career event cloned from Event 302.
- FM4's poster archive is `Media\UI\Textures.zip`.
- Event poster assets use the event ID in their filenames. For Event 302, the relevant entries are:
  - `UI4/Logos/Posters/EventBkgd302.xds`
  - `UI4/Logos/Posters_SM/EventBkgd302.xds`
- The corresponding entries for Event 749 are:
  - `UI4/Logos/Posters/EventBkgd749.xds`
  - `UI4/Logos/Posters_SM/EventBkgd749.xds`
- A modified archive made with the byte-preserving patcher (below) successfully loaded in Xenia. This confirms the archive can be extended safely using this method.
- The user has **not yet confirmed that Event 749 actually displays its poster in the career UI**.
- A custom full-size Event 749 XDS was generated from a sample PNG and its output size matches the Event 302 template exactly. **Its visual decoding and in-game display have not yet been verified.**
- The small Event 749 XDS has **not yet been generated**.

## Paths used on the user's Windows PC

The current local workspace is `D:\code\py\fm4`.

Original extracted game archive:
```text
D:\hlam\fm4\Forza-Motorsport-4-USA-Disc-1-Play-Disc-XBOX-360\Forza-Motorsport-4-USA-Disc-1-Play-Disc-XBOX-360\Media\UI\Textures.zip
```

Extracted original poster templates:
```text
D:\code\py\fm4\poster_originals\UI4\Logos\Posters\EventBkgd302.xds
D:\code\py\fm4\poster_originals\UI4\Logos\Posters_SM\EventBkgd302.xds
```

Generated sample art:
```text
D:\code\py\fm4\fm4_event749_artwork_922x617.png
```
The sample is a cinematic red supercar / sunset race circuit image with “WORLD CHAMPIONSHIP” text, 922 x 617 pixels, RGB.

Full-size custom XDS currently generated:
```text
D:\code\py\fm4\poster_changes\UI4\Logos\Posters\EventBkgd749.xds
```
Reported size: **2,375,732 bytes**, exactly matching the Event 302 full-size template.

Working archive patcher output:
```text
D:\code\py\fm4\Textures_event749_rawcopy.zip
```
This archive was copied into the game's `Media\UI\Textures.zip` and Xenia loaded the game successfully.

## Exact poster texture specifications

### Full-size Event 302 template

Source: `UI4/Logos/Posters/EventBkgd302.xds`

| Property | Value |
|---|---:|
| File size | 2,375,732 bytes |
| Dimensions | 922 x 617 |
| Xenos format | 50 — `8_8_8_8_AS_16_16_16_16` |
| Tiled | Yes |
| Pitch | 928 texels |
| Endian | 2 — `Swap8In32` |
| Mip levels | 1 |
| Packed mips flag | Yes |
| Swizzle | `ZYXW` |
| Header | 52 bytes |

The converter currently supports this precise format only. It preserves the 52-byte template header, resizes/crops the PNG to the template size, creates 32-bit texel data in Xenos tiled order, and writes a same-length XDS.

### Small Event 302 template

Source: `UI4/Logos/Posters_SM/EventBkgd302.xds`

| Property | Value |
|---|---:|
| File size | 98,356 bytes |
| Dimensions | 185 x 123 |
| Xenos format | 50 |
| Tiled | Yes |
| Pitch | 192 texels |
| Endian | 2 |
| Mip levels | 1 |
| Packed mips flag | Yes |
| Swizzle | **Not yet checked** |

Before generating the small version, run the same header diagnostic and print its swizzle. The expected channel layout must be checked from the actual small template rather than assumed.

## Archive editing: what works and what fails

### Do not use `lzxzip update` for this FM4 archive

The CuteTenshii `lzxzip` utility built successfully and can list, extract and test the archive. Its `update` command adds new entries and copies untouched compressed data, but it reconstructs ZIP local headers and the central directory.

Test results:
- Original archive: 4,544 entries, 435,538,044 bytes.
- `lzxzip update` archive: 4,546 entries; `lzxzip test` reported 0 failures.
- The two new files appeared with compressed sizes 313,873 and 22,807 bytes, versus the originals' 314,226 and 22,114 bytes.
- Xenia froze when the `lzxzip update` archive was installed.
- A structural comparison showed the 4,544 original entries retained their order and their parsed metadata matched, but this did not make the repacked archive game-compatible.

### Use byte-preserving cloning of existing archive entries

The working approach is to clone the original Event 302 ZIP records directly:
- Copy each original local record and compressed LZX bytes as-is.
- Rename the copied entry from Event 302 to Event 749.
- Update the Xbox-specific absolute-data-offset extra field `0x1123`.
- Add the two new central-directory records.
- Update the ZIP end-of-central-directory counts and offsets.
- Preserve all original local-file bytes and original central-directory records.

The original archive has the Xbox extra field `0x1123` on all 4,544 entries. The patcher validates that each source entry has one 4-byte offset field pointing to its compressed data.

Successful byte-copy patcher output (reported):
- Original entries: 4,544; new total: 4,546.
- Full poster compressed stream copied: 314,226 bytes.
- Small poster compressed stream copied: 22,114 bytes.
- Output size: 435,874,694 bytes.
- `lzxzip list` showed the expected Event 749 entries and original compressed sizes.
- `lzxzip test` was reported as passing: 4,546 entries, 0 failures.
- **The game loaded in Xenia after this archive was installed.**

Important: `add_fm4_event749_posters.py` is a first-time *add* patcher. It refuses to run if the Event 749 entries already exist. To test new artwork, a future replacement-aware patcher is needed, or start from the clean original archive and patch a new output. Do not use the already patched archive as the source for a second add.

### Existing archive tool paths

```text
D:\code\py\fm4\lzx\build\bin\Release\lzxzip.exe
```

Example validation:
```powershell
$lzx = "D:\code\py\fm4\lzx\build\bin\Release\lzxzip.exe"
$output = "D:\code\py\fm4\Textures_event749_rawcopy.zip"

& $lzx list $output | Select-String "EventBkgd749"
& $lzx test $output
```

Do not copy an experimental archive into the game until it passes structural checks. Keep the user's separately saved original archive unchanged.

## Creating the XDS from the sample PNG

The helper script is `make_fm4_xds_from_png.py`. It:
- Reads the 52-byte header from an original XDS template.
- Checks the template is tiled format 50, endian 2, swizzle ZYXW, one mip level, and has the expected pitch constraints.
- Fits the input image to the template dimensions using Lanczos.
- Writes the 32-bit texel values into Xenos tiled positions.
- Accounts for the template's endian swap and ZYXW swizzle by storing bytes as A,R,G,B before the Swap8In32 transformation.
- Preserves the template data area length, so the output file has the same byte length as the template.

The full-size command already used:
```powershell
$script = "D:\code\py\fm4\make_fm4_xds_from_png.py"
$template = "D:\code\py\fm4\poster_originals\UI4\Logos\Posters\EventBkgd302.xds"
$image = "D:\code\py\fm4\fm4_event749_artwork_922x617.png"
$output = "D:\code\py\fm4\poster_changes\UI4\Logos\Posters\EventBkgd749.xds"

python $script $template $image $output --force
```

Python dependency:
```powershell
python -m pip install pillow
```

**Validation still required:** the generated XDS has only been checked for output length (2,375,732 bytes). Next, decode it using the open-source XDS reader or another trusted method and compare its rendered pixels to the source PNG. Then test in Xenia. Same-size output alone does not prove that the texture contents are correct.

## ForzaStudio EVO dependencies and alternatives

ForzaStudio EVO is not currently usable by the user because its README requires:
- `xcompress.dll`: proprietary Microsoft Xbox 360 SDK component, used for XMemCompress / XMemDecompress.
- `xds.dll`: from the Forza Studio 4.6 release, used for XDS conversion.

The user asked about `GoobyCorp/Xbox-360-Crypto`. That repository's LZX libraries target Xbox boot images and LZX-DELTA workflows; they are **not a drop-in replacement** for the XMemCompress API expected by EVO.

Useful public repositories:
- ForzaStudio EVO: https://github.com/FMdangaina/ForzaStudio-EVO
- CuteTenshii XDS reader/converter: https://github.com/CuteTenshii/xds
- CuteTenshii LZX ZIP reader/writer: https://github.com/CuteTenshii/lzx
- GoobyCorp Xbox 360 Crypto (not a replacement for EVO's XMem DLL): https://github.com/GoobyCorp/Xbox-360-Crypto

The open-source `CuteTenshii/xds` project can read/decode XDS and export PNG/DDS, but its documented command-line tool does not provide an XDS encoder/importer. The custom Python encoder is an experiment for the specific EventBkgd poster format.

## Next steps

1. Check the small template's swizzle and all header fields.
2. Generate the 185 x 123 small poster from the same sample artwork using the small Event 302 template.
3. Decode both generated XDS files with the open-source reader (build the reader if needed) and visually compare to the PNGs. Verify exact dimensions, colors, alpha and tiled data bounds.
4. Write a replacement-capable archive patcher for the already-added Event 749 entries, or recreate a clean archive from the original and use a safe patch flow.
5. Build a new archive from the clean original, inserting both custom Event 749 XDS files while preserving the original archive records/compression data. Avoid full archive repacking.
6. Run `lzxzip list` and `lzxzip test`, install only the verified test archive, and check both whether Xenia loads and whether Event 749 visibly displays the custom poster in the career UI.

## Handoff reminder

When continuing this project, read this note first. Treat every status explicitly labelled “not yet verified” as unknown. The byte-preserving archive method is game-load-tested; custom XDS generation is not yet game-tested.


## Committed helper scripts

The helper scripts are now stored in this repository as source files:

- [`tools/add_fm4_event749_posters.py`](tools/add_fm4_event749_posters.py) — first-time byte-preserving clone of the two Event 302 poster records to Event 749. It does not overwrite the input archive and refuses to add the Event 749 names if they already exist.
- [`tools/make_fm4_xds_from_png.py`](tools/make_fm4_xds_from_png.py) — experimental PNG-to-XDS generator for the checked EventBkgd poster template format (format 50, tiled, endian 2, ZYXW, one mip).

The XDS generator requires Pillow:

```powershell
python -m pip install pillow
```

These scripts have been committed to GitHub, but the archive patcher is tested in Xenia only for adding the original Event 302 artwork unchanged. The XDS generator has only passed the output-size check so far; its custom image has not yet been visually decoded or tested in the game.


## Related FH1 research: ZIP container quirks

The user found this useful technical reference for Forza Horizon 1:
- Overview: https://arcanite24.github.io/pinyon-shift/game-files/#files-zip-container-quirks
- Reference patcher: https://github.com/arcanite24/pinyon-shift/blob/dev/tools/patch-fh1-archive.py
- Native reader: https://github.com/arcanite24/pinyon-shift/blob/dev/src/ui/hostui/fh1_archive.cpp

Relevant findings from that work:
- ZIP central-directory extra field `0x1123` stores the absolute offset of the compressed payload, not merely the local-header offset.
- A rebuilt archive must update both the normal central-directory local-header offset (field at byte 42) and `0x1123`.
- Some FH1 archives omit local headers entirely; for those, the directory offset is itself the payload offset.
- The FH1 implementation detects a local-header signature and, if present, adds 30 bytes plus filename and extra-field lengths to reach the payload.
- CRC-32 is calculated over decompressed file contents.

How this applies to our FM4 case:
- Our direct inspection of the actual FM4 `Media\\UI\\Textures.zip` found 4,544 entries and every entry had a local ZIP header and a 4-byte `0x1123` extra field.
- All 4,544 original `0x1123` values pointed to the actual compressed payload offsets.
- The working byte-preserving Event 749 poster clone patcher preserves the entire original local-file area and central-directory records, and adds new records while relocating both local-header offset and central `0x1123` payload offset.
- Therefore the FH1 no-local-header branch is important general knowledge, but it was not present in the inspected FM4 archive.

The FH1 patcher also contains an experimental `xmem_stored()` function that wraps literal LZX blocks in XMem chunk framing (32 KiB chunks, method 21). Its comments explicitly say native UI delivery remains experimental. Do not assume FH1's custom XMem literal stream has been validated for FM4.

The CuteTenshii/lzx README independently documents the same 0x1123 field and says its compressor emits XMemCompress-style method-21 streams. It has been validated with its own decoder and libmspack, but its README says game loading of repacked archives has not been verified. Our observed Xenia freeze with `lzxzip update` therefore remains a warning; testing the newly generated payload in a staging ZIP and then transplanting it into the FM4 archive with minimal original-record changes is a separate experiment.

A new helper is committed:
- [`tools/append_fm4_lzx_entries.py`](tools/append_fm4_lzx_entries.py) — accepts a small staging ZIP created with `lzxzip create` and appends its method-21 `.xds` entries to the FM4 archive while preserving the original local-file area and original central-directory records byte-for-byte. It relocates the normal local-header offsets and central `0x1123` payload offsets. This script has **not yet been tested** with the generated poster artwork or in Xenia.

Potential next experiment:
1. Put only the two generated Event 749 XDS files in a staging folder with the correct archive-relative names:
   - `UI4/Logos/Posters/EventBkgd749.xds`
   - `UI4/Logos/Posters_SM/EventBkgd749.xds`
2. Use `lzxzip create` to make a tiny staging ZIP from that folder and `lzxzip test` it.
3. Use `append_fm4_lzx_entries.py` to append those staged entries to a clean/known-good FM4 archive that does not already contain Event 749 names. The user's current working game archive may already contain the two cloned Event 749 entries; if so, use their separately saved clean original or add replacement support before proceeding.
4. Validate the final archive with `lzxzip list` and `lzxzip test`.
5. Only then test in Xenia. Success is not guaranteed until the game loads and the event poster is visually confirmed.


A replacement-oriented helper is now committed:
- [`tools/replace_fm4_lzx_entries.py`](tools/replace_fm4_lzx_entries.py) — takes a working archive that already contains the Event 749 entries, plus a staging archive containing new method-21 XDS records. It appends the new local records, replaces only the matching central-directory records, updates the normal local-header offsets and `0x1123` absolute payload offsets, and preserves every other original central-directory record and the complete existing local-file area byte-for-byte. The old Event 749 local records remain as unreferenced/orphaned bytes; the central directory points to the new records. This is an experimental workflow and is not yet tested.


A PNG preview decoder is also committed:
- [`tools/preview_fm4_xds.py`](tools/preview_fm4_xds.py) — decodes the supported EventBkgd format 50 / tiled / endian 2 / ZYXW / single-mip layout back to PNG. Use it to inspect the generated full and small XDS visually before archive insertion. This decoder is purpose-built for the confirmed poster format, not a general XDS reader.


## Result: first custom-art Xenia test failed (2026-11-25)

The custom XDS files decoded back to PNG previews that the user inspected and said looked good. Both generated XDS files had the exact template file lengths:
- Full: 2,375,732 bytes (922 x 617)
- Small: 98,356 bytes (185 x 123)

They were compressed with `lzxzip create` into a staging archive. `lzxzip test` passed for the staging archive. `replace_fm4_lzx_entries.py` transplanted those streams into the known-working Event 749 archive; it preserved the original local-file area and unaffected central records. The resulting ZIP listed both Event 749 entries as method 21 and `lzxzip test` passed. However, after the user copied the custom archive into the game folder, Xenia froze.

Interpretation:
- CRC/integrity validation and successful preview decoding are not sufficient to prove compatibility with the game's native XMem/LZX decoder or its XDS loader.
- We do not yet know whether the freeze is caused by the newly generated LZX streams, XDS payload contents/header, the full poster, the small poster, or another central/local metadata difference.
- Restore the known-working archive `Textures_event749_rawcopy.zip` to the game before any more tests. That archive was previously confirmed to load in Xenia, with original Event 302 poster bytes cloned to Event 749.

Recommended isolation plan (after confirming the working archive launches again):
1. Keep the original EventBkgd302 poster entries unchanged; only replace one Event 749 entry at a time.
2. First test replacing only the *small* Event 749 entry, leaving the full-size one as the byte-identical Event 302 clone. Then restore the working archive and separately test only the *full* entry. This isolates which texture is triggering the freeze.
3. Before further game tests, validate generated XDS files using an independent XDS decoder (e.g. build/use CuteTenshii/xds), not only the custom preview decoder. Compare output pixels against the original PNG to verify channel order, swizzle and texture layout.
4. Investigate whether FM4 expects a different XMem compression framing/window or particular ZIP header fields than the lzxzip-generated stream. The fact that the same lzxzip stream passes its own test and is listed as method 21 does not establish game compatibility.
5. Do not overwrite the game archive with another custom archive until isolated tests pass.

The custom artwork generation path is currently **experimental and not game-verified**. The byte-preserving cloning path for unchanged Event 302 poster content remains game-load-tested.


### Registration check: original cloned poster is visible (2026-11-25)

After restoring `Textures_event749_rawcopy.zip`, the user confirmed Event 749 displays the original Ford Ka / Event 302 poster. This confirms the EventBkgd749 filenames are recognized by the career UI for this event; poster registration/filename lookup is not the reason the custom artwork was missing. The custom-XDS Xenia freeze must now be isolated to the custom texture payload, compression stream, or a per-entry interaction.

Next isolation step: replace only the small Event 749 XDS using a staging ZIP, leaving the full-size Event 749 entry as the byte-identical Event 302 clone. Base the replacement on `Textures_event749_rawcopy.zip`, which is already game-load-tested. If small-only succeeds, then test the full entry separately.


### Isolation result: small custom poster alone loads (2026-11-25)

The user created a staging ZIP containing only the custom small poster `UI4/Logos/Posters_SM/EventBkgd749.xds`, then replaced that entry in `Textures_event749_rawcopy.zip` using `replace_fm4_lzx_entries.py`.
- Output: `Textures_event749_small_test.zip`
- Replaced entries: 1
- Compressed size: 61,877 bytes; uncompressed: 98,356 bytes
- `lzxzip test`: 4,546 entries, 0 failures
- Xenia launched successfully with this archive.
- Event 749 still displayed the Ford Ka poster. The full-size Event 749 poster entry was deliberately left as the byte-identical Event 302/Ford Ka texture. The menu may simply be displaying the full-size poster; we have not proven whether the small poster was rendered in this UI.
- This establishes that the generated small XDS and its newly compressed method-21 stream do not by themselves cause the earlier startup freeze, at least in this test.

Next: test only the full-size custom XDS, basing the test archive on `Textures_event749_rawcopy.zip` (known-good duplicate-art archive), leaving the small poster as the original Ford Ka clone. Build a staging ZIP with just `UI4/Logos/Posters/EventBkgd749.xds`, replace that one entry, validate, then test Xenia. If it freezes, the full-size custom XDS or its stream is isolated as the cause. If it loads, investigate menu selection/rendering and whether the combination itself mattered.


### Full-size recompression header comparison (2026-11-25)

The user compared ZIP metadata for the working raw-copy archive and the freezing recompressed-full archive. For both Event302 (original) and Event749 (recompressed), the following values match: creator/version (made-by 0, version 10), needed version 10, flags 0, method 21, CRC32 0xAFDD3049, uncompressed size 2,375,732, and the central extra-field list contains only 0x1123. The local headers also match in version, flags, method, CRC, and size fields; neither local header has extra fields.

The only differences for recompressed Event749 are compressed size (313,873 vs. original Event302 314,226) and the expected 0x1123 absolute payload offset. Therefore the tested ZIP metadata layout does not explain the Xenia freeze. The payload is the key remaining difference.

Additional upstream notes checked:
- CuteTenshii/lzx README says its encoder emits XMemCompress-style 32 KiB chunk framing, but explicitly says game loading of repacked archives has not been verified. Source include/lzx/lzx.h specifies the default 128 KiB window (17 bits); src/encoder.cpp uses 32 KiB frames.
- D3FEKT/ForzaTechStudio docs say method 21 is XMemCompress LZX and that ForzaTech Studio can read/decompress method-21 entries through native xcompress64.dll, but does not currently write method 21. The documented typical Forza window is 0x20000 (128 KiB).
- Current ForzaTech Studio release page lists V0.9.2.2 and asset ForzaTech.Studio.zip. This is a potential native-XMem validation tool, distinct from the older Forza Studio 4.6 the user previously investigated.

Next best diagnostic: use ForzaTech Studio's ZIP viewer/extractor on both the original/working archive and the failed recompressed archive, extracting Event302 and Event749 and checking whether it can decode the recompressed stream. If native XMem extraction succeeds with matching CRC, the remaining failure is likely specific to Xenia or its stream-handling path; if it fails, the generated stream is not accepted by native XMem either.
