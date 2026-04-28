"""Tests for `document prep` and svg.prep helpers."""

from __future__ import annotations

import json

from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.svg.attrs import INKSTITCH_NS
from cli_anything_inkstitch.svg.prep import (
    _subpath_count,
    find_illustrator_rings,
    prep_svg,
)


# --- unit tests on the helpers --------------------------------------------

def test_subpath_count_single():
    assert _subpath_count("M10,10 L20,20 z") == 1


def test_subpath_count_multiple():
    # Outer + inner rings — Illustrator's stroke-to-outline pattern.
    assert _subpath_count("M0,0 L10,0 L10,10 z M2,2 L8,2 L8,8 z") == 2


def test_subpath_count_lowercase_relative():
    assert _subpath_count("m10,10 l20,20 z m5,5 l3,3 z") == 2


def _parse(svg: str):
    return etree.ElementTree(etree.fromstring(svg.encode()))


def test_find_rings_skips_path_with_explicit_fill():
    tree = _parse(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '  <path id="a" d="M0,0 L10,0 z M2,2 L8,2 z" fill="#e57263"/>'
        '</svg>'
    )
    assert find_illustrator_rings(tree) == []


def test_find_rings_skips_path_with_style_fill():
    tree = _parse(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '  <path id="a" d="M0,0 L10,0 z M2,2 L8,2 z" style="fill:#abc"/>'
        '</svg>'
    )
    assert find_illustrator_rings(tree) == []


def test_find_rings_skips_path_with_stroke():
    tree = _parse(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '  <path id="a" d="M0,0 L10,0 z M2,2 L8,2 z" stroke="#000"/>'
        '</svg>'
    )
    assert find_illustrator_rings(tree) == []


def test_find_rings_skips_single_subpath():
    # No fill, no stroke, but only one subpath — that's a stroked outline,
    # not a ring. (User probably forgot to set the fill, but that's a
    # different problem than the Illustrator-ring artifact.)
    tree = _parse(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '  <path id="a" d="M0,0 L10,0 L10,10 z"/>'
        '</svg>'
    )
    assert find_illustrator_rings(tree) == []


def test_find_rings_finds_classic_outline_ring():
    tree = _parse(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '  <path id="ring" d="M0,0 L10,0 L10,10 L0,10 z M2,2 L8,2 L8,8 L2,8 z"/>'
        '</svg>'
    )
    rings = find_illustrator_rings(tree)
    assert len(rings) == 1
    assert rings[0].get("id") == "ring"


def test_find_rings_treats_fill_none_as_no_fill():
    """`fill="none"` means transparent — same effective treatment as no fill
    attr (will default to black in inkstitch)."""
    tree = _parse(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '  <path id="ring" d="M0,0 L10,0 z M2,2 L8,2 z" fill="none"/>'
        '</svg>'
    )
    assert len(find_illustrator_rings(tree)) == 1


# --- prep_svg integration with each action -------------------------------

_RING_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg">'
    '  <defs><style>.cls-1 { fill: #abc; }</style></defs>'
    '  <path class="cls-1" d="M0,0 L10,0 L10,10 z"/>'
    '  <path d="M0,0 L20,0 L20,20 L0,20 z M2,2 L18,2 L18,18 L2,18 z"/>'
    '</svg>'
)


def test_prep_default_action_is_detect():
    tree = _parse(_RING_SVG)
    stats = prep_svg(tree)
    assert stats["illustrator_rings_action"] == "detect"
    assert stats["illustrator_rings_found"] == 1
    assert stats["illustrator_rings_modified"] == 0
    # The ring path shouldn't have been touched.
    rings = find_illustrator_rings(tree)
    assert len(rings) == 1
    ring = rings[0]
    assert ring.get("display") is None
    assert ring.get("fill") is None


def test_prep_skip_sets_display_none():
    tree = _parse(_RING_SVG)
    stats = prep_svg(tree, ring_action="skip")
    assert stats["illustrator_rings_modified"] == 1
    rings = tree.getroot().xpath("//*[@display='none']")
    assert len(rings) == 1


def test_prep_fill_black_sets_explicit_fill():
    tree = _parse(_RING_SVG)
    stats = prep_svg(tree, ring_action="fill-black")
    assert stats["illustrator_rings_modified"] == 1
    # After mutation it's no longer detected as a ring (has explicit fill).
    assert find_illustrator_rings(tree) == []
    fills = tree.getroot().xpath("//*[@fill='#000000']")
    assert len(fills) == 1


def test_prep_satin_sets_inkstitch_satin_column():
    tree = _parse(_RING_SVG)
    stats = prep_svg(tree, ring_action="satin")
    assert stats["illustrator_rings_modified"] == 1
    sats = tree.getroot().xpath(
        f"//*[@*[local-name()='satin_column' and namespace-uri()='{INKSTITCH_NS}']]"
    )
    assert len(sats) == 1


def test_prep_rejects_invalid_action():
    tree = _parse(_RING_SVG)
    try:
        prep_svg(tree, ring_action="bogus")
    except ValueError as e:
        assert "ring_action must be one of" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_prep_runs_inline_fill_before_ring_detection():
    """A path whose fill comes only from a CSS class shouldn't be detected as
    a ring AFTER prep_svg inlines the fill."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '  <defs><style>.x { fill: #abc; }</style></defs>'
        '  <path class="x" d="M0,0 L10,0 z M2,2 L8,2 z"/>'
        '</svg>'
    )
    tree = _parse(svg)
    stats = prep_svg(tree, ring_action="satin")
    # Should NOT be classified as a ring — it has a class-derived fill.
    assert stats["illustrator_rings_found"] == 0
    assert stats["inlined_styles"] == 1


# --- end-to-end through the CLI -------------------------------------------

def test_cli_prep_ring_actions_round_trip(workdir, project_path):
    svg_path = workdir / "design.svg"
    svg_path.write_text(_RING_SVG)
    runner = CliRunner()
    runner.invoke(
        root, ["document", "open", "--project", project_path, "--svg", str(svg_path)],
        catch_exceptions=False,
    )
    result = runner.invoke(
        root, ["--json", "document", "prep", "--project", project_path,
               "--illustrator-rings", "satin"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["illustrator_rings_found"] == 1
    assert data["illustrator_rings_action"] == "satin"
    assert data["illustrator_rings_modified"] == 1
    assert data["illustrator_rings"][0]["subpaths"] == 2


def test_cli_prep_invalid_action_rejected(workdir, project_path):
    svg_path = workdir / "design.svg"
    svg_path.write_text(_RING_SVG)
    runner = CliRunner()
    runner.invoke(
        root, ["document", "open", "--project", project_path, "--svg", str(svg_path)],
        catch_exceptions=False,
    )
    result = runner.invoke(
        root, ["document", "prep", "--project", project_path,
               "--illustrator-rings", "bogus"],
    )
    # Click rejects invalid choices with exit 2.
    assert result.exit_code == 2
    assert "invalid value" in result.output.lower()
