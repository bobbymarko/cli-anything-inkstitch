"""TTF text → per-stroke satin columns (`tools text-to-satin`).

Why: rung-based fill-to-satin broke letters at junctions (the 6's wedge,
EST. 2018's E/T/1/8 in the 2026-11-08 head-to-head). The planner
(trace/glyph_columns.py, stitchpilot's column planner) pairs rails by
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
    import stitchpilot.art_columns  # noqa: F401
    import shapely  # noqa: F401
    HAVE_PLANNER = True
except ImportError:
    HAVE_PLANNER = False

needs_planner = pytest.mark.skipif(not (HAVE_PLANNER and FONT),
                                   reason="needs the lettering extra and a TTF")
needs_binary = pytest.mark.skipif(discover() is None,
                                  reason="Ink/Stitch binary not installed")


# ---------------------------------------------------------------- pure contracts

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
# planner (stitchpilot art_columns.plan_shape) digitizes them stroke by stroke; the HIDA chest logo
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
