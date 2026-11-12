# SPDX-License-Identifier: Apache-2.0
# Derived from stitchpilot (same author, Apache-2.0), moved here so the column planner has one home.
# Public names, signatures and return types are kept as they were there, so code written against it
# keeps working; new behaviour goes behind new parameters whose defaults reproduce the old output.
"""Satin columns from a font's letter outlines, for digitizing with EmbroideryStudio's Column A tool.

ES's own lettering decides where each letter's columns start, end and overlap. Puff needs that under
control: each column end perforates the foam, and two ends on one spot cut it through. Here each letter
is described as strokes, in sewing order. A stroke is a centreline in the letter's ink box (u, v from
0 to 1, v up). Its two edges (rails) are found by casting rays from the centreline, perpendicular to
it, to the letter's outline. Where a ray runs on into another stroke (a leg at the crossbar of an H),
it's much longer than its neighbours, and the edge is interpolated across: so strokes butt along the
line where their edges would be, and don't overlap.

Ends: a stroke's first and last pair are cut perpendicular to the centreline, or along a vertical or
horizontal line, so a diagonal can butt square against a leg.

Everything here is geometry, in mm, y up. `ops/columns.py` turns pairs into clicks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

DEFAULT_SPACING_MM = 2.5     # between pairs along a curved column
OUTLIER = 1.35               # a ray this much longer than the median for its side ran into another stroke
OUTLIER_STRAIGHT = 1.05      # straight strokes (legs, bars) keep one width, so a little longer already means that
END_INSET_MM = 0.02          # end samples sit this far inside, so rays don't run along an edge


@dataclass
class Stroke:
    path: list[tuple[float, float]]
    smooth: bool = False
    closed: bool = False
    start_cut: str = "perpendicular"     # or "vertical", "horizontal"
    end_cut: str = "perpendicular"


@dataclass
class Column:
    pairs: list[tuple[tuple[float, float], tuple[float, float]]]   # (left, right) edge points, mm
    curve: list[bool]                                               # right-click (curve) points
    closed: bool = False
    ring_end: int | None = None      # a ring's closing pair (same as pairs[0]); later pairs are its overlap
    overlap_mm: float = 0.0          # how far a ring sews on past its start, along its centre
    # The pairs as clicked into ES, when they differ from the planned shape: ends pushed past the
    # baseline or cap line against pull-in (compensate_pull_in). Checks use `pairs`, the font's shape.
    sew_pairs: list | None = None

    def quads(self):
        from shapely.geometry import Polygon

        out = []
        for (l0, r0), (l1, r1) in zip(self.pairs, self.pairs[1:]):
            q = Polygon([l0, l1, r1, r0])
            if not q.is_valid:
                q = q.buffer(0)
            out.append(q)
        return out

    def shape(self):
        from shapely.ops import unary_union

        return unary_union(self.quads())

    def curved_shape(self, per_seg: int = 6):
        """The column as EmbroideryStudio sews it: between two curve points each edge is a smooth
        curve through them, not the straight chord `shape` uses. On a round top (an O, a 2, a 6) the
        chords cut up to 0.14 mm2 off the letter that the sewn satin covers."""
        from shapely.geometry import Polygon
        from shapely.ops import unary_union

        n = len(self.pairs)
        if n < 3:
            return self.shape()
        side = [np.array([p[k] for p in self.pairs], float) for k in (0, 1)]
        last = self.ring_end if self.closed and self.ring_end else None

        def before(i):
            if i > 0:
                return i - 1
            return last - 1 if last else None

        dense = [[], []]
        for i in range(n - 1):
            curved = self.curve[i] and self.curve[i + 1]
            b = before(i)
            a = i + 2 if i + 2 < n else (1 if last and i + 1 == last else None)
            for k in (0, 1):
                p1, p2 = side[k][i], side[k][i + 1]
                if not curved:
                    dense[k].append([p1])
                    continue
                p0 = side[k][b] if b is not None and self.curve[b] else 2 * p1 - p2
                p3 = side[k][a] if a is not None and self.curve[a] else 2 * p2 - p1
                t = np.arange(per_seg)[:, None] / per_seg
                dense[k].append(0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                                       + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
        ls = np.vstack(dense[0] + [side[0][-1:]])
        rs = np.vstack(dense[1] + [side[1][-1:]])
        quads = []
        for l0, r0, l1, r1 in zip(ls, rs, ls[1:], rs[1:]):
            q = Polygon([l0, l1, r1, r0])
            quads.append(q if q.is_valid else q.buffer(0))
        return unary_union(quads)


@dataclass
class Cap:
    """A short satin across an open column end, its stitches running along the column: a line of
    close needle holes right at the end edge, which perforates puff foam there and covers its cut."""
    column: int                       # index in the letter's columns
    end: str                          # "start" or "end"
    pairs: list[tuple[tuple[float, float], tuple[float, float]]]   # two (outer, inner) pairs, mm
    width_mm: float = 0.0

    def shape(self):
        from shapely.geometry import Polygon

        (o0, i0), (o1, i1) = self.pairs
        return Polygon([o0, i0, i1, o1])

    @property
    def centre(self) -> tuple[float, float]:
        return tuple(np.mean([p for pr in self.pairs for p in pr], axis=0))


@dataclass
class Letter:
    char: str
    columns: list[Column]
    outline: list[np.ndarray]        # closed contours, mm
    box: tuple[float, float, float, float]   # ink box x0, y0, x1, y1, mm
    strokes: list[Stroke] = field(default_factory=list)
    caps: list[Cap] = field(default_factory=list)            # in sewing order, after the columns
    runs: list[np.ndarray] = field(default_factory=list)     # outline paths in sewing order, after the caps
    butt_overlap: float = 0.2                                # how far butting ends reach in (BUTT_OVERLAP_MM)

    def glyph(self):
        """The letter's filled shape (outer contours minus counters)."""
        from shapely.geometry import Polygon

        polys = [Polygon(c) for c in self.outline if len(c) >= 3]
        polys = [p if p.is_valid else p.buffer(0) for p in polys]    # art outlines can touch themselves
        polys.sort(key=lambda p: -p.area)
        shape = None
        for p in polys:
            if shape is None:
                shape = p
            elif shape.contains(p.representative_point()):
                shape = shape.difference(p)
            else:
                shape = shape.union(p)
        return shape


# ---------------------------------------------------------------- fonts

def find_font_file(name: str, dirs: list[Path] | None = None) -> Path:
    """The TTF/OTF whose full name (or family + style) is `name`, e.g. "Lato Black"."""
    from fontTools.ttLib import TTFont

    import os
    dirs = dirs or [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    want = name.strip().lower()
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() not in (".ttf", ".otf"):
                continue
            try:
                names = TTFont(str(p), lazy=True)["name"]
            except Exception:  # noqa: BLE001 - unreadable font files are skipped
                continue
            full = (names.getDebugName(4) or "").lower()
            fam = f"{names.getDebugName(16) or names.getDebugName(1) or ''} {names.getDebugName(17) or names.getDebugName(2) or ''}"
            if want in (full, fam.strip().lower(), fam.replace(" Regular", "").strip().lower()):
                return p
    raise FileNotFoundError(f"no font file named {name!r} in {', '.join(str(d) for d in dirs)}")


class _Flatten:
    """fontTools pen that flattens curves into polylines."""

    def __init__(self, steps: int = 12):
        from fontTools.pens.basePen import BasePen

        outer = self

        class Pen(BasePen):
            def _moveTo(self, p):
                outer.cur = [p]

            def _lineTo(self, p):
                outer.cur.append(p)

            def _curveToOne(self, a, b, c):
                p0 = outer.cur[-1]
                for i in range(1, steps + 1):
                    t = i / steps
                    outer.cur.append(tuple((1 - t) ** 3 * p0[k] + 3 * (1 - t) ** 2 * t * a[k] + 3 * (1 - t) * t * t * b[k]
                                           + t ** 3 * c[k] for k in (0, 1)))

            def _qCurveToOne(self, a, b):
                p0 = outer.cur[-1]
                for i in range(1, steps + 1):
                    t = i / steps
                    outer.cur.append(tuple((1 - t) ** 2 * p0[k] + 2 * (1 - t) * t * a[k] + t * t * b[k] for k in (0, 1)))

            def _closePath(self):
                outer.contours.append(outer.cur)
                outer.cur = []

            _endPath = _closePath

        self.contours: list = []
        self.cur: list = []
        self.Pen = Pen


def glyph_outline(font_path: str | Path, char: str, height_mm: float) -> tuple[list[np.ndarray], float, float]:
    """The character's contours in mm (origin at the glyph's origin, y up), scaled so the font's
    capital height is `height_mm` (ES's lettering Height). Returns (contours, advance_mm, scale)."""
    from fontTools.ttLib import TTFont

    f = TTFont(str(font_path), fontNumber=0)
    gs, cmap = f.getGlyphSet(), f.getBestCmap()
    if ord(char) not in cmap:
        raise ValueError(f"{Path(font_path).name} has no glyph for {char!r}")
    cap = getattr(f["OS/2"], "sCapHeight", 0) or 0
    if not cap:
        raise ValueError(f"{Path(font_path).name} doesn't say how tall its capitals are")
    k = height_mm / cap
    fl = _Flatten()
    gs[cmap[ord(char)]].draw(fl.Pen(gs))
    contours = [np.array(c, float) * k for c in fl.contours if len(c) >= 3]
    return contours, f["hmtx"][cmap[ord(char)]][0] * k, k


# ---------------------------------------------------------------- centrelines

def _catmull_rom(pts: np.ndarray, closed: bool, per_seg: int = 24) -> np.ndarray:
    if closed:
        ext = np.vstack([pts[-1:], pts, pts[:2]])
    else:
        ext = np.vstack([2 * pts[0] - pts[1], pts, 2 * pts[-1] - pts[-2]])
    out = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for j in range(per_seg):
            t = j / per_seg
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(pts[0] if closed else pts[-1])
    return np.array(out)


def _resample(line: np.ndarray, spacing: float) -> np.ndarray:
    seg = np.hypot(*np.diff(line, axis=0).T)
    s = np.concatenate([[0], np.cumsum(seg)])
    n = max(1, int(round(s[-1] / spacing)))
    t = np.linspace(0, s[-1], n + 1)
    return np.column_stack([np.interp(t, s, line[:, 0]), np.interp(t, s, line[:, 1])])


def _seam_on_side(pts: np.ndarray) -> np.ndarray:
    """A ring starts (and, after its overlap, ends) on its left side at about mid-height: the seam's
    small bump hides in the curve's side, where a top seam on a cap stood out as a lump (a stitch-out
    of an O at 1.2 mm overlap). Same points, same direction, another first point."""
    mid = (pts[:, 1].min() + pts[:, 1].max()) / 2
    span = (pts[:, 0].max() - pts[:, 0].min()) or 1.0
    k = int(np.argmin(pts[:, 0] + 0.25 * span * np.abs(pts[:, 1] - mid) / ((pts[:, 1].max() - pts[:, 1].min()) or 1)))
    return np.vstack([pts[k:], pts[:k]])


def centreline(stroke: Stroke, box: tuple[float, float, float, float], spacing: float) -> np.ndarray:
    x0, y0, x1, y1 = box
    pts = np.array([(x0 + u * (x1 - x0), y0 + v * (y1 - y0)) for u, v in stroke.path], float)
    if stroke.closed and len(pts) >= 3:
        pts = _seam_on_side(pts)
    if stroke.smooth and len(pts) >= 3:
        line = _catmull_rom(pts, stroke.closed)
    else:
        line = np.vstack([pts, pts[:1]]) if stroke.closed else pts
    if not stroke.closed and len(line) >= 2:   # keep end samples just inside the ink
        for end, nxt in ((0, 1), (-1, -2)):
            d = line[nxt] - line[end]
            line[end] = line[end] + d / (np.hypot(*d) or 1) * END_INSET_MM
    return _resample(line, spacing)


# ---------------------------------------------------------------- rails

def _ray(boundary, p: np.ndarray, d: np.ndarray, far: float) -> float | None:
    """Distance from p along unit vector d to the first crossing of the outline."""
    from shapely.geometry import LineString

    hit = boundary.intersection(LineString([tuple(p), tuple(p + d * far)]))
    best = None
    for g in getattr(hit, "geoms", [hit]):
        for c in getattr(g, "coords", []):
            t = float(np.dot(np.array(c) - p, d))
            if t > 1e-6 and (best is None or t < best):
                best = t
    return best


RAY_FAR = 4.5                # a ray this many times the stroke's median ray, jumped to, ran on past the stroke
                             # (the D's were ~6x; at 3x an R's leg-to-bowl rays went too, and a spur showed)
JUMP_FRACTION = 0.3          # a ray this much longer than its neighbour (or JUMP_MM) jumped into another stroke
JUMP_MM = 0.5


def _jumped(a: np.ndarray, long_: np.ndarray) -> np.ndarray:
    """Of the long rays, those that ran into another stroke: each run of long rays must start or
    end with a jump from its neighbour. A run the rays grow into gradually is the stroke itself
    widening (a tapering swoosh, a flaring torso), and its edge is kept."""
    out = long_.copy()
    n = len(a)
    i = 0
    while i < n:
        if not long_[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and long_[j + 1]:
            j += 1
        steps = []
        for inside, outside in ((i, i - 1), (j, j + 1)):
            if 0 <= outside < n and not np.isnan(a[outside]) and not np.isnan(a[inside]):
                steps.append(a[inside] - a[outside] > max(JUMP_FRACTION * a[outside], JUMP_MM))
        if steps and not any(steps):
            out[i:j + 1] = False
        i = j + 1
    return out


def _fill(d: list[float | None], keep: set[int], also_bad: np.ndarray | None, line: np.ndarray,
          dirs: np.ndarray, outlier: float = OUTLIER, into: np.ndarray | None = None,
          seam: float = 0.0, clamp=frozenset()) -> np.ndarray:
    """Edge points along one side. Missing and too-long rays (they ran on into another stroke) are
    replaced by interpolating the edge points of the good ones, so the edge runs straight across the
    gap. `keep` are indices never treated as outliers (cut ends); `also_bad` marks rays that landed
    past the stroke's own end cut; `into` (when the letter's other strokes are known) marks rays that
    run a good way through another stroke: only those long rays are replaced."""
    a = np.array([np.nan if v is None else v for v in d], float)
    if outlier == OUTLIER_STRAIGHT and len(line) >= 2:
        fitted = _fit_straight_edge(a, keep, also_bad, line, dirs, clamp)
        if fitted is not None:
            return fitted
        if _curved_edge(a, keep, also_bad):
            return _fill(d, keep, also_bad, line, dirs, OUTLIER, into, seam)
        # Near a slanted cut the rays lean towards the cut, so they're longer for the same width.
        # Compare and fill widths across the stroke, not along the rays: otherwise a ray that reaches
        # the letter's corner looks too long, is replaced by the plain width, and the end narrows to a
        # wedge that stops short of the corner (an N's diagonal at its legs).
        t = line[-1] - line[0]
        t = t / (np.hypot(*t) or 1)
        cos = np.abs(dirs @ np.array([-t[1], t[0]]))
        cos = np.where(cos < 0.2, 0.2, cos)
        perp = a * cos
        pool = [perp[i] for i in range(len(a)) if i not in keep] if len(a) > len(keep) else list(perp)
        med = np.nanpercentile(pool, 25)
        bad = np.isnan(perp) | ((perp > outlier * med) & ~np.isin(np.arange(len(a)), list(keep)))
        if also_bad is not None:
            bad |= also_bad
        if bad.all():
            raise ValueError("no edge found for a stroke: is its centreline inside the letter?")
        a[bad] = med / cos[bad]
        return line + dirs * a[:, None]
    pool = [a[i] for i in range(len(a)) if i not in keep] if len(a) > len(keep) else list(a)
    # A straight stroke can have other strokes along more than half of one side (an E's stem), so
    # its width is taken from the shorter rays.
    med = np.nanpercentile(pool, 25) if outlier == OUTLIER_STRAIGHT else np.nanmedian(pool)
    long_ = (a > outlier * med) & ~np.isin(np.arange(len(a)), list(keep))
    if outlier != OUTLIER_STRAIGHT:
        # ...that run through another stroke, or jump from their neighbour to far past the stroke's
        # width: a D's top bar, where its slanted stem meets it, cast rays down beside the stem to the
        # bottom bar (4 mm to 25 mm), and those rungs covered the stem (370 mm2 of overlap). Not every
        # jump: at a crossing (an X) the rays widen into the other stroke's ink and are its real edge.
        jumped = _jumped(a, long_)
        if into is not None:
            long_ = long_ & (into | (jumped & (a > RAY_FAR * med)))
        else:
            long_ = jumped
    bad = np.isnan(a) | long_
    if also_bad is not None:
        bad |= also_bad
    if bad.all():
        raise ValueError("no edge found for a stroke: is its centreline inside the letter?")
    idx = np.arange(len(a))
    good = idx[~bad]
    if outlier == OUTLIER_STRAIGHT:
        # A leg or bar keeps its width across the stroke it meets.
        a[bad] = med
        return line + dirs * a[:, None]
    # Before the first good ray and after the last, keep the nearest good distance along the ray's own
    # direction (a leg's end beside a bar); between good rays, run the edge straight across.
    a[bad] = np.interp(idx[bad], good, a[~bad])
    pts = line + dirs * a[:, None]
    inner = bad & (idx > good[0]) & (idx < good[-1])
    for k in (0, 1):
        pts[inner, k] = np.interp(idx[inner], good, pts[~bad, k])
    return pts


EDGE_FIT_TOL_MM = 0.12      # a straight stroke's edge point this close to the fitted edge line is on it
EDGE_FIT_MIN_SHARE = 0.6     # the fitted line must hold at least this share of the rays


def _curved_edge(a: np.ndarray, keep: set[int], also_bad: np.ndarray | None) -> bool:
    """Whether a straight stroke's rays change smoothly along it with no jump (a round dot's edge),
    rather than jumping where they run into another stroke: then the edge is followed ray by ray."""
    # Not the two rays at each end: a round end's edge turns steeply there.
    idx = [i for i in range(2, len(a) - 2) if i not in keep and not np.isnan(a[i])
           and not (also_bad is not None and also_bad[i])]
    if len(idx) < 4:
        return False
    v = a[idx]
    jumps = np.abs(np.diff(v))
    return bool(jumps.max() <= max(0.25 * np.median(v), 0.3))


def _fit_straight_edge(a: np.ndarray, keep: set[int], also_bad: np.ndarray | None, line: np.ndarray,
                       dirs: np.ndarray, clamp=frozenset()) -> np.ndarray | None:
    """One side of a straight stroke whose edge is a straight line, not necessarily parallel to the
    centreline (a leg that tapers, a diagonal of a thick-thin W). The line is fitted to the rays that
    land on it; rays that run past it (into another stroke) end on it instead. Rays that stop short
    of it keep their own point: the edge really is narrower there (a notch shaped by the next stroke).
    None when no line holds enough of the rays (the caller falls back to a constant width)."""
    n = len(a)
    hits = line + dirs * np.nan_to_num(a)[:, None]
    ok = ~np.isnan(a)
    if also_bad is not None:
        ok &= ~also_bad
    inner = np.array([i not in keep for i in range(n)])
    cand = ok & inner
    if cand.sum() < 3:
        return None
    t = line[-1] - line[0]
    t = t / (np.hypot(*t) or 1)
    nrm = np.array([-t[1], t[0]])
    # Fit offset = c0 + c1 * s (s along the stroke, offset across it) to the inner rays, trimming
    # those far off the line; start from the shortest-quarter rays, which are on the stroke's own edge.
    s = (hits - line[0]) @ t
    off = (hits - line[0]) @ nrm
    perp = np.abs(off)
    base = np.nanpercentile(perp[cand], 25)
    use = cand & (perp <= base * 1.15 + EDGE_FIT_TOL_MM)
    coef = None
    for _ in range(6):
        if use.sum() < 2:
            return None
        coef = np.polyfit(s[use], off[use], 1) if np.ptp(s[use]) > 1e-6 else np.array([0.0, np.mean(off[use])])
        res = np.abs(off - np.polyval(coef, s))
        new = cand & (res <= EDGE_FIT_TOL_MM)
        if (new == use).all():
            break
        use = new
    # The line must hold most of the rays that aren't plainly into another stroke: a round dot's or
    # bulging stroke's edge isn't a line, and is followed ray by ray instead.
    plain = cand & (perp <= 1.5 * base + EDGE_FIT_TOL_MM)
    if use.sum() < max(2, EDGE_FIT_MIN_SHARE * plain.sum()):
        return None
    pred = np.polyval(coef, s)
    # Each ray against the line: past it (or missing, or past a cut) ends on the line.
    out = hits.copy()
    sign = np.sign(np.nanmedian(off[use])) or 1.0
    for i in range(n):
        # A cut end's ray is kept as it lands, except at an end inside the letter (butting another
        # stroke), where the outline flares into the stroke it meets (a bar's rounded fillets into the
        # stem): there it ends on the stroke's own edge line too.
        past = (not ok[i]) or ((inner[i] or i in clamp) and sign * (off[i] - pred[i]) > EDGE_FIT_TOL_MM)
        if not past:
            continue
        # where the ray from line[i] along dirs[i] crosses offset = c0 + c1 * s
        p0, d = line[i], dirs[i]
        s0, o0 = (p0 - line[0]) @ t, (p0 - line[0]) @ nrm
        ds, do = d @ t, d @ nrm
        den = do - coef[0] * ds
        if abs(den) < 1e-6:
            return None
        k = (coef[0] * s0 + coef[1] - o0) / den
        if k <= 0:
            return None
        out[i] = p0 + d * k
    return out


def _cut_dir(kind: str, normal: np.ndarray) -> np.ndarray:
    if kind == "vertical":
        v = np.array([0.0, 1.0])
    elif kind == "horizontal":
        v = np.array([-1.0, 0.0])
    elif kind == "perpendicular":
        return normal
    elif kind.startswith("angle:"):        # along a line at this many degrees from horizontal
        a = math.radians(float(kind.split(":", 1)[1]))
        v = np.array([math.cos(a), math.sin(a)])
    else:
        raise ValueError(f"unknown cut {kind!r}")
    return v if np.dot(v, normal) >= 0 else -v


STRAIGHT_TOLERANCE_MM = 0.1
STRAIGHT_SAMPLE_MM = 0.5
STRAIGHT_MIN_SAMPLES = 16      # samples along even a short straight stroke
STRAIGHT_MIN_STEP_MM = 0.15
STRAIGHT_TURN_TOL_DEG = 4.0    # a straight stroke keeps a pair where its stitch is this far off a steady turn
BUTT_OVERLAP_MM = 0.2
RING_OVERLAP_MM = 0.3        # a ring sews on past its start this far: pull-in opened a butted seam (O on fleece),
                             # and 1.2 (or 0.8) mm made a lump at the seam (O on a cap)
CUT_LINE_MM = 0.1            # a ray landing within this of an end's cut line hit the cut, not the edge
CUT_REACH = 3.0              # ...checked only this many stroke widths from that end
END_RAY_REACH = 1.6          # an end's ray along its cut longer than this many times the stroke's own reach
                             # across the cut ran into another stroke
BLEND_FRACTION = 0.18       # a slanted end's angle turns square over this share of the letter's height
BLEND_SPREAD = 0.25          # ...or longer, so the outer edge's stitches open up by at most this share
BUTT_MIN_INSIDE_MM = 0.5     # an end whose middle is this far inside the outline butts another stroke


TUCK_MIN_DEG = 8.0           # a straight stroke's slanted butting end turns at least this far from square...
TUCK_MAX_DEG = 62.0          # ...and at most this far (beyond, the tuck would reach through the other stroke)
TUCK_REACH = 0.85            # the tuck reaches at most this share of the stroke's width past the cut line
TUCK_INSIDE = 0.5            # an end this share of the stroke's half width inside the outline butts another
TUCK_INSIDE_MIN_MM = 0.15


def _tuck_butting_ends(line: np.ndarray, stroke: Stroke, boundary):
    """A straight stroke whose end butts another stroke along a slanted cut (an N's diagonal into its
    legs, an A's crossbar, an A's leg into the other at the apex): cut square instead, and run the end
    on into the other stroke until the square end is wholly inside it. Its stitches then keep one angle
    along the whole stroke: turning them to the slant over a blend fanned the diagonal of a 5.75 mm N
    by 34 degrees, the wiggle a render review flagged. Returns (line, stroke with the new cuts, the
    tucked ends)."""
    from dataclasses import replace

    from shapely.geometry import Point

    tucked = set()
    cuts = {0: stroke.start_cut, len(line) - 1: stroke.end_cut}
    d = line[-1] - line[0]
    L = float(np.hypot(*d))
    if L < 1e-6:
        return line, stroke, tucked
    t = d / L
    normal = np.array([-t[1], t[0]])
    for end, cut in cuts.items():
        if cut == "perpendicular":
            continue
        # the stroke's half width near that end, from the outline either side a little way in
        q = line[end] + (t if end == 0 else -t) * min(L / 3, 1.0)
        half = boundary.distance(Point(q))
        # Butting, not free: the end's middle is well inside the letter, relative to the stroke's own
        # width (a 5.75 mm A's crossbar ends 0.46 mm in: under any fixed limit that suits 20 mm letters).
        if boundary.distance(Point(line[end])) <= max(TUCK_INSIDE_MIN_MM, TUCK_INSIDE * half):
            continue
        c = _cut_dir(cut, normal)
        turn = math.degrees(math.acos(min(1.0, abs(float(np.dot(c, normal))))))
        if not TUCK_MIN_DEG <= turn <= TUCK_MAX_DEG:
            continue
        ext = min(half * math.tan(math.radians(turn)), TUCK_REACH * 2 * half)
        sign = -1.0 if end == 0 else 1.0
        new_end = line[end] + sign * t * ext
        line = np.vstack([new_end, line]) if end == 0 else np.vstack([line, new_end])
        tucked.add("start" if end == 0 else "end")
    if tucked:
        stroke = replace(stroke, start_cut="perpendicular" if "start" in tucked else stroke.start_cut,
                         end_cut="perpendicular" if "end" in tucked else stroke.end_cut)
    return line, stroke, tucked


CLIP_FREE_MM = 0.3           # a straight stroke's end this close to the outline is free (on a flat line)
CLIP_MIN_DEG = 8.0           # ...and its cut this far off square is clipped rather than turned
CLIP_MAX_DEG = 70.0
CLIP_RAIL_MM = 0.15          # ...its fitted rails meet the cut within this of the end's own corners
CLIP_RAIL_LOOSE = 0.6        # ...or one of them, the other within this share of the stroke's width
CLIP_LINE_MM = 0.1           # ...on the baseline or cap height: within this of the letter's bottom or top
CLIP_SQUARE_MM = 0.7         # square rungs down to this width; then the last pair along the cut line,
CLIP_TIP_MM = 0.45           # ...this long, to the far corner
CLIP_TIP_GAP_MM = 0.05       # ...ending at least this short of where the last square rung meets the cut
CLIP_APEX = 0.5              # an end within this share of its width of another stroke is an apex: left as is
CLIP_ON_EDGE_MM = 0.12       # a free end's cut lies along the outline: its rung's middle is this close to it


def _clipped_ends(stroke: Stroke, line: np.ndarray, boundary) -> list[str]:
    """A straight stroke's free ends cut along a slanted line of the outline (a slanted leg on the
    baseline: an R's or A's foot). Flat lettering clips these instead of turning the stitches to the
    cut: a render review read the turned last rungs as a separate flat strip with a 0.3-0.4 mm step."""
    from shapely.geometry import Point

    out = []
    if len(line) < 2:
        return out
    d = line[-1] - line[0]
    L = float(np.hypot(*d))
    if L < 1e-6:
        return out
    normal = np.array([-d[1], d[0]]) / L
    for end, cut, name in ((0, stroke.start_cut, "start"), (-1, stroke.end_cut, "end")):
        # on the baseline or the cap height: a level cut (an end cut along a curve, such as an R's leg
        # against its bowl, isn't a flat line to clip by)
        if cut != "horizontal" or boundary.distance(Point(line[end])) > CLIP_FREE_MM:
            continue
        c = _cut_dir(cut, normal)
        turn = math.degrees(math.acos(min(1.0, abs(float(np.dot(c, normal))))))
        if CLIP_MIN_DEG <= turn <= CLIP_MAX_DEG:
            out.append(name)
    return out


def _line_hit(p, d, a, b):
    """Where the line through p along d meets the line through a and b (None if parallel)."""
    p, d, a, b = (np.asarray(v, float) for v in (p, d, a, b))
    e = b - a
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-12:
        return None
    s = ((a - p)[0] * e[1] - (a - p)[1] * e[0]) / den
    return p + s * d


def _clip_ends(pairs: list, left: np.ndarray, right: np.ndarray, ends: list[str], boundary=None,
               others=None, sides: dict | None = None) -> list:
    """A straight column's free slanted ends rebuilt with the stitches square to the stroke all the
    way: a square rung where the cut begins (its near corner), and a short square rung at the far
    corner. ES fills between two parallel rungs at that one angle, its edges running along the cut
    and the far rail, so the end lies flat along the outline without the stitches turning. The
    rails are fitted to the middle of the stroke's edges; the corners are where they meet the cut."""
    n = len(left)
    if n < 4 or len(pairs) < 2:
        return pairs
    mid = list(range(n // 4, max(n // 4 + 2, 3 * n // 4 + 1)))

    def fit(pts):
        q = np.asarray(pts, float)
        c = q.mean(axis=0)
        u, s, vt = np.linalg.svd(q - c)
        return c, vt[0]

    lc, ld = fit(left[mid])
    rc, rd = fit(right[mid])
    t = (left[-1] + right[-1]) / 2 - (left[0] + right[0]) / 2
    if np.hypot(*t) < 1e-6:
        return pairs
    t = t / np.hypot(*t)
    nrm = np.array([-t[1], t[0]])
    width = abs(float((rc - lc) @ nrm))

    def rail_hit(c, d, a, b):
        return _line_hit(c, d, a, b)

    out = list(pairs)
    for end in ends:
        k = -1 if end == "end" else 0
        sgn = 1.0 if end == "end" else -1.0
        ca, cb = np.asarray(left[k], float), np.asarray(right[k], float)
        if np.hypot(*(cb - ca)) < 1e-6:
            continue
        if boundary is not None:
            from shapely.geometry import Point

            # the end's cut lies along the outline (a foot on the baseline), not across the letter's
            # inside (an end that meets another stroke, close to the outline only because it's small)
            if boundary.distance(Point((ca + cb) / 2)) > CLIP_ON_EDGE_MM:
                continue
            # ...and on the letter's baseline or cap height, not a level edge inside it (the bottom of
            # an R's counter, where its leg starts)
            bx0, by0, bx1, by1 = boundary.bounds
            y = float((ca[1] + cb[1]) / 2)
            if min(abs(y - by0), abs(y - by1)) > CLIP_LINE_MM:
                continue
        # Not an apex: another stroke meeting this end (an A's legs at its top) keeps the font's shape
        # there as planned.
        if others is not None and not others.is_empty:
            from shapely.geometry import Point

            if others.distance(Point((ca + cb) / 2)) < CLIP_APEX * width:
                continue
        A = rail_hit(lc, ld, ca, cb)          # the left rail meets the cut
        B = rail_hit(rc, rd, ca, cb)          # the right rail meets the cut
        if A is None or B is None:
            continue
        # The fitted rails must be the stroke's own edges at this end: they meet the cut where the
        # end's own corners are (an R's leg whose edge runs into its bowl's curve isn't a straight leg).
        # One side may run into another stroke at this end (an A's right leg at the apex, beside the left
        # leg): that side only has to be roughly where the stroke's edge would be.
        da, db = math.dist(A, ca), math.dist(B, cb)
        if min(da, db) > CLIP_RAIL_MM or max(da, db) > CLIP_RAIL_LOOSE * width:
            continue
        near_left = sgn * float(A @ t) <= sgn * float(B @ t)
        near, far = (A, B) if near_left else (B, A)
        p_near, p_far = sgn * float(near @ t), sgn * float(far @ t)
        if p_far - p_near < 0.05:
            continue

        def rung(p):
            """Square to the stroke at p (along this end): the near corner's rail is past the cut
            there, so that side ends on the cut."""
            centre = lc + t * (sgn * p - float(lc @ t))
            a = _line_hit(centre, nrm, lc, lc + ld)
            b = _line_hit(centre, nrm, rc, rc + rd)
            cut = _line_hit(centre, nrm, ca, cb)
            if a is None or b is None or cut is None:
                return None
            if near_left:
                a = cut
            else:
                b = cut
            return (tuple(a), tuple(b))

        def proj(pr):
            return max(sgn * float(np.asarray(pr[0]) @ t), sgn * float(np.asarray(pr[1]) @ t))

        def square(pr):
            v = np.subtract(pr[1], pr[0])
            return abs(float(v @ t)) / (np.hypot(*v) or 1) < math.sin(math.radians(5))

        body = out[:-1] if end == "end" else out[1:]
        # the turned rungs dropped are this end's own: in the half of the stroke nearest it (a short
        # leg's other end keeps its turn)
        half_way = (sgn * float(((left[0] + right[0]) / 2) @ t) + sgn * float(((left[-1] + right[-1]) / 2) @ t)) / 2
        keep = [pr for pr in body if proj(pr) < p_near - 1e-6
                and (square(pr) or proj(pr) < max(half_way, p_near - 3 * width))]
        if not keep:
            keep = [out[0] if end == "end" else out[-1]]
        first = rung(p_near)
        if first is None:
            continue
        # the near corner's rung is full width: both rails
        first = (tuple(_line_hit(lc + t * (sgn * p_near - float(lc @ t)), nrm, lc, lc + ld)),
                 tuple(_line_hit(lc + t * (sgn * p_near - float(lc @ t)), nrm, rc, rc + rd)))
        # Square rungs down to where the satin is CLIP_SQUARE_MM wide; then the column's last pair lies
        # along the cut itself, CLIP_TIP_MM long, ending at the far corner. So the end's edge is the
        # outline's line (flat on the baseline, as the font is) all the way to the corner; only the
        # last short stitches turn. A square last rung stood the foot's far side off the line: the
        # foot read 9-12 degrees off flat on a 5.75 mm cap line.
        full = float(np.hypot(*np.subtract(first[1], first[0])))
        if full <= CLIP_SQUARE_MM:
            continue
        p_last = p_near + (p_far - p_near) * (1 - CLIP_SQUARE_MM / full)
        last = rung(p_last)
        if last is None:
            continue
        along = (far - near) / (np.hypot(*(far - near)) or 1)
        # The tip pair is CLIP_TIP_MM long, or half the cut on a short one, but never shorter than the
        # stretch of cut the square rungs leave bare allows: art_columns.trim_tips drops end pairs under
        # 0.4 mm as satin points, and dropping this one ended the column on the square rung. A 5.87 mm
        # Futura Condensed A's left foot (0.78 mm cut, 0.39 mm tip) then stood 9.8 deg off the baseline
        # with its outer corner bare.
        cut_len = float(np.hypot(*(far - near)))
        last_on_cut = np.asarray(last[0] if near_left else last[1], float)
        room = float(np.hypot(*(far - last_on_cut))) - CLIP_TIP_GAP_MM
        tip = far - along * max(min(CLIP_TIP_MM, 0.5 * cut_len), min(CLIP_TIP_MM, room))
        final = (tuple(tip), tuple(far)) if near_left else (tuple(far), tuple(tip))
        tail = [first, last, final]
        if boundary is not None:
            rounded = _round_near_corner(first, p_near, p_last, near_left, sgn, t, nrm, lc, ld, rc, rd, rung, boundary)
            if rounded is not None:
                p_top, corner = rounded
                keep = [pr for pr in keep if proj(pr) < p_top - 1e-6] or keep[:1]
                tail = corner + [last, final]
        if sides is not None:
            sides[end] = near_left
        out = keep + tail if end == "end" else tail[::-1] + keep
    return out

CORNER_ROUND_MM = 0.1        # a clipped end's near corner further than this off the outline is rounded
CORNER_ON_MM = 0.03          # ...and a point this close to the outline is on it
CORNER_SEARCH_MM = 1.5       # ...and its rounding is looked for this far along the rail and the cut
CORNER_STEP_MM = 0.01


def _round_near_corner(first, p_near, p_last, near_left, sgn, t, nrm, lc, ld, rc, rd, rung, boundary):
    """A clipped end whose near corner the font rounds (Lato Black's 7's foot): the corner where the
    rail meets the cut stood 0.13 mm2 out past the letter. Three square rungs follow the rounding
    instead: where the near rail leaves it, one halfway with its near end on the outline, and one
    where it meets the cut. All square to the stroke (an end's rungs stay square: turning one tilted
    an A's foot), so the column's near edge runs round the corner in two short chords.
    Returns (where the first of them is, along the stroke; the rungs), or None for a sharp corner."""
    from shapely.geometry import Point

    k = 0 if near_left else 1
    if boundary.distance(Point(first[k])) <= CORNER_ROUND_MM:
        return None

    def full(p):
        c = lc + t * (sgn * p - float(lc @ t))
        a = _line_hit(c, nrm, lc, lc + ld)
        b = _line_hit(c, nrm, rc, rc + rd)
        return None if a is None or b is None else (np.asarray(a, float), np.asarray(b, float))

    steps = np.arange(CORNER_STEP_MM, CORNER_SEARCH_MM, CORNER_STEP_MM)
    p_top = next((p_near - s for s in steps
                  if (r := full(p_near - s)) is not None and boundary.distance(Point(r[k])) <= CORNER_ON_MM), None)
    p_bot = next((p_near + s for s in steps
                  if p_near + s < p_last and (r := rung(p_near + s)) is not None
                  and boundary.distance(Point(r[k])) <= CORNER_ON_MM), None)
    if p_top is None or p_bot is None:
        return None
    mid = full((p_top + p_bot) / 2)
    if mid is None:
        return None
    # the halfway rung's near end: along it, from the far rail, to where the outline is
    far_pt, near_pt = mid[1 - k], mid[k]
    span = float(np.hypot(*(near_pt - far_pt)))
    u = (near_pt - far_pt) / (span or 1)
    hit = None
    for s in np.arange(0.5 * span, span + CORNER_SEARCH_MM, CORNER_STEP_MM):
        q = far_pt + u * s
        if boundary.distance(Point(q)) <= CORNER_ON_MM:
            hit = q
            break
    if hit is None:
        return None
    top = full(p_top)
    mid_pair = (tuple(hit), tuple(far_pt)) if k == 0 else (tuple(far_pt), tuple(hit))
    return p_top, [(tuple(top[0]), tuple(top[1])), mid_pair, rung(p_bot)]


def tuck_order(strokes: list[Stroke], outline: list[np.ndarray], box) -> list[Stroke]:
    """Strokes reordered so each one that tucks an end into another (see _tuck_butting_ends) sews
    before it: the tucked end then lies under that stroke's satin, not on top of it (an N's diagonal
    before its legs). Otherwise the order is kept."""
    from shapely.geometry import LineString, MultiLineString, Point

    boundary = MultiLineString([np.vstack([c, c[:1]]) for c in outline])
    lines = [centreline(s, box, 0.25) for s in strokes]
    before: dict[int, set[int]] = {j: set() for j in range(len(strokes))}
    for i, s in enumerate(strokes):
        if s.smooth or s.closed or len(s.path) != 2:
            continue
        _, _, tucked = _tuck_butting_ends(lines[i], s, boundary)
        for end in tucked:
            p = Point(lines[i][0 if end == "start" else -1])
            others = [(LineString(lines[j]).distance(p), j) for j in range(len(strokes))
                      if j != i and len(lines[j]) >= 2]
            if others:
                before[min(others)[1]].add(i)
    out, placed, busy = [], set(), set()

    def place(j):
        if j in placed or j in busy:
            return
        busy.add(j)
        for i in sorted(before[j]):
            place(i)
        busy.discard(j)
        placed.add(j)
        out.append(strokes[j])

    for j in range(len(strokes)):
        place(j)
    return out


def _simplify(pairs, tol: float):
    """A straight stroke's pairs, keeping only those where an edge bends by more than `tol` (corner
    points in ES): a leg whose end is shaped by the next stroke keeps that shape."""
    def off(p, a, b):
        p, a, b = map(np.asarray, (p, a, b))
        d = b - a
        n = np.hypot(*d)
        return np.hypot(*(p - a)) if n == 0 else abs(d[0] * (p - a)[1] - d[1] * (p - a)[0]) / n

    def stitch(m):
        v = np.subtract(pairs[m][1], pairs[m][0])
        return v / (np.hypot(*v) or 1)

    def mid(m):
        return (np.asarray(pairs[m][0]) + np.asarray(pairs[m][1])) / 2

    def turn_off(m, i, j):
        """Degrees the stitch at m is off a steady turn from i's stitch to j's (by place along)."""
        a, b, p = mid(i), mid(j), mid(m)
        L = np.hypot(*(b - a))
        f = 0.0 if L == 0 else float(np.clip(np.dot(p - a, b - a) / (L * L), 0, 1))
        want = stitch(i) * (1 - f) + stitch(j) * f
        want = want / (np.hypot(*want) or 1)
        return float(np.degrees(np.arccos(np.clip(abs(np.dot(want, stitch(m))), -1, 1))))

    # Also kept: pairs where the stitch turns (a slanted end's angle blending back to square). Dropping
    # them turns the stitches in one step at the end, a fan with open rungs (a K's leg on the baseline).
    keep = {0, len(pairs) - 1}
    stack = [(0, len(pairs) - 1)]
    while stack:
        i, j = stack.pop()
        worst, k = 0.0, None
        for m in range(i + 1, j):
            e = max(off(pairs[m][0], pairs[i][0], pairs[j][0]), off(pairs[m][1], pairs[i][1], pairs[j][1])) / tol
            e = max(e, turn_off(m, i, j) / STRAIGHT_TURN_TOL_DEG)
            if e > worst:
                worst, k = e, m
        if k is not None and worst > 1.0:
            keep.add(k)
            stack += [(i, k), (k, j)]
    return [pairs[i] for i in sorted(keep)]


def _extend_ring(pairs: list, overlap: float) -> list:
    """Pairs that carry a closed ring on past its start (pairs[0] == pairs[-1]) by `overlap` mm along
    its centre: the start's pairs again, the last one cut short to the exact length."""
    if overlap <= 0:
        return []
    mids = [np.mean(np.array(p), axis=0) for p in pairs]
    extra, s = [], 0.0
    for k in range(1, len(pairs) - 1):
        step = float(np.hypot(*(mids[k] - mids[k - 1])))
        if s + step >= overlap:
            t = (overlap - s) / (step or 1)
            (l0, r0), (l1, r1) = (np.array(q) for q in pairs[k - 1]), (np.array(q) for q in pairs[k])
            extra.append((tuple(l0 + (l1 - l0) * t), tuple(r0 + (r1 - r0) * t)))
            return extra
        extra.append(pairs[k])
        s += step
    return extra


INTO_OTHER = 0.12            # a ray with this share of its length inside another stroke ran into it
SHORT_END_RAY = 0.5          # a square end's ray shorter than this share of its neighbour's left through the end
SHORT_END_OUTSIDE_MM = 0.2   # ...and is lengthened only if its new end is at most this far off the ink
REGION_REACH = 0.5          # a stroke's area runs on past its ends by this many half widths


def stroke_regions(strokes: list[Stroke], outline: list[np.ndarray], box, spacing: float = DEFAULT_SPACING_MM):
    """Each stroke's rough area: its centreline widened by most of its half width (the centreline's
    distance to the outline, clear of its ends). Used to tell a ray that ran into another stroke from
    one that reached the stroke's own widening edge."""
    from shapely.geometry import LineString, MultiLineString, Point

    boundary = MultiLineString([np.vstack([c, c[:1]]) for c in outline])
    out = []
    for s in strokes:
        line = centreline(s, box, spacing)
        if len(line) < 2:
            out.append(None)
            continue
        mid = line[len(line) // 4: max(len(line) // 4 + 1, 3 * len(line) // 4)]
        half = float(np.median([boundary.distance(Point(q)) for q in mid])) if len(mid) else 0.0
        if half <= 0:
            out.append(None)
            continue
        if not s.closed:
            # A butting stroke's centreline stops at the edge it meets, and a V's legs meet in a point
            # past both: its area runs on past its ends by its half width.
            ext = []
            for a_, b_ in ((line[0], line[1]), (line[-1], line[-2])):
                t = a_ - b_
                ext.append(a_ + t / (np.hypot(*t) or 1) * REGION_REACH * half)
            line = np.vstack([ext[0], line, ext[1]])
        out.append(LineString(line).buffer(0.8 * half))
    return out


def letter_columns(strokes: list[Stroke], outline: list[np.ndarray], box, spacing: float = DEFAULT_SPACING_MM,
                   ring_overlap: float = RING_OVERLAP_MM, butt_overlap: float = BUTT_OVERLAP_MM,
                   seam_overlap: float = 0.0, tuck: bool = False) -> list[Column]:
    """Every stroke's column, each knowing where the letter's other strokes are. seam_overlap: how far
    columns lying side by side reach into each other (flat work; 0 for puff, where it perforates the foam
    twice). tuck (flat work): straight strokes butting another at a slant end square, tucked into it."""
    from shapely.ops import unary_union

    regions = stroke_regions(strokes, outline, box, spacing)
    cols = []
    for k, s in enumerate(strokes):
        rest = [r for j, r in enumerate(regions) if j != k and r is not None]
        cols.append(stroke_column(s, outline, box, spacing, ring_overlap, butt_overlap,
                                  others=unary_union(rest) if rest else None, tuck=tuck))
    return overlap_seams(cols, outline, seam_overlap)


SEAM_TOUCH_MM = 0.15         # a rail point this far inside the outline isn't on it
SEAM_NEAR_MM = 1.5           # ...and this close to another column is on a seam with it


def overlap_seams(cols: list[Column], outline: list[np.ndarray], reach: float) -> list[Column]:
    """Where two columns lie side by side (a dancer's two strokes along the line between them), each
    edge on the seam reaches `reach` into the other, as a butting end does: pull draws each satin in,
    and edges that only meet open a gap. Rail points on the letter's own outline stay put."""
    from shapely.geometry import MultiLineString, Point

    if reach <= 0 or len(cols) < 2:
        return cols
    from shapely.geometry import LineString

    boundary = MultiLineString([np.vstack([c, c[:1]]) for c in outline])
    allp = np.vstack(outline)
    ink = Letter("?", [], outline, (allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max())).glyph()
    shapes = [c.shape() for c in cols]
    mids = [LineString([tuple((np.array(l) + np.array(r)) / 2) for l, r in c.pairs]) if len(c.pairs) >= 2 else None
            for c in cols]
    out = []
    for i, c in enumerate(cols):
        others = [j for j in range(len(cols)) if j != i and mids[j] is not None]
        pairs = []
        last = len(c.pairs) - 1
        for k, (l, r) in enumerate(c.pairs):
            l, r = np.array(l), np.array(r)
            new = []
            for p, q in ((l, r), (r, l)):
                pt = Point(p)
                # an open column's end pairs are its ends: a butting end already reaches in
                if not c.closed and k in (0, last):
                    new.append(tuple(p))
                    continue
                near = [j for j in others if shapes[j].distance(pt) <= SEAM_NEAR_MM]
                if mids[i] is not None and near and boundary.distance(pt) > SEAM_TOUCH_MM:
                    # The seam is where this column's centre and the other's are equally far: find it
                    # along the stitch (from the far rail q out past p), then reach past it.
                    v = (p - q) / (np.hypot(*(p - q)) or 1)
                    j = min(near, key=lambda k: shapes[k].distance(pt))

                    def side(x):
                        return mids[j].distance(Point(x)) - mids[i].distance(Point(x))

                    lo, hi = q + v * 0.05, p + v * SEAM_NEAR_MM
                    if side(lo) > 0 > side(hi):
                        for _ in range(30):
                            m = (lo + hi) / 2
                            lo, hi = (m, hi) if side(m) > 0 else (lo, m)
                        # Only outwards, onto a seam at or past this edge: where the halfway line is
                        # inside the column, the other one butts against it (a T's bar), and its end
                        # already reaches in.
                        cand = lo + v * reach
                        if np.dot(lo - p, v) >= -SEAM_TOUCH_MM and ink.distance(Point(cand)) <= 0.02:
                            p = cand
                new.append(tuple(p))
            pairs.append((new[0], new[1]))
        out.append(Column(pairs=pairs, curve=c.curve, closed=c.closed, ring_end=c.ring_end,
                          overlap_mm=c.overlap_mm))
    return out


def stroke_column(stroke: Stroke, outline: list[np.ndarray], box, spacing: float = DEFAULT_SPACING_MM,
                  ring_overlap: float = RING_OVERLAP_MM, butt_overlap: float = BUTT_OVERLAP_MM,
                  others=None, tuck: bool = False) -> Column:
    """The stroke's column. A slanted end cut that twists the satin (its stitches turning across one
    another as they blend back to square, on a short or tightly curved stroke) is cut square instead;
    any crossing left after that drops the pair that causes it. `others`: the letter's other strokes'
    areas (`stroke_regions`), when known."""
    from dataclasses import replace

    c = _stroke_column(stroke, outline, box, spacing, ring_overlap, butt_overlap, others, tuck)
    if stroke.closed or not _crossed_pairs(c):
        return c
    best = c
    for start, end in ((stroke.start_cut, "perpendicular"), ("perpendicular", stroke.end_cut),
                       ("perpendicular", "perpendicular")):
        if (start, end) == (stroke.start_cut, stroke.end_cut):
            continue
        alt = _stroke_column(replace(stroke, start_cut=start, end_cut=end), outline, box, spacing,
                             ring_overlap, butt_overlap, others, tuck)
        if _crossed_pairs(alt) < _crossed_pairs(best):
            best = alt
        if not _crossed_pairs(best):
            return best
    return _uncross(best)


FREE_END_MM = 0.3            # a centreline end this close to the outline is a free end (a tip)
END_DENSE_SPANS = 2         # within this many pair spacings of an open end, pairs come closer
END_DENSE_STEP_MM = 0.3      # ...this far apart: a tapering tip is trimmed where it gets too thin, not a
                             # whole spacing short of it (a dancer's 2 mm spacing left her arm tips 2 mm short)


def _dense_ends(line: np.ndarray, spacing: float, start: bool = True, end: bool = True) -> np.ndarray:
    """The centreline with extra samples near its start and/or end."""
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(line, axis=0).T))])
    total = s[-1]
    reach = min(END_DENSE_SPANS * spacing, total / 3)
    if reach <= END_DENSE_STEP_MM or not (start or end):
        return line
    steps = np.arange(END_DENSE_STEP_MM, reach, END_DENSE_STEP_MM)
    extra = np.concatenate([steps if start else [], total - steps if end else []])
    t = np.unique(np.concatenate([s, extra]))
    return np.column_stack([np.interp(t, s, line[:, 0]), np.interp(t, s, line[:, 1])])


def edge_spread(c: Column) -> float:
    """How much farther the column's longer edge runs than its middle, at worst between two pairs
    (1.0: the edges run as far as the middle). EmbroideryStudio spaces a column's stitches evenly
    along its middle, so where the stitches fan (turning to a slanted end) the outer edge's needle
    points are this much farther apart."""
    segs = []
    for (l0, r0), (l1, r1) in zip(c.pairs, c.pairs[1:]):
        m0 = (np.asarray(l0) + np.asarray(r0)) / 2
        m1 = (np.asarray(l1) + np.asarray(r1)) / 2
        mid = float(np.hypot(*(m1 - m0)))
        if mid < SPREAD_MIN_STEP_MM:
            continue
        edge = max(float(np.hypot(*np.subtract(l1, l0))), float(np.hypot(*np.subtract(r1, r0))))
        segs.append((edge / mid, mid))
    # the worst spread kept up over at least SPREAD_SPAN_MM of the column (not one sliver of a segment)
    run = 0.0
    for ratio, mid in sorted(segs, reverse=True):
        run += mid
        if run >= SPREAD_SPAN_MM:
            return max(1.0, ratio)
    return 1.0


SPREAD_MIN_STEP_MM = 0.15     # pairs closer than this along the middle are a cut's end, not a fan
SPREAD_SPAN_MM = 0.5
SPACING_FLOOR_MM = 0.28        # a fanning column's spacing is tightened no further than this


def spacing_for(c: Column, stroke: Stroke | None, spacing_mm: float, floor_mm: float = SPACING_FLOOR_MM) -> float:
    """Satin spacing for a column so its outer edge, where its stitches fan, is no more open than
    `spacing_mm` (never denser than `floor_mm`). Only straight strokes: on a curve EmbroideryStudio
    already spaces at the outside edge (an O's outer edge measured 0.355 mm at 0.38)."""
    if stroke is not None and (stroke.smooth or stroke.closed or len(stroke.path) != 2):
        return spacing_mm
    return round(max(floor_mm, spacing_mm / edge_spread(c)), 2)



def _uncross(c: Column) -> Column:
    """Drop inner pairs whose stitch line crosses a neighbour's, until none do."""
    from shapely.geometry import LineString

    pairs, curve = list(c.pairs), list(c.curve)
    for _ in range(len(pairs)):
        hit = None
        for i in range(len(pairs) - 1):
            a, b = LineString(pairs[i]), LineString(pairs[i + 1])
            if a.length and b.length and a.crosses(b):
                hit = i
                break
        if hit is None or len(pairs) <= 2:
            break
        k = hit + 1 if hit + 1 < len(pairs) - 1 else hit
        if k == 0:
            break
        del pairs[k], curve[k]
    curve[0] = curve[-1] = False
    return Column(pairs=pairs, curve=curve, closed=c.closed, ring_end=c.ring_end, overlap_mm=c.overlap_mm)


def _stroke_column(stroke: Stroke, outline: list[np.ndarray], box, spacing: float = DEFAULT_SPACING_MM,
                   ring_overlap: float = RING_OVERLAP_MM, butt_overlap: float = BUTT_OVERLAP_MM,
                   others=None, tuck: bool = False) -> Column:
    from shapely.geometry import MultiLineString, Point

    boundary = MultiLineString([np.vstack([c, c[:1]]) for c in outline])
    straight = not stroke.smooth and len(stroke.path) == 2 and not stroke.closed
    # Straight strokes are sampled finely to find where their edges bend; most pairs are dropped after.
    step = spacing
    if straight:
        # fine enough to follow a small piece's edge too (a 2 mm dot): at least STRAIGHT_MIN_SAMPLES
        (u0, v0), (u1, v1) = stroke.path
        span = float(np.hypot((u1 - u0) * (box[2] - box[0]), (v1 - v0) * (box[3] - box[1])))
        step = min(STRAIGHT_SAMPLE_MM, max(STRAIGHT_MIN_STEP_MM, span / STRAIGHT_MIN_SAMPLES))
    line = centreline(stroke, box, step)
    if not straight and not stroke.closed and len(line) >= 3:
        # Only free square ends on the outline (a tip); a butting end keeps its pairs.
        free = [cut == "perpendicular" and boundary.distance(Point(line[k])) < FREE_END_MM
                for k, cut in ((0, stroke.start_cut), (-1, stroke.end_cut))]
        line = _dense_ends(line, spacing, *free)
    straight = not stroke.smooth and len(stroke.path) == 2 and not stroke.closed
    tucked = set()
    if tuck and straight and len(line) >= 2:
        line, stroke, tucked = _tuck_butting_ends(line, stroke, boundary)
    n = len(line)
    far = 4 * max(box[2] - box[0], box[3] - box[1])
    tang = np.gradient(line, axis=0)
    if stroke.closed:   # wrap the tangent at the join
        tang[0] = tang[-1] = line[1] - line[-2]
    tang /= np.hypot(*tang.T)[:, None]
    normal = np.column_stack([-tang[:, 1], tang[:, 0]])          # left of the direction of travel
    dirs = [normal[i] for i in range(n)]
    keep = set()
    if not stroke.closed:
        dirs[0] = _cut_dir(stroke.start_cut, normal[0])
        dirs[-1] = _cut_dir(stroke.end_cut, normal[-1])
        keep = {i for i, c in ((0, stroke.start_cut), (n - 1, stroke.end_cut)) if c != "perpendicular"}
        # From a slanted cut, the stitch angle turns gradually to square across the column: a sudden
        # turn makes neighbouring stitches cross, piling thread on one spot.
        s_along = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(line, axis=0).T))])
        # The turn is spread over enough length that the outer edge's stitches open up no more than
        # BLEND_SPREAD (a wide stroke turning far needs longer: a K's leg turning to the baseline).
        width_est = 2 * float(np.median([boundary.distance(Point(q)) for q in line]))
        for end, cut in ((0, stroke.start_cut), (n - 1, stroke.end_cut)):
            if cut == "perpendicular":
                continue
            turn = float(np.arccos(np.clip(abs(np.dot(dirs[end], normal[end])), -1, 1)))
            blend = min(max(BLEND_FRACTION * (box[3] - box[1]), width_est / 2 * turn / BLEND_SPREAD),
                        s_along[-1] / 2)
            if blend <= 0:
                continue
            for i in range(1, n - 1):
                d = abs(s_along[i] - s_along[end])
                if d >= blend:
                    continue
                t = d / blend
                v = dirs[end] * (1 - t) + normal[i] * t
                dirs[i] = v / (np.hypot(*v) or 1)
    straight = not stroke.smooth and len(stroke.path) == 2 and not stroke.closed
    sides = []
    followed = False          # a straight stroke whose edge follows a curved outline (see _curved_edge)
    rays = {sign: [_ray(boundary, line[i], sign * dirs[i], far) for i in range(n)] for sign in (1, -1)}
    s_line = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(line, axis=0).T))])
    both = [a + b for a, b in zip(rays[1], rays[-1]) if a is not None and b is not None]
    width = float(np.percentile(both, 25)) if both else far
    if not stroke.closed and both:
        # A slanted end's ray runs along its cut. Where the cut lies along another stroke's edge (a
        # 7's diagonal cut along the top of its bar), the ray runs on through that stroke to the far
        # outline: it's cut back to the width the stroke itself has across the cut.
        for end, cut in ((0, stroke.start_cut), (n - 1, stroke.end_cut)):
            if cut == "perpendicular":
                continue
            cos = max(abs(float(np.dot(dirs[end], normal[end]))), 0.3)
            # the stroke's width next to this end (a tapering stroke is much wider at one end)
            # (twice its nearer side: the other side's rays may run into the stroke the cut lies along)
            near = [2 * min(rays[1][i], rays[-1][i]) for i in (range(1, min(n - 1, 4)) if end == 0 else range(max(1, n - 4), n - 1))
                    if rays[1][i] is not None and rays[-1][i] is not None]
            expect = (float(np.median(near)) if near else width) / 2 / cos
            for sign in (1, -1):
                r = rays[sign][end]
                if r is not None and r > END_RAY_REACH * expect + EDGE_FIT_TOL_MM:
                    rays[sign][end] = expect
    if not stroke.closed and n >= 3:
        # A square end on an outline edge that isn't quite square to the stroke (a swoosh's flat
        # foot): one end ray leaves through that edge after a short way, and the end pair comes out
        # narrow, leaving a wedge bare. It reaches as far as its neighbour instead, but only where
        # that point is still on the ink: a round end (a dot, a head) narrows because it's round, and
        # widening it squares the circle off.
        ink = Letter("?", [], outline, box).glyph()
        for end, nxt, cut in ((0, 1, stroke.start_cut), (n - 1, n - 2, stroke.end_cut)):
            if cut != "perpendicular":
                continue
            for sign in (1, -1):
                r, q = rays[sign][end], rays[sign][nxt]
                if r is not None and q is not None and r < SHORT_END_RAY * q:
                    p = line[end] + sign * dirs[end] * q
                    if ink.distance(Point(p)) <= SHORT_END_OUTSIDE_MM:
                        rays[sign][end] = q
    for sign in (1, -1):
        d = rays[sign]
        behind = np.zeros(n, bool)
        if not stroke.closed:
            # A ray from near an end can land past that end's cut, on the next stroke's edge: a notch.
            # Only near the end: further along, a curved stroke (C) can come back round past its own
            # cut line, and those rays are its real edge.
            for end, t, cut in ((0, tang[0], stroke.start_cut), (n - 1, -tang[-1], stroke.end_cut)):
                if cut == "perpendicular":
                    continue
                c = dirs[end]
                m = t - np.dot(t, c) * c
                m /= np.hypot(*m) or 1
                for i in range(1, n - 1):
                    if abs(s_line[i] - s_line[end]) > CUT_REACH * width:
                        continue
                    # Past the cut, or on it: a ray that reaches the cut line has missed this side's edge.
                    if d[i] is not None and np.dot(line[i] + sign * dirs[i] * d[i] - line[end], m) < CUT_LINE_MM:
                        behind[i] = True
        into = None
        if others is not None:
            from shapely.geometry import LineString

            into = np.zeros(n, bool)
            for i in range(n):
                if d[i] is None or d[i] <= 1e-6:
                    continue
                seg = LineString([tuple(line[i]), tuple(line[i] + sign * dirs[i] * d[i])])
                into[i] = seg.intersection(others).length > INTO_OTHER * seg.length
        butting = set() if stroke.closed else {
            k for k in (0, n - 1) if k in keep and boundary.distance(Point(line[k])) > BUTT_MIN_INSIDE_MM}
        if straight:
            a_ = np.array([np.nan if v is None else v for v in d], float)
            if (_fit_straight_edge(a_, keep, behind, line, sign * np.array(dirs), butting) is None
                    and _curved_edge(a_, keep, behind)):
                followed = True
        sides.append(_fill(d, keep, behind, line, sign * np.array(dirs), OUTLIER_STRAIGHT if straight else OUTLIER,
                           into, clamp=butting))
    left, right = sides
    if not stroke.closed:
        # An end inside the letter butts another stroke. Clicks round to whole pixels (about 0.1 mm),
        # which can leave a hairline slit between the two, and an outline then runs into it: so the
        # end reaches `butt_overlap` into the stroke it meets.
        for end, t in ((0, -tang[0]), (n - 1, tang[-1])):
            mid = (left[end] + right[end]) / 2
            if boundary.distance(Point(mid)) > BUTT_MIN_INSIDE_MM:
                left[end] = left[end] + t * butt_overlap
                right[end] = right[end] + t * butt_overlap
    pairs = [(tuple(left[i]), tuple(right[i])) for i in range(n)]
    straight = not stroke.smooth and len(stroke.path) == 2 and not stroke.closed
    if straight and followed:
        # Its edge follows a curved outline (a round dot or head): pairs at the curve spacing, as curve
        # points, so the rails come out round, not as a polygon of corner points.
        k = max(1, int(round(spacing / STRAIGHT_SAMPLE_MM)))
        # every sample near the ends, where a round end narrows fast (the tips are trimmed finely there)
        idx = sorted(set(range(0, n, k)) | {n - 1} | set(range(min(n, k + 1))) | set(range(max(0, n - k - 1), n)))
        pairs = [pairs[i] for i in idx]
        curve = [False] + [True] * (len(pairs) - 2) + [False]
    elif straight:
        pairs = _simplify(pairs, STRAIGHT_TOLERANCE_MM)
        if tuck:
            clips = _clipped_ends(stroke, line, boundary)
            if clips:
                clip_sides: dict = {}
                pairs = _clip_ends(pairs, np.asarray(left), np.asarray(right), clips, boundary, others, clip_sides)
                # ES sews a column's pairs left, right, left, right... A clipped end sews its stitch along
                # the cut whole (near corner to far corner) only when the near corner is on the side
                # sewn second at that end: the right rail at the column's end, the left at its start.
                # The other way the cut is sewn in two pieces with a V between them: an A's left foot
                # (sewn from its foot) kinked where its right foot (sewn to its foot) lay flat. Swap the
                # rails when that puts more clipped ends the right way, so mirrored legs end alike.
                good = sum(1 for e, nl_ in clip_sides.items() if nl_ == (e == "start"))
                if len(clip_sides) - good > good:
                    pairs = [(r, l) for l, r in pairs]
        curve = [False] * len(pairs)
    else:
        curve = [False] + [True] * (len(pairs) - 2) + [False]
        if stroke.closed:
            pairs[-1] = pairs[0]
            extra = _extend_ring(pairs, ring_overlap)
            if extra:
                # The seam is now mid-column: the closing pair is a curve point, the new last one the end.
                ring_end = len(pairs) - 1
                curve[-1] = True
                pairs += extra
                curve += [True] * (len(extra) - 1) + [False]
                return Column(pairs=pairs, curve=curve, closed=True, ring_end=ring_end, overlap_mm=ring_overlap)
    return Column(pairs=pairs, curve=curve, closed=stroke.closed)


# ---------------------------------------------------------------- words

PULL_IN_SHARE = 0.22        # a satin stitch's end pulls in this share of the stitch's length (cap twill)
PULL_IN_BAND_MM = 0.6       # ends this close to the baseline or cap line are compensated...
PULL_IN_FULL_MM = 0.3       # ...fully within this (a bowl's run can sit 0.2 mm inside the line), fading out beyond
PULL_IN_UPRIGHT_DEG = 35.0  # ...if their stitch is within this of square to the line


def compensate_pull_in(letters: list[Letter], pull_comp_mm: float, share: float = PULL_IN_SHARE) -> int:
    """Flat lettering: a satin stitch that crosses the letter's baseline or cap line (a D's bowl along
    its bottom, an O's or S's bottom, an E's bars) pulls in by about `share` of its length, while a
    stroke's square end on the line (a stem: stitches along the line) doesn't. ES's pull compensation
    is a fixed amount, enough for short stitches only: on a 12.9 mm all-caps word the D's bowl (2.7 mm
    stitches, 0.25 mm compensation, planned 0.2 mm past the stem) sewed short of its stem, which read as
    the stem dipping below the line. At 5.75 mm (0.9 mm stitches) the same plan sewed flush.

    Each such end is pushed outward along its stitch by share x length - pull_comp_mm, fading out over
    PULL_IN_FULL_MM to PULL_IN_BAND_MM from the line. Set on `Column.sew_pairs`; `pairs` keep the font's shape for the
    plan checks. Returns how many ends moved."""
    moved = 0
    up = math.cos(math.radians(PULL_IN_UPRIGHT_DEG))
    for lt in letters:
        gx0, gy0, gx1, gy1 = lt.glyph().bounds
        for col in lt.columns:
            out, changed = [], False
            for a, b in col.pairs:
                a, b = np.asarray(a, float), np.asarray(b, float)
                v = b - a
                L = float(np.hypot(*v))
                # the longer stitch pulls in more too: ext = share x (L + ext) - pull_comp
                ext = (share * L - pull_comp_mm) / (1 - share) if share < 1 else 0.0
                if L < 1e-6 or ext <= 0 or abs(v[1]) / L < up:
                    out.append((tuple(a), tuple(b)))
                    continue
                pts = [a, b]
                for k in (0, 1):
                    p, q = pts[k], pts[1 - k]
                    for line, outward in ((gy0, -1.0), (gy1, 1.0)):
                        d = abs(p[1] - line)
                        # the end on the line's side of its stitch (moving it goes outward, off the letter)
                        if d < PULL_IN_BAND_MM and (p[1] - q[1]) * outward > 0:
                            u = (p - q) / L
                            fade = 1.0 if d <= PULL_IN_FULL_MM else (PULL_IN_BAND_MM - d) / (PULL_IN_BAND_MM - PULL_IN_FULL_MM)
                            pts[k] = p + u * ext * fade
                            moved += 1
                            changed = True
                            break
                out.append((tuple(pts[0]), tuple(pts[1])))
            col.sew_pairs = out if changed else None
    return moved


def sewing_order(letters: list[Letter], order: str = "left_to_right") -> list[Letter]:
    """The letters (left to right as planned) in the order they're sewn. center_out: the letter
    nearest the word's centre, then the ones to its right going outward, then the ones to its left
    going outward (caps: the fabric is pushed out from the middle rather than gathered)."""
    if order == "left_to_right" or len(letters) < 3:
        return list(letters)
    if order != "center_out":
        raise ValueError(f"unknown letter order {order!r}")
    x0, _, x1, _ = word_box(letters)
    mid = (x0 + x1) / 2
    centres = [(lt.glyph().bounds[0] + lt.glyph().bounds[2]) / 2 for lt in letters]
    k = min(range(len(letters)), key=lambda i: abs(centres[i] - mid))
    return [letters[k]] + letters[k + 1:] + letters[:k][::-1]


def word_columns(font_path: str | Path, text: str, height_mm: float, letter_spacing_pct: float | None,
                 strokes: dict[str, list[Stroke]], spacing: float = DEFAULT_SPACING_MM,
                 width_mm: float | None = None, ring_overlap: float = RING_OVERLAP_MM,
                 butt_overlap: float = BUTT_OVERLAP_MM, flat: bool = False) -> list[Letter]:
    """Every letter's columns, the word centred on (0, 0) by its ink box. lat: flat embroidery, not puff
    (slanted butting ends are tucked into the stroke they meet; see letter_columns). Letters advance by the
    font's advance widths plus `letter_spacing_pct` of the height (ES's Letter Spacing). Spaces advance
    only. With `width_mm`, the spacing is instead whatever makes the ink that wide."""
    if width_mm is not None:
        base = word_columns(font_path, text, height_mm, 0, strokes, spacing, None, ring_overlap, butt_overlap, flat)
        x0, _, x1, _ = word_box(base)
        gaps = len(text) - 1
        if gaps < 1:
            raise ValueError("a single letter's width comes from its height, not spacing")
        letter_spacing_pct = (width_mm - (x1 - x0)) / gaps / height_mm * 100
    letters, x = [], 0.0
    gap = (letter_spacing_pct or 0.0) / 100 * height_mm
    for ch in text:
        contours, adv, _ = glyph_outline(font_path, ch, height_mm)
        if ch.isspace() or not contours:
            x += adv + gap
            continue
        if ch not in strokes:
            raise ValueError(f"no strokes for {ch!r}: give its centrelines in the job's columns.strokes")
        moved = [c + [x, 0.0] for c in contours]
        allp = np.vstack(moved)
        box = (allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max())
        sts = tuck_order(strokes[ch], moved, box) if flat else strokes[ch]
        cols = letter_columns(sts, moved, box, spacing, ring_overlap, butt_overlap, tuck=flat)
        letters.append(Letter(ch, cols, moved, box, sts, butt_overlap=butt_overlap))
        x += adv + gap
    if not letters:
        raise ValueError("nothing to stitch")
    return _centre_word(letters)


def _centre_word(letters: list[Letter]) -> list[Letter]:
    """Move the word so its ink box is centred on (0, 0)."""
    x0, y0, x1, y1 = word_box(letters)
    dx, dy = -(x0 + x1) / 2, -(y0 + y1) / 2
    for lt in letters:
        lt.outline = [c + [dx, dy] for c in lt.outline]
        lt.box = (lt.box[0] + dx, lt.box[1] + dy, lt.box[2] + dx, lt.box[3] + dy)
        for c in lt.columns:
            c.pairs = [((l[0] + dx, l[1] + dy), (r[0] + dx, r[1] + dy)) for l, r in c.pairs]
    return letters


SVG_CURVE_STEPS = 16         # points per curved segment of an SVG letter outline


def svg_letter_outlines(svg_path: str | Path, fill: str) -> list[list[np.ndarray]]:
    """Each filled shape of colour `fill` in the SVG as one letter's contours, left to right, in SVG
    units with y up. A logo file can hold the same artwork twice, one copy on top of the other (an
    exporter's hidden layer): identical shapes count once."""
    from svgelements import SVG, Close, Color, Line, Move, Path as SvgPath, Shape

    want = Color(fill).hex.lower()
    seen, letters = set(), []
    for el in SVG.parse(str(svg_path)).elements():
        if not isinstance(el, Shape) or el.fill is None or el.fill.value is None or el.fill.hex.lower() != want:
            continue
        path = SvgPath(el)
        path.reify()
        if not len(path):
            continue
        key = tuple(round(v, 2) for v in path.bbox())
        if key in seen:
            continue
        seen.add(key)
        contours = []
        for sub in path.as_subpaths():
            pts = []
            for seg in SvgPath(sub):
                if isinstance(seg, Move):
                    pts.append((seg.end.x, seg.end.y))
                elif isinstance(seg, (Line, Close)):
                    if seg.end is not None:
                        pts.append((seg.end.x, seg.end.y))
                else:
                    pts += [(seg.point(t).x, seg.point(t).y) for t in np.linspace(0, 1, SVG_CURVE_STEPS + 1)[1:]]
            c = np.array(pts, float)
            c[:, 1] *= -1
            keep = np.concatenate([[True], np.hypot(*np.diff(c, axis=0).T) > 1e-6])   # repeated points
            c = c[keep]
            if len(c) >= 3:
                contours.append(c)
        if contours:
            letters.append(contours)
    letters.sort(key=lambda cs: min(c[:, 0].min() for c in cs))
    return letters


def svg_word_columns(svg_path: str | Path, fill: str, text: str, strokes: dict[str, list[Stroke]],
                     width_mm: float, spacing: float = DEFAULT_SPACING_MM, ring_overlap: float = RING_OVERLAP_MM,
                     butt_overlap: float = BUTT_OVERLAP_MM, auto: bool = True,
                     flat: bool = False) -> tuple[list[Letter], list[str]]:
    """Columns on letters drawn in an SVG (custom letterforms), instead of a font's. Each
    shape of colour `fill` is one letter of `text` (spaces skipped), left to right, scaled together so
    the word's ink is `width_mm` wide. Strokes are in each letter's ink box, so a stroke table measured
    on a similar font fits; letters with none get them worked out from the outline when `auto`.
    Returns the letters, centred on (0, 0), and the characters whose strokes were worked out."""
    chars = [c for c in text if not c.isspace()]
    shapes = svg_letter_outlines(svg_path, fill)
    if len(shapes) != len(chars):
        raise ValueError(f"{Path(svg_path).name} has {len(shapes)} shape(s) filled {fill}, and {text!r} has "
                         f"{len(chars)} letters: they must match one to one")
    x0 = min(c[:, 0].min() for cs in shapes for c in cs)
    x1 = max(c[:, 0].max() for cs in shapes for c in cs)
    k = width_mm / (x1 - x0)
    letters, worked_out = [], []
    for ch, contours in zip(chars, shapes):
        moved = [c * k for c in contours]
        allp = np.vstack(moved)
        box = (allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max())
        sts = strokes.get(ch)
        if sts is None:
            if not auto:
                raise ValueError(f"no strokes for {ch!r}")
            from .autostrokes import letter_strokes

            sts = letter_strokes(moved, box[3] - box[1], overlap_feet=flat)
            worked_out.append(ch)
        if flat:
            sts = tuck_order(sts, moved, box)
        cols = letter_columns(sts, moved, box, spacing, ring_overlap, butt_overlap, tuck=flat)
        letters.append(Letter(ch, cols, moved, box, sts, butt_overlap=butt_overlap))
    return _centre_word(letters), worked_out


def word_box(letters: list[Letter]) -> tuple[float, float, float, float]:
    return (min(lt.box[0] for lt in letters), min(lt.box[1] for lt in letters),
            max(lt.box[2] for lt in letters), max(lt.box[3] for lt in letters))


# ---------------------------------------------------------------- end caps and per-letter outlines

CAP_DEPTH_MM = 1.8           # how far the cap reaches into the column from its end edge
CAP_OVERHANG_MM = 0.3        # past the end edge, and past each side of the column
CAP_MEET_MM = 1.0            # where the edge is past the rung, the cap still overlaps the column this much
RUN_SIMPLIFY_MM = 0.1       # outline paths keep the letter's shape this closely (a click is ~0.1 mm)


def end_name(letter: Letter, ci: int, end: str) -> str:
    """How a job names a column end: the letter, its column number from 1, start or end ("N1.end")."""
    return f"{letter.char}{ci + 1}.{end}"


OPEN_REACH_MM = 3.0          # an end whose letter edge is at most this far past its rung is open
EDGE_PROBE_MM = 0.2          # rays to the edge start this far inside the rung's ends


def open_ends(letter: Letter) -> list[tuple[int, str, np.ndarray, np.ndarray, np.ndarray, float]]:
    """Column ends at the letter's edge (not butting another stroke): (column, "start"/"end", left,
    right, unit vector into the column, reach). Left and right are where the letter's edge is, past
    the column's own end rung: an angled terminal (an S) can reach a little past a square rung, and
    `reach` is how far the cap must come in to meet the rung. A butt end looks out through the stroke
    it meets, much further than OPEN_REACH_MM."""
    from shapely.geometry import MultiLineString

    boundary = MultiLineString([np.vstack([c, c[:1]]) for c in letter.outline])
    out = []
    for ci, col in enumerate(letter.columns):
        if col.closed or len(col.pairs) < 2:
            continue
        for end, k, nk in (("start", 0, 1), ("end", -1, -2)):
            L, R = (np.array(p, float) for p in col.pairs[k])
            mid = (L + R) / 2
            w = R - L
            if np.hypot(*w) < 0.5:
                continue
            w /= np.hypot(*w)
            t = np.array([-w[1], w[0]])
            if np.dot(t, np.mean(col.pairs[nk], axis=0) - mid) < 0:
                t = -t
            d_mid = _ray(boundary, mid + t * EDGE_PROBE_MM, -t, 4 * OPEN_REACH_MM)
            if d_mid is None or d_mid - EDGE_PROBE_MM > OPEN_REACH_MM:
                continue
            edge = []
            for P, inward in ((L, w), (R, -w)):
                q = P + inward * EDGE_PROBE_MM + t * EDGE_PROBE_MM
                d = _ray(boundary, q, -t, 4 * OPEN_REACH_MM)
                d = EDGE_PROBE_MM if d is None or d - EDGE_PROBE_MM > OPEN_REACH_MM else d
                edge.append(P - t * (d - EDGE_PROBE_MM))
            Le, Re = edge
            we = (Re - Le) / (np.hypot(*(Re - Le)) or 1)
            te = np.array([-we[1], we[0]])
            if np.dot(te, t) < 0:
                te = -te
            reach = max(float(np.dot(P - E, te)) for P, E in ((L, Le), (R, Re)))
            out.append((ci, end, Le, Re, te, max(reach, 0.0)))
    return out


def plan_caps(letter: Letter, start: tuple[float, float], depth: float = CAP_DEPTH_MM,
              overhang: float = CAP_OVERHANG_MM, skip: set[str] = frozenset()) -> list[Cap]:
    """A cap on each open end not in `skip`, in the order that keeps the jumps short, starting from
    `start` (where the letter's last column ends). Each cap is sewn from its nearer side."""
    todo = []
    for ci, end, L, R, t, reach in open_ends(letter):
        if end_name(letter, ci, end) in skip:
            continue
        w = (R - L) / np.hypot(*(R - L))
        deep = max(depth, reach + CAP_MEET_MM)
        a = (L - w * overhang - t * overhang, L - w * overhang + t * deep)
        b = (R + w * overhang - t * overhang, R + w * overhang + t * deep)
        todo.append((ci, end, a, b, float(np.hypot(*(R - L)) + 2 * overhang)))
    caps, here = [], np.array(start, float)
    while todo:
        def cost(item):
            _, _, a, b, _ = item
            return min(np.hypot(*(np.mean(a, axis=0) - here)), np.hypot(*(np.mean(b, axis=0) - here)))
        item = min(todo, key=cost)
        todo.remove(item)
        ci, end, a, b, width = item
        if np.hypot(*(np.mean(b, axis=0) - here)) < np.hypot(*(np.mean(a, axis=0) - here)):
            a, b = b, a
        caps.append(Cap(ci, end, [tuple(map(tuple, a)), tuple(map(tuple, b))], round(width, 2)))
        here = np.mean(b, axis=0)
    return caps


def plan_runs(letter: Letter, start: tuple[float, float]) -> list[np.ndarray]:
    """The letter's outline as closed paths (outer and counters), each starting at its point nearest
    to where the needle is, nearest contour first."""
    from shapely.geometry import LinearRing

    rings = []
    for c in letter.outline:
        r = LinearRing(c).simplify(RUN_SIMPLIFY_MM)
        pts = np.array(r.coords)[:-1]
        if len(pts) >= 3:
            rings.append(pts)
    out, here = [], np.array(start, float)
    while rings:
        k = min(range(len(rings)), key=lambda i: np.hypot(*(rings[i] - here).T).min())
        ring = rings.pop(k)
        j = int(np.argmin(np.hypot(*(ring - here).T)))
        path = np.vstack([ring[j:], ring[:j], ring[j:j + 1]])
        out.append(path)
        here = path[-1]
    return out


def plan_letter_extras(letters: list[Letter], caps: bool, runs: bool, depth: float = CAP_DEPTH_MM,
                       overhang: float = CAP_OVERHANG_MM, skip: set[str] = frozenset()) -> None:
    """Fill each letter's caps and outline runs, sewn after its columns."""
    for lt in letters:
        last = lt.columns[-1]
        here = tuple(np.mean(last.pairs[-1], axis=0))     # a ring ends at its start, or past it
        lt.caps = plan_caps(lt, here, depth, overhang, skip) if caps else []
        if lt.caps:
            here = tuple(np.mean(lt.caps[-1].pairs[1], axis=0))
        lt.runs = plan_runs(lt, here) if runs else []


def sewing_runs(path: str | Path, letters: list[Letter], at: tuple[float, float] | None = None) -> list[tuple]:
    """What the file sews near the word, in order, as (letter, kind, index, points): kind "column"
    (satin across a column), "cap" (short stitches inside a cap, off the edge) or "outline" (short
    stitches along the letter's edge). Stretches too short to be more than a tie or a pass are left out."""
    import pyembroidery
    from shapely.geometry import MultiLineString, Point
    from shapely.strtree import STRtree

    p = pyembroidery.read(str(path))
    a = np.array([(x / 10.0, -y / 10.0) for x, y, c in p.stitches if (c & pyembroidery.COMMAND_MASK) == pyembroidery.STITCH])
    x0, y0, x1, y1 = word_box(letters)
    if at is None:
        a += [(x0 + x1) / 2 - (a[:, 0].min() + a[:, 0].max()) / 2, (y0 + y1) / 2 - (a[:, 1].min() + a[:, 1].max()) / 2]
    else:
        a -= [at[0] - (x0 + x1) / 2, at[1] - (y0 + y1) / 2]
    seg = np.hypot(*np.diff(a, axis=0).T)
    longest = np.maximum(np.concatenate([[0], seg]), np.concatenate([seg, [0]]))
    col_ids, col_shapes, cap_ids, cap_shapes, cap_dirs = [], [], [], [], []
    for li, lt in enumerate(letters):
        for ci, c in enumerate(lt.columns):
            col_ids.append((li, ci))
            col_shapes.append(c.shape())
        for k, cp in enumerate(lt.caps):
            cap_ids.append((li, k))
            cap_shapes.append(cp.shape().buffer(0.15))
            o, i = (np.array(q) for q in cp.pairs[0])
            cap_dirs.append((i - o) / (np.hypot(*(i - o)) or 1))
    edges = [MultiLineString([np.vstack([c, c[:1]]) for c in lt.outline]) for lt in letters]
    col_tree = STRtree(col_shapes)
    steps = np.diff(a, axis=0)
    labels = []
    for n, (q, ln) in enumerate(zip(a, longest)):
        pt = Point(q)
        lab = None
        if ln >= SATIN_MIN_MM:
            k = col_tree.nearest(pt)
            if col_shapes[k].distance(pt) <= STRAY_MM:
                lab = (col_ids[k][0], "column", col_ids[k][1])
        else:
            # A cap stitch runs along the column, across the end edge; an outline runs along the edge.
            adjacent = [steps[j] for j in (n - 1, n) if 0 <= j < len(steps) and np.hypot(*steps[j]) >= CAP_STITCH_MIN_MM]
            for (cli, k), sh, d in zip(cap_ids, cap_shapes, cap_dirs):
                if sh.contains(pt) and any(abs(np.dot(s, d)) / np.hypot(*s) >= 0.7 for s in adjacent):
                    lab = (cli, "cap", k)
                    break
            if lab is None:
                d_edge = [e.distance(pt) for e in edges]
                li = int(np.argmin(d_edge))
                if d_edge[li] <= OUTLINE_NEAR_MM:
                    lab = (li, "outline", 0)
        labels.append(lab)
    out = []
    for lab in labels:
        lab = lab or (None, None, None)
        if out and tuple(out[-1][:3]) == lab:
            out[-1][3] += 1
        else:
            out.append([*lab, 1])
    keep = [tuple(r) for r in out if r[0] is not None and r[3] >= MIN_POINTS[r[1]]]
    merged = []
    for r in keep:
        if merged and merged[-1][:3] == r[:3]:
            merged[-1] = (*r[:3], merged[-1][3] + r[3])
        else:
            merged.append(r)
    return merged


OUTLINE_NEAR_MM = 0.3      # a short stitch this close to the letter's edge is the outline
CAP_STITCH_MIN_MM = 1.2    # a cap's stitches are about its depth plus overhang long
# Column: as MIN_RUN. A cap is dozens of points (56-94 on a 12.9 mm word); an outline turning a corner beside a
# cap passes a few points along the column inside it, which mustn't count as the cap sewn again.
# An outline is hundreds of points; a column's tie-off and the next one's tie-in on the letter's edge
# (an N's diagonal ending at its corners) are about six.
MIN_POINTS = {"column": 6, "cap": 16, "outline": 12}


def check_letter_order(path: str | Path, letters: list[Letter], at: tuple[float, float] | None = None) -> dict:
    """Per letter: its columns, then its caps (each in one piece, in the planned order), then its
    outline, before the next letter starts."""
    seq = sewing_runs(path, letters, at)
    rows, blocks = [], []
    for li, kind, idx, _ in seq:
        if not blocks or blocks[-1] != li:
            blocks.append(li)
    for li, lt in enumerate(letters):
        mine = [(k, i) for l, k, i, _ in seq if l == li]
        kinds = []
        for k, _ in mine:
            if not kinds or kinds[-1] != k:
                kinds.append(k)
        want = ["column"] + (["cap"] if lt.caps else []) + (["outline"] if lt.runs else [])
        caps_seen = [i for k, i in mine if k == "cap"]
        pieces = {k: caps_seen.count(k) for k in range(len(lt.caps))}
        caps_ok = caps_seen == list(range(len(lt.caps)))
        rows.append({"letter": lt.char, "order": kinds, "caps_sewn": len(set(caps_seen)), "caps": len(lt.caps),
                     "cap_pieces": [pieces[k] for k in range(len(lt.caps))],
                     "ok": kinds == want and caps_ok})
    return {"letters": rows, "letters_one_block_each": blocks == list(range(len(letters))),
            "ok": all(r["ok"] for r in rows) and blocks == list(range(len(letters)))}


# ---------------------------------------------------------------- checks

OVERLAP_MM2 = 1.0          # per letter, beyond the butt joins' own overlap: more is two column ends on one spot
UNCOVERED_PCT = 3.0        # of the letter's area: more and foam shows
OUTSIDE_PCT = 3.0


def check_letter(letter: Letter, uncovered_pct: float = None, outside_pct: float = None) -> dict:
    """Columns against the letter's outline: how much of the letter they leave bare, how much they
    stick out, and how much any two columns overlap (each overlap is foam perforated twice). The
    bare and outside limits default to puff's (UNCOVERED_PCT, OUTSIDE_PCT)."""
    uncovered_limit = UNCOVERED_PCT if uncovered_pct is None else uncovered_pct
    outside_limit = OUTSIDE_PCT if outside_pct is None else outside_pct
    glyph = letter.glyph()
    shapes = [c.shape() for c in letter.columns]
    from shapely.ops import unary_union

    union = unary_union(shapes)
    overlaps = []
    for i in range(len(shapes)):
        for j in range(i + 1, len(shapes)):
            a = shapes[i].intersection(shapes[j]).area
            if a > 0.01:
                overlaps.append({"columns": [i + 1, j + 1], "mm2": round(a, 2)})
    bare = glyph.difference(unary_union([union] + [c.shape() for c in letter.caps]))
    gaps = bare_patches(bare)
    uncovered = glyph.difference(union).area / glyph.area * 100
    outside = union.difference(glyph).area / glyph.area * 100
    overlap = sum(o["mm2"] for o in overlaps)
    # Butt joins overlap by letter.butt_overlap on purpose, across an end's width (a puff column is at
    # most ~12 mm). A ring's overlap with itself is inside one column, so it isn't counted here.
    allowed = OVERLAP_MM2 + (len(letter.columns) - 1) * 2 * max(BUTT_OVERLAP_MM, letter.butt_overlap) * 12.0
    twisted = sum(_crossed_pairs(c) for c in letter.columns)
    ok = overlap <= allowed and uncovered <= uncovered_limit and outside <= outside_limit and twisted == 0
    return {"letter": letter.char, "columns": len(letter.columns),
            "joins": len(letter.columns) - 1 + sum(c.closed for c in letter.columns),
            "uncovered_pct": round(uncovered, 2), "outside_pct": round(outside, 2),
            "overlap_mm2": round(overlap, 2), "overlaps": overlaps, "crossed_pairs": twisted,
            "bare_patches": gaps, "ok": ok}


GAP_MIN_WIDTH_MM = 0.4     # a bare patch at least this wide, between the outline and the columns (or caps)
GAP_MIN_AREA_MM2 = 1.0     # and at least this big, shows foam on puff


def bare_patches(bare) -> list[dict]:
    """Patches of the letter that no column or cap covers, wide and big enough to show foam: thinner
    slivers are covered by pull compensation and the outline. Each as area (mm2) and centre."""
    if bare.is_empty:
        return []
    core = bare.buffer(-GAP_MIN_WIDTH_MM / 2)
    out = []
    for g in getattr(bare, "geoms", [bare]):
        if g.area >= GAP_MIN_AREA_MM2 and g.intersects(core):
            c = g.representative_point()
            out.append({"mm2": round(g.area, 2), "at": [round(c.x, 1), round(c.y, 1)]})
    return out


def _crossed_pairs(col: Column) -> int:
    """Neighbouring pairs whose stitch lines cross: the satin twists there, sewing a knot of stitches
    on one spot and leaving a gap beside it."""
    from shapely.geometry import LineString

    n = 0
    for (l0, r0), (l1, r1) in zip(col.pairs, col.pairs[1:]):
        a, b = LineString([l0, r0]), LineString([l1, r1])
        if a.length and b.length and a.crosses(b):
            n += 1
    return n


STRAY_MM = 1.0             # a needle point this far from every column is off the letters
MIN_RUN = 6                # fewer stitches in a row inside one column is travel passing through
NEAR_MM = 3.0
SATIN_MIN_MM = 3.0         # a stitch at least this long is satin across a column (puff columns are wider)


SATIN_SHARE = 0.6          # a stitch this share of the column's width where it lies is satin across it
SATIN_FLOOR_MM = 0.5       # ...and never shorter than this (tie-in stitches)


def _satin_min(a: np.ndarray, letters: list[Letter]) -> np.ndarray:
    """For each stitch a[i] -> a[i+1]: how long it must be to count as satin, from the width of the
    planned column nearest its middle, capped at SATIN_MIN_MM (wide letters)."""
    from scipy.spatial import cKDTree

    mids, widths = [], []
    for lt in letters:
        for col in lt.columns:
            for l, r in col.pairs:
                mids.append(((l[0] + r[0]) / 2, (l[1] + r[1]) / 2))
                widths.append(math.hypot(r[0] - l[0], r[1] - l[1]))
    if len(a) < 2:
        return np.zeros(0)
    if not mids:
        return np.full(len(a) - 1, SATIN_MIN_MM)
    _, k = cKDTree(np.array(mids)).query((a[:-1] + a[1:]) / 2)
    return np.clip(SATIN_SHARE * np.array(widths)[k], SATIN_FLOOR_MM, SATIN_MIN_MM)


def _satin_points(path, letters: list[Letter], at):
    """The file's satin needle points near the word, lined up with the letters, each with the
    (letter, column) it falls in (None: off every column). Also the stray count, the column ids and
    the running stitches inside the letters (mm)."""
    import pyembroidery
    from shapely.geometry import Point
    from shapely.strtree import STRtree

    p = pyembroidery.read(str(path))
    pts = [(x / 10.0, -y / 10.0) for x, y, c in p.stitches if (c & pyembroidery.COMMAND_MASK) == pyembroidery.STITCH]
    if not pts:
        raise ValueError(f"{path} has no stitches")
    a = np.array(pts)
    x0, y0, x1, y1 = word_box(letters)
    if at is None:
        a += [(x0 + x1) / 2 - (a[:, 0].min() + a[:, 0].max()) / 2, (y0 + y1) / 2 - (a[:, 1].min() + a[:, 1].max()) / 2]
    else:
        a -= [at[0] - (x0 + x1) / 2, at[1] - (y0 + y1) / 2]
    near = ((a[:, 0] > x0 - NEAR_MM) & (a[:, 0] < x1 + NEAR_MM) & (a[:, 1] > y0 - NEAR_MM) & (a[:, 1] < y1 + NEAR_MM))
    if not near.any():
        raise ValueError(f"no needle points near the word at {at}")
    # Satin only: a satin stitch spans the column. Short stitches are runs (an outline sewn over the
    # columns afterwards, tie-ins), which would count as visiting a column a second time. "Spans" is
    # measured against the column's own width where the stitch is: a fixed length read none of a
    # 5.75 mm cap line's satin (its columns are 1 to 1.6 mm wide), and all of a wide letter's runs.
    seg = np.hypot(*np.diff(a, axis=0).T)
    satin_seg = seg >= _satin_min(a, letters)
    satin = np.concatenate([[False], satin_seg]) | np.concatenate([satin_seg, [False]])
    run_inside = _runs_inside(a, ~satin, near, letters)
    a = a[satin & near]
    ids, shapes = [], []
    for li, lt in enumerate(letters):
        for ci, col in enumerate(lt.columns):
            ids.append((li, ci))
            shapes.append(col.shape())
    tree = STRtree(shapes)
    seq, stray = [], 0
    for q in a:
        pt = Point(q)
        k = tree.nearest(pt)
        if shapes[k].distance(pt) > STRAY_MM:
            stray += 1
            seq.append(None)
        else:
            seq.append(ids[k])
    return a, seq, stray, ids, run_inside


RING_SEWN_MIN = 0.5        # of the planned overlap, read back from the file
RING_SPAN_SHARE = 0.8      # a ring's satin stitch spans at least this share of its median span across it


def _across(loop, p: np.ndarray, q: np.ndarray) -> float:
    """How far a stitch p -> q reaches across a column with centre line `loop`: the distance between
    its ends' signed offsets from the line, 0 if both lie on one side."""
    from shapely.geometry import Point

    def side(x):
        t = loop.project(Point(x))
        c = np.array(loop.interpolate(t).coords[0])
        u = np.array(loop.interpolate(min(loop.length, t + 0.05)).coords[0]) - np.array(
            loop.interpolate(max(0.0, t - 0.05)).coords[0])
        u = u / (np.hypot(*u) or 1)
        return float(u[0] * (x[1] - c[1]) - u[1] * (x[0] - c[0]))

    o0, o1 = side(p), side(q)
    return abs(o0 - o1) if o0 * o1 < 0 else 0.0


def ring_overlaps(path: str | Path, letters: list[Letter], at: tuple[float, float] | None = None) -> list[dict]:
    """For each ring (closed column) with an overlap: how far the file really sews it on past its
    start. Its satin points are placed along the ring's centre and the distance travelled is
    unwrapped; past one full loop is the overlap."""
    from shapely.geometry import LineString, Point

    a, seq, *_ = _satin_points(path, letters, at)
    out = []
    for li, lt in enumerate(letters):
        for ci, col in enumerate(lt.columns):
            if col.ring_end is None:
                continue
            idx = [i for i, v in enumerate(seq) if v == (li, ci)]
            # The column's longest unbroken stretch: another column's points near a join don't count.
            best, cur = [], []
            for i in idx:
                cur = cur + [i] if cur and i - cur[-1] <= 3 else [i]
                if len(cur) > len(best):
                    best = cur
            mids = [np.mean(np.array(p), axis=0) for p in col.pairs[:col.ring_end + 1]]
            loop = LineString(mids)
            L = loop.length
            row = {"letter": lt.char, "column": ci + 1, "ring": f"{lt.char}{ci + 1}",
                   "planned_mm": round(col.overlap_mm, 2), "loop_mm": round(L, 1)}
            if len(best) < 10 or L == 0:
                out.append({**row, "sewn_mm": None, "ok": False})
                continue
            # Only stitches right across the ring, as far as its satin spans: the underlay zigzags inside
            # its edges and read as a second loop (an O on a hat read 35 mm past its start).
            span = np.array([_across(loop, a[i], a[j]) for i, j in zip(best, best[1:])])
            wide = span >= RING_SPAN_SHARE * float(np.median(span))
            # each stitch by its middle, which lies on the centre line: a needle point on a slanted
            # stitch's end projects ahead of or behind it
            keep = [(a[i] + a[j]) / 2 for (i, j), w in zip(zip(best, best[1:]), wide) if w]
            if len(keep) < 10:
                out.append({**row, "sewn_mm": None, "ok": False})
                continue
            s = np.array([loop.project(Point(m)) for m in keep])
            d = np.diff(s)
            d = (d + L / 2) % L - L / 2              # unwrap across the seam
            # How far along the ring the satin reaches, end to end, not its net travel: the 0 in "2026"
            # steps back from 1.1 mm to its start before sewing on, and net travel read that as a
            # gap (-0.08 mm) though the satin closed 0.35 mm past its start.
            c = np.concatenate([[0.0], np.cumsum(d)])
            sewn = float(c.max() - c.min()) - L
            out.append({**row, "sewn_mm": round(sewn, 2), "ok": sewn >= RING_SEWN_MIN * col.overlap_mm})
    return out


def stitch_file_columns(path: str | Path, letters: list[Letter], at: tuple[float, float] | None = None) -> list[dict]:
    """What the machine file really sews, letter by letter: each needle point is put in the column it
    falls in, and the order of those runs is read back. A column sewn in more than one run was split
    (ES cut it, or another column came between); runs out of order mean the sewing order changed.
    The file is lined up with the letters by the centres of their boxes, which pull compensation
    leaves in place. With `at` (where the word's centre is in the file's coordinates, y up), only
    the needle points within a few mm of the word count, so other items in the file are left out."""
    a, seq, stray, ids, run_inside = _satin_points(path, letters, at)
    runs = []
    for v in seq:
        if runs and runs[-1][0] == v:
            runs[-1][1] += 1
        else:
            runs.append([v, 1])
    sewn = [v for v, n in runs if v is not None and n >= MIN_RUN]
    order = []
    for v in sewn:
        if not order or order[-1] != v:
            order.append(v)
    out = []
    expected = list(ids)
    for li, lt in enumerate(letters):
        mine = [v for v in order if v[0] == li]
        pieces = {ci: sum(1 for v in mine if v[1] == ci) for ci in range(len(lt.columns))}
        first_seen = []
        for v in mine:
            if v[1] not in first_seen:
                first_seen.append(v[1])
        out.append({"letter": lt.char, "columns": len(lt.columns),
                    "sewn_order": [c + 1 for c in first_seen],
                    "in_order": first_seen == list(range(len(lt.columns))),
                    "pieces": [pieces[c] for c in range(len(lt.columns))],
                    "ok": first_seen == list(range(len(lt.columns))) and all(n == 1 for n in pieces.values())})
    letter_order = []
    for v in order:
        if not letter_order or letter_order[-1] != v[0]:
            letter_order.append(v[0])
    out.append({"letters_in_order": letter_order == sorted(set(letter_order)) and len(letter_order) == len(letters),
                "stray_points": stray, "points": len(a), "expected_columns": len(expected),
                "run_inside_mm": run_inside,
                "stray_at": [[round(float(a[i][0]), 2), round(float(a[i][1]), 2)]
                             for i, v in enumerate(seq) if v is None][:20]})
    return out


CLOSEUP_PAD_MM = 3.0
CLOSEUP_PX_PER_MM = 60


def sewn_closeups(path: str | Path, letters: list[Letter], at, out_dir: str | Path,
                  columns: list[tuple[int, int]] = (), points: list = ()) -> list[Path]:
    """Close-ups for a person to judge what `stitch_file_columns` flagged: each flagged column
    (letter index, column index) and each stray point, with the planned columns (grey outlines) under
    the file's stitches (green; jumps red), lined up as the check lines them up."""
    import pyembroidery
    from PIL import Image, ImageDraw

    p = pyembroidery.read(str(path))
    raw = [(x / 10.0, -y / 10.0, c & pyembroidery.COMMAND_MASK) for x, y, c in p.stitches]
    a = np.array([(x, y) for x, y, _ in raw])
    x0, y0, x1, y1 = word_box(letters)
    if at is None:
        st = np.array([(x, y) for x, y, c in raw if c == pyembroidery.STITCH])
        a += [(x0 + x1) / 2 - (st[:, 0].min() + st[:, 0].max()) / 2, (y0 + y1) / 2 - (st[:, 1].min() + st[:, 1].max()) / 2]
    else:
        a -= [at[0] - (x0 + x1) / 2, at[1] - (y0 + y1) / 2]
    boxes = []
    for li, ci in columns:
        sh = letters[li].columns[ci].shape()
        boxes.append((f"{letters[li].char}{li + 1}_col{ci + 1}", sh.bounds))
    for k, q in enumerate(points):
        boxes.append((f"stray{k + 1}", (q[0], q[1], q[0], q[1])))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for name, (bx0, by0, bx1, by1) in boxes:
        bx0, by0, bx1, by1 = bx0 - CLOSEUP_PAD_MM, by0 - CLOSEUP_PAD_MM, bx1 + CLOSEUP_PAD_MM, by1 + CLOSEUP_PAD_MM
        S = CLOSEUP_PX_PER_MM
        img = Image.new("RGB", (max(1, int((bx1 - bx0) * S)), max(1, int((by1 - by0) * S))), (25, 25, 25))
        d = ImageDraw.Draw(img)
        tr = lambda q: ((q[0] - bx0) * S, (by1 - q[1]) * S)   # noqa: E731
        for lt in letters:
            for col in lt.columns:
                ring = np.array(col.shape().exterior.coords)
                d.line([tr(q) for q in ring], fill=(120, 120, 120), width=2)
        for i in range(1, len(a)):
            c = raw[i][2]
            if c not in (pyembroidery.STITCH, pyembroidery.JUMP):
                continue
            d.line([tr(a[i - 1]), tr(a[i])], fill=(80, 220, 120) if c == pyembroidery.STITCH else (230, 60, 60), width=1)
        for q in points:
            cx, cy = tr(q)
            d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], outline=(255, 210, 0), width=2)
        f = out_dir / f"sewn_{name}.png"
        img.save(f)
        made.append(f)
    return made


RUN_DEEP_MM = 1.0          # a running stitch this far inside a letter perforates the top of the puff
RUN_MIN_POINTS = 5         # fewer in a row are a tie-in or tie-off


def _runs_inside(a: np.ndarray, short: np.ndarray, near: np.ndarray, letters: list[Letter]) -> float:
    """Length in mm of running stitches (travel, or an outline that found a slit) deep inside the letters,
    in stretches of at least RUN_MIN_POINTS: tie-ins and tie-offs are shorter."""
    from shapely.geometry import Point
    from shapely.ops import unary_union

    glyph = unary_union([lt.glyph() for lt in letters])
    edge = glyph.boundary
    deep = np.array([bool(short[i] and near[i]) and glyph.contains(Point(a[i])) and edge.distance(Point(a[i])) > RUN_DEEP_MM
                     for i in range(len(a))])
    total, i = 0.0, 0
    while i < len(a):
        if not deep[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(a) and deep[j + 1]:
            j += 1
        if j - i + 1 >= RUN_MIN_POINTS:
            # A triple run goes over each stitch three times: count the ground it covers, not the thread.
            total += float(np.ptp(a[i:j + 1, 0]) + np.ptp(a[i:j + 1, 1]))
        i = j + 1
    return round(total, 1)


def preview_png(letters: list[Letter], path: str | Path, px_per_mm: float = 8.0) -> Path:
    """The letters' outlines (black), each column's pairs (coloured, start marked with a dot, end with
    a square) and its number in sewing order."""
    from PIL import Image, ImageDraw

    x0, y0, x1, y1 = word_box(letters)
    pad = 10
    W, H = int((x1 - x0) * px_per_mm) + 2 * pad, int((y1 - y0) * px_per_mm) + 2 * pad
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    tr = lambda p: (pad + (p[0] - x0) * px_per_mm, pad + (y1 - p[1]) * px_per_mm)
    colours = [(214, 39, 40), (31, 119, 180), (44, 160, 44), (148, 103, 189), (255, 127, 14)]
    n = 0
    for lt in letters:
        for k, col in enumerate(lt.columns):
            n += 1
            c = colours[k % len(colours)]
            for q in col.quads():
                for g in getattr(q, "geoms", [q]):
                    d.polygon([tr(p) for p in g.exterior.coords], fill=tuple(int(v * 0.25 + 191) for v in c))
            for l, r in col.pairs:
                d.line([tr(l), tr(r)], fill=c, width=1)
            d.line([tr(l) for l, _ in col.pairs], fill=c, width=2)
            d.line([tr(r) for _, r in col.pairs], fill=c, width=2)
            sx, sy = tr(np.mean(col.pairs[0], axis=0))
            ex, ey = tr(np.mean(col.pairs[-1], axis=0))
            d.ellipse([sx - 5, sy - 5, sx + 5, sy + 5], fill=c)
            d.rectangle([ex - 4, ey - 4, ex + 4, ey + 4], outline=c, width=2)
            mx, my = tr(np.mean(col.pairs[len(col.pairs) // 2], axis=0))
            d.text((mx + 4, my - 6), str(n), fill="black")
        for cont in lt.outline:
            d.line([tr(p) for p in np.vstack([cont, cont[:1]])], fill="black", width=1)
        for k, cp in enumerate(lt.caps):
            d.polygon([tr(p) for p in cp.shape().exterior.coords], fill=(60, 60, 60))
            cx, cy = tr(cp.centre)
            d.text((cx + 4, cy + 2), f"c{k + 1}", fill=(200, 0, 0))
        for run in lt.runs:
            d.line([tr(p) for p in run], fill=(0, 150, 150), width=1)
    img.save(path)
    return Path(path)


# ---------------------------------------------------------------- built-in strokes

def _s(path, **kw) -> Stroke:
    return Stroke([tuple(p) for p in path], **kw)


def _arc(cx, cy, rx, ry, a0, a1, n=9):
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / (n - 1))),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / (n - 1)))) for i in range(n)]


# Strokes measured from one font file's points (u, v in the letter's ink box, v up). They only fit that
# file: another version of the "same" font has other stem widths, so each table is keyed by the
# file's capital height and its H's advance, and isn't used on a file that doesn't match.

# Lato Black 2.x (cap height 1451, H advance 1547): the Google Fonts file.
LATO_BLACK_2 = {
    "H": [_s([(0.1215, 0.0), (0.1215, 1.0)]),
          _s([(0.243, 0.5031), (0.757, 0.5031)]),
          _s([(0.8785, 1.0), (0.8785, 0.0)])],
    "U": [_s([(0.12265, 1.0), (0.12265, 0.62)] + _arc(0.5, 0.411, 0.37735, 0.3237, 180, 360)
             + [(0.87735, 0.62), (0.87735, 1.0)], smooth=True)],
    "D": [_s([(0.11605, 0.0), (0.11605, 1.0)]),
          _s([(0.2321, 0.919), (0.33, 0.919)] + _arc(0.4289, 0.5, 0.4528, 0.419, 90, -90, 11)
             + [(0.33, 0.081), (0.2321, 0.081)], smooth=True)],
    "S": [_s([(0.905, 0.835), (0.73, 0.905), (0.545, 0.919), (0.33, 0.885), (0.2, 0.75), (0.25, 0.6),
              (0.5, 0.52), (0.76, 0.43), (0.858, 0.3), (0.76, 0.13), (0.49, 0.082), (0.25, 0.12),
              (0.075, 0.19)], smooth=True)],
    "O": [_s(_arc(0.5003, 0.5003, 0.39675, 0.4149, 90, -270, 25)[:-1], smooth=True, closed=True)],
    "N": [_s([(0.1066, 0.0), (0.1066, 1.0)]),
          _s([(0.2132, 0.8128), (0.7868, 0.1882)], start_cut="vertical", end_cut="vertical"),
          _s([(0.8934, 0.0), (0.8934, 1.0)])],
}

# Lato Black 1.x (cap height 1457, H advance 1512): the file EmbroideryStudio uses.
LATO_BLACK_1 = {
    "H": [_s([(0.13365, 0.0), (0.13365, 1.0)]),
          _s([(0.2673, 0.5024), (0.7327, 0.5024)]),
          _s([(0.86635, 1.0), (0.86635, 0.0)])],
    "U": [_s([(0.135, 1.0), (0.135, 0.62)] + _arc(0.5, 0.4141, 0.365, 0.318, 180, 360)
             + [(0.865, 0.62), (0.865, 1.0)], smooth=True)],
    "D": [_s([(0.1286, 0.0), (0.1286, 1.0)]),
          _s([(0.2572, 0.9108), (0.33, 0.9108)] + _arc(0.4281, 0.5003, 0.4409, 0.4105, 90, -90, 11)
             + [(0.33, 0.0898), (0.2572, 0.0898)], smooth=True)],
    "S": [_s([(0.9, 0.83), (0.73, 0.895), (0.545, 0.91), (0.34, 0.875), (0.21, 0.75), (0.26, 0.6),
              (0.5, 0.52), (0.75, 0.43), (0.845, 0.3), (0.76, 0.14), (0.49, 0.09), (0.25, 0.125),
              (0.075, 0.19)], smooth=True)],
    "O": [_s(_arc(0.5007, 0.5003, 0.38575, 0.40615, 90, -270, 25)[:-1], smooth=True, closed=True)],
    "N": [_s([(0.11715, 0.0), (0.11715, 1.0)]),
          _s([(0.2343, 0.7927), (0.7657, 0.2063)], start_cut="vertical", end_cut="vertical"),
          _s([(0.88285, 0.0), (0.88285, 1.0)])],
}

BUILTIN = {"lato black": {(1451, 1547): LATO_BLACK_2, (1457, 1512): LATO_BLACK_1}}


def font_fingerprint(font_path: str | Path) -> tuple[int, int]:
    """(capital height, H's advance) in font units: tells versions of a font apart."""
    from fontTools.ttLib import TTFont

    f = TTFont(str(font_path), fontNumber=0)
    cmap = f.getBestCmap()
    h = cmap.get(ord("H"))
    return int(getattr(f["OS/2"], "sCapHeight", 0) or 0), int(f["hmtx"][h][0]) if h else 0


def builtin_strokes(font: str, font_path: str | Path | None = None) -> dict[str, list[Stroke]]:
    """Built-in strokes for this font, if its file is a version they were measured from."""
    versions = BUILTIN.get(font.strip().lower(), {})
    if font_path is None:
        return dict(next(iter(versions.values()), {}))
    return dict(versions.get(font_fingerprint(font_path), {}))