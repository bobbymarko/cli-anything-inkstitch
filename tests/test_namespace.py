"""Tests for ensure_inkstitch_namespace — confirms `inkstitch:` prefix
sticks on attrs added after open."""

from __future__ import annotations

import json

from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.svg.attrs import (
    INKSTITCH_NS,
    ensure_inkstitch_namespace,
    set_inkstitch,
)


# --- direct unit tests ---------------------------------------------------

def test_namespace_added_via_tree():
    """Pass a tree → root nsmap actually carries inkstitch."""
    tree = etree.ElementTree(etree.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg"><path id="p"/></svg>'
    ))
    changed = ensure_inkstitch_namespace(tree)
    assert changed is True
    assert tree.getroot().nsmap.get("inkstitch") == INKSTITCH_NS


def test_namespace_idempotent():
    """Second call returns False without modifying."""
    tree = etree.ElementTree(etree.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:inkstitch="http://inkstitch.org/namespace"/>'
    ))
    assert ensure_inkstitch_namespace(tree) is False


def test_inkstitch_attrs_use_short_prefix_after_fix():
    """After ensure_inkstitch_namespace via tree, setting an inkstitch attr
    on a child uses `inkstitch:` (not `ns0:`)."""
    tree = etree.ElementTree(etree.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg"><path id="p"/></svg>'
    ))
    ensure_inkstitch_namespace(tree)
    path = tree.getroot().find("{http://www.w3.org/2000/svg}path")
    set_inkstitch(path, "satin_column", True)
    raw = etree.tostring(tree).decode()
    assert "inkstitch:satin_column" in raw
    # Negative: no per-element ns0:/ns1: declarations should appear.
    assert "ns0:" not in raw and "ns1:" not in raw


def test_namespace_via_root_with_parent_still_works():
    """When the element has a parent, the in-tree replace path runs."""
    tree = etree.ElementTree(etree.fromstring(
        '<container xmlns="http://example.com/c">'
        '  <svg xmlns="http://www.w3.org/2000/svg"><path id="p"/></svg>'
        '</container>'
    ))
    inner = tree.getroot().find("{http://www.w3.org/2000/svg}svg")
    assert ensure_inkstitch_namespace(inner) is True
    assert tree.getroot().find("{http://www.w3.org/2000/svg}svg").nsmap.get(
        "inkstitch"
    ) == INKSTITCH_NS


def test_namespace_via_root_only_falls_back():
    """Document root with no tree handle → in-place attribute swap. Children
    survive but nsmap can't update (lxml limitation; documented). Subsequent
    inkstitch attrs WILL get nsN: prefixes — that's the fallback we want
    callers to avoid by passing the tree."""
    root_only = etree.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg"><path id="p"/></svg>'
    )
    assert ensure_inkstitch_namespace(root_only) is True
    # Children survive
    assert root_only.find("{http://www.w3.org/2000/svg}path") is not None


def test_children_preserved_through_namespace_fix():
    """The replace path must not lose existing children/attribs."""
    tree = etree.ElementTree(etree.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg" id="root" data-x="y">'
        '  <metadata>m</metadata>'
        '  <path id="p" d="M0,0 L1,1"/>'
        '  <rect id="r" x="0" y="0" width="10" height="10"/>'
        '</svg>'
    ))
    ensure_inkstitch_namespace(tree)
    new_root = tree.getroot()
    assert new_root.get("id") == "root"
    assert new_root.get("data-x") == "y"
    assert len(new_root.findall("{http://www.w3.org/2000/svg}path")) == 1
    assert len(new_root.findall("{http://www.w3.org/2000/svg}rect")) == 1
    assert len(new_root.findall("{http://www.w3.org/2000/svg}metadata")) == 1


# --- end-to-end via the CLI ----------------------------------------------

_CSS_RING_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '  <path id="ring" d="M0,0 L20,0 L20,20 L0,20 z M2,2 L18,2 L18,18 L2,18 z"/>'
    '</svg>'
)


def _open(workdir, project_path):
    svg_path = workdir / "design.svg"
    svg_path.write_text(_CSS_RING_SVG)
    runner = CliRunner()
    runner.invoke(
        root, ["document", "open", "--project", project_path, "--svg", str(svg_path)],
        catch_exceptions=False,
    )
    return runner, svg_path


def test_prep_satin_writes_clean_inkstitch_prefix(workdir, project_path):
    """End-to-end regression for the `ns0:satin_column` bug:
    document prep --illustrator-rings=satin should produce
    `inkstitch:satin_column` not `ns0:satin_column`."""
    runner, svg_path = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "prep", "--project", project_path,
               "--illustrator-rings", "satin"],
        catch_exceptions=False,
    )
    raw = svg_path.read_text()
    assert "inkstitch:satin_column" in raw
    assert "ns0:" not in raw and "ns1:" not in raw
    # Root should declare xmlns:inkstitch
    assert 'xmlns:inkstitch="http://inkstitch.org/namespace"' in raw


def test_set_palette_writes_clean_metadata_prefix(workdir, project_path):
    """Same regression for set-palette metadata: should be
    `<inkstitch:thread-palette>` not `<ns0:thread-palette xmlns:ns0=...>`."""
    runner, svg_path = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "set-palette", "--project", project_path,
               "--palette", "Madeira Polyneon"],
        catch_exceptions=False,
    )
    raw = svg_path.read_text()
    assert "<inkstitch:thread-palette>" in raw
    assert "ns0:" not in raw and "ns1:" not in raw
