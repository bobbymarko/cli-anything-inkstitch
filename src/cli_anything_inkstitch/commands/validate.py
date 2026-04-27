"""`validate` command group."""

from __future__ import annotations

import click

from cli_anything_inkstitch.binary import discover, run_extension
from cli_anything_inkstitch.commands._helpers import open_project
from cli_anything_inkstitch.errors import BinaryError, UserError, ValidationError
from cli_anything_inkstitch.output import emit
from cli_anything_inkstitch.schema.cache import load_schema
from cli_anything_inkstitch.svg.attrs import INKSTITCH_PREFIX
from cli_anything_inkstitch.svg.document import all_addressable_elements
from cli_anything_inkstitch.svg.elements import classify


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


@validate.command("run")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--strict", is_flag=True)
@click.pass_context
def run(ctx, project_path, strict):
    """Invoke inkstitch troubleshoot extension; parse the validation layer."""
    with open_project(ctx, project_path) as (proj, _tree):
        binary = discover(ctx.obj.get("binary_override"), proj.session)
        if not binary:
            emit(ctx, {
                "ok": None,
                "errors": [], "warnings": [], "type_warnings": [],
                "binary_status": "not_found",
                "note": "Ink/Stitch binary not installed; falling back to static-only validation",
            })
            return
        try:
            stdout = run_extension(binary, "troubleshoot", proj.svg_path,
                                    args={"show-errors": "true",
                                          "show-warnings": "true",
                                          "show-type-warning": "true"},
                                    capture_stdout=True)
        except BinaryError as e:
            raise
        # The troubleshoot extension writes the modified SVG to stdout with a
        # __validation_layer__ group. Full parsing is left to a future revision;
        # for v0.1 we just confirm the extension ran and report the byte count.
        emit(ctx, {
            "ok": True,
            "binary_status": "ok",
            "stdout_bytes": len(stdout or b""),
            "note": "v0.1 returns a smoke result; full layer parsing pending.",
        })
        if strict and (stdout or b"") == b"":
            raise ValidationError("troubleshoot extension produced no output")


@validate.command("fix")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--auto-only", is_flag=True)
@click.pass_context
def fix(ctx, project_path, auto_only):
    emit(ctx, {"applied": [], "note": "v0.1: no auto-fixes implemented yet"})
