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
import re

from cli_anything_inkstitch.trace.satinize import (
    distance_transform,
    extract_branches,
    merge_collinear_branches,
    skeletonize,
)

RUNG_COLOR = "#ed2024"        # the review convention (artist's rung red)


def segment_crosses_path(p1, p2, d: str) -> int:
    """How many times the segment crosses the path's boundary."""
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)
    n = 0
    for ring in _flatten_subpaths(d):
        for a, b in zip(ring, ring[1:]):
            if orient(p1, p2, a) != orient(p1, p2, b) and \
               orient(a, b, p1) != orient(a, b, p2):
                n += 1
    return n


def unrunged_fills(tree, ids: list[str]) -> list[str]:
    """Selected fills with NO selected rung crossing them twice.

    The compiled engine reports these with a GUI DIALOG on the user's
    desktop (invisible to a headless caller) and skips them — so callers
    must refuse the selection BEFORE invoking the engine.
    """
    from lxml import etree as _etree
    by_id = {e.get("id"): e for e in tree.getroot().iter() if e.get("id")}
    fills, strokes = [], []
    for i in ids:
        el = by_id.get(i)
        if el is None or not el.get("d"):
            continue
        if el.get("fill") not in (None, "none"):
            fills.append(el)
        else:
            strokes.append(el)
    bad = []
    for f in fills:
        hit = False
        for s in strokes:
            from cli_anything_inkstitch.artifact.gate import flatten_path
            pts = flatten_path(s.get("d"))
            if len(pts) >= 2 and segment_crosses_path(
                    pts[0], pts[-1], f.get("d")) >= 2:
                hit = True
                break
        if not hit:
            bad.append(f.get("id"))
    return bad


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


# ---------------------------------------------------------------- pairing rails of an edited satin

PAIR_RUNG_STEP_MM = 2.5        # one rung per this much column length (14 on a 35 mm hand-edited band)
PAIR_RUNG_OVERSHOOT_MM = 0.3   # each rung end runs this far past its rail, so it surely crosses it
PAIR_RUNG_SQUARE_DEG = 20.0    # a rung crosses each rail within this of square (and the rails run within
                               # this of parallel there), or it isn't placed


def pairing_rungs(rail_a, rail_b, units_per_mm: float, step_mm: float = PAIR_RUNG_STEP_MM,
                  overshoot_mm: float = PAIR_RUNG_OVERSHOOT_MM,
                  square_deg: float = PAIR_RUNG_SQUARE_DEG) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Rungs that pair two satin rails square across the column, without touching the rails.

    For a satin whose rails have different node counts and no rungs: the engine then invents
    rungs from the rails' own nodes (lib/elements/satin_column.py _synthesize_rungs: node i of
    one rail to node i of the other, ends dropped), so a hand-edited rail with extra nodes on one
    side pairs unrelated points and the zigzag slants or twists. Resampling the rails to equal
    counts would fix the pairing but moves the outline; explicit rungs fix it and leave the rails
    exactly as drawn.

    rail_a / rail_b: flattened rail points (any units); units_per_mm converts step_mm and
    overshoot_mm into them. A rung is tried every step_mm along the column's centre line (the
    series centred on the column), square to the centre line, from rail to rail, each end
    overshooting its rail by overshoot_mm. Only where the rails run alongside each other: a rung
    must cross each rail within square_deg of square, and the rails there run within square_deg
    of parallel, so a slanted or tapered end keeps the engine's own fan from the last rung to the
    rail ends (a band ending on a slanted edge, as drawn). Each must meet the engine's reading of a rung
    (lib/elements/satin_column.py rail_indices / flattened_sections / validation_warnings): it
    crosses each rail exactly once, crosses no other rung, and the rungs cut both rails in the
    same order. Rungs failing that are dropped. Two rungs exactly is ambiguous to the engine
    (TwoRungsWarning: a '#' shape whose rails it has to guess), so two become one. Returns
    [] when no rung fits (a very short column)."""
    from shapely.geometry import LineString

    A, B = LineString(rail_a), LineString(rail_b)
    if A.length == 0 or B.length == 0:
        return []
    a0, a1 = rail_a[0], rail_a[-1]
    b0, b1 = rail_b[0], rail_b[-1]
    Bc = B if math.dist(a0, b0) + math.dist(a1, b1) <= math.dist(a0, b1) + math.dist(a1, b0) \
        else LineString(list(rail_b)[::-1])
    n = 200
    centre = []
    for i in range(n + 1):
        p, q = A.interpolate(i / n, normalized=True), Bc.interpolate(i / n, normalized=True)
        centre.append(((p.x + q.x) / 2.0, (p.y + q.y) / 2.0))
    C = LineString(centre)
    step = step_mm * units_per_mm
    over = overshoot_mm * units_per_mm
    if C.length < step:
        return []
    reach = 4.0 * max(A.hausdorff_distance(Bc), units_per_mm)

    def tangent(rail, pt):
        t = rail.project(pt)
        q0 = rail.interpolate(max(t - 0.3 * units_per_mm, 0.0))
        q1 = rail.interpolate(min(t + 0.3 * units_per_mm, rail.length))
        rx, ry = q1.x - q0.x, q1.y - q0.y
        rl = math.hypot(rx, ry) or 1.0
        return rx / rl, ry / rl

    def hit(line, rail, c):
        x = line.intersection(rail)
        pts = [g for g in getattr(x, "geoms", [x]) if g.geom_type == "Point" and not g.is_empty]
        return min(pts, key=lambda g: g.distance(c)) if pts else None

    lim = math.sin(math.radians(square_deg))
    out, cuts = [], []
    k = int((C.length - step) // step) + 1
    first = (C.length - (k - 1) * step) / 2.0          # centre the rungs on the column
    for j in range(k):
        s = first + j * step
        c = C.interpolate(s)
        p0 = C.interpolate(max(s - 0.5 * units_per_mm, 0.0))
        p1 = C.interpolate(min(s + 0.5 * units_per_mm, C.length))
        tx, ty = p1.x - p0.x, p1.y - p0.y
        tl = math.hypot(tx, ty)
        if tl == 0:
            continue
        nx, ny = -ty / tl, tx / tl
        probe = LineString([(c.x - nx * reach, c.y - ny * reach), (c.x + nx * reach, c.y + ny * reach)])
        pa, pb = hit(probe, A, c), hit(probe, B, c)
        if pa is None or pb is None:
            continue
        ax, ay, bx, by = pa.x, pa.y, pb.x, pb.y
        w = math.hypot(bx - ax, by - ay)
        if w == 0:
            continue
        ux, uy = (bx - ax) / w, (by - ay) / w
        rung = ((ax - ux * over, ay - uy * over), (bx + ux * over, by + uy * over))
        ls = LineString(rung)
        if ls.intersection(A).geom_type != "Point" or ls.intersection(B).geom_type != "Point":
            continue
        if any(ls.intersects(LineString(r)) for r in out):
            continue
        (tax, tay), (tbx, tby) = tangent(A, pa), tangent(B, pb)
        # square to each rail, and the rails alongside each other (not an end cut or a taper)
        if abs(tax * ux + tay * uy) > lim or abs(tbx * ux + tby * uy) > lim \
                or abs(tax * tby - tay * tbx) > lim:
            continue
        ta, tb = A.project(pa), B.project(pb)
        if cuts and ta <= cuts[-1][0]:                  # must move on along rail A
            continue
        out.append(rung)
        cuts.append((ta, tb))
    # the rungs must cut rail B in one consistent order too (else sections pair across a twist)
    if len(cuts) > 1:
        tbs = [t for _, t in cuts]
        inc = sum(1 for x, y in zip(tbs, tbs[1:]) if y > x) >= len(tbs) / 2
        keep, last = [], None
        for r, t in zip(out, tbs):
            if last is None or (t > last if inc else t < last):
                keep.append(r)
                last = t
        out = keep
    if len(out) == 2:
        out = out[:1]
    return out


def pair_unpaired_rails(d: str, units_per_mm: float, **kw) -> tuple[str, int]:
    """A satin path's `d` with pairing rungs added when its two rails have different node counts
    and it has no rungs (the gate's twist_risk), else unchanged. The rails' text is kept byte for
    byte; rungs are appended as extra subpaths, which is how the engine reads them
    (lib/elements/satin_column.py rail_indices: subpaths crossing exactly two others are rungs).
    Returns (d, rungs added)."""
    from cli_anything_inkstitch.artifact.gate import flatten_path, rail_node_count

    subs = [x.strip() for x in re.split(r"(?=[Mm])", d or "") if x.strip()]
    if len(subs) != 2 or rail_node_count(subs[0]) == rail_node_count(subs[1]):
        return d, 0
    a, b = flatten_path(subs[0]), flatten_path(subs[1])
    if len(a) < 2 or len(b) < 2:
        return d, 0
    rungs = pairing_rungs(a, b, units_per_mm, **kw)
    if not rungs:
        return d, 0
    return d.rstrip() + "".join(f" M {p[0]:.3f},{p[1]:.3f} L {q[0]:.3f},{q[1]:.3f}" for p, q in rungs), len(rungs)
