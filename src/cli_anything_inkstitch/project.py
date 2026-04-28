"""Project file (.inkstitch-cli.json) — load, save, locking, schema versioning."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from cli_anything_inkstitch.errors import ProjectError, UserError

SCHEMA_VERSION = 1
HISTORY_LIMIT = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_project() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "svg_path": None,
        "svg_sha256": None,
        "session": {
            "hoop": {"width_mm": 100.0, "height_mm": 100.0, "name": "100x100"},
            "units": "mm",
            "machine_target": "dst",
            "thread_palette": None,
            "collapse_len_mm": 3.0,
            "min_stitch_len_mm": 0.1,
            "inkstitch_binary": None,
            "context": {},
        },
        "elements": {},
        "history": {"cursor": -1, "entries": []},
    }


def require_absolute(path: str, label: str = "path") -> str:
    p = Path(path)
    if not p.is_absolute():
        raise UserError(f"{label} must be absolute: {path}")
    return str(p)


class ProjectFile:
    def __init__(self, path: str, data: dict[str, Any]):
        self.path = path
        self.data = data

    @classmethod
    def load(cls, path: str) -> "ProjectFile":
        require_absolute(path, "project")
        p = Path(path)
        if not p.exists():
            raise ProjectError(f"project not found: {path}")
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            raise ProjectError(f"failed to parse project JSON: {e}") from e
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ProjectError(
                f"unsupported project schema_version {data.get('schema_version')}; expected {SCHEMA_VERSION}"
            )
        return cls(path=str(p), data=data)

    @classmethod
    def load_or_create(cls, path: str) -> tuple["ProjectFile", bool]:
        require_absolute(path, "project")
        p = Path(path)
        if p.exists():
            return cls.load(path), False
        proj = cls(path=str(p), data=_empty_project())
        return proj, True

    def save(self) -> None:
        self.data["updated_at"] = _now_iso()
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=False))
        os.replace(tmp, p)

    # convenience accessors
    @property
    def svg_path(self) -> str | None:
        return self.data.get("svg_path")

    @svg_path.setter
    def svg_path(self, value: str) -> None:
        self.data["svg_path"] = value

    @property
    def svg_sha256(self) -> str | None:
        return self.data.get("svg_sha256")

    @svg_sha256.setter
    def svg_sha256(self, value: str) -> None:
        self.data["svg_sha256"] = value

    @property
    def session(self) -> dict[str, Any]:
        return self.data["session"]

    @property
    def elements(self) -> dict[str, Any]:
        return self.data["elements"]

    @property
    def history(self) -> dict[str, Any]:
        return self.data["history"]


@contextmanager
def project_lock(project_path: str):
    """Hold a file lock on the project for the duration of a command."""
    require_absolute(project_path, "project")
    lock_path = project_path + ".lock"
    lock = FileLock(lock_path, timeout=30)
    with lock:
        yield
