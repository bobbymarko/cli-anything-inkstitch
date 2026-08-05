"""font compose (task #28): text from pre-digitized Ink/Stitch fonts.

Layout contracts against a synthetic font fixture (no binary needed), then
an integration pass against the fonts shipped with an installed binary
(skips when absent). Glyphs are finished satin elements — compose must
preserve their inkstitch params byte-for-byte and only move geometry.
"""

from __future__ import annotations

import json
import math

import pytest
from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.embroidery.lettering import (
    PX_PER_MM,
    compose,
    list_fonts,
    load_font,
    shipped_fonts_dir,
)
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import sha256_of

SODIPODI = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"

FONT_SVG = f"""<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     xmlns:sodipodi="{SODIPODI}"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     width="100" height="100" viewBox="0 0 100 100">
  <sodipodi:namedview>
    <sodipodi:guide position="0,30" inkscape:label="baseline"/>
  </sodipodi:namedview>
  <g inkscape:label="GlyphLayer-A">
    <path id="ga1" d="M0,70 L10,40 M4,70 L14,40 M0,70 L4,70 M10,40 L14,40"
          stroke="#112233" fill="none" inkstitch:satin_column="true"
          inkstitch:zigzag_spacing_mm="0.35"/>
  </g>
  <g inkscape:label="GlyphLayer-B" transform="translate(20,0)">
    <path id="gb1" d="M0,70 L0,40 M6,70 L6,40 M0,70 L6,70 M0,40 L6,40"
          stroke="#112233" fill="none" inkstitch:satin_column="true"/>
  </g>
</svg>
"""

FONT_JSON = {
    "name": "TestFont", "size": 10.0, "units_per_em": 30, "leading": 40,
    "min_scale": 0.8, "max_scale": 2.0, "default_variant": "ltr",
    "horiz_adv_x": {}, "kerning_pairs": {"AB": 5.0},
    "horiz_adv_x_space": 12,
}


@pytest.fixture
def font_dir(tmp_path):
    d = tmp_path / "testfont"
    d.mkdir()
    (d / "ltr.svg").write_text(FONT_SVG)
    (d / "font.json").write_text(json.dumps(FONT_JSON))
    return d


def _xs(elements):
    from cli_anything_inkstitch.svg.geometry import path_bbox
    return [path_bbox(e.get("d")) for e in elements]


class TestCompose:
    def test_glyphs_advance_left_to_right_and_params_survive(self, font_dir):
        font = load_font(font_dir)
        assert set(font.glyphs) == {"A", "B"}
        # layer transform translate(20,0) must be baked into B's geometry
        assert math.isclose(font.glyphs["B"].min_x, 20.0, abs_tol=1e-6)
        p = compose(font, "AB")
        boxes = _xs(p.elements)
        assert boxes[1][0] > boxes[0][0]          # B starts right of A
        for el in p.elements:
            assert el.get("{http://inkstitch.org/namespace}satin_column") == "true"
        assert p.elements[0].get(
            "{http://inkstitch.org/namespace}zigzag_spacing_mm") == "0.35"

    def test_kerning_pulls_pair_closer(self, font_dir):
        font = load_font(font_dir)
        kerned = compose(font, "AB")
        font.meta["kerning_pairs"] = {}
        loose = compose(font, "AB")
        k_x = _xs(kerned.elements)[1][0]
        l_x = _xs(loose.elements)[1][0]
        assert math.isclose(l_x - k_x, 5.0, abs_tol=1e-6)

    def test_height_scales_geometry_not_params(self, font_dir):
        font = load_font(font_dir)
        p1 = compose(font, "A")
        p2 = compose(font, "A", height_mm=20.0)      # 2x the 10mm font size
        b1, b2 = _xs(p1.elements)[0], _xs(p2.elements)[0]
        assert math.isclose((b2[2] - b2[0]) / (b1[2] - b1[0]), 2.0, rel_tol=1e-6)
        assert p2.elements[0].get(
            "{http://inkstitch.org/namespace}zigzag_spacing_mm") == "0.35"
        assert not p2.warnings                        # 2.0 == max_scale

    def test_scale_limit_violation_warns(self, font_dir):
        font = load_font(font_dir)
        p = compose(font, "A", height_mm=30.0)        # 3x > max_scale 2
        assert any("exceeds" in w for w in p.warnings)

    def test_lines_and_missing_glyphs(self, font_dir):
        font = load_font(font_dir)
        p = compose(font, "A\nA")
        boxes = _xs(p.elements)
        assert math.isclose(boxes[1][1] - boxes[0][1], 40.0, abs_tol=1e-6)
        p2 = compose(font, "AZ")
        assert any("Z" in w for w in p2.warnings)

    def test_baseline_lands_at_requested_y(self, font_dir):
        font = load_font(font_dir)
        # baseline guide at inkscape y=30 in a 100-high doc → svg y 70;
        # glyph bottoms sit ON the baseline (y=70), so placed bottoms = at_y
        p = compose(font, "A", at_px=(0.0, 200.0))
        assert math.isclose(_xs(p.elements)[0][3], 200.0, abs_tol=1e-6)


SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     width="60mm" height="60mm" viewBox="0 0 226.77 226.77">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  <path id="bg" d="M0,0 L226,0 L226,226 L0,226 Z" fill="#eeeeee"/>
</svg>"""


class TestCli:
    def test_compose_places_group_with_history(self, tmp_path, font_dir):
        svg = tmp_path / "design.svg"
        svg.write_text(SVG)
        pp = tmp_path / "design.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(pp))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()
        result = CliRunner().invoke(
            root, ["--json", "font", "compose", "--project", str(pp),
                   "--font", str(font_dir), "--text", "AB",
                   "--height-mm", "15", "--at-mm", "5,30"],
            catch_exceptions=False)
        assert result.exit_code == 0, result.output
        out = json.loads(result.output[result.output.index("{"):])
        assert out["elements"] == 2
        assert out["font"] == "TestFont"
        tree = etree.parse(str(svg))
        group = next(g for g in tree.getroot()
                     if isinstance(g.tag, str)
                     and etree.QName(g.tag).localname == "g"
                     and (g.get("id") or "").startswith("lettering_"))
        assert len(group) == 2
        proj2 = ProjectFile.load(str(pp))
        assert proj2.history["entries"][-1]["command"].startswith("font compose")


needs_fonts = pytest.mark.skipif(
    shipped_fonts_dir(discover()) is None,
    reason="no installed Ink/Stitch binary with shipped fonts")


@needs_fonts
class TestShippedFonts:
    def test_small_font_loads_and_composes(self):
        shipped = shipped_fonts_dir(discover())
        font = load_font(shipped / "small_font")
        assert len(font.glyphs) > 50
        p = compose(font, "Hi", height_mm=6.0)
        assert p.elements
        assert p.width_px > 0

    def test_list_fonts_enumerates(self):
        fonts = list_fonts(discover())
        assert len(fonts) > 50
        assert any(f["font"] == "small_font" for f in fonts)
