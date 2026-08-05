"""Trim economy: keep a trim only where the jump would show (task #55).

The celtic patch shipped with 37 trims — one per satin — because
trim_after-everywhere is the safe default. Machines pay for it: each trim
is seconds of dwell and a thread-break opportunity. Digitizer practice is
to trim only when the connecting jump would lie across open fabric;
short walks, and walks that later stitching covers, stay untrimmed.

Engine contract (readers cited):
* trim decision — lib/elements/element.py trim_after,
  get_boolean_param('trim_after', False): a per-element attribute, so
  stripping it is a plain attribute edit.
* thread color — the engine takes it from fill/stroke paint, so a color
  CHANGE boundary always keeps its trim (the machine stops there anyway).

Jump endpoints are derived from the vector geometry (rail/path ends,
overridden by attached starting_point/ending_point commands — the same
precedence SatinColumn uses, lib/elements/satin_column.py start_point/
end_point). That approximation is fine for a keep/strip decision.
"""

from __future__ import annotations

import math
import re

from lxml import etree

INK_NS = "http://inkstitch.org/namespace"
PX_PER_MM = 96.0 / 25.4


def _subpaths(d: str) -> list[str]:
    return [c for c in re.split(r"(?=[Mm])", (d or "").strip()) if c.strip()]


def _flatten(sub: str):
    from cli_anything_inkstitch.artifact.gate import flatten_path
    return flatten_path(sub)


def _thread_color(elem) -> str:
    style = elem.get("style") or ""
    m = re.search(r"stroke:\s*([^;]+)", style)
    if m and m.group(1).strip() not in ("none", ""):
        return m.group(1).strip().lower()
    for attr in ("stroke", "fill"):
        v = elem.get(attr)
        if v and v != "none":
            return v.lower()
    return ""


def _command_point(tree, elem, name: str):
    from cli_anything_inkstitch.artifact.design_model import _command_uses
    for cmd in _command_uses(tree, elem):
        if cmd.get("command") == name and not cmd.get("legacy"):
            return (float(cmd["x"]), float(cmd["y"]))
    return None


def _entry_exit(tree, elem):
    """Approximate first/last needle positions of an element."""
    d = elem.get("d")
    if not d:
        return None, None
    subs = _subpaths(d)
    pts = _flatten(subs[0])
    if not pts:
        return None, None
    entry = _command_point(tree, elem, "starting_point") or pts[0]
    exit_ = _command_point(tree, elem, "ending_point") or pts[-1]
    return entry, exit_


def _coverage_rings(elem):
    """Polygon rings later stitching covers the fabric with (px space)."""
    d = elem.get("d")
    if not d:
        return []
    is_satin = (elem.get(f"{{{INK_NS}}}satin_column") or "").lower() == "true"
    subs = _subpaths(d)
    if is_satin and len(subs) >= 2:
        a = _flatten(subs[0])
        b = _flatten(subs[1])
        if len(a) > 1 and len(b) > 1:
            ring = a + list(reversed(b))
            return [ring + [ring[0]]]
        return []
    if elem.get("fill") not in (None, "none"):
        return [r + [r[0]] for r in (_flatten(s) for s in subs) if len(r) >= 3]
    return []                      # runs are hairlines — no cover credit


def _point_in_rings(pt, rings) -> bool:
    x, y = pt
    inside = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            if (y1 > y) != (y2 > y):
                if x1 + (y - y1) / (y2 - y1) * (x2 - x1) > x:
                    inside = not inside
    return inside


def plan_trim_economy(tree, *, min_jump_mm: float = 2.0,
                      cover_fraction: float = 0.8,
                      samples: int = 9) -> list[dict]:
    """Decide keep/strip for every trim_after in stitch (document) order.

    Returns one record per trimmed element:
      {"id", "action": "keep"|"strip", "reason", "jump_mm"}
    """
    from cli_anything_inkstitch.artifact.design_model import (
        _is_command_plumbing,
        _is_command_use,
    )

    def _in_nonrendered(e) -> bool:
        p = e.getparent()
        while p is not None:
            if isinstance(p.tag, str) and etree.QName(p.tag).localname in (
                    "defs", "symbol", "marker", "pattern", "clipPath", "mask"):
                return True
            p = p.getparent()
        return False

    root = tree.getroot()
    # design elements only: command markers/connectors have id+d too, and
    # counting them as neighbors made every satin look like a color change
    # to "" (caught on the celtic patch dry-run: 33 bogus keeps)
    seq = [e for e in root.iter()
           if isinstance(e.tag, str)
           and etree.QName(e.tag).localname in
               ("path", "polygon", "rect", "ellipse", "circle", "line")
           and e.get("id") and e.get("d")
           and not _is_command_use(e) and not _is_command_plumbing(e)
           and not _in_nonrendered(e)]
    out = []
    for i, elem in enumerate(seq):
        if (elem.get(f"{{{INK_NS}}}trim_after") or "").lower() not in ("true",):
            continue
        rec = {"id": elem.get("id")}
        if i + 1 >= len(seq):
            rec.update(action="keep", reason="last element", jump_mm=None)
            out.append(rec)
            continue
        nxt = seq[i + 1]
        if _thread_color(nxt) != _thread_color(elem):
            rec.update(action="keep", reason="color change", jump_mm=None)
            out.append(rec)
            continue
        _entry, exit_ = _entry_exit(tree, elem)
        entry, _exit2 = _entry_exit(tree, nxt)
        if exit_ is None or entry is None:
            rec.update(action="keep", reason="no geometry", jump_mm=None)
            out.append(rec)
            continue
        jump_mm = math.dist(exit_, entry) / PX_PER_MM
        rec["jump_mm"] = round(jump_mm, 2)
        if jump_mm <= min_jump_mm:
            rec.update(action="strip", reason=f"short jump ({jump_mm:.1f}mm)")
            out.append(rec)
            continue
        later_rings = []
        for later in seq[i + 1:]:
            later_rings.extend(_coverage_rings(later))
        covered = 0
        for k in range(1, samples + 1):
            t = k / (samples + 1)
            p = (exit_[0] + t * (entry[0] - exit_[0]),
                 exit_[1] + t * (entry[1] - exit_[1]))
            if _point_in_rings(p, later_rings):
                covered += 1
        if covered >= samples * cover_fraction:
            rec.update(action="strip",
                       reason=f"jump covered by later stitching "
                              f"({covered}/{samples} samples)")
        else:
            rec.update(action="keep",
                       reason=f"crosses open fabric "
                              f"({samples - covered}/{samples} exposed)")
        out.append(rec)
    return out


def apply_trim_economy(tree, plan: list[dict]) -> int:
    """Strip trim_after per plan; returns how many were removed."""
    root = tree.getroot()
    strip_ids = {r["id"] for r in plan if r["action"] == "strip"}
    removed = 0
    for e in root.iter():
        if e.get("id") in strip_ids:
            if e.attrib.pop(f"{{{INK_NS}}}trim_after", None) is not None:
                removed += 1
    return removed
