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
