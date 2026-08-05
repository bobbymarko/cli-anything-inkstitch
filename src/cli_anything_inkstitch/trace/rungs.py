"""Suggest satin rungs for a fill shape — imitating a human digitizer.

The celtic-patch breakthrough (skills/embroidery-digitization/SKILL.md §15)
was the ARTIST's rung placement driving the engine's fill_to_satin: one
rung across any straight stretch, dense clusters through tight turns,
always roughly perpendicular to the stroke's local axis. Every
skeleton-derived RAIL set we generated lost to that input — but the
skeleton is still the right tool for placing RUNGS, because a rung only
needs a position and a normal, not clean geometry.

Pipeline: rasterize the fill (even-odd holes) → Zhang-Suen skeleton +
chamfer distance transform (trace/satinize.py) → branches → walk each
branch emitting a rung when the accumulated TURN exceeds `turn_deg` or the
arc length exceeds `spacing`. Rung length = local width both sides plus
margin, so it safely crosses both edges — the engine matches rungs to
fills by intersection (lib/extensions/fill_to_satin.py).

This module only SUGGESTS: callers append the rungs as visible red guide
strokes for human review before any conversion. The review step is the
point — do not wire this straight into fill_to_satin.
"""

from __future__ import annotations

import math

from cli_anything_inkstitch.trace.satinize import (
    distance_transform,
    extract_branches,
    merge_collinear_branches,
    skeletonize,
)

RUNG_COLOR = "#ed2024"        # the review convention (artist's rung red)


def _flatten_subpaths(d: str, per_curve: int = 16):
    import re
    from cli_anything_inkstitch.artifact.gate import flatten_path
    rings = []
    for chunk in [c for c in re.split(r"(?=[Mm])", (d or "").strip()) if c.strip()]:
        pts = flatten_path(chunk, per_curve=per_curve)
        clean = [pts[0]] if pts else []
        for p in pts[1:]:
            if math.dist(p, clean[-1]) > 1e-6:
                clean.append(p)
        if len(clean) >= 3:
            rings.append(clean)
    return rings


def _ring_area(ring) -> float:
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def _point_in_rings(pt, rings) -> bool:
    x, y = pt
    inside = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            if (y1 > y) != (y2 > y):
                if x1 + (y - y1) / (y2 - y1) * (x2 - x1) > x:
                    inside = not inside
    return inside


def _mask_from_rings(rings, resolution: float):
    """Binary mask of the fill at `resolution` px per doc unit (even-odd)."""
    from PIL import Image, ImageDraw
    xs = [x for r in rings for x, _ in r]
    ys = [y for r in rings for _, y in r]
    pad = 2.0 / resolution
    x0, y0 = min(xs) - pad, min(ys) - pad
    w = int((max(xs) - x0 + pad) * resolution) + 2
    h = int((max(ys) - y0 + pad) * resolution) + 2
    img = Image.new("1", (w, h), 0)
    draw = ImageDraw.Draw(img)
    ordered = sorted(rings, key=_ring_area, reverse=True)
    for ring in ordered:
        pts = [((x - x0) * resolution, (y - y0) * resolution) for x, y in ring]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        probe = (cx / resolution + x0, cy / resolution + y0)
        outer_hits = sum(1 for other in ordered
                         if other is not ring and _ring_area(other) > _ring_area(ring)
                         and _point_in_rings(probe, [other]))
        draw.polygon(pts, fill=0 if outer_hits % 2 else 1)
    mask = {(x, y) for y in range(h) for x in range(w) if img.getpixel((x, y))}
    return mask, (x0, y0)


def suggest_rungs(d: str, *, spacing: float = 30.0, turn_deg: float = 20.0,
                  min_spacing: float = 4.0, resolution: float = 4.0,
                  margin_frac: float = 0.3):
    """Rung segments for a fill path `d` (document units).

    spacing / min_spacing are in document units (px docs: 30px ≈ 8mm).
    Returns a list of ((x1, y1), (x2, y2)) rung segments.
    """
    rings = _flatten_subpaths(d)
    if not rings:
        return []
    # keep the mask a sane size for big shapes
    xs = [x for r in rings for x, _ in r]
    ys = [y for r in rings for _, y in r]
    extent = max(max(xs) - min(xs), max(ys) - min(ys))
    res = resolution if extent * resolution <= 2000 else 2000.0 / extent
    mask, (ox, oy) = _mask_from_rings(rings, res)
    if not mask:
        return []
    skel = skeletonize(mask)
    radii = distance_transform(mask)
    branches = merge_collinear_branches(extract_branches(skel, radii))

    rungs = []
    for br in branches:
        if len(br) < 3:
            continue
        # skip half the local width at each end — a rung on a terminal or
        # junction crosses the neighboring branch's territory and mis-cuts
        r_start = radii.get(br[0], 1.0)
        r_end = radii.get(br[-1], 1.0)
        lo, hi = 0, len(br) - 1
        acc = 0.0
        while lo < hi and acc < r_start:
            acc += math.dist(br[lo], br[lo + 1])
            lo += 1
        acc = 0.0
        while hi > lo and acc < r_end:
            acc += math.dist(br[hi - 1], br[hi])
            hi -= 1
        seg = br[lo:hi + 1]
        if len(seg) < 2:
            mid = br[len(br) // 2]
            seg = [mid]
        placed_arc = None       # arc position of the last rung
        arc = 0.0
        turn = 0.0
        prev_ang = None
        span = 3                # tangent smoothing window (mask px indices)

        def tangent(i):
            a = seg[max(0, i - span)]
            b = seg[min(len(seg) - 1, i + span)]
            tx, ty = b[0] - a[0], b[1] - a[1]
            length = math.hypot(tx, ty) or 1.0
            return tx / length, ty / length

        def emit(i):
            px_, py_ = seg[i]
            tx, ty = tangent(i)
            nx, ny = -ty, tx
            r = max(radii.get((round(px_), round(py_)), 1.0), 1.0)
            half = r * (1.0 + margin_frac) + 1.0
            p1 = ((px_ + nx * half) / res + ox, (py_ + ny * half) / res + oy)
            p2 = ((px_ - nx * half) / res + ox, (py_ - ny * half) / res + oy)
            rungs.append((p1, p2))

        min_spacing_px = min_spacing * res
        spacing_px = spacing * res
        for i in range(len(seg)):
            if i > 0:
                arc += math.dist(seg[i - 1], seg[i])
            tx, ty = tangent(i)
            ang = math.atan2(ty, tx)
            if prev_ang is not None:
                da = abs(ang - prev_ang)
                if da > math.pi:
                    da = 2 * math.pi - da
                turn += da
            prev_ang = ang
            due = (placed_arc is None
                   or (arc - placed_arc >= min_spacing_px
                       and (turn >= math.radians(turn_deg)
                            or arc - placed_arc >= spacing_px)))
            if due:
                emit(i)
                placed_arc = arc
                turn = 0.0
        # every branch ends with a rung near its far end so the last
        # section is bounded (unless one was just placed there)
        if placed_arc is None or arc - placed_arc > min_spacing_px:
            emit(len(seg) - 1)
    return rungs
