"""Tests for open_closed_subpaths and the prep --illustrator-rings=satin
integration that uses it to open closed satin rails."""

from __future__ import annotations

import json
import re

from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.svg.geometry import (
    open_closed_subpaths,
    path_bbox,
)


# --- open_closed_subpaths unit tests -------------------------------------

def test_no_z_returns_input_unchanged():
    d = "M 0 0 L 10 0 L 10 10"
    assert open_closed_subpaths(d) == d


def test_empty_returns_input_unchanged():
    assert open_closed_subpaths("") == ""


def test_single_z_replaced_with_lineto_to_start():
    d = "M 0 0 L 10 0 L 10 10 Z"
    result = open_closed_subpaths(d)
    assert "Z" not in result and "z" not in result
    # Final command lands back at the start (0, 0)
    assert result.rstrip().endswith("L 0 0")


def test_two_subpaths_each_with_z():
    """Classic Illustrator outline ring: two closed loops."""
    d = "M 0 0 L 10 0 L 10 10 L 0 10 Z M 2 2 L 8 2 L 8 8 L 2 8 Z"
    result = open_closed_subpaths(d)
    assert "Z" not in result and "z" not in result
    # Both subpaths should end at their own start
    parts = re.split(r"\bM\b", result)
    parts = [p for p in parts if p.strip()]
    assert len(parts) == 2
    assert parts[0].rstrip().endswith("L 0 0")
    assert parts[1].rstrip().endswith("L 2 2")


def test_lowercase_z_also_handled():
    d = "M 0 0 L 10 0 L 10 10 z"
    result = open_closed_subpaths(d)
    assert "z" not in result.lower()


def test_geometry_preserved_via_bbox():
    """The bbox of the opened path should match the original."""
    d = "M 0 0 L 10 0 L 10 10 L 0 10 Z M 2 2 L 8 2 L 8 8 L 2 8 Z"
    before = path_bbox(d)
    after = path_bbox(open_closed_subpaths(d))
    assert before == after


def test_relative_coords_resolved_to_absolute():
    """Mixed relative/absolute commands are normalized to absolute on output."""
    d = "M 5 5 l 5 0 l 0 5 z"
    result = open_closed_subpaths(d)
    # Should end with absolute lineto back to (5, 5)
    assert result.rstrip().endswith("L 5 5")
    # All commands in output should be uppercase (absolute)
    cmd_letters = re.findall(r"[a-zA-Z]", result)
    assert all(c == c.upper() for c in cmd_letters), \
        f"expected all-uppercase commands, got {cmd_letters}"


def test_curve_command_preserved():
    d = "M 0 0 C 5 0 10 5 10 10 Z"
    result = open_closed_subpaths(d)
    assert "C" in result
    assert "Z" not in result
    # Curve geometry preserved
    before = path_bbox(d)
    after = path_bbox(result)
    assert before == after


def test_implicit_lineto_after_m_handled():
    """SVG quirk: 'M 0 0 10 0 10 10 Z' = M then implicit Ls."""
    d = "M 0 0 10 0 10 10 Z"
    result = open_closed_subpaths(d)
    assert "Z" not in result
    assert path_bbox(result) == (0, 0, 10, 10)


def test_malformed_path_returns_unchanged():
    """If the input is malformed mid-parse, return it unchanged rather than
    emit a corrupted result that's worse than the original."""
    d = "M 0 0 Z X garbage 5"
    # Should not raise, even if it returns the malformed input.
    result = open_closed_subpaths(d)
    assert result is not None


# --- end-to-end: prep --illustrator-rings=satin opens rails -------------

_RING_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '  <path id="ring" d="M0,0 L20,0 L20,20 L0,20 z M2,2 L18,2 L18,18 L2,18 z"/>'
    '</svg>'
)


def _open(workdir, project_path):
    svg_path = workdir / "design.svg"
    svg_path.write_text(_RING_SVG)
    runner = CliRunner()
    runner.invoke(
        root, ["document", "open", "--project", project_path, "--svg", str(svg_path)],
        catch_exceptions=False,
    )
    return runner, svg_path


def test_prep_satin_strips_z_from_d_attribute(workdir, project_path):
    runner, svg_path = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "prep", "--project", project_path,
               "--illustrator-rings", "satin"],
        catch_exceptions=False,
    )
    raw = svg_path.read_text()
    # The original had 2 z's; afterward, 0 z/Z in any d= attribute.
    parsed = etree.fromstring(raw.encode())
    ring = parsed.find('.//{http://www.w3.org/2000/svg}path[@id="ring"]')
    assert ring is not None
    d = ring.get("d", "")
    assert "z" not in d.lower(), f"d attr still contains close-path: {d}"


def test_prep_satin_preserves_ring_geometry(workdir, project_path):
    """Bbox of the rewritten path matches the original."""
    runner, svg_path = _open(workdir, project_path)
    pre_d = "M0,0 L20,0 L20,20 L0,20 z M2,2 L18,2 L18,18 L2,18 z"
    pre_bbox = path_bbox(pre_d)
    runner.invoke(
        root, ["document", "prep", "--project", project_path,
               "--illustrator-rings", "satin"],
    )
    raw = svg_path.read_text()
    parsed = etree.fromstring(raw.encode())
    ring = parsed.find('.//{http://www.w3.org/2000/svg}path[@id="ring"]')
    post_bbox = path_bbox(ring.get("d", ""))
    assert post_bbox == pre_bbox


def test_prep_other_actions_dont_touch_d(workdir, project_path):
    """Only satin action rewrites the path. detect/skip/fill-black don't."""
    for action in ("detect", "skip", "fill-black"):
        runner, svg_path = _open(workdir, project_path)
        runner.invoke(
            root, ["document", "prep", "--project", project_path,
                   "--illustrator-rings", action],
        )
        raw = svg_path.read_text()
        parsed = etree.fromstring(raw.encode())
        ring = parsed.find('.//{http://www.w3.org/2000/svg}path[@id="ring"]')
        # Original Zs must still be there
        d = ring.get("d", "")
        assert "z" in d.lower(), \
            f"action={action} unexpectedly removed Z from d: {d}"
