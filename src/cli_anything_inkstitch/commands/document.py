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
        # Load, stamp inkstitch_svg_version if missing, write back.
        tree = load_svg(svg_path)
        proj.svg_sha256 = save_svg(tree, svg_path)
        proj.svg_path = svg_path
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
@click.option("--palette", required=True,
              help="Thread palette name (e.g. 'Madeira Polyneon', 'Isacord').")
@click.pass_context
def set_palette(ctx, project_path, palette):
    """Record the thread palette so inkstitch resolves fills to thread names.

    Writes BOTH our session JSON (for our own bookkeeping) AND the SVG's
    `<metadata>/<inkstitch:thread-palette>` element. The latter is what
    inkstitch's exports actually read — the threadlist generation, PES/JEF
    output, and apply-palette extension all consult it. Without writing
    metadata, the palette name was a no-op for inkstitch.
    """
    from cli_anything_inkstitch.svg.document import set_inkstitch_metadata
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        proj.session["thread_palette"] = palette
        if tree is not None:
            set_inkstitch_metadata(tree, "thread-palette", palette)
        emit(ctx, {"thread_palette": palette,
                   "wrote_svg_metadata": tree is not None})


@document.command("list-thread-colors")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.pass_context
def list_thread_colors(ctx, project_path):
    """List unique thread colors used in the design, with element counts.

    Useful for operator handoff: tells the machine operator which threads to
    load. Resolves color from each addressable element's fill (or stroke if
    no fill), normalizes to lowercase hex, returns deduplicated by color
    with the count of elements using each.

    Pair with `document set-palette` so subsequent exports resolve these
    hex colors to actual thread catalog names.
    """
    from collections import Counter

    from cli_anything_inkstitch.svg.colors import closest_named
    from cli_anything_inkstitch.svg.document import (
        all_addressable_elements,
        get_inkstitch_metadata,
    )
    from cli_anything_inkstitch.svg.elements import element_summary

    def _resolved_thread_color(elem) -> str | None:
        s = element_summary(elem)
        # Inkstitch reads color from fill first, then falls back to black if
        # neither fill nor stroke is set (matches inkstitch's fill_color default).
        candidate = s["fill"] or s["stroke"] or "#000000"
        candidate = candidate.strip().lower()
        return candidate if candidate.startswith("#") else None

    with open_project(ctx, project_path) as (proj, tree):
        if tree is None:
            raise ProjectError("project has no SVG attached")
        counter: Counter = Counter()
        for elem in all_addressable_elements(tree):
            if not elem.get("id"):
                continue
            c = _resolved_thread_color(elem)
            if c is not None:
                counter[c] += 1
        rows = [
            {
                "hex": hex_color,
                "name": closest_named(hex_color),
                "element_count": count,
            }
            for hex_color, count in counter.most_common()
        ]
        emit(ctx, {
            "thread_palette": get_inkstitch_metadata(tree, "thread-palette"),
            "colors": rows,
            "unique_count": len(rows),
        })


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


# --- design-intent context for AI reasoning -------------------------------
#
# These commands capture the *non-geometric* facts the LLM needs to make good
# digitization decisions: what fabric is this going on, what thread, what's
# the design for. Stored in session.context, surfaced in element list /
# element describe output so the LLM sees it on every contextual call.

# Well-known fields with nudged-toward-correct values. Anything else can go
# through --set KEY=VALUE.
_STRETCH_CHOICES = ("none", "low", "medium", "high")
_TENSION_CHOICES = ("light", "medium", "firm")


def _ensure_context(proj: ProjectFile) -> dict:
    """Initialize session.context if a legacy project file lacks it."""
    if "context" not in proj.session:
        proj.session["context"] = {}
    return proj.session["context"]


@document.command("set-context")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--material", default=None,
              help="Fabric description (e.g. 'knit cotton t-shirt', 'denim').")
@click.option("--stretch", type=click.Choice(_STRETCH_CHOICES), default=None,
              help="How stretchy the substrate is. Drives push/pull comp choices.")
@click.option("--thread", default=None,
              help="Thread description (e.g. '40wt polyester', '60wt cotton').")
@click.option("--stabilizer", default=None,
              help="Backing description (e.g. 'medium cut-away', 'tear-away').")
@click.option("--hoop-tension", type=click.Choice(_TENSION_CHOICES), default=None,
              help="Hooping firmness.")
@click.option("--intent", default=None,
              help="Free-form description of what this design is for.")
@click.option("--set", "kvs", multiple=True, metavar="KEY=VALUE",
              help="Set an arbitrary context key. Repeatable.")
@click.option("--unset", "unset_keys", multiple=True, metavar="KEY",
              help="Remove a context key. Repeatable.")
@click.option("--clear", is_flag=True, help="Drop all context.")
@click.pass_context
def set_context(ctx, project_path, material, stretch, thread, stabilizer,
                 hoop_tension, intent, kvs, unset_keys, clear):
    """Capture material/intent context that informs param choices.

    The LLM consumes this on every `element list` and `element describe`
    call, so parameter choices ("more pull comp because it's stretchy",
    "tighter spacing because the design will be washed often") can be
    grounded in real conditions rather than assumed defaults.
    """
    with open_project(ctx, project_path, mutate=True) as (proj, _tree):
        ctx_obj = _ensure_context(proj)
        if clear:
            ctx_obj.clear()
        # Typed fields
        for key, val in (
            ("material", material), ("stretch", stretch),
            ("thread", thread), ("stabilizer", stabilizer),
            ("hoop_tension", hoop_tension), ("intent", intent),
        ):
            if val is not None:
                ctx_obj[key] = val
        # Free-form key=value
        for kv in kvs:
            if "=" not in kv:
                raise UserError(f"--set value must be KEY=VALUE, got: {kv!r}")
            k, v = kv.split("=", 1)
            ctx_obj[k.strip()] = v.strip()
        # Removals
        for k in unset_keys:
            ctx_obj.pop(k, None)
        emit(ctx, {"context": dict(ctx_obj)})


@document.command("get-context")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.pass_context
def get_context(ctx, project_path):
    """Print the project's design-intent context."""
    with open_project(ctx, project_path) as (proj, _tree):
        emit(ctx, {"context": dict(_ensure_context(proj))})


@document.command("prep")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--illustrator-rings", "ring_action",
              type=click.Choice(["detect", "skip", "fill-black", "satin"]),
              default="detect", show_default=True,
              help="How to handle paths with no fill/stroke and 2+ subpaths "
                   "(Illustrator's stroke-to-outline-ring artifact). "
                   "detect = report only; skip = display:none; "
                   "fill-black = explicit fill='#000000'; "
                   "satin = inkstitch:satin_column='True'.")
@click.pass_context
def prep(ctx, project_path, ring_action):
    """Assign IDs, inline CSS class-based fills/strokes, and detect/handle
    Illustrator stroke-to-outline rings.

    Useful for Illustrator-exported SVGs where elements lack id attributes,
    fills are declared in a `<style>` block rather than inline, and strokes
    are pre-converted to filled outline rings.
    """
    from cli_anything_inkstitch.svg.prep import prep_svg
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        if tree is None:
            raise ProjectError("project has no SVG attached")
        stats = prep_svg(tree, ring_action=ring_action)
        emit(ctx, stats)


@document.command("json")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.pass_context
def json_cmd(ctx, project_path):
    """Print raw project JSON."""
    with open_project(ctx, project_path) as (proj, _tree):
        # Always JSON regardless of --json
        click.echo(json.dumps(proj.data, indent=2, default=str))
