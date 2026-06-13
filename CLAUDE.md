# CLAUDE.md

Stateful CLI for machine-embroidery digitization with Ink/Stitch — writes `inkstitch:*` XML attributes onto SVG elements, then delegates stitch generation / preview / export to the Ink/Stitch binary.

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
