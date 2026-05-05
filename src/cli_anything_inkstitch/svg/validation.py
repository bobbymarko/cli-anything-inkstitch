"""Parse the SVG validation layer emitted by Ink/Stitch's troubleshoot extension."""

from __future__ import annotations

import re

from lxml import etree

from cli_anything_inkstitch.errors import ValidationError
from cli_anything_inkstitch.svg.attrs import INKSCAPE_NS, SVG_NS
from cli_anything_inkstitch.svg.geometry import PIXELS_PER_MM

CATEGORY_GROUP_IDS = {
    "__validation_errors__": "error",
    "__validation_warnings__": "warning",
    "__validation_ignored__": "type_warning",
}

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
    pointers.reverse()

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
    if not tspan_text:
        return ""
    stripped = tspan_text.strip()
    if stripped.startswith(problem_name):
        rest = stripped[len(problem_name):].strip()
        if rest.startswith("(") and rest.endswith(")"):
            return rest[1:-1]
    m = re.search(r"\(([^)]+)\)\s*$", stripped)
    return m.group(1) if m else ""
