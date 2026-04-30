"""Bounding boxes, positions, and overlaps for SVG elements.

Pure-Python (no shapely) — bbox is approximate for curves (uses control points,
which gives a safe over-estimate). Arcs are approximated by their endpoints.
SVG transforms are not yet handled — designs with `transform="..."` will report
bboxes in untransformed user-coordinate space.

Inkstitch's PIXELS_PER_MM convention is used: 96/25.4 user units per mm.
"""

from __future__ import annotations

import re

from lxml import etree

PIXELS_PER_MM = 96.0 / 25.4

Bbox = tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax)


def px_to_mm(px: float) -> float:
    return px / PIXELS_PER_MM


_TOKEN_RE = re.compile(
    r'([MmLlHhVvCcSsQqTtAaZz])|(-?\d*\.?\d+(?:[eE][+-]?\d+)?)'
)

_PARAM_COUNTS = {
    'M': 2, 'L': 2, 'H': 1, 'V': 1,
    'C': 6, 'S': 4, 'Q': 4, 'T': 2,
    'A': 7, 'Z': 0,
}


def _tokenize_d(d: str):
    for m in _TOKEN_RE.finditer(d):
        if m.group(1):
            yield ('cmd', m.group(1))
        else:
            yield ('num', float(m.group(2)))


def path_bbox(d: str) -> Bbox | None:
    """Compute approximate bbox of a path's `d` attribute.

    Uses endpoints + Bezier control points (safe over-estimate). Ignores arc
    curvature (uses endpoints only). Returns None for an empty `d`.
    """
    if not d or not d.strip():
        return None
    tokens = list(_tokenize_d(d))
    if not tokens:
        return None

    cur_x = cur_y = 0.0
    start_x = start_y = 0.0
    xs: list[float] = []
    ys: list[float] = []

    i = 0
    cmd: str | None = None
    while i < len(tokens):
        kind, val = tokens[i]
        if kind == 'cmd':
            cmd = val
            i += 1
            if cmd in 'Zz':
                cur_x, cur_y = start_x, start_y
                continue
        # Parse the right number of params for cmd (and re-parse if implicit
        # repeat — same cmd letter omitted between coord groups).
        if cmd is None:
            i += 1
            continue
        upper = cmd.upper()
        n = _PARAM_COUNTS.get(upper, 0)
        if i + n > len(tokens):
            break
        params = []
        for j in range(n):
            ptok = tokens[i + j]
            if ptok[0] != 'num':
                # bail on malformed
                return _finalize(xs, ys)
            params.append(ptok[1])
        i += n
        relative = cmd.islower()

        if upper == 'M':
            x, y = params
            if relative and xs:  # first M is implicitly absolute
                x += cur_x
                y += cur_y
            cur_x, cur_y = x, y
            start_x, start_y = x, y
            xs.append(x); ys.append(y)
            # Subsequent coord pairs after M are implicit lineto
            cmd = 'l' if relative else 'L'
        elif upper == 'L':
            x, y = params
            if relative:
                x += cur_x; y += cur_y
            cur_x, cur_y = x, y
            xs.append(x); ys.append(y)
        elif upper == 'H':
            x = params[0]
            if relative:
                x += cur_x
            cur_x = x
            xs.append(x); ys.append(cur_y)
        elif upper == 'V':
            y = params[0]
            if relative:
                y += cur_y
            cur_y = y
            xs.append(cur_x); ys.append(y)
        elif upper == 'C':
            x1, y1, x2, y2, x, y = params
            if relative:
                x1 += cur_x; y1 += cur_y
                x2 += cur_x; y2 += cur_y
                x += cur_x; y += cur_y
            xs.extend([x1, x2, x]); ys.extend([y1, y2, y])
            cur_x, cur_y = x, y
        elif upper == 'S':
            x2, y2, x, y = params
            if relative:
                x2 += cur_x; y2 += cur_y
                x += cur_x; y += cur_y
            xs.extend([x2, x]); ys.extend([y2, y])
            cur_x, cur_y = x, y
        elif upper == 'Q':
            x1, y1, x, y = params
            if relative:
                x1 += cur_x; y1 += cur_y
                x += cur_x; y += cur_y
            xs.extend([x1, x]); ys.extend([y1, y])
            cur_x, cur_y = x, y
        elif upper == 'T':
            x, y = params
            if relative:
                x += cur_x; y += cur_y
            xs.append(x); ys.append(y)
            cur_x, cur_y = x, y
        elif upper == 'A':
            # arc: rx ry x-axis-rotation large-arc sweep x y
            x, y = params[5], params[6]
            if relative:
                x += cur_x; y += cur_y
            # Conservative: just include endpoint, ignore arc bulge.
            xs.append(x); ys.append(y)
            cur_x, cur_y = x, y

    return _finalize(xs, ys)


def _finalize(xs, ys) -> Bbox | None:
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _f(elem, attr, default=0.0) -> float:
    v = elem.get(attr)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        # strip trailing units like "227mm"
        m = re.match(r'^\s*(-?[\d.]+)', v)
        return float(m.group(1)) if m else default


def element_bbox(elem) -> Bbox | None:
    """Bbox of one SVG element in user-coordinate units.

    Handles: path, rect, circle, ellipse, line, polygon, polyline.
    Returns None for unsupported tags or unparseable geometry.
    """
    local = etree.QName(elem.tag).localname
    if local == 'path':
        return path_bbox(elem.get('d', ''))
    if local == 'rect':
        x = _f(elem, 'x'); y = _f(elem, 'y')
        w = _f(elem, 'width'); h = _f(elem, 'height')
        return (x, y, x + w, y + h)
    if local == 'circle':
        cx = _f(elem, 'cx'); cy = _f(elem, 'cy'); r = _f(elem, 'r')
        return (cx - r, cy - r, cx + r, cy + r)
    if local == 'ellipse':
        cx = _f(elem, 'cx'); cy = _f(elem, 'cy')
        rx = _f(elem, 'rx'); ry = _f(elem, 'ry')
        return (cx - rx, cy - ry, cx + rx, cy + ry)
    if local == 'line':
        x1 = _f(elem, 'x1'); y1 = _f(elem, 'y1')
        x2 = _f(elem, 'x2'); y2 = _f(elem, 'y2')
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if local in ('polygon', 'polyline'):
        pts = elem.get('points', '')
        nums = [float(n) for n in re.findall(r'-?[\d.]+', pts)]
        if len(nums) < 4:
            return None
        xs = nums[0::2]; ys = nums[1::2]
        return (min(xs), min(ys), max(xs), max(ys))
    return None


def design_bbox_from_root(root) -> Bbox:
    """Resolve the design's coordinate-space bbox.

    Prefer `viewBox` (the standard SVG way to declare the user-coord space).
    Fall back to width/height attrs if viewBox is missing.
    """
    vb = root.get('viewBox')
    if vb:
        nums = [float(n) for n in re.findall(r'-?[\d.]+', vb)]
        if len(nums) == 4:
            x, y, w, h = nums
            return (x, y, x + w, y + h)
    w = _f(root, 'width', 0.0)
    h = _f(root, 'height', 0.0)
    return (0.0, 0.0, w, h)


_POSITION_LABELS = (
    ('top-left', 'top-center', 'top-right'),
    ('middle-left', 'center', 'middle-right'),
    ('bottom-left', 'bottom-center', 'bottom-right'),
)


def position_descriptor(bbox: Bbox, design_bbox: Bbox) -> str:
    """3x3 grid descriptor based on the bbox center.

    Returns one of: top-left, top-center, top-right,
    middle-left, center, middle-right,
    bottom-left, bottom-center, bottom-right.
    """
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    dx0, dy0, dx1, dy1 = design_bbox
    dw = dx1 - dx0
    dh = dy1 - dy0
    if dw <= 0 or dh <= 0:
        return 'unknown'
    rel_x = (cx - dx0) / dw
    rel_y = (cy - dy0) / dh
    col = min(max(int(rel_x * 3), 0), 2)
    row = min(max(int(rel_y * 3), 0), 2)
    return _POSITION_LABELS[row][col]


def bbox_overlap(a: Bbox, b: Bbox) -> str | None:
    """Classify how two bboxes relate.

    Returns: "contains", "contained_by", "intersects", or None (no overlap).
    """
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    if ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0:
        return None
    # Strict containment (with small float tolerance).
    eps = 1e-6
    if ax0 <= bx0 + eps and ay0 <= by0 + eps and ax1 + eps >= bx1 and ay1 + eps >= by1:
        return 'contains'
    if bx0 <= ax0 + eps and by0 <= ay0 + eps and bx1 + eps >= ax1 and by1 + eps >= ay1:
        return 'contained_by'
    return 'intersects'


def bbox_area(b: Bbox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def aspect_ratio(b: Bbox) -> float | None:
    """Width / height. Returns None for zero-height bbox."""
    w = b[2] - b[0]
    h = b[3] - b[1]
    if h <= 0:
        return None
    return w / h


def _fmt(n: float) -> str:
    """Format a coordinate compactly: drop trailing .0, keep precision."""
    if n == int(n):
        return str(int(n))
    return f"{n:g}"


def open_closed_subpaths(d: str) -> str:
    """Replace `Z`/`z` close-path commands with explicit lineto-to-start.

    Inkstitch's `ClosedPathWarning` fires on `any(letter == 'Z')` anywhere
    in a satin column path's `d` attribute (see
    inkstitch/lib/elements/satin_column.py). Just removing Z would lose the
    closing segment of geometry; replacing it with an explicit
    `L <start_x> <start_y>` preserves the rail's full traversal while
    eliminating the literal Z that triggers the warning.

    The output uses absolute coordinates throughout and parses back to
    geometrically equivalent paths (subject to the bezier control-point
    over-estimate documented on `path_bbox`).

    Returns the input unchanged if it contains no Z/z.
    """
    if not d or 'z' not in d.lower():
        return d

    tokens = list(_tokenize_d(d))
    if not tokens:
        return d

    out: list[str] = []
    cur_x = cur_y = 0.0
    start_x = start_y = 0.0
    cmd: str | None = None
    i = 0

    while i < len(tokens):
        kind, val = tokens[i]
        if kind == 'cmd':
            cmd = val
            i += 1
            if cmd in 'Zz':
                # Substitute explicit lineto back to subpath start.
                out.append(f"L {_fmt(start_x)} {_fmt(start_y)}")
                cur_x, cur_y = start_x, start_y
                continue
        if cmd is None:
            i += 1
            continue
        upper = cmd.upper()
        n = _PARAM_COUNTS.get(upper, 0)
        if i + n > len(tokens):
            break
        params = []
        for j in range(n):
            ptok = tokens[i + j]
            if ptok[0] != 'num':
                # Malformed: bail and return original to avoid worse damage
                return d
            params.append(ptok[1])
        i += n
        relative = cmd.islower()

        if upper == 'M':
            x, y = params
            if relative and out:  # first M is implicitly absolute
                x += cur_x
                y += cur_y
            cur_x, cur_y = x, y
            start_x, start_y = x, y
            out.append(f"M {_fmt(x)} {_fmt(y)}")
            cmd = 'L'  # subsequent coord pairs become implicit lineto
        elif upper == 'L':
            x, y = params
            if relative:
                x += cur_x; y += cur_y
            cur_x, cur_y = x, y
            out.append(f"L {_fmt(x)} {_fmt(y)}")
        elif upper == 'H':
            x = params[0]
            if relative:
                x += cur_x
            cur_x = x
            out.append(f"H {_fmt(x)}")
        elif upper == 'V':
            y = params[0]
            if relative:
                y += cur_y
            cur_y = y
            out.append(f"V {_fmt(y)}")
        elif upper == 'C':
            x1, y1, x2, y2, x, y = params
            if relative:
                x1 += cur_x; y1 += cur_y
                x2 += cur_x; y2 += cur_y
                x += cur_x; y += cur_y
            cur_x, cur_y = x, y
            out.append(f"C {_fmt(x1)} {_fmt(y1)} {_fmt(x2)} {_fmt(y2)} {_fmt(x)} {_fmt(y)}")
        elif upper == 'S':
            x2, y2, x, y = params
            if relative:
                x2 += cur_x; y2 += cur_y
                x += cur_x; y += cur_y
            cur_x, cur_y = x, y
            out.append(f"S {_fmt(x2)} {_fmt(y2)} {_fmt(x)} {_fmt(y)}")
        elif upper == 'Q':
            x1, y1, x, y = params
            if relative:
                x1 += cur_x; y1 += cur_y
                x += cur_x; y += cur_y
            cur_x, cur_y = x, y
            out.append(f"Q {_fmt(x1)} {_fmt(y1)} {_fmt(x)} {_fmt(y)}")
        elif upper == 'T':
            x, y = params
            if relative:
                x += cur_x; y += cur_y
            cur_x, cur_y = x, y
            out.append(f"T {_fmt(x)} {_fmt(y)}")
        elif upper == 'A':
            rx, ry, rot, large, sweep, x, y = params
            if relative:
                x += cur_x; y += cur_y
            cur_x, cur_y = x, y
            out.append(f"A {_fmt(rx)} {_fmt(ry)} {_fmt(rot)} "
                        f"{int(large)} {int(sweep)} {_fmt(x)} {_fmt(y)}")

    return ' '.join(out)
