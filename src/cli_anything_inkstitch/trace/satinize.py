"""Glyph → satin columns via the medial axis (variable-width rails).

Why this exists: centerline + CONSTANT stroke width destroys letterforms —
short branches (crossbars, dots, terminals) get dropped and uniform width
erases stroke contrast; offset rails overshoot at tight loops (measured on
the celtic-patch v1: 'Celtic' stitched as 'eluc').  The information a satin
column needs is all in the raster mask:

1. skeletonize the mask (Zhang-Suen thinning),
2. chamfer distance transform = LOCAL half-width at every skeleton pixel,
3. split the skeleton into branches at junctions, pruning only spurs
   shorter than the local width (a crossbar is longer than the stroke is
   wide — that asymmetry is what separates features from noise),
4. rails = branch polyline offset perpendicular by the LOCAL radius —
   the true boundary for ribbon-like shapes, so terminals taper and loops
   cannot overshoot (the radius shrinks with the shape),
5. rungs pair rail points born from the same skeleton sample — no twist.

Engine contract (readers cited):
* rails+rungs layout — lib/elements/satin_column.py: subpaths = 2 rails,
  additional subpaths are rungs; each rung must intersect both rails.
* element dispatch — lib/elements/utils/nodes.py node_to_elements: stroke
  paint + inkstitch:satin_column="True" → SatinColumn.
"""

from __future__ import annotations

import math
from collections import deque

# 8-neighborhood in Zhang-Suen order P2..P9 (N, NE, E, SE, S, SW, W, NW)
_ZS = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))


def skeletonize(mask: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Zhang-Suen thinning of a pixel set (shape pixels, any coordinates)."""
    img = set(mask)

    def neighbors(p):
        x, y = p
        return [(x + dx, y + dy) in img for dx, dy in _ZS]

    def transitions(nb):
        return sum(1 for i in range(8) if not nb[i] and nb[(i + 1) % 8])

    changed = True
    while changed:
        changed = False
        for phase in (0, 1):
            to_del = []
            for p in img:
                nb = neighbors(p)
                bn = sum(nb)
                if not (2 <= bn <= 6) or transitions(nb) != 1:
                    continue
                # nb indexes: 0=N 2=E 4=S 6=W
                if phase == 0:
                    if (nb[0] and nb[2] and nb[4]) or (nb[2] and nb[4] and nb[6]):
                        continue
                else:
                    if (nb[0] and nb[2] and nb[6]) or (nb[0] and nb[4] and nb[6]):
                        continue
                to_del.append(p)
            if to_del:
                img.difference_update(to_del)
                changed = True
    return img


def distance_transform(mask: set[tuple[int, int]]) -> dict[tuple[int, int], float]:
    """Chamfer 3-4 distance (÷3 ≈ px) from each shape pixel to background."""
    if not mask:
        return {}
    xs = [p[0] for p in mask]
    ys = [p[1] for p in mask]
    x0, x1 = min(xs) - 1, max(xs) + 1
    y0, y1 = min(ys) - 1, max(ys) + 1
    INF = 10 ** 9
    dist = {p: INF for p in mask}
    # forward pass
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            p = (x, y)
            if p not in dist:
                continue
            best = dist[p]
            for dx, dy, w in ((-1, 0, 3), (0, -1, 3), (-1, -1, 4), (1, -1, 4)):
                q = (x + dx, y + dy)
                best = min(best, dist.get(q, 0) + w if q in dist else w)
            dist[p] = best
    # backward pass
    for y in range(y1, y0 - 1, -1):
        for x in range(x1, x0 - 1, -1):
            p = (x, y)
            if p not in dist:
                continue
            best = dist[p]
            for dx, dy, w in ((1, 0, 3), (0, 1, 3), (1, 1, 4), (-1, 1, 4)):
                q = (x + dx, y + dy)
                best = min(best, dist.get(q, 0) + w if q in dist else w)
            dist[p] = best
    return {p: d / 3.0 for p, d in dist.items()}


def _neighbors8(p, pix):
    x, y = p
    return [(x + dx, y + dy) for dx, dy in _ZS if (x + dx, y + dy) in pix]


def _graph_neighbors(p, pix):
    """Connectivity-reduced adjacency: a diagonal neighbor is linked only
    when neither of its adjacent orthogonals is present.  Skeleton
    staircases otherwise read as fake 3-way junctions and shred closed
    loops (a ring came back as arcs, never as a loop)."""
    x, y = p
    out = []
    for dx, dy in _ZS:
        q = (x + dx, y + dy)
        if q not in pix:
            continue
        if dx and dy:
            if (x + dx, y) in pix or (x, y + dy) in pix:
                continue
        out.append(q)
    return out


def extract_branches(skel: set[tuple[int, int]],
                     radii: dict[tuple[int, int], float],
                     min_spur_factor: float = 1.2):
    """Split a skeleton into branch polylines at junction pixels.

    Endpoints/junctions = pixels with ≠2 neighbors.  A spur (endpoint
    branch) is pruned only when SHORTER than min_spur_factor × the local
    width at its junction — crossbars and dots are longer than the stroke
    is wide and survive; medial-axis corner artifacts don't.  Closed loops
    with no junction (letter 'o') come back as single closed branches.
    """
    pix = set(skel)
    nodes = {p for p in pix if len(_graph_neighbors(p, pix)) != 2}
    branches = []
    visited_edges = set()

    def walk(start, first):
        path = [start, first]
        prev, cur = start, first
        while cur not in nodes and cur != start:
            nxt = [n for n in _graph_neighbors(cur, pix) if n != prev]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            path.append(cur)
        return path

    for n in nodes:
        for nb in _graph_neighbors(n, pix):
            if (n, nb) in visited_edges:
                continue
            path = walk(n, nb)
            visited_edges.add((n, nb))
            visited_edges.add((path[-1], path[-2]))
            branches.append(path)
    # pure loops (no junction pixels at all)
    seen = set(q for b in branches for q in b)
    for p in list(pix):
        if p in seen or p in nodes:
            continue
        loop = [p]
        nbs = _graph_neighbors(p, pix)
        if not nbs:
            seen.add(p)
            continue
        prev, cur = p, nbs[0]
        while cur != p:
            loop.append(cur)
            nxt = [n for n in _graph_neighbors(cur, pix) if n != prev]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
        loop.append(p)
        branches.append(loop)
        seen.update(loop)

    # spur pruning: endpoint branches shorter than the junction's width
    kept = []
    for b in branches:
        is_spur = (len(_graph_neighbors(b[0], pix)) == 1) ^ \
                  (len(_graph_neighbors(b[-1], pix)) == 1)
        if is_spur:
            length = sum(math.hypot(b[i+1][0]-b[i][0], b[i+1][1]-b[i][1])
                         for i in range(len(b) - 1))
            junction = b[-1] if len(_neighbors8(b[0], pix)) == 1 else b[0]
            local_w = 2 * radii.get(junction, 1.0)
            if length < min_spur_factor * local_w:
                continue
        kept.append(b)
    return kept


def _end_tangent(branch, at_start: bool, span: int = 6):
    """Unit direction pointing OUT of the branch at one end."""
    if at_start:
        a, b = branch[min(span, len(branch) - 1)], branch[0]
    else:
        a, b = branch[max(0, len(branch) - 1 - span)], branch[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1.0
    return dx / L, dy / L


def merge_collinear_branches(branches, angle_dot: float = -0.75):
    """Join branch pairs that continue straight through a junction.

    A skeleton junction breaks the ribbon it sits on: a smooth spiral
    crossed by a spur becomes fragments whose columns lie at arbitrary
    angles (user-visible "random shapes" on the celtic J).  Where two
    branch ends meet and their outward tangents oppose (dot < angle_dot),
    they are one stroke and get spliced back together.  Side branches at
    real corners (L joints, T crossbars) don't oppose and stay separate.
    """
    branches = [list(b) for b in branches]
    merged = True
    while merged:
        merged = False
        for i in range(len(branches)):
            if merged:
                break
            for j in range(len(branches)):
                if i == j:
                    continue
                bi, bj = branches[i], branches[j]
                if bi[0] == bi[-1] or bj[0] == bj[-1]:
                    continue  # closed loops don't merge
                for i_end, j_end in ((-1, 0), (-1, -1), (0, 0), (0, -1)):
                    pi = bi[i_end]
                    pj = bj[j_end]
                    if abs(pi[0] - pj[0]) > 2 or abs(pi[1] - pj[1]) > 2:
                        continue
                    ti = _end_tangent(bi, at_start=(i_end == 0))
                    tj = _end_tangent(bj, at_start=(j_end == 0))
                    if ti[0]*tj[0] + ti[1]*tj[1] > angle_dot:
                        continue
                    a = bi if i_end == -1 else bi[::-1]
                    b = bj if j_end == 0 else bj[::-1]
                    branches[i] = a + b
                    del branches[j]
                    merged = True
                    break
                if merged:
                    break
    return branches


def _smooth(pts, passes=2):
    for _ in range(passes):
        out = [pts[0]]
        for i in range(1, len(pts) - 1):
            out.append(((pts[i-1][0] + 2*pts[i][0] + pts[i+1][0]) / 4,
                        (pts[i-1][1] + 2*pts[i][1] + pts[i+1][1]) / 4))
        out.append(pts[-1])
        pts = out
    return pts


def branch_to_rails(branch, radii, min_half_width: float = 1.0,
                    sample_every: float = 2.0):
    """Variable-width rails for one skeleton branch, in source px.

    Rails are the branch offset perpendicular by the LOCAL radius (smoothed
    along the branch).  Returns (rail_a, rail_b, closed).  For a closed
    branch the rails are closed rings.
    """
    closed = branch[0] == branch[-1] and len(branch) > 3
    pts = [(float(x), float(y)) for x, y in branch]
    pts = _smooth(pts, passes=3)
    # resample to regular spacing so normals are stable
    total = sum(math.hypot(pts[i+1][0]-pts[i][0], pts[i+1][1]-pts[i][1])
                for i in range(len(pts) - 1))
    n = max(4, int(total / sample_every))
    resampled = []
    ri = []
    step = total / n
    acc = 0.0
    target = 0.0
    i = 0
    while i < len(pts) - 1 and len(resampled) <= n:
        a, b = pts[i], pts[i + 1]
        seg = math.hypot(b[0]-a[0], b[1]-a[1])
        while acc + seg >= target and len(resampled) <= n:
            t = (target - acc) / seg if seg else 0
            x, y = a[0] + (b[0]-a[0]) * t, a[1] + (b[1]-a[1]) * t
            resampled.append((x, y))
            key = (int(round(x)), int(round(y)))
            r = radii.get(key)
            if r is None:  # nearest skeleton pixel radius
                r = min((radii[q] for q in radii
                         if abs(q[0]-x) < 4 and abs(q[1]-y) < 4),
                        default=min_half_width)
            ri.append(r)  # raw; taper truncation needs it — floored below
            target += step
        acc += seg
        i += 1
    if len(resampled) < 2:
        return None
    # smooth radii so rails don't jitter
    for _ in range(2):
        ri = [ri[0]] + [(ri[j-1] + 2*ri[j] + ri[j+1]) / 4
                        for j in range(1, len(ri)-1)] + [ri[-1]]
    if not closed:
        # where the shape genuinely tapers below stitchable width (a spiral
        # tail narrowing to a point), END the column — flooring the radius
        # there manufactures width the artwork doesn't have and the rails
        # collapse into a pile of folds (user-visible garbage at the
        # celtic C's tail)
        lo = 0
        hi = len(resampled)
        while lo < hi - 2 and ri[lo] < 0.55 * min_half_width:
            lo += 1
        while hi > lo + 2 and ri[hi - 1] < 0.55 * min_half_width:
            hi -= 1
        resampled = resampled[lo:hi]
        ri = ri[lo:hi]
        if len(resampled) < 2:
            return None
    ri = [max(min_half_width, r) for r in ri]
    if not closed:
        # the medial axis stops one radius short of the true stroke end —
        # extend both ends along the tangent so the satin caps the terminal
        # (uncapped columns left every letter tip uncovered, measured 12.8%
        # of the celtic-patch ink)
        (x0, y0), (x1, y1) = resampled[0], resampled[1]
        L = math.hypot(x1 - x0, y1 - y0) or 1.0
        resampled.insert(0, (x0 - (x1-x0)/L * ri[0], y0 - (y1-y0)/L * ri[0]))
        ri.insert(0, ri[0])
        (xa, ya), (xb, yb) = resampled[-2], resampled[-1]
        L = math.hypot(xb - xa, yb - ya) or 1.0
        resampled.append((xb + (xb-xa)/L * ri[-1], yb + (yb-ya)/L * ri[-1]))
        ri.append(ri[-1])
    rail_a, rail_b = [], []
    m = len(resampled)
    for j, (x, y) in enumerate(resampled):
        p_prev = resampled[max(0, j-1)]
        p_next = resampled[min(m-1, j+1)]
        dx, dy = p_next[0]-p_prev[0], p_next[1]-p_prev[1]
        L = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / L, dx / L
        rail_a.append((x + nx * ri[j], y + ny * ri[j]))
        rail_b.append((x - nx * ri[j], y - ny * ri[j]))
    if closed:
        rail_a.append(rail_a[0])
        rail_b.append(rail_b[0])
    # offset rails fold at curvature tighter than the half-width — cut the
    # folds instead of shipping self-crossing rails
    rail_a = remove_folds(rail_a)
    rail_b = remove_folds(rail_b)
    if closed:
        # fold cutting can leave closed rails with opposite winding, which
        # sweeps the zigzag across the shape — align orientations
        def _area(p):
            return sum(p[i][0]*p[(i+1) % len(p)][1]
                       - p[(i+1) % len(p)][0]*p[i][1]
                       for i in range(len(p) - 1))
        if _area(rail_a) * _area(rail_b) < 0:
            rail_b = rail_b[::-1]
    return rail_a, rail_b, closed


def remove_folds(pts, max_span: int = 60, max_rounds: int = 20):
    """Cut small self-intersection folds out of a polyline.

    At curvature tighter than the local half-width, an offset rail folds
    over itself in a short loop.  Cutting the loop (splice a straight
    segment across it) is what a digitizer would do by hand; large-span
    crossings are structural and left for the caller/gate to surface.
    """
    from cli_anything_inkstitch.artifact.gate import segments_intersect
    pts = list(pts)
    for _ in range(max_rounds):
        n = len(pts) - 1
        closed = (n >= 2 and abs(pts[0][0] - pts[n][0]) < 1e-6
                  and abs(pts[0][1] - pts[n][1]) < 1e-6)
        found = None
        for i in range(n):
            hi = min(n, i + max_span)
            for j in range(i + 2, hi):
                if closed and i == 0 and j == n - 1:
                    continue
                if segments_intersect(pts[i], pts[i+1], pts[j], pts[j+1]):
                    found = (i, j)
                    break
            if found:
                break
        if not found:
            return pts
        i, j = found
        pts = pts[:i + 1] + pts[j + 1:]
    return pts


def rails_to_satin_d(rail_a, rail_b, transform=None,
                     rung_spacing: float = 0.0) -> str:
    """Satin path data: two rails + explicit rungs from paired samples.

    Rung pairing by construction (same skeleton sample) — the twist-free
    property the medial-axis approach buys.  transform maps source px →
    doc px, applied last.

    rung_spacing is an ARC LENGTH along rail_a (pre-transform units);
    rungs guide the engine's zigzag direction and a human fixing a column
    drags them — a handful per column, not one per sample (index-spaced
    rungs put hundreds of nodes on a spiral, measured unusable).  0 means
    auto: ~8× the mean rail separation.
    """
    def tx(p):
        if transform is None:
            return p
        a, b, c, d, e, f = transform
        return (a*p[0] + c*p[1] + e, b*p[0] + d*p[1] + f)

    n = min(len(rail_a), len(rail_b))
    if rung_spacing <= 0:
        mean_sep = sum(math.hypot(rail_a[i][0]-rail_b[i][0],
                                  rail_a[i][1]-rail_b[i][1])
                       for i in range(n)) / max(1, n)
        rung_spacing = max(8 * mean_sep, 1e-6)

    def arc_point(rail, t):
        """Point at arc fraction t along a polyline."""
        total = sum(math.hypot(rail[i+1][0]-rail[i][0],
                               rail[i+1][1]-rail[i][1])
                    for i in range(len(rail) - 1))
        target = t * total
        acc = 0.0
        for i in range(len(rail) - 1):
            seg = math.hypot(rail[i+1][0]-rail[i][0], rail[i+1][1]-rail[i][1])
            if acc + seg >= target and seg > 0:
                u = (target - acc) / seg
                return (rail[i][0] + (rail[i+1][0]-rail[i][0]) * u,
                        rail[i][1] + (rail[i+1][1]-rail[i][1]) * u)
            acc += seg
        return rail[-1]

    # rung fractions: pairing by equal ARC FRACTION keeps rungs radial on
    # concentric curves — index pairing leaned the zigzag over on spirals
    # (the inner rail is shorter).  Placement is curvature-adaptive: a new
    # rung whenever the arc budget runs out OR the rail direction has
    # turned ~15° since the last one, so tight curls get the guidance the
    # engine needs to keep stitches perpendicular.
    fractions = []
    total_a = sum(math.hypot(rail_a[i+1][0]-rail_a[i][0],
                             rail_a[i+1][1]-rail_a[i][1])
                  for i in range(len(rail_a) - 1))
    arc = 0.0
    arc_since = rung_spacing / 2
    turn_since = 0.0
    prev_dir = None
    for i in range(1, len(rail_a)):
        seg = math.hypot(rail_a[i][0]-rail_a[i-1][0],
                         rail_a[i][1]-rail_a[i-1][1])
        if seg <= 0:
            continue
        d = ((rail_a[i][0]-rail_a[i-1][0]) / seg,
             (rail_a[i][1]-rail_a[i-1][1]) / seg)
        if prev_dir is not None:
            dot = max(-1.0, min(1.0, d[0]*prev_dir[0] + d[1]*prev_dir[1]))
            turn_since += math.degrees(math.acos(dot))
        prev_dir = d
        arc += seg
        arc_since += seg
        near_end = arc > total_a - rung_spacing / 3
        if not near_end and (arc_since >= rung_spacing or turn_since >= 15.0):
            fractions.append(arc / total_a)
            arc_since = 0.0
            turn_since = 0.0
    if not fractions:
        fractions = [0.5]

    A = [tx(p) for p in rail_a]
    B = [tx(p) for p in rail_b]
    parts = ["M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in A),
             "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in B)]
    for t in fractions:
        ax, ay = tx(arc_point(rail_a, t))
        bx, by = tx(arc_point(rail_b, t))
        dx, dy = bx - ax, by - ay
        parts.append(f"M {ax - dx*0.15:.2f},{ay - dy*0.15:.2f} "
                     f"L {bx + dx*0.15:.2f},{by + dy*0.15:.2f}")
    return " ".join(parts)


def satinize_mask(mask: set[tuple[int, int]], min_half_width: float = 1.0,
                  min_spur_factor: float = 1.2,
                  split_loops_shorter_than: float = 0.0):
    """Full pipeline for one shape: mask pixels → list of rail pairs.

    split_loops_shorter_than (px): closed skeleton loops shorter than this
    are split into two OPEN half-arc columns instead of one ring column.
    At small scale (a letter 'o' with a ~1mm counter) a closed ring satin
    sweeps across the hole — two open arcs is how small loop letters are
    digitized by hand.
    """
    skel = skeletonize(mask)
    if not skel:
        return []
    radii = distance_transform(mask)
    skel_radii = {p: radii.get(p, 1.0) for p in skel}
    out = []
    branches = merge_collinear_branches(
        extract_branches(skel, skel_radii, min_spur_factor))
    for branch in branches:
        # a column shorter than it is wide is a bartack, not a satin —
        # junction fragments this small are already covered by their
        # neighbors' terminal caps (273 columns collapsed to ~90 on the
        # celtic patch with this rule, coverage unchanged)
        length = sum(math.hypot(branch[i+1][0]-branch[i][0],
                                branch[i+1][1]-branch[i][1])
                     for i in range(len(branch) - 1))
        mean_r = sum(skel_radii.get(p, 1.0) for p in branch) / len(branch)
        if length < 2.4 * mean_r:
            continue
        closed = branch[0] == branch[-1] and len(branch) > 3
        if closed and split_loops_shorter_than > 0:
            length = sum(math.hypot(branch[i+1][0]-branch[i][0],
                                    branch[i+1][1]-branch[i][1])
                         for i in range(len(branch) - 1))
            if length < split_loops_shorter_than:
                half = len(branch) // 2
                for part in (branch[:half + 1], branch[half:]):
                    if len(part) >= 3:
                        rails = branch_to_rails(part, skel_radii,
                                                min_half_width)
                        if rails is not None:
                            out.append(rails)
                continue
        rails = branch_to_rails(branch, skel_radii, min_half_width)
        if rails is not None:
            out.append(rails)
    return out
