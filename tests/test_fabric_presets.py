"""shapes-to-satin --fabric: satin spacing, pull compensation and underlay ladder per fabric.

Why: one underlay ladder for every fabric over-builds stable woven fabric (a twill jacket back
needs an edge walk where fleece needs zigzag) and under-builds pile. Presets live in
commands/tools.py FABRICS; "standard" is the ladder text-to-satin has always used.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.commands.tools import FABRICS, _lettering_satin_params

try:
    import cli_anything_inkstitch.planner.art_columns  # noqa: F401
    import scipy.spatial  # noqa: F401
    HAVE_PLANNER = True
except ImportError:
    HAVE_PLANNER = False


def _underlay(p):
    return sorted(k for k in ("center_walk_underlay", "contour_underlay", "zigzag_underlay") if p.get(k) == "true")


class TestLadder:
    def test_standard_is_the_old_ladder(self):
        assert _underlay(_lettering_satin_params([1.0])) == ["center_walk_underlay"]
        assert _underlay(_lettering_satin_params([2.0])) == ["contour_underlay"]
        assert _underlay(_lettering_satin_params([4.5])) == ["contour_underlay", "zigzag_underlay"]
        assert "zigzag_spacing_mm" not in _lettering_satin_params([2.0])

    def test_twill_by_width(self):
        tw = lambda w: _lettering_satin_params([w], None, "twill")
        assert _underlay(tw(1.8)) == ["center_walk_underlay"]               # < 2 mm: centre walk
        assert _underlay(tw(4.5)) == ["contour_underlay"]                   # 2-5 mm: edge walk only
        assert _underlay(tw(6.0)) == ["contour_underlay", "zigzag_underlay"]
        p = tw(3.0)
        assert p["zigzag_spacing_mm"] == "0.4" and p["pull_compensation_mm"] == "0.2"

    def test_fleece_builds_more_and_explicit_pull_wins(self):
        assert _underlay(_lettering_satin_params([3.5], None, "fleece")) == ["contour_underlay", "zigzag_underlay"]
        assert _lettering_satin_params([3.5], 0.5, "fleece")["pull_compensation_mm"] == "0.5"

    def test_split_satin_above_seven_mm(self):
        assert _lettering_satin_params([8.0], None, "twill")["max_stitch_length_mm"] == "7"

    def test_every_preset_names_the_same_keys(self):
        assert all(set(v) == set(FABRICS["standard"]) for v in FABRICS.values())


@pytest.mark.skipif(not HAVE_PLANNER or discover() is None, reason="planner or inkstitch binary missing")
class TestEngineReadsThePreset:
    def _stitches(self, tmp_path, fabric):
        from cli_anything_inkstitch.cli import root
        import pyembroidery as pe
        k = 96 / 25.4
        d = f"M {5 * k},{5 * k} L {45 * k},{5 * k} L {45 * k},{8.5 * k} L {5 * k},{8.5 * k} Z"   # 40 x 3.5 mm
        svg = tmp_path / f"{fabric}.svg"
        svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="14mm" '
                       f'viewBox="0 0 {50 * k:.4f} {14 * k:.4f}"><path id="bar" d="{d}" style="fill:#2e7d32"/></svg>')
        proj = tmp_path / f"{fabric}.json"
        assert CliRunner().invoke(root, ["document", "open", "--project", str(proj), "--svg", str(svg)]).exit_code == 0
        r = CliRunner().invoke(root, ["--json", "tools", "shapes-to-satin", "--project", str(proj), "--fabric", fabric])
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["shapes"][0]["stitch"] == "satin"
        out = tmp_path / f"{fabric}.dst"
        assert CliRunner().invoke(root, ["export", "file", "--project", str(proj), "--format", "dst",
                                         "--out", str(out)]).exit_code == 0
        return sum(1 for _, _, c in pe.read(str(out)).stitches if c & pe.COMMAND_MASK == pe.STITCH)

    def test_fleece_sews_more_than_twill(self, tmp_path):
        # a 3.5 mm column: twill = edge walk, fleece = edge walk + zigzag at tighter spacing
        assert self._stitches(tmp_path, "fleece") > self._stitches(tmp_path, "twill") * 1.1


class TestUnderlayPerShape:
    """The underlay band is decided once per shape, so near-identical columns straddling a width
    threshold (an H's two legs at 2.01 and 1.92 mm) don't get different underlay."""

    @staticmethod
    def _leg(width):
        from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn
        return PlannedColumn(left=[(0.0, 0.0), (0.0, 10.0)], right=[(width, 0.0), (width, 10.0)])

    def test_representative_width_is_median_of_column_medians(self):
        from cli_anything_inkstitch.commands.tools import shape_band_width_mm
        assert shape_band_width_mm([self._leg(2.01), self._leg(1.92)]) == pytest.approx(1.965)
        assert shape_band_width_mm([]) is None

    def test_straddling_legs_share_one_band(self):
        from cli_anything_inkstitch.commands.tools import _lettering_satin_params, shape_band_width_mm
        legs = [self._leg(2.01), self._leg(1.92)]
        # per column (no band width) the legs flip across the 2.0 mm twill threshold
        assert _underlay(_lettering_satin_params([2.01], None, "twill")) == ["contour_underlay"]
        assert _underlay(_lettering_satin_params([1.92], None, "twill")) == ["center_walk_underlay"]
        # with the shape band width both legs agree (median 1.965 < 2.0 -> centre walk)
        bw = shape_band_width_mm(legs)
        assert _underlay(_lettering_satin_params([2.01], None, "twill", bw)) == ["center_walk_underlay"]
        assert _underlay(_lettering_satin_params([1.92], None, "twill", bw)) == ["center_walk_underlay"]

    def test_zigzag_stays_per_column_on_a_mixed_width_shape(self):
        # a shape with a wide column (6.8 mm) and a medium one (3.3 mm): the medium column must not
        # pick up zigzag underlay just because the shape's representative width is wide.
        from cli_anything_inkstitch.commands.tools import _lettering_satin_params, shape_band_width_mm
        cols = [self._leg(6.8), self._leg(3.3)]
        bw = shape_band_width_mm(cols)            # median of medians = 5.05 (wide)
        assert _underlay(_lettering_satin_params([3.3], None, "twill", bw)) == ["contour_underlay"]
        assert _underlay(_lettering_satin_params([6.8], None, "twill", bw)) == ["contour_underlay", "zigzag_underlay"]

    def test_split_satin_stays_per_column(self):
        from cli_anything_inkstitch.commands.tools import _lettering_satin_params
        # a wide column still splits even when the shape band width is thin
        assert _lettering_satin_params([8.0], None, "twill", band_width_mm=1.0)["max_stitch_length_mm"] == "7"


class TestUnderlayRegions:
    """One underlay setting per design region (Bob 11/15, HIDA champ back): a knot's strands are
    separate shapes of one thread; picked per strand, look-alike strands straddling a width
    threshold sewed with and without zigzag and read as different thicknesses."""

    @staticmethod
    def _strand(x0, width, length=10.0):
        from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn, PlannedLetter
        col = PlannedColumn(left=[(x0, 0.0), (x0, length / 2), (x0, length)],
                            right=[(x0 + width, 0.0), (x0 + width, length / 2), (x0 + width, length)])
        outline = [[(x0, 0.0), (x0 + width, 0.0), (x0 + width, length), (x0, length)]]
        return PlannedLetter(char=f"s{x0}", columns=[col], contours=outline, ok=True)

    def test_near_same_thread_shapes_form_one_region(self):
        from cli_anything_inkstitch.commands.tools import underlay_regions
        planned = [self._strand(0, 3.6), self._strand(5, 4.2), self._strand(40, 3.9), self._strand(10, 3.8)]
        threads = ["gold", "gold", "gold", "green"]
        region, width = underlay_regions(planned, threads, [False] * 4)
        assert region[0] == region[1]                 # 1.4 mm apart, same thread
        assert region[2] not in (region[0], None)     # 36 mm away
        assert region[3] not in (region[0], None)     # touching, but another thread
        assert width[region[0]] == pytest.approx(3.9)  # median of the strands: 3.6 and 4.2

    def test_fill_shapes_are_left_out(self):
        from cli_anything_inkstitch.commands.tools import underlay_regions
        planned = [self._strand(0, 3.6), self._strand(5, 4.2)]
        region, _ = underlay_regions(planned, ["gold", "gold"], [False, True])
        assert region[1] is None and region[0] is not None

    def test_region_width_decides_zigzag_for_look_alike_strands(self):
        # standard ladder: zigzag from 4.0 mm. Per strand, 3.6 and 4.2 mm differ; given the
        # region's width both agree.
        assert _underlay(_lettering_satin_params([3.6])) == ["contour_underlay"]
        assert _underlay(_lettering_satin_params([4.2])) == ["contour_underlay", "zigzag_underlay"]
        for w in (3.6, 4.2):
            assert _underlay(_lettering_satin_params([w], zigzag_width_mm=4.1)) == ["contour_underlay",
                                                                                   "zigzag_underlay"]

    def test_knit_zigzags_mid_width_satin(self):
        # knit sinks and stretches: edge walk + zigzag from 2.5 mm (the hand-fixed HIDA v7 had it
        # under every 2-4 mm emblem strand); twill keeps the edge walk alone there
        knit = lambda w: _underlay(_lettering_satin_params([w], None, "knit"))
        assert knit(1.2) == ["center_walk_underlay"]
        assert knit(2.0) == ["contour_underlay"]
        assert knit(2.7) == ["contour_underlay", "zigzag_underlay"]
        assert _underlay(_lettering_satin_params([2.7], None, "twill")) == ["contour_underlay"]


class TestPointedEnds:
    """A zigzag-underlaid satin sewn to a point stacks underlay, top layer and lock on one spot
    (HIDA knot points 56/mm2): knit ends it where it's still end_min_mm wide."""

    @staticmethod
    def _taper():
        from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn
        # 0.2 mm wide at y=0, widening 1 mm per mm of length, 3 mm wide from y=3 on
        ys = [0.0, 0.5, 1.0, 2.0, 3.0, 6.0, 9.0]
        w = [min(3.0, 0.2 + y) for y in ys]
        return PlannedColumn(left=[(-wi / 2, y) for wi, y in zip(w, ys)],
                             right=[(wi / 2, y) for wi, y in zip(w, ys)])

    def test_point_cut_back_to_min_width(self):
        from cli_anything_inkstitch.trace.glyph_columns import end_at_min_width
        c = self._taper()
        assert end_at_min_width(c, 0.8, 0.6) == (True, False)    # the far end is 3 mm wide
        assert c.widths()[0] == pytest.approx(0.8)
        assert c.left[0][1] == pytest.approx(0.6)                # 0.6 mm along: where it is 0.8 wide
        assert c.left[-1] == (-1.5, 9.0)

    def test_cut_is_capped_along_the_column(self):
        from cli_anything_inkstitch.trace.glyph_columns import end_at_min_width
        c = self._taper()
        end_at_min_width(c, 2.0, 0.6)                            # 2 mm wide is 1.8 mm in: capped
        assert c.left[0][1] == pytest.approx(0.6)
        assert c.widths()[0] == pytest.approx(0.8)

    def test_square_and_closed_ends_untouched(self):
        from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn, end_at_min_width
        sq = PlannedColumn(left=[(0, 0), (0, 5), (0, 10)], right=[(2, 0), (2, 5), (2, 10)])
        assert end_at_min_width(sq, 0.8, 0.6) == (False, False)
        ring = self._taper()
        ring.closed = True
        assert end_at_min_width(ring, 0.8, 0.6) == (False, False)

    def test_only_knit_cuts_points(self):
        assert FABRICS["knit"]["end_min_mm"] == 0.8
        assert FABRICS["standard"]["end_min_mm"] is None and FABRICS["twill"]["end_min_mm"] is None


class TestSewOrderByFabric:
    """Sew order is a property of what's being stitched on (Thready / Bob 11/15)."""

    def test_stable_fabric_sews_by_thread_stretch_outward(self):
        assert FABRICS["standard"]["order"] == FABRICS["twill"]["order"] == "thread"
        assert FABRICS["knit"]["order"] == FABRICS["fleece"]["order"] == "centre_out"

    def test_cap_sews_from_the_bottom_centre_with_firmer_settings(self):
        cap = FABRICS["cap"]
        assert cap["order"] == "bottom_out"
        assert cap["pull_comp_mm"] > FABRICS["twill"]["pull_comp_mm"]
        assert _underlay(_lettering_satin_params([2.7], None, "cap")) == ["contour_underlay", "zigzag_underlay"]

    def test_outward_orientation_starts_at_the_end_nearer_the_origin(self):
        from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn, PlannedLetter, plan_one_pass
        strand = lambda: PlannedLetter(char="s", ok=True, contours=[[(10, 0), (30, 0), (30, 2), (10, 2)]],
                                       columns=[PlannedColumn(left=[(30, 0), (20, 0), (10, 0)],
                                                              right=[(30, 2), (20, 2), (10, 2)])])
        lt = strand()                                   # drawn from x=30 inward
        plan_one_pass(lt, arrival=(31, 1), outward_from=(0, 1))
        assert lt.columns[0].left[0][0] == 10           # now starts at the inner end
        lt = strand()
        plan_one_pass(lt, arrival=(31, 1))              # without an origin: nearest the needle
        assert lt.columns[0].left[0][0] == 30


class TestEdgeWalkInset:
    """Stretch fabric: the edge walk sits ~1 mm in, never past 25 % per side (Bob 11/13: on a
    hoodie a 0.4 mm-in edge walk peeked out past the satin)."""

    def test_knit_insets_a_quarter_up_to_one_mm(self):
        narrow = _lettering_satin_params([3.0], None, "knit")
        assert (narrow["contour_underlay_inset_mm"], narrow["contour_underlay_inset_percent"]) == ("0", "25")
        wide = _lettering_satin_params([7.0], None, "knit")
        assert (wide["contour_underlay_inset_mm"], wide["contour_underlay_inset_percent"]) == ("1", "0")
        assert FABRICS["fleece"]["contour_inset_mm"] == 1.0

    def test_stable_fabric_keeps_the_engine_default(self):
        for fabric in ("standard", "twill", "cap"):
            p = _lettering_satin_params([3.0], None, fabric)
            assert "contour_underlay_inset_mm" not in p and "contour_underlay_inset_percent" not in p

    def test_centre_walk_columns_have_no_inset(self):
        assert "contour_underlay_inset_mm" not in _lettering_satin_params([1.0], None, "knit")


class TestTransformsApplied:
    def test_path_transform_scales_the_contours(self):
        # an EPS export: every path carries matrix(.1,0,0,-.1,0,H) over 10x coordinates
        from lxml import etree
        from cli_anything_inkstitch.commands.tools import _path_contours_mm
        svg = etree.fromstring(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100mm" height="100mm">'
            '<g transform="translate(5,0)"><path transform="matrix(.1,0,0,-.1,0,100)" '
            'd="M 0 0 L 200 0 L 200 300 L 0 300 Z"/></g></svg>')
        el = svg[0][0]
        cs = _path_contours_mm(el, 1.0)
        xs = [p[0] for c in cs for p in c]
        ys = [p[1] for c in cs for p in c]
        assert max(xs) - min(xs) == pytest.approx(20, abs=0.01)     # 200 * 0.1, not 200
        assert max(ys) - min(ys) == pytest.approx(30, abs=0.01)
        assert min(xs) == pytest.approx(5, abs=0.01)                 # the group's translate too


class TestThinColumns:
    """Too-thin columns are an ART problem at this size (Thready / Bob 11/15): the planner warns,
    it doesn't widen or re-stitch."""

    @staticmethod
    def _letter(cols):
        from cli_anything_inkstitch.trace.glyph_columns import PlannedLetter
        return PlannedLetter(char="x", columns=cols, contours=[], ok=True, check={})

    @staticmethod
    def _col(width, length, x=0.0):
        from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn
        ys = [0.0, length / 2, length]
        return PlannedColumn(left=[(x - width / 2, y) for y in ys], right=[(x + width / 2, y) for y in ys])

    def test_hairline_and_small_stroke_warned_not_changed(self):
        from cli_anything_inkstitch.trace.glyph_columns import thin_column_warnings
        lt = self._letter([self._col(0.43, 30.0), self._col(0.5, 3.5, x=2.0), self._col(1.1, 8.0, x=5.0)])
        w = thin_column_warnings(lt)
        assert [(t["column"], t["hint"]) for t in w] == [(0, "hairline"), (1, "small stroke")]
        assert [round(min(c.widths()), 2) for c in lt.columns] == [0.43, 0.5, 1.1]   # untouched

    def test_small_artifact_floor_stops_at_a_missing_stroke(self):
        from cli_anything_inkstitch.planner.art_columns import flat_ok
        base = {"outside_pct": 0.0, "crossed_pairs": 0, "overlap_mm2": 0.0}
        assert flat_ok({**base, "sewable_bare_pct": 8.3, "sewable_bare_mm2": 0.8})      # IRISH I corner
        assert not flat_ok({**base, "sewable_bare_pct": 54.0, "sewable_bare_mm2": 1.1})  # half an L bare


class TestTipTrimInterpolates:
    def test_straight_taper_keeps_its_length(self):
        """A straight tapering stroke has few pairs; trimming its 0.14 mm tip must stop where the
        rails reach the minimum width, not drop the whole last span (North Stars N, 11/15)."""
        from cli_anything_inkstitch.planner import columns as col
        from cli_anything_inkstitch.planner.art_columns import MIN_TIP_MM, _width, trim_tips
        pairs = [((-4.0, 0.0), (4.0, 0.0)), ((-3.7, 1.0), (3.7, 1.0)), ((-3.4, 2.0), (3.4, 2.0)),
                 ((-0.07, 30.0), (0.07, 30.0))]
        c = trim_tips(col.Column(pairs=pairs, curve=[False] * 4, closed=False))
        assert len(c.pairs) == 4
        assert _width(c.pairs[-1]) == pytest.approx(MIN_TIP_MM)
        assert c.pairs[-1][0][1] == pytest.approx(28.9, abs=0.05)   # 1.1 mm short of the tip, not 28 mm


class TestThinReporting:
    """Thready 11/15 on b6d64b6: one warning line per shape, a design-level fix line, and no silent
    fill fallback for a thin shape (satin + warning) or a hairline (bean stitch)."""

    @staticmethod
    def _col(width, length, x=0.0):
        from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn
        ys = [0.0, length / 2, length]
        return PlannedColumn(left=[(x - width / 2, y) for y in ys], right=[(x + width / 2, y) for y in ys])

    def _letter(self, cols, ok=True, contours=None):
        from cli_anything_inkstitch.trace.glyph_columns import PlannedLetter, thin_column_warnings
        lt = PlannedLetter(char="x", columns=cols, contours=contours or [], ok=ok, check={"sewable_bare_pct": 16.0})
        lt.check["thin_columns"] = thin_column_warnings(lt)
        return lt

    def test_one_line_per_shape(self):
        from cli_anything_inkstitch.commands.tools import _shape_warning
        lt = self._letter([self._col(0.52, 3.0), self._col(0.62, 3.0, 1), self._col(0.55, 3.0, 2)])
        w = _shape_warning(lt, lt.check, False, None)
        assert w.count("\n") == 0 and w.startswith("3 columns 0.52-0.62 mm wide, under 0.8 mm")

    def test_thin_failed_shape_stays_satin_with_a_note(self):
        from cli_anything_inkstitch.commands.tools import _sews_as_fill, _shape_warning
        lt = self._letter([self._col(0.48, 3.0), self._col(0.48, 1.0, 1)], ok=False)
        assert _sews_as_fill(lt, {}, "fill") is False
        assert "kept as satin" in _shape_warning(lt, lt.check, False, None)
        wide = self._letter([self._col(3.0, 10.0)], ok=False)
        assert _sews_as_fill(wide, {}, "fill") is True           # a wide failed shape still falls back

    def test_unplannable_hairline_sews_as_a_run(self):
        from cli_anything_inkstitch.commands.tools import _hairline_run
        from cli_anything_inkstitch.trace.glyph_columns import PlannedLetter
        sliver = [[(0.0, 0.0), (30.0, 0.0), (30.0, 0.3), (0.0, 0.3)]]
        lt = PlannedLetter(char="h", columns=[], contours=sliver, ok=False, check={})
        line = _hairline_run(lt, {})
        assert line and abs(line[len(line) // 2][1] - 0.15) < 0.02          # on the sliver's centre
        assert abs(abs(line[-1][0] - line[0][0]) - 30.0) < 0.6
        blob = PlannedLetter(char="b", columns=[], contours=[[(0, 0), (5, 0), (5, 5), (0, 5)]], ok=False, check={})
        assert _hairline_run(blob, {}) is None

    def test_design_warning_says_how_much_to_enlarge(self):
        from shapely.geometry import box
        from cli_anything_inkstitch.commands.tools import _design_warnings
        lts = [self._letter([self._col(0.5, 3.0)]), self._letter([self._col(0.5, 3.0, 5)])]
        dw = _design_warnings(lts, [False, False], [None, None], [box(0, 0, 70, 10)])
        assert dw == ["small strokes ~0.50 mm wide in 2 shapes: enlarge the design about 1.6x (to ~112 mm wide) "
                      "or thicken the art to 0.8 mm"]


class TestEveryHairlineRuns:
    def test_a_hairline_that_planned_still_sews_as_a_run(self):
        """elem_16/17 on North Stars: 0.43 mm arcs whose satin plan passed stayed satin with advice
        only; the planner knows the answer, so it applies it (Thready 11/15)."""
        from cli_anything_inkstitch.commands.tools import _hairline_run
        from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn, PlannedLetter
        sliver = [[(0.0, 0.0), (30.0, 0.0), (30.0, 0.43), (0.0, 0.43)]]
        col = PlannedColumn(left=[(0.0, 0.0), (15.0, 0.0), (30.0, 0.0)], right=[(0.0, 0.43), (15.0, 0.43), (30.0, 0.43)])
        lt = PlannedLetter(char="h", columns=[col], contours=sliver, ok=True, check={})
        assert _hairline_run(lt, {}) is not None
        assert _hairline_run(lt, {"stitch": "satin"}) is None       # an override wins

    def test_no_warning_just_under_the_minimum(self):
        from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn, PlannedLetter, thin_column_warnings
        ys = [0.0, 2.0, 4.0]
        mk = lambda w: PlannedColumn(left=[(0, y) for y in ys], right=[(w, y) for y in ys])
        lt = PlannedLetter(char="x", columns=[mk(0.77), mk(0.6)], contours=[], ok=True, check={})
        assert [t["column"] for t in thin_column_warnings(lt)] == [1]
