"""shapes-to-satin --fabric: satin spacing, pull compensation and underlay ladder per fabric.

Why: one underlay ladder for every fabric over-builds stable woven fabric (a twill jacket back
needs an edge walk where fleece needs zigzag) and under-builds pile. Presets live in
commands/tools.py FABRICS; "standard" is the ladder text-to-satin has always used.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.commands.tools import FABRICS, _lettering_satin_params

try:
    import cli_anything_inkstitch.planner.art_columns  # noqa: F401
    import scipy.spatial  # noqa: F401
    HAVE_PLANNER = True
except ImportError:
    HAVE_PLANNER = False


def _underlay(p):
    return sorted(k for k in ("center_walk_underlay", "contour_underlay", "zigzag_underlay") if p.get(k) == "true")


class TestLadder:
    def test_standard_is_the_old_ladder(self):
        assert _underlay(_lettering_satin_params([1.0])) == ["center_walk_underlay"]
        assert _underlay(_lettering_satin_params([2.0])) == ["contour_underlay"]
        assert _underlay(_lettering_satin_params([4.5])) == ["contour_underlay", "zigzag_underlay"]
        assert "zigzag_spacing_mm" not in _lettering_satin_params([2.0])

    def test_twill_by_width(self):
        tw = lambda w: _lettering_satin_params([w], None, "twill")
        assert _underlay(tw(1.8)) == ["center_walk_underlay"]               # < 2 mm: centre walk
        assert _underlay(tw(4.5)) == ["contour_underlay"]                   # 2-5 mm: edge walk only
        assert _underlay(tw(6.0)) == ["contour_underlay", "zigzag_underlay"]
        p = tw(3.0)
        assert p["zigzag_spacing_mm"] == "0.4" and p["pull_compensation_mm"] == "0.2"

    def test_fleece_builds_more_and_explicit_pull_wins(self):
        assert _underlay(_lettering_satin_params([3.5], None, "fleece")) == ["contour_underlay", "zigzag_underlay"]
        assert _lettering_satin_params([3.5], 0.5, "fleece")["pull_compensation_mm"] == "0.5"

    def test_split_satin_above_seven_mm(self):
        assert _lettering_satin_params([8.0], None, "twill")["max_stitch_length_mm"] == "7"

    def test_every_preset_names_the_same_keys(self):
        assert all(set(v) == set(FABRICS["standard"]) for v in FABRICS.values())


@pytest.mark.skipif(not HAVE_PLANNER or discover() is None, reason="planner or inkstitch binary missing")
class TestEngineReadsThePreset:
    def _stitches(self, tmp_path, fabric):
        from cli_anything_inkstitch.cli import root
        import pyembroidery as pe
        k = 96 / 25.4
        d = f"M {5 * k},{5 * k} L {45 * k},{5 * k} L {45 * k},{8.5 * k} L {5 * k},{8.5 * k} Z"   # 40 x 3.5 mm
        svg = tmp_path / f"{fabric}.svg"
        svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="14mm" '
                       f'viewBox="0 0 {50 * k:.4f} {14 * k:.4f}"><path id="bar" d="{d}" style="fill:#2e7d32"/></svg>')
        proj = tmp_path / f"{fabric}.json"
        assert CliRunner().invoke(root, ["document", "open", "--project", str(proj), "--svg", str(svg)]).exit_code == 0
        r = CliRunner().invoke(root, ["--json", "tools", "shapes-to-satin", "--project", str(proj), "--fabric", fabric])
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["shapes"][0]["stitch"] == "satin"
        out = tmp_path / f"{fabric}.dst"
        assert CliRunner().invoke(root, ["export", "file", "--project", str(proj), "--format", "dst",
                                         "--out", str(out)]).exit_code == 0
        return sum(1 for _, _, c in pe.read(str(out)).stitches if c & pe.COMMAND_MASK == pe.STITCH)

    def test_fleece_sews_more_than_twill(self, tmp_path):
        # a 3.5 mm column: twill = edge walk, fleece = edge walk + zigzag at tighter spacing
        assert self._stitches(tmp_path, "fleece") > self._stitches(tmp_path, "twill") * 1.1
