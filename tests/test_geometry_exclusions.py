"""FINDINGS-stitch-geometry.md Bugs 1-3: geometry/enumeration must exclude
what the engine excludes.

Engine readers (cited, not inferred):
* inkstitch/lib/commands.py:303-304 is_command — connector paths carry
  inkscape:connection-start/-end and are NEVER stitched
  (lib/elements/utils/nodes.py builds a Stroke only `elif not is_command`).
* <defs>/<symbol> contents are template data (the engine's own trim-icon
  glyphs) — referenced, never traversed, by stitch generation.

Measured impact that motivated these: art_bbox() overstated the rose-bag
design's height by 58% because one connector's bbox set the top edge, and
`document info` reported `auto_fill: 2` on a design with zero fills (the
trim symbol's icon paths).
"""

from __future__ import annotations

import json

from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import all_addressable_elements, sha256_of
from cli_anything_inkstitch.svg.units import PIXELS_PER_MM, art_bbox

SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     width="100mm" height="100mm" viewBox="0 0 377.95 377.95">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  <defs>
    <symbol id="inkstitch_trim">
      <path id="icon_circle" style="fill:#fafafa" d="M0,0 L8,0 L8,8 L0,8 Z"/>
      <path id="icon_glyph" style="fill:#050505" d="M2,2 L6,2 L6,6 L2,6 Z"/>
    </symbol>
  </defs>
  <path id="art" d="M100,100 L150,100 L150,150 L100,150 Z" fill="#336699"/>
  <path id="command_connector1" d="M10,10 L370,370" fill="none"
        stroke="#000000" style="fill:none;stroke:#000000"
        inkscape:connection-start="#art" inkscape:connection-end="#use1"/>
  <use id="use1" xlink:href="#inkstitch_trim" x="360" y="360"/>
</svg>"""


def _tree():
    return etree.ElementTree(etree.fromstring(SVG.encode()))


class TestArtBbox:
    def test_connector_does_not_inflate_bbox(self):
        """Bug 1: the connector spans (10,10)-(370,370); the art is a 50px
        square. The union must be the square alone."""
        bb = art_bbox(_tree())
        assert bb == (100.0, 100.0, 150.0, 150.0)

    def test_symbol_template_shapes_excluded(self):
        # already covered by the fixture: icon paths sit at (0,0)-(8,8);
        # including them would drag x0/y0 to 0
        bb = art_bbox(_tree())
        assert bb[0] == 100.0 and bb[1] == 100.0


class TestEnumeration:
    def test_defs_symbol_paths_not_addressable(self):
        """Bug 2: the engine never traverses <defs>; neither do we."""
        ids = [e.get("id") for e in all_addressable_elements(_tree())]
        assert "icon_circle" not in ids
        assert "icon_glyph" not in ids

    def test_connector_not_addressable(self):
        """Bug 3: params set on a connector is a silent engine no-op — the
        exact bug class CLAUDE.md exists to prevent. Not enumerable."""
        ids = [e.get("id") for e in all_addressable_elements(_tree())]
        assert "command_connector1" not in ids
        assert "art" in ids
        # the <use> marker reference itself stays addressable (it is how
        # commands render; the commands group manages it)
        assert "use1" in ids


class TestDocumentInfoHistogram:
    def test_no_phantom_fills_or_connector_runs(self, tmp_path):
        svg = tmp_path / "design.svg"
        svg.write_text(SVG)
        pp = tmp_path / "design.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(pp))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()
        result = CliRunner().invoke(
            root, ["--json", "document", "info", "--project", str(pp)],
            catch_exceptions=False)
        assert result.exit_code == 0, result.output
        info = json.loads(result.output[result.output.index("{"):])
        histo = info.get("stitch_type_histogram") or {}
        # one real fill; no phantom auto_fill from the symbol icon paths,
        # no running_stitch from the connector
        assert histo.get("running_stitch") is None or \
            histo.get("running_stitch") == 0, histo
        assert (histo.get("auto_fill") or 0) <= 1, histo
