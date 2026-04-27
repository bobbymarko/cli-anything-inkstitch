"""Element type dispatch — mirrors lib/elements/utils/nodes.py:node_to_elements."""

from __future__ import annotations

import re

from lxml import etree

from cli_anything_inkstitch.svg.attrs import (
    INKSTITCH_PREFIX,
    get_inkstitch,
    iter_inkstitch_attrs,
    parse_bool,
)

# Heuristic threshold (mm) above which a stroked path is treated as a satin column
# even without inkstitch:satin_column. Mirrors the inkstitch default.
SATIN_THRESHOLD_PX = 5.0


def _style_dict(elem) -> dict[str, str]:
    style = elem.get("style", "") or ""
    out: dict[str, str] = {}
    for part in style.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def has_fill(elem) -> bool:
    style = _style_dict(elem)
    fill = elem.get("fill") or style.get("fill")
    fill_opacity = style.get("fill-opacity") or elem.get("fill-opacity")
    if fill in (None, "", "none"):
        return False
    try:
        if fill_opacity is not None and float(fill_opacity) == 0:
            return False
    except ValueError:
        pass
    return True


def has_stroke(elem) -> bool:
    style = _style_dict(elem)
    stroke = elem.get("stroke") or style.get("stroke")
    if stroke in (None, "", "none"):
        return False
    return True


def stroke_width_px(elem) -> float:
    style = _style_dict(elem)
    sw = style.get("stroke-width") or elem.get("stroke-width") or "0"
    m = re.match(r"^\s*([0-9.]+)\s*([a-z]*)\s*$", sw)
    if not m:
        return 0.0
    return float(m.group(1))


def classify(elem) -> str:
    """Return the stitch_type for an element. See SPEC.md §1.4."""
    if get_inkstitch(elem, "manual_stitch") and parse_bool(get_inkstitch(elem, "manual_stitch")):
        return "manual_stitch"

    # explicit stroke method overrides
    stroke_method = get_inkstitch(elem, "stroke_method")
    fill_method = get_inkstitch(elem, "fill_method")

    satin = get_inkstitch(elem, "satin_column")
    if satin and parse_bool(satin):
        return "satin_column"

    if has_stroke(elem) and stroke_width_px(elem) >= SATIN_THRESHOLD_PX and \
            not (has_fill(elem) and (get_inkstitch(elem, "auto_fill") in (None, "True", "true"))):
        # wide stroke with no competing fill — treated as satin per inkstitch heuristic
        if etree.QName(elem.tag).localname in {"path", "polyline", "line"}:
            return "satin_column"

    if has_fill(elem):
        af = get_inkstitch(elem, "auto_fill")
        if af is not None and not parse_bool(af):
            return "legacy_fill"
        if fill_method:
            return fill_method  # contour_fill, guided_fill, meander_fill, etc.
        return "auto_fill"

    if has_stroke(elem):
        if stroke_method:
            return stroke_method  # running_stitch, ripple_stitch, zigzag_stitch, bean_stitch
        return "running_stitch"

    return "unassigned"


def set_params_on(elem) -> list[str]:
    """List of inkstitch:* attribute local names currently present on the element."""
    return sorted(local for local, _ in iter_inkstitch_attrs(elem))


def element_summary(elem) -> dict:
    """Compact dict representation of one SVG element for `element list` JSON output."""
    from cli_anything_inkstitch.svg.document import get_label

    style = _style_dict(elem)
    fill = elem.get("fill") or style.get("fill")
    stroke = elem.get("stroke") or style.get("stroke")
    return {
        "id": elem.get("id"),
        "tag": etree.QName(elem.tag).localname,
        "label": get_label(elem),
        "fill": None if fill in (None, "", "none") else fill,
        "stroke": None if stroke in (None, "", "none") else stroke,
        "stroke_width_px": stroke_width_px(elem),
        "stitch_type": classify(elem),
        "set_params": set_params_on(elem),
    }
