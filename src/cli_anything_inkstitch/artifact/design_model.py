"""Design model — the shared state the artifact loop edits.

Read side: project SVG → editor JSON (objects with stable element ids, kind
classification, satin rail/rung decomposition, fill start/end handles).

Write side: edit ops → SVG mutations routed through the same primitives the
CLI commands use (project lock, SHA-256 coherence, history patches), so undo
and external-edit detection keep working and the binary's per-element stitch
plan cache keys stay stable.

No Click in this module (repo convention: pure logic outside commands/).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lxml import etree

from cli_anything_inkstitch.errors import ProjectError, UserError
from cli_anything_inkstitch.history import attr_diff, make_entry, node_delete, node_insert, push
from cli_anything_inkstitch.project import ProjectFile, project_lock
from cli_anything_inkstitch.svg.attrs import (
    SVG_NS,
    XLINK_NS,
    iter_inkstitch_attrs,
    qname,
)
from cli_anything_inkstitch.svg.document import (
    find_by_id,
    load_svg,
    save_svg,
    sha256_of,
)
from cli_anything_inkstitch.svg.elements import classify, element_summary

# stitch_plan_preview args mirroring `preview generate` defaults
_PREVIEW_ARGS = {
    "render-mode": "simple",
    "needle-points": "false",
    "visual-commands": "false",
    "render-jumps": "true",
    "insensitive": "false",
}


@contextmanager
def _open_locked(project_file: str, *, mutate: bool = False):
    """Non-Click twin of commands._helpers.open_project: lock, load, SHA-check,
    yield (proj, tree), save on clean exit when mutating."""
    with project_lock(project_file):
        proj = ProjectFile.load(project_file)
        if not proj.svg_path:
            raise ProjectError("project has no SVG attached (run `document open` first)")
        if not Path(proj.svg_path).exists():
            raise ProjectError(f"SVG referenced by project not found: {proj.svg_path}")
        current_sha = sha256_of(proj.svg_path)
        if proj.svg_sha256 and current_sha != proj.svg_sha256:
            raise ProjectError(
                "SVG modified outside cli-anything-inkstitch since last command "
                "(re-open with `document open --force` to resync)"
            )
        tree = load_svg(proj.svg_path)
        yield proj, tree
        if mutate:
            proj.svg_sha256 = save_svg(tree, proj.svg_path)
            proj.save()


# -- read side ----------------------------------------------------------------

_SUBPATH_SPLIT = re.compile(r"(?=[Mm])")


def split_subpaths(d: str) -> list[str]:
    """Split a path's `d` into subpath strings (each starting with M/m)."""
    return [s.strip() for s in _SUBPATH_SPLIT.split(d or "") if s.strip()]


def _kind_for(stitch_type: str) -> str:
    if stitch_type == "satin_column":
        return "satin"
    if stitch_type.endswith("_fill") or stitch_type == "auto_fill":
        return "fill"
    if stitch_type in ("running_stitch", "ripple_stitch", "zigzag_stitch", "bean_stitch",
                       "manual_stitch"):
        return "run"
    return "other"


def _command_uses(elem) -> list[dict[str, Any]]:
    """Visual-command <use> children of an element (fill_start, fill_end, ...)."""
    out = []
    for child in elem.findall(f"{{{SVG_NS}}}use"):
        href = child.get(f"{{{XLINK_NS}}}href") or child.get("href") or ""
        if "inkstitch_" in href:
            out.append({
                "use_id": child.get("id"),
                "command": href.split("#")[-1].removeprefix("inkstitch_"),
                "x": float(child.get("x") or 0),
                "y": float(child.get("y") or 0),
            })
    return out


def _is_command_use(elem) -> bool:
    if etree.QName(elem.tag).localname != "use":
        return False
    href = elem.get(f"{{{XLINK_NS}}}href") or elem.get("href") or ""
    return "inkstitch_" in href


def read_design(project_file: str) -> dict[str, Any]:
    """The design as editor JSON. Read-only (no lock held after return)."""
    with _open_locked(project_file) as (proj, tree):
        root = tree.getroot()
        objects: list[dict[str, Any]] = []
        for elem in root.iter():
            if not isinstance(elem.tag, str):
                continue
            local = etree.QName(elem.tag).localname
            if local not in ("path", "rect", "circle", "ellipse", "line", "polygon", "polyline"):
                continue
            if _is_command_use(elem) or not elem.get("id"):
                continue
            summary = element_summary(elem)
            stitch_type = summary["stitch_type"]
            kind = _kind_for(stitch_type)
            obj: dict[str, Any] = {
                "id": elem.get("id"),
                "kind": kind,
                "stitch_type": stitch_type,
                "tag": local,
                "d": elem.get("d"),
                "fill": summary["fill"],
                "stroke": summary["stroke"],
                "params": dict(iter_inkstitch_attrs(elem)),
                "commands": _command_uses(elem),
            }
            if kind == "satin" and elem.get("d"):
                subpaths = split_subpaths(elem.get("d"))
                obj["rails"] = subpaths[:2]
                obj["rungs"] = subpaths[2:]
            if kind == "fill":
                for cmd in obj["commands"]:
                    if cmd["command"] == "fill_start":
                        obj["start"] = {"x": cmd["x"], "y": cmd["y"], "use_id": cmd["use_id"]}
                    elif cmd["command"] == "fill_end":
                        obj["end"] = {"x": cmd["x"], "y": cmd["y"], "use_id": cmd["use_id"]}
            objects.append(obj)
        return {
            "svg_path": proj.svg_path,
            "width": root.get("width"),
            "height": root.get("height"),
            "viewBox": root.get("viewBox"),
            "objects": objects,
        }


# -- write side ---------------------------------------------------------------

def _find_use(tree, use_id: str):
    elem = find_by_id(tree, use_id)
    if elem is None or etree.QName(elem.tag).localname != "use":
        raise UserError(f"no <use> command with id={use_id!r}")
    return elem


def _xpath(svg_id: str) -> str:
    return f"//*[@id='{svg_id}']"


def _apply_one(proj: ProjectFile, tree, op: dict[str, Any]) -> dict[str, Any]:
    name = op.get("op")
    if name == "set_attr":
        elem = find_by_id(tree, op["id"])
        if elem is None:
            raise UserError(f"no element with id={op['id']!r}")
        key = qname(str(op["name"]))
        before = {key: elem.get(key)}
        elem.set(key, str(op["value"]))
        push(proj.history, make_entry(
            command=f"artifact edit set_attr --id {op['id']} {op['name']}={op['value']}",
            patch=attr_diff(_xpath(op["id"]), before, {key: elem.get(key)})))
        return {"op": name, "id": op["id"], "name": op["name"]}

    if name == "del_attr":
        elem = find_by_id(tree, op["id"])
        if elem is None:
            raise UserError(f"no element with id={op['id']!r}")
        key = qname(str(op["name"]))
        before = {key: elem.get(key)}
        if key in elem.attrib:
            del elem.attrib[key]
        push(proj.history, make_entry(
            command=f"artifact edit del_attr --id {op['id']} {op['name']}",
            patch=attr_diff(_xpath(op["id"]), before, {key: None})))
        return {"op": name, "id": op["id"], "name": op["name"]}

    if name == "set_path":
        elem = find_by_id(tree, op["id"])
        if elem is None:
            raise UserError(f"no element with id={op['id']!r}")
        before = {"d": elem.get("d")}
        elem.set("d", str(op["d"]))
        push(proj.history, make_entry(
            command=f"artifact edit set_path --id {op['id']}",
            patch=attr_diff(_xpath(op["id"]), before, {"d": elem.get("d")})))
        return {"op": name, "id": op["id"]}

    if name == "move_command":
        use = _find_use(tree, op["use_id"])
        before = {"x": use.get("x"), "y": use.get("y")}
        use.set("x", str(op["x"]))
        use.set("y", str(op["y"]))
        push(proj.history, make_entry(
            command=f"artifact edit move_command --use-id {op['use_id']}",
            patch=attr_diff(_xpath(op["use_id"]), before,
                            {"x": use.get("x"), "y": use.get("y")})))
        return {"op": name, "use_id": op["use_id"]}

    if name == "attach_command":
        import secrets
        elem = find_by_id(tree, op["id"])
        if elem is None:
            raise UserError(f"no element with id={op['id']!r}")
        use = etree.SubElement(elem, f"{{{SVG_NS}}}use")
        use.set(f"{{{XLINK_NS}}}href", f"#inkstitch_{op['command']}")
        use.set("id", f"use_{secrets.token_hex(3)}")
        if op.get("x") is not None:
            use.set("x", str(op["x"]))
        if op.get("y") is not None:
            use.set("y", str(op["y"]))
        index = list(elem).index(use)
        push(proj.history, make_entry(
            command=f"artifact edit attach_command --id {op['id']} --command {op['command']}",
            patch=node_insert(parent_xpath=_xpath(op["id"]), index=index,
                              after_xml=etree.tostring(use).decode())))
        return {"op": name, "id": op["id"], "use_id": use.get("id")}

    if name == "detach_command":
        use = _find_use(tree, op["use_id"])
        parent = use.getparent()
        index = list(parent).index(use)
        before_xml = etree.tostring(use).decode()
        parent_id = parent.get("id")
        parent.remove(use)
        push(proj.history, make_entry(
            command=f"artifact edit detach_command --use-id {op['use_id']}",
            patch=node_delete(parent_xpath=_xpath(parent_id), index=index,
                              before_xml=before_xml)))
        return {"op": name, "use_id": op["use_id"]}

    raise UserError(f"unknown edit op: {name!r}")


def apply_edits(project_file: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply edit ops in order under one lock/load/save cycle.

    Each op records its own history entry, so per-batch undo works with the
    existing `session undo` machinery. All-or-nothing: an invalid op aborts
    the batch before anything is written to disk.
    """
    if not ops:
        return {"applied": 0}
    results = []
    with _open_locked(project_file, mutate=True) as (proj, tree):
        for op in ops:
            results.append(_apply_one(proj, tree, op))
    proj_after = ProjectFile.load(project_file)
    return {"applied": len(results), "results": results,
            "svg_sha256": proj_after.svg_sha256}


# -- Tier-2: authoritative stitch plan ------------------------------------------

def ensure_prepped(project_file: str) -> bool:
    """Guarantee the on-disk SVG carries inkstitch_svg_version metadata.

    The compiled binary blocks headless invocations with a GUI dialog on
    unversioned SVGs, so the binary must never see one (spec §6a finding 3).
    load_svg stamps the marker in memory; this persists it when missing.
    Returns True if the file was modified.
    """
    with _open_locked(project_file) as (proj, _tree):
        raw = Path(proj.svg_path).read_text(encoding="utf-8", errors="replace")
        if "inkstitch_svg_version" in raw:
            return False
    with _open_locked(project_file, mutate=True):
        pass  # load_svg stamped the marker; mutate=True persists it
    return True


def stitch_plan_svg(project_file: str, *, binary_override: str | None = None,
                    ids: list[str] | None = None) -> bytes:
    """Authoritative Tier-2 render: the binary's stitch-plan SVG for the design."""
    from cli_anything_inkstitch.binary import require, run_extension

    ensure_prepped(project_file)
    with _open_locked(project_file) as (proj, _tree):
        binary = require(binary_override, proj.session)
        svg_path = proj.svg_path
        return run_extension(binary, "stitch_plan_preview", svg_path,
                             args=_PREVIEW_ARGS, ids=list(ids or []),
                             capture_stdout=True) or b""
