"""Knife: slice path geometry along a polyline, keeping curves exact.

Pure geometry (no lxml, no engine). Two entry points:

* ``knife_fill(d, line)`` — a closed (fill) path split into closed pieces.
* ``knife_run(d, line)`` — an open (running-stitch) path split at every
  crossing.

Both return ``None`` when the line changes nothing (misses the shape, only
grazes it, or ends inside it without cutting through), so callers can treat
"no pieces" as a no-op instead of an error.

Curves stay curves: every boundary segment is cut at the exact intersection
parameter with de Casteljau subdivision, so a piece's boundary is the
original curve, sub-segment for sub-segment — never a flattened polyline.
Flattening is used only to *classify* faces (inside / outside, which hole
lies in which piece), never to produce output geometry.

What counts as "inside" mirrors the engine's own reading of a fill path:
FillStitch.original_shape (inkstitch/lib/elements/fill_stitch.py) takes the
largest subpath as the shell and every other subpath as a hole, and
FillStitch.shape runs make_valid when that polygon is invalid. Pieces are
written the same way (shell first, holes after), so each piece is a valid
fill to the engine.
"""

from __future__ import annotations

import math
from typing import Sequence

Point = tuple[float, float]
# A segment carries its own start: ("L", p0, p1) or ("C", p0, c1, c2, p1)
Seg = tuple

_EPS_T = 1e-9
_FLAT_N = 24          # samples per cubic for classification polygons


# -- segment primitives --------------------------------------------------------

def _lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _split_seg(seg: Seg, t: float) -> tuple[Seg, Seg]:
    """Exact split at parameter t (de Casteljau for cubics)."""
    if seg[0] == "L":
        m = _lerp(seg[1], seg[2], t)
        return ("L", seg[1], m), ("L", m, seg[2])
    p0, p1, p2, p3 = seg[1:]
    a, b, c = _lerp(p0, p1, t), _lerp(p1, p2, t), _lerp(p2, p3, t)
    d, e = _lerp(a, b, t), _lerp(b, c, t)
    m = _lerp(d, e, t)
    return ("C", p0, a, d, m), ("C", m, e, c, p3)


def _split_seg_many(seg: Seg, ts: Sequence[float]) -> list[Seg]:
    """Split at several increasing parameters of the ORIGINAL segment."""
    out: list[Seg] = []
    rest, consumed = seg, 0.0
    for t in ts:
        local = (t - consumed) / (1.0 - consumed)
        a, rest = _split_seg(rest, local)
        out.append(a)
        consumed = t
    out.append(rest)
    return out


def _reverse_seg(seg: Seg) -> Seg:
    if seg[0] == "L":
        return ("L", seg[2], seg[1])
    return ("C", seg[4], seg[3], seg[2], seg[1])


def _seg_end(seg: Seg) -> Point:
    return seg[-1]


def _seg_point(seg: Seg, t: float) -> Point:
    if seg[0] == "L":
        return _lerp(seg[1], seg[2], t)
    p0, p1, p2, p3 = seg[1:]
    mt = 1 - t
    return (mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0],
            mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1])


def _start_tangent(seg: Seg) -> Point:
    p0 = seg[1]
    for q in seg[2:]:
        if math.dist(p0, q) > 1e-12:
            return (q[0] - p0[0], q[1] - p0[1])
    return (0.0, 0.0)


def _flatten(seg: Seg, include_start: bool = True) -> list[Point]:
    if seg[0] == "L":
        return ([seg[1]] if include_start else []) + [seg[2]]
    pts = [_seg_point(seg, i / _FLAT_N) for i in range(0 if include_start else 1, _FLAT_N + 1)]
    return pts


def _cubic_roots(a: float, b: float, c: float, d: float) -> list[float]:
    """Real roots of a t^3 + b t^2 + c t + d (degenerate degrees handled)."""
    scale = max(abs(a), abs(b), abs(c), abs(d), 1e-300)
    a, b, c, d = a / scale, b / scale, c / scale, d / scale
    if abs(a) < 1e-12:
        if abs(b) < 1e-12:
            if abs(c) < 1e-12:
                return []
            return [-d / c]
        disc = c * c - 4 * b * d
        if disc < 0:
            return []
        s = math.sqrt(disc)
        return [(-c + s) / (2 * b), (-c - s) / (2 * b)]
    # depressed cubic (Cardano / trigonometric)
    b, c, d = b / a, c / a, d / a
    p = c - b * b / 3
    q = 2 * b**3 / 27 - b * c / 3 + d
    off = -b / 3
    disc = (q / 2) ** 2 + (p / 3) ** 3
    if disc > 1e-14:
        s = math.sqrt(disc)
        u = math.copysign(abs(-q / 2 + s) ** (1 / 3), -q / 2 + s)
        v = math.copysign(abs(-q / 2 - s) ** (1 / 3), -q / 2 - s)
        return [u + v + off]
    if abs(p) < 1e-14:
        return [math.copysign(abs(q) ** (1 / 3), -q) + off]
    r = math.sqrt(-p / 3)
    arg = max(-1.0, min(1.0, -q / (2 * r**3)))
    phi = math.acos(arg)
    return [2 * r * math.cos((phi - 2 * math.pi * k) / 3) + off for k in range(3)]


def _polish(seg: Seg, k0: Point, n: Point, t: float) -> float:
    """Newton-polish a cubic root of the signed distance to the knife line."""
    for _ in range(4):
        p = _seg_point(seg, t)
        f = (p[0] - k0[0]) * n[0] + (p[1] - k0[1]) * n[1]
        h = 1e-7
        p2 = _seg_point(seg, min(1.0, t + h)) if t + h <= 1 else _seg_point(seg, t - h)
        f2 = (p2[0] - k0[0]) * n[0] + (p2[1] - k0[1]) * n[1]
        df = (f2 - f) / (h if t + h <= 1 else -h)
        if abs(df) < 1e-14:
            break
        t = min(1.0, max(0.0, t - f / df))
    return t


def _intersect(seg: Seg, k0: Point, k1: Point) -> list[tuple[float, float]]:
    """(t on seg, s on knife segment k0→k1) for every crossing, both in [0, 1]."""
    dx, dy = k1[0] - k0[0], k1[1] - k0[1]
    ll = dx * dx + dy * dy
    if ll < 1e-24:
        return []
    n = (-dy, dx)                          # normal (unnormalized)
    ys = [(q[0] - k0[0]) * n[0] + (q[1] - k0[1]) * n[1] for q in seg[1:]]
    if all(y > 0 for y in ys) or all(y < 0 for y in ys):
        return []                          # convex hull entirely on one side
    if seg[0] == "L":
        y0, y1 = ys
        if y0 == y1:
            return []                      # parallel / collinear: no crossing
        ts = [y0 / (y0 - y1)]
    else:
        y0, y1, y2, y3 = ys
        a = -y0 + 3 * y1 - 3 * y2 + y3
        b = 3 * y0 - 6 * y1 + 3 * y2
        c = -3 * y0 + 3 * y1
        ts = [_polish(seg, k0, n, t) for t in _cubic_roots(a, b, c, y0)
              if -1e-6 <= t <= 1 + 1e-6]
    out = []
    for t in ts:
        t = min(1.0, max(0.0, t))
        p = _seg_point(seg, t)
        s = ((p[0] - k0[0]) * dx + (p[1] - k0[1]) * dy) / ll
        if -1e-9 <= s <= 1 + 1e-9:
            out.append((t, min(1.0, max(0.0, s))))
    return out


# -- d parsing -----------------------------------------------------------------

def parse_rings(d: str) -> list[tuple[list[Seg], bool]]:
    """Every subpath of ``d`` as (segments-with-starts, closed)."""
    from cli_anything_inkstitch.svg.curvefit import parse_segments
    from cli_anything_inkstitch.artifact.design_model import split_subpaths
    out = []
    for sub in split_subpaths(_absolutize(d)):
        start, segs, closed = parse_segments(sub)
        full: list[Seg] = []
        cur = start
        for s in segs:
            if s[0] == "L":
                if math.dist(cur, s[1]) > 1e-12:
                    full.append(("L", cur, s[1]))
            else:
                full.append(("C", cur, s[1], s[2], s[3]))
            cur = s[-1]
        if full:
            out.append((full, closed))
    return out


def _absolutize(d: str) -> str:
    """Normalize to absolute M/L/C/Z first so relative subpath starts (m)
    survive splitting into subpaths."""
    from cli_anything_inkstitch.svg.geometry import IDENTITY, transform_d
    return transform_d(d, IDENTITY)


def segs_to_d(segs: list[Seg], closed: bool) -> str:
    from cli_anything_inkstitch.svg.curvefit import segments_to_d
    body = [("L", s[2]) if s[0] == "L" else ("C", s[2], s[3], s[4]) for s in segs]
    if closed and body and body[-1][0] == "L":
        body = body[:-1]       # Z draws the closing line
    return segments_to_d(segs[0][1], body, closed=closed)


def _ring_poly(segs: list[Seg]) -> list[Point]:
    pts: list[Point] = [segs[0][1]]
    for s in segs:
        pts.extend(_flatten(s, include_start=False))
    return pts


def _signed_area(pts: Sequence[Point]) -> float:
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return a / 2


# -- splitting machinery ---------------------------------------------------------

def _cut_params(segs: list[Seg], line: Sequence[Point]):
    """For every (segment index) the sorted t params where the knife crosses,
    plus every crossing's (knife segment index, s)."""
    per_seg: dict[int, list[float]] = {}
    hits: list[tuple[int, float, Point]] = []
    for i, seg in enumerate(segs):
        for j in range(len(line) - 1):
            for t, s in _intersect(seg, line[j], line[j + 1]):
                per_seg.setdefault(i, []).append(t)
                hits.append((j, s, _seg_point(seg, t)))
    return per_seg, hits


def _split_ring(segs: list[Seg], per_seg: dict[int, list[float]]) -> list[Seg]:
    out: list[Seg] = []
    for i, seg in enumerate(segs):
        ts = sorted(t for t in per_seg.get(i, []) if _EPS_T < t < 1 - _EPS_T)
        dedup: list[float] = []
        for t in ts:
            if not dedup or t - dedup[-1] > 1e-9:
                dedup.append(t)
        out.extend(_split_seg_many(seg, dedup) if dedup else [seg])
    return out


class _Snap:
    """Merge points closer than tol into one vertex id."""

    def __init__(self, tol: float):
        self.tol = tol
        self.pts: list[Point] = []

    def id(self, p: Point) -> int:
        for i, q in enumerate(self.pts):
            if math.dist(p, q) <= self.tol:
                return i
        self.pts.append(p)
        return len(self.pts) - 1


class _EngineShape:
    """The fill area as the engine reads a fill path:
    FillStitch.original_shape (inkstitch/lib/elements/fill_stitch.py) takes
    the largest subpath as the shell and subtracts every other subpath as a
    hole. Point membership only (pure Python, no shapely: the editor server
    must not need the optional geometry extra). Invalid inputs the engine
    would make_valid (overlapping holes, islands outside the shell) are not
    modelled; for well-formed fills this is exact up to curve flattening."""

    def __init__(self, rings: list[list[Seg]]):
        polys = [_ring_poly(r) for r in rings]
        polys = [p for p in polys if len(p) >= 3]
        polys.sort(key=lambda p: abs(_signed_area(p)), reverse=True)
        self.shell = polys[0]
        self.holes = polys[1:]

    def area(self) -> float:
        return abs(_signed_area(self.shell)) - sum(abs(_signed_area(h)) for h in self.holes)

    def contains(self, p: Point) -> bool:
        return _pip(p, self.shell) and not any(_pip(p, h) for h in self.holes)


def fill_area(d: str) -> float:
    """Area of a fill path under the engine's shell-minus-holes reading."""
    return _EngineShape([segs for segs, _c in parse_rings(d)]).area()


def _pip(p: Point, poly: Sequence[Point]) -> bool:
    """Even-odd point in polygon."""
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def _interior_point(outer: Sequence[Point],
                    inner: Sequence[Sequence[Point]] = ()) -> Point | None:
    """A point well inside ``outer`` minus the ``inner`` rings: midpoint of
    the widest even-odd span over a few horizontal scanlines."""
    ys = [p[1] for p in outer]
    y0, y1 = min(ys), max(ys)
    if y1 - y0 <= 0:
        return None
    best: tuple[float, Point] | None = None
    for k in range(1, 12):
        y = y0 + (y1 - y0) * (k / 12 + 0.0137)        # dodge vertex rows
        xs: list[float] = []
        for poly in [outer, *inner]:
            n = len(poly)
            for i in range(n):
                ax, ay = poly[i]
                bx, by = poly[(i + 1) % n]
                if (ay > y) != (by > y):
                    xs.append(ax + (y - ay) * (bx - ax) / (by - ay))
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            w = xs[i + 1] - xs[i]
            if best is None or w > best[0]:
                best = (w, ((xs[i] + xs[i + 1]) / 2, y))
    return best[1] if best else None


def knife_fill(d: str, line: Sequence[Point]) -> list[str] | None:
    """Split a fill path along the polyline ``line``.

    Returns the pieces' ``d`` strings (each shell first, then the holes that
    lie inside it), or None when nothing was cut.
    """
    line = [tuple(map(float, p)) for p in line]
    if len(line) < 2:
        return None
    rings = []
    for segs, _closed in parse_rings(d):
        # the engine closes every fill subpath (shapely rings close)
        if math.dist(segs[-1][-1], segs[0][1]) > 1e-9:
            segs = segs + [("L", segs[-1][-1], segs[0][1])]
        rings.append(segs)
    if not rings:
        return None
    shape = _EngineShape(rings)

    xs = [p[0] for r in rings for s in r for p in s[1:]]
    ys = [p[1] for r in rings for s in r for p in s[1:]]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
    snap = _Snap(span * 1e-7)

    # 1. boundary edges, split at every crossing
    touched: list[bool] = []
    knife_hits: dict[int, list[tuple[float, Point]]] = {}
    ring_edges: list[list[Seg]] = []
    for segs in rings:
        per_seg, hits = _cut_params(segs, line)
        for j, s, p in hits:
            knife_hits.setdefault(j, []).append((s, p))
        ring_edges.append(_split_ring(segs, per_seg))
        touched.append(bool(hits))
    if not knife_hits:
        return None

    # 2. knife pieces between consecutive crossings, kept where they run
    #    through the fill (outside stretches bound nothing)
    knife_edges: list[Seg] = []
    for j in range(len(line) - 1):
        stops = [(0.0, line[j]), (1.0, line[j + 1])] + knife_hits.get(j, [])
        stops.sort(key=lambda sp: sp[0])
        for (_s0, p0), (_s1, p1) in zip(stops, stops[1:]):
            if math.dist(p0, p1) <= snap.tol * 10:
                continue
            if shape.contains(_lerp(p0, p1, 0.5)):
                knife_edges.append(("L", p0, p1))

    # 3. graph: vertices snapped, drop dangling knife edges (a knife that
    #    starts or ends inside the shape cuts nothing there)
    def key(e: Seg) -> tuple[int, int]:
        return snap.id(e[1]), snap.id(e[-1])

    bound = [(e, key(e)) for r, t in zip(ring_edges, touched) if t for e in r]
    knife = [(e, key(e)) for e in knife_edges]
    while True:
        deg: dict[int, int] = {}
        for _e, (u, v) in bound + knife:
            deg[u] = deg.get(u, 0) + 1
            deg[v] = deg.get(v, 0) + 1
        keep = [k for k in knife if deg[k[1][0]] > 1 and deg[k[1][1]] > 1]
        if len(keep) == len(knife):
            break
        knife = keep
    if not knife:
        return None

    # 4. planar faces: half-edges; at each vertex take the next edge
    #    clockwise from the one we arrived on, so bounded faces come out with
    #    positive shoelace area and each component's outside negative
    half: list[tuple[int, int, Seg]] = []
    for e, (u, v) in bound + knife:
        half.append((u, v, e))
        half.append((v, u, _reverse_seg(e)))
    out_at: dict[int, list[int]] = {}
    for h, (u, _v, _e) in enumerate(half):
        out_at.setdefault(u, []).append(h)
    angle = []
    for _u, _v, e in half:
        tx, ty = _start_tangent(e)
        angle.append(math.atan2(ty, tx))

    used = [False] * len(half)
    faces: list[list[Seg]] = []
    for h0 in range(len(half)):
        if used[h0]:
            continue
        face, h = [], h0
        while not used[h]:
            used[h] = True
            _u, v, e = half[h]
            face.append(e)
            back = angle[h ^ 1]                # the twin leaves v back along e
            best, best_turn = None, None
            for c in out_at.get(v, []):
                turn = (back - angle[c]) % (2 * math.pi)
                if c == (h ^ 1) or turn < 1e-12:
                    turn = 2 * math.pi
                if best_turn is None or turn < best_turn:
                    best, best_turn = c, turn
            if best is None:
                break
            h = best
        if h == h0 and face:
            faces.append(face)

    # 5. classify. Untouched rings take part whole, as islands or holes.
    untouched = [r for r, t in zip(ring_edges, touched) if not t]
    untouched_polys = [_ring_poly(r) for r in untouched]

    def is_inside(poly: list[Point], exclude: int = -1) -> bool:
        inner = [up for k, up in enumerate(untouched_polys)
                 if k != exclude and _pip(up[0], poly)
                 and abs(_signed_area(up)) < abs(_signed_area(poly))]
        pt = _interior_point(poly, inner)
        return pt is not None and shape.contains(pt)

    pieces: list[tuple[list[Seg], list[Point]]] = []
    for face in faces:
        poly = _ring_poly(face)
        if _signed_area(poly) <= (snap.tol * 10) ** 2:
            continue                       # outside of a component / sliver
        if is_inside(poly):
            pieces.append((face, poly))
    holes: list[tuple[list[Seg], list[Point]]] = []
    for k, (r, poly) in enumerate(zip(untouched, untouched_polys)):
        (pieces if is_inside(poly, exclude=k) else holes).append((r, poly))
    if len(pieces) < 2:
        return None

    # 6. each hole goes with the smallest piece that contains it
    assigned: list[list[list[Seg]]] = [[] for _ in pieces]
    for r, hp in holes:
        probe = _interior_point(hp) or hp[0]
        owners = [i for i, (_f, pp) in enumerate(pieces) if _pip(probe, pp)]
        if owners:
            i = min(owners, key=lambda k: abs(_signed_area(pieces[k][1])))
            assigned[i].append(r)
    return [" ".join([segs_to_d(face, True)] + [segs_to_d(h, True) for h in hs])
            for (face, _p), hs in zip(pieces, assigned)]


def knife_run(d: str, line: Sequence[Point]) -> list[str] | None:
    """Split an open (running-stitch) path at every crossing of ``line``.

    Each cut subpath yields one piece per stretch; subpaths the knife does
    not touch stay together as one more piece. A closed subpath (a ring of
    running stitch) is opened at its cuts: one cut opens it into a single
    piece, n cuts make n arcs.
    """
    line = [tuple(map(float, p)) for p in line]
    if len(line) < 2:
        return None
    pieces: list[str] = []
    untouched: list[str] = []
    cut_any = False
    for segs, closed in parse_rings(d):
        if closed and math.dist(segs[-1][-1], segs[0][1]) > 1e-9:
            segs = segs + [("L", segs[-1][-1], segs[0][1])]
        per_seg, hits = _cut_params(segs, line)
        # cut points strictly inside the path (an open path's own ends are
        # not cuts)
        split = _split_ring(segs, per_seg)
        cut_pts = [p for _j, _s, p in hits]
        span = max(1e-9, max(math.dist(s[1], s[-1]) for s in segs))
        tol = span * 1e-6

        def is_cut(p):
            return any(math.dist(p, q) <= tol for q in cut_pts)

        runs: list[list[Seg]] = [[]]
        for s in split:
            runs[-1].append(s)
            if is_cut(s[-1]):
                runs.append([])
        runs = [r for r in runs if r]
        if not closed:
            if len(runs) < 2:
                untouched.append(segs_to_d(segs, False))
                continue
        else:
            if not hits:
                untouched.append(segs_to_d(segs, True))
                continue
            if not is_cut(segs[0][1]) and len(runs) > 1:
                runs = [runs[-1] + runs[0]] + runs[1:-1]   # rejoin across start
        cut_any = True
        pieces.extend(segs_to_d(r, False) for r in runs)
    if not cut_any:
        return None
    if untouched:
        pieces.append(" ".join(untouched))
    return pieces
