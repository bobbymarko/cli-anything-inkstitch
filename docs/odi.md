# ODI: Open Digitizing Interchange (draft 0.1)

ODI is a neutral, object-level JSON description of an embroidery design. A source
format is read *into* ODI and a target is written *from* it, so N formats need
2N converters rather than N-squared. Today: `.rde` (Chroma) in, Ink/Stitch SVG
out. Further sources (other design formats, stitch-only files) are planned.

Code: `src/cli_anything_inkstitch/odi/`. Check a file with
`python -m cli_anything_inkstitch.odi validate design.odi.json`, write SVG with
`... to-svg design.odi.json out.svg`.

## Rules

1. **Geometry is the source of truth; stitches are evidence.** Targets that
   re-stitch need outlines and parameters. Baked stitches (optional) let a
   converter measure what was not decoded and verify the result.
2. **Every value carries provenance**: `decoded` (read from the file),
   `measured` (derived from stitches), `defaulted`, or `user`, optionally with a
   confidence (`measured:0.92`). It lives in each object's `provenance` map keyed
   by dotted path (`geometry`, `params.angle`, `connect.trim_after`). A converter
   must never present a measured value as decoded.
3. **Lossless passthrough.** Anything understood but not mapped goes under an
   `x-<vendor>` key (on the document, an object or a thread). Any other unknown
   key is a validation error.
4. **One unit system**: millimetres, y down, angles in degrees (as Ink/Stitch's
   `angle`). Coordinates stay in the source's own space; `extents` says where the
   design sits and writers place it from there.
5. **Order is meaning**: `objects[]` is the sewing order.

Ink/Stitch parameter names and units are used wherever a concept matches, so the
SVG writer is close to a rename-free copy.

## Shape

```jsonc
{
  "odi": "0.1", "units": "mm",
  "source":  { "format": "rde", "grade": "object", "file_sha256": "...", "converter": "..." },
  "extents": { "min": [-51.4, -13.1], "max": [51.4, 13.1] },
  "engine":  { "collapse_len_mm": 3.0, "min_stitch_len_mm": 0.1 },   // optional, SVG <metadata>
  "threads": [ { "id": "t0", "rgb": "#048dad", "code": "1295", "name": "Cyan", "index": 0 } ],
  "objects": [
    { "id": "rde1", "type": "fill", "thread": "t0",
      "geometry": { "shapes": [ { "outer": "M x,y C ... Z", "holes": ["M ... Z"] } ] },
      "params":   { "angle": 45.0, "expand_mm": 0.2, "row_spacing_mm": 0.31 },
      "connect":  { "trim_after": true },
      "provenance": { "geometry": "decoded", "params.angle": "measured",
                      "params.expand_mm": "defaulted", "connect.trim_after": "measured" },
      "x-chroma": { "source_object": 0 } }
  ],
  "x-chroma": { "skipped_objects": 0 }
}
```

Object types: `run` (`path`), `satin` (`rails` pair, or `centerline` +
`width_mm` which may be `[[t, width], ...]`), `fill` (`shapes`: outer plus
holes; one object may be several islands), `manual` (stitches only),
`group` (`children`), `applique` (`outline`; its pass objects are `children`),
`lettering` (`params.text`/`font`; decomposed glyph objects under `glyphs`).
Paths are SVG path strings in mm using absolute `M L C Z` only. Stitches are
`[x, y, "STITCH"|"JUMP"|"TRIM"|"COLOR_CHANGE"|"STOP"|"END"]` (pyembroidery's
command names), per object or per document.

## Writers must not be silently lossy

`write_inkstitch` returns `(svg, report)`. `report.dropped` lists everything ODI
said that the writer could not express: underlay passes, entry/exit points,
parameters with no mapped Ink/Stitch attribute, lettering without glyphs,
non-stitch commands in manual objects. `report.provenance` is a histogram of
the tags in the input.

## Draft status

Not yet implemented: a JSON Schema file (the hand-written validator is
normative for now), colorways, previews, document-level stitch attribution
(`object_spans`), underlay mapping, and entry/exit points as Ink/Stitch commands
(`tools/rde_start_end.py` still does that for RDE).
