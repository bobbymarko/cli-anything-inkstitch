"""Wrap a narrow satin through a corner as one column, the way EmbroideryStudio letters do.

Where two strokes meet end to end at a corner (an L's stem into its foot, a D's stem into its bowl,
the two legs of a W at its foot) the planner cuts them into two columns: the more upright one runs
through to the outline and the other butts against its edge, tucked under it. EmbroideryStudio's
own lettering does that only for wide strokes. A narrow stroke it sews round the corner as one
column: the stitches turn about the inside corner, and at the corner itself one stitch runs from the
inside corner to the outside one (about 1.4 times the width on a square corner). Measured on its
Avenir Black alphabet at 13 mm (strokes about 2.6 mm): the D is one ring, the E's stem runs on into
its top and bottom arms, the W's legs wrap through its feet and its middle point, and nothing is
sewn twice at those joints. The butted version sews the overlap twice and leaves a seam a knit can
pull open.

The rule here: at a sharp inside corner where exactly two column ends meet, the corner stitch
(inside corner to the outside corner, or to the middle of a flat foot) is measured. If it is at
most CORNER_WRAP_MAX_STITCH_MM (CORNER_STITCH_RATIO x WRAP_MAX_WIDTH_MM, kept under the split-satin
length so the corner is never a split or snagging stitch), the two columns are cut on that line
(a mitre) and joined into one, their rails following the outline round the corner. A wider stroke,
or a sharper corner whose corner stitch would be longer (an N's points), keeps the butt join. Two
ends of the same column meeting at a corner close it into a ring (the D).

Each wrap is kept only if the letter's check is no worse: no crossed pairs, no more bare fabric or
overhang than a small tolerance, and less overlap. All geometry is mm, y up (PlannedLetter).
"""

from __future__ import annotations

import math
import os
import sys

from cli_anything_inkstitch.trace.glyph_columns import SPLIT_SATIN_MM, PlannedColumn

WRAP_MAX_WIDTH_MM = 4.0          # strokes up to about this wide wrap round a square corner...
CORNER_STITCH_RATIO = 1.4        # ...whose corner stitch is about this times the width (sqrt 2)
CORNER_WRAP_MAX_STITCH_MM = CORNER_STITCH_RATIO * WRAP_MAX_WIDTH_MM   # 5.6: the threshold itself
assert CORNER_WRAP_MAX_STITCH_MM < SPLIT_SATIN_MM   # a corner stitch must never need splitting

CORNER_END_REACH = 2.5           # a column end within this many of its widths of the inside corner...
RING_END_REACH = 1.6             # both ends of one column within this many widths of the corner: a ring...
RING_MIN_LENGTH = 6.0            # ...if the column is at least this many widths long (else it's a short stub)
CORNER_TOUCH_MM = 0.4
JUNCTION_REACH = 2.0             # an inside corner within this many junction radii of a 3-way junction is its
TWO_STROKE_JUNCTIONS = ("corner", "v_foot")   # junction types (autostrokes) where only two strokes meet            # ...whose end part reaches the corner to within this, meets it there
CORNER_MIN_TURN_DEG = 30.0       # the two legs turn at least this much (else it's one straight stroke)
OUTER_SNAP = 0.6                 # an outside corner within this share of the width of the bisector's hit
CLIP_MARGIN_MM = 0.15            # pairs this close to the corner stitch's line are dropped (re-made round it)
CORNER_FAN_WIDTHS = 1.0          # the stitches turn over this many widths each side of the inside corner,
WRAP_PASSES = 2                  # times the corners are gone over
SHORT_FAN_SHARE = 0.45           # ...or this share of a short column's inside rail, if less
ARC_STEP_MM = 0.5                # pairs along the outline round the corner, about this far apart
BARE_TOL_PCT = 0.15              # a wrap may leave at most this much more of the letter bare (percent)
OUTSIDE_TOL_PCT = 0.1


_DEBUG = bool(os.environ.get("INKSTITCH_CORNERS_DEBUG"))


def _dbg(*a):
    if _DEBUG:
        print("[corners]", *a, file=sys.stderr)


def _np():
    import numpy as np
    return np


def _mid(c, e):
    return ((c.left[e][0] + c.right[e][0]) / 2, (c.left[e][1] + c.right[e][1]) / 2)


def _width(c, e):
    return math.dist(c.left[e], c.right[e])


def _stroke_width(c: PlannedColumn) -> float:
    """The column's width as a stroke: the median of its pairs' lengths (an end cut along the outline
    can be much shorter or longer than the stroke is wide)."""
    ws = sorted(c.widths())
    return ws[len(ws) // 2] if ws else 0.0


def _reversed(c: PlannedColumn) -> PlannedColumn:
    return PlannedColumn(list(reversed(c.left)), list(reversed(c.right)), c.closed)


def _swapped(c: PlannedColumn) -> PlannedColumn:
    return PlannedColumn(list(c.right), list(c.left), c.closed)


def _inside_left(c: PlannedColumn, p_i) -> PlannedColumn:
    """c with its inside rail (the one passing nearer the inside corner, over its corner end's part)
    as left. A butting column's end lies inside the other column, so its end points alone can't
    tell (either may be nearer the corner)."""
    from shapely.geometry import LineString, Point

    w = max(_stroke_width(c), 0.5)
    keep = [i for i in range(len(c.left)) if math.dist(_mid(c, i), p_i) <= 3 * w] or [len(c.left) - 1]
    lo, hi = max(0, min(keep) - 1), max(keep)
    rails = [c.left[lo:hi + 1], c.right[lo:hi + 1]]
    d = [Point(p_i).distance(LineString(r)) if len(r) >= 2 else math.dist(r[0], p_i) for r in rails]
    return _swapped(c) if d[1] < d[0] else c


def _leg_dir(c: PlannedColumn, w: float):
    """Unit direction from the column's last pair back along its centre, over about 2 widths."""
    np = _np()
    mids = [np.array(_mid(c, i)) for i in range(len(c.left))]
    end = mids[-1]
    for m in reversed(mids[:-1]):
        if np.hypot(*(m - end)) >= 2 * w:
            break
    v = m - end
    n = float(np.hypot(*v))
    return None if n < 1e-6 else v / n


def _rings(contours):
    from shapely.geometry import LinearRing
    return [LinearRing(c) for c in contours if len(c) >= 3]


def _arc(ring, a, b):
    """The outline from a to b along `ring`, the shorter way round, as points (a and b included)."""
    from shapely.geometry import Point

    L = ring.length
    ta, tb = ring.project(Point(a)), ring.project(Point(b))
    fwd = (tb - ta) % L
    back = (ta - tb) % L
    coords = list(ring.coords)[:-1]
    ts = [ring.project(Point(p)) for p in coords]
    if fwd <= back:
        inner = sorted(((t - ta) % L, p) for t, p in zip(ts, coords) if 1e-6 < (t - ta) % L < fwd - 1e-6)
    else:
        inner = sorted(((ta - t) % L, p) for t, p in zip(ts, coords) if 1e-6 < (ta - t) % L < back - 1e-6)
    return [tuple(a)] + [tuple(p) for _, p in inner] + [tuple(b)], min(fwd, back)


def _resample(pts, n):
    """n points evenly along polyline pts (ends kept)."""
    np = _np()
    p = np.asarray(pts, float)
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(p, axis=0).T))])
    if s[-1] <= 0:
        return [tuple(p[0])] * n
    t = np.linspace(0, s[-1], n)
    return [(float(np.interp(x, s, p[:, 0])), float(np.interp(x, s, p[:, 1]))) for x in t]


def _cut_at_mitre(c: PlannedColumn, p_i, p_o, ring_i, ring_o):
    """Column c (corner end last, left = inside rail) re-made round the corner: its pairs up to
    CORNER_FAN_WIDTHS of its width short of the inside corner (along the inside rail) are kept, and
    from there the stitches turn: pairs along the outline, the inside rail to p_i and the outside
    rail to p_o, ending on the corner stitch (p_i, p_o). EmbroideryStudio's corners turn over about
    that far (its 13 mm L: from about one width up the stem to one width along the foot). None if it
    can't be cut cleanly."""
    from shapely.geometry import LineString, Point

    w = max(_stroke_width(c), 0.5)
    fan = CORNER_FAN_WIDTHS * w
    c = _densified(c, p_i, fan + 2 * w + math.dist(p_i, p_o))
    cum = [0.0]
    for a, b in zip(c.left, c.left[1:]):
        cum.append(cum[-1] + math.dist(a, b))
    # where the corner falls along the inside rail, looked for on the rail's corner-end part only (a
    # column closing into a ring passes the same corner at its other end too)
    j0 = 0
    while j0 < len(cum) - 2 and cum[-1] - cum[j0 + 1] >= fan + 4 * w + math.dist(p_i, p_o):
        j0 += 1
    s_i = cum[j0] + LineString(c.left[j0:]).project(Point(p_i))
    # a short column (a G's spur between its bar and its bowl) turns over at most part of its length,
    # leaving the rest for the corner at its other end
    fan = min(fan, SHORT_FAN_SHARE * s_i)
    # the last pair at least `fan` back along the inside rail whose stitch stays clear of the corner
    # stitch (the segment itself: at a sharp V the legs run nearly along its line, far past it)
    corner = LineString([p_i, p_o])
    k = len(c.left) - 1
    while k >= 0 and (cum[k] > s_i - fan
                      or LineString([c.left[k], c.right[k]]).distance(corner) < CLIP_MARGIN_MM):
        k -= 1
    if k < 0:
        _dbg("   cut: no pair clear of the corner", round(s_i, 2), round(fan, 2))
        return None
    li, lo = c.left[k], c.right[k]
    arc_i, len_i = _arc(ring_i, li, p_i)
    arc_o, len_o = _arc(ring_o, lo, p_o)
    # the rails' own way round must stay near the column: an arc the long way round is a wrong pick
    if len_i > 3 * math.dist(li, p_i) + 1.0 or len_o > 3 * math.dist(lo, p_o) + 1.0:
        _dbg("   cut: arc", round(len_i, 2), round(math.dist(li, p_i), 2), round(len_o, 2), round(math.dist(lo, p_o), 2),
             "from", li, lo)
        return None
    n = max(2, int(math.ceil(max(len_i, len_o) / ARC_STEP_MM)) + 1)
    ins, outs = _resample(arc_i, n), _resample(arc_o, n)
    left = list(c.left[:k + 1]) + ins[1:]
    right = list(c.right[:k + 1]) + outs[1:]
    left[-1], right[-1] = tuple(p_i), tuple(p_o)
    return PlannedColumn(left, right, False)


def _densified(c: PlannedColumn, near, reach: float, step: float = ARC_STEP_MM) -> PlannedColumn:
    """c with pairs added every `step` mm (by linear interpolation along both rails: the satin's own
    reading of a span between two pairs) on the spans within `reach` of `near`, so a mitre cut there
    drops only the pairs at the corner, not a whole straight span."""
    left, right = [c.left[0]], [c.right[0]]
    for i in range(1, len(c.left)):
        a0, b0, a1, b1 = c.left[i - 1], c.right[i - 1], c.left[i], c.right[i]
        m0 = ((a0[0] + b0[0]) / 2, (a0[1] + b0[1]) / 2)
        m1 = ((a1[0] + b1[0]) / 2, (a1[1] + b1[1]) / 2)
        L = max(math.dist(a0, a1), math.dist(b0, b1))
        if min(math.dist(m0, near), math.dist(m1, near)) <= reach + L and L > step:
            n = int(math.ceil(L / step))
            for j in range(1, n):
                t = j / n
                left.append((a0[0] + (a1[0] - a0[0]) * t, a0[1] + (a1[1] - a0[1]) * t))
                right.append((b0[0] + (b1[0] - b0[0]) * t, b0[1] + (b1[1] - b0[1]) * t))
        left.append(a1)
        right.append(b1)
    return PlannedColumn(left, right, c.closed)


def _end_region(c: PlannedColumn, w: float):
    """The quads of column c (corner end last) within about 2.5 widths of that end."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    qs, run = [], 0.0
    for i in range(len(c.left) - 1, 0, -1):
        l0, r0, l1, r1 = c.left[i], c.right[i], c.left[i - 1], c.right[i - 1]
        q = Polygon([l0, l1, r1, r0])
        qs.append(q if q.is_valid else q.buffer(0))
        run += math.dist(((l0[0] + r0[0]) / 2, (l0[1] + r0[1]) / 2), ((l1[0] + r1[0]) / 2, (l1[1] + r1[1]) / 2))
        if run >= 2.5 * w:
            break
    return unary_union(qs)


def _corner_points(contours, glyph):
    """Sharp inside corners of the outline, each snapped to the outline vertex nearest it."""
    np = _np()
    from cli_anything_inkstitch.planner.autostrokes import _concave_corners

    arrs = [np.asarray(c, float) for c in contours]
    found = _concave_corners(arrs, glyph)
    verts = np.vstack(arrs)
    out = []
    for q in found:
        d = np.hypot(*(verts - q).T)
        i = int(np.argmin(d))
        out.append(tuple(verts[i]) if d[i] <= 0.15 else (float(q[0]), float(q[1])))
    return out


def _convex_corners(contours, glyph):
    """Sharp outside corners (the outline turning outwards by more than 40 degrees within 0.4 mm)."""
    np = _np()
    from shapely import contains_xy

    out = []
    step, k = 0.1, 4
    for c in contours:
        c = np.asarray(c, float)
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
        bis = (d1 / (np.hypot(*d1.T)[:, None] + 1e-12) - d2 / (np.hypot(*d2.T)[:, None] + 1e-12))
        probe = p - bis / (np.hypot(*bis.T)[:, None] + 1e-12) * 0.3
        inside = contains_xy(glyph, probe[:, 0], probe[:, 1])
        hot = np.where(turn > 40.0)[0]
        run = []
        for i in list(hot) + [None]:
            if i is not None and (not run or i - run[-1] <= 2):
                run.append(i)
                continue
            if run:
                mm = max(run, key=lambda q: turn[q])
                if inside[mm]:
                    d = np.hypot(*(c - p[mm]).T)
                    j = int(np.argmin(d))
                    out.append(tuple(c[j]) if d[j] <= 0.15 else (float(p[mm][0]), float(p[mm][1])))
            run = [i] if i is not None else []
    return out


def _check(planned):
    """flat_check of the planned letter as it stands (columns as straight-chord quads)."""
    np = _np()
    from cli_anything_inkstitch.planner import art_columns as ac
    from cli_anything_inkstitch.planner import columns as col

    cols = [col.Column(pairs=list(zip(c.left, c.right)), curve=[False] * len(c.left), closed=c.closed)
            for c in planned.columns]
    outline = [np.asarray(c, float) for c in planned.contours]
    allp = np.vstack(outline)
    box = (allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max())
    return ac.flat_check(col.Letter(planned.char, cols, outline, box, butt_overlap=col.BUTT_OVERLAP_MM))


def _crossed(c: PlannedColumn) -> int:
    from shapely.geometry import LineString

    n = 0
    for (l0, r0), (l1, r1) in zip(zip(c.left, c.right), zip(c.left[1:], c.right[1:])):
        a, b = LineString([l0, r0]), LineString([l1, r1])
        if a.length and b.length and a.crosses(b):
            n += 1
    return n


def _simple(c: PlannedColumn, old=None) -> bool:
    """Neither rail crosses itself. With `old` (the rails the columns had before the wrap),
    only crossings that involve a new segment count: a merged column may still carry a tucked end
    elsewhere (a P's bowl into its stem), which was there before and is the joint working as meant."""
    from shapely.geometry import LineString

    rails = [c.left, c.right]
    if c.closed:
        # a ring: up to its closing pair (the start's pair again); past it is the overlap, which
        # retraces the start on purpose
        n = next((i for i in range(1, len(c.left)) if c.left[i] == c.left[0] and c.right[i] == c.right[0]),
                 len(c.left) - 1)
        rails = [c.left[:n + 1], c.right[:n + 1]]
    for r in rails:
        if len(r) < 2 or LineString(r).is_simple:
            continue
        if old is None:
            return False
        segs = [LineString([a, b]) for a, b in zip(r, r[1:])]
        # new: not lying along a rail the columns had (densifying a span adds points on it, not new rail)
        new = [i for i in range(len(segs)) if old.distance(segs[i].interpolate(0.5, normalized=True)) > 1e-6]
        for i in new:
            for j, b in enumerate(segs):
                if abs(i - j) > 1 and segs[i].intersects(b) and not (c.closed and {i, j} == {0, len(segs) - 1}):
                    return False
    return True


def corner_stitch(planned, p_i, legs):
    """The outside end of the corner stitch from inside corner p_i, given the two legs' directions
    away from the corner: the outside corner nearest the bisector's hit on the outline (a square
    corner's point), or the hit itself (a flat foot's middle, a rounded corner)."""
    np = _np()
    from shapely.geometry import MultiLineString

    from cli_anything_inkstitch.planner.autostrokes import _ray_to_outline

    da, db = legs
    o = -(da + db)
    if np.hypot(*o) < 1e-6:
        return None
    o = o / np.hypot(*o)
    boundary = MultiLineString([np.vstack([c, c[:1]]) for c in (np.asarray(c, float) for c in planned.contours)])
    hit = _ray_to_outline(boundary, np.asarray(p_i, float) + o * 0.02, o)
    if hit is None:
        return None
    return hit[1]


def wrap_corners(planned, max_stitch_mm: float = CORNER_WRAP_MAX_STITCH_MM):
    """See the module docstring. Lists the wraps in check["corners_wrapped"] ({at, stitch_mm}) and
    the corners left butted in check["corners_butted"] (their corner stitch was too long)."""
    np = _np()
    from shapely.geometry import Point

    if not planned.ok or len(planned.columns) < 1 or not planned.contours:
        return planned
    from cli_anything_inkstitch.planner import columns as col

    outline = [np.asarray(c, float) for c in planned.contours]
    allp = np.vstack(outline)
    box = (allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max())
    glyph = col.Letter("?", [], outline, box).glyph()
    rings = _rings(outline)
    inside = []
    for q in _corner_points(outline, glyph):
        if all(math.dist(q, r) > 0.3 for r in inside):
            inside.append(q)
    outside = _convex_corners(outline, glyph)
    base = _check(planned)
    wrapped, butted = [], []
    done: set = set()
    # a wrap can make the next corner possible (a G's spur wraps into its bar, then its bowl into the
    # spur), so the corners are gone over again until nothing changes
    for p_i in [q for _ in range(WRAP_PASSES) for q in inside]:
        if p_i in done:
            continue
        cols = planned.columns
        ends = []
        for k, c in enumerate(cols):
            if c.closed or len(c.left) < 2:
                continue
            w = _stroke_width(c)
            for e in (0, -1):
                if w <= 0 or math.dist(_mid(c, e), p_i) > CORNER_END_REACH * w + 0.3:
                    continue
                # ...and the corner is on (or at) the column's end part, not beside it
                if Point(p_i).distance(_end_region(c if e == -1 else _reversed(c), w)) > CORNER_TOUCH_MM:
                    continue
                ends.append((math.dist(_mid(c, e), p_i), k, e))
            mine = [x for x in ends if x[1] == k]
            if len(mine) == 2:
                # a short column reaches the corner from both ends' parts: only its nearer end meets it
                # there, unless both ends are right at the corner (a column that closes into a ring)
                far = max(mine)
                length = sum(math.dist(_mid(c, i), _mid(c, i + 1)) for i in range(len(c.left) - 1))
                if far[0] > RING_END_REACH * w + 0.3 or length < RING_MIN_LENGTH * w:
                    ends.remove(far)
        if len(ends) != 2:
            _dbg(planned.char, "corner", p_i, "ends", [(round(d, 2), k, e) for d, k, e in ends])
            continue
        (_, ka, ea), (_, kb, eb) = sorted(ends)
        same = ka == kb
        # A third column's body at the corner makes it a junction, not a corner
        near = [j for j, o in enumerate(cols) if j not in (ka, kb)
                and Point(p_i).distance(_poly(o)) < 0.3]
        if near:
            _dbg(planned.char, "corner", p_i, "third column", near)
            continue
        # ...or a medial-axis junction of three strokes there (an A's crossbar into a leg that runs on
        # to its foot): a corner of two strokes has none, or one the planner read as a corner
        junction = [j for j in planned.check.get("junctions", [])
                    if j.get("type") not in TWO_STROKE_JUNCTIONS and math.dist(j["pos"], p_i) <= JUNCTION_REACH * j["radius"] + 0.2]
        if junction:
            _dbg(planned.char, "corner", p_i, "junction", [j["type"] for j in junction])
            continue
        a = cols[ka] if ea == -1 else _reversed(cols[ka])          # corner end last
        b = cols[kb] if eb == -1 else _reversed(cols[kb])          # (b reversed again below)
        wa, wb = _stroke_width(a), _stroke_width(b)
        da, db = _leg_dir(a, max(wa, 0.5)), _leg_dir(b, max(wb, 0.5))
        if da is None or db is None:
            continue
        turn = 180.0 - math.degrees(math.acos(max(-1.0, min(1.0, float(da @ db)))))
        if turn < CORNER_MIN_TURN_DEG:
            _dbg(planned.char, "corner", p_i, "turn", turn)
            continue
        # A level bar meeting a slanted leg (a Z's or 2's foot, a 7's top) stays a butt join: the bar
        # runs on, flat along the letter's edge, and the leg's stitches lie along it. The planner's
        # own rule (autostrokes _split_corners), and what EmbroideryStudio does with its Z.
        from cli_anything_inkstitch.planner.autostrokes import _level, _slanted
        if (_level(da) and _slanted(db)) or (_level(db) and _slanted(da)):
            _dbg(planned.char, "corner", p_i, "bar meets slanted leg")
            continue
        hit = corner_stitch(planned, p_i, (da, db))
        if hit is None:
            continue
        w = max(wa, wb)
        p_o = tuple(map(float, hit))
        snap = [q for q in outside if math.dist(q, hit) <= OUTER_SNAP * w]
        if snap:
            p_o = min(snap, key=lambda q: math.dist(q, hit))
        stitch = math.dist(p_i, p_o)
        _dbg(planned.char, "corner", p_i, "turn", round(turn), "stitch", round(stitch, 2), "to", p_o)
        if stitch > max_stitch_mm:
            done.add(p_i)
            butted.append({"at": [round(float(p_i[0]), 2), round(float(p_i[1]), 2)], "stitch_mm": round(stitch, 2)})
            continue
        ring_i = min(rings, key=lambda r: r.distance(Point(p_i)))
        ring_o = min(rings, key=lambda r: r.distance(Point(p_o)))
        a, b = _inside_left(a, p_i), _inside_left(b, p_i)
        ca = _cut_at_mitre(a, p_i, p_o, ring_i, ring_o)
        cb = _cut_at_mitre(b, p_i, p_o, ring_i, ring_o)
        if ca is None or cb is None:
            _dbg(planned.char, "corner", p_i, "no mitre cut", ca is None, cb is None)
            continue
        if same:
            # both ends of one column meet here: close it into a ring at the mitre
            ring = _ring_from(cols[ka] if ea == -1 else _reversed(cols[ka]), p_i, p_o, ring_i, ring_o)
            if ring is None:
                continue
            new_cols = [ring if j == ka else c for j, c in enumerate(cols)]
        else:
            merged = PlannedColumn(ca.left + list(reversed(cb.left))[1:],
                                   ca.right + list(reversed(cb.right))[1:], False)
            new_cols = []
            for j, c in enumerate(cols):
                if j == min(ka, kb):
                    new_cols.append(merged)
                elif j != max(ka, kb):
                    new_cols.append(c)
        cand = [c for c in new_cols]
        from shapely.geometry import LineString, MultiLineString
        old = MultiLineString([LineString(r) for c in cols for r in (c.left, c.right) if len(r) >= 2])
        if (any(_crossed(c) for c in cand if c not in cols)
                or not all(_simple(c, old) for c in cand if c not in cols)):
            _dbg(planned.char, "corner", p_i, "crossed or not simple",
                 [(_crossed(c), _simple(c, old)) for c in cand if c not in cols])
            continue
        trial = type(planned)(planned.char, cand, planned.contours, planned.ok, dict(planned.check), planned.error)
        chk = _check(trial)
        if (chk["crossed_pairs"] > base["crossed_pairs"]
                or chk["sewable_bare_pct"] > base["sewable_bare_pct"] + BARE_TOL_PCT
                or chk["outside_pct"] > base["outside_pct"] + OUTSIDE_TOL_PCT
                or chk["overlap_mm2"] >= base["overlap_mm2"] - 1e-6 and not same):
            _dbg(planned.char, "corner", p_i, "worse", {k: (base[k], chk[k]) for k in
                 ("crossed_pairs", "sewable_bare_pct", "outside_pct", "overlap_mm2")})
            continue
        planned.columns = cand
        base = chk
        done.add(p_i)
        wrapped.append({"at": [round(float(p_i[0]), 2), round(float(p_i[1]), 2)], "stitch_mm": round(stitch, 2),
                        **({"ring": True} if same else {})})
    if wrapped:
        planned.check["corners_wrapped"] = wrapped
        for k in ("sewable_bare_pct", "outside_pct", "overlap_mm2", "crossed_pairs", "uncovered_pct"):
            if k in base:
                planned.check[k] = base[k]
    if butted:
        planned.check["corners_butted"] = butted
    return planned


def _poly(c: PlannedColumn):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    qs = []
    for (l0, r0), (l1, r1) in zip(zip(c.left, c.right), zip(c.left[1:], c.right[1:])):
        q = Polygon([l0, l1, r1, r0])
        qs.append(q if q.is_valid else q.buffer(0))
    return unary_union(qs)


def _ring_from(c: PlannedColumn, p_i, p_o, ring_i, ring_o):
    """A column whose two ends meet at one corner (corner end last, left = inside), closed into a
    ring that starts and ends on the mitre: both ends cut back to the mitre line, the start end's
    pairs re-made along the outline from the mitre, and the start pair repeated at the end with a
    short overlap (RING_OVERLAP_MM), as the planner closes an O."""
    from cli_anything_inkstitch.planner.columns import RING_OVERLAP_MM, _extend_ring

    c = _inside_left(c, p_i)
    # the same rail must be the inside one at both ends, or the ring would twist
    if _inside_left(_reversed(c), p_i).left != list(reversed(c.left)):
        return None
    end = _cut_at_mitre(c, p_i, p_o, ring_i, ring_o)
    if end is None:
        return None
    start = _cut_at_mitre(_reversed(end), p_i, p_o, ring_i, ring_o)
    if start is None:
        return None
    col = _reversed(start)
    pairs = list(zip(col.left, col.right))
    if math.dist(pairs[0][0], pairs[-1][0]) > 1e-6 or math.dist(pairs[0][1], pairs[-1][1]) > 1e-6:
        return None
    pairs = pairs + _extend_ring(pairs, RING_OVERLAP_MM)
    return PlannedColumn([p for p, _ in pairs], [q for _, q in pairs], True)
