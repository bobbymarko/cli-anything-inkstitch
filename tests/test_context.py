"""Tests for `document set-context` / `get-context` and surfacing in
element list / describe."""

from __future__ import annotations

import json

from click.testing import CliRunner

from cli_anything_inkstitch.cli import root


_DESIGN_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '  <rect id="bg" x="0" y="0" width="100" height="100" fill="#000000"/>'
    '  <circle id="dot" cx="50" cy="50" r="10" fill="#ff0000"/>'
    '</svg>'
)


def _open(workdir, project_path):
    svg_path = workdir / "design.svg"
    svg_path.write_text(_DESIGN_SVG)
    runner = CliRunner()
    runner.invoke(
        root, ["document", "open", "--project", project_path, "--svg", str(svg_path)],
        catch_exceptions=False,
    )
    return runner


# --- get-context ----------------------------------------------------------

def test_get_context_empty_by_default(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "document", "get-context", "--project", project_path],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert json.loads(result.output) == {"context": {}}


# --- set-context typed fields ---------------------------------------------

def test_set_context_typed_fields(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "document", "set-context", "--project", project_path,
               "--material", "denim",
               "--stretch", "low",
               "--thread", "40wt polyester",
               "--stabilizer", "tear-away",
               "--hoop-tension", "firm",
               "--intent", "patch for jacket back"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["context"] == {
        "material": "denim",
        "stretch": "low",
        "thread": "40wt polyester",
        "stabilizer": "tear-away",
        "hoop_tension": "firm",
        "intent": "patch for jacket back",
    }


def test_set_context_persists_across_calls(workdir, project_path):
    runner = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "set-context", "--project", project_path,
               "--material", "fleece"],
        catch_exceptions=False,
    )
    result = runner.invoke(
        root, ["--json", "document", "get-context", "--project", project_path],
        catch_exceptions=False,
    )
    assert json.loads(result.output)["context"]["material"] == "fleece"


def test_set_context_partial_update(workdir, project_path):
    """Setting one field leaves the others alone."""
    runner = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "set-context", "--project", project_path,
               "--material", "denim", "--thread", "60wt cotton"],
    )
    runner.invoke(
        root, ["document", "set-context", "--project", project_path,
               "--thread", "40wt polyester"],  # overwrite just thread
    )
    result = runner.invoke(
        root, ["--json", "document", "get-context", "--project", project_path],
    )
    ctx = json.loads(result.output)["context"]
    assert ctx == {"material": "denim", "thread": "40wt polyester"}


# --- set-context choice validation ----------------------------------------

def test_set_context_invalid_stretch_rejected(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["document", "set-context", "--project", project_path,
               "--stretch", "extreme"],
    )
    assert result.exit_code == 2
    assert "invalid value" in result.output.lower()


def test_set_context_invalid_tension_rejected(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["document", "set-context", "--project", project_path,
               "--hoop-tension", "extra-firm"],
    )
    assert result.exit_code == 2


# --- set / unset / clear ---------------------------------------------------

def test_set_arbitrary_kv(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "document", "set-context", "--project", project_path,
               "--set", "wash_count=50",
               "--set", "color_palette=team_2026"],
        catch_exceptions=False,
    )
    ctx = json.loads(result.output)["context"]
    assert ctx == {"wash_count": "50", "color_palette": "team_2026"}


def test_set_kv_value_with_equals_sign(workdir, project_path):
    """KEY=VALUE only splits on the first =."""
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "document", "set-context", "--project", project_path,
               "--set", "url=https://example.com/?a=b"],
    )
    ctx = json.loads(result.output)["context"]
    assert ctx["url"] == "https://example.com/?a=b"


def test_set_kv_without_equals_errors(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["document", "set-context", "--project", project_path,
               "--set", "no_equals_here"],
    )
    assert result.exit_code == 1
    assert "KEY=VALUE" in result.output


def test_unset_removes_key(workdir, project_path):
    runner = _open(workdir, project_path)
    runner.invoke(root, ["document", "set-context", "--project", project_path,
                          "--material", "denim", "--thread", "40wt"])
    result = runner.invoke(
        root, ["--json", "document", "set-context", "--project", project_path,
               "--unset", "thread"],
    )
    ctx = json.loads(result.output)["context"]
    assert ctx == {"material": "denim"}


def test_unset_unknown_key_is_silent(workdir, project_path):
    """Removing a key that isn't set shouldn't error."""
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "document", "set-context", "--project", project_path,
               "--unset", "never_was_here"],
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["context"] == {}


def test_clear_wipes_context(workdir, project_path):
    runner = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "set-context", "--project", project_path,
               "--material", "denim", "--thread", "40wt"],
    )
    result = runner.invoke(
        root, ["--json", "document", "set-context", "--project", project_path,
               "--clear"],
    )
    assert json.loads(result.output)["context"] == {}


def test_clear_then_set_in_one_call(workdir, project_path):
    """--clear runs first, then the new fields apply."""
    runner = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "set-context", "--project", project_path,
               "--material", "denim"],
    )
    result = runner.invoke(
        root, ["--json", "document", "set-context", "--project", project_path,
               "--clear", "--material", "fleece"],
    )
    assert json.loads(result.output)["context"] == {"material": "fleece"}


# --- surfacing in element list / describe --------------------------------

def test_element_list_surfaces_context(workdir, project_path):
    runner = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "set-context", "--project", project_path,
               "--material", "denim"],
    )
    result = runner.invoke(
        root, ["--json", "element", "list", "--project", project_path],
        catch_exceptions=False,
    )
    data = json.loads(result.output)
    assert data["document_context"]["material"] == "denim"


def test_element_list_omits_context_field_when_empty(workdir, project_path):
    runner = _open(workdir, project_path)
    result = runner.invoke(
        root, ["--json", "element", "list", "--project", project_path],
    )
    data = json.loads(result.output)
    assert "document_context" not in data  # no clutter when nothing's set


def test_element_describe_surfaces_context_for_single_id(workdir, project_path):
    runner = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "set-context", "--project", project_path,
               "--material", "fleece"],
    )
    result = runner.invoke(
        root, ["--json", "element", "describe", "--project", project_path,
               "--id", "dot"],
    )
    data = json.loads(result.output)
    assert data["document_context"]["material"] == "fleece"


def test_element_describe_surfaces_context_for_all(workdir, project_path):
    runner = _open(workdir, project_path)
    runner.invoke(
        root, ["document", "set-context", "--project", project_path,
               "--intent", "test patch"],
    )
    result = runner.invoke(
        root, ["--json", "element", "describe", "--project", project_path],
    )
    data = json.loads(result.output)
    assert data["document_context"]["intent"] == "test patch"


# --- legacy project compat (no `context` key in stored JSON) -------------

def test_get_context_handles_legacy_session_without_context_key(
    workdir, project_path
):
    runner = _open(workdir, project_path)
    # Simulate a project file written by an older version: strip context.
    p = project_path  # type: ignore
    raw = json.loads(open(p).read())
    raw["session"].pop("context", None)
    open(p, "w").write(json.dumps(raw))
    result = runner.invoke(
        root, ["--json", "document", "get-context", "--project", p],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert json.loads(result.output) == {"context": {}}
