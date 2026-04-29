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


def ensure_inkstitch_namespace(tree_or_root) -> bool:
    """Ensure the SVG root carries `xmlns:inkstitch`.

    lxml's nsmap is set at element creation time and is immutable on a live
    element, so we can't add `inkstitch` to the existing root's nsmap. We
    build a fresh root with the correct nsmap, move children/text/attribs
    over, and replace the original root in the tree.

    Pass an `_ElementTree` when you have one (most callers do). Passing only
    an `_Element` works too — but only reliably if the element is mid-tree
    (we use `getparent().replace()` in that case). For a document-root
    element with no tree handle we fall back to in-place attribute/child
    swap, which preserves children but NOT the inkstitch nsmap declaration
    on the root — so any inkstitch:* attrs added later will serialize with
    per-element `nsN:` prefixes instead of `inkstitch:`. Always prefer to
    pass the tree.

    Returns True if the tree was modified.
    """
    if hasattr(tree_or_root, "getroot"):
        tree = tree_or_root
        root = tree.getroot()
    else:
        root = tree_or_root
        tree = None

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
    if parent is not None:
        # Mid-tree element: simple in-place replace.
        parent.replace(root, new_root)
        return True

    if tree is not None:
        # Document root + caller-owned tree: _setroot works correctly when
        # called on the tree the caller actually holds. (Calling
        # `root.getroottree()._setroot(...)` silently fails because
        # getroottree() returns a transient wrapper that doesn't share state
        # with the caller's tree object.)
        tree._setroot(new_root)
        return True

    # Document root, no tree handle. Fall back: copy attribs/children back
    # into the original root in-place. nsmap won't update (limitation of
    # lxml). See docstring.
    raw = etree.tostring(new_root)
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
    replacement = etree.fromstring(raw, parser)
    root.tag = replacement.tag
    root.attrib.clear()
    root.attrib.update(replacement.attrib)
    for child in list(root):
        root.remove(child)
    for child in list(replacement):
        root.append(child)
    root.text = replacement.text
    return True
