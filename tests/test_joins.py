"""Tidying where a shape's satin columns meet (trace/joins.py), on synthetic shapes.

A bar with an arm running 2 mm into it, a V's legs overlapping, a wedge left between an end cut and its
neighbour, a crotch where two strokes part over a straight outline, a blunt cap whose rungs fan from one
corner, and a pointed tip that must keep its point. All mm, y up.
"""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("shapely")

from cli_anything_inkstitch.trace import joins  # noqa: E402
from cli_anything_inkstitch.trace.glyph_columns import PlannedColumn, PlannedLetter  # noqa: E402


def _col(left, right):
    return PlannedColumn(left=[tuple(map(float, p)) for p in left], right=[tuple(map(float, p)) for p in right])


def _letter(contour, cols):
    return PlannedLetter("t", cols, [[tuple(map(float, p)) for p in contour]], True, {})


def _overlap(a, b):
    return joins._poly(a).intersection(joins._poly(b))


def _bare(pl):
    return joins._bare(joins._outline(pl.contours), pl.columns)


def _tee():
    """A 6 mm bar along y = 0..6, x = 0..40, and an arm (x = 18..24) coming down from y = 20 and running
    2 mm into the bar (to y = 4). The outline is the union of the two."""
    bar = _col([(0, 6), (40, 6)], [(0, 0), (40, 0)])
    arm = _col([(18, 20), (18, 4)], [(24, 20), (24, 4)])
    outline = [(0, 0), (40, 0), (40, 6), (24, 6), (24, 20), (18, 20), (18, 6), (0, 6)]
    return _letter(outline, [bar, arm])


class TestTrimOverlaps:
    def test_arm_cut_back_to_keep_mm(self):
        pl = _tee()
        before = _overlap(*pl.columns)
        assert joins._depth(before) == pytest.approx(2.0, abs=0.05)
        bare0 = _bare(pl)
        joins.trim_overlaps(pl)
        after = _overlap(*pl.columns)
        assert joins._depth(after) == pytest.approx(joins.KEEP_MM, abs=0.05)
        assert _bare(pl) <= bare0 + joins.BARE_TOL_MM2
        assert pl.check["joins_trimmed"][0]["column"] == 1
        # the arm's new end lies along the bar's edge, 0.4 mm in: both rail ends at y = 5.6
        arm = pl.columns[1]
        assert arm.left[-1][1] == pytest.approx(6 - joins.KEEP_MM, abs=0.02)
        assert arm.right[-1][1] == pytest.approx(6 - joins.KEEP_MM, abs=0.02)

    def test_shallow_overlap_left_alone(self):
        pl = _tee()
        arm = pl.columns[1]
        arm.left[-1], arm.right[-1] = (18, 5.6), (24, 5.6)
        joins.trim_overlaps(pl)
        assert "joins_trimmed" not in pl.check

    def test_slanted_end_with_one_corner_deep(self):
        # the arm's end is cut on a slant: its right corner 2 mm into the bar, its left only touching it
        pl = _tee()
        arm = pl.columns[1]
        arm.left[-1], arm.right[-1] = (18, 6.2), (24, 4.0)
        joins.trim_overlaps(pl)
        x = _overlap(*pl.columns)
        assert joins._depth(x) <= joins.KEEP_MM + 0.1

    def test_closed_ring_never_trimmed(self):
        pl = _tee()
        pl.columns[1].closed = True
        pl.columns[0].closed = True
        joins.trim_overlaps(pl)
        assert "joins_trimmed" not in pl.check


class TestCloseWedges:
    def test_wedge_between_end_cut_and_neighbour_closed(self):
        # a bar, and an arm whose end stops short of the bar on one side: a 3 mm-long sliver of fabric
        bar = _col([(0, 6), (40, 6)], [(0, 0), (40, 0)])
        arm = _col([(18, 20), (18, 6.4)], [(24, 20), (24, 8.4)])
        outline = [(0, 0), (40, 0), (40, 6), (24, 6), (24, 20), (18, 20), (18, 6), (0, 6)]
        pl = _letter(outline, [bar, arm])
        bare0 = _bare(pl)
        assert bare0 > 4
        joins.close_wedges(pl)
        assert _bare(pl) < 0.5 * bare0
        assert pl.check["wedges_closed"]
        # nothing pushed out of the outline
        ink = joins._outline(pl.contours).buffer(0.1)
        assert joins._poly(pl.columns[1]).difference(ink).area < 0.1

    def test_crotch_over_straight_outline_filled(self):
        # two strokes side by side that part just above a straight base: between their sides, a 2 mm
        # triangle the outline covers (the art goes straight across there)
        a = _col([(0, 0), (0, 2), (0, 20)], [(6, 0), (7, 2), (7, 20)])
        b = _col([(8, 0), (7, 2), (7, 20)], [(14, 0), (14, 2), (14, 20)])
        pl = _letter([(0, 0), (14, 0), (14, 20), (0, 20)], [a, b])
        bare0 = _bare(pl)
        assert bare0 > 1.0
        joins.close_wedges(pl)
        assert _bare(pl) < 0.5 * bare0
        ink = joins._outline(pl.contours).buffer(0.1)
        assert all(joins._poly(c).difference(ink).area < 0.1 for c in pl.columns)


def _fan_cap():
    """A 6 mm-wide strand along +x ending in a blunt cap whose last rungs all leave from its top corner
    (a flat top edge to x = 22, the bottom rail curving up to meet it): a one-sided fan."""
    left, right = [], []
    for x in np.arange(0, 16.1, 1.0):
        left.append((x, 6.0))
        right.append((x, 0.0))
    for a in np.linspace(0, 60, 6)[1:]:
        t = math.radians(a)
        left.append((22.0, 6.0))
        right.append((16 + 6 * math.sin(t), 6 - 6 * math.cos(t)))
    contour = [(0, 0), (16, 0)] + [(16 + 6.2 * math.sin(math.radians(a)), 6 - 6.2 * math.cos(math.radians(a)))
                                   for a in range(5, 90, 5)] + [(22.2, 6), (0, 6)]
    return _letter(contour, [_col(left, right)])


class TestStraightenTips:
    def test_one_sided_fan_straightened(self):
        pl = _fan_cap()
        c = pl.columns[0]
        ref = math.degrees(math.atan2(c.right[5][1] - c.left[5][1], c.right[5][0] - c.left[5][0])) % 180
        joins.straighten_tips(pl, bare_tol=1.0)
        assert pl.check.get("tips_straightened")
        c = pl.columns[0]
        for a, b in list(zip(c.left, c.right))[-4:]:
            ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180
            assert joins._angdiff(ang, ref) < 10

    def test_pointed_tip_kept(self):
        # a tapering strand that narrows to a point on both rails: not straightened
        left = [(x, 3 + 3 * (1 - x / 20)) for x in np.arange(0, 20.1, 1.0)]
        right = [(x, 3 - 3 * (1 - x / 20)) for x in np.arange(0, 20.1, 1.0)]
        left[-1] = right[-1] = (20.0, 3.0)
        contour = [(0, 0), (20, 3), (0, 6)]
        pl = _letter(contour, [_col(left, right)])
        joins.straighten_tips(pl, bare_tol=1.0)
        assert "tips_straightened" not in pl.check

    def test_lettering_size_columns_left_alone(self):
        pl = _fan_cap()
        c = pl.columns[0]
        c.left = [(x, y / 3) for x, y in c.left]
        c.right = [(x, y / 3) for x, y in c.right]
        pl.contours = [[(x, y / 3) for x, y in pl.contours[0]]]
        joins.straighten_tips(pl, bare_tol=1.0)
        assert "tips_straightened" not in pl.check


def test_tidy_joins_keeps_a_clean_shape():
    bar = _col([(0, 6), (40, 6)], [(0, 0), (40, 0)])
    pl = _letter([(0, 0), (40, 0), (40, 6), (0, 6)], [bar])
    before = (list(bar.left), list(bar.right))
    joins.tidy_joins(pl)
    assert (pl.columns[0].left, pl.columns[0].right) == before


# ---------------------------------------------------------------- S spine: rungs square to the middle

def _offset_path(center, w):
    c = np.asarray(center, float)
    tg = np.gradient(c, axis=0)
    tg /= np.hypot(*tg.T)[:, None]
    nr = np.column_stack([-tg[:, 1], tg[:, 0]])
    return c + nr * w, c - nr * w


def _s_column():
    """A 6 mm column: a tight 180-degree bowl (centre-line radius 4) and then a straight 16 mm spine.
    Its rails are paired in proportion over their whole length, the way outline_column pairs two
    outline arcs: the outer rail is longer round the bowl, so on the spine the rungs lean."""
    th = np.linspace(math.pi / 2, -math.pi / 2, 60)
    bowl = np.column_stack([4 * np.cos(th), 4 * np.sin(th)])
    spine = np.column_stack([np.linspace(0, -16, 33)[1:], np.full(32, -4.0)])
    center = np.vstack([bowl, spine])
    lft, rgt = _offset_path(center, 3.0)
    from shapely.geometry import LineString

    Lr, Rr = LineString(lft), LineString(rgt)
    n = 80
    pairs = [(Lr.interpolate(k / n, normalized=True).coords[0], Rr.interpolate(k / n, normalized=True).coords[0])
             for k in range(n + 1)]
    return pairs


def _spine_dev(pairs):
    """Worst angle (deg) between a rung and the vertical (the spine runs along x) on the spine's middle."""
    worst = 0.0
    for a, b in pairs:
        mx = (a[0] + b[0]) / 2
        if -13 < mx < -3 and (a[1] + b[1]) / 2 < -1:
            v = np.subtract(b, a)
            worst = max(worst, math.degrees(math.acos(min(1.0, abs(v[1]) / (np.hypot(*v) or 1)))))
    return worst


def test_proportional_pairing_leans_on_the_spine():
    assert _spine_dev(_s_column()) > 10       # the defect the fix is for


def test_medial_rungs_square_on_the_spine():
    from cli_anything_inkstitch.trace.glyph_columns import medial_rungs, spread_turns

    pairs = medial_rungs(_s_column())
    assert pairs is not None
    assert _spine_dev(pairs) < 3
    # spread_turns tilts only the bowl: the spine stays square
    spread, _ = spread_turns(pairs)
    assert _spine_dev(spread) < 3


class TestSnapRails:
    """snap_rails squares a near-straight, near-axis rail; it leaves a real slant alone.

    The companion rail in each case is a clear slant so only the rail under test can snap.
    """

    def test_near_vertical_rail_snapped_to_one_x(self):
        # a 6 mm stem whose left rail drifts 0.05 mm over its length (a source-art taper/wobble)
        left = [(10.00, 0), (10.02, 2), (10.03, 4), (10.05, 6)]
        right = [(12.0, 0), (12.3, 2), (12.6, 4), (12.9, 6)]   # an 8-degree slant: untouched
        pl = _letter([(10, 0), (12.9, 6), (10.05, 6)], [_col(left, right)])
        assert joins.snap_rails(pl) == 1
        xs = {round(p[0], 6) for p in pl.columns[0].left}
        assert len(xs) == 1                                   # the left rail is exactly vertical now
        assert abs(next(iter(xs)) - 10.025) < 0.02            # at the rail's mean x
        assert pl.columns[0].right == right                   # the slant is left alone

    def test_near_horizontal_rail_snapped_to_one_y(self):
        top = [(0, 6.0), (5, 6.03), (10, 6.05)]
        bot = [(0, 4.0), (5, 4.3), (10, 4.6)]                 # a slant: untouched
        pl = _letter([(0, 4), (10, 4.6), (10, 6.05), (0, 6)], [_col(top, bot)])
        assert joins.snap_rails(pl) == 1
        ys = {round(p[1], 6) for p in pl.columns[0].left}
        assert len(ys) == 1

    def test_genuine_slant_left_alone(self):
        # a 20-degree italic stem: straight, but well off vertical -> both rails untouched
        import math as _m
        dx = _m.tan(_m.radians(20))
        left = [(0, 0), (dx * 2, 2), (dx * 4, 4), (dx * 6, 6)]
        right = [(2, 0), (2 + dx * 2, 2), (2 + dx * 4, 4), (2 + dx * 6, 6)]
        pl = _letter([(0, 0), (2, 0), (2 + dx * 6, 6), (dx * 6, 6)], [_col(left, right)])
        assert joins.snap_rails(pl) == 0
        assert pl.columns[0].left[0][0] != pl.columns[0].left[-1][0]

    def test_curved_rail_left_alone(self):
        # near-vertical end-to-end but each rail bows ~0.4 mm in the middle -> not near-straight
        left = [(10, 0), (10.4, 3), (10, 6)]
        right = [(12, 0), (12.4, 3), (12, 6)]
        pl = _letter([(10, 0), (12, 0), (12, 6), (10, 6)], [_col(left, right)])
        assert joins.snap_rails(pl) == 0

    def test_ring_skipped(self):
        c = _col([(10, 0), (10.03, 6)], [(12, 0), (12.3, 6)])
        c.closed = True
        pl = _letter([(10, 0), (12, 0), (12.3, 6), (10.03, 6)], [c])
        assert joins.snap_rails(pl) == 0


def _leg(tip, top, w=1.0, n=11):
    """A straight satin column of half-width w from `tip` to `top` (mm, y up)."""
    tip, top = np.array(tip, float), np.array(top, float)
    d = (top - tip) / np.hypot(*(top - tip)); perp = np.array([-d[1], d[0]])
    L = [tuple(tip + (top - tip) * k / (n - 1) + perp * w) for k in range(n)]
    R = [tuple(tip + (top - tip) * k / (n - 1) - perp * w) for k in range(n)]
    return joins.PlannedColumn(left=L, right=R) if hasattr(joins, "PlannedColumn") else _col(L, R)


def _v_letter(a, b):
    from shapely.ops import unary_union
    uni = unary_union([joins._poly(a), joins._poly(b)])
    return _letter(list(uni.exterior.coords), [a, b])


class TestAcuteVFill:
    """A sharp inner-V (two columns converging at an acute point) fills to the point rather than
    being trimmed to a notch or a seam — the ES-matching treatment."""

    def test_acute_v_is_detected(self):
        a, b = _leg((0, 0), (-4, 10)), _leg((0, 0), (4, 10))     # ~33 deg apart: a sharp V
        assert joins._acute_v(a, b) is True

    def test_wide_corner_is_not_an_acute_v(self):
        a, b = _leg((0, 0), (-8.66, 5)), _leg((0, 0), (8.66, 5))  # 120 deg: a wide corner, not a V
        assert joins._acute_v(a, b) is False

    def test_butt_junction_is_not_an_acute_v(self):
        pl = _tee()                                              # an arm meeting a bar at 90 deg
        assert joins._acute_v(pl.columns[0], pl.columns[1]) is False

    def test_acute_v_overlap_is_filled_not_trimmed(self):
        a, b = _leg((0, 0), (-4, 10)), _leg((0, 0), (4, 10))
        before = _overlap(a, b).area
        assert joins._depth(_overlap(a, b)) > joins.MAX_DEPTH_MM   # deep enough that trim would fire
        pl = _v_letter(a, b)
        joins.trim_overlaps(pl)
        assert pl.check.get("acute_v_fills") == [[0, 1]]          # recognised and left to fill
        assert "joins_trimmed" not in pl.check                    # not trimmed
        after = joins._poly(pl.columns[0]).intersection(joins._poly(pl.columns[1])).area
        assert after == pytest.approx(before, abs=1e-6)           # the overlap is preserved

    def test_wide_corner_overlap_is_not_marked_acute(self):
        a, b = _leg((0, 0), (-8.66, 5)), _leg((0, 0), (8.66, 5))
        pl = _v_letter(a, b)
        joins.trim_overlaps(pl)
        assert "acute_v_fills" not in pl.check                    # a wide corner is handled normally
