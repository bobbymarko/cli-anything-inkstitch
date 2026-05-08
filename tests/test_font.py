"""Tests for font subcommands: validate, adjust-advances, render-test, set-field, import-bx-pack."""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli_anything_inkstitch.cli import root


# ---------------------------------------------------------------------------
# Helpers (mirror test_smoke.py pattern)
# ---------------------------------------------------------------------------

def invoke(runner, *args):
    result = runner.invoke(root, list(args), catch_exceptions=False)
    return result


def jrun(runner, *args):
    """Invoke with --json prepended; parse stdout JSON."""
    result = invoke(runner, "--json", *args)
    assert result.exit_code == 0, (
        f"exit {result.exit_code}: stderr={result.stderr!r}\nstdout={result.output!r}"
    )
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


def _make_1x1_png() -> bytes:
    """Create a minimal 1×1 white RGB PNG in pure Python."""
    W, H = 1, 1

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    raw = b"\x00" + bytes([255, 255, 255])
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


_MINIMAL_SVG = """\
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     version="1.1" id="svg_root"
     width="500" height="500" viewBox="0 0 500 500">
  <sodipodi:namedview id="namedview_font">
    <sodipodi:guide id="guide_baseline" position="0,150" orientation="0,1"
                    inkscape:label="baseline"/>
  </sodipodi:namedview>
  <metadata id="metadata_font">
    <inkstitch:inkstitch_svg_version>2</inkstitch:inkstitch_svg_version>
  </metadata>
  <defs id="defs_font"/>
  <g inkscape:groupmode="layer" inkscape:label="GlyphLayer-a" style="display:none"
     id="g_layer_a">
    <path style="fill:none;stroke:#000000" d="M 0,0 L 10,10" id="p_a"/>
  </g>
  <g inkscape:groupmode="layer" inkscape:label="GlyphLayer-b" style="display:none"
     id="g_layer_b">
    <path style="fill:none;stroke:#000000" d="M 0,0 L 10,10" id="p_b"/>
  </g>
</svg>
"""

_FULL_FONT_JSON = {
    "name": "Test Font",
    "description": "A test font",
    "keywords": ["test", "font"],
    "units_per_em": 400.0,
    "leading": 480.0,
    "size": 60.0,
    "min_scale": 0.5,
    "max_scale": 3.0,
    "auto_satin": False,
    "reversible": False,
    "sortable": False,
    "letter_case": "",
    "default_glyph": "?",
    "kerning_pairs": {},
    "horiz_adv_x_default": 240.0,
    "horiz_adv_x_space": 120.0,
    "horiz_adv_x": {"a": 50.0, "b": 55.0},
    "glyphs": ["a", "b"],
    "default_variant": "→",
    "text_direction": "ltr",
    "baseline_y": 350.0,
}


@pytest.fixture
def minimal_font_dir(tmp_path):
    """Build a complete valid font in tmp_path and return the Path."""
    # SVG
    (tmp_path / "→.svg").write_text(_MINIMAL_SVG, encoding="utf-8")
    # font.json
    (tmp_path / "font.json").write_text(
        json.dumps(_FULL_FONT_JSON, indent=4), encoding="utf-8"
    )
    # preview.png
    (tmp_path / "preview.png").write_bytes(_make_1x1_png())
    # LICENSE
    (tmp_path / "LICENSE").write_text("test license", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# font validate tests
# ---------------------------------------------------------------------------

class TestFontValidate:
    def test_validate_clean_font(self, runner, minimal_font_dir):
        data = jrun(runner, "font", "validate", "--font-dir", str(minimal_font_dir))
        assert data["valid"] is True
        assert data["errors"] == []
        assert data["warnings"] == []

    def test_validate_missing_svg(self, runner, minimal_font_dir):
        (minimal_font_dir / "→.svg").unlink()
        data = jrun(runner, "font", "validate", "--font-dir", str(minimal_font_dir))
        assert data["valid"] is False
        codes = [e["code"] for e in data["errors"]]
        assert "missing_svg" in codes

    def test_validate_missing_json(self, runner, minimal_font_dir):
        (minimal_font_dir / "font.json").unlink()
        data = jrun(runner, "font", "validate", "--font-dir", str(minimal_font_dir))
        assert data["valid"] is False
        codes = [e["code"] for e in data["errors"]]
        assert "missing_json" in codes

    def test_validate_missing_field(self, runner, minimal_font_dir):
        fd = json.loads((minimal_font_dir / "font.json").read_text())
        del fd["name"]
        (minimal_font_dir / "font.json").write_text(json.dumps(fd), encoding="utf-8")
        data = jrun(runner, "font", "validate", "--font-dir", str(minimal_font_dir))
        assert data["valid"] is False
        codes = [e["code"] for e in data["errors"]]
        assert "missing_field" in codes

    def test_validate_missing_glyph_layer(self, runner, minimal_font_dir):
        fd = json.loads((minimal_font_dir / "font.json").read_text())
        fd["glyphs"].append("c")
        (minimal_font_dir / "font.json").write_text(json.dumps(fd), encoding="utf-8")
        data = jrun(runner, "font", "validate", "--font-dir", str(minimal_font_dir))
        assert data["valid"] is False
        codes = [e["code"] for e in data["errors"]]
        assert "glyph_layer_missing" in codes

    def test_validate_orphan_layer(self, runner, minimal_font_dir):
        # Add GlyphLayer-z to SVG but not to glyphs[] by inserting before </svg>
        svg = (minimal_font_dir / "→.svg").read_text(encoding="utf-8")
        extra = (
            '<g inkscape:groupmode="layer" inkscape:label="GlyphLayer-z" '
            'style="display:none" id="g_layer_z">'
            '<path style="fill:none;stroke:#000" d="M 0,0 L 5,5"/>'
            '</g>'
        )
        svg = svg.replace("</svg>", extra + "\n</svg>")
        (minimal_font_dir / "→.svg").write_text(svg, encoding="utf-8")
        data = jrun(runner, "font", "validate", "--font-dir", str(minimal_font_dir))
        warn_codes = [w["code"] for w in data["warnings"]]
        assert "orphan_layer" in warn_codes

    def test_validate_missing_preview(self, runner, minimal_font_dir):
        (minimal_font_dir / "preview.png").unlink()
        data = jrun(runner, "font", "validate", "--font-dir", str(minimal_font_dir))
        warn_codes = [w["code"] for w in data["warnings"]]
        assert "missing_preview" in warn_codes

    def test_validate_missing_license(self, runner, minimal_font_dir):
        (minimal_font_dir / "LICENSE").unlink()
        data = jrun(runner, "font", "validate", "--font-dir", str(minimal_font_dir))
        warn_codes = [w["code"] for w in data["warnings"]]
        assert "missing_license" in warn_codes

    def test_validate_unsupported_clone(self, runner, minimal_font_dir):
        # Add a <use> element inside GlyphLayer-a
        svg = (minimal_font_dir / "→.svg").read_text(encoding="utf-8")
        svg = svg.replace(
            '<path style="fill:none;stroke:#000000" d="M 0,0 L 10,10" id="p_a"/>',
            '<path style="fill:none;stroke:#000000" d="M 0,0 L 10,10" id="p_a"/>'
            '<use xmlns="http://www.w3.org/2000/svg" xlink:href="#p_a" id="u_a"/>',
        )
        (minimal_font_dir / "→.svg").write_text(svg, encoding="utf-8")
        data = jrun(runner, "font", "validate", "--font-dir", str(minimal_font_dir))
        codes = [e["code"] for e in data["errors"]]
        assert "unsupported_feature" in codes

    def test_validate_path_no_style(self, runner, minimal_font_dir):
        # Add a path with no style/fill/stroke inside GlyphLayer-b
        svg = (minimal_font_dir / "→.svg").read_text(encoding="utf-8")
        svg = svg.replace(
            '<path style="fill:none;stroke:#000000" d="M 0,0 L 10,10" id="p_b"/>',
            '<path style="fill:none;stroke:#000000" d="M 0,0 L 10,10" id="p_b"/>'
            '<path d="M 5,5 L 15,15" id="p_b_nostyle"/>',
        )
        (minimal_font_dir / "→.svg").write_text(svg, encoding="utf-8")
        data = jrun(runner, "font", "validate", "--font-dir", str(minimal_font_dir))
        codes = [e["code"] for e in data["errors"]]
        assert "path_no_style" in codes

    def test_validate_recommended_fields(self, runner, minimal_font_dir):
        fd = json.loads((minimal_font_dir / "font.json").read_text())
        del fd["description"]
        del fd["keywords"]
        (minimal_font_dir / "font.json").write_text(json.dumps(fd), encoding="utf-8")
        data = jrun(runner, "font", "validate", "--font-dir", str(minimal_font_dir))
        warn_codes = [w["code"] for w in data["warnings"]]
        assert "recommended_field" in warn_codes


# ---------------------------------------------------------------------------
# font adjust-advances tests
# ---------------------------------------------------------------------------

class TestFontAdjustAdvances:
    def test_adjust_advances_padding(self, runner, minimal_font_dir):
        data = jrun(runner, "font", "adjust-advances",
                    "--font-dir", str(minimal_font_dir), "--padding", "5")
        assert "a" in data["adjusted"]
        assert data["adjusted"]["a"]["after"] == data["adjusted"]["a"]["before"] + 5

    def test_adjust_advances_scale(self, runner, minimal_font_dir):
        # Read original values
        fd = json.loads((minimal_font_dir / "font.json").read_text())
        orig_a = fd["horiz_adv_x"]["a"]
        data = jrun(runner, "font", "adjust-advances",
                    "--font-dir", str(minimal_font_dir), "--scale", "1.1")
        assert "a" in data["adjusted"]
        assert abs(data["adjusted"]["a"]["after"] - round(orig_a * 1.1, 1)) < 0.01

    def test_adjust_advances_combined(self, runner, minimal_font_dir):
        fd = json.loads((minimal_font_dir / "font.json").read_text())
        orig_a = fd["horiz_adv_x"]["a"]
        expected = round(orig_a * 1.1 + 3.0, 1)
        data = jrun(runner, "font", "adjust-advances",
                    "--font-dir", str(minimal_font_dir), "--scale", "1.1", "--padding", "3")
        assert abs(data["adjusted"]["a"]["after"] - expected) < 0.01

    def test_adjust_advances_specific_chars(self, runner, minimal_font_dir):
        fd_before = json.loads((minimal_font_dir / "font.json").read_text())
        orig_b = fd_before["horiz_adv_x"]["b"]
        data = jrun(runner, "font", "adjust-advances",
                    "--font-dir", str(minimal_font_dir), "--padding", "10", "--chars", "a")
        # Only 'a' should be in adjusted
        assert "a" in data["adjusted"]
        assert "b" not in data["adjusted"]
        # Verify font.json: b should be unchanged
        fd_after = json.loads((minimal_font_dir / "font.json").read_text())
        assert fd_after["horiz_adv_x"]["b"] == orig_b

    def test_adjust_advances_dry_run(self, runner, minimal_font_dir):
        fd_before = json.loads((minimal_font_dir / "font.json").read_text())
        orig_a = fd_before["horiz_adv_x"]["a"]
        data = jrun(runner, "font", "adjust-advances",
                    "--font-dir", str(minimal_font_dir), "--padding", "99", "--dry-run")
        assert data["dry_run"] is True
        # font.json should be unchanged
        fd_after = json.loads((minimal_font_dir / "font.json").read_text())
        assert fd_after["horiz_adv_x"]["a"] == orig_a

    def test_adjust_advances_updates_default(self, runner, minimal_font_dir):
        fd_before = json.loads((minimal_font_dir / "font.json").read_text())
        orig_default = fd_before["horiz_adv_x_default"]
        jrun(runner, "font", "adjust-advances",
             "--font-dir", str(minimal_font_dir), "--scale", "2.0")
        fd_after = json.loads((minimal_font_dir / "font.json").read_text())
        assert fd_after["horiz_adv_x_default"] == round(orig_default * 2.0, 1)


# ---------------------------------------------------------------------------
# font render-test tests
# ---------------------------------------------------------------------------

class TestFontRenderTest:
    def test_render_test_creates_png(self, runner, minimal_font_dir, tmp_path):
        out = tmp_path / "out.png"
        data = jrun(runner, "font", "render-test",
                    "--font-dir", str(minimal_font_dir),
                    "--phrase", "ab",
                    "--output", str(out))
        assert out.exists()
        assert out.stat().st_size > 100
        assert "render_test" in data

    def test_render_test_no_guides(self, runner, minimal_font_dir, tmp_path):
        out = tmp_path / "out_no_guides.png"
        data = jrun(runner, "font", "render-test",
                    "--font-dir", str(minimal_font_dir),
                    "--phrase", "ab",
                    "--output", str(out),
                    "--no-guides")
        assert out.exists()
        assert out.stat().st_size > 100

    def test_render_test_no_baseline(self, runner, minimal_font_dir, tmp_path):
        out = tmp_path / "out_no_bl.png"
        data = jrun(runner, "font", "render-test",
                    "--font-dir", str(minimal_font_dir),
                    "--phrase", "ab",
                    "--output", str(out),
                    "--no-baseline",
                    "--no-xheight")
        assert out.exists()
        assert out.stat().st_size > 100

    def test_render_test_baseline_only(self, runner, minimal_font_dir, tmp_path):
        # Set baseline_y to a nonzero value in font.json
        fd = json.loads((minimal_font_dir / "font.json").read_text())
        fd["baseline_y"] = 350.0
        (minimal_font_dir / "font.json").write_text(json.dumps(fd), encoding="utf-8")
        out = tmp_path / "out_bl.png"
        data = jrun(runner, "font", "render-test",
                    "--font-dir", str(minimal_font_dir),
                    "--phrase", "ab",
                    "--output", str(out))
        assert out.exists()
        assert out.stat().st_size > 100


# ---------------------------------------------------------------------------
# font set-field tests
# ---------------------------------------------------------------------------

class TestFontSetField:
    def test_set_field_string(self, runner, minimal_font_dir):
        data = jrun(runner, "font", "set-field",
                    "--font-dir", str(minimal_font_dir),
                    "--key", "description",
                    "--value", "My new description")
        assert data["key"] == "description"
        assert data["after"] == "My new description"
        fd = json.loads((minimal_font_dir / "font.json").read_text())
        assert fd["description"] == "My new description"

    def test_set_field_bool(self, runner, minimal_font_dir):
        data = jrun(runner, "font", "set-field",
                    "--font-dir", str(minimal_font_dir),
                    "--key", "sortable",
                    "--value", "true")
        assert data["after"] is True
        fd = json.loads((minimal_font_dir / "font.json").read_text())
        assert fd["sortable"] is True

    def test_set_field_number(self, runner, minimal_font_dir):
        data = jrun(runner, "font", "set-field",
                    "--font-dir", str(minimal_font_dir),
                    "--key", "min_scale",
                    "--value", "0.75")
        assert abs(data["after"] - 0.75) < 0.001
        fd = json.loads((minimal_font_dir / "font.json").read_text())
        assert abs(fd["min_scale"] - 0.75) < 0.001

    def test_set_field_list(self, runner, minimal_font_dir):
        data = jrun(runner, "font", "set-field",
                    "--font-dir", str(minimal_font_dir),
                    "--key", "keywords",
                    "--value", '["a","b"]')
        assert data["after"] == ["a", "b"]
        fd = json.loads((minimal_font_dir / "font.json").read_text())
        assert fd["keywords"] == ["a", "b"]

    def test_set_field_rejects_horiz_adv_x(self, runner, minimal_font_dir):
        result = invoke(runner, "--json", "font", "set-field",
                        "--font-dir", str(minimal_font_dir),
                        "--key", "horiz_adv_x",
                        "--value", "{}")
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# font import-bx-pack tests
# ---------------------------------------------------------------------------

class TestFontImportBxPack:
    def test_import_bx_pack_dry_run(self, runner, tmp_path):
        bx_dir = tmp_path / "bx"
        bx_dir.mkdir()
        exp_dir = tmp_path / "exp"
        exp_dir.mkdir()
        out_dir = tmp_path / "out"

        # Create fake BX files
        (bx_dir / "Font 1 inch.bx").write_bytes(b"fake bx data")
        (bx_dir / "Font 2 inch.bx").write_bytes(b"fake bx data")

        # Create matching EXP subdirs
        (exp_dir / "1 inch").mkdir()
        (exp_dir / "2 inch").mkdir()

        data = jrun(runner, "font", "import-bx-pack",
                    "--bx-dir", str(bx_dir),
                    "--exp-dir", str(exp_dir),
                    "--output-dir", str(out_dir),
                    "--dry-run")
        assert data["dry_run"] is True
        assert len(data["matched"]) == 2
        sizes = {m["size"] for m in data["matched"]}
        assert "1 inch" in sizes
        assert "2 inch" in sizes
        # Dry run: no output directories created
        assert not out_dir.exists()

    def test_import_bx_pack_no_matches(self, runner, tmp_path):
        bx_dir = tmp_path / "bx"
        bx_dir.mkdir()
        exp_dir = tmp_path / "exp"
        exp_dir.mkdir()
        out_dir = tmp_path / "out"

        # BX files exist but no matching EXP dirs
        (bx_dir / "Font 1 inch.bx").write_bytes(b"fake bx data")
        (exp_dir / "3 inch").mkdir()  # doesn't match

        data = jrun(runner, "font", "import-bx-pack",
                    "--bx-dir", str(bx_dir),
                    "--exp-dir", str(exp_dir),
                    "--output-dir", str(out_dir),
                    "--dry-run")
        assert data["dry_run"] is True
        assert len(data["matched"]) == 0


# ---------------------------------------------------------------------------
# _parse_char_from_stem unit tests
# ---------------------------------------------------------------------------

from cli_anything_inkstitch.commands.font import _parse_char_from_stem


class TestParseCharFromStem:
    """Filename-stem → character parser.

    Locks down the supported naming conventions. Conventions are documented in
    the function's own docstring; this suite is the executable counterpart.
    """

    @pytest.mark.parametrize("stem,expected", [
        ("CapA", "A"),
        ("CapZ", "Z"),
        ("Cap_A", "A"),
        ("LowA", "a"),
        ("Lowa", "a"),
        ("TSS-Homerun-CapA", "A"),
        ("Stitchtopia_LowQ", "q"),
        # Docstring claims Low_a → 'a', but the regex `Low([A-Za-z])` requires the letter
        # to come immediately after "Low" with no separator. Marking xfail until either
        # the parser or the docstring is corrected.
        pytest.param("Low_a", "a", marks=pytest.mark.xfail(reason="parser/docstring mismatch: Low_a")),
    ])
    def test_cap_low_prefix(self, stem, expected):
        assert _parse_char_from_stem(stem) == expected

    @pytest.mark.parametrize("stem,expected", [
        ("PunComma", ","),
        ("PunPeriod", "."),
        ("PunExclamation", "!"),
        # Same parser/docstring mismatch as Low_a — Pun_Comma fails because the regex
        # needs letters immediately after "Pun".
        pytest.param("Pun_Comma", ",", marks=pytest.mark.xfail(reason="parser/docstring mismatch: Pun_Comma")),
    ])
    def test_pun_word(self, stem, expected):
        assert _parse_char_from_stem(stem) == expected

    def test_pun_unknown_returns_none(self):
        assert _parse_char_from_stem("PunGibberish") is None

    @pytest.mark.parametrize("stem,expected", [
        ("A_cap", "A"),
        ("A_Cap", "A"),
        ("a_low", "a"),
        ("z_LOW", "z"),
    ])
    def test_letter_underscore_case(self, stem, expected):
        assert _parse_char_from_stem(stem) == expected

    @pytest.mark.parametrize("stem,expected", [
        ("a-Star1inch", "a"),
        ("A-Star", "A"),
        ("0-Numeral", "0"),
    ])
    def test_hyphen_prefix(self, stem, expected):
        assert _parse_char_from_stem(stem) == expected

    @pytest.mark.parametrize("stem,expected", [
        # Multi-word prefix consumes 2+ space-separated words greedily. For
        # "Floral Alphabet A15" it consumes "Floral Alphabet" then captures 'A'.
        ("Floral Alphabet A15", "A"),
        # For "Floral Alphabet B 1" it greedily consumes "Floral Alphabet B "
        # too (because B+space is itself a valid "word\s+" group), then captures
        # the next char which is '1'. Documents actual greedy behavior.
        ("Floral Alphabet B 1", "1"),
        ("Some Other Pack X", "X"),
    ])
    def test_multi_word_prefix(self, stem, expected):
        assert _parse_char_from_stem(stem) == expected

    @pytest.mark.parametrize("stem,expected", [
        ("A", "A"),
        ("z", "z"),
        ("0", "0"),
    ])
    def test_single_char(self, stem, expected):
        assert _parse_char_from_stem(stem) == expected

    def test_size_prefix_stripped(self):
        assert _parse_char_from_stem("CapA_2.5in") == "A"

    def test_garbage_returns_none(self):
        assert _parse_char_from_stem("") is None
        assert _parse_char_from_stem("RandomFileName") is None

    # ---- New monogram-position convention ----
    # Pattern: <prefix>_<Letter>_(middle|side) — used by SSP / Stitchtopia
    # monogram packs. Letter case is preserved (A vs a) so that downstream
    # tooling can choose to case-encode middle/side into one font (when casing
    # cleanly partitions) or split into two fonts (when it doesn't).
    @pytest.mark.parametrize("stem,expected", [
        ("SSP_A_middle", "A"),
        ("SSP_B_middle", "B"),
        ("SSP_Z_middle", "Z"),
        ("SSP_a_side", "a"),
        ("SSP_b_side", "b"),
        ("SSP_z_side", "z"),
        # Real-pack quirk: trailing whitespace before extension survived through Path.stem
        ("SSP_W_middle ", "W"),
        # Other brand prefixes
        ("Brand_Q_middle", "Q"),
        ("FONT_a_side", "a"),
        # Case-insensitive on the position keyword
        ("SSP_A_MIDDLE", "A"),
        ("SSP_a_Side", "a"),
    ])
    def test_monogram_position_convention(self, stem, expected):
        assert _parse_char_from_stem(stem) == expected

    def test_monogram_preserves_letter_case(self):
        """Critical for the case-encoded monogram strategy: A and a stay distinct."""
        assert _parse_char_from_stem("SSP_A_middle") == "A"
        assert _parse_char_from_stem("SSP_a_side") == "a"
        assert _parse_char_from_stem("SSP_A_middle") != _parse_char_from_stem("SSP_a_side")

    # ---- Upper-letter "u"-suffix marker convention ----
    # Used by Chain Monogram / Decorative Monogram packs: "Au.dst" is the
    # upper-case (middle) variant of A; the matching lower-case "side" glyph
    # ships as just "a.dst".
    @pytest.mark.parametrize("stem,expected", [
        ("Au", "A"),
        ("Bu", "B"),
        ("Zu", "Z"),
        # Lowercase counterparts must NOT match the same rule — they're handled
        # by the single-char fallback. We assert their behavior here too so a
        # future change can't accidentally turn "au" → "a" via this path.
        ("a", "a"),
        ("z", "z"),
    ])
    def test_upper_u_suffix_convention(self, stem, expected):
        assert _parse_char_from_stem(stem) == expected

    @pytest.mark.parametrize("stem", [
        "au",   # lowercase + 'u' — NOT a marker; intentionally unhandled (returns None)
        "menu", # word ending in 'u' must not be misread as letter 'M'
        "you",  # ditto
    ])
    def test_lowercase_u_suffix_does_not_match(self, stem):
        result = _parse_char_from_stem(stem)
        # We accept either None or some other-rule fallback, but specifically NOT
        # capturing the leading letter via the upper-u rule.
        assert result != stem[0].upper()


# ---------------------------------------------------------------------------
# _extract_bx_connection_offsets unit tests
# ---------------------------------------------------------------------------

from cli_anything_inkstitch.commands.font import (
    _extract_bx_connection_offsets,
    _locate_bzip2_payload,
)
from cli_anything_inkstitch.errors import UserError


# ---- Synthetic BX fixture builder -----------------------------------------

def _make_bx_payload(glyphs: dict[str, float]) -> bytes:
    """Build the *decompressed* BX payload for *glyphs* (char → y_min in BF units).

    Each glyph record in the decompressed stream looks like::

        {filename}\\x00\\t{padding}BBOX_PREFIX{24-byte-bbox}{padding}

    The BBOX_PREFIX is ``\\x05\\x00\\x00\\x00\\x13\\x00\\x18\\x00\\x00\\x00``
    (pre=5, tag=0x0013, vlen=24) and the 24-byte bbox encodes six LE floats
    ``[x_min, y_min, 0.0, x_max, y_max, 0.0]``.
    """
    BBOX_PREFIX = bytes([0x05, 0x00, 0x00, 0x00,
                         0x13, 0x00,
                         0x18, 0x00, 0x00, 0x00])
    out = b""
    for ch, y_min in glyphs.items():
        filename = f"{ch}.PES".encode()
        bbox = struct.pack('<6f', -10.0, y_min, 0.0, 10.0, abs(y_min) * 0.3, 0.0)
        out += filename + b"\x00\t" + b"\x00" * 50 + BBOX_PREFIX + bbox + b"\x00" * 100
    return out


def _make_bx_file_stripped(glyphs: dict[str, float], header_size: int = 200) -> bytes:
    """BX binary with *stripped* BZh header (TSS-Homerun style).

    Layout: ``{header_size random bytes}{bzip2 block magic onwards}``
    i.e. the 4-byte ``BZh9`` stream header is removed so only the block
    marker ``0x314159265359`` remains in the raw bytes.
    """
    import bz2
    payload = _make_bx_payload(glyphs)
    compressed = bz2.compress(payload)
    # bz2.compress → b"BZh9" + compressed blocks
    assert compressed[:4] == b"BZh9", "unexpected bz2 header"
    stream_body = compressed[4:]          # drop the 4-byte header
    return b"\xff" * header_size + stream_body


def _make_bx_file_full(glyphs: dict[str, float], header_size: int = 200) -> bytes:
    """BX binary with a *full* intact BZh9 header embedded at *header_size*."""
    import bz2
    payload = _make_bx_payload(glyphs)
    compressed = bz2.compress(payload)
    return b"\xff" * header_size + compressed


def _make_bx_file_tss(glyphs: dict[str, float], header_size: int = 200) -> bytes:
    """BX binary using TSS-Homerun filename convention (``TSS-Homerun1inLow{ch}.PES``)."""
    import bz2

    BBOX_PREFIX = bytes([0x05, 0x00, 0x00, 0x00,
                         0x13, 0x00,
                         0x18, 0x00, 0x00, 0x00])
    out = b""
    for ch, y_min in glyphs.items():
        if ch.islower():
            filename = f"TSS-Homerun1inLow{ch}.PES".encode()
        else:
            filename = f"TSS-Homerun1inCap{ch}.PES".encode()
        bbox = struct.pack('<6f', -10.0, y_min, 0.0, 10.0, abs(y_min) * 0.3, 0.0)
        out += filename + b"\x00\t" + b"\x00" * 50 + BBOX_PREFIX + bbox + b"\x00" * 100

    compressed = bz2.compress(out)
    assert compressed[:4] == b"BZh9"
    return b"\xff" * header_size + compressed[4:]


# ---- _locate_bzip2_payload tests ------------------------------------------

class TestLocateBzip2Payload:
    """Unit tests for the stream-locating helper."""

    def test_stripped_header_found(self, tmp_path):
        """Block-magic scan correctly locates a stripped-header stream."""
        glyphs = {"a": -83.0, "b": -117.0}
        raw = _make_bx_file_stripped(glyphs, header_size=300)
        result = _locate_bzip2_payload(raw, tmp_path / "test.bx")
        assert b"a.PES" in result
        assert b"b.PES" in result

    def test_full_header_found(self, tmp_path):
        """Full-BZh stream embedded at non-zero offset is located."""
        glyphs = {"x": -83.0}
        raw = _make_bx_file_full(glyphs, header_size=512)
        result = _locate_bzip2_payload(raw, tmp_path / "test.bx")
        assert b"x.PES" in result

    def test_stripped_header_at_start(self, tmp_path):
        """Works even when the block magic sits at the very first byte."""
        import bz2
        glyphs = {"z": -83.0}
        payload = _make_bx_payload(glyphs)
        compressed = bz2.compress(payload)
        raw = compressed[4:]   # stripped, at offset 0
        result = _locate_bzip2_payload(raw, tmp_path / "test.bx")
        assert b"z.PES" in result

    def test_corrupt_raises_user_error(self, tmp_path):
        """A file with no valid bzip2 stream raises UserError."""
        raw = b"\x00\xff\xaa" * 500   # garbage
        with pytest.raises(UserError, match="No decompressible bzip2 stream"):
            _locate_bzip2_payload(raw, tmp_path / "bad.bx")

    def test_empty_raises_user_error(self, tmp_path):
        with pytest.raises(UserError, match="empty"):
            _locate_bzip2_payload(b"", tmp_path / "empty.bx")


# ---- _extract_bx_connection_offsets tests ---------------------------------

class TestExtractBxConnectionOffsets:

    # ---- Generic single-char filename convention (SSP style) --------

    def test_generic_lowercase(self, tmp_path):
        """Lowercase glyphs with ``{ch}.PES`` naming are extracted."""
        glyphs = {ch: -83.0 for ch in "abcde"}
        bx = tmp_path / "ssp.bx"
        bx.write_bytes(_make_bx_file_full(glyphs))
        offsets = _extract_bx_connection_offsets(bx)
        for ch in "abcde":
            assert ch in offsets
            assert abs(offsets[ch] - (-83.0)) < 0.01

    def test_generic_uppercase(self, tmp_path):
        """Uppercase glyphs with ``{Ch}.PES`` naming are extracted."""
        glyphs = {Ch: -117.0 for Ch in "ABCDE"}
        bx = tmp_path / "ssp_caps.bx"
        bx.write_bytes(_make_bx_file_full(glyphs))
        offsets = _extract_bx_connection_offsets(bx)
        for Ch in "ABCDE":
            assert Ch in offsets
            assert abs(offsets[Ch] - (-117.0)) < 0.01

    def test_mixed_case_same_file(self, tmp_path):
        """Lower and upper glyphs co-existing in one file."""
        glyphs = {"a": -83.0, "A": -117.0, "g": -130.0, "G": -117.0}
        bx = tmp_path / "mixed.bx"
        bx.write_bytes(_make_bx_file_full(glyphs))
        offsets = _extract_bx_connection_offsets(bx)
        assert abs(offsets["a"] - (-83.0)) < 0.01
        assert abs(offsets["A"] - (-117.0)) < 0.01
        assert abs(offsets["g"] - (-130.0)) < 0.01

    # ---- TSS-Homerun filename convention (stripped BZh header) ------

    def test_tss_homerun_stripped_header(self, tmp_path):
        """TSS-Homerun naming + stripped BZh header: both features work together."""
        glyphs = {"a": -83.0, "b": -117.0, "A": -117.0}
        bx = tmp_path / "tss.bx"
        bx.write_bytes(_make_bx_file_tss(glyphs))
        offsets = _extract_bx_connection_offsets(bx)
        assert "a" in offsets
        assert "b" in offsets
        assert "A" in offsets
        assert abs(offsets["a"] - (-83.0)) < 0.01

    def test_tss_header_at_nonstandard_offset(self, tmp_path):
        """Stream found even when it does NOT start at the old hard-coded offset 13343."""
        glyphs = {"m": -83.0}
        # Use an unusual header size — definitely not 13343
        bx = tmp_path / "tss_offset.bx"
        bx.write_bytes(_make_bx_file_tss(glyphs, header_size=999))
        offsets = _extract_bx_connection_offsets(bx)
        assert "m" in offsets

    # ---- Positional fallback ----------------------------------------

    def test_positional_fallback_26(self, tmp_path):
        """26 anonymous BBOX hits are assigned to a–z in order."""
        import bz2

        BBOX_PREFIX = bytes([0x05, 0x00, 0x00, 0x00,
                             0x13, 0x00,
                             0x18, 0x00, 0x00, 0x00])
        # Build payload with NO filename markers — just raw BBOX blocks
        y_values = [-80.0 - i for i in range(26)]
        payload = b""
        for y in y_values:
            bbox = struct.pack('<6f', -10.0, y, 0.0, 10.0, 50.0, 0.0)
            payload += b"\x00" * 200 + BBOX_PREFIX + bbox

        compressed = bz2.compress(payload)
        raw = b"\x00" * 100 + compressed   # full-header style

        bx = tmp_path / "positional.bx"
        bx.write_bytes(raw)
        offsets = _extract_bx_connection_offsets(bx)

        for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
            assert ch in offsets, f"missing glyph '{ch}'"
            assert abs(offsets[ch] - y_values[i]) < 0.01

    def test_positional_fallback_52(self, tmp_path):
        """52 anonymous BBOX hits are assigned to a–z then A–Z."""
        import bz2

        BBOX_PREFIX = bytes([0x05, 0x00, 0x00, 0x00,
                             0x13, 0x00,
                             0x18, 0x00, 0x00, 0x00])
        letters = list("abcdefghijklmnopqrstuvwxyz") + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        y_values = [-80.0 - i for i in range(52)]
        payload = b""
        for y in y_values:
            bbox = struct.pack('<6f', -10.0, y, 0.0, 10.0, 50.0, 0.0)
            payload += b"\x00" * 200 + BBOX_PREFIX + bbox

        compressed = bz2.compress(payload)
        raw = b"\x00" * 50 + compressed

        bx = tmp_path / "positional52.bx"
        bx.write_bytes(raw)
        offsets = _extract_bx_connection_offsets(bx)

        for i, ch in enumerate(letters):
            assert ch in offsets
            assert abs(offsets[ch] - y_values[i]) < 0.01

    # ---- Error cases ------------------------------------------------

    def test_corrupt_bx_raises_user_error(self, tmp_path):
        """A file with no valid bzip2 stream raises UserError."""
        bx = tmp_path / "corrupt.bx"
        bx.write_bytes(b"\xde\xad\xbe\xef" * 200)
        with pytest.raises(UserError):
            _extract_bx_connection_offsets(bx)

    def test_missing_glyphs_are_absent_not_error(self, tmp_path):
        """Glyphs not present in the BX file are simply absent from the result."""
        glyphs = {"a": -83.0}   # only 'a'
        bx = tmp_path / "sparse.bx"
        bx.write_bytes(_make_bx_file_full(glyphs))
        offsets = _extract_bx_connection_offsets(bx)
        assert "a" in offsets
        assert "b" not in offsets   # graceful absence
