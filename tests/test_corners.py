"""Lettering corners and junctions (trace/corners.py, glyph_columns.plan_glyph).

* A narrow stroke turning a corner is one satin column round it, the corner stitch from the inside
  corner to the outside one (about 1.4 x the width); a wide one keeps the butt join. The threshold is
  a named constant compared with that corner stitch, kept under the split-satin length.
* A glyph's plan doesn't depend on where it sits on the line.
* Small lettering gets a centre walk under columns narrower than 2 mm.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

pytest.importorskip("shapely")
pytest.importorskip("scipy")
pytest.importorskip("fontTools")

import numpy as np  # noqa: E402

from cli_anything_inkstitch.trace import corners  # noqa: E402
from cli_anything_inkstitch.trace import glyph_columns as gc  # noqa: E402

LATO = Path(__file__).parent / "fixtures" / "fonts" / "Lato-Black.ttf"


def _l_shape(w, h=20.0, foot=12.0):
    return [np.array([(0, 0), (foot, 0), (foot, w), (w, w), (w, h), (0, h)], float)]


class TestCornerRule:
    def test_threshold_is_the_corner_stitch_and_under_split_length(self):
        assert corners.CORNER_WRAP_MAX_STITCH_MM == pytest.approx(
            corners.CORNER_STITCH_RATIO * corners.WRAP_MAX_WIDTH_MM)
        assert corners.CORNER_WRAP_MAX_STITCH_MM < gc.SPLIT_SATIN_MM

    @pytest.mark.parametrize("w", [1.7, 2.6, 3.5])
    def test_narrow_l_wraps_as_one_column(self, w):
        p = gc.plan_glyph("L", _l_shape(w), 20.0, 1.5)
        assert p.ok and len(p.columns) == 1
        (wrap,) = p.check["corners_wrapped"]
        assert wrap["stitch_mm"] == pytest.approx(w * math.sqrt(2), abs=0.05)
        assert p.check["overlap_mm2"] == 0
        # the corner stitch is one of the column's pairs: inside corner to outside corner
        c = p.columns[0]
        pairs = list(zip(c.left, c.right))
        assert any(math.dist(a, (w, w)) < 1e-6 and math.dist(b, (0, 0)) < 1e-6 for a, b in pairs)

    @pytest.mark.parametrize("w", [4.5, 5.0])
    def test_wide_l_keeps_the_butt_join(self, w):
        p = gc.plan_glyph("L", _l_shape(w), 20.0, 1.5)
        assert p.ok and len(p.columns) == 2
        (butt,) = p.check["corners_butted"]
        assert butt["stitch_mm"] > corners.CORNER_WRAP_MAX_STITCH_MM
        assert "corners_wrapped" not in p.check

    def test_the_threshold_decides(self, monkeypatch):
        """The same 2.6 mm L butts when the threshold is set under its 3.7 mm corner stitch."""
        real = corners.wrap_corners
        monkeypatch.setattr(corners, "wrap_corners", lambda p: real(p, max_stitch_mm=3.0))
        p = gc.plan_glyph("L", _l_shape(2.6), 20.0, 1.5)
        assert len(p.columns) == 2 and p.check["corners_butted"]


@pytest.mark.skipif(not LATO.exists(), reason="vendored Lato Black missing")
class TestLettering:
    def test_square_corners_and_points_wrap_at_13mm(self):
        got = {lt.char: lt for lt in gc.plan_text(LATO, "LDWV", 13.0)}
        assert all(lt.ok for lt in got.values())
        assert len(got["L"].columns) == 1
        d = got["D"].columns
        assert len(d) == 1 and d[0].closed               # stem and bowl one ring, as EmbroideryStudio's D
        assert len(got["W"].columns) == 1                # legs wrapped through both feet and the middle
        assert len(got["V"].columns) == 1
        assert all(lt.check["overlap_mm2"] == 0 for k, lt in got.items() if k in "LDWV")

    def test_wide_strokes_keep_butt_joins(self):
        # 30 mm caps: Lato Black strokes about 5.6 mm, corner stitches about 8 mm
        got = {lt.char: lt for lt in gc.plan_text(LATO, "LD", 30.0)}
        assert len(got["L"].columns) == 2 and got["L"].check["corners_butted"]
        assert len(got["D"].columns) == 2

    def test_plan_does_not_depend_on_position(self):
        a = gc.plan_text(LATO, "RKYG", 10.0, letter_spacing_pct=0)
        b = gc.plan_text(LATO, "RKYG", 10.0, letter_spacing_pct=37)
        for x, y in zip(a, b):
            assert x.char == y.char and len(x.columns) == len(y.columns)
            dx = min(p[0] for c in y.contours for p in c) - min(p[0] for c in x.contours for p in c)
            for cx, cy in zip(x.columns, y.columns):
                assert len(cx.left) == len(cy.left)
                assert max(abs(p[0] + dx - q[0]) + abs(p[1] - q[1])
                           for p, q in zip(cx.left + cx.right, cy.left + cy.right)) < 1e-6
            for k in ("sewable_bare_pct", "overlap_mm2", "outside_pct"):
                assert x.check[k] == y.check[k]


class TestSmallLetteringUnderlay:
    def test_centre_walk_under_2mm_for_lettering(self):
        from cli_anything_inkstitch.commands.tools import (LETTERING_CENTER_WALK_BELOW_MM,
                                                           _lettering_satin_params)
        w = [1.8] * 5
        plain = _lettering_satin_params(w, 0.2)
        small = _lettering_satin_params(w, 0.2, center_below_mm=LETTERING_CENTER_WALK_BELOW_MM)
        assert plain.get("contour_underlay") == "true" and "center_walk_underlay" not in plain
        assert small.get("center_walk_underlay") == "true" and "contour_underlay" not in small
        wide = _lettering_satin_params([2.6] * 5, 0.2, center_below_mm=LETTERING_CENTER_WALK_BELOW_MM)
        assert wide.get("contour_underlay") == "true"
