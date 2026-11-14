"""Joints: a butting column's end lies flat along the edge it meets, keep_mm under it, and sews
before the column it tucks under (trace/joins.py flatten_joint_ends / tucked_first).

Synthetic columns and shapes only."""

from __future__ import annotations

import json
import math

import pytest

pytest.importorskip("shapely")

from cli_anything_inkstitch.trace import joins as J  # noqa: E402
from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn, PlannedLetter  # noqa: E402


def _bar(x0, y0, x1, y1, w):
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy)
    ox, oy = -dy / n * w / 2, dx / n * w / 2
    return PlannedColumn(left=[(x0 + ox, y0 + oy), (x1 + ox, y1 + oy)],
                         right=[(x0 - ox, y0 - oy), (x1 - ox, y1 - oy)])


def _letter(cols, outline):
    return PlannedLetter("t", cols, [outline], True, {})


def _depths(c, end, host):
    from shapely.geometry import Point
    p = J._poly(host)
    out = []
    for q in (c.left[end], c.right[end]):
        d = p.exterior.distance(Point(q))
        out.append(d if p.contains(Point(q)) else -d)
    return out


# a T: a 20 x 4 mm bar on top (y 16..20), a 4 mm stem from y 0 whose top is cut on a slant and
# runs 0.8-2.0 mm up into the bar (a planner's overlap, not flat on the bar's edge)
T_OUTLINE = [(0, 16), (0, 20), (20, 20), (20, 16), (12, 16), (12, 0), (8, 0), (8, 16)]


def _t_shape(stem_first=False):
    bar = _bar(0, 18, 20, 18, 4)
    stem = PlannedColumn(left=[(8, 0), (8, 18.0)], right=[(12, 0), (12, 16.8)])
    return _letter([stem, bar] if stem_first else [bar, stem], T_OUTLINE)


class TestFlatJointEnds:
    def test_slanted_overlap_becomes_flat_keep_under(self):
        lt = J.flatten_joint_ends(_t_shape(), keep_mm=0.4)
        stem, bar = lt.columns[1], lt.columns[0]
        assert all(abs(d - 0.4) < 0.05 for d in _depths(stem, -1, bar))
        assert lt.check["joint_ends_flattened"][0]["under"] == 0

    def test_zero_keep_butts_exactly_on_the_edge(self):
        lt = J.flatten_joint_ends(_t_shape(), keep_mm=0.0)
        assert all(abs(d) < 0.05 for d in _depths(lt.columns[1], -1, lt.columns[0]))

    def test_short_end_runs_on_under_the_edge(self):
        bar = _bar(0, 18, 20, 18, 4)
        stem = PlannedColumn(left=[(8, 0), (8, 16.1)], right=[(12, 0), (12, 16.1)])   # 0.1 mm in
        lt = J.flatten_joint_ends(_letter([bar, stem], T_OUTLINE), keep_mm=0.4)
        assert all(abs(d - 0.4) < 0.05 for d in _depths(lt.columns[1], -1, lt.columns[0]))

    def test_no_bare_fabric_added(self):
        lt = _t_shape()
        outline = J._outline(lt.contours)
        before = J._bare(outline, lt.columns)
        J.flatten_joint_ends(lt, keep_mm=0.4)
        assert J._bare(outline, lt.columns) <= before + J.BARE_TOL_MM2

    def test_mitre_is_left_alone(self):
        # an L whose two columns end in each other (a mitred corner): neither butts the other's side
        a = PlannedColumn(left=[(0, 0), (10, 0)], right=[(0, 4), (14, 4)])
        b = PlannedColumn(left=[(10, 0), (10, 20)], right=[(14, 4), (14, 20)])
        a.right[-1] = (13.9, 4.1)
        b.left[0] = (10.1, -0.1)
        lt = J.flatten_joint_ends(_letter([a, b], [(0, 0), (14, 0), (14, 20), (10, 20), (10, 4), (0, 4)]))
        assert "joint_ends_flattened" not in lt.check


class TestTuckedFirst:
    def test_the_tucked_column_sews_before_the_one_on_top(self):
        lt = J.tucked_first(J.flatten_joint_ends(_t_shape(stem_first=False)))
        # the stem (its end under the bar) now sews first
        assert lt.columns[0].left[0] == (8, 0)
        assert lt.check["sew_order_changed"] == [1, 0]
        assert lt.check["joint_ends_flattened"][0]["column"] == 0       # indices remapped
        assert lt.check["joint_ends_flattened"][0]["under"] == 1

    def test_good_order_is_untouched(self):
        lt = J.tucked_first(J.flatten_joint_ends(_t_shape(stem_first=True)))
        assert "sew_order_changed" not in lt.check

    def test_tidy_joins_runs_both(self):
        lt = J.tidy_joins(_t_shape(), keep_mm=0.4)
        stem, bar = lt.columns
        assert stem.left[0] == (8, 0)
        assert all(abs(d - 0.4) < 0.1 for d in _depths(stem, -1, bar))


class TestSetting:
    def test_presets_carry_a_joint_overlap(self):
        from cli_anything_inkstitch.commands.tools import FABRICS
        assert FABRICS["twill"]["joint_overlap_mm"] == J.KEEP_MM
        assert FABRICS["knit"]["joint_overlap_mm"] > J.KEEP_MM

    def test_override_key_is_known(self, tmp_path):
        from cli_anything_inkstitch.commands.tools import _load_overrides
        p = tmp_path / "ov.json"
        p.write_text(json.dumps({"shape1": {"joint_overlap_mm": 0.8}}))
        assert _load_overrides(str(p))["shape1"]["joint_overlap_mm"] == 0.8


try:
    import cli_anything_inkstitch.planner.art_columns  # noqa: F401
    import scipy.spatial  # noqa: F401
    HAVE_PLANNER = True
except ImportError:
    HAVE_PLANNER = False


def _engine():
    from cli_anything_inkstitch.binary import discover
    return discover() is not None


@pytest.mark.skipif(not (HAVE_PLANNER and _engine()), reason="needs the planner and the Ink/Stitch binary")
class TestEngineCoversTheJoint:
    """Behavioural proof (CLAUDE.md rule 3) on a filled T (1 unit = 1 mm): the stem's end runs under
    the bar, the engine sews the stem first, and its stitches leave no bare fabric along the joint."""

    T = ('<path id="t" d="M10,5 L40,5 L40,9 L27,9 L27,35 L23,35 L23,9 L10,9 Z" '
         'style="fill:#2e7d32;stroke:none"/>')

    def _run(self, tmp_path, *opts):
        from click.testing import CliRunner

        from cli_anything_inkstitch.artifact.design_model import extract_stitch_blocks, stitch_plan_svg
        from cli_anything_inkstitch.cli import root
        from tests.test_artifact_gate import make_project

        proj = make_project(tmp_path, self.T)
        r = CliRunner().invoke(root, ["--json", "tools", "shapes-to-satin", "--project", proj, "--fabric", "twill", *opts])
        assert r.exit_code == 0, r.output
        segs = [(a, c) for b in extract_stitch_blocks(stitch_plan_svg(proj))["blocks"]
                for p in b["paths"] for a, c in zip(p, p[1:])]
        return segs

    @staticmethod
    def _covered(segs, pt, tol=0.25):
        from shapely.geometry import LineString, Point
        return any(LineString([a, c]).distance(Point(pt)) <= tol for a, c in segs if a != c)

    def test_stem_first_and_the_joint_is_covered(self, tmp_path):
        segs = self._run(tmp_path)
        stem = [i for i, (a, c) in enumerate(segs) if abs(c[0] - a[0]) > 2.5 and abs(c[1] - a[1]) < 1.5
                and 10 < (a[1] + c[1]) / 2 < 34]
        bar = [i for i, (a, c) in enumerate(segs) if abs(c[1] - a[1]) > 2.5 and 5 < (a[1] + c[1]) / 2 < 9]
        assert stem and bar and max(stem) < min(bar)
        joint = [(x / 10, y) for x in range(233, 268, 2) for y in (8.8, 9.0, 9.2)]
        assert all(self._covered(segs, p) for p in joint)
        assert not self._covered(segs, (25.0, 2.0))          # the check can fail: off the design

    def test_the_overlap_setting_reaches_the_engine(self, tmp_path):
        """--joint-overlap-mm moves where the stem's stitches stop under the bar (y = 9 is the bar's
        edge, y grows downward): 0.4 runs ~0.4 mm further up under the bar than 0."""
        def stem_top(segs):
            # the stem's zigzag: rail to rail across it (x 23 -> 27, plus pull comp), not the bar's
            # underlay, which also runs along y ~ 8.6
            rail = lambda x, r: abs(x - r) < 0.6
            return min(min(a[1], c[1]) for a, c in segs
                       if ((rail(a[0], 23) and rail(c[0], 27)) or (rail(a[0], 27) and rail(c[0], 23)))
                       and abs(c[1] - a[1]) < 1.5)
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        keep = stem_top(self._run(tmp_path / "a", "--joint-overlap-mm", "0.4"))
        flush = stem_top(self._run(tmp_path / "b", "--joint-overlap-mm", "0"))
        assert 0.25 < flush - keep < 0.6


def _slanted_band():
    """A 4 mm band along x (y 0..4) whose right end is cut at 45 degrees from (30, 0) to (26, 4),
    paired the way a planner might: proportionally along each rail, so the rungs lean more and more
    toward the cut (a long fan into the end)."""
    n = 30
    return PlannedColumn(left=[(30.0 * i / n, 0.0) for i in range(n + 1)],
                         right=[(26.0 * i / n, 4.0) for i in range(n + 1)])


def _diagonal_host():
    """A 4 mm strand along x + y = 32 whose near edge (x + y = 29.17) runs 0.6 mm in front of the
    band's cut: the band's end tucks under it."""
    c, h = (1 / math.sqrt(2), 1 / math.sqrt(2)), 2.0
    a, b = (36.0, -4.0), (22.0, 10.0)
    off = lambda p, s: (p[0] - s * h * c[0], p[1] - s * h * c[1])
    return PlannedColumn(left=[off(a, 1), off(b, 1)], right=[off(a, -1), off(b, -1)])


def _angle(pair):
    (ax, ay), (bx, by) = pair
    return math.degrees(math.atan2(by - ay, bx - ax)) % 180


class TestTuckedEndsAreSquareNotEased:
    """An end that tucks under another column keeps the band's own stitch angle right to the end:
    stitches follow the path (perpendicular to its centre line) and a slanted end cut only shortens
    them; the band's edges run all the way to the other piece (no notch) and tuck keep_mm under it.
    A free end is still eased (glyph_columns.ease_end)."""

    def _outline(self, *cols):
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        return unary_union([Polygon(list(c.left) + list(c.right)[::-1]) for c in cols]).buffer(0)

    def test_stitches_keep_the_band_angle_to_the_end(self):
        from shapely.geometry import Polygon

        from cli_anything_inkstitch.trace.glyph_columns import straight_tucked_end
        band, host = _slanted_band(), _diagonal_host()
        outline = self._outline(band, host)
        pairs = list(zip(band.left, band.right))[::-1]            # the cut is index 0
        st = straight_tucked_end(pairs, J._poly(host), outline, None, keep_mm=0.4)
        assert st is not None
        recast = [p for p in st if min(p[0][0], p[1][0]) > 20.5]
        assert len(recast) > 10 and all(abs(_angle(p) - 90) < 1.0 for p in recast)   # vertical, no fan
        # the band ends 0.4 mm under the host's edge (x + y = 29.17 + 0.57) along both its rails
        tip_x = max(max(p[0][0], p[1][0]) for p in st)
        assert abs(tip_x - 29.74) < 0.3
        # ... and covers everything the planned band did short of that tuck depth
        band_poly = Polygon(list(band.left) + list(band.right)[::-1])
        new_poly = Polygon([p[0] for p in st] + [p[1] for p in st][::-1]).buffer(0)
        assert band_poly.difference(J._poly(host).buffer(-0.4)).difference(new_poly).area < 0.1

    def test_no_notch_where_the_band_stopped_short_of_the_host(self):
        from shapely.geometry import Polygon

        from cli_anything_inkstitch.trace.glyph_columns import straight_tucked_end
        # the planned cut stops 1 mm short of the host at its bottom corner: the outline runs on
        band = PlannedColumn(left=[(30.0 * i / 30, 0.0) for i in range(30)] + [(28.0, 0.0)],
                             right=[(26.0 * i / 30, 4.0) for i in range(30)] + [(25.4, 4.0)])
        band.left = [(x * 28.0 / 29.0, y) for x, y in band.left[:-1]] + [band.left[-1]]
        host = _diagonal_host()
        outline = self._outline(_slanted_band(), host)            # the art: band reaching the host
        pairs = list(zip(band.left, band.right))[::-1]
        st = straight_tucked_end(pairs, J._poly(host), outline, None, keep_mm=0.4)
        assert st is not None
        new_poly = Polygon([p[0] for p in st] + [p[1] for p in st][::-1]).buffer(0)
        bare = outline.difference(J._poly(host)).difference(new_poly)
        assert bare.area < 0.1                                    # the corner is covered

    def test_tucked_end_is_squared_and_free_end_is_eased(self):
        from cli_anything_inkstitch.trace.glyph_columns import ease_letter_ends
        band, host = _slanted_band(), _diagonal_host()
        outline = self._outline(band, host)
        lt = ease_letter_ends(PlannedLetter("t", [band, host], [list(outline.exterior.coords)[:-1]], True, {}))
        assert [0, -1] in lt.check.get("squared_tucked_ends", [])
        tucked = [p for p in zip(lt.columns[0].left, lt.columns[0].right) if min(p[0][0], p[1][0]) > 20.5]
        assert tucked and all(abs(_angle(p) - 90) < 1.0 for p in tucked)
        free = _slanted_band()
        lt2 = ease_letter_ends(PlannedLetter("f", [free], [free.left + free.right[::-1]], True, {}))
        assert lt2.check.get("eased_ends") == [0]


class TestEndStyle:
    """--end-style for free slanted ends: ease (#11), path (keep the stroke's angle and shorten into
    the cut), engine (leave the planned pairs)."""

    def test_path_keeps_the_angle_ease_turns_it_engine_leaves_it(self):
        from cli_anything_inkstitch.trace.glyph_columns import ease_letter_ends
        res = {}
        for style in ("ease", "path", "engine"):
            band = _slanted_band()
            # square the planned pairs first, so only the end treatment differs
            band.right = [(x, 4.0) for x, _ in band.left[:-4]] + list(band.right[-4:])
            lt = ease_letter_ends(PlannedLetter("t", [band], [band.left + band.right[::-1]], True, {}),
                                  end_style=style)
            near = [p for p in zip(lt.columns[0].left, lt.columns[0].right) if min(p[0][0], p[1][0]) > 21.0]
            res[style] = max(abs(_angle(p) - 90) for p in near)
        assert res["path"] < 1.0                 # square to the stroke right into the cut
        assert res["ease"] > 10.0                # turned toward the 45 degree cut
        assert res["engine"] > res["path"]       # the planned end pairs, as they were

    def test_unknown_style_is_refused(self):
        from cli_anything_inkstitch.trace.glyph_columns import ease_letter_ends
        band = _slanted_band()
        with pytest.raises(ValueError):
            ease_letter_ends(PlannedLetter("t", [band], [band.left + band.right[::-1]], True, {}), end_style="x")
