# BX font test fixtures

This directory holds real Embrilliance `.bx` font files used as integration
test fixtures for `_extract_bx_connection_offsets` and `_parse_bx_glyphs`.

**These files are NOT committed to the repository** — they are commercial
embroidery fonts and distributing them would violate the vendors' licenses.
The directory itself and `*.bx` files are listed in `.gitignore`.

## Running the integration tests

The real-file tests use `@pytest.mark.skipif` so they are silently skipped
when the fixtures are absent.  To enable them, drop your own `.bx` files here
with the following names (one representative size per vendor is enough):

| Filename | Source |
|---|---|
| `tss_homerun_1in.bx` | TSS Homerun BX pack (any size) |
| `nitkabonitka_bubble_1in.bx` | NitkaBonitka Bubble Font 2D (any size) |
| `ld_signature_1.bx` | LD Signature Font (any size) |
| `stitchtopia_romantic_1in.bx` | Stitchtopia ActuallyRomantic (any size) |
| `chinoiserie_3in.bx` | Chinoiserie Alphabet BX (any size) |

Any `.bx` file from any Embrilliance-compatible pack should exercise the
parser.  The test assertions check structural properties (y_min ranges,
glyph counts, lower/upper presence) rather than exact byte values, so
a different size from the same font family will pass.
