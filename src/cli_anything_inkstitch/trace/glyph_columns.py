"""TTF/OTF text → satin columns planned stroke by stroke (no rung review).

Why this exists: `tools suggest-rungs` + `tools fill-to-satin` breaks at
junctions. A rung near where two strokes meet crosses both, so the engine
pairs the wrong edges: the 2026 head-to-head (2026-11-08) got a bare wedge
in the 6, and EST. 2018 lost parts of the E, T, 1 and 8. EmbroideryStudio
won lettering on exactly that.

The fix is to plan a letter the way a digitizer does before any rail
exists. The column planner (cli_anything_inkstitch.planner:
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

The planner needs numpy, scipy, shapely, fonttools and svgelements, which
the core install doesn't: they come with the `satin` extra. Without them,
`require_planner` raises a UserError with the install line.
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
    closed: bool = False                     # a ring (an O): starts and ends at its seam

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
        import numpy  # noqa: F401
        import scipy.spatial  # noqa: F401
        import shapely  # noqa: F401
        import fontTools  # noqa: F401
        import svgelements  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised by the skip path
        raise UserError(
            "satin planning needs the satin extra's geometry libraries: "
            "pip install 'cli-anything-inkstitch[satin]' "
            f"({type(exc).__name__}: {exc})") from exc
    from cli_anything_inkstitch.planner import art_columns, columns
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
                          [tuple(map(float, b)) for _, b in c.pairs], bool(c.closed))
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
        # then spread the difference over the gaps (planner columns.
        # word_columns does the same for EmbroideryStudio's fit-to-width)
        gaps = len(text) - 1
        if gaps < 1:
            raise UserError("--width-mm needs at least two characters; "
                            "a single letter's width comes from its height")
        base = plan_text(font_path, text, height_mm, letter_spacing_pct=0.0)
        xs = [p[0] for lt in base for c in lt.contours for p in c]
        letter_spacing_pct = (width_mm - (max(xs) - min(xs))) / gaps / height_mm * 100
    # Layout as the planner's columns.word_columns (advance widths plus spacing, the
    # ink box centred on 0, 0), but each letter planned on its own: a planner
    # exception in one glyph (measured: a GEOS "endpoints are equal" error on
    # one Lato Regular lowercase letter at 10 mm) fails that letter to the
    # fallback instead of the whole line. Steps per letter mirror
    # art_columns.text_columns: letter_strokes → tuck_order → letter_columns
    # (flat, tucked) → trim_tips → flat_check.
    from cli_anything_inkstitch.planner.autostrokes import letter_strokes
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


def plan_shapes(shapes: list[tuple[str, list]], one_column: bool = True,
                overrides: dict | None = None, spread_turns_on: bool = True,
                tidy_joins_on: bool = True, joint_overlap_mm: float | None = None) -> list[PlannedLetter]:
    """Plan filled artwork shapes (a logo's knots, crown, heart, letters drawn as
    outlines) as satin columns, one shape at a time: the planner's
    `art_columns.plan_shape`, the same planner that drove an all-satin client
    chest logo in EmbroideryStudio. Each item is (label, contours),
    contours in mm, y up, outer ring first then holes. A shape the planner
    can't do, or whose check fails, comes back ok=False for the caller's
    fallback.

    one_column: where the planner cut one stroke into columns that meet end to
    end with no drawn corner there (an S split at its bowls: a hard seam, see
    column_checks), the pieces are joined into one stroke and re-planned, with
    rails that follow the outline. The joined plan is kept only if it passes
    the same check, with fewer hard seams and no more fans.

    overrides: label → {"split": "none" | [[x, y], ...] (cut the stroke there,
    mm y up), "seam": [x, y] (where a ring starts), "rungs": [[[x1, y1], [x2, y2]], ...]
    (hand-placed rungs, mm y up: see place_rungs)}.

    spread_turns_on: tilt the rungs into tight turns so the inner ends spread
    along the inner curve instead of pinching at one point (spread_turns).
    A shape with hand-placed rungs is left as placed.

    tidy_joins_on: where columns meet, trim deep overlaps, close bare wedges at junctions, and lay
    every butting end flat along the edge it meets, joint_overlap_mm under it (default
    joins.KEEP_MM, 0.4), sewn before the column it tucks under (trace/joins.py). A shape's
    override "joint_overlap_mm" sets its own.
    """
    import numpy as np

    ac, _ = require_planner()
    out = []
    for i, (label, contours) in enumerate(shapes):
        arrs = [np.asarray(c, float) for c in contours if len(c) >= 3]
        ov = (overrides or {}).get(label) or {}
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
        planned = _from_letter(sc.letter, bool(sc.ok), sc.check or {}, label)
        split = ov.get("split")
        if split == "none" or (one_column and split is None):
            joined = _join_seamed_strokes(i, arrs, sc, planned, force=split == "none")
            if joined is not None:
                planned = joined
        if isinstance(split, list) and split:
            cut = _split_at(i, arrs, sc, label, split)
            if cut is not None:
                planned = cut
        if ov.get("seam") is not None:
            planned = _move_ring_seam(planned, ov["seam"])
        do_ease = False
        if ov.get("rungs"):
            planned = place_rungs(planned, ov["rungs"])
        elif spread_turns_on:
            planned = spread_letter_turns(planned)
            do_ease = True
        if tidy_joins_on:
            # Run even on a rungs-override shape: tidy_joins works on where the shape's own
            # columns MEET (overlaps/wedges), which is orthogonal to interior rung placement,
            # and place_rungs has already run above. Skipping it left override shapes un-tidied.
            from cli_anything_inkstitch.trace.joins import KEEP_MM, tidy_joins
            keep = ov.get("joint_overlap_mm", joint_overlap_mm)
            planned = tidy_joins(planned, keep_mm=KEEP_MM if keep is None else float(keep))
        if do_ease:
            # After tidy_joins, so the ease works on the final end cuts (a junction end may have been
            # trimmed/snapped) and nothing undoes it. A hand-placed-rungs shape is left as placed.
            planned = ease_letter_ends(planned)
        out.append(planned)
    return out


def seam_report(planned: PlannedLetter) -> dict:
    from cli_anything_inkstitch.trace import column_checks as cc

    cols = [list(zip(c.left, c.right)) for c in planned.columns]
    seams = cc.seams(cols, planned.contours, [c.closed for c in planned.columns])
    return {"hard_seams": sum(1 for x in seams if x["kind"] == "hard_seam"),
            "fans": sum(len(cc.fans(c)) for c in cols), "turns": sum(len(cc.turns(c)) for c in cols),
            "seams": seams}


def _letter_from_pairs(ac, i, arrs, box, strokes, pair_lists, closed):
    from cli_anything_inkstitch.planner import columns as col

    cols = [col.Column(pairs=[(tuple(l), tuple(r)) for l, r in pl], curve=[False] * len(pl), closed=c)
            for pl, c in zip(pair_lists, closed)]
    return col.Letter(str(i), cols, arrs, box, strokes)


def _chain(strokes, groups_of):
    """Strokes joined end to end, in order, oriented to run on. groups_of: list of index lists."""
    import numpy as np
    from cli_anything_inkstitch.planner import columns as col

    out = []
    for grp in groups_of:
        if len(grp) == 1:
            out.append(strokes[grp[0]])
            continue
        # order: start from an index whose stroke has only one joined neighbour in the group
        pts = {k: np.asarray(strokes[k].path, float) for k in grp}
        rest = list(grp)
        def ends(k):
            return pts[k][0], pts[k][-1]
        # the chain end: the stroke whose nearest other end is farthest away
        def nearest(k, e):
            return min(np.hypot(*(e - x)) for q in rest if q != k for x in ends(q))
        first = max(rest, key=lambda k: max(nearest(k, ends(k)[0]), nearest(k, ends(k)[1])))
        a0, a1 = ends(first)
        path = list(pts[first]) if nearest(first, a1) <= nearest(first, a0) else list(pts[first][::-1])
        cut0 = strokes[first].start_cut if nearest(first, a1) <= nearest(first, a0) else strokes[first].end_cut
        rest.remove(first)
        cut1 = strokes[first].end_cut
        while rest:
            tail = path[-1]
            k = min(rest, key=lambda q: min(np.hypot(*(tail - x)) for x in ends(q)))
            q0, q1 = ends(k)
            fwd = np.hypot(*(tail - q0)) <= np.hypot(*(tail - q1))
            seg = list(pts[k]) if fwd else list(pts[k][::-1])
            cut1 = strokes[k].end_cut if fwd else strokes[k].start_cut
            # the joint: drop the ends of both strokes that lie within the gap of where they meet,
            # so the joined centreline turns through the joint instead of doubling back
            gap = np.hypot(*(tail - seg[0]))
            mid = (tail + seg[0]) / 2
            while len(path) > 2 and np.hypot(*(path[-1] - mid)) < gap:
                path.pop()
            while len(seg) > 2 and np.hypot(*(seg[0] - mid)) < gap:
                seg.pop(0)
            path += seg
            rest.remove(k)
        out.append(col.Stroke(path=[tuple(map(float, p)) for p in path], smooth=True,
                              start_cut=cut0, end_cut=cut1))
    return out


def _join_seamed_strokes(i, arrs, sc, planned: PlannedLetter, force: bool = False):
    """Re-plan with the strokes behind each hard seam joined (see plan_shapes)."""
    from cli_anything_inkstitch.trace import column_checks as cc

    ac, _ = require_planner()
    lt = sc.letter
    if len(lt.columns) < 2 or len(lt.strokes) != len(lt.columns):
        return None
    cols = [[(tuple(map(float, a)), tuple(map(float, b))) for a, b in c.pairs] for c in lt.columns]
    closed = [bool(c.closed) for c in lt.columns]
    seams = cc.seams(cols, [c.tolist() for c in arrs], closed)
    joins = [x["columns"] for x in seams if x["kind"] == "hard_seam" or (force and x["kind"] == "corner_join")]
    if not joins:
        return None
    parent = list(range(len(cols)))
    def find(k):
        while parent[k] != k:
            k = parent[k]
        return k
    for a, b in joins:
        parent[find(a)] = find(b)
    groups, seen = [], {}
    for k in range(len(cols)):                      # keep sewing order: a group sews where its first stroke did
        r = find(k)
        if r not in seen:
            seen[r] = len(groups)
            groups.append([])
        groups[seen[r]].append(k)
    strokes = _chain(lt.strokes, groups)
    try:
        sc2 = ac.plan_shape(i, arrs, strokes=strokes)
    except Exception:  # noqa: BLE001
        return None
    if sc2.letter is None:
        return None
    joined_idx = [g for g, grp in enumerate(groups) if len(grp) > 1]
    pairs = [[(tuple(map(float, a)), tuple(map(float, b))) for a, b in c.pairs] for c in sc2.letter.columns]
    for g in joined_idx:
        if g < len(pairs):
            ring = outline_column(pairs[g], [c.tolist() for c in arrs]) if len(arrs) == 1 else None
            if ring:
                # outline_column pairs the two outline arcs in proportion between sparse anchors; where
                # the arcs' lengths differ (after an S's bowl) the rungs lean ~30 deg on the straight
                # spine. Re-pair square to the middle; spread_turns then leans only the tight turns.
                med = medial_rungs(ring)
                if med:
                    spread, _ = spread_turns(med)
                    if _crossed(spread) <= _crossed(med):
                        med = spread
                    if _crossed(med) <= _crossed(ring):
                        ring = med
            pairs[g] = ring or follow_outline(pairs[g], [c.tolist() for c in arrs])
    lt2 = _letter_from_pairs(ac, i, arrs, sc2.letter.box, strokes, pairs, [bool(c.closed) for c in sc2.letter.columns])
    check = ac.flat_check(lt2)
    new = _from_letter(lt2, bool(check["ok"]), check, planned.char)
    before, after = seam_report(planned), seam_report(new)
    if not force and not (check["ok"] and check["crossed_pairs"] == 0
                          and after["hard_seams"] < before["hard_seams"] and after["fans"] <= before["fans"]):
        return None
    new.check["joined_strokes"] = [list(grp) for grp in groups if len(grp) > 1]
    return new


def follow_outline(pairs, contours, on_mm: float = 0.15, sag_mm: float = 0.1, step_mm: float = 1.0):
    """A column's rails made to follow the outline between its pairs.

    The engine sews straight between a column's rail nodes, so on a curve a rail drawn
    pair to pair cuts the corner (a chord), leaving the outline's bulge bare. Where both of
    a rail's consecutive points lie on the same outline ring (within on_mm), and the outline
    between them sags more than sag_mm from the chord, pairs are added every ~step_mm along it.
    Both rails get the same number of new pairs, spaced in proportion along each, which is how the
    engine spreads stitches between two pairs anyway."""
    from shapely.geometry import LinearRing, LineString, Point

    rings = [LinearRing(c) for c in contours if len(c) >= 3]

    def arc(a, b):
        pa, pb = Point(a), Point(b)
        for r in rings:
            if r.distance(pa) <= on_mm and r.distance(pb) <= on_mm:
                L = r.length
                sa, sb = r.project(pa), r.project(pb)
                f, bk = (sb - sa) % L, (sa - sb) % L
                if min(f, bk) > 4 * max(Point(a).distance(pb), 1e-6) + 1.0:
                    return None                       # not the short way round: a different stretch
                return (r, sa, f) if f <= bk else (r, sa, -bk)
        return None

    def sample(spec, a, b, t):
        if spec is None:
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        r, s0, ln = spec
        q = r.interpolate((s0 + ln * t) % r.length)
        return (q.x, q.y)

    def sags(spec, a, b):
        if spec is None:
            return 0.0, 0.0
        r, s0, ln = spec
        chord = LineString([a, b])
        n = max(2, int(abs(ln) / 0.2))
        dev = max(chord.distance(Point(*sample(spec, a, b, k / n))) for k in range(n + 1))
        return dev, abs(ln)

    out = [pairs[0]]
    for (l0, r0), (l1, r1) in zip(pairs, pairs[1:]):
        sl, sr = arc(l0, l1), arc(r0, r1)
        dl, ll = sags(sl, l0, l1)
        dr, lr = sags(sr, r0, r1)
        m = 0
        if max(dl, dr) > sag_mm:
            m = max(1, int(max(ll, lr) / step_mm))
        for k in range(1, m):
            t = k / m
            out.append((sample(sl if dl > sag_mm / 4 else None, l0, l1, t),
                        sample(sr if dr > sag_mm / 4 else None, r0, r1, t)))
        out.append((l1, r1))
    return out


def outline_column(pairs, contours, on_mm: float = 0.15, step_mm: float = 1.0):
    """A column whose rails ARE the outline: for a shape with one contour sewn as one column
    (an S), the outline splits at the column's two end cuts into its two rails. The planned pairs
    whose both points lie on the outline stay as anchors (where the stitch angle is set); between
    anchors, pairs follow both outline arcs in proportion every ~step_mm. None if the end pairs
    aren't on the outline or the arcs don't run one each way."""
    from shapely.geometry import LinearRing, Point

    ring = LinearRing(contours[0])
    L = ring.length
    on = lambda p: ring.distance(Point(p)) <= on_mm
    if len(pairs) < 2 or not (on(pairs[0][0]) and on(pairs[0][1]) and on(pairs[-1][0]) and on(pairs[-1][1])):
        return None
    pos = lambda p: ring.project(Point(p))
    l0, r0, l1, r1 = pos(pairs[0][0]), pos(pairs[0][1]), pos(pairs[-1][0]), pos(pairs[-1][1])
    # the left rail runs from l0 to l1 one way round, the right from r0 to r1 the other way, and
    # neither passes the other's ends
    def span(a, b, d):
        return (b - a) % L if d > 0 else (a - b) % L
    def inside(x, a, ln, d):
        return 0 < span(a, x, d) < ln
    best = None
    for d in (1, -1):
        ll, lr = span(l0, l1, d), span(r0, r1, -d)
        if not (inside(r0, l0, ll, d) or inside(r1, l0, ll, d) or inside(l0, r0, lr, -d) or inside(l1, r0, lr, -d)):
            best = (d, ll, lr)
    if best is None:
        return None
    d, ll, lr = best
    if ll <= 0 or lr <= 0:
        return None
    anchors = [(0.0, 0.0)]
    for l, r in pairs[1:-1]:
        if on(l) and on(r):
            tl, tr = span(l0, pos(l), d) / ll, span(r0, pos(r), -d) / lr
            if anchors[-1][0] < tl < 1 and anchors[-1][1] < tr < 1:
                anchors.append((tl, tr))
    anchors.append((1.0, 1.0))
    at = lambda s0, ln, dd, t: ring.interpolate((s0 + dd * ln * t) % L)
    out = []
    for (a0, b0), (a1, b1) in zip(anchors, anchors[1:]):
        m = max(1, int(round(max((a1 - a0) * ll, (b1 - b0) * lr) / step_mm)))
        for k in range(m):
            t = k / m
            pl = at(l0, ll, d, a0 + (a1 - a0) * t)
            pr = at(r0, lr, -d, b0 + (b1 - b0) * t)
            out.append(((pl.x, pl.y), (pr.x, pr.y)))
    out.append((tuple(pairs[-1][0]), tuple(pairs[-1][1])))
    out[0] = (tuple(pairs[0][0]), tuple(pairs[0][1]))
    return out


def medial_rungs(pairs, step_mm: float = 0.8, smooth_mm: float = 1.5):
    """Re-pair a column so every rung is square to its middle line: the middle is traced from the
    left rail to the nearest point of the right, smoothed over smooth_mm and resampled every step_mm;
    at each sample the line along the normal is cut by both rails. Samples whose hits don't move on
    along both rails (the inside of a tight turn) are dropped, so the turn keeps sparse pairs for
    spread_turns to tilt. End pairs are kept as planned. None if fewer than 3 pairs come out."""
    import numpy as np
    from shapely.geometry import LineString, Point

    if len(pairs) < 3:
        return None
    left = [tuple(map(float, p[0])) for p in pairs]
    right = [tuple(map(float, p[1])) for p in pairs]
    Lr, Rr = LineString(left), LineString(right)
    if Lr.length < 2 * step_mm or Rr.length < 2 * step_mm:
        return None
    mids = []
    for t in np.arange(0, Lr.length + 1e-9, 0.25):
        pl = Lr.interpolate(t)
        pr = Rr.interpolate(Rr.project(pl))
        mids.append(((pl.x + pr.x) / 2, (pl.y + pr.y) / 2))
    mids = np.asarray(mids)
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(mids, axis=0).T))])
    keep = np.concatenate([[True], np.diff(s) > 1e-6])
    mids, s = mids[keep], s[keep]
    if len(mids) < 3:
        return None
    sm = np.empty_like(mids)
    for i in range(len(mids)):
        w = np.abs(s - s[i]) <= smooth_mm
        sm[i] = mids[w].mean(axis=0)
    ss = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(sm, axis=0).T))])
    if ss[-1] < 2 * step_mm:
        return None
    t = np.arange(step_mm, ss[-1] - step_mm / 2, step_mm)
    mx, my = np.interp(t, ss, sm[:, 0]), np.interp(t, ss, sm[:, 1])
    width = max(float(np.hypot(*np.subtract(a, b))) for a, b in zip(left, right))
    out_pos = [(0.0, 0.0)]
    for i in range(len(t)):
        a = max(0, i - 1)
        b = min(len(t) - 1, i + 1)
        tg = np.array([mx[b] - mx[a], my[b] - my[a]])
        n = np.hypot(*tg)
        if n == 0:
            continue
        nr = np.array([-tg[1], tg[0]]) / n
        m = np.array([mx[i], my[i]])
        line = LineString([tuple(m - nr * 2 * width), tuple(m + nr * 2 * width)])
        hits = []
        for rail in (Lr, Rr):
            x = line.intersection(rail)
            pts = [g for g in getattr(x, "geoms", [x]) if g.geom_type == "Point" and not g.is_empty]
            if not pts:
                break
            hits.append(rail.project(min(pts, key=lambda g: g.distance(Point(m)))))
        if len(hits) != 2:
            continue
        sl, sr = hits
        if sl > out_pos[-1][0] + 0.05 and sr > out_pos[-1][1] + 0.05 and sl < Lr.length - 0.05 and sr < Rr.length - 0.05:
            out_pos.append((sl, sr))
    if len(out_pos) < 3:
        return None
    out_pos.append((Lr.length, Rr.length))
    # Where samples were dropped (inside a tight turn) one rail has a long gap: the engine would sew
    # its chord and cut the outline's corner. Fill it with pairs in proportion, as between anchors.
    dense = [out_pos[0]]
    for (a0, b0), (a1, b1) in zip(out_pos, out_pos[1:]):
        m = int(np.ceil(max(a1 - a0, b1 - b0) / step_mm))
        for k in range(1, m):
            dense.append((a0 + (a1 - a0) * k / m, b0 + (b1 - b0) * k / m))
        dense.append((a1, b1))
    out = [(left[0], right[0])]
    for sl, sr in dense[1:-1]:
        pl, pr = Lr.interpolate(sl), Rr.interpolate(sr)
        out.append(((pl.x, pl.y), (pr.x, pr.y)))
    out.append((left[-1], right[-1]))
    return out


# ---------------------------------------------------------------- tight turns

# Where a column turns tightly, its inner rail is much shorter than its outer. The engine spreads a
# stretch's stitches in proportion along each rail between two consecutive pairs, so if the planner
# set a run of pairs whose inner ends sit within a millimetre while the outer ends sweep a whole
# bowl, every stitch in that run starts from nearly one point: a pinch, thread piled on the inside
# and gaps outside. A digitizer instead tilts the rungs into the turn. The inner ends start the turn
# earlier and finish it later, spread evenly along the inner curve, and short stitches thin the
# inside. spread_turns does the tilting.
TURN_RATIO = 0.25        # a span whose shorter rail moves less than this share of the longer one
TURN_MIN_MM = 0.2        # ...ignored if the longer rail moves less than this
TURN_MAX_TILT_DEG = 55.0  # a rung may lean this far from square to the column, no further


def _cum(points):
    out = [0.0]
    for a, b in zip(points, points[1:]):
        out.append(out[-1] + math.dist(a, b))
    return out


def _span_ratios(sL, sR):
    """Per span: (shorter rail's move / longer rail's move, longer move)."""
    out = []
    for k in range(len(sL) - 1):
        a, b = sL[k + 1] - sL[k], sR[k + 1] - sR[k]
        lo, hi = min(a, b), max(a, b)
        out.append((lo / hi if hi > 1e-9 else 1.0, hi))
    return out


def _max_tilt_deg(pairs) -> float:
    """How far the column's rungs lean from square to its centreline, worst case."""
    mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in pairs]
    worst = 0.0
    for k in range(1, len(pairs) - 1):
        tx, ty = mids[k + 1][0] - mids[k - 1][0], mids[k + 1][1] - mids[k - 1][1]
        rx, ry = pairs[k][1][0] - pairs[k][0][0], pairs[k][1][1] - pairs[k][0][1]
        tn, rn = math.hypot(tx, ty), math.hypot(rx, ry)
        if tn < 1e-9 or rn < 1e-9:
            continue
        # a square rung is perpendicular to the centreline: |cos| between them is 0
        c = abs(tx * rx + ty * ry) / (tn * rn)
        worst = max(worst, math.degrees(math.asin(min(1.0, c))))
    return worst


def spread_turns(pairs, ratio: float = TURN_RATIO, max_tilt_deg: float = TURN_MAX_TILT_DEG):
    """A column's pairs with the inner ends of tight turns spread along the inner rail.

    Each run of spans where one rail barely moves (shorter/longer below `ratio`) is widened a pair at
    a time, and inside it both rails' pair positions are made even (the converged result of smoothing
    them). The run grows until every span in it reaches `ratio` or a rung would lean more than
    max_tilt_deg; the last widening that kept the tilt is used. Rails are the column's own (the
    polylines through its pairs, which follow the outline), so the outline is unchanged; the end
    pairs (the column's cuts) never move. Returns (pairs, changed)."""
    from shapely.geometry import LineString

    n = len(pairs)
    if n < 5:
        return pairs, False
    Lp, Rp = [tuple(p[0]) for p in pairs], [tuple(p[1]) for p in pairs]
    sL, sR = _cum(Lp), _cum(Rp)
    if sL[-1] < 1e-6 or sR[-1] < 1e-6:
        return pairs, False
    rL, rR = LineString(Lp), LineString(Rp)
    bad = [k for k, (r, hi) in enumerate(_span_ratios(sL, sR)) if r < ratio and hi >= TURN_MIN_MM]
    if not bad:
        return pairs, False
    # group the bad spans into runs (spans k..j means pairs k..j+1)
    runs = []
    for k in bad:
        if runs and k <= runs[-1][1] + 2:
            runs[-1][1] = k
        else:
            runs.append([k, k])
    base_tilt = _max_tilt_deg(pairs)
    tilt_cap = max(max_tilt_deg, base_tilt)   # never refuse a run because the plan already leaned
    nL, nR = list(sL), list(sR)
    changed = False
    for a, b in runs:
        best = None
        lo, hi = a, b + 1                      # pair indices whose neighbours anchor the run
        while True:
            lo, hi = max(1, lo - 1), min(n - 2, hi + 1)
            tL, tR = list(nL), list(nR)
            for s, t in ((sL, tL), (sR, tR)):
                x0, x1 = s[lo - 1], s[hi + 1]
                m = hi + 1 - (lo - 1)
                for q in range(lo, hi + 1):
                    t[q] = x0 + (x1 - x0) * (q - (lo - 1)) / m
            cand = [(rL.interpolate(tL[q]).coords[0], rR.interpolate(tR[q]).coords[0]) for q in range(n)]
            cand[0], cand[-1] = pairs[0], pairs[-1]
            seg = cand[max(0, lo - 2):min(n, hi + 3)]
            if _max_tilt_deg(seg) > tilt_cap:
                break
            best = (tL, tR)
            worst = min(r for r, h in _span_ratios(tL[lo - 1:hi + 2], tR[lo - 1:hi + 2]))
            if worst >= ratio or (lo == 1 and hi == n - 2):
                break
        if best is not None:
            nL, nR = best
            changed = True
    if not changed:
        return pairs, False
    out = [(tuple(map(float, rL.interpolate(nL[q]).coords[0])), tuple(map(float, rR.interpolate(nR[q]).coords[0])))
           for q in range(n)]
    out[0], out[-1] = (tuple(pairs[0][0]), tuple(pairs[0][1])), (tuple(pairs[-1][0]), tuple(pairs[-1][1]))
    return out, True


def _crossed(pairs) -> int:
    """Consecutive rungs that cross each other (a twist the engine would sew as a bow-tie)."""
    from shapely.geometry import LineString

    return sum(1 for p, q in zip(pairs, pairs[1:])
               if LineString(p).crosses(LineString(q)))


def spread_letter_turns(planned: PlannedLetter, ratio: float = TURN_RATIO) -> PlannedLetter:
    """spread_turns on every open column of a planned shape; a column keeps its new pairs only if no
    rungs cross and it has no more fans than before. Lists the changed columns in check["spread_turns"]."""
    from cli_anything_inkstitch.trace import column_checks as cc

    done = []
    for k, c in enumerate(planned.columns):
        if c.closed:
            continue
        pairs = list(zip(c.left, c.right))
        new, changed = spread_turns(pairs, ratio)
        if not changed or _crossed(new) > _crossed(pairs) or len(cc.fans(new)) > len(cc.fans(pairs)):
            continue
        c.left, c.right = [p[0] for p in new], [p[1] for p in new]
        done.append(k)
    if done:
        planned.check["spread_turns"] = done
    return planned


# ---------------------------------------------------------------- slanted / pointed ends

# Where a column's end is cut on a slant or runs to a point (a V's tip, a diagonal leg tucked into a
# stem, a mitred corner), the end pair sits at the cut's angle while the column's interior rungs are
# square to the spine. Left to the engine, the rungs turn from square to the cut angle in the last
# couple of millimetres, so the stitches fan/pinch at the tip. A digitizer eases that turn in over a
# longer run. This is spread_turns' idea applied at the ends: smoothstep the rung angle from the end
# cut's angle at the tip to the column's own angle L mm in (StrokeStyles maps this to easing toward
# the stroke-end/L straight-transition direction; the paper gives the target angle, the easing is
# empirical). The end cut itself (the end pair) is not moved, so the outline and any junction trim
# are unchanged.
END_EASE_MM = 15.0        # ease a slanted end over about this far...
END_EASE_POINT_MM = 10.0  # ...a bit less into a near-pointed end
END_POINT_WIDTH_MM = 1.0  # an end narrower than this reads as a point
END_EASE_STEP_MM = 0.7
END_EASE_MIN_TILT_DEG = 20.0  # only ease an end whose cut leans at least this far from square
END_EASE_SPINE_FRAC = 0.6     # never ease more than this share of the column's length


def _rung_angle(pair) -> float:
    (lx, ly), (rx, ry) = pair
    return math.atan2(ry - ly, rx - lx)


def ease_end(pairs, ease_mm: float = END_EASE_MM, point_mm: float = END_EASE_POINT_MM,
             step_mm: float = END_EASE_STEP_MM):
    """Re-pair the near end (index 0) so the rung angle eases from the end cut's angle at the tip to
    the column's own angle `ease_mm` (or `point_mm` for a pointed end) in, by a smoothstep. Rungs are
    cast across the column's own rails, so the outline is unchanged; the end pair (the cut) and every
    pair beyond the eased run are kept. Returns new pairs, or None when the end is already near-square
    or the column is too short/odd to ease."""
    import numpy as np
    from shapely.geometry import LineString, Point

    if len(pairs) < 5:
        return None
    L = [tuple(map(float, p[0])) for p in pairs]
    R = [tuple(map(float, p[1])) for p in pairs]
    rL, rR = LineString(L), LineString(R)
    mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(L, R)]
    s = [0.0]
    for a, b in zip(mids, mids[1:]):
        s.append(s[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = s[-1]
    if total < 3 * step_mm:
        return None
    end_width = math.dist(L[0], R[0])
    want = point_mm if end_width < END_POINT_WIDTH_MM else ease_mm
    span = min(want, total * END_EASE_SPINE_FRAC)
    if span < 2 * step_mm:
        return None
    k = next((i for i, v in enumerate(s) if v >= span), len(s) - 1)
    if k < 2:
        return None
    a0 = _rung_angle((L[0], R[0]))          # the end cut's angle
    a1 = _rung_angle((L[k], R[k]))          # the column's own angle, span mm in
    da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
    if abs(da) < math.radians(END_EASE_MIN_TILT_DEG):
        return None                          # already near-square: nothing to ease
    cl = LineString(mids)
    sk = s[k]
    new = [(L[0], R[0])]                      # keep the cut
    t = step_mm
    while t < sk - 1e-9:
        u = t / sk
        w = u * u * (3 - 2 * u)               # smoothstep: 0 at the tip, 1 at span
        a = a1 - da * (1 - w)                 # cut angle at the tip -> square angle at span
        d = (math.cos(a), math.sin(a))
        cp = cl.interpolate(t).coords[0]
        big = LineString([(cp[0] - 30 * d[0], cp[1] - 30 * d[1]),
                          (cp[0] + 30 * d[0], cp[1] + 30 * d[1])])
        hits = []
        for rail in (rL, rR):
            x = big.intersection(rail)
            pts = [g for g in getattr(x, "geoms", [x]) if g.geom_type == "Point" and not g.is_empty]
            if not pts:
                break
            hits.append(min(pts, key=lambda g: g.distance(Point(cp))).coords[0])
        if len(hits) == 2:
            new.append((hits[0], hits[1]))
        t += step_mm
    new += [(L[i], R[i]) for i in range(k, len(pairs))]
    if len(new) < 3:
        return None
    return [((float(a[0]), float(a[1])), (float(b[0]), float(b[1]))) for a, b in new]


TUCK_SQUARE_MM = 15.0     # a tucked end's rungs are re-cast square to the band over this far in
TUCK_TIP_MM = 0.3         # the width left at the cut's outer corner (the column ends there in a point)


def square_tucked_end(pairs, span_mm: float = TUCK_SQUARE_MM, step_mm: float = END_EASE_STEP_MM,
                      tip_mm: float = TUCK_TIP_MM):
    """Re-pair the near end (index 0) of a column whose end tucks under another column at a slant:
    the rail that ends at the cut's inner corner (the corner nearer the column's body) is carried on
    along the cut to its outer corner, and every rung is square to the band, so the stitches stay
    square right into the joint and only shorten along the slanted edge, ending in a point at the
    outer corner. That is how a hand-digitized band ending on a slanted strand is built. The column's
    outline (rails + cut) is unchanged, so its coverage is too. No ease (ease_end) here: an eased
    tucked end turned the band's stitches toward the other column's slant over ~15 mm, and a plain
    slanted end pair fanned the last section from one corner, which the engine left part-bare at the
    sharp outer corner. Returns new pairs, or None when the end is already near-square or the column
    too short."""
    from shapely.geometry import LineString, Point

    if len(pairs) < 2:
        return None
    L = [tuple(map(float, p[0])) for p in pairs]
    R = [tuple(map(float, p[1])) for p in pairs]
    width = math.dist(L[0], R[0])
    mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(L, R)]
    cl = LineString(mids[::-1])
    if cl.length < 3 * step_mm or width <= 0:
        return None
    # the band's direction just behind the end region (body -> end)
    back = min(cl.length, max(2.0 * width, 2.0))
    p0, p1 = cl.interpolate(cl.length - back), cl.interpolate(cl.length - back / 2)
    tx, ty = p1.x - p0.x, p1.y - p0.y
    tn = math.hypot(tx, ty)
    if tn == 0:
        return None
    tx, ty = tx / tn, ty / tn
    cut = (R[0][0] - L[0][0], R[0][1] - L[0][1])
    if abs(cut[0] * tx + cut[1] * ty) / (math.hypot(*cut) or 1.0) < math.sin(math.radians(END_EASE_MIN_TILT_DEG)):
        return None                                        # already near-square: nothing to do
    inner_left = (L[0][0] * tx + L[0][1] * ty) <= (R[0][0] * tx + R[0][1] * ty)
    inner_rail, outer_rail = (L, R) if inner_left else (R, L)
    corner, outer = inner_rail[0], outer_rail[0]
    if math.dist(corner, outer) <= tip_mm + step_mm:
        return None
    # the inner rail, carried on along the cut to just short of the outer corner (body -> end)
    along = LineString([corner, outer])
    tip_in = along.interpolate(along.length - tip_mm)
    near = LineString(inner_rail[::-1] + [(tip_in.x, tip_in.y)])
    far = LineString(outer_rail[::-1])
    orig_near = LineString(inner_rail[::-1])
    span = min(span_mm, far.length * END_EASE_SPINE_FRAC)
    if span < 2 * step_mm:
        return None

    def _dir(rail, u):
        q0, q1 = rail.interpolate(max(u - 2.0, 0.0)), rail.interpolate(min(u + 2.0, rail.length))
        vx, vy = q1.x - q0.x, q1.y - q0.y
        vn = math.hypot(vx, vy) or 1.0
        return vx / vn, vy / vn

    new = [((tip_in.x, tip_in.y), outer)]                 # the end: a point at the outer corner
    n_prev = near.length - tip_mm * 0                     # rungs must step back along both rails
    t = far.length - step_mm
    while t > far.length - span + 1e-9:
        b = far.interpolate(t)
        # square to the band: the outer rail's direction and the inner rail's own (not the cut's)
        (ax_, ay_) = _dir(far, t)
        (bx_, by_) = _dir(orig_near, min(orig_near.project(b), orig_near.length))
        dx, dy = ax_ + bx_, ay_ + by_
        dn = math.hypot(dx, dy)
        if dn == 0:
            break
        nx, ny = -dy / dn, dx / dn
        reach = 4 * width + 1
        big = LineString([(b.x - nx * reach, b.y - ny * reach), (b.x + nx * reach, b.y + ny * reach)])
        x = big.intersection(near)
        pts = [g for g in getattr(x, "geoms", [x]) if g.geom_type == "Point" and not g.is_empty]
        if not pts:
            break
        a = min(pts, key=lambda g: g.distance(b))
        u = near.project(a)
        if u < n_prev - 1e-6:
            new.append(((a.x, a.y), (b.x, b.y)))
            n_prev = u
        t -= step_mm
    if len(new) < 3:
        return None
    # original pairs wholly further in than the last square rung (on both rails) are kept as they were
    a_last, b_last = new[-1]
    n_last, f_last = near.project(Point(a_last)), far.project(Point(b_last))
    keep = [(inner_rail[i], outer_rail[i]) for i in range(1, len(L))
            if near.project(Point(inner_rail[i])) < n_last - 0.5 * step_mm
            and far.project(Point(outer_rail[i])) < f_last - 0.5 * step_mm]
    out = new + keep                                       # (inner, outer) pairs, end first
    if not inner_left:
        out = [(o, i) for i, o in out]                     # back to (left, right)
    return [((float(a[0]), float(a[1])), (float(b[0]), float(b[1]))) for a, b in out]


def _head_turn_tilt(pairs, within_mm: float = 3.0) -> float:
    """Worst rung-to-rung angle step (deg) within `within_mm` of the head (index 0): how sharply the
    rungs turn as they approach that cut. The defect ease_end spreads out. Measured on the head alone
    so easing one end is judged by that end only (the caller orients the end being eased to index 0)."""
    if len(pairs) < 3:
        return 0.0
    mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in pairs]
    s = [0.0]
    for a, b in zip(mids, mids[1:]):
        s.append(s[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    worst = 0.0
    for i in range(len(pairs) - 1):
        if s[i] > within_mm:
            break
        da = (_rung_angle(pairs[i + 1]) - _rung_angle(pairs[i]) + math.pi) % (2 * math.pi) - math.pi
        worst = max(worst, abs(math.degrees(da)))
    return worst


def ease_letter_ends(planned: PlannedLetter, ease_mm: float = END_EASE_MM) -> PlannedLetter:
    """Ease the rung angle into every open column's free slanted or pointed ends (ease_end), one end
    at a time. An end that tucks under another column (joins._host) is not eased: its rungs are
    squared to the band up to the cut instead (square_tucked_end, check["squared_tucked_ends"]). An
    end's new pairs are kept only if they leave no more crossings and no more fans than before and,
    for an ease, actually reduce how sharply the rungs turn into that end. Rails (the outline) are
    never moved. Lists the eased columns in check["eased_ends"]."""
    from cli_anything_inkstitch.trace import column_checks as cc
    from cli_anything_inkstitch.trace.joins import _host

    done, squared, kept_ease = [], [], []
    for k, c in enumerate(planned.columns):
        if c.closed:
            continue
        for end in (0, -1):
            pairs = list(zip(c.left, c.right))
            work = pairs if end == 0 else pairs[::-1]       # the end being eased is now index 0
            if len(planned.columns) > 1 and _host(planned.columns, k, end, skip_acute_v=False) is not None:
                sq = square_tucked_end(work)
                if sq is not None and _crossed(sq) <= _crossed(work) and _same_outline(sq, work):
                    sq = sq if end == 0 else sq[::-1]
                    c.left, c.right = [p[0] for p in sq], [p[1] for p in sq]
                    squared.append([k, end])
                    continue
                if sq is not None:
                    # Squaring would move the outline (a tight curve the square rungs can't follow on
                    # its inner rail): ease that end as before (ease_end), so its cut stays covered.
                    kept_ease.append([k, end])
                # (sq None: already square or too short; ease_end below leaves a square end alone)
            eased_head = ease_end(work, ease_mm)
            if eased_head is None:
                continue
            if (_head_turn_tilt(eased_head) >= _head_turn_tilt(work)
                    or _crossed(eased_head) > _crossed(work)
                    or len(cc.fans(eased_head)) > len(cc.fans(work))):
                continue
            eased = eased_head if end == 0 else eased_head[::-1]
            c.left, c.right = [p[0] for p in eased], [p[1] for p in eased]
            if k not in done:
                done.append(k)
    if done:
        planned.check["eased_ends"] = done
    if squared:
        planned.check["squared_tucked_ends"] = squared
    if kept_ease:
        planned.check["tucked_ends_eased"] = kept_ease
    return planned


def _same_outline(a_pairs, b_pairs, tol_mm2: float = 0.3) -> bool:
    """Whether two pairings of one column cover the same outline (rails + end cuts), within tol_mm2
    (a squared tucked end loses ~0.15 mm2: its point and rail sampling; a tight curve the square rungs
    could not follow lost 1.4 mm2)."""
    from shapely.geometry import Polygon

    poly = lambda ps: Polygon([p[0] for p in ps] + [p[1] for p in ps][::-1]).buffer(0)
    return poly(a_pairs).symmetric_difference(poly(b_pairs)).area <= tol_mm2


def place_rungs(planned: PlannedLetter, rungs, step_mm: float = 1.0) -> PlannedLetter:
    """Override: hand-placed rungs. Each rung is a segment ((x1, y1), (x2, y2)) in mm, y up, across one
    column. The column's pairs are rebuilt with those rungs as anchors (its end pairs kept), and between
    anchors pairs follow both rails in proportion every ~step_mm. A rung that doesn't cross both rails
    of a column is ignored (listed in check["rungs_ignored"])."""
    from shapely.geometry import LineString, Point

    ignored, used = [], 0
    for c in planned.columns:
        if c.closed or len(c.left) < 2:
            continue
        rL, rR = LineString(c.left), LineString(c.right)
        sL0, sL1 = 0.0, rL.length
        sR0, sR1 = 0.0, rR.length
        anchors = []
        for seg in rungs:
            (x1, y1), (x2, y2) = seg
            dx, dy = x2 - x1, y2 - y1
            line = LineString([(x1 - 0.15 * dx, y1 - 0.15 * dy), (x2 + 0.15 * dx, y2 + 0.15 * dy)])
            hits = []
            for rail in (rL, rR):
                x = line.intersection(rail)
                pts = [g for g in getattr(x, "geoms", [x]) if g.geom_type == "Point" and not g.is_empty]
                if not pts:
                    break
                mid = Point((x1 + x2) / 2, (y1 + y2) / 2)
                hits.append(rail.project(min(pts, key=lambda p: p.distance(mid))))
            if len(hits) == 2 and sL0 < hits[0] < sL1 and sR0 < hits[1] < sR1:
                anchors.append(tuple(hits))
        if not anchors:
            continue
        anchors.sort()
        keep = [(sL0, sR0)]
        for a in anchors:                       # drop any rung that would cross the previous one
            if a[0] > keep[-1][0] and a[1] > keep[-1][1]:
                keep.append(a)
        keep.append((sL1, sR1))
        used += len(keep) - 2
        out = []
        for (a0, b0), (a1, b1) in zip(keep, keep[1:]):
            m = max(1, int(round(max(a1 - a0, b1 - b0) / step_mm)))
            for q in range(m):
                t = q / m
                out.append((rL.interpolate(a0 + (a1 - a0) * t).coords[0], rR.interpolate(b0 + (b1 - b0) * t).coords[0]))
        out.append((c.left[-1], c.right[-1]))
        out[0] = (c.left[0], c.right[0])
        c.left = [tuple(map(float, p[0])) for p in out]
        c.right = [tuple(map(float, p[1])) for p in out]
    planned.check["rungs_placed"] = used
    ignored = len(rungs) - used
    if ignored:
        planned.check["rungs_ignored"] = ignored
    return planned


def _split_at(i, arrs, sc, label, points):
    """Override: cut the shape's strokes at the given points (mm, y up) and re-plan. Each point cuts
    the stroke whose centreline passes nearest it; the two pieces butt there."""
    import numpy as np
    from cli_anything_inkstitch.planner import columns as col

    ac, _ = require_planner()
    lt = sc.letter
    box = lt.box
    W, H = (box[2] - box[0]) or 1.0, (box[3] - box[1]) or 1.0
    strokes = list(lt.strokes)
    for pt in points:
        uv = np.array([(pt[0] - box[0]) / W, (pt[1] - box[1]) / H])
        best = None
        for k, s in enumerate(strokes):
            P = np.asarray(s.path, float)
            if s.closed or len(P) < 2:
                continue
            dense = [P[0]]
            for a, b in zip(P, P[1:]):
                for t in np.linspace(0, 1, 12)[1:]:
                    dense.append(a + (b - a) * t)
            dense = np.array(dense)
            d = np.hypot((dense[:, 0] - uv[0]) * W, (dense[:, 1] - uv[1]) * H)
            j = int(np.argmin(d))
            if best is None or d[j] < best[0]:
                best = (d[j], k, dense, j)
        if best is None:
            continue
        _, k, dense, j = best
        if j < 2 or j > len(dense) - 3:
            continue
        s = strokes[k]
        a = col.Stroke(path=[tuple(map(float, p)) for p in dense[:j + 1]], smooth=s.smooth,
                       start_cut=s.start_cut, end_cut="perpendicular")
        b = col.Stroke(path=[tuple(map(float, p)) for p in dense[j:]], smooth=s.smooth,
                       start_cut="perpendicular", end_cut=s.end_cut)
        strokes[k:k + 1] = [a, b]
    try:
        sc2 = ac.plan_shape(i, arrs, strokes=strokes)
    except Exception:  # noqa: BLE001
        return None
    if sc2.letter is None:
        return None
    p = _from_letter(sc2.letter, bool(sc2.ok), sc2.check or {}, label)
    p.check["split_at"] = [list(map(float, q)) for q in points]
    return p


def _move_ring_seam(planned: PlannedLetter, at) -> PlannedLetter:
    """Override: start each ring (closed column) at the pair nearest `at` (mm, y up), keeping its
    overlap length past the seam (planner columns.RING_OVERLAP_MM, ~0.3 mm along the centre)."""
    import math as m

    for c in planned.columns:
        if not c.closed or len(c.left) < 4:
            continue
        mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(c.left, c.right)]
        # the ring proper ends at the first pair that repeats pairs[0]; later pairs are the overlap
        n = next((k for k in range(1, len(mids)) if m.dist(mids[k], mids[0]) < 1e-6), len(mids))
        ring_l, ring_r = c.left[:n], c.right[:n]
        k = min(range(n), key=lambda q: m.dist(mids[q], at))
        L = ring_l[k:] + ring_l[:k]
        R = ring_r[k:] + ring_r[:k]
        over = 0.3
        nxt_l, nxt_r = L[1 % n], R[1 % n]
        ml, mr = m.dist(L[0], nxt_l) or 1.0, m.dist(R[0], nxt_r) or 1.0
        mid_step = (ml + mr) / 2
        t = min(1.0, over / mid_step)
        ext_l = (L[0][0] + (nxt_l[0] - L[0][0]) * t, L[0][1] + (nxt_l[1] - L[0][1]) * t)
        ext_r = (R[0][0] + (nxt_r[0] - R[0][0]) * t, R[0][1] + (nxt_r[1] - R[0][1]) * t)
        c.left = L + [L[0], ext_l]
        c.right = R + [R[0], ext_r]
    planned.check["seam_at"] = list(map(float, at))
    return planned


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
    return sum(mm for _, mm in exposed_hops(letter))


def column_ends(left, right):
    """A column's two ends: the midpoints of its first and last rail pair,
    (end_a, end_b). The engine's start_at_nearest_point enters the column at one
    of these and leaves at the other."""
    mid = lambda i: ((left[i][0] + right[i][0]) / 2.0, (left[i][1] + right[i][1]) / 2.0)
    return mid(0), mid(-1)


def glyph_polygon(contours):
    """The filled glyph as one shapely geometry: outer rings add, rings they
    contain cut out as counters. None when there is nothing fillable. Shared by
    exposed_hops and the artifact /route endpoint so both test travel against
    the same covered area."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    polys = [Polygon(c) for c in contours if len(c) >= 3]
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
    return glyph


def centre_line(left, right, samples: int = 40):
    """A column's centre line from its two rails: the midpoints of its rail pairs when the rails
    pair node for node (the planner's columns, the engine's rung-less pairing), else both rails
    resampled at `samples` even steps. The engine walks a satin's offset_center_line
    (lib/elements/satin_column.py) for start_at_nearest_point's hidden entry run."""
    if len(left) == len(right):
        return [((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0) for a, b in zip(left, right)]
    from shapely.geometry import LineString

    ls, rs = LineString(left), LineString(right)
    out = []
    for i in range(samples + 1):
        a, b = ls.interpolate(i / samples, normalized=True), rs.interpolate(i / samples, normalized=True)
        out.append(((a.x + b.x) / 2.0, (a.y + b.y) / 2.0))
    return out


def _flags(v, n):
    if v is None or isinstance(v, bool):
        return [bool(v)] * n
    if isinstance(v, (set, frozenset)):
        return [k in v for k in range(n)]
    return [bool(x) for x in v]


def route_walk(ends, covered=None, *, as_drawn=None, centre_lines=None, exact=None, start=None):
    """Simulate the engine's sew-walk over a sequence of columns, given each column's two ends
    (column_ends) in SEWING ORDER.

    Returns one record per column, in order:
      {"order": k, "entry": (x, y), "exit": (x, y), "exposed": float,
       "is_jump_before": bool, "arrive": (x, y), "travel": float, "hidden_run": float}

    Default model (end_at_nearest_point on, the engine default): the first column is taken to sew
    end_a -> end_b (entry=end_a, exit=end_b); each later column starts at whichever of its ends is
    nearer where the previous column left the needle and leaves it at the far end -- exactly as
    exposed_hops measures travel, so the entry/exit decision lives in one place.

    as_drawn (one bool for all, or per column: a list of bools or a set of indexes): the column has
    end_at_nearest_point off, so it sews end_a -> end_b as drawn: the engine neither picks the end
    nor splits the top layer to finish near the next element (lib/elements/satin_column.py
    end_point / _split_top_layer). Its exit is end_b. With start_at_nearest_point on
    (start_point) the needle lands on the point of its centre line (centre_lines[k], when given)
    nearest the previous exit and runs back along it, under the column, to end_a: that hidden
    run's length is `hidden_run`. exact (same forms): start_at_nearest_point off, so the needle
    lands on end_a itself.

    `arrive` is where the travel into the column lands, `travel` its straight length. `exposed` is
    the part of that travel outside `covered` (a shapely geometry, same units as the ends): 0 for
    the first column (unless `start`, the needle's position before column 0, is given) and
    whenever `covered` is None. is_jump_before is True when that travel crosses bare fabric at
    all; callers that only care about visible jumps apply their own length threshold to
    `exposed`."""
    out = []
    if not ends:
        return out
    n = len(ends)
    drawn, ex = _flags(as_drawn, n), _flags(exact, n)
    if covered is not None or centre_lines is not None:
        from shapely.geometry import LineString, Point
    cur = start
    for k in range(n):
        a, b = ends[k]
        hidden = 0.0
        if drawn[k] or cur is None:
            entry, far = a, b
            arrive = a
            if drawn[k] and not ex[k] and cur is not None and centre_lines is not None \
                    and len(centre_lines[k]) >= 2:
                cl = LineString(centre_lines[k])
                t = cl.project(Point(cur))
                q = cl.interpolate(t)
                arrive, hidden = (q.x, q.y), t
        else:
            entry, far = (a, b) if math.dist(cur, a) <= math.dist(cur, b) else (b, a)
            arrive = entry
        travel = math.dist(cur, arrive) if cur is not None else 0.0
        exposed = 0.0
        if covered is not None and travel > 1e-6:
            exposed = LineString([cur, arrive]).difference(covered).length
        out.append({"order": k, "entry": entry, "exit": far, "exposed": exposed,
                    "is_jump_before": exposed > 0.0, "arrive": arrive, "travel": travel,
                    "hidden_run": hidden})
        cur = far
    return out


# The engine's collapse_len default (metadata collapse_len_mm, 3 mm: read in lib/elements/satin_column.py
# min_jump_stitch_len and stitch_groups_to_stitch_plan): travel longer than
# this becomes a jump the machine sews as a long loose stitch unless it is trimmed.
ONE_PASS_TRIM_MM = 3.0
# ...measured from the planned rails, which the needle overshoots: pull compensation widens a satin
# (0.2-0.3 mm a side) and the last stitch lands past the rail, so a 2.99 mm planned handoff sewed as
# a 3.2 mm jump. Handoffs within this margin of trim_mm are trimmed too.
ONE_PASS_TRIM_MARGIN_MM = 0.5


def _reverse_column(c: PlannedColumn) -> None:
    c.left, c.right = list(c.left)[::-1], list(c.right)[::-1]


def plan_one_pass(letter: PlannedLetter, arrival=None, trim_handoffs: bool = True,
                  trim_mm: float = ONE_PASS_TRIM_MM, exposed_mm: float = EXPOSED_TRAVEL_MM,
                  margin_mm: float = ONE_PASS_TRIM_MARGIN_MM) -> dict:
    """Orient a shape's columns so each sews in ONE pass, and say where to trim.

    The engine's satin default end_at_nearest_point splits a column's top layer into two passes
    that meet mid-column, so it can finish near the next element (lib/elements/satin_column.py
    end_point / _split_top_layer): a visible seam. With it off a column sews end_a -> end_b as
    drawn, so the planner picks the direction instead, in sewing order (columns are reversed in
    place; their rail pairing is kept):

      1. each column but the last finishes at the end nearest the next column;
      2. trim after a column whose handoff to the next one, from its farther end rail point, is
         longer than trim_mm less margin_mm (the engine would jump it: the needle overshoots the
         planned rail by the pull compensation) or lies on bare fabric for more than exposed_mm (route_walk, as drawn);
         the shape's last column always trims (shapes-to-satin trims between shapes);
      3. a column that trims afterwards instead starts at the end nearest where the needle
         arrives (a shorter hidden entry run, or a shorter jump), and a column the needle reaches
         after a trim starts exactly at its start (start_at_nearest_point off: `start_exact`).

    arrival: where the needle is before this shape (the previous shape's exit), or None.
    trim_handoffs False: no trims inside the shape (only the orientation).

    Returns {"trim_after": [k...] (inside the shape, not the last column), "start_exact": [k...],
    "reversed": [k...], "exit": (x, y), "hidden_runs": [mm per column]}."""
    cols = letter.columns
    n = len(cols)
    if n == 0:
        return {"trim_after": [], "start_exact": [], "reversed": [], "exit": arrival, "hidden_runs": []}
    from shapely.geometry import LineString, Point, Polygon

    def band(c):
        p = Polygon(list(c.left) + list(c.right)[::-1])
        return p if p.is_valid else p.buffer(0)

    flipped: set[int] = set()

    def flip(k):
        _reverse_column(cols[k])
        flipped.symmetric_difference_update({k})

    for k in range(n - 1):                       # 1. finish nearest the next column
        if cols[k].closed:
            continue
        a, b = column_ends(cols[k].left, cols[k].right)
        nxt = band(cols[k + 1])
        if nxt.distance(Point(a)) < nxt.distance(Point(b)):
            flip(k)
    covered = glyph_polygon(letter.contours)
    covered = covered.buffer(0.05) if covered is not None else None
    trims = {n - 1}
    if trim_handoffs:                            # 2. trims where the handoff is a jump or bare
        recs = route_walk([column_ends(c.left, c.right) for c in cols], covered, as_drawn=True,
                          centre_lines=[centre_line(c.left, c.right) for c in cols])
        lines = [LineString(centre_line(c.left, c.right)) for c in cols]

        def rail_travel(k):
            # The top layer's last stitch lands on a RAIL at the column's end, up to half its width
            # from the centre-line end the walk uses: a 1.5 mm centre handoff sewed as a 3.2 mm jump.
            # Measure from whichever end rail point is farther from the next column's centre line.
            prev = cols[k - 1]
            return max(lines[k].distance(Point(prev.left[-1])), lines[k].distance(Point(prev.right[-1])))

        trims |= {k - 1 for k in range(1, n)
                  if max(recs[k]["travel"], rail_travel(k)) > trim_mm - margin_mm or recs[k]["exposed"] > exposed_mm}
    cur = arrival                                # 3. a trimmed column starts nearest its arrival
    for k in range(n):
        a, b = column_ends(cols[k].left, cols[k].right)
        if k in trims and cur is not None and not cols[k].closed and math.dist(cur, b) < math.dist(cur, a):
            flip(k)
            a, b = b, a
        cur = b
    exact = {0} | {k + 1 for k in trims if k + 1 < n}
    recs = route_walk([column_ends(c.left, c.right) for c in cols], None, as_drawn=True,
                      centre_lines=[centre_line(c.left, c.right) for c in cols], exact=exact,
                      start=arrival)
    return {"trim_after": sorted(trims - {n - 1}), "start_exact": sorted(exact),
            "reversed": sorted(flipped), "exit": recs[-1]["exit"],
            "hidden_runs": [r["hidden_run"] for r in recs]}


def exposed_hops(letter: PlannedLetter) -> list[tuple[int, float]]:
    """(k, mm) for each travel from column k to column k+1, sewn in planned order, that would lie
    on bare fabric for more than a hair (exposed_travel_mm sums these)."""
    if len(letter.columns) < 2:
        return []
    glyph = glyph_polygon(letter.contours)
    if glyph is None:
        return []
    glyph = glyph.buffer(0.05)
    ends = [column_ends(c.left, c.right) for c in letter.columns]
    return [(r["order"] - 1, r["exposed"])
            for r in route_walk(ends, glyph)[1:] if r["exposed"] > 0.0]
