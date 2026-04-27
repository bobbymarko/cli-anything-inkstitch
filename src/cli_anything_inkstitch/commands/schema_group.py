"""`schema` command group — introspect param schema."""

from __future__ import annotations

import click

from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.output import emit
from cli_anything_inkstitch.schema.cache import load_schema


@click.group("schema")
def schema_group():
    """Introspect the inkstitch param schema."""


@schema_group.command("list-stitch-types")
@click.option("--refresh-schema", is_flag=True)
@click.pass_context
def list_stitch_types(ctx, refresh_schema):
    schema = load_schema(refresh=refresh_schema)
    types = []
    for name, st in schema["stitch_types"].items():
        types.append({
            "name": name,
            "defining_attribute": st.get("defining_attribute"),
            "geometry_requirements": st.get("geometry_requirements", []),
            "param_count": len(st.get("params", {})),
        })
    emit(ctx, {"stitch_types": types})


@schema_group.command("get-stitch-type")
@click.option("--type", "stitch_type", required=True)
@click.option("--refresh-schema", is_flag=True)
@click.pass_context
def get_stitch_type(ctx, stitch_type, refresh_schema):
    schema = load_schema(refresh=refresh_schema)
    st = schema["stitch_types"].get(stitch_type)
    if not st:
        raise UserError(f"unknown stitch type: {stitch_type}")
    emit(ctx, {"stitch_type": stitch_type, **st})


@schema_group.command("get-extension")
@click.option("--extension", "ext_name", required=True)
@click.pass_context
def get_extension(ctx, ext_name):
    schema = load_schema()
    ext = schema.get("extensions", {}).get(ext_name)
    if not ext:
        # bootstrap schema doesn't enumerate extensions; tell the user honestly
        raise UserError(
            f"extension schema for '{ext_name}' not in cache. "
            "v0.1 bootstrap schema does not enumerate inkstitch extensions; "
            "the install-time extractor (SPEC §3) will populate this."
        )
    emit(ctx, {"extension": ext_name, **ext})


@schema_group.command("list-commands")
@click.pass_context
def list_commands(ctx):
    schema = load_schema()
    emit(ctx, {"commands": schema["commands"]})


@schema_group.command("list-machine-formats")
@click.pass_context
def list_machine_formats(ctx):
    """Live introspect via pyembroidery."""
    try:
        from pyembroidery import EmbPattern  # noqa: F401
        from pyembroidery.PyEmbroidery import supported_formats  # type: ignore
        formats = []
        for f in supported_formats():
            formats.append({
                "extension": f.get("extension"),
                "extensions": list(f.get("extensions", ())),
                "description": f.get("description", ""),
                "writer": "writer" in f,
                "reader": "reader" in f,
            })
        emit(ctx, {"machine_formats": formats})
    except Exception as e:  # noqa: BLE001
        emit(ctx, {"machine_formats": [], "error": str(e)})
