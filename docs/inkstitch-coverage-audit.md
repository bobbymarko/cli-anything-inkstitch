# Ink/Stitch engine coverage audit

Task #20. Engine surface = the extensions registry
(`inkstitch/lib/extensions/__init__.py`, 83 entries), element/param classes
(`inkstitch/lib/elements/`), the visual-command registry
(`inkstitch/lib/commands.py`), and output formats. Compared against the CLI
command groups, the schema extractor, and the artifact browser editor.
Produced by an automated sweep, then hand-verified — every claim kept here
was checked against the named engine file. Date: 2026-07-10.

## Covered (verified)

- **Digitization core** — element classification, every `@param` on
  FillStitch/Stroke/SatinColumn (mined by `schema/extract.py`, including
  combo `ParamOption` values and per-method `select_items` filtering),
  validation against the engine's read contracts (index dropdowns, combo id
  strings, multi-value lists). Backed by differential stitch-plan tests.
- **Visual commands** — the full `COMMANDS` registry via
  `commands attach/detach/list/migrate` and editor handles, with the real
  connector structure the engine reads (`lib/commands.py find_commands`).
- **Geometry tools** — auto-satin, auto-run, stroke↔satin, fill→stroke,
  break-apart, cleanup, flip (`tools` group), all recording
  `document_replace` history since #27.
- **Stitch-order reordering** — Layers panel drag → `reorder_element` op
  (element order is stitch order). The engine's `reorder.py` extension is a
  GUI selection-order variant of the same thing.
- **Export** — all machine formats the binary writes, via `export`.
- **Params dialog / simulator / stacking editor** — replaced by `params`,
  the editor's stitch views + timeline, and the Layers panel respectively.

## Corrections to the automated sweep

- `fill_to_satin` is **not** covered anywhere: the engine requires
  user-drawn rung guides (`lib/extensions/fill_to_satin.py` errors without
  them); the editor's convert control refuses with an explanation. A CLI
  wrapper would be possible for callers that supply rung element ids.
- `fill_to_stroke` is covered as editor `convert_element` fill→run, but its
  engine options (`threshold_mm`, `line_width_mm`, `close_gaps`,
  `keep_original`) are not exposed.

## Verified gaps, ranked by workflow value

1. **Text composition (lettering)** — `font` group *builds* fonts but
   nothing renders "SPARKLE SQUAD" into stitchable elements. The engine's
   `lettering.py` extension is wxPython GUI, but the composing core lives in
   `lib/lettering/` and is drivable headless. Biggest missing capability
   for an agent-driven workflow.
2. **Density map** — `lib/extensions/density_map.py` is a plain headless
   extension (argparse in/SVG out) we never wrapped. Cheap `tools
   density-map`; candidates: gate warning on red-zone counts, editor
   overlay.
3. **Font metadata depth** — kerning pairs, per-glyph baseline/descender
   editing (`lettering_edit_json.py`); our `font` group covers advances and
   a single baseline.
4. **Generator dialogs** — satin multicolor, gradient blocks, tartan
   builder (`satin_multicolor.py`, `gradient_blocks.py`, `tartan.py`): the
   underlying params are settable through us, but the generators that split
   geometry/colors are unwrapped.
5. **Cutwork segmentation** (`cutwork_segmentation.py`) — niche; manual
   layering works today.

Everything else uncovered is GUI plumbing (installers, preferences, print
dialogs, toggles) with no headless value.
