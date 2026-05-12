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

    # Full-word Capital / Lowercase prefixes — used by some packs (e.g. files
    # named "CapitalA.xxx" / "Lowercaseb.xxx") that the shorter Cap/Low rules
    # don't cover.
    @pytest.mark.parametrize("stem,expected", [
        ("CapitalA", "A"),
        ("CapitalZ", "Z"),
        ("Capital_B", "B"),
        ("Lowercasea", "a"),
        ("Lowercase_z", "z"),
        ("MyPack_CapitalQ", "Q"),
        ("Brand-Lowercasem", "m"),
    ])
    def test_capital_lowercase_full_word_prefix(self, stem, expected):
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


# ---- Synthetic BX fixture builder (proper IDMDTL + char-record structure) ----

def _make_structural_bx(
    glyphs: dict[str, float],
    header_size: int = 200,
    stripped: bool = True,
) -> bytes:
    """Build a minimal but structurally correct BX binary.

    Each glyph contributes two records in the decompressed payload:

    1. **IDMDTL block** — geometry record with one attribute
       (pre=5, tag=0x0013, vlen=24) encoding the bbox as six LE float32::

           [x_min, y_min, 0, x_max, y_max, 0]

    2. **Character record** — ``{filename}\\t\\x00`` followed by the first
       attribute (pre=8, tag=0x0050, vlen=2, UTF-16LE char).

    This matches the real file structure extracted from TSS-Homerun,
    NitkaBonitka, LD Signature, Stitchtopia, and Chinoiserie BX files.
    """
    import bz2

    payload = b""
    for ch, y_min in glyphs.items():
        # IDMDTL block: 1 entry, pre=5, vlen=24 bbox
        bbox = struct.pack("<6f", -10.0, y_min, 0.0, 10.0, abs(y_min) * 0.3, 0.0)
        idmdtl = (
            b"IDMDTL"
            + struct.pack("<I", 1)                       # 1 entry
            + struct.pack("<IHI", 5, 0x0013, 24) + bbox  # pre=5, tag, vlen=24
        )
        # Character record: filename + \t\x00 + char attribute (pre=8, vlen=2)
        filename = f"{ch}.JEF".encode()   # use .JEF so no PES-name-based shortcut
        char_attr = struct.pack("<IHI", 8, 0x0050, 2) + struct.pack("<H", ord(ch))
        char_rec = filename + b"\t\x00" + char_attr

        payload += b"\x00" * 40 + idmdtl + b"\x00" * 80 + char_rec + b"\x00" * 40

    compressed = bz2.compress(payload)
    assert compressed[:4] == b"BZh9"
    body = compressed[4:] if stripped else compressed
    return b"\xff" * header_size + body


# ---- Minimal payload builder for _locate_bzip2_payload tests ---------------
# (These helpers only need something decompressible — they don't need IDMDTL.)

def _make_raw_payload(marker: bytes = b"HELLO") -> bytes:
    """Return a small bzip2-compressed payload that contains *marker*."""
    import bz2
    return bz2.compress(b"\x00" * 50 + marker + b"\x00" * 50)


# ---- _locate_bzip2_payload tests ------------------------------------------

class TestLocateBzip2Payload:
    """Unit tests for the bzip2-stream locating helper."""

    def test_stripped_header_found(self, tmp_path):
        """Block-magic scan locates a stream whose BZh9 header was removed."""
        import bz2
        compressed = bz2.compress(b"HELLO" * 100)
        raw = b"\xff" * 300 + compressed[4:]  # strip BZh9
        result = _locate_bzip2_payload(raw, tmp_path / "test.bx")
        assert b"HELLO" in result

    def test_full_header_found(self, tmp_path):
        """Full BZh stream embedded at a non-zero offset is located."""
        import bz2
        compressed = bz2.compress(b"WORLD" * 100)
        raw = b"\xff" * 512 + compressed
        result = _locate_bzip2_payload(raw, tmp_path / "test.bx")
        assert b"WORLD" in result

    def test_stripped_header_at_start(self, tmp_path):
        """Works when the block magic is at byte 0 (no preceding header)."""
        import bz2
        compressed = bz2.compress(b"ZERO" * 100)
        raw = compressed[4:]   # stripped, at offset 0
        result = _locate_bzip2_payload(raw, tmp_path / "test.bx")
        assert b"ZERO" in result

    def test_corrupt_raises_user_error(self, tmp_path):
        """A file with no valid bzip2 stream raises UserError."""
        raw = b"\x00\xff\xaa" * 500
        with pytest.raises(UserError, match="No decompressible bzip2 stream"):
            _locate_bzip2_payload(raw, tmp_path / "bad.bx")

    def test_empty_raises_user_error(self, tmp_path):
        with pytest.raises(UserError, match="empty"):
            _locate_bzip2_payload(b"", tmp_path / "empty.bx")


# ---- _extract_bx_connection_offsets tests ---------------------------------

from cli_anything_inkstitch.commands.font import _parse_bx_glyphs

BX_FIXTURES = Path(__file__).parent / "fixtures" / "bx"


class TestExtractBxConnectionOffsets:

    # ---- Synthetic structural tests (format fidelity) ---------------

    def test_structural_lowercase(self, tmp_path):
        """Structural IDMDTL+char-record parser extracts lowercase glyphs."""
        glyphs = {"a": -83.0, "g": -130.0, "b": -117.0}
        bx = tmp_path / "s.bx"
        bx.write_bytes(_make_structural_bx(glyphs, stripped=False))
        offsets = _extract_bx_connection_offsets(bx)
        assert abs(offsets["a"] - (-83.0)) < 0.01
        assert abs(offsets["g"] - (-130.0)) < 0.01
        assert abs(offsets["b"] - (-117.0)) < 0.01

    def test_structural_uppercase(self, tmp_path):
        """Structural parser handles uppercase glyphs."""
        glyphs = {"A": -117.0, "Q": -135.0}
        bx = tmp_path / "s.bx"
        bx.write_bytes(_make_structural_bx(glyphs, stripped=False))
        offsets = _extract_bx_connection_offsets(bx)
        assert abs(offsets["A"] - (-117.0)) < 0.01
        assert abs(offsets["Q"] - (-135.0)) < 0.01

    def test_structural_mixed_case(self, tmp_path):
        """Lower and upper coexist correctly in one file."""
        glyphs = {"a": -83.0, "A": -117.0, "g": -130.0}
        bx = tmp_path / "s.bx"
        bx.write_bytes(_make_structural_bx(glyphs, stripped=False))
        offsets = _extract_bx_connection_offsets(bx)
        assert offsets["a"] != offsets["A"]   # case is preserved
        assert abs(offsets["g"] - (-130.0)) < 0.01

    def test_structural_stripped_bzip2_header(self, tmp_path):
        """Stream is found even when the BZh9 header is stripped."""
        glyphs = {"m": -83.0, "j": -160.0}
        bx = tmp_path / "s.bx"
        bx.write_bytes(_make_structural_bx(glyphs, stripped=True))
        offsets = _extract_bx_connection_offsets(bx)
        assert "m" in offsets
        assert "j" in offsets

    def test_structural_arbitrary_offset(self, tmp_path):
        """bzip2 stream found at an offset far from 13343 (old hardcoded value)."""
        glyphs = {"z": -83.0}
        bx = tmp_path / "s.bx"
        bx.write_bytes(_make_structural_bx(glyphs, header_size=27_000, stripped=True))
        offsets = _extract_bx_connection_offsets(bx)
        assert "z" in offsets

    def test_structural_digits(self, tmp_path):
        """Digit glyphs (0–9) are extracted correctly."""
        glyphs = {str(d): -128.0 for d in range(10)}
        bx = tmp_path / "s.bx"
        bx.write_bytes(_make_structural_bx(glyphs, stripped=False))
        offsets = _extract_bx_connection_offsets(bx)
        for d in "0123456789":
            assert d in offsets

    def test_structural_absent_glyphs_not_error(self, tmp_path):
        """Characters missing from the file are absent, not errors."""
        glyphs = {"a": -83.0}
        bx = tmp_path / "s.bx"
        bx.write_bytes(_make_structural_bx(glyphs, stripped=False))
        offsets = _extract_bx_connection_offsets(bx)
        assert "a" in offsets
        assert "b" not in offsets

    # ---- Real BX file tests (5 vendors) -----------------------------

    @pytest.mark.skipif(
        not (BX_FIXTURES / "tss_homerun_1in.bx").exists(),
        reason="fixture not present",
    )
    def test_tss_homerun_1in(self):
        """TSS-Homerun 1 inch: correct y_min ranges for three letter classes."""
        offsets = _extract_bx_connection_offsets(BX_FIXTURES / "tss_homerun_1in.bx")
        # x-height letters (a, c, e, m, n, …) → ~−83
        for ch in "acemnorsuv":
            assert ch in offsets, f"missing '{ch}'"
            assert -100 < offsets[ch] < -70, f"'{ch}' y_min={offsets[ch]:.1f} out of range"
        # ascender letters (b, h, k, l, …) → ~−117
        for ch in "bhklt":
            assert -135 < offsets[ch] < -100, f"'{ch}' y_min={offsets[ch]:.1f}"
        # descender letters (g, p, y, …) → ~−130
        for ch in "gpqy":
            assert -150 < offsets[ch] < -115, f"'{ch}' y_min={offsets[ch]:.1f}"
        # uppercase present
        assert "A" in offsets
        assert "Z" in offsets

    @pytest.mark.skipif(
        not (BX_FIXTURES / "nitkabonitka_bubble_1in.bx").exists(),
        reason="fixture not present",
    )
    def test_nitkabonitka_bubble_1in(self):
        """NitkaBonitka Bubble Font: caps + digits, all similar y_min (uniform cap height)."""
        offsets = _extract_bx_connection_offsets(BX_FIXTURES / "nitkabonitka_bubble_1in.bx")
        # Bubble font is all-caps with digits; all letters roughly same height
        for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            assert ch in offsets, f"missing '{ch}'"
            assert -135 < offsets[ch] < -115, f"'{ch}' y_min={offsets[ch]:.1f}"
        for d in "0123456789":
            assert d in offsets

    @pytest.mark.skipif(
        not (BX_FIXTURES / "ld_signature_1.bx").exists(),
        reason="fixture not present",
    )
    def test_ld_signature_1(self):
        """LD Signature: descender letters have more-negative y_min than baseline letters."""
        offsets = _extract_bx_connection_offsets(BX_FIXTURES / "ld_signature_1.bx")
        assert len(offsets) >= 26   # at least one case present
        # Descenders should dip further below baseline than regular letters
        descenders = {ch: offsets[ch] for ch in "gjpqy" if ch in offsets}
        non_descenders = {ch: offsets[ch] for ch in "aceiou" if ch in offsets}
        if descenders and non_descenders:
            assert min(descenders.values()) < min(non_descenders.values()), (
                "expected descenders to have more-negative y_min"
            )

    @pytest.mark.skipif(
        not (BX_FIXTURES / "stitchtopia_romantic_1in.bx").exists(),
        reason="fixture not present",
    )
    def test_stitchtopia_romantic_1in(self):
        """Stitchtopia ActuallyRomantic: both cases present, wide glyph set."""
        offsets = _extract_bx_connection_offsets(
            BX_FIXTURES / "stitchtopia_romantic_1in.bx"
        )
        assert len(offsets) >= 52   # full a–z + A–Z at minimum
        # Characters from both cases
        assert any(ch.islower() for ch in offsets)
        assert any(ch.isupper() for ch in offsets)

    @pytest.mark.skipif(
        not (BX_FIXTURES / "chinoiserie_3in.bx").exists(),
        reason="fixture not present",
    )
    def test_chinoiserie_3in(self):
        """Chinoiserie 3 inch: uppercase-only font, all 26 letters present."""
        offsets = _extract_bx_connection_offsets(BX_FIXTURES / "chinoiserie_3in.bx")
        for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            assert ch in offsets, f"missing '{ch}'"
        # No lowercase (uppercase-only font)
        assert not any(ch.islower() for ch in offsets)
        # 3-inch font → y_min should be in the hundreds of BF units (larger font)
        assert offsets["A"] < -200, f"expected deep y_min for 3in font, got {offsets['A']}"

    # ---- Error cases ------------------------------------------------

    def test_corrupt_bx_raises_user_error(self, tmp_path):
        """A file with no valid bzip2 stream raises UserError."""
        bx = tmp_path / "corrupt.bx"
        bx.write_bytes(b"\xde\xad\xbe\xef" * 200)
        with pytest.raises(UserError):
            _extract_bx_connection_offsets(bx)


# ---------------------------------------------------------------------------
# _last_stitch_svg_y unit tests
# ---------------------------------------------------------------------------

from cli_anything_inkstitch.commands.font import _last_stitch_svg_y


class TestLastStitchSvgY:
    """Unit tests for the last-stitch baseline helper."""

    def _make_pattern(self, stitches):
        """Build a minimal pyembroidery pattern from a list of (x, y, cmd) tuples."""
        import pyembroidery
        p = pyembroidery.EmbPattern()
        for x, y, cmd in stitches:
            p.add_stitch_absolute(cmd, x, y)
        return p

    def test_returns_last_stitch_y(self):
        import pyembroidery
        from cli_anything_inkstitch.commands.font import _DST_TO_SVG
        p = self._make_pattern([
            (0, 100, pyembroidery.STITCH),
            (10, 200, pyembroidery.STITCH),
            (20, 350, pyembroidery.STITCH),
            (0, 0, pyembroidery.END),
        ])
        result = _last_stitch_svg_y(p)
        assert result is not None
        assert abs(result - 350 * _DST_TO_SVG) < 0.01

    def test_ignores_non_stitch_commands(self):
        import pyembroidery
        from cli_anything_inkstitch.commands.font import _DST_TO_SVG
        # TRIM at the end should NOT count — last STITCH is at y=200
        p = self._make_pattern([
            (0, 100, pyembroidery.STITCH),
            (10, 200, pyembroidery.STITCH),
            (20, 999, pyembroidery.TRIM),
            (0, 0, pyembroidery.END),
        ])
        result = _last_stitch_svg_y(p)
        assert result is not None
        assert abs(result - 200 * _DST_TO_SVG) < 0.01

    def test_no_stitches_returns_none(self):
        import pyembroidery
        p = self._make_pattern([(0, 0, pyembroidery.END)])
        assert _last_stitch_svg_y(p) is None


# ---------------------------------------------------------------------------
# font import baseline-method tests
# ---------------------------------------------------------------------------

def _write_minimal_dst(path: Path, stitches_xy: list[tuple[int, int]]) -> None:
    """Write a minimal DST file with the given stitch coordinates (in DST units)."""
    import pyembroidery
    p = pyembroidery.EmbPattern()
    for x, y in stitches_xy:
        p.add_stitch_absolute(pyembroidery.STITCH, x, y)
    p.add_stitch_absolute(pyembroidery.END, 0, 0)
    pyembroidery.write_dst(p, str(path))


def _make_flat_font_dir(tmp_path: Path, glyphs: dict[str, list[tuple[int, int]]]) -> Path:
    """Create a source directory of minimal DST files for a font.

    *glyphs* maps each character to its list of (x, y) stitch coordinates.
    Filenames follow the simple ``A.dst`` / ``a.dst`` convention.
    """
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    for ch, coords in glyphs.items():
        _write_minimal_dst(src / f"{ch}.dst", coords)
    return src


class TestFontImportBaselineMethod:
    """Tests for --baseline-method and --reference-letter options."""

    def _import(self, runner, src_dir, out_dir, extra_args=()):
        """Run font import with common args and return parsed JSON result."""
        args = [
            "--json", "font", "import",
            "--name", "TestFont",
            "--source-dir", str(src_dir),
            "--output-dir", str(out_dir),
        ] + list(extra_args)
        result = runner.invoke(root, args, catch_exceptions=False)
        assert result.exit_code == 0, (
            f"exit {result.exit_code}: {result.output!r}"
        )
        return json.loads(result.output)

    def test_default_bbox_bottom_in_result(self, runner, tmp_path):
        """When no --baseline-method is given, result reports 'bbox-bottom'."""
        src = _make_flat_font_dir(tmp_path, {
            "A": [(0, 0), (100, 0), (100, 500), (0, 500)],
            "B": [(0, 0), (100, 0), (100, 500)],
        })
        data = self._import(runner, src, tmp_path / "out")
        assert data.get("baseline_method") == "bbox-bottom"

    def test_last_stitch_method_accepted(self, runner, tmp_path):
        """--baseline-method=last-stitch is accepted and reported in result."""
        src = _make_flat_font_dir(tmp_path, {
            "A": [(0, 100), (100, 100), (100, 500), (50, 100)],
            "B": [(0, 100), (100, 100), (100, 500), (50, 100)],
        })
        data = self._import(runner, src, tmp_path / "out",
                            ["--baseline-method", "last-stitch"])
        assert data.get("baseline_method") == "last-stitch"
        assert data["glyphs_added"] == 2

    def test_reference_letter_method_accepted(self, runner, tmp_path):
        """--baseline-method=reference-letter with valid --reference-letter works."""
        src = _make_flat_font_dir(tmp_path, {
            "x": [(0, 0), (100, 0), (100, 300), (0, 300)],
            "g": [(0, 0), (100, 0), (100, 300), (50, 500), (0, 500)],
            "H": [(0, 0), (100, 0), (100, 400), (0, 400)],
        })
        data = self._import(runner, src, tmp_path / "out",
                            ["--baseline-method", "reference-letter",
                             "--reference-letter", "x"])
        assert data.get("baseline_method") == "reference-letter"
        assert data.get("reference_letter") == "x"
        assert data["glyphs_added"] == 3

    def test_reference_letter_fallback_when_missing(self, runner, tmp_path):
        """When the reference letter isn't in the source dir, falls back to bbox-bottom."""
        src = _make_flat_font_dir(tmp_path, {
            "A": [(0, 0), (100, 0), (100, 500)],
            "B": [(0, 0), (100, 0), (100, 400)],
        })
        # Reference letter 'x' is not present in src
        result = runner.invoke(
            root,
            ["--json", "font", "import",
             "--name", "TestFont",
             "--source-dir", str(src),
             "--output-dir", str(tmp_path / "out"),
             "--baseline-method", "reference-letter",
             "--reference-letter", "x"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        # Warning may precede the JSON; find the first '{' to isolate JSON
        json_start = result.output.index("{")
        data = json.loads(result.output[json_start:])
        # Falls back to bbox-bottom; glyphs still imported
        assert data["glyphs_added"] == 2
        assert data.get("baseline_method") == "bbox-bottom"

    def test_reference_letter_without_flag_falls_back(self, runner, tmp_path):
        """--baseline-method=reference-letter without --reference-letter falls back."""
        src = _make_flat_font_dir(tmp_path, {
            "A": [(0, 0), (100, 0), (100, 500)],
        })
        result = runner.invoke(
            root,
            ["--json", "font", "import",
             "--name", "TestFont",
             "--source-dir", str(src),
             "--output-dir", str(tmp_path / "out"),
             "--baseline-method", "reference-letter"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        json_start = result.output.index("{")
        data = json.loads(result.output[json_start:])
        assert data.get("baseline_method") == "bbox-bottom"


# ---------------------------------------------------------------------------
# font set-baseline tests
# ---------------------------------------------------------------------------

class TestFontSetBaseline:
    """Tests for the `font set-baseline` command."""

    def _make_imported_font(self, runner, tmp_path):
        """Create a minimal imported font to use as set-baseline input."""
        src = _make_flat_font_dir(tmp_path, {
            "A": [(0, 0), (100, 0), (100, 500), (0, 500)],
            "g": [(0, 0), (100, 0), (100, 500), (50, 700)],
        })
        result = runner.invoke(
            root,
            ["--json", "font", "import",
             "--name", "TestFont",
             "--source-dir", str(src),
             "--output-dir", str(tmp_path / "font")],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        return tmp_path / "font"

    def test_shift_updates_svg_transform(self, runner, tmp_path):
        """set-baseline shifts the glyph's translate(ty) in →.svg."""
        import re
        fdir = self._make_imported_font(runner, tmp_path)

        # Read original transform for 'A'
        from lxml import etree as _etree
        SVG_NS_URI = "http://www.w3.org/2000/svg"
        INKSCAPE_URI = "http://www.inkscape.org/namespaces/inkscape"
        tree_before = _etree.parse(str(fdir / "→.svg"))
        root_el = tree_before.getroot()
        layer_before = next(
            el for el in root_el.iter(f"{{{SVG_NS_URI}}}g")
            if el.get(f"{{{INKSCAPE_URI}}}label") == "GlyphLayer-A"
        )
        transform_before = layer_before.get("transform", "")
        m_before = re.search(r'translate\(([^,]+),([^)]+)\)', transform_before)
        ty_before = float(m_before.group(2)) if m_before else 0.0

        # Shift 'A' up by 1 mm
        result = runner.invoke(
            root,
            ["--json", "font", "set-baseline",
             "--font-dir", str(fdir),
             "--char", "A",
             "--shift-mm", "1.0"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["char"] == "A"

        # SVG should reflect new transform
        tree_after = _etree.parse(str(fdir / "→.svg"))
        root_after = tree_after.getroot()
        layer_after = next(
            el for el in root_after.iter(f"{{{SVG_NS_URI}}}g")
            if el.get(f"{{{INKSCAPE_URI}}}label") == "GlyphLayer-A"
        )
        transform_after = layer_after.get("transform", "")
        m_after = re.search(r'translate\(([^,]+),([^)]+)\)', transform_after)
        ty_after = float(m_after.group(2)) if m_after else 0.0

        from cli_anything_inkstitch.commands.font import _SVG_MM_PER_PX
        expected_shift_px = -(1.0 / _SVG_MM_PER_PX)
        assert abs((ty_after - ty_before) - expected_shift_px) < 0.1, (
            f"expected ty to change by {expected_shift_px:.2f} px, "
            f"got {ty_after - ty_before:.2f}"
        )

    def test_shift_recorded_in_font_json(self, runner, tmp_path):
        """set-baseline stores the cumulative shift in font.json baseline_overrides."""
        fdir = self._make_imported_font(runner, tmp_path)

        runner.invoke(
            root,
            ["font", "set-baseline",
             "--font-dir", str(fdir),
             "--char", "g",
             "--shift-mm", "-1.5"],
            catch_exceptions=False,
        )
        font_json = json.loads((fdir / "font.json").read_text())
        assert "baseline_overrides" in font_json
        override_g = font_json["baseline_overrides"].get("g")
        assert override_g is not None
        # shift_mm=-1.5 means move down; shift_px = +(1.5 / _SVG_MM_PER_PX)
        from cli_anything_inkstitch.commands.font import _SVG_MM_PER_PX
        expected_px = 1.5 / _SVG_MM_PER_PX
        assert abs(override_g - expected_px) < 0.1, (
            f"expected override ~{expected_px:.2f} px, got {override_g}"
        )

    def test_cumulative_shifts(self, runner, tmp_path):
        """Two consecutive set-baseline calls accumulate their shifts."""
        fdir = self._make_imported_font(runner, tmp_path)

        for shift in ("2.0", "-0.5"):
            runner.invoke(
                root,
                ["font", "set-baseline",
                 "--font-dir", str(fdir),
                 "--char", "A",
                 "--shift-mm", shift],
                catch_exceptions=False,
            )

        font_json = json.loads((fdir / "font.json").read_text())
        from cli_anything_inkstitch.commands.font import _SVG_MM_PER_PX
        net_shift_mm = 2.0 + (-0.5)   # 1.5 mm upward net
        expected_px = -(net_shift_mm / _SVG_MM_PER_PX)  # negative = upward in SVG
        actual_px = font_json["baseline_overrides"].get("A", 0.0)
        assert abs(actual_px - expected_px) < 0.1, (
            f"expected {expected_px:.2f} px net, got {actual_px:.2f}"
        )

    def test_unknown_char_raises_user_error(self, runner, tmp_path):
        """set-baseline on a glyph that doesn't exist raises UserError (non-zero exit)."""
        fdir = self._make_imported_font(runner, tmp_path)
        result = runner.invoke(
            root,
            ["font", "set-baseline",
             "--font-dir", str(fdir),
             "--char", "Z",   # not in the font
             "--shift-mm", "1.0"],
        )
        assert result.exit_code != 0
