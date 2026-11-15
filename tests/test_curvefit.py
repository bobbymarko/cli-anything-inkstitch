"""Curve fitting of engine-emitted polylines + curve-preserving transforms.

Engine conversions (stroke_to_satin, fill_to_stroke, satin_to_stroke, ...)
write dense M/L point chains; svg/curvefit.py refits them as beziers so the
artifact editor shows a few handles instead of a node forest. The contract
that matters is the engine's: the refit geometry must stitch where the
original did. The binary-backed classes below drive the same element both
ways and compare the stitch plans (CLAUDE.md rule 3), with a control that
proves the measurement can see a real 0.5 mm move (rule 4a).
"""

from __future__ import annotations

import math
import re

import pytest

from cli_anything_inkstitch.artifact.gate import flatten_path
from cli_anything_inkstitch.svg.curvefit import (
    FIT_TOL_MM,
    fit_d_polylines,
    fit_polyline,
    fit_polyline_d,
    parse_segments,
    reverse_segments,
    segments_to_d,
)
from cli_anything_inkstitch.svg.geometry import IDENTITY, apply_matrix, transform_d
from cli_anything_inkstitch.trace.satinize import simplify_satin_d

PX_PER_MM = 96 / 25.4
TOL = FIT_TOL_MM * PX_PER_MM

_needs_binary = pytest.mark.skipif(
    __import__("cli_anything_inkstitch.binary", fromlist=["discover"]).discover() is None,
    reason="Ink/Stitch binary not installed")


# ---------------------------------------------------------------- helpers

def _seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _dist_to_poly(p, poly):
    return min(_seg_dist(p, poly[i], poly[i + 1]) for i in range(len(poly) - 1))


def _hausdorff(a, b):
    return max(max(_dist_to_poly(p, b) for p in a),
               max(_dist_to_poly(p, a) for p in b))


def _anchors(d):
    return len(re.findall(r"[MLC]", transform_d(d, IDENTITY)))


S_CURVE = "M20,150 C60,110 100,190 180,150"


def _dense(d, n=60):
    return flatten_path(d, n)


def _offset(pts, w):
    out = []
    for i, p in enumerate(pts):
        a = pts[max(0, i - 1)]
        b = pts[min(len(pts) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        out.append((p[0] - dy / L * w, p[1] + dx / L * w))
    return out


def _poly(pts):
    return "M" + " ".join(f"{x:.4f},{y:.4f}" for x, y in pts)


def _dense_satin_d(half_width_px=8.0, every=6, overhang=1.0):
    """Engine-shaped satin: two dense polyline rails + rungs (stroke_to_satin
    writes exactly this: rails then rungs, all M/L)."""
    center = _dense(S_CURVE, 60)
    a, b = _offset(center, half_width_px), _offset(center, -half_width_px)
    rungs = []
    for i in range(every, len(center) - every, every):
        ax, ay = a[i]; bx, by = b[i]
        ux, uy = (bx - ax), (by - ay)
        L = math.hypot(ux, uy)
        ux, uy = ux / L * overhang, uy / L * overhang
        rungs.append([(ax - ux, ay - uy), (bx + ux, by + uy)])
    return " ".join([_poly(a), _poly(b)] + [_poly(r) for r in rungs]), a, b


# ---------------------------------------------------------------- pure fit

class TestFitPolyline:
    def test_dense_curve_becomes_few_cubics_within_tolerance(self):
        pts = _dense(S_CURVE, 60)
        d, nodes = fit_polyline_d(pts, TOL)
        assert nodes <= 8 and "C" in d
        assert _hausdorff(pts, flatten_path(d, 200)) <= TOL * 1.05

    def test_circle_ring_closes_smoothly(self):
        pts = [(50 + 30 * math.cos(k * math.pi / 40), 50 + 30 * math.sin(k * math.pi / 40))
               for k in range(80)]
        d, nodes = fit_polyline_d(pts, TOL, closed=True)
        assert nodes <= 10 and d.rstrip().endswith("Z")
        assert _hausdorff(pts + [pts[0]], flatten_path(d, 200)) <= TOL * 1.05

    def test_corners_stay_exact_corners(self):
        square = [(0, 0), (5, 0), (10, 0), (10, 5), (10, 10), (5, 10), (0, 10), (0, 5)]
        segs = fit_polyline(square, TOL, closed=True)
        ends = {s[-1] for s in segs}
        assert {(0, 0), (10, 0), (10, 10), (0, 10)} <= ends
        assert all(s[0] == "L" for s in segs)

    def test_curves_in_d_pass_through_untouched(self):
        d = "M0,0 C1,1 2,2 3,3"
        assert fit_d_polylines(d, TOL)[0] == d


class TestSatinRailFit:
    def test_rails_become_curves_and_rungs_are_untouched(self):
        d, _, _ = _dense_satin_d()
        new_d, before, after = simplify_satin_d(d, tolerance=TOL)
        assert after < before / 3
        subs = [c for c in re.split(r"(?=M)", new_d) if c.strip()]
        old = [c for c in re.split(r"(?=M)", d) if c.strip()]
        assert "C" in subs[0] and "C" in subs[1]
        assert [s.strip() for s in subs[2:]] == [s.strip() for s in old[2:]]

    def test_rung_that_would_lose_its_rail_blocks_the_loose_fit(self):
        # rungs that barely reach the rails: a loose fit slips off their
        # ends, so the guard must refit tighter or keep the original
        d, _, _ = _dense_satin_d(overhang=0.02)
        new_d, before, after = simplify_satin_d(d, tolerance=TOL)
        from cli_anything_inkstitch.trace.satinize import _rung_crossings_kept
        old = [c for c in re.split(r"(?=M)", d) if c.strip()]
        new = [c for c in re.split(r"(?=M)", new_d) if c.strip()]
        assert _rung_crossings_kept(old, new)

    def test_two_rail_satin_without_rungs_is_never_refit(self):
        # old-style satins pair rail NODES (satin_column.py _synthesize_rungs)
        d, _, _ = _dense_satin_d()
        two = " ".join([c for c in re.split(r"(?=M)", d) if c.strip()][:2])
        assert simplify_satin_d(two, tolerance=TOL)[0] == two


# ---------------------------------------------------------------- transform_d

class TestTransformKeepsCurves:
    CASES = {
        "smooth_s": "M50,20c30,0,60,20,60,50s-30,50-60,50S-10,100-10,70,20,20,50,20z",
        "quad_t": "M20,150q40-40,80,0t80,0",
        "arc": "M10,50 A40,20 30 1 1 90,50",
        "rel_arc": "M10,10 a30,30 0 0 0 60,0 h20 v10",
    }

    @pytest.mark.parametrize("name", list(CASES))
    @pytest.mark.parametrize("m", [(1, 0, 0, 1, 5, 5), (1.2, 0.3, -0.4, 0.9, 7, -3)])
    def test_baked_geometry_matches_transformed_original(self, name, m):
        d = self.CASES[name]
        expect = [apply_matrix(m, *p) for p in flatten_path(d, 100)]
        got = flatten_path(transform_d(d, m), 100)
        assert _hausdorff(expect, got) < 0.05
        assert "C" in transform_d(d, m)


# ---------------------------------------------------------------- satin→fill

class TestSatinToFillKeepsRailCurves:
    def test_band_outline_reuses_rail_segments(self):
        a = "M0,0 C30,-10 60,10 90,0"
        b = "M0,10 C30,0 60,20 90,10"
        a0, asg, _ = parse_segments(a)
        b0, bsg, _ = parse_segments(b)
        end, rev = reverse_segments(b0, bsg)
        d = segments_to_d(a0, asg + [("L", end)] + rev, closed=True)
        assert _anchors(d) == 4 and d.count("C") == 2
        ring = flatten_path(a, 50) + flatten_path(b, 50)[::-1]
        assert _hausdorff(ring + [ring[0]], flatten_path(d, 50)) < 1e-6


# ---------------------------------------------------------------- engine

def _project(tmp_path, body):
    from cli_anything_inkstitch.project import ProjectFile
    from cli_anything_inkstitch.svg.document import sha256_of
    svg = tmp_path / "d.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:inkstitch="http://inkstitch.org/namespace" '
        'width="200" height="220" viewBox="0 0 200 220">' + body + "</svg>")
    proj_path = tmp_path / "d.inkstitch-cli.json"
    proj, _ = ProjectFile.load_or_create(str(proj_path))
    proj.svg_path = str(svg)
    proj.svg_sha256 = sha256_of(svg)
    proj.save()
    return str(proj_path)


def _stitches(tmp_path, body):
    from cli_anything_inkstitch.artifact.design_model import (
        extract_stitch_blocks, stitch_plan_svg)
    p = _project(tmp_path, body)
    blocks = extract_stitch_blocks(stitch_plan_svg(p))
    return [q for b in blocks["blocks"] for path in b["paths"] for q in path]


# lock stitches protrude past path ends (CLAUDE.md rule 4a: they broke
# the first version of this measurement) — ties="3" is "Neither"
# (lib/elements/element.py EmbroideryElement.ties, get_int_param index)
_NO_TIES = 'inkstitch:ties="3"'


# short stitches inset alternate stitches on a curve's inside by 15 % of
# the width by default — intended, but it also reads as "off the rail";
# 0 makes every inset offset zero (satin_column.py short_stitch_inset,
# percent; consumed by inset_short_stitches_sawtooth → _get_offset_px)
_NO_SHORT = 'inkstitch:short_stitch_inset="0"'


def _satin(d):
    return (f'<path id="s" d="{d}" inkstitch:satin_column="true" {_NO_TIES} '
            f'{_NO_SHORT} style="fill:none;stroke:#0a0;stroke-width:1"/>')


def _run(d):
    return (f'<path id="r" d="{d}" {_NO_TIES} '
            'style="fill:none;stroke:#00c;stroke-width:1"/>')


# the measurement: every stitch of a plan lies on the given rails/path —
# the engine's defaults (no pull compensation, no underlay) put satin
# stitches exactly on the rails, so off-rail distance is fit error
def _max_off(stitches, polys):
    return max(min(_dist_to_poly(s, poly) for poly in polys) for s in stitches)


@_needs_binary
class TestEngineStitchesWhereTheOriginalDid:
    def test_satin_refit_moves_no_stitch_beyond_tolerance(self, tmp_path):
        d, a, b = _dense_satin_d()
        new_d, before, after = simplify_satin_d(d, tolerance=TOL)
        assert after < before
        (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
        orig = _stitches(tmp_path / "a", _satin(d))
        fit = _stitches(tmp_path / "b", _satin(new_d))
        # control: the original plan sits on the original rails (measurement
        # sees what it should), and a rail moved 0.5 mm is caught
        assert _max_off(orig, [a, b]) < 0.15 * PX_PER_MM
        moved = [(x, y + 0.5 * PX_PER_MM) for x, y in a]
        assert _max_off(orig, [moved, b]) > 0.4 * PX_PER_MM
        # the fit: within tolerance + engine flattening + rounding
        assert _max_off(fit, [a, b]) < TOL + 0.1 + 0.02
        assert abs(len(fit) - len(orig)) <= max(3, 0.05 * len(orig))

    def test_running_stitch_refit_stays_on_the_path(self, tmp_path):
        pts = _dense(S_CURVE, 60)
        d = _poly(pts)
        new_d, before, after = fit_d_polylines(d, TOL)
        assert after < before / 3
        (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
        orig = _stitches(tmp_path / "a", _run(d))
        fit = _stitches(tmp_path / "b", _run(new_d))
        assert _max_off(orig, [pts]) < 0.02 * PX_PER_MM          # control
        assert _max_off(fit, [pts]) < TOL + 0.1 + 0.02

    def test_baked_transform_stitches_like_the_live_transform(self, tmp_path):
        # Illustrator's s/S shorthand under a transform: before the fix the
        # baked path was a polygon through the anchors (≈ 6 mm off here)
        d = TestTransformKeepsCurves.CASES["smooth_s"]
        m = (1.2, 0.3, -0.4, 0.9, 7, -3)
        style = 'style="fill:none;stroke:#00c;stroke-width:1"'
        live = (f'<path id="f" transform="matrix{m}" d="{d}" {_NO_TIES} '
                f'{style}/>')
        baked = f'<path id="f" d="{transform_d(d, m)}" {_NO_TIES} {style}/>'
        (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
        a = _stitches(tmp_path / "a", live)
        b = _stitches(tmp_path / "b", baked)
        ref = [apply_matrix(m, *p) for p in flatten_path(d, 100)]
        assert _max_off(a, [ref]) < 0.05 * PX_PER_MM             # control
        assert _max_off(b, [ref]) < 0.05 * PX_PER_MM


# ---------------------------------------------------------------- planner columns

def _arc_column(n=90, r_mm=20.0, w_mm=3.0, twist_deg=0.0):
    """A planner column along a 120° arc: one node pair per stitch-ish step,
    pairs radial, optionally twisted (the planner tilts rungs into turns)."""
    from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn
    left, right = [], []
    for i in range(n):
        t = math.radians(-60 + 120 * i / (n - 1))
        tw = math.radians(twist_deg) * math.sin(math.pi * i / (n - 1))
        for rad, out in ((r_mm - w_mm / 2, left), (r_mm + w_mm / 2, right)):
            a = t + (tw if out is right else -tw) * (w_mm / 2) / r_mm
            out.append((rad * math.sin(a), rad * math.cos(a) - r_mm / 2))
    return PlannedColumn(left=left, right=right)


def _column_tx():
    from cli_anything_inkstitch.trace.glyph_columns import make_transform
    return make_transform(PX_PER_MM, 100, 110)


class TestPlannerColumnsKeepCurves:
    def test_rails_become_a_few_curves_with_explicit_rungs(self):
        from cli_anything_inkstitch.trace.glyph_columns import satin_d, satin_d_curves
        col, tx = _arc_column(), _column_tx()
        dense = satin_d(col, tx)
        d = satin_d_curves(col, tx, TOL)
        subs = [c for c in re.split(r"(?=M)", d) if c.strip()]
        assert "C" in subs[0] and "C" in subs[1]
        assert len(subs) > 2                          # explicit rungs: no node pairing left to break
        assert _anchors(" ".join(subs[:2])) < _anchors(dense) / 4
        # the refit rails stay within tolerance of the planned rails
        for sub, rail in zip(subs[:2], (col.left, col.right)):
            assert _hausdorff(flatten_path(sub, 200), [tx(p) for p in rail]) < TOL * 1.5

    def test_short_column_is_written_as_before(self):
        from cli_anything_inkstitch.trace.glyph_columns import satin_d, satin_d_curves
        col, tx = _arc_column(n=4), _column_tx()
        assert satin_d_curves(col, tx, TOL) == satin_d(col, tx)


def _zigzag(stitches, min_len_px):
    """(midpoint, direction angle) of each satin crossing: consecutive
    stitches farther apart than ``min_len_px`` (underlay and locks are short)."""
    out = []
    for p, q in zip(stitches, stitches[1:]):
        dx, dy = q[0] - p[0], q[1] - p[1]
        if math.hypot(dx, dy) > min_len_px:
            out.append((((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), math.atan2(dy, dx) % math.pi))
    return out


def _angle_err_deg(ref, test):
    """Per crossing of ``test``: angle difference to the nearest crossing of ``ref``."""
    from scipy.spatial import cKDTree
    t = cKDTree([m for m, _ in ref])
    errs = []
    for m, a in test:
        _, j = t.query(m)
        d = abs(a - ref[j][1]) % math.pi
        errs.append(math.degrees(min(d, math.pi - d)))
    return sorted(errs)


@_needs_binary
class TestPlannerColumnsStitchAsPlanned:
    """satin_d pairs every node (the engine's _synthesize_rungs); satin_d_curves
    keeps only some of those pairs as rungs. The plans must still match:
    stitches on the same rails AND at the planner's stitch angle where it
    twisted its pairs (that angle is what explicit rungs could lose)."""

    @pytest.mark.parametrize("twist", [0.0, 25.0])
    def test_curved_column_stitches_like_the_dense_one(self, tmp_path, twist):
        from cli_anything_inkstitch.trace.glyph_columns import satin_d, satin_d_curves
        col, tx = _arc_column(twist_deg=twist), _column_tx()
        for k in "abc":
            (tmp_path / k).mkdir()
        dense = _stitches(tmp_path / "a", _satin(satin_d(col, tx)))
        fit = _stitches(tmp_path / "b", _satin(satin_d_curves(col, tx, TOL)))
        # rails: every stitch of the fit within tolerance of the planned rails
        rails = [[tx(p) for p in col.left], [tx(p) for p in col.right]]
        assert _max_off(dense, rails) < 0.15 * PX_PER_MM          # measurement control
        assert _max_off(fit, rails) < TOL + 0.1 + 0.02
        # angle: control first — a pairing twisted 20 degrees the other way
        # (same rails) must read as wrong
        zl = 1.5 * PX_PER_MM
        ref = _zigzag(dense, zl)
        wrong = _stitches(tmp_path / "c", _satin(satin_d(_arc_column(twist_deg=twist - 20.0), tx)))
        assert _angle_err_deg(ref, _zigzag(wrong, zl))[len(ref) // 2] > 4.0
        errs = _angle_err_deg(ref, _zigzag(fit, zl))
        assert errs[int(0.95 * len(errs))] < 2.0
        assert abs(len(fit) - len(dense)) <= max(3, 0.05 * len(dense))
