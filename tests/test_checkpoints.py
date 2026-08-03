"""Persistent history checkpoints (task #51).

The design promise under test: a flagged state is durable INDEPENDENT of the
history ring buffer — materialized to a content-addressed SVG snapshot at
flag time, restorable as a normal undoable history entry even after every
history entry that produced it has been evicted.
"""

from __future__ import annotations

import hashlib
import json

from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.artifact.design_model import apply_edits
from cli_anything_inkstitch.checkpoints import (
    checkpoint_dir,
    create_checkpoint,
    delete_checkpoint,
    annotate_checkpoint,
    list_checkpoints,
    restore_checkpoint,
)
from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import sha256_of

SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     width="30mm" height="30mm" viewBox="0 0 113.386 113.386">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  <path id="r1" d="M10,20 L100,20" fill="none" stroke="#000000"/>
  <path id="r2" d="M10,50 L100,50" fill="none" stroke="#000000"/>
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


def set_attr(proj_path, elem_id, name, value):
    apply_edits(proj_path, [{"op": "set_attr", "id": elem_id,
                             "name": name, "value": value}])


def entry_ids(proj_path):
    return [e["id"] for e in ProjectFile.load(proj_path).history["entries"]]


class TestCreate:
    def test_current_state_snapshot_and_index(self, tmp_path):
        proj_path, svg = make_project(tmp_path)
        record = create_checkpoint(proj_path, "baseline")
        assert record["annotation"] == "baseline"
        assert record["auto"] is False
        sha = record["svg_sha256"]
        snap = checkpoint_dir(proj_path) / f"{sha[:16]}.svg"
        assert snap.exists()
        assert hashlib.sha256(snap.read_bytes()).hexdigest() == sha
        assert record["thumbnail"] is True
        assert (checkpoint_dir(proj_path) / f"{sha[:16]}.png").exists()
        assert [c["id"] for c in list_checkpoints(proj_path)] == [record["id"]]

    def test_same_content_dedups_snapshot_file(self, tmp_path):
        proj_path, _svg = make_project(tmp_path)
        a = create_checkpoint(proj_path, "one")
        b = create_checkpoint(proj_path, "two")
        assert a["svg_sha256"] == b["svg_sha256"]
        svgs = list(checkpoint_dir(proj_path).glob("*.svg"))
        assert len(svgs) == 1
        assert len(list_checkpoints(proj_path)) == 2

    def test_flag_older_history_entry(self, tmp_path):
        proj_path, _svg = make_project(tmp_path)
        set_attr(proj_path, "r1", "bean_stitch_repeats", "1")
        set_attr(proj_path, "r2", "bean_stitch_repeats", "3")
        first, _second = entry_ids(proj_path)
        record = create_checkpoint(proj_path, "after first edit only",
                                   history_entry_id=first)
        snap = checkpoint_dir(proj_path) / f"{record['svg_sha256'][:16]}.svg"
        root_el = etree.fromstring(snap.read_bytes())
        NS = "{http://inkstitch.org/namespace}bean_stitch_repeats"
        vals = {p.get("id"): p.get(NS) for p in root_el.iter()
                if p.get("id") in ("r1", "r2")}
        # r1's edit is in that state; r2's later edit is not
        assert vals == {"r1": "1", "r2": None}

    def test_flag_redo_branch_entry(self, tmp_path):
        proj_path, _svg = make_project(tmp_path)
        set_attr(proj_path, "r1", "bean_stitch_repeats", "1")
        CliRunner().invoke(root, ["--json", "session", "undo",
                                  "--project", proj_path])
        eid = entry_ids(proj_path)[0]
        record = create_checkpoint(proj_path, "the undone state",
                                   history_entry_id=eid)
        snap = checkpoint_dir(proj_path) / f"{record['svg_sha256'][:16]}.svg"
        assert b"bean_stitch_repeats" in snap.read_bytes()

    def test_flagging_evicted_entry_fails_clearly(self, tmp_path):
        import pytest
        proj_path, _svg = make_project(tmp_path)
        with pytest.raises(UserError, match="no longer in the ring buffer"):
            create_checkpoint(proj_path, "x", history_entry_id="h_GONE")


class TestRestore:
    def test_restore_is_a_normal_undoable_entry(self, tmp_path):
        proj_path, svg = make_project(tmp_path)
        record = create_checkpoint(proj_path, "before styling")
        set_attr(proj_path, "r1", "bean_stitch_repeats", "9")
        assert "bean_stitch_repeats" in svg.read_text()

        result = restore_checkpoint(proj_path, record["id"])
        assert result["restored"] == record["id"]
        assert "bean_stitch_repeats" not in svg.read_text()
        # the restore is on the history stack — undo brings the edit back
        proj = ProjectFile.load(proj_path)
        assert proj.history["entries"][-1]["command"].startswith(
            "session checkpoint restore")
        r = CliRunner().invoke(root, ["--json", "session", "undo",
                                      "--project", proj_path])
        assert r.exit_code == 0, r.output
        assert "bean_stitch_repeats" in svg.read_text()

    def test_survives_total_ring_eviction(self, tmp_path):
        """THE durability property: restore works after every history entry
        from flag time has been evicted from the 50-entry ring."""
        proj_path, svg = make_project(tmp_path)
        record = create_checkpoint(proj_path, "keeper")
        for i in range(60):
            set_attr(proj_path, "r1", "bean_stitch_repeats", str(i % 7))
        proj = ProjectFile.load(proj_path)
        assert len(proj.history["entries"]) == 50  # ring rolled over
        result = restore_checkpoint(proj_path, record["id"])
        assert result["restored"] == record["id"]
        assert "bean_stitch_repeats" not in svg.read_text()

    def test_corrupted_snapshot_refused(self, tmp_path):
        import pytest
        proj_path, _svg = make_project(tmp_path)
        record = create_checkpoint(proj_path, "x")
        snap = checkpoint_dir(proj_path) / f"{record['svg_sha256'][:16]}.svg"
        snap.write_bytes(snap.read_bytes() + b"<!-- tampered -->")
        with pytest.raises(UserError, match="does not match its recorded"):
            restore_checkpoint(proj_path, record["id"])


class TestAnnotateDelete:
    def test_annotate_updates_index(self, tmp_path):
        proj_path, _svg = make_project(tmp_path)
        record = create_checkpoint(proj_path, "first thought")
        annotate_checkpoint(proj_path, record["id"], "better thought")
        assert list_checkpoints(proj_path)[0]["annotation"] == "better thought"

    def test_delete_removes_file_only_when_unreferenced(self, tmp_path):
        proj_path, _svg = make_project(tmp_path)
        a = create_checkpoint(proj_path, "one")
        b = create_checkpoint(proj_path, "two")  # same content, same file
        snap = checkpoint_dir(proj_path) / f"{a['svg_sha256'][:16]}.svg"
        delete_checkpoint(proj_path, a["id"])
        assert snap.exists()  # b still references it
        delete_checkpoint(proj_path, b["id"])
        assert not snap.exists()
        assert list_checkpoints(proj_path) == []


class TestCLI:
    def test_create_list_restore_via_cli(self, tmp_path):
        proj_path, svg = make_project(tmp_path)
        r = CliRunner().invoke(root, ["--json", "session", "checkpoint",
                                      "create", "--project", proj_path,
                                      "-m", "cli flag"])
        assert r.exit_code == 0, r.output
        cp_id = json.loads(r.output)["checkpoint"]["id"]

        set_attr(proj_path, "r1", "bean_stitch_repeats", "2")
        r = CliRunner().invoke(root, ["--json", "session", "checkpoint",
                                      "list", "--project", proj_path])
        assert json.loads(r.output)["checkpoints"][0]["annotation"] == "cli flag"

        r = CliRunner().invoke(root, ["--json", "session", "checkpoint",
                                      "restore", "--project", proj_path,
                                      "--id", cp_id])
        assert r.exit_code == 0, r.output
        assert "bean_stitch_repeats" not in svg.read_text()


class TestAutoCheckpoint:
    def _route(self, proj_path, monkeypatch):
        from cli_anything_inkstitch.commands import tools as tools_mod

        def fake_run_extension(binary, ext, svg_path, args=None, ids=None,
                               capture_stdout=False, **kw):
            return etree.tostring(etree.parse(svg_path).getroot())
        monkeypatch.setattr(tools_mod, "require", lambda *a, **k: "/fake")
        monkeypatch.setattr(tools_mod, "run_extension", fake_run_extension)
        return CliRunner().invoke(root, ["--json", "tools", "auto-run",
                                         "--project", proj_path,
                                         "--ids", "r1,r2"])

    def test_auto_checkpoint_taken_before_routing(self, tmp_path, monkeypatch):
        proj_path, _svg = make_project(tmp_path)
        r = self._route(proj_path, monkeypatch)
        assert r.exit_code == 0, r.output
        cps = list_checkpoints(proj_path)
        assert len(cps) == 1
        assert cps[0]["auto"] is True
        assert "auto_run" in cps[0]["annotation"]

    def test_auto_checkpoints_pruned_user_flags_kept(self, tmp_path, monkeypatch):
        proj_path, _svg = make_project(tmp_path)
        keeper = create_checkpoint(proj_path, "user flag")
        for i in range(8):
            # distinct content each round so snapshots don't dedup away
            set_attr(proj_path, "r1", "bean_stitch_repeats", str(i))
            r = self._route(proj_path, monkeypatch)
            assert r.exit_code == 0, r.output
        cps = list_checkpoints(proj_path)
        autos = [c for c in cps if c["auto"]]
        assert len(autos) == 5  # AUTO_KEEP
        assert any(c["id"] == keeper["id"] for c in cps)  # user flag survives
