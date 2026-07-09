"""`commands` command group — visual command attach/detach.

Writes the structure Ink/Stitch actually reads (symbol in defs + marker
<use> + connector path linking it to the target element). Legacy names
(fill_start, ...) map to the engine's real command names.
"""

from __future__ import annotations

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
from cli_anything_inkstitch.svg import commands as vc


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
        if svg_id:
            targets = [require_id(tree, svg_id)]
        else:
            targets = [e for e in tree.getroot().iter()
                       if isinstance(e.tag, str) and e.get("id")]
        for elem in targets:
            for cmd in vc.find_commands(tree, elem):
                rows.append({"id": elem.get("id"), **{k: v for k, v in cmd.items()
                                                      if k != "connector_id"}})
            for cmd in vc.find_legacy_markers(elem):
                rows.append({"id": elem.get("id"), **cmd})
        emit(ctx, {"commands": rows, "count": len(rows)})


def _known_commands(refresh: bool = False) -> set[str]:
    schema = load_schema(refresh=refresh)
    return {c["name"] for c in schema["commands"]}


@commands_group.command("attach")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.option("--command", "command_name", required=True)
@click.option("--at-x", "at_x", type=float, default=None)
@click.option("--at-y", "at_y", type=float, default=None)
@click.option("--refresh-schema", is_flag=True,
              help="Re-extract the schema from inkstitch source before loading.")
@click.pass_context
def attach(ctx, project_path, svg_id, command_name, at_x, at_y, refresh_schema):
    canonical = vc.canonical_command(command_name)
    valid = _known_commands(refresh_schema)
    if canonical not in valid and command_name not in valid:
        raise UserError(
            f"unknown command '{command_name}' (known: {sorted(valid)}; "
            f"legacy aliases: {sorted(vc.LEGACY_ALIASES)})")
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        elem = require_id(tree, svg_id)
        if at_x is None or at_y is None:
            from cli_anything_inkstitch.svg.geometry import element_bbox
            bb = element_bbox(elem)
            if bb:
                at_x = bb[0] if at_x is None else at_x
                at_y = bb[1] - 2 if at_y is None else at_y
            else:
                at_x, at_y = at_x or 0.0, at_y or 0.0
        result = vc.attach_command(tree, elem, canonical, at_x, at_y)
        group = result.pop("group")
        parent = group.getparent()
        record(proj.history,
               f"commands attach --id {svg_id} --command {result['command']}",
               node_insert(parent_xpath=f"//*[@id='{parent.get('id')}']",
                           index=list(parent).index(group),
                           after_xml=etree.tostring(group).decode()))
        emit(ctx, {"id": svg_id, "attached": result["command"],
                   "use_id": result["use_id"],
                   **({"alias_of": command_name} if canonical != command_name else {})})


@commands_group.command("detach")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.option("--command", "command_name", required=True)
@click.pass_context
def detach(ctx, project_path, svg_id, command_name):
    canonical = vc.canonical_command(command_name)
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        elem = require_id(tree, svg_id)
        removed = []
        matches = [c for c in vc.find_commands(tree, elem)
                   + vc.find_legacy_markers(elem)
                   if c["command"] in (canonical, command_name)]
        for cmd in matches:
            result = vc.detach_command(tree, cmd["use_id"])
            record(proj.history,
                   f"commands detach --id {svg_id} --command {command_name}",
                   node_delete(parent_xpath=f"//*[@id='{result['parent_id']}']",
                               index=result["index"], before_xml=result["xml"]))
            removed.append(cmd["use_id"])
        emit(ctx, {"id": svg_id, "detached": command_name, "use_ids_removed": removed})


@commands_group.command("migrate")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.pass_context
def migrate(ctx, project_path):
    """Upgrade legacy bare-<use> markers (which the engine ignores) to the
    connector structure it honors, keeping positions and mapping names."""
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        migrated = []
        for elem in list(tree.getroot().iter()):
            if not isinstance(elem.tag, str) or not elem.get("id"):
                continue
            for marker in vc.find_legacy_markers(elem):
                removal = vc.detach_command(tree, marker["use_id"])
                record(proj.history,
                       f"commands migrate --id {elem.get('id')} (remove legacy)",
                       node_delete(parent_xpath=f"//*[@id='{removal['parent_id']}']",
                                   index=removal["index"], before_xml=removal["xml"]))
                result = vc.attach_command(
                    tree, elem, vc.canonical_command(marker["command"]),
                    marker["x"], marker["y"])
                group = result.pop("group")
                parent = group.getparent()
                record(proj.history,
                       f"commands migrate --id {elem.get('id')} (attach {result['command']})",
                       node_insert(parent_xpath=f"//*[@id='{parent.get('id')}']",
                                   index=list(parent).index(group),
                                   after_xml=etree.tostring(group).decode()))
                migrated.append({"id": elem.get("id"),
                                 "from": marker["command"],
                                 "to": result["command"],
                                 "use_id": result["use_id"]})
        emit(ctx, {"migrated": migrated, "count": len(migrated)})


@commands_group.command("list-types")
@click.option("--refresh-schema", is_flag=True,
              help="Re-extract the schema from inkstitch source before loading.")
@click.pass_context
def list_types(ctx, refresh_schema):
    schema = load_schema(refresh=refresh_schema)
    emit(ctx, {"commands": schema["commands"],
               "legacy_aliases": vc.LEGACY_ALIASES})
