# cli-anything-inkstitch

Stateful CLI for machine-embroidery digitization with Ink/Stitch.

See [SPEC.md](SPEC.md) for the full technical specification and
[skills/cli-anything-inkstitch/SKILL.md](skills/cli-anything-inkstitch/SKILL.md)
for agent-facing usage.

## Install (dev)

```bash
pip install -e '.[dev]'
```

## Quick start

```bash
PROJ=/tmp/logo.inkstitch-cli.json
cli-anything-inkstitch document open --project $PROJ --svg /tmp/logo.svg
cli-anything-inkstitch --json element list --project $PROJ --refresh
cli-anything-inkstitch params set --project $PROJ --id logo_outline \
    --stitch-type satin_column --pull-compensation-mm 0.4
cli-anything-inkstitch session history --project $PROJ
```

## Status

v0.1 — core surface (lxml-only commands + binary stubs). See SPEC.md §4.12 for what's
out of scope. Binary-backed commands (`tools`, `preview generate`, `export`, `validate run`)
require the Ink/Stitch binary at runtime.

## Tests

```bash
pytest
```
