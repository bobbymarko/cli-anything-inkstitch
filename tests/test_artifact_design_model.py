"""Tests for the artifact design-model layer (read + edit ops + prep guarantee)."""

from __future__ import annotations

import json

import pytest

from cli_anything_inkstitch.artifact.design_model import (
    apply_edits,
    ensure_prepped,
    read_design,
    split_subpaths,
    stitch_plan_svg,
)
from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.errors import ProjectError, UserError
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import sha256_of

SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     width="30mm" height="30mm" viewBox="0 0 30 30">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  <path id="elem_fill" d="M2,2 L14,2 L14,14 L2,14 Z" fill="#f17095"
        inkstitch:fill_method="auto_fill" inkstitch:angle="0">
    <use id="use_start" xlink:href="#inkstitch_fill_start" x="2.5" y="2.5"/>
    <use id="use_end" xlink:href="#inkstitch_fill_end" x="13.5" y="13.5"/>
  </path>
  <path id="elem_satin" fill="none" stroke="#000000"
        d="M16,2 C18,6 18,10 16,14 M22,2 C20,6 20,10 22,14 M16,2 L22,2 M16,14 L22,14"
        inkstitch:satin_column="True"/>
  <path id="elem_run" d="M2,20 L28,20" fill="none" stroke="#000000"/>
</svg>
"""


@pytest.fixture
def project(tmp_path):
    svg = tmp_path / "design.svg"
    svg.write_text(SVG)
    proj_path = tmp_path / "design.inkstitch-cli.json"
    proj, _created = ProjectFile.load_or_create(str(proj_path))
    proj.svg_path = str(svg)
    proj.svg_sha256 = sha256_of(svg)
    proj.save()
    return str(proj_path)


class TestSplitSubpaths:
    def test_multiple_subpaths(self):
        d = "M0,0 L1,1 M2,2 L3,3 m4,4 l1,0"
        assert split_subpaths(d) == ["M0,0 L1,1", "M2,2 L3,3", "m4,4 l1,0"]

    def test_single(self):
        assert split_subpaths("M0,0 L5,5 Z") == ["M0,0 L5,5 Z"]

    def test_empty(self):
        assert split_subpaths("") == []


class TestReadDesign:
    def test_document_dimensions(self, project):
        design = read_design(project)
        assert design["width"] == "30mm"
        assert design["viewBox"] == "0 0 30 30"

    def test_objects_classified(self, project):
        design = read_design(project)
        kinds = {o["id"]: o["kind"] for o in design["objects"]}
        assert kinds == {"elem_fill": "fill", "elem_satin": "satin", "elem_run": "run"}

    def test_satin_rails_and_rungs(self, project):
        design = read_design(project)
        satin = next(o for o in design["objects"] if o["id"] == "elem_satin")
        assert len(satin["rails"]) == 2
        assert satin["rails"][0].startswith("M16,2")
        assert len(satin["rungs"]) == 2

    def test_fill_start_end_handles(self, project):
        design = read_design(project)
        fill = next(o for o in design["objects"] if o["id"] == "elem_fill")
        assert fill["start"] == {"x": 2.5, "y": 2.5, "use_id": "use_start"}
        assert fill["end"] == {"x": 13.5, "y": 13.5, "use_id": "use_end"}

    def test_params_exposed(self, project):
        design = read_design(project)
        fill = next(o for o in design["objects"] if o["id"] == "elem_fill")
        assert fill["params"]["angle"] == "0"
        assert fill["params"]["fill_method"] == "auto_fill"

    def test_command_uses_not_objects(self, project):
        design = read_design(project)
        ids = [o["id"] for o in design["objects"]]
        assert "use_start" not in ids

    def test_external_edit_detected(self, project):
        proj = ProjectFile.load(project)
        with open(proj.svg_path, "a") as f:
            f.write("<!-- external edit -->")
        with pytest.raises(ProjectError, match="modified outside"):
            read_design(project)


class TestApplyEdits:
    def test_set_attr(self, project):
        result = apply_edits(project, [
            {"op": "set_attr", "id": "elem_fill", "name": "angle", "value": "30"},
        ])
        assert result["applied"] == 1
        fill = next(o for o in read_design(project)["objects"] if o["id"] == "elem_fill")
        assert fill["params"]["angle"] == "30"

    def test_sha_stays_coherent(self, project):
        result = apply_edits(project, [
            {"op": "set_attr", "id": "elem_fill", "name": "angle", "value": "45"},
        ])
        proj = ProjectFile.load(project)
        assert proj.svg_sha256 == result["svg_sha256"] == sha256_of(proj.svg_path)
        # and a follow-up read works without --force
        read_design(project)

    def test_history_entry_per_op(self, project):
        before = len(ProjectFile.load(project).history["entries"])
        apply_edits(project, [
            {"op": "set_attr", "id": "elem_fill", "name": "angle", "value": "10"},
            {"op": "set_attr", "id": "elem_fill", "name": "row_spacing_mm", "value": "0.3"},
        ])
        after = len(ProjectFile.load(project).history["entries"])
        assert after == before + 2

    def test_set_path(self, project):
        apply_edits(project, [{"op": "set_path", "id": "elem_run", "d": "M2,22 L28,22"}])
        run = next(o for o in read_design(project)["objects"] if o["id"] == "elem_run")
        assert run["d"] == "M2,22 L28,22"

    def test_move_command(self, project):
        apply_edits(project, [{"op": "move_command", "use_id": "use_start", "x": 8.0, "y": 2.1}])
        fill = next(o for o in read_design(project)["objects"] if o["id"] == "elem_fill")
        assert fill["start"]["x"] == 8.0
        assert fill["start"]["y"] == 2.1

    def test_attach_and_detach_command(self, project):
        result = apply_edits(project, [
            {"op": "attach_command", "id": "elem_run", "command": "trim", "x": 28, "y": 20},
        ])
        use_id = result["results"][0]["use_id"]
        run = next(o for o in read_design(project)["objects"] if o["id"] == "elem_run")
        assert run["commands"][0]["command"] == "trim"
        apply_edits(project, [{"op": "detach_command", "use_id": use_id}])
        run = next(o for o in read_design(project)["objects"] if o["id"] == "elem_run")
        assert run["commands"] == []

    def test_del_attr(self, project):
        apply_edits(project, [{"op": "del_attr", "id": "elem_fill", "name": "angle"}])
        fill = next(o for o in read_design(project)["objects"] if o["id"] == "elem_fill")
        assert "angle" not in fill["params"]

    def test_unknown_op_aborts_batch(self, project):
        sha_before = ProjectFile.load(project).svg_sha256
        with pytest.raises(UserError, match="unknown edit op"):
            apply_edits(project, [
                {"op": "set_attr", "id": "elem_fill", "name": "angle", "value": "60"},
                {"op": "explode"},
            ])
        # nothing written: first op rolled back with the batch
        assert ProjectFile.load(project).svg_sha256 == sha_before
        fill = next(o for o in read_design(project)["objects"] if o["id"] == "elem_fill")
        assert fill["params"]["angle"] == "0"

    def test_unknown_element_rejected(self, project):
        with pytest.raises(UserError, match="no element"):
            apply_edits(project, [{"op": "set_attr", "id": "nope", "name": "a", "value": "1"}])

    def test_empty_ops_noop(self, project):
        assert apply_edits(project, []) == {"applied": 0}


class TestEnsurePrepped:
    def test_stamps_missing_version_marker(self, tmp_path):
        svg = tmp_path / "raw.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" '
                       'viewBox="0 0 10 10"><path id="p" d="M1,1 L9,9" fill="#000"/></svg>')
        proj_path = tmp_path / "raw.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(proj_path))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()

        assert ensure_prepped(str(proj_path)) is True
        assert "inkstitch_svg_version" in svg.read_text()
        # sha stayed coherent through the stamp
        assert ProjectFile.load(str(proj_path)).svg_sha256 == sha256_of(svg)
        # second call is a no-op
        assert ensure_prepped(str(proj_path)) is False

    def test_already_prepped_untouched(self, project):
        proj = ProjectFile.load(project)
        sha_before = proj.svg_sha256
        assert ensure_prepped(project) is False
        assert ProjectFile.load(project).svg_sha256 == sha_before


@pytest.mark.skipif(discover() is None, reason="Ink/Stitch binary not installed")
class TestStitchPlan:
    def test_stitch_plan_svg_renders(self, project):
        svg = stitch_plan_svg(project)
        assert b"<svg" in svg or b"<?xml" in svg


class TestExtractStitchBlocks:
    """Parser for the binary's stitch-plan SVG → ordered stitch polylines.

    Structure mirrors real Ink/Stitch 3.2.2 output: the plan layer carries a
    side-by-side translate() (which must be DROPPED so stitches land on the
    design), and each path carries a scale() (which must be applied)."""

    PLAN = b"""<svg xmlns="http://www.w3.org/2000/svg"
        xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
        width="60mm" height="40mm" viewBox="0 0 60 40">
      <path id="elem_1" d="M0,0 L10,10" fill="#f17095"/>
      <g id="__inkstitch_stitch_plan__" inkscape:label="Stitch Plan"
         inkscape:groupmode="layer" transform="translate(60, 0)">
        <g id="__color_block_0__" inkscape:label="color block 1">
          <path style="stroke: #F07094; stroke-width: 0.4; fill: none;"
                d="M10 20 20 20 30 20" transform="scale(0.5, 0.5)"/>
          <path style="stroke: #F07094; stroke-width: 0.4; fill: none;"
                d="M40 20 50 20" transform="scale(0.5, 0.5)"/>
        </g>
        <g id="__color_block_1__" inkscape:label="color block 2">
          <path style="stroke: #D8D8D8; stroke-width: 0.4; fill: none;"
                d="M10 40 12 42 14 40" transform="scale(0.5, 0.5)"/>
        </g>
        <use id="use1" href="#inkstitch_ignore_layer" x="0" y="-10"/>
      </g>
    </svg>"""

    def test_blocks_and_colors(self):
        from cli_anything_inkstitch.artifact.design_model import extract_stitch_blocks
        result = extract_stitch_blocks(self.PLAN)
        assert len(result["blocks"]) == 2
        assert result["blocks"][0]["color"] == "#F07094"
        assert result["blocks"][1]["color"] == "#D8D8D8"
        assert result["total_stitches"] == 8  # 3 + 2 + 3 vertices

    def test_scale_applied_translate_dropped(self):
        from cli_anything_inkstitch.artifact.design_model import extract_stitch_blocks
        result = extract_stitch_blocks(self.PLAN)
        first = result["blocks"][0]["paths"][0]
        # scale(0.5) applied → 10,20 becomes 5,10; layer translate(60) NOT added
        assert first == [[5.0, 10.0], [10.0, 10.0], [15.0, 10.0]]

    def test_no_plan_layer_raises(self):
        from cli_anything_inkstitch.artifact.design_model import extract_stitch_blocks
        from cli_anything_inkstitch.errors import UserError
        with pytest.raises(UserError):
            extract_stitch_blocks(b'<svg xmlns="http://www.w3.org/2000/svg"/>')


class TestParamMeta:
    """Inspector controls are typed from the Ink/Stitch param schema."""

    def test_meta_types_for_known_params(self, tmp_path):
        from cli_anything_inkstitch.schema.cache import load_schema
        schema_params = (load_schema().get("stitch_types", {})
                         .get("auto_fill", {}).get("params", {}))
        svg = tmp_path / "design.svg"
        svg.write_text(SVG.replace(
            'inkstitch:fill_method="auto_fill" inkstitch:angle="0"',
            'inkstitch:fill_method="auto_fill" inkstitch:angle="0" '
            'inkstitch:auto_fill="True" inkstitch:row_spacing_mm="0.25"'))
        proj_path = tmp_path / "design.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(proj_path))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()
        design = read_design(str(proj_path))
        fill = next(o for o in design["objects"] if o["id"] == "elem_fill")
        assert "param_meta" in fill
        if "angle" in schema_params:      # schema present (extracted or bootstrap)
            assert fill["param_meta"]["angle"]["type"] == "float"
            assert fill["param_meta"]["auto_fill"]["type"] == "boolean"
            rs = fill["param_meta"]["row_spacing_mm"]
            assert rs["type"] == "float" and rs.get("min") is not None

    def test_dropdown_options_tokenized(self, monkeypatch):
        # canned schema — deterministic regardless of the machine's cache
        from cli_anything_inkstitch.artifact import design_model
        from cli_anything_inkstitch.schema import cache
        canned = {"stitch_types": {"contour_fill": {"params": {
            "contour_strategy": {"type": "dropdown",
                                 "options": ["Inner to Outer", "Single spiral"],
                                 "tooltip": "strategy"},
            "row_spacing_mm": {"type": "float", "min": 0.05, "max": 5.0},
        }}}}
        monkeypatch.setattr(cache, "load_schema", lambda *a, **k: canned)
        meta = design_model._param_meta_for(
            "contour_fill", ["contour_strategy", "row_spacing_mm"])
        # Ink/Stitch stores dropdown values as option indexes (get_int_param)
        assert meta["contour_strategy"]["options"] == [
            {"value": "0", "label": "Inner to Outer"},
            {"value": "1", "label": "Single spiral"},
        ]
        assert meta["row_spacing_mm"]["min"] == 0.05
        assert meta["contour_strategy"]["tooltip"] == "strategy"

    def test_unknown_params_get_no_meta(self, tmp_path):
        from cli_anything_inkstitch.artifact.design_model import _param_meta_for
        meta = _param_meta_for("auto_fill", ["not_a_real_param"])
        assert "not_a_real_param" not in meta
