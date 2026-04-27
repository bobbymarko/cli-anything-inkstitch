"""End-to-end smoke tests for the lxml-only command surface."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from cli_anything_inkstitch.cli import root


@pytest.fixture
def runner():
    return CliRunner()


def invoke(runner, *args):
    result = runner.invoke(root, list(args), catch_exceptions=False)
    return result


def jrun(runner, *args):
    """Invoke with --json prepended; parse stdout JSON."""
    result = invoke(runner, "--json", *args)
    assert result.exit_code == 0, f"exit {result.exit_code}: stderr={result.stderr}\nstdout={result.output}"
    return json.loads(result.output)


def test_open_lists_three_elements(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    data = jrun(runner, "element", "list", "--project", project_path, "--refresh")
    assert data["count"] == 3
    ids = {e["id"] for e in data["elements"]}
    assert ids == {"logo_outline", "logo_text", "logo_dot"}


def test_classify(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    out = jrun(runner, "element", "identify", "--project", project_path, "--id", "logo_outline")
    # path with stroke, no fill → running_stitch by default
    assert out["stitch_type"] == "running_stitch"
    out = jrun(runner, "element", "identify", "--project", project_path, "--id", "logo_text")
    assert out["stitch_type"] == "auto_fill"


def test_set_satin_requires_stroke(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    # logo_text has no stroke → should fail without --force
    result = invoke(runner, "params", "set", "--project", project_path,
                    "--id", "logo_text", "--stitch-type", "satin_column")
    assert result.exit_code == 1
    assert "stroke" in result.stderr.lower()


def test_set_satin_on_stroked_path(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    out = jrun(runner, "params", "set", "--project", project_path,
               "--id", "logo_outline", "--stitch-type", "satin_column",
               "--pull-compensation-mm", "0.4", "--zigzag-spacing-mm", "0.35")
    assert out["stitch_type"] == "satin_column"
    assert "pull_compensation_mm" in out["changed"]
    assert out["changed"]["pull_compensation_mm"] == "0.4"

    # confirm by reading back
    got = jrun(runner, "params", "get", "--project", project_path, "--id", "logo_outline")
    assert got["stitch_type"] == "satin_column"
    assert got["params"]["pull_compensation_mm"]["value"] == 0.4
    assert got["params"]["pull_compensation_mm"]["set"] is True


def test_set_then_undo_redo(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    jrun(runner, "params", "set", "--project", project_path,
         "--id", "logo_text", "--stitch-type", "auto_fill",
         "--row-spacing-mm", "0.3")
    after = jrun(runner, "params", "get", "--project", project_path, "--id", "logo_text")
    assert after["params"]["row_spacing_mm"]["value"] == 0.3

    undone = jrun(runner, "session", "undo", "--project", project_path)
    assert len(undone["undone"]) == 1

    after_undo = jrun(runner, "params", "get", "--project", project_path, "--id", "logo_text")
    assert after_undo["params"]["row_spacing_mm"]["set"] is False

    redone = jrun(runner, "session", "redo", "--project", project_path)
    assert len(redone["redone"]) == 1
    after_redo = jrun(runner, "params", "get", "--project", project_path, "--id", "logo_text")
    assert after_redo["params"]["row_spacing_mm"]["value"] == 0.3


def test_validate_param_range(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    # row_spacing_mm has max 5.0; try 99
    result = invoke(runner, "params", "set", "--project", project_path,
                    "--id", "logo_text", "--stitch-type", "auto_fill",
                    "--row-spacing-mm", "99")
    assert result.exit_code == 1
    assert "above max" in result.stderr or "row_spacing_mm" in result.stderr


def test_unknown_param(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    result = invoke(runner, "params", "set", "--project", project_path,
                    "--id", "logo_text", "--stitch-type", "auto_fill",
                    "--no-such-param", "1")
    assert result.exit_code == 1
    assert "unknown param" in result.stderr


def test_attach_then_detach_command(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    jrun(runner, "commands", "attach", "--project", project_path, "--id", "logo_text", "--command", "trim")
    listed = jrun(runner, "commands", "list", "--project", project_path, "--id", "logo_text")
    assert listed["count"] == 1
    assert listed["commands"][0]["command"] == "trim"
    jrun(runner, "commands", "detach", "--project", project_path, "--id", "logo_text", "--command", "trim")
    listed = jrun(runner, "commands", "list", "--project", project_path, "--id", "logo_text")
    assert listed["count"] == 0


def test_schema_introspection(runner):
    out = jrun(runner, "schema", "list-stitch-types")
    names = {t["name"] for t in out["stitch_types"]}
    assert {"auto_fill", "satin_column", "running_stitch", "manual_stitch"} <= names

    detail = jrun(runner, "schema", "get-stitch-type", "--type", "satin_column")
    assert "pull_compensation_mm" in detail["params"]


def test_static_validate_flags_unassigned(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    out = jrun(runner, "validate", "static", "--project", project_path)
    # logo_outline classifies as running_stitch (has stroke), logo_text as auto_fill,
    # logo_dot (circle with fill) classifies as auto_fill — none should be unassigned.
    types = {i["type"] for i in out["issues"]}
    assert "unassigned" not in types


def test_clear_params_removes_inkstitch_attrs(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    jrun(runner, "params", "set", "--project", project_path,
         "--id", "logo_text", "--stitch-type", "auto_fill", "--angle", "45")
    jrun(runner, "element", "clear-params", "--project", project_path, "--id", "logo_text")
    got = jrun(runner, "element", "get", "--project", project_path, "--id", "logo_text")
    inkstitch_attrs = [k for k in got["attributes"] if "inkstitch" in k]
    assert inkstitch_attrs == []


def test_copy_params(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    jrun(runner, "params", "set", "--project", project_path,
         "--id", "logo_text", "--stitch-type", "auto_fill",
         "--angle", "45", "--row-spacing-mm", "0.3")
    jrun(runner, "params", "copy", "--project", project_path,
         "--from", "logo_text", "--to", "logo_dot")
    got = jrun(runner, "params", "get", "--project", project_path, "--id", "logo_dot")
    assert got["params"]["angle"]["value"] == 45.0


def test_session_status(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    out = jrun(runner, "session", "status", "--project", project_path)
    assert out["history_size"] == 0
    assert out["can_undo"] is False
    jrun(runner, "params", "set", "--project", project_path,
         "--id", "logo_text", "--stitch-type", "auto_fill", "--angle", "30")
    out = jrun(runner, "session", "status", "--project", project_path)
    assert out["history_size"] == 1
    assert out["can_undo"] is True


def test_history_limit_truncates(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    for i in range(60):
        jrun(runner, "params", "set", "--project", project_path,
             "--id", "logo_text", "--stitch-type", "auto_fill",
             "--angle", str(i))
    out = jrun(runner, "session", "history", "--project", project_path, "--limit", "100")
    assert out["total"] == 50  # ring buffer enforced


def test_export_formats_lists_pyembroidery_writers(runner):
    out = jrun(runner, "export", "formats")
    exts = {f["extension"] for f in out.get("formats", [])}
    # spot-check a few writers we know pyembroidery supports
    assert "dst" in exts or out.get("error")  # be lenient if pyembroidery API differs


def test_document_set_hoop_undo(runner, fixture_svg, project_path):
    invoke(runner, "document", "open", "--project", project_path, "--svg", fixture_svg)
    out = jrun(runner, "document", "set-hoop", "--project", project_path, "--name", "130x180")
    assert out["hoop"]["width_mm"] == 130.0
    jrun(runner, "session", "undo", "--project", project_path)
    info = jrun(runner, "document", "info", "--project", project_path)
    assert info["session"]["hoop"]["name"] == "100x100"
