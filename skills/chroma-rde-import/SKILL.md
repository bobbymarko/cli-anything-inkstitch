---
name: "chroma-rde-import"
description: >-
  Convert a Melco/Chroma .rde design file into an Ink/Stitch-editable SVG, so it can be resized, re-parameterized, previewed and re-exported. Use whenever a .rde file appears — "convert this Chroma file", "open this .rde in Ink/Stitch", "resize this embroidery design", "preview this design" — or when reviewing/changing tools/rde_to_inkstitch.py. Covers running the converter, what it decides and why (fill vs satin vs run, counters, row spacing, trims), the verification protocol that catches its silent failure modes, its known limits, and the corpus regression harness. The format is undocumented and reverse-engineered, so its constants are measurements, not defaults to be tuned.
---

# Chroma .rde → Ink/Stitch SVG

`.rde` is Melco/Chroma's design format: an object list (outlines + baked
stitches + parameters), not a stitch file. Converting it to Ink/Stitch
**elements** — rather than importing the companion DST — is what makes a design
resizable: scaling baked stitches scales the density with them and wrecks the
sew-out, while elements are re-stitched by the engine at whatever size you pick.

```bash
python3 tools/rde_to_inkstitch.py design.rde design.svg
#   satin columns: 0  fills: 43  runs: 0  trims: 40
```

Output is a px-unit SVG (viewBox = width/height at 96 dpi, so engine tools need
no correction transform), one Inkscape layer per thread named from the file's
own thread table, elements in stitch order, and `inkstitch:*` params on each.
From there it is an ordinary project: `document open`, then the usual flow in
the **cli-anything-inkstitch** skill.

## What it decides, and on what evidence

| Decision | Evidence | Where |
|---|---|---|
| fill vs satin vs run | Chroma's own fill flag where readable (~68%), else stitch geometry — a satin reverses direction every stitch | `FILL_FLAG_OFFSET`, `satin_runs` |
| what a contour *is* | share of the object's own stitches inside it | `_contour_coverage` |
| counter (hole) vs stitched detail | thread density inside it vs the rest of its parent | `_is_hole` |
| row spacing | thread the original actually spent over the net area | `_row_spacing_mm` |
| trim after an object | gap to the next object > engine's `collapse_len` | `TRIM_MIN_JUMP_MM` |

**The numeric constants are measurements over the 127-design corpus, not
defaults.** `HOLE_THREAD_DENSITY_MAX = 0.25` sits in a gap in a bimodal
distribution; the contour block's `8 + 2*len(name)` offset holds for all 15476
objects seen. Re-measure before changing one — see CLAUDE.md, "Chroma .rde
conversion".

## Verify like this, every time

The format fails silent: a wrong read yields a valid SVG that is quietly the
wrong design. Exit codes, stitch counts and element counts have all been
green while the output was visibly wrong. So:

1. **Render the vectors and look at them.** Not the stitch plan — a stitch
   render hides geometry bugs. Every counter in a lettering design was filled
   solid while the plan looked plausible.
2. **Compare against the source artwork** when there is any — the .ai/.eps/.svg
   the design was drawn from. `pdftoppm -png -r 150 art.ai out` renders a
   PDF-compatible .ai. Check letter by letter; that comparison is what exposed
   both the filled counters and two letters that had come through as traces of
   their own stitches.
3. **Then check the engine's stitch plan**, for coverage and trims:
   `inkstitch --extension=stitch_plan_preview design.svg`, or
   `--extension=output --format=csv` and read the `COMMAND_TRIM` /
   `COMMAND_JUMP` counts in the header.
4. **Re-measure the corpus** if you touched the converter:
   `python3 tools/rde_regress.py record base.json tests/fixtures/rde`, make the
   change, then `check`. Name every design that moved.

## Known limits

- **Jump estimates are approximate on dense designs.** Trims are decided from
  Chroma's object gaps, but Ink/Stitch picks its own entry/exit points when it
  re-stitches, so it can open a jump the converter did not predict. On a spread
  out design the two agree exactly (proven in `TestAgainstTheEngine`); on a
  dense one, run Ink/Stitch's own **Jumps to Trims** afterwards — it skips
  anything already marked. Then refine with `tools optimize-trims`, which is
  the piece the converter cannot judge: whether later stitching covers the
  float. The rubric is embroidery-digitization §8.
- **Satins are rebuilt from stitch geometry**, not from stored rails — Chroma
  does not store a satin flag. Check `zigzag_spacing_mm` against the original.
- **~27% of objects use an outline preamble that is not mapped**; those fall
  back to measurement.
- Underlay is dropped on purpose: Ink/Stitch regenerates it. Set underlay
  params per embroidery-digitization §5 rather than reproducing Chroma's.

## The corpus

`tests/fixtures/rde/` is licensed commercial artwork — gitignored, never
committed, and every corpus-backed test skips without it. CI coverage comes
from `tests/rde_synth.py`, which builds .rde files from source (the cipher is a
symmetric XOR, so writing one is the decoder run backwards). A new rule needs
a synthetic case, or CI does not guard it.
