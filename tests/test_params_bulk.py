"""Bulk `params set --ids` (task #48).

Styling N routed elements used to take N processes and N history entries —
which flooded the 50-slot ring buffer and evicted the snapshot that would
have made a bad bulk operation a one-undo fix (rose-bag session, 2026-08).
Bulk mode must be: one process, one history entry, atomic undo, per-element
failures reported without aborting the rest.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import sha256_of

SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     width="30mm" height="30mm" viewBox="0 0 113.386 113.386">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  <path id="r1" d="M10,20 L100,20" fill="none" stroke="#000000"/>
  <path id="r2" d="M10,50 L100,50" fill="none" stroke="#000000"/>
  <path id="r3" d="M10,80 L100,80" fill="none" stroke="#000000"/>
</svg>
"""


def make_project(tmp_path):
    svg = tmp_path / "d.svg"
    svg.write_text(SVG)
    proj_path = tmp_path / "d.inkstitch-cli.json"
    proj, _ = ProjectFile.load_or_create(str(proj_path))
    proj.svg_path = str(svg)
    proj.svg_sha256 = sha256_of(svg)
    proj.save()
    return str(proj_path), svg


def set_params(proj_path, *args):
    return CliRunner().invoke(root, ["--json", "params", "set",
                                     "--project", proj_path, *args])


class TestBulkParamsSet:
    def test_bulk_applies_to_all_with_one_history_entry(self, tmp_path):
        proj_path, svg = make_project(tmp_path)
        r = set_params(proj_path, "--ids", "r1,r2,r3",
                       "--bean_stitch_repeats=1",
                       "--running_stitch_length_mm=2.5")
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload["history_entries"] == 1
        assert [a["id"] for a in payload["applied"]] == ["r1", "r2", "r3"]
        assert payload["errors"] == []

        text = svg.read_text()
        assert text.count('bean_stitch_repeats="1"') == 3

        proj = ProjectFile.load(proj_path)
        entries = [e for e in proj.history["entries"]
                   if e["command"].startswith("params set")]
        assert len(entries) == 1
        assert entries[0]["patch"]["type"] == "multi"
        assert len(entries[0]["patch"]["patches"]) == 3

    def test_bulk_undo_is_atomic(self, tmp_path):
        proj_path, svg = make_project(tmp_path)
        r = set_params(proj_path, "--ids", "r1,r2,r3",
                       "--bean_stitch_repeats=1")
        assert r.exit_code == 0, r.output
        assert svg.read_text().count("bean_stitch_repeats") == 3

        from cli_anything_inkstitch.artifact.design_model import apply_history_step
        apply_history_step(proj_path)
        assert "bean_stitch_repeats" not in svg.read_text()
        # and redo restores all three
        apply_history_step(proj_path, redo=True)
        assert svg.read_text().count("bean_stitch_repeats") == 3

    def test_per_element_failure_does_not_abort_the_rest(self, tmp_path):
        proj_path, svg = make_project(tmp_path)
        r = set_params(proj_path, "--ids", "r1,missing,r3",
                       "--bean_stitch_repeats=1")
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert [e["id"] for e in payload["errors"]] == ["missing"]
        assert [a["id"] for a in payload["applied"]] == ["r1", "r3"]
        assert svg.read_text().count("bean_stitch_repeats") == 2

    def test_exactly_one_of_id_and_ids(self, tmp_path):
        proj_path, _svg = make_project(tmp_path)
        r = set_params(proj_path, "--id", "r1", "--ids", "r1,r2",
                       "--bean_stitch_repeats=1")
        assert r.exit_code != 0
        r = set_params(proj_path, "--bean_stitch_repeats=1")
        assert r.exit_code != 0

    def test_single_id_mode_unchanged(self, tmp_path):
        proj_path, svg = make_project(tmp_path)
        r = set_params(proj_path, "--id", "r1", "--bean_stitch_repeats=1")
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload["id"] == "r1"
        assert payload["changed"] == {"bean_stitch_repeats": "1"}
        proj = ProjectFile.load(proj_path)
        entry = next(e for e in proj.history["entries"]
                     if e["command"].startswith("params set"))
        assert entry["patch"]["type"] == "attr_diff"
