"""SVG prep utilities: assign IDs, inline CSS class-based styles, detect
Illustrator-style stroke-to-outline rings."""

from __future__ import annotations

import re

from lxml import etree

from cli_anything_inkstitch.svg.attrs import set_inkstitch

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

# Valid actions for handling Illustrator outline rings.
RING_ACTIONS = ("detect", "skip", "fill-black", "satin")


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


def _subpath_count(d: str) -> int:
    """Count subpaths in a path's `d` attribute. Each subpath starts with M/m."""
    return sum(1 for c in d if c in "Mm")


def _has_explicit_fill(elem) -> bool:
    """True if the element has any non-`none` fill set directly (attr or style).

    CSS-class fills are NOT considered explicit — they should be inlined first
    via the prep pass before ring detection runs.
    """
    fill_attr = elem.get("fill")
    if fill_attr not in (None, "", "none"):
        return True
    style = elem.get("style", "")
    m = re.search(r"(?:^|;)\s*fill\s*:\s*([^;]+)", style)
    if m and m.group(1).strip() not in ("", "none"):
        return True
    return False


def _has_explicit_stroke(elem) -> bool:
    stroke_attr = elem.get("stroke")
    if stroke_attr not in (None, "", "none"):
        return True
    style = elem.get("style", "")
    m = re.search(r"(?:^|;)\s*stroke\s*:\s*([^;]+)", style)
    if m and m.group(1).strip() not in ("", "none"):
        return True
    return False


def find_illustrator_rings(tree) -> list:
    """Return paths that look like Illustrator's stroke-converted-to-outline rings.

    Heuristic:
      - `<path>` element
      - No explicit fill (defaults to black per SVG spec — inkstitch will stitch
        these as solid black auto-fills, which is rarely the intent)
      - No explicit stroke
      - `d` contains 2+ subpaths (the outer boundary + inner boundary that
        together form the ring via even-odd fill)

    Run AFTER `prep_svg` has inlined CSS-class fills, otherwise paths whose
    fill comes from a `<style>` block will be misclassified as rings.
    """
    rings = []
    for elem in tree.getroot().iter():
        if etree.QName(elem.tag).localname != "path":
            continue
        if _has_explicit_fill(elem) or _has_explicit_stroke(elem):
            continue
        d = elem.get("d", "")
        if _subpath_count(d) >= 2:
            rings.append(elem)
    return rings


def _apply_ring_action(elem, action: str) -> bool:
    """Mutate one ring per the action. Returns True if anything changed."""
    if action == "detect":
        return False
    if action == "skip":
        # Use the SVG `display` attribute (not style merging) — inkstitch's
        # iterate_nodes treats display:none as "skip this node entirely".
        if elem.get("display") == "none":
            return False
        elem.set("display", "none")
        return True
    if action == "fill-black":
        # Make the implicit black fill explicit so the user can see in
        # `element list` that this will stitch as a black auto-fill.
        if elem.get("fill") == "#000000":
            return False
        elem.set("fill", "#000000")
        return True
    if action == "satin":
        # Mark as satin column — inkstitch's `rails` property reads the path's
        # subpaths directly, so a 2-subpath ring becomes a satin with two rails.
        # NB: closed-rail satins emit a ClosedPathWarning from inkstitch's
        # troubleshoot but still stitch. Opening the subpaths is a future
        # refinement.
        set_inkstitch(elem, "satin_column", True)
        return True
    raise ValueError(f"unknown ring action: {action}")


def prep_svg(tree, ring_action: str = "detect") -> dict:
    """Assign IDs, inline CSS fills, and detect/handle Illustrator outline rings.

    `ring_action`:
      - "detect" (default) — report rings found, don't modify them
      - "skip" — set `display="none"` so inkstitch ignores them
      - "fill-black" — set explicit `fill="#000000"`
      - "satin" — set `inkstitch:satin_column="True"`
    """
    if ring_action not in RING_ACTIONS:
        raise ValueError(
            f"ring_action must be one of {RING_ACTIONS}, got {ring_action!r}"
        )
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

    # Ring detection runs AFTER inline-fill so CSS-class fills don't get
    # misclassified as rings.
    rings = find_illustrator_rings(tree)
    rings_modified = 0
    rings_report = []
    for ring in rings:
        modified = _apply_ring_action(ring, ring_action)
        if modified:
            rings_modified += 1
        rings_report.append({
            "id": ring.get("id"),
            "subpaths": _subpath_count(ring.get("d", "")),
            "action": ring_action if modified else "detected",
        })

    return {
        "assigned_ids": assigned_ids,
        "inlined_styles": inlined_styles,
        "illustrator_rings_found": len(rings),
        "illustrator_rings_action": ring_action,
        "illustrator_rings_modified": rings_modified,
        "illustrator_rings": rings_report,
    }
