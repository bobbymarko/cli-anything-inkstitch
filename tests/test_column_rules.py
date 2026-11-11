"""shapes-to-satin column rules: one column for an unbranched stroke, measurable fans and seams,
trims instead of seam-adding reroutes.

Why: on a large logo the planner cut an S into three columns that met end to end at its bowls. Each
meeting was a hard seam (the stitch angle jumps and a line shows across one stroke) with fanned
stitches beside it. A hand-digitized S is one column round both bowls. Separately, auto_satin
routing (used to hide travel between a shape's columns) cut columns where they crossed, adding
seams to shapes that had none. Synthetic shapes only.
"""

from __future__ import annotations

import json
import math

import pytest
from click.testing import CliRunner

from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.trace import column_checks as cc

try:
    import numpy as np
    import scipy.spatial  # noqa: F401
    import shapely  # noqa: F401
    HAVE_PLANNER = True
except ImportError:
    HAVE_PLANNER = False

needs_planner = pytest.mark.skipif(not HAVE_PLANNER, reason="satin extra not installed")
needs_binary = pytest.mark.skipif(discover() is None, reason="inkstitch binary not found")


def _s_outline(r=7.0, w=5.0, n=60):
    """An S: two opposite three-quarter arcs, one stroke of width w, square ends."""
    from shapely.geometry import LineString
    top = [(r * math.cos(a), r + r * math.sin(a)) for a in np.linspace(-0.05 * math.pi, 1.5 * math.pi, n)]
    bot = [(-r * math.cos(a), -r + r * math.sin(a)) for a in np.linspace(0.5 * math.pi, -1.05 * math.pi, n)]
    poly = LineString(top[::-1] + bot).buffer(w / 2, cap_style=2, join_style=1)
    return [list(poly.exterior.coords)[:-1]]


class TestChecks:
    def test_pivot_is_a_fan_and_a_curve_is_not(self):
        # a column turning about a point: left rail still, right rail sweeping 90 degrees at r = 4
        pivot = [((0.0, 0.0), (4 * math.cos(a), 4 * math.sin(a))) for a in np.linspace(0, math.pi / 2, 12)]
        assert len(cc.fans(pivot)) == 1
        # a quarter ring, inner r 3, outer r 7: an ordinary curve
        ring = [((3 * math.cos(a), 3 * math.sin(a)), (7 * math.cos(a), 7 * math.sin(a)))
                for a in np.linspace(0, math.pi / 2, 30)]
        assert cc.fans(ring) == []

    def test_end_to_end_on_a_smooth_outline_is_a_hard_seam(self):
        # one straight 4 mm bar sewn as two columns meeting at x = 10: no corner in the outline there
        a = [((x, 0.0), (x, 4.0)) for x in np.linspace(0, 10, 6)]
        b = [((x, 0.0), (x, 4.0)) for x in np.linspace(10.2, 20, 6)]
        out = cc.seams([a, b], [[(0, 0), (20, 0), (20, 4), (0, 4)]])
        assert [s["kind"] for s in out] == ["hard_seam"]

    def test_an_elbow_is_a_corner_join(self):
        # an L: a leg up and a foot right, meeting at the outline's corner
        leg = [((0.0, y), (4.0, y)) for y in np.linspace(20, 2, 8)]
        foot = [((x, 0.0), (x, 4.0)) for x in np.linspace(2, 14, 8)]
        outline = [[(0, 0), (14, 0), (14, 4), (4, 4), (4, 20), (0, 20)]]
        assert [s["kind"] for s in cc.seams([leg, foot], outline)] == ["corner_join"]

    def test_ring_seam_is_listed(self):
        ring = [((3 * math.cos(a), 3 * math.sin(a)), (6 * math.cos(a), 6 * math.sin(a)))
                for a in np.linspace(0, 2 * math.pi, 40)]
        assert [s["kind"] for s in cc.seams([ring], [[(6, 0), (0, 6), (-6, 0), (0, -6)]], [True])] == ["ring_seam"]


@needs_planner
class TestOneColumn:
    def test_an_s_becomes_one_column_that_covers_it(self):
        from cli_anything_inkstitch.trace import glyph_columns as gc
        s = _s_outline()
        split = gc.plan_shapes([("s", s)], one_column=False)[0]
        one = gc.plan_shapes([("s", s)])[0]
        assert len(split.columns) >= 2 and gc.seam_report(split)["hard_seams"] >= 1
        assert len(one.columns) == 1 and one.ok and gc.seam_report(one)["hard_seams"] == 0
        assert one.check["sewable_bare_pct"] < split.check["sewable_bare_pct"]
        assert one.check["crossed_pairs"] == 0 and one.check["joined_strokes"]

    def test_rails_follow_the_outline(self):
        from shapely.geometry import LinearRing, Point
        from cli_anything_inkstitch.trace import glyph_columns as gc
        s = _s_outline()
        one = gc.plan_shapes([("s", s)])[0]
        ring = LinearRing(s[0])
        c = one.columns[0]
        assert max(ring.distance(Point(p)) for p in c.left + c.right) < 0.2

    def test_an_l_keeps_its_corner(self):
        from cli_anything_inkstitch.trace import glyph_columns as gc
        ell = [[(0, 0), (14, 0), (14, 4), (4, 4), (4, 20), (0, 20)]]
        a = gc.plan_shapes([("l", ell)], one_column=False)[0]
        b = gc.plan_shapes([("l", ell)])[0]
        assert len(a.columns) == len(b.columns)


def _two_bars_svg(k=96 / 25.4):
    d = (f"M {5 * k},{5 * k} L {35 * k},{5 * k} L {35 * k},{8 * k} L {5 * k},{8 * k} Z "
         f"M {5 * k},{20 * k} L {35 * k},{20 * k} L {35 * k},{23 * k} L {5 * k},{23 * k} Z")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="28mm" '
            f'viewBox="0 0 {40 * k:.4f} {28 * k:.4f}"><path id="eq" d="{d}" style="fill:#2e7d32"/></svg>')


@needs_planner
@needs_binary
class TestTravel:
    def _run(self, tmp_path, travel):
        from cli_anything_inkstitch.cli import root
        import pyembroidery as pe
        svg = tmp_path / f"{travel}.svg"
        svg.write_text(_two_bars_svg())
        proj = tmp_path / f"{travel}.json"
        assert CliRunner().invoke(root, ["document", "open", "--project", str(proj), "--svg", str(svg)]).exit_code == 0
        r = CliRunner().invoke(root, ["--json", "tools", "shapes-to-satin", "--project", str(proj), "--travel", travel])
        assert r.exit_code == 0, r.output
        out = tmp_path / f"{travel}.dst"
        assert CliRunner().invoke(root, ["export", "file", "--project", str(proj), "--format", "dst",
                                         "--out", str(out)]).exit_code == 0
        cmds = [c & pe.COMMAND_MASK for _, _, c in pe.read(str(out)).stitches]
        return json.loads(r.output), cmds.count(pe.TRIM)

    def test_travel_over_bare_fabric_is_trimmed_and_columns_stay_whole(self, tmp_path):
        trimmed, n_trim = self._run(tmp_path, "trim")
        left, n_left = self._run(tmp_path, "leave")
        assert trimmed["trims_added"] == 1 and left["trims_added"] == 0
        assert n_trim == n_left + 1
        assert trimmed["shapes"][0]["columns"] == 2 and trimmed["routed_shapes"] == 0
