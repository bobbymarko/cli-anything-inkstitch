"""font.json metadata handling — defaults, load, save."""

from __future__ import annotations

import json
from pathlib import Path

from cli_anything_inkstitch.errors import UserError


def _default_font_json(name: str, units_per_em: float, size_mm: float,
                        leading: float,
                        description: str = "",
                        keywords: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "keywords": keywords or [],
        "units_per_em": units_per_em,
        "leading": leading,
        "size": size_mm,
        "min_scale": 0.5,
        "max_scale": 3.0,
        "auto_satin": False,
        "reversible": False,
        "sortable": False,
        "letter_case": "",
        "default_glyph": "?",
        "kerning_pairs": {},
        "horiz_adv_x_default": round(units_per_em * 0.6, 1),
        "horiz_adv_x_space": round(units_per_em * 0.3, 1),
        "horiz_adv_x": {},
        "glyphs": [],
        "default_variant": "→",
        "text_direction": "ltr",
        "baseline_y": 0,
    }


def _load_font_json(font_dir: Path) -> dict:
    p = font_dir / "font.json"
    if not p.exists():
        raise UserError(f"font.json not found in {font_dir}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _save_font_json(font_dir: Path, data: dict) -> None:
    p = font_dir / "font.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
