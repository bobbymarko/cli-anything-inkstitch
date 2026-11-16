"""Artwork shapes → what to digitize: fills, duplicates, threads and sewing order.

Vector art from a design program is drawn for screens, not for a machine, and three of its habits
cost stitches or colour changes if digitized as found:

* **Colour by CSS class.** Illustrator's SVG export puts `class="st0"` on each path and the fill in a
  `<style>` sheet, so a reader that only looks at `style=` and `fill=` misses every shape.
* **Shapes drawn twice.** An export can hold the art twice in the same place (a live copy and an
  outlined one, or a darker shape laid exactly over a lighter one). On screen the top copy wins; a
  digitizer that plans both sews every stitch twice, doubling the stitch count and the density.
* **Near-identical colours.** Three greens a few ΔE apart look like one thread and sew as one thread,
  but kept separate they are three colour changes, and the shapes of one colour may be interleaved
  with others in drawing order, adding changes again.

This module answers those before planning: `css_fills`/`element_fill` read the fill wherever it is
set, `dedupe` drops shapes hidden under an identical later one, `merge_colors` clusters fills into
threads by CIEDE2000 distance, and `thread_order` sews each thread as one block wherever that
doesn't change which shape lies on top. Pure geometry and colour math (shapely), no engine calls.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Two fills closer than this (CIEDE2000) are one thread. ~1 is a just-noticeable difference on
# screen; thread charts step about 5-10 between neighbouring shades of one hue, so fills within 8
# are the same thread on any chart. Distinct design colours (a light and a dark green) sit at 15+.
MERGE_DE = 8.0
# Two shapes are the same drawing when their outlines differ by less than this share of the area.
DUPLICATE_AREA_PCT = 0.5
# Shapes of different threads overlapping by less than this (mm²) don't constrain sewing order:
# rails of neighbouring shapes touching at an edge are not one shape lying on another.
LAYER_OVERLAP_MM2 = 0.5

_NAMED = {"white": "#ffffff", "black": "#000000", "red": "#ff0000", "green": "#008000",
          "blue": "#0000ff", "yellow": "#ffff00", "gray": "#808080", "grey": "#808080"}


def normalize_color(value: str | None) -> str | None:
    """'#fd0', '#FFDD00', 'yellow' → '#ffdd00'; 'none', unset, url(#grad) → None."""
    if not value:
        return None
    v = value.strip().lower()
    if v in _NAMED:
        return _NAMED[v]
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", v)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h


def css_fills(root) -> dict[str, str]:
    """`.cls { fill: #xxx }` rules from every <style> sheet: class name → fill colour. Only simple
    class selectors (also comma lists), which is what design-program exports write."""
    out: dict[str, str] = {}
    for st in root.iter():
        if not isinstance(st.tag, str) or not st.tag.endswith("style") or not st.text:
            continue
        text = re.sub(r"/\*.*?\*/", "", st.text, flags=re.S)
        for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", text):
            m = re.search(r"(?:^|;)\s*fill\s*:\s*([^;]+)", body)
            if not m:
                continue
            for s in sel.split(","):
                s = s.strip()
                if re.fullmatch(r"\.[A-Za-z0-9_-]+", s):
                    out[s[1:]] = m.group(1).strip()
    return out


def _style_value(el, prop: str) -> str | None:
    m = re.search(rf"(?:^|;)\s*{prop}\s*:\s*([^;]+)", el.get("style") or "")
    return m.group(1).strip() if m else None


def element_fill(el, classes: dict[str, str]) -> str | None:
    """The fill an SVG renderer would paint this element with, normalized; None for no fill.
    Precedence as in CSS: style attribute, then class rules, then the fill presentation attribute.
    Inherited fills (set on a parent group) are followed up the tree."""
    e = el
    while e is not None and isinstance(e.tag, str):
        v = _style_value(e, "fill")
        if v is None:
            for c in (e.get("class") or "").split():
                if c in classes:
                    v = classes[c]
        if v is None:
            v = e.get("fill")
        if v is not None:
            return normalize_color(v)
        e = e.getparent()
    return "#000000"                      # SVG's initial fill


def hidden(el) -> bool:
    """display:none or visibility:hidden on the element or any ancestor (a hidden layer)."""
    e = el
    while e is not None and isinstance(e.tag, str):
        if (_style_value(e, "display") or e.get("display")) == "none":
            return True
        if (_style_value(e, "visibility") or e.get("visibility")) in ("hidden", "collapse"):
            return True
        e = e.getparent()
    return False


# ---------------------------------------------------------------- duplicates

def outline_polygon(contours):
    """Contours (outer ring first, then holes; even-odd) as one shapely geometry."""
    from shapely.geometry import Polygon

    geom = None
    for c in contours:
        if len(c) < 3:
            continue
        p = Polygon(c)
        p = p if p.is_valid else p.buffer(0)
        geom = p if geom is None else geom.symmetric_difference(p)
    return geom


KNOCKOUT_MIN_MM2 = 2.0   # same-thread shapes overlapping more than this would stack two satins
KNOCKOUT_TUCK_MM = 0.3   # ...so the later one is cut back to tuck this far under the earlier one


def knockout_overlaps(contours: list, polys: list, threads: list[str], order: list[int],
                      labels: list[str] | None = None) -> tuple[list, list[dict]]:
    """Where two shapes of one thread overlap (a V or butt joint drawn as two overlapping strokes:
    the North Stars N's diagonal over its stem, 56 mm2), cut the one sewn LATER back so it tucks
    KNOCKOUT_TUCK_MM under the earlier one's edge, instead of sewing two full satins on top of each
    other. Bob's V-joint rule (DW MOM back, 11/14): never stack two full satins at a V; knock out
    by default (first stroke whole, second tucked ~0.3 mm under it). A mitre is for a wide
    symmetric V only and isn't attempted here.

    Only when the cut leaves one piece with at least half the shape (an end joint): a stroke that
    crosses straight through another, or one mostly inside it (a highlight), is left as drawn.
    Different threads are left alone: there the overlap is layering. Returns (contours with the
    cut shapes replaced, one record per cut)."""
    n = len(contours)
    pos = {i: k for k, i in enumerate(order)}
    out, geo, cuts = list(contours), list(polys), []
    for j in sorted(range(n), key=lambda q: pos.get(q, q)):
        for i in sorted(range(n), key=lambda q: pos.get(q, q)):
            if pos.get(i, i) >= pos.get(j, j) or threads[i] != threads[j]:
                continue
            a, b = geo[i], geo[j]
            if a is None or b is None or not a.intersects(b):
                continue
            ov = a.intersection(b).area
            if ov <= KNOCKOUT_MIN_MM2:
                continue
            # the part outside the first stroke, reaching KNOCKOUT_TUCK_MM back under it only where
            # it continues out of it (b minus a shrunk by the tuck would also keep a sliver all
            # round a's edge inside the overlap: a stray satin along the stem's foot)
            rest = b.difference(a)
            cut = b.intersection(rest.buffer(KNOCKOUT_TUCK_MM, join_style=2)).buffer(0)
            pieces = [g for g in getattr(cut, "geoms", [cut]) if g.geom_type == "Polygon" and g.area >= 0.5]
            if len(pieces) != 1 or pieces[0].area < 0.5 * b.area:     # crumbs of the outline don't count
                continue
            cut = pieces[0]
            geo[j] = cut
            out[j] = [[tuple(map(float, q)) for q in list(cut.exterior.coords)[:-1]]] + \
                     [[tuple(map(float, q)) for q in list(r.coords)[:-1]] for r in cut.interiors]
            cuts.append({"shape": labels[j] if labels else j, "tucked_under": labels[i] if labels else i,
                         "overlap_mm2": round(ov, 1)})
    return out, cuts


def dedupe(shapes: list[tuple[str, list]]) -> tuple[list[int], list[dict]]:
    """Indices of the shapes to keep, and the ones dropped: a shape whose outline a LATER shape
    repeats (bounding boxes within 0.1 mm, outlines differing by under DUPLICATE_AREA_PCT of the
    area) is hidden under it, whatever its colour, so only the later, visible one is sewn.
    shapes: (id, contours in mm)."""
    polys = [outline_polygon(c) for _, c in shapes]
    dropped: dict[int, int] = {}
    for i, a in enumerate(polys):
        if a is None or a.is_empty:
            continue
        for j in range(len(polys) - 1, i, -1):
            b = polys[j]
            if j in dropped or b is None or b.is_empty:
                continue
            if max(abs(u - v) for u, v in zip(a.bounds, b.bounds)) > 0.1:
                continue
            if a.symmetric_difference(b).area <= DUPLICATE_AREA_PCT / 100 * max(a.area, b.area):
                dropped[i] = j
                break
    keep = [i for i in range(len(shapes)) if i not in dropped]
    return keep, [{"id": shapes[i][0], "under": shapes[j][0]} for i, j in sorted(dropped.items())]


# ---------------------------------------------------------------- threads

def _lab(hex_color: str) -> tuple[float, float, float]:
    """sRGB hex → CIE L*a*b* (D65)."""
    h = hex_color.lstrip("#")
    rgb = [int(h[k:k + 2], 16) / 255 for k in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    r, g, b = lin
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b)
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(c1: str, c2: str) -> float:
    """CIEDE2000 colour difference between two hex colours."""
    return delta_e_lab(_lab(c1), _lab(c2))


def delta_e_lab(lab1, lab2) -> float:
    """CIEDE2000 (Sharma, Wu & Dalal 2005) between two L*a*b* triples."""
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cm = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cm ** 7 / (Cm ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    else:
        dhp = h2p - h1p - 360 if h2p > h1p else h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lpm, Cpm = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hpm = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hpm = (h1p + h2p) / 2
    else:
        hpm = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hpm - 30)) + 0.24 * math.cos(math.radians(2 * hpm))
         + 0.32 * math.cos(math.radians(3 * hpm + 6)) - 0.20 * math.cos(math.radians(4 * hpm - 63)))
    dtheta = 30 * math.exp(-(((hpm - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cpm ** 7 / (Cpm ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lpm - 50) ** 2 / math.sqrt(20 + (Lpm - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cpm, 1 + 0.015 * Cpm * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


@dataclass
class ThreadPlan:
    thread: dict[str, str]                       # fill colour → the thread colour it sews in
    merges: list[dict] = field(default_factory=list)

    @property
    def threads(self) -> list[str]:
        return sorted(set(self.thread.values()))


def merge_colors(areas: dict[str, float], max_de: float = MERGE_DE,
                 max_colors: int | None = None) -> ThreadPlan:
    """Cluster fill colours into threads. areas: colour → total area sewn in it (mm²).

    Agglomerative, complete linkage: two clusters join only if EVERY pair of their colours is within
    max_de, so a chain of small steps can't drift from yellow to green. Then, if max_colors is set,
    the closest clusters keep joining (whatever the distance) until there are that many. Each
    cluster sews in its biggest colour by area (the thread the eye sees most of)."""
    cols = sorted(areas, key=lambda c: -areas[c])
    clusters = [[c] for c in cols]

    def link(a, b):
        return max(delta_e(x, y) for x in a for y in b)

    def closest():
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = link(clusters[i], clusters[j])
                if best is None or d < best[0]:
                    best = (d, i, j)
        return best

    while len(clusters) > 1:
        d, i, j = closest()
        if d > max_de and (max_colors is None or len(clusters) <= max_colors):
            break
        clusters[i] += clusters.pop(j)
    plan = ThreadPlan({})
    for cl in clusters:
        rep = max(cl, key=lambda c: areas[c])
        for c in cl:
            plan.thread[c] = rep
        if len(cl) > 1:
            plan.merges.append({"thread": rep, "fills": sorted(cl, key=lambda c: -areas[c]),
                                "max_delta_e": round(max(delta_e(x, y) for x in cl for y in cl), 1)})
    return plan


def _layer_before(threads: list[str], polys: list) -> dict[int, set[int]]:
    """For each shape, the shapes that must sew before it: an earlier-drawn shape of another
    thread it overlaps (the later one lies on top)."""
    n = len(threads)
    before: dict[int, set[int]] = {j: set() for j in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if threads[i] == threads[j] or polys[i] is None or polys[j] is None:
                continue
            if not polys[i].intersects(polys[j]):
                continue
            if polys[i].intersection(polys[j]).area > LAYER_OVERLAP_MM2:
                before[j].add(i)
    return before


INTERLOCK_GAP_MM = 8.0   # shapes of any thread this close interlock (a crest's heart, crown and knots)
REGION_GAP_MM = 6.0      # shapes of one thread this close are one element (a knot's strands)


def _components(n: int, linked) -> list[int]:
    """Single-link components: comp[i] for i in range(n), linked(i, j) -> bool."""
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if find(i) != find(j) and linked(i, j):
                parent[find(i)] = find(j)
    return [find(i) for i in range(n)]


def centre_out_order(threads: list[str], polys: list, origin: tuple[float, float]
                     ) -> tuple[list[int], list[dict]]:
    """A sewing order for stretch fabric: outward from `origin`, each element finished first.

    On knit and fleece the fabric moves as it's sewn; sewing outward from the middle pushes that
    movement out to free fabric instead of into finished work, and finishing one element before
    the next keeps its parts registered to each other (Thready / Bob 11/15, HIDA champ back).

    - Elements: shapes of one thread within REGION_GAP_MM (a knot's strands), each sewn whole.
    - Interlocking clusters: shapes of ANY thread within INTERLOCK_GAP_MM (a crest's heart, crown
      and knots). One with several threads is sewn as one unit, its threads batched (each thread's
      nearest element to the origin first). Free-standing single-thread clusters (letters of a
      name) are batched per thread, so spaced lettering doesn't add colour changes.
    - Units, then each thread batch, then elements sew by their nearest distance to the origin;
      inside an element, shapes sew outward from the element's own middle. Layering still wins: an earlier-drawn shape of another thread that a shape
      overlaps sews first (as thread_order).

    Returns (order, splits): splits lists each thread sewn in more than one block (an extra colour
    change the outward order costs)."""
    from shapely.geometry import Point

    n = len(threads)
    if n == 0:
        return [], []
    before = _layer_before(threads, polys)
    o = Point(origin)
    far = float("inf")
    d = [polys[i].distance(o) if polys[i] is not None else far for i in range(n)]

    def near(i, j, gap):
        a, b = polys[i], polys[j]
        if a is None or b is None:
            return False
        ax0, ay0, ax1, ay1 = a.bounds
        bx0, by0, bx1, by1 = b.bounds
        if bx0 > ax1 + gap or ax0 > bx1 + gap or by0 > ay1 + gap or ay0 > by1 + gap:
            return False
        return a.distance(b) <= gap

    cluster = _components(n, lambda i, j: near(i, j, INTERLOCK_GAP_MM))
    element = _components(n, lambda i, j: threads[i] == threads[j] and near(i, j, REGION_GAP_MM))
    multi = {c for c in set(cluster) if len({threads[i] for i in range(n) if cluster[i] == c}) > 1}
    unit = [("cluster", cluster[i]) if cluster[i] in multi else ("thread", threads[i]) for i in range(n)]

    def rank(keys):
        best: dict = {}
        for i in range(n):
            best[keys[i]] = min(best.get(keys[i], far), d[i])
        return best

    unit_r = rank(unit)
    batch = [(unit[i], threads[i]) for i in range(n)]
    batch_r = rank(batch)
    elem_r = rank(element)
    # inside an element, shapes sew outward from the element's own middle (a heart's inner swirl
    # before its two halves, a knot's strands from its centre): the elements themselves already
    # sew outward from the origin
    from shapely.ops import unary_union
    centre = {}
    for e in set(element):
        g = [polys[i] for i in range(n) if element[i] == e and polys[i] is not None]
        if g:
            x0, y0, x1, y1 = unary_union(g).bounds
            centre[e] = Point((x0 + x1) / 2, (y0 + y1) / 2)
    d_in = [polys[i].distance(centre[element[i]]) if polys[i] is not None and element[i] in centre else far
            for i in range(n)]
    key = {i: (unit_r[unit[i]], str(unit[i]), batch_r[batch[i]], threads[i], elem_r[element[i]], element[i],
               d_in[i], d[i], i) for i in range(n)}
    want = sorted(range(n), key=key.get)
    done: list[int] = []
    left = set(range(n))
    while left:
        k = next((q for q in want if q in left and before[q] <= set(done)), None)
        if k is None:                      # a layering cycle can't happen (before is drawing order)
            k = min(left)
        done.append(k)
        left.discard(k)
    blocks = [threads[done[0]]]
    for k in done[1:]:
        if threads[k] != blocks[-1]:
            blocks.append(threads[k])
    splits = [{"thread": t, "blocks": blocks.count(t), "reason": "sewn outward from the middle"}
              for t in dict.fromkeys(blocks) if blocks.count(t) > 1]
    return done, splits


def thread_order(threads: list[str], polys: list) -> tuple[list[int], list[dict]]:
    """A sewing order that sews each thread as one block where layering allows.

    threads[i] is shape i's thread in drawing order; polys[i] its outline (shapely, mm). Shapes of
    different threads that overlap must keep their drawing order (the later one lies on top), so
    those pairs are constraints; everything else may move. Greedy topological sort: keep sewing
    the current thread while any shape of it is free to sew, else start the free thread that
    appears first in the drawing. Returns the order and the pairs that forced a thread to be split
    (each one an extra colour change)."""
    n = len(threads)
    before = _layer_before(threads, polys)
    first = {}
    for i, t in enumerate(threads):
        first.setdefault(t, i)
    done: list[int] = []
    left = set(range(n))
    cur = None
    while left:
        free = sorted(k for k in left if before[k] <= set(done))
        same = [k for k in free if threads[k] == cur]
        if same:
            k = same[0]
        else:
            k = min(free, key=lambda q: (first[threads[q]], q))
            cur = threads[k]
        done.append(k)
        left.discard(k)
    splits = []
    blocks = [threads[done[0]]] if done else []
    for k in done[1:]:
        if threads[k] != blocks[-1]:
            blocks.append(threads[k])
    seen = set()
    for t in blocks:
        if t in seen:
            splits.append({"thread": t, "reason": "a shape of another thread lies under it and over another of its own"})
        seen.add(t)
    return done, splits
