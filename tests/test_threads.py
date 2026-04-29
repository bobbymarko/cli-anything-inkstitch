"""Tests for `document set-palette` (now writes SVG metadata) and
`document list-thread-colors`."""

from __future__ import annotations

import json

from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.svg.attrs import INKSTITCH_NS
from cli_anything_inkstitch.svg.document import (
    get_inkstitch_metadata,
    set_inkstitch_metadata,
)


# --- set_inkstitch_metadata / get_inkstitch_metadata helpers -------------

def _empty_tree():
    return etree.ElementTree(etree.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg"/>'
    ))


def test_set_metadata_creates_metadata_node_if_missing():
    tree = _empty_tree()
    set_inkstitch_metadata(tree, "thread-palette", "Madeira Polyneon")
    md = tree.getroot().find("{http://www.w3.org/2000/svg}metadata")
    assert md is not None
    item = md.find(f"{{{INKSTITCH_NS}}}thread-palette")
    assert item.text == '"Madeira Polyneon"'  # JSON-encoded


def test_set_metadata_idempotent_replaces_value():
    tree = _empty_tree()
    set_inkstitch_metadata(tree, "thread-palette", "Madeira Polyneon")
    set_inkstitch_metadata(tree, "thread-palette", "Isacord")
    md = tree.getroot().find("{http://www.w3.org/2000/svg}metadata")
    items = md.findall(f"{{{INKSTITCH_NS}}}thread-palette")
    assert len(items) == 1  # not duplicated
    assert items[0].text == '"Isacord"'


def test_set_metadata_none_removes_key():
    tree = _empty_tree()
    set_inkstitch_metadata(tree, "thread-palette", "Madeira")
    set_inkstitch_metadata(tree, "thread-palette", None)
    md = tree.getroot().find("{http://www.w3.org/2000/svg}metadata")
    assert md.find(f"{{{INKSTITCH_NS}}}thread-palette") is None


def test_get_metadata_returns_decoded_value():
    tree = _empty_tree()
    set_inkstitch_metadata(tree, "thread-palette", "Madeira Polyneon")
    assert get_inkstitch_metadata(tree, "thread-palette") == "Madeira Polyneon"


def test_get_metadata_returns_none_when_absent():
    assert get_inkstitch_metadata(_empty_tree(), "thread-palette") is None


def test_get_metadata_returns_none_for_unparseable():
    tree = _empty_tree()
    set_inkstitch_metadata(tree, "thread-palette", "x")
    # Hand-corrupt the value
    md = tree.getroot().find("{http://www.w3.org/2000/svg}metadata")
    md.find(f"{{{INKSTITCH_NS}}}thread-palette").text = "not-json"
    assert get_inkstitch_metadata(tree, "thread-palette") is None


# --- end-to-end: set-palette writes both session and SVG metadata --------

_PALETTE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '  <rect id="bg"  x="0" y="0" width="100" height="100" fill="#000000"/>'
    '  <circle id="dot" cx="50" cy="50" r="10" fill="#e57263"/>'
    '  <rect id="r2"  x="20" y="20" width="10" height="10" fill="#e57263"/>'
    '  <path id="ring" d="M0,0 L10,10 z M2,2 L8,8 z"/>'  # default-black
    '</svg>'
)


def _open(workdir, project_path):
    svg_path = workdir / "design.svg"
    svg_path.write_text(_PALETTE_SVG)
    runner = CliRunner()
    runner.invoke(
        root, ["document", "open", "--project", project_path, "--svg", str(svg_path)],
        catch_exceptions=False,
    )
    return runner, svg_path


def test_set_palette_writes_svg_metadata(workdir, project_path):
    runner, svg_path = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "document", "set-palette",
               "--project", project_path,
               "--palette", "Madeira Polyneon"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["thread_palette"] == "Madeira Polyneon"
    assert data["wrote_svg_metadata"] is True
    # Verify the metadata is actually in the SVG.
    raw = svg_path.read_text()
    assert "thread-palette" in raw
    assert '"Madeira Polyneon"' in raw  # JSON-encoded


def test_set_palette_persists_in_session_json(workdir, project_path):
    runner, _ = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "set-palette", "--project", project_path,
               "--palette", "Isacord"],
    )
    proj_data = json.loads(open(project_path).read())
    assert proj_data["session"]["thread_palette"] == "Isacord"


def test_set_palette_idempotent_doesnt_duplicate(workdir, project_path):
    runner, svg_path = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "set-palette", "--project", project_path,
               "--palette", "Madeira Polyneon"],
    )
    runner.invoke(
        root, ["document", "set-palette", "--project", project_path,
               "--palette", "Isacord"],  # change palette
    )
    raw = svg_path.read_text()
    # Only the latter palette should remain
    assert raw.count("Isacord") == 1
    assert "Madeira Polyneon" not in raw


# --- list-thread-colors --------------------------------------------------

def test_list_thread_colors_groups_by_unique_color(workdir, project_path):
    runner, _ = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "document", "list-thread-colors",
               "--project", project_path],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    by_hex = {c["hex"]: c for c in data["colors"]}
    # bg is #000000 and so is the default-black ring → 2 elements at black
    assert by_hex["#000000"]["element_count"] == 2
    # dot + r2 share #e57263 → 2 elements
    assert by_hex["#e57263"]["element_count"] == 2
    assert by_hex["#e57263"]["name"] == "salmon"
    assert data["unique_count"] == 2


def test_list_thread_colors_includes_palette_when_set(workdir, project_path):
    runner, _ = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "set-palette", "--project", project_path,
               "--palette", "Madeira Polyneon"],
    )
    result = runner.invoke(
        root, ["--json", "document", "list-thread-colors",
               "--project", project_path],
    )
    data = json.loads(result.output)
    assert data["thread_palette"] == "Madeira Polyneon"


def test_list_thread_colors_palette_null_when_unset(workdir, project_path):
    runner, _ = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "document", "list-thread-colors",
               "--project", project_path],
    )
    data = json.loads(result.output)
    assert data["thread_palette"] is None


def test_list_thread_colors_sorted_by_frequency(workdir, project_path):
    runner, _ = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "document", "list-thread-colors",
               "--project", project_path],
    )
    data = json.loads(result.output)
    counts = [c["element_count"] for c in data["colors"]]
    assert counts == sorted(counts, reverse=True)
