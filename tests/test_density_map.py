"""Stitch-density analysis (task #29): penetrations/mm² from the stitch plan.

Pure-math contracts plus the CLI wired through a faked stitch_sequence —
the density code must measure needle penetrations, so its input is the
engine plan, never the vectors.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.embroidery.density import (
    PX_PER_MM,
    density_grid,
    hotspots,
    render_heatmap,
)
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import sha256_of


def _mm(x, y):
    return (x * PX_PER_MM, y * PX_PER_MM)


class TestGridMath:
    def test_uniform_low_density_has_no_hotspots(self):
        # 2 penetrations per mm² spread over a 10×10mm field
        pts = [_mm(x * 0.7, y * 0.7) for x in range(14) for y in range(14)]
        grid = density_grid(pts)
        assert grid.peak() <= 5.0
        assert hotspots(grid) == []

    def test_stacked_cluster_flags_error_region(self):
        # 30 penetrations piled into one 0.5mm cell = 120/mm²
        pts = [_mm(5.05 + (i % 3) * 0.05, 5.05 + (i // 3) * 0.05)
               for i in range(30)]
        pts += [_mm(x, 0.0) for x in range(10)]        # sparse background
        grid = density_grid(pts)
        spots = hotspots(grid)
        assert len(spots) == 1
        s = spots[0]
        assert s["severity"] == "error"
        assert s["peak_per_mm2"] >= 100
        # region localized around x=5mm y=5mm
        assert 4.0 <= s["x_mm"] <= 5.5 and 4.0 <= s["y_mm"] <= 5.5

    def test_isolated_warn_cell_is_noise_not_hotspot(self):
        # a single bar-tack-like cell just over warn, nothing adjacent
        pts = [_mm(3.1 + i * 0.01, 3.1) for i in range(2)]   # 8/mm² in one cell
        grid = density_grid(pts, cell_mm=0.5)
        assert hotspots(grid, warn=6.0, error=10.0) == []

    def test_heatmap_renders_png(self, tmp_path):
        pts = [_mm(x * 0.3, y * 0.3) for x in range(20) for y in range(20)]
        grid = density_grid(pts)
        out = tmp_path / "heat.png"
        render_heatmap(grid, str(out))
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     width="30mm" height="30mm" viewBox="0 0 113.386 113.386">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  <path id="r1" d="M10,20 L100,20" fill="none" stroke="#000"/>
</svg>"""


class TestCli:
    def test_reports_hotspots_and_writes_heatmap(self, tmp_path, monkeypatch):
        svg = tmp_path / "design.svg"
        svg.write_text(SVG)
        p = tmp_path / "design.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(p))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()

        dense = [[x * PX_PER_MM * 0.05 + 30, 30] for x in range(40)]
        fake = {"blocks": [{"color": "#111111", "paths": [dense], "jumps": []}],
                "total_stitches": len(dense)}
        from cli_anything_inkstitch.artifact import design_model as dm
        monkeypatch.setattr(dm, "stitch_sequence", lambda *a, **k: fake)
        result = CliRunner().invoke(
            root, ["--json", "tools", "density-map", "--project", str(p)],
            catch_exceptions=False)
        assert result.exit_code == 0, result.output
        out = json.loads(result.output[result.output.index("{"):])
        assert out["peak_per_mm2"] > 10
        assert out["hotspots"] and out["hotspots"][0]["severity"] == "error"
        assert (tmp_path / "density-map.png").exists()
