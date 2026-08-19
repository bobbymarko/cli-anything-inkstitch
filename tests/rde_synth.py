"""Build synthetic Chroma .rde files for tests.

The real .rde corpus is licensed commercial artwork and cannot be committed
(.gitignore), so every corpus-backed test skips in CI. These builders make the
same code paths reachable from nothing but source: the format is documented in
tools/rde_decode.py, and its obfuscation is a symmetric XOR, so writing a file
is the decoder run backwards.

What a synthetic file is for is the STRUCTURE the corpus can't guard in CI --
a counter nested in a letter, an object that carries a name, a short move that
must not trim. It is not artwork and proves nothing about how a real design
converts; the corpus tests still do that locally.
"""

from __future__ import annotations

import struct

import tools_path  # noqa: F401  (puts tools/ on sys.path)
from rde_decode import keystream

FILL_TAG = 0x66
# tools/rde_to_inkstitch.py PARAM_MARKER / FILL_FLAG_OFFSET / FILL_FLAG_VALUE:
# byte +56 from the marker is Chroma's fill flag, and 2 means "filled".
PARAM_MARKER = b"\x6e\x02\x00\x00"
FILL_FLAG_OFFSET = 56


def _wstr(text):
    """Length-prefixed UTF-16, the encoding read_wstr reads."""
    return struct.pack("<I", len(text)) + text.encode("utf-16-le")


def _node(anchor, hin=None, hout=None):
    """One outline node: two flag bytes then anchor/handle-in/handle-out.

    Bit 0x80 of the second flag byte means "anchor only", which is how a
    corner without handles is stored (tools/rde_decode.py read_outlines).
    """
    if hin is None and hout is None:
        return b"\x00\x80" + struct.pack("<ff", *anchor)
    return b"\x00\x00" + struct.pack("<ffffff", *anchor, *hin, *hout)


def _contour(points):
    """A closed contour of corner nodes."""
    return struct.pack("<I", len(points)) + b"".join(_node(p) for p in points)


def rect(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def fill_rows(x, y, w, h, spacing=5.0):
    """A serpentine fill of `spacing`-unit rows covering a rectangle."""
    pts, row, left = [], y, True
    while row <= y + h:
        span = [(x, row), (x + w, row)]
        if not left:
            span.reverse()
        pts += span
        row += spacing
        left = not left
    return pts


def ring_fill(x, y, w, h, hole, spacing=5.0):
    """Fill a rectangle with a rectangular counter, stitching AROUND the hole.

    Routing matters to the fixture: a fill that crossed the counter -- row by
    row, or on the way from one band to the next -- would lay thread inside it,
    and thread inside is exactly how _is_hole tells a counter from a shape that
    is genuinely stitched. So the four bands around the hole run in ring order,
    each one starting where the last ended, so every connecting move hugs the
    outer edge instead of cutting across the middle.
    """
    hx0, hy0, hx1, hy1 = hole
    left = fill_rows(x, y, hx0 - x, h, spacing)                    # bottom to top
    above = fill_rows(hx0, hy1, hx1 - hx0, y + h - hy1, spacing)
    right = fill_rows(hx1, y, x + w - hx1, h, spacing)[::-1]       # top to bottom
    below = fill_rows(hx0, y, hx1 - hx0, hy0 - y, spacing)[::-1]
    return left + above + right + below


def object_payload(color_index, stitches, contours, name="", tag=FILL_TAG,
                   is_fill=True):
    """One decrypted object record: stitches, name, outline, parameter block."""
    out = bytearray()
    out += struct.pack("<HI", color_index, len(stitches))
    for x, y in stitches:
        out += struct.pack("<ffB", float(x), float(y), 0)
    out += _wstr(name)
    out += b"\x00\x00\x00\x00"
    # Contour block signature: u32 2, pad, u32 1, pad, tag, u32 contour count.
    out += struct.pack("<I", 2) + b"\x00" + struct.pack("<I", 1) + b"\x00"
    out += bytes([tag]) + struct.pack("<I", len(contours))
    for c in contours:
        out += _contour(c)
    params = bytearray(PARAM_MARKER + bytes(FILL_FLAG_OFFSET + 4))
    params[FILL_FLAG_OFFSET] = 2 if is_fill else 0
    out += params
    return bytes(out)


def thread_section(colors):
    """The plaintext tag-0x02 thread list (tools/rde_decode.py read_colors)."""
    out = bytearray(struct.pack("<I", len(colors)))
    for rgb, code, name in colors:
        out += bytes(rgb) + b"\x00"
        out += _wstr(code) + _wstr(name) + _wstr("Synthetic")
        out += b"\x00\x00\x00\x00"
    return bytes(out)


def build(path, colors, objects):
    """Write a .rde: 0xA0 of header, then tag/length/payload sections."""
    data = bytearray(b"\x00" * 0xA0)

    def section(tag, payload):
        data.extend(bytes([tag]) + struct.pack("<I", len(payload)) + payload)

    section(0x02, thread_section(colors))
    for payload in objects:
        # The cipher is an XOR keystream, so encrypting is the same operation.
        ks = keystream(len(payload))
        section(FILL_TAG, bytes(a ^ b for a, b in zip(payload, ks)))
    open(path, "wb").write(bytes(data))
    return path
