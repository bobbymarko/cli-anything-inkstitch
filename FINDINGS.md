# Architectural Review Findings — working doc

Temp tracking doc for the 2026-06-12 architecture review. Delete when all items are resolved.
Baseline before any fixes: **289 passed, 2 xfailed**. After fixes: **338 passed, 2 xfailed**
(49 new tests). Every fix kept the suite green.

**Remaining open:** H2c only (ship pre-extracted schema in the wheel — release-time work;
the H2a warning covers the immediate risk).

Status legend: `[ ]` todo · `[~]` in progress · `[x]` fixed + tested · `[-]` won't fix (reason noted)

---

## High priority

### H1. `font.py` monolith bypasses shared infrastructure `[ ]`
`commands/font.py` is 2,541 lines (~40% of codebase), seven responsibilities, and duplicates
instead of reusing shared code:
- Own `_load_font_svg`/`_save_font_svg` instead of `svg/document.py` `load_svg`/`save_svg`
- Own `_find_elem_by_id` (font.py:348) duplicating `svg/document.find_by_id`
- Own namespace map (font.py:23-30) duplicating `svg/attrs.py`
- Hand-rolled PNG encoder (~165 lines, font.py:1024+) — Pillow is now a hard dep used by preview.py
- 34 pure-logic helpers with zero Click dependency locked in a commands module, mostly untested
  (BX parsing, baseline detection, char-name parsing)

Plan (incremental, each step testable):
- H1a `[x]` Replace SVG load/save + find-by-id duplication with `svg/` reuse.
  **Fixed:** extracted shared `parse_svg`/`write_svg_atomic` primitives in svg/document.py
  (font SVGs keep their own version marker + pretty-printing, so wholesale `load_svg` reuse
  would have corrupted them); font.py now delegates. NSMAP was already partially shared.
- H1b `[x]` Replace hand-rolled PNG encoder with Pillow.
  **Fixed:** `_write_png`/`_draw_line_img` (~60 lines stdlib PNG + Bresenham) replaced with
  PIL Image/ImageDraw. Added pixel-decoding tests (size, ink presence, baseline color).
- H1c `[x]` Extract pure logic → `embroidery/` (file I/O, baseline/advance detection, BX parsing)
  and `font_format/` (font SVG building, font.json metadata, char parsing); add unit tests.
  **Fixed:** font.py 2526 → 1596 lines. New packages: embroidery/{files,analysis,bx}.py
  (179/215/241 lines), font_format/{svg_build,metadata}.py (335/51 lines). All moved names
  re-exported from commands.font for back-compat; existing unit tests (char parsing, BX glyphs,
  last-stitch baseline) now exercise the library modules through those re-exports.
- H1d `[x]` Document (or fix) the no-history-integration design choice for font commands.
  **Fixed:** design note in font.py module docstring — fonts are standalone portable assets,
  intentionally outside the project/undo model.

### H2. Fresh-install schema silently degrades `[ ]`
`schema/extract.py` needs Ink/Stitch *source* (sibling clone, macOS app bundle, /usr/share/inkstitch).
No Windows candidate. pip-install users silently fall back to thin `bootstrap.py` schema →
degraded `params set` validation with no warning.
Plan:
- H2a `[x]` Emit a visible warning (in `--json` payload too) when running on bootstrap schema.
  **Fixed:** bootstrap schema now carries `source.kind="bootstrap"`; `schema_warning()` helper in
  cache.py; surfaced as `schema_warning` field in schema list/get, params set, validate static.
- H2b `[x]` Add Windows source candidate path.
  **Fixed:** added Windows + Inkscape user-extension-dir + both macOS app spellings to
  DEFAULT_SOURCE_CANDIDATES (all gated by the lib/elements/element.py existence check).
  **Bonus bug found+fixed:** the "Set INKSTITCH_SOURCE" error message referenced an env var the
  code never read — find_inkstitch_source() now honors it (set-but-invalid = error, no fallthrough).
- H2c `[ ]` Ship pre-extracted schema snapshot in the wheel as fallback instead of hand-written
  bootstrap. (Deferred: needs a release-time step; warning from H2a covers the immediate risk.)

---

## Medium priority

### M1. History patches can bloat project JSON `[x]`
`subtree_replace` stores full before/after XML in the project file (history.py:40); 50-entry ring
buffer; file parsed+rewritten every command. Path-heavy designs → multi-MB project files.
**Fixed:** patches carrying >256KB of XML are recorded as a non-undoable `oversize` marker
(command + size kept, XML dropped); undoing one raises a clear ProjectError. Tested.

### M2. Inconsistent None semantics between patch types `[x]`
`attr_diff` treats None as "delete attribute" (history.py:146-148); `metadata_diff` undo
(session.py:122-131) assigns None instead of deleting the session key. Undoing a command that
*added* a key leaves `key: None` behind.
**Fixed:** session.py `_set_session_keys` now pops keys on None; covered in tests/test_history.py.
Related observation (new): `document set-units` / `set-machine-target` / `set-palette` mutate the
session without recording any history entry, unlike `set-hoop` → tracked as M5 below.

### M5. Some session mutations record no history `[x]`
`document set-units`, `set-machine-target` (document.py:157-174) and possibly `set-palette`/
`set-context` mutate `proj.session` without a `metadata_diff` history entry; `set-hoop` records
one. Undo silently skips them. Decide: record history for all, or document why not.
**Fixed:** shared `_record_session_change` helper; set-units / set-machine-target / set-palette /
set-collapse-len / set-min-stitch-len / set-binary / set-context all record history now. Undo/redo
of a palette change also re-syncs the SVG `<metadata>` thread-palette key. Tested.

### M3. Geometry ignores transforms; describe output silently wrong `[x]`
`svg/geometry.py` doesn't apply `transform`; arcs approximated by endpoints. `element describe`
reports wrong bbox/position for rotated/scaled elements — and LLM decisions are built on that.
**Fixed (beyond minimum):** full affine transform support in geometry.py (`parse_transform`,
`ctm_for`, `element_bbox_in_root` — translate/scale/rotate/matrix/skew composed through
ancestors). `element describe` now reports transformed bboxes, plus `has_transform` and
`bbox_approx` (rotation/skew = safe over-estimate) flags. 8 new unit tests.

### M4. Warnings bypass --json output `[x]`
Several font commands write fallback notices via `click.echo(err=True)` (e.g. silent
baseline-method switch in `font import`). `--json` consumers (the whole point of this CLI) never
see them. Plan: route warnings into `emit()` payloads as a `warnings: []` field; sweep all
commands for stderr-only warnings.
**Fixed:** both stderr-only sites (font import baseline fallbacks) now also append to a
`warnings` list in the JSON payload; stderr echo kept for humans. Schema degradation uses the
same pattern (`schema_warning` field, see H2a). Full-pipeline tests added (real DST files).

### M6. Schema goes stale across Ink/Stitch upgrades; no binary↔schema coherence `[x]`
The param schema changes with each Ink/Stitch release, but: (1) `load_schema` picks the newest
cache by *mtime* (cache.py:23) and nothing invalidates it when the user upgrades Ink/Stitch —
stale schema is used silently until a manual `--refresh-schema`; (2) the discovered binary
(binary.py) and the extraction source are independent — `schema.inkstitch_version` is never
compared to the binary's version; (3) CI extracts from inkstitch HEAD, a moving target vs.
users' release binaries. Blast radius is bounded (binary re-parses SVG attrs itself; stale
schema = wrong validation guidance, not corrupted output) but the LLM trusts validation errors.
**Fixed:** (1) `binary.detect_binary_version()` reads the VERSION file shipped with the binary
(candidate paths mirror inkstitch's own get_bundled_dir: Resources/ on macOS, alongside or one
level above the exe on Linux/Windows); (2) `load_schema(prefer_version=...)` selects the cache
matching the installed binary over newest-by-mtime; (3) `schema_warning(schema, binary_version)`
emits a mismatch notice in the JSON payload of schema list/get, params set, validate static —
only when both versions are release-like (a `src-<hash>` dev-clone schema isn't comparable);
(4) CI inkstitch checkout pinned to v3.2.2 with a bump note. 12 new tests incl. end-to-end
mismatch surfacing via a fake binary + INKSTITCH_BINARY.

---

## Small stuff

### S1. `ulid-py` is a dead dependency `[x]`
Declared in pyproject.toml, never imported (history.py:17 uses uuid4). Remove.
**Fixed:** removed from pyproject.toml.

### S2. `.DS_Store` not gitignored `[x]`
Sitting untracked in the working tree. Add to .gitignore.
**Fixed.**

### S3. Hardcoded `"→.svg"` filename ×7 in font.py `[x]`
Unicode-arrow filename is fragile (Windows shells, zips). Centralize as a constant; consider a
`FontDirectory` helper. (Renaming the file itself would break existing fonts — constant only.)
**Fixed:** `FONT_SVG_FILENAME` constant; all path-construction sites use it.

### S4. Index-based undo can delete wrong sibling after --force `[x]`
`node_insert`/`node_delete` undo uses bare index (history.py:166-193). After a `--force` open past
an external edit, undo can silently delete the wrong node. Plan: store tag (+id if present) in the
patch and verify before deleting.
**Fixed:** `_verify_indexed_node` in history.py checks tag+id against the recorded patch XML before
any index-based delete; raises ProjectError on mismatch. Covered in tests/test_history.py.

### S5. BX vendor magic numbers undocumented `[x]`
Thresholds 46/70/82 in BX parsing tested against five vendor packs; silent glyph-skips on
mismatch. Document assumptions; log skips into command output (ties into M4).
**Fixed:** named constants `_BX_DESCENDER_MIN_BF`/`_BX_DESCENDER_MAX_BF` with the empirical
extra_bf cluster table and the five calibrated vendor packs documented at the definition site.

### S6. No Windows CI job `[x]`
`win32` paths in binary.py/inkscape.py never exercised. Add a windows-latest job to ci.yml.
**Fixed:** `test-windows` job (windows-latest, py3.12, with inkstitch checkout) added to ci.yml.

---

## Explicitly healthy (no action)
- `svg/` layering clean: no Click imports, no cycles
- Typed errors → exit codes, single catch point in cli.py
- Atomic writes + file locking + sha256 external-edit guard
- Vendored clones gitignored; only 61 files tracked
- All binary calls mocked in tests; CI green without Ink/Stitch installed
