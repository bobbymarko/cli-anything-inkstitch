"""Ink/Stitch visual command structure — the engine's REAL contract.

Ink/Stitch discovers an element's commands via CONNECTOR PATHS
(lib/commands.py `find_commands`): a `<path>` whose
`inkscape:connection-start`/`inkscape:connection-end` link a command
`<use xlink:href="#inkstitch_<name>">` to the target element, with the
symbol definition present in `<defs>`. The command's position is the use's
x/y. A bare `<use>` child of the element — what this CLI wrote before
2026-07 — is silently ignored by the engine.

Engine command names come from the COMMANDS registry (starting_point,
ending_point, trim, ...). The names this CLI historically exposed
(fill_start, fill_end, ...) never existed; they map via LEGACY_ALIASES.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from lxml import etree

from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.svg.attrs import SVG_NS, XLINK_NS
from cli_anything_inkstitch.svg.geometry import element_bbox

INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
CONNECTION_START = f"{{{INKSCAPE_NS}}}connection-start"
CONNECTION_END = f"{{{INKSCAPE_NS}}}connection-end"
INKSCAPE_LABEL = f"{{{INKSCAPE_NS}}}label"
XLINK_HREF = f"{{{XLINK_NS}}}href"

# names this CLI used before the engine contract was implemented
LEGACY_ALIASES = {
    "fill_start": "starting_point",
    "fill_end": "ending_point",
    "satin_start": "starting_point",
    "satin_end": "ending_point",
    "ignore": "ignore_object",
    "pause": "stop",
}

_CONNECTOR_STYLE = ("fill:none;stroke:#000000;stroke-width:1;stroke-opacity:0.5;"
                    "vector-effect:non-scaling-stroke")


def canonical_command(name: str) -> str:
    return LEGACY_ALIASES.get(name, name)


def _defs(tree):
    root = tree.getroot()
    defs = root.find(f"{{{SVG_NS}}}defs")
    if defs is None:
        defs = etree.SubElement(root, f"{{{SVG_NS}}}defs")
        root.insert(0, defs)
    return defs


def _bundled_symbol(command: str):
    """The real symbol from the installed Ink/Stitch bundle, if findable."""
    from cli_anything_inkstitch.binary import discover
    binary = discover()
    if not binary:
        return None
    b = Path(binary)
    for candidate in (b.parent.parent / "Resources" / "symbols" / "inkstitch.svg",
                      b.parent / "symbols" / "inkstitch.svg",
                      b.parent / "_internal" / "symbols" / "inkstitch.svg"):
        if candidate.is_file():
            try:
                symbols = etree.parse(str(candidate))
                sym = symbols.getroot().find(
                    f".//*[@id='inkstitch_{command}']")
                if sym is not None:
                    import copy
                    node = copy.deepcopy(sym)
                    node.set("transform", "scale(0.25)")
                    return node
            except (etree.XMLSyntaxError, OSError):
                return None
    return None


def ensure_symbol(tree, command: str) -> None:
    """Guarantee `<symbol id="inkstitch_<command>">` exists in defs.

    Prefers the real bundled symbol (correct visuals in Inkscape); falls back
    to a minimal marker — the engine's parser only requires a symbol tag with
    the right id, not any particular content.
    """
    defs = _defs(tree)
    sid = f"inkstitch_{command}"
    if defs.find(f"*[@id='{sid}']") is not None:
        return
    node = _bundled_symbol(command)
    if node is None:
        node = etree.SubElement(defs, f"{{{SVG_NS}}}symbol")
        node.set("id", sid)
        circle = etree.SubElement(node, f"{{{SVG_NS}}}circle")
        circle.set("r", "2")
        circle.set("style", "fill:#fddc33;stroke:#000000;stroke-width:0.5")
    else:
        node.set("id", sid)
        defs.append(node)


def _elem_center(elem) -> tuple[float, float]:
    bb = element_bbox(elem)          # (xmin, ymin, xmax, ymax)
    if bb is None:
        return (0.0, 0.0)
    return ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)


def attach_command(tree, elem, command: str, x: float, y: float) -> dict:
    """Create the engine-recognized structure: defs symbol + sibling group
    holding the marker use and the connector path. Returns ids + the group
    node (for history serialization)."""
    command = canonical_command(command)
    ensure_symbol(tree, command)
    if not elem.get("id"):
        raise UserError("target element needs an id")
    parent = elem.getparent()
    token = secrets.token_hex(3)

    group = etree.Element(f"{{{SVG_NS}}}g")
    group.set("id", f"command_group_{token}")
    group.set(INKSCAPE_LABEL, f"Ink/Stitch Command: {command}")

    use = etree.SubElement(group, f"{{{SVG_NS}}}use")
    use.set("id", f"command_use_{token}")
    use.set(XLINK_HREF, f"#inkstitch_{command}")
    use.set("height", "100%")
    use.set("width", "100%")
    use.set("x", str(x))
    use.set("y", str(y))
    use.set(INKSCAPE_LABEL, "command marker")

    cx, cy = _elem_center(elem)
    connector = etree.SubElement(group, f"{{{SVG_NS}}}path")
    connector.set("id", f"command_connector_{token}")
    connector.set("d", f"M {x},{y} {cx},{cy}")
    connector.set("style", _CONNECTOR_STYLE)
    connector.set(CONNECTION_START, f"#{use.get('id')}")
    connector.set(CONNECTION_END, f"#{elem.get('id')}")

    parent.insert(parent.index(elem) + 1, group)
    return {"command": command, "use_id": use.get("id"),
            "group_id": group.get("id"), "group": group}


def find_commands(tree, elem) -> list[dict]:
    """Commands attached to elem via connectors (the engine's discovery)."""
    eid = elem.get("id")
    if not eid:
        return []
    out = []
    root = tree.getroot()
    ref = f"#{eid}"
    for path in root.iter(f"{{{SVG_NS}}}path"):
        cs, ce = path.get(CONNECTION_START), path.get(CONNECTION_END)
        if ref not in (cs, ce):
            continue
        other = ce if cs == ref else cs
        if not other or not other.startswith("#"):
            continue
        use = root.find(f".//*[@id='{other[1:]}']")
        if use is None or etree.QName(use.tag).localname != "use":
            continue
        href = use.get(XLINK_HREF) or use.get("href") or ""
        if not href.startswith("#inkstitch_"):
            continue
        out.append({
            "command": href[len("#inkstitch_"):],
            "use_id": use.get("id"),
            "connector_id": path.get("id"),
            "x": float(use.get("x") or 0),
            "y": float(use.get("y") or 0),
        })
    return out


def find_legacy_markers(elem) -> list[dict]:
    """Bare `<use>` children of the element — the pre-contract format the
    engine ignores. Reported so they can be surfaced and migrated."""
    out = []
    for child in elem.findall(f"{{{SVG_NS}}}use"):
        href = child.get(XLINK_HREF) or child.get("href") or ""
        if href.startswith("#inkstitch_"):
            out.append({
                "command": href[len("#inkstitch_"):],
                "use_id": child.get("id"),
                "x": float(child.get("x") or 0),
                "y": float(child.get("y") or 0),
                "legacy": True,
            })
    return out


def move_command(tree, use_id: str, x: float, y: float) -> dict:
    """Move a command marker: update the use position AND the connector's
    start so Inkscape renders it consistently. Legacy child markers are
    MIGRATED to the real structure on first move (same position semantics,
    engine goes from ignoring them to honoring them)."""
    root = tree.getroot()
    use = root.find(f".//*[@id='{use_id}']")
    if use is None:
        raise UserError(f"no <use> command with id={use_id!r}")
    parent = use.getparent()
    parent_tag = etree.QName(parent.tag).localname if isinstance(parent.tag, str) else ""
    if parent_tag != "g" or not (parent.get("id") or "").startswith("command_group_"):
        # legacy child marker → migrate in place
        elem = parent
        href = use.get(XLINK_HREF) or use.get("href") or ""
        command = canonical_command(href[len("#inkstitch_"):])
        elem.remove(use)
        return attach_command(tree, elem, command, x, y) | {"migrated_from": use_id}
    use.set("x", str(x))
    use.set("y", str(y))
    for sib in parent.findall(f"{{{SVG_NS}}}path"):
        if sib.get(CONNECTION_START) == f"#{use_id}":
            d = sib.get("d") or ""
            rest = d.split(" ", 3)
            end = rest[3] if len(rest) > 3 else (rest[-1] if len(rest) > 1 else "")
            sib.set("d", f"M {x},{y} {end}".strip())
    return {"use_id": use_id, "x": x, "y": y}


def detach_command(tree, use_id: str) -> dict:
    """Remove a command (its whole group for the real structure; the bare
    use for legacy markers). Returns what was removed for history."""
    root = tree.getroot()
    use = root.find(f".//*[@id='{use_id}']")
    if use is None:
        raise UserError(f"no <use> command with id={use_id!r}")
    parent = use.getparent()
    if (parent.get("id") or "").startswith("command_group_"):
        target, container = parent, parent.getparent()
    else:
        target, container = use, parent
    index = list(container).index(target)
    xml = etree.tostring(target).decode()
    container.remove(target)
    return {"use_id": use_id, "removed_id": target.get("id"),
            "parent_id": container.get("id"), "index": index, "xml": xml}
