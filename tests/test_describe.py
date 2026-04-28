"""Tests for `element describe` and the geometry/color helpers."""

from __future__ import annotations

import json

from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.svg.colors import closest_named, hex_to_rgb
from cli_anything_inkstitch.svg.geometry import (
    aspect_ratio,
    bbox_overlap,
    design_bbox_from_root,
    element_bbox,
    path_bbox,
    position_descriptor,
    px_to_mm,
)


# --- geometry: path_bbox ---------------------------------------------------

def test_path_bbox_simple_absolute():
    assert path_bbox("M0,0 L10,5 L20,15 z") == (0, 0, 20, 15)


def test_path_bbox_relative_lineto():
    # m10,10 (absolute since first M) then l5,5 (relative) → ends at (15,15)
    assert path_bbox("m10,10 l5,5 z") == (10, 10, 15, 15)


def test_path_bbox_implicit_lineto_after_m():
    # M then more coords = implicit L
    assert path_bbox("M0,0 10,0 10,10 0,10 z") == (0, 0, 10, 10)


def test_path_bbox_horizontal_vertical():
    assert path_bbox("M5,5 H20 V15 H5 z") == (5, 5, 20, 15)


def test_path_bbox_cubic_uses_control_points():
    # Cubic from (0,0) to (10,0) with control points pulling up to y=20
    bb = path_bbox("M0,0 C5,20 5,20 10,0")
    assert bb is not None
    assert bb[0] == 0 and bb[2] == 10
    assert bb[3] == 20  # control points dominate


def test_path_bbox_multiple_subpaths():
    # Outer ring + inner ring
    bb = path_bbox("M0,0 L10,0 L10,10 L0,10 z M2,2 L8,2 L8,8 L2,8 z")
    assert bb == (0, 0, 10, 10)


def test_path_bbox_empty_returns_none():
    assert path_bbox("") is None
    assert path_bbox("   ") is None


# --- geometry: element_bbox ------------------------------------------------

def _e(xml: str):
    return etree.fromstring(xml)


def test_element_bbox_rect():
    bb = element_bbox(_e('<rect xmlns="http://www.w3.org/2000/svg" '
                        'x="10" y="20" width="30" height="40"/>'))
    assert bb == (10, 20, 40, 60)


def test_element_bbox_circle():
    bb = element_bbox(_e('<circle xmlns="http://www.w3.org/2000/svg" '
                        'cx="50" cy="50" r="10"/>'))
    assert bb == (40, 40, 60, 60)


def test_element_bbox_ellipse():
    bb = element_bbox(_e('<ellipse xmlns="http://www.w3.org/2000/svg" '
                        'cx="50" cy="50" rx="20" ry="10"/>'))
    assert bb == (30, 40, 70, 60)


def test_element_bbox_polygon():
    bb = element_bbox(_e('<polygon xmlns="http://www.w3.org/2000/svg" '
                        'points="0,0 10,0 5,10"/>'))
    assert bb == (0, 0, 10, 10)


def test_element_bbox_unsupported_returns_none():
    assert element_bbox(_e('<text xmlns="http://www.w3.org/2000/svg">hi</text>')) is None


# --- geometry: design_bbox_from_root --------------------------------------

def test_design_bbox_from_viewbox():
    root_el = _e('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 200"/>')
    assert design_bbox_from_root(root_el) == (0, 0, 100, 200)


def test_design_bbox_with_offset_viewbox():
    root_el = _e('<svg xmlns="http://www.w3.org/2000/svg" viewBox="-10 -20 100 200"/>')
    assert design_bbox_from_root(root_el) == (-10, -20, 90, 180)


def test_design_bbox_falls_back_to_width_height():
    root_el = _e('<svg xmlns="http://www.w3.org/2000/svg" width="50" height="100"/>')
    assert design_bbox_from_root(root_el) == (0, 0, 50, 100)


def test_design_bbox_strips_units():
    root_el = _e('<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="100mm"/>')
    assert design_bbox_from_root(root_el) == (0, 0, 50, 100)


# --- geometry: position_descriptor ----------------------------------------

def test_position_center():
    assert position_descriptor((40, 40, 60, 60), (0, 0, 100, 100)) == "center"


def test_position_top_left():
    assert position_descriptor((0, 0, 10, 10), (0, 0, 100, 100)) == "top-left"


def test_position_bottom_right():
    assert position_descriptor((90, 90, 100, 100), (0, 0, 100, 100)) == "bottom-right"


def test_position_middle_left():
    assert position_descriptor((0, 40, 20, 60), (0, 0, 100, 100)) == "middle-left"


def test_position_unknown_for_zero_design():
    assert position_descriptor((0, 0, 10, 10), (0, 0, 0, 0)) == "unknown"


# --- geometry: bbox_overlap -----------------------------------------------

def test_overlap_disjoint_returns_none():
    assert bbox_overlap((0, 0, 10, 10), (20, 20, 30, 30)) is None


def test_overlap_intersects():
    assert bbox_overlap((0, 0, 10, 10), (5, 5, 15, 15)) == "intersects"


def test_overlap_contains():
    assert bbox_overlap((0, 0, 100, 100), (10, 10, 20, 20)) == "contains"


def test_overlap_contained_by():
    assert bbox_overlap((10, 10, 20, 20), (0, 0, 100, 100)) == "contained_by"


# --- geometry: misc -------------------------------------------------------

def test_aspect_ratio_square():
    assert aspect_ratio((0, 0, 10, 10)) == 1.0


def test_aspect_ratio_wide():
    assert aspect_ratio((0, 0, 20, 10)) == 2.0


def test_aspect_ratio_zero_height_is_none():
    assert aspect_ratio((0, 5, 10, 5)) is None


def test_px_to_mm_known_conversion():
    # 96 px = 1 inch = 25.4 mm
    assert abs(px_to_mm(96) - 25.4) < 1e-6


# --- colors ---------------------------------------------------------------

def test_hex_to_rgb_six_digits():
    assert hex_to_rgb("#e57263") == (229, 114, 99)


def test_hex_to_rgb_three_digits():
    assert hex_to_rgb("#abc") == (170, 187, 204)


def test_hex_to_rgb_no_hash():
    assert hex_to_rgb("e57263") == (229, 114, 99)


def test_hex_to_rgb_named_passthrough():
    assert hex_to_rgb("teal") == (0, 128, 128)


def test_hex_to_rgb_invalid_returns_none():
    assert hex_to_rgb("not-a-color") is None
    assert hex_to_rgb("") is None


def test_closest_named_basic():
    assert closest_named("#000000") == "black"
    assert closest_named("#ffffff") == "white"


def test_closest_named_strongbad_palette():
    """Strongbad's actual fills should resolve to recognizable names."""
    # #e57263 — coral red → salmon
    assert closest_named("#e57263") == "salmon"
    # #50989e — muted teal → cadetblue (more accurate than gray)
    assert closest_named("#50989e") == "cadetblue"
    # #39af75 — sea green → mediumseagreen (more accurate than teal)
    assert closest_named("#39af75") == "mediumseagreen"


# --- end-to-end through the CLI ------------------------------------------

_DESIGN_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '  <rect id="bg" x="0" y="0" width="100" height="100" fill="#000000"/>'
    '  <circle id="dot" cx="50" cy="50" r="10" fill="#ff0000"/>'
    '  <rect id="corner" x="80" y="80" width="15" height="15" fill="#0000ff"/>'
    '</svg>'
)


def _open(workdir, project_path):
    svg_path = workdir / "design.svg"
    svg_path.write_text(_DESIGN_SVG)
    runner = CliRunner()
    runner.invoke(
        root, ["document", "open", "--project", project_path, "--svg", str(svg_path)],
        catch_exceptions=False,
    )
    return runner


def test_describe_single_element(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "element", "describe", "--project", project_path,
               "--id", "dot"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["id"] == "dot"
    assert data["color_name"] == "red"
    assert data["position"] == "center"
    assert data["size_mm"] == [round(20 / (96/25.4), 2)] * 2
    assert data["aspect_ratio"] == 1.0
    # bg contains dot, corner doesn't overlap dot
    nbrs = {n["id"]: n["relation"] for n in data["neighbors"]}
    assert nbrs["bg"] == "contained_by"
    assert "corner" not in nbrs


def test_describe_all_elements(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "element", "describe", "--project", project_path],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["count"] == 3
    assert data["design_size_mm"] == [round(100 / (96/25.4), 2)] * 2
    by_id = {e["id"]: e for e in data["elements"]}
    assert by_id["bg"]["position"] == "center"
    assert by_id["dot"]["color_name"] == "red"
    assert by_id["corner"]["position"] == "bottom-right"


def test_describe_no_neighbors_omits_neighbors_field(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "element", "describe", "--project", project_path,
               "--id", "dot", "--no-neighbors"],
        catch_exceptions=False,
    )
    data = json.loads(result.output)
    assert "neighbors" not in data


def test_describe_unknown_id_errors(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["element", "describe", "--project", project_path, "--id", "nope"],
    )
    assert result.exit_code == 1  # UserError
    assert "no element with id" in result.output.lower()
