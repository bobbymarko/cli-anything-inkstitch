"""Convert a Chroma .rde design into an Ink/Stitch-editable SVG.

The point of this conversion is resizing. Scaling a DST scales baked stitches
and wrecks density; emitting Ink/Stitch *elements* lets the engine regenerate
stitches at whatever size you pick.

Stitch type comes from the file where it is recorded, and from measurement only
where it is not. Chroma stores a fill flag in the object's parameter block --
byte +56 from the 6E 02 00 00 marker, where 2 means fill (see FILL_FLAG_OFFSET).
It does not appear to store a satin-vs-running-stitch flag, so that split is
made by looking at the stitches: a satin reverses direction every stitch.

Conversion unit is a RUN WITHIN AN OBJECT, not the object. Chroma objects are
not homogeneous -- one object routinely mixes running stitch with satin, and
its stitch stream also carries underlay and travel that Ink/Stitch would
regenerate itself. Segmenting the stream lets each piece become the right
element and lets underlay be dropped rather than stitched twice.

Every inkstitch:* attribute written here is cited to the engine-side reader,
per the project's engine-contract rule:

  * element dispatch -- lib/elements/utils/nodes.py:node_to_elements
        fill color present            -> FillStitch
        stroke color + satin_column   -> SatinColumn (needs >1 subpath)
        stroke color, otherwise       -> Stroke
  * inkstitch:satin_column -- read by node_to_elements via
        element.get_boolean_param("satin_column", False). With exactly two
        subpaths SatinColumn.rungs synthesizes the rungs
        (lib/elements/satin_column.py:_synthesize_rungs), so we emit rails only.
  * inkstitch:zigzag_spacing_mm -- lib/elements/satin_column.py:263-272,
        get_float_param("zigzag_spacing_mm", 0.4), peak-to-peak in mm.
  * inkstitch:row_spacing_mm -- lib/elements/fill_stitch.py:364-379,
        get_float_param("row_spacing_mm", 0.25); derived from area/thread.
  * inkstitch:expand_mm -- lib/elements/fill_stitch.py:276-284,
        get_float_param('expand_mm', 0), compensates gaps between shapes.
  * inkstitch:angle -- lib/elements/fill_stitch.py:300-310, read with
        math.radians(get_float_param('angle', 0)), i.e. DEGREES.
  * stroke_method defaults to "running_stitch"
        (lib/elements/stroke.py:92-94), so a plain stroke needs no attribute.
  * <metadata><inkstitch:inkstitch_svg_version> -- lib/update.py
        update_inkstitch_document reads it via local-name() and, when it is
        absent (version 0) in a document that DOES carry inkstitch attributes,
        pops the "unversioned SVG" update prompt. Stamping the current version
        makes update_inkstitch_document return immediately. The value must
        match the installed engine: too low triggers a legacy migration, too
        high triggers a "created with a newer version" error.

Units are px (96 dpi user units) throughout. A mm-unit viewBox makes engine
tools drift scale silently.
"""

import math
import struct
import sys

sys.path.insert(0, __file__.rsplit('/', 1)[0])

from rde_decode import (OBJECT_TAGS, decrypt, read_colors,  # noqa: E402
                        read_sections, read_wstr, satin_runs)

# .rde coordinates are float32 in 0.1 mm; Inkscape user units are px at 96 dpi.
PX_PER_UNIT = 0.1 * 96.0 / 25.4

# lib/update.py INKSTITCH_SVG_VERSION for the engine this targets (3.3.0).
INKSTITCH_SVG_VERSION = 4
INKSTITCH_NS = 'http://inkstitch.org/namespace'
INKSCAPE_NS = 'http://www.inkscape.org/namespaces/inkscape'

PARAM_MARKER = b'\x6e\x02\x00\x00'
# Byte +56 from PARAM_MARKER is Chroma's fill flag. Over 13685 objects it split
# cleanly against measured stitch behaviour: value 2 -> median reversal 0.27
# (parallel rows), value 0 -> 0.73 (zigzag). It also agrees with the section
# tags, e.g. tag 0x66 is 97% value 2 and tag 0x67 is 100% value 0.
FILL_FLAG_OFFSET = 56
FILL_FLAG_VALUE = 2

# Fraction of a non-fill object's stitches that must fall inside satin runs
# before it is treated as a satin (the remainder being underlay and travel).
SATIN_COVERAGE = 0.25
# A satin narrower or shorter than this is a routing wobble, not a column.
MIN_SATIN_WIDTH_PX = 0.6
MIN_SATIN_LENGTH_PX = 2.0
SIMPLIFY_PX = 0.30
# Directional concentration above which stitches read as the parallel rows of
# a fill. Only used when Chroma's own fill flag could not be read.
FILL_CONCENTRATION = 0.60
# Isoperimetric compactness below which a contour is a thin open stroke rather
# than a fillable area (see _is_area).
SLIVER_COMPACTNESS = 0.15
# A contour must hold at least this share of the object's own stitches to be
# treated as the shape being filled; below it, it is a guide or a thin detail.
FILL_MIN_COVERAGE = 0.15
# Segments longer than this are jumps between shapes, not stitching, and must
# not be counted as thread laid down anywhere (also the cutoff in _thread_mm).
JUMP_MM = 15.0
# A nested contour is a counter (a hole) rather than a stitched detail when the
# thread inside it runs at this fraction of the parent's density or less.
# Chroma travels ACROSS a counter rather than around it, so a counter's
# interior is never empty: the nine counters in Lake Superior carry 0.00x-0.17x
# the surrounding fill's density. Measured over the 346 nested area-enclosing
# contours in 104 designs the split is bimodal -- 263 sit below 0.10x and the
# stitched ones cluster from 0.30x up -- so the cut goes in the gap between
# them, where only two contours in the whole corpus lie.
HOLE_THREAD_DENSITY_MAX = 0.25
# Cap on segments sampled for that density. Sampling every k-th segment scales
# the numerator and denominator alike, so the ratio is unaffected.
HOLE_SAMPLE_SEGMENTS = 3000
# Trim the thread when the move to the next object is longer than this. It is
# Ink/Stitch's own collapse_len default (lib/stitch_plan/stitch_plan.py:32-33),
# the distance above which the engine stops stitching through a gap and starts
# jumping it -- exactly the moves that leave a float on the finished piece.
# Trimming shorter moves as well is what Chroma's object list would imply, and
# it is wrong: in Flower Adult Hat 938 of 1139 moves are under 2 mm, so
# trimming per object would put a thousand needless trims in the file.
TRIM_MIN_JUMP_MM = 3.0
# Chroma overlaps adjacent shapes so their fills meet; Ink/Stitch does not by
# default, which leaves hairline gaps along every shared petal edge. expand_mm
# exists for exactly this ("compensate for gaps between shapes",
# lib/elements/fill_stitch.py:276-284, read via get_float_param('expand_mm', 0)).
FILL_EXPAND_MM = 0.2
# An object is only filled if its own stitches carry at least this share of the
# thread a fill of that area would need (area / 0.25 mm rows). Chroma's real
# fills run 1.2x-2.4x that figure; objects it merely outlined run ~0.05x, and
# filling those over-stitched one design by 8.9x. Measured over 769 fills and
# 2871 non-fills: at 0.25 no real fill is rejected.
FILL_DENSITY_MIN = 0.25
# Share of a filled object's thread that is fill rows rather than underlay/travel.
FILL_THREAD_SHARE = 0.85


def read_design(path):
    """Walk sections once: per-object stitches, outline contours, fill flag."""
    colors, objects = [], []
    for tag, payload in read_sections(path):
        if tag == 0x02:
            colors = read_colors(payload)
            continue
        if tag not in OBJECT_TAGS:
            continue
        p = decrypt(payload)
        if len(p) < 6:
            continue
        color_index, count = struct.unpack_from('<HI', p, 0)
        off, stitches, bad = 6, [], False
        for _ in range(count):
            if off + 9 > len(p):
                bad = True
                break
            x, y, flag = struct.unpack_from('<ffB', p, off)
            off += 11 if flag == 0x04 else 9
            stitches.append((x, y))
        if bad or not all(abs(v) < 1e5 for s in stitches for v in s):
            continue
        contours, after = _read_contours(p, off, tag)
        objects.append({'tag': tag, 'color': color_index,
                        'stitches': stitches, 'contours': contours,
                        'is_fill': _fill_flag(p, after)})
    return colors, objects


def _fill_flag(p, after):
    if after is None:
        return None
    marker = p.find(PARAM_MARKER, after)
    if marker < 0 or marker - after > 300:
        return None
    at = marker + FILL_FLAG_OFFSET
    if at >= len(p):
        return None
    return p[at] == FILL_FLAG_VALUE


def _read_contours(p, off, tag):
    # The stitch stream is followed by the object's NAME -- a length-prefixed
    # UTF-16 string, the same encoding read_wstr already reads for the thread
    # table -- and then four bytes, before the contour block. Treating that
    # prefix as a fixed 8 bytes only works while the name is empty, which is
    # why two letters of Lake Superior lost their outlines and came through as
    # a trace of their own stitches. Across 15476 objects in 104 designs the
    # signature sits at exactly 8 + 2*len(name) every time (14026 unnamed, 35
    # one-character names such as "l" or "k", 2 named "P2").
    if off + 4 > len(p):
        return [], None
    if struct.unpack_from('<I', p, off)[0] > 64:
        return [], None
    try:
        _name, q = read_wstr(p, off)
    except (struct.error, UnicodeDecodeError):
        return [], None
    q += 4
    if q + 19 > len(p):
        return [], None
    if struct.unpack_from('<I', p, q)[0] != 2:
        return [], None
    if struct.unpack_from('<I', p, q + 5)[0] != 1:
        return [], None
    if p[q + 10] != tag:
        return [], None
    ncontours = struct.unpack_from('<I', p, q + 11)[0]
    q += 15
    contours = []
    for _ in range(max(ncontours, 1)):
        if q + 4 > len(p):
            break
        nnodes = struct.unpack_from('<I', p, q)[0]
        q += 4
        if nnodes == 0:
            break
        nodes, ok = [], True
        for _ in range(nnodes):
            if q + 10 > len(p):
                ok = False
                break
            npt = 1 if p[q + 1] & 0x80 else 3
            if q + 2 + 8 * npt > len(p):
                ok = False
                break
            pts = [struct.unpack_from('<ff', p, q + 2 + 8 * k)
                   for k in range(npt)]
            if not all(abs(v) < 1e5 for pt in pts for v in pt):
                ok = False
                break
            nodes.append(pts)
            q += 2 + 8 * npt
        if not ok:
            break
        contours.append(nodes)
    return contours, q


def _flatten(nodes, steps=6):
    """Approximate a contour's cubic segments as a polyline, for containment
    tests only (the emitted path keeps the real Beziers)."""
    anchor = [n[0] for n in nodes]
    hout = [n[2] if len(n) == 3 else n[0] for n in nodes]
    hin = [n[1] if len(n) == 3 else n[0] for n in nodes]
    pts = []
    for i in range(len(nodes)):
        j = (i + 1) % len(nodes)
        p0, p1, p2, p3 = anchor[i], hout[i], hin[j], anchor[j]
        for s in range(steps):
            t = s / steps
            u = 1 - t
            pts.append((u**3 * p0[0] + 3 * u * u * t * p1[0]
                        + 3 * u * t * t * p2[0] + t**3 * p3[0],
                        u**3 * p0[1] + 3 * u * u * t * p1[1]
                        + 3 * u * t * t * p2[1] + t**3 * p3[1]))
    return pts


def _point_in(poly, pt):
    x, y = pt
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y):
            xx = x1 + (y - y1) / (y2 - y1) * (x2 - x1)
            if xx > x:
                inside = not inside
    return inside


def _zigzag_spacing_mm(rail):
    """Peak-to-peak zigzag spacing, measured off one rail.

    Consecutive points on the same rail are one full zigzag cycle apart, which
    is exactly what Ink/Stitch's zigzag_spacing_mm means
    (lib/elements/satin_column.py:263-272, default 0.4). Chroma designs vary --
    one logo stitches at 0.22 mm, and leaving it at the default under-stitched
    that design to 0.56x the original thread.
    """
    gaps = []
    for a, b in zip(rail, rail[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        if 0.3 < d < 60:
            gaps.append(d)
    if len(gaps) < 4:
        return None
    gaps.sort()
    # Measured, not clamped. Chroma's plain satins sit at 0.36-0.47 mm, but
    # chain-stitch designs genuinely measure far finer and forcing them back to
    # the 0.4 default halved their thread (0.62x -> 0.31x). Trust the file.
    return gaps[len(gaps) // 2] / 10.0


def _thread_mm(points):
    total = 0.0
    for a, b in zip(points, points[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        if d < 150:
            total += d
    return total / 10.0


def _area_mm2(nodes):
    poly = _flatten(nodes)
    a = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2 / 100.0


def _net_area_mm2(shapes, holes):
    """Area a fill actually covers: the outer shapes minus their counters.

    Counting a counter as filled area inflates the shape and makes both the
    fill test and the row spacing read a denser design than Chroma stitched.
    """
    return (sum(_area_mm2(c) for c in shapes)
            - sum(_area_mm2(c) for c in holes))


def _row_spacing_mm(area, stitches):
    """Row spacing implied by the thread Chroma actually spent on this shape.

    A fill of area A at spacing s consumes about A/s of thread, so s = A/thread.
    Chroma's fills range from dense (~0.14 mm) to sparse (~0.8 mm); leaving
    Ink/Stitch at its 0.25 mm default over-stitched the sparse ones several
    times over. Read by lib/elements/fill_stitch.py:364-379,
    get_float_param("row_spacing_mm", 0.25).
    """
    thread = _thread_mm(stitches)
    if area <= 0.5 or thread <= 0.5:
        return None
    # Only part of an object's thread is fill rows -- the rest is underlay and
    # travel, which Ink/Stitch regenerates separately. Attributing all of it to
    # rows computes too tight a spacing and over-stitches. FILL_THREAD_SHARE is
    # calibrated so the median design lands near 1.0x the original thread.
    derived = min(area / (thread * FILL_THREAD_SHARE), 1.50)
    derived = max(derived, 0.15)
    # Only ever WIDEN. Ink/Stitch's 0.25 mm default already matched Chroma's
    # dense fills (designs came out at 0.93x-1.03x the original thread);
    # narrowing below it over-stitched them to 1.25x-1.45x. The real defect was
    # sparse fills, where the default packed rows several times too tightly.
    return derived


def _stitched_as_fill(area, stitches):
    """Did Chroma actually FILL this shape, or only outline it?

    A shape's area implies how much thread a fill needs; if the object's own
    stitches carry far less than that, it was never filled and turning it into
    one is a large over-stitch.
    """
    if area <= 0.5:
        return True
    return _thread_mm(stitches) / (area / 0.25) >= FILL_DENSITY_MIN


def _thread_density(poly, area_mm2, stitches, outside=None):
    """Thread laid down inside a region, in mm per mm^2.

    Stitch POINTS cannot answer this: a counter is crossed by travel, and a few
    travel points inside it read the same as the tail of a sparse fill. Length
    can -- travel crosses a counter once, while a fill sweeps it end to end.
    """
    if area_mm2 <= 0:
        return 0.0
    step = max(1, len(stitches) // HOLE_SAMPLE_SEGMENTS)
    total = 0.0
    for a, b in zip(stitches[::step], stitches[step::step]):
        d = math.hypot(b[0] - a[0], b[1] - a[1]) / 10.0
        # A jump lays no thread; counting it would credit a counter with the
        # whole diagonal of whatever the machine crossed to get there.
        if d > JUMP_MM * step:
            continue
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if not _point_in(poly, mid):
            continue
        if outside is not None and _point_in(outside, mid):
            continue
        total += d
    return total * step / area_mm2


def _is_hole(cand, shapes, flats, stitches):
    """Is this contour a counter cut out of one of the shapes?

    Letters are the reason this exists: the bowl of an A, R, O or B is a
    contour nested in the letter's outline that Chroma never stitches, and
    filling it turns every counter solid. It cannot be told from a stitched
    detail by stitch COVERAGE -- neither holds many of the object's stitches --
    so the test is thread DENSITY inside it against the density of the rest of
    its parent. Nesting and _is_area come first because the details this used
    to protect (a flower's stamens, Chroma's direction fan) are thin open
    paths, and measured across 104 designs not one of them is nested inside a
    shape contour at all.
    """
    if not _is_area(cand):
        return False
    cf = flats[id(cand)]
    inside = [s for s in shapes
              if s is not cand and _point_in(flats[id(s)], cf[0])]
    if not inside:
        return False
    parent = min(inside, key=_area_mm2)   # the immediate parent, not the outermost
    ca, pa = _area_mm2(cand), _area_mm2(parent)
    if ca <= 0.05 or pa - ca <= 0.5:
        return False
    ring = _thread_density(flats[id(parent)], pa - ca, stitches, outside=cf)
    if ring <= 0:
        return False
    return _thread_density(cf, ca, stitches) / ring <= HOLE_THREAD_DENSITY_MAX


def _split_shapes(contours, stitches):
    """Split an object's contours into fillable shapes, counters, and details.

    Detail contours (a flower's stamens, Chroma's direction fan) sit INSIDE the
    outline, so they must never be handed to _group_contours: nesting logic
    would treat them as holes and evenodd would carve them out of the fill,
    which is what cut radial gaps through the flowers. They are real stitching
    though -- the original stitches them as lines -- so they come back as
    strokes rather than being dropped.

    Counters reach _group_contours instead: they are the same low-coverage
    contours (a hole holds none of the object's stitches either), separated
    from details by _is_hole.
    """
    shapes, rest = [], []
    for c in contours:
        if _contour_coverage(c, stitches) >= FILL_MIN_COVERAGE:
            shapes.append(c)
        else:
            rest.append(c)
    if not shapes and contours:
        best = max(contours, key=lambda c: _contour_coverage(c, stitches))
        shapes = [best]
        rest = [c for c in contours if c is not best]
    flats = {id(c): _flatten(c) for c in contours}
    holes, details = [], []
    for c in rest:
        (holes if _is_hole(c, shapes, flats, stitches) else details).append(c)
    return shapes, holes, details


def _group_contours(contours):
    """Group each outer contour with the contours nested inside it.

    Chroma packs several shapes into one object -- the petals of a flower, a
    leaf and its vein -- and they frequently overlap without being nested.
    Emitting them all in one evenodd path makes every overlap cancel into a
    hole, which is what left the flowers and leaves see-through. Only genuinely
    nested contours are holes; everything else gets its own filled path.
    """
    flat = [_flatten(c) for c in contours]
    depth = []
    for i, fi in enumerate(flat):
        probe = fi[0]
        depth.append(sum(1 for j, fj in enumerate(flat)
                         if j != i and _point_in(fj, probe)))
    groups = []
    for i, c in enumerate(contours):
        if depth[i] % 2:
            continue  # odd depth => this is a hole, attached below
        holes = [contours[j] for j, fj in enumerate(flat)
                 if j != i and depth[j] == depth[i] + 1
                 and _point_in(flat[i], flat[j][0])]
        groups.append([c] + holes)
    return groups or [[c] for c in contours]


def _reverse_contour(nodes):
    """Reverse node order, swapping each node's in/out handles."""
    out = []
    for n in reversed(nodes):
        out.append([n[0], n[2], n[1]] if len(n) == 3 else list(n))
    return out


def _join_rails(contours):
    """Join a pair of boundary rails into one closed contour.

    Chroma builds a shape from two boundary curves rather than one closed
    outline -- the same construction that makes its satins incompatible with
    Ink/Stitch's rails. Each curve is an OPEN arc, so closing them individually
    with Z draws a long chord across the shape; that chord is what appeared as
    slabs and wedges through the flowers. Measured over 130 two-contour objects
    in one design, joining the pair closed the shape better than self-closing
    for 119 of them (median gap 18 vs 49, units of 0.1 mm).
    """
    if len(contours) != 2:
        return contours
    a, b = contours
    if not a or not b:
        return contours

    def gap(p, q):
        return math.hypot(p[0] - q[0], p[1] - q[1])

    self_close = gap(a[0][0], a[-1][0])
    best, joined = None, None
    for cand in (b, _reverse_contour(b)):
        score = gap(a[-1][0], cand[0][0]) + gap(cand[-1][0], a[0][0])
        if best is None or score < best:
            best, joined = score, cand
    if best >= self_close:
        return contours
    return [list(a) + list(joined)]


def _contour_coverage(nodes, stitches):
    """Fraction of the object's stitches lying inside this one contour.

    This replaces a shape heuristic (compactness) that could not tell a thin
    open sliver from a legitimately narrow shape, and so silently dropped the
    fill from 42 of 131 objects in one design -- including stored fills with
    over a hundred stitches. Containment of the object's own stitches is the
    ground truth for whether a contour is the shape being stitched.
    """
    if not stitches:
        return 0.0
    poly = _flatten(nodes)
    step = max(1, len(stitches) // 150)
    sample = stitches[::step]
    return sum(1 for s in sample if _point_in(poly, s)) / len(sample)


def _stitch_coverage(contours, stitches):
    """Fraction of the object's own stitches that land inside its shape.

    The stitches are ground truth for where the shape is, so this scores a
    candidate interpretation of the contours without guessing.
    """
    polys = [_flatten(c) for c in contours]
    if not polys or not stitches:
        return 0.0
    step = max(1, len(stitches) // 200)
    sample = stitches[::step]
    hits = sum(1 for s in sample if any(_point_in(p, s) for p in polys))
    return hits / len(sample)


def _choose_contours(contours, stitches):
    """Pick the contour interpretation whose shape actually holds the stitches.

    Chroma sometimes stores a shape as one closed outline and sometimes as two
    open rails that must be joined. Rather than guess from endpoint gaps, score
    both readings against the stitches and take the better one.
    """
    if len(contours) != 2:
        return contours
    joined = _join_rails(contours)
    if len(joined) == len(contours):
        # _join_rails declined; try it anyway and let coverage decide.
        a, b = contours
        joined = [list(a) + list(_reverse_contour(b))]
    if _stitch_coverage(joined, stitches) > _stitch_coverage(contours, stitches):
        return joined
    return contours


def _is_area(nodes):
    """True if a contour encloses a real area rather than being a thin sliver.

    Chroma stores fine details (stamens, thin strokes) as open paths. Closing
    one with Z and filling it produces a self-intersecting spike, which is what
    put shards through the middle of the flowers. Isoperimetric compactness
    (1.0 = circle, ~0 = line) separates them: 72 of 78 such contours in Teacher
    Tote scored below 0.15, while real petals score far higher.
    """
    poly = _flatten(nodes)
    area = 0.0
    per = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        area += x1 * y2 - x2 * y1
        per += math.hypot(x2 - x1, y2 - y1)
    if per <= 0:
        return False
    return 4 * math.pi * (abs(area) / 2) / (per * per) >= SLIVER_COMPACTNESS


def _span(points):
    return sum(math.hypot(q[0] - p[0], q[1] - p[1])
               for p, q in zip(points, points[1:]))


def _simplify(points, tol):
    if len(points) < 3:
        return list(points)
    a, b = points[0], points[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    span = math.hypot(dx, dy)
    worst, idx = -1.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        if span < 1e-9:
            dist = math.hypot(px - a[0], py - a[1])
        else:
            dist = abs(dy * px - dx * py + b[0] * a[1] - b[1] * a[0]) / span
        if dist > worst:
            worst, idx = dist, i
    if worst <= tol:
        return [a, b]
    return _simplify(points[:idx + 1], tol)[:-1] + _simplify(points[idx:], tol)


def _concentration(points):
    sx = sy = 0.0
    n = 0
    for a, b in zip(points, points[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        if not (0.3 < d < 150):
            continue
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        sx += math.cos(2 * ang)
        sy += math.sin(2 * ang)
        n += 1
    return math.hypot(sx, sy) / n if n >= 8 else 0.0


def _dominant_angle(points):
    sx = sy = 0.0
    n = 0
    for a, b in zip(points, points[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        if not (0.3 < d < 150):
            continue
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        sx += math.cos(2 * ang)
        sy += math.sin(2 * ang)
        n += 1
    if n < 8:
        return 0.0
    return math.degrees((math.atan2(sy, sx) / 2) % math.pi)


def _esc(text):
    return (str(text).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def convert(path):
    colors, objects = read_design(path)
    allpts = [s for o in objects for s in o['stitches']]
    minx = min(p[0] for p in allpts)
    miny = min(p[1] for p in allpts)
    w = (max(p[0] for p in allpts) - minx) * PX_PER_UNIT
    h = (max(p[1] for p in allpts) - miny) * PX_PER_UNIT

    def X(v):
        return (v[0] - minx) * PX_PER_UNIT

    def Y(v):
        return (v[1] - miny) * PX_PER_UNIT

    counts = {'satin': 0, 'fill': 0, 'run': 0, 'trim': 0}
    # One layer per thread, each holding its objects in stitch order.
    layers = {}
    order = []
    uid = [0]

    def add(color_index, markup):
        if color_index not in layers:
            layers[color_index] = []
            order.append(color_index)
        layers[color_index].append(markup)

    def emit_shapes(obj, stitches, color):
        """Emit one object's contours: filled areas (counters cut out) and,
        separately, the thin details Chroma stitches as lines."""
        shapes, holes, details = _split_shapes(
            _choose_contours(obj['contours'], stitches), stitches)
        area = _net_area_mm2(shapes, holes)
        if not _stitched_as_fill(area, stitches):
            # Never filled, only outlined -- every contour is a line.
            shapes, holes, details = [], [], shapes + holes + details
        rs = _row_spacing_mm(area, stitches)
        rsa = f' inkstitch:row_spacing_mm="{rs:.2f}"' if rs else ''
        # Counters ride along as subpaths of their parent so fill-rule evenodd
        # cuts them out; _group_contours pairs each one with the shape it sits
        # in by nesting depth.
        for group in _group_contours(shapes + holes):
            subpaths = [d for d in (_contour_path(n, X, Y)
                                    for n in group) if d]
            if not subpaths:
                continue
            uid[0] += 1
            add(obj['color'],
                f'<path id="rde{uid[0]}" d="{" ".join(subpaths)}" '
                f'style="fill:{color};fill-rule:evenodd;stroke:none" '
                f'inkstitch:angle="{_dominant_angle(stitches):.1f}" '
                f'inkstitch:expand_mm="{FILL_EXPAND_MM}"{rsa}/>')
            counts['fill'] += 1
        for c in details:
            d = _contour_path(c, X, Y)
            if not d:
                continue
            uid[0] += 1
            add(obj['color'],
                f'<path id="rde{uid[0]}" d="{d}" '
                f'style="fill:none;stroke:{color};stroke-width:1"/>')
            counts['run'] += 1

    def emit_object(obj, stitches, color):

        # Prefer Chroma's own flag; fall back to measuring the stitches only
        # where the parameter block could not be located (~32% of objects).
        is_fill = obj['is_fill']
        if is_fill is None:
            is_fill = _concentration(stitches) >= FILL_CONCENTRATION

        if is_fill and obj['contours']:
            # A fill object carries its outline AND, usually, a second contour
            # that is a spiky fan: Chroma's stitch-direction guide, not a
            # shape. Filling that guide is what put shards through the middle
            # of the flowers. For a fill object the stitches ARE the fill, so
            # a contour that does not hold the object's stitches is metadata
            # and gets dropped rather than stitched.
            emit_shapes(obj, stitches, color)
            return

        runs = list(satin_runs(stitches))
        covered = sum(b - a for a, b, _, _ in runs)
        if runs and covered >= SATIN_COVERAGE * len(stitches):
            emitted = False
            for _, _, rail_a, rail_b in runs:
                # Rails must keep EQUAL point counts: with two subpaths and no
                # rungs the engine pairs their nodes sequentially, so
                # simplifying each rail independently breaks the column.
                n = min(len(rail_a), len(rail_b))
                if n < 2:
                    continue
                ra = [(X(p), Y(p)) for p in rail_a[:n]]
                rb = [(X(p), Y(p)) for p in rail_b[:n]]
                widths = sorted(math.hypot(a[0] - b[0], a[1] - b[1])
                                for a, b in zip(ra, rb))
                if widths[len(widths) // 2] < MIN_SATIN_WIDTH_PX:
                    continue
                # BOTH rails need real length. A rail of coincident points
                # stitches to nothing and aborts the whole export
                # (satin_column.py:922-933, NotStitchableError).
                if min(_span(ra), _span(rb)) < MIN_SATIN_LENGTH_PX:
                    continue
                uid[0] += 1
                d = ('M ' + ' L '.join(f'{x:.3f},{y:.3f}' for x, y in ra)
                     + ' M ' + ' L '.join(f'{x:.3f},{y:.3f}' for x, y in rb))
                spacing = _zigzag_spacing_mm(rail_a[:n])
                zz = (f' inkstitch:zigzag_spacing_mm="{spacing:.2f}"'
                      if spacing else '')
                add(obj['color'],
                    f'<path id="rde{uid[0]}" d="{d}" '
                    f'style="fill:none;stroke:{color};stroke-width:1" '
                    f'inkstitch:satin_column="true"{zz}/>')
                counts['satin'] += 1
                emitted = True
            if emitted:
                return

        # A Chroma object that owns a closed contour is a solid area -- either a
        # fill or a satin. It is never a bare outline. So once satin detection
        # has declined it, fill it; emitting a stroke here is what left whole
        # flowers as hollow outlines. Bare running stitch is only correct for
        # objects that carry no contour at all.
        if obj['contours']:
            emit_shapes(obj, stitches, color)
            return

        pts = _simplify([(X(p), Y(p)) for p in stitches], SIMPLIFY_PX)
        if len(pts) >= 2:
            uid[0] += 1
            d = 'M ' + ' L '.join(f'{x:.3f},{y:.3f}' for x, y in pts)
            add(obj['color'],
                f'<path id="rde{uid[0]}" d="{d}" '
                f'style="fill:none;stroke:{color};stroke-width:1"/>')
            counts['run'] += 1


    def trim_last(color_index):
        """Trim the thread after the last element an object emitted.

        Ink/Stitch reads inkstitch:trim_after per element
        (lib/elements/element.py:455-462, get_boolean_param("trim_after",
        False)); element.py:772 carries it into the stitch group and
        lib/stitch_plan/stitch_plan.py:97-98 turns it into a real trim. Without
        it the engine only ties off and jumps -- collapse_len never inserts a
        trim -- so the move shows as a float on the finished piece. It goes on
        the LAST element of the object, not every element: an object that
        becomes a run of satins is one continuous piece of stitching.
        """
        markup = layers[color_index][-1]
        layers[color_index][-1] = markup.replace(
            '/>', ' inkstitch:trim_after="true"/>', 1)

    # Trims are decided by the move to the NEXT object, so each object is held
    # until the following one is known. The final object needs no trim -- the
    # design ends there.
    pending = None
    for obj in objects:
        stitches = obj['stitches']
        if len(stitches) < 4:
            continue
        color = colors[obj['color']]['rgb'] if colors else '#000000'
        if pending is not None and pending[0] == obj['color']:
            # Across a color change the machine trims anyway, so no command is
            # needed -- the engine's own jump_to_trim skips those moves too
            # (lib/extensions/jump_to_trim.py, the color != color continue).
            gap = math.hypot(stitches[0][0] - pending[1][0],
                             stitches[0][1] - pending[1][1]) / 10.0
            if gap > TRIM_MIN_JUMP_MM:
                trim_last(pending[0])
                counts['trim'] += 1
        before = len(layers.get(obj['color'], ()))
        emit_object(obj, stitches, color)
        if len(layers.get(obj['color'], ())) > before:
            pending = (obj['color'], stitches[-1])

    out = [f'<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'xmlns:inkstitch="{INKSTITCH_NS}" '
           f'xmlns:inkscape="{INKSCAPE_NS}" '
           f'version="1.1" width="{w:.3f}" height="{h:.3f}" '
           f'viewBox="0 0 {w:.3f} {h:.3f}">',
           '  <metadata>',
           f'    <inkstitch:inkstitch_svg_version>{INKSTITCH_SVG_VERSION}'
           f'</inkstitch:inkstitch_svg_version>',
           '  </metadata>']
    for ci in order:
        thread = colors[ci] if ci < len(colors) else None
        label = (f"{thread['code']} {thread['name']}" if thread
                 else f'color {ci}')
        out.append(f'  <g id="color{ci}" inkscape:groupmode="layer" '
                   f'inkscape:label="{_esc(label)}">')
        out.extend('    ' + m for m in layers[ci])
        out.append('  </g>')
    out.append('</svg>')
    return '\n'.join(out), counts


def _contour_path(nodes, X, Y):
    if len(nodes) < 2:
        return None
    anchor = [n[0] for n in nodes]
    hout = [n[2] if len(n) == 3 else n[0] for n in nodes]
    hin = [n[1] if len(n) == 3 else n[0] for n in nodes]
    d = [f'M {X(anchor[0]):.3f},{Y(anchor[0]):.3f}']
    for i in range(len(nodes)):
        j = (i + 1) % len(nodes)
        d.append(f'C {X(hout[i]):.3f},{Y(hout[i]):.3f} '
                 f'{X(hin[j]):.3f},{Y(hin[j]):.3f} '
                 f'{X(anchor[j]):.3f},{Y(anchor[j]):.3f}')
    return ' '.join(d) + ' Z'


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    svg, counts = convert(src)
    open(dst, 'w').write(svg)
    print(f'{src} -> {dst}')
    print(f'  satin columns: {counts["satin"]}  fills: {counts["fill"]}  '
          f'runs: {counts["run"]}  trims: {counts["trim"]}')
