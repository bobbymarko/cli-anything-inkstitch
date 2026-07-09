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
        artifact.py         # Artifact command group: open/poll/reply/gate/end/stop
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
