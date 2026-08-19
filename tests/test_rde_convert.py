"""Tests for the Chroma .rde -> Ink/Stitch SVG converter (tools/rde_to_inkstitch.py).

The .rde fixtures are licensed commercial designs and are not committed, so
every test here skips when its fixture is absent (same pattern as the BX tests
in test_font.py).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import tools_path  # noqa: F401  (puts tools/ on sys.path)
from rde_synth import build, fill_rows, object_payload, rect, ring_fill
from rde_to_inkstitch import convert

RDE_FIXTURES = Path(__file__).parent / "fixtures" / "rde"

# One thread, so a synthetic design is a single color block.
CYAN = [((4, 141, 173), "1295", "Cyan")]


def paths(svg, kind):
    out = []
    for tag in re.findall(r'<path [^>]*/>', svg):
        d = re.search(r'\sd="([^"]*)"', tag).group(1)
        filled = 'fill:none' not in tag
        if (kind == 'fill') == filled:
            out.append(d)
    return out


def subpaths(d):
    return d.count('M ')


class TestCounters:
    """A letter's counter is a hole in the letter, not a shape of its own."""

    @pytest.mark.skipif(
        not (RDE_FIXTURES / "lake-superior-social-club.rde").exists(),
        reason="fixture not present",
    )
    def test_letter_counters_become_holes(self):
        svg, counts = convert(str(RDE_FIXTURES / "lake-superior-social-club.rde"))
        # LAKE SUPERIOR SOCIAL CLUB has nine counters: A P R O R O A B B.
        holed = [d for d in paths(svg, 'fill') if subpaths(d) > 1]
        assert len(holed) >= 8, f"only {len(holed)} letters kept a counter"
        # A counter that stayed a separate contour would come out as a stroke.
        assert counts['run'] == 0

    @pytest.mark.skipif(
        not (RDE_FIXTURES / "Adult MN Flowers.rde").exists(),
        reason="fixture not present",
    )
    def test_petal_details_are_not_carved_out(self):
        """The detail contours inside a flower are stitched lines, not holes.

        Cutting them out of the petal with evenodd is the regression this
        guards: it leaves see-through gaps across the flowers.
        """
        svg, counts = convert(str(RDE_FIXTURES / "Adult MN Flowers.rde"))
        holed = [d for d in paths(svg, 'fill') if subpaths(d) > 1]
        assert len(holed) <= 5, f"{len(holed)} shapes gained holes"
        assert counts['run'] >= 20, "detail lines were dropped, not kept"


class TestNamedObjects:
    """An object's name sits between its stitches and its contours."""

    @pytest.mark.skipif(
        not (RDE_FIXTURES / "Lucky Hat.rde").exists(),
        reason="fixture not present",
    )
    def test_named_objects_keep_their_outlines(self):
        """Every object in this design is named, so a fixed-width name field
        loses all of them and the design degrades to a trace of its stitches."""
        svg, counts = convert(str(RDE_FIXTURES / "Lucky Hat.rde"))
        assert counts['fill'] > 0
        assert counts['run'] == 0
        # A stitch trace is thousands of segments; a real outline is tens.
        for d in paths(svg, 'fill'):
            assert d.count('C ') < 200, "path looks like a traced stitch run"


class TestTrims:
    """A trim goes exactly where the engine would otherwise leave a float."""

    @pytest.mark.skipif(
        not (RDE_FIXTURES / "lake-superior-social-club.rde").exists(),
        reason="fixture not present",
    )
    def test_every_move_in_a_spread_out_design_trims(self):
        """Nothing in this design is close to anything else -- the shortest
        move between objects is 11 mm -- so every move within a color trims.

        These are the same 40 elements Ink/Stitch's own jump_to_trim marks when
        it is run over this document at its default 3 mm.
        """
        svg, counts = convert(str(RDE_FIXTURES / "lake-superior-social-club.rde"))
        assert counts['trim'] == 40
        # One attribute per trim: it lands on the object's last element, not
        # on every element the object emitted.
        assert svg.count('inkstitch:trim_after="true"') == counts['trim']

    @pytest.mark.skipif(
        not (RDE_FIXTURES / "Flower Adult Hat.rde").exists(),
        reason="fixture not present",
    )
    def test_short_moves_do_not_trim(self):
        """1146 objects, but 938 of the moves between them are under 2 mm --
        stitching the engine runs straight through. Trimming per object here
        would put a thousand needless trims in the file.
        """
        svg, counts = convert(str(RDE_FIXTURES / "Flower Adult Hat.rde"))
        assert counts['trim'] < 200, "short moves are being trimmed"
        assert counts['trim'] > 50, "long moves are not being trimmed"
        assert svg.count('inkstitch:trim_after="true"') == counts['trim']


class TestSynthetic:
    """The rules, exercised on files built from source.

    The corpus tests above are the real evidence, but they need licensed .rde
    files that are not in the repo, so they all skip in CI. These build their
    own input and run everywhere.
    """

    def test_counter_becomes_a_hole(self, tmp_path):
        """A square with a square counter, filled around the counter."""
        design = build(
            tmp_path / "letter.rde", CYAN,
            [object_payload(0, ring_fill(0, 0, 100, 100, (35, 35, 65, 65)),
                            [rect(0, 0, 100, 100), rect(35, 35, 30, 30)])],
        )
        svg, counts = convert(str(design))
        assert counts == {'satin': 0, 'fill': 1, 'run': 0, 'trim': 0}
        d = paths(svg, 'fill')[0]
        assert subpaths(d) == 2, "the counter is not a subpath of the letter"

    def test_a_stitched_region_is_not_a_hole(self, tmp_path):
        """Same two contours, but the fill runs straight through the inner one.

        Thread inside is what separates a counter from a detail Chroma really
        stitches; carving this one out would be the flower regression.
        """
        design = build(
            tmp_path / "crossed.rde", CYAN,
            [object_payload(0, fill_rows(0, 0, 100, 100),
                            [rect(0, 0, 100, 100), rect(35, 35, 30, 30)])],
        )
        svg, counts = convert(str(design))
        assert subpaths(paths(svg, 'fill')[0]) == 1, "a stitched region was carved out"
        assert counts['run'] == 1, "the inner contour should come back as a line"

    @pytest.mark.parametrize("name", ["", "l", "P2", "letter"])
    def test_contours_survive_any_object_name(self, tmp_path, name):
        """The name sits between the stitches and the contours, so a fixed-width
        assumption loses the outline of every named object."""
        design = build(
            tmp_path / f"named{len(name)}.rde", CYAN,
            [object_payload(0, fill_rows(0, 0, 100, 100),
                            [rect(0, 0, 100, 100)], name=name)],
        )
        svg, counts = convert(str(design))
        assert counts['fill'] == 1 and counts['run'] == 0
        # Four corners, not a trace of the stitches.
        assert paths(svg, 'fill')[0].count('C ') == 4

    @pytest.mark.parametrize("gap,trims", [(10, 0), (100, 1)])
    def test_trim_follows_the_gap_to_the_next_object(self, tmp_path, gap, trims):
        """1 mm apart the engine stitches through; 10 mm apart it jumps, and a
        jump is a float unless something trims it."""
        first = fill_rows(0, 0, 100, 100)
        x, y = first[-1]
        design = build(
            tmp_path / f"gap{gap}.rde", CYAN,
            [object_payload(0, first, [rect(0, 0, 100, 100)]),
             object_payload(0, fill_rows(x + gap, y, 50, 50),
                            [rect(x + gap, y, 50, 50)])],
        )
        svg, counts = convert(str(design))
        assert counts['trim'] == trims
        assert svg.count('inkstitch:trim_after="true"') == trims


needs_binary = pytest.mark.skipif(
    __import__("cli_anything_inkstitch.binary", fromlist=["discover"]).discover() is None,
    reason="Ink/Stitch binary not installed",
)


@needs_binary
class TestAgainstTheEngine:
    """Differential proof against the installed engine, not against ourselves."""

    def _run(self, extension, svg_path, args=None):
        from cli_anything_inkstitch.binary import discover, run_extension
        return run_extension(discover(), extension, str(svg_path),
                             args=args or {}, capture_stdout=True)

    def test_trims_match_the_engines_own_jump_to_trim(self, tmp_path):
        """Ink/Stitch ships jump_to_trim, which marks the same attribute using
        the engine's real routing. Our converter decides at conversion time
        from Chroma's object gaps; on a design whose objects are far apart the
        two must land on exactly the same elements.
        """
        design = build(
            tmp_path / "spread.rde", CYAN,
            # Three squares, each a long move from the last.
            [object_payload(0, fill_rows(x, 0, 60, 60), [rect(x, 0, 60, 60)])
             for x in (0, 400, 800)],
        )
        svg, counts = convert(str(design))
        assert counts['trim'] == 2

        ours = tmp_path / "ours.svg"
        ours.write_text(svg)
        # The same document with the trims stripped, for the engine to mark.
        bare = tmp_path / "bare.svg"
        bare.write_text(svg.replace(' inkstitch:trim_after="true"', ''))
        marked = self._run("jump_to_trim", bare, {"minimum-jump-length": 3.0})
        assert marked, "jump_to_trim produced no output"

        def trimmed_ids(text):
            return {m.group(1) for m in re.finditer(
                r'<path id="(rde\d+)"[^>]*trim_after="[Tt]rue"', text)}

        engine_ids = trimmed_ids(marked.decode("utf-8"))
        assert trimmed_ids(svg) == engine_ids
        assert len(engine_ids) == 2

    def test_a_counter_is_left_unstitched(self, tmp_path):
        """The vectors having a hole proves nothing on its own -- what matters
        is that the engine's fill does not lay thread through it.

        Stitch COUNT cannot answer this: cutting the counter out also shrinks
        the area the row spacing is derived from, so the holed version can come
        out denser. Where the stitches land is the question, so ask that.
        """
        stitching = ring_fill(0, 0, 300, 300, (100, 100, 200, 200))
        outer = rect(0, 0, 300, 300)
        counter = rect(100, 100, 100, 100)

        def share_inside_the_counter(name, contours):
            design = build(tmp_path / name, CYAN,
                           [object_payload(0, stitching, contours)])
            svg, _ = convert(str(design))
            path = tmp_path / f"{name}.svg"
            path.write_text(svg)
            csv = self._run("output", path, {"format": "csv"}).decode("utf-8")
            box = {k: float(v) for k, v in re.findall(
                r'"EXTENTS_(LEFT|RIGHT|TOP|BOTTOM):","([-\d.]+)"', csv)}
            w = box["RIGHT"] - box["LEFT"]
            h = box["BOTTOM"] - box["TOP"]
            pts = [(float(x), float(y)) for x, y in re.findall(
                r'"\*","\d+","STITCH","([-\d.]+)","([-\d.]+)"', csv)]
            assert pts, "no stitches in the export"
            # The counter is the middle third of the square in both axes,
            # inset a little: rows that stop AT the counter leave their last
            # stitch on its edge, and those are correct, not stitched-through.
            lo, hi = 0.36, 0.64
            inside = sum(1 for x, y in pts
                         if lo < (x - box["LEFT"]) / w < hi
                         and lo < (y - box["TOP"]) / h < hi)
            return inside / len(pts)

        holed = share_inside_the_counter("holed.rde", [outer, counter])
        solid = share_inside_the_counter("solid.rde", [outer])
        assert solid > 0.03, "the solid control is not stitching the middle"
        assert holed == 0, f"the engine stitched into the counter ({holed:.3f})"
