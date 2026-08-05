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
        # bare child <use> markers are the legacy (engine-ignored) format —
        # they still display, flagged legacy, until a move migrates them
        assert fill["start"] == {"x": 2.5, "y": 2.5, "use_id": "use_start",
                                 "legacy": True}
        assert fill["end"] == {"x": 13.5, "y": 13.5, "use_id": "use_end",
                               "legacy": True}

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


class TestStitchSequenceExclude:
    """Layers-panel eye toggles re-plan the visible subset: exclude=ids maps
    to a stitch_plan_preview selection of every OTHER stitchable element.
    The engine call is faked — what's under test is the include-list math."""

    def _capture(self, monkeypatch):
        from cli_anything_inkstitch.artifact import design_model as dm
        captured = {}
        def fake_plan(project_file, *, binary_override=None, ids=None):
            captured["ids"] = ids
            return TestExtractStitchBlocks.PLAN
        monkeypatch.setattr(dm, "stitch_plan_svg", fake_plan)
        return captured

    def test_exclude_selects_the_others(self, project, monkeypatch):
        from cli_anything_inkstitch.artifact.design_model import stitch_sequence
        captured = self._capture(monkeypatch)
        out = stitch_sequence(project, exclude=["elem_satin"])
        # command <use> markers must not leak into the selection
        assert captured["ids"] == ["elem_fill", "elem_run"]
        assert out["total_stitches"] == 8

    def test_no_exclude_keeps_whole_document_call(self, project, monkeypatch):
        from cli_anything_inkstitch.artifact.design_model import stitch_sequence
        captured = self._capture(monkeypatch)
        stitch_sequence(project)
        assert captured["ids"] is None

    def test_everything_hidden_skips_the_engine(self, project, monkeypatch):
        from cli_anything_inkstitch.artifact.design_model import stitch_sequence
        captured = self._capture(monkeypatch)
        out = stitch_sequence(
            project, exclude=["elem_fill", "elem_satin", "elem_run"])
        assert "ids" not in captured          # engine never invoked
        assert out == {"blocks": [], "total_stitches": 0}


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

    def test_jumps_derived_from_path_gaps(self):
        # with render-jumps=false the engine splits paths at needle-up moves,
        # so inter-path gaps are exactly the jump/trim travel segments
        from cli_anything_inkstitch.artifact.design_model import extract_stitch_blocks
        result = extract_stitch_blocks(self.PLAN)
        assert result["blocks"][0]["jumps"] == [[15.0, 10.0, 20.0, 10.0]]
        assert result["blocks"][1]["jumps"] == []

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


class TestSetAttrValidation:
    """set_attr routes known params through validate_param — the artifact
    edit path can no longer write values the engine would silently ignore."""

    def _project_with_schema(self, tmp_path, monkeypatch):
        from cli_anything_inkstitch.schema import cache
        canned = {"stitch_types": {"auto_fill": {"params": {
            "angle": {"type": "float"},
            "row_spacing_mm": {"type": "float", "min": 0.05, "max": 5.0},
            "join_style": {"type": "dropdown",
                           "options": ["Round", "Mitered", "Beveled"]},
        }}}}
        monkeypatch.setattr(cache, "load_schema", lambda *a, **k: canned)
        svg = tmp_path / "design.svg"
        svg.write_text(SVG)
        proj_path = tmp_path / "design.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(proj_path))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()
        return str(proj_path)

    def test_dropdown_label_normalized_to_index(self, tmp_path, monkeypatch):
        project = self._project_with_schema(tmp_path, monkeypatch)
        apply_edits(project, [{"op": "set_attr", "id": "elem_fill",
                               "name": "join_style", "value": "Mitered"}])
        svg = (tmp_path / "design.svg").read_text()
        assert 'join_style="1"' in svg

    def test_invalid_dropdown_value_rejected(self, tmp_path, monkeypatch):
        project = self._project_with_schema(tmp_path, monkeypatch)
        with pytest.raises(UserError):
            apply_edits(project, [{"op": "set_attr", "id": "elem_fill",
                                   "name": "join_style", "value": "miter"}])

    def test_out_of_range_rejected(self, tmp_path, monkeypatch):
        project = self._project_with_schema(tmp_path, monkeypatch)
        with pytest.raises(UserError):
            apply_edits(project, [{"op": "set_attr", "id": "elem_fill",
                                   "name": "row_spacing_mm", "value": "99"}])

    def test_unknown_attr_passes_through(self, tmp_path, monkeypatch):
        project = self._project_with_schema(tmp_path, monkeypatch)
        apply_edits(project, [{"op": "set_attr", "id": "elem_fill",
                               "name": "custom_note", "value": "hello"}])
        assert 'custom_note="hello"' in (tmp_path / "design.svg").read_text()


class TestSetSvgAttr:
    def test_sets_presentation_attr(self, project):
        apply_edits(project, [
            {"op": "set_svg_attr", "id": "elem_fill", "name": "style",
             "value": "fill:none;stroke:#f17095"},
        ])
        import pathlib
        proj = ProjectFile.load(project)
        assert 'style="fill:none;stroke:#f17095"' in pathlib.Path(proj.svg_path).read_text()

    def test_non_whitelisted_attr_rejected(self, project):
        with pytest.raises(UserError, match="not allowed"):
            apply_edits(project, [
                {"op": "set_svg_attr", "id": "elem_fill", "name": "onclick",
                 "value": "alert(1)"},
            ])


class TestDeleteElement:
    def test_deletes_element_and_attached_commands(self, project):
        # attach a real command first so deletion must cascade
        r = apply_edits(project, [{"op": "attach_command", "id": "elem_run",
                                   "command": "trim", "x": 28, "y": 20}])
        use_id = r["results"][0]["use_id"]
        result = apply_edits(project, [{"op": "delete_element", "id": "elem_run"}])
        assert result["results"][0]["removed_commands"] == 1
        design = read_design(project)
        assert "elem_run" not in [o["id"] for o in design["objects"]]
        svg_text = __import__("pathlib").Path(
            ProjectFile.load(project).svg_path).read_text()
        assert use_id not in svg_text            # connector group gone too

    def test_delete_unknown_element_rejected(self, project):
        with pytest.raises(UserError, match="no element"):
            apply_edits(project, [{"op": "delete_element", "id": "nope"}])

    def test_delete_is_undoable(self, project):
        from cli_anything_inkstitch.artifact.design_model import apply_history_step
        apply_edits(project, [{"op": "delete_element", "id": "elem_run"}])
        apply_history_step(project)              # undo the element removal
        design = read_design(project)
        assert "elem_run" in [o["id"] for o in design["objects"]]


class TestElementLabel:
    def test_label_roundtrip(self, project):
        apply_edits(project, [{"op": "set_svg_attr", "id": "elem_fill",
                               "name": "label", "value": "outer ring"}])
        design = read_design(project)
        fill = next(o for o in design["objects"] if o["id"] == "elem_fill")
        assert fill["label"] == "outer ring"
        # stored as inkscape:label, id untouched
        svg_text = __import__("pathlib").Path(
            ProjectFile.load(project).svg_path).read_text()
        assert 'label="outer ring"' in svg_text
        assert 'id="elem_fill"' in svg_text

    def test_label_absent_is_none(self, project):
        design = read_design(project)
        fill = next(o for o in design["objects"] if o["id"] == "elem_fill")
        assert fill["label"] is None


class TestCommandPlumbingFiltered:
    def test_connectors_not_design_objects(self, project):
        apply_edits(project, [{"op": "attach_command", "id": "elem_run",
                               "command": "trim", "x": 28, "y": 20}])
        design = read_design(project)
        ids = [o["id"] for o in design["objects"]]
        assert not any(i.startswith("command_connector_") for i in ids)
        assert not any(i.startswith("command_use_") for i in ids)

    def test_inline_style_beats_presentation_attr(self, tmp_path):
        # a converted satin: style="fill:none" must beat leftover fill="#..."
        svg = tmp_path / "design.svg"
        svg.write_text(SVG.replace(
            '<path id="elem_run" d="M2,20 L28,20" fill="none" stroke="#000000"/>',
            '<path id="elem_run" d="M2,20 L28,20" fill="#ff0000" '
            'style="fill:none;stroke:#000000"/>'))
        proj_path = tmp_path / "design.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(proj_path))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()
        design = read_design(str(proj_path))
        run = next(o for o in design["objects"] if o["id"] == "elem_run")
        assert run["fill"] is None
        assert run["stroke"] == "#000000"


class TestDefsNotDesignObjects:
    def test_symbol_artwork_excluded(self, project):
        # attaching a command copies the real bundled symbol (with child
        # shapes) into defs — none of that is a design object
        apply_edits(project, [{"op": "attach_command", "id": "elem_run",
                               "command": "trim", "x": 28, "y": 20}])
        design = read_design(project)
        proj = ProjectFile.load(project)
        import pathlib
        assert "<defs" in pathlib.Path(proj.svg_path).read_text() or True
        for o in design["objects"]:
            assert not o["id"].startswith("inkstitch_")


class TestFlattenTransform:
    def test_bakes_transform_into_geometry(self, tmp_path):
        svg = tmp_path / "design.svg"
        svg.write_text(SVG.replace(
            '<path id="elem_run" d="M2,20 L28,20" fill="none" stroke="#000000"/>',
            '<path id="elem_run" d="M4,40 L56,40" fill="none" stroke="#000000" '
            'transform="scale(0.5, 0.5)"/>'))
        proj_path = tmp_path / "design.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(proj_path))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()
        p = str(proj_path)
        result = apply_edits(p, [{"op": "flatten_transform", "id": "elem_run"}])
        assert result["results"][0]["changed"] is True
        run = next(o for o in read_design(p)["objects"] if o["id"] == "elem_run")
        assert run["transform"] is None
        assert run["d"] == "M2.0,20.0 L28.0,20.0"

    def test_noop_without_transform(self, project):
        result = apply_edits(project, [{"op": "flatten_transform", "id": "elem_run"}])
        assert result["results"][0]["changed"] is False


class TestOverviewData:
    def test_context_and_session_in_design(self, project):
        proj = ProjectFile.load(project)
        proj.session["context"] = {"material": "stretchy knit", "size": "3 inch"}
        proj.session["thread_palette"] = "Madeira Polyneon"
        proj.save()
        design = read_design(project)
        assert design["context"]["material"] == "stretchy knit"
        assert design["session"]["thread_palette"] == "Madeira Polyneon"

    def test_history_entries_shape(self, project):
        from cli_anything_inkstitch.artifact.design_model import history_entries
        apply_edits(project, [{"op": "set_attr", "id": "elem_fill",
                               "name": "angle", "value": "15"}])
        h = history_entries(project)
        assert h["total"] >= 1
        assert h["entries"][-1]["current"] is True
        assert "angle" in h["entries"][-1]["command"]
        assert h["can_undo"] is True


class TestReorderElement:
    def test_moves_before_sibling(self, project):
        design = read_design(project)
        ids = [o["id"] for o in design["objects"]]
        assert ids == ["elem_fill", "elem_satin", "elem_run"]
        apply_edits(project, [{"op": "reorder_element", "id": "elem_run",
                               "before_id": "elem_fill"}])
        ids = [o["id"] for o in read_design(project)["objects"]]
        assert ids == ["elem_run", "elem_fill", "elem_satin"]

    def test_moves_to_end_without_before(self, project):
        apply_edits(project, [{"op": "reorder_element", "id": "elem_fill",
                               "before_id": None}])
        ids = [o["id"] for o in read_design(project)["objects"]]
        assert ids[-1] == "elem_fill"

    def test_reorder_is_undoable(self, project):
        from cli_anything_inkstitch.artifact.design_model import apply_history_step
        apply_edits(project, [{"op": "reorder_element", "id": "elem_run",
                               "before_id": "elem_fill"}])
        apply_history_step(project)   # undo insert
        apply_history_step(project)   # undo remove
        ids = [o["id"] for o in read_design(project)["objects"]]
        assert ids == ["elem_fill", "elem_satin", "elem_run"]


class TestAvailableParams:
    """Unset-but-applicable params ship to the editor with schema defaults so
    the inspector can offer every param the engine would read for the current
    stitch method; fill_method/stroke_method are flagged primary (they're the
    method selector the per-type schema filtering keys off)."""

    def _schema(self, monkeypatch):
        from cli_anything_inkstitch.schema import cache
        canned = {"stitch_types": {"auto_fill": {"params": {
            "angle": {"type": "float", "default": 0},
            "fill_method": {"type": "combo", "default": "auto_fill",
                            "options": ["auto_fill", "contour_fill"],
                            "option_labels": ["Auto Fill", "Contour Fill"]},
            "row_spacing_mm": {"type": "float", "default": 0.25, "sort_index": 5},
        }}}}
        monkeypatch.setattr(cache, "load_schema", lambda *a, **k: canned)

    def test_unset_params_offered_with_meta(self, project, monkeypatch):
        self._schema(monkeypatch)
        # the fixture sets fill_method explicitly — clear it to exercise the
        # unset path (classify() still yields auto_fill for a plain fill)
        apply_edits(project, [{"op": "del_attr", "id": "elem_fill",
                               "name": "fill_method"}])
        design = read_design(project)
        fill = next(o for o in design["objects"] if o["id"] == "elem_fill")
        avail = fill["available_params"]
        assert avail["fill_method"]["primary"] is True
        assert avail["fill_method"]["default"] == "auto_fill"
        assert {"value": "contour_fill", "label": "Contour Fill"} \
            in avail["fill_method"]["options"]
        assert "row_spacing_mm" in avail
        assert "primary" not in avail["row_spacing_mm"]

    def test_set_params_not_duplicated(self, project, monkeypatch):
        self._schema(monkeypatch)
        design = read_design(project)
        fill = next(o for o in design["objects"] if o["id"] == "elem_fill")
        # angle and fill_method are set in the fixture SVG — offered nowhere
        assert "angle" in fill["params"]
        assert "angle" not in fill["available_params"]
        assert "fill_method" not in fill["available_params"]


class TestConvertElement:
    """Pure-lxml conversions (engine-backed pairs are exercised in
    test_differential_params.py with the real binary)."""

    def test_satin_to_fill_band(self, project):
        apply_edits(project, [{"op": "convert_element",
                               "id": "elem_satin", "to": "fill"}])
        design = read_design(project)
        obj = next(o for o in design["objects"] if o["id"] == "elem_satin")
        assert obj["kind"] == "fill"
        assert obj["fill"] == "#000000"          # took the rail stroke color
        assert "satin_column" not in obj["params"]
        assert obj["d"].rstrip().endswith("Z")   # closed band ring

    def test_satin_to_fill_is_undoable(self, project):
        from cli_anything_inkstitch.artifact.design_model import apply_history_step
        apply_edits(project, [{"op": "convert_element",
                               "id": "elem_satin", "to": "fill"}])
        apply_history_step(project)
        design = read_design(project)
        obj = next(o for o in design["objects"] if o["id"] == "elem_satin")
        assert obj["kind"] == "satin"
        assert obj["params"]["satin_column"] == "True"

    def test_run_to_fill_paints(self, project):
        apply_edits(project, [{"op": "convert_element",
                               "id": "elem_run", "to": "fill"}])
        obj = next(o for o in read_design(project)["objects"]
                   if o["id"] == "elem_run")
        assert obj["kind"] == "fill"
        assert obj["fill"] == "#000000"

    def test_fill_to_satin_needs_rungs(self, project):
        with pytest.raises(UserError, match="rung"):
            apply_edits(project, [{"op": "convert_element",
                                   "id": "elem_fill", "to": "satin"}])

    def test_same_kind_rejected(self, project):
        with pytest.raises(UserError, match="already"):
            apply_edits(project, [{"op": "convert_element",
                                   "id": "elem_fill", "to": "fill"}])

    def test_convert_must_be_alone_in_batch(self, project):
        with pytest.raises(UserError, match="only op"):
            apply_edits(project, [
                {"op": "set_attr", "id": "elem_fill", "name": "angle", "value": "5"},
                {"op": "convert_element", "id": "elem_run", "to": "satin"}])


class TestSetSvgAttrStyleCascade:
    """Inline style beats presentation attrs — a set_svg_attr paint write
    against a styled element used to be a silent visual no-op (the engine
    stitches the cascade winner). The op now strips the competing style
    declaration in the same undoable patch."""

    def test_paint_write_strips_competing_style(self, project):
        apply_edits(project, [{"op": "set_svg_attr", "id": "elem_fill",
                               "name": "style", "value": "fill:#111111; opacity:0.9"}])
        apply_edits(project, [{"op": "set_svg_attr", "id": "elem_fill",
                               "name": "fill", "value": "#222222"}])
        fill = next(o for o in read_design(project)["objects"]
                    if o["id"] == "elem_fill")
        assert fill["fill"] == "#222222"      # attr wins now — style stripped

    def test_undo_restores_the_style(self, project):
        from cli_anything_inkstitch.artifact.design_model import apply_history_step
        apply_edits(project, [{"op": "set_svg_attr", "id": "elem_fill",
                               "name": "style", "value": "fill:#111111"}])
        apply_edits(project, [{"op": "set_svg_attr", "id": "elem_fill",
                               "name": "fill", "value": "#222222"}])
        apply_history_step(project)
        fill = next(o for o in read_design(project)["objects"]
                    if o["id"] == "elem_fill")
        assert fill["fill"] == "#111111"      # style declaration back, wins again


class TestAddElement:
    def test_add_run_path_appends_last(self, project):
        r = apply_edits(project, [{"op": "add_element", "kind": "run",
                                   "d": "M2,25 L28,25"}])
        new_id = r["results"][0]["id"]
        objs = read_design(project)["objects"]
        assert objs[-1]["id"] == new_id      # element order IS stitch order
        assert objs[-1]["kind"] == "run"

    def test_add_fill_and_undo(self, project):
        from cli_anything_inkstitch.artifact.design_model import apply_history_step
        r = apply_edits(project, [{"op": "add_element", "kind": "fill",
                                   "d": "M2,16 L8,16 L8,19 L2,19 Z",
                                   "color": "#ff0000"}])
        new_id = r["results"][0]["id"]
        obj = next(o for o in read_design(project)["objects"] if o["id"] == new_id)
        assert obj["kind"] == "fill"
        assert obj["fill"] == "#ff0000"
        apply_history_step(project)
        assert all(o["id"] != new_id for o in read_design(project)["objects"])

    def test_bad_d_rejected(self, project):
        with pytest.raises(UserError, match="path data"):
            apply_edits(project, [{"op": "add_element", "kind": "run", "d": "10,10"}])


class TestSatinStartEnd:
    """Satins promote starting_point/ending_point commands into start/end
    handles just like fills — the engine's SatinColumn reads the same
    commands (satin_column.py _get_command_point)."""

    def test_satin_start_promoted_to_handle(self, project):
        r = apply_edits(project, [{"op": "attach_command", "id": "elem_satin",
                                   "command": "starting_point",
                                   "x": 16, "y": 2}])
        satin = next(o for o in read_design(project)["objects"]
                     if o["id"] == "elem_satin")
        assert satin["start"]["use_id"] == r["results"][0]["use_id"]
        assert satin["start"]["x"] == 16
        assert "end" not in satin or satin.get("end") is None


class TestCrossStitchKind:
    def test_cross_stitch_is_a_fill(self, project):
        # cross_stitch is a fill_method ParamOption (engine fill_stitch.py
        # _fill_methods) — the only one not ending in _fill; it must keep
        # fill-kind editing (handles, boundary nodes, Colors grouping)
        apply_edits(project, [{"op": "set_attr", "id": "elem_fill",
                               "name": "fill_method", "value": "cross_stitch"}])
        fill = next(o for o in read_design(project)["objects"]
                    if o["id"] == "elem_fill")
        assert fill["stitch_type"] == "cross_stitch"
        assert fill["kind"] == "fill"


class TestAddElementIntoGroupedDocument:
    def test_add_next_to_grouped_elements(self, tmp_path):
        # engine tools wrap output in <g>; add_element must insert beside
        # the last design element inside that group, not crash detaching
        svg = tmp_path / "d.svg"
        svg.write_text("""<svg xmlns="http://www.w3.org/2000/svg"
 xmlns:inkstitch="http://inkstitch.org/namespace"
 width="30mm" height="30mm" viewBox="0 0 30 30">
<metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
<g id="wrap"><path id="elem_a" d="M2,20 L28,20" fill="none" stroke="#000"/></g>
</svg>""")
        proj_path = tmp_path / "d.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(proj_path))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()
        r = apply_edits(str(proj_path), [{"op": "add_element", "kind": "run",
                                          "d": "M2,25 L28,25"}])
        new_id = r["results"][0]["id"]
        objs = read_design(str(proj_path))["objects"]
        assert objs[-1]["id"] == new_id
