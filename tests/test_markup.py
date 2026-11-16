"""Mark-up on a feedback message (artifact/markup.py): pins + sketches for the agent."""
from __future__ import annotations

import pytest

from cli_anything_inkstitch.artifact import markup as mk


class TestClean:
    def test_keeps_valid_drops_junk(self):
        c = mk.clean({
            "pins": [{"n": 1, "x": 10, "y": 20, "x_mm": 5.3, "y_mm": 10.6, "objects": ["elem_4"]},
                     {"x": "bad", "y": 1}],                              # no numeric x
            "strokes": [{"kind": "arrow", "points": [[0, 0], [30, 30]], "objects": ["elem_5"]},
                        {"kind": "pen", "points": [[1, 1]]}],            # < 2 points
            "view": {"x": 0, "y": 0, "w": 100, "h": 100}})
        assert len(c["pins"]) == 1 and c["pins"][0]["objects"] == ["elem_4"]
        assert len(c["strokes"]) == 1 and c["strokes"][0]["kind"] == "arrow"
        assert c["view"] == {"x": 0.0, "y": 0.0, "w": 100.0, "h": 100.0}

    def test_empty_is_none(self):
        assert mk.clean(None) is None
        assert mk.clean({}) is None
        assert mk.clean({"pins": [], "strokes": [{"points": [[1, 1]]}]}) is None   # nothing valid

    def test_kind_defaults_to_pen_and_nonfinite_dropped(self):
        c = mk.clean({"strokes": [{"points": [[0, 0], [float("inf"), 1], [2, 2]]}]})
        assert c["strokes"][0]["kind"] == "pen"
        assert c["strokes"][0]["points"] == [[0.0, 0.0], [2.0, 2.0]]     # the inf point is gone

    def test_summary(self):
        c = mk.clean({"pins": [{"x": 1, "y": 1}, {"x": 2, "y": 2}],
                      "strokes": [{"kind": "arrow", "points": [[0, 0], [1, 1]]}]})
        assert mk.summary(c) == "2 pins, 1 arrow"
        assert mk.summary(None) == ""


class TestRenderSnapshot:
    def _blocks(self):
        return [{"color": "#008a49", "paths": [[[i, 20] for i in range(0, 120, 2)]]},
                {"color": "#ffffff", "paths": [[[5, 5], [90, 8], [90, 40]]]}]

    def test_renders_a_png(self, tmp_path):
        c = mk.clean({"pins": [{"x": 40, "y": 22}],
                      "strokes": [{"kind": "arrow", "points": [[20, 10], [50, 30]]}]})
        out = mk.render_snapshot(c, tmp_path / "s.png", blocks=self._blocks())
        from PIL import Image
        img = Image.open(out)
        assert img.size[0] > 0 and img.size[1] > 0
        # the mark colour is somewhere in it
        assert any(abs(r - 224) < 30 and abs(g - 36) < 40 and abs(b - 94) < 40
                   for r, g, b in list(img.convert("RGB").getdata()))

    def test_plan_callable_used_when_no_blocks(self, tmp_path):
        calls = []
        def plan():
            calls.append(1)
            return {"blocks": self._blocks()}
        c = mk.clean({"pins": [{"x": 40, "y": 22}]})
        mk.render_snapshot(c, tmp_path / "s.png", plan=plan)
        assert calls == [1]

    def test_no_marks_raises(self, tmp_path):
        with pytest.raises(ValueError):
            mk.render_snapshot({"pins": [], "strokes": []}, tmp_path / "s.png", blocks=self._blocks())
