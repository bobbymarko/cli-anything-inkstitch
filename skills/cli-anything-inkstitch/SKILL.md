---
name: "cli-anything-inkstitch"
description: >-
  Command-line interface for Ink/Stitch — A stateful command-line interface for machine-embroidery digitization, following the same patterns as cli-anything-inkscape. Directly manipulates SVG (XML) documents with `inkstitch:` namespace attributes via lxml, then invokes the Ink/Stitch binary for stitch generation, preview, and export to DST/PES/JEF/VP3 and other machine formats.
---

# cli-anything-inkstitch

A stateful command-line interface for machine-embroidery digitization, following the same patterns as `cli-anything-inkscape`. Sits between vector preparation and stitch generation: writes `inkstitch:` namespace attributes onto SVG path elements via lxml (the digitization step has no native CLI in Ink/Stitch), then delegates to the Ink/Stitch binary for stitch-plan preview and machine-format export.

## Installation

```bash
pip install cli-anything-inkstitch
```

**Prerequisites:**
- Python 3.10+
- Ink/Stitch must be installed on your system (https://inkstitch.org/docs/install/)
- The Ink/Stitch binary must be on `PATH`, or pointed at via `INKSTITCH_BINARY`, or recorded in the project JSON's `session.inkstitch_binary`.

## Usage

### Basic Commands

```bash
# Show help
cli-anything-inkstitch --help

# Start interactive REPL mode (against an existing or new project)
cli-anything-inkstitch --project /abs/path/logo.inkstitch-cli.json

# Open an SVG into a new project
cli-anything-inkstitch document open --project /abs/path/logo.inkstitch-cli.json --svg /abs/path/logo.svg

# Run with JSON output (for agent consumption)
cli-anything-inkstitch --json element list --project /abs/path/logo.inkstitch-cli.json --refresh
```

### REPL Mode

When invoked with only `--project` and no subcommand, the CLI enters an interactive REPL session:

```bash
cli-anything-inkstitch --project /abs/path/logo.inkstitch-cli.json
```

You can also start the REPL with a nonexistent project path. The CLI seeds a new in-memory project (you must `document open --svg <path>` before mutating):

```bash
cli-anything-inkstitch --project /abs/path/new-logo.inkstitch-cli.json
```

REPL meta-commands are prefixed with `:`  — `:save`, `:exit`, `:help`. All other input is parsed as a normal subcommand.

### Digitization model

Every embroidery file starts as an SVG annotated with `inkstitch:*` attributes. This CLI's job is to set those attributes correctly per element, then hand the SVG to Ink/Stitch for stitch math. The element's *stitch type* (`auto_fill`, `satin_column`, `running_stitch`, etc.) is determined by a combination of the path's geometry (fill / stroke / stroke-width) and the `inkstitch:*` attributes set on it. Use `schema get-stitch-type --type <name>` to discover what params each type accepts.

## Command Groups


### Document

Document and project management.

| Command | Description |
|---------|-------------|
| `new` | Create a new project (no SVG attached yet) |
| `open` | Open an existing SVG and create/attach a project |
| `prep` | Assign IDs, inline CSS-class fills/strokes, and detect/handle Illustrator stroke-to-outline rings (`--illustrator-rings={detect\|skip\|fill-black\|satin}`) |
| `set-context` | Capture design-intent context (material, stretch, thread, stabilizer, hoop_tension, intent, plus arbitrary `--set KEY=VALUE`). Surfaced in `element list` / `describe`. |
| `get-context` | Print the design-intent context. |
| `save` | Flush in-memory mutations to the SVG and project JSON |
| `info` | Show SVG dimensions, hoop, units, palette, element counts, stitch_type histogram |
| `set-hoop` | Set hoop size (`--name 100x100` or `--width-mm W --height-mm H`) |
| `set-units` | Set display units (`mm` or `in`); XML always stores `_mm` |
| `set-machine-target` | Set the default export format (`dst`, `pes`, `jef`, `vp3`, `exp`) |
| `set-palette` | Set the thread palette name. Writes BOTH session JSON and the SVG's `<metadata>/<inkstitch:thread-palette>` element — the latter is what inkstitch's exports / threadlists / apply-palette extension actually read. |
| `list-thread-colors` | Enumerate unique thread colors in the design (with element counts and closest CSS named color), plus the current palette. Useful for operator handoff: "load these N threads." |
| `set-collapse-len` | Set the collapse-jump distance in mm (default 3.0) |
| `set-min-stitch-len` | Set the minimum stitch length in mm (default 0.1) |
| `json` | Print raw project JSON |


### Element

Enumerate and inspect SVG elements; clear digitization state.

| Command | Description |
|---------|-------------|
| `list` | List elements with stitch_type, set params, warnings (`--refresh` rescans the SVG). Each element gains a `warnings: [...]` field if it would behave unexpectedly under inkstitch (e.g. `default_fill_black` for paths with no fill/stroke that will silently stitch as solid black). |
| `get` | Full attribute dump for one element by `--id` |
| `describe` | Rich derived context for AI reasoning: bbox in mm + as % of design, position (3x3 grid), aspect ratio, area %, closest named color, neighbors. `--id` for one element or omit for all. `--no-neighbors` to skip overlap analysis. |
| `identify` | Echo the element-class dispatch (FillStitch / SatinColumn / Stroke / …) |
| `delete` | Remove an SVG node entirely |
| `clear-params` | Strip all `inkstitch:*` attributes from an element (`--keep-commands` to preserve attached visual commands) |
| `clear-commands` | Remove all visual commands attached to an element |
| `ensure-id` | Assign an `@id` to an element matched by `--xpath` if missing; returns the id |


### Params

The core digitization group. Set stitch type and parameters on individual elements.

| Command | Description |
|---------|-------------|
| `set` | Set `--stitch-type` and any `--<param>` values on an element. Validates against the schema before writing. |
| `unset` | Remove specific params from an element |
| `get` | Dump current params for an element (with defaults and types) |
| `copy` | Copy params from one element `--from` to one or more `--to` elements (with `--only`/`--except` allowlists) |
| `apply-preset` | Apply a saved preset of params |
| `save-preset` | Save the current params on an element as a named preset |
| `list-presets` | List all available presets |

Param flags are kebab-cased versions of the inkstitch attribute names: `inkstitch:row_spacing_mm` ⇒ `--row-spacing-mm`. Booleans accept `true|false|yes|no|1|0`.


### Commands

Attach and detach Ink/Stitch visual commands (stops, trims, ignores, fill start/end markers).

| Command | Description |
|---------|-------------|
| `list` | Show all visual commands in the document, optionally filtered by `--id` |
| `attach` | Attach a visual command to an element (`--command stop|trim|ignore|fill_start|fill_end|pause|satin_start|satin_end`) |
| `detach` | Detach all matching visual commands from an element |
| `list-types` | List all visual command types this Ink/Stitch install supports |


### Tools

Binary-backed geometry rewrites — operations that require Ink/Stitch's stitch math.

| Command | Description |
|---------|-------------|
| `auto-satin` | Convert selected satin segments into one continuous auto-routed path (`--trim`, `--preserve-order`, `--keep-originals`) |
| `convert-to-satin` | Convert a stroke to a satin column |
| `convert-satin-to-stroke` | Convert a satin column back to a stroke |
| `flip-satin` | Swap rails on a satin column |
| `auto-run` | Auto-route running-stitch elements |
| `break-apart` | Split a compound path into individual subpaths |
| `cleanup` | Remove empty `<path>` elements, fills below an area threshold, strokes/satins below a length threshold, and empty groups |


### Validate

Static and binary-backed checks for digitization completeness and geometry health.

| Command | Description |
|---------|-------------|
| `run` | Invoke the Ink/Stitch troubleshoot extension; returns errors, warnings, type warnings as JSON. `--strict` makes any error a non-zero exit. |
| `static` | Run harness-only checks (no binary): missing required params, unknown attrs, out-of-range values |
| `fix` | Categorize issues: auto-fixable ones (empty paths, tiny fills) are dispatched to `cleanup` (default `--auto`; pass `--no-auto` to skip). Manual issues come back with one-line suggestions. `--strict` exits non-zero if any errors remain. |


### Preview

Generate a stitch-plan preview SVG and extract stitch-plan statistics.

| Command | Description |
|---------|-------------|
| `generate` | Render the stitch plan to an SVG file (`--render-mode simple\|realistic-300\|realistic-600\|realistic-vector`, `--needle-points`, `--visual-commands`, `--render-jumps`). Add `--raster --dpi 150` to also write a PNG via Inkscape showing what the *design* will look like (filled areas, not individual stitches). |
| `stitch-sim` | **Primary QA tool.** Renders the actual needle path of any DST/PES file as a PNG — stitches as solid colored lines, jump stitches as dashed gray lines. No project file or Ink/Stitch binary required. Options: `--dst <file>`, `--out <png>`, `--thread-color <hex>`, `--width`, `--height`, `--show-jumps/--hide-jumps`. Run after every export to catch fill-direction problems, excessive jumps, and travel stitches before a physical test-sew. |
| `stats` | Return JSON with stitch count, color stops, and estimated runtime |
| `rasterize` | Standalone SVG → PNG conversion via Inkscape (`--svg`, `--out`, `--dpi`). Useful for converting validation-layer SVGs or old previews into a visual. |


### Export

Produce machine-format embroidery files via the Ink/Stitch `output` and `zip` extensions.

| Command | Description |
|---------|-------------|
| `formats` | List supported export formats (introspected from pyembroidery: dst, pes, jef, vp3, exp, u01, pec, xxx, tbf, gcode, csv, json, svg, png, txt) |
| `file` | Export to a single file (`--format <fmt> --out <abs>`) |
| `zip` | Export multiple formats together (`--formats dst,pes,jef --out <abs.zip>`); add `--png-realistic`, `--svg`, `--threadlist` for bonus contents |


### Schema

Introspect the param schema (cached at install time from Ink/Stitch's element classes and INX templates).

| Command | Description |
|---------|-------------|
| `list-stitch-types` | List all assignable stitch types |
| `get-stitch-type` | Full param schema for one stitch type: name, type, default, min/max, enum, gui_text, description |
| `get-extension` | Full INX-style schema for any Ink/Stitch extension |
| `list-commands` | All available visual commands |
| `list-machine-formats` | All export formats with reader/writer flags |

Pass `--refresh-schema` on any command to rebuild the cache (also runs automatically when the resolved Ink/Stitch binary version or hash changes).


### Session

Undo / redo / history. Up to 50 levels.

| Command | Description |
|---------|-------------|
| `status` | Current SVG path, history cursor, dirty flag |
| `undo` | Undo the last operation (`--steps N` for multiple) |
| `redo` | Redo the last undone operation |
| `history` | Show undo history (`--limit N`, `--json`) |
| `reset` | Drop history; current SVG state is retained |


### Font

Create, import, calibrate, and validate Inkstitch-compatible embroidery font packages from per-letter embroidery files (DST, PES, EXP, etc.). Font commands operate on a font directory directly — no `--project` file required.

| Command | Description |
|---------|-------------|
| `init` | Create a blank font directory (SVG skeleton + `font.json`) |
| `import` | Import per-letter embroidery files into a complete font package. Key options: `--bx-file` for Embrilliance BX baseline extraction; `--baseline-method bbox-bottom\|last-stitch\|reference-letter`; `--script` for connecting script fonts; `--advance-padding` for extra spacing. |
| `set-baseline` | Per-glyph vertical shift correction (`--char`, `--shift-mm`). Positive = up. Cumulative; stored in `font.json["baseline_overrides"]`. |
| `add-glyph` | Add or replace a single glyph from an embroidery file |
| `remove-glyph` | Remove a glyph from an existing font |
| `set-advance` | Manually override the advance width for a specific glyph |
| `set-field` | Set any `font.json` field (name, description, keywords, etc.) |
| `info` | Show font metadata, glyph inventory, and advance widths |
| `preview` | Regenerate `preview.png` for an existing font |
| `render-test` | Render a phrase to a large PNG for visual advance-width inspection |
| `validate` | Validate all glyphs: missing characters, advance widths, SVG structure |
| `adjust-advances` | Bulk-adjust advance widths (`--add`, `--subtract`, `--min`, `--max`) |
| `import-bx-pack` | Batch-import a whole Embrilliance BX pack + matching EXP/DST directories |

### Artifact

The digitizing-artifact correction loop (see `docs/digitizing-artifact-spec.md`): opens a browser editor on the project's design where a human corrects the digitization by direct manipulation (drag satin rail nodes, rungs, fill start/end handles) and chat, while the agent long-polls for feedback and edits the same design through the CLI. All editor manipulations write through the project layer, so history/undo and SHA coherence keep working.

| Command | Description |
|---------|-------------|
| `open` | Open (or resume) the editor session; spawns a detached local server and opens the browser. `--no-browser` to just print the URL; `--reopen` only if the user ended the session from the browser and asked for further review. |
| `poll` | Long-poll for the next human feedback batch (`{objects, manipulation, text}` items). `--agent-reply "msg"` sends a chat reply first. Re-run after each result; queued feedback is never lost. |
| `reply` | Send a chat reply into the editor without polling |
| `gate` | Stitchability audit: desynced satin rungs, width limits, self-crossing rails, misplaced fill handles. Fix `errors` before handback; `warnings` are advisory. |
| `end` | End the session (agent-initiated; a plain `open` can revive it) |
| `stop` | Shut down the background artifact server |

Loop shape: `open` → repeat (`poll` → apply requested edits via `element`/`params`/`commands` or the server edit API → `poll --agent-reply "what changed"`) → `gate` → `end`. The editor live-reloads whenever the design changes on disk, and its "Stitch plan" toggle overlays the binary's authoritative render (~1.5s after each edit).


## Examples


### Open an SVG and Inspect

```bash
PROJ=/tmp/logo.inkstitch-cli.json

cli-anything-inkstitch document open --project $PROJ --svg /tmp/logo.svg
cli-anything-inkstitch document set-hoop --project $PROJ --name 100x100
cli-anything-inkstitch document set-machine-target --project $PROJ --format dst

cli-anything-inkstitch --json element list --project $PROJ --refresh
```


### Prep an Illustrator-exported SVG

Illustrator emits SVGs without element IDs and with fills/strokes defined via `<style>` CSS classes (`.cls-1 { fill: #abc }`). The CLI cannot address or classify those elements until they're prepped:

```bash
cli-anything-inkstitch document open --project $PROJ --svg /tmp/illustrator-export.svg
cli-anything-inkstitch --json document prep --project $PROJ
# → {"assigned_ids": 47, "inlined_styles": 47,
#    "illustrator_rings_found": 4, "illustrator_rings_action": "detect", ...}
```

`prep` also detects **Illustrator stroke-to-outline rings**: when you stroke a shape in Illustrator and export to SVG, the stroke is converted into a separate filled-ring `<path>` with no fill attribute and 2+ subpaths. SVG defaults that to black, and inkstitch will silently auto-fill the ring as solid black — usually not what you want.

`--illustrator-rings={detect|skip|fill-black|satin}` controls handling:

- `detect` (default): report only, don't modify. Good for inspecting first.
- `skip`: set `display="none"` so inkstitch ignores them entirely.
- `fill-black`: set explicit `fill="#000000"` so the auto-fill behavior is visible in `element list` (matches what inkstitch does silently anyway).
- `satin`: set `inkstitch:satin_column="True"` — the two subpaths become the satin rails. Best fit for designs where the rings represent intended outline embroidery. The path's `d` attribute is also rewritten to replace each `Z` close-path with an explicit lineto-back-to-start, preserving ring geometry while eliminating the literal `Z` that triggers inkstitch's `ClosedPathWarning`.

```bash
# typical Illustrator import workflow:
cli-anything-inkstitch document open --project $PROJ --svg /tmp/illustrator-export.svg
cli-anything-inkstitch --json document prep --project $PROJ --illustrator-rings satin
```

`prep` is self-contained (no Inkscape dependency). Idempotent — safe to re-run.


### Discover what's possible before assigning params

```bash
cli-anything-inkstitch --json schema list-stitch-types
cli-anything-inkstitch --json schema get-stitch-type --type satin_column
```


### Capture design intent before reasoning about params

```bash
# Tell the CLI (and the LLM) what this design is FOR
cli-anything-inkstitch document set-context --project $PROJ \
    --material "knit cotton t-shirt" \
    --stretch high \
    --thread "40wt polyester" \
    --stabilizer "medium cut-away" \
    --hoop-tension medium \
    --intent "team logo for left chest, will be washed weekly"

# Add arbitrary keys not covered by the typed flags
cli-anything-inkstitch document set-context --project $PROJ \
    --set "color_palette=team_2026" --set "wash_count=50"
```

Stored in `session.context`; surfaced as `document_context` at the top of every `element list` and `element describe` payload so the LLM sees it on every contextual call. This is what makes "more pull comp because it's stretchy" possible — the choice is grounded in real conditions, not assumed defaults.

`--stretch` accepts `none|low|medium|high`; `--hoop-tension` accepts `light|medium|firm`. Other values are free-form. `--unset KEY` removes one key, `--clear` wipes the whole context.


### Get rich design context before reasoning about params

```bash
# Per-element context: position, size, color name, neighbors
cli-anything-inkstitch --json element describe --project $PROJ
# → { design_size_mm: [60.2, 76.2],
#     document_context: { material: "...", stretch: "high", ... },
#     elements: [
#       { id: "elem_3", stitch_type: "auto_fill", color_name: "black",
#         size_mm: [68.4, 49.8], bbox_pct_of_design: { area: 74.2 },
#         position: "center", aspect_ratio: 1.37,
#         neighbors: [{id: "elem_1", relation: "contained_by"}, ...] },
#       ...
#     ] }
```

This is the LLM's window into the design. Use it before `params set` so
parameter choices are informed by what each element *is* (a small detail vs
a large background, near the edge vs centered, surrounded by what colors)
rather than just its fill hex. `--id X` for one element; omit for all.


### Assign stitch types and parameters

```bash
cli-anything-inkstitch params set --project $PROJ --id logo_outline \
    --stitch-type satin_column \
    --pull-compensation-mm 0.4 \
    --zigzag-spacing-mm 0.35 \
    --contour-underlay true \
    --contour-underlay-inset-mm 0.4

cli-anything-inkstitch params set --project $PROJ --id logo_text \
    --stitch-type auto_fill \
    --angle 45 --row-spacing-mm 0.25 \
    --fill-underlay true
```


### Attach a visual command (thread trim)

```bash
cli-anything-inkstitch commands attach --project $PROJ --id logo_text --command trim
```


### Validate, preview, then export

```bash
# Run binary-backed validation first
cli-anything-inkstitch --json validate run --project $PROJ

# Auto-fix what's auto-fixable; report manual issues with suggestions
cli-anything-inkstitch --json validate fix --project $PROJ
# → { ok, before, after, applied: [{tool: "cleanup", addresses: [...]}],
#     manual: [{name, label, suggestion, x_mm, y_mm, ...}] }

# Iterate manual fixes via params/tools, then strict-gate before export
cli-anything-inkstitch --json validate run --project $PROJ --strict

cli-anything-inkstitch preview generate --project $PROJ --out /tmp/logo-preview.svg
cli-anything-inkstitch --json preview stats --project $PROJ

cli-anything-inkstitch export file --project $PROJ --format dst --out /tmp/logo.dst
cli-anything-inkstitch export zip  --project $PROJ --formats dst,pes,jef --out /tmp/logo.zip
```


### Iterate with undo/redo

```bash
cli-anything-inkstitch params set --project $PROJ --id logo_outline --zigzag-spacing-mm 0.4
cli-anything-inkstitch session history --project $PROJ
cli-anything-inkstitch session undo --project $PROJ
cli-anything-inkstitch document save --project $PROJ
```


### Copy params between elements

```bash
cli-anything-inkstitch params copy --project $PROJ \
    --from logo_outline --to logo_subtitle --to logo_caption \
    --only pull_compensation_mm,zigzag_spacing_mm
```


## State Management

The CLI maintains session state with:

- **Undo/Redo**: Up to 50 levels of history
- **Project persistence**: Save/load project state as `.inkstitch-cli.json`
- **Session tracking**: Hoop size, units, machine target, palette, collapse/min-stitch lengths
- **SVG integrity**: SHA-256 of the SVG is recorded; mismatch on next invocation requires `--force`

The SVG is the source of truth — the project JSON is an index plus history. If you edit the SVG outside this CLI, run `element list --refresh` to resync.


## Output Formats

All commands support dual output modes:

- **Human-readable** (default): Tables, colors, formatted text
- **Machine-readable** (`--json` flag): Structured JSON for agent consumption

```bash
# Human output
cli-anything-inkstitch document info --project $PROJ

# JSON output for agents
cli-anything-inkstitch --json document info --project $PROJ
```

Errors always go to **stderr**. With `--json`, errors also appear on stdout as `{"error": {"type": "...", "message": "..."}}` while the exit code is preserved.


## For AI Agents

When using this CLI programmatically:

1. **Always use `--json`** for parseable output.
2. **Check return codes**: `0` success, `1` user error, `2` project/SVG error, `3` Ink/Stitch binary error, `4` validation error (under `--strict`).
3. **Parse stderr** for human-readable error messages on failure.
4. **Use absolute paths** for `--project`, `--svg`, `--out`, and any other file argument.
5. **Discover before assigning**: call `schema list-stitch-types` and `schema get-stitch-type --type <t>` before `params set`. The schema reflects the *installed* Ink/Stitch version, so don't hardcode param names.
6. **Refresh element state** with `element list --refresh` after any external edit or `tools` invocation — those rewrite SVG geometry.
7. **Validate before export**: `validate run --strict` catches malformed satin rails, too-narrow shapes, and missing rungs that would otherwise produce a useless DST file.
8. **Use `preview stats`** to sanity-check stitch counts and runtime before exporting — a 50,000-stitch file on a 50×50mm hoop is almost certainly an error.
9. **Geometry decides element type, not just attributes**: setting `--stitch-type satin_column` on a path with no stroke will fail validation. Use `cli-anything-inkscape` upstream to add a stroke first.
10. **Booleans** are written as Ink/Stitch's `True`/`False` (capital first letter); the CLI normalizes on input but agents reading SVG directly should expect that casing.
11. **Prep imported SVGs**: if `element list` returns nothing or every element shows `unassigned`, the SVG was likely exported from Illustrator (no IDs, CSS-class fills). Run `document prep` once before continuing. For Illustrator designs with stroked outlines, pass `--illustrator-rings=satin` to convert the auto-emitted outline-ring artifacts into satin columns rather than letting them stitch as solid black auto-fills.
12. **Use `validate fix` as a triage step**: it splits issues into auto-fixed (cleanup-handled) vs manual (with one-line suggestions). Pass `--no-auto` to inspect without mutating the SVG.
13. **Always run `element describe` before `params set`**: parameter choices are dependent on what each element *is* (small detail vs big background, surrounded by what colors, near the edge vs centered) — not just its fill hex. Describe gives the LLM the geometric and relational context heuristic auto-digitization can't see.
14. **Capture intent with `document set-context` early**: material, stretch, thread, stabilizer, hoop tension, what the design is for. This appears as `document_context` in every `element list` / `describe` call thereafter, so param choices ground in real conditions ("more pull comp because the substrate is stretchy") instead of assumed defaults.
15. **Consult the `embroidery-digitization` skill** when choosing stitch types or parameter values. It encodes the per-element decision flow (stitch type → direction → spacing → underlay → comp), fabric-specific starting numbers, satin width thresholds, and visual failure modes — knowledge the CLI surface alone doesn't carry.
16. **`preview generate --raster` shows design appearance; `preview stitch-sim` shows what the machine will do.** Use `generate --raster` to visually confirm design coverage and color layout (requires Inkscape). Use `stitch-sim` to inspect the actual needle path — fill sweep direction, jump distances, travel stitches around holes. They answer different questions; use both.
17. **Run `preview stitch-sim` after every export, before test-sewing.** It requires no project file or binary — just the DST. Read the PNG and check: are fill rows sweeping the right direction? Are jump stitches (dashed gray) short and local, or do they cross the whole design? Are there unexpected travel paths inside a fill? This catches problems that neither `validate run` nor `preview generate` will surface.
18. **Illustrator SVGs often have no physical size.** A bare `viewBox` with no `width`/`height` is interpreted by inkstitch as pixels at 96 dpi — a 300×300 viewBox unit design will digitize as ~79mm, not 300mm. Always check `document info` → `root_attrib` for explicit `width`/`height`. If absent, add them (e.g. `width="304.8mm"`) before assigning any params, or the output size will be wrong.
19. **SVG element order is stitch order. Scattered elements cause long jumps.** Decorative elements (sparkles, dots, scattered accents) that appear in arbitrary document order will be stitched in that order, zigzagging across the design and producing long jump stitches. Reorder them geographically — a clockwise sweep, or left-to-right — so each sequential jump is short. Visible in `stitch-sim` output as long dashed lines crossing the design.
20. **Compound paths with holes create fill travel stitches.** If an `auto_fill` element has cutout holes (eyes, smile, counters), inkstitch must navigate around each hole with travel stitches on every row that intersects it. Two clean solutions: (a) **`contour_fill`** — traces the outline spiraling inward, no row-by-row interruptions, good for organic shapes; creates visible concentric-line texture. (b) **Split the holes into separate elements** with a contrasting thread color stitched on top after the main fill — the main shape becomes a clean solid fill, and the face/counter features become second-color shapes that cover it.
21. **`fill_start` placement controls sweep direction, not just entry point.** The fill sweeps from the `fill_start` marker edge toward `fill_end`. For shapes where a single fill angle creates disconnected row sections (e.g., star tips, concave indentations), place `fill_start` at the topmost (or leading) point of the shape so the narrow extremity is stitched first and naturally connects into the widening body below — rather than being discovered mid-sweep as an isolated island requiring a jump. Use `commands attach --command fill_start --at-x <mm> --at-y <mm>` positioned at the element's bounding box edge.
22. **After any direct SVG edit outside the CLI, re-open with `--force`.** If you manipulate the SVG with Python/lxml directly (splitting compound paths, reordering elements, adding new elements), the project's stored SHA-256 will mismatch and the CLI will refuse to mutate. Resync with: `document open --project $PROJ --svg $SVG --force`.


### QA a digitized file with stitch-sim

Run this after every export — before loading into embroidery software or test-sewing:

```bash
cli-anything-inkstitch preview stitch-sim \
    --dst /path/to/design.dst \
    --out /path/to/design-sim.png \
    --thread-color "#e85454" \
    --width 1800 --height 1600

# Read the PNG and check:
# - Fill rows sweep in the intended direction with no disconnected sections
# - Dashed gray lines (jumps) are short and local — not crossing the whole design
# - No unexpected travel curves inside a filled area (sign of compound-path holes)
# - Scattered small elements travel in a logical geographic sequence
# - Underlay cross-hatch is visible beneath the top fill rows
```

If you see long jumps between scattered elements → reorder them in SVG document order geographically.
If you see travel curves inside a fill → the element has compound-path holes; use `contour_fill` or split the holes into a separate color.
If you see disconnected fill sections at a shape tip → move `fill_start` to that tip so it's stitched first.


### Import an embroidery font

**Happy path — with a BX file (most accurate baselines):**

```bash
font import \
    --name "My Script" \
    --source-dir /path/to/dst-files/ \
    --output-dir /path/to/fonts/my-script/ \
    --bx-file /path/to/MyScript.bx \
    --script \
    --advance-padding 8
```

The BX file is a metadata-only Embrilliance installer package. The parser extracts per-glyph `y_min` values (baseline/connection-point Y in 0.1 mm units) for exact placement without any vendor-specific handling.

**Without a BX file — using a reference letter for baseline alignment:**

```bash
font import \
    --name "My Block" \
    --source-dir /path/to/dst-files/ \
    --output-dir /path/to/fonts/my-block/ \
    --baseline-method reference-letter \
    --reference-letter H \
    --advance-padding 6
```

**Per-glyph correction after import:**

If a specific glyph sits too high or low after import, adjust it in place without re-importing:

```bash
font set-baseline --font-dir /path/to/fonts/my-block/ --char g --shift-mm -0.8
# Shifts 'g' descender down by 0.8 mm. Cumulative; safe to call multiple times.
```

**Visual advance-width inspection:**

```bash
font render-test \
    --font-dir /path/to/fonts/my-block/ \
    --text "Hamming distance" \
    --out /tmp/render-test.png \
    --dpi 150
# Open the PNG and look for gaps or collisions between letters.
# Adjust with: font set-advance --char <c> --advance <px>
# Or bulk-pad: font adjust-advances --add 4 --min 20
```


## More Information

- Full technical specification: See `SPEC.md` in the package
- Ink/Stitch documentation: https://inkstitch.org/docs/
- Ink/Stitch namespace reference: https://inkstitch.org/namespace/
- pyembroidery (machine-format library): https://github.com/EmbroidePy/pyembroidery


## Version

0.1.3
