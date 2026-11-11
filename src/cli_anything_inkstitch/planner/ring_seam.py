# SPDX-License-Identifier: Apache-2.0
# Derived from stitchpilot (same author, Apache-2.0), moved here so the column planner has one home.
# Public names, signatures and return types are kept as they were there, so code written against it
# keeps working; new behaviour goes behind new parameters whose defaults reproduce the old output.
"""A ring's seam, edited in place: the geometry.

EmbroideryStudio can't move where a satin ring (an O, a 0) starts, or how far it sews on past its start:
both are part of the object's digitized points, and Reshape means dragging nodes on the canvas. So the
`ring_seam` revision edit rebuilds that one object from its own stitches, not from a font:

1. its satin, read from the design's machine file (the longest run of needle points inside its box);
2. its two edges, recovered around the ring's middle (needle points alternate between them), less the
   pull compensation ES added, so the new column's edges are the old one's digitized edges;
3. new pairs from the seam asked for, round the ring once, then on past the start by the overlap asked
   for, in the direction the old ring was sewn.

The rest (its stitch settings, thread and place in the sewing order) is copied by the recipe, and a check
holds every other object to its old stitches. Works for rings the ring's middle can see all of (O, 0, Q's
bowl); a ring folded round on itself isn't one.
"""

from __future__ import annotations

import math
import re

import numpy as np

BIN_DEG = 3.0            # the edges are read in slices of this many degrees round the ring
WIDTH_BINS = 4           # ...the ring's width at a slice read over this many slices either side
SMOOTH_BINS = 2          # ...averaged with this many slices either side
EDGE_SHARE = 0.25        # needle points in the middle quarter of the ring's width either side are underlay
RING_COVER = 0.35         # each edge must show in at least this share of the slices
PAIR_STEP_MM = 0.8       # new pairs this far apart along the ring's middle (as flat lettering's curves)
NAMED = {"right": 0.0, "top_right": 45.0, "top": 90.0, "top_left": 135.0, "left": 180.0,
         "bottom_left": 225.0, "bottom": 270.0, "bottom_right": 315.0}


def seam_angle(spec) -> float:
    """Degrees counter-clockwise from the right (3 o'clock): a number, a name (left, top_right, ...) or
    a clock position ("7 o'clock")."""
    if isinstance(spec, (int, float)):
        return float(spec) % 360.0
    s = str(spec).strip().lower().replace("'", "").replace("-", "_").replace(" ", "_")
    if s in NAMED:
        return NAMED[s]
    m = re.fullmatch(r"(\d{1,2}(?:\.\d+)?)_?o_?clock", s)
    if m:
        h = float(m.group(1)) % 12
        return (90.0 - 30.0 * h) % 360.0
    raise ValueError(f"seam {spec!r}: give degrees, one of {sorted(NAMED)}, or a clock position like '7 o'clock'")


def object_run(points: np.ndarray, box: tuple[float, float, float, float], margin: float = 0.8) -> np.ndarray:
    """The longest unbroken run of needle points inside a box (x, y, w, h: centre and size), in order:
    one object's own stitches (others only pass through)."""
    x, y, w, h = box
    inside = (np.abs(points[:, 0] - x) <= w / 2 + margin) & (np.abs(points[:, 1] - y) <= h / 2 + margin)
    best, start = (0, 0), None
    for i, v in enumerate(list(inside) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    return points[best[0]:best[1]]


def _circular_smooth(v: np.ndarray, k: int) -> np.ndarray:
    if k <= 0:
        return v
    n = len(v)
    out = np.empty(n)
    for i in range(n):
        idx = [(i + j) % n for j in range(-k, k + 1)]
        vals = v[idx]
        vals = vals[~np.isnan(vals)]
        out[i] = vals.mean() if len(vals) else np.nan
    return out


def _fill_gaps(v: np.ndarray) -> np.ndarray:
    """Empty slices from their neighbours, round the ring."""
    n = len(v)
    ok = ~np.isnan(v)
    if ok.all():
        return v
    xs = np.arange(n)
    good = xs[ok]
    return np.interp(xs, np.concatenate([good - n, good, good + n]), np.tile(v[ok], 3))


def recover_ring(run: np.ndarray, pull_comp_mm: float = 0.0) -> dict:
    """The ring's centre, its outer and inner edge (radius at each slice angle) and the way it was sewn
    (+1 counter-clockwise, -1 clockwise), from its needle points. The edges are the digitized ones: the
    needle points sit pull_comp_mm outside them, along the stitch."""
    c = (run.min(axis=0) + run.max(axis=0)) / 2
    d = run - c
    r = np.hypot(*d.T)
    a = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 360.0
    nb = int(round(360.0 / BIN_DEG))
    b = np.minimum((a / BIN_DEG).astype(int), nb - 1)
    # The ring's width at each slice, read over the slices round it: one slice may hold a single
    # edge's needle point and an underlay point, which alone would pass for the two edges.
    lo_k, hi_k = np.full(nb, np.nan), np.full(nb, np.nan)
    for k in range(nb):
        near = np.isin(b, [(k + j) % nb for j in range(-WIDTH_BINS, WIDTH_BINS + 1)])
        if near.sum() >= 3:
            lo_k[k], hi_k[k] = r[near].min(), r[near].max()
    outer, inner = np.full(nb, np.nan), np.full(nb, np.nan)
    for k in range(nb):
        rk = r[b == k]
        lo, hi = lo_k[k], hi_k[k]
        if len(rk) == 0 or np.isnan(lo) or hi - lo < 0.2:
            continue
        span = hi - lo
        o = rk[rk > lo + (0.5 + EDGE_SHARE) * span]
        i_ = rk[rk < lo + (0.5 - EDGE_SHARE) * span]
        if len(o):
            outer[k] = np.median(o)
        if len(i_):
            inner[k] = np.median(i_)
    if min(np.mean(~np.isnan(outer)), np.mean(~np.isnan(inner))) < RING_COVER:
        raise ValueError("not a ring: its stitches don't go round a middle")
    outer = _circular_smooth(_fill_gaps(outer), SMOOTH_BINS) - pull_comp_mm
    inner = _circular_smooth(_fill_gaps(inner), SMOOTH_BINS) + pull_comp_mm
    if np.any(inner >= outer):
        raise ValueError("couldn't read the ring's two edges from its stitches")
    # the way round it was sewn: the angle's steady drift through the run (satin zigzags across, moving on)
    turn = np.unwrap(np.radians(a))
    direction = 1 if turn[-1] - turn[0] >= 0 else -1
    return {"centre": (float(c[0]), float(c[1])), "outer": outer, "inner": inner, "direction": direction,
            "bin_deg": 360.0 / nb}


def _edge_at(edge: np.ndarray, deg: float, bin_deg: float) -> float:
    x = (deg % 360.0) / bin_deg - 0.5
    i0 = int(math.floor(x)) % len(edge)
    f = x - math.floor(x)
    return float(edge[i0] * (1 - f) + edge[(i0 + 1) % len(edge)] * f)


def ring_pairs(ring: dict, seam_deg: float, overlap_mm: float, step_mm: float = PAIR_STEP_MM):
    """Pairs (outer, inner) round the ring from the seam, in its sewing direction, then on past the
    start by overlap_mm along its middle. Returns (pairs, curve flags, ring_end index)."""
    cx, cy = ring["centre"]
    sgn, bd = ring["direction"], ring["bin_deg"]

    def pair(deg):
        ro, ri = _edge_at(ring["outer"], deg, bd), _edge_at(ring["inner"], deg, bd)
        u = (math.cos(math.radians(deg)), math.sin(math.radians(deg)))
        return ((cx + ro * u[0], cy + ro * u[1]), (cx + ri * u[0], cy + ri * u[1])), (ro + ri) / 2

    # the angles: every step_mm along the middle, the last landing on the start
    angles, deg, travelled = [0.0], 0.0, 0.0
    while True:
        _, rm = pair(seam_deg + sgn * deg)
        deg += math.degrees(step_mm / max(rm, 1e-6))
        if deg >= 360.0 - 1e-6:
            break
        angles.append(deg)
    n_loop = len(angles)
    angles.append(360.0)
    ring_end = n_loop
    if overlap_mm > 0:
        _, rm = pair(seam_deg)
        extra = math.degrees(overlap_mm / max(rm, 1e-6))
        angles.append(360.0 + extra)
    pairs = [pair(seam_deg + sgn * a)[0] for a in angles]
    # the start, the closing pair (a curve point, the seam mid-column) and the end are corners;
    # the closing pair is the last one when there's no overlap
    curve = [False] + [True] * (len(pairs) - 2) + [False]
    return pairs, curve, ring_end


def seam_density(run: np.ndarray, window_mm: float = 1.2) -> dict:
    """The ring's satin at its densest stretch against the rest: full-width stitches (at least half the
    ring's typical stitch) counted by their middles round the ring, in windows of window_mm along its
    middle. A seam sewn twice over, with ties on top, stands out (the hat's O: 2-3x)."""
    seg = np.diff(run, axis=0)
    L = np.hypot(*seg.T)
    if len(L) < 20:
        return {"ratio": 0.0, "densest": 0, "typical": 0.0, "at_deg": None}
    full = L >= 0.5 * np.median(L[L > 0.3]) if np.any(L > 0.3) else L > 0
    mid = (run[:-1] + run[1:]) / 2
    c = (run.min(axis=0) + run.max(axis=0)) / 2
    r_mid = float(np.median(np.hypot(*(mid[full] - c).T)))
    deg = (np.degrees(np.arctan2(mid[full, 1] - c[1], mid[full, 0] - c[0])) % 360.0)
    step = math.degrees(window_mm / max(r_mid, 1e-6))
    k = max(4, int(360.0 // step))
    counts = np.histogram(deg, bins=k, range=(0, 360))[0]
    typical = float(np.median(counts))
    i = int(np.argmax(counts))
    return {"ratio": round(float(counts[i]) / typical, 2) if typical else 0.0, "densest": int(counts[i]),
            "typical": typical, "at_deg": round((i + 0.5) * 360.0 / k, 1)}
