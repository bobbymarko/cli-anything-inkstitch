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


# Tags inkstitch *would* try to stitch as path geometry. text/image/use trigger
# their own warnings (TextTypeWarning, ImageTypeWarning, Clone) so we don't
# flag them as "stitches as black" — that's a different problem.
_FILLABLE_TAGS = frozenset({
    "path", "rect", "circle", "ellipse", "line", "polygon", "polyline",
})


def _in_defs(elem) -> bool:
    """True if elem is inside a <defs> block (not directly rendered)."""
    parent = elem.getparent()
    while parent is not None:
        if etree.QName(parent.tag).localname == "defs":
            return True
        parent = parent.getparent()
    return False


def warnings_for_element(elem) -> list[dict]:
    """Static (no-binary) warnings about how this element will stitch.

    Currently flags:
      - `default_fill_black`: the element has no fill and no stroke, so
        inkstitch's `fill_color` default ("black") makes it silently stitch
        as a solid black auto-fill. This is rarely intentional — the user
        usually meant "no fill, don't stitch this" or forgot to set a fill.
        Compare to `document prep --illustrator-rings=...` which handles
        the multi-subpath ring subset of this problem.
    """
    out: list[dict] = []
    local = etree.QName(elem.tag).localname
    if (
        local in _FILLABLE_TAGS
        and not _in_defs(elem)
        and not has_fill(elem)
        and not has_stroke(elem)
    ):
        out.append({
            "type": "default_fill_black",
            "severity": "warning",
            "message": (
                "no fill or stroke set; inkstitch will silently stitch this "
                "as a solid black auto-fill. Set an explicit fill, add a "
                "stroke, or run `document prep --illustrator-rings=skip` to "
                "exclude it from stitching."
            ),
        })
    return out


def describe_element(elem, bb, d_bbox, all_elems, d_w_px: float, d_h_px: float, d_area_px: float) -> dict:
    """Rich derived context for one element — geometry, position, color, neighbors.

    Args:
        elem: lxml element
        bb: bounding box tuple (xmin, ymin, xmax, ymax) in px, or None
        d_bbox: design bounding box in px
        all_elems: list of (elem, bb) pairs for neighbor lookup; pass [] to skip
        d_w_px, d_h_px, d_area_px: pre-computed design dimensions in px
    """
    from cli_anything_inkstitch.svg.colors import closest_named
    from cli_anything_inkstitch.svg.geometry import (
        aspect_ratio,
        bbox_area,
        bbox_overlap,
        position_descriptor,
        px_to_mm,
    )

    summary = element_summary(elem)
    out: dict = {
        "id": elem.get("id"),
        "tag": summary["tag"],
        "stitch_type": summary["stitch_type"],
        "fill": summary["fill"],
        "stroke": summary["stroke"],
        "color_name": closest_named(summary["fill"]) if summary["fill"] else None,
    }
    if summary.get("warnings"):
        out["warnings"] = summary["warnings"]
    if bb is None:
        out["bbox"] = None
        out["note"] = "geometry not parseable (transforms or unsupported tag)"
        return out

    w_px = bb[2] - bb[0]
    h_px = bb[3] - bb[1]
    area_px = bbox_area(bb)

    out["bbox_mm"] = [round(px_to_mm(v), 2) for v in bb]
    out["size_mm"] = [round(px_to_mm(w_px), 2), round(px_to_mm(h_px), 2)]
    out["bbox_pct_of_design"] = {
        "width": round(100.0 * w_px / d_w_px, 1),
        "height": round(100.0 * h_px / d_h_px, 1),
        "area": round(100.0 * area_px / d_area_px, 1),
    }
    out["position"] = position_descriptor(bb, d_bbox)
    ar = aspect_ratio(bb)
    out["aspect_ratio"] = round(ar, 2) if ar is not None else None

    if all_elems:
        nbrs = []
        for other, other_bb in all_elems:
            if other is elem or other_bb is None:
                continue
            rel = bbox_overlap(bb, other_bb)
            if rel:
                nbrs.append({"id": other.get("id"), "relation": rel})
        out["neighbors"] = nbrs
    return out


def element_summary(elem) -> dict:
    """Compact dict representation of one SVG element for `element list` JSON output."""
    from cli_anything_inkstitch.svg.document import get_label

    style = _style_dict(elem)
    fill = elem.get("fill") or style.get("fill")
    stroke = elem.get("stroke") or style.get("stroke")
    out = {
        "id": elem.get("id"),
        "tag": etree.QName(elem.tag).localname,
        "label": get_label(elem),
        "fill": None if fill in (None, "", "none") else fill,
        "stroke": None if stroke in (None, "", "none") else stroke,
        "stroke_width_px": stroke_width_px(elem),
        "stitch_type": classify(elem),
        "set_params": set_params_on(elem),
    }
    warnings = warnings_for_element(elem)
    if warnings:
        out["warnings"] = warnings
    return out
