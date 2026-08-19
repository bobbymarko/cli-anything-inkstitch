# CLAUDE.md

Stateful CLI for machine-embroidery digitization with Ink/Stitch — writes `inkstitch:*` XML attributes onto SVG elements, then delegates stitch generation / preview / export to the Ink/Stitch binary.

## Engine contract discipline (non-negotiable)

This tool's entire value is fidelity to the Ink/Stitch engine, and the engine
**fails silent**: invalid attr values, unknown command names, and unconnected
markers produce a perfectly valid stitch file that quietly ignores them. We
shipped three such bugs (invented `fill_start`/`fill_end` command names, a
command `<use>` structure the engine never reads, GUI-label dropdown values
where the engine stores indexes) — all plausible, all invisible to exit codes,
stitch counts, and our own unit tests. The engine source is checked out at
`./inkstitch` (also the schema-extraction source). Rules:

1. **No write without a read.** Before writing anything the engine consumes —
   attr names/values, XML structures, metadata keys, command names — find the
   engine-side READER in `./inkstitch/lib/` and cite it (file + symbol) in a
   code comment. If you cannot find the reader, stop and say so; do not
   interpolate a plausible format. Good examples: `svg/document.py` metadata
   helpers (cites `lib/metadata.py`), `svg/commands.py` (cites
   `lib/commands.py find_commands`).
2. **Enumerable engine facts are mined, never hand-written.** Params, command
   names, dropdown options and their value encodings come from
   `schema/extract.py` AST extraction. A hand-maintained list of engine facts
   is a bug (the bootstrap command list was one).
3. **Behavioral proof, not success signals.** Engine-facing changes ship with
   a differential test: drive the input two ways and assert the stitch plans
   differ (or are identical when they must be). Templates:
   `tests/test_extractor.py::TestEngineReadContract` (declared type vs read
   contract) and `tests/test_svg_commands.py::TestCommandsChangeStitchPlan`
   (marker moves must change the plan). Binary-backed tests use the
   `discover() is None` skipif pattern.
4. **Verify by looking, not counting.** For geometry/visual output, check the
   produced stitch geometry (segment-length distributions, rendered plan)
   or a screenshot — a plausible stitch count proved nothing when a satin's
   zigzag swept across the whole shape.
5. **Engine tools write px space; never read raw `d` from their output.**
   Extensions compute geometry in px and attach the inverse viewBox/ancestor
   transform (`lib/svg/path.py get_correction_transform`); some carry it as a
   `transform` attribute (fill_to_stroke), some bake it (auto_run). In a
   non-px-unit document the raw coordinates are off by the document scale
   (measured ×3.78) even though the SVG renders correctly — a transform-
   ignoring reader silently mis-measures everything (this cost a full design
   rebuild). All tool wrappers must go through `svg/units.py`:
   `bake_transforms` after every engine invocation, `check_scale_drift` as
   the backstop, `unit_scale_warning` at document open/prep. Prefer px-unit
   documents (viewBox = width/height at 96 px/inch) so the correction is
   identity. Related: never feed `autorun-underpath` elements back into
   `auto_run` — the router treats every stroke as art
   (`lib/stitches/auto_run.py autorun`) and re-routes travel, doubling the
   design; `tools auto-run` strips them (`_strip_stale_underpaths`).

## Chroma .rde conversion (`tools/rde_to_inkstitch.py`)

Same discipline, different source of truth. The converter reads a proprietary
format that no spec exists for, so its facts are **mined from the 127-design
corpus**, not reasoned out — and, like the engine, the format fails silent: a
wrong guess produces a valid SVG that is quietly the wrong design (a hand-set
name-field width cost two letters their outlines, and they came out as hairy
traces of their own stitches without erroring anywhere).

1. **The tuned constants are measurements. Do not round them.**
   `HOLE_THREAD_DENSITY_MAX` (0.25) sits in a gap in a bimodal distribution
   over 346 nested contours; `TRIM_MIN_JUMP_MM` (3.0) is the engine's own
   `collapse_len` default; the contour block's `8 + 2*len(name)` offset holds
   for all 15476 objects in the corpus. Changing any of them means re-running
   the measurement, not picking a rounder number.
2. **Re-measure the corpus before and after every change.**
   `tools/rde_regress.py record` then `check` reports which designs moved.
   Every moved design should be one you can name.
3. **Prefer the engine's own tool over a rule of our own.** Where Ink/Stitch
   ships the same decision — `lib/extensions/jump_to_trim.py` for trims — the
   converter's output is expected to match it element for element, and
   `tests/test_rde_convert.py::TestAgainstTheEngine` asserts that.
4. **Look at the art, not the stitch count.** Judge output against the source
   artwork (the .ai/.svg the design was drawn from), rendered as vectors. A
   stitch-plan render hides geometry bugs, and a plausible count proved
   nothing when every counter in the design was filled solid.

`tests/rde_synth.py` builds .rde files from source so the rules stay under
test in CI; the corpus itself is licensed artwork and is gitignored, so every
corpus-backed test skips there.

## Running tests

```bash
pip install -e '.[dev]'
pytest tests/
```

## Code layout

```
src/cli_anything_inkstitch/
    cli.py                  # Click root group + global flags (--json, --project, --verbose)
    commands/               # One file per command group
        document.py
        element.py
        params.py
        commands_group.py
        tools.py
        validate.py
        preview.py
        export.py
        schema_group.py
        session.py
        font.py             # Font command group (Click orchestration; pure logic lives in
                            # embroidery/ and font_format/, re-exported here for back-compat)
        artifact.py         # Artifact command group: open/poll/reply/gate/end/stop.
                            # `poll` stdout IS the human's message — run it tracked,
                            # never `>/dev/null &` (see SKILL.md "Polling discipline")
    artifact/               # Digitizing-artifact correction loop (docs/digitizing-artifact-spec.md)
        sessions.py         # session store: project-path identity, queued feedback, end semantics
        server.py           # stdlib HTTP server: long-poll, SSE, presence, live reload
        design_model.py     # design→editor JSON; edit ops routed through the project layer
        gate.py             # stitchability audit (rung pairing, widths, self-crossing, handles)
        editor/editor.html  # self-contained browser editor (canvas, Tier-1/2 preview, chat)
    embroidery/             # Pure logic: embroidery file I/O + analysis (no Click)
        files.py            # file discovery, filename→character parsing, DST/SVG unit constants
        analysis.py         # baseline detection, exit-advance detection, stitch→SVG paths
        bx.py               # Embrilliance BX binary parsing + descender thresholds
    font_format/            # Pure logic: Ink/Stitch font directory format (no Click)
        svg_build.py        # →.svg building/loading/saving, guides, path x-range helpers
        metadata.py         # font.json load/save/defaults
    svg/                    # lxml helpers (namespace registration, attr get/set, element dispatch,
                            # geometry incl. SVG transform support)
        satin.py            # WIP: geometric fill-to-satin conversion (parked on feat/fill-to-satin)
    schema/                 # INX/param schema extraction and cache. Degraded states surface as
                            # a schema_warning payload field: bootstrap fallback, or extracted
                            # schema version != installed binary version (read from the VERSION
                            # file shipped with the binary). INKSTITCH_SOURCE env var sets source.
    project.py              # ProjectFile dataclass, load/save, filelock
    history.py              # Patch types, apply/reverse, ring buffer, oversize-patch guard
    binary.py               # Ink/Stitch binary discovery and invocation
    repl.py                 # Interactive REPL loop
```

## Dependencies worth knowing

- **Pillow** (`Pillow>=10.0`) — required by `preview stitch-sim`. Added to `pyproject.toml`; install automatically via `pip install -e '.[dev]'`.

## BX test fixtures

`tests/fixtures/bx/*.bx` files are **not committed** — they are licensed commercial embroidery fonts. BX-dependent tests in `tests/test_font.py` are decorated with `@pytest.mark.skipif` and skip silently when the fixtures are absent. The suite passes fully without them.

## Key reference files

- `SPEC.md` — full technical specification (command surface, project JSON schema, binary invocation, error model)
- `skills/cli-anything-inkstitch/SKILL.md` — agent-facing usage doc (every command, every flag, examples)
- `skills/embroidery-digitization/SKILL.md` — parameter-decision wisdom (stitch type selection, fabric-specific starting numbers, failure-mode diagnosis)
