"""`preview` command group."""

from __future__ import annotations

import re
from pathlib import Path

import click
from lxml import etree as _etree

from cli_anything_inkstitch.binary import require, run_extension
from cli_anything_inkstitch.commands._helpers import open_project
from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.output import emit
from cli_anything_inkstitch.project import require_absolute

_SVG_NS = "http://www.w3.org/2000/svg"
_INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
_STROKE_RE = re.compile(r"stroke:\s*(#[0-9a-fA-F]{3,6})")
_NUM_RE = re.compile(r"[-+]?[0-9]*\.?[0-9]+")


def _parse_stitch_stats(root, spm: int) -> dict:
    """Parse the __inkstitch_stitch_plan__ layer from a preview SVG.

    Color blocks are <g id="__color_block_N__"> children of the plan layer.
    Each contains a nested <g> whose paths have style="stroke: #RRGGBB; ...".
    Stitch count = number of coordinate pairs across all paths (implicit lineto format).
    """
    plan_layer = root.find(f".//{{{_SVG_NS}}}g[@id='__inkstitch_stitch_plan__']")
    if plan_layer is None:
        return {"stitch_count": 0, "color_stops": [], "estimated_time_seconds": 0}

    total = 0
    color_stops: list[dict] = []
    block_index = 0

    for color_block in plan_layer.findall(f"{{{_SVG_NS}}}g"):
        block_id = color_block.get("id", "")
        if not block_id.startswith("__color_block_"):
            continue
        # Paths are direct children of the color block (no extra nesting).
        block_stitches = 0
        block_color = ""
        for path in color_block.findall(f".//{{{_SVG_NS}}}path"):
            d = path.get("d", "")
            nums = _NUM_RE.findall(d)
            block_stitches += len(nums) // 2
            if not block_color:
                style = path.get("style", "")
                m = _STROKE_RE.search(style)
                if m:
                    block_color = m.group(1).upper()
        total += block_stitches
        color_stops.append({
            "index": block_index,
            "rgb": block_color or "#000000",
            "stitches": block_stitches,
        })
        block_index += 1

    return {
        "stitch_count": total,
        "color_stops": color_stops,
        "estimated_time_seconds": round(60 * total / max(spm, 1)),
    }


@click.group("preview")
def preview():
    """Stitch-plan preview generation and stats."""


@preview.command("generate")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--out", required=True, type=click.Path())
@click.option("--id", "ids", multiple=True)
@click.option("--render-mode", type=click.Choice(["simple", "realistic-300", "realistic-600", "realistic-vector"]),
              default="simple")
@click.option("--needle-points", is_flag=True)
@click.option("--visual-commands", is_flag=True)
@click.option("--render-jumps", is_flag=True)
@click.option("--insensitive", is_flag=True)
@click.option("--raster", is_flag=True,
              help="Also rasterize the preview SVG to PNG via Inkscape "
                   "so it can be loaded as an image. PNG is written "
                   "alongside --out with .png extension.")
@click.option("--dpi", type=int, default=150, show_default=True,
              help="Rasterization DPI (only used with --raster).")
@click.pass_context
def generate(ctx, project_path, out, ids, render_mode, needle_points, visual_commands, render_jumps, insensitive, raster, dpi):
    out = require_absolute(out, "out")
    with open_project(ctx, project_path) as (proj, _tree):
        binary = require(ctx.obj.get("binary_override"), proj.session)
        args = {
            "render-mode": render_mode,
            "needle-points": str(needle_points).lower(),
            "visual-commands": str(visual_commands).lower(),
            "render-jumps": str(render_jumps).lower(),
            "insensitive": str(insensitive).lower(),
        }
        stdout = run_extension(binary, "stitch_plan_preview", proj.svg_path,
                                args=args, ids=list(ids), capture_stdout=True)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(stdout or b"")
        result = {"preview": out, "bytes": len(stdout or b"")}
        if raster:
            from cli_anything_inkstitch.inkscape import rasterize
            png_path = str(Path(out).with_suffix(".png"))
            png_bytes = rasterize(out, png_path, dpi=dpi)
            result["raster"] = png_path
            result["raster_bytes"] = png_bytes
            result["raster_dpi"] = dpi
        emit(ctx, result)


@preview.command("rasterize")
@click.option("--svg", "svg_in", required=True, type=click.Path(),
              help="Path to an SVG file (e.g. a stitch-plan preview).")
@click.option("--out", required=True, type=click.Path(),
              help="Output PNG path.")
@click.option("--dpi", type=int, default=150, show_default=True)
@click.pass_context
def rasterize_cmd(ctx, svg_in, out, dpi):
    """Convert any SVG to PNG via Inkscape.

    Standalone rasterizer — useful for converting previously-generated
    preview SVGs, validation-layer SVGs, or any other SVG into something
    the LLM can visually consume.
    """
    from cli_anything_inkstitch.inkscape import rasterize
    svg_in = require_absolute(svg_in, "svg")
    out = require_absolute(out, "out")
    if not Path(svg_in).exists():
        raise UserError(f"SVG not found: {svg_in}")
    png_bytes = rasterize(svg_in, out, dpi=dpi)
    emit(ctx, {"raster": out, "raster_bytes": png_bytes, "raster_dpi": dpi})


@preview.command("stats")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "ids", multiple=True)
@click.option("--spm", "spm", type=int, default=800,
              help="Stitches per minute (for runtime estimate).")
@click.pass_context
def stats(ctx, project_path, ids, spm):
    """Run stitch_plan_preview and parse counts out of the generated SVG."""
    with open_project(ctx, project_path) as (proj, _tree):
        binary = require(ctx.obj.get("binary_override"), proj.session)
        stdout = run_extension(binary, "stitch_plan_preview", proj.svg_path,
                                args={"render-mode": "simple"},
                                ids=list(ids), capture_stdout=True)
        if not stdout:
            raise UserError("preview produced no output")
        root = _etree.fromstring(stdout)
        emit(ctx, _parse_stitch_stats(root, spm))
