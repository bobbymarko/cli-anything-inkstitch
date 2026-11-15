"""One editor action = one undo step.

The editor sends a group move, an align or a nudge burst as ONE /edit batch
(set_path per shape plus move_command per start/end marker). Each op used to
record its own history entry, so an align of three marked satins took nine
Cmd+Z presses to undo. apply_edits now folds a batch into one entry.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def project(tmp_path):
    from cli_anything_inkstitch.project import ProjectFile
    from cli_anything_inkstitch.svg.document import sha256_of
    svg = tmp_path / "d.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" '
        'viewBox="0 0 100 100">'
        '<path id="a" d="M0,0 L10,0 L10,10 Z" style="fill:#c33"/>'
        '<path id="b" d="M20,0 L30,0 L30,10 Z" style="fill:#c33"/>'
        '<path id="c" d="M40,0 L50,0 L50,10 Z" style="fill:#c33"/></svg>')
    proj_path = tmp_path / "d.inkstitch-cli.json"
    proj, _ = ProjectFile.load_or_create(str(proj_path))
    proj.svg_path = str(svg)
    proj.svg_sha256 = sha256_of(svg)
    proj.save()
    return str(proj_path)


def _d(project_file, svg_id):
    from cli_anything_inkstitch.artifact.design_model import read_design
    return next(o["d"] for o in read_design(project_file)["objects"] if o["id"] == svg_id)


def _entries(project_file):
    from cli_anything_inkstitch.project import ProjectFile
    return ProjectFile.load(project_file).history.get("entries", [])


class TestBatchIsOneUndoStep:
    def test_three_shape_move_is_one_entry_and_one_undo(self, project):
        from cli_anything_inkstitch.artifact.design_model import (
            apply_edits, apply_history_step)
        before = {k: _d(project, k) for k in "abc"}
        n0 = len(_entries(project))
        apply_edits(project, [
            {"op": "set_path", "id": "a", "d": "M5,5 L15,5 L15,15 Z"},
            {"op": "set_path", "id": "b", "d": "M25,5 L35,5 L35,15 Z"},
            {"op": "set_path", "id": "c", "d": "M45,5 L55,5 L55,15 Z"},
        ])
        assert len(_entries(project)) == n0 + 1
        apply_history_step(project)                       # ONE undo
        assert {k: _d(project, k) for k in "abc"} == before
        apply_history_step(project, redo=True)            # ONE redo
        assert _d(project, "c") == "M45,5 L55,5 L55,15 Z"

    def test_single_op_keeps_its_own_entry(self, project):
        from cli_anything_inkstitch.artifact.design_model import apply_edits
        apply_edits(project, [{"op": "set_path", "id": "a", "d": "M1,1 L2,1 Z"}])
        assert _entries(project)[-1]["command"].startswith("artifact edit set_path")
