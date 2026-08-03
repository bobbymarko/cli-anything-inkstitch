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
                _reverse_metadata(proj, patch, tree)
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
                _apply_metadata(proj, patch, tree)
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


# ---- checkpoints ------------------------------------------------------------
#
# Durable flagged states, independent of the 50-entry history ring
# (checkpoints.py documents the materialize-at-flag-time design).

@session.group("checkpoint")
def checkpoint():
    """Flag, list, annotate, and restore durable design states."""


def _resolve_project(ctx, project_path):
    from cli_anything_inkstitch.commands._helpers import get_project_path
    return get_project_path(ctx, project_path)


@checkpoint.command("create")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--annotation", "-m", default="", help="Why this state matters.")
@click.option("--at", "history_entry_id", default=None,
              help="History entry id to flag (default: the current state). "
                   "Only states still reachable through the patch chain can "
                   "be flagged — flag early, while history remembers.")
@click.pass_context
def checkpoint_create(ctx, project_path, annotation, history_entry_id):
    from cli_anything_inkstitch.checkpoints import create_checkpoint
    record = create_checkpoint(_resolve_project(ctx, project_path),
                               annotation, history_entry_id)
    emit(ctx, {"checkpoint": record})


@checkpoint.command("list")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.pass_context
def checkpoint_list(ctx, project_path):
    from cli_anything_inkstitch.checkpoints import list_checkpoints
    emit(ctx, {"checkpoints": list_checkpoints(
        _resolve_project(ctx, project_path))})


@checkpoint.command("restore")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "checkpoint_id", required=True)
@click.pass_context
def checkpoint_restore(ctx, project_path, checkpoint_id):
    """Swap the flagged state back in — recorded as a normal, undoable
    history entry (nothing is rolled back or lost)."""
    from cli_anything_inkstitch.checkpoints import restore_checkpoint
    emit(ctx, restore_checkpoint(_resolve_project(ctx, project_path),
                                 checkpoint_id))


@checkpoint.command("annotate")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "checkpoint_id", required=True)
@click.option("--annotation", "-m", required=True)
@click.pass_context
def checkpoint_annotate(ctx, project_path, checkpoint_id, annotation):
    from cli_anything_inkstitch.checkpoints import annotate_checkpoint
    emit(ctx, {"checkpoint": annotate_checkpoint(
        _resolve_project(ctx, project_path), checkpoint_id, annotation)})


@checkpoint.command("delete")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "checkpoint_id", required=True)
@click.pass_context
def checkpoint_delete(ctx, project_path, checkpoint_id):
    from cli_anything_inkstitch.checkpoints import delete_checkpoint
    emit(ctx, delete_checkpoint(_resolve_project(ctx, project_path),
                                checkpoint_id))


def _apply_metadata(proj, patch, tree=None):
    _set_session_keys(proj, patch.get("after", {}), tree)


def _reverse_metadata(proj, patch, tree=None):
    _set_session_keys(proj, patch.get("before", {}), tree)


def _set_session_keys(proj, desired: dict, tree=None) -> None:
    # None means "key absent", matching attr_diff semantics in history.py.
    for k, v in desired.items():
        if v is None:
            proj.session.pop(k, None)
        else:
            proj.session[k] = v
        if k == "thread_palette" and tree is not None:
            # set-palette writes both session AND <metadata>/<inkstitch:thread-palette>;
            # keep them in sync through undo/redo (None removes the metadata key).
            from cli_anything_inkstitch.svg.document import set_inkstitch_metadata
            set_inkstitch_metadata(tree, "thread-palette", v)
