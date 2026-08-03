"""Persistent history checkpoints — flagged, annotated design states.

The undo history is a 50-entry ring buffer of diff patches (history.py):
old states are only reachable by replaying patches, and that chain breaks
through eviction, oversize entries, and structural drift.  Checkpoints exist
to make a state durable the moment the user cares about it:

* **Materialize at flag time, never at restore time.**  Flagging walks the
  patch chain to the requested history entry (while it still can) and writes
  the FULL SVG to a content-addressed snapshot.  From that moment the
  checkpoint depends on nothing but its file.
* **Snapshots are plain SVG files** in `.checkpoints/` next to the project —
  self-describing, openable in Inkscape, deduplicated by content hash, and
  recoverable even if the project JSON index is lost.
* **Restore is non-destructive**: it pushes a normal document_replace history
  entry, so restoring is itself undoable and flagged states act like
  lightweight branches you can hop between.

The index lives in the project JSON under "checkpoints"; each record is
{id, annotation, created, svg_sha256, history_entry_id, auto}.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lxml import etree

from cli_anything_inkstitch.errors import ProjectError, UserError
from cli_anything_inkstitch.history import (
    apply_patch,
    document_replace,
    make_entry,
    push,
)

# auto-checkpoints (taken before destructive tools) kept per project;
# user-flagged checkpoints are never pruned
AUTO_KEEP = 5

THUMB_SIZE = 220


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def checkpoint_dir(project_path: str) -> Path:
    return Path(project_path).parent / ".checkpoints"


def _records(proj) -> list[dict[str, Any]]:
    return proj.data.setdefault("checkpoints", [])


def _snapshot_path(project_path: str, sha: str) -> Path:
    return checkpoint_dir(project_path) / f"{sha[:16]}.svg"


def _thumb_path(project_path: str, sha: str) -> Path:
    return checkpoint_dir(project_path) / f"{sha[:16]}.png"


# ---- state materialization --------------------------------------------------

def materialize_state(tree, history: dict, entry_id: str | None) -> bytes:
    """Serialize the design as it was at history entry `entry_id`.

    None (or the entry at the cursor) means the current state.  Older
    entries are reached by reverse-applying patches from the cursor down to
    just after the target; redo-branch entries by forward-applying.  Raises
    UserError when the chain to that entry is broken (evicted or oversize
    entries) — that state is genuinely unreconstructable, which is exactly
    why checkpoints snapshot at flag time.
    """
    entries = history.get("entries", [])
    cursor = history.get("cursor", -1)
    if entry_id is None:
        return etree.tostring(tree.getroot())
    idx = next((i for i, e in enumerate(entries) if e["id"] == entry_id), None)
    if idx is None:
        raise UserError(
            f"history entry {entry_id} is no longer in the ring buffer — "
            "that state cannot be reconstructed (flag states while they are "
            "still in history, or flag the current state)")
    if idx == cursor:
        return etree.tostring(tree.getroot())

    # work on a copy — materialization must not disturb the live document
    copy = etree.ElementTree(etree.fromstring(etree.tostring(tree.getroot())))
    try:
        if idx < cursor:
            for i in range(cursor, idx, -1):
                apply_patch(copy, entries[i]["patch"], reverse=True)
        else:  # redo branch
            for i in range(cursor + 1, idx + 1):
                apply_patch(copy, entries[i]["patch"], reverse=False)
    except ProjectError as exc:
        raise UserError(
            f"cannot reconstruct the state at {entry_id}: {exc} — the patch "
            "chain to it is broken; flag states closer to the present") from exc
    return etree.tostring(copy.getroot())


# ---- thumbnail --------------------------------------------------------------

def render_thumbnail(svg_xml: bytes, out_path: Path,
                     size: int = THUMB_SIZE) -> bool:
    """Small raster preview of a snapshot (strokes as lines, fills as
    polygons).  Best-effort: a thumbnail failure must never block a
    checkpoint, so callers treat False as 'no thumbnail'."""
    try:
        from PIL import Image, ImageDraw
        from cli_anything_inkstitch.artifact.gate import flatten_path
        root = etree.fromstring(svg_xml)
        SVG = "{http://www.w3.org/2000/svg}"
        shapes = []
        xs: list[float] = []
        ys: list[float] = []
        for p in root.iter(f"{SVG}path"):
            pts = flatten_path(p.get("d") or "")
            if len(pts) < 2:
                continue
            fill = (p.get("fill") or "").strip().lower()
            filled = fill not in ("", "none")
            shapes.append((pts, filled))
            xs += [q[0] for q in pts]
            ys += [q[1] for q in pts]
        if not shapes:
            return False
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        span = max(w, h)
        if span <= 0:
            return False  # a single point has no renderable extent
        scale = (size - 12) / span
        ox = (size - w * scale) / 2 - min(xs) * scale
        oy = (size - h * scale) / 2 - min(ys) * scale
        img = Image.new("RGB", (size, size), (24, 24, 28))
        draw = ImageDraw.Draw(img)
        for pts, filled in shapes:
            spts = [(x * scale + ox, y * scale + oy) for x, y in pts]
            if filled and len(spts) >= 3:
                draw.polygon(spts, fill=(200, 200, 205))
            else:
                draw.line(spts, fill=(230, 230, 235), width=1)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out_path))
        return True
    except Exception:  # noqa: BLE001 — advisory artifact only
        return False


# ---- operations on an OPEN project (caller holds the lock) ------------------

def record_checkpoint(proj, tree, annotation: str,
                      history_entry_id: str | None = None,
                      auto: bool = False) -> dict[str, Any]:
    """Snapshot + index a state of an already-open project.

    Exists separately from create_checkpoint so callers already inside a
    project lock (e.g. tools taking an auto-checkpoint before a destructive
    rewrite) don't deadlock re-acquiring it.
    """
    if tree is None:
        raise UserError("project has no SVG attached")
    xml = materialize_state(tree, proj.history, history_entry_id)
    sha = hashlib.sha256(xml).hexdigest()
    snap = _snapshot_path(proj.path, sha)
    snap.parent.mkdir(parents=True, exist_ok=True)
    if not snap.exists():
        snap.write_bytes(xml)
    thumb = _thumb_path(proj.path, sha)
    has_thumb = thumb.exists() or render_thumbnail(xml, thumb)
    record = {
        "id": "cp_" + uuid.uuid4().hex[:10],
        "annotation": annotation,
        "created": _now_iso(),
        "svg_sha256": sha,
        "history_entry_id": history_entry_id,
        "auto": bool(auto),
        "thumbnail": bool(has_thumb),
    }
    _records(proj).append(record)
    if auto:
        _prune_autos(proj)
    return record


def _prune_autos(proj) -> None:
    """Keep the newest AUTO_KEEP auto-checkpoints; snapshot files are removed
    only when no surviving record (auto or user) references their hash."""
    records = _records(proj)
    autos = [r for r in records if r.get("auto")]
    for stale in autos[:-AUTO_KEEP]:
        records.remove(stale)
        if not any(r["svg_sha256"] == stale["svg_sha256"] for r in records):
            _snapshot_path(proj.path, stale["svg_sha256"]).unlink(missing_ok=True)
            _thumb_path(proj.path, stale["svg_sha256"]).unlink(missing_ok=True)


def find_checkpoint(proj, checkpoint_id: str) -> dict[str, Any]:
    for r in _records(proj):
        if r["id"] == checkpoint_id:
            return r
    raise UserError(f"no checkpoint with id {checkpoint_id}")


# ---- locked entry points ----------------------------------------------------

def _open(project_path: str, mutate: bool):
    """(context manager) lock + load project and its SVG tree.

    mutate saves the project JSON only — the SVG is written back solely by
    restore_checkpoint, so index-only operations (create/annotate/delete)
    never touch the design file or trigger editor live-reloads.
    """
    from contextlib import contextmanager
    from cli_anything_inkstitch.project import ProjectFile, project_lock
    from cli_anything_inkstitch.svg.document import load_svg

    @contextmanager
    def cm():
        with project_lock(project_path):
            proj = ProjectFile.load(project_path)
            tree = load_svg(proj.svg_path) if proj.svg_path else None
            yield proj, tree
            if mutate:
                proj.save()
    return cm()


def create_checkpoint(project_path: str, annotation: str,
                      history_entry_id: str | None = None,
                      auto: bool = False) -> dict[str, Any]:
    with _open(project_path, mutate=True) as (proj, tree):
        return record_checkpoint(proj, tree, annotation,
                                 history_entry_id, auto)


def list_checkpoints(project_path: str) -> list[dict[str, Any]]:
    with _open(project_path, mutate=False) as (proj, _tree):
        return list(_records(proj))


def annotate_checkpoint(project_path: str, checkpoint_id: str,
                        annotation: str) -> dict[str, Any]:
    with _open(project_path, mutate=True) as (proj, _tree):
        record = find_checkpoint(proj, checkpoint_id)
        record["annotation"] = annotation
        return dict(record)


def delete_checkpoint(project_path: str, checkpoint_id: str) -> dict[str, Any]:
    with _open(project_path, mutate=True) as (proj, _tree):
        record = find_checkpoint(proj, checkpoint_id)
        _records(proj).remove(record)
        if not any(r["svg_sha256"] == record["svg_sha256"]
                   for r in _records(proj)):
            _snapshot_path(proj.path, record["svg_sha256"]).unlink(missing_ok=True)
            _thumb_path(proj.path, record["svg_sha256"]).unlink(missing_ok=True)
        return {"deleted": checkpoint_id}


def restore_checkpoint(project_path: str, checkpoint_id: str) -> dict[str, Any]:
    """Swap the checkpointed SVG in as a NORMAL history entry.

    Restore never rewrites history — it is one more edit, undoable like any
    other, so flagged states behave as branches to hop between rather than
    rollback points that destroy work.
    """
    with _open(project_path, mutate=True) as (proj, tree):
        if tree is None:
            raise UserError("project has no SVG attached")
        record = find_checkpoint(proj, checkpoint_id)
        snap = _snapshot_path(proj.path, record["svg_sha256"])
        if not snap.exists():
            raise UserError(
                f"snapshot file missing: {snap} — the .checkpoints directory "
                "was moved or deleted")
        xml = snap.read_bytes()
        if hashlib.sha256(xml).hexdigest() != record["svg_sha256"]:
            raise UserError(f"snapshot {snap} does not match its recorded "
                            "hash; refusing to restore corrupted content")
        before_xml = etree.tostring(tree.getroot()).decode("utf-8")
        new_root = etree.fromstring(xml)
        push(proj.history, make_entry(
            command=f"session checkpoint restore {checkpoint_id}"
                    f" ({record.get('annotation') or 'no annotation'})",
            patch=document_replace(before_xml, xml.decode("utf-8"))))
        tree._setroot(new_root)
        from cli_anything_inkstitch.svg.document import save_svg
        proj.svg_sha256 = save_svg(tree, proj.svg_path)
        return {"restored": checkpoint_id,
                "svg_sha256": record["svg_sha256"],
                "annotation": record.get("annotation")}
