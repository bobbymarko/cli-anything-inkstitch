"""Compose text from an Ink/Stitch font directory into stitchable elements.

Ink/Stitch fonts are PRE-DIGITIZED: every glyph layer contains finished
satin/stroke elements with their params. Lettering is therefore layout,
not digitization — copy glyph elements, kern, scale, place on a baseline.

Engine contract (readers cited — the format is the engine's, not ours):
* glyph layers — lib/lettering/font_variant.py: groups labeled
  "GlyphLayer-<char>" (NFC-normalized), one per character.
* baseline — lib/lettering/glyph.py _process_baseline: the SVG guide
  labeled "baseline" (we reuse font_format/svg_build._get_font_baseline_y).
* metrics — font.json: horiz_adv_x (per-char advance, falls back to glyph
  width), horiz_adv_x_space, kerning_pairs (advance REDUCTION, SVG hkern
  semantics), leading (line height), size (mm at scale 1), min/max_scale
  (satin params are physical mm and do not scale with geometry — that is
  WHY fonts carry scale limits), default_variant.
* shipped fonts live under <binary>/../Resources/fonts/src, each file
  optionally .xz-compressed (font.json.xz, ltr.svg.xz).

Scaling multiplies geometry only. Params like zigzag_spacing_mm stay
physical — correct: density shouldn't change with letter size — which is
exactly what makes exceeding the font's scale range a real warning.
"""

from __future__ import annotations

import json
import lzma
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
SVG_NS = "http://www.w3.org/2000/svg"
PX_PER_MM = 96.0 / 25.4
LABEL = f"{{{INKSCAPE_NS}}}label"


@dataclass
class Glyph:
    char: str
    elements: list          # etree elements, transforms baked, as-authored coords
    min_x: float
    width: float


@dataclass
class Font:
    name: str
    dir: Path
    meta: dict
    baseline_y: float
    glyphs: dict[str, Glyph] = field(default_factory=dict)

    @property
    def size_mm(self) -> float:
        return float(self.meta.get("size") or 10.0)

    @property
    def leading(self) -> float:
        return float(self.meta.get("leading") or self.meta.get("units_per_em") or 30.0)


def _read_maybe_xz(base: Path) -> bytes | None:
    if base.exists():
        return base.read_bytes()
    xz = base.with_name(base.name + ".xz")
    if xz.exists():
        return lzma.open(xz).read()
    return None


def shipped_fonts_dir(binary_path: str | None) -> Path | None:
    """fonts/src inside the installed binary's Resources, if present."""
    if not binary_path:
        return None
    for up in Path(binary_path).parents:
        cand = up / "Resources" / "fonts" / "src"
        if cand.is_dir():
            return cand
    return None


def resolve_font(name_or_dir: str, binary_path: str | None = None) -> Path:
    p = Path(name_or_dir)
    if p.is_dir():
        return p
    shipped = shipped_fonts_dir(binary_path)
    if shipped and (shipped / name_or_dir).is_dir():
        return shipped / name_or_dir
    raise FileNotFoundError(
        f"font {name_or_dir!r} is neither a directory nor a shipped font"
        + (f" (searched {shipped})" if shipped else " (no shipped fonts found)"))


def list_fonts(binary_path: str | None) -> list[dict]:
    shipped = shipped_fonts_dir(binary_path)
    if not shipped:
        return []
    out = []
    for d in sorted(shipped.iterdir()):
        raw = _read_maybe_xz(d / "font.json") if d.is_dir() else None
        if not raw:
            continue
        try:
            meta = json.loads(raw)
        except ValueError:
            continue
        out.append({"font": d.name, "name": meta.get("name"),
                    "size_mm": meta.get("size"),
                    "glyphs": len(meta.get("glyphs") or []),
                    "scale": [meta.get("min_scale"), meta.get("max_scale")]})
    return out


def _compose_ctm(elem, stop):
    """Cumulative transform from `stop`'s children down to elem, baked order."""
    from cli_anything_inkstitch.svg.geometry import (
        IDENTITY,
        matrix_multiply,
        parse_transform,
    )
    chain = []
    cur = elem
    while cur is not None and cur is not stop:
        chain.append(cur)
        cur = cur.getparent()
    m = IDENTITY
    for node in reversed(chain):
        t = node.get("transform")
        if t:
            m = matrix_multiply(m, parse_transform(t))
    return m


def load_font(font_dir: str | Path) -> Font:
    font_dir = Path(font_dir)
    raw = _read_maybe_xz(font_dir / "font.json")
    meta = json.loads(raw) if raw else {}
    variant = meta.get("default_variant") or "ltr"
    svg_raw = None
    for cand in (f"{variant}.svg", "→.svg", "ltr.svg", "rtl.svg"):
        svg_raw = _read_maybe_xz(font_dir / cand)
        if svg_raw:
            break
    if not svg_raw:
        raise FileNotFoundError(f"no variant SVG in {font_dir}")
    tree = etree.ElementTree(etree.fromstring(svg_raw))
    from cli_anything_inkstitch.font_format.svg_build import _get_font_baseline_y
    baseline = _get_font_baseline_y(tree)
    from cli_anything_inkstitch.svg.geometry import path_bbox, transform_d

    font = Font(name=meta.get("name") or font_dir.name, dir=font_dir,
                meta=meta, baseline_y=baseline)
    for layer in tree.getroot().iter(f"{{{SVG_NS}}}g"):
        label = layer.get(LABEL) or ""
        if not label.startswith("GlyphLayer-"):
            continue
        char = unicodedata.normalize("NFC", label[len("GlyphLayer-"):])
        elements = []
        xs: list[float] = []
        for node in layer.iter():
            if not isinstance(node.tag, str):
                continue
            if etree.QName(node.tag).localname != "path" or not node.get("d"):
                continue
            copy = etree.fromstring(etree.tostring(node))
            m = _compose_ctm(node, layer.getparent())
            copy.set("d", transform_d(copy.get("d"), m))
            copy.attrib.pop("transform", None)
            bb = path_bbox(copy.get("d"))
            if bb is not None:
                xs.extend([bb[0], bb[2]])
            elements.append(copy)
        if not elements or not xs:
            continue
        font.glyphs[char] = Glyph(char=char, elements=elements,
                                  min_x=min(xs), width=max(xs) - min(xs))
    return font


@dataclass
class Placement:
    elements: list          # positioned element copies, transforms baked
    warnings: list[str]
    width_px: float
    height_px: float
    dropped_commands: int


def compose(font: Font, text: str, *, height_mm: float | None = None,
            at_px: tuple[float, float] = (0.0, 0.0),
            tracking_em: float = 0.1) -> Placement:
    """Lay out `text` (\\n = new line) starting its baseline at `at_px`."""
    from cli_anything_inkstitch.svg.geometry import transform_d
    meta = font.meta
    scale = 1.0 if height_mm is None else height_mm / font.size_mm
    warnings = []
    lo, hi = meta.get("min_scale"), meta.get("max_scale")
    if lo and scale < float(lo) - 1e-9:
        warnings.append(
            f"scale {scale:.2f} is below the font's minimum {lo} — satin "
            "params are physical mm and do not shrink with the letters; "
            "expect over-dense columns")
    if hi and scale > float(hi) + 1e-9:
        warnings.append(
            f"scale {scale:.2f} exceeds the font's maximum {hi} — columns "
            "get wide while density stays fixed; expect gappy satin")
    upe = float(meta.get("units_per_em") or 30.0)
    tracking = tracking_em * upe
    adv_map = meta.get("horiz_adv_x") or {}
    kern = meta.get("kerning_pairs") or {}
    space_adv = float(meta.get("horiz_adv_x_space") or upe / 2.0)

    placed = []
    missing: set[str] = set()
    pen_x, line = 0.0, 0
    prev_char = None
    max_x = 0.0
    dropped = 0
    for ch in text:
        if ch == "\n":
            pen_x, prev_char, line = 0.0, None, line + 1
            continue
        if ch == " ":
            pen_x += space_adv
            prev_char = None
            continue
        glyph = font.glyphs.get(ch) or font.glyphs.get(
            unicodedata.normalize("NFC", ch))
        if glyph is None:
            missing.add(ch)
            pen_x += space_adv
            prev_char = None
            continue
        if prev_char is not None:
            pen_x -= float(kern.get(prev_char + ch, 0.0))
        dx = pen_x - glyph.min_x
        dy = line * font.leading - font.baseline_y
        # bake: translate within the font, then scale, then move to at_px
        m = (scale, 0.0, 0.0, scale,
             at_px[0] + dx * scale, at_px[1] + dy * scale)
        for el in glyph.elements:
            copy = etree.fromstring(etree.tostring(el))
            copy.set("d", transform_d(copy.get("d"), m))
            placed.append(copy)
        adv = float(adv_map.get(ch) or (glyph.width + tracking))
        pen_x += adv
        max_x = max(max_x, pen_x)
        prev_char = ch
    if missing:
        warnings.append("glyphs not in this font (skipped): "
                        + "".join(sorted(missing)))
    return Placement(elements=placed, warnings=warnings,
                     width_px=max_x * scale,
                     height_px=(line * font.leading + upe) * scale,
                     dropped_commands=dropped)
