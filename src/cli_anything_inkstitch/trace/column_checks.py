"""Measurable satin-column defects: fans and hard seams.

A planned column is a list of (left, right) pairs in mm. The engine lays its stitches between
consecutive pairs in proportion along each rail, so the column's look follows from the pairs alone:

* **Fan.** Where one rail moves a lot while the other barely moves, every stitch in that stretch
  starts from nearly the same point and the stitches radiate like a fan: thread piles on the short
  side and gaps open on the long one. A smooth curve is not a fan (an O's inner rail is shorter
  than its outer, ~1:2 to 1:4); a pivot is (the short rail stands still while the long one sweeps).
  Measured over a window: the short rail's run reaching FAN_SHORT_MM while the long one's passes
  FAN_RATIO times that.
* **Hard seam.** Two columns of one shape meeting end to end where the outline has no drawn
  corner: one stroke sewn as two columns, as an S cut at its bowls. The stitch angle jumps and a
  line shows across what should read as one stroke. Columns meeting end to end at a drawn corner
  (an L's elbow, an N's points) are a corner join, which is how such a shape is sewn; a column
  ending against another's side (an H's bar) is an ordinary butt and isn't listed. A ring's start
  is listed as its seam so a reviewer can see where it is.

Pure geometry (shapely), no planner or engine calls, so a review can run on any satin columns.
"""

from __future__ import annotations

import math

FAN_SHORT_MM = 0.5      # the window: the short rail runs this far...
FAN_RATIO = 8.0         # ...while the long rail runs this many times as far (a pivot; a tight
                        # bowl's inner curve runs ~1:3 to 1:6 and is not one)
FAN_MIN_LONG_MM = 2.0   # and at least this far: tiny tips can't show a fan
JOIN_GAP_WIDTHS = 1.0   # two column ends this close (in the columns' width) meet end to end
CORNER_SPAN_MM = 0.6    # a drawn corner: the outline turns CORNER_TURN_DEG within this either side
CORNER_TURN_DEG = 45.0
CORNER_NEAR_WIDTHS = 0.9  # ...within this many column widths of where two columns meet


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _mid(pair):
    (lx, ly), (rx, ry) = pair
    return ((lx + rx) / 2, (ly + ry) / 2)


def fans(pairs) -> list[dict]:
    """Stretches of one column where its stitches radiate from a point. Each:
    {"at": (x, y) mm of the short rail's pivot, "short_mm", "long_mm"}. Overlapping windows are
    reported once."""
    if len(pairs) < 2:
        return []
    L = [p[0] for p in pairs]
    R = [p[1] for p in pairs]
    sl = [_d(a, b) for a, b in zip(L, L[1:])]
    sr = [_d(a, b) for a, b in zip(R, R[1:])]
    out, i, n = [], 0, len(sl)
    while i < n:
        a = b = 0.0
        j, hit = i, None
        while j < n and min(a, b) < FAN_SHORT_MM:
            a += sl[j]
            b += sr[j]
            j += 1
            short, long_ = min(a, b), max(a, b)
            if long_ >= FAN_MIN_LONG_MM and long_ >= FAN_RATIO * max(short, 1e-6):
                hit = (L if a < b else R, short, long_)
        if hit is not None:
            rail, short, long_ = hit
            if out and out[-1]["_end"] == i:        # the same fan running on: one report
                out[-1].update(_end=j, short_mm=round(out[-1]["short_mm"] + short, 2),
                               long_mm=round(out[-1]["long_mm"] + long_, 2))
            else:
                out.append({"at": tuple(round(v, 2) for v in rail[i]), "short_mm": round(short, 2),
                            "long_mm": round(long_, 2), "_end": j})
            i = j
        else:
            i += 1
    for f in out:
        del f["_end"]
    return out


def sharp_corners(contours, span_mm: float = CORNER_SPAN_MM, turn_deg: float = CORNER_TURN_DEG):
    """Points where the outline turns at least turn_deg within span_mm either side: a drawn corner
    (an L's elbow, a D's stem meeting its bowl), not a curve (an S's bowl). One point per corner."""
    out = []
    for c in contours:
        n = len(c)
        if n < 5:
            continue
        seg = [_d(c[k], c[(k + 1) % n]) for k in range(n)]
        hits = []
        for k in range(n):
            a = b = k
            la = lb = 0.0
            while la < span_mm and (k - a) < n // 2:
                la += seg[(a - 1) % n]
                a -= 1
            while lb < span_mm and (b - k) < n // 2:
                lb += seg[b % n]
                b += 1
            p0, p1, p2 = c[a % n], c[k], c[b % n]
            v1 = (p1[0] - p0[0], p1[1] - p0[1])
            v2 = (p2[0] - p1[0], p2[1] - p1[1])
            n1, n2 = math.hypot(*v1), math.hypot(*v2)
            if n1 == 0 or n2 == 0:
                continue
            cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
            if math.degrees(math.acos(max(-1.0, min(1.0, cos)))) >= turn_deg:
                hits.append(k)
        group = []           # neighbouring hits within span_mm are one corner: keep its middle
        for k in hits + [None]:
            if group and (k is None or k != group[-1] + 1 or _d(c[k], c[group[-1]]) > span_mm):
                out.append(tuple(c[group[len(group) // 2]]))
                group = []
            if k is not None:
                group.append(k)
    return out


def end_joints(columns, max_gap_widths: float = JOIN_GAP_WIDTHS) -> list[tuple]:
    """Pairs of open-column ends that meet end to end: ((i, at_start), (j, at_start), midpoint,
    width). The nearest other end within max_gap_widths of the columns' width; each end used once."""
    ends = []
    for i, pairs in enumerate(columns):
        if len(pairs) >= 2:
            for at_start in (True, False):
                e = pairs[0] if at_start else pairs[-1]
                ends.append(((i, at_start), _mid(e), max(_d(*e), 0.3), pairs))
    used, out = set(), []
    cand = []
    for x in range(len(ends)):
        for y in range(x + 1, len(ends)):
            (i, sa), m1, w1, _ = ends[x]
            (j, sb), m2, w2, _ = ends[y]
            if i == j:
                continue
            w = _width_near(ends[x][3], sa, ends[y][3], sb)
            g = _d(m1, m2)
            if g <= max_gap_widths * w:
                cand.append((g, x, y, w))
    for g, x, y, w in sorted(cand):
        if x in used or y in used:
            continue
        used |= {x, y}
        m1, m2 = ends[x][1], ends[y][1]
        out.append((ends[x][0], ends[y][0], ((m1[0] + m2[0]) / 2, (m1[1] + m2[1]) / 2), w))
    return out


def _width_near(p1, s1, p2, s2):
    """The columns' width a few pairs in from the joined ends (the end pairs themselves are cut)."""
    def w(pairs, at_start):
        k = min(2, len(pairs) - 1)
        return _d(*(pairs[k] if at_start else pairs[-1 - k]))
    return max(0.3, (w(p1, s1) + w(p2, s2)) / 2)


def seams(columns, contours, closed=None) -> list[dict]:
    """Where a shape's columns meet. columns: list of pair lists; contours: the shape's outline
    (mm, outer first, holes after); closed: per column, True for a ring. Each item:
    {"kind", "at": (x, y), "columns": [i, j]} with kind one of
      hard_seam   - two columns end to end where the outline has no corner: one stroke cut in two;
      corner_join - two columns end to end at a drawn corner (expected);
      ring_seam   - where a ring starts and ends."""
    closed = closed or [False] * len(columns)
    out = []
    for i, pairs in enumerate(columns):
        if closed[i] and len(pairs) >= 2:
            out.append({"kind": "ring_seam", "at": tuple(round(v, 2) for v in _mid(pairs[0])), "columns": [i]})
    opened = [p if not closed[k] else [] for k, p in enumerate(columns)]
    corners = sharp_corners(contours)
    for (i, _), (j, _), m, w in end_joints(opened):
        near = any(_d(c, m) <= CORNER_NEAR_WIDTHS * w for c in corners)
        out.append({"kind": "corner_join" if near else "hard_seam",
                    "at": tuple(round(v, 2) for v in m), "columns": [i, j]})
    return out
