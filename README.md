# cli-anything-inkstitch

Stateful CLI for machine-embroidery digitization with Ink/Stitch — built so an LLM (or a human at a terminal) can take a vector design from raw SVG to stitchable DST/PES/JEF without leaving the command line.

See [SPEC.md](SPEC.md) for the full technical specification, [skills/cli-anything-inkstitch/SKILL.md](skills/cli-anything-inkstitch/SKILL.md) for agent-facing CLI usage, and [skills/embroidery-digitization/SKILL.md](skills/embroidery-digitization/SKILL.md) for the parameter-decision wisdom that informs *which* settings to pick.

## Install (dev)

```bash
pip install -e '.[dev]'
```

The CLI itself is pure Python (lxml + click + pyembroidery). For binary-backed commands (`tools`, `preview generate`, `export`, `validate run/fix`) install [Ink/Stitch](https://inkstitch.org/docs/install/). For raster previews (`preview generate --raster`, `preview rasterize`) install Inkscape 1.0+.

## Quick start

```bash
PROJ=/tmp/logo.inkstitch-cli.json

# Open an SVG into a project, prep it (assigns IDs, inlines CSS-class fills,
# detects Illustrator stroke-to-outline rings)
cli-anything-inkstitch document open --project $PROJ --svg /tmp/logo.svg
cli-anything-inkstitch document prep --project $PROJ --illustrator-rings satin

# Capture material/intent so subsequent param decisions are grounded
cli-anything-inkstitch document set-context --project $PROJ \
    --material "knit cotton t-shirt" --stretch high \
    --thread "40wt polyester" --stabilizer "medium cut-away" \
    --intent "wash-durable team logo"

# Get rich per-element context (size, position, neighbors, color name)
cli-anything-inkstitch --json element describe --project $PROJ

# Set stitch type and parameters
cli-anything-inkstitch params set --project $PROJ --id logo_outline \
    --stitch-type satin_column --pull-compensation-mm 0.4

# Validate and auto-fix what can be fixed
cli-anything-inkstitch --json validate fix --project $PROJ

# Preview the stitch plan as PNG so the LLM (or you) can visually evaluate
cli-anything-inkstitch preview generate --project $PROJ \
    --out /tmp/logo-preview.svg --raster --dpi 200

# Export
cli-anything-inkstitch export file --project $PROJ --format dst --out /tmp/logo.dst
```

## What's in here

**Core CLI** (Python):
- `document` — project & SVG management, prep for Illustrator-exported SVGs, design-intent context capture, palette + thread color enumeration
- `element` — list, get, describe (with bbox/position/color/neighbors), classify, delete, clear-params
- `params` — set, get, copy, presets — with full inkstitch param-schema validation
- `commands` — attach/detach Ink/Stitch visual commands (stops, trims, fill markers)
- `validate` — static schema checks, binary-backed troubleshoot run, fix dispatcher (auto + manual suggestions)
- `tools` — binary-backed geometry rewrites (auto-satin, auto-run, convert satin↔stroke, cleanup)
- `preview` — stitch-plan SVG generation, raster (PNG) output via Inkscape, parsed stats
- `export` — single-format file, multi-format ZIP with optional threadlist
- `schema` — introspect the inkstitch param schema (extracted from inkstitch source)
- `session` — undo / redo / history (50 levels)

**Two skills**:
- [`cli-anything-inkstitch`](skills/cli-anything-inkstitch/SKILL.md) — agent-facing CLI usage: every command, every flag, every output format, agent gotchas.
- [`embroidery-digitization`](skills/embroidery-digitization/SKILL.md) — the wisdom layer: per-element decision flow, fabric-specific push/pull comp tables, satin width thresholds, underlay strategies, color/needle ordering, visual failure-mode diagnosis, three full starting recipes.

## Status

v0.1 — core surface complete. ~180 tests cover both pure-Python paths and binary-mocked integrations; the binary-backed commands are also live-verified against Ink/Stitch v3.2.2 on macOS.

Out of scope (per [SPEC.md §4.12](SPEC.md)): GUI, Inkscape extension wrapping, lettering UI, real-time machine control.

## Tests

```bash
pytest          # full suite
pytest -k fix   # one area
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
