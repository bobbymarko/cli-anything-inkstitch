# SPDX-License-Identifier: Apache-2.0
# Derived from stitchpilot (same author, Apache-2.0), moved here so the column planner has one home.
# Public names, signatures and return types are kept as they were there, so code written against it
# keeps working; new behaviour goes behind new parameters whose defaults reproduce the old output.
"""Work out a letter's satin strokes from its outline, for letters without built-in strokes.

The letter's centrelines are its medial axis: the centres of the circles that touch the outline in two
places. It's taken from a Voronoi diagram of points along the outline, keeping only the ridges whose two
outline points face each other across the stroke (a stroke is about twice as wide as the circle's radius;
the spur running into a square corner is only about 1.4 times, so it's dropped).

The axis is then cut into branches at its junctions and joined back into strokes as a digitizer would:
- where branches meet, the two that run on most nearly straight are one stroke; the others butt against
  it, cut back to its edge and cut along it;
- a sharp corner (N's leg into its diagonal, D's stem into its bowl) is two strokes, and the more
  upright one runs through to the outline;
- a stroke's free ends run on to the outline;
- an axis loop with no junction (O) is one closed stroke.

Strokes are ordered as a sewing path: from the lowest left end, each next stroke starts at the end
nearest where the last one finished. The result is only a proposal: `columns.check_letter` checks it
against the outline, and anything it doesn't pass needs strokes given by hand.
"""

from __future__ import annotations

import math

import numpy as np

from .columns import Stroke

SAMPLE_MM = 0.25           # spacing of outline points
NEIGHBOUR_ARC_MM = 1.0     # ridges between outline points closer than this along the outline are hairs
SPUR = 1.6                 # a terminal branch shorter than this many junction radii runs into a corner
STUB_TAPER = 0.3           # ...unless its outer half narrows slower than this (mm per mm): a stub
STUB_MIN_R = 0.6           # and its tip is still this wide a circle (corner spurs stop near NEIGHBOUR_ARC)
CORNER_TURN_DEG = 50.0     # a turn sharper than this at a junction is a corner: two strokes
CORNER_SPAN = 1.5          # a corner's turn is measured over this many half widths each side
TANGENT_SPAN = 2.5         # branch direction at a junction is measured this many radii along it
STRAIGHT_PCT = 1.2         # a branch within this % of the cap height of its chord is straight
CURVE_POINT_MM = 3.0       # spacing of points on a curved stroke's path
CUT_SNAP_DEG = 12.0        # a cut this close to vertical/horizontal is made exactly that
SIDE_HALF_MAX = 2.0        # a through stroke's width from its back is trusted up to this times the clear one
SIDE_SAMPLES = 6           # ...measured over this many axis points next to the junction
MIN_CUT_DEG = 35.0         # a cut must cross the stroke at least this steeply
PLAN_MIN_STROKE_MM = 2.0   # the mm thresholds here are tuned on strokes at least this wide; thinner
                           # letters are planned scaled up to it (letter_strokes)


def stroke_width_mm(contours: list[np.ndarray]) -> float:
    """A shape's typical stroke width: twice its area over its outline's length (a band w wide and
    L long has area wL and outline about 2L)."""
    from .columns import Letter

    a = np.vstack(contours)
    g = Letter("?", [], contours, (a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max())).glyph()
    return 2 * g.area / g.length if g.length else 0.0


# ---------------------------------------------------------------- medial axis

def _sample(contours: list[np.ndarray], step: float):
    pts, owner, arc = [], [], []
    for ci, c in enumerate(contours):
        ring = np.vstack([c, c[:1]])
        seg = np.hypot(*np.diff(ring, axis=0).T)
        s = np.concatenate([[0], np.cumsum(seg)])
        n = max(8, int(s[-1] / step))
        t = np.linspace(0, s[-1], n, endpoint=False)
        pts.append(np.column_stack([np.interp(t, s, ring[:, 0]), np.interp(t, s, ring[:, 1])]))
        owner += [ci] * n
        arc.append(t)
    return np.vstack(pts), np.array(owner), np.concatenate(arc)


def medial_axis(contours: list[np.ndarray], glyph, step: float = SAMPLE_MM):
    """(vertices, radius per vertex, edges) of the letter's medial axis, in mm."""
    from scipy.spatial import Voronoi
    from shapely import contains_xy

    pts, owner, arc = _sample(contours, step)
    perim = {ci: float(np.hypot(*np.diff(np.vstack([c, c[:1]]), axis=0).T).sum()) for ci, c in enumerate(contours)}
    vor = Voronoi(pts)
    v = vor.vertices
    inside = contains_xy(glyph, v[:, 0], v[:, 1])
    edges = set()
    for (p, q), (a, b) in zip(vor.ridge_points, vor.ridge_vertices):
        if a < 0 or b < 0 or not (inside[a] and inside[b]):
            continue
        if owner[p] == owner[q]:
            gap = abs(arc[p] - arc[q])
            if min(gap, perim[owner[p]] - gap) < NEIGHBOUR_ARC_MM:
                continue        # two points side by side on the outline: a hair, not the axis
        edges.add((min(a, b), max(a, b)))
    radius = np.full(len(v), np.nan)
    for (p, q), (a, b) in zip(vor.ridge_points, vor.ridge_vertices):
        for k in (a, b):
            if k >= 0 and np.isnan(radius[k]):
                radius[k] = np.hypot(*(v[k] - pts[p]))
    return v, radius, edges


# ---------------------------------------------------------------- branches

def _adjacency(edges) -> dict[int, set[int]]:
    adj: dict[int, set[int]] = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def _branches(adj):
    """Maximal chains between nodes whose degree isn't 2, plus junction-free loops."""
    seen, out = set(), []
    nodes = [n for n in adj if len(adj[n]) != 2]
    for n in nodes:
        for m in adj[n]:
            if (n, m) in seen:
                continue
            chain = [n, m]
            seen.add((n, m))
            seen.add((m, n))
            while len(adj[chain[-1]]) == 2:
                nxt = next(x for x in adj[chain[-1]] if x != chain[-2])
                if (chain[-1], nxt) in seen:
                    break
                seen.add((chain[-1], nxt))
                seen.add((nxt, chain[-1]))
                chain.append(nxt)
            out.append(chain)
    for n in adj:                      # loops with no junction (O)
        if len(adj[n]) != 2 or any((n, m) in seen for m in adj[n]):
            continue
        chain = [n]
        prev, cur = None, n
        while True:
            nxt = next(x for x in adj[cur] if x != prev)
            seen.add((cur, nxt))
            seen.add((nxt, cur))
            if nxt == n:
                chain.append(n)
                break
            chain.append(nxt)
            prev, cur = cur, nxt
        out.append(chain)
    return out


def _length(v, chain) -> float:
    p = v[chain]
    return float(np.hypot(*np.diff(p, axis=0).T).sum())


def _largest_component(edges):
    adj = _adjacency(edges)
    best: set[int] = set()
    seen: set[int] = set()
    for n in adj:
        if n in seen:
            continue
        comp, stack = set(), [n]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            stack += list(adj[x] - comp)
        seen |= comp
        if len(comp) > len(best):
            best = comp
    return {e for e in edges if e[0] in best}


def _is_stub(v, radius, chain) -> bool:
    """Whether a terminal branch (from its junction to its tip) keeps its width over its outer half,
    as a stroke end does, rather than narrowing into a corner (by about sin(45deg) per mm into a
    square corner, faster into an obtuse one)."""
    p = v[chain]
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(p, axis=0).T))])
    if len(chain) < 4 or s[-1] <= 0:
        return False
    r = radius[chain]
    half = s[-1] / 2
    r_mid = float(np.interp(half, s, r))
    return (r_mid - float(r[-1])) / half < STUB_TAPER and r[-1] >= STUB_MIN_R


def prune(v, radius, edges):
    """Drop leftover spurs (a terminal branch shorter than the circle at its junction) until none are
    left, and keep the largest connected piece."""
    edges = set(edges)
    for _ in range(50):
        adj = _adjacency(edges)
        changed = False
        for chain in _branches(adj):
            a, b = chain[0], chain[-1]
            if a == b:
                continue
            da, db = len(adj[a]), len(adj[b])
            if (da == 1) == (db == 1):
                continue
            root = a if da != 1 else b
            # A spur runs into a corner and narrows steadily towards it; a short stroke end that keeps
            # its width (a t's crossbar sticking out past the stem, a 4's bar) is ink to cover.
            if _length(v, chain) < SPUR * radius[root] and not _is_stub(v, radius, chain if root == a else chain[::-1]):
                for x, y in zip(chain, chain[1:]):
                    edges.discard((min(x, y), max(x, y)))
                changed = True
        if not changed:
            break
    return _largest_component(edges)


# ---------------------------------------------------------------- strokes

class _Piece:
    """A branch as a polyline from its first node to its last."""

    def __init__(self, pts: np.ndarray, start: int | None, end: int | None, r: np.ndarray, closed=False):
        self.pts, self.start, self.end, self.r, self.closed = pts, start, end, r, closed

    def direction_from(self, at_start: bool) -> np.ndarray:
        """Unit direction leaving the node at this end, measured some radii along."""
        pts = self.pts if at_start else self.pts[::-1]
        r = self.r if at_start else self.r[::-1]
        span = TANGENT_SPAN * float(np.nanmax(r[: max(2, len(r) // 4)]))
        s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        k = int(np.searchsorted(s, min(span, s[-1] * 0.5)))
        k = max(1, min(k, len(pts) - 1))
        d = pts[k] - pts[0]
        return d / (np.hypot(*d) or 1)

    def half_width(self) -> float:
        """The stroke's typical half width (the junction circles are bigger)."""
        return float(np.nanmedian(self.r))


def _smooth(pts: np.ndarray, window: int = 5) -> np.ndarray:
    if len(pts) <= window:
        return pts
    k = window // 2
    out = pts.copy()
    for i in range(k, len(pts) - k):
        out[i] = pts[i - k:i + k + 1].mean(axis=0)
    return out


def _ray_to_outline(boundary, p, d, far=1000.0):
    from shapely.geometry import LineString

    hit = boundary.intersection(LineString([tuple(p), tuple(p + d * far)]))
    best = None
    for g in getattr(hit, "geoms", [hit]):
        for c in getattr(g, "coords", []):
            t = float(np.dot(np.array(c) - p, d))
            if t > 1e-6 and (best is None or t < best[0]):
                best = (t, np.array(c))
    return best


def _edge_dir_at(contours, q) -> np.ndarray:
    """Direction of the outline at point q (nearest contour segment)."""
    best, bd = None, 1e9
    for c in contours:
        ring = np.vstack([c, c[:1]])
        a, b = ring[:-1], ring[1:]
        ab = b - a
        t = np.clip(((q - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
        proj = a + ab * t[:, None]
        dist = np.hypot(*(proj - q).T)
        i = int(np.argmin(dist))
        if dist[i] < bd:
            bd, best = dist[i], ab[i] / (np.hypot(*ab[i]) or 1)
    return best


INSIDE_CORNER_DEG = 40.0   # the outline turns at least this much, inwards, within INSIDE_CORNER_SPAN_MM
INSIDE_CORNER_SPAN_MM = 0.4
BEND_CORNER_REACH = 2.5   # a bend this many junction radii from a smooth-paired junction stays a curve
SECTOR_REACH = 2.5         # an inside corner this many junction radii from it lies between its branches


def _concave_corners(contours, glyph) -> np.ndarray:
    """Points where the outline turns sharply inwards (a notch between two strokes)."""
    from shapely import contains_xy

    out = []
    step = 0.1
    k = max(1, int(round(INSIDE_CORNER_SPAN_MM / step)))
    for c in contours:
        ring = np.vstack([c, c[:1]])
        s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(ring, axis=0).T))])
        if s[-1] < 4 * k * step:
            continue
        t = np.arange(0, s[-1], step)
        p = np.column_stack([np.interp(t, s, ring[:, 0]), np.interp(t, s, ring[:, 1])])
        n = len(p)
        a, b = p[(np.arange(n) - k) % n], p[(np.arange(n) + k) % n]
        d1, d2 = p - a, b - p
        cos = (d1 * d2).sum(1) / (np.hypot(*d1.T) * np.hypot(*d2.T) + 1e-12)
        turn = np.degrees(np.arccos(np.clip(cos, -1, 1)))
        hot = np.where(turn > INSIDE_CORNER_DEG)[0]
        if not len(hot):
            continue
        # inwards: the point a little way along the turn's outward bisector is on the ink
        bis = (d1 / (np.hypot(*d1.T)[:, None] + 1e-12) - d2 / (np.hypot(*d2.T)[:, None] + 1e-12))
        probe = p - bis / (np.hypot(*bis.T)[:, None] + 1e-12) * 0.3
        inside = contains_xy(glyph, probe[:, 0], probe[:, 1])
        # one point per corner: the sharpest of each run
        run = []
        for i in list(hot) + [None]:
            if i is not None and (not run or i - run[-1] <= 2):
                run.append(i)
                continue
            if run:
                m = max(run, key=lambda q: turn[q])
                if not inside[m]:          # the probe outside the bend's mouth: an inside corner
                    out.append(p[m])
            run = [i] if i is not None else []
    return np.array(out).reshape(-1, 2)


def _smooth_pairs(es, dirs, at, r, corners, near_lines) -> list:
    """At a junction of 3 or more branches: the pairs of neighbouring branches (by direction) with no
    inside corner between them, when those pairs give each branch at most one partner and at least
    one branch is separated by corners. Empty when there's no such clear reading (then the
    straightest pair runs through). A corner is between the two branches whose centrelines near the
    junction (`near_lines`) pass closest to it."""
    from shapely.geometry import LineString, Point

    if len(es) < 3 or not len(corners):
        return []
    ang = {e: math.atan2(dirs[e][1], dirs[e][0]) for e in es}
    order = sorted(es, key=lambda e: ang[e])
    lines = {e: LineString(near_lines[e]) for e in es if len(near_lines[e]) >= 2}
    if len(lines) != len(es):
        return []
    split = set()
    for c in corners:
        if np.hypot(*(c - at)) > SECTOR_REACH * max(r, 0.3):
            continue
        two = sorted(es, key=lambda e: lines[e].distance(Point(c)))[:2]
        split.add(frozenset(two))
    if not split:
        return []
    pairs, sharp = [], 0
    for i, e in enumerate(order):
        f = order[(i + 1) % len(order)]
        if frozenset((e, f)) in split:
            sharp += 1
        elif e[0] != f[0]:
            pairs.append((e, f))
    used = [x for p in pairs for x in p]
    # Only a full reading: every branch has exactly one smooth partner, so the junction resolves into
    # continuous strokes meeting between the corners, with nothing left to butt. Where a branch would
    # be left over (an R's leg, a T's stem) the straightest pair runs through as before.
    if not pairs or sharp == 0 or len(used) != len(set(used)) or set(used) != set(es):
        return []
    return pairs


END_EDGE_SHARE = 0.7      # a free end's edge is found this share of its half width out from the centre


BLOB_MIN_MM = 0.4          # a piece with no centreline is sewn only if it's at least this wide
RUN_TURN_DEG = 12.0       # an outline run stays straight while it turns less than this from where it started
RUN_MIN_MM = 0.6           # a straight run shorter than this isn't an edge to cut along
RUN_MIN_SHARE = 0.8        # ...nor one shorter than this share of the stroke's width


def _straight_run_dir(contours, q, half: float):
    """Direction of the straight stretch of outline through q (a W's flat foot, a cap line): the
    outline followed both ways from q while it keeps its direction, up to 1.5 half widths each way.
    None where the outline curves there (a round tip)."""
    best, bd = None, 1e9
    for c in contours:
        ring = np.vstack([c, c[:1]])
        s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(ring, axis=0).T))])
        t = np.arange(0, s[-1], 0.05)
        p = np.column_stack([np.interp(t, s, ring[:, 0]), np.interp(t, s, ring[:, 1])])
        i = int(np.argmin(np.hypot(*(p - q).T)))
        dist = float(np.hypot(*(p[i] - q)))
        if dist < bd:
            bd, best = dist, (p, i)
    if best is None:
        return None
    p, i = best
    n = len(p)
    k = 4                                            # 0.2 mm either side for the local direction
    local = p[(i + k) % n] - p[(i - k) % n]
    local = local / (np.hypot(*local) or 1)
    limit = math.cos(math.radians(RUN_TURN_DEG))
    reach = int(1.5 * max(half, 0.5) / 0.05)
    ends = []
    for step in (1, -1):
        j = i
        for _ in range(reach):
            nxt = (j + step) % n
            seg = p[(nxt + step * k) % n] - p[nxt]
            seg = seg * step
            if np.dot(seg / (np.hypot(*seg) or 1), local) < limit:
                break
            j = nxt
        ends.append(p[j])
    v = ends[0] - ends[1]
    # A foot spans most of the stroke's width; a shorter run is a side edge near a corner.
    if np.hypot(*v) < max(RUN_MIN_MM, RUN_MIN_SHARE * 2 * half):
        return None
    return v / np.hypot(*v)


def _end_edge_dir(boundary, contours, p, d, half: float) -> np.ndarray:
    """Direction of the outline across a stroke's free end: the line between where its two sides run
    into the outline, going on along d from p (just inside the end). That's the baseline under a W's
    flat foot, square across a round tip, slanted under a slanted foot. The single nearest outline
    segment can be a side edge at a corner instead."""
    n = np.array([-d[1], d[0]])
    hits = []
    for sgn in (1, -1):
        h = _ray_to_outline(boundary, p + sgn * n * END_EDGE_SHARE * half, d)
        if h is None:
            return _edge_dir_at(contours, p)
        hits.append(h[1])
    v = hits[0] - hits[1]
    if np.hypot(*v) < 1e-6:
        return _edge_dir_at(contours, p)
    return v / np.hypot(*v)


END_VEER_SPAN = 2.5       # a free end's last this many half widths, against the stretch before them
END_VEER_DEG = 15.0       # ...turning this much more than the stroke's own curve, veer off to a corner
END_VEER_BARE_MM = 0.4    # ...run straight on, unless that leaves this much of the tip past the end bare
END_VEER_SPAN_FOOT = 4.0  # a longer veer is straightened only onto a flat foot (_flat_end_ahead)
END_VEER_FOOT_DEG = 10.0  # ...where a smaller veer counts (the leg is checked straight and the end flat)
FOOT_LEG_STRAIGHT = 0.15  # ...from a straight leg: its axis within this share of the half width of its chord
FOOT_LEG_MIN = 1.2        # ...over at least this many half widths
FOOT_LEG_SHORT = 0.8      # ...or down to this many, if within FOOT_LEG_DEAD of its chord
FOOT_LEG_DEAD = 0.015
FOOT_LEG_SPAN = 3.0       # ...and at most this many, behind the end
END_VEER_NARROW = 0.8     # ...and only where the stroke narrows into that end (a quarter way along the last
                          # stretch, at most this share of its width at the start of it)


FLAT_END_SPREAD = 0.8     # a flat end ahead: rays this share of the half width either side of the centre
FLAT_END_TOL = 0.06       # ...all meet one straight edge, to within this share of the half width


def _flat_end_ahead(boundary, p: np.ndarray, d: np.ndarray, half: float) -> bool:
    """Running on from p along d, does the stroke's whole width meet one straight edge (a foot on the
    baseline), crossing it at least MIN_CUT_DEG steeply?"""
    n = np.array([-d[1], d[0]])
    hits = []
    for k in (-FLAT_END_SPREAD, 0.0, FLAT_END_SPREAD):
        h = _ray_to_outline(boundary, p + k * half * n, d)
        if h is None:
            return False
        hits.append(np.asarray(h[1], float))
    a, m, b = hits
    v = b - a
    L = float(np.hypot(*v))
    if L < 1e-6:
        return False
    v = v / L
    off = abs(float((m - a)[0] * v[1] - (m - a)[1] * v[0]))
    return off <= FLAT_END_TOL * half and abs(float(np.dot(v, d))) <= math.cos(math.radians(MIN_CUT_DEG))


def _signed_turn(d1: np.ndarray, d2: np.ndarray) -> float:
    return math.degrees(math.atan2(d1[0] * d2[1] - d1[1] * d2[0], float(np.dot(d1, d2))))


def _straighten_free_end(seg: np.ndarray, half: float, boundary=None) -> np.ndarray:
    """A free end (seg[0]) whose centreline veers off to one side for its last stretch: the medial axis
    runs into the nearest sharp corner of an end that isn't square across the stroke (a blade-shaped
    tip, one edge straight and the other curving in to meet it; a slanted cut end). Followed, the
    stitches fan round from one point. Dropped, the stroke runs straight on to the outline and the
    column narrows along the curved edge into the corner. Not where running straight on leaves through
    the stroke's side (a curved leaf's pointed tip, whose point lies off that line)."""
    for span, flat_only in ((END_VEER_SPAN, False), (END_VEER_SPAN_FOOT, True)):
        out = _straighten_span(seg, half, boundary, span, flat_only)
        if out is not None:
            return out
    return seg


def _straighten_span(seg, half, boundary, span, flat_only):
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(seg, axis=0).T))])
    T = span * max(half, 0.3)
    if flat_only:
        # a leg onto a foot can be short for its width (an R's): its body is its first half
        T = min(T, s[-1] / 2)
        if T < END_VEER_SPAN * max(half, 0.3) / 2:
            return None
    elif s[-1] < 3 * T:
        return None

    def at(x):
        return np.array([np.interp(x, s, seg[:, 0]), np.interp(x, s, seg[:, 1])])

    def unit(v):
        return v / (np.hypot(*v) or 1)

    body = unit(at(T) - at(2 * T))          # towards the end
    prev = unit(at(2 * T) - at(3 * T)) if s[-1] >= 3 * T else body
    tail = unit(seg[0] - at(T))
    expect = _signed_turn(prev, body)       # the stroke's own curve over that length
    if abs(_signed_turn(body, tail) - expect) <= (END_VEER_FOOT_DEG if flat_only else END_VEER_DEG):
        return None
    keep = s >= T
    if keep.sum() < 2:
        return None
    p0 = at(T)
    if flat_only:
        # Runs on from where the veer starts in the leg's direction (the caller casts on from its
        # first points), not the axis's local bend there.
        step = max(0.05, 0.1 * half)
        out = np.vstack([p0 - body * step * k for k in range(4)] + [seg[s > T + 3 * step]])
    else:
        out = seg[keep]
        p0 = out[0]
        body = unit(out[0] - out[min(3, len(out) - 1)])
    if boundary is None:
        return None if flat_only else out
    from shapely.geometry import Point

    # A slanted leg onto a flat foot (an R's or K's leg on the baseline): the foot's fillet on one
    # side pulls the axis over to its sharp corner on the other. Running straight on meets the flat
    # end square across the whole stroke.
    flat = _flat_end_ahead(boundary, p0, body, half)
    if flat_only:
        if not flat:
            return None
        # ...and the leg is straight up to where the veer starts (an n's arch or a u's bowl into its
        # foot is a curve of the stroke, followed as it is).
        # the leg just behind the end, clear of the junction at its other end (a Y's arm runs on
        # through the junction into the stem: that bend isn't the arm's)
        rest = seg[(s >= T) & (s <= min(s[-1] - half, T + FOOT_LEG_SPAN * half))]
        if len(rest) < 3:
            return None
        chord = rest[-1] - rest[0]
        L = float(np.hypot(*chord))
        if L < FOOT_LEG_SHORT * half:
            return None
        dev = np.abs((rest[:, 0] - rest[0, 0]) * chord[1] - (rest[:, 1] - rest[0, 1]) * chord[0]) / L
        # A short stretch (a short leg on its own, as an R's leg drawn apart from its bowl) has to be
        # dead straight to count: a 2's or 6's curve can look straight over a little length.
        if float(dev.max()) > (FOOT_LEG_STRAIGHT if L >= FOOT_LEG_MIN * half else FOOT_LEG_DEAD) * half:
            return None
    # A blade narrows into its corner. An end that keeps its width (a J's hook curling up to its flat
    # end) or swells (a c's ball) is a real curve of the stroke, followed as it is.
    if not flat_only and boundary.distance(Point(at(T / 4))) > END_VEER_NARROW * boundary.distance(Point(at(T))):
        return None
    hit = _ray_to_outline(boundary, p0, body)
    if hit is None:
        return None
    if not flat_only:
        # The corner the axis ran into must be about where running straight on reaches the outline
        # (the blade's corner, at its end): otherwise the tip lies further on, off that line.
        d0 = unit(seg[0] - seg[min(3, len(seg) - 1)])
        tip = _ray_to_outline(boundary, seg[0], d0)
        if tip is not None and float(np.dot(tip[1] - hit[1], body)) > END_VEER_BARE_MM:
            return None
    # ...and running straight on must reach the end, not leave through a side (a serif's point).
    dirs = _outline_dirs_at(boundary, hit[1], 0.2)
    if dirs is None:
        return None
    edge = unit(dirs[1] - dirs[0])
    if abs(float(np.dot(edge, body))) > math.cos(math.radians(MIN_CUT_DEG)):
        return None
    return out


def _cut_name(direction: np.ndarray, stroke_dir: np.ndarray) -> str:
    """A cut along `direction`: perpendicular if that's what it is, else vertical/horizontal/angle."""
    d = direction / (np.hypot(*direction) or 1)
    t = stroke_dir / (np.hypot(*stroke_dir) or 1)
    # Nearly along the stroke (an outline edge beside a foot, not under it): no cut can run that way.
    if abs(np.dot(d, t)) > math.cos(math.radians(MIN_CUT_DEG)):
        return "perpendicular"
    # A level or upright edge (a baseline, a cap line) is named as such before "square": the axis bends
    # into a foot, so square to its last bit (upright, at a W's foot) isn't square to the finished
    # straight stroke (slanted), and the foot would come out slanted instead of on the baseline.
    ang = math.degrees(math.atan2(d[1], d[0])) % 180
    if abs(ang - 90) < CUT_SNAP_DEG:
        return "vertical"
    if ang < CUT_SNAP_DEG or ang > 180 - CUT_SNAP_DEG:
        return "horizontal"
    if abs(np.dot(d, t)) < math.sin(math.radians(CUT_SNAP_DEG)):
        return "perpendicular"
    return f"angle:{ang:.1f}"


STUB_LINK_HALVES = 2.6     # a piece between two junctions no longer than this many half widths is a stub
STUB_TURN_MAX_DEG = 80.0   # ...joined on to a loop turning at most this much at each end: an 8's bowls
                           # turn 45-76 deg into their waist; a narrow B's middle bar meets its stem at 87-93


def _stub_links(pieces) -> list[tuple[tuple[int, bool], tuple[int, bool]]]:
    """A short piece between two junctions (an 8's waist) can't be a stroke of its own: at both ends
    it joins the one stroke that runs between the same two junctions with the least turn in all. Lato
    Black's 8 turned 56 deg into its lower bowl, past CORNER_TURN_DEG (Lato Bold's: 45): the waist was
    left a 3 mm stub, the bowl's ends were cut against the upper bowl's slant and two wedges stayed bare.
    Returns pairs of (piece, at its start) ends to link."""
    out = []
    for i, p in enumerate(pieces):
        if p.closed or p.start is None or p.end is None or p.start == p.end:
            continue
        if _length(p.pts, range(len(p.pts))) > STUB_LINK_HALVES * p.half_width():
            continue
        best, n = None, 0
        for k, q in enumerate(pieces):
            if k == i or q.closed or q.start is None or q.end is None or q.start == q.end:
                continue
            if {q.start, q.end} != {p.start, p.end}:
                continue
            sa = q.start == p.start          # q's end at the stub's start
            t0 = _turn(p.direction_from(True), q.direction_from(sa))
            t1 = _turn(p.direction_from(False), q.direction_from(not sa))
            n += 1
            if max(t0, t1) <= STUB_TURN_MAX_DEG and (best is None or t0 + t1 < best[0]):
                best = (t0 + t1, k, sa)
        # a waist: two loops (an 8's bowls) run between the stub's junctions, and nothing else meets
        # there. An R's leg junction has one (its bowl), and its stub stays a stroke.
        degree = sum((q.start in (p.start, p.end)) + (q.end in (p.start, p.end)) for q in pieces if not q.closed)
        if best is not None and n == 2 and degree == 6:
            _, k, sa = best
            out += [((i, True), (k, sa)), ((i, False), (k, not sa))]
    return out


def _turn(d1, d2) -> float:
    """How sharply a path turns going in along -d1 and out along d2 (0 = straight on)."""
    c = float(np.clip(np.dot(-d1, d2), -1, 1))
    return math.degrees(math.acos(c))


def _near_end(piece: _Piece, at_start: bool, radii: float = 4.0) -> np.ndarray:
    """The first few half widths of a piece's centreline, from its end at a junction."""
    pts = piece.pts if at_start else piece.pts[::-1]
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    k = int(np.searchsorted(s, radii * piece.half_width())) + 1
    return pts[: max(2, k)]


def _clear_part(piece: _Piece, at_start: bool) -> np.ndarray:
    """A piece's centreline from just clear of its junction (1.5 junction radii) on for a few half
    widths, starting at the junction side."""
    pts = piece.pts if at_start else piece.pts[::-1]
    r0 = float((piece.r if at_start else piece.r[::-1])[0])
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    lo = min(1.5 * r0, s[-1] * 0.5)
    sel = (s >= lo) & (s <= lo + 6 * piece.half_width())
    if sel.sum() < 2:
        return _near_end(piece, at_start)
    return pts[sel]


def _side_half(pts: np.ndarray, away: np.ndarray, boundary) -> float:
    """A through stroke's half width, measured across it on the side away from the strokes that butt
    against it (that side's edge is clean: an E's stem's back, not the corners where its arms join)."""
    if len(pts) < 2:
        return float("nan")
    t = pts[-1] - pts[0]
    t = t / (np.hypot(*t) or 1)
    nrm = np.array([-t[1], t[0]])
    if np.dot(nrm, away) < 0:
        nrm = -nrm
    d = []
    for q in pts[:: max(1, len(pts) // 15)]:
        hit = _ray_to_outline(boundary, q, nrm)
        if hit is not None:
            d.append(hit[0])
    return float(np.median(d)) if d else float("nan")


def _side_half_at(tp: np.ndarray, junction: np.ndarray, r0: float, away: np.ndarray, boundary) -> float:
    """The through stroke's half width at a junction, from its straightened line there to its back
    (the side away from the butting strokes): rays across the line, within half the junction's
    radius of the junction."""
    from shapely.geometry import LineString, Point

    line = LineString(tp)
    at = line.project(Point(junction))
    pts = np.array([line.interpolate(at + k * 0.25 * r0).coords[0] for k in (-2, -1, 0, 1, 2)])
    a = np.array(line.interpolate(max(0.0, at - r0)).coords[0])
    b = np.array(line.interpolate(at + r0).coords[0])
    t = b - a
    t = t / (np.hypot(*t) or 1)
    nrm = np.array([-t[1], t[0]])
    if np.dot(nrm, away) < 0:
        nrm = -nrm
    d = [h[0] for h in (_ray_to_outline(boundary, q, nrm) for q in pts) if h is not None]
    return float(np.median(d)) if d else float("nan")


def _clear_half(piece: _Piece, at_start: bool, boundary) -> float:
    """A through stroke's half width beside a junction: its centreline's distance to the outline,
    measured clear of the junction (whose circle is wider: an M's stem where the diagonal joins). A
    butting stroke is cut back to this, so it reaches the stroke's real edge."""
    from shapely.geometry import Point

    pts = piece.pts if at_start else piece.pts[::-1]
    r = piece.r if at_start else piece.r[::-1]
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    clear = s > 1.5 * float(r[0])
    near = clear & (s < 1.5 * float(r[0]) + 6 * piece.half_width())
    sel = pts[near] if near.sum() >= 2 else pts[clear] if clear.sum() >= 2 else pts
    d = [boundary.distance(Point(q)) for q in sel[:: max(1, len(sel) // 15)]]
    return float(np.median(d)) if d else piece.half_width()


def _trim_to(pts: np.ndarray, through_pts: np.ndarray, half: float) -> np.ndarray:
    """Cut a polyline (starting at the junction) back to where it's `half` clear of the through
    stroke's centreline."""
    from shapely.geometry import LineString, Point

    line = LineString(through_pts)
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    for i, p in enumerate(pts):
        if line.distance(Point(p)) >= half:
            if i == 0:
                return pts
            # between i-1 and i
            lo, hi = s[i - 1], s[i]
            for _ in range(30):
                mid = (lo + hi) / 2
                q = np.array([np.interp(mid, s, pts[:, 0]), np.interp(mid, s, pts[:, 1])])
                if line.distance(Point(q)) >= half:
                    hi = mid
                else:
                    lo = mid
            q = np.array([np.interp(hi, s, pts[:, 0]), np.interp(hi, s, pts[:, 1])])
            return np.vstack([q, pts[i:]])
    return pts[-2:]


def _resample_line(pts: np.ndarray, step: float) -> np.ndarray:
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    n = max(2, int(s[-1] / step) + 1)
    t = np.linspace(0, s[-1], n)
    return np.column_stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])])


def _corners(pts: np.ndarray, half: float, closed: bool, step: float) -> list[int]:
    """Indices where the centreline turns sharper than CORNER_TURN_DEG, one per bend."""
    n = len(pts)
    # Measured over about a half width each side: a corner turns within that, a curve doesn't.
    k = max(2, int(max(CORNER_SPAN * half, 1.0) / step))
    if n < 2 * k + 1:
        return []
    turn = np.zeros(n)
    for i in range(n):
        if not closed and (i < k or i >= n - k):
            continue
        a, b, c = pts[(i - k) % n], pts[i], pts[(i + k) % n]
        d1, d2 = b - a, c - b
        cosv = np.dot(d1, d2) / ((np.hypot(*d1) * np.hypot(*d2)) or 1)
        turn[i] = math.degrees(math.acos(float(np.clip(cosv, -1, 1))))
    hot = turn > CORNER_TURN_DEG
    out, i = [], 0
    if closed and hot.all():
        return []
    start = int(np.argmin(hot)) if closed else 0     # begin outside a bend so none is cut in two
    idx = [(start + j) % n for j in range(n)] if closed else list(range(n))
    run: list[int] = []
    for j in idx + [None]:
        if j is not None and hot[j]:
            run.append(j)
            continue
        if run:
            out.append(max(run, key=lambda q: turn[q]))
            run = []
    return sorted(out)


V_FOOT_DEG = 50.0         # two legs leaving a corner within this angle of each other meet in a V
V_FOOT_REACH = 2.5        # ...a foot, if the outline past the tip is within this many half widths
V_FOOT_CLEAR = 1.0        # each leg's centreline is taken from this many half widths clear of the tip
V_FOOT_MIN_SLANT = 8.0    # each leg is at least this many degrees off square to the edge it ends on
V_FOOT_SLANT_DIFF = 15.0  # ...and the two legs' slants differ by at most this
V_FOOT_OPEN_MM = 1.0      # past the tip, no ink again within this
V_FOOT_LEG = 5.0         # leg directions for the V test are measured this many half widths up the legs


def _far_dir(piece: "_Piece", at_start: bool, halves: float) -> np.ndarray:
    """Direction from a piece's end at a junction to its centreline `halves` half widths along."""
    pts = piece.pts if at_start else piece.pts[::-1]
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    k = int(min(len(pts) - 1, max(1, np.searchsorted(s, halves * piece.half_width()))))
    d = pts[k] - pts[0]
    return d / (np.hypot(*d) or 1)


def _clear_tip(piece: "_Piece", at_start: bool, reach: float) -> None:
    """Drop a piece's centreline within `reach` of its end at a junction (where it bends into the
    tip), so its free end runs straight on to the outline."""
    end = piece.pts[0] if at_start else piece.pts[-1]
    keep = np.hypot(*(piece.pts - end).T) > reach
    if keep.sum() >= 2:
        piece.pts, piece.r = piece.pts[keep], piece.r[keep]
    if at_start:
        piece.start = None
    else:
        piece.end = None


def _v_foot(corner, na, nb, half: float, boundary) -> bool:
    """Whether a corner is a V's foot: its two legs leave it in nearly the same direction, and the
    letter's edge is just past the tip (not a joint inside the letter)."""
    if float(np.degrees(np.arccos(np.clip(np.dot(na, nb), -1, 1)))) > V_FOOT_DEG:
        return False
    out = -(na + nb)
    out = out / (np.hypot(*out) or 1)
    hit = _ray_to_outline(boundary, np.asarray(corner, float), out)
    if hit is None or hit[0] > V_FOOT_REACH * max(half, 0.3):
        return False
    # Open fabric past the tip: a slit cut in the art (an h's arm cut free of its stem) has ink again
    # just beyond it, and isn't a foot.
    again = _ray_to_outline(boundary, hit[1] + out * 0.01, out)
    if again is not None and again[0] < V_FOOT_OPEN_MM:
        return False
    # Both legs slant into the edge they end on. A leg running square into it (an N's stem at its
    # top corner) is the natural stroke to run through, and the other butts against it as before.
    from shapely.geometry import Point

    g = min(getattr(boundary, "geoms", [boundary]), key=lambda ln: ln.distance(Point(hit[1])))
    at = g.project(Point(hit[1]))
    p0, p1 = np.array(g.interpolate(max(0.0, at - 0.3)).coords[0]), np.array(g.interpolate(at + 0.3).coords[0])
    edge = p1 - p0
    if np.hypot(*edge) < 1e-6:
        return True
    edge = edge / np.hypot(*edge)
    # Both legs slant into it, and by about as much: a V. A leg nearly square to it beside a steep
    # one is an upright stroke with a diagonal joining it (an N's stem at its corner).
    slants = [float(np.degrees(np.arcsin(np.clip(abs(np.dot(leg, edge)), 0, 1)))) for leg in (na, nb)]
    return min(slants) >= V_FOOT_MIN_SLANT and abs(slants[0] - slants[1]) <= V_FOOT_SLANT_DIFF


MITRE_MAX_DEG = 75.0      # legs meeting at a sharper angle than this, in a point their outer edges make,
                          # meet on a mitre: each cut along the line from the inside corner to the point
MITRE_REACH = 4.0         # ...the point and the inside corner each within this many half widths
MITRE_EDGE_DEG = 15.0     # ...and the outline leaving the point runs along each leg to within this (a
                          # square corner with a curve leaving it, a D's stem foot, is ~20 off on the curve)
MITRE_POINT_SLACK = 20.0  # ...and the point is at most this much blunter than the legs' angle


def _outline_dirs_at(boundary, q: np.ndarray, reach: float) -> tuple[np.ndarray, np.ndarray] | None:
    """The outline's two directions away from its point nearest q, each over `reach` mm."""
    from shapely.geometry import Point

    g = min(getattr(boundary, "geoms", [boundary]), key=lambda ln: ln.distance(Point(q)))
    at, L = g.project(Point(q)), g.length
    p = np.array(g.interpolate(at).coords[0])
    out = []
    for sgn in (-1, 1):
        x = at + sgn * reach
        if g.is_ring or np.allclose(g.coords[0], g.coords[-1]):
            x %= L
        x = min(max(x, 0.0), L)
        v = np.array(g.interpolate(x).coords[0]) - p
        if np.hypot(*v) < 1e-6:
            return None
        out.append(v / np.hypot(*v))
    return out[0], out[1]


def _mitre(corner: np.ndarray, va: np.ndarray, vb: np.ndarray, half: float, boundary):
    """The mitre line (inside corner, point) where two legs leaving `corner` along va and vb meet in a
    point made by their own outer edges (a Celtic knot's lobe tip), or None. Not a V's foot (narrower,
    handled before), nor a corner whose outline is square across one leg (an N's stem top: the stem
    runs through and the diagonal butts)."""
    ang = float(np.degrees(np.arccos(np.clip(np.dot(va, vb), -1, 1))))
    if ang > MITRE_MAX_DEG or boundary is None:
        return None
    out = -(va + vb)
    if np.hypot(*out) < 1e-6:
        return None
    out = out / np.hypot(*out)
    reach = MITRE_REACH * max(half, 0.3)
    inner = _ray_to_outline(boundary, corner, -out)
    if inner is None or inner[0] > reach:
        return None
    # The point: the outline's furthest reach outwards near the corner. (The bisector from the axis's
    # corner can miss it and land on one leg's outer edge when the legs differ in width or curve.)
    from shapely.geometry import Point

    near = boundary.intersection(Point(corner).buffer(reach))
    pts = []
    for g in getattr(near, "geoms", [near]):
        if g.is_empty or g.length <= 0:
            continue
        n = max(2, int(g.length / 0.05))
        pts += [g.interpolate(i / (n - 1), normalized=True).coords[0] for i in range(n)]
    if not pts:
        return None
    pts = np.array(pts)
    tip = pts[int(np.argmax((pts - corner) @ out))]
    if float(np.dot(tip - corner, out)) <= 0:
        return None
    dirs = _outline_dirs_at(boundary, tip, max(half, 0.5))
    if dirs is None:
        return None
    # A point, about as sharp as the legs meet: not a square corner (a 1's flag at its flat top, a D's
    # stem foot), where the upright runs through as before.
    if float(np.degrees(np.arccos(np.clip(np.dot(*dirs), -1, 1)))) > ang + MITRE_POINT_SLACK:
        return None
    lim = math.cos(math.radians(MITRE_EDGE_DEG))
    # each direction away from the point runs along one leg (the legs point away from it too)
    if not ((np.dot(dirs[0], va) >= lim and np.dot(dirs[1], vb) >= lim)
            or (np.dot(dirs[0], vb) >= lim and np.dot(dirs[1], va) >= lim)):
        return None
    return inner[1], tip


def _onto_mitre(body: np.ndarray, corner: np.ndarray, half: float, boundary, a: np.ndarray, b: np.ndarray,
                step: float) -> np.ndarray | None:
    """A leg's centreline (ending at the corner) run on to the middle of the mitre line a-b: the leg's
    two edges end at its two ends (the inside corner and the point), so its middle ends halfway. The
    axis's bend into the corner is dropped first."""
    from shapely.geometry import Point

    bend = max(half, 1.5 * boundary.distance(Point(corner)))
    keep = np.hypot(*(body - corner).T) > bend
    if keep.sum() < 2:
        return None
    body = body[keep]
    mid = (a + b) / 2
    m = (b - a) / (np.hypot(*(b - a)) or 1)
    nrm = np.array([-m[1], m[0]])
    # Near the mitre, the body stays on its own side of it (further on, a leg can come back round
    # past the line: a D's bowl).
    side = (body - mid) @ nrm
    close = np.hypot(*(body - mid).T) <= np.hypot(*(b - a)) + 2 * half
    own = np.sign(np.median(side[close])) if close.any() else np.sign(np.median(side))
    body = body[~close | (side * own > 0.5 * half)]
    if len(body) < 2:
        return None
    t = body[-1] - body[max(0, len(body) - 1 - int(2 * half / step))]
    if np.dot(mid - body[-1], t) <= 0:
        return None
    return np.vstack([body, mid])


BAR_LEVEL_DEG = CUT_SNAP_DEG  # a corner's side this close to horizontal is a level bar
LEG_SLANT_DEG = 18.0          # ...and one at least this far off both horizontal and vertical a slanted leg
                              # (a 7's steep leg is 23 off upright; an L's stem is upright)
BAR_EDGE_HALVES = 1.5         # ...the bar's axis within this many half widths of the baseline or cap line


def _axis_off_deg(d: np.ndarray) -> tuple[float, float]:
    """How far a direction is off horizontal, and off vertical (degrees)."""
    ang = math.degrees(math.atan2(abs(float(d[1])), abs(float(d[0]))))
    return ang, 90.0 - ang


def _level(d: np.ndarray) -> bool:
    return _axis_off_deg(d)[0] <= BAR_LEVEL_DEG


def _slanted(d: np.ndarray) -> bool:
    return min(_axis_off_deg(d)) >= LEG_SLANT_DEG


CORNER_SIDE_STRAIGHT = 0.15   # a bar or leg at a corner is straight: its axis within this share of the half
                              # width of its chord, from one to V_FOOT_LEG half widths from the corner


def _straight_side(body: np.ndarray, corner: np.ndarray, half: float) -> bool:
    """Whether a corner's side (body: its centreline, the corner end last) runs straight away from the
    corner, clear of the axis's bend into it. A bend inside a curved stroke (a 3's or 8's waist) isn't
    a bar meeting a leg."""
    d = np.hypot(*(body - corner).T)
    part = body[(d >= half) & (d <= V_FOOT_LEG * half)]
    if len(part) < 3:
        return False
    chord = part[0] - part[-1]
    L = float(np.hypot(*chord))
    if L < 2 * half:
        return False
    dev = np.abs((part[:, 0] - part[-1, 0]) * chord[1] - (part[:, 1] - part[-1, 1]) * chord[0]) / L
    return float(dev.max()) <= CORNER_SIDE_STRAIGHT * half


def _split_corners(strokes_d: list[dict], step: float = 0.25, boundary=None, keep_bends=(),
                   overlap_feet: bool = False) -> list[dict]:
    """Cut strokes in two at sharp corners. The more upright side runs on through the corner to the
    outline; the other butts against it."""
    out = []
    for sd in strokes_d:
        pts = _resample_line(sd["pts"], step)
        half, closed = sd["half"], sd["closed"]
        cs = _corners(pts, half, closed, step)
        if keep_bends:
            # A bend through a junction whose branches were paired by their smooth outline is a curve,
            # not a corner (a dancer's arm round her head into her raised arm).
            cs = [i for i in cs if not any(np.hypot(*(pts[i] - q)) <= BEND_CORNER_REACH * max(rq, half)
                                           for q, rq in keep_bends)]
        if not cs:
            out.append({**sd, "pts": pts})
            continue
        if closed:
            pts = np.vstack([pts[cs[0]:], pts[1:cs[0] + 1]]) if cs[0] else pts
            shift = cs[0]
            cs = [0] + [(c - shift) % (len(pts) - 1) for c in cs[1:]] + [len(pts) - 1]
        else:
            cs = [0] + cs + [len(pts) - 1]
        segs = [pts[a:b + 1] for a, b in zip(cs, cs[1:]) if b - a >= 2]
        ends = [[None, None] for _ in segs]
        square = [[False, False] for _ in segs]   # a bar's free end cut square across it
        if not closed:
            ends[0][0], ends[-1][1] = sd["ends"][0], sd["ends"][1]
            square[0][0], square[-1][1] = sd.get("square", [False, False])
        k = max(2, int(max(2 * half, 1.5) / step))
        pairs = list(zip(range(len(segs) - 1), range(1, len(segs))))
        if closed and len(segs) >= 2:
            pairs.append((len(segs) - 1, 0))
        for a, b in pairs:
            da = segs[a][max(0, len(segs[a]) - 1 - k)] - segs[a][-1]   # from the corner back into a
            db = segs[b][min(k, len(segs[b]) - 1)] - segs[b][0]        # from the corner into b
            na, nb = da / (np.hypot(*da) or 1), db / (np.hypot(*db) or 1)
            # for the V test, the legs' directions further up (each bends into the tip)
            k4 = max(k, int(V_FOOT_LEG * half / step))
            va = segs[a][max(0, len(segs[a]) - 1 - k4)] - segs[a][-1]
            vb = segs[b][min(k4, len(segs[b]) - 1)] - segs[b][0]
            va, vb = va / (np.hypot(*va) or 1), vb / (np.hypot(*vb) or 1)
            if overlap_feet and boundary is not None and _v_foot(segs[a][-1], va, vb, half, boundary):
                # Two legs meeting in a sharp V at the letter's edge (a W's or V's foot): neither butts
                # against the other in so narrow a tip. Both run on to the outline, each cut along it,
                # and they overlap at the tip.
                corner = segs[a][-1]
                for si, at_end in ((a, True), (b, False)):
                    body = segs[si] if at_end else segs[si][::-1]
                    clear = np.hypot(*(body - corner).T) > V_FOOT_CLEAR * half
                    if clear.sum() >= 2:
                        body = body[clear]
                    segs[si] = body if at_end else body[::-1]
                continue
            mitre = _mitre(segs[a][-1], va, vb, half, boundary)
            if mitre is not None:
                # Two legs meeting in a point their outer edges make (a knot's lobe tip): one running on
                # through to the point leaves a long wedge past the inside corner, sewn over by the other
                # leg's end too. Each stops on the line from the inside corner to the point instead.
                corner = segs[a][-1]
                ma, mb = mitre
                ext = (mb - ma) / (np.hypot(*(mb - ma)) or 1)
                line = np.vstack([ma - ext * 3 * half, mb + ext * 3 * half])
                new = []
                for si, at_end in ((a, True), (b, False)):
                    body = segs[si] if at_end else segs[si][::-1]
                    new.append(_onto_mitre(body, corner, half, boundary, ma, mb, step))
                if all(x is not None for x in new):
                    segs[a], segs[b] = new[0], new[1][::-1]
                    target = {"pts": line, "half": 0.0, "mitre": True}
                    ends[a][1] = target
                    ends[b][0] = target
                    continue
            a_through = abs(na[1]) >= abs(nb[1])
            # A level bar and a slanted leg (a 2's or Z's foot, a 7's top): the bar runs on through to
            # the outline, flat along the letter's edge, and the leg butts onto it. The leg run through
            # ends on a slant across the corner, stepped against the bar's end.
            level = [_level(v_) for v_ in (va, vb)]
            slant = [_slanted(v_) for v_ in (va, vb)]
            if (level[0] and slant[1]) or (level[1] and slant[0]):
                straight = (_straight_side(segs[a], segs[a][-1], half)
                            and _straight_side(segs[b][::-1], segs[b][0], half))
                # ...and the bar lies along the letter's baseline or cap line (not a K's arm stub, nor
                # a 4's crossbar inside the letter)
                bar = segs[a] if level[0] else segs[b]
                on_edge = boundary is not None and (
                    min(abs(float(np.median(bar[:, 1])) - y) for y in (boundary.bounds[1], boundary.bounds[3]))
                    <= BAR_EDGE_HALVES * half)
                if not (straight and on_edge):
                    level = slant = [False, False]
            if level[0] and slant[1]:
                a_through = True
                square[a][1] = True     # ...and ends square across, on the letter's side
            elif level[1] and slant[0]:
                a_through = False
                square[b][0] = True
            if a_through:
                through, tdir, butt = (a, 1), -na, (b, 0)
            else:
                through, tdir, butt = (b, 0), -nb, (a, 1)
            tp = segs[through[0]]
            corner = tp[-1] if through[1] == 1 else tp[0]
            body = tp if through[1] == 1 else tp[::-1]
            # This side's own half width (the joined stroke's mixes both sides): its distance to the
            # outline, away from the corner.
            own, bend = half, half
            if boundary is not None:
                from shapely.geometry import Point
                # The axis bends into the corner over about the corner circle's radius.
                bend = max(half, 1.5 * boundary.distance(Point(corner)))
                far = body[np.hypot(*(body - corner).T) > max(2 * half, bend)]
                if len(far):
                    own = float(np.median([boundary.distance(Point(q)) for q in far[:: max(1, len(far) // 20)]]))
            # Drop that bend and run straight on, along the line of the part just clear of it.
            dist = np.hypot(*(body - corner).T)
            keep = dist > bend
            if keep.sum() >= 2:
                body = body[keep]
                # Its line: the main direction of the stretch before the bend (a junction further
                # along pulls the axis aside for a short way: an E's stem at its middle arm).
                near = body[-min(len(body), max(3, int(12 * own / step))):]
                u, sv, vt = np.linalg.svd(near - near.mean(axis=0), full_matrices=False)
                if sv[1] / np.sqrt(len(near)) < 0.1 * own:       # straight: its fitted line
                    tdir = vt[0] if np.dot(vt[0], near[-1] - near[0]) >= 0 else -vt[0]
                else:                                             # curved: its last stretch's chord
                    near = body[-min(len(body), max(2, int(6 * own / step))):]
                    tdir = near[-1] - near[0]
                    tdir = tdir / (np.hypot(*tdir) or 1)
            else:
                keep = dist > half
                body = body[keep] if keep.sum() >= 2 else body
            ext = np.vstack([body, body[-1] + tdir * (bend + 3 * own)])
            target = {"pts": ext, "half": own}
            segs[through[0]] = body if through[1] == 1 else body[::-1]
            ends[butt[0]][butt[1]] = target
        for s_, e_, q_ in zip(segs, ends, square):
            out.append({"pts": s_, "closed": False, "ends": e_, "half": half, "square": q_})
    return out


def islands(contours: list[np.ndarray]) -> list[list[np.ndarray]]:
    """The letter's separate pieces (an i's stem and dot), each as its outer contour and holes."""
    from .columns import Letter

    allp = np.vstack(contours)
    box = (allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max())
    glyph = Letter("?", [], contours, box).glyph()
    out = []
    for g in sorted(getattr(glyph, "geoms", [glyph]), key=lambda g: -g.area):
        if g.is_empty or g.area <= 0:
            continue
        out.append([np.array(r.coords[:-1]) for r in [g.exterior, *g.interiors]])
    return out


def letter_strokes(contours: list[np.ndarray], cap_mm: float, overlap_feet: bool = False) -> list[Stroke]:
    """Proposed strokes for one letter (contours in mm, y up), in sewing order, in the letter's ink
    box (u, v from 0 to 1). Each separate piece of the letter (an i's dot) gets its own strokes.
    overlap_feet (flat work): legs meeting in a V at the letter's edge both run on to it and overlap at
    the tip (a W's feet), rather than one butting against the other; off for puff, where it'd perforate twice."""
    # The thresholds here (how far a tip may veer, a mitre's reach, a stub's length) are in mm, tuned
    # on strokes at least PLAN_MIN_STROKE_MM wide (bold letters about 20 mm tall). Thinner ones (small
    # lettering) are planned scaled up to that width: the strokes come back in u, v of the box, which
    # scaling doesn't change. (At 5.75 mm, half the benchmark's letters failed before.)
    w = stroke_width_mm(contours)
    if 0 < w < PLAN_MIN_STROKE_MM:
        k = PLAN_MIN_STROKE_MM / w
        contours = [np.asarray(c, float) * k for c in contours]
        cap_mm *= k
    allp = np.vstack(contours)
    box = (allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max())
    strokes = []
    for piece in islands(contours):
        try:
            got = _piece_strokes(piece, cap_mm, box, overlap_feet)
        except (ValueError, IndexError, KeyError):
            got = []
        strokes += got or _blob_stroke(piece, box)
    return order_strokes(strokes)


def _blob_stroke(contours: list[np.ndarray], box) -> list[Stroke]:
    """One straight stroke across a piece too small or round for a centreline (a dot): along its
    longest direction, edge to edge, so its column's edges follow the outline."""
    from shapely.geometry import LineString, Polygon

    poly = Polygon(contours[0])
    if not poly.is_valid:
        poly = poly.buffer(0)
    # A sliver where an outline touches itself isn't a piece to sew.
    if poly.is_empty or poly.buffer(-BLOB_MIN_MM / 2).is_empty:
        return []
    rect = np.array(poly.minimum_rotated_rectangle.exterior.coords[:4])
    e1, e2 = rect[1] - rect[0], rect[2] - rect[1]
    long_, short = (e1, e2) if np.hypot(*e1) >= np.hypot(*e2) else (e2, e1)
    c = np.array(poly.centroid.coords[0])
    u = long_ / (np.hypot(*long_) or 1)
    far = 2 * np.hypot(*long_)
    hit = LineString([tuple(c - u * far), tuple(c + u * far)]).intersection(poly.exterior)
    pts = np.array([q for g in getattr(hit, "geoms", [hit]) for q in getattr(g, "coords", [])])
    if len(pts) < 2:
        return []
    t = (pts - c) @ u
    a, b = pts[np.argmin(t)], pts[np.argmax(t)]
    x0, y0, x1, y1 = box

    def uv(p):
        return ((p[0] - x0) / (x1 - x0), (p[1] - y0) / (y1 - y0))

    return [Stroke([uv(a), uv(b)])]


def _piece_strokes(contours: list[np.ndarray], cap_mm: float, box, overlap_feet: bool = False) -> list[Stroke]:
    """Strokes for one connected piece, in `box`'s u, v units, not yet in sewing order."""
    from shapely.geometry import MultiLineString

    from .columns import Letter

    glyph = Letter("?", [], contours, box).glyph()
    boundary = MultiLineString([np.vstack([c, c[:1]]) for c in contours])
    v, radius, edges = medial_axis(contours, glyph)
    edges = prune(v, radius, edges)
    adj = _adjacency(edges)
    chains = _branches(adj)

    # Junctions closer together than their circles are one junction.
    junction = {n for n in adj if len(adj[n]) >= 3}
    parent = {n: n for n in junction}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for ch in chains:
        a, b = ch[0], ch[-1]
        if a in junction and b in junction and a != b and _length(v, ch) < 0.8 * max(radius[a], radius[b]):
            parent[find(a)] = find(b)
    pieces: list[_Piece] = []
    for ch in chains:
        a, b = ch[0], ch[-1]
        ja = find(a) if a in junction else None
        jb = find(b) if b in junction else None
        if ja is not None and ja == jb and a != b and _length(v, ch) < 0.8 * max(radius[a], radius[b]):
            continue                      # an inner link of a merged junction (not a loop through it: 4, Q)
        closed = a == b and a not in junction
        pts = _smooth(v[ch])
        pieces.append(_Piece(pts, ja, jb, radius[ch], closed=closed))

    # At each junction, pair up the ends that run on straightest; the rest butt.
    ends: dict[int, list[tuple[int, bool]]] = {}
    for i, p in enumerate(pieces):
        if p.closed:
            continue
        if p.start is not None:
            ends.setdefault(p.start, []).append((i, True))
        if p.end is not None:
            ends.setdefault(p.end, []).append((i, False))
    links: dict[tuple[int, bool], tuple[int, bool]] = {}
    butts: dict[tuple[int, bool], tuple[int, bool]] = {}   # butting end -> an end of the stroke it meets
    corners = _concave_corners(contours, glyph)
    smooth_at: list = []          # junctions resolved by smooth pairing: their bends stay curves
    dropped: set[int] = set()     # stub pieces into a V's foot, covered by both legs
    stub_links = _stub_links(pieces)
    for j, es in ends.items():
        dirs = {e: pieces[e[0]].direction_from(e[1]) for e in es}
        # A V's foot at a junction (two legs, and a short stub on down into a flat foot): both legs
        # run on to the outline, each cut along it, overlapping at the tip; the stub is dropped.
        legs = [e for e in es if _length(pieces[e[0]].pts, range(len(pieces[e[0]].pts)))
                > V_FOOT_LEG * pieces[e[0]].half_width()]
        if overlap_feet and len(legs) == 2 and len(es) >= 3 and legs[0][0] != legs[1][0]:
            d0 = _far_dir(pieces[legs[0][0]], legs[0][1], V_FOOT_LEG)
            d1 = _far_dir(pieces[legs[1][0]], legs[1][1], V_FOOT_LEG)
            if _v_foot(v[j], d0, d1, float(np.median([pieces[e[0]].half_width() for e in legs])), boundary):
                for e in legs:
                    _clear_tip(pieces[e[0]], e[1], V_FOOT_CLEAR * float(radius[j]) + pieces[e[0]].half_width())
                dropped |= {e[0] for e in es if e not in legs}
                continue
        # Branches whose outline between them runs on smoothly are one stroke; a sharp inside corner
        # between two branches separates them (a dancer: left arm round her head into the raised arm,
        # leg round into the right arm, the two meeting between the corners under the arms).
        smooth = _smooth_pairs(es, dirs, v[j], float(radius[j]), corners,
                               {e: _near_end(pieces[e[0]], e[1], SECTOR_REACH * 2) for e in es})
        if smooth:
            smooth_at.append((np.array(v[j]), float(radius[j])))
            for a_, b_ in smooth:
                links[a_], links[b_] = b_, a_
        # Otherwise one pair runs on through, the straightest; every other end butts against it (an
        # X's second diagonal is two strokes, so the crossing is covered once).
        best = None if smooth else min(((a, b) for ai, a in enumerate(es) for b in es[ai + 1:]
                                        if a[0] != b[0] or len(es) == 2),
                                       key=lambda ab: _turn(dirs[ab[0]], dirs[ab[1]]), default=None)
        forced = None if smooth else next(((a, b) for a, b in stub_links if a in es and b in es), None)
        if forced is not None:
            links[forced[0]], links[forced[1]] = forced[1], forced[0]
        elif best is not None and _turn(dirs[best[0]], dirs[best[1]]) <= CORNER_TURN_DEG:
            links[best[0]], links[best[1]] = best[1], best[0]
        through = [e for e in es if e in links]
        if not through:
            # A corner: the most upright end runs through.
            up = max(es, key=lambda e: abs(dirs[e][1]))
            through = [up]
        # What a butting end meets: the through stroke's centreline near this junction only (a P's
        # bowl butts against the stem it's part of).
        # The axis bends towards a junction (its circle touches the butting stroke too), so the
        # through stroke's line there is taken from its centreline clear of the junction, run
        # straight across it.
        # A short through piece (a stub up into a joint: the top of an M's stem) lies in the joint's
        # wide part, so its line and width are left out when the other piece is long.
        ref = list(through)
        lens = [_length(pieces[e[0]].pts, range(len(pieces[e[0]].pts))) for e in ref]
        # ...but a stub joined on at both ends (an 8's waist) is the through stroke's line here: the
        # stroke turns along it, and the bowl's line run on straight cut the other bowl's ends on a slant
        if len(ref) > 1 and forced is None:
            long_ = [k for k in range(len(ref))
                     if lens[k] >= 1.5 * float(pieces[ref[k][0]].r[0 if ref[k][1] else -1])
                     + 3 * pieces[ref[k][0]].half_width()]
            if len(long_) == 1:
                ref = [ref[long_[0]]]
            elif not long_:
                ref = [ref[int(np.argmax(lens))]]
        line = [_clear_part(pieces[e[0]], e[1]) for e in ref]
        half_t = float(np.mean([_clear_half(pieces[e[0]], e[1], boundary) for e in ref]))
        # Near the junction the outline's inside corners are closer than the stroke's edge, so the
        # clear half width can come out short (and a butting bar reaches into the leg), and a tapering
        # stroke (an A's legs) is wider here than further along. Its back, away from the butting
        # strokes, is its real edge: measured there, next to the junction, on each side of it.
        if len(line) > 1:
            tp = np.vstack([line[0][::-1], line[1]])
        else:   # the through end runs on past the junction to the outline
            d0 = line[0][min(len(line[0]) - 1, 3)] - line[0][0]
            d0 = d0 / (np.hypot(*d0) or 1)
            reach = float(np.hypot(*(line[0][0] - pieces[ref[0][0]].pts[0 if ref[0][1] else -1])))
            tp = np.vstack([line[0][::-1], line[0][:1] - d0 * (reach + 3 * half_t)])
        butting = [e for e in es if e not in through]
        if butting:
            away = -np.sum([dirs[e] for e in butting], axis=0)
            if np.hypot(*away) > 0.3:       # butting from one side only (not an X's two halves)
                side = np.nanmean([_side_half(ln[:SIDE_SAMPLES], away, boundary) for ln in line] + [np.nan])
                if np.isfinite(side):
                    half_t = float(np.clip(side, half_t, SIDE_HALF_MAX * half_t))
        target = {"pts": tp, "half": half_t}
        for e in es:
            if e not in through:
                butts[e] = target

    # Chains of linked pieces become strokes.
    used = set()
    strokes_pts = []        # (points, start end key, end end key, closed)
    for i, p in enumerate(pieces):
        if i in used or i in dropped:
            continue
        if p.closed:
            used.add(i)
            strokes_pts.append((p.pts, None, None, True, [i]))
            continue
        # walk back to an end that isn't linked
        cur, at_start = i, True
        visited = {i}
        while (cur, at_start) in links:
            nxt = links[(cur, at_start)]
            if nxt[0] in visited:
                break
            visited.add(nxt[0])
            cur, at_start = nxt[0], not nxt[1]
        # now walk forward from (cur, at_start)
        seq, k, s0 = [], cur, at_start
        first_key = (cur, at_start)
        visited = set()
        while True:
            visited.add(k)
            pts = pieces[k].pts if s0 else pieces[k].pts[::-1]
            seq.append(pts if not seq else pts[1:])
            far_end = (k, not s0)
            if far_end in links and links[far_end][0] not in visited:
                k, s0 = links[far_end][0], links[far_end][1]
                continue
            break
        used |= visited
        closed_loop = far_end in links and links[far_end] == first_key
        strokes_pts.append((np.vstack(seq), first_key, far_end, closed_loop, sorted(visited)))

    owner = {}
    for si, (_, _, _, _, members) in enumerate(strokes_pts):
        for m in members:
            owner[m] = si

    # Each stroke as {pts, closed, ends}; an end is None (free: runs on to the outline) or the
    # centreline and half width of the stroke it butts against.
    strokes_d = []
    for pts, k0, k1, closed, members in strokes_pts:
        half = float(np.median([pieces[m].half_width() for m in members]))
        ends_ = [None, None]
        if not closed:
            for which, key in ((0, k0), (1, k1)):
                ends_[which] = butts.get(key)
        strokes_d.append({"pts": pts, "closed": closed, "ends": ends_, "half": half})
    strokes_d = _split_corners(strokes_d, boundary=boundary, keep_bends=smooth_at, overlap_feet=overlap_feet)

    # Ends: free ends run on to the outline, butting ends are cut back to the through stroke's edge.
    out = []
    for sd in strokes_d:
        pts, closed = sd["pts"].copy(), sd["closed"]
        cuts = ["perpendicular", "perpendicular"]
        if not closed:
            for which in (0, 1):
                seg = pts if which == 0 else pts[::-1]
                target = sd["ends"][which]
                if target is not None:
                    tp, half = target["pts"], target["half"]
                    seg = _trim_to(seg, tp, half)
                    from shapely.geometry import LineString, Point
                    line = LineString(tp)
                    proj = line.project(Point(seg[0]))
                    a = np.array(line.interpolate(max(0, proj - 0.5)).coords[0])
                    b = np.array(line.interpolate(proj + 0.5).coords[0])
                    # the stroke's own direction over a half width or so (trimming can leave its first
                    # two points almost on top of each other)
                    s_ = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(seg, axis=0).T))])
                    k = int(min(len(seg) - 1, max(1, np.searchsorted(s_, max(half, 0.5)))))
                    if target.get("mitre"):
                        # a mitre runs at whatever slant the point makes: no snapping to square
                        m = (b - a) / (np.hypot(*(b - a)) or 1)
                        cuts[which] = f"angle:{math.degrees(math.atan2(m[1], m[0])) % 180:.1f}"
                    else:
                        cuts[which] = _cut_name(b - a, seg[k] - seg[0])
                else:
                    blade = False
                    if overlap_feet:      # flat work (puff's tighter coverage keeps the axis's tip)
                        straight = _straighten_free_end(seg, sd["half"], boundary)
                        blade = straight is not seg
                        seg = straight
                    d = seg[0] - seg[min(3, len(seg) - 1)]
                    d /= np.hypot(*d) or 1
                    hit = _ray_to_outline(boundary, seg[0], d)
                    if hit is not None:
                        seg = np.vstack([hit[1], seg])
                        run = _straight_run_dir(contours, hit[1], sd["half"])
                        if sd.get("square", [False, False])[which]:
                            # a level bar run on through a slanted leg's corner: square across it, not
                            # along the corner's rounding
                            cuts[which] = _cut_name(np.array([-d[1], d[0]]), d)
                        elif blade and run is None:
                            # a blade's round side: square stitches narrowing along the curve into its
                            # corner (a cut across the curve would turn them all to one slant)
                            cuts[which] = "perpendicular"
                        else:
                            edge = run if run is not None else _end_edge_dir(boundary, contours, seg[1], d, sd["half"])
                            cuts[which] = _cut_name(edge, d)
                pts = seg if which == 0 else seg[::-1]
        out.append((pts, cuts, closed))

    # Straight or curved, in box units.
    x0, y0, x1, y1 = box

    def uv(p):
        return ((p[0] - x0) / (x1 - x0), (p[1] - y0) / (y1 - y0))

    strokes = []
    for pts, cuts, closed in out:
        chord = pts[-1] - pts[0]
        n = np.hypot(*chord) or 1
        dev = np.abs(chord[0] * (pts[:, 1] - pts[0, 1]) - chord[1] * (pts[:, 0] - pts[0, 0])) / n
        # Straight if most of it is: a junction pulls the axis aside for a short way (a T's bar dips
        # into the stem by up to a few tolerances).
        tol = STRAIGHT_PCT / 100 * cap_mm
        if not closed and np.median(dev) <= tol and dev.max() <= 4 * tol:
            strokes.append(Stroke([uv(pts[0]), uv(pts[-1])], start_cut=cuts[0], end_cut=cuts[1]))
            continue
        s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        m = max(3, int(round(s[-1] / CURVE_POINT_MM)) + 1)
        t = np.linspace(0, s[-1], m, endpoint=not closed)
        res = np.column_stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])])
        strokes.append(Stroke([uv(p) for p in res], smooth=True, closed=closed,
                              start_cut=cuts[0], end_cut=cuts[1]))
    return order_strokes(strokes)


def order_strokes(strokes: list[Stroke]) -> list[Stroke]:
    """Sewing order: start at the lowest-left end, then always the stroke with an end nearest the
    last one's end, sewn from that end."""
    def flip(s: Stroke) -> Stroke:
        return Stroke(list(reversed(s.path)), s.smooth, s.closed, s.end_cut, s.start_cut)

    left = list(strokes)
    if not left:
        return []
    def key(p):
        return p[0] + p[1]
    first = min(left, key=lambda s: min(key(s.path[0]), key(s.path[-1])))
    left.remove(first)
    if key(first.path[-1]) < key(first.path[0]) and not first.closed:
        first = flip(first)
    done = [first]
    while left:
        here = np.array(done[-1].path[-1 if not done[-1].closed else 0])

        def dist(s):
            if s.closed:
                return min(np.hypot(*(np.array(p) - here)) for p in s.path)
            return min(np.hypot(*(np.array(s.path[0]) - here)), np.hypot(*(np.array(s.path[-1]) - here)))
        nxt = min(left, key=dist)
        left.remove(nxt)
        if nxt.closed:
            i = int(np.argmin([np.hypot(*(np.array(p) - here)) for p in nxt.path]))
            nxt = Stroke(nxt.path[i:] + nxt.path[:i], nxt.smooth, True)
        elif np.hypot(*(np.array(nxt.path[-1]) - here)) < np.hypot(*(np.array(nxt.path[0]) - here)):
            nxt = flip(nxt)
        done.append(nxt)
    return done
