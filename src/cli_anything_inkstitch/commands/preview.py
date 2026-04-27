"""`preview` command group."""

from __future__ import annotations

from pathlib import Path

import click

from cli_anything_inkstitch.binary import require, run_extension
from cli_anything_inkstitch.commands._helpers import open_project
from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.output import emit
from cli_anything_inkstitch.project import require_absolute


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
@click.pass_context
def generate(ctx, project_path, out, ids, render_mode, needle_points, visual_commands, render_jumps, insensitive):
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
        emit(ctx, {"preview": out, "bytes": len(stdout or b"")})


@preview.command("stats")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "ids", multiple=True)
@click.option("--spm", "spm", type=int, default=800,
              help="Stitches per minute (for runtime estimate).")
@click.pass_context
def stats(ctx, project_path, ids, spm):
    """Run stitch_plan_preview and parse counts out of the generated SVG."""
    import tempfile
    from lxml import etree as _etree
    with open_project(ctx, project_path) as (proj, _tree):
        binary = require(ctx.obj.get("binary_override"), proj.session)
        stdout = run_extension(binary, "stitch_plan_preview", proj.svg_path,
                                args={"render-mode": "simple"},
                                ids=list(ids), capture_stdout=True)
        if not stdout:
            raise UserError("preview produced no output")
        with tempfile.NamedTemporaryFile(suffix=".svg") as f:
            f.write(stdout)
            f.flush()
            tree = _etree.parse(f.name)
        # Extract approximate counts from the __inkstitch_stitch_plan__ layer.
        # Each stroke segment in the plan is one stitch; jumps are represented
        # by separate paths with a specific class. v0.1 returns a coarse estimate.
        root = tree.getroot()
        stitch_paths = root.xpath(
            "//*[local-name()='g'][@id='__inkstitch_stitch_plan__']//*[local-name()='path']"
        )
        total = 0
        color_set = set()
        for p in stitch_paths:
            d = p.get("d", "")
            total += d.count("L") + d.count("l")
            stroke = p.get("stroke") or ""
            if stroke:
                color_set.add(stroke)
        result = {
            "stitch_count": total,
            "color_stops": [{"index": i, "rgb": c} for i, c in enumerate(sorted(color_set))],
            "estimated_time_seconds": int(60 * total / max(spm, 1)),
            "note": "v0.1 coarse estimate from path d-attribute parsing",
        }
        emit(ctx, result)
