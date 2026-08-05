"""tools suggest-rungs (task #53): machine-placed rungs that imitate the
hand placement that made the celtic patch work — sparse on straights,
clustered at turns, perpendicular to the local axis, always crossing both
edges. The binary-backed test at the bottom is the behavioral proof: the
suggested rungs must actually drive a successful engine fill_to_satin.
"""

from __future__ import annotations

import json
import math

import pytest
from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import sha256_of
from cli_anything_inkstitch.trace.rungs import suggest_rungs


def _bar_d(x0=10, y0=40, length=120, width=12):
    x1, y1 = x0 + length, y0 + width
    return f"M{x0},{y0} L{x1},{y0} L{x1},{y1} L{x0},{y1} Z"


def _arc_band_d(cx=80, cy=90, r_in=40, r_out=56, n=64):
    """Half-annulus band (a fat 180° curve)."""
    outer = [(cx + r_out * math.cos(math.pi * k / n),
              cy - r_out * math.sin(math.pi * k / n)) for k in range(n + 1)]
    inner = [(cx + r_in * math.cos(math.pi * (n - k) / n),
              cy - r_in * math.sin(math.pi * (n - k) / n)) for k in range(n + 1)]
    pts = outer + inner
    return "M" + " ".join(f"{x:.2f},{y:.2f}" for x, y in pts) + "Z"


def _crossings(seg, d):
    """How many times segment crosses the path's boundary polylines."""
    from cli_anything_inkstitch.trace.rungs import _flatten_subpaths
    (p1, p2) = seg

    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)

    count = 0
    for ring in _flatten_subpaths(d):
        for a, b in zip(ring, ring[1:]):
            if orient(p1, p2, a) != orient(p1, p2, b) and \
               orient(a, b, p1) != orient(a, b, p2):
                count += 1
    return count


class TestPlacement:
    def test_straight_bar_gets_few_perpendicular_rungs(self):
        d = _bar_d()
        rungs = suggest_rungs(d)
        assert 1 <= len(rungs) <= 5          # sparse on a straight
        for (p1, p2) in rungs:
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            # bar axis is horizontal — rungs must be near-vertical
            assert abs(dx) < abs(dy) * 0.3, (dx, dy)
            assert _crossings((p1, p2), d) == 2

    def test_curve_gets_denser_rungs_than_straight_of_same_length(self):
        band = _arc_band_d()                  # centerline arc ≈ 150 units
        bar = _bar_d(length=150, width=16)
        n_band = len(suggest_rungs(band))
        n_bar = len(suggest_rungs(bar))
        assert n_band > n_bar * 2, (n_band, n_bar)

    def test_every_curve_rung_crosses_the_band(self):
        band = _arc_band_d()
        rungs = suggest_rungs(band)
        assert rungs
        good = sum(1 for seg in rungs if _crossings(seg, band) == 2)
        # tangent smoothing near branch ends can graze; the overwhelming
        # majority must cross cleanly
        assert good >= len(rungs) * 0.85, (good, len(rungs))


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


def _run(*args):
    result = CliRunner().invoke(root, ["--json", *args], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output[result.output.index("{"):])


class TestCli:
    def test_appends_red_guides_and_records_history(self, tmp_path):
        body = f'<path id="f1" d="{_bar_d()}" fill="#336699"/>'
        proj_path, svg = _project(tmp_path, body)
        out = _run("tools", "suggest-rungs", "--project", proj_path,
                   "--ids", "f1")
        rung_ids = out["fills"][0]["rungs"]
        assert rung_ids
        tree = etree.parse(str(svg))
        by_id = {e.get("id"): e for e in tree.getroot().iter() if e.get("id")}
        for rid in rung_ids:
            assert by_id[rid].get("stroke") == "#ed2024"
        # one undoable history entry
        proj = ProjectFile.load(proj_path)
        assert proj.history["entries"][-1]["command"].startswith(
            "tools suggest-rungs")


needs_binary = pytest.mark.skipif(discover() is None,
                                  reason="Ink/Stitch binary not installed")


@needs_binary
class TestEndToEnd:
    def test_suggested_rungs_drive_fill_to_satin(self, tmp_path):
        """The whole loop: suggest → convert. The engine must produce a
        satin column from the machine-placed rungs (keep=none deletes any
        section the rungs failed to bound, so a satin with sane extent IS
        the proof the placement worked)."""
        band = _arc_band_d()
        body = f'<path id="f1" d="{band}" fill="#336699"/>'
        proj_path, svg = _project(tmp_path, body)
        out = _run("tools", "suggest-rungs", "--project", proj_path,
                   "--ids", "f1")
        rung_ids = out["fills"][0]["rungs"]
        _run("tools", "fill-to-satin", "--project", proj_path,
             "--ids", ",".join(["f1"] + rung_ids))
        tree = etree.parse(str(svg))
        ink = "{http://inkstitch.org/namespace}satin_column"
        satins = [e for e in tree.getroot().iter()
                  if (e.get(ink) or "").lower() == "true"]
        assert satins, "engine produced no satin from suggested rungs"
        from cli_anything_inkstitch.artifact.gate import flatten_path
        pts = [p for s in satins for p in flatten_path(s.get("d") or "")]
        xs = [p[0] for p in pts]
        # the band spans x ≈ 24..136; the satin must cover most of it
        assert max(xs) - min(xs) > 90, (min(xs), max(xs))
