"""Embrilliance BX font binary parsing — per-letter connection-offset extraction."""

from __future__ import annotations

from pathlib import Path

from cli_anything_inkstitch.errors import UserError

# --- BX descender detection thresholds (empirical) -------------------------
#
# Embrilliance BX glyph records carry a per-glyph "extra baseline-to-bottom"
# value (extra_bf, in BX units). Observed clustering across the packs we've
# calibrated against (TSS-Homerun, NitkaBonitka, LD Signature, Stitchtopia,
# Chinoiserie):
#
#   x-height letters (a, c, e, …)   extra_bf =  0–2
#   ascenders (b, h, l, …)          extra_bf = 31–36
#   f and A–Z caps                  extra_bf = 46   ← boundary, NOT a descender
#   p, q                            extra_bf = 47
#   g, y                            extra_bf = 48
#   j                               extra_bf = 78   (above MAX: top feature is real)
#
# A glyph counts as a shiftable descender when MIN < extra_bf <= MAX.
# If a new vendor pack misaligns, dump its offsets (font import --bx-file ...
# --dry-run) and check whether its clusters match this table before adjusting.
_BX_DESCENDER_MIN_BF = 46
_BX_DESCENDER_MAX_BF = 70


def _locate_bzip2_payload(raw: bytes, bx_path: "Path") -> bytes:
    """Locate and decompress the bzip2 payload embedded in a BX font file.

    Handles two stream variants found in the wild:

    * **Full bzip2 stream** – starts with ``BZh[1-9]`` (intact header, e.g.
      SSP-style packs where the stream appears somewhere inside the binary).
    * **Stripped-header stream** – the 4-byte ``BZh9`` header is omitted and
      only the bzip2 block marker ``0x314159265359`` (leading digits of π) is
      present in the raw bytes.  Embrilliance TSS-Homerun files use this
      variant; the header is reconstructed by prepending ``BZh9`` before
      decompressing.

    Trailing bytes after the bzip2 end-of-stream marker (common when the
    stream is embedded mid-file) are tolerated via :class:`bz2.BZ2Decompressor`.

    Returns the raw decompressed bytes.  Raises :class:`UserError` if no
    usable stream can be found.
    """
    import bz2

    if not raw:
        raise UserError(f"BX file is empty: {bx_path}")

    def _try_decompress(data: bytes) -> bytes | None:
        """Decompress *data*, tolerating trailing garbage after stream end."""
        # First try the straightforward path (works when data is a complete file).
        try:
            result = bz2.decompress(data)
            if len(result) > 30:   # must produce something non-trivial
                return result
        except OSError:
            pass
        # BZ2Decompressor stops cleanly at end-of-stream and ignores trailing bytes.
        try:
            dec = bz2.BZ2Decompressor()
            result = dec.decompress(data)
            if len(result) > 30:
                return result
        except OSError:
            pass
        return None

    # ---- Strategy A: intact BZh stream header ----------------------------
    # Scan for "BZh" followed by a valid block-size digit ('1'–'9').
    _BZH = b"BZh"
    pos = 0
    while True:
        idx = raw.find(_BZH, pos)
        if idx == -1:
            break
        if idx + 3 < len(raw) and 0x31 <= raw[idx + 3] <= 0x39:
            result = _try_decompress(raw[idx:])
            if result is not None:
                return result
        pos = idx + 1

    # ---- Strategy B: stripped-header — prepend BZh to block magic --------
    # BZip2 block-header magic = 0x314159265359 (first 48 bits of π).
    # Embrilliance omits the 4-byte stream header; reconstruct it before
    # decompressing.  Try block-size digits 9, 1, 5 in that order.
    _BLOCK_MAGIC = bytes([0x31, 0x41, 0x59, 0x26, 0x53, 0x59])
    pos = 0
    last_exc: Exception | None = None
    while True:
        idx = raw.find(_BLOCK_MAGIC, pos)
        if idx == -1:
            break
        for hdr in (b"BZh9", b"BZh1", b"BZh5"):
            try:
                result = _try_decompress(hdr + raw[idx:])
                if result is not None:
                    return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        pos = idx + 1

    raise UserError(
        f"No decompressible bzip2 stream found in BX file {bx_path}"
        + (f": {last_exc}" if last_exc else "")
    )


def _parse_bx_glyphs(bf_data: bytes) -> dict[str, float]:
    """Parse per-glyph y_min values from decompressed BX font data.

    Every Embrilliance BX file (regardless of vendor) decompresses to a binary
    stream containing two types of records per glyph:

    **IDMDTL block** — geometry record::

        b"IDMDTL"  {n_entries: u32}  [{pre: u32}  {tag: u16}  {vlen: u32}  {data: vlen}] ...

        The entry with ``pre == 5`` and ``vlen == 24`` encodes the glyph's
        axis-aligned bounding box as six LE float32 values::

            [x_min, y_min, 0, x_max, y_max, 0]

        ``y_min`` is the connection-line Y in BF units (1 BF = 0.1 mm),
        centred at 0 (origin = glyph baseline entry/exit point).  Typical:
        x-height letters ≈ −83, ascenders ≈ −117, descenders ≈ −130.

        The *tag* number varies by vendor (0x000c, 0x0011, 0x0012, 0x0013, …);
        we match on ``pre=5, vlen=24`` which is consistent across all tested
        packs (TSS-Homerun, NitkaBonitka, LD Signature, Stitchtopia, Chinoiserie).

    **Character record** — attribute record immediately following the filename::

        {filename}  b"\\t\\x00"  {pre=8: u32}  {tag: u16}  {vlen=2|3: u32}  {char_bytes}

        The first attribute always has ``pre == 8`` and ``vlen ∈ {2, 3}``.
        ``char_bytes[0]`` is the ASCII codepoint of the glyph.  The tag number
        varies by vendor; we match structurally.

    **Matching strategy**: after building an ordered list of all IDMDTL bbox
    positions, each character record is paired with the nearest preceding IDMDTL
    bbox via binary search.  This is order-stable even when glyph records
    contain multiple IDMDTL blocks (e.g. a ``treeitem``/``original`` metadata
    block followed by the actual geometry block).
    """
    import bisect
    import struct

    # ---- Step 1: collect all IDMDTL bbox offsets in file order ----
    # Each entry is (file_offset_of_IDMDTL_marker, y_min_float).
    idmdtl_bboxes: list[tuple[int, float]] = []
    pos = 0
    while True:
        idx = bf_data.find(b"IDMDTL", pos)
        if idx == -1:
            break
        after = idx + 6
        if after + 4 > len(bf_data):
            pos = idx + 1
            continue
        n_entries = struct.unpack_from("<I", bf_data, after)[0]
        if n_entries == 0 or n_entries > 200:   # sanity guard
            pos = idx + 1
            continue
        after += 4
        for _ in range(n_entries):
            if after + 10 > len(bf_data):
                break
            pre, tag, vlen = struct.unpack_from("<IHI", bf_data, after)
            if vlen > 100_000:
                break
            # Bbox attribute: pre=5, vlen=24 (six LE float32).
            if pre == 5 and vlen == 24 and after + 34 <= len(bf_data):
                floats = struct.unpack_from("<6f", bf_data, after + 10)
                idmdtl_bboxes.append((idx, float(floats[1])))  # y_min
            after += 10 + vlen
        pos = idx + 1

    if not idmdtl_bboxes:
        return {}

    bbox_offsets = [t[0] for t in idmdtl_bboxes]  # sorted ascending (file order)

    # ---- Step 2: find character records and pair with preceding bbox ----
    # Character record separator is b'\t\x00' (TAB + NUL) after the filename.
    # The immediately following attribute has pre=8, vlen∈{2,3}, data[0]=ASCII char.
    offsets: dict[str, float] = {}
    sep = b"\t\x00"
    pos = 0
    while True:
        idx = bf_data.find(sep, pos)
        if idx == -1:
            break
        pos = idx + 2   # advance past separator for next iteration

        attr_pos = idx + 2
        if attr_pos + 10 > len(bf_data):
            continue
        pre, _tag, vlen = struct.unpack_from("<IHI", bf_data, attr_pos)
        if pre != 8 or vlen not in (2, 3):
            continue
        ch_bytes = bf_data[attr_pos + 10: attr_pos + 10 + vlen]
        if not ch_bytes or not (0x20 <= ch_bytes[0] <= 0x7E):
            continue   # not a printable ASCII glyph

        ch = chr(ch_bytes[0])

        # Binary-search for the nearest IDMDTL block *before* this record.
        ins = bisect.bisect_right(bbox_offsets, idx)
        if ins == 0:
            continue   # no IDMDTL block precedes this record
        _, y_min = idmdtl_bboxes[ins - 1]

        if ch not in offsets:   # first occurrence wins
            offsets[ch] = y_min

    return offsets


def _extract_bx_connection_offsets(bx_path: str | Path) -> dict[str, float]:
    """Return per-character y_min values (in BF units, 1 BF = 0.1 mm) from a BX font file.

    Locates and decompresses the embedded bzip2 stream via
    :func:`_locate_bzip2_payload`, then delegates to :func:`_parse_bx_glyphs`
    for structure-based glyph extraction.

    Tested against five real-world vendors (TSS-Homerun, NitkaBonitka Bubble,
    LD Signature, Stitchtopia, Chinoiserie) with no vendor-specific code paths.

    Returns a mapping ``{'a': -83.0, 'b': -117.0, ...}`` for every glyph
    whose bbox could be found.  Missing glyphs are simply absent so callers
    can fall back to the global ``--baseline-from-bottom-mm``.
    """
    bx_path = Path(bx_path)
    raw = bx_path.read_bytes()
    bf_data = _locate_bzip2_payload(raw, bx_path)
    return _parse_bx_glyphs(bf_data)
