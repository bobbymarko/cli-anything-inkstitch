"""`commands` command group — visual command attach/detach (lxml only)."""

from __future__ import annotations

import secrets

import click
from lxml import etree

from cli_anything_inkstitch.commands._helpers import (
    open_project,
    record,
    require_id,
)
from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.history import node_delete, node_insert
from cli_anything_inkstitch.output import emit
from cli_anything_inkstitch.schema.cache import load_schema
from cli_anything_inkstitch.svg.attrs import SVG_NS, XLINK_NS


@click.group("commands")
def commands_group():
    """Attach and detach Ink/Stitch visual commands."""


@commands_group.command("list")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", default=None)
@click.pass_context
def list_cmd(ctx, project_path, svg_id):
    with open_project(ctx, project_path) as (_proj, tree):
        rows = []
        targets = [require_id(tree, svg_id)] if svg_id else _all_with_use(tree)
        for elem in targets:
            for child in elem.findall(f"{{{SVG_NS}}}use"):
                href = child.get(f"{{{XLINK_NS}}}href") or child.get("href") or ""
                if "inkstitch_" in href:
                    name = href.split("#")[-1].removeprefix("inkstitch_")
                    rows.append({"id": elem.get("id"), "command": name})
        emit(ctx, {"commands": rows, "count": len(rows)})


@commands_group.command("attach")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.option("--command", "command_name", required=True)
@click.option("--at-x", "at_x", type=float, default=None)
@click.option("--at-y", "at_y", type=float, default=None)
@click.pass_context
def attach(ctx, project_path, svg_id, command_name, at_x, at_y):
    schema = load_schema()
    valid = {c["name"] for c in schema["commands"]}
    if command_name not in valid:
        raise UserError(f"unknown command '{command_name}' (known: {sorted(valid)})")
    href = f"#inkstitch_{command_name}"
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        elem = require_id(tree, svg_id)
        # ensure the symbol definition exists (best-effort: harness doesn't ship the actual SVG paths,
        # but inkstitch creates them on first export. We just insert the <use>.)
        use = etree.SubElement(elem, f"{{{SVG_NS}}}use")
        use.set(f"{{{XLINK_NS}}}href", href)
        use.set("id", f"use_{secrets.token_hex(3)}")
        if at_x is not None:
            use.set("x", str(at_x))
        if at_y is not None:
            use.set("y", str(at_y))
        index = list(elem).index(use)
        record(proj.history, f"commands attach --id {svg_id} --command {command_name}",
               node_insert(parent_xpath=f"//*[@id='{svg_id}']", index=index,
                            after_xml=etree.tostring(use).decode()))
        emit(ctx, {"id": svg_id, "attached": command_name, "use_id": use.get("id")})


@commands_group.command("detach")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.option("--command", "command_name", required=True)
@click.pass_context
def detach(ctx, project_path, svg_id, command_name):
    href_suffix = f"#inkstitch_{command_name}"
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        elem = require_id(tree, svg_id)
        removed = []
        for child in list(elem.findall(f"{{{SVG_NS}}}use")):
            href = child.get(f"{{{XLINK_NS}}}href") or child.get("href") or ""
            if href.endswith(href_suffix):
                index = list(elem).index(child)
                before_xml = etree.tostring(child).decode()
                elem.remove(child)
                record(proj.history, f"commands detach --id {svg_id} --command {command_name}",
                       node_delete(parent_xpath=f"//*[@id='{svg_id}']", index=index, before_xml=before_xml))
                removed.append(child.get("id"))
        emit(ctx, {"id": svg_id, "detached": command_name, "use_ids_removed": removed})


@commands_group.command("list-types")
@click.pass_context
def list_types(ctx):
    schema = load_schema()
    emit(ctx, {"commands": schema["commands"]})


def _all_with_use(tree):
    for elem in tree.getroot().iter():
        if any(etree.QName(c.tag).localname == "use" for c in elem):
            yield elem
