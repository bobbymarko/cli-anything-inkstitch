"""Tests for the stitchability gate (spec §8, v1 checks)."""

from __future__ import annotations

import pytest

from cli_anything_inkstitch.artifact.gate import (
    flatten_path,
    mm_per_unit,
    point_to_poly_dist,
    poly_self_intersects,
    run_gate,
    sample_poly,
    segments_intersect,
)
from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import sha256_of

# 1 user unit == 1 mm in these fixtures (width 60mm / viewBox 60)
SVG_TMPL = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     width="60mm" height="40mm" viewBox="0 0 60 40">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  {body}
</svg>
"""


def make_project(tmp_path, body):
    svg = tmp_path / "design.svg"
    svg.write_text(SVG_TMPL.format(body=body))
    proj_path = tmp_path / "design.inkstitch-cli.json"
    proj, _ = ProjectFile.load_or_create(str(proj_path))
    proj.svg_path = str(svg)
    proj.svg_sha256 = sha256_of(svg)
    proj.save()
    return str(proj_path)


GOOD_SATIN = ('<path id="s" fill="none" stroke="#000" inkstitch:satin_column="True" '
              'd="M10,12 C20,8 40,8 50,12 M10,18 C20,22 40,22 50,18 '
              'M10,11 L10,19 M30,8 L30,22 M50,11 L50,19"/>')


class TestGeometryPrimitives:
    def test_flatten_line(self):
        assert flatten_path("M0,0 L10,0") == [(0, 0), (10, 0)]

    def test_flatten_curve_endpoint(self):
        pts = flatten_path("M0,0 C0,10 10,10 10,0")
        assert pts[0] == (0, 0)
        assert pts[-1] == (10.0, 0.0)
        assert len(pts) > 5

    def test_relative_commands(self):
        assert flatten_path("m5,5 l5,0")[-1] == (10.0, 5.0)

    def test_illustrator_compact_decimals(self):
        # "-3.3.5" is two numbers (-3.3 and .5); "M38.4.7" is (38.4, .7)
        assert flatten_path("M38.4.7L-3.3.5") == [(38.4, 0.7), (-3.3, 0.5)]

    def test_segments_intersect(self):
        assert segments_intersect((0, 0), (10, 10), (0, 10), (10, 0))
        assert not segments_intersect((0, 0), (1, 1), (5, 5), (6, 6))

    def test_self_intersection(self):
        assert poly_self_intersects([(0, 0), (10, 10), (10, 0), (0, 10)])
        assert not poly_self_intersects([(0, 0), (10, 0), (10, 10)])

    def test_point_to_poly(self):
        assert point_to_poly_dist((5, 3), [(0, 0), (10, 0)]) == pytest.approx(3.0)

    def test_sample_poly_even(self):
        pts = sample_poly([(0, 0), (10, 0)], 5)
        assert [p[0] for p in pts] == pytest.approx([0, 2.5, 5, 7.5, 10])

    def test_mm_per_unit(self):
        assert mm_per_unit("60mm", "0 0 60 40") == pytest.approx(1.0)
        assert mm_per_unit("76.2mm", "0 0 116.8 100.2") == pytest.approx(0.6524, abs=1e-3)
        assert mm_per_unit(None, "0 0 100 100") == pytest.approx(25.4 / 96)


class TestSatinChecks:
    def test_good_satin_passes(self, tmp_path):
        result = run_gate(make_project(tmp_path, GOOD_SATIN))
        assert result["ok"] is True
        assert result["errors"] == []

    def test_desynced_rung_is_error(self, tmp_path):
        # rail A moved up; first rung left dangling (the editor-drag scenario)
        body = ('<path id="s" fill="none" stroke="#000" inkstitch:satin_column="True" '
                'd="M10,6.9 C20,0.9 40,6 50,12 M10,20 C20,26 40,26 50,20 '
                'M10,12 L10,20 M30,7.5 L30,24.5 M50,12 L50,20"/>')
        result = run_gate(make_project(tmp_path, body))
        assert result["ok"] is False
        assert any(f["check"] == "rung_pairing" for f in result["errors"])

    def test_no_rungs_mismatched_nodes_is_error(self, tmp_path):
        body = ('<path id="s" fill="none" stroke="#000" inkstitch:satin_column="True" '
                'd="M10,10 L20,10 L30,10 L50,10 M10,18 C20,22 40,22 50,18"/>')
        result = run_gate(make_project(tmp_path, body))
        assert any(f["check"] == "twist_risk" for f in result["errors"])

    def test_self_crossing_rail_is_error(self, tmp_path):
        body = ('<path id="s" fill="none" stroke="#000" inkstitch:satin_column="True" '
                'd="M10,10 L50,14 L10,14 L50,10 M10,20 L50,20 M10,10 L10,20 M50,10 L50,20"/>')
        result = run_gate(make_project(tmp_path, body))
        assert any(f["check"] == "rail_self_cross" for f in result["errors"])

    def test_too_narrow_is_error(self, tmp_path):
        body = ('<path id="s" fill="none" stroke="#000" inkstitch:satin_column="True" '
                'd="M10,10 L50,10 M10,10.2 L50,10.2 M10,10 L10,10.2 M50,10 L50,10.2"/>')
        result = run_gate(make_project(tmp_path, body))
        assert any(f["check"] == "width_min" for f in result["errors"])

    def test_too_wide_is_error(self, tmp_path):
        body = ('<path id="s" fill="none" stroke="#000" inkstitch:satin_column="True" '
                'd="M10,5 L50,5 M10,25 L50,25 M10,5 L10,25 M50,5 L50,25"/>')
        result = run_gate(make_project(tmp_path, body))
        assert any(f["check"] == "width_max" for f in result["errors"])

    def test_wide_but_stitchable_is_warning(self, tmp_path):
        body = ('<path id="s" fill="none" stroke="#000" inkstitch:satin_column="True" '
                'd="M10,10 L50,10 M10,19 L50,19 M10,10 L10,19 M50,10 L50,19"/>')
        result = run_gate(make_project(tmp_path, body))
        assert result["ok"] is True
        assert any(f["check"] == "width_wide" for f in result["warnings"])


class TestFillChecks:
    FILL = ('<path id="f" fill="#f17095" inkstitch:fill_method="auto_fill" '
            'd="M30,15 L55,15 L55,35 L30,35 Z"/>')

    def _with_start(self, tmp_path, x, y):
        from cli_anything_inkstitch.artifact.design_model import apply_edits
        project = make_project(tmp_path, self.FILL)
        apply_edits(project, [{"op": "attach_command", "id": "f",
                               "command": "starting_point", "x": x, "y": y}])
        return project

    def test_far_start_handle_is_warning(self, tmp_path):
        # the sparkle-squad scenario: marker at document top-left, shape center-right
        result = run_gate(self._with_start(tmp_path, 2, 2))
        assert result["ok"] is True  # warning, not error
        w = [f for f in result["warnings"] if f["check"] == "start_handle_far"]
        assert w and w[0]["distance_mm"] > 10

    def test_on_shape_handle_passes(self, tmp_path):
        result = run_gate(self._with_start(tmp_path, 31, 16))
        assert result["warnings"] == []

    def test_legacy_child_marker_is_inert_error(self, tmp_path):
        body = ('<path id="f" fill="#f17095" inkstitch:fill_method="auto_fill" '
                'd="M30,15 L55,15 L55,35 L30,35 Z">'
                '<use id="u1" xlink:href="#inkstitch_fill_start" x="31" y="16"/>'
                '</path>')
        result = run_gate(make_project(tmp_path, body))
        assert result["ok"] is False
        e = [f for f in result["errors"] if f["check"] == "inert_command"]
        assert e and e[0]["use_id"] == "u1"


class TestGateCLI:
    def test_artifact_gate_command(self, tmp_path):
        import json
        from click.testing import CliRunner
        from cli_anything_inkstitch.cli import root
        project = make_project(tmp_path, GOOD_SATIN)
        result = CliRunner().invoke(root, ["--json", "artifact", "gate", "--project", project])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output[result.output.index("{"):])
        assert payload["ok"] is True


class TestGatePayload:
    def test_counts_objects(self, tmp_path):
        result = run_gate(make_project(tmp_path, GOOD_SATIN))
        assert result["checked_objects"] == 1

    def test_multiple_findings_collected(self, tmp_path):
        body = (
            '<path id="s" fill="none" stroke="#000" inkstitch:satin_column="True" '
            'd="M10,6.9 C20,0.9 40,6 50,12 M10,20 C20,26 40,26 50,20 '
            'M10,12 L10,20 M30,7.5 L30,24.5 M50,12 L50,20"/>'
            '<path id="f" fill="#f17095" inkstitch:fill_method="auto_fill" '
            'd="M30,30 L55,30 L55,38 L30,38 Z">'
            '<use id="u1" xlink:href="#inkstitch_fill_start" x="2" y="2"/>'
            '</path>')
        result = run_gate(make_project(tmp_path, body))
        assert result["ok"] is False
        assert result["errors"] and result["warnings"]


class TestClosedRailDirection:
    """Opposite-direction closed rails make the engine's zigzag sweep across
    the shape while the stitch count still looks plausible — found the hard
    way converting a ring fill to satin."""

    def _ring(self, inner_reversed):
        # outer square loop CW; inner square loop CW or CCW
        outer = "M10,10 L30,10 L30,30 L10,30 L10,10"
        inner_cw = "M12,12 L28,12 L28,28 L12,28 L12,12"
        inner_ccw = "M12,12 L12,28 L28,28 L28,12 L12,12"
        rungs = "M9,11 L13,13 M31,11 L27,13"
        inner = inner_ccw if inner_reversed else inner_cw
        return (f'<path id="s" fill="none" stroke="#000" '
                f'inkstitch:satin_column="True" d="{outer} {inner} {rungs}"/>')

    def test_opposed_directions_error(self, tmp_path):
        result = run_gate(make_project(tmp_path, self._ring(inner_reversed=True)))
        assert any(f["check"] == "rail_direction" for f in result["errors"])

    def test_same_direction_passes(self, tmp_path):
        result = run_gate(make_project(tmp_path, self._ring(inner_reversed=False)))
        assert not any(f["check"] == "rail_direction" for f in result["errors"])


class TestTaperedColumnEnds:
    def test_end_taper_not_width_min_error(self, tmp_path):
        # rails meet at both ends (natural satin taper) but the column body
        # is a healthy 3mm — interior-only minimum must not flag the tips
        body = ('<path id="s" fill="none" stroke="#000" inkstitch:satin_column="True" '
                'd="M10,20 C20,17 40,17 50,20 M10,20 C20,23 40,23 50,20 '
                'M20,17.5 L20,22.5 M40,17.5 L40,22.5"/>')
        result = run_gate(make_project(tmp_path, body))
        assert not any(f["check"] == "width_min" for f in result["errors"])


class TestFlattenSmoothSegments:
    """S reflects the previous cubic control; Q/T degree-elevate — exact,
    not endpoint-flattened (Illustrator leans on s; flattening it misstated
    satin widths and faceted the satin→fill band)."""

    def test_s_shorthand_matches_explicit_cubic(self):
        # S's first control = reflection of the prior C's second control:
        # 2*(40,0) - (30,20) = (50,-20)
        explicit = "M0,0 C10,20 30,20 40,0 C50,-20 70,-20 80,0"
        shorthand = "M0,0 C10,20 30,20 40,0 S70,-20 80,0"
        a = flatten_path(explicit)
        b = flatten_path(shorthand)
        assert len(a) == len(b)
        assert all(abs(p[0] - q[0]) < 1e-9 and abs(p[1] - q[1]) < 1e-9
                   for p, q in zip(a, b))

    def test_quadratic_is_curved_not_chorded(self):
        pts = flatten_path("M0,0 Q10,20 20,0")
        assert max(p[1] for p in pts) > 5    # apex of the quad is y=10


@pytest.mark.skipif(discover() is None, reason="Ink/Stitch binary not installed")
class TestIgnoredFillMethods:
    """The gate measures whether a set fill_method actually reached the
    stitches: identical plan to auto_fill = silently ignored (engine older
    than the option, or requirements unmet). Version-independent probe: an
    unknown method value falls through the engine's elif chain in EVERY
    release (fill_stitch.py to_stitch_groups)."""

    SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     width="40mm" height="40mm" viewBox="0 0 40 40">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  <path id="e" d="M5,5 L35,5 L35,35 L5,35 Z" fill="#cc2244"
        inkstitch:fill_method="{m}"/>
</svg>"""

    def _project(self, tmp_path, method):
        svg = tmp_path / "d.svg"
        svg.write_text(self.SVG.format(m=method))
        proj_path = tmp_path / "d.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(proj_path))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()
        return str(proj_path)

    def test_unknown_method_flagged(self, tmp_path):
        # planted raw (the validator would reject it) — mimics an option
        # from engine source the installed binary predates
        project = self._project(tmp_path, "method_from_the_future")
        result = run_gate(project)
        checks = [f["check"] for f in result["errors"]]
        assert "ignored_fill_method" in checks

    def test_working_method_not_flagged(self, tmp_path):
        project = self._project(tmp_path, "contour_fill")
        result = run_gate(project)
        checks = [f["check"] for f in result["errors"]]
        assert "ignored_fill_method" not in checks


class TestClosedPolylineSelfIntersection:
    """Ring-satin rails are closed outlines (first point repeated at the
    end); the closing segment is adjacent to the first and must not read
    as a self-crossing (it did — first exercised by the celtic-patch
    letter 'o')."""

    def test_closed_square_is_clean(self):
        from cli_anything_inkstitch.artifact.gate import poly_self_intersects
        assert not poly_self_intersects(
            [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])

    def test_open_square_unchanged(self):
        from cli_anything_inkstitch.artifact.gate import poly_self_intersects
        assert not poly_self_intersects([(0, 0), (10, 0), (10, 10), (0, 10)])

    def test_real_crossing_still_detected_when_closed(self):
        from cli_anything_inkstitch.artifact.gate import poly_self_intersects
        bowtie = [(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)]
        assert poly_self_intersects(bowtie)
