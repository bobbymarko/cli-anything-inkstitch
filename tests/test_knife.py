"""Pure-geometry tests for svg/knife.py (no engine binary needed)."""

from __future__ import annotations

import math

import pytest
from cli_anything_inkstitch.svg.knife import (
    _seg_point,
    _split_seg,
    fill_area,
    knife_fill,
    knife_run,
    parse_rings,
)


def _area(d: str) -> float:
    """Engine-model area: largest ring shell, the rest holes."""
    return fill_area(d)


def _commands(d: str) -> str:
    return "".join(ch for ch in d if ch.isalpha())


SQUARE = "M0,0 L100,0 L100,100 L0,100 Z"
# a square whose top edge is one cubic bulging upward
BULGE = "M0,100 L0,0 C30,-40 70,-40 100,0 L100,100 Z"
RING = "M0,0 L100,0 L100,100 L0,100 Z M30,30 L30,70 L70,70 L70,30 Z"


class TestDeCasteljau:
    def test_split_halves_lie_on_original(self):
        c = ("C", (0, 0), (10, 40), (60, 40), (80, 0))
        a, b = _split_seg(c, 0.3)
        for u in (0.0, 0.25, 0.5, 0.75, 1.0):
            pa = _seg_point(a, u)
            pb = _seg_point(b, u)
            assert math.dist(pa, _seg_point(c, 0.3 * u)) < 1e-9
            assert math.dist(pb, _seg_point(c, 0.3 + 0.7 * u)) < 1e-9


class TestKnifeFill:
    def test_miss_is_noop(self):
        assert knife_fill(SQUARE, [(-10, -10), (-10, 200)]) is None

    def test_line_ending_inside_is_noop(self):
        assert knife_fill(SQUARE, [(-10, 50), (50, 50)]) is None

    def test_straight_cut_two_pieces_area_conserved(self):
        pieces = knife_fill(SQUARE, [(50, -10), (50, 110)])
        assert pieces is not None and len(pieces) == 2
        areas = sorted(_area(p) for p in pieces)
        assert areas == pytest.approx([5000, 5000], rel=1e-6)

    def test_curve_stays_curve_and_exact(self):
        pieces = knife_fill(BULGE, [(50, -60), (50, 120)])
        assert len(pieces) == 2
        for p in pieces:
            assert "C" in _commands(p), p        # not flattened to lines
        # both halves' cubic bits reassemble the original cubic exactly
        orig = ("C", (0, 0), (30, -40), (70, -40), (100, 0))
        for p in pieces:
            for segs, _c in parse_rings(p):
                for s in segs:
                    if s[0] == "C":
                        for u in (0.1, 0.5, 0.9):
                            q = _seg_point(s, u)
                            # q lies on the original curve: find t by x and compare y
                            best = min((math.dist(q, _seg_point(orig, t / 2000)), t)
                                       for t in range(2001))
                            assert best[0] < 0.05
        # areas are measured on flattened curves; split halves are sampled
        # more finely than the whole, hence the loose tolerance
        total = sum(_area(p) for p in pieces)
        assert total == pytest.approx(_area(BULGE), rel=1e-3)

    def test_hole_goes_with_containing_piece(self):
        pieces = knife_fill(RING, [(10, -10), (10, 110)])   # misses the hole
        assert len(pieces) == 2
        withhole = [p for p in pieces if _commands(p).count("M") == 2]
        assert len(withhole) == 1
        assert _area(withhole[0]) == pytest.approx(90 * 100 - 40 * 40, rel=1e-6)

    def test_cut_through_hole(self):
        pieces = knife_fill(RING, [(50, -10), (50, 110)])
        assert len(pieces) == 2
        for p in pieces:
            assert _commands(p).count("M") == 1             # hole opened up
            assert _area(p) == pytest.approx((10000 - 1600) / 2, rel=1e-6)

    def test_multiple_crossings_multisegment(self):
        # zigzag knife crossing the square three times → 3 strips... plus
        # wedges: every bounded region is a piece, areas sum to the whole
        line = [(20, -10), (20, 110), (50, -10), (80, 110)]
        pieces = knife_fill(SQUARE, line)
        assert pieces is not None and len(pieces) >= 3
        assert sum(_area(p) for p in pieces) == pytest.approx(10000, rel=1e-6)

    def test_concave_shape_cut_once_gives_three(self):
        u_shape = "M0,0 L30,0 L30,70 L70,70 L70,0 L100,0 L100,100 L0,100 Z"
        pieces = knife_fill(u_shape, [(-10, 30), (110, 30)])
        assert len(pieces) == 3
        assert sum(_area(p) for p in pieces) == pytest.approx(_area(u_shape), rel=1e-6)

    def test_relative_path_data(self):
        rel = "m0,0 l100,0 l0,100 l-100,0 z"
        pieces = knife_fill(rel, [(50, -10), (50, 110)])
        assert len(pieces) == 2


class TestKnifeRun:
    def test_open_line_two_pieces(self):
        pieces = knife_run("M0,0 L100,0", [(40, -10), (40, 10)])
        assert len(pieces) == 2
        assert pieces[0].startswith("M0,0") and pieces[0].endswith("40,0")
        assert pieces[1].startswith("M40,0")

    def test_curve_run_split_exact(self):
        pieces = knife_run("M0,0 C30,-40 70,-40 100,0", [(50, -60), (50, 60)])
        assert len(pieces) == 2
        assert all("C" in p for p in pieces)
        assert pieces[0].endswith("50,-30")          # apex of the curve

    def test_miss_is_noop(self):
        assert knife_run("M0,0 L100,0", [(0, 10), (100, 10)]) is None

    def test_two_crossings_three_pieces(self):
        pieces = knife_run("M0,0 L100,0", [(30, -10), (30, 10), (60, 10), (60, -10)])
        assert len(pieces) == 3

    def test_zigzag_knife_crosses_three_times(self):
        # the diagonal leg (30,10)→(60,-10) crosses too, at x=45
        pieces = knife_run("M0,0 L100,0", [(30, -10), (30, 10), (60, -10), (60, 10)])
        assert len(pieces) == 4

    def test_closed_ring_single_cut_opens(self):
        pieces = knife_run(SQUARE, [(50, -10), (50, 10)])
        assert len(pieces) == 1
        assert "Z" not in pieces[0]

    def test_untouched_subpath_kept(self):
        pieces = knife_run("M0,0 L100,0 M0,50 L100,50", [(40, -10), (40, 10)])
        assert len(pieces) == 3
        assert pieces[-1] == "M0,50 L100,50"


class TestKnifeFillEdgeCases:
    def test_diagonal_through_corners(self):
        pieces = knife_fill(SQUARE, [(-10, -10), (110, 110)])
        assert pieces is not None and len(pieces) == 2
        assert [_area(p) for p in pieces] == pytest.approx([5000, 5000], rel=1e-6)

    def test_circle_from_arcs_stays_curved(self):
        circle = "M50,0 A50,50 0 1 1 50,100 A50,50 0 1 1 50,0 Z"
        pieces = knife_fill(circle, [(-10, 30), (110, 30)])
        assert len(pieces) == 2
        for p in pieces:
            segs = [s for r, _c in parse_rings(p) for s in r]
            # only the knife's chord is straight; the rest is the arc's cubics
            assert sum(1 for s in segs if s[0] == "L") == 1, p
        assert sum(_area(p) for p in pieces) == pytest.approx(_area(circle), rel=1e-3)

    def test_knife_along_edge_is_noop(self):
        assert knife_fill(SQUARE, [(0, -10), (0, 110)]) is None
