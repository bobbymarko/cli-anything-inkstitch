"""Manufacturer thread palettes — parser mirrors the engine's reader
(inkstitch lib/threads/palette.py ThreadPalette.parse_palette_file)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli_anything_inkstitch.embroidery import palettes as P

GPL = """GIMP Palette
Name: Ink/Stitch: Testco Rayon
Columns: 4
# RGB Value\t\tColor Name Number
240\t186\t212\t  Sugar Pink   1624
not a color line
237\t171\t194\t  Two Word Name   1636
"""


class TestParseGpl:
    def test_engine_parse_rules(self, tmp_path):
        f = tmp_path / "t.gpl"
        f.write_text(GPL)
        pal = P.parse_gpl(f)
        assert pal["name"] == "Testco Rayon"          # Ink/Stitch prefix stripped
        assert len(pal["threads"]) == 2               # malformed row skipped
        assert pal["threads"][0] == {"hex": "#f0bad4",
                                     "name": "Sugar Pink", "number": "1624"}
        # remainder rsplit once: multi-word names keep their spaces
        assert pal["threads"][1]["name"] == "Two Word Name"

    def test_non_gimp_file_rejected(self, tmp_path):
        f = tmp_path / "bad.gpl"
        f.write_text("Adobe Swatches\nwhatever\n")
        assert P.parse_gpl(f) is None


class TestEnginePalettes:
    """Against the real files the engine ships (skips without a checkout)."""

    @pytest.fixture(autouse=True)
    def real_dir(self, monkeypatch):
        src = Path(__file__).parent.parent / "inkstitch" / "palettes"
        if not src.is_dir():
            pytest.skip("inkstitch source checkout not present")
        monkeypatch.setattr(P, "palettes_dir", lambda: src)
        P._CACHE.clear()

    def test_robison_anton_present(self):
        names = P.list_palettes()
        assert any("Robison-Anton" in n for n in names)
        assert len(names) > 50

    def test_threads_have_hex_and_numbers(self):
        name = next(n for n in P.list_palettes() if "Robison-Anton" in n)
        pal = P.read_palette(name)
        assert len(pal["threads"]) > 50
        for t in pal["threads"][:10]:
            assert t["hex"].startswith("#") and len(t["hex"]) == 7
            assert t["name"] and t["number"]
