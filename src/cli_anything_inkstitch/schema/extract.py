"""Install-time schema extractor (SPEC §3.2).

Mines `@param(...)` decorators from inkstitch's `lib/elements/*.py` source to
produce a full param schema. Runs without importing inkstitch (pure AST), so it
works in environments where wxPython/inkex aren't installed.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cli_anything_inkstitch.schema.bootstrap import STITCH_TYPES as BOOTSTRAP_TYPES
from cli_anything_inkstitch.schema.bootstrap import VISUAL_COMMANDS

# ---- inkstitch source resolution ------------------------------------------------

DEFAULT_SOURCE_CANDIDATES = [
    Path(__file__).resolve().parents[3] / "inkstitch",  # sibling clone in this repo
    Path("/Applications/Inkstitch.app/Contents/Resources/lib/python3.10/site-packages/inkstitch"),
    Path("/usr/share/inkstitch"),
]

ELEMENT_FILES = ["element.py", "fill_stitch.py", "satin_column.py", "stroke.py", "clone.py"]

# Map of stitch_type -> (host_class, discriminator_param, discriminator_value).
# When `discriminator_param` is None the stitch type is selected by a defining
# attribute (e.g. `manual_stitch=True`) rather than a method enum.
STITCH_MAP: dict[str, dict[str, Any]] = {
    "auto_fill": {
        "host_class": "FillStitch",
        "discriminator": ("fill_method", "auto_fill"),
        "defining_attribute": {"name": "auto_fill", "value": "True"},
        "geometry_requirements": ["closed_filled_path"],
    },
    "legacy_fill": {
        "host_class": "FillStitch",
        "discriminator": ("fill_method", "legacy_fill"),
        "defining_attribute": {"name": "auto_fill", "value": "False"},
        "geometry_requirements": ["closed_filled_path"],
    },
    "contour_fill": {
        "host_class": "FillStitch",
        "discriminator": ("fill_method", "contour_fill"),
        "defining_attribute": {"name": "fill_method", "value": "contour_fill"},
        "geometry_requirements": ["closed_filled_path"],
    },
    "guided_fill": {
        "host_class": "FillStitch",
        "discriminator": ("fill_method", "guided_fill"),
        "defining_attribute": {"name": "fill_method", "value": "guided_fill"},
        "geometry_requirements": ["closed_filled_path", "guide_line"],
    },
    "meander_fill": {
        "host_class": "FillStitch",
        "discriminator": ("fill_method", "meander_fill"),
        "defining_attribute": {"name": "fill_method", "value": "meander_fill"},
        "geometry_requirements": ["closed_filled_path"],
    },
    "circular_fill": {
        "host_class": "FillStitch",
        "discriminator": ("fill_method", "circular_fill"),
        "defining_attribute": {"name": "fill_method", "value": "circular_fill"},
        "geometry_requirements": ["closed_filled_path"],
    },
    "tartan_fill": {
        "host_class": "FillStitch",
        "discriminator": ("fill_method", "tartan_fill"),
        "defining_attribute": {"name": "fill_method", "value": "tartan_fill"},
        "geometry_requirements": ["closed_filled_path"],
    },
    "linear_gradient_fill": {
        "host_class": "FillStitch",
        "discriminator": ("fill_method", "linear_gradient_fill"),
        "defining_attribute": {"name": "fill_method", "value": "linear_gradient_fill"},
        "geometry_requirements": ["closed_filled_path", "linear_gradient"],
    },
    "cross_stitch": {
        "host_class": "FillStitch",
        "discriminator": ("fill_method", "cross_stitch"),
        "defining_attribute": {"name": "fill_method", "value": "cross_stitch"},
        "geometry_requirements": ["closed_filled_path"],
    },
    "satin_column": {
        "host_class": "SatinColumn",
        "discriminator": ("satin_method", "satin_column"),
        "defining_attribute": {"name": "satin_column", "value": "True"},
        "geometry_requirements": ["stroke", "two_rails_with_rungs"],
    },
    "e_stitch": {
        "host_class": "SatinColumn",
        "discriminator": ("satin_method", "e_stitch"),
        "defining_attribute": {"name": "satin_method", "value": "e_stitch"},
        "geometry_requirements": ["stroke", "two_rails_with_rungs"],
    },
    "s_stitch": {
        "host_class": "SatinColumn",
        "discriminator": ("satin_method", "s_stitch"),
        "defining_attribute": {"name": "satin_method", "value": "s_stitch"},
        "geometry_requirements": ["stroke", "two_rails_with_rungs"],
    },
    "satin_zigzag": {
        "host_class": "SatinColumn",
        "discriminator": ("satin_method", "zigzag"),
        "defining_attribute": {"name": "satin_method", "value": "zigzag"},
        "geometry_requirements": ["stroke", "two_rails_with_rungs"],
    },
    "running_stitch": {
        "host_class": "Stroke",
        "discriminator": ("stroke_method", "running_stitch"),
        "defining_attribute": None,
        "geometry_requirements": ["stroke"],
    },
    "bean_stitch": {
        "host_class": "Stroke",
        "discriminator": ("stroke_method", "running_stitch"),
        "defining_attribute": {"name": "stroke_method", "value": "bean_stitch"},
        "geometry_requirements": ["stroke"],
    },
    "zigzag_stitch": {
        "host_class": "Stroke",
        "discriminator": ("stroke_method", "zigzag_stitch"),
        "defining_attribute": {"name": "stroke_method", "value": "zigzag_stitch"},
        "geometry_requirements": ["stroke"],
    },
    "ripple_stitch": {
        "host_class": "Stroke",
        "discriminator": ("stroke_method", "ripple_stitch"),
        "defining_attribute": {"name": "stroke_method", "value": "ripple_stitch"},
        "geometry_requirements": ["stroke"],
    },
    "manual_stitch": {
        "host_class": "Stroke",
        "discriminator": ("stroke_method", "manual_stitch"),
        "defining_attribute": {"name": "manual_stitch", "value": "True"},
        "geometry_requirements": ["path"],
    },
}

# Inkstitch's `@param` type strings -> our normalized type names.
TYPE_NORMALIZE = {
    "boolean": "boolean",
    "toggle": "boolean",
    "float": "float",
    "int": "int",
    "string": "string",
    "combo": "combo",
    "dropdown": "dropdown",
    "filename": "string",
}


# ---- source resolution ---------------------------------------------------------

def find_inkstitch_source(explicit: str | None = None) -> Path | None:
    if explicit:
        p = Path(explicit)
        if (p / "lib" / "elements" / "element.py").exists():
            return p
        return None
    for cand in DEFAULT_SOURCE_CANDIDATES:
        if (cand / "lib" / "elements" / "element.py").exists():
            return cand
    return None


def detect_inkstitch_version(source_root: Path) -> str:
    """Best-effort version string from inkstitch source."""
    version_file = source_root / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()
    pyproject = source_root / "pyproject.toml"
    if pyproject.exists():
        m = re.search(r'version\s*=\s*"([^"]+)"', pyproject.read_text())
        if m:
            return m.group(1)
    # Fall back to a short hash of the element source so cache invalidates on edits.
    elem = (source_root / "lib" / "elements" / "element.py").read_text()
    import hashlib
    return f"src-{hashlib.sha1(elem.encode()).hexdigest()[:10]}"


# ---- AST extraction ------------------------------------------------------------

def _safe_literal(node: ast.AST) -> Any:
    """Return a Python value for a static AST node, or a structural marker if dynamic."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_safe_literal(e) for e in node.elts]
    if isinstance(node, ast.Dict):
        return {_safe_literal(k): _safe_literal(v) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _safe_literal(node.operand)
        return -v if isinstance(v, (int, float)) else None
    # `_('translated')` -> just take the string arg
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_":
        if node.args and isinstance(node.args[0], ast.Constant):
            return node.args[0].value
    # `ParamOption('id', _('label'))` -> id
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ParamOption":
        if node.args:
            return _safe_literal(node.args[0])
    # Name references and complex calls -> mark as dynamic.
    return None


def _extract_param_call(call: ast.Call) -> dict[str, Any] | None:
    """Convert a `@param(...)` call AST node into a param info dict."""
    if not call.args:
        return None
    name = _safe_literal(call.args[0])
    if not isinstance(name, str):
        return None
    info: dict[str, Any] = {"name": name}
    if len(call.args) > 1:
        info["gui_text"] = _safe_literal(call.args[1])
    for kw in call.keywords:
        if kw.arg is None:
            continue
        info[kw.arg] = _safe_literal(kw.value)
    return info


def _resolve_method_options(class_node: ast.ClassDef) -> dict[str, list[str]]:
    """Find `_fill_methods = [ParamOption('auto_fill', ...), ...]` style class attrs.

    Returns {assign_name: [option_value, ...]}.
    """
    out: dict[str, list[str]] = {}
    for stmt in class_node.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            value = _safe_literal(stmt.value)
            if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
                out[stmt.targets[0].id] = value
    return out


def parse_element_file(path: Path) -> dict[str, dict]:
    """Return {class_name: {"params": [param_info, ...], "options": {assign_name: [...]}, "order": int}}."""
    tree = ast.parse(path.read_text(), filename=str(path))
    classes: dict[str, dict] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        params: list[dict] = []
        for stmt in node.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in stmt.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                if isinstance(func, ast.Name) and func.id == "param":
                    info = _extract_param_call(dec)
                    if info:
                        params.append(info)
        if params:
            classes[node.name] = {
                "params": params,
                "options": _resolve_method_options(node),
                "lineno": node.lineno,
            }
    return classes


def extract_all(source_root: Path) -> dict[str, dict]:
    """Extract param metadata from every element source file. Merge classes by name."""
    merged: dict[str, dict] = {}
    for fname in ELEMENT_FILES:
        path = source_root / "lib" / "elements" / fname
        if not path.exists():
            continue
        for cname, cdata in parse_element_file(path).items():
            if cname in merged:
                merged[cname]["params"].extend(cdata["params"])
                merged[cname]["options"].update(cdata["options"])
            else:
                merged[cname] = cdata
    return merged


# ---- schema assembly -----------------------------------------------------------

def _normalize_param(raw: dict) -> dict:
    """Translate a raw extracted @param dict into the schema-shape used by the rest of the CLI."""
    name = raw["name"]
    out: dict[str, Any] = {}
    ptype = TYPE_NORMALIZE.get(raw.get("type") or "", raw.get("type") or "string")
    out["type"] = ptype
    if "gui_text" in raw and raw["gui_text"] is not None:
        out["gui_text"] = raw["gui_text"]
    for key in ("unit", "tooltip", "group", "sort_index", "enables", "inverse"):
        if raw.get(key) is not None:
            out[key] = raw[key]
    if "options" in raw and raw["options"] is not None:
        opts = raw["options"]
        if isinstance(opts, list) and all(isinstance(o, str) for o in opts):
            out["options"] = opts
            if ptype in ("combo", "dropdown") and "enum" not in out:
                out["enum"] = opts
    if "select_items" in raw and raw["select_items"] is not None:
        si = raw["select_items"]
        if isinstance(si, list):
            cleaned: list[list[str]] = []
            for item in si:
                if isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item):
                    cleaned.append(item)
            if cleaned:
                out["select_items"] = cleaned
    if raw.get("default") is not None:
        default = raw["default"]
        if ptype == "boolean":
            out["default"] = bool(default) if isinstance(default, (bool, int)) else default
        elif ptype == "float":
            try:
                out["default"] = float(default)
            except (TypeError, ValueError):
                out["default"] = default
        elif ptype == "int":
            try:
                out["default"] = int(default)
            except (TypeError, ValueError):
                out["default"] = default
        elif ptype in ("combo", "dropdown") and isinstance(default, int):
            opts = out.get("options")
            if opts and 0 <= default < len(opts):
                out["default"] = opts[default]
            else:
                out["default"] = default
        else:
            out["default"] = default
    if name.endswith("_mm") and "unit" not in out:
        out["unit"] = "mm"
    if name.endswith("_percent") and "unit" not in out:
        out["unit"] = "%"
    return out


def _params_for_stitch_type(
    classes: dict[str, dict],
    host_class: str,
    discriminator: tuple[str, str] | None,
) -> dict[str, dict]:
    """Collect base-class + host-class params filtered by select_items discriminator."""
    base = classes.get("EmbroideryElement", {}).get("params", [])
    host = classes.get(host_class, {}).get("params", [])
    disc_key = discriminator[0] if discriminator else None
    out: dict[str, dict] = {}
    for raw in [*base, *host]:
        si = raw.get("select_items")
        if not si:
            out[raw["name"]] = _normalize_param(raw)
            continue
        # If any select_item references our discriminator key, only include when the value matches.
        # If all select_items reference unrelated keys (e.g. split_method, lock_start), include the param.
        keyed_to_disc = [item for item in si
                         if isinstance(item, list) and len(item) == 2 and item[0] == disc_key]
        if not keyed_to_disc:
            out[raw["name"]] = _normalize_param(raw)
            continue
        if discriminator and any(tuple(item) == discriminator for item in keyed_to_disc):
            out[raw["name"]] = _normalize_param(raw)
    return out


def assemble_schema(classes: dict[str, dict], version: str) -> dict:
    """Build the schema dict (same shape as bootstrap_schema) from extracted classes."""
    stitch_types: dict[str, dict] = {}
    for st_name, mapping in STITCH_MAP.items():
        host = mapping["host_class"]
        disc = mapping.get("discriminator")
        params = _params_for_stitch_type(classes, host, disc)
        # Carry forward bootstrap min/max ranges where AST can't see them.
        if st_name in BOOTSTRAP_TYPES:
            for pname, bp in BOOTSTRAP_TYPES[st_name]["params"].items():
                merged = params.get(pname, {}).copy()
                for key in ("min", "max"):
                    if key in bp and key not in merged:
                        merged[key] = bp[key]
                if "default" in bp and "default" not in merged:
                    merged["default"] = bp["default"]
                if "type" not in merged and "type" in bp:
                    merged["type"] = bp["type"]
                if "gui_text" in bp and "gui_text" not in merged:
                    merged["gui_text"] = bp["gui_text"]
                if merged:
                    params[pname] = merged
        stitch_types[st_name] = {
            "defining_attribute": mapping["defining_attribute"],
            "geometry_requirements": mapping["geometry_requirements"],
            "host_class": host,
            "params": params,
        }
    return {
        "inkstitch_version": version,
        "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "namespace": "http://inkstitch.org/namespace",
        "stitch_types": stitch_types,
        "extensions": {},
        "commands": VISUAL_COMMANDS,
        "machine_formats": [],
        "source": {
            "kind": "ast-extract",
            "classes": sorted(classes.keys()),
            "param_count": sum(len(c["params"]) for c in classes.values()),
        },
    }


def extract_schema(source_root: Path | None = None) -> dict:
    """Top-level: locate inkstitch source, AST-extract, assemble schema."""
    root = source_root or find_inkstitch_source()
    if root is None:
        raise FileNotFoundError(
            "inkstitch source not found. Set INKSTITCH_SOURCE or pass --source explicitly."
        )
    classes = extract_all(root)
    if not classes:
        raise RuntimeError(f"no @param decorators found under {root}")
    version = detect_inkstitch_version(root)
    schema = assemble_schema(classes, version)
    schema["source"]["root"] = str(root)
    return schema


def write_cache(schema: dict, version: str | None = None) -> Path:
    from cli_anything_inkstitch.schema.cache import cache_file
    v = version or schema.get("inkstitch_version", "extracted")
    path = cache_file(v)
    path.write_text(json.dumps(schema, indent=2, sort_keys=True))
    return path
