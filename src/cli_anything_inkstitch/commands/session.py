"""`session` command group."""

from __future__ import annotations

import click
from lxml import etree

from cli_anything_inkstitch.commands._helpers import open_project
from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.history import (
    apply_patch,
    can_redo,
    can_undo,
    peek_redo,
    peek_undo,
)
from cli_anything_inkstitch.output import emit


@click.group("session")
def session():
    """Undo / redo / history."""


@session.command("status")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.pass_context
def status(ctx, project_path):
    with open_project(ctx, project_path) as (proj, _tree):
        h = proj.history
        emit(ctx, {
            "project": proj.path,
            "svg": proj.svg_path,
            "svg_sha256": proj.svg_sha256,
            "history_cursor": h.get("cursor", -1),
            "history_size": len(h.get("entries", [])),
            "can_undo": can_undo(h),
            "can_redo": can_redo(h),
        })


@session.command("undo")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--steps", "steps", type=int, default=1)
@click.pass_context
def undo(ctx, project_path, steps):
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        applied = []
        for _ in range(steps):
            entry = peek_undo(proj.history)
            if entry is None:
                break
            patch = entry["patch"]
            if patch["type"] == "metadata_diff":
                _reverse_metadata(proj, patch)
            else:
                if tree is None:
                    raise UserError("project has no SVG to undo against")
                apply_patch(tree, patch, reverse=True)
            proj.history["cursor"] -= 1
            applied.append(entry["id"])
        emit(ctx, {"undone": applied, "cursor": proj.history["cursor"]})


@session.command("redo")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--steps", "steps", type=int, default=1)
@click.pass_context
def redo(ctx, project_path, steps):
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        applied = []
        for _ in range(steps):
            entry = peek_redo(proj.history)
            if entry is None:
                break
            patch = entry["patch"]
            if patch["type"] == "metadata_diff":
                _apply_metadata(proj, patch)
            else:
                if tree is None:
                    raise UserError("project has no SVG to redo against")
                apply_patch(tree, patch, reverse=False)
            proj.history["cursor"] += 1
            applied.append(entry["id"])
        emit(ctx, {"redone": applied, "cursor": proj.history["cursor"]})


@session.command("history")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--limit", type=int, default=20)
@click.pass_context
def history(ctx, project_path, limit):
    with open_project(ctx, project_path) as (proj, _tree):
        entries = proj.history.get("entries", [])
        cursor = proj.history.get("cursor", -1)
        recent = entries[-limit:] if limit > 0 else entries
        out = []
        for i, e in enumerate(recent):
            real_i = len(entries) - len(recent) + i
            out.append({
                "index": real_i,
                "id": e["id"],
                "ts": e["ts"],
                "command": e["command"],
                "scope": e.get("scope", "svg"),
                "patch_type": e["patch"]["type"],
                "current": real_i == cursor,
            })
        emit(ctx, {"entries": out, "cursor": cursor, "total": len(entries)})


@session.command("reset")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.pass_context
def reset(ctx, project_path):
    with open_project(ctx, project_path, mutate=True) as (proj, _tree):
        proj.history["entries"] = []
        proj.history["cursor"] = -1
        emit(ctx, {"reset": True})


def _apply_metadata(proj, patch):
    after = patch.get("after", {})
    for k, v in after.items():
        proj.session[k] = v


def _reverse_metadata(proj, patch):
    before = patch.get("before", {})
    for k, v in before.items():
        proj.session[k] = v
