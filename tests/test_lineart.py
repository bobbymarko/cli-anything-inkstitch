"""`tools digitize-lineart` + trace/lineart.py (task #49).

Pure-function tests use synthetic rasters drawn with Pillow so every
expectation is exact.  The end-to-end test drives the real vtracer + engine
binary on a small synthetic drawing and verifies the OUTPUT GEOMETRY against
the input raster (both coverage directions), not just exit codes — the
engine fails silent, so success signals prove nothing (CLAUDE.md rule 3/4).
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from PIL import Image, ImageDraw

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.trace import lineart


def synthetic_lineart(tmp_path, name="art.png"):
    """A 200x200 drawing: square outline + diagonal + isolated thin tick.

    The tick is thin (2 px) on purpose — the class of mark fill_to_stroke
    drops and reverse coverage must recover.
    """
    img = Image.new("L", (200, 200), 255)
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, 160, 160), outline=0, width=6)
    d.line((40, 40, 160, 160), fill=0, width=6)
    d.line((70, 100, 90, 120), fill=0, width=2)  # isolated tick
    p = tmp_path / name
    img.save(p)
    return p


class TestThreshold:
    def test_crops_to_ink_with_margin(self, tmp_path):
        p = synthetic_lineart(tmp_path)
        img = lineart.threshold_image(p, margin=8)
        # wherever the ink lands (stroke widths overhang endpoints), the
        # crop must leave exactly the margin around it on every side
        x0, y0, x1, y1 = lineart.ink_bbox(img)
        w, h = img.size
        assert (x0, y0) == (8, 8)
        assert (x1, y1) == (w - 9, h - 9)

    def test_blank_image_is_an_error(self, tmp_path):
        p = tmp_path / "blank.png"
        Image.new("L", (50, 50), 255).save(p)
        with pytest.raises(ValueError, match="no dark pixels"):
            lineart.threshold_image(p)


class TestInkFraction:
    def _img(self):
        img = Image.new("L", (100, 100), 255)
        ImageDraw.Draw(img).line((10, 50, 90, 50), fill=0, width=4)
        return img

    def test_stroke_on_ink_scores_high(self):
        img = self._img()
        kd = 2.0  # doc px per source px
        pts = [(20.0, 100.0), (180.0, 100.0)]  # maps onto the drawn line
        assert lineart.ink_fraction(pts, img, kd) > 0.95

    def test_stroke_over_blank_paper_scores_low(self):
        img = self._img()
        kd = 2.0
        pts = [(20.0, 20.0), (180.0, 20.0)]  # maps onto y=10: blank
        assert lineart.ink_fraction(pts, img, kd) < 0.1


class TestReverseCoverage:
    def test_uncovered_tick_found_and_centerlined(self):
        img = Image.new("L", (100, 100), 255)
        d = ImageDraw.Draw(img)
        d.line((10, 20, 90, 20), fill=0, width=4)   # covered stroke
        d.line((30, 60, 60, 80), fill=0, width=2)   # missed tick
        mask = lineart.stroke_cover_mask(img.size, [[(10, 20), (90, 20)]])
        missed = lineart.uncovered_ink_components(img, mask)
        assert len(missed) == 1
        line = lineart.component_centerline(missed[0])
        xs = [p[0] for p in line]
        ys = [p[1] for p in line]
        # the centerline traverses the tick end to end
        assert min(xs) <= 32 and max(xs) >= 58
        assert min(ys) <= 62 and max(ys) >= 78

    def test_fully_covered_ink_reports_nothing(self):
        img = Image.new("L", (100, 100), 255)
        ImageDraw.Draw(img).line((10, 20, 90, 20), fill=0, width=4)
        mask = lineart.stroke_cover_mask(img.size, [[(10, 20), (90, 20)]])
        assert lineart.uncovered_ink_components(img, mask) == []


class TestBuildPxDocument:
    def test_px_unit_viewbox_and_scale(self, tmp_path):
        traced = tmp_path / "t.svg"
        traced.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<path d="M0,0 L100,0 L100,200 L0,200 Z"/></svg>')
        tree, kd = lineart.build_px_document(traced, (100, 200),
                                             width_mm=None, height_mm=50.0)
        root_el = tree.getroot()
        from cli_anything_inkstitch.svg.units import document_scale
        sx, sy = document_scale(root_el)
        assert abs(sx - 1.0) < 0.001 and abs(sy - 1.0) < 0.001
        assert root_el.get("width") == "25mm"  # aspect-derived
        # kd maps 200 source px onto 50mm of document px
        assert abs(kd * 200 - 50 * 96 / 25.4) < 0.01

    def test_requires_exactly_one_dimension(self, tmp_path):
        traced = tmp_path / "t.svg"
        traced.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0,0 L1,1"/></svg>')
        with pytest.raises(ValueError):
            lineart.build_px_document(traced, (10, 10), None, None)


# ---- end to end -------------------------------------------------------------

from cli_anything_inkstitch.binary import discover  # noqa: E402

try:
    import vtracer  # noqa: F401
    HAVE_VTRACER = True
except ImportError:
    HAVE_VTRACER = False


@pytest.mark.skipif(discover() is None, reason="Ink/Stitch binary not installed")
@pytest.mark.skipif(not HAVE_VTRACER, reason="vtracer not installed")
class TestDigitizeLineartEndToEnd:
    def test_raster_becomes_verified_routed_design(self, tmp_path):
        image = synthetic_lineart(tmp_path)
        proj = tmp_path / "art.inkstitch-cli.json"
        r = CliRunner().invoke(root, [
            "--json", "tools", "digitize-lineart",
            "--project", str(proj), "--image", str(image),
            "--width-mm", "50"])
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload["gate_ok"] is True
        assert payload["routed"] is True
        assert payload["styled_tops"] > 0
        assert payload["strokes"] >= 3  # square + diagonal pieces + tick

        # behavioral proof, both directions, against the source raster:
        from cli_anything_inkstitch.artifact.design_model import read_design
        from cli_anything_inkstitch.artifact.gate import flatten_path
        img = lineart.threshold_image(image)
        design = read_design(str(proj))
        kd = (50 * 96 / 25.4) / img.size[0]

        tops = [o for o in design["objects"]
                if o.get("params", {}).get("path_type") == "autorun-top"]
        assert tops, "no routed tops in the design"
        # 1) stroke→ink: every stitched top runs over actual drawing
        for o in tops:
            pts = flatten_path(o["d"])
            assert lineart.ink_fraction(pts, img, kd) > 0.7, \
                f"{o['id']} stitches over blank paper"
        # 2) ink→stroke: no drawn mark was lost (incl. the thin tick that
        #    fill_to_stroke drops without the recovery pass)
        mask = lineart.stroke_cover_mask(
            img.size,
            [[(x / kd, y / kd) for x, y in flatten_path(o["d"])]
             for o in tops])
        assert lineart.uncovered_ink_components(img, mask) == []

        # 3) physical size: the DST must sew at the requested width
        out_dst = tmp_path / "art.dst"
        r = CliRunner().invoke(root, [
            "--json", "export", "file", "--project", str(proj),
            "--format", "dst", "--out", str(out_dst)])
        assert r.exit_code == 0, r.output
        import pyembroidery
        p = pyembroidery.read(str(out_dst))
        x0, _y0, x1, _y1 = p.bounds()
        # --width-mm covers the cropped raster; centerlines sit half a
        # stroke-width inside the ink, so the sewn width is a bit under the
        # request.  The failure this catches is scale corruption: a px/mm
        # mix-up sews at 13.2 or 189 mm, not inside this band.
        assert 0.7 * 50 < (x1 - x0) / 10 < 1.05 * 50
