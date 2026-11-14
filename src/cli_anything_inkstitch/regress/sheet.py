"""Contact sheets: the engine's stitches as lines in sewing order, plus a side-by-side of two sheets."""

from __future__ import annotations

import colorsys
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = (16, 17, 20)
FG = (225, 227, 232)
MAX_W, MAX_H, MAX_PX_PER_MM, MIN_PX_PER_MM = 1800, 1100, 70.0, 3.0
MARGIN = 28
UNTRIMMED_MM = 3.0           # red from here (matches compare.MAX_UNTRIMMED_MOVE_MM)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                     # Pillow < 10.1
        return ImageFont.load_default()


def draw_sheet(stream, rails_multi: list[dict], header: list[str], out: str | Path) -> None:
    """stream: [(cmd, x_mm, y_mm)], y down. rails_multi: [{"label", "left", "right"}] to outline."""
    segs, trims, floats = [], [], []
    prev = None
    last_cmd = None
    in_jump = False
    for cmd, x, y in stream:
        if cmd == "STITCH" and prev is not None and last_cmd == "STITCH":
            segs.append((prev, (x, y)))
        elif cmd == "JUMP" and prev is not None:
            cut = last_cmd in ("TRIM", "COLOR_CHANGE")
            if not in_jump:
                floats.append([prev, (x, y), cut])
            else:
                floats[-1][1] = (x, y)
        elif cmd == "TRIM":
            trims.append((x, y))
        in_jump = cmd == "JUMP"
        last_cmd = cmd
        if cmd in ("STITCH", "JUMP", "TRIM"):
            prev = (x, y)
    xs = [p[0] for s in segs for p in s] or [0.0]
    ys = [p[1] for s in segs for p in s] or [0.0]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    for f in floats:
        for p in f[:2]:
            x0, x1, y0, y1 = min(x0, p[0]), max(x1, p[0]), min(y0, p[1]), max(y1, p[1])
    w_mm, h_mm = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    k = max(MIN_PX_PER_MM, min(MAX_PX_PER_MM, (MAX_W - 2 * MARGIN) / w_mm,
                               (MAX_H - 2 * MARGIN) / h_mm))
    f_head = _font(15)
    head_h = 8 + 20 * len(header)
    W = max(int(w_mm * k) + 2 * MARGIN, 640)
    H = int(h_mm * k) + 2 * MARGIN + head_h
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    ox = MARGIN + (W - 2 * MARGIN - w_mm * k) / 2

    def T(p):
        return (ox + (p[0] - x0) * k, head_h + MARGIN + (p[1] - y0) * k)

    # column outlines first, so stitches sit on top
    for r in rails_multi:
        poly = [T(p) for p in r["left"]] + [T(p) for p in r["right"][::-1]]
        d.line(poly + [poly[0]], fill=(255, 64, 200), width=2)
        d.text((poly[0][0] + 3, poly[0][1] - 18), r["label"], fill=(255, 120, 220), font=f_head)
    n = max(len(segs) - 1, 1)
    width = 2 if k >= 25 else 1
    for i, (a, b) in enumerate(segs):
        hue = 0.62 * (1 - i / n)           # blue first -> red last
        r, g, bl = colorsys.hsv_to_rgb(hue, 0.75, 1.0)
        d.line([T(a), T(b)], fill=(int(r * 255), int(g * 255), int(bl * 255)), width=width)
    for a, b, cut in floats:
        if cut:
            d.line([T(a), T(b)], fill=(90, 94, 104), width=1)
        elif math.dist(a, b) >= 0.2:
            big = math.dist(a, b) >= UNTRIMMED_MM
            d.line([T(a), T(b)], fill=(255, 40, 40) if big else (255, 160, 60), width=3 if big else 1)
            if big:
                m = T(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
                d.text((m[0] + 4, m[1] + 4), f"{math.dist(a, b):.1f} mm", fill=(255, 90, 90), font=f_head)
    for p in trims:
        cx, cy = T(p)
        d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], outline=(255, 255, 255), width=1)
    for i, line in enumerate(header):
        d.text((10, 6 + 20 * i), line, fill=FG, font=f_head)
    scale = f"{k:.0f} px/mm"
    d.text((W - 12 - 9 * len(scale), 6), scale, fill=(140, 144, 154), font=f_head)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    im.save(out, optimize=True)


def side_by_side(left: str | Path, right: str | Path, left_label: str, right_label: str,
                 out: str | Path) -> None:
    ims = [Image.open(left).convert("RGB"), Image.open(right).convert("RGB")]
    f = _font(18)
    gap, bar = 14, 30
    H = max(i.height for i in ims) + bar
    W = sum(i.width for i in ims) + gap
    im = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(im)
    x = 0
    for img, label in zip(ims, (left_label, right_label)):
        d.text((x + 8, 5), label, fill=(255, 255, 255), font=f)
        im.paste(img, (x, bar))
        x += img.width + gap
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    im.save(out, optimize=True)
