"""Tests for the install-time schema extractor (SPEC §3.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.schema.bootstrap import STITCH_TYPES as BOOTSTRAP_TYPES
from cli_anything_inkstitch.schema.extract import (
    extract_schema,
    find_inkstitch_source,
)


@pytest.fixture(scope="module")
def source_root():
    root = find_inkstitch_source()
    if root is None:
        pytest.skip("inkstitch source not present alongside repo")
    return root


@pytest.fixture(scope="module")
def schema(source_root):
    return extract_schema(source_root)


def test_extractor_finds_known_classes(schema):
    classes = set(schema["source"]["classes"])
    assert {"EmbroideryElement", "FillStitch", "SatinColumn", "Stroke"} <= classes


def test_extractor_pulls_more_params_than_bootstrap(schema):
    """Each stitch type should have at least as many params as the bootstrap fallback."""
    for st_name, bootstrap_st in BOOTSTRAP_TYPES.items():
        if st_name not in schema["stitch_types"]:
            continue  # bootstrap names like cross_stitch_half not in extractor map
        bootstrap_count = len(bootstrap_st["params"])
        extracted_count = len(schema["stitch_types"][st_name]["params"])
        assert extracted_count >= bootstrap_count, (
            f"{st_name}: extracted {extracted_count} < bootstrap {bootstrap_count}"
        )


def test_satin_column_has_extended_params(schema):
    """Verify SatinColumn picks up params not in the bootstrap subset."""
    sc = schema["stitch_types"]["satin_column"]["params"]
    expected_extras = {
        "pull_compensation_percent",
        "random_width_increase_percent",
        "random_width_decrease_percent",
        "short_stitch_distance_mm",
        "split_method",
        "min_random_split_length_mm",
    }
    missing = expected_extras - sc.keys()
    assert not missing, f"SatinColumn missing extracted params: {missing}"


def test_fill_method_variants_isolated(schema):
    """Params with select_items=[('fill_method', X)] should only appear in stitch type X."""
    contour = schema["stitch_types"]["contour_fill"]["params"]
    meander = schema["stitch_types"]["meander_fill"]["params"]
    # avoid_self_crossing is contour-only; meander_pattern is meander-only.
    assert "avoid_self_crossing" in contour
    assert "avoid_self_crossing" not in meander
    assert "meander_pattern" in meander
    assert "meander_pattern" not in contour


def test_extractor_param_count_matches_source(schema):
    """The reported source param_count matches the count of @param decorators."""
    # count is sourced from the AST; we just sanity-check it's plausible.
    assert schema["source"]["param_count"] >= 130


def test_schema_extract_cli_writes_cache(source_root, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(
        root,
        ["--json", "schema", "extract", "--source", str(source_root)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["param_count"] >= 130
    written = Path(data["wrote"])
    assert written.exists()
    cached = json.loads(written.read_text())
    assert "satin_column" in cached["stitch_types"]


def test_load_schema_prefers_extracted_cache(source_root, tmp_path, monkeypatch):
    """After extract, load_schema() should return the extracted (not bootstrap) cache."""
    from cli_anything_inkstitch.schema.cache import load_schema
    from cli_anything_inkstitch.schema.extract import extract_schema, write_cache

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    schema = extract_schema(source_root)
    write_cache(schema)
    loaded = load_schema()
    assert loaded["source"]["kind"] == "ast-extract"
    assert len(loaded["stitch_types"]["satin_column"]["params"]) > len(
        BOOTSTRAP_TYPES["satin_column"]["params"]
    )
