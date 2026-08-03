"""Line-art digitization: raster → verified stroke geometry.

Pure logic for `tools digitize-lineart`.  The pipeline this encodes was
developed on a real design (the rose-bag session, 2026-08) where every
skipped verification step produced a silent failure, so each stage carries
its check:

1. threshold + crop the raster to its ink
2. vector-trace (vtracer) → filled outlines
3. build a px-user-unit SVG document (see svg/units.py for why px)
4. [command] engine fill_to_stroke per fill → centerlines
5. score every stroke's *ink fraction* against the source raster — a
   centerline that mostly runs over blank paper is a tracing spur (the
   medial-axis artifacts at thick-stroke junctions); render an overlay
   PNG so the deletion is visually verifiable before it happens
6. *reverse* coverage: find ink marks no stroke covers (thin accents the
   centerline pass drops) and extract their centerlines to re-add

The two directions of step 5/6 are different checks: stroke→ink catches
geometry that shouldn't exist, ink→stroke catches drawing that got lost.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
INKSTITCH_NS = "http://inkstitch.org/namespace"
PX_PER_MM = 96 / 25.4


# ---- raster preparation -----------------------------------------------------

def threshold_image(image_path: str | Path, threshold: int = 128,
                    margin: int = 8):
    """Load → grayscale → binarize → crop to ink (+margin). Returns PIL image.

    Cropping matters: it makes the raster's pixel grid the canonical
    coordinate system every later verification maps back into.
    """
    from PIL import Image
    img = Image.open(str(image_path)).convert("L")
    img = img.point(lambda v: 0 if v < threshold else 255)
    bbox = ink_bbox(img)
    if bbox is None:
        raise ValueError(f"no dark pixels found in {image_path} "
                         f"(threshold {threshold})")
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0 - margin); y0 = max(0, y0 - margin)
    x1 = min(img.size[0], x1 + margin + 1); y1 = min(img.size[1], y1 + margin + 1)
    return img.crop((x0, y0, x1, y1))


def ink_bbox(img, stride: int = 1):
    """Bbox of dark pixels (inclusive min, inclusive max), or None.

    stride > 1 trades exactness for speed — fine for probes, but the crop
    in threshold_image needs stride 1 (an off-by-one here shifts every
    later raster↔geometry mapping).
    """
    w, h = img.size
    px = img.load()
    xs = [x for x in range(w) if any(px[x, y] < 128 for y in range(0, h, stride))]
    ys = [y for y in range(h) if any(px[x, y] < 128 for x in range(0, w, stride))]
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def trace_to_svg(bw_png_path: str | Path, out_svg_path: str | Path) -> None:
    """Vector-trace a binarized PNG into filled outlines (vtracer)."""
    try:
        import vtracer
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "vtracer is required for tracing: pip install "
            "'cli-anything-inkstitch[trace]'") from exc
    vtracer.convert_image_to_svg_py(
        str(bw_png_path), str(out_svg_path),
        colormode="binary", mode="spline")


# ---- document construction --------------------------------------------------

def build_px_document(traced_svg_path: str | Path, source_size_px,
                      width_mm: float | None, height_mm: float | None):
    """Traced SVG → px-user-unit document at the requested physical size.

    Exactly one of width_mm/height_mm may be None (derived from the source
    aspect ratio).  Returns (etree tree, kd) where kd converts source px →
    document px — the constant every later raster check uses to map
    document geometry back onto the source image.

    px user units (viewBox == px conversion of the physical size) keep the
    engine's correction transform at identity — svg/units.py documents the
    measured behavior behind that choice.
    """
    from cli_anything_inkstitch.svg.geometry import transform_d
    src_w, src_h = source_size_px
    if width_mm is None and height_mm is None:
        raise ValueError("one of width_mm/height_mm is required")
    if width_mm is None:
        width_mm = height_mm * src_w / src_h
    if height_mm is None:
        height_mm = width_mm * src_h / src_w
    doc_w, doc_h = width_mm * PX_PER_MM, height_mm * PX_PER_MM
    kd = doc_h / src_h

    src = etree.parse(str(traced_svg_path))
    out = etree.Element(f"{{{SVG_NS}}}svg", nsmap={
        None: SVG_NS, "inkstitch": INKSTITCH_NS,
        "xlink": "http://www.w3.org/1999/xlink"})
    out.set("width", f"{width_mm:g}mm")
    out.set("height", f"{height_mm:g}mm")
    out.set("viewBox", f"0 0 {doc_w:.3f} {doc_h:.3f}")
    meta = etree.SubElement(out, f"{{{SVG_NS}}}metadata")
    ver = etree.SubElement(meta, "inkstitch_svg_version")
    ver.text = "3"
    n = 0
    for p in src.getroot().iter(f"{{{SVG_NS}}}path"):
        n += 1
        el = etree.SubElement(out, f"{{{SVG_NS}}}path")
        el.set("id", f"elem_{n}")
        el.set("d", transform_d(p.get("d"), (kd, 0, 0, kd, 0, 0)))
        el.set("fill", "#1a1a1a")
    if n == 0:
        raise ValueError(f"trace produced no paths: {traced_svg_path}")
    return etree.ElementTree(out), kd


# ---- verification: stroke → ink ---------------------------------------------

def ink_fraction(pts_doc, img, kd: float, radius: int = 3,
                 sample_step: float = 4.0) -> float:
    """Fraction of a polyline's samples that sit on (or near) source ink.

    pts_doc is in document px; kd maps document px → source px.  A genuine
    centerline scores ~1.0; a junction spur crossing blank paper scores low.
    The radius forgives centerline-vs-outline offsets of a few pixels — the
    mapping itself must NOT be assumed but derives from kd and the crop
    (guessing an offset once falsely condemned 37 real lines).
    """
    from cli_anything_inkstitch.artifact.gate import poly_length, sample_poly
    w, h = img.size
    px = img.load()
    samples = sample_poly(pts_doc, max(8, int(poly_length(pts_doc) / sample_step)))
    if not samples:
        return 0.0
    hits = 0
    for x_doc, y_doc in samples:
        cx, cy = int(x_doc / kd), int(y_doc / kd)
        if any(0 <= cx + a < w and 0 <= cy + b < h and px[cx + a, cy + b] < 128
               for a in range(-radius, radius + 1)
               for b in range(-radius, radius + 1)):
            hits += 1
    return hits / len(samples)


# ---- verification: ink → stroke ---------------------------------------------

def stroke_cover_mask(size, stroke_polys_src, width: int = 9):
    """Rasterize strokes (source-px polylines) into a coverage mask."""
    from PIL import Image, ImageDraw
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for pts in stroke_polys_src:
        if len(pts) >= 2:
            draw.line(pts, fill=255, width=width)
    return mask


def uncovered_ink_components(img, mask, min_size: int = 40,
                             max_cover: float = 0.4):
    """Connected ink components mostly missed by the stroke mask.

    These are the thin accent marks the centerline pass drops (measured:
    fill_to_stroke skipped 6 accent ticks on the rose design).  Components
    touching covered ink (junction slivers) exceed max_cover and are not
    returned.
    """
    w, h = img.size
    ipx, mpx = img.load(), mask.load()
    ink = {(x, y) for y in range(h) for x in range(w) if ipx[x, y] < 128}
    seen: set = set()
    missed = []
    for start in ink:
        if start in seen:
            continue
        comp = []
        q = deque([start])
        seen.add(start)
        while q:
            x, y = q.popleft()
            comp.append((x, y))
            for a in (-1, 0, 1):
                for b in (-1, 0, 1):
                    n = (x + a, y + b)
                    if n in ink and n not in seen:
                        seen.add(n)
                        q.append(n)
        if len(comp) < min_size:
            continue
        covered = sum(1 for x, y in comp if mpx[x, y] > 0)
        if covered / len(comp) < max_cover:
            missed.append(comp)
    return missed


def component_centerline(comp, simplify_step: int = 6):
    """Polyline through a thin pixel component: BFS farthest-pair walk.

    Two BFS passes find the component's diameter endpoints; walking back
    down the distance field yields a single path through the mark.  Only
    meaningful for thin, elongated components (accent ticks) — a blob
    returns an arbitrary chord.
    """
    pix = set(comp)

    def bfs(src):
        dist = {src: 0}
        q = deque([src])
        far = src
        while q:
            x, y = q.popleft()
            for a in (-1, 0, 1):
                for b in (-1, 0, 1):
                    n = (x + a, y + b)
                    if n in pix and n not in dist:
                        dist[n] = dist[(x, y)] + 1
                        if dist[n] > dist[far]:
                            far = n
                        q.append(n)
        return far, dist

    e1, _ = bfs(next(iter(pix)))
    e2, dist = bfs(e1)
    path = [e2]
    cur = e2
    while dist[cur] > 0:
        cur = min(
            ((cur[0] + a, cur[1] + b) for a in (-1, 0, 1) for b in (-1, 0, 1)
             if (cur[0] + a, cur[1] + b) in dist),
            key=lambda n: dist[n])
        path.append(cur)
    simplified = path[::simplify_step]
    if simplified[-1] != path[-1]:
        simplified.append(path[-1])
    return simplified


# ---- visual verification artifact -------------------------------------------

def overlay_png(img, kept_src=(), suspects_src=(), added_src=(),
                out_path: str | Path | None = None):
    """Render strokes over the source: green kept, red suspect, blue added.

    This is the look-before-you-delete artifact — the deletion decision must
    be verifiable against the drawing, not just a score.
    """
    from PIL import ImageDraw
    vis = img.convert("RGB")
    draw = ImageDraw.Draw(vis)
    for pts in kept_src:
        if len(pts) >= 2:
            draw.line(pts, fill=(0, 200, 0), width=1)
    for pts in suspects_src:
        if len(pts) >= 2:
            draw.line(pts, fill=(255, 0, 0), width=3)
    for pts in added_src:
        if len(pts) >= 2:
            draw.line(pts, fill=(0, 90, 255), width=2)
    if out_path:
        vis.save(str(out_path))
    return vis
