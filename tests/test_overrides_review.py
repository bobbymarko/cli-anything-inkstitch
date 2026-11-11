"""shapes-to-satin --overrides and tools review: the fix-it loop for a digitized logo.

Why: fixing a planned design used to mean a person reading renders and describing problem areas in
words. review names each problem by shape id and position (document mm); --overrides takes a fix per
shape (split points, ring seam position, underlay, density, pull compensation, fill angle, fill or
satin) and logs each applied fix with the shape's outline to a cases file, so the planner can be
taught from them later. Synthetic shapes only.
"""

from __future__ import annotations

import json
import math

import pytest
from click.testing import CliRunner

from cli_anything_inkstitch.binary import discover

try:
    import numpy  # noqa: F401
    import scipy.spatial  # noqa: F401
    import shapely  # noqa: F401
    HAVE_PLANNER = True
except ImportError:
    HAVE_PLANNER = False

needs_planner = pytest.mark.skipif(not HAVE_PLANNER, reason="satin extra not installed")
needs_binary = pytest.mark.skipif(discover() is None, reason="inkstitch binary not found")
K = 96 / 25.4


def _rect(x, y, w, h):
    return f"M {x * K:.3f},{y * K:.3f} L {(x + w) * K:.3f},{y * K:.3f} L {(x + w) * K:.3f},{(y + h) * K:.3f} L {x * K:.3f},{(y + h) * K:.3f} Z"


def _ring(cx, cy, ro, ri, n=64):
    o = [(cx + ro * math.cos(2 * math.pi * i / n), cy + ro * math.sin(2 * math.pi * i / n)) for i in range(n)]
    q = [(cx + ri * math.cos(-2 * math.pi * i / n), cy + ri * math.sin(-2 * math.pi * i / n)) for i in range(n)]
    f = lambda pts: "M " + " L ".join(f"{x * K:.3f},{y * K:.3f}" for x, y in pts) + " Z"
    return f(o) + " " + f(q)


def _doc(tmp_path, body, w=80, h=40):
    from cli_anything_inkstitch.cli import root
    svg = tmp_path / "t.svg"
    svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" '
                   f'viewBox="0 0 {w * K:.4f} {h * K:.4f}">{body}</svg>')
    proj = tmp_path / "t.json"
    assert CliRunner().invoke(root, ["document", "open", "--project", str(proj), "--svg", str(svg)]).exit_code == 0
    return svg, proj


def _s2s(proj, *opts):
    from cli_anything_inkstitch.cli import root
    r = CliRunner().invoke(root, ["--json", "tools", "shapes-to-satin", "--project", str(proj), *opts])
    assert r.exit_code == 0, r.output
    return json.loads(r.output)


class TestOverrideFile:
    def test_unknown_keys_and_values_are_refused(self, tmp_path):
        from cli_anything_inkstitch.commands.tools import _load_overrides
        from cli_anything_inkstitch.errors import UserError
        for bad in ({"a": {"splitt": "none"}}, {"a": {"underlay": "tatami"}}, {"a": {"stitch": "cross"}}):
            p = tmp_path / "o.json"
            p.write_text(json.dumps(bad))
            with pytest.raises(UserError):
                _load_overrides(p)

    def test_params(self):
        from cli_anything_inkstitch.commands.tools import _override_params
        p = _override_params({"underlay": "center+zigzag", "density_mm": 0.45, "pull_comp_mm": 0.3}, True)
        assert p == {"center_walk_underlay": "true", "contour_underlay": "false", "zigzag_underlay": "true",
                     "zigzag_spacing_mm": "0.45", "pull_compensation_mm": "0.3"}
        assert _override_params({"angle": 30}, False) == {"angle": "30"}


@needs_planner
class TestPlanOverrides:
    def test_split_points_cut_a_bar(self):
        from cli_anything_inkstitch.trace import glyph_columns as gc
        bar = [[(0, 0), (30, 0), (30, 4), (0, 4)]]
        one = gc.plan_shapes([("bar", bar)])[0]
        two = gc.plan_shapes([("bar", bar)], overrides={"bar": {"split": [(15.0, 2.0)]}})[0]
        assert len(one.columns) == 1 and len(two.columns) == 2

    def test_split_none_forces_one_column_through_a_corner(self):
        from cli_anything_inkstitch.trace import glyph_columns as gc
        ell = [[(0, 0), (14, 0), (14, 4), (4, 4), (4, 20), (0, 20)]]
        a = gc.plan_shapes([("l", ell)])[0]
        b = gc.plan_shapes([("l", ell)], overrides={"l": {"split": "none"}})[0]
        assert len(a.columns) == 2 and len(b.columns) == 1

    def test_ring_seam_moves(self):
        from cli_anything_inkstitch.trace import glyph_columns as gc
        n = 64
        ring = [[(10 * math.cos(2 * math.pi * i / n), 10 * math.sin(2 * math.pi * i / n)) for i in range(n)],
                [(7 * math.cos(-2 * math.pi * i / n), 7 * math.sin(-2 * math.pi * i / n)) for i in range(n)]]
        lt = gc.plan_shapes([("o", ring)], overrides={"o": {"seam": (0.0, -8.5)}})[0]
        c = lt.columns[0]
        start = ((c.left[0][0] + c.right[0][0]) / 2, (c.left[0][1] + c.right[0][1]) / 2)
        assert math.dist(start, (0.0, -8.5)) < 1.5               # starts at the bottom now
        assert math.dist(c.left[-2], c.left[0]) < 1e-6            # round once, then ~0.3 mm on
        assert 0.1 < math.dist(c.left[-1], c.left[0]) < 0.6


@needs_planner
@needs_binary
class TestLoop:
    def test_overrides_apply_by_id_and_hash_and_are_logged(self, tmp_path):
        svg, proj = _doc(tmp_path, f'<path id="a" d="{_rect(5, 5, 30, 4)}" style="fill:#2e7d32"/>'
                                   f'<path id="b" d="{_rect(5, 20, 30, 4)}" style="fill:#2e7d32"/>')
        first = _s2s(proj, "--no-one-column")
        hb = next(s["hash"] for s in first["shapes"] if s["shape"] == "b")
        sub = tmp_path / "second"
        sub.mkdir()
        svg2, proj2 = _doc(sub, f'<path id="a" d="{_rect(5, 5, 30, 4)}" style="fill:#2e7d32"/>'
                                f'<path id="b" d="{_rect(5, 20, 30, 4)}" style="fill:#2e7d32"/>')
        ov = tmp_path / "ov.json"
        ov.write_text(json.dumps({"a": {"underlay": "center", "density_mm": 0.5, "reason": "test a"},
                                  f"sha:{hb}": {"stitch": "fill", "angle": 30, "reason": "test b"}}))
        cases = tmp_path / "cases.jsonl"
        out = _s2s(proj2, "--overrides", str(ov), "--cases", str(cases))
        by = {s["shape"]: s for s in out["shapes"]}
        assert by["a"]["stitch"] == "satin" and by["b"]["stitch"] == "fill" and out["overrides_applied"] == 2
        from lxml import etree
        t = etree.parse(str(svg2)).getroot()
        a0 = next(e for e in t.iter() if e.get("id") == "a_c0")
        ns = "{http://inkstitch.org/namespace}"
        assert a0.get(ns + "center_walk_underlay") in ("true", "True")
        assert a0.get(ns + "contour_underlay") in ("false", "False")
        assert a0.get(ns + "zigzag_spacing_mm") == "0.5"
        bf = next(e for e in t.iter() if e.get("id") == "b")
        assert bf.get(ns + "angle") == "30"
        logged = [json.loads(line) for line in cases.read_text().splitlines()]
        assert {c["reason"] for c in logged} == {"test a", "test b"}
        assert all(c["hash"] and c["outline_mm"] and c["result"]["stitch"] for c in logged)
        b_case = next(c for c in logged if c["shape"] == "b")
        xs = [p[0] for p in b_case["outline_mm"][0]]
        ys = [p[1] for p in b_case["outline_mm"][0]]
        assert abs(min(xs) - 5) < 0.2 and abs(min(ys) - 20) < 0.2       # document mm, y down

    def test_review_names_shapes_and_places_flags(self, tmp_path):
        from cli_anything_inkstitch.cli import root
        svg, proj = _doc(tmp_path, f'<path id="o" d="{_ring(20, 20, 10, 7)}" style="fill:#e8c21a;fill-rule:evenodd"/>'
                                   f'<path id="bar" d="{_rect(45, 18, 30, 4)}" style="fill:#2e7d32"/>')
        _s2s(proj)
        png = tmp_path / "r.png"
        r = CliRunner().invoke(root, ["--json", "tools", "review", "--project", str(proj), "--out", str(png)])
        assert r.exit_code == 0, r.output
        out = json.loads(r.output)
        assert out["color_changes"] == 1 and png.exists()
        ring = [f for f in out["flags"] if f["kind"] == "ring_seam"]
        assert len(ring) == 1 and ring[0]["shape"] == "o"
        x, y = ring[0]["at_mm"]
        assert 10 < x < 30 and 10 < y < 30                                # on the ring, document mm
        assert not [f for f in out["flags"] if f["kind"] in ("hard_seam", "long_stitch")]
