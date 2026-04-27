"""`document` command group."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import click

from cli_anything_inkstitch.commands._helpers import open_project
from cli_anything_inkstitch.errors import ProjectError, UserError
from cli_anything_inkstitch.output import emit
from cli_anything_inkstitch.project import ProjectFile, project_lock, require_absolute
from cli_anything_inkstitch.svg.document import (
    all_addressable_elements,
    load_svg,
    save_svg,
    sha256_of,
)
from cli_anything_inkstitch.svg.elements import classify, element_summary


@click.group("document")
def document():
    """Document and project management."""


@document.command("new")
@click.option("--project", "project_path", required=True, type=click.Path())
@click.pass_context
def new(ctx, project_path):
    """Create an empty project file (no SVG attached yet)."""
    path = require_absolute(project_path, "project")
    if Path(path).exists():
        raise UserError(f"project already exists: {path}")
    with project_lock(path):
        proj, _ = ProjectFile.load_or_create(path)
        proj.save()
    emit(ctx, {"project": path, "created": True}, human=f"created {path}")


@document.command("open")
@click.option("--project", "project_path", required=True, type=click.Path())
@click.option("--svg", "svg_path", required=True, type=click.Path())
@click.option("--force", is_flag=True, help="Overwrite an existing project's SVG pointer.")
@click.pass_context
def open_cmd(ctx, project_path, svg_path, force):
    """Attach an SVG to a project (creates the project if absent)."""
    project_path = require_absolute(project_path, "project")
    svg_path = require_absolute(svg_path, "svg")
    if not Path(svg_path).exists():
        raise UserError(f"SVG not found: {svg_path}")
    with project_lock(project_path):
        proj, created = ProjectFile.load_or_create(project_path)
        if proj.svg_path and proj.svg_path != svg_path and not force:
            raise UserError(
                f"project already attached to {proj.svg_path} (use --force to switch)"
            )
        # validate the SVG parses
        load_svg(svg_path)
        proj.svg_path = svg_path
        proj.svg_sha256 = sha256_of(svg_path)
        proj.save()
    emit(ctx, {
        "project": project_path,
        "svg": svg_path,
        "created_project": created,
    }, human=f"opened {svg_path} in {project_path}")


@document.command("save")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--svg-out", type=click.Path(), default=None,
              help="Optional alternate path to also write the SVG to.")
@click.pass_context
def save(ctx, project_path, svg_out):
    """Flush the project (and SVG, if --svg-out) to disk."""
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        if svg_out:
            out = require_absolute(svg_out, "svg-out")
            if tree is None:
                raise ProjectError("project has no SVG attached")
            sha = save_svg(tree, out)
            emit(ctx, {"saved": True, "project": proj.path, "svg_out": out, "sha256": sha},
                 human=f"saved to {out}")
            return
        emit(ctx, {"saved": True, "project": proj.path, "svg_sha256": proj.svg_sha256},
             human=f"saved {proj.path}")


@document.command("info")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.pass_context
def info(ctx, project_path):
    """Show document info: dimensions, hoop, units, palette, element histogram."""
    with open_project(ctx, project_path) as (proj, tree):
        result = {
            "project": proj.path,
            "svg": proj.svg_path,
            "session": proj.session,
        }
        if tree is not None:
            root = tree.getroot()
            result["root_attrib"] = {
                "width": root.get("width"),
                "height": root.get("height"),
                "viewBox": root.get("viewBox"),
            }
            elements = list(all_addressable_elements(tree))
            histogram = Counter(classify(e) for e in elements if e.get("id"))
            result["element_count"] = sum(1 for e in elements if e.get("id"))
            result["stitch_type_histogram"] = dict(histogram)
        emit(ctx, result)


@document.command("set-hoop")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--name", default=None)
@click.option("--width-mm", "width_mm", type=float, default=None)
@click.option("--height-mm", "height_mm", type=float, default=None)
@click.pass_context
def set_hoop(ctx, project_path, name, width_mm, height_mm):
    if not (name or (width_mm and height_mm)):
        raise UserError("provide --name or both --width-mm and --height-mm")
    with open_project(ctx, project_path, mutate=True) as (proj, _tree):
        before = dict(proj.session["hoop"])
        if name and not (width_mm or height_mm):
            preset = HOOP_PRESETS.get(name)
            if not preset:
                raise UserError(f"unknown hoop preset: {name} (known: {sorted(HOOP_PRESETS)})")
            proj.session["hoop"] = {"name": name, **preset}
        else:
            proj.session["hoop"] = {
                "name": name or f"{int(width_mm)}x{int(height_mm)}",
                "width_mm": float(width_mm),
                "height_mm": float(height_mm),
            }
        from cli_anything_inkstitch.history import make_entry, metadata_diff
        from cli_anything_inkstitch.history import push as _push
        _push(proj.history, make_entry("document set-hoop", metadata_diff(
            {"hoop": before}, {"hoop": proj.session["hoop"]}
        ), scope="project"))
        emit(ctx, {"hoop": proj.session["hoop"]})


HOOP_PRESETS = {
    "100x100": {"width_mm": 100.0, "height_mm": 100.0},
    "130x180": {"width_mm": 130.0, "height_mm": 180.0},
    "150x240": {"width_mm": 150.0, "height_mm": 240.0},
    "200x300": {"width_mm": 200.0, "height_mm": 300.0},
    "260x160": {"width_mm": 260.0, "height_mm": 160.0},
    "360x200": {"width_mm": 360.0, "height_mm": 200.0},
}


@document.command("set-units")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--units", type=click.Choice(["mm", "in"]), required=True)
@click.pass_context
def set_units(ctx, project_path, units):
    with open_project(ctx, project_path, mutate=True) as (proj, _tree):
        proj.session["units"] = units
        emit(ctx, {"units": units})


@document.command("set-machine-target")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--format", "fmt", required=True)
@click.pass_context
def set_machine_target(ctx, project_path, fmt):
    with open_project(ctx, project_path, mutate=True) as (proj, _tree):
        proj.session["machine_target"] = fmt
        emit(ctx, {"machine_target": fmt})


@document.command("set-palette")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--palette", required=True)
@click.pass_context
def set_palette(ctx, project_path, palette):
    with open_project(ctx, project_path, mutate=True) as (proj, _tree):
        proj.session["thread_palette"] = palette
        emit(ctx, {"thread_palette": palette})


@document.command("set-collapse-len")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--mm", "mm", type=float, required=True)
@click.pass_context
def set_collapse_len(ctx, project_path, mm):
    with open_project(ctx, project_path, mutate=True) as (proj, _tree):
        proj.session["collapse_len_mm"] = float(mm)
        emit(ctx, {"collapse_len_mm": float(mm)})


@document.command("set-min-stitch-len")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--mm", "mm", type=float, required=True)
@click.pass_context
def set_min_stitch_len(ctx, project_path, mm):
    with open_project(ctx, project_path, mutate=True) as (proj, _tree):
        proj.session["min_stitch_len_mm"] = float(mm)
        emit(ctx, {"min_stitch_len_mm": float(mm)})


@document.command("json")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.pass_context
def json_cmd(ctx, project_path):
    """Print raw project JSON."""
    with open_project(ctx, project_path) as (proj, _tree):
        # Always JSON regardless of --json
        click.echo(json.dumps(proj.data, indent=2, default=str))
