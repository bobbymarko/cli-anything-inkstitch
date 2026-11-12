"""Tight turns: spread the inner ends of the rungs instead of pinching them at one point.

Why: an S re-joined into one column with outline rails kept the planner's rungs, and at the inside
of a bowl a dozen consecutive pairs had their inner ends within a millimetre while the outer ends
swept the whole bowl (inner rail 1.3 mm against 12 mm). The engine spreads stitches in proportion
along each rail between pairs, so every stitch there started from one point: a pinch, thread piled
inside and a visible lump. A digitizer tilts the rungs into the turn so the inner ends spread evenly
along the inner curve, and lets short stitches thin the inside. spread_turns does the tilting; the
fabric presets turn short stitches on; turns() lists a tight turn as a turn, not a fan; and a
`rungs` override lets a reviewer place the rungs by hand. Synthetic shapes only.
"""

from __future__ import annotations

import json
import math

import pytest

try:
    import numpy as np
    import shapely  # noqa: F401
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False

pytestmark = pytest.mark.skipif(not HAVE_SHAPELY, reason="satin extra not installed")


def _u_pinched(r_in=1.0, r_out=8.0, n_turn=13, n_leg=10, leg=12.0):
    """A U: two straight legs joined by a half-turn about (0, 0), inner radius r_in, outer r_out.
    The turn's pairs are planned badly, the way the bowl was: their inner ends all sit within a
    small stretch at the inner apex while the outer ends sweep the half-turn."""
    pairs = []
    for k in range(n_leg):                                   # left leg, going down to the turn
        y = leg - leg * k / n_leg
        pairs.append(((-r_in, y), (-r_out, y)))
    apex = math.pi                                           # inner ends bunched around the apex
    for k in range(n_turn + 1):
        a_out = math.pi + math.pi * k / n_turn               # outer sweeps pi .. 2pi
        a_in = apex + 0.5 * math.pi + (k / n_turn - 0.5) * 0.25  # inner: a 0.25 rad stretch
        pairs.append(((r_in * math.cos(a_in), r_in * math.sin(a_in)),
                      (r_out * math.cos(a_out), r_out * math.sin(a_out))))
    for k in range(1, n_leg + 1):                            # right leg, back up
        y = leg * k / n_leg
        pairs.append(((r_in, y), (r_out, y)))
    return pairs


def _ratios(pairs):
    from cli_anything_inkstitch.trace import glyph_columns as gc
    sL, sR = gc._cum([p[0] for p in pairs]), gc._cum([p[1] for p in pairs])
    return [r for r, hi in gc._span_ratios(sL, sR) if hi >= gc.TURN_MIN_MM]


class TestSpreadTurns:
    def test_a_pinched_u_turn_spreads_its_inner_ends(self):
        from cli_anything_inkstitch.trace import column_checks as cc
        from cli_anything_inkstitch.trace import glyph_columns as gc
        pairs = _u_pinched()
        assert min(_ratios(pairs)) < 0.05 and cc.fans(pairs)     # the pinch, as planned
        new, changed = gc.spread_turns(pairs)
        assert changed
        # inner r 1 against outer r 8 is 1:8 around the turn itself; spread into the legs as far as
        # the tilt allows, every span now moves both rails, far from the pinch's 1:20+
        assert min(_ratios(new)) >= 0.18
        wide, _ = gc.spread_turns(_u_pinched(r_in=2.5))           # a gentler turn reaches the target
        assert min(_ratios(wide)) >= gc.TURN_RATIO * 0.98
        assert cc.fans(new) == []
        assert gc._crossed(new) == 0
        assert new[0] == pairs[0] and new[-1] == pairs[-1]     # the column's cuts don't move
        assert gc._max_tilt_deg(new) <= gc.TURN_MAX_TILT_DEG + 1e-6
        # the inner ends now cover much more of the inner curve than the pinch did: spacing spread
        inner_moves = [math.dist(a[0], b[0]) for a, b in zip(new, new[1:])]
        assert max(inner_moves) / max(min(inner_moves), 1e-9) < 20

    def test_rails_are_unchanged(self):
        """Pairs move along the rails, never off them: the outline sewn is the same."""
        from shapely.geometry import LineString, Point
        from cli_anything_inkstitch.trace import glyph_columns as gc
        pairs = _u_pinched()
        new, _ = gc.spread_turns(pairs)
        L, R = LineString([p[0] for p in pairs]), LineString([p[1] for p in pairs])
        assert max(L.distance(Point(p[0])) for p in new) < 1e-6
        assert max(R.distance(Point(p[1])) for p in new) < 1e-6

    def test_an_even_curve_is_left_alone(self):
        from cli_anything_inkstitch.trace import glyph_columns as gc
        ring = [((3 * math.cos(a), 3 * math.sin(a)), (7 * math.cos(a), 7 * math.sin(a)))
                for a in np.linspace(0, math.pi, 30)]
        new, changed = gc.spread_turns(ring)
        assert not changed and new == ring

    def test_an_s_with_two_pinched_bowls(self):
        """Two opposite turns in one column (an S): both spread, neither end moves."""
        from cli_anything_inkstitch.trace import column_checks as cc
        from cli_anything_inkstitch.trace import glyph_columns as gc
        u = _u_pinched()
        # the U turned half round about (0, 12) (its top-centre) starts where the U ends and turns
        # the other way: one continuous column with two pinched bowls
        second = [((-l[0], 24.0 - l[1]), (-r[0], 24.0 - r[1])) for l, r in u[1:]]
        assert math.dist(u[-1][0], (1.0, 12.0)) < 1e-9 and math.dist(second[0][0], (1.0, 13.2)) < 1e-9
        s = u + second
        assert len(cc.fans(s)) >= 2
        new, changed = gc.spread_turns(s)
        assert changed and cc.fans(new) == [] and gc._crossed(new) == 0
        assert new[0] == s[0] and new[-1] == s[-1]


class TestTurnCheck:
    def test_a_spread_turn_is_a_turn_not_a_fan(self):
        from cli_anything_inkstitch.trace import column_checks as cc
        from cli_anything_inkstitch.trace import glyph_columns as gc
        new, _ = gc.spread_turns(_u_pinched())
        assert cc.fans(new) == [] and len(cc.turns(new)) >= 1

    def test_a_pivot_is_still_a_fan(self):
        from cli_anything_inkstitch.trace import column_checks as cc
        pivot = [((0.0, 0.0), (4 * math.cos(a), 4 * math.sin(a))) for a in np.linspace(0, math.pi / 2, 12)]
        assert len(cc.fans(pivot)) == 1 and cc.turns(pivot) == []

    def test_an_ordinary_curve_is_neither(self):
        from cli_anything_inkstitch.trace import column_checks as cc
        ring = [((3 * math.cos(a), 3 * math.sin(a)), (7 * math.cos(a), 7 * math.sin(a)))
                for a in np.linspace(0, math.pi / 2, 30)]
        assert cc.fans(ring) == [] and cc.turns(ring) == []


class TestPlaceRungs:
    def test_hand_rungs_become_anchors(self):
        from cli_anything_inkstitch.trace import glyph_columns as gc
        bar = [((x, 0.0), (x, 4.0)) for x in np.linspace(0, 20, 11)]
        lt = gc.PlannedLetter("bar", [gc.PlannedColumn([p[0] for p in bar], [p[1] for p in bar])],
                              [[(0, 0), (20, 0), (20, 4), (0, 4)]], True, {})
        # one slanted rung across the bar at x 8 .. 12, and one that misses it
        lt = gc.place_rungs(lt, [((8.0, -0.5), (12.0, 4.5)), ((30.0, 0.0), (30.0, 4.0))])
        c = lt.columns[0]
        assert lt.check["rungs_placed"] == 1 and lt.check["rungs_ignored"] == 1
        pairs = list(zip(c.left, c.right))
        assert any(math.dist(l, (8.4, 0.0)) < 0.05 and math.dist(r, (11.6, 4.0)) < 0.05 for l, r in pairs)
        assert pairs[0] == ((0.0, 0.0), (0.0, 4.0)) and pairs[-1] == ((20.0, 0.0), (20.0, 4.0))

    def test_override_file_accepts_the_new_keys(self, tmp_path):
        from cli_anything_inkstitch.commands.tools import _load_overrides, _override_params
        from cli_anything_inkstitch.errors import UserError
        p = tmp_path / "o.json"
        p.write_text(json.dumps({"a": {"rungs": [[[1, 2], [3, 4]]], "short_stitch_inset": "30 50",
                                       "short_stitch_distance_mm": 0.25}}))
        assert "a" in _load_overrides(p)
        p.write_text(json.dumps({"a": {"rungs": [[1, 2, 3, 4]]}}))
        with pytest.raises(UserError):
            _load_overrides(p)
        assert _override_params({"short_stitch_inset": [30, 50], "short_stitch_distance_mm": 0.3}, True) == {
            "short_stitch_inset": "30 50", "short_stitch_distance_mm": "0.3"}


class TestFabricShortStitches:
    def test_twill_turns_short_stitches_on_below_the_spacing(self):
        from cli_anything_inkstitch.commands.tools import FABRICS, _lettering_satin_params
        p = _lettering_satin_params([3.0], fabric="twill")
        assert p["short_stitch_inset"] == "30 50"
        # above the rail spacing every stitch would be shortened and the column would thin
        assert float(p["short_stitch_distance_mm"]) < FABRICS["twill"]["spacing_mm"]

    def test_standard_keeps_its_old_output(self):
        from cli_anything_inkstitch.commands.tools import _lettering_satin_params
        p = _lettering_satin_params([3.0], fabric="standard")
        assert "short_stitch_inset" not in p and "short_stitch_distance_mm" not in p


def _slanted_end_bar(theta_deg=40.0, length=20.0, w=1.0, n=21):
    """A straight bar up +y, width 2w, whose y=0 end is cut on a slant of theta_deg (the end pair
    leans that far from square); the rest of the pairs are square. Left to the engine the rungs turn
    the whole slant in the first segment: the pinch ease_end spreads out."""
    import math
    t = math.tan(math.radians(theta_deg))
    pairs = [((-w, -w * t), (w, w * t))]                     # the slanted cut
    for i in range(1, n):
        y = length * i / (n - 1)
        pairs.append(((-w, y), (w, y)))                      # square above the cut
    return pairs


class TestEaseEnds:
    def test_slanted_end_tilt_is_spread_out(self):
        from cli_anything_inkstitch.trace import glyph_columns as gc
        pairs = _slanted_end_bar()
        before = gc._head_turn_tilt(pairs)
        eased = gc.ease_end(pairs)
        assert eased is not None
        after = gc._head_turn_tilt(eased)
        assert after < before - 10                           # the turn is eased, not abrupt
        assert gc._crossed(eased) == 0                       # no twisted rungs
        assert eased[0] == pairs[0]                          # the end cut itself is not moved
        assert eased[-1] == pairs[-1]

    def test_square_end_is_left_alone(self):
        from cli_anything_inkstitch.trace import glyph_columns as gc
        square = [((-1.0, float(i)), (1.0, float(i))) for i in range(21)]
        assert gc.ease_end(square) is None                   # nothing to ease on a square end

    def test_letter_ends_keeps_only_improving_ends(self):
        from cli_anything_inkstitch.trace import column_checks as cc
        from cli_anything_inkstitch.trace import glyph_columns as gc
        pairs = _slanted_end_bar()
        c = gc.PlannedColumn(left=[p[0] for p in pairs], right=[p[1] for p in pairs])
        pl = gc.PlannedLetter("t", [c], [[(-1, -1), (1, 1), (1, 20), (-1, 20)]], True, {})
        before_fans = len(cc.fans(list(zip(c.left, c.right))))
        gc.ease_letter_ends(pl)
        new = list(zip(pl.columns[0].left, pl.columns[0].right))
        assert pl.check.get("eased_ends") == [0]
        assert len(cc.fans(new)) <= before_fans
        assert gc._crossed(new) == 0
