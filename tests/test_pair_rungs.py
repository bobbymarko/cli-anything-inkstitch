"""Pairing rungs for a rung-less satin whose rails have different node counts (gate twist_risk).

The engine invents rungs node-for-node from the rails (lib/elements/satin_column.py
_synthesize_rungs), so a hand edit that adds nodes to one rail pairs unrelated points and the
zigzag slants. Resampling the rails would move the drawn outline; explicit square rungs fix the
pairing with the rails untouched. Synthetic columns only."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("shapely")

from cli_anything_inkstitch.artifact.design_model import apply_edits  # noqa: E402
from cli_anything_inkstitch.artifact.gate import flatten_path, run_gate  # noqa: E402
from cli_anything_inkstitch.binary import discover  # noqa: E402
from cli_anything_inkstitch.trace.rungs import pair_unpaired_rails, pairing_rungs  # noqa: E402

from tests.test_artifact_gate import make_project  # noqa: E402

# a 40 x 4 mm column (1 unit = 1 mm); rail A has extra nodes bunched at its start, so node i of
# A pairs with node i of B far down the column
UNEQUAL = "M10,10 L11,10 L12,10 L13,10 L50,10 M10,14 L48,14 L50,14"


def _rungs(d):
    from cli_anything_inkstitch.artifact.design_model import split_subpaths
    return [flatten_path(s) for s in split_subpaths(d)[2:]]


class TestPairingRungs:
    def test_unequal_rails_get_square_rungs_and_rails_are_untouched(self):
        d, n = pair_unpaired_rails(UNEQUAL, 1.0)
        assert n >= 3 and d.startswith(UNEQUAL)          # rails' text kept byte for byte
        for r in _rungs(d):
            (x0, y0), (x1, y1) = r[0], r[-1]
            assert abs(x1 - x0) < 0.05                       # square across a horizontal column
            assert min(y0, y1) < 10 and max(y0, y1) > 14     # crosses both rails

    def test_rungs_are_spaced_and_never_two(self):
        rungs = pairing_rungs([(0, 0), (40, 0)], [(0, 4), (20, 4), (40, 4)], 1.0)
        xs = sorted(a[0] for a, _ in rungs)
        assert len(rungs) != 2 and all(b - a > 2.0 for a, b in zip(xs, xs[1:]))
        short = pairing_rungs([(0, 0), (6, 0)], [(0, 4), (3, 4), (6, 4)], 1.0)
        assert len(short) != 2

    def test_equal_counts_or_existing_rungs_are_left_alone(self):
        equal = "M10,10 L50,10 M10,14 L50,14"
        assert pair_unpaired_rails(equal, 1.0) == (equal, 0)
        runged = UNEQUAL + " M30,9 L30,15"
        assert pair_unpaired_rails(runged, 1.0) == (runged, 0)

    def test_slanted_end_keeps_its_fan(self):
        # rail A runs on along a 45 degree end cut for its last 4 mm: no rung is put across the cut
        a = [(0, 0), (30, 0), (34, 4)]
        b = [(0, 4), (10, 4), (20, 4), (34, 4)]
        rungs = pairing_rungs(a, b, 1.0)
        assert rungs and max(max(p[0], q[0]) for p, q in rungs) < 30.5

    def test_curved_column_rungs_do_not_cross(self):
        from shapely.geometry import LineString
        arc = lambda r, n: [(r * math.cos(math.pi * i / n), r * math.sin(math.pi * i / n)) for i in range(n + 1)]
        rungs = pairing_rungs(arc(10, 40), arc(14, 23), 1.0)
        assert len(rungs) >= 5
        lines = [LineString(r) for r in rungs]
        assert not any(a.intersects(b) for i, a in enumerate(lines) for b in lines[i + 1:])
        for (p, q) in rungs:                                  # radial: square to the arc
            mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            dx, dy = q[0] - p[0], q[1] - p[1]
            assert abs(mx * dy - my * dx) / (math.hypot(mx, my) * math.hypot(dx, dy)) < 0.1


class TestSetPathPairsRails:
    def test_hand_edit_with_unequal_rails_passes_the_gate(self, tmp_path):
        body = ('<path id="s" fill="none" stroke="#000" inkstitch:satin_column="True" '
                'd="M10,10 L50,10 M10,14 L50,14"/>')
        proj = make_project(tmp_path, body)
        out = apply_edits(proj, [{"op": "set_path", "id": "s", "d": UNEQUAL}])
        assert out["results"][0]["rungs_added"] >= 3
        result = run_gate(proj)
        assert not any(f["check"] == "twist_risk" for f in result["errors"])

    def test_a_fill_or_stroke_path_is_not_touched(self, tmp_path):
        body = '<path id="r" fill="none" stroke="#000" d="M10,10 L50,10"/>'
        proj = make_project(tmp_path, body)
        apply_edits(proj, [{"op": "set_path", "id": "r", "d": UNEQUAL}])
        assert (tmp_path / "design.svg").read_text().count(" M") == UNEQUAL.count(" M")


@pytest.mark.skipif(discover() is None, reason="Ink/Stitch binary not installed")
class TestEngineSewsSquare:
    """Behavioural proof (CLAUDE.md rule 3): the engine's top layer on the unequal column slants
    without rungs and runs square with the added ones."""

    def _slant(self, tmp_path, d):
        from cli_anything_inkstitch.artifact.design_model import extract_stitch_blocks, stitch_plan_svg
        body = f'<path id="s" fill="none" stroke="#000" inkstitch:satin_column="True" d="{d}"/>'
        proj = make_project(tmp_path, body)
        blocks = extract_stitch_blocks(stitch_plan_svg(proj))["blocks"]
        seg = [(a, c) for b in blocks for p in b["paths"] for a, c in zip(p, p[1:])
               if abs(c[1] - a[1]) > 3.0]                     # zigzag crossing the 4 mm column
        return sorted(abs(c[0] - a[0]) for a, c in seg)[len(seg) // 2]   # median x travel per cross

    def test_rungs_square_the_zigzag(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        bare = self._slant(tmp_path / "a", UNEQUAL)
        paired = self._slant(tmp_path / "b", pair_unpaired_rails(UNEQUAL, 1.0)[0])
        assert paired < 0.6 < bare
