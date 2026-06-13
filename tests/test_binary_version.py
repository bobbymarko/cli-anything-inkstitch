"""Tests for binary version detection and schema↔binary coherence warnings."""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from cli_anything_inkstitch.binary import detect_binary_version
from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.schema.cache import load_schema, schema_warning


# ---- detect_binary_version ------------------------------------------------

def test_detect_version_macos_bundle_layout(tmp_path):
    macos = tmp_path / "Ink Stitch.app" / "Contents" / "MacOS"
    resources = tmp_path / "Ink Stitch.app" / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    binary = macos / "inkstitch"
    binary.write_text("#!/bin/sh\n")
    (resources / "VERSION").write_text("v3.2.2\n")
    assert detect_binary_version(str(binary)) == "3.2.2"


def test_detect_version_next_to_binary(tmp_path):
    binary = tmp_path / "inkstitch.exe"
    binary.write_text("")
    (tmp_path / "VERSION").write_text("3.0.1")
    assert detect_binary_version(str(binary)) == "3.0.1"


def test_detect_version_parent_dir(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    binary = bindir / "inkstitch"
    binary.write_text("")
    (tmp_path / "VERSION").write_text("3.1.0\n")
    assert detect_binary_version(str(binary)) == "3.1.0"


def test_detect_version_missing_returns_none(tmp_path):
    binary = tmp_path / "inkstitch"
    binary.write_text("")
    assert detect_binary_version(str(binary)) is None        # no VERSION anywhere
    assert detect_binary_version(str(tmp_path / "nope")) is None  # binary absent
    assert detect_binary_version(None) is None


# ---- schema_warning version coherence ---------------------------------------

def _extracted_schema(version: str) -> dict:
    return {
        "inkstitch_version": version,
        "stitch_types": {"auto_fill": {"params": {}}},
        "commands": [],
        "source": {"kind": "ast-extract"},
    }


def test_schema_warning_on_version_mismatch():
    w = schema_warning(_extracted_schema("3.0.0"), binary_version="3.2.2")
    assert w is not None
    assert "3.0.0" in w and "3.2.2" in w


def test_schema_warning_none_when_versions_match():
    assert schema_warning(_extracted_schema("3.2.2"), binary_version="3.2.2") is None
    # leading 'v' on either side is normalized away
    assert schema_warning(_extracted_schema("v3.2.2"), binary_version="3.2.2") is None


def test_schema_warning_skips_unreleased_schema_versions():
    # src-<hash> versions (dev clone without a VERSION file) can't be compared
    assert schema_warning(_extracted_schema("src-ab12cd34ef"), binary_version="3.2.2") is None


def test_schema_warning_none_without_binary_version():
    assert schema_warning(_extracted_schema("3.0.0")) is None
    assert schema_warning(_extracted_schema("3.0.0"), binary_version=None) is None


def test_bootstrap_warning_takes_priority_over_mismatch():
    bootstrap = {"source": {"kind": "bootstrap"}, "inkstitch_version": "bootstrap"}
    w = schema_warning(bootstrap, binary_version="3.2.2")
    assert "bootstrap" in w


# ---- load_schema prefer_version ---------------------------------------------

def test_load_schema_prefers_binary_matching_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache = tmp_path / "cli-anything-inkstitch"
    cache.mkdir()
    old = cache / "schema-1.0.0.json"
    new = cache / "schema-2.0.0.json"
    old.write_text(json.dumps(_extracted_schema("1.0.0")))
    new.write_text(json.dumps(_extracted_schema("2.0.0")))
    # make 2.0.0 unambiguously newer by mtime
    os.utime(old, (1_000_000_000, 1_000_000_000))
    os.utime(new, (2_000_000_000, 2_000_000_000))

    # default: newest by mtime wins
    assert load_schema()["inkstitch_version"] == "2.0.0"
    # binary version known: matching cache wins over newer mtime
    assert load_schema(prefer_version="1.0.0")["inkstitch_version"] == "1.0.0"
    # no matching cache: silently falls back to newest
    assert load_schema(prefer_version="9.9.9")["inkstitch_version"] == "2.0.0"


# ---- end-to-end: mismatch surfaces in --json output --------------------------

def test_cli_surfaces_version_mismatch_warning(tmp_path, monkeypatch):
    # extracted-style cache from "1.0.0"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache = tmp_path / "cli-anything-inkstitch"
    cache.mkdir()
    (cache / "schema-1.0.0.json").write_text(json.dumps(_extracted_schema("1.0.0")))

    # fake installed binary reporting 9.9.9
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    binary = bindir / "inkstitch"
    binary.write_text("")
    (bindir / "VERSION").write_text("9.9.9\n")
    monkeypatch.setenv("INKSTITCH_BINARY", str(binary))

    result = CliRunner().invoke(
        root, ["--json", "schema", "list-stitch-types"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "schema_warning" in data
    assert "1.0.0" in data["schema_warning"] and "9.9.9" in data["schema_warning"]


def test_cli_no_warning_when_binary_matches_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache = tmp_path / "cli-anything-inkstitch"
    cache.mkdir()
    (cache / "schema-9.9.9.json").write_text(json.dumps(_extracted_schema("9.9.9")))

    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    binary = bindir / "inkstitch"
    binary.write_text("")
    (bindir / "VERSION").write_text("9.9.9\n")
    monkeypatch.setenv("INKSTITCH_BINARY", str(binary))

    result = CliRunner().invoke(
        root, ["--json", "schema", "list-stitch-types"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "schema_warning" not in data
