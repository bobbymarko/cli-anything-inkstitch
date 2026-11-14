"""TTF text → per-stroke satin columns (`tools text-to-satin`).

Why: rung-based fill-to-satin broke letters at junctions (the 6's wedge,
EST. 2018's E/T/1/8 in the 2026-11-08 head-to-head). The planner
(trace/glyph_columns.py, the column planner in planner/) pairs rails by
construction and writes old-style two-rail satins.

Pure tests pin the two contracts this depends on: old-style satins keep
their node pairing through rail simplification, and the scale guard
ignores rung overshoot. Planner tests need the `lettering` extra and a
system TTF. The binary-backed tests are the behavioural proof (CLAUDE.md
rule 3): the engine sews what was planned, at the planned size, and
routing changes the plan only where it removes travel over bare fabric.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.svg.units import art_bbox, satin_rails_d
from cli_anything_inkstitch.trace.satinize import simplify_satin_d

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
FONT = next((p for p in FONT_CANDIDATES if Path(p).exists()), None)

try:
    import cli_anything_inkstitch.planner.art_columns  # noqa: F401
    import scipy.spatial  # noqa: F401
    import shapely  # noqa: F401
    HAVE_PLANNER = True
except ImportError:
    HAVE_PLANNER = False

needs_planner = pytest.mark.skipif(not (HAVE_PLANNER and FONT),
                                   reason="needs the lettering extra and a TTF")
needs_binary = pytest.mark.skipif(discover() is None,
                                  reason="Ink/Stitch binary not installed")


# ---------------------------------------------------------------- pure contracts

class TestRouteWalk:
    """The shared start_at_nearest_point sew-walk behind both exposed_hops and
    the artifact /route endpoint: per column, where it enters and exits."""

    def _cols(self):
        # three horizontal bands; the middle one placed to the right of the
        # first, the third drawn right-to-left so its NEAR end is its index -1
        from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn
        a = PlannedColumn(left=[(0, 0), (10, 0)], right=[(0, 2), (10, 2)])
        b = PlannedColumn(left=[(12, 0), (22, 0)], right=[(12, 2), (22, 2)])
        c = PlannedColumn(left=[(34, 0), (24, 0)], right=[(34, 2), (24, 2)])
        return [a, b, c]

    def test_entry_exit_follow_nearest_end(self):
        from cli_anything_inkstitch.trace.glyph_columns import column_ends, route_walk
        cols = self._cols()
        ends = [column_ends(c.left, c.right) for c in cols]
        recs = route_walk(ends)
        # column 0 is sewn end_a -> end_b by definition
        assert recs[0]["entry"] == (0.0, 1.0)
        assert recs[0]["exit"] == (10.0, 1.0)
        assert recs[0]["is_jump_before"] is False
        # column 1 enters at the end nearer the previous exit (10,1): (12,1)
        assert recs[1]["entry"] == (12.0, 1.0)
        assert recs[1]["exit"] == (22.0, 1.0)
        # column 2 is drawn reversed; its near end to (22,1) is its index -1
        assert recs[2]["entry"] == (24.0, 1.0)
        assert recs[2]["exit"] == (34.0, 1.0)

    def test_order_preserved_and_one_record_per_column(self):
        from cli_anything_inkstitch.trace.glyph_columns import column_ends, route_walk
        cols = self._cols()
        recs = route_walk([column_ends(c.left, c.right) for c in cols])
        assert [r["order"] for r in recs] == [0, 1, 2]

    def test_no_covered_means_no_jumps(self):
        from cli_anything_inkstitch.trace.glyph_columns import column_ends, route_walk
        cols = self._cols()
        recs = route_walk([column_ends(c.left, c.right) for c in cols], covered=None)
        assert all(r["is_jump_before"] is False for r in recs)
        assert all(r["exposed"] == 0.0 for r in recs)

    def test_bare_travel_is_a_jump_covered_travel_is_not(self):
        shapely = pytest.importorskip("shapely")
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        from cli_anything_inkstitch.trace.glyph_columns import (
            PlannedColumn, column_ends, route_walk)
        # two bands with a wide bare gap between them: the travel across the gap
        # is a jump; a third band abutting the second gives a covered travel
        a = PlannedColumn(left=[(0, 0), (0, 10)], right=[(2, 0), (2, 10)])
        b = PlannedColumn(left=[(20, 10), (20, 0)], right=[(22, 10), (22, 0)])
        c = PlannedColumn(left=[(20, 0), (20, -10)], right=[(22, 0), (22, -10)])
        cols = [a, b, c]
        covered = unary_union([Polygon(col.left + col.right[::-1]) for col in cols])
        recs = route_walk([column_ends(col.left, col.right) for col in cols], covered)
        assert recs[1]["is_jump_before"] is True        # across the bare gap
        assert recs[1]["exposed"] > 0.0
        assert recs[2]["is_jump_before"] is False        # b -> c stays in thread

    def test_exposed_hops_reads_the_same_walk(self):
        """exposed_hops must agree with route_walk — one source of truth."""
        pytest.importorskip("shapely")
        from cli_anything_inkstitch.trace.glyph_columns import (
            PlannedColumn, PlannedLetter, column_ends, exposed_hops, glyph_polygon,
            route_walk)
        a = PlannedColumn(left=[(0, 0), (0, 10)], right=[(2, 0), (2, 10)])
        b = PlannedColumn(left=[(20, 10), (20, 0)], right=[(22, 10), (22, 0)])
        contours = [[(0, 0), (2, 0), (2, 10), (0, 10)],
                    [(20, 0), (22, 0), (22, 10), (20, 10)]]
        letter = PlannedLetter("x", [a, b], contours, True)
        hops = dict(exposed_hops(letter))
        glyph = glyph_polygon(contours).buffer(0.05)
        recs = route_walk([column_ends(c.left, c.right) for c in [a, b]], glyph)
        assert hops == {r["order"] - 1: r["exposed"]
                        for r in recs[1:] if r["exposed"] > 0.0}
        assert hops          # there IS an exposed hop across the gap


class TestRouteWalkAsDrawn:
    """end_at_nearest_point off: the engine sews a column end_a -> end_b as drawn
    (lib/elements/satin_column.py end_point / _split_top_layer), so the walk must not pick the
    nearer end; with start_at_nearest_point on the needle lands on the nearest centre-line point
    and runs back under the column to end_a."""

    def _cols(self):
        from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn
        a = PlannedColumn(left=[(0, 0), (10, 0)], right=[(0, 2), (10, 2)])
        # drawn away from a: its end_a is the FAR end (24, 1)
        b = PlannedColumn(left=[(24, 0), (12, 0)], right=[(24, 2), (12, 2)])
        return [a, b]

    def test_exit_is_end_b_even_when_the_other_end_is_nearer(self):
        from cli_anything_inkstitch.trace.glyph_columns import column_ends, route_walk
        cols = self._cols()
        recs = route_walk([column_ends(c.left, c.right) for c in cols], as_drawn=True)
        assert recs[1]["entry"] == (24.0, 1.0) and recs[1]["exit"] == (12.0, 1.0)
        assert recs[1]["arrive"] == (24.0, 1.0) and recs[1]["travel"] == pytest.approx(14.0)

    def test_start_at_nearest_lands_on_the_centre_line_with_a_hidden_run(self):
        pytest.importorskip("shapely")
        from cli_anything_inkstitch.trace.glyph_columns import centre_line, column_ends, route_walk
        cols = self._cols()
        recs = route_walk([column_ends(c.left, c.right) for c in cols], as_drawn=True,
                          centre_lines=[centre_line(c.left, c.right) for c in cols])
        assert recs[1]["arrive"] == pytest.approx((12.0, 1.0))
        assert recs[1]["travel"] == pytest.approx(2.0)
        assert recs[1]["hidden_run"] == pytest.approx(12.0)   # back along the column to (24, 1)

    def test_exact_start_lands_on_end_a(self):
        pytest.importorskip("shapely")
        from cli_anything_inkstitch.trace.glyph_columns import centre_line, column_ends, route_walk
        cols = self._cols()
        recs = route_walk([column_ends(c.left, c.right) for c in cols], as_drawn=True,
                          centre_lines=[centre_line(c.left, c.right) for c in cols], exact={1})
        assert recs[1]["arrive"] == (24.0, 1.0) and recs[1]["hidden_run"] == 0.0

    def test_start_gives_column_zero_its_travel(self):
        from cli_anything_inkstitch.trace.glyph_columns import column_ends, route_walk
        cols = self._cols()
        recs = route_walk([column_ends(c.left, c.right) for c in cols], as_drawn=True, start=(0.0, 5.0))
        assert recs[0]["travel"] == pytest.approx(4.0)

    def test_default_model_is_unchanged(self):
        from cli_anything_inkstitch.trace.glyph_columns import column_ends, route_walk
        cols = self._cols()
        recs = route_walk([column_ends(c.left, c.right) for c in cols])
        assert recs[1]["entry"] == (12.0, 1.0) and recs[1]["exit"] == (24.0, 1.0)


def _bar(x0, y0, x1, y1, w=2.0):
    """A straight column from (x0, y0) to (x1, y1), w wide, two pairs."""
    from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy)
    ox, oy = -dy / n * w / 2, dx / n * w / 2
    return PlannedColumn(left=[(x0 + ox, y0 + oy), (x1 + ox, y1 + oy)],
                         right=[(x0 - ox, y0 - oy), (x1 - ox, y1 - oy)])


def _letter(cols):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from cli_anything_inkstitch.trace.glyph_columns import PlannedLetter
    u = unary_union([Polygon(c.left + c.right[::-1]) for c in cols])
    polys = list(getattr(u, "geoms", [u]))
    return PlannedLetter("x", cols, [list(p.exterior.coords)[:-1] for p in polys], True)


class TestPlanOnePass:
    """One pass per satin: each column finishes nearest the next, trims where the handoff would
    jump (> collapse_len) or cross bare fabric, exact starts after a trim."""

    def test_column_is_turned_to_finish_at_the_next_one(self):
        pytest.importorskip("shapely")
        from cli_anything_inkstitch.trace.glyph_columns import column_ends, plan_one_pass
        # an L: the stem is drawn from its corner end upward, so as drawn it would finish far from
        # the foot; it must be reversed to finish at the corner
        stem = _bar(1, 0, 1, 20)
        foot = _bar(0, 1, 12, 1)
        plan = plan_one_pass(_letter([stem, foot]))
        assert 0 in plan["reversed"]
        assert column_ends(stem.left, stem.right)[1][1] < 2.0       # exit at the corner
        assert plan["trim_after"] == []                                # covered 1 mm handoff
        assert plan["start_exact"] == [0]

    def test_already_good_order_is_left_alone(self):
        pytest.importorskip("shapely")
        from cli_anything_inkstitch.trace.glyph_columns import plan_one_pass
        stem = _bar(1, 20, 1, 0)
        foot = _bar(0, 1, 12, 1)
        before = (list(stem.left), list(stem.right))
        plan = plan_one_pass(_letter([stem, foot]))
        assert 0 not in plan["reversed"] and (stem.left, stem.right) == before

    def test_long_handoff_is_trimmed_and_the_next_column_starts_exactly(self):
        pytest.importorskip("shapely")
        from cli_anything_inkstitch.trace.glyph_columns import plan_one_pass
        # two separate bars 10 mm apart: the handoff is a jump over bare fabric
        a, b = _bar(0, 0, 20, 0), _bar(0, 10, 20, 10)
        plan = plan_one_pass(_letter([a, b]))
        assert plan["trim_after"] == [0]
        assert plan["start_exact"] == [0, 1]

    def test_covered_handoff_over_collapse_len_is_trimmed(self):
        pytest.importorskip("shapely")
        from cli_anything_inkstitch.trace.glyph_columns import plan_one_pass
        # a T: the bar ends at its right, the stem leaves from the bar's middle, 10 mm back along
        # covered thread -- no bare fabric, but longer than collapse_len, so the engine would jump
        bar = _bar(0, 20, 20, 20)
        stem = _bar(10, 19, 10, 0)
        plan = plan_one_pass(_letter([bar, stem]))
        assert plan["trim_after"] == [0]
        plan_off = plan_one_pass(_letter([_bar(0, 20, 20, 20), _bar(10, 19, 10, 0)]), trim_handoffs=False)
        assert plan_off["trim_after"] == []

    def test_handoff_is_measured_from_the_rail_end_not_the_centre(self):
        pytest.importorskip("shapely")
        from cli_anything_inkstitch.trace.glyph_columns import plan_one_pass
        # a 6 mm wide bar ending at x = 10, then a post whose centre line starts 1.5 mm from the bar's
        # centre-line end, but 3.35 mm from its top rail corner, where the last stitch can land
        wide = _bar(0, 3, 10, 3, w=6.0)
        post = _bar(11.5, 3, 11.5, -10, w=2.0)
        plan = plan_one_pass(_letter([wide, post]))
        assert plan["trim_after"] == [0]

    def test_trimmed_column_starts_nearest_its_arrival(self):
        pytest.importorskip("shapely")
        from cli_anything_inkstitch.trace.glyph_columns import column_ends, plan_one_pass
        # one column, the needle arrives near its END: it is turned to start there
        bar = _bar(0, 0, 20, 0)
        plan = plan_one_pass(_letter([bar]), arrival=(22.0, 0.0))
        assert plan["reversed"] == [0]
        assert column_ends(bar.left, bar.right)[0][0] == pytest.approx(20.0)
        assert plan["exit"][0] == pytest.approx(0.0)

    def test_ring_is_never_reversed(self):
        pytest.importorskip("shapely")
        from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn, plan_one_pass
        n = 24
        ring = PlannedColumn(left=[(10 * math.cos(2 * math.pi * i / n), 10 * math.sin(2 * math.pi * i / n)) for i in range(n + 1)],
                             right=[(8 * math.cos(2 * math.pi * i / n), 8 * math.sin(2 * math.pi * i / n)) for i in range(n + 1)],
                             closed=True)
        tail = _bar(9, 0, 30, 0)
        plan = plan_one_pass(_letter([ring, tail]), arrival=(50.0, 0.0))
        assert 0 not in plan["reversed"]


class TestOldStyleSatinSurvivesSimplify:
    def test_two_subpath_satin_passes_through(self):
        """Engine pairs a rung-less satin's rails node for node
        (satin_column.py _synthesize_rungs). Simplifying the rails apart
        would re-pair them: an S came out twisted at 18 vs 17 nodes."""
        a = [(x, 0.0 + 0.001 * x * x) for x in range(0, 40, 2)]
        b = [(x, 5.0 + 0.001 * x * x) for x in range(0, 40, 2)]
        d = "M" + " ".join(f"{x},{y}" for x, y in a) + " M" + " ".join(f"{x},{y}" for x, y in b)
        out, nb, na = simplify_satin_d(d)
        assert out == d and nb == na == 0


class TestScaleGuardIgnoresRungs:
    def _tree(self, d):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
               f'xmlns:inkstitch="http://inkstitch.org/namespace">'
               f'<path id="s" inkstitch:satin_column="True" d="{d}"/></svg>')
        return etree.ElementTree(etree.fromstring(svg))

    def test_rails_chosen_like_the_engine(self):
        rails = "M 0,0 L 100,0 M 0,10 L 100,10"
        rungs = " M 20,-3 L 20,13 M 50,-3 L 50,13 M 80,-3 L 80,13"
        assert satin_rails_d(rails) is None              # nothing to drop
        assert satin_rails_d(rails + rungs).split() == rails.split()

    def test_bbox_is_the_rails_not_the_rung_overshoot(self):
        d = "M 0,0 L 100,0 M 0,10 L 100,10 M 20,-3 L 20,13 M 50,-3 L 50,13 M 80,-3 L 80,13"
        x0, y0, x1, y1 = art_bbox(self._tree(d))
        assert (y0, y1) == (0.0, 10.0)


# ---------------------------------------------------------------- planner

@needs_planner
class TestPlanner:
    def test_E_is_four_columns_with_paired_rails(self):
        from cli_anything_inkstitch.trace.glyph_columns import plan_text
        (e,) = plan_text(FONT, "E", 20.0)
        assert e.ok, e.check
        assert len(e.columns) >= 3        # stem + arms (DejaVu's E: 4)
        for c in e.columns:
            assert len(c.left) == len(c.right) >= 2
            assert min(c.widths()) > 0.3

    def test_exposed_travel_flags_E_not_I(self):
        from cli_anything_inkstitch.trace.glyph_columns import (
            EXPOSED_TRAVEL_MM, exposed_travel_mm, plan_text)
        e, i = plan_text(FONT, "EI", 20.0)
        assert exposed_travel_mm(e) > EXPOSED_TRAVEL_MM
        assert exposed_travel_mm(i) == 0.0

    def test_width_mm_fits_the_ink(self):
        from cli_anything_inkstitch.trace.glyph_columns import plan_text
        letters = plan_text(FONT, "HIT", 10.0, width_mm=60.0)
        xs = [p[0] for lt in letters for c in lt.contours for p in c]
        assert abs((max(xs) - min(xs)) - 60.0) < 0.05


# ---------------------------------------------------------------- engine-backed

def _project(tmp_path, w_mm=80, h_mm=40):
    k = 96 / 25.4
    svg = tmp_path / "t.svg"
    svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" '
                   f'xmlns:inkstitch="http://inkstitch.org/namespace" '
                   f'width="{w_mm}mm" height="{h_mm}mm" '
                   f'viewBox="0 0 {w_mm * k:.4f} {h_mm * k:.4f}"></svg>')
    proj = tmp_path / "t.json"
    r = CliRunner().invoke(root, ["document", "open", "--project", str(proj), "--svg", str(svg)])
    assert r.exit_code == 0, r.output
    return proj


def _export(proj, tmp_path, name):
    out = tmp_path / f"{name}.dst"
    r = CliRunner().invoke(root, ["export", "file", "--project", str(proj),
                                  "--format", "dst", "--out", str(out)])
    assert r.exit_code == 0, r.output
    import pyembroidery as pe
    return pe.read(str(out))


def _stitches(pattern, with_jumps=False):
    """Needle positions in mm. with_jumps: also untrimmed JUMP moves, which
    leave thread on top just like a stitch (filtering them out makes the
    positions either side look adjacent, CLAUDE.md rule 4a)."""
    import pyembroidery as pe
    keep = {pe.STITCH, pe.JUMP} if with_jumps else {pe.STITCH}
    return [(x / 10, y / 10) for x, y, c in pattern.stitches
            if (c & pe.COMMAND_MASK) in keep]


@needs_planner
@needs_binary
class TestEngineSewsThePlan:
    def test_size_matches_the_plan(self, tmp_path):
        """The engine read the satins at the planned scale: stitch extent
        equals the letters' ink box within pull compensation."""
        from cli_anything_inkstitch.trace.glyph_columns import plan_text
        proj = _project(tmp_path)
        r = CliRunner().invoke(root, ["--json", "tools", "text-to-satin", "--project", str(proj),
                                      "--font", FONT, "--text", "HI", "--height-mm", "15"])
        assert r.exit_code == 0, r.output
        pts = _stitches(_export(proj, tmp_path, "hi"))
        letters = plan_text(FONT, "HI", 15.0)
        xs = [p[0] for lt in letters for c in lt.contours for p in c]
        ys = [p[1] for lt in letters for c in lt.contours for p in c]
        w = max(x for x, _ in pts) - min(x for x, _ in pts)
        h = max(y for _, y in pts) - min(y for _, y in pts)
        assert abs(w - (max(xs) - min(xs))) < 1.0
        assert abs(h - (max(ys) - min(ys))) < 1.0

    def test_routed_E_keeps_every_move_on_the_letter(self, tmp_path):
        """Routing re-orders and cuts the E's columns (auto_satin), and the
        result must not lay any stitch or untrimmed jump longer than 1 mm
        across bare fabric. The measurement is checked first against a
        control whose answer is known (a move across the upper counter),
        CLAUDE.md rule 4a. The unrouted failure this guards against was
        measured on Lato Bold 10.2 mm (top arm to middle arm across the
        counter, h2h LETTERING.md); DejaVu's E happens to sew clean either
        way, so no unrouted assertion is made on it."""
        from shapely.geometry import Point, Polygon
        from cli_anything_inkstitch.trace.glyph_columns import plan_text

        (e,) = plan_text(FONT, "E", 20.0)
        glyph = Polygon(e.contours[0]).buffer(0.3)

        def off_letter(pts):
            n = 0
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                if math.dist((x0, y0), (x1, y1)) < 1.0:
                    continue
                mx, my = (x0 + x1) / 2, (y0 + y1) / 2
                if not glyph.contains(Point(mx, -my)):   # DST y down, plan y up
                    n += 1
            return n

        # control: top-arm tip straight down to the middle-arm tip crosses
        # the counter (DST coordinates, y down)
        x0, y0, x1, y1 = Polygon(e.contours[0]).bounds
        tip = x1 - 0.5
        assert off_letter([(tip, -(y1 - 0.5)), (tip, -(y0 + y1) / 2)]) == 1

        proj = _project(tmp_path)
        r = CliRunner().invoke(root, ["--json", "tools", "text-to-satin", "--project", str(proj),
                                      "--font", FONT, "--text", "E", "--height-mm", "20"])
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["routed_letters"] == 1
        # DST is centred on the design, like the plan (0, 0)
        pts = _stitches(_export(proj, tmp_path, "e"), with_jumps=True)
        assert off_letter(pts) == 0


# ---------------------------------------------------------------- artwork shapes (tools shapes-to-satin)
# Why: a logo's knots, crown, heart and outlined letters are filled shapes, not font text. The same
# planner (planner/art_columns.plan_shape) digitizes them stroke by stroke; a client chest logo
# head-to-head (2026-11-10) planned all 17 of its shapes as satin this way.

def _ring_d(cx, cy, r_out, r_in, n=48):
    out = [(cx + r_out * math.cos(2 * math.pi * i / n), cy + r_out * math.sin(2 * math.pi * i / n)) for i in range(n)]
    inn = [(cx + r_in * math.cos(-2 * math.pi * i / n), cy + r_in * math.sin(-2 * math.pi * i / n)) for i in range(n)]
    f = lambda pts: "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in pts) + " Z"
    return f(out) + " " + f(inn)


@needs_planner
class TestPlanShapes:
    def test_ring_is_satin_and_a_bar_too(self):
        from cli_anything_inkstitch.trace.glyph_columns import plan_shapes
        n = 48
        ring = [[(10 * math.cos(2 * math.pi * i / n), 10 * math.sin(2 * math.pi * i / n)) for i in range(n)],
                [(7 * math.cos(-2 * math.pi * i / n), 7 * math.sin(-2 * math.pi * i / n)) for i in range(n)]]
        bar = [[(0, 0), (30, 0), (30, 3), (0, 3)]]
        r, b = plan_shapes([("ring", ring), ("bar", bar)])
        assert r.ok and r.columns and b.ok and len(b.columns) == 1
        w = sorted(b.columns[0].widths())
        assert abs(w[len(w) // 2] - 3.0) < 0.3


@needs_planner
@needs_binary
class TestShapesToSatinEngine:
    def test_transformed_ring_keeps_size_place_and_colour(self, tmp_path):
        """A filled ring inside a translated, scaled group: the satins replace it at its
        drawn size (transforms applied), in its colour, with nothing left as fill."""
        k = 96 / 25.4
        svg = tmp_path / "t.svg"
        d = _ring_d(0, 0, 10 * k, 7 * k)
        svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkstitch="http://inkstitch.org/namespace" '
                       f'width="80mm" height="40mm" viewBox="0 0 {80 * k:.4f} {40 * k:.4f}">'
                       f'<g transform="translate({40 * k:.4f},{20 * k:.4f}) scale(1.5)">'
                       f'<path id="ring" d="{d}" style="fill:#ffdd00;fill-rule:evenodd;stroke:none"/></g></svg>')
        proj = tmp_path / "t.json"
        r = CliRunner().invoke(root, ["document", "open", "--project", str(proj), "--svg", str(svg)])
        assert r.exit_code == 0, r.output
        r = CliRunner().invoke(root, ["--json", "tools", "shapes-to-satin", "--project", str(proj)])
        assert r.exit_code == 0, r.output
        out = json.loads(r.output)
        assert out["failed_plans"] == [] and out["shapes"][0]["stitch"] == "satin"
        tree = etree.parse(str(svg))
        paths = tree.getroot().findall(".//{http://www.w3.org/2000/svg}path")
        assert paths and all("fill:none" in (p.get("style") or "") and "#ffdd00" in p.get("style") for p in paths)
        pts = _stitches(_export(proj, tmp_path, "ring"))
        w = max(x for x, _ in pts) - min(x for x, _ in pts)
        assert abs(w - 30.0) < 1.2          # 20 mm ring x scale(1.5), within pull compensation


def _bar_and_post_svg(k=96 / 25.4):
    """A 40 x 4 mm bar and, below its middle, a separate 3 mm post: the bar's nearest point to
    the post is mid-column, which is where end_at_nearest_point splits it."""
    bar = f"M {5 * k},{5 * k} L {45 * k},{5 * k} L {45 * k},{9 * k} L {5 * k},{9 * k} Z"
    post = f"M {23.5 * k},{15 * k} L {26.5 * k},{15 * k} L {26.5 * k},{35 * k} L {23.5 * k},{35 * k} Z"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="40mm" '
            f'viewBox="0 0 {50 * k:.4f} {40 * k:.4f}"><path id="bar" d="{bar}" style="fill:#2e7d32"/>'
            f'<path id="post" d="{post}" style="fill:#2e7d32"/></svg>')


def _top_layer_runs(pattern, min_dy_mm=3.0):
    """Passes of the bar's top layer: its zigzag is the only stitching that crosses ~4 mm in y
    (the post zigzags in x, underlay runs along x). Consecutive crossings advancing < 1.5 mm in x
    are one run; runs of 10+ crossings are passes."""
    pts = _stitches(pattern, with_jumps=True)
    xs = [(a[0] + b[0]) / 2 for a, b in zip(pts, pts[1:]) if abs(b[1] - a[1]) > min_dy_mm]
    runs, cur = [], [xs[0]] if xs else []
    for x in xs[1:]:
        if abs(x - cur[-1]) < 1.5:
            cur.append(x)
        else:
            runs.append(cur)
            cur = [x]
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 10]


@needs_planner
@needs_binary
class TestOnePassEngine:
    """Behavioural proof (CLAUDE.md rule 3): with --one-pass the engine sews the bar's top layer
    in one pass; with --no-one-pass (end_at_nearest_point on) it splits it to finish near the
    post."""

    @pytest.fixture(autouse=True)
    def real_schema(self):
        """conftest isolates the cache (bootstrap schema, which doesn't know the nearest-point
        params): mine the real one from the engine source, as test_differential_params does."""
        import os
        cands = [Path(__file__).parent.parent / "inkstitch", Path(os.environ.get("INKSTITCH_SOURCE", "/nonexistent"))]
        src = next((c for c in cands if (c / "lib" / "elements").exists()), None)
        if src is None:
            pytest.skip("inkstitch source checkout not present")
        from cli_anything_inkstitch.schema.extract import extract_schema, write_cache
        write_cache(extract_schema(src))

    def _run(self, tmp_path, flag):
        svg = tmp_path / f"{flag}.svg"
        svg.write_text(_bar_and_post_svg())
        proj = tmp_path / f"{flag}.json"
        assert CliRunner().invoke(root, ["document", "open", "--project", str(proj), "--svg", str(svg)]).exit_code == 0
        r = CliRunner().invoke(root, ["--json", "tools", "shapes-to-satin", "--project", str(proj), "--keep-order", flag])
        assert r.exit_code == 0, r.output
        assert "params_skipped" not in json.loads(r.output)
        return _top_layer_runs(_export(proj, tmp_path, flag[2:]))

    def test_default_sews_the_bar_in_one_pass_and_the_engine_default_splits_it(self, tmp_path):
        one = self._run(tmp_path, "--one-pass")
        split = self._run(tmp_path, "--no-one-pass")
        assert len(one) == 1 and max(one[0]) - min(one[0]) > 35
        assert len(split) == 2


def _stem_runs(pattern, x_max, min_dx=1.5):
    """Top-layer passes over a letter's left stem (x < x_max, DST mm): its zigzag crosses the stem
    in x; consecutive crossings advancing < 1.5 mm in y are one run; runs of 10+ are passes."""
    pts = _stitches(pattern, with_jumps=True)
    ys = [(a[1] + b[1]) / 2 for a, b in zip(pts, pts[1:])
          if a[0] < x_max and b[0] < x_max and abs(b[0] - a[0]) > min_dx and abs(b[1] - a[1]) < 1.0]
    runs, cur = [], [ys[0]] if ys else []
    for y in ys[1:]:
        if abs(y - cur[-1]) < 1.5:
            cur.append(y)
        else:
            runs.append(cur)
            cur = [y]
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 10]


@needs_planner
@needs_binary
class TestLetteringOnePass:
    """text-to-satin sews every column in one pass, as shapes-to-satin does (behavioural proof,
    CLAUDE.md rule 3): an H's first stem ends beside its crossbar, and with the engine default
    (end_at_nearest_point on) its top layer splits to finish near the bar."""

    @pytest.fixture(autouse=True)
    def real_schema(self):
        import os
        cands = [Path(__file__).parent.parent / "inkstitch", Path(os.environ.get("INKSTITCH_SOURCE", "/nonexistent"))]
        src = next((c for c in cands if (c / "lib" / "elements").exists()), None)
        if src is None:
            pytest.skip("inkstitch source checkout not present")
        from cli_anything_inkstitch.schema.extract import extract_schema, write_cache
        write_cache(extract_schema(src))

    def _run(self, tmp_path, flag):
        d = tmp_path / flag[2:]
        d.mkdir()
        proj = _project(d)
        r = CliRunner().invoke(root, ["--json", "tools", "text-to-satin", "--project", str(proj), "--font", FONT,
                                      "--text", "H", "--height-mm", "20", flag])
        assert r.exit_code == 0, r.output
        assert "params_skipped" not in json.loads(r.output)
        tree = etree.parse(json.loads(Path(proj).read_text())["svg_path"])
        sat = [e for e in tree.getroot().iter() if (e.get("{http://inkstitch.org/namespace}satin_column") or "").lower() == "true"]
        pattern = _export(proj, tmp_path / flag[2:], "h")
        xs = [x for x, _ in _stitches(pattern)]
        return sat, _stem_runs(pattern, min(xs) + 6.0)          # the stem is 5.2 mm wide

    def test_every_column_one_pass_and_the_default_splits(self, tmp_path):
        sat, runs = self._run(tmp_path, "--one-pass")
        assert sat and all(e.get("{http://inkstitch.org/namespace}end_at_nearest_point") == "False" for e in sat)
        assert len(runs) == 1
        _, split = self._run(tmp_path, "--no-one-pass")
        assert len(split) == 2


class TestRoutedTravelIsSewn:
    """auto_satin copies only a source satin's `style` into its travel strokes (lib/stitches/
    auto_satin.py RunningStitch.to_element); once a satin's colour lives in a `stroke` attribute the
    travel stroke has none, and the engine never sews a path without a stroke colour
    (lib/elements/utils/nodes.py node_to_elements)."""

    SVG = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkstitch="http://inkstitch.org/namespace">'
           '<g><path id="s" stroke="#123456" inkstitch:satin_column="True" d="M0,0 L9,0 M0,2 L9,2"/>'
           '<path id="r" style="fill:none;stroke-dasharray:1,0.2" inkstitch:path_type="satin-underpath" d="M0,0 L9,0"/>'
           '<path id="ok" style="fill:none;stroke:#ff0000" inkstitch:path_type="satin-underpath" d="M0,0 L9,0"/></g>'
           '<g><path id="lone" style="fill:none" inkstitch:path_type="satin-underpath" d="M0,0 L9,0"/></g></svg>')

    def test_uncoloured_travel_takes_its_satins_colour(self):
        from cli_anything_inkstitch.commands.tools import _color_satin_underpaths
        t = etree.ElementTree(etree.fromstring(self.SVG))
        by = lambda i: next(e for e in t.getroot().iter() if e.get("id") == i)
        assert _color_satin_underpaths("auto_satin", t) == 1
        assert by("r").get("stroke") == "#123456"
        assert by("ok").get("stroke") is None          # already coloured: left alone
        assert by("lone").get("stroke") is None        # no satin beside it to take a colour from

    def test_only_after_auto_satin(self):
        from cli_anything_inkstitch.commands.tools import _color_satin_underpaths
        t = etree.ElementTree(etree.fromstring(self.SVG))
        assert _color_satin_underpaths("fill_to_satin", t) == 0


class TestRoutedHandoffs:
    """A routed letter's satins end where drawn once end_at_nearest_point is off; a handoff to the
    next sewn element that would be a jump is sewn as a travel under stitching still to come when
    that line is covered, else trimmed (same distance rule as plan_one_pass). 1 unit = 1 mm."""

    def _group(self, body):
        return etree.fromstring('<g xmlns="http://www.w3.org/2000/svg" '
                                'xmlns:inkstitch="http://inkstitch.org/namespace">' + body + '</g>')

    def _fixes(self, g):
        from cli_anything_inkstitch.commands.tools import _routed_handoffs
        return [(h["after"].get("id"), h["hide"] is not None) for h in _routed_handoffs(g, 1.0)]

    def test_bare_long_handoff_is_trimmed_short_one_left(self):
        g = self._group(
            '<path id="a" stroke="#000" inkstitch:satin_column="True" d="M0,0 L10,0 M0,2 L10,2"/>'
            # b starts 4 mm on from a's end, across bare fabric: trim
            '<path id="b" stroke="#000" inkstitch:satin_column="True" d="M14,0 L14,10 M16,0 L16,10"/>'
            # a running stitch picking up right at b's end: nothing to do
            '<path id="r" style="fill:none;stroke:#000" d="M15,10 L15,20"/>'
            # an unsewn stroke (no colour) is not an element the engine sews
            '<path id="x" style="fill:none" d="M40,40 L50,50"/>')
        assert self._fixes(g) == [("a", False)]

    def test_covered_handoff_is_hidden_travel_not_a_trim(self):
        g = self._group(
            '<path id="a" stroke="#000" inkstitch:satin_column="True" d="M0,0 L10,0 M0,2 L10,2"/>'
            '<path id="b" stroke="#000" inkstitch:satin_column="True" d="M14,5 L14,30 M16,5 L16,30"/>'
            # c, sewn later, covers the straight line from a's end to b's start
            '<path id="c" stroke="#000" inkstitch:satin_column="True" d="M8,-2 L18,-2 M8,6 L18,6"/>')
        assert self._fixes(g) == [("a", True), ("b", False)]       # b -> c is a bare jump: trimmed

    def test_handoff_just_under_collapse_len_counts_for_the_overshoot(self):
        # b's outline (x >= 12.7) is 2.7 mm from a's end: under collapse_len (3 mm), where the engine
        # would stitch through, but within the 0.5 mm margin for the needle's overshoot
        g = self._group(
            '<path id="a" stroke="#000" inkstitch:satin_column="True" d="M0,0 L10,0 M0,2 L10,2"/>'
            '<path id="b" stroke="#000" inkstitch:satin_column="True" d="M12.7,2 L12.7,-10 M14.7,2 L14.7,-10"/>')
        assert self._fixes(g) == [("a", False)]
        far = self._group(
            '<path id="a" stroke="#000" inkstitch:satin_column="True" d="M0,0 L10,0 M0,2 L10,2"/>'
            '<path id="b" stroke="#000" inkstitch:satin_column="True" d="M12.0,2 L12.0,-10 M14.0,2 L14.0,-10"/>')
        assert self._fixes(far) == []                      # 2.0 mm: stitched through


class TestRoutedHandoffRails:
    def test_rails_are_found_like_the_engine_when_a_rung_comes_first(self):
        """auto_satin output can list a rung before the rails; the engine takes the longest subpaths
        as rails (satin_column.py rail_indices). Here a's rails run x 0 -> 10, so it ends at x = 10,
        right beside b: no handoff, although its first subpath (a rung) ends at x = 1."""
        from cli_anything_inkstitch.commands.tools import _routed_handoffs
        g = etree.fromstring(
            '<g xmlns="http://www.w3.org/2000/svg" xmlns:inkstitch="http://inkstitch.org/namespace">'
            '<path id="a" stroke="#000" inkstitch:satin_column="True" '
            'd="M1,-0.5 L1,2.5 M0,0 L10,0 M0,2 L10,2 M5,-0.5 L5,2.5 M9,-0.5 L9,2.5"/>'
            '<path id="b" stroke="#000" inkstitch:satin_column="True" d="M10.5,0 L10.5,10 M12.5,0 L12.5,10"/>'
            '</g>')
        assert _routed_handoffs(g, 1.0) == []
