"""Schema cache load/save."""

from __future__ import annotations

import json
import os
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


def load_schema(version: str | None = None, refresh: bool = False) -> dict:
    """Load schema. Prefers extracted cache; falls back to bootstrap."""
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
