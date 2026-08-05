"""`font` command group — create and edit Inkstitch embroidery fonts.

Design note: font commands operate on standalone font *directories* (ltr.svg or legacy →.svg +
font.json + preview.png), not on project-attached SVGs, so they deliberately
bypass the project/history system (no --project, no undo/redo, no project
lock). Fonts are portable assets meant to be versioned with git or copied
into Ink/Stitch's font folder; the project undo model doesn't apply to them.
Writes are still atomic (tmp + rename) via svg.document.write_svg_atomic.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
from pathlib import Path

import click
from lxml import etree

from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.output import emit
from cli_anything_inkstitch.svg.attrs import INKSCAPE_NS, SVG_NS

# Pure-logic helpers were moved out of this module into the embroidery/ and
# font_format/ packages.  Every moved name is re-imported here explicitly so
# existing imports of `cli_anything_inkstitch.commands.font` keep working.
from cli_anything_inkstitch.embroidery.analysis import (
    _detect_exit_advance,
    _emb_to_svg_paths,
    _exit_advance_at_connection_y,
    _last_stitch_svg_y,
    _rotate_coords,  # noqa: F401  (re-exported for back-compat)
    _visual_baseline_y,
)
from cli_anything_inkstitch.embroidery.bx import (
    _BX_DESCENDER_MAX_BF,
    _BX_DESCENDER_MIN_BF,
    _extract_bx_connection_offsets,
    _locate_bzip2_payload,  # noqa: F401  (re-exported for back-compat)
    _parse_bx_glyphs,  # noqa: F401  (re-exported for back-compat)
)
from cli_anything_inkstitch.embroidery.files import (
    _DST_MM_PER_UNIT,  # noqa: F401  (re-exported for back-compat)
    _DST_TO_SVG,
    _EMB_EXTS,  # noqa: F401  (re-exported for back-compat)
    _PUNCT_MAP,  # noqa: F401  (re-exported for back-compat)
    _SVG_MM_PER_PX,
    _find_embroidery_files,
    _parse_char_from_stem,
    _read_embroidery,
)
from cli_anything_inkstitch.font_format.metadata import (
    _default_font_json,
    _load_font_json,
    _save_font_json,
)
from cli_anything_inkstitch.font_format.svg_build import (
    FONT_NSMAP,  # noqa: F401  (re-exported for back-compat)
    FONT_SVG_FILENAME,
    FONT_SVG_VERSION,  # noqa: F401
    font_svg_path,
    SODIPODI_NS,  # noqa: F401  (re-exported for back-compat)
    _DOC_HEIGHT,  # noqa: F401  (re-exported for back-compat)
    _DOC_WIDTH,  # noqa: F401  (re-exported for back-compat)
    _TOKEN_RE,  # noqa: F401  (re-exported for back-compat)
    _build_font_svg,
    _build_guide,  # noqa: F401  (re-exported for back-compat)
    _elem_x_range,
    _find_elem_by_id,
    _find_elems_by_label,
    _find_source_baseline_y,
    _get_font_baseline_y,
    _inkscape_y,  # noqa: F401  (re-exported for back-compat)
    _load_font_svg,
    _load_source_svg,
    _path_x_range,  # noqa: F401  (re-exported for back-compat)
    _save_font_svg,
    _tokenize_path,  # noqa: F401  (re-exported for back-compat)
)


# ---------------------------------------------------------------------------
# Click command group
# ---------------------------------------------------------------------------

@click.group("font")
def font():
    """Create and manage Inkstitch embroidery fonts."""


@font.command("fonts")
@click.pass_context
def font_fonts(ctx):
    """List fonts shipped with the installed Ink/Stitch binary."""
    from cli_anything_inkstitch.binary import discover
    from cli_anything_inkstitch.embroidery.lettering import list_fonts
    binary = discover(ctx.obj.get("binary_override"))
    fonts = list_fonts(binary)
    emit(ctx, {"fonts": fonts, "count": len(fonts)})


@font.command("compose")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--font", "font_ref", required=True,
              help="Shipped font name (see `font fonts`) or a font directory.")
@click.option("--text", required=True, help="Text to place; \\n = new line.")
@click.option("--height-mm", type=float, default=None,
              help="Target letter height; scales geometry only — satin "
                   "params stay physical mm, which is why fonts carry "
                   "min/max scale limits (violations become warnings).")
@click.option("--at-mm", default=None,
              help="Baseline start 'x,y' in mm (default 10,10).")
@click.option("--tracking-em", type=float, default=0.1, show_default=True,
              help="Extra advance between letters when the font has no "
                   "per-glyph advance table.")
@click.pass_context
def font_compose(ctx, project_path, font_ref, text, height_mm, at_mm,
                 tracking_em):
    """Compose text from a pre-digitized Ink/Stitch font into the design.

    Glyphs are finished satin/stroke elements — this is layout, not
    digitization (readers: lib/lettering/font_variant.py GlyphLayer-*,
    glyph.py baseline guide, font.json metrics). Elements land in one
    labeled group, fully editable in the artifact editor like anything
    else.
    """
    from cli_anything_inkstitch.binary import discover
    from cli_anything_inkstitch.commands._helpers import open_project
    from cli_anything_inkstitch.embroidery.lettering import (
        PX_PER_MM,
        compose,
        load_font,
        resolve_font,
    )
    from cli_anything_inkstitch.history import (
        document_replace,
        make_entry,
        push,
    )
    binary = discover(ctx.obj.get("binary_override"))
    fdir = resolve_font(font_ref, binary)
    fobj = load_font(fdir)
    if not fobj.glyphs:
        raise UserError(f"{fdir} has no glyph layers")
    text = text.replace("\\n", "\n")
    if at_mm:
        try:
            ax, ay = (float(v) for v in at_mm.split(","))
        except ValueError as exc:
            raise UserError("--at-mm must be 'x,y' in mm") from exc
    else:
        ax = ay = 10.0
    placement = compose(fobj, text, height_mm=height_mm,
                        at_px=(ax * PX_PER_MM, ay * PX_PER_MM),
                        tracking_em=tracking_em)
    if not placement.elements:
        raise UserError("nothing to place — no glyph in this font matched")
    with open_project(ctx, project_path, mutate=True) as (proj, tree):
        root = tree.getroot()
        before_xml = etree.tostring(root).decode("utf-8")
        group = etree.SubElement(root, f"{{{SVG_NS}}}g")
        gid = "lettering_" + secrets.token_hex(3)
        group.set("id", gid)
        group.set(f"{{{INKSCAPE_NS}}}label",
                  f"lettering: {text[:24]}" + ("…" if len(text) > 24 else ""))
        ids = []
        for el in placement.elements:
            eid = "let_" + secrets.token_hex(3)
            el.set("id", eid)
            ids.append(eid)
            group.append(el)
        push(proj.history, make_entry(
            command=f"font compose --font {font_ref} --text {text[:32]!r}",
            patch=document_replace(
                before_xml, etree.tostring(root).decode("utf-8"))))
        payload = {"group": gid, "elements": len(ids),
                   "font": fobj.name,
                   "width_mm": round(placement.width_px / PX_PER_MM, 2),
                   "warnings": placement.warnings}
        if fobj.meta.get("auto_satin"):
            payload["note"] = ("this font expects routing: run "
                               f"`tools auto-satin --ids {','.join(ids[:1])},…`"
                               " over the group's elements for engine-order "
                               "stitching")
        emit(ctx, payload)


@font.command("init")
@click.option("--name", required=True, help="Human-readable font name.")
@click.option("--dir", "font_dir", required=True, type=click.Path(),
              help="Directory to create the font in (will be created).")
@click.option("--units-per-em", "units_per_em", type=float, default=400.0, show_default=True,
              help="Coordinate scale (design units per em).")
@click.option("--size", "size_mm", type=float, default=60.0, show_default=True,
              help="Approximate cap-height in millimetres.")
@click.option("--baseline-y", "baseline_y", type=float, default=None,
              help="SVG Y coordinate of the baseline (default: 0.85 × units-per-em).")
@click.option("--ascender-height", "ascender_height", type=float, default=None,
              help="Units above baseline for ascender (default: 0.95 × upe).")
@click.option("--descender-depth", "descender_depth", type=float, default=None,
              help="Units below baseline for descender (default: 0.2 × upe).")
@click.option("--caps-height", "caps_height", type=float, default=None,
              help="Units above baseline for cap-height (default: 0.85 × upe).")
@click.option("--xheight", "xheight", type=float, default=None,
              help="Units above baseline for x-height (default: 0.55 × upe).")
@click.pass_context
def font_init(ctx, name, font_dir, units_per_em, size_mm, baseline_y,
              ascender_height, descender_depth, caps_height, xheight):
    """Create a new empty Inkstitch font in FONT_DIR."""
    fdir = Path(font_dir)
    fdir.mkdir(parents=True, exist_ok=True)

    upe = units_per_em
    bl_y = baseline_y if baseline_y is not None else round(upe * 0.80)
    asc_h = ascender_height if ascender_height is not None else round(upe * 0.80)
    desc_d = descender_depth if descender_depth is not None else round(upe * 0.20)
    cap_h = caps_height if caps_height is not None else round(upe * 0.72)
    xh = xheight if xheight is not None else round(upe * 0.52)

    # SVG Y positions (Y increases downward)
    asc_svg_y = bl_y - asc_h
    desc_svg_y = bl_y + desc_d
    caps_svg_y = bl_y - cap_h
    xh_svg_y = bl_y - xh

    doc_height = desc_svg_y + max(50.0, upe * 0.1)

    tree = _build_font_svg(
        baseline_y=bl_y,
        ascender_y=asc_svg_y,
        descender_y=desc_svg_y,
        caps_y=caps_svg_y,
        xheight_y=xh_svg_y,
        doc_height=doc_height,
    )
    _save_font_svg(tree, fdir)

    leading = upe + desc_d + max(20.0, upe * 0.05)
    data = _default_font_json(name, upe, size_mm, leading)
    _save_font_json(fdir, data)

    emit(ctx, {
        "created": str(fdir),
        "units_per_em": upe,
        "baseline_y": bl_y,
        "ascender_y": asc_svg_y,
        "descender_y": desc_svg_y,
        "caps_y": caps_svg_y,
        "xheight_y": xh_svg_y,
    })


@font.command("add-glyph")
@click.option("--font-dir", "font_dir", required=True, type=click.Path(),
              help="Font directory (must contain ltr.svg — or legacy →.svg — and font.json).")
@click.option("--source-svg", "source_svg", required=True, type=click.Path(),
              help="SVG file containing the glyph artwork.")
@click.option("--char", "chars_csv", default=None,
              help="Character(s) to add, comma-separated. If omitted, auto-discovers by inkscape:label.")
@click.option("--source-id", "source_id", default=None,
              help="Element ID in source SVG. Required when --char specifies exactly one character.")
@click.option("--baseline-y", "baseline_y", type=float, default=None,
              help="Baseline SVG Y in the source file. Auto-detected from guide if omitted.")
@click.option("--advance", "advance", type=float, default=None,
              help="Horizontal advance (units). Defaults to glyph x-width.")
@click.option("--advance-padding", "advance_padding", type=float, default=0.0,
              help="Extra units added to auto-computed advance.")
@click.pass_context
def add_glyph(ctx, font_dir, source_svg, chars_csv, source_id, baseline_y, advance, advance_padding):
    """Add one or more glyphs from a source SVG into the font.

    Without --char, looks for elements whose inkscape:label is a single
    Unicode character and imports all of them.

    With --char and --source-id, imports a specific element as that character.
    """
    fdir = Path(font_dir)
    src_tree = _load_source_svg(source_svg)
    font_tree = _load_font_svg(fdir)
    font_data = _load_font_json(fdir)

    # Resolve source baseline
    src_baseline = baseline_y
    if src_baseline is None:
        src_baseline = _find_source_baseline_y(src_tree)
    if src_baseline is None:
        raise UserError(
            "Could not auto-detect baseline from source SVG. "
            "Add a guide labelled 'baseline' in Inkscape, or pass --baseline-y."
        )

    font_baseline = _get_font_baseline_y(font_tree)

    # Build list of (char, source_elem) to process
    pairs: list[tuple[str, object]] = []
    if chars_csv is not None:
        chars = [c.strip() for c in chars_csv.split(",") if c.strip()]
        if len(chars) == 1 and source_id is not None:
            elem = _find_elem_by_id(src_tree, source_id)
            if elem is None:
                raise UserError(f"no element with id={source_id!r} in {source_svg}")
            pairs = [(chars[0], elem)]
        elif source_id is not None:
            raise UserError("--source-id can only be used with exactly one --char value")
        else:
            # Chars provided but no source-id: look up by inkscape:label
            for char in chars:
                found = None
                for elem in src_tree.getroot().iter():
                    if not isinstance(elem.tag, str):
                        continue
                    if elem.get(f"{{{INKSCAPE_NS}}}label", "") == char:
                        found = elem
                        break
                if found is None:
                    raise UserError(f"no element with inkscape:label={char!r} in {source_svg}")
                pairs.append((char, found))
    else:
        # Auto-discover by inkscape:label
        pairs = _find_elems_by_label(src_tree)
        if not pairs:
            raise UserError(
                "No elements with single-character inkscape:label found. "
                "Label each glyph with its character in Inkscape, or use --char + --source-id."
            )

    font_root = font_tree.getroot()
    dy = font_baseline - src_baseline
    added = []

    for char, src_elem in pairs:
        layer_label = f"GlyphLayer-{char}"

        # Remove existing glyph layer for this char if present
        existing = font_root.xpath(
            f".//svg:g[@inkscape:label=$label]",
            namespaces={"svg": SVG_NS, "inkscape": INKSCAPE_NS},
            label=layer_label,
        )
        for old in existing:
            parent = old.getparent()
            if parent is not None:
                parent.remove(old)

        # Compute advance width
        if advance is not None:
            adv = advance
        else:
            min_x, max_x = _elem_x_range(src_elem)
            adv = round(max_x - min_x + advance_padding, 1)

        # Build GlyphLayer group
        layer = etree.SubElement(font_root, f"{{{SVG_NS}}}g")
        layer.set(f"{{{INKSCAPE_NS}}}groupmode", "layer")
        layer.set(f"{{{INKSCAPE_NS}}}label", layer_label)
        layer.set("style", "display:none")
        layer.set("id", f"g_{secrets.token_hex(4)}")
        if dy != 0.0:
            layer.set("transform", f"translate(0,{dy:.4f})")

        # Deep-copy source element's contents into the layer
        src_local = etree.QName(src_elem.tag).localname
        if src_local in ("g", "svg"):
            # Copy children of the group/svg
            for child in src_elem:
                layer.append(copy.deepcopy(child))
        else:
            # Single path or other element — copy it directly
            layer.append(copy.deepcopy(src_elem))

        # Update font.json
        font_data["horiz_adv_x"][char] = adv
        if char not in font_data.get("glyphs", []):
            font_data.setdefault("glyphs", []).append(char)

        added.append({"char": char, "advance": adv, "dy": round(dy, 4)})

    _save_font_svg(font_tree, fdir)
    _save_font_json(fdir, font_data)
    emit(ctx, {"added": added, "font_dir": str(fdir)})


@font.command("set-advance")
@click.option("--font-dir", "font_dir", required=True, type=click.Path())
@click.option("--char", "char", required=True, help="Character to update.")
@click.option("--value", "value", required=True, type=float, help="New advance width.")
@click.pass_context
def set_advance(ctx, font_dir, char, value):
    """Set the horizontal advance width for a character."""
    fdir = Path(font_dir)
    data = _load_font_json(fdir)
    data.setdefault("horiz_adv_x", {})[char] = value
    _save_font_json(fdir, data)
    emit(ctx, {"char": char, "advance": value})


@font.command("info")
@click.option("--font-dir", "font_dir", required=True, type=click.Path())
@click.pass_context
def font_info(ctx, font_dir):
    """Show font metadata and glyph count."""
    fdir = Path(font_dir)
    data = _load_font_json(fdir)
    glyph_count = len(data.get("glyphs", []))
    advance_count = len(data.get("horiz_adv_x", {}))
    emit(ctx, {
        "name": data.get("name"),
        "units_per_em": data.get("units_per_em"),
        "size_mm": data.get("size"),
        "leading": data.get("leading"),
        "glyph_count": glyph_count,
        "advance_count": advance_count,
        "glyphs": sorted(data.get("glyphs", [])),
    })


@font.command("remove-glyph")
@click.option("--font-dir", "font_dir", required=True, type=click.Path())
@click.option("--char", "char", required=True, help="Character to remove.")
@click.pass_context
def remove_glyph(ctx, font_dir, char):
    """Remove a glyph from the font."""
    fdir = Path(font_dir)
    font_tree = _load_font_svg(fdir)
    font_data = _load_font_json(fdir)
    font_root = font_tree.getroot()

    layer_label = f"GlyphLayer-{char}"
    removed_layers = 0
    existing = font_root.xpath(
        ".//svg:g[@inkscape:label=$label]",
        namespaces={"svg": SVG_NS, "inkscape": INKSCAPE_NS},
        label=layer_label,
    )
    for old in existing:
        parent = old.getparent()
        if parent is not None:
            parent.remove(old)
            removed_layers += 1

    font_data.get("horiz_adv_x", {}).pop(char, None)
    glyphs = font_data.get("glyphs", [])
    if char in glyphs:
        glyphs.remove(char)

    _save_font_svg(font_tree, fdir)
    _save_font_json(fdir, font_data)
    emit(ctx, {"removed": char, "layers_removed": removed_layers})


# ---------------------------------------------------------------------------
# Preview PNG generation (Pillow)
# ---------------------------------------------------------------------------

def _new_canvas(W: int, H: int, color=(255, 255, 255)):
    """White RGB canvas + draw handle. PIL clips out-of-bounds drawing safely."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), color)
    return img, ImageDraw.Draw(img)


def _parse_path_points_simple(d: str) -> list[tuple[float, float]]:
    """Extract (x,y) sequence from an 'M x,y L x1,y1 ...' path (our generated format)."""
    nums = [float(n) for n in re.findall(
        r'[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?', d)]
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def _generate_preview_png(
    font_dir: Path,
    font_data: dict,
    font_tree: etree._ElementTree,
    override_chars: str | None = None,
) -> None:
    """Write preview.png (300×20 px) for Inkstitch lettering dialog.

    Renders the font name in the typeface.  Falls back to first available
    uppercase letters if the name characters aren't available.
    """
    W, H = 300, 20
    margin_y, margin_x = 2, 2

    glyphs_available = set(font_data.get("glyphs", []))

    if override_chars is not None:
        sample = list(override_chars)
    else:
        # Spell out the font name using available glyphs
        font_name = font_data.get("name", "")
        sample = []
        for c in font_name:
            if c == ' ':
                continue
            if c in glyphs_available:
                sample.append(c)
            elif c.upper() in glyphs_available:
                sample.append(c.upper())
            elif c.lower() in glyphs_available:
                sample.append(c.lower())
        # Fallback: first available uppercase letters
        if not sample:
            sample = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c in glyphs_available][:7]
        if not sample:
            sample = sorted(glyphs_available)[:7]

    img, draw = _new_canvas(W, H)

    if not sample:
        img.save(font_dir / "preview.png")
        return

    font_root = font_tree.getroot()
    horiz_adv = font_data.get("horiz_adv_x", {})
    default_adv = font_data.get("horiz_adv_x_default", 60.0)

    # Collect line segments laid out side by side with advance-width spacing
    segments: list[tuple[float, float, float, float]] = []
    x_cursor = 0.0

    for char in sample:
        layer_label = f"GlyphLayer-{char}"
        layers = font_root.xpath(
            ".//svg:g[@inkscape:label=$label]",
            namespaces={"svg": SVG_NS, "inkscape": INKSCAPE_NS},
            label=layer_label,
        )
        if not layers:
            continue
        layer = layers[0]
        tdx, tdy = 0.0, 0.0
        m = re.match(r'translate\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', layer.get("transform", ""))
        if m:
            tdx, tdy = float(m.group(1)), float(m.group(2))

        char_segs: list[tuple[float, float, float, float]] = []
        for path_el in layer.iter(f"{{{SVG_NS}}}path"):
            pts = _parse_path_points_simple(path_el.get("d", ""))
            for i in range(len(pts) - 1):
                char_segs.append((
                    pts[i][0] + tdx, pts[i][1] + tdy,
                    pts[i + 1][0] + tdx, pts[i + 1][1] + tdy,
                ))

        if not char_segs:
            continue

        char_xs = [s[0] for s in char_segs] + [s[2] for s in char_segs]
        char_min_x = min(char_xs)
        ox = x_cursor - char_min_x
        for x0, y0, x1, y1 in char_segs:
            segments.append((x0 + ox, y0, x1 + ox, y1))

        x_cursor += float(horiz_adv.get(char, default_adv))

    if not segments:
        img.save(font_dir / "preview.png")
        return

    all_xs = [s[0] for s in segments] + [s[2] for s in segments]
    all_ys = [s[1] for s in segments] + [s[3] for s in segments]
    min_x, max_x = min(all_xs), max(all_xs)
    min_y, max_y = min(all_ys), max(all_ys)
    span_x = max_x - min_x or 1.0
    span_y = max_y - min_y or 1.0
    scale = min((W - 2 * margin_x) / span_x, (H - 2 * margin_y) / span_y)

    dark = (40, 40, 100)
    for x0, y0, x1, y1 in segments:
        px0 = int((x0 - min_x) * scale) + margin_x
        py0 = int((y0 - min_y) * scale) + margin_y
        px1 = int((x1 - min_x) * scale) + margin_x
        py1 = int((y1 - min_y) * scale) + margin_y
        draw.line([(px0, py0), (px1, py1)], fill=dark)

    img.save(font_dir / "preview.png")


@font.command("import")
@click.option("--name", required=True, help="Human-readable font name.")
@click.option("--source-dir", "source_dirs", required=True, multiple=True, type=click.Path(exists=True),
              help="Directory containing per-letter embroidery files (DST, PES, etc.). "
                   "May be specified multiple times to combine several directories.")
@click.option("--output-dir", "output_dir", required=True, type=click.Path(),
              help="Font output directory (will be created).")
@click.option("--ext", "pick_ext", default=None,
              help="File extension to prefer (e.g. 'dst'). Auto-selects DST if omitted.")
@click.option("--size-mm", "size_mm", type=float, default=None,
              help="Physical cap-height in mm. Computed from glyph height if omitted.")
@click.option("--stitch-length-mm", "stitch_length_mm", type=float, default=1.5, show_default=True,
              help="Running stitch length for re-rendering.")
@click.option("--baseline-from-bottom-mm", "baseline_from_bottom_mm", type=float, default=0.0,
              show_default=True,
              help="How far above the glyph bottom (in mm) to place the baseline. "
                   "Use a positive value for fonts with descenders (e.g. 3.0 for g, y, j).")
@click.option("--skip-alts", "skip_alts", is_flag=True, default=True,
              help="Skip alternate glyphs (Alt*, Swoosh*, etc.).")
@click.option("--script", "is_script", is_flag=True, default=False,
              help="Enable exit-connector detection for connecting script fonts. "
                   "Trims each letter's advance width to where the body ends so "
                   "letters overlap/connect rather than sitting monospaced.")
@click.option("--bx-file", "bx_file", default=None, type=click.Path(exists=True),
              help="Path to an Embrilliance BX font file.  When supplied, per-letter "
                   "connection Y offsets are extracted from the BX binary and used to "
                   "align each glyph individually (ascenders, descenders, x-height "
                   "letters all land on the correct baseline without guessing).")
@click.option("--advance-padding", "advance_padding", type=float, default=0.0, show_default=True,
              help="Extra SVG px added to every auto-computed advance width.  Use a small "
                   "positive value (e.g. 3–6) if connected-script letters feel too tight.")
@click.option("--per-char", "per_char", default="all",
              type=click.Choice(["all", "first", "last"], case_sensitive=False),
              show_default=True,
              help="When multiple source files resolve to the same character, "
                   "'first' keeps the alphabetically first stem, 'last' keeps the last, "
                   "'all' imports all (last one written wins).")
@click.option("--sortable", "sortable", is_flag=True, default=False,
              help="Mark the font as sortable (multicolor). Sets sortable=true in font.json "
                   "so Inkstitch shows the color-sort controls in the lettering dialog.")
@click.option("--description", "description", default="", show_default=False,
              help="Short description of the font written into font.json.")
@click.option("--keywords", "keywords", default="", show_default=False,
              help="Comma-separated keywords for the font (e.g. 'script,handwriting,latin').")
@click.option("--dry-run", is_flag=True, help="Parse filenames only; don't write anything.")
@click.option("--baseline-method", "baseline_method",
              type=click.Choice(["bbox-bottom", "last-stitch", "reference-letter"],
                                case_sensitive=False),
              default="bbox-bottom", show_default=True,
              help="How to determine each glyph's baseline when no BX file is supplied. "
                   "'bbox-bottom' (default) places the baseline at the visual bottom of each "
                   "glyph's stitch bounding box.  'last-stitch' uses the Y coordinate of the "
                   "last stitch — best for script fonts whose exit connector tip is the "
                   "natural connection point.  'reference-letter' normalises all glyphs "
                   "relative to a chosen letter so every glyph lands on a consistent baseline "
                   "regardless of individual height variation.")
@click.option("--reference-letter", "reference_letter", default=None,
              help="Single character used as the baseline anchor when "
                   "--baseline-method=reference-letter.  All other glyphs are shifted so "
                   "their visual bottom aligns with this letter's visual bottom.  "
                   "Good choices: 'x' (x-height), 'H' (cap-height).")
@click.pass_context
def font_import(ctx, name, source_dirs, output_dir, pick_ext, size_mm, stitch_length_mm,
                baseline_from_bottom_mm, skip_alts, is_script, bx_file, advance_padding,
                per_char, sortable, description, keywords, dry_run,
                baseline_method, reference_letter):
    """Create an Inkstitch font by importing per-letter embroidery files (DST, PES, etc.).

    Each file in SOURCE_DIR is matched to a character via its filename. Supported
    naming conventions: CapA/LowA/PunComma (Stitchtopia, TSS), A/a/0 (simple),
    a_low/A_cap, a-Star* (Star-DST), "Floral Alphabet A15".

    The baseline is placed at the bottom of each glyph's stitch bounding box plus
    --baseline-from-bottom-mm. For fonts with descenders (g, j, p, q, y), pass a
    positive value so those letters sit correctly relative to uppercase letters.

    If you have the matching Embrilliance BX file, pass it via --bx-file.  The
    per-letter connection Y coordinates are then read directly from the BX binary
    so each glyph is positioned precisely — no guessing required.

    """
    import pyembroidery

    sdirs = [Path(d) for d in source_dirs]
    odir = Path(output_dir)

    # --bx-file: extract per-letter connection Y from BX binary
    bx_offsets: dict[str, float] | None = None
    bx_y_min_baseline: float = 0.0  # most-positive y_min = x-height reference
    if bx_file:
        bx_offsets = _extract_bx_connection_offsets(bx_file)
        if bx_offsets:
            # y_min values are negative; the one closest to 0 is the x-height
            # baseline reference (e.g. -83 for Homerun 1in).
            bx_y_min_baseline = max(bx_offsets.values())  # e.g. -83.0

    # Determine extension preference (per first source dir, consistent across all)
    ext_filter = None
    if pick_ext:
        ext_filter = f".{pick_ext.lstrip('.').lower()}"
    else:
        # Prefer DST, then PES, then whatever's there
        all_files_flat = [p for sd in sdirs for p in sd.iterdir() if p.is_file()]
        for preferred in [".dst", ".pes", ".vp3", ".jef"]:
            if any(p.suffix.lower() == preferred for p in all_files_flat):
                ext_filter = preferred
                break

    # Collect files from all source dirs
    all_raw_files: list[Path] = []
    for sd in sdirs:
        all_raw_files.extend(_find_embroidery_files(sd, ext_filter))
    all_raw_files.sort(key=lambda p: p.name)

    if not all_raw_files:
        raise UserError(
            f"no embroidery files found in {[str(d) for d in sdirs]} "
            f"(ext filter: {ext_filter})"
        )

    # Parse characters from filenames
    ALT_RE = re.compile(r'Alt[A-Za-z]', re.IGNORECASE)
    parsed_all: list[tuple[str, Path]] = []
    skipped: list[str] = []
    for f in all_raw_files:
        stem = f.stem
        if skip_alts and ALT_RE.search(stem):
            skipped.append(stem)
            continue
        char = _parse_char_from_stem(stem)
        if char is None:
            skipped.append(stem)
            continue
        parsed_all.append((char, f))

    if not parsed_all:
        raise UserError(
            f"could not parse any characters from filenames. "
            f"Try --dry-run to see what's being detected."
        )

    # --per-char: when multiple files map to the same character, pick first/last/all
    from collections import defaultdict as _dd
    char_variants: dict[str, list[Path]] = _dd(list)
    for char, f in parsed_all:
        char_variants[char].append(f)

    parsed: list[tuple[str, Path]] = []
    duplicates: list[str] = []
    for char in sorted(char_variants):
        variants = sorted(char_variants[char], key=lambda p: p.name)
        if per_char == "first":
            chosen = variants[:1]
            dropped = variants[1:]
        elif per_char == "last":
            chosen = variants[-1:]
            dropped = variants[:-1]
        else:  # "all" — last written wins; add all in order
            chosen = variants
            dropped = []
        parsed.extend((char, f) for f in chosen)
        duplicates.extend(f.name for f in dropped)

    if dry_run:
        result = {"parsed": [{"char": c, "file": str(f.name)} for c, f in parsed],
                  "skipped": skipped,
                  "duplicates_dropped": duplicates}
        emit(ctx, result)
        return

    # Read all glyphs once, caching patterns to avoid double I/O.
    # Compute median height for calibration — robust against any single
    # outlier letter (unusually tall A, swash-heavy R, etc.).
    cached: dict[str, object] = {}   # char -> pattern
    heights_svg: list[float] = []
    for char, f in parsed:
        try:
            p = _read_embroidery(f)
            ex = p.extents()
            h = (ex[3] - ex[1]) * _DST_TO_SVG
            if h > 0:
                cached[char] = p
                heights_svg.append(h)
        except Exception:
            pass

    if not heights_svg:
        raise UserError("could not read any embroidery file to calibrate font metrics")

    heights_svg.sort()
    # Median: central tendency ignores tall swash/descender outliers and
    # short accent marks equally.
    height_svg = heights_svg[len(heights_svg) // 2]
    height_mm = height_svg * _SVG_MM_PER_PX

    actual_size_mm = size_mm if size_mm is not None else round(height_mm, 1)
    units_per_em = round(height_svg * 1.2)  # upe = glyph height + 20% margin
    leading = round(units_per_em * 1.2)

    # Font coordinate system:
    # baseline SVG_Y = height_svg + baseline_from_bottom_px
    baseline_from_bottom_px = (baseline_from_bottom_mm / _SVG_MM_PER_PX) if baseline_from_bottom_mm else 0
    baseline_svg_y = round(height_svg + baseline_from_bottom_px)

    # Resolve reference bottom for the 'reference-letter' baseline method.
    # This is the visual bottom of the reference letter in raw stitch SVG space
    # (before translation).  Every other glyph is shifted to the *same* raw
    # bottom level, so all glyphs land on a consistent baseline automatically.
    ref_svg_bottom: float | None = None
    import_warnings: list[str] = []
    if baseline_method == "reference-letter":
        if not reference_letter:
            msg = ("--baseline-method=reference-letter requires --reference-letter; "
                   "falling back to bbox-bottom.")
            import_warnings.append(msg)
            click.echo(f"Warning: {msg}", err=True)
            baseline_method = "bbox-bottom"
        else:
            ref_letter = reference_letter[0]  # take only first char if user typed more
            ref_pattern = cached.get(ref_letter)
            if ref_pattern is None:
                # May not have been in the cache if reading failed — try direct load
                ref_path = next((f for ch, f in parsed if ch == ref_letter), None)
                if ref_path:
                    try:
                        ref_pattern = _read_embroidery(ref_path)
                    except Exception:
                        pass
            if ref_pattern is not None:
                ref_svg_bottom = _visual_baseline_y(ref_pattern, height_svg)
            else:
                msg = (f"reference letter '{ref_letter}' not found or unreadable; "
                       "falling back to bbox-bottom.")
                import_warnings.append(msg)
                click.echo(f"Warning: {msg}", err=True)
                baseline_method = "bbox-bottom"

    # Initialize font
    asc_h = round(height_svg * 0.80)
    desc_d = round(height_svg * 0.20)
    cap_h = round(height_svg * 0.72)
    xh = round(height_svg * 0.52)

    # When BX data is present we may shift descender letters (g, p, y, z …)
    # down so their tops align with x-height.  The shifted letters have a
    # deeper descent: 2 × extra_bf × _DST_TO_SVG below baseline.  Pre-compute
    # the maximum such depth so we can size the canvas correctly.
    if bx_offsets:
        _max_shifted_extra_bf = max(
            (abs(y_min_bf) - abs(bx_y_min_baseline))
            for y_min_bf in bx_offsets.values()
            if _BX_DESCENDER_MIN_BF
            < (abs(y_min_bf) - abs(bx_y_min_baseline)) <= _BX_DESCENDER_MAX_BF
        ) if any(_BX_DESCENDER_MIN_BF
                 < (abs(v) - abs(bx_y_min_baseline)) <= _BX_DESCENDER_MAX_BF
                 for v in bx_offsets.values()) else 0
        if _max_shifted_extra_bf > 0:
            desc_d = max(desc_d,
                         round(2 * _max_shifted_extra_bf * _DST_TO_SVG) + 2)

    odir.mkdir(parents=True, exist_ok=True)
    font_tree = _build_font_svg(
        baseline_y=baseline_svg_y,
        ascender_y=baseline_svg_y - asc_h,
        descender_y=baseline_svg_y + desc_d,
        caps_y=baseline_svg_y - cap_h,
        xheight_y=baseline_svg_y - xh,
        doc_height=baseline_svg_y + desc_d + 20,
    )
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []
    font_data = _default_font_json(name, units_per_em, actual_size_mm, leading,
                                    description=description, keywords=kw_list)
    font_data["baseline_y"] = baseline_svg_y
    if sortable:
        font_data["sortable"] = True

    font_root = font_tree.getroot()
    added = []
    errors = []

    for char, emb_path in parsed:
        try:
            pattern = cached.get(char) or _read_embroidery(emb_path)

            # Use stitch-density analysis to find the letter body bounds.
            # This ignores swashes / connector strokes that extend beyond the
            # Visual baseline: height beyond calibration = swash or descender below baseline.
            svg_bottom = _visual_baseline_y(pattern, height_svg)

            ex = pattern.extents()
            svg_min_x = ex[0] * _DST_TO_SVG
            svg_width = (ex[2] - ex[0]) * _DST_TO_SVG
            glyph_height_svg = (ex[3] - ex[1]) * _DST_TO_SVG

            # Compute the vertical translation (dy).
            # The goal: place each letter's connection stitch at baseline_svg_y.
            #
            # When BX data is available, we know exactly where each letter's
            # connection point lives in the raw stitch coordinate space.
            # Embrilliance stores every letter centered at (0, 0) in BF units;
            # in the EXP stitch file (pyembroidery Y-down) the raw Y of any
            # point at BF Y-up coordinate v is  +abs(v) * _DST_TO_SVG.
            #
            #   x-height letters (a, c, e …) and ascenders (b, h, k …):
            #     The foot of the letter IS the connection point.
            #     connection_raw_y = abs(y_min_bf) × _DST_TO_SVG
            #
            #   Letters with extra_bf > 46 fall into two sub-types:
            #
            #   (A) "Top-exit descenders" (g, p, q, y, z …):
            #     The exit connector ends near the TOP of the letter body
            #     (last stitch y_dst ≪ −bx_y_min_baseline).  The letter's
            #     upper body is centred on y_dst=0; the x-height connection
            #     runs through y_dst ≈ +bx_y_min_baseline, and the descender
            #     hangs below that.  To align the top with x-height we use:
            #       connection_raw_y = (abs(bx_y_min_baseline) − extra_bf)
            #                          × _DST_TO_SVG
            #     (may be negative, meaning the connection is above the local
            #     stitch origin, which is fine — translate_y just gets larger)
            #
            #   (B) "Dot / ascender descenders" (j …):
            #     The exit connector ends near the x-height baseline
            #     (last stitch y_dst ≈ −bx_y_min_baseline).  These letters
            #     have a DOT or ascender feature that legitimately extends
            #     above x-height.  Keep connection at bx_y_min_baseline level.
            #
            # Without BX data we fall back to the _visual_baseline_y heuristic.
            advance_y_for_adv: float | None = None   # override for advance calc
            if bx_offsets and char in bx_offsets:
                y_min_bf = bx_offsets[char]
                extra_bf = abs(y_min_bf) - abs(bx_y_min_baseline)
                if extra_bf > _BX_DESCENDER_MIN_BF:
                    # Determine sub-type by checking where the exit connector ends.
                    # In the EXP files the last stitch is the exit connector tip.
                    # If abs(last_y_dst) > abs(bx_y_min_baseline) + 20, the exit
                    # is near the letter TOP → sub-type (A), needs shift.
                    last_y_dst = next(
                        (y for x, y, cmd in reversed(pattern.stitches)
                         if cmd == pyembroidery.STITCH),
                        None
                    )
                    exit_near_top = (
                        last_y_dst is not None
                        and abs(last_y_dst) > abs(bx_y_min_baseline) + 20
                        # j (extra≈78) is above MAX: its top feature is legitimate
                        and extra_bf <= _BX_DESCENDER_MAX_BF
                    )
                    if exit_near_top:
                        # Sub-type (A): shift letter down so top aligns with x-height.
                        # connection_raw_y may be negative — that's intentional.
                        connection_raw_y = (
                            (abs(bx_y_min_baseline) - extra_bf) * _DST_TO_SVG
                        )
                        # Advance width is measured at the natural connection
                        # level (foot of x-height letters), not the shifted point.
                        advance_y_for_adv = abs(bx_y_min_baseline) * _DST_TO_SVG
                    else:
                        # Sub-type (B): dot/ascender letter — keep standard.
                        connection_raw_y = abs(bx_y_min_baseline) * _DST_TO_SVG
                else:
                    # X-height, ascender, 'f', or cap height: foot = connection
                    # (A–Z caps sit at extra_bf = 46, exactly the boundary)
                    connection_raw_y = abs(y_min_bf) * _DST_TO_SVG
                dy = baseline_svg_y - connection_raw_y - baseline_from_bottom_px
            else:
                # No BX data — use the selected baseline method.
                if baseline_method == "last-stitch":
                    _last_y = _last_stitch_svg_y(pattern)
                    svg_bottom_for_method = _last_y if _last_y is not None else svg_bottom
                elif baseline_method == "reference-letter" and ref_svg_bottom is not None:
                    # All glyphs share the same raw-space bottom as the reference
                    # letter.  Letters with descenders extend below this level
                    # naturally; x-height and ascender letters sit above it.
                    svg_bottom_for_method = ref_svg_bottom
                else:
                    # bbox-bottom (default) or fallback
                    svg_bottom_for_method = svg_bottom
                dy = baseline_svg_y - svg_bottom_for_method - baseline_from_bottom_px

            # Horizontal advance width.
            # For connecting scripts, trim to the body right (before the thin
            # exit connector) so letters overlap and connect.  For all other
            # fonts, use the full bounding-box width with a small gap.
            #
            # Exception: letters whose total height significantly exceeds the
            # median have a descender or swash that hangs below the body and
            # occupies horizontal space past the body exit.  Trimming those
            # would cause the next letter to collide with the descender.
            # With BX data we know exactly which letters have descenders, so
            # we can also detect them via the y_min threshold rather than the
            # heuristic height check.
            if bx_offsets and char in bx_offsets:
                # "True descender" = glyph extends BELOW the connection line —
                # see the extra_bf cluster table at _BX_DESCENDER_MIN_BF.
                has_descender = (
                    abs(bx_offsets[char]) - abs(bx_y_min_baseline)
                ) > _BX_DESCENDER_MIN_BF
            else:
                has_descender = glyph_height_svg > height_svg * 1.15

            if is_script:
                if bx_offsets and char in bx_offsets:
                    # BX path: use the rightmost stitch near the connection Y.
                    # For top-exit descenders (g, p, y …) advance_y_for_adv
                    # overrides connection_raw_y so we measure at the natural
                    # foot-of-x-height level rather than the shifted origin.
                    _adv_y = (advance_y_for_adv
                              if advance_y_for_adv is not None
                              else connection_raw_y)
                    exit_x = _exit_advance_at_connection_y(pattern, _adv_y)
                    adv = round((exit_x - svg_min_x) + 1.0 + advance_padding, 1)
                elif not has_descender:
                    vis_right = _detect_exit_advance(pattern)
                    adv = round((vis_right - svg_min_x) + 1.0 + advance_padding, 1)
                else:
                    adv = round(svg_width + 2.0 + advance_padding, 1)
            else:
                adv = round(svg_width + 2.0 + advance_padding, 1)

            # Build GlyphLayer
            layer = etree.SubElement(font_root, f"{{{SVG_NS}}}g")
            layer.set(f"{{{INKSCAPE_NS}}}groupmode", "layer")
            layer.set(f"{{{INKSCAPE_NS}}}label", f"GlyphLayer-{char}")
            layer.set("style", "display:none")
            layer.set("id", f"g_{secrets.token_hex(4)}")
            transform_parts = []
            if abs(svg_min_x) > 0.01 or abs(dy) > 0.01:
                dx = -svg_min_x
                transform_parts.append(f"translate({dx:.2f},{dy:.2f})")
            if transform_parts:
                layer.set("transform", " ".join(transform_parts))

            paths = _emb_to_svg_paths(pattern, stitch_length_mm=stitch_length_mm)
            for p in paths:
                layer.append(p)

            font_data["horiz_adv_x"][char] = adv
            if char not in font_data.get("glyphs", []):
                font_data.setdefault("glyphs", []).append(char)

            entry = {"char": char, "file": emb_path.name,
                     "stitches": sum(1 for x, y, c in pattern.stitches
                                     if c == pyembroidery.STITCH),
                     "advance": adv}
            if is_script:
                bbox_adv = round(svg_width + 1.0, 1)
                entry["connector_trim_px"] = round(bbox_adv - adv, 1) if bbox_adv > adv else 0
            added.append(entry)
        except Exception as e:
            errors.append({"file": emb_path.name, "error": str(e)})

    _save_font_svg(font_tree, odir)
    _save_font_json(odir, font_data)

    # Write a LICENSE placeholder if one doesn't already exist.
    license_path = odir / "LICENSE"
    if not license_path.exists():
        license_path.write_text(
            f"Font: {name}\n\n"
            "TODO: Add licensing information for this font.\n",
            encoding="utf-8",
        )

    preview_error = None
    try:
        _generate_preview_png(odir, font_data, font_tree)
    except Exception as e:
        preview_error = str(e)

    result: dict = {
        "font_dir": str(odir),
        "glyphs_added": len(added),
        "skipped_files": len(skipped) + len(errors),
        "size_mm": actual_size_mm,
        "units_per_em": units_per_em,
        "preview_png": str(odir / "preview.png") if not preview_error else None,
        "preview_error": preview_error,
        "added": added,
        "errors": errors if errors else None,
    }
    if bx_offsets is not None:
        result["bx_glyphs_calibrated"] = len(bx_offsets)
        result["bx_y_min_baseline_bf"] = bx_y_min_baseline
    else:
        result["baseline_method"] = baseline_method
        if baseline_method == "reference-letter" and reference_letter:
            result["reference_letter"] = reference_letter[0]
    if import_warnings:
        result["warnings"] = import_warnings
    emit(ctx, result)


@font.command("set-baseline")
@click.option("--font-dir", "font_dir", required=True, type=click.Path(exists=True),
              help="Font directory (must contain ltr.svg — or legacy →.svg — and font.json).")
@click.option("--char", "char", required=True,
              help="The glyph to adjust (single character, e.g. 'g').")
@click.option("--shift-mm", "shift_mm", required=True, type=float,
              help="How far to move the glyph in mm.  Positive values move the glyph "
                   "upward (reducing its baseline-to-bottom gap); negative values move "
                   "it downward.  Fractions are fine, e.g. --shift-mm -0.5.")
@click.pass_context
def font_set_baseline(ctx, font_dir, char, shift_mm):
    """Shift a single glyph up or down to correct its baseline alignment.

    Use this after 'font import' to fine-tune individual letters whose
    baselines are slightly off.  The shift is applied to the glyph's SVG
    transform and recorded in font.json under 'baseline_overrides' so the
    cumulative correction survives multiple 'set-baseline' calls.

    Positive --shift-mm values move the glyph upward (letter appears to rise);
    negative values move it downward.

    Example — move 'g' down by 1.5 mm:

        font set-baseline --font-dir ./MyFont --char g --shift-mm -1.5
    """
    import re as _re

    fdir = Path(font_dir)
    ch = char[0] if char else ""
    if not ch:
        raise UserError("--char must be a non-empty single character")

    font_data = _load_font_json(fdir)
    font_tree = _load_font_svg(fdir)
    font_root = font_tree.getroot()

    # Convert mm → SVG px (positive shift_mm = move up = negative SVG delta)
    shift_px = -(shift_mm / _SVG_MM_PER_PX)

    # Locate the glyph layer
    layer_label = f"GlyphLayer-{ch}"
    target_layer = None
    for el in font_root.iter(f"{{{SVG_NS}}}g"):
        if el.get(f"{{{INKSCAPE_NS}}}label") == layer_label:
            target_layer = el
            break

    if target_layer is None:
        raise UserError(
            f"Glyph layer '{layer_label}' not found in the font SVG.  "
            f"Available glyphs: {font_data.get('glyphs', [])}"
        )

    # Parse current transform (may be absent or not a translate)
    _TRANSLATE_RE = _re.compile(
        r'translate\(\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*\)'
    )
    current_transform = target_layer.get("transform", "")
    m = _TRANSLATE_RE.search(current_transform)
    if m:
        cur_tx = float(m.group(1))
        cur_ty = float(m.group(2))
        new_ty = cur_ty + shift_px
        new_transform = _TRANSLATE_RE.sub(
            f"translate({cur_tx:.4f},{new_ty:.4f})", current_transform
        )
    else:
        # No translate present — add one (leave any existing transform intact)
        new_ty = shift_px
        if current_transform.strip():
            new_transform = f"translate(0.0000,{new_ty:.4f}) {current_transform}"
        else:
            new_transform = f"translate(0.0000,{new_ty:.4f})"

    target_layer.set("transform", new_transform)

    # Persist to SVG
    _save_font_svg(font_tree, fdir)

    # Record the cumulative override in font.json so external tools know
    overrides = font_data.setdefault("baseline_overrides", {})
    prev_px = float(overrides.get(ch, 0.0))
    overrides[ch] = round(prev_px + shift_px, 4)
    _save_font_json(fdir, font_data)

    emit(ctx, {
        "char": ch,
        "shift_mm": shift_mm,
        "shift_px": round(-shift_px, 4),  # positive = moved up, matches UX expectation
        "cumulative_override_px": round(overrides[ch], 4),
        "new_transform": new_transform,
    })


@font.command("preview")
@click.option("--font-dir", "font_dir", required=True, type=click.Path(),
              help="Font directory (must contain ltr.svg — or legacy →.svg — and font.json).")
@click.option("--chars", "chars", default=None,
              help="Characters to include in preview (default: first 7 uppercase letters).")
@click.pass_context
def font_preview(ctx, font_dir, chars):
    """Regenerate preview.png for an existing font."""
    fdir = Path(font_dir)
    font_data = _load_font_json(fdir)
    font_tree = _load_font_svg(fdir)
    _generate_preview_png(fdir, font_data, font_tree, override_chars=chars or None)
    emit(ctx, {"preview_png": str(fdir / "preview.png")})


@font.command("render-test")
@click.option("--font-dir", "font_dir", required=True, type=click.Path())
@click.option("--phrase", "phrase", required=True,
              help="Text to render (spaces become gaps).")
@click.option("--output", "output", default=None, type=click.Path(),
              help="Output PNG path (default: <font-dir>/render_test.png).")
@click.option("--width", "img_width", type=int, default=1400, show_default=True)
@click.option("--height", "img_height", type=int, default=160, show_default=True)
@click.option("--guides/--no-guides", "show_guides", default=True, show_default=True,
              help="Draw vertical lines at each letter's advance boundary.")
@click.option("--baseline/--no-baseline", "show_baseline", default=True, show_default=True,
              help="Draw a red horizontal line at the baseline.")
@click.option("--xheight/--no-xheight", "show_xheight", default=True, show_default=True,
              help="Draw a green horizontal line at the x-height.")
@click.pass_context
def font_render_test(ctx, font_dir, phrase, output, img_width, img_height, show_guides,
                     show_baseline, show_xheight):
    """Render a phrase to a large PNG for visual advance-width inspection.

    Each letter's advance boundary is drawn as a faint vertical line so you
    can immediately see which letters have too much or too little space.
    """
    fdir = Path(font_dir)
    font_data = _load_font_json(fdir)
    font_tree = _load_font_svg(fdir)
    dest = Path(output) if output else fdir / "render_test.png"

    W, H = img_width, img_height
    margin_x, margin_y = 8, 8

    font_root = font_tree.getroot()
    horiz_adv = font_data.get("horiz_adv_x", {})
    default_adv = font_data.get("horiz_adv_x_default", 60.0)
    space_adv = font_data.get("horiz_adv_x_space", default_adv * 0.5)

    # Collect all stroke segments with x_cursor tracking
    segments: list[tuple[float, float, float, float]] = []
    advance_xs: list[float] = []   # advance boundary x positions (pre-scale)
    x_cursor = 0.0

    for char in phrase:
        if char == " ":
            x_cursor += float(space_adv)
            advance_xs.append(x_cursor)
            continue

        layer_label = f"GlyphLayer-{char}"
        layers = font_root.xpath(
            ".//svg:g[@inkscape:label=$label]",
            namespaces={"svg": SVG_NS, "inkscape": INKSCAPE_NS},
            label=layer_label,
        )
        if not layers:
            x_cursor += float(horiz_adv.get(char, default_adv))
            advance_xs.append(x_cursor)
            continue

        layer = layers[0]
        tdx, tdy = 0.0, 0.0
        m = re.match(r'translate\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', layer.get("transform", ""))
        if m:
            tdx, tdy = float(m.group(1)), float(m.group(2))

        for path_el in layer.iter(f"{{{SVG_NS}}}path"):
            pts = _parse_path_points_simple(path_el.get("d", ""))
            for i in range(len(pts) - 1):
                segments.append((
                    pts[i][0] + tdx + x_cursor,
                    pts[i][1] + tdy,
                    pts[i + 1][0] + tdx + x_cursor,
                    pts[i + 1][1] + tdy,
                ))

        x_cursor += float(horiz_adv.get(char, default_adv))
        advance_xs.append(x_cursor)

    img, draw = _new_canvas(W, H)

    if not segments:
        img.save(dest)
        emit(ctx, {"render_test": str(dest)})
        return

    all_xs = [s[0] for s in segments] + [s[2] for s in segments]
    all_ys = [s[1] for s in segments] + [s[3] for s in segments]
    min_x, max_x = min(all_xs), max(all_xs)
    min_y, max_y = min(all_ys), max(all_ys)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    scale = min((W - 2 * margin_x) / span_x, (H - 2 * margin_y) / span_y)

    def to_px(x, y):
        return (
            int((x - min_x) * scale) + margin_x,
            int((y - min_y) * scale) + margin_y,
        )

    # Draw advance boundary guides first (behind glyphs)
    if show_guides:
        guide_color = (200, 180, 255)   # soft purple
        for ax in advance_xs:
            gx = int((ax - min_x) * scale) + margin_x
            if 0 <= gx < W:
                draw.line([(gx, 0), (gx, H - 1)], fill=guide_color)

    # Draw baseline and x-height lines (behind glyph strokes)
    _baseline_y_val = font_data.get("baseline_y", 0)
    if _baseline_y_val:
        units_per_em_val = font_data.get("units_per_em", 400.0)
        if show_baseline:
            _, bl_py = to_px(min_x, _baseline_y_val)
            if 0 <= bl_py < H:
                draw.line([(0, bl_py), (W - 1, bl_py)], fill=(220, 60, 60))
        if show_xheight:
            xh_y = _baseline_y_val - units_per_em_val * 0.52
            _, xh_py = to_px(min_x, xh_y)
            if 0 <= xh_py < H:
                draw.line([(0, xh_py), (W - 1, xh_py)], fill=(60, 180, 60))

    # Draw strokes
    ink = (30, 30, 30)
    for x0, y0, x1, y1 in segments:
        px0, py0 = to_px(x0, y0)
        px1, py1 = to_px(x1, y1)
        draw.line([(px0, py0), (px1, py1)], fill=ink)

    img.save(dest)
    emit(ctx, {"render_test": str(dest)})


# ---------------------------------------------------------------------------
# font validate
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = [
    "name", "units_per_em", "leading", "size", "min_scale", "max_scale",
    "horiz_adv_x_default", "horiz_adv_x_space", "horiz_adv_x", "glyphs",
    "default_variant", "text_direction",
]
_RECOMMENDED_FIELDS = [
    "description", "keywords", "auto_satin", "reversible", "sortable",
    "letter_case", "default_glyph", "kerning_pairs",
]


def _validate_font(font_dir: Path) -> dict:
    errors = []
    warnings = []
    fdir = font_dir

    svg_path = font_svg_path(fdir)
    json_path = fdir / "font.json"
    preview_path = fdir / "preview.png"
    license_path = fdir / "LICENSE"

    # Check required files
    if not svg_path.exists():
        errors.append({"code": "missing_svg",
                       "message": "no variant SVG found (ltr.svg, or legacy →.svg)"})
    if not json_path.exists():
        errors.append({"code": "missing_json", "message": "font.json not found"})

    # Warnings for optional files
    if not preview_path.exists():
        warnings.append({"code": "missing_preview", "message": "preview.png not found"})
    if not license_path.exists():
        warnings.append({"code": "missing_license", "message": "LICENSE file not found"})

    # Can't do further checks if core files are missing
    if not json_path.exists() or not svg_path.exists():
        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}

    # Load font.json
    try:
        with open(json_path, encoding="utf-8") as f:
            font_data = json.load(f)
    except Exception as e:
        errors.append({"code": "missing_json", "message": f"font.json parse error: {e}"})
        return {"valid": False, "errors": errors, "warnings": warnings}

    # Check required fields
    for field in _REQUIRED_FIELDS:
        if field not in font_data:
            errors.append({
                "code": "missing_field",
                "message": f"required field '{field}' absent from font.json",
            })

    # Check recommended fields
    for field in _RECOMMENDED_FIELDS:
        if field not in font_data:
            warnings.append({
                "code": "recommended_field",
                "message": f"recommended field '{field}' absent from font.json",
            })

    # Load SVG
    try:
        parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
        tree = etree.parse(str(svg_path), parser)
        svg_root = tree.getroot()
    except Exception as e:
        errors.append({"code": "missing_svg",
                       "message": f"{svg_path.name} parse error: {e}"})
        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}

    # Build set of GlyphLayer chars present in SVG
    svg_glyph_chars: set[str] = set()
    svg_glyph_layers: dict[str, object] = {}
    for elem in svg_root.iter():
        if not isinstance(elem.tag, str):
            continue
        label = elem.get(f"{{{INKSCAPE_NS}}}label", "")
        if label.startswith("GlyphLayer-") and len(label) > len("GlyphLayer-"):
            char = label[len("GlyphLayer-"):]
            svg_glyph_chars.add(char)
            svg_glyph_layers[char] = elem

    glyphs_list = font_data.get("glyphs", [])

    # Check glyph_layer_missing: char in glyphs[] but no matching layer
    for char in glyphs_list:
        if char not in svg_glyph_chars:
            errors.append({
                "code": "glyph_layer_missing",
                "message": f"GlyphLayer-{char!r} not found in the font SVG",
            })

    # Check orphan_layer: layer exists but char not in glyphs[]
    glyphs_set = set(glyphs_list)
    for char in svg_glyph_chars:
        if char not in glyphs_set:
            warnings.append({
                "code": "orphan_layer",
                "message": f"GlyphLayer-{char!r} exists in SVG but '{char}' is not in glyphs[]",
            })

    # Check missing_advance: char in glyphs[] but no entry in horiz_adv_x
    horiz_adv = font_data.get("horiz_adv_x", {})
    for char in glyphs_list:
        if char not in horiz_adv:
            warnings.append({
                "code": "missing_advance",
                "message": f"no horiz_adv_x entry for '{char}' (will use default)",
            })

    # Per-glyph layer checks: path_no_style, unsupported_feature
    for char, layer_elem in svg_glyph_layers.items():
        for node in layer_elem.iter():
            if not isinstance(node.tag, str):
                continue
            local = etree.QName(node.tag).localname

            # unsupported_feature: <use> element
            if local == "use":
                errors.append({
                    "code": "unsupported_feature",
                    "message": f"<use> (clone) element found in GlyphLayer-{char}",
                })
            # unsupported_feature: inkscape:path-effect attribute
            if node.get(f"{{{INKSCAPE_NS}}}path-effect") is not None:
                errors.append({
                    "code": "unsupported_feature",
                    "message": f"inkscape:path-effect found in GlyphLayer-{char}",
                })
            # unsupported_feature: gradients
            if local in ("linearGradient", "radialGradient"):
                errors.append({
                    "code": "unsupported_feature",
                    "message": f"<{local}> found in GlyphLayer-{char}",
                })

            # path_no_style: path with no fill or stroke
            if local == "path":
                style_attr = node.get("style", "")
                fill_attr = node.get("fill")
                stroke_attr = node.get("stroke")
                has_fill_in_style = "fill" in style_attr
                has_stroke_in_style = "stroke" in style_attr
                has_style = has_fill_in_style or has_stroke_in_style
                has_standalone = fill_attr is not None or stroke_attr is not None
                if not has_style and not has_standalone:
                    errors.append({
                        "code": "path_no_style",
                        "message": (
                            f"path in GlyphLayer-{char} has neither fill nor stroke"
                        ),
                    })

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


@font.command("validate")
@click.option("--font-dir", "font_dir", required=True, type=click.Path(),
              help="Font directory to validate.")
@click.pass_context
def font_validate(ctx, font_dir):
    """Check a font directory and return a structured validation report."""
    fdir = Path(font_dir)
    report = _validate_font(fdir)
    emit(ctx, report)


# ---------------------------------------------------------------------------
# font adjust-advances
# ---------------------------------------------------------------------------

@font.command("adjust-advances")
@click.option("--font-dir", "font_dir", required=True, type=click.Path())
@click.option("--padding", "padding", type=float, default=0.0, show_default=True,
              help="Add this value (SVG px) to every advance.")
@click.option("--scale", "scale", type=float, default=1.0, show_default=True,
              help="Multiply every advance by this factor (applied before padding).")
@click.option("--chars", "chars_csv", default=None,
              help="Comma-separated characters to adjust. If omitted, adjusts all.")
@click.option("--dry-run", is_flag=True, help="Print what would change without writing.")
@click.pass_context
def font_adjust_advances(ctx, font_dir, padding, scale, chars_csv, dry_run):
    """Adjust horizontal advance widths for glyphs in a font."""
    fdir = Path(font_dir)
    font_data = _load_font_json(fdir)

    target_chars: set[str] | None = None
    if chars_csv is not None:
        target_chars = {c.strip() for c in chars_csv.split(",") if c.strip()}

    horiz_adv = font_data.get("horiz_adv_x", {})
    adjusted: dict[str, dict] = {}

    new_horiz_adv = dict(horiz_adv)
    for char, before_val in horiz_adv.items():
        if target_chars is not None and char not in target_chars:
            continue
        after_val = round(float(before_val) * scale + padding, 1)
        if after_val != before_val:
            adjusted[char] = {"before": before_val, "after": after_val}
        new_horiz_adv[char] = after_val

    # Also adjust defaults when no specific chars selected
    new_default = font_data.get("horiz_adv_x_default", 0.0)
    new_space = font_data.get("horiz_adv_x_space", 0.0)
    if target_chars is None:
        before_default = font_data.get("horiz_adv_x_default", 0.0)
        before_space = font_data.get("horiz_adv_x_space", 0.0)
        new_default = round(float(before_default) * scale + padding, 1)
        new_space = round(float(before_space) * scale + padding, 1)
        if new_default != before_default:
            adjusted["__default__"] = {"before": before_default, "after": new_default}
        if new_space != before_space:
            adjusted["__space__"] = {"before": before_space, "after": new_space}

    if not dry_run:
        font_data["horiz_adv_x"] = new_horiz_adv
        if target_chars is None:
            font_data["horiz_adv_x_default"] = new_default
            font_data["horiz_adv_x_space"] = new_space
        _save_font_json(fdir, font_data)

    emit(ctx, {"adjusted": adjusted, "dry_run": dry_run})


# ---------------------------------------------------------------------------
# font set-field
# ---------------------------------------------------------------------------

_PROTECTED_FIELDS = {"horiz_adv_x", "glyphs"}


@font.command("set-field")
@click.option("--font-dir", "font_dir", required=True, type=click.Path())
@click.option("--key", "key", required=True, help="Top-level key in font.json to set.")
@click.option("--value", "value", required=True,
              help="Value to set (parsed as JSON; falls back to plain string).")
@click.pass_context
def font_set_field(ctx, font_dir, key, value):
    """Set a single top-level key in font.json."""
    if key in _PROTECTED_FIELDS:
        raise UserError(
            f"'{key}' cannot be set via set-field (use a dedicated command)."
        )
    fdir = Path(font_dir)
    font_data = _load_font_json(fdir)

    # Parse value as JSON; fall back to plain string
    try:
        parsed_value = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        parsed_value = value

    before = font_data.get(key)
    font_data[key] = parsed_value
    _save_font_json(fdir, font_data)
    emit(ctx, {"key": key, "before": before, "after": parsed_value})


# ---------------------------------------------------------------------------
# font import-bx-pack
# ---------------------------------------------------------------------------

def _extract_size_label(name: str) -> str | None:
    """Extract a size label like '1 inch' or '1.5 inch' from a name."""
    m = re.search(r'(\d+\.?\d*)\s*inch', name, flags=re.IGNORECASE)
    if m:
        return f"{m.group(1)} inch"
    return None


@font.command("import-bx-pack")
@click.option("--bx-dir", "bx_dir", required=True, type=click.Path(exists=True),
              help="Directory containing *.bx font files.")
@click.option("--exp-dir", "exp_dir", required=True, type=click.Path(exists=True),
              help="Directory containing per-size EXP subdirectories.")
@click.option("--output-dir", "output_dir", required=True, type=click.Path(),
              help="Root output directory; fonts are written to subdirs.")
@click.option("--name-prefix", "name_prefix", default=None,
              help="Font name prefix (default: derived from BX filename).")
@click.option("--advance-padding", "advance_padding", type=float, default=0.0,
              show_default=True, help="Extra SVG px added to every advance width.")
@click.option("--dry-run", is_flag=True, help="Print matched pairs without importing.")
@click.pass_context
def font_import_bx_pack(ctx, bx_dir, exp_dir, output_dir, name_prefix, advance_padding, dry_run):
    """Import a pack of BX font files (one per size) with matching EXP directories."""
    bx_path = Path(bx_dir)
    exp_path = Path(exp_dir)
    out_path = Path(output_dir)

    # Find all .bx files
    bx_files: list[Path] = sorted(bx_path.glob("*.bx"))

    # Build size → bx_file mapping
    bx_by_size: dict[str, Path] = {}
    derived_prefix: str | None = None
    for bf in bx_files:
        label = _extract_size_label(bf.stem)
        if label:
            bx_by_size[label] = bf
            if derived_prefix is None and name_prefix is None:
                # Strip the size part from the stem to get a base name
                stripped = re.sub(r'\s*\d+\.?\d*\s*inch', '', bf.stem,
                                  flags=re.IGNORECASE).strip()
                # Replace hyphens/underscores with spaces and clean up
                stripped = re.sub(r'[-_]+', ' ', stripped).strip()
                derived_prefix = stripped

    prefix = name_prefix if name_prefix is not None else (derived_prefix or "Font")

    # Find all EXP subdirectories
    exp_subdirs: dict[str, Path] = {}
    for item in exp_path.iterdir():
        if item.is_dir():
            label = item.name.strip()
            exp_subdirs[label] = item

    # Match BX files with EXP subdirs by size label
    matches: list[tuple[str, Path, Path]] = []
    for label, bf in sorted(bx_by_size.items()):
        if label in exp_subdirs:
            matches.append((label, bf, exp_subdirs[label]))

    if dry_run:
        pairs = [
            {"size": label, "bx_file": str(bf.name), "exp_dir": str(ed.name)}
            for label, bf, ed in matches
        ]
        emit(ctx, {"matched": pairs, "dry_run": True})
        return

    results = []
    for label, bf, ed in matches:
        font_name = f"{prefix} {label}"
        size_slug = label.replace(" ", "_")
        font_out = out_path / size_slug

        try:
            # Use ctx.invoke to call font_import with appropriate arguments
            import_result = ctx.invoke(
                font_import,
                name=font_name,
                source_dir=str(ed),
                output_dir=str(font_out),
                pick_ext="exp",
                size_mm=None,
                stitch_length_mm=1.5,
                baseline_from_bottom_mm=0.0,
                skip_alts=True,
                is_script=True,
                bx_file=str(bf),
                advance_padding=advance_padding,
                description="",
                keywords="script,handwriting,connected,running_stitch,latin",
                dry_run=False,
            )
            # font_import calls emit(ctx, result) — we can't easily capture its output
            # So we read font.json for the glyph count
            fj = font_out / "font.json"
            glyphs_added = 0
            if fj.exists():
                with open(fj, encoding="utf-8") as f:
                    fd = json.load(f)
                glyphs_added = len(fd.get("glyphs", []))
            results.append({
                "size": label,
                "font_dir": str(font_out),
                "glyphs_added": glyphs_added,
                "errors": [],
            })
        except Exception as e:
            results.append({
                "size": label,
                "font_dir": str(font_out),
                "glyphs_added": 0,
                "errors": [str(e)],
            })

    emit(ctx, {"results": results, "dry_run": False})
