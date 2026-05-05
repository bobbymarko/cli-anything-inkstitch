"""`validate` command group."""

from __future__ import annotations

import click
from lxml import etree

from cli_anything_inkstitch.binary import discover, run_extension
from cli_anything_inkstitch.commands._helpers import open_project
from cli_anything_inkstitch.errors import UserError, ValidationError
from cli_anything_inkstitch.output import emit
from cli_anything_inkstitch.schema.cache import load_schema
from cli_anything_inkstitch.svg.attrs import INKSTITCH_PREFIX
from cli_anything_inkstitch.svg.document import all_addressable_elements
from cli_anything_inkstitch.svg.elements import classify, warnings_for_element
from cli_anything_inkstitch.svg.geometry import PIXELS_PER_MM
from cli_anything_inkstitch.svg.validation import parse_validation_layer


@click.group("validate")
def validate():
    """Static and binary-backed validation."""


@validate.command("static")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--strict", is_flag=True)
@click.option("--refresh-schema", is_flag=True,
              help="Re-extract the schema from inkstitch source before loading.")
@click.pass_context
def static(ctx, project_path, strict, refresh_schema):
    schema = load_schema(refresh=refresh_schema)
    issues = []
    with open_project(ctx, project_path) as (proj, tree):
        if tree is None:
            raise UserError("project has no SVG attached")
        for elem in all_addressable_elements(tree):
            if not elem.get("id"):
                continue
            # Surface "stitches as black by default" before the unassigned
            # warning — it's the more actionable one. (Unassigned implies
            # the same outcome but doesn't tell you *why*.)
            for w in warnings_for_element(elem):
                issues.append({**w, "id": elem.get("id")})
            st_name = classify(elem)
            if st_name == "unassigned":
                issues.append({"severity": "warning", "id": elem.get("id"),
                                "type": "unassigned",
                                "message": "element has no stitch type assigned"})
                continue
            st = schema["stitch_types"].get(st_name)
            if not st:
                issues.append({"severity": "warning", "id": elem.get("id"),
                                "type": "unknown_stitch_type",
                                "message": f"classified as {st_name} but not in schema"})
                continue
            known = set(st.get("params", {}).keys())
            for k in elem.attrib:
                if isinstance(k, str) and k.startswith(INKSTITCH_PREFIX):
                    local = k[len(INKSTITCH_PREFIX):]
                    if local not in known:
                        issues.append({"severity": "warning", "id": elem.get("id"),
                                        "type": "unknown_param",
                                        "message": f"unknown param {local} for stitch type {st_name}"})
    payload = {"issues": issues, "ok": not any(i["severity"] == "error" for i in issues)}
    emit(ctx, payload)
    if strict and issues:
        raise ValidationError(f"{len(issues)} issue(s) found")


@validate.command("run")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--strict", is_flag=True)
@click.option("--no-errors", "show_errors", flag_value=False, default=True,
              help="Skip error detection.")
@click.option("--no-warnings", "show_warnings", flag_value=False, default=True,
              help="Skip warning detection.")
@click.option("--no-type-warnings", "show_type_warnings", flag_value=False, default=True,
              help="Skip object-type warning detection.")
@click.pass_context
def run(ctx, project_path, strict, show_errors, show_warnings, show_type_warnings):
    """Invoke inkstitch's troubleshoot extension and parse the validation layer."""
    with open_project(ctx, project_path) as (proj, _tree):
        binary = discover(ctx.obj.get("binary_override"), proj.session)
        if not binary:
            emit(ctx, {
                "ok": None,
                "issues": [], "errors": [], "warnings": [], "type_warnings": [],
                "binary_status": "not_found",
                "note": "Ink/Stitch binary not installed; run `validate static` for schema-only checks.",
            })
            return

        # The compiled inkstitch binary only accepts params declared in the INX.
        # show-errors/show-warnings/show-type-warning are argparse-only and default True,
        # so we only pass them when they're being turned off (which requires a source build).
        extra_args: dict = {}
        if not show_errors:
            extra_args["show-errors"] = "False"
        if not show_warnings:
            extra_args["show-warnings"] = "False"
        if not show_type_warnings:
            extra_args["show-type-warning"] = "False"
        stdout = run_extension(
            binary, "troubleshoot", proj.svg_path,
            args=extra_args or None,
            capture_stdout=True,
        )
        parsed = parse_validation_layer(stdout or b"")
        ok = len(parsed["errors"]) == 0
        emit(ctx, {
            "ok": ok,
            "binary_status": "ok",
            "counts": {
                "errors": len(parsed["errors"]),
                "warnings": len(parsed["warnings"]),
                "type_warnings": len(parsed["type_warnings"]),
            },
            **parsed,
        })
        if strict and not ok:
            raise ValidationError(
                f"{len(parsed['errors'])} error(s), {len(parsed['warnings'])} warning(s)"
            )


# Issue `name` strings whose remediation is a `cleanup` extension run.
# Source: inkstitch/lib/elements/{empty_d_object,fill_stitch}.py — these classes'
# `steps_to_solve` literally tell the user "run Cleanup Document".
#
# We list multiple labels per concept because inkstitch's troubleshoot output
# varies between versions (EmptyD class lives in `name`, but newer versions of
# the troubleshoot extension surface `element_name = "Empty Path"` instead).
# Verified empirically against inkstitch 3.2.2 emitting "Empty Path".
AUTO_FIX_NAMES = frozenset({
    # Empty <path d=""/> — cleanup removes
    "EmptyD",
    "Empty Path",
    # Fill below area threshold — cleanup removes
    "Small Fill",
})

# Map issue name → human suggestion for the manual case. Anything not listed
# falls back to the generic "open in Ink/Stitch" message.
MANUAL_SUGGESTIONS = {
    "Image": "convert image to a path (Ink/Stitch ignores raster images)",
    "Marker Element": "convert marker to a path or remove it",
    "Text": "convert text to a path (Path > Object to Path in Inkscape)",
    "Not stitchable satin column": "fix satin geometry: rails must not self-intersect",
    "Rail is a closed path": "open the satin rail; rails must not be closed loops",
    "Rung doesn't intersect rails": "extend rungs so each crosses both rails",
    "Satin has no rungs": "add at least one rung perpendicular to the rails",
    "Rung intersects too many times": "split the satin or simplify the rung path",
    "This shape is invalid": "repair the path geometry (Path > Union, then Path > Break Apart)",
    "Border crosses itself": "remove self-intersections from the fill border",
    "Fill and Stroke color": "remove either the fill or the stroke",
    "Unconnected": "merge disjoint subpaths or split into separate elements",
}


@validate.command("fix")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--auto/--no-auto", default=True,
              help="Apply auto-fixes (currently: cleanup) when binary is available.")
@click.option("--strict", is_flag=True,
              help="Exit non-zero if any errors remain after fixes.")
@click.pass_context
def fix(ctx, project_path, auto, strict):
    """Categorize validation issues and optionally apply auto-fixes.

    Auto-fixable issues (empty paths, tiny fills) are dispatched to the
    `cleanup` extension. Everything else is reported as manual with a
    one-line suggestion drawn from inkstitch's own `steps_to_solve`.
    """
    from cli_anything_inkstitch.svg.attrs import ensure_inkstitch_namespace
    from cli_anything_inkstitch.svg.document import save_svg

    with open_project(ctx, project_path, mutate=False) as (proj, _tree):
        binary = discover(ctx.obj.get("binary_override"), proj.session)
        if not binary:
            emit(ctx, {
                "ok": None,
                "binary_status": "not_found",
                "applied": [],
                "manual": [],
                "note": "Ink/Stitch binary not installed; cannot run validation or auto-fixes.",
            })
            return

        before = parse_validation_layer(
            run_extension(binary, "troubleshoot", proj.svg_path,
                          capture_stdout=True) or b""
        )
        before_issues = before["issues"]

        auto_issues = [i for i in before_issues if i["name"] in AUTO_FIX_NAMES]
        manual_before = [i for i in before_issues if i["name"] not in AUTO_FIX_NAMES]

        applied: list[dict] = []
        after_issues = before_issues
        manual_after = manual_before

        if auto and auto_issues:
            cleanup_stdout = run_extension(binary, "cleanup", proj.svg_path,
                                           capture_stdout=True)
            if cleanup_stdout:
                new_tree = etree.ElementTree(etree.fromstring(cleanup_stdout))
                ensure_inkstitch_namespace(new_tree)
                proj.svg_sha256 = save_svg(new_tree, proj.svg_path)
                proj.save()
                applied.append({"tool": "cleanup",
                                "addresses": sorted({i["name"] for i in auto_issues})})
                after = parse_validation_layer(
                    run_extension(binary, "troubleshoot", proj.svg_path,
                                  capture_stdout=True) or b""
                )
                after_issues = after["issues"]
                manual_after = [i for i in after_issues
                                if i["name"] not in AUTO_FIX_NAMES]

        manual = [{
            **issue,
            "suggestion": MANUAL_SUGGESTIONS.get(
                issue["name"],
                "open in Ink/Stitch and follow the troubleshoot dialog",
            ),
        } for issue in manual_after]

        ok = not any(i["category"] == "error" for i in after_issues)
        emit(ctx, {
            "ok": ok,
            "binary_status": "ok",
            "before": {
                "errors": len(before["errors"]),
                "warnings": len(before["warnings"]),
                "type_warnings": len(before["type_warnings"]),
            },
            "after": {
                "errors": sum(1 for i in after_issues if i["category"] == "error"),
                "warnings": sum(1 for i in after_issues if i["category"] == "warning"),
                "type_warnings": sum(1 for i in after_issues
                                     if i["category"] == "type_warning"),
            },
            "applied": applied,
            "manual": manual,
        })
        if strict and not ok:
            raise ValidationError(
                f"{sum(1 for i in after_issues if i['category'] == 'error')} error(s) remain"
            )
