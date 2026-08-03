"""Font SVG document building — guides, glyph layout helpers, path x-range estimation."""

from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from cli_anything_inkstitch.errors import ProjectError, UserError
from cli_anything_inkstitch.svg.attrs import (
    INKSCAPE_NS, INKSTITCH_NS, SVG_NS, XLINK_NS,
)
from cli_anything_inkstitch.svg.document import (
    find_by_id as _find_by_id,
    parse_svg,
    write_svg_atomic,
)

SODIPODI_NS = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"

# Ink/Stitch v3.3.0 renamed the variant files: "ltr.svg" (rtl/ttb/btt) is
# canonical and the old arrow names ("→.svg") are legacy — the engine reads
# new-then-legacy (lib/lettering/font_variant.py _get_variant_file_paths,
# VARIANT_TYPES vs LEGACY_VARIANT_TYPES). We mirror that: new fonts are
# written with the modern name; existing arrow-named fonts keep their
# filename on save so a directory is never half-migrated. Never inline
# these strings — keeping them in one place made THIS migration tractable.
FONT_SVG_FILENAME = "ltr.svg"
LEGACY_FONT_SVG_FILENAME = "→.svg"


def font_svg_path(font_dir) -> Path:
    """The font's left-to-right variant file.

    An existing file wins, modern name first (the engine's own lookup
    order); a fresh font gets the modern name. Pre-v3.3.0 engines only
    know the arrow names, so fonts created here need Ink/Stitch ≥ 3.3.0
    (or a manual rename back)."""
    for name in (FONT_SVG_FILENAME, LEGACY_FONT_SVG_FILENAME):
        p = Path(font_dir) / name
        if p.exists():
            return p
    return Path(font_dir) / FONT_SVG_FILENAME

FONT_NSMAP = {
    None: SVG_NS,
    "inkscape": INKSCAPE_NS,
    "sodipodi": SODIPODI_NS,
    "inkstitch": INKSTITCH_NS,
    "xlink": XLINK_NS,
    "svg": SVG_NS,
}

FONT_SVG_VERSION = 2  # inkstitch_svg_version matching bundled fonts

# Fixed SVG document height used by all fonts we create.
# Guide positions are expressed as Inkscape Y = doc_height - svg_y.
_DOC_HEIGHT = 500.0
_DOC_WIDTH = 500.0


# ---------------------------------------------------------------------------
# Path bbox helper — used to estimate horiz_adv_x
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r'([MmLlHhVvCcSsQqTtAaZz])'
    r'|([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)'
)


def _tokenize_path(d: str):
    """Yield (cmd_letter_or_None, number_or_None) pairs."""
    for letter, num in _TOKEN_RE.findall(d):
        if letter:
            yield letter, None
        else:
            yield None, float(num)


def _path_x_range(d: str) -> tuple[float, float]:
    """Return (min_x, max_x) for endpoint x-coordinates of an SVG path.

    Tracks absolute current position through all standard commands.
    Returns (0.0, 0.0) if no coordinates are found.
    """
    # Build list of (command, [args])
    segments: list[tuple[str, list[float]]] = []
    cur_cmd = None
    cur_args: list[float] = []
    for letter, num in _tokenize_path(d):
        if letter is not None:
            if cur_cmd is not None:
                segments.append((cur_cmd, cur_args))
            cur_cmd = letter
            cur_args = []
        else:
            cur_args.append(num)  # type: ignore[arg-type]
    if cur_cmd is not None:
        segments.append((cur_cmd, cur_args))

    xs: list[float] = []
    cx, cy = 0.0, 0.0
    sx, sy = 0.0, 0.0  # subpath start

    def consume_pairs(args, relative, n_skip=0):
        nonlocal cx, cy
        i = n_skip
        while i + 1 < len(args):
            dx, dy = args[i], args[i + 1]
            if relative:
                cx += dx
                cy += dy
            else:
                cx, cy = dx, dy
            xs.append(cx)
            i += 2

    for cmd, args in segments:
        uc = cmd.upper()
        rel = cmd.islower()
        if not args and uc != 'Z':
            continue

        if uc == 'M':
            # First coord is moveto; subsequent are implicit lineto
            if rel:
                cx += args[0]; cy += args[1]
            else:
                cx, cy = args[0], args[1]
            xs.append(cx)
            sx, sy = cx, cy
            consume_pairs(args, rel, n_skip=2)

        elif uc in ('L', 'T'):
            consume_pairs(args, rel)

        elif uc == 'H':
            for x in args:
                cx = cx + x if rel else x
                xs.append(cx)

        elif uc == 'V':
            for y in args:
                cy = cy + y if rel else y

        elif uc == 'C':
            i = 0
            while i + 5 < len(args):
                ex, ey = args[i + 4], args[i + 5]
                if rel:
                    cx += ex; cy += ey
                else:
                    cx, cy = ex, ey
                xs.append(cx)
                i += 6

        elif uc in ('S', 'Q'):
            i = 0
            while i + 3 < len(args):
                ex, ey = args[i + 2], args[i + 3]
                if rel:
                    cx += ex; cy += ey
                else:
                    cx, cy = ex, ey
                xs.append(cx)
                i += 4

        elif uc == 'A':
            i = 0
            while i + 6 < len(args):
                ex, ey = args[i + 5], args[i + 6]
                if rel:
                    cx += ex; cy += ey
                else:
                    cx, cy = ex, ey
                xs.append(cx)
                i += 7

        elif uc == 'Z':
            cx, cy = sx, sy

    if not xs:
        return 0.0, 0.0
    return min(xs), max(xs)


def _elem_x_range(elem) -> tuple[float, float]:
    """Return (min_x, max_x) across all <path> descendants of elem (or elem itself)."""
    all_xs: list[float] = []
    # include elem itself if it's a path
    targets = list(elem.iter())
    for node in targets:
        if not isinstance(node.tag, str):
            continue
        local = etree.QName(node.tag).localname
        if local == 'path':
            d = node.get('d', '')
            if d:
                lo, hi = _path_x_range(d)
                all_xs.extend([lo, hi])
    if not all_xs:
        return 0.0, 0.0
    return min(all_xs), max(all_xs)


# ---------------------------------------------------------------------------
# Font SVG building helpers
# ---------------------------------------------------------------------------

def _inkscape_y(svg_y: float, doc_height: float = _DOC_HEIGHT) -> float:
    """Convert SVG Y (from top) to Inkscape Y (from bottom)."""
    return doc_height - svg_y


def _build_guide(label: str, svg_y: float, guide_id: str, doc_height: float = _DOC_HEIGHT) -> etree._Element:
    ink_y = _inkscape_y(svg_y, doc_height)
    g = etree.Element(f"{{{SODIPODI_NS}}}guide")
    g.set("position", f"0,{ink_y:.4f}")
    g.set("orientation", "0,1")
    g.set(f"{{{INKSCAPE_NS}}}label", label)
    g.set("id", guide_id)
    g.set(f"{{{INKSCAPE_NS}}}locked", "false")
    return g


def _build_font_svg(
    baseline_y: float,
    ascender_y: float,
    descender_y: float,
    caps_y: float,
    xheight_y: float,
    doc_height: float = _DOC_HEIGHT,
    doc_width: float = _DOC_WIDTH,
) -> etree._ElementTree:
    """Build a minimal Inkstitch font variant SVG with guides and no glyphs."""
    root = etree.Element(
        f"{{{SVG_NS}}}svg",
        nsmap=FONT_NSMAP,
    )
    root.set("version", "1.1")
    root.set("id", "svg_root")
    root.set("width", f"{doc_width:.4f}")
    root.set("height", f"{doc_height:.4f}")
    root.set("viewBox", f"0 0 {doc_width:.4f} {doc_height:.4f}")
    root.set(f"{{{INKSCAPE_NS}}}version", "1.3")

    # namedview with guides
    nv = etree.SubElement(root, f"{{{SODIPODI_NS}}}namedview")
    nv.set("id", "namedview_font")
    nv.set(f"{{{INKSCAPE_NS}}}document-units", "px")
    nv.append(_build_guide("baseline", baseline_y, "guide_baseline", doc_height))
    nv.append(_build_guide("ascender", ascender_y, "guide_ascender", doc_height))
    nv.append(_build_guide("descender", descender_y, "guide_descender", doc_height))
    nv.append(_build_guide("caps", caps_y, "guide_caps", doc_height))
    nv.append(_build_guide("xheight", xheight_y, "guide_xheight", doc_height))

    # metadata
    md = etree.SubElement(root, f"{{{SVG_NS}}}metadata")
    md.set("id", "metadata_font")
    version_el = etree.SubElement(md, f"{{{INKSTITCH_NS}}}inkstitch_svg_version")
    version_el.text = str(FONT_SVG_VERSION)

    # defs (empty for now)
    etree.SubElement(root, f"{{{SVG_NS}}}defs").set("id", "defs_font")

    return etree.ElementTree(root)


def _load_font_svg(font_dir: Path) -> etree._ElementTree:
    path = font_svg_path(font_dir)
    try:
        return parse_svg(path)
    except ProjectError as e:
        # font dirs are standalone assets, not projects → user-level error
        raise UserError(f"{path.name} not loadable from {font_dir}: {e}") from e


def _save_font_svg(tree: etree._ElementTree, font_dir: Path) -> None:
    # pretty=True: font SVGs are meant to be hand-opened in Inkscape and
    # diffed against the bundled fonts, which are pretty-printed.
    write_svg_atomic(tree, font_svg_path(font_dir), pretty=True)


def _get_font_baseline_y(tree: etree._ElementTree) -> float:
    """Read baseline guide SVG_Y from font SVG."""
    root = tree.getroot()
    nv = root.find(f"{{{SODIPODI_NS}}}namedview")
    if nv is None:
        return _DOC_HEIGHT * 0.7  # fallback
    for guide in nv.findall(f"{{{SODIPODI_NS}}}guide"):
        label = guide.get(f"{{{INKSCAPE_NS}}}label", "")
        if label == "baseline":
            pos = guide.get("position", "0,0")
            ink_y = float(pos.split(",")[1])
            # Get doc height from root viewBox or height
            vb = root.get("viewBox", "")
            if vb:
                parts = vb.split()
                doc_h = float(parts[3]) if len(parts) == 4 else _DOC_HEIGHT
            else:
                doc_h = float(root.get("height", _DOC_HEIGHT))
            return doc_h - ink_y  # SVG_Y = doc_height - inkscape_y
    return _DOC_HEIGHT * 0.7


# ---------------------------------------------------------------------------
# Source SVG helpers
# ---------------------------------------------------------------------------

def _load_source_svg(path: str) -> etree._ElementTree:
    try:
        return parse_svg(path)
    except ProjectError as e:
        raise UserError(str(e)) from e


def _find_elem_by_id(tree: etree._ElementTree, elem_id: str):
    return _find_by_id(tree, elem_id)


def _find_elems_by_label(tree: etree._ElementTree) -> list[tuple[str, object]]:
    """Return [(char, elem)] for all elements with a single-char inkscape:label."""
    results = []
    for elem in tree.getroot().iter():
        if not isinstance(elem.tag, str):
            continue
        label = elem.get(f"{{{INKSCAPE_NS}}}label", "")
        # Single visible character (covers multi-byte Unicode)
        if len(label) == 1:
            results.append((label, elem))
    return results


def _find_source_baseline_y(tree: etree._ElementTree) -> float | None:
    """Look for a 'baseline' guide in source SVG. Returns SVG_Y or None."""
    root = tree.getroot()
    nv = root.find(f"{{{SODIPODI_NS}}}namedview")
    if nv is None:
        return None
    vb = root.get("viewBox", "")
    if vb:
        parts = vb.split()
        doc_h = float(parts[3]) if len(parts) == 4 else _DOC_HEIGHT
    else:
        doc_h = float(root.get("height", _DOC_HEIGHT))
    for guide in nv.findall(f"{{{SODIPODI_NS}}}guide"):
        label = guide.get(f"{{{INKSCAPE_NS}}}label", "")
        if label == "baseline":
            pos = guide.get("position", "0,0")
            ink_y = float(pos.split(",")[1])
            return doc_h - ink_y
    return None
