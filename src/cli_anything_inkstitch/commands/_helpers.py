"""Shared helpers for command modules: context loading, save, history append."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import click
from lxml import etree

from cli_anything_inkstitch.errors import ProjectError, UserError
from cli_anything_inkstitch.history import push as push_history
from cli_anything_inkstitch.project import ProjectFile, project_lock, require_absolute
from cli_anything_inkstitch.svg.document import (
    find_by_id,
    load_svg,
    save_svg,
    sha256_of,
)


def get_project_path(ctx: click.Context, override: str | None = None) -> str:
    p = override or ctx.obj.get("project_path")
    if not p:
        raise UserError("--project is required")
    return require_absolute(p, "project")


@contextmanager
def open_project(ctx: click.Context, override: str | None = None, *, mutate: bool = False, force: bool = False):
    """Yield a (ProjectFile, tree-or-None) pair under the project lock.

    If `mutate` is True the project (and SVG, if dirty) are saved on clean exit.
    """
    path = get_project_path(ctx, override)
    with project_lock(path):
        proj = ProjectFile.load(path)
        tree = None
        if proj.svg_path:
            if not Path(proj.svg_path).exists():
                raise ProjectError(f"SVG referenced by project not found: {proj.svg_path}")
            current_sha = sha256_of(proj.svg_path)
            if proj.svg_sha256 and current_sha != proj.svg_sha256 and not force:
                raise ProjectError(
                    "SVG modified outside cli-anything-inkstitch since last command "
                    "(use --force to proceed)"
                )
            tree = load_svg(proj.svg_path)
        yield proj, tree
        if mutate:
            if tree is not None and proj.svg_path:
                proj.svg_sha256 = save_svg(tree, proj.svg_path)
            proj.save()


def require_id(tree, svg_id: str):
    elem = find_by_id(tree, svg_id)
    if elem is None:
        raise UserError(f"no element with id={svg_id!r} in SVG")
    return elem


def xpath_for_id(svg_id: str) -> str:
    return f"//*[@id='{svg_id}']"


def record(history: dict, command: str, patch: dict, scope: str = "svg") -> None:
    from cli_anything_inkstitch.history import make_entry
    push_history(history, make_entry(command=command, patch=patch, scope=scope))


def serialize_command(ctx: click.Context, group: str, name: str) -> str:
    """Best-effort reconstruction of the command string for history."""
    parts = [group, name]
    parts += [a for a in (ctx.args or []) if a]
    # Click's Context.params already has parsed values; we can stringify them
    for k, v in (ctx.params or {}).items():
        if v is None or v is False:
            continue
        flag = "--" + k.replace("_", "-")
        if v is True:
            parts.append(flag)
        elif isinstance(v, (list, tuple)):
            for item in v:
                parts.extend([flag, str(item)])
        else:
            parts.extend([flag, str(v)])
    return " ".join(parts)
