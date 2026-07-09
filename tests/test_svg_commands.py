"""Tests for the engine-contract visual command structure (svg/commands.py).

Ink/Stitch reads commands via connector paths (inkscape:connection-start/end
linking a marker <use> to the target element) with the symbol in <defs>. The
bare child <use> this CLI wrote historically is silently ignored — verified
by differential stitch-plan comparison in TestCommandsChangeStitchPlan.
"""

from __future__ import annotations

import pytest
from lxml import etree

from cli_anything_inkstitch.svg.commands import (
    CONNECTION_END,
    CONNECTION_START,
    XLINK_HREF,
    attach_command,
    canonical_command,
    detach_command,
    find_commands,
    find_legacy_markers,
    move_command,
)

SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     width="40mm" height="40mm" viewBox="0 0 40 40">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  <path id="sq" d="M5,5 L35,5 L35,35 L5,35 Z" fill="#ff0000"
        inkstitch:fill_method="auto_fill"/>
</svg>
"""


@pytest.fixture
def tree():
    return etree.ElementTree(etree.fromstring(SVG.encode()))


def elem(tree):
    return tree.getroot().find(".//*[@id='sq']")


class TestAttach:
    def test_creates_engine_structure(self, tree):
        result = attach_command(tree, elem(tree), "starting_point", 5, 5)
        root = tree.getroot()
        # symbol in defs
        sym = root.find(".//*[@id='inkstitch_starting_point']")
        assert sym is not None
        assert etree.QName(sym.tag).localname == "symbol"
        # marker use
        use = root.find(f".//*[@id='{result['use_id']}']")
        assert use.get(XLINK_HREF) == "#inkstitch_starting_point"
        assert use.get("x") == "5"
        # connector linking use ↔ element
        conn = root.find(f".//*[@id='command_connector_{result['use_id'].split('_')[-1]}']")
        assert conn is not None
        assert conn.get(CONNECTION_START) == f"#{result['use_id']}"
        assert conn.get(CONNECTION_END) == "#sq"
        assert (conn.get("d") or "").startswith("M 5,5")

    def test_legacy_name_maps_to_engine_name(self, tree):
        result = attach_command(tree, elem(tree), "fill_start", 5, 5)
        assert result["command"] == "starting_point"
        assert canonical_command("fill_end") == "ending_point"

    def test_findable_after_attach(self, tree):
        attach_command(tree, elem(tree), "starting_point", 5, 5)
        cmds = find_commands(tree, elem(tree))
        assert len(cmds) == 1
        assert cmds[0]["command"] == "starting_point"
        assert cmds[0]["x"] == 5.0


class TestMoveDetach:
    def test_move_updates_use_and_connector(self, tree):
        r = attach_command(tree, elem(tree), "starting_point", 5, 5)
        move_command(tree, r["use_id"], 30, 31)
        root = tree.getroot()
        use = root.find(f".//*[@id='{r['use_id']}']")
        assert use.get("x") == "30" and use.get("y") == "31"
        conn = root.find(f".//*[@{{{'http://www.inkscape.org/namespaces/inkscape'}}}connection-start='#{r['use_id']}']")
        assert (conn.get("d") or "").startswith("M 30,31")

    def test_move_migrates_legacy_marker(self, tree):
        # the pre-contract format: bare child <use>, engine-ignored
        e = elem(tree)
        legacy = etree.SubElement(e, "{http://www.w3.org/2000/svg}use")
        legacy.set(XLINK_HREF, "#inkstitch_fill_start")
        legacy.set("id", "old_use")
        legacy.set("x", "5")
        legacy.set("y", "5")
        assert find_legacy_markers(e)
        result = move_command(tree, "old_use", 20, 20)
        assert result.get("migrated_from") == "old_use"
        assert not find_legacy_markers(e)              # gone from the element
        cmds = find_commands(tree, e)
        assert cmds and cmds[0]["command"] == "starting_point"
        assert cmds[0]["x"] == 20.0

    def test_detach_removes_whole_group(self, tree):
        r = attach_command(tree, elem(tree), "trim", 10, 10)
        detach_command(tree, r["use_id"])
        assert find_commands(tree, elem(tree)) == []
        assert tree.getroot().find(f".//*[@id='{r['group_id']}']") is None


class TestSymbolFallback:
    def test_minimal_symbol_without_binary(self, tree, monkeypatch):
        from cli_anything_inkstitch import binary
        monkeypatch.setattr(binary, "discover", lambda *a, **k: None)
        attach_command(tree, elem(tree), "starting_point", 5, 5)
        sym = tree.getroot().find(".//*[@id='inkstitch_starting_point']")
        assert sym is not None
        assert etree.QName(sym.tag).localname == "symbol"


class TestCommandsChangeStitchPlan:
    """The differential proof (task #7 methodology): moving starting_point/
    ending_point must change the engine's stitch plan. This is what the bare
    child-use format failed — plans were byte-identical wherever the markers
    sat."""

    @pytest.mark.skipif(
        __import__("cli_anything_inkstitch.binary", fromlist=["discover"]).discover() is None,
        reason="Ink/Stitch binary not installed")
    def test_moving_start_end_changes_plan(self, tmp_path):
        from cli_anything_inkstitch.artifact.design_model import (
            apply_edits, extract_stitch_blocks, read_design, stitch_plan_svg)
        from cli_anything_inkstitch.project import ProjectFile
        from cli_anything_inkstitch.svg.document import sha256_of

        svg = tmp_path / "design.svg"
        svg.write_text(SVG)
        proj_path = tmp_path / "design.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(proj_path))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()
        p = str(proj_path)

        r = apply_edits(p, [
            {"op": "attach_command", "id": "sq", "command": "starting_point",
             "x": 5, "y": 5},
            {"op": "attach_command", "id": "sq", "command": "ending_point",
             "x": 35, "y": 35},
        ])
        start_use = r["results"][0]["use_id"]
        end_use = r["results"][1]["use_id"]

        def endpoints():
            blocks = extract_stitch_blocks(stitch_plan_svg(p))
            pts = [q for b in blocks["blocks"] for path in b["paths"] for q in path]
            return tuple(round(v) for v in pts[0]), tuple(round(v) for v in pts[-1])

        first_a, last_a = endpoints()
        apply_edits(p, [
            {"op": "move_command", "use_id": start_use, "x": 35, "y": 35},
            {"op": "move_command", "use_id": end_use, "x": 5, "y": 5},
        ])
        first_b, last_b = endpoints()
        assert (first_a, last_a) != (first_b, last_b), (
            "stitch plan did not change when start/end markers swapped — "
            "commands are not reaching the engine")
        # and the positions are actually honored (near the requested corners)
        assert abs(first_a[0] - 5) <= 3 and abs(first_b[0] - 35) <= 3
