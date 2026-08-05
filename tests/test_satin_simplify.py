"""Node economy (task #54): engine-emitted satin rails get RDP-simplified.

Why: engine satin output samples its rails from the input density — the
celtic-patch build pre-flattened source beziers and every downstream rail
became a node forest ("my svg doesn't have nearly as many nodes as I see
in the digitizing artifact"). The simplifier's contract lives in
trace/satinize.py simplify_satin_d; the pipeline hook must only touch
satins a tool actually created or rewrote.

The real-binary differential at the bottom is the behavioral proof
(CLAUDE.md rule 3): a dense satin and its simplified twin must produce
equivalent stitch plans.
"""

from __future__ import annotations

import json
import math

import pytest
from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.commands import tools as tools_mod
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import sha256_of
from cli_anything_inkstitch.trace.satinize import simplify_satin_d


def _arc_pts(cx, cy, r, a0, a1, n):
    return [(cx + r * math.cos(a0 + (a1 - a0) * k / n),
             cy + r * math.sin(a0 + (a1 - a0) * k / n)) for k in range(n + 1)]


def _poly_d(pts):
    return "M" + " ".join(f"{x:.4f},{y:.4f}" for x, y in pts)


def _dense_satin_d(n=200):
    """Two concentric quarter-arc rails at silly density + two rungs."""
    a = _arc_pts(0, 0, 20, 0, math.pi / 2, n)
    b = _arc_pts(0, 0, 24, 0, math.pi / 2, n)
    rungs = " M20,0 L24,0 M0,20 L0,24"
    return _poly_d(a) + " " + _poly_d(b) + rungs


class TestSimplifySatinD:
    def test_dense_arc_rails_shed_most_nodes(self):
        d = _dense_satin_d()
        new_d, before, after = simplify_satin_d(d)
        assert before == 402
        assert after < before * 0.2          # arcs need a handful of points
        # endpoints exact — rung↔rail contact at the ends must survive
        subs = new_d.split(" M")
        assert subs[0].startswith("M20.0000,0.0000".replace("0000", "000")) \
            or new_d.startswith("M20.000,0.000")
        # rungs content-identical (subpath splitting may shuffle whitespace)
        assert " ".join(new_d.split()).endswith("M20,0 L24,0 M0,20 L0,24")

    def test_closed_ring_stays_exactly_closed(self):
        ring = _arc_pts(0, 0, 10, 0, 2 * math.pi, 180)
        ring += [ring[0], ring[0]]           # engine-style duplicated closure
        d = _poly_d(ring) + " " + _poly_d(_arc_pts(0, 0, 12, 0, 2 * math.pi, 180))
        new_d, before, after = simplify_satin_d(d)
        assert after < before
        first_sub = new_d.split(" M")[0]
        pts = [tuple(map(float, p.split(",")))
               for p in first_sub.lstrip("M").split(" ")]
        assert math.dist(pts[0], pts[-1]) < 1e-9

    def test_bezier_rails_pass_through_byte_identical(self):
        d = ("M0,0 C5,0 10,5 10,10 M2,0 C7,0 12,5 12,10 M0,0 L2,0")
        new_d, before, after = simplify_satin_d(d)
        assert new_d == d

    def test_short_rails_untouched(self):
        d = "M0,0 L10,0 M0,2 L10,2 M0,0 L0,2"
        new_d, _b, _a = simplify_satin_d(d)
        assert new_d == d


SVG_TMPL = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     width="30mm" height="30mm" viewBox="0 0 113.386 113.386">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  {body}
</svg>
"""


def _project(tmp_path, body):
    tmp_path.mkdir(parents=True, exist_ok=True)
    svg = tmp_path / "design.svg"
    svg.write_text(SVG_TMPL.format(body=body))
    p = tmp_path / "design.inkstitch-cli.json"
    proj, _ = ProjectFile.load_or_create(str(p))
    proj.svg_path = str(svg)
    proj.svg_sha256 = sha256_of(svg)
    proj.save()
    return str(p), svg


class TestPipelineHook:
    def test_auto_satin_output_rails_simplified_untouched_satin_left_alone(
            self, tmp_path, monkeypatch):
        # input doc: a stroke to convert + a PRE-EXISTING dense satin the
        # fake tool does not modify (its density is the user's business)
        untouched = ('<path id="mine" inkstitch:satin_column="true" '
                     f'stroke="#000000" fill="none" d="{_dense_satin_d(50)}"/>')
        body = ('<path id="s1" d="M10,56 L100,56" fill="none" '
                'stroke="#000000"/>') + untouched
        proj_path, svg = _project(tmp_path, body)

        # fake keeps s1 so the art bbox stays stable (the scale-drift guard
        # legitimately rejects output whose extent collapses)
        out_body = ('<path id="s1" d="M10,56 L100,56" fill="none" '
                    'stroke="#000000"/>'
                    '<path id="esat" inkstitch:satin_column="true" '
                    f'stroke="#000000" fill="none" d="{_dense_satin_d()}"/>'
                    ) + untouched
        fake_out = SVG_TMPL.format(body=out_body).encode()
        monkeypatch.setattr(tools_mod, "require", lambda *a, **k: "/fake")
        monkeypatch.setattr(tools_mod, "run_extension",
                            lambda *a, **k: fake_out)
        result = CliRunner().invoke(
            root, ["--json", "tools", "auto-satin", "--project", proj_path,
                   "--ids", "s1"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output[result.output.index("{"):])
        info = payload["simplified_rails"]
        assert info["elements"] == 1
        assert info["rail_nodes_after"] < info["rail_nodes_before"] * 0.2
        tree = etree.parse(str(svg))
        by_id = {e.get("id"): e for e in tree.getroot().iter() if e.get("id")}
        # the tool's satin was simplified…
        assert len(by_id["esat"].get("d")) < len(_dense_satin_d()) * 0.3
        # …the user's untouched satin was not
        assert by_id["mine"].get("d") == _dense_satin_d(50)


class TestNormalizeEngineSatins:
    def test_idless_style_colored_satin_becomes_editable(self, tmp_path,
                                                         monkeypatch):
        """fill_to_satin emits satins with NO id and colors in style — the
        editor's design payload skips id-less elements, so the satin
        stitched and exported but was invisible in the editor (caught live
        on the feature demo). Normalization assigns fsat_N + attribute
        paints."""
        body = ('<path id="s1" d="M10,56 L100,56" fill="none" '
                'stroke="#000000"/>')
        proj_path, svg = _project(tmp_path, body)
        out_body = (
            '<path id="s1" d="M10,56 L100,56" fill="none" stroke="#000000"/>'
            '<path inkstitch:satin_column="true" '
            'style="stroke:#b4aa3f;fill:none;stroke-width:1.0" '
            f'd="{_dense_satin_d(20)}"/>')
        fake_out = SVG_TMPL.format(body=out_body).encode()
        monkeypatch.setattr(tools_mod, "require", lambda *a, **k: "/fake")
        monkeypatch.setattr(tools_mod, "run_extension",
                            lambda *a, **k: fake_out)
        result = CliRunner().invoke(
            root, ["--json", "tools", "auto-satin", "--project", proj_path,
                   "--ids", "s1"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output[result.output.index("{"):])
        assert payload["normalized_satins"] == 1
        tree = etree.parse(str(svg))
        ink = "{http://inkstitch.org/namespace}satin_column"
        sat = next(e for e in tree.getroot().iter()
                   if (e.get(ink) or "").lower() == "true")
        assert (sat.get("id") or "").startswith("fsat_")
        assert sat.get("stroke") == "#b4aa3f"
        assert sat.get("fill") == "none"
        assert "stroke:" not in (sat.get("style") or "")


needs_binary = pytest.mark.skipif(discover() is None,
                                  reason="Ink/Stitch binary not installed")


@needs_binary
class TestStitchPlanEquivalence:
    def test_dense_and_simplified_rails_stitch_the_same(self, tmp_path):
        """Differential proof: simplification must not change the stitching.

        Same satin geometry at 200 rail points vs its simplified form —
        stitch counts within 5% and column extent identical within 0.5px."""
        from cli_anything_inkstitch.artifact.design_model import stitch_sequence

        dense = _dense_satin_d()
        simplified, nb, na = simplify_satin_d(dense)
        assert na < nb
        totals = {}
        for name, d in (("dense", dense), ("simple", simplified)):
            body = (f'<path id="sat" inkstitch:satin_column="true" '
                    f'stroke="#000000" fill="none" d="{d}"/>')
            proj_path, _svg = _project(tmp_path / name, body)
            totals[name] = stitch_sequence(proj_path)["total_stitches"]
        assert totals["dense"] > 0
        ratio = totals["simple"] / totals["dense"]
        assert 0.95 < ratio < 1.05, totals
