"""Binary tools must record history like any other edit (task #27).

`tools convert-to-satin` used to swap in the binary's full-document output
with no history entry — invisible to undo and the artifact History panel.
The binary is faked here; the history mechanics are what's under test
(the real extension is exercised in test_differential_params.py).
"""

from __future__ import annotations

from click.testing import CliRunner
from lxml import etree

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.commands import tools as tools_mod
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import sha256_of

SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     width="30mm" height="30mm" viewBox="0 0 30 30">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  <path id="elem_run" d="M2,20 L28,20" fill="none" stroke="#000000"/>
</svg>
"""


def _project(tmp_path):
    svg = tmp_path / "d.svg"
    svg.write_text(SVG)
    proj_path = tmp_path / "d.inkstitch-cli.json"
    proj, _ = ProjectFile.load_or_create(str(proj_path))
    proj.svg_path = str(svg)
    proj.svg_sha256 = sha256_of(svg)
    proj.save()
    return str(proj_path), svg


def test_run_tool_records_undoable_history(tmp_path, monkeypatch):
    proj_path, svg = _project(tmp_path)

    def fake_run_extension(binary, ext, svg_path, args=None, ids=None,
                           capture_stdout=False, **kw):
        tree = etree.parse(svg_path)
        el = tree.getroot().find(".//*[@id='elem_run']")
        el.set("{http://inkstitch.org/namespace}satin_column", "True")
        return etree.tostring(tree.getroot())

    monkeypatch.setattr(tools_mod, "require", lambda *a, **k: "/fake/inkstitch")
    monkeypatch.setattr(tools_mod, "run_extension", fake_run_extension)

    r = CliRunner().invoke(root, ["--json", "tools", "convert-to-satin",
                                  "--project", proj_path, "--ids", "elem_run"])
    assert r.exit_code == 0, r.output

    proj = ProjectFile.load(proj_path)
    assert any(e["command"].startswith("tools stroke_to_satin")
               for e in proj.history["entries"])
    assert "satin_column" in svg.read_text()

    # undoes through the same machinery the artifact editor uses
    from cli_anything_inkstitch.artifact.design_model import apply_history_step
    apply_history_step(proj_path)
    assert "satin_column" not in svg.read_text()
