---
name: "embroidery-digitization"
description: >-
  Decision wisdom for machine embroidery digitization. Use whenever assigning stitch types or parameters to SVG elements via cli-anything-inkstitch (or reasoning about why an embroidery preview looks wrong). Covers the per-element decision tree (stitch type → fill direction → spacing → underlay → compensation), fabric-specific starting points for push/pull comp and density, satin width thresholds, underlay strategies, color/needle ordering, and visual failure modes seen in stitch-plan previews. Numbers given are *starting points* — embroidery is empirical and every fabric/thread/stabilizer combination behaves differently. Always sample-stitch before committing to a final design.
---

# Embroidery Digitization Wisdom

This skill encodes the practical knowledge a digitizer applies when turning a vector design into stitch parameters. Read it when working with `cli-anything-inkstitch` and you're about to:

- Choose a stitch type for an element (`params set --stitch-type ...`)
- Set parameters like row spacing, pull compensation, underlay (`params set --row-spacing-mm ...`)
- Diagnose why a stitch-plan preview looks wrong
- Decide whether to convert satins to fills, or vice versa

**Important framing:** Embroidery is empirical. The "right" parameter for a given element depends on the fabric, thread, stabilizer, hoop tension, machine, and design intent — none of which are visible from the SVG alone. The numbers in this skill are **starting points based on industry conventions and Ink/Stitch's defaults**; expect to refine via test sew-outs. Run `document set-context` to capture material/thread/stabilizer/intent first so your decisions are grounded.

---

## 1. The per-element decision flow

When `element describe` returns an element you need to assign params to, think in this order:

```
  ┌─────────────────────────┐
  │ 1. What stitch type?    │  ← geometry + size + position decide this
  └────────────┬────────────┘
               │
  ┌────────────▼────────────┐
  │ 2. Direction / angle    │  ← visual flow + adjacent elements
  └────────────┬────────────┘
               │
  ┌────────────▼────────────┐
  │ 3. Spacing / density    │  ← thread weight + coverage intent
  └────────────┬────────────┘
               │
  ┌────────────▼────────────┐
  │ 4. Underlay strategy    │  ← fabric stretch + element size
  └────────────┬────────────┘
               │
  ┌────────────▼────────────┐
  │ 5. Push/pull comp       │  ← fabric stretch + satin width
  └────────────┬────────────┘
               │
  ┌────────────▼────────────┐
  │ 6. Trim / start / end   │  ← color order + element neighbors
  └─────────────────────────┘
```

Don't skip steps. The order matters because earlier decisions constrain later ones (you can't pick a sensible underlay until you know the stitch type, can't pick comp until you know the width, etc.).

---

## 2. Choosing a stitch type

### Decision rules

| If the element is...                                              | Use                                  |
| ---                                                               | ---                                  |
| A filled shape, area > ~5 mm² (any direction longer than ~3 mm)   | `auto_fill`                          |
| A long/narrow shape — ratio width:length > 1:3, width 1–8 mm     | `satin_column`                        |
| A line/outline — single stroke, 0.3–1 mm wide                     | `running_stitch`                     |
| A fine outline meant to *look* heavier than running stitch        | `bean_stitch` (3-pass) or `zigzag_stitch` |
| Text characters > 5 mm tall                                       | `satin_column` per stroke (or convert text → path → satin) |
| Text characters ≤ 5 mm tall                                       | Run stitch only, or pre-digitized fonts; satin gets unreadable |

### Width thresholds for satin columns

This is the most common digitization decision and where beginners go wrong most.

- **< 1 mm**: too narrow to satin reliably. Inkstitch will warn ("Too narrow satin"). Use running stitch or convert to a stroke.
- **1–3 mm**: classic narrow satin, very forgiving.
- **3–8 mm**: standard satin width. Default zigzag spacing (0.4 mm) usually works.
- **8–10 mm**: getting wide. Satin tends to "rope" (snag, pull, look loose). Either:
  - Add a contour underlay to anchor the rails, OR
  - Convert to fill stitch
- **> 10 mm**: don't satin. Always convert to fill (or split into multiple narrower satins along the centerline).

### Filled shape vs satin column for elongated elements

Elongated regions (logos, monograms, organic shapes) sit in a gray zone. A heuristic:

- Width consistent along the length, < 8 mm → satin column (looks better, smoother edges, no row direction)
- Width varies a lot, or > 8 mm → fill stitch (more forgiving, but visible row pattern)

---

## 3. Direction (angle / fill orientation)

Auto-fill defaults to angle 0° (horizontal rows). This is rarely the best choice.

### Heuristics

1. **Visual flow** — fill direction should follow the natural flow of the shape. A leaf fills along its vein; a feather fills along its quill; a teardrop fills perpendicular to its axis (or along it for a shimmery effect).
2. **Perpendicular to the long axis** — when no visual flow, fill perpendicular to the shape's major dimension. The rows are then short and stable rather than long and floppy.
3. **Alternate adjacent fills** — if two fills touch and stitch in the same direction, the seam is invisible (good for blending) or invisible (bad for edge definition). Alternate by ~30° between adjacent fills to give edge contrast.
4. **Avoid 0°/90°/45°** — these "machine angles" look artificial. Pick something like 17°, 32°, 73°.
5. **Hide jumps in the angle** — if a fill is split into pieces by a stop or trim, picking an angle that aligns the joining edge with the row direction hides the seam.

### Inkstitch param

`--angle <degrees>` on a fill_stitch element. 0 = horizontal (rows go →), 90 = vertical, etc.

---

## 4. Spacing and density

### Row spacing (auto_fill)

- **Default**: `row_spacing_mm = 0.25` (Ink/Stitch's default — dense, nearly opaque coverage with 40wt thread)
- **Light/show-through coverage** (water-color effect, light fabric peek): 0.4–0.6 mm
- **Standard coverage**: 0.25–0.35 mm
- **Heavy coverage** (dark thread on dark fabric, no show-through): 0.20–0.25 mm
- **Thread-weight rule of thumb**: row spacing ≈ thread weight in tex × 0.01 mm; for 40wt poly that's ~0.4 mm max before thinning

### Stagger pattern (auto_fill)

Stitch stagger is the offset pattern across rows that prevents needle penetrations from forming visible "slots" in the fill.

- **Default**: `staggers = 4` (penetrations land on every 4th row before repeating)
- **Increase** (5–8) for denser fabrics or to spread penetration load
- **Decrease** (2–3) almost never useful — creates visible slot patterns

### Zigzag spacing (satin_column)

- **Default**: `zigzag_spacing_mm = 0.4` (Ink/Stitch default; standard "satin density")
- **Heavier satin**: 0.3 mm
- **Lighter / more thread-savings**: 0.5 mm
- Below 0.3 mm: thread builds up at edges; can damage fabric or break needles
- Above 0.6 mm: visible gaps; doesn't read as satin

---

## 5. Underlay strategy

Underlay is the foundation pass before the visible top stitching. It anchors the shape to the stabilizer and gives top stitches a base to lock against. **Never skip underlay on anything > 5 mm in any dimension.**

### For auto_fill

`--fill-underlay true` enables, with these defaults that you can override:

- `fill_underlay_row_spacing_mm` defaults to `row_spacing × 3` (so ~0.75 mm if you're at default fill density)
- `fill_underlay_max_stitch_length_mm` defaults to the same as the fill's max stitch length
- `fill_underlay_angle` should be at ~+90° from the top fill (perpendicular). Two underlay angles separated by space (e.g. `"45 -45"`) gives a cross-hatch — best for stretchy fabrics.

When to use:
- Always for fills > 5 mm in any direction
- Cross-hatch for stretchy fabric or for anything the design will be washed often
- Single-direction (perpendicular to top) is enough for stable substrates

### For satin_column

Three options, often combined:

| Underlay              | Use when                                                    | Inkstitch param                |
| ---                   | ---                                                         | ---                            |
| **Center-walk**       | Thin satin (1–3 mm), or as base for elaborate underlay      | `center_walk_underlay = True`  |
| **Contour**           | Small/medium satin (2–8 mm); anchors rails to stabilizer    | `contour_underlay = True`      |
| **Zigzag**            | Wide satin (> 6 mm); fills in for top-stitch coverage       | `zigzag_underlay = True`       |
| **German underlay**   | Wide satin OR stretchy fabric — combine zigzag + contour    | both `zigzag_underlay = True` and `contour_underlay = True` |

Default underlay stitch length: `3 mm` (longer than the top stitches — underlay is structural, not visible).

---

## 6. Push/pull compensation

When stitches enter and exit the fabric, they pull it together along the stitch line and push it outward perpendicular. Designs come out narrower (in satin) and shorter (in fill) than the SVG describes. Compensation expands the source path to counteract this.

### Starting points by fabric (set as `--pull-compensation-mm` on satins, also affects fill border alignment)

| Fabric                                | Starting pull comp (mm) | Notes                                   |
| ---                                   | ---                     | ---                                     |
| Stable woven (canvas, denim, twill)   | 0.1–0.2                 | Minimal stretch; lower numbers          |
| Light woven (poplin, cotton sheeting) | 0.15–0.25               |                                         |
| Knit (T-shirt cotton)                 | 0.3–0.4                 | Moderate stretch                        |
| Performance knit (poly blends)        | 0.35–0.45               | Stretchy + slick; higher                |
| Fleece                                | 0.4–0.6                 | Bulky and stretchy; thread sinks        |
| Towel / pile fabric                   | 0.5+, plus a topping    | Extreme; needs water-soluble topping    |
| Leather / vinyl                       | 0                       | No stretch; needles perforate, holes ⇒ tearing if comp added |

### Width-aware satin comp

Even on stable fabric, wider satins need *more* comp because they have more pulling stitches:

- Satin 1–2 mm wide: minimum comp (0.05–0.1 mm)
- Satin 2–5 mm wide: standard (per fabric table above)
- Satin 5–10 mm wide: add 0.1–0.2 mm extra

### Fill stitch and comp

Fills push outward (perpendicular to the row direction) by ~0.1–0.3 mm depending on density and fabric. Inkstitch doesn't have a single "fill pull comp" param — instead, you may need to **inset the fill source path** by hand for high-density fills on stretchy fabric, OR use `expand_mm` (negative value) in fill_stitch params.

---

## 7. Color and needle ordering

DST and most modern embroidery formats split the design into "color blocks" — each color stops the machine, the operator changes thread, then resumes. Bad ordering wastes time and creates visible thread cuts.

### Rules

1. **Minimize color changes** — group all elements of the same color contiguously in stitch order. Even if visual layering says otherwise, machine efficiency wins for production work.
2. **Underlay first** — if a design has an "underlay color" (skeletal foundation traced under the visible color blocks), stitch it first.
3. **Light to dark when adjacent** — when light and dark colors butt up against each other, stitch the dark one second so its outline covers any light-thread bleed.
4. **Background fills before foreground details** — a face has skin (background) then hair, eyes, mouth (foreground). Always fill skin first; small details on top.
5. **Avoid stitching over satin borders** — once a satin border is down, fills should not cross it. Order fills *before* their border satins.
6. **Trim between distant elements** — if two same-color elements are > ~3 cm apart, attach a trim command (`commands attach --command trim`) between them so no long thread floats over the design.

### Inkstitch ordering

Element stitch order follows the SVG's z-order (top to bottom in the document). Use `element list --refresh` to see current order; reorder via SVG editing or via `params set --stop-after`/`--stop-before` for explicit color-stop control.

---

## 8. Push/pull, compensation, and the fabric-stretch axis

Stretchy fabrics (knits, performance wear, fleece) deserve special care. Symptoms of under-compensated stretchy designs:

- **Fabric "smiles"** — the design pulls the substrate inward, causing rippled/wrinkled fabric around the stitch area
- **Outlines visibly recede** — designed-as-flush borders end up *inside* the colored fill they were supposed to outline
- **Letters look squashed** vertically (tall fabric pull)

Fixes (in order of strength):

1. Add fill cross-hatch underlay (two underlays at ±45° from top stitch)
2. Increase pull compensation per the fabric table above
3. Choose a stronger stabilizer (medium cut-away, not tear-away)
4. Reduce overall fill density (e.g., row spacing from 0.3 → 0.4)
5. Reduce satin density (zigzag spacing from 0.4 → 0.5)
6. Convert wide satins to fills (less aggressive pulling per area)

---

## 9. Stabilizer & substrate notes

Stabilizer is the "second layer" hooped behind (sometimes in front of) the fabric to provide a stable surface to stitch onto. The CLI doesn't control stabilizer — it's a physical step at hooping time — but `document set-context --stabilizer ...` records the choice so subsequent decisions can account for it.

| Substrate                    | Recommended stabilizer                              |
| ---                          | ---                                                 |
| T-shirt / knit cotton        | Medium cut-away (1.5 oz) behind                     |
| Denim / canvas / heavy woven | Light cut-away or tear-away behind                  |
| Towel / pile / fleece        | Cut-away behind + water-soluble topping             |
| Performance / athletic wear  | Heavy cut-away or no-show mesh behind               |
| Vinyl / leather              | Tear-away or paper backing                          |
| Caps                         | Specialty cap backing (stiffer)                     |

---

## 10. Reading stitch-plan previews — visual failure modes

When `preview generate` produces a stitch-plan SVG (or `preview stats` shows red flags), here's what the symptoms mean:

| Symptom in preview                       | Likely cause                                        | Fix                                          |
| ---                                      | ---                                                 | ---                                          |
| Radial fan stitches from a center point  | Degenerate satin column (rails collapsed to a point or two-subpath ring being treated as satin) | Check element classification with `element identify`; reassign `--stitch-type` |
| Visible "stripes" in fill at sharp angle | Stagger pattern aligned with row direction          | Increase `staggers` (default 4 → try 5 or 6) |
| Long jumps across the design             | Color blocks not grouped; needs trims               | Reorder elements; add `commands attach --command trim` |
| Stitch count > 30,000 on a 50×50mm patch | Fill density too high                               | Increase `row_spacing_mm` toward 0.35–0.4    |
| Border satin crosses inside its own fill | Fill stitched after border, or border too narrow    | Reorder; check satin width                   |
| Outline doesn't match SVG geometry       | Pull comp not set on stretchy fabric                | Add `--pull-compensation-mm` per fabric table |
| Tiny "dots" in middle of fills           | Connector stitches between subpaths                 | Use `--connector_method` to route along edges |
| Empty regions inside fills               | Fill contains tiny holes from path imprecision      | Run `tools cleanup` or `validate fix --auto` |

---

## 11. Small-detail survival guide

Anything below 5 mm in any dimension is a "small detail" — text, dots, fine outlines, eyes in a face. These need different treatment from regular elements.

### Sub-3mm features

- **Text < 5mm tall**: don't satin. Use `running_stitch` or pre-digitized fonts, or scale up. Below 5mm, the satin density crushes the letterforms.
- **Dots < 1mm radius**: a single satin tack stitch beats trying to fill it.
- **Fine outlines (0.3–0.8mm wide)**: `running_stitch` or `bean_stitch` (3-pass = thicker visual). Satin needs > 1mm width.

### Fill density adjustment for small fills

Small auto-fills (< 5×5mm) over-stitch quickly:
- Reduce density: `row_spacing_mm` from 0.25 → 0.3 or 0.35
- Single underlay row, not cross-hatch
- Stagger 3–4 (default fine)

---

## 12. Recommended starting recipes

When in doubt, start here. Always sample-stitch first.

### Logo on T-shirt (knit cotton, 40wt poly thread, medium cut-away backing)

```bash
# Set context first
cli-anything-inkstitch document set-context --project $PROJ \
    --material "knit cotton" --stretch high \
    --thread "40wt polyester" --stabilizer "medium cut-away" \
    --hoop-tension medium --intent "wash-durable t-shirt logo"

# For each fill element
cli-anything-inkstitch params set --project $PROJ --id <id> \
    --stitch-type auto_fill \
    --row-spacing-mm 0.3 \
    --fill-underlay true \
    --fill-underlay-angle "45 -45" \
    --staggers 4

# For each satin border
cli-anything-inkstitch params set --project $PROJ --id <id> \
    --stitch-type satin_column \
    --zigzag-spacing-mm 0.4 \
    --pull-compensation-mm 0.35 \
    --contour-underlay true \
    --zigzag-underlay true
```

### Patch (canvas/twill, sew-on or iron-on backing)

```bash
cli-anything-inkstitch document set-context --project $PROJ \
    --material "canvas twill" --stretch none \
    --thread "40wt polyester" --stabilizer "tear-away" \
    --intent "patch with merrow border"

# Fills can be denser — substrate is stable
cli-anything-inkstitch params set --project $PROJ --id <id> \
    --stitch-type auto_fill \
    --row-spacing-mm 0.25 \
    --fill-underlay true \
    --staggers 4

# Satins with low pull comp
cli-anything-inkstitch params set --project $PROJ --id <id> \
    --stitch-type satin_column \
    --zigzag-spacing-mm 0.4 \
    --pull-compensation-mm 0.15 \
    --contour-underlay true
```

### Fleece / sweatshirt (high-stretch, bulky)

```bash
cli-anything-inkstitch document set-context --project $PROJ \
    --material "fleece" --stretch high \
    --thread "40wt polyester" --stabilizer "heavy cut-away" \
    --intent "embroidered logo on sweatshirt; topping needed for fleece pile"

# Looser fill, cross-hatch underlay essential
cli-anything-inkstitch params set --project $PROJ --id <id> \
    --stitch-type auto_fill \
    --row-spacing-mm 0.35 \
    --fill-underlay true \
    --fill-underlay-angle "45 -45" \
    --fill-underlay-row-spacing-mm 1.0

# Aggressive pull comp for satins
cli-anything-inkstitch params set --project $PROJ --id <id> \
    --stitch-type satin_column \
    --zigzag-spacing-mm 0.4 \
    --pull-compensation-mm 0.5 \
    --contour-underlay true \
    --zigzag-underlay true
```

---

## 13. Common questions

**Q: Should I always use `--fill-underlay`?**
A: For any fill > 5 mm in any direction, yes. Skip only for tiny details where underlay would over-stitch.

**Q: My satin looks "ropy" — what's wrong?**
A: Either (a) too wide for satin (> 8 mm), (b) zigzag spacing too tight, (c) no contour underlay anchoring the rails, (d) thread tension too high on the machine. Try contour underlay first, then widen zigzag spacing to 0.5 mm.

**Q: What's the difference between `--pull-compensation-mm` and `--pull-compensation-percent`?**
A: `_mm` is a fixed amount applied to every stitch regardless of width. `_percent` scales with width (10% comp on a 5mm satin = 0.5mm). For mixed-width satin work, percent gives more uniform results.

**Q: Should I convert text to paths?**
A: Yes, almost always. Inkstitch handles `<text>` poorly (it's a TextTypeWarning in `validate run`). Convert text to paths in Inkscape (Path > Object to Path) before opening with this CLI. Or use Ink/Stitch's lettering extension first.

**Q: How do I know if my design is "too dense"?**
A: Use `preview stats --project $PROJ`. Heuristic: stitch count / area in mm² should be roughly 0.6–1.5 for most designs. > 2.0 is over-stitched (will damage fabric); < 0.4 is sparse (will show through).

**Q: Why are my colors all wrong in the DST preview?**
A: DST format doesn't store RGB — only color stops. Whatever previewer you're using is assigning arbitrary palette colors. The actual colors come from the thread you load on the machine; the operator should reference the design's color order. To control: set `--thread_color` per element, or use `document set-palette` to attach a thread palette name (e.g., "Madeira Polyneon") that previewers can resolve.

---

## 14. When this skill doesn't have the answer

Test sew-out. Embroidery is empirical. Take a 5×5cm scrap of the actual fabric you'll use, hoop with the actual stabilizer, run the design at the actual machine speed, then look at it under good light. If it looks wrong, the symptom usually maps to one of the entries in §10 above. Iterate.

Trusted reference sources for deeper reading:
- Inkstitch official docs: https://inkstitch.org/docs/
- Inkstitch tutorials: https://inkstitch.org/tutorials/
- "EmbroideryMag" blog by Erich Campbell (industry veteran, heavy on technique)
- Wilcom's "Embroidery Adventures" educational series
- Lindee Goodall's tutorials (especially for lettering and small text)
