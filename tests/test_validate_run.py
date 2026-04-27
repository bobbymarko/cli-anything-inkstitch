"""Tests for `validate run` and the troubleshoot-layer parser."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.commands.validate import (
    PIXELS_PER_MM,
    parse_validation_layer,
)


def _make_layer_svg(problems):
    """Synthesize an SVG mimicking what inkstitch's troubleshoot extension emits.

    `problems` is a list of (category, problem_name, label, x, y) tuples where
    category is 'error' / 'warning' / 'type_warning'.
    """
    cat_to_id = {
        "error": "__validation_errors__",
        "warning": "__validation_warnings__",
        "type_warning": "__validation_ignored__",
    }
    # Group by (category, problem_name)
    grouped: dict[tuple[str, str], list] = {}
    for cat, name, label, x, y in problems:
        grouped.setdefault((cat, name), []).append((label, x, y))

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">',
        '  <g id="__validation_layer__">',
    ]
    by_cat: dict[str, list] = {"error": [], "warning": [], "type_warning": []}
    for (cat, name), entries in grouped.items():
        by_cat[cat].append((name, entries))

    pointer_id = 0
    for cat in ("error", "warning", "type_warning"):
        cat_groups = by_cat[cat]
        if not cat_groups:
            continue
        parts.append(f'    <g id="{cat_to_id[cat]}">')
        for name, entries in cat_groups:
            parts.append(f'      <g inkscape:label="{name}">')
            # Inkstitch inserts pointers at index 0 (so they end up reverse-ordered)
            # and appends texts in order. Mirror that pattern.
            paths = []
            texts = []
            for label, x, y in entries:
                paths.append(
                    f'        <path id="inkstitch__invalid_pointer__{pointer_id}" '
                    f'd="m {x},{y} 1,5 h -2 l 1,-5"/>'
                )
                pointer_id += 1
                tspan_text = f"{name} ({label})" if label else name
                texts.append(
                    f'        <text><tspan>{tspan_text}</tspan></text>'
                )
            parts.extend(reversed(paths))  # reverse-ordered like real output
            parts.extend(texts)
            parts.append('      </g>')
        parts.append('    </g>')
    parts.append('  </g>')
    parts.append('</svg>')
    return "\n".join(parts).encode()


def test_parse_empty_svg():
    out = parse_validation_layer(b"")
    assert out == {"errors": [], "warnings": [], "type_warnings": [], "issues": []}


def test_parse_no_validation_layer():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><g id="something_else"/></svg>'
    out = parse_validation_layer(svg)
    assert out["issues"] == []


def test_parse_single_error():
    svg = _make_layer_svg([("error", "Dangling Rung", "logo_outline", 100, 200)])
    out = parse_validation_layer(svg)
    assert len(out["errors"]) == 1
    assert out["errors"][0]["name"] == "Dangling Rung"
    assert out["errors"][0]["label"] == "logo_outline"
    assert out["errors"][0]["x"] == 100.0
    assert out["errors"][0]["y"] == 200.0
    assert out["errors"][0]["x_mm"] == round(100.0 / PIXELS_PER_MM, 3)


def test_parse_mixed_categories():
    svg = _make_layer_svg([
        ("error", "Invalid Shape", "shape_a", 10, 20),
        ("warning", "Small Shape", "shape_b", 30, 40),
        ("warning", "Small Shape", "shape_c", 50, 60),
        ("type_warning", "Marker Warning", "marker1", 70, 80),
    ])
    out = parse_validation_layer(svg)
    assert len(out["errors"]) == 1
    assert len(out["warnings"]) == 2
    assert len(out["type_warnings"]) == 1
    # Both warnings carry the right name/labels.
    labels = {w["label"] for w in out["warnings"]}
    assert labels == {"shape_b", "shape_c"}


def test_parse_pointer_order_pairs_correctly():
    """Each pointer's coords must pair with the right element label."""
    svg = _make_layer_svg([
        ("error", "Dangling Rung", "first", 1, 1),
        ("error", "Dangling Rung", "second", 2, 2),
        ("error", "Dangling Rung", "third", 3, 3),
    ])
    out = parse_validation_layer(svg)
    pairs = sorted((e["label"], e["x"]) for e in out["errors"])
    assert pairs == [("first", 1.0), ("second", 2.0), ("third", 3.0)]


def test_parse_label_missing():
    svg = _make_layer_svg([("error", "No Label Problem", "", 5, 5)])
    out = parse_validation_layer(svg)
    assert out["errors"][0]["label"] == ""
    assert out["errors"][0]["name"] == "No Label Problem"


def test_validate_run_no_binary(fixture_svg, project_path):
    """Without an inkstitch binary, validate run should report binary_status=not_found."""
    runner = CliRunner()
    runner.invoke(root, ["document", "open", "--project", project_path, "--svg", fixture_svg],
                  catch_exceptions=False)
    with patch("cli_anything_inkstitch.commands.validate.discover", return_value=None):
        result = runner.invoke(root, ["--json", "validate", "run", "--project", project_path],
                                catch_exceptions=False)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["binary_status"] == "not_found"
    assert data["ok"] is None
    assert data["issues"] == []


def test_validate_run_parses_extension_output(fixture_svg, project_path):
    """End-to-end: mock the binary call, return a synthesized validation layer, parse it."""
    runner = CliRunner()
    runner.invoke(root, ["document", "open", "--project", project_path, "--svg", fixture_svg],
                  catch_exceptions=False)
    fake_svg = _make_layer_svg([
        ("error", "Invalid Shape", "logo_outline", 96, 96),  # 96px = ~25.4mm
        ("warning", "Small Shape", "logo_dot", 0, 0),
    ])
    with patch("cli_anything_inkstitch.commands.validate.discover", return_value="/fake/inkstitch"), \
         patch("cli_anything_inkstitch.commands.validate.run_extension", return_value=fake_svg):
        result = runner.invoke(root, ["--json", "validate", "run", "--project", project_path],
                                catch_exceptions=False)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["counts"] == {"errors": 1, "warnings": 1, "type_warnings": 0}
    assert data["errors"][0]["label"] == "logo_outline"
    assert data["errors"][0]["x_mm"] == 25.4


def test_validate_run_strict_raises_on_errors(fixture_svg, project_path):
    runner = CliRunner()
    runner.invoke(root, ["document", "open", "--project", project_path, "--svg", fixture_svg],
                  catch_exceptions=False)
    fake_svg = _make_layer_svg([("error", "Invalid Shape", "logo_outline", 0, 0)])
    with patch("cli_anything_inkstitch.commands.validate.discover", return_value="/fake/inkstitch"), \
         patch("cli_anything_inkstitch.commands.validate.run_extension", return_value=fake_svg):
        result = runner.invoke(root, ["--json", "validate", "run",
                                       "--project", project_path, "--strict"],
                                catch_exceptions=False)
    assert result.exit_code == 4  # ValidationError exit code
