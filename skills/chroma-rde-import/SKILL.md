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

## Carrying over the digitiser's routing

Chroma does not store "start here, end there" as a setting -- it IS the
object's stitch stream, so the first and last stitch are the two points the
digitiser chose. Ink/Stitch reads them as commands, so they survive:

```bash
python3 tools/rde_start_end.py design.rde design.svg     # also works on a scaled copy
```

This fixes where each fill ENTERS and LEAVES (verified honoured to two decimal
places, at full size and scaled). It does not reproduce Chroma's section
ORDER in between: `auto_fill` decomposes a concave shape into sections and
routes them itself, and there is no knob for that. The travel between them is
underpath (`underpath` defaults True in fill_stitch.py), so it runs inside the
shape under the fill -- a stitch-count cost, not a visible one. Reproducing the
order means splitting the shape the way the digitiser would have, which is
re-digitising, not a parameter.

**Before believing a fill is broken, turn its underlay off and look again.**
Cross-hatch underlay covers the shape at +45 then -45 before the top fill
arrives, and in a preview that reads exactly like "it fills halfway then
finishes from the other direction". A sun that looked badly routed turned out
to be a single clean sweep with two underlay passes over it; the shapes that
really were splitting were the concave ones. Isolating the passes takes one
`params set --fill_underlay false` and costs less than an hour of theorising.

## Travel between shapes: the part that is invisible to counting

`collapse_len` (3 mm by default) decides what happens to the move from one
element to the next. **Longer than it, the machine jumps. Shorter, the machine
never lifts the needle and stitches straight through** — which lays real thread
across the garment between letters, and appears in no jump count, no trim
count, and nowhere in the editor's plan, because there is no gap there to draw.
In one 60-element crest, 46 of 59 transitions were stitched through like this
while every jump was correctly trimmed.

The engine reads it from the SVG's `<metadata>` (see CLAUDE.md rule 5), so it
only takes effect if it is written there. Lowering it converts those
stitched-through links into jumps, which `jump_to_trim` can then cut.

That is a trade with no free side, and it is worth stating to whoever is
paying for the sew-out:

* eliminating thread between letters needs a trim at each gap;
* every trim adds a tie-off and a tie-in, so the needle penetrations pile up at
  the start and end of each shape (32/mm² with no hotspots became 44/mm² in
  seven cells at 40 trims);
* fewer trims means less density and more thread on the front.

Lock style decides whether those ties are visible. `half_stitch` (the default)
doubles back along stitching that is already there. `simple`, `zigzag`, `arrow`
and the rest are protruding paths scaled by `lock_start_scale_mm` — 0.7 mm of
stitching sticking out of every shape, repeated at every tie point. `zigzag`
scored the best density of any style and looked the worst.

## Satins recovered from stitches

Rails are reconstructed by reading which side of the column each needle landed
on, so:

* a width that varies along a column is often **correct** — the crest's arch
  genuinely tapers 2.1 to 5.3 mm, and "the rails must be mis-paired" was wrong;
* pieces that abut in the artwork should stay separate. Joining two columns
  across a real gap fills it with a tapering sliver of satin that is not in the
  design. Chroma's own file keeps them separate for the same reason;
* if you do join, orientation must be chosen by testing all four combinations
  (either column reversed, rails swapped or not) and rejecting any where the
  two connecting segments **cross** — that is a twisted column, its zigzag
  doubles back over itself, and the density map cannot see it.

## Fonts

Chroma stores lettering as a font reference rather than an outline, which is
why those objects convert with no contours. The font name is recoverable: scan
the decrypted object payload for length-prefixed UTF-16 strings. The STS crest
gave up `DIN Condensed` (top arc) and `Avenir` (bottom arc), alongside `Satin`
and `100`. Useful when a row looks too light — a lighter face digitised at its
natural weight is not a digitising error, and rebuilding the text from the real
font beats widening rails traced from needle positions.

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
