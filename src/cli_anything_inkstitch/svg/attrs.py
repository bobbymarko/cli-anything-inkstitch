"""Namespace + attribute helpers for inkstitch SVG manipulation."""

from __future__ import annotations

from typing import Iterator

from lxml import etree

INKSTITCH_NS = "http://inkstitch.org/namespace"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

NSMAP = {
    None: SVG_NS,
    "inkstitch": INKSTITCH_NS,
    "inkscape": INKSCAPE_NS,
    "xlink": XLINK_NS,
}


def qname(local: str, ns: str = INKSTITCH_NS) -> str:
    return f"{{{ns}}}{local}"


INKSTITCH_PREFIX = qname("", INKSTITCH_NS)  # "{http://inkstitch.org/namespace}"


def get_inkstitch(node, name: str, default=None):
    return node.get(qname(name), default)


def set_inkstitch(node, name: str, value) -> None:
    node.set(qname(name), encode_value(value))


def del_inkstitch(node, name: str) -> bool:
    key = qname(name)
    if key in node.attrib:
        del node.attrib[key]
        return True
    return False


def iter_inkstitch_attrs(node) -> Iterator[tuple[str, str]]:
    """Yield (local_name, value) pairs for every inkstitch:* attribute on node."""
    for k, v in node.attrib.items():
        if isinstance(k, str) and k.startswith(INKSTITCH_PREFIX):
            yield k[len(INKSTITCH_PREFIX):], v


def encode_value(value) -> str:
    """Convert a Python value to its inkstitch attribute string form."""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        # avoid trailing .0 noise but keep precision
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return str(value)


def parse_bool(value: str) -> bool:
    """Parse inkstitch's True/False (and tolerate true/false/1/0/yes/no)."""
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n", ""):
        return False
    raise ValueError(f"cannot parse boolean: {value!r}")


def ensure_inkstitch_namespace(root) -> bool:
    """Ensure the SVG root carries xmlns:inkstitch.

    lxml's nsmap is immutable post-creation, so if the prefix isn't already
    declared we rebuild the root. Returns True if the tree was rebuilt.
    """
    if root.nsmap.get("inkstitch") == INKSTITCH_NS:
        return False
    new_nsmap = dict(root.nsmap or {})
    new_nsmap["inkstitch"] = INKSTITCH_NS
    new_root = etree.Element(root.tag, attrib=root.attrib, nsmap=new_nsmap)
    new_root.text = root.text
    new_root.tail = root.tail
    for child in list(root):
        new_root.append(child)
    parent = root.getparent()
    if parent is None:
        # Root of a tree — replace via getroottree
        tree = root.getroottree()
        tree._setroot(new_root)
    else:
        parent.replace(root, new_root)
    return True
