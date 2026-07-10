"""Thread-manufacturer palettes, read the way the engine reads them.

The engine parses GIMP palette files in lib/threads/palette.py
(ThreadPalette.parse_palette_file): the first line must be 'GIMP Palette'
(case-insensitive), the Name line's 'Ink/Stitch: ' prefix is stripped for
display, the next two lines (columns, header) are skipped, and each data
row is 'R G B <name...> <number>' — three ints, then the remainder
rsplit once into name and catalog number; malformed rows are silently
skipped. This module mirrors those exact rules over the palette files the
engine ships (<source>/palettes/*.gpl, also bundled with the installed
binary).
"""

from __future__ import annotations

from pathlib import Path

_CACHE: dict[str, dict] = {}


def palettes_dir() -> Path | None:
    """The engine's palettes directory: source checkout first (the schema
    records where it was extracted from), then the installed bundle
    (same layout probing as svg/commands.py _bundled_symbol)."""
    try:
        from cli_anything_inkstitch.schema.cache import load_schema
        root = load_schema().get("source", {}).get("root")
        if root and (Path(root) / "palettes").is_dir():
            return Path(root) / "palettes"
    except Exception:  # noqa: BLE001 — fall through to the binary bundle
        pass
    try:
        from cli_anything_inkstitch.binary import discover
        binary = discover()
    except Exception:  # noqa: BLE001
        binary = None
    if binary:
        b = Path(binary)
        for candidate in (b.parent.parent / "Resources" / "palettes",
                          b.parent / "palettes",
                          b.parent / "_internal" / "palettes"):
            if candidate.is_dir():
                return candidate
    return None


def parse_gpl(path: Path) -> dict | None:
    """Parse one GIMP palette file with the engine's tolerances."""
    try:
        with open(path, encoding="utf8") as f:
            if f.readline().strip().lower() != "gimp palette":
                return None
            name = f.readline().strip()
            if name.lower().startswith("name:"):
                name = name[5:].strip()
            if name.lower().startswith("ink/stitch: "):
                name = name[12:]
            f.readline()   # columns
            f.readline()   # header comment
            threads = []
            for line in f:
                try:
                    fields = line.split(None, 3)
                    r, g, b = (int(x) for x in fields[:3])
                    thread_name, thread_number = fields[3].strip().rsplit(" ", 1)
                except (ValueError, IndexError):
                    continue
                threads.append({
                    "hex": f"#{r:02x}{g:02x}{b:02x}",
                    "name": thread_name.strip(),
                    "number": thread_number,
                })
            return {"name": name, "threads": threads}
    except OSError:
        return None


def _index() -> dict[str, dict]:
    d = palettes_dir()
    if d is None:
        return {}
    key = str(d)
    if key in _CACHE:
        return _CACHE[key]
    out: dict[str, dict] = {}
    for path in sorted(d.glob("*.gpl")):
        pal = parse_gpl(path)
        if pal and pal["threads"]:
            out[pal["name"]] = pal
    _CACHE[key] = out
    return out


def list_palettes() -> list[str]:
    return list(_index())


def read_palette(name: str) -> dict | None:
    return _index().get(name)
