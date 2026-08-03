"""Document units + engine correction-transform handling.

How the engine writes geometry (readers: ``lib/svg/path.py
get_correction_transform``, ``lib/svg/units.py get_viewbox_transform``):
extension tools compute paths in absolute **px space** and attach the inverse
of the ancestors' + viewBox transform so the result lands correctly.  Some
tools bake that correction into the coordinates (``auto_run``'s
``create_element`` calls ``node.apply_transform()``); others carry it as a
``transform`` attribute (``fill_to_stroke``'s ``_insert_elements`` writes
``transform=str(transform)``).  Measured on v3.3.0 in a 1-unit=1-mm document:
``fill_to_stroke`` output ``d`` values are ×3.7795 with a compensating
``transform="scale(0.264583)"`` on each path.

That document *renders* correctly — but any code that reads ``d`` without
applying ``transform`` (stitch math, coverage checks, naive exporters) sees
geometry at the wrong scale.  That failure cost a full design rebuild in the
rose-bag digitization (2026-08); two defenses came out of it:

* ``bake_transforms`` — after every engine tool, fold each path's composed
  transform into its coordinates so raw ``d`` equals effective geometry.
* px user units (viewBox dims = width/height converted at 96 px/inch) make
  the correction transform the identity in the first place;
  ``unit_scale_warning`` nudges documents toward that convention.

``check_scale_drift`` is the backstop: if a transform is dropped or
mis-composed anywhere in the chain, the art's span changes by the document
scale factor, and the tool wrappers refuse the output instead of saving it.
"""

from __future__ import annotations

import re

# CSS/Inkscape standard, mirrors the engine's lib/svg/units.py PIXELS_PER_MM.
PIXELS_PER_MM = 96 / 25.4

# px-per-unit factors as the engine resolves them (lib/svg/units.py
# convert_length → inkex.units.convert_unit(..., 'px')).
_UNIT_TO_PX = {
    "px": 1.0,
    "": 1.0,
    "mm": PIXELS_PER_MM,
    "cm": 10 * PIXELS_PER_MM,
    "in": 96.0,
    "pt": 96.0 / 72.0,
    "pc": 16.0,
}

_LENGTH_RE = re.compile(r"^\s*([0-9.eE+-]+)\s*([a-z%]*)\s*$")


def parse_doc_length_px(value: str | None) -> float | None:
    """Parse an SVG root width/height into px, or None if unparseable."""
    if not value:
        return None
    m = _LENGTH_RE.match(value)
    if not m:
        return None
    unit = m.group(2)
    if unit not in _UNIT_TO_PX:
        return None  # e.g. "%" — no basis for a px conversion
    try:
        return float(m.group(1)) * _UNIT_TO_PX[unit]
    except ValueError:
        return None


def document_scale(root) -> tuple[float, float] | None:
    """(sx, sy) the engine will compute for this document, or None if unknown.

    Mirrors the engine's ``lib/svg/units.py get_viewbox_transform``:
    sx = doc_width_px / viewBox_width.  A document without a viewBox or
    without parseable dimensions returns None (the engine then treats user
    units as px, which is safe).
    """
    viewbox = (root.get("viewBox") or "").replace(",", " ").split()
    if len(viewbox) != 4:
        return None
    try:
        vb_w, vb_h = float(viewbox[2]), float(viewbox[3])
    except ValueError:
        return None
    if vb_w <= 0 or vb_h <= 0:
        return None
    w_px = parse_doc_length_px(root.get("width"))
    h_px = parse_doc_length_px(root.get("height"))
    if w_px is None or h_px is None:
        return None
    return w_px / vb_w, h_px / vb_h


def unit_scale_warning(root, tolerance: float = 0.01) -> str | None:
    """Human-readable note when the engine's document scale is not 1.

    In such documents engine tools write px-space coordinates with
    compensating ``transform`` attributes (see module docstring).  This
    tooling bakes those transforms after every engine call, but any external
    code reading raw ``d`` values from intermediate output sees the wrong
    scale — px user units avoid the whole hazard class.
    """
    scale = document_scale(root)
    if scale is None:
        return None
    sx, sy = scale
    if abs(sx - 1.0) <= tolerance and abs(sy - 1.0) <= tolerance:
        return None
    return (
        f"document user unit is not 1 px (engine document scale sx={sx:.4f}, "
        f"sy={sy:.4f}; engine reader lib/svg/units.py get_viewbox_transform). "
        "Engine tools write geometry in px space with compensating transform "
        "attributes in such documents (lib/svg/path.py "
        "get_correction_transform) — rendering-correct, but raw path data is "
        f"{max(sx, sy):.2f}x the user-unit scale until transforms are baked. "
        "Prefer px user units: viewBox dimensions equal to width/height "
        "converted at 96 px/inch (e.g. width='88.2mm' → viewBox 0 0 333.35 "
        "...)."
    )


def bake_transforms(tree) -> int:
    """Fold transform attributes into path coordinates; returns paths changed.

    Engine tools that don't call the engine-side ``apply_transform`` leave
    their correction transform as an attribute (fill_to_stroke does; see
    module docstring).  Raw-``d`` readers ignore it, so bake it: group
    transforms are first distributed onto children, then each path's own
    transform is applied to its coordinates and dropped.  Non-path leaves
    keep their (composed) transform attribute — still correct, just not
    bakeable here.
    """
    from cli_anything_inkstitch.svg.geometry import (
        IDENTITY,
        matrix_multiply,
        parse_transform,
        transform_d,
    )
    root = tree.getroot()

    def local(elem) -> str:
        from lxml import etree
        return etree.QName(elem.tag).localname if isinstance(elem.tag, str) else ""

    # distribute group transforms down to children (top-down so nested
    # groups compose in document order)
    changed = True
    while changed:
        changed = False
        for g in root.iter():
            if local(g) != "g" or g.get("transform") is None:
                continue
            gm = parse_transform(g.get("transform"))
            if gm != IDENTITY:
                for child in list(g):
                    if not isinstance(child.tag, str):
                        continue
                    cm = parse_transform(child.get("transform"))
                    composed = matrix_multiply(gm, cm)
                    child.set(
                        "transform",
                        "matrix(%s)" % ",".join(f"{v:.8g}" for v in composed))
            del g.attrib["transform"]
            changed = True

    baked = 0
    for elem in root.iter():
        if local(elem) != "path":
            continue
        t = elem.get("transform")
        if t is None:
            continue
        m = parse_transform(t)
        d = elem.get("d")
        if d and m != IDENTITY:
            elem.set("d", transform_d(d, m))
            baked += 1
        del elem.attrib["transform"]
    return baked


_ART_TAGS = {"path", "rect", "circle", "ellipse", "line", "polygon", "polyline"}

# containers whose contents don't render in place — symbol/marker template
# shapes live at the origin and would poison the union bbox
_NONRENDERED = {"defs", "symbol", "marker", "pattern", "clipPath", "mask"}


def art_bbox(tree):
    """Union bbox of the design's shape geometry, in root coordinates.

    Deliberately excludes `use`/`text`/`image` and anything inside
    defs/symbol/marker containers: the engine's trim/stop command markers
    are `use` references to origin-centered symbol templates — both the
    reference placement and the template shapes would read as art growth
    (measured +9% on a small design routed with --trim), which is marker
    plumbing, not a geometry rescale.
    """
    from lxml import etree as _etree
    from cli_anything_inkstitch.svg.geometry import element_bbox_in_root

    def in_nonrendered(elem) -> bool:
        node = elem.getparent()
        while node is not None and isinstance(node.tag, str):
            if _etree.QName(node.tag).localname in _NONRENDERED:
                return True
            node = node.getparent()
        return False

    boxes = []
    for elem in tree.getroot().iter():
        if not isinstance(elem.tag, str) or not elem.get("id"):
            continue
        if _etree.QName(elem.tag).localname not in _ART_TAGS:
            continue
        if in_nonrendered(elem):
            continue
        bb, _meta = element_bbox_in_root(elem)
        if bb is not None:
            boxes.append(bb)
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )


def check_scale_drift(tool_name: str, before, after, tolerance: float) -> None:
    """Raise UserError when a tool's output rescaled the art beyond tolerance.

    `before`/`after` are art bboxes (from art_bbox, transform-aware);
    tolerance is the allowed relative span deviation per axis.  Position
    shifts are deliberately NOT checked: engine tools legitimately translate
    output (px-space origin), and callers re-align; a multiplicative rescale
    is the unrecoverable failure this refuses.  With correction transforms
    intact (or baked) this never fires — it catches a transform dropped or
    mis-composed anywhere in the chain.
    """
    from cli_anything_inkstitch.errors import UserError
    if before is None or after is None:
        return
    w0, h0 = before[2] - before[0], before[3] - before[1]
    w1, h1 = after[2] - after[0], after[3] - after[1]
    for axis, s0, s1 in (("width", w0, w1), ("height", h0, h1)):
        if s0 < 2 or s1 < 2:
            continue  # too small for a meaningful ratio
        ratio = s1 / s0
        if abs(ratio - 1.0) > tolerance:
            raise UserError(
                f"{tool_name} rescaled the design ({axis} span "
                f"{s0:.1f} -> {s1:.1f} user units, x{ratio:.3f}); output "
                "discarded. Engine tools write px-space coordinates with a "
                "compensating transform (engine readers lib/svg/path.py "
                "get_correction_transform, lib/svg/units.py "
                "get_viewbox_transform); a drift like this means that "
                "transform was dropped or mis-composed. Rebuild the SVG with "
                "px user units (viewBox dimensions = width/height converted "
                "at 96 px/inch) so the correction transform is the identity, "
                "and re-run."
            )
