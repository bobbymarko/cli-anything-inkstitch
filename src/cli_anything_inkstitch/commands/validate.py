"""`validate` command group."""

from __future__ import annotations

import re

import click
from lxml import etree

from cli_anything_inkstitch.binary import discover, run_extension
from cli_anything_inkstitch.commands._helpers import open_project
from cli_anything_inkstitch.errors import UserError, ValidationError
from cli_anything_inkstitch.output import emit
from cli_anything_inkstitch.schema.cache import load_schema
from cli_anything_inkstitch.svg.attrs import INKSCAPE_NS, INKSTITCH_PREFIX, SVG_NS
from cli_anything_inkstitch.svg.document import all_addressable_elements
from cli_anything_inkstitch.svg.elements import classify

PIXELS_PER_MM = 96.0 / 25.4  # inkstitch's PIXELS_PER_MM


@click.group("validate")
def validate():
    """Static and binary-backed validation."""


@validate.command("static")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--strict", is_flag=True)
@click.pass_context
def static(ctx, project_path, strict):
    schema = load_schema()
    issues = []
    with open_project(ctx, project_path) as (proj, tree):
        if tree is None:
            raise UserError("project has no SVG attached")
        for elem in all_addressable_elements(tree):
            if not elem.get("id"):
                continue
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


CATEGORY_GROUP_IDS = {
    "__validation_errors__": "error",
    "__validation_warnings__": "warning",
    "__validation_ignored__": "type_warning",
}

# `m X,Y` or `M X Y` from the pointer path's `d` attribute.
_POINTER_RE = re.compile(r"[mM]\s*(-?[\d.]+)\s*[, ]\s*(-?[\d.]+)")


def parse_validation_layer(svg_bytes: bytes) -> dict:
    """Parse the SVG returned by the troubleshoot extension.

    Returns {"errors": [...], "warnings": [...], "type_warnings": [...], "issues": [...]}.
    Each issue dict has: category, name, label, x, y, x_mm, y_mm.
    """
    if not svg_bytes:
        return {"errors": [], "warnings": [], "type_warnings": [], "issues": []}
    try:
        root = etree.fromstring(svg_bytes)
    except etree.XMLSyntaxError as e:
        raise ValidationError(f"troubleshoot output not parseable as SVG: {e}") from e

    layer = root.find(f".//{{{SVG_NS}}}*[@id='__validation_layer__']")
    if layer is None:
        # Newer/older inkstitch may omit the layer on success; treat as clean.
        return {"errors": [], "warnings": [], "type_warnings": [], "issues": []}

    issues: list[dict] = []
    for cat_id, cat_name in CATEGORY_GROUP_IDS.items():
        cat_group = layer.find(f"./{{{SVG_NS}}}*[@id='{cat_id}']")
        if cat_group is None:
            continue
        for problem_group in cat_group.findall(f"./{{{SVG_NS}}}g"):
            problem_name = problem_group.get(f"{{{INKSCAPE_NS}}}label") or "Unknown"
            issues.extend(_extract_problem_group(problem_group, cat_name, problem_name))

    grouped = {"errors": [], "warnings": [], "type_warnings": []}
    for issue in issues:
        if issue["category"] == "error":
            grouped["errors"].append(issue)
        elif issue["category"] == "warning":
            grouped["warnings"].append(issue)
        else:
            grouped["type_warnings"].append(issue)
    grouped["issues"] = issues
    return grouped


def _extract_problem_group(group, category: str, problem_name: str) -> list[dict]:
    """Inkstitch inserts pointers at index 0 (reverse-order) and appends texts (in order).
    Pair them by reversing pointers."""
    pointers = []
    texts = []
    for child in group:
        local = etree.QName(child.tag).localname
        if local == "path" and (child.get("id") or "").startswith("inkstitch__invalid_pointer__"):
            d = child.get("d") or ""
            m = _POINTER_RE.search(d)
            if m:
                pointers.append((float(m.group(1)), float(m.group(2))))
        elif local == "text":
            texts.append(_first_tspan_text(child))
    pointers.reverse()  # restore insertion order

    issues: list[dict] = []
    for i, (x, y) in enumerate(pointers):
        text = texts[i] if i < len(texts) else ""
        label = _extract_label_from_text(text, problem_name)
        issues.append({
            "category": category,
            "name": problem_name,
            "label": label,
            "x": x,
            "y": y,
            "x_mm": round(x / PIXELS_PER_MM, 3),
            "y_mm": round(y / PIXELS_PER_MM, 3),
        })
    return issues


def _first_tspan_text(text_elem) -> str:
    for child in text_elem.iter(f"{{{SVG_NS}}}tspan"):
        if child.text:
            return child.text
    return text_elem.text or ""


def _extract_label_from_text(tspan_text: str, problem_name: str) -> str:
    """Inkstitch formats tspan as `<name>` or `<name> (<label>)`. Recover the label."""
    if not tspan_text:
        return ""
    stripped = tspan_text.strip()
    if stripped.startswith(problem_name):
        rest = stripped[len(problem_name):].strip()
        if rest.startswith("(") and rest.endswith(")"):
            return rest[1:-1]
    # Fall back to any "(...)" in the string.
    m = re.search(r"\(([^)]+)\)\s*$", stripped)
    return m.group(1) if m else ""


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


@validate.command("fix")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--auto-only", is_flag=True)
@click.pass_context
def fix(ctx, project_path, auto_only):
    emit(ctx, {"applied": [], "note": "v0.1: no auto-fixes implemented yet"})
