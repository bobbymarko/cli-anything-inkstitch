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


# ---------------------------------------------------------------------------
# Source resolution (env var) + bootstrap degradation visibility
# ---------------------------------------------------------------------------

def _fake_source_tree(base: Path) -> Path:
    src = base / "fake-inkstitch"
    (src / "lib" / "elements").mkdir(parents=True)
    (src / "lib" / "elements" / "element.py").write_text("# stub")
    return src


def test_find_source_via_env_var(tmp_path, monkeypatch):
    src = _fake_source_tree(tmp_path)
    monkeypatch.setenv("INKSTITCH_SOURCE", str(src))
    assert find_inkstitch_source() == src


def test_invalid_env_var_does_not_fall_through(tmp_path, monkeypatch):
    """A set-but-wrong INKSTITCH_SOURCE is an error, not silently ignored."""
    monkeypatch.setenv("INKSTITCH_SOURCE", str(tmp_path / "nope"))
    assert find_inkstitch_source() is None


def test_explicit_arg_beats_env_var(tmp_path, monkeypatch):
    src = _fake_source_tree(tmp_path)
    monkeypatch.setenv("INKSTITCH_SOURCE", str(tmp_path / "nope"))
    assert find_inkstitch_source(str(src)) == src


def test_bootstrap_schema_is_marked_and_warns():
    from cli_anything_inkstitch.schema.bootstrap import bootstrap_schema
    from cli_anything_inkstitch.schema.cache import is_bootstrap, schema_warning

    bs = bootstrap_schema()
    assert bs["source"]["kind"] == "bootstrap"
    assert is_bootstrap(bs)
    assert "bootstrap" in schema_warning(bs)
    assert schema_warning({"source": {"kind": "ast-extract"}}) is None
    # legacy cache files without a source block count as degraded too
    assert is_bootstrap({})


def test_bootstrap_warning_surfaces_in_cli_json(tmp_path, monkeypatch):
    """With no extracted cache available, --json output must flag the degraded schema."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(
        root, ["--json", "schema", "list-stitch-types"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "schema_warning" in data
    assert "bootstrap" in data["schema_warning"]


def test_extracted_schema_emits_no_warning_in_cli_json(source_root, tmp_path, monkeypatch):
    from cli_anything_inkstitch.schema.extract import extract_schema, write_cache

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    write_cache(extract_schema(source_root))
    runner = CliRunner()
    result = runner.invoke(
        root, ["--json", "schema", "list-stitch-types"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "schema_warning" not in data


class TestValidateDropdownParams:
    """Dropdowns store option INDEXES (Ink/Stitch reads them with
    get_int_param); labels are GUI-only. The validator normalizes indexes,
    exact labels, and snake_case label forms to the index string, and
    rejects anything else instead of passing it through to be silently
    ignored at stitch time."""

    SCHEMA = {"params": {"join_style": {
        "type": "dropdown", "options": ["Round", "Mitered", "Beveled"]}}}

    def _validate(self, value):
        from cli_anything_inkstitch.schema.validate import validate_param
        return validate_param(self.SCHEMA, "join_style", value)

    def test_index_accepted(self):
        assert self._validate("1") == "1"

    def test_exact_label_normalized_to_index(self):
        assert self._validate("Mitered") == "1"

    def test_snake_case_label_normalized(self):
        assert self._validate("mitered") == "1"
        assert self._validate("beveled") == "2"

    def test_invalid_value_rejected(self):
        from cli_anything_inkstitch.errors import UserError
        with pytest.raises(UserError):
            self._validate("miter")     # the historical silent-failure value

    def test_out_of_range_index_rejected(self):
        from cli_anything_inkstitch.errors import UserError
        with pytest.raises(UserError):
            self._validate("7")

    def test_no_options_passthrough(self):
        from cli_anything_inkstitch.schema.validate import validate_param
        schema = {"params": {"x": {"type": "dropdown"}}}
        assert validate_param(schema, "x", "anything") == "anything"


class TestComboOptionMining:
    """combo params (fill_method, stroke_method) store ParamOption id
    STRINGS — the engine reads them with plain get_param(), not
    get_int_param() (lib/elements/fill_stitch.py fill_method, stroke.py
    stroke_method). The decorator references a class attr
    (`options=_fill_methods`), which the extractor must resolve; and
    meander_pattern's options come from the bundled tiles directory
    (dynamic `sorted(tiles.all_tiles())` — mined from tile.json files)."""

    def test_fill_method_options_are_value_strings(self, schema):
        fm = schema["stitch_types"]["auto_fill"]["params"]["fill_method"]
        assert "contour_fill" in fm["options"]
        assert "meander_fill" in fm["options"]
        assert fm["default"] == "auto_fill"

    def test_fill_method_labels_parallel_options(self, schema):
        fm = schema["stitch_types"]["auto_fill"]["params"]["fill_method"]
        labels = fm["option_labels"]
        assert len(labels) == len(fm["options"])
        assert labels[fm["options"].index("contour_fill")] == "Contour Fill"

    def test_stroke_method_options_mined(self, schema):
        sm = schema["stitch_types"]["running_stitch"]["params"]["stroke_method"]
        assert "ripple_stitch" in sm["options"]

    def test_meander_pattern_options_from_tiles_dir(self, schema, source_root):
        if not (source_root / "tiles").is_dir():
            pytest.skip("engine checkout has no bundled tiles directory")
        mp = schema["stitch_types"]["meander_fill"]["params"]["meander_pattern"]
        assert mp["options"], "tile ids should be mined from tiles/*/tile.json"
        # engine default is min(all_tiles()).id — first tile sorted by name
        assert mp["default"] == mp["options"][0]


class TestValidateComboParams:
    """combo values are stored verbatim and silently ignored by the engine
    when unknown (get_param falls back to the getter default), so the
    validator accepts a known id or GUI label and rejects everything else —
    including indexes, which are the DROPDOWN convention, not combo's."""

    SCHEMA = {"params": {"fill_method": {
        "type": "combo",
        "options": ["auto_fill", "contour_fill", "meander_fill"],
        "option_labels": ["Auto Fill", "Contour Fill", "Meander Fill"]}}}

    def _validate(self, value):
        from cli_anything_inkstitch.schema.validate import validate_param
        return validate_param(self.SCHEMA, "fill_method", value)

    def test_id_accepted_verbatim(self):
        assert self._validate("contour_fill") == "contour_fill"

    def test_label_normalized_to_id(self):
        assert self._validate("Contour Fill") == "contour_fill"
        assert self._validate("meander fill") == "meander_fill"

    def test_index_rejected(self):
        from cli_anything_inkstitch.errors import UserError
        with pytest.raises(UserError):
            self._validate("1")     # index-writing: the dropdown bug's twin

    def test_unknown_value_rejected(self):
        from cli_anything_inkstitch.errors import UserError
        with pytest.raises(UserError):
            self._validate("contour")

    def test_no_options_passthrough(self):
        from cli_anything_inkstitch.schema.validate import validate_param
        schema = {"params": {"x": {"type": "combo"}}}
        assert validate_param(schema, "x", "anything") == "anything"


class TestEngineReadContract:
    """Cross-check every extracted param against the getter the engine
    actually reads it with (mined from the same inkstitch source). This is
    the regression net for the join_style class of bug: a GUI-declared type
    that disagrees with the engine's read side must be represented by
    value_kind, or validation will either reject valid values or silently
    write ignored ones."""

    # (declared type, engine value_kind) pairs that are correct WITHOUT any
    # special handling in validate_param
    COMPATIBLE = {
        ("string", "string"), ("str", "string"), ("combo", "string"),
        ("random_seed", "string"), ("boolean", "boolean"),
        ("float", "float"), ("int", "int"),
        ("dropdown", "int"),          # dropdowns store option indexes
    }
    # kinds validate_param has dedicated handling for
    HANDLED_KINDS = {"multi_float", "multi_int", "json"}

    def test_no_unhandled_type_mismatches(self):
        import pathlib
        source = pathlib.Path(__file__).parent.parent / "inkstitch"
        if not (source / "lib" / "elements").exists():
            pytest.skip("inkstitch source checkout not present")
        from cli_anything_inkstitch.schema.extract import extract_schema
        schema = extract_schema(source)
        problems = []
        for stype, spec in schema["stitch_types"].items():
            for name, p in (spec.get("params") or {}).items():
                kind = p.get("value_kind")
                if kind is None:
                    continue          # getter not statically resolvable
                ptype = p.get("type", "string")
                if (ptype, kind) in self.COMPATIBLE:
                    continue
                if kind in self.HANDLED_KINDS:
                    continue
                if kind == "float" and ptype == "int":
                    continue          # validator widens int→float
                if kind == "string" and ptype in ("float", "int"):
                    continue          # validator treats as numeric list
                problems.append(f"{stype}.{name}: declared {ptype}, engine reads {kind}")
        assert not problems, (
            "params whose declared type disagrees with the engine's read "
            "contract and have no validator handling:\n  " + "\n  ".join(problems))


class TestValidateMultiValueParams:
    """Engine-read-contract handling: multi-value params accept
    space/comma-separated lists; numeric-declared-but-string-read params
    (fill_underlay_angle) accept angle lists; int-declared float-read
    params (staggers) accept floats."""

    def _validate(self, spec, value):
        from cli_anything_inkstitch.schema.validate import validate_param
        return validate_param({"params": {"p": spec}}, "p", value)

    def test_multi_float_list(self):
        spec = {"type": "float", "value_kind": "multi_float"}
        assert self._validate(spec, "10 20") == "10 20"
        assert self._validate(spec, "10, 20") == "10 20"
        assert self._validate(spec, "1.5") == "1.5"

    def test_multi_float_rejects_garbage(self):
        from cli_anything_inkstitch.errors import UserError
        spec = {"type": "float", "value_kind": "multi_float"}
        with pytest.raises(UserError):
            self._validate(spec, "10 abc")

    def test_multi_float_range_applies_per_element(self):
        from cli_anything_inkstitch.errors import UserError
        spec = {"type": "float", "value_kind": "multi_float", "min": 0.0}
        with pytest.raises(UserError):
            self._validate(spec, "1 -5")

    def test_multi_int_list(self):
        spec = {"type": "str", "value_kind": "multi_int"}
        assert self._validate(spec, "0 1 2") == "0 1 2"

    def test_numeric_declared_string_read(self):
        # fill_underlay_angle: declared float, engine reads a string angle list
        spec = {"type": "float", "value_kind": "string"}
        assert self._validate(spec, "45 135") == "45 135"

    def test_int_declared_float_read_widens(self):
        # staggers: declared int, engine reads float
        spec = {"type": "int", "value_kind": "float"}
        assert self._validate(spec, "2.5") == "2.5"


def test_integer_type_spelling_normalized():
    # newer engine source declares some int params as type='integer'
    # (smoothness_mm) — both spellings must normalize to 'int' so the
    # int-declared/float-read validator handling applies
    from cli_anything_inkstitch.schema.extract import _normalize_param
    out = _normalize_param({"name": "smoothness_mm", "type": "integer",
                            "value_kind": "float"})
    assert out["type"] == "int"
