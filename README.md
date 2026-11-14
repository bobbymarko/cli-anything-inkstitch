# cli-anything-inkstitch

Stateful CLI for machine-embroidery digitization with Ink/Stitch — built so an LLM (or a human at a terminal) can take a vector design from raw SVG to stitchable DST/PES/JEF without leaving the command line.

See [SPEC.md](SPEC.md) for the full technical specification, [skills/cli-anything-inkstitch/SKILL.md](skills/cli-anything-inkstitch/SKILL.md) for agent-facing CLI usage, and [skills/embroidery-digitization/SKILL.md](skills/embroidery-digitization/SKILL.md) for the parameter-decision wisdom that informs *which* settings to pick.

## Install (dev)

```bash
pip install -e '.[dev]'          # add ,satin for text-to-satin / shapes-to-satin
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
- `font` — create, import, calibrate, and validate Inkstitch-compatible embroidery fonts from per-letter DST/PES files, with optional Embrilliance BX file for precise baseline calibration
- `artifact` — the digitizing-artifact correction loop: a browser editor for human correction of satin rails, rungs, and fill start/end handles, bidirectional with the agent (long-poll feedback, chat, live reload, stitchability gate) — see [docs/digitizing-artifact-spec.md](docs/digitizing-artifact-spec.md)

**Two skills**:
- [`cli-anything-inkstitch`](skills/cli-anything-inkstitch/SKILL.md) — agent-facing CLI usage: every command, every flag, every output format, agent gotchas.
- [`embroidery-digitization`](skills/embroidery-digitization/SKILL.md) — the wisdom layer: per-element decision flow, fabric-specific push/pull comp tables, satin width thresholds, underlay strategies, color/needle ordering, visual failure-mode diagnosis, three full starting recipes.

## Planner regression suite

A planner change is judged on the stitches the engine will sew (`engine_gate`), across fonts and
shapes, against a baseline. **Run the suite before and after any planner change and attach the
`regress compare` output to the PR.**

```bash
# the baseline: the branch you are about to change (once per base commit)
cli-anything-inkstitch regress run --results /path/to/base.json
# ... change the planner ...
cli-anything-inkstitch regress run --results /path/to/new.json
cli-anything-inkstitch regress compare /path/to/base.json /path/to/new.json --out /path/to/compare
```

- Cases are declared in [`regress/cases.json`](src/cli_anything_inkstitch/regress/cases.json),
  public and synthetic only: lettering through `tools text-to-satin` (sans = Lato Black, vendored
  under `tests/fixtures/fonts` with its OFL licence; serif = Liberation Serif; script = Pacifico and
  the connected Kaushan Script with `--merge-joins`; fonts that are not installed skip) and synthetic
  joint shapes through `tools shapes-to-satin --fabric twill` (T, L, V, M, X crossing, ring with a
  tail, band meeting a pointed strand, 12 mm stroke, tight curve). `text-to-satin` has no fabric preset;
  it runs with its own defaults, as in production.
- `--quick` runs about six cases (a few minutes); the full set takes longer. `--only id,id` and
  `--list` select and show cases.
- `--cases FILE` adds a case file kept outside the repo, for client art and fonts that must never
  enter it (same format; paths may use `~`, `$REPO`, or be relative to the file). Its results and
  contact sheets go wherever `--results` points, so keep those outside the repo too.
- Per case, from the engine's stitches: satin columns, multi-pass columns (and which, by letter or
  shape), trims, untrimmed moves, longest stitch, stitch count, runtime, deltas against a reference
  DST when the case names one, and a contact sheet PNG (`<results stem>_sheets/<case>.png`: stitch
  lines in sewing order, blue to red; white rings are trims; red lines are untrimmed moves over 3 mm;
  magenta outlines are multi-pass columns).
- `regress compare base.json new.json` prints a per-case table, writes a side-by-side image for each
  changed case, and exits 1 on a regression: any multi-pass increase, a new untrimmed move over 3 mm,
  a longest stitch over 8 mm (only when worse than the baseline's), trims up by more than
  max(2, 10%), or a case that now fails. A stitch count moving more than 5% is a warning. The
  thresholds are named constants at the top of
  [`regress/compare.py`](src/cli_anything_inkstitch/regress/compare.py).
- A regression the owner has agreed to is listed as an accepted change: `{case, metric, reason, max?}`
  in [`regress/accepted.json`](src/cli_anything_inkstitch/regress/accepted.json) for public cases,
  and in a file beside the private case files, passed with `regress compare --accepted FILE`. The
  compare reports it as `accepted` with its reason and does not fail on it while the value stays at or
  under `max`. An entry that no longer matches anything is listed as stale.
- To measure a PR branch without checking it out, point the suite at its source:
  `python tools/regress_with_src.py /path/to/branch/src --label branch@rev -- regress run --results new.json`
  (that checkout's planner sews the cases; this one supplies the suite and the engine gate; the
  checkout is not modified; `git archive <branch> src` makes a throwaway copy).
- **Contender: Bold Bobbin.** `regress run --versus --results vs.json` sews the same words in the same
  fonts (vendored Lato Regular, Lora, Great Vibes) with our planner and with the Bold Bobbin CLI
  (`$BOLDBOBBIN_CLI`, default `~/code/ext/boldbobbin-cli/1.4.4/boldbobbin`); `regress versus vs.json`
  prints planner|contender per case (stitches, trims, untrimmed moves over 3 mm, longest stitch, time, ink
  size) with a side-by-side image each. Its numbers come from its exported DST; its multi-pass is n/a.
  BB's `sizeMm` is an em size, so it is set from the ink height of the planner's own "H"; it runs with the
  app's defaults, without `optimizePorts`. Its exports are licensed to the signed-in account: keep its DSTs,
  sheets and results outside the repository (the suite ships only the harness).
- Runs are sequential: each case takes the host render lock (`/tmp/inkstitch-render.lock`) around
  every engine run, as `engine_gate` does. Do not wrap `regress run` in another `flock` on it.

## Status

v0.1 — core surface complete. ~430 tests (428 passing, 2 xfailed) cover both pure-Python paths and binary-mocked integrations; the binary-backed commands are also live-verified against Ink/Stitch v3.2.2 on macOS.

Out of scope (per [SPEC.md §4.12](SPEC.md)): GUI, Inkscape extension wrapping, lettering UI, real-time machine control.

## Tests

```bash
pytest          # full suite
pytest -k fix   # one area
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
