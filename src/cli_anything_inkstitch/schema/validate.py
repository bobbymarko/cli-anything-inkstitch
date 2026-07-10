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
    kind = spec.get("value_kind")

    # value_kind is the engine's READ contract (mined from get_*_param calls
    # in the inkstitch source) and outranks the GUI-declared type when they
    # disagree — e.g. pull_compensation_percent is declared float but the
    # engine reads a space-separated per-side list; staggers is declared int
    # but read as float. Dropdowns keep their index handling below (their
    # get_int_param read is exactly the index contract).
    if ptype != "dropdown":
        if kind in ("multi_float", "multi_int"):
            parts = str(raw_value).replace(",", " ").split()
            if not parts:
                raise UserError(f"{param_name}: expected one or more values")
            out = []
            for part in parts:
                try:
                    v = int(part) if kind == "multi_int" else float(part)
                except ValueError as e:
                    raise UserError(
                        f"{param_name}: {part!r} is not "
                        f"{'an int' if kind == 'multi_int' else 'a number'} "
                        f"(space-separated list allowed)") from e
                _check_range(param_name, v, spec)
                out.append(str(v) if kind == "multi_int" else _fmt_float(v))
            return " ".join(out)
        if kind == "float" and ptype == "int":
            ptype = "float"     # engine is more permissive than the GUI type
        if kind == "string" and ptype in ("float", "int"):
            # declared numeric but read as a raw string (e.g.
            # fill_underlay_angle: space-separated multi-pass angles)
            parts = str(raw_value).replace(",", " ").split()
            try:
                [float(p) for p in parts]
            except ValueError as e:
                raise UserError(
                    f"{param_name}: expected number(s), got {raw_value!r}") from e
            return " ".join(parts)

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

    if ptype == "dropdown":
        # Ink/Stitch reads dropdown params with get_int_param(): the stored
        # value is the option INDEX; labels are GUI-only. Accept an index, an
        # exact label, or a snake_case label form — normalize to the index
        # string. (These used to pass through unvalidated, so values like
        # "miter" got written and were silently ignored at stitch time.)
        options = [str(o) for o in (spec.get("options") or spec.get("enum") or [])]
        s = str(raw_value).strip()
        if not options:
            return s
        if s.isdigit() and int(s) < len(options):
            return s
        token = s.lower().replace(" ", "_")
        for i, o in enumerate(options):
            if token == o.lower().replace(" ", "_"):
                return str(i)
        raise UserError(
            f"{param_name}: must be an option index (0–{len(options) - 1}) "
            f"or one of {options}, got {s!r}"
        )

    if ptype == "combo":
        # Ink/Stitch reads combo params with plain get_param(): the stored
        # value is the ParamOption id string, NOT an index (fill_stitch.py
        # fill_method → get_param('fill_method', 'auto_fill'); stroke.py
        # stroke_method likewise). An unknown value is silently ignored at
        # stitch time (the getter falls back to its default), so reject
        # anything outside the mined ids. Accept an id verbatim or a GUI
        # label ("Contour Fill" → contour_fill).
        options = [str(o) for o in (spec.get("options") or spec.get("enum") or [])]
        s = str(raw_value).strip()
        if not options:
            return s     # options the extractor couldn't mine — pass through
        if s in options:
            return s
        token = s.lower().replace(" ", "_")
        labels = [str(x) for x in (spec.get("option_labels") or [])]
        for i, lab in enumerate(labels):
            if token == lab.lower().replace(" ", "_"):
                return options[i]
        for o in options:
            if token == o.lower():
                return o
        raise UserError(
            f"{param_name}: must be one of {options}, got {s!r}"
        )

    # Unknown type (e.g. random_seed) — pass through as string
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
