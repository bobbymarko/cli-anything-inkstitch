"""Tests for the no-fill warning surfaced in element_summary, element list,
element describe, and validate static."""

from __future__ import annotations

import json

from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.svg.elements import warnings_for_element


def _e(xml: str):
    return etree.fromstring(xml)


# --- warnings_for_element ------------------------------------------------

def test_no_warning_when_fill_set():
    elem = _e('<path xmlns="http://www.w3.org/2000/svg" '
              'd="M0,0 L10,10 z" fill="#ff0000"/>')
    assert warnings_for_element(elem) == []


def test_no_warning_when_style_fill_set():
    elem = _e('<path xmlns="http://www.w3.org/2000/svg" '
              'd="M0,0 L10,10 z" style="fill:#abc"/>')
    assert warnings_for_element(elem) == []


def test_no_warning_when_stroke_set():
    elem = _e('<path xmlns="http://www.w3.org/2000/svg" '
              'd="M0,0 L10,10" stroke="#000"/>')
    assert warnings_for_element(elem) == []


def test_warning_when_no_fill_no_stroke_path():
    elem = _e('<path xmlns="http://www.w3.org/2000/svg" '
              'd="M0,0 L10,10 L10,0 z M2,2 L8,2 L8,8 z"/>')
    ws = warnings_for_element(elem)
    assert len(ws) == 1
    assert ws[0]["type"] == "default_fill_black"
    assert ws[0]["severity"] == "warning"


def test_warning_for_unfilled_rect():
    elem = _e('<rect xmlns="http://www.w3.org/2000/svg" '
              'x="0" y="0" width="10" height="10"/>')
    assert warnings_for_element(elem)[0]["type"] == "default_fill_black"


def test_warning_for_unfilled_circle():
    elem = _e('<circle xmlns="http://www.w3.org/2000/svg" '
              'cx="5" cy="5" r="3"/>')
    assert warnings_for_element(elem)[0]["type"] == "default_fill_black"


def test_no_warning_for_text_element():
    """text gets TextTypeWarning from inkstitch, not the black-fill issue."""
    elem = _e('<text xmlns="http://www.w3.org/2000/svg">hi</text>')
    assert warnings_for_element(elem) == []


def test_no_warning_for_image_element():
    elem = _e('<image xmlns="http://www.w3.org/2000/svg" '
              'href="x.png" width="10" height="10"/>')
    assert warnings_for_element(elem) == []


def test_no_warning_for_use_element():
    elem = _e('<use xmlns="http://www.w3.org/2000/svg" href="#foo"/>')
    assert warnings_for_element(elem) == []


def test_no_warning_when_inside_defs():
    """Elements in <defs> aren't rendered directly, so they don't stitch."""
    tree = etree.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '  <defs><path id="a" d="M0,0 L10,10 z"/></defs>'
        '  <path id="b" d="M0,0 L10,10 z"/>'
        '</svg>'
    )
    in_defs = tree.find('.//{http://www.w3.org/2000/svg}defs/'
                          '{http://www.w3.org/2000/svg}path')
    direct = tree.findall('.//{http://www.w3.org/2000/svg}path')[-1]
    assert warnings_for_element(in_defs) == []
    assert warnings_for_element(direct)[0]["type"] == "default_fill_black"


def test_no_warning_when_fill_none_with_stroke():
    """fill='none' + stroke is a stroked-only path. Inkstitch will treat it
    as a Stroke; no default-black risk."""
    elem = _e('<path xmlns="http://www.w3.org/2000/svg" '
              'd="M0,0 L10,10" fill="none" stroke="#000"/>')
    assert warnings_for_element(elem) == []


def test_warning_when_fill_none_no_stroke():
    """fill='none' + no stroke is the same trap as no fill attr at all —
    inkstitch's default still triggers."""
    elem = _e('<path xmlns="http://www.w3.org/2000/svg" '
              'd="M0,0 L10,10 z" fill="none"/>')
    assert warnings_for_element(elem)[0]["type"] == "default_fill_black"


# --- element list / describe surfacing ----------------------------------

_RISKY_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '  <path id="filled" d="M0,0 L10,10 z" fill="#abc"/>'
    '  <path id="risky"  d="M20,20 L30,30 z M22,22 L28,22 z"/>'  # no fill, multi-subpath
    '</svg>'
)


def _open(workdir, project_path):
    svg_path = workdir / "design.svg"
    svg_path.write_text(_RISKY_SVG)
    runner = CliRunner()
    runner.invoke(
        root, ["document", "open", "--project", project_path, "--svg", str(svg_path)],
        catch_exceptions=False,
    )
    return runner


def test_element_list_surfaces_warning_for_risky(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "element", "list", "--project", project_path],
        catch_exceptions=False,
    )
    data = json.loads(result.output)
    by_id = {e["id"]: e for e in data["elements"]}
    assert "warnings" not in by_id["filled"]
    assert by_id["risky"]["warnings"][0]["type"] == "default_fill_black"


def test_element_describe_surfaces_warning_for_risky(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "element", "describe", "--project", project_path,
               "--id", "risky"],
        catch_exceptions=False,
    )
    data = json.loads(result.output)
    assert data["warnings"][0]["type"] == "default_fill_black"


def test_element_describe_omits_warnings_field_when_clean(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "element", "describe", "--project", project_path,
               "--id", "filled"],
    )
    data = json.loads(result.output)
    assert "warnings" not in data


# --- validate static ----------------------------------------------------

def test_validate_static_emits_default_fill_black_issue(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "validate", "static", "--project", project_path],
        catch_exceptions=False,
    )
    data = json.loads(result.output)
    risky_issues = [i for i in data["issues"] if i["id"] == "risky"]
    types = {i["type"] for i in risky_issues}
    # Both warnings fire on the same element: black-fill *and* unassigned.
    assert "default_fill_black" in types
    assert "unassigned" in types


def test_validate_static_clean_for_filled_path(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "validate", "static", "--project", project_path],
    )
    data = json.loads(result.output)
    filled_issues = [i for i in data["issues"] if i["id"] == "filled"]
    assert filled_issues == []
