"""Hex-to-named-color resolution for human-friendly element descriptions.

Uses Euclidean distance in RGB. Good enough for "this element looks blue"
classification — not for color-management-grade palette mapping.
"""

from __future__ import annotations

import re

# A focused list of CSS named colors that appear in real designs. Smaller than
# the full CSS3 list (147 names) — picking high-frequency, visually distinct
# names so the closest-match output is recognizable.
NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "black":      (0, 0, 0),
    "white":      (255, 255, 255),
    "gray":       (128, 128, 128),
    "darkgray":   (169, 169, 169),
    "lightgray":  (211, 211, 211),
    "silver":     (192, 192, 192),
    "slategray":  (112, 128, 144),
    "darkslategray": (47, 79, 79),
    "red":        (255, 0, 0),
    "darkred":    (139, 0, 0),
    "maroon":     (128, 0, 0),
    "coral":      (255, 127, 80),
    "salmon":     (250, 128, 114),
    "pink":       (255, 192, 203),
    "orange":     (255, 165, 0),
    "darkorange": (255, 140, 0),
    "gold":       (255, 215, 0),
    "yellow":     (255, 255, 0),
    "khaki":      (240, 230, 140),
    "olive":      (128, 128, 0),
    "lime":       (0, 255, 0),
    "limegreen":     (50, 205, 50),
    "mediumseagreen":(60, 179, 113),
    "seagreen":      (46, 139, 87),
    "green":      (0, 128, 0),
    "darkgreen":  (0, 100, 0),
    "teal":       (0, 128, 128),
    "darkcyan":   (0, 139, 139),
    "cadetblue":  (95, 158, 160),
    "turquoise":  (64, 224, 208),
    "aqua":       (0, 255, 255),
    "lightblue":  (173, 216, 230),
    "blue":       (0, 0, 255),
    "navy":       (0, 0, 128),
    "indigo":     (75, 0, 130),
    "purple":     (128, 0, 128),
    "violet":     (238, 130, 238),
    "magenta":    (255, 0, 255),
    "brown":      (165, 42, 42),
    "tan":        (210, 180, 140),
    "beige":      (245, 245, 220),
    "ivory":      (255, 255, 240),
    "lavender":   (230, 230, 250),
}

_HEX3_RE = re.compile(r"^#?([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$")
_HEX6_RE = re.compile(r"^#?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$")


def hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    """Parse #abc, #aabbcc, or a CSS color name. Returns None if unparseable."""
    if not value:
        return None
    s = value.strip()
    m = _HEX6_RE.match(s)
    if m:
        return (int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16))
    m = _HEX3_RE.match(s)
    if m:
        return tuple(int(c * 2, 16) for c in m.groups())  # type: ignore[return-value]
    # Fallback: maybe it's already a name.
    if s.lower() in NAMED_COLORS:
        return NAMED_COLORS[s.lower()]
    return None


def closest_named(hex_color: str) -> str | None:
    """Return the closest named color by Euclidean RGB distance."""
    rgb = hex_to_rgb(hex_color)
    if rgb is None:
        return None
    best_name: str | None = None
    best_dist = float("inf")
    for name, ref in NAMED_COLORS.items():
        d = (rgb[0] - ref[0]) ** 2 + (rgb[1] - ref[1]) ** 2 + (rgb[2] - ref[2]) ** 2
        if d < best_dist:
            best_name, best_dist = name, d
    return best_name
