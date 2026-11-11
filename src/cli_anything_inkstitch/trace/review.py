"""A digitized design's problem areas, by shape: what `tools review` reports and draws.

The loop it serves: digitize (shapes-to-satin), review, fix the flagged shapes with per-shape
overrides, digitize again. Each flag names the shape (the id shapes-to-satin and --overrides use),
where it is in document mm (x right, y down from the page's top-left, as the overrides take it),
and what is wrong:

* fan / hard_seam / ring_seam - column geometry (column_checks), read back from the satin columns
  in the SVG. The outline is the union of each shape's columns.
* density   - needle penetrations per mm² over the warn level (embroidery/density.hotspots), from
  the engine's own stitch plan, attributed to the shape whose columns are nearest.
* long_stitch - a stitch longer than LONG_STITCH_MM: it snags.
Plus the design's thread count and colour changes, from the stitch plan's colour blocks.
"""

from __future__ import annotations

import math
import re

LONG_STITCH_MM = 8.0

_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:e-?\d+)?")


def satin_pairs(d: str):
    """A two-rail, rung-less satin's pairs (as shapes-to-satin writes them: 'M l0 L l1 ... M r0 L
    r1 ...', one node per pair). None for any other layout."""
    subs = [s for s in re.split(r"[Mm]", d) if s.strip()]
    if len(subs) != 2 or re.search(r"[CcQqSsTtAaHhVvZz]", d):
        return None
    rails = []
    for s in subs:
        nums = [float(v) for v in _NUM.findall(s)]
        rails.append(list(zip(nums[0::2], nums[1::2])))
    if len(rails[0]) != len(rails[1]) or len(rails[0]) < 2:
        return None
    return list(zip(rails[0], rails[1]))


def shape_of(el) -> str:
    """The shape id a satin belongs to: its group's id less '_sat' (shapes-to-satin), else its own."""
    g = el.getparent()
    while g is not None and isinstance(g.tag, str):
        gid = g.get("id") or ""
        if gid.endswith("_sat"):
            return gid[:-4]
        g = g.getparent()
    return el.get("id") or "?"


def column_flags(columns_by_shape: dict, to_mm, outlines: dict | None = None) -> list[dict]:
    """fan / hard_seam / ring_seam flags. columns_by_shape: shape → [(column id, pairs, closed)] in
    document user units; to_mm maps a user-unit point to document mm. outlines: shape → contours in
    document mm (shapes-to-satin stores them); without one, the union of the columns stands in."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from cli_anything_inkstitch.trace import column_checks as cc

    out = []
    for shape, cols in columns_by_shape.items():
        mm_cols = [[(to_mm(l), to_mm(r)) for l, r in pairs] for _, pairs, _ in cols]
        bodies = []
        for pairs in mm_cols:
            ring = [p[0] for p in pairs] + [p[1] for p in reversed(pairs)]
            poly = Polygon(ring)
            bodies.append(poly if poly.is_valid else poly.buffer(0))
        contours = (outlines or {}).get(shape)
        if not contours:
            union = unary_union(bodies)
            contours = []
            for poly in getattr(union, "geoms", [union]):
                if poly.geom_type == "Polygon" and not poly.is_empty:
                    contours.append(list(poly.exterior.coords)[:-1])
                    contours += [list(r.coords)[:-1] for r in poly.interiors]
        for (cid, _, _), pairs in zip(cols, mm_cols):
            for f in cc.fans(pairs):
                out.append({"kind": "fan", "shape": shape, "column": cid, "at_mm": list(f["at"]),
                            "detail": f"short rail {f['short_mm']} mm against {f['long_mm']} mm"})
            for f in cc.turns(pairs):
                out.append({"kind": "turn", "shape": shape, "column": cid, "at_mm": list(f["at"]),
                            "detail": f"tight turn, inner rail {f['short_mm']} mm against {f['long_mm']} mm "
                                      "(not a defect: short stitches thin the inside; look at it)"})
        for s in cc.seams(mm_cols, contours, [c for _, _, c in cols]):
            if s["kind"] == "corner_join":
                continue
            out.append({"kind": s["kind"], "shape": shape,
                        "column": ",".join(cols[i][0] for i in s["columns"]), "at_mm": list(s["at"])})
    return out


def stitch_flags(blocks, to_mm, nearest_shape, warn: float = 20.0) -> tuple[list[dict], dict]:
    """density and long_stitch flags from the engine's stitch plan, and plan totals."""
    from cli_anything_inkstitch.embroidery.density import density_grid, hotspots

    pts_mm, flags, longs = [], [], {}
    for b in blocks:
        for path in b["paths"]:
            q = [to_mm(p) for p in path]
            pts_mm += q
            for a, c in zip(q, q[1:]):
                ln = math.dist(a, c)
                if ln > LONG_STITCH_MM:
                    mid = ((a[0] + c[0]) / 2, (a[1] + c[1]) / 2)
                    sh = nearest_shape(mid)
                    n, worst, at = longs.get(sh, (0, 0.0, mid))
                    longs[sh] = (n + 1, max(worst, ln), mid if ln > worst else at)
    for sh, (n, worst, at) in longs.items():            # one flag per shape, at its longest stitch
        flags.append({"kind": "long_stitch", "shape": sh, "at_mm": [round(at[0], 2), round(at[1], 2)],
                      "detail": f"{n} stitch(es) over {LONG_STITCH_MM:g} mm, longest {worst:.1f} mm"})
    k = 96.0 / 25.4                              # density_grid takes px; these are already mm
    grid = density_grid([(x * k, y * k) for x, y in pts_mm], cell_mm=1.0)
    for h in (hotspots(grid, warn=warn) if grid else []):
        c = (h["x_mm"] + h["w_mm"] / 2, h["y_mm"] + h["h_mm"] / 2)
        flags.append({"kind": "density", "shape": nearest_shape(c), "at_mm": [round(c[0], 2), round(c[1], 2)],
                      "detail": f"{h['peak_per_mm2']}/mm² over {h['area_mm2']} mm² ({h['severity']})"})
    colors = [b["color"] for b in blocks]
    totals = {"stitches": len(pts_mm), "threads": sorted(set(colors)),
              "color_changes": max(0, len(colors) - 1)}
    return flags, totals


KIND_COLOR = {"fan": "#ff9800", "hard_seam": "#ff1744", "ring_seam": "#40c4ff", "turn": "#80cbc4",
              "density": "#e040fb", "long_stitch": "#ffee58"}


def render(blocks, to_mm, flags, out_path, px_per_mm: float = 6.0, box=None):
    """The stitch plan drawn thin (stitch direction stays visible) on a dark ground, each flag
    circled in its kind's colour and labelled with its number in the flag list. box: (x0, y0, x1,
    y1) document mm to crop to."""
    from PIL import Image, ImageDraw, ImageFont

    pts = [to_mm(p) for b in blocks for path in b["paths"] for p in path]
    if not pts:
        return None
    x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
    x1, y1 = max(p[0] for p in pts), max(p[1] for p in pts)
    if box:
        x0, y0, x1, y1 = box
    pad = 6
    W, H = int((x1 - x0) * px_per_mm) + 2 * pad, int((y1 - y0) * px_per_mm) + 2 * pad
    im = Image.new("RGB", (W, H), "#141414")
    d = ImageDraw.Draw(im)
    T = lambda p: (pad + (p[0] - x0) * px_per_mm, pad + (p[1] - y0) * px_per_mm)
    for b in blocks:
        col = b["color"] if re.fullmatch(r"#[0-9a-fA-F]{6}", b["color"] or "") else "#ffffff"
        for path in b["paths"]:
            d.line([T(to_mm(p)) for p in path], fill=col, width=1)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(12, int(px_per_mm * 2.2)))
    except OSError:
        font = ImageFont.load_default()
    r = max(8, int(px_per_mm * 2.5))
    for n, f in enumerate(flags, 1):
        cx, cy = T(f["at_mm"])
        if not (0 <= cx <= W and 0 <= cy <= H):
            continue
        c = KIND_COLOR.get(f["kind"], "#ffffff")
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=c, width=2)
        d.text((cx + r + 2, cy - r), str(n), fill=c, font=font)
    im.save(out_path)
    return out_path
