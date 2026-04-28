"""`element` command group."""

from __future__ import annotations

import secrets

import click
from lxml import etree

from cli_anything_inkstitch.commands._helpers import (
    open_project,
    record,
    require_id,
    xpath_for_id,
)
from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.history import attr_diff, node_delete
from cli_anything_inkstitch.output import emit, print_table
from cli_anything_inkstitch.svg.attrs import INKSTITCH_PREFIX, qname
from cli_anything_inkstitch.svg.document import all_addressable_elements
from cli_anything_inkstitch.svg.elements import classify, element_summary


@click.group("element")
def element():
    """Enumerate and inspect SVG elements."""


@element.command("list")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--refresh", is_flag=True, help="Rescan the SVG and rebuild cached snapshot.")
@click.option("--filter", "stitch_type_filter", default=None,
              help="Only show elements of this stitch type.")
@click.option("--with-params", is_flag=True, help="Include set_params in output.")
@click.pass_context
def list_cmd(ctx, project_path, refresh, stitch_type_filter, with_params):
    with open_project(ctx, project_path, mutate=refresh) as (proj, tree):
        if tree is None:
            raise UserError("project has no SVG attached; use `document open --svg <path>`")
        rows = []
        for elem in all_addressable_elements(tree):
            if not elem.get("id"):
                continue
            summary = element_summary(elem)
            if stitch_type_filter and summary["stitch_type"] != stitch_type_filter:
                continue
            rows.append(summary)
        if refresh:
            proj.elements.clear()
            for r in rows:
                proj.elements[r["id"]] = {
                    "tag": r["tag"],
                    "stitch_type": r["stitch_type"],
                    "set_params": r["set_params"],
                }
        payload = {"elements": rows, "count": len(rows)}
        if with_params or ctx.obj.get("json"):
            emit(ctx, payload)
        else:
            cols = ["id", "tag", "label", "stitch_type", "fill", "stroke"]
            emit(ctx, payload, human=lambda c: print_table(rows, cols))


@element.command("get")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.pass_context
def get(ctx, project_path, svg_id):
    with open_project(ctx, project_path) as (_proj, tree):
        elem = require_id(tree, svg_id)
        attrs = {k: v for k, v in elem.attrib.items()}
        result = {
            "id": svg_id,
            **element_summary(elem),
            "attributes": attrs,
        }
        emit(ctx, result)


@element.command("describe")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", default=None,
              help="Describe a single element. Omit to describe all addressable elements.")
@click.option("--neighbors/--no-neighbors", default=True,
              help="Include bbox-overlap relationships with other elements.")
@click.pass_context
def describe(ctx, project_path, svg_id, neighbors):
    """Rich, derived per-element context for AI reasoning.

    Returns geometry-derived facts that aren't on the element directly:
    bbox in mm and as % of the design, position (3x3 grid descriptor),
    aspect ratio, area %, closest named color, and (if --neighbors)
    bbox-overlap relationships with other elements.

    Use this before calling `params set` so the LLM knows what each element
    is and how it relates to its surroundings.
    """
    from cli_anything_inkstitch.svg.colors import closest_named
    from cli_anything_inkstitch.svg.geometry import (
        aspect_ratio,
        bbox_area,
        bbox_overlap,
        design_bbox_from_root,
        element_bbox,
        position_descriptor,
        px_to_mm,
    )

    with open_project(ctx, project_path) as (_proj, tree):
        root = tree.getroot()
        d_bbox = design_bbox_from_root(root)
        d_w_px = max(d_bbox[2] - d_bbox[0], 1.0)
        d_h_px = max(d_bbox[3] - d_bbox[1], 1.0)
        d_area_px = bbox_area(d_bbox) or 1.0

        # Pre-compute bbox for every addressable element so neighbor lookup
        # doesn't recompute per-element.
        all_elems: list[tuple[object, "_Bbox | None"]] = []
        for e in all_addressable_elements(tree):
            if not e.get("id"):
                continue
            try:
                bb = element_bbox(e)
            except Exception:  # noqa: BLE001
                bb = None
            all_elems.append((e, bb))

        if svg_id is not None:
            target = next(((e, bb) for e, bb in all_elems if e.get("id") == svg_id), None)
            if target is None:
                raise UserError(f"no element with id={svg_id!r} in SVG")
            description = _describe_one(
                target[0], target[1], d_bbox,
                all_elems if neighbors else [],
                px_to_mm, position_descriptor, aspect_ratio, bbox_area,
                bbox_overlap, closest_named,
                d_w_px, d_h_px, d_area_px,
            )
            emit(ctx, description)
            return

        out = []
        for e, bb in all_elems:
            out.append(_describe_one(
                e, bb, d_bbox,
                all_elems if neighbors else [],
                px_to_mm, position_descriptor, aspect_ratio, bbox_area,
                bbox_overlap, closest_named,
                d_w_px, d_h_px, d_area_px,
            ))
        emit(ctx, {
            "design_bbox_px": list(d_bbox),
            "design_size_mm": [round(px_to_mm(d_w_px), 2),
                                round(px_to_mm(d_h_px), 2)],
            "elements": out,
            "count": len(out),
        })


def _describe_one(elem, bb, d_bbox, all_elems,
                  px_to_mm, position_descriptor, aspect_ratio, bbox_area,
                  bbox_overlap, closest_named,
                  d_w_px, d_h_px, d_area_px) -> dict:
    """Build the description payload for one element. Helper kept module-local
    so the command function reads top-down."""
    summary = element_summary(elem)
    out: dict = {
        "id": elem.get("id"),
        "tag": summary["tag"],
        "stitch_type": summary["stitch_type"],
        "fill": summary["fill"],
        "stroke": summary["stroke"],
        "color_name": closest_named(summary["fill"]) if summary["fill"] else None,
    }
    if bb is None:
        out["bbox"] = None
        out["note"] = "geometry not parseable (transforms or unsupported tag)"
        return out

    w_px = bb[2] - bb[0]
    h_px = bb[3] - bb[1]
    area_px = bbox_area(bb)

    out["bbox_mm"] = [round(px_to_mm(v), 2) for v in bb]
    out["size_mm"] = [round(px_to_mm(w_px), 2), round(px_to_mm(h_px), 2)]
    out["bbox_pct_of_design"] = {
        "width": round(100.0 * w_px / d_w_px, 1),
        "height": round(100.0 * h_px / d_h_px, 1),
        "area": round(100.0 * area_px / d_area_px, 1),
    }
    out["position"] = position_descriptor(bb, d_bbox)
    ar = aspect_ratio(bb)
    out["aspect_ratio"] = round(ar, 2) if ar is not None else None

    if all_elems:
        nbrs = []
        for other, other_bb in all_elems:
            if other is elem or other_bb is None:
                continue
            rel = bbox_overlap(bb, other_bb)
            if rel:
                nbrs.append({"id": other.get("id"), "relation": rel})
        out["neighbors"] = nbrs
    return out


@element.command("identify")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.pass_context
def identify(ctx, project_path, svg_id):
    """Echo the element-class dispatch for one element."""
    with open_project(ctx, project_path) as (_proj, tree):
        elem = require_id(tree, svg_id)
        emit(ctx, {"id": svg_id, "stitch_type": classify(elem),
                   "tag": etree.QName(elem.tag).localname})


@element.command("delete")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.pass_context
def delete(ctx, project_path, svg_id):
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        elem = require_id(tree, svg_id)
        parent = elem.getparent()
        if parent is None:
            raise UserError(f"cannot delete root element")
        index = list(parent).index(elem)
        before_xml = etree.tostring(elem).decode()
        parent.remove(elem)
        proj.elements.pop(svg_id, None)
        record(proj.history, f"element delete --id {svg_id}",
               node_delete(parent_xpath=_xpath(parent), index=index, before_xml=before_xml))
        emit(ctx, {"deleted": svg_id})


@element.command("clear-params")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.option("--keep-commands", is_flag=True)
@click.pass_context
def clear_params(ctx, project_path, svg_id, keep_commands):
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        elem = require_id(tree, svg_id)
        before: dict[str, str | None] = {}
        after: dict[str, str | None] = {}
        for k, v in list(elem.attrib.items()):
            if isinstance(k, str) and k.startswith(INKSTITCH_PREFIX):
                before[k] = v
                after[k] = None
                del elem.attrib[k]
        if not keep_commands:
            for child in list(elem):
                if etree.QName(child.tag).localname == "use":
                    href = child.get(qname("href", "http://www.w3.org/1999/xlink")) or child.get("href") or ""
                    if "inkstitch_" in href:
                        elem.remove(child)
        if before:
            record(proj.history, f"element clear-params --id {svg_id}",
                   attr_diff(xpath_for_id(svg_id), before, after))
        if svg_id in proj.elements:
            proj.elements[svg_id]["stitch_type"] = classify(elem)
            proj.elements[svg_id]["set_params"] = []
        emit(ctx, {"id": svg_id, "cleared": list(before.keys())})


@element.command("clear-commands")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.pass_context
def clear_commands(ctx, project_path, svg_id):
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        elem = require_id(tree, svg_id)
        removed = []
        for child in list(elem):
            if etree.QName(child.tag).localname == "use":
                href = child.get(qname("href", "http://www.w3.org/1999/xlink")) or child.get("href") or ""
                if "inkstitch_" in href:
                    removed.append(href)
                    elem.remove(child)
        emit(ctx, {"id": svg_id, "removed": removed})


@element.command("ensure-id")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--xpath", required=True)
@click.pass_context
def ensure_id(ctx, project_path, xpath):
    with open_project(ctx, project_path, mutate=True) as (_proj, tree):
        matches = tree.getroot().xpath(xpath)
        if not matches:
            raise UserError(f"xpath matched no nodes: {xpath}")
        if len(matches) > 1:
            raise UserError(f"xpath matched {len(matches)} nodes; need a unique selector")
        elem = matches[0]
        if elem.get("id"):
            emit(ctx, {"id": elem.get("id"), "created": False})
            return
        new_id = f"el_{secrets.token_hex(4)}"
        elem.set("id", new_id)
        emit(ctx, {"id": new_id, "created": True})


def _xpath(elem) -> str:
    """Best-effort xpath to a node (used as a coarse pointer in history)."""
    if elem.get("id"):
        return f"//*[@id='{elem.get('id')}']"
    return etree.ElementTree(elem).getpath(elem)
