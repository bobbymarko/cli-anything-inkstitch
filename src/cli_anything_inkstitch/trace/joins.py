"""Tidy where a shape's satin columns meet: deep overlaps trimmed, bare wedges closed.

The planner joins strokes generously: where two strokes meet they may overlap by about a square of
their width (a V's legs, a knot's arm running into its bar), and an end cut that doesn't line up with
its neighbour's edge leaves a thin wedge of bare fabric between the two. On a large logo that shows:
an arm's end band sewn twice across a bar, a strand behind a crossing jutting out past the one in
front, a slit of fabric at a junction, a small triangle at the outline where two columns meet.

Here, per shape, after planning:

* trim_overlaps: where a column's end lies inside another column deeper than `max_depth_mm`, the
  column is cut back so it reaches only `keep_mm` into the other: each rail is cut where it enters the
  other column (shrunk by keep_mm), walking back from the end, and the new end pair joins those two
  points, so the end lies along the other column's edge. When both ends lie in each other (a V or a
  mitred tip), both cuts are tried and the one that leaves less bare fabric wins.
* close_wedges: a bare patch at a junction is closed by pushing the nearest column end on along its
  rails, into the neighbour by keep_mm, without leaving the outline.

Each change is kept only if it leaves no more bare fabric than before (within a small tolerance),
nothing outside the outline, and no crossed pairs. All geometry is mm, y up, like PlannedLetter.
"""

from __future__ import annotations

import os
import sys

_DEBUG = bool(os.environ.get("INKSTITCH_JOINS_DEBUG"))


def _dbg(*a):
    if _DEBUG:
        print("[joins]", *a, file=sys.stderr)


KEEP_MM = 0.4            # how far a trimmed or extended end reaches into the column it meets
MAX_DEPTH_MM = 1.0       # overlaps deeper than this are trimmed
MIN_OVERLAP_MM2 = 0.5
BARE_TOL_MM2 = 0.15      # a change may leave at most this much more bare fabric
MIN_WEDGE_MM2 = 0.3      # bare patches smaller than this are left alone
EDGE_INSET_MM = 0.15     # the outline is shrunk this much before measuring bare fabric (pull comp covers it)
MAX_EXTEND_MM = 4.0
EXTEND_STEP_MM = 0.25


def _poly(c):
    from shapely.geometry import Polygon

    return Polygon(list(c.left) + list(c.right)[::-1]).buffer(0)


def _outline(contours):
    from shapely.geometry import Polygon

    rings = [list(map(tuple, r)) for r in contours if len(r) >= 3]
    if not rings:
        return None
    return Polygon(rings[0], rings[1:]).buffer(0)


def _bare(outline, cols):
    from shapely.ops import unary_union

    if not cols:
        return outline.area
    return outline.buffer(-EDGE_INSET_MM).difference(unary_union([_poly(c) for c in cols])).area


def _depth(x) -> float:
    """Narrow width of an overlap region (its minimum rotated rectangle's short side)."""
    import numpy as np

    if x.is_empty or x.area <= 0:
        return 0.0
    xs = np.asarray(x.minimum_rotated_rectangle.exterior.coords)
    return float(min(np.hypot(*np.diff(xs, axis=0).T)))


def _fans(c) -> int:
    from cli_anything_inkstitch.trace import column_checks as cc

    return len(cc.fans(list(zip(c.left, c.right))))


def _crossed(left, right) -> int:
    from shapely.geometry import LineString

    n = 0
    for i in range(len(left) - 1):
        a, b = LineString([left[i], right[i]]), LineString([left[i + 1], right[i + 1]])
        if a.length and b.length and a.crosses(b):
            n += 1
    return n


def _rail_hit(rail, line, near):
    """Where `line` crosses the rail polyline nearest to `near`: (segment index, point) or None."""
    from shapely.geometry import LineString, Point

    best = None
    for i in range(len(rail) - 1):
        seg = LineString([rail[i], rail[i + 1]])
        x = seg.intersection(line)
        for g in getattr(x, "geoms", [x]):
            if g.geom_type != "Point" or g.is_empty:
                continue
            d = g.distance(Point(near))
            if best is None or d < best[0]:
                best = (d, i, (float(g.x), float(g.y)))
    return None if best is None else best[1:]


def _densify(L, R, step):
    import numpy as np

    oL, oR, orig = [L[0]], [R[0]], [True]
    for i in range(len(L) - 1):
        a = (np.asarray(L[i]) + np.asarray(R[i])) / 2
        b = (np.asarray(L[i + 1]) + np.asarray(R[i + 1])) / 2
        n = max(1, int(np.ceil(np.hypot(*(b - a)) / step)))
        for q in range(1, n):
            t = q / n
            oL.append(tuple(np.asarray(L[i]) * (1 - t) + np.asarray(L[i + 1]) * t))
            oR.append(tuple(np.asarray(R[i]) * (1 - t) + np.asarray(R[i + 1]) * t))
            orig.append(False)
        oL.append(L[i + 1])
        oR.append(R[i + 1])
        orig.append(True)
    return oL, oR, orig


def _trimmed(c, other_poly, end: int, keep_mm: float, line_at=None):
    """Column c with its `end` (0 or -1) cut back to reach keep_mm into other_poly, or None.

    The cut follows the other column's edge: walking back from the end along the column's middle,
    find where it leaves other_poly shrunk by keep_mm; the cut line runs through that point along the
    nearest stretch of that (shrunk) edge, and each rail is cut where it crosses that line. Rails are
    cut by a line, not tested point by point, so a rail lying on the shared outline still gets cut."""
    from copy import copy

    import numpy as np
    from shapely.geometry import LineString, Point

    L, R = list(c.left), list(c.right)
    if end == 0:
        L, R = L[::-1], R[::-1]
    if line_at is not None:                    # a given cut line (a mitre): point and direction
        L, R, orig = _densify(L, R, 0.5)
        mp, e = np.asarray(line_at[0], float), np.asarray(line_at[1], float)
        return _cut_with(c, L, R, orig, end, mp, e)
    region = other_poly.buffer(-keep_mm)
    if region.is_empty:
        return None
    # Straight columns carry few pairs (one per end): add pairs between them, in proportion along both
    # rails, so the cut can land between; only the original pairs and the new end pair are kept.
    L, R, orig = _densify(L, R, 0.5)
    mids = [((l[0] + r[0]) / 2, (l[1] + r[1]) / 2) for l, r in zip(L, R)]
    if region.contains(Point(mids[-1])):
        k = len(mids) - 1
        while k >= 0 and region.contains(Point(mids[k])):
            k -= 1
        if k < 0:
            _dbg("     cut: whole middle inside")
            return None
        x = LineString([mids[k], mids[k + 1]]).intersection(region.boundary)
        hits = [g for g in getattr(x, "geoms", [x]) if g.geom_type == "Point" and not g.is_empty]
        if not hits:
            return None
        m = min(hits, key=lambda g: g.distance(Point(mids[k])))
    elif other_poly.contains(Point(mids[-1])):
        # the end's middle is in the other column but not keep_mm deep: a slanted end whose one corner
        # runs on through it. Cut along the other column's edge (shrunk) nearest the end.
        rb = region.boundary
        m = rb.interpolate(rb.project(Point(mids[-1])))
    else:
        _dbg("     cut: end mid not inside")
        return None
    # the edge's direction there: the boundary segment nearest m
    ring = region.exterior if region.geom_type == "Polygon" else max(region.geoms, key=lambda g: g.area).exterior
    t = ring.project(m)
    p0 = np.asarray(ring.interpolate(max(0.0, t - 0.5)).coords[0])
    p1 = np.asarray(ring.interpolate(min(ring.length, t + 0.5)).coords[0])
    e = p1 - p0
    if np.hypot(*e) == 0:
        return None
    e /= np.hypot(*e)
    return _cut_with(c, L, R, orig, end, np.asarray(m.coords[0]), e)


def _cut_with(c, L, R, orig, end, mp, e):
    """Cut the (densified, end-last) rails L, R by the line through mp along e; see _trimmed."""
    from copy import copy

    import numpy as np
    from shapely.geometry import LineString

    w = max(np.hypot(*np.subtract(a, b)) for a, b in zip(L, R)) * 3 + 2
    line = LineString([tuple(mp - e * w), tuple(mp + e * w)])
    hl, hr = _rail_hit(L, line, mp), _rail_hit(R, line, mp)
    if hl is None and hr is None:
        _dbg("     cut: line misses both rails")
        return None
    # A rail the line misses ends short of it (a slanted end): it keeps its end.
    if hl is None:
        hl = (len(L) - 1, L[-1])
    if hr is None:
        hr = (len(R) - 1, R[-1])
    kl, pl = hl
    kr, pr = hr
    kk = min(kl, kr)
    if kk < 0:
        return None
    keep = [i for i in range(min(kk + 1, len(L) - 1)) if orig[i]]
    if not keep:
        return None
    L2, R2 = [L[i] for i in keep] + [pl], [R[i] for i in keep] + [pr]
    if end == 0:
        L2, R2 = L2[::-1], R2[::-1]
    out = copy(c)
    out.left, out.right = L2, R2
    return out


def _mitre(a, b, pa, pb, keep_mm):
    """Two columns whose ends lie in each other (a V, a mitred tip): both cut along the line through
    the two farthest points where their edges cross (the inner corner to the outer one), each reaching
    keep_mm / 2 past it. Returns (a', b') or None."""
    import numpy as np
    from shapely.geometry import Point

    ends_a = [e for e in (0, -1) if _mid_inside(a, e, pb, 0.05)]
    ends_b = [e for e in (0, -1) if _mid_inside(b, e, pa, 0.05)]
    if len(ends_a) != 1 or len(ends_b) != 1 or a.closed or b.closed:
        return None
    x = pa.boundary.intersection(pb.boundary)
    pts = [np.asarray(g.coords[0]) for g in getattr(x, "geoms", [x]) if g.geom_type == "Point"]
    if len(pts) < 2:
        return None
    p, q = max(((u, v) for n, u in enumerate(pts) for v in pts[n + 1:]), key=lambda uv: np.hypot(*(uv[0] - uv[1])))
    e = q - p
    if np.hypot(*e) < 0.5:
        return None
    e = e / np.hypot(*e)
    nrm = np.array([-e[1], e[0]])
    mid = (p + q) / 2
    out = []
    for c, end in ((a, ends_a[0]), (b, ends_b[0])):
        # which side of the line the column's body lies on: it reaches keep/2 across to the other side
        far = np.asarray(c.left[0 if end == -1 else -1]) / 2 + np.asarray(c.right[0 if end == -1 else -1]) / 2
        side = 1.0 if np.dot(far - mid, nrm) > 0 else -1.0
        t = _trimmed(c, None, end, keep_mm, line_at=(mid - side * nrm * keep_mm / 2, e))
        if t is None:
            return None
        out.append(t)
    return tuple(out)


def _mid_inside(c, end, poly, margin):
    from shapely.geometry import Point

    m = ((c.left[end][0] + c.right[end][0]) / 2, (c.left[end][1] + c.right[end][1]) / 2)
    return poly.buffer(-margin).contains(Point(m))


def trim_overlaps(planned, keep_mm: float = KEEP_MM, max_depth_mm: float = MAX_DEPTH_MM):
    """See the module docstring. Lists the trims in check["joins_trimmed"]."""
    outline = _outline(planned.contours)
    cols = planned.columns
    if outline is None or len(cols) < 2:
        return planned
    done = []
    for _ in range(3):
        changed = False
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                a, b = cols[i], cols[j]
                if a.closed and b.closed:
                    continue
                pa, pb = _poly(a), _poly(b)
                x = pa.intersection(pb)
                if x.area < MIN_OVERLAP_MM2 or _depth(x) <= max_depth_mm:
                    continue
                base_bare = _bare(outline, cols)
                _dbg(planned.char, "overlap", i, j, round(x.area, 2), "depth", round(_depth(x), 2), "bare", round(base_bare, 2))
                best = None
                for (k, c, other) in ((i, a, pb), (j, b, pa)):
                    if c.closed:
                        continue
                    for end in (0, -1):
                        if not _mid_inside(c, end, other, 0.1):
                            _dbg("  ", k, end, "end not inside")
                            continue
                        t = _trimmed(c, other, end, keep_mm)
                        if t is not None and _fans(t) > _fans(c):
                            t = None
                        if t is None or _crossed(t.left, t.right) > _crossed(c.left, c.right):
                            _dbg("  ", k, end, "no cut" if t is None else "crossed")
                            continue
                        trial = list(cols)
                        trial[k] = t
                        bare = _bare(outline, trial)
                        ov = _poly(trial[i]).intersection(_poly(trial[j])).area
                        _dbg("  ", k, end, "bare", round(bare, 2), "ov", round(ov, 2))
                        if bare > base_bare + BARE_TOL_MM2:
                            continue
                        if ov >= x.area * 0.8:
                            continue
                        score = bare + 0.2 * ov
                        if best is None or score < best[0]:
                            best = (score, k, t, round(x.area, 2), round(ov, 2))
                mit = _mitre(a, b, pa, pb, keep_mm)
                if mit is not None:
                    ta, tb = mit
                    trial = list(cols)
                    trial[i], trial[j] = ta, tb
                    bare = _bare(outline, trial)
                    ov = _poly(ta).intersection(_poly(tb)).area
                    _dbg("   mitre bare", round(bare, 2), "ov", round(ov, 2))
                    if bare <= base_bare + BARE_TOL_MM2 and ov < x.area * 0.8:
                        score = bare + 0.2 * ov
                        if best is None or score < best[0]:
                            best = (score, (i, j), (ta, tb), round(x.area, 2), round(ov, 2))
                if best and isinstance(best[1], tuple):
                    _, (ki, kj), (ta, tb), before, after = best
                    cols[ki], cols[kj] = ta, tb
                    done.append({"column": ki, "with": kj, "mitre": True, "overlap_mm2": [before, after]})
                    changed = True
                elif best:
                    _, k, t, before, after = best
                    cols[k] = t
                    done.append({"column": k, "with": j if k == i else i,
                                 "overlap_mm2": [before, after]})
                    changed = True
        if not changed:
            break
    if done:
        planned.check["joins_trimmed"] = done
    return planned


def _tangent(rail, end):
    import numpy as np

    p = np.asarray(rail, float)
    if len(p) < 2:
        return None
    if end == -1:
        a, b = p[-1], p[-2]
        k = len(p) - 2
        while k > 0 and np.hypot(*(a - b)) < 0.3:
            k -= 1
            b = p[k]
    else:
        a, b = p[0], p[1]
        k = 1
        while k < len(p) - 1 and np.hypot(*(a - b)) < 0.3:
            k += 1
            b = p[k]
    v = a - b
    n = np.hypot(*v)
    return None if n == 0 else v / n


def close_wedges(planned, keep_mm: float = KEEP_MM):
    """See the module docstring. Lists the closed wedges in check["wedges_closed"]."""
    import numpy as np
    from shapely.geometry import LineString
    from shapely.ops import unary_union

    outline = _outline(planned.contours)
    cols = planned.columns
    if outline is None or len(cols) < 2:
        return planned
    ink = outline.buffer(0.1)
    union = unary_union([_poly(c) for c in cols])
    bare = outline.buffer(-EDGE_INSET_MM).difference(union)
    patches = [g for g in getattr(bare, "geoms", [bare]) if g.area >= MIN_WEDGE_MM2]
    done = []
    for patch in sorted(patches, key=lambda g: -g.area):
        base = _bare(outline, cols)
        best = None
        _dbg(planned.char, "wedge", round(patch.area, 2), "at", [round(v, 1) for v in patch.centroid.coords[0]])
        why = {}
        for k, c in enumerate(cols):
            if c.closed or len(c.left) < 2:
                continue
            for end in (0, -1):
                rung = LineString([c.left[end], c.right[end]])
                if rung.distance(patch) > 0.6:
                    continue
                out0 = _poly(c).difference(ink).area
                why[(k, end)] = {"out": 0, "cross": 0, "little": 0, "ok": 0}
                tl, tr = _tangent(c.left, end), _tangent(c.right, end)
                if tl is None or tr is None:
                    continue
                steps = np.arange(0, MAX_EXTEND_MM + 1e-9, EXTEND_STEP_MM)
                for dl in steps:
                    for dr in steps:
                        if dl == 0 and dr == 0:
                            continue
                        pl = tuple(np.asarray(c.left[end]) + tl * dl)
                        pr = tuple(np.asarray(c.right[end]) + tr * dr)
                        L, R = list(c.left), list(c.right)
                        if end == -1:
                            L.append(pl)
                            R.append(pr)
                        else:
                            L.insert(0, pl)
                            R.insert(0, pr)
                        t = type(c)(left=L, right=R, closed=c.closed)
                        if _fans(t) > _fans(c):
                            continue
                        tp = _poly(t)
                        grew = tp.difference(ink).area - out0
                        if grew > 0.05 or _crossed(L, R) > _crossed(c.left, c.right):
                            why[(k, end)]["out" if grew > 0.05 else "cross"] += 1
                            continue
                        trial = list(cols)
                        trial[k] = t
                        b = _bare(outline, trial)
                        if b > base - 0.5 * patch.area:
                            why[(k, end)]["little"] += 1
                            continue
                        why[(k, end)]["ok"] += 1
                        # how far it now runs into its neighbours: keep it near keep_mm
                        others = unary_union([_poly(o) for n, o in enumerate(cols) if n != k])
                        extra = tp.intersection(others).area - _poly(c).intersection(others).area
                        score = b + 0.5 * max(0.0, extra - keep_mm * rung.length) + 0.01 * (dl + dr)
                        if best is None or score < best[0]:
                            best = (score, k, t, round(patch.area, 2), round(b - base, 2))
        _dbg("   candidates", why)
        if best is None:
            best = _widen_into(cols, patch, outline, ink, base, keep_mm)
            if best is not None:
                _dbg("   widened column", best[1])
        if best:
            _, k, t, area, gain = best
            cols[k] = t
            done.append({"column": k, "bare_mm2": area, "change_mm2": gain})
    if done:
        planned.check["wedges_closed"] = done
    return planned


def _widen_into(cols, patch, outline, ink, base, keep_mm):
    """A bare crotch between two columns' sides (the art goes straight across where two strokes
    part): push the nearer column's side out across it. The column gets pairs every 0.5 mm along the
    stretch, and each rail point there moves out along its stitch line to the far side of the patch
    (+ keep_mm), staying on the ink."""
    import numpy as np
    from shapely.geometry import LineString, Point

    best = None
    for k, c in enumerate(cols):
        if c.closed or len(c.left) < 2:
            continue
        L, R, _ = _densify(list(c.left), list(c.right), 0.5)
        for side in (0, 1):
            rail, opp = (L, R) if side == 0 else (R, L)
            if LineString(rail).distance(patch) > 0.3:
                continue
            new = list(rail)
            moved = 0
            for i, (q, o) in enumerate(zip(rail, opp)):
                if Point(q).distance(patch) > 1.5:
                    continue
                v = np.subtract(q, o)
                n = np.hypot(*v)
                if n == 0:
                    continue
                v = v / n
                ray = LineString([tuple(np.asarray(q) - v * 0.05), tuple(np.asarray(q) + v * 6)])
                x = ray.intersection(patch.buffer(0.05))
                if x.is_empty:
                    continue
                far = max((np.dot(np.subtract(pt, q), v) for g in getattr(x, "geoms", [x])
                           for pt in g.coords), default=0)
                if far <= 0.05:
                    continue
                p2 = np.asarray(q) + v * (far + keep_mm)
                if not ink.contains(Point(p2)):
                    p2 = np.asarray(q) + v * far
                new[i] = tuple(map(float, p2))
                moved += 1
            if not moved:
                continue
            nl, nr = (new, R) if side == 0 else (L, new)
            if _crossed(nl, nr) > _crossed(L, R):
                continue
            t = type(c)(left=nl, right=nr, closed=c.closed)
            if _fans(t) > _fans(c):
                continue
            trial = list(cols)
            trial[k] = t
            b = _bare(outline, trial)
            if _poly(t).difference(ink).area - _poly(c).difference(ink).area > 0.1:
                continue
            if b > base - 0.5 * patch.area:
                continue
            if best is None or b < best[0]:
                best = (b, k, t, round(patch.area, 2), round(b - base, 2))
    return best


TIP_SPAN_MM = 3.0        # rungs this close to a free end are checked for fanning
TIP_REF_MM = (3.0, 7.0)  # ...against the rung direction this far from it
TIP_FAN_DEG = 25.0
TIP_MIN_WIDTH_MM = 3.0   # narrower columns (lettering) keep their tips
TIP_MIN_END_MM = 1.5     # a narrower end is a pointed tip: kept


def _ang(v):
    import math

    return math.degrees(math.atan2(v[1], v[0])) % 180.0


def _angdiff(a, b):
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def straighten_tips(planned, bare_tol: float = 0.3):
    """A free end whose last rungs fan from one corner (a blunt cap with a flat side: every stitch
    radiating from the corner) is re-paired with rungs parallel to the column's own rung direction a
    little way in, swept across the end section to where it gets too thin (MIN_TIP_MM, 0.4). Kept
    only if it leaves at most bare_tol mm2 more bare and no crossed pairs. check["tips_straightened"]."""
    import numpy as np
    from shapely.geometry import LineString, Point, Polygon

    outline = _outline(planned.contours)
    cols = planned.columns
    if outline is None:
        return planned
    done = []
    for k, c in enumerate(cols):
        if c.closed or len(c.left) < 4:
            continue
        for end in (0, -1):
            L, R = list(c.left), list(c.right)
            if end == 0:
                L, R = L[::-1], R[::-1]
            mids = np.array([((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(L, R)])
            s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(mids, axis=0).T))])
            dist = s[-1] - s
            if s[-1] < TIP_REF_MM[1] + 1:
                continue
            ref = [np.subtract(R[i], L[i]) for i in range(len(L)) if TIP_REF_MM[0] <= dist[i] <= TIP_REF_MM[1]]
            if not ref:
                continue
            refv = np.mean([v / (np.hypot(*v) or 1) * (1 if np.dot(v, ref[0]) >= 0 else -1) for v in ref], axis=0)
            ra = _ang(refv)
            tip = [i for i in range(len(L)) if dist[i] < TIP_SPAN_MM]
            if not tip or max(_angdiff(_ang(np.subtract(R[i], L[i])), ra) for i in tip) < TIP_FAN_DEG:
                continue
            # Only a blunt cap on a wide column (a strand's rounded end with a flat side, every stitch
            # radiating from its corner). A pointed tip (a leaf, a tapering strand: a narrow end) keeps
            # its point, a slanted end cut (wider than the column) keeps its blend, and lettering-size
            # columns are left alone.
            widths = [float(np.hypot(*np.subtract(a, b))) for a, b in zip(L, R)]
            med = float(np.median(widths))
            if med < TIP_MIN_WIDTH_MM or not (TIP_MIN_END_MM <= widths[-1] <= med):
                continue
            i0 = tip[0] - 1 if tip[0] > 0 else tip[0]
            # the end section's outline: rails from i0 to the end, closed by the end rung
            sec = Polygon(L[i0:] + R[i0:][::-1]).buffer(0)
            if sec.is_empty or sec.area < 0.5:
                continue
            axis = np.array([-refv[1], refv[0]]) / (np.hypot(*refv) or 1)
            if np.dot(mids[-1] - mids[i0], axis) < 0:
                axis = -axis
            o = mids[i0]
            span = float(max(np.dot(np.subtract(q, o), axis) for q in sec.exterior.coords))
            w = 4 * max(np.hypot(*np.subtract(a, b)) for a, b in zip(L, R)) + 4
            newL, newR = [], []
            for t in np.arange(0.5, span + 1e-9, 0.5):
                cpt = o + axis * t
                ln = LineString([tuple(cpt - refv * w), tuple(cpt + refv * w)])
                x = ln.intersection(sec)
                if x.is_empty or x.length < 0.4:
                    break
                seg = max(getattr(x, "geoms", [x]), key=lambda g: g.length)
                a, b = np.asarray(seg.coords[0]), np.asarray(seg.coords[-1])
                if np.dot(b - a, np.subtract(R[i0], L[i0])) < 0:
                    a, b = b, a
                newL.append(tuple(map(float, a)))
                newR.append(tuple(map(float, b)))
            if len(newL) < 2:
                continue
            L2, R2 = L[:i0 + 1] + newL, R[:i0 + 1] + newR
            if _crossed(L2, R2) > _crossed(L, R):
                continue
            if end == 0:
                L2, R2 = L2[::-1], R2[::-1]
            t_ = type(c)(left=L2, right=R2, closed=c.closed)
            if _fans(t_) > _fans(c):
                continue
            trial = list(cols)
            trial[k] = t_
            if _bare(outline, trial) > _bare(outline, cols) + bare_tol:
                _dbg(planned.char, "tip", k, end, "more bare", round(_bare(outline, trial) - _bare(outline, cols), 2))
                continue
            cols[k] = t_
            done.append({"column": k, "end": end})
            _dbg(planned.char, "tip straightened", k, end)
    if done:
        planned.check["tips_straightened"] = done
    return planned


def tidy_joins(planned, keep_mm: float = KEEP_MM, max_depth_mm: float = MAX_DEPTH_MM):
    """trim_overlaps then close_wedges, on one planned shape."""
    if not planned.ok or not planned.columns:
        return planned
    planned = trim_overlaps(planned, keep_mm, max_depth_mm)
    planned = straighten_tips(planned)
    planned = close_wedges(planned, keep_mm)
    trimmed = planned.check.get("joins_trimmed", [])
    planned = trim_overlaps(planned, keep_mm, max_depth_mm)       # a widened side may now run deep
    if trimmed and planned.check.get("joins_trimmed") is not trimmed:
        planned.check["joins_trimmed"] = trimmed + planned.check.get("joins_trimmed", [])
    return planned
