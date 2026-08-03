"""Engine-tool geometry guards (tasks #46/#47).

Measured engine behaviors these guards exist for (see svg/units.py):

* Engine tools write geometry in px space with a compensating transform
  (lib/svg/path.py get_correction_transform); fill_to_stroke carries it as
  a `transform` attribute — rendering-correct, but raw-`d` readers see the
  wrong scale (×3.78 in a 1-unit=1-mm doc, measured on v3.3.0).  Tool
  wrappers must bake the transform into the coordinates, and refuse output
  whose art actually rescaled (a dropped/mis-composed transform).
* The router treats every Stroke it is given as art
  (lib/stitches/auto_run.py autorun — no path_type filtering), so feeding
  a previously-routed document back in re-routes travel as art.  `tools
  auto-run` must drop stale `autorun-underpath` elements first.

Fake-binary tests pin the guard mechanics; the real-binary differentials at
the bottom prove both unit conventions against the installed engine.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.commands import tools as tools_mod
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import sha256_of
from cli_anything_inkstitch.svg.units import (
    document_scale,
    parse_doc_length_px,
    unit_scale_warning,
)

SVG_NS = "http://www.w3.org/2000/svg"

PX_DOC = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     width="30mm" height="30mm" viewBox="0 0 113.386 113.386">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  {body}
</svg>
"""

MM_DOC = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     width="30mm" height="30mm" viewBox="0 0 30 30">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  {body}
</svg>
"""

CROSS_PX = (
    '<path id="s1" d="M10,56 L100,56" fill="none" stroke="#000000"/>'
    '<path id="s2" d="M56,10 L56,100" fill="none" stroke="#000000"/>'
)
CROSS_MM = (
    '<path id="s1" d="M3,15 L27,15" fill="none" stroke="#000000"/>'
    '<path id="s2" d="M15,3 L15,27" fill="none" stroke="#000000"/>'
)


def make_project(tmp_path, svg_text):
    tmp_path.mkdir(parents=True, exist_ok=True)
    svg = tmp_path / "design.svg"
    svg.write_text(svg_text)
    proj_path = tmp_path / "design.inkstitch-cli.json"
    proj, _ = ProjectFile.load_or_create(str(proj_path))
    proj.svg_path = str(svg)
    proj.svg_sha256 = sha256_of(svg)
    proj.save()
    return str(proj_path), svg


class TestUnitScaleDetection:
    def test_px_doc_scale_is_identity(self):
        root_el = etree.fromstring(PX_DOC.format(body="").encode())
        sx, sy = document_scale(root_el)
        assert abs(sx - 1.0) < 0.001 and abs(sy - 1.0) < 0.001
        assert unit_scale_warning(root_el) is None

    def test_mm_doc_scale_is_pixels_per_mm(self):
        root_el = etree.fromstring(MM_DOC.format(body="").encode())
        sx, sy = document_scale(root_el)
        assert abs(sx - 96 / 25.4) < 0.001 and abs(sy - 96 / 25.4) < 0.001
        warning = unit_scale_warning(root_el)
        assert warning and "px user units" in warning

    def test_no_viewbox_is_safe(self):
        root_el = etree.fromstring(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="30" height="30"/>')
        assert document_scale(root_el) is None
        assert unit_scale_warning(root_el) is None

    def test_length_parsing(self):
        assert parse_doc_length_px("96px") == 96
        assert parse_doc_length_px("96") == 96
        assert abs(parse_doc_length_px("25.4mm") - 96) < 1e-9
        assert parse_doc_length_px("1in") == 96
        assert parse_doc_length_px("100%") is None
        assert parse_doc_length_px(None) is None


class TestBakeTransforms:
    def test_path_transform_folded_into_coordinates(self):
        from cli_anything_inkstitch.svg.units import bake_transforms
        tree = etree.ElementTree(etree.fromstring(PX_DOC.format(
            body='<path id="p" d="M37.795,37.795 L75.59,37.795" '
                 'transform="scale(0.264583, 0.264583)" '
                 'fill="none" stroke="#000"/>').encode()))
        assert bake_transforms(tree) == 1
        p = tree.getroot().find(f".//{{{SVG_NS}}}path")
        assert p.get("transform") is None
        from cli_anything_inkstitch.artifact.gate import flatten_path
        pts = flatten_path(p.get("d"))
        assert abs(pts[0][0] - 10.0) < 0.01 and abs(pts[-1][0] - 20.0) < 0.01

    def test_group_transform_distributed_then_baked(self):
        from cli_anything_inkstitch.svg.units import bake_transforms
        tree = etree.ElementTree(etree.fromstring(PX_DOC.format(
            body='<g transform="translate(10, 0)">'
                 '<path id="p" d="M0,0 L10,0" transform="scale(2)" '
                 'fill="none" stroke="#000"/></g>').encode()))
        assert bake_transforms(tree) == 1
        g = tree.getroot().find(f".//{{{SVG_NS}}}g")
        assert g.get("transform") is None
        p = tree.getroot().find(f".//{{{SVG_NS}}}path")
        assert p.get("transform") is None
        from cli_anything_inkstitch.artifact.gate import flatten_path
        pts = flatten_path(p.get("d"))
        # translate(10) ∘ scale(2): (0,0)→(10,0), (10,0)→(30,0)
        assert abs(pts[0][0] - 10.0) < 0.01 and abs(pts[-1][0] - 30.0) < 0.01

    def test_no_transforms_is_a_no_op(self):
        from cli_anything_inkstitch.svg.units import bake_transforms
        tree = etree.ElementTree(etree.fromstring(
            PX_DOC.format(body=CROSS_PX).encode()))
        before = etree.tostring(tree.getroot())
        assert bake_transforms(tree) == 0
        assert etree.tostring(tree.getroot()) == before


def _fake_extension(transform):
    """A run_extension stand-in that rewrites path d attrs via `transform`."""
    from cli_anything_inkstitch.svg.geometry import transform_d

    def fake(binary, ext, svg_path, args=None, ids=None,
             capture_stdout=False, **kw):
        tree = etree.parse(svg_path)
        for p in tree.getroot().iter(f"{{{SVG_NS}}}path"):
            p.set("d", transform_d(p.get("d"), transform))
        return etree.tostring(tree.getroot())
    return fake


class TestScaleDriftGuard:
    def _run(self, tmp_path, monkeypatch, fake, doc=PX_DOC, body=CROSS_PX,
             extra_args=()):
        proj_path, svg = make_project(tmp_path, doc.format(body=body))
        monkeypatch.setattr(tools_mod, "require", lambda *a, **k: "/fake")
        monkeypatch.setattr(tools_mod, "run_extension", fake)
        r = CliRunner().invoke(root, ["--json", "tools", "auto-run",
                                      "--project", proj_path,
                                      "--ids", "s1,s2", *extra_args])
        return r, proj_path, svg

    def test_rescaled_output_is_refused(self, tmp_path, monkeypatch):
        # simulates the measured mm-doc auto_run failure: output ÷3.78
        k = 25.4 / 96
        r, proj_path, svg = self._run(
            tmp_path, monkeypatch, _fake_extension((k, 0, 0, k, 0, 0)))
        assert r.exit_code != 0
        assert "px user units" in r.output
        # document untouched, nothing recorded
        assert 'M10,56' in svg.read_text().replace(" ", ",").replace("M 10", "M10") or \
               "M10,56" in svg.read_text()
        proj = ProjectFile.load(proj_path)
        assert not any(e["command"].startswith("tools auto_run")
                       for e in proj.history["entries"])

    def test_upscaled_output_is_refused(self, tmp_path, monkeypatch):
        # simulates the measured mm-doc fill_to_stroke failure: output ×3.78
        k = 96 / 25.4
        r, _proj, _svg = self._run(
            tmp_path, monkeypatch, _fake_extension((k, 0, 0, k, 0, 0)))
        assert r.exit_code != 0
        assert "px user units" in r.output

    def test_scale_preserving_output_is_accepted(self, tmp_path, monkeypatch):
        r, proj_path, _svg = self._run(
            tmp_path, monkeypatch, _fake_extension((1, 0, 0, 1, 0.5, 0.5)))
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["changed"] is True
        proj = ProjectFile.load(proj_path)
        assert any(e["command"].startswith("tools auto_run")
                   for e in proj.history["entries"])


ROUTED_BODY = (
    '<path id="top1" d="M10,56 L100,56" fill="none" stroke="#000000" '
    'inkstitch:path_type="autorun-top"/>'
    '<path id="under1" d="M100,56 L56,10" fill="none" stroke="#000000" '
    'stroke-dasharray="3 0.5" inkstitch:path_type="autorun-underpath"/>'
    '<path id="top2" d="M56,10 L56,100" fill="none" stroke="#000000" '
    'inkstitch:path_type="autorun-top"/>'
)


class TestUnderpathStrip:
    def _fake_recording(self, seen):
        def fake(binary, ext, svg_path, args=None, ids=None,
                 capture_stdout=False, **kw):
            seen["ids"] = list(ids or [])
            seen["disk_doc"] = (tmp := etree.parse(svg_path)) and etree.tostring(
                tmp.getroot()).decode()
            return etree.tostring(etree.parse(svg_path).getroot())
        return fake

    def test_stale_underpaths_dropped_before_routing(self, tmp_path, monkeypatch):
        proj_path, _svg = make_project(tmp_path, PX_DOC.format(body=ROUTED_BODY))
        seen = {}
        monkeypatch.setattr(tools_mod, "require", lambda *a, **k: "/fake")
        monkeypatch.setattr(tools_mod, "run_extension", self._fake_recording(seen))
        r = CliRunner().invoke(root, ["--json", "tools", "auto-run",
                                      "--project", proj_path,
                                      "--ids", "top1,under1,top2"])
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload["removed_stale_underpaths"] == ["under1"]
        # the engine was invoked without the underpath — in the selection
        # AND in the on-disk document it reads
        assert seen["ids"] == ["top1", "top2"]
        assert "under1" not in seen["disk_doc"]

    def test_keep_underpaths_flag_disables_strip(self, tmp_path, monkeypatch):
        proj_path, _svg = make_project(tmp_path, PX_DOC.format(body=ROUTED_BODY))
        seen = {}
        monkeypatch.setattr(tools_mod, "require", lambda *a, **k: "/fake")
        monkeypatch.setattr(tools_mod, "run_extension", self._fake_recording(seen))
        r = CliRunner().invoke(root, ["--json", "tools", "auto-run",
                                      "--project", proj_path,
                                      "--ids", "top1,under1,top2",
                                      "--keep-underpaths"])
        assert r.exit_code == 0, r.output
        assert "removed_stale_underpaths" not in json.loads(r.output)
        assert seen["ids"] == ["top1", "under1", "top2"]


# ---- real-binary differential proof -----------------------------------------

from cli_anything_inkstitch.binary import discover  # noqa: E402

needs_binary = pytest.mark.skipif(discover() is None,
                                  reason="Ink/Stitch binary not installed")


FILL_PX = ('<path id="f1" d="M20,20 L90,20 L90,90 L20,90 Z" '
           'fill="#1a1a1a"/>')
FILL_MM = ('<path id="f1" d="M5,5 L24,5 L24,24 L5,24 Z" '
           'fill="#1a1a1a"/>')


@needs_binary
class TestUnitConventionAgainstRealEngine:
    def _route(self, proj_path, ids):
        return CliRunner().invoke(root, ["--json", "tools", "auto-run",
                                         "--project", proj_path,
                                         "--ids", ",".join(ids), "--trim"])

    def _tops(self, svg_path):
        tree = etree.parse(str(svg_path))
        key = "{http://inkstitch.org/namespace}path_type"
        return [e.get("id") for e in tree.getroot().iter()
                if e.get(key) == "autorun-top"]

    def _raw_d_span(self, svg_path):
        """Width of the art as a transform-IGNORING reader sees it."""
        from cli_anything_inkstitch.artifact.gate import flatten_path
        tree = etree.parse(str(svg_path))
        xs = [x for p in tree.getroot().iter(f"{{{SVG_NS}}}path")
              for x, _y in flatten_path(p.get("d") or "")]
        return max(xs) - min(xs) if xs else 0.0

    def test_fill_to_stroke_baked_in_both_unit_conventions(self, tmp_path):
        """The differential: identical physical art through fill_to_stroke in
        both unit conventions.  The engine emits px-space coordinates with a
        compensating transform (svg/units.py has the measurements); after
        conversion the raw path data must equal the effective geometry — no
        transform attributes left, spans matching the input art — in BOTH
        documents.  Without bake_transforms the mm document's raw spans come
        out ×3.78 and this fails."""
        from cli_anything_inkstitch.artifact.design_model import apply_edits

        px_proj, px_svg = make_project(
            tmp_path / "px", PX_DOC.format(body=FILL_PX))
        apply_edits(px_proj, [{"op": "convert_element", "id": "f1",
                               "to": "run"}])
        assert 'transform=' not in px_svg.read_text()
        # input art was 70 px wide; centerlines must stay in that ballpark
        assert 40 < self._raw_d_span(px_svg) < 90

        mm_proj, mm_svg = make_project(
            tmp_path / "mm", MM_DOC.format(body=FILL_MM))
        apply_edits(mm_proj, [{"op": "convert_element", "id": "f1",
                               "to": "run"}])
        assert 'transform=' not in mm_svg.read_text()
        # input art was 19 mm-units wide; un-baked px-space output would
        # measure ~72 here (the rose-bag failure mode)
        assert 10 < self._raw_d_span(mm_svg) < 25

    def test_routing_twice_does_not_grow_the_design(self, tmp_path):
        proj_path, svg = make_project(
            tmp_path / "twice", PX_DOC.format(body=CROSS_PX))
        r = self._route(proj_path, ["s1", "s2"])
        assert r.exit_code == 0, r.output
        tops_first = self._tops(svg)
        assert tops_first, "routing produced no autorun tops"

        tree = etree.parse(str(svg))
        all_ids = [e.get("id") for e in tree.getroot().iter()
                   if e.get("id") and e.tag.endswith("path")]
        r = self._route(proj_path, all_ids)
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload.get("removed_stale_underpaths") is None or \
            payload["removed_stale_underpaths"]
        tops_second = self._tops(svg)
        # the 320-element regression: re-routing must not multiply the art
        assert len(tops_second) <= len(tops_first) + 1
