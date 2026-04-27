"""`export` command group."""

from __future__ import annotations

from pathlib import Path

import click

from cli_anything_inkstitch.binary import require, run_extension
from cli_anything_inkstitch.commands._helpers import open_project
from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.output import emit
from cli_anything_inkstitch.project import require_absolute


@click.group("export")
def export():
    """Export to machine embroidery formats."""


@export.command("formats")
@click.pass_context
def formats(ctx):
    """List supported export formats (introspected from pyembroidery)."""
    try:
        from pyembroidery.PyEmbroidery import supported_formats
        rows = []
        for f in supported_formats():
            rows.append({
                "extension": f.get("extension"),
                "description": f.get("description", ""),
                "writer": "writer" in f,
                "reader": "reader" in f,
            })
        emit(ctx, {"formats": rows})
    except Exception as e:  # noqa: BLE001
        emit(ctx, {"formats": [], "error": str(e)})


@export.command("file")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--format", "fmt", required=True)
@click.option("--out", required=True, type=click.Path())
@click.option("--id", "ids", multiple=True)
@click.pass_context
def file_cmd(ctx, project_path, fmt, out, ids):
    out = require_absolute(out, "out")
    with open_project(ctx, project_path) as (proj, _tree):
        binary = require(ctx.obj.get("binary_override"), proj.session)
        stdout = run_extension(binary, "output", proj.svg_path,
                                args={"format": fmt}, ids=list(ids),
                                capture_stdout=True)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(stdout or b"")
        emit(ctx, {"out": out, "format": fmt, "bytes": len(stdout or b"")})


@export.command("zip")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--formats", "fmts", required=True, help="Comma-separated formats.")
@click.option("--out", required=True, type=click.Path())
@click.option("--png-realistic", is_flag=True)
@click.option("--svg", "include_svg", is_flag=True)
@click.option("--threadlist", is_flag=True)
@click.option("--x-repeats", "x_repeats", type=int, default=1)
@click.option("--y-spacing-mm", "y_spacing_mm", type=float, default=0.0)
@click.pass_context
def zip_cmd(ctx, project_path, fmts, out, png_realistic, include_svg, threadlist, x_repeats, y_spacing_mm):
    out = require_absolute(out, "out")
    args = {f"format-{f.strip()}": "true" for f in fmts.split(",") if f.strip()}
    if png_realistic:
        args["format-png"] = "true"
    if include_svg:
        args["format-svg"] = "true"
    if threadlist:
        args["format-threadlist"] = "true"
    args["x-repeats"] = str(x_repeats)
    args["y-spacing"] = str(y_spacing_mm)
    with open_project(ctx, project_path) as (proj, _tree):
        binary = require(ctx.obj.get("binary_override"), proj.session)
        stdout = run_extension(binary, "zip", proj.svg_path, args=args, capture_stdout=True)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(stdout or b"")
        emit(ctx, {"out": out, "formats": list(args.keys()), "bytes": len(stdout or b"")})
