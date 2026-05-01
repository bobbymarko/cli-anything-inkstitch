"""Tests for `validate fix`: dispatch to cleanup + manual issue reporting."""

from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner

from cli_anything_inkstitch.cli import root
# pytest adds tests/ to sys.path, so this resolves on local + CI without
# requiring tests/ to be a package.
from test_validate_run import _make_layer_svg


def _open(runner, fixture_svg, project_path):
    runner.invoke(
        root, ["document", "open", "--project", project_path, "--svg", fixture_svg],
        catch_exceptions=False,
    )


def test_fix_no_binary(fixture_svg, project_path):
    """Without an inkstitch binary, fix reports binary_status=not_found."""
    runner = CliRunner()
    _open(runner, fixture_svg, project_path)
    with patch("cli_anything_inkstitch.commands.validate.discover", return_value=None):
        result = runner.invoke(
            root, ["--json", "validate", "fix", "--project", project_path],
            catch_exceptions=False,
        )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["binary_status"] == "not_found"
    assert data["ok"] is None
    assert data["applied"] == []
    assert data["manual"] == []


def test_fix_no_issues(fixture_svg, project_path):
    """Clean SVG: nothing to do, no auto-fix runs, ok=True."""
    runner = CliRunner()
    _open(runner, fixture_svg, project_path)
    clean = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    with patch("cli_anything_inkstitch.commands.validate.discover",
               return_value="/fake/inkstitch"), \
         patch("cli_anything_inkstitch.commands.validate.run_extension",
               return_value=clean) as mock_ext:
        result = runner.invoke(
            root, ["--json", "validate", "fix", "--project", project_path],
            catch_exceptions=False,
        )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["before"] == {"errors": 0, "warnings": 0, "type_warnings": 0}
    assert data["after"] == {"errors": 0, "warnings": 0, "type_warnings": 0}
    assert data["applied"] == []
    assert data["manual"] == []
    # Only the initial troubleshoot run; cleanup wasn't invoked.
    extensions_run = [c.args[1] for c in mock_ext.call_args_list]
    assert extensions_run == ["troubleshoot"]


def test_fix_auto_dispatches_cleanup(fixture_svg, project_path):
    """An EmptyD warning triggers cleanup, then re-validates clean."""
    runner = CliRunner()
    _open(runner, fixture_svg, project_path)
    before_svg = _make_layer_svg([
        ("type_warning", "EmptyD", "empty_path_1", 10, 20),
    ])
    cleanup_svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'  # cleaned-up document
    after_svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'    # no remaining issues
    call_log = []

    def fake_run(_binary, ext, *_args, **_kwargs):
        call_log.append(ext)
        if ext == "cleanup":
            return cleanup_svg
        # troubleshoot is called twice — first returns issues, second returns clean
        return before_svg if call_log.count("troubleshoot") == 1 else after_svg

    with patch("cli_anything_inkstitch.commands.validate.discover",
               return_value="/fake/inkstitch"), \
         patch("cli_anything_inkstitch.commands.validate.run_extension",
               side_effect=fake_run):
        result = runner.invoke(
            root, ["--json", "validate", "fix", "--project", project_path],
            catch_exceptions=False,
        )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert call_log == ["troubleshoot", "cleanup", "troubleshoot"]
    assert data["before"]["type_warnings"] == 1
    assert data["after"] == {"errors": 0, "warnings": 0, "type_warnings": 0}
    assert data["applied"] == [{"tool": "cleanup", "addresses": ["EmptyD"]}]
    assert data["manual"] == []
    assert data["ok"] is True


def test_fix_no_auto_skips_cleanup(fixture_svg, project_path):
    """`--no-auto` reports issues without running cleanup."""
    runner = CliRunner()
    _open(runner, fixture_svg, project_path)
    before_svg = _make_layer_svg([("type_warning", "EmptyD", "empty_1", 0, 0)])
    call_log = []

    def fake_run(_binary, ext, *_args, **_kwargs):
        call_log.append(ext)
        return before_svg

    with patch("cli_anything_inkstitch.commands.validate.discover",
               return_value="/fake/inkstitch"), \
         patch("cli_anything_inkstitch.commands.validate.run_extension",
               side_effect=fake_run):
        result = runner.invoke(
            root, ["--json", "validate", "fix", "--project", project_path,
                   "--no-auto"],
            catch_exceptions=False,
        )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert call_log == ["troubleshoot"]  # cleanup NOT called
    assert data["applied"] == []
    # Auto-fixable issue still surfaces in the before counts but isn't in manual
    # (it wasn't fixed, but it also isn't a "manual" suggestion candidate).
    assert data["before"]["type_warnings"] == 1


def test_fix_manual_issues_get_suggestions(fixture_svg, project_path):
    """Non-auto-fixable issues are reported with one-line suggestions."""
    runner = CliRunner()
    _open(runner, fixture_svg, project_path)
    before_svg = _make_layer_svg([
        ("error", "Not stitchable satin column", "satin1", 5, 5),
        ("type_warning", "Text", "title_text", 50, 50),
        ("warning", "Mystery Problem", "thing", 1, 1),  # no suggestion entry
    ])
    with patch("cli_anything_inkstitch.commands.validate.discover",
               return_value="/fake/inkstitch"), \
         patch("cli_anything_inkstitch.commands.validate.run_extension",
               return_value=before_svg):
        result = runner.invoke(
            root, ["--json", "validate", "fix", "--project", project_path],
            catch_exceptions=False,
        )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["applied"] == []  # nothing auto-fixable
    assert len(data["manual"]) == 3
    by_name = {m["name"]: m for m in data["manual"]}
    assert "satin geometry" in by_name["Not stitchable satin column"]["suggestion"]
    assert "Object to Path" in by_name["Text"]["suggestion"]
    # Unknown name falls back to generic suggestion
    assert "Ink/Stitch" in by_name["Mystery Problem"]["suggestion"]
    assert data["ok"] is False  # the satin error blocks


def test_fix_strict_raises_on_remaining_errors(fixture_svg, project_path):
    """`--strict` exits non-zero if errors remain after fixes."""
    runner = CliRunner()
    _open(runner, fixture_svg, project_path)
    before_svg = _make_layer_svg([
        ("error", "Not stitchable satin column", "satin1", 0, 0),
    ])
    with patch("cli_anything_inkstitch.commands.validate.discover",
               return_value="/fake/inkstitch"), \
         patch("cli_anything_inkstitch.commands.validate.run_extension",
               return_value=before_svg):
        result = runner.invoke(
            root, ["--json", "validate", "fix", "--project", project_path,
                   "--strict"],
            catch_exceptions=False,
        )
    assert result.exit_code == 4  # ValidationError exit code


def test_fix_recognizes_empty_path_label_from_inkstitch_3_2(fixture_svg, project_path):
    """Inkstitch 3.2.x emits 'Empty Path' (not 'EmptyD') for empty-d issues
    in the troubleshoot layer. Both labels must trigger the cleanup auto-fix.
    Regression for the live-test bug where AUTO_FIX_NAMES only contained 'EmptyD'."""
    runner = CliRunner()
    _open(runner, fixture_svg, project_path)
    before_svg = _make_layer_svg([
        ("type_warning", "Empty Path", "p1", 1, 1),
        ("type_warning", "Empty Path", "p2", 2, 2),
    ])
    after_svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    cleanup_svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    call_log = []

    def fake_run(_binary, ext, *_args, **_kwargs):
        call_log.append(ext)
        if ext == "cleanup":
            return cleanup_svg
        return before_svg if call_log.count("troubleshoot") == 1 else after_svg

    with patch("cli_anything_inkstitch.commands.validate.discover",
               return_value="/fake/inkstitch"), \
         patch("cli_anything_inkstitch.commands.validate.run_extension",
               side_effect=fake_run):
        result = runner.invoke(
            root, ["--json", "validate", "fix", "--project", project_path],
            catch_exceptions=False,
        )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "cleanup" in call_log  # cleanup was dispatched
    assert data["applied"] == [{"tool": "cleanup", "addresses": ["Empty Path"]}]
    assert data["manual"] == []


def test_fix_mixed_auto_and_manual(fixture_svg, project_path):
    """When both auto and manual issues exist, cleanup runs and manual stays."""
    runner = CliRunner()
    _open(runner, fixture_svg, project_path)
    before_svg = _make_layer_svg([
        ("type_warning", "EmptyD", "empty_1", 1, 1),
        ("warning", "Small Fill", "tiny_fill", 2, 2),
        ("type_warning", "Text", "label_text", 3, 3),  # manual
    ])
    after_svg = _make_layer_svg([
        ("type_warning", "Text", "label_text", 3, 3),  # only manual remains
    ])
    cleanup_svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    call_log = []

    def fake_run(_binary, ext, *_args, **_kwargs):
        call_log.append(ext)
        if ext == "cleanup":
            return cleanup_svg
        return before_svg if call_log.count("troubleshoot") == 1 else after_svg

    with patch("cli_anything_inkstitch.commands.validate.discover",
               return_value="/fake/inkstitch"), \
         patch("cli_anything_inkstitch.commands.validate.run_extension",
               side_effect=fake_run):
        result = runner.invoke(
            root, ["--json", "validate", "fix", "--project", project_path],
            catch_exceptions=False,
        )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["applied"] == [{"tool": "cleanup",
                                "addresses": ["EmptyD", "Small Fill"]}]
    assert len(data["manual"]) == 1
    assert data["manual"][0]["name"] == "Text"
    assert data["after"]["type_warnings"] == 1
    assert data["after"]["warnings"] == 0
