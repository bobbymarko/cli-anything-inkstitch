# SPDX-License-Identifier: Apache-2.0
# Derived from stitchpilot (same author, Apache-2.0), moved here so the column planner has one home.
# Public names, signatures and return types are kept as they were there, so code written against it
# keeps working; new behaviour goes behind new parameters whose defaults reproduce the old output.
"""Stroke-like shapes of artwork as satin columns, the way a digitizer would stitch them.

EmbroideryStudio's click-to-stitch traces a shape's outline as one turning satin: lettering wraps
round its corners, a tapered tip ends blunt with a spike, and an end cut at an angle comes out stepped.
Here each satin-width shape gets its centrelines from its medial axis (`autostrokes`), its edges from
its own outline (`columns.stroke_column`), so a column tapers into a tip and ends along the art's edge.

Planning is geometry only. A group of shapes is digitized as columns only if every shape's plan
passes `check`; otherwise the group keeps EmbroideryStudio's click-to-stitch, and the report says why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import columns as col

FLAT_UNCOVERED_PCT = 5.0    # flat work: pull compensation covers a thin edge, tips may stay bare
FLAT_OUTSIDE_PCT = 3.0
FLAT_OVERLAP_MM2 = 12.0     # per join: a V's two legs overlap where they meet (a 17 mm W: ~11 mm2)
SEAM_OVERLAP_MM = 0.3       # two columns side by side (a dancer's two strokes) each reach this far into the
                            # other, so pull doesn't open a gap along their seam (ends butting: BUTT_OVERLAP_MM)
FLAT_OVERLAP_PER_WIDTH2 = 1.0   # ...or a square of the strokes' median width, if more
MIN_TIP_MM = 0.4            # satin narrower than this piles thread on one spot: the tip ends here
                            # (0.6 left a long tapered swoosh 4 mm short of its point)
SPACING_FRACTION = 0.04     # pairs on curves, as a share of the shape's longer side
MIN_SPACING_MM, MAX_SPACING_MM = 0.6, 2.0


@dataclass
class ShapeColumns:
    shape: int
    letter: col.Letter | None
    check: dict = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.letter is not None and bool(self.check.get("ok"))


@dataclass
class GroupColumns:
    group: int
    shapes: list[ShapeColumns]

    @property
    def ok(self) -> bool:
        return bool(self.shapes) and all(s.ok for s in self.shapes)

    @property
    def columns(self) -> int:
        return sum(len(s.letter.columns) for s in self.shapes if s.letter)

    def reason(self) -> str:
        bad = [s for s in self.shapes if not s.ok]
        if not bad:
            return "every shape's columns cover it"
        parts = []
        for s in bad:
            if s.error:
                parts.append(f"shape {s.shape}: {s.error}")
            else:
                c = s.check
                parts.append(f"shape {s.shape}: {c.get('sewable_bare_pct')}% bare, {c.get('outside_pct')}% outside, "
                             f"{c.get('overlap_mm2')} mm2 overlap, {c.get('crossed_pairs')} crossed pairs")
        return "; ".join(parts)


def _load_shapes(svg_path):
    """Every filled shape in an SVG: [(svgelements Path, fill hex)], in drawing order."""
    from svgelements import SVG, Path as SvgPath, Shape

    shapes = []
    for el in SVG.parse(str(svg_path)).elements():
        if isinstance(el, Shape) and el.fill is not None and el.fill.value is not None:
            p = SvgPath(el)
            p.reify()
            if len(p):
                shapes.append((p, el.fill.hex))
    if not shapes:
        raise ValueError(f"no filled shapes in {svg_path}")
    return shapes


def _outline(sp, px_per_unit: float) -> np.ndarray:
    """Points along a subpath, sampled per segment (about one per output pixel).
    Path.npoint would measure exact arc lengths first, which is very slow on detailed art."""
    from svgelements import Close, Line, Move

    pts = []
    for seg in sp:
        if isinstance(seg, Move) or seg.start is None or seg.end is None:
            continue
        if isinstance(seg, (Line, Close)):
            pts += [(seg.start.x, seg.start.y), (seg.end.x, seg.end.y)]
            continue
        coarse = seg.npoint(np.linspace(0, 1, 9))
        n = int(min(max(np.hypot(*np.diff(coarse, axis=0).T).sum() * px_per_unit, 8), 4000))
        pts += [tuple(q) for q in seg.npoint(np.linspace(0, 1, n + 1))]
    return np.array(pts, float).reshape(-1, 2)


def shape_contours_mm(svg_path: str | Path, width_mm: float) -> tuple[list[list[np.ndarray]], float, float]:
    """Each filled shape's contours in mm (same order as the planner's shapes), with the art's
    bounding box from (0, 0) to (width, height), y up. Returns (contours per shape, width, height)."""
    from svgelements import Path as SvgPath

    shapes = _load_shapes(svg_path)
    boxes = [p.bbox() for p, _ in shapes]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    k = width_mm / (x1 - x0)
    out = []
    for p, _ in shapes:
        cs = []
        for sub in p.as_subpaths():
            pts = _outline(SvgPath(sub), k * 10)
            if len(pts) < 3 or not np.isfinite(pts).all():
                continue
            c = np.column_stack([(pts[:, 0] - x0) * k, (y1 - pts[:, 1]) * k])
            keep = np.concatenate([[True], np.hypot(*np.diff(c, axis=0).T) > 1e-6])
            c = c[keep]
            if len(c) >= 3:
                cs.append(c)
        out.append(cs)
    return out, width_mm, (y1 - y0) * k


def _width(pair) -> float:
    (lx, ly), (rx, ry) = pair
    return float(np.hypot(lx - rx, ly - ry))


def trim_tips(c: col.Column, min_mm: float = MIN_TIP_MM) -> col.Column:
    """End an open column where it's still `min_mm` wide: satin can't sew down to a point."""
    if c.closed or len(c.pairs) <= 2:
        return c
    pairs, curve = list(c.pairs), list(c.curve)
    while len(pairs) > 2 and _width(pairs[0]) < min_mm:
        pairs.pop(0)
        curve.pop(0)
    while len(pairs) > 2 and _width(pairs[-1]) < min_mm:
        pairs.pop()
        curve.pop()
    curve[0] = curve[-1] = False
    return col.Column(pairs=pairs, curve=curve, closed=False)


def plan_shape(index: int, contours: list[np.ndarray], strokes: list[col.Stroke] | None = None) -> ShapeColumns:
    """`strokes` (u, v in the shape's box, as for lettering columns) replace the worked-out ones."""
    from .autostrokes import letter_strokes

    if not contours:
        return ShapeColumns(index, None, error="no outline")
    a = np.vstack(contours)
    box = (a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max())
    size = max(box[2] - box[0], box[3] - box[1])
    spacing = min(MAX_SPACING_MM, max(MIN_SPACING_MM, SPACING_FRACTION * size))
    try:
        junctions: list = []
        strokes = strokes or letter_strokes(contours, size, overlap_feet=True, junctions_out=junctions)
        cols = [trim_tips(c) for c in col.letter_columns(strokes, contours, box, spacing,
                                                          seam_overlap=SEAM_OVERLAP_MM)]
        lt = col.Letter(str(index), cols, contours, box, strokes)
        check = col.check_letter(lt, 100.0, FLAT_OUTSIDE_PCT)
        # The medial-axis junction reading (type per junction) and the concavities, carried forward
        # for the tidy/element stage so it need not re-derive junctions from the finished polygons.
        if junctions:
            check["junctions"] = [j for pc in junctions for j in pc["junctions"]]
            check["concavities"] = [list(p) for pc in junctions for p in pc["concavities"]]
        # Coverage counts only the part satin can sew: tips and slivers narrower than MIN_TIP_MM are
        # left bare on purpose (trim_tips), so they're taken out of the shape before measuring.
        from shapely.ops import unary_union

        glyph = lt.glyph()
        sewable = glyph.buffer(-MIN_TIP_MM / 2).buffer(MIN_TIP_MM / 2).intersection(glyph)
        if sewable.is_empty or sewable.area <= 0:
            return ShapeColumns(index, None, error=f"narrower than {MIN_TIP_MM} mm everywhere")
        bare = sewable.difference(unary_union([c.shape() for c in cols])).area / sewable.area * 100
        check["sewable_bare_pct"] = round(bare, 2)
        # Flat work: two columns overlapping where they join only adds a little thickness there, so
        # the overlap allowance is per join, not puff's tight one.
        # Where two strokes meet they overlap by about a square of their width (a V's legs at its
        # point), so the allowance grows with the strokes' width: a 3 mm W's legs ~10 mm2, a dancer's
        # 6 mm torso into her arms ~35 mm2.
        joins = max(1, len(cols) - 1)
        widths = [_width(p) for c in cols for p in c.pairs]
        w = float(np.median(widths)) if widths else 0.0
        allowed = max(FLAT_OVERLAP_MM2, FLAT_OVERLAP_PER_WIDTH2 * w * w) * joins
        check["overlap_allowed_mm2"] = round(allowed, 1)
        check["ok"] = (bare <= FLAT_UNCOVERED_PCT and check["outside_pct"] <= FLAT_OUTSIDE_PCT
                       and check["crossed_pairs"] == 0 and check["overlap_mm2"] <= allowed)
    except Exception as e:  # noqa: BLE001 - an unplannable shape keeps click-to-stitch
        return ShapeColumns(index, None, error=f"no column plan ({type(e).__name__}: {str(e)[:80]})")
    return ShapeColumns(index, lt, check)


def flat_check(lt: col.Letter) -> dict:
    """A letter's or shape's columns against flat-work limits (`plan_shape`'s): bare (of the part
    satin can sew), outside, crossed pairs, and overlap per join scaled by the columns' width."""
    from shapely.ops import unary_union

    check = col.check_letter(lt, 100.0, FLAT_OUTSIDE_PCT)
    glyph = lt.glyph()
    sewable = glyph.buffer(-MIN_TIP_MM / 2).buffer(MIN_TIP_MM / 2).intersection(glyph)
    if sewable.is_empty or sewable.area <= 0:
        check.update(ok=False, sewable_bare_pct=100.0)
        return check
    bare = sewable.difference(unary_union([c.shape() for c in lt.columns])).area / sewable.area * 100
    check["sewable_bare_pct"] = round(bare, 2)
    joins = max(1, len(lt.columns) - 1)
    widths = [_width(p) for c in lt.columns for p in c.pairs]
    w = float(np.median(widths)) if widths else 0.0
    allowed = max(FLAT_OVERLAP_MM2, FLAT_OVERLAP_PER_WIDTH2 * w * w) * joins
    check["overlap_allowed_mm2"] = round(allowed, 1)
    check["ok"] = (bare <= FLAT_UNCOVERED_PCT and check["outside_pct"] <= FLAT_OUTSIDE_PCT
                   and check["crossed_pairs"] == 0 and check["overlap_mm2"] <= allowed)
    return check


def text_columns(font: str, text: str, height_mm: float, letter_spacing_pct: float | None = None,
                 font_file=None):
    """A line of lettering as satin columns for flat work: the font's built-in strokes where it has
    them, the rest worked out from each letter's outline, each letter checked with `flat_check`.
    Returns (letters centred on (0, 0), checks per letter, characters worked out)."""
    from .autostrokes import letter_strokes

    font_file = font_file or col.find_font_file(font)
    strokes = dict(col.builtin_strokes(font, font_file))
    auto = []
    for ch in sorted(set(text)):
        if ch.isspace() or ch in strokes:
            continue
        contours, _, _ = col.glyph_outline(font_file, ch, height_mm)
        if contours:
            strokes[ch] = letter_strokes(contours, height_mm, overlap_feet=True)
            auto.append(ch)
    spacing = min(col.DEFAULT_SPACING_MM, max(MIN_SPACING_MM, 0.075 * height_mm))
    letters = col.word_columns(font_file, text, height_mm, letter_spacing_pct, strokes, spacing, flat=True)
    for lt in letters:
        lt.columns = [trim_tips(c) for c in lt.columns]
    return letters, {lt.char: flat_check(lt) for lt in letters}, auto


def plan_groups(svg_path: str | Path, plan, width_mm: float, kinds: tuple[str, ...] = ("satin", "split_satin"),
                strokes: dict[int, list[col.Stroke]] | None = None) -> dict[int, GroupColumns]:
    """Column plans for the plan's groups of these stitch kinds, by group id. `strokes`: given strokes
    per shape index, for shapes whose worked-out strokes fail (a W's four diagonals)."""
    contours, _, _ = shape_contours_mm(svg_path, width_mm)
    strokes = strokes or {}
    out = {}
    for g in plan.groups:
        if g.stitch in kinds or any(i in strokes for i in g.shapes):
            out[g.id] = GroupColumns(g.id, [plan_shape(i, contours[i], strokes.get(i)) for i in g.shapes])
    return out


def screen_pairs(c: col.Column, box: tuple[int, int, int, int], width_mm: float, height_mm: float):
    """A column's pairs on screen, through the art's screen box (l, t, r, b), as the click points are."""
    l, t, r, b = box

    def px(p):
        return int(round(l + p[0] / width_mm * (r - l))), int(round(b - p[1] / height_mm * (b - t)))

    return [(px(a), px(z)) for a, z in c.pairs]


def preview(svg_path: str | Path, plans: dict[int, GroupColumns], width_mm: float, out_png: str | Path) -> Path:
    """The art, with each column-planned shape drawn as its columns; failed plans outlined red."""
    from PIL import Image, ImageDraw

    contours, w, h = shape_contours_mm(svg_path, width_mm)
    S = 1600 / max(w, h)
    im = Image.new("RGB", (int(w * S) + 40, int(h * S) + 40), (30, 30, 30))
    d = ImageDraw.Draw(im, "RGBA")

    def px(p):
        return (20 + p[0] * S, 20 + (h - p[1]) * S)

    for cs in contours:
        for c in cs:
            d.polygon([px(p) for p in c], fill=(80, 80, 80, 255))
    colours = [(230, 90, 90), (90, 160, 230), (110, 200, 120), (220, 170, 60), (180, 110, 220), (90, 210, 200)]
    for gc in plans.values():
        for sc in gc.shapes:
            if sc.letter is None or not gc.ok:
                for c in contours[sc.shape]:
                    d.line([px(p) for p in np.vstack([c, c[:1]])], fill=(255, 60, 60), width=3)
                continue
            for ci, c in enumerate(sc.letter.columns):
                cc = colours[ci % len(colours)]
                for q in c.quads():
                    for poly in getattr(q, "geoms", [q]):
                        if not poly.is_empty:
                            d.polygon([px(p) for p in poly.exterior.coords], fill=cc + (150,))
                for a, z in c.pairs:
                    d.line([px(a), px(z)], fill=cc + (255,), width=1)
    im.save(out_png)
    return Path(out_png)
