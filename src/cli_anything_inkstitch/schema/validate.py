"""Validate param values against the schema."""

from __future__ import annotations

from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.svg.attrs import parse_bool


def validate_param(stitch_type_schema: dict, param_name: str, raw_value) -> str:
    """Validate raw_value (str|bool|int|float) for the given param.

    Returns the normalized string form (suitable for writing to the SVG attr).
    Raises UserError on failure.
    """
    params = stitch_type_schema.get("params", {})
    if param_name not in params:
        raise UserError(
            f"unknown param '{param_name}' for this stitch type "
            f"(known: {', '.join(sorted(params)) or 'none'})"
        )
    spec = params[param_name]
    ptype = spec.get("type")

    if ptype == "boolean":
        if isinstance(raw_value, bool):
            return "True" if raw_value else "False"
        try:
            return "True" if parse_bool(str(raw_value)) else "False"
        except ValueError as e:
            raise UserError(f"{param_name}: {e}") from e

    if ptype == "int":
        try:
            v = int(str(raw_value))
        except ValueError as e:
            raise UserError(f"{param_name}: not an int: {raw_value!r}") from e
        _check_range(param_name, v, spec)
        return str(v)

    if ptype == "float":
        try:
            v = float(str(raw_value))
        except ValueError as e:
            raise UserError(f"{param_name}: not a float: {raw_value!r}") from e
        _check_range(param_name, v, spec)
        return _fmt_float(v)

    if ptype == "string":
        s = str(raw_value)
        enum = spec.get("enum")
        if enum and s not in enum:
            raise UserError(
                f"{param_name}: must be one of {enum}, got {s!r}"
            )
        return s

    # Unknown type — pass through as string
    return str(raw_value)


def _check_range(name: str, value, spec: dict) -> None:
    lo = spec.get("min")
    hi = spec.get("max")
    if lo is not None and value < lo:
        raise UserError(f"{name}: {value} below min {lo}")
    if hi is not None and value > hi:
        raise UserError(f"{name}: {value} above max {hi}")


def _fmt_float(v: float) -> str:
    if v == int(v):
        return f"{int(v)}"
    return f"{v:g}"


def validate_geometry(stitch_type: str, schema: dict, elem) -> list[str]:
    """Return a list of geometry compatibility issues (empty if ok)."""
    from cli_anything_inkstitch.svg.elements import has_fill, has_stroke

    st = schema["stitch_types"].get(stitch_type)
    if not st:
        return [f"unknown stitch type: {stitch_type}"]
    issues: list[str] = []
    reqs = st.get("geometry_requirements", [])
    if "stroke" in reqs and not has_stroke(elem):
        issues.append(f"stitch type '{stitch_type}' requires the path to have a stroke")
    if "closed_filled_path" in reqs and not has_fill(elem):
        issues.append(f"stitch type '{stitch_type}' requires a fill color")
    return issues
