"""ODI -> Ink/Stitch SVG writer.

Writes one Inkscape layer per thread (first appearance order), elements in
sewing order, in px user units (96 dpi, viewBox = width/height) so engine tools
need no correction transform.

Every ``inkstitch:*`` attribute written here is cited to the engine-side reader
(project rule: no write without a read). Paths are relative to the Ink/Stitch
source tree.

  * Element dispatch -- lib/elements/utils/nodes.py node_to_elements:
        fill colour present            -> FillStitch
        stroke + inkstitch:satin_column -> SatinColumn (needs >1 subpath)
        stroke, otherwise              -> Stroke
    With exactly two subpaths SatinColumn synthesizes the rungs, so satins are
    written as two rails and no rungs.
  * fill: angle (fill_stitch.py:310, DEGREES), row_spacing_mm (:364-379),
    expand_mm (:276-284), max_stitch_length_mm (:361).
  * satin: zigzag_spacing_mm (satin_column.py:263-272, peak to peak, mm),
    pull_compensation_mm (satin_column.py:291-305).
  * run / stroke: stroke_method (stroke.py:92-94 default running_stitch;
    manual_stitch :129), repeats (:112), bean_stitch_repeats (:137),
    running_stitch_length_mm (:173), running_stitch_tolerance_mm (:195).
  * trim_after / stop_after -- element.py:461-462 / :471-472
    get_boolean_param(...); the trim is turned into a real command by the
    stitch plan (lib/stitch_plan/stitch_plan.py:97-98).
  * <metadata><inkstitch:inkstitch_svg_version> -- lib/update.py
    update_inkstitch_document; the value must match the targeted engine
    (INKSTITCH_SVG_VERSION = 4 for 3.x) or the engine prompts a migration.
  * <metadata> engine settings (collapse_len_mm, min_stitch_len_mm) are
    JSON-encoded children read as self.metadata[...] (lib/metadata.py;
    lib/extensions/density_map.py:41-42).

The writer refuses to be silently lossy: whatever ODI says that this writer
cannot express (underlay, entry/exit points, unsupported params, lettering
without glyphs) is listed in ``Report.dropped``.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from .model import Document, Obj, Thread, parse_prov
from .paths import (Point, flatten, format_path, map_points, parse_path,
                    polyline, subpaths)

INKSTITCH_SVG_VERSION = 4
INKSTITCH_NS = "http://inkstitch.org/namespace"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
PX_PER_MM = 96.0 / 25.4


def _g(v):
    return f"{v:g}"


def _i(v):
    return str(int(v))


def _s(v):
    return str(v)


# param name -> formatter, in the order the attributes are written.
_FILL = {"angle": "{:.1f}".format, "expand_mm": _g, "row_spacing_mm": "{:.2f}".format,
         "max_stitch_length_mm": _g}
_SATIN = {"zigzag_spacing_mm": "{:.2f}".format, "pull_compensation_mm": _g}
_RUN = {"stroke_method": _s, "running_stitch_length_mm": _g,
        "running_stitch_tolerance_mm": _g, "repeats": _i, "bean_stitch_repeats": _i}
_STROKE_METHODS = ("running_stitch", "manual_stitch")


@dataclass
class Report:
    counts: Dict[str, int] = field(default_factory=lambda: {"satin": 0, "fill": 0, "run": 0, "trim": 0})
    dropped: List[str] = field(default_factory=list)
    provenance: Counter = field(default_factory=Counter)


def _esc(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _leaves(o: Obj, report: Report) -> Iterator[Obj]:
    """Objects that become elements, in sewing order."""
    if o.type == "group":
        for c in o.children:
            yield from _leaves(c, report)
    elif o.type == "applique":
        if not o.children:
            report.dropped.append(f"{o.id}: applique without pass objects")
        for c in o.children:
            yield from _leaves(c, report)
    elif o.type == "lettering":
        if not o.children:
            report.dropped.append(f"{o.id}: lettering has no glyph objects and this writer has no lettering tool")
        for c in o.children:
            yield from _leaves(c, report)
    else:
        yield o


def _document_extents(doc: Document) -> Tuple[Point, Point]:
    if doc.extents:
        return tuple(doc.extents["min"]), tuple(doc.extents["max"])  # type: ignore[return-value]
    pts: List[Point] = []
    for o in doc.walk():
        g = o.geometry
        ds = []
        if "path" in g:
            ds.append(g["path"])
        ds += g.get("rails", [])
        if "centerline" in g:
            ds.append(g["centerline"])
        for s in g.get("shapes", []):
            ds.append(s["outer"])
        for d in ds:
            for _c, p in parse_path(d):
                pts += p
        pts += [(s[0], s[1]) for s in o.stitches or []]
    if not pts:
        raise ValueError("ODI document has no geometry to place")
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys)), (max(xs), max(ys))


def _expand_centerline(d: str, width) -> Tuple[str, str]:
    """Offset a centreline into two rails (mm in, mm out)."""
    sub = subpaths(parse_path(d))[0]
    pts = flatten(sub)
    pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
    if len(pts) < 2:
        raise ValueError("centerline is degenerate")
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = cum[-1]

    def w_at(t):
        if not isinstance(width, list):
            return float(width)
        prof = sorted(width)
        if t <= prof[0][0]:
            return prof[0][1]
        for (t0, w0), (t1, w1) in zip(prof, prof[1:]):
            if t <= t1:
                return w0 + (w1 - w0) * (t - t0) / ((t1 - t0) or 1)
        return prof[-1][1]

    left, right = [], []
    for i, p in enumerate(pts):
        a, b = pts[max(i - 1, 0)], pts[min(i + 1, len(pts) - 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / n, dx / n
        h = w_at(cum[i] / total) / 2
        left.append((p[0] + nx * h, p[1] + ny * h))
        right.append((p[0] - nx * h, p[1] - ny * h))
    return polyline(left), polyline(right)


def write_inkstitch(doc: Document) -> Tuple[str, Report]:
    """Return ``(svg_text, report)``."""
    report = Report()
    for o in doc.walk():
        for v in o.provenance.values():
            report.provenance[parse_prov(v)[0]] += 1

    (minx, miny), (maxx, maxy) = _document_extents(doc)
    w = (maxx - minx) * PX_PER_MM
    h = (maxy - miny) * PX_PER_MM

    def to_px(x, y):
        return ((x - minx) * PX_PER_MM, (y - miny) * PX_PER_MM)

    def px_path(d: str) -> str:
        return format_path(map_points(parse_path(d), to_px), "{:.3f}".format)

    threads: Dict[str, Tuple[int, Thread]] = {
        t.id: (t.index if t.index is not None else n, t) for n, t in enumerate(doc.threads)}
    layers: Dict[str, List[str]] = {}

    def add(o: Obj, markup: str):
        layers.setdefault(o.thread, []).append(markup)

    def attrs(o: Obj, table) -> str:
        out = ""
        for name, fmt in table.items():
            if name in o.params and o.params[name] is not None:
                if name == "stroke_method" and o.params[name] not in _STROKE_METHODS:
                    report.dropped.append(f"{o.id}: params.stroke_method={o.params[name]!r} not supported")
                    continue
                out += f' inkstitch:{name}="{fmt(o.params[name])}"'
        for name in o.params:
            if name not in table:
                report.dropped.append(f"{o.id}: params.{name} has no Ink/Stitch mapping in this writer")
        return out

    def tail(o: Obj) -> str:
        out = ""
        c = o.connect
        if c.get("trim_after"):
            out += ' inkstitch:trim_after="true"'
            report.counts["trim"] += 1
        if c.get("stop_after"):
            out += ' inkstitch:stop_after="true"'
        for k in c:
            if k not in ("trim_after", "stop_after"):
                report.dropped.append(f"{o.id}: connect.{k} not written (entry/exit points need commands)")
        if o.underlay:
            kinds = ", ".join(str(u.get("kind", "?")) for u in o.underlay)
            report.dropped.append(f"{o.id}: underlay ({kinds}) not written; the engine regenerates its own")
        return out

    for o in (leaf for top in doc.objects for leaf in _leaves(top, report)):
        if o.thread not in threads:
            raise ValueError(f"object {o.id!r} has no valid thread")
        rgb = threads[o.thread][1].rgb
        oid = _esc(o.id)
        stroke = f"fill:none;stroke:{rgb};stroke-width:1"
        g = o.geometry
        if o.type == "fill":
            subs = []
            for s in g["shapes"]:
                subs.append(s["outer"])
                subs.extend(s.get("holes", []))
            d = " ".join(px_path(p) for p in subs)
            add(o, f'<path id="{oid}" d="{d}" style="fill:{rgb};fill-rule:evenodd;stroke:none"'
                   f'{attrs(o, _FILL)}{tail(o)}/>')
            report.counts["fill"] += 1
        elif o.type == "satin":
            rails = g["rails"] if "rails" in g else _expand_centerline(g["centerline"], g["width_mm"])
            d = " ".join(px_path(r) for r in rails)
            add(o, f'<path id="{oid}" d="{d}" style="{stroke}" inkstitch:satin_column="true"'
                   f'{attrs(o, _SATIN)}{tail(o)}/>')
            report.counts["satin"] += 1
        elif o.type == "run":
            add(o, f'<path id="{oid}" d="{px_path(g["path"])}" style="{stroke}"'
                   f'{attrs(o, _RUN)}{tail(o)}/>')
            report.counts["run"] += 1
        elif o.type == "manual":
            cmds = {s[2] for s in o.stitches or []}
            if cmds - {"STITCH", "END"}:
                report.dropped.append(f"{o.id}: manual stitches contain {sorted(cmds - {'STITCH', 'END'})}; only STITCH is written")
            pts = [to_px(s[0], s[1]) for s in o.stitches or [] if s[2] == "STITCH"]
            if len(pts) < 2:
                report.dropped.append(f"{o.id}: manual object has fewer than two stitches")
                continue
            params = dict(o.params)
            params.setdefault("stroke_method", "manual_stitch")
            o2 = Obj(id=o.id, type="manual", params=params, connect=o.connect, underlay=o.underlay)
            add(o, f'<path id="{oid}" d="{format_path([("M", [pts[0]])] + [("L", [p]) for p in pts[1:]], "{:.3f}".format)}"'
                   f' style="{stroke}"{attrs(o2, _RUN)}{tail(o)}/>')
            report.counts["run"] += 1

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkstitch="{INKSTITCH_NS}" '
        f'xmlns:inkscape="{INKSCAPE_NS}" version="1.1" width="{w:.3f}" height="{h:.3f}" '
        f'viewBox="0 0 {w:.3f} {h:.3f}">',
        "  <metadata>",
        f"    <inkstitch:inkstitch_svg_version>{INKSTITCH_SVG_VERSION}</inkstitch:inkstitch_svg_version>",
    ]
    for key in ("collapse_len_mm", "min_stitch_len_mm"):
        if doc.engine.get(key) is not None:
            out.append(f"    <inkstitch:{key}>{json.dumps(float(doc.engine[key]))}</inkstitch:{key}>")
    out.append("  </metadata>")
    for tid, markups in layers.items():
        ci, t = threads[tid]
        parts = [p for p in (t.code, t.name) if p is not None]
        label = " ".join(parts) if parts else f"color {ci}"
        out.append(f'  <g id="color{ci}" inkscape:groupmode="layer" inkscape:label="{_esc(label)}">')
        out.extend("    " + m for m in markups)
        out.append("  </g>")
    out.append("</svg>")
    return "\n".join(out), report
