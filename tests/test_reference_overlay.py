"""Tests for the reference-art overlay: `document set-reference` and the
design payload the editor reads.

The reference lives in the project SESSION, never in the SVG — the engine
reads the SVG, and a tracing aid must never become a stitchable element,
gate finding, or export artifact. These tests pin both halves of that
contract: the session round-trip works, and the SVG stays untouched.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from cli_anything_inkstitch.cli import root


_DESIGN_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '  <rect id="bg" x="0" y="0" width="100" height="100" fill="#000000"/>'
    '</svg>'
)


def _open(workdir, project_path):
    svg_path = workdir / "design.svg"
    svg_path.write_text(_DESIGN_SVG)
    runner = CliRunner()
    result = runner.invoke(
        root, ["document", "open", "--project", project_path,
               "--svg", str(svg_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    return runner, svg_path


def _run(runner, *args):
    result = runner.invoke(root, ["--json", *args], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output[result.output.index("{"):])


def _ref_image(workdir):
    p = workdir / "ref.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return p


def test_set_reference_roundtrip_defaults(workdir, project_path):
    runner, svg_path = _open(workdir, project_path)
    img = _ref_image(workdir)
    out = _run(runner, "document", "set-reference", "--project", project_path,
               "--image", str(img))
    assert out["reference"] == {"path": str(img), "opacity": 0.4,
                                "visible": True, "x": 0.0, "y": 0.0,
                                "scale": 1.0}
    # persisted in the project session…
    proj = json.loads((workdir / "project.inkstitch-cli.json").read_text())
    assert proj["session"]["reference"]["path"] == str(img)
    # …and NEVER in the engine-facing SVG
    assert "ref.png" not in svg_path.read_text()


def test_set_reference_updates_and_clamps(workdir, project_path):
    runner, _svg = _open(workdir, project_path)
    img = _ref_image(workdir)
    _run(runner, "document", "set-reference", "--project", project_path,
         "--image", str(img))
    out = _run(runner, "document", "set-reference", "--project", project_path,
               "--opacity", "3.0", "--hidden", "--x", "5", "--scale", "1.25")
    ref = out["reference"]
    assert ref["opacity"] == 1.0          # clamped to 0..1
    assert ref["visible"] is False
    assert ref["x"] == 5.0 and ref["scale"] == 1.25
    assert ref["path"] == str(img)        # path survives a metadata-only call


def test_set_reference_clear(workdir, project_path):
    runner, _svg = _open(workdir, project_path)
    img = _ref_image(workdir)
    _run(runner, "document", "set-reference", "--project", project_path,
         "--image", str(img))
    out = _run(runner, "document", "set-reference", "--project", project_path,
               "--clear")
    assert out["reference"] is None


def test_set_reference_missing_image_errors(workdir, project_path):
    runner, _svg = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "document", "set-reference", "--project",
               project_path, "--image", str(workdir / "nope.png")])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_design_payload_carries_reference(workdir, project_path):
    """The editor learns about the overlay through read_design — pin it."""
    from cli_anything_inkstitch.artifact.design_model import read_design
    runner, _svg = _open(workdir, project_path)
    assert read_design(project_path)["reference"] is None
    img = _ref_image(workdir)
    _run(runner, "document", "set-reference", "--project", project_path,
         "--image", str(img), "--opacity", "0.6")
    ref = read_design(project_path)["reference"]
    assert ref["path"] == str(img) and ref["opacity"] == 0.6
