"""Settings the engine reads from <metadata>, not from our project JSON.

collapse_len and min_stitch_len were recorded in the session only, so they
never reached the engine: every design ran the 3.0 mm collapse default however
it was set. That is invisible in the worst way — a gap under the default is
stitched THROUGH rather than jumped, so the thread it lays between shapes
appears in no jump or trim count.

Reader: lib/extensions/density_map.py:41-42 (self.metadata['collapse_len_mm'],
self.metadata['min_stitch_len_mm']), same in element_info.py and
batch_lettering.py.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.svg.document import get_inkstitch_metadata

SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"
     viewBox="0 0 100 100"><path id="a" d="M 0,0 L 10,10"
     style="fill:none;stroke:#000"/></svg>"""


@pytest.fixture()
def project(tmp_path):
    svg = tmp_path / "d.svg"
    svg.write_text(SVG)
    proj = tmp_path / "d.inkstitch-cli.json"
    r = CliRunner().invoke(root, ["document", "open", "--project", str(proj),
                                  "--svg", str(svg)], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return proj, svg


@pytest.mark.parametrize("command,key,value", [
    ("set-collapse-len", "collapse_len_mm", 0.6),
    ("set-min-stitch-len", "min_stitch_len_mm", 0.2),
])
def test_setting_reaches_the_svg_metadata(project, command, key, value):
    proj, svg = project
    r = CliRunner().invoke(root, ["document", command, "--project", str(proj),
                                  "--mm", str(value)], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    tree = etree.parse(str(svg))
    assert get_inkstitch_metadata(tree, key) == value, (
        f"{command} did not write {key} where the engine reads it")


def test_the_value_survives_a_reopen(project):
    proj, svg = project
    CliRunner().invoke(root, ["document", "set-collapse-len", "--project",
                              str(proj), "--mm", "1.2"], catch_exceptions=False)
    CliRunner().invoke(root, ["document", "open", "--project", str(proj),
                              "--svg", str(svg), "--force"], catch_exceptions=False)
    assert get_inkstitch_metadata(etree.parse(str(svg)), "collapse_len_mm") == 1.2
