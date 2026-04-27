"""SVG prep utilities: assign IDs, inline CSS class-based styles."""

from __future__ import annotations

import re

from lxml import etree

_CSS_RULE_RE = re.compile(r'\.([\w-]+)\s*\{([^}]*)\}', re.DOTALL)
_CSS_PROP_RE = re.compile(r'([\w-]+)\s*:\s*([^;}\n]+)')

_INLINE_PROPS = frozenset({
    "fill", "stroke", "stroke-width", "opacity",
    "fill-opacity", "stroke-opacity", "display",
})

_ADDRESSABLE_TAGS = frozenset({
    "path", "rect", "circle", "ellipse", "line",
    "polygon", "polyline", "use", "image", "text",
})


def _parse_stylesheet(text: str) -> dict[str, dict[str, str]]:
    rules: dict[str, dict[str, str]] = {}
    for m in _CSS_RULE_RE.finditer(text):
        props = {
            pm.group(1).strip(): pm.group(2).strip()
            for pm in _CSS_PROP_RE.finditer(m.group(2))
        }
        if props:
            rules[m.group(1)] = props
    return rules


def prep_svg(tree) -> dict:
    """Assign IDs and inline CSS fills. Returns change stats."""
    root = tree.getroot()

    # Collect all stylesheet rules from <style> elements.
    style_rules: dict[str, dict[str, str]] = {}
    for elem in root.iter():
        if etree.QName(elem.tag).localname == "style" and elem.text:
            style_rules.update(_parse_stylesheet(elem.text))

    counter = 0
    assigned_ids = 0
    inlined_styles = 0

    for elem in root.iter():
        local = etree.QName(elem.tag).localname
        if local not in _ADDRESSABLE_TAGS:
            continue

        # --- Assign ID ---
        if not elem.get("id"):
            counter += 1
            elem.set("id", f"elem_{counter}")
            assigned_ids += 1

        # --- Inline CSS class props ---
        cls_attr = elem.get("class", "")
        if not cls_attr:
            continue

        merged: dict[str, str] = {}
        for cls in cls_attr.split():
            merged.update(style_rules.get(cls, {}))
        if not merged:
            continue

        changed = False
        for prop, val in merged.items():
            if prop not in _INLINE_PROPS:
                continue
            if elem.get(prop):
                continue
            # Don't overwrite if already present in inline style="..."
            existing = elem.get("style", "")
            if re.search(rf'(?:^|;)\s*{re.escape(prop)}\s*:', existing):
                continue
            elem.set(prop, val)
            changed = True

        if changed:
            inlined_styles += 1

    return {"assigned_ids": assigned_ids, "inlined_styles": inlined_styles}
