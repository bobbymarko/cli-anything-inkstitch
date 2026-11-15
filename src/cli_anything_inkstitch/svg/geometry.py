"""Bounding boxes, positions, and overlaps for SVG elements.

Pure-Python (no shapely) — bbox is approximate for curves (uses control points,
which gives a safe over-estimate). Arcs are approximated by their endpoints.
SVG transforms (translate/scale/rotate/matrix/skew, composed through ancestors)
are applied by element_bbox_in_root(); for rotation/skew the result is the bbox
of the transformed corners — a safe over-estimate, flagged `bbox_approx`.

Inkstitch's PIXELS_PER_MM convention is used: 96/25.4 user units per mm.
"""

from __future__ import annotations

import math
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


# --- SVG transforms ---------------------------------------------------------
#
# Matrices use the SVG (a, b, c, d, e, f) layout:
#   | a c e |
#   | b d f |
#   | 0 0 1 |

Matrix = tuple[float, float, float, float, float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

_TRANSFORM_RE = re.compile(r'(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)')
_NUM_RE = re.compile(r'[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?')


def matrix_multiply(m1: Matrix, m2: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def apply_matrix(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def parse_transform(s: str | None) -> Matrix:
    """Parse an SVG `transform` attribute into one composed affine matrix.

    Unknown function names are ignored; per the SVG spec, a list like
    "translate(10) rotate(45)" composes left-to-right (rightmost applies to
    local coordinates first).
    """
    if not s:
        return IDENTITY
    m = IDENTITY
    for name, argstr in _TRANSFORM_RE.findall(s):
        args = [float(v) for v in _NUM_RE.findall(argstr)]
        if name == 'matrix' and len(args) == 6:
            t: Matrix = tuple(args)  # type: ignore[assignment]
        elif name == 'translate' and args:
            t = (1.0, 0.0, 0.0, 1.0, args[0], args[1] if len(args) > 1 else 0.0)
        elif name == 'scale' and args:
            sx = args[0]
            sy = args[1] if len(args) > 1 else sx
            t = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == 'rotate' and args:
            rad = math.radians(args[0])
            cos_a, sin_a = math.cos(rad), math.sin(rad)
            t = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(args) == 3:
                cx, cy = args[1], args[2]
                t = matrix_multiply(
                    matrix_multiply((1.0, 0.0, 0.0, 1.0, cx, cy), t),
                    (1.0, 0.0, 0.0, 1.0, -cx, -cy),
                )
        elif name == 'skewX' and args:
            t = (1.0, 0.0, math.tan(math.radians(args[0])), 1.0, 0.0, 0.0)
        elif name == 'skewY' and args:
            t = (1.0, math.tan(math.radians(args[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        m = matrix_multiply(m, t)
    return m


def ctm_for(elem) -> Matrix:
    """Composed transform from the SVG root down to elem (inclusive)."""
    chain = []
    node = elem
    while node is not None and isinstance(node.tag, str):
        t = node.get('transform')
        if t:
            chain.append(parse_transform(t))
        node = node.getparent()
    m = IDENTITY
    for t in reversed(chain):
        m = matrix_multiply(m, t)
    return m


def _is_axis_aligned(m: Matrix, eps: float = 1e-9) -> bool:
    return abs(m[1]) < eps and abs(m[2]) < eps


def transformed_bbox(bb: Bbox, m: Matrix) -> Bbox:
    corners = [(bb[0], bb[1]), (bb[2], bb[1]), (bb[0], bb[3]), (bb[2], bb[3])]
    pts = [apply_matrix(m, x, y) for x, y in corners]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def element_bbox_in_root(elem) -> tuple[Bbox | None, dict]:
    """Bbox in root (design) coordinates plus geometry metadata.

    Returns (bbox, meta) where meta may carry:
      has_transform: a transform applies to this element (own or ancestor)
      bbox_approx:   transform includes rotation/skew, so the bbox is the
                     (safe, possibly larger) bound of the rotated corners
    """
    bb = element_bbox(elem)
    if bb is None:
        return None, {}
    m = ctm_for(elem)
    if m == IDENTITY:
        return bb, {}
    meta: dict = {"has_transform": True}
    if not _is_axis_aligned(m):
        meta["bbox_approx"] = True
    return transformed_bbox(bb, m), meta


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


_D_TOKEN = None  # populated lazily in transform_d


def arc_to_cubics(x0: float, y0: float, rx: float, ry: float, phi_deg: float,
                  large: bool, sweep: bool, x: float, y: float):
    """SVG elliptical arc → list of cubic segments ((x1,y1),(x2,y2),(x,y)).

    Endpoint → center parameterization per SVG 1.1 F.6.5/F.6.6 (same math as
    artifact/gate.py flatten_path), then the standard 4/3·tan(θ/4) cubic
    approximation per ≤90° piece (radial error < 0.03% of the radius).
    Cubics survive any affine transform exactly, which an arc's rx/ry/rotation
    parameters do not under shear — that is why transform_d emits these.
    Degenerate arcs (zero radius or coincident ends) return a straight cubic.
    """
    if rx < 1e-9 or ry < 1e-9 or (abs(x - x0) < 1e-12 and abs(y - y0) < 1e-12):
        return [((x0, y0), (x, y), (x, y))]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg)
    cosp, sinp = math.cos(phi), math.sin(phi)
    x1p = (cosp * (x0 - x) + sinp * (y0 - y)) / 2
    y1p = (-sinp * (x0 - x) + cosp * (y0 - y)) / 2
    lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if lam > 1:
        k = math.sqrt(lam)
        rx *= k; ry *= k
    num_ = (rx * ry) ** 2 - (rx * y1p) ** 2 - (ry * x1p) ** 2
    den = (rx * y1p) ** 2 + (ry * x1p) ** 2
    coef = math.sqrt(max(num_, 0.0) / den) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    ccx = cosp * cxp - sinp * cyp + (x0 + x) / 2
    ccy = sinp * cxp + cosp * cyp + (y0 + y) / 2

    def ang(ux, uy, vx, vy):
        dd = math.hypot(ux, uy) * math.hypot(vx, vy) or 1.0
        a = math.acos(max(-1.0, min(1.0, (ux * vx + uy * vy) / dd)))
        return a if ux * vy - uy * vx >= 0 else -a

    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry,
              (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    n = max(1, math.ceil(abs(dth) / (math.pi / 2) - 1e-9))
    step = dth / n
    alpha = 4 / 3 * math.tan(step / 4)

    def on(t):
        return (ccx + rx * math.cos(t) * cosp - ry * math.sin(t) * sinp,
                ccy + rx * math.cos(t) * sinp + ry * math.sin(t) * cosp)

    def deriv(t):
        return (-rx * math.sin(t) * cosp - ry * math.cos(t) * sinp,
                -rx * math.sin(t) * sinp + ry * math.cos(t) * cosp)

    out = []
    t = th1
    px, py = x0, y0
    for k in range(n):
        t2 = th1 + step * (k + 1)
        d1 = deriv(t); d2 = deriv(t2)
        ex, ey = (x, y) if k == n - 1 else on(t2)
        out.append(((px + alpha * d1[0], py + alpha * d1[1]),
                    (ex - alpha * d2[0], ey - alpha * d2[1]),
                    (ex, ey)))
        px, py, t = ex, ey, t2
    return out


def transform_d(d: str, matrix) -> str:
    """Apply an affine matrix to every coordinate of a path's d.

    Parses to absolute M/L/C segments and matrix-applies each point — used
    to FLATTEN an element's transform into its geometry so the whole
    pipeline can keep working in root coordinates. Engine tools (e.g.
    convert_to_satin) emit elements with compensating transforms.

    Curves stay curves: H/V normalize to L; S reflects the previous cubic
    control and Q/T degree-elevate to C (both exact); A becomes cubics via
    arc_to_cubics. Collapsing S/Q/T/A to their endpoints (the old behavior)
    turned Illustrator exports — which lean on ``s`` — into straight-edged
    polygons the moment a transform was baked.
    """
    import re as _re
    global _D_TOKEN
    if _D_TOKEN is None:
        _D_TOKEN = _re.compile(
            r"[MmLlCcZzHhVvSsQqTtAa]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
    tokens = _D_TOKEN.findall(d or "")
    out: list[str] = []
    i = 0
    cx = cy = sx = sy = 0.0
    cmd = None
    last_c: tuple[float, float] | None = None   # trailing cubic control (for S)
    last_q: tuple[float, float] | None = None   # trailing quadratic control (for T)

    def num() -> float:
        nonlocal i
        v = float(tokens[i]); i += 1
        return v

    def pt(x: float, y: float) -> str:
        tx, ty = apply_matrix(matrix, x, y)
        return f"{round(tx, 3)},{round(ty, 3)}"

    def cubic(x1, y1, x2, y2, x, y) -> None:
        out.append("C" + " ".join((pt(x1, y1), pt(x2, y2), pt(x, y))))

    while i < len(tokens):
        if _re.match(r"[A-Za-z]", tokens[i]):
            cmd = tokens[i]; i += 1
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x = num() + (cx if rel else 0); y = num() + (cy if rel else 0)
            out.append("M" + pt(x, y)); cx, cy, sx, sy = x, y, x, y
            cmd = "l" if rel else "L"
            last_c = last_q = None
        elif c == "L":
            x = num() + (cx if rel else 0); y = num() + (cy if rel else 0)
            out.append("L" + pt(x, y)); cx, cy = x, y
            last_c = last_q = None
        elif c == "H":
            x = num() + (cx if rel else 0)
            out.append("L" + pt(x, cy)); cx = x
            last_c = last_q = None
        elif c == "V":
            y = num() + (cy if rel else 0)
            out.append("L" + pt(cx, y)); cy = y
            last_c = last_q = None
        elif c == "C":
            x1 = num() + (cx if rel else 0); y1 = num() + (cy if rel else 0)
            x2 = num() + (cx if rel else 0); y2 = num() + (cy if rel else 0)
            x = num() + (cx if rel else 0); y = num() + (cy if rel else 0)
            cubic(x1, y1, x2, y2, x, y)
            cx, cy = x, y
            last_c = (x2, y2); last_q = None
        elif c == "S":
            x2 = num() + (cx if rel else 0); y2 = num() + (cy if rel else 0)
            x = num() + (cx if rel else 0); y = num() + (cy if rel else 0)
            x1 = 2 * cx - last_c[0] if last_c else cx
            y1 = 2 * cy - last_c[1] if last_c else cy
            cubic(x1, y1, x2, y2, x, y)
            cx, cy = x, y
            last_c = (x2, y2); last_q = None
        elif c in ("Q", "T"):
            if c == "Q":
                qx = num() + (cx if rel else 0); qy = num() + (cy if rel else 0)
            else:
                qx = 2 * cx - last_q[0] if last_q else cx
                qy = 2 * cy - last_q[1] if last_q else cy
            x = num() + (cx if rel else 0); y = num() + (cy if rel else 0)
            cubic(cx + 2 / 3 * (qx - cx), cy + 2 / 3 * (qy - cy),
                  x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y), x, y)
            cx, cy = x, y
            last_q = (qx, qy); last_c = None
        elif c == "A":
            rx = num(); ry = num(); rot = num()
            large = num() != 0; sweep = num() != 0
            x = num() + (cx if rel else 0); y = num() + (cy if rel else 0)
            for (x1, y1), (x2, y2), (ex, ey) in arc_to_cubics(
                    cx, cy, rx, ry, rot, large, sweep, x, y):
                cubic(x1, y1, x2, y2, ex, ey)
            cx, cy = x, y
            last_c = last_q = None
        elif c == "Z":
            out.append("Z"); cx, cy = sx, sy
            last_c = last_q = None
        else:
            i += 1
    return " ".join(out)
