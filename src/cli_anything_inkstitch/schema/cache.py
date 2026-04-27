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


def load_schema(version: str = "bootstrap", refresh: bool = False) -> dict:
    f = cache_file(version)
    if not refresh and f.exists():
        try:
            return json.loads(f.read_text())
        except json.JSONDecodeError:
            pass
    schema = bootstrap_schema(version=version)
    f.write_text(json.dumps(schema, indent=2))
    return schema
