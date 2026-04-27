# cli-anything-inkstitch — Technical Specification

**Status:** Implementation-ready. v1.0 surface.
**Target reader:** A developer who will build this tool from scratch with no further design input.
**Companion files:** [SKILL.md](skills/cli-anything-inkstitch/SKILL.md) (agent-facing usage doc).

---

## 0. Architecture & Trust Model

### 0.1 Pipeline position

```
cli-anything-inkscape   →   design.svg  (vector geometry, layers, shapes)
         ↓
cli-anything-inkstitch  →   design.svg  (same file, now annotated with inkstitch:* attrs)
         ↓
inkstitch binary        →   output.dst / .pes / .jef / preview.svg
```

The harness sits between vector preparation (Inkscape-side) and stitch generation (Ink/Stitch-side). It owns the **digitization** step that has no existing CLI surface.

### 0.2 What we write directly vs. what we delegate

The Ink/Stitch element classes (`FillStitch`, `SatinColumn`, `Stroke` in `lib/elements/`) read params via `Element.get_param(name, default)` which does `node.get(INKSTITCH_ATTRIBS[name], "")`. Setting these attributes with `lxml` is functionally identical to setting them through the GUI Params dialog — the GUI just writes the same XML.

**Write directly with lxml** (zero binary invocation):
- All `inkstitch:*` namespaced attributes on path/group elements
- Visual command `<use xlink:href="#inkstitch_*"/>` insertions
- Document-level metadata (`<metadata>` elements: `inkstitch:collapse_len_mm`, `inkstitch:min_stitch_len_mm`, `inkstitch:thread-palette`)
- Project JSON state (undo, history, session)

**Delegate to the inkstitch binary** (operations that require actual stitch math):
- `stitch_plan_preview` — needs to compute the stitch path
- All exports (`output`, `zip`) — needs pyembroidery + the stitch pipeline
- `auto_satin`, `auto_run`, `convert_to_satin`, `convert_satin_to_stroke` — geometry rewrites the harness shouldn't reimplement
- `troubleshoot` — runs the validation rules over computed geometry

### 0.3 SVG is the source of truth

The project JSON is an *index* with undo/redo and session metadata. The actual digitization state lives in the SVG file. Never mirror SVG state into the project JSON beyond a checksum + a per-element status snapshot for fast `element list` rendering.

---

## 1. Project JSON Schema

### 1.1 File location and naming

- Suffix: **`.inkstitch-cli.json`** (mirrors `.inkscape-cli.json` from the inkscape skill).
- Always referenced by absolute path (`--project /abs/path/foo.inkstitch-cli.json`).
- A new project is created via `document open --svg <path> --project <path>` or `document new --project <path>`.

### 1.2 Top-level shape

```jsonc
{
  "schema_version": 1,
  "created_at": "2026-04-26T18:00:00Z",
  "updated_at": "2026-04-26T18:14:32Z",
  "svg_path": "/abs/path/design.svg",
  "svg_sha256": "9f86d081884c…",     // recomputed on every save; mismatch ⇒ external edit warning
  "session": {
    "hoop": { "width_mm": 100.0, "height_mm": 100.0, "name": "100x100" },
    "units": "mm",                     // "mm" | "in"  (display only; XML always stores _mm)
    "machine_target": "dst",           // default export format
    "thread_palette": "Madeira Polyneon",
    "collapse_len_mm": 3.0,
    "min_stitch_len_mm": 0.1,
    "inkstitch_binary": "/Applications/Ink Stitch.app/Contents/MacOS/inkstitch"
  },
  "elements": {
    // keyed by SVG element @id — populated by `element list --refresh`
    "logo_outline": {
      "tag": "path",
      "stitch_type": "satin_column",   // null until set
      "set_params": ["satin_column", "pull_compensation_mm", "zigzag_spacing_mm"],
      "last_modified": "2026-04-26T18:12:01Z"
    }
  },
  "history": {
    "cursor": 7,                       // index into `entries`; entries[cursor] is current
    "entries": [ /* see 1.3 */ ]
  }
}
```

### 1.3 History entry format

Bounded ring buffer, **max 50 entries**. Older entries dropped FIFO. Each entry stores a *patch*, not a full SVG snapshot.

```jsonc
{
  "id": "h_01HW8…",                          // ULID
  "ts": "2026-04-26T18:12:01Z",
  "command": "params set --id logo_outline --stitch-type satin_column --pull-compensation-mm 0.4",
  "scope": "svg",                             // "svg" | "project"
  "patch": {
    "type": "attr_diff",                      // see 1.3.1
    "target_xpath": "//*[@id='logo_outline']",
    "before": { "inkstitch:pull_compensation_mm": null },
    "after":  { "inkstitch:pull_compensation_mm": "0.4" }
  }
}
```

#### 1.3.1 Patch types

| `type` | Used by | `before`/`after` shape |
|---|---|---|
| `attr_diff` | `params set`, `params clear`, `tools flip-satin` | `{ "<qname>": "value" \| null }` per attr |
| `subtree_replace` | `tools auto-satin`, `tools route-fill` (binary rewrites geometry) | `{ "before_xml": "<g…>…</g>", "after_xml": "<g…>…</g>" }` |
| `node_insert` | Visual command insertions | `{ "after_xml": "<svg:use …/>", "parent_xpath": "…", "index": 3 }` |
| `node_delete` | `element delete`, `element clear-commands` | `{ "before_xml": "…", "parent_xpath": "…", "index": 3 }` |
| `metadata_diff` | `document set-hoop`, `document set-collapse-len` | `{ "<key>": value }` per field |

`undo` reverses the patch; `redo` re-applies it. For `subtree_replace`, the implementation re-parses `before_xml` / `after_xml` and swaps the parent's child at `target_xpath`'s position.

### 1.4 Element status snapshot

`elements[<id>].set_params` is a denormalized list of which `inkstitch:*` attributes are *currently* present on the element. This is rebuilt by `element list --refresh` (a single `lxml` walk). Agents should treat it as a hint; the SVG is authoritative.

`elements[<id>].stitch_type` is one of:

| Value | Detection rule (matches `lib/elements/utils/nodes.py:node_to_elements`) |
|---|---|
| `auto_fill` | has fill color, fill-opacity > 0, `inkstitch:auto_fill="True"` (or unset — default is True) |
| `legacy_fill` | has fill color, `inkstitch:auto_fill="False"` |
| `satin_column` | has stroke color AND (`inkstitch:satin_column="True"` OR stroke-width > satin_threshold) |
| `running_stitch` | has stroke color, no satin attr, `inkstitch:stroke_method` unset or `"running_stitch"` |
| `manual_stitch` | `inkstitch:manual_stitch="True"` |
| `ripple_stitch` / `zigzag_stitch` / `bean_stitch` | `inkstitch:stroke_method=<value>` |
| `ignore` | `<svg:use xlink:href="#inkstitch_ignore">` attached |
| `unassigned` | none of the above (will be skipped during stitch) |

---

## 2. Command Surface

### 2.1 Global conventions

- Binary name: **`cli-anything-inkstitch`** (entry point; `inkstitch-cli` as a short alias).
- Subcommand syntax: `cli-anything-inkstitch <group> <action> [flags]`.
- Mandatory on every stateful command: `--project <abs-path>` (alias `-p`).
- `--json` on every command → machine-readable JSON to stdout.
- Without `--json` → human-readable text to stdout.
- Exit codes: `0` success, `1` user error (bad flag, missing element id), `2` SVG/project error (unparseable, sha mismatch), `3` binary invocation error, `4` validation error (when `--strict` is set).
- All error messages go to **stderr**; stdout stays clean for piping.
- All file paths are **absolute**; relative paths return exit 1 with `error: path must be absolute: <path>`.
- All commands that mutate write a history entry before returning. `--no-history` suppresses (used internally by `session redo`).

### 2.2 REPL mode

`cli-anything-inkstitch --project /abs/path/foo.inkstitch-cli.json` (no subcommand) enters a REPL. State is held in memory; every command runs against the same loaded SVG; `:save` flushes to disk; `:exit` saves and quits. REPL prompt is `inkstitch> `. REPL preserves the same flag syntax (so transcripts are copy-pasteable).

### 2.3 Command groups

Eight groups, mirroring the inkscape skill's structure where applicable.

| Group | Purpose |
|---|---|
| `document` | open/save/info; document-wide settings (hoop, units, target machine, palette) |
| `element` | enumerate, inspect, identify SVG elements; clear params |
| `params` | **the core group** — set stitch type, set/get individual params, copy params |
| `commands` | attach/detach visual commands (`stop`, `trim`, `ignore`, `fill_start`, `fill_end`) |
| `tools` | invoke binary-side rewrites: auto-satin, stroke↔satin, flip-satin, auto-route, break-apart |
| `validate` | run troubleshoot extension; static checks (missing required params, unsupported attrs) |
| `preview` | invoke `stitch_plan_preview`; return stats + path to rendered SVG |
| `export` | invoke `output` / `zip` extensions; produce DST/PES/JEF/VP3/EXP/SVG/ZIP |
| `schema` | introspect the param schema (extracted from INX/Jinja templates) |
| `session` | undo / redo / history / status |

Total: 10 groups. Detailed surface follows.

---

### 2.4 `document` group

```
document new        --project <abs>  --svg <abs>  [--hoop <name>|--hoop-mm WxH]
document open       --project <abs>  --svg <abs>  [--force]      # creates project pointing at existing SVG
document save       --project <abs>  [--svg-out <abs>]           # flushes SVG; --svg-out copies to new path
document info       --project <abs>                              # prints SVG dims, hoop, units, palette, element count, stitch_type histogram
document set-hoop   --project <abs>  --name <preset> | --width-mm <f> --height-mm <f>
document set-units  --project <abs>  --units mm|in
document set-machine-target --project <abs>  --format dst|pes|jef|vp3|exp
document set-palette --project <abs>  --palette <name>           # writes inkstitch:thread-palette in <metadata>
document set-collapse-len   --project <abs>  --mm <f>            # default 3.0
document set-min-stitch-len --project <abs>  --mm <f>            # default 0.1
document json       --project <abs>                              # dump raw project JSON
```

**Reads**: project JSON, SVG.
**Writes**: project JSON; for `set-*` also writes inkstitch metadata into SVG `<metadata>`.

---

### 2.5 `element` group

```
element list       --project <abs>  [--refresh]  [--filter <stitch_type>]  [--with-params]
element get        --project <abs>  --id <svg-id>                # full attr dump for one element
element identify   --project <abs>  --id <svg-id>                # echoes the dispatch (FillStitch / SatinColumn / Stroke / …)
element delete     --project <abs>  --id <svg-id>                # removes the SVG node entirely
element clear-params --project <abs>  --id <svg-id> [--keep-commands]
element clear-commands --project <abs>  --id <svg-id>            # removes <use> children pointing at inkstitch_*
element ensure-id  --project <abs>  --xpath <expr>               # assigns an @id if missing; returns the id (used by AI to address freshly-created shapes)
```

**`element list` JSON output** (representative):

```json
{
  "elements": [
    {
      "id": "logo_outline",
      "tag": "path",
      "label": "Logo Outline",
      "fill": "#000000",
      "stroke": null,
      "stroke_width_mm": 0.0,
      "stitch_type": "auto_fill",
      "set_params": ["auto_fill", "angle", "row_spacing_mm"],
      "commands": [],
      "warnings": []
    }
  ],
  "count": 1
}
```

`--refresh` walks the SVG and rebuilds the `elements` map in the project JSON; otherwise the cached snapshot is used.

---

### 2.6 `params` group — the core

```
params set         --project <abs>  --id <svg-id>  --stitch-type <type>  [--<param>=<value> …]
params unset       --project <abs>  --id <svg-id>  --param <name> [--param <name> …]
params get         --project <abs>  --id <svg-id>  [--param <name>]      # all if --param omitted
params copy        --project <abs>  --from <id> --to <id> [--to <id> …]  [--only <p1,p2>]  [--except <p1,p2>]
params apply-preset --project <abs>  --id <svg-id>  --preset <name>      # presets are JSON files in ~/.config/cli-anything-inkstitch/presets/
params save-preset  --project <abs>  --id <svg-id>  --preset <name>
params list-presets [--json]
```

**Allowed `--stitch-type` values** (each maps to a default attribute set; see §3.2):

`auto_fill`, `legacy_fill`, `contour_fill`, `guided_fill`, `meander_fill`, `circular_fill`, `linear_gradient_fill`, `tartan_fill`, `running_stitch`, `bean_stitch`, `ripple_stitch`, `zigzag_stitch`, `manual_stitch`, `satin_column`, `e_stitch`, `cross_stitch`, `cross_stitch_half`.

When `--stitch-type` is set, the implementation:
1. Validates the element's geometry is compatible with that type (e.g., satin requires a stroke; auto_fill requires a closed filled path). On mismatch returns exit 1 with a clear message; `--force` overrides.
2. Sets the *defining* attribute (`inkstitch:auto_fill`, `inkstitch:satin_column`, `inkstitch:manual_stitch`, or `inkstitch:stroke_method`/`inkstitch:fill_method` as appropriate).
3. Sets any other attributes given in the same call.
4. Records one `attr_diff` history entry covering all changes.

**`--<param>` flag naming**: kebab-case maps 1:1 to underscore_case attribute names. `--row-spacing-mm 0.3` ⇒ `inkstitch:row_spacing_mm="0.3"`. Booleans accept `true|false|yes|no|1|0` (lowercase, written as `True`/`False` per inkstitch convention).

**Validation**: every value is checked against the schema (§3) for type, min/max, enum membership. Failures return exit 1 with `error: <param>: <reason>`.

**`params get` JSON output**:

```json
{
  "id": "logo_outline",
  "stitch_type": "satin_column",
  "params": {
    "satin_column": { "value": true,  "default": false, "type": "boolean" },
    "pull_compensation_mm": { "value": 0.4, "default": 0.0, "type": "float", "unit": "mm" },
    "zigzag_spacing_mm":    { "value": 0.4, "default": 0.4, "type": "float", "unit": "mm", "is_default": true }
  }
}
```

---

### 2.7 `commands` group

Visual commands are SVG `<use>` elements whose `xlink:href` points to an `<svg:symbol>` defined under `<defs>` (`#inkstitch_stop`, `#inkstitch_trim`, `#inkstitch_ignore`, `#inkstitch_fill_start`, `#inkstitch_fill_end`, plus less-common ones: `#inkstitch_pause`, `#inkstitch_satin_start`, `#inkstitch_satin_end`).

```
commands list     --project <abs>  [--id <svg-id>]               # all visual commands; or just on one element
commands attach   --project <abs>  --id <svg-id>  --command <name>  [--at-x <mm> --at-y <mm>]
commands detach   --project <abs>  --id <svg-id>  --command <name>  # detach all matching
commands list-types
```

`commands attach` ensures the symbol definition exists under `<defs>` (insert if missing) and creates the `<use>` element parented to the SVG element's group.

---

### 2.8 `tools` group — binary-backed geometry rewrites

```
tools auto-satin            --project <abs>  --ids <id,id,…>  [--trim] [--preserve-order] [--keep-originals]
tools convert-to-satin      --project <abs>  --ids <id,id,…>
tools convert-satin-to-stroke --project <abs>  --ids <id,id,…>
tools flip-satin            --project <abs>  --id <svg-id>
tools auto-run              --project <abs>  --ids <id,id,…>     # auto-route running stitches
tools break-apart           --project <abs>  --id <svg-id>       # for compound paths
tools cleanup               --project <abs>  [--keep-empty-groups]
```

Each invokes the inkstitch binary with `--extension=<name>` and an `--id=…` for each target. The binary rewrites the SVG; the harness diffs the affected subtree and records a `subtree_replace` history entry.

---

### 2.9 `validate` group

```
validate run         --project <abs>  [--strict]                 # invokes inkstitch troubleshoot, parses validation_layer
validate static      --project <abs>                             # harness-side checks only (no binary)
validate fix         --project <abs>  [--auto-only]              # apply auto-fixable suggestions
```

`validate run` invokes `inkstitch --extension=troubleshoot` with arguments to suppress the visual layer (or generates it then strips it from the working SVG before commit), parses the warnings/errors out of the layer's `<text>` nodes, returns:

```json
{
  "errors":   [{ "id": "logo_outline", "type": "InvalidShapeError",   "message": "…" }],
  "warnings": [{ "id": "logo_outline", "type": "SmallShapeWarning",   "message": "…" }],
  "type_warnings": [{ "id": "frame", "type": "ObjectTypeWarning", "message": "…" }],
  "ok": false
}
```

`validate static` runs without the binary: checks that every element with a stitch_type has its required params set; flags unknown `inkstitch:*` attribute names; flags param values out of declared range.

Exit code with `--strict`: 4 if any error, 0 otherwise.

---

### 2.10 `preview` group

```
preview generate   --project <abs>  --out <abs.svg>  [--ids <id,id,…>]
                                    [--render-mode simple|realistic-300|realistic-600|realistic-vector]
                                    [--needle-points] [--visual-commands] [--render-jumps]
                                    [--insensitive]
preview stats      --project <abs>  [--ids <id,id,…>]            # JSON: stitch count, jump count, color stops, est. time
```

`preview generate` invokes `inkstitch --extension=stitch_plan_preview` and writes the resulting SVG (which contains the `__inkstitch_stitch_plan__` layer). `preview stats` runs the same extension to a temp file, then parses the layer to extract counts and color-change positions; returns:

```json
{
  "stitch_count": 12340,
  "jump_count": 18,
  "trim_count": 4,
  "color_stops": [
    { "index": 0, "thread": "Madeira Polyneon 1701", "rgb": "#000000", "stitches": 5400 },
    { "index": 1, "thread": "Madeira Polyneon 1801", "rgb": "#ffffff", "stitches": 6940 }
  ],
  "estimated_time_seconds": 412,
  "bounding_box_mm": { "x": 12.4, "y": 8.1, "width": 78.0, "height": 62.5 }
}
```

Estimated time uses a configurable stitches-per-minute constant (default 800) — exposed via `--spm`.

---

### 2.11 `export` group

```
export formats     [--json]                                      # introspect via pyembroidery.supported_formats()
export file        --project <abs>  --format <fmt>  --out <abs>  [--id <id> …]
export zip         --project <abs>  --formats <f1,f2,…>  --out <abs.zip>
                                    [--png-realistic] [--svg] [--threadlist]
                                    [--x-repeats <n>] [--y-spacing-mm <f>]
```

`export file` invokes `inkstitch --extension=output --format=<fmt>`; `export zip` invokes `inkstitch --extension=zip --format-<fmt>=true …` for each requested format. Both stream the binary's stdout to `--out`.

`--format` accepts whatever `pyembroidery.EmbPattern.supported_formats()` returns as a writer: dst, pes, jef, vp3, exp, u01, pec, xxx, tbf, gcode, csv, json, svg, png, txt.

---

### 2.12 `schema` group — introspection

```
schema list-stitch-types    [--json]
schema get-stitch-type      --type <name>  [--json]              # all params for a stitch type with type/default/min/max/enum
schema get-extension        --extension <name>  [--json]         # full INX-style schema for any inkstitch extension
schema list-commands        [--json]                             # available visual commands
schema list-machine-formats [--json]
```

This is what an agent calls to *discover* what's available. Output for `schema get-stitch-type`:

```json
{
  "stitch_type": "satin_column",
  "defining_attribute": { "name": "inkstitch:satin_column", "value": "True" },
  "geometry_requirements": ["stroke", "two_rails_with_rungs"],
  "params": [
    {
      "name": "pull_compensation_mm",
      "attribute": "inkstitch:pull_compensation_mm",
      "type": "float",
      "unit": "mm",
      "default": 0.0,
      "min": -10.0, "max": 10.0,
      "gui_text": "Pull compensation",
      "description": "Additional pull compensation per side (millimeters)."
    },
    …
  ]
}
```

Cached schema source: §3.

---

### 2.13 `session` group

```
session status     --project <abs>                               # current SVG path, history cursor, dirty flag
session undo       --project <abs>  [--steps <n>]                # default 1
session redo       --project <abs>  [--steps <n>]
session history    --project <abs>  [--limit <n>] [--json]
session reset      --project <abs>                               # drops history; current SVG state retained
```

History depth is **50** (matches inkscape skill).

---

## 3. INX / Param Schema Strategy

### 3.1 Source of truth

The repo's `inx/` directory is **not** checked in — INX files are generated from Jinja2 templates in `inkstitch/templates/` by `bin/generate-inx-files`. The actual canonical schema is the `@param(...)` decorator usage in `lib/elements/{fill_stitch,satin_column,stroke,…}.py` and in the extension classes.

### 3.2 Build-time schema extraction

The harness ships a `bin/extract-schema` script run at install time. It does, in order:

1. If the target inkstitch install has pre-generated INX files, parse those (lxml: each `<param name=… type=… min=… max=…>` plus `<option value=…>` children) into a normalized dict.
2. If not, run inkstitch's own `bin/generate-inx-files` against a temp directory and parse the result.
3. Independently, import `inkstitch.lib.elements` and walk the `@param(...)` decorators on `FillStitch`, `SatinColumn`, `Stroke`, etc. (introspect via `inspect.getsource` or by importing the param decorator module and reading its registry). Use this to fill in fields the INX doesn't expose: `unit`, `select_items` (conditional visibility), `sort_index`.
4. Cross-reference (1)+(3); flag any params present in code but not INX as `gui_hidden=true`.
5. Write the merged schema to `~/.cache/cli-anything-inkstitch/schema-<inkstitch-version>.json`.

### 3.3 Cache file shape

```jsonc
{
  "inkstitch_version": "3.1.0",
  "extracted_at": "2026-04-26T18:00:00Z",
  "namespace": "http://inkstitch.org/namespace",
  "stitch_types": {
    "satin_column": {
      "defining_attribute": { "name": "satin_column", "value": "True" },
      "geometry_requirements": ["stroke"],
      "params": {
        "pull_compensation_mm": {
          "attribute": "pull_compensation_mm",
          "type": "float", "unit": "mm",
          "default": 0.0, "min": -10.0, "max": 10.0,
          "gui_text": "Pull compensation",
          "description": "…",
          "sort_index": 10
        }
      }
    }
  },
  "extensions": { /* full INX dump per extension, keyed by extension name */ },
  "commands": [
    { "id": "inkstitch_stop", "label": "Stop", "scope": "element" },
    { "id": "inkstitch_trim", "label": "Trim", "scope": "element" },
    …
  ],
  "machine_formats": [
    { "ext": "dst", "writer": true,  "reader": true,  "description": "Tajima" },
    …
  ]
}
```

### 3.4 Runtime schema lookup

All `params` and `schema` commands consult the cache. If missing, they auto-trigger extraction. `--refresh-schema` forces re-extraction. Schema is keyed by inkstitch binary version (resolved via `inkstitch --version`); upgrading inkstitch invalidates the cache automatically.

### 3.5 Param naming convention

- Schema attribute names are stored *without* the `inkstitch:` prefix.
- CLI flags are kebab-case versions of the attribute name: `inkstitch:row_spacing_mm` ⇒ `--row-spacing-mm`.
- The `_mm` / `_percent` / `_deg` suffix is preserved in both (no implicit unit conversion at the CLI level).

---

## 4. Implementation Notes

### 4.1 Language and dependencies

- **Python 3.10+** (matches inkstitch's modern minimum).
- **Click 8.x** for the CLI tree (preferred over argparse — group/subgroup ergonomics, automatic `--json` flag inheritance via a custom `Group` subclass).
- **lxml ≥ 4.9** for SVG manipulation (must register the inkstitch namespace in the `nsmap`).
- **pyembroidery ≥ 1.5** for format introspection.
- **rich** for human-readable output (tables, color, indented dumps). Disabled when `--json`.
- **filelock** for project JSON write safety (REPL + concurrent CLI calls).
- No dependency on inkex at runtime — the harness must be installable without the full inkstitch Python environment.

### 4.2 Repo layout

```
cli-anything-inkstitch/
├── pyproject.toml
├── README.md
├── SPEC.md                          # this document
├── skills/
│   └── cli-anything-inkstitch/
│       └── SKILL.md
├── src/
│   └── cli_anything_inkstitch/
│       ├── __init__.py
│       ├── __main__.py              # entry point
│       ├── cli.py                   # Click root + global flags
│       ├── project.py               # ProjectFile dataclass + load/save + locking
│       ├── history.py               # patch types, apply/reverse, ring buffer
│       ├── svg/
│       │   ├── document.py          # lxml wrapper, namespace registration
│       │   ├── elements.py          # element type dispatch (mirror inkstitch logic)
│       │   ├── attrs.py             # qname helpers, get/set inkstitch:* attrs
│       │   └── commands.py          # visual command insert/remove
│       ├── schema/
│       │   ├── extract.py           # bin/extract-schema implementation
│       │   ├── cache.py             # load/save the cache JSON
│       │   └── validate.py          # schema-driven param validation
│       ├── commands/                # one module per command group
│       │   ├── document.py
│       │   ├── element.py
│       │   ├── params.py
│       │   ├── commands_group.py
│       │   ├── tools.py
│       │   ├── validate.py
│       │   ├── preview.py
│       │   ├── export.py
│       │   ├── schema_group.py
│       │   └── session.py
│       ├── binary.py                # inkstitch binary discovery + invocation
│       └── repl.py                  # REPL loop
├── bin/
│   └── extract-schema               # thin wrapper over schema/extract.py
└── tests/
    ├── fixtures/
    │   ├── empty.svg
    │   ├── one_path.svg
    │   └── multi_element.svg
    ├── test_project.py
    ├── test_history.py
    ├── test_params.py
    ├── test_schema_extraction.py
    └── test_e2e_export.py           # requires inkstitch installed
```

### 4.3 Namespace handling

```python
# svg/attrs.py
INKSTITCH_NS = "http://inkstitch.org/namespace"
INKSCAPE_NS  = "http://www.inkscape.org/namespaces/inkscape"
SVG_NS       = "http://www.w3.org/2000/svg"
XLINK_NS     = "http://www.w3.org/1999/xlink"

NSMAP = {
    None:        SVG_NS,
    "inkstitch": INKSTITCH_NS,
    "inkscape":  INKSCAPE_NS,
    "xlink":     XLINK_NS,
}

def qname(local: str, ns: str = INKSTITCH_NS) -> str:
    return f"{{{ns}}}{local}"

def get_inkstitch(node, name: str, default=None):
    return node.get(qname(name), default)

def set_inkstitch(node, name: str, value):
    node.set(qname(name), str(value))
```

When loading an SVG, ensure the root element has `xmlns:inkstitch="http://inkstitch.org/namespace"`. lxml will preserve this if it was already declared; if not, the harness adds it on first `set_inkstitch` call by re-creating the root with an extended `nsmap` (lxml gotcha: `nsmap` is immutable once a node is created, so this requires a tree rebuild — implement once in `svg/document.py`).

### 4.4 Boolean encoding

Ink/Stitch uses Python-cased `True`/`False` (capital first letter), not XML `true`/`false`. The harness must write `"True"` / `"False"` strings and accept both casings on read.

### 4.5 Binary discovery

```python
# binary.py
SEARCH_PATHS = {
    "darwin":  ["/Applications/Ink Stitch.app/Contents/MacOS/inkstitch",
                "~/Applications/Ink Stitch.app/Contents/MacOS/inkstitch"],
    "linux":   ["/opt/inkstitch/bin/inkstitch",
                "/usr/local/bin/inkstitch"],
    "win32":   [r"C:\Program Files\Ink Stitch\inkstitch.exe",
                r"C:\Program Files (x86)\Ink Stitch\inkstitch.exe"],
}
```

Resolution order:
1. `--inkstitch-binary` flag (any command)
2. `INKSTITCH_BINARY` env var
3. Project JSON `session.inkstitch_binary`
4. `which inkstitch` / `where inkstitch`
5. Platform default search paths

If not found, exit 3 with an install pointer (`https://inkstitch.org/docs/install/`).

### 4.6 Binary invocation pattern

```python
# binary.py
def run_extension(binary, extension, svg_path, args=None, ids=None, capture_stdout=False):
    cmd = [binary, f"--extension={extension}"]
    for k, v in (args or {}).items():
        cmd.append(f"--{k}={v}")
    for ident in (ids or []):
        cmd.append(f"--id={ident}")
    cmd.append(svg_path)
    env = {**os.environ, "INKSTITCH_OFFLINE_SCRIPT": "true"}
    result = subprocess.run(cmd, env=env, capture_output=True, check=False, timeout=300)
    if result.returncode != 0:
        raise BinaryError(extension, result.returncode, result.stderr.decode("utf-8"))
    return result.stdout if capture_stdout else None
```

`INKSTITCH_OFFLINE_SCRIPT=true` is critical — it disables Inkscape-specific GUI code paths (verified in `inkstitch.py:74`). Without it, the binary may try to initialize wxPython on macOS and crash.

For extensions that *modify* the SVG in place (auto-satin, convert-to-satin, troubleshoot, stitch_plan_preview), the binary writes to stdout the full modified SVG. The harness:
1. Snapshots the affected subtree (`subtree_replace` patch).
2. Captures stdout into a temp file.
3. Diffs to confirm the change matches the expected scope.
4. Writes the new SVG and records the patch.

### 4.7 Error model

| Exception | Exit | When |
|---|---|---|
| `UserError` | 1 | Bad flag, unknown id, invalid stitch_type for geometry |
| `ProjectError` | 2 | Project JSON missing/corrupt, SVG parse fail, sha256 mismatch |
| `BinaryError` | 3 | inkstitch binary not found, returns non-zero, times out |
| `ValidationError` | 4 | Only raised under `--strict`; otherwise warnings go to stderr |

All exceptions render as `error: <message>` on stderr; `--json` mode wraps them as `{"error": {"type": "...", "message": "..."}}` on stdout *and* still uses the exit code.

### 4.8 SVG mutation safety

- Always re-parse the SVG from disk at command start; never trust an in-memory cache between CLI invocations (REPL excepted).
- After mutating, write to a sibling temp file then `os.replace()` for atomicity.
- Compute and store the SHA-256 in project JSON; on next invocation, if it disagrees with the on-disk file, refuse to mutate without `--force` and emit `error: SVG modified outside cli-anything-inkstitch since last command (use --force to proceed)`.

### 4.9 Concurrency

`filelock` on the project JSON path. Held for the entire command duration. REPL holds it for the session.

### 4.10 Logging

Default WARNING to stderr. `--verbose` ⇒ INFO. `--debug` ⇒ DEBUG with subprocess command lines logged. Never log to stdout.

### 4.11 Testing strategy

- **Unit tests**: schema extraction (with a fixture inkstitch checkout), patch apply/reverse round-trip, namespace handling, binary discovery (mocked).
- **Integration tests**: full command runs against fixture SVGs, asserting attribute writes match expected.
- **End-to-end tests** (gated on `INKSTITCH_BINARY` env var, skipped in CI by default): `params set` → `validate run` → `preview stats` → `export file --format dst`, asserting non-empty output and exit 0.
- Schema cache versioning: tests must pass against at least the two latest released inkstitch versions.

### 4.12 What is explicitly out of scope for v1.0

- No bitmap/PNG-to-SVG tracing (use cli-anything-inkscape upstream).
- No font/text-to-stitch conversion (Ink/Stitch's `lettering` extension is not wrapped).
- No live machine output (no USB/serial transport).
- No fill ordering optimization beyond what `auto-satin` / `auto-run` provide.
- No multi-hooping or hoop splitting (cleanly out of scope for the CLI; users should use Ink/Stitch's own `auto_split_satin` if needed and we'll wrap it in v1.1).

---

## 5. Worked End-to-End Flow

A full agent-driven digitization, illustrating how the surface composes:

```bash
PROJ=/tmp/logo.inkstitch-cli.json

# 0. Open
cli-anything-inkstitch document open --project $PROJ --svg /tmp/logo.svg
cli-anything-inkstitch document set-hoop --project $PROJ --name 100x100
cli-anything-inkstitch document set-machine-target --project $PROJ --format dst

# 1. Inspect
cli-anything-inkstitch --json element list --project $PROJ --refresh
# → JSON enumerates "logo_outline" (path, has fill+stroke), "logo_text" (path, fill only)

# 2. Discover what's possible
cli-anything-inkstitch --json schema list-stitch-types
cli-anything-inkstitch --json schema get-stitch-type --type satin_column

# 3. Assign stitch types + params
cli-anything-inkstitch params set --project $PROJ --id logo_outline \
    --stitch-type satin_column --pull-compensation-mm 0.4 --zigzag-spacing-mm 0.35 \
    --contour-underlay true --contour-underlay-inset-mm 0.4

cli-anything-inkstitch params set --project $PROJ --id logo_text \
    --stitch-type auto_fill --angle 45 --row-spacing-mm 0.25 --fill-underlay true

# 4. Add a thread trim before the text starts
cli-anything-inkstitch commands attach --project $PROJ --id logo_text --command trim

# 5. Validate
cli-anything-inkstitch --json validate run --project $PROJ --strict || \
    cli-anything-inkstitch validate run --project $PROJ   # show human-readable on failure

# 6. Preview
cli-anything-inkstitch preview generate --project $PROJ --out /tmp/logo-preview.svg
cli-anything-inkstitch --json preview stats --project $PROJ
# → { "stitch_count": 4820, "color_stops": [...], "estimated_time_seconds": 195, ... }

# 7. Export
cli-anything-inkstitch export file --project $PROJ --format dst --out /tmp/logo.dst
cli-anything-inkstitch export zip  --project $PROJ --formats dst,pes,jef --out /tmp/logo.zip

# 8. Iterate (oops, density too high)
cli-anything-inkstitch params set --project $PROJ --id logo_outline --zigzag-spacing-mm 0.4
cli-anything-inkstitch session history --project $PROJ
cli-anything-inkstitch session undo --project $PROJ           # roll back last density tweak
cli-anything-inkstitch document save --project $PROJ
```

This sequence is what the SKILL.md surfaces directly to the agent.

---

## 6. Open Items (to confirm before implementation)

These are *very small* loose ends, not blocking design choices:

1. **Preset directory location on Windows** — proposed `%APPDATA%\cli-anything-inkstitch\presets\`; confirm matches inkscape skill's convention.
2. **REPL command prefix** — `:` for meta-commands (`:save`, `:exit`, `:help`) seems right; confirm against inkscape skill's choice.
3. **`element list` field `label`** — read from `inkscape:label` attribute or fall back to `<title>` child or `@id`?  Recommend: prefer `inkscape:label`, then `<title>`, then `@id`.
4. **Schema cache invalidation when inkstitch is reinstalled in place** — version string can be identical; recommend hashing the binary too. Cheap (4 KB read).
