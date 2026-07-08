"""Stitchability gate — static geometry audit before handback (spec §8).

Error severity blocks handback; warning severity surfaces without blocking.
v1 checks:

  errors   — satin rail count != 2; rung not paired to both rails (the risk
             accepted by choosing independent rails); rail node-count mismatch
             with no rungs (interpolation twist); self-crossing rail; satin
             width outside stitchable range
  warnings — narrow/wide-but-stitchable satin, very long unbroken satin,
             fill start/end handle far from its boundary (engine resolves to
             the nearest boundary point, so a far handle almost certainly
             isn't where the digitizer thinks it is)

Pure geometry, no binary invocation — cheap enough to run on every handback
and after every edit batch.
"""

from __future__ import annotations

import math
import re
from typing import Any

# stitchable satin width, mm (zigzag physics: too narrow = perforation,
# too wide = loose loops that snag)
SATIN_MIN_WIDTH_MM = 0.5
SATIN_MAX_WIDTH_MM = 12.0
SATIN_NARROW_WARN_MM = 1.0
SATIN_WIDE_WARN_MM = 8.0
SATIN_LONG_WARN_MM = 150.0
RUNG_PAIR_TOL_MM = 0.8
FILL_HANDLE_FAR_MM = 10.0
_WIDTH_SAMPLES = 24

_PX_TO_MM = 25.4 / 96.0  # SVG default: 1 user unit = 1 CSS px


# -- geometry primitives -------------------------------------------------------

# An SVG number has at most one decimal point — Illustrator compacts
# "-3.3 0.5" to "-3.3.5", so a greedy [\d.]+ would glue them into one token.
_TOKEN = re.compile(r"[MmLlCcZzHhVvSsQqTtAa]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def flatten_path(d: str, per_curve: int = 12) -> list[tuple[float, float]]:
    """Flatten one subpath's d into a polyline (M/L/C/H/V/Z; S/Q/T/A endpoints)."""
    tokens = _TOKEN.findall(d or "")
    pts: list[tuple[float, float]] = []
    i = 0
    cx = cy = sx = sy = 0.0
    cmd = None

    def num() -> float:
        nonlocal i
        v = float(tokens[i]); i += 1
        return v

    while i < len(tokens):
        if re.match(r"[A-Za-z]", tokens[i]):
            cmd = tokens[i]; i += 1
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x = num() + (cx if rel else 0); y = num() + (cy if rel else 0)
            cx, cy, sx, sy = x, y, x, y
            pts.append((x, y))
            cmd = "l" if rel else "L"
        elif c == "L":
            x = num() + (cx if rel else 0); y = num() + (cy if rel else 0)
            cx, cy = x, y
            pts.append((x, y))
        elif c == "H":
            cx = num() + (cx if rel else 0)
            pts.append((cx, cy))
        elif c == "V":
            cy = num() + (cy if rel else 0)
            pts.append((cx, cy))
        elif c == "C":
            x1 = num() + (cx if rel else 0); y1 = num() + (cy if rel else 0)
            x2 = num() + (cx if rel else 0); y2 = num() + (cy if rel else 0)
            x = num() + (cx if rel else 0); y = num() + (cy if rel else 0)
            for k in range(1, per_curve + 1):
                t = k / per_curve; u = 1 - t
                pts.append((
                    u*u*u*cx + 3*u*u*t*x1 + 3*u*t*t*x2 + t*t*t*x,
                    u*u*u*cy + 3*u*u*t*y1 + 3*u*t*t*y2 + t*t*t*y,
                ))
            cx, cy = x, y
        elif c in ("S", "Q", "T", "A"):
            n = {"S": 4, "Q": 4, "T": 2, "A": 7}[c]
            vals = [num() for _ in range(n)]
            x = vals[-2] + (cx if rel else 0); y = vals[-1] + (cy if rel else 0)
            cx, cy = x, y
            pts.append((x, y))
        elif c == "Z":
            cx, cy = sx, sy
            pts.append((sx, sy))
        else:
            i += 1
    return pts


def poly_length(pts: list[tuple[float, float]]) -> float:
    return sum(math.dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))


def sample_poly(pts: list[tuple[float, float]], n: int) -> list[tuple[float, float]]:
    """n points at equal arc-length spacing along the polyline."""
    total = poly_length(pts)
    if total <= 0 or len(pts) < 2 or n < 2:
        return list(pts)
    out = [pts[0]]
    want = total / (n - 1)
    acc, nxt = 0.0, want
    for i in range(1, len(pts)):
        (px_, py_), (qx, qy) = pts[i - 1], pts[i]
        seg = math.dist((px_, py_), (qx, qy))
        while seg > 0 and acc + seg >= nxt - 1e-9 and len(out) < n - 1:
            t = (nxt - acc) / seg
            out.append((px_ + (qx - px_) * t, py_ + (qy - py_) * t))
            nxt += want
        acc += seg
    out.append(pts[-1])
    return out


def point_to_poly_dist(pt: tuple[float, float], pts: list[tuple[float, float]]) -> float:
    x, y = pt
    best = math.inf
    for i in range(1, len(pts)):
        (ax, ay), (bx, by) = pts[i - 1], pts[i]
        vx, vy = bx - ax, by - ay
        l2 = vx * vx + vy * vy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / l2))
        best = min(best, math.dist((x, y), (ax + vx * t, ay + vy * t)))
    return best


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(p1, p2, p3, p4) -> bool:
    d1, d2 = _orient(p3, p4, p1), _orient(p3, p4, p2)
    d3, d4 = _orient(p1, p2, p3), _orient(p1, p2, p4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return False


def poly_self_intersects(pts: list[tuple[float, float]]) -> bool:
    """True if any two non-adjacent segments of the polyline cross."""
    n = len(pts) - 1
    for i in range(n):
        for j in range(i + 2, n):
            if segments_intersect(pts[i], pts[i + 1], pts[j], pts[j + 1]):
                return True
    return False


def segment_crosses_poly(a, b, pts) -> bool:
    return any(segments_intersect(a, b, pts[i - 1], pts[i]) for i in range(1, len(pts)))


def _point_to_segment_dist(p, a, b) -> float:
    vx, vy = b[0] - a[0], b[1] - a[1]
    l2 = vx * vx + vy * vy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / l2))
    return math.dist(p, (a[0] + vx * t, a[1] + vy * t))


def seg_to_poly_distance(a, b, pts: list[tuple[float, float]]) -> float:
    """Minimum distance between segment a-b and a polyline (0 if they cross).

    Proper crossings, T-touches, and endpoint touches all yield ~0 — this is
    the pairing test for rungs: a rung counts as meeting a rail if it crosses
    it OR ends on/near it.
    """
    best = math.inf
    for i in range(1, len(pts)):
        c, d = pts[i - 1], pts[i]
        if segments_intersect(a, b, c, d):
            return 0.0
        best = min(best,
                   _point_to_segment_dist(a, c, d), _point_to_segment_dist(b, c, d),
                   _point_to_segment_dist(c, a, b), _point_to_segment_dist(d, a, b))
    return best


# -- document scale ---------------------------------------------------------------

def mm_per_unit(width: str | None, view_box: str | None) -> float:
    """mm per user unit from width="Xmm" + viewBox; SVG px default otherwise."""
    if width and view_box:
        m = re.match(r"^\s*([\d.]+)\s*mm\s*$", width)
        parts = view_box.replace(",", " ").split()
        if m and len(parts) == 4 and float(parts[2]) > 0:
            return float(m.group(1)) / float(parts[2])
    return _PX_TO_MM


# -- the gate --------------------------------------------------------------------

def _finding(severity: str, obj_id: str, check: str, message: str, **data) -> dict:
    return {"severity": severity, "object": obj_id, "check": check,
            "message": message, **data}


def check_satin(obj: dict[str, Any], scale: float) -> list[dict]:
    out: list[dict] = []
    rails = obj.get("rails") or []
    if len(rails) != 2:
        out.append(_finding("error", obj["id"], "rail_count",
                            f"satin column needs two rails, found {len(rails)}"))
        return out
    flat = [flatten_path(r) for r in rails]
    if any(len(f) < 2 for f in flat):
        out.append(_finding("error", obj["id"], "rail_geometry",
                            "a rail has no usable geometry"))
        return out

    # self-crossing rails (concave-turn failure mode)
    for i, f in enumerate(flat):
        if poly_self_intersects(f):
            out.append(_finding("error", obj["id"], "rail_self_cross",
                                f"rail {'AB'[i]} crosses itself", rail="AB"[i]))

    # closed-loop rails running in opposite directions: the engine pairs rail
    # sections by order-from-start, so opposed directions make the zigzag
    # sweep between unrelated sections (a web across the shape). Caught here
    # because the stitch count still looks plausible when it happens.
    closed = [math.dist(f[0], f[-1]) * scale < 0.5 for f in flat]
    if all(closed):
        def _signed_area(pts):
            return sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1]
                       for i in range(1, len(pts))) / 2
        a0, a1 = _signed_area(flat[0]), _signed_area(flat[1])
        if a0 * a1 < 0:
            out.append(_finding(
                "error", obj["id"], "rail_direction",
                "closed rails run in opposite directions — the zigzag will "
                "sweep across the shape; reverse one rail so both run the "
                "same way"))

    # rung pairing — the primary check (spec §8): every rung must meet BOTH rails
    tol = RUNG_PAIR_TOL_MM / scale
    rungs = obj.get("rungs") or []
    for ri, rung in enumerate(rungs):
        rf = flatten_path(rung)
        if len(rf) < 2:
            continue
        for side, f in enumerate(flat):
            if seg_to_poly_distance(rf[0], rf[-1], f) > tol:
                out.append(_finding(
                    "error", obj["id"], "rung_pairing",
                    f"rung {ri + 1} does not reach rail {'AB'[side]} — rails and "
                    "rungs are desynced (drag the rung endpoint back onto the rail "
                    "or delete and re-add it)",
                    rung=ri, rail="AB"[side]))

    # no rungs at all: node-count mismatch means the zigzag can twist
    if not rungs:
        n_a = len(re.findall(r"[MLCmlc]", rails[0]))
        n_b = len(re.findall(r"[MLCmlc]", rails[1]))
        if n_a != n_b:
            out.append(_finding(
                "error", obj["id"], "twist_risk",
                f"no rungs and rail node counts differ ({n_a} vs {n_b}) — "
                "zigzag interpolation can twist; add rungs to pair the rails"))

    # width envelope — nearest-rail distance per sample, NOT same-arc-fraction
    # pairing: closed or reversed rails (e.g. a ring converted to satin) put
    # equal fractions at unrelated positions, which reads as ring-diameter
    # "width". Nearest-point matches how the zigzag actually spans the column.
    a = sample_poly(flat[0], _WIDTH_SAMPLES)
    widths = [point_to_poly_dist(p, flat[1]) * scale for p in a]
    if widths:
        wmin, wmax = min(widths), max(widths)
        if wmin < SATIN_MIN_WIDTH_MM:
            out.append(_finding("error", obj["id"], "width_min",
                                f"satin narrows to {wmin:.2f}mm (min stitchable "
                                f"{SATIN_MIN_WIDTH_MM}mm)", width_mm=round(wmin, 2)))
        elif wmin < SATIN_NARROW_WARN_MM:
            out.append(_finding("warning", obj["id"], "width_narrow",
                                f"satin narrows to {wmin:.2f}mm — consider ≥"
                                f"{SATIN_NARROW_WARN_MM}mm", width_mm=round(wmin, 2)))
        if wmax > SATIN_MAX_WIDTH_MM:
            out.append(_finding("error", obj["id"], "width_max",
                                f"satin reaches {wmax:.2f}mm (max stitchable "
                                f"{SATIN_MAX_WIDTH_MM}mm) — split or use a fill",
                                width_mm=round(wmax, 2)))
        elif wmax > SATIN_WIDE_WARN_MM:
            out.append(_finding("warning", obj["id"], "width_wide",
                                f"satin reaches {wmax:.2f}mm — long stitches may "
                                "snag; consider splitting", width_mm=round(wmax, 2)))

    # very long unbroken satin
    length = max(poly_length(f) for f in flat) * scale
    if length > SATIN_LONG_WARN_MM:
        out.append(_finding("warning", obj["id"], "satin_long",
                            f"unbroken satin is {length:.0f}mm long — consider "
                            "splitting for trim/registration", length_mm=round(length)))
    return out


def check_fill(obj: dict[str, Any], scale: float) -> list[dict]:
    out: list[dict] = []
    boundary = flatten_path(obj.get("d") or "")
    if len(boundary) < 2:
        return out
    for role in ("start", "end"):
        h = obj.get(role)
        if not h:
            continue
        dist = point_to_poly_dist((h["x"], h["y"]), boundary) * scale
        if dist > FILL_HANDLE_FAR_MM:
            out.append(_finding(
                "warning", obj["id"], f"{role}_handle_far",
                f"fill_{role} handle is {dist:.1f}mm from the boundary — the "
                "engine snaps it to the nearest boundary point, which is "
                "probably not where you intended (drag it onto the shape)",
                distance_mm=round(dist, 1)))
    return out


def run_gate(project_file: str) -> dict[str, Any]:
    """Audit the design. `ok` is False only on error-severity findings."""
    from cli_anything_inkstitch.artifact.design_model import read_design

    design = read_design(project_file)
    scale = mm_per_unit(design.get("width"), design.get("viewBox"))
    findings: list[dict] = []
    for obj in design["objects"]:
        if obj["kind"] == "satin":
            findings.extend(check_satin(obj, scale))
        elif obj["kind"] == "fill":
            findings.extend(check_fill(obj, scale))
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "checked_objects": len(design["objects"])}
