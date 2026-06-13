"""Schema cache load/save."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from cli_anything_inkstitch.schema.bootstrap import bootstrap_schema


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    p = Path(base) / "cli-anything-inkstitch"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_file(version: str = "bootstrap") -> Path:
    return cache_dir() / f"schema-{version}.json"


def latest_extracted_cache() -> Path | None:
    """Return the most-recent non-bootstrap cache file, or None if missing."""
    candidates = [
        p for p in cache_dir().glob("schema-*.json") if p.name != "schema-bootstrap.json"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_schema(version: str | None = None, refresh: bool = False,
                prefer_version: str | None = None) -> dict:
    """Load schema. Prefers extracted cache; falls back to bootstrap.

    `prefer_version` (typically the installed binary's version) selects a
    matching extracted cache over the newest-by-mtime one when both exist;
    if no matching cache exists it falls through silently.
    """
    if refresh:
        try:
            from cli_anything_inkstitch.schema.extract import extract_schema, write_cache
            schema = extract_schema()
            write_cache(schema)
            return schema
        except (FileNotFoundError, RuntimeError):
            pass

    if version:
        f = cache_file(version)
        if f.exists():
            try:
                return json.loads(f.read_text())
            except json.JSONDecodeError:
                pass
    else:
        if prefer_version:
            f = cache_file(prefer_version)
            if f.exists():
                try:
                    return json.loads(f.read_text())
                except json.JSONDecodeError:
                    pass
        latest = latest_extracted_cache()
        if latest is not None:
            try:
                return json.loads(latest.read_text())
            except json.JSONDecodeError:
                pass

    v = version or "bootstrap"
    schema = bootstrap_schema(version=v)
    cache_file(v).write_text(json.dumps(schema, indent=2))
    return schema


def is_bootstrap(schema: dict) -> bool:
    """True when the schema is the hand-written fallback, not mined from source."""
    return schema.get("source", {}).get("kind") != "ast-extract"


def _release_like(v: str | None) -> bool:
    """True for version strings like '3.2.2' (vs 'src-<hash>' or 'bootstrap')."""
    return bool(re.match(r"^v?\d+(\.\d+)*", v or ""))


def schema_warning(schema: dict, binary_version: str | None = None) -> str | None:
    """Warning string for degraded or mismatched schemas; None when healthy.

    Two conditions, in priority order:
    - bootstrap schema in use (param coverage incomplete);
    - schema extracted from one Ink/Stitch release while the installed binary
      is another (only when both versions are release-like — a 'src-<hash>'
      schema from a dev clone can't be meaningfully compared).

    Commands that validate or enumerate params should surface this in their
    emitted payload so --json consumers know validation coverage is partial.
    """
    if is_bootstrap(schema):
        return (
            "using built-in bootstrap schema (core stitch types only; param "
            "coverage is incomplete). For the full schema, make Ink/Stitch "
            "source available (sibling clone, INKSTITCH_SOURCE env var, or "
            "an Ink/Stitch install) and run: schema extract"
        )
    if binary_version:
        sv = str(schema.get("inkstitch_version") or "").strip().lstrip("v")
        bv = str(binary_version).strip().lstrip("v")
        if _release_like(sv) and _release_like(bv) and sv != bv:
            return (
                f"schema was extracted from Ink/Stitch {sv} but the installed "
                f"binary is {bv}; param validation may not match what the "
                "binary accepts. Re-extract with `schema extract` against "
                "matching source (or pass --refresh-schema)."
            )
    return None
