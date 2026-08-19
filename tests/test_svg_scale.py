"""Scaling a design must move its geometry and leave its density alone."""

from __future__ import annotations

import re

import pytest

import tools_path  # noqa: F401  (puts tools/ on sys.path)
from svg_scale import document_mm, scale_svg

SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
    'width="378.000" height="189.000" viewBox="0 0 378.000 189.000">\n'
    '  <path id="a" d="M 0.000,0.000 C 10.000,20.000 30.000,40.000 100.000,50.000 Z" '
    'style="fill:#048dad;fill-rule:evenodd;stroke:none" '
    'inkstitch:angle="1.9" inkstitch:expand_mm="0.2" '
    'inkstitch:row_spacing_mm="0.25" inkstitch:trim_after="true"/>\n'
    '</svg>\n'
)


def coords(svg):
    d = re.search(r'\sd="([^"]*)"', svg).group(1)
    return [float(n) for n in re.findall(r'-?\d+\.?\d*', d)]


def test_geometry_scales():
    out = scale_svg(SVG, 0.5)
    assert coords(out) == [c / 2 for c in coords(SVG)]
    assert document_mm(out) == pytest.approx([v / 2 for v in document_mm(SVG)])
    assert 'viewBox="0.000 0.000 189.000 94.500"' in out


def test_density_params_do_not_scale():
    """A youth-size design is stitched at the same density as the adult one,
    with fewer rows -- not at a proportionally coarser one."""
    out = scale_svg(SVG, 0.5)
    assert 'inkstitch:row_spacing_mm="0.25"' in out
    assert 'inkstitch:expand_mm="0.2"' in out
    assert 'inkstitch:angle="1.9"' in out
    assert 'inkstitch:trim_after="true"' in out


def test_a_transform_is_refused():
    """A transform would scale twice or not at all, depending where it sits."""
    with pytest.raises(SystemExit):
        scale_svg(SVG.replace('<path id="a"', '<path id="a" transform="translate(5)"'), 0.5)


SVG_WITH_COMMAND = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
    'version="1.1" width="378.000" height="189.000" viewBox="0 0 378.000 189.000">\n'
    '  <defs><symbol id="inkstitch_starting_point">'
    '<path d="M 0,0 L 4,4" transform="scale(2)"/></symbol></defs>\n'
    '  <path id="a" d="M 0.000,0.000 L 100.000,50.000 Z" style="fill:#048dad"/>\n'
    '  <g><use id="u" xlink:href="#inkstitch_starting_point" x="40.000" y="20.000"/>'
    '<path id="c" d="M 40.000,20.000 50.000,25.000"/></g>\n'
    '</svg>\n'
)


def test_command_markers_move_with_the_design():
    """A design must stay resizable after commands are attached to it."""
    out = scale_svg(SVG_WITH_COMMAND, 0.5)
    assert 'x="20.000" y="10.000"' in out          # the marker
    assert 'd="M 20.000,10.000 25.000,12.500"' in out  # its connector


def test_marker_artwork_is_left_alone():
    """The symbols in <defs> are fixed-size icons, and they carry transforms of
    their own -- scaling them would resize the icon with the design, and
    refusing the document because of them would make it unresizable."""
    out = scale_svg(SVG_WITH_COMMAND, 0.5)
    assert '<path d="M 0,0 L 4,4" transform="scale(2)"/>' in out


def test_a_transform_outside_defs_is_still_refused():
    with pytest.raises(SystemExit):
        scale_svg(SVG_WITH_COMMAND.replace('<path id="a"',
                                           '<path id="a" transform="translate(5)"'), 0.5)
