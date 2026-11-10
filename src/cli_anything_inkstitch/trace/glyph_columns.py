"""TTF/OTF text → satin columns planned stroke by stroke (no rung review).

Why this exists: `tools suggest-rungs` + `tools fill-to-satin` breaks at
junctions. A rung near where two strokes meet crosses both, so the engine
pairs the wrong edges: the 2026 head-to-head (2026-11-08) got a bare wedge
in the 6, and EST. 2018 lost parts of the E, T, 1 and 8. EmbroideryStudio
won lettering on exactly that.

The fix is to plan a letter the way a digitizer does before any rail
exists. stitchpilot's column planner (bobbymarko/stitchpilot, Apache-2.0,
`autostrokes.letter_strokes` + `columns.letter_columns`) does this:
  1. the letter's medial axis (a Voronoi diagram of its outline), cut into
     branches at junctions;
  2. branches rejoined into strokes: the straightest pair through a
     junction is one stroke, the others butt against its edge; sharp
     corners split; free ends run on to the outline;
  3. each stroke's two edges found by casting rays from its centreline to
     the outline, with rays that run into a neighbouring stroke
     interpolated across. The result is (left, right) point pairs: rails
     and rungs together, paired by construction;
  4. a check of every letter against its outline: bare area, area outside
     the outline, overlap between columns, crossed (twisted) pairs.
That planner drove the rig's EmbroideryStudio clicks. Here its pairs
become Ink/Stitch satin columns directly.

Engine contract (readers cited):
* Satin layout. lib/elements/satin_column.py `SatinColumn.rungs`: a path
  with exactly two subpaths is an "old-style" satin, and
  `_synthesize_rungs` pairs the rails' nodes in order when both rails have
  the same number of nodes. So the planner's pairs are written as two
  rails with one node per pair and no rung subpaths. The pairing is
  exactly the planner's, and there is no rung for the engine to mis-match
  at a junction.
* Element dispatch. lib/elements/utils/nodes.py `node_to_elements`: a
  stroke paint plus inkstitch:satin_column="True" makes a SatinColumn.
  The caller sets that through the mined schema (params path).

stitchpilot is an optional, undeclared dependency for now (it isn't on
PyPI; declaring it as a git extra in pyproject.toml is a packaging decision
left to the maintainer). Without it, `require_planner` raises a UserError
with the install line.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path

from cli_anything_inkstitch.errors import UserError

PX_PER_MM = 96.0 / 25.4

# Satin wider than this is split into shorter stitches by the engine's
# max_stitch_length_mm. A long satin stitch snags, and a 7-8 mm limit is
# the usual digitizing rule. Columns are planned at full width either way.
SPLIT_SATIN_MM = 7.0


@dataclass
class PlannedColumn:
    left: list[tuple[float, float]]          # mm, y up
    right: list[tuple[float, float]]

    def widths(self) -> list[float]:
        return [math.dist(a, b) for a, b in zip(self.left, self.right)]


@dataclass
class PlannedLetter:
    char: str
    columns: list[PlannedColumn]
    contours: list[list[tuple[float, float]]]  # the letter's outline, mm, y up
    ok: bool
    check: dict = field(default_factory=dict)
    error: str = ""


def require_planner():
    try:
        from stitchpilot import art_columns, columns  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised by the skip path
        raise UserError(
            "satin lettering needs the column planner (stitchpilot) and its "
            "geometry libraries: pip install numpy scipy shapely fonttools "
            "svgelements 'stitchpilot @ git+https://github.com/bobbymarko/stitchpilot' "
            f"({type(exc).__name__}: {exc})") from exc
    from stitchpilot import art_columns, columns
    return art_columns, columns


def static_font(font_path: str | Path, wght: float | None,
                cache_dir: Path | None = None) -> Path:
    """A variable font pinned to one weight (a static TTF in a cache dir).
    Static fonts come back unchanged; so does any font when `wght` is None."""
    font_path = Path(font_path)
    if wght is None:
        return font_path
    from fontTools.ttLib import TTFont
    f = TTFont(str(font_path))
    if "fvar" not in f:
        return font_path
    from fontTools.varLib.instancer import instantiateVariableFont
    cache_dir = cache_dir or Path.home() / ".cache" / "cli-anything-inkstitch" / "fonts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(font_path.read_bytes()).hexdigest()[:10]
    out = cache_dir / f"{font_path.stem}-{digest}-wght{wght:g}.ttf"
    if not out.exists():
        inst = instantiateVariableFont(f, {"wght": wght})
        tmp = out.with_suffix(".tmp")
        inst.save(str(tmp))
        tmp.replace(out)
    return out


def with_cap_height(font_path: str | Path, cache_dir: Path | None = None) -> Path:
    """The font, or a cached copy with OS/2 sCapHeight filled in from the
    H's height when the font leaves it at 0 (DejaVu does). The planner
    scales to the capital height and refuses fonts that don't state it."""
    font_path = Path(font_path)
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.ttLib import TTFont
    f = TTFont(str(font_path))
    os2 = f["OS/2"] if "OS/2" in f else None
    if os2 is None or getattr(os2, "sCapHeight", 0):
        return font_path
    cmap = f.getBestCmap()
    gs = f.getGlyphSet()
    if ord("H") not in cmap:
        return font_path
    bp = BoundsPen(gs)
    gs[cmap[ord("H")]].draw(bp)
    if not bp.bounds:
        return font_path
    cache_dir = cache_dir or Path.home() / ".cache" / "cli-anything-inkstitch" / "fonts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(font_path.read_bytes()).hexdigest()[:10]
    out = cache_dir / f"{font_path.stem}-{digest}-cap.ttf"
    if not out.exists():
        if os2.version < 2:
            os2.version = 2
            for k in ("ulCodePageRange1", "ulCodePageRange2", "sxHeight",
                      "usDefaultChar", "usBreakChar", "usMaxContext"):
                if not hasattr(os2, k):
                    setattr(os2, k, 0)
        os2.sCapHeight = int(round(bp.bounds[3]))
        tmp = out.with_suffix(".tmp")
        f.save(str(tmp))
        tmp.replace(out)
    return out


def _from_letter(lt, ok: bool, check: dict, char: str | None = None) -> PlannedLetter:
    cols = [PlannedColumn([tuple(map(float, a)) for a, _ in c.pairs],
                          [tuple(map(float, b)) for _, b in c.pairs])
            for c in lt.columns if len(c.pairs) >= 2]
    return PlannedLetter(char if char is not None else lt.char, cols,
                         [[tuple(map(float, p)) for p in c] for c in lt.outline],
                         ok, check)


def plan_text(font_path: str | Path, text: str, height_mm: float, *,
              letter_spacing_pct: float | None = None,
              width_mm: float | None = None,
              merge_joins: bool = False) -> list[PlannedLetter]:
    """Plan `text` (one line) as satin columns, centred on (0, 0), mm, y up.

    height_mm is the font's capital height (the sCapHeight in its OS/2
    table), which is what EmbroideryStudio's lettering Height means.
    merge_joins (script fonts): letters whose outlines touch or overlap,
    such as Lobster's joins, are unioned into one shape and planned as one
    piece, so the join is one stroke and not two columns stacked.
    width_mm (overrides letter_spacing_pct): spacing that makes the ink
    this wide.
    """
    ac, col = require_planner()
    font_path = with_cap_height(font_path)
    if not text.strip():
        raise UserError("nothing to stitch: the text is empty")
    if width_mm is not None:
        # spacing that makes the ink box this wide: plan once with none,
        # then spread the difference over the gaps (stitchpilot's
        # word_columns does the same for EmbroideryStudio's fit-to-width)
        gaps = len(text) - 1
        if gaps < 1:
            raise UserError("--width-mm needs at least two characters; "
                            "a single letter's width comes from its height")
        base = plan_text(font_path, text, height_mm, letter_spacing_pct=0.0)
        xs = [p[0] for lt in base for c in lt.contours for p in c]
        letter_spacing_pct = (width_mm - (max(xs) - min(xs))) / gaps / height_mm * 100
    # Layout as stitchpilot's word_columns (advance widths plus spacing, the
    # ink box centred on 0, 0), but each letter planned on its own: a planner
    # exception in one glyph (measured: a GEOS "endpoints are equal" error on
    # one Lato Regular lowercase letter at 10 mm) fails that letter to the
    # fallback instead of the whole line. Steps per letter mirror
    # art_columns.text_columns: letter_strokes → tuck_order → letter_columns
    # (flat, tucked) → trim_tips → flat_check.
    from stitchpilot.autostrokes import letter_strokes
    import numpy as np

    spacing = min(col.DEFAULT_SPACING_MM, max(ac.MIN_SPACING_MM, 0.075 * height_mm))
    gap = (letter_spacing_pct or 0.0) / 100 * height_mm
    raw, x = [], 0.0
    for ch in text:
        try:
            contours, adv, _ = col.glyph_outline(Path(font_path), ch, height_mm)
        except ValueError as exc:
            raise UserError(str(exc)) from exc
        if ch.isspace() or not contours:
            x += adv + gap
            continue
        moved = [c + [x, 0.0] for c in contours]
        allp = np.vstack(moved)
        box = (allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max())
        raw.append((ch, moved, box))
        x += adv + gap
    if not raw:
        raise UserError("nothing to stitch")
    x0 = min(b[0] for _, _, b in raw)
    x1 = max(b[2] for _, _, b in raw)
    y0 = min(b[1] for _, _, b in raw)
    y1 = max(b[3] for _, _, b in raw)
    dx, dy = -(x0 + x1) / 2, -(y0 + y1) / 2
    shells = []
    for ch, moved, box in raw:
        moved = [c + [dx, dy] for c in moved]
        box = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
        shells.append(col.Letter(ch, [], moved, box))
    if merge_joins:
        return _plan_merged(shells, ac)
    out = []
    for sh in shells:
        try:
            strokes = letter_strokes(sh.outline, height_mm, overlap_feet=True)
            strokes = col.tuck_order(strokes, sh.outline, sh.box)
            cols = col.letter_columns(strokes, sh.outline, sh.box, spacing,
                                      col.RING_OVERLAP_MM, col.BUTT_OVERLAP_MM, tuck=True)
            lt = col.Letter(sh.char, [ac.trim_tips(c) for c in cols], sh.outline, sh.box,
                            strokes, butt_overlap=col.BUTT_OVERLAP_MM)
            check = ac.flat_check(lt)
        except Exception as exc:  # noqa: BLE001 - one unplannable glyph falls back alone
            out.append(PlannedLetter(sh.char, [], [[tuple(map(float, p)) for p in c] for c in sh.outline],
                                     False, {}, f"no column plan ({type(exc).__name__}: {str(exc)[:80]})"))
            continue
        out.append(_from_letter(lt, bool(check["ok"]), check))
    return out


def _plan_merged(letters, ac) -> list[PlannedLetter]:
    """Union the letters' glyphs; plan each connected piece as one shape."""
    import numpy as np
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    glyph = unary_union([lt.glyph() for lt in letters])
    pieces = sorted(getattr(glyph, "geoms", [glyph]), key=lambda p: p.bounds[0])
    out = []
    for i, poly in enumerate(pieces):
        if not isinstance(poly, Polygon) or poly.is_empty:
            continue
        contours = [np.array(poly.exterior.coords[:-1], float)]
        contours += [np.array(r.coords[:-1], float) for r in poly.interiors]
        chars = "".join(lt.char for lt in letters
                        if lt.glyph().intersects(poly))
        sc = ac.plan_shape(i, contours)
        if sc.letter is None:
            out.append(PlannedLetter(chars, [], [[tuple(p) for p in c] for c in contours],
                                     False, {}, sc.error))
            continue
        out.append(_from_letter(sc.letter, sc.ok, sc.check, chars))
    return out


def plan_shapes(shapes: list[tuple[str, list]]) -> list[PlannedLetter]:
    """Plan filled artwork shapes (a logo's knots, crown, heart, letters drawn as
    outlines) as satin columns, one shape at a time: stitchpilot's
    `art_columns.plan_shape`, the same planner the rig used for the HIDA chest
    logo's all-satin v5-v9 in EmbroideryStudio. Each item is (label, contours),
    contours in mm, y up, outer ring first then holes. A shape the planner
    can't do, or whose check fails, comes back ok=False for the caller's
    fallback."""
    import numpy as np

    ac, _ = require_planner()
    out = []
    for i, (label, contours) in enumerate(shapes):
        arrs = [np.asarray(c, float) for c in contours if len(c) >= 3]
        try:
            sc = ac.plan_shape(i, arrs)
        except Exception as exc:  # noqa: BLE001 - one unplannable shape falls back alone
            out.append(PlannedLetter(label, [], [[tuple(map(float, q)) for q in c] for c in arrs],
                                     False, {}, f"no column plan ({type(exc).__name__}: {str(exc)[:80]})"))
            continue
        if sc.letter is None:
            out.append(PlannedLetter(label, [], [[tuple(map(float, q)) for q in c] for c in arrs],
                                     False, {}, sc.error or "no column plan"))
            continue
        out.append(_from_letter(sc.letter, bool(sc.ok), sc.check or {}, label))
    return out


# ---------------------------------------------------------------- SVG output

def make_transform(uu_per_mm: float, cx: float, cy: float, rotate_deg: float = 0.0):
    """mm (y up, centred on 0, 0) → document user units (y down) at (cx, cy).
    rotate_deg is counter-clockwise as seen (a slight upward tilt is +5)."""
    a = math.radians(rotate_deg)
    ca, sa = math.cos(a), math.sin(a)

    def tx(p):
        x, y = p
        xr, yr = x * ca - y * sa, x * sa + y * ca
        return (cx + xr * uu_per_mm, cy - yr * uu_per_mm)
    return tx


def _fmt(pts) -> str:
    return " L ".join(f"{x:.3f},{y:.3f}" for x, y in pts)


def satin_d(column: PlannedColumn, tx) -> str:
    """Two rails, one node per planned pair, no rung subpaths. The engine
    pairs the nodes in order (lib/elements/satin_column.py
    `_synthesize_rungs`, which needs equal node counts)."""
    left = [tx(p) for p in column.left]
    right = [tx(p) for p in column.right]
    assert len(left) == len(right) >= 2
    return f"M {_fmt(left)} M {_fmt(right)}"


def fill_ds(contours, tx) -> list[str]:
    """The letter's outline as fill paths, one per separate piece (an i's
    dot is its own path). The engine's FillStitch.original_shape
    (lib/elements/fill_stitch.py) treats a path's biggest subpath as the
    outer edge and every other subpath as a hole. A dot written in the
    same path as its stem would be cut out as a hole, so each piece gets
    its own path."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    polys = [Polygon(c) for c in contours if len(c) >= 3]
    polys = [p if p.is_valid else p.buffer(0) for p in polys]
    polys.sort(key=lambda p: -p.area)
    shape = None
    for p in polys:                      # outer contours add, counters subtract
        if shape is None:
            shape = p
        elif shape.contains(p.representative_point()):
            shape = shape.difference(p)
        else:
            shape = unary_union([shape, p])
    out = []
    for piece in getattr(shape, "geoms", [shape]) if shape is not None else []:
        if piece.is_empty or piece.geom_type != "Polygon":
            continue
        rings = [list(piece.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in piece.interiors]
        out.append(" ".join(f"M {_fmt([tx(p) for p in r])} Z" for r in rings))
    return out


# ---------------------------------------------------------------- routing

EXPOSED_TRAVEL_MM = 0.5   # travel this long over bare fabric shows as a stray thread


def exposed_travel_mm(letter: PlannedLetter) -> float:
    """How much straight travel between this letter's columns, sewn in
    planned order, would lie on bare fabric (mm).

    The engine starts each satin at the end nearest where the last one
    stopped (satin param start_at_nearest_point, on by default) and sews
    it to the other end, so the walk is simulated: each column starts at
    its end nearer the current position and leaves the needle at its far
    end. The part of each straight travel outside the letter is what
    shows. An E's top arm to
    its middle arm crosses the counter (exposed); a 2's diagonal into its
    base stays inside the letter (not exposed). Callers route only the
    letters where this is above EXPOSED_TRAVEL_MM, because the engine's
    auto_satin cuts columns at every crossing, and on a 2 whose diagonal
    tucks into the base it split the base and added three trims (measured
    on Lato Black 16.4 mm, 2026-11-08).
    """
    if len(letter.columns) < 2:
        return 0.0
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union

    polys = [Polygon(c) for c in letter.contours if len(c) >= 3]
    polys = [p if p.is_valid else p.buffer(0) for p in polys]
    polys.sort(key=lambda p: -p.area)
    glyph = None
    for p in polys:
        if glyph is None:
            glyph = p
        elif glyph.contains(p.representative_point()):
            glyph = glyph.difference(p)
        else:
            glyph = unary_union([glyph, p])
    if glyph is None:
        return 0.0
    glyph = glyph.buffer(0.05)

    def ends(c: PlannedColumn):
        mid = lambda i: ((c.left[i][0] + c.right[i][0]) / 2, (c.left[i][1] + c.right[i][1]) / 2)
        return [mid(0), mid(-1)]

    total = 0.0
    cur = ends(letter.columns[0])[1]
    for c in letter.columns[1:]:
        e0, e1 = ends(c)
        start, far = (e0, e1) if math.dist(cur, e0) <= math.dist(cur, e1) else (e1, e0)
        if math.dist(cur, start) > 1e-6:
            total += LineString([cur, start]).difference(glyph).length
        cur = far
    return total
