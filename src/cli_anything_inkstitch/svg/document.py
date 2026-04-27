"""SVG document loader and atomic writer."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from lxml import etree

from cli_anything_inkstitch.errors import ProjectError
from cli_anything_inkstitch.svg.attrs import SVG_NS, ensure_inkstitch_namespace


INKSTITCH_SVG_VERSION = 3  # matches lib/update.py INKSTITCH_SVG_VERSION


def load_svg(path: str | Path):
    p = Path(path)
    if not p.exists():
        raise ProjectError(f"SVG not found: {path}")
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
    try:
        tree = etree.parse(str(p), parser)
    except etree.XMLSyntaxError as e:
        raise ProjectError(f"failed to parse SVG {path}: {e}") from e
    root = tree.getroot()
    if not root.tag.endswith("}svg") and root.tag != "svg":
        raise ProjectError(f"file is not an SVG document: {path}")
    ensure_inkstitch_namespace(root)
    _ensure_inkstitch_version(root)
    return tree


def _ensure_inkstitch_version(root) -> None:
    """Stamp inkstitch_svg_version into <metadata> if absent.

    The compiled inkstitch binary shows a blocking GUI dialog on SVGs that lack
    this marker (the "Unversioned Ink/Stitch SVG file detected" prompt). We pre-stamp
    it so headless runs are never blocked.

    Uses localname matching: after lxml round-trips the SVG, child elements of
    <metadata> inherit the default SVG namespace, so a plain find("inkstitch_svg_version")
    misses them. Checking by localname avoids duplicate entries.
    """
    ns = root.nsmap.get(None) or "http://www.w3.org/2000/svg"
    metadata = root.find(f"{{{ns}}}metadata")
    if metadata is None:
        metadata = etree.SubElement(root, f"{{{ns}}}metadata")
        root.insert(0, metadata)
    # Remove any duplicates, keep at most one.
    existing = [c for c in metadata if etree.QName(c.tag).localname == "inkstitch_svg_version"]
    for dup in existing[1:]:
        metadata.remove(dup)
    if not existing:
        el = etree.SubElement(metadata, "inkstitch_svg_version")
        el.text = str(INKSTITCH_SVG_VERSION)


def save_svg(tree, path: str | Path) -> str:
    """Write tree to path atomically. Returns the new sha256 of the saved file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tree.write(str(tmp), xml_declaration=True, encoding="utf-8", pretty_print=False)
    os.replace(tmp, p)
    return sha256_of(p)


def sha256_of(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_by_id(tree, svg_id: str):
    root = tree.getroot()
    matches = root.xpath(f"//*[@id=$id]", id=svg_id)
    if not matches:
        return None
    return matches[0]


def all_addressable_elements(tree):
    """Iterate elements that could be embroidery targets: paths, rects, circles, ellipses, lines, polygons, polylines, use, image, text, g."""
    root = tree.getroot()
    tags = {"path", "rect", "circle", "ellipse", "line", "polygon", "polyline", "use", "image", "text"}
    for elem in root.iter():
        local = etree.QName(elem.tag).localname
        if local in tags:
            yield elem


def get_label(elem) -> str | None:
    """Resolve a human label: inkscape:label, then <title>, then None."""
    for k, v in elem.attrib.items():
        if isinstance(k, str) and k.endswith("}label"):
            return v
    for child in elem:
        if etree.QName(child.tag).localname == "title" and child.text:
            return child.text.strip()
    return None
