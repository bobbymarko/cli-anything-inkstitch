"""`params` command group — the core digitization surface."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from cli_anything_inkstitch.commands._helpers import (
    open_project,
    record,
    require_id,
    xpath_for_id,
)
from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.history import attr_diff, multi_patch
from cli_anything_inkstitch.output import emit
from cli_anything_inkstitch.schema.cache import load_schema, schema_warning
from cli_anything_inkstitch.schema.validate import validate_geometry, validate_param
from cli_anything_inkstitch.svg.attrs import (
    INKSTITCH_PREFIX,
    del_inkstitch,
    get_inkstitch,
    qname,
    set_inkstitch,
)
from cli_anything_inkstitch.svg.elements import classify, set_params_on


@click.group("params")
def params():
    """Set, get, copy stitch params on SVG elements."""


SET_CONTEXT = {"ignore_unknown_options": True, "allow_extra_args": True}


# Stitch types -> the (attr_name, attr_value) that "marks" the type.
# Mirrors STITCH_TYPES[*]['defining_attribute'] in schema/bootstrap.py.
def _defining(stitch_type: str, schema: dict) -> tuple[str, str] | None:
    st = schema["stitch_types"].get(stitch_type)
    if not st:
        return None
    da = st.get("defining_attribute")
    if not da:
        return None
    return da["name"], da["value"]


def _strip_competing_definers(elem, target: str, schema: dict) -> dict[str, str | None]:
    """Remove other stitch types' defining attributes so element classifies as `target`.

    Returns the before-map (qname -> old value) for history.
    """
    keep = _defining(target, schema)
    keep_name = keep[0] if keep else None
    before: dict[str, str | None] = {}
    for st_name, st in schema["stitch_types"].items():
        da = st.get("defining_attribute")
        if not da:
            continue
        if da["name"] == keep_name:
            continue
        # only strip if value matches (don't accidentally remove a shared key)
        existing = get_inkstitch(elem, da["name"])
        if existing == da["value"]:
            before[qname(da["name"])] = existing
            del_inkstitch(elem, da["name"])
    return before


def _apply_params_to_element(elem, stitch_type, extra, schema, force):
    """Apply --stitch-type/params to one element; returns (before, after).

    Raises UserError on validation failure without mutating the element
    beyond what already validated (defining-attr and each param write happen
    only after their own validation).
    """
    effective = stitch_type or classify(elem)
    st_schema = schema["stitch_types"].get(effective)
    if not st_schema:
        raise UserError(f"unknown stitch type: {effective}")

    if stitch_type and not force:
        issues = validate_geometry(stitch_type, schema, elem)
        if issues:
            raise UserError("; ".join(issues) + " (use --force to override)")

    # validate every param BEFORE writing anything, so a failing element is
    # left untouched (matters in bulk mode where we continue past failures)
    normalized_params = {
        raw_name.replace("-", "_"):
            validate_param(st_schema, raw_name.replace("-", "_"), raw_value)
        for raw_name, raw_value in extra.items()
    }

    before: dict[str, str | None] = {}
    after: dict[str, str | None] = {}

    if stitch_type:
        before.update(_strip_competing_definers(elem, stitch_type, schema))
        da = _defining(stitch_type, schema)
        if da:
            key = qname(da[0])
            old = elem.get(key)
            if old != da[1]:
                before.setdefault(key, old)
                after[key] = da[1]
                elem.set(key, da[1])

    for attr_local, normalized in normalized_params.items():
        key = qname(attr_local)
        old = elem.get(key)
        if old != normalized:
            before.setdefault(key, old)
            after[key] = normalized
            elem.set(key, normalized)

    return before, after


def _capability_warning(changed: dict) -> str | None:
    """Measured per-binary verdict: warn when a fill_method just written
    provably has no effect on the installed engine (schema/probe.py)."""
    try:
        from cli_anything_inkstitch.schema.probe import get_cached
        probe = get_cached()
        method = changed.get("fill_method")
        if probe and method in probe.get("no_effect", []):
            return (
                f"fill_method='{method}' has no effect on the installed "
                f"Ink/Stitch ({probe.get('binary_version')}) — it stitches "
                "as plain auto fill (measured; the option comes from newer "
                "engine source)")
    except Exception:  # noqa: BLE001 — advisory only
        pass
    return None


@params.command("set", context_settings=SET_CONTEXT)
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", default=None)
@click.option("--ids", "ids_csv", default=None,
              help="Comma-separated element ids: apply the same assignments "
                   "to every one, recorded as ONE undoable history entry.")
@click.option("--stitch-type", "stitch_type", default=None,
              help="Optional stitch type to assign (validates geometry).")
@click.option("--force", is_flag=True, help="Skip geometry-compatibility check.")
@click.option("--refresh-schema", is_flag=True,
              help="Re-extract the schema from inkstitch source before loading.")
@click.pass_context
def set_cmd(ctx, project_path, svg_id, ids_csv, stitch_type, force, refresh_schema):
    """Set --stitch-type and any --<param>=<value> on element(s).

    Bulk mode (--ids a,b,c) applies the same assignments to every element in
    one process and one history entry (undo reverts the whole batch);
    per-element validation failures are reported in the payload without
    aborting the rest.
    """
    from cli_anything_inkstitch.binary import installed_version
    binary_version = installed_version(ctx.obj.get("binary_override") if ctx.obj else None)
    schema = load_schema(refresh=refresh_schema, prefer_version=binary_version)
    extra = _parse_extra_kv_args(ctx.args)

    if stitch_type is None and not extra:
        raise UserError("nothing to set: pass --stitch-type and/or one or more --<param>=<value>")
    if bool(svg_id) == bool(ids_csv):
        raise UserError("pass exactly one of --id or --ids")
    ids = [svg_id] if svg_id else [s.strip() for s in ids_csv.split(",") if s.strip()]
    bulk = ids_csv is not None

    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        patches = []
        applied = []
        errors = []
        changed_union: dict[str, str] = {}
        for one_id in ids:
            try:
                elem = require_id(tree, one_id)
                before, after = _apply_params_to_element(
                    elem, stitch_type, extra, schema, force)
            except UserError as exc:
                if not bulk:
                    raise
                errors.append({"id": one_id, "error": str(exc)})
                continue
            if not after:
                applied.append({"id": one_id, "no_op": True})
                continue
            patches.append(attr_diff(xpath_for_id(one_id), before, after))
            proj.elements.setdefault(one_id, {})
            proj.elements[one_id]["stitch_type"] = classify(elem)
            proj.elements[one_id]["set_params"] = set_params_on(elem)
            changed = {k.replace(INKSTITCH_PREFIX, ""): v for k, v in after.items()}
            changed_union.update(changed)
            applied.append({"id": one_id, "stitch_type": classify(elem),
                            "changed": changed})

        if patches:
            if bulk:
                record(proj.history, f"params set --ids {','.join(ids)}",
                       multi_patch(patches))
            else:
                record(proj.history, f"params set --id {ids[0]}", patches[0])

        if not bulk:
            result = applied[0] if applied else {"id": ids[0], "no_op": True}
            payload = dict(result)
        else:
            payload = {
                "ids": ids,
                "applied": applied,
                "errors": errors,
                "history_entries": 1 if patches else 0,
            }
        if (w := schema_warning(schema, binary_version)):
            payload["schema_warning"] = w
        if (cw := _capability_warning(changed_union)):
            payload["capability_warning"] = cw
        emit(ctx, payload)


@params.command("unset")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.option("--param", "names", multiple=True, required=True)
@click.pass_context
def unset(ctx, project_path, svg_id, names):
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        elem = require_id(tree, svg_id)
        before: dict[str, str | None] = {}
        after: dict[str, str | None] = {}
        for name in names:
            local = name.replace("-", "_")
            key = qname(local)
            if key in elem.attrib:
                before[key] = elem.attrib[key]
                after[key] = None
                del elem.attrib[key]
        if before:
            record(proj.history, f"params unset --id {svg_id} {' '.join('--param ' + n for n in names)}",
                   attr_diff(xpath_for_id(svg_id), before, after))
        proj.elements.setdefault(svg_id, {})
        proj.elements[svg_id]["stitch_type"] = classify(elem)
        proj.elements[svg_id]["set_params"] = set_params_on(elem)
        emit(ctx, {"id": svg_id, "unset": [n for n in names]})


@params.command("get")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.option("--param", "name", default=None)
@click.option("--refresh-schema", is_flag=True,
              help="Re-extract the schema from inkstitch source before loading.")
@click.pass_context
def get_cmd(ctx, project_path, svg_id, name, refresh_schema):
    schema = load_schema(refresh=refresh_schema)
    with open_project(ctx, project_path) as (_proj, tree):
        elem = require_id(tree, svg_id)
        st_name = classify(elem)
        st_schema = schema["stitch_types"].get(st_name) or {"params": {}}
        result = {"id": svg_id, "stitch_type": st_name, "params": {}}
        names = [name] if name else list(st_schema["params"].keys()) + sorted(set(set_params_on(elem)) - set(st_schema["params"].keys()))
        for n in names:
            local = n.replace("-", "_")
            spec = st_schema["params"].get(local, {})
            raw = elem.get(qname(local))
            value = _coerce_for_display(raw, spec) if raw is not None else spec.get("default")
            result["params"][local] = {
                "value": value,
                "default": spec.get("default"),
                "type": spec.get("type", "string"),
                "unit": spec.get("unit"),
                "is_default": raw is None,
                "set": raw is not None,
            }
        emit(ctx, result)


@params.command("copy")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--from", "src_id", required=True)
@click.option("--to", "dst_ids", required=True, multiple=True)
@click.option("--only", default=None, help="Comma-separated list of params to copy.")
@click.option("--except", "excluded", default=None, help="Comma-separated list to skip.")
@click.pass_context
def copy_cmd(ctx, project_path, src_id, dst_ids, only, excluded):
    only_set = set((only or "").split(",")) - {""}
    except_set = set((excluded or "").split(",")) - {""}
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        src = require_id(tree, src_id)
        src_attrs = {k: v for k, v in src.attrib.items()
                     if isinstance(k, str) and k.startswith(INKSTITCH_PREFIX)}
        if only_set:
            src_attrs = {k: v for k, v in src_attrs.items()
                         if k.replace(INKSTITCH_PREFIX, "") in only_set}
        if except_set:
            src_attrs = {k: v for k, v in src_attrs.items()
                         if k.replace(INKSTITCH_PREFIX, "") not in except_set}
        results = []
        for dst_id in dst_ids:
            dst = require_id(tree, dst_id)
            before, after = {}, {}
            for k, v in src_attrs.items():
                old = dst.get(k)
                if old != v:
                    before[k] = old
                    after[k] = v
                    dst.set(k, v)
            if after:
                record(proj.history, f"params copy --from {src_id} --to {dst_id}",
                       attr_diff(xpath_for_id(dst_id), before, after))
            results.append({"id": dst_id, "applied": list(after.keys())})
        emit(ctx, {"from": src_id, "to": results})


@params.command("apply-preset")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.option("--preset", required=True)
@click.option("--refresh-schema", is_flag=True,
              help="Re-extract the schema from inkstitch source before loading.")
@click.pass_context
def apply_preset(ctx, project_path, svg_id, preset, refresh_schema):
    pf = _preset_path(preset)
    if not pf.exists():
        raise UserError(f"preset not found: {preset} (looked in {pf.parent})")
    data = json.loads(pf.read_text())
    schema = load_schema(refresh=refresh_schema)
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        elem = require_id(tree, svg_id)
        stitch_type = data.get("stitch_type") or classify(elem)
        st_schema = schema["stitch_types"].get(stitch_type)
        if not st_schema:
            raise UserError(f"preset references unknown stitch type: {stitch_type}")
        before: dict[str, str | None] = {}
        after: dict[str, str | None] = {}
        before.update(_strip_competing_definers(elem, stitch_type, schema))
        da = _defining(stitch_type, schema)
        if da:
            key = qname(da[0])
            old = elem.get(key)
            if old != da[1]:
                before.setdefault(key, old)
                after[key] = da[1]
                elem.set(key, da[1])
        for k, v in data.get("params", {}).items():
            normalized = validate_param(st_schema, k, v)
            key = qname(k)
            old = elem.get(key)
            if old != normalized:
                before.setdefault(key, old)
                after[key] = normalized
                elem.set(key, normalized)
        if after:
            record(proj.history, f"params apply-preset --id {svg_id} --preset {preset}",
                   attr_diff(xpath_for_id(svg_id), before, after))
        emit(ctx, {"id": svg_id, "preset": preset, "applied": list(after.keys())})


@params.command("save-preset")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--id", "svg_id", required=True)
@click.option("--preset", required=True)
@click.pass_context
def save_preset(ctx, project_path, svg_id, preset):
    with open_project(ctx, project_path) as (_proj, tree):
        elem = require_id(tree, svg_id)
        st_name = classify(elem)
        params_dict = {}
        for local, value in [(k.replace(INKSTITCH_PREFIX, ""), v) for k, v in elem.attrib.items()
                              if isinstance(k, str) and k.startswith(INKSTITCH_PREFIX)]:
            params_dict[local] = value
        pf = _preset_path(preset)
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(json.dumps({
            "name": preset,
            "stitch_type": st_name,
            "params": params_dict,
        }, indent=2))
        emit(ctx, {"preset": preset, "path": str(pf), "stitch_type": st_name,
                   "param_count": len(params_dict)})


@params.command("list-presets")
@click.pass_context
def list_presets(ctx):
    d = _preset_dir()
    presets = []
    if d.exists():
        for p in sorted(d.glob("*.json")):
            try:
                data = json.loads(p.read_text())
                presets.append({
                    "name": p.stem,
                    "stitch_type": data.get("stitch_type"),
                    "param_count": len(data.get("params", {})),
                })
            except json.JSONDecodeError:
                presets.append({"name": p.stem, "error": "invalid_json"})
    emit(ctx, {"presets": presets, "directory": str(d)})


# ---- helpers ----

def _preset_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "cli-anything-inkstitch" / "presets"


def _preset_path(name: str) -> Path:
    return _preset_dir() / f"{name}.json"


def _coerce_for_display(raw: str, spec: dict):
    t = spec.get("type")
    if t == "boolean":
        return raw.lower() in ("true", "1", "yes")
    if t == "int":
        try:
            return int(raw)
        except ValueError:
            return raw
    if t == "float":
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def _parse_extra_kv_args(args: list[str]) -> dict[str, str]:
    """Parse Click's leftover args of the form `--name value` or `--name=value`."""
    out: dict[str, str] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if not a.startswith("--"):
            raise UserError(f"unexpected positional arg: {a}")
        if "=" in a:
            k, v = a[2:].split("=", 1)
            out[k] = v
            i += 1
        else:
            k = a[2:]
            if i + 1 >= len(args):
                raise UserError(f"missing value for --{k}")
            out[k] = args[i + 1]
            i += 2
    return out
