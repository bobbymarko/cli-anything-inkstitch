"""Chroma's fill start/end positions must survive into the SVG.

A digitiser picks where a shape starts and ends so the fill crosses it in one
pass. Chroma stores that as the object's first and last stitch, and Ink/Stitch
reads it as a command (fill_stitch.py get_starting_point / get_ending_point),
so it is recoverable -- and lost unless something carries it.
"""

from __future__ import annotations

import re

import pytest

import tools_path  # noqa: F401  (puts tools/ on sys.path)
from rde_start_end import apply
from rde_synth import build, fill_rows, object_payload, rect
from rde_to_inkstitch import convert
from svg_scale import scale_svg

CYAN = [((4, 141, 173), "1295", "Cyan")]


def _design(tmp_path, name="d.rde"):
    """One square whose fill deliberately runs bottom-left to top-right."""
    stitches = fill_rows(0, 0, 200, 200)
    return build(tmp_path / name, CYAN,
                 [object_payload(0, stitches, [rect(0, 0, 200, 200)])]), stitches


def _commands(svg):
    return {m.group(1): (float(m.group(2)), float(m.group(3)))
            for m in re.finditer(
                # lxml picks its own prefix for the xlink namespace.
                r'href="#inkstitch_(\w+)"[^>]*?\sx="([-\d.]+)"\s+y="([-\d.]+)"', svg)}


def test_start_and_end_land_on_the_first_and_last_stitch(tmp_path):
    design, stitches = _design(tmp_path)
    svg_path = tmp_path / "d.svg"
    svg, _ = convert(str(design))
    svg_path.write_text(svg)

    assert apply(str(design), str(svg_path)) == 1
    cmds = _commands(svg_path.read_text())
    assert set(cmds) == {"starting_point", "ending_point"}

    # The .rde is in 0.1 mm units and the SVG in px, both from the same origin.
    px = 0.1 * 96 / 25.4
    xs = [s[0] for s in stitches]
    ys = [s[1] for s in stitches]
    for cmd, stitch in (("starting_point", stitches[0]), ("ending_point", stitches[-1])):
        want = ((stitch[0] - min(xs)) * px, (stitch[1] - min(ys)) * px)
        assert cmds[cmd] == pytest.approx(want, abs=0.05)


def test_a_scaled_design_gets_them_in_scaled_places(tmp_path):
    """A youth size is the same design at another scale; the factor is
    recovered from the document rather than remembered by the caller."""
    design, stitches = _design(tmp_path)
    full = tmp_path / "full.svg"
    svg, _ = convert(str(design))
    full.write_text(svg)
    half = tmp_path / "half.svg"
    half.write_text(scale_svg(svg, 0.5))

    apply(str(design), str(full))
    apply(str(design), str(half))
    a, b = _commands(full.read_text()), _commands(half.read_text())
    for cmd in ("starting_point", "ending_point"):
        assert b[cmd] == pytest.approx((a[cmd][0] / 2, a[cmd][1] / 2), abs=0.05)
