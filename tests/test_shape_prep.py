"""shapes-to-satin input cleanup: CSS-class fills, duplicate copies, thread merging and order.

Why: a large back logo exported from a design program digitized at about twice the stitches of the
hand-digitized production file, with several times its colour changes. The export drew every shape
twice in the same place (one shape in two greens, the darker on top), set fills by CSS class, and
used two greens a few ΔE apart for what is one thread. All fixtures here are synthetic.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.trace import shape_prep as sp

try:
    import shapely  # noqa: F401
    import cli_anything_inkstitch.planner.art_columns  # noqa: F401
    import scipy.spatial  # noqa: F401
    HAVE_PLANNER = True
except ImportError:
    HAVE_PLANNER = False

needs_planner = pytest.mark.skipif(not HAVE_PLANNER, reason="column planner not installed")
needs_binary = pytest.mark.skipif(discover() is None, reason="inkstitch binary not found")


def _bar(x, y, w, h):
    return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h)]]


class TestColor:
    def test_ciede2000_matches_published_pairs(self):
        # Sharma, Wu & Dalal (2005), Table 1: pairs 1, 7, 17 and 34
        for a, b, want in [((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
                           ((50, 0, 0), (50, -1, 2), 2.3669),
                           ((50, 2.5, 0), (73, 25, -18), 27.1492),
                           ((2.0776, 0.0795, -1.135), (0.9033, -0.0636, -0.5514), 0.9082)]:
            assert abs(sp.delta_e_lab(a, b) - want) < 1e-3

    def test_near_greens_merge_distinct_ones_stay(self):
        # two greens ~5 apart are one thread; a lighter green ~13 away is a design colour
        assert sp.delta_e("#2e7d32", "#1f8048") < sp.MERGE_DE < sp.delta_e("#2e7d32", "#4f9a3c")
        plan = sp.merge_colors({"#2e7d32": 900.0, "#1f8048": 50.0, "#4f9a3c": 400.0, "#e8c21a": 300.0})
        assert plan.thread["#1f8048"] == "#2e7d32"          # sews in the colour with the most area
        assert plan.thread["#4f9a3c"] == "#4f9a3c"
        assert plan.merges == [{"thread": "#2e7d32", "fills": ["#2e7d32", "#1f8048"],
                                "max_delta_e": round(sp.delta_e("#2e7d32", "#1f8048"), 1)}]

    def test_complete_linkage_does_not_chain(self):
        # a ramp of shades each under the threshold from the next: single linkage would sew yellow and green as one thread
        shades = ["#e5c319", "#e5db19", "#d7e519", "#c0e519", "#a8e519", "#90e519", "#78e519", "#60e519",
                  "#49e519"]
        steps = [sp.delta_e(a, b) for a, b in zip(shades, shades[1:])]
        assert max(steps) < sp.MERGE_DE and sp.delta_e(shades[0], shades[-1]) > 2 * sp.MERGE_DE
        plan = sp.merge_colors({c: 100.0 for c in shades})
        assert plan.thread[shades[0]] != plan.thread[shades[-1]]

    def test_max_colors_caps_threads(self):
        plan = sp.merge_colors({"#2e7d32": 900.0, "#4f9a3c": 400.0, "#e8c21a": 300.0}, max_colors=2)
        assert len(plan.threads) == 2 and plan.thread["#4f9a3c"] == "#2e7d32"

    def test_zero_threshold_keeps_every_fill(self):
        plan = sp.merge_colors({"#2e7d32": 1.0, "#1f8048": 1.0}, max_de=0)
        assert len(plan.threads) == 2 and plan.merges == []


class TestFills:
    SVG = ('<svg xmlns="http://www.w3.org/2000/svg"><defs><style>.a{fill:#e8c21a}.b, .c { fill: #1F8048; }'
           '</style></defs><g fill="#123456"><path id="p1" class="a"/><path id="p2" class="c"/>'
           '<path id="p3"/><path id="p4" class="a" style="fill:#ffffff"/></g>'
           '<g style="display:none"><path id="p5" class="a"/></g><path id="p6" style="fill:none"/></svg>')

    def test_fill_from_class_style_attribute_and_parent(self):
        root = etree.fromstring(self.SVG)
        classes = sp.css_fills(root)
        by_id = {e.get("id"): e for e in root.iter() if e.get("id")}
        assert sp.element_fill(by_id["p1"], classes) == "#e8c21a"
        assert sp.element_fill(by_id["p2"], classes) == "#1f8048"
        assert sp.element_fill(by_id["p3"], classes) == "#123456"    # inherited from the group
        assert sp.element_fill(by_id["p4"], classes) == "#ffffff"    # style beats class
        assert sp.element_fill(by_id["p6"], classes) is None
        assert sp.hidden(by_id["p5"]) and not sp.hidden(by_id["p1"])


@needs_planner                 # shape_prep dedupes with shapely
class TestDuplicates:
    def test_lower_copy_of_an_identical_shape_is_dropped_whatever_its_colour(self):
        ring = _bar(0, 0, 20, 3)
        shapes = [("light", ring), ("other", _bar(0, 10, 20, 3)), ("dark", [list(reversed(ring[0]))]),
                  ("near", _bar(0.02, 10.0, 20, 3))]
        keep, dropped = sp.dedupe(shapes)
        assert keep == [2, 3]
        assert dropped == [{"id": "light", "under": "dark"}, {"id": "other", "under": "near"}]

    def test_overlapping_but_different_shapes_both_stay(self):
        keep, dropped = sp.dedupe([("a", _bar(0, 0, 20, 3)), ("b", _bar(0, 0, 20, 3.5))])
        assert keep == [0, 1] and dropped == []


@needs_planner                 # thread order needs shapely overlap tests
class TestThreadOrder:
    def _polys(self, *bars):
        return [sp.outline_polygon(b) for b in bars]

    def test_interleaved_threads_become_blocks(self):
        polys = self._polys(_bar(0, 0, 5, 5), _bar(10, 0, 5, 5), _bar(20, 0, 5, 5), _bar(30, 0, 5, 5))
        order, splits = sp.thread_order(["g", "y", "g", "y"], polys)
        assert order == [0, 2, 1, 3] and splits == []

    def test_layering_wins_over_blocks(self):
        # the yellow shape lies on the first green and under the second: green must sew twice
        polys = self._polys(_bar(0, 0, 10, 5), _bar(5, 0, 10, 5), _bar(10, 0, 10, 5))
        order, splits = sp.thread_order(["g", "y", "g"], polys)
        assert order == [0, 1, 2] and [s["thread"] for s in splits] == ["g"]


def _svg(body, w=80, h=40):
    k = 96 / 25.4
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" '
            f'viewBox="0 0 {w * k:.4f} {h * k:.4f}"><defs><style>.g1{{fill:#2e7d32}}.g2{{fill:#1f8048}}'
            f'.y{{fill:#e8c21a}}</style></defs>{body}</svg>')


def _rect_d(x, y, w, h):
    k = 96 / 25.4
    x, y, w, h = x * k, y * k, w * k, h * k
    return f"M {x:.3f},{y:.3f} L {x + w:.3f},{y:.3f} L {x + w:.3f},{y + h:.3f} L {x:.3f},{y + h:.3f} Z"


@needs_planner
@needs_binary
class TestShapesToSatinCleanup:
    def _run(self, tmp_path, body, *opts):
        from cli_anything_inkstitch.cli import root
        svg = tmp_path / "t.svg"
        svg.write_text(_svg(body))
        proj = tmp_path / "t.json"
        r = CliRunner().invoke(root, ["document", "open", "--project", str(proj), "--svg", str(svg)])
        assert r.exit_code == 0, r.output
        r = CliRunner().invoke(root, ["--json", "tools", "shapes-to-satin", "--project", str(proj), *opts])
        assert r.exit_code == 0, r.output
        out = tmp_path / "t.dst"
        r2 = CliRunner().invoke(root, ["export", "file", "--project", str(proj), "--format", "dst",
                                       "--out", str(out)])
        assert r2.exit_code == 0, r2.output
        import pyembroidery as pe
        pat = pe.read(str(out))
        cmds = [c & pe.COMMAND_MASK for _, _, c in pat.stitches]
        return json.loads(r.output), cmds.count(pe.STITCH), cmds.count(pe.COLOR_CHANGE)

    BODY = (f'<path id="a" class="g1" d="{_rect_d(5, 5, 30, 4)}"/>'
            f'<path id="b" class="y" d="{_rect_d(5, 20, 30, 4)}"/>'
            f'<path id="c" class="g2" d="{_rect_d(45, 5, 20, 4)}"/>'
            f'<path id="d" class="y" d="{_rect_d(45, 20, 30, 4)}"/>')

    def test_class_fills_merge_into_blocks(self, tmp_path):
        out, _, cc = self._run(tmp_path, self.BODY)
        assert [s["thread"] for s in out["shapes"]] == ["#2e7d32", "#2e7d32", "#e8c21a", "#e8c21a"]
        assert out["color_changes"] == 1 and cc == 1
        assert out["color_merges"][0]["fills"] == ["#2e7d32", "#1f8048"]

    def test_merge_off_and_order_kept_sews_as_drawn(self, tmp_path):
        out, _, cc = self._run(tmp_path, self.BODY, "--merge-colors-de", "0", "--keep-order")
        assert out["color_changes"] == 3 and cc == 3

    def test_duplicate_copy_is_sewn_once(self, tmp_path):
        one, n1, _ = self._run(tmp_path, f'<path id="a" class="y" d="{_rect_d(5, 5, 30, 4)}"/>')
        two, n2, _ = self._run(tmp_path, f'<path id="a" class="g1" d="{_rect_d(5, 5, 30, 4)}"/>'
                                         f'<circle id="dot" class="y" cx="200" cy="100" r="8"/>'
                                         f'<path id="b" class="y" d="{_rect_d(5, 5, 30, 4)}"/>')
        assert two["duplicates_dropped"] == [{"id": "a", "under": "b"}]
        assert [s["shape"] for s in two["shapes"]] == ["dot", "b"]        # the circle is digitized too
        dot = next(s for s in two["shapes"] if s["shape"] == "dot")
        assert dot["stitch"] == "satin"
        assert n2 > n1 and n2 < 2 * n1                                    # the bar once, plus the dot


class TestCentreOutOrder:
    """Stretch fabric sews outward from the middle, each element whole (Bob/Thready 11/15)."""

    @staticmethod
    def _box(x, y, w=4.0, h=4.0):
        from shapely.geometry import box
        return box(x, y, x + w, y + h)

    def test_outward_from_the_origin(self):
        polys = [self._box(40, 0), self._box(-2, 0), self._box(20, 0), self._box(-60, 0)]
        order, splits = sp.centre_out_order(["w"] * 4, polys, (0.0, 2.0))
        assert order == [1, 2, 0, 3] and splits == []

    def test_an_elements_strands_sew_together(self):
        # gold strands 0+1 (2 mm apart) are one element; a lone gold shape 2 sits between them and
        # the origin in distance but is a separate element further out by its nearest point
        polys = [self._box(10, 0), self._box(16, 0), self._box(-14, 0)]
        order, _ = sp.centre_out_order(["g", "g", "g"], polys, (0.0, 2.0))
        assert order == [0, 1, 2]

    def test_interlocked_threads_form_one_unit_letters_batch_by_thread(self):
        # a crest at the middle (green heart touching two gold strands) and white letters spaced
        # 20 mm apart, one of them nearer the origin than the far gold strand
        polys = [self._box(0, 0), self._box(5, 0), self._box(-5, 0),      # crest: g, y, y
                 self._box(0, 30), self._box(25, 30), self._box(-25, 30)]  # letters: w w w
        threads = ["g", "y", "y", "w", "w", "w"]
        order, splits = sp.centre_out_order(threads, polys, (2.0, 2.0))
        assert order[:3] == [0, 1, 2] or order[:3] == [0, 2, 1]           # crest first, heart first
        assert set(order[3:]) == {3, 4, 5} and order[3] == 3               # then letters, centre-out
        assert splits == []

    def test_layering_still_wins(self):
        # shape 1 (yellow) is drawn over shape 0 (green): green sews first even though yellow is
        # nearer the origin
        polys = [self._box(0, 0, 20, 4), self._box(-1, 0, 6, 4)]
        order, _ = sp.centre_out_order(["g", "y"], polys, (0.0, 2.0))
        assert order == [0, 1]

    def test_inside_an_element_shapes_sew_from_its_own_middle(self):
        # a heart: two halves either side, an inner swirl in the middle; the origin sits low, by
        # the halves' bottom tips. The swirl still sews first: it's the element's middle.
        polys = [self._box(-6, 0, 5, 10), self._box(1, 0, 5, 10), self._box(-1, 3, 2, 4)]
        order, _ = sp.centre_out_order(["g", "g", "g"], polys, (-0.5, -3.0))
        assert order[0] == 2
