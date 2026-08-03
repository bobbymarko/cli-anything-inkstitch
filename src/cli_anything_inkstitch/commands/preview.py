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


@preview.command("stitch-sim")
@click.option("--dst", "dst_path", required=True, type=click.Path(exists=True),
              help="Embroidery file to simulate (DST, PES, JEF, etc.).")
@click.option("--out", required=True, type=click.Path(),
              help="Output PNG path.")
@click.option("--width", "img_width", type=int, default=1600, show_default=True,
              help="Output image width in pixels.")
@click.option("--height", "img_height", type=int, default=1400, show_default=True,
              help="Output image height in pixels.")
@click.option("--thread-color", "thread_color", default=None,
              help="Override thread color as hex (e.g. #e85454).  "
                   "Defaults to the color stored in the file.")
@click.option("--show-jumps/--hide-jumps", "show_jumps", default=True,
              show_default=True,
              help="Draw jump stitches as dashed gray lines.")
@click.option("--background", "bg_color", default="#f5f5f5", show_default=True,
              help="Background hex color.")
@click.pass_context
def stitch_sim(ctx, dst_path, out, img_width, img_height,
               thread_color, show_jumps, bg_color):
    """Render the actual needle path of an embroidery file as a PNG.

    Unlike 'preview generate' (which renders filled stitch areas via Inkscape),
    this command draws every individual stitch segment so you can see exactly
    where the needle travels, where jumps occur, and how fill sections connect.

    Jump stitches are drawn as thin dashed gray lines.  Regular stitches are
    drawn in the thread color.  Useful for catching fill-order problems, messy
    travel stitches, and unintended needle paths before a physical test-sew.

    Example:

        preview stitch-sim --dst design.dst --out sim.png
    """
    import pyembroidery
    from PIL import Image, ImageDraw

    dst_path = require_absolute(dst_path, "dst")
    out = require_absolute(out, "out")

    pattern = pyembroidery.read(dst_path)
    if pattern is None:
        raise UserError(f"could not read embroidery file: {dst_path}")

    # ── Collect all coordinate extents ──────────────────────────────────────
    xs = [s[0] for s in pattern.stitches]
    ys = [s[1] for s in pattern.stitches]
    if not xs:
        raise UserError("embroidery file contains no stitches")

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    design_w = x_max - x_min or 1
    design_h = y_max - y_min or 1

    PADDING = 0.06   # 6% padding on each side
    pad_x = design_w * PADDING
    pad_y = design_h * PADDING
    scale_x = img_width  / (design_w + 2 * pad_x)
    scale_y = img_height / (design_h + 2 * pad_y)
    scale = min(scale_x, scale_y)

    # Center in image
    render_w = (design_w + 2 * pad_x) * scale
    render_h = (design_h + 2 * pad_y) * scale
    off_x = (img_width  - render_w) / 2 + pad_x * scale
    off_y = (img_height - render_h) / 2 + pad_y * scale

    def to_px(x, y):
        return (
            off_x + (x - x_min) * scale,
            off_y + (y - y_min) * scale,
        )

    # ── Resolve thread color ─────────────────────────────────────────────────
    def _hex_to_rgb(h: str):
        h = h.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    # Try to get color from first thread in pattern
    stitch_rgb = (220, 80, 80)   # default warm red
    if thread_color:
        stitch_rgb = _hex_to_rgb(thread_color)
    elif pattern.threadlist:
        t = pattern.threadlist[0]
        r = getattr(t, "color", None)
        if r and isinstance(r, int):
            stitch_rgb = ((r >> 16) & 0xFF, (r >> 8) & 0xFF, r & 0xFF)

    jump_rgb  = (180, 180, 180)
    trim_rgb  = (100, 180, 100)
    bg_rgb    = _hex_to_rgb(bg_color)

    # ── Draw ─────────────────────────────────────────────────────────────────
    img = Image.new("RGB", (img_width, img_height), bg_rgb)
    draw = ImageDraw.Draw(img)

    STITCH_W = max(1, int(scale * 0.8))   # line width scales with zoom
    JUMP_W   = max(1, STITCH_W - 1)

    prev_px = None
    in_jump = False

    for sx, sy, cmd in pattern.stitches:
        cur_px = to_px(sx, sy)

        if cmd == pyembroidery.STITCH:
            if prev_px is not None and not in_jump:
                draw.line([prev_px, cur_px], fill=stitch_rgb, width=STITCH_W)
            in_jump = False
            prev_px = cur_px

        elif cmd == pyembroidery.JUMP:
            if show_jumps and prev_px is not None:
                # Dashed line: draw short segments with gaps
                dx = cur_px[0] - prev_px[0]
                dy = cur_px[1] - prev_px[1]
                seg_len = max(scale * 3, 4)
                total = (dx**2 + dy**2) ** 0.5
                if total > 0:
                    steps = max(1, int(total / (seg_len * 2)))
                    for i in range(steps):
                        t0 = i / steps
                        t1 = (i + 0.5) / steps
                        p0 = (prev_px[0] + dx * t0, prev_px[1] + dy * t0)
                        p1 = (prev_px[0] + dx * t1, prev_px[1] + dy * t1)
                        draw.line([p0, p1], fill=jump_rgb, width=JUMP_W)
            in_jump = True
            prev_px = cur_px

        elif cmd == pyembroidery.TRIM:
            in_jump = True
            prev_px = cur_px

        elif cmd in (pyembroidery.END, pyembroidery.STOP):
            prev_px = None
            in_jump = False

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)

    # Stats
    n_stitches = sum(1 for _, _, c in pattern.stitches if c == pyembroidery.STITCH)
    n_jumps    = sum(1 for _, _, c in pattern.stitches if c == pyembroidery.JUMP)
    n_trims    = sum(1 for _, _, c in pattern.stitches if c == pyembroidery.TRIM)
    emit(ctx, {
        "sim_png":   out,
        "stitches":  n_stitches,
        "jumps":     n_jumps,
        "trims":     n_trims,
        "size_bytes": Path(out).stat().st_size,
    })


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
