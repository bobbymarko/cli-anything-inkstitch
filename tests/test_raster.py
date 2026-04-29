"""Tests for `preview generate --raster` and `preview rasterize`."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.errors import BinaryError
from cli_anything_inkstitch.inkscape import discover, rasterize


def _open(workdir, project_path):
    svg_path = workdir / "design.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '  <rect id="bg" x="0" y="0" width="100" height="100" fill="#000000"/>'
        '</svg>'
    )
    runner = CliRunner()
    runner.invoke(
        root, ["document", "open", "--project", project_path, "--svg", str(svg_path)],
        catch_exceptions=False,
    )
    return runner, svg_path


# --- inkscape.discover ----------------------------------------------------

def test_discover_uses_env_var(tmp_path, monkeypatch):
    fake = tmp_path / "fake-inkscape"
    fake.touch()
    monkeypatch.setenv("INKSCAPE_BINARY", str(fake))
    assert discover() == str(fake)


def test_discover_ignores_env_var_if_path_missing(tmp_path, monkeypatch):
    """A bogus INKSCAPE_BINARY shouldn't masquerade as a hit."""
    monkeypatch.setenv("INKSCAPE_BINARY", str(tmp_path / "does-not-exist"))
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("cli_anything_inkstitch.inkscape.SEARCH_PATHS", {})
    monkeypatch.setattr("platform.system", lambda: "Linux")
    assert discover() is None


def test_discover_falls_back_to_path(tmp_path, monkeypatch):
    monkeypatch.delenv("INKSCAPE_BINARY", raising=False)
    monkeypatch.setattr("shutil.which",
                         lambda name: "/fake/bin/inkscape" if "inkscape" in name else None)
    assert discover() == "/fake/bin/inkscape"


# --- inkscape.rasterize ---------------------------------------------------

def test_rasterize_raises_when_inkscape_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("cli_anything_inkstitch.inkscape.discover",
                         lambda: None)
    svg = tmp_path / "x.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    try:
        rasterize(str(svg), str(tmp_path / "x.png"))
    except BinaryError as e:
        assert "Inkscape not found" in str(e)
    else:
        raise AssertionError("expected BinaryError")


def test_rasterize_subprocess_failure_propagates(tmp_path, monkeypatch):
    monkeypatch.setattr("cli_anything_inkstitch.inkscape.discover",
                         lambda: "/fake/inkscape")

    class FakeResult:
        returncode = 2
        stderr = b"inkscape failed"

    monkeypatch.setattr("subprocess.run", lambda *_a, **_kw: FakeResult())
    svg = tmp_path / "x.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    try:
        rasterize(str(svg), str(tmp_path / "x.png"))
    except BinaryError as e:
        assert "Inkscape failed" in str(e)
    else:
        raise AssertionError("expected BinaryError")


def test_rasterize_succeeds_when_png_written(tmp_path, monkeypatch):
    """When subprocess returns 0 and the PNG file exists, return its size."""
    monkeypatch.setattr("cli_anything_inkstitch.inkscape.discover",
                         lambda: "/fake/inkscape")

    class FakeResult:
        returncode = 0
        stderr = b""

    out_png = tmp_path / "out.png"

    def fake_run(cmd, **_kw):
        # Simulate inkscape writing the PNG.
        out_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
        return FakeResult()

    monkeypatch.setattr("subprocess.run", fake_run)
    svg = tmp_path / "in.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    n = rasterize(str(svg), str(out_png))
    assert n == out_png.stat().st_size
    assert n > 0


def test_rasterize_zero_exit_but_no_png_errors(tmp_path, monkeypatch):
    """Inkscape can return 0 yet silently fail to write the PNG.
    We catch that explicitly so the caller doesn't trust a missing file."""
    monkeypatch.setattr("cli_anything_inkstitch.inkscape.discover",
                         lambda: "/fake/inkscape")

    class FakeResult:
        returncode = 0
        stderr = b""

    monkeypatch.setattr("subprocess.run", lambda *_a, **_kw: FakeResult())
    svg = tmp_path / "in.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    try:
        rasterize(str(svg), str(tmp_path / "missing.png"))
    except BinaryError as e:
        assert "PNG was not written" in str(e)
    else:
        raise AssertionError("expected BinaryError")


# --- preview generate --raster -------------------------------------------

def test_preview_generate_raster_invokes_rasterizer(workdir, project_path):
    runner, _svg = _open(workdir, project_path)
    fake_preview_svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    out_svg = workdir / "preview.svg"
    out_png = workdir / "preview.png"

    def fake_rasterize(svg_in, png_out, dpi=150, timeout=120):
        # Validate inputs and write a stub PNG.
        assert Path(svg_in).read_bytes() == fake_preview_svg
        Path(png_out).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
        return Path(png_out).stat().st_size

    with patch("cli_anything_inkstitch.commands.preview.require",
               return_value="/fake/inkstitch"), \
         patch("cli_anything_inkstitch.commands.preview.run_extension",
               return_value=fake_preview_svg), \
         patch("cli_anything_inkstitch.inkscape.rasterize",
               side_effect=fake_rasterize):
        result = runner.invoke(
            root, ["--json", "preview", "generate",
                   "--project", project_path,
                   "--out", str(out_svg),
                   "--raster", "--dpi", "200"],
            catch_exceptions=False,
        )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["preview"] == str(out_svg)
    assert data["raster"] == str(out_png)
    assert data["raster_dpi"] == 200
    assert data["raster_bytes"] > 0
    assert out_png.exists()


def test_preview_generate_without_raster_omits_png_fields(workdir, project_path):
    runner, _svg = _open(workdir, project_path)
    out_svg = workdir / "preview.svg"
    with patch("cli_anything_inkstitch.commands.preview.require",
               return_value="/fake/inkstitch"), \
         patch("cli_anything_inkstitch.commands.preview.run_extension",
               return_value=b'<svg xmlns="http://www.w3.org/2000/svg"/>'):
        result = runner.invoke(
            root, ["--json", "preview", "generate",
                   "--project", project_path, "--out", str(out_svg)],
            catch_exceptions=False,
        )
    data = json.loads(result.output)
    assert "raster" not in data
    assert "raster_bytes" not in data


def test_preview_generate_raster_propagates_inkscape_missing(workdir, project_path):
    runner, _svg = _open(workdir, project_path)
    out_svg = workdir / "preview.svg"
    with patch("cli_anything_inkstitch.commands.preview.require",
               return_value="/fake/inkstitch"), \
         patch("cli_anything_inkstitch.commands.preview.run_extension",
               return_value=b'<svg xmlns="http://www.w3.org/2000/svg"/>'), \
         patch("cli_anything_inkstitch.inkscape.discover", return_value=None):
        result = runner.invoke(
            root, ["preview", "generate",
                   "--project", project_path, "--out", str(out_svg),
                   "--raster"],
        )
    # BinaryError → exit code 3 per the CLI's error mapping.
    assert result.exit_code == 3
    assert "Inkscape not found" in result.output


# --- preview rasterize (standalone) -------------------------------------

def test_rasterize_command_round_trip(workdir, monkeypatch):
    svg = workdir / "input.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    out_png = workdir / "output.png"

    def fake_rasterize(svg_in, png_out, dpi=150, timeout=120):
        Path(png_out).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        return Path(png_out).stat().st_size

    runner = CliRunner()
    with patch("cli_anything_inkstitch.inkscape.rasterize",
               side_effect=fake_rasterize):
        result = runner.invoke(
            root, ["--json", "preview", "rasterize",
                   "--svg", str(svg),
                   "--out", str(out_png),
                   "--dpi", "300"],
            catch_exceptions=False,
        )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["raster"] == str(out_png)
    assert data["raster_dpi"] == 300
    assert out_png.exists()


def test_rasterize_command_missing_svg_errors(workdir):
    runner = CliRunner()
    result = runner.invoke(
        root, ["preview", "rasterize",
               "--svg", str(workdir / "does-not-exist.svg"),
               "--out", str(workdir / "out.png")],
    )
    assert result.exit_code == 1  # UserError
    assert "SVG not found" in result.output
