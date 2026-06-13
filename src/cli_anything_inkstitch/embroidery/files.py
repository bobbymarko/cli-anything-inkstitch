"""Embroidery file discovery and filename→character parsing."""

from __future__ import annotations

import re
from pathlib import Path

from cli_anything_inkstitch.errors import UserError

# ---------------------------------------------------------------------------
# DST/embroidery import helpers
# ---------------------------------------------------------------------------

# 1 DST step = 0.1mm; 1 SVG px = 0.2646mm (= 25.4/96 mm)
_DST_MM_PER_UNIT = 0.1
_SVG_MM_PER_PX = 25.4 / 96  # ≈ 0.2646
_DST_TO_SVG = _DST_MM_PER_UNIT / _SVG_MM_PER_PX  # ≈ 0.378

# Embroidery file extensions pyembroidery can read
_EMB_EXTS = {".dst", ".pes", ".vip", ".vp3", ".jef", ".hus", ".exp",
             ".xxx", ".sew", ".shv", ".cnd", ".bx", ".pec"}

_PUNCT_MAP = {
    "period": ".", "comma": ",", "exclamation": "!", "question": "?",
    "ampersand": "&", "apostrophe": "'", "quote": '"', "slash": "/",
    "backslash": "\\", "dash": "-", "hyphen": "-", "underscore": "_",
    "plus": "+", "minus": "-", "equals": "=", "equal": "=",
    "at": "@", "pound": "#", "dollar": "$", "percent": "%",
    "caret": "^", "asterisk": "*", "tilde": "~",
    "parenopen": "(", "parenclose": ")", "parenthesisopen": "(",
    "parenthesisclose": ")", "bracketopen": "[", "bracketclose": "]",
    "braceopen": "{", "braceclose": "}",
    "colon": ":", "semicolon": ";", "pipe": "|",
    "lessthan": "<", "greaterthan": ">",
}


def _parse_char_from_stem(stem: str) -> str | None:
    """Try to extract a single Unicode character from an embroidery filename stem.

    Handles common naming conventions:
      CapA / Cap_A → 'A'
      LowA / Lowa / Low_a → 'a'
      CapitalA → 'A'        (full-word "Capital" prefix)
      Lowercaseb → 'b'      (full-word "Lowercase" prefix)
      PunComma / Pun_Comma → ','
      A_cap / a_low → 'A' / 'a'
      Single letter or digit → that char
      a-Star* → 'a'
      'Floral Alphabet A15' → 'A'
    """
    # Strip common prefix patterns that encode size info
    s = re.sub(r'\d+(\.\d+)?in', '', stem, flags=re.IGNORECASE)
    # Strip leading brand prefixes (e.g. "TSS-Homerun", "StitchtopiaActuallyRomantic")
    # by finding Cap/Low/Pun patterns or single-char patterns

    # Pattern: Capital<Letter> / Lowercase<Letter> (full-word prefixes used by
    # some packs, e.g. CapitalA.xxx, Lowercase_b.dst). Run BEFORE the shorter
    # Cap/Low rules because otherwise "Low" matches the start of "Lowercaseb"
    # and captures the wrong letter ('e'). Allow optional underscore/hyphen
    # between the prefix and the letter.
    m = re.search(r'Capital[_-]?([A-Za-z])', s, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.search(r'Lowercase[_-]?([A-Za-z])', s, flags=re.IGNORECASE)
    if m:
        return m.group(1).lower()

    # Pattern: Cap<Letter>
    m = re.search(r'Cap([A-Z])', s)
    if m:
        return m.group(1)

    # Pattern: Low<Letter> (case-insensitive suffix, but letter is lowercase)
    m = re.search(r'Low([A-Za-z])', s)
    if m:
        return m.group(1).lower()

    # Pattern: Pun<Word>
    m = re.search(r'Pun([A-Za-z]+)', s, flags=re.IGNORECASE)
    if m:
        word = m.group(1).lower()
        if word in _PUNCT_MAP:
            return _PUNCT_MAP[word]
        return None

    # Pattern: <prefix>_<letter>_(middle|side) — monogram-pack position convention.
    # The letter's case is preserved (SSP_A_middle → 'A', SSP_a_side → 'a') so a
    # downstream tool can case-encode middle vs. side into one font when the casing
    # cleanly partitions, or split into two fonts otherwise.
    m = re.search(r'_([A-Za-z])_(?:middle|side)\b', s, flags=re.IGNORECASE)
    if m:
        return m.group(1)

    # Pattern: <UpperLetter>u — used by some monogram packs (Chain Monogram,
    # Decorative Monogram) to mark the upper-case / center-of-monogram glyph;
    # the matching lower-case / side glyph ships as a single letter (handled by
    # the single-character fallback further down).
    # Only matches uppercase to avoid swallowing words ending in "u".
    m = re.match(r'^([A-Z])u$', s)
    if m:
        return m.group(1)

    # Pattern: <Letter>_cap or <Letter>_Cap
    m = re.match(r'^([A-Za-z])_cap', s, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper()

    # Pattern: <letter>_low
    m = re.match(r'^([A-Za-z])_low', s, flags=re.IGNORECASE)
    if m:
        return m.group(1).lower()

    # Pattern: <char>-<anything> (e.g. "a-Star1inch")
    m = re.match(r'^([A-Za-z0-9])-', s)
    if m:
        return m.group(1)

    # Pattern: "Multi Word Prefix {char}..." e.g. "Floral Alphabet A15" → 'A',
    # "Floral Alphabet 0 2 1" → '0', "Floral Alphabet 215 1" → '2'.
    # Two or more title-case words precede the glyph character.
    m = re.match(r'^(?:[A-Za-z][A-Za-z\-]*\s+){2,}([A-Za-z0-9])', s)
    if m:
        ch = m.group(1)
        return ch if (ch.isupper() or ch.isdigit()) else ch.lower()

    # Pattern: "Floral Alphabet A15" → look for '<Word> <SingleLetter><digits>'
    m = re.search(r'\b([A-Za-z])\d', s)
    if m:
        return m.group(1)

    # Pattern: stem ends with a single digit (e.g. "TSS-Homerun0" after size strip)
    m = re.search(r'(\d)$', s)
    if m:
        return m.group(1)

    # Pattern: stem ends with a single non-alphanumeric separator + char
    m = re.search(r'[^A-Za-z0-9]([A-Za-z0-9])$', s)
    if m:
        c = m.group(1)
        if c.isdigit() or c.isupper():
            return c

    # Strip all non-alphanumeric from start, take first char if single
    clean = re.sub(r'^[^A-Za-z0-9]+', '', s)
    if re.match(r'^[A-Za-z0-9]$', clean):
        return clean
    if re.match(r'^[A-Za-z0-9][^A-Za-z0-9]', clean):
        return clean[0]

    return None


def _find_embroidery_files(source_dir: Path, pick_ext: str | None = None) -> list[Path]:
    """Return all embroidery files in source_dir (non-recursive)."""
    files = []
    for p in source_dir.iterdir():
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in _EMB_EXTS:
            continue
        if pick_ext and ext != pick_ext:
            continue
        files.append(p)
    return sorted(files)


def _read_embroidery(path: Path):
    """Read any supported embroidery file via pyembroidery."""
    try:
        import pyembroidery
    except ImportError:
        raise UserError("pyembroidery is required — install it with: pip install pyembroidery")

    pattern = pyembroidery.read(str(path))
    if pattern is None:
        raise UserError(f"could not read embroidery file: {path}")
    return pattern
