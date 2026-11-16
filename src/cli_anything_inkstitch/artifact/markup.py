"""Mark-up on a feedback message: pins and sketches drawn over the design (Bob 11/15, msg 7699).

Bob can already select whole shapes and send them with a note. Often he means a SPOT ("the
stitches here") or a direction ("make them go this way", "bring them all the way over here"),
which a shape selection can't say. The editor's Mark up tool lets him drop numbered pins and
draw pen strokes or arrows over the design; they travel with his next message as `markup`:

    {"pins":    [{"n": 1, "x": .., "y": .., "x_mm": .., "y_mm": .., "objects": [ids]}],
     "strokes": [{"kind": "pen" | "arrow", "points": [[x, y], ...], "points_mm": [[..]..],
                  "objects": [ids]}],
     "view":    {"x": .., "y": .., "w": .., "h": ..}}

x/y are SVG user units (the design's own coordinates); *_mm are document mm from the viewBox's
top-left, y down (the frame shapes-to-satin reports outlines in). An arrow points from its
first point to its last. `objects` are the shapes under each mark (the editor's hit test).

The agent reading the batch can't see Bob's screen, so the server also renders a snapshot: the
engine's stitch plan around the marks with the marks drawn on it in red (render_snapshot). Its
path goes in the feedback item; the agent opens it before acting.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

MARK = (224, 36, 94)            # the editor's mark colour
MAX_POINTS = 2000               # per stroke, after the editor's decimation
MAX_MARKS = 60


def clean(markup: Any) -> dict[str, Any] | None:
    """The markup a client sent, validated and trimmed to known fields; None when empty."""
    if not isinstance(markup, dict):
        return None

    def num(v):
        return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None

    def ids(v):
        return [str(i) for i in v[:50]] if isinstance(v, list) else []

    pins = []
    for p in (markup.get("pins") or [])[:MAX_MARKS]:
        if not isinstance(p, dict) or num(p.get("x")) is None or num(p.get("y")) is None:
            continue
        pins.append({"n": int(p.get("n") or len(pins) + 1), "x": num(p["x"]), "y": num(p["y"]),
                     "x_mm": num(p.get("x_mm")), "y_mm": num(p.get("y_mm")), "objects": ids(p.get("objects"))})
    strokes = []
    for s in (markup.get("strokes") or [])[:MAX_MARKS]:
        if not isinstance(s, dict):
            continue
        pts = [[num(q[0]), num(q[1])] for q in (s.get("points") or [])[:MAX_POINTS]
               if isinstance(q, (list, tuple)) and len(q) >= 2]
        pts = [q for q in pts if None not in q]
        if len(pts) < 2:
            continue
        mm = [[num(q[0]), num(q[1])] for q in (s.get("points_mm") or [])[:MAX_POINTS]
              if isinstance(q, (list, tuple)) and len(q) >= 2]
        strokes.append({"kind": "arrow" if s.get("kind") == "arrow" else "pen", "points": pts,
                        "points_mm": [q for q in mm if None not in q], "objects": ids(s.get("objects"))})
    if not pins and not strokes:
        return None
    out: dict[str, Any] = {"pins": pins, "strokes": strokes}
    v = markup.get("view")
    if isinstance(v, dict) and all(num(v.get(k)) is not None for k in ("x", "y", "w", "h")):
        out["view"] = {k: num(v[k]) for k in ("x", "y", "w", "h")}
    return out


def summary(markup: dict[str, Any] | None) -> str:
    """'2 pins, 1 arrow, 1 sketch' for the chat log."""
    if not markup:
        return ""
    n_pin = len(markup.get("pins") or [])
    n_arrow = sum(1 for s in markup.get("strokes") or [] if s["kind"] == "arrow")
    n_pen = sum(1 for s in markup.get("strokes") or [] if s["kind"] == "pen")
    parts = [f"{n} {w}{'s' if n > 1 else ''}" for n, w in ((n_pin, "pin"), (n_arrow, "arrow"), (n_pen, "sketch")) if n]
    return ", ".join(parts)


def _mark_points(markup: dict[str, Any]) -> list[tuple[float, float]]:
    pts = [(p["x"], p["y"]) for p in markup.get("pins") or []]
    for s in markup.get("strokes") or []:
        pts += [tuple(q) for q in s["points"]]
    return pts


def _bounds(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def render_snapshot(markup: dict[str, Any], out_path: str | Path, *, blocks: list[dict] | None = None,
                    plan: Callable[[], dict] | None = None, uu_per_mm: float = 96 / 25.4,
                    max_px: int = 1400) -> Path:
    """PNG of the stitch plan around the marks with the marks drawn on it.

    blocks: the plan's colour blocks (design_model.extract_stitch_blocks), or `plan` to fetch them
    (the engine run; skipped when blocks are given). The view is the marks' box with a margin
    (at least 12 mm, or half the marks' size), and when that's well inside the design, a
    whole-design inset in the corner with the view's box on it."""
    from PIL import Image, ImageDraw, ImageFont

    if blocks is None:
        blocks = (plan() if plan else {}).get("blocks", [])
    stitch_pts = [tuple(q) for b in blocks for path in b.get("paths", []) for q in path]
    marks = _mark_points(markup)
    if not marks:
        raise ValueError("no marks to render")
    mx0, my0, mx1, my1 = _bounds(marks)
    pad = max(12 * uu_per_mm, 0.5 * max(mx1 - mx0, my1 - my0))
    vx0, vy0, vx1, vy1 = mx0 - pad, my0 - pad, mx1 + pad, my1 + pad
    if stitch_pts:
        dx0, dy0, dx1, dy1 = _bounds(stitch_pts)
    else:
        dx0, dy0, dx1, dy1 = vx0, vy0, vx1, vy1
    w, h = max(vx1 - vx0, 1e-6), max(vy1 - vy0, 1e-6)
    k = max_px / max(w, h)
    W, H = max(1, int(w * k)), max(1, int(h * k))
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img, "RGBA")

    def f(p, x0=vx0, y0=vy0, s=k):
        return ((p[0] - x0) * s, (p[1] - y0) * s)

    def hexrgb(c):
        c = (c or "#000000").lstrip("#")
        try:
            return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return (0, 0, 0)

    def draw_plan(draw, tf, width):
        for b in blocks:
            col = hexrgb(b.get("color"))
            col = tuple(min(220, v) for v in col)          # white thread stays visible on white
            for path in b.get("paths", []):
                draw.line([tf(q) for q in path], fill=col + (200,), width=width)

    draw_plan(d, f, 1)
    lw = max(3, int(0.6 * uu_per_mm * k))
    try:
        font = ImageFont.load_default(size=max(14, lw * 4))
    except TypeError:
        font = ImageFont.load_default()
    for s in markup.get("strokes") or []:
        pts = [f(q) for q in s["points"]]
        d.line(pts, fill=MARK + (235,), width=lw, joint="curve")
        if s["kind"] == "arrow":
            # arrowhead at the last point, along the last ~3 mm of the stroke
            tip = pts[-1]
            back = next((q for q in reversed(pts[:-1]) if math.dist(q, tip) > 3 * uu_per_mm * k), pts[0])
            ang = math.atan2(tip[1] - back[1], tip[0] - back[0])
            L = lw * 5
            for da in (2.6, -2.6):
                d.line([tip, (tip[0] + L * math.cos(ang + da), tip[1] + L * math.sin(ang + da))],
                       fill=MARK + (235,), width=lw)
    for p in markup.get("pins") or []:
        c = f((p["x"], p["y"]))
        r = lw * 3
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=MARK + (235,), outline=(255, 255, 255), width=2)
        d.text(c, str(p["n"]), fill="white", font=font, anchor="mm")
    # whole-design inset when the view is a small part of the design
    if stitch_pts and (w * h) < 0.5 * (dx1 - dx0) * (dy1 - dy0):
        iw = max(1e-6, dx1 - dx0)
        ih = max(1e-6, dy1 - dy0)
        ik = (min(W, H) * 0.28) / max(iw, ih)
        IW, IH = int(iw * ik) + 8, int(ih * ik) + 8
        inset = Image.new("RGB", (IW, IH), (246, 246, 246))
        di = ImageDraw.Draw(inset, "RGBA")

        def g(p):
            return ((p[0] - dx0) * ik + 4, (p[1] - dy0) * ik + 4)

        draw_plan(di, g, 1)
        di.rectangle([g((vx0, vy0)), g((vx1, vy1))], outline=MARK + (255,), width=2)
        di.rectangle([0, 0, IW - 1, IH - 1], outline=(150, 150, 150), width=1)
        img.paste(inset, (W - IW - 6, 6))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
