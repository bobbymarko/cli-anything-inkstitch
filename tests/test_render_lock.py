"""Stitch-plan renders must not hold the project lock.

The editor re-renders the stitch plan after every edit. That render used to
run the Ink/Stitch binary inside the project lock, so for the seconds it
took, every /edit and /design request queued behind it — over a remote
link, moving a point visibly hung. engine_snapshot copies the design under
the lock and lets the engine read the copy.

The engine is faked with a deliberately slow run_extension: what's under
test is lock scope, not stitch output.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

SLOW_RENDER_S = 1.5


@pytest.fixture
def project(tmp_path):
    from cli_anything_inkstitch.project import ProjectFile
    from cli_anything_inkstitch.svg.document import sha256_of
    svg = tmp_path / "d.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:inkstitch="http://inkstitch.org/namespace" '
        'width="100" height="100" viewBox="0 0 100 100">'
        '<path id="sq" d="M10,10 L90,10 L90,90 L10,90 Z" '
        'style="fill:#c33;stroke:none"/></svg>')
    proj_path = tmp_path / "d.inkstitch-cli.json"
    proj, _ = ProjectFile.load_or_create(str(proj_path))
    proj.svg_path = str(svg)
    proj.svg_sha256 = sha256_of(svg)
    proj.save()
    return str(proj_path), svg


@pytest.fixture
def slow_engine(monkeypatch):
    from cli_anything_inkstitch import binary
    seen = {}

    def fake_run(binary_path, extension, svg_path, args=None, ids=None,
                 capture_stdout=False, timeout=300):
        seen["path"] = svg_path
        seen["content"] = Path(svg_path).read_text()
        time.sleep(SLOW_RENDER_S)
        return b"<svg/>"

    monkeypatch.setattr(binary, "run_extension", fake_run)
    monkeypatch.setattr(binary, "require", lambda override, session: "fake-binary")
    return seen


def _render_in_background(project_file):
    from cli_anything_inkstitch.artifact.design_model import stitch_plan_svg
    t = threading.Thread(target=stitch_plan_svg, args=(project_file,), daemon=True)
    t.start()
    return t


class TestRenderReleasesTheLock:
    def test_edit_completes_while_a_render_is_running(self, project, slow_engine):
        from cli_anything_inkstitch.artifact.design_model import apply_edits
        project_file, svg = project
        render = _render_in_background(project_file)
        time.sleep(0.3)                          # render is inside the engine
        assert render.is_alive()
        t0 = time.monotonic()
        apply_edits(project_file, [{"op": "set_path", "id": "sq",
                                    "d": "M20,20 L80,20 L80,80 L20,80 Z"}])
        waited = time.monotonic() - t0
        still_rendering = render.is_alive()
        render.join()
        # control: the render really was still running when the edit landed
        assert still_rendering
        assert waited < SLOW_RENDER_S / 3, (
            f"edit waited {waited:.2f}s — the render is holding the lock")
        assert "M20,20" in svg.read_text()

    def test_engine_reads_a_private_copy_not_the_live_file(self, project, slow_engine):
        project_file, svg = project
        _render_in_background(project_file).join()
        assert Path(slow_engine["path"]) != svg
        assert Path(slow_engine["path"]).name == svg.name
        assert "M10,10" in slow_engine["content"]
        assert not Path(slow_engine["path"]).exists()     # cleaned up


class TestEnginePassesOverlap:
    """stitch_sequence needs two engine runs (plan + trim flags); each pays
    ~1.1 s of binary startup, so they must run side by side, not back to
    back."""

    def test_plan_and_trim_passes_run_in_parallel(self, project, monkeypatch):
        from cli_anything_inkstitch import binary
        from cli_anything_inkstitch.artifact import design_model as dm
        pass_s = 0.8
        calls = []

        def fake_run(binary_path, extension, svg_path, args=None, ids=None,
                     capture_stdout=False, timeout=300):
            calls.append(extension)
            time.sleep(pass_s)
            return b"<svg/>" if extension == "stitch_plan_preview" else b""

        monkeypatch.setattr(binary, "run_extension", fake_run)
        monkeypatch.setattr(binary, "require", lambda override, session: "fake-binary")
        monkeypatch.setattr(dm, "extract_stitch_blocks",
                            lambda svg: {"blocks": [], "total_stitches": 0})
        t0 = time.monotonic()
        dm.stitch_sequence(project[0])
        elapsed = time.monotonic() - t0
        assert sorted(calls) == ["output", "stitch_plan_preview"]   # control
        assert elapsed < pass_s * 1.5, f"passes ran back to back ({elapsed:.2f}s)"
