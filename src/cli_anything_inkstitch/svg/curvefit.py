"""Fit cubic beziers back onto dense polylines.

Engine conversion tools (stroke_to_satin, fill_to_stroke, satin_to_stroke,
auto_satin, ...) compute in shapely and write their results as long M/L
point chains: one smooth Illustrator curve comes back as 50+ straight
segments with no handles, which makes the shape miserable to hand-edit in
the artifact editor. This module turns such a chain back into a few cubic
segments within a hard error bound.

Algorithm: P. J. Schneider, "An Algorithm for Automatically Fitting
Digitized Curves", Graphics Gems (1990) — chord-length parameterization,
least-squares control points along fixed end tangents, Newton–Raphson
reparameterization, split at the worst point. Two additions:

* the polyline is first split at sharp vertices (see ``CORNER_DEG``) so a
  corner stays a corner node instead of a smoothed one;
* acceptance is checked in BOTH directions — samples of the polyline to the
  curve (Schneider's own test) and samples of the curve back to the
  polyline — so a curve cannot bulge between widely spaced input points.

Error bound: callers pass ``tol`` in document units. The editor and tool
wrappers use ``FIT_TOL_MM`` (see below); the result is guaranteed within
``tol`` of the input polyline everywhere, so the engine's stitch plan moves
by at most ``tol`` plus its own flattening tolerance.
"""

from __future__ import annotations

import math
from typing import Sequence

Point = tuple[float, float]

# Max distance between the fitted curves and the engine's polyline, in mm.
# The engine flattens beziers back with cspsubdiv flatness 0.1 px
# (inkstitch lib/elements/element.py EmbroideryElement.flatten) ≈ 0.026 mm;
# 0.05 mm + 0.026 mm stays under 0.1 mm, the coordinate quantum of DST
# (Tajima stores stitches in 0.1 mm units) — the fit cannot move a stitch
# by a step the machine file can even represent.
FIT_TOL_MM = 0.05

# A vertex turning more than this is kept as a corner (both handles
# retracted toward the neighbours, no tangent continuity). Editing-UX knob,
# not an engine fact: the error bound holds whichever way it classifies.
CORNER_DEG = 50.0


# ---------------------------------------------------------------- vectors

def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def _mul(a: Point, s: float) -> Point:
    return (a[0] * s, a[1] * s)


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _norm(a: Point) -> Point:
    n = math.hypot(a[0], a[1])
    return (a[0] / n, a[1] / n) if n > 1e-12 else (0.0, 0.0)


def _bez(c: Sequence[Point], t: float) -> Point:
    u = 1 - t
    b0, b1, b2, b3 = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (b0 * c[0][0] + b1 * c[1][0] + b2 * c[2][0] + b3 * c[3][0],
            b0 * c[0][1] + b1 * c[1][1] + b2 * c[2][1] + b3 * c[3][1])


def _bez_d1(c, t):
    u = 1 - t
    return (3 * (u * u * (c[1][0] - c[0][0]) + 2 * u * t * (c[2][0] - c[1][0])
                 + t * t * (c[3][0] - c[2][0])),
            3 * (u * u * (c[1][1] - c[0][1]) + 2 * u * t * (c[2][1] - c[1][1])
                 + t * t * (c[3][1] - c[2][1])))


def _bez_d2(c, t):
    u = 1 - t
    return (6 * (u * (c[2][0] - 2 * c[1][0] + c[0][0]) + t * (c[3][0] - 2 * c[2][0] + c[1][0])),
            6 * (u * (c[2][1] - 2 * c[1][1] + c[0][1]) + t * (c[3][1] - 2 * c[2][1] + c[1][1])))


def _seg_dist(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


# ---------------------------------------------------------------- Schneider

def _chord_params(pts: Sequence[Point]) -> list[float]:
    u = [0.0]
    for i in range(1, len(pts)):
        u.append(u[-1] + math.dist(pts[i], pts[i - 1]))
    total = u[-1] or 1.0
    return [x / total for x in u]


def _generate(pts, u, t1: Point, t2: Point):
    p0, p3 = pts[0], pts[-1]
    c00 = c01 = c11 = x0 = x1 = 0.0
    for p, t in zip(pts, u):
        s = 1 - t
        b0, b1, b2, b3 = s * s * s, 3 * s * s * t, 3 * s * t * t, t * t * t
        a1 = _mul(t1, b1)
        a2 = _mul(t2, b2)
        c00 += _dot(a1, a1)
        c01 += _dot(a1, a2)
        c11 += _dot(a2, a2)
        tmp = _sub(p, _add(_mul(p0, b0 + b1), _mul(p3, b2 + b3)))
        x0 += _dot(a1, tmp)
        x1 += _dot(a2, tmp)
    det = c00 * c11 - c01 * c01
    seg = math.dist(p0, p3)
    eps = 1e-6 * seg
    if abs(det) > 1e-12:
        al = (x0 * c11 - x1 * c01) / det
        ar = (c00 * x1 - c01 * x0) / det
    else:
        al = ar = 0.0
    if al < eps or ar < eps:          # Wu/Barsky heuristic fallback
        al = ar = seg / 3
    return (p0, _add(p0, _mul(t1, al)), _add(p3, _mul(t2, ar)), p3)


def _reparam(c, pts, u):
    out = []
    for p, t in zip(pts, u):
        q, d1, d2 = _bez(c, t), _bez_d1(c, t), _bez_d2(c, t)
        diff = _sub(q, p)
        num = _dot(diff, d1)
        den = _dot(d1, d1) + _dot(diff, d2)
        out.append(min(1.0, max(0.0, t - num / den)) if abs(den) > 1e-12 else t)
    return out


def _max_err(c, pts, u):
    worst, split = 0.0, len(pts) // 2
    for i in range(1, len(pts) - 1):
        d = math.dist(_bez(c, u[i]), pts[i])
        if d > worst:
            worst, split = d, i
    return worst, split


def _curve_to_poly_err(c, pts, samples: int = 24) -> float:
    """Max distance from the curve to the polyline — catches bulges."""
    worst = 0.0
    for k in range(1, samples):
        q = _bez(c, k / samples)
        d = min(_seg_dist(q, pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        worst = max(worst, d)
    return worst


def _straight(pts, tol) -> bool:
    return all(_seg_dist(p, pts[0], pts[-1]) <= tol for p in pts[1:-1])


def _fit(pts, t1, t2, tol, out, depth=0):
    if len(pts) == 2 or _straight(pts, tol):
        out.append(("L", pts[-1]))
        return
    u = _chord_params(pts)
    c = _generate(pts, u, t1, t2)
    err, split = _max_err(c, pts, u)
    if err <= tol and _curve_to_poly_err(c, pts) <= tol:
        out.append(("C", c[1], c[2], c[3]))
        return
    if err <= tol * 4:
        for _ in range(20):
            u = _reparam(c, pts, u)
            c = _generate(pts, u, t1, t2)
            err, split = _max_err(c, pts, u)
            if err <= tol and _curve_to_poly_err(c, pts) <= tol:
                out.append(("C", c[1], c[2], c[3]))
                return
    if depth > 40:                     # pathological — keep the points
        for p in pts[1:]:
            out.append(("L", p))
        return
    split = max(1, min(len(pts) - 2, split))
    tc = _norm(_sub(pts[split - 1], pts[split + 1]))
    if tc == (0.0, 0.0):
        tc = _norm(_sub(pts[split - 1], pts[split]))
    _fit(pts[:split + 1], t1, tc, tol, out, depth + 1)
    _fit(pts[split:], _mul(tc, -1), t2, tol, out, depth + 1)


def _dedupe(pts: Sequence[Point], eps: float = 1e-6) -> list[Point]:
    out = [tuple(pts[0])]
    for p in pts[1:]:
        if math.dist(p, out[-1]) > eps:
            out.append(tuple(p))
    return out


def _turn_deg(a: Point, b: Point, c: Point) -> float:
    v1, v2 = _norm(_sub(b, a)), _norm(_sub(c, b))
    return math.degrees(math.acos(max(-1.0, min(1.0, _dot(v1, v2)))))


def fit_polyline(pts: Sequence[Point], tol: float, closed: bool = False,
                 corner_deg: float = CORNER_DEG) -> list[tuple]:
    """Segments ("L", p) / ("C", c1, c2, p) from pts[0], within ``tol``.

    ``closed``: pts describe a ring (the closing point may or may not be
    repeated); the result returns to pts[0] and, absent a corner there,
    keeps tangent continuity across the seam. Endpoints and corner
    vertices are reproduced exactly.
    """
    pts = _dedupe(pts)
    if closed:
        if len(pts) > 1 and math.dist(pts[0], pts[-1]) <= 1e-6:
            pts = pts[:-1]
        if len(pts) < 3:
            return [("L", p) for p in pts[1:] + [pts[0]]]
        n = len(pts)
        corners = [i for i in range(n)
                   if _turn_deg(pts[i - 1], pts[i], pts[(i + 1) % n]) > corner_deg]
        if corners:                    # rotate so the ring starts on a corner
            k = corners[0]
            pts = pts[k:] + pts[:k]
            corners = [(c - k) % n for c in corners]
        ring = pts + [pts[0]]
        cuts = sorted(set(corners) | {0, len(ring) - 1})
        seam_smooth = not corners
    else:
        if len(pts) < 3:
            return [("L", p) for p in pts[1:]]
        ring = pts
        cuts = [0] + [i for i in range(1, len(pts) - 1)
                      if _turn_deg(pts[i - 1], pts[i], pts[i + 1]) > corner_deg] + [len(pts) - 1]
        seam_smooth = False

    out: list[tuple] = []
    for a, b in zip(cuts, cuts[1:]):
        piece = ring[a:b + 1]
        if seam_smooth:
            # one smooth ring: shared tangent at the seam point
            t = _norm(_sub(ring[1], ring[-2]))
            t1, t2 = t, _mul(t, -1)
        else:
            t1 = _norm(_sub(piece[1], piece[0]))
            t2 = _norm(_sub(piece[-2], piece[-1]))
        _fit(piece, t1, t2, tol, out)
    return out


def segments_to_d(start: Point, segs: list[tuple], closed: bool = False,
                  prec: int = 3) -> str:
    def f(p):
        return f"{round(p[0], prec):g},{round(p[1], prec):g}"
    parts = ["M" + f(start)]
    for s in segs:
        if s[0] == "L":
            parts.append("L" + f(s[1]))
        else:
            parts.append("C" + " ".join(f(p) for p in s[1:]))
    if closed:
        parts.append("Z")
    return " ".join(parts)


def fit_polyline_d(pts: Sequence[Point], tol: float, closed: bool = False) -> tuple[str, int]:
    """``(d, nodes)`` for a fitted polyline subpath. ``nodes`` counts anchors
    (start included) so callers can refuse a fit that saved nothing."""
    pts = _dedupe(pts)
    if closed and len(pts) > 1 and math.dist(pts[0], pts[-1]) <= 1e-6:
        body = pts[:-1]
    else:
        body = pts
    segs = fit_polyline(body if closed else pts, tol, closed=closed)
    # fit_polyline may rotate a ring to start on a corner; a ring's last
    # segment always ends back on its start, so that endpoint IS the start
    start = segs[-1][-1] if closed else pts[0]
    d = segments_to_d(start, segs, closed=closed)
    return d, 1 + len(segs)


def uu_tolerance(uu_per_mm: float) -> float:
    """FIT_TOL_MM in document user units."""
    return FIT_TOL_MM * uu_per_mm


def fit_d_polylines(d: str, tol: float) -> tuple[str, int, int]:
    """Refit every pure-polyline subpath of ``d``; ``(new_d, before, after)``.

    Subpaths that already contain curves are user geometry and pass through
    byte-identical, as do subpaths too short to gain anything. Counts are
    anchors (subpath starts included). ``new_d`` is ``d`` itself when the
    fit saved nothing.
    """
    import re
    from cli_anything_inkstitch.svg.geometry import IDENTITY, transform_d

    subs = [c for c in re.split(r"(?=[Mm])", (d or "").strip()) if c.strip()]
    # relative subpaths start from the previous subpath's end, so normalize
    # the whole d once and split the absolute form instead
    norm = [c for c in re.split(r"(?=M)", transform_d(d, IDENTITY)) if c.strip()]
    if len(norm) != len(subs):
        return d, 0, 0
    out: list[str] = []
    before = after = 0
    for orig, sub in zip(subs, norm):
        toks = re.findall(r"[MLCZ]|-?[\d.]+(?:e-?\d+)?", sub)
        if "C" in toks:
            out.append(orig)
            continue
        closed = "Z" in toks
        nums = [float(t) for t in toks if t not in ("M", "L", "Z")]
        pts = list(zip(nums[0::2], nums[1::2]))
        before += len(pts)
        if len(pts) < 4:
            out.append(orig)
            after += len(pts)
            continue
        fitted, nodes = fit_polyline_d(pts, tol, closed=closed)
        if nodes < len(pts):
            out.append(fitted)
            after += nodes
        else:
            out.append(orig)
            after += len(pts)
    if after >= before:
        return d, before, before
    return " ".join(out), before, after


def parse_segments(sub: str) -> tuple[Point, list[tuple], bool]:
    """One subpath → ``(start, segments, closed)`` in absolute coordinates.

    Goes through svg/geometry.py transform_d with the identity matrix, which
    normalizes every command to M/L/C exactly (S/Q/T elevated, A as cubics).
    """
    import re
    from cli_anything_inkstitch.svg.geometry import IDENTITY, transform_d

    toks = re.findall(r"[MLCZ]|-?[\d.]+(?:e-?\d+)?", transform_d(sub, IDENTITY))
    start: Point | None = None
    segs: list[tuple] = []
    closed = False
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "M":
            if start is not None:      # only the first subpath is read
                break
            start = (float(toks[i + 1]), float(toks[i + 2])); i += 3
        elif t == "L":
            segs.append(("L", (float(toks[i + 1]), float(toks[i + 2])))); i += 3
        elif t == "C":
            v = [float(x) for x in toks[i + 1:i + 7]]
            segs.append(("C", (v[0], v[1]), (v[2], v[3]), (v[4], v[5]))); i += 7
        elif t == "Z":
            closed = True; i += 1
        else:
            i += 1
    if start is None:
        raise ValueError("subpath has no moveto")
    if closed and segs and math.dist(segs[-1][-1], start) > 1e-9:
        segs.append(("L", start))
    return start, segs, closed


def reverse_segments(start: Point, segs: list[tuple]) -> tuple[Point, list[tuple]]:
    """The same curve traversed backwards — exact (cubics reverse by swapping
    their control points)."""
    pts = [start] + [s[-1] for s in segs]
    out: list[tuple] = []
    for k in range(len(segs) - 1, -1, -1):
        s, prev = segs[k], pts[k]
        if s[0] == "L":
            out.append(("L", prev))
        else:
            out.append(("C", s[2], s[1], prev))
    return pts[-1], out
