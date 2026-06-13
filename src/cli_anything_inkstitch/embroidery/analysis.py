"""Stitch-pattern geometry analysis — baselines, exit connectors, SVG path conversion."""

from __future__ import annotations

import secrets

from lxml import etree

from cli_anything_inkstitch.embroidery.files import _DST_TO_SVG
from cli_anything_inkstitch.svg.attrs import INKSTITCH_NS, SVG_NS


def _rotate_coords(
    coords: list[tuple[float, float]], degrees: int
) -> list[tuple[float, float]]:
    """Rotate (x,y) pairs clockwise by 0/90/180/270 degrees (in SVG Y-down space)."""
    if degrees == 0:
        return coords
    if degrees == 90:
        return [(y, -x) for x, y in coords]
    if degrees == 180:
        return [(-x, -y) for x, y in coords]
    if degrees == 270:
        return [(-y, x) for x, y in coords]
    return coords


def _emb_to_svg_paths(
    pattern,
    stroke_color: str = "#231f20",
    stitch_length_mm: float = 1.5,
) -> list[etree._Element]:
    """Convert a pyembroidery pattern to a list of lxml <path> elements.

    Each contiguous run of STITCH commands between TRIM/COLOR_CHANGE/END
    becomes one <path> element with running-stitch inkstitch attributes.
    pyembroidery normalises all formats to SVG Y-down, so no Y-flip is applied.
    """
    import pyembroidery

    STITCH = pyembroidery.STITCH
    TRIM = pyembroidery.TRIM
    END = pyembroidery.END
    COLOR_CHANGE = pyembroidery.COLOR_CHANGE

    elements = []
    current: list[tuple[float, float]] = []
    color_idx = 0
    threads = pattern.threadlist if pattern.threadlist else []

    def _flush(pts: list, cidx: int) -> None:
        if len(pts) < 2:
            return
        coords = [(x * _DST_TO_SVG, y * _DST_TO_SVG) for x, y in pts]
        d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in coords)

        # Resolve thread color
        color = stroke_color
        if threads and cidx < len(threads):
            t = threads[cidx]
            if hasattr(t, 'color') and t.color is not None:
                color = "#{:06x}".format(t.color & 0xFFFFFF)

        el = etree.Element(f"{{{SVG_NS}}}path")
        el.set("d", d)
        el.set("style", f"fill:none;stroke:{color};stroke-width:1")
        el.set(f"{{{INKSTITCH_NS}}}color_sort_index", str(cidx))
        el.set(f"{{{INKSTITCH_NS}}}running_stitch_length_mm", str(stitch_length_mm))
        el.set(f"{{{INKSTITCH_NS}}}min_stitch_length_mm", "0.5")
        el.set(f"{{{INKSTITCH_NS}}}min_jump_stitch_length_mm", "3.0")
        el.set("id", f"path_{secrets.token_hex(4)}")
        elements.append(el)

    for x, y, cmd in pattern.stitches:
        if cmd == STITCH:
            current.append((x, y))
        elif cmd in (TRIM, COLOR_CHANGE, END):
            _flush(current, color_idx)
            current = []
            if cmd == COLOR_CHANGE:
                color_idx += 1

    _flush(current, color_idx)
    return elements


# ---------------------------------------------------------------------------
# Swash / descender detection for automatic baseline placement
# ---------------------------------------------------------------------------

def _visual_baseline_y(pattern, expected_height_svg: float) -> float:
    """Return the visual baseline Y (SVG px) for a single glyph.

    Strategy: any height beyond expected_height_svg is assumed to be a
    decorative swash (R, J) or intentional descender (g, j, y) that extends
    below the natural sitting-position of the letter.  We subtract that
    excess from the bounding-box bottom so the letter body lands on the
    baseline while swashes/descenders hang below it.

    For letters whose height ≈ expected_height (H, T, A, …) the excess is
    near zero and the result equals the raw bounding-box bottom.
    """
    import pyembroidery
    ys = [sy * _DST_TO_SVG for _, sy, cmd in pattern.stitches
          if cmd == pyembroidery.STITCH]
    if not ys:
        return 0.0
    bbox_bottom = max(ys)
    bbox_height = bbox_bottom - min(ys)
    excess = max(0.0, bbox_height - expected_height_svg)
    return bbox_bottom - excess


# ---------------------------------------------------------------------------
# Exit-connector detection for script-font kerning
# ---------------------------------------------------------------------------

def _detect_exit_advance(pattern, step_threshold_pct: float = 0.30, bins: int = 20) -> float:
    """Return the visual right edge (SVG px) where the letter body ends.

    Script fonts have a thin exit connector that reaches into the next letter.
    Using the full bbox right would leave gaps between letters.  We detect the
    connector by scanning Y-span per X-bin in the rightmost 30% of the letter:
    the first bin whose Y-span drops ≥ step_threshold_pct × max_span signals
    the start of the connector, and we return the right edge of the bin before
    it (the body right).

    Returns full bbox right when no connector is detected (round letters, etc.).
    """
    import pyembroidery

    pts = [(x * _DST_TO_SVG, y * _DST_TO_SVG)
           for x, y, cmd in pattern.stitches
           if cmd == pyembroidery.STITCH]
    if not pts:
        return 0.0

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    lo_x, hi_x = min(xs), max(xs)
    span = hi_x - lo_x
    if span < 1.0:
        return hi_x

    bin_w = span / bins
    y_min_b = [float("inf")] * bins
    y_max_b = [float("-inf")] * bins
    for x, y in pts:
        b = min(int((x - lo_x) / bin_w), bins - 1)
        if y < y_min_b[b]:
            y_min_b[b] = y
        if y > y_max_b[b]:
            y_max_b[b] = y

    yspans = [
        (y_max_b[b] - y_min_b[b]) if y_max_b[b] > y_min_b[b] else 0.0
        for b in range(bins)
    ]
    max_yspan = max(yspans) if yspans else 0.0
    if max_yspan < 1.0:
        return hi_x

    search_start = int(bins * 0.70)
    vis_right_bin = bins - 1  # default: full bbox right
    for i in range(search_start, bins - 1):
        if yspans[i] < 1.0:
            # Empty bin = end of body
            vis_right_bin = max(search_start, i - 1)
            break
        drop = yspans[i] - yspans[i + 1]
        if max_yspan > 0 and drop / max_yspan >= step_threshold_pct:
            vis_right_bin = i
            break

    return lo_x + (vis_right_bin + 1) * bin_w


def _exit_advance_at_connection_y(
        pattern, connection_raw_y: float, tolerance: float = 5.0) -> float:
    """Return the rightmost stitch X (SVG px) among stitches near connection_raw_y.

    For a connecting script font whose letters are stored in BF coordinates
    centred at (0, 0), the exit connector always passes through the connection
    line (connection_raw_y in the raw stitch space).  The rightmost X among
    those stitches is where the connector ends — and therefore the X position
    at which the next letter's entry connector must begin.

    Returns full-bbox right edge when no stitches are found in the band
    (should not happen for well-formed BX fonts, but guards against edge cases).
    """
    import pyembroidery

    pts = [(x * _DST_TO_SVG, y * _DST_TO_SVG)
           for x, y, cmd in pattern.stitches
           if cmd == pyembroidery.STITCH]
    if not pts:
        return 0.0

    near = [x for x, y in pts if abs(y - connection_raw_y) <= tolerance]
    if near:
        return max(near)
    return max(x for x, y in pts)   # fallback: full bbox right


def _last_stitch_svg_y(pattern) -> float | None:
    """Return the SVG-space Y coordinate of the last STITCH command in *pattern*.

    Used by the ``last-stitch`` baseline method.  Returns ``None`` when the
    pattern contains no STITCH commands (e.g. empty file or commands only).
    """
    import pyembroidery as _pe
    for _x, y, cmd in reversed(pattern.stitches):
        if cmd == _pe.STITCH:
            return float(y) * _DST_TO_SVG
    return None
