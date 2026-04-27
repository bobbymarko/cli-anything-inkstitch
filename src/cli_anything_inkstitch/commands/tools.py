"""`tools` command group — binary-backed geometry rewrites."""

from __future__ import annotations

import click
from lxml import etree

from cli_anything_inkstitch.binary import require, run_extension
from cli_anything_inkstitch.commands._helpers import open_project
from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.history import subtree_replace
from cli_anything_inkstitch.output import emit


@click.group("tools")
def tools():
    """Binary-backed geometry rewrites (auto-satin, convert, flip, route, ...)."""


def _run_tool(ctx, project_path, extension_name, ids, args):
    """Common pattern: run binary tool; if it returns SVG, swap it in.

    Uses mutate=False — we save the SVG ourselves so open_project's exit-save
    doesn't clobber the binary's output with the original tree.
    """
    from cli_anything_inkstitch.svg.document import save_svg
    from cli_anything_inkstitch.svg.attrs import ensure_inkstitch_namespace
    with open_project(ctx, project_path, mutate=False) as (proj, _tree):
        binary = require(ctx.obj.get("binary_override"), proj.session)
        stdout = run_extension(binary, extension_name, proj.svg_path,
                                args=args, ids=ids, capture_stdout=True)
        if not stdout:
            # Empty stdout = nothing applicable; not an error.
            emit(ctx, {"tool": extension_name, "ids": ids, "changed": False,
                       "note": "no changes — selection may not match tool requirements"})
            return
        new_tree = etree.ElementTree(etree.fromstring(stdout))
        ensure_inkstitch_namespace(new_tree.getroot())
        proj.svg_sha256 = save_svg(new_tree, proj.svg_path)
        proj.save()
        emit(ctx, {"tool": extension_name, "ids": ids, "changed": True, "bytes": len(stdout)})


@tools.command("auto-satin")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--ids", "ids_csv", required=True)
@click.option("--trim/--no-trim", default=True)
@click.option("--preserve-order", is_flag=True)
@click.option("--keep-originals", is_flag=True)
@click.pass_context
def auto_satin(ctx, project_path, ids_csv, trim, preserve_order, keep_originals):
    ids = [s.strip() for s in ids_csv.split(",") if s.strip()]
    args = {
        "trim": str(trim).lower(),
        "preserve_order": str(preserve_order).lower(),
        "keep_originals": str(keep_originals).lower(),
    }
    _run_tool(ctx, project_path, "auto_satin", ids, args)


@tools.command("convert-to-satin")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--ids", "ids_csv", required=True)
@click.pass_context
def convert_to_satin(ctx, project_path, ids_csv):
    ids = [s.strip() for s in ids_csv.split(",") if s.strip()]
    _run_tool(ctx, project_path, "stroke_to_satin", ids, {})


@tools.command("convert-satin-to-stroke")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--ids", "ids_csv", required=True)
@click.option("--keep-satin", is_flag=True)
@click.pass_context
def convert_satin_to_stroke(ctx, project_path, ids_csv, keep_satin):
    ids = [s.strip() for s in ids_csv.split(",") if s.strip()]
    _run_tool(ctx, project_path, "satin_to_stroke", ids,
              {"keep_satin": str(keep_satin).lower()})


@tools.command("flip-satin")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.pass_context
def flip_satin(ctx, project_path, svg_id):
    _run_tool(ctx, project_path, "flip", [svg_id], {})


@tools.command("auto-run")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--ids", "ids_csv", required=True)
@click.option("--trim/--no-trim", default=False)
@click.option("--preserve-order", is_flag=True)
@click.option("--break-up/--no-break-up", default=True)
@click.pass_context
def auto_run(ctx, project_path, ids_csv, trim, preserve_order, break_up):
    ids = [s.strip() for s in ids_csv.split(",") if s.strip()]
    _run_tool(ctx, project_path, "auto_run", ids, {
        "trim": str(trim).lower(),
        "preserve_order": str(preserve_order).lower(),
        "break_up": str(break_up).lower(),
    })


@tools.command("break-apart")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.pass_context
def break_apart(ctx, project_path, svg_id):
    _run_tool(ctx, project_path, "break_apart", [svg_id], {})


@tools.command("cleanup")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.pass_context
def cleanup(ctx, project_path):
    _run_tool(ctx, project_path, "cleanup", [], {})
